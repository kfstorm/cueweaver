"""HTTP adapter for durable Jobs."""

from typing import Literal, Protocol

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from ..application.jobs import CreateJobRequest
from ..application.jobs.model import project_job_detail


class JobOptionsBody(BaseModel):
    model_config = {"extra": "forbid"}
    target_language_code: str = Field(min_length=1)
    output_suffix: str | None = None
    output_conflict_policy: Literal["append-number", "overwrite"] = "append-number"
    term_map_mode: Literal["follow", "selected", "none"]
    term_map_id: str | None = Field(default=None, min_length=1, validate_default=True)
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True

    @field_validator("term_map_id")
    @classmethod
    def validate_term_map_id(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        mode = info.data.get("term_map_mode")
        if mode in {"follow", "none"} and value is not None:
            raise ValueError("Term map ID must be null for follow or none mode")
        if mode == "selected" and value is None:
            raise ValueError("Selected mode requires a Term map ID")
        return value


class SubtitleSourceBody(BaseModel):
    model_config = {"extra": "forbid"}
    media_path: str = Field(min_length=1)
    subtitle_path: str | None = Field(default=None, min_length=1)
    stream_index: int | None = Field(default=None, strict=True, ge=0)
    source_format: str | None = Field(default=None, min_length=1)


class CreateJobBody(JobOptionsBody, SubtitleSourceBody):
    pass


class CreateBatchItem(SubtitleSourceBody):
    @model_validator(mode="after")
    def validate_subtitle_source(self) -> "CreateBatchItem":
        if (self.subtitle_path is None) == (self.stream_index is None):
            raise ValueError("Exactly one subtitle source is required")
        if self.stream_index is not None and self.source_format is None:
            raise ValueError("Embedded subtitles require a source format")
        if self.subtitle_path is not None and self.source_format is not None:
            raise ValueError("External subtitles must not provide a source format")
        return self


class CreateBatchBody(JobOptionsBody):
    items: list[CreateBatchItem] = Field(min_length=1)


class JobsOperation(Protocol):
    def create(self, request: CreateJobRequest) -> dict[str, object]: ...

    def create_batch(
        self, requests: list[CreateJobRequest]
    ) -> list[dict[str, object]]: ...

    def retry(self, job_id: str) -> dict[str, object]: ...

    def cancel(self, job_id: str) -> dict[str, object]: ...

    def delete(self, job_id: str) -> dict[str, object]: ...

    def clear_completed(self) -> dict[str, object]: ...

    def list_page(
        self, limit: int = 50, cursor: str | None = None
    ) -> dict[str, object]: ...

    def get(self, job_id: str) -> dict[str, object]: ...

    def close(self) -> None: ...


class JobsApplication(Protocol):
    @property
    def jobs(self) -> JobsOperation: ...


def register_jobs(app: FastAPI, application: JobsApplication) -> None:
    app.router.on_shutdown.append(application.jobs.close)

    @app.post("/api/jobs")
    def create_job(body: CreateJobBody) -> dict[str, object]:
        return _project_detail(
            application.jobs.create(
                CreateJobRequest(
                    media_path=body.media_path,
                    subtitle_path=body.subtitle_path,
                    target_language_code=body.target_language_code,
                    term_map_mode=body.term_map_mode,
                    term_map_id=body.term_map_id,
                    dynamic_terminology_enabled=body.dynamic_terminology_enabled,
                    subtitle_terminology_filter_enabled=body.subtitle_terminology_filter_enabled,
                    output_suffix=body.output_suffix,
                    output_conflict_policy=body.output_conflict_policy,
                    stream_index=body.stream_index,
                    source_format=body.source_format,
                )
            )
        )

    @app.post("/api/jobs/batch")
    def create_batch(body: CreateBatchBody) -> dict[str, object]:
        requests = [
            CreateJobRequest(
                media_path=item.media_path,
                subtitle_path=item.subtitle_path,
                target_language_code=body.target_language_code,
                term_map_mode=body.term_map_mode,
                term_map_id=body.term_map_id,
                dynamic_terminology_enabled=body.dynamic_terminology_enabled,
                subtitle_terminology_filter_enabled=body.subtitle_terminology_filter_enabled,
                output_suffix=body.output_suffix,
                output_conflict_policy=body.output_conflict_policy,
                stream_index=item.stream_index,
                source_format=item.source_format,
            )
            for item in body.items
        ]
        results = application.jobs.create_batch(requests)
        return {"results": [_batch_result(result) for result in results]}

    @app.get("/api/jobs")
    def list_jobs(
        limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None
    ) -> dict[str, object]:
        return application.jobs.list_page(limit, cursor)

    @app.delete("/api/jobs/completed")
    def clear_completed_jobs() -> dict[str, object]:
        return application.jobs.clear_completed()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        return _project_detail(application.jobs.get(job_id))

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, object]:
        return application.jobs.delete(job_id)

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str) -> dict[str, object]:
        return _project_detail(application.jobs.retry(job_id))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, object]:
        return _project_detail(application.jobs.cancel(job_id))


def _project_detail(job: dict[str, object]) -> dict[str, object]:
    if "request" not in job:
        return job
    queue_position = job.get("queue_position")
    if not isinstance(queue_position, int) or isinstance(queue_position, bool):
        queue_position = None
    return project_job_detail(job, queue_position)


def _batch_result(result: dict[str, object]) -> dict[str, object]:
    if "error_code" in result:
        return result
    return _project_detail(result)
