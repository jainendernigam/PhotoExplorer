"""Memory cards for the strip at the top of the Library ("On this day",
"Trip to Goa", "Asha over the years", "Best of 2025", "Rediscover"...).

Made from the library alone, fresh each day, with nothing sent anywhere.
Every card is a short list of photos (videos left out, look-alike copies
skipped) shown as a story; it can also be saved as a slideshow or collage.
"""
from __future__ import annotations

import random
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from .scanner.dates import wall_seconds

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
MAX = 40
BASE = ("m.status = 1 AND m.archived = 0 AND m.kind != 'video' AND m.taken_at IS NOT NULL "
        "AND m.id NOT IN (SELECT media_id FROM dup_members WHERE keep = 0)")


def _day(ts: int) -> date:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def _spread(ids: list[int], n: int = MAX) -> list[int]:
    if len(ids) <= n:
        return ids
    step = (len(ids) - 1) / (n - 1)
    return [ids[round(i * step)] for i in range(n)]


def _best(conn: sqlite3.Connection, ids: list[int], n: int) -> list[int]:
    """Prefer photos with clear faces and more pixels, then keep date order."""
    if len(ids) <= n:
        return ids
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT m.id, m.taken_at, coalesce(m.width * m.height, 0) px, "
        f"coalesce((SELECT max(f.size_px * f.score) FROM faces f WHERE f.media_id = m.id), 0) face, m.favorite "
        f"FROM media m WHERE m.id IN ({marks})", ids).fetchall()
    ranked = sorted(rows, key=lambda r: (r["favorite"], min(r["face"], 300), min(r["px"], 12e6)), reverse=True)[:n]
    return [r["id"] for r in sorted(ranked, key=lambda r: (r["taken_at"], r["id"]))]


