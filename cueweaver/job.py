"""The Job runner seam for CueWeaver's first vertical slice."""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from os import PathLike
from os import replace as atomic_replace
from pathlib import Path
from threading import Event, Lock
from typing import Protocol, TypeVar, cast, runtime_checkable

from .metadata import (
    Glossary,
    GlossaryProvider,
    MetadataCache,
    MetadataContext,
    MetadataError,
    MetadataProvider,
    MetadataRequest,
    MetadataValue,
    SeriesWikidataIdentifierProvider,
    TMDbMetadataProvider,
    WikidataGlossaryProvider,
)
from .overrides import UserOverrideError, UserOverrideStore
from .publishing import publish_atomically
from .subtitles import (
    SubtitleFormat,
    SubtitleValidationError,
    validate_subtitle,
    validate_subtitle_pair,
)
from .trace import TraceWriteError, TraceWriter
from .translation import PySubtransTranslator
from .translation_context import translation_context_instructions
from .workspaces import extraction_cache_path, job_work_directory


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


class SourceSelectionMode(str, Enum):
    EXPLICIT = "explicit"
    AUTOMATIC = "automatic"
    INTERACTIVE = "interactive"


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
        glossary: Glossary | None = None,
        user_overrides: dict[str, str] | None = None,
        work_directory: PathLike[str] | None = None,
    ) -> bytes | str | PathLike[str]:
        """Return translated subtitle content with explicit terminology seeds."""


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


@dataclass(frozen=True)
class SourceSelection:
    mode: SourceSelectionMode
    candidate: SubtitleCandidate
    reason: str | None = None


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
    dynamic_terminology_enabled: bool = True
    error: str | None = None
    intermediate_path: Path | None = None
    translated_content: bytes | None = None
    context: str = ""
    metadata_degradation: str | None = None
    metadata_request: MetadataRequest | None = None
    glossary: Glossary = field(default_factory=Glossary)
    user_overrides: dict[str, str] = field(default_factory=dict)
    trace_path: Path | None = None
    token_usage: dict[str, object] | None = None

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
_ObserverEvent = TypeVar("_ObserverEvent")


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
        if not external or not _is_unreadable_container_error(error):
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


