"""Save photo edits as a new file or in place of the original, and index them.

* copy     the edit goes next to the original as "<name> (edited).<ext>" (or into
           a chosen folder) and becomes its own photo in the library, linked to
           the original. Removing the original later leaves the copy alone.
* replace  the file itself is rewritten. The file as it was is kept under
           <data>/edit-backups, so "Restore original" can put it back.

Both keep the camera details, date and location: in the file (EXIF) and in
the library, where they are copied from the original's index entry.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from ..config import Settings
from ..db import Database, transaction
from ..organize.runner import _inside
from ..scanner import extract
from ..scanner.formats import long_path, split_ext
from ..scanner.manager import DONE, ERROR, PENDING, RESULT_COLUMNS, _UPDATE_RESULT_SQL
from . import ops as edit_ops
from .ops import EditError

log = logging.getLogger(__name__)

# What can be written back in the same format (RAW, GIF, BMP, AVIF are saved as JPEG copies).
WRITE_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "jpe": "JPEG", "jfif": "JPEG", "png": "PNG", "webp": "WEBP",
                 "tif": "TIFF", "tiff": "TIFF", "heic": "HEIF", "heif": "HEIF"}
KEEP_FROM_ORIGINAL = ("taken_at", "tz_offset", "date_source", "camera_make", "camera_model", "lens",
                      "gps_lat", "gps_lon", "gps_alt")
FULL_SIZE = 100_000


class EditManager:
    def __init__(self, db: Database, settings: Settings, faces_needed=lambda: None):
        self.db, self.s = db, settings
        self._faces_needed = faces_needed
        self._worker_ready = False
        self._lock = threading.Lock()
        self._cache: OrderedDict = OrderedDict()    # (media_id, sha, size) -> preview base image
        self._batch: dict = {"state": "idle", "done": 0, "total": 0, "failed": [], "created": [], "message": ""}
        self._batch_cancel = threading.Event()

    # ------------------------------------------------------------ helpers

    def _row(self, conn, media_id: int):
        r = conn.execute("SELECT m.*, s.path AS source_path FROM media m JOIN sources s ON s.id = m.source_id "
                         "WHERE m.id = ? AND m.status = 1", (media_id,)).fetchone()
        if not r:
            raise EditError("Photo not found (moved or not indexed yet?)")
        if r["kind"] == "video":
            raise EditError("Videos can't be edited")
        return r

    def _faces(self, conn, media_id: int) -> list[dict]:
        return [dict(f) for f in conn.execute("SELECT x, y, w, h FROM faces WHERE media_id = ?", (media_id,))]

    def can_replace(self, row) -> bool:
        return row["kind"] == "image" and row["ext"] in WRITE_FORMATS

    # ------------------------------------------------------------ preview

    def preview(self, media_id: int, raw_ops: dict, size: int = 1400) -> bytes:
        o = edit_ops.normalize(raw_ops)
        conn = self.db.conn
        r = self._row(conn, media_id)
        key = (media_id, r["sha256"], size)
        with self._lock:
            base = self._cache.get(key)
            if base is not None:
                self._cache.move_to_end(key)
        if base is None:
            base = extract.load_rgb(r["path"], r["kind"], size)
            with self._lock:
                self._cache[key] = base
                while len(self._cache) > 6:
                    self._cache.popitem(last=False)
        # resize and frame are relative to the full photo: scale them to the preview
        o = dict(o)
        full = max(r["width"] or base.width, r["height"] or base.height)
        if o["resize"]:
            o["resize"] = max(64, round(o["resize"] * max(base.size) / full)) if o["resize"] < full else 0
        img = edit_ops.apply(base.copy(), o, self._faces(conn, media_id))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return buf.getvalue()

    # ------------------------------------------------------------ save

    def save(self, media_id: int, raw_ops: dict, mode: str, dest_dir: str | None = None) -> dict:
        if mode not in ("copy", "replace"):
            raise EditError("mode must be copy or replace")
        o = edit_ops.normalize(raw_ops)
        if edit_ops.is_empty(o):
            raise EditError("Nothing to save: no changes")
        conn = self.db.conn
        r = self._row(conn, media_id)
        if mode == "replace" and not self.can_replace(r):
            raise EditError(f".{r['ext']} files can't be rewritten; save a copy instead (as JPEG)")
        if not os.path.isfile(long_path(r["path"])):
            raise EditError("The original file is missing on disk")

        img, exif, icc = self._load_full(r)
        out = edit_ops.apply(img, o, self._faces(conn, media_id))
        exif = self._fill_exif(exif, r)
        if mode == "replace":
            res = self._replace(conn, r, out, exif, icc, o)
        else:
            res = self._copy(conn, r, out, exif, icc, o, dest_dir)
        self._faces_needed()
        return res

    def _load_full(self, r):
        exif, icc = Image.Exif(), None
        if r["kind"] == "image":
            try:
                with Image.open(long_path(r["path"])) as im:
                    exif = im.getexif()
                    icc = im.info.get("icc_profile")
            except Exception:
                pass
        return extract.load_rgb(r["path"], r["kind"], FULL_SIZE), exif, icc

    @staticmethod
    def _fill_exif(exif: Image.Exif, r) -> Image.Exif:
        """Pixels are saved upright, so orientation becomes 1. Details the file lacked
        but the library knows (e.g. a RAW's camera, a date read from the file name)
        are written in, so they stay with the photo wherever it goes."""
        exif[0x0112] = 1
        if r["camera_make"] and not exif.get(0x010F):
            exif[0x010F] = r["camera_make"]
        if r["camera_model"] and not exif.get(0x0110):
            exif[0x0110] = r["camera_model"]
        ifd = exif.get_ifd(0x8769)
        if r["taken_at"] and not ifd.get(0x9003):
            ifd[0x9003] = datetime.fromtimestamp(r["taken_at"], tz=timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
            if r["tz_offset"]:
                ifd[0x9011] = r["tz_offset"]
        if r["gps_lat"] is not None and r["gps_lon"] is not None and not exif.get_ifd(0x8825):
            gps = exif.get_ifd(0x8825)

            def dms(v: float):
                v = abs(v)
                d, m = int(v), int((v - int(v)) * 60)
                return (IFDRational(d, 1), IFDRational(m, 1), IFDRational(round((v - d - m / 60) * 3600 * 1000), 1000))
            gps[1], gps[2] = ("N" if r["gps_lat"] >= 0 else "S"), dms(r["gps_lat"])
            gps[3], gps[4] = ("E" if r["gps_lon"] >= 0 else "W"), dms(r["gps_lon"])
        return exif

    @staticmethod
    def _write(img: Image.Image, path: str, fmt: str, exif: Image.Exif, icc) -> None:
        kw: dict = {"exif": exif.tobytes()}
        if icc:
            kw["icc_profile"] = icc
        if fmt == "JPEG":
            kw.update(quality=95, subsampling=0, optimize=True)
        elif fmt == "WEBP":
            kw.update(quality=95, method=4)
        elif fmt == "HEIF":
            import pillow_heif
            pillow_heif.register_heif_opener()
            kw.update(quality=92)
        elif fmt == "TIFF":
            kw.update(compression="tiff_lzw")
        img.save(long_path(path), fmt, **kw)

    def _index(self, conn, media_id: int, original) -> None:
        """Read the new file like a scan would, then keep the original's details."""
        if not self._worker_ready:
            extract.init_worker(str(self.s.thumbs_dir), self.s.thumb_size, self.s.ffmpeg, self.s.ffprobe,
                                low_priority=False)
            self._worker_ready = True
        m = conn.execute("SELECT path, kind, mtime_ns FROM media WHERE id = ?", (media_id,)).fetchone()
        res = extract.process((media_id, m["path"], m["kind"], m["mtime_ns"]))
        row = {c: res.get(c) for c in RESULT_COLUMNS}
        row["hash_sampled"] = row["hash_sampled"] or 0
        row["has_thumb"] = row["has_thumb"] or 0
        row.update(id=media_id, now=int(time.time()), error=res.get("error"),
                   status=ERROR if res.get("error") else DONE)
        for c in KEEP_FROM_ORIGINAL:
            if original[c] is not None:
                row[c] = original[c]
        conn.execute(_UPDATE_RESULT_SQL, row)

    def _replace(self, conn, r, img, exif, icc, o) -> dict:
        path = r["path"]
        st = os.stat(long_path(path))
        backup_dir = self.s.data_dir / "edit-backups" / str(r["id"])
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{int(time.time() * 1000)}_{r['filename']}"
        shutil.copy2(long_path(path), backup)
        folder, name = os.path.split(path)
        tmp = os.path.join(folder, f".{name}.pe-edit.tmp")
        try:
            self._write(img, tmp, WRITE_FORMATS[r["ext"]], exif, icc)
            os.utime(long_path(tmp), ns=(st.st_atime_ns, st.st_mtime_ns))   # keeps "date modified" too
            os.replace(long_path(tmp), long_path(path))
        except BaseException:
            try:
                os.remove(long_path(tmp))
            except OSError:
                pass
            backup.unlink(missing_ok=True)
            raise
        self._after_rewrite(conn, r, edit_ops.changes_geometry(o))
        with transaction(conn):
            conn.execute("INSERT INTO media_edits(edited_id, original_id, mode, ops, backup_path, created_at) "
                         "VALUES (?, ?, 'replace', ?, ?, ?)", (r["id"], r["id"], json.dumps(o), str(backup), int(time.time())))
        return {"media_id": r["id"], "mode": "replace", "path": path}

    def _after_rewrite(self, conn, r, geometry_changed: bool) -> None:
        st = os.stat(long_path(r["path"]))
        with transaction(conn):
            conn.execute(f"UPDATE media SET size = ?, mtime_ns = ?, status = {PENDING} WHERE id = ?",
                         (st.st_size, st.st_mtime_ns, r["id"]))
            self._index(conn, r["id"], r)
            if geometry_changed:   # face boxes no longer fit: find them again
                conn.execute("DELETE FROM faces WHERE media_id = ?", (r["id"],))
                conn.execute("UPDATE media SET faces_status = 0 WHERE id = ?", (r["id"],))

    def _copy(self, conn, r, img, exif, icc, o, dest_dir: str | None) -> dict:
        folder = os.path.abspath(dest_dir) if dest_dir else os.path.dirname(r["path"])
        if not os.path.isdir(long_path(folder)):
            raise EditError(f"Folder not found: {folder}")
        out_ext = r["ext"] if r["kind"] == "image" and r["ext"] in WRITE_FORMATS else "jpg"
        stem = r["filename"][: -(len(r["ext"]) + 1)] if r["ext"] else r["filename"]
        n, dest = 1, ""
        while True:
            label = "edited" if n == 1 else f"edited {n}"
            dest = os.path.join(folder, f"{stem} ({label}).{out_ext}")
            if not os.path.exists(long_path(dest)):
                break
            n += 1
        st = os.stat(long_path(r["path"]))
        self._write(img, dest, WRITE_FORMATS[out_ext], exif, icc)
        os.utime(long_path(dest), ns=(st.st_atime_ns, st.st_mtime_ns))
        try:
            new_id = self._add_to_library(conn, r, dest, o)
        except BaseException:
            os.remove(long_path(dest))
            raise
        return {"media_id": new_id, "mode": "copy", "path": dest}

    def _add_to_library(self, conn, r, dest: str, o) -> int:
        folder, name = os.path.split(dest)
        st = os.stat(long_path(dest))
        with transaction(conn):
            src = None
            for s in conn.execute("SELECT id, path FROM sources ORDER BY length(path) DESC"):
                if _inside(folder, s["path"]):
                    src = s["id"]
                    break
            if src is None:     # a folder outside the library: add it, so the copy shows up
                src = conn.execute("INSERT INTO sources(path, label, added_at) VALUES (?, 'Edited', ?)",
                                   (folder, int(time.time()))).lastrowid
            row = conn.execute("SELECT id FROM folders WHERE path = ?", (folder,)).fetchone()
            gen = conn.execute("SELECT scan_gen FROM sources WHERE id = ?", (src,)).fetchone()[0]
            fid = row[0] if row else conn.execute(
                "INSERT INTO folders(source_id, path, scan_gen) VALUES (?, ?, ?)", (src, folder, gen)).lastrowid
            new_id = conn.execute(
                "INSERT INTO media(source_id, folder_id, path, filename, ext, kind, size, mtime_ns, added_at) "
                "VALUES (?, ?, ?, ?, ?, 'image', ?, ?, ?)",
                (src, fid, dest, name, split_ext(name), st.st_size, st.st_mtime_ns, int(time.time()))).lastrowid
            self._index(conn, new_id, r)
            if not edit_ops.changes_geometry(o):
                # same framing: the original's faces (and names) fit the copy as they are
                conn.execute(
                    "INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model, "
                    "chip, cluster_id, person_id, assigned_by) SELECT ?, ordinal, x, y, w, h, score, sharpness, "
                    "size_px, embedding, model, chip, cluster_id, person_id, assigned_by FROM faces WHERE media_id = ?",
                    (new_id, r["id"]))
                conn.execute("UPDATE media SET faces_status = ? WHERE id = ?", (r["faces_status"], new_id))
            conn.execute("INSERT INTO media_edits(edited_id, original_id, mode, ops, created_at) VALUES (?, ?, 'copy', ?, ?)",
                         (new_id, r["id"], json.dumps(o), int(time.time())))
            # an edit looks like its original: never offer one as the other's duplicate
            conn.execute("INSERT OR IGNORE INTO dup_ignored(a, b) VALUES (?, ?)", (min(r["id"], new_id), max(r["id"], new_id)))
        return new_id

    # ------------------------------------------------------------ restore / links

    def info(self, media_id: int) -> dict:
        conn = self.db.conn
        r = conn.execute("SELECT kind, ext FROM media WHERE id = ?", (media_id,)).fetchone()
        of = conn.execute("SELECT e.original_id id, m.filename, m.status FROM media_edits e LEFT JOIN media m "
                          "ON m.id = e.original_id WHERE e.edited_id = ? AND e.mode = 'copy'", (media_id,)).fetchone()
        copies = [dict(x) for x in conn.execute(
            "SELECT e.edited_id id, m.filename FROM media_edits e JOIN media m ON m.id = e.edited_id "
            "WHERE e.original_id = ? AND e.mode = 'copy' AND m.status = 1 ORDER BY e.id", (media_id,))]
        restore = conn.execute("SELECT id, created_at FROM media_edits WHERE edited_id = ? AND mode = 'replace' "
                               "AND restored_at IS NULL ORDER BY id DESC LIMIT 1", (media_id,)).fetchone()
        return {
            "editable": bool(r) and r["kind"] != "video",
            "can_replace": bool(r) and r["kind"] == "image" and r["ext"] in WRITE_FORMATS,
            "edit_of": dict(of) if of and of["status"] == 1 else None,
            "edited_copies": copies,
            "can_restore": bool(restore),
            "edited_at": restore["created_at"] if restore else None,
        }

    def restore(self, media_id: int) -> dict:
        conn = self.db.conn
        e = conn.execute("SELECT * FROM media_edits WHERE edited_id = ? AND mode = 'replace' AND restored_at IS NULL "
                         "ORDER BY id DESC LIMIT 1", (media_id,)).fetchone()
        if not e:
            raise EditError("No earlier version of this photo is kept")
        if not os.path.isfile(e["backup_path"]):
            raise EditError("The saved original is missing: " + e["backup_path"])
        r = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        folder, name = os.path.split(r["path"])
        tmp = os.path.join(folder, f".{name}.pe-restore.tmp")
        shutil.copy2(e["backup_path"], long_path(tmp))
        os.replace(long_path(tmp), long_path(r["path"]))
        self._after_rewrite(conn, r, edit_ops.changes_geometry(json.loads(e["ops"])))
        with transaction(conn):
            conn.execute("UPDATE media_edits SET restored_at = ? WHERE id = ?", (int(time.time()), e["id"]))
        os.remove(e["backup_path"])
        self._faces_needed()
        return {"media_id": media_id}

    # ------------------------------------------------------------ batch

    def batch_status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._batch))

    def batch_start(self, media_ids: list[int], raw_ops: dict, mode: str, dest_dir: str | None) -> None:
        o = edit_ops.normalize(raw_ops)
        if o["crop"] or o["redeye"] or o["straighten"]:
            raise EditError("Crop, straighten and red-eye are for one photo at a time")
        if edit_ops.is_empty(o):
            raise EditError("Nothing to apply: no changes")
        if mode not in ("copy", "replace"):
            raise EditError("mode must be copy or replace")
        with self._lock:
            if self._batch["state"] == "running":
                raise EditError("Another batch edit is running")
            self._batch = {"state": "running", "done": 0, "total": len(media_ids), "failed": [], "created": [],
                           "message": f"Editing {len(media_ids)} photos"}
        self._batch_cancel.clear()
        threading.Thread(target=self._run_batch, args=(list(media_ids), o, mode, dest_dir),
                         name="batch-edit", daemon=True).start()

    def batch_cancel(self) -> None:
        self._batch_cancel.set()

    def _run_batch(self, ids: list[int], o: dict, mode: str, dest_dir: str | None) -> None:
        for mid in ids:
            if self._batch_cancel.is_set():
                break
            try:
                conn = self.db.conn
                m = mode
                if mode == "replace" and not self.can_replace(self._row(conn, mid)):
                    m = "copy"     # e.g. RAW: a JPEG copy instead
                res = self.save(mid, o, m, dest_dir)
                with self._lock:
                    if res["mode"] == "copy":
                        self._batch["created"].append(res["media_id"])
            except Exception as e:
                log.warning("batch edit of %s failed: %s", mid, e)
                with self._lock:
                    self._batch["failed"].append({"id": mid, "error": str(e)[:200]})
            with self._lock:
                self._batch["done"] += 1
        with self._lock:
            b = self._batch
            b["state"] = "cancelled" if self._batch_cancel.is_set() else "done"
            b["message"] = f"Edited {b['done'] - len(b['failed'])} of {b['total']}" + \
                (f", {len(b['failed'])} failed" if b["failed"] else "")
