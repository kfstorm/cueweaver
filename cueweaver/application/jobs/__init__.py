"""Durable subtitle translation Jobs."""

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
from typing import Literal, Protocol
from unicodedata import category

from ...adapters.output import AtomicOutputPublisher
from ...subtitle_formats import EXTERNAL_FORMATS
from ..errors import ServiceError
from ..extraction import Extraction, ExtractRequest
from ..media import require_readable_media
from ..term_maps import TermMapDetail
from ..translation import OutputPublisher, TranslateRequest, Translation, Translator

JOB_STATUSES = frozenset(
    {"Queued", "Extracting", "Translating", "Completed", "Failed", "Interrupted"}
)
CONTROL_CHARACTER_LIMIT = 32
DELETE_CHARACTER = 127
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

    def __init__(
        self,
        translator: Translator,
        media_root: Path,
        work_root: Path,
        term_maps: TermMapResolver | None = None,
        extraction: Extraction | None = None,
    ) -> None:
        self._translator = translator
        self._term_maps = term_maps
        self._extraction = extraction
        self._media_root = media_root.resolve()
        self._jobs_root = work_root / "jobs"
        self._pending: queue.Queue[str | None] = queue.Queue()
        self._records: dict[str, dict[str, object]] = {}
        self._next_queue_sequence = 0
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
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "request": job_request,
                "error": None,
                "queue_sequence": self._next_queue_sequence,
            }
            self._write_record(job_id, record)
            with self._lock:
                self._records[job_id] = record
            self._pending.put(job_id)
            return self._record_with_queue_position(record)

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            records = [
                self._record_with_queue_position(record)
                for record in self._records.values()
            ]
        return sorted(
            records, key=lambda record: str(record["created_at"]), reverse=True
        )

    def get(self, job_id: str) -> dict[str, object]:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise ServiceError("job_not_found", "Job does not exist")
            return self._record_with_queue_position(record)

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
        copied = _copy_record(record)
        if copied.get("status") != "Queued":
            copied["queue_position"] = None
            return copied
        queued = sorted(
            (item for item in self._records.values() if item.get("status") == "Queued"),
            key=_queue_sequence,
        )
        copied["queue_position"] = next(
            index + 1 for index, item in enumerate(queued) if item["id"] == record["id"]
        )
        return copied

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
            external_format = EXTERNAL_FORMATS.get(subtitle.suffix.casefold())
            if external_format is None:
                raise ServiceError(
                    "unsupported_subtitle_format",
                    "External subtitle must use a supported extension",
                )
            source_format = external_format
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
            if status in {"Queued", "Extracting", "Translating"}:
                interrupted = _interrupted_record(record)
                record.update(interrupted)
                with suppress(OSError):
                    self._write_record(job_id, interrupted)
            _normalize_record(record)
            self._next_queue_sequence = max(
                self._next_queue_sequence, _queue_sequence(record)
            )
            with suppress(OSError):
                self._write_record(job_id, record)
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
                request = record["request"]
                assert isinstance(request, dict)
                embedded = "stream_index" in request
                record["status"] = "Extracting" if embedded else "Translating"
                record["started_at"] = _timestamp()
                output_path = self._execution_output_path(request)
                request["output_path"] = str(output_path.relative_to(self._media_root))
                self._write_record(job_id, record)
        assert isinstance(request, dict)
        work_directory = self._jobs_root / job_id
        extracting = "stream_index" in request
        try:
            if extracting:
                if self._extraction is None:
                    raise ServiceError(
                        "extraction_unavailable",
                        "Embedded subtitle Extraction is unavailable",
                    )
                source_format = str(request["source_format"])
                subtitle_path = work_directory / f"source.{source_format}"
                self._extraction.extract(
                    ExtractRequest(
                        self._media_root / str(request["media_path"]),
                        int(request["stream_index"]),
                        subtitle_path,
                    )
                )
                extracting = False
                with self._lifecycle_lock:
                    if self._closed.is_set():
                        return
                    with self._lock:
                        record = self._records[job_id]
                        record["status"] = "Translating"
                        self._write_record(job_id, record)
            else:
                subtitle_path = self._media_root / str(request["subtitle_path"])
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
    if not _valid_request(request):
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


def _valid_request(request: dict[str, object]) -> bool:
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


def _normalize_record(record: dict[str, object]) -> None:
    request = record["request"]
    assert isinstance(request, dict)
    request.setdefault("term_map", None)
    request.setdefault("dynamic_terminology_enabled", True)
    request.setdefault("subtitle_terminology_filter_enabled", True)
    request.setdefault("output_suffix", str(request["target_language_code"]))
    request.setdefault("output_conflict_policy", "append-number")
    queue_sequence = record.get("queue_sequence")
    if not isinstance(queue_sequence, int) or queue_sequence < 1:
        record["queue_sequence"] = 0


def _queue_sequence(record: dict[str, object]) -> int:
    sequence = record.get("queue_sequence")
    return sequence if isinstance(sequence, int) else 0


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


__all__ = ["CreateJobRequest", "Jobs"]
