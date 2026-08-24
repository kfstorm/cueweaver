"""Durable storage for Job records."""

from __future__ import annotations

import json
import sqlite3
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import (
    JobRow,
    SqliteDatabase,
)
from ..errors import ServiceError
from .model import JobRecord, migrate_record, valid_job_id, valid_record


class JobRecordStore(Protocol):
    """Persistence boundary used by the serial Jobs application."""

    def load(self) -> list[JobRecord]: ...

    def write(self, record: JobRecord) -> None: ...

    def remove(self, job_id: str) -> None: ...


class SqliteJobRecordStore:
    """Persist Job records in SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def load(self) -> list[JobRecord]:
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

    def write(self, record: JobRecord) -> None:
        self._upsert(record)

    def remove(self, job_id: str) -> None:
        _require_valid_job_id(job_id)
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

    def _upsert(self, record: JobRecord) -> None:
        prepared = self._prepare_record(record)
        try:
            with self._database.session() as session:
                _upsert_row(session, prepared)
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
        _require_valid_job_id(job_id)
        if not valid_record(record, strict=True):
            raise ServiceError("invalid_job_record", "Job record is invalid")
        raw_record = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
        created_at = record.get("created_at")
        sequence = record.get("queue_sequence")
        if not isinstance(created_at, str) or not isinstance(sequence, int):
            raise ServiceError("invalid_job_record", "Job record metadata is invalid")
        return job_id, raw_record, created_at, sequence


def _require_valid_job_id(job_id: str) -> None:
    if not valid_job_id(job_id):
        raise ServiceError("invalid_job_id", "Job ID is invalid")


def _upsert_row(session: Session, prepared: tuple[str, str, str, int]) -> None:
    job_id, raw_record, created_at, sequence = prepared
    row = session.get(JobRow, job_id)
    if row is None:
        row = JobRow(id=job_id)
        session.add(row)
    row.record_json = raw_record
    row.created_at = created_at
    row.queue_sequence = sequence


__all__ = [
    "JobRecordStore",
    "SqliteJobRecordStore",
]
