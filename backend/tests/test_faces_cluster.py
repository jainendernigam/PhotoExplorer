"""Clustering and people logic on synthetic embeddings (no models needed)."""
from __future__ import annotations

import time

import numpy as np
import pytest

from photoexplorer.db import Database
from photoexplorer.faces import people
from photoexplorer.faces.cluster import ClusterParams, graph_dbscan, knn, recluster

MODEL = "test-model"


def _people_vectors(rng, n_people, per_person, dim=128, spread=0.35):
    """Unit vectors around random centres: same-person cosine ~0.9, others ~0."""
    centres = rng.normal(size=(n_people, dim))
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    vecs, who = [], []
    for p in range(n_people):
        v = centres[p] + spread * rng.normal(size=(per_person, dim)) / np.sqrt(dim)
        vecs.append(v / np.linalg.norm(v, axis=1, keepdims=True))
        who += [p] * per_person
    return np.vstack(vecs).astype(np.float32), np.array(who)


def test_graph_dbscan_separates_people_and_noise():
    rng = np.random.default_rng(1)
    emb, who = _people_vectors(rng, 4, 30)
    noise = rng.normal(size=(5, 128)).astype(np.float32)
    noise /= np.linalg.norm(noise, axis=1, keepdims=True)
    sims, idx = knn(np.vstack([emb, noise]), 30)
    labels = graph_dbscan(sims, idx, 0.5, 3, np.ones(len(emb) + 5, bool))
    people_labels = labels[:120]
    for p in range(4):
        assert len(set(people_labels[who == p])) == 1 and people_labels[who == p][0] >= 0  # one cluster each
    assert len(set(people_labels)) == 4                                       # never merged
    assert (labels[120:] == -1).all()                                         # random faces = noise


def test_knn_numpy_matches_bruteforce(monkeypatch):
    import builtins
    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda name, *a, **k: (_ for _ in ()).throw(ImportError()) if name == "faiss"
                        else real_import(name, *a, **k))
    rng = np.random.default_rng(2)
    emb = rng.normal(size=(300, 16)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    sims, idx = knn(emb, 5)
    full = emb @ emb.T
    assert np.allclose(sims, -np.sort(-full, axis=1)[:, :5], atol=1e-5)


@pytest.fixture
def db(tmp_path):
    """Library with 3 people x 20 photos (one face each) + 10 photos with persons 0 and 1 together."""
    d = Database(tmp_path / "lib.db")
    c = d.conn
    rng = np.random.default_rng(3)
    emb, who = _people_vectors(rng, 3, 30)
    now = int(time.time())
    c.execute("INSERT INTO sources(id, path, added_at) VALUES (1, 'X:/p', ?)", (now,))
    c.execute("INSERT INTO folders(id, source_id, path) VALUES (1, 1, 'X:/p')")

    def photo(i):
        c.execute("INSERT INTO media(id, source_id, folder_id, path, filename, ext, kind, size, mtime_ns, "
                  "added_at, status, taken_at) VALUES (?, 1, 1, ?, ?, 'jpg', 'image', 1, 1, ?, 1, ?)",
                  (i, f"X:/p/{i}.jpg", f"{i}.jpg", now, 1_600_000_000 + i * 86400))

    def face(mid, ordinal, vec):
        c.execute("INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model) "
                  "VALUES (?, ?, 0, 0, .1, .1, .9, 100, 120, ?, ?)", (mid, ordinal, vec.tobytes(), MODEL))

    for i in range(60):                      # faces 0-19 -> person 0, 20-39 -> 1, 40-59 -> 2
        photo(i + 1)
        p, k = divmod(i, 20)
        face(i + 1, 0, emb[p * 30 + k])
    for j in range(10):                      # group photos with persons 0 and 1
        photo(100 + j)
        face(100 + j, 0, emb[20 + j])
        face(100 + j, 1, emb[30 + 20 + j])
    return d


def _cluster_of(c, media_id, ordinal=0):
    return c.execute("SELECT cluster_id FROM faces WHERE media_id = ? AND ordinal = ?",
                     (media_id, ordinal)).fetchone()[0]


def test_name_merge_reject_and_survive_recluster(db):
    c = db.conn
    res = recluster(c, MODEL, ClusterParams(eps_sim=0.5))
    assert res["faces"] == 80 and res["clusters"] == 3 and res["noise"] == 0

    # Name two clusters; same name twice merges into one person.
    me = people.name_cluster(c, _cluster_of(c, 1), "Me")["person_id"]
    kid = people.name_cluster(c, _cluster_of(c, 21), "Daughter")["person_id"]
    assert people.name_cluster(c, _cluster_of(c, 21), "daughter")["person_id"] == kid  # case-insensitive

    # A wrong face: user says face of photo 2 is not "Me".
    f2 = c.execute("SELECT id FROM faces WHERE media_id = 2").fetchone()[0]
    people.reject_faces(c, [f2], me)

    # New photos of "Me" arrive; re-clustering auto-names them, keeps rejection.
    rng = np.random.default_rng(3)
    emb, _ = _people_vectors(rng, 3, 30)
    now = int(time.time())
    for i in range(5):
        c.execute("INSERT INTO media(id, source_id, folder_id, path, filename, ext, kind, size, mtime_ns, "
                  "added_at, status, taken_at) VALUES (?, 1, 1, ?, ?, 'jpg', 'image', 1, 1, ?, 1, 0)",
                  (200 + i, f"X:/p/n{i}.jpg", f"n{i}.jpg", now))
        c.execute("INSERT INTO faces(media_id, ordinal, x, y, w, h, score, sharpness, size_px, embedding, model) "
                  "VALUES (?, 0, 0, 0, .1, .1, .9, 100, 120, ?, ?)", (200 + i, emb[25 + i].tobytes(), MODEL))
    res = recluster(c, MODEL, ClusterParams(eps_sim=0.5))
    assert res["auto_assigned"] == 5
    named = dict(c.execute("SELECT media_id, person_id FROM faces WHERE media_id >= 200").fetchall())
    assert set(named.values()) == {me}
    assert c.execute("SELECT person_id FROM faces WHERE id = ?", (f2,)).fetchone()[0] is None

    # "Me AND Daughter" -> only the 10 group photos; "any" -> all their photos.
    cond, args = people.media_filter_sql([me, kid], "all")
    both = [r[0] for r in c.execute(f"SELECT m.id FROM media m WHERE {cond}", args)]
    assert sorted(both) == list(range(100, 110))
    cond, args = people.media_filter_sql([me, kid], "any")
    assert c.execute(f"SELECT count(*) FROM media m WHERE {cond}", args).fetchone()[0] == 19 + 5 + 20 + 10

    # Merge, then delete
    other = people.name_cluster(c, _cluster_of(c, 41), "Someone")["person_id"]
    people.merge_persons(c, kid, [other])
    assert c.execute("SELECT count(*) FROM persons").fetchone()[0] == 2
    people.delete_person(c, kid)
    assert c.execute("SELECT count(*) FROM faces WHERE person_id IS NOT NULL").fetchone()[0] == 19 + 5 + 10


def test_leftover_faces_join_nearest_group():
    import numpy as np
    from photoexplorer.faces.cluster import attach_leftovers
    labels = np.array([0, 0, -1, -1])
    idx = np.array([[0, 1], [1, 0], [2, 0], [3, 1]])
    sims = np.array([[1, .9], [1, .9], [1, .40], [1, .20]], np.float32)
    assert attach_leftovers(labels, sims, idx, 0.37).tolist() == [0, 0, 0, -1]
