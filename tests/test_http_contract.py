from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueweaver.application.browsing import BrowseEntry, BrowseRequest, BrowseResult
from cueweaver.application.discovery import (
    DiscoverRequest,
    DiscoverResult,
    SubtitleCandidateResult,
    UnsupportedCandidateResult,
)
from cueweaver.application.errors import ServiceError
from cueweaver.http import create_app


def expected_discovery_payload(external_path: str) -> dict[str, object]:
    return {
        "candidates": [
            {
                "kind": "external",
                "path": external_path,
                "format": "srt",
                "tags": {"language": "en", "title": ""},
            },
            {
                "kind": "embedded",
                "stream_index": 3,
                "format": "ass",
                "tags": {"language": "zhs", "title": "Chinese"},
                "dispositions": ["default", "forced"],
            },
        ],
        "unsupported_candidates": [
            {"kind": "embedded", "stream_index": 4, "reason": "bitmap subtitle"}
        ],
    }


class ApplicationFixture:
    def __init__(self) -> None:
        self.discover_request: DiscoverRequest | None = None
        self.discovery = self
        self.browsing = self

    def discover(self, request: DiscoverRequest) -> DiscoverResult:
        self.discover_request = request
        return DiscoverResult(
            request.media_path,
            [
                SubtitleCandidateResult(
                    "external",
                    "srt",
                    {"language": "en", "title": ""},
                    path=Path("/media/Movie.en.srt"),
                ),
                SubtitleCandidateResult(
                    "embedded",
                    "ass",
                    {"language": "zhs", "title": "Chinese"},
                    stream_index=3,
                    dispositions=["default", "forced"],
                ),
            ],
            [UnsupportedCandidateResult("embedded", "bitmap subtitle", stream_index=4)],
        )

    def browse(self, request: BrowseRequest) -> BrowseResult:
        return BrowseResult(
            request.path,
            [BrowseEntry("Movie.mkv", Path("Movie.mkv"), "media", "Movie", 2024)],
        )


class JobsApplicationFixture(ApplicationFixture):
    def __init__(self) -> None:
        super().__init__()
        self.jobs = self
        self.retried_job_id: str | None = None
        self.cancelled_job_id: str | None = None
        self.deleted_job_id: str | None = None
        self.cleared_completed = False
        self.list_page_arguments: tuple[int, str | None, str, str] | None = None

    def create(self, _request: object) -> dict[str, object]:
        return {}

    def create_batch(self, requests: list[object]) -> list[dict[str, object]]:
        return [
            {"id": str(index), "request": {"media_path": request.media_path}}
            if index == 0
            else {"error_code": "invalid_media_path", "message": "bad media"}
            for index, request in enumerate(requests)
        ]

    def list_page(
        self,
        _limit: int = 50,
        _cursor: str | None = None,
        _search: str = "",
        _status: str = "all",
    ) -> dict[str, object]:
        self.list_page_arguments = (_limit, _cursor, _search, _status)
        return {
            "active_jobs": [],
            "history_jobs": [],
            "next_cursor": None,
            "matching_count": 0,
            "completed_count": 0,
        }

    def get(self, _job_id: str) -> dict[str, object]:
        return {}

    def retry(self, job_id: str) -> dict[str, object]:
        self.retried_job_id = job_id
        return {"id": job_id, "status": "Queued"}

    def cancel(self, job_id: str) -> dict[str, object]:
        self.cancelled_job_id = job_id
        return {"id": job_id, "status": "Cancelled"}

    def delete(self, job_id: str) -> dict[str, object]:
        self.deleted_job_id = job_id
        return {"id": job_id, "deleted": True}

    def clear_completed(self) -> dict[str, object]:
        self.cleared_completed = True
        return {"deleted": [], "failed": []}

    def close(self) -> None:
        pass


