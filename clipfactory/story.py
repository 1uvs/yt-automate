"""Write original mystery/history story scripts with GPT — the source of the channel.

This replaces Twitch discovery as the front of the pipeline. Nothing is scraped or
reposted: every script is written here, narrated by our own voice, and illustrated
with our own images. That's deliberate. YouTube's 2026 *inauthentic content* policy
demonetises "generic, repetitive, or template-based" output, and the single most
targeted format is exactly the one this channel could most easily have become —
a scraped story read by a stock voice over stock gameplay.

Three things in here are what make a script defensible rather than template output:

* a **consistent narrator persona** with a point of view and editorial asides, so the
  channel sounds like someone rather than like a text-to-speech queue;
* **concrete, checkable specifics** — names, dates, places — which is what "original
  research" looks like at Shorts length, and what stops every script reading the same;
* a **loop**: the closing line hands back to the opening line. Replays count as views
  and a high replay rate is one of the strongest distribution signals there is.

The strategy brain (strategy.py) steers topic and hook selection from real analytics,
so what gets written drifts toward whatever is actually earning subscribers.
"""
from __future__ import annotations

import json
import re

from . import llm
from .config import DATA
from .strategy import load_strategy

USED_PATH = DATA / "used_topics.json"

# Kept small on purpose: the model sees recent topics to avoid repeating itself, and
# a channel that has covered 400 topics shouldn't blow its prompt budget listing them.
_RECENT_WINDOW = 120


def recent_asides(n: int = 12) -> list[str]:
    """Asides from recent scripts, so the narrator doesn't settle into a catchphrase."""
    out = []
    for u in reversed(_used()):
        a = (u.get("aside") or "").strip()
        if a and a not in out:
            out.append(a)
        if len(out) >= n:
            break
    return out


def _used() -> list[dict]:
    if USED_PATH.exists():
        try:
            return json.loads(USED_PATH.read_text())
        except Exception:
            return []
    return []


def remember_topic(key: str, title: str, aside: str = "") -> None:
    """Record a topic so future runs don't cover it (or repeat its aside) again."""
    used = _used()
    if any(u.get("key") == key for u in used):
        return
    used.append({"key": key, "title": title, "aside": aside})
    USED_PATH.write_text(json.dumps(used[-1000:], indent=2))


def used_keys() -> set[str]:
    return {u.get("key", "") for u in _used()}


_PERSONA = """You write for a single recurring narrator called {narrator}. The narrator \
is calm, precise and quietly obsessive — a researcher who finds the specific detail more \
unsettling than the spooky framing. The narrator:
- states facts plainly and lets them land, never oversells with "you won't believe"
- uses exact names, dates, places and numbers
- allows himself exactly one short editorial aside per script — a genuine reaction to
  THIS case's specific detail, phrased fresh every time. It must read as a thought he
  just had about this evidence, never a catchphrase. Do NOT reuse a stock line like
  "that's the part I can't get past"; if the aside would work unchanged on a different
  case, it is wrong and you must rewrite it
- never claims a supernatural explanation; where the record is unresolved, he says so"""


