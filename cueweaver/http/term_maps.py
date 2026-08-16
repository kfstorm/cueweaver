"""HTTP adapter for persistent Term maps."""

from __future__ import annotations

import json
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..application.errors import ServiceError
from ..application.term_maps import (
    MAX_TERM_MAP_REQUEST_BYTES,
    MAX_TERM_MAP_UPLOAD_BYTES,
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


# These limits apply to the raw request and raw JSON content respectively.
# The application validator separately measures compact canonical UTF-8 content.
MAX_REQUEST_BYTES = MAX_TERM_MAP_REQUEST_BYTES


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
        raw_body = await _read_limited_body(request)
        pairs, content_size = _decode_upload(raw_body)
        if content_size > MAX_TERM_MAP_UPLOAD_BYTES:
            raise ServiceError(
                "invalid_term_map", "Term map must be at most 1 MiB", field="content"
            )
        name, content = _parse_upload(pairs)
        return summary_body(application.term_maps.create(name, content))

    @app.post("/api/term-maps/{term_map_id}")
    def post_term_map_item_not_found(_term_map_id: str) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error_code": "not_found", "message": "Resource not found"},
        )

    @app.patch("/api/term-maps/{term_map_id}")
    async def rename_term_map(term_map_id: str, request: Request) -> dict[str, object]:
        raw_body = await _read_limited_body(request)
        pairs, _ = _decode_upload(raw_body)
        name = _parse_string_field(pairs, "name", "invalid_term_map")
        return summary_body(application.term_maps.rename(term_map_id, name))

    @app.put("/api/term-maps/{term_map_id}")
    async def replace_term_map(term_map_id: str, request: Request) -> dict[str, object]:
        raw_body = await _read_limited_body(request)
        pairs, content_size = _decode_upload(raw_body)
        if content_size > MAX_TERM_MAP_UPLOAD_BYTES:
            raise ServiceError(
                "invalid_term_map", "Term map must be at most 1 MiB", field="content"
            )
        fields = _parse_fields(pairs, {"content"})
        raw_content = fields.get("content")
        if not isinstance(raw_content, JsonPairs):
            raise ServiceError(
                "invalid_term_map",
                "Term map content must be a JSON object",
                field="content",
            )
        content = validate_term_map_entries(raw_content)
        return summary_body(application.term_maps.replace(term_map_id, content))

    @app.delete("/api/term-maps/{term_map_id}")
    async def delete_term_map(term_map_id: str, request: Request) -> dict[str, object]:
        raw_body = await _read_limited_body(request)
        pairs, _ = _decode_upload(raw_body)
        name = _parse_string_field(
            pairs,
            "name",
            "term_map_delete_confirmation_required",
        )
        return summary_body(application.term_maps.delete(term_map_id, name))


async def _read_limited_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise ServiceError("invalid_term_map", "Term map upload is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_upload(raw_body: bytes) -> tuple[JsonPairs, int]:
    try:
        text = raw_body.decode("utf-8")
        decoder = json.JSONDecoder(object_pairs_hook=JsonPairs)
        position = _skip_whitespace(text, 0)
        if position >= len(text) or text[position] != "{":
            raise TypeError
        position += 1
        pairs: JsonPairs = JsonPairs()
        content_size: int | None = None
        after_comma = False
        while True:
            position = _skip_whitespace(text, position)
            if position < len(text) and text[position] == "}":
                if after_comma:
                    raise TypeError
                position += 1
                break
            key, position = decoder.raw_decode(text, position)
            if not isinstance(key, str):
                raise TypeError
            position = _skip_whitespace(text, position)
            if position >= len(text) or text[position] != ":":
                raise TypeError
            value_start = _skip_whitespace(text, position + 1)
            value, position = decoder.raw_decode(text, value_start)
            pairs.append((key, value))
            if key == "content":
                content_size = len(text[value_start:position].encode("utf-8"))
            position = _skip_whitespace(text, position)
            if position < len(text) and text[position] == ",":
                position += 1
                after_comma = True
                continue
            if position < len(text) and text[position] == "}":
                position += 1
                break
            raise TypeError
        if _skip_whitespace(text, position) != len(text):
            raise TypeError
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise ServiceError(
            "invalid_term_map", "Term map upload is not valid JSON"
        ) from error
    return pairs, content_size or 0


def _skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def _parse_upload(payload: object) -> tuple[str, dict[str, str]]:
    if not isinstance(payload, JsonPairs):
        raise ServiceError("invalid_term_map", "Upload must be a JSON object")
    fields = _parse_fields(payload, {"name", "content"})
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


def _parse_fields(payload: JsonPairs, allowed_fields: set[str]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in payload:
        if not isinstance(key, str):
            raise ServiceError("invalid_term_map", "Upload field names must be strings")
        if key in fields:
            raise ServiceError("invalid_term_map", "Upload contains duplicate fields")
        fields[key] = value
    unknown_fields = set(fields) - allowed_fields
    if unknown_fields:
        raise ServiceError(
            "invalid_term_map",
            "Upload contains unknown fields",
            field=min(str(field) for field in unknown_fields),
        )
    return fields


def _parse_string_field(payload: JsonPairs, field: str, error_code: str) -> str:
    value = _parse_fields(payload, {field}).get(field)
    if not isinstance(value, str):
        message = (
            "Enter the current Term map name to confirm deletion"
            if error_code == "term_map_delete_confirmation_required"
            else "Term map name must be a string"
        )
        raise ServiceError(error_code, message, field=field)
    return value


def summary_body(summary: TermMapSummary) -> dict[str, object]:
    return {
        "id": summary.id,
        "name": summary.name,
        "entry_count": summary.entry_count,
        "updated_at": summary.updated_at,
    }


def detail_body(detail: TermMapDetail) -> dict[str, object]:
    return {**summary_body(detail), "content": dict(detail.content)}
