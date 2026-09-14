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
_SIGNAL_KEYS = ("clips", "total_subs", "by_format", "by_streamer", "by_category",
                "by_length", "by_hashtag", "top_titles")

_PROMPT = """You are the growth strategist for a YouTube Shorts channel that reposts \
Twitch/streamer clips (Kai Cenat, xQc, IShowSpeed, etc.). Turn real performance data \
into a generation strategy. Most important metric: subscribers gained, then views, \
then retention.

REAL analytics from this channel:
{data}

Current strategy (may be empty on the first run):
{current}

CRITICAL — be statistically honest and AVOID OVERFITTING. This channel likely has \
very few clips and little/no subscriber signal yet. With thin data, almost any pattern \
is noise. So:
- Make INCREMENTAL edits to the current strategy, not a full rewrite. Keep sections \
that lack strong evidence exactly as they are (list them under "holding").
- Only change priority_streamers or boost_hashtags when a pattern is backed by \
AGGREGATED evidence (multiple clips, real subs/views differences) — not one lucky clip.
- Preserve broad, proven defaults and exploration until sample size is meaningful. \
Don't collapse onto recent winners.

Return STRICT JSON:
- "confidence": "low" | "med" | "high" — your confidence given the data volume.
- "title_formulas": array of 3-6 title patterns (use {{streamer}} placeholder). Keep \
broad/proven ones when data is thin.
- "hook_style": one sentence on the best thumbnail hook phrase style.
- "priority_streamers": array of streamer names to prioritize (only reorder on real \
evidence; otherwise keep prior order / broad roster).
- "boost_hashtags": array of lowercase hashtags (no #). Change slowly.
- "avoid": array of short things to stop doing (only if evidenced).
- "changes": array of short strings describing what you actually changed this run (empty \
if nothing changed).
- "holding": array of short strings naming sections you deliberately left unchanged for \
lack of evidence.
- "rationale": 1-3 sentences citing the specific numbers. If data is too thin to justify \
changes, say so and keep prior guidance.
JSON only, no prose."""


_STORY_PROMPT = """You are the growth strategist for a YouTube Shorts channel that \
publishes ORIGINAL unsolved-mystery and strange-history Shorts. Every Short is written, \
narrated and illustrated in-house by one recurring narrator. Turn real performance data \
into a generation strategy. Most important metric: subscribers gained, then retention \
(the algorithm needs ~50% average view percentage on a 30-60s Short to push it wider), \
then views.

REAL analytics from this channel:
{data}

Current strategy (may be empty on the first run):
{current}

CRITICAL — be statistically honest and AVOID OVERFITTING. This channel likely has very \
few Shorts and little/no subscriber signal yet. With thin data, almost any pattern is \
noise. So:
- Make INCREMENTAL edits to the current strategy, not a full rewrite. Keep sections \
that lack strong evidence exactly as they are (list them under "holding").
- Only change priority_topics or boost_hashtags on AGGREGATED evidence (several Shorts, \
real differences in subs/retention) — never on one lucky video.
- Keep exploring topic categories until sample size is meaningful. Do not collapse onto \
recent winners; a channel that only makes one kind of Short is exactly what YouTube's \
inauthentic-content policy demonetises.

Return STRICT JSON:
- "confidence": "low" | "med" | "high" — your confidence given the data volume.
- "title_formulas": array of 3-6 title patterns that fit a documentary audience. \
Specific over sensational. Keep broad/proven ones when data is thin.
- "hook_style": one sentence on what the opening line should do to stop the swipe.
- "priority_topics": array of topic types to lean into (e.g. "maritime disappearance", \
"archive photograph", "cold case with a physical artifact"). Reorder only on evidence.
- "avoid": array of short things to stop doing (only if evidenced).
- "boost_hashtags": array of lowercase hashtags (no #). Change slowly.
- "pacing_note": one sentence on script length/beat pacing given the retention numbers.
- "changes": array of short strings describing what you actually changed this run \
(empty if nothing changed).
- "holding": array of short strings naming sections you deliberately left unchanged \
for lack of evidence.
- "rationale": 1-3 sentences citing the specific numbers. If data is too thin to \
justify changes, say so and keep prior guidance.
JSON only, no prose."""


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

    # Story mode and clip mode reward completely different things, so they get
    # different strategists — a prompt about which streamer to prioritise is noise
    # on a channel that doesn't repost anyone.
    from .config import load_config
    mode = ((load_config().get("autopilot") or {}).get("mode") or "clips").lower()
    template = _STORY_PROMPT if mode in ("story", "both") else _PROMPT

    prompt = template.format(
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
        conf = strat.get("confidence", "?")
        changes = strat.get("changes") or []
        chg = "; ".join(changes)[:120] if changes else "no changes (holding — data too thin)"
        print(f"  🧠 strategy updated (from {strat['based_on_clips']} clips, "
              f"confidence={conf}): {chg}")
    return strat
