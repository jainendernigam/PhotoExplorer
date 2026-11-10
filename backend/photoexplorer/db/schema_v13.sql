-- Version 13: doubtful new faces wait for the user ("Is this X?").
-- New faces are added without regrouping (cluster.assign_new): a sure match
-- joins its person automatically; a doubtful one is suggested here until the
-- user confirms (faces/assign) or rejects (faces/reject) it.
CREATE TABLE face_suggestions (
    face_id    INTEGER PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE,
    person_id  INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    score      REAL    NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_face_suggestions_person ON face_suggestions(person_id, score DESC);
