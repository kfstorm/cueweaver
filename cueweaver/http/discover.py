"""HTTP adapter for discovery."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ..application.discovery import (
    DiscoverRequest,
    DiscoverResult,
    SubtitleCandidateResult,
    UnsupportedCandidateResult,
)


class DiscoverBody(BaseModel):
    model_config = {"extra": "forbid"}
    media_path: str = Field(min_length=1)


class DiscoveryOperation(Protocol):
    def discover(self, request: DiscoverRequest) -> DiscoverResult: ...


class DiscoveryApplication(Protocol):
    @property
    def discovery(self) -> DiscoveryOperation: ...


def register_discover(app: FastAPI, application: DiscoveryApplication) -> None:
    @app.post("/api/discover")
    def discover(body: DiscoverBody) -> dict[str, object]:
        return result_body(
            application.discovery.discover(DiscoverRequest(Path(body.media_path)))
        )


def result_body(result: DiscoverResult) -> dict[str, object]:
    return {
        "media_path": str(result.media_path),
        "candidates": [candidate_body(candidate) for candidate in result.candidates],
        "unsupported_candidates": [
            unsupported_candidate_body(candidate)
            for candidate in result.unsupported_candidates
        ],
    }


def candidate_body(candidate: SubtitleCandidateResult) -> dict[str, object]:
    body: dict[str, object] = {
        "kind": candidate.kind,
        "format": candidate.format,
        "tags": candidate.tags,
    }
    add_candidate_location(body, candidate, str)
    return body


def unsupported_candidate_body(
    candidate: UnsupportedCandidateResult,
) -> dict[str, object]:
    body: dict[str, object] = {"kind": candidate.kind, "reason": candidate.reason}
    add_candidate_location(body, candidate, str)
    return body


def add_candidate_location(
    body: dict[str, object],
    candidate: SubtitleCandidateResult | UnsupportedCandidateResult,
    path_formatter: Callable[[Path], str],
) -> None:
    if candidate.path is not None:
        body["path"] = path_formatter(candidate.path)
    if candidate.stream_index is not None:
        body["stream_index"] = candidate.stream_index
