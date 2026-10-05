"""Narration via OpenAI TTS.

gpt-4o-mini-tts takes a free-text `instructions` string describing *how* to read the
line, not just which voice to use. That steering is worth more than the voice choice:
it's what keeps a documentary narrator from sounding like a phone menu, and it's part
of what separates this from the stock-TTS sound YouTube now demotes.

Falls back to tts-1-hd (no steering) if the steerable model is unavailable, so a
model change can't take the pipeline down.
"""
from __future__ import annotations

from pathlib import Path

from .config import env

MODEL = "gpt-4o-mini-tts"
FALLBACK_MODEL = "tts-1-hd"

# Read once per script, so the whole channel keeps one identifiable delivery.
DEFAULT_INSTRUCTIONS = (
    "Read as a documentary narrator telling a true unsolved case, with urgency. "
    "Brisk and forward-moving — you are holding the attention of someone about to "
    "scroll away. Low and level, never excited or salesy, but do not dawdle: clip "
    "the ends of sentences and keep the pauses short. Land the specific details — "
    "names, dates, numbers — with a fraction more weight, then move straight on."
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


def narrate(text: str, out: Path, voice: str = "onyx",
            instructions: str | None = None, speed: float = 1.0) -> Path | None:
    """Render `text` to an mp3 at `out`. Returns None if TTS is unavailable.

    `speed` is applied on top of the delivery instructions. The default read came out
    at ~140 wpm, which is slow for a format where the viewer decides in two seconds;
    story.voice_speed nudges it without re-recording the prompt.
    """
    client = _client()
    if client is None:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)

    kw = {"speed": speed} if speed and abs(speed - 1.0) > 0.01 else {}
    attempts = [
        (MODEL, {"instructions": instructions or DEFAULT_INSTRUCTIONS, **kw}),
        # Not every model accepts every parameter; drop the optional ones in turn
        # rather than losing the narration over a rejected keyword.
        (MODEL, {"instructions": instructions or DEFAULT_INSTRUCTIONS}),
        (FALLBACK_MODEL, kw),
        (FALLBACK_MODEL, {}),
    ]
    last = None
    for model, extra in attempts:
        try:
            resp = client.audio.speech.create(model=model, voice=voice, input=text, **extra)
            out.write_bytes(resp.content)
            return out
        except Exception as e:
            last = e
            if "spend" in str(e).lower() or "quota" in str(e).lower():
                break   # retrying a billing wall just wastes time
    print(f"    (TTS failed: {str(last)[:160]})")
    return None
