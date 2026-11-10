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
    min_cohesion: float = 0.0    # groups less alike than this are split (see split_loose); 0 = off
    merge_sim: float = 0.0       # groups whose mean faces are this alike are joined (see merge_alike); 0 = off
    min_quality: float = 0.0     # faces with a known quality below this are left out of groups (not faces)
    min_score: float = 0.0       # ... and, when quality is unknown, faces with a detector score below this
    # Adding new faces without regrouping (assign_new): a face's score for a person or group is the
    # mean similarity of its `top` closest faces there.
    sure_sim: float = 1.0        # at least this, and `margin` ahead of the runner-up: added automatically
    doubt_sim: float = 1.0       # at least this: a suggestion for the user to confirm ("Is this X?")
    margin: float = 0.0
    top: int = 3


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

    return _compact(labels)  # 0 = biggest cluster


def _compact(labels: np.ndarray) -> np.ndarray:
    """Renumber labels 0..C-1, biggest cluster first; -1 stays -1."""
    valid = labels >= 0
    out = np.full(len(labels), -1, np.int64)
    if not valid.any():
        return out
    uniq, inv, counts = np.unique(labels[valid], return_inverse=True, return_counts=True)
    rank = np.empty(len(uniq), np.int64)
    rank[np.argsort(-counts, kind="stable")] = np.arange(len(uniq))
    out[valid] = rank[inv]
    return out


def cohesion(emb: np.ndarray) -> float:
    """How alike a group is: the 10th-percentile similarity of its faces to
    their mean. One person scores high; a chain of look-alikes scores low."""
    c = emb.mean(0)
    c /= max(float(np.linalg.norm(c)), 1e-9)
    return float(np.percentile(emb @ c, 10))


def same_photo_share(media: np.ndarray) -> float:
    """Share of a group's faces that have another face from the same photo in
    the group. One person appears once per photo, so this is ~0 for a clean group."""
    _, c = np.unique(media, return_counts=True)
    return float((c[c > 1] - 1).sum()) / max(len(media), 1)


def _distinct_parts(sub: np.ndarray, emb: np.ndarray, min_part: int, max_part_sim: float) -> bool:
    """True when a stricter re-grouping gives 2+ sizeable parts that look like
    different people (their mean faces are not alike). Two looks of one person
    (glasses, beard, years apart) stay alike and are not split."""
    labs, cnt = np.unique(sub[sub >= 0], return_counts=True)
    big = labs[cnt >= min_part]
    if len(big) < 2:
        return False
    cent = np.stack([emb[sub == l].mean(0) for l in big])
    cent /= np.maximum(np.linalg.norm(cent, axis=1, keepdims=True), 1e-9)
    s = cent @ cent.T
    np.fill_diagonal(s, -1)
    return bool((s.max(axis=1) < max_part_sim).any())


