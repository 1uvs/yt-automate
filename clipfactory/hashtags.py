"""Hashtag strategy for YouTube Shorts.

YouTube shows only the first ~3 hashtags above the title and ignores videos with
>15 hashtags entirely. So the winning play is a small, focused set: one broad
discovery tag, the streamer's community tags, and 1-2 content tags. Over time the
coach (see analytics.py) can pass a `boost` list of hashtags that have actually
correlated with more views on your channel, and they get prioritized.
"""
from __future__ import annotations

EVERGREEN = ["shorts", "viral", "fyp"]

# Community / fandom tags per streamer — these reach the people already searching.
STREAMER_TAGS = {
    "kaicenat": ["kaicenat", "amp", "ampclips"],
    "ishowspeed": ["ishowspeed", "speed", "speedclips"],
    "ksi": ["ksi", "sidemen", "sidemenclips"],
    "jasontheween": ["jasontheween", "jason"],
    "duke": ["dukedennis", "amp"],
    "agent00": ["agent00", "amp"],
    "fanum": ["fanum", "amp"],
}

# If these words show up in the title/transcript, add the matching content tag.
CONTENT_TAGS = {
    "funny": ["rage", "mad", "angry", "funny", "hilarious", "lmao", "lol", "crazy"],
    "fight": ["fight", "beef", "argue", "argument", "drama"],
    "gaming": ["game", "gaming", "gta", "fortnite", "minecraft", "clutch", "win"],
    "reaction": ["reaction", "reacts", "shocked", "insane", "no way", "unbelievable"],
    "irl": ["stream", "chat", "irl", "sub", "donation"],
}


def build_hashtags(streamer: str, title: str = "", transcript: str = "",
                   boost: list[str] | None = None, limit: int = 5) -> list[str]:
    s = streamer.lower()
    text = f"{title} {transcript}".lower()

    ordered: list[str] = []

    def add(tag: str):
        tag = tag.lstrip("#").lower().replace(" ", "")
        if tag and tag not in ordered:
            ordered.append(tag)

    # 1) learned winners first (if the coach found any)
    for t in (boost or []):
        add(t)
    # 2) always-on discovery tag
    add("shorts")
    # 3) streamer community tags
    for t in STREAMER_TAGS.get(s, [s]):
        add(t)
    # 4) content tags detected from the clip
    for tag, kws in CONTENT_TAGS.items():
        if any(k in text for k in kws):
            add(tag)
    # 5) top up with evergreen
    for t in EVERGREEN:
        add(t)

    return ordered[:limit]


def hashtag_line(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags)
