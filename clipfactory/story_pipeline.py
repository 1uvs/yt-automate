"""End-to-end for one original story Short: write -> narrate -> illustrate -> render.

    script -> narration mp3 -> beat images -> ken burns base -> captions -> queue

Ordering follows the same rule as the clip pipeline: never pay for expensive work on
something that won't publish. The script is checked before a cent is spent on images
(the costly step), and images only start once we have audio whose real duration tells
us how long each beat should be on screen.

The hook image is reused for the closing loop line, so the last frame matches the
first — the replay reads as continuous rather than as a restart.
"""
from __future__ import annotations

import json
import shutil
import traceback

from . import imagery, state, voice
from .captions import align_to_script, render_caption_overlays, transcribe
from .config import CLIPS_DIR, OUTPUT_DIR
from .edit import probe_duration, render_vertical
from .kenburns import apportion, build_base, split_long_holds
from .metadata import generate_story_metadata
from .story import remember_topic, write_story
from .thumbnail import generate_thumbnail


def _segments(story: dict) -> tuple[list[str], list[str]]:
    """Spoken segments and the image prompt that illustrates each.

    hook -> beats -> loop line, with the loop line back on the hook's image.
    """
    hook_img = story.get("hook_image_prompt") or story["beats"][0]["image_prompt"]
    texts = [story["hook"]]
    prompts = [hook_img]
    for b in story["beats"]:
        texts.append(b["narration"])
        prompts.append(b["image_prompt"])
    texts.append(story["loop_line"])
    prompts.append(hook_img)
    return texts, prompts


def _story_id(story: dict) -> str:
    return "story_" + str(story["topic_key"]).strip().lower().replace(" ", "-")[:60]


def make_story(cfg: dict, hashtag_boost: list[str] | None = None) -> str | None:
    """Produce one story Short and leave it pending in the review queue.

    Returns the clip id, or None if nothing could be produced.
    """
    sc = cfg.get("story") or {}

    print("  ✍  writing an original script…")
    story = write_story(cfg)
    if not story:
        print("    ⨯ could not write a script (GPT unavailable or all attempts rejected)")
        return None

    sid = _story_id(story)
    if state.seen(sid):
        print(f"    ⨯ '{story['topic_key']}' already in the database, skipping")
        return None

    print(f"    → {story['title']}  [{story.get('category', '?')}]")
    state.upsert(sid, streamer=sc.get("narrator", "The Archivist"),
                 title=story["title"], view_count=0, status="discovered",
                 category=story.get("category"))

    try:
        return _build(sid, story, cfg, sc, hashtag_boost)
    except Exception as e:
        # Without this the row stays "discovered", which state._is_done() counts as
        # a finished outcome — so seen() would block this topic forever even though
        # nothing was produced. record_failure marks it retryable instead, exactly
        # as the clip pipeline does via _safe_process.
        used = state.record_failure(sid, str(e), streamer=sc.get("narrator"),
                                    title=story["title"])
        left = max(0, state.MAX_ATTEMPTS - used)
        print(f"    x failed after {used} attempt(s)"
              + (f" — will retry next run ({left} left)" if left else " — giving up on it"))
        raise


