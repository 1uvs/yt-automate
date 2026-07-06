"""End-to-end: discover clips (Twitch + YouTube VODs) -> caption -> render -> queue."""
from __future__ import annotations

import json
import traceback

from . import state, twitch, youtube_source
from .captions import build_caption_overlays
from .config import OUTPUT_DIR, load_config
from .download import cut_segment, download_clip
from .analytics import safe_insights
from .edit import probe_duration, render_vertical
from .metadata import generate_metadata
from .thumbnail import generate_thumbnail


def _gather_twitch(cfg: dict, only: list[str] | None,
                   ranking: list[str] | None = None,
                   max_clips: int | None = None) -> list[dict]:
    streamers = [s for s in cfg.get("streamers", []) if not only or s in only]
    if not streamers:
        return []
    # put the streamers that convert best (from analytics) first
    if ranking:
        streamers.sort(key=lambda s: ranking.index(s) if s in ranking else 999)
    ids = twitch.resolve_user_ids(streamers)

    # collect each streamer's eligible clips (best-first) separately
    per_streamer: dict[str, list[dict]] = {}
    for s in streamers:
        bid = ids.get(s.lower())
        if not bid:
            print(f"  ! could not resolve Twitch user '{s}' (skipping)")
            continue
        picks = []
        for c in twitch.top_clips(bid, cfg["lookback_hours"], first=40):
            if len(picks) >= cfg["clips_per_streamer"]:
                break
            if c.get("view_count", 0) < cfg["min_view_count"]:
                continue
            dur = c.get("duration", 0) or 0
            if dur and not (cfg["min_duration_sec"] <= dur <= cfg["max_duration_sec"]):
                continue
            if state.seen(c["id"]):
                continue
            picks.append({
                "id": c["id"], "streamer": s, "title": c.get("title", ""),
                "view_count": c.get("view_count", 0), "kind": "twitch",
                "clip_url": c["url"], "origin_url": c["url"],
            })
        if picks:
            per_streamer[s] = picks

    # round-robin across streamers (everyone's #1 before anyone's #2) for max variety,
    # capped at max_clips_per_run so runs stay reasonable.
    cap = max_clips if max_clips is not None else cfg.get("max_clips_per_run", 999)
    cands: list[dict] = []
    depth = 0
    while len(cands) < cap and any(len(v) > depth for v in per_streamer.values()):
        for s in streamers:
            lst = per_streamer.get(s)
            if lst and len(lst) > depth:
                cands.append(lst[depth])
                if len(cands) >= cap:
                    break
        depth += 1
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


def _process(cand: dict, cfg: dict, variant: dict | None = None,
             hashtag_boost: list[str] | None = None,
             title_examples: list[str] | None = None) -> None:
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

    cap_cfg = cfg.get("captions") or {}
    want_caps = cap_cfg.get("enabled", True) and streamer not in (cap_cfg.get("skip_streamers") or [])
    if want_caps and cap_cfg.get("skip_if_burned_in", True):
        from .burnin import looks_captioned
        if looks_captioned(src):
            want_caps = False
            print("    (captions off — source already has burned-in captions)")
    if want_caps:
        specs, transcript = build_caption_overlays(
            src, OUTPUT_DIR / f"{cid}_caps", res=(r["target_w"], r["target_h"])
        )
    else:
        specs, transcript = [], ""
        if streamer in (cap_cfg.get("skip_streamers") or []):
            print("    (captions off — this streamer burns their own)")
    out = OUTPUT_DIR / f"{cid}.mp4"
    render_vertical(src, out, specs, layout=layout,
                    w=r["target_w"], h=r["target_h"], max_sec=r["max_final_sec"])

    meta = generate_metadata(streamer, cand["title"], transcript,
                             cfg["publish"].get("tags_extra"), hashtag_boost=hashtag_boost,
                             title_examples=title_examples)

    thumb = OUTPUT_DIR / f"{cid}_thumb.jpg"
    try:
        generate_thumbnail(out, thumb, streamer, meta.get("hook", ""))
    except Exception as e:
        print(f"    (thumbnail failed: {e})")

    (OUTPUT_DIR / f"{cid}.json").write_text(json.dumps({
        "clip_id": cid, "streamer": streamer, "source_views": cand["view_count"],
        "source_url": cand.get("origin_url", ""), **meta,
    }, indent=2))
    state.upsert(cid, status="pending", output_path=str(out), title=meta["title"],
                 length_sec=probe_duration(out))
    print(f"    ✓ rendered -> {out.name}  |  \"{meta['title']}\"")


