"""Optional better face recognition: InsightFace ArcFace models run with onnxruntime.

* "arcface_s" - buffalo_s pack, w600k_mbf (MobileFaceNet), about 13 MB model
* "arcface_l" - buffalo_l pack, w600k_r50 (ResNet-50), about 166 MB model, best

InsightFace's pretrained models are for non-commercial use, so they are never
shipped: the user downloads a pack from InsightFace's own release page from
inside the app, and only the recognition model is kept. YuNet still finds the
faces; its five landmarks align each face to ArcFace's 112 x 112 layout.

Which recogniser is in use is stored in the library (meta 'face_model'); faces
are tagged with it, so faces from the old and new model are never mixed.
"""
from __future__ import annotations

import io
import os
import sqlite3
import threading
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

PACKS = {
    "arcface_s": {"zip": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip",
                  "member": "w600k_mbf.onnx", "size": 125_000_000, "label": "Better (smaller download)"},
    "arcface_l": {"zip": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
                  "member": "w600k_r50.onnx", "size": 288_000_000, "label": "Best (bigger download)"},
}
TAGS = {"sface": "sface-2021dec", "arcface_s": "arcface-w600k-mbf", "arcface_l": "arcface-w600k-r50"}
# Grouping strictness per recogniser (cosine similarity): (link, join a group)
CLUSTER = {"sface": (0.42, 0.37), "arcface_s": (0.36, 0.30), "arcface_l": (0.36, 0.30)}
DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)


def model_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "models" / "faces"


def model_file(data_dir: Path, kind: str) -> Path:
    return model_dir(data_dir) / PACKS[kind]["member"]


def installed(data_dir: Path) -> list[str]:
    return [k for k in PACKS if model_file(data_dir, k).is_file()]


def current(conn: sqlite3.Connection, data_dir: Path) -> str:
    r = conn.execute("SELECT value FROM meta WHERE key = 'face_model'").fetchone()
    kind = r[0] if r else "sface"
    if kind != "sface" and not model_file(data_dir, kind).is_file():
        return "sface"
    return kind


def set_current(conn: sqlite3.Connection, kind: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('face_model', ?)", (kind,))


def download(data_dir: Path, kind: str, progress=lambda done, total: None, cancel: threading.Event | None = None) -> None:
    p = PACKS[kind]
    dest = model_file(data_dir, kind)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(p["zip"], headers={"User-Agent": "PhotoExplorer"})
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or p["size"])
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Cancelled")
            b = r.read(1 << 20)
            if not b:
                break
            buf.write(b)
            progress(buf.tell(), total)
    with zipfile.ZipFile(buf) as z:
        name = next((n for n in z.namelist() if n.endswith(p["member"])), None)
        if not name:
            raise RuntimeError(f"{p['member']} not found in the pack")
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(z.read(name))
        os.replace(tmp, dest)


class ArcFace:
    """Aligned 112 x 112 face -> L2-normalised 512-number embedding."""

    def __init__(self, path: str | Path, threads: int = 1):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    @staticmethod
    def align(bgr: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        import cv2
        m, _ = cv2.estimateAffinePartial2D(landmarks.reshape(5, 2).astype(np.float32), DST, method=cv2.LMEDS)
        if m is None:
            raise ValueError("cannot align")
        return cv2.warpAffine(bgr, m, (112, 112), borderValue=0)

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        rgb = aligned_bgr[:, :, ::-1].astype(np.float32)
        x = ((rgb - 127.5) / 127.5).transpose(2, 0, 1)[None]
        v = self.sess.run(None, {self.inp: x})[0][0].astype(np.float32)
        return v / max(float(np.linalg.norm(v)), 1e-9)
