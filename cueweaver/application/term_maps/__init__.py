"""Term map persistence and inspection operations."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from ..errors import ServiceError

MAX_TERM_MAP_BYTES = 1 * 1024 * 1024
# The canonical content limit applies after JSON parsing. Upload and request
# limits are raw UTF-8 byte limits enforced by the HTTP adapter.
MAX_TERM_MAP_UPLOAD_BYTES = MAX_TERM_MAP_BYTES
MAX_TERM_MAP_REQUEST_BYTES = MAX_TERM_MAP_BYTES * 2
_HIGH_SURROGATE_START = 0xD800
_HIGH_SURROGATE_END = 0xDBFF
_LOW_SURROGATE_START = 0xDC00
_LOW_SURROGATE_END = 0xDFFF


def reject_duplicate_json_pairs(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    """Build a JSON object while rejecting duplicate keys at every level."""

    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


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
        return self._store.create(name, validate_term_map(name, content))

    def rename(self, term_map_id: str, name: str) -> TermMapSummary:
        validate_term_map_name(name)
        return self._store.rename(term_map_id, name)

    def replace(self, term_map_id: str, content: Mapping[str, str]) -> TermMapSummary:
        validated = validate_term_map_content(content)
        return self._store.replace(term_map_id, validated)

    def delete(self, term_map_id: str, name: str) -> TermMapSummary:
        if not isinstance(name, str) or not name:
            raise ServiceError(
                "term_map_delete_confirmation_required",
                "Enter the current Term map name to confirm deletion",
                field="name",
            )
        return self._store.delete(term_map_id, name)


def validate_term_map(name: str, content: Mapping[str, str]) -> dict[str, str]:
    validate_term_map_name(name)
    return validate_term_map_content(content)


def validate_term_map_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ServiceError(
            "invalid_term_map", "Term map name must be non-empty", field="name"
        )


def validate_term_map_content(content: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(content, dict) or not content:
        raise ServiceError(
            "invalid_term_map",
            "Term map must be a non-empty JSON object",
            field="content",
        )
    return validate_term_map_entries(content.items())


def canonical_term_map_bytes(content: Mapping[str, str]) -> bytes:
    """Return the compact UTF-8 JSON representation used for size checks."""

    try:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise ServiceError(
            "invalid_term_map", "Term map must contain valid Unicode strings"
        ) from error


def _validate_canonical_size(content: Mapping[str, str]) -> None:
    if len(canonical_term_map_bytes(content)) > MAX_TERM_MAP_BYTES:
        raise ServiceError(
            "invalid_term_map", "Term map must be at most 1 MiB", field="content"
        )


def validate_term_map_entries(
    entries: Iterable[tuple[object, object]],
) -> dict[str, str]:
    """Validate JSON object pairs without losing duplicate or folded keys."""
    content: dict[str, str] = {}
    folded_sources: set[str] = set()
    for raw_source, raw_target in entries:
        if not isinstance(raw_source, str) or not raw_source:
            raise ServiceError(
                "invalid_term_map",
                "Term map source keys must be non-empty strings",
                field="content",
            )
        if not isinstance(raw_target, str) or not raw_target:
            raise ServiceError(
                "invalid_term_map",
                "Term map target values must be non-empty strings",
                field="content",
            )
        source = _normalize_unicode_scalars(raw_source)
        target = _normalize_unicode_scalars(raw_target)
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
    _validate_canonical_size(content)
    return content


def _normalize_unicode_scalars(value: str) -> str:
    normalized: list[str] = []
    index = 0
    while index < len(value):
        code_point = ord(value[index])
        if (
            _HIGH_SURROGATE_START <= code_point <= _HIGH_SURROGATE_END
            and index + 1 < len(value)
        ):
            next_code_point = ord(value[index + 1])
            if _LOW_SURROGATE_START <= next_code_point <= _LOW_SURROGATE_END:
                normalized.append(
                    chr(
                        0x10000
                        + ((code_point - 0xD800) << 10)
                        + next_code_point
                        - 0xDC00
                    )
                )
                index += 2
                continue
        normalized.append(value[index])
        index += 1
    return "".join(normalized)


__all__ = [
    "MAX_TERM_MAP_BYTES",
    "MAX_TERM_MAP_REQUEST_BYTES",
    "MAX_TERM_MAP_UPLOAD_BYTES",
    "TermMapDetail",
    "TermMapStore",
    "TermMapSummary",
    "TermMaps",
    "canonical_term_map_bytes",
    "reject_duplicate_json_pairs",
    "validate_term_map",
    "validate_term_map_content",
    "validate_term_map_entries",
    "validate_term_map_name",
]
