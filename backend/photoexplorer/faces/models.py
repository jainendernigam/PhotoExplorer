"""Face model files: where they live and how to fetch them.

Default models are from the OpenCV model zoo and run on CPU through OpenCV's
DNN module (no GPU, no extra runtime):

* YuNet (face_detection_yunet_2023mar) - detector + 5 landmarks, MIT licence
* SFace (face_recognition_sface_2021dec) - 128-d identity embedding, Apache 2.0

They are shipped in ``<app root>/models`` alongside the app.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import urllib.request
from pathlib import Path

from ..config import app_root

MODEL_TAG = "sface-2021dec"   # stored in faces.model; change when the embedder changes

MODELS = {
    "detector": {
        "file": "face_detection_yunet_2023mar.onnx",
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "urls": [
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
            "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        ],
    },
    "recognizer": {
        "file": "face_recognition_sface_2021dec.onnx",
        "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "urls": [
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
            "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
        ],
    },
}


def models_dir() -> Path:
    return Path(os.environ.get("PHOTOEXPLORER_MODELS") or app_root() / "models")


def model_path(key: str) -> Path:
    return models_dir() / MODELS[key]["file"]


def missing() -> list[str]:
    """What is needed before faces can run (empty list = ready)."""
    out = []
    if importlib.util.find_spec("cv2") is None:
        out.append("python package opencv-python-headless")
    out += [MODELS[k]["file"] for k in MODELS if not model_path(k).is_file()]
    return out


def available() -> bool:
    return not missing()


def _sha256(p: Path) -> str:
    with open(p, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def download(log=print) -> None:
    d = models_dir()
    d.mkdir(parents=True, exist_ok=True)
    for key, m in MODELS.items():
        dest = d / m["file"]
        if dest.is_file():
            log(f"{m['file']}: present ({_sha256(dest)})")
            continue
        tmp = dest.with_suffix(".part")
        for url in m["urls"]:
            try:
                log(f"{m['file']}: downloading {url}")
                urllib.request.urlretrieve(url, tmp)
                with open(tmp, "rb") as f:
                    if f.read(40).startswith(b"version https://git-lfs"):
                        raise ValueError("got a Git LFS pointer, not the model")
                digest = _sha256(tmp)
                if m["sha256"] and digest != m["sha256"]:
                    raise ValueError(f"checksum mismatch ({digest})")
                os.replace(tmp, dest)
                log(f"{m['file']}: ok, {dest.stat().st_size:,} bytes, sha256 {digest}")
                break
            except Exception as e:
                log(f"  failed: {e}")
                tmp.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"Could not download {m['file']}")
