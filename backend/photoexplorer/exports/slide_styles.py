"""Extra slideshow styles: the special slides and the timing they need.

* split screen   - two photos share one slide, split on a slant.
* big titles     - large bold title cards and captions (they zoom in faster).
* month cards    - "Year in review": a card before each new month.
* trip map       - a drawn route of where the photos were taken (from GPS, no
                   internet map), shown before each new day of the trip.
* highlights     - picks the best photos for a short reel (faces, named people,
                   sharp faces), spread over the whole time span.
* beat sync      - finds the beat of the music so every cut lands on a beat.
"""
from __future__ import annotations

import math
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import frames, looks

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def _dt(it: dict) -> datetime | None:
    return datetime.fromtimestamp(it["taken_at"], tz=timezone.utc) if it.get("taken_at") else None


def _fit(d: ImageDraw.ImageDraw, text: str, style: str, size: int, max_w: float):
    f = looks.font(style, max(12, size))
    while size > 12 and d.textlength(text, font=f) > max_w:
        size = int(size * 0.92)
        f = looks.font(style, size)
    return f


def _shadow_text(d, xy, text, f, fill, anchor="mm", off=3):
    for dx, dy in ((off, off), (0, off), (off, 0)):
        d.text((xy[0] + dx, xy[1] + dy), text, font=f, fill=(0, 0, 0), anchor=anchor)
    d.text(xy, text, font=f, fill=fill, anchor=anchor)


# ---------------------------------------------------------------- split screen