def split_loose(labels: np.ndarray, emb: np.ndarray, core_ok: np.ndarray, eps_sim: float,
                min_samples: int, k: int, min_cohesion: float, media: np.ndarray | None = None,
                max_same_photo: float = 0.02, step: float = 0.04, max_eps: float = 0.85,
                min_part: int = 8, max_part_sim: float = 0.5, probe: int = 4,
                dense_share: float = 0.05) -> np.ndarray:
    """Break up chained clusters.

    Linking faces A~B, B~C... can join different people through a few
    in-between faces, so one group swallows several people. A group is
    clustered again on its own with a stricter link when
    * its faces are not alike enough (cohesion), or
    * it holds two faces from the same photo too often, or
    * a slightly stricter link (up to ``probe`` steps) splits it into sizeable
      parts that look like different people (the two-people-and-a-bridge case,
      which the averages above can miss).
    This repeats until each part is clean (or the link limit is hit). Faces
    that fit no part become left-overs (-1)."""
    out = labels.copy()
    nxt = int(out.max()) + 1 if len(out) else 0
    todo = [(lab, eps_sim) for lab in np.unique(out[out >= 0])]
    while todo:
        lab, eps = todo.pop()
        members = np.flatnonzero(out == lab)
        if len(members) < min_samples or eps + step > max_eps:
            continue
        sub_emb = emb[members]
        bad = (media is not None and same_photo_share(media[members]) > max_same_photo) \
            or cohesion(sub_emb) < min_cohesion
        if not bad and len(members) < 2 * min_part:
            continue
        s, i = knn(sub_emb, k)
        sub = None
        if bad:
            tries = [(eps + step, min_samples)]
        else:
            # A thin bridge (a few in-between faces, each close to only a few
            # others) survives any link; asking core faces for more neighbours
            # removes it, while a real person keeps many close faces.
            dense = max(min_samples, min(k // 2, int(np.ceil(dense_share * len(members)))))
            tries = [(eps + t * step, ms) for t in range(probe + 1) for ms in (min_samples, dense)
                     if (t, ms) != (0, min_samples)]
        for e, ms in tries:
            if e > max_eps:
                break
            cand = graph_dbscan(s, i, e, ms, core_ok[members])
            if bad or _distinct_parts(cand, sub_emb, min_part, max_part_sim):
                sub, eps = cand, max(e, eps + step)
                break
        if sub is None:
            continue
        out[members] = -1
        for sl in np.unique(sub[sub >= 0]):
            out[members[sub == sl]] = nxt
            todo.append((nxt, eps))
            nxt += 1
    return _compact(out)


def merge_alike(labels: np.ndarray, emb: np.ndarray, merge_sim: float, media: np.ndarray | None = None,
                max_same_photo: float = 0.02, k: int = 10, rounds: int = 5) -> np.ndarray:
    """Join groups of the same person. Splitting with a stricter link can cut
    one person into a few groups; two groups whose mean faces are this alike
    are one person, unless joining them would put the same photo in twice too
    often. Most alike pairs are joined first, and passes repeat (a joined
    group has a new mean face, so new pairs can qualify) until nothing joins."""
    out = labels.copy()
    for _ in range(rounds):
        before = len(np.unique(out[out >= 0]))
        out = _merge_pass(out, emb, merge_sim, media, max_same_photo, k)
        if len(np.unique(out[out >= 0])) == before:
            break
    return out


def _merge_pass(labels: np.ndarray, emb: np.ndarray, merge_sim: float, media: np.ndarray | None,
                max_same_photo: float, k: int) -> np.ndarray:
    out = labels.copy()
    labs = np.unique(out[out >= 0])
    if len(labs) < 2:
        return out
    members = {int(l): np.flatnonzero(out == l) for l in labs}

    def centroid(m):
        c = emb[m].mean(0)
        return c / max(float(np.linalg.norm(c)), 1e-9)

    cent = np.stack([centroid(members[int(l)]) for l in labs]).astype(np.float32)
    s, i = knn(cent, min(k + 1, len(labs)))
    pairs = sorted(((float(s[a, j]), a, int(i[a, j])) for a in range(len(labs)) for j in range(s.shape[1])
                    if i[a, j] > a and s[a, j] >= merge_sim), reverse=True)
    root = list(range(len(labs)))

    def find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    for _, a, b in pairs:
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        ma, mb = members[int(labs[ra])], members[int(labs[rb])]
        if float(centroid(ma) @ centroid(mb)) < merge_sim:
            continue  # the groups have grown apart since this pair was listed
        if media is not None:
            # Photos showing a face of each group: one person can't be in a photo
            # twice. Allow a stray double detection (one_per_photo cleans it up).
            shared = len(np.intersect1d(media[ma], media[mb]))
            if shared > max(1, max_same_photo * min(len(ma), len(mb))):
                continue
        root[rb] = ra
        members[int(labs[ra])] = np.concatenate([ma, mb])
    for x in range(len(labs)):
        r = find(x)
        if r != x:
            out[out == labs[x]] = labs[r]
    return _compact(out)


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


def one_per_photo(labels: np.ndarray, emb: np.ndarray, media: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """A person appears once in a photo: when a group holds several faces of
    one photo, only the one most like the group stays (faces in ``keep``, the
    user's own choices, always stay)."""
    out = labels.copy()
    g = np.flatnonzero(out >= 0)
    if len(g) == 0:
        return out
    order = np.lexsort((media[g], out[g]))
    g = g[order]
    lab, med = out[g], media[g]
    dup = np.r_[False, (lab[1:] == lab[:-1]) & (med[1:] == med[:-1])]
    if not dup.any():
        return out
    cent = {}
    for l in np.unique(lab[dup]):
        c = emb[out == l].mean(0)
        cent[l] = c / max(float(np.linalg.norm(c)), 1e-9)
    # runs of the same (group, photo): keep the best match in each run
    start = np.flatnonzero(~dup)
    ends = np.r_[start[1:], len(g)]
    for s, e in zip(start, ends):
        if e - s < 2:
            continue
        run = g[s:e]
        sims = emb[run] @ cent[out[run[0]]]
        best = run[np.argmax(np.where(keep[run], np.inf, sims))]
        for f in run:
            if f != best and not keep[f]:
                out[f] = -1
    return out


def not_a_face(score: np.ndarray, quality: np.ndarray, p: ClusterParams) -> np.ndarray:
    """Detections too weak to trust as faces: hands, fabric, flowers, statues,
    side-on faces. Judged by quality when it is known (NaN = unknown), else by
    the detector score."""
    known = ~np.isnan(quality)
    weak_q = known & (np.nan_to_num(quality, nan=np.inf) < p.min_quality)
    weak_s = ~known & (score < p.min_score)
    return weak_q | weak_s


def group_faces(emb: np.ndarray, media: np.ndarray, size_px: np.ndarray, sharp: np.ndarray,
                score: np.ndarray, quality: np.ndarray, anchor: np.ndarray, user: np.ndarray,
                p: ClusterParams) -> np.ndarray:
    """Group labels 0..C-1 (-1 = in no group) for one model's faces.

    1. leave out detections that are not faces (anchored faces always stay)
    2. graph-DBSCAN on the k-nearest-neighbour graph
    3. split chained groups (split_loose), attach left-overs
    4. join groups of one person (merge_alike)
    5. keep one face per photo in each group (one_per_photo)"""
    labels = np.full(len(emb), -1, np.int64)
    keep = ~not_a_face(score, quality, p) | anchor
    sel = np.flatnonzero(keep)
    if len(sel) == 0:
        return labels
    e, m, a, u = emb[sel], media[sel], anchor[sel], user[sel]
    sims, idx = knn(e, p.k)
    core_ok = ((size_px[sel] >= p.core_min_px) & (sharp[sel] >= p.core_min_sharpness)) | a
    lab = graph_dbscan(sims, idx, p.eps_sim, p.min_samples, core_ok)
    if p.min_cohesion > 0:
        lab = split_loose(lab, e, core_ok, p.eps_sim, p.min_samples, p.k, p.min_cohesion, m)
    lab = attach_leftovers(lab, sims, idx, p.join_sim)
    if p.merge_sim > 0:
        lab = merge_alike(lab, e, p.merge_sim, m)
    labels[sel] = one_per_photo(lab, e, m, u)
    return labels


def best_targets(ref: np.ndarray, ref_target: np.ndarray, new: np.ndarray, k: int = 50, top: int = 3
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """For each new face, the best and second-best target (a person or a group,
    given per reference face in ``ref_target``) and their scores. A target's
    score is the mean similarity of the new face's ``top`` closest faces in it
    (among its ``k`` nearest reference faces; missing ones count as 0, so a tiny
    group needs a closer match), which suits people whose look
    changes over the years better than one mean face would.
    Returns t1, s1, t2, s2; no target = -1 with score 0."""
    n = len(new)
    t1, t2 = np.full(n, -1, np.int64), np.full(n, -1, np.int64)
    s1, s2 = np.zeros(n, np.float32), np.zeros(n, np.float32)
    if n == 0 or len(ref) == 0:
        return t1, s1, t2, s2
    k = min(k, len(ref))
    for st in range(0, n, 1024):
        sims = new[st:st + 1024] @ ref.T
        part = np.argpartition(-sims, k - 1, axis=1)[:, :k]
        psim = np.take_along_axis(sims, part, axis=1)
        order = np.argsort(-psim, axis=1)
        part, psim = np.take_along_axis(part, order, axis=1), np.take_along_axis(psim, order, axis=1)
        for r in range(len(part)):
            best: dict[int, list[float]] = {}
            for j, s in zip(part[r], psim[r]):
                lst = best.setdefault(int(ref_target[j]), [])
                if len(lst) < top:
                    lst.append(float(s))
            scored = sorted(((sum(v) / top, t) for t, v in best.items()), reverse=True)
            if scored:
                s1[st + r], t1[st + r] = scored[0]
            if len(scored) > 1:
                s2[st + r], t2[st + r] = scored[1]
    return t1, s1, t2, s2


# ------------------------------------------------------------ database step

def assign_new(conn: sqlite3.Connection, model: str, params: ClusterParams | None = None) -> dict:
    """Add faces found since the last grouping without regrouping anything.

    People the user named and the groups already shown stay exactly as they
    are. Each new face (cluster_id still NULL, no person) is compared with
    every named person and every unnamed group of the same model:
    * sure match - joins that person ('auto') or group;
    * doubtful   - recorded in face_suggestions ("Is this X?") and grouped with
                   the other new faces;
    * no match   - grouped with the other new faces (and earlier left-overs)
                   into new groups.
    Faces with a name but no group yet (names carried over when a photo was
    checked again) are kept as they are and just marked as grouped."""
    p = params or ClusterParams()
    rows = conn.execute(
        "SELECT f.id, f.media_id, f.embedding, f.size_px, f.sharpness, f.score, f.quality, f.cluster_id, "
        "f.person_id, f.assigned_by FROM faces f JOIN media m ON m.id = f.media_id AND m.status = 1 "
        "WHERE f.model = ?", (model,)).fetchall()
    out = {"new": 0, "auto_assigned": 0, "suggested": 0, "new_groups": 0}
    if not rows:
        return out
    ids = np.array([r[0] for r in rows], np.int64)
    media = np.array([r[1] for r in rows], np.int64)
    emb = np.stack([np.frombuffer(r[2], np.float32) for r in rows])
    size_px = np.array([r[3] for r in rows], np.int64)
    sharp = np.array([r[4] for r in rows], np.float32)
    score = np.array([1.0 if r[5] is None else r[5] for r in rows], np.float32)
    quality = np.array([np.nan if r[6] is None else r[6] for r in rows], np.float32)
    cluster = np.array([-2 if r[7] is None else r[7] for r in rows], np.int64)    # -2 = not grouped yet
    person = np.array([-1 if r[8] is None else r[8] for r in rows], np.int64)
    by = [r[9] for r in rows]

    new = (cluster == -2) & (person < 0)
    named_ungrouped = (cluster == -2) & (person >= 0)
    out["new"] = int(new.sum())
    junk = not_a_face(score, quality, p)
    updates: dict[int, tuple] = {}        # face index -> (cluster_id, person_id, assigned_by)
    for i in np.flatnonzero(named_ungrouped):
        updates[i] = (-1, int(person[i]), by[i])
    for i in np.flatnonzero(new & junk):
        updates[i] = (-1, None, None)

    # Targets: named people (person id >= 0) and unnamed groups (-(cluster id) - 2; -1 = no target).
    ref = np.flatnonzero(((person >= 0) | (cluster >= 0)) & ~junk & ~new)
    target = np.where(person[ref] >= 0, person[ref], -cluster[ref] - 2)
    cand = np.flatnonzero(new & ~junk)
    t1, s1, t2, s2 = best_targets(emb[ref], target, emb[cand], top=p.top)
    taken = {(int(t), int(m)) for t, m in zip(target, media[ref])}   # a person is once per photo
    rejected = {(r[0], r[1]) for r in conn.execute("SELECT face_id, person_id FROM face_rejections")}
    group_of = {}
    for t, c in zip(target, cluster[ref]):
        if t >= 0 and c >= 0:
            group_of.setdefault(int(t), Counter())[int(c)] += 1
    suggestions, leftover = [], []
    for j, i in enumerate(cand):
        t, s = int(t1[j]), float(s1[j])
        clear = t != -1 and (t, int(media[i])) not in taken and (int(ids[i]), t) not in rejected
        if clear and s >= p.sure_sim and s - float(s2[j]) >= p.margin:
            taken.add((t, int(media[i])))
            if t >= 0:     # a named person
                g = group_of.get(t)
                updates[i] = (g.most_common(1)[0][0] if g else -1, t, "auto")
                out["auto_assigned"] += 1
            else:          # an unnamed group
                updates[i] = (-t - 2, None, None)
            continue
        if clear and t >= 0 and s >= p.doubt_sim:
            suggestions.append((int(ids[i]), t, s))
        leftover.append(i)

    # Group what is left together with earlier left-overs (never with shown groups).
    pool = np.array(sorted(set(leftover) | set(np.flatnonzero((cluster == -1) & (person < 0) & ~junk).tolist())),
                    np.int64)
    if len(pool):
        lab = group_faces(emb[pool], media[pool], size_px[pool], sharp[pool], score[pool], quality[pool],
                          np.zeros(len(pool), bool), np.zeros(len(pool), bool), p)
        base = conn.execute("SELECT coalesce(max(cluster_id), -1) + 1 FROM faces").fetchone()[0]
        for i, l in zip(pool, lab):
            if l >= 0 or cluster[i] == -2:
                updates[i] = (int(base + l) if l >= 0 else -1, None, None)
        out["new_groups"] = int(len(np.unique(lab[lab >= 0])))

    with transaction(conn):
        conn.executemany(
            "UPDATE faces SET cluster_id = ?, "
            "person_id = CASE WHEN assigned_by IN ('user', 'cluster') THEN person_id ELSE ? END, "
            "assigned_by = CASE WHEN assigned_by IN ('user', 'cluster') THEN assigned_by ELSE ? END "
            "WHERE id = ?", [(c, pid, b, int(ids[i])) for i, (c, pid, b) in updates.items()])
        conn.executemany("INSERT OR REPLACE INTO face_suggestions(face_id, person_id, score, created_at) "
                         "VALUES (?, ?, ?, strftime('%s', 'now'))", suggestions)
    out["suggested"] = len(suggestions)
    log.info("added %d new faces: %d to people, %d suggested, %d new groups", out["new"],
             out["auto_assigned"], out["suggested"], out["new_groups"])
    return out


def recluster(conn: sqlite3.Connection, model: str, params: ClusterParams | None = None,
              offset: int = 0) -> dict:
    """Regroup the faces of one recogniser. Cluster ids start at ``offset``
    (see pipeline.cluster_all, which groups each model in its own id range)."""
    p = params or ClusterParams()
    n = conn.execute(
        "SELECT count(*) FROM faces f JOIN media m ON m.id = f.media_id AND m.status = 1 WHERE f.model = ?",
        (model,)).fetchone()[0]
    if n == 0:
        return {"faces": 0, "clusters": 0, "auto_assigned": 0}

    ids = np.empty(n, np.int64)
    media = np.empty(n, np.int64)
    size_px = np.empty(n, np.int64)
    sharp = np.empty(n, np.float32)
    score = np.empty(n, np.float32)
    quality = np.full(n, np.nan, np.float32)
    person = np.full(n, -1, np.int64)
    anchor = np.zeros(n, bool)
    user = np.zeros(n, bool)
    emb = None
    cur = conn.execute(
        "SELECT f.id, f.media_id, f.embedding, f.size_px, f.sharpness, f.score, f.quality, f.person_id, "
        "f.assigned_by FROM faces f JOIN media m ON m.id = f.media_id AND m.status = 1 WHERE f.model = ? "
        "ORDER BY f.id", (model,))
    i = 0
    for fid, mid, blob, px, sh, sc, q, pid, by in cur:
        if i >= n:
            break
        v = np.frombuffer(blob, np.float32)
        if emb is None:
            emb = np.empty((n, len(v)), np.float32)
        emb[i], ids[i], media[i], size_px[i], sharp[i] = v, fid, mid, px, sh
        score[i] = sc if sc is not None else 1.0
        if q is not None:
            quality[i] = q
        if pid is not None:
            person[i] = pid
            anchor[i] = by in ("user", "cluster")
            user[i] = by == "user"
        i += 1
    n = i
    emb, ids, media, size_px, sharp = emb[:n], ids[:n], media[:n], size_px[:n], sharp[:n]
    score, quality, person, anchor, user = score[:n], quality[:n], person[:n], anchor[:n], user[:n]

    labels = group_faces(emb, media, size_px, sharp, score, quality, anchor, user, p)
    labels = np.where(labels >= 0, labels + offset, -1)

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
    clusters = int(labels.max()) + 1 - offset if (labels >= 0).any() else 0
    log.info("clustered %d faces into %d clusters, %d auto-named", n, clusters, auto)
    return {"faces": n, "clusters": clusters, "noise": int((labels < 0).sum()), "auto_assigned": auto}
