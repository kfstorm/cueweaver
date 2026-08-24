"""Shared SQLite database, ORM models, and migration lifecycle."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, ForeignKey, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import Boolean, Integer, String


class Base(DeclarativeBase):
    """Base class for the application's persisted records."""


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)
    error_field: Mapped[str | None] = mapped_column(String)
    error_media_path: Mapped[str | None] = mapped_column(String)
    error_output_path: Mapped[str | None] = mapped_column(String)
    error_path: Mapped[str | None] = mapped_column(String)
    error_stream_index: Mapped[int | None] = mapped_column(Integer)
    queue_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    media_path: Mapped[str] = mapped_column(String, nullable=False)
    subtitle_path: Mapped[str | None] = mapped_column(String)
    stream_index: Mapped[int | None] = mapped_column(Integer)
    target_language_code: Mapped[str] = mapped_column(String, nullable=False)
    term_map_mode: Mapped[str] = mapped_column(String, nullable=False)
    term_map_id: Mapped[str | None] = mapped_column(String)
    term_map_name: Mapped[str | None] = mapped_column(String)
    output_path: Mapped[str] = mapped_column(String, nullable=False)
    source_format: Mapped[str] = mapped_column(String, nullable=False)
    dynamic_terminology_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    subtitle_terminology_filter_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    output_suffix: Mapped[str] = mapped_column(String, nullable=False)
    output_conflict_policy: Mapped[str] = mapped_column(String, nullable=False)
    extraction_status: Mapped[str | None] = mapped_column(String)
    extraction_path: Mapped[str | None] = mapped_column(String)
    extraction_format: Mapped[str | None] = mapped_column(String)
    extraction_content_digest: Mapped[str | None] = mapped_column(String)


class JobStatusHistoryRow(Base):
    __tablename__ = "job_status_history"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String)


class _OrderedTermMapEntryFields:
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_folded: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False)


class JobTermMapSnapshotRow(_OrderedTermMapEntryFields, Base):
    __tablename__ = "job_term_map_snapshots"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )


class TermMapRow(Base):
    __tablename__ = "term_maps"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_folded: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)


class TermMapEntryRow(_OrderedTermMapEntryFields, Base):
    __tablename__ = "term_map_entries"

    term_map_id: Mapped[str] = mapped_column(
        ForeignKey("term_maps.id", ondelete="CASCADE"), primary_key=True
    )


class DirectoryTermMapBindingRow(Base):
    __tablename__ = "directory_term_map_bindings"

    directory: Mapped[str] = mapped_column(String, primary_key=True)
    term_map_id: Mapped[str] = mapped_column(
        ForeignKey("term_maps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class DatabasePathError(OSError):
    """The SQLite database parent directory could not be created."""


class DatabaseOpenError(sqlite3.Error):
    """The SQLite database could not be bootstrapped or migrated."""


class SqliteDatabase:
    """Run migrations once and provide explicit ORM read/write scopes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._schema_lock = Lock()
        self._initialized = False
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    def initialize(self) -> None:
        """Open the database and apply all pending schema migrations."""
        self._initialize()

    @contextmanager
    def read_session(self) -> Iterator[Session]:
        self.initialize()
        assert self._session_factory is not None
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    @contextmanager
    def write_transaction(self, *, immediate: bool = False) -> Iterator[Session]:
        self.initialize()
        assert self._session_factory is not None
        session = self._session_factory()
        try:
            if immediate:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session
            session.commit()
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
                    URL.create("sqlite+pysqlite", database=str(self.path)),
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
    "Base",
    "DatabaseOpenError",
    "DatabasePathError",
    "DirectoryTermMapBindingRow",
    "JobRow",
    "JobStatusHistoryRow",
    "JobTermMapSnapshotRow",
    "SqliteDatabase",
    "TermMapEntryRow",
    "TermMapRow",
]
