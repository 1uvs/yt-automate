"""OpenAI (GPT) access layer.

Used as the primary metadata generator (the cheap/fast 'mini' model) and by the
strategy brain (the bigger model). Everything degrades gracefully: if there's no
key, no billing, or a rate limit, calls return None and the caller falls back to
Gemini/templates — the pipeline never breaks because OpenAI is unavailable.
"""
from __future__ import annotations

from .config import env

# Once we hit a quota/billing/rate-limit wall in a run, stop retrying (saves time).
_exhausted = False
# Cache the keyword combo that this SDK+model actually accepts, so we don't pay
# for trial-and-error on every single call (GPT-5-era models differ on param names).
_param_mode: dict | None = None


def mini_model() -> str:
    return env("OPENAI_MODEL_MINI", "gpt-5.4-mini")


def smart_model() -> str:
    return env("OPENAI_MODEL", "gpt-5.4")


def available() -> bool:
    """True if OpenAI is configured and hasn't been ruled out this run."""
    return bool(env("OPENAI_API_KEY")) and not _exhausted


def _client():
    if not env("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=env("OPENAI_API_KEY"))
    except Exception:
        return None


def chat_json(prompt: str, model: str | None = None, max_tokens: int = 700) -> str | None:
    """Ask a GPT model for a JSON answer. Returns the raw text, or None to fall back.

    The prompt should ask for JSON (json_object response mode requires the word
    'json' to appear in the prompt).
    """
    global _exhausted, _param_mode
    if _exhausted:
        return None
    client = _client()
    if client is None:
        return None
    model = model or mini_model()

    # Try param combos in order; remember the first that works. Newer models want
    # max_completion_tokens (not max_tokens) and reject non-default temperature, so
    # we don't send temperature at all.
    combos = [
        {"response_format": {"type": "json_object"}, "max_completion_tokens": max_tokens},
        {"max_completion_tokens": max_tokens},
        {"response_format": {"type": "json_object"}, "max_tokens": max_tokens},
        {"max_tokens": max_tokens},
        {},
    ]
    if _param_mode is not None and _param_mode not in combos:
        combos.insert(0, _param_mode)
    elif _param_mode is not None:
        combos.insert(0, combos.pop(combos.index(_param_mode)))

    return _create_json([{"role": "user", "content": prompt}], model, max_tokens)


def chat_vision_json(content: list, model: str | None = None,
                     max_tokens: int = 1500) -> str | None:
    """Like chat_json but for a multimodal message (text + image_url parts).

    `content` is the OpenAI content array, e.g.
    [{"type":"text","text":...}, {"type":"image_url","image_url":{"url": "data:..."}}].
    """
    if _exhausted:
        return None
    if _client() is None:
        return None
    return _create_json([{"role": "user", "content": content}],
                        model or smart_model(), max_tokens)


def _create_json(messages: list, model: str, max_tokens: int) -> str | None:
    """Shared call path: negotiate param combos, back off on quota/rate limits."""
    global _exhausted, _param_mode
    client = _client()
    if client is None:
        return None
    combos = [
        {"response_format": {"type": "json_object"}, "max_completion_tokens": max_tokens},
        {"max_completion_tokens": max_tokens},
        {"response_format": {"type": "json_object"}, "max_tokens": max_tokens},
        {"max_tokens": max_tokens},
        {},
    ]
    if _param_mode is not None and _param_mode not in combos:
        combos.insert(0, _param_mode)
    elif _param_mode is not None:
        combos.insert(0, combos.pop(combos.index(_param_mode)))

    last_err = None
    for kw in combos:
        try:
            resp = client.chat.completions.create(model=model, messages=messages, **kw)
            _param_mode = kw
            return resp.choices[0].message.content
        except Exception as e:
            s = str(e).lower()
            if "insufficient_quota" in s or "exceeded your current quota" in s:
                _exhausted = True
                print("    (OpenAI unavailable — no billing/credits; falling back)")
                return None
            if "rate_limit" in s or "rate limit" in s:
                _exhausted = True
                print("    (OpenAI rate limit hit — falling back for the rest of this run)")
                return None
            last_err = e  # unknown/param error: try the next combo
            continue
    if last_err:
        print(f"    (OpenAI call failed: {str(last_err)[:140]})")
    return None
