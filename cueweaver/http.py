"""FastAPI adapter for CueWeaver's application layer."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .application import (
    DiscoverRequest,
    DiscoverResult,
    ExtractRequest,
    ExtractResult,
    ServiceError,
    SubtitleApplication,
    SubtitleCandidateResult,
    TranslateRequest,
    TranslateResult,
    UnsupportedCandidateResult,
)


class DiscoverBody(BaseModel):
    media_path: str = Field(min_length=1)


class ExtractBody(BaseModel):
    media_path: str = Field(min_length=1)
    stream_index: int
    output_path: str = Field(min_length=1)


class TranslateBody(BaseModel):
    subtitle_path: str = Field(min_length=1)
    target_language_code: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    work_directory: str = Field(min_length=1)
    term_map_path: str | None = Field(default=None, min_length=1)
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True


async def unexpected_error_handler(
    _request: Request, _error: Exception
) -> JSONResponse:
    return _error_response(ServiceError("internal_error", "Operation failed"))


def create_app(application: SubtitleApplication) -> FastAPI:
    """Create the HTTP service without coupling it to CLI startup."""

    app = FastAPI()

    app.add_exception_handler(ServiceError, _service_error_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

    @app.post("/api/discover")
    def discover(body: DiscoverBody) -> dict[str, object]:
        return _result_body(
            application.discover(DiscoverRequest(Path(body.media_path)))
        )

    @app.post("/api/extract")
    def extract(body: ExtractBody) -> dict[str, object]:
        return _result_body(
            application.extract(
                ExtractRequest(
                    media_path=Path(body.media_path),
                    stream_index=body.stream_index,
                    output_path=Path(body.output_path),
                )
            )
        )

    @app.post("/api/translate")
    def translate(body: TranslateBody) -> dict[str, object]:
        return _result_body(
            application.translate(
                TranslateRequest(
                    subtitle_path=Path(body.subtitle_path),
                    target_language_code=body.target_language_code,
                    output_path=Path(body.output_path),
                    work_directory=Path(body.work_directory),
                    term_map_path=(
                        Path(body.term_map_path)
                        if body.term_map_path is not None
                        else None
                    ),
                    dynamic_terminology_enabled=body.dynamic_terminology_enabled,
                    subtitle_terminology_filter_enabled=(
                        body.subtitle_terminology_filter_enabled
                    ),
                )
            )
        )

    return app


async def _service_error_handler(_request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, ServiceError):
        return _error_response(error)
    return _error_response(ServiceError("internal_error", "Operation failed"))


async def _request_validation_error_handler(
    _request: Request, error: Exception
) -> JSONResponse:
    if isinstance(error, RequestValidationError):
        field = error.errors()[0]["loc"][-1]
        return _error_response(
            ServiceError(
                "invalid_request", "Request validation failed", field=str(field)
            )
        )
    return _error_response(ServiceError("internal_error", "Operation failed"))


async def _http_error_handler(_request: Request, _error: Exception) -> JSONResponse:
    return _error_response(ServiceError("invalid_request", "Request failed"))


def _result_body(
    result: DiscoverResult | ExtractResult | TranslateResult,
) -> dict[str, object]:
    if isinstance(result, DiscoverResult):
        return {
            "media_path": str(result.media_path),
            "candidates": [
                _candidate_body(candidate) for candidate in result.candidates
            ],
            "unsupported_candidates": [
                _unsupported_candidate_body(candidate)
                for candidate in result.unsupported_candidates
            ],
        }
    if isinstance(result, ExtractResult):
        return {"output_path": str(result.output_path), "format": result.format}
    return {
        "output_path": str(result.output_path),
        "target_language_code": result.target_language_code,
        "format": result.format,
    }


def _candidate_body(candidate: SubtitleCandidateResult) -> dict[str, object]:
    body: dict[str, object] = {
        "kind": candidate.kind,
        "format": candidate.format,
        "tags": candidate.tags,
    }
    if candidate.path is not None:
        body["path"] = str(candidate.path)
    if candidate.stream_index is not None:
        body["stream_index"] = candidate.stream_index
    return body


def _unsupported_candidate_body(
    candidate: UnsupportedCandidateResult,
) -> dict[str, object]:
    body: dict[str, object] = {"kind": candidate.kind, "reason": candidate.reason}
    if candidate.path is not None:
        body["path"] = str(candidate.path)
    if candidate.stream_index is not None:
        body["stream_index"] = candidate.stream_index
    return body


def _error_response(error: ServiceError) -> JSONResponse:
    body: dict[str, object] = {"error_code": error.error_code, "message": error.message}
    body.update(
        {
            key: str(value) if isinstance(value, Path) else value
            for key, value in error.context.items()
        }
    )
    return JSONResponse(status_code=400, content=body)
