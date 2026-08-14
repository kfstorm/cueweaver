import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueweaver.adapters.output import AtomicOutputPublisher
from cueweaver.application.errors import ServiceError
from cueweaver.application.extraction import Extraction
from cueweaver.application.jobs import CreateJobRequest, Jobs
from cueweaver.product import create_product_app

SRT = b"1\n00:00:00,000 --> 00:00:01,000\nTranslated\n"


class FakeTranslator:
    def __init__(
        self,
        *,
        delay: threading.Event | None = None,
        error: Exception | None = None,
        available: bool = True,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ):
        self.available = available
        self.delay = delay
        self.error = error
        self.started = started
        self.release = release
        self.sources: list[Path] = []

    def translate(self, source: Path, target_language: str, **kwargs: object) -> bytes:
        self.sources.append(source)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(timeout=5)
        if self.delay is not None:
            self.delay.wait(timeout=5)
        if self.error is not None:
            raise self.error
        return SRT


class MediaExtractorFixture:
    def __init__(
        self,
        streams: list[dict[str, object]],
        *,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ):
        self.streams = streams
        self.started = started
        self.release = release
        self.probe_calls: list[Path] = []
        self.extract_calls: list[tuple[Path, int, Path]] = []

    def probe_subtitle_streams(self, media_path: Path) -> list[dict[str, object]]:
        self.probe_calls.append(media_path)
        return self.streams

    def extract_subtitle(
        self, media_path: Path, stream_index: int, output_path: Path
    ) -> None:
        self.extract_calls.append((media_path, stream_index, output_path))
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(timeout=5)
        output_path.write_bytes(SRT)


class RecordingTranslator(FakeTranslator):
    def __init__(self, delay: threading.Event, started: threading.Event | None = None):
        self.started = started or threading.Event()
        super().__init__(delay=delay, started=self.started)
        self.calls: list[dict[str, object]] = []

    def translate(self, source: Path, target_language: str, **kwargs: object) -> bytes:
        self.calls.append({"target": target_language, **kwargs})
        return super().translate(source, target_language, **kwargs)


def make_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    media_root = tmp_path / "media"
    media_root.mkdir()
    media = media_root / "Movie.mkv"
    subtitle = media_root / "Movie.en.srt"
    media.write_bytes(b"media")
    subtitle.write_bytes(SRT)
    return media_root, tmp_path / "work", media, subtitle


def make_client(
    media_root: Path, work_root: Path, translator: FakeTranslator
) -> TestClient:
    static_root = work_root.parent / "static"
    static_root.mkdir(exist_ok=True)
    (static_root / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    return TestClient(
        create_product_app(
            media_root,
            work_root,
            translator,
            static_root=static_root,
        )
    )


def create_job(client: TestClient, target: str = "zh-Hans"):
    return client.post(
        "/api/jobs",
        json={
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": target,
        },
    )


def persisted_external_job(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, object], Path, dict[str, object]]:
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    with make_client(media_root, work_root, FakeTranslator()) as client:
        queued = create_job(client).json()
        wait_for_status(client, queued["id"], "Completed")
    record_path = work_root / "jobs" / f"{queued['id']}.json"
    return (
        media_root,
        work_root,
        queued,
        record_path,
        json.loads(record_path.read_text(encoding="utf-8")),
    )


def wait_for_status(
    client: TestClient, job_id: str, expected: str
) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        body = response.json()
        if body["status"] == expected:
            return body
        time.sleep(0.01)
    pytest.fail(f"Job did not reach {expected}")


def wait_for_status_from_jobs(
    jobs: Jobs, job_id: str, expected: str
) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = jobs.get(job_id)
        if body["status"] == expected:
            return body
        time.sleep(0.01)
    pytest.fail(f"Job did not reach {expected}")


