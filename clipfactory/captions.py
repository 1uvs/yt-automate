"""Word-level captions rendered as transparent PNG overlays (no libass needed).

Produces the 'viral' word-by-word highlight: the whole short phrase is on screen,
the currently-spoken word is highlighted. Each word gets one PNG, shown during its
time window; edit.py composites them with ffmpeg's overlay filter.

Transcription and overlay rendering are deliberately separate steps. The pipeline
needs the transcript early (to screen the clip before spending any render time),
but only needs the PNGs for clips that actually survive screening — so it calls
`transcribe()` first and `render_caption_overlays()` later, if at all.
"""
from __future__ import annotations

import difflib
import re

from pathlib import Path

from .config import env

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Black.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

WHITE = (255, 255, 255, 255)
HIGHLIGHT = (255, 222, 0, 255)   # punchy yellow
STROKE = (0, 0, 0, 255)

CAPTION_CENTER_Y = 0.60          # caption band sits ~lower third
FPS = 30.0                       # must match the render frame rate in edit.py

# Loading a Whisper model off disk takes several seconds; a run transcribes up to
# max_per_run clips, so keep the loaded model around instead of paying that each time.
_model_cache: dict[str, object] = {}


def _font(size: int):
    from PIL import ImageFont

    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _whisper(model_size: str):
    model = _model_cache.get(model_size)
    if model is None:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        _model_cache[model_size] = model
    return model


# A caption group never runs past one of these — a group that ends mid-clause reads
# as a mistake, and one that welds the tail of a sentence to the head of the next
# ("me. Witnesses reported") is actively confusing to skim.
_HARD_BREAK = (".", "!", "?", ";", ":")
_SOFT_BREAK = (",", "—", "–")
# Closing quotes/brackets sit outside the punctuation we care about.
_TRIM = "\"')”"


