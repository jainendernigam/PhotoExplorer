"""Face detection + identity embedding (OpenCV YuNet + SFace)."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class DetectedFace:
    x: float          # box in input-image pixels
    y: float
    w: float
    h: float
    score: float
    sharpness: float  # variance of Laplacian on the aligned 112x112 face
    embedding: np.ndarray  # float32[128], L2-normalised


class FaceEngine:
    def __init__(self, detector_path: str, recognizer_path: str,
                 score_threshold: float = 0.8, min_face_px: int = 32, arcface_path: str | None = None):
        self.min_face_px = min_face_px
        self.det = cv2.FaceDetectorYN.create(detector_path, "", (320, 320), score_threshold, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(recognizer_path, "")
        self.arc = None
        if arcface_path:   # optional better recogniser (see arcface.py)
            from .arcface import ArcFace
            self.arc = ArcFace(arcface_path)

    def analyze(self, rgb: np.ndarray) -> list[DetectedFace]:
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, found = self.det.detect(bgr)
        if found is None:
            return []
        out = []
        for f in found:
            x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
            if min(fw, fh) < self.min_face_px:
                continue
            if self.arc is not None:
                try:
                    aligned = self.arc.align(bgr, f[4:14])
                except ValueError:
                    continue
                emb = self.arc.embed(aligned)
            else:
                aligned = self.rec.alignCrop(bgr, f)        # 112x112, eyes aligned
                emb = self.rec.feature(aligned).reshape(-1).astype(np.float32)
            norm = float(np.linalg.norm(emb))
            if norm == 0:
                continue
            gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
            sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            out.append(DetectedFace(x, y, fw, fh, score, sharp, emb / norm))
        return out