def _is_unreadable_container_error(error: DiscoveryFailed) -> bool:
    cause = error.__cause__
    if isinstance(cause, OSError):
        return True
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
        glossary_provider: GlossaryProvider | None = None,
        metadata_cache: MetadataCache | PathLike[str] | str | None = None,
        user_override_store: UserOverrideStore | None = None,
        user_override_directory: PathLike[str] | str | None = None,
        extractor: SubtitleExtractor | None = None,
        source_selector: Callable[
            [tuple[SubtitleCandidate, ...]],
            SubtitleCandidate,
        ]
        | None = None,
        discovery_observer: Callable[[tuple[SubtitleCandidate, ...]], None]
        | None = None,
        progress_observer: Callable[[JobState], None] | None = None,
        selection_observer: Callable[[SourceSelection], None] | None = None,
        language_priority: Sequence[str] | str | None = None,
    ):
        self._translator = translator
        self._metadata_provider = metadata_provider
        self._glossary_provider = glossary_provider
        self._metadata_cache = (
            metadata_cache
            if isinstance(metadata_cache, MetadataCache)
            else MetadataCache(metadata_cache)
            if metadata_cache is not None
            else None
        )
        configured_override_directory = user_override_directory or os.environ.get(
            "CUEWEAVER_USER_OVERRIDE_DIRECTORY"
        )
        if (
            user_override_store is not None
            and configured_override_directory is not None
        ):
            raise ValueError(
                "Provide either user_override_store or user_override_directory"
            )
        self._user_override_required = (
            user_override_store is not None or configured_override_directory is not None
        )
        self._user_override_store = user_override_store or UserOverrideStore(
            configured_override_directory or _default_user_override_directory()
        )
        self._extractor = extractor or SeconvExtractor()
        self._source_selector = source_selector
        self._discovery_observer = discovery_observer
        self._progress_observer = progress_observer
        self._selection_observer = selection_observer
        self._language_priority = (
            language_priority
            if language_priority is not None
            else os.environ.get("CUEWEAVER_SOURCE_LANGUAGE_PRIORITY")
        )
        self._state_lock = Lock()
        self._cancel_requested = Event()
        self._active_translator: Translator | TranslatorFunction | None = None
        self._active_metadata_provider: object | None = None
        self._intermediate_path: Path | None = None
        self._translated_content: bytes | None = None
        self._job_work_directory: Path | None = None
        self._trace_writer: TraceWriter | None = None
        self._trace_path: Path | None = None
        self._token_usage: dict[str, object] | None = None

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
                staged_path = _stage_translation(
                    content,
                    destination,
                    work_directory=job_work_directory(
                        result.media,
                        result.target_language,
                        result.source.selection_id,
                        dynamic_terminology_enabled=result.dynamic_terminology_enabled,
                    ),
                )
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
            target_language=result.target_language or "",
            refresh=True,
        )
        return replace(
            result,
            context=context.text,
            glossary=context.glossary,
            metadata_degradation=context.degradation,
            metadata_request=context.request,
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
        no_metadata_fetch: bool = False,
        debug: bool = False,
        dynamic_terminology_enabled: bool | None = None,
    ) -> JobResult:
        self._cancel_requested.clear()
        self._intermediate_path = None
        self._translated_content = None
        self._job_work_directory = None
        self._trace_writer = None
        self._trace_path = None
        self._token_usage = None
        self._reset_translator_for_job()
        self._reset_metadata_provider_for_job()
        media_path = Path(media).expanduser().resolve()
        lifecycle: list[JobState] = []
        selected_source: SubtitleCandidate | None = None
        configured_target: str | None = None
        no_op = False
        metadata_context: MetadataContext | None = None
        metadata_request: MetadataRequest | None = None
        user_overrides: dict[str, str] = {}
        translation_context = translation_context_instructions()

        def record_state(state: JobState) -> None:
            lifecycle.append(state)
            try:
                _notify_observer(self._progress_observer, state)
            except JobCanceled:
                if state is not JobState.PUBLISHED:
                    raise

        effective_dynamic_terminology_enabled = True
        try:
            if (
                debug
                and self._translator is not None
                and not isinstance(self._translator, PySubtransTranslator)
            ):
                raise JobError(
                    "Debug tracing requires the built-in PySubtransTranslator"
                )
            effective_dynamic_terminology_enabled = (
                _resolve_dynamic_terminology_enabled(dynamic_terminology_enabled)
            )
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
            language_priority_label = "configured language priority"
            if language_priority is None:
                language_priority = discover_media_primary_language(media_path)
                language_priority_label = "Media language priority"
            record_state(JobState.DISCOVERED)
            if self._discovery_observer is not None:
                _notify_observer(self._discovery_observer, candidates)
            selection = _select_source(
                candidates,
                source,
                media_path.parent,
                language_priority=language_priority,
                language_priority_label=language_priority_label,
                source_selector=self._source_selector,
            )
            selected_source = selection.candidate
            _notify_observer(self._selection_observer, selection)
            self._job_work_directory = job_work_directory(
                media_path,
                configured_target,
                selected_source.selection_id,
                dynamic_terminology_enabled=effective_dynamic_terminology_enabled,
            )
            if debug:
                try:
                    self._trace_writer = TraceWriter.create(self._job_work_directory)
                    self._trace_path = self._trace_writer.path
                except TraceWriteError as error:
                    raise JobError(str(error)) from error
            self._raise_if_canceled()

            source_path = selected_source.path
            if selected_source.subtype is SubtitleSubtype.EMBEDDED:
                record_state(JobState.EXTRACTING)
                selected_label = selected_source.label
                source_path = self._extract_source(media_path, selected_source)
                selected_source = replace(
                    selected_source,
                    path=source_path,
                    display_name=selected_label,
                )
                self._raise_if_canceled()

            effective_source_language = source_language or selected_source.language
            if metadata_request is not None:
                metadata_request = replace(
                    metadata_request,
                    source_language=(
                        normalize_language(effective_source_language)
                        if effective_source_language is not None
                        else None
                    ),
                    target_language=configured_target,
                )
            no_op = languages_match(effective_source_language, configured_target)
            source_content = source_path.read_bytes()
            self._raise_if_canceled()
            if not no_op:
                self._validate_translation_configuration()
            if not no_op and metadata_request is not None and not no_metadata_fetch:
                record_state(JobState.METADATA)
                metadata_context = self._gather_metadata(
                    metadata_request,
                    target_language=configured_target,
                    refresh=refresh_metadata,
                )
                self._raise_if_canceled()
            translation_context = (
                metadata_context.text
                if metadata_context is not None
                else translation_context_instructions()
            )
            user_overrides = self._load_user_overrides(
                metadata_request.series_id if metadata_request else media_path.stem
            )
            if no_op:
                delivered_content = source_content
            else:
                record_state(JobState.TRANSLATING)
                delivered_content = self._translate(
                    source_path,
                    configured_target,
                    context=translation_context,
                    glossary=(
                        metadata_context.glossary
                        if metadata_context is not None
                        else Glossary()
                    ),
                    user_overrides=user_overrides,
                    dynamic_terminology_enabled=effective_dynamic_terminology_enabled,
                )
                self._raise_if_canceled()

            self._translated_content = delivered_content
            self._raise_if_canceled()
            record_state(JobState.VALIDATING)
            validate_subtitle_pair(
                source_content,
                delivered_content,
                selected_source.subtitle_format,
            )

            self._raise_if_canceled()
            record_state(JobState.PUBLISHING)
            published_path = _published_path(
                media_path,
                configured_target,
                selected_source.subtitle_format,
            )
            staged_path = _stage_translation(
                delivered_content,
                published_path,
                work_directory=self._job_work_directory,
            )
            self._intermediate_path = staged_path
            publish_atomically(delivered_content, published_path)
            _discard_staged_translation(staged_path)
            self._intermediate_path = None
            self._translated_content = None
            record_state(JobState.PUBLISHED)
            self._finish_trace("completed", token_usage=self._token_usage)
            return JobResult(
                state=JobState.PUBLISHED,
                lifecycle=tuple(lifecycle),
                media=media_path,
                target_language=configured_target,
                source=selected_source,
                published_path=published_path,
                no_op=no_op,
                dynamic_terminology_enabled=effective_dynamic_terminology_enabled,
                context=translation_context,
                metadata_degradation=(
                    metadata_context.degradation if metadata_context else None
                ),
                metadata_request=(
                    metadata_context.request
                    if metadata_context is not None
                    else metadata_request
                ),
                glossary=(
                    metadata_context.glossary
                    if metadata_context is not None
                    else Glossary()
                ),
                user_overrides=user_overrides,
                trace_path=self._trace_path,
                token_usage=self._token_usage,
            )
        except (JobCanceled, JobError, OSError, SubtitleValidationError) as error:
            terminal_state = (
                JobState.CANCELED if isinstance(error, JobCanceled) else JobState.FAILED
            )
            record_state(terminal_state)
            trace_error: TraceWriteError | None = None
            if self._trace_writer is not None:
                try:
                    self._finish_trace(
                        "canceled" if terminal_state is JobState.CANCELED else "failed",
                        error=str(error),
                        error_type=type(error).__name__,
                        token_usage=self._token_usage,
                    )
                except TraceWriteError as finish_error:
                    trace_error = finish_error
            result_error = str(error)
            if trace_error is not None:
                result_error = f"{result_error}; debug trace failed: {trace_error}"
            return JobResult(
                state=terminal_state,
                lifecycle=tuple(lifecycle),
                media=media_path,
                target_language=configured_target,
                source=selected_source,
                published_path=None,
                no_op=no_op,
                dynamic_terminology_enabled=effective_dynamic_terminology_enabled,
                error=result_error,
                intermediate_path=self._intermediate_path,
                translated_content=self._translated_content,
                context=translation_context,
                metadata_degradation=(
                    metadata_context.degradation if metadata_context else None
                ),
                metadata_request=(
                    metadata_context.request
                    if metadata_context is not None
                    else metadata_request
                ),
                glossary=(
                    metadata_context.glossary
                    if metadata_context is not None
                    else Glossary()
                ),
                user_overrides=user_overrides,
                trace_path=self._trace_path,
                token_usage=self._token_usage,
            )
        finally:
            if self._trace_writer is not None:
                try:
                    self._finish_trace(
                        "failed",
                        error="Job terminated unexpectedly",
                        error_type="UnexpectedJobError",
                        token_usage=self._token_usage,
                    )
                except TraceWriteError:
                    self._trace_writer = None

    def _finish_trace(self, state: str, **payload: object) -> None:
        writer = self._trace_writer
        if writer is None:
            return
        self._trace_writer = None
        writer.finish(state, **payload)

    def _gather_metadata(
        self,
        request: MetadataRequest,
        *,
        target_language: str,
        refresh: bool,
    ) -> MetadataContext:
        provider = self._metadata_provider or TMDbMetadataProvider()
        with self._state_lock:
            self._active_metadata_provider = provider
        try:
            request = self._resolve_metadata_cache_request(request, provider)
            self._raise_if_canceled()
        finally:
            with self._state_lock:
                self._active_metadata_provider = None
        glossary_provider = self._resolve_glossary_provider(provider)
        cache = self._metadata_cache or MetadataCache(_default_metadata_cache_path())
        cached_series: dict[str, MetadataValue] = {}
        cached_episode: dict[str, MetadataValue] = {}
        series_overview: str | None = None
        episode_overview: str | None = None
        series_title_source = ""
        series_overview_source = ""
        series_title_target = ""
        series_overview_target = ""
        episode_title_source = ""
        episode_overview_source = ""
        episode_title_target = ""
        episode_overview_target = ""
        glossary: Glossary | None = None
        degradation: list[str] = []
        context_failed = False
        glossary_cached = False
        if refresh:
            cache.clear_context(request)
        else:
            cached_series, cached_episode = cache.load(request)
            glossary = cache.load_glossary(request, target_language)
            glossary_cached = glossary is not None

        localized = _supports_localized_metadata(provider)
        if localized:
            (
                series_title_source,
                series_overview_source,
                series_title_target,
                series_overview_target,
                series_failed,
            ) = self._gather_localized_entity(
                provider,
                cache,
                request,
                cached_series,
                entity="series",
                degradation=degradation,
            )
            context_failed = context_failed or series_failed
            series_overview = series_overview_source or series_overview_target or None
            if request.episode_key is not None:
                (
                    episode_title_source,
                    episode_overview_source,
                    episode_title_target,
                    episode_overview_target,
                    episode_failed,
                ) = self._gather_localized_entity(
                    provider,
                    cache,
                    request,
                    cached_episode,
                    entity="episode",
                    degradation=degradation,
                )
                context_failed = context_failed or episode_failed
                episode_overview = (
                    episode_overview_source or episode_overview_target or None
                )
        else:
            for attempt in range(2):
                try:
                    with self._state_lock:
                        self._active_metadata_provider = provider
                    self._raise_if_canceled()
                    legacy_series = cached_series.get("legacy")
                    if legacy_series is not None:
                        series_overview = legacy_series.overview
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

                        def fetch_episode_overview(
                            season_number: int = season_number,
                            episode_number: int = episode_number,
                        ) -> str:
                            return provider.get_episode_overview(
                                request.series_id,
                                season_number,
                                episode_number,
                            )

                        legacy_episode = cached_episode.get("legacy")
                        if legacy_episode is not None:
                            episode_overview = legacy_episode.overview
                        if episode_overview is None:
                            episode_overview = _fetch_metadata_overview(
                                fetch_episode_overview,
                                "episode",
                            )
                            cache.store(request, episode_overview=episode_overview)
                    self._raise_if_canceled()
                    break
                except JobCanceled:
                    raise
                except (MetadataError, OSError) as error:
                    if self._cancel_requested.is_set():
                        raise JobCanceled("Job canceled") from error
                    if attempt == 1:
                        context_failed = True
                        degradation.append(f"Metadata degraded: {error}")
                finally:
                    with self._state_lock:
                        self._active_metadata_provider = None

        if (
            glossary is None
            and glossary_provider is not None
            and (
                not context_failed
                or self._glossary_provider is not None
                or series_overview is not None
            )
        ):
            try:
                with self._state_lock:
                    self._active_metadata_provider = glossary_provider
                self._raise_if_canceled()
                glossary = _fetch_metadata_glossary(
                    glossary_provider,
                    request.series_id,
                    target_language,
                )
                cache.store_glossary(
                    request,
                    glossary,
                    target_language=target_language,
                )
                if glossary.is_empty:
                    degradation.append("Glossary degraded: no usable series Terms")
                self._raise_if_canceled()
            except JobCanceled:
                raise
            except (MetadataError, OSError) as error:
                if self._cancel_requested.is_set():
                    raise JobCanceled("Job canceled") from error
                degradation.append(f"Glossary degraded: {error}")
            finally:
                with self._state_lock:
                    self._active_metadata_provider = None

        if glossary_cached and glossary is not None and glossary.is_empty:
            degradation.append("Glossary degraded: no usable series Terms")

        return MetadataContext(
            request=request,
            series_overview=series_overview or "",
            episode_overview=episode_overview or "",
            glossary=glossary or Glossary(),
            degradation="; ".join(degradation) or None,
            series_title_source=series_title_source,
            series_overview_source=series_overview_source,
            series_title_target=series_title_target,
            series_overview_target=series_overview_target,
            episode_title_source=episode_title_source,
            episode_overview_source=episode_overview_source,
            episode_title_target=episode_title_target,
            episode_overview_target=episode_overview_target,
            localized=localized,
        )

    def _gather_localized_entity(
        self,
        provider: MetadataProvider,
        cache: MetadataCache,
        request: MetadataRequest,
        cached: dict[str, MetadataValue],
        *,
        entity: str,
        degradation: list[str],
    ) -> tuple[str, str, str, str, bool]:
        values: list[tuple[str, str]] = []
        failed = False
        for role, language in (
            ("source", request.source_language),
            ("target", request.target_language),
        ):
            if language is None:
                values.extend((("", ""), ("", "")))
                continue
            for metadata_field in ("title", "overview"):
                cached_metadata = cached.get(language)
                cached_value = (
                    getattr(cached_metadata, metadata_field)
                    if cached_metadata is not None
                    else ""
                )
                if cached_value:
                    values.append((role, cached_value))
                    continue
                method = getattr(provider, f"get_{entity}_{metadata_field}", None)
                if not callable(method):
                    failed = True
                    degradation.append(
                        "Metadata degraded: Metadata provider does not support "
                        f"localized {entity} {metadata_field} ({role}: {language})"
                    )
                    values.append((role, ""))
                    continue
                args: tuple[object, ...] = (request.series_id,)
                if entity == "episode":
                    assert request.season_number is not None
                    assert request.episode_number is not None
                    args += (request.season_number, request.episode_number)
                try:
                    value = self._fetch_localized_metadata_field(
                        method,
                        args,
                        language,
                        f"{entity} {metadata_field} ({role}: {language})",
                        provider,
                    )
                except JobCanceled:
                    raise
                except MetadataError as error:
                    failed = True
                    degradation.append(f"Metadata degraded: {error}")
                    value = ""
                values.append((role, value))
                if value:
                    cache.store(
                        request,
                        language=language,
                        **{
                            f"{entity}_{metadata_field}": value,
                        },
                    )

        source_title = values[0][1]
        source_overview = values[1][1]
        target_title = values[2][1]
        target_overview = values[3][1]
        return (
            source_title,
            source_overview,
            target_title,
            target_overview,
            failed,
        )

    def _fetch_localized_metadata_field(
        self,
        method: Callable[..., object],
        args: tuple[object, ...],
        language: str,
        label: str,
        provider: object,
    ) -> str:
        for attempt in range(2):
            try:
                with self._state_lock:
                    self._active_metadata_provider = provider
                self._raise_if_canceled()
                value = _call_metadata_method(method, args, language)
                if not isinstance(value, str) or not value.strip():
                    raise MetadataError(
                        f"Metadata provider returned no localized {label}"
                    )
                return value
            except JobCanceled:
                raise
            except Exception as error:
                if self._cancel_requested.is_set():
                    raise JobCanceled("Job canceled") from error
                if attempt == 1:
                    raise MetadataError(str(error)) from error
            finally:
                with self._state_lock:
                    self._active_metadata_provider = None
        raise MetadataError(f"Metadata provider returned no {label}")

    def _resolve_metadata_cache_request(
        self,
        request: MetadataRequest,
        metadata_provider: MetadataProvider,
    ) -> MetadataRequest:
        if request.cache_key is not None:
            return request

        for provider in (metadata_provider, self._glossary_provider):
            if provider is None:
                continue
            get_series_wikidata_id = getattr(provider, "get_series_wikidata_id", None)
            if not callable(get_series_wikidata_id):
                continue
            try:
                series_qid = get_series_wikidata_id(request.series_id)
            except JobCanceled:
                raise
            except (MetadataError, OSError, ValueError):
                continue
            if isinstance(series_qid, str) and re.fullmatch(
                r"Q[1-9][0-9]*", series_qid.strip()
            ):
                return replace(request, cache_key=series_qid.strip())
        return request

    def _resolve_glossary_provider(
        self,
        metadata_provider: MetadataProvider,
    ) -> GlossaryProvider | None:
        if self._glossary_provider is not None:
            return self._glossary_provider
        get_glossary = getattr(metadata_provider, "get_glossary", None)
        if callable(get_glossary):
            return cast(GlossaryProvider, metadata_provider)
        get_series_wikidata_id = getattr(
            metadata_provider,
            "get_series_wikidata_id",
            None,
        )
        if callable(get_series_wikidata_id):
            return WikidataGlossaryProvider(
                series_identity_provider=cast(
                    SeriesWikidataIdentifierProvider,
                    metadata_provider,
                )
            )
        if self._metadata_provider is None:
            return WikidataGlossaryProvider()
        return None

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
        glossary: Glossary | None = None,
        user_overrides: dict[str, str] | None = None,
        dynamic_terminology_enabled: bool = True,
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
                glossary=glossary or Glossary(),
                user_overrides=user_overrides or {},
                work_directory=self._job_work_directory,
                trace_writer=self._trace_writer,
                dynamic_terminology_enabled=dynamic_terminology_enabled,
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
            token_usage = getattr(translator, "token_usage", None)
            self._token_usage = (
                dict(token_usage) if isinstance(token_usage, dict) else None
            )
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

    def _validate_translation_configuration(self) -> None:
        translator = self._translator
        try:
            if translator is None:
                translator = PySubtransTranslator()
                self._translator = translator
            validate = getattr(translator, "validate_configuration", None)
            if callable(validate):
                validate()
        except Exception as error:
            raise TranslationFailed(
                f"Translation provider configuration failed: {error}"
            ) from error

    def _load_user_overrides(self, series_scope: str) -> dict[str, str]:
        try:
            return self._user_override_store.load(
                series_scope,
                required=self._user_override_required,
            )
        except UserOverrideError as error:
            raise JobError(f"User override failed: {error}") from error

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
        providers = (self._metadata_provider, self._glossary_provider)
        for provider in providers:
            if provider is None:
                continue
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


