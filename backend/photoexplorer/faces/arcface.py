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
# Grouping settings per recogniser (fields of cluster.ClusterParams), tuned on a
# 2,000-photo / 5,600-face ArcFace R50 sample of a real library:
# * eps_sim: at 0.36, 70% of the faces chained into one group (half of it
#   two-per-photo); 0.42 plus split_loose keeps same-photo clashes near 1%.
# * merge_sim: checked by eye, groups whose mean faces were >= 0.84 alike were
#   one person split apart; 0.6-0.75 were different people or unclear.
# * min_quality: raw ArcFace length; real faces p10 ~20, junk (hands, fabric,
#   flowers, side-on faces) median ~13, p90 16-18.
# * min_score: detector score, used when quality is unknown (SFace, older
#   faces); real faces p10 0.85, junk median 0.75.
# * sure_sim / margin / doubt_sim (adding new faces, cluster.assign_new): with 20%
#   of the sample's grouped faces held out as "new", 0.45 / 0.10 added 87% of them
#   automatically at 99.9% precision and left 13% to confirm; only 3% of faces in
#   no group were added to one. SFace values are a conservative guess (untuned).
_ARC = dict(eps_sim=0.42, join_sim=0.36, min_cohesion=0.40, merge_sim=0.80, min_quality=17.0, min_score=0.80,
            sure_sim=0.45, margin=0.10, doubt_sim=0.30)
CLUSTER = {"sface": dict(eps_sim=0.42, join_sim=0.37, min_cohesion=0.45, merge_sim=0.80, min_score=0.80,
                         sure_sim=0.55, margin=0.10, doubt_sim=0.40),
           "arcface_s": _ARC, "arcface_l": _ARC}
DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)


def model_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "models" / "faces"


def model_file(data_dir: Path, kind: str) -> Path:
    return model_dir(data_dir) / PACKS[kind]["member"]


def runtime_ok() -> bool:
    """onnxruntime is importable. Without it an ArcFace model file is useless:
    every face worker would fail and mark its photos as errors."""
    import importlib.util
    return importlib.util.find_spec("onnxruntime") is not None


def installed(data_dir: Path) -> list[str]:
    if not runtime_ok():
        return []
    return [k for k in PACKS if model_file(data_dir, k).is_file()]


def current(conn: sqlite3.Connection, data_dir: Path) -> str:
    r = conn.execute("SELECT value FROM meta WHERE key = 'face_model'").fetchone()
    kind = r[0] if r else "sface"
    if kind != "sface" and kind not in installed(data_dir):
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

    def embed_raw(self, aligned_bgr: np.ndarray) -> np.ndarray:
        """The model's output before scaling. Its length is a face-quality
        signal: real, frontal faces give about 20-25; hands, fabric, flowers
        and side-on faces mistaken for faces give about 10-17."""
        rgb = aligned_bgr[:, :, ::-1].astype(np.float32)
        x = ((rgb - 127.5) / 127.5).transpose(2, 0, 1)[None]
        return self.sess.run(None, {self.inp: x})[0][0].astype(np.float32)

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        v = self.embed_raw(aligned_bgr)
        return v / max(float(np.linalg.norm(v)), 1e-9)
