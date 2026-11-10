"""Update software: new app versions, theme packs and music packs.

Nothing is contacted until the user clicks "Check for updates". The app reads
one small manifest.json from the update address (by default the latest
GitHub release of the project):

    {"format": 1,
     "app":   {"version": "0.2.0", "url": "PhotoExplorer-0.2.0-windows-portable.zip",
               "sha256": "...", "size": 123, "notes": "What's new"},
     "packs": [{"id": "music-1", "kind": "music", "version": 1, "title": "Music pack 1",
                "description": "...", "url": "music-1.zip", "sha256": "...", "size": 123}]}

URLs may be relative to the manifest. Every download is checked against its
SHA-256 before it is used.

* Packs unzip into <data>/content/<id> (only data files: json, audio, images).
* An app update unzips into <data>/update/<version>; "Restart to update" then
  runs a small script that waits for the app to close, copies the new files
  over the app folder (never touching data/), and starts the app again.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__
from .config import app_root
from .content import Content

# The project's GitHub releases; "latest/download" always points at the newest release.
DEFAULT_MANIFEST_URL = "https://github.com/jainendernigam/PhotoExplorer/releases/latest/download/manifest.json"
PACK_EXTS = {".json", ".txt", ".md", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".png", ".jpg", ".jpeg", ".webp"}
MAX_PACK = 500 * 1024 * 1024
MAX_APP = 1536 * 1024 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DETACHED = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW


class UpdateError(Exception):
    pass


def app_folder_problem(root: Path | None = None) -> str | None:
    """Why this copy's own files can't be replaced by an app update, or None if they can."""
    root = root or app_root()
    if sys.platform != "win32":
        return "Automatic app updates are only available on Windows for now."
    if not (root / "PhotoExplorer.exe").is_file():
        return "App updates work only in the packaged app (PhotoExplorer.exe)."
    for d in (root, root / "backend", root / "runtime"):
        try:
            fd, probe = tempfile.mkstemp(dir=d, prefix=".pe-write-test-")
            os.close(fd)
            os.unlink(probe)
        except OSError:
            return (f"Windows does not allow changes in {root} without admin rights, so the app can't update itself there. "
                    "Close Photo Explorer, move the whole PhotoExplorer folder to Documents, the Desktop or another drive, "
                    "and update from there. Themes and music still install here.")
    return None


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:4]) or (0,)


def _allowed_url(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))


def _safe_members(z: zipfile.ZipFile, prefix: str = "", exts: set[str] | None = None) -> list[zipfile.ZipInfo]:
    out = []
    for m in z.infolist():
        p = PurePosixPath(m.filename.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]):
            raise UpdateError(f"Unsafe path in download: {m.filename}")
        if prefix and (not p.parts or p.parts[0] != prefix):
            raise UpdateError(f"Unexpected file in download: {m.filename}")
        if m.is_dir():
            continue
        if exts is not None and p.suffix.lower() not in exts:
            raise UpdateError(f"File type not allowed in a pack: {m.filename}")
        out.append(m)
    return out


class Updater:
    def __init__(self, data_dir: Path, content: Content):
        self.data_dir = data_dir
        self.content = content
        self.cfg = data_dir / "update.json"
        self.stage = data_dir / "update"
        self.manifest: dict | None = None
        self.manifest_base = ""
        self.checked_at: float | None = None
        self.app_ready: str | None = None
        self.job = {"state": "idle", "message": "", "item": None, "done": 0, "total": 0,
                    "bytes": 0, "bytes_total": 0, "failed": []}
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        # A previous app update has run (or was abandoned): note how it went, then clear its files.
        self.last_update: dict | None = None
        try:
            log = (self.stage / "apply.log").read_text(encoding="mbcs" if sys.platform == "win32" else "utf-8", errors="replace")
            ok = any(line.strip() == "Done" for line in log.splitlines())
            self.last_update = {"ok": ok, "message": f"Updated to Photo Explorer {__version__}." if ok else
                                "The last app update could not replace all files; this copy may still be the older version. "
                                "Download the zip from the releases page and unzip it over this folder (keep its data folder)."}
        except OSError:
            pass
        shutil.rmtree(self.stage, ignore_errors=True)

    # ------------------------------------------------------------ address

    def manifest_url(self) -> str:
        env = os.environ.get("PE_UPDATE_URL")
        if env:
            return env
        try:
            return json.loads(self.cfg.read_text(encoding="utf-8")).get("manifest_url") or DEFAULT_MANIFEST_URL
        except (OSError, ValueError):
            return DEFAULT_MANIFEST_URL

    def set_manifest_url(self, url: str) -> None:
        url = url.strip()
        if url and not _allowed_url(url):
            raise UpdateError("The update address must start with https://")
        self.cfg.write_text(json.dumps({"manifest_url": url}), encoding="utf-8")
        self.manifest = None

    # ------------------------------------------------------------ check

    def _open(self, url: str, timeout: float = 30):
        if not _allowed_url(url):
            raise UpdateError(f"Refusing a non-https address: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": f"PhotoExplorer/{__version__}"})
        return urllib.request.urlopen(req, timeout=timeout)

    def check(self) -> dict:
        url = self.manifest_url()
        if not url:
            raise UpdateError("No update address is set yet.")
        try:
            with self._open(url, 20) as r:
                raw = r.read(2_000_000)
                # Relative URLs resolve against the address asked for, not where it redirected
                # (GitHub redirects to a temporary storage link).
                self.manifest_base = url
        except UpdateError:
            raise
        except Exception as e:
            raise UpdateError(f"Could not reach the update address: {e}")
        try:
            m = json.loads(raw)
        except ValueError:
            raise UpdateError("The update address did not return a valid manifest.")
        if not isinstance(m, dict) or m.get("format") != 1:
            raise UpdateError("Unknown manifest format.")
        self.manifest = m
        self.checked_at = time.time()
        return self.status()

    def _abs(self, url: str) -> str:
        return urllib.parse.urljoin(self.manifest_base, url)

    def status(self) -> dict:
        installed = self.content.installed()
        packs, app = [], None
        m = self.manifest or {}
        for p in m.get("packs", []) if isinstance(m.get("packs"), list) else []:
            if not isinstance(p, dict) or not _ID.match(str(p.get("id", ""))) or p.get("kind") not in ("themes", "music"):
                continue
            have = installed.get(p["id"])
            packs.append({k: p.get(k) for k in ("id", "kind", "version", "title", "description", "size", "items")}
                         | {"installed_version": have,
                            "state": "installed" if have is not None and have >= int(p.get("version", 0))
                            else "update" if have is not None else "new"})
        a = m.get("app")
        if isinstance(a, dict) and a.get("version"):
            newer = version_tuple(a["version"]) > version_tuple(__version__)
            app = {"version": a["version"], "notes": a.get("notes", ""), "size": a.get("size"), "newer": newer,
                   "ready": self.app_ready == a["version"]}
        local = [{"id": p["id"], "kind": p["kind"], "version": p.get("version"), "title": p.get("title", p["id"])}
                 for p in self.content.packs()]
        problem = app_folder_problem()
        return {"current_version": __version__, "manifest_url": self.manifest_url(), "checked_at": self.checked_at,
                "app": app, "packs": packs, "installed": local, "job": dict(self.job),
                "can_apply": problem is None, "app_problem": problem, "last_update": self.last_update}

    # ------------------------------------------------------------ install

    def start(self, pack_ids: list[str] | None, include_app: bool) -> None:
        """Download and install in the background: the chosen packs (None = every new or updated pack), and the app."""
        if self.manifest is None:
            raise UpdateError("Check for updates first.")
        with self._lock:
            if self.job["state"] == "running":
                raise UpdateError("An update is already running.")
            st = self.status()
            todo = [p for p in st["packs"] if p["state"] != "installed" and (pack_ids is None or p["id"] in pack_ids)]
            app = (st["app"] if include_app and st["can_apply"] and st["app"] and st["app"]["newer"]
                   and not st["app"]["ready"] else None)      # no point downloading what can't be installed here
            if not todo and not app:
                raise UpdateError("Nothing to download: everything is up to date.")
            self._cancel.clear()
            self.job = {"state": "running", "message": "Starting", "item": None, "done": 0,
                        "total": len(todo) + (1 if app else 0), "bytes": 0, "bytes_total": 0, "failed": []}
        threading.Thread(target=self._run, args=([p["id"] for p in todo], app is not None),
                         name="update", daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, pack_ids: list[str], app: bool) -> None:
        by_id = {p["id"]: p for p in self.manifest.get("packs", []) if isinstance(p, dict)}
        try:
            for pid in pack_ids:
                if self._cancel.is_set():
                    break
                p = by_id[pid]
                self.job.update(item=p.get("title", pid), message=f"Downloading {p.get('title', pid)}")
                try:
                    self._install_pack(p)
                except Exception as e:
                    self.job["failed"].append({"item": p.get("title", pid), "error": str(e)[:300]})
                self.job["done"] += 1
            if app and not self._cancel.is_set():
                a = self.manifest["app"]
                self.job.update(item=f"Photo Explorer {a['version']}", message=f"Downloading Photo Explorer {a['version']}")
                try:
                    self._stage_app(a)
                except Exception as e:
                    self.job["failed"].append({"item": f"Photo Explorer {a['version']}", "error": str(e)[:300]})
                self.job["done"] += 1
            self.job.update(state="cancelled" if self._cancel.is_set() else "done", item=None,
                            message="Stopped" if self._cancel.is_set() else "Finished")
        except Exception as e:  # unexpected: report, don't crash the engine
            self.job.update(state="error", message=str(e)[:300])

    def _download(self, entry: dict, dest: Path, limit: int) -> Path:
        url, want = self._abs(str(entry.get("url", ""))), str(entry.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", want):
            raise UpdateError("The manifest has no valid checksum for this download.")
        size = int(entry.get("size") or 0)
        if size > limit:
            raise UpdateError("Download is larger than allowed.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        h = hashlib.sha256()
        got = 0
        self.job.update(bytes=0, bytes_total=size)
        try:
            with self._open(url, 60) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or size or 0)
                self.job["bytes_total"] = total
                while True:
                    if self._cancel.is_set():
                        raise UpdateError("Stopped")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > limit:
                        raise UpdateError("Download is larger than allowed.")
                    h.update(chunk)
                    f.write(chunk)
                    self.job["bytes"] = got
            if h.hexdigest() != want:
                raise UpdateError("The download is damaged or was changed (checksum does not match).")
            os.replace(part, dest)
            return dest
        finally:
            if part.exists():
                part.unlink(missing_ok=True)

    def _install_pack(self, p: dict) -> None:
        pid = p["id"]
        downloads = self.stage / "downloads"
        z_path = self._download(p, downloads / f"{pid}.zip", MAX_PACK)
        tmp = self.content.dir / f".tmp-{pid}"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            with zipfile.ZipFile(z_path) as z:
                members = _safe_members(z, exts=PACK_EXTS)
                for m in members:
                    target = tmp / PurePosixPath(m.filename.replace("\\", "/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(m) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
            meta = json.loads((tmp / "pack.json").read_text(encoding="utf-8"))
            if meta.get("id") != pid or meta.get("kind") != p.get("kind"):
                raise UpdateError("The pack does not match the manifest.")
            final = self.content.dir / pid
            old = self.content.dir / f".old-{pid}"
            shutil.rmtree(old, ignore_errors=True)
            if final.exists():
                os.replace(final, old)
            os.replace(tmp, final)
            shutil.rmtree(old, ignore_errors=True)
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            raise UpdateError(f"Could not install the pack: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            z_path.unlink(missing_ok=True)

    def _stage_app(self, a: dict) -> None:
        ver = a["version"]
        if not re.fullmatch(r"[0-9][0-9A-Za-z.\-]{0,30}", ver):
            raise UpdateError("Bad version in the manifest.")
        z_path = self._download(a, self.stage / "downloads" / f"app-{ver}.zip", MAX_APP)
        target = self.stage / ver
        shutil.rmtree(target, ignore_errors=True)
        self.job["message"] = f"Unpacking Photo Explorer {ver}"
        try:
            with zipfile.ZipFile(z_path) as z:
                for m in _safe_members(z, prefix="PhotoExplorer"):
                    if PurePosixPath(m.filename.replace("\\", "/")).parts[1:2] == ("data",):
                        continue                      # never ship or overwrite user data
                    z.extract(m, target)
        except zipfile.BadZipFile as e:
            raise UpdateError(f"The app download is not a valid zip: {e}")
        finally:
            z_path.unlink(missing_ok=True)
        root = target / "PhotoExplorer"
        for need in ("PhotoExplorer.exe", "backend/photoexplorer/__init__.py", "runtime/python"):
            if not (root / need).exists():
                shutil.rmtree(target, ignore_errors=True)
                raise UpdateError(f"The app download is incomplete (no {need}).")
        self.app_ready = ver

    # ------------------------------------------------------------ apply app update

    def apply(self) -> dict:
        """Start the script that swaps in the new version once the app has closed. The UI then quits."""
        problem = app_folder_problem()
        if problem:
            raise UpdateError(problem)
        if not self.app_ready:
            raise UpdateError("No downloaded update is waiting.")
        src = self.stage / self.app_ready / "PhotoExplorer"
        root = app_root()
        log = self.stage / "apply.log"
        pids = [os.getpid(), os.getppid()]
        wait = "\n".join(
            f':wait{p}\ntasklist /FI "PID eq {p}" 2>nul | find " {p} " >nul && (ping -n 2 127.0.0.1 >nul & goto wait{p})'
            for p in pids)
        q = lambda p: str(p).replace('"', "")
        script = f"""@echo off
setlocal
{wait}
echo Updating from "{q(src)}" >> "{q(log)}"
robocopy "{q(src)}" "{q(root)}" /E /XD "{q(root / 'data')}" /R:5 /W:2 /NFL /NDL /NJH /NP >> "{q(log)}"
if errorlevel 8 goto failed
robocopy "{q(src / 'backend')}" "{q(root / 'backend')}" /MIR /XD __pycache__ /R:5 /W:2 /NFL /NDL /NJH /NP >> "{q(log)}"
if errorlevel 8 goto failed
echo Done >> "{q(log)}"
start "" "{q(root / 'PhotoExplorer.exe')}"
exit /b 0
:failed
echo Update failed; the previous version is kept where files could not be replaced. >> "{q(log)}"
start "" "{q(root / 'PhotoExplorer.exe')}"
"""
        cmd = self.stage / "apply.cmd"
        cmd.write_text(script, encoding="mbcs" if sys.platform == "win32" else "utf-8")
        subprocess.Popen(["cmd.exe", "/c", str(cmd)], cwd=str(self.stage), creationflags=_DETACHED,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return {"applying": self.app_ready}