def _resolve_dynamic_terminology_enabled(value: bool | None) -> bool:
    if value is not None:
        if type(value) is not bool:
            raise JobError("dynamic_terminology_enabled must be a bool or None")
        return value

    configured = os.environ.get("CUEWEAVER_DYNAMIC_TERMINOLOGY_MAP")
    if configured is None:
        return True
    normalized = configured.strip().casefold()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise JobError(
        "CUEWEAVER_DYNAMIC_TERMINOLOGY_MAP must be one of true, false, yes, no, 1, or 0"
    )


def _default_user_override_directory() -> Path:
    configured = os.environ.get("CUEWEAVER_USER_OVERRIDE_DIRECTORY")
    if configured:
        return Path(configured).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "cueweaver" / "overrides"


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


def _supports_localized_metadata(provider: object) -> bool:
    for entity in ("series", "episode"):
        for metadata_field in ("title", "overview"):
            method = getattr(provider, f"get_{entity}_{metadata_field}", None)
            if callable(method) and _method_accepts_language(method):
                return True
    return False


def _method_accepts_language(method: Callable[..., object]) -> bool:
    try:
        parameters = tuple(inspect.signature(method).parameters.values())
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "language"
        or parameter.kind is parameter.VAR_POSITIONAL
        or parameter.kind is parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _call_metadata_method(
    method: Callable[..., object],
    args: tuple[object, ...],
    language: str,
) -> object:
    try:
        parameters = tuple(inspect.signature(method).parameters.values())
    except (TypeError, ValueError):
        return method(*args, language)
    language_parameter = next(
        (parameter for parameter in parameters if parameter.name == "language"),
        None,
    )
    if language_parameter is not None and (
        language_parameter.kind is language_parameter.KEYWORD_ONLY
    ):
        return method(*args, language=language)
    return method(*args, language)


