"""SQLite state so we never re-process or re-publish the same clip."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DB_PATH

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
]


def _conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute(SCHEMA)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(clips)")}
    for name, coltype in _MIGRATIONS:
        if name not in cols:
            c.execute(f"ALTER TABLE clips ADD COLUMN {name} {coltype}")
    return c


def seen(clip_id: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM clips WHERE clip_id=?", (clip_id,)).fetchone() is not None


def upsert(clip_id: str, **fields) -> None:
    fields["updated_at"] = None  # trigger datetime('now') below
    with _conn() as c:
        row = c.execute("SELECT 1 FROM clips WHERE clip_id=?", (clip_id,)).fetchone()
        if row is None:
            cols = ["clip_id"] + [k for k in fields if k != "updated_at"]
            vals = [clip_id] + [fields[k] for k in fields if k != "updated_at"]
            q = f"INSERT INTO clips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
            c.execute(q, vals)
        else:
            sets = ", ".join(f"{k}=?" for k in fields if k != "updated_at")
            sets += ", updated_at=datetime('now')"
            vals = [fields[k] for k in fields if k != "updated_at"] + [clip_id]
            c.execute(f"UPDATE clips SET {sets} WHERE clip_id=?", vals)


def get(clip_id: str) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute("SELECT * FROM clips WHERE clip_id=?", (clip_id,)).fetchone()


def by_status(status: str) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM clips WHERE status=? ORDER BY view_count DESC", (status,)
        ).fetchall()
