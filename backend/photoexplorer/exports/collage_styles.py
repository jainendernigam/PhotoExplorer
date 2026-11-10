"""Decorative collage styles (drawn by hand rather than tiled).

* polaroid  - tilted instant prints scattered on the background, date under each.
* magazine  - one large photo, a big title with the dates, smaller photos beside it.
* story     - tall phone-shaped panels, one per day, photos stacked in each.
* bubbles   - round photos of different sizes packed together.
* heart     - small square tiles filling a heart shape.
* filmstrip - photos in film frames with sprocket holes, 1 to 3 strips.
* year      - one photo for every month of the year.
* then_now  - the oldest and the newest photo side by side with their years.

Every style gets the same items as the tiled layouts (oldest first) and crops
around faces, so people are not cut.
"""
from __future__ import annotations

import math
import random
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Callable

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageStat

from . import frames, looks

STYLES = ("polaroid", "magazine", "story", "bubbles", "heart", "filmstrip", "year", "then_now")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
FULL_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


class Cancelled(Exception):
    pass


def _dt(it: dict) -> datetime | None:
    return datetime.fromtimestamp(it["taken_at"], tz=timezone.utc) if it.get("taken_at") else None


def _month_year(it: dict) -> str:
    t = _dt(it)
    return f"{MONTHS[t.month - 1]} {t.year}" if t else ""


def _span(items: list[dict]) -> str:
    ds = [d for d in (_dt(i) for i in items) if d]
    if not ds:
        return ""
    a, b = min(ds), max(ds)
    if (a.year, a.month) == (b.year, b.month):
        return f"{FULL_MONTHS[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{FULL_MONTHS[a.month - 1]} to {FULL_MONTHS[b.month - 1]} {a.year}"
    return f"{a.year} to {b.year}"


def _dark(bg: tuple[int, int, int]) -> bool:
    return sum(bg) < 384


def _ink(bg) -> tuple[int, int, int]:
    return (240, 240, 240) if _dark(bg) else (34, 34, 34)


def _fit_text(d: ImageDraw.ImageDraw, text: str, style: str, size: int, max_w: int):
    f = looks.font(style, max(10, size))
    while size > 10 and d.textlength(text, font=f) > max_w:
        size = int(size * 0.92)
        f = looks.font(style, size)
    return f


def _text(d, xy, text, f, fill, anchor="la"):
    d.text(xy, text, font=f, fill=fill, anchor=anchor)


class _Photos:
    """Loads and crops photos, reporting progress and honouring cancel."""

    def __init__(self, conn, items, progress, cancel):
        self.conn, self.items, self.progress, self.cancel = conn, items, progress, cancel
        self.done = 0

    def get(self, it: dict, w: int, h: int) -> Image.Image | None:
        if self.cancel.is_set():
            raise Cancelled()
        w, h = max(2, int(w)), max(2, int(h))
        try:
            img = frames.load(it["path"], it["kind"], max(w, h) * 2)
        except Exception:
            return None
        self.done += 1
        self.progress(min(1.0, self.done / max(1, len(self.items))), f"Placing photo {self.done}")
        return frames.cover(img, w, h, frames.face_focus(self.conn, it["id"]))


