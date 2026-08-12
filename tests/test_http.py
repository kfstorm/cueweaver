import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueweaver.application import (
    CueWeaverApplication,
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


def test_http_service_returns_json_errors_for_malformed_json_requests():
    response = TestClient(create_app(ApplicationFixture())).post(
        "/api/discover",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code >= 400
    assert set(response.json()).issuperset({"error_code", "message", "field"})


def test_http_service_uses_the_shared_error_envelope_for_unknown_routes():
    response = TestClient(create_app(ApplicationFixture())).post("/api/missing")

    assert response.status_code >= 400
    assert set(response.json()).issuperset({"error_code", "message"})


def test_http_discovery_reports_external_embedded_and_unsupported_candidates(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    (tmp_path / "Movie.en.forced.srt").write_text("subtitles", encoding="utf-8")
    (tmp_path / "Movie.zh-Hans.default.ass").write_text("subtitles", encoding="utf-8")

    def run(*_args, **_kwargs):
        return type(
            "CompletedProcessFixture",
            (),
            {
                "stdout": json.dumps(
                    {
                        "streams": [
                            {
                                "index": 3,
                                "codec_name": "ass",
                                "tags": {
                                    "language": "zhs",
                                    "title": "Chinese Simplified",
                                },
                            },
                            {"index": 4, "codec_name": "hdmv_pgs_subtitle"},
                            {"index": 5, "codec_name": "dvb_subtitle"},
                        ]
                    }
                )
            },
        )()

    monkeypatch.setattr("cueweaver.application.subprocess.run", run)

    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/discover", json={"media_path": str(media)}
    )

    assert response.status_code == 200
    assert response.json() == {
        "media_path": str(media),
        "candidates": [
            {
                "kind": "external",
                "path": str(tmp_path / "Movie.en.forced.srt"),
                "format": "srt",
                "tags": {"language": "en", "title": ""},
            },
            {
                "kind": "external",
                "path": str(tmp_path / "Movie.zh-Hans.default.ass"),
                "format": "ass",
                "tags": {"language": "zh-Hans", "title": ""},
            },
            {
                "kind": "embedded",
                "stream_index": 3,
                "format": "ass",
                "tags": {"language": "zhs", "title": "Chinese Simplified"},
            },
        ],
        "unsupported_candidates": [
            {"kind": "embedded", "stream_index": 4, "reason": "bitmap subtitle"},
            {
                "kind": "embedded",
                "stream_index": 5,
                "reason": "unsupported subtitle codec: dvb_subtitle",
            },
        ],
    }


def test_http_discovery_uses_error_envelope_for_missing_media(tmp_path):
    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/discover", json={"media_path": str(tmp_path / "missing.mkv")}
    )

    assert response.status_code >= 400
    assert set(response.json()).issuperset({"error_code", "message"})


def test_http_discovery_uses_error_envelope_when_ffprobe_fails(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    (tmp_path / "Movie.en.srt").write_text("subtitles", encoding="utf-8")

    def run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "ffprobe")

    monkeypatch.setattr("cueweaver.application.subprocess.run", run)

    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/discover", json={"media_path": str(media)}
    )

    assert response.status_code >= 400
    assert set(response.json()).issuperset({"error_code", "message"})


@pytest.mark.parametrize(
    ("codec", "extension", "format"),
    [("subrip", "srt", "srt"), ("ssa", "ass", "ass"), ("webvtt", "vtt", "vtt")],
)
def test_http_extracts_a_matching_text_embedded_subtitle(
    tmp_path, monkeypatch, codec, extension, format
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    output = tmp_path / "work" / f"Movie.en.{extension}"
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return type(
                "CompletedProcessFixture",
                (),
                {
                    "stdout": json.dumps(
                        {"streams": [{"index": 3, "codec_name": codec}]}
                    )
                },
            )()
        output.write_text("[Events]\n", encoding="utf-8")
        return type("CompletedProcessFixture", (), {"stdout": ""})()

    monkeypatch.setattr("cueweaver.application.subprocess.run", run)

    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(output),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"output_path": str(output), "format": format}
    assert commands[1] == [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(media),
        "-map",
        "0:3",
        "-c:s",
        "copy",
        str(output),
    ]