def test_job_returns_queued_keeps_api_responsive_and_persists_success(tmp_path: Path):
    media_root, work_root, media, _subtitle = make_roots(tmp_path)
    release = threading.Event()
    with make_client(media_root, work_root, FakeTranslator(delay=release)) as client:
        response = create_job(client)

        assert response.status_code == 200
        queued = response.json()
        assert queued["status"] == "Queued"
        assert queued["request"] == {
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": "zh-Hans",
            "term_map": None,
            "dynamic_terminology_enabled": True,
            "subtitle_terminology_filter_enabled": True,
            "output_suffix": "zh-Hans",
            "output_conflict_policy": "append-number",
            "output_path": "Movie.zh-Hans.srt",
            "source_format": "srt",
        }
        assert queued["queue_position"] == 1
        assert client.get("/api/status").status_code == 200
        assert client.get(f"/api/jobs/{queued['id']}").json()["status"] in {
            "Queued",
            "Translating",
        }
        record_path = work_root / "jobs" / f"{queued['id']}.json"
        assert record_path.is_file()
        assert json.loads(record_path.read_text(encoding="utf-8"))["status"] in {
            "Queued",
            "Translating",
        }

        release.set()
        completed = wait_for_status(client, queued["id"], "Completed")

        assert completed["finished_at"]
        assert (media.parent / "Movie.zh-Hans.srt").read_bytes() == SRT
        assert not (work_root / "jobs" / queued["id"]).exists()

    restarted = make_client(media_root, work_root, FakeTranslator())
    assert restarted.get(f"/api/jobs/{queued['id']}").json() == completed
    restarted.close()


def test_failed_job_retains_work_directory_and_structured_error(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    client = make_client(
        media_root, work_root, FakeTranslator(error=RuntimeError("boom"))
    )

    queued = create_job(client).json()
    failed = wait_for_status(client, queued["id"], "Failed")

    assert failed["error"]["code"] == "translation_failed"
    assert failed["error"]["message"] == "Translation failed"
    assert (work_root / "jobs" / queued["id"]).is_dir()
    assert not (media_root / "Movie.zh-Hans.srt").exists()


@pytest.mark.parametrize(
    ("codec", "source_format"),
    [("subrip", "srt"), ("ass", "ass"), ("webvtt", "vtt")],
)
def test_embedded_job_extracts_in_work_directory_before_translation(
    tmp_path: Path, codec: str, source_format: str
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    extracting = threading.Event()
    release = threading.Event()
    translating = threading.Event()
    translation_release = threading.Event()
    media_adapter = MediaExtractorFixture(
        [{"index": 3, "codec_name": codec}],
        started=extracting,
        release=release,
    )
    extraction = Extraction(media_adapter, AtomicOutputPublisher())
    translator = FakeTranslator(started=translating, release=translation_release)

    jobs = Jobs(
        translator,
        media_root,
        work_root,
        extraction=extraction,
    )
    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            None,
            "zh-Hans",
            stream_index=3,
            source_format=source_format,
        )
    )
    assert queued["status"] == "Queued"

    assert extracting.wait(timeout=5)
    extracting_record = jobs.get(str(queued["id"]))
    assert extracting_record["status"] == "Extracting"
    assert "subtitle_path" not in extracting_record["request"]
    assert extracting_record["request"]["stream_index"] == 3

    release.set()
    assert translating.wait(timeout=5)
    assert jobs.get(str(queued["id"]))["status"] == "Translating"
    translation_release.set()
    deadline = time.monotonic() + 5
    while jobs.get(str(queued["id"]))["status"] != "Completed":
        if time.monotonic() >= deadline:
            pytest.fail("Embedded Job did not complete")
        time.sleep(0.01)

    completed = jobs.get(str(queued["id"]))
    assert completed["request"]["output_path"] == f"Movie.zh-Hans.{source_format}"
    assert media_adapter.probe_calls == [_media]
    assert media_adapter.extract_calls[0][:2] == (_media, 3)
    extracted_path = media_adapter.extract_calls[0][2]
    assert extracted_path.parent == work_root / "jobs" / str(queued["id"])
    assert extracted_path.name.startswith(f".source.{source_format}.")
    assert extracted_path.suffix == f".{source_format}"
    assert translator.sources == [
        work_root / "jobs" / str(queued["id"]) / f"source.{source_format}"
    ]
    assert (media_root / f"Movie.zh-Hans.{source_format}").read_bytes() == SRT
    assert not (work_root / "jobs" / str(queued["id"])).exists()
    jobs.close()


