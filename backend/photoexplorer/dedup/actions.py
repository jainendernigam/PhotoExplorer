"""Moving redundant files away - safely, and undoably.

Methods
* archive (default): move into ``<source root>/.photoexplorer-archive/<same
  relative path>``. Same drive = an instant rename; the scanner skips dot
  folders, and Undo moves the file back.
* recycle: Windows Recycle Bin, only on local fixed drives (on network shares
  and most USB drives Windows would delete permanently instead).

Nothing is ever deleted permanently. Before each move the file must still
match the index (size and modified time) and, for exact copies, its bytes are
hashed again and must equal a copy that is being kept. A group can never lose
all of its members.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
import uuid

from ..db import transaction
from ..scanner.formats import long_path
from ..scanner.hashing import file_sha256

ARCHIVE_DIR = ".photoexplorer-archive"
DONE, REMOVED, HIDDEN = 1, 4, 5


class RemovalError(Exception):
    pass


# ---------------------------------------------------------------- recycle bin (Windows)

def _drive_is_fixed(path: str) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    return ctypes.windll.kernel32.GetDriveTypeW(root) == 3  # DRIVE_FIXED


def recycle_supported(path: str) -> bool:
    return sys.platform == "win32" and _drive_is_fixed(path)


def _recycle(path: str) -> None:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_WANTNUKEWARNING = 0x4, 0x10, 0x40, 0x4000
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0"   # double-NUL terminated list
    # ALLOWUNDO = Recycle Bin. WANTNUKEWARNING: if Windows cannot recycle it,
    # it must ask instead of deleting permanently.
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_WANTNUKEWARNING
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise RemovalError(f"Recycle Bin refused the file (code {rc})")


# ---------------------------------------------------------------- archive

def _archive_target(source_root: str, path: str) -> str:
    rel = os.path.relpath(path, source_root)
    dest = os.path.join(source_root, ARCHIVE_DIR, rel)
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(long_path(dest)):
        dest = f"{base} ({n}){ext}"
        n += 1
    return dest


def _move(src: str, dest: str) -> None:
    os.makedirs(long_path(os.path.dirname(dest)), exist_ok=True)
    try:
        os.rename(long_path(src), long_path(dest))      # same drive: instant, atomic
    except OSError:
        size = os.path.getsize(long_path(src))
        shutil.copy2(long_path(src), long_path(dest))   # other drive: copy, verify, then remove
        if os.path.getsize(long_path(dest)) != size:
            os.remove(long_path(dest))
            raise RemovalError("Copy to the archive was incomplete; original left in place")
        os.remove(long_path(src))


# ---------------------------------------------------------------- public

def remove(conn: sqlite3.Connection, group_id: int, remove_ids: list[int], method: str = "archive",
           batch: str | None = None) -> dict:
    """Move ``remove_ids`` (members of ``group_id``) away. Returns per-file results."""
    if method not in ("archive", "recycle", "app"):
        raise RemovalError("method must be 'archive', 'recycle' or 'app'")
    g = conn.execute("SELECT kind FROM dup_groups WHERE id = ?", (group_id,)).fetchone()
    if not g:
        raise RemovalError("Group not found (run Find duplicates again)")
    members = {r["media_id"]: r for r in conn.execute(
        "SELECT dm.media_id, m.path, m.size, m.mtime_ns, m.sha256, m.status, s.path AS source_root "
        "FROM dup_members dm JOIN media m ON m.id = dm.media_id JOIN sources s ON s.id = m.source_id "
        "WHERE dm.group_id = ?", (group_id,))}
    remove_ids = list(dict.fromkeys(remove_ids))
    if not remove_ids:
        return {"removed": [], "failed": []}
    if any(i not in members for i in remove_ids):
        raise RemovalError("Some files are not in this group")
    keep = [members[i] for i in members if i not in remove_ids and members[i]["status"] == DONE
            and os.path.isfile(long_path(members[i]["path"]))]
    if not keep:
        raise RemovalError("At least one photo of the group must be kept (and still be on disk)")
    keep_hashes = {k["sha256"] for k in keep}

    batch = batch or uuid.uuid4().hex[:12]
    done, failed = [], []
    for mid in remove_ids:
        m = members[mid]
        try:
            if m["status"] != DONE:
                raise RemovalError("Already removed or missing")
            if method == "app":  # the file stays where it is; the app just stops showing it
                with transaction(conn):
                    conn.execute(f"UPDATE media SET status = {HIDDEN} WHERE id = ?", (mid,))
                    conn.execute(
                        "INSERT INTO removals(media_id, original_path, archived_path, method, size, reason, batch, removed_at) "
                        "VALUES (?, ?, NULL, 'app', ?, ?, ?, ?)",
                        (mid, m["path"], m["size"], g["kind"], batch, int(time.time())))
                done.append(mid)
                continue
            lp = long_path(m["path"])
            st = os.stat(lp)
            if st.st_size != m["size"] or st.st_mtime_ns != m["mtime_ns"]:
                raise RemovalError("File changed since it was scanned; rescan first")
            if g["kind"] == "exact":
                digest, _ = file_sha256(m["path"])
                if digest != m["sha256"] or digest not in keep_hashes:
                    raise RemovalError("Bytes no longer match the kept copy; rescan first")
            if method == "recycle":
                if not recycle_supported(m["path"]):
                    raise RemovalError("Recycle Bin is only used on local drives; use Archive here")
                _recycle(m["path"])
                dest = None
            else:
                dest = _archive_target(m["source_root"], m["path"])
                _move(m["path"], dest)
            with transaction(conn):
                conn.execute(f"UPDATE media SET status = {REMOVED} WHERE id = ?", (mid,))
                conn.execute(
                    "INSERT INTO removals(media_id, original_path, archived_path, method, size, reason, batch, removed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (mid, m["path"], dest, method, m["size"], g["kind"], batch, int(time.time())))
            done.append(mid)
        except (OSError, RemovalError) as e:
            failed.append({"id": mid, "error": str(e)})

    # The group is settled once only one member is left.
    left = conn.execute(f"SELECT count(*) FROM dup_members dm JOIN media m ON m.id = dm.media_id "
                        f"WHERE dm.group_id = ? AND m.status = {DONE}", (group_id,)).fetchone()[0]
    if left <= 1:
        with transaction(conn):
            conn.execute("DELETE FROM dup_groups WHERE id = ?", (group_id,))
    return {"batch": batch, "removed": done, "failed": failed}


def undo(conn: sqlite3.Connection, removal_ids: list[int]) -> dict:
    """Put archived files back where they were."""
    restored, failed = [], []
    for rid in removal_ids:
        r = conn.execute("SELECT * FROM removals WHERE id = ?", (rid,)).fetchone()
        try:
            if r is None or r["restored_at"]:
                raise RemovalError("Nothing to undo")
            if r["method"] == "recycle":
                raise RemovalError("Restore it from the Windows Recycle Bin, then rescan")
            if r["method"] == "app":
                with transaction(conn):
                    conn.execute("UPDATE removals SET restored_at = ? WHERE id = ?", (int(time.time()), rid))
                    if r["media_id"] is not None:
                        conn.execute(f"UPDATE media SET status = {DONE} WHERE id = ? AND status = {HIDDEN}",
                                     (r["media_id"],))
                restored.append(rid)
                continue
            if os.path.exists(long_path(r["original_path"])):
                raise RemovalError("A file already exists at the original location")
            if not os.path.isfile(long_path(r["archived_path"])):
                raise RemovalError("Archived file not found")
            _move(r["archived_path"], r["original_path"])
            st = os.stat(long_path(r["original_path"]))
            with transaction(conn):
                conn.execute("UPDATE removals SET restored_at = ? WHERE id = ?", (int(time.time()), rid))
                if r["media_id"] is not None:
                    conn.execute(f"UPDATE media SET status = {DONE}, mtime_ns = ? WHERE id = ?",
                                 (st.st_mtime_ns, r["media_id"]))
            restored.append(rid)
        except (OSError, RemovalError) as e:
            failed.append({"id": rid, "error": str(e)})
    return {"restored": restored, "failed": failed}


def ignore_group(conn: sqlite3.Connection, group_id: int) -> None:
    """User says: not duplicates. Remember every pair; drop the group."""
    ids = [r[0] for r in conn.execute("SELECT media_id FROM dup_members WHERE group_id = ?", (group_id,))]
    with transaction(conn):
        conn.executemany("INSERT OR IGNORE INTO dup_ignored(a, b) VALUES (?, ?)",
                         [(min(a, b), max(a, b)) for i, a in enumerate(ids) for b in ids[i + 1:]])
        conn.execute("DELETE FROM dup_groups WHERE id = ?", (group_id,))
