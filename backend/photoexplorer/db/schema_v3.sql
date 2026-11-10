-- Version 3: duplicates, look-alikes and safe removal.

-- media.status gains 4 = removed by the user (archived or sent to the Recycle
-- Bin through the app). Rescans leave these rows alone.
ALTER TABLE media ADD COLUMN sharpness REAL;   -- blur measure from the thumbnail, for "best shot"

-- Result of the last grouping run. Rebuilt on every run (cheap), except what
-- the user decided, which lives in dup_ignored and removals.
CREATE TABLE dup_groups (
    id            INTEGER PRIMARY KEY,
    kind          TEXT    NOT NULL CHECK (kind IN ('exact', 'near', 'burst')),
    size          INTEGER NOT NULL,
    reclaim_bytes INTEGER NOT NULL,     -- bytes freed if the suggestion is applied
    taken_at      INTEGER,
    max_distance  INTEGER               -- largest pHash distance inside (0 for exact)
);
CREATE INDEX idx_dup_groups_kind ON dup_groups(kind, reclaim_bytes DESC);

CREATE TABLE dup_members (
    group_id INTEGER NOT NULL REFERENCES dup_groups(id) ON DELETE CASCADE,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    rank     INTEGER NOT NULL,          -- 0 = best
    keep     INTEGER NOT NULL,          -- suggestion: 1 keep, 0 remove
    PRIMARY KEY (group_id, media_id)
) WITHOUT ROWID;
CREATE INDEX idx_dup_members_media ON dup_members(media_id);

-- "These are not duplicates": pairs never grouped again (a < b).
CREATE TABLE dup_ignored (
    a INTEGER NOT NULL,
    b INTEGER NOT NULL,
    PRIMARY KEY (a, b)
) WITHOUT ROWID;

-- Every file the app moved away, so it can be undone.
CREATE TABLE removals (
    id            INTEGER PRIMARY KEY,
    media_id      INTEGER REFERENCES media(id) ON DELETE SET NULL,
    original_path TEXT    NOT NULL,
    archived_path TEXT,                 -- NULL when sent to the Recycle Bin
    method        TEXT    NOT NULL CHECK (method IN ('archive', 'recycle')),
    size          INTEGER,
    reason        TEXT,                 -- exact | near | burst
    batch         TEXT,                 -- one user action = one batch (undo together)
    removed_at    INTEGER NOT NULL,
    restored_at   INTEGER
);
CREATE INDEX idx_removals_batch ON removals(batch);
