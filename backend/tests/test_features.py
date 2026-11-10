"""Favourites, archive, notes, places, memories strip, categories and pets."""
from __future__ import annotations

import time
from datetime import date, datetime

import numpy as np
import pytest

from photoexplorer import memories, places
from photoexplorer.config import Settings
from photoexplorer.db import Database
from photoexplorer.scanner.dates import wall_seconds
from photoexplorer.smart import clip as clipmod
from photoexplorer.smart.phase import group_pets


@pytest.fixture
def lib(tmp_path):
    s = Settings.load(tmp_path / "data", workers=1, faces_enabled=False)
    db = Database(s.db_path)
    c = db.conn
    c.execute("INSERT INTO sources(path, added_at) VALUES ('x', 0)")
    c.execute("INSERT INTO folders(source_id, path, scan_gen) VALUES (1, 'x', 0)")
    return s, db


def add(c, i, when: datetime, lat=None, lon=None, name=None):
    c.execute("INSERT INTO media(source_id, folder_id, path, filename, ext, kind, size, mtime_ns, added_at, status, "
              "sha256, taken_at, gps_lat, gps_lon, width, height, has_thumb) "
              "VALUES (1, 1, ?, ?, 'jpg', 'image', 1, 1, 0, 1, ?, ?, ?, ?, 4000, 3000, 1)",
              (f"x/{name or i}.jpg", f"{name or i}.jpg", bytes([i % 256, i // 256]) * 16, wall_seconds(when), lat, lon))


def endpoint(app, path, method="GET"):
    return next(r.endpoint for r in app.routes if getattr(r, "path", "") == path and method in getattr(r, "methods", {method}))


def test_fav_archive_note_and_filters(lib):
    from photoexplorer.features import MarkIn, NoteIn
    from photoexplorer.server import MediaFilter, create_app
    s, db = lib
    for i in range(1, 6):
        add(db.conn, i, datetime(2020, 1, i))
    app = create_app(s, "t")
    endpoint(app, "/api/media/mark", "POST")(MarkIn(media_ids=[1, 2], favorite=True))
    endpoint(app, "/api/media/mark", "POST")(MarkIn(media_ids=[3], archived=True))
    endpoint(app, "/api/media/{media_id}/note", "POST")(4, NoteIn(note="Grandma's garden party"))

    def ids(**kw):
        where, args = MediaFilter(**kw).where()
        return sorted(r[0] for r in db.conn.execute(f"SELECT m.id FROM media m WHERE {' AND '.join(where)}", args))
    assert ids() == [1, 2, 4, 5]                     # archived photo left out
    assert ids(show="archive") == [3]
    assert ids(fav=True) == [1, 2]
    assert ids(q="garden") == [4]                    # notes are searchable


def test_places_and_trips(lib):
    s, db = lib
    near = places.nearest(19.07, 72.88)              # Mumbai
    assert near["country"] == "India" and near["km"] < 30
    c = db.conn
    k = 0
    for d in range(1, 26):                            # home: Mumbai
        k += 1; add(c, k, datetime(2023, 3, d), 19.07, 72.88)
    for d in range(10, 13):                           # three days in Goa
        for h in range(6):
            k += 1; add(c, k, datetime(2023, 5, d, 9 + h), 15.49, 73.83)
    lst = places.place_list(c)
    assert lst[0]["count"] == 25 and len(lst) == 2
    assert len(places.place_ids(c, lst[1]["id"])) == 18
    cards = memories.strip(c, date(2024, 5, 11))
    kinds = {x["kind"] for x in cards}
    assert "on_this_day" in kinds and "trip" in kinds
    trip = next(x for x in cards if x["kind"] == "trip")
    assert trip["title"].startswith("Trip to") and len(trip["media_ids"]) == 18
    assert all(x["media_ids"] for x in cards)


def test_best_of_year_and_rediscover(lib):
    s, db = lib
    k = 0
    for m in range(1, 13):
        for d in range(1, 25, 2):
            k += 1; add(db.conn, k, datetime(2021, m, d))
    cards = memories.strip(db.conn, date(2025, 8, 1))
    best = next(x for x in cards if x["kind"] == "best_of_year")
    assert best["title"] == "Best of 2021" and len(best["media_ids"]) == 36
    assert any(x["kind"] == "rediscover" for x in cards)


def test_pets_grouped_and_names_kept(lib):
    s, db = lib
    c = db.conn
    rng = np.random.default_rng(1)
    a, b = rng.normal(size=512), rng.normal(size=512)
    for i in range(1, 9):
        add(c, i, datetime(2022, 1, i))
        base = a if i <= 5 else b
        v = (base + rng.normal(scale=0.15, size=512)).astype(np.float32)
        v /= np.linalg.norm(v)
        c.execute("INSERT INTO media_clip(media_id, embedding) VALUES (?, ?)", (i, v.tobytes()))
        c.execute("INSERT INTO media_tags(media_id, tag, score) VALUES (?, 'dog', 0.9)", (i,))
    group_pets(c)
    groups = [r[0] for r in c.execute("SELECT DISTINCT cluster FROM pet_media WHERE cluster >= 0")]
    assert len(groups) == 2
    big = c.execute("SELECT cluster FROM pet_media WHERE media_id = 1").fetchone()[0]
    pid = c.execute("INSERT INTO pets(name, species, created_at) VALUES ('Bruno', 'dog', 0)").lastrowid
    c.execute("UPDATE pet_media SET pet_id = ? WHERE cluster = ?", (pid, big))
    group_pets(c)                                       # regrouping keeps the name
    assert c.execute("SELECT count(*) FROM pet_media WHERE pet_id = ?", (pid,)).fetchone()[0] == 5


def test_clip_tokenizer_matches_openai(tmp_path):
    """Known CLIP token ids for a short phrase (needs the downloaded model)."""
    import os
    d = os.environ.get("PE_CLIP_DATA")
    if not d or clipmod.missing(d):
        pytest.skip("CLIP model not downloaded")
    tok = clipmod.Tokenizer(clipmod.model_dir(d) / "tokenizer.json")
    assert tok.encode("a photo of a dog") == [49406, 320, 1125, 539, 320, 1929, 49407]
