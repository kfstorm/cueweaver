"""SQLite ORM storage for Term maps and directory bindings."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..application.database import (
    AppMetadataRow,
    DatabaseOpenError,
    DatabasePathError,
    DirectoryTermMapBindingRow,
    SqliteDatabase,
    TermMapRow,
)
from ..application.directory_term_maps import DirectoryTermMapStore
from ..application.errors import ServiceError
from ..application.term_maps import (
    TermMapDetail,
    TermMapStore,
    TermMapSummary,
    validate_term_map_content,
)
from .term_maps import _utc_timestamp


class LegacyTermMapSource(Protocol):
    def list(self) -> list[TermMapSummary]: ...

    def get(self, term_map_id: str) -> TermMapDetail: ...


class LegacyDirectoryBindingSource(Protocol):
    def snapshot_bindings(self) -> dict[str, str]: ...


class SqliteTermMapStore(TermMapStore):
    """Persist Term maps as ORM rows and import the legacy JSON layout once."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def list(self) -> list[TermMapSummary]:
        try:
            with self._database.session() as session:
                rows = session.scalars(
                    select(TermMapRow).order_by(TermMapRow.sequence, TermMapRow.id)
                ).all()
                return [_summary(row) for row in rows]
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map metadata cannot be read"
            ) from error
        except SQLAlchemyError as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map metadata cannot be read"
            ) from error

    def get(self, term_map_id: str) -> TermMapDetail:
        try:
            with self._database.session() as session:
                row = session.get(TermMapRow, term_map_id)
                if row is None:
                    raise ServiceError(
                        "term_map_not_found", "Term map does not exist", id=term_map_id
                    )
                content = _decode_content(row.content_json)
                return TermMapDetail(
                    id=row.id,
                    name=row.name,
                    entry_count=row.entry_count,
                    updated_at=row.updated_at,
                    content=content,
                )
        except ServiceError:
            raise
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map metadata cannot be read"
            ) from error
        except SQLAlchemyError as error:
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
                    entry_count=len(content),
                    updated_at=timestamp,
                    sequence=(sequence if sequence is not None else -1) + 1,
                    content_json=_encode_content(content),
                )
                session.add(row)
                session.commit()
                return _summary(row)
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
                session.commit()
                return _summary(row)
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
                row.entry_count = len(content)
                row.updated_at = _utc_timestamp()
                row.content_json = _encode_content(content)
                session.commit()
                return _summary(row)
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
                summary = _summary(row)
                session.execute(
                    delete(DirectoryTermMapBindingRow).where(
                        DirectoryTermMapBindingRow.term_map_id == term_map_id
                    )
                )
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

    def import_legacy(
        self,
        work_root: Path,
        term_maps: LegacyTermMapSource,
        bindings: LegacyDirectoryBindingSource | None,
    ) -> None:
        directory = work_root / "term-maps"
        if directory.is_symlink():
            raise ServiceError(
                "term_maps_unavailable", "Term map storage cannot be opened"
            )
        if not directory.exists():
            self._mark_import_complete()
            return
        try:
            with self._database.session() as session:
                marker = session.get(AppMetadataRow, _IMPORT_MARKER)
                if marker is not None and marker.value == "1":
                    self._retire_legacy_files(directory)
                    return
                legacy_records = term_maps.list()
                legacy_bindings = (
                    bindings.snapshot_bindings() if bindings is not None else {}
                )
                for sequence, summary in enumerate(legacy_records):
                    detail = term_maps.get(summary.id)
                    if detail.id != summary.id:
                        raise ServiceError(
                            "term_maps_unavailable", "Term map metadata is invalid"
                        )
                    session.add(
                        TermMapRow(
                            id=detail.id,
                            name=detail.name,
                            name_folded=detail.name.casefold(),
                            entry_count=len(detail.content),
                            updated_at=detail.updated_at,
                            sequence=sequence,
                            content_json=_encode_content(detail.content),
                        )
                    )
                session.flush()
                for directory_name, term_map_id in legacy_bindings.items():
                    if not any(item.id == term_map_id for item in legacy_records):
                        raise ServiceError(
                            "directory_term_maps_unavailable",
                            "Directory Term map metadata is invalid",
                        )
                    session.add(
                        DirectoryTermMapBindingRow(
                            directory=directory_name,
                            term_map_id=term_map_id,
                        )
                    )
                session.merge(AppMetadataRow(key=_IMPORT_MARKER, value="1"))
                session.commit()
        except ServiceError:
            raise
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map metadata cannot be imported"
            ) from error
        except IntegrityError as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map metadata is invalid"
            ) from error
        except SQLAlchemyError as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map metadata cannot be imported"
            ) from error
        self._retire_legacy_files(directory)

    def _mark_import_complete(self) -> None:
        try:
            with self._database.session() as session:
                session.merge(AppMetadataRow(key=_IMPORT_MARKER, value="1"))
                session.commit()
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map migration state cannot be saved"
            ) from error
        except SQLAlchemyError as error:
            raise ServiceError(
                "term_maps_unavailable", "Term map migration state cannot be saved"
            ) from error

    @staticmethod
    def _retire_legacy_files(directory: Path) -> None:
        for path in directory.glob("*.json"):
            with suppress(OSError):
                path.unlink()


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
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "directory_term_maps_unavailable",
                "Directory Term map metadata cannot be read",
            ) from error
        except SQLAlchemyError as error:
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
        except (DatabaseOpenError, DatabasePathError) as error:
            raise ServiceError(
                "directory_term_maps_unavailable",
                "Directory Term map binding cannot be saved",
            ) from error
        except SQLAlchemyError as error:
            raise ServiceError(
                "directory_term_map_write_failed",
                "Directory Term map binding cannot be saved",
            ) from error


def _require_row(session: Session, term_map_id: str) -> TermMapRow:
    row = session.get(TermMapRow, term_map_id)
    if row is None:
        raise ServiceError(
            "term_map_not_found", "Term map does not exist", id=term_map_id
        )
    return row


def _summary(row: TermMapRow) -> TermMapSummary:
    return TermMapSummary(
        id=row.id,
        name=row.name,
        entry_count=row.entry_count,
        updated_at=row.updated_at,
    )


def _decode_content(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return validate_term_map_content(value)
    except (TypeError, ValueError, json.JSONDecodeError, ServiceError) as error:
        raise ServiceError(
            "term_map_unreadable", "Term map content is invalid"
        ) from error


def _encode_content(content: Mapping[str, str]) -> str:
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))


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


_IMPORT_MARKER = "term_maps.legacy_json_import_complete"


__all__ = ["SqliteDirectoryTermMapStore", "SqliteTermMapStore"]
