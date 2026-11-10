-- Version 2: faces and people.
-- The v1 placeholder tables `faces` and `persons` were never written to, so
-- they are rebuilt with the final design.

DROP TABLE faces;
DROP TABLE persons;

CREATE TABLE persons (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL,
    birth_date    TEXT,                -- 'YYYY-MM-DD': enables "age 0 to 8" timelines
    cover_face_id INTEGER,             -- user-chosen face for the People page
    hidden        INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX idx_persons_name ON persons(name COLLATE NOCASE);

CREATE TABLE faces (
    id          INTEGER PRIMARY KEY,
    media_id    INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,      -- face number within the photo
    x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,  -- 0..1 of the oriented photo
    score       REAL    NOT NULL,      -- detector confidence
    sharpness   REAL    NOT NULL,      -- variance of Laplacian on the aligned face; low = blurry
    size_px     INTEGER NOT NULL,      -- face width in the photo's own pixels
    embedding   BLOB    NOT NULL,      -- float32, L2-normalised
    model       TEXT    NOT NULL,      -- embeddings from different models are not comparable
    chip        TEXT,                  -- face thumbnail, relative to thumbs/
    cluster_id  INTEGER,               -- result of the last clustering run (-1 = noise)
    person_id   INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    -- who decided person_id:
    --   'user'    confirmed by hand          (never changed by clustering)
    --   'cluster' user named its cluster      (kept; acts as an anchor like 'user')
    --   'auto'    clustering matched it to a named person (recomputed every run)
    assigned_by TEXT CHECK (assigned_by IN ('user', 'cluster', 'auto')),
    UNIQUE (media_id, ordinal)
);
CREATE INDEX idx_faces_person  ON faces(person_id, media_id);
CREATE INDEX idx_faces_cluster ON faces(cluster_id);

-- "This face is NOT that person": clustering must never put it back.
CREATE TABLE face_rejections (
    face_id   INTEGER NOT NULL REFERENCES faces(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    PRIMARY KEY (face_id, person_id)
) WITHOUT ROWID;

-- media.faces_status: 0 todo, 1 done, 2 error. Find work quickly:
CREATE INDEX idx_media_faces_todo ON media(id) WHERE status = 1 AND faces_status = 0;
CREATE INDEX idx_media_sha_faces ON media(sha256, faces_status) WHERE sha256 IS NOT NULL;
