"""Durable JSON storage for Job records."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..errors import ServiceError
from ..term_maps import reject_duplicate_json_pairs
from .model import JobRecord, migrate_record, valid_job_id

CORRUPT_DIRECTORY = "corrupt"
UNSUPPORTED_DIRECTORY = "unsupported"


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
                    # Keep the best-effort restoration from hiding the original failure.
                    record_path.write_bytes(previous)
            raise

    def _ensure_jobs_root(self) -> None:
        if self._jobs_root.is_symlink():
            raise ServiceError(
                "invalid_work_directory",
                "Job Work root must not be a symbolic link",
            )
        try:
            self._jobs_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ServiceError(
                "invalid_work_directory",
                "Job Work root cannot be created",
            ) from error


def _read_record_bytes(record_path: Path) -> bytes | None:
    try:
        return record_path.read_bytes()
    except OSError:
        return None


def _require_valid_job_id(job_id: str) -> None:
    if not valid_job_id(job_id):
        raise ServiceError("invalid_job_id", "Job ID is invalid")


def _classify_record(
    record_path: Path,
) -> tuple[str, JobRecord | None, bool, bytes | None, str | None]:
    raw_record = _read_record_bytes(record_path)
    if raw_record is None or record_path.is_symlink():
        return "corrupt", None, False, raw_record, None
    try:
        parsed = json.loads(raw_record, object_pairs_hook=reject_duplicate_json_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return "corrupt", None, False, raw_record, None
    if not isinstance(parsed, dict):
        return "corrupt", None, False, raw_record, None
    record_id = parsed.get("id")
    if not isinstance(record_id, str):
        record_id = None
    record, migrated, future = migrate_record(parsed)
    if future:
        return UNSUPPORTED_DIRECTORY, None, False, raw_record, record_id
    if record is None:
        return "corrupt", None, False, raw_record, record_id
    return "valid", record, migrated, raw_record, record_id


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


__all__ = ["FileJobRecordStore", "JobRecordHealth", "JobRecordStore"]
