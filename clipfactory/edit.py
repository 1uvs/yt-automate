"""Render a source clip into a captioned 9:16 vertical video with ffmpeg.

Captions are composited as transparent PNG overlays (see captions.py) so no libass
build of ffmpeg is required — only the always-available overlay/scale/boxblur filters.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def probe_duration(path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return round(float(proc.stdout.strip()), 2)
    except ValueError:
        return 0.0


def _base_chain(layout: str, w: int, h: int) -> str:
    if layout == "center_crop":
        return f"[0:v]scale=-2:{h},crop={w}:{h},setsar=1[base]"
    # blur_pad (default): whole frame centered over a blurred fill
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=42[bgb];"
        f"[fg]scale={w}:-2[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
    )


def render_vertical(
    src: Path,
    out: Path,
    caption_specs: list[dict] | None = None,
    layout: str = "blur_pad",
    w: int = 1080,
    h: int = 1920,
    max_sec: int = 58,
) -> Path:
    caption_specs = caption_specs or []
    inputs = ["-i", str(src)]
    chain = [_base_chain(layout, w, h)]

    prev = "[base]"
    for i, spec in enumerate(caption_specs):
        inputs += ["-i", str(spec["path"])]
        label = "[outv]" if i == len(caption_specs) - 1 else f"[v{i}]"
        idx = i + 1  # input stream index (0 is the source)
        # Half-open window [start, end): a word shows from its start up to (but
        # not including) the next word's start. Because captions.py tiles windows
        # so end == the next start, this guarantees exactly one caption per frame
        # with no blackout gap between words (no doubling, no flicker).
        chain.append(
            f"{prev}[{idx}:v]overlay=0:0:"
            f"enable='gte(t,{spec['start']})*lt(t,{spec['end']})'{label}"
        )
        prev = f"[v{i}]"
    if not caption_specs:
        chain[0] = chain[0].replace("[base]", "[outv]")

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-t", str(max_sec),
        "-filter_complex", ";".join(chain),
        "-map", "[outv]", "-map", "0:a?",
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr[-2000:]}")
    return out
