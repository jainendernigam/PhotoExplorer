"""Slideshow "looks" used by themes: colour, grain, vignette, photo frame,
letterbox and caption fonts. They change only the frames that go into the
video; the photos themselves are never touched.
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import frames

COLORS = ("natural", "warm", "cool", "vivid", "faded", "sepia", "mono", "cinematic")
FRAMES = ("blur", "black", "white", "print")      # print = white border with a deeper bottom edge
FONTS = ("sans", "serif", "script", "bold", "hand")

# Windows fonts first, then common Linux/macOS ones; Pillow's own font if none is found.
_FONT_FILES = {
    "sans": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"],
    "serif": ["georgia.ttf", "times.ttf", "DejaVuSerif.ttf", "Times.ttc"],
    "script": ["segoesc.ttf", "Inkfree.ttf", "DejaVuSerif-Italic.ttf", "Apple Chancery.ttf"],
    "bold": ["seguibl.ttf", "ariblk.ttf", "DejaVuSans-Bold.ttf", "Arial Black.ttf"],
    "hand": ["Inkfree.ttf", "segoepr.ttf", "comic.ttf", "DejaVuSans.ttf", "Chalkboard.ttc"],
}
_FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
              os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts"),
              "/usr/share/fonts/truetype/dejavu", "/System/Library/Fonts", "/Library/Fonts"]


@lru_cache(maxsize=64)
def font(style: str, size: int) -> ImageFont.ImageFont:
    for name in _FONT_FILES.get(style, _FONT_FILES["sans"]):
        for d in _FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, size)
                except OSError:
                    continue
    return frames.font(size)


def _hex(c: str, default=(255, 255, 255)) -> tuple[int, int, int]:
    try:
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except (ValueError, AttributeError, IndexError):
        return default


def color(img: Image.Image, name: str) -> Image.Image:
    if name in ("", "natural"):
        return img
    if name == "mono":
        return ImageOps.grayscale(ImageEnhance.Contrast(img).enhance(1.1)).convert("RGB")
    if name == "sepia":
        g = ImageOps.grayscale(img)
        return ImageOps.colorize(g, black=(38, 24, 12), white=(255, 240, 205), mid=(160, 120, 80))
    if name == "vivid":
        return ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.35)).enhance(1.08)
    a = np.asarray(img, dtype=np.float32)
    if name == "warm":
        a = a * np.array([1.07, 1.0, 0.88])
    elif name == "cool":
        a = a * np.array([0.92, 1.0, 1.08])
    elif name == "faded":
        a = 30 + a * 0.82                                      # lifted blacks, softer whites
        a = a * 0.85 + a.mean(axis=2, keepdims=True) * 0.15    # a little less colour
    elif name == "cinematic":
        lum = a.mean(axis=2, keepdims=True) / 255
        a = a + (np.array([-10, 4, 14]) * (1 - lum)) + (np.array([14, 6, -10]) * lum)   # teal shadows, warm lights
        a = (a - 128) * 1.12 + 120
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def grain(img: Image.Image, amount: float, seed: int) -> Image.Image:
    if amount <= 0:
        return img
    rng = np.random.default_rng(seed)
    a = np.asarray(img, dtype=np.float32)
    noise = rng.normal(0, 18 * amount, a.shape[:2])[..., None]
    return Image.fromarray(np.clip(a + noise, 0, 255).astype(np.uint8))


@lru_cache(maxsize=4)
def _vignette_mask(w: int, h: int, amount: float) -> np.ndarray:
    y, x = np.ogrid[-1:1:complex(0, h), -1:1:complex(0, w)]
    r = np.sqrt(x * x + y * y) / np.sqrt(2)
    return (1 - amount * np.clip((r - 0.35) / 0.65, 0, 1) ** 1.6)[..., None].astype(np.float32)


def vignette(img: Image.Image, amount: float) -> Image.Image:
    if amount <= 0:
        return img
    a = np.asarray(img, dtype=np.float32) * _vignette_mask(img.width, img.height, round(amount, 2))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def place(img: Image.Image, w: int, h: int, frame: str, background: str = "#000000",
          text: str = "", style: str = "sans") -> tuple[Image.Image, bool]:
    """The whole photo on a w x h frame: blurred copy behind it, plain colour, or a
    white print border. Returns (frame, True if `text` was written on the print's
    bottom border, like ink on a photo print)."""
    if frame == "blur":
        return frames.contain_on_blur(img, w, h), False
    if frame in ("white", "print"):
        bg = Image.new("RGB", (w, h), _hex(background, (24, 24, 26)))
        border = int(min(w, h) * 0.025)
        bottom = int(border * (4 if frame == "print" else 3.2))
        s = min((w * 0.86 - 2 * border) / img.width, (h * 0.86 - border - bottom) / img.height)
        fg = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.Resampling.LANCZOS)
        card = Image.new("RGB", (fg.width + 2 * border, fg.height + border + bottom), (250, 248, 242))
        card.paste(fg, (border, border))
        if text:
            d = ImageDraw.Draw(card)
            size = int(bottom * 0.55)
            f = font(style, max(12, size))
            while size > 10 and d.textlength(text, font=f) > card.width - 2 * border:
                size -= 2
                f = font(style, size)
            l, t, r, b = d.textbbox((0, 0), text, font=f)
            d.text(((card.width - (r - l)) // 2 - l, border + fg.height + (bottom - (b - t)) // 2 - t),
                   text, font=f, fill=(62, 56, 50))
        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        cx, cy = (w - card.width) // 2, (h - card.height) // 2
        ImageDraw.Draw(shadow).rectangle((cx + 6, cy + 10, cx + card.width + 6, cy + card.height + 10), fill=(0, 0, 0, 120))
        soft = shadow.filter(ImageFilter.GaussianBlur(12))
        bg.paste(soft, (0, 0), soft)
        bg.paste(card, (cx, cy))
        return bg, bool(text)
    bg = Image.new("RGB", (w, h), _hex(background, (0, 0, 0)))
    s = min(w / img.width, h / img.height)
    fg = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.Resampling.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg, False


def letterbox(img: Image.Image, on: bool) -> Image.Image:
    """Black cinema bars (2.35:1 picture area) on a landscape frame."""
    if not on or img.width <= img.height:
        return img
    bar = max(0, (img.height - round(img.width / 2.35)) // 2)
    if bar:
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, img.width, bar), fill=(0, 0, 0))
        d.rectangle((0, img.height - bar, img.width, img.height), fill=(0, 0, 0))
    return img


def caption(img: Image.Image, text: str, style: str = "sans", fill: str = "#ffffff",
            where: str = "bottom-left", size_frac: float = 0.042, bottom_frac: float = 0.0,
            margin: float = 0.0) -> Image.Image:
    """Caption with a soft shadow. `bottom_frac` keeps it clear of bars; `margin`
    (fraction of the frame) keeps it inside what a slow zoom leaves visible."""
    if not text:
        return img
    w, h = img.size
    f = font(style, max(14, int(h * size_frac)))
    d = ImageDraw.Draw(img)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    tw, th = r - l, b - t
    pad = int(h * 0.035)
    y = h - pad - th - int(h * (bottom_frac + margin))
    x = (w - tw) // 2 if where == "bottom-center" else pad + int(w * margin)
    if where == "center":
        x, y = (w - tw) // 2, (h - th) // 2
    for dx, dy in ((2, 2), (1, 1), (0, 2), (2, 0)):
        d.text((x + dx - l, y + dy - t), text, font=f, fill=(0, 0, 0))
    d.text((x - l, y - t), text, font=f, fill=_hex(fill))
    return img


def title_card(text: str, w: int, h: int, subtitle: str, style: str, fg: str, bg: str) -> Image.Image:
    img = Image.new("RGB", (w, h), _hex(bg, (12, 12, 14)))
    d = ImageDraw.Draw(img)
    f = font(style, int(h * 0.08))
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    d.text(((w - (r - l)) // 2 - l, int(h * 0.42) - (b - t) // 2 - t), text, font=f, fill=_hex(fg))
    if subtitle:
        f2 = font(style, int(h * 0.036))
        l, t, r, b = d.textbbox((0, 0), subtitle, font=f2)
        d.text(((w - (r - l)) // 2 - l, int(h * 0.58) - t), subtitle, font=f2, fill=_hex(fg, (200, 200, 200)))
    return img
