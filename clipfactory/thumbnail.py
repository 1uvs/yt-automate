"""Auto-generate a punchy 16:9 YouTube thumbnail from a rendered clip."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .captions import HIGHLIGHT, STROKE, WHITE, _font


def extract_frame(video_path: Path, out_png: Path, at: float | None = None) -> Path:
    if at is not None:
        cmd = ["ffmpeg", "-y", "-ss", str(at), "-i", str(video_path),
               "-frames:v", "1", str(out_png), "-loglevel", "error"]
    else:  # let ffmpeg pick a representative (high-contrast) frame
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vf", "thumbnail=n=100",
               "-frames:v", "1", str(out_png), "-loglevel", "error"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_png.exists():
        # fallback: just grab the first frame
        subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1",
                        str(out_png), "-loglevel", "error"], check=True)
    return out_png


def _cover(img, w: int, h: int):
    from PIL import Image

    ratio = max(w / img.width, h / img.height)
    resized = img.resize((int(img.width * ratio) + 1, int(img.height * ratio) + 1))
    left = (resized.width - w) // 2
    top = (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h)).resize((w, h))


def _draw_centered(draw, text, font, cx, y, stroke_w, fill=WHITE):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill,
              stroke_width=stroke_w, stroke_fill=STROKE)


def _wrap(text, font, max_w, draw):
    words, lines, cur = text.split(), [], []
    for word in words:
        if cur and draw.textlength(" ".join(cur + [word]), font=font) > max_w:
            lines.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        lines.append(" ".join(cur))
    return lines[:3]


def generate_thumbnail(
    video_path: Path,
    out_jpg: Path,
    streamer: str,
    hook: str,
    frame_at: float | None = None,
    size=(1280, 720),
) -> Path:
    from PIL import Image, ImageDraw, ImageFilter

    W, H = size
    tmp = out_jpg.with_suffix(".frame.png")
    extract_frame(video_path, tmp, at=frame_at)
    frame = Image.open(tmp).convert("RGB")

    # blurred, darkened cover as the background
    bg = _cover(frame, W, H).filter(ImageFilter.GaussianBlur(22))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.38)

    # the actual (vertical) frame, full height, centered
    fw = int(frame.width * (H / frame.height))
    bg.paste(frame.resize((fw, H)), ((W - fw) // 2, 0))

    draw = ImageDraw.Draw(bg)
    cx = W // 2

    # hook text, big, top
    hook = (hook or streamer).upper()
    hfont = _font(int(W * 0.11))
    lines = _wrap(hook, hfont, int(W * 0.94), draw)
    line_h = int(hfont.size * 1.05)
    y = 28
    for ln in lines:
        _draw_centered(draw, ln, hfont, cx, y, max(6, hfont.size // 10), fill=HIGHLIGHT)
        y += line_h

    # streamer badge, bottom
    sfont = _font(int(W * 0.055))
    _draw_centered(draw, f"@{streamer.upper()}", sfont, cx, H - int(sfont.size * 1.6),
                   max(5, sfont.size // 10), fill=WHITE)

    bg.save(out_jpg, quality=88)
    tmp.unlink(missing_ok=True)
    return out_jpg
