"""Group faces into people.

DBSCAN on cosine similarity, made to scale to millions of faces: instead of
comparing every pair (n^2), each face's k nearest neighbours are found with
FAISS (HNSW index for big libraries; NumPy fallback when FAISS is missing),
and DBSCAN's rules are applied on that neighbour graph:

* a face is a *core* face if at least ``min_samples`` faces (itself included)
  are within ``eps_sim``, and it is large and sharp enough to trust;
* core faces linked within ``eps_sim`` form one cluster;
* other faces join the cluster of their most similar core neighbour, or stay
  unclustered (noise, cluster_id = -1).

Names survive re-clustering: faces the user confirmed ('user') or whose
cluster the user named ('cluster') are anchors. Each new cluster takes the
person that most of its anchors belong to; its other faces get that person
as 'auto' (unless the user rejected that face for that person).
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from ..db import transaction

log = logging.getLogger(__name__)


@dataclass
class ClusterParams:
    eps_sim: float = 0.42        # SFace: same person is typically > 0.36; links stay a bit stricter
    min_samples: int = 3
    k: int = 40
    core_min_px: int = 32        # faces smaller than this cannot seed clusters
    core_min_sharpness: float = 10.0
    join_sim: float = 0.37       # a left-over face joins the group of its closest grouped neighbour
    anchor_share: float = 0.6    # share of a cluster's anchors needed to name it


# ------------------------------------------------------------ neighbour graph

def knn(emb: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Top-k cosine neighbours of every row (rows must be L2-normalised).
    Returns (similarities, indices), both shape (n, k); index -1 = none."""
    n, d = emb.shape
    k = min(k, n)
    try:
        import faiss
    except ImportError:
        faiss = None
    if faiss is not None:
        if n > 50_000:
            index = faiss.IndexHNSWFlat(d, 32, faiss.METRIC_INNER_PRODUCT)
            index.hnsw.efConstruction = 80
            index.hnsw.efSearch = max(64, 2 * k)
        else:
            index = faiss.IndexFlatIP(d)
        index.add(emb)
        sims, idx = index.search(emb, k)
        return sims, idx.astype(np.int64)

    sims = np.empty((n, k), np.float32)
    idx = np.empty((n, k), np.int64)
    for s in range(0, n, 2048):
        block = emb[s:s + 2048] @ emb.T
        part = np.argpartition(-block, k - 1, axis=1)[:, :k]
        ps = np.take_along_axis(block, part, axis=1)
        order = np.argsort(-ps, axis=1)
        idx[s:s + 2048] = np.take_along_axis(part, order, axis=1)
        sims[s:s + 2048] = np.take_along_axis(ps, order, axis=1)
    return sims, idx


