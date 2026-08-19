"""Retry with exponential backoff for the flaky parts (network, yt-dlp).

Every network step in a run is a chance for the whole clip to be thrown away over
a blip. Twitch rate-limits, yt-dlp occasionally 403s on a CDN edge, DNS hiccups.
One retry pass turns most of those into a two-second delay instead of a lost clip.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def with_retry(fn: Callable[[], T], attempts: int = 3, base_delay: float = 1.5,
               label: str = "", quiet: bool = False) -> T:
    """Call fn(), retrying transient failures. Re-raises the last error if all fail."""
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as e:                  # noqa: BLE001 — caller decides what's fatal
            last = e
            if i == attempts - 1:
                break
            delay = base_delay * (2 ** i)
            if not quiet:
                what = f" {label}" if label else ""
                print(f"    (retrying{what} in {delay:.0f}s — {str(e)[:100]})")
            time.sleep(delay)
    raise last  # type: ignore[misc]
