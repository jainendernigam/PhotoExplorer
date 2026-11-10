"""Photo edits (copy / replace / restore / batch) and auto organize rules."""
from __future__ import annotations

import hashlib
import os
import time

import pytest
from PIL import Image

from photoexplorer.edit import ops as edit_ops
from photoexplorer.edit.manager import EditManager
from photoexplorer.edit.ops import EditError
from photoexplorer.organize.rules import Rules
from photoexplorer.organize.runner import OrganizeManager

from test_organize import _photo, _run, lib, recycled  # noqa: F401  (fixtures)


def _sha(p) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _media(db, mid):
    return db.conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()


DETAILS = ("taken_at", "date_source", "camera_make", "camera_model", "gps_lat", "gps_lon")


def test_ops_geometry_and_checks():
    img = Image.new("RGB", (400, 200), (200, 50, 50))
    o = edit_ops.normalize({"rotate": 90})
    assert edit_ops.apply(img, o).size == (200, 400)
    o = edit_ops.normalize({"crop": {"x": 0.25, "y": 0, "w": 0.5, "h": 0.5}})
    assert edit_ops.apply(img, o).size == (200, 100)
    o = edit_ops.normalize({"frame": "print", "frame_size": 5})
    assert edit_ops.apply(img, o).size == (420, 250)                 # 10 px sides and top, 40 px bottom
    assert edit_ops.apply(img, edit_ops.normalize({"resize": 100})).size == (100, 50)
    s = edit_ops.apply(img, edit_ops.normalize({"straighten": 10})).size
    assert s[0] < 400 and s[1] < 200 and abs(s[0] / s[1] - 2) < 0.05  # same shape, corners cropped
    for o in ({"auto": True, "brightness": 40, "contrast": -20, "saturation": 30, "warmth": 50,
               "highlights": -40, "shadows": 40, "filter": "sepia", "sharpen": 50,
               "denoise": 30, "text": {"text": "Hello", "where": "bottom"}},):
        assert edit_ops.apply(img, edit_ops.normalize(o)).size == (400, 200)
    with pytest.raises(EditError):
        edit_ops.normalize({"rotate": 45})
    with pytest.raises(EditError):
        edit_ops.normalize({"nope": 1})
    assert edit_ops.is_empty(edit_ops.normalize({}))


def test_save_copy_keeps_details_and_survives_original(lib):  # noqa: F811
    tmp, src, s, db, ids = lib
    ed = EditManager(db, s)
    mid = ids["dump/IMG_1.jpg"]                      # Canon, 2019-06-25, Anna
    orig = _media(db, mid)
    before = _sha(orig["path"])
    res = ed.save(mid, {"brightness": 30, "warmth": 20}, "copy")
    new = _media(db, res["media_id"])
    assert os.path.basename(res["path"]) == "IMG_1 (edited).jpg" and new["status"] == 1
    assert _sha(orig["path"]) == before                                 # original untouched
    assert all(new[k] == orig[k] for k in DETAILS)
    with Image.open(res["path"]) as im:                                 # details are in the file too
        ex = im.getexif()
        assert ex.get(0x010F) == "Canon" and ex.get_ifd(0x8769).get(0x9003) == "2019:06:25 12:00:00"
    assert os.stat(res["path"]).st_mtime_ns == os.stat(orig["path"]).st_mtime_ns
    # same framing: Anna is on the copy straight away
    assert db.conn.execute("SELECT person_id FROM faces WHERE media_id = ?", (new["id"],)).fetchone()[0] == 1
    info = ed.info(new["id"])
    assert info["edit_of"]["id"] == mid and ed.info(mid)["edited_copies"][0]["id"] == new["id"]
    assert db.conn.execute("SELECT 1 FROM dup_ignored WHERE a = ? AND b = ?",
                           (min(mid, new["id"]), max(mid, new["id"]))).fetchone()
    again = ed.save(mid, {"rotate": 90}, "copy")                        # a second copy gets a new name
    assert os.path.basename(again["path"]) == "IMG_1 (edited 2).jpg"
    assert _media(db, again["media_id"])["width"] == orig["height"]
    assert not db.conn.execute("SELECT 1 FROM faces WHERE media_id = ?", (again["media_id"],)).fetchone()

    # deleting the original leaves the edited copy alone
    os.remove(orig["path"])
    db.conn.execute("UPDATE media SET status = 3 WHERE id = ?", (mid,))
    assert os.path.isfile(res["path"]) and _media(db, new["id"])["status"] == 1
    assert ed.info(new["id"])["edit_of"] is None


