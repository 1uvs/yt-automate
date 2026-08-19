"""Download upcoming clips while the current one is being transcribed and rendered.

Downloading is network-bound and rendering is CPU-bound, but the pipeline used to
do them strictly one after another, so the CPU sat idle through every download and
the network sat idle through every render. Keeping a couple of downloads in flight
ahead of the cursor overlaps the two without adding real load — the worker count is
small on purpose so a run never looks like a scraper.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .download import download_clip


class Prefetcher:
    """Keeps up to `depth` Twitch downloads running ahead of the clip being processed."""

    def __init__(self, candidates: list[dict], depth: int = 2, workers: int = 2):
        # Only Twitch clips are fetched by URL; VOD segments are cut from a file that
        # discovery has already downloaded, so there's nothing to fetch ahead for them.
        self._queue = [c for c in candidates if c.get("kind") == "twitch"]
        self._depth = max(0, depth)
        self._futures: dict[str, Future] = {}
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers),
                                        thread_name_prefix="prefetch")
        self._next = 0

    def prime(self, upto: int) -> None:
        """Submit downloads so that `depth` clips beyond index `upto` are in flight."""
        target = min(len(self._queue), upto + self._depth + 1)
        while self._next < target:
            cand = self._queue[self._next]
            cid = cand["id"]
            if cid not in self._futures:
                self._futures[cid] = self._pool.submit(download_clip, cand["clip_url"], cid)
            self._next += 1

    def get(self, cand: dict) -> Path:
        """Return the downloaded file, waiting on an in-flight fetch if there is one."""
        fut = self._futures.pop(cand["id"], None)
        if fut is None:
            return download_clip(cand["clip_url"], cand["id"])
        return fut.result()

    def shutdown(self) -> None:
        for fut in self._futures.values():
            fut.cancel()
        self._futures.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> "Prefetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()
