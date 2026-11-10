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


def place_list(conn: sqlite3.Connection, limit: int = 300) -> list[dict]:
    """Towns and cities with photos, most photos first, with a cover photo each."""
    _, p = _places()
    out = []
    for k, ids in sorted(_groups(conn).items(), key=lambda kv: -len(kv[1]))[:limit]:
        r = p[k]
        out.append({"id": k, "name": r[0], "state": r[5], "country": r[4], "lat": r[1], "lon": r[2],
                    "count": len(ids), "cover": ids[len(ids) // 2]})
    return out
