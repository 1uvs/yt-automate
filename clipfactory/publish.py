"""Publish a pending clip to YouTube. Shared by review.py (CLI) and app.py (web UI)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import state
from .config import OUTPUT_DIR
from .youtube import upload


def load_meta(cid: str) -> dict:
    p = OUTPUT_DIR / f"{cid}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_meta(cid: str, meta: dict) -> None:
    (OUTPUT_DIR / f"{cid}.json").write_text(json.dumps(meta, indent=2))


def publish_clip(cid: str, cfg: dict, publish_at: str | None = None) -> tuple[bool, str]:
    """Publish now (publish_at=None) or schedule for a future ISO time."""
    r = state.get(cid)
    if not r or r["status"] != "pending":
        return False, "clip is not pending"
    m = load_meta(cid)
    p = cfg["publish"]
    thumb = OUTPUT_DIR / f"{cid}_thumb.jpg"
    try:
        vid = upload(
            Path(r["output_path"]),
            title=m["title"],
            description=m["description"],
            tags=m.get("tags", []),
            privacy=p["privacy_on_approve"],
            category_id=str(p["category_id"]),
            made_for_kids=p["made_for_kids"],
            thumbnail=thumb if thumb.exists() else None,
            publish_at=publish_at,
        )
        if publish_at:
            state.upsert(cid, status="scheduled", youtube_id=vid, scheduled_at=publish_at,
                         published_at=publish_at[:10])
        else:
            state.upsert(cid, status="published", youtube_id=vid,
                         published_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        return True, vid
    except Exception as e:
        state.upsert(cid, status="pending", error=str(e))
        return False, str(e)
