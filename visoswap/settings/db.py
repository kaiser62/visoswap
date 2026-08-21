"""The settings DDL: one SQL string, applied to a ``sqlite3.Connection``.

All five tables are declared here, in one place, even though plan 03-01 only
exercises three of them. Plans 03-02 and 03-03 add code in their own files rather
than editing this one, and Phase 4's backend executes this same string rather
than hand-copying it -- a second copy of the DDL is how a project's settings
tables start differing from the ones the tests proved.

Two facts about this DDL that a reader who does not know them will "fix" wrongly:

1. **Three tables carry a foreign key onto ``projects(id)``, and this module does
   not create a ``projects`` table.** SQLite resolves a foreign key at DML time,
   not at CREATE time, so the DDL applies cleanly to a database that has no
   ``projects`` table yet. That is deliberate: ``projects`` belongs to the
   backend, and declaring a stub of it here would put two definitions of the
   backend's own table into the tree.

2. **``apply_settings_schema`` does not turn ``PRAGMA foreign_keys`` on.**
   Whether referential integrity is enforced is the connection owner's decision,
   and in Phase 4 that owner is the backend. Turning it on here would make
   Phase 3's own tests fail against the missing ``projects`` table described
   above.

Every value column holds a **JSON-encoded scalar**, not raw text. That is what
makes an int come back an int without a second coercion step at every read, and
it is precisely the difference between this design and the string round-tripping
it replaces.
"""

import sqlite3

#: Executed with ``executescript``. Every statement is ``IF NOT EXISTS`` so
#: applying it to an already-initialised database is a no-op.
SETTINGS_SCHEMA = """
-- Global tier: one row per overridden key. The design's "app config" tier.
-- Absence of a row means "inherit the schema default"; there is no sentinel
-- value that means the same thing.
CREATE TABLE IF NOT EXISTS global_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT
);

-- Project tier: one row per (project, overridden key).
CREATE TABLE IF NOT EXISTS project_settings (
    project_id  TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT,
    PRIMARY KEY (project_id, key),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Face identity: the recognition embedding behind a face key.
--
-- The face key is a content-addressed digest of the embedding, and a digest is
-- one-way, so plan 03-02 cannot do similarity-threshold matching against it. The
-- embedding itself is stored beside it for exactly that reason, along with the
-- name of the recognition model that produced it -- two embeddings from
-- different models are not comparable and must never be matched against each
-- other.
CREATE TABLE IF NOT EXISTS project_faces (
    project_id          TEXT NOT NULL,
    face_key            TEXT NOT NULL,
    recognition_model   TEXT NOT NULL,
    embedding           BLOB NOT NULL,
    created_at          TEXT,
    PRIMARY KEY (project_id, face_key),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Face tier: one row per (project, face, overridden key). The most specific
-- tier, and the first one resolution consults.
CREATE TABLE IF NOT EXISTS face_settings (
    project_id  TEXT NOT NULL,
    face_key    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT,
    PRIMARY KEY (project_id, face_key, key),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Presets: a named pair of tier payloads. Plan 03-03 seeds this from the two
-- migrated profiles; declaring it here is what keeps the DDL in one file.
-- Both payloads are JSON objects of {key: value}, not JSON-encoded scalars.
CREATE TABLE IF NOT EXISTS setting_presets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    project_values  TEXT NOT NULL,
    global_values   TEXT NOT NULL,
    created_at      TEXT,
    updated_at      TEXT
);
"""

#: The tables this module owns, for tests and for Phase 4's migration check.
SETTINGS_TABLES = (
    "global_settings",
    "project_settings",
    "project_faces",
    "face_settings",
    "setting_presets",
)


def apply_settings_schema(connection: sqlite3.Connection) -> None:
    """Create every settings table on ``connection`` if it is not already there.

    Idempotent. The caller owns the connection and its transaction, because in
    Phase 4 this runs inside the backend's own connect sequence alongside its
    other DDL. Note that ``executescript`` commits any transaction already open
    before it runs the script -- that is sqlite3's behaviour, not a choice made
    here, and it is why this is called at connect time rather than mid-write.
    """
    connection.executescript(SETTINGS_SCHEMA)
