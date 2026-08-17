"""Directory-scoped Term map binding and inheritance operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import ServiceError
from .term_maps import TermMapDetail, TermMapSummary


@dataclass(frozen=True)
class DirectoryTermMapState:
    directory: str
    local: TermMapSummary | None
    effective: TermMapSummary | None
    source_directory: str | None


class DirectoryTermMapStore(Protocol):
    def get_binding(self, directory: str) -> str | None: ...

    def bind(
        self,
        directory: str,
        term_map_id: str,
        validate: Callable[[str], object] | None = None,
    ) -> None: ...

    def remove(self, directory: str) -> None: ...


class DirectoryTermMaps:
    """Resolve the nearest persisted binding under the configured Media root."""

    def __init__(
        self,
        store: DirectoryTermMapStore,
        term_maps: DirectoryTermMapResolver,
        media_root: Path,
    ) -> None:
        self._store = store
        self._term_maps = term_maps
        self._media_root = Path(media_root).resolve()

    def get(self, directory: str) -> DirectoryTermMapState:
        canonical = self._canonical_directory(directory)
        local_id = self._store.get_binding(canonical)
        effective_id, source = self._effective_binding(canonical)
        return DirectoryTermMapState(
            directory=canonical,
            local=self._summary(local_id),
            effective=self._summary(effective_id),
            source_directory=source,
        )

    def bind(self, directory: str, term_map_id: str) -> DirectoryTermMapState:
        canonical = self._canonical_directory(directory, require_existing=True)
        self._store.bind(canonical, term_map_id, self._summary)
        return self.get(canonical)

    def remove(self, directory: str) -> DirectoryTermMapState:
        canonical = self._canonical_directory(directory)
        self._store.remove(canonical)
        return self.get(canonical)

    def _effective_binding(self, directory: str) -> tuple[str | None, str | None]:
        path = Path(directory)
        candidates = (path, *path.parents)
        for candidate in candidates:
            key = "" if str(candidate) == "." else candidate.as_posix()
            term_map_id = self._store.get_binding(key)
            if term_map_id is not None:
                return term_map_id, key
        return None, None

    def _summary(self, term_map_id: str | None) -> TermMapSummary | None:
        if term_map_id is None:
            return None
        return self._term_maps.get(term_map_id)

    def _canonical_directory(
        self, value: str, *, require_existing: bool = False
    ) -> str:
        if not isinstance(value, str):
            raise ServiceError("invalid_media_path", "Media path must be relative")
        path = Path(value)
        if "\\" in value or "\x00" in value or path.is_absolute() or ".." in path.parts:
            raise ServiceError(
                "invalid_media_path",
                "Media path must stay inside Media root",
                path=value,
            )
        try:
            resolved = (self._media_root / path).resolve()
        except (OSError, RuntimeError) as error:
            raise ServiceError(
                "invalid_media_path", "Media path cannot be resolved", path=value
            ) from error
        if not resolved.is_relative_to(self._media_root):
            raise ServiceError(
                "invalid_media_path",
                "Media path must stay inside Media root",
                path=value,
            )
        if require_existing and not resolved.is_dir():
            raise ServiceError(
                "directory_not_found", "Media directory does not exist", path=value
            )
        relative = resolved.relative_to(self._media_root)
        return "" if str(relative) == "." else relative.as_posix()


class DirectoryTermMapResolver(Protocol):
    def get(self, term_map_id: str) -> TermMapDetail: ...


__all__ = ["DirectoryTermMapState", "DirectoryTermMapStore", "DirectoryTermMaps"]