def _validate_metadata_overview(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise MetadataError(f"Metadata provider returned an invalid {label} overview")
    return value


def _fetch_metadata_glossary(
    provider: GlossaryProvider,
    series_id: str,
    target_language: str,
) -> Glossary:
    try:
        value = provider.get_glossary(series_id, target_language)
    except JobCanceled:
        raise
    except Exception as error:
        raise MetadataError(str(error)) from error
    if not isinstance(value, Glossary):
        raise MetadataError("Glossary provider returned an invalid Glossary")
    return value


def _call_translator(
    translator: Translator | TranslatorFunction,
    source: Path,
    target_language: str,
    *,
    context: str,
    glossary: Glossary,
    user_overrides: dict[str, str],
    work_directory: Path | None,
    trace_writer: TraceWriter | None,
    dynamic_terminology_enabled: bool,
) -> bytes | str | PathLike[str]:
    method = cast(
        Callable[..., bytes | str | PathLike[str]],
        translator if callable(translator) else translator.translate,
    )
    kwargs: dict[str, object] = {}
    if _accepts_parameter(method, "context"):
        kwargs["context"] = context
    if _accepts_parameter(method, "glossary"):
        kwargs["glossary"] = glossary
    if _accepts_parameter(method, "user_overrides"):
        kwargs["user_overrides"] = user_overrides
    if work_directory is not None and _accepts_parameter(method, "work_directory"):
        kwargs["work_directory"] = work_directory
    if trace_writer is not None and _accepts_parameter(method, "trace_writer"):
        kwargs["trace_writer"] = trace_writer
    if _accepts_parameter(method, "dynamic_terminology_enabled"):
        kwargs["dynamic_terminology_enabled"] = dynamic_terminology_enabled
    return method(source, target_language, **kwargs)


def _accepts_parameter(method: Callable[..., object], name: str) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _infer_language(suffix: str) -> str | None:
    tokens = [token for token in re.split(r"[^A-Za-z_-]+", suffix) if token]
    for token in tokens:
        language = _normalise_discovered_language(token)
        if language is not None:
            return language
    return None


def _notify_observer(
    observer: Callable[[_ObserverEvent], None] | None,
    event: _ObserverEvent,
) -> None:
    if observer is None:
        return
    try:
        observer(event)
    except JobCanceled:
        raise
    except Exception:  # noqa: BLE001 - observer failures must not alter the Job
        return


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
    language_priority_label: str = "language priority",
    source_selector: Callable[
        [tuple[SubtitleCandidate, ...]],
        SubtitleCandidate,
    ]
    | None = None,
) -> SourceSelection:
    if source is not None:
        requested_source = _resolve_source_reference(
            candidates, source, media_directory
        )
        if requested_source is None:
            raise _selection_error_with_candidates(
                f"Source was not discovered: {source}", candidates
            )
        if not requested_source.selectable:
            raise _selection_error_with_candidates(
                "Bitmap Sources are visible but disabled and cannot be selected",
                candidates,
            )
        return SourceSelection(SourceSelectionMode.EXPLICIT, requested_source)

    eligible = tuple(candidate for candidate in candidates if candidate.selectable)
    if not eligible:
        if candidates:
            raise _selection_error_with_candidates(
                "No eligible Source found: the discovered candidates are Bitmap "
                "subtitles, which are visible but disabled; Subtitle OCR is not "
                "available in v0.1",
                candidates,
            )
        raise SourceSelectionError(
            "No eligible Source found beside the Media "
            "(supported formats: SRT, ASS, VTT)"
        )

    ranked = rank_subtitle_candidates(eligible, language_priority)
    if _source_needs_confirmation(ranked, language_priority):
        if source_selector is None:
            raise _selection_error_with_candidates(
                "Explicit Source selection is required; choose one with --source",
                candidates,
            )
        try:
            selected_reference = source_selector(candidates)
        except KeyboardInterrupt as error:
            raise JobCanceled("Job canceled") from error
        except SourceSelectionError as error:
            raise _selection_error_with_candidates(str(error), candidates) from error
        selected_source = _resolve_source_reference(
            candidates, selected_reference, media_directory
        )
        if selected_source is None:
            raise _selection_error_with_candidates(
                "Interactive Source selection returned an undiscovered Source",
                candidates,
            )
        if not selected_source.selectable:
            raise _selection_error_with_candidates(
                "Bitmap Sources are visible but disabled and cannot be selected",
                candidates,
            )
        return SourceSelection(SourceSelectionMode.INTERACTIVE, selected_source)

    return SourceSelection(
        SourceSelectionMode.AUTOMATIC,
        ranked[0],
        _selection_reason(ranked, language_priority, language_priority_label),
    )


