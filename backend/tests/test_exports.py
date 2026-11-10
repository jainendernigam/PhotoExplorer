"""Collages, slideshows (real FFmpeg when available) and photo selection."""
from __future__ import annotations

import json
import math
import random
import struct
import subprocess
import threading
import time
import wave

import pytest
from PIL import Image, ImageDraw

from photoexplorer.config import Settings
from photoexplorer.db import Database
from photoexplorer.exports import collage, frames, jobs, selection, slideshow
from photoexplorer.exports.jobs import ExportManager
from photoexplorer.scanner.manager import ScanManager

HAS_FFMPEG = bool(Settings(data_dir=None).ffmpeg)


def _photo(path, seed, size, when):
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        d.ellipse((x, y, x + 150, y + 150), fill=(rnd.randint(0, 255), 80, rnd.randint(0, 255)))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = when
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=90, exif=exif)


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("exp")
    photos = tmp / "photos"
    sizes = [(800, 600), (600, 800), (1000, 500), (700, 700)]
    for i in range(12):
        _photo(photos / f"p{i:02d}.jpg", i, sizes[i % 4], f"20{18 + i // 4}:{1 + i % 12:02d}:10 10:00:00")
    s = Settings.load(tmp / "data", workers=2, faces_enabled=False)
    db = Database(s.db_path)
    db.conn.execute("INSERT INTO sources(path, added_at) VALUES (?, ?)", (str(photos), int(time.time())))
    m = ScanManager(db, s)
    m.start()
    assert m.wait(120) and m.status()["state"] == "done"
    ids = [r[0] for r in db.conn.execute("SELECT id FROM media ORDER BY taken_at")]
    return tmp, s, db, ids


def test_selection_and_ages(lib):
    _, _, db, ids = lib
    items = selection.prepare(db.conn, list(reversed(ids)), max_photos=4)
    assert [i["id"] for i in items] == [ids[0], ids[4], ids[7], ids[11]]   # oldest first, evenly spread
    assert selection.age_text("2017-03-10", 1_500_000_000) == "4 months"        # 14 Jul 2017
    assert selection.age_text("2017-03-10", 1_489_000_000) == ""                # 8 Mar 2017: before birth
    assert selection.age_text("2017-03-10", 1_490_000_000) == "newborn"         # 20 Mar 2017
    assert selection.age_text("2015-01-01", 1_600_000_000) == "5 years 8 months"


def test_cover_crops_around_focus():
    img = Image.new("RGB", (400, 100), "black")
    img.paste((255, 255, 255), (300, 0, 400, 100))            # white block on the right
    out = frames.cover(img, 100, 100, focus=(0.875, 0.5))      # square crop centred on it
    assert out.getpixel((50, 50)) == (255, 255, 255)
    assert frames.cover(img, 100, 100).getpixel((50, 50)) == (0, 0, 0)   # default: centre


@pytest.mark.parametrize("layout", ["mosaic", "grid", "featured"])
def test_collage_layouts(lib, layout):
    tmp, _, db, ids = lib
    items = selection.prepare(db.conn, ids, 40)
    out = tmp / f"c_{layout}.jpg"
    collage.render(db.conn, items, collage.CollageOptions(layout=layout, size="landscape_4k", title="Test"),
                   out, lambda f, m: None, threading.Event())
    with Image.open(out) as im:
        assert im.size == (3840, 2160)
        # the photos cover the page: little plain background left
        bg = sum(1 for p in im.resize((96, 54)).get_flattened_data() if p == (255, 255, 255))
        assert bg < 96 * 54 * 0.25, bg


def test_mosaic_rects_fill_area_without_overlap():
    rects = collage._mosaic([1.33, 0.75, 2.0, 1.0, 1.5, 0.66, 1.33], (10, 10, 1000, 600), 8)
    assert all(w > 0 and h > 0 for _, _, w, h in rects)
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
    assert max(x + w for x, _, w, _ in rects) <= 1011 and max(y + h for _, y, _, h in rects) <= 611


