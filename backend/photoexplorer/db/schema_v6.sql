-- Version 6: photo edits and auto-organize rules.

-- One row per saved edit. A copy gets its own media row (edited_id) linked to
-- the photo it came from; a replace keeps the same media row and a backup of
-- the file as it was. original_id is a plain number on purpose: removing the
-- original never removes its edited copies.
CREATE TABLE media_edits (
    id           INTEGER PRIMARY KEY,
    edited_id    INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    original_id  INTEGER NOT NULL,
    mode         TEXT    NOT NULL CHECK (mode IN ('copy', 'replace')),
    ops          TEXT    NOT NULL,      -- JSON: what was done
    backup_path  TEXT,                  -- replace: the file before the edit
    created_at   INTEGER NOT NULL,
    restored_at  INTEGER                -- replace undone ("Restore original")
);
CREATE INDEX idx_media_edits_edited ON media_edits(edited_id);
CREATE INDEX idx_media_edits_original ON media_edits(original_id);

-- Saved Organize settings that "Run auto organize" applies to photos of the
-- chosen people that are not in the destination yet.
CREATE TABLE organize_rules (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    dest_root    TEXT    NOT NULL,
    mode         TEXT    NOT NULL CHECK (mode IN ('copy', 'hardlink', 'symlink', 'move')),
    options      TEXT    NOT NULL,      -- JSON: folder / name templates, person folders
    people       TEXT    NOT NULL,      -- JSON list of person ids
    people_mode  TEXT    NOT NULL DEFAULT 'any' CHECK (people_mode IN ('any', 'all')),
    created_at   INTEGER NOT NULL,
    last_run_at  INTEGER,
    last_job_id  INTEGER,
    last_result  TEXT
);