def format_candidates(
    candidates: Sequence[SubtitleCandidate],
    *,
    heading: str = "Discovered Sources",
) -> str:
    lines = [heading]
    for index, candidate in enumerate(candidates, start=1):
        lines.append(format_candidate(candidate, index))
    return "\n".join(lines)


def format_candidate(candidate: SubtitleCandidate, index: int) -> str:
    if candidate.subtype is SubtitleSubtype.BITMAP:
        status = "disabled; needs Subtitle OCR"
    elif candidate.subtype is SubtitleSubtype.EMBEDDED:
        status = "needs Extraction"
    else:
        status = "ready"
    return (
        f"  {index}. {candidate.label} "
        f"[{candidate.subtype.value}, I/O cost {candidate.io_cost}; {status}]"
    )


def _selection_error_with_candidates(
    message: str,
    candidates: Sequence[SubtitleCandidate],
) -> SourceSelectionError:
    return SourceSelectionError(
        f"{message}\n{format_candidates(candidates, heading='Available Sources')}"
    )


def _selection_reason(
    ranked: tuple[SubtitleCandidate, ...],
    language_priority: Sequence[str] | str | None,
    language_priority_label: str,
) -> str:
    if len(ranked) == 1:
        return "only eligible Source"
    first, second = ranked[:2]
    if first.io_cost != second.io_cost:
        return "lowest I/O cost"
    priorities = _normalise_language_priority(language_priority)
    if _language_rank(first.language, priorities) != _language_rank(
        second.language, priorities
    ):
        return f"{language_priority_label}: {first.language}"
    return "deterministic ranking"


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
    return _language_rank(first.language, priorities) == _language_rank(
        second.language, priorities
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
    return extraction_cache_path(
        media,
        track_identity=str(candidate.container_number or candidate.container_index),
        codec=candidate.codec,
        extension=candidate.subtitle_format.extension,
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


def _stage_translation(
    content: bytes,
    destination: Path,
    *,
    work_directory: Path | None,
) -> Path:
    """Persist a complete translation in the Job workspace for retry."""

    publishing_directory = (
        work_directory
        or job_work_directory(
            destination,
            "unknown",
            destination.name,
        )
    ) / "publishing"
    publishing_directory.mkdir(parents=True, exist_ok=True)
    staged_path = publishing_directory / f"{destination.name}.pending"
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{staged_path.name}.",
            suffix=".tmp",
            dir=publishing_directory,
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
