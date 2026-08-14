"""Atomic publication of generated subtitle output."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from ..application.errors import ServiceError


class AtomicOutputPublisher:
    def publish(
        self,
        output_path: Path,
        write: Callable[[Path], None],
        *,
        overwrite: bool = False,
    ) -> None:
        if not overwrite and output_path.exists():
            raise ServiceError(
                "output_exists", "Output path already exists", path=output_path
            )
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ServiceError(
                "invalid_output_path",
                "Output directory cannot be created",
                path=output_path,
            ) from error
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=output_path.suffix,
            )
            temporary_path = Path(temporary_name)
            os.close(descriptor)
            write(temporary_path)
            if overwrite:
                temporary_path.replace(output_path)
            else:
                os.link(temporary_path, output_path)
        except FileExistsError as error:
            raise ServiceError(
                "output_exists", "Output path already exists", path=output_path
            ) from error
        except ServiceError:
            raise
        except OSError as error:
            raise ServiceError(
                "output_write_failed", "Output cannot be written", path=output_path
            ) from error
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
