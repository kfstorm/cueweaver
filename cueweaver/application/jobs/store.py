"""Durable storage for Job records."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

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
        descriptor, raw_path = tempfile.mkstemp(
            dir=self._jobs_root, prefix=f".{job_id}."
        )
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(record, file, ensure_ascii=True, indent=2)
                file.write("\n")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def remove(self, job_id: str) -> None:
        _require_valid_job_id(job_id)
        self._ensure_jobs_root()
        record_path = self._jobs_root / f"{job_id}.json"
        record_path.unlink(missing_ok=True)

    def _ensure_jobs_root(self) -> None:
        _ensure_jobs_root(self._jobs_root)


_UPSERT_JOB_SQL = """
INSERT INTO jobs (
    id, record_json, created_at, queue_sequence
) VALUES (?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    record_json = excluded.record_json,
    created_at = excluded.created_at,
    queue_sequence = excluded.queue_sequence
"""


class SqliteJobRecordStore:
    """Persist Job records in SQLite while importing the legacy JSON layout."""

    def __init__(self, jobs_root: Path, database_path: Path | None = None) -> None:
        self._jobs_root = jobs_root
        self._database_path = database_path or jobs_root / "jobs.sqlite3"
        self._legacy_store = FileJobRecordStore(jobs_root)
        self._schema_lock = Lock()
        self._initialized = False

    def load(self) -> list[JobRecord]:
        self._ensure_migrated()
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT id, record_json FROM jobs "
                    "ORDER BY queue_sequence, created_at, id"
                ).fetchall()
        except sqlite3.Error as error:
            raise ServiceError(
                "job_store_unavailable", "Job records cannot be loaded"
            ) from error
        records: list[JobRecord] = []
        migrated_records: list[JobRecord] = []
        for row in rows:
            try:
                record = json.loads(row[1])
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
            if migrated.get("id") != row[0]:
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
        self._upsert(record)

    def remove(self, job_id: str) -> None:
        _require_valid_job_id(job_id)
        self._ensure_migrated()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                connection.commit()
        except sqlite3.Error as error:
            raise ServiceError(
                "job_store_unavailable", "Job record could not be deleted"
            ) from error

    def _ensure_jobs_root(self) -> None:
        _ensure_jobs_root(self._jobs_root)

    def _migration_complete(self) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'legacy_import_complete'"
            ).fetchone()
        return row is not None and row[0] == "1"

    def _ensure_migrated(self) -> None:
        if self._jobs_root.exists() or self._jobs_root.is_symlink():
            self._ensure_jobs_root()
        self._initialize()
        if self._migration_complete():
            self._drop_legacy_tombstones()
            self._retire_legacy_snapshots()
            return
        deleted_ids = self._legacy_deleted_ids()
        legacy_records = (
            self._legacy_store.load()
            if self._jobs_root.exists() or self._jobs_root.is_symlink()
            else []
        )
        legacy_records = [
            record for record in legacy_records if record.get("id") not in deleted_ids
        ]
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for record in legacy_records:
                    job_id, raw_record, created_at, sequence = self._prepare_record(
                        record
                    )
                    connection.execute(
                        _UPSERT_JOB_SQL,
                        (job_id, raw_record, created_at, sequence),
                    )
                connection.execute(
                    """
                    INSERT INTO metadata (key, value)
                    VALUES ('legacy_import_complete', '1')
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """
                )
                connection.execute("DROP TABLE IF EXISTS deleted_jobs")
                connection.commit()
        except sqlite3.Error as error:
            raise ServiceError(
                "job_store_unavailable", "Legacy Job records could not be imported"
            ) from error
        self._retire_legacy_snapshots()

    def _legacy_deleted_ids(self) -> set[str]:
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'deleted_jobs'"
            ).fetchone()
            if exists is None:
                return set()
            return {row[0] for row in connection.execute("SELECT id FROM deleted_jobs")}

    def _drop_legacy_tombstones(self) -> None:
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DROP TABLE IF EXISTS deleted_jobs")
                connection.commit()
        except sqlite3.Error as error:
            raise ServiceError(
                "job_store_unavailable", "Legacy Job metadata could not be retired"
            ) from error

    def _initialize(self) -> None:
        with self._schema_lock:
            if self._initialized:
                return
            try:
                self._database_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise ServiceError(
                    "invalid_work_directory",
                    "Job Work root cannot be created",
                ) from error
            try:
                with self._connection() as connection:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS jobs (
                            id TEXT PRIMARY KEY,
                            record_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            queue_sequence INTEGER NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS metadata (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        );
                        """
                    )
            except sqlite3.Error as error:
                raise ServiceError(
                    "job_store_unavailable", "Job database cannot be opened"
                ) from error
            self._initialized = True

    def _upsert(self, record: JobRecord) -> None:
        self._initialize()
        job_id, raw_record, created_at, sequence = self._prepare_record(record)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    _UPSERT_JOB_SQL,
                    (job_id, raw_record, created_at, sequence),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise ServiceError(
                "job_store_unavailable", "Job record could not be persisted"
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

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=30,
                isolation_level=None,
            )
            yield connection
        except sqlite3.Error as error:
            raise ServiceError(
                "job_store_unavailable", "Job database cannot be opened"
            ) from error
        finally:
            if connection is not None:
                connection.close()

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


def _require_valid_job_id(job_id: str) -> None:
    if not valid_job_id(job_id):
        raise ServiceError("invalid_job_id", "Job ID is invalid")


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
            temporary.replace(destination)
            record_path.unlink()
        finally:
            temporary.unlink(missing_ok=True)
    else:
        record_path.replace(destination)


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
