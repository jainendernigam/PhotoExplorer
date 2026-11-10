"""Background queue for slideshows and collages (one job at a time)."""
from __future__ import annotations

import dataclasses
import json
import logging
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..db import Database
from . import collage, selection, slideshow

log = logging.getLogger(__name__)

KINDS = {
    "slideshow": (slideshow.SlideshowOptions, ".mp4"),
    "collage": (collage.CollageOptions, ".jpg"),
}


def parse_options(kind: str, raw: dict) -> object:
    cls, _ = KINDS[kind]
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(raw) - names
    if unknown:
        raise ValueError(f"Unknown options: {', '.join(sorted(unknown))}")
    return cls(**raw)


def _safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .")[:80] or "memories"


class ExportManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.s = settings
        self._q: queue.Queue[int] = queue.Queue()
        self._cancel = threading.Event()
        self._current: int | None = None
        c = db.connect()
        try:  # jobs that were running when the app closed
            c.execute("UPDATE exports SET status = 'error', message = 'Interrupted (app closed)' "
                      "WHERE status IN ('queued', 'running')")
        finally:
            c.close()
        threading.Thread(target=self._loop, name="exports", daemon=True).start()

    # ------------------------------------------------------------ public

    def submit(self, kind: str, media_ids: list[int], options: dict, title: str | None,
               output_dir: str | None) -> int:
        if kind not in KINDS:
            raise ValueError("kind must be 'slideshow' or 'collage'")
        opts = parse_options(kind, options)
        if output_dir:
            p = Path(output_dir)
            if not p.is_absolute() or not p.is_dir():
                raise ValueError(f"Output folder does not exist: {output_dir}")
        data = dataclasses.asdict(opts) | {"output_dir": output_dir}
        jid = self.db.conn.execute(
            "INSERT INTO exports(kind, title, options, media_ids, created_at) VALUES (?, ?, ?, ?, ?)",
            (kind, title, json.dumps(data), json.dumps(media_ids), int(time.time()))).lastrowid
        self._q.put(jid)
        return jid

    def cancel(self, jid: int) -> None:
        if self._current == jid:
            self._cancel.set()
        else:
            self.db.conn.execute("UPDATE exports SET status = 'cancelled', finished_at = ? "
                                 "WHERE id = ? AND status = 'queued'", (int(time.time()), jid))

    # ------------------------------------------------------------ worker

    def _loop(self) -> None:
        while True:
            jid = self._q.get()
            try:
                self._run(jid)
            except Exception:
                log.exception("export %s crashed", jid)

    def _run(self, jid: int) -> None:
        conn = self.db.connect()
        try:
            row = conn.execute("SELECT * FROM exports WHERE id = ?", (jid,)).fetchone()
            if not row or row["status"] != "queued":
                return
            self._current = jid
            self._cancel.clear()
            conn.execute("UPDATE exports SET status = 'running', progress = 0 WHERE id = ?", (jid,))
            data = json.loads(row["options"])
            out_dir = Path(data.pop("output_dir", None) or self.s.exports_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            opts = parse_options(row["kind"], data)
            stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
            out = out_dir / f"{_safe_name(row['title'] or row['kind'].title())} {stamp}{KINDS[row['kind']][1]}"

            chosen = json.loads(row["media_ids"])
            items = selection.prepare(conn, chosen, opts.max_photos, opts.skip_duplicates)
            used = f"Used {len(items)} of {len(chosen)} photos"
            if len(items) < len(chosen):
                why = []
                if opts.skip_duplicates:
                    why.append("duplicates and look-alikes left out")
                if len(chosen) > opts.max_photos:
                    why.append(f"at most {opts.max_photos}")
                used += f" ({', '.join(why)})" if why else ""
            last = [0.0]

            def progress(frac: float, msg: str) -> None:
                now = time.monotonic()
                if now - last[0] > 0.4 or frac >= 1:
                    last[0] = now
                    conn.execute("UPDATE exports SET progress = ?, message = ? WHERE id = ?",
                                 (round(frac, 4), msg, jid))

            if row["kind"] == "slideshow":
                slideshow.render(conn, self.s.ffmpeg, items, opts, out, progress, self._cancel)
            else:
                collage.render(conn, items, opts, out, progress, self._cancel)
            conn.execute("UPDATE exports SET status = 'done', progress = 1, output_path = ?, "
                         "message = ?, finished_at = ? WHERE id = ?",
                         (str(out), used, int(time.time()), jid))
        except (slideshow.Cancelled, collage.Cancelled):
            conn.execute("UPDATE exports SET status = 'cancelled', message = NULL, finished_at = ? WHERE id = ?",
                         (int(time.time()), jid))
        except Exception as e:
            log.warning("export %s failed: %s", jid, e)
            conn.execute("UPDATE exports SET status = 'error', message = ?, finished_at = ? WHERE id = ?",
                         (str(e)[:500], int(time.time()), jid))
        finally:
            self._current = None
            conn.close()
