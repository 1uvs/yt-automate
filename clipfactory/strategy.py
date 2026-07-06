"""The strategy brain — the part that makes the system evolve.

Once a day it feeds the channel's REAL analytics (from analytics.py) to the bigger
GPT model and gets back an updated generation strategy: title formulas, hook style,
which streamers to prioritize, which hashtags to push, and what to stop doing. That
strategy is saved (and versioned) and read back by metadata/pipeline on every new
clip — so generation continuously steers toward whatever is actually earning subs
on this channel. Auto-applied; every version is kept so you can roll back.
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import llm
from .analytics import safe_insights
from .config import DATA

STRATEGY_PATH = DATA / "strategy.json"
HISTORY_DIR = DATA / "strategy_history"

# keys from insights() that are useful signal for the strategist
_SIGNAL_KEYS = ("clips", "total_subs", "by_format", "by_streamer",
                "by_length", "by_hashtag", "top_titles")

_PROMPT = """You are the growth strategist for a YouTube Shorts channel that reposts \
Twitch/streamer clips (Kai Cenat, xQc, IShowSpeed, etc.). Your job is to turn real \
performance data into a concrete generation strategy. The most important metric is \
subscribers gained, then views, then retention.

REAL analytics from this channel:
{data}

Current strategy (may be empty on the first run):
{current}

Return an UPDATED strategy as STRICT JSON with these keys:
- "title_formulas": array of 3-6 concrete title patterns that fit what's converting. \
Use {{streamer}} as a placeholder, e.g. "{{streamer}} did NOT expect this".
- "hook_style": one sentence describing the best thumbnail hook phrase style.
- "priority_streamers": array of streamer names to post more of (best converters first).
- "boost_hashtags": array of lowercase hashtags (no # symbol) to push.
- "avoid": array of short things to stop doing (patterns that underperform).
- "rationale": 1-3 sentences citing the specific numbers you based this on.

If the data is too thin to justify a change, keep the prior guidance and say so in \
"rationale". JSON only, no prose."""


def load_strategy() -> dict:
    """Current auto-applied strategy (empty dict if none yet)."""
    if STRATEGY_PATH.exists():
        try:
            return json.loads(STRATEGY_PATH.read_text())
        except Exception:
            return {}
    return {}


def evolve_strategy(verbose: bool = True) -> dict | None:
    """Analyze analytics with the big model and save an updated strategy.

    Returns the new strategy, or None if it couldn't run (no OpenAI / no analytics).
    """
    if not llm.available():
        if verbose:
            print("  (strategy brain skipped — OpenAI not available)")
        return None
    ins = safe_insights()
    if not ins:
        if verbose:
            print("  (strategy brain skipped — analytics not available yet)")
        return None

    data = json.dumps({k: ins[k] for k in _SIGNAL_KEYS if k in ins}, indent=2)[:8000]
    current = load_strategy()
    prompt = _PROMPT.format(
        data=data,
        current=json.dumps(current, indent=2) if current else "(none yet)",
    )
    raw = llm.chat_json(prompt, model=llm.smart_model(), max_tokens=1200)
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        strat = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None

    strat["updated"] = date.today().isoformat()
    strat["based_on_clips"] = ins.get("clips", 0)

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    STRATEGY_PATH.write_text(json.dumps(strat, indent=2))
    (HISTORY_DIR / f"strategy_{strat['updated']}.json").write_text(json.dumps(strat, indent=2))
    if verbose:
        print(f"  🧠 strategy updated (from {strat['based_on_clips']} clips): "
              f"{strat.get('rationale', '')[:140]}")
    return strat
