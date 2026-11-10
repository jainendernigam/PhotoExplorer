"""Places: an offline world map and place names for photo locations.

The map data is Natural Earth (public domain, naturalearthdata.com), shipped
as ``world.json``: country shapes, state lines and about 7,300 towns and
cities. A photo's place is the nearest town in that list, so no internet
lookup is ever made.
"""
from __future__ import annotations

import json
import math
import sqlite3
from functools import lru_cache
from importlib import resources

import numpy as np

WORLD_FILE = "world.json"


@lru_cache(maxsize=1)
def world_text() -> str:
    return resources.files(__package__).joinpath(WORLD_FILE).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _places() -> tuple[np.ndarray, list[list]]:
    p = json.loads(world_text())["places"]
    xyz = np.array([_xyz(r[1], r[2]) for r in p], np.float64)
    return xyz, p


def _xyz(lat: float, lon: float) -> tuple[float, float, float]:
    a, b = math.radians(lat), math.radians(lon)
    return (math.cos(a) * math.cos(b), math.cos(a) * math.sin(b), math.sin(a))


def nearest(lat: float, lon: float) -> dict:
    """The nearest town or city: name, state, country and distance in km."""
    xyz, p = _places()
    v = np.array(_xyz(lat, lon))
    i = int(np.argmax(xyz @ v))
    km = 6371 * math.acos(max(-1.0, min(1.0, float(xyz[i] @ v))))
    r = p[i]
    return {"name": r[0], "state": r[5], "country": r[4], "km": round(km, 1)}


def nearest_many(points: np.ndarray, chunk: int = 4096) -> np.ndarray:
    """Index of the nearest place for each (lat, lon) row."""
    xyz, _ = _places()
    la, lo = np.radians(points[:, 0]), np.radians(points[:, 1])
    v = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=1)
    out = np.empty(len(points), np.int64)
    for s in range(0, len(points), chunk):
        out[s:s + chunk] = np.argmax(v[s:s + chunk] @ xyz.T, axis=1)
    return out


def photo_points(conn: sqlite3.Connection, where: str = "m.status = 1 AND m.archived = 0", args=()) -> list[list]:
    rows = conn.execute(f"SELECT m.id, m.gps_lat, m.gps_lon FROM media m WHERE {where} "
                        "AND m.gps_lat IS NOT NULL AND m.gps_lon IS NOT NULL "
                        "AND NOT (m.gps_lat = 0 AND m.gps_lon = 0)", args).fetchall()
    return [[r[0], round(r[1], 4), round(r[2], 4)] for r in rows]


_cache: dict = {"stamp": None, "groups": {}}


def _groups(conn: sqlite3.Connection) -> dict[int, list[int]]:
    """Photo ids per nearest place (main view photos), cached until the library changes."""
    stamp = tuple(conn.execute("SELECT count(*), coalesce(max(id), 0), coalesce(sum(archived), 0) FROM media "
                               "WHERE status = 1 AND gps_lat IS NOT NULL").fetchone())
    if _cache["stamp"] == stamp:
        return _cache["groups"]
    pts = photo_points(conn)
    groups: dict[int, list[int]] = {}
    if pts:
        near = nearest_many(np.array([[p[1], p[2]] for p in pts], np.float64))
        for (mid, _, _), k in zip(pts, near):
            groups.setdefault(int(k), []).append(mid)
    _cache.update(stamp=stamp, groups=groups)
    return groups


def place_ids(conn: sqlite3.Connection, place: int) -> list[int]:
    return _groups(conn).get(place, [])


# Newer or common spellings -> the name used in the map data (Natural Earth uses older names).
ALIASES = {
    "shimla": "simla", "gurugram": "gurgaon", "dehradun": "dehra dun", "uttarakhand": "uttaranchal",
    "bengaluru": "bangalore", "mumbai": "bombay", "kolkata": "calcutta", "chennai": "madras",
    "puducherry": "pondicherry", "varanasi": "benares", "kochi": "cochin", "thiruvananthapuram": "trivandrum",
    "prayagraj": "allahabad", "mysuru": "mysore", "vadodara": "baroda",
}


def _close(word: str, name: str) -> bool:
    """A search word matches a place name: the whole name or one word of it
    ('goa', 'delhi'), the start of one (4+ letters: 'hima' -> Himachal), or
    one letter off (5+ letters): 'shimla' finds 'Simla', 'dehradun' finds
    'Dehra Dun'."""
    flat = name.replace(" ", "").replace("-", "")
    tokens = name.replace("-", " ").split()
    if word == flat or (len(word) >= 4 and word in tokens):   # not 'new' -> New Delhi
        return True
    if len(word) >= 4 and (flat.startswith(word) or any(t.startswith(word) for t in tokens)):
        return True
    if len(word) < 5 or abs(len(word) - len(flat)) > 1:
        return False
    # edit distance <= 1
    if len(word) == len(flat):
        return sum(a != b for a, b in zip(word, flat)) <= 1
    short, long_ = (word, flat) if len(word) < len(flat) else (flat, word)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def search_ids(conn: sqlite3.Connection, q: str) -> list[int]:
    """Photos whose GPS location is in a town, state or country named in the
    search ("Simla", "Himachal", "UK"...). Offline: the town is the nearest
    one in the map data (see place_list)."""
    codes = {"uk": "united kingdom", "us": "united states of america", "usa": "united states of america",
             "uae": "united arab emirates", "britain": "united kingdom", "england": "united kingdom",
             "america": "united states of america"}
    words = [w for w in (x.strip(".,;:!?\"'").lower() for x in q.split())
             if (len(w) >= 3 or w in codes) and not w.isdigit()]
    if not words:
        return []
    _, p = _places()
    groups = _groups(conn)
    out: list[int] = []
    for k, ids in groups.items():
        name, country, state = p[k][0].lower(), str(p[k][4]).lower(), str(p[k][5]).lower()
        for w in words:
            w = ALIASES.get(w, w)
            if codes.get(w) == country or _close(w, name) or _close(w, state) or _close(w, country):
                out += ids
                break
    return out


def place_list(conn: sqlite3.Connection, limit: int = 300) -> list[dict]:
    """Towns and cities with photos, most photos first, with a cover photo each."""
    _, p = _places()
    out = []
    for k, ids in sorted(_groups(conn).items(), key=lambda kv: -len(kv[1]))[:limit]:
        r = p[k]
        out.append({"id": k, "name": r[0], "state": r[5], "country": r[4], "lat": r[1], "lon": r[2],
                    "count": len(ids), "cover": ids[len(ids) // 2]})
    return out
