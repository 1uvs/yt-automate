"""Vision-based thumbnail frame picker.

Samples candidate frames from the rendered vertical clip and asks GPT-5.4 (vision)
which one is the most clickable thumbnail — visible face, strong emotion, motion
clarity, and a text-safe zone for the hook. Returns the timestamp to grab, which
thumbnail.generate_thumbnail() then uses via its frame_at argument.

Fails open: returns None (let ffmpeg pick a representative frame) if vision is
unavailable or errors.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
from pathlib import Path

from .binaries import FFMPEG
from . import llm
from .edit import probe_duration

_N_FRAMES = 6


def _sample_frames(video_path: Path, n: int) -> tuple[list[Path], list[float], Path]:
    """Grab n small JPEG frames spread across the clip (skipping the very edges)."""
    dur = probe_duration(video_path) or 0.0
    tmp = Path(tempfile.mkdtemp(prefix="visthumb_"))
    frames, times = [], []
    for i in range(n):
        t = dur * (i + 1) / (n + 1) if dur > 0 else i
        out = tmp / f"f_{i}.jpg"
        subprocess.run(
            [FFMPEG, "-y", "-ss", str(round(t, 2)), "-i", str(video_path),
             "-frames:v", "1", "-vf", "scale=360:-1", str(out), "-loglevel", "error"],
            capture_output=True,
        )
        if out.exists():
            frames.append(out)
            times.append(round(t, 2))
    return frames, times, tmp


def _b64(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def pick_thumbnail_time(video_path: Path, n: int = _N_FRAMES) -> float | None:
    """Return the timestamp (sec) of the most clickable frame, or None to fall back."""
    if not llm.available():
        return None
    import shutil

    frames, times, tmp = _sample_frames(Path(video_path), n)
    try:
        if len(frames) < 2:
            return None
        content = [{
            "type": "text",
            "text": (
                f"Pick the best YouTube Shorts THUMBNAIL from these {len(frames)} frames "
                "of one clip. Best = a clearly visible face with strong emotion/expression, "
                "sharp (low motion blur), and clean space near the top for a big text hook. "
                'Return STRICT JSON {"best_index": <1-based int>, "scores": [ints], '
                '"reason": "short"}.'
            ),
        }]
        for f in frames:
            content.append({"type": "image_url", "image_url": {"url": _b64(f)}})

        # mini handles frame selection fine and runs on the big (2.5M/day) quota,
        # keeping the smaller smart-model pool free for the strategy brain.
        raw = llm.chat_vision_json(content, model=llm.mini_model(), max_tokens=1500)
        if not raw:
            return None
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        idx = int(json.loads(m.group(0)).get("best_index", 0))
        if 1 <= idx <= len(times):
            return times[idx - 1]
        return None
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
