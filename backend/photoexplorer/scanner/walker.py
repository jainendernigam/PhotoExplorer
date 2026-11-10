"""Fast directory walk.

``os.scandir`` returns size and mtime with the directory listing on Windows,
so walking a million files needs no per-file stat calls. Symlinks and
junctions are skipped to avoid loops.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Iterator

from .formats import SKIP_DIRS, classify, long_path


@dataclass
class DirListing:
    path: str
    files: list[tuple[str, int, int]] = field(default_factory=list)  # (name, size, mtime_ns)
    error: str | None = None


def walk(root: str, exclude: set[str], cancel: threading.Event | None = None) -> Iterator[DirListing]:
    """Depth-first walk yielding one DirListing per directory (media files only).

    ``exclude`` holds ``os.path.normcase``-d paths to skip (e.g. our data dir).
    A directory that cannot be read is yielded with ``error`` set so the
    caller does not treat its contents as deleted.
    """
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        d = stack.pop()
        files: list[tuple[str, int, int]] = []
        subdirs: list[str] = []
        try:
            with os.scandir(long_path(d)) as it:
                for e in it:
                    try:
                        if e.is_symlink() or (hasattr(e, "is_junction") and e.is_junction()):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            name = e.name
                            if name.startswith(".") or name.lower() in SKIP_DIRS:
                                continue
                            p = os.path.join(d, name)
                            if os.path.normcase(p) not in exclude:
                                subdirs.append(p)
                        elif e.is_file(follow_symlinks=False) and classify(e.name):
                            st = e.stat(follow_symlinks=False)
                            if st.st_size > 0:
                                files.append((e.name, st.st_size, st.st_mtime_ns))
                    except OSError:
                        continue
        except OSError as ex:
            yield DirListing(d, error=f"{type(ex).__name__}: {ex}")
            continue
        subdirs.sort(reverse=True)  # pop() then visits them in name order
        stack.extend(subdirs)
        yield DirListing(d, files)
