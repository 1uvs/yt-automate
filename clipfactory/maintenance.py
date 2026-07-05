"""Disk cleanup so autopilot can run for months without filling the drive."""
from __future__ import annotations

import shutil

from . import state
from .config import CLIPS_DIR, OUTPUT_DIR


def cleanup_disk() -> float:
    """Remove files we no longer need. Returns MB freed.

    Deletes: downloaded VODs (huge, transient), and the raw source clip + caption
    PNG folder for any clip that's already published/scheduled/rejected/failed.
    Keeps: the final vertical mp4, thumbnail, and metadata json.
    """
    freed = 0

    # VOD downloads are only needed during the run that cuts highlights from them
    for p in CLIPS_DIR.glob("_vod_*"):
        try:
            freed += p.stat().st_size
            p.unlink()
        except OSError:
            pass

    done = set()
    for st in ("published", "scheduled", "rejected", "failed"):
        for r in state.by_status(st):
            done.add(r["clip_id"])

    for cid in done:
        src = CLIPS_DIR / f"{cid}.mp4"
        if src.exists():
            try:
                freed += src.stat().st_size
                src.unlink()
            except OSError:
                pass
        capdir = OUTPUT_DIR / f"{cid}_caps"
        if capdir.exists():
            for f in capdir.glob("*"):
                try:
                    freed += f.stat().st_size
                except OSError:
                    pass
            shutil.rmtree(capdir, ignore_errors=True)

    return round(freed / 1_000_000, 1)