def _probe(s, path):
    out = subprocess.run([s.ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return json.loads(out)


def _tone(path, seconds=2):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 22050)))
                               for i in range(22050 * seconds)))


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg not installed")
@pytest.mark.parametrize("chunk", [24, 2])           # 2 = exercise the long-slideshow (chunked) path
def test_slideshow_video(lib, monkeypatch, chunk):
    tmp, s, db, ids = lib
    monkeypatch.setattr(slideshow, "CHUNK", chunk)
    music = tmp / "tone.wav"
    _tone(music)                                      # shorter than the video: must loop
    items = selection.prepare(db.conn, ids, 5)
    o = slideshow.SlideshowOptions(seconds_per_photo=1.5, transition="mixed", transition_s=0.5, ken_burns=True,
                                   resolution=720, fps=25, music_path=str(music), title="Our year")
    out = tmp / f"show_{chunk}.mp4"
    seen = []
    slideshow.render(db.conn, s.ffmpeg, items, o, out, lambda f, m: seen.append(f), threading.Event())
    info = _probe(s, out)
    kinds = {st["codec_type"] for st in info["streams"]}
    v = next(st for st in info["streams"] if st["codec_type"] == "video")
    expected = 6 * 1.5 - 5 * 0.5                      # title + 5 photos
    assert kinds == {"video", "audio"} and (v["width"], v["height"]) == (1280, 720)
    assert abs(float(info["format"]["duration"]) - expected) < 0.25, info["format"]
    assert seen and max(seen) <= 1.0


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg not installed")
def test_job_queue_runs_and_cancels(lib):
    tmp, s, db, ids = lib
    mgr = ExportManager(db, s)
    out_dir = tmp / "out"
    out_dir.mkdir()
    j1 = mgr.submit("collage", ids, {"layout": "grid", "size": "square"}, "Grid / test", str(out_dir))
    j2 = mgr.submit("slideshow", ids * 20, {"seconds_per_photo": 2, "resolution": 720, "max_photos": 200}, "Long", None)
    for _ in range(300):
        st = db.conn.execute("SELECT status FROM exports WHERE id = ?", (j2,)).fetchone()[0]
        if st == "running":
            break
        time.sleep(0.1)
    mgr.cancel(j2)
    for _ in range(300):
        rows = dict(db.conn.execute("SELECT id, status FROM exports").fetchall())
        if rows[j1] in ("done", "error") and rows[j2] in ("cancelled", "error", "done"):
            break
        time.sleep(0.1)
    r1 = db.conn.execute("SELECT * FROM exports WHERE id = ?", (j1,)).fetchone()
    assert r1["status"] == "done", r1["message"]
    assert r1["output_path"].startswith(str(out_dir)) and "Grid _ test" in r1["output_path"]
    assert db.conn.execute("SELECT status FROM exports WHERE id = ?", (j2,)).fetchone()[0] == "cancelled"
    assert not list(s.exports_dir.glob(".tmp-*"))               # temp frames cleaned up
    with pytest.raises(ValueError):
        mgr.submit("collage", ids, {"no_such_option": 1}, None, None)


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg not installed")
@pytest.mark.parametrize("theme", ["vintage", "quick-reel", "cinematic", "family-album"])
def test_themed_slideshow(lib, theme):
    from photoexplorer.content import BUILTIN_THEMES
    tmp, s, db, ids = lib
    opts = dict(next(t for t in BUILTIN_THEMES if t["id"] == theme)["options"])
    opts.update(resolution=720, fps=25, seconds_per_photo=1.0, transition_s=0.3, max_photos=3, captions="date")
    o = jobs.parse_options("slideshow", opts)
    out = tmp / f"theme_{theme}.mp4"
    slideshow.render(db.conn, s.ffmpeg, selection.prepare(db.conn, ids, 3), o, out, lambda f, m: None, threading.Event())
    v = next(st for st in _probe(s, out)["streams"] if st["codec_type"] == "video")
    assert (v["width"], v["height"]) == slideshow.frame_size(720, o.aspect)


def test_looks_on_a_frame():
    from PIL import Image
    from photoexplorer.exports import looks
    img = Image.new("RGB", (400, 300), (200, 120, 60))
    for c in looks.COLORS:
        assert looks.color(img, c).size == img.size
    for f in looks.FRAMES:
        framed, written = looks.place(img, 640, 360, f, "#223344", "May 2019")
        assert framed.size == (640, 360) and written == (f in ("white", "print"))
    boxed = looks.letterbox(Image.new("RGB", (640, 360), (255, 255, 255)), True)
    assert boxed.getpixel((320, 2)) == (0, 0, 0) and boxed.getpixel((320, 180)) == (255, 255, 255)
    assert looks.color(img, "mono").getpixel((5, 5))[0] == looks.color(img, "mono").getpixel((5, 5))[2]


