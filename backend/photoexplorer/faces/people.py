"""People operations: name clusters, merge, confirm or reject faces.

All of these only change the database; photos on disk are never touched.
"""
from __future__ import annotations

import sqlite3
import time

from ..db import transaction


class PeopleError(ValueError):
    pass


def _person_by_name(conn: sqlite3.Connection, name: str) -> int | None:
    r = conn.execute("SELECT id FROM persons WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    return r[0] if r else None


def _ensure_person(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise PeopleError("Name cannot be empty.")
    pid = _person_by_name(conn, name)
    if pid is None:
        pid = conn.execute("INSERT INTO persons(name, created_at) VALUES (?, ?)",
                           (name, int(time.time()))).lastrowid
    return pid


def name_cluster(conn: sqlite3.Connection, cluster_id: int, name: str) -> dict:
    """Give a cluster a name. Using an existing name merges the cluster into
    that person (the usual way to join two clusters of the same person)."""
    if cluster_id < 0:
        raise PeopleError("Unclustered faces cannot be named as a group.")
    with transaction(conn):
        pid = _ensure_person(conn, name)
        n = conn.execute(
            "UPDATE faces SET person_id = ?, assigned_by = 'cluster' WHERE cluster_id = ? "
            "AND NOT (assigned_by = 'user' AND person_id IS NOT NULL AND person_id != ?) "
            "AND id NOT IN (SELECT face_id FROM face_rejections WHERE person_id = ?)",
            (pid, cluster_id, pid, pid)).rowcount
    return {"person_id": pid, "faces": n}


def rename_person(conn: sqlite3.Connection, person_id: int, name: str | None = None,
                  birth_date: str | None = None, hidden: bool | None = None,
                  cover_face_id: int | None = None) -> None:
    with transaction(conn):
        if name is not None:
            other = _person_by_name(conn, name.strip())
            if other is not None and other != person_id:
                raise PeopleError(f"'{name}' already exists; merge instead.")
            conn.execute("UPDATE persons SET name = ? WHERE id = ?", (name.strip(), person_id))
        if birth_date is not None:
            conn.execute("UPDATE persons SET birth_date = ? WHERE id = ?", (birth_date or None, person_id))
        if hidden is not None:
            conn.execute("UPDATE persons SET hidden = ? WHERE id = ?", (int(hidden), person_id))
        if cover_face_id is not None:
            conn.execute("UPDATE persons SET cover_face_id = ? WHERE id = ?", (cover_face_id, person_id))


def merge_persons(conn: sqlite3.Connection, target_id: int, source_ids: list[int]) -> int:
    """Move all faces of ``source_ids`` into ``target_id`` and delete the sources."""
    sources = [s for s in source_ids if s != target_id]
    if not sources:
        return 0
    marks = ",".join("?" * len(sources))
    with transaction(conn):
        if conn.execute("SELECT 1 FROM persons WHERE id = ?", (target_id,)).fetchone() is None:
            raise PeopleError("Target person not found.")
        n = conn.execute(f"UPDATE faces SET person_id = ? WHERE person_id IN ({marks})",
                         (target_id, *sources)).rowcount
        conn.execute(f"INSERT OR IGNORE INTO face_rejections(face_id, person_id) "
                     f"SELECT face_id, ? FROM face_rejections WHERE person_id IN ({marks})",
                     (target_id, *sources))
        conn.execute(f"DELETE FROM persons WHERE id IN ({marks})", sources)
    return n


def assign_faces(conn: sqlite3.Connection, face_ids: list[int], person_id: int | None = None,
                 name: str | None = None) -> int:
    """User says: these faces are this person (by id, or by name, created if new)."""
    with transaction(conn):
        if person_id is None:
            person_id = _ensure_person(conn, name or "")
        marks = ",".join("?" * len(face_ids))
        conn.execute(f"DELETE FROM face_rejections WHERE person_id = ? AND face_id IN ({marks})",
                     (person_id, *face_ids))
        return conn.execute(f"UPDATE faces SET person_id = ?, assigned_by = 'user' WHERE id IN ({marks})",
                            (person_id, *face_ids)).rowcount


def reject_faces(conn: sqlite3.Connection, face_ids: list[int], person_id: int) -> int:
    """User says: these faces are NOT this person. Re-clustering respects it."""
    marks = ",".join("?" * len(face_ids))
    with transaction(conn):
        conn.executemany("INSERT OR IGNORE INTO face_rejections(face_id, person_id) VALUES (?, ?)",
                         [(f, person_id) for f in face_ids])
        return conn.execute(
            f"UPDATE faces SET person_id = NULL, assigned_by = NULL WHERE person_id = ? AND id IN ({marks})",
            (person_id, *face_ids)).rowcount


def delete_person(conn: sqlite3.Connection, person_id: int) -> None:
    """Forget the name; its faces go back to unnamed clusters."""
    with transaction(conn):
        conn.execute("UPDATE faces SET person_id = NULL, assigned_by = NULL WHERE person_id = ?", (person_id,))
        conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))


def media_filter_sql(person_ids: list[int], mode: str = "all") -> tuple[str, list]:
    """SQL condition on ``m.id`` for photos showing these people.
    mode 'all' = every one of them is in the photo; 'any' = at least one."""
    ids = sorted(set(person_ids))
    marks = ",".join("?" * len(ids))
    if mode == "any":
        return f"m.id IN (SELECT media_id FROM faces WHERE person_id IN ({marks}))", ids
    return (f"m.id IN (SELECT media_id FROM faces WHERE person_id IN ({marks}) "
            f"GROUP BY media_id HAVING count(DISTINCT person_id) = {len(ids)})", ids)
