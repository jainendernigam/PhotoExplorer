"""File hashes (exact duplicates) and perceptual hashes (look-alikes).

pHash/dHash follow the ``imagehash`` library's algorithm (same bits), but use
a NumPy DCT so we avoid shipping SciPy.
"""
from __future__ import annotations

import hashlib
import os

import numpy as np
from PIL import Image

from .formats import long_path

FULL_HASH_LIMIT = 256 * 1024 * 1024   # above this (videos), hash samples
SAMPLE = 8 * 1024 * 1024


def file_sha256(path: str) -> tuple[bytes, bool]:
    """(digest, sampled). Large files hash size + head + middle + tail."""
    p = long_path(path)
    size = os.path.getsize(p)
    with open(p, "rb", buffering=0) as f:
        if size <= FULL_HASH_LIMIT:
            return hashlib.file_digest(f, "sha256").digest(), False
        h = hashlib.sha256(size.to_bytes(8, "little"))
        for off in (0, size // 2 - SAMPLE // 2, size - SAMPLE):
            f.seek(off)
            h.update(f.read(SAMPLE))
        return h.digest(), True


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    return np.cos(np.pi * (2 * i + 1) * k / (2 * n))


_D32 = _dct_matrix(32)


def _bits_to_int(bits: np.ndarray) -> int:
    return int.from_bytes(np.packbits(bits.astype(np.uint8).ravel()).tobytes(), "big")


def phash(img: Image.Image) -> int:
    g = np.asarray(img.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_D32 @ g @ _D32.T)[:8, :8]
    return _bits_to_int(low > np.median(low))


def dhash(img: Image.Image) -> int:
    g = np.asarray(img.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    return _bits_to_int(g[:, 1:] > g[:, :-1])


def to_signed64(u: int) -> int:
    """SQLite INTEGER is signed 64-bit."""
    return u - (1 << 64) if u >= (1 << 63) else u


def to_unsigned64(s: int) -> int:
    return s + (1 << 64) if s < 0 else s


def bands(u: int) -> tuple[int, int, int, int]:
    """Four 16-bit slices. Two hashes within Hamming distance 3 must share at
    least one slice exactly, so indexed equality lookups find candidates."""
    return ((u >> 48) & 0xFFFF, (u >> 32) & 0xFFFF, (u >> 16) & 0xFFFF, u & 0xFFFF)


def hamming(a: int, b: int) -> int:
    return (to_unsigned64(a) ^ to_unsigned64(b)).bit_count()
