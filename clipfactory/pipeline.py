"""End-to-end: discover clips (Twitch + YouTube VODs) -> caption -> render -> queue.

Step order matters here, and it's chosen around one rule: **never pay for work on a
clip that won't be published.** The screener needs a transcript, and the transcript
is also what makes titles specific, so the order is

    download -> pick the best stretch -> transcribe -> SCREEN -> caption PNGs ->
    render -> metadata -> thumbnail -> queue

Screening sits immediately after transcription and before the two genuinely
expensive steps (rasterising ~150 caption PNGs and the ffmpeg render), so a rejected
clip costs a download and a transcript instead of a full render.
"""
from __future__ import annotations

import difflib
import json
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import state, twitch, youtube_source
from .analytics import safe_insights
from .captions import clip_words, render_caption_overlays, transcribe, words_to_text
from .config import OUTPUT_DIR, load_config
from .download import cut_segment, download_clip
from .edit import probe_duration, render_vertical
from .highlight import best_window
from .metadata import generate_metadata
from .prefetch import Prefetcher
from .thumbnail import generate_thumbnail


def _perf(cfg: dict) -> dict:
    return cfg.get("performance") or {}


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).strip()


def _is_near_dupe(title: str, kept: list[str], threshold: float = 0.86) -> bool:
    """True if this title is a near-copy of one we already picked.

    A moment that pops off gets clipped by a dozen viewers at once, so a streamer's
    top-clips list is often the same 5 seconds under 5 slightly different titles.
    Publishing all of them looks like a spam channel.
    """
    n = _norm_title(title)
    if not n:
        return False
    return any(difflib.SequenceMatcher(None, n, k).ratio() >= threshold for k in kept)


def _eligible_picks(clips: list[dict], cfg: dict, streamer: str,
                    already: set[str]) -> list[dict]:
    picks: list[dict] = []
    titles: list[str] = []
    for c in clips:
        if len(picks) >= cfg["clips_per_streamer"]:
            break
        if c.get("view_count", 0) < cfg["min_view_count"]:
            continue
        dur = c.get("duration", 0) or 0
        if dur and not (cfg["min_duration_sec"] <= dur <= cfg["max_duration_sec"]):
            continue
        if c["id"] in already:
            continue
        if _is_near_dupe(c.get("title", ""), titles):
            continue
        titles.append(_norm_title(c.get("title", "")))
        picks.append({
            "id": c["id"], "streamer": streamer, "title": c.get("title", ""),
            "view_count": c.get("view_count", 0), "kind": "twitch",
            "clip_url": c["url"], "origin_url": c["url"],
        })
    return picks


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

    # One HTTP round trip per streamer, and none of them depend on each other — with
    # a 28-streamer roster, doing these serially was the bulk of discovery time.
    def fetch(s: str) -> tuple[str, list[dict] | None]:
        bid = ids.get(s.lower())
        if not bid:
            return s, None
        try:
            return s, twitch.top_clips(bid, cfg["lookback_hours"], first=40)
        except Exception as e:
            print(f"  ! twitch lookup failed for '{s}': {str(e)[:90]}")
            return s, []

    workers = min(int(_perf(cfg).get("discovery_workers", 8)), len(streamers))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        fetched = dict(pool.map(fetch, streamers))

    for s, clips in fetched.items():
        if clips is None:
            print(f"  ! could not resolve Twitch user '{s}' (skipping)")

    # One "have I seen this?" query for the whole run instead of one per candidate.
    already = state.seen_many([c["id"] for v in fetched.values() if v for c in v])

    per_streamer: dict[str, list[dict]] = {}
    for s in streamers:
        clips = fetched.get(s)
        if not clips:
            continue
        picks = _eligible_picks(clips, cfg, s, already)
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
            found = youtube_source.get_candidates(entry)
            fresh = state.seen_many([c["id"] for c in found])
            cands += [c for c in found if c["id"] not in fresh]
        except Exception as e:
            print(f"  ! youtube source '{entry['name']}' failed: {e}")
    return cands


def _source_file(cand: dict, fetcher: Prefetcher | None) -> Path:
    if cand["kind"] == "twitch":
        if fetcher is not None:
            return fetcher.get(cand)
        return download_clip(cand["clip_url"], cand["id"])
    return cut_segment(Path(cand["source_path"]), cand["id"], cand["start"], cand["end"])


