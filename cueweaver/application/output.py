"""The application contract for publishing generated output."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .errors import ServiceError


class OutputPublisher(Protocol):
    def publish(
        self,
        output_path: Path,
        write: Callable[[Path], None],
        *,
        overwrite: bool = False,
    ) -> None: ...


class OutputPublicationError(ServiceError):
    """A structured error raised while an operation publishes its output."""

    def __init__(self, error: ServiceError) -> None:
        super().__init__(error.error_code, error.message, **error.context)