def _components(n: int, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Connected components (label = smallest node index), vectorised
    hook-and-shortcut so millions of edges need no Python loop."""
    lab = np.arange(n)
    if len(a) == 0:
        return lab
    while True:
        m = np.minimum(lab[a], lab[b])
        new = lab.copy()
        np.minimum.at(new, a, m)
        np.minimum.at(new, b, m)
        while True:
            jumped = new[new]
            if np.array_equal(jumped, new):
                break
            new = jumped
        if np.array_equal(new, lab):
            return lab
        lab = new


def graph_dbscan(sims: np.ndarray, idx: np.ndarray, eps_sim: float, min_samples: int,
                 core_ok: np.ndarray) -> np.ndarray:
    """Labels 0..C-1 (largest cluster first), -1 for noise."""
    n, k = idx.shape
    rows = np.repeat(np.arange(n), k)
    cols, s = idx.ravel(), sims.ravel()
    keep = (cols >= 0) & (cols != rows) & (s >= eps_sim)
    a, b, s = rows[keep], cols[keep], s[keep]

    deg = np.bincount(a, minlength=n) + 1          # +1: a point is its own neighbour
    core = (deg >= min_samples) & core_ok
    cc = core[a] & core[b]
    comp = _components(n, a[cc], b[cc])
    labels = np.where(core, comp, -1)

    # border faces: best core neighbour in either direction of the kNN graph
    ea, eb, es = np.concatenate([a, b]), np.concatenate([b, a]), np.concatenate([s, s])
    m = ~core[ea] & core[eb]
    ea, eb, es = ea[m], eb[m], es[m]
    if len(ea):
        order = np.lexsort((-es, ea))
        ea, eb = ea[order], eb[order]
        first = np.r_[True, ea[1:] != ea[:-1]]
        labels[ea[first]] = labels[eb[first]]

    # compact: 0 = biggest cluster
    valid = labels >= 0
    if not valid.any():
        return labels
    uniq, inv, counts = np.unique(labels[valid], return_inverse=True, return_counts=True)
    rank = np.empty(len(uniq), np.int64)
    rank[np.argsort(-counts, kind="stable")] = np.arange(len(uniq))
    out = np.full(n, -1, np.int64)
    out[valid] = rank[inv]
    return out


def attach_leftovers(labels: np.ndarray, sims: np.ndarray, idx: np.ndarray, join_sim: float) -> np.ndarray:
    """Faces left out of every group (side-on, blurred, small) join the group
    of their most similar grouped neighbour when it is close enough."""
    out = labels.copy()
    noise = np.flatnonzero(labels < 0)
    if len(noise) == 0 or not (labels >= 0).any():
        return out
    nb, ns = idx[noise], sims[noise]
    nl = np.where(nb >= 0, labels[np.clip(nb, 0, None)], -1)
    ok = (nl >= 0) & (ns >= join_sim)
    best = np.where(ok, ns, -np.inf).argmax(axis=1)
    has = ok[np.arange(len(noise)), best]
    out[noise[has]] = nl[np.arange(len(noise)), best][has]
    return out


# ------------------------------------------------------------ database step

def recluster(conn: sqlite3.Connection, model: str, params: ClusterParams | None = None) -> dict:
    p = params or ClusterParams()
    n = conn.execute(
        "SELECT count(*) FROM faces f JOIN media m ON m.id = f.media_id AND m.status = 1 WHERE f.model = ?",
        (model,)).fetchone()[0]
    if n == 0:
        return {"faces": 0, "clusters": 0, "auto_assigned": 0}

    ids = np.empty(n, np.int64)
    size_px = np.empty(n, np.int64)
    sharp = np.empty(n, np.float32)
    person = np.full(n, -1, np.int64)
    anchor = np.zeros(n, bool)
    emb = None
    cur = conn.execute(
        "SELECT f.id, f.embedding, f.size_px, f.sharpness, f.person_id, f.assigned_by "
        "FROM faces f JOIN media m ON m.id = f.media_id AND m.status = 1 WHERE f.model = ? ORDER BY f.id",
        (model,))
    i = 0
    for fid, blob, px, sh, pid, by in cur:
        if i >= n:
            break
        v = np.frombuffer(blob, np.float32)
        if emb is None:
            emb = np.empty((n, len(v)), np.float32)
        emb[i], ids[i], size_px[i], sharp[i] = v, fid, px, sh
        if pid is not None:
            person[i] = pid
            anchor[i] = by in ("user", "cluster")
        i += 1
    n = i
    emb, ids, size_px, sharp, person, anchor = emb[:n], ids[:n], size_px[:n], sharp[:n], person[:n], anchor[:n]

    sims, idx = knn(emb, p.k)
    core_ok = (size_px >= p.core_min_px) & (sharp >= p.core_min_sharpness)
    labels = graph_dbscan(sims, idx, p.eps_sim, p.min_samples, core_ok | anchor)
    labels = attach_leftovers(labels, sims, idx, p.join_sim)

    # Which person does each cluster belong to? Majority of its anchors.
    votes: dict[int, Counter] = defaultdict(Counter)
    for lab, pid in zip(labels[anchor], person[anchor]):
        if lab >= 0:
            votes[int(lab)][int(pid)] += 1
    winner: dict[int, int] = {}
    for lab, c in votes.items():
        pid, cnt = c.most_common(1)[0]
        if cnt / sum(c.values()) >= p.anchor_share:
            winner[lab] = pid
    rejected = {(r[0], r[1]) for r in conn.execute("SELECT face_id, person_id FROM face_rejections")}

    rows, auto = [], 0
    for fid, lab, is_anchor in zip(ids.tolist(), labels.tolist(), anchor.tolist()):
        pid = None if is_anchor else winner.get(lab)
        if pid is not None and (fid, pid) in rejected:
            pid = None
        auto += pid is not None
        rows.append((lab, pid, None if pid is None else "auto", fid))

    with transaction(conn):
        # Rows that the user anchored meanwhile keep their person (CASE).
        conn.executemany(
            "UPDATE faces SET cluster_id = ?, "
            "person_id = CASE WHEN assigned_by IN ('user', 'cluster') THEN person_id ELSE ? END, "
            "assigned_by = CASE WHEN assigned_by IN ('user', 'cluster') THEN assigned_by ELSE ? END "
            "WHERE id = ?", rows)
    clusters = int(labels.max()) + 1 if n else 0
    log.info("clustered %d faces into %d clusters, %d auto-named", n, clusters, auto)
    return {"faces": n, "clusters": clusters, "noise": int((labels < 0).sum()), "auto_assigned": auto}