def _variant_for(cfg: dict, index: int) -> dict | None:
    ab = cfg.get("ab_test") or {}
    if not ab.get("enabled") or not ab.get("variants"):
        return None
    variants = ab["variants"]
    return variants[index % len(variants)]


def _safe_process(cand: dict, cfg: dict, variant: dict | None,
                  boost: list[str] | None = None,
                  titles: list[str] | None = None) -> None:
    try:
        _process(cand, cfg, variant, boost, titles)
    except Exception as e:
        state.upsert(cand["id"], streamer=cand["streamer"], status="failed", error=str(e))
        print(f"    x failed {cand['id']}: {e}")
        traceback.print_exc()


def run(limit_streamers: list[str] | None = None, max_clips: int | None = None,
        skip_youtube: bool = False) -> None:
    cfg = load_config()
    n = 0  # global counter so A/B variants alternate evenly across the whole run

    # Mark any scheduled posts whose time has passed as published (keeps slots sane).
    from .schedule import reconcile_scheduled
    reconcile_scheduled()

    # Learn from past performance (best-effort — needs published clips + analytics).
    ins = safe_insights()
    boost = ins["hashtag_boost"] if ins else None
    ranking = ins["streamer_ranking"] if ins else None
    titles = ins["top_titles"] if ins else None
    if ins and ins["recommendations"]:
        print("🧠 Coach:")
        for rec in ins["recommendations"]:
            print(f"   • {rec}")
        print()

    # Fold in the evolving strategy brain: its boosted hashtags + priority streamers
    # merge with (and take precedence over) the raw analytics ranking.
    from .strategy import load_strategy
    strat = load_strategy()
    if strat:
        boost = list(dict.fromkeys([*(strat.get("boost_hashtags") or []), *(boost or [])]))
        ranking = list(dict.fromkeys([*(strat.get("priority_streamers") or []), *(ranking or [])]))

    # Twitch first — these are fast, so clips show up in the queue right away.
    print("🔎 Checking Twitch clips…")
    tw = _gather_twitch(cfg, limit_streamers, ranking, max_clips)
    print(f"   found {len(tw)} new Twitch clip(s).\n")
    for i, cand in enumerate(tw, 1):
        v = _variant_for(cfg, n); n += 1
        tag = f" [{v['name']}]" if v else ""
        print(f"[{i}/{len(tw)}] {cand['streamer']}{tag}")
        _safe_process(cand, cfg, v, boost, titles)

    # YouTube (IShowSpeed) — downloads a full video, so it's slower. Skipped in
    # autopilot by default so daily runs stay fast/reliable.
    yt_cfg = cfg.get("youtube_streamers") or []
    if not skip_youtube and yt_cfg and (not limit_streamers or any(e["name"] in limit_streamers for e in yt_cfg)):
        print("\n🔎 Checking YouTube sources (IShowSpeed)…")
        print("   ⏳ downloading the latest video — this can take a few minutes, please wait.")
        yt = _gather_youtube(cfg, limit_streamers)
        print(f"   found {len(yt)} new YouTube clip(s).\n")
        for i, cand in enumerate(yt, 1):
            v = _variant_for(cfg, n); n += 1
            tag = f" [{v['name']}]" if v else ""
            print(f"[{i}/{len(yt)}] {cand['streamer']}{tag}")
            _safe_process(cand, cfg, v, boost, titles)

    pend = len(state.by_status("pending"))
    print(f"\n✅ Done. {pend} clip(s) waiting in the review queue.")
