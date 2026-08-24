"""Shared SQLite database bootstrap and connection lifecycle."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

APPLICATION_DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    queue_sequence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DatabasePathError(OSError):
    """The SQLite database parent directory could not be created."""


class DatabaseOpenError(sqlite3.Error):
    """The SQLite database could not be bootstrapped."""


class SqliteDatabase:
    """Bootstrap the application database and provide short-lived connections."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._schema_lock = Lock()
        self._initialized = False

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self._initialize()
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
        )
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._schema_lock:
            if self._initialized:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise DatabasePathError from error
            try:
                with sqlite3.connect(
                    self.path,
                    timeout=30,
                    isolation_level=None,
                ) as connection:
                    connection.executescript(APPLICATION_DATABASE_SCHEMA)
            except sqlite3.Error as error:
                raise DatabaseOpenError from error
            self._initialized = True


__all__ = ["DatabaseOpenError", "DatabasePathError", "SqliteDatabase"]
