"""API for favourites, archive, notes, search by content, categories ("Things"),
pets, places, the memories strip and the optional better face models."""
from __future__ import annotations

import threading
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from . import memories, places
from .db import transaction
from .faces import arcface
from .smart import clip as clipmod
from .smart.phase import group_pets

TAGS = {"screenshot": "Screenshots", "selfie": "Selfies", "document": "Documents", "receipt": "Receipts",
        "dog": "Dogs", "cat": "Cats"}


class MarkIn(BaseModel):
    media_ids: list[int]
    favorite: bool | None = None
    archived: bool | None = None


class NoteIn(BaseModel):
    note: str | None = None


class PetNameIn(BaseModel):
    cluster: int | None = None
    pet_id: int | None = None
    name: str


class KindIn(BaseModel):
    kind: str


class Downloads:
    """Background downloads with progress the UI can poll."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, key: str, fn, after=None) -> dict:
        with self._lock:
            j = self.jobs.get(key)
            if j and j["state"] == "running":
                return j
            j = self.jobs[key] = {"state": "running", "done": 0, "total": 0, "error": None, "started": time.time()}

        def prog(done, total):
            j["done"], j["total"] = done, total

        def run():
            try:
                fn(prog)
                j["state"] = "done"
                if after:
                    after()
            except Exception as e:
                j["state"], j["error"] = "error", f"{type(e).__name__}: {e}"
        threading.Thread(target=run, daemon=True, name=f"download-{key}").start()
        return j

    def get(self, key: str) -> dict | None:
        return self.jobs.get(key)


def register(app: FastAPI, db, settings, scanner) -> None:
    downloads = Downloads()

    # ---------------------------------------------------------------- favourites, archive, notes

    @app.post("/api/media/mark")
    def mark(body: MarkIn):
        if not body.media_ids:
            return {"changed": 0}
        sets, args = [], []
        if body.favorite is not None:
            sets.append("favorite = ?"); args.append(int(body.favorite))
        if body.archived is not None:
            sets.append("archived = ?"); args.append(int(body.archived))
        if not sets:
            raise HTTPException(400, "Nothing to change")
        n = 0
        with transaction(db.conn):
            for i in range(0, len(body.media_ids), 900):
                chunk = body.media_ids[i:i + 900]
                n += db.conn.execute(f"UPDATE media SET {', '.join(sets)} WHERE id IN ({','.join('?' * len(chunk))})",
                                     (*args, *chunk)).rowcount
        return {"changed": n}

    @app.post("/api/media/{media_id}/note")
    def set_note(media_id: int, body: NoteIn):
        note = (body.note or "").strip()[:2000] or None
        if not db.conn.execute("UPDATE media SET note = ? WHERE id = ?", (note, media_id)).rowcount:
            raise HTTPException(404)
        return {"ok": True}

    @app.get("/api/media/{media_id}/place")
    def media_place(media_id: int):
        r = db.conn.execute("SELECT gps_lat, gps_lon FROM media WHERE id = ?", (media_id,)).fetchone()
        if not r or r[0] is None or (r[0] == 0 and r[1] == 0):
            return None
        return places.nearest(r[0], r[1])

    # ---------------------------------------------------------------- search by content (CLIP)

    def smart_status():
        missing = clipmod.missing(settings.data_dir)
        c = db.conn
        todo = c.execute("SELECT count(*) FROM media WHERE status = 1 AND clip_status = 0 AND has_thumb = 1").fetchone()[0]
        done = c.execute("SELECT count(*) FROM media WHERE status = 1 AND clip_status = 1").fetchone()[0]
        return {"available": not missing, "missing": missing, "todo": todo, "done": done,
                "download": downloads.get("clip"), "size_mb": round(sum(s for _, s in clipmod.FILES.values()) / 1e6)}

    @app.get("/api/smart/status")
    def smart_status_ep():
        return smart_status()

    @app.post("/api/smart/download")
    def smart_download():
        if "python package onnxruntime" in clipmod.missing(settings.data_dir):
            raise HTTPException(400, "This copy of the app lacks onnxruntime; update the app first.")

        def after():
            if not scanner.running:
                scanner.start(mode="smart")
        return downloads.start("clip", lambda prog: clipmod.download(settings.data_dir, prog), after)

    @app.post("/api/smart/run")
    def smart_run():
        if clipmod.missing(settings.data_dir):
            raise HTTPException(400, "Search by content is not set up yet.")
        if not scanner.start(mode="smart"):
            raise HTTPException(409, "A scan is already running.")
        return scanner.status()

    # ---------------------------------------------------------------- things and pets

    @app.get("/api/things")
    def things():
        c = db.conn
        out = []
        for tag, label in TAGS.items():
            r = c.execute("SELECT count(*), max(t.media_id) FROM media_tags t JOIN media m ON m.id = t.media_id "
                          "AND m.status = 1 AND m.archived = 0 WHERE t.tag = ?", (tag,)).fetchone()
            if r[0]:
                cover = c.execute("SELECT t.media_id FROM media_tags t JOIN media m ON m.id = t.media_id AND m.status = 1 "
                                  "AND m.archived = 0 WHERE t.tag = ? ORDER BY t.score DESC LIMIT 1", (tag,)).fetchone()[0]
                out.append({"tag": tag, "label": label, "count": r[0], "cover": cover})
        return {"categories": out, "smart": smart_status()}

    @app.get("/api/pets")
    def pets():
        c = db.conn
        named = [dict(r) for r in c.execute(
            "SELECT p.id, p.name, p.species, count(*) photos, max(pm.media_id) cover FROM pets p "
            "JOIN pet_media pm ON pm.pet_id = p.id JOIN media m ON m.id = pm.media_id AND m.status = 1 AND m.archived = 0 "
            "GROUP BY p.id ORDER BY photos DESC")]
        groups = [dict(r) for r in c.execute(
            "SELECT pm.cluster, min(pm.species) species, count(*) photos, max(pm.media_id) cover FROM pet_media pm "
            "JOIN media m ON m.id = pm.media_id AND m.status = 1 AND m.archived = 0 "
            "WHERE pm.pet_id IS NULL AND pm.cluster >= 0 GROUP BY pm.cluster HAVING photos >= 2 ORDER BY photos DESC LIMIT 100")]
        return {"named": named, "groups": groups}

    @app.post("/api/pets/name")
    def name_pet(body: PetNameIn):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Give a name")
        c = db.conn
        with transaction(c):
            r = c.execute("SELECT id FROM pets WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            if body.pet_id is not None and not r:
                c.execute("UPDATE pets SET name = ? WHERE id = ?", (name, body.pet_id))
                return {"pet_id": body.pet_id}
            if r:
                pid = r[0]
            else:
                sp = c.execute("SELECT min(species) FROM pet_media WHERE cluster = ?", (body.cluster,)).fetchone()[0]
                pid = c.execute("INSERT INTO pets(name, species, created_at) VALUES (?, ?, ?)",
                                (name, sp, int(time.time()))).lastrowid
            if body.cluster is not None:
                c.execute("UPDATE pet_media SET pet_id = ? WHERE cluster = ? AND pet_id IS NULL", (pid, body.cluster))
            if body.pet_id is not None and body.pet_id != pid:   # renamed to an existing name: merge
                c.execute("UPDATE pet_media SET pet_id = ? WHERE pet_id = ?", (pid, body.pet_id))
                c.execute("DELETE FROM pets WHERE id = ?", (body.pet_id,))
        return {"pet_id": pid}

    @app.post("/api/pets/regroup")
    def regroup_pets():
        return {"grouped": group_pets(db.conn)}

    # ---------------------------------------------------------------- places

    @app.get("/api/map/world")
    def world():
        return Response(places.world_text(), media_type="application/json",
                        headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/places")
    def place_list(limit: int = Query(300, ge=1, le=2000)):
        return places.place_list(db.conn, limit)

    @app.get("/api/places/points")
    def place_points():
        return places.photo_points(db.conn)

    # ---------------------------------------------------------------- memories strip

    _strip: dict = {"day": None, "stamp": None, "cards": []}

    @app.get("/api/memories/strip")
    def memory_strip():
        from datetime import date
        stamp = tuple(db.conn.execute("SELECT count(*), coalesce(max(id), 0), coalesce(sum(favorite), 0), "
                                      "coalesce(sum(archived), 0) FROM media WHERE status = 1").fetchone())
        today = date.today().isoformat()
        if _strip["day"] != today or _strip["stamp"] != stamp:
            _strip.update(day=today, stamp=stamp, cards=memories.strip(db.conn))
        # Songs can be added to "My music" at any time: pick them fresh (cheap).
        from .content import Content
        return memories.add_music(_strip["cards"], Content(settings.data_dir).music())

    # ---------------------------------------------------------------- better face models (ArcFace)

    @app.get("/api/faces/models")
    def face_models():
        return {"current": arcface.current(db.conn, settings.data_dir), "installed": installed_kinds(),
                "packs": {k: {"label": v["label"], "size_mb": round(v["size"] / 1e6)} for k, v in arcface.PACKS.items()},
                "downloads": {k: downloads.get(f"face-{k}") for k in arcface.PACKS}}

    def installed_kinds():
        return ["sface"] + arcface.installed(settings.data_dir)

    @app.post("/api/faces/models/download")
    def face_model_download(body: KindIn):
        if body.kind not in arcface.PACKS:
            raise HTTPException(400, "Unknown model")
        if not arcface.runtime_ok():
            raise HTTPException(400, "This copy of the app lacks onnxruntime; update the app first.")
        return downloads.start(f"face-{body.kind}", lambda prog: arcface.download(settings.data_dir, body.kind, prog))

    @app.post("/api/faces/models/use")
    def face_model_use(body: KindIn):
        if body.kind not in installed_kinds():
            raise HTTPException(400, "Download that model first.")
        if scanner.running:
            raise HTTPException(409, "Stop the scan first.")
        arcface.set_current(db.conn, body.kind)
        return {"current": body.kind}
