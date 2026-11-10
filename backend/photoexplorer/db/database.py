"""SQLite access: tuned connections, migrations, transactions.

Connections run in autocommit mode (isolation_level=None); writes are grouped
explicitly with ``transaction()``. WAL lets the API read while the scanner
writes. Each thread gets its own connection via ``Database.conn``.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

# (version, script file). Append new migrations; never edit a shipped one.
MIGRATIONS: list[tuple[int, str]] = [
    (1, "schema_v1.sql"),
    (2, "schema_v2.sql"),
    (3, "schema_v3.sql"),
    (4, "schema_v4.sql"),
    (5, "schema_v5.sql"),
    (6, "schema_v6.sql"),
    (7, "schema_v7.sql"),
    (8, "schema_v8.sql"),
    (9, "schema_v9.sql"),
    (10, "schema_v10.sql"),
    (11, "schema_v11.sql"),
    (12, "schema_v12.sql"),
    (13, "schema_v13.sql"),
]

PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -65536",     # 64 MB page cache per connection
    "PRAGMA mmap_size = 268435456",   # 256 MB memory-mapped reads
    "PRAGMA busy_timeout = 10000",
)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        for p in PRAGMAS:
            conn.execute(p)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """Connection owned by the calling thread."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._local.conn = self.connect()
        return c

    def migrate(self) -> int:
        conn = self.connect()
        try:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for version, script in MIGRATIONS:
                if version <= current:
                    continue
                sql = resources.files(__package__).joinpath(script).read_text(encoding="utf-8")
                conn.executescript(
                    f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('migrated_at', ?)",
                    (str(int(time.time())),),
                )
                current = version
            return current
        finally:
            conn.close()

    def optimize(self) -> None:
        c = self.connect()
        try:
            c.execute("PRAGMA optimize")
            c.execute("INSERT INTO media_fts(media_fts) VALUES ('optimize')")
        finally:
            c.close()


@contextmanager
def transaction(conn: sqlite3.Connection, mode: str = "IMMEDIATE") -> Iterator[sqlite3.Connection]:
    conn.execute(f"BEGIN {mode}")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
