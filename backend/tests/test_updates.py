"""Update software: manifest check, pack install, checksum and zip safety, app staging.
Runs against a local web server, never the internet."""
from __future__ import annotations

import hashlib
import http.server
import json
import threading
import zipfile
from functools import partial

import pytest

from photoexplorer import __version__
from photoexplorer import updates
from photoexplorer.content import Content
from photoexplorer.updates import Updater, UpdateError


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _zip(path, files: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return {"url": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}


def _pack(root, pid, kind, version, extra=None):
    items = ([{"id": "song", "title": "Song", "file": "song.m4a"}] if kind == "music"
             else [{"id": f"{pid}-t", "name": "Test theme", "options": {"color": "sepia"}}])
    files = {"pack.json": json.dumps({"id": pid, "kind": kind, "version": version, "title": pid, "items": items}).encode()}
    if kind == "music":
        files["song.m4a"] = b"not really audio"
    files.update(extra or {})
    return {"id": pid, "kind": kind, "version": version, "title": pid} | _zip(root / f"{pid}.zip", files)


def _run(u: Updater, packs=None, app=False):
    u.start(packs, app)
    for _ in range(200):
        if u.job["state"] != "running":
            return u.job
        threading.Event().wait(0.05)
    raise AssertionError("update did not finish")


def _updater(tmp_path, url, manifest, root):
    (root / "manifest.json").write_text(json.dumps(manifest))
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    u = Updater(data, Content(data))
    u.set_manifest_url(f"{url}/manifest.json")
    return u


def test_check_and_install_packs(tmp_path, site):
    root, url = site
    m = {"format": 1, "packs": [_pack(root, "music-1", "music", 1), _pack(root, "themes-1", "themes", 2)],
         "app": {"version": __version__, "url": "x.zip", "sha256": "0" * 64}}
    u = _updater(tmp_path, url, m, root)
    st = u.check()
    assert [p["state"] for p in st["packs"]] == ["new", "new"] and st["app"]["newer"] is False
    job = _run(u)
    assert job["state"] == "done" and not job["failed"], job
    assert {p["state"] for p in u.status()["packs"]} == {"installed"}
    assert [t["id"] for t in u.content.music()] == ["song"]
    assert any(t["id"] == "themes-1-t" and t["source"] == "themes-1" for t in u.content.themes())
    # a newer pack version is offered as an update and replaces the old files
    m["packs"][1] = _pack(root, "themes-1", "themes", 3)
    (root / "manifest.json").write_text(json.dumps(m))
    assert {p["id"]: p["state"] for p in u.check()["packs"]}["themes-1"] == "update"
    assert _run(u, ["themes-1"])["failed"] == []
    assert u.content.installed()["themes-1"] == 3
    with pytest.raises(UpdateError):
        u.start(None, True)                      # nothing left to do


def test_bad_checksum_and_unsafe_zip_are_refused(tmp_path, site):
    root, url = site
    bad = _pack(root, "music-1", "music", 1) | {"sha256": "1" * 64}
    evil = _pack(root, "themes-1", "themes", 1, {"../escape.json": b"{}"})
    exe = _pack(root, "themes-2", "themes", 1, {"run.exe": b"MZ"})
    u = _updater(tmp_path, url, {"format": 1, "packs": [bad, evil, exe]}, root)
    u.check()
    job = _run(u)
    assert len(job["failed"]) == 3
    assert "checksum" in job["failed"][0]["error"]
    assert "Unsafe path" in job["failed"][1]["error"] and "not allowed" in job["failed"][2]["error"]
    assert u.content.installed() == {} and not (tmp_path / "data" / "escape.json").exists()


def test_app_update_is_staged(tmp_path, site, monkeypatch):
    monkeypatch.setattr(updates, "app_folder_problem", lambda root=None: None)
    root, url = site
    files = {"PhotoExplorer/PhotoExplorer.exe": b"MZ", "PhotoExplorer/backend/photoexplorer/__init__.py": b"",
             "PhotoExplorer/runtime/python/python.exe": b"MZ", "PhotoExplorer/data/library.db": b"never copied"}
    app = {"version": "99.0.0", "notes": "New things"} | _zip(root / "app.zip", files)
    u = _updater(tmp_path, url, {"format": 1, "packs": [], "app": app}, root)
    st = u.check()
    assert st["app"]["newer"] and not st["app"]["ready"]
    assert _run(u, app=True)["failed"] == []
    staged = tmp_path / "data" / "update" / "99.0.0" / "PhotoExplorer"
    assert (staged / "PhotoExplorer.exe").is_file() and not (staged / "data").exists()
    assert u.status()["app"]["ready"]


def test_only_https_addresses(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "DEFAULT_MANIFEST_URL", "")   # never reach the real address in tests
    monkeypatch.delenv("PE_UPDATE_URL", raising=False)
    u = Updater(tmp_path, Content(tmp_path))
    with pytest.raises(UpdateError):
        u.set_manifest_url("http://example.com/manifest.json")
    with pytest.raises(UpdateError):
        u.check()                                # no address set


def test_app_folder_problem_and_skipping(tmp_path, site, monkeypatch):
    app = tmp_path / "PhotoExplorer"
    for d in ("backend", "runtime"):
        (app / d).mkdir(parents=True)
    assert "packaged app" in updates.app_folder_problem(app)          # no exe: a development copy
    (app / "PhotoExplorer.exe").write_bytes(b"MZ")
    assert updates.app_folder_problem(app) is None
    assert not list(app.glob(".pe-write-test-*"))                      # the write test leaves nothing behind

    # where the app can't update itself, the big app download is skipped and packs still install
    monkeypatch.setattr(updates, "app_folder_problem", lambda root=None: "protected folder")
    root, url = site
    m = {"format": 1, "packs": [_pack(root, "themes-1", "themes", 1)],
         "app": {"version": "99.0.0"} | _zip(root / "app.zip", {"PhotoExplorer/x.txt": b"x"})}
    u = _updater(tmp_path, url, m, root)
    st = u.check()
    assert st["can_apply"] is False and st["app_problem"] == "protected folder"
    job = _run(u, app=True)
    assert job["total"] == 1 and not job["failed"] and u.content.installed() == {"themes-1": 1}
    with pytest.raises(UpdateError, match="protected folder"):
        u.apply()


def test_last_update_result_is_reported(tmp_path):
    stage = tmp_path / "update"
    stage.mkdir()
    enc = "mbcs" if updates.sys.platform == "win32" else "utf-8"
    (stage / "apply.log").write_text("Updating\nrobocopy output\nDone \n", encoding=enc)
    assert Updater(tmp_path, Content(tmp_path)).last_update["ok"] is True
    assert not stage.exists()                                          # staged files are cleared
    stage.mkdir()
    (stage / "apply.log").write_text("Updating\nUpdate failed; ...\n", encoding=enc)
    assert Updater(tmp_path, Content(tmp_path)).last_update["ok"] is False
    assert Updater(tmp_path, Content(tmp_path)).last_update is None    # reported once