def _card(key: str, kind: str, title: str, subtitle: str, ids: list[int]) -> dict:
    return {"key": key, "kind": kind, "title": title, "subtitle": subtitle, "cover": ids[len(ids) // 3] if ids else None,
            "media_ids": ids}


def strip(conn: sqlite3.Connection, today: date | None = None) -> list[dict]:
    today = today or date.today()
    rnd = random.Random(today.toordinal())
    cards: list[dict] = []

    # On this day, in earlier years (one card per year, the latest three)
    md = today.strftime("%m-%d")
    rows = conn.execute(f"SELECT m.id, m.taken_at FROM media m WHERE {BASE} "
                        "AND strftime('%m-%d', m.taken_at, 'unixepoch') = ? "
                        "AND CAST(strftime('%Y', m.taken_at, 'unixepoch') AS INTEGER) < ? ORDER BY m.taken_at",
                        (md, today.year)).fetchall()
    by_year: dict[int, list[int]] = {}
    for r in rows:
        by_year.setdefault(_day(r["taken_at"]).year, []).append(r["id"])
    for y in sorted(by_year, reverse=True)[:3]:
        ago = today.year - y
        cards.append(_card(f"otd-{y}-{md}", "on_this_day", "On this day",
                           f"{today.day} {MONTHS[today.month - 1][:3]} {y} · {ago} year{'s' if ago > 1 else ''} ago",
                           _best(conn, by_year[y], MAX)))
    if not by_year:   # nothing on the very day: this week in an earlier year
        lo, hi = (today - timedelta(days=3)).strftime("%m-%d"), (today + timedelta(days=3)).strftime("%m-%d")
        rows = conn.execute(f"SELECT m.id, m.taken_at FROM media m WHERE {BASE} "
                            "AND strftime('%m-%d', m.taken_at, 'unixepoch') BETWEEN ? AND ? "
                            "AND CAST(strftime('%Y', m.taken_at, 'unixepoch') AS INTEGER) < ? ORDER BY m.taken_at",
                            (lo, hi, today.year)).fetchall() if lo <= hi else []
        for r in rows:
            by_year.setdefault(_day(r["taken_at"]).year, []).append(r["id"])
        for y in sorted(by_year, reverse=True)[:2]:
            ago = today.year - y
            cards.append(_card(f"week-{y}-{md}", "this_week", f"{ago} year{'s' if ago > 1 else ''} ago",
                               f"This week in {y}", _best(conn, by_year[y], MAX)))

    # Birthdays coming up, then a person through the years
    people = conn.execute(
        "SELECT p.id, p.name, p.birth_date, count(DISTINCT f.media_id) n FROM persons p "
        "JOIN faces f ON f.person_id = p.id JOIN media m ON m.id = f.media_id AND m.status = 1 AND m.archived = 0 "
        "WHERE p.hidden = 0 GROUP BY p.id HAVING n >= 8 ORDER BY n DESC").fetchall()
    chosen = None
    for p in people:
        if p["birth_date"]:
            try:
                b = date.fromisoformat(p["birth_date"])
                nb = b.replace(year=today.year)
            except ValueError:
                continue
            if 0 <= (nb - today).days <= 7:
                chosen = (p, f"Birthday {nb.day} {MONTHS[nb.month - 1][:3]}")
                break
    if not chosen and people:
        p = people[rnd.randrange(min(len(people), 5))]
        chosen = (p, None)
    if chosen:
        p, note = chosen
        rows = conn.execute(f"SELECT m.id, m.taken_at, max(f.size_px * f.score) q FROM media m "
                            f"JOIN faces f ON f.media_id = m.id AND f.person_id = ? WHERE {BASE} "
                            "GROUP BY m.id ORDER BY m.taken_at", (p["id"],)).fetchall()
        per_year: dict[int, list] = {}
        for r in rows:
            per_year.setdefault(_day(r["taken_at"]).year, []).append(r)
        picks = []
        for y in sorted(per_year):
            best = sorted(per_year[y], key=lambda r: -(r["q"] or 0))[:3]
            picks += sorted(best, key=lambda r: r["taken_at"])
        ids = [r["id"] for r in picks][-MAX:]
        if len(per_year) >= 2 and len(ids) >= 4:
            years = sorted(per_year)
            cards.append(_card(f"person-{p['id']}-{today.isoformat()}", "person", f"{p['name']} over the years",
                               note or f"{years[0]} – {years[-1]}", ids))

    # Trips: days in a row with photos far from home
    cards += _trips(conn, rnd)

    # Best of last year (or the latest year with enough photos)
    yrs = conn.execute(f"SELECT CAST(strftime('%Y', m.taken_at, 'unixepoch') AS INTEGER) y, count(*) n FROM media m "
                       f"WHERE {BASE} GROUP BY y HAVING n >= 20 ORDER BY y DESC").fetchall()
    past = [r["y"] for r in yrs if r["y"] < today.year] or [r["y"] for r in yrs]
    if past:
        y = past[0]
        ids = []
        for mth in range(1, 13):
            lo, hi = wall_seconds(datetime(y, mth, 1)), wall_seconds(datetime(y + (mth == 12), mth % 12 + 1, 1))
            month_ids = [r[0] for r in conn.execute(f"SELECT m.id FROM media m WHERE {BASE} AND m.taken_at >= ? "
                                                    "AND m.taken_at < ? ORDER BY m.taken_at", (lo, hi))]
            ids += _best(conn, month_ids, 3)
        if len(ids) >= 6:
            cards.append(_card(f"best-{y}", "best_of_year", f"Best of {y}", f"{len(ids)} photos", ids))

    # Rediscover a month from long ago
    months = conn.execute(f"SELECT strftime('%Y-%m', m.taken_at, 'unixepoch') ym, count(*) n FROM media m WHERE {BASE} "
                          "AND m.taken_at < ? GROUP BY ym HAVING n >= 10",
                          (wall_seconds(datetime(today.year - 2, today.month, 1)),)).fetchall()
    if months:
        ym = months[rnd.randrange(len(months))]["ym"]
        y, mth = int(ym[:4]), int(ym[5:])
        lo, hi = wall_seconds(datetime(y, mth, 1)), wall_seconds(datetime(y + (mth == 12), mth % 12 + 1, 1))
        ids = [r[0] for r in conn.execute(f"SELECT m.id FROM media m WHERE {BASE} AND m.taken_at >= ? AND m.taken_at < ? "
                                          "ORDER BY m.taken_at", (lo, hi))]
        cards.append(_card(f"rediscover-{ym}", "rediscover", "Rediscover", f"{MONTHS[mth - 1]} {y}", _best(conn, ids, MAX)))

    # Favourites and pets
    fav = [r[0] for r in conn.execute(f"SELECT m.id FROM media m WHERE {BASE} AND m.favorite = 1 ORDER BY m.taken_at")]
    if len(fav) >= 4:
        cards.append(_card("favourites", "favourites", "Favourites", f"{len(fav)} photos", _spread(fav)))
    pets = [r[0] for r in conn.execute(f"SELECT m.id FROM media m JOIN pet_media p ON p.media_id = m.id WHERE {BASE} "
                                       "ORDER BY m.taken_at")]
    if len(pets) >= 4:
        cards.append(_card("pets", "pets", "Your pets", f"{len(pets)} photos", _best(conn, pets, MAX)))
    return [c for c in cards if c["media_ids"]]


# Background music for each kind of memory: pack moods (content.py music packs), best fit first.
MUSIC_MOODS = {
    "on_this_day": ["nostalgic", "reflective", "warm"],
    "this_week": ["nostalgic", "reflective", "warm"],
    "person": ["warm", "calm", "reflective"],
    "birthday": ["festive", "warm"],
    "trip": ["adventure", "upbeat", "warm"],
    "best_of_year": ["warm", "upbeat", "reflective"],
    "rediscover": ["nostalgic", "ambient", "reflective"],
    "favourites": ["warm", "calm"],
    "pets": ["upbeat", "lullaby"],
}


def add_music(cards: list[dict], tracks: list[dict]) -> list[dict]:
    """Give every card a song to play while its story is shown.

    The user's own songs ("My music" folder) come first: they are what the
    user chose to hear. Otherwise a pack track whose mood suits the memory
    (nostalgic for "On this day", festive for a birthday...). The pick is
    fixed per card, so a memory plays the same song each time it's opened."""
    own = [t for t in tracks if t["id"].startswith("mine:")]
    pack = [t for t in tracks if not t["id"].startswith("mine:")]
    for c in cards:
        rnd = random.Random(c["key"])
        if own:
            pool = own
        else:
            kind = "birthday" if c["kind"] == "person" and c["subtitle"].startswith("Birthday") else c["kind"]
            moods = MUSIC_MOODS.get(kind, ["warm", "calm"])
            pool = []
            for mood in moods:   # the best-fitting mood that has any track
                pool = [t for t in pack if t.get("mood") == mood]
                if pool:
                    break
            pool = pool or pack
        t = rnd.choice(pool) if pool else None
        c["music"] = {"id": t["id"], "title": t["title"], "seconds": t.get("seconds")} if t else None
    return cards


def _trips(conn: sqlite3.Connection, rnd: random.Random) -> list[dict]:
    from . import places
    import numpy as np
    rows = conn.execute(f"SELECT m.id, m.taken_at, m.gps_lat, m.gps_lon FROM media m WHERE {BASE} "
                        "AND m.gps_lat IS NOT NULL AND NOT (m.gps_lat = 0 AND m.gps_lon = 0) ORDER BY m.taken_at").fetchall()
    if len(rows) < 20:
        return []
    near = places.nearest_many(np.array([[r["gps_lat"], r["gps_lon"]] for r in rows], np.float64))
    home = Counter(near.tolist()).most_common(1)[0][0]
    xyz, plist = places._places()
    hv = xyz[home]
    trips, cur, last_day = [], [], None
    for r, k in zip(rows, near):
        far = 6371 * float(np.arccos(np.clip(xyz[k] @ hv, -1, 1))) > 100
        d = _day(r["taken_at"])
        if far and (not cur or (d - last_day).days <= 2):
            cur.append((r, int(k)))
        elif far:
            trips.append(cur); cur = [(r, int(k))]
        else:
            if cur:
                trips.append(cur); cur = []
        if far:
            last_day = d
    if cur:
        trips.append(cur)
    trips = [t for t in trips if len(t) >= 15]
    if not trips:
        return []
    t = trips[rnd.randrange(len(trips))]
    where = Counter(k for _, k in t).most_common(1)[0][0]
    name = plist[where][0]
    d0 = _day(t[0][0]["taken_at"])
    ids = [r["id"] for r, _ in t]
    return [_card(f"trip-{t[0][0]['id']}", "trip", f"Trip to {name}",
                  f"{MONTHS[d0.month - 1][:3]} {d0.year} · {len(ids)} photos", _best(conn, ids, MAX))]