def split(a: Image.Image, b: Image.Image, w: int, h: int, focus_a=None, focus_b=None) -> Image.Image:
    """Two photos on one slide with a slanted dividing line."""
    slant = int(w * 0.08)
    left = frames.cover(a, w // 2 + slant, h, focus_a)
    right = frames.cover(b, w // 2 + slant, h, focus_b)
    out = Image.new("RGB", (w, h))
    out.paste(left, (0, 0))
    layer = Image.new("RGB", (w, h))
    layer.paste(right, (w - right.width, 0))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon([(w // 2 + slant, 0), (w, 0), (w, h), (w // 2 - slant, h)], fill=255)
    out.paste(layer, (0, 0), mask)
    line = max(4, w // 240)
    ImageDraw.Draw(out).line([(w // 2 + slant, 0), (w // 2 - slant, h)], fill=(250, 250, 250), width=line)
    return out


def is_portrait(it: dict) -> bool:
    return (it.get("height") or 0) > (it.get("width") or 0)


def pairs(items: list[dict]) -> list[list[dict]]:
    """Consecutive portrait photos go two to a slide; landscape ones stay alone."""
    out, i = [], 0
    while i < len(items):
        if i + 1 < len(items) and is_portrait(items[i]) and is_portrait(items[i + 1]):
            out.append([items[i], items[i + 1]])
            i += 2
        else:
            out.append([items[i]])
            i += 1
    return out


# ---------------------------------------------------------------- cards


def big_title_card(title: str, subtitle: str, w: int, h: int, bg: str, fg: str, accent: str = "#ffd84d") -> Image.Image:
    img = Image.new("RGB", (w, h), looks._hex(bg, (20, 20, 24)))
    d = ImageDraw.Draw(img)
    words = title.upper().split()
    lines = [" ".join(words[: math.ceil(len(words) / 2)]), " ".join(words[math.ceil(len(words) / 2):])] \
        if len(words) > 1 and d.textlength(title.upper(), font=looks.font("bold", int(h * 0.2))) > w * 0.9 else [title.upper()]
    y = h * (0.42 if len(lines) == 1 else 0.33)
    for k, line in enumerate(l for l in lines if l):
        f = _fit(d, line, "bold", int(h * 0.2), w * 0.88)
        d.text((w / 2, y), line, font=f, fill=looks._hex(accent if k else fg), anchor="mm")
        y += f.size * 1.05
    if subtitle:
        f = _fit(d, subtitle, "sans", int(h * 0.05), w * 0.8)
        d.text((w / 2, y + h * 0.04), subtitle, font=f, fill=looks._hex(fg), anchor="mm")
    return img


def big_caption(img: Image.Image, text: str, margin: float) -> Image.Image:
    if not text:
        return img
    w, h = img.size
    d = ImageDraw.Draw(img)
    f = _fit(d, text.upper(), "bold", int(h * 0.075), w * 0.86)
    _shadow_text(d, (w / 2, h * (0.88 - margin)), text.upper(), f, (255, 255, 255), off=max(2, h // 300))
    return img


def month_card(first: Image.Image, thumbs: list[Image.Image], when: datetime, w: int, h: int,
               style: str = "bold") -> Image.Image:
    """Year in review: the month's first photo blurred behind its name and a strip of its photos."""
    bg = frames.cover(first, max(1, w // 10), max(1, h // 10)).filter(ImageFilter.GaussianBlur(2)).resize((w, h))
    bg = Image.blend(bg, Image.new("RGB", (w, h), (10, 12, 30)), 0.55)
    d = ImageDraw.Draw(bg)
    fy = looks.font(style, int(h * 0.08))
    d.text((w / 2, h * 0.24), str(when.year), font=fy, fill=(255, 216, 120), anchor="mm")
    fm = _fit(d, MONTHS[when.month - 1].upper(), style, int(h * 0.16), w * 0.85)
    _shadow_text(d, (w / 2, h * 0.42), MONTHS[when.month - 1].upper(), fm, (255, 255, 255), off=max(2, h // 300))
    if thumbs:
        n = len(thumbs)
        tw = min(w * 0.8 / n, h * 0.3 * 4 / 3)
        th = tw * 0.75
        x = (w - n * tw - (n - 1) * tw * 0.06) / 2
        for t in thumbs:
            c = frames.cover(t, int(tw), int(th))
            d.rectangle((x - 4, h * 0.6 - 4, x + tw + 4, h * 0.6 + th + 4), fill=(255, 255, 255))
            bg.paste(c, (int(x), int(h * 0.6)))
            x += tw * 1.06
    return bg


# ---------------------------------------------------------------- trip map


def has_route(items: list[dict]) -> bool:
    pts = {(round(i["gps_lat"], 3), round(i["gps_lon"], 3)) for i in items if i.get("gps_lat") is not None}
    return len(pts) >= 2


def map_card(items: list[dict], upto: int, day_no: int, when: datetime | None, photo: Image.Image | None,
             w: int, h: int, style: str = "bold") -> Image.Image:
    """The whole route of the trip, travelled part drawn strong, today's place marked."""
    img = Image.new("RGB", (w, h), (226, 233, 218))
    d = ImageDraw.Draw(img)
    grid = max(40, w // 24)
    for x in range(0, w, grid):
        d.line([(x, 0), (x, h)], fill=(214, 223, 205), width=1)
    for y in range(0, h, grid):
        d.line([(0, y), (w, y)], fill=(214, 223, 205), width=1)
    pts = [(i["gps_lat"], i["gps_lon"]) for i in items if i.get("gps_lat") is not None]
    lat0 = sum(p[0] for p in pts) / len(pts)
    k = math.cos(math.radians(lat0))
    xs, ys = [p[1] * k for p in pts], [p[0] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny, 1e-4)
    box = (w * 0.08, h * 0.16, w * (0.58 if photo else 0.92), h * 0.9)
    sc = min((box[2] - box[0]) / max(maxx - minx, span * 0.2), (box[3] - box[1]) / max(maxy - miny, span * 0.2))
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    mx, my = (minx + maxx) / 2, (miny + maxy) / 2

    def proj(lat, lon):
        return cx + (lon * k - mx) * sc, cy - (lat - my) * sc

    seq = []
    for i in items:                                          # route through places in time order
        if i.get("gps_lat") is None:
            continue
        p = proj(i["gps_lat"], i["gps_lon"])
        if not seq or math.dist(seq[-1], p) > max(6, w * 0.006):
            seq.append(p)
    gps_before = [i for i in items[:upto + 1] if i.get("gps_lat") is not None]
    here = proj(gps_before[-1]["gps_lat"], gps_before[-1]["gps_lon"]) if gps_before else seq[0]
    lw = max(3, w // 320)
    if len(seq) > 1:
        d.line(seq, fill=(200, 160, 130), width=lw, joint="curve")
    done = [proj(i["gps_lat"], i["gps_lon"]) for i in gps_before]
    if len(done) > 1:
        d.line(done, fill=(180, 95, 42), width=lw * 2, joint="curve")
    r = max(6, w // 160)
    for p in seq:
        d.ellipse((p[0] - r * 0.6, p[1] - r * 0.6, p[0] + r * 0.6, p[1] + r * 0.6), fill=(180, 95, 42))
    d.ellipse((here[0] - r * 2, here[1] - r * 2, here[0] + r * 2, here[1] + r * 2), fill=(255, 255, 255))
    d.ellipse((here[0] - r * 1.4, here[1] - r * 1.4, here[0] + r * 1.4, here[1] + r * 1.4), fill=(180, 95, 42))
    label = f"Day {day_no}" + (f"  ·  {when.day} {MONTHS[when.month - 1]} {when.year}" if when else "")
    f = _fit(d, label, style, int(h * 0.07), w * 0.9)
    d.text((w * 0.06, h * 0.08), label, font=f, fill=(51, 51, 51), anchor="lm")
    if photo:
        pw = int(w * 0.32)
        ph = int(pw * 0.75)
        c = frames.cover(photo, pw, ph)
        x, y = int(w * 0.62), int((h - ph) / 2)
        d.rectangle((x - 10, y - 10, x + pw + 10, y + ph + 10), fill=(255, 255, 255))
        img.paste(c, (x, y))
    return img


def trip_days(items: list[dict]) -> list[int]:
    """Indexes where a new day of the trip starts (only days that have GPS photos)."""
    out, last = [], None
    for k, it in enumerate(items):
        t = _dt(it)
        day = t.date() if t else None
        if day != last and it.get("gps_lat") is not None:
            out.append(k)
            last = day
    return out


# ---------------------------------------------------------------- highlights


def highlights(conn: sqlite3.Connection, items: list[dict], n: int) -> list[dict]:
    """The best n photos, spread over the whole span: one per time slice."""
    if n <= 0 or len(items) <= n:
        return items
    ids = [i["id"] for i in items]
    score = {i: 0.0 for i in ids}
    for c in range(0, len(ids), 900):
        chunk = ids[c:c + 900]
        for r in conn.execute(
                f"SELECT media_id, count(*), sum(person_id IS NOT NULL), max(coalesce(sharpness, 0)), max(w * h) "
                f"FROM faces WHERE media_id IN ({','.join('?' * len(chunk))}) GROUP BY media_id", chunk):
            faces, named, sharp, big = r[1], r[2] or 0, r[3] or 0, r[4] or 0
            score[r[0]] = 1.0 + min(faces, 4) * 0.5 + named * 1.5 + min(sharp, 200) / 100 + min(big * 20, 2)
    out = []
    step = len(items) / n
    for k in range(n):
        part = items[int(k * step): max(int(k * step) + 1, int((k + 1) * step))]
        out.append(max(part, key=lambda it: (score[it["id"]], (it.get("width") or 0) * (it.get("height") or 0))))
    return out


# ---------------------------------------------------------------- beat sync


def find_beat(ffmpeg: str, music: str, max_s: float = 90.0) -> tuple[float, float] | None:
    """(seconds per beat, time of the first beat) of a song, or None if no clear beat."""
    sr = 11025
    try:
        raw = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-t", str(max_s), "-i", music,
                              "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True, timeout=60,
                             creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if a.size < sr * 6:
        return None
    hop = 256
    n = a.size // hop
    energy = np.sqrt((a[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    onset = np.maximum(0, np.diff(np.log1p(energy)))                     # rises in loudness
    onset = onset - np.convolve(onset, np.ones(16) / 16, mode="same")
    onset = np.maximum(onset, 0)
    fps = sr / hop
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)                   # 70..180 beats a minute
    ac = np.correlate(onset, onset, mode="full")[onset.size - 1:]
    if ac[0] <= 0 or hi >= ac.size:
        return None
    lag = lo + int(np.argmax(ac[lo:hi]))
    if ac[lag] / ac[0] < 0.05:
        return None
    period = lag / fps
    # phase: the offset whose comb of beats collects the most onset
    best, phase = -1.0, 0.0
    for p in range(lag):
        s = onset[p::lag].sum()
        if s > best:
            best, phase = s, p / fps
    return period, phase


def beat_length(period: float, wanted: float) -> float:
    """A slide length of a whole number of beats, near the wanted seconds."""
    beats = max(1, round(wanted / period))
    return beats * period
