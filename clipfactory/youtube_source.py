"""Source clips from a YouTube channel (for streamers not on Twitch, e.g. IShowSpeed).

Pulls recent uploads, downloads the ones in a sensible length range, finds their
loudest moments, and yields candidate segments for the normal render pipeline.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import CLIPS_DIR
from .highlight import detect_highlights


def recent_videos(channel: str, limit: int = 6) -> list[dict]:
    """List recent uploads (id, title, duration, view_count) via yt-dlp, no download."""
    url = channel.rstrip("/")
    if not url.endswith(("/videos", "/streams")):
        url += "/videos"
    proc = subprocess.run(
        ["yt-dlp", "-J", "--flat-playlist", "--playlist-end", str(limit), url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp channel listing failed:\n{proc.stderr[-800:]}")
    data = json.loads(proc.stdout)
    out = []
    for e in data.get("entries", []) or []:
        if not e.get("id"):
            continue
        out.append({
            "id": e["id"],
            "title": e.get("title", ""),
            "duration": e.get("duration") or 0,
            "view_count": e.get("view_count") or 0,
            "url": f"https://www.youtube.com/watch?v={e['id']}",
        })
    return out


def download_video(url: str, video_id: str, max_height: int = 1080) -> Path:
    dst = CLIPS_DIR / f"_vod_{video_id}.mp4"
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    fmt = (f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
           f"b[height<={max_height}][ext=mp4]/b")
    proc = subprocess.run(
        ["yt-dlp", "-f", fmt, "--merge-output-format", "mp4",
         "--no-playlist", "-o", str(dst), url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"yt-dlp download failed for {url}:\n{proc.stderr[-800:]}")
    return dst


def get_candidates(cfg_entry: dict) -> list[dict]:
    """Return normalized candidate dicts for one youtube_streamers config entry."""
    name = cfg_entry["name"]
    vids = recent_videos(cfg_entry["channel"], limit=cfg_entry.get("scan_videos", 6))

    # skip music videos / trailers / promo uploads (copyright + not real "moments")
    skips = [s.lower() for s in cfg_entry.get("skip_titles", [])]
    if skips:
        kept = []
        for v in vids:
            t = v["title"].lower()
            if any(s in t for s in skips):
                print(f"  ⊘ skipping '{v['title'][:50]}' (matched skip filter)")
            else:
                kept.append(v)
        vids = kept

    lo = cfg_entry.get("min_video_sec", 120)
    hi = cfg_entry.get("max_video_sec", 1800)
    vids = [v for v in vids if lo <= (v["duration"] or 0) <= hi]
    vids.sort(key=lambda v: v["view_count"], reverse=True)
    vids = vids[: cfg_entry.get("max_videos", 2)]

    per = cfg_entry.get("highlights_per_video", 3)
    clip_len = cfg_entry.get("clip_len_sec", 30)
    candidates = []
    for v in vids:
        vod = download_video(v["url"], v["id"], cfg_entry.get("max_height", 1080))
        segs = detect_highlights(vod, n=per, clip_len=clip_len)
        for k, (start, end) in enumerate(segs):
            candidates.append({
                "id": f"yt_{v['id']}_{k}",
                "streamer": name,
                "title": v["title"],
                "view_count": v["view_count"],
                "duration": end - start,
                "kind": "vod",
                "source_path": str(vod),
                "start": start,
                "end": end,
                "origin_url": v["url"],
            })
    return candidates
