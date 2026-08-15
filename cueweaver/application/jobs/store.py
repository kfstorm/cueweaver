"""Durable JSON storage for Job records."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from ..errors import ServiceError
from .model import JobRecord


class JobRecordStore(Protocol):
    """Persistence boundary used by the serial Jobs application."""

    def load(self) -> list[JobRecord]: ...

    def write(self, job_id: str, record: JobRecord) -> None: ...

    def remove(self, job_id: str) -> None: ...


class FileJobRecordStore:
    """Store each Job record as an atomically replaced JSON file."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root

    def load(self) -> list[JobRecord]:
        records: list[JobRecord] = []
        for record_path in self._jobs_root.glob("*.json"):
            record = _read_record(record_path)
            if record is not None:
                records.append(record)
        return records

    def write(self, job_id: str, record: JobRecord) -> None:
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


def _read_record(record_path: Path) -> JobRecord | None:
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            return None
        return record
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None


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


__all__ = ["FileJobRecordStore", "JobRecordStore"]