def test_save_copy_to_other_folder_adds_it(lib):  # noqa: F811
    tmp, src, s, db, ids = lib
    out = tmp / "Edits"
    out.mkdir()
    res = EditManager(db, s).save(ids["old/IMG_1.jpg"], {"filter": "mono"}, "copy", str(out))
    assert os.path.dirname(res["path"]) == str(out)
    assert db.conn.execute("SELECT count(*) FROM sources WHERE path = ?", (str(out),)).fetchone()[0] == 1


def test_replace_and_restore(lib):  # noqa: F811
    tmp, src, s, db, ids = lib
    ed = EditManager(db, s)
    mid = ids["dump/IMG_2.jpg"]
    orig = _media(db, mid)
    before, mtime = _sha(orig["path"]), os.stat(orig["path"]).st_mtime_ns
    res = ed.save(mid, {"crop": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}, "replace")
    now = _media(db, mid)
    assert res["media_id"] == mid and _sha(orig["path"]) != before
    assert (now["width"], now["height"]) == (160, 120)
    assert all(now[k] == orig[k] for k in DETAILS)                      # details unchanged after replace
    assert os.stat(orig["path"]).st_mtime_ns == mtime
    assert db.conn.execute("SELECT count(*) FROM faces WHERE media_id = ?", (mid,)).fetchone()[0] == 0  # re-found later
    assert ed.info(mid)["can_restore"]
    ed.restore(mid)
    assert _sha(orig["path"]) == before and _media(db, mid)["width"] == 320
    assert not ed.info(mid)["can_restore"]
    with pytest.raises(EditError):
        ed.restore(mid)
    with pytest.raises(EditError):
        ed.save(mid, {}, "replace")                                     # nothing changed


def test_replace_png_and_refuse_raw(lib, tmp_path):  # noqa: F811
    tmp, src, s, db, ids = lib
    ed = EditManager(db, s)
    r = _media(db, ids["nodate/scan.jpg"])
    png = os.path.join(os.path.dirname(r["path"]), "pic.png")
    Image.new("RGB", (64, 48), (10, 200, 30)).save(png)
    db.conn.execute("INSERT INTO media(source_id, folder_id, path, filename, ext, kind, size, mtime_ns, added_at, status) "
                    "VALUES (?, ?, ?, 'pic.png', 'png', 'image', 1, 1, 1, 1)", (r["source_id"], r["folder_id"], png))
    pid = db.conn.execute("SELECT id FROM media WHERE path = ?", (png,)).fetchone()[0]
    ed.save(pid, {"rotate": 90}, "replace")
    with Image.open(png) as im:
        assert im.format == "PNG" and im.size == (48, 64)
    db.conn.execute("UPDATE media SET kind = 'raw', ext = 'nef' WHERE id = ?", (pid,))
    with pytest.raises(EditError, match="copy"):
        ed.save(pid, {"rotate": 90}, "replace")


def test_batch(lib):  # noqa: F811
    tmp, src, s, db, ids = lib
    ed = EditManager(db, s)
    sel = [ids["dump/IMG_1.jpg"], ids["old/IMG_1.jpg"]]
    with pytest.raises(EditError):
        ed.batch_start(sel, {"crop": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}, "copy", None)
    ed.batch_start(sel, {"auto": True, "resize": 160}, "copy", None)
    for _ in range(200):
        st = ed.batch_status()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done" and len(st["created"]) == 2 and not st["failed"], st
    assert all(_media(db, i)["width"] == 160 for i in st["created"])


