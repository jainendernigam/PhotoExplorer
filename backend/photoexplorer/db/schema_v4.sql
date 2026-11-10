-- Version 4: memories export (slideshows, collages) and Google Photos import.

CREATE TABLE exports (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL CHECK (kind IN ('slideshow', 'collage')),
    title       TEXT,
    status      TEXT    NOT NULL DEFAULT 'queued',  -- queued | running | done | error | cancelled
    progress    REAL    NOT NULL DEFAULT 0,         -- 0..1
    message     TEXT,
    options     TEXT    NOT NULL,                   -- JSON
    media_ids   TEXT    NOT NULL,                   -- JSON list, in order
    output_path TEXT,
    created_at  INTEGER NOT NULL,
    finished_at INTEGER
);

-- Items imported from Google Photos (via Google's Photos Picker).
CREATE TABLE gphotos_items (
    remote_id     TEXT    PRIMARY KEY,
    path          TEXT    NOT NULL,
    create_time   TEXT,
    mime_type     TEXT,
    downloaded_at INTEGER NOT NULL
);