def _shadow(canvas: Image.Image, box, radius: int, alpha: int = 110, blur: int = 14, shape: str = "rect") -> None:
    x0, y0, x1, y1 = [int(v) for v in box]
    pad = blur * 3
    layer = Image.new("L", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), 0)
    ld = ImageDraw.Draw(layer)
    if shape == "circle":
        ld.ellipse((pad, pad, pad + x1 - x0, pad + y1 - y0), fill=alpha)
    else:
        ld.rounded_rectangle((pad, pad, pad + x1 - x0, pad + y1 - y0), radius, fill=alpha)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    canvas.paste((0, 0, 0), (x0 - pad + blur // 2, y0 - pad + blur), layer)


# ---------------------------------------------------------------- styles


def polaroid(ph: _Photos, items, canvas, area, o) -> None:
    ax, ay, aw, ah = area
    n = len(items)
    cols = max(1, round(math.sqrt(n * aw / ah * 0.85)))
    rows = math.ceil(n / cols)
    cw, ch = aw / cols, ah / rows
    card_w = min(cw * 1.08, ch * 1.08 / 1.2)
    card_h = card_w * 1.2
    rnd = random.Random(n * 7919 + len(o.title or ""))
    order = list(range(n))
    for k in order:
        it = items[k]
        r, c = divmod(k, cols)
        in_row = min(cols, n - r * cols)
        off = (cols - in_row) * cw / 2
        cx = ax + off + c * cw + cw / 2 + rnd.uniform(-0.12, 0.12) * cw
        cy = ay + r * ch + ch / 2 + rnd.uniform(-0.1, 0.1) * ch
        border = int(card_w * 0.06)
        pw, phh = int(card_w - 2 * border), int(card_w - 2 * border)
        photo = ph.get(it, pw, phh)
        if photo is None:
            continue
        card = Image.new("RGBA", (int(card_w), int(card_h)), (252, 251, 247, 255))
        card.paste(photo, (border, border))
        d = ImageDraw.Draw(card)
        cap = _month_year(it)
        if cap:
            f = _fit_text(d, cap, "hand", int((card_h - card_w) * 0.5), int(card_w - 2 * border))
            _text(d, (card_w / 2, card_w + (card_h - card_w) / 2 - border * 0.3), cap, f, (60, 56, 52), "mm")
        ang = rnd.uniform(-9, 9)
        rot = card.rotate(ang, expand=True, resample=Image.Resampling.BICUBIC)
        x = int(min(max(cx - rot.width / 2, 0), canvas.width - rot.width))
        y = int(min(max(cy - rot.height / 2, 0), canvas.height - rot.height))
        _shadow(canvas, (x + 8, y + 8, x + rot.width - 8, y + rot.height - 8), 4, 90, max(6, border // 2))
        canvas.paste(rot, (x, y), rot)


def magazine(ph: _Photos, items, canvas, area, o) -> None:
    ax, ay, aw, ah = area
    s = max(8, int(o.spacing))
    bg = canvas.getpixel((0, 0))
    portrait = ah > aw
    d = ImageDraw.Draw(canvas)
    title = o.title or _span(items) or "Memories"
    sub = _span(items) if o.title else f"{len(items)} photos"
    if portrait:
        big = (ax, ay, aw, int(ah * 0.58))
        side = (ax, ay + big[3] + s, aw, ah - big[3] - s)
    else:
        big = (ax, ay, int(aw * 0.62), ah)
        side = (ax + big[2] + s, ay, aw - big[2] - s, ah)
    p = ph.get(items[0], big[2], big[3])
    if p:
        canvas.paste(p, (big[0], big[1]))
    sx, sy, sw, sh = side
    tf = _fit_text(d, title.upper(), "bold", int(min(sw, sh) * 0.2), int(sw * 0.94))
    _text(d, (sx + sw * 0.03, sy), title.upper(), tf, _ink(bg))
    tb = d.textbbox((sx, sy), title.upper(), font=tf)
    sf = looks.font("serif", max(14, int(tf.size * 0.36)))
    _text(d, (sx + sw * 0.03, tb[3] + s), sub, sf, (110, 110, 110) if not _dark(bg) else (190, 190, 190))
    top = d.textbbox((sx, tb[3] + s), sub or "x", font=sf)[3] + 2 * s
    rest = items[1:]
    if not rest:
        return
    n = min(len(rest), 6)
    cols = 1 if n <= 3 or portrait is False and sw < sh * 0.45 else 2
    if portrait:
        cols = min(3, n)
    rows = math.ceil(n / cols)
    cell_w = (sw - (cols - 1) * s) / cols
    cell_h = (sy + sh - top - (rows - 1) * s) / rows
    for i, it in enumerate(rest[:n]):
        r, c = divmod(i, cols)
        q = ph.get(it, cell_w, cell_h)
        if q:
            canvas.paste(q, (int(sx + c * (cell_w + s)), int(top + r * (cell_h + s))))


def story(ph: _Photos, items, canvas, area, o) -> None:
    ax, ay, aw, ah = area
    groups: dict[str, list[dict]] = {}
    for it in items:
        t = _dt(it)
        groups.setdefault(t.strftime("%Y-%m-%d") if t else "?", []).append(it)
    days = list(groups.values())
    panels = max(1, min(5, len(days) if len(days) > 1 else math.ceil(len(items) / 3)))
    if len(days) < panels or len(days) > 5:            # not by day: even chunks
        per = math.ceil(len(items) / panels)
        days = [items[i:i + per] for i in range(0, len(items), per)]
    best = None
    for cols in range(1, panels + 1):                  # the panel grid that gives the biggest panels
        rows = math.ceil(panels / cols)
        w = min(aw / cols * 0.9, ah / rows * 0.92 * 9 / 16)
        if best is None or w > best[0]:
            best = (w, cols, rows)
    pw, cols, rows = best
    gap = (aw - pw * cols) / (cols + 1)
    vgap = (ah - pw * 16 / 9 * rows) / (rows + 1)
    radius = int(pw * 0.08)
    d = ImageDraw.Draw(canvas)
    for k, grp in enumerate(days[:panels]):
        r, c = divmod(k, cols)
        x0 = ax + gap + c * (pw + gap)
        y0 = ay + vgap + r * (pw * 16 / 9 + vgap)
        ph_h = pw * 16 / 9
        _shadow(canvas, (x0, y0, x0 + pw, y0 + ph_h), radius, 120, 18)
        panel = Image.new("RGB", (int(pw), int(ph_h)), (18, 18, 20))
        shown = grp[:3]
        inner = int(pw * 0.04)
        each = (ph_h - inner * (len(shown) + 1)) / len(shown)
        for j, it in enumerate(shown):
            q = ph.get(it, pw - 2 * inner, each)
            if q:
                panel.paste(q, (inner, int(inner + j * (each + inner))))
        mask = Image.new("L", panel.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, panel.width - 1, panel.height - 1), radius, fill=255)
        canvas.paste(panel, (int(x0), int(y0)), mask)
        t = _dt(grp[0])
        label = (f"Day {k + 1}" if len(days) > 1 else "") + (f" · {t.day} {MONTHS[t.month - 1]}" if t else "")
        f = looks.font("bold", int(pw * 0.085))
        for dx, dy in ((2, 2), (0, 2)):
            _text(d, (x0 + inner * 2.5 + dx, y0 + ph_h - inner * 2.5 + dy), label.strip(" ·"), f, (0, 0, 0), "ld")
        _text(d, (x0 + inner * 2.5, y0 + ph_h - inner * 2.5), label.strip(" ·"), f, (255, 255, 255), "ld")


def _pack_circles(n: int, aw: float, ah: float, seed: int) -> list[tuple[float, float, float]]:
    """Radii from big to small, each placed as near the centre as it fits."""
    rnd = random.Random(seed)
    big = min(aw, ah) * (0.3 if n > 1 else 0.48)
    radii = [big] + [big * max(0.28, 0.75 * (0.93 ** i)) * rnd.uniform(0.8, 1.1) for i in range(n - 1)]
    placed: list[tuple[float, float, float]] = []
    cx, cy = aw / 2, ah / 2
    for r in radii:
        for scale in (1.0, 0.85, 0.7, 0.55, 0.4):
            rr = r * scale
            best = None
            for step in range(0, 4000):
                a = step * 0.35
                dist = 2.2 * math.sqrt(step) * rr / 6
                x, y = cx + math.cos(a) * dist * aw / ah, cy + math.sin(a) * dist
                if x - rr < 0 or y - rr < 0 or x + rr > aw or y + rr > ah:
                    if dist > max(aw, ah):
                        break
                    continue
                if all((x - px) ** 2 + (y - py) ** 2 >= (rr + pr + min(aw, ah) * 0.008) ** 2 for px, py, pr in placed):
                    best = (x, y, rr)
                    break
            if best:
                placed.append(best)
                break
    return placed


def bubbles(ph: _Photos, items, canvas, area, o) -> None:
    ax, ay, aw, ah = area
    circles = _pack_circles(min(len(items), 24), aw, ah, len(items))
    ring = max(3, int(min(aw, ah) * 0.006))
    for it, (x, y, r) in zip(items, circles):
        size = int(2 * r)
        q = ph.get(it, size, size)
        if q is None:
            continue
        x0, y0 = int(ax + x - r), int(ay + y - r)
        _shadow(canvas, (x0, y0, x0 + size, y0 + size), 0, 80, max(6, ring * 2), "circle")
        mask = Image.new("L", (size * 2, size * 2), 0)           # drawn at 2x for smooth edges
        ImageDraw.Draw(mask).ellipse((0, 0, size * 2 - 1, size * 2 - 1), fill=255)
        mask = mask.resize((size, size), Image.Resampling.LANCZOS)
        ring_img = Image.new("RGB", (size + 2 * ring, size + 2 * ring), (255, 255, 255))
        rm = Image.new("L", (ring_img.width * 2, ring_img.height * 2), 0)
        ImageDraw.Draw(rm).ellipse((0, 0, rm.width - 1, rm.height - 1), fill=255)
        canvas.paste(ring_img, (x0 - ring, y0 - ring), rm.resize(ring_img.size, Image.Resampling.LANCZOS))
        canvas.paste(q, (x0, y0), mask)


def _heart_mask(side: int) -> Image.Image:
    """Smooth heart shape, white inside, side x side."""
    k = 2                                              # supersample
    n = side * k // 4                                  # evaluate at 1/2 resolution, then scale up
    import numpy as np
    ys, xs = np.mgrid[0:n, 0:n]
    x = (xs + 0.5) / n * 2.6 - 1.3
    y = 1.3 - (ys + 0.5) / n * 2.6
    inside = ((x * x + y * y - 1) ** 3 - x * x * y ** 3) <= 0
    m = Image.fromarray((inside * 255).astype("uint8"), "L")
    return m.resize((side, side), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(1))


def heart(ph: _Photos, items, canvas, area, o) -> None:
    ax, ay, aw, ah = area
    side = int(min(aw, ah))
    gap = max(2, int(o.spacing * 0.5))
    mask = _heart_mask(side)
    small = mask.resize((200, 200))
    n = len(items)

    def cells(k: int) -> list[tuple[int, int]]:
        c = 200 / k
        out = []
        for gy in range(k):
            for gx in range(k):
                box = small.crop((int(gx * c), int(gy * c), int((gx + 1) * c), int((gy + 1) * c)))
                if ImageStat.Stat(box).mean[0] / 255 > 0.2:
                    out.append((gx, gy))
        return out

    k = 3
    while len(cells(k)) < n and k < 30:
        k += 1
    grid = cells(k)
    cell = side / k
    pool = [ph.get(it, cell - gap, cell - gap) for it in items[:len(grid)]]
    pool = [p for p in pool if p is not None]
    if not pool:
        return
    layer = Image.new("RGB", (side, side), canvas.getpixel((0, 0)))
    for i, (gx, gy) in enumerate(grid):
        layer.paste(pool[i % len(pool)], (int(gx * cell + gap / 2), int(gy * cell + gap / 2)))
    x0, y0 = int(ax + (aw - side) / 2), int(ay + (ah - side) / 2)
    canvas.paste(layer, (x0, y0), mask)


def filmstrip(ph: _Photos, items, canvas, area, o) -> None:
    ax, ay, aw, ah = area
    n = len(items)
    d = ImageDraw.Draw(canvas)
    bg = canvas.getpixel((0, 0))
    title = o.title or _span(items)
    title_h = int(ah * 0.1) if title else 0
    best = None
    for strips in range(1, min(n, 8) + 1):               # strip count that gives the biggest frames
        per = math.ceil(n / strips)
        fw = min(aw / per, (ah - title_h) / (strips * 1.36 * 0.72 + (strips + 1) * 0.12))
        if best is None or fw > best[0] * 1.02:
            best = (fw, strips, per)
    frame_w, strips, per = best
    frame_h = frame_w * 0.72
    strip_h = frame_h * 1.36
    gap = (ah - title_h - strips * strip_h) / (strips + 1)
    left = ax + (aw - frame_w * per) / 2
    hole_w, hole_h = frame_h * 0.07, frame_h * 0.09
    for sidx in range(strips):
        y = ay + gap + sidx * (strip_h + gap)
        d.rectangle((ax - o.spacing, y, ax + aw + o.spacing, y + strip_h), fill=(17, 17, 17))
        hx = ax - o.spacing + hole_w
        while hx < ax + aw + o.spacing:
            for hy in (y + (strip_h - frame_h) / 4 - hole_h / 2, y + strip_h - (strip_h - frame_h) / 4 - hole_h / 2):
                d.rounded_rectangle((hx, hy, hx + hole_w, hy + hole_h), int(hole_w * 0.25), fill=bg)
            hx += hole_w * 2.2
        for j, it in enumerate(items[sidx * per:(sidx + 1) * per]):
            q = ph.get(it, frame_w * 0.92, frame_h)
            if q:
                canvas.paste(q, (int(left + j * frame_w + frame_w * 0.04), int(y + (strip_h - frame_h) / 2)))
    if title:
        f = _fit_text(d, title, "sans", int(title_h * 0.55), int(aw))
        _text(d, (ax + aw / 2, ay + ah - title_h / 2), title, f, _ink(bg), "mm")


def year(ph: _Photos, items, canvas, area, o) -> None:
    ax, ay, aw, ah = area
    d = ImageDraw.Draw(canvas)
    bg = canvas.getpixel((0, 0))
    yrs = Counter(t.year for t in (_dt(i) for i in items) if t)
    yr = yrs.most_common(1)[0][0] if yrs else None
    by_month: dict[int, dict] = {}
    for it in items:
        t = _dt(it)
        if t and t.year == yr and t.month not in by_month:
            by_month[t.month] = it
    title = o.title or (f"{yr} in photos" if yr else "A year in photos")
    th = int(ah * 0.11)
    f = _fit_text(d, title, "bold", int(th * 0.62), int(aw))
    _text(d, (ax, ay + th * 0.45), title, f, _ink(bg), "lm")
    cols, rows = (4, 3) if aw >= ah else (3, 4)
    s = max(6, int(o.spacing))
    label_h = int(ah * 0.045)
    cw = (aw - (cols - 1) * s) / cols
    ch = (ah - th - rows * label_h - (rows - 1) * s) / rows
    lf = looks.font("sans", int(label_h * 0.6))
    muted = (120, 120, 120) if not _dark(bg) else (170, 170, 170)
    for m in range(12):
        r, c = divmod(m, cols)
        x, y = ax + c * (cw + s), ay + th + r * (ch + label_h + s)
        it = by_month.get(m + 1)
        q = ph.get(it, cw, ch) if it else None
        if q:
            canvas.paste(q, (int(x), int(y)))
        else:
            d.rectangle((x, y, x + cw, y + ch), fill=tuple(max(0, min(255, v + (18 if _dark(bg) else -14))) for v in bg))
        _text(d, (x, y + ch + label_h * 0.55), FULL_MONTHS[m], lf, muted, "lm")


def then_now(ph: _Photos, items, canvas, area, o) -> None:
    ax, ay, aw, ah = area
    d = ImageDraw.Draw(canvas)
    bg = canvas.getpixel((0, 0))
    a, b = items[0], items[-1]
    s = max(10, int(o.spacing) * 2)
    label_h = int(ah * 0.12)
    cap_h = int(ah * 0.1)
    portrait = ah > aw * 1.1
    if portrait:
        cw, ch = aw, (ah - 2 * label_h - cap_h - s) / 2
        boxes = [(ax, ay + label_h), (ax, ay + 2 * label_h + ch + s)]
    else:
        cw, ch = (aw - s) / 2, ah - label_h - cap_h
        boxes = [(ax, ay + label_h), (ax + cw + s, ay + label_h)]
    f = looks.font("bold", int(label_h * 0.6))
    for it, (x, y) in zip((a, b), boxes):
        q = ph.get(it, cw, ch)
        if q:
            canvas.paste(q, (int(x), int(y)))
        t = _dt(it)
        _text(d, (x + cw / 2, y - label_h / 2), str(t.year) if t else "", f, _ink(bg), "mm")
    cap = o.title or "Then and now"
    cf = _fit_text(d, cap, "serif", int(cap_h * 0.5), int(aw))
    _text(d, (ax + aw / 2, ay + ah - cap_h / 2), cap, cf, _ink(bg), "mm")


DRAW = {"polaroid": polaroid, "magazine": magazine, "story": story, "bubbles": bubbles, "heart": heart,
        "filmstrip": filmstrip, "year": year, "then_now": then_now}
# Styles that write the title themselves (the others get the normal title band).
OWN_TITLE = {"magazine", "filmstrip", "year", "then_now"}
def render(conn: sqlite3.Connection, items: list[dict], o, canvas: Image.Image, area, progress: Callable,
           cancel: threading.Event) -> None:
    DRAW[o.layout](_Photos(conn, items, progress, cancel), items, canvas, area, o)

