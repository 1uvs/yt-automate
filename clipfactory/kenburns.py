"""Assemble still images + narration into a 9:16 base video.

A row of static images under a voice track reads as a slideshow, and slideshow is
one of the shapes YouTube's inauthentic-content policy calls out by name. Two things
fix that cheaply: continuous Ken Burns motion on every beat (so no frame is ever
still), and a crossfade between beats (so nothing hard-cuts).

Beat durations are apportioned by word count against the real narration length, so
the image changes land on the sentence they illustrate rather than on a fixed timer.

Motion presets rotate per beat. A channel where every image slowly zooms into the
centre looks as templated as one with no motion at all.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .binaries import FFMPEG

FPS = 30
XFADE = 0.5          # seconds of crossfade between beats
ZOOM_RANGE = 0.18    # how far a beat travels over its duration

# (zoom direction, x expression, y expression). `P` is substituted with the beat's
# linear progress term so pans complete exactly over the beat's own length.
_MOTION = [
    ("in",  "(iw-iw/zoom)/2",   "(ih-ih/zoom)/2"),      # push in, centred
    ("in",  "(iw-iw/zoom)*P",   "(ih-ih/zoom)/2"),      # push in, drift right
    ("out", "(iw-iw/zoom)/2",   "(ih-ih/zoom)*P"),      # pull out, drift down
    ("in",  "(iw-iw/zoom)*(1-P)", "(ih-ih/zoom)/2"),    # push in, drift left
    ("out", "(iw-iw/zoom)/2",   "(ih-ih/zoom)*(1-P)"),  # pull out, drift up
]


def apportion(beat_word_counts: list[int], total_sec: float,
              min_sec: float = 1.2) -> list[float]:
    """Split `total_sec` across beats in proportion to how much each one says."""
    counts = [max(1, c) for c in beat_word_counts]
    total_words = sum(counts)
    raw = [total_sec * c / total_words for c in counts]
    # Don't let a three-word beat flash past; borrow from the longest beat instead.
    for i, d in enumerate(raw):
        if d < min_sec:
            deficit = min_sec - d
            j = max(range(len(raw)), key=lambda k: raw[k])
            if raw[j] - deficit > min_sec:
                raw[j] -= deficit
                raw[i] = min_sec
    return [round(d, 3) for d in raw]


def _segment_chain(idx: int, dur: float, w: int, h: int, label: str) -> str:
    """zoompan chain for one beat, emitting `label`."""
    frames = max(2, int(round(dur * FPS)))
    direction, x_expr, y_expr = _MOTION[idx % len(_MOTION)]
    step = ZOOM_RANGE / frames

    if direction == "in":
        z = f"min(1+{step:.8f}*on,{1 + ZOOM_RANGE:.4f})"
    else:
        z = f"max({1 + ZOOM_RANGE:.4f}-{step:.8f}*on,1)"

    progress = f"min(on/{frames},1)"
    x_expr = x_expr.replace("P", f"({progress})")
    y_expr = y_expr.replace("P", f"({progress})")

    # Oversample before zoompan: zooming a 1080-wide source softens badly, so the
    # still is scaled to 2x the target first and the zoom eats into that headroom.
    return (
        f"[{idx}:v]scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"zoompan=z='{z}':d=1:x='{x_expr}':y='{y_expr}':s={w}x{h}:fps={FPS},"
        f"trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
        f"format=yuv420p,setsar=1{label}"
    )


def _build_graph(n: int, durs: list[float], w: int, h: int) -> str:
    """Ken Burns every beat, then crossfade them into one stream labelled [outv]."""
    xf = XFADE if n > 1 else 0.0
    # Every beat but the last carries an extra `xf` of tail for the next one to
    # dissolve over, so after the chain each beat still occupies its own duration.
    seg_durs = [d + (xf if i < n - 1 else 0.0) for i, d in enumerate(durs)]
    parts = [_segment_chain(i, seg_durs[i], w, h, f"[s{i}]") for i in range(n)]

    if n == 1:
        return ";".join(parts).replace("[s0]", "[outv]")

    prev, elapsed = "[s0]", 0.0
    for i in range(1, n):
        elapsed += durs[i - 1]
        label = "[outv]" if i == n - 1 else f"[x{i}]"
        parts.append(
            f"{prev}[s{i}]xfade=transition=fade:duration={xf}:"
            f"offset={elapsed:.3f}{label}"
        )
        prev = f"[x{i}]"
    return ";".join(parts)


def _run(cmd: list[str], what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed:\n{proc.stderr[-1500:]}")


def build_base(images: list[Path], durations: list[float], audio: Path, out: Path,
               w: int = 1080, h: int = 1920) -> Path:
    """Render the stills into a moving 9:16 video carrying the narration track.

    Falls back to hard cuts if the crossfade graph is rejected — a picky ffmpeg build
    shouldn't cost us the whole story.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for img, dur in zip(images, durations):
        seg = dur + XFADE
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seg:.3f}", "-i", str(img)]
    inputs += ["-i", str(audio)]
    audio_idx = len(images)

    def cmd_for(graph: str) -> list[str]:
        return [
            FFMPEG, "-y", *inputs,
            "-filter_complex", graph,
            "-map", "[outv]", "-map", f"{audio_idx}:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(out),
        ]

    try:
        _run(cmd_for(_build_graph(len(images), durations, w, h)), "ken burns render")
    except RuntimeError as e:
        print(f"    (crossfade graph failed, falling back to hard cuts: {str(e)[:120]})")
        parts = [_segment_chain(i, durations[i], w, h, f"[s{i}]")
                 for i in range(len(images))]
        concat = "".join(f"[s{i}]" for i in range(len(images)))
        graph = ";".join(parts) + f";{concat}concat=n={len(images)}:v=1:a=0[outv]"
        _run(cmd_for(graph), "ken burns render (hard cuts)")
    return out
