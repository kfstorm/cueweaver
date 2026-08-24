"""SQLite ORM storage for Term maps and directory bindings."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..application.database import (
    DatabaseOpenError,
    DatabasePathError,
    DirectoryTermMapBindingRow,
    SqliteDatabase,
    TermMapEntryRow,
    TermMapRow,
)
from ..application.directory_term_maps import DirectoryTermMapStore
from ..application.errors import ServiceError
from ..application.term_maps import (
    TermMapDetail,
    TermMapStore,
    TermMapSummary,
)


class SqliteTermMapStore(TermMapStore):
    """Persist Term map metadata and ordered entries as relational rows."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def list(self) -> list[TermMapSummary]:
        try:
            with self._database.session() as session:
                rows = session.execute(
                    select(TermMapRow, func.count(TermMapEntryRow.position))
                    .outerjoin(
                        TermMapEntryRow,
                        TermMapEntryRow.term_map_id == TermMapRow.id,
                    )
                    .group_by(TermMapRow.id)
                    .order_by(TermMapRow.sequence, TermMapRow.id)
                ).all()
                return [_summary(row, count) for row, count in rows]
        except (DatabaseOpenError, DatabasePathError, SQLAlchemyError) as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map metadata cannot be read"
            ) from error

    def get(self, term_map_id: str) -> TermMapDetail:
        try:
            with self._database.session() as session:
                row = _require_row(session, term_map_id)
                entries = session.scalars(
                    select(TermMapEntryRow)
                    .where(TermMapEntryRow.term_map_id == term_map_id)
                    .order_by(TermMapEntryRow.position)
                ).all()
                content = {entry.source: entry.target for entry in entries}
                return TermMapDetail(
                    id=row.id,
                    name=row.name,
                    entry_count=len(entries),
                    updated_at=row.updated_at,
                    content=content,
                )
        except ServiceError:
            raise
        except (DatabaseOpenError, DatabasePathError, SQLAlchemyError) as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map metadata cannot be read"
            ) from error

    def create(self, name: str, content: Mapping[str, str]) -> TermMapSummary:
        timestamp = _utc_timestamp()
        try:
            with self._database.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                sequence = session.scalar(select(func.max(TermMapRow.sequence)))
                row = TermMapRow(
                    id=_new_id(),
                    name=name,
                    name_folded=name.casefold(),
                    updated_at=timestamp,
                    sequence=(sequence if sequence is not None else -1) + 1,
                )
                session.add(row)
                _replace_entries(session, row.id, content)
                count = _entry_count(session, row.id)
                session.commit()
                return _summary(row, count)
        except (
            DatabaseOpenError,
            DatabasePathError,
            IntegrityError,
            SQLAlchemyError,
        ) as error:
            raise _term_map_name_write_error(error, name) from error

    def rename(self, term_map_id: str, name: str) -> TermMapSummary:
        try:
            with self._database.session() as session:
                row = _require_row(session, term_map_id)
                row.name = name
                row.name_folded = name.casefold()
                row.updated_at = _utc_timestamp()
                count = _entry_count(session, term_map_id)
                session.commit()
                return _summary(row, int(count or 0))
        except ServiceError:
            raise
        except (
            DatabaseOpenError,
            DatabasePathError,
            IntegrityError,
            SQLAlchemyError,
        ) as error:
            raise _term_map_name_write_error(error, name) from error

    def replace(self, term_map_id: str, content: Mapping[str, str]) -> TermMapSummary:
        try:
            with self._database.session() as session:
                row = _require_row(session, term_map_id)
                _replace_entries(session, term_map_id, content)
                row.updated_at = _utc_timestamp()
                count = _entry_count(session, term_map_id)
                session.commit()
                return _summary(row, count)
        except ServiceError:
            raise
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map cannot be saved"
            ) from error
        except SQLAlchemyError as error:
            raise ServiceError(
                "term_map_write_failed", "Term map cannot be saved"
            ) from error

    def delete(self, term_map_id: str, name: str) -> TermMapSummary:
        try:
            with self._database.session() as session:
                row = _require_row(session, term_map_id)
                if name != row.name:
                    raise ServiceError(
                        "term_map_delete_confirmation_required",
                        "Enter the current Term map name to confirm deletion",
                        field="name",
                    )
                count = _entry_count(session, term_map_id)
                summary = _summary(row, int(count or 0))
                session.delete(row)
                session.commit()
                return summary
        except ServiceError:
            raise
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map cannot be deleted"
            ) from error
        except SQLAlchemyError as error:
            raise ServiceError(
                "term_map_write_failed", "Term map cannot be deleted"
            ) from error


