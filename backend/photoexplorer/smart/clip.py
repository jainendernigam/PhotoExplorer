"""CLIP (OpenAI ViT-B/32, MIT licence) run offline with onnxruntime.

One model gives every photo a 512-number "meaning" fingerprint. A typed phrase
gets one too, and photos whose fingerprint is close to the phrase's match it:
"beach", "birthday cake", "red car". The same fingerprints sort photos into
categories (screenshot, selfie, document, receipt, dog, cat) and group pets.

The model files are an optional download (~155 MB) from Hugging Face
(Xenova/clip-vit-base-patch32, quantized ONNX), kept in ``<data>/models/clip``.
"""
from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

REPO = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
FILES = {   # name: (path in the repo, approximate size in bytes)
    "vision.onnx": ("onnx/vision_model_quantized.onnx", 89_117_001),
    "text.onnx": ("onnx/text_model_quantized.onnx", 64_504_507),
    "tokenizer.json": ("tokenizer.json", 2_224_119),
}
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
SIZE = 224


def model_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "models" / "clip"


def missing(data_dir: Path) -> list[str]:
    out = []
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        out.append("python package onnxruntime")
    d = model_dir(data_dir)
    out += [n for n in FILES if not (d / n).is_file()]
    return out


def download(data_dir: Path, progress=lambda done, total: None, cancel: threading.Event | None = None) -> None:
    d = model_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    total = sum(s for _, s in FILES.values())
    done = 0
    for name, (path, size) in FILES.items():
        dest = d / name
        if dest.is_file():
            done += size
            continue
        tmp = dest.with_suffix(".part")
        req = urllib.request.Request(REPO + path, headers={"User-Agent": "PhotoExplorer"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("Cancelled")
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                done += len(b)
                progress(done, total)
        if tmp.stat().st_size < size * 0.9:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{name}: download incomplete")
        os.replace(tmp, dest)
    progress(total, total)


# ------------------------------------------------------------ tokenizer (CLIP byte-level BPE)

def _bytes_to_unicode() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b); cs.append(256 + n); n += 1
    return dict(zip(bs, map(chr, cs)))


# letters | a digit | other non-space symbols (the CLIP split, without the \p{} classes)
_SPLIT = re.compile(r"'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|[^\s\w]+|_+", re.UNICODE)


class Tokenizer:
    def __init__(self, path: Path):
        t = json.loads(Path(path).read_text(encoding="utf-8"))
        self.vocab: dict[str, int] = t["model"]["vocab"]
        merges = t["model"]["merges"]
        self.ranks = {tuple(m.split(" ") if isinstance(m, str) else m): i for i, m in enumerate(merges)}
        self.byte_enc = _bytes_to_unicode()
        self.sot, self.eot = 49406, 49407
        self.cache: dict[str, list[str]] = {}

    def _bpe(self, word: str) -> list[str]:
        if word in self.cache:
            return self.cache[word]
        parts = list(word[:-1]) + [word[-1] + "</w>"]
        while len(parts) > 1:
            pairs = [(self.ranks.get((a, b), 1 << 30), i) for i, (a, b) in enumerate(zip(parts, parts[1:]))]
            rank, i = min(pairs)
            if rank == 1 << 30:
                break
            parts = parts[:i] + [parts[i] + parts[i + 1]] + parts[i + 2:]
        self.cache[word] = parts
        return parts

    def encode(self, text: str, max_len: int = 77) -> list[int]:
        text = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().lower()
        ids = [self.sot]
        for w in _SPLIT.findall(text):
            enc = "".join(self.byte_enc[b] for b in w.encode("utf-8"))
            ids += [self.vocab.get(p, self.eot) for p in self._bpe(enc)]
        return ids[:max_len - 1] + [self.eot]


# ------------------------------------------------------------ model

class Clip:
    def __init__(self, data_dir: Path, threads: int = 0):
        import onnxruntime as ort
        d = model_dir(data_dir)
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        so.log_severity_level = 3
        self._vision = ort.InferenceSession(str(d / "vision.onnx"), so, providers=["CPUExecutionProvider"])
        self._text = ort.InferenceSession(str(d / "text.onnx"), so, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer(d / "tokenizer.json")
        self._lock = threading.Lock()

    @staticmethod
    def prepare(img: Image.Image) -> np.ndarray:
        """Shortest side to 224 (bicubic), centre crop, normalise -> CHW float32."""
        img = img.convert("RGB")
        w, h = img.size
        k = SIZE / min(w, h)
        img = img.resize((max(SIZE, round(w * k)), max(SIZE, round(h * k))), Image.BICUBIC)
        w, h = img.size
        l, t = (w - SIZE) // 2, (h - SIZE) // 2
        a = np.asarray(img.crop((l, t, l + SIZE, t + SIZE)), np.float32) / 255.0
        return ((a - MEAN) / STD).transpose(2, 0, 1)

    def images(self, batch: list[np.ndarray]) -> np.ndarray:
        """Prepared images -> L2-normalised embeddings (n, 512)."""
        out = self._vision.run(None, {"pixel_values": np.stack(batch).astype(np.float32)})[0]
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)

    def text(self, phrase: str) -> np.ndarray:
        return self._text_cached(phrase)

    @lru_cache(maxsize=512)
    def _text_cached(self, phrase: str) -> np.ndarray:
        ids = np.array([self.tok.encode(phrase)], np.int64)
        with self._lock:
            v = self._text.run(None, {"input_ids": ids})[0][0]
        return (v / max(float(np.linalg.norm(v)), 1e-9)).astype(np.float32)
