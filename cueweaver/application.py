"""Application contracts for CueWeaver's HTTP subtitle operations."""

from __future__ import annotations

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
