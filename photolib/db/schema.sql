CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS archives (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id      TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    size          INTEGER NOT NULL,
    modified_time TEXT,
    indexed_at    TEXT
);

CREATE TABLE IF NOT EXISTS entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id          INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
    path                TEXT NOT NULL,
    name                TEXT NOT NULL,
    crc32               INTEGER NOT NULL,
    size                INTEGER NOT NULL,
    compressed_size     INTEGER NOT NULL,
    method              INTEGER NOT NULL,
    local_header_offset INTEGER NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('media', 'sidecar')),
    UNIQUE (archive_id, path)
);

CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name);
CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    action       TEXT NOT NULL,
    params       TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    progress     REAL NOT NULL DEFAULT 0.0,
    message      TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS job_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id);

CREATE TABLE IF NOT EXISTS sidecars (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id         INTEGER NOT NULL UNIQUE REFERENCES entries(id) ON DELETE CASCADE,
    title            TEXT,
    photo_taken_time INTEGER,
    creation_time    INTEGER,
    latitude         REAL,
    longitude        REAL,
    altitude         REAL,
    url              TEXT,
    device           TEXT,
    raw_json         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id         INTEGER NOT NULL UNIQUE REFERENCES entries(id) ON DELETE CASCADE,
    sidecar_id       INTEGER REFERENCES sidecars(id) ON DELETE SET NULL,
    capture_time     INTEGER,
    capture_source   TEXT,
    latitude         REAL,
    longitude        REAL,
    country          TEXT,
    target_folder    TEXT,
    target_name      TEXT,
    duplicate_of     TEXT,
    duplicate_reason TEXT,
    upload_status    TEXT NOT NULL DEFAULT 'pending'
                     CHECK (upload_status IN ('pending', 'done', 'error')),
    drive_file_id    TEXT,
    md5              TEXT,
    error            TEXT,
    upload_session_uri TEXT,
    upload_offset      INTEGER NOT NULL DEFAULT 0,
    session_started_at TEXT,
    attempts           INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_media_target ON media(target_folder, target_name);
CREATE INDEX IF NOT EXISTS idx_media_status ON media(upload_status);

CREATE TABLE IF NOT EXISTS drive_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    parent_path TEXT NOT NULL,
    md5         TEXT,
    size        INTEGER,
    indexed_at  TEXT,
    trashed_at  TEXT,
    mime_type   TEXT,
    synced_tags TEXT,
    capture_hint INTEGER
);

CREATE INDEX IF NOT EXISTS idx_drive_files_name ON drive_files(name);

CREATE TABLE IF NOT EXISTS geocache (
    key      TEXT PRIMARY KEY,
    place    TEXT,
    country  TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    slug  TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#6b7280'
);

-- No foreign key to drive_files on purpose: Scan rebuilds that table, and a
-- cascade would delete every tag with it. Orphans are retained and excluded
-- from counts instead.
CREATE TABLE IF NOT EXISTS file_tags (
    drive_id TEXT NOT NULL,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (drive_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_file_tags_tag ON file_tags(tag_id);

CREATE TABLE IF NOT EXISTS job_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    phase      TEXT NOT NULL,
    item_key   TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    state      TEXT NOT NULL CHECK (state IN ('pending','done','failed','skipped')),
    detail     TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, phase, item_key)
);

CREATE INDEX IF NOT EXISTS idx_job_items_run ON job_items(run_id, phase, state);