def test_http_queues_ordered_batch_results_with_mixed_item_errors():
    application = JobsApplicationFixture()
    client = TestClient(create_app(application))

    response = client.post(
        "/api/jobs/batch",
        json={
            "items": [
                {"media_path": "one.mkv", "subtitle_path": "one.srt"},
                {"media_path": "two.mkv", "subtitle_path": "two.srt"},
            ],
            "target_language_code": "zh-Hans",
            "term_map_mode": "none",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["id"] == "0"
    assert body["results"][0]["request"]["media_path"] == "one.mkv"
    assert body["results"][1] == {
        "error_code": "invalid_media_path",
        "message": "bad media",
    }


def test_http_lists_jobs_with_search_and_status_filters():
    application = JobsApplicationFixture()
    client = TestClient(create_app(application))

    response = client.get(
        "/api/jobs",
        params={
            "limit": 10,
            "cursor": "opaque",
            "search": " movie ",
            "status": "Failed",
        },
    )

    assert response.status_code == 200
    assert application.list_page_arguments == (10, "opaque", " movie ", "Failed")
    assert response.json()["matching_count"] == 0


def test_http_batch_preflight_rejects_invalid_term_map_without_calling_application():
    application = JobsApplicationFixture()
    client = TestClient(create_app(application))

    response = client.post(
        "/api/jobs/batch",
        json={
            "items": [{"media_path": "one.mkv", "subtitle_path": "one.srt"}],
            "target_language_code": "zh-Hans",
            "term_map_mode": "none",
            "term_map_id": "map-1",
        },
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"


def test_http_batch_surfaces_term_map_preflight_error_as_top_level_error():
    class PreflightErrorJobs(JobsApplicationFixture):
        def create_batch(self, _requests: list[object]) -> list[dict[str, object]]:
            raise ServiceError(
                "term_map_not_found", "Term map does not exist", id="missing"
            )

    response = TestClient(create_app(PreflightErrorJobs())).post(
        "/api/jobs/batch",
        json={
            "items": [{"media_path": "one.mkv", "subtitle_path": "one.srt"}],
            "target_language_code": "zh-Hans",
            "term_map_mode": "selected",
            "term_map_id": "missing",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "error_code": "term_map_not_found",
        "message": "Term map does not exist",
        "id": "missing",
    }


def test_http_batch_unexpected_error_uses_top_level_internal_error_envelope():
    class UnexpectedErrorJobs(JobsApplicationFixture):
        def create_batch(self, _requests: list[object]) -> list[dict[str, object]]:
            raise RuntimeError("unexpected")

    response = TestClient(
        create_app(UnexpectedErrorJobs()), raise_server_exceptions=False
    ).post(
        "/api/jobs/batch",
        json={
            "items": [{"media_path": "one.mkv", "subtitle_path": "one.srt"}],
            "target_language_code": "zh-Hans",
            "term_map_mode": "none",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "error_code": "internal_error",
        "message": "Operation failed",
    }


def test_http_retries_a_job_with_no_editable_request_body():
    application = JobsApplicationFixture()
    client = TestClient(create_app(application))

    response = client.post("/api/jobs/job-1/retry")

    assert response.status_code == 200
    assert response.json() == {"id": "job-1", "status": "Queued"}
    assert application.retried_job_id == "job-1"


def test_http_browse_serializes_episode_metadata():
    class EpisodeApplication(ApplicationFixture):
        def browse(self, request: BrowseRequest) -> BrowseResult:
            return BrowseResult(
                request.path,
                [
                    BrowseEntry(
                        "Episode.mkv",
                        Path("Episode.mkv"),
                        "media",
                        "Episode title",
                        season=2,
                        episode=7,
                    )
                ],
            )

    response = TestClient(create_app(EpisodeApplication())).post(
        "/api/media/browse", json={"path": "Series"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "path": "Series",
        "entries": [
            {
                "name": "Episode.mkv",
                "path": "Episode.mkv",
                "kind": "media",
                "title": "Episode title",
                "season": 2,
                "episode": 7,
            }
        ],
    }


def test_http_cancels_a_job_with_no_editable_request_body():
    application = JobsApplicationFixture()
    client = TestClient(create_app(application))

    response = client.post("/api/jobs/job-1/cancel")

    assert response.status_code == 200
    assert response.json() == {"id": "job-1", "status": "Cancelled"}
    assert application.cancelled_job_id == "job-1"


def test_http_returns_conflict_for_a_job_that_cannot_be_cancelled():
    class ConflictingJobs(JobsApplicationFixture):
        def cancel(self, _job_id: str) -> dict[str, object]:
            raise ServiceError(
                "job_cancel_conflict",
                "Only Queued Jobs can be cancelled",
                status="Completed",
            )

    response = TestClient(create_app(ConflictingJobs())).post("/api/jobs/job-1/cancel")

    assert response.status_code == 409
    assert response.json() == {
        "error_code": "job_cancel_conflict",
        "message": "Only Queued Jobs can be cancelled",
        "status": "Completed",
    }


def test_http_deletes_one_job_and_clears_completed_jobs_without_a_request_body():
    application = JobsApplicationFixture()
    client = TestClient(create_app(application))

    deleted = client.delete("/api/jobs/job-1")
    cleared = client.delete("/api/jobs/completed")

    assert deleted.status_code == 200
    assert deleted.json() == {"id": "job-1", "deleted": True}
    assert application.deleted_job_id == "job-1"
    assert cleared.status_code == 200
    assert cleared.json() == {"deleted": [], "failed": []}
    assert application.cleared_completed is True


def test_product_discover_resolves_relative_media_path_and_redacts_absolute_paths(
    tmp_path: Path,
):
    media_root = tmp_path / "media"
    media_root.mkdir()

    class RootApplication(ApplicationFixture):
        def discover(self, request: DiscoverRequest) -> DiscoverResult:
            result = super().discover(request)
            return DiscoverResult(
                request.media_path,
                [
                    SubtitleCandidateResult(
                        "external",
                        "srt",
                        {"language": "en", "title": ""},
                        path=media_root / "Movie.en.srt",
                    ),
                    *result.candidates[1:],
                ],
                result.unsupported_candidates,
            )

    application = RootApplication()
    client = TestClient(create_app(application, media_root))

    response = client.post("/api/media/discover", json={"path": "Movie.mkv"})

    assert response.status_code == 200
    assert response.json() == {
        "path": "Movie.mkv",
        **expected_discovery_payload("Movie.en.srt"),
    }
    assert application.discover_request == DiscoverRequest(media_root / "Movie.mkv")
    assert str(media_root) not in response.text


@pytest.mark.parametrize(
    "path", ["../outside.mkv", "/media/Movie.mkv", "inside\\Movie.mkv"]
)
def test_product_discover_rejects_paths_outside_relative_media_contract(
    tmp_path: Path, path: str
):
    media_root = tmp_path / "media"
    media_root.mkdir()

    response = TestClient(create_app(ApplicationFixture(), media_root)).post(
        "/api/media/discover", json={"path": path}
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_media_path"


def test_product_discover_redacts_absolute_paths_from_operation_errors(tmp_path: Path):
    media_root = tmp_path / "media"
    media_root.mkdir()

    class FailingApplication(ApplicationFixture):
        def discover(self, request: DiscoverRequest) -> DiscoverResult:
            raise ServiceError(
                "media_not_found", "Media does not exist", path=request.media_path
            )

    response = TestClient(create_app(FailingApplication(), media_root)).post(
        "/api/media/discover", json={"path": "Missing.mkv"}
    )

    assert response.status_code == 400
    assert response.json() == {
        "error_code": "media_not_found",
        "message": "Media does not exist",
        "path": "Missing.mkv",
    }
    assert str(media_root) not in response.text
