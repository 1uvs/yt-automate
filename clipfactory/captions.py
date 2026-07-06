"""Word-level captions rendered as transparent PNG overlays (no libass needed).

Produces the 'viral' word-by-word highlight: the whole short phrase is on screen,
the currently-spoken word is highlighted. Each word gets one PNG, shown during its
time window; edit.py composites them with ffmpeg's overlay filter.
"""
from __future__ import annotations

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


def _font(size: int):
    from PIL import ImageFont

    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _group_words(words, max_words=3, max_gap=0.6, max_dur=1.5):
    lines, cur = [], []
    for w in words:
        if not cur:
            cur = [w]
            continue
        gap = w["start"] - cur[-1]["end"]
        dur = w["end"] - cur[0]["start"]
        if len(cur) >= max_words or gap > max_gap or dur > max_dur:
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


def _render_word_png(tokens, active_idx, out_path, size, font, res, stroke_w):
    from PIL import Image, ImageDraw

    W, H = res
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    max_w = int(W * 0.86)
    lines = _wrap(tokens, font, max_w, draw)

    line_h = int(size * 1.15)
    total_h = line_h * len(lines)
    y = int(H * 0.60) - total_h // 2   # caption band ~ lower third

    idx = 0
    for line in lines:
        line_w = draw.textlength(" ".join(line), font=font)
        x = (W - line_w) // 2
        for tok in line:
            fill = HIGHLIGHT if idx == active_idx else WHITE
            draw.text((x, y), tok, font=font, fill=fill,
                     stroke_width=stroke_w, stroke_fill=STROKE)
            x += draw.textlength(tok + " ", font=font)
            idx += 1
        y += line_h
    img.save(out_path)


def build_caption_overlays(video_path: Path, work_dir: Path, res=(1080, 1920)):
    """Transcribe and render one PNG per word.

    Returns (specs, transcript) where specs = [{path, start, end}, ...] (may be empty).
    """
    from faster_whisper import WhisperModel

    # 'small' gives noticeably better word splits/timestamps than 'base' (fewer
    # mangled words like 'sw'/'switch'); override with WHISPER_MODEL if you want.
    model_size = env("WHISPER_MODEL", "small")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(video_path), word_timestamps=True, vad_filter=True)

    words = []
    for seg in segments:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append({"start": float(w.start), "end": float(w.end), "text": t})
    if not words:
        return [], ""

    # Clean the raw word stream: Whisper (esp. with VAD) can emit stutters and
    # out-of-order / overlapping timestamps. Left unchecked these cause the two
    # symptoms we're fixing — duplicate ("double") captions and per-word flicker.
    cleaned = []
    for w in words:
        if cleaned:
            prev = cleaned[-1]
            # Collapse an immediate repeat of the same word that overlaps the
            # previous one (a hallucinated stutter) into one longer word.
            if w["text"].lower() == prev["text"].lower() and w["start"] < prev["end"] + 0.05:
                prev["end"] = max(prev["end"], w["end"])
                continue
            # Force starts to be monotonic so windows never overlap.
            if w["start"] < prev["end"]:
                w["start"] = prev["end"]
        if w["end"] <= w["start"]:
            w["end"] = w["start"] + 0.08
        cleaned.append(w)
    words = cleaned

    transcript = " ".join(w["text"] for w in words)
    size = int(res[0] * 0.085)          # ~92px on 1080-wide
    stroke_w = max(6, size // 12)
    font = _font(size)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Flatten every word into one ordered list so we can guarantee that no two
    # caption images are ever on screen at the same time (fixes overlapping captions).
    phrases = _group_words(words)
    flat = []
    for phrase in phrases:
        tokens = [w["text"] for w in phrase]
        for i, w in enumerate(phrase):
            flat.append({"tokens": tokens, "active": i, "start": w["start"], "wend": w["end"]})
    # Whisper can emit word times slightly out of order; sort so windows stay monotonic.
    flat.sort(key=lambda x: x["start"])

    # Timing model: each word's overlay stays on screen continuously until the
    # next word takes over (no blackout frame between words -> no flicker). The
    # caption only clears during a genuine speech pause. edit.py uses half-open
    # [start, end) windows so no two overlays are ever live on the same frame.
    FPS = 30.0                 # must match the render frame rate in edit.py
    FRAME = 1.0 / FPS
    HOLD = 0.4                 # linger after the last word of a phrase (seconds)
    PAUSE_GAP = 0.5            # gap larger than this = clear the caption (a pause)

    # Quantize each word's start to a *strictly increasing* integer frame index.
    # Words spoken less than one frame apart get nudged a frame apart rather than
    # collapsing to a zero-length window — this is what guarantees windows never
    # overlap and never leave a blackout frame (no doubling, no flicker).
    start_frame = []
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
        _render_word_png(item["tokens"], item["active"], png, size, font, res, stroke_w)
        specs.append({"path": png, "start": s, "end": e})
    return specs, transcript
