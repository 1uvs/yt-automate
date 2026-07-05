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


def _process(cand: dict, cfg: dict, variant: dict | None = None) -> None:
    cid, streamer = cand["id"], cand["streamer"]
    state.upsert(cid, streamer=streamer, title=cand["title"],
                 view_count=cand["view_count"], status="discovered",
                 variant=(variant or {}).get("name"))
    r = cfg["render"]
    layout = (variant or {}).get("layout", r["layout"])

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
    render_vertical(src, out, specs, layout=layout,
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


def _variant_for(cfg: dict, index: int) -> dict | None:
    ab = cfg.get("ab_test") or {}
    if not ab.get("enabled") or not ab.get("variants"):
        return None
    variants = ab["variants"]
    return variants[index % len(variants)]


def _safe_process(cand: dict, cfg: dict, variant: dict | None) -> None:
    try:
        _process(cand, cfg, variant)
    except Exception as e:
        state.upsert(cand["id"], streamer=cand["streamer"], status="failed", error=str(e))
        print(f"    x failed {cand['id']}: {e}")
        traceback.print_exc()


def run(limit_streamers: list[str] | None = None) -> None:
    cfg = load_config()
    n = 0  # global counter so A/B variants alternate evenly across the whole run

    # Twitch first — these are fast, so clips show up in the queue right away.
    print("🔎 Checking Twitch clips…")
    tw = _gather_twitch(cfg, limit_streamers)
    print(f"   found {len(tw)} new Twitch clip(s).\n")
    for i, cand in enumerate(tw, 1):
        v = _variant_for(cfg, n); n += 1
        tag = f" [{v['name']}]" if v else ""
        print(f"[{i}/{len(tw)}] {cand['streamer']}{tag}")
        _safe_process(cand, cfg, v)

    # YouTube (IShowSpeed) — downloads a full video, so it's slower.
    yt_cfg = cfg.get("youtube_streamers") or []
    if yt_cfg and (not limit_streamers or any(e["name"] in limit_streamers for e in yt_cfg)):
        print("\n🔎 Checking YouTube sources (IShowSpeed)…")
        print("   ⏳ downloading the latest video — this can take a few minutes, please wait.")
        yt = _gather_youtube(cfg, limit_streamers)
        print(f"   found {len(yt)} new YouTube clip(s).\n")
        for i, cand in enumerate(yt, 1):
            v = _variant_for(cfg, n); n += 1
            tag = f" [{v['name']}]" if v else ""
            print(f"[{i}/{len(yt)}] {cand['streamer']}{tag}")
            _safe_process(cand, cfg, v)

    pend = len(state.by_status("pending"))
    print(f"\n✅ Done. {pend} clip(s) waiting in the review queue.")
