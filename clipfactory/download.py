"""Download a Twitch clip's mp4 via yt-dlp, or cut a segment out of a longer VOD."""
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


def cut_segment(source: Path, clip_id: str, start: float, end: float) -> Path:
    """Extract [start, end] from a longer video into its own clip (re-encoded)."""
    out = CLIPS_DIR / f"{clip_id}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", str(source), "-t", str(end - start),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"segment cut failed:\n{proc.stderr[-800:]}")
    return out
