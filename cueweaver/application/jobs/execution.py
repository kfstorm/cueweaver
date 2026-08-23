"""Execution of one Job's subtitle workflow."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkstemp
from typing import Literal

from ...subtitle_formats import EXTERNAL_FORMATS
from ..errors import ServiceError
from ..extraction import Extraction, ExtractRequest
from ..output import OutputPublisher
from ..term_maps import validate_term_map_content
from ..translation import (
    TranslateRequest,
    TranslateResult,
    Translation,
    Translator,
)


@dataclass(frozen=True)
class EmbeddedExecutionInput:
    media_path: Path
    stream_index: int
    source_format: str
    extraction_marker: Mapping[str, object] | None = None


@dataclass(frozen=True)
class JobExecutionInput:
    """The read-only inputs needed to translate one subtitle for a Job."""

    subtitle_path: Path | None
    target_language_code: str
    output_path: Path
    work_directory: Path
    translation_directory: Path | None = None
    term_map: Mapping[str, str] | None = None
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True
    overwrite: bool = False
    skip_if_exists: bool = False
    embedded: EmbeddedExecutionInput | None = None


@dataclass(frozen=True)
class ExtractedEmbeddedSubtitle:
    path: Path
    format: str
    content_digest: str


@dataclass(frozen=True)
class JobExecutionProgress:
    phase: Literal["Translating"]
    embedded_subtitle: ExtractedEmbeddedSubtitle
    reused: bool


@dataclass(frozen=True)
class JobExecutionOutcome:
    status: Literal["Completed", "Failed", "Interrupted"]
    result: TranslateResult | None = None
    error: ServiceError | None = None
    preserve_failure: bool = False
    terminal_persisted: bool = False
    output_digest: str | None = None
    cleanup_pending: bool = False


class JobExecutionProgressPersistenceError(Exception):
    """A progress callback failed while persisting lifecycle state."""


class JobExecutionFinalizationError(Exception):
    """The lifecycle owner could not persist a terminal execution outcome."""


class _PublicationFailureError(Exception):
    def __init__(self, error: ServiceError) -> None:
        super().__init__(error.message)
        self.error = error


class JobExecution:
    """Run one Job's execution steps behind a synchronous interface."""

    def __init__(  # noqa: PLR0913
        self,
        translator: Translator,
        output: OutputPublisher,
        *,
        extraction: Extraction | None = None,
        publication_guard: Callable[[], AbstractContextManager[None]] | None = None,
        before_publication: Callable[[], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        finalize: Callable[[JobExecutionOutcome], bool] | None = None,
    ) -> None:
        self._translator = translator
        self._output = output
        self._extraction = extraction
        self._publication_guard = publication_guard or nullcontext
        self._before_publication_callback = before_publication
        self._should_stop = should_stop or (lambda: False)
        self._finalize = finalize
        self._output_digest: str | None = None
        self._cleanup_pending = False

    def _before_publication(self) -> None:
        self._check_publication_allowed()
        if self._before_publication_callback is not None:
            self._before_publication_callback()

    def execute(
        self,
        execution_input: JobExecutionInput,
        *,
        on_progress: Callable[[JobExecutionProgress], bool] | None = None,
    ) -> JobExecutionOutcome:
        subtitle_path = execution_input.subtitle_path
        extracting = execution_input.embedded is not None
        outcome: JobExecutionOutcome | None = None
        publication_finalized = False
        self._output_digest = None
        self._cleanup_pending = False

        def finalize(outcome_to_persist: JobExecutionOutcome) -> bool:
            nonlocal publication_finalized
            if self._finalize is None:
                return False
            publication_finalized = self._finalize(outcome_to_persist)
            return publication_finalized

        try:
            if execution_input.skip_if_exists and execution_input.output_path.exists():
                raise ServiceError(
                    "output_exists",
                    "Output path already exists",
                    path=execution_input.output_path,
                )
            if execution_input.embedded is not None:
                source = self._prepare_embedded_source(execution_input)
                if source is None or (
                    on_progress is not None
                    and not on_progress(
                        JobExecutionProgress(
                            "Translating", source.subtitle, source.reused
                        )
                    )
                ):
                    outcome = _interrupted_outcome()
                else:
                    subtitle_path = source.subtitle.path
                    extracting = False
            if outcome is None:
                if subtitle_path is None:
                    raise ServiceError(
                        "invalid_job_execution",
                        "Job execution requires a subtitle path",
                    )
                result = self._translate(
                    execution_input,
                    subtitle_path,
                    finalize=finalize,
                )
                outcome = JobExecutionOutcome(
                    "Completed",
                    result=result,
                    terminal_persisted=publication_finalized,
                    output_digest=self._output_digest,
                    cleanup_pending=self._cleanup_pending,
                )
        except JobExecutionProgressPersistenceError:
            raise
        except JobExecutionFinalizationError:
            raise
        except _PublicationFailureError as error:
            outcome = JobExecutionOutcome(
                "Failed",
                error=error.error,
                preserve_failure=True,
                terminal_persisted=publication_finalized,
            )
        except _ExecutionInterruptedError:
            outcome = _interrupted_outcome()
        except ServiceError as error:
            outcome = self._outcome_for_error(error)
        except Exception:
            outcome = self._outcome_for_error(
                ServiceError(
                    "extraction_failed" if extracting else "translation_failed",
                    "Extraction failed" if extracting else "Translation failed",
                )
            )
        assert outcome is not None
        return outcome

    def _outcome_for_error(self, error: ServiceError) -> JobExecutionOutcome:
        if self._should_stop():
            return _interrupted_outcome()
        return JobExecutionOutcome("Failed", error=error)

    def _translate(
        self,
        execution_input: JobExecutionInput,
        subtitle_path: Path,
        *,
        finalize: Callable[[JobExecutionOutcome], bool],
    ) -> TranslateResult:
        term_map_path = _write_term_map(
            execution_input.work_directory, execution_input.term_map
        )
        result = Translation(
            self._translator,
            self._output,
            publication_guard=self._publication_guard,
            before_publication=self._before_publication,
            on_publication_failure=lambda error: self._publication_failed(
                finalize, error
            ),
            after_publication=lambda: self._publication_succeeded(
                execution_input.work_directory, execution_input.output_path, finalize
            ),
        ).translate(
            TranslateRequest(
                subtitle_path=subtitle_path,
                target_language_code=execution_input.target_language_code,
                output_path=execution_input.output_path,
                work_directory=(
                    execution_input.translation_directory
                    or execution_input.work_directory
                ),
                term_map_path=term_map_path,
                dynamic_terminology_enabled=execution_input.dynamic_terminology_enabled,
                subtitle_terminology_filter_enabled=(
                    execution_input.subtitle_terminology_filter_enabled
                ),
                overwrite=execution_input.overwrite,
            )
        )
        return result

    def _check_publication_allowed(self) -> None:
        if self._should_stop():
            raise _ExecutionInterruptedError

    def _publication_failed(
        self, finalize: Callable[[JobExecutionOutcome], bool], error: Exception
    ) -> None:
        failure = (
            error
            if isinstance(error, ServiceError)
            else ServiceError("output_write_failed", "Output could not be published")
        )
        self._finalize_failure(finalize, failure)
        raise _PublicationFailureError(failure) from error

    def _publication_succeeded(
        self,
        work_directory: Path,
        output_path: Path,
        finalize: Callable[[JobExecutionOutcome], bool],
    ) -> None:
        output_digest = _content_digest(output_path)
        cleanup_pending = False
        try:
            shutil.rmtree(work_directory)
        except OSError:
            cleanup_pending = True
        self._output_digest = output_digest
        self._cleanup_pending = cleanup_pending
        self._finalize_success(finalize, output_digest, cleanup_pending)

    @staticmethod
    def _finalize_failure(
        finalize: Callable[[JobExecutionOutcome], bool], error: ServiceError
    ) -> None:
        try:
            finalize(JobExecutionOutcome("Failed", error=error, preserve_failure=True))
        except Exception as finalization_error:
            raise JobExecutionFinalizationError from finalization_error

    @staticmethod
    def _finalize_success(
        finalize: Callable[[JobExecutionOutcome], bool],
        output_digest: str,
        cleanup_pending: bool,
    ) -> None:
        try:
            finalize(
                JobExecutionOutcome(
                    "Completed",
                    output_digest=output_digest,
                    cleanup_pending=cleanup_pending,
                )
            )
        except Exception as finalization_error:
            raise JobExecutionFinalizationError from finalization_error

    def _prepare_embedded_source(
        self, execution_input: JobExecutionInput
    ) -> _PreparedEmbeddedSubtitle | None:
        embedded = execution_input.embedded
        if embedded is None:
            raise ValueError("Embedded Job execution requires embedded input")
        source = _verified_extracted_source(
            embedded.extraction_marker,
            execution_input.work_directory,
            embedded.source_format,
        )
        if source is not None:
            return _PreparedEmbeddedSubtitle(source, True)
        if self._extraction is None:
            raise ServiceError(
                "extraction_unavailable",
                "Embedded subtitle Extraction is unavailable",
            )
        extracted = _extract_embedded_source(
            self._extraction,
            embedded.media_path,
            embedded.stream_index,
            embedded.source_format,
            execution_input.work_directory,
        )
        return _PreparedEmbeddedSubtitle(extracted, False)


@dataclass(frozen=True)
class _PreparedEmbeddedSubtitle:
    subtitle: ExtractedEmbeddedSubtitle
    reused: bool


class _ExecutionInterruptedError(Exception):
    pass


def _interrupted_outcome() -> JobExecutionOutcome:
    return JobExecutionOutcome(
        "Interrupted",
        error=ServiceError(
            "job_interrupted", "Job was interrupted when CueWeaver stopped"
        ),
    )


def _write_term_map(
    work_directory: Path, term_map: Mapping[str, str] | None
) -> Path | None:
    if term_map is None:
        return None
    content = validate_term_map_content(dict(term_map))
    work_directory.mkdir(parents=True, exist_ok=True)
    term_map_path = work_directory / "term-map.json"
    term_map_path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return term_map_path


def _verified_extracted_source(
    marker: Mapping[str, object] | None,
    work_directory: Path,
    source_format: str,
) -> ExtractedEmbeddedSubtitle | None:
    if (
        marker is None
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
    return ExtractedEmbeddedSubtitle(
        source, source_format, str(marker["content_digest"])
    )


def _extract_embedded_source(
    extraction: Extraction,
    media_path: Path,
    stream_index: int | None,
    source_format: str,
    work_directory: Path,
) -> ExtractedEmbeddedSubtitle:
    if stream_index is None:
        raise ValueError("Embedded Job execution requires a stream index")
    subtitle_path = work_directory / f"source.{source_format}"
    work_directory.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = mkstemp(
        dir=work_directory,
        prefix=f".{subtitle_path.name}.retry.",
        suffix=subtitle_path.suffix,
    )
    os.close(descriptor)
    candidate_path = Path(raw_path)
    candidate_path.unlink()
    try:
        extraction.extract(ExtractRequest(media_path, stream_index, candidate_path))
        _replace_extracted_source(candidate_path, subtitle_path)
        digest = _content_digest(subtitle_path)
    finally:
        candidate_path.unlink(missing_ok=True)
    return ExtractedEmbeddedSubtitle(subtitle_path, source_format, digest)


def _replace_extracted_source(candidate: Path, destination: Path) -> None:
    diagnostic: Path | None = None
    if destination.is_dir():
        descriptor, raw_path = mkstemp(
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


def _fsync_directory(directory: Path) -> None:
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


__all__ = [
    "EmbeddedExecutionInput",
    "ExtractedEmbeddedSubtitle",
    "JobExecution",
    "JobExecutionFinalizationError",
    "JobExecutionInput",
    "JobExecutionOutcome",
    "JobExecutionProgress",
    "JobExecutionProgressPersistenceError",
]
