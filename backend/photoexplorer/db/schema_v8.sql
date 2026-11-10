-- Version 8: duplicates can be removed from the app only (method 'app'):
-- the file stays on disk and the photo is hidden (media.status = 5).
CREATE TABLE removals_v8 (
    id            INTEGER PRIMARY KEY,
    media_id      INTEGER REFERENCES media(id) ON DELETE SET NULL,
    original_path TEXT    NOT NULL,
    archived_path TEXT,                 -- NULL for the Recycle Bin and for 'app'
    method        TEXT    NOT NULL CHECK (method IN ('archive', 'recycle', 'app')),
    size          INTEGER,
    reason        TEXT,
    batch         TEXT,
    removed_at    INTEGER NOT NULL,
    restored_at   INTEGER
);
INSERT INTO removals_v8 SELECT id, media_id, original_path, archived_path, method, size, reason, batch,
    removed_at, restored_at FROM removals;
DROP TABLE removals;
ALTER TABLE removals_v8 RENAME TO removals;
CREATE INDEX idx_removals_batch ON removals(batch);
