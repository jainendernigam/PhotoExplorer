"""Face phase of a scan: detect faces in indexed photos, then re-cluster.

Identical files (same SHA-256) are analysed once; their copies reuse the
faces of the first one.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Callable, Iterator

from ..config import Settings
from ..db import transaction
from ..scanner.pool import run_pool
from . import arcface, models, worker
from .cluster import ClusterParams, recluster

TODO = "m.status = 1 AND m.faces_status = 0 AND m.kind IN ('image', 'raw')"

# While faces are being found, regroup now and then so People fills in as it goes.
REGROUP_EVERY_S = 600

_INSERT = ("INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model, chip) "
           "VALUES (:media_id, :ordinal, :x, :y, :w, :h, :score, :sharpness, :size_px, :embedding, :model, :chip)")


class FacePhase:
    def __init__(self, settings: Settings, cancel: threading.Event,
                 set_status: Callable[..., None], inc_status: Callable[..., None]):
        self.s = settings
        self.cancel = cancel
        self._set = set_status
        self._inc = inc_status

    def run(self, conn: sqlite3.Connection, source_ids: list[int] | None, cluster: bool = True) -> None:
        src = ""
        args: list = []
        if source_ids:
            src = f" AND m.source_id IN ({','.join('?' * len(source_ids))})"
            args = list(source_ids)

        # Which recogniser: the built-in SFace, or a downloaded ArcFace model.
        self.kind = arcface.current(conn, self.s.data_dir)
        self.tag = arcface.TAGS[self.kind]
        # Faces found by an earlier run that was stopped are grouped first.
        if cluster and not self.cancel.is_set() and self._needs_cluster(conn):
            self._cluster(conn)
        self._set(state="faces")
        self._copy_from_twins(conn)
        self._last_group = time.monotonic()
        total = conn.execute(f"SELECT count(*) FROM media m WHERE {TODO}{src}", args).fetchone()[0]
        self._set(faces_todo=total)
        if total and not self.cancel.is_set():
            run_pool(
                self._jobs(conn, src, args), worker.process,
                initializer=worker.init_worker,
                initargs=(str(self.s.thumbs_dir), str(models.model_path("detector")),
                          str(models.model_path("recognizer")), self.s.face_max_side,
                          self.s.face_min_px, self.s.face_det_score,
                          str(arcface.model_file(self.s.data_dir, self.kind)) if self.kind != "sface" else ""),
                workers=self.s.workers, cancel=self.cancel,
                on_results=lambda rs: self._write_and_group(conn, rs, cluster),
                on_crash=lambda job: {"id": job[0], "error": "Worker process crashed reading this file"},
                batch=100,
            )
            self._copy_from_twins(conn)

        if cluster and not self.cancel.is_set() and self._needs_cluster(conn):
            self._cluster(conn)

    def _cluster(self, conn: sqlite3.Connection) -> None:
        self._set(state="clustering")
        res = recluster(conn, self.tag, cluster_params(self.kind))
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('faces_clustered_max_id', "
                     "(SELECT coalesce(max(id), 0) FROM faces))")
        self._set(clusters=res["clusters"], faces_auto_named=res["auto_assigned"])

    def _write_and_group(self, conn: sqlite3.Connection, results: list[dict], cluster: bool) -> None:
        self._write(conn, results)
        if cluster and time.monotonic() - self._last_group > REGROUP_EVERY_S and not self.cancel.is_set():
            self._copy_from_twins(conn)
            if self._needs_cluster(conn):
                self._cluster(conn)
                self._set(state="faces")
            self._last_group = time.monotonic()

    def _jobs(self, conn, src: str, args: list) -> Iterator[tuple]:
        last = 0
        while True:
            # Only the first copy of identical files; the rest are copied after.
            rows = conn.execute(
                f"SELECT m.id, m.path, m.kind, m.width FROM media m WHERE {TODO}{src} AND m.id > ? "
                "AND NOT EXISTS (SELECT 1 FROM media d WHERE d.sha256 = m.sha256 AND d.id < m.id "
                "AND d.status = 1 AND d.kind IN ('image', 'raw')) ORDER BY m.id LIMIT 500",
                (*args, last)).fetchall()
            if not rows:
                return
            for r in rows:
                yield tuple(r)
            last = rows[-1][0]

    def _write(self, conn: sqlite3.Connection, results: list[dict]) -> None:
        found = errors = 0
        with transaction(conn):
            for r in results:
                if "error" in r:
                    errors += 1
                    conn.execute("UPDATE media SET faces_status = 2 WHERE id = ?", (r["id"],))
                    continue
                old = _old_faces(conn, r["id"])
                conn.execute("DELETE FROM faces WHERE media_id = ?", (r["id"],))
                conn.executemany(_INSERT, [dict(f, media_id=r["id"], model=self.tag)
                                           for f in r["faces"]])
                _carry_names(conn, r["id"], old)
                found += len(r["faces"])
                conn.execute("UPDATE media SET faces_status = 1 WHERE id = ?", (r["id"],))
        self._inc(faces_done=len(results), faces_found=found, faces_errors=errors)

    def _copy_from_twins(self, conn: sqlite3.Connection) -> None:
        """Pending photos whose identical twin was already analysed reuse its faces."""
        with transaction(conn):
            conn.execute("DROP TABLE IF EXISTS temp.twin")
            conn.execute(
                "CREATE TEMP TABLE twin AS SELECT m.id AS pid, "
                "(SELECT d.id FROM media d WHERE d.sha256 = m.sha256 AND d.faces_status IN (1, 2) "
                " AND d.id != m.id LIMIT 1) AS did "
                f"FROM media m WHERE {TODO} AND m.sha256 IS NOT NULL")
            conn.execute("DELETE FROM temp.twin WHERE did IS NULL")
            n = conn.execute("SELECT count(*) FROM temp.twin").fetchone()[0]
            named = [r[0] for r in conn.execute(
                "SELECT DISTINCT media_id FROM faces WHERE person_id IS NOT NULL "
                "AND media_id IN (SELECT pid FROM temp.twin)")] if n else []
            olds = {m: _old_faces(conn, m) for m in named}
            if n:
                conn.execute("DELETE FROM faces WHERE media_id IN (SELECT pid FROM temp.twin)")
                conn.execute(
                    "INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model, chip) "
                    "SELECT t.pid, f.ordinal, f.x, f.y, f.w, f.h, f.score, f.sharpness, f.size_px, "
                    "f.embedding, f.model, f.chip FROM temp.twin t JOIN faces f ON f.media_id = t.did")
                conn.execute(
                    "UPDATE media SET faces_status = (SELECT d.faces_status FROM media d "
                    "JOIN temp.twin t ON t.did = d.id WHERE t.pid = media.id) "
                    "WHERE id IN (SELECT pid FROM temp.twin)")
                for m, old in olds.items():
                    _carry_names(conn, m, old)
            conn.execute("DROP TABLE temp.twin")
        if n:
            self._inc(faces_copied=n)

    @staticmethod
    def reset_for_recheck(conn: sqlite3.Connection) -> int:
        """Mark every checked photo to be looked at again. Faces and names stay
        until each photo is redone; names move to the same face found again."""
        with transaction(conn):
            return conn.execute("UPDATE media SET faces_status = 0 WHERE status = 1 AND faces_status IN (1, 2) "
                                "AND kind IN ('image', 'raw')").rowcount

    @staticmethod
    def _needs_cluster(conn: sqlite3.Connection) -> bool:
        r = conn.execute("SELECT value FROM meta WHERE key = 'faces_clustered_max_id'").fetchone()
        last = int(r[0]) if r else -1
        cur = conn.execute("SELECT coalesce(max(id), 0) FROM faces").fetchone()[0]
        return cur != last


# ------------------------------------------------------------ keep names when a photo is redone

def _old_faces(conn: sqlite3.Connection, media_id: int) -> list[dict]:
    rows = conn.execute("SELECT id, x, y, w, h, person_id, assigned_by FROM faces WHERE media_id = ?",
                        (media_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(("id", "x", "y", "w", "h", "person_id", "assigned_by"), r))
        d["rejected"] = [x[0] for x in conn.execute(
            "SELECT person_id FROM face_rejections WHERE face_id = ?", (d["id"],))]
        d["cover_of"] = [x[0] for x in conn.execute(
            "SELECT id FROM persons WHERE cover_face_id = ?", (d["id"],))]
        if d["person_id"] is not None or d["rejected"] or d["cover_of"]:
            out.append(d)
    return out


def _iou(a: dict, b: dict) -> float:
    x1, y1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x2 = min(a["x"] + a["w"], b["x"] + b["w"])
    y2 = min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _carry_names(conn: sqlite3.Connection, media_id: int, old: list[dict]) -> None:
    """Give each newly found face the name, rejections and cover role of the
    old face in the same place (boxes overlap by at least half)."""
    if not old:
        return
    new = [dict(zip(("id", "x", "y", "w", "h"), r)) for r in conn.execute(
        "SELECT id, x, y, w, h FROM faces WHERE media_id = ?", (media_id,))]
    pairs = sorted(((_iou(o, n), i, j) for i, o in enumerate(old) for j, n in enumerate(new)), reverse=True)
    used_o, used_n = set(), set()
    for v, i, j in pairs:
        if v < 0.5:
            break
        if i in used_o or j in used_n:
            continue
        used_o.add(i); used_n.add(j)
        o, nid = old[i], new[j]["id"]
        if o["person_id"] is not None:
            conn.execute("UPDATE faces SET person_id = ?, assigned_by = ? WHERE id = ?",
                         (o["person_id"], o["assigned_by"], nid))
        conn.executemany("INSERT OR IGNORE INTO face_rejections(face_id, person_id) VALUES (?, ?)",
                         [(nid, p) for p in o["rejected"]])
        conn.executemany("UPDATE persons SET cover_face_id = ? WHERE id = ?", [(nid, p) for p in o["cover_of"]])


def cluster_params(kind: str) -> ClusterParams:
    link, join = arcface.CLUSTER.get(kind, arcface.CLUSTER["sface"])
    return ClusterParams(eps_sim=link, join_sim=join)
