"""Job records and projections used by the Jobs application."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

JobStatus = Literal[
    "Queued", "Extracting", "Translating", "Completed", "Failed", "Interrupted"
]
JobRecord = dict[str, object]

JOB_STATUSES = frozenset(
    {"Queued", "Extracting", "Translating", "Completed", "Failed", "Interrupted"}
)
TERMINAL_JOB_STATUSES = frozenset({"Completed", "Failed", "Interrupted"})


@dataclass(frozen=True)
class JobSummary:
    """The current record shape exposed by the application.

    The projection is deliberately independent from the durable record. It
    currently contains the same fields for compatibility; later HTTP slices
    can reduce the summary without changing the persisted execution record.
    """

    record: Mapping[str, object]
    queue_position: int | None

    def to_dict(self) -> JobRecord:
        projected = copy_job_record(self.record)
        projected["queue_position"] = self.queue_position
        return projected


@dataclass(frozen=True)
class JobDetail(JobSummary):
    """A detail projection kept separate from the durable record."""


def project_job_detail(record: JobRecord, queue_position: int | None) -> JobRecord:
    return JobDetail(record, queue_position).to_dict()


def copy_job_record(record: Mapping[str, object]) -> JobRecord:
    copied = json.loads(json.dumps(record))
    if not isinstance(copied, dict):
        raise TypeError("Job record must be an object")
    return copied


def valid_record(record: JobRecord) -> bool:
    job_id = record.get("id")
    status = record.get("status")
    request = record.get("request")
    if not isinstance(job_id, str) or not job_id:
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


def queue_sequence(record: Mapping[str, object]) -> int:
    sequence = record.get("queue_sequence")
    return sequence if isinstance(sequence, int) else 0


__all__ = [
    "JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "JobDetail",
    "JobRecord",
    "JobStatus",
    "JobSummary",
    "copy_job_record",
    "normalize_record",
    "project_job_detail",
    "queue_sequence",
    "valid_record",
    "valid_request",
]
