"""The Job runner seam for CueWeaver's first vertical slice."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from os import PathLike
from os import replace as atomic_replace
from pathlib import Path
from threading import Event, Lock
from typing import Protocol, runtime_checkable

from .publishing import publish_atomically
from .subtitles import (
    SubtitleFormat,
    SubtitleValidationError,
    validate_subtitle,
    validate_subtitle_pair,
)
from .translation import PySubtransTranslator


class JobState(str, Enum):
    DISCOVERED = "discovered"
    TRANSLATING = "translating"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    CANCELED = "canceled"
    FAILED = "failed"


class JobError(Exception):
    """A user-visible Job failure."""


class TargetLanguageRequired(JobError):
    """Raised when the required user Target language is not configured."""


class SourceSelectionError(JobError):
    """Raised when Discovery cannot select one External subtitle."""


class TranslationFailed(JobError):
    """Raised when an injected translation provider fails."""


class JobCanceled(JobError):
    """Raised internally when the user cancels an active Job."""


@runtime_checkable
class Translator(Protocol):
    def translate(
        self, source: Path, target_language: str
    ) -> bytes | str | PathLike[str]:
        """Return translated subtitle content or a path containing it."""


TranslatorFunction = Callable[[Path, str], bytes | str | PathLike[str]]


@dataclass(frozen=True)
class SubtitleCandidate:
    path: Path
    subtitle_format: SubtitleFormat
    language: str | None


@dataclass(frozen=True)
class JobResult:
    state: JobState
    lifecycle: tuple[JobState, ...]
    media: Path
    target_language: str | None
    source: SubtitleCandidate | None
    published_path: Path | None
    no_op: bool
    error: str | None = None
    intermediate_path: Path | None = None
    translated_content: bytes | None = None

    @property
    def status(self) -> str:
        return self.state.value


_LANGUAGE_ALIASES = {
    "chinese": "zh",
    "english": "en",
    "french": "fr",
    "german": "de",
    "japanese": "ja",
    "korean": "ko",
    "mandarin": "zh",
    "simplified chinese": "zh-cn",
    "spanish": "es",
    "traditional chinese": "zh-tw",
}
_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z]{2,4})?$", re.IGNORECASE)
_LANGUAGE_CODE_IN_TEXT = re.compile(
    r"(?<![a-z])([a-z]{2,3}(?:[-_][a-z]{2,4})?)(?![a-z])", re.IGNORECASE
)
_SUPPORTED_SUFFIXES = frozenset(
    subtitle_format.extension for subtitle_format in SubtitleFormat
)


def discover_external_subtitles(
    media: PathLike[str] | str,
) -> tuple[SubtitleCandidate, ...]:
    """Discover supported External subtitles named after one Media."""

    media_path = Path(media).expanduser().resolve()
    if not media_path.exists() or not media_path.is_file():
        raise SourceSelectionError(f"Media does not exist: {media_path}")

    candidates = []
    prefix = f"{media_path.stem}."
    for path in media_path.parent.iterdir():
        if not path.is_file() or path.suffix.casefold() not in _SUPPORTED_SUFFIXES:
            continue
        if path.stem != media_path.stem and not path.stem.startswith(prefix):
            continue
        candidates.append(
            SubtitleCandidate(
                path=path.resolve(),
                subtitle_format=SubtitleFormat.from_path(path),
                language=_infer_language(path.stem[len(media_path.stem) :]),
            )
        )
    return tuple(
        sorted(candidates, key=lambda candidate: candidate.path.name.casefold())
    )


def normalize_language(value: str) -> str:
    """Return a lower-case language tag, rejecting ambiguous configuration."""

    clean_value = value.strip()
    if not clean_value:
        raise TargetLanguageRequired(
            "Target language is required; set --target-language or "
            "CUEWEAVER_TARGET_LANGUAGE."
        )
    alias = _LANGUAGE_ALIASES.get(clean_value.casefold())
    if alias is not None:
        return alias
    if _LANGUAGE_CODE.fullmatch(clean_value):
        return clean_value.replace("_", "-").casefold()
    matches: list[str] = _LANGUAGE_CODE_IN_TEXT.findall(clean_value)
    if len(matches) == 1:
        return matches[0].replace("_", "-").casefold()
    raise TargetLanguageRequired(f"Invalid Target language: {value!r}")


def languages_match(source_language: str | None, target_language: str) -> bool:
    if source_language is None:
        return False
    source = normalize_language(source_language)
    target = normalize_language(target_language)
    source_base = source.split("-", 1)[0]
    target_base = target.split("-", 1)[0]
    return source == target or (
        source_base == target_base and ("-" not in source or "-" not in target)
    )


def language_tag(value: str) -> str:
    """Format a normalized language for a published filename."""

    normalized = normalize_language(value)
    parts = normalized.split("-")
    return "-".join([parts[0], *(part.upper() for part in parts[1:])])


class JobRunner:
    """Run one Media through External Source selection and Publishing."""

    def __init__(self, translator: Translator | TranslatorFunction | None = None):
        self._translator = translator
        self._state_lock = Lock()
        self._cancel_requested = Event()
        self._active_translator: Translator | TranslatorFunction | None = None
        self._intermediate_path: Path | None = None
        self._translated_content: bytes | None = None

    def cancel(self) -> None:
        """Cancel the active Job without publishing its partial translation."""

        self._cancel_requested.set()
        with self._state_lock:
            translator = self._active_translator
        if translator is None:
            return
        cancel = getattr(translator, "cancel", None)
        if callable(cancel):
            cancel()

    def publish_intermediate(
        self,
        result: JobResult,
        *,
        confirmed: bool = False,
    ) -> Path:
        """Publish a partial result only after explicit caller confirmation."""

        if not confirmed:
            raise JobError(
                "Explicit confirmation is required to publish partial output"
            )
        if result.state not in {JobState.CANCELED, JobState.FAILED}:
            raise JobError("Only an incomplete Job can publish intermediate output")
        if (
            result.intermediate_path is None
            or result.source is None
            or result.target_language is None
        ):
            raise JobError("Job has no intermediate output to publish")
        content = result.intermediate_path.read_bytes()
        validate_subtitle(content, result.source.subtitle_format)
        destination = _published_path(
            result.media,
            result.target_language,
            result.source.subtitle_format,
        )
        return publish_atomically(content, destination)

    def retry_publishing(self, result: JobResult) -> JobResult:
        """Retry Publishing for a failed Job without translating again."""

        if (
            result.state is not JobState.FAILED
            or JobState.PUBLISHING not in result.lifecycle
        ):
            raise JobError("Only a Publishing failure can be retried")
        if (
            result.source is None
            or result.target_language is None
            or (result.intermediate_path is None and result.translated_content is None)
        ):
            raise JobError("Job has no translated result to republish")

        retry_lifecycle = (*result.lifecycle, JobState.PUBLISHING)
        staged_path = result.intermediate_path
        try:
            content = result.translated_content
            if content is None:
                assert staged_path is not None
                content = staged_path.read_bytes()
            self._translated_content = content
            validate_subtitle_pair(
                result.source.path.read_bytes(),
                content,
                result.source.subtitle_format,
            )
            destination = _published_path(
                result.media,
                result.target_language,
                result.source.subtitle_format,
            )
            if staged_path is None:
                staged_path = _stage_translation(content, destination)
            self._intermediate_path = staged_path
            publish_atomically(content, destination)
        except (OSError, SubtitleValidationError) as error:
            self._intermediate_path = staged_path
            return replace(
                result,
                lifecycle=(*retry_lifecycle, JobState.FAILED),
                error=str(error),
                intermediate_path=staged_path,
                translated_content=content,
            )

        if staged_path is not None:
            _discard_staged_translation(staged_path)
        self._intermediate_path = None
        self._translated_content = None
        return replace(
            result,
            state=JobState.PUBLISHED,
            lifecycle=(*retry_lifecycle, JobState.PUBLISHED),
            published_path=destination,
            error=None,
            intermediate_path=None,
            translated_content=None,
        )

    def run(
        self,
        media: PathLike[str] | str,
        *,
        target_language: str | None = None,
        source: PathLike[str] | str | None = None,
        source_language: str | None = None,
    ) -> JobResult:
        self._cancel_requested.clear()
        self._intermediate_path = None
        self._translated_content = None
        self._reset_translator_for_job()
        media_path = Path(media).expanduser().resolve()
        lifecycle: list[JobState] = []
        selected_source: SubtitleCandidate | None = None
        configured_target: str | None = None
        no_op = False
        try:
            configured_target = normalize_language(
                target_language
                if target_language is not None
                else os.environ.get("CUEWEAVER_TARGET_LANGUAGE", "")
            )
            candidates = discover_external_subtitles(media_path)
            selected_source = _select_source(candidates, source, media_path.parent)
            lifecycle.append(JobState.DISCOVERED)
            self._raise_if_canceled()

            effective_source_language = source_language or selected_source.language
            no_op = languages_match(effective_source_language, configured_target)
            source_content = selected_source.path.read_bytes()
            self._raise_if_canceled()
            if no_op:
                delivered_content = source_content
            else:
                lifecycle.append(JobState.TRANSLATING)
                delivered_content = self._translate(
                    selected_source.path,
                    configured_target,
                )
                self._raise_if_canceled()

            self._translated_content = delivered_content
            self._raise_if_canceled()
            lifecycle.append(JobState.VALIDATING)
            validate_subtitle_pair(
                source_content,
                delivered_content,
                selected_source.subtitle_format,
            )

            self._raise_if_canceled()
            lifecycle.append(JobState.PUBLISHING)
            published_path = _published_path(
                media_path,
                configured_target,
                selected_source.subtitle_format,
            )
            staged_path = _stage_translation(delivered_content, published_path)
            self._intermediate_path = staged_path
            publish_atomically(delivered_content, published_path)
            _discard_staged_translation(staged_path)
            self._intermediate_path = None
            self._translated_content = None
            lifecycle.append(JobState.PUBLISHED)
            return JobResult(
                state=JobState.PUBLISHED,
                lifecycle=tuple(lifecycle),
                media=media_path,
                target_language=configured_target,
                source=selected_source,
                published_path=published_path,
                no_op=no_op,
            )
        except JobCanceled as error:
            lifecycle.append(JobState.CANCELED)
            return JobResult(
                state=JobState.CANCELED,
                lifecycle=tuple(lifecycle),
                media=media_path,
                target_language=configured_target,
                source=selected_source,
                published_path=None,
                no_op=no_op,
                error=str(error),
                intermediate_path=self._intermediate_path,
                translated_content=self._translated_content,
            )
        except (JobError, OSError, SubtitleValidationError) as error:
            lifecycle.append(JobState.FAILED)
            return JobResult(
                state=JobState.FAILED,
                lifecycle=tuple(lifecycle),
                media=media_path,
                target_language=configured_target,
                source=selected_source,
                published_path=None,
                no_op=no_op,
                error=str(error),
                intermediate_path=self._intermediate_path,
                translated_content=self._translated_content,
            )

    def _translate(self, source: Path, target_language: str) -> bytes:
        try:
            translator = self._translator
            if translator is None:
                translator = PySubtransTranslator()
                self._translator = translator
            with self._state_lock:
                self._active_translator = translator
                canceled = self._cancel_requested.is_set()
            if canceled:
                raise JobCanceled("Job canceled")
            if callable(translator):
                translated = translator(source, target_language)
            else:
                translated = translator.translate(source, target_language)
        except Exception as error:
            if isinstance(error, JobCanceled):
                raise
            if self._cancel_requested.is_set():
                raise JobCanceled("Job canceled") from error
            raise TranslationFailed(f"Translation failed: {error}") from error
        finally:
            with self._state_lock:
                self._active_translator = None
            self._intermediate_path = _get_intermediate_path(translator)
        self._raise_if_canceled()
        if isinstance(translated, (str, bytes, bytearray)):
            return (
                translated.encode("utf-8")
                if isinstance(translated, str)
                else bytes(translated)
            )
        if not isinstance(translated, PathLike):
            raise TranslationFailed(
                "Translation provider must return subtitle bytes, text, or a path"
            )
        try:
            translated_path = Path(translated)
            return translated_path.read_bytes()
        except Exception as error:
            raise TranslationFailed(
                "Translation provider returned an unreadable path"
            ) from error

    def _raise_if_canceled(self) -> None:
        if self._cancel_requested.is_set():
            raise JobCanceled("Job canceled")

    def _reset_translator_for_job(self) -> None:
        translator = self._translator
        if translator is None:
            return
        reset = getattr(translator, "reset_for_job", None)
        if callable(reset):
            reset()


def _infer_language(suffix: str) -> str | None:
    tokens = [token for token in re.split(r"[^A-Za-z_-]+", suffix) if token]
    for token in tokens:
        try:
            return normalize_language(token)
        except TargetLanguageRequired:
            continue
    return None


def _select_source(
    candidates: tuple[SubtitleCandidate, ...],
    source: PathLike[str] | str | None,
    media_directory: Path,
) -> SubtitleCandidate:
    if source is not None:
        requested = Path(source).expanduser()
        requested_paths = {requested.resolve()}
        if not requested.is_absolute():
            requested_paths.add((media_directory / requested).resolve())
        for candidate in candidates:
            if (
                candidate.path in requested_paths
                or candidate.path.name == requested.name
            ):
                return candidate
        raise SourceSelectionError(f"External subtitle was not discovered: {source}")
    if not candidates:
        raise SourceSelectionError(
            "No eligible External subtitle found beside the Media "
            "(supported formats: SRT, ASS, VTT)"
        )
    if len(candidates) > 1:
        names = ", ".join(candidate.path.name for candidate in candidates)
        raise SourceSelectionError(
            f"Multiple External subtitles found; choose one with --source: {names}"
        )
    return candidates[0]


def _published_path(
    media: Path,
    target_language: str,
    subtitle_format: SubtitleFormat,
) -> Path:
    return media.with_name(
        f"{media.stem}.{language_tag(target_language)}{subtitle_format.extension}"
    )


def _get_intermediate_path(
    translator: Translator | TranslatorFunction | None,
) -> Path | None:
    if translator is None:
        return None
    path = getattr(translator, "intermediate_path", None)
    if isinstance(path, (str, PathLike)):
        return Path(path)
    return None


def _stage_translation(content: bytes, destination: Path) -> Path:
    """Persist a complete translation outside the Media directory for retry."""

    work_directory = destination.parent / ".cueweaver" / "publishing"
    work_directory.mkdir(parents=True, exist_ok=True)
    staged_path = work_directory / f"{destination.name}.pending"
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{staged_path.name}.",
            suffix=".tmp",
            dir=work_directory,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        atomic_replace(temporary_path, staged_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return staged_path


def _discard_staged_translation(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
