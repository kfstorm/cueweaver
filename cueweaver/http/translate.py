"""HTTP adapter for translation."""

from pathlib import Path
from typing import Protocol

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ..application.translation import TranslateRequest, TranslateResult


class TranslateBody(BaseModel):
    model_config = {"extra": "forbid"}
    subtitle_path: str = Field(min_length=1)
    target_language_code: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    work_directory: str = Field(min_length=1)
    term_map_path: str | None = Field(default=None, min_length=1)
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True


class TranslationOperation(Protocol):
    def translate(self, request: TranslateRequest) -> TranslateResult: ...


class TranslationApplication(Protocol):
    translation: TranslationOperation


def register_translate(app: FastAPI, application: TranslationApplication) -> None:
    @app.post("/api/translate")
    def translate(body: TranslateBody) -> dict[str, object]:
        result = application.translation.translate(
            TranslateRequest(
                Path(body.subtitle_path),
                body.target_language_code,
                Path(body.output_path),
                Path(body.work_directory),
                Path(body.term_map_path) if body.term_map_path is not None else None,
                body.dynamic_terminology_enabled,
                body.subtitle_terminology_filter_enabled,
            )
        )
        return result_body(result)


def result_body(result: TranslateResult) -> dict[str, object]:
    return {
        "output_path": str(result.output_path),
        "target_language_code": result.target_language_code,
        "format": result.format,
    }
