"""Paths and settings.

Portable by design: data lives in ``<app root>/data`` when that folder is
writable, otherwise in the per-user app-data folder. Nothing is written to
system locations or the registry.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "PhotoExplorer"


def app_root() -> Path:
    """Folder holding backend/, runtime/, bin/ and (by default) data/."""
    env = os.environ.get("PHOTOEXPLORER_ROOT")
    if env:
        return Path(env).resolve()
    # backend/photoexplorer/config.py -> project root
    return Path(__file__).resolve().parents[2]


def _user_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def resolve_data_dir(explicit: str | os.PathLike | None = None) -> Path:
    for cand in (explicit, os.environ.get("PHOTOEXPLORER_DATA")):
        if cand:
            d = Path(cand).resolve()
            d.mkdir(parents=True, exist_ok=True)
            return d
    portable = app_root() / "data"
    if _writable(portable):
        return portable
    d = _user_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_tool(name: str) -> str | None:
    """Bundled binary (bin/ or runtime/bin/) first, then PATH."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    for d in (app_root() / "bin", app_root() / "runtime" / "bin"):
        if (d / exe).is_file():
            return str(d / exe)
    return shutil.which(name)


@dataclass
class Settings:
    data_dir: Path
    workers: int = field(default_factory=lambda: max(1, (os.cpu_count() or 2) - 1))
    thumb_size: int = 320
    ffmpeg: str | None = field(default_factory=lambda: find_tool("ffmpeg"))
    ffprobe: str | None = field(default_factory=lambda: find_tool("ffprobe"))
    faces_enabled: bool = True
    face_max_side: int = 2048      # photos are scaled to this before detection
    face_min_px: int = 24          # smallest face (in the scaled photo) to keep;
                                   # under 40 px they join groups but never start one
    face_det_score: float = 0.7    # detector confidence threshold

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def exports_dir(self) -> Path:
        """Default folder for slideshows and collages (each job can choose another)."""
        return self.data_dir / "exports"

    @classmethod
    def load(cls, data_dir: str | os.PathLike | None = None, **overrides) -> "Settings":
        s = cls(data_dir=resolve_data_dir(data_dir), **overrides)
        s.thumbs_dir.mkdir(parents=True, exist_ok=True)
        return s
