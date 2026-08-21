"""Job records and projections used by the Jobs application."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from ...work import is_safe_job_identifier
from ..errors import ServiceError
from ..term_maps import validate_term_map_content

JobStatus = Literal[
    "Queued",
    "Extracting",
    "Translating",
    "Completed",
    "Failed",
    "Interrupted",
    "Cancelled",
]
JobRecord = dict[str, object]
CURRENT_JOB_SCHEMA_VERSION = 1
STATUS_HISTORY_FIELDS = frozenset({"status", "attempt", "started_at", "finished_at"})

JOB_STATUSES = frozenset(
    {
        "Queued",
        "Extracting",
        "Translating",
        "Completed",
        "Failed",
        "Interrupted",
        "Cancelled",
    }
)
TERMINAL_JOB_STATUSES = frozenset({"Completed", "Failed", "Interrupted", "Cancelled"})
HISTORY_CURSOR_LENGTH_LIMIT = 512


@dataclass(frozen=True)
class JobSummary:
    """The bounded record shape used by Job history and active-job polling."""

    record: Mapping[str, object]
    queue_position: int | None

    def to_dict(self) -> JobRecord:
        projected = _project_common(self.record, summary=True)
        projected["queue_position"] = self.queue_position
        return projected


@dataclass(frozen=True)
class JobDetail(JobSummary):
    """The detail shape exposed by HTTP without immutable Term map content."""

    def to_dict(self) -> JobRecord:
        projected = _project_common(self.record, summary=False)
        projected["queue_position"] = self.queue_position
        return projected


def project_job_detail(record: JobRecord, queue_position: int | None) -> JobRecord:
    return JobDetail(record, queue_position).to_dict()


def project_job_summary(record: JobRecord, queue_position: int | None) -> JobRecord:
    return JobSummary(record, queue_position).to_dict()


def _project_common(record: Mapping[str, object], *, summary: bool) -> JobRecord:
    fields: tuple[str, ...] = (
        "id",
        "status",
        "attempt",
        "created_at",
        "started_at",
        "finished_at",
        "error",
    )
    if not summary:
        fields += ("extraction", "status_history")
    copied_record = copy_job_record(record)
    projected = {
        field: copied_record[field] for field in fields if field in copied_record
    }
    request = record.get("request")
    if isinstance(request, dict):
        request_fields: tuple[str, ...] = (
            "media_path",
            "subtitle_path",
            "stream_index",
            "target_language_code",
            "term_map_mode",
            "output_path",
            "source_format",
        )
        if not summary:
            request_fields += (
                "dynamic_terminology_enabled",
                "subtitle_terminology_filter_enabled",
                "output_suffix",
                "output_conflict_policy",
            )
        copied_request = copy_job_record(request)
        projected_request = {
            field: copied_request[field]
            for field in request_fields
            if field in copied_request
        }
        term_map = request.get("term_map")
        if isinstance(term_map, dict):
            projected_request["term_map"] = {
                field: term_map[field] for field in ("id", "name") if field in term_map
            }
        else:
            projected_request["term_map"] = None
        projected["request"] = projected_request
    return projected


def encode_history_cursor(
    created_at: str,
    job_id: str,
    search: str = "",
    status: str = "all",
) -> str:
    payload = json.dumps(
        {"created_at": created_at, "id": job_id, "search": search, "status": status},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_history_cursor(cursor: str) -> tuple[str, str, str, str]:
    if (
        not cursor
        or len(cursor) > HISTORY_CURSOR_LENGTH_LIMIT
        or any(character.isspace() for character in cursor)
        or any(
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in cursor
        )
    ):
        raise ValueError("Invalid history cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = urlsafe_b64decode(cursor + padding)
        if urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != cursor:
            raise ValueError("Invalid history cursor")
        payload = json.loads(decoded.decode("utf-8"))
    except (Base64Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid history cursor") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"created_at", "id", "search", "status"}
        or not isinstance(payload["created_at"], str)
        or not payload["created_at"]
        or not isinstance(payload["id"], str)
        or not payload["id"]
        or not isinstance(payload["search"], str)
        or not isinstance(payload["status"], str)
    ):
        raise ValueError("Invalid history cursor")
    return payload["created_at"], payload["id"], payload["search"], payload["status"]


def copy_job_record(record: Mapping[str, object]) -> JobRecord:
    copied = json.loads(json.dumps(record))
    if not isinstance(copied, dict):
        raise TypeError("Job record must be an object")
    return copied


def valid_job_id(value: object) -> bool:
    return is_safe_job_identifier(value)


def _valid_strict_record_metadata(record: JobRecord) -> bool:
    required_fields = {
        "schema_version",
        "id",
        "status",
        "attempt",
        "created_at",
        "started_at",
        "finished_at",
        "request",
        "error",
        "queue_sequence",
    }
    if not required_fields <= record.keys():
        return False
    schema_version = record["schema_version"]
    created_at = record["created_at"]
    started_at = record["started_at"]
    finished_at = record["finished_at"]
    error = record["error"]
    queue_sequence = record["queue_sequence"]
    return (
        isinstance(schema_version, int)
        and not isinstance(schema_version, bool)
        and schema_version >= CURRENT_JOB_SCHEMA_VERSION
        and isinstance(created_at, str)
        and bool(created_at)
        and all(
            value is None or (isinstance(value, str) and bool(value))
            for value in (started_at, finished_at)
        )
        and (
            error is None
            or (
                isinstance(error, dict)
                and isinstance(error.get("code"), str)
                and bool(error["code"])
                and isinstance(error.get("message"), str)
                and bool(error["message"])
            )
        )
        and isinstance(queue_sequence, int)
        and not isinstance(queue_sequence, bool)
        and queue_sequence >= 0
    )


def valid_record(record: JobRecord, *, strict: bool = False) -> bool:
    if strict and not _valid_strict_record_metadata(record):
        return False
    job_id = record.get("id")
    status = record.get("status")
    request = record.get("request")
    if not valid_job_id(job_id):
        return False
    attempt = record.get("attempt")
    if (
        not isinstance(status, str)
        or status not in JOB_STATUSES
        or (
            attempt is not None
            and (
                not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1
            )
        )
    ):
        return False
    if not isinstance(request, dict) or not valid_request(request):
        return False
    for field in (
        "dynamic_terminology_enabled",
        "subtitle_terminology_filter_enabled",
    ):
        if field in request and not isinstance(request[field], bool):
            return False
    term_map = request.get("term_map")
    return (
        (
            "status_history" not in record
            or valid_status_history(
                record["status_history"], status=status, attempt=attempt
            )
        )
        and (
            "output_suffix" not in request
            or (
                isinstance(request["output_suffix"], str)
                and bool(request["output_suffix"])
            )
        )
        and (
            "output_conflict_policy" not in request
            or (
                isinstance(request["output_conflict_policy"], str)
                and request["output_conflict_policy"]
                in {"append-number", "overwrite", "skip"}
            )
        )
        and all(
            field not in request or isinstance(request[field], bool)
            for field in (
                "dynamic_terminology_enabled",
                "subtitle_terminology_filter_enabled",
            )
        )
        and _valid_term_map_snapshot(term_map)
        and _valid_term_map_selection(request, term_map)
    )


def _valid_term_map_snapshot(term_map: object) -> bool:
    if term_map is None:
        return True
    if (
        not isinstance(term_map, dict)
        or not isinstance(term_map.get("id"), str)
        or not term_map["id"]
        or not isinstance(term_map.get("name"), str)
        or not term_map["name"]
        or not isinstance(term_map.get("content"), dict)
    ):
        return False
    try:
        validate_term_map_content(term_map["content"])
    except (TypeError, ServiceError):
        return False
    return True


def _valid_term_map_selection(request: dict[str, object], term_map: object) -> bool:
    mode = request.get("term_map_mode")
    if mode is None:
        return True
    if mode == "none":
        return term_map is None
    if mode == "selected":
        return term_map is not None
    return mode == "follow"


def valid_request(request: dict[str, object]) -> bool:
    required_request_fields = {
        "media_path",
        "target_language_code",
        "output_path",
        "source_format",
    }
    if not required_request_fields <= request.keys() or not all(
        isinstance(request[field], str) and request[field]
        for field in required_request_fields
    ):
        return False
    stream_index = request.get("stream_index")
    subtitle_path = request.get("subtitle_path")
    if stream_index is None:
        return isinstance(subtitle_path, str) and bool(subtitle_path)
    return (
        isinstance(stream_index, int)
        and not isinstance(stream_index, bool)
        and stream_index >= 0
        and subtitle_path is None
    )


def valid_status_history(value: object, *, status: object, attempt: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if (
        not isinstance(status, str)
        or status not in JOB_STATUSES
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt < 1
    ):
        return False

    previous_attempt = 0
    for index, entry in enumerate(value):
        if not isinstance(entry, dict) or not entry.keys() >= STATUS_HISTORY_FIELDS:
            return False
        entry_status = entry["status"]
        entry_attempt = entry["attempt"]
        started_at = entry["started_at"]
        finished_at = entry["finished_at"]
        if (
            not isinstance(entry_status, str)
            or entry_status not in JOB_STATUSES
            or not isinstance(entry_attempt, int)
            or isinstance(entry_attempt, bool)
            or entry_attempt < 1
            or entry_attempt < previous_attempt
            or entry_attempt > attempt
            or not isinstance(started_at, str)
            or not started_at
            or (
                finished_at is not None
                and (not isinstance(finished_at, str) or not finished_at)
            )
            or (index < len(value) - 1 and finished_at is None)
        ):
            return False
        previous_attempt = entry_attempt

    last = value[-1]
    last_finished_at = last["finished_at"]
    return not (
        last["status"] != status
        or last["attempt"] != attempt
        or (status in TERMINAL_JOB_STATUSES) != (last_finished_at is not None)
    )


def transition_status(
    record: JobRecord,
    status: str,
    *,
    attempt: int | None = None,
    at: str,
    terminal: bool = False,
) -> None:
    """Record a new status only for records that already support history."""
    history = record.get("status_history")
    if not isinstance(history, list):
        record["status"] = status
        return
    if (
        history
        and isinstance(history[-1], dict)
        and history[-1].get("finished_at") is None
    ):
        history[-1]["finished_at"] = at
    current_attempt = record.get("attempt", 1) if attempt is None else attempt
    assert isinstance(current_attempt, int) and not isinstance(current_attempt, bool)
    history.append(
        {
            "status": status,
            "attempt": current_attempt,
            "started_at": at,
            "finished_at": at if terminal else None,
        }
    )
    record["status"] = status


def normalize_record(record: JobRecord) -> None:
    record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    record.setdefault("started_at", None)
    record.setdefault("finished_at", None)
    record.setdefault("error", None)
    request = record["request"]
    assert isinstance(request, dict)
    request.setdefault("term_map", None)
    request.setdefault(
        "term_map_mode", "selected" if request["term_map"] is not None else "none"
    )
    request.setdefault("dynamic_terminology_enabled", True)
    request.setdefault("subtitle_terminology_filter_enabled", True)
    request.setdefault("output_suffix", str(request["target_language_code"]))
    request.setdefault("output_conflict_policy", "append-number")
    attempt = record.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        record["attempt"] = 1
    queue_sequence = record.get("queue_sequence")
    if not isinstance(queue_sequence, int) or queue_sequence < 1:
        record["queue_sequence"] = 0


def migrate_record(record: JobRecord) -> tuple[JobRecord | None, bool, bool]:
    """Return the v1 record, whether it was migrated, and whether it is future data."""
    if "schema_version" not in record:
        legacy = True
    else:
        schema_version = record["schema_version"]
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version < CURRENT_JOB_SCHEMA_VERSION
        ):
            return None, False, False
        if schema_version > CURRENT_JOB_SCHEMA_VERSION:
            if not valid_record(record, strict=True):
                return None, False, False
            return None, False, True
        legacy = False

    if not valid_record(record, strict=not legacy):
        return None, False, False
    migrated = copy_job_record(record)
    normalize_record(migrated)
    migrated["schema_version"] = CURRENT_JOB_SCHEMA_VERSION
    return migrated, legacy, False


def queue_sequence(record: Mapping[str, object]) -> int:
    sequence = record.get("queue_sequence")
    return sequence if isinstance(sequence, int) else 0


__all__ = [
    "CURRENT_JOB_SCHEMA_VERSION",
    "JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "JobDetail",
    "JobRecord",
    "JobStatus",
    "JobSummary",
    "copy_job_record",
    "decode_history_cursor",
    "encode_history_cursor",
    "migrate_record",
    "normalize_record",
    "project_job_detail",
    "project_job_summary",
    "queue_sequence",
    "transition_status",
    "valid_job_id",
    "valid_record",
    "valid_request",
    "valid_status_history",
]
