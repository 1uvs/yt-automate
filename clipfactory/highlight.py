"""Find the most exciting moments in a long video via audio-loudness peaks.

Streamers like IShowSpeed spike in volume exactly when things go viral
(screaming / reactions), so loudness peaks are a strong, cheap highlight signal.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np


def _load_audio(path: Path, sr: int = 4000) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
         "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"could not decode audio from {path}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)


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

    hop = int(sr * 0.5)
    win = int(sr * 1.0)
    n_hops = max(1, (len(audio) - win) // hop)
    energy = np.empty(n_hops, dtype=np.float32)
    for i in range(n_hops):
        seg = audio[i * hop : i * hop + win]
        energy[i] = np.sqrt(np.mean(seg * seg) + 1.0)

    # smooth so a single spike doesn't win over a sustained hype moment
    k = 5
    kernel = np.ones(k) / k
    energy = np.convolve(energy, kernel, mode="same")

    sep_hops = int(min_separation / 0.5)
    order = np.argsort(energy)[::-1]
    chosen: list[int] = []
    for idx in order:
        if all(abs(idx - c) >= sep_hops for c in chosen):
            chosen.append(idx)
        if len(chosen) >= n:
            break

    segments = []
    for idx in sorted(chosen):
        center = idx * 0.5
        start = max(0.0, center - clip_len * 0.4)   # a little lead-in, then the payoff
        end = min(duration, start + clip_len)
        start = max(0.0, end - clip_len)
        segments.append((round(start, 2), round(end, 2)))
    return segments
