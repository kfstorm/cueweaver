"""Durable storage for Job records."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import (
    AppMetadataRow,
    DatabaseOpenError,
    DatabasePathError,
    JobRow,
    SqliteDatabase,
)
from ..errors import ServiceError
from .model import JobRecord, migrate_record, valid_job_id, valid_record

CORRUPT_DIRECTORY = "corrupt"
UNSUPPORTED_DIRECTORY = "unsupported"


class _JsonPairs(list[tuple[object, object]]):
    """Preserve object pairs for the one nested duplicate-key check we need."""


@dataclass(frozen=True)
class JobRecordHealth:
    corrupt_count: int
    unsupported_count: int
    corrupt_location: str = "jobs/corrupt"
    unsupported_location: str = "jobs/unsupported"


class JobRecordStore(Protocol):
    """Persistence boundary used by the serial Jobs application."""

    def load(self) -> list[JobRecord]: ...

    def write(self, job_id: str, record: JobRecord) -> None: ...

    def remove(self, job_id: str) -> None: ...

    def health(self) -> JobRecordHealth: ...


class FileJobRecordStore:
    """Store each Job record as an atomically replaced JSON file."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root

    def load(self) -> list[JobRecord]:
        if not self._jobs_root.exists() and not self._jobs_root.is_symlink():
            return []
        self._ensure_jobs_root()
        records: list[JobRecord] = []
        record_paths = sorted(self._jobs_root.glob("*.json"))
        initial_classifications = {
            path: _classify_record(path) for path in record_paths
        }
        processed_paths: set[Path] = set()
        for record_path in record_paths:
            if record_path in processed_paths:
                continue
            classification, record, migrated, raw_record, _ = initial_classifications[
                record_path
            ]
            if classification != "valid":
                _quarantine(record_path, classification, raw_record)
                continue
            assert record is not None
            job_id = str(record["id"])
            canonical_path = self._jobs_root / f"{job_id}.json"
            target = initial_classifications.get(canonical_path)
            if record_path != canonical_path and target is not None:
                target_classification, _, _, target_raw, target_id = target
                if target_classification == "valid" or (
                    target_classification == UNSUPPORTED_DIRECTORY
                    and target_id == job_id
                ):
                    _quarantine(record_path, CORRUPT_DIRECTORY, raw_record)
                    continue
                if canonical_path not in processed_paths:
                    _quarantine(canonical_path, target_classification, target_raw)
                    processed_paths.add(canonical_path)
            if migrated or record_path != canonical_path:
                self.write(job_id, record)
            if record_path != canonical_path:
                record_path.unlink()
                processed_paths.add(canonical_path)
                _fsync_directory(self._jobs_root)
            records.append(record)
        return records

    def health(self) -> JobRecordHealth:
        return JobRecordHealth(
            corrupt_count=_quarantine_count(self._jobs_root, CORRUPT_DIRECTORY),
            unsupported_count=_quarantine_count(self._jobs_root, UNSUPPORTED_DIRECTORY),
        )

    def write(self, job_id: str, record: JobRecord) -> None:
        _require_valid_job_id(job_id)
        self._ensure_jobs_root()
        destination = self._jobs_root / f"{job_id}.json"
        previous = destination.read_bytes() if destination.exists() else None
        descriptor, raw_path = tempfile.mkstemp(
            dir=self._jobs_root, prefix=f".{job_id}."
        )
        temporary = Path(raw_path)
        replaced = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(record, file, ensure_ascii=True, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(destination)
            replaced = True
            _fsync_directory(self._jobs_root)
        except OSError:
            if replaced:
                _restore_record(destination, previous)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def remove(self, job_id: str) -> None:
        _require_valid_job_id(job_id)
        self._ensure_jobs_root()
        record_path = self._jobs_root / f"{job_id}.json"
        previous: bytes | None = None
        removed = False
        try:
            previous = record_path.read_bytes() if record_path.exists() else None
            record_path.unlink(missing_ok=True)
            removed = previous is not None
            _fsync_directory(self._jobs_root)
        except OSError:
            if removed and previous is not None:
                try:
                    _restore_record(record_path, previous)
                except OSError:
                    record_path.write_bytes(previous)
            raise

    def _ensure_jobs_root(self) -> None:
        _ensure_jobs_root(self._jobs_root)


class SqliteJobRecordStore:
    """Persist Job records in SQLite while importing the legacy JSON layout."""

    def __init__(self, jobs_root: Path, database: SqliteDatabase) -> None:
        self._jobs_root = jobs_root
        self._database = database
        self._legacy_store = FileJobRecordStore(jobs_root)
        self._migration_ready = False

    def load(self) -> list[JobRecord]:
        self._ensure_migrated()
        try:
            with self._database.session() as session:
                rows = session.scalars(
                    select(JobRow).order_by(
                        JobRow.queue_sequence, JobRow.created_at, JobRow.id
                    )
                ).all()
        except (sqlite3.Error, SQLAlchemyError) as error:
            raise ServiceError(
                "database_unavailable", "Job records cannot be loaded"
            ) from error
        records: list[JobRecord] = []
        migrated_records: list[JobRecord] = []
        for row in rows:
            try:
                record = json.loads(row.record_json)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ServiceError(
                    "job_store_corrupt", "Job database contains invalid record data"
                ) from error
            if not isinstance(record, dict):
                raise ServiceError(
                    "job_store_corrupt", "Job database contains an invalid record"
                )
            migrated, _legacy, future = migrate_record(record)
            if migrated is None or future:
                raise ServiceError(
                    "job_store_corrupt", "Job database contains an invalid record"
                )
            if migrated.get("id") != row.id:
                raise ServiceError(
                    "job_store_corrupt", "Job database contains a mismatched record"
                )
            records.append(migrated)
            if migrated != record:
                migrated_records.append(migrated)
        for record in migrated_records:
            self._upsert(record)
        return records

    def health(self) -> JobRecordHealth:
        return self._legacy_store.health()

    def write(self, job_id: str, record: JobRecord) -> None:
        _require_valid_job_id(job_id)
        self._ensure_migrated()
        self._ensure_jobs_root()
        self._upsert(record)

    def remove(self, job_id: str) -> None:
        _require_valid_job_id(job_id)
        self._ensure_migrated()
        try:
            with self._database.session() as session:
                row = session.get(JobRow, job_id)
                if row is not None:
                    session.delete(row)
                session.commit()
        except (sqlite3.Error, SQLAlchemyError) as error:
            raise ServiceError(
                "database_unavailable", "Job record could not be deleted"
            ) from error

    def _ensure_jobs_root(self) -> None:
        _ensure_jobs_root(self._jobs_root)

    def _migration_complete(self) -> bool:
        try:
            with self._database.session() as session:
                row = session.get(AppMetadataRow, _JOB_IMPORT_MARKER)
            return row is not None and row.value == "1"
        except DatabasePathError as error:
            raise ServiceError(
                "invalid_work_directory", "Job Work root cannot be created"
            ) from error
        except DatabaseOpenError as error:
            raise ServiceError(
                "database_unavailable", "Job database cannot be opened"
            ) from error
        except (sqlite3.Error, SQLAlchemyError) as error:
            raise ServiceError(
                "database_unavailable", "Job migration state could not be read"
            ) from error

    def _ensure_migrated(self) -> None:
        if self._migration_ready:
            return
        if self._jobs_root.exists() or self._jobs_root.is_symlink():
            self._ensure_jobs_root()
        if self._migration_complete():
            self._retire_legacy_snapshots()
            self._migration_ready = True
            return
        legacy_records = (
            self._legacy_store.load()
            if self._jobs_root.exists() or self._jobs_root.is_symlink()
            else []
        )
        try:
            with self._database.session() as session:
                for record in legacy_records:
                    _upsert_row(session, record)
                session.merge(AppMetadataRow(key=_JOB_IMPORT_MARKER, value="1"))
                session.commit()
        except (sqlite3.Error, SQLAlchemyError) as error:
            raise ServiceError(
                "database_unavailable", "Legacy Job records could not be imported"
            ) from error
        self._retire_legacy_snapshots()
        self._migration_ready = True

    def _upsert(self, record: JobRecord) -> None:
        self._prepare_record(record)
        try:
            with self._database.session() as session:
                _upsert_row(session, record)
                session.commit()
        except (sqlite3.Error, SQLAlchemyError) as error:
            raise ServiceError(
                "database_unavailable", "Job record could not be persisted"
            ) from error

    @staticmethod
    def _prepare_record(record: JobRecord) -> tuple[str, str, str, int]:
        migrated, _was_migrated, future = migrate_record(record)
        if migrated is not None:
            record = migrated
        elif future:
            raise ServiceError(
                "unsupported_job_record", "Job record schema is unsupported"
            )
        job_id = record.get("id")
        if not isinstance(job_id, str):
            raise ServiceError("invalid_job_id", "Job ID is invalid")
        if not valid_record(record, strict=True):
            raise ServiceError("invalid_job_record", "Job record is invalid")
        raw_record = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
        created_at = record.get("created_at")
        sequence = record.get("queue_sequence")
        if not isinstance(created_at, str) or not isinstance(sequence, int):
            raise ServiceError("invalid_job_record", "Job record metadata is invalid")
        return job_id, raw_record, created_at, sequence

    def _retire_legacy_snapshots(self) -> None:
        if not self._jobs_root.exists():
            return
        for record_path in self._jobs_root.glob("*.json"):
            _retire_snapshot_best_effort(record_path)


def _read_record_bytes(record_path: Path) -> bytes | None:
    try:
        return record_path.read_bytes()
    except OSError:
        return None


def _retire_snapshot_best_effort(record_path: Path) -> None:
    with suppress(OSError):
        record_path.unlink()


def _fsync_directory(directory: Path) -> None:
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _restore_record(destination: Path, previous: bytes | None) -> None:
    if previous is None:
        destination.unlink(missing_ok=True)
        _fsync_directory(destination.parent)
        return
    descriptor, raw_path = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.restore."
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(previous)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _require_valid_job_id(job_id: str) -> None:
    if not valid_job_id(job_id):
        raise ServiceError("invalid_job_id", "Job ID is invalid")


def _upsert_row(session: Session, record: JobRecord) -> None:
    job_id, raw_record, created_at, sequence = SqliteJobRecordStore._prepare_record(
        record
    )
    row = session.get(JobRow, job_id)
    if row is None:
        row = JobRow(id=job_id)
        session.add(row)
    row.record_json = raw_record
    row.created_at = created_at
    row.queue_sequence = sequence


_JOB_IMPORT_MARKER = "jobs.legacy_json_import_complete"


def _ensure_jobs_root(jobs_root: Path) -> None:
    if jobs_root.is_symlink():
        raise ServiceError(
            "invalid_work_directory",
            "Job Work root must not be a symbolic link",
        )
    try:
        jobs_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ServiceError(
            "invalid_work_directory",
            "Job Work root cannot be created",
        ) from error


def _classify_record(
    record_path: Path,
) -> tuple[str, JobRecord | None, bool, bytes | None, str | None]:
    raw_record = _read_record_bytes(record_path)
    if raw_record is None or record_path.is_symlink():
        return "corrupt", None, False, raw_record, None
    try:
        parsed = json.loads(raw_record)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return "corrupt", None, False, raw_record, None
    if not isinstance(parsed, dict):
        return "corrupt", None, False, raw_record, None
    record_id = parsed.get("id")
    if not isinstance(record_id, str):
        record_id = None
    if _term_map_content_has_duplicate_keys(raw_record):
        record, migrated, future = None, False, False
    else:
        record, migrated, future = migrate_record(parsed)
    if future:
        return UNSUPPORTED_DIRECTORY, None, False, raw_record, record_id
    if record is None:
        return "corrupt", None, False, raw_record, record_id
    return "valid", record, migrated, raw_record, record_id


def _term_map_content_has_duplicate_keys(raw_record: bytes) -> bool:
    paired = json.loads(raw_record, object_pairs_hook=_JsonPairs)
    request = _last_json_object_value(paired, "request")
    term_map = _last_json_object_value(request, "term_map")
    content = _last_json_object_value(term_map, "content")
    if not isinstance(content, _JsonPairs):
        return False
    keys = [key for key, _value in content]
    return len(keys) != len(set(keys))


def _last_json_object_value(value: object, key: str) -> object | None:
    if not isinstance(value, _JsonPairs):
        return None
    result: object | None = None
    for object_key, object_value in value:
        if object_key == key:
            result = object_value
    return result


def _quarantine_destination(directory: Path, filename: str) -> Path:
    destination = directory / filename
    if not destination.exists() and not destination.is_symlink():
        return destination
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    number = 2
    while True:
        candidate = directory / f"{stem}.{number}{suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        number += 1


def _quarantine(
    record_path: Path, category: str, raw_record: bytes | None = None
) -> None:
    directory = record_path.parent / category
    if directory.is_symlink():
        raise ServiceError(
            "invalid_work_directory",
            "Job record quarantine directory must not be a symbolic link",
        )
    directory.mkdir(parents=True, exist_ok=True)
    destination = _quarantine_destination(directory, record_path.name)
    if raw_record is not None and record_path.is_symlink():
        descriptor, raw_path = tempfile.mkstemp(
            dir=directory, prefix=f".{destination.name}."
        )
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(raw_record)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(destination)
            record_path.unlink()
        finally:
            temporary.unlink(missing_ok=True)
    else:
        record_path.replace(destination)
    _fsync_directory(directory)
    _fsync_directory(record_path.parent)


def _quarantine_count(jobs_root: Path, category: str) -> int:
    directory = jobs_root / category
    try:
        return sum(
            1
            for path in directory.glob("*.json")
            if path.is_file() or path.is_symlink()
        )
    except OSError:
        return 0


__all__ = [
    "FileJobRecordStore",
    "JobRecordHealth",
    "JobRecordStore",
    "SqliteJobRecordStore",
]
