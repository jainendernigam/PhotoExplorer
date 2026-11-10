"""Viewer tools: photo details, animation frames, print PDFs, opened files."""
from __future__ import annotations

import os

from PIL import Image

from photoexplorer import printing, system, viewer_tools


def _jpeg(path, **exif_fields):
    img = Image.new("RGB", (640, 480), (30, 120, 200))
    ex = Image.Exif()
    ex[0x010F] = "Canon"
    ex[0x0110] = "EOS 80D"
    sub = ex.get_ifd(0x8769)
    sub[0x829D] = 2.8          # FNumber
    sub[0x829A] = 0.004        # ExposureTime 1/250
    sub[0x8827] = 400          # ISO
    sub[0x920A] = 35.0         # FocalLength
    sub[0x9209] = 16           # Flash: no flash
    img.save(path, exif=ex, quality=90)


def test_details_reads_camera_settings(tmp_path):
    p = tmp_path / "a.jpg"
    _jpeg(p)
    d = viewer_tools.details(str(p), "image")
    s = d["settings"]
    assert s["Aperture"] == "f/2.8" and s["Shutter"] == "1/250 s" and s["ISO"] == "400"
    assert s["Focal length"].startswith("35 mm") and s["Flash"].startswith("No flash")
    assert d["tags"]["Make"] == "Canon" and d["format"] == "JPEG"
    assert viewer_tools.details(str(tmp_path / "missing.jpg"), "image").get("error")


def test_frames_of_a_gif(tmp_path):
    p = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (64, 48), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=[100, 200, 300], loop=0)
    info = viewer_tools.frames_info(str(p))
    assert info["count"] == 3 and info["durations"] == [100, 200, 300]
    assert viewer_tools.frame(str(p), 1).convert("RGB").getpixel((10, 10))[1] > 200
    saved = viewer_tools.save_frames(str(p), str(tmp_path / "out"), None)
    assert [os.path.basename(x) for x in saved] == ["anim frame 001.png", "anim frame 002.png", "anim frame 003.png"]
    assert len(viewer_tools.save_frames(str(p), str(tmp_path / "one"), [2])) == 1


def test_print_layouts(tmp_path):
    items = []
    for i in range(7):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (800, 600) if i % 2 else (600, 800), (i * 30, 90, 160)).save(p)
        items.append({"id": i, "path": str(p), "kind": "image", "filename": p.name, "taken_at": 1_600_000_000})
    for layout, pages in (("full", 7), ("two", 4), ("four", 2), ("contact", 1)):
        out = tmp_path / f"{layout}.pdf"
        n = printing.render(None, items, out, "a4", layout, captions=True)
        assert n == pages and out.read_bytes()[:4] == b"%PDF"
        assert printing.page_count(7, layout) == pages


def test_folder_images_and_preview(tmp_path):
    for n in ("b.jpg", "a.png", "notes.txt", "c.webp"):
        if n.endswith(".txt"):
            (tmp_path / n).write_text("x")
        else:
            Image.new("RGB", (300, 200), (200, 10, 10)).save(tmp_path / n)
    files = viewer_tools.folder_images(str(tmp_path / "b.jpg"))
    assert [os.path.basename(f) for f in files] == ["a.png", "b.jpg", "c.webp"]
    data = viewer_tools.loose_preview(str(tmp_path / "a.png"), 128)
    assert data[:2] == b"\xff\xd8"


def test_open_with_status_without_exe(monkeypatch):
    monkeypatch.delenv("PHOTOEXPLORER_EXE", raising=False)
    st = system.open_with_status()
    assert st["available"] is False and st["enabled"] is False


def test_no_request_body_is_read_as_a_query(tmp_path):
    """Request models defined inside create_app become query fields; keep them at module level."""
    from fastapi.routing import APIRoute
    from photoexplorer.config import Settings
    from photoexplorer.server import create_app
    app = create_app(Settings.load(tmp_path / "data", workers=1, faces_enabled=False), "t")
    bad = [r.path for r in app.routes if isinstance(r, APIRoute)
           and any(q.name == "body" for q in r.dependant.query_params)]
    assert not bad, bad
