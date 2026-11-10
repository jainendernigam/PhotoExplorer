"""Import photos the user picks in Google Photos (Photos Picker API).

Flow: create a picker session -> open Google's picker in the browser -> poll
until the user has chosen -> download the originals into a local folder
(YYYY/MM by Google's creation time) -> make sure that folder is a library
source -> scan it -> tag the rows as coming from Google Photos.

Already-imported items (by Google's id) are not downloaded again.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..db import Database, transaction
from ..scanner.manager import ScanManager
from .oauth import GoogleAuth, GoogleError, http_json


def default_import_dir() -> Path:
    return Path.home() / "Pictures" / "Google Photos import"


def _seconds(duration: str | None, default: float) -> float:
    try:
        return float(str(duration).rstrip("s"))
    except (TypeError, ValueError):
        return default


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .") or "photo"


class Importer:
    API = "https://photospicker.googleapis.com/v1"

    def __init__(self, db: Database, settings: Settings, auth: GoogleAuth, scanner: ScanManager,
                 open_browser: Callable[[str], object] = webbrowser.open, api: str | None = None):
        self.db, self.s, self.auth, self.scanner = db, settings, auth, scanner
        self.open_browser = open_browser
        if api:
            self.API = api
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = {"state": "idle", "message": None, "picked": 0, "downloaded": 0,
                      "skipped": 0, "failed": 0, "picker_uri": None, "folder": None}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, folder: str | None = None) -> dict:
        if self.running:
            raise GoogleError("An import is already running")
        dest = Path(folder) if folder else default_import_dir()
        if not dest.is_absolute():
            raise GoogleError("Choose a full folder path")
        session = http_json(f"{self.API}/sessions", {}, token=self.auth.access_token())
        self._cancel.clear()
        self.state = {"state": "waiting", "message": "Choose photos in the Google Photos window",
                      "picked": 0, "downloaded": 0, "skipped": 0, "failed": 0,
                      "picker_uri": session["pickerUri"], "folder": str(dest)}
        self.open_browser(session["pickerUri"].rstrip("/") + "/autoclose")
        self._thread = threading.Thread(target=self._run, args=(session, dest), daemon=True, name="gphotos")
        self._thread.start()
        return self.state

    def cancel(self) -> None:
        self._cancel.set()

    # ------------------------------------------------------------ worker

    def _run(self, session: dict, dest: Path) -> None:
        sid = session["id"]
        final: dict = {}
        try:
            poll = _seconds(session.get("pollingConfig", {}).get("pollInterval"), 5)
            deadline = time.time() + _seconds(session.get("pollingConfig", {}).get("timeoutIn"), 1800)
            while True:                                   # wait for the user to finish picking
                if self._cancel.wait(min(max(poll, 0.05), 30)):
                    raise _Stop("Cancelled")
                if time.time() > deadline:
                    raise _Stop("Timed out waiting for photos to be picked")
                if http_json(f"{self.API}/sessions/{sid}", token=self.auth.access_token()).get("mediaItemsSet"):
                    break

            items, page = [], None
            while True:
                url = f"{self.API}/mediaItems?sessionId={sid}&pageSize=100" + (f"&pageToken={page}" if page else "")
                r = http_json(url, token=self.auth.access_token())
                items += r.get("mediaItems", [])
                page = r.get("nextPageToken")
                if not page:
                    break
            self.state.update(state="downloading", picked=len(items), message=f"Downloading {len(items)} items")

            conn = self.db.connect()
            try:
                for it in items:
                    if self._cancel.is_set():
                        raise _Stop("Cancelled")
                    try:
                        if conn.execute("SELECT 1 FROM gphotos_items WHERE remote_id = ?", (it["id"],)).fetchone():
                            self.state["skipped"] += 1
                            continue
                        self._download(conn, it, dest)
                        self.state["downloaded"] += 1
                    except (GoogleError, OSError, KeyError) as e:
                        self.state["failed"] += 1
                        self.state["message"] = f"Some items failed: {e}"
                source_id = self._ensure_source(conn, dest)
                self._scan(source_id)
                with transaction(conn):
                    conn.execute("UPDATE media SET origin = 'gphotos', remote_id = "
                                 "(SELECT g.remote_id FROM gphotos_items g WHERE g.path = media.path) "
                                 "WHERE path IN (SELECT path FROM gphotos_items)")
            finally:
                conn.close()
            st = self.state
            final = dict(state="done", message=f"Imported {st['downloaded']} new, {st['skipped']} already here"
                         + (f", {st['failed']} failed" if st["failed"] else ""))
        except _Stop as e:
            final = dict(state="cancelled", message=str(e))
        except Exception as e:
            final = dict(state="error", message=str(e)[:300])
        finally:
            try:   # close Google's picker session before reporting the end state
                http_json(f"{self.API}/sessions/{sid}", token=self.auth.access_token(), method="DELETE")
            except Exception:
                pass
            self.state.update(final or dict(state="error", message="Stopped unexpectedly"))

    def _download(self, conn: sqlite3.Connection, it: dict, dest: Path) -> None:
        mf = it["mediaFile"]
        created = it.get("createTime") or ""
        try:
            when = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            when = datetime.now()
        folder = dest / f"{when:%Y}" / f"{when:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        name = _safe(mf.get("filename") or f"{it['id']}.jpg")
        target = folder / name
        n = 1
        while target.exists():
            target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        suffix = "=dv" if it.get("type") == "VIDEO" else "=d"     # original bytes (with EXIF)
        req = urllib.request.Request(mf["baseUrl"] + suffix,
                                     headers={"Authorization": f"Bearer {self.auth.access_token()}"})
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        except urllib.error.URLError as e:
            tmp.unlink(missing_ok=True)
            raise GoogleError(f"Download failed: {e}") from None
        os.replace(tmp, target)
        ts = when.timestamp()
        os.utime(target, (ts, ts))     # file date = Google's date, if the photo has no EXIF date
        with transaction(conn):
            conn.execute("INSERT INTO gphotos_items(remote_id, path, create_time, mime_type, downloaded_at) "
                         "VALUES (?, ?, ?, ?, ?)", (it["id"], str(target), created, mf.get("mimeType"), int(time.time())))

    def _ensure_source(self, conn: sqlite3.Connection, dest: Path) -> int:
        d = os.path.normcase(str(dest.resolve()))
        for sid, path in conn.execute("SELECT id, path FROM sources"):
            p = os.path.normcase(path)
            if d == p or d.startswith(p.rstrip("\\/") + os.sep):
                return sid                       # already inside a library folder
            if p.startswith(d.rstrip("\\/") + os.sep):
                raise GoogleError(f"The import folder contains an existing source ({path}); choose another")
        return conn.execute("INSERT INTO sources(path, label, added_at) VALUES (?, 'Google Photos', ?)",
                            (str(dest.resolve()), int(time.time()))).lastrowid

    def _scan(self, source_id: int) -> None:
        self.state.update(state="scanning", message="Adding the photos to the library")
        while not self.scanner.start([source_id]):     # another scan is running: wait for it
            if self._cancel.wait(2):
                raise _Stop("Cancelled")
        self.scanner.wait()


class _Stop(Exception):
    pass
