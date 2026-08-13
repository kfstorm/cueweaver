"""FastAPI routing and shared error adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..application.errors import ServiceError
from ..application.term_maps import TermMaps
from .browse import BrowseOperation, register_browse
from .discover import DiscoveryOperation, register_discover
from .extract import ExtractionOperation, register_extract
from .jobs import JobsOperation, register_jobs
from .media_discover import register_media_discover
from .term_maps import register_term_maps
from .translate import TranslationOperation, register_translate

BUSINESS_ROUTES = frozenset(
    {
        "/api/discover",
        "/api/extract",
        "/api/translate",
        "/api/term-maps",
        "/api/media/browse",
        "/api/media/discover",
        "/api/jobs",
    }
)


class Application(Protocol):
    @property
    def discovery(self) -> DiscoveryOperation: ...

    @property
    def extraction(self) -> ExtractionOperation: ...

    @property
    def translation(self) -> TranslationOperation: ...

    @property
    def term_maps(self) -> TermMaps: ...

    @property
    def browsing(self) -> BrowseOperation | None: ...

    @property
    def jobs(self) -> JobsOperation: ...


def create_app(application: Application, media_root: Path | None = None) -> FastAPI:
    """Create the HTTP service without coupling it to CLI startup."""
    app = FastAPI()
    app.add_exception_handler(ServiceError, service_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

    @app.middleware("http")
    async def require_json_content_type(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        term_map_path = request.url.path.removeprefix("/api/term-maps/")
        is_term_map_mutation = (
            request.method in {"PATCH", "PUT", "DELETE"}
            and bool(term_map_path)
            and "/" not in term_map_path
        )
        if (
            request.method == "POST"
            and bool(term_map_path)
            and "/" not in term_map_path
        ):
            return JSONResponse(
                status_code=404,
                content={"error_code": "not_found", "message": "Resource not found"},
            )
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and (
            request.url.path in BUSINESS_ROUTES or is_term_map_mutation
        ):
            content_type = request.headers.get("content-type", "")
            if (
                content_type.split(";", maxsplit=1)[0].strip().casefold()
                != "application/json"
            ):
                return error_response(
                    ServiceError("invalid_request", "Request must use application/json")
                )
        return await call_next(request)

    register_discover(app, application)
    register_extract(app, application)
    register_translate(app, application)
    register_term_maps(app, application)
    if getattr(application, "browsing", None) is not None:
        register_browse(app, application)
    if media_root is not None:
        register_media_discover(app, application.discovery, media_root)
    if getattr(application, "jobs", None) is not None:
        register_jobs(app, application)
    return app


async def unexpected_error_handler(
    _request: Request, _error: Exception
) -> JSONResponse:
    return error_response(ServiceError("internal_error", "Operation failed"))


async def service_error_handler(_request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, ServiceError):
        return error_response(error)
    return error_response(ServiceError("internal_error", "Operation failed"))


async def request_validation_error_handler(
    _request: Request, error: Exception
) -> JSONResponse:
    if isinstance(error, RequestValidationError):
        return error_response(
            ServiceError(
                "invalid_request",
                "Request validation failed",
                field=str(error.errors()[0]["loc"][-1]),
            )
        )
    return error_response(ServiceError("internal_error", "Operation failed"))


async def http_error_handler(_request: Request, _error: Exception) -> JSONResponse:
    return error_response(ServiceError("invalid_request", "Request failed"))


def error_response(error: ServiceError) -> JSONResponse:
    body: dict[str, object] = {"error_code": error.error_code, "message": error.message}
    body.update(
        {
            key: str(value) if hasattr(value, "__fspath__") else value
            for key, value in error.context.items()
        }
    )
    return JSONResponse(status_code=400, content=body)