@pytest.mark.parametrize("layout", ["polaroid", "magazine", "story", "bubbles", "heart", "filmstrip", "year", "then_now"])
@pytest.mark.parametrize("size", ["landscape_4k", "phone"])
def test_collage_styles(lib, layout, size):
    tmp, _, db, ids = lib
    items = selection.prepare(db.conn, ids, 40)
    out = tmp / f"cs_{layout}_{size}.jpg"
    collage.render(db.conn, items, collage.CollageOptions(layout=layout, size=size, background="#fafafa"),
                   out, lambda f, m: None, threading.Event())
    with Image.open(out) as im:
        assert im.size == collage.PRESETS[size]
        px = list(im.resize((64, 64)).get_flattened_data())
        plain = sum(1 for p in px if max(abs(c - 250) for c in p) < 6)
        assert plain < len(px) * 0.9, plain                # photos were drawn


def test_slide_style_helpers(lib):
    from photoexplorer.exports import slide_styles as ss
    _, _, db, ids = lib
    items = selection.prepare(db.conn, ids, 40)
    groups = ss.pairs(items)
    assert sum(len(g) for g in groups) == len(items)
    assert all(len(g) == 1 or all(ss.is_portrait(x) for x in g) for g in groups)
    best = ss.highlights(db.conn, items, 4)
    assert len(best) == 4 and best == sorted(best, key=lambda i: i["taken_at"])
    assert abs(ss.beat_length(0.5, 1.6) - 1.5) < 1e-9 and ss.beat_length(0.5, 0.1) == 0.5
    trip = [dict(i, gps_lat=10 + k * 0.1, gps_lon=76.0) for k, i in enumerate(items)]
    assert ss.has_route(trip) and not ss.has_route(items)
    assert ss.trip_days(trip)[0] == 0
    img = Image.new("RGB", (800, 600), (90, 120, 200))
    assert ss.map_card(trip, 3, 1, None, img, 640, 360).size == (640, 360)
    assert ss.split(img, img, 640, 360).size == (640, 360)
    assert ss.big_title_card("Happy birthday Anna", "2025", 640, 360, "#c2185b", "#ffffff").size == (640, 360)


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg not installed")
@pytest.mark.parametrize("theme", ["beat-sync", "split-screen", "big-titles", "highlight-reel", "trip-map",
                                   "zoom-through", "year-in-review"])
def test_style_slideshows(lib, theme):
    from photoexplorer.content import BUILTIN_THEMES
    tmp, s, db, ids = lib
    music = tmp / "tone.wav"
    if not music.exists():
        _tone(music)
    opts = dict(next(t for t in BUILTIN_THEMES if t["id"] == theme)["options"])
    opts.update(resolution=720, fps=25, seconds_per_photo=1.0, transition_s=0.3, max_photos=6,
                music_path=str(music), title="Test")
    o = jobs.parse_options("slideshow", opts)
    out = tmp / f"style_{theme}.mp4"
    items = selection.prepare(db.conn, ids, 6)
    if theme == "trip-map":
        items = [dict(i, gps_lat=15 + k * 0.05, gps_lon=74.0) for k, i in enumerate(items)]
    slideshow.render(db.conn, s.ffmpeg, items, o, out, lambda f, m: None, threading.Event())
    info = _probe(s, out)
    assert float(info["format"]["duration"]) > 2


@pytest.mark.parametrize("layout", ["mosaic", "polaroid", "heart"])
def test_export_preview(lib, layout):
    import base64, io
    from photoexplorer.server import create_app, ExportIn
    _, s, db, ids = lib
    app = create_app(s, "t")
    prev = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/exports/preview")
    r = prev(ExportIn(kind="slideshow", media_ids=ids, options={"captions": "date", "max_photos": 3}))
    assert r["used"] <= 3 and all("caption" in it for it in r["items"])
    r = prev(ExportIn(kind="collage", media_ids=ids, options={"layout": layout, "size": "square", "title": "Hi"}))
    from PIL import Image as I
    im = I.open(io.BytesIO(base64.b64decode(r["image"].split(",", 1)[1])))
    assert im.size == (1100, 1100)
