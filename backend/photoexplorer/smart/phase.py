"""Smart phase of a scan: CLIP fingerprints, categories and pet groups.

Runs on the small thumbnails the scan already made (fast: about 25 photos a
second). Identical files are done once. Then:

* categories - each photo is compared with a few descriptions per category
  (and some everyday "other" descriptions); a category is given when it wins
  clearly. Screenshots are also recognised by their file name.
* pets - photos of a dog or cat are grouped by look (close fingerprints), so
  one pet's photos come together; a group can be named.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from ..db import transaction
from . import clip as clipmod

log = logging.getLogger(__name__)

TODO = "m.status = 1 AND m.clip_status = 0 AND m.kind IN ('image', 'raw', 'video') AND m.has_thumb = 1"

CATEGORIES: dict[str, list[str]] = {
    "screenshot": ["a screenshot of a phone screen", "a screenshot of a computer screen", "a screenshot of a chat app"],
    "selfie": ["a selfie", "a close-up selfie taken with a front camera"],
    "document": ["a photo of a document page with text", "a scanned paper document", "a photo of a printed form"],
    "receipt": ["a photo of a shop receipt", "a printed bill with prices", "an invoice"],
    "dog": ["a photo of a dog", "a photo of a puppy"],
    "cat": ["a photo of a cat", "a photo of a kitten"],
}
OTHERS = [
    "a photo of people", "a group photo of friends", "a portrait of a person", "a landscape photo", "a photo of a city street",
    "a photo of food", "a photo of a building", "a photo of a room", "a photo of a car", "a photo of flowers",
    "a photo of a beach", "a photo of a party", "a photo of a child", "a photo of a temple", "a photo",
]
MIN_PROB = 0.55            # share of the softmax a category must win
PET_SAME = 0.86            # fingerprints at least this close: the same pet (close-up pet photos)


def thumb_path(thumbs_dir: Path, sha: bytes) -> Path:
    h = sha.hex()
    return Path(thumbs_dir) / h[:2] / h[2:4] / f"{h}.webp"


class SmartPhase:
    def __init__(self, data_dir: Path, thumbs_dir: Path, cancel: threading.Event,
                 set_status: Callable[..., None], inc_status: Callable[..., None]):
        self.data_dir, self.thumbs_dir = Path(data_dir), Path(thumbs_dir)
        self.cancel, self._set, self._inc = cancel, set_status, inc_status

    def run(self, conn: sqlite3.Connection) -> None:
        if clipmod.missing(self.data_dir):
            return
        total = conn.execute(f"SELECT count(*) FROM media m WHERE {TODO}").fetchone()[0]
        if not total:
            return
        self._set(state="smart", smart_todo=total, smart_done=0)
        model = clipmod.Clip(self.data_dir)
        cats = _category_matrix(model)
        done_sha: dict[bytes, bytes] = {}
        last = 0
        while not self.cancel.is_set():
            rows = conn.execute(f"SELECT m.id, m.sha256, m.filename, m.kind, m.camera_make FROM media m WHERE {TODO} AND m.id > ? "
                                "ORDER BY m.id LIMIT 64", (last,)).fetchall()
            if not rows:
                break
            last = rows[-1]["id"]
            batch, keep = [], []
            for r in rows:
                if r["sha256"] and r["sha256"] in done_sha:
                    continue
                try:
                    with Image.open(thumb_path(self.thumbs_dir, r["sha256"])) as im:
                        batch.append(model.prepare(im))
                    keep.append(r)
                except Exception:
                    pass
            emb = model.images(batch) if batch else np.zeros((0, 512), np.float32)
            by_sha = {r["sha256"]: e.astype(np.float32).tobytes() for r, e in zip(keep, emb)}
            done_sha.update(by_sha)
            with transaction(conn):
                for r in rows:
                    blob = by_sha.get(r["sha256"]) or done_sha.get(r["sha256"])
                    if blob is None:
                        conn.execute("UPDATE media SET clip_status = 2 WHERE id = ?", (r["id"],))
                        continue
                    conn.execute("INSERT OR REPLACE INTO media_clip(media_id, embedding) VALUES (?, ?)", (r["id"], blob))
                    _tag(conn, r, np.frombuffer(blob, np.float32), cats)
                    conn.execute("UPDATE media SET clip_status = 1 WHERE id = ?", (r["id"],))
            self._inc(smart_done=len(rows))
        if not self.cancel.is_set():
            group_pets(conn)


@np.errstate(over="ignore")
def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(100.0 * (x - x.max()))
    return e / e.sum()


def _category_matrix(model: clipmod.Clip) -> tuple[np.ndarray, list[str]]:
    texts, owner = [], []
    for cat, prompts in CATEGORIES.items():
        for p in prompts:
            texts.append(model.text(p)); owner.append(cat)
    for p in OTHERS:
        texts.append(model.text(p)); owner.append("")
    return np.stack(texts), owner


def _tag(conn: sqlite3.Connection, r, emb: np.ndarray, cats: tuple[np.ndarray, list[str]]) -> None:
    mat, owner = cats
    prob = _softmax(mat @ emb)
    scores: dict[str, float] = {}
    for p, o in zip(prob, owner):
        if o:
            scores[o] = scores.get(o, 0.0) + float(p)
    if r["camera_make"]:          # taken with a camera, so not a screenshot
        scores.pop("screenshot", None)
    name = (r["filename"] or "").lower()
    if "screenshot" in name or "screen shot" in name or name.startswith("scr_"):
        scores["screenshot"] = max(scores.get("screenshot", 0.0), 0.99)
    conn.execute("DELETE FROM media_tags WHERE media_id = ?", (r["id"],))
    for cat, s in scores.items():
        if s >= MIN_PROB:
            conn.execute("INSERT INTO media_tags(media_id, tag, score) VALUES (?, ?, ?)", (r["id"], cat, s))


def group_pets(conn: sqlite3.Connection) -> int:
    """Group dog and cat photos by look. Names given to groups are kept: a new
    group takes the pet most of its photos already belonged to."""
    rows = conn.execute(
        "SELECT t.media_id, t.tag, c.embedding, pm.pet_id FROM media_tags t "
        "JOIN media_clip c ON c.media_id = t.media_id JOIN media m ON m.id = t.media_id AND m.status = 1 "
        "LEFT JOIN pet_media pm ON pm.media_id = t.media_id "
        "WHERE t.tag IN ('dog', 'cat') ORDER BY t.score DESC").fetchall()
    seen, items = set(), []
    for r in rows:
        if r["media_id"] not in seen:
            seen.add(r["media_id"]); items.append(r)
    with transaction(conn):
        conn.execute("DELETE FROM pet_media WHERE media_id NOT IN (SELECT media_id FROM media_tags WHERE tag IN ('dog', 'cat'))")
        if not items:
            return 0
        emb = np.stack([np.frombuffer(r["embedding"], np.float32) for r in items])
        species = [r["tag"] for r in items]
        n = len(items)
        lab = np.arange(n)
        for s in range(0, n, 1024):           # union of close pairs of the same species
            sims = emb[s:s + 1024] @ emb.T
            a, b = np.nonzero(sims >= PET_SAME)
            for i, j in zip(a + s, b):
                if i < j and species[i] == species[j]:
                    ri, rj = _find(lab, i), _find(lab, j)
                    if ri != rj:
                        lab[max(ri, rj)] = min(ri, rj)
        roots = np.array([_find(lab, i) for i in range(n)])
        uniq, inv, counts = np.unique(roots, return_inverse=True, return_counts=True)
        order = np.argsort(-counts, kind="stable")
        rank = np.empty(len(uniq), np.int64); rank[order] = np.arange(len(uniq))
        clusters = [int(rank[k]) if counts[k] >= 2 else -1 for k in inv]
        votes: dict[int, dict[int, int]] = {}
        for c, r in zip(clusters, items):
            if c >= 0 and r["pet_id"] is not None:
                votes.setdefault(c, {}).setdefault(r["pet_id"], 0)
                votes[c][r["pet_id"]] += 1
        winner = {c: max(v, key=v.get) for c, v in votes.items()}
        for c, r, sp in zip(clusters, items, species):
            pid = winner.get(c, r["pet_id"] if c < 0 else None)
            conn.execute("INSERT OR REPLACE INTO pet_media(media_id, species, cluster, pet_id) VALUES (?, ?, ?, ?)",
                         (r["media_id"], sp, c, pid))
    return int((np.array(clusters) >= 0).sum())


def _find(lab: np.ndarray, i: int) -> int:
    while lab[i] != i:
        lab[i] = lab[lab[i]]
        i = lab[i]
    return int(i)


# ------------------------------------------------------------ search

class Searcher:
    """Typed search by content: keeps all fingerprints in memory (25k photos =
    50 MB) and refreshes them when photos were added."""

    def __init__(self, data_dir: Path, db=None):
        self.data_dir = Path(data_dir)
        self.db = db
        self._model: clipmod.Clip | None = None
        self._ids = np.zeros(0, np.int64)
        self._emb = np.zeros((0, 512), np.float32)
        self._stamp: tuple | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return not clipmod.missing(self.data_dir)

    def ready_for(self, q: str) -> bool:
        """Make sure search_hits holds the photos for q; False when search by content is not set up."""
        if self.db is None or not self.available():
            return False
        try:
            self.hits(self.db.conn, q)
            return True
        except Exception:
            log.exception("search by content failed")
            return False

    def _load(self, conn: sqlite3.Connection) -> None:
        stamp = conn.execute("SELECT count(*), coalesce(max(rowid), 0) FROM media_clip").fetchone()
        stamp = tuple(stamp)
        if stamp == self._stamp:
            return
        rows = conn.execute("SELECT media_id, embedding FROM media_clip").fetchall()
        self._ids = np.array([r[0] for r in rows], np.int64)
        self._emb = np.stack([np.frombuffer(r[1], np.float32) for r in rows]) if rows else np.zeros((0, 512), np.float32)
        self._stamp = stamp

    def hits(self, conn: sqlite3.Connection, q: str, limit: int = 3000) -> int:
        """Fill search_hits for q (once per library change); returns the count."""
        q = q.strip().lower()
        if not q or not self.available():
            return 0
        with self._lock:
            self._load(conn)
            have = conn.execute("SELECT value FROM meta WHERE key = 'search_stamp'").fetchone()
            stamp = f"{self._stamp}"
            if have and have[0] == stamp and conn.execute("SELECT 1 FROM search_hits WHERE q = ? LIMIT 1", (q,)).fetchone():
                return -1
            if not have or have[0] != stamp:
                conn.execute("DELETE FROM search_hits")
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('search_stamp', ?)", (stamp,))
            if self._model is None:
                self._model = clipmod.Clip(self.data_dir, threads=2)
            if not len(self._ids):
                return 0
            t = self._model.text(q if len(q.split()) > 2 else f"a photo of {q}")
            sims = self._emb @ t
            best = float(sims.max())
            cut = max(0.245, best - 0.06)   # words that are not things in a picture score below 0.245
            idx = np.nonzero(sims >= cut)[0]
            idx = idx[np.argsort(-sims[idx])][:limit]
            with transaction(conn):
                conn.execute("DELETE FROM search_hits WHERE q = ?", (q,))
                conn.executemany("INSERT INTO search_hits(q, media_id, score) VALUES (?, ?, ?)",
                                 [(q, int(self._ids[i]), float(sims[i])) for i in idx])
            return len(idx)