@pytest.mark.parametrize(
    ("streams", "source_format", "error_code"),
    [
        ([], "srt", "stream_not_found"),
        ([{"index": 3, "codec_name": "mov_text"}], "srt", "unsupported_stream"),
        ([{"index": 3, "codec_name": "ass"}], "srt", "format_mismatch"),
    ],
)
def test_embedded_extraction_failure_does_not_translate_or_publish(
    tmp_path: Path,
    streams: list[dict[str, object]],
    source_format: str,
    error_code: str,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    translator = FakeTranslator()
    media_adapter = MediaExtractorFixture(streams)
    extraction = Extraction(media_adapter, AtomicOutputPublisher())
    jobs = Jobs(translator, media_root, work_root, extraction=extraction)

    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            None,
            "zh-Hans",
            stream_index=3,
            source_format=source_format,
        )
    )
    deadline = time.monotonic() + 5
    while jobs.get(str(queued["id"]))["status"] != "Failed":
        if time.monotonic() >= deadline:
            pytest.fail("Embedded Job did not fail")
        time.sleep(0.01)

    failed = jobs.get(str(queued["id"]))
    assert failed["error"]["code"] == error_code
    assert failed["error"]["media_path"] == "Movie.mkv"
    assert failed["error"]["stream_index"] == 3
    assert translator.sources == []
    assert not (media_root / "Movie.zh-Hans.srt").exists()
    jobs.close()


def test_http_accepts_embedded_stream_without_an_extraction_path(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    with make_client(media_root, work_root, FakeTranslator()) as client:
        response = client.post(
            "/api/jobs",
            json={
                "media_path": "Movie.mkv",
                "stream_index": 3,
                "source_format": "srt",
                "target_language_code": "zh-Hans",
            },
        )

        assert response.status_code == 200
        request = response.json()["request"]
        assert request["media_path"] == "Movie.mkv"
        assert request["stream_index"] == 3
        assert request["source_format"] == "srt"
        assert "subtitle_path" not in request


def test_embedded_job_redacts_absolute_error_paths(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)

    class FailingExtraction:
        def extract(self, request):
            raise ServiceError(
                "extraction_failed",
                "Extraction failed",
                path=request.media_path,
                output_path=request.output_path,
            )

    jobs = Jobs(
        FakeTranslator(),
        media_root,
        work_root,
        extraction=FailingExtraction(),
    )
    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            None,
            "zh-Hans",
            stream_index=3,
            source_format="srt",
        )
    )

    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")

    assert failed["error"] == {
        "code": "extraction_failed",
        "message": "Extraction failed",
        "media_path": "Movie.mkv",
        "stream_index": 3,
        "path": "Movie.mkv",
        "output_path": "Job Work directory",
    }
    assert str(media_root) not in json.dumps(failed)
    jobs.close()


