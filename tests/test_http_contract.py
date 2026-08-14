from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueweaver.adapters.output import AtomicOutputPublisher
from cueweaver.application.browsing import BrowseEntry, BrowseRequest, BrowseResult
from cueweaver.application.discovery import (
    DiscoverRequest,
    DiscoverResult,
    SubtitleCandidateResult,
    UnsupportedCandidateResult,
)
from cueweaver.application.errors import ServiceError
from cueweaver.application.extraction import ExtractRequest, ExtractResult
from cueweaver.application.translation import (
    TranslateRequest,
    TranslateResult,
    Translation,
)
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
            },
        ],
        "unsupported_candidates": [
            {"kind": "embedded", "stream_index": 4, "reason": "bitmap subtitle"}
        ],
    }


class ApplicationFixture:
    def __init__(self) -> None:
        self.discover_request: DiscoverRequest | None = None
        self.extract_request: ExtractRequest | None = None
        self.translate_request: TranslateRequest | None = None
        self.discovery = self
        self.extraction = self
        self.translation = self
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
                ),
            ],
            [UnsupportedCandidateResult("embedded", "bitmap subtitle", stream_index=4)],
        )

    def extract(self, request: ExtractRequest) -> ExtractResult:
        self.extract_request = request
        return ExtractResult(request.output_path, "ass")

    def translate(self, request: TranslateRequest) -> TranslateResult:
        self.translate_request = request
        return TranslateResult(request.output_path, request.target_language_code, "srt")

    def browse(self, request: BrowseRequest) -> BrowseResult:
        return BrowseResult(
            request.path,
            [BrowseEntry("Movie.mkv", Path("Movie.mkv"), "media", "Movie", 2024)],
        )


class PublicTranslator:
    def __init__(self, error: bool = False) -> None:
        self.error = error

    def translate(self, *_args, **_kwargs) -> bytes:
        if self.error:
            raise RuntimeError("translation failed")
        return b"translated"


class PublicTranslationApplication(ApplicationFixture):
    def __init__(self, translator: PublicTranslator) -> None:
        super().__init__()
        self.translation = Translation(translator, AtomicOutputPublisher())


def test_http_routes_requests_to_operations_and_serializes_results():
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
            "overwrite": True,
        },
    )
    browse = client.post("/api/media/browse", json={"path": "Shows"})

    assert discover.status_code == 200
    assert discover.headers["content-type"] == "application/json"
    assert discover.json() == {
        "media_path": "/media/Movie.mkv",
        **expected_discovery_payload("/media/Movie.en.srt"),
    }
    assert application.discover_request == DiscoverRequest(Path("/media/Movie.mkv"))
    assert extract.json() == {"output_path": "/work/Movie.ass", "format": "ass"}
    assert application.extract_request == ExtractRequest(
        Path("/media/Movie.mkv"), 3, Path("/work/Movie.ass")
    )
    assert translate.json() == {
        "output_path": "/media/Movie.zh.srt",
        "target_language_code": "zh-Hans",
        "format": "srt",
    }
    assert application.translate_request == TranslateRequest(
        Path("/work/Movie.en.srt"),
        "zh-Hans",
        Path("/media/Movie.zh.srt"),
        Path("/work/job-1"),
        None,
        False,
        True,
        True,
    )
    assert browse.json() == {
        "path": "Shows",
        "entries": [
            {
                "name": "Movie.mkv",
                "path": "Movie.mkv",
                "kind": "media",
                "title": "Movie",
                "year": 2024,
            }
        ],
    }


def post_public_translation(client: TestClient, subtitle: Path, output: Path):
    return client.post(
        "/api/translate",
        json={
            "subtitle_path": str(subtitle),
            "target_language_code": "zh",
            "output_path": str(output),
            "work_directory": str(subtitle.parent / "work"),
            "overwrite": True,
        },
    )


def test_http_translate_atomically_overwrites_after_success(tmp_path: Path):
    subtitle = tmp_path / "Movie.srt"
    output = tmp_path / "Movie.zh.srt"
    subtitle.write_text("source", encoding="utf-8")
    output.write_text("old", encoding="utf-8")
    client = TestClient(create_app(PublicTranslationApplication(PublicTranslator())))

    response = post_public_translation(client, subtitle, output)

    assert response.status_code == 200
    assert output.read_bytes() == b"translated"


def test_http_translate_overwrite_preserves_old_output_on_failure(tmp_path: Path):
    subtitle = tmp_path / "Movie.srt"
    output = tmp_path / "Movie.zh.srt"
    subtitle.write_text("source", encoding="utf-8")
    output.write_text("old", encoding="utf-8")
    client = TestClient(
        create_app(PublicTranslationApplication(PublicTranslator(error=True)))
    )

    response = post_public_translation(client, subtitle, output)

    assert response.status_code == 400
    assert response.json()["error_code"] == "translation_failed"
    assert output.read_text(encoding="utf-8") == "old"


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


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("/api/discover", {"media_path": 3}, "media_path"),
        (
            "/api/extract",
            {"media_path": "x", "stream_index": "3", "output_path": "x.srt"},
            "stream_index",
        ),
        ("/api/translate", {}, "subtitle_path"),
        (
            "/api/translate",
            {
                "subtitle_path": "a.srt",
                "target_language_code": "zh",
                "output_path": "a.srt",
                "work_directory": "work",
                "media_path": "removed",
            },
            "media_path",
        ),
    ],
)
def test_http_validates_request_bodies(path, body, field):
    response = TestClient(create_app(ApplicationFixture())).post(path, json=body)

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"
    assert response.json()["field"] == field


def test_http_rejects_non_json_request_content():
    response = TestClient(create_app(ApplicationFixture())).post(
        "/api/discover",
        content='{"media_path":"/media/Movie.mkv"}',
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error_code": "invalid_request",
        "message": "Request must use application/json",
    }


def test_http_returns_error_envelopes_for_malformed_unknown_and_operation_errors():
    class FailingApplication(ApplicationFixture):
        def discover(self, request: DiscoverRequest) -> DiscoverResult:
            raise ServiceError(
                "discovery_failed", "ffprobe failed", path=request.media_path
            )

        def translate(self, request: TranslateRequest) -> TranslateResult:
            raise RuntimeError("unexpected")

    client = TestClient(create_app(FailingApplication()), raise_server_exceptions=False)

    malformed = client.post(
        "/api/discover", content="{", headers={"content-type": "application/json"}
    )
    unknown = client.post("/api/missing")
    service_error = client.post(
        "/api/discover", json={"media_path": "/media/Movie.mkv"}
    )
    unexpected = client.post(
        "/api/translate",
        json={
            "subtitle_path": "/work/Movie.srt",
            "target_language_code": "zh",
            "output_path": "/media/Movie.zh.srt",
            "work_directory": "/work/job-1",
        },
    )

    assert malformed.json()["error_code"] == "invalid_request"
    assert unknown.json() == {
        "error_code": "invalid_request",
        "message": "Request failed",
    }
    assert service_error.json() == {
        "error_code": "discovery_failed",
        "message": "ffprobe failed",
        "path": "/media/Movie.mkv",
    }
    assert unexpected.json() == {
        "error_code": "internal_error",
        "message": "Operation failed",
    }
