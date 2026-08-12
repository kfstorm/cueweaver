"""Discovery operation and its contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ...subtitle_formats import BITMAP_CODECS, EXTERNAL_FORMATS, TEXT_CODEC_FORMATS
from ..errors import ServiceError
from ..media import require_readable_media, stream_index


@dataclass(frozen=True)
class DiscoverRequest:
    media_path: Path


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
class DiscoverResult:
    media_path: Path
    candidates: list[SubtitleCandidateResult] = field(default_factory=list)
    unsupported_candidates: list[UnsupportedCandidateResult] = field(
        default_factory=list
    )


class MediaProbe(Protocol):
    def probe_subtitle_streams(self, media_path: Path) -> list[dict[str, object]]: ...


class Discovery:
    def __init__(self, media: MediaProbe) -> None:
        self._media = media

    def discover(self, request: DiscoverRequest) -> DiscoverResult:
        require_readable_media(request.media_path)
        candidates, unsupported = _external_subtitles(request.media_path)
        for stream in self._media.probe_subtitle_streams(request.media_path):
            index = stream_index(stream)
            if index is None:
                continue
            codec = str(stream.get("codec_name", "")).casefold()
            if codec in TEXT_CODEC_FORMATS:
                candidates.append(
                    SubtitleCandidateResult(
                        "embedded",
                        TEXT_CODEC_FORMATS[codec],
                        _stream_tags(stream),
                        stream_index=index,
                    )
                )
            else:
                reason = (
                    "bitmap subtitle"
                    if codec in BITMAP_CODECS
                    else f"unsupported subtitle codec: {codec or 'unknown'}"
                )
                unsupported.append(
                    UnsupportedCandidateResult("embedded", reason, stream_index=index)
                )
        return DiscoverResult(request.media_path, candidates, unsupported)


def _external_subtitles(
    media_path: Path,
) -> tuple[list[SubtitleCandidateResult], list[UnsupportedCandidateResult]]:
    candidates: list[SubtitleCandidateResult] = []
    try:
        paths = sorted(
            media_path.parent.iterdir(), key=lambda path: path.name.casefold()
        )
    except OSError as error:
        raise ServiceError(
            "media_unreadable", "Media directory cannot be read", path=media_path
        ) from error
    prefix = f"{media_path.stem}."
    for path in paths:
        if not path.is_file() or (
            path.stem != media_path.stem and not path.stem.startswith(prefix)
        ):
            continue
        subtitle_format = EXTERNAL_FORMATS.get(path.suffix.casefold())
        if subtitle_format is not None:
            suffix = path.stem[len(media_path.stem) :].lstrip(".")
            candidates.append(
                SubtitleCandidateResult(
                    "external",
                    subtitle_format,
                    {
                        "language": next(
                            (part for part in suffix.split(".") if part), ""
                        ),
                        "title": "",
                    },
                    path=path,
                )
            )
    return candidates, []


def _stream_tags(stream: dict[str, object]) -> dict[str, str]:
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        return {"language": "", "title": ""}
    return {
        "language": str(tags.get("language", "")),
        "title": str(tags.get("title", "")),
    }
