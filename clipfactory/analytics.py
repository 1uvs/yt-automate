"""Learn what actually drives views + SUBSCRIBERS on your channel, and recommend
what to do more of. Pulls real YouTube Analytics, joins it with each clip's
format / streamer / length / hashtags, and ranks every dimension.

Reality check: analytics lag 1-3 days and you need a decent sample (this uses a
min-clips threshold) before a recommendation is trustworthy. No tool can promise
virality — this just tilts the odds toward whatever is already working for you.
"""
from __future__ import annotations

import json
from datetime import date

from . import state
from .config import OUTPUT_DIR
from .youtube import analytics_service

METRICS = "views,estimatedMinutesWatched,averageViewPercentage,subscribersGained,likes,shares"
MIN_CLIPS = 3  # need at least this many clips in a bucket before we trust it


def fetch_video_stats(video_ids: list[str], start_date: str = "2020-01-01") -> dict:
    if not video_ids:
        return {}
    resp = analytics_service().reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=date.today().isoformat(),
        metrics=METRICS,
        dimensions="video",
        filters="video==" + ",".join(video_ids[:450]),
        maxResults=450,
    ).execute()
    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    out = {}
    for row in resp.get("rows", []) or []:
        rec = dict(zip(headers, row))
        out[rec.pop("video")] = rec
    return out


def _meta_hashtags(cid: str) -> list[str]:
    p = OUTPUT_DIR / f"{cid}.json"
    if p.exists():
        try:
            return json.loads(p.read_text()).get("hashtags", []) or []
        except Exception:
            return []
    return []


def _length_bucket(sec) -> str:
    s = sec or 0
    if s <= 20:
        return "≤20s"
    if s <= 35:
        return "21-35s"
    if s <= 50:
        return "36-50s"
    return ">50s"


def published_with_stats() -> list[dict]:
    pub = [dict(r) for r in state.by_status("published") if r["youtube_id"]]
    stats = fetch_video_stats([p["youtube_id"] for p in pub])
    rows = []
    for p in pub:
        s = stats.get(p["youtube_id"], {})
        rows.append({
            "youtube_id": p["youtube_id"],
            "title": p["title"],
            "variant": p.get("variant") or "?",
            "streamer": p.get("streamer") or "?",
            "length_bucket": _length_bucket(p.get("length_sec")),
            "hashtags": _meta_hashtags(p["clip_id"]),
            "views": int(float(s.get("views", 0) or 0)),
            "subs": int(float(s.get("subscribersGained", 0) or 0)),
            "retention": round(float(s.get("averageViewPercentage", 0) or 0), 1),
            "likes": int(float(s.get("likes", 0) or 0)),
        })
    return rows


def _rank(rows: list[dict], key: str) -> list[dict]:
    agg: dict = {}
    for r in rows:
        a = agg.setdefault(r[key], {"name": r[key], "clips": 0, "subs": 0, "views": 0, "ret": 0.0})
        a["clips"] += 1
        a["subs"] += r["subs"]
        a["views"] += r["views"]
        a["ret"] += r["retention"]
    board = []
    for a in agg.values():
        n = max(1, a["clips"])
        board.append({
            "name": a["name"], "clips": a["clips"],
            "subs_per_clip": round(a["subs"] / n, 2),
            "avg_views": round(a["views"] / n, 1),
            "avg_retention": round(a["ret"] / n, 1),
        })
    board.sort(key=lambda x: (x["subs_per_clip"], x["avg_views"]), reverse=True)
    return board


def _rank_hashtags(rows: list[dict]) -> list[dict]:
    agg: dict = {}
    for r in rows:
        for tag in r["hashtags"]:
            a = agg.setdefault(tag, {"tag": tag, "clips": 0, "subs": 0, "views": 0})
            a["clips"] += 1
            a["subs"] += r["subs"]
            a["views"] += r["views"]
    board = [{
        "tag": a["tag"], "clips": a["clips"],
        "subs_per_clip": round(a["subs"] / max(1, a["clips"]), 2),
        "avg_views": round(a["views"] / max(1, a["clips"]), 1),
    } for a in agg.values() if a["clips"] >= 2]
    board.sort(key=lambda x: (x["subs_per_clip"], x["avg_views"]), reverse=True)
    return board


def insights() -> dict:
    rows = published_with_stats()
    total_subs = sum(r["subs"] for r in rows)
    by_format = _rank(rows, "variant")
    by_streamer = _rank(rows, "streamer")
    by_length = _rank(rows, "length_bucket")
    by_hashtag = _rank_hashtags(rows)

    recs = []
    if len(rows) < MIN_CLIPS:
        recs.append(f"Publish more clips ({len(rows)}/{MIN_CLIPS}+ needed) — then I can tell "
                    "you what's working. Consistency is the #1 driver of subs.")
    else:
        best_fmt = next((b for b in by_format if b["clips"] >= MIN_CLIPS), None)
        if best_fmt:
            recs.append(f"Format: '{best_fmt['name']}' is winning "
                        f"({best_fmt['subs_per_clip']} subs/clip). Lean into it.")
        good_streamers = [b for b in by_streamer if b["clips"] >= 2 and b["subs_per_clip"] > 0]
        if good_streamers:
            top = good_streamers[0]
            recs.append(f"Streamer: {top['name']} converts best "
                        f"({top['subs_per_clip']} subs/clip) — post more of them.")
        best_len = next((b for b in by_length if b["clips"] >= 2), None)
        if best_len:
            recs.append(f"Length: {best_len['name']} clips perform best — target that.")
        if by_hashtag:
            tops = ", ".join(f"#{h['tag']}" for h in by_hashtag[:3])
            recs.append(f"Hashtags driving subs: {tops} — now auto-boosted on new clips.")

    return {
        "clips": len(rows),
        "total_subs": total_subs,
        "by_format": by_format,
        "by_streamer": by_streamer,
        "by_length": by_length,
        "by_hashtag": by_hashtag,
        "recommendations": recs,
        # fed back into generation:
        "hashtag_boost": [h["tag"] for h in by_hashtag[:3]],
        "streamer_ranking": [b["name"] for b in by_streamer if b["subs_per_clip"] > 0],
        "best_format": (by_format[0]["name"] if by_format and by_format[0]["clips"] >= MIN_CLIPS else None),
    }


def safe_insights() -> dict | None:
    """Never raises — returns None if analytics aren't available yet."""
    try:
        return insights()
    except Exception:
        return None


# Back-compat for the earlier stats panel.
def variant_leaderboard():
    rows = published_with_stats()
    board = [{
        "variant": b["name"], "clips": b["clips"],
        "avg_views": b["avg_views"], "avg_retention": b["avg_retention"],
    } for b in _rank(rows, "variant")]
    per = [{
        "variant": r["variant"], "title": r["title"], "youtube_id": r["youtube_id"],
        "views": r["views"], "retention": r["retention"],
    } for r in sorted(rows, key=lambda x: x["views"], reverse=True)]
    return board, per
