"""Pre-publish clip screener — a single GPT-mini pass that gates each candidate
clip BEFORE we spend a render + upload on it.

Two jobs in one call:
1. Brand-safety / copyright risk — flag licensed music, TV/movie/broadcast footage,
   slurs/harassment, or sensitive/tragic incidents that make a repost takedown-prone.
2. Quality / virality potential — score hook strength, context clarity, payoff.

Fails open: if OpenAI is unavailable or the call errors, the clip is allowed through
(publish=True) so screening can never silently stall the pipeline.
"""
from __future__ import annotations

import json
import re

from . import llm

_PROMPT = """You are the safety + quality gate for a YouTube Shorts channel that reposts \
Twitch/streamer clips. Decide whether this clip is worth rendering and safe to publish.

Streamer: {streamer}
Clip title: {title}
Transcript (may be partial or empty): {transcript}

Assess two things:
1) RISK — copyright/brand-safety. High risk = licensed/background music that would \
trigger Content ID, visible TV/movie/sports-broadcast footage, slurs or harassment, \
doxxing, or a sensitive/tragic real-world incident. Low risk = ordinary gameplay/IRL \
banter/reactions.
2) QUALITY — viral potential as a Short: is there a clear hook, understandable context \
(even out of stream), and a payoff/punchline?

Return STRICT JSON:
- "risk": "low" | "med" | "high"
- "risk_reasons": array of short strings (empty if low)
- "quality": integer 0-100
- "publish": boolean — false ONLY if risk is high, or quality is clearly too weak to bother
- "verdict": one short sentence explaining the decision
JSON only."""


def screen_clip(streamer: str, title: str, transcript: str = "",
                min_quality: int = 25, block_high_risk: bool = True) -> dict:
    """Return a screening verdict dict. Fails open (publish=True) when GPT is unavailable."""
    allow = {"risk": "low", "risk_reasons": [], "quality": 100,
             "publish": True, "verdict": "not screened (LLM unavailable)"}
    if not llm.available():
        return allow
    prompt = _PROMPT.format(
        streamer=streamer,
        title=title or "(none)",
        transcript=(transcript or "(no speech detected)")[:1500],
    )
    raw = llm.chat_json(prompt, model=llm.mini_model(), max_tokens=500)
    if not raw:
        return allow
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return allow
    try:
        v = json.loads(m.group(0))
    except json.JSONDecodeError:
        return allow

    # Enforce the gate on our side too (don't fully trust the model's own publish bit).
    risk = str(v.get("risk", "low")).lower()
    quality = int(v.get("quality", 100) or 100)
    publish = bool(v.get("publish", True))
    if block_high_risk and risk == "high":
        publish = False
    if quality < min_quality:
        publish = False
    return {
        "risk": risk,
        "risk_reasons": v.get("risk_reasons") or [],
        "quality": quality,
        "publish": publish,
        "verdict": v.get("verdict", ""),
    }
