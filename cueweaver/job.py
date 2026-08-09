"""The Job runner seam for CueWeaver's first vertical slice."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from os import PathLike
from os import replace as atomic_replace
from pathlib import Path
from threading import Event, Lock
from typing import Protocol, cast, runtime_checkable

from .metadata import (
    MetadataCache,
    MetadataContext,
    MetadataError,
    MetadataProvider,
    MetadataRequest,
    TMDbMetadataProvider,
)
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
    EXTRACTING = "extracting"
    METADATA = "metadata"
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
    """Raised when Discovery cannot select one eligible Source."""


class DiscoveryFailed(JobError):
    """Raised when container metadata cannot be inspected."""


class ExtractionFailed(JobError):
    """Raised when a confirmed Embedded Source cannot be materialized."""


class TranslationFailed(JobError):
    """Raised when an injected translation provider fails."""


class JobCanceled(JobError):
    """Raised internally when the user cancels an active Job."""


@runtime_checkable
class Translator(Protocol):
    def translate(
        self,
        source: Path,
        target_language: str,
        *,
        context: str = "",
    ) -> bytes | str | PathLike[str]:
        """Return translated subtitle content or a path containing it."""


TranslatorFunction = Callable[[Path, str], bytes | str | PathLike[str]]


class SubtitleSubtype(str, Enum):
    EXTERNAL = "external"
    EMBEDDED = "embedded"
    BITMAP = "bitmap"


_SUBTYPE_IO_COST = {
    SubtitleSubtype.EXTERNAL: 0,
    SubtitleSubtype.EMBEDDED: 1,
    SubtitleSubtype.BITMAP: 2,
}


@dataclass(frozen=True)
class SubtitleCandidate:
    path: Path
    subtitle_format: SubtitleFormat
    language: str | None
    subtype: SubtitleSubtype = SubtitleSubtype.EXTERNAL
    io_cost: int | None = None
    container_index: int | None = None
    container_number: int | None = None
    codec: str | None = None
    title: str | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        subtype = SubtitleSubtype(self.subtype)
        object.__setattr__(self, "subtype", subtype)
        if self.io_cost is None:
            object.__setattr__(self, "io_cost", _SUBTYPE_IO_COST[subtype])

    @property
    def selectable(self) -> bool:
        return self.subtype is not SubtitleSubtype.BITMAP

    @property
    def label(self) -> str:
        if self.display_name is not None:
            return self.display_name
        if self.subtype is SubtitleSubtype.EXTERNAL:
            return self.path.name
        embedded_number = self.container_number or self.container_index
        description = (
            f"Embedded subtitle {embedded_number}"
            if embedded_number is not None
            else "Embedded subtitle"
        )
        details = [description]
        if self.language is not None:
            details.append(self.language)
        if self.title:
            details.append(self.title)
        return f"{self.path.name} ({', '.join(details)})"

    @property
    def selection_id(self) -> str:
        if self.subtype is SubtitleSubtype.EXTERNAL:
            return str(self.path)
        container_number = self.container_number or self.container_index
        if container_number is not None:
            return f"embedded:{container_number}"
        return self.label


class SubtitleExtractor(Protocol):
    def extract(
        self, media: Path, candidate: SubtitleCandidate, destination: Path
    ) -> PathLike[str] | None:
        """Materialize a confirmed Embedded Source at *destination*."""


class SeconvExtractor:
    """Extract one Embedded Source through the installed seconv command."""

    def __init__(self, command: str | PathLike[str] | None = None) -> None:
        self.command = str(command or os.environ.get("CUEWEAVER_SECONV", "seconv"))

    def extract(
        self, media: Path, candidate: SubtitleCandidate, destination: Path
    ) -> Path:
        container_number = candidate.container_number or candidate.container_index
        if container_number is None:
            raise ExtractionFailed(
                "Embedded Source has no container subtitle number for Extraction"
            )
        with tempfile.TemporaryDirectory(prefix="cueweaver-seconv-") as directory:
            output_directory = Path(directory)
            command = [
                self.command,
                str(media),
                candidate.subtitle_format.value,
                f"--track-number:{container_number}",
                "--output-folder",
                str(output_directory),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                raise ExtractionFailed(
                    f"Extraction failed through seconv: {error}"
                ) from error

            outputs = sorted(
                path
                for path in output_directory.rglob("*")
                if path.is_file() and path.suffix.casefold() in _SUPPORTED_SUFFIXES
            )
            if len(outputs) != 1:
                raise ExtractionFailed(
                    "seconv did not produce exactly one Embedded subtitle"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                content = outputs[0].read_bytes()
            except OSError as error:
                raise ExtractionFailed(
                    "seconv produced an unreadable Embedded subtitle"
                ) from error
            _write_cached_extraction(destination, content)
        return destination


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
    context: str = ""
    metadata_degradation: str | None = None
    metadata_request: MetadataRequest | None = None

    @property
    def status(self) -> str:
        return self.state.value


_LANGUAGE_ALIASES = {
    "chi": "zh",
    "chinese": "zh",
    "deu": "de",
    "english": "en",
    "eng": "en",
    "french": "fr",
    "fra": "fr",
    "german": "de",
    "ita": "it",
    "japanese": "ja",
    "jpn": "ja",
    "korean": "ko",
    "kor": "ko",
    "mandarin": "zh",
    "simplified chinese": "zh-cn",
    "spanish": "es",
    "spa": "es",
    "traditional chinese": "zh-tw",
    "zho": "zh",
}
_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z]{2,4})?$", re.IGNORECASE)
_LANGUAGE_CODE_IN_TEXT = re.compile(
    r"(?<![a-z])([a-z]{2,3}(?:[-_][a-z]{2,4})?)(?![a-z])", re.IGNORECASE
)
_SUPPORTED_SUFFIXES = frozenset(
    subtitle_format.extension for subtitle_format in SubtitleFormat
)
_CONTAINER_SUFFIXES = frozenset({".mkv", ".mp4"})
_BITMAP_CODECS = frozenset({"dvd_subtitle", "hdmv_pgs_subtitle", "pgssub"})
_TEXT_CODEC_FORMATS = {
    "ass": SubtitleFormat.ASS,
    "ssa": SubtitleFormat.ASS,
    "subrip": SubtitleFormat.SRT,
    "srt": SubtitleFormat.SRT,
    "webvtt": SubtitleFormat.VTT,
    "mov_text": SubtitleFormat.SRT,
    "text": SubtitleFormat.SRT,
    "hdmv_text_subtitle": SubtitleFormat.SRT,
    "substation_alpha": SubtitleFormat.ASS,
}


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


def discover_subtitles(
    media: PathLike[str] | str,
) -> tuple[SubtitleCandidate, ...]:
    """Discover External and container subtitle candidates without Extraction."""

    media_path = Path(media).expanduser().resolve()
    external = discover_external_subtitles(media_path)
    try:
        embedded = discover_embedded_subtitles(media_path)
    except DiscoveryFailed as error:
        if not external or not _is_invalid_container_error(error):
            raise
        embedded = ()
    return tuple(
        sorted(
            (*external, *embedded),
            key=lambda candidate: (
                candidate.io_cost,
                candidate.label.casefold(),
                candidate.container_index
                if candidate.container_index is not None
                else -1,
            ),
        )
    )


def discover_embedded_subtitles(
    media: PathLike[str] | str,
) -> tuple[SubtitleCandidate, ...]:
    """Read MKV/MP4 Embedded subtitle metadata without reading payloads."""

    media_path = Path(media).expanduser().resolve()
    if media_path.suffix.casefold() not in _CONTAINER_SUFFIXES:
        return ()
    entries = _probe_container_entries(media_path, subtitles_only=True)
    candidates: list[SubtitleCandidate] = []
    for entry in entries:
        codec = str(entry.get("codec_name", "")).casefold()
        if codec in _BITMAP_CODECS:
            subtype = SubtitleSubtype.BITMAP
            subtitle_format = SubtitleFormat.SRT
        elif codec in _TEXT_CODEC_FORMATS:
            subtype = SubtitleSubtype.EMBEDDED
            subtitle_format = _TEXT_CODEC_FORMATS[codec]
        else:
            continue
        index_value = entry.get("index")
        if not isinstance(index_value, (int, str)):
            continue
        try:
            container_index = int(index_value)
        except ValueError:
            continue
        container_number = _parse_container_number(entry.get("id"))
        if container_number is None:
            container_number = (
                container_index + 1
                if media_path.suffix.casefold() == ".mkv"
                else container_index
            )
        tags = entry.get("tags", {})
        if not isinstance(tags, dict):
            tags = {}
        language_value = tags.get("language")
        language = (
            _normalise_discovered_language(str(language_value))
            if language_value
            else None
        )
        title_value = tags.get("title")
        title = str(title_value).strip() if title_value else None
        candidates.append(
            SubtitleCandidate(
                path=media_path,
                subtitle_format=subtitle_format,
                language=language,
                subtype=subtype,
                container_index=container_index,
                container_number=container_number,
                codec=codec or None,
                title=title,
            )
        )
    return tuple(candidates)


def discover_media_primary_language(
    media: PathLike[str] | str,
) -> str | None:
    """Read the first declared audio language from MKV/MP4 metadata."""

    media_path = Path(media).expanduser().resolve()
    entries = [
        entry
        for entry in _probe_container_entries(
            media_path, subtitles_only=False, strict=False
        )
        if entry.get("codec_type") == "audio"
    ]
    default_entries = []
    for entry in entries:
        disposition = entry.get("disposition")
        if isinstance(disposition, dict) and disposition.get("default") in (True, "1"):
            default_entries.append(entry)
    for entry in (*default_entries, *entries):
        tags = entry.get("tags", {})
        if not isinstance(tags, dict):
            continue
        language = tags.get("language")
        if language:
            return _normalise_discovered_language(str(language))
    return None


def _probe_container_entries(
    media: Path,
    *,
    subtitles_only: bool,
    strict: bool = True,
) -> list[dict[str, object]]:
    command = [
        "ffprobe",
        "-v",
        "error",
    ]
    if subtitles_only:
        command.extend(("-select_streams", "s"))
    command.extend(("-show_streams", "-of", "json", str(media)))
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        TypeError,
        ValueError,
    ) as error:
        if strict:
            raise DiscoveryFailed(
                "Cannot inspect MKV/MP4 Embedded subtitles; install ffprobe "
                f"or check the Media container: {error}"
            ) from error
        return []
    if not isinstance(payload, dict):
        if strict:
            raise DiscoveryFailed("ffprobe returned invalid container metadata")
        return []
    entries = payload.get("streams")
    if not isinstance(entries, list):
        if strict:
            raise DiscoveryFailed("ffprobe returned no usable container metadata")
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _is_invalid_container_error(error: DiscoveryFailed) -> bool:
    cause = error.__cause__
    if not isinstance(cause, subprocess.CalledProcessError):
        return False
    diagnostic = str(cause.stderr or "").casefold()
    return any(
        marker in diagnostic
        for marker in (
            "invalid data",
            "ebml header parsing failed",
            "moov atom not found",
            "could not find codec parameters",
        )
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


def _normalise_discovered_language(value: str) -> str | None:
    if value.strip().casefold() in {"und", "unknown", "zxx"}:
        return None
    try:
        return normalize_language(value)
    except TargetLanguageRequired:
        return None


def _parse_container_number(value: object) -> int | None:
    if value is None:
        return None
    try:
        text = str(value)
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except ValueError:
        return None


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
    """Run one Media through Source selection, translation, and Publishing."""

    def __init__(
        self,
        translator: Translator | TranslatorFunction | None = None,
        *,
        metadata_provider: MetadataProvider | None = None,
        metadata_cache: MetadataCache | PathLike[str] | str | None = None,
        extractor: SubtitleExtractor | None = None,
        source_selector: Callable[
            [tuple[SubtitleCandidate, ...]],
            SubtitleCandidate,
        ]
        | None = None,
        discovery_observer: Callable[[tuple[SubtitleCandidate, ...]], None]
        | None = None,
        language_priority: Sequence[str] | str | None = None,
    ):
        self._translator = translator
        self._metadata_provider = metadata_provider
        self._metadata_cache = (
            metadata_cache
            if isinstance(metadata_cache, MetadataCache)
            else MetadataCache(metadata_cache)
            if metadata_cache is not None
            else None
        )
        self._extractor = extractor or SeconvExtractor()
        self._source_selector = source_selector
        self._discovery_observer = discovery_observer
        self._language_priority = (
            language_priority
            if language_priority is not None
            else os.environ.get("CUEWEAVER_SOURCE_LANGUAGE_PRIORITY")
        )
        self._state_lock = Lock()
        self._cancel_requested = Event()
        self._active_translator: Translator | TranslatorFunction | None = None
        self._active_metadata_provider: MetadataProvider | None = None
        self._intermediate_path: Path | None = None
        self._translated_content: bytes | None = None

    def cancel(self) -> None:
        """Cancel the active Job without publishing its partial translation."""

        self._cancel_requested.set()
        with self._state_lock:
            translator = self._active_translator
            metadata_provider = self._active_metadata_provider
        if translator is None:
            metadata_cancel = getattr(metadata_provider, "cancel", None)
            if callable(metadata_cancel):
                metadata_cancel()
            return
        cancel = getattr(translator, "cancel", None)
        if callable(cancel):
            cancel()
        metadata_cancel = getattr(metadata_provider, "cancel", None)
        if callable(metadata_cancel):
            metadata_cancel()

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

    def retry_metadata(self, result: JobResult) -> JobResult:
        """Retry degraded metadata without repeating a completed translation."""

        if (
            result.state is not JobState.PUBLISHED
            or result.metadata_request is None
            or result.metadata_degradation is None
        ):
            raise JobError("Only a published Job with degraded metadata can be retried")

        context = self._gather_metadata(
            result.metadata_request,
            refresh=True,
        )
        return replace(
            result,
            context=context.text,
            metadata_degradation=context.degradation,
        )

    def run(
        self,
        media: PathLike[str] | str,
        *,
        target_language: str | None = None,
        source: SubtitleCandidate | PathLike[str] | str | None = None,
        source_language: str | None = None,
        series_id: str | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
        refresh_metadata: bool = False,
    ) -> JobResult:
        self._cancel_requested.clear()
        self._intermediate_path = None
        self._translated_content = None
        self._reset_translator_for_job()
        self._reset_metadata_provider_for_job()
        media_path = Path(media).expanduser().resolve()
        lifecycle: list[JobState] = []
        selected_source: SubtitleCandidate | None = None
        configured_target: str | None = None
        no_op = False
        metadata_context: MetadataContext | None = None
        metadata_request: MetadataRequest | None = None
        try:
            configured_target = normalize_language(
                target_language
                if target_language is not None
                else os.environ.get("CUEWEAVER_TARGET_LANGUAGE", "")
            )
            metadata_request = _metadata_request(
                series_id,
                season_number,
                episode_number,
                refresh_metadata,
            )
            candidates = discover_subtitles(media_path)
            language_priority = self._language_priority
            if language_priority is None:
                language_priority = discover_media_primary_language(media_path)
            if self._discovery_observer is not None:
                self._discovery_observer(candidates)
            selected_source = _select_source(
                candidates,
                source,
                media_path.parent,
                language_priority=language_priority,
                source_selector=self._source_selector,
            )
            lifecycle.append(JobState.DISCOVERED)
            self._raise_if_canceled()

            source_path = selected_source.path
            if selected_source.subtype is SubtitleSubtype.EMBEDDED:
                lifecycle.append(JobState.EXTRACTING)
                selected_label = selected_source.label
                source_path = self._extract_source(media_path, selected_source)
                selected_source = replace(
                    selected_source,
                    path=source_path,
                    display_name=selected_label,
                )
                self._raise_if_canceled()

            effective_source_language = source_language or selected_source.language
            no_op = languages_match(effective_source_language, configured_target)
            source_content = source_path.read_bytes()
            self._raise_if_canceled()
            if no_op:
                delivered_content = source_content
            else:
                if metadata_request is not None:
                    lifecycle.append(JobState.METADATA)
                    metadata_context = self._gather_metadata(
                        metadata_request,
                        refresh=refresh_metadata,
                    )
                    self._raise_if_canceled()
                lifecycle.append(JobState.TRANSLATING)
                delivered_content = self._translate(
                    source_path,
                    configured_target,
                    context=(metadata_context.text if metadata_context else ""),
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
                context=metadata_context.text if metadata_context else "",
                metadata_degradation=(
                    metadata_context.degradation if metadata_context else None
                ),
                metadata_request=metadata_request,
            )
        except (JobCanceled, JobError, OSError, SubtitleValidationError) as error:
            terminal_state = (
                JobState.CANCELED if isinstance(error, JobCanceled) else JobState.FAILED
            )
            lifecycle.append(terminal_state)
            return JobResult(
                state=terminal_state,
                lifecycle=tuple(lifecycle),
                media=media_path,
                target_language=configured_target,
                source=selected_source,
                published_path=None,
                no_op=no_op,
                error=str(error),
                intermediate_path=self._intermediate_path,
                translated_content=self._translated_content,
                context=metadata_context.text if metadata_context else "",
                metadata_degradation=(
                    metadata_context.degradation if metadata_context else None
                ),
                metadata_request=metadata_request,
            )

    def _gather_metadata(
        self,
        request: MetadataRequest,
        *,
        refresh: bool,
    ) -> MetadataContext:
        provider = self._metadata_provider or TMDbMetadataProvider()
        cache = self._metadata_cache or MetadataCache(_default_metadata_cache_path())
        series_overview: str | None = None
        episode_overview: str | None = None
        if not refresh:
            series_overview, episode_overview = cache.load(request)

        try:
            with self._state_lock:
                self._active_metadata_provider = provider
            self._raise_if_canceled()
            if series_overview is None:
                series_overview = _fetch_metadata_overview(
                    lambda: provider.get_series_overview(request.series_id),
                    "series",
                )
                cache.store(request, series_overview=series_overview)
            self._raise_if_canceled()
            if request.episode_key is not None and episode_overview is None:
                season_number = request.season_number
                episode_number = request.episode_number
                assert season_number is not None
                assert episode_number is not None
                episode_overview = _fetch_metadata_overview(
                    lambda: provider.get_episode_overview(
                        request.series_id,
                        season_number,
                        episode_number,
                    ),
                    "episode",
                )
                cache.store(request, episode_overview=episode_overview)
            self._raise_if_canceled()
        except JobCanceled:
            raise
        except (MetadataError, OSError) as error:
            if self._cancel_requested.is_set():
                raise JobCanceled("Job canceled") from error
            return MetadataContext(
                request=request,
                degradation=f"Metadata degraded: {error}",
            )
        finally:
            with self._state_lock:
                self._active_metadata_provider = None

        return MetadataContext(
            request=request,
            series_overview=series_overview or "",
            episode_overview=episode_overview or "",
        )

    def _extract_source(self, media: Path, candidate: SubtitleCandidate) -> Path:
        destination = _extraction_cache_path(media, candidate)
        if destination.is_file():
            return destination

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            extracted = self._extractor.extract(media, candidate, destination)
        except JobError:
            raise
        except Exception as error:
            raise ExtractionFailed(f"Extraction failed: {error}") from error

        if extracted is not None:
            _cache_extracted_path(Path(extracted), destination)
        if not destination.is_file():
            raise ExtractionFailed("Extraction did not produce a subtitle in the cache")
        return destination

    def _translate(
        self,
        source: Path,
        target_language: str,
        *,
        context: str = "",
    ) -> bytes:
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
            translated = _call_translator(
                translator,
                source,
                target_language,
                context=context,
            )
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

    def _reset_metadata_provider_for_job(self) -> None:
        provider = self._metadata_provider
        if provider is None:
            return
        reset = getattr(provider, "reset_for_job", None)
        if callable(reset):
            reset()


def _metadata_request(
    series_id: str | None,
    season_number: int | None,
    episode_number: int | None,
    refresh_metadata: bool,
) -> MetadataRequest | None:
    if series_id is None:
        if season_number is not None or episode_number is not None or refresh_metadata:
            raise JobError(
                "A TMDb series ID is required with season, episode, or metadata refresh"
            )
        return None
    try:
        return MetadataRequest(series_id, season_number, episode_number)
    except MetadataError as error:
        raise JobError(str(error)) from error


def _default_metadata_cache_path() -> Path:
    configured = os.environ.get("CUEWEAVER_METADATA_CACHE")
    if configured:
        return Path(configured).expanduser()
    cache_home = os.environ.get("XDG_CACHE_HOME")
    root = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return root / "cueweaver" / "metadata"


def _fetch_metadata_overview(
    fetch: Callable[[], str],
    label: str,
) -> str:
    try:
        value = fetch()
    except JobCanceled:
        raise
    except Exception as error:
        raise MetadataError(str(error)) from error
    return _validate_metadata_overview(value, label)


def _validate_metadata_overview(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise MetadataError(f"Metadata provider returned an invalid {label} overview")
    return value


def _call_translator(
    translator: Translator | TranslatorFunction,
    source: Path,
    target_language: str,
    *,
    context: str,
) -> bytes | str | PathLike[str]:
    method = cast(
        Callable[..., bytes | str | PathLike[str]],
        translator if callable(translator) else translator.translate,
    )
    if _accepts_context(method):
        return method(source, target_language, context=context)
    return method(source, target_language)


def _accepts_context(method: Callable[..., object]) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "context" or parameter.kind is parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _infer_language(suffix: str) -> str | None:
    tokens = [token for token in re.split(r"[^A-Za-z_-]+", suffix) if token]
    for token in tokens:
        language = _normalise_discovered_language(token)
        if language is not None:
            return language
    return None


def rank_subtitle_candidates(
    candidates: Sequence[SubtitleCandidate],
    language_priority: Sequence[str] | str | None = None,
) -> tuple[SubtitleCandidate, ...]:
    """Return candidates in the documented type/language/tie-break order."""

    priorities = _normalise_language_priority(language_priority)
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.io_cost,
                _language_rank(candidate.language, priorities),
                _format_rank(candidate.subtitle_format),
                candidate.label.casefold(),
                candidate.container_index
                if candidate.container_index is not None
                else -1,
            ),
        )
    )


def _select_source(
    candidates: tuple[SubtitleCandidate, ...],
    source: SubtitleCandidate | PathLike[str] | str | None,
    media_directory: Path,
    *,
    language_priority: Sequence[str] | str | None = None,
    source_selector: Callable[
        [tuple[SubtitleCandidate, ...]],
        SubtitleCandidate,
    ]
    | None = None,
) -> SubtitleCandidate:
    if source is not None:
        requested_source = _resolve_source_reference(
            candidates, source, media_directory
        )
        if requested_source is None:
            raise SourceSelectionError(f"Source was not discovered: {source}")
        if not requested_source.selectable:
            raise SourceSelectionError(
                "Bitmap Sources are visible but disabled and cannot be selected"
            )
        return requested_source

    eligible = tuple(candidate for candidate in candidates if candidate.selectable)
    if not eligible:
        if candidates:
            raise SourceSelectionError(
                "No eligible Source found: the discovered candidates are Bitmap "
                "subtitles, which are visible but disabled; Subtitle OCR is not "
                "available in v0.1"
            )
        raise SourceSelectionError(
            "No eligible Source found beside the Media "
            "(supported formats: SRT, ASS, VTT)"
        )

    ranked = rank_subtitle_candidates(eligible, language_priority)
    if _source_needs_confirmation(ranked, language_priority):
        if source_selector is None:
            names = ", ".join(candidate.label for candidate in candidates)
            raise SourceSelectionError(
                "Explicit Source selection is required; choose one with "
                f"--source: {names}"
            )
        selected_reference = source_selector(candidates)
        return _select_source(
            candidates,
            selected_reference,
            media_directory,
            language_priority=language_priority,
        )
    return ranked[0]


def _resolve_source_reference(
    candidates: tuple[SubtitleCandidate, ...],
    source: SubtitleCandidate | PathLike[str] | str,
    media_directory: Path,
) -> SubtitleCandidate | None:
    if isinstance(source, SubtitleCandidate):
        for candidate in candidates:
            if candidate == source or candidate.selection_id == source.selection_id:
                return candidate
        return None

    requested_text = os.fspath(source)
    requested = Path(requested_text).expanduser()
    requested_paths = {requested.resolve()}
    if not requested.is_absolute():
        requested_paths.add((media_directory / requested).resolve())
    for candidate in candidates:
        if candidate.subtype is SubtitleSubtype.EXTERNAL:
            if (
                candidate.path in requested_paths
                or candidate.path.name == requested.name
            ):
                return candidate
            continue
        if requested_text.casefold() in {
            candidate.selection_id.casefold(),
            candidate.label.casefold(),
        } or requested_text in {
            str(candidate.container_index),
            str(candidate.container_number),
        }:
            return candidate
    return None


def _source_needs_confirmation(
    ranked: tuple[SubtitleCandidate, ...],
    language_priority: Sequence[str] | str | None,
) -> bool:
    if len(ranked) == 1:
        return (
            ranked[0].subtype is not SubtitleSubtype.EXTERNAL
            or ranked[0].language is None
        )
    first, second = ranked[:2]
    if first.language is None:
        return True
    if first.io_cost != second.io_cost:
        return False
    priorities = _normalise_language_priority(language_priority)
    if _language_rank(first.language, priorities) != _language_rank(
        second.language, priorities
    ):
        return False
    return not (
        first.subtitle_format is not second.subtitle_format
        and {first.subtitle_format, second.subtitle_format}
        == {SubtitleFormat.SRT, SubtitleFormat.ASS}
    )


def _normalise_language_priority(
    language_priority: Sequence[str] | str | None,
) -> tuple[str, ...]:
    if language_priority is None:
        return ()
    values = (
        language_priority.split(",")
        if isinstance(language_priority, str)
        else language_priority
    )
    return tuple(normalize_language(value) for value in values if value.strip())


def _language_rank(
    language: str | None,
    priorities: tuple[str, ...],
) -> tuple[int, int, str]:
    if language is None:
        return (len(priorities) + 1, 1, "")
    normalized = _normalise_discovered_language(language)
    if normalized is None:
        return (len(priorities) + 1, 1, "")
    base = normalized.split("-", 1)[0]
    for index, priority in enumerate(priorities):
        priority_base = priority.split("-", 1)[0]
        if normalized == priority:
            return (index, 0, "")
        if base == priority_base:
            return (index, 1, "")
    return (len(priorities), 1, "")


def _format_rank(subtitle_format: SubtitleFormat) -> int:
    return {
        SubtitleFormat.SRT: 0,
        SubtitleFormat.ASS: 1,
        SubtitleFormat.VTT: 2,
    }[subtitle_format]


def _extraction_cache_path(media: Path, candidate: SubtitleCandidate) -> Path:
    try:
        stat = media.stat()
        media_version = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        media_version = "unknown"
    key_material = "\0".join(
        (
            str(media),
            media_version,
            str(candidate.container_number or candidate.container_index),
            candidate.codec or "",
            candidate.subtitle_format.value,
        )
    ).encode("utf-8")
    digest = hashlib.sha256(key_material).hexdigest()[:16]
    return (
        media.parent
        / ".cueweaver"
        / "extraction"
        / f"{media.stem}.{digest}{candidate.subtitle_format.extension}"
    )


def _write_cached_extraction(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    publish_atomically(content, destination)


def _cache_extracted_path(source: Path, destination: Path) -> None:
    if source == destination:
        return
    try:
        content = source.read_bytes()
    except OSError as error:
        raise ExtractionFailed(
            "Extraction returned an unreadable subtitle path"
        ) from error
    _write_cached_extraction(destination, content)


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
