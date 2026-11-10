"""Command line.

    python -m photoexplorer serve [--port 8765 --token dev]
    python -m photoexplorer scan "D:\\Photos" "E:\\Phone backup"
    python -m photoexplorer stats
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import sys
import time


def _settings(args):
    from .config import Settings
    over = {"workers": args.workers} if getattr(args, "workers", None) else {}
    return Settings.load(args.data_dir, **over)


def cmd_serve(args) -> None:
    from .server import serve
    serve(_settings(args), port=args.port, token=args.token, parent_pid=args.parent_pid)


def cmd_scan(args) -> None:
    from .db import Database
    from .scanner.manager import ScanManager

    s = _settings(args)
    db = Database(s.db_path)
    ids = []
    for p in args.paths:
        p = os.path.abspath(p)
        row = db.conn.execute("SELECT id FROM sources WHERE path = ?", (p,)).fetchone()
        if row is None:
            row = [db.conn.execute("INSERT INTO sources(path, added_at) VALUES (?, ?)",
                                   (p, int(time.time()))).lastrowid]
            print(f"Added source {p}")
        ids.append(row[0])

    print(f"Data: {s.data_dir}   workers: {s.workers}   ffmpeg: {'yes' if s.ffmpeg else 'no'}")
    m = ScanManager(db, s)
    m.start(ids or None, retry_errors=args.retry_errors)
    try:
        while not m.wait(1.0):
            st = m.status()
            if st["state"] == "walking":
                line = f"walking  folders={st['folders']:,} files={st['discovered']:,} new={st['new']:,}"
            else:
                eta = f" eta={st['eta_s']}s" if st["eta_s"] is not None else ""
                line = (f"indexing {st['indexed']:,}/{st['to_index']:,} "
                        f"errors={st['errors']} {st['rate']}/s{eta}")
            print("\r" + line.ljust(90), end="", flush=True)
    except KeyboardInterrupt:
        print("\nCancelling (progress is kept)...")
        m.cancel()
        m.wait()
    st = m.status()
    print("\n" + json.dumps({k: st[k] for k in (
        "state", "folders", "discovered", "new", "changed", "missing", "indexed", "errors")}))
    for w in st["warnings"]:
        print("warning:", w)


def cmd_models(args) -> None:
    from .faces import models
    if args.download:
        models.download()
    missing = models.missing()
    print(f"Models folder: {models.models_dir()}")
    print("Face recognition ready." if not missing else "Missing: " + ", ".join(missing))


def cmd_faces(args) -> None:
    from .db import Database
    from .scanner.manager import ScanManager

    s = _settings(args)
    db = Database(s.db_path)
    m = ScanManager(db, s)
    m.start(mode="cluster" if args.cluster_only else "faces")
    try:
        while not m.wait(1.0):
            st = m.status()
            print(f"\r{st['state']:10} photos {st['faces_done']:,}/{st['faces_todo']:,} "
                  f"faces {st['faces_found']:,} copied {st['faces_copied']:,}".ljust(90), end="", flush=True)
    except KeyboardInterrupt:
        m.cancel()
        m.wait()
    st = m.status()
    print("\n" + json.dumps({k: st[k] for k in ("state", "faces_todo", "faces_done", "faces_found",
                                                "faces_copied", "faces_errors", "clusters", "faces_auto_named")}))
    for w in st["warnings"]:
        print("warning:", w)


def cmd_stats(args) -> None:
    from .db import Database
    s = _settings(args)
    c = Database(s.db_path).conn
    for r in c.execute("SELECT kind, status, count(*), sum(size) FROM media GROUP BY 1, 2"):
        print(f"{r[0]:6} status={r[1]}  files={r[2]:,}  bytes={r[3]:,}")
    for r in c.execute("SELECT error, count(*) FROM media WHERE status = 2 GROUP BY 1 ORDER BY 2 DESC LIMIT 10"):
        print(f"error x{r[1]}: {r[0]}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="photoexplorer")
    ap.add_argument("--data-dir", help="where library.db and thumbs/ live (default: <app>/data)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the local API for the desktop UI")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--token")
    p.add_argument("--parent-pid", type=int, help="exit when this process ends")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("scan", help="add folders and scan them from the terminal")
    p.add_argument("paths", nargs="*")
    p.add_argument("--workers", type=int)
    p.add_argument("--retry-errors", action="store_true")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("models", help="check or download the face models")
    p.add_argument("--download", action="store_true")
    p.set_defaults(fn=cmd_models)

    p = sub.add_parser("faces", help="find faces in indexed photos and group them")
    p.add_argument("--cluster-only", action="store_true", help="only regroup existing faces")
    p.add_argument("--workers", type=int)
    p.set_defaults(fn=cmd_faces)

    p = sub.add_parser("stats", help="library summary")
    p.set_defaults(fn=cmd_stats)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    args.fn(args)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
