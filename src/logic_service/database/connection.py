"""
Async SQLite connection manager using aiosqlite.

WAL journal mode is enabled on every connection for safe concurrent reads
while the ZMQ subscriber writes.
"""

from __future__ import annotations

import aiosqlite

# Global connection — initialised once in FastAPI lifespan
_db: aiosqlite.Connection | None = None


async def init_db(db_path: str = "logic_service.db") -> aiosqlite.Connection:
    """
    Open the SQLite database, enable WAL mode, and create tables.

    Returns the open connection (also stored as module-level singleton).
    """
    global _db
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row

    # Enable WAL for safe concurrent reads + writes
    await _db.execute("PRAGMA journal_mode=WAL;")

    await _db.execute("""
        CREATE TABLE IF NOT EXISTS crossing_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    TEXT    NOT NULL UNIQUE,
            track_id    INTEGER NOT NULL,
            person_id   TEXT    NOT NULL,
            direction   TEXT    NOT NULL,   -- "in" or "out"
            age         INTEGER,            -- NULL if uncertain
            gender      TEXT,               -- "M", "F", or NULL
            timestamp   REAL    NOT NULL,   -- Unix epoch (seconds)
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS stranger_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    TEXT    NOT NULL UNIQUE,
            track_id    INTEGER NOT NULL,
            person_id   TEXT    NOT NULL,
            age         INTEGER,
            gender      TEXT,
            alert_count INTEGER NOT NULL DEFAULT 1,
            timestamp   REAL    NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await _db.commit()
    return _db


async def close_db() -> None:
    """Close the database connection gracefully."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def get_db() -> aiosqlite.Connection:
    """Return the active connection (raises if not yet initialised)."""
    if _db is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _db
