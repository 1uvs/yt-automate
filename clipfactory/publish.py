"""Publish a pending clip to YouTube. Shared by review.py (CLI) and app.py (web UI)."""
from __future__ import annotations

import json
from pathlib import Path

from . import state
from .config import OUTPUT_DIR
from .youtube import upload


def load_meta(cid: str) -> dict:
    p = OUTPUT_DIR / f"{cid}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_meta(cid: str, meta: dict) -> None:
    (OUTPUT_DIR / f"{cid}.json").write_text(json.dumps(meta, indent=2))


def publish_clip(cid: str, cfg: dict) -> tuple[bool, str]:
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
        )
        state.upsert(cid, status="published", youtube_id=vid)
        return True, vid
    except Exception as e:
        state.upsert(cid, status="pending", error=str(e))
        return False, str(e)
