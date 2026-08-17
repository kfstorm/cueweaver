"""Durable storage for Media-directory Term map bindings."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ..application.directory_term_maps import DirectoryTermMapStore
from ..application.errors import ServiceError
from ..work import WorkRoot
from .locking import DurableFileLock
from .term_maps import atomic_write_json


class FileDirectoryTermMapStore(DirectoryTermMapStore):
    """Store canonical Media-relative directory bindings below Work root."""

    def __init__(
        self, work_root: WorkRoot, lock: DurableFileLock | None = None
    ) -> None:
        if not isinstance(work_root, WorkRoot):
            raise TypeError("FileDirectoryTermMapStore requires a WorkRoot")
        self._directory = work_root.term_maps_directory
        self._path = self._directory / "directory-bindings.json"
        self._lock = lock or DurableFileLock(self._directory / ".lock")

    def get_binding(self, directory: str) -> str | None:
        with self._locked():
            return self._read().get(directory)

    def bind(
        self,
        directory: str,
        term_map_id: str,
        validate: Callable[[str], object] | None = None,
    ) -> None:
        with self._locked():
            if validate is not None:
                validate(term_map_id)
            bindings = self._read()
            bindings[directory] = term_map_id
            self._write(bindings)

    def remove(self, directory: str) -> None:
        with self._locked():
            bindings = self._read()
            if directory not in bindings:
                return
            del bindings[directory]
            self._write(bindings)

    def remove_term_map_locked(self, term_map_id: str) -> dict[str, str]:
        """Remove references while the owning Term map lock is held."""
        bindings = self._read()
        removed = {
            key: value for key, value in bindings.items() if value == term_map_id
        }
        if removed:
            self._write(
                {key: value for key, value in bindings.items() if key not in removed}
            )
        return removed

    def snapshot_bindings_locked(self) -> dict[str, str]:
        return self._read()

    def replace_bindings_locked(self, bindings: dict[str, str]) -> None:
        self._write(bindings)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ServiceError(
                "directory_term_maps_unavailable",
                "Directory Term map metadata cannot be read",
            ) from error
        if not isinstance(payload, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not value
            for key, value in payload.items()
        ):
            raise ServiceError(
                "directory_term_maps_unavailable",
                "Directory Term map metadata is invalid",
            )
        return payload

    def _write(self, bindings: dict[str, str]) -> None:
        atomic_write_json(
            self._path,
            bindings,
            "directory_term_map_write_failed",
            "Directory Term map binding cannot be saved",
        )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            with self._lock.locked(self._directory):
                yield
        except OSError as error:
            raise ServiceError(
                "directory_term_maps_unavailable",
                "Directory Term map storage cannot be opened",
            ) from error


__all__ = ["FileDirectoryTermMapStore"]