def test_job_rejects_missing_term_map_before_queueing(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    with make_client(media_root, work_root, FakeTranslator()) as client:
        response = client.post(
            "/api/jobs",
            json={
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "zh-Hans",
                "term_map_id": "missing-map",
            },
        )

        assert response.status_code == 400
        assert response.json()["error_code"] == "term_map_not_found"
        assert client.get("/api/jobs").json()["jobs"] == []
        assert list((work_root / "jobs").glob("*.json")) == []


def test_jobs_run_serially_and_forward_immutable_terminology_configuration(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    release = threading.Event()

    translator = RecordingTranslator(release)
    with make_client(media_root, work_root, translator) as client:
        first = client.post(
            "/api/jobs",
            json={
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "zh-Hans",
                "dynamic_terminology_enabled": False,
                "subtitle_terminology_filter_enabled": False,
            },
        ).json()
        assert translator.started.wait(timeout=5)
        second = create_job(client, "ja").json()
        third = create_job(client, "ko").json()

        assert second["status"] == "Queued"
        assert second["queue_position"] == 1
        assert third["status"] == "Queued"
        assert third["queue_position"] == 2
        assert first["request"]["dynamic_terminology_enabled"] is False
        assert first["request"]["subtitle_terminology_filter_enabled"] is False
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/jobs").status_code == 200

        release.set()
        wait_for_status(client, first["id"], "Completed")
        wait_for_status(client, second["id"], "Completed")
        wait_for_status(client, third["id"], "Completed")

    assert [call["target"] for call in translator.calls] == ["zh-Hans", "ja", "ko"]
    assert translator.calls[0]["user_overrides"] == {}
    assert translator.calls[0]["dynamic_terminology_enabled"] is False
    assert translator.calls[0]["subtitle_terminology_filter_enabled"] is False


def test_running_and_queued_jobs_use_term_map_snapshots_after_rename_and_delete(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    release = threading.Event()

    translator = RecordingTranslator(release)
    with make_client(media_root, work_root, translator) as client:
        running_term_map = client.post(
            "/api/term-maps",
            json={"name": "Characters", "content": {"Captain": "队长"}},
        ).json()
        first = client.post(
            "/api/jobs",
            json={
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "zh-Hans",
                "term_map_id": running_term_map["id"],
            },
        ).json()
        wait_for_status(client, first["id"], "Translating")
        queued_term_map = client.post(
            "/api/term-maps",
            json={"name": "Ships", "content": {"Enterprise": "企业号"}},
        ).json()
        queued = client.post(
            "/api/jobs",
            json={
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "ja",
                "term_map_id": queued_term_map["id"],
            },
        ).json()

        assert first["request"]["term_map"] == {
            "id": running_term_map["id"],
            "name": "Characters",
            "content": {"Captain": "队长"},
        }
        assert queued["request"]["term_map"] == {
            "id": queued_term_map["id"],
            "name": "Ships",
            "content": {"Enterprise": "企业号"},
        }
        assert queued["status"] == "Queued"
        assert queued["queue_position"] == 1
        assert (
            client.put(
                f"/api/term-maps/{running_term_map['id']}",
                json={"content": {"Captain": "船长"}},
            ).status_code
            == 200
        )
        assert (
            client.patch(
                f"/api/term-maps/{running_term_map['id']}",
                json={"name": "Renamed characters"},
            ).status_code
            == 200
        )
        assert (
            client.request(
                "DELETE",
                f"/api/term-maps/{running_term_map['id']}",
                json={"name": "Renamed characters"},
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/api/term-maps/{queued_term_map['id']}",
                json={"content": {"Enterprise": "进取号"}},
            ).status_code
            == 200
        )
        assert (
            client.patch(
                f"/api/term-maps/{queued_term_map['id']}",
                json={"name": "Renamed ships"},
            ).status_code
            == 200
        )
        assert (
            client.request(
                "DELETE",
                f"/api/term-maps/{queued_term_map['id']}",
                json={"name": "Renamed ships"},
            ).status_code
            == 200
        )
        assert client.get(f"/api/term-maps/{running_term_map['id']}").status_code == 400
        assert client.get(f"/api/term-maps/{queued_term_map['id']}").status_code == 400
        assert client.get(f"/api/jobs/{first['id']}").json()["request"]["term_map"] == {
            "id": running_term_map["id"],
            "name": "Characters",
            "content": {"Captain": "队长"},
        }
        assert client.get(f"/api/jobs/{queued['id']}").json()["request"][
            "term_map"
        ] == {
            "id": queued_term_map["id"],
            "name": "Ships",
            "content": {"Enterprise": "企业号"},
        }

        release.set()
        wait_for_status(client, first["id"], "Completed")
        completed = wait_for_status(client, queued["id"], "Completed")

        assert completed["request"]["term_map"]["name"] == "Ships"

    assert translator.calls[0]["user_overrides"] == {"Captain": "队长"}
    assert translator.calls[1]["user_overrides"] == {"Enterprise": "企业号"}
    assert not (work_root / "jobs" / first["id"]).exists()
    assert not (work_root / "jobs" / queued["id"]).exists()


def test_failed_term_map_job_retains_immutable_working_copy(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    with make_client(
        media_root, work_root, FakeTranslator(error=RuntimeError("boom"))
    ) as client:
        term_map = client.post(
            "/api/term-maps",
            json={"name": "Characters", "content": {"Captain": "队长"}},
        ).json()
        queued = client.post(
            "/api/jobs",
            json={
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "zh-Hans",
                "term_map_id": term_map["id"],
            },
        ).json()

        failed = wait_for_status(client, queued["id"], "Failed")

        assert failed["request"]["term_map"]["content"] == {"Captain": "队长"}
        assert json.loads(
            (work_root / "jobs" / queued["id"] / "term-map.json").read_text(
                encoding="utf-8"
            )
        ) == {"Captain": "队长"}


def test_failed_job_retains_structured_error_context(tmp_path: Path, monkeypatch):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    client = make_client(media_root, work_root, FakeTranslator())

    def fail_translation(_translation, _request):
        raise ServiceError(
            "output_write_failed",
            "Output cannot be written",
            path=media_root / "out.srt",
            provider_secret="must-not-be-exposed",
        )

    monkeypatch.setattr(
        "cueweaver.application.jobs.Translation.translate", fail_translation
    )

    failed = wait_for_status(client, create_job(client).json()["id"], "Failed")

    assert failed["error"] == {
        "code": "output_write_failed",
        "message": "Output cannot be written",
        "path": "out.srt",
    }


def test_worker_survives_a_persistence_failure_and_processes_next_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    client = make_client(media_root, work_root, FakeTranslator())
    original_write = Jobs._write_record
    failed_once = False

    def fail_translating_write(jobs, job_id, record):
        nonlocal failed_once
        if not failed_once and record["status"] == "Translating":
            failed_once = True
            raise OSError("record unavailable")
        original_write(jobs, job_id, record)

    monkeypatch.setattr(Jobs, "_write_record", fail_translating_write)
    first = create_job(client, "zh").json()
    second = create_job(client, "ja").json()

    failed = wait_for_status(client, first["id"], "Failed")
    completed = wait_for_status(client, second["id"], "Completed")

    assert failed["error"]["code"] == "job_worker_failed"
    assert completed["status"] == "Completed"


def test_cleanup_failure_does_not_claim_job_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    client = make_client(media_root, work_root, FakeTranslator())
    original_rmtree = shutil.rmtree

    def fail_cleanup(path: Path) -> None:
        if path.parent == work_root / "jobs":
            raise OSError("cleanup failed")
        original_rmtree(path)

    monkeypatch.setattr("cueweaver.application.jobs.shutil.rmtree", fail_cleanup)

    queued = create_job(client).json()
    failed = wait_for_status(client, queued["id"], "Failed")

    assert failed["error"] == {
        "code": "work_cleanup_failed",
        "message": "Completed Job work data could not be cleaned up",
    }
    assert (work_root / "jobs" / queued["id"]).is_dir()


@pytest.mark.parametrize(
    ("translator", "body", "expected_code"),
    [
        (
            FakeTranslator(available=False),
            {
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "zh",
            },
            "provider_unavailable",
        ),
        (
            FakeTranslator(),
            {
                "media_path": "../Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "zh",
            },
            "invalid_media_path",
        ),
        (
            FakeTranslator(),
            {
                "media_path": "Movie.mkv",
                "subtitle_path": "Other.srt",
                "target_language_code": "zh",
            },
            "invalid_external_subtitle",
        ),
        (
            FakeTranslator(),
            {
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie\\en.srt",
                "target_language_code": "zh",
            },
            "invalid_external_subtitle",
        ),
        (
            FakeTranslator(),
            {
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "   ",
            },
            "invalid_target_language",
        ),
        (
            FakeTranslator(),
            {
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "zh\x00Hans",
            },
            "invalid_target_language",
        ),
    ],
)
def test_job_validation_rejects_before_queueing(
    tmp_path: Path,
    translator: FakeTranslator,
    body: dict[str, str],
    expected_code: str,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    client = make_client(media_root, work_root, translator)

    response = client.post("/api/jobs", json=body)

    assert response.status_code == 400
    assert response.json()["error_code"] == expected_code
    assert list((work_root / "jobs").glob("*.json")) == []


def test_job_appends_a_number_to_an_existing_suggested_output(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    (media_root / "Movie.zh.srt").write_bytes(b"keep")
    client = make_client(media_root, work_root, FakeTranslator())

    response = create_job(client, "zh")

    assert response.status_code == 200
    queued = response.json()
    completed = wait_for_status(client, queued["id"], "Completed")
    assert completed["request"]["output_path"] == "Movie.zh.2.srt"
    assert (media_root / "Movie.zh.srt").read_bytes() == b"keep"
    assert (media_root / "Movie.zh.2.srt").read_bytes() == SRT


def test_queued_jobs_choose_append_numbers_at_execution_time(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    release = threading.Event()
    with make_client(media_root, work_root, FakeTranslator(delay=release)) as client:
        first = create_job(client, "zh").json()
        second = create_job(client, "zh").json()

        assert first["request"]["output_path"] == "Movie.zh.srt"
        assert second["request"]["output_path"] == "Movie.zh.srt"
        release.set()
        first_completed = wait_for_status(client, first["id"], "Completed")
        second_completed = wait_for_status(client, second["id"], "Completed")

    assert first_completed["request"]["output_path"] == "Movie.zh.srt"
    assert second_completed["request"]["output_path"] == "Movie.zh.2.srt"
    assert (media_root / "Movie.zh.srt").read_bytes() == SRT
    assert (media_root / "Movie.zh.2.srt").read_bytes() == SRT


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "zh..Hans",
        "zh.",
        "zh ",
        "CON",
        "com1",
        "bad/name",
        "bad\\name",
        "bad\x00name",
    ],
)
def test_job_rejects_unsafe_output_suffix(tmp_path: Path, suffix: str):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    client = make_client(media_root, work_root, FakeTranslator())

    response = client.post(
        "/api/jobs",
        json={
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": "zh",
            "output_suffix": suffix,
        },
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_output_suffix"


def test_job_overwrite_replaces_output_after_success(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    output = media_root / "Movie.zh.srt"
    output.write_bytes(b"old")
    client = make_client(media_root, work_root, FakeTranslator())

    queued = client.post(
        "/api/jobs",
        json={
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": "zh",
            "output_conflict_policy": "overwrite",
        },
    ).json()

    completed = wait_for_status(client, queued["id"], "Completed")
    assert completed["request"]["output_path"] == "Movie.zh.srt"
    assert output.read_bytes() == SRT


def test_job_overwrite_preserves_existing_output_when_translation_fails(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    output = media_root / "Movie.zh.srt"
    output.write_bytes(b"old")
    client = make_client(
        media_root, work_root, FakeTranslator(error=RuntimeError("boom"))
    )

    queued = client.post(
        "/api/jobs",
        json={
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": "zh",
            "output_conflict_policy": "overwrite",
        },
    ).json()

    wait_for_status(client, queued["id"], "Failed")
    assert output.read_bytes() == b"old"


def test_job_accepts_external_subtitle_without_language_suffix(tmp_path: Path):
    media_root, work_root, media, subtitle = make_roots(tmp_path)
    subtitle.rename(media.with_name("Movie.srt"))
    client = make_client(media_root, work_root, FakeTranslator())

    response = client.post(
        "/api/jobs",
        json={
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.srt",
            "target_language_code": "zh",
        },
    )

    assert response.status_code == 200
    assert response.json()["request"]["subtitle_path"] == "Movie.srt"


@pytest.mark.parametrize("active_status", ["Queued", "Extracting", "Translating"])
def test_restart_recovers_every_active_job_without_requeueing(
    tmp_path: Path, active_status: str
):
    media_root, work_root, queued, record_path, record = persisted_external_job(
        tmp_path
    )
    record["status"] = active_status
    record["request"]["term_map"] = {
        "id": "map-1",
        "name": "Characters",
        "content": {"Captain": "队长"},
    }
    record["finished_at"] = None
    record_path.write_text(json.dumps(record), encoding="utf-8")
    work_directory = work_root / "jobs" / queued["id"]
    work_directory.mkdir()
    source = work_directory / "source.srt"
    source.write_bytes(SRT)
    request_snapshot = record["request"].copy()
    translator = FakeTranslator(started=threading.Event())

    restarted = Jobs(translator, media_root, work_root)

    recovered = restarted.get(queued["id"])
    assert recovered["status"] == "Interrupted"
    assert recovered["id"] == queued["id"]
    assert recovered["request"] == request_snapshot
    assert recovered["error"] == {
        "code": "job_interrupted",
        "message": "Job was interrupted when CueWeaver stopped",
    }
    assert source.read_bytes() == SRT
    assert not translator.started.is_set()
    assert json.loads(record_path.read_text(encoding="utf-8"))["status"] == (
        "Interrupted"
    )
    restarted.close()


def test_product_startup_recovers_active_job_through_http(tmp_path: Path):
    media_root, work_root, queued, record_path, record = persisted_external_job(
        tmp_path
    )
    record["status"] = "Translating"
    record["finished_at"] = None
    record_path.write_text(json.dumps(record), encoding="utf-8")
    translator = FakeTranslator(started=threading.Event())

    with make_client(media_root, work_root, translator) as client:
        response = client.get(f"/api/jobs/{queued['id']}")

        assert response.status_code == 200
        assert response.json()["status"] == "Interrupted"
        assert response.json()["error"] == {
            "code": "job_interrupted",
            "message": "Job was interrupted when CueWeaver stopped",
        }

    assert not translator.started.is_set()


@pytest.mark.parametrize("terminal_status", ["Completed", "Failed"])
def test_restart_preserves_terminal_job_records(tmp_path: Path, terminal_status: str):
    media_root, work_root, queued, record_path, record = persisted_external_job(
        tmp_path
    )
    record["status"] = terminal_status
    record["finished_at"] = "2026-08-14T00:00:00Z"
    record["error"] = (
        None
        if terminal_status == "Completed"
        else {"code": "translation_failed", "message": "Translation failed"}
    )
    persisted = json.dumps(record, separators=(",", ":"))
    record_path.write_text(persisted, encoding="utf-8")

    restarted = Jobs(FakeTranslator(), media_root, work_root)

    assert restarted.get(queued["id"])["status"] == terminal_status
    assert restarted.get(queued["id"])["finished_at"] == "2026-08-14T00:00:00Z"
    assert record_path.read_text(encoding="utf-8") == persisted
    restarted.close()


def test_restart_recovery_is_idempotent(tmp_path: Path):
    media_root, work_root, queued, record_path, record = persisted_external_job(
        tmp_path
    )
    record["status"] = "Translating"
    record["finished_at"] = None
    record_path.write_text(json.dumps(record), encoding="utf-8")

    first = Jobs(FakeTranslator(), media_root, work_root)
    first_recovery = first.get(queued["id"])
    first.close()
    persisted_after_first_recovery = record_path.read_text(encoding="utf-8")

    second = Jobs(
        FakeTranslator(error=RuntimeError("must not run")), media_root, work_root
    )
    assert second.get(queued["id"]) == first_recovery
    assert record_path.read_text(encoding="utf-8") == persisted_after_first_recovery
    second.close()


@pytest.mark.parametrize("active_status", ["Extracting", "Translating"])
def test_restart_retains_extracted_source_for_an_interrupted_embedded_job(
    tmp_path: Path, active_status: str
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    media_adapter = MediaExtractorFixture([{"index": 3, "codec_name": "subrip"}])
    extraction = Extraction(media_adapter, AtomicOutputPublisher())
    jobs = Jobs(
        FakeTranslator(),
        media_root,
        work_root,
        extraction=extraction,
    )
    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            None,
            "zh-Hans",
            stream_index=3,
            source_format="srt",
        )
    )
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")
    jobs.close()

    record_path = work_root / "jobs" / f"{queued['id']}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["status"] = active_status
    record["finished_at"] = None
    record_path.write_text(json.dumps(record), encoding="utf-8")
    source = work_root / "jobs" / str(queued["id"]) / "source.srt"
    source.parent.mkdir()
    source.write_bytes(SRT)

    restarted = Jobs(FakeTranslator(), media_root, work_root, extraction=extraction)

    recovered = restarted.get(str(queued["id"]))
    assert recovered["status"] == "Interrupted"
    assert recovered["request"] == completed["request"]
    assert recovered["request"]["stream_index"] == 3
    assert "subtitle_path" not in recovered["request"]
    assert source.read_bytes() == SRT
    restarted.close()


def test_restart_ignores_job_with_invalid_conflict_policy(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    jobs_root = work_root / "jobs"
    jobs_root.mkdir(parents=True)
    (jobs_root / "broken.json").write_text(
        json.dumps(
            {
                "id": "broken",
                "status": "Completed",
                "request": {
                    "media_path": "Movie.mkv",
                    "subtitle_path": "Movie.en.srt",
                    "target_language_code": "zh",
                    "output_path": "Movie.zh.srt",
                    "source_format": "srt",
                    "output_conflict_policy": [],
                },
            }
        ),
        encoding="utf-8",
    )

    jobs = Jobs(FakeTranslator(), media_root, work_root)

    assert jobs.list() == []
    jobs.close()


def test_close_returns_while_translation_is_blocked(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    started = threading.Event()
    release = threading.Event()

    class BlockingTranslator(FakeTranslator):
        def translate(
            self, source: Path, target_language: str, **kwargs: object
        ) -> bytes:
            started.set()
            release.wait(timeout=5)
            return super().translate(source, target_language, **kwargs)

    jobs = Jobs(BlockingTranslator(), media_root, work_root)
    jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    assert started.wait(timeout=5)

    closed = threading.Event()
    shutdown = threading.Thread(target=lambda: (jobs.close(), closed.set()))
    shutdown.start()
    assert closed.wait(timeout=0.5)

    release.set()
    shutdown.join(timeout=5)
    jobs._worker.join(timeout=5)


def test_shutdown_after_publish_persists_completed_job(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(), media_root, work_root)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    deadline = time.monotonic() + 5
    while not (media_root / "Movie.zh.srt").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    jobs.close()
    record = json.loads(
        (work_root / "jobs" / f"{queued['id']}.json").read_text(encoding="utf-8")
    )
    assert record["status"] == "Completed"
    assert not (work_root / "jobs" / str(queued["id"])).exists()
