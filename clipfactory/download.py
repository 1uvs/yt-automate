"""Download a Twitch clip's mp4 via yt-dlp, or cut a segment out of a longer VOD."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .binaries import FFMPEG, YTDLP
from .config import CLIPS_DIR
from .retry import with_retry


def _run(cmd: list[str], what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Surface stderr — a bare CalledProcessError just says "exit 1", which makes
        # every download failure look identical in the queue's error column.
        raise RuntimeError(f"{what} failed (exit {proc.returncode}):\n"
                           f"{(proc.stderr or proc.stdout or '')[-800:]}")


def download_clip(clip_url: str, clip_id: str, attempts: int = 3) -> Path:
    out = CLIPS_DIR / f"{clip_id}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = [
        YTDLP,
        "-f", "mp4/best",
        "--no-playlist",
        "--force-overwrites",
        "-o", str(out),
        clip_url,
    ]

    def attempt() -> Path:
        _run(cmd, "yt-dlp")
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(f"yt-dlp produced no file for {clip_url}")
        return out

    return with_retry(attempt, attempts=attempts, label="download")


def cut_segment(source: Path, clip_id: str, start: float, end: float) -> Path:
    """Extract [start, end] from a longer video into its own clip (re-encoded)."""
    out = CLIPS_DIR / f"{clip_id}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    _run([
        FFMPEG, "-y", "-ss", str(start), "-i", str(source), "-t", str(end - start),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", str(out),
    ], "segment cut")
    if not out.exists():
        raise RuntimeError(f"segment cut produced no file for {source}")
    return out
