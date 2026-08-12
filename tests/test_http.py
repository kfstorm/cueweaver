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
    _write_output,
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

    response = TestClient(
        create_app(FailingApplication()), raise_server_exceptions=False
    ).post("/api/discover", json={"media_path": "/media/Movie.mkv"})

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


SRT = """1
00:00:01,000 --> 00:00:02,000
Hello
"""


def test_http_translates_an_explicit_subtitle_to_an_explicit_output(
    tmp_path, monkeypatch
):
    subtitle = tmp_path / "Movie.en.SRT"
    subtitle.write_text(SRT, encoding="utf-8")
    output = tmp_path / "media" / "Movie.zh.srt"
    work_directory = tmp_path / "work" / "job-123"
    term_map = tmp_path / "terms.json"
    term_map.write_text('{"Hello":"你好"}', encoding="utf-8")
    captured: dict[str, object] = {}

    def translate(self, source, target_language, **kwargs):
        captured.update(source=source, target_language=target_language, **kwargs)
        return SRT.replace("Hello", "你好").encode()

    monkeypatch.setattr(
        "cueweaver.application.PySubtransTranslator.translate", translate
    )

    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/translate",
        json={
            "subtitle_path": str(subtitle),
            "target_language_code": "zh-Hans-SG",
            "output_path": str(output),
            "work_directory": str(work_directory),
            "term_map_path": str(term_map),
            "subtitle_terminology_filter_enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "output_path": str(output),
        "target_language_code": "zh-Hans-SG",
        "format": "srt",
    }
    assert output.read_text(encoding="utf-8") == SRT.replace("Hello", "你好")
    assert work_directory.is_dir()
    assert captured == {
        "source": subtitle,
        "target_language": "zh-Hans-SG",
        "user_overrides": {"Hello": "你好"},
        "work_directory": work_directory,
        "dynamic_terminology_enabled": True,
        "subtitle_terminology_filter_enabled": False,
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {
            "subtitle_path": "",
            "target_language_code": "zh",
            "output_path": "a.srt",
            "work_directory": "work",
        },
        {
            "subtitle_path": "a.srt",
            "target_language_code": "",
            "output_path": "b.srt",
            "work_directory": "work",
        },
    ],
)
def test_http_translate_uses_error_envelope_for_invalid_requests(body):
    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/translate", json=body
    )

    assert response.status_code >= 400
    assert set(response.json()).issuperset({"error_code", "message", "field"})


@pytest.mark.parametrize("removed_field", ["media_path", "source_language", "no_op"])
def test_http_translate_rejects_removed_request_fields(removed_field):
    response = TestClient(create_app(ApplicationFixture())).post(
        "/api/translate",
        json={
            "subtitle_path": "/work/Movie.srt",
            "target_language_code": "zh-Hans",
            "output_path": "/media/Movie.zh.srt",
            "work_directory": "/work/job-123",
            removed_field: "removed",
        },
    )

    assert response.status_code >= 400
    assert response.json()["field"] == removed_field


@pytest.mark.parametrize(
    ("term_map_content", "expected_code"),
    [
        (None, "subtitle_not_found"),
        ("[]", "invalid_term_map"),
        ('{"":"x"}', "invalid_term_map"),
    ],
)
def test_http_translate_uses_error_envelope_for_invalid_inputs(
    tmp_path, term_map_content, expected_code
):
    subtitle = tmp_path / "Movie.srt"
    subtitle.write_text(SRT, encoding="utf-8")
    term_map = tmp_path / "terms.json"
    if term_map_content is not None:
        term_map.write_text(term_map_content, encoding="utf-8")
    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/translate",
        json={
            "subtitle_path": str(tmp_path / "missing.srt")
            if term_map_content is None
            else str(subtitle),
            "target_language_code": "zh",
            "output_path": str(tmp_path / "output.srt"),
            "work_directory": str(tmp_path / "work"),
            "term_map_path": str(term_map) if term_map_content is not None else None,
        },
    )

    assert response.status_code >= 400
    assert response.json()["error_code"] == expected_code


@pytest.mark.parametrize("term_map_content", [None, "{", '{"Hello":""}'])
def test_http_translate_uses_error_envelope_for_missing_or_malformed_term_map(
    tmp_path, term_map_content
):
    subtitle = tmp_path / "Movie.srt"
    subtitle.write_text(SRT, encoding="utf-8")
    term_map = tmp_path / "terms.json"
    if term_map_content is not None:
        term_map.write_text(term_map_content, encoding="utf-8")

    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/translate",
        json={
            "subtitle_path": str(subtitle),
            "target_language_code": "zh",
            "output_path": str(tmp_path / "output.srt"),
            "work_directory": str(tmp_path / "work"),
            "term_map_path": str(term_map),
        },
    )

    assert response.status_code >= 400
    assert response.json()["error_code"] == "invalid_term_map"


