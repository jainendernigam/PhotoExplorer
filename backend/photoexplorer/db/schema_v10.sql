-- Version 10: favourites, archive, notes, smart (CLIP) search, categories and pets.

ALTER TABLE media ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0;
ALTER TABLE media ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;   -- hidden from the main views, not deleted
ALTER TABLE media ADD COLUMN note TEXT;                              -- the user's own description
ALTER TABLE media ADD COLUMN clip_status INTEGER NOT NULL DEFAULT 0; -- 0 todo, 1 done, 2 error
CREATE INDEX idx_media_fav ON media(favorite) WHERE favorite = 1;
CREATE INDEX idx_media_clip_todo ON media(id) WHERE status = 1 AND clip_status = 0;

-- Search covers notes too: rebuild the full-text index with a third column.
DROP TRIGGER media_fts_ai;
DROP TRIGGER media_fts_ad;
DROP TRIGGER media_fts_au;
DROP TABLE media_fts;
CREATE VIRTUAL TABLE media_fts USING fts5(
    filename, path, note,
    content = 'media', content_rowid = 'id',
    tokenize = "unicode61 remove_diacritics 2"
);
INSERT INTO media_fts(media_fts) VALUES ('rebuild');
CREATE TRIGGER media_fts_ai AFTER INSERT ON media BEGIN
    INSERT INTO media_fts(rowid, filename, path, note) VALUES (new.id, new.filename, new.path, new.note);
END;
CREATE TRIGGER media_fts_ad AFTER DELETE ON media BEGIN
    INSERT INTO media_fts(media_fts, rowid, filename, path, note)
    VALUES ('delete', old.id, old.filename, old.path, old.note);
END;
CREATE TRIGGER media_fts_au AFTER UPDATE OF filename, path, note ON media BEGIN
    INSERT INTO media_fts(media_fts, rowid, filename, path, note)
    VALUES ('delete', old.id, old.filename, old.path, old.note);
    INSERT INTO media_fts(rowid, filename, path, note) VALUES (new.id, new.filename, new.path, new.note);
END;

-- One CLIP picture fingerprint per photo (512 floats), for search by content.
CREATE TABLE media_clip (
    media_id  INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL
);

-- Automatic categories: screenshot, selfie, document, receipt, dog, cat.
CREATE TABLE media_tags (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    score    REAL NOT NULL,
    PRIMARY KEY (media_id, tag)
);
CREATE INDEX idx_media_tags_tag ON media_tags(tag, media_id);

-- Pets: photos of a pet grouped by look; a group can be named.
CREATE TABLE pets (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    species    TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE pet_media (
    media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
    species  TEXT NOT NULL,             -- dog | cat
    cluster  INTEGER NOT NULL DEFAULT -1,
    pet_id   INTEGER REFERENCES pets(id) ON DELETE SET NULL
);
CREATE INDEX idx_pet_media_cluster ON pet_media(cluster);
CREATE INDEX idx_pet_media_pet ON pet_media(pet_id);

-- Photos found for a typed search by their content (filled when the search runs).
CREATE TABLE search_hits (
    q        TEXT NOT NULL,
    media_id INTEGER NOT NULL,
    score    REAL NOT NULL,
    PRIMARY KEY (q, media_id)
) WITHOUT ROWID;
