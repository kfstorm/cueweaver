"""The application contract for publishing generated output."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class OutputPublisher(Protocol):
    def publish(
        self,
        output_path: Path,
        write: Callable[[Path], None],
        *,
        overwrite: bool = False,
    ) -> None: ...
