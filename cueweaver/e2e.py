"""Test-only production factory for the deterministic Docker release matrix."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI

from .product import create_product_app_from_env


class _FakeTranslator:
    available = True

    def __init__(self) -> None:
        self._attempts: dict[str, int] = {}
        self._lock = threading.Lock()

    def translate(  # noqa: PLR0913
        self,
        source: Path,
        target_language: str,
        *,
        user_overrides: Mapping[str, str] | None = None,
        work_directory: Path,
        dynamic_terminology_enabled: bool = True,
        subtitle_terminology_filter_enabled: bool = True,
    ) -> bytes:
        del user_overrides, work_directory, dynamic_terminology_enabled
        del subtitle_terminology_filter_enabled
        if not source.is_file():
            raise RuntimeError("deterministic fake source is missing")
        with self._lock:
            attempt = self._attempts.get(target_language, 0)
            self._attempts[target_language] = attempt + 1
        if target_language.startswith("e2e-fail"):
            raise RuntimeError("deterministic fake translation failure")
        if target_language.startswith("e2e-retry") and attempt == 0:
            raise RuntimeError("deterministic fake first-attempt failure")
        delay = 5 if target_language.startswith("e2e-interrupted") else 1
        time.sleep(delay)
        return b"1\n00:00:00,000 --> 00:00:01,000\nFake translation\n"


def create_e2e_app_from_env() -> FastAPI:
    """Create the production app with a deterministic injected Translator."""
    return create_product_app_from_env(translator=_FakeTranslator())


__all__ = ["create_e2e_app_from_env"]
