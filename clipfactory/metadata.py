"""Generate viral-optimized YouTube title, description, and hashtags.

Uses Claude (Haiku, cheap+fast) when ANTHROPIC_API_KEY is set; otherwise falls
back to solid templates so the pipeline always works.
"""
from __future__ import annotations

import json
import re

from .config import env

MODEL = "claude-haiku-4-5-20251001"

_PROMPT = """You are a YouTube Shorts growth expert. Given a clip, write metadata that \
maximizes click-through and watch-time for a Gen-Z gaming/entertainment audience.

Streamer: {streamer}
Original clip title: {clip_title}
Transcript (may be partial): {transcript}

Return STRICT JSON with keys:
- "title": <=70 chars, punchy, curiosity-driven, includes the streamer's name, NO clickbait lies
- "description": 1-2 lines + a call to subscribe
- "tags": array of 8-12 lowercase search tags (no # symbol)
- "hook": 2-4 WORD all-caps thumbnail phrase, max 18 chars, high-emotion (e.g. "HE DID WHAT?!")

JSON only, no prose."""


def _fallback(streamer: str, clip_title: str) -> dict:
    name = streamer.capitalize()
    base = clip_title.strip() or "goes crazy"
    title = f"{name} {base}"[:70]
    return {
        "title": title,
        "description": (
            f"{name} clip \U0001f525 Subscribe for daily {name} moments!\n"
            f"#shorts #{streamer.lower()} #clips"
        ),
        "tags": [streamer.lower(), "clips", "shorts", "twitch", "funny moments",
                 "viral", "gaming", "reaction"],
        "hook": (base.split(" WHAT")[0][:18] or "NO WAY").upper(),
    }


def _hashtags(tags: list[str], streamer: str) -> str:
    picks = [streamer.lower(), "shorts"] + [t.replace(" ", "") for t in tags[:3]]
    seen, out = set(), []
    for t in picks:
        if t and t not in seen:
            seen.add(t)
            out.append(f"#{t}")
    return " ".join(out)


def generate_metadata(streamer: str, clip_title: str, transcript: str = "",
                      tags_extra: list[str] | None = None) -> dict:
    tags_extra = tags_extra or []
    data = None
    if env("ANTHROPIC_API_KEY"):
        try:
            import anthropic

            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=MODEL,
                max_tokens=600,
                messages=[{
                    "role": "user",
                    "content": _PROMPT.format(
                        streamer=streamer,
                        clip_title=clip_title or "(none)",
                        transcript=(transcript or "(no speech)")[:1200],
                    ),
                }],
            )
            raw = "".join(b.text for b in msg.content if b.type == "text")
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
        except Exception:
            data = None

    if not data or "title" not in data:
        data = _fallback(streamer, clip_title)

    tags = list(dict.fromkeys([*(data.get("tags") or []), *tags_extra]))
    desc = data.get("description", "").rstrip()
    desc += "\n\n" + _hashtags(tags, streamer)
    hook = (data.get("hook") or data["title"]).upper()[:22]
    return {
        "title": data["title"][:100],
        "description": desc,
        "tags": tags[:15],
        "hook": hook,
    }
