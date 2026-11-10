"""Print layouts saved as a PDF (print it from any PDF viewer, or keep it).

Layouts: one photo per page, 2 or 4 per page, or a contact sheet (a grid of
small photos with their file names). Photos are fitted whole (not cropped),
turned to suit the cell, and placed at 200 dpi.
"""
from __future__ import annotations

import math
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from .exports import frames, looks

DPI = 200
PAPERS = {"a4": (210, 297), "letter": (215.9, 279.4), "a3": (297, 420), "4x6": (101.6, 152.4)}
LAYOUTS = {"full": (1, 1), "two": (1, 2), "four": (2, 2), "contact": None}


class Cancelled(Exception):
    pass


def _px(mm: float) -> int:
    return int(round(mm / 25.4 * DPI))


def _caption(it: dict) -> str:
    t = datetime.fromtimestamp(it["taken_at"], tz=timezone.utc).strftime("%d %b %Y") if it.get("taken_at") else ""
    return f"{it['filename']}  {t}".strip()


def render(conn: sqlite3.Connection, items: list[dict], out: Path, paper: str = "a4", layout: str = "full",
           landscape: bool = False, captions: bool = False, contact_cols: int = 5, margin_mm: float = 10,
           progress: Callable[[float, str], None] = lambda f, m: None,
           cancel: threading.Event | None = None) -> int:
    if not items:
        raise ValueError("No photos to print")
    pw, ph = PAPERS.get(paper, PAPERS["a4"])
    if landscape:
        pw, ph = ph, pw
    W, H, m = _px(pw), _px(ph), _px(margin_mm)
    if layout == "contact":
        cols = max(2, min(10, int(contact_cols)))
        cell_w = (W - 2 * m) / cols
        rows = max(1, int((H - 2 * m) / (cell_w * 0.95)))
        captions = True
    else:
        cols, rows = LAYOUTS.get(layout, (1, 1))
        if landscape and layout == "two":
            cols, rows = 2, 1
    per_page = cols * rows
    gap = _px(4)
    cell_w = (W - 2 * m - (cols - 1) * gap) / cols
    cell_h = (H - 2 * m - (rows - 1) * gap) / rows
    cap_h = int(cell_h * (0.12 if layout == "contact" else 0.06)) if captions else 0
    f = looks.font("sans", max(14, int(cap_h * 0.55))) if captions else None
    pages: list[Image.Image] = []
    for p0 in range(0, len(items), per_page):
        page = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(page)
        for k, it in enumerate(items[p0:p0 + per_page]):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            r, c = divmod(k, cols)
            x0, y0 = m + c * (cell_w + gap), m + r * (cell_h + gap)
            bw, bh = int(cell_w), int(cell_h - cap_h)
            try:
                img = frames.load(it["path"], it["kind"], int(max(bw, bh) * 1.1))
            except Exception:
                d.rectangle((x0, y0, x0 + bw, y0 + bh), outline=(200, 200, 200), width=2)
                continue
            if (img.width > img.height) != (bw > bh) and layout != "contact" and min(bw, bh) > 0:
                img = img.rotate(90, expand=True)              # use the cell's shape best
            s = min(bw / img.width, bh / img.height)
            img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.Resampling.LANCZOS)
            page.paste(img, (int(x0 + (bw - img.width) / 2), int(y0 + (bh - img.height) / 2)))
            if captions and f:
                text = _caption(it)
                while len(text) > 4 and d.textlength(text, font=f) > bw:
                    text = text[:-2]
                d.text((x0 + bw / 2, y0 + bh + cap_h / 2), text, font=f, fill=(60, 60, 60), anchor="mm")
            done = p0 + k + 1
            progress(done / len(items), f"Page {len(pages) + 1}, photo {done} of {len(items)}")
        pages.append(page)
    out.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(out, "PDF", resolution=DPI, save_all=True, append_images=pages[1:])
    return len(pages)


def page_count(n: int, layout: str, paper: str = "a4", landscape: bool = False, contact_cols: int = 5) -> int:
    if layout == "contact":
        pw, ph = PAPERS.get(paper, PAPERS["a4"])
        if landscape:
            pw, ph = ph, pw
        cols = max(2, min(10, int(contact_cols)))
        rows = max(1, int((ph - 20) / ((pw - 20) / cols * 0.95)))
        return math.ceil(n / (cols * rows))
    c, r = LAYOUTS.get(layout, (1, 1))
    return math.ceil(n / (c * r))
