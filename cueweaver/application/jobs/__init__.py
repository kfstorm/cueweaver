"""Durable subtitle translation Jobs."""

from __future__ import annotations

import queue
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, cast
from unicodedata import category

from ...adapters.output import AtomicOutputPublisher
from ...subtitle_formats import EXTERNAL_FORMATS
from ...work import WorkRoot
from ..directory_term_maps import DirectoryTermMaps, DirectoryTermMapState
from ..errors import ServiceError, project_service_error
from ..extraction import Extraction
from ..media import require_readable_media
from ..term_maps import TermMapDetail
from ..translation import Translator
from .execution import (
    EmbeddedExecutionInput,
    JobExecution,
    JobExecutionFinalizationError,
    JobExecutionInput,
    JobExecutionOutcome,
    JobExecutionProgress,
    JobExecutionProgressPersistenceError,
)
from .model import (
    CURRENT_JOB_SCHEMA_VERSION,
    JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobDetail,
    JobRecord,
    JobStatus,
    JobSummary,
    copy_job_record,
    decode_history_cursor,
    encode_history_cursor,
    history_cursor_condition,
    normalize_record,
    project_job_summary,
    queue_sequence,
    transition_status,
    valid_job_id,
    valid_record,
)
from .store import FileJobRecordStore, JobRecordHealth, JobRecordStore

CONTROL_CHARACTER_LIMIT = 32
DELETE_CHARACTER = 127
MAX_HISTORY_PAGE_LIMIT = 100
MAX_HISTORY_SEARCH_LENGTH = 200
APPROVED_ERROR_CONTEXT_KEYS = frozenset(
    {"field", "media_path", "output_path", "path", "stream_index"}
)
OUTPUT_CONFLICT_POLICIES = frozenset({"append-number", "overwrite", "skip"})
OUTPUT_EXISTS_REASON = "Output path already exists"


@dataclass(frozen=True)
class CreateJobRequest:
    media_path: str
    subtitle_path: str | None
    target_language_code: str
    term_map_mode: Literal["follow", "selected", "none"]
    term_map_id: str | None = None
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True
    output_suffix: str | None = None
    output_conflict_policy: Literal["append-number", "overwrite", "skip"] = "skip"
    stream_index: int | None = None
    source_format: str | None = None


class TermMapResolver(Protocol):
    def get(self, term_map_id: str) -> TermMapDetail: ...