def _pick_window(src: Path, max_final: float) -> tuple[float, float] | None:
    """Choose which stretch of an over-long source to keep, or None to use the head.

    Source clips run up to max_duration_sec (75s by default) but a Short is trimmed
    to max_final_sec, so something gets cut. Keeping the first N seconds throws away
    the payoff — which is usually the loudest moment, and usually near the end.
    """
    dur = probe_duration(src) or 0.0
    if dur <= max_final + 1.0:
        return None
    try:
        return best_window(src, max_final)
    except Exception as e:
        print(f"    (loudness trim skipped, using the first {max_final:.0f}s: {e})")
        return None


def _captions_wanted(cfg_caps: dict, streamer: str, src: Path) -> bool:
    if not cfg_caps.get("enabled", True):
        return False
    if streamer in (cfg_caps.get("skip_streamers") or []):
        print("    (captions off — this streamer burns their own)")
        return False
    if cfg_caps.get("skip_if_burned_in", True):
        from .burnin import looks_captioned

        if looks_captioned(src):
            print("    (captions off — source already has burned-in captions)")
            return False
    return True


def _process(cand: dict, cfg: dict, variant: dict | None = None,
             hashtag_boost: list[str] | None = None,
             title_examples: list[str] | None = None,
             fetcher: Prefetcher | None = None) -> None:
    cid, streamer = cand["id"], cand["streamer"]
    state.upsert(cid, streamer=streamer, title=cand["title"],
                 view_count=cand["view_count"], status="discovered",
                 variant=(variant or {}).get("name"))
    r = cfg["render"]
    cap_cfg = cfg.get("captions") or {}
    layout = (variant or {}).get("layout", r["layout"])

    tag = f"{cand['view_count']} views" if cand["kind"] == "twitch" else "loudness peak"
    print(f"  ↓ {streamer}: '{cand['title'][:50]}' ({tag})")

    src = _source_file(cand, fetcher)

    # 1. Which stretch are we actually publishing? Everything downstream (captions,
    #    transcript, screening) is about that stretch, not the whole source.
    max_final = float(r["max_final_sec"])
    window = _pick_window(src, max_final)
    if window:
        print(f"    ✂ best {max_final:.0f}s starts at {window[0]:.0f}s (loudest stretch)")

    # 2. Captions on or off for this clip.
    want_caps = _captions_wanted(cap_cfg, streamer, src)

    # 3. Transcribe. Even when captions are off, the transcript is what lets the
    #    screener judge risk and the title generator name what actually happened —
    #    without it those clips get generic titles and an unscreened pass.
    need_text = want_caps or cap_cfg.get("transcribe_when_captions_off", True)
    words = clip_words(transcribe(src), window) if need_text else []
    transcript = words_to_text(words)

    # 4. Safety + quality gate. This runs before the caption PNGs and the render —
    #    the two steps that actually cost minutes — so a rejected clip costs a
    #    download and a transcript, not a full render + upload.
    sc_cfg = cfg.get("screening") or {}
    screening = None
    if sc_cfg.get("enabled", True):
        from .screen import screen_clip

        screening = screen_clip(streamer, cand["title"], transcript,
                                min_quality=sc_cfg.get("min_quality", 25),
                                block_high_risk=sc_cfg.get("block_high_risk", True))
        if not screening["publish"]:
            reason = screening["verdict"] or (", ".join(screening["risk_reasons"])
                                              or "screened out")
            state.upsert(cid, status="rejected",
                         error=f"screened out ({screening['risk']} risk, "
                               f"q{screening['quality']}): {reason}")
            print(f"    ⨯ screened out — {screening['risk']} risk / "
                  f"quality {screening['quality']}: {reason}")
            return

    # 5. Caption overlays (only now that the clip has earned them).
    specs = []
    if want_caps and words:
        specs = render_caption_overlays(
            words, OUTPUT_DIR / f"{cid}_caps", res=(r["target_w"], r["target_h"])
        )

    out = OUTPUT_DIR / f"{cid}.mp4"
    render_vertical(src, out, specs, layout=layout,
                    w=r["target_w"], h=r["target_h"], max_sec=int(max_final),
                    start=window[0] if window else 0.0)

    # Caption PNGs are baked into the mp4 now — drop them so the queue doesn't
    # accumulate ~100 tiny files per pending clip (not just after publish).
    if specs:
        import shutil

        shutil.rmtree(OUTPUT_DIR / f"{cid}_caps", ignore_errors=True)

    meta = generate_metadata(streamer, cand["title"], transcript,
                             cfg["publish"].get("tags_extra"), hashtag_boost=hashtag_boost,
                             title_examples=title_examples)

    # Vision picks the most clickable frame for the thumbnail (falls back to ffmpeg's
    # representative-frame heuristic if vision is unavailable).
    frame_at = None
    if (cfg.get("thumbnail") or {}).get("vision", True):
        try:
            from .vision_thumb import pick_thumbnail_time

            frame_at = pick_thumbnail_time(out)
        except Exception as e:
            print(f"    (vision thumbnail pick skipped: {e})")

    thumb = OUTPUT_DIR / f"{cid}_thumb.jpg"
    try:
        generate_thumbnail(out, thumb, streamer, meta.get("hook", ""), frame_at=frame_at)
    except Exception as e:
        print(f"    (thumbnail failed: {e})")

    (OUTPUT_DIR / f"{cid}.json").write_text(json.dumps({
        "clip_id": cid, "streamer": streamer, "source_views": cand["view_count"],
        "source_url": cand.get("origin_url", ""),
        "trim_start": round(window[0], 2) if window else 0,
        "screen_quality": (screening or {}).get("quality"),
        "screen_risk": (screening or {}).get("risk"),
        **meta,
    }, indent=2))
    state.upsert(cid, status="pending", output_path=str(out), title=meta["title"],
                 length_sec=probe_duration(out), error=None)
    print(f"    ✓ rendered -> {out.name}  |  \"{meta['title']}\"")


