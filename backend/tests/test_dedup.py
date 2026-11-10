"""Duplicates, look-alikes, safe removal and undo."""
from __future__ import annotations

import os
import random
import shutil
import time

import pytest
from PIL import Image, ImageDraw, ImageFilter

from photoexplorer.config import Settings
from photoexplorer.db import Database
from photoexplorer.dedup import actions
from photoexplorer.dedup.groups import find_groups
from photoexplorer.scanner.manager import ScanManager


def _scene(seed, size=(900, 700)):
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    d = ImageDraw.Draw(img)
    for _ in range(25):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        d.ellipse((x, y, x + rnd.randint(40, 260), y + rnd.randint(40, 260)),
                  fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    return img


def _save(img, path, when=None, model="CamX"):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if when:
        exif.get_ifd(0x8769)[0x9003] = when
        exif[0x0110] = model
    img.save(path, quality=92, exif=exif)


@pytest.fixture
def lib(tmp_path):
    photos = tmp_path / "photos"
    a = _scene(1)
    _save(a, photos / "2020" / "a.jpg", "2020:01:01 10:00:00")
    shutil.copy(photos / "2020" / "a.jpg", photos / "Backup" / "a.jpg") if (photos / "Backup").mkdir() is None else None
    shutil.copy(photos / "2020" / "a.jpg", photos / "2020" / "a (1).jpg")
    # WhatsApp-style copy: smaller, recompressed, no EXIF -> different bytes, same picture
    (photos / "WhatsApp").mkdir()
    a.resize((450, 350)).save(photos / "WhatsApp" / "IMG-20200105-WA0001.jpg", quality=60)

    # Burst: same scene, camera moved a little; the middle one is blurred.
    big = _scene(2, (1000, 760))
    for i, (dx, sec) in enumerate([(0, 0), (12, 1), (24, 3)]):
        shot = big.crop((dx, dx // 2, dx + 900, dx // 2 + 700))
        if i == 1:
            shot = shot.filter(ImageFilter.GaussianBlur(3))
        _save(shot, photos / "2021" / f"burst_{i}.jpg", f"2021:06:01 12:00:{sec:02d}")
    # Different picture in the same minute: must not join the burst.
    _save(_scene(3), photos / "2021" / "other.jpg", "2021:06:01 12:00:30")
    # Flat pictures: identical-looking hashes, but never "look-alikes".
    _save(Image.new("RGB", (300, 300), (10, 10, 10)), photos / "flat" / "black.jpg", "2019:01:01 00:00:00")
    _save(Image.new("RGB", (300, 300), (12, 12, 12)), photos / "flat" / "black2.jpg", "2018:01:01 00:00:00")

    s = Settings.load(tmp_path / "data", workers=2, faces_enabled=False)
    db = Database(s.db_path)
    db.conn.execute("INSERT INTO sources(path, added_at) VALUES (?, ?)", (str(photos), int(time.time())))
    return photos, s, db


def _scan(db, s, mode="full"):
    m = ScanManager(db, s)
    assert m.start(mode=mode)
    assert m.wait(120)
    st = m.status()
    assert st["state"] == "done", st
    return st


def _groups(c):
    out = []
    for g in c.execute("SELECT * FROM dup_groups ORDER BY kind, id").fetchall():
        mem = c.execute("SELECT m.filename, m.path, dm.keep, dm.media_id FROM dup_members dm "
                        "JOIN media m ON m.id = dm.media_id WHERE dm.group_id = ? ORDER BY dm.rank",
                        (g["id"],)).fetchall()
        out.append((g, mem))
    return out


def test_groups_and_suggestions(lib):
    photos, s, db = lib
    st = _scan(db, s)
    assert st["dedup_exact"] == 1
    c = db.conn
    groups = _groups(c)
    kinds = sorted(g["kind"] for g, _ in groups)
    print([(g["kind"], g["max_distance"], [m["filename"] for m in mem]) for g, mem in groups])
    assert kinds == ["burst", "exact", "near"]

    exact = next(mem for g, mem in groups if g["kind"] == "exact")
    assert len(exact) == 3
    assert exact[0]["path"] == str(photos / "2020" / "a.jpg")      # not the Backup / "(1)" copy
    assert [m["keep"] for m in exact] == [1, 0, 0]

    near = next(mem for g, mem in groups if g["kind"] == "near")
    assert {m["filename"] for m in near} >= {"IMG-20200105-WA0001.jpg"} and len(near) == 2
    assert near[0]["path"] == str(photos / "2020" / "a.jpg")      # the original stands in for its copies

    burst = next(mem for g, mem in groups if g["kind"] == "burst")
    assert {m["filename"] for m in burst} == {"burst_0.jpg", "burst_1.jpg", "burst_2.jpg"}
    assert burst[0]["filename"] != "burst_1.jpg"                    # the blurred frame is never the pick
    assert all("other" not in m["filename"] and "black" not in m["filename"] for _, mem in groups for m in mem)


def test_archive_rescan_undo_and_guards(lib):
    photos, s, db = lib
    _scan(db, s)
    c = db.conn
    g = c.execute("SELECT id FROM dup_groups WHERE kind = 'exact'").fetchone()[0]
    remove_ids = [r[0] for r in c.execute("SELECT media_id FROM dup_members WHERE group_id = ? AND keep = 0", (g,))]
    all_ids = [r[0] for r in c.execute("SELECT media_id FROM dup_members WHERE group_id = ?", (g,))]

    with pytest.raises(actions.RemovalError):                       # never every copy
        actions.remove(c, g, all_ids)

    res = actions.remove(c, g, remove_ids, "archive")
    assert sorted(res["removed"]) == sorted(remove_ids) and not res["failed"]
    archive = photos / actions.ARCHIVE_DIR
    assert (archive / "Backup" / "a.jpg").is_file() and (archive / "2020" / "a (1).jpg").is_file()
    assert not (photos / "Backup" / "a.jpg").exists()
    assert (photos / "2020" / "a.jpg").is_file()                     # keeper untouched
    assert c.execute("SELECT count(*) FROM dup_groups WHERE id = ?", (g,)).fetchone()[0] == 0  # settled

    # Rescan: removed files stay "removed" (not "missing"), archive folder is not indexed.
    st = _scan(db, s)
    assert st["new"] == 0 and st["missing"] == 0
    assert {r[0] for r in c.execute("SELECT status FROM media WHERE id IN (%s)" % ",".join(map(str, remove_ids)))} == {4}
    assert c.execute("SELECT count(*) FROM dup_groups WHERE kind = 'exact'").fetchone()[0] == 0

    # Undo the whole batch.
    batch = c.execute("SELECT batch FROM removals LIMIT 1").fetchone()[0]
    ids = [r[0] for r in c.execute("SELECT id FROM removals WHERE batch = ?", (batch,))]
    res = actions.undo(c, ids)
    assert sorted(res["restored"]) == sorted(ids) and not res["failed"]
    assert (photos / "Backup" / "a.jpg").is_file()
    st = _scan(db, s)
    assert st["new"] == 0 and st["changed"] == 0
    assert c.execute("SELECT count(*) FROM dup_groups WHERE kind = 'exact'").fetchone()[0] == 1

    # A file edited after the scan is refused.
    g = c.execute("SELECT id FROM dup_groups WHERE kind = 'exact'").fetchone()[0]
    victim = c.execute("SELECT m.id, m.path FROM dup_members dm JOIN media m ON m.id = dm.media_id "
                       "WHERE dm.group_id = ? AND dm.keep = 0 LIMIT 1", (g,)).fetchone()
    with open(victim["path"], "ab") as f:
        f.write(b"x")
    res = actions.remove(c, g, [victim["id"]])
    assert not res["removed"] and "changed" in res["failed"][0]["error"]

    # "Not duplicates" is remembered across runs.
    near = c.execute("SELECT id FROM dup_groups WHERE kind = 'near'").fetchone()[0]
    actions.ignore_group(c, near)
    find_groups(c, s.thumbs_dir)
    assert c.execute("SELECT count(*) FROM dup_groups WHERE kind = 'near'").fetchone()[0] == 0


def test_recycle_refused_off_local_drives(tmp_path):
    assert actions.recycle_supported(str(tmp_path)) == (os.name == "nt")
    assert not actions.recycle_supported(r"\\server\share\photo.jpg")


def test_remove_from_app_only(lib):
    photos, s, db = lib
    _scan(db, s)
    c = db.conn
    g = c.execute("SELECT id FROM dup_groups WHERE kind = 'exact'").fetchone()[0]
    ids = [r[0] for r in c.execute("SELECT media_id FROM dup_members WHERE group_id = ? AND keep = 0", (g,))]
    res = actions.remove(c, g, ids, "app")
    assert sorted(res["removed"]) == sorted(ids) and not res["failed"]
    assert (photos / "Backup" / "a.jpg").is_file()                   # file left on disk
    assert not (photos / actions.ARCHIVE_DIR).exists()
    # A rescan does not bring them back, nor call them missing.
    st = _scan(db, s)
    assert st["new"] == 0 and st["missing"] == 0
    assert {r[0] for r in c.execute("SELECT status FROM media WHERE id IN (%s)" % ",".join(map(str, ids)))} == {5}
    assert c.execute("SELECT count(*) FROM dup_groups WHERE kind = 'exact'").fetchone()[0] == 0
    rids = [r[0] for r in c.execute("SELECT id FROM removals WHERE method = 'app'")]
    res = actions.undo(c, rids)
    assert sorted(res["restored"]) == sorted(rids) and not res["failed"]
    _scan(db, s)
    assert c.execute("SELECT count(*) FROM dup_groups WHERE kind = 'exact'").fetchone()[0] == 1


def test_unlink_and_relink_folder(lib):
    from photoexplorer.server import create_app
    photos, s, db = lib
    (photos / "2020" / "sub").mkdir()
    shutil.copy(photos / "2021" / "other.jpg", photos / "2020" / "sub" / "x.jpg")
    _scan(db, s)
    c = db.conn
    ep = {(r.path, tuple(r.methods)[0]): r.endpoint for r in create_app(s, "t").routes if hasattr(r, "methods")}
    fid = c.execute("SELECT id FROM folders WHERE path = ?", (str(photos / "2020"),)).fetchone()[0]
    root = c.execute("SELECT id FROM folders WHERE path = ?", (str(photos),)).fetchone()
    if root:
        with pytest.raises(Exception, match="whole source"):
            ep[("/api/folders/{folder_id}/unlink", "POST")](root[0])
    r = ep[("/api/folders/{folder_id}/unlink", "POST")](fid)
    assert r["files"] == 3 and r["subfolders"] == 1
    assert (photos / "2020" / "a.jpg").is_file()                     # files untouched
    st = _scan(db, s)
    assert st["new"] == 0 and st["missing"] == 0                       # the scan skips it
    assert c.execute("SELECT count(*) FROM media WHERE path LIKE ?", (str(photos / "2020") + "%",)).fetchone()[0] == 0
    assert [u["id"] for u in ep[("/api/folders/unlinked", "GET")]()] == [fid]
    ep[("/api/folders/{folder_id}/relink", "POST")](fid)          # starts a scan of that source
    q = "SELECT count(*) FROM media WHERE status = 1 AND path LIKE ?"
    for _ in range(600):
        if c.execute(q, (str(photos / "2020") + "%",)).fetchone()[0] == 3:
            break
        time.sleep(0.1)
    assert c.execute(q, (str(photos / "2020") + "%",)).fetchone()[0] == 3


def test_export_thumb(lib, tmp_path):
    from photoexplorer.server import create_app
    photos, s, db = lib
    out = tmp_path / "c.jpg"
    _scene(5).save(out)
    app = create_app(s, "t")
    jid = db.conn.execute("INSERT INTO exports(kind, title, status, media_ids, options, output_path, created_at) "
                          "VALUES ('collage', 't', 'done', '[]', '{}', ?, 0)", (str(out),)).lastrowid
    thumb = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/exports/{export_id}/thumb")
    resp = thumb(jid)
    from PIL import Image as I
    assert max(I.open(resp.path).size) == 480