def test_http_translate_rejects_format_errors_and_existing_output(
    tmp_path, monkeypatch
):
    subtitle = tmp_path / "Movie.srt"
    subtitle.write_text("not subtitles", encoding="utf-8")
    valid_subtitle = tmp_path / "Valid.srt"
    valid_subtitle.write_text(SRT, encoding="utf-8")
    output = tmp_path / "output.ass"
    client = TestClient(create_app(CueWeaverApplication()))
    monkeypatch.setattr(
        "cueweaver.application.PySubtransTranslator.translate",
        lambda *_args, **_kwargs: b"translated subtitles",
    )

    mismatch = client.post(
        "/api/translate",
        json={
            "subtitle_path": str(subtitle),
            "target_language_code": "zh",
            "output_path": str(output),
            "work_directory": str(tmp_path / "work"),
        },
    )
    invalid = client.post(
        "/api/translate",
        json={
            "subtitle_path": str(subtitle),
            "target_language_code": "zh",
            "output_path": str(tmp_path / "output.srt"),
            "work_directory": str(tmp_path / "work"),
        },
    )
    existing = tmp_path / "existing.srt"
    existing.write_text("existing", encoding="utf-8")
    exists = client.post(
        "/api/translate",
        json={
            "subtitle_path": str(valid_subtitle),
            "target_language_code": "zh",
            "output_path": str(existing),
            "work_directory": str(tmp_path / "work"),
        },
    )

    assert mismatch.status_code >= 400
    assert invalid.status_code == 200
    assert exists.status_code >= 400
    assert set(mismatch.json()).issuperset({"error_code", "message"})
    assert set(exists.json()).issuperset({"error_code", "message"})


@pytest.mark.parametrize(
    ("subtitle_name", "output_name", "expected_error_code"),
    [
        ("Movie.SRT", "output.srt", None),
        ("Movie.srt", "output.txt", "unsupported_subtitle_format"),
        ("Movie.txt", "output.txt", "unsupported_subtitle_format"),
    ],
)
def test_http_translate_enforces_the_extension_contract(
    tmp_path, monkeypatch, subtitle_name, output_name, expected_error_code
):
    subtitle = tmp_path / subtitle_name
    subtitle.write_bytes(b"\xff")
    captured: dict[str, Path] = {}

    def translate(self, source, *_args, **_kwargs):
        captured["source"] = source
        return b"translated subtitles"

    monkeypatch.setattr(
        "cueweaver.application.PySubtransTranslator.translate", translate
    )
    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/translate",
        json={
            "subtitle_path": str(subtitle),
            "target_language_code": "zh",
            "output_path": str(tmp_path / output_name),
            "work_directory": str(tmp_path / "work"),
        },
    )

    if expected_error_code is None:
        assert response.status_code == 200
        assert captured == {"source": subtitle}
    else:
        assert response.status_code >= 400
        assert response.json()["error_code"] == expected_error_code
        assert captured == {}


def test_http_translate_uses_error_envelope_without_writing_output_on_translation_failure(
    tmp_path, monkeypatch
):
    subtitle = tmp_path / "Movie.srt"
    subtitle.write_text(SRT, encoding="utf-8")

    def translate(self, source, target_language, **kwargs):
        raise RuntimeError("PySubtrans could not parse subtitle")

    monkeypatch.setattr(
        "cueweaver.application.PySubtransTranslator.translate", translate
    )
    response = TestClient(create_app(CueWeaverApplication())).post(
        "/api/translate",
        json={
            "subtitle_path": str(subtitle),
            "target_language_code": "zh",
            "output_path": str(tmp_path / "output.srt"),
            "work_directory": str(tmp_path / "work"),
        },
    )

    assert response.status_code >= 400
    assert response.json()["error_code"] == "translation_failed"
    assert not (tmp_path / "output.srt").exists()


def test_http_translate_uses_error_envelope_for_unexpected_application_failures():
    class FailingApplication(ApplicationFixture):
        def translate(self, request: TranslateRequest) -> TranslateResult:
            raise RuntimeError("unexpected")

    response = TestClient(
        create_app(FailingApplication()), raise_server_exceptions=False
    ).post(
        "/api/translate",
        json={
            "subtitle_path": "/work/Movie.srt",
            "target_language_code": "zh",
            "output_path": "/media/Movie.zh.srt",
            "work_directory": "/work/job-123",
        },
    )

    assert response.status_code >= 400
    assert response.json() == {
        "error_code": "internal_error",
        "message": "Operation failed",
    }


def test_output_write_failure_removes_its_temporary_file(tmp_path, monkeypatch):
    output = tmp_path / "Movie.zh.srt"

    def fail_to_publish(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("cueweaver.application.os.link", fail_to_publish)

    with pytest.raises(ServiceError, match="Output cannot be written"):
        _write_output(output, b"translated")

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
