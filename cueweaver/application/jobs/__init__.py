"""Durable External subtitle translation Jobs."""

from __future__ import annotations

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

from ...adapters.output import AtomicOutputPublisher
from ...subtitle_formats import EXTERNAL_FORMATS
from ..errors import ServiceError
from ..media import require_readable_media
from ..translation import OutputPublisher, TranslateRequest, Translation, Translator

JOB_STATUSES = frozenset(
    {"Queued", "Translating", "Completed", "Failed", "Interrupted"}
)
CONTROL_CHARACTER_LIMIT = 32
DELETE_CHARACTER = 127


@dataclass(frozen=True)
class CreateJobRequest:
    media_path: str
    subtitle_path: str
    target_language_code: str


class Jobs:
    """Validate, persist, execute, and expose one serial stream of Jobs."""

    def __init__(
        self, translator: Translator, media_root: Path, work_root: Path
    ) -> None:
        self._translator = translator
        self._media_root = media_root.resolve()
        self._jobs_root = work_root / "jobs"
        self._pending: queue.Queue[str | None] = queue.Queue()
        self._records: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._closed = threading.Event()
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
            job_id = uuid.uuid4().hex
            now = _timestamp()
            record: dict[str, object] = {
                "id": job_id,
                "status": "Queued",
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "request": {
                    "media_path": str(media.relative_to(self._media_root)),
                    "subtitle_path": str(subtitle.relative_to(self._media_root)),
                    "target_language_code": request.target_language_code,
                    "output_path": str(output.relative_to(self._media_root)),
                    "source_format": source_format,
                },
                "error": None,
            }
            self._write_record(job_id, record)
            with self._lock:
                self._records[job_id] = record
            self._pending.put(job_id)
            return _copy_record(record)

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            records = [_copy_record(record) for record in self._records.values()]
        return sorted(
            records, key=lambda record: str(record["created_at"]), reverse=True
        )

    def get(self, job_id: str) -> dict[str, object]:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise ServiceError("job_not_found", "Job does not exist")
            return _copy_record(record)

    def _validate(self, request: CreateJobRequest) -> tuple[Path, Path, Path, str]:
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
        subtitle = self._media_path(request.subtitle_path, "invalid_external_subtitle")
        require_readable_media(media)
        if not subtitle.is_file():
            raise ServiceError(
                "invalid_external_subtitle", "External subtitle does not exist"
            )
        if subtitle.parent != media.parent or (
            subtitle.stem != media.stem
            and not subtitle.stem.startswith(f"{media.stem}.")
        ):
            raise ServiceError(
                "invalid_external_subtitle",
                "External subtitle must be beside the Media and share its stem",
            )
        source_format = EXTERNAL_FORMATS.get(subtitle.suffix.casefold())
        if source_format is None:
            raise ServiceError(
                "unsupported_subtitle_format",
                "External subtitle must use a supported extension",
            )
        output = media.with_name(
            f"{media.stem}.{request.target_language_code}.{source_format}"
        )
        if output.exists():
            raise ServiceError("output_exists", "Suggested output already exists")
        _require_writable_directory(output.parent)
        return media, subtitle, output, source_format

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
        for record_path in self._jobs_root.glob("*.json"):
            record = _read_record(record_path)
            if record is None or not _valid_record(record):
                continue
            job_id = record.get("id")
            status = record.get("status")
            assert isinstance(job_id, str)
            assert isinstance(status, str)
            if status in {"Queued", "Translating"}:
                interrupted = _interrupted_record(record)
                record.update(interrupted)
                with suppress(OSError):
                    self._write_record(job_id, interrupted)
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
        with self._lifecycle_lock:
            if self._closed.is_set():
                return
            with self._lock:
                record = self._records[job_id]
                record["status"] = "Translating"
                record["started_at"] = _timestamp()
                self._write_record(job_id, record)
                request = record["request"]
        assert isinstance(request, dict)
        work_directory = self._jobs_root / job_id
        try:
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
                    self._media_root / str(request["subtitle_path"]),
                    str(request["target_language_code"]),
                    self._media_root / str(request["output_path"]),
                    work_directory,
                )
            )
        except ServiceError as error:
            self._finish_if_active(
                job_id,
                "Failed",
                {
                    "code": error.error_code,
                    "message": error.message,
                    **_error_context(error.context),
                },
            )
            return
        except Exception:
            self._finish_if_active(
                job_id,
                "Failed",
                {"code": "translation_failed", "message": "Translation failed"},
            )
            return

    def _finish_published(self, job_id: str, work_directory: Path) -> None:
        """Commit the published output while holding the lifecycle lock."""
        try:
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
        self._jobs_root.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            dir=self._jobs_root, prefix=f".{job_id}."
        )
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(record, file, ensure_ascii=True, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self._jobs_root / f"{job_id}.json")
        finally:
            temporary.unlink(missing_ok=True)


def _error_context(context: dict[str, object]) -> dict[str, object]:
    return {
        key: str(value) if hasattr(value, "__fspath__") else value
        for key, value in context.items()
    }


def _interrupted_record(record: dict[str, object]) -> dict[str, object]:
    interrupted = _copy_record(record)
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

    def publish(self, output_path: Path, write: Callable[[Path], None]) -> None:
        with self._lifecycle_lock:
            if self._closed.is_set():
                raise ServiceError(
                    "job_interrupted", "Job was interrupted when CueWeaver stopped"
                )
            self._publisher.publish(output_path, write)
            self._on_published()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _copy_record(record: dict[str, object]) -> dict[str, object]:
    copied = json.loads(json.dumps(record))
    if not isinstance(copied, dict):
        raise TypeError("Job record must be an object")
    return copied


def _read_record(record_path: Path) -> dict[str, object] | None:
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            return None
        return record
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _valid_record(record: dict[str, object]) -> bool:
    job_id = record.get("id")
    status = record.get("status")
    request = record.get("request")
    if not isinstance(job_id, str) or not job_id:
        return False
    if not isinstance(status, str) or status not in JOB_STATUSES:
        return False
    if not isinstance(request, dict):
        return False
    required_request_fields = {
        "media_path",
        "subtitle_path",
        "target_language_code",
        "output_path",
        "source_format",
    }
    return required_request_fields <= request.keys() and all(
        isinstance(request[field], str) and request[field]
        for field in required_request_fields
    )


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


__all__ = ["CreateJobRequest", "Jobs"]
