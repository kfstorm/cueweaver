"""Application contracts for CueWeaver's HTTP subtitle operations."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .subtitles import (
    SubtitleFormat,
    SubtitleValidationError,
    UnsupportedSubtitleFormat,
    validate_subtitle,
    validate_subtitle_pair,
)
from .translation import PySubtransTranslator


@dataclass(frozen=True)
class DiscoverRequest:
    media_path: Path


@dataclass(frozen=True)
class DiscoverResult:
    media_path: Path
    candidates: list[SubtitleCandidateResult] = field(default_factory=list)
    unsupported_candidates: list[UnsupportedCandidateResult] = field(
        default_factory=list
    )


@dataclass(frozen=True)
class SubtitleCandidateResult:
    kind: str
    format: str
    tags: dict[str, str]
    path: Path | None = None
    stream_index: int | None = None


@dataclass(frozen=True)
class UnsupportedCandidateResult:
    kind: str
    reason: str
    path: Path | None = None
    stream_index: int | None = None


@dataclass(frozen=True)
class ExtractRequest:
    media_path: Path
    stream_index: int
    output_path: Path


@dataclass(frozen=True)
class ExtractResult:
    output_path: Path
    format: str


@dataclass(frozen=True)
class TranslateRequest:
    subtitle_path: Path
    target_language_code: str
    output_path: Path
    work_directory: Path
    term_map_path: Path | None = None
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True


@dataclass(frozen=True)
class TranslateResult:
    output_path: Path
    target_language_code: str
    format: str


class ServiceError(Exception):
    """A processing error safe to expose through the HTTP error envelope."""

    def __init__(self, error_code: str, message: str, **context: object) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.context = context


class SubtitleApplication(Protocol):
    def discover(self, request: DiscoverRequest) -> DiscoverResult: ...

    def extract(self, request: ExtractRequest) -> ExtractResult: ...

    def translate(self, request: TranslateRequest) -> TranslateResult: ...


class CueWeaverApplication:
    """Application service for HTTP operations that do not require a Job."""

    def discover(self, request: DiscoverRequest) -> DiscoverResult:
        media_path = request.media_path
        if not media_path.is_file():
            raise ServiceError(
                "media_not_found", "Media does not exist", path=media_path
            )
        try:
            with media_path.open("rb"):
                pass
        except OSError as error:
            raise ServiceError(
                "media_unreadable", "Media cannot be read", path=media_path
            ) from error

        candidates, unsupported = _discover_external_subtitles(media_path)
        streams = _probe_subtitle_streams(media_path)
        for stream in streams:
            stream_index = _stream_index(stream)
            if stream_index is None:
                continue
            codec = str(stream.get("codec_name", "")).casefold()
            if codec in _TEXT_CODEC_FORMATS:
                candidates.append(
                    SubtitleCandidateResult(
                        kind="embedded",
                        stream_index=stream_index,
                        format=_TEXT_CODEC_FORMATS[codec],
                        tags=_stream_tags(stream),
                    )
                )
            else:
                reason = (
                    "bitmap subtitle"
                    if codec in _BITMAP_CODECS
                    else f"unsupported subtitle codec: {codec or 'unknown'}"
                )
                unsupported.append(
                    UnsupportedCandidateResult(
                        kind="embedded", stream_index=stream_index, reason=reason
                    )
                )
        return DiscoverResult(
            media_path=media_path,
            candidates=candidates,
            unsupported_candidates=unsupported,
        )

    def extract(self, request: ExtractRequest) -> ExtractResult:
        media_path = request.media_path
        _require_readable_media(media_path)
        output_format = _output_format(request.output_path)
        _prepare_output_path(request.output_path)

        streams = _probe_subtitle_streams(media_path)
        stream = next(
            (
                stream
                for stream in streams
                if _stream_index(stream) == request.stream_index
            ),
            None,
        )
        if stream is None:
            raise ServiceError(
                "stream_not_found",
                "Embedded subtitle stream was not found",
                stream_index=request.stream_index,
            )
        codec = str(stream.get("codec_name", "")).casefold()
        stream_format = _EXTRACT_CODEC_FORMATS.get(codec)
        if stream_format is None:
            raise ServiceError(
                "unsupported_stream",
                "Embedded subtitle stream is not a supported text format",
                stream_index=request.stream_index,
            )
        if stream_format != output_format:
            raise ServiceError(
                "format_mismatch",
                "Output format must match the Embedded subtitle stream format",
                stream_index=request.stream_index,
            )
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(media_path),
                    "-map",
                    f"0:{request.stream_index}",
                    "-c:s",
                    "copy",
                    str(request.output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise ServiceError("extraction_failed", "ffmpeg failed") from error
        return ExtractResult(output_path=request.output_path, format=output_format)

    def translate(self, request: TranslateRequest) -> TranslateResult:
        subtitle_format = _matching_subtitle_format(
            request.subtitle_path, request.output_path
        )
        source_content = _read_subtitle(request.subtitle_path)
        try:
            validate_subtitle(source_content, subtitle_format)
        except SubtitleValidationError as error:
            raise ServiceError(
                "invalid_subtitle",
                "Subtitle failed validation",
                path=request.subtitle_path,
            ) from error
        _prepare_output_path(request.output_path)
        try:
            request.work_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ServiceError(
                "invalid_work_directory",
                "Work directory cannot be created",
                path=request.work_directory,
            ) from error

        term_map = _load_term_map(request.term_map_path)
        try:
            translated_content = PySubtransTranslator().translate(
                request.subtitle_path,
                request.target_language_code,
                user_overrides=term_map,
                work_directory=request.work_directory,
                dynamic_terminology_enabled=request.dynamic_terminology_enabled,
                subtitle_terminology_filter_enabled=(
                    request.subtitle_terminology_filter_enabled
                ),
            )
        except Exception as error:
            raise ServiceError("translation_failed", "Translation failed") from error
        try:
            validate_subtitle_pair(source_content, translated_content, subtitle_format)
        except SubtitleValidationError as error:
            raise ServiceError(
                "invalid_translation", "Translated subtitle failed validation"
            ) from error
        _write_output(request.output_path, translated_content)
        return TranslateResult(
            output_path=request.output_path,
            target_language_code=request.target_language_code,
            format=subtitle_format.value,
        )


_TEXT_CODEC_FORMATS = {
    "ass": "ass",
    "ssa": "ass",
    "subrip": "srt",
    "srt": "srt",
    "webvtt": "vtt",
    "mov_text": "srt",
    "text": "srt",
    "hdmv_text_subtitle": "srt",
    "substation_alpha": "ass",
}
_EXTRACT_CODEC_FORMATS = {
    "ass": "ass",
    "ssa": "ass",
    "subrip": "srt",
    "srt": "srt",
    "webvtt": "vtt",
}
_BITMAP_CODECS = frozenset({"dvd_subtitle", "hdmv_pgs_subtitle", "pgssub"})
_EXTERNAL_FORMATS = {".srt": "srt", ".ass": "ass", ".vtt": "vtt"}


def _require_readable_media(media_path: Path) -> None:
    if not media_path.is_file():
        raise ServiceError("media_not_found", "Media does not exist", path=media_path)
    try:
        with media_path.open("rb"):
            pass
    except OSError as error:
        raise ServiceError(
            "media_unreadable", "Media cannot be read", path=media_path
        ) from error


def _output_format(output_path: Path) -> str:
    output_format = _EXTERNAL_FORMATS.get(output_path.suffix.casefold())
    if output_format is None:
        raise ServiceError(
            "unsupported_output_format",
            "Output path must use a supported subtitle extension",
            path=output_path,
        )
    return output_format


def _matching_subtitle_format(subtitle_path: Path, output_path: Path) -> SubtitleFormat:
    try:
        input_format = SubtitleFormat.from_path(subtitle_path)
        output_format = SubtitleFormat.from_path(output_path)
    except UnsupportedSubtitleFormat as error:
        raise ServiceError(
            "unsupported_subtitle_format",
            "Subtitle paths must use supported extensions",
        ) from error
    if input_format is not output_format:
        raise ServiceError(
            "format_mismatch", "Input and output subtitle formats must match"
        )
    return input_format


def _read_subtitle(subtitle_path: Path) -> bytes:
    if not subtitle_path.is_file():
        raise ServiceError(
            "subtitle_not_found", "Subtitle does not exist", path=subtitle_path
        )
    try:
        return subtitle_path.read_bytes()
    except OSError as error:
        raise ServiceError(
            "subtitle_unreadable", "Subtitle cannot be read", path=subtitle_path
        ) from error


def _load_term_map(term_map_path: Path | None) -> dict[str, str]:
    if term_map_path is None:
        return {}
    try:
        payload = json.loads(term_map_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ServiceError(
            "invalid_term_map", "Term map cannot be read", path=term_map_path
        ) from error
    if not isinstance(payload, dict) or any(
        not isinstance(source, str)
        or not source
        or not isinstance(target, str)
        or not target
        for source, target in payload.items()
    ):
        raise ServiceError("invalid_term_map", "Term map must map non-empty strings")
    return payload


def _write_output(output_path: Path, content: bytes) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        os.link(temporary_path, output_path)
    except FileExistsError as error:
        raise ServiceError(
            "output_exists", "Output path already exists", path=output_path
        ) from error
    except OSError as error:
        raise ServiceError(
            "output_write_failed", "Output cannot be written", path=output_path
        ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _prepare_output_path(output_path: Path) -> None:
    if output_path.exists():
        raise ServiceError(
            "output_exists", "Output path already exists", path=output_path
        )
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ServiceError(
            "invalid_output_path",
            "Output directory cannot be created",
            path=output_path,
        ) from error


def _discover_external_subtitles(
    media_path: Path,
) -> tuple[list[SubtitleCandidateResult], list[UnsupportedCandidateResult]]:
    candidates: list[SubtitleCandidateResult] = []
    unsupported: list[UnsupportedCandidateResult] = []
    prefix = f"{media_path.stem}."
    try:
        paths = sorted(
            media_path.parent.iterdir(), key=lambda path: path.name.casefold()
        )
    except OSError as error:
        raise ServiceError(
            "media_unreadable", "Media directory cannot be read", path=media_path
        ) from error
    for path in paths:
        if not path.is_file() or (
            path.stem != media_path.stem and not path.stem.startswith(prefix)
        ):
            continue
        subtitle_format = _EXTERNAL_FORMATS.get(path.suffix.casefold())
        if subtitle_format is None:
            continue
        suffix = path.stem[len(media_path.stem) :].lstrip(".")
        language = next((segment for segment in suffix.split(".") if segment), "")
        candidates.append(
            SubtitleCandidateResult(
                kind="external",
                path=path,
                format=subtitle_format,
                tags={"language": language, "title": ""},
            )
        )
    return candidates, unsupported


def _probe_subtitle_streams(media_path: Path) -> list[dict[str, object]]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_streams",
                "-of",
                "json",
                str(media_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, TypeError, ValueError) as error:
        raise ServiceError(
            "discovery_failed", "ffprobe failed", path=media_path
        ) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise ServiceError(
            "discovery_failed", "ffprobe returned invalid container metadata"
        )
    return [stream for stream in payload["streams"] if isinstance(stream, dict)]


def _stream_index(stream: dict[str, object]) -> int | None:
    index = stream.get("index")
    if isinstance(index, bool):
        return None
    if isinstance(index, int):
        return index
    if isinstance(index, str):
        try:
            return int(index)
        except ValueError:
            pass
    return None


def _stream_tags(stream: dict[str, object]) -> dict[str, str]:
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        return {"language": "", "title": ""}
    return {
        "language": str(tags.get("language", "")),
        "title": str(tags.get("title", "")),
    }
