"""HTTP adapter for extraction."""

from pathlib import Path
from typing import Protocol

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ..application.extraction import ExtractRequest, ExtractResult


class ExtractBody(BaseModel):
    model_config = {"extra": "forbid"}
    media_path: str = Field(min_length=1)
    stream_index: int = Field(strict=True)
    output_path: str = Field(min_length=1)


class ExtractionOperation(Protocol):
    def extract(self, request: ExtractRequest) -> ExtractResult: ...


class ExtractionApplication(Protocol):
    @property
    def extraction(self) -> ExtractionOperation: ...


def register_extract(app: FastAPI, application: ExtractionApplication) -> None:
    @app.post("/api/extract")
    def extract(body: ExtractBody) -> dict[str, object]:
        result = application.extraction.extract(
            ExtractRequest(
                Path(body.media_path), body.stream_index, Path(body.output_path)
            )
        )
        return result_body(result)


def result_body(result: ExtractResult) -> dict[str, object]:
    return {"output_path": str(result.output_path), "format": result.format}
