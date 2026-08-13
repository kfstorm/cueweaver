"""Runnable CueWeaver Web product composition root."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

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
    media_root = _require_absolute(media_root, "Media root")
    work_root = _require_absolute(work_root, "Work root")
    _validate_media_root(media_root)
    _prepare_work_root(work_root)
    static_root = _validate_static_root(static_root or STATIC_ROOT)

    app = create_app(CueWeaverApplication(translator))
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

    @app.get("/{client_path:path}", include_in_schema=False)
    def spa(client_path: str) -> FileResponse:
        if client_path.startswith("api/"):
            raise HTTPException(status_code=404)
        asset = (static_root / client_path).resolve()
        if client_path and asset.is_relative_to(static_root) and asset.is_file():
            return FileResponse(asset)
        return FileResponse(static_root / "index.html", media_type="text/html")

    return app


def create_product_app_from_env(
    *, translator: Translator | None = None, static_root: Path | None = None
) -> FastAPI:
    """Create the product from its required process configuration."""
    media_root = _root_from_env(MEDIA_ROOT_ENV)
    work_root = _root_from_env(WORK_ROOT_ENV)
    configured_translator = PySubtransTranslator() if translator is None else translator
    return create_product_app(
        media_root, work_root, configured_translator, static_root=static_root
    )


def run() -> None:
    """Run the officially supported single-worker ASGI server."""
    uvicorn.run(create_product_app_from_env(), host="0.0.0.0", port=8000, workers=1)


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


__all__ = ["create_product_app", "create_product_app_from_env", "run"]
