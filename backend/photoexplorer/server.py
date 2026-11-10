"""Local HTTP API used by the desktop UI.

Listens on 127.0.0.1 only, on a random free port, and requires a per-launch
token (header ``X-PE-Token``, or ``?token=`` for <img> URLs). The first line
on stdout is ``{"event": "ready", "port": ..., "token": ...}`` so the Tauri
shell knows where to connect.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__
from .config import Settings
from .content import Content
from .db import Database, transaction
from .faces import models as face_models
from .faces import people
from .faces.people import PeopleError
from .dedup import actions as dedup_actions
from .exports.jobs import ExportManager
from .organize import templates as org_templates
from .organize.runner import OrganizeError, OrganizeManager
from .organize.rules import Rules
from .edit.manager import EditManager
from .edit.ops import EditError
from .gphotos.importer import Importer, default_import_dir
from .gphotos.oauth import GoogleAuth, GoogleError
from .scanner.dates import wall_seconds, wall_to_iso
from .scanner.formats import long_path
from .scanner.manager import DONE, ScanManager
from .updates import Updater, UpdateError

ALLOWED_ORIGINS = [
    "http://tauri.localhost", "https://tauri.localhost", "tauri://localhost",
    "http://localhost:1420", "http://127.0.0.1:1420",
]
MEDIA_COLUMNS = (
    "id, source_id, folder_id, path, filename, kind, size, width, height, duration_ms, "
    "taken_at, tz_offset, date_source, camera_make, camera_model, gps_lat, gps_lon, "
    "has_thumb, status, favorite, archived"
)


def _id_list(value: str | None, name: str) -> list[int] | None:
    """'1,2,3' -> [1, 2, 3]; None stays None (no filter), '' means none at all."""
    if value is None:
        return None
    try:
        return [int(x) for x in value.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, f"Bad {name}")


class MediaFilter:
    """Filters shared by /api/media and /api/timeline (FastAPI dependency)."""

    def __init__(
        self,
        source_id: int | None = None,
        sources: str | None = Query(None, description="comma-separated source ids"),
        folder_id: int | None = None,
        album_id: int | None = None,
        kind: str | None = Query(None, pattern="^(image|raw|video)$"),
        q: str | None = None,
        persons: str | None = Query(None, description="comma-separated person ids"),
        persons_mode: str = Query("all", pattern="^(all|any)$"),
        date_from: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
        date_to: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
        fav: bool = False,
        show: str = Query("main", pattern="^(main|archive|all)$", description="archive = only archived photos"),
        tag: str | None = Query(None, pattern="^[a-z_]+$"),
        pet: str | None = Query(None, description="pet id, or c<cluster> for an unnamed group"),
        bbox: str | None = Query(None, description="lat_min,lon_min,lat_max,lon_max"),
        place: int | None = Query(None, description="a town from /api/places"),
    ):
        # Built directly in code (not by FastAPI): Query(...) defaults become their plain values.
        from fastapi import params as _params
        _d = lambda v: v.default if isinstance(v, _params.Param) else v  # noqa: E731
        (source_id, sources, folder_id, album_id, kind, q, persons, persons_mode, date_from, date_to, fav, show, tag, pet,
         bbox, place) = map(_d, (source_id, sources, folder_id, album_id, kind, q, persons, persons_mode, date_from,
                                 date_to, fav, show, tag, pet, bbox, place))
        self.source_id, self.folder_id, self.kind, self.q = source_id, folder_id, kind, q
        self.album_id = album_id
        self.sources = _id_list(sources, "sources")
        self.persons, self.persons_mode = persons, persons_mode
        self.date_from, self.date_to = date_from, date_to
        self.fav, self.show, self.tag, self.pet, self.bbox, self.place = fav, show, tag, pet, bbox, place

    def where(self) -> tuple[list[str], list]:
        where, args = [f"m.status = {DONE}"], []
        if self.source_id is not None:
            where.append("m.source_id = ?"); args.append(self.source_id)
        if self.sources is not None:
            where.append(f"m.source_id IN ({','.join('?' * len(self.sources)) or 'NULL'})"); args += self.sources
        if self.folder_id is not None:
            where.append("m.folder_id = ?"); args.append(self.folder_id)
        if self.kind:
            where.append("m.kind = ?"); args.append(self.kind)
        if self.album_id is not None:
            where.append("m.id IN (SELECT media_id FROM album_items WHERE album_id = ?)"); args.append(self.album_id)
        if self.persons:
            try:
                pids = [int(x) for x in self.persons.split(",") if x.strip()]
            except ValueError:
                raise HTTPException(400, "Bad persons")
            if pids:
                cond, pargs = people.media_filter_sql(pids, self.persons_mode)
                where.append(cond); args += pargs
        for value, op, end in ((self.date_from, ">=", False), (self.date_to, "<", True)):
            if value:
                where.append(f"m.taken_at {op} ?"); args.append(_day_seconds(value, end))
        if self.show == "main":
            where.append("m.archived = 0")
        elif self.show == "archive":
            where.append("m.archived = 1")
        if self.fav:
            where.append("m.favorite = 1")
        if self.tag:
            where.append("m.id IN (SELECT media_id FROM media_tags WHERE tag = ?)"); args.append(self.tag)
        if self.pet:
            if self.pet.startswith("c"):
                where.append("m.id IN (SELECT media_id FROM pet_media WHERE cluster = ? AND pet_id IS NULL)")
            else:
                where.append("m.id IN (SELECT media_id FROM pet_media WHERE pet_id = ?)")
            try:
                args.append(int(self.pet.lstrip("c")))
            except ValueError:
                raise HTTPException(400, "Bad pet")
        if self.bbox:
            try:
                a, b, c, d = (float(x) for x in self.bbox.split(","))
            except ValueError:
                raise HTTPException(400, "Bad bbox")
            where.append("m.gps_lat BETWEEN ? AND ? AND m.gps_lon BETWEEN ? AND ?"); args += [a, c, b, d]
        if self.place is not None and SEARCH is not None and SEARCH.db is not None:
            from . import places as places_mod
            ids = places_mod.place_ids(SEARCH.db.conn, self.place)
            where.append(f"m.id IN ({','.join(str(int(i)) for i in ids) or 'NULL'})")
        if self.q:
            terms = " ".join('"' + t.replace('"', "") + '"*' for t in self.q.split() if t.replace('"', ""))
            # Names, folders and notes; and, when search by content is set up, what is in the picture.
            q = self.q.strip().lower()
            smart = SEARCH is not None and SEARCH.ready_for(q)
            conds = []
            if terms:
                conds.append("m.id IN (SELECT rowid FROM media_fts WHERE media_fts MATCH ?)"); args.append(terms)
            if smart:
                conds.append("m.id IN (SELECT media_id FROM search_hits WHERE q = ?)"); args.append(q)
            if SEARCH is not None and SEARCH.db is not None:
                # Where the photo was taken (GPS): "Simla", "Himachal", "UK"...
                from . import places as places_mod
                ids = places_mod.search_ids(SEARCH.db.conn, self.q)
                if ids:
                    conds.append(f"m.id IN ({','.join(str(int(i)) for i in ids)})")
            if conds:
                where.append("(" + " OR ".join(conds) + ")")
        return where, args


SEARCH = None   # smart.phase.Searcher, set by create_app


class SourceIn(BaseModel):
    path: str
    label: str | None = None


class ScanIn(BaseModel):
    source_ids: list[int] | None = None
    retry_errors: bool = False


class OrganizeIn(BaseModel):
    media_ids: list[int] | None = None
    filters: dict | None = None
    dest_root: str
    mode: str = "copy"               # copy | hardlink | symlink | move
    options: dict = {}
    people: list[int] | None = None  # only photos showing these people
    people_mode: str = "any"         # any | all


class RuleIn(OrganizeIn):
    name: str = ""


class RulePatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    dest_root: str | None = None
    mode: str | None = None
    options: dict | None = None
    people: list[int] | None = None
    people_mode: str | None = None


class FramesSaveIn(BaseModel):
    which: list[int] | None = None       # None = every frame


class PrintIn(BaseModel):
    media_ids: list[int]
    paper: str = "a4"
    layout: str = "full"                 # full | two | four | contact
    landscape: bool = False
    captions: bool = False
    contact_cols: int = 5
    title: str | None = None


class PathIn(BaseModel):
    path: str


class OpenWithIn(BaseModel):
    enable: bool


class RuleRunIn(BaseModel):
    rule_ids: list[int] | None = None   # None = every switched-on rule


class EditPreviewIn(BaseModel):
    ops: dict = {}
    size: int = 1400


class EditSaveIn(BaseModel):
    ops: dict = {}
    mode: str                        # copy | replace
    dest_dir: str | None = None      # copy: None = next to the original


class EditBatchIn(EditSaveIn):
    media_ids: list[int]


class AlbumIn(BaseModel):
    name: str
    media_ids: list[int] = []


class AlbumItemsIn(BaseModel):
    media_ids: list[int]


class ExportIn(BaseModel):
    kind: str                        # slideshow | collage
    title: str | None = None
    media_ids: list[int] | None = None
    filters: dict | None = None      # same keys as /api/media (persons, date_from, ...)
    options: dict = {}
    output_dir: str | None = None


class UpdateUrlIn(BaseModel):
    url: str


class UpdateInstallIn(BaseModel):
    packs: list[str] | None = None   # None = every new or updated pack
    app: bool = True


class GoogleClientIn(BaseModel):
    client_id: str
    client_secret: str


class GoogleImportIn(BaseModel):
    folder: str | None = None


class RemoveIn(BaseModel):
    remove: list[int]
    method: str = "archive"   # archive | recycle | app (hide in the app, file stays)


class ApplyIn(BaseModel):
    kind: str                 # exact | near | burst
    method: str = "archive"
    group_ids: list[int] | None = None   # None = every group of this kind


class UndoIn(BaseModel):
    ids: list[int] | None = None
    batch: str | None = None


class FacesRunIn(BaseModel):
    mode: str = "faces"   # 'faces' (detect new + cluster) | 'recheck' (look again everywhere) | 'cluster'


class NameIn(BaseModel):
    name: str


class PersonPatch(BaseModel):
    name: str | None = None
    birth_date: str | None = None   # 'YYYY-MM-DD' or '' to clear
    hidden: bool | None = None
    cover_face_id: int | None = None


class MergeIn(BaseModel):
    source_ids: list[int]


class AssignIn(BaseModel):
    face_ids: list[int]
    person_id: int | None = None
    name: str | None = None


class RejectIn(BaseModel):
    face_ids: list[int]
    person_id: int


def _media_dict(r) -> dict:
    d = dict(r)
    d["taken"] = wall_to_iso(d.get("taken_at"))
    return d


def _overlaps(a: str, b: str) -> bool:
    a, b = os.path.normcase(a), os.path.normcase(b)
    try:
        common = os.path.commonpath([a, b])
    except ValueError:  # different drives
        return False
    return common in (a, b)


def create_app(settings: Settings, token: str) -> FastAPI:
    db = Database(settings.db_path)
    scanner = ScanManager(db, settings)
    global SEARCH
    from .smart.phase import Searcher
    SEARCH = Searcher(settings.data_dir, db)
    exporter = ExportManager(db, settings)
    organizer = OrganizeManager(db, settings)
    rules = Rules(db, organizer)

    def _faces_needed():
        if settings.faces_enabled and not scanner.running:
            scanner.start(mode="faces")
    editor = EditManager(db, settings, _faces_needed)
    gauth = GoogleAuth(settings.data_dir)
    gimport = Importer(db, settings, gauth, scanner)
    content = Content(settings.data_dir)
    updater = Updater(settings.data_dir, content)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        scanner.cancel()
        scanner.wait(10)

    app = FastAPI(title="Photo Explorer", version=__version__, lifespan=lifespan)
    app.state.db, app.state.scanner = db, scanner
    from .features import register as register_features

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.method != "OPTIONS" and request.url.path != "/api/health":
            given = request.headers.get("x-pe-token") or request.query_params.get("token") or ""
            if not secrets.compare_digest(given, token):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                       allow_methods=["*"], allow_headers=["*"])

    # ---------------------------------------------------------------- misc

    register_features(app, db, settings, scanner)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/info")
    def info():
        return {
            "version": __version__,
            "data_dir": str(settings.data_dir),
            "workers": settings.workers,
            "ffmpeg": settings.ffmpeg is not None,
            "heif": _has("pillow_heif"),
            "raw": _has("rawpy"),
            "ffmpeg_path": settings.ffmpeg,
            "exports_dir": str(settings.exports_dir),
            "faces": face_models.available(),
            "faces_missing": face_models.missing(),
        }

    @app.get("/api/stats")
    def stats():
        c = db.conn
        by = c.execute(
            "SELECT kind, status, count(*) n, coalesce(sum(size), 0) bytes FROM media GROUP BY kind, status"
        ).fetchall()
        dup = c.execute(
            f"SELECT count(*) groups, coalesce(sum(n - 1), 0) extra FROM ("
            f" SELECT count(*) n FROM media WHERE status = {DONE} AND sha256 IS NOT NULL"
            f" GROUP BY sha256 HAVING n > 1)"
        ).fetchone()
        span = c.execute(f"SELECT min(taken_at), max(taken_at) FROM media WHERE status = {DONE}").fetchone()
        return {
            "breakdown": [dict(r) for r in by],
            "exact_duplicate_groups": dup["groups"],
            "exact_duplicate_extra_files": dup["extra"],
            "date_from": wall_to_iso(span[0]),
            "date_to": wall_to_iso(span[1]),
        }

    # ---------------------------------------------------------------- sources

    @app.get("/api/sources")
    def list_sources():
        rows = db.conn.execute(
            "SELECT s.*, (SELECT count(*) FROM media m WHERE m.source_id = s.id AND m.status IN (0, 1, 2)) files "
            "FROM sources s ORDER BY s.path").fetchall()
        return [dict(r) | {"online": os.path.isdir(long_path(r["path"]))} for r in rows]

    @app.post("/api/sources")
    def add_source(body: SourceIn):
        raw = body.path.strip().strip('"')
        if not raw or not os.path.isabs(os.path.expanduser(raw)):
            raise HTTPException(400, "Give the full path of a folder.")
        path = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isdir(long_path(path)):
            raise HTTPException(400, f"Not a folder: {path}")
        if _overlaps(path, str(settings.data_dir)):
            raise HTTPException(400, "This folder contains (or is inside) the app's own data folder.")
        for r in db.conn.execute("SELECT path FROM sources"):
            if _overlaps(path, r["path"]):
                raise HTTPException(409, f"Overlaps an existing source: {r['path']}")
        cur = db.conn.execute("INSERT INTO sources(path, label, added_at) VALUES (?, ?, ?)",
                              (path, body.label, int(time.time())))
        return {"id": cur.lastrowid, "path": path}

    @app.delete("/api/sources/{source_id}")
    def remove_source(source_id: int):
        """Forgets the folder and its index. Files on disk are never touched."""
        if scanner.running:
            raise HTTPException(409, "Stop the scan first.")
        with transaction(db.conn):
            n = db.conn.execute("DELETE FROM sources WHERE id = ?", (source_id,)).rowcount
        if not n:
            raise HTTPException(404)
        return {"removed": source_id}

    # ---------------------------------------------------------------- scanning

    @app.post("/api/scan")
    def start_scan(body: ScanIn | None = None):
        body = body or ScanIn()
        if not scanner.start(body.source_ids, body.retry_errors):
            raise HTTPException(409, "A scan is already running.")
        return scanner.status()

    @app.post("/api/scan/cancel")
    def cancel_scan():
        scanner.cancel()
        return scanner.status()

    @app.get("/api/scan/status")
    def scan_status():
        return scanner.status()

    # ---------------------------------------------------------------- browsing

    @app.get("/api/folders")
    def folders(source_id: int | None = None, sources: str | None = None):
        q = ("SELECT f.id, f.source_id, f.path, count(m.id) files FROM folders f "
             f"JOIN media m ON m.folder_id = f.id AND m.status = {DONE} ")
        args: list = []
        if source_id is not None:
            q += "WHERE f.source_id = ? "
            args.append(source_id)
        elif (ids := _id_list(sources, "sources")) is not None:
            q += f"WHERE f.source_id IN ({','.join('?' * len(ids)) or 'NULL'}) "
            args += ids
        q += "GROUP BY f.id ORDER BY f.path"
        return [dict(r) for r in db.conn.execute(q, args)]

    @app.post("/api/folders/{folder_id}/unlink")
    def unlink_folder(folder_id: int):
        """Leave a folder (and its subfolders) out of the library. Files on disk are never touched."""
        if scanner.running:
            raise HTTPException(409, "Stop the scan first.")
        c = db.conn
        f = c.execute("SELECT f.id, f.path, f.source_id, s.path AS root FROM folders f "
                      "JOIN sources s ON s.id = f.source_id WHERE f.id = ?", (folder_id,)).fetchone()
        if not f:
            raise HTTPException(404)
        if os.path.normcase(f["path"]) == os.path.normcase(f["root"]):
            raise HTTPException(400, "This is the whole source folder. Remove it from the source folders instead.")
        prefix = os.path.normcase(f["path"]) + os.sep
        subs = [r[0] for r in c.execute("SELECT id, path FROM folders WHERE source_id = ? AND id != ?",
                                        (f["source_id"], folder_id)) if os.path.normcase(r[1]).startswith(prefix)]
        with transaction(c):
            n = c.execute("DELETE FROM media WHERE folder_id = ?", (folder_id,)).rowcount
            for sid in subs:
                n += c.execute("SELECT count(*) FROM media WHERE folder_id = ?", (sid,)).fetchone()[0]
                c.execute("DELETE FROM folders WHERE id = ?", (sid,))
            c.execute("UPDATE folders SET excluded = 1 WHERE id = ?", (folder_id,))
        return {"unlinked": folder_id, "files": n, "subfolders": len(subs)}

    @app.get("/api/folders/unlinked")
    def unlinked_folders():
        return [dict(r) for r in db.conn.execute(
            "SELECT id, source_id, path FROM folders WHERE excluded = 1 ORDER BY path")]

    @app.post("/api/folders/{folder_id}/relink")
    def relink_folder(folder_id: int):
        """Bring an unlinked folder back; it is read again by a scan of its source."""
        if scanner.running:
            raise HTTPException(409, "Stop the scan first.")
        r = db.conn.execute("SELECT source_id FROM folders WHERE id = ? AND excluded = 1", (folder_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        db.conn.execute("UPDATE folders SET excluded = 0 WHERE id = ?", (folder_id,))
        scanner.start([r["source_id"]])
        return {"relinked": folder_id}

    @app.get("/api/media")
    def media(
        f: "MediaFilter" = Depends(),
        cursor: str | None = Query(None, description="'<taken_at>:<id>' from the previous page"),
        limit: int = Query(200, ge=1, le=5000),
        order: str = Query("desc", pattern="^(asc|desc)$"),
    ):
        """Photos in date order with keyset pagination: constant speed at any
        depth, unlike OFFSET."""
        where, args = f.where()
        if cursor:
            try:
                t, i = (int(x) for x in cursor.split(":"))
            except ValueError:
                raise HTTPException(400, "Bad cursor")
            op = "<" if order == "desc" else ">"
            where.append(f"(m.taken_at {op} ? OR (m.taken_at = ? AND m.id {op} ?))")
            args += [t, t, i]
        direction = "DESC" if order == "desc" else "ASC"
        rows = db.conn.execute(
            f"SELECT {MEDIA_COLUMNS.replace('id,', 'm.id,', 1)} FROM media m WHERE {' AND '.join(where)} "
            f"ORDER BY m.taken_at {direction}, m.id {direction} LIMIT ?", (*args, limit)).fetchall()
        items = [_media_dict(r) for r in rows]
        nxt = f"{rows[-1]['taken_at']}:{rows[-1]['id']}" if len(rows) == limit else None
        return {"items": items, "next": nxt}

    @app.get("/api/timeline")
    def timeline(f: "MediaFilter" = Depends(), order: str = Query("desc", pattern="^(asc|desc)$")):
        """Photo counts per month for the same filters: lets the UI lay out
        the whole library (and a date scrubber) before loading any photos."""
        where, args = f.where()
        rows = db.conn.execute(
            "SELECT strftime('%Y-%m', m.taken_at, 'unixepoch') month, count(*) n FROM media m "
            f"WHERE {' AND '.join(where)} GROUP BY month ORDER BY month {order.upper()}", args).fetchall()
        return [{"month": r["month"], "count": r["n"]} for r in rows]

    @app.get("/api/media/{media_id}")
    def media_item(media_id: int):
        r = db.conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        d = _media_dict(r)
        d["sha256"] = r["sha256"].hex() if r["sha256"] else None
        d["same_content"] = [x[0] for x in db.conn.execute(
            "SELECT id FROM media WHERE sha256 = ? AND id != ? AND status = 1", (r["sha256"], media_id))] \
            if r["sha256"] else []
        d["faces"] = [dict(f) for f in db.conn.execute(
            "SELECT f.id, f.x, f.y, f.w, f.h, f.score, f.person_id, p.name person, f.assigned_by, f.cluster_id "
            "FROM faces f LEFT JOIN persons p ON p.id = f.person_id WHERE f.media_id = ? ORDER BY f.ordinal",
            (media_id,))]
        return d

    @app.get("/api/thumb/{media_id}")
    def thumb(media_id: int):
        r = db.conn.execute("SELECT sha256 FROM media WHERE id = ? AND has_thumb = 1", (media_id,)).fetchone()  # any status
        if not r:
            raise HTTPException(404)
        h = r[0].hex()
        p = settings.thumbs_dir / h[:2] / h[2:4] / f"{h}.webp"
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/webp",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get("/api/preview/{media_id}")
    def preview(media_id: int, size: int = Query(2048, ge=256, le=4096)):
        """Screen-sized JPEG of any photo (HEIC and RAW included). Cached on disk."""
        r = db.conn.execute("SELECT path, kind, sha256 FROM media WHERE id = ? AND status = 1 "
                            "AND kind != 'video'", (media_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        cache = settings.data_dir / "previews" / f"{r['sha256'].hex()}_{size}.jpg"
        if not cache.is_file():
            from .scanner import extract
            try:
                img = extract.load_rgb(r["path"], r["kind"], size)
            except Exception as e:
                raise HTTPException(422, f"Cannot open: {e}")
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            img.save(tmp, "JPEG", quality=88)
            os.replace(tmp, cache)
        return FileResponse(cache, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get("/api/file/{media_id}")
    def original(media_id: int):
        r = db.conn.execute("SELECT path FROM media WHERE id = ? AND status = 1", (media_id,)).fetchone()
        if not r or not os.path.isfile(long_path(r[0])):
            raise HTTPException(404)
        return FileResponse(long_path(r[0]))

    # ---------------------------------------------------------------- memories export

    def _ids_for(body) -> list[int]:
        if body.media_ids:
            return list(dict.fromkeys(body.media_ids))
        if body.filters is None:
            raise HTTPException(400, "Give media_ids or filters")
        allowed = {"source_id", "sources", "folder_id", "album_id", "kind", "q", "persons", "persons_mode", "date_from", "date_to",
                   "fav", "show", "tag", "pet", "bbox", "place"}
        bad = set(body.filters) - allowed
        if bad:
            raise HTTPException(400, f"Unknown filters: {', '.join(sorted(bad))}")
        vals = {k: body.filters.get(k) for k in allowed - {"persons_mode"}}
        if isinstance(vals["persons"], list):
            vals["persons"] = ",".join(str(int(x)) for x in vals["persons"])
        if isinstance(vals["sources"], list):
            vals["sources"] = ",".join(str(int(x)) for x in vals["sources"])
        vals["fav"] = bool(vals["fav"])
        vals["show"] = vals["show"] or "main"
        if vals["pet"] is not None:
            vals["pet"] = str(vals["pet"])
        f = MediaFilter(**vals, persons_mode=body.filters.get("persons_mode") or "all")
        where, args = f.where()
        return [r[0] for r in db.conn.execute(
            f"SELECT m.id FROM media m WHERE {' AND '.join(where)} ORDER BY m.taken_at, m.id", args)]

    @app.post("/api/exports")
    def create_export(body: ExportIn):
        ids = _ids_for(body)
        if not ids:
            raise HTTPException(400, "No photos match")
        if body.kind == "slideshow" and not settings.ffmpeg:
            raise HTTPException(400, "FFmpeg is not installed (bin folder)")
        try:
            jid = exporter.submit(body.kind, ids, body.options, body.title, body.output_dir)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        return {"id": jid, "photos": len(ids)}

    @app.post("/api/exports/preview")
    def preview_export(body: ExportIn):
        """What a slideshow or collage will look like before it is made: for a
        slideshow, the photos it will use with their captions; for a collage, a
        quick small rendering."""
        import base64
        import io
        from .exports import collage as collage_mod, selection
        ids = _ids_for(body)
        o = dict(body.options)
        o.pop("output_dir", None)
        max_photos = int(o.get("max_photos") or (40 if body.kind == "collage" else 150))
        items = selection.prepare(db.conn, ids, max_photos, bool(o.get("skip_duplicates", True)))
        if body.kind == "slideshow":
            person = None
            if o.get("captions") == "date_age" and o.get("person_id"):
                r = db.conn.execute("SELECT name, birth_date FROM persons WHERE id = ?", (o["person_id"],)).fetchone()
                person = dict(r) if r else None
            return {"used": len(items), "items": [
                {"id": it["id"], "caption": selection.caption_for(it, o.get("captions") or "none", person)}
                for it in items]}
        if body.kind != "collage":
            raise HTTPException(400, "kind must be slideshow or collage")
        try:
            opts = collage_mod.CollageOptions(**{k: v for k, v in o.items()
                                                 if k in collage_mod.CollageOptions.__dataclass_fields__})
        except TypeError as e:
            raise HTTPException(400, str(e))
        if not items:
            raise HTTPException(400, "No photos match")
        out = io.BytesIO()
        collage_mod.render(db.conn, items, opts, out, lambda f, m: None, threading.Event(), scale_to=1100)
        return {"used": len(items), "image": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()}

    @app.get("/api/exports")
    def list_exports(limit: int = Query(50, ge=1, le=500)):
        rows = db.conn.execute("SELECT id, kind, title, status, progress, message, output_path, created_at, "
                               "finished_at, json_array_length(media_ids) photos FROM exports "
                               "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) | {"exists": bool(r["output_path"]) and os.path.isfile(r["output_path"])} for r in rows]

    @app.post("/api/exports/{export_id}/cancel")
    def cancel_export(export_id: int):
        exporter.cancel(export_id)
        return {"ok": True}

    def _export_path(export_id: int) -> str:
        r = db.conn.execute("SELECT output_path FROM exports WHERE id = ? AND status = 'done'", (export_id,)).fetchone()
        if not r or not r[0] or not os.path.isfile(r[0]):
            raise HTTPException(404, "File not found (moved or deleted?)")
        return r[0]

    @app.get("/api/exports/{export_id}/file")
    def export_file(export_id: int):
        return FileResponse(_export_path(export_id))

    @app.get("/api/exports/{export_id}/thumb")
    def export_thumb(export_id: int):
        """A small picture of a finished collage or slideshow (a frame from the video), cached."""
        path = _export_path(export_id)
        kind = db.conn.execute("SELECT kind FROM exports WHERE id = ?", (export_id,)).fetchone()[0]
        out_dir = settings.data_dir / "export_thumbs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{export_id}_{os.stat(path).st_mtime_ns}.jpg"
        if not out.is_file():
            for old in out_dir.glob(f"{export_id}_*.jpg"):
                old.unlink(missing_ok=True)
            if kind == "slideshow":
                if not settings.ffmpeg:
                    raise HTTPException(404, "No picture for this video")
                import subprocess
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                for at in ("2", "0"):
                    subprocess.run([settings.ffmpeg, "-y", "-loglevel", "error", "-ss", at, "-i", path,
                                    "-frames:v", "1", "-vf", "scale=480:-2", str(out)],
                                   capture_output=True, timeout=60, creationflags=flags)
                    if out.is_file():
                        break
                if not out.is_file():
                    raise HTTPException(404, "No picture for this video")
            else:
                from PIL import Image
                with Image.open(path) as im:
                    im.thumbnail((480, 480))
                    im.convert("RGB").save(out, quality=85)
        return FileResponse(out, media_type="image/jpeg")

    @app.post("/api/exports/{export_id}/reveal")
    def reveal_export(export_id: int):
        """Show the file in Explorer / Finder."""
        _open_folder(_export_path(export_id), select=True)
        return {"ok": True}

    # ---------------------------------------------------------------- photo edits

    def _edit(fn, *a):
        try:
            return fn(*a)
        except EditError as e:
            raise HTTPException(400, str(e))
        except OSError as e:
            raise HTTPException(400, f"Could not write the file: {e}")

    @app.post("/api/edit/{media_id}/preview")
    def edit_preview(media_id: int, body: EditPreviewIn):
        from fastapi.responses import Response
        size = min(max(body.size, 256), 2400)
        return Response(_edit(editor.preview, media_id, body.ops, size), media_type="image/jpeg")

    @app.post("/api/edit/{media_id}/save")
    def edit_save(media_id: int, body: EditSaveIn):
        return _edit(editor.save, media_id, body.ops, body.mode, body.dest_dir)

    @app.get("/api/edit/{media_id}/info")
    def edit_info(media_id: int):
        return editor.info(media_id)

    @app.post("/api/edit/{media_id}/restore")
    def edit_restore(media_id: int):
        return _edit(editor.restore, media_id)

    @app.post("/api/edit/batch")
    def edit_batch(body: EditBatchIn):
        if not body.media_ids:
            raise HTTPException(400, "No photos selected")
        _edit(editor.batch_start, body.media_ids, body.ops, body.mode, body.dest_dir)
        return editor.batch_status()

    @app.get("/api/edit/batch")
    def edit_batch_status():
        return editor.batch_status()

    @app.post("/api/edit/batch/cancel")
    def edit_batch_cancel():
        editor.batch_cancel()
        return {"ok": True}

    # ---------------------------------------------------------------- organise into folders

    @app.get("/api/organize/presets")
    def organize_presets():
        return {"folders": {k: {"label": l, "template": t} for k, (l, t) in org_templates.FOLDER_PRESETS.items()},
                "names": {k: {"label": l, "template": t} for k, (l, t) in org_templates.NAME_PRESETS.items()},
                "tokens": org_templates.TOKENS}

    def _org(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except OrganizeError as e:
            raise HTTPException(400, str(e))

    def _org_ids(body: OrganizeIn) -> list[int]:
        ids = _ids_for(body)
        if body.people and ids:
            if body.people_mode not in ("any", "all"):
                raise HTTPException(400, "people_mode must be any or all")
            cond, pargs = people.media_filter_sql(body.people, body.people_mode)
            keep: set[int] = set()
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                keep.update(r[0] for r in db.conn.execute(
                    f"SELECT m.id FROM media m WHERE {cond} AND m.id IN ({','.join('?' * len(chunk))})", pargs + chunk))
            ids = [i for i in ids if i in keep]
        return ids

    @app.post("/api/organize/preview")
    def organize_preview(body: OrganizeIn):
        ids = _org_ids(body)
        if not ids:
            raise HTTPException(400, "No photos match")
        return _org(organizer.preview, ids, body.dest_root, body.mode, body.options)

    @app.post("/api/organize")
    def organize_start(body: OrganizeIn):
        ids = _org_ids(body)
        if not ids:
            raise HTTPException(400, "No photos match")
        if body.mode == "move" and scanner.running:
            raise HTTPException(409, "Wait for the scan to finish before moving files.")
        return {"id": _org(organizer.start, ids, body.dest_root, body.mode, body.options)}

    @app.get("/api/organize/rules")
    def organize_rules():
        return rules.list()

    @app.post("/api/organize/rules")
    def organize_rule_create(body: RuleIn):
        if not body.people:
            raise HTTPException(400, "Tick at least one person for a rule")
        return {"id": _org(rules.create, body.name, body.dest_root, body.mode, body.options,
                           body.people, body.people_mode)}

    @app.patch("/api/organize/rules/{rule_id}")
    def organize_rule_patch(rule_id: int, body: RulePatch):
        _org(rules.update, rule_id, body.name, body.enabled, dest_root=body.dest_root, mode=body.mode,
             options=body.options, people=body.people, people_mode=body.people_mode)
        return {"ok": True}

    @app.delete("/api/organize/rules/{rule_id}")
    def organize_rule_delete(rule_id: int):
        rules.delete(rule_id)
        return {"ok": True}

    @app.post("/api/organize/rules/run")
    def organize_rules_run(body: RuleRunIn):
        if scanner.running and any(r["mode"] == "move" and r["enabled"] for r in rules.list()):
            raise HTTPException(409, "Wait for the scan to finish before running rules that move files.")
        return {"results": rules.run(body.rule_ids)}

    @app.get("/api/organize")
    def organize_jobs(limit: int = Query(50, ge=1, le=500)):
        return [dict(r) for r in db.conn.execute(
            "SELECT id, status, mode, dest_root, planned, done, skipped, failed, bytes, progress, message, "
            "manifest_path, created_at, finished_at, undone_at FROM organize_jobs ORDER BY id DESC LIMIT ?", (limit,))]

    @app.post("/api/organize/{job_id}/cancel")
    def organize_cancel(job_id: int):
        organizer.cancel(job_id)
        return {"ok": True}

    @app.post("/api/organize/{job_id}/undo")
    def organize_undo(job_id: int):
        if scanner.running:
            raise HTTPException(409, "Wait for the scan to finish.")
        return _org(organizer.undo, job_id)

    @app.post("/api/organize/{job_id}/reveal")
    def organize_reveal(job_id: int):
        r = db.conn.execute("SELECT dest_root FROM organize_jobs WHERE id = ?", (job_id,)).fetchone()
        if not r or not os.path.isdir(r[0]):
            raise HTTPException(404, "Folder not found")
        _open_folder(r[0])
        return {"ok": True}

    # ---------------------------------------------------------------- albums

    @app.get("/api/albums")
    def albums():
        return [dict(r) for r in db.conn.execute(
            "SELECT a.id, a.name, a.created_at, count(m.id) photos, "
            "coalesce(a.cover_media_id, (SELECT ai.media_id FROM album_items ai JOIN media x ON x.id = ai.media_id "
            "  WHERE ai.album_id = a.id AND x.status = 1 AND x.has_thumb = 1 ORDER BY x.taken_at DESC LIMIT 1)) cover_media_id "
            "FROM albums a LEFT JOIN (album_items i JOIN media m ON m.id = i.media_id AND m.status = 1) ON i.album_id = a.id "
            "GROUP BY a.id ORDER BY a.name COLLATE NOCASE")]

    def _album_name(name: str, album_id: int | None = None) -> str:
        name = name.strip()
        if not name:
            raise HTTPException(400, "Album name cannot be empty")
        r = db.conn.execute("SELECT id FROM albums WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if r and r[0] != album_id:
            raise HTTPException(409, f"An album called '{name}' already exists")
        return name

    def _add_items(album_id: int, ids: list[int]) -> int:
        start = db.conn.execute("SELECT coalesce(max(position), 0) FROM album_items WHERE album_id = ?",
                                (album_id,)).fetchone()[0]
        with transaction(db.conn):
            before = db.conn.total_changes
            db.conn.executemany("INSERT OR IGNORE INTO album_items(album_id, media_id, position) VALUES (?, ?, ?)",
                                [(album_id, m, start + k + 1) for k, m in enumerate(dict.fromkeys(ids))])
            return db.conn.total_changes - before

    @app.post("/api/albums")
    def create_album(body: AlbumIn):
        name = _album_name(body.name)
        aid = db.conn.execute("INSERT INTO albums(name, created_at) VALUES (?, ?)", (name, int(time.time()))).lastrowid
        added = _add_items(aid, body.media_ids) if body.media_ids else 0
        return {"id": aid, "name": name, "added": added}

    @app.patch("/api/albums/{album_id}")
    def rename_album(album_id: int, body: AlbumIn):
        name = _album_name(body.name, album_id)
        if not db.conn.execute("UPDATE albums SET name = ? WHERE id = ?", (name, album_id)).rowcount:
            raise HTTPException(404)
        return {"ok": True}

    @app.delete("/api/albums/{album_id}")
    def delete_album(album_id: int):
        """Deletes the album only; the photos stay where they are."""
        with transaction(db.conn):
            db.conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        return {"ok": True}

    @app.post("/api/albums/{album_id}/add")
    def album_add(album_id: int, body: AlbumItemsIn):
        if not db.conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone():
            raise HTTPException(404)
        return {"added": _add_items(album_id, body.media_ids)}

    @app.post("/api/albums/{album_id}/remove")
    def album_remove(album_id: int, body: AlbumItemsIn):
        ids = list(dict.fromkeys(body.media_ids))
        with transaction(db.conn):
            n = sum(db.conn.execute("DELETE FROM album_items WHERE album_id = ? AND media_id = ?",
                                    (album_id, m)).rowcount for m in ids)
        return {"removed": n}

    # ---------------------------------------------------------------- Google Photos

    def _g(fn, *a):
        try:
            return fn(*a)
        except GoogleError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/gphotos/status")
    def gphotos_status():
        return gauth.status() | {"import": gimport.state, "default_folder": str(default_import_dir())}

    @app.post("/api/gphotos/client")
    def gphotos_client(body: GoogleClientIn):
        _g(gauth.set_client, body.client_id, body.client_secret)
        return gauth.status()

    @app.post("/api/gphotos/connect")
    def gphotos_connect():
        """Opens Google's sign-in page in the default browser."""
        return {"auth_url": _g(gauth.start_login)}

    @app.post("/api/gphotos/disconnect")
    def gphotos_disconnect():
        gauth.disconnect()
        return gauth.status()

    @app.post("/api/gphotos/import")
    def gphotos_import(body: GoogleImportIn):
        """Opens Google's photo picker in the browser; downloads what is chosen."""
        return _g(gimport.start, body.folder)

    @app.post("/api/gphotos/import/cancel")
    def gphotos_import_cancel():
        gimport.cancel()
        return gimport.state

    # ---------------------------------------------------------------- duplicates

    def _not_scanning():
        if scanner.running and scanner.status()["state"] not in ("faces", "clustering"):
            raise HTTPException(409, "Wait for the scan to finish.")

    @app.post("/api/dedup/run")
    def dedup_run():
        if not scanner.start(mode="dedup"):
            raise HTTPException(409, "A scan is already running.")
        return scanner.status()

    @app.get("/api/dedup/summary")
    def dedup_summary():
        c = db.conn
        kinds = {r["kind"]: dict(r) for r in c.execute(
            "SELECT kind, count(*) groups, sum(size) files, sum(reclaim_bytes) reclaim_bytes "
            "FROM dup_groups GROUP BY kind")}
        rem = c.execute("SELECT count(*) n, coalesce(sum(size), 0) bytes FROM removals "
                        "WHERE restored_at IS NULL").fetchone()
        return {
            "kinds": {k: kinds.get(k, {"kind": k, "groups": 0, "files": 0, "reclaim_bytes": 0})
                      for k in ("exact", "near", "burst")},
            "removed_files": rem["n"], "removed_bytes": rem["bytes"],
            "recycle_available": sys.platform == "win32",
        }

    @app.get("/api/dedup/groups")
    def dedup_groups(kind: str = Query(..., pattern="^(exact|near|burst)$"),
                     sort: str = Query("size", pattern="^(size|date)$"),
                     limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0)):
        c = db.conn
        order = "g.reclaim_bytes DESC, g.id" if sort == "size" else "g.taken_at DESC, g.id"
        groups = [dict(r) for r in c.execute(
            f"SELECT g.* FROM dup_groups g WHERE g.kind = ? ORDER BY {order} LIMIT ? OFFSET ?",
            (kind, limit, offset))]
        if not groups:
            return {"groups": [], "total": c.execute("SELECT count(*) FROM dup_groups WHERE kind = ?",
                                                     (kind,)).fetchone()[0]}
        marks = ",".join("?" * len(groups))
        members = c.execute(
            f"SELECT dm.group_id, dm.rank, dm.keep, m.id, m.path, m.filename, m.size, m.width, m.height, "
            f"m.taken_at, m.date_source, m.camera_model, m.sharpness, m.has_thumb, m.kind, m.status, "
            f"(SELECT count(*) - 1 FROM media x WHERE x.sha256 = m.sha256 AND x.status = 1) copies "
            f"FROM dup_members dm JOIN media m ON m.id = dm.media_id "
            f"WHERE dm.group_id IN ({marks}) ORDER BY dm.group_id, dm.rank",
            [g["id"] for g in groups]).fetchall()
        by_group: dict[int, list] = {g["id"]: [] for g in groups}
        for m in members:
            d = dict(m)
            d["taken"] = wall_to_iso(d.pop("taken_at"))
            d["folder"] = d["path"][: len(d["path"]) - len(d["filename"]) - 1]
            by_group[d.pop("group_id")].append(d)
        for g in groups:
            g["members"] = by_group[g["id"]]
            g["taken"] = wall_to_iso(g.pop("taken_at"))
        total = c.execute("SELECT count(*) FROM dup_groups WHERE kind = ?", (kind,)).fetchone()[0]
        return {"groups": groups, "total": total}

    @app.post("/api/dedup/groups/{group_id}/remove")
    def dedup_remove(group_id: int, body: RemoveIn):
        _not_scanning()
        try:
            return dedup_actions.remove(db.conn, group_id, body.remove, body.method)
        except dedup_actions.RemovalError as e:
            raise HTTPException(400, str(e))

    # Applying to thousands of groups re-hashes every file, so it runs in the
    # background; the UI polls /api/dedup/apply/status.
    bulk = {"state": "idle", "done": 0, "total": 0, "removed": 0, "bytes": 0,
            "failed": [], "failed_count": 0, "batch": None, "kind": None}
    bulk_cancel = threading.Event()

    def _run_bulk(group_ids: list[int], method: str, batch: str) -> None:
        conn = db.connect()
        try:
            for gid in group_ids:
                if bulk_cancel.is_set():
                    bulk["state"] = "cancelled"
                    break
                ids = [r[0] for r in conn.execute(
                    "SELECT media_id FROM dup_members WHERE group_id = ? AND keep = 0", (gid,))]
                try:
                    r = dedup_actions.remove(conn, gid, ids, method, batch) if ids else {"removed": [], "failed": []}
                    bulk["removed"] += len(r["removed"])
                    new_failed = [dict(f, group=gid) for f in r["failed"]]
                except dedup_actions.RemovalError as e:
                    new_failed = [{"group": gid, "error": str(e)}]
                bulk["failed_count"] += len(new_failed)
                bulk["failed"] = (bulk["failed"] + new_failed)[:50]
                bulk["done"] += 1
                if bulk["done"] % 20 == 0:
                    bulk["bytes"] = conn.execute("SELECT coalesce(sum(size), 0) FROM removals WHERE batch = ?",
                                                 (batch,)).fetchone()[0]
            bulk["bytes"] = conn.execute("SELECT coalesce(sum(size), 0) FROM removals WHERE batch = ?",
                                         (batch,)).fetchone()[0]
            if bulk["state"] == "running":
                bulk["state"] = "done"
        except Exception as e:
            bulk.update(state="error", error=str(e)[:300])
        finally:
            conn.close()

    @app.post("/api/dedup/apply")
    def dedup_apply(body: ApplyIn):
        """Apply the suggestion (move every 'remove' member away) to many groups, in the background."""
        _not_scanning()
        if bulk["state"] == "running":
            raise HTTPException(409, "Already applying suggestions.")
        if body.kind not in ("exact", "near", "burst"):
            raise HTTPException(400, "Bad kind")
        q = "SELECT id FROM dup_groups WHERE kind = ?"
        args: list = [body.kind]
        if body.group_ids:
            q += f" AND id IN ({','.join('?' * len(body.group_ids))})"
            args += body.group_ids
        ids = [r[0] for r in db.conn.execute(q, args)]
        batch = secrets.token_hex(6)
        bulk_cancel.clear()
        bulk.clear()
        bulk.update(state="running", done=0, total=len(ids), removed=0, bytes=0, failed=[], failed_count=0,
                    batch=batch, kind=body.kind)
        threading.Thread(target=_run_bulk, args=(ids, body.method, batch), daemon=True, name="dedup-bulk").start()
        return bulk

    @app.get("/api/dedup/apply/status")
    def dedup_apply_status():
        return bulk

    @app.post("/api/dedup/apply/cancel")
    def dedup_apply_cancel():
        bulk_cancel.set()
        return bulk

    @app.post("/api/dedup/groups/{group_id}/ignore")
    def dedup_ignore(group_id: int):
        _not_scanning()
        dedup_actions.ignore_group(db.conn, group_id)
        return {"ok": True}

    @app.get("/api/removals")
    def removals(limit: int = Query(200, ge=1, le=2000)):
        rows = db.conn.execute(
            "SELECT r.*, m.has_thumb FROM removals r LEFT JOIN media m ON m.id = r.media_id "
            "ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/removals/undo")
    def removals_undo(body: UndoIn):
        _not_scanning()
        ids = list(body.ids or [])
        if body.batch:
            ids += [r[0] for r in db.conn.execute(
                "SELECT id FROM removals WHERE batch = ? AND restored_at IS NULL", (body.batch,))]
        if not ids:
            raise HTTPException(400, "Nothing to undo")
        return dedup_actions.undo(db.conn, ids)

    # ---------------------------------------------------------------- faces & people

    def _people_call(fn, *a, **kw):
        if scanner.status()["state"] == "clustering":
            raise HTTPException(409, "Faces are being regrouped; try again in a moment.")
        try:
            return fn(db.conn, *a, **kw)
        except PeopleError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/faces/status")
    def faces_status():
        c = db.conn
        row = c.execute(
            "SELECT count(*) faces, count(DISTINCT f.media_id) photos, "
            "count(DISTINCT CASE WHEN f.person_id IS NULL AND f.cluster_id >= 0 THEN f.cluster_id END) unnamed_clusters, "
            "coalesce(sum(f.person_id IS NOT NULL), 0) named_faces, coalesce(sum(f.cluster_id IS NULL), 0) unclustered, "
            "coalesce(sum(f.cluster_id < 0 AND f.person_id IS NULL), 0) ungrouped "
            "FROM faces f JOIN media m ON m.id = f.media_id AND m.status = 1").fetchone()
        todo = c.execute("SELECT count(*) FROM media WHERE status = 1 AND faces_status = 0 "
                         "AND kind IN ('image', 'raw')").fetchone()[0]
        failed = c.execute("SELECT count(*) FROM media WHERE status = 1 AND faces_status = 2").fetchone()[0]
        doubtful = c.execute("SELECT count(*) FROM face_suggestions s JOIN faces f ON f.id = s.face_id "
                             "WHERE f.person_id IS NULL").fetchone()[0]
        return dict(row) | {
            "photos_todo": todo,
            "photos_failed": failed,
            "suggestions": doubtful,
            "persons": c.execute("SELECT count(*) FROM persons").fetchone()[0],
            "available": face_models.available(),
            "missing": face_models.missing(),
        }

    @app.post("/api/faces/run")
    def faces_run(body: FacesRunIn):
        if body.mode not in ("faces", "recheck", "cluster"):
            raise HTTPException(400, "mode must be 'faces', 'recheck' or 'cluster'")
        if not face_models.available():
            raise HTTPException(400, "Face models missing: " + ", ".join(face_models.missing()))
        if not scanner.start(mode=body.mode):
            raise HTTPException(409, "A scan is already running.")
        return scanner.status()

    @app.get("/api/people")
    def list_people(include_hidden: bool = False):
        rows = db.conn.execute(
            "SELECT p.id, p.name, p.birth_date, p.hidden, count(f.id) faces, count(DISTINCT f.media_id) photos, "
            "coalesce(p.cover_face_id, (SELECT f2.id FROM faces f2 WHERE f2.person_id = p.id "
            "  ORDER BY f2.assigned_by != 'auto' DESC, f2.score * min(f2.size_px, 200) DESC LIMIT 1)) cover_face_id "
            "FROM persons p LEFT JOIN (faces f JOIN media m ON m.id = f.media_id AND m.status = 1) "
            "  ON f.person_id = p.id "
            + ("" if include_hidden else "WHERE p.hidden = 0 ")
            + "GROUP BY p.id ORDER BY photos DESC, p.name").fetchall()
        return [dict(r) for r in rows]

    @app.patch("/api/people/{person_id}")
    def patch_person(person_id: int, body: PersonPatch):
        if body.birth_date:
            _day_seconds(body.birth_date, False)  # validates the format
        _people_call(people.rename_person, person_id, body.name, body.birth_date, body.hidden, body.cover_face_id)
        return {"ok": True}

    @app.post("/api/people/{person_id}/merge")
    def merge_people(person_id: int, body: MergeIn):
        return {"moved_faces": _people_call(people.merge_persons, person_id, body.source_ids)}

    @app.delete("/api/people/{person_id}")
    def delete_person(person_id: int):
        _people_call(people.delete_person, person_id)
        return {"ok": True}

    @app.get("/api/clusters")
    def clusters(min_size: int = Query(3, ge=1), limit: int = Query(100, ge=1, le=1000),
                 offset: int = Query(0, ge=0)):
        """Unnamed groups of similar faces, biggest first, with sample faces."""
        rows = db.conn.execute(
            "WITH c AS (SELECT f.cluster_id, count(*) n FROM faces f JOIN media m ON m.id = f.media_id "
            "  AND m.status = 1 WHERE f.person_id IS NULL AND f.cluster_id >= 0 "
            "  GROUP BY f.cluster_id HAVING n >= ? ORDER BY n DESC LIMIT ? OFFSET ?) "
            "SELECT c.cluster_id, c.n faces, (SELECT group_concat(id) FROM (SELECT f2.id FROM faces f2 "
            "  WHERE f2.cluster_id = c.cluster_id AND f2.person_id IS NULL "
            "  ORDER BY f2.score * min(f2.size_px, 200) DESC LIMIT 8)) samples "
            "FROM c ORDER BY c.n DESC", (min_size, limit, offset)).fetchall()
        return [{"cluster_id": r["cluster_id"], "faces": r["faces"],
                 "sample_face_ids": [int(x) for x in (r["samples"] or "").split(",") if x]} for r in rows]

    @app.post("/api/clusters/{cluster_id}/name")
    def name_cluster(cluster_id: int, body: NameIn):
        return _people_call(people.name_cluster, cluster_id, body.name)

    @app.get("/api/faces")
    def list_faces(person_id: int | None = None, cluster_id: int | None = None,
                   limit: int = Query(200, ge=1, le=2000), after_id: int = 0):
        if (person_id is None) == (cluster_id is None):
            raise HTTPException(400, "Give person_id or cluster_id")
        col, val = ("f.person_id", person_id) if person_id is not None else ("f.cluster_id", cluster_id)
        rows = db.conn.execute(
            f"SELECT f.id, f.media_id, f.score, f.size_px, f.sharpness, f.assigned_by, f.cluster_id, f.person_id, "
            f"m.taken_at FROM faces f JOIN media m ON m.id = f.media_id AND m.status = 1 "
            f"WHERE {col} = ? AND f.id > ? ORDER BY f.id LIMIT ?", (val, after_id, limit)).fetchall()
        return [dict(r) | {"taken": wall_to_iso(r["taken_at"])} for r in rows]

    @app.get("/api/faces/suggestions")
    def face_suggestions(person_id: int | None = None, limit: int = Query(100, ge=1, le=1000),
                         after_score: float | None = None):
        """Doubtful new faces to confirm ("Is this X?"), surest first. Confirm
        with POST /api/faces/assign, reject with POST /api/faces/reject."""
        return people.suggestions(db.conn, person_id, limit, after_score)

    @app.post("/api/faces/assign")
    def assign(body: AssignIn):
        if not body.face_ids or (body.person_id is None and not body.name):
            raise HTTPException(400, "Give face_ids and person_id or name")
        return {"updated": _people_call(people.assign_faces, body.face_ids, body.person_id, body.name)}

    @app.post("/api/faces/reject")
    def reject(body: RejectIn):
        if not body.face_ids:
            raise HTTPException(400, "Give face_ids")
        return {"updated": _people_call(people.reject_faces, body.face_ids, body.person_id)}

    @app.get("/api/face/{face_id}/chip")
    def face_chip(face_id: int):
        r = db.conn.execute("SELECT chip FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not r or not r[0]:
            raise HTTPException(404)
        path = (settings.thumbs_dir / r[0]).resolve()
        if settings.thumbs_dir.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/webp",
                            headers={"Cache-Control": "private, max-age=86400"})

    # ---------------------------------------------------------------- themes and music

    @app.get("/api/themes")
    def themes():
        return content.themes()

    @app.get("/api/music")
    def music():
        return content.music()

    @app.get("/api/music/file")
    def music_file(id: str):
        path = content.music_path(id)
        if not path:
            raise HTTPException(404)
        return FileResponse(path)

    @app.post("/api/music/open-folder")
    def music_open_folder():
        content.music_dir.mkdir(parents=True, exist_ok=True)
        _open_folder(str(content.music_dir))
        return {"path": str(content.music_dir)}

    # ---------------------------------------------------------------- update software

    def _u(fn, *a, **k):
        try:
            return fn(*a, **k)
        except UpdateError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/updates")
    def updates_status():
        return updater.status()

    @app.post("/api/updates/check")
    def updates_check():
        return _u(updater.check)

    @app.post("/api/updates/url")
    def updates_url(body: UpdateUrlIn):
        _u(updater.set_manifest_url, body.url)
        return updater.status()

    @app.post("/api/updates/install")
    def updates_install(body: UpdateInstallIn):
        _u(updater.start, body.packs, body.app)
        return updater.status()

    @app.post("/api/updates/cancel")
    def updates_cancel():
        updater.cancel()
        return updater.status()

    @app.post("/api/updates/apply")
    def updates_apply():
        if scanner.running:
            raise HTTPException(409, "Pause the scan first.")
        return _u(updater.apply)

    # ------------------------------------------------------------ viewer tools
    from fastapi.responses import Response
    from . import printing, system, viewer_tools

    def _row(media_id: int):
        r = db.conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        return r

    @app.get("/api/media/{media_id}/details")
    def media_details(media_id: int):
        r = _row(media_id)
        return viewer_tools.details(r["path"], r["kind"])

    @app.get("/api/media/{media_id}/frames")
    def media_frames(media_id: int):
        r = _row(media_id)
        if r["kind"] != "image" or (r["ext"] or "").lower() not in viewer_tools.ANIMATED_EXTS:
            return {"count": 1, "durations": []}
        try:
            return viewer_tools.frames_info(r["path"])
        except Exception as e:
            raise HTTPException(422, f"Cannot open: {e}")

    @app.get("/api/media/{media_id}/frame/{n}")
    def media_frame(media_id: int, n: int, size: int = Query(2048, ge=128, le=4096)):
        r = _row(media_id)
        import io
        buf = io.BytesIO()
        viewer_tools.frame(r["path"], n, size).save(buf, "PNG", compress_level=1)
        return Response(buf.getvalue(), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/media/{media_id}/frames/save")
    def media_frames_save(media_id: int, body: FramesSaveIn):
        r = _row(media_id)
        stem = os.path.splitext(r["filename"])[0]
        out = os.path.join(str(settings.exports_dir), "Frames", stem)
        saved = viewer_tools.save_frames(r["path"], out, body.which)
        if saved:
            _open_folder(saved[0] if len(saved) == 1 else out, select=len(saved) == 1)
        return {"saved": len(saved), "folder": out}

    @app.post("/api/print")
    def print_pdf(body: PrintIn):
        if body.layout not in printing.LAYOUTS or body.paper not in printing.PAPERS:
            raise HTTPException(400, "Unknown paper or layout")
        if not body.media_ids:
            raise HTTPException(400, "Choose photos first")
        if len(body.media_ids) > 1000:
            raise HTTPException(400, "At most 1,000 photos in one PDF")
        rows = []
        for c in range(0, len(body.media_ids), 900):
            chunk = body.media_ids[c:c + 900]
            rows += db.conn.execute(
                f"SELECT id, path, kind, filename, taken_at FROM media WHERE status = 1 AND kind != 'video' "
                f"AND id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        order = {m: i for i, m in enumerate(body.media_ids)}
        items = sorted((dict(r) for r in rows), key=lambda r: order[r["id"]])
        name = "".join(ch for ch in (body.title or "Print") if ch not in '<>:"/\\|?*').strip() or "Print"
        out = settings.exports_dir / f"{name} {datetime.now():%Y-%m-%d %H%M%S}.pdf"
        try:
            pages = printing.render(db.conn, items, out, body.paper, body.layout, body.landscape, body.captions,
                                    body.contact_cols)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"path": str(out), "pages": pages, "photos": len(items)}

    @app.post("/api/open-pdf")
    def open_pdf(body: PathIn):
        """Open a PDF this app made (Print) in the PC's PDF viewer, to print it."""
        p = os.path.abspath(body.path)
        if not p.lower().endswith(".pdf") or os.path.dirname(p) != os.path.abspath(str(settings.exports_dir)) \
                or not os.path.isfile(p):
            raise HTTPException(400, "Not a PDF made by Photo Explorer")
        system.open_file(p)
        return {"ok": True}

    @app.post("/api/reveal-file")
    def reveal_file(body: PathIn):
        p = os.path.abspath(body.path)
        if not os.path.exists(p):
            raise HTTPException(404)
        _open_folder(p, select=True)
        return {"ok": True}

    # ---- "Open with Photo Explorer": files opened from Explorer
    opened: list[str] = []

    @app.post("/api/opened")
    def opened_push(body: PathIn):
        opened.append(_loose(body.path))
        return {"ok": True}

    @app.get("/api/opened")
    def opened_pop():
        out = list(opened)
        opened.clear()
        return out

    def _loose(path: str) -> str:
        p = os.path.abspath(path)
        if p.rsplit(".", 1)[-1].lower() not in viewer_tools.LOOSE_EXTS or not os.path.isfile(long_path(p)):
            raise HTTPException(404, "Not a picture this viewer can open")
        return p

    @app.get("/api/loose/list")
    def loose_list(path: str):
        p = _loose(path)
        files = viewer_tools.folder_images(p)
        known = {}
        for c in range(0, len(files), 900):
            chunk = files[c:c + 900]
            for r in db.conn.execute(f"SELECT id, path FROM media WHERE status = 1 AND path IN "
                                     f"({','.join('?' * len(chunk))})", chunk):
                known[r["path"]] = r["id"]
        return {"files": [{"path": f, "name": os.path.basename(f), "media_id": known.get(f)} for f in files],
                "index": files.index(p) if p in files else 0, "folder": os.path.dirname(p)}

    @app.get("/api/loose/preview")
    def loose_preview(path: str, size: int = Query(2048, ge=128, le=4096)):
        p = _loose(path)
        try:
            data = viewer_tools.loose_preview(p, size)
        except Exception as e:
            raise HTTPException(422, f"Cannot open: {e}")
        return Response(data, media_type="image/jpeg")

    @app.get("/api/loose/details")
    def loose_details(path: str):
        p = _loose(path)
        st = os.stat(long_path(p))
        d = viewer_tools.details(p, "image")
        d["size"], d["name"] = st.st_size, os.path.basename(p)
        return d

    @app.get("/api/system/open-with")
    def open_with_status():
        return system.open_with_status()

    @app.post("/api/system/open-with")
    def open_with_set(body: OpenWithIn):
        try:
            return system.set_open_with(body.enable)
        except system.SystemError_ as e:
            raise HTTPException(400, str(e))

    # ---------------------------------------------------------------- automatic scan

    def _busy() -> bool:
        """Something else is moving or creating library files right now."""
        return (scanner.running or organizer.busy or bulk.get("state") == "running" or gimport.running
                or editor.batch_status().get("state") == "running")

    if os.environ.get("PHOTOEXPLORER_AUTOSCAN", "1") != "0":
        _start_autoscan(scanner, _busy)
    return app


AUTOSCAN_FIRST_S = 60      # after start-up, so the window opens without a scan competing
AUTOSCAN_EVERY_S = 600


def _start_autoscan(scanner: ScanManager, busy) -> None:
    """Pick up new, changed, moved and deleted files without the user pressing
    Scan: a quick walk now and then ('auto' mode), which goes on to index,
    faces and grouping only when the walk found something. Unchanged files
    cost a directory listing, nothing more."""
    def run():
        time.sleep(AUTOSCAN_FIRST_S)
        while True:
            try:
                if not busy():
                    scanner.start(mode="auto")
            except Exception:  # never let the timer die
                import logging
                logging.getLogger(__name__).exception("automatic scan failed to start")
            time.sleep(AUTOSCAN_EVERY_S)
    threading.Thread(target=run, daemon=True, name="autoscan").start()


def _day_seconds(day: str, end: bool) -> int:
    """'YYYY-MM-DD' -> wall seconds at 00:00 (the next day's 00:00 if end)."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, f"Bad date: {day}")
    if end:
        d += timedelta(days=1)
    return wall_seconds(datetime(d.year, d.month, d.day))


def _open_folder(path: str, select: bool = False) -> None:
    """Show a folder (or a file, selected) in Explorer / Finder / the file manager."""
    import subprocess
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)] if select else ["explorer", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if select else ["open", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if select else path])


def _has(module: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module) is not None


def _watch_parent(pid: int) -> None:
    """Exit when the desktop shell process ends (including a crash).

    Polls the PID rather than blocking on stdin: on Windows a thread blocked
    reading a stdin pipe stops multiprocessing workers from starting.
    """
    def alive() -> bool:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not h:
                return False
            try:
                return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT = still running
            finally:
                k32.CloseHandle(h)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def run():
        while alive():
            time.sleep(2)
        os._exit(0)
    threading.Thread(target=run, daemon=True, name="parent-watch").start()


def serve(settings: Settings, port: int = 0, token: str | None = None, parent_pid: int | None = None) -> None:
    import uvicorn

    token = token or secrets.token_urlsafe(24)
    app = create_app(settings, token)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    port = sock.getsockname()[1]

    print(json.dumps({"event": "ready", "port": port, "token": token}), flush=True)
    if parent_pid:
        _watch_parent(parent_pid)
    uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False)).run(sockets=[sock])
