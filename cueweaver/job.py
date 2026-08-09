"""The Job runner seam for CueWeaver's first vertical slice."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from os import PathLike
from pathlib import Path
from typing import Protocol, runtime_checkable

from .publishing import publish_atomically
from .subtitles import (
    SubtitleFormat,
    SubtitleValidationError,
    validate_subtitle_pair,
)


class JobState(str, Enum):
    DISCOVERED = "discovered"
    TRANSLATING = "translating"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class JobError(Exception):
    """A user-visible Job failure."""


class TargetLanguageRequired(JobError):
    """Raised when the required user Target language is not configured."""


class SourceSelectionError(JobError):
    """Raised when Discovery cannot select one External subtitle."""


class TranslationUnavailable(JobError):
    """Raised when a non-no-op Job has no configured translation provider."""


class TranslationFailed(JobError):
    """Raised when an injected translation provider fails."""


@runtime_checkable
class Translator(Protocol):
    def translate(
        self, source: Path, target_language: str
    ) -> bytes | str | PathLike[str]:
        """Return translated subtitle content or a path containing it."""


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
    matches = _LANGUAGE_CODE_IN_TEXT.findall(clean_value)
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

    def __init__(self, translator: Translator | object | None = None):
        self._translator = translator

    def run(
        self,
        media: PathLike[str] | str,
        *,
        target_language: str | None = None,
        source: PathLike[str] | str | None = None,
        source_language: str | None = None,
    ) -> JobResult:
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

            effective_source_language = source_language or selected_source.language
            no_op = languages_match(effective_source_language, configured_target)
            source_content = selected_source.path.read_bytes()
            if no_op:
                delivered_content = source_content
            else:
                lifecycle.append(JobState.TRANSLATING)
                delivered_content = self._translate(
                    selected_source.path,
                    configured_target,
                )

            lifecycle.append(JobState.VALIDATING)
            validate_subtitle_pair(
                source_content,
                delivered_content,
                selected_source.subtitle_format,
            )

            lifecycle.append(JobState.PUBLISHING)
            published_path = _published_path(
                media_path,
                configured_target,
                selected_source.subtitle_format,
            )
            publish_atomically(delivered_content, published_path)
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
            )

    def _translate(self, source: Path, target_language: str) -> bytes:
        if self._translator is None:
            raise TranslationUnavailable(
                "Source is not already in Target language and no translator is configured"
            )
        try:
            if callable(self._translator):
                translated = self._translator(source, target_language)
            else:
                translated = self._translator.translate(source, target_language)
        except Exception as error:
            raise TranslationFailed(f"Translation failed: {error}") from error
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
