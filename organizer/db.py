"""SQLite storage for photos, detected faces and people."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT UNIQUE NOT NULL,
    taken_at    TEXT NOT NULL,          -- ISO 'YYYY-MM-DD HH:MM:SS'
    date_source TEXT NOT NULL,          -- exif | filename | file
    width       INTEGER,
    height      INTEGER,
    mtime       REAL,
    rating      INTEGER NOT NULL DEFAULT 0, -- 0-5 stars
    flag        INTEGER NOT NULL DEFAULT 0  -- 1 = pick, -1 = reject
);
CREATE INDEX IF NOT EXISTS idx_photos_taken ON photos(taken_at);

CREATE TABLE IF NOT EXISTS people (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT,
    named INTEGER NOT NULL DEFAULT 0    -- 0 = automatic group, 1 = named by the user
);

CREATE TABLE IF NOT EXISTS faces (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id  INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    x REAL, y REAL, w REAL, h REAL,     -- box as fractions of the (upright) photo size
    score     REAL,
    embedding BLOB NOT NULL,            -- 128 x float32, L2-normalised
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    locked    INTEGER NOT NULL DEFAULT 0 -- 1 = set by the user, never changed automatically
);
CREATE INDEX IF NOT EXISTS idx_faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, always close."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first version; older databases get them on startup.
MIGRATIONS = {
    "rating": "ALTER TABLE photos ADD COLUMN rating INTEGER NOT NULL DEFAULT 0",
    "flag": "ALTER TABLE photos ADD COLUMN flag INTEGER NOT NULL DEFAULT 0",
}


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(photos)")}
        for column, sql in MIGRATIONS.items():
            if column not in columns:
                conn.execute(sql)


def get_setting(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def person_label(row) -> str:
    return row["name"] or f"Person {row['id']}"
