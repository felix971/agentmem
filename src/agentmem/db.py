"""SQLite schema and connection setup."""

import sqlite3
import time

from .config import ensure_home


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
INSERT OR IGNORE INTO meta (k, v) VALUES ('schema_version', '1');
INSERT OR IGNORE INTO meta (k, v) VALUES ('session_projects_v1', '1');
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    inode INTEGER,
    size INTEGER,
    mtime_ns INTEGER,
    offset INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    project_id TEXT,
    cwd TEXT,
    title TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('main', 'subagent', 'imported')),
    path TEXT,
    started_at TEXT,
    last_at TEXT,
    PRIMARY KEY (source, session_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    project_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    ts TEXT NOT NULL,
    text TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_offset INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(tok);
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('git', 'path'))
);
CREATE TABLE IF NOT EXISTS cwd_map (cwd TEXT PRIMARY KEY, project_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS messages_session ON messages(source, session_id, ts, id);
CREATE INDEX IF NOT EXISTS messages_project ON messages(project_id, ts);
CREATE INDEX IF NOT EXISTS sessions_project ON sessions(project_id, kind, last_at);
"""


def connect() -> sqlite3.Connection:
    home = ensure_home()
    path = home / "agentmem.db"
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        path.chmod(0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        meta_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        version = None
        if meta_exists:
            version = connection.execute(
                "SELECT v FROM meta WHERE k='schema_version'"
            ).fetchone()
        if version is not None:
            if version[0] != "1":
                raise ValueError("Unsupported database schema version: " + str(version[0]))
            from .migrations import migrate_session_projects

            migrate_session_projects(connection)
            # Once migrated, readers do not write or wait for the indexer.
            return connection

        # Setting WAL can race another first connection before either has
        # created the schema. SQLite does not always apply busy_timeout to this
        # pragma, so retry this initialization-only operation for up to 5 s.
        deadline = time.monotonic() + 5
        while True:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        # The version marker and all tables become visible together, including
        # when multiple callers initialize a fresh database concurrently.
        connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
    except BaseException:
        connection.close()
        raise
    return connection