def _variant_for(cfg: dict, index: int) -> dict | None:
    ab = cfg.get("ab_test") or {}
    if not ab.get("enabled") or not ab.get("variants"):
        return None
    variants = ab["variants"]
    return variants[index % len(variants)]


def _safe_process(cand: dict, cfg: dict, variant: dict | None,
                  boost: list[str] | None = None,
                  titles: list[str] | None = None,
                  fetcher: Prefetcher | None = None) -> None:
    try:
        _process(cand, cfg, variant, boost, titles, fetcher)
    except Exception as e:
        used = state.record_failure(cand["id"], str(e), streamer=cand["streamer"],
                                    title=cand["title"], view_count=cand["view_count"])
        left = max(0, state.MAX_ATTEMPTS - used)
        note = f" — will retry next run ({left} left)" if left else " — giving up on it"
        print(f"    x failed {cand['id']}: {str(e)[:160]}{note}")
        traceback.print_exc()


def _process_batch(cands: list[dict], cfg: dict, start_index: int,
                   boost, titles, label: str) -> int:
    """Run a list of candidates through the pipeline, prefetching downloads ahead."""
    perf = _perf(cfg)
    n = start_index
    with Prefetcher(cands,
                    depth=int(perf.get("prefetch_depth", 2)),
                    workers=int(perf.get("download_workers", 2))) as fetcher:
        for i, cand in enumerate(cands, 1):
            fetcher.prime(i - 1)
            v = _variant_for(cfg, n)
            n += 1
            tag = f" [{v['name']}]" if v else ""
            print(f"[{i}/{len(cands)}] {cand['streamer']}{tag}{label}")
            _safe_process(cand, cfg, v, boost, titles, fetcher)
    return n


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

    before_rejected = len(state.by_status("rejected"))

    # Twitch first — these are fast, so clips show up in the queue right away.
    print("🔎 Checking Twitch clips…")
    tw = _gather_twitch(cfg, limit_streamers, ranking, max_clips)
    print(f"   found {len(tw)} new Twitch clip(s).\n")
    n = _process_batch(tw, cfg, n, boost, titles, "")

    # YouTube (IShowSpeed) — downloads a full video, so it's slower. Skipped in
    # autopilot by default so daily runs stay fast/reliable.
    yt_cfg = cfg.get("youtube_streamers") or []
    if not skip_youtube and yt_cfg and (not limit_streamers or any(e["name"] in limit_streamers for e in yt_cfg)):
        print("\n🔎 Checking YouTube sources (IShowSpeed)…")
        print("   ⏳ downloading the latest video — this can take a few minutes, please wait.")
        yt = _gather_youtube(cfg, limit_streamers)
        print(f"   found {len(yt)} new YouTube clip(s).\n")
        n = _process_batch(yt, cfg, n, boost, titles, "")

    pend = len(state.by_status("pending"))
    rejected = len(state.by_status("rejected")) - before_rejected
    extra = f"  ({rejected} screened out before rendering)" if rejected > 0 else ""
    print(f"\n✅ Done. {pend} clip(s) waiting in the review queue.{extra}")
