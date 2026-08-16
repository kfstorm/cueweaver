"""Extraction operation and its contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...subtitle_formats import EXTRACT_CODEC_FORMATS, output_format
from ..discovery import MediaProbe
from ..errors import ServiceError
from ..media import require_readable_media, stream_index
from ..output import OutputPublisher


@dataclass(frozen=True)
class ExtractRequest:
    media_path: Path
    stream_index: int
    output_path: Path


@dataclass(frozen=True)
class ExtractResult:
    output_path: Path
    format: str


class MediaExtractor(MediaProbe, Protocol):
    def extract_subtitle(
        self, media_path: Path, stream_index: int, output_path: Path
    ) -> None: ...


class Extraction:
    def __init__(self, media: MediaExtractor, output: OutputPublisher) -> None:
        self._media = media
        self._output = output

    def extract(self, request: ExtractRequest) -> ExtractResult:
        require_readable_media(request.media_path)
        result_format = output_format(request.output_path)
        stream = next(
            (
                stream
                for stream in self._media.probe_subtitle_streams(request.media_path)
                if stream_index(stream) == request.stream_index
            ),
            None,
        )
        if stream is None:
            raise ServiceError(
                "stream_not_found",
                "Embedded subtitle stream was not found",
                stream_index=request.stream_index,
            )
        stream_format = EXTRACT_CODEC_FORMATS.get(
            str(stream.get("codec_name", "")).casefold()
        )
        if stream_format is None:
            raise ServiceError(
                "unsupported_stream",
                "Embedded subtitle stream is not a supported text format",
                stream_index=request.stream_index,
            )
        if stream_format != result_format:
            raise ServiceError(
                "format_mismatch",
                "Output format must match the Embedded subtitle stream format",
                stream_index=request.stream_index,
            )
        self._output.publish(
            request.output_path,
            lambda temporary_path: self._media.extract_subtitle(
                request.media_path, request.stream_index, temporary_path
            ),
        )
        return ExtractResult(request.output_path, result_format)
