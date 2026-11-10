"""Organise into folders (copy / hard link / move + undo) and albums."""
from __future__ import annotations

import os
import random
import time

import pytest
from PIL import Image, ImageDraw

from photoexplorer.config import Settings
from photoexplorer.db import Database
from photoexplorer.organize import templates
from photoexplorer.organize.runner import OrganizeError, OrganizeManager
from photoexplorer.scanner.manager import ScanManager


def _photo(path, seed, when, make=None, model=None):
    rnd = random.Random(seed)
    img = Image.new("RGB", (320, 240), (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    ImageDraw.Draw(img).ellipse((rnd.randint(0, 200), 20, 300, 200), fill=(rnd.randint(0, 255), 90, 40))
    exif = Image.Exif()
    if when:
        exif.get_ifd(0x8769)[0x9003] = when
    if make:
        exif[0x010F], exif[0x0110] = make, model
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=90, exif=exif)


@pytest.fixture
def recycled(monkeypatch):
    """Never touch the real Recycle Bin from tests: record instead."""
    from photoexplorer.dedup import actions
    bin_ = []

    def fake_recycle(path):
        bin_.append(path)
        os.remove(path)

    monkeypatch.setattr(actions, "recycle_supported", lambda p: True)
    monkeypatch.setattr(actions, "_recycle", fake_recycle)
    return bin_


@pytest.fixture
def lib(tmp_path):
    src = tmp_path / "Messy"
    _photo(src / "dump" / "IMG_1.jpg", 1, "2019:06:25 12:00:00", "Canon", "Canon EOS 80D")
    _photo(src / "dump" / "IMG_2.jpg", 2, "2019:07:01 09:30:00")
    _photo(src / "old" / "IMG_1.jpg", 3, "2021:12:24 18:00:00")         # same name, different photo
    _photo(src / "nodate" / "scan.jpg", 4, None)
    s = Settings.load(tmp_path / "data", workers=1, faces_enabled=False)
    db = Database(s.db_path)
    c = db.conn
    c.execute("INSERT INTO sources(path, added_at) VALUES (?, ?)", (str(src), int(time.time())))
    m = ScanManager(db, s)
    m.start()
    assert m.wait(120) and m.status()["state"] == "done"
    ids = {r["path"].replace(str(src), "").lstrip("\\/").replace("\\", "/"): r["id"]
           for r in c.execute("SELECT id, path FROM media")}
    # two named people: A in IMG_1 + IMG_2, B in IMG_2 (photo with both)
    now = int(time.time())
    c.execute("INSERT INTO persons(id, name, created_at) VALUES (1, 'Anna', ?), (2, 'Ben', ?)", (now, now))
    for mid, pid, o in ((ids["dump/IMG_1.jpg"], 1, 0), (ids["dump/IMG_2.jpg"], 1, 0), (ids["dump/IMG_2.jpg"], 2, 1)):
        c.execute("INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model, person_id, assigned_by) "
                  "VALUES (?, ?, 0, 0, .1, .1, .9, 50, 100, x'00', 't', ?, 'user')", (mid, o, pid))
    return tmp_path, src, s, db, ids


def _run(mgr, db, *a):
    jid = mgr.start(*a)
    for _ in range(300):
        row = db.conn.execute("SELECT * FROM organize_jobs WHERE id = ?", (jid,)).fetchone()
        if row["status"] in ("done", "error", "cancelled"):
            return row
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _scan_date(db, ids):
    from datetime import datetime, timezone
    ts = db.conn.execute("SELECT taken_at FROM media WHERE id = ?", (ids["nodate/scan.jpg"],)).fetchone()[0]
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _tree(root):
    return sorted(os.path.relpath(os.path.join(d, f), root).replace("\\", "/")
                  for d, _, fs in os.walk(root) for f in fs if not f.startswith("_PhotoExplorer report"))


def test_templates():
    assert templates.safe_segment('a<b>:c?.') == "a_b__c_"
    assert templates.safe_segment("con") == "_con" and templates.safe_segment("  ") == "_"
    with pytest.raises(ValueError):
        templates.validate("{year}/{nope}")
    with pytest.raises(ValueError):
        templates.validate("../{year}")
    with pytest.raises(ValueError):
        templates.validate("{year}/x", allow_slash=False)


def test_copy_by_year_month_then_rerun_and_undo(lib, recycled):
    tmp, src, s, db, ids = lib
    mgr = OrganizeManager(db, s)
    dest = tmp / "Organised"
    opts = {"folder_template": "{year}/{year}-{month} {month_name}", "name_template": "{date} {time} {name}"}
    pv = mgr.preview(list(ids.values()), str(dest), "copy", opts)
    assert pv["new"] == 4 and not pv["errors"] and pv["folders"] == 4
    row = _run(mgr, db, list(ids.values()), str(dest), "copy", opts)
    assert row["status"] == "done" and row["done"] == 4, row["message"]
    scan = _scan_date(db, ids)        # no EXIF date: dated by the file's modified time
    assert _tree(dest) == sorted([
        "2019/2019-06 June/2019-06-25 120000 IMG_1.jpg",
        "2019/2019-07 July/2019-07-01 093000 IMG_2.jpg",
        "2021/2021-12 December/2021-12-24 180000 IMG_1.jpg",
        f"{scan:%Y}/{scan:%Y-%m %B}/{scan:%Y-%m-%d %H%M%S} scan.jpg",
    ])
    assert (src / "dump" / "IMG_1.jpg").is_file()                          # originals untouched
    assert row["manifest_path"] and os.path.isfile(row["manifest_path"])

    again = _run(mgr, db, list(ids.values()), str(dest), "copy", opts)      # re-run: nothing new
    assert again["done"] == 0 and again["skipped"] == 4

    res = mgr.undo(row["id"])
    assert res["undone"] == 4 and not res["failed"]
    assert len(recycled) == 4                                               # copies go to the Recycle Bin
    assert _tree(dest) == []                                                # emptied folders removed too


def test_same_names_get_numbered(lib):
    tmp, src, s, db, ids = lib
    mgr = OrganizeManager(db, s)
    dest = tmp / "Flat"
    row = _run(mgr, db, [ids["dump/IMG_1.jpg"], ids["old/IMG_1.jpg"]], str(dest), "copy", {"folder_template": ""})
    assert row["done"] == 2 and _tree(dest) == ["IMG_1 (1).jpg", "IMG_1.jpg"]


def test_by_person_and_hardlinks(lib):
    tmp, src, s, db, ids = lib
    mgr = OrganizeManager(db, s)
    dest = tmp / "People"
    opts = {"folder_template": "{person}/{year}", "persons": [1, 2]}
    pv = mgr.preview(list(ids.values()), str(dest), "hardlink", opts)
    assert not pv["errors"], pv
    row = _run(mgr, db, list(ids.values()), str(dest), "hardlink", opts)
    assert row["status"] == "done", row["message"]
    scan = _scan_date(db, ids)
    assert _tree(dest) == sorted(["Anna/2019/IMG_1.jpg", "Anna/2019/IMG_2.jpg", "Ben/2019/IMG_2.jpg",
                                  "Other photos/2021/IMG_1.jpg", f"Other photos/{scan:%Y}/scan.jpg"])
    # a hard link is the same file: no extra space
    assert os.path.samefile(dest / "Anna" / "2019" / "IMG_1.jpg", src / "dump" / "IMG_1.jpg")


def test_move_reindexes_and_undo_restores(lib):
    tmp, src, s, db, ids = lib
    mgr = OrganizeManager(db, s)
    dest = src / "Sorted"                                                 # inside the library: allowed for move
    opts = {"folder_template": "{person}/{year}", "persons": [1, 2]}
    pv = mgr.preview(list(ids.values()), str(dest), "move", opts)
    assert pv["files"] == 4 and any("first person" in w for w in pv["warnings"])
    row = _run(mgr, db, list(ids.values()), str(dest), "move", opts)
    assert row["status"] == "done" and row["done"] == 4, row["message"]
    assert not (src / "dump" / "IMG_2.jpg").exists() and (dest / "Anna" / "2019" / "IMG_2.jpg").is_file()
    p = db.conn.execute("SELECT path, status FROM media WHERE id = ?", (ids["dump/IMG_2.jpg"],)).fetchone()
    assert p["path"] == str(dest / "Anna" / "2019" / "IMG_2.jpg") and p["status"] == 1
    # faces/people survive the move; a rescan finds nothing new or missing
    assert db.conn.execute("SELECT count(*) FROM faces WHERE media_id = ?", (ids["dump/IMG_2.jpg"],)).fetchone()[0] == 2
    m = ScanManager(db, s)
    m.start()
    m.wait(60)
    st = m.status()
    assert (st["new"], st["changed"], st["missing"]) == (0, 0, 0), st

    res = mgr.undo(row["id"])
    assert res["undone"] == 4 and not res["failed"], res
    assert (src / "dump" / "IMG_2.jpg").is_file() and not (dest / "Anna").exists()
    assert db.conn.execute("SELECT path FROM media WHERE id = ?", (ids["dump/IMG_2.jpg"],)).fetchone()[0] \
        == str(src / "dump" / "IMG_2.jpg")


def test_guards(lib, monkeypatch):
    tmp, src, s, db, ids = lib
    mgr = OrganizeManager(db, s)
    all_ids = list(ids.values())
    assert mgr.preview(all_ids, str(s.data_dir / "x"), "copy", {})["errors"]           # app data folder
    assert any("duplicates" in w for w in mgr.preview(all_ids, str(src / "copies"), "copy", {})["warnings"])
    with pytest.raises(OrganizeError):
        mgr.preview(all_ids, str(tmp / "o"), "copy", {"folder_template": "{oops}"})
    with pytest.raises(OrganizeError):
        mgr.start(all_ids, str(s.data_dir / "x"), "copy", {})
    import photoexplorer.organize.runner as r
    monkeypatch.setattr(r, "_volume", lambda p: "X:" if "Other" in p else "C:")
    assert any("same drive" in e for e in mgr.preview(all_ids, str(tmp / "Other"), "hardlink", {})["errors"])
    monkeypatch.setattr(r.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    assert any("free space" in e for e in mgr.preview(all_ids, str(tmp / "o2"), "copy", {})["errors"])


def test_organize_only_chosen_people(lib):
    from photoexplorer.server import OrganizeIn, create_app
    tmp, src, s, db, ids = lib
    app = create_app(s, "t")
    preview = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/organize/preview")
    base = {"filters": {}, "dest_root": str(tmp / "Out"), "mode": "copy", "options": {"folder_template": "{year}"}}
    assert preview(OrganizeIn(**base))["files"] == 4                                   # no people: everything
    assert preview(OrganizeIn(**base, people=[1, 2], people_mode="any"))["files"] == 2  # Anna or Ben
    assert preview(OrganizeIn(**base, people=[1, 2], people_mode="all"))["files"] == 1  # both together
    sel = {**base, "filters": None, "media_ids": [ids["dump/IMG_1.jpg"], ids["old/IMG_1.jpg"]]}
    assert preview(OrganizeIn(**sel, people=[1]))["files"] == 1                        # selection, then Anna
    with pytest.raises(Exception, match="No photos match"):
        preview(OrganizeIn(**sel, people=[2]))                                         # Ben is in neither
