"""Job records and projections used by the Jobs application."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
        fields += ("extraction",)
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


def encode_history_cursor(created_at: str, job_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at, "id": job_id},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_history_cursor(cursor: str) -> tuple[str, str]:
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
        or set(payload) != {"created_at", "id"}
        or not isinstance(payload["created_at"], str)
        or not payload["created_at"]
        or not isinstance(payload["id"], str)
        or not payload["id"]
    ):
        raise ValueError("Invalid history cursor")
    return payload["created_at"], payload["id"]


def copy_job_record(record: Mapping[str, object]) -> JobRecord:
    copied = json.loads(json.dumps(record))
    if not isinstance(copied, dict):
        raise TypeError("Job record must be an object")
    return copied


def valid_job_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and "\\" not in value
        and "\x00" not in value
        and not Path(value).is_absolute()
        and Path(value).name == value
    )


def valid_record(record: JobRecord) -> bool:
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
    if not isinstance(request, dict):
        return False
    if not valid_request(request):
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
                and request["output_conflict_policy"] in {"append-number", "overwrite"}
            )
        )
        and all(
            field not in request or isinstance(request[field], bool)
            for field in (
                "dynamic_terminology_enabled",
                "subtitle_terminology_filter_enabled",
            )
        )
        and (
            term_map is None
            or (
                isinstance(term_map, dict)
                and isinstance(term_map.get("id"), str)
                and bool(term_map["id"])
                and isinstance(term_map.get("name"), str)
                and bool(term_map["name"])
                and isinstance(term_map.get("content"), dict)
                and all(
                    isinstance(source, str)
                    and bool(source)
                    and isinstance(target, str)
                    and bool(target)
                    for source, target in term_map["content"].items()
                )
            )
        )
    )


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


def normalize_record(record: JobRecord) -> None:
    request = record["request"]
    assert isinstance(request, dict)
    request.setdefault("term_map", None)
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
            return None, False, True
        legacy = False

    if not valid_record(record):
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
    "valid_job_id",
    "valid_record",
    "valid_request",
]
