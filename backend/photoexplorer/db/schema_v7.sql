-- Version 7: Undo of an Organize also removes what the job itself created.
-- created_root: the destination folder did not exist before the job.
-- created_source: the library source the job added for moved files (Move only).
ALTER TABLE organize_jobs ADD COLUMN created_root INTEGER NOT NULL DEFAULT 0;
ALTER TABLE organize_jobs ADD COLUMN created_source INTEGER;
