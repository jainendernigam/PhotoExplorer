"""Collages with Pillow.

Layouts
* mosaic   - justified rows: photos keep (almost) their shape, rows fill the width.
* grid     - equal cells.
* featured - the first photo large on the left, the rest in a grid beside it.
* polaroid, magazine, story, bubbles, heart, filmstrip, year, then_now - drawn
  styles, see collage_styles.py.

Photos are cropped around the faces in them (from the face index), so people
are not cut in half.
"""
from __future__ import annotations

import dataclasses
import math
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageColor, ImageDraw

from . import collage_styles, frames

PRESETS = {
    "landscape_4k": (3840, 2160),
    "square": (3000, 3000),
    "a4_portrait": (2480, 3508),     # 300 dpi
    "a4_landscape": (3508, 2480),
    "phone": (1440, 2560),
}


@dataclass
class CollageOptions:
    layout: str = "mosaic"          # mosaic | grid | featured | a collage_styles.STYLES name
    size: str = "landscape_4k"      # a PRESETS key
    spacing: int = 12
    background: str = "#ffffff"
    title: str | None = None
    max_photos: int = 40
    skip_duplicates: bool = True


class Cancelled(Exception):
    pass


Rect = tuple[int, int, int, int]   # x, y, w, h


def render(conn: sqlite3.Connection, items: list[dict], o: CollageOptions, out_path,
           progress: Callable[[float, str], None], cancel: threading.Event, scale_to: int | None = None) -> None:
    """scale_to: draw a small preview whose longer side is this many pixels."""
    if not items:
        raise RuntimeError("No photos for the collage")
    W, H = PRESETS.get(o.size, PRESETS["landscape_4k"])
    if scale_to:
        k = scale_to / max(W, H)
        W, H = max(1, round(W * k)), max(1, round(H * k))
        o = dataclasses.replace(o, spacing=round(o.spacing * k))
    s = max(0, int(o.spacing))
    bg = ImageColor.getrgb(o.background or "#ffffff")
    canvas = Image.new("RGB", (W, H), bg)

    top = 0
    if o.title and o.layout not in collage_styles.OWN_TITLE:
        band = int(H * 0.09)
        d = ImageDraw.Draw(canvas)
        f = frames.font(int(band * 0.5))
        l, t, r, b = d.textbbox((0, 0), o.title, font=f)
        dark = sum(bg) < 384
        d.text(((W - (r - l)) // 2, (band - (b - t)) // 2 - t + s // 2), o.title, font=f,
               fill=(240, 240, 240) if dark else (30, 30, 30))
        top = band

    area = (s, top + s, W - 2 * s, H - top - 2 * s)
    if o.layout in collage_styles.STYLES:
        try:
            collage_styles.render(conn, items, o, canvas, area, progress, cancel)
        except collage_styles.Cancelled:
            raise Cancelled()
        canvas.save(out_path, "JPEG", quality=92, subsampling=0)
        return
    aspects = [((it["width"] or 4) / (it["height"] or 3)) for it in items]
    if o.layout == "grid":
        rects = _grid(len(items), area, s)
    elif o.layout == "featured" and len(items) >= 3:
        rects = _featured(len(items), area, s)
    else:
        rects = _mosaic(aspects, area, s)

    for i, (it, (x, y, w, h)) in enumerate(zip(items, rects)):
        if cancel.is_set():
            raise Cancelled()
        if w < 2 or h < 2:
            continue
        try:
            img = frames.load(it["path"], it["kind"], max(w, h) * 2)
        except Exception:
            continue
        canvas.paste(frames.cover(img, w, h, frames.face_focus(conn, it["id"])), (x, y))
        progress((i + 1) / len(items), f"Placing photo {i + 1} of {len(items)}")
    canvas.save(out_path, "JPEG", quality=92, subsampling=0)


def _grid(n: int, area: tuple[int, int, int, int], s: int) -> list[Rect]:
    ax, ay, aw, ah = area
    cols = max(1, round(math.sqrt(n * aw / ah)))
    rows = math.ceil(n / cols)
    cw = (aw - (cols - 1) * s) / cols
    ch = (ah - (rows - 1) * s) / rows
    rects = []
    for i in range(n):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        offset = (cols - in_row) * (cw + s) / 2       # centre a short last row
        rects.append((int(ax + offset + c * (cw + s)), int(ay + r * (ch + s)), int(cw), int(ch)))
    return rects


def _featured(n: int, area: tuple[int, int, int, int], s: int) -> list[Rect]:
    ax, ay, aw, ah = area
    big_w = int(aw * 0.58)
    return [(ax, ay, big_w, ah)] + _grid(n - 1, (ax + big_w + s, ay, aw - big_w - s, ah), s)


def _mosaic(aspects: list[float], area: tuple[int, int, int, int], s: int) -> list[Rect]:
    """Justified rows; tries every row count and keeps the one whose natural
    height is closest to the space, then stretches rows to fill it exactly."""
    ax, ay, aw, ah = area
    n = len(aspects)
    best = None
    for rows in range(1, min(n, 14) + 1):
        split = _split(aspects, rows)
        heights = [(aw - (len(r) - 1) * s) / sum(aspects[i] for i in r) for r in split]
        natural = sum(heights) + (len(split) - 1) * s
        score = abs(math.log(natural / ah))
        if best is None or score < best[0]:
            best = (score, split, heights)
    _, split, heights = best
    k = (ah - (len(split) - 1) * s) / sum(heights)
    rects: list[Rect] = [None] * n  # type: ignore
    y = ay
    for r, h in zip(split, heights):
        h = h * k
        x = ax
        total_a = sum(aspects[i] for i in r)
        avail = aw - (len(r) - 1) * s
        for j, i in enumerate(r):
            w = avail - (x - ax - j * s) if j == len(r) - 1 else avail * aspects[i] / total_a
            rects[i] = (int(round(x)), int(round(y)), int(round(w)), int(round(h)))
            x += w + s
        y += h + s
    return rects


def _split(aspects: list[float], rows: int) -> list[list[int]]:
    """Consecutive photos into `rows` rows with similar total width."""
    target = sum(aspects) / rows
    out, cur, acc = [], [], 0.0
    for i, a in enumerate(aspects):
        remaining_rows = rows - len(out)
        remaining_items = len(aspects) - i
        if cur and (acc + a / 2 > target and remaining_rows > 1) or (cur and remaining_items < remaining_rows):
            out.append(cur)
            cur, acc = [], 0.0
        cur.append(i)
        acc += a
    out.append(cur)
    return out
