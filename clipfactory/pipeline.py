"""End-to-end: discover top clips -> download -> caption -> render -> queue for review."""
from __future__ import annotations

import json
import traceback
from pathlib import Path

from . import state, twitch
from .captions import build_caption_overlays
from .config import OUTPUT_DIR, load_config
from .download import download_clip
from .edit import render_vertical
from .metadata import generate_metadata


def _process_one(clip: dict, streamer: str, cfg: dict) -> None:
    cid = clip["id"]
    title = clip.get("title", "")
    views = clip.get("view_count", 0)
    state.upsert(cid, streamer=streamer, title=title, view_count=views, status="discovered")

    r = cfg["render"]
    dur = clip.get("duration", 0) or 0
    if dur and not (cfg["min_duration_sec"] <= dur <= cfg["max_duration_sec"]):
        state.upsert(cid, status="rejected", error=f"duration {dur}s out of range")
        return

    print(f"  ↓ {streamer}: '{title[:50]}' ({views} views)")
    src = download_clip(clip["url"], cid)

    cap_dir = OUTPUT_DIR / f"{cid}_caps"
    specs, transcript = build_caption_overlays(
        src, cap_dir, res=(r["target_w"], r["target_h"])
    )

    out = OUTPUT_DIR / f"{cid}.mp4"
    render_vertical(
        src, out, specs,
        layout=r["layout"], w=r["target_w"], h=r["target_h"], max_sec=r["max_final_sec"],
    )

    meta = generate_metadata(streamer, title, transcript, cfg["publish"].get("tags_extra"))
    (OUTPUT_DIR / f"{cid}.json").write_text(json.dumps({
        "clip_id": cid, "streamer": streamer, "source_views": views,
        "twitch_url": clip["url"], **meta,
    }, indent=2))

    state.upsert(cid, status="pending", output_path=str(out), title=meta["title"])
    print(f"    ✓ rendered -> {out.name}  |  \"{meta['title']}\"")


def run(limit_streamers: list[str] | None = None) -> None:
    cfg = load_config()
    streamers = limit_streamers or cfg["streamers"]
    ids = twitch.resolve_user_ids(streamers)

    for s in streamers:
        bid = ids.get(s.lower())
        if not bid:
            print(f"  ! could not resolve Twitch user '{s}' (skipping)")
            continue
        clips = twitch.top_clips(bid, cfg["lookback_hours"], first=25)
        picked = 0
        for clip in clips:
            if picked >= cfg["clips_per_streamer"]:
                break
            if clip.get("view_count", 0) < cfg["min_view_count"]:
                continue
            if state.seen(clip["id"]):
                continue
            try:
                _process_one(clip, s, cfg)
                picked += 1
            except Exception as e:
                state.upsert(clip["id"], streamer=s, status="failed", error=str(e))
                print(f"    x failed {clip['id']}: {e}")
                traceback.print_exc()

    pend = len(state.by_status("pending"))
    print(f"\nDone. {pend} clip(s) waiting in the review queue -> `python review.py list`")
