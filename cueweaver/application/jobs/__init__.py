"""Durable subtitle translation Jobs."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol
from unicodedata import category

from ...adapters.output import AtomicOutputPublisher
from ...subtitle_formats import EXTERNAL_FORMATS
from ..errors import ServiceError
from ..extraction import Extraction, ExtractRequest
from ..media import require_readable_media
from ..term_maps import TermMapDetail
from ..translation import OutputPublisher, TranslateRequest, Translation, Translator
from .model import (
    JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobDetail,
    JobRecord,
    JobStatus,
    JobSummary,
    copy_job_record,
    decode_history_cursor,
    encode_history_cursor,
    normalize_record,
    project_job_summary,
    queue_sequence,
    valid_record,
)
from .store import FileJobRecordStore, JobRecordStore

CONTROL_CHARACTER_LIMIT = 32
DELETE_CHARACTER = 127
MAX_HISTORY_PAGE_LIMIT = 100
APPROVED_ERROR_CONTEXT_KEYS = frozenset(
    {"field", "media_path", "output_path", "path", "stream_index"}
)


@dataclass(frozen=True)
class CreateJobRequest:
    media_path: str
    subtitle_path: str | None
    target_language_code: str
    term_map_id: str | None = None
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True
    output_suffix: str | None = None
    output_conflict_policy: Literal["append-number", "overwrite"] = "append-number"
    stream_index: int | None = None
    source_format: str | None = None


class TermMapResolver(Protocol):
    def get(self, term_map_id: str) -> TermMapDetail: ...


class Jobs:
    """Validate, persist, execute, and expose one serial stream of Jobs."""

    def __init__(  # noqa: PLR0913
        self,
        translator: Translator,
        media_root: Path,
        work_root: Path,
        term_maps: TermMapResolver | None = None,
        extraction: Extraction | None = None,
        *,
        record_store: JobRecordStore | None = None,
    ) -> None:
        self._translator = translator
        self._term_maps = term_maps
        self._extraction = extraction
        self._media_root = media_root.resolve()
        self._work_root = work_root.resolve()
        self._jobs_root = self._work_root / "jobs"
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
            if not self._translator.available:
                raise ServiceError(
                    "provider_unavailable",
                    "Translation provider is unavailable; configure a provider and restart CueWeaver",
                )
            media, subtitle, output, source_format = self._validate(request)
            term_map = self._snapshot_term_map(request.term_map_id)
            self._next_queue_sequence += 1
            job_id = uuid.uuid4().hex
            now = _timestamp()
            job_request: dict[str, object] = {
                "media_path": str(media.relative_to(self._media_root)),
                "target_language_code": request.target_language_code,
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
                "status": "Queued",
                "attempt": 1,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
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
                retry_record["status"] = "Queued"
                attempt = retry_record.get("attempt", 1)
                assert isinstance(attempt, int)
                retry_record["attempt"] = attempt + 1
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
            cancelled_record["status"] = "Cancelled"
            cancelled_record["finished_at"] = _timestamp()
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
        self, limit: int = 50, cursor: str | None = None
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
        position: tuple[str, str] | None = None
        if cursor is not None:
            try:
                position = decode_history_cursor(cursor)
            except ValueError as error:
                raise ServiceError(
                    "invalid_job_cursor", "Job history cursor is invalid"
                ) from error

        with self._lock:
            active_records = [
                record
                for record in self._records.values()
                if record.get("status") in {"Queued", "Extracting", "Translating"}
            ]
            active_records.sort(key=_active_sort_key)
            history_records = [
                record
                for record in self._records.values()
                if record.get("status") in TERMINAL_JOB_STATUSES
            ]
            history_records.sort(key=_history_sort_key, reverse=True)
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
            created_at = last.get("created_at")
            job_id = last.get("id")
            if isinstance(created_at, str) and isinstance(job_id, str):
                next_cursor = encode_history_cursor(created_at, job_id)
        return {
            "active_jobs": active_jobs,
            "history_jobs": history_jobs,
            "next_cursor": next_cursor,
        }

    def get(self, job_id: str) -> dict[str, object]:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise ServiceError("job_not_found", "Job does not exist")
            return self._record_with_queue_position(record)

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
            source_format = _source_format(request.source_format)
            subtitle = None
        else:
            if request.subtitle_path is None:
                raise ServiceError(
                    "invalid_external_subtitle",
                    "External subtitle path is required",
                )
            subtitle = self._media_path(
                request.subtitle_path, "invalid_external_subtitle"
            )
        if subtitle is not None:
            source_format = self._validate_external_subtitle(media, subtitle)
        output_suffix = (
            request.target_language_code
            if request.output_suffix is None
            else request.output_suffix
        )
        _validate_output_suffix(output_suffix)
        if request.output_conflict_policy not in {"append-number", "overwrite"}:
            raise ServiceError(
                "invalid_output_conflict_policy",
                "Output conflict policy must be append-number or overwrite",
            )
        output = media.with_name(f"{media.stem}.{output_suffix}.{source_format}")
        _require_writable_directory(output.parent)
        return media, subtitle, output, source_format

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

    def _media_path(self, value: str, error_code: str) -> Path:
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
            if not valid_record(loaded_record):
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
                    self._mark_failed_after_worker_error(job_id, error)
            finally:
                self._pending.task_done()

    def _execute(self, job_id: str) -> None:
        prepared = self._prepare_execution(job_id)
        if prepared is None:
            return
        request, embedded, work_directory, record = prepared
        subtitle_path: Path | None = None
        extracting = embedded
        try:
            work_directory = self._job_work_directory(job_id)
            if embedded:
                subtitle_path, should_stop = self._reuse_extracted_source(
                    job_id, record, work_directory
                )
                if should_stop:
                    return
                extracting = subtitle_path is None
            if extracting:
                subtitle_path = self._extract_embedded_source(
                    job_id, request, work_directory
                )
                if subtitle_path is None:
                    return
                extracting = False
                with self._lifecycle_lock:
                    if self._closed.is_set():
                        return
                    with self._lock:
                        record = self._records[job_id]
                        record["status"] = "Translating"
                        self._write_record(job_id, record)
            if not embedded:
                subtitle_path = self._media_root / str(request["subtitle_path"])
            assert subtitle_path is not None
            term_map_path: Path | None = None
            term_map = request.get("term_map")
            if isinstance(term_map, dict):
                content = term_map.get("content")
                if not isinstance(content, dict):
                    raise ServiceError("invalid_term_map", "Job Term map is invalid")
                work_directory.mkdir(parents=True, exist_ok=True)
                term_map_path = work_directory / "term-map.json"
                term_map_path.write_text(
                    json.dumps(content, ensure_ascii=False), encoding="utf-8"
                )
            Translation(
                self._translator,
                _JobOutputPublisher(
                    AtomicOutputPublisher(),
                    self._lifecycle_lock,
                    self._closed,
                    lambda: self._finish_published(job_id, work_directory),
                ),
            ).translate(
                TranslateRequest(
                    subtitle_path,
                    str(request["target_language_code"]),
                    self._media_root / str(request["output_path"]),
                    work_directory,
                    term_map_path,
                    bool(request.get("dynamic_terminology_enabled", True)),
                    bool(request.get("subtitle_terminology_filter_enabled", True)),
                    request.get("output_conflict_policy") == "overwrite",
                )
            )
        except ServiceError as error:
            context = self._error_context(error.context)
            if embedded:
                context = _embedded_error_context(
                    context, request, self._media_root, work_directory
                )
            self._finish_if_active(
                job_id,
                "Failed",
                {
                    "code": error.error_code,
                    "message": error.message,
                    **context,
                },
            )
            return

        except Exception:
            self._finish_if_active(
                job_id,
                "Failed",
                {
                    "code": "extraction_failed" if extracting else "translation_failed",
                    "message": "Extraction failed"
                    if extracting
                    else "Translation failed",
                    **(
                        _embedded_error_context(
                            {}, request, self._media_root, work_directory
                        )
                        if embedded
                        else {}
                    ),
                },
            )
            return

    def _prepare_execution(
        self, job_id: str
    ) -> tuple[dict[str, object], bool, Path, dict[str, object]] | None:
        with self._lifecycle_lock:
            if self._closed.is_set():
                return None
            with self._lock:
                record = self._records[job_id]
                if record.get("status") != "Queued":
                    return None
                request = record["request"]
                assert isinstance(request, dict)
                embedded = "stream_index" in request
                record["status"] = "Extracting" if embedded else "Translating"
                record["started_at"] = _timestamp()
                output_path = self._execution_output_path(request)
                request["output_path"] = str(output_path.relative_to(self._media_root))
                self._write_record(job_id, record)
                return request, embedded, self._jobs_root / job_id, record

    def _execution_output_path(self, request: dict[str, object]) -> Path:
        output = self._media_path(str(request["output_path"]), "invalid_output_path")
        if request.get("output_conflict_policy", "append-number") == "overwrite":
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

    def _verified_extracted_source(
        self, record: dict[str, object], work_directory: Path
    ) -> Path | None:
        request = record.get("request")
        marker = record.get("extraction")
        if not isinstance(request, dict) or not isinstance(marker, dict):
            return None
        source_format = request.get("source_format")
        if (
            not isinstance(source_format, str)
            or source_format not in EXTERNAL_FORMATS.values()
            or marker.get("status") != "Completed"
            or marker.get("path") != f"source.{source_format}"
            or marker.get("format") != source_format
            or not isinstance(marker.get("content_digest"), str)
        ):
            return None
        source = work_directory / f"source.{source_format}"
        try:
            if (
                source.is_symlink()
                or not source.is_file()
                or _content_digest(source) != marker["content_digest"]
            ):
                return None
        except OSError:
            return None
        return source

    def _reuse_extracted_source(
        self,
        job_id: str,
        record: dict[str, object],
        work_directory: Path,
    ) -> tuple[Path | None, bool]:
        source = self._verified_extracted_source(record, work_directory)
        if source is None:
            return None, False
        with self._lifecycle_lock:
            if self._closed.is_set():
                return None, True
            with self._lock:
                current_record = self._records[job_id]
                current_record["status"] = "Translating"
                self._write_record(job_id, current_record)
        return source, False

    def _extract_embedded_source(
        self,
        job_id: str,
        request: dict[str, object],
        work_directory: Path,
    ) -> Path | None:
        if self._extraction is None:
            raise ServiceError(
                "extraction_unavailable",
                "Embedded subtitle Extraction is unavailable",
            )
        source_format = str(request["source_format"])
        stream_index = request["stream_index"]
        assert isinstance(stream_index, int)
        subtitle_path = work_directory / f"source.{source_format}"
        work_directory.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            dir=work_directory,
            prefix=f".{subtitle_path.name}.retry.",
            suffix=subtitle_path.suffix,
        )
        os.close(descriptor)
        candidate_path = Path(raw_path)
        candidate_path.unlink()
        try:
            self._extraction.extract(
                ExtractRequest(
                    self._media_root / str(request["media_path"]),
                    stream_index,
                    candidate_path,
                )
            )
            _replace_extracted_source(candidate_path, subtitle_path)
            digest = _content_digest(subtitle_path)
        finally:
            candidate_path.unlink(missing_ok=True)
        with self._lifecycle_lock:
            if self._closed.is_set():
                return None
            with self._lock:
                record = self._records[job_id]
                record["extraction"] = {
                    "status": "Completed",
                    "path": subtitle_path.name,
                    "format": source_format,
                    "content_digest": digest,
                }
                self._write_record(job_id, record)
        return subtitle_path

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

    def _finish_published(self, job_id: str, work_directory: Path) -> None:
        """Commit the published output while holding the lifecycle lock."""
        try:
            self._job_work_directory(job_id)
            shutil.rmtree(work_directory)
        except OSError:
            self._finish(
                job_id,
                "Failed",
                {
                    "code": "work_cleanup_failed",
                    "message": "Completed Job work data could not be cleaned up",
                },
            )
            return
        self._finish(job_id, "Completed", None)

    def _finish(
        self, job_id: str, status: str, error: dict[str, object] | None
    ) -> None:
        with self._lock:
            record = self._records[job_id]
            record["status"] = status
            record["finished_at"] = _timestamp()
            record["error"] = error
            self._write_record(job_id, record)

    def _finish_if_active(
        self, job_id: str, status: str, error: dict[str, object] | None
    ) -> None:
        with self._lifecycle_lock:
            if not self._closed.is_set():
                self._finish(job_id, status, error)

    def _mark_failed_after_worker_error(self, job_id: str, error: Exception) -> None:
        with self._lifecycle_lock:
            if self._closed.is_set():
                return
            with self._lock:
                record = self._records.get(job_id)
                if record is None:
                    return
                record["status"] = "Failed"
                record["finished_at"] = _timestamp()
                record["error"] = {
                    "code": "job_worker_failed",
                    "message": "Job execution could not be persisted",
                }
                with suppress(OSError):
                    self._write_record(job_id, record)

    def _write_record(self, job_id: str, record: dict[str, object]) -> None:
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
        if not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
            raise ServiceError("invalid_job_id", "Job ID is invalid")
        self._ensure_jobs_root()
        work_directory = self._jobs_root / job_id
        if work_directory.is_symlink():
            raise ServiceError(
                "invalid_work_directory",
                "Job Work directory must not be a symbolic link",
            )
        try:
            resolved = work_directory.resolve()
        except OSError as error:
            raise ServiceError(
                "invalid_work_directory",
                "Job Work directory cannot be resolved",
            ) from error
        if not resolved.is_relative_to(self._work_root):
            raise ServiceError(
                "invalid_work_directory",
                "Job Work directory must remain inside the Work root",
            )
        return work_directory


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
    interrupted["status"] = "Interrupted"
    interrupted["finished_at"] = _timestamp()
    interrupted["error"] = {
        "code": "job_interrupted",
        "message": "Job was interrupted when CueWeaver stopped",
    }
    return interrupted


class _JobOutputPublisher:
    def __init__(
        self,
        publisher: OutputPublisher,
        lifecycle_lock: threading.Lock,
        closed: threading.Event,
        on_published: Callable[[], None],
    ) -> None:
        self._publisher = publisher
        self._lifecycle_lock = lifecycle_lock
        self._closed = closed
        self._on_published = on_published

    def publish(
        self,
        output_path: Path,
        write: Callable[[Path], None],
        *,
        overwrite: bool = False,
    ) -> None:
        with self._lifecycle_lock:
            if self._closed.is_set():
                raise ServiceError(
                    "job_interrupted", "Job was interrupted when CueWeaver stopped"
                )
            self._publisher.publish(output_path, write, overwrite=overwrite)
            self._on_published()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _history_sort_key(record: dict[str, object]) -> tuple[str, str]:
    created_at = record.get("created_at")
    job_id = record.get("id")
    return (
        created_at if isinstance(created_at, str) else "",
        job_id if isinstance(job_id, str) else "",
    )


def _active_sort_key(record: dict[str, object]) -> tuple[int, str, str]:
    created_at, job_id = _history_sort_key(record)
    return queue_sequence(record), created_at, job_id


def _replace_extracted_source(candidate: Path, destination: Path) -> None:
    diagnostic: Path | None = None
    if destination.is_dir():
        descriptor, raw_path = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f"{destination.name}.invalid.",
        )
        os.close(descriptor)
        diagnostic = Path(raw_path)
        diagnostic.unlink()
        destination.replace(diagnostic)
    try:
        candidate.replace(destination)
    except OSError:
        if diagnostic is not None:
            diagnostic.replace(destination)
        raise
    _fsync_directory(destination.parent)


def _content_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_input_path(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", maxsplit=1)[-1] or "<invalid path>"


def _fsync_directory(directory: Path) -> None:
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


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
    "JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "CreateJobRequest",
    "FileJobRecordStore",
    "JobDetail",
    "JobRecord",
    "JobRecordStore",
    "JobStatus",
    "JobSummary",
    "Jobs",
]
