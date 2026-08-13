"""HTTP adapter for Media directory browsing."""

from pathlib import Path
from typing import Protocol

from fastapi import FastAPI
from pydantic import BaseModel

from ..application.browsing import BrowseEntry, BrowseRequest, BrowseResult


class BrowseBody(BaseModel):
    model_config = {"extra": "forbid"}
    path: str = ""


class BrowseOperation(Protocol):
    def browse(self, request: BrowseRequest) -> BrowseResult: ...


class BrowseApplication(Protocol):
    @property
    def browsing(self) -> BrowseOperation | None: ...


def register_browse(app: FastAPI, application: BrowseApplication) -> None:
    operation = application.browsing
    if operation is None:
        return

    @app.post("/api/media/browse")
    def browse(body: BrowseBody) -> dict[str, object]:
        return result_body(operation.browse(BrowseRequest(Path(body.path))))


def result_body(result: BrowseResult) -> dict[str, object]:
    return {
        "path": "" if str(result.path) == "." else str(result.path),
        "entries": [entry_body(entry) for entry in result.entries],
    }


def entry_body(entry: BrowseEntry) -> dict[str, object]:
    body: dict[str, object] = {
        "name": entry.name,
        "path": str(entry.path),
        "kind": entry.kind,
    }
    if entry.title is not None:
        body["title"] = entry.title
    if entry.year is not None:
        body["year"] = entry.year
    return body
