"""Run folder exports: copy / hard link / symbolic link / move, with undo.

* copy      - originals untouched; copies verified by size.
* hardlink  - a second name for the same file: no extra disk space, same drive
              only, no admin rights needed (NTFS, APFS, ext4...).
* symlink   - shortcut-like link; on Windows needs Developer Mode or admin.
* move      - real clean-up: originals are moved into the new structure and
              the library index follows them (no rescan). Undo moves them back.

Every job writes a CSV report into the destination and keeps a per-file log
in the database, which is what Undo uses.
"""
from __future__ import annotations

import csv
import dataclasses
import json
import logging
import os
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime

from ..config import Settings
from ..db import Database, transaction
from ..dedup import actions as dedup_actions
from ..scanner.formats import long_path, split_ext
from ..scanner.hashing import file_sha256
from . import plan as planner
from .plan import OrganizeOptions

log = logging.getLogger(__name__)
MODES = ("copy", "hardlink", "symlink", "move")


class OrganizeError(Exception):
    pass


def parse_options(raw: dict) -> OrganizeOptions:
    names = {f.name for f in dataclasses.fields(OrganizeOptions)}
    unknown = set(raw) - names
    if unknown:
        raise OrganizeError(f"Unknown options: {', '.join(sorted(unknown))}")
    o = OrganizeOptions(**raw)
    try:
        o.validate()
    except ValueError as e:
        raise OrganizeError(str(e)) from None
    return o


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/")


def _inside(child: str, parent: str) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p + os.sep)


def _volume(path: str) -> str:
    p = os.path.abspath(path)
    while not os.path.exists(p):
        p = os.path.dirname(p)
    return os.path.splitdrive(p)[0].upper() if os.name == "nt" else str(os.stat(p).st_dev)


def _symlinks_allowed(folder: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder) as d:
            os.symlink(os.path.join(d, "x"), os.path.join(d, "y"))
        return True
    except (OSError, NotImplementedError):
        return False


