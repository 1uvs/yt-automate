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


def _generate_one(client, prompt: str, out: Path,
                  model: str | None = None) -> Path | None:
    full = f"{prompt.strip()} {HOUSE_STYLE}"
    # Flare is ~2x gpt-image-2's token rates, so the fallback is also the budget option.
    chain = [model, FALLBACK_MODEL] if model else [MODEL, FALLBACK_MODEL]
    for model in dict.fromkeys(chain):
        try:
            r = client.images.generate(model=model, prompt=full, size=SIZE)
            out.write_bytes(base64.b64decode(r.data[0].b64_json))
            return out
        except Exception as e:
            msg = str(e)[:130]
            # A content-policy refusal won't be fixed by retrying on another model.
            if "safety" in msg.lower() or "policy" in msg.lower():
                print(f"    (image refused by safety filter: {msg})")
                return None
            print(f"    (image via {model} failed: {msg})")
    return None


def generate_beat_images(prompts: list[str], work_dir: Path,
                         workers: int = 4,
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
