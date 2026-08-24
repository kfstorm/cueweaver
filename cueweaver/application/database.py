"""Shared SQLite database, ORM models, and migration lifecycle."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from sqlalchemy import ForeignKey, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import Integer, String, Text


class Base(DeclarativeBase):
    """Base class for the application's persisted records."""


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    queue_sequence: Mapped[int] = mapped_column(Integer, nullable=False)


class TermMapRow(Base):
    __tablename__ = "term_maps"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_folded: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)


class DirectoryTermMapBindingRow(Base):
    __tablename__ = "directory_term_map_bindings"

    directory: Mapped[str] = mapped_column(String, primary_key=True)
    term_map_id: Mapped[str] = mapped_column(
        ForeignKey("term_maps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class AppMetadataRow(Base):
    __tablename__ = "app_metadata"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class DatabasePathError(OSError):
    """The SQLite database parent directory could not be created."""


class DatabaseOpenError(sqlite3.Error):
    """The SQLite database could not be bootstrapped or migrated."""


class SqliteDatabase:
    """Run migrations once and provide short-lived ORM sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._schema_lock = Lock()
        self._initialized = False
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Provide a raw connection for diagnostics and compatibility callers."""
        self._initialize()
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        self._initialize()
        assert self._session_factory is not None
        session = self._session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        """Release pooled connections owned by this application instance."""
        with self._schema_lock:
            if self._engine is not None:
                self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._initialized = False

    def _initialize(self) -> None:
        with self._schema_lock:
            if self._initialized:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise DatabasePathError from error
            try:
                engine = create_engine(
                    f"sqlite+pysqlite:///{self.path}",
                    connect_args={"timeout": 30},
                )
                event.listen(engine, "connect", _configure_sqlite_connection)
                config = _migration_config()
                config.attributes["connection"] = engine.connect()
                try:
                    command.upgrade(config, "head")
                finally:
                    config.attributes["connection"].close()
            except Exception as error:
                if "engine" in locals():
                    engine.dispose()
                raise DatabaseOpenError from error
            self._engine = engine
            self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            self._initialized = True


def _configure_sqlite_connection(
    dbapi_connection: object, _connection_record: object
) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.execute("PRAGMA foreign_keys = ON")


def _migration_config() -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).parents[1] / "migrations"),
    )
    return config


__all__ = [
    "AppMetadataRow",
    "Base",
    "DatabaseOpenError",
    "DatabasePathError",
    "DirectoryTermMapBindingRow",
    "JobRow",
    "SqliteDatabase",
    "TermMapRow",
]
