"""End-to-end scanner tests on a generated photo folder.

    runtime\\python\\python.exe -m pytest backend\\tests -q
"""
from __future__ import annotations

import os
import shutil
import time

import pytest
from PIL import Image

from photoexplorer.config import Settings
from photoexplorer.db import Database
from photoexplorer.scanner import dates
from photoexplorer.scanner.hashing import hamming
from photoexplorer.scanner.manager import DONE, ERROR, MISSING, ScanManager


def _photo(path, color, date=None, size=(640, 480)):
    img = Image.new("RGB", size, color)
    # some structure so perceptual hashes are meaningful
    for x in range(0, size[0], 40):
        for y in range(0, size[1], 40):
            if (x // 40 + y // 40) % 2:
                img.paste((color[0] // 2, color[1] // 2, color[2] // 2), (x, y, x + 40, y + 40))
    exif = Image.Exif()
    if date:
        exif[0x0132] = date
        exif.get_ifd(0x8769)[0x9003] = date
    exif[0x010F] = "TestCam"
    img.save(path, "JPEG", quality=90, exif=exif)


@pytest.fixture
def lib(tmp_path):
    photos = tmp_path / "photos"
    (photos / "2019" / "goa").mkdir(parents=True)
    (photos / "misc").mkdir()
    _photo(photos / "2019" / "goa" / "a.jpg", (200, 50, 50), "2019:05:12 10:20:30")
    shutil.copy(photos / "2019" / "goa" / "a.jpg", photos / "misc" / "a_copy.jpg")  # exact dup
    _photo(photos / "misc" / "IMG_20200102_030405.jpg", (50, 200, 50))                # date in name
    Image.new("RGBA", (300, 200), (0, 0, 255, 128)).save(photos / "misc" / "alpha.png")
    (photos / "misc" / "broken.jpg").write_bytes(b"not really a jpeg")
    (photos / "misc" / "notes.txt").write_text("ignored")

    s = Settings.load(tmp_path / "data", workers=2)
    db = Database(s.db_path)
    db.conn.execute("INSERT INTO sources(path, added_at) VALUES (?, ?)", (str(photos), int(time.time())))
    return photos, s, db


def _scan(db, s):
    m = ScanManager(db, s)
    assert m.start()
    assert m.wait(120)
    st = m.status()
    assert st["state"] == "done", st
    return st


def test_full_and_incremental_scan(lib):
    photos, s, db = lib
    st = _scan(db, s)
    assert st["discovered"] == 5 and st["new"] == 5 and st["indexed"] == 5 and st["errors"] == 1

    c = db.conn
    rows = {r["filename"]: r for r in c.execute("SELECT * FROM media")}
    a, dup = rows["a.jpg"], rows["a_copy.jpg"]
    assert a["status"] == DONE and a["sha256"] == dup["sha256"]
    assert a["date_source"] == "exif" and dates.wall_to_iso(a["taken_at"]) == "2019-05-12T10:20:30"
    assert a["camera_make"] == "TestCam" and (a["width"], a["height"]) == (640, 480)
    assert hamming(a["phash"], dup["phash"]) == 0

    named = rows["IMG_20200102_030405.jpg"]
    assert named["date_source"] == "filename"
    assert dates.wall_to_iso(named["taken_at"]) == "2020-01-02T03:04:05"
    assert rows["alpha.png"]["status"] == DONE
    assert rows["broken.jpg"]["status"] == ERROR and rows["broken.jpg"]["error"]

    h = a["sha256"].hex()
    assert (s.thumbs_dir / h[:2] / h[2:4] / f"{h}.webp").is_file()

    # FTS finds by folder name
    hits = c.execute("SELECT rowid FROM media_fts WHERE media_fts MATCH 'goa'").fetchall()
    assert [r[0] for r in hits] == [a["id"]]

    # Rescan with no changes: nothing new, nothing to index
    st = _scan(db, s)
    assert (st["new"], st["changed"], st["missing"], st["to_index"]) == (0, 0, 0, 0)

    # Change one file, delete one, remove a whole folder
    _photo(photos / "misc" / "IMG_20200102_030405.jpg", (10, 10, 10), size=(800, 600))
    (photos / "misc" / "alpha.png").unlink()
    shutil.rmtree(photos / "2019")
    st = _scan(db, s)
    assert st["changed"] == 1 and st["missing"] == 2 and st["indexed"] == 1
    assert c.execute("SELECT status FROM media WHERE filename='a.jpg'").fetchone()[0] == MISSING
    assert c.execute("SELECT width FROM media WHERE filename='IMG_20200102_030405.jpg'").fetchone()[0] == 800


def test_filename_dates():
    iso = lambda n: dates.wall_to_iso(dates.from_filename(n))
    assert iso("IMG-20190512-WA0001.jpg") == "2019-05-12T00:00:00"
    assert iso("PXL_20230101_101010123.jpg") == "2023-01-01T10:10:10"
    assert iso("Screenshot_2020-01-02-03-04-05.png") == "2020-01-02T03:04:05"
    assert dates.from_filename("DSC01234.JPG") is None
    assert dates.from_filename("20191345_bad.jpg") is None


def test_filter_by_sources(lib, tmp_path):
    from fastapi import HTTPException
    from photoexplorer.server import MediaFilter

    photos, s, db = lib
    other = tmp_path / "other"
    other.mkdir()
    _photo(other / "b.jpg", (20, 20, 220), "2021:01:01 09:00:00")
    db.conn.execute("INSERT INTO sources(path, added_at) VALUES (?, ?)", (str(other), int(time.time())))
    _scan(db, s)

    def names(sources):
        f = MediaFilter(source_id=None, sources=sources, folder_id=None, album_id=None, kind=None, q=None,
                        persons=None, persons_mode="all", date_from=None, date_to=None)
        where, args = f.where()
        return sorted(r[0] for r in db.conn.execute(
            f"SELECT m.filename FROM media m WHERE {' AND '.join(where)}", args))

    first, second = [r[0] for r in db.conn.execute("SELECT id FROM sources ORDER BY id")]
    assert names(None) == ["IMG_20200102_030405.jpg", "a.jpg", "a_copy.jpg", "alpha.png", "b.jpg"]
    assert names(str(second)) == ["b.jpg"]
    assert names(f"{first},{second}") == names(None)
    assert names("") == []
    with pytest.raises(HTTPException):
        names("1,x")


@pytest.mark.skipif(not (Settings(data_dir=None).ffmpeg and Settings(data_dir=None).ffprobe), reason="needs ffmpeg and ffprobe")
def test_video_details_without_ffprobe(tmp_path):
    """The portable app has no ffprobe: `ffmpeg -i` must give the same video details."""
    import json
    import subprocess
    from photoexplorer.scanner import extract
    s = Settings(data_dir=None)
    clip = tmp_path / "clip.mp4"
    subprocess.run([s.ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
                    "-metadata", "creation_time=2019-05-12T10:20:30Z", "-metadata", "location=+12.9716+077.5946/",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "plain.mp4")], check=True)
    subprocess.run([s.ffmpeg, "-v", "error", "-display_rotation", "90", "-i", str(tmp_path / "plain.mp4"),
                    "-c", "copy", str(clip)], check=True)
    want = json.loads(subprocess.run([s.ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                                      "-show_streams", str(clip)], capture_output=True).stdout)
    got = extract._probe_with_ffmpeg(s.ffmpeg, str(clip))

    def details(info):
        fmt, v = info["format"], next(x for x in info["streams"] if x["codec_type"] == "video")
        tags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
        rot = [sd["rotation"] for sd in v.get("side_data_list") or [] if "rotation" in sd]
        return (round(float(fmt["duration"]), 1), v["width"], v["height"], v["codec_name"], [abs(r) for r in rot],
                tags.get("creation_time", "")[:19], tags.get("location"))

    assert details(got) == details(want)
    assert details(got)[4] == [90] and details(got)[6] == "+12.9716+077.5946/"
