from pathlib import Path

from fastapi.testclient import TestClient

from cueweaver.application import (
    DiscoverRequest,
    DiscoverResult,
    ExtractRequest,
    ExtractResult,
    ServiceError,
    TranslateRequest,
    TranslateResult,
)
from cueweaver.http import create_app


class ApplicationFixture:
    def __init__(self):
        self.discover_request: DiscoverRequest | None = None
        self.extract_request: ExtractRequest | None = None
        self.translate_request: TranslateRequest | None = None

    def discover(self, request: DiscoverRequest) -> DiscoverResult:
        self.discover_request = request
        return DiscoverResult(media_path=request.media_path)

    def extract(self, request: ExtractRequest) -> ExtractResult:
        self.extract_request = request
        return ExtractResult(output_path=request.output_path, format="ass")

    def translate(self, request: TranslateRequest) -> TranslateResult:
        self.translate_request = request
        return TranslateResult(
            output_path=request.output_path,
            target_language_code=request.target_language_code,
            format="srt",
        )


def test_http_service_routes_json_requests_to_the_application_seam():
    application = ApplicationFixture()
    client = TestClient(create_app(application))

    discover = client.post("/api/discover", json={"media_path": "/media/Movie.mkv"})
    extract = client.post(
        "/api/extract",
        json={
            "media_path": "/media/Movie.mkv",
            "stream_index": 3,
            "output_path": "/work/Movie.ass",
        },
    )
    translate = client.post(
        "/api/translate",
        json={
            "subtitle_path": "/work/Movie.en.srt",
            "target_language_code": "zh-Hans",
            "output_path": "/media/Movie.zh.srt",
            "work_directory": "/work/job-1",
            "dynamic_terminology_enabled": False,
        },
    )

    assert discover.status_code == 200
    assert discover.headers["content-type"] == "application/json"
    assert discover.json() == {
        "media_path": "/media/Movie.mkv",
        "candidates": [],
        "unsupported_candidates": [],
    }
    assert application.discover_request == DiscoverRequest(Path("/media/Movie.mkv"))
    assert extract.status_code == 200
    assert extract.json() == {
        "output_path": "/work/Movie.ass",
        "format": "ass",
    }
    assert application.extract_request == ExtractRequest(
        media_path=Path("/media/Movie.mkv"),
        stream_index=3,
        output_path=Path("/work/Movie.ass"),
    )
    assert translate.status_code == 200
    assert translate.json() == {
        "output_path": "/media/Movie.zh.srt",
        "target_language_code": "zh-Hans",
        "format": "srt",
    }
    assert application.translate_request == TranslateRequest(
        subtitle_path=Path("/work/Movie.en.srt"),
        target_language_code="zh-Hans",
        output_path=Path("/media/Movie.zh.srt"),
        work_directory=Path("/work/job-1"),
        dynamic_terminology_enabled=False,
    )


def test_http_service_returns_the_shared_error_envelope():
    class FailingApplication(ApplicationFixture):
        def discover(self, request: DiscoverRequest) -> DiscoverResult:
            raise ServiceError(
                "discovery_failed", "ffprobe failed", path=request.media_path
            )

    response = TestClient(create_app(FailingApplication())).post(
        "/api/discover", json={"media_path": "/media/Movie.mkv"}
    )

    assert response.status_code >= 400
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "error_code": "discovery_failed",
        "message": "ffprobe failed",
        "path": "/media/Movie.mkv",
    }


def test_http_service_returns_json_errors_for_invalid_requests():
    response = TestClient(create_app(ApplicationFixture())).post(
        "/api/discover", json={"media_path": 3}
    )

    assert response.status_code >= 400
    payload = response.json()
    assert set(payload).issuperset({"error_code", "message", "field"})
    assert payload["field"] == "media_path"


def test_http_service_uses_the_shared_error_envelope_for_unknown_routes():
    response = TestClient(create_app(ApplicationFixture())).post("/api/missing")

    assert response.status_code >= 400
    assert set(response.json()).issuperset({"error_code", "message"})
