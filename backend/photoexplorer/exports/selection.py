"""Choosing which photos go into a slideshow or collage, and their captions."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def prepare(conn: sqlite3.Connection, media_ids: list[int], max_photos: int,
            skip_duplicates: bool = True, include_videos: bool = False) -> list[dict]:
    """Oldest first; redundant copies/look-alikes dropped (the suggested keeper
    stays); evenly thinned across the whole time span when there are too many."""
    if not media_ids:
        return []
    rows = []
    for i in range(0, len(media_ids), 900):           # SQLite variable limit
        chunk = media_ids[i:i + 900]
        rows += conn.execute(
            f"SELECT id, path, kind, taken_at, width, height, gps_lat, gps_lon FROM media WHERE status = 1 "
            f"AND id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    if not include_videos:
        rows = [r for r in rows if r["kind"] != "video"]
    if skip_duplicates:
        drop = {r[0] for r in conn.execute("SELECT media_id FROM dup_members WHERE keep = 0")}
        rows = [r for r in rows if r["id"] not in drop]
    rows.sort(key=lambda r: (r["taken_at"] or 0, r["id"]))
    if max_photos and len(rows) > max_photos:
        if max_photos == 1:
            rows = [rows[0]]
        else:
            step = (len(rows) - 1) / (max_photos - 1)
            rows = [rows[round(i * step)] for i in range(max_photos)]
    return [dict(r) for r in rows]


def age_text(birth: str, taken_at: int) -> str:
    born = date.fromisoformat(birth)
    t = datetime.fromtimestamp(taken_at, tz=timezone.utc).date()
    months = (t.year - born.year) * 12 + (t.month - born.month) - (t.day < born.day)
    if months < 0:
        return ""
    if months < 1:
        return "newborn"
    y, m = divmod(months, 12)
    if y == 0:
        return f"{m} month{'s' if m > 1 else ''}"
    return f"{y} year{'s' if y > 1 else ''}" + (f" {m} month{'s' if m > 1 else ''}" if m else "")


def caption_for(item: dict, mode: str, person: dict | None) -> str:
    """mode: none | date | date_age"""
    if mode == "none" or not item.get("taken_at"):
        return ""
    t = datetime.fromtimestamp(item["taken_at"], tz=timezone.utc)
    text = f"{MONTHS[t.month - 1]} {t.year}"
    if mode == "date_age" and person and person.get("birth_date"):
        age = age_text(person["birth_date"], item["taken_at"])
        if age:
            text += f"  ·  {person['name']}, {age}"
    return text
