"""Runnable CueWeaver Web product composition root."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .application import CueWeaverApplication
from .application.translation import Translator
from .http import create_app
from .http.app import http_error_handler
from .translation import PySubtransTranslator
from .work import WorkRoot

MEDIA_ROOT_ENV = "CUEWEAVER_MEDIA_ROOT"
WORK_ROOT_ENV = "CUEWEAVER_WORK_ROOT"
STATIC_ROOT = Path(__file__).parent / "static"
PROVIDER_MESSAGE = (
    "Set PROVIDER and the matching provider environment variables, then restart "
    "CueWeaver."
)


def create_product_app(
    media_root: Path,
    work_root: Path,
    translator: Translator,
    *,
    static_root: Path | None = None,
) -> FastAPI:
    """Create the complete product with validated roots and injected translation."""
    app = _create_api_app(media_root, work_root, translator)
    static_root = _validate_static_root(static_root or STATIC_ROOT)

    async def product_not_found(request: Request, _error: Exception) -> Response:
        if _is_api_path(request.url.path):
            return _api_not_found()
        if request.method not in {"GET", "HEAD"}:
            return await http_error_handler(request, _error)
        asset = (static_root / request.url.path.removeprefix("/")).resolve()
        if (
            request.url.path != "/"
            and asset.is_relative_to(static_root)
            and asset.is_file()
        ):
            return FileResponse(asset)
        return FileResponse(static_root / "index.html", media_type="text/html")

    app.add_exception_handler(404, product_not_found)

    return app


def create_development_app_from_env(*, translator: Translator | None = None) -> FastAPI:
    """Create the API-only app used behind the Vite development server."""
    media_root, work_root, configured_translator = _configured_product_inputs(
        translator
    )
    return _create_api_app(media_root, work_root, configured_translator)


def _create_api_app(
    media_root: Path, work_root: Path, translator: Translator
) -> FastAPI:
    """Create the shared product API without choosing a frontend delivery mode."""
    media_root = _require_absolute(media_root, "Media root")
    work_root = _require_absolute(work_root, "Work root")
    _validate_media_root(media_root)
    WorkRoot(work_root).prepare()

    application = CueWeaverApplication(
        translator, work_root=work_root, media_root=media_root
    )
    app = create_app(application, media_root)
    app.add_exception_handler(404, api_not_found_handler)
    provider_ready = _provider_available(translator)

    @app.get("/api/status")
    def product_status() -> dict[str, object]:
        provider: dict[str, object] = {"ready": provider_ready}
        if not provider_ready:
            message = getattr(translator, "availability_message", None)
            provider["message"] = (
                message if isinstance(message, str) else PROVIDER_MESSAGE
            )
        return {
            "api": {"ready": True},
            "roots": {"ready": _roots_ready(media_root, work_root)},
            "translation_provider": provider,
            "worker": {"ready": True, "mode": "single"},
        }

    return app


def create_product_app_from_env(
    *, translator: Translator | None = None, static_root: Path | None = None
) -> FastAPI:
    """Create the product from its required process configuration."""
    media_root, work_root, configured_translator = _configured_product_inputs(
        translator
    )
    return create_product_app(
        media_root, work_root, configured_translator, static_root=static_root
    )


def _api_not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error_code": "not_found", "message": "Resource not found"},
    )


async def api_not_found_handler(request: Request, error: Exception) -> Response:
    if _is_api_path(request.url.path):
        return _api_not_found()
    return await http_error_handler(request, error)


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _configured_product_inputs(
    translator: Translator | None,
) -> tuple[Path, Path, Translator]:
    media_root = _root_from_env(MEDIA_ROOT_ENV)
    work_root = _root_from_env(WORK_ROOT_ENV)
    if translator is not None:
        configured_translator = translator
    else:
        configured_translator = PySubtransTranslator()
    return media_root, work_root, configured_translator


def _root_from_env(name: str) -> Path:
    value = os.environ.get(name)
    if value is None or not value:
        raise ValueError(f"{name} is required")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


def _require_absolute(path: Path, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve()


def _validate_media_root(media_root: Path) -> None:
    try:
        if not media_root.is_dir() or not os.access(media_root, os.R_OK):
            raise OSError
        with os.scandir(media_root):
            pass
    except OSError as error:
        raise ValueError("Media root must be a readable directory") from error


def _roots_ready(media_root: Path, work_root: Path) -> bool:
    return _directory_ready(media_root, os.R_OK) and _directory_ready(
        work_root, os.R_OK | os.W_OK
    )


def _directory_ready(path: Path, access: int) -> bool:
    try:
        if not path.is_dir() or not os.access(path, access | os.X_OK):
            return False
        with os.scandir(path):
            pass
    except OSError:
        return False
    return True


def _validate_static_root(static_root: Path) -> Path:
    static_root = Path(static_root).resolve()
    if not (static_root / "index.html").is_file():
        raise ValueError("Built Web product is missing")
    return static_root


def _provider_available(translator: Translator) -> bool:
    return translator.available


__all__ = [
    "create_development_app_from_env",
    "create_product_app",
    "create_product_app_from_env",
]
