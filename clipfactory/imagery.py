"""Generate the still images a story is told over, with gpt-image.

One bespoke image per beat. This is the expensive part of a story, and it's also the
part that makes the channel look like a channel rather than a template: stock gameplay
footage under a voice is the exact signature YouTube's inauthentic-content policy
targets, and every channel using it looks identical to every other one.

Images are generated concurrently — they're independent API calls and doing six of
them serially is most of a story's wall-clock time.

A house style string is appended to every prompt so beats in one story (and stories
across the channel) share a look. If an image fails, the beat falls back to the
previous image rather than taking down the whole render.
"""
from __future__ import annotations

import base64
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import env

MODEL = "gpt-image-2.5-flare"
FALLBACK_MODEL = "gpt-image-2"

# 2:3 source for a 9:16 frame leaves headroom for the Ken Burns pan to move within.
SIZE = "1024x1536"

HOUSE_STYLE = (
    "Cinematic photoreal still, vertical composition, shallow depth of field, "
    "muted desaturated palette with one warm light source, heavy atmosphere, "
    "35mm film grain, natural volumetric light, archival documentary mood. "
    "No text, no captions, no watermarks, no logos, no readable writing. "
    "No real identifiable person's face."
)


def _client():
    key = env("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception:
        return None


def _is_rate_limit(e: Exception) -> bool:
    s = str(e).lower()
    return "rate limit" in s or "rate_limit" in s or "429" in s


def _is_out_of_budget(e: Exception) -> bool:
    """A billing wall, not a throughput blip — backing off will never clear it."""
    s = str(e).lower()
    return "insufficient_quota" in s or "spend limit" in s or "exceeded your current quota" in s


def _retry_after(e: Exception, attempt: int) -> float:
    """Honour the server's suggested wait, else exponential backoff with jitter."""
    m = re.search(r"try again in ([0-9.]+)s", str(e))
    if m:
        return min(60.0, float(m.group(1)) + 0.5)
    return min(60.0, 4.0 * (2 ** attempt)) + random.uniform(0, 1.5)


def _generate_one(client, prompt: str, out: Path,
                  model: str | None = None, attempts: int = 4) -> Path | None:
    full = f"{prompt.strip()} {HOUSE_STYLE}"
    # Flare is ~2x gpt-image-2's token rates, so the fallback is also the budget option.
    chain = [m for m in dict.fromkeys([model or MODEL, FALLBACK_MODEL]) if m]
    for name in chain:
        for i in range(attempts):
            try:
                r = client.images.generate(model=name, prompt=full, size=SIZE)
                out.write_bytes(base64.b64decode(r.data[0].b64_json))
                return out
            except Exception as e:
                msg = str(e)[:130]
                if _is_out_of_budget(e):
                    print(f"    (image generation stopped — out of budget: {msg})")
                    return None
                # Image tiers are throttled hard (5 images/min on this account), so a
                # 429 is the expected case under concurrency, not a failure. Waiting
                # is the fix; falling through to a neighbouring frame would silently
                # degrade the video for something that resolves in seconds.
                if _is_rate_limit(e) and i < attempts - 1:
                    wait = _retry_after(e, i)
                    print(f"    (image rate-limited, waiting {wait:.0f}s)")
                    time.sleep(wait)
                    continue
                # A content-policy refusal won't be fixed by retrying or by another model.
                if "safety" in msg.lower() or "policy" in msg.lower():
                    print(f"    (image refused by safety filter: {msg})")
                    return None
                print(f"    (image via {name} failed: {msg})")
                break
    return None


def generate_beat_images(prompts: list[str], work_dir: Path,
                         workers: int = 2,
                         model: str | None = None) -> list[Path | None]:
    """Generate one image per prompt. Returns a list aligned with `prompts`.

    Entries are None where generation failed; the caller decides how to cover the gap.
    """
    client = _client()
    if client is None:
        return [None] * len(prompts)
    work_dir.mkdir(parents=True, exist_ok=True)

    def one(item):
        i, p = item
        return _generate_one(client, p, work_dir / f"beat_{i:02d}.png", model)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(prompts)))) as pool:
        return list(pool.map(one, enumerate(prompts)))


def fill_gaps(images: list[Path | None]) -> list[Path] | None:
    """Replace failed images with the nearest successful one.

    Returns None if nothing generated at all — there's no story to render then.
    """
    good = [p for p in images if p is not None]
    if not good:
        return None
    out: list[Path] = []
    last = good[0]
    for p in images:
        if p is not None:
            last = p
        out.append(last)
    return out
