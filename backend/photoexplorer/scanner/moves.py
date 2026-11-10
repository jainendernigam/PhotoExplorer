"""Files that were moved or renamed keep their library entry.

The library knows a photo by its path. When a folder is renamed or moved, a
walk sees the old paths vanish and the same files appear somewhere else;
without this step each one would become a new photo and lose its names,
groups, albums, favourites, duplicate decisions and edits.

A vanished entry and a new one are the same file when
* their file name, size and modified time all match (a move or folder rename
  keeps the modified time), or
* after indexing, their content hash and size match (renamed as well as moved),
and the match is unique on both sides. ``relink`` then points the old entry
at the new path and drops the new one.
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict

from ..db import transaction
from .formats import long_path

PENDING, DONE, ERROR, MISSING, REMOVED, HIDDEN = 0, 1, 2, 3, 4, 5   # as in scanner.manager


def _unique_pairs(old: dict, new: dict) -> list[tuple[int, int]]:
    """Keys present exactly once on each side -> (old id, new id), old before new."""
    pairs = []
    for key, olds in old.items():
        news = new.get(key)
        if len(olds) == 1 and news is not None and len(news) == 1 and olds[0][0] < news[0][0]:
            pairs.append((olds[0][0], news[0][0]))
    return pairs


def find_moves(conn: sqlite3.Connection, first_new_id: int = 0) -> list[tuple[int, int]]:
    """(old id, new id) pairs: a vanished entry and an entry added since
    ``first_new_id`` that are the same file."""
    new_rows = conn.execute(
        f"SELECT id, filename, size, mtime_ns, sha256 FROM media WHERE id >= ? "
        f"AND status IN ({PENDING}, {DONE}, {ERROR})", (first_new_id,)).fetchall()
    if not new_rows:
        return []
    by_name, by_hash = defaultdict(list), defaultdict(list)
    for r in new_rows:
        by_name[(r[1].lower(), r[2], r[3])].append(r)
        if r[4] is not None:
            by_hash[(r[4], r[2])].append(r)
    sizes = {r[2] for r in new_rows}

    # Vanished: marked missing, or hidden by the user and no longer at its path.
    old_name, old_hash = defaultdict(list), defaultdict(list)
    for r in conn.execute(f"SELECT id, filename, size, mtime_ns, sha256, status, path FROM media "
                          f"WHERE status IN ({MISSING}, {HIDDEN})"):
        if r[2] not in sizes:
            continue
        name_key, hash_key = (r[1].lower(), r[2], r[3]), (r[4], r[2])
        if name_key not in by_name and (r[4] is None or hash_key not in by_hash):
            continue
        if r[5] == HIDDEN and os.path.exists(long_path(r[6])):
            continue
        old_name[name_key].append(r)
        if r[4] is not None:
            old_hash[hash_key].append(r)

    pairs = _unique_pairs(old_name, by_name)
    used_old, used_new = {o for o, _ in pairs}, {n for _, n in pairs}
    for o, n in _unique_pairs(old_hash, by_hash):
        if o not in used_old and n not in used_new:
            pairs.append((o, n))
            used_old.add(o)
            used_new.add(n)
    return pairs


def relink(conn: sqlite3.Connection, pairs: list[tuple[int, int]]) -> int:
    """Point each old entry at its new entry's path and delete the new entry
    (its own faces, thumbnails rows etc. are duplicates of the old ones).
    A hidden photo stays hidden; a missing one is back (pending if it was
    never indexed)."""
    if not pairs:
        return 0
    done = 0
    with transaction(conn):
        for old_id, new_id in pairs:
            n = conn.execute("SELECT source_id, folder_id, path, filename, ext FROM media WHERE id = ?",
                             (new_id,)).fetchone()
            o = conn.execute("SELECT status, sha256 FROM media WHERE id = ?", (old_id,)).fetchone()
            if n is None or o is None:
                continue
            status = HIDDEN if o[0] == HIDDEN else (DONE if o[1] is not None else PENDING)
            conn.execute("DELETE FROM media WHERE id = ?", (new_id,))
            conn.execute("UPDATE media SET source_id = ?, folder_id = ?, path = ?, filename = ?, ext = ?, "
                         "status = ? WHERE id = ?", (*n, status, old_id))
            # "Removed from the app only" entries remember where the file was.
            conn.execute("UPDATE removals SET original_path = ? WHERE media_id = ? AND method = 'app' "
                         "AND restored_at IS NULL", (n[2], old_id))
            done += 1
    return done
