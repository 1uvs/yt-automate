"""End-to-end: discover clips (Twitch + YouTube VODs) -> caption -> render -> queue."""
from __future__ import annotations

import json
import traceback

from . import state, twitch, youtube_source
from .captions import build_caption_overlays
from .config import OUTPUT_DIR, load_config
from .download import cut_segment, download_clip
from .edit import render_vertical
from .metadata import generate_metadata
from .thumbnail import generate_thumbnail


def _gather_twitch(cfg: dict, only: list[str] | None) -> list[dict]:
    streamers = [s for s in cfg.get("streamers", []) if not only or s in only]
    if not streamers:
        return []
    ids = twitch.resolve_user_ids(streamers)
    cands: list[dict] = []
    for s in streamers:
        bid = ids.get(s.lower())
        if not bid:
            print(f"  ! could not resolve Twitch user '{s}' (skipping)")
            continue
        picked = 0
        for c in twitch.top_clips(bid, cfg["lookback_hours"], first=25):
            if picked >= cfg["clips_per_streamer"]:
                break
            if c.get("view_count", 0) < cfg["min_view_count"]:
                continue
            dur = c.get("duration", 0) or 0
            if dur and not (cfg["min_duration_sec"] <= dur <= cfg["max_duration_sec"]):
                continue
            if state.seen(c["id"]):
                continue
            cands.append({
                "id": c["id"], "streamer": s, "title": c.get("title", ""),
                "view_count": c.get("view_count", 0), "kind": "twitch",
                "clip_url": c["url"], "origin_url": c["url"],
            })
            picked += 1
    return cands


def _gather_youtube(cfg: dict, only: list[str] | None) -> list[dict]:
    cands: list[dict] = []
    for entry in cfg.get("youtube_streamers", []) or []:
        if only and entry["name"] not in only:
            continue
        try:
            for c in youtube_source.get_candidates(entry):
                if not state.seen(c["id"]):
                    cands.append(c)
        except Exception as e:
            print(f"  ! youtube source '{entry['name']}' failed: {e}")
    return cands


def _process(cand: dict, cfg: dict) -> None:
    cid, streamer = cand["id"], cand["streamer"]
    state.upsert(cid, streamer=streamer, title=cand["title"],
                 view_count=cand["view_count"], status="discovered")
    r = cfg["render"]

    tag = f"{cand['view_count']} views" if cand["kind"] == "twitch" else "loudness peak"
    print(f"  ↓ {streamer}: '{cand['title'][:50]}' ({tag})")

    if cand["kind"] == "twitch":
        src = download_clip(cand["clip_url"], cid)
    else:  # vod highlight
        from pathlib import Path
        src = cut_segment(Path(cand["source_path"]), cid, cand["start"], cand["end"])

    specs, transcript = build_caption_overlays(
        src, OUTPUT_DIR / f"{cid}_caps", res=(r["target_w"], r["target_h"])
    )
    out = OUTPUT_DIR / f"{cid}.mp4"
    render_vertical(src, out, specs, layout=r["layout"],
                    w=r["target_w"], h=r["target_h"], max_sec=r["max_final_sec"])

    meta = generate_metadata(streamer, cand["title"], transcript,
                             cfg["publish"].get("tags_extra"))

    thumb = OUTPUT_DIR / f"{cid}_thumb.jpg"
    try:
        generate_thumbnail(out, thumb, streamer, meta.get("hook", ""))
    except Exception as e:
        print(f"    (thumbnail failed: {e})")

    (OUTPUT_DIR / f"{cid}.json").write_text(json.dumps({
        "clip_id": cid, "streamer": streamer, "source_views": cand["view_count"],
        "source_url": cand.get("origin_url", ""), **meta,
    }, indent=2))
    state.upsert(cid, status="pending", output_path=str(out), title=meta["title"])
    print(f"    ✓ rendered -> {out.name}  |  \"{meta['title']}\"")


def run(limit_streamers: list[str] | None = None) -> None:
    cfg = load_config()
    candidates = _gather_twitch(cfg, limit_streamers) + _gather_youtube(cfg, limit_streamers)
    print(f"{len(candidates)} new candidate clip(s) to process.\n")

    for cand in candidates:
        try:
            _process(cand, cfg)
        except Exception as e:
            state.upsert(cand["id"], streamer=cand["streamer"], status="failed", error=str(e))
            print(f"    x failed {cand['id']}: {e}")
            traceback.print_exc()

    pend = len(state.by_status("pending"))
    print(f"\nDone. {pend} clip(s) waiting in the review queue -> `python review.py list`")
