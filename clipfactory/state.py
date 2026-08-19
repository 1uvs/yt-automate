"""SQLite state so we never re-process or re-publish the same clip.

Two things worth knowing about how this is used:

* The discovery pass asks "have I seen this clip?" once per candidate — with a
  28-streamer roster that's ~1000 questions per run, so the connection is opened
  once per thread and reused (`seen_many` answers the whole batch in one query).
* A clip that failed for a transient reason (yt-dlp hiccup, network blip) used to
  be blacklisted forever, because "seen" and "done" were the same thing. Failures
  now carry an attempt counter and become eligible again until they've burned
  through MAX_ATTEMPTS.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .config import DB_PATH

# A clip that fails this many times is given up on for good.
MAX_ATTEMPTS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    clip_id      TEXT PRIMARY KEY,   -- Twitch clip id
    streamer     TEXT,
    title        TEXT,
    view_count   INTEGER,
    status       TEXT,               -- discovered|rendered|pending|published|rejected|failed
    output_path  TEXT,
    youtube_id   TEXT,
    variant      TEXT,               -- A/B variant name (e.g. crop|blur)
    published_at TEXT,               -- when it went live (for analytics windows)
    error        TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);
"""

# Columns added after the first release — applied to existing DBs on connect.
_MIGRATIONS = [
    ("variant", "TEXT"),
    ("published_at", "TEXT"),
    ("length_sec", "REAL"),
    ("scheduled_at", "TEXT"),
    ("attempts", "INTEGER DEFAULT 0"),
]

# One connection per thread (sqlite objects aren't shareable across threads), so a
# run pays the schema/migration check once instead of once per query.
_local = threading.local()


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is not None:
        return c
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")    # concurrent readers while a write is open
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute(SCHEMA)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(clips)")}
    for name, coltype in _MIGRATIONS:
        if name not in cols:
            c.execute(f"ALTER TABLE clips ADD COLUMN {name} {coltype}")
    c.commit()
    _local.conn = c
    return c


def _is_done(status: str | None, attempts: int | None) -> bool:
    """True if this row should block the clip from being picked up again.

    Anything that reached a real outcome is done. A failure is only done once it
    has used up its retries — otherwise a transient error would blacklist a clip
    that would have worked fine on the next run.
    """
    if status == "failed":
        return (attempts or 0) >= MAX_ATTEMPTS
    return True


def seen(clip_id: str) -> bool:
    """True if we've already handled this clip (and shouldn't pick it up again)."""
    c = _conn()
    row = c.execute("SELECT status, attempts FROM clips WHERE clip_id=?",
                    (clip_id,)).fetchone()
    if row is None:
        return False
    return _is_done(row["status"], row["attempts"])


def seen_many(clip_ids: list[str]) -> set[str]:
    """Bulk version of seen() — one query for a whole batch of candidates."""
    if not clip_ids:
        return set()
    c = _conn()
    out: set[str] = set()
    for i in range(0, len(clip_ids), 500):   # stay under SQLite's variable limit
        batch = clip_ids[i : i + 500]
        q = (f"SELECT clip_id, status, attempts FROM clips "
             f"WHERE clip_id IN ({','.join('?' * len(batch))})")
        for row in c.execute(q, batch):
            if _is_done(row["status"], row["attempts"]):
                out.add(row["clip_id"])
    return out


def upsert(clip_id: str, **fields) -> None:
    fields.pop("updated_at", None)
    c = _conn()
    with c:  # transaction — commits on success, rolls back on error
        row = c.execute("SELECT 1 FROM clips WHERE clip_id=?", (clip_id,)).fetchone()
        if row is None:
            cols = ["clip_id"] + list(fields)
            vals = [clip_id] + [fields[k] for k in fields]
            q = f"INSERT INTO clips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
            c.execute(q, vals)
        elif fields:
            sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=datetime('now')"
            c.execute(f"UPDATE clips SET {sets} WHERE clip_id=?",
                      [fields[k] for k in fields] + [clip_id])


def record_failure(clip_id: str, error: str, **fields) -> int:
    """Mark a clip failed and increment its attempt counter. Returns attempts used."""
    upsert(clip_id, status="failed", error=error, **fields)
    c = _conn()
    with c:
        c.execute("UPDATE clips SET attempts=COALESCE(attempts,0)+1 WHERE clip_id=?",
                  (clip_id,))
    row = c.execute("SELECT attempts FROM clips WHERE clip_id=?", (clip_id,)).fetchone()
    return (row["attempts"] if row else 0) or 0


def get(clip_id: str) -> sqlite3.Row | None:
    return _conn().execute("SELECT * FROM clips WHERE clip_id=?", (clip_id,)).fetchone()


def by_status(status: str) -> list[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM clips WHERE status=? ORDER BY view_count DESC", (status,)
    ).fetchall()
