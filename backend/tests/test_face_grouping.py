"""Face grouping tests on synthetic face fingerprints (no photos or models needed).

    python -m pytest backend/tests/test_face_grouping.py
    python backend/tests/test_face_grouping.py        # without pytest
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from photoexplorer.db import Database  # noqa: E402
from photoexplorer.faces import arcface  # noqa: E402
from photoexplorer.faces import people, pipeline  # noqa: E402
from photoexplorer.scanner import moves  # noqa: E402
from photoexplorer.faces.cluster import (  # noqa: E402
    attach_leftovers, graph_dbscan, group_faces, knn, merge_alike, not_a_face, one_per_photo, same_photo_share,
    split_loose)

D = 512


def _unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v, axis=-1, keepdims=True)).astype(np.float32)


def _person(rng, centre: np.ndarray, n: int, spread: float = 0.9) -> np.ndarray:
    """n faces of one person: the centre plus noise (cosine to centre ~0.75)."""
    return _unit(centre + spread * rng.standard_normal((n, D)) / np.sqrt(D))


def _two_people_with_bridge(rng):
    """Two people, joined by a few blurry in-between faces - the case that
    used to end up as one group."""
    a, b = _unit(rng.standard_normal(D)), _unit(rng.standard_normal(D))
    fa, fb = _person(rng, a, 60), _person(rng, b, 60)
    mids = _unit(np.stack([(1 - t) * a + t * b for t in (0.35, 0.42, 0.5, 0.58, 0.65)]))
    bridge = np.vstack([_person(rng, m, 3) for m in mids])
    emb = np.vstack([fa, fb, bridge])
    who = np.r_[np.zeros(60, int), np.ones(60, int), np.full(len(bridge), -1)]
    return emb, who


def test_one_per_photo_keeps_one_face_per_photo():
    rng = np.random.default_rng(0)
    emb = _person(rng, _unit(rng.standard_normal(D)), 6)
    labels = np.zeros(6, np.int64)
    media = np.array([1, 1, 2, 3, 3, 3])
    out = one_per_photo(labels, emb, media, np.zeros(6, bool))
    for m in (1, 3):
        assert (out[media == m] >= 0).sum() == 1
    assert out[2] == 0


def test_one_per_photo_never_drops_user_confirmed_face():
    rng = np.random.default_rng(1)
    emb = _person(rng, _unit(rng.standard_normal(D)), 3)
    keep = np.array([False, False, True])
    out = one_per_photo(np.zeros(3, np.int64), emb, np.array([7, 7, 7]), keep)
    assert out[2] == 0 and (out >= 0).sum() == 1


def test_split_loose_breaks_a_chain_between_two_people():
    rng = np.random.default_rng(2)
    emb, who = _two_people_with_bridge(rng)
    sims, idx = knn(emb, 40)
    core = np.ones(len(emb), bool)
    base = graph_dbscan(sims, idx, 0.42, 3, core)
    a, b = base[who == 0], base[who == 1]
    assert np.bincount(a[a >= 0]).argmax() == np.bincount(b[b >= 0]).argmax(), "fixture should chain"
    out = attach_leftovers(split_loose(base, emb, core, 0.42, 3, 40, 0.40), sims, idx, 0.36)
    la, lb = out[who == 0], out[who == 1]
    ga, gb = np.bincount(la[la >= 0]).argmax(), np.bincount(lb[lb >= 0]).argmax()
    assert ga != gb
    assert (la == ga).mean() > 0.9 and (lb == gb).mean() > 0.9


def test_split_loose_splits_groups_with_same_photo_faces():
    rng = np.random.default_rng(3)
    emb, who = _two_people_with_bridge(rng)
    media = np.arange(len(emb))
    media[60:120] = media[:60]          # A and B are photographed together every time
    sims, idx = knn(emb, 40)
    core = np.ones(len(emb), bool)
    base = graph_dbscan(sims, idx, 0.42, 3, core)
    out = split_loose(base, emb, core, 0.42, 3, 40, 0.0, media)   # cohesion check off
    for lab in np.unique(out[out >= 0]):
        assert same_photo_share(media[out == lab]) <= 0.02


def test_split_loose_leaves_a_clean_person_alone():
    rng = np.random.default_rng(4)
    emb = _person(rng, _unit(rng.standard_normal(D)), 80)
    sims, idx = knn(emb, 40)
    core = np.ones(80, bool)
    base = graph_dbscan(sims, idx, 0.42, 3, core)
    out = split_loose(base, emb, core, 0.42, 3, 40, 0.40, np.arange(80))
    assert (out == 0).mean() > 0.95


def test_merge_alike_joins_one_person_split_in_two():
    rng = np.random.default_rng(6)
    a, b = _unit(rng.standard_normal(D)), _unit(rng.standard_normal(D))
    emb = np.vstack([_person(rng, a, 30), _person(rng, a, 30), _person(rng, b, 30)])
    labels = np.r_[np.zeros(30, int), np.ones(30, int), np.full(30, 2)]
    out = merge_alike(labels, emb, 0.8, np.arange(90))
    assert len(set(out[:60])) == 1          # the two halves of A are one group again
    assert out[0] != out[60]                # B stays apart


def test_merge_alike_refuses_when_photos_would_repeat():
    rng = np.random.default_rng(7)
    a = _unit(rng.standard_normal(D))
    emb = np.vstack([_person(rng, a, 30), _person(rng, a, 30)])
    media = np.r_[np.arange(30), np.arange(30)]   # every photo shows a face from each group
    out = merge_alike(np.r_[np.zeros(30, int), np.ones(30, int)], emb, 0.8, media)
    assert out[0] != out[30]


def test_not_a_face_uses_quality_then_detector_score():
    p = pipeline.cluster_params("arcface_l")
    score = np.array([0.95, 0.95, 0.70, 0.70, 0.95])
    quality = np.array([22.0, 12.0, 22.0, np.nan, np.nan])
    assert not_a_face(score, quality, p).tolist() == [False, True, False, True, False]


def test_group_faces_leaves_junk_out_but_keeps_anchored_faces():
    rng = np.random.default_rng(8)
    person = _person(rng, _unit(rng.standard_normal(D)), 20)
    junk = _person(rng, _unit(rng.standard_normal(D)), 20)      # tight group of non-faces
    emb = np.vstack([person, junk])
    n = len(emb)
    quality = np.r_[np.full(20, 22.0), np.full(20, 12.0)]
    anchor = np.zeros(n, bool)
    anchor[25] = True                                            # the user said this one is a face
    lab = group_faces(emb, np.arange(n), np.full(n, 200), np.full(n, 500.0), np.full(n, 0.9),
                      quality, anchor, anchor.copy(), pipeline.cluster_params("arcface_l"))
    assert (lab[:20] >= 0).all() and len(set(lab[:20])) == 1
    assert (np.delete(lab[20:], 5) == -1).all()


# ------------------------------------------------------------ database level

def _library(tmp: Path, faces: dict[str, np.ndarray]) -> sqlite3.Connection:
    """New library (all migrations) with one photo per face, faces tagged by model."""
    db = Database(tmp / "library.db")
    c = db.conn
    now = int(time.time())
    c.execute("INSERT INTO sources(id, path, added_at) VALUES (1, 'C:/p', ?)", (now,))
    c.execute("INSERT INTO folders(id, source_id, path) VALUES (1, 1, 'C:/p')")
    mid = 0
    for model, emb in faces.items():
        for v in emb:
            mid += 1
            c.execute("INSERT INTO media(id, source_id, folder_id, path, filename, ext, kind, size, mtime_ns, "
                      "status, added_at, faces_status) VALUES (?, 1, 1, ?, ?, 'jpg', 'image', 1, 1, 1, ?, 1)",
                      (mid, f"C:/p/{mid}.jpg", f"{mid}.jpg", now))
            c.execute("INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model) "
                      "VALUES (?, 0, 0, 0, .1, .1, .9, 100, 200, ?, ?)", (mid, v.tobytes(), model))
    return c


def test_cluster_all_groups_every_model_in_its_own_id_range():
    rng = np.random.default_rng(5)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:  # Windows: db file still open
        c = _library(Path(t), {
            arcface.TAGS["sface"]: _person(rng, _unit(rng.standard_normal(D)), 20)[:, :128].copy(),
            arcface.TAGS["arcface_l"]: _person(rng, _unit(rng.standard_normal(D)), 20),
        })
        # SFace fingerprints are 128 numbers: re-normalise the cut vectors
        for fid, blob in c.execute("SELECT id, embedding FROM faces WHERE model = ?",
                                   (arcface.TAGS["sface"],)).fetchall():
            v = np.frombuffer(blob, np.float32)
            c.execute("UPDATE faces SET embedding = ? WHERE id = ?", (_unit(v).tobytes(), fid))
        res = pipeline.cluster_all(c)
        assert res["faces"] == 40
        ids = {m: {r[0] for r in c.execute("SELECT cluster_id FROM faces WHERE model = ?", (m,))}
               for m in (arcface.TAGS["sface"], arcface.TAGS["arcface_l"])}
        assert all(None not in s for s in ids.values())
        grouped = [s - {-1} for s in ids.values()]
        assert all(grouped) and not (grouped[0] & grouped[1])


def test_failed_photo_is_retried_then_marked_error():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:  # Windows: db file still open
        c = _library(Path(t), {})
        now = int(time.time())
        c.execute("INSERT INTO sources(id, path, added_at) VALUES (2, 'C:/q', ?)", (now,))
        c.execute("INSERT INTO folders(id, source_id, path) VALUES (2, 2, 'C:/q')")
        c.execute("INSERT INTO media(id, source_id, folder_id, path, filename, ext, kind, size, mtime_ns, "
                  "status, added_at) VALUES (99, 2, 2, 'C:/q/x.jpg', 'x.jpg', 'jpg', 'image', 1, 1, 1, ?)", (now,))
        phase = pipeline.FacePhase.__new__(pipeline.FacePhase)
        phase._inc = lambda **kw: None
        phase.tag = arcface.TAGS["sface"]
        seen = []
        for _ in range(pipeline.MAX_TRIES):
            phase._write(c, [{"id": 99, "error": "boom"}])
            seen.append(c.execute("SELECT faces_status, faces_tries, faces_error FROM media WHERE id = 99").fetchone())
        assert [s[0] for s in seen] == [0] * (pipeline.MAX_TRIES - 1) + [2]
        assert seen[-1][1] == pipeline.MAX_TRIES and seen[-1][2] == "boom"
        phase._write(c, [{"id": 99, "faces": []}])
        assert tuple(c.execute("SELECT faces_status, faces_tries, faces_error FROM media WHERE id = 99").fetchone()) \
            == (1, 0, None)


def test_arcface_falls_back_to_sface_without_onnxruntime(monkeypatch=None):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:  # Windows: db file still open
        data = Path(t)
        f = arcface.model_file(data, "arcface_l")
        f.parent.mkdir(parents=True)
        f.write_bytes(b"model")
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        arcface.set_current(c, "arcface_l")
        real = arcface.runtime_ok
        try:
            arcface.runtime_ok = lambda: False
            assert arcface.current(c, data) == "sface" and arcface.installed(data) == []
            arcface.runtime_ok = lambda: True
            assert arcface.current(c, data) == "arcface_l"
        finally:
            arcface.runtime_ok = real


# ------------------------------------------------------------ moved / renamed folders

def _media(c, mid, path, status, size=1000, mtime=5, sha=None):
    c.execute("INSERT INTO media(id, source_id, folder_id, path, filename, ext, kind, size, mtime_ns, status, "
              "added_at, sha256) VALUES (?, 1, 1, ?, ?, 'jpg', 'image', ?, ?, ?, 0, ?)",
              (mid, path, path.rsplit("/", 1)[1], size, mtime, status, sha))


def test_renamed_folder_keeps_names_and_hidden_stays_hidden():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        c = _library(Path(t), {})
        c.execute("INSERT INTO persons(id, name, created_at) VALUES (1, 'Asha', 0)")
        _media(c, 1, f"{t}/old/a.jpg", 3, sha=b"x" * 32)            # missing after the rename
        c.execute("INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model, "
                  "person_id, assigned_by) VALUES (1, 0, 0, 0, .1, .1, .9, 100, 200, x'00', 'm', 1, 'user')")
        _media(c, 2, f"{t}/old/dup.jpg", 5, size=7)                  # removed from the app only
        _media(c, 10, f"{t}/new/a.jpg", 0)
        _media(c, 11, f"{t}/new/dup.jpg", 0, size=7)
        _media(c, 12, f"{t}/new/other.jpg", 0, size=8)               # really new
        pairs = moves.find_moves(c, 10)
        assert sorted(pairs) == [(1, 10), (2, 11)]
        assert moves.relink(c, pairs) == 2
        r = c.execute("SELECT path, status FROM media WHERE id = 1").fetchone()
        assert r[0].endswith("new/a.jpg") and r[1] == 1              # indexed before: back to done
        assert c.execute("SELECT person_id FROM faces WHERE media_id = 1").fetchone()[0] == 1
        assert c.execute("SELECT status FROM media WHERE id = 2").fetchone()[0] == 5
        assert c.execute("SELECT count(*) FROM media WHERE id IN (10, 11)").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM media WHERE id = 12").fetchone()[0] == 1


def test_renamed_file_is_matched_by_content_after_indexing():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        c = _library(Path(t), {})
        _media(c, 1, f"{t}/old/IMG_1.jpg", 3, sha=b"h" * 32)
        _media(c, 5, f"{t}/new/Birthday.jpg", 1, mtime=99, sha=b"h" * 32)
        assert moves.find_moves(c, 5) == [(1, 5)]


# ------------------------------------------------------------ new faces without regrouping

def _faces_library(t, groups: dict, model="arcface-w600k-r50"):
    """groups: name -> (embeddings, cluster_id or None, person_id or None)."""
    c = _library(Path(t), {})
    c.execute("INSERT INTO persons(id, name, created_at) VALUES (7, 'Asha', 0)")
    ids, mid = {}, 0
    for name, (emb, cl, pid) in groups.items():
        ids[name] = []
        for v in emb:
            mid += 1
            _media(c, mid, f"C:/p/{mid}.jpg", 1)
            fid = c.execute("INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, "
                            "model, quality, cluster_id, person_id, assigned_by) VALUES "
                            "(?, 0, 0, 0, .1, .1, .95, 300, 200, ?, ?, 22, ?, ?, ?)",
                            (mid, v.tobytes(), model, cl, pid, "cluster" if pid else None)).lastrowid
            ids[name].append(fid)
    return c, ids


def test_new_faces_join_people_and_groups_without_regrouping():
    rng = np.random.default_rng(9)
    a, b, x = (_unit(rng.standard_normal(D)) for _ in range(3))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        c, ids = _faces_library(t, {
            "asha": (_person(rng, a, 20), 0, 7),          # named person
            "group": (_person(rng, b, 15), 1, None),      # unnamed group on screen
            "asha_new": (_person(rng, a, 4), None, None),
            "group_new": (_person(rng, b, 4), None, None),
            "stranger": (_person(rng, x, 6), None, None),
        })
        before = c.execute("SELECT id, cluster_id, person_id FROM faces WHERE cluster_id IS NOT NULL").fetchall()
        res = pipeline.cluster_all(c)
        assert res["new"] == 14 and res["auto_assigned"] == 4
        assert c.execute("SELECT id, cluster_id, person_id FROM faces WHERE cluster_id IS NOT NULL "
                         "AND id <= ?", (max(ids["group"]),)).fetchall() == before     # untouched
        get = lambda name: c.execute(f"SELECT cluster_id, person_id FROM faces WHERE id IN "
                                     f"({','.join(map(str, ids[name]))})").fetchall()
        assert all(p == 7 for _, p in get("asha_new"))
        assert all(cl == 1 and p is None for cl, p in get("group_new"))
        st = get("stranger")
        assert len({cl for cl, _ in st}) == 1 and st[0][0] not in (0, 1, -1) and st[0][1] is None


def test_doubtful_new_face_is_suggested_not_added():
    import dataclasses
    from photoexplorer.faces.cluster import assign_new
    rng = np.random.default_rng(10)
    a = _unit(rng.standard_normal(D))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        c, ids = _faces_library(t, {"asha": (_person(rng, a, 20), 0, 7), "maybe": (_person(rng, a, 1), None, None)})
        p = dataclasses.replace(pipeline.cluster_params("arcface_l"), sure_sim=0.99)   # nothing is "sure"
        assert assign_new(c, "arcface-w600k-r50", p)["suggested"] == 1
        fid = ids["maybe"][0]
        assert c.execute("SELECT person_id FROM faces WHERE id = ?", (fid,)).fetchone()[0] is None
        assert [s["face_id"] for s in people.suggestions(c, 7)] == [fid]
        people.assign_faces(c, [fid], 7)                                           # the user says yes
        assert people.suggestions(c, 7) == []


# ------------------------------------------------------------ memories music

def test_memories_get_fitting_music_and_own_songs_come_first():
    from photoexplorer.memories import add_music
    pack = [{"id": "nostalgia", "title": "Nostalgia", "mood": "nostalgic"},
            {"id": "celebration", "title": "Celebration", "mood": "festive"},
            {"id": "warm", "title": "Warm Memories", "mood": "warm"}]
    cards = lambda: [{"key": "otd-2018", "kind": "on_this_day", "subtitle": "9 Nov 2018"},
                     {"key": "person-1", "kind": "person", "subtitle": "Birthday 12 Nov"},
                     {"key": "pets", "kind": "pets", "subtitle": "40 photos"}]
    got = {c["kind"]: c["music"]["id"] for c in add_music(cards(), pack)}
    assert got["on_this_day"] == "nostalgia" and got["person"] == "celebration"
    assert got["pets"] in {t["id"] for t in pack}                     # no upbeat track: any track
    assert add_music(cards(), pack) == add_music(cards(), pack)       # same song each time
    own = pack + [{"id": "mine:song.mp3", "title": "song", "mood": ""}]
    assert all(c["music"]["id"] == "mine:song.mp3" for c in add_music(cards(), own))
    assert all(c["music"] is None for c in add_music(cards(), []))


# ------------------------------------------------------------ search by place

def test_place_names_match_spellings_but_not_ordinary_words():
    from photoexplorer.places import ALIASES, _close
    assert _close("simla", "simla") and _close(ALIASES["shimla"], "simla")
    assert _close("shimla", "simla")                     # one letter off
    assert _close("dehradun", "dehra dun") and _close("delhi", "new delhi") and _close("hima", "himachal pradesh")
    assert _close("goa", "goa")
    assert not _close("new", "new delhi") and not _close("cake", "calcutta") and not _close("par", "paris")


if __name__ == "__main__":
    tests = [(k, v) for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} passed")
