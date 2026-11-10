"""Find duplicate and look-alike groups, and suggest which copy to keep.

* exact - identical bytes (same SHA-256), any folder, any date.
* near  - the same picture saved again (resized, recompressed, re-exported,
          WhatsApp copy...): perceptual hashes almost equal, any date.
          Candidates come from the indexed 16-bit pHash bands, so this never
          compares every pair.
* burst - "similar shots": photos taken within a short time on the same
          camera that look alike (rapid-fire, retakes of the same pose).

near/burst work on one representative per exact-copy set, so a picture is
not listed twice. Pairs the user marked "not duplicates" are never grouped.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..db import transaction
from ..scanner.hashing import to_unsigned64

log = logging.getLogger(__name__)


@dataclass
class DedupParams:
    near_phash: int = 6          # max pHash bits different for "same picture"
    near_dhash: int = 10
    burst_window_s: int = 90     # max seconds between neighbouring shots
    burst_phash: int = 18        # looser: same scene, small changes
    burst_dhash: int = 18
    burst_max_neighbours: int = 40
    bucket_cap: int = 400        # huge pHash-band buckets are compared by sorted neighbours only


# A copy whose path looks like this is a worse keeper than one that doesn't.
_COPY_HINTS = re.compile(
    r"(\bbackup\b|\bbackups\b|\bcopy\b|\bcopies\b|\bduplicate|\bdownloads?\b|\btemp\b|\btmp\b|"
    r"whatsapp|\bsent\b|\bexport|\(\d+\)|[-_ ]copy\d*\.|^copy of )", re.I)

_RELIABLE_DATES = ("exif", "video")


def _copy_penalty(path: str) -> int:
    return len(_COPY_HINTS.findall(path))


def _popcount(v: int) -> int:
    return to_unsigned64(v).bit_count()


def _ham(a: int, b: int) -> int:
    return (to_unsigned64(a) ^ to_unsigned64(b)).bit_count()


class _UF:
    def __init__(self):
        self.p: dict[int, int] = {}

    def find(self, x: int) -> int:
        p = self.p
        p.setdefault(x, x)
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def sharpness_of(thumb: Path) -> float | None:
    """Variance of the Laplacian on the thumbnail: higher = sharper."""
    try:
        with Image.open(thumb) as im:
            g = np.asarray(im.convert("L"), dtype=np.float32)
    except Exception:
        return None
    if g.shape[0] < 3 or g.shape[1] < 3:
        return None
    lap = g[1:-1, :-2] + g[1:-1, 2:] + g[:-2, 1:-1] + g[2:, 1:-1] - 4 * g[1:-1, 1:-1]
    return float(lap.var())


def find_groups(conn: sqlite3.Connection, thumbs_dir: Path, params: DedupParams | None = None,
                progress=lambda **kw: None) -> dict:
    p = params or DedupParams()
    rows = conn.execute(
        "SELECT id, path, size, mtime_ns, sha256, phash, dhash, ph0, ph1, ph2, ph3, width, height, "
        "taken_at, date_source, camera_make, camera_model, sharpness, kind "
        "FROM media WHERE status = 1 AND sha256 IS NOT NULL").fetchall()
    ignored = {(r[0], r[1]) for r in conn.execute("SELECT a, b FROM dup_ignored")}
    by_id = {r["id"]: r for r in rows}

    def ok_pair(a: int, b: int) -> bool:
        return (min(a, b), max(a, b)) not in ignored

    def exact_key(i):
        """Best copy among identical files: no 'backup/copy/(1)' hints, oldest, shallowest."""
        r = by_id[i]
        return (_copy_penalty(r["path"]), r["mtime_ns"], r["path"].count("\\") + r["path"].count("/"),
                len(r["path"]), i)

    # ---- exact: same bytes
    by_sha: dict[bytes, list[int]] = defaultdict(list)
    for r in rows:
        by_sha[r["sha256"]].append(r["id"])
    exact = [sorted(ids) for ids in by_sha.values() if len(ids) > 1]
    exact_groups = []
    for ids in exact:
        uf = _UF()
        for i in ids:
            uf.find(i)
        for i, a in enumerate(ids):            # respect "not duplicates" even for exact copies
            for b in ids[i + 1:]:
                if ok_pair(a, b):
                    uf.union(a, b)
        comp: dict[int, list[int]] = defaultdict(list)
        for i in ids:
            comp[uf.find(i)].append(i)
        exact_groups += [c for c in comp.values() if len(c) > 1]
    progress(dedup_exact=len(exact_groups))

    # ---- one representative per content for look-alikes (images with a usable pHash):
    # the copy the exact-duplicate suggestion would keep
    reps = []
    for ids in by_sha.values():
        r = by_id[min(ids, key=exact_key)]
        if r["phash"] is None or r["kind"] == "video":
            continue
        pc = _popcount(r["phash"])
        if pc < 8 or pc > 56:      # flat/blank pictures: every one "matches" every other
            continue
        reps.append(r)

    uf = _UF()
    edge_dist: dict[tuple[int, int], int] = {}

    def link(a, b, d):
        if a == b or not ok_pair(a, b):
            return
        k = (min(a, b), max(a, b))
        if k not in edge_dist or d < edge_dist[k]:
            edge_dist[k] = d
        uf.union(a, b)

    # near: any date, candidates share a 16-bit band (guaranteed for distance <= 3,
    # and catches most pairs up to ~6-8 in practice)
    for band in ("ph0", "ph1", "ph2", "ph3"):
        buckets: dict[int, list] = defaultdict(list)
        for r in reps:
            buckets[r[band]].append(r)
        for members in buckets.values():
            if len(members) < 2:
                continue
            if len(members) > p.bucket_cap:
                members = sorted(members, key=lambda r: to_unsigned64(r["phash"]))
                pairs = ((members[i], members[j]) for i in range(len(members))
                         for j in range(i + 1, min(i + 20, len(members))))
            else:
                pairs = ((members[i], members[j]) for i in range(len(members))
                         for j in range(i + 1, len(members)))
            for a, b in pairs:
                d = _ham(a["phash"], b["phash"])
                if d <= p.near_phash and _ham(a["dhash"], b["dhash"]) <= p.near_dhash:
                    link(a["id"], b["id"], d)

    # burst: neighbours in time with reliable capture dates, same camera
    timed = sorted((r for r in reps if r["taken_at"] is not None and r["date_source"] in _RELIABLE_DATES),
                   key=lambda r: r["taken_at"])
    for i, a in enumerate(timed):
        for b in timed[i + 1:i + 1 + p.burst_max_neighbours]:
            if b["taken_at"] - a["taken_at"] > p.burst_window_s:
                break
            if (a["camera_model"] or "") != (b["camera_model"] or ""):
                continue
            d = _ham(a["phash"], b["phash"])
            if d <= p.burst_phash and _ham(a["dhash"], b["dhash"]) <= p.burst_dhash:
                link(a["id"], b["id"], d)

    comps: dict[int, list[int]] = defaultdict(list)
    for node in {x for e in edge_dist for x in e}:
        comps[uf.find(node)].append(node)
    comp_max: dict[int, int] = defaultdict(int)       # largest linked distance per group
    for (a, _), d in edge_dist.items():
        root = uf.find(a)
        comp_max[root] = max(comp_max[root], d)
    look = [sorted(c) for c in comps.values() if len(c) > 1]
    progress(dedup_similar=len(look))

    # ---- sharpness for look-alike members (used to pick the best shot)
    need = [i for c in look for i in c if by_id[i]["sharpness"] is None]
    sharp_updates = []
    for i in need:
        h = by_id[i]["sha256"].hex()
        s = sharpness_of(thumbs_dir / h[:2] / h[2:4] / f"{h}.webp")
        if s is not None:
            sharp_updates.append((s, i))
    sharp = {i: by_id[i]["sharpness"] for c in look for i in c}
    sharp.update({i: s for s, i in sharp_updates})

    # ---- rank members and write
    def look_key(i):
        r = by_id[i]
        return (-(r["width"] or 0) * (r["height"] or 0),
                -(round(sharp.get(i) or 0, 0)),
                r["date_source"] not in _RELIABLE_DATES,
                _copy_penalty(r["path"]),
                -r["size"], i)

    out_groups = []
    for ids in exact_groups:
        ranked = sorted(ids, key=exact_key)
        out_groups.append(("exact", ranked, 0))
    for ids in look:
        ranked = sorted(ids, key=look_key)
        dmax = comp_max[uf.find(ids[0])]
        # A burst is about how the photos were taken: all with real capture
        # times, seconds apart. Otherwise it is the same picture saved again.
        times = [by_id[i]["taken_at"] for i in ids if by_id[i]["date_source"] in _RELIABLE_DATES]
        is_burst = (len(times) == len(ids) and len(set(times)) > 1
                    and max(times) - min(times) <= p.burst_window_s * len(ids))
        kind = "burst" if is_burst else "near"
        out_groups.append((kind, ranked, dmax))

    with transaction(conn):
        if sharp_updates:
            conn.executemany("UPDATE media SET sharpness = ? WHERE id = ?", sharp_updates)
        conn.execute("DELETE FROM dup_members")
        conn.execute("DELETE FROM dup_groups")
        for kind, ranked, dmax in out_groups:
            reclaim = sum(by_id[i]["size"] for i in ranked[1:])
            taken = min((by_id[i]["taken_at"] for i in ranked if by_id[i]["taken_at"] is not None), default=None)
            gid = conn.execute(
                "INSERT INTO dup_groups(kind, size, reclaim_bytes, taken_at, max_distance) VALUES (?, ?, ?, ?, ?)",
                (kind, len(ranked), reclaim, taken, dmax)).lastrowid
            conn.executemany("INSERT INTO dup_members(group_id, media_id, rank, keep) VALUES (?, ?, ?, ?)",
                             [(gid, m, k, int(k == 0)) for k, m in enumerate(ranked)])
    counts = defaultdict(int)
    for kind, _, _ in out_groups:
        counts[kind] += 1
    log.info("dedup: %s", dict(counts))
    return {"exact": counts["exact"], "near": counts["near"], "burst": counts["burst"]}