def test_auto_organize_rules(lib):  # noqa: F811
    tmp, src, s, db, ids = lib
    org = OrganizeManager(db, s)
    rules = Rules(db, org)
    dest = tmp / "Anna"
    rid = rules.create("Anna's photos", str(dest), "copy", {"folder_template": "{year}"}, [1], "any")
    res = rules.run()
    assert res[0]["new"] == 2 and res[0]["job_id"]
    for _ in range(300):
        if db.conn.execute("SELECT status FROM organize_jobs WHERE id = ?", (res[0]["job_id"],)).fetchone()[0] == "done":
            break
        time.sleep(0.05)
    assert sorted(os.listdir(dest / "2019")) == ["IMG_1.jpg", "IMG_2.jpg"]
    again = rules.run()                                                 # nothing new the second time
    assert again[0]["new"] == 0 and again[0]["job_id"] is None and again[0]["message"] == "Nothing new"
    rules.update(rid, enabled=False)
    assert rules.run() == []
    assert rules.list()[0]["people_names"] == ["Anna"]
    with pytest.raises(Exception):
        rules.create("x", str(dest), "copy", {}, [], "any")


def _wait_job(db, jid):
    for _ in range(300):
        if db.conn.execute("SELECT status FROM organize_jobs WHERE id = ?", (jid,)).fetchone()[0] in ("done", "error"):
            return
        time.sleep(0.05)


def test_undo_removes_what_the_job_made(lib, recycled):  # noqa: F811
    tmp, src, s, db, ids = lib
    org = OrganizeManager(db, s)
    for mode in ("copy", "move"):
        dest = tmp / f"Out-{mode}"
        before = {r[0] for r in db.conn.execute("SELECT id FROM sources")}
        jid = org.start(list(ids.values()), str(dest), mode, {"folder_template": "{year}/{month}"})
        _wait_job(db, jid)
        assert dest.is_dir() and any(dest.iterdir())
        res = org.undo(jid)
        assert res["undone"] > 0 and not res["failed"], res
        assert not dest.exists(), list(dest.rglob("*"))          # report, sub-folders and the folder itself are gone
        assert {r[0] for r in db.conn.execute("SELECT id FROM sources")} == before
        assert not db.conn.execute("SELECT 1 FROM folders WHERE path LIKE ?", (str(dest) + "%",)).fetchone()
        assert all(os.path.isfile(_media(db, i)["path"]) for i in ids.values())
    # a folder that was already there stays (only emptied)
    keep = tmp / "Existing"
    keep.mkdir()
    jid = org.start(list(ids.values()), str(keep), "copy", {"folder_template": "{year}"})
    _wait_job(db, jid)
    org.undo(jid)
    assert keep.is_dir() and not any(keep.iterdir())


def test_edit_rule(lib):  # noqa: F811
    tmp, src, s, db, ids = lib
    rules = Rules(db, OrganizeManager(db, s))
    rid = rules.create("Anna", str(tmp / "A"), "copy", {"folder_template": "{year}"}, [1], "any")
    rules.update(rid, name="Anna & co", dest_root=str(tmp / "B"), mode="move",
                 options={"folder_template": "{year}/{month}"}, people=[1, 2], people_mode="all")
    r = rules.list()[0]
    assert (r["name"], r["dest_root"], r["mode"], r["people"], r["people_mode"]) == (
        "Anna & co", str(tmp / "B"), "move", [1, 2], "all")
    assert r["options"]["folder_template"] == "{year}/{month}"
    with pytest.raises(Exception):
        rules.update(rid, people=[])
    with pytest.raises(Exception):
        rules.update(rid, dest_root="relative")
    assert rules.list()[0]["people"] == [1, 2]
