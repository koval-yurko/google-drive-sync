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