class Jobs:
    """Validate, persist, execute, and expose one serial stream of Jobs."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        translator: Translator,
        media_root: Path,
        work_root: Path,
        term_maps: TermMapResolver | None = None,
        extraction: Extraction | None = None,
        directory_term_maps: DirectoryTermMaps | None = None,
        *,
        record_store: JobRecordStore | None = None,
    ) -> None:
        self._translator = translator
        self._term_maps = term_maps
        self._directory_term_maps = directory_term_maps
        self._extraction = extraction
        self._media_root = media_root.resolve()
        self._work = WorkRoot(work_root)
        self._jobs_root = self._work.jobs_directory
        self._record_store = (
            record_store
            if record_store is not None
            else FileJobRecordStore(self._jobs_root)
        )
        self._pending: queue.Queue[str | None] = queue.Queue()
        self._records: dict[str, dict[str, object]] = {}
        self._next_queue_sequence = 0
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._closed = threading.Event()
        self._check_jobs_root()
        self._load_records()
        self._worker = threading.Thread(
            target=self._run, daemon=True, name="cueweaver-job-worker"
        )
        self._worker.start()

    def close(self) -> None:
        """Stop accepting work and signal the worker to exit when it can."""
        with self._lifecycle_lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self._pending.put(None)

    def create(self, request: CreateJobRequest) -> dict[str, object]:
        with self._lifecycle_lock:
            if self._closed.is_set():
                raise ServiceError("worker_unavailable", "Job worker is shutting down")
            media, subtitle, output, source_format = self._validate(request)
            term_map = self._resolve_term_map(request, media)
            if request.output_conflict_policy == "skip" and output.exists():
                return _skipped_result(media, output, self._media_root)
            _require_writable_directory(output.parent)
            if not self._translator.available:
                raise ServiceError(
                    "provider_unavailable",
                    "Translation provider is unavailable; configure a provider and restart CueWeaver",
                )
            self._next_queue_sequence += 1
            job_id = uuid.uuid4().hex
            now = _timestamp()
            job_request: dict[str, object] = {
                "media_path": str(media.relative_to(self._media_root)),
                "target_language_code": request.target_language_code,
                "term_map_mode": request.term_map_mode,
                "term_map": term_map,
                "dynamic_terminology_enabled": request.dynamic_terminology_enabled,
                "subtitle_terminology_filter_enabled": request.subtitle_terminology_filter_enabled,
                "output_suffix": (
                    request.target_language_code
                    if request.output_suffix is None
                    else request.output_suffix
                ),
                "output_conflict_policy": request.output_conflict_policy,
                "output_path": str(output.relative_to(self._media_root)),
                "source_format": source_format,
            }
            if subtitle is not None:
                job_request["subtitle_path"] = str(
                    subtitle.relative_to(self._media_root)
                )
            else:
                assert request.stream_index is not None
                job_request["stream_index"] = request.stream_index
            record: dict[str, object] = {
                "id": job_id,
                "schema_version": CURRENT_JOB_SCHEMA_VERSION,
                "status": "Queued",
                "attempt": 1,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "status_history": [
                    {
                        "status": "Queued",
                        "attempt": 1,
                        "started_at": now,
                        "finished_at": None,
                    }
                ],
                "request": job_request,
                "error": None,
                "queue_sequence": self._next_queue_sequence,
            }
            if subtitle is None:
                record["extraction"] = None
            self._write_record(job_id, record)
            with self._lock:
                self._records[job_id] = record
            self._pending.put(job_id)
            return self._record_with_queue_position(record)

    def create_batch(self, requests: list[CreateJobRequest]) -> list[dict[str, object]]:
        """Queue requests in order while isolating service-level item errors."""
        if not requests:
            raise ServiceError(
                "invalid_request", "Batch must contain at least one item"
            )
        if self._closed.is_set():
            raise ServiceError("worker_unavailable", "Job worker is shutting down")
        if any(
            request.term_map_mode != requests[0].term_map_mode for request in requests
        ):
            raise ServiceError(
                "invalid_term_map_mode",
                "All batch items must use one Term map mode",
                field="term_map_mode",
            )
        parent_directories: set[str] = set()
        item_errors: dict[int, dict[str, object]] = {}
        valid_media: dict[int, Path] = {}
        for index, request in enumerate(requests):
            self._validate_shared_options(request)
            try:
                media = self._media_path(request.media_path, "invalid_media_path")
            except ServiceError as error:
                item_errors[index] = project_service_error(error)
                continue
            valid_media[index] = media
            parent = media.parent.relative_to(self._media_root)
            parent_directories.add("" if str(parent) == "." else parent.as_posix())
        if len(parent_directories) > 1:
            raise ServiceError(
                "invalid_media_path",
                "All batch items must share one parent directory",
                field="items",
            )
        if 0 not in item_errors:
            self._resolve_term_map(requests[0], valid_media[0])

        results: list[dict[str, object]] = []
        for index, request in enumerate(requests):
            if index in item_errors:
                results.append(item_errors[index])
                continue
            try:
                results.append(self.create(request))
            except ServiceError as error:
                results.append(project_service_error(error))
        return results

    def retry(self, job_id: str) -> dict[str, object]:
        """Requeue a failed External subtitle Job without changing its identity."""
        with self._lifecycle_lock:
            with self._lock:
                record = self._records.get(job_id)
                if record is None:
                    raise ServiceError("job_not_found", "Job does not exist")
                status = record.get("status")
                request = record.get("request")
                if (
                    status not in {"Failed", "Interrupted"}
                    or not isinstance(request, dict)
                    or (
                        "subtitle_path" not in request and "stream_index" not in request
                    )
                ):
                    raise ServiceError(
                        "job_retry_conflict",
                        "Only Failed or Interrupted subtitle Jobs can be retried",
                        status=status,
                    )
            if self._closed.is_set():
                raise ServiceError("worker_unavailable", "Job worker is shutting down")
            if not self._translator.available:
                raise ServiceError(
                    "provider_unavailable",
                    "Translation provider is unavailable; configure a provider and restart CueWeaver",
                )
            try:
                self._job_work_directory(job_id)
                if "stream_index" in request:
                    self._validate_retry_media(request)
                else:
                    self._validate_retry_sources(request)
            except ServiceError as error:
                context = self._error_context(error.context)
                safe_error = ServiceError(error.error_code, error.message, **context)
                with self._lock:
                    failed_record = copy_job_record(record)
                    failed_record["error"] = {
                        "code": error.error_code,
                        "message": error.message,
                        **context,
                    }
                    self._write_record(job_id, failed_record)
                    self._records[job_id] = failed_record
                raise safe_error from error
            with self._lock:
                retry_record = copy_job_record(record)
                retry_request = retry_record["request"]
                assert isinstance(retry_request, dict)
                next_queue_sequence = self._next_queue_sequence + 1
                attempt = retry_record.get("attempt", 1)
                assert isinstance(attempt, int)
                next_attempt = attempt + 1
                retry_record["attempt"] = next_attempt
                retry_at = _timestamp()
                transition_status(
                    retry_record,
                    "Queued",
                    attempt=next_attempt,
                    at=retry_at,
                )
                retry_record["started_at"] = None
                retry_record["finished_at"] = None
                retry_record["error"] = None
                retry_request["output_path"] = str(
                    self._base_output_path(retry_request).relative_to(self._media_root)
                )
                retry_record["queue_sequence"] = next_queue_sequence
                self._write_record(job_id, retry_record)
                self._records[job_id] = retry_record
                self._next_queue_sequence = next_queue_sequence
                self._pending.put(job_id)
                return self._record_with_queue_position(retry_record)

    def cancel(self, job_id: str) -> dict[str, object]:
        """Cancel a queued Job while retaining its terminal history record."""
        with self._lifecycle_lock, self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise ServiceError("job_not_found", "Job does not exist")
            status = record.get("status")
            if status != "Queued":
                raise ServiceError(
                    "job_cancel_conflict",
                    "Only Queued Jobs can be cancelled",
                    status=status,
                )
            cancelled_record = copy_job_record(record)
            cancelled_at = _timestamp()
            transition_status(
                cancelled_record, "Cancelled", at=cancelled_at, terminal=True
            )
            cancelled_record["finished_at"] = cancelled_at
            cancelled_record["error"] = None
            self._write_record(job_id, cancelled_record)
            self._records[job_id] = cancelled_record
            return self._record_with_queue_position(cancelled_record)

    def delete(self, job_id: str) -> dict[str, object]:
        """Delete one terminal Job and its residual Work directory."""
        with self._lifecycle_lock:
            with self._lock:
                record = self._records.get(job_id)
                if record is None:
                    raise ServiceError("job_not_found", "Job does not exist")
                status = record.get("status")
                if status not in TERMINAL_JOB_STATUSES:
                    raise ServiceError(
                        "job_delete_conflict",
                        "Only Completed, Failed, Interrupted, or Cancelled Jobs can be deleted",
                        status=status,
                    )
            self._delete_terminal_job(job_id)
            return {"id": job_id, "deleted": True}

    def clear_completed(self) -> dict[str, object]:
        """Delete every Completed Job, retaining records whose cleanup fails."""
        with self._lock:
            job_ids = sorted(
                (
                    str(record["id"])
                    for record in self._records.values()
                    if record.get("status") == "Completed"
                ),
                key=lambda value: value,
            )
        deleted: list[str] = []
        failed: list[dict[str, object]] = []
        for job_id in job_ids:
            with self._lifecycle_lock:
                with self._lock:
                    record = self._records.get(job_id)
                    if record is None or record.get("status") != "Completed":
                        continue
                error = self._attempt_delete_terminal_job(job_id)
            if error is not None:
                context = self._error_context(error.context)
                failed.append(
                    {
                        "id": job_id,
                        "error_code": error.error_code,
                        "message": error.message,
                        **context,
                    }
                )
            else:
                deleted.append(job_id)
        return {"deleted": deleted, "failed": failed}

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            records = [
                self._record_with_queue_position(record)
                for record in self._records.values()
            ]
        return sorted(
            records, key=lambda record: str(record["created_at"]), reverse=True
        )

    def list_page(
        self,
        limit: int = 50,
        cursor: str | None = None,
        search: str = "",
        status: str = "all",
    ) -> dict[str, object]:
        """Return active Jobs and one bounded page of terminal history."""
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_HISTORY_PAGE_LIMIT
        ):
            raise ServiceError(
                "invalid_job_limit", "Job history limit must be between 1 and 100"
            )
        normalized_search = search.strip().casefold()
        if len(normalized_search) > MAX_HISTORY_SEARCH_LENGTH:
            raise ServiceError("invalid_job_search", "Job search is too long")
        if status not in {
            "all",
            "Queued",
            "Extracting",
            "Translating",
            *TERMINAL_JOB_STATUSES,
        }:
            raise ServiceError("invalid_job_status", "Job status filter is invalid")
        condition_hash = history_cursor_condition(normalized_search, status)
        position: tuple[str, str] | None = None
        if cursor is not None:
            try:
                created_at, job_id, cursor_condition_hash = decode_history_cursor(
                    cursor
                )
                if cursor_condition_hash != condition_hash:
                    raise ValueError("Cursor conditions do not match")
                position = (created_at, job_id)
            except ValueError as error:
                raise ServiceError(
                    "invalid_job_cursor", "Job history cursor is invalid"
                ) from error

        with self._lock:
            active_records = [
                record
                for record in self._records.values()
                if record.get("status") in {"Queued", "Extracting", "Translating"}
                and (status == "all" or record.get("status") == status)
                and _job_matches_search(record, normalized_search)
            ]
            active_records.sort(key=_active_sort_key)
            history_records = [
                record
                for record in self._records.values()
                if record.get("status") in TERMINAL_JOB_STATUSES
                and (status == "all" or record.get("status") == status)
                and _job_matches_search(record, normalized_search)
            ]
            history_records.sort(key=_history_sort_key, reverse=True)
            matching_count = len(active_records) + len(history_records)
            if position is not None:
                history_records = [
                    record
                    for record in history_records
                    if _history_sort_key(record) < position
                ]
            page_records = history_records[:limit]
            has_more = len(history_records) > limit
            active_jobs = [
                self._summary_with_queue_position(record) for record in active_records
            ]
            history_jobs = [
                project_job_summary(record, None) for record in page_records
            ]

        next_cursor = None
        if has_more:
            last = page_records[-1]
            last_created_at = last.get("created_at")
            last_job_id = last.get("id")
            if isinstance(last_created_at, str) and isinstance(last_job_id, str):
                next_cursor = encode_history_cursor(
                    last_created_at, last_job_id, condition_hash
                )
        with self._lock:
            completed_count = sum(
                record.get("status") == "Completed" for record in self._records.values()
            )
        return {
            "active_jobs": active_jobs,
            "history_jobs": history_jobs,
            "next_cursor": next_cursor,
            "matching_count": matching_count,
            "completed_count": completed_count,
        }

    def get(self, job_id: str) -> dict[str, object]:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise ServiceError("job_not_found", "Job does not exist")
            return self._record_with_queue_position(record)

    def record_health(self) -> dict[str, object]:
        health = getattr(self._record_store, "health", None)
        if not callable(health):
            return _empty_record_health()
        result = health()
        if not isinstance(result, JobRecordHealth):
            return _empty_record_health()
        return {
            "corrupt": {
                "count": result.corrupt_count,
                "location": result.corrupt_location,
            },
            "unsupported": {
                "count": result.unsupported_count,
                "location": result.unsupported_location,
            },
        }

    def _delete_terminal_job(self, job_id: str) -> None:
        try:
            work_directory = self._job_work_directory(job_id)
        except ServiceError as error:
            context = {"path": f"jobs/{_safe_input_path(job_id)}", **error.context}
            raise ServiceError(
                error.error_code,
                error.message,
                **context,
            ) from error
        try:
            shutil.rmtree(work_directory)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ServiceError(
                "job_work_cleanup_failed",
                "Job Work data could not be cleaned up",
                path=f"jobs/{job_id}",
            ) from error

        try:
            self._remove_record(job_id)
        except OSError as error:
            raise ServiceError(
                "job_record_delete_failed",
                "Job history record could not be deleted",
                path=f"jobs/{job_id}.json",
            ) from error
        with self._lock:
            self._records.pop(job_id, None)

    def _attempt_delete_terminal_job(self, job_id: str) -> ServiceError | None:
        try:
            self._delete_terminal_job(job_id)
        except ServiceError as error:
            return error
        return None

    def _remove_record(self, job_id: str) -> None:
        self._record_store.remove(job_id)

    def _snapshot_term_map(self, term_map_id: str | None) -> dict[str, object] | None:
        if term_map_id is None:
            return None
        if self._term_maps is None:
            raise ServiceError("term_map_not_found", "Term map does not exist")
        detail = self._term_maps.get(term_map_id)
        return {
            "id": detail.id,
            "name": detail.name,
            "content": dict(detail.content),
        }

    def _resolve_term_map(
        self, request: CreateJobRequest, media: Path
    ) -> dict[str, object] | None:
        mode = request.term_map_mode
        if mode not in {"follow", "selected", "none"}:
            raise ServiceError(
                "invalid_term_map_mode",
                "Term map mode must be follow, selected, or none",
                field="term_map_mode",
            )
        if mode in {"follow", "none"}:
            if request.term_map_id is not None:
                raise ServiceError(
                    "invalid_term_map_mode",
                    "Term map ID must be null for follow or none mode",
                    field="term_map_id",
                )
            if mode == "none" or self._directory_term_maps is None:
                return None
            parent = media.parent.relative_to(self._media_root)
            directory = "" if str(parent) == "." else parent.as_posix()
            state: DirectoryTermMapState = self._directory_term_maps.get(directory)
            effective = state.effective
            return self._snapshot_term_map(
                effective.id if effective is not None else None
            )
        if not isinstance(request.term_map_id, str) or not request.term_map_id:
            raise ServiceError(
                "invalid_term_map_mode",
                "Selected mode requires a Term map ID",
                field="term_map_id",
            )
        return self._snapshot_term_map(request.term_map_id)

    def _record_with_queue_position(
        self, record: dict[str, object]
    ) -> dict[str, object]:
        return self._copy_with_queue_position(record)

    def _summary_with_queue_position(
        self, record: dict[str, object]
    ) -> dict[str, object]:
        return self._project_with_queue_position(record, project_job_summary)

    def _project_with_queue_position(
        self,
        record: dict[str, object],
        projector: Callable[[JobRecord, int | None], dict[str, object]],
    ) -> dict[str, object]:
        return projector(record, self._queue_position(record))

    def _copy_with_queue_position(self, record: dict[str, object]) -> dict[str, object]:
        copied = copy_job_record(record)
        copied["queue_position"] = self._queue_position(record)
        return copied

    def _queue_position(self, record: dict[str, object]) -> int | None:
        if record.get("status") != "Queued":
            return None
        queued = sorted(
            (item for item in self._records.values() if item.get("status") == "Queued"),
            key=queue_sequence,
        )
        return next(
            index + 1 for index, item in enumerate(queued) if item["id"] == record["id"]
        )

    def _validate(
        self, request: CreateJobRequest
    ) -> tuple[Path, Path | None, Path, str]:
        self._validate_shared_options(request)
        media = self._media_path(request.media_path, "invalid_media_path")
        require_readable_media(media)
        if request.stream_index is not None:
            if (
                isinstance(request.stream_index, bool)
                or not isinstance(request.stream_index, int)
                or request.stream_index < 0
            ):
                raise ServiceError(
                    "invalid_embedded_subtitle",
                    "Embedded subtitle stream index must be a non-negative integer",
                )
            if request.subtitle_path is not None:
                raise ServiceError(
                    "invalid_embedded_subtitle",
                    "Embedded subtitle Jobs must not provide an Extraction path",
                )
            if request.source_format is None:
                raise ServiceError(
                    "invalid_embedded_subtitle",
                    "Embedded subtitle source format is required",
                )
            source_format = _source_format(request.source_format)
            subtitle = None
        else:
            if request.subtitle_path is None:
                raise ServiceError(
                    "invalid_external_subtitle",
                    "External subtitle path is required",
                )
            if not isinstance(request.subtitle_path, str):
                raise ServiceError(
                    "invalid_external_subtitle",
                    "External subtitle path must be a string",
                )
            subtitle = self._media_path(
                request.subtitle_path, "invalid_external_subtitle"
            )
            if request.source_format is not None:
                raise ServiceError(
                    "invalid_external_subtitle",
                    "External subtitles must not provide a source format",
                )
        if subtitle is not None:
            source_format = self._validate_external_subtitle(media, subtitle)
        output = media.with_name(
            f"{media.stem}.{request.output_suffix or request.target_language_code}.{source_format}"
        )
        return media, subtitle, output, source_format

    def _validate_shared_options(self, request: CreateJobRequest) -> None:
        if (
            not request.target_language_code.strip()
            or "\\" in request.target_language_code
            or any(
                ord(character) < CONTROL_CHARACTER_LIMIT
                or ord(character) == DELETE_CHARACTER
                for character in request.target_language_code
            )
            or Path(request.target_language_code).name != request.target_language_code
        ):
            raise ServiceError(
                "invalid_target_language",
                "Target language must be non-empty and filename-safe",
            )
        output_suffix = (
            request.target_language_code
            if request.output_suffix is None
            else request.output_suffix
        )
        _validate_output_suffix(output_suffix)
        if request.output_conflict_policy not in OUTPUT_CONFLICT_POLICIES:
            raise ServiceError(
                "invalid_output_conflict_policy",
                "Output conflict policy must be append-number, overwrite, or skip",
            )
        if request.term_map_mode not in {"follow", "selected", "none"}:
            raise ServiceError(
                "invalid_term_map_mode",
                "Term map mode must be follow, selected, or none",
                field="term_map_mode",
            )
        if (
            request.term_map_mode in {"follow", "none"}
            and request.term_map_id is not None
        ):
            raise ServiceError(
                "invalid_term_map_mode",
                "Term map ID must be null for follow or none mode",
                field="term_map_id",
            )
        if request.term_map_mode == "selected" and not request.term_map_id:
            raise ServiceError(
                "invalid_term_map_mode",
                "Selected mode requires a Term map ID",
                field="term_map_id",
            )

    def _validate_retry_sources(self, request: dict[str, object]) -> None:
        media_value = request.get("media_path")
        subtitle_value = request.get("subtitle_path")
        assert isinstance(media_value, str)
        assert isinstance(subtitle_value, str)
        media = self._retry_media_path(media_value)
        try:
            subtitle = self._media_path(subtitle_value, "invalid_external_subtitle")
        except ServiceError as error:
            raise ServiceError(
                error.error_code,
                error.message,
                path=_safe_input_path(subtitle_value),
            ) from error
        self._validate_external_subtitle(media, subtitle, path=subtitle_value)

    def _validate_retry_media(self, request: dict[str, object]) -> None:
        media_value = request.get("media_path")
        self._retry_media_path(media_value)

    def _retry_media_path(self, media_value: object) -> Path:
        assert isinstance(media_value, str)
        try:
            media = self._media_path(media_value, "invalid_media_path")
        except ServiceError as error:
            raise ServiceError(
                error.error_code,
                error.message,
                media_path=_safe_input_path(media_value),
            ) from error
        require_readable_media(media)
        return media

    def _validate_external_subtitle(
        self, media: Path, subtitle: Path, *, path: str | None = None
    ) -> str:
        if not subtitle.is_file():
            raise ServiceError(
                "invalid_external_subtitle",
                "External subtitle does not exist",
                **({"path": path} if path is not None else {}),
            )
        try:
            with subtitle.open("rb"):
                pass
        except OSError as error:
            raise ServiceError(
                "invalid_external_subtitle",
                "External subtitle cannot be read",
                **({"path": path} if path is not None else {}),
            ) from error
        if subtitle.parent != media.parent or (
            subtitle.stem != media.stem
            and not subtitle.stem.startswith(f"{media.stem}.")
        ):
            raise ServiceError(
                "invalid_external_subtitle",
                "External subtitle must be beside the Media and share its stem",
                **({"path": path} if path is not None else {}),
            )
        external_format = EXTERNAL_FORMATS.get(subtitle.suffix.casefold())
        if external_format is None:
            raise ServiceError(
                "unsupported_subtitle_format",
                "External subtitle must use a supported extension",
                **({"path": path} if path is not None else {}),
            )
        return external_format

    def _media_path(self, value: object, error_code: str) -> Path:
        if not isinstance(value, str) or not value:
            raise ServiceError(error_code, "Media path must be a non-empty string")
        path = Path(value)
        if (
            "\\" in value
            or "\x00" in value
            or path.is_absolute()
            or any(part == ".." for part in path.parts)
        ):
            raise ServiceError(error_code, "Path must be relative to the Media root")
        resolved = (self._media_root / path).resolve()
        if not resolved.is_relative_to(self._media_root):
            raise ServiceError(error_code, "Path must be inside the Media root")
        return resolved

    def _load_records(self) -> None:
        for loaded_record in self._record_store.load():
            if not valid_record(loaded_record, strict=True):
                continue
            job_id = loaded_record.get("id")
            status = loaded_record.get("status")
            assert isinstance(job_id, str)
            assert isinstance(status, str)
            if status in {"Queued", "Extracting", "Translating"}:
                normalize_record(loaded_record)
                record = _interrupted_record(loaded_record)
                # Recovery is the only startup write. The atomic record replace
                # makes the interrupted state durable before the worker starts.
                self._write_record(job_id, record)
            else:
                record = loaded_record
                normalize_record(record)
            self._next_queue_sequence = max(
                self._next_queue_sequence, queue_sequence(record)
            )
            self._records[job_id] = record

    def _run(self) -> None:
        while True:
            job_id = self._pending.get()
            try:
                if job_id is None:
                    return
                if self._closed.is_set():
                    continue
                self._execute(job_id)
            except Exception as error:
                # A persistence error must not kill the serial worker or strand a Job.
                if job_id is not None:
                    self._mark_failed_after_worker_error(
                        job_id,
                        error,
                        force=isinstance(error, JobExecutionFinalizationError)
                        or self._closed.is_set(),
                    )
            finally:
                self._pending.task_done()

    def _execute(self, job_id: str) -> None:
        prepared = self._prepare_execution(job_id)
        if prepared is None:
            return
        request, embedded, work_directory, record = prepared
        outcome: JobExecutionOutcome
        try:
            work_directory = self._job_work_directory(job_id)

            def finalize(outcome_to_persist: JobExecutionOutcome) -> bool:
                self._finish_execution_locked(
                    job_id, outcome_to_persist, request, embedded, work_directory
                )
                return True

            subtitle_path: Path | None = None
            on_progress: Callable[[JobExecutionProgress], bool] | None = None
            embedded_details: EmbeddedExecutionInput | None = None
            if embedded:
                stream_index = request.get("stream_index")
                if not isinstance(stream_index, int):
                    raise ServiceError(
                        "invalid_stream_index",
                        "Embedded subtitle stream index is invalid",
                    )
                extraction_marker = record.get("extraction")
                embedded_details = EmbeddedExecutionInput(
                    self._media_root / str(request["media_path"]),
                    stream_index,
                    str(request["source_format"]),
                    extraction_marker if isinstance(extraction_marker, dict) else None,
                )

                def persist_progress(progress: JobExecutionProgress) -> bool:
                    continued = self._persist_embedded_progress(job_id, progress)
                    return continued

                on_progress = persist_progress
            else:
                subtitle_path = self._media_root / str(request["subtitle_path"])

            term_map = self._embedded_term_map(request)
            outcome = JobExecution(
                self._translator,
                AtomicOutputPublisher(),
                extraction=self._extraction,
                publication_guard=self._publication_guard,
                should_stop=self._closed.is_set,
                finalize=finalize,
            ).execute(
                JobExecutionInput(
                    subtitle_path=subtitle_path,
                    target_language_code=str(request["target_language_code"]),
                    output_path=self._media_root / str(request["output_path"]),
                    work_directory=work_directory,
                    translation_directory=self._translation_directory(job_id),
                    term_map=term_map,
                    dynamic_terminology_enabled=bool(
                        request.get("dynamic_terminology_enabled", True)
                    ),
                    subtitle_terminology_filter_enabled=bool(
                        request.get("subtitle_terminology_filter_enabled", True)
                    ),
                    overwrite=request.get("output_conflict_policy") == "overwrite",
                    skip_if_exists=request.get("output_conflict_policy") == "skip",
                    embedded=embedded_details,
                ),
                on_progress=on_progress,
            )
        except JobExecutionProgressPersistenceError:
            raise
        except JobExecutionFinalizationError:
            raise
        except ServiceError as error:
            outcome = JobExecutionOutcome("Failed", error=error)
        except Exception:
            outcome = JobExecutionOutcome(
                "Failed",
                error=ServiceError(
                    "extraction_failed" if embedded else "translation_failed",
                    "Extraction failed" if embedded else "Translation failed",
                ),
            )
        if not outcome.terminal_persisted:
            self._finish_execution(job_id, outcome, request, embedded, work_directory)

    @contextmanager
    def _publication_guard(self) -> Iterator[None]:
        with self._lifecycle_lock:
            yield

    def _finish_execution(
        self,
        job_id: str,
        outcome: JobExecutionOutcome,
        request: dict[str, object],
        embedded: bool,
        work_directory: Path,
    ) -> None:
        try:
            with self._lifecycle_lock:
                self._finish_execution_locked(
                    job_id, outcome, request, embedded, work_directory
                )
        except JobExecutionFinalizationError:
            raise
        except Exception as error:
            raise JobExecutionFinalizationError from error

    def _finish_execution_locked(
        self,
        job_id: str,
        outcome: JobExecutionOutcome,
        request: dict[str, object],
        embedded: bool,
        work_directory: Path,
    ) -> None:
        if outcome.status == "Completed":
            self._finish(job_id, "Completed", None)
            return
        if outcome.status == "Interrupted":
            self._finish_interrupted(job_id)
            return
        if self._closed.is_set() and not outcome.preserve_failure:
            self._finish_interrupted(job_id)
            return
        error = outcome.error
        assert error is not None
        context = self._error_context(error.context)
        if embedded:
            context = _embedded_error_context(
                context, request, self._media_root, work_directory
            )
        self._finish(
            job_id,
            "Failed",
            {"code": error.error_code, "message": error.message, **context},
        )

    def _embedded_term_map(self, request: dict[str, object]) -> dict[str, str] | None:
        term_map = request.get("term_map")
        if term_map is None:
            return None
        content = term_map.get("content") if isinstance(term_map, dict) else None
        if not isinstance(content, dict):
            raise ServiceError("invalid_term_map", "Job Term map is invalid")
        return cast(dict[str, str], content)

    def _persist_embedded_progress(
        self, job_id: str, progress: JobExecutionProgress
    ) -> bool:
        with self._lifecycle_lock:
            if self._closed.is_set():
                return False
            with self._lock:
                record = copy_job_record(self._records[job_id])
                source = progress.embedded_subtitle
                record["extraction"] = {
                    "status": "Completed",
                    "path": source.path.name,
                    "format": source.format,
                    "content_digest": source.content_digest,
                }
                transition_status(record, progress.phase, at=_timestamp())
                try:
                    self._write_record(job_id, record)
                except OSError as error:
                    raise JobExecutionProgressPersistenceError from error
                self._records[job_id] = record
        return True

    def _prepare_execution(
        self, job_id: str
    ) -> tuple[dict[str, object], bool, Path, dict[str, object]] | None:
        with self._lifecycle_lock:
            if self._closed.is_set():
                return None
            with self._lock:
                record = self._records.get(job_id)
                if record is None or record.get("status") != "Queued":
                    return None
                request = record["request"]
                assert isinstance(request, dict)
                embedded = "stream_index" in request
                status = "Extracting" if embedded else "Translating"
                started_at = _timestamp()
                transition_status(record, status, at=started_at)
                record["started_at"] = started_at
                output_path = self._execution_output_path(request)
                request["output_path"] = str(output_path.relative_to(self._media_root))
                self._write_record(job_id, record)
                return request, embedded, self._jobs_root / job_id, record

    def _execution_output_path(self, request: dict[str, object]) -> Path:
        output = self._media_path(str(request["output_path"]), "invalid_output_path")
        if request.get("output_conflict_policy", "append-number") in {
            "overwrite",
            "skip",
        }:
            return output
        candidate = output
        number = 2
        while candidate.exists():
            candidate = output.with_name(f"{output.stem}.{number}{output.suffix}")
            number += 1
        return candidate

    def _base_output_path(self, request: dict[str, object]) -> Path:
        media = self._media_path(str(request["media_path"]), "invalid_media_path")
        return media.with_name(
            f"{media.stem}.{request['output_suffix']}.{request['source_format']}"
        )

    def _error_context(self, context: dict[str, object]) -> dict[str, object]:
        roots = (self._media_root, self._jobs_root.parent.resolve())
        safe: dict[str, object] = {}
        for key, value in context.items():
            if key not in APPROVED_ERROR_CONTEXT_KEYS:
                continue
            if not hasattr(value, "__fspath__"):
                safe[key] = value
                continue
            path = Path(value)
            for root in roots:
                try:
                    safe[key] = str(path.resolve().relative_to(root))
                    break
                except ValueError:
                    continue
            else:
                safe[key] = path.name
        return safe

    def _finish(
        self, job_id: str, status: str, error: dict[str, object] | None
    ) -> None:
        with self._lock:
            record = self._records[job_id]
            finished_at = _timestamp()
            transition_status(record, status, at=finished_at, terminal=True)
            record["finished_at"] = finished_at
            record["error"] = error
            self._write_record(job_id, record)

    def _finish_interrupted(self, job_id: str) -> None:
        with self._lock:
            interrupted = _interrupted_record(self._records[job_id])
            self._records[job_id] = interrupted
            # A failed write is recovered on the next startup without blocking shutdown.
            with suppress(OSError):
                self._write_record(job_id, interrupted)

    def _mark_failed_after_worker_error(
        self, job_id: str, error: Exception, *, force: bool = False
    ) -> None:
        with self._lifecycle_lock:
            if self._closed.is_set() and not force:
                return
            with self._lock:
                record = self._records.get(job_id)
                if record is None:
                    return
                finished_at = _timestamp()
                transition_status(record, "Failed", at=finished_at, terminal=True)
                record["finished_at"] = finished_at
                record["error"] = {
                    "code": "job_worker_failed",
                    "message": "Job execution could not be persisted",
                }
                with suppress(Exception):
                    self._write_record(job_id, record)

    def _write_record(self, job_id: str, record: dict[str, object]) -> None:
        record["schema_version"] = CURRENT_JOB_SCHEMA_VERSION
        self._record_store.write(job_id, record)

    def _ensure_jobs_root(self) -> None:
        self._check_jobs_root()
        try:
            self._jobs_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ServiceError(
                "invalid_work_directory",
                "Job Work root cannot be created",
            ) from error

    def _check_jobs_root(self) -> None:
        if self._jobs_root.is_symlink():
            raise ServiceError(
                "invalid_work_directory",
                "Job Work root must not be a symbolic link",
            )

    def _job_work_directory(self, job_id: str) -> Path:
        try:
            return self._work.job_directory(job_id)
        except ValueError as error:
            message = str(error)
            code = (
                "invalid_job_id"
                if message == "Job ID is invalid"
                else "invalid_work_directory"
            )
            raise ServiceError(
                code,
                message,
            ) from error

    def _translation_directory(self, job_id: str) -> Path:
        try:
            return self._work.ensure_translation_directory(job_id)
        except ValueError as error:
            raise ServiceError("invalid_work_directory", str(error)) from error


def _skipped_result(media: Path, output: Path, media_root: Path) -> dict[str, object]:
    return {
        "status": "skipped",
        "media_path": str(media.relative_to(media_root)),
        "output_path": str(output.relative_to(media_root)),
        "reason": OUTPUT_EXISTS_REASON,
    }


def _source_format(value: str | None) -> str:
    if not isinstance(value, str) or value.casefold() not in EXTERNAL_FORMATS.values():
        raise ServiceError(
            "unsupported_subtitle_format",
            "Embedded subtitle format must be srt, ass, or vtt",
        )
    return value.casefold()


def _embedded_error_context(
    context: dict[str, object],
    request: dict[str, object],
    media_root: Path,
    work_directory: Path,
) -> dict[str, object]:
    context = dict(context)
    context["media_path"] = request["media_path"]
    context["stream_index"] = request["stream_index"]
    for key, value in context.items():
        if key == "output_path" and not Path(str(value)).is_absolute():
            context[key] = "Job Work directory"
            continue
        if not isinstance(value, str) or not Path(value).is_absolute():
            continue
        resolved_path = Path(value).resolve()
        if resolved_path.is_relative_to(media_root):
            context[key] = str(resolved_path.relative_to(media_root))
        else:
            context[key] = "Job Work directory"
    return context


def _interrupted_record(record: dict[str, object]) -> dict[str, object]:
    interrupted = copy_job_record(record)
    finished_at = _timestamp()
    transition_status(interrupted, "Interrupted", at=finished_at, terminal=True)
    interrupted["finished_at"] = finished_at
    interrupted["error"] = {
        "code": "job_interrupted",
        "message": "Job was interrupted when CueWeaver stopped",
    }
    return interrupted


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _history_sort_key(record: dict[str, object]) -> tuple[str, str]:
    created_at = record.get("created_at")
    job_id = record.get("id")
    return (
        created_at if isinstance(created_at, str) else "",
        job_id if isinstance(job_id, str) else "",
    )


def _job_matches_search(record: dict[str, object], search: str) -> bool:
    if not search:
        return True
    request = record.get("request")
    values = [record.get("id")]
    if isinstance(request, dict):
        values.extend(
            request.get(field)
            for field in (
                "media_path",
                "subtitle_path",
                "target_language_code",
                "output_path",
            )
        )
    return any(
        isinstance(value, str) and search in value.casefold() for value in values
    )


def _active_sort_key(record: dict[str, object]) -> tuple[int, str, str]:
    created_at, job_id = _history_sort_key(record)
    return queue_sequence(record), created_at, job_id


def _empty_record_health() -> dict[str, object]:
    return {
        "corrupt": {"count": 0, "location": "jobs/corrupt"},
        "unsupported": {"count": 0, "location": "jobs/unsupported"},
    }


def _safe_input_path(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", maxsplit=1)[-1] or "<invalid path>"


def _require_writable_directory(directory: Path) -> None:
    if not directory.is_dir():
        raise ServiceError(
            "output_directory_unwritable", "Media output directory is not writable"
        )
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".cueweaver-check-", delete=True
        ):
            pass
    except OSError as error:
        raise ServiceError(
            "output_directory_unwritable",
            "Media output directory is not writable",
        ) from error


_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def _validate_output_suffix(value: str) -> None:
    if not value:
        raise ServiceError("invalid_output_suffix", "Output suffix must be non-empty")
    for segment in value.split("."):
        if not segment or segment in {".", ".."}:
            raise ServiceError(
                "invalid_output_suffix",
                "Output suffix segments must be non-empty",
            )
        if segment[-1].isspace() or segment[-1] == ".":
            raise ServiceError(
                "invalid_output_suffix",
                "Output suffix segments cannot end in a space or dot",
            )
        if segment.casefold() in _WINDOWS_DEVICE_NAMES:
            raise ServiceError(
                "invalid_output_suffix", "Output suffix contains a reserved name"
            )
        if any(
            category(character).startswith("C")
            or not (
                character.isalnum() or character.isspace() or character in {"-", "_"}
            )
            for character in segment
        ):
            raise ServiceError(
                "invalid_output_suffix",
                "Output suffix contains an unsafe character",
            )


__all__ = [
    "CURRENT_JOB_SCHEMA_VERSION",
    "JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "CreateJobRequest",
    "FileJobRecordStore",
    "JobDetail",
    "JobRecord",
    "JobRecordHealth",
    "JobRecordStore",
    "JobStatus",
    "JobSummary",
    "Jobs",
    "valid_job_id",
]
