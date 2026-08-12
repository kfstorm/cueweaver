"""FastAPI routing and shared error adapters."""

from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..application.errors import ServiceError
from .discover import DiscoveryOperation, register_discover
from .extract import ExtractionOperation, register_extract
from .translate import TranslationOperation, register_translate


class Application(Protocol):
    discovery: DiscoveryOperation
    extraction: ExtractionOperation
    translation: TranslationOperation


def create_app(application: Application) -> FastAPI:
    """Create the HTTP service without coupling it to CLI startup."""
    app = FastAPI()
    app.add_exception_handler(ServiceError, service_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    register_discover(app, application)
    register_extract(app, application)
    register_translate(app, application)
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
