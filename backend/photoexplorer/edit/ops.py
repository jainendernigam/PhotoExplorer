"""Photo edits as a list of settings applied in a fixed order.

Every setting is size-independent (fractions and percentages), so the same
``ops`` give the same result on a small preview and on the full photo.

    rotate      0 | 90 | 180 | 270 (clockwise)     flip_h, flip_v   bool
    straighten  -45..45 degrees (crops to fill)     crop  {x, y, w, h} 0..1 or None
    auto        bool: levels and colour balance
    brightness, contrast, saturation, warmth, highlights, shadows   -100..100
    filter      one of looks.COLORS                sharpen, denoise  0..100
    redeye      bool (uses the face boxes)          resize  longest side in px, 0 = keep
    frame       none | white | black | print       frame_size  1..15 (% of the short side)
    text        {text, where: top|center|bottom, size: 2..20 (% of height), color, font}
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from ..exports import looks

DEFAULTS: dict = {
    "rotate": 0, "flip_h": False, "flip_v": False, "straighten": 0.0, "crop": None,
    "auto": False, "brightness": 0, "contrast": 0, "saturation": 0, "warmth": 0,
    "highlights": 0, "shadows": 0, "filter": "natural", "sharpen": 0, "denoise": 0,
    "redeye": False, "resize": 0, "frame": "none", "frame_size": 4, "text": None,
}
FRAMES = ("none", "white", "black", "print")
GEOMETRY = ("rotate", "flip_h", "flip_v", "straighten", "crop")


class EditError(ValueError):
    pass


def _num(v, lo: float, hi: float, name: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise EditError(f"{name} must be a number")
    if not math.isfinite(f) or f < lo or f > hi:
        raise EditError(f"{name} must be between {lo:g} and {hi:g}")
    return f


def normalize(raw: dict | None) -> dict:
    """Checked copy of ``raw`` with every key present."""
    raw = raw or {}
    bad = set(raw) - set(DEFAULTS)
    if bad:
        raise EditError("Unknown edit: " + ", ".join(sorted(bad)))
    o = {**DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}
    if int(o["rotate"]) not in (0, 90, 180, 270):
        raise EditError("rotate must be 0, 90, 180 or 270")
    o["rotate"] = int(o["rotate"])
    o["flip_h"], o["flip_v"], o["auto"], o["redeye"] = (bool(o[k]) for k in ("flip_h", "flip_v", "auto", "redeye"))
    o["straighten"] = _num(o["straighten"], -45, 45, "straighten")
    for k in ("brightness", "contrast", "saturation", "warmth", "highlights", "shadows"):
        o[k] = _num(o[k], -100, 100, k)
    for k in ("sharpen", "denoise"):
        o[k] = _num(o[k], 0, 100, k)
    o["resize"] = int(_num(o["resize"], 0, 20000, "resize"))
    if o["resize"] and o["resize"] < 64:
        raise EditError("resize must be at least 64 px")
    if o["filter"] not in looks.COLORS:
        raise EditError("filter must be one of " + ", ".join(looks.COLORS))
    if o["frame"] not in FRAMES:
        raise EditError("frame must be one of " + ", ".join(FRAMES))
    o["frame_size"] = _num(o["frame_size"], 1, 15, "frame_size")
    c = o["crop"]
    if c:
        try:
            x, y, w, h = (float(c[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise EditError("crop needs x, y, w and h")
        x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        w, h = min(w, 1.0 - x), min(h, 1.0 - y)
        if w < 0.01 or h < 0.01:
            raise EditError("The crop is too small")
        o["crop"] = None if (x, y, w, h) == (0.0, 0.0, 1.0, 1.0) else {"x": x, "y": y, "w": w, "h": h}
    t = o["text"]
    if t:
        if not isinstance(t, dict) or not str(t.get("text", "")).strip():
            o["text"] = None
        else:
            where = t.get("where", "bottom")
            if where not in ("top", "center", "bottom"):
                raise EditError("text position must be top, center or bottom")
            font = t.get("font", "sans")
            o["text"] = {"text": str(t["text"])[:200], "where": where,
                         "size": _num(t.get("size", 6), 2, 20, "text size"),
                         "color": str(t.get("color", "#ffffff"))[:9],
                         "font": font if font in looks.FONTS else "sans"}
    return o


def changes_geometry(o: dict) -> bool:
    """True when face boxes no longer fit the result (rotate, crop, frame...)."""
    return bool(o["rotate"] or o["flip_h"] or o["flip_v"] or o["straighten"] or o["crop"] or o["frame"] != "none")


def is_empty(o: dict) -> bool:
    return all(o[k] == v for k, v in DEFAULTS.items())


# ---------------------------------------------------------------- apply

def apply(img: Image.Image, o: dict, faces: list[dict] | None = None) -> Image.Image:
    """``o`` from normalize(); ``faces`` = boxes (x, y, w, h 0..1) of the photo as it is."""
    img = img.convert("RGB")
    if o["redeye"] and faces:
        img = _redeye(img, faces)          # before geometry: the boxes fit the photo as it is

    if o["rotate"]:
        img = img.rotate(-o["rotate"], expand=True)
    if o["flip_h"]:
        img = ImageOps.mirror(img)
    if o["flip_v"]:
        img = ImageOps.flip(img)
    if o["straighten"]:
        img = _straighten(img, o["straighten"])
    if o["crop"]:
        c, (w, h) = o["crop"], img.size
        box = (round(c["x"] * w), round(c["y"] * h), round((c["x"] + c["w"]) * w), round((c["y"] + c["h"]) * h))
        img = img.crop(box)

    if o["auto"]:
        img = ImageOps.autocontrast(img, cutoff=0.5)
    if o["brightness"]:
        img = ImageEnhance.Brightness(img).enhance(1 + o["brightness"] / 100 * 0.6)
    if o["contrast"]:
        img = ImageEnhance.Contrast(img).enhance(1 + o["contrast"] / 100 * 0.8)
    if o["saturation"]:
        img = ImageEnhance.Color(img).enhance(1 + o["saturation"] / 100)
    if o["warmth"] or o["highlights"] or o["shadows"]:
        img = _tone(img, o["warmth"], o["highlights"], o["shadows"])
    img = looks.color(img, o["filter"])

    if o["denoise"]:
        img = _denoise(img, o["denoise"])
    if o["sharpen"]:
        radius = max(1.0, max(img.size) / 1500)
        img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(o["sharpen"] * 2.5), threshold=2))
    if o["resize"] and max(img.size) > o["resize"]:
        img.thumbnail((o["resize"], o["resize"]), Image.Resampling.LANCZOS)
    if o["frame"] != "none":
        img = _frame(img, o["frame"], o["frame_size"])
    if o["text"]:
        img = _text(img, o["text"])
    return img


def _straighten(img: Image.Image, angle: float) -> Image.Image:
    """Rotate by ``angle`` (positive = clockwise) and crop away the empty corners."""
    w, h = img.size
    a = math.radians(abs(angle))
    scale = 1 / (math.cos(a) + math.sin(a) * max(w / h, h / w))
    r = img.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=False)
    cw, ch = w * scale, h * scale
    return r.crop((round((w - cw) / 2), round((h - ch) / 2), round((w + cw) / 2), round((h + ch) / 2)))


def _tone(img: Image.Image, warmth: float, highlights: float, shadows: float) -> Image.Image:
    a = np.asarray(img, dtype=np.float32) / 255.0
    if warmth:
        k = warmth / 100 * 0.15
        a[..., 0] *= 1 + k
        a[..., 2] *= 1 - k
    if highlights or shadows:
        lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        if shadows:
            m = np.clip(1 - lum / 0.5, 0, 1) ** 2
            a += (shadows / 100 * 0.35 * m)[..., None]
        if highlights:
            m = np.clip((lum - 0.5) / 0.5, 0, 1) ** 2
            a += (highlights / 100 * 0.35 * m)[..., None]
    return Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))


def _denoise(img: Image.Image, amount: float) -> Image.Image:
    try:
        import cv2
    except ImportError:
        return img.filter(ImageFilter.MedianFilter(3))
    a = np.asarray(img)[..., ::-1].copy()
    h = 3 + amount / 100 * 12
    out = cv2.fastNlMeansDenoisingColored(a, None, h, h, 7, 21)
    return Image.fromarray(out[..., ::-1].copy())


def _redeye(img: Image.Image, faces: list[dict]) -> Image.Image:
    a = np.asarray(img).astype(np.int16).copy()
    H, W = a.shape[:2]
    for f in faces:
        fx, fy, fw, fh = f["x"] * W, f["y"] * H, f["w"] * W, f["h"] * H
        for ex in (0.15, 0.55):            # left and right eye areas of the face box
            x0, x1 = int(fx + ex * fw), int(fx + (ex + 0.3) * fw)
            y0, y1 = int(fy + 0.2 * fh), int(fy + 0.5 * fh)
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
            if x1 <= x0 or y1 <= y0:
                continue
            p = a[y0:y1, x0:x1]
            r, g, b = p[..., 0], p[..., 1], p[..., 2]
            red = (r > 80) & (r > 1.6 * np.maximum(g, b))
            p[..., 0] = np.where(red, (g + b) // 2, r)
    return Image.fromarray(a.astype(np.uint8))


def _frame(img: Image.Image, frame: str, size: float) -> Image.Image:
    w, h = img.size
    b = max(2, round(min(w, h) * size / 100))
    bottom = b * 4 if frame == "print" else b
    fill = (12, 12, 12) if frame == "black" else (250, 248, 243) if frame == "print" else (255, 255, 255)
    out = Image.new("RGB", (w + 2 * b, h + b + bottom), fill)
    out.paste(img, (b, b))
    return out


def _text(img: Image.Image, t: dict) -> Image.Image:
    w, h = img.size
    f = looks.font(t["font"], max(12, int(h * t["size"] / 100)))
    d = ImageDraw.Draw(img)
    l, top, r, btm = d.textbbox((0, 0), t["text"], font=f)
    tw, th = r - l, btm - top
    pad = int(h * 0.04)
    x = (w - tw) // 2
    y = pad if t["where"] == "top" else (h - th) // 2 if t["where"] == "center" else h - pad - th
    shadow = max(1, int(th * 0.06))
    d.text((x + shadow - l, y + shadow - top), t["text"], font=f, fill=(0, 0, 0))
    d.text((x - l, y - top), t["text"], font=f, fill=looks._hex(t["color"]))
    return img
