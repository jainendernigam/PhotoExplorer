"""Main colours (placeholder while a thumbnail loads), Undo data from mark and
album add, and the id list behind Ctrl+A."""
from __future__ import annotations

from datetime import datetime

from PIL import Image

from photoexplorer.scanner.colors import backfill, color_of
from test_features import add, endpoint, lib  # noqa: F401  (fixture)


def test_color_backfill_from_thumbnails(lib):
    s, db = lib
    c = db.conn
    for i in (1, 2):
        add(c, i, datetime(2024, 1, i))
    sha = c.execute("SELECT sha256 FROM media WHERE id = 1").fetchone()[0]
    h = sha.hex()
    tp = s.thumbs_dir / h[:2] / h[2:4] / f"{h}.webp"
    tp.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), (200, 100, 50)).save(tp, "WEBP", lossless=True)
    assert color_of(Image.new("RGB", (8, 8), (1, 2, 3))) == 0x010203
    assert backfill(s.db_path, s.thumbs_dir) == 1          # media 2 has no thumbnail file: stays empty
    got = c.execute("SELECT color FROM media ORDER BY id").fetchall()
    assert got[0][0] == (200 << 16) | (100 << 8) | 50 and got[1][0] is None
    assert backfill(s.db_path, s.thumbs_dir) == 0          # nothing left to do


def test_mark_returns_changed_ids_and_album_add_new_ids(lib):
    from photoexplorer.features import MarkIn
    from photoexplorer.server import AlbumIn, AlbumItemsIn, create_app
    s, db = lib
    for i in (1, 2, 3):
        add(db.conn, i, datetime(2024, 1, i))
    app = create_app(s, "t")
    mark = endpoint(app, "/api/media/mark", "POST")
    assert mark(MarkIn(media_ids=[1, 2], favorite=True))["ids"] == [1, 2]
    r = mark(MarkIn(media_ids=[1, 2, 3], favorite=True))   # 1 and 2 already are: only 3 changes
    assert r == {"changed": 1, "ids": [3]}
    a = endpoint(app, "/api/albums", "POST")(AlbumIn(name="A", media_ids=[1]))
    r = endpoint(app, "/api/albums/{album_id}/add", "POST")(a["id"], AlbumItemsIn(media_ids=[1, 2, 3]))
    assert r == {"added": 2, "added_ids": [2, 3]}
