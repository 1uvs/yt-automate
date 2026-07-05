"""Download a Twitch clip's mp4 via yt-dlp."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import CLIPS_DIR


def download_clip(clip_url: str, clip_id: str) -> Path:
    out = CLIPS_DIR / f"{clip_id}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = [
        "yt-dlp",
        "-f", "mp4/best",
        "--no-playlist",
        "--force-overwrites",
        "-o", str(out),
        clip_url,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    if not out.exists():
        raise RuntimeError(f"yt-dlp produced no file for {clip_url}")
    return out
