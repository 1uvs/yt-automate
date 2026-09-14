"""Find the most exciting moments in a video via audio-loudness peaks.

Streamers like IShowSpeed spike in volume exactly when things go viral
(screaming / reactions), so loudness peaks are a strong, cheap highlight signal.

Two users of this:
  * detect_highlights() carves several clips out of a long VOD.
  * best_window() picks which stretch of an over-long source clip to keep when the
    render has to trim it to Shorts length.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .binaries import FFMPEG

HOP = 0.5   # seconds between energy samples
WIN = 1.0   # seconds averaged per energy sample


def _load_audio(path: Path, sr: int = 4000) -> np.ndarray:
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
         "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"could not decode audio from {path}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)


def _energy(audio: np.ndarray, sr: int) -> np.ndarray:
    """RMS energy per HOP, smoothed. Vectorised — a 90-minute VOD is ~11k windows,
    which is slow enough to matter as a Python loop and free as a cumulative sum."""
    hop, win = int(sr * HOP), int(sr * WIN)
    n_hops = max(1, (len(audio) - win) // hop)
    # cumulative sum of squares -> every window's mean square in one shot
    csum = np.concatenate(([0.0], np.cumsum(audio.astype(np.float64) ** 2)))
    starts = np.arange(n_hops) * hop
    sums = csum[starts + win] - csum[starts]
    energy = np.sqrt(sums / win + 1.0).astype(np.float32)

    # smooth so a single spike doesn't win over a sustained hype moment
    k = 5
    return np.convolve(energy, np.ones(k) / k, mode="same")


def detect_highlights(
    path: Path,
    n: int = 3,
    clip_len: float = 30.0,
    min_separation: float = 45.0,
    sr: int = 4000,
) -> list[tuple[float, float]]:
    """Return up to n (start, end) segments centered on the loudest moments."""
    audio = _load_audio(path, sr)
    duration = len(audio) / sr
    if duration < clip_len:
        return [(0.0, duration)]

    energy = _energy(audio, sr)
    sep_hops = int(min_separation / HOP)
    order = np.argsort(energy)[::-1]
    chosen: list[int] = []
    for idx in order:
        if all(abs(idx - c) >= sep_hops for c in chosen):
            chosen.append(idx)
        if len(chosen) >= n:
            break

    segments = []
    for idx in sorted(chosen):
        center = idx * HOP
        start = max(0.0, center - clip_len * 0.4)   # a little lead-in, then the payoff
        end = min(duration, start + clip_len)
        start = max(0.0, end - clip_len)
        segments.append((round(start, 2), round(end, 2)))
    return segments


def best_window(path: Path, length: float, sr: int = 4000) -> tuple[float, float]:
    """Pick the `length`-second stretch with the most energy in it.

    Twitch clips run up to ~75s but a Short is capped well below that, so something
    has to go. Taking the first N seconds throws away the payoff, which is usually
    the loudest part and usually at the end. This keeps the loudest stretch instead,
    biased slightly early so the punchline isn't sitting on the last frame.
    """
    audio = _load_audio(path, sr)
    duration = len(audio) / sr
    if duration <= length:
        return (0.0, duration)

    energy = _energy(audio, sr)
    win_hops = max(1, int(length / HOP))
    if win_hops >= len(energy):
        return (0.0, length)

    csum = np.concatenate(([0.0], np.cumsum(energy.astype(np.float64))))
    totals = csum[win_hops:] - csum[:-win_hops]
    start = float(np.argmax(totals) * HOP)
    # nudge earlier so the loudest beat lands ~70% through rather than at the edge
    start = max(0.0, start - length * 0.15)
    start = min(start, duration - length)
    return (round(start, 2), round(start + length, 2))
