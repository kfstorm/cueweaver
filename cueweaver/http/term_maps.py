"""HTTP adapter for persistent Term maps."""

from __future__ import annotations

import json
from typing import Protocol

from fastapi import FastAPI, Request

from ..application.errors import ServiceError
from ..application.term_maps import (
    TermMapDetail,
    TermMaps,
    TermMapSummary,
    validate_term_map_entries,
)


class TermMapsApplication(Protocol):
    @property
    def term_maps(self) -> TermMaps: ...


class JsonPairs(list[tuple[object, object]]):
    """Keep JSON object pairs so case-fold collisions are not silently lost."""


def register_term_maps(app: FastAPI, application: TermMapsApplication) -> None:
    @app.get("/api/term-maps")
    def list_term_maps() -> dict[str, object]:
        return {
            "term_maps": [summary_body(item) for item in application.term_maps.list()]
        }

    @app.get("/api/term-maps/{term_map_id}")
    def get_term_map(term_map_id: str) -> dict[str, object]:
        return detail_body(application.term_maps.get(term_map_id))

    @app.post("/api/term-maps")
    async def create_term_map(request: Request) -> dict[str, object]:
        raw_body = await request.body()
        try:
            pairs = json.loads(raw_body, object_pairs_hook=JsonPairs)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ServiceError(
                "invalid_term_map", "Term map upload is not valid JSON"
            ) from error
        name, content = _parse_upload(pairs)
        return summary_body(application.term_maps.create(name, content))


def _parse_upload(payload: object) -> tuple[str, dict[str, str]]:
    if not isinstance(payload, JsonPairs):
        raise ServiceError("invalid_term_map", "Upload must be a JSON object")
    fields: dict[object, object] = {}
    for key, value in payload:
        if key in fields:
            raise ServiceError("invalid_term_map", "Upload contains duplicate fields")
        fields[key] = value
    unknown_fields = set(fields) - {"name", "content"}
    if unknown_fields:
        raise ServiceError(
            "invalid_term_map",
            "Upload contains unknown fields",
            field=min(str(field) for field in unknown_fields),
        )
    name = fields.get("name")
    raw_content = fields.get("content")
    if not isinstance(name, str):
        raise ServiceError(
            "invalid_term_map", "Term map name must be a string", field="name"
        )
    if not isinstance(raw_content, JsonPairs):
        raise ServiceError(
            "invalid_term_map",
            "Term map content must be a JSON object",
            field="content",
        )
    return name, validate_term_map_entries(raw_content)


def summary_body(summary: TermMapSummary) -> dict[str, object]:
    return {
        "id": summary.id,
        "name": summary.name,
        "entry_count": summary.entry_count,
        "updated_at": summary.updated_at,
    }


def detail_body(detail: TermMapDetail) -> dict[str, object]:
    return {**summary_body(detail), "content": dict(detail.content)}
