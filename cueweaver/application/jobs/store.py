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
    CURRENT_JOB_SCHEMA_VERSION,
    TERMINAL_JOB_STATUSES,
    JobRecord,
    copy_job_record,
    migrate_record,
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
                rows = session.scalars(
                    select(JobRow).order_by(
                        JobRow.queue_sequence, JobRow.created_at, JobRow.id
                    )
                ).all()
                return [_record_from_row(session, row) for row in rows]
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
        migrated, _was_migrated, future = migrate_record(record)
        if future:
            raise ServiceError(
                "unsupported_job_record", "Job record schema is unsupported"
            )
        prepared = copy_job_record(migrated or record)
        prepared.setdefault("schema_version", CURRENT_JOB_SCHEMA_VERSION)
        _ensure_history(prepared)
        job_id = prepared.get("id")
        if not isinstance(job_id, str):
            raise ServiceError("invalid_job_id", "Job ID is invalid")
        if not valid_record(prepared, strict=True):
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
    if row is None:
        row = JobRow(id=str(record["id"]))
        session.add(row)

    schema_version = record["schema_version"]
    attempt = record["attempt"]
    queue_sequence = record["queue_sequence"]
    assert isinstance(schema_version, int)
    assert isinstance(attempt, int)
    assert isinstance(queue_sequence, int)
    row.schema_version = schema_version
    row.status = str(record["status"])
    row.attempt = attempt
    row.created_at = str(record["created_at"])
    row.started_at = _optional_str(record.get("started_at"))
    row.finished_at = _optional_str(record.get("finished_at"))
    row.queue_sequence = queue_sequence
    _set_error_fields(row, error_values)
    term_map = _set_request_fields(row, request)
    _set_extraction_fields(row, extraction_values)

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

    # A Job owns this snapshot.  Once populated, later Job writes cannot replace it.
    has_snapshot = (
        session.scalar(
            select(JobTermMapSnapshotRow.position)
            .where(JobTermMapSnapshotRow.job_id == row.id)
            .limit(1)
        )
        is not None
    )
    if not has_snapshot and isinstance(term_map, dict):
        content = term_map.get("content")
        if isinstance(content, dict):
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


def _record_from_row(session: Session, row: JobRow) -> JobRecord:
    snapshot_rows = session.scalars(
        select(JobTermMapSnapshotRow)
        .where(JobTermMapSnapshotRow.job_id == row.id)
        .order_by(JobTermMapSnapshotRow.position)
    ).all()
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
        "schema_version": row.schema_version,
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
            for item in session.scalars(
                select(JobStatusHistoryRow)
                .where(JobStatusHistoryRow.job_id == row.id)
                .order_by(JobStatusHistoryRow.sequence)
            ).all()
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
    history = record.get("status_history")
    migrated, _legacy, future = migrate_record(record)
    if not isinstance(history, list) or not history or migrated is None or future:
        raise ServiceError(
            "job_store_corrupt", "Job database contains an invalid record"
        )
    return migrated


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
    row.term_map_id = (
        _optional_str(term_map.get("id")) if isinstance(term_map, dict) else None
    )
    row.term_map_name = (
        _optional_str(term_map.get("name")) if isinstance(term_map, dict) else None
    )
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


def _ensure_history(record: JobRecord) -> None:
    if isinstance(record.get("status_history"), list):
        return
    status = record.get("status")
    attempt = record.get("attempt")
    created_at = record.get("created_at")
    if (
        not isinstance(status, str)
        or not isinstance(attempt, int)
        or not isinstance(created_at, str)
    ):
        return
    finished_at = record.get("finished_at")
    if status in TERMINAL_JOB_STATUSES:
        finished_at = finished_at if isinstance(finished_at, str) else created_at
        record["finished_at"] = finished_at
    record["status_history"] = [
        {
            "status": status,
            "attempt": attempt,
            "started_at": created_at,
            "finished_at": finished_at,
        }
    ]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["JobRecordStore", "SqliteJobRecordStore"]
