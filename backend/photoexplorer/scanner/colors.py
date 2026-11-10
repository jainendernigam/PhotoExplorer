"""Main colour of each photo (0xRRGGBB), shown in the grid while its thumbnail
loads. New photos get it during the scan; this fills it in for photos scanned
before version 14, from their thumbnails, in a background thread."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)


def color_of(img: Image.Image) -> int:
    """Average colour of an RGB image, as 0xRRGGBB."""
    r, g, b = img.convert("RGB").resize((1, 1), Image.Resampling.BOX, reducing_gap=4.0).getpixel((0, 0))[:3]
    return (r << 16) | (g << 8) | b


def _thumb(thumbs_dir: Path, sha: bytes) -> Path:
    h = sha.hex()
    return thumbs_dir / h[:2] / h[2:4] / f"{h}.webp"


def backfill(db_path: Path, thumbs_dir: Path, batch: int = 300) -> int:
    """Fills `color` for photos with a thumbnail but no colour yet. Returns how many."""
    conn = sqlite3.connect(db_path, timeout=30)
    done = 0
    try:
        last = 0
        while True:
            rows = conn.execute(
                "SELECT id, sha256 FROM media WHERE id > ? AND has_thumb = 1 AND color IS NULL "
                "AND sha256 IS NOT NULL ORDER BY id LIMIT ?", (last, batch)).fetchall()
            if not rows:
                return done
            out = []
            for mid, sha in rows:
                try:
                    with Image.open(_thumb(thumbs_dir, sha)) as im:
                        out.append((color_of(im), mid))
                except Exception:   # thumbnail missing or unreadable: leave it grey
                    pass
            if out:
                with conn:
                    conn.executemany("UPDATE media SET color = ? WHERE id = ? AND color IS NULL", out)
            done += len(out)
            last = rows[-1][0]
            time.sleep(0.05)   # stay gentle while the app is in use
    finally:
        conn.close()


def start_backfill(db_path: Path, thumbs_dir: Path) -> None:
    def run():
        try:
            n = backfill(Path(db_path), Path(thumbs_dir))
            if n:
                log.info("Main colours filled for %d photos", n)
        except Exception:
            log.exception("Filling main colours failed")
    threading.Thread(target=run, daemon=True, name="colors").start()
