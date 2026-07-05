"""Pull YouTube Analytics for published clips and rank the A/B variants.

Answers the real question: which format actually performs best on YOUR channel?
Note: YouTube Analytics lags ~1-3 days, so freshly published clips show zeros first.
"""
from __future__ import annotations

from datetime import date

from . import state
from .youtube import analytics_service

METRICS = "views,estimatedMinutesWatched,averageViewPercentage,averageViewDuration,likes"


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


def variant_leaderboard():
    """Return (leaderboard, per_video). leaderboard is sorted best-first."""
    pub = [dict(r) for r in state.by_status("published") if r["youtube_id"]]
    stats = fetch_video_stats([p["youtube_id"] for p in pub])

    per_video, agg = [], {}
    for p in pub:
        s = stats.get(p["youtube_id"], {})
        views = float(s.get("views", 0) or 0)
        retention = float(s.get("averageViewPercentage", 0) or 0)
        per_video.append({
            "variant": p.get("variant") or "?", "title": p["title"],
            "youtube_id": p["youtube_id"], "views": int(views),
            "retention": round(retention, 1),
        })
        v = p.get("variant") or "unknown"
        a = agg.setdefault(v, {"clips": 0, "views": 0.0, "ret": 0.0})
        a["clips"] += 1
        a["views"] += views
        a["ret"] += retention

    board = []
    for v, a in agg.items():
        n = max(1, a["clips"])
        board.append({
            "variant": v,
            "clips": a["clips"],
            "avg_views": round(a["views"] / n, 1),
            "avg_retention": round(a["ret"] / n, 1),
        })
    # rank by retention first (time-independent), then avg views
    board.sort(key=lambda x: (x["avg_retention"], x["avg_views"]), reverse=True)
    per_video.sort(key=lambda x: x["views"], reverse=True)
    return board, per_video
