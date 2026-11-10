"""Which files we index, and path helpers."""
from __future__ import annotations

import os
import sys

IMAGE_EXT = frozenset({
    "jpg", "jpeg", "jpe", "jfif", "png", "gif", "bmp", "tif", "tiff",
    "webp", "heic", "heif", "avif",
})
RAW_EXT = frozenset({
    "cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "dng", "orf",
    "raf", "rw2", "rwl", "pef", "srw", "x3f", "3fr", "iiq", "erf", "kdc", "mrw",
})
VIDEO_EXT = frozenset({
    "mp4", "m4v", "mov", "avi", "mkv", "wmv", "3gp", "3g2", "mts", "m2ts",
    "mpg", "mpeg", "webm", "flv",
})

# Folder names never worth descending into (compared lower-case).
SKIP_DIRS = frozenset({
    "$recycle.bin", "system volume information", "recycler", "@eadir",
    ".thumbnails", ".trash", ".trashes", ".spotlight-v100", ".fseventsd",
    ".git", "node_modules", "__pycache__",
})


def split_ext(name: str) -> str:
    dot = name.rfind(".")
    return name[dot + 1:].lower() if dot > 0 else ""


def classify(name: str) -> str | None:
    """'image' | 'raw' | 'video' | None (not media)."""
    if name.startswith("._"):  # macOS AppleDouble sidecar files
        return None
    ext = split_ext(name)
    if ext in IMAGE_EXT:
        return "image"
    if ext in RAW_EXT:
        return "raw"
    if ext in VIDEO_EXT:
        return "video"
    return None


def long_path(p: str) -> str:
    """Windows: add the \\\\?\\ prefix so paths over 260 chars work without
    the (admin-only) LongPathsEnabled registry switch."""
    if sys.platform != "win32" or len(p) < 240 or p.startswith("\\\\?\\"):
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\"):  # UNC share
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p
