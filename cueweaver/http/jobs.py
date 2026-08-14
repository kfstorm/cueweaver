"""HTTP adapter for durable Jobs."""

from typing import Literal, Protocol

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ..application.jobs import CreateJobRequest


class CreateJobBody(BaseModel):
    model_config = {"extra": "forbid"}
    media_path: str = Field(min_length=1)
    subtitle_path: str | None = Field(default=None, min_length=1)
    target_language_code: str = Field(min_length=1)
    output_suffix: str | None = None
    output_conflict_policy: Literal["append-number", "overwrite"] = "append-number"
    term_map_id: str | None = None
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True
    stream_index: int | None = Field(default=None, strict=True, ge=0)
    source_format: str | None = Field(default=None, min_length=1)


class JobsOperation(Protocol):
    def create(self, request: CreateJobRequest) -> dict[str, object]: ...

    def list(self) -> list[dict[str, object]]: ...

    def get(self, job_id: str) -> dict[str, object]: ...

    def close(self) -> None: ...


class JobsApplication(Protocol):
    @property
    def jobs(self) -> JobsOperation: ...


def register_jobs(app: FastAPI, application: JobsApplication) -> None:
    app.router.on_shutdown.append(application.jobs.close)

    @app.post("/api/jobs")
    def create_job(body: CreateJobBody) -> dict[str, object]:
        return application.jobs.create(
            CreateJobRequest(
                media_path=body.media_path,
                subtitle_path=body.subtitle_path,
                target_language_code=body.target_language_code,
                term_map_id=body.term_map_id,
                dynamic_terminology_enabled=body.dynamic_terminology_enabled,
                subtitle_terminology_filter_enabled=body.subtitle_terminology_filter_enabled,
                output_suffix=body.output_suffix,
                output_conflict_policy=body.output_conflict_policy,
                stream_index=body.stream_index,
                source_format=body.source_format,
            )
        )

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, object]:
        return {"jobs": application.jobs.list()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        return application.jobs.get(job_id)