_PROMPT = """You are writing an original YouTube Short for a channel about \
{niche_desc}.

{persona}

{strategy}

Already covered — do NOT pick any of these topics again:
{used}

Asides you have already used on this channel — write something different in kind, not
just reworded:
{asides}

Write ONE script about a genuinely interesting, REAL, verifiable case. Prefer cases \
that are documented but not over-covered; avoid the ten or so stories every channel \
has already done to death unless you have a genuinely fresh angle.

STRUCTURE — read this carefully. The narration is assembled as:
    hook, then each beat in order, then loop_line
Each piece is spoken ONCE, exactly as written. Do NOT restate the hook at the start \
of the first beat, and do NOT end the last beat with the loop line — that makes the \
narrator stutter the same sentence twice. The beats carry the middle of the story only.

HARD REQUIREMENTS:
1. HOOK: the first line must land in under 2 seconds spoken, and must open a \
curiosity gap — a concrete, strange, specific fact. No "imagine if", no "here's a \
story about". Lead with the strangest verifiable detail.
2. LENGTH: {beats} beats, {words_per_beat} words each. Total narration must be \
{total_words} words or fewer — this has to fit in {target_sec} seconds spoken.
3. LOOP: the final line must flow naturally back into the hook, so a replay feels \
continuous. Do not summarise; hand the viewer back to the opening.
4. TRUTH: every factual claim must be accurate and checkable. If a detail is \
disputed or unknown, say so in the narration. Do not invent quotes, dates or names.
5. IMAGES: each beat needs an image_prompt for a cinematic, photoreal vertical still \
that illustrates that beat. Never depict a real identifiable person's face — use \
environments, objects, documents, landscapes, silhouettes, hands, weather, period \
detail. No text, captions, logos or watermarks in the image.
6. VISUAL VARIETY — this matters as much as the writing. Consecutive stills must not \
look alike. Across the beats, deliberately vary:
   - SHOT SCALE: alternate extreme close-up (an object filling the frame), medium \
(a desk, a doorway), and wide (a landscape, a street, a coastline). Never three \
close-ups of small objects in a row.
   - SETTING: do not set five beats in the same room. Move between interior and \
exterior, day and night, indoors and weather.
   - LIGHT AND COLOUR: vary the dominant tone beat to beat — cold daylight, grey \
overcast, warm lamplight, deep blue dusk. A story where every frame is a dim amber \
room reads as one long static image no matter how good each still is.
   The viewer should be able to tell the picture changed without reading the caption.

Return STRICT JSON:
- "topic_key": short lowercase slug identifying the case (e.g. "ss-ourang-medan")
- "title": the YouTube title, under 70 chars, specific and curiosity-driven, no clickbait lies
- "category": one of "unsolved-mystery", "history", "disappearance", "artifact"
- "hook": the opening line (spoken in under 2 seconds)
- "hook_image_prompt": the opening still — the strongest, strangest image in the \
script. It is the first frame the viewer sees and decides whether they swipe.
- "beats": array of {beats} objects, each with:
    - "narration": the spoken words for this beat
    - "image_prompt": the still to generate for it
- "loop_line": the closing line that hands back to the hook
- "aside": the one editorial aside, quoted exactly as it appears in a beat
- "payoff": one sentence — what the viewer actually learns
- "fact_risk": "low" | "med" | "high" — your honest confidence the claims are accurate
- "why_this_works": one sentence on the retention mechanic you used
JSON only, no prose."""


def _strategy_block() -> str:
    s = load_strategy()
    if not s:
        return "No performance data yet — write for broad appeal within the niche."
    bits = []
    if s.get("hook_style"):
        bits.append(f"Hook style that is working: {s['hook_style']}")
    for key, label in (("priority_topics", "Lean toward these topic types"),
                       ("title_formulas", "Title patterns that are working"),
                       ("avoid", "Stop doing")):
        if s.get(key):
            bits.append(f"{label}: {', '.join(str(x) for x in s[key][:6])}")
    if not bits:
        return "No strong performance signal yet — write for broad appeal."
    return ("What this channel's real analytics say (follow this):\n  "
            + "\n  ".join(bits))


def _parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _validate(s: dict, beats: int, total_words: int) -> str | None:
    """Return a reason the script is unusable, or None if it's good."""
    for k in ("topic_key", "title", "hook", "hook_image_prompt", "beats", "loop_line"):
        if not s.get(k):
            return f"missing '{k}'"
    if not isinstance(s["beats"], list) or len(s["beats"]) < 2:
        return "too few beats"
    for b in s["beats"]:
        if not b.get("narration") or not b.get("image_prompt"):
            return "a beat is missing narration or image_prompt"
    spoken = narration_text(s)
    n = len(spoken.split())
    # A long script doesn't fail the render, it just overruns the target length —
    # so this is a generous ceiling, not the word budget from the prompt.
    if n > total_words * 1.6:
        return f"script too long ({n} words)"
    if s.get("fact_risk") == "high":
        return "model flagged its own facts as high-risk"
    return None


def _norm_words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()


