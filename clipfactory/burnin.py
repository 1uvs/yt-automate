"""Detect whether a source clip already has burned-in captions.

Some clips we pull (edited reposts, IShowSpeed-style produced videos) already ship
with the editor's own captions burned into the pixels. If we add our word-by-word
captions on top, you get two sets of text on screen — the 'double caption' look.

This samples a handful of frames from the bottom-center band (where burned captions
almost always sit) and looks for the signature of outlined caption text: bright
glyphs, a dark outline, and dense vertical edges, appearing consistently across
frames. When that's present we skip our own captions for that clip.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


# Tuned on real clips: a frame with burned caption text scores ~0.011 on the
# outlined-white metric below; raw uncaptioned frames top out around 0.003.
_FRAME_THRESHOLD = 0.005
# Captions are intermittent (only while someone's talking), so we don't need most
# frames to show text — a modest fraction across the clip is a confident signal.
_MIN_TEXTY_RATIO = 0.15


def _sample_band_frames(video_path: Path, n: int) -> tuple[list[Path], Path]:
    """Extract n frames spread across the clip, pre-cropped to the caption band."""
    from .edit import probe_duration

    dur = probe_duration(video_path) or 0.0
    tmp = Path(tempfile.mkdtemp(prefix="burnin_"))
    fps = (n / dur) if dur > 0 else 1.0
    fps = max(0.1, min(fps, 6.0))
    # bottom-center band (72%–98% of height, middle 80% wide) — where burned-in
    # captions and caption-style watermarks sit. Kept tight on purpose: reaching
    # up into the mid-frame catches faces/bright objects and causes false hits.
    vf = f"fps={fps:.5f},crop=iw*0.8:ih*0.26:iw*0.1:ih*0.72"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
         "-frames:v", str(n), str(tmp / "f_%03d.png")],
        capture_output=True, text=True,
    )
    return sorted(tmp.glob("f_*.png")), tmp


def _outlined_white_frac(png: Path) -> float:
    """Fraction of bright pixels sitting right next to a dark pixel.

    This is the fingerprint of outlined caption text (white glyphs + dark stroke).
    Plain bright regions (sky, jerseys, lights) score ~0 because they aren't
    bordered by dark, which is what makes this robust to busy footage.
    """
    import numpy as np
    from PIL import Image

    g = np.asarray(Image.open(png).convert("L"), dtype=np.int16)
    if g.size == 0:
        return 0.0
    white = g > 200
    dark = g < 70
    near_dark = dark.copy()
    for s in (1, 2, 3):
        near_dark |= (np.roll(dark, s, 0) | np.roll(dark, -s, 0)
                      | np.roll(dark, s, 1) | np.roll(dark, -s, 1))
    return float((white & near_dark).mean())


def _frame_has_caption(png: Path) -> bool:
    return _outlined_white_frac(png) > _FRAME_THRESHOLD


def looks_captioned(video_path: Path | str, min_ratio: float = _MIN_TEXTY_RATIO,
                    n: int = 16) -> bool:
    """True if the source already appears to have burned-in captions."""
    frames, tmp = _sample_band_frames(Path(video_path), n)
    try:
        if not frames:
            return False
        hits = sum(1 for f in frames if _frame_has_caption(f))
        return hits / len(frames) >= min_ratio
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
