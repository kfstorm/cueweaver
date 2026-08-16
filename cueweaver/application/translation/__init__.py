"""Translation operation and its contracts."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...subtitle_formats import matching_format
from ..errors import ServiceError
from ..output import OutputPublisher
from ..term_maps import reject_duplicate_json_pairs, validate_term_map_content


@dataclass(frozen=True)
class TranslateRequest:
    subtitle_path: Path
    target_language_code: str
    output_path: Path
    work_directory: Path
    term_map_path: Path | None = None
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True
    overwrite: bool = False


@dataclass(frozen=True)
class TranslateResult:
    output_path: Path
    target_language_code: str
    format: str


class Translator(Protocol):
    @property
    def available(self) -> bool: ...

    def translate(
        self,
        source: Path,
        target_language: str,
        *,
        user_overrides: Mapping[str, str] | None = None,
        work_directory: Path,
        dynamic_terminology_enabled: bool = True,
        subtitle_terminology_filter_enabled: bool = True,
    ) -> bytes: ...


class Translation:
    def __init__(
        self,
        translator: Translator,
        output: OutputPublisher,
        *,
        publication_guard: Callable[[], AbstractContextManager[None]] | None = None,
        before_publication: Callable[[], None] | None = None,
        on_publication_failure: Callable[[Exception], None] | None = None,
        after_publication: Callable[[], None] | None = None,
    ) -> None:
        self._translator = translator
        self._output = output
        self._publication_guard = publication_guard or nullcontext
        self._before_publication = before_publication or (lambda: None)
        self._on_publication_failure = on_publication_failure or (lambda _error: None)
        self._after_publication = after_publication or (lambda: None)

    def translate(self, request: TranslateRequest) -> TranslateResult:
        subtitle_format = matching_format(request.subtitle_path, request.output_path)
        _require_readable_subtitle(request.subtitle_path)
        _create_work_directory(request.work_directory)
        term_map = _load_term_map(request.term_map_path)
        try:
            content = self._translator.translate(
                request.subtitle_path,
                request.target_language_code,
                user_overrides=term_map,
                work_directory=request.work_directory,
                dynamic_terminology_enabled=request.dynamic_terminology_enabled,
                subtitle_terminology_filter_enabled=request.subtitle_terminology_filter_enabled,
            )
        except Exception as error:
            raise ServiceError("translation_failed", "Translation failed") from error

        def write(temporary_path: Path) -> None:
            temporary_path.write_bytes(content)

        with self._publication_guard():
            self._before_publication()
            try:
                self._output.publish(
                    request.output_path, write, overwrite=request.overwrite
                )
            except Exception as error:
                self._on_publication_failure(error)
                raise
            self._after_publication()
        return TranslateResult(
            request.output_path, request.target_language_code, subtitle_format
        )


def _require_readable_subtitle(subtitle_path: Path) -> None:
    if not subtitle_path.is_file():
        raise ServiceError(
            "subtitle_not_found", "Subtitle does not exist", path=subtitle_path
        )
    try:
        with subtitle_path.open("rb"):
            pass
    except OSError as error:
        raise ServiceError(
            "subtitle_unreadable", "Subtitle cannot be read", path=subtitle_path
        ) from error


def _create_work_directory(work_directory: Path) -> None:
    try:
        work_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ServiceError(
            "invalid_work_directory",
            "Work directory cannot be created",
            path=work_directory,
        ) from error


def _load_term_map(term_map_path: Path | None) -> dict[str, str]:
    if term_map_path is None:
        return {}
    try:
        payload = json.loads(
            term_map_path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_json_pairs,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ServiceError(
            "invalid_term_map", "Term map cannot be read", path=term_map_path
        ) from error
    if not isinstance(payload, dict):
        raise ServiceError("invalid_term_map", "Term map must map non-empty strings")
    try:
        return validate_term_map_content(payload)
    except ServiceError as error:
        # Keep the Translation operation's existing error context contract.
        raise ServiceError(error.error_code, error.message) from error
