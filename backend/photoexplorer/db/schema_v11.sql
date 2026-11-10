-- Version 11: face errors are kept with their reason and retried.
-- A photo whose face check fails stays waiting (faces_status 0) and is tried
-- again on the next run, up to 3 times; then it is marked as an error (2).
ALTER TABLE media ADD COLUMN faces_error TEXT;
ALTER TABLE media ADD COLUMN faces_tries INTEGER NOT NULL DEFAULT 0;

-- Errors from before v11 have no reason recorded (most came from a missing
-- onnxruntime with the ArcFace model selected): try them again.
UPDATE media SET faces_status = 0 WHERE faces_status = 2;
