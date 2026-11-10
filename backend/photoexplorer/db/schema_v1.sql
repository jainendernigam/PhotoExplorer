-- Photo Explorer library schema, version 1.
-- One row in `media` per file on disk. Exact duplicates share `sha256`;
-- near-duplicates are found through the pHash bands (ph0..ph3).

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Root folders the user added.
CREATE TABLE sources (
    id           INTEGER PRIMARY KEY,
    path         TEXT    NOT NULL UNIQUE,
    label        TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    scan_gen     INTEGER NOT NULL DEFAULT 0,  -- bumped every walk; stale folders = missing
    added_at     INTEGER NOT NULL,
    last_scan_at INTEGER
);

-- Every directory that holds (or held) media. Only touched once per walk,
-- so a rescan of a million unchanged files costs ~#folders writes, not ~#files.
CREATE TABLE folders (
    id        INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path      TEXT    NOT NULL UNIQUE,
    scan_gen  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_folders_source ON folders(source_id, scan_gen);

-- status: 0 = pending index, 1 = indexed, 2 = error, 3 = missing on disk
CREATE TABLE media (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    folder_id     INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    path          TEXT    NOT NULL UNIQUE,
    filename      TEXT    NOT NULL,
    ext           TEXT    NOT NULL,
    kind          TEXT    NOT NULL CHECK (kind IN ('image', 'raw', 'video')),
    size          INTEGER NOT NULL,
    mtime_ns      INTEGER NOT NULL,
    status        INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    added_at      INTEGER NOT NULL,
    indexed_at    INTEGER,

    -- hashes
    sha256        BLOB,                -- 32 bytes
    hash_sampled  INTEGER NOT NULL DEFAULT 0,  -- 1 = large video, sampled hash
    phash         INTEGER,             -- 64-bit DCT hash (signed storage)
    dhash         INTEGER,             -- 64-bit gradient hash
    ph0 INTEGER, ph1 INTEGER, ph2 INTEGER, ph3 INTEGER,  -- 16-bit bands of phash

    -- image / video properties (width/height are display-oriented)
    width         INTEGER,
    height        INTEGER,
    duration_ms   INTEGER,
    video_codec   TEXT,
    has_thumb     INTEGER NOT NULL DEFAULT 0,

    -- when: wall-clock seconds (local time, stored as if UTC) for stable sorting
    taken_at      INTEGER,
    tz_offset     TEXT,                -- e.g. '+05:30' when the camera recorded it
    date_source   TEXT,                -- exif | video | filename | mtime

    -- camera / place
    camera_make   TEXT,
    camera_model  TEXT,
    lens          TEXT,
    gps_lat       REAL,
    gps_lon       REAL,
    gps_alt       REAL,

    -- later phases
    faces_status  INTEGER NOT NULL DEFAULT 0,  -- 0 todo, 1 done, 2 error
    origin        TEXT    NOT NULL DEFAULT 'local',  -- local | gphotos
    remote_id     TEXT
);
CREATE INDEX idx_media_folder   ON media(folder_id);
CREATE INDEX idx_media_status   ON media(status, id);
CREATE INDEX idx_media_sha      ON media(sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX idx_media_timeline ON media(taken_at DESC, id DESC) WHERE status = 1;
CREATE INDEX idx_media_ph0 ON media(ph0) WHERE status = 1;
CREATE INDEX idx_media_ph1 ON media(ph1) WHERE status = 1;
CREATE INDEX idx_media_ph2 ON media(ph2) WHERE status = 1;
CREATE INDEX idx_media_ph3 ON media(ph3) WHERE status = 1;
CREATE INDEX idx_media_faces ON media(faces_status, id) WHERE status = 1;
CREATE INDEX idx_media_remote ON media(remote_id) WHERE remote_id IS NOT NULL;

-- Full-text search over file names and folder paths ("goa", "wedding", "IMG_2019").
-- External-content table: the text is not stored twice.
CREATE VIRTUAL TABLE media_fts USING fts5(
    filename, path,
    content = 'media', content_rowid = 'id',
    tokenize = "unicode61 remove_diacritics 2"
);
CREATE TRIGGER media_fts_ai AFTER INSERT ON media BEGIN
    INSERT INTO media_fts(rowid, filename, path) VALUES (new.id, new.filename, new.path);
END;
CREATE TRIGGER media_fts_ad AFTER DELETE ON media BEGIN
    INSERT INTO media_fts(media_fts, rowid, filename, path)
    VALUES ('delete', old.id, old.filename, old.path);
END;
CREATE TRIGGER media_fts_au AFTER UPDATE OF filename, path ON media BEGIN
    INSERT INTO media_fts(media_fts, rowid, filename, path)
    VALUES ('delete', old.id, old.filename, old.path);
    INSERT INTO media_fts(rowid, filename, path) VALUES (new.id, new.filename, new.path);
END;

-- ---------- Phase 2: people ----------
CREATE TABLE persons (
    id            INTEGER PRIMARY KEY,
    name          TEXT,
    birth_date    TEXT,                -- 'YYYY-MM-DD', enables age-based timelines
    cover_face_id INTEGER,
    hidden        INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);

CREATE TABLE faces (
    id          INTEGER PRIMARY KEY,
    media_id    INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,  -- normalised 0..1
    det_score   REAL,
    embedding   BLOB,                  -- float32[512], L2-normalised
    cluster_id  INTEGER,               -- unsupervised cluster
    person_id   INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    is_manual   INTEGER NOT NULL DEFAULT 0  -- user confirmed / moved
);
CREATE INDEX idx_faces_media   ON faces(media_id);
CREATE INDEX idx_faces_person  ON faces(person_id, media_id);
CREATE INDEX idx_faces_cluster ON faces(cluster_id);

-- ---------- Phase 3/6: albums ----------
CREATE TABLE albums (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE album_items (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    position INTEGER,
    PRIMARY KEY (album_id, media_id)
) WITHOUT ROWID;

-- ---------- bookkeeping ----------
CREATE TABLE scan_runs (
    id          INTEGER PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    state       TEXT    NOT NULL,      -- running | done | cancelled | error
    stats       TEXT                   -- JSON counters
);
