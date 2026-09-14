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
    "Read as a calm, precise documentary narrator telling a true unsolved case. "
    "Measured pace, low and level. Land the specific details — names, dates, numbers "
    "— with a fraction more weight, then move on. Do not sound excited or salesy. "
    "Let the strange facts do the work. A short natural pause at the end of each "
    "sentence, never a dramatic gasp."
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
            instructions: str | None = None) -> Path | None:
    """Render `text` to an mp3 at `out`. Returns None if TTS is unavailable."""
    client = _client()
    if client is None:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        resp = client.audio.speech.create(
            model=MODEL, voice=voice, input=text,
            instructions=instructions or DEFAULT_INSTRUCTIONS,
        )
        out.write_bytes(resp.content)
        return out
    except Exception as e:
        print(f"    (steerable TTS failed, falling back to {FALLBACK_MODEL}: {str(e)[:120]})")

    try:
        resp = client.audio.speech.create(model=FALLBACK_MODEL, voice=voice, input=text)
        out.write_bytes(resp.content)
        return out
    except Exception as e:
        print(f"    (TTS failed: {str(e)[:140]})")
        return None
