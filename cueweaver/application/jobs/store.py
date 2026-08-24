"""Durable relational storage for Job records."""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import (
    JobRow,
    JobStatusHistoryRow,
    JobTermMapSnapshotRow,
    SqliteDatabase,
)
from ..errors import ServiceError
from .model import (
    JobRecord,
    copy_job_record,
    valid_job_id,
    valid_record,
)


class JobRecordStore(Protocol):
    """Persistence boundary used by the serial Jobs application."""

    def load(self) -> list[JobRecord]: ...

    def write(self, record: JobRecord) -> None: ...

    def remove(self, job_id: str) -> None: ...


class SqliteJobRecordStore:
    """Persist a Job across scalar and relational SQLite rows."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def load(self) -> list[JobRecord]:
        try:
            with self._database.read_session() as session:
                job_rows = session.scalars(
                    select(JobRow).order_by(
                        JobRow.queue_sequence, JobRow.created_at, JobRow.id
                    )
                ).all()
                history_rows = session.scalars(
                    select(JobStatusHistoryRow).order_by(
                        JobStatusHistoryRow.job_id, JobStatusHistoryRow.sequence
                    )
                ).all()
                snapshot_rows = session.scalars(
                    select(JobTermMapSnapshotRow).order_by(
                        JobTermMapSnapshotRow.job_id, JobTermMapSnapshotRow.position
                    )
                ).all()
                histories_by_job: dict[str, list[JobStatusHistoryRow]] = {}
                for history_row in history_rows:
                    histories_by_job.setdefault(history_row.job_id, []).append(
                        history_row
                    )
                snapshots_by_job: dict[str, list[JobTermMapSnapshotRow]] = {}
                for snapshot_row in snapshot_rows:
                    snapshots_by_job.setdefault(snapshot_row.job_id, []).append(
                        snapshot_row
                    )
                return [
                    _record_from_row(
                        row,
                        histories_by_job.get(row.id, []),
                        snapshots_by_job.get(row.id, []),
                    )
                    for row in job_rows
                ]
        except (sqlite3.Error, SQLAlchemyError) as error:
            raise ServiceError(
                "database_unavailable", "Job records cannot be loaded"
            ) from error

    def write(self, record: JobRecord) -> None:
        self._upsert(record)

    def remove(self, job_id: str) -> None:
        _require_valid_job_id(job_id)
        try:
            with self._database.write_transaction() as session:
                row = session.get(JobRow, job_id)
                if row is not None:
                    session.delete(row)
        except (sqlite3.Error, SQLAlchemyError) as error:
            raise ServiceError(
                "database_unavailable", "Job record could not be deleted"
            ) from error

    def _upsert(self, record: JobRecord) -> None:
        prepared = self._prepare_record(record)
        try:
            with self._database.write_transaction() as session:
                _upsert_row(session, prepared)
        except (sqlite3.Error, SQLAlchemyError) as error:
            raise ServiceError(
                "database_unavailable", "Job record could not be persisted"
            ) from error

    @staticmethod
    def _prepare_record(record: JobRecord) -> JobRecord:
        prepared = copy_job_record(record)
        job_id = prepared.get("id")
        if not isinstance(job_id, str):
            raise ServiceError("invalid_job_id", "Job ID is invalid")
        if not valid_record(prepared):
            raise ServiceError("invalid_job_record", "Job record is invalid")
        _require_valid_job_id(job_id)
        return prepared


def _require_valid_job_id(job_id: str) -> None:
    if not valid_job_id(job_id):
        raise ServiceError("invalid_job_id", "Job ID is invalid")


def _upsert_row(session: Session, record: JobRecord) -> None:
    request = record["request"]
    assert isinstance(request, dict)
    error = record.get("error")
    error_values = error if isinstance(error, dict) else {}
    extraction = record.get("extraction")
    extraction_values = extraction if isinstance(extraction, dict) else {}
    row = session.get(JobRow, record["id"])
    is_new_job = row is None
    if row is None:
        row = JobRow(id=str(record["id"]))
        session.add(row)

    attempt = record["attempt"]
    queue_sequence = record["queue_sequence"]
    assert isinstance(attempt, int)
    assert isinstance(queue_sequence, int)
    row.status = str(record["status"])
    row.attempt = attempt
    row.created_at = str(record["created_at"])
    row.started_at = _optional_str(record.get("started_at"))
    row.finished_at = _optional_str(record.get("finished_at"))
    row.queue_sequence = queue_sequence
    _set_error_fields(row, error_values)
    term_map = _set_request_fields(row, request)
    _set_extraction_fields(row, extraction_values)

    if is_new_job:
        _set_snapshot_fields(row, term_map, session)

    session.execute(
        delete(JobStatusHistoryRow).where(JobStatusHistoryRow.job_id == row.id)
    )
    history = record["status_history"]
    assert isinstance(history, list)
    for sequence, entry in enumerate(history):
        assert isinstance(entry, dict)
        session.add(
            JobStatusHistoryRow(
                job_id=row.id,
                sequence=sequence,
                status=str(entry["status"]),
                attempt=int(entry["attempt"]),
                started_at=str(entry["started_at"]),
                finished_at=_optional_str(entry.get("finished_at")),
            )
        )


def _set_snapshot_fields(row: JobRow, term_map: object, session: Session) -> None:
    if not isinstance(term_map, dict):
        return
    row.term_map_id = _optional_str(term_map.get("id"))
    row.term_map_name = _optional_str(term_map.get("name"))
    content = term_map.get("content")
    if not isinstance(content, dict):
        return
    for position, (source, target) in enumerate(content.items()):
        session.add(
            JobTermMapSnapshotRow(
                job_id=row.id,
                position=position,
                source=str(source),
                source_folded=str(source).casefold(),
                target=str(target),
            )
        )


def _record_from_row(
    row: JobRow,
    history_rows: list[JobStatusHistoryRow],
    snapshot_rows: list[JobTermMapSnapshotRow],
) -> JobRecord:
    content = {item.source: item.target for item in snapshot_rows}
    term_map: dict[str, object] | None = None
    if row.term_map_id is not None:
        term_map = {
            "id": row.term_map_id,
            "name": row.term_map_name or "",
            "content": content,
        }
    request: dict[str, object] = {
        "media_path": row.media_path,
        "target_language_code": row.target_language_code,
        "term_map_mode": row.term_map_mode,
        "term_map": term_map,
        "dynamic_terminology_enabled": bool(row.dynamic_terminology_enabled),
        "subtitle_terminology_filter_enabled": bool(
            row.subtitle_terminology_filter_enabled
        ),
        "output_suffix": row.output_suffix,
        "output_conflict_policy": row.output_conflict_policy,
        "output_path": row.output_path,
        "source_format": row.source_format,
    }
    if row.subtitle_path is not None:
        request["subtitle_path"] = row.subtitle_path
    if row.stream_index is not None:
        request["stream_index"] = row.stream_index

    record: JobRecord = {
        "id": row.id,
        "status": row.status,
        "attempt": row.attempt,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "request": request,
        "error": _error_from_row(row),
        "queue_sequence": row.queue_sequence,
        "status_history": [
            {
                "status": item.status,
                "attempt": item.attempt,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
            }
            for item in history_rows
        ],
    }
    if row.stream_index is not None:
        record["extraction"] = (
            None
            if row.extraction_status is None
            else {
                "status": row.extraction_status,
                "path": row.extraction_path,
                "format": row.extraction_format,
                "content_digest": row.extraction_content_digest,
            }
        )
    if not valid_record(record):
        raise ServiceError(
            "job_store_corrupt", "Job database contains an invalid record"
        )
    return record


def _set_error_fields(row: JobRow, error: dict[str, object]) -> None:
    row.error_code = _optional_str(error.get("code"))
    row.error_message = _optional_str(error.get("message"))
    row.error_field = _optional_str(error.get("field"))
    row.error_media_path = _optional_str(error.get("media_path"))
    row.error_output_path = _optional_str(error.get("output_path"))
    row.error_path = _optional_str(error.get("path"))
    row.error_stream_index = _optional_int(error.get("stream_index"))


def _set_request_fields(row: JobRow, request: dict[str, object]) -> object:
    row.media_path = str(request["media_path"])
    row.subtitle_path = _optional_str(request.get("subtitle_path"))
    row.stream_index = _optional_int(request.get("stream_index"))
    row.target_language_code = str(request["target_language_code"])
    row.term_map_mode = str(request["term_map_mode"])
    term_map = request.get("term_map")
    row.output_path = str(request["output_path"])
    row.source_format = str(request["source_format"])
    row.dynamic_terminology_enabled = bool(request["dynamic_terminology_enabled"])
    row.subtitle_terminology_filter_enabled = bool(
        request["subtitle_terminology_filter_enabled"]
    )
    row.output_suffix = str(request["output_suffix"])
    row.output_conflict_policy = str(request["output_conflict_policy"])
    return term_map


def _set_extraction_fields(row: JobRow, extraction: dict[str, object]) -> None:
    row.extraction_status = _optional_str(extraction.get("status"))
    row.extraction_path = _optional_str(extraction.get("path"))
    row.extraction_format = _optional_str(extraction.get("format"))
    row.extraction_content_digest = _optional_str(extraction.get("content_digest"))


def _error_from_row(row: JobRow) -> dict[str, object] | None:
    if row.error_code is None or row.error_message is None:
        return None
    error: dict[str, object] = {"code": row.error_code, "message": row.error_message}
    error.update(
        {
            key: value
            for key, value in (
                ("field", row.error_field),
                ("media_path", row.error_media_path),
                ("output_path", row.error_output_path),
                ("path", row.error_path),
                ("stream_index", row.error_stream_index),
            )
            if value is not None
        }
    )
    return error


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["JobRecordStore", "SqliteJobRecordStore"]