def _build(sid: str, story: dict, cfg: dict, sc: dict,
           hashtag_boost: list[str] | None) -> str:
    """Narrate, illustrate, render and queue an already-written script."""
    r = cfg["render"]

    texts, prompts = _segments(story)
    work = CLIPS_DIR / sid
    work.mkdir(parents=True, exist_ok=True)

    # 1. Narration first — its real duration is what the beat timings are built on.
    print("  🎙  narrating…")
    audio = voice.narrate(story["narration"], work / "narration.mp3",
                          voice=sc.get("voice", "onyx"),
                          instructions=sc.get("voice_instructions"),
                          speed=float(sc.get("voice_speed", 1.0)))
    if not audio:
        raise RuntimeError("TTS produced no narration")
    total = probe_duration(audio)
    if total <= 0:
        raise RuntimeError("narration audio has no duration")
    print(f"    → {total:.1f}s of narration")

    # 2. Images — the expensive step, so it runs only after the script and voice are good.
    unique: list[str] = []
    slot_for: list[int] = []      # per segment, which unique prompt it uses
    for pr in prompts:
        if pr not in unique:
            unique.append(pr)
        slot_for.append(unique.index(pr))

    print(f"  🎨 generating {len(unique)} images…")
    raw_unique = imagery.generate_beat_images(
        unique, work / "images", model=sc.get("image_model"))
    filled = imagery.fill_gaps(raw_unique)
    if not filled:
        raise RuntimeError("no images could be generated")
    images = [filled[i] for i in slot_for]
    missing = sum(1 for i in raw_unique if i is None)
    if missing:
        print(f"    ({missing} image(s) failed — covered with neighbouring frames)")

    # 3. Ken Burns assembly against the real narration length.
    durations = apportion([len(t.split()) for t in texts], total)
    # A beat that speaks for six seconds would otherwise hold one still for six
    # seconds. Split it so the frame keeps moving without buying another image.
    images, durations = split_long_holds(images, durations)
    print(f"  🎬 rendering motion… ({len(durations)} shots, "
          f"longest hold {max(durations):.1f}s)")
    base = build_base(images, durations, audio, work / "base.mp4",
                      w=r["target_w"], h=r["target_h"])

    # 4. Captions. Whisper reads back our own narration, which gives word-level timing
    #    that matches the audio exactly — the script's text alone has no timestamps.
    specs = []
    if (cfg.get("captions") or {}).get("enabled", True):
        print("  💬 timing captions…")
        # Whisper supplies the timings; the script supplies the spelling. Without
        # this, recognition errors on the proper nouns the story is *about* get
        # burned into the video ("Tamam Shud" came back as "Tamam should").
        words = align_to_script(transcribe(base), story["narration"])
        if words:
            specs = render_caption_overlays(words, OUTPUT_DIR / f"{sid}_caps",
                                            res=(r["target_w"], r["target_h"]))

    out = OUTPUT_DIR / f"{sid}.mp4"
    render_vertical(base, out, specs, layout="center_crop",
                    w=r["target_w"], h=r["target_h"],
                    max_sec=int(sc.get("max_final_sec", r["max_final_sec"])))
    if specs:
        shutil.rmtree(OUTPUT_DIR / f"{sid}_caps", ignore_errors=True)

    meta = generate_story_metadata(story, cfg["publish"].get("tags_extra"),
                                   hashtag_boost=hashtag_boost)

    thumb = OUTPUT_DIR / f"{sid}_thumb.jpg"
    try:
        # Beat 0 is the hook image — the strongest frame in the script by construction.
        generate_thumbnail(out, thumb, sc.get("narrator", "The Archivist"),
                           meta.get("hook", ""), frame_at=0.5)
    except Exception as e:
        print(f"    (thumbnail failed: {e})")

    (OUTPUT_DIR / f"{sid}.json").write_text(json.dumps({
        "clip_id": sid, "kind": "story", "topic_key": story["topic_key"],
        "category": story.get("category"), "fact_risk": story.get("fact_risk"),
        "aside": story.get("aside"), "payoff": story.get("payoff"),
        "why_this_works": story.get("why_this_works"),
        "narration": story["narration"],
        "beats": len(story["beats"]), "narration_sec": total,
        **meta,
    }, indent=2))

    state.upsert(sid, status="pending", output_path=str(out), title=meta["title"],
                 length_sec=probe_duration(out), error=None)
    remember_topic(story["topic_key"], meta["title"], story.get("aside", ""))

    # The stills and narration are baked into the mp4 now; only the source art is
    # worth keeping around, and even that goes on the next cleanup sweep.
    shutil.rmtree(work / "images", ignore_errors=True)
    print(f'    ✓ rendered -> {out.name}  |  "{meta["title"]}"')
    return sid


def run(max_stories: int = 3, hashtag_boost: list[str] | None = None) -> int:
    """Generate up to `max_stories` Shorts. Returns how many succeeded."""
    from .config import load_config

    cfg = load_config()
    made = 0
    for i in range(1, max_stories + 1):
        print(f"\n[{i}/{max_stories}] new story")
        try:
            if make_story(cfg, hashtag_boost):
                made += 1
        except Exception as e:
            print(f"    x story failed: {str(e)[:200]}")
            traceback.print_exc()
    return made