def test_http_extract_uses_error_envelope_for_invalid_requests(tmp_path):
    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(tmp_path / "Movie.mkv"),
            "stream_index": "3",
            "output_path": str(tmp_path / "Movie.srt"),
        },
    )

    assert response.status_code >= 400
    assert set(response.json()).issuperset({"error_code", "message", "field"})
    assert response.json()["field"] == "stream_index"


def test_http_extract_uses_error_envelope_for_invalid_media_and_output_paths(tmp_path):
    missing = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(tmp_path / "missing.mkv"),
            "stream_index": 3,
            "output_path": str(tmp_path / "Movie.srt"),
        },
    )
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    (tmp_path / "not-a-directory").write_text("file", encoding="utf-8")
    invalid_output = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(tmp_path / "not-a-directory" / "Movie.srt"),
        },
    )

    assert missing.status_code >= 400
    assert set(missing.json()).issuperset({"error_code", "message"})
    assert invalid_output.status_code >= 400
    assert set(invalid_output.json()).issuperset({"error_code", "message"})


def test_http_extract_uses_error_envelope_for_existing_or_unsupported_outputs(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    output = tmp_path / "Movie.srt"
    output.write_text("existing", encoding="utf-8")

    existing = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(output),
        },
    )
    unsupported = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(tmp_path / "Movie.txt"),
        },
    )

    assert existing.status_code >= 400
    assert set(existing.json()).issuperset({"error_code", "message"})
    assert unsupported.status_code >= 400
    assert set(unsupported.json()).issuperset({"error_code", "message"})


def test_http_extract_uses_error_envelope_for_stream_and_process_failures(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")

    def unsupported_stream(*_args, **_kwargs):
        return type(
            "CompletedProcessFixture",
            (),
            {
                "stdout": json.dumps(
                    {"streams": [{"index": 3, "codec_name": "hdmv_pgs_subtitle"}]}
                )
            },
        )()

    monkeypatch.setattr("cueweaver.application.subprocess.run", unsupported_stream)
    bitmap = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(tmp_path / "Movie.srt"),
        },
    )

    def failed_process(command, **_kwargs):
        if command[0] == "ffprobe":
            return type(
                "CompletedProcessFixture",
                (),
                {
                    "stdout": json.dumps(
                        {"streams": [{"index": 3, "codec_name": "subrip"}]}
                    )
                },
            )()
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("cueweaver.application.subprocess.run", failed_process)
    failed = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(tmp_path / "Movie2.srt"),
        },
    )

    assert bitmap.status_code >= 400
    assert set(bitmap.json()).issuperset({"error_code", "message"})
    assert failed.status_code >= 400
    assert set(failed.json()).issuperset({"error_code", "message"})


def test_http_extract_rejects_codecs_outside_the_explicit_mapping(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")

    def unsupported_stream(*_args, **_kwargs):
        return type(
            "CompletedProcessFixture",
            (),
            {
                "stdout": json.dumps(
                    {"streams": [{"index": 3, "codec_name": "mov_text"}]}
                )
            },
        )()

    monkeypatch.setattr("cueweaver.application.subprocess.run", unsupported_stream)
    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(tmp_path / "Movie.srt"),
        },
    )

    assert response.status_code >= 400
    assert set(response.json()).issuperset({"error_code", "message"})


def test_http_extract_uses_error_envelope_for_format_mismatch_and_ffprobe_failure(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")

    def mismatched_stream(*_args, **_kwargs):
        return type(
            "CompletedProcessFixture",
            (),
            {"stdout": json.dumps({"streams": [{"index": 3, "codec_name": "ass"}]})},
        )()

    monkeypatch.setattr("cueweaver.application.subprocess.run", mismatched_stream)
    mismatch = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(tmp_path / "Movie.srt"),
        },
    )

    def failed_probe(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "ffprobe")

    monkeypatch.setattr("cueweaver.application.subprocess.run", failed_probe)
    failed = TestClient(create_app(CueWeaverApplication())).post(
        "/api/extract",
        json={
            "media_path": str(media),
            "stream_index": 3,
            "output_path": str(tmp_path / "Movie2.srt"),
        },
    )

    assert mismatch.status_code >= 400
    assert set(mismatch.json()).issuperset({"error_code", "message"})
    assert failed.status_code >= 400
    assert set(failed.json()).issuperset({"error_code", "message"})
