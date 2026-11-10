-- Version 5: organise into folders (copy / link / move) and albums.
-- (albums and album_items already exist since v1; they get used now.)

ALTER TABLE albums ADD COLUMN cover_media_id INTEGER;
CREATE UNIQUE INDEX idx_albums_name ON albums(name COLLATE NOCASE);
CREATE INDEX idx_album_items_media ON album_items(media_id);

CREATE TABLE organize_jobs (
    id              INTEGER PRIMARY KEY,
    status          TEXT    NOT NULL DEFAULT 'queued',  -- queued | running | done | error | cancelled
    mode            TEXT    NOT NULL CHECK (mode IN ('copy', 'hardlink', 'symlink', 'move')),
    dest_root       TEXT    NOT NULL,
    options         TEXT    NOT NULL,   -- JSON: templates, people handling...
    media_ids       TEXT    NOT NULL,   -- JSON list
    planned         INTEGER NOT NULL DEFAULT 0,
    done            INTEGER NOT NULL DEFAULT 0,
    skipped         INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    bytes           INTEGER NOT NULL DEFAULT 0,
    progress        REAL    NOT NULL DEFAULT 0,
    message         TEXT,
    manifest_path   TEXT,
    created_at      INTEGER NOT NULL,
    finished_at     INTEGER,
    undone_at       INTEGER
);

-- What each job did to each file (for the report and for undo).
CREATE TABLE organize_items (
    job_id   INTEGER NOT NULL REFERENCES organize_jobs(id) ON DELETE CASCADE,
    media_id INTEGER,
    src      TEXT    NOT NULL,
    dest     TEXT    NOT NULL,
    action   TEXT    NOT NULL,          -- copied | linked | moved | skipped | failed | undone
    error    TEXT
);
CREATE INDEX idx_organize_items_job ON organize_items(job_id);
