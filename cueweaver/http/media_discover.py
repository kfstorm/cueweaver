"""Product-facing, Media-root-relative Discovery HTTP adapter."""

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
from ..application.errors import ServiceError


class MediaDiscoverBody(BaseModel):
    model_config = {"extra": "forbid"}
    path: str = Field(min_length=1)


class MediaDiscoveryOperation(Protocol):
    def discover(self, request: DiscoverRequest) -> DiscoverResult: ...


def register_media_discover(
    app: FastAPI, operation: MediaDiscoveryOperation, media_root: Path
) -> None:
    root = media_root.resolve()

    @app.post("/api/media/discover")
    def discover(body: MediaDiscoverBody) -> dict[str, object]:
        relative = _relative_media_path(body.path)
        media_path = (root / relative).resolve()
        if not media_path.is_relative_to(root):
            raise ServiceError(
                "invalid_media_path",
                "Media path must stay inside Media root",
                path=body.path,
            )
        try:
            result = operation.discover(DiscoverRequest(media_path))
        except ServiceError as error:
            context = dict(error.context)
            if "path" in context:
                context["path"] = body.path
            raise ServiceError(error.error_code, error.message, **context) from error
        return result_body(result, root, relative)


def result_body(
    result: DiscoverResult, media_root: Path, relative_media_path: Path
) -> dict[str, object]:
    return {
        "path": str(relative_media_path),
        "candidates": [
            candidate_body(candidate, media_root) for candidate in result.candidates
        ],
        "unsupported_candidates": [
            unsupported_candidate_body(candidate, media_root)
            for candidate in result.unsupported_candidates
        ],
    }


def candidate_body(
    candidate: SubtitleCandidateResult, media_root: Path
) -> dict[str, object]:
    body: dict[str, object] = {
        "kind": candidate.kind,
        "format": candidate.format,
        "tags": candidate.tags,
    }
    if candidate.path is not None:
        body["path"] = _relative_path(candidate.path, media_root)
    if candidate.stream_index is not None:
        body["stream_index"] = candidate.stream_index
    return body


def unsupported_candidate_body(
    candidate: UnsupportedCandidateResult, media_root: Path
) -> dict[str, object]:
    body: dict[str, object] = {"kind": candidate.kind, "reason": candidate.reason}
    if candidate.path is not None:
        body["path"] = _relative_path(candidate.path, media_root)
    if candidate.stream_index is not None:
        body["stream_index"] = candidate.stream_index
    return body


def _relative_media_path(value: str) -> Path:
    path = Path(value)
    if "\\" in value or "\x00" in value or path.is_absolute() or ".." in path.parts:
        raise ServiceError(
            "invalid_media_path", "Media path must be relative", path=value
        )
    return path


def _relative_path(path: Path, media_root: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(media_root):
        raise ServiceError(
            "invalid_media_path",
            "Discovered subtitle is outside Media root",
        )
    return str(resolved.relative_to(media_root))
