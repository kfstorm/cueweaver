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
        backup_path: Path | None = None
        publication_attempted = False
        try:
            temporary_path = _create_temporary_path(output_path)
            write(temporary_path)
            _fsync_file(temporary_path)
            backup_path = _publish_temporary(
                temporary_path, output_path, overwrite=overwrite
            )
            publication_attempted = True
            _fsync_directory(output_path.parent)
            publication_attempted = False
            _remove_backup(backup_path)
            backup_path = None
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
            rollback_succeeded = True
            if publication_attempted:
                rollback_succeeded = _rollback_publication(output_path, backup_path)
            if rollback_succeeded:
                _remove_backup(backup_path)
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)


def _create_temporary_path(output_path: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=output_path.suffix,
    )
    os.close(descriptor)
    return Path(temporary_name)


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as file:
        file.flush()
        os.fsync(file.fileno())


def _publish_temporary(
    temporary_path: Path, output_path: Path, *, overwrite: bool
) -> Path | None:
    backup_path = _backup_existing_output(output_path) if overwrite else None
    if overwrite:
        temporary_path.replace(output_path)
    else:
        os.link(temporary_path, output_path)
    return backup_path


def _backup_existing_output(output_path: Path) -> Path | None:
    if not output_path.exists():
        return None
    descriptor, backup_name = tempfile.mkstemp(dir=output_path.parent)
    os.close(descriptor)
    backup_path = Path(backup_name)
    backup_path.unlink()
    os.link(output_path, backup_path)
    return backup_path


def _remove_backup(backup_path: Path | None) -> None:
    if backup_path is not None:
        with suppress(OSError):
            backup_path.unlink(missing_ok=True)


def _rollback_publication(output_path: Path, backup_path: Path | None) -> bool:
    try:
        if backup_path is not None:
            backup_path.replace(output_path)
        else:
            output_path.unlink(missing_ok=True)
        _fsync_directory(output_path.parent)
    except OSError:
        return False
    return True


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
