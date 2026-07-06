"""Generate viral-optimized YouTube title, description, and hashtags.

Uses Claude (Haiku, cheap+fast) when ANTHROPIC_API_KEY is set; otherwise falls
back to solid templates so the pipeline always works.
"""
from __future__ import annotations

import json
import re

from .config import env
from .hashtags import build_hashtags, hashtag_line

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


_gemini_exhausted = False  # once quota is hit in a run, stop retrying (saves time)


def _call_llm(prompt: str) -> str | None:
    """Generate metadata. Priority: OpenAI GPT-mini -> Gemini -> Anthropic -> None."""
    global _gemini_exhausted
    # Primary: OpenAI GPT-mini (high daily quota). Falls through on any failure.
    from . import llm
    if llm.available():
        out = llm.chat_json(prompt, model=llm.mini_model())
        if out:
            return out
    gkey = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
    if gkey and not _gemini_exhausted:
        try:
            from google import genai

            client = genai.Client(api_key=gkey)
            resp = client.models.generate_content(
                model=env("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return resp.text
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                _gemini_exhausted = True  # out of quota; use templates for the rest
                print("    (Gemini quota hit — using templates for remaining clips)")
    if env("ANTHROPIC_API_KEY"):
        try:
            import anthropic

            msg = anthropic.Anthropic().messages.create(
                model=ANTHROPIC_MODEL, max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in msg.content if b.type == "text")
        except Exception:
            pass
    return None

_PROMPT = """You are a YouTube Shorts growth expert. Given a clip, write metadata that \
maximizes click-through and watch-time for a Gen-Z gaming/entertainment audience.

Streamer: {streamer}
Original clip title: {clip_title}
Transcript (may be partial): {transcript}
{examples}{strategy}
Return STRICT JSON with keys:
- "title": <=70 chars, punchy, curiosity-driven, includes the streamer's name, NO clickbait lies
- "description": 1-2 lines + a call to subscribe
- "tags": array of 8-12 lowercase search tags (no # symbol)
- "hook": 2-4 WORD all-caps thumbnail phrase, max 18 chars, high-emotion (e.g. "HE DID WHAT?!")

JSON only, no prose."""


def _strategy_block() -> str:
    """Fold the learned, auto-evolving strategy (if any) into the prompt."""
    try:
        from .strategy import load_strategy
        strat = load_strategy()
    except Exception:
        strat = {}
    if not strat:
        return ""
    parts = []
    if strat.get("title_formulas"):
        formulas = "\n".join(f"- {f}" for f in strat["title_formulas"][:6])
        parts.append("Proven title formulas (adapt to this clip, don't copy verbatim):\n"
                     + formulas)
    if strat.get("hook_style"):
        parts.append(f"Hook style that converts here: {strat['hook_style']}")
    if strat.get("avoid"):
        parts.append("Avoid: " + "; ".join(strat["avoid"][:5]))
    if not parts:
        return ""
    return ("\nLEARNED STRATEGY (auto-derived from THIS channel's analytics — follow it):\n"
            + "\n".join(parts) + "\n")


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
                      tags_extra: list[str] | None = None,
                      hashtag_boost: list[str] | None = None,
                      title_examples: list[str] | None = None) -> dict:
    tags_extra = tags_extra or []
    examples = ""
    if title_examples:
        joined = "\n".join(f"- {t}" for t in title_examples[:5])
        examples = ("\nYour BEST-PERFORMING past titles (emulate this style — it's what "
                    f"gets subscribers on this channel):\n{joined}\n")
    prompt = _PROMPT.format(
        streamer=streamer,
        clip_title=clip_title or "(none)",
        transcript=(transcript or "(no speech)")[:1200],
        examples=examples,
        strategy=_strategy_block(),
    )
    data = None
    raw = _call_llm(prompt)
    if raw:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None

    if not data or "title" not in data:
        data = _fallback(streamer, clip_title)

    tags = list(dict.fromkeys([*(data.get("tags") or []), *tags_extra]))
    # focused, strategy-driven hashtags (with any learned winners boosted first)
    hashtags = build_hashtags(streamer, clip_title, transcript, boost=hashtag_boost)
    desc = data.get("description", "").rstrip()
    desc += "\n\n" + hashtag_line(hashtags)
    hook = (data.get("hook") or data["title"]).upper()[:22]
    return {
        "title": data["title"][:100],
        "description": desc,
        "tags": tags[:15],
        "hashtags": hashtags,
        "hook": hook,
    }