class SqliteDirectoryTermMapStore(DirectoryTermMapStore):
    """Persist canonical Media-relative directory bindings in SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def snapshot_bindings(self) -> dict[str, str]:
        try:
            with self._database.session() as session:
                rows = session.scalars(
                    select(DirectoryTermMapBindingRow).order_by(
                        DirectoryTermMapBindingRow.directory
                    )
                ).all()
                return {row.directory: row.term_map_id for row in rows}
        except (DatabaseOpenError, DatabasePathError, SQLAlchemyError) as error:
            raise ServiceError(
                "directory_term_maps_unavailable",
                "Directory Term map metadata cannot be read",
            ) from error

    def bind(
        self,
        directory: str,
        term_map_id: str,
        validate: Callable[[str], object] | None = None,
    ) -> None:
        if callable(validate):
            validate(term_map_id)
        try:
            with self._database.session() as session:
                row = session.get(DirectoryTermMapBindingRow, directory)
                if row is None:
                    session.add(
                        DirectoryTermMapBindingRow(
                            directory=directory, term_map_id=term_map_id
                        )
                    )
                else:
                    row.term_map_id = term_map_id
                session.commit()
        except ServiceError:
            raise
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "directory_term_maps_unavailable",
                "Directory Term map binding cannot be saved",
            ) from error
        except IntegrityError as error:
            raise ServiceError(
                "term_map_not_found", "Term map does not exist", id=term_map_id
            ) from error
        except SQLAlchemyError as error:
            raise ServiceError(
                "directory_term_map_write_failed",
                "Directory Term map binding cannot be saved",
            ) from error

    def remove(self, directory: str) -> None:
        try:
            with self._database.session() as session:
                session.execute(
                    delete(DirectoryTermMapBindingRow).where(
                        DirectoryTermMapBindingRow.directory == directory
                    )
                )
                session.commit()
        except (DatabaseOpenError, DatabasePathError, SQLAlchemyError) as error:
            raise ServiceError(
                "directory_term_maps_unavailable",
                "Directory Term map binding cannot be saved",
            ) from error


def _replace_entries(
    session: Session, term_map_id: str, content: Mapping[str, str]
) -> None:
    session.execute(
        delete(TermMapEntryRow).where(TermMapEntryRow.term_map_id == term_map_id)
    )
    for position, (source, target) in enumerate(content.items()):
        session.add(
            TermMapEntryRow(
                term_map_id=term_map_id,
                position=position,
                source=source,
                source_folded=source.casefold(),
                target=target,
            )
        )


def _entry_count(session: Session, term_map_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(TermMapEntryRow.position)).where(
                TermMapEntryRow.term_map_id == term_map_id
            )
        )
        or 0
    )


def _require_row(session: Session, term_map_id: str) -> TermMapRow:
    row = session.get(TermMapRow, term_map_id)
    if row is None:
        raise ServiceError(
            "term_map_not_found", "Term map does not exist", id=term_map_id
        )
    return row


def _summary(row: TermMapRow, entry_count: int) -> TermMapSummary:
    return TermMapSummary(
        id=row.id,
        name=row.name,
        entry_count=entry_count,
        updated_at=row.updated_at,
    )


def _term_map_name_write_error(error: Exception, name: str) -> ServiceError:
    if isinstance(error, (DatabaseOpenError, DatabasePathError)):
        return ServiceError("term_maps_unavailable", "Term map cannot be saved")
    if isinstance(error, IntegrityError):
        return ServiceError(
            "duplicate_term_map_name",
            "A Term map with this name already exists",
            name=name,
        )
    return ServiceError("term_map_write_failed", "Term map cannot be saved")


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["SqliteDirectoryTermMapStore", "SqliteTermMapStore"]
