"""Runnable CueWeaver Web product composition root."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

from .application import CueWeaverApplication
from .application.translation import Translator
from .http import create_app
from .translation import PySubtransTranslator

MEDIA_ROOT_ENV = "CUEWEAVER_MEDIA_ROOT"
WORK_ROOT_ENV = "CUEWEAVER_WORK_ROOT"
STATIC_ROOT = Path(__file__).parent / "static"
PROVIDER_MESSAGE = (
    "Configure a provider in PySubtrans service settings, then restart CueWeaver."
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

    @app.get("/{client_path:path}", include_in_schema=False)
    def spa(client_path: str) -> Response:
        if client_path == "api" or client_path.startswith("api/"):
            return _api_not_found()
        asset = (static_root / client_path).resolve()
        if client_path and asset.is_relative_to(static_root) and asset.is_file():
            return FileResponse(asset)
        return FileResponse(static_root / "index.html", media_type="text/html")

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
    _prepare_work_root(work_root)

    app = create_app(CueWeaverApplication(translator, media_root))
    provider_ready = _provider_available(translator)

    @app.get("/api/status")
    def product_status() -> dict[str, object]:
        provider: dict[str, object] = {"ready": provider_ready}
        if not provider_ready:
            provider["message"] = PROVIDER_MESSAGE
        return {
            "api": {"ready": True},
            "roots": {"ready": True},
            "translation_provider": provider,
            "worker": {"ready": True, "mode": "single"},
        }

    @app.api_route(
        "/api",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def api_root_not_found() -> JSONResponse:
        return _api_not_found()

    @app.api_route(
        "/api/{api_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def api_path_not_found(api_path: str) -> JSONResponse:
        del api_path
        return _api_not_found()

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


def _configured_product_inputs(
    translator: Translator | None,
) -> tuple[Path, Path, Translator]:
    media_root = _root_from_env(MEDIA_ROOT_ENV)
    work_root = _root_from_env(WORK_ROOT_ENV)
    configured_translator = PySubtransTranslator() if translator is None else translator
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


def _prepare_work_root(work_root: Path) -> None:
    try:
        work_root.mkdir(parents=True, exist_ok=True)
        if not work_root.is_dir():
            raise OSError
        with tempfile.TemporaryDirectory(
            prefix=".cueweaver-check-", dir=work_root
        ) as raw:
            probe_directory = Path(raw)
            source = probe_directory / "source"
            destination = probe_directory / "destination"
            source.write_bytes(b"ready")
            if source.read_bytes() != b"ready":
                raise OSError
            destination.write_bytes(b"replace")
            os.replace(source, destination)
            if destination.read_bytes() != b"ready":
                raise OSError
    except OSError as error:
        raise ValueError(
            "Work root must support reading, writing, directory creation, and atomic replacement"
        ) from error


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
