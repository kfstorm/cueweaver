"""Term map persistence and inspection operations."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from ..errors import ServiceError

MAX_TERM_MAP_BYTES = 1 * 1024 * 1024


@dataclass(frozen=True)
class TermMapSummary:
    id: str
    name: str
    entry_count: int
    updated_at: str


@dataclass(frozen=True)
class TermMapDetail(TermMapSummary):
    content: Mapping[str, str]


class TermMapStore(Protocol):
    def list(self) -> list[TermMapSummary]: ...

    def get(self, term_map_id: str) -> TermMapDetail: ...

    def create(self, name: str, content: Mapping[str, str]) -> TermMapSummary: ...

    def rename(self, term_map_id: str, name: str) -> TermMapSummary: ...

    def replace(
        self, term_map_id: str, content: Mapping[str, str]
    ) -> TermMapSummary: ...

    def delete(self, term_map_id: str, name: str) -> TermMapSummary: ...


class TermMaps:
    def __init__(self, store: TermMapStore) -> None:
        self._store = store

    def list(self) -> list[TermMapSummary]:
        return self._store.list()

    def get(self, term_map_id: str) -> TermMapDetail:
        return self._store.get(term_map_id)

    def create(self, name: str, content: Mapping[str, str]) -> TermMapSummary:
        validate_term_map(name, content)
        return self._store.create(name, content)

    def rename(self, term_map_id: str, name: str) -> TermMapSummary:
        validate_term_map_name(name)
        return self._store.rename(term_map_id, name)

    def replace(self, term_map_id: str, content: Mapping[str, str]) -> TermMapSummary:
        validate_term_map_content(content)
        return self._store.replace(term_map_id, content)

    def delete(self, term_map_id: str, name: str) -> TermMapSummary:
        if not isinstance(name, str) or not name:
            raise ServiceError(
                "term_map_delete_confirmation_required",
                "Enter the current Term map name to confirm deletion",
                field="name",
            )
        return self._store.delete(term_map_id, name)


def validate_term_map(name: str, content: Mapping[str, str]) -> None:
    validate_term_map_name(name)
    validate_term_map_content(content)


def validate_term_map_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ServiceError(
            "invalid_term_map", "Term map name must be non-empty", field="name"
        )


def validate_term_map_content(content: Mapping[str, str]) -> None:
    if not isinstance(content, dict) or not content:
        raise ServiceError(
            "invalid_term_map",
            "Term map must be a non-empty JSON object",
            field="content",
        )
    validate_term_map_entries(content.items())
    try:
        encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise ServiceError(
            "invalid_term_map", "Term map must contain JSON values"
        ) from error
    if len(encoded) > MAX_TERM_MAP_BYTES:
        raise ServiceError(
            "invalid_term_map", "Term map must be at most 1 MiB", field="content"
        )


def validate_term_map_entries(
    entries: Iterable[tuple[object, object]],
) -> dict[str, str]:
    """Validate JSON object pairs without losing duplicate or folded keys."""
    content: dict[str, str] = {}
    folded_sources: set[str] = set()
    for source, target in entries:
        if not isinstance(source, str) or not source:
            raise ServiceError(
                "invalid_term_map",
                "Term map source keys must be non-empty strings",
                field="content",
            )
        if not isinstance(target, str) or not target:
            raise ServiceError(
                "invalid_term_map",
                "Term map target values must be non-empty strings",
                field="content",
            )
        folded_source = source.casefold()
        if folded_source in folded_sources:
            raise ServiceError(
                "invalid_term_map",
                "Term map source keys must be unique regardless of case",
                field="content",
            )
        folded_sources.add(folded_source)
        content[source] = target
    if not content:
        raise ServiceError(
            "invalid_term_map",
            "Term map must be a non-empty JSON object",
            field="content",
        )
    return content
