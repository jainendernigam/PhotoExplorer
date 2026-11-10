"""Scan orchestration.

A scan has two phases:

1. **Walk** - list every source folder and reconcile with the database:
   new files are inserted as pending, changed files (size/mtime differ) are
   reset to pending, vanished files are marked missing. Unchanged files are
   not written at all, so rescans are cheap.
2. **Index** - pending files go through a process pool (``extract.process``).
   Results are written back in batches by this thread, the only writer.

Everything is resumable: pending state lives in the database, so a cancelled
or crashed scan continues where it stopped. A worker that crashes the pool
(e.g. a corrupt RAW file inside LibRaw) is isolated and its file marked as
an error instead of stopping the scan.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Iterator

from ..config import Settings
from ..db import Database, transaction
from . import extract
from .formats import classify, long_path, split_ext
from .pool import run_pool
from .walker import walk

log = logging.getLogger(__name__)

# 4 = moved away by the user via the app; 5 = removed from the app only (file left on disk)
PENDING, DONE, ERROR, MISSING, REMOVED, HIDDEN = 0, 1, 2, 3, 4, 5

RESULT_COLUMNS = (
    "sha256", "hash_sampled", "phash", "dhash", "ph0", "ph1", "ph2", "ph3",
    "width", "height", "duration_ms", "video_codec", "has_thumb",
    "taken_at", "tz_offset", "date_source",
    "camera_make", "camera_model", "lens", "gps_lat", "gps_lon", "gps_alt",
)
_UPDATE_RESULT_SQL = (
    "UPDATE media SET status = :status, error = :error, indexed_at = :now, "
    + ", ".join(f"{c} = :{c}" for c in RESULT_COLUMNS)
    + " WHERE id = :id"
)


def _idle_status() -> dict:
    return {
        "state": "idle",  # idle | walking | indexing | dedup | smart | faces | clustering | done | cancelled | error
        "mode": "full",
        "run_id": None,
        "started_at": None,
        "finished_at": None,
        "current": None,
        "folders": 0,
        "discovered": 0,
        "new": 0,
        "changed": 0,
        "missing": 0,
        "moved": 0,             # files found at a new path (folder renamed or moved); they keep everything
        "to_index": 0,
        "indexed": 0,
        "errors": 0,
        "rate": 0.0,            # files / second (indexing)
        "eta_s": None,
        "faces_todo": 0,        # photos to analyse for faces
        "faces_done": 0,
        "faces_found": 0,
        "faces_copied": 0,      # photos that reused an identical file's faces
        "faces_errors": 0,
        "clusters": None,
        "faces_auto_named": None,
        "dedup_exact": None,    # duplicate groups found
        "dedup_similar": None,
        "smart_todo": 0,        # photos to fingerprint for search by content
        "smart_done": 0,
        "warnings": [],
        "error": None,
    }


class ScanManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.s = settings
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._st = _idle_status()

    # ------------------------------------------------------------ public API

    def status(self) -> dict:
        with self._lock:
            st = dict(self._st)
            st["warnings"] = list(st["warnings"][-20:])
            return st

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, source_ids: list[int] | None = None, retry_errors: bool = False,
              mode: str = "full") -> bool:
        """mode: 'full' (walk, index, faces, cluster, duplicates) | 'faces'
        (faces + cluster) | 'recheck' (look again in every photo, then cluster) |
        'cluster' (re-cluster only) | 'dedup' (duplicates only)."""
        if mode not in ("full", "auto", "faces", "recheck", "cluster", "dedup", "smart"):
            raise ValueError(mode)
        with self._lock:
            if self.running:
                return False
            self._cancel.clear()
            self._st = _idle_status()
            self._st.update(state={"full": "walking", "auto": "walking", "dedup": "dedup",
                                   "smart": "smart"}.get(mode, "faces"),
                            mode=mode, started_at=int(time.time()))
            self._thread = threading.Thread(
                target=self._run, args=(source_ids, retry_errors, mode), name="scanner", daemon=True)
            self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: float | None = None) -> bool:
        t = self._thread
        if t:
            t.join(timeout)
        return not self.running

    # ------------------------------------------------------------ internals

    def _set(self, **kw) -> None:
        with self._lock:
            self._st.update(kw)

    def _inc(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                self._st[k] += v

    def _warn(self, msg: str) -> None:
        log.warning(msg)
        with self._lock:
            self._st["warnings"].append(msg)

    def _run(self, source_ids: list[int] | None, retry_errors: bool, mode: str) -> None:
        conn = self.db.connect()
        run_id = conn.execute(
            "INSERT INTO scan_runs(started_at, state) VALUES (?, 'running')", (int(time.time()),)
        ).lastrowid
        self._set(run_id=run_id)
        final = "done"
        try:
            q = "SELECT id, path FROM sources WHERE enabled = 1"
            params: list = []
            if source_ids:
                q += f" AND id IN ({','.join('?' * len(source_ids))})"
                params = list(source_ids)
            sources = conn.execute(q, params).fetchall()
            first_new = conn.execute("SELECT coalesce(max(id), 0) + 1 FROM media").fetchone()[0]
            reachable = []
            for src in sources:
                if self._cancel.is_set() or mode not in ("full", "auto"):
                    break
                if self._walk_source(conn, src["id"], src["path"]):
                    reachable.append(src["id"])
            ids = reachable
            # Renamed / moved folders: the "new" files are old ones (names, groups... stay).
            if ids and not self._cancel.is_set():
                self._relink_moves(conn, first_new)
            if mode == "auto":
                if not self._auto_has_work(conn):
                    final = "idle"
                    return
                mode = "full"     # something changed: index, duplicates, search, faces as usual
                self._set(mode="auto")

            if retry_errors and ids and not self._cancel.is_set():
                conn.execute(
                    f"UPDATE media SET status = {PENDING}, error = NULL "
                    f"WHERE status = {ERROR} AND source_id IN ({','.join('?' * len(ids))})", ids)

            if ids and not self._cancel.is_set():
                self._index(conn, ids)
                if not self._cancel.is_set():
                    self._relink_moves(conn, first_new)   # renamed too: matched by content now
            # Duplicates first: they take minutes, faces can take hours on a big library.
            if mode in ("full", "dedup") and not self._cancel.is_set():
                self._dedup(conn)
            # Search by content next: minutes, and useful long before faces are done.
            if mode in ("full", "smart") and not self._cancel.is_set():
                self._smart(conn)
            if self.s.faces_enabled and mode in ("full", "faces", "recheck", "cluster") and not self._cancel.is_set():
                self._faces(conn, mode)
            if self._cancel.is_set():
                final = "cancelled"
        except Exception as e:
            log.exception("scan failed")
            final = "error"
            self._set(error=f"{type(e).__name__}: {e}")
        finally:
            st = self.status()
            stats = {k: st[k] for k in ("mode", "folders", "discovered", "new", "changed",
                                        "missing", "moved", "indexed", "errors", "faces_done",
                                        "faces_found", "clusters", "dedup_exact", "dedup_similar")}
            if final == "idle":   # automatic check, nothing changed: no history entry
                conn.execute("DELETE FROM scan_runs WHERE id = ?", (run_id,))
            else:
                conn.execute("UPDATE scan_runs SET finished_at = ?, state = ?, stats = ? WHERE id = ?",
                             (int(time.time()), final, json.dumps(stats), run_id))
            conn.close()
            self._set(state=final, finished_at=int(time.time()), current=None, eta_s=None)

    # ------------------------------------------------------------ smart search (CLIP)

    def _smart(self, conn: sqlite3.Connection) -> None:
        from ..smart.phase import SmartPhase
        try:
            SmartPhase(self.s.data_dir, self.s.thumbs_dir, self._cancel, self._set, self._inc).run(conn)
        except Exception as e:      # never let it stop faces and the rest of the scan
            log.exception("smart phase failed")
            self._warn(f"Search by content skipped: {type(e).__name__}: {e}")

    # ------------------------------------------------------------ phase 3: faces

    def _faces(self, conn: sqlite3.Connection, mode: str) -> None:
        from ..faces import models
        missing = models.missing()
        if missing:
            self._warn("Face recognition skipped; missing: " + ", ".join(missing))
            return
        from ..faces.pipeline import FacePhase
        phase = FacePhase(self.s, self._cancel, self._set, self._inc)
        if mode == "cluster":
            from ..faces.pipeline import cluster_all
            self._set(state="clustering")
            res = cluster_all(conn, full=True)   # the user asked to regroup everything
            self._set(clusters=res["clusters"], faces_auto_named=res["auto_assigned"])
        else:
            if mode == "recheck":
                phase.reset_for_recheck(conn)
            phase.run(conn, None)

    # ------------------------------------------------------------ phase 4: duplicates

    def _dedup(self, conn: sqlite3.Connection) -> None:
        from ..dedup.groups import find_groups
        self._set(state="dedup", current=None)
        find_groups(conn, self.s.thumbs_dir, progress=self._set)

    # ------------------------------------------------------------ phase 1: walk

    def _auto_has_work(self, conn: sqlite3.Connection) -> bool:
        """After an automatic walk: did anything change, or is work left over
        (files to index, photos to check for faces, a stopped scan)?"""
        st = self.status()
        if st["new"] or st["changed"] or st["missing"] or st["moved"]:
            return True
        if conn.execute(f"SELECT EXISTS (SELECT 1 FROM media WHERE status = {PENDING})").fetchone()[0]:
            return True
        return self.s.faces_enabled and bool(conn.execute(
            f"SELECT EXISTS (SELECT 1 FROM media WHERE status = {DONE} AND faces_status = 0 "
            f"AND kind IN ('image', 'raw'))").fetchone()[0])

    def _relink_moves(self, conn: sqlite3.Connection, first_new: int) -> None:
        from .moves import find_moves, relink
        moved = relink(conn, find_moves(conn, first_new))
        if moved:
            self._inc(moved=moved, new=-moved, missing=-moved)

    def _walk_source(self, conn: sqlite3.Connection, source_id: int, root: str) -> bool:
        """Returns False if the source folder could not be reached."""
        if not os.path.isdir(long_path(root)):
            # Unplugged drive / offline share: leave its records alone.
            self._warn(f"Source not reachable, skipped: {root}")
            return False
        gen = conn.execute("SELECT scan_gen FROM sources WHERE id = ?", (source_id,)).fetchone()[0] + 1
        conn.execute("UPDATE sources SET scan_gen = ? WHERE id = ?", (gen, source_id))

        folders = {r[1]: r[0] for r in conn.execute(
            "SELECT id, path FROM folders WHERE source_id = ?", (source_id,))}
        exclude = {os.path.normcase(str(self.s.data_dir))}
        exclude |= {os.path.normcase(r[0]) for r in conn.execute(
            "SELECT path FROM folders WHERE source_id = ? AND excluded = 1", (source_id,))}
        unreadable: list[str] = []
        now = int(time.time())

        ops, last_commit = 0, time.monotonic()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for lst in walk(root, exclude, self._cancel):
                if lst.error:
                    unreadable.append(lst.path)
                    self._warn(f"Cannot read folder: {lst.path} ({lst.error})")
                    continue
                self._inc(folders=1, discovered=len(lst.files))
                self._set(current=lst.path)

                fid = folders.get(lst.path)
                if fid is None:
                    if not lst.files:
                        continue
                    fid = conn.execute(
                        "INSERT INTO folders(source_id, path, scan_gen) VALUES (?, ?, ?)",
                        (source_id, lst.path, gen)).lastrowid
                    folders[lst.path] = fid
                    existing = {}
                else:
                    conn.execute("UPDATE folders SET scan_gen = ? WHERE id = ?", (gen, fid))
                    existing = {r["filename"]: r for r in conn.execute(
                        "SELECT id, filename, size, mtime_ns, status, sha256 FROM media WHERE folder_id = ?",
                        (fid,))}

                new_rows, changed, restored = [], [], []
                for name, size, mtime_ns in lst.files:
                    r = existing.pop(name, None)
                    if r is not None and r["status"] == HIDDEN:
                        continue  # the user removed it from the app; leave it out
                    if r is None:
                        new_rows.append((source_id, fid, os.path.join(lst.path, name), name,
                                         split_ext(name), classify(name), size, mtime_ns, now))
                    elif r["size"] != size or r["mtime_ns"] != mtime_ns:
                        changed.append((size, mtime_ns, r["id"]))
                    elif r["status"] in (MISSING, REMOVED):  # back at its path (e.g. restored from Recycle Bin)
                        restored.append((DONE if r["sha256"] is not None else PENDING, r["id"]))
                gone = [(r["id"],) for r in existing.values() if r["status"] not in (MISSING, REMOVED, HIDDEN)]

                if new_rows:
                    conn.executemany(
                        "INSERT INTO media(source_id, folder_id, path, filename, ext, kind, size, mtime_ns, added_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", new_rows)
                if changed:
                    conn.executemany(
                        f"UPDATE media SET size = ?, mtime_ns = ?, status = {PENDING}, error = NULL, "
                        "sha256 = NULL, phash = NULL, dhash = NULL, has_thumb = 0, faces_status = 0 "
                        "WHERE id = ?", changed)
                if restored:
                    conn.executemany("UPDATE media SET status = ? WHERE id = ?", restored)
                if gone:
                    conn.executemany(f"UPDATE media SET status = {MISSING} WHERE id = ?", gone)
                self._inc(new=len(new_rows), changed=len(changed), missing=len(gone))

                ops += 1 + len(new_rows) + len(changed) + len(restored) + len(gone)
                if ops >= 5000 or time.monotonic() - last_commit > 2:
                    conn.execute("COMMIT")
                    conn.execute("BEGIN IMMEDIATE")
                    ops, last_commit = 0, time.monotonic()
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

        if self._cancel.is_set():
            return True  # incomplete walk: never infer deletions from it

        # Folders not seen this walk are gone (unless under an unreadable folder).
        stale = conn.execute("SELECT id, path FROM folders WHERE source_id = ? AND scan_gen < ? AND excluded = 0",
                             (source_id, gen)).fetchall()
        prefixes = [os.path.normcase(p) + os.sep for p in unreadable]
        missing = 0
        with transaction(conn):
            for fid, path in stale:
                np_ = os.path.normcase(path) + os.sep
                if any(np_.startswith(p) for p in prefixes):
                    continue
                missing += conn.execute(
                    f"UPDATE media SET status = {MISSING} WHERE folder_id = ? AND status NOT IN ({MISSING}, {REMOVED}, {HIDDEN})",
                    (fid,)).rowcount
            conn.execute("UPDATE sources SET last_scan_at = ? WHERE id = ?", (int(time.time()), source_id))
        self._inc(missing=missing)
        return True

    # ------------------------------------------------------------ phase 2: index

    def _pending(self, conn: sqlite3.Connection, ids: list[int]) -> Iterator[tuple]:
        marks = ",".join("?" * len(ids))
        last = 0
        while True:
            rows = conn.execute(
                f"SELECT id, path, kind, mtime_ns FROM media WHERE status = {PENDING} "
                f"AND source_id IN ({marks}) AND id > ? ORDER BY id LIMIT 1000",
                (*ids, last)).fetchall()
            if not rows:
                return
            for r in rows:
                yield tuple(r)
            last = rows[-1][0]

    def _index(self, conn: sqlite3.Connection, ids: list[int]) -> None:
        marks = ",".join("?" * len(ids))
        total = conn.execute(
            f"SELECT count(*) FROM media WHERE status = {PENDING} AND source_id IN ({marks})", ids
        ).fetchone()[0]
        self._set(state="indexing", to_index=total, current=None)
        if not total:
            return
        self._t0 = time.monotonic()
        run_pool(
            self._pending(conn, ids), extract.process,
            initializer=extract.init_worker,
            initargs=(str(self.s.thumbs_dir), self.s.thumb_size, self.s.ffmpeg, self.s.ffprobe),
            workers=self.s.workers, cancel=self._cancel,
            on_results=lambda rs: self._write(conn, rs),
            on_crash=lambda job: {"id": job[0], "error": "Worker process crashed reading this file"},
        )

    def _write(self, conn: sqlite3.Connection, results: list[dict]) -> None:
        now = int(time.time())
        rows, errors = [], 0
        for r in results:
            row = {c: r.get(c) for c in RESULT_COLUMNS}
            row["hash_sampled"] = row["hash_sampled"] or 0
            row["has_thumb"] = row["has_thumb"] or 0
            row.update(id=r["id"], now=now, error=r.get("error"),
                       status=ERROR if r.get("error") else DONE)
            errors += bool(r.get("error"))
            rows.append(row)
        with transaction(conn):
            conn.executemany(_UPDATE_RESULT_SQL, rows)

        self._inc(indexed=len(rows), errors=errors)
        st = self.status()
        elapsed = max(time.monotonic() - self._t0, 1e-6)
        rate = st["indexed"] / elapsed
        remaining = max(st["to_index"] - st["indexed"], 0)
        self._set(rate=round(rate, 1), eta_s=int(remaining / rate) if rate > 0 else None)