class OrganizeManager:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.s = db, settings
        self._q: queue.Queue[int] = queue.Queue()
        self._cancel = threading.Event()
        self._current: int | None = None
        c = db.connect()
        try:
            c.execute("UPDATE organize_jobs SET status = 'error', message = 'Interrupted (app closed)' "
                      "WHERE status IN ('queued', 'running')")
        finally:
            c.close()
        threading.Thread(target=self._loop, name="organize", daemon=True).start()

    # ------------------------------------------------------------ checks + preview

    def _check(self, conn: sqlite3.Connection, dest_root: str, mode: str, p: planner.Plan) -> tuple[list[str], list[str]]:
        """(errors that block the job, warnings to show)."""
        errors, warnings = [], []
        if not os.path.isabs(dest_root):
            errors.append("Choose a full destination folder path")
            return errors, warnings
        if _inside(dest_root, str(self.s.data_dir)) or _inside(str(self.s.data_dir), dest_root):
            errors.append("The destination cannot be (inside) the app's own data folder")
        sources = conn.execute("SELECT id, path FROM sources").fetchall()
        in_source = next((s["path"] for s in sources if _inside(dest_root, s["path"])), None)
        if mode != "move" and in_source:
            warnings.append(f"The destination is inside a library folder ({in_source}); after the next scan the "
                            "new files will show up as duplicates. A folder outside the library is usually better.")
        if mode == "move" and not in_source:
            warnings.append("The destination will be added to the library as a new folder, so moved photos stay in it.")
        if mode == "move" and any(_inside(s["path"], dest_root) for s in sources if not _inside(dest_root, s["path"])):
            errors.append("The destination contains a library folder; choose a folder inside it or elsewhere")
        new = p.new
        if mode == "hardlink":
            dv = _volume(dest_root)
            if any(_volume(i.src) != dv for i in new):
                errors.append("Hard links only work on the same drive as the photos; use Copy instead")
        if mode == "symlink" and new and not _symlinks_allowed(dest_root):
            errors.append("Windows only allows symbolic links with Developer Mode or admin rights; use Hard link or Copy")
        if mode == "copy" and new:
            need = sum(i.size for i in new)
            probe = dest_root
            while not os.path.exists(probe):
                probe = os.path.dirname(probe)
            free = shutil.disk_usage(probe).free
            if need > free * 0.98:
                errors.append(f"Not enough free space: need {need / 1e9:.1f} GB, {free / 1e9:.1f} GB free")
        if mode == "move" and p.trimmed_people:
            warnings.append(f"{p.trimmed_people} photo(s) with several of the chosen people go to the first person's "
                            "folder only (a moved file can only be in one place).")
        return errors, warnings

    def preview(self, media_ids: list[int], dest_root: str, mode: str, options: dict, sample: int = 80) -> dict:
        if mode not in MODES:
            raise OrganizeError("mode must be copy, hardlink, symlink or move")
        o = parse_options(options)
        conn = self.db.conn
        p = planner.build(conn, media_ids, dest_root, o, one_place_per_photo=(mode == "move"))
        errors, warnings = self._check(conn, dest_root, mode, p)
        new = p.new
        return {
            "files": len(p.items), "new": len(new),
            "already_there": sum(i.status == "exists" for i in p.items),
            "same_in_selection": sum(i.status == "same" for i in p.items),
            "bytes": sum(i.size for i in new),
            "folders": len({os.path.dirname(i.dest) for i in new}),
            "sample": [{"src": i.src, "dest": os.path.relpath(i.dest, dest_root), "status": i.status}
                       for i in p.items[:sample]],
            "errors": errors, "warnings": warnings,
        }

    # ------------------------------------------------------------ jobs

    @property
    def busy(self) -> bool:
        """A job is running or waiting (files are being copied or moved)."""
        return self._current is not None or not self._q.empty()

    def start(self, media_ids: list[int], dest_root: str, mode: str, options: dict) -> int:
        """Check everything (same as preview), then queue the job."""
        pv = self.preview(media_ids, dest_root, mode, options, sample=0)
        if pv["errors"]:
            raise OrganizeError(pv["errors"][0])
        jid = self.db.conn.execute(
            "INSERT INTO organize_jobs(mode, dest_root, options, media_ids, created_at) VALUES (?, ?, ?, ?, ?)",
            (mode, dest_root, json.dumps(options), json.dumps(media_ids), int(time.time()))).lastrowid
        self._q.put(jid)
        return jid

    def cancel(self, jid: int) -> None:
        if self._current == jid:
            self._cancel.set()
        else:
            self.db.conn.execute("UPDATE organize_jobs SET status = 'cancelled', finished_at = ? "
                                 "WHERE id = ? AND status = 'queued'", (int(time.time()), jid))

    def _loop(self) -> None:
        while True:
            jid = self._q.get()
            try:
                self._run(jid)
            except Exception:
                log.exception("organize job %s crashed", jid)

    def _run(self, jid: int) -> None:
        conn = self.db.connect()
        try:
            job = conn.execute("SELECT * FROM organize_jobs WHERE id = ?", (jid,)).fetchone()
            if not job or job["status"] != "queued":
                return
            self._current = jid
            self._cancel.clear()
            mode, dest_root = job["mode"], job["dest_root"]
            o = parse_options(json.loads(job["options"]))
            p = planner.build(conn, json.loads(job["media_ids"]), dest_root, o, one_place_per_photo=(mode == "move"))
            errors, _ = self._check(conn, dest_root, mode, p)
            if errors:
                raise OrganizeError(errors[0])
            conn.execute("UPDATE organize_jobs SET status = 'running', planned = ? WHERE id = ?", (len(p.items), jid))
            if not os.path.isdir(long_path(dest_root)):
                os.makedirs(long_path(dest_root), exist_ok=True)
                conn.execute("UPDATE organize_jobs SET created_root = 1 WHERE id = ?", (jid,))
            source_id = None
            if mode == "move":
                n_before = conn.execute("SELECT max(id) FROM sources").fetchone()[0] or 0
                source_id = self._ensure_source(conn, dest_root)
                if source_id > n_before:
                    conn.execute("UPDATE organize_jobs SET created_source = ? WHERE id = ?", (source_id, jid))

            done = skipped = failed = nbytes = 0
            last = 0.0
            log_rows = []
            for k, it in enumerate(p.items, 1):
                if self._cancel.is_set():
                    break
                action, err = "skipped", None
                if it.status == "new":
                    try:
                        action = self._do(conn, mode, it, source_id)
                        done += 1
                        nbytes += it.size
                    except (OSError, OrganizeError) as e:
                        action, err = "failed", str(e)[:300]
                        failed += 1
                else:
                    skipped += 1
                log_rows.append((jid, it.media_id, it.src, it.dest, action, err))
                if time.monotonic() - last > 0.4 or k == len(p.items):
                    last = time.monotonic()
                    self._flush(conn, log_rows)
                    conn.execute("UPDATE organize_jobs SET progress = ?, done = ?, skipped = ?, failed = ?, bytes = ?, "
                                 "message = ? WHERE id = ?",
                                 (k / max(len(p.items), 1), done, skipped, failed, nbytes, f"{k} of {len(p.items)}", jid))
            self._flush(conn, log_rows)
            manifest = self._manifest(conn, jid, dest_root)
            status = "cancelled" if self._cancel.is_set() else "done"
            verb = {"copy": "Copied", "hardlink": "Linked", "symlink": "Linked", "move": "Moved"}[mode]
            msg = f"{verb} {done}, skipped {skipped} already there" + (f", {failed} failed" if failed else "")
            conn.execute("UPDATE organize_jobs SET status = ?, done = ?, skipped = ?, failed = ?, bytes = ?, "
                         "progress = 1, message = ?, manifest_path = ?, finished_at = ? WHERE id = ?",
                         (status, done, skipped, failed, nbytes, msg, manifest, int(time.time()), jid))
        except Exception as e:
            conn.execute("UPDATE organize_jobs SET status = 'error', message = ?, finished_at = ? WHERE id = ?",
                         (str(e)[:500], int(time.time()), jid))
        finally:
            self._current = None
            conn.close()

    @staticmethod
    def _flush(conn: sqlite3.Connection, rows: list) -> None:
        if rows:
            with transaction(conn):
                conn.executemany("INSERT INTO organize_items(job_id, media_id, src, dest, action, error) "
                                 "VALUES (?, ?, ?, ?, ?, ?)", rows)
            rows.clear()

    def _do(self, conn: sqlite3.Connection, mode: str, it: planner.PlanItem, source_id: int | None) -> str:
        src, dest = long_path(it.src), long_path(it.dest)
        if not os.path.isfile(src):
            raise OrganizeError("Original not found (moved or deleted since the scan)")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if mode == "copy":
            tmp = dest + ".pe-part"
            shutil.copy2(src, tmp)
            if os.path.getsize(tmp) != it.size:
                os.remove(tmp)
                raise OrganizeError("Copy incomplete")
            os.replace(tmp, dest)
            return "copied"
        if mode == "hardlink":
            os.link(src, dest)
            return "linked"
        if mode == "symlink":
            os.symlink(os.path.abspath(it.src), dest)
            return "linked"
        # move
        _move_file(src, dest, it.size)
        self._reindex(conn, it.media_id, it.dest, source_id)
        return "moved"

    def _ensure_source(self, conn: sqlite3.Connection, dest_root: str) -> int:
        for s in conn.execute("SELECT id, path FROM sources"):
            if _inside(dest_root, s["path"]):
                return s["id"]
        return conn.execute("INSERT INTO sources(path, label, added_at) VALUES (?, 'Organised', ?)",
                            (os.path.abspath(dest_root), int(time.time()))).lastrowid

    @staticmethod
    def _reindex(conn: sqlite3.Connection, media_id: int, new_path: str, source_id: int) -> None:
        """Point the index at the file's new place (keeps faces, albums, dates)."""
        folder = os.path.dirname(new_path)
        with transaction(conn):
            row = conn.execute("SELECT id FROM folders WHERE path = ?", (folder,)).fetchone()
            gen = conn.execute("SELECT scan_gen FROM sources WHERE id = ?", (source_id,)).fetchone()[0]
            fid = row[0] if row else conn.execute(
                "INSERT INTO folders(source_id, path, scan_gen) VALUES (?, ?, ?)", (source_id, folder, gen)).lastrowid
            name = os.path.basename(new_path)
            conn.execute("UPDATE media SET path = ?, filename = ?, ext = ?, folder_id = ?, source_id = ? WHERE id = ?",
                         (new_path, name, split_ext(name), fid, source_id, media_id))

    @staticmethod
    def _manifest(conn: sqlite3.Connection, jid: int, dest_root: str) -> str | None:
        path = os.path.join(dest_root, f"_PhotoExplorer report {jid} {datetime.now():%Y-%m-%d %H%M}.csv")
        try:
            with open(long_path(path), "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["action", "original", "destination", "error"])
                for r in conn.execute("SELECT action, src, dest, error FROM organize_items WHERE job_id = ?", (jid,)):
                    w.writerow(list(r))
            return path
        except OSError:
            return None

    # ------------------------------------------------------------ undo

    def undo(self, jid: int) -> dict:
        conn = self.db.conn
        job = conn.execute("SELECT * FROM organize_jobs WHERE id = ?", (jid,)).fetchone()
        if not job or job["status"] not in ("done", "cancelled") or job["undone_at"]:
            raise OrganizeError("This job cannot be undone")
        if self._current is not None:
            raise OrganizeError("Wait for the running export to finish")
        items = conn.execute("SELECT rowid, * FROM organize_items WHERE job_id = ? AND action IN "
                             "('copied', 'linked', 'moved') ORDER BY rowid DESC", (jid,)).fetchall()
        undone, failed, touched = 0, [], set()
        for it in items:
            try:
                dest, src = long_path(it["dest"]), long_path(it["src"])
                if it["action"] == "moved":
                    if os.path.exists(src):
                        raise OrganizeError("Something already exists at the original place")
                    if not os.path.isfile(dest):
                        raise OrganizeError("Not found where it was moved to")
                    sid = next((r[0] for r in conn.execute("SELECT id, path FROM sources ORDER BY length(path) DESC")
                                if _inside(it["src"], r[1])), None)
                    if sid is None:
                        raise OrganizeError("Original library folder no longer exists")
                    _move_file(dest, src, os.path.getsize(dest))
                    if it["media_id"] is not None:
                        self._reindex(conn, it["media_id"], it["src"], sid)
                else:
                    # A copy or link we made: only remove it if it is still what we put there.
                    if not os.path.lexists(dest):
                        pass
                    elif it["action"] == "linked" and os.path.islink(dest):
                        os.remove(dest)
                    else:
                        sha = conn.execute("SELECT sha256 FROM media WHERE id = ?", (it["media_id"],)).fetchone()
                        if not sha or file_sha256(it["dest"])[0] != sha[0]:
                            raise OrganizeError("File was changed after the export; left in place")
                        if dedup_actions.recycle_supported(it["dest"]):
                            dedup_actions._recycle(it["dest"])
                        else:
                            os.remove(dest)       # verified identical copy; the original is untouched
                conn.execute("UPDATE organize_items SET action = 'undone' WHERE rowid = ?", (it["rowid"],))
                touched.add(os.path.dirname(it["dest"]))
                undone += 1
            except (OSError, OrganizeError, dedup_actions.RemovalError) as e:
                failed.append({"file": it["dest"], "error": str(e)})
        root = job["dest_root"]
        if job["manifest_path"] and _inside(job["manifest_path"], root):
            try:
                os.remove(long_path(job["manifest_path"]))     # the report this job wrote
            except OSError:
                pass
        _remove_emptied_dirs(touched, root)
        if job["created_root"]:
            try:
                os.rmdir(long_path(root))                      # only if empty: the job made it
            except OSError:
                pass
        self._forget_empty_folders(conn, root, job["created_source"])
        conn.execute("UPDATE organize_jobs SET undone_at = ?, message = ? WHERE id = ?",
                     (int(time.time()), f"Undone: {undone} files" + (f", {len(failed)} left" if failed else ""), jid))
        return {"undone": undone, "failed": failed[:50]}

    @staticmethod
    def _forget_empty_folders(conn: sqlite3.Connection, root: str, created_source: int | None) -> None:
        """Drop index rows for folders under root that are gone and hold no media,
        and the library source the job added if nothing is left in it."""
        with transaction(conn):
            for f in conn.execute("SELECT id, path FROM folders").fetchall():
                if _inside(f["path"], root) and not os.path.isdir(long_path(f["path"])) and not conn.execute(
                        "SELECT 1 FROM media WHERE folder_id = ? LIMIT 1", (f["id"],)).fetchone():
                    conn.execute("DELETE FROM folders WHERE id = ?", (f["id"],))
            if created_source and not conn.execute(
                    "SELECT 1 FROM media WHERE source_id = ? LIMIT 1", (created_source,)).fetchone():
                conn.execute("DELETE FROM folders WHERE source_id = ?", (created_source,))
                conn.execute("DELETE FROM sources WHERE id = ?", (created_source,))


def _move_file(src: str, dest: str, size: int) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        os.rename(src, dest)
    except OSError:
        shutil.copy2(src, dest)                       # other drive: copy, verify, then remove
        if os.path.getsize(dest) != size:
            os.remove(dest)
            raise OrganizeError("Copy to the new place was incomplete; original left in place")
        os.remove(src)


def _remove_emptied_dirs(dirs: set[str], root: str) -> None:
    """Remove the folders an undo left empty, walking up to (not including) root.
    os.rmdir refuses non-empty folders, so nothing else can be removed."""
    for d in sorted(dirs, key=len, reverse=True):
        while _inside(d, root) and _norm(d) != _norm(root):
            try:
                os.rmdir(long_path(d))
            except OSError:
                break
            d = os.path.dirname(d)
