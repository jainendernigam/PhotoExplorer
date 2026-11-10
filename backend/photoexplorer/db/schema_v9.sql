-- Version 9: a folder can be unlinked from the library without removing its source.
-- The scan skips an excluded folder and everything below it.
ALTER TABLE folders ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0;