_TRAILING_CONNECTIVES = {"then", "and", "but", "so", "until", "before", "when", "where"}


def dedupe_segments(story: dict) -> dict:
    """Strip the hook off the first beat and the loop line off the last one.

    Models restate the hook to open the story and echo the loop line to close it,
    even when told not to. Left alone, the narrator says the same sentence twice in
    a row at both ends of the Short — which sounds like a broken file, and costs the
    first two seconds, the only ones that decide whether the viewer stays.
    """
    beats = story.get("beats") or []
    if not beats:
        return story

    hook_w = _norm_words(story.get("hook", ""))
    first_w = _norm_words(beats[0].get("narration", ""))
    if hook_w and first_w[:len(hook_w)] == hook_w:
        words = beats[0]["narration"].split()
        beats[0]["narration"] = " ".join(words[len(hook_w):]).lstrip(" ,.;:—-")
        beats[0]["narration"] = beats[0]["narration"][:1].upper() + beats[0]["narration"][1:]

    loop_w = _norm_words(story.get("loop_line", ""))
    last_w = _norm_words(beats[-1].get("narration", ""))
    # Longest tail the last beat shares with the loop line (they rarely match exactly
    # — the beat leads into it, so only the closing phrase overlaps).
    overlap = 0
    for k in range(min(len(loop_w), len(last_w)), 3, -1):
        if last_w[-k:] == loop_w[-k:]:
            overlap = k
            break
    if overlap:
        words = beats[-1]["narration"].split()
        kept = words[:-overlap] if overlap < len(words) else []
        while kept and _norm_words(kept[-1]) and _norm_words(kept[-1])[0] in _TRAILING_CONNECTIVES:
            kept.pop()
        trimmed = " ".join(kept).rstrip(" ,.;:—-")
        # The beat now ends mid-sentence; TTS needs the full stop to place the pause.
        if trimmed and trimmed[-1] not in ".!?":
            trimmed += "."
        beats[-1]["narration"] = trimmed

    # A beat emptied by the trim would render as a silent image.
    story["beats"] = [b for b in beats if (b.get("narration") or "").strip()]
    return story


def narration_text(s: dict) -> str:
    """The full spoken script, in order: hook -> beats -> loop line."""
    parts = [s.get("hook", "")]
    parts += [b.get("narration", "") for b in s.get("beats", [])]
    parts.append(s.get("loop_line", ""))
    return " ".join(p.strip() for p in parts if p and p.strip())


def write_story(cfg: dict, attempts: int = 3) -> dict | None:
    """Generate one original script. Returns None if GPT is unavailable."""
    sc = cfg.get("story") or {}
    beats = int(sc.get("beats", 6))
    wpb = int(sc.get("words_per_beat", 18))
    target_sec = int(sc.get("target_sec", 42))
    total_words = int(sc.get("max_words", beats * wpb + 25))

    used = sorted(used_keys())
    used_block = ", ".join(used[-_RECENT_WINDOW:]) if used else "(nothing yet)"

    prompt = _PROMPT.format(
        niche_desc=sc.get("niche_desc", "unsolved mysteries and strange history"),
        persona=_PERSONA.format(narrator=sc.get("narrator", "The Archivist")),
        strategy=_strategy_block(),
        used=used_block,
        asides="\n".join(f"- {a}" for a in recent_asides()) or "(none yet)",
        beats=beats, words_per_beat=wpb,
        total_words=total_words, target_sec=target_sec,
    )

    for i in range(attempts):
        raw = llm.chat_json(prompt, model=llm.smart_model(), max_tokens=4000)
        if not raw:
            return None
        s = _parse(raw)
        if not s:
            print(f"    (script attempt {i+1}: unparseable JSON, retrying)")
            continue
        s = dedupe_segments(s)
        bad = _validate(s, beats, total_words)
        if bad:
            print(f"    (script attempt {i+1} rejected: {bad})")
            continue
        if s["topic_key"] in used_keys():
            print(f"    (script attempt {i+1}: '{s['topic_key']}' already covered, retrying)")
            continue
        s["narration"] = narration_text(s)
        return s
    return None
