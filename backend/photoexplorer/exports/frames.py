"""Image helpers shared by slideshows and collages."""
from __future__ import annotations

import sqlite3
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..scanner import extract


def load(path: str, kind: str, max_side: int) -> Image.Image:
    """Oriented RGB image (HEIC / RAW / JPEG / PNG...) no larger than max_side."""
    return extract.load_rgb(path, kind, max_side)


def face_focus(conn: sqlite3.Connection, media_id: int) -> tuple[float, float] | None:
    """Centre of the faces in a photo (0..1), weighted by face size, to crop
    around people instead of the middle of the picture."""
    rows = conn.execute("SELECT x, y, w, h FROM faces WHERE media_id = ?", (media_id,)).fetchall()
    if not rows:
        return None
    tw = sum(r[2] * r[3] for r in rows)
    if tw <= 0:
        return None
    fx = sum((r[0] + r[2] / 2) * r[2] * r[3] for r in rows) / tw
    fy = sum((r[1] + r[3] / 2) * r[2] * r[3] for r in rows) / tw
    return fx, fy


def cover(img: Image.Image, w: int, h: int, focus: tuple[float, float] | None = None) -> Image.Image:
    """Fill w x h exactly, cropping the excess around `focus` (default: centre)."""
    fx, fy = focus or (0.5, 0.5)
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    cw, ch = min(w / scale, iw), min(h / scale, ih)    # crop size in source pixels
    left = max(0.0, min(fx * iw - cw / 2, iw - cw))    # max(0, …) guards float rounding
    top = max(0.0, min(fy * ih - ch / 2, ih - ch))
    return img.resize((w, h), Image.Resampling.LANCZOS, box=(left, top, left + cw, top + ch))


def contain_on_blur(img: Image.Image, w: int, h: int) -> Image.Image:
    """Whole photo centred on a blurred, darkened copy of itself (no black bars)."""
    bg = cover(img, max(w // 8, 1), max(h // 8, 1)).filter(ImageFilter.GaussianBlur(3)).resize((w, h))
    bg = Image.blend(bg, Image.new("RGB", (w, h)), 0.35)
    iw, ih = img.size
    s = min(w / iw, h / ih)
    fg = img.resize((max(1, round(iw * s)), max(1, round(ih * s))), Image.Resampling.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


@lru_cache(maxsize=16)
def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)        # Pillow's built-in scalable font
    except TypeError:                                   # very old Pillow
        return ImageFont.load_default()


def caption(img: Image.Image, text: str, size_frac: float = 0.038, where: str = "bottom-left") -> Image.Image:
    """Text with a soft shadow, readable on any photo."""
    if not text:
        return img
    w, h = img.size
    f = font(max(14, int(h * size_frac)))
    d = ImageDraw.Draw(img)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    tw, th = r - l, b - t
    pad = int(h * 0.035)
    if where == "center":
        x, y = (w - tw) // 2, (h - th) // 2
    else:
        x, y = pad, h - pad - th
    for dx, dy in ((2, 2), (1, 1), (0, 2), (2, 0)):
        d.text((x + dx, y + dy - t), text, font=f, fill=(0, 0, 0))
    d.text((x, y - t), text, font=f, fill=(255, 255, 255))
    return img


def title_card(text: str, w: int, h: int, subtitle: str = "") -> Image.Image:
    img = Image.new("RGB", (w, h), (12, 12, 14))
    caption(img, text, 0.075, "center")
    if subtitle:
        d = ImageDraw.Draw(img)
        f = font(int(h * 0.035))
        l, t, r, b = d.textbbox((0, 0), subtitle, font=f)
        d.text(((w - (r - l)) // 2, int(h * 0.62) - t), subtitle, font=f, fill=(170, 170, 170))
    return img
