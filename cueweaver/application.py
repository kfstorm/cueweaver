"""Application contracts for CueWeaver's HTTP subtitle operations."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


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
_BITMAP_CODECS = frozenset({"dvd_subtitle", "hdmv_pgs_subtitle", "pgssub"})
_EXTERNAL_FORMATS = {".srt": "srt", ".ass": "ass", ".vtt": "vtt"}


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
