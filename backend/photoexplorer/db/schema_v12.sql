-- Version 12: face quality. For ArcFace faces, the length of the raw embedding:
-- real faces give about 20-25, things mistaken for faces (hands, fabric,
-- flowers, side-on faces) about 10-17. NULL for SFace faces and for faces found
-- before v12; grouping then falls back to the detector score.
ALTER TABLE faces ADD COLUMN quality REAL;
