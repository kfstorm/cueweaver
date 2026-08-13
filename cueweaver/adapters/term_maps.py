"""Durable, atomically published storage for Term maps."""

from __future__ import annotations

import builtins
import json
import os
import tempfile
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..application.errors import ServiceError
from ..application.term_maps import TermMapDetail, TermMapSummary

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported runtime is POSIX
    fcntl = None  # type: ignore[assignment]


class FileTermMapStore:
    """Store Term map content and its index below the configured Work root."""

    def __init__(self, work_root: Path) -> None:
        self._directory = Path(work_root) / "term-maps"
        self._index_path = self._directory / "index.json"
        self._lock_path = self._directory / ".lock"
        self._thread_lock = threading.RLock()

    def list(self) -> list[TermMapSummary]:
        with self._locked():
            return [self._summary(record) for record in self._read_index()]

    def get(self, term_map_id: str) -> TermMapDetail:
        with self._locked():
            record = self._find(self._read_index(), term_map_id)
            content_path = self._directory / record.content_file
            try:
                content = json.loads(content_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ServiceError(
                    "term_map_unreadable", "Term map content cannot be read"
                ) from error
            if not isinstance(content, dict):
                raise ServiceError("term_map_unreadable", "Term map content is invalid")
            summary = self._summary(record)
            return TermMapDetail(
                id=summary.id,
                name=summary.name,
                entry_count=summary.entry_count,
                updated_at=summary.updated_at,
                content=content,
            )

    def create(self, name: str, content: Mapping[str, str]) -> TermMapSummary:
        with self._locked():
            records = self._read_index()
            folded_name = name.casefold()
            if any(record.name.casefold() == folded_name for record in records):
                raise ServiceError(
                    "duplicate_term_map_name",
                    "A Term map with this name already exists",
                    name=name,
                )
            term_map_id = uuid.uuid4().hex
            timestamp = _utc_timestamp()
            record = _TermMapRecord(
                id=term_map_id,
                name=name,
                entry_count=len(content),
                updated_at=timestamp,
                content_file=f"{term_map_id}.json",
            )
            self._write_json(self._directory / record.content_file, content)
            try:
                self._write_json(
                    self._index_path, [item.to_json() for item in [*records, record]]
                )
            except ServiceError:
                (self._directory / record.content_file).unlink(missing_ok=True)
                raise
            return self._summary(record)

    def rename(self, term_map_id: str, name: str) -> TermMapSummary:
        with self._locked():
            records = self._read_index()
            record = self._find(records, term_map_id)
            folded_name = name.casefold()
            if any(
                item.id != term_map_id and item.name.casefold() == folded_name
                for item in records
            ):
                raise ServiceError(
                    "duplicate_term_map_name",
                    "A Term map with this name already exists",
                    name=name,
                )
            updated = _TermMapRecord(
                id=record.id,
                name=name,
                entry_count=record.entry_count,
                updated_at=_utc_timestamp(),
                content_file=record.content_file,
            )
            self._write_index(
                [
                    *records[: records.index(record)],
                    updated,
                    *records[records.index(record) + 1 :],
                ]
            )
            return self._summary(updated)

    def replace(self, term_map_id: str, content: Mapping[str, str]) -> TermMapSummary:
        with self._locked():
            records = self._read_index()
            record = self._find(records, term_map_id)
            replacement_file = f"{record.id}.{uuid.uuid4().hex}.json"
            replacement_path = self._directory / replacement_file
            self._write_json(replacement_path, content)
            updated = _TermMapRecord(
                id=record.id,
                name=record.name,
                entry_count=len(content),
                updated_at=_utc_timestamp(),
                content_file=replacement_file,
            )
            try:
                self._write_index(
                    [updated if item.id == term_map_id else item for item in records]
                )
            except ServiceError:
                replacement_path.unlink(missing_ok=True)
                raise
            self._remove_content_file(record.content_file)
            return self._summary(updated)

    def delete(self, term_map_id: str, name: str) -> TermMapSummary:
        with self._locked():
            records = self._read_index()
            record = self._find(records, term_map_id)
            if name != record.name:
                raise ServiceError(
                    "term_map_delete_confirmation_required",
                    "Enter the current Term map name to confirm deletion",
                    field="name",
                )
            self._write_index([item for item in records if item.id != term_map_id])
            self._remove_content_file(record.content_file)
            return self._summary(record)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            try:
                self._directory.mkdir(parents=True, exist_ok=True)
                lock_file = self._lock_path.open("a+")
            except OSError as error:
                raise ServiceError(
                    "term_maps_unavailable", "Term map storage cannot be opened"
                ) from error
            try:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()

    def _read_index(self) -> builtins.list[_TermMapRecord]:
        if self._index_path.exists():
            try:
                payload = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ServiceError(
                    "term_maps_unavailable", "Term map metadata cannot be read"
                ) from error
            if not isinstance(payload, list):
                raise ServiceError(
                    "term_maps_unavailable", "Term map metadata is invalid"
                )
            records = [_TermMapRecord.from_json(record) for record in payload]
        else:
            records = []
        self._remove_orphans(records)
        return records

    def _write_index(self, records: builtins.list[_TermMapRecord]) -> None:
        self._write_json(self._index_path, [item.to_json() for item in records])

    def _remove_content_file(self, content_file: str | None) -> None:
        if not content_file:
            return
        with suppress(OSError):
            (self._directory / content_file).unlink(missing_ok=True)

    def _remove_orphans(self, records: builtins.list[_TermMapRecord]) -> None:
        referenced = {record.content_file for record in records}
        for path in self._directory.iterdir():
            if path.name == self._index_path.name or path.name in referenced:
                continue
            if (
                path.suffix == ".json"
                or path.name.startswith(".deleted-")
                or (path.name.startswith(".") and ".json." in path.name)
            ):
                with suppress(OSError):
                    path.unlink(missing_ok=True)

    @staticmethod
    def _find(
        records: builtins.list[_TermMapRecord], term_map_id: str
    ) -> _TermMapRecord:
        for record in records:
            if record.id == term_map_id:
                return record
        raise ServiceError(
            "term_map_not_found", "Term map does not exist", id=term_map_id
        )

    @staticmethod
    def _summary(record: _TermMapRecord) -> TermMapSummary:
        return TermMapSummary(
            id=record.id,
            name=record.name,
            entry_count=record.entry_count,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}."
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            temporary_path.replace(path)
            temporary_path = None
        except (OSError, TypeError, ValueError) as error:
            raise ServiceError(
                "term_map_write_failed", "Term map cannot be saved"
            ) from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class _TermMapRecord:
    id: str
    name: str
    entry_count: int
    updated_at: str
    content_file: str

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "entry_count": self.entry_count,
            "updated_at": self.updated_at,
            "content_file": self.content_file,
        }

    @classmethod
    def from_json(cls, payload: object) -> _TermMapRecord:
        if not isinstance(payload, dict):
            raise ServiceError("term_maps_unavailable", "Term map metadata is invalid")
        record_id = payload.get("id")
        name = payload.get("name")
        updated_at = payload.get("updated_at")
        content_file = payload.get("content_file")
        if not all(
            isinstance(value, str) and value
            for value in (record_id, name, updated_at, content_file)
        ):
            raise ServiceError("term_maps_unavailable", "Term map metadata is invalid")
        entry_count = payload.get("entry_count")
        if not isinstance(entry_count, int) or entry_count < 1:
            raise ServiceError("term_maps_unavailable", "Term map metadata is invalid")
        assert isinstance(record_id, str)
        assert isinstance(name, str)
        assert isinstance(updated_at, str)
        assert isinstance(content_file, str)
        if Path(content_file).name != content_file or not content_file.endswith(
            ".json"
        ):
            raise ServiceError("term_maps_unavailable", "Term map metadata is invalid")
        return cls(
            id=record_id,
            name=name,
            entry_count=entry_count,
            updated_at=updated_at,
            content_file=content_file,
        )
