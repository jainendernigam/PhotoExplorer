"""End-to-end: scan -> detect faces -> cluster -> name -> filter photos.

Needs the face models (python -m photoexplorer models --download) and two
sample portraits in a folder named by PE_FACE_SAMPLES:
  person_a.jpg - one clear face (e.g. OpenCV's samples/data/lena.jpg)
  person_b.jpg - a different person (e.g. samples/data/messi5.jpg)
Skipped otherwise.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageEnhance, ImageOps

from photoexplorer.config import Settings
from photoexplorer.db import Database
from photoexplorer.faces import models, people
from photoexplorer.scanner.manager import ScanManager

SAMPLES = Path(os.environ.get("PE_FACE_SAMPLES", "__none__"))
pytestmark = pytest.mark.skipif(
    not models.available() or not (SAMPLES / "person_a.jpg").is_file(),
    reason="face models or PE_FACE_SAMPLES not available")


def _emb(c, media_id):
    rows = c.execute("SELECT embedding FROM faces WHERE media_id = ?", (media_id,)).fetchall()
    return [np.frombuffer(r[0], np.float32) for r in rows]


def test_faces_end_to_end(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    a = Image.open(SAMPLES / "person_a.jpg").convert("RGB")
    a.save(photos / "a1.jpg", quality=95)
    a.resize((a.width * 3 // 5, a.height * 3 // 5)).save(photos / "a2_small.jpg")
    ImageEnhance.Brightness(a).enhance(1.35).save(photos / "a3_bright.jpg")
    ImageOps.mirror(a).save(photos / "a4_mirror.jpg")
    # Stored rotated, with EXIF "rotate 90 CW to display": must be un-rotated before detection.
    exif = Image.Exif()
    exif[0x0112] = 6
    a.rotate(90, expand=True).save(photos / "a5_exif_rotated.jpg", exif=exif)
    shutil.copy(photos / "a1.jpg", photos / "a6_copy.jpg")                    # identical twin
    shutil.copy(SAMPLES / "person_b.jpg", photos / "b1.jpg")
    Image.new("RGB", (640, 480), (90, 140, 200)).save(photos / "sky.jpg")      # no face

    s = Settings.load(tmp_path / "data", workers=2)
    db = Database(s.db_path)
    c = db.conn
    c.execute("INSERT INTO sources(path, added_at) VALUES (?, ?)", (str(photos), int(time.time())))
    m = ScanManager(db, s)
    m.start()
    assert m.wait(300)
    st = m.status()
    assert st["state"] == "done", st
    assert st["indexed"] == 8 and st["errors"] == 0
    assert st["faces_copied"] == 1                       # a6_copy reused a1's faces
    assert st["faces_errors"] == 0

    ids = dict(c.execute("SELECT filename, id FROM media").fetchall())
    a_names = ["a1.jpg", "a2_small.jpg", "a3_bright.jpg", "a4_mirror.jpg", "a5_exif_rotated.jpg", "a6_copy.jpg"]
    for n in a_names:
        assert len(_emb(c, ids[n])) == 1, n
    assert len(_emb(c, ids["sky.jpg"])) == 0
    assert len(_emb(c, ids["b1.jpg"])) >= 1
    assert c.execute("SELECT count(*) FROM media WHERE faces_status != 1 AND kind = 'image'").fetchone()[0] == 0

    ref = _emb(c, ids["a1.jpg"])[0]
    same = [float(ref @ _emb(c, ids[n])[0]) for n in a_names[1:]]
    other = max(float(ref @ e) for e in _emb(c, ids["b1.jpg"]))
    print("same-person similarities", [round(x, 3) for x in same], "other person", round(other, 3))
    assert min(same) > 0.6 and other < 0.3

    # Face box is in display orientation: the rotated copy's box matches the original's.
    box = lambda n: c.execute("SELECT x, y, w, h FROM faces WHERE media_id = ?", (ids[n],)).fetchone()
    assert np.allclose(box("a1.jpg"), box("a5_exif_rotated.jpg"), atol=0.03)

    # One cluster for all six A photos; name it, then filter photos by person.
    clusters = {r[0] for r in c.execute(
        f"SELECT cluster_id FROM faces WHERE media_id IN ({','.join(str(ids[n]) for n in a_names)})")}
    assert len(clusters) == 1 and clusters.pop() >= 0
    pid = people.name_cluster(c, c.execute("SELECT cluster_id FROM faces WHERE media_id = ?",
                                           (ids["a1.jpg"],)).fetchone()[0], "Person A")["person_id"]
    cond, args = people.media_filter_sql([pid])
    found = {r[0] for r in c.execute(f"SELECT m.filename FROM media m WHERE {cond}", args)}
    assert found == set(a_names)

    chip = c.execute("SELECT chip FROM faces WHERE media_id = ?", (ids["a1.jpg"],)).fetchone()[0]
    assert (s.thumbs_dir / chip).is_file()
