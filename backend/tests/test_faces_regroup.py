"""People fills in while faces are still being found, and after a stopped run."""
from __future__ import annotations

import threading
import time

from photoexplorer.config import Settings
from photoexplorer.db import Database
from photoexplorer.faces import pipeline


def test_regroups_during_and_after_stopped_run(tmp_path, monkeypatch):
    s = Settings.load(tmp_path / "data", workers=1)
    db = Database(s.db_path)
    c = db.conn
    c.execute("INSERT INTO sources(path, added_at) VALUES ('x', 0)")
    c.execute("INSERT INTO folders(source_id, path, scan_gen) VALUES (1, 'x', 0)")
    for i in range(6):
        c.execute("INSERT INTO media(source_id, folder_id, path, filename, ext, kind, size, mtime_ns, added_at, "
                  "status, sha256) VALUES (1, 1, ?, ?, 'jpg', 'image', 1, 1, 0, 1, ?)",
                  (f"x/{i}.jpg", f"{i}.jpg", bytes([i]) * 32))
    face = dict(ordinal=0, x=0, y=0, w=1, h=1, score=0.9, sharpness=1.0, size_px=100,
                embedding=b"\0" * 2048, chip=None)
    calls = []
    # first grouping is a full one, later ones only add new faces (assign_new): count both
    monkeypatch.setattr(pipeline, "recluster", lambda *a, **k: calls.append(1) or {"clusters": 1, "auto_assigned": 0})
    monkeypatch.setattr(pipeline, "assign_new", lambda *a, **k: calls.append(1) or {"new": 1})
    monkeypatch.setattr(pipeline, "REGROUP_EVERY_S", -1)

    def fake_pool(jobs, fn, on_results, **kw):
        for j in jobs:
            on_results([{"id": j[0], "faces": [face]}])
    monkeypatch.setattr(pipeline, "run_pool", fake_pool)
    st = {}
    ph = pipeline.FacePhase(s, threading.Event(), lambda **k: st.update(k), lambda **k: None)
    ph.run(c, None)
    assert len(calls) >= 6                      # regrouped as it went, not only at the end
    assert c.execute("SELECT count(*) FROM media WHERE faces_status = 1").fetchone()[0] == 6

    # A run stopped before its final grouping: the next run groups first.
    c.execute("UPDATE meta SET value = '-5' WHERE key = 'faces_clustered_max_id'")
    calls.clear()
    pipeline.FacePhase(s, threading.Event(), lambda **k: None, lambda **k: None).run(c, None)
    assert calls == [1]


def test_look_again_keeps_names(tmp_path, monkeypatch):
    s = Settings.load(tmp_path / "data", workers=1)
    db = Database(s.db_path)
    c = db.conn
    c.execute("INSERT INTO sources(path, added_at) VALUES ('x', 0)")
    c.execute("INSERT INTO folders(source_id, path, scan_gen) VALUES (1, 'x', 0)")
    c.execute("INSERT INTO media(source_id, folder_id, path, filename, ext, kind, size, mtime_ns, added_at, "
              "status, sha256, faces_status) VALUES (1, 1, 'x/a.jpg', 'a.jpg', 'jpg', 'image', 1, 1, 0, 1, ?, 1)",
              (b"a" * 32,))
    c.execute("INSERT INTO persons(name, created_at) VALUES ('Asha', 0)")
    c.execute("INSERT INTO persons(name, created_at) VALUES ('Ravi', 0)")
    c.execute("INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model, "
              "person_id, assigned_by) VALUES (1, 0, 0.10, 0.10, 0.2, 0.2, 0.9, 50, 100, ?, 'm', 1, 'user')",
              (b"\0" * 512,))
    c.execute("INSERT INTO face_rejections(face_id, person_id) VALUES (1, 2)")
    c.execute("UPDATE persons SET cover_face_id = 1 WHERE id = 1")
    monkeypatch.setattr(pipeline, "recluster", lambda *a, **k: {"clusters": 0, "auto_assigned": 0})
    found = [dict(ordinal=0, x=0.11, y=0.10, w=0.2, h=0.2, score=0.9, sharpness=50, size_px=100,
                  embedding=b"\0" * 512, chip=None),        # same face, box moved a little
             dict(ordinal=1, x=0.60, y=0.50, w=0.1, h=0.1, score=0.8, sharpness=30, size_px=50,
                  embedding=b"\0" * 512, chip=None)]        # a face missed the first time
    monkeypatch.setattr(pipeline, "run_pool",
                        lambda jobs, fn, on_results, **kw: [on_results([{"id": j[0], "faces": found}]) for j in jobs])
    assert pipeline.FacePhase.reset_for_recheck(c) == 1
    pipeline.FacePhase(s, threading.Event(), lambda **k: None, lambda **k: None).run(c, None)
    rows = c.execute("SELECT id, ordinal, person_id, assigned_by FROM faces ORDER BY ordinal").fetchall()
    assert len(rows) == 2
    assert (rows[0][2], rows[0][3]) == (1, "user") and rows[1][2] is None
    assert c.execute("SELECT face_id FROM face_rejections").fetchone()[0] == rows[0][0]
    assert c.execute("SELECT cover_face_id FROM persons WHERE id = 1").fetchone()[0] == rows[0][0]