def _group_words(words, max_words=3, max_gap=0.6, max_dur=1.5):
    """Group words into caption cards, breaking on sense rather than on a counter.

    Grouping purely by count splits phrases wherever the third word happens to land,
    so "sleeping pills" ends up straddling two cards. Punctuation is the cheapest
    available signal for where a phrase actually ends, so a group always closes at a
    sentence end, and prefers to close at a comma rather than run to the word cap.
    """
    lines, cur = [], []

    def ends_with(word, marks):
        return word["text"].rstrip(_TRIM).endswith(marks)

    for w in words:
        if not cur:
            cur = [w]
            continue
        prev = cur[-1]
        gap = w["start"] - prev["end"]
        dur = w["end"] - cur[0]["start"]
        # Break after a sentence end, after a comma once the group has some weight,
        # or when the card is simply full / the speaker has paused.
        # At the word cap, allow one extra word if it closes the phrase — otherwise
        # a pair like "sleeping pills" gets split by the counter landing between them.
        full = len(cur) >= max_words
        if full and len(cur) < max_words + 1 and ends_with(w, _HARD_BREAK + _SOFT_BREAK) \
                and gap <= max_gap and (w["end"] - cur[0]["start"]) <= max_dur + 0.4:
            cur.append(w)
            continue
        if (ends_with(prev, _HARD_BREAK)
                or (ends_with(prev, _SOFT_BREAK) and len(cur) >= 2)
                or full or gap > max_gap or dur > max_dur):
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def _wrap(tokens, font, max_w, draw):
    """Greedy-wrap a phrase's tokens into lines that fit max_w. Returns list of lists."""
    lines, cur = [], []
    for tok in tokens:
        trial = cur + [tok]
        w = draw.textlength(" ".join(trial), font=font)
        if cur and w > max_w:
            lines.append(cur)
            cur = [tok]
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _render_word_png(tokens, active_idx, out_path, size, font, res, stroke_w) -> int:
    """Draw one caption frame and return the y offset it should be overlaid at.

    Only the caption band is rasterised, not the whole 1080x1920 frame. A phrase
    occupies a few hundred pixels of height, so a full-frame PNG spends ~85% of its
    pixels (and of ffmpeg's per-frame compositing work) on transparency. Rendering
    the band alone and telling edit.py where to place it is the same picture for a
    fraction of the cost — and there are ~150 of these per clip.
    """
    from PIL import Image, ImageDraw

    W, H = res
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = _wrap(tokens, font, int(W * 0.86), probe)

    line_h = int(size * 1.15)
    pad = int(size * 0.45)                      # room for stroke + descenders
    band_h = min(H, line_h * len(lines) + 2 * pad)
    y = int(H * CAPTION_CENTER_Y) - band_h // 2
    y = max(0, min(y, H - band_h))

    img = Image.new("RGBA", (W, band_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    ty, idx = pad, 0
    for line in lines:
        line_w = draw.textlength(" ".join(line), font=font)
        x = (W - line_w) // 2
        for tok in line:
            fill = HIGHLIGHT if idx == active_idx else WHITE
            draw.text((x, ty), tok, font=font, fill=fill,
                     stroke_width=stroke_w, stroke_fill=STROKE)
            x += draw.textlength(tok + " ", font=font)
            idx += 1
        ty += line_h
    img.save(out_path)
    return y


def _clean(words: list[dict]) -> list[dict]:
    """Collapse stutters and force monotonic, non-overlapping word windows.

    Whisper (especially with VAD) emits repeated words and out-of-order timestamps.
    Left alone those produce the two classic artefacts: duplicated ("double")
    captions and per-word flicker.
    """
    cleaned: list[dict] = []
    for w in words:
        if cleaned:
            prev = cleaned[-1]
            # An immediate repeat of the same word overlapping the previous one is a
            # hallucinated stutter — merge it into one longer word.
            if w["text"].lower() == prev["text"].lower() and w["start"] < prev["end"] + 0.05:
                prev["end"] = max(prev["end"], w["end"])
                continue
            if w["start"] < prev["end"]:
                w["start"] = prev["end"]
        if w["end"] <= w["start"]:
            w["end"] = w["start"] + 0.08
        cleaned.append(w)
    return cleaned


def transcribe(video_path: Path) -> list[dict]:
    """Transcribe to a cleaned word stream: [{start, end, text}, ...] (may be empty)."""
    # 'small' gives noticeably better word splits/timestamps than 'base' (fewer
    # mangled words like 'sw'/'switch'); override with WHISPER_MODEL if you want.
    model = _whisper(env("WHISPER_MODEL", "small"))
    segments, _ = model.transcribe(str(video_path), word_timestamps=True, vad_filter=True)

    words = []
    for seg in segments:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append({"start": float(w.start), "end": float(w.end), "text": t})
    return _clean(words)


def words_to_text(words: list[dict]) -> str:
    return " ".join(w["text"] for w in words)


def clip_words(words: list[dict], window: tuple[float, float] | None) -> list[dict]:
    """Restrict words to a (start, end) source window and rebase them to zero.

    Used when the render trims a long source down to its best stretch: the captions
    have to move with it, or every word lands late by the trim offset.
    """
    if not window:
        return words
    ws, we = window
    out = []
    for w in words:
        if w["end"] <= ws or w["start"] >= we:
            continue
        out.append({
            "start": max(0.0, w["start"] - ws),
            "end": min(we, w["end"]) - ws,
            "text": w["text"],
        })
    return out


def render_caption_overlays(words: list[dict], work_dir: Path,
                            res=(1080, 1920)) -> list[dict]:
    """Render one PNG per word. Returns specs = [{path, start, end, y}, ...]."""
    if not words:
        return []

    size = int(res[0] * 0.085)          # ~92px on 1080-wide
    stroke_w = max(6, size // 12)
    font = _font(size)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Flatten every word into one ordered list so we can guarantee that no two
    # caption images are ever on screen at the same time (fixes overlapping captions).
    flat = []
    for phrase in _group_words(words):
        tokens = [w["text"] for w in phrase]
        for i, w in enumerate(phrase):
            flat.append({"tokens": tokens, "active": i, "start": w["start"], "wend": w["end"]})
    # Whisper can emit word times slightly out of order; sort so windows stay monotonic.
    flat.sort(key=lambda x: x["start"])

    # Timing model: each word's overlay stays on screen continuously until the next
    # word takes over (no blackout frame between words -> no flicker). The caption
    # only clears during a genuine speech pause. edit.py uses half-open [start, end)
    # windows so no two overlays are ever live on the same frame.
    FRAME = 1.0 / FPS
    HOLD = 0.4                 # linger after the last word of a phrase (seconds)
    PAUSE_GAP = 0.5            # gap larger than this = clear the caption (a pause)

    # Quantize each word's start to a *strictly increasing* integer frame index.
    # Words spoken less than one frame apart get nudged a frame apart rather than
    # collapsing to a zero-length window — this is what guarantees windows never
    # overlap and never leave a blackout frame (no doubling, no flicker).
    start_frame: list[int] = []
    for item in flat:
        f = round(item["start"] / FRAME)
        if start_frame and f <= start_frame[-1]:
            f = start_frame[-1] + 1
        start_frame.append(f)

    specs = []
    for j, item in enumerate(flat):
        s = round(start_frame[j] * FRAME, 3)
        if j + 1 < len(flat):
            next_s = start_frame[j + 1]
            if flat[j + 1]["start"] - item["wend"] <= PAUSE_GAP:
                e_frame = next_s                                   # seamless hand-off
            else:
                e_frame = min(round((item["wend"] + HOLD) / FRAME), next_s)  # linger
        else:
            e_frame = round((item["wend"] + HOLD) / FRAME)
        e_frame = max(e_frame, start_frame[j] + 1)                 # always >= 1 frame
        e = round(e_frame * FRAME, 3)
        png = work_dir / f"cap_{j:04d}.png"
        y = _render_word_png(item["tokens"], item["active"], png, size, font, res, stroke_w)
        specs.append({"path": png, "start": s, "end": e, "y": y})
    return specs


def build_caption_overlays(video_path: Path, work_dir: Path, res=(1080, 1920)):
    """Transcribe and render in one call. Returns (specs, transcript).

    Kept for callers that just want captions and don't need the transcript early;
    the pipeline uses transcribe() + render_caption_overlays() separately.
    """
    words = transcribe(video_path)
    return render_caption_overlays(words, work_dir, res=res), words_to_text(words)


def align_to_script(words: list[dict], script: str) -> list[dict]:
    """Keep whisper's timings but restore the words we actually wrote.

    In story mode we are transcribing narration generated from a script we already
    have, so speech recognition can only lose information. It mishears proper nouns
    in exactly the places that matter most: "Tamam Shud" came back as "Tamam should",
    which burned the wrong spelling of the story's key phrase onto the screen.

    Whisper is still the only thing that knows *when* each word is spoken, so we keep
    its timings and map the real script text onto them. Matched runs take their text
    from the script; where the two disagree, the script's words for that stretch are
    spread evenly across the time whisper assigned to it.
    """
    script_words = script.split()
    if not words or not script_words:
        return words

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    heard = [norm(w["text"]) for w in words]
    real = [norm(w) for w in script_words]

    out: list[dict] = []
    sm = difflib.SequenceMatcher(None, heard, real, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out.append({**words[i1 + k], "text": script_words[j1 + k]})
            continue
        if j1 == j2:
            continue  # whisper invented words that aren't in the script — drop them

        # Time whisper attributed to this stretch. For a pure insertion (i1 == i2)
        # there is no span, so borrow a beat from the gap at that point.
        if i2 > i1:
            start, end = words[i1]["start"], words[i2 - 1]["end"]
        else:
            start = words[i1 - 1]["end"] if i1 > 0 else 0.0
            end = words[i1]["start"] if i1 < len(words) else start + 0.4
            if end <= start:
                end = start + 0.4

        n = j2 - j1
        step = (end - start) / n
        for k in range(n):
            out.append({
                "start": round(start + k * step, 3),
                "end": round(start + (k + 1) * step, 3),
                "text": script_words[j1 + k],
            })
    return _clean(out)
