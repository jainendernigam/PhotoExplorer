"""Per-photo face work, run inside worker processes (see scanner.pool)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ..scanner import extract

_engine = None
_cfg: dict = {}
CHIP_SIZE = 160


def init_worker(thumb_dir: str, detector: str, recognizer: str, max_side: int,
                min_face_px: int, det_score: float, arcface: str = "") -> None:
    global _engine
    extract.init_worker(thumb_dir, 320, None, None)  # HEIC opener + low priority
    import cv2
    cv2.setNumThreads(1)  # parallelism comes from the process pool
    from .engine import FaceEngine
    _engine = FaceEngine(detector, recognizer, det_score, min_face_px, arcface or None)
    _cfg.update(thumb_dir=thumb_dir, max_side=max_side)


def chip_rel(media_id: int, ordinal: int) -> str:
    return f"faces/{media_id % 4096:03x}/{media_id}_{ordinal}.webp"


def process(job: tuple[int, str, str, int | None]) -> dict:
    media_id, path, kind, orig_w = job
    try:
        img = extract.load_rgb(path, kind, _cfg["max_side"])
        W, H = img.size
        scale = (orig_w or W) / W
        faces = []
        for i, f in enumerate(_engine.analyze(np.asarray(img))):
            # square chip with margin, for the People page
            side = max(f.w, f.h) * 1.5
            cx, cy = f.x + f.w / 2, f.y + f.h / 2
            box = (int(max(cx - side / 2, 0)), int(max(cy - side / 2, 0)),
                   int(min(cx + side / 2, W)), int(min(cy + side / 2, H)))
            chip = img.crop(box)
            chip.thumbnail((CHIP_SIZE, CHIP_SIZE))
            rel = chip_rel(media_id, i)
            dest = Path(_cfg["thumb_dir"]) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            chip.save(dest, "WEBP", quality=80)
            faces.append({
                "ordinal": i,
                "x": max(f.x, 0) / W, "y": max(f.y, 0) / H,
                "w": min(f.w, W) / W, "h": min(f.h, H) / H,
                "score": f.score, "sharpness": f.sharpness, "quality": f.quality,
                "size_px": int(f.w * scale),
                "embedding": f.embedding.tobytes(),
                "chip": rel,
            })
        return {"id": media_id, "faces": faces}
    except Exception as e:
        return {"id": media_id, "error": f"{type(e).__name__}: {e}"[:500]}
