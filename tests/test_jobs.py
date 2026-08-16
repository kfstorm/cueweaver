import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueweaver.adapters.output import AtomicOutputPublisher
from cueweaver.application.errors import ServiceError
from cueweaver.application.extraction import Extraction
from cueweaver.application.jobs import CreateJobRequest, FileJobRecordStore, Jobs
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
        error: Exception | None = None,
    ):
        self.streams = streams
        self.started = started
        self.release = release
        self.error = error
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
        if self.error is not None:
            raise self.error
        output_path.write_bytes(SRT)


class RecordingTranslator(FakeTranslator):
    def __init__(self, delay: threading.Event, started: threading.Event | None = None):
        self.started = started or threading.Event()
        super().__init__(delay=delay, started=self.started)
        self.calls: list[dict[str, object]] = []

    def translate(self, source: Path, target_language: str, **kwargs: object) -> bytes:
        self.calls.append({"target": target_language, **kwargs})
        return super().translate(source, target_language, **kwargs)


class FalsyRecordStore:
    def __init__(self):
        self.records: dict[str, dict[str, object]] = {}
        self.calls: list[str] = []

    def __bool__(self) -> bool:
        return False

    def load(self) -> list[dict[str, object]]:
        self.calls.append("load")
        return list(self.records.values())

    def write(self, job_id: str, record: dict[str, object]) -> None:
        self.calls.append("write")
        self.records[job_id] = json.loads(json.dumps(record))

    def remove(self, job_id: str) -> None:
        self.calls.append("remove")
        del self.records[job_id]


class InterruptedWriteFailureStore(FileJobRecordStore):
    def __init__(self, jobs_root: Path, error: Exception, *, once: bool = False):
        super().__init__(jobs_root)
        self.error = error
        self.once = once
        self.failed = False

    def write(self, job_id: str, record: dict[str, object]) -> None:
        if record.get("status") == "Interrupted" and (not self.once or not self.failed):
            self.failed = True
            raise self.error
        super().write(job_id, record)


def make_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    media_root = tmp_path / "media"
    media_root.mkdir()
    media = media_root / "Movie.mkv"
    subtitle = media_root / "Movie.en.srt"
    media.write_bytes(b"media")
    subtitle.write_bytes(SRT)
    return media_root, tmp_path / "work", media, subtitle


def persisted_job_record(job_id: str, status: str = "Failed") -> dict[str, object]:
    return {
        "id": job_id,
        "status": status,
        "request": {
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": "zh-Hans",
            "output_path": "Movie.zh-Hans.srt",
            "source_format": "srt",
        },
    }


def status_history_entry(
    status: str,
    *,
    attempt: int = 1,
    started_at: str | None = "2026-08-13T12:00:00Z",
    finished_at: str | None = "2026-08-13T12:00:01Z",
) -> dict[str, object]:
    return {
        "status": status,
        "attempt": attempt,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def set_record_status(
    record: dict[str, object], status: str, *, finished_at: str | None
) -> None:
    history = record["status_history"]
    attempt = record["attempt"]
    created_at = record["created_at"]
    assert isinstance(history, list)
    assert isinstance(attempt, int)
    assert isinstance(created_at, str)

    if status in {"Queued", "Extracting", "Translating"}:
        matching_index = next(
            (
                index
                for index, entry in enumerate(history)
                if isinstance(entry, dict) and entry.get("status") == status
            ),
            None,
        )
        if status == "Queued":
            history[:] = history[:1]
        elif matching_index is None:
            history[:] = [
                *history[:1],
                status_history_entry(
                    status,
                    attempt=attempt,
                    started_at=created_at,
                    finished_at=None,
                ),
            ]
        else:
            history[:] = history[: matching_index + 1]
        history[-1].update(
            status=status,
            attempt=attempt,
            started_at=history[-1].get("started_at") or created_at,
            finished_at=finished_at,
        )
        record["started_at"] = None if status == "Queued" else history[-1]["started_at"]
    else:
        history[-1].update(status=status, attempt=attempt, finished_at=finished_at)
    record["status"] = status
    record["finished_at"] = finished_at


def test_record_store_migrates_legacy_records_and_quarantines_unreadable_invalid_and_future_records(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    legacy = {
        "id": "legacy-job",
        "status": "Failed",
        "created_at": "2026-08-13T12:00:00Z",
        "request": {
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": "zh-Hans",
            "output_path": "Movie.zh-Hans.srt",
            "source_format": "srt",
        },
    }
    (jobs_root / "legacy-job.json").write_text(json.dumps(legacy), encoding="utf-8")
    invalid_bytes = b"not-json"
    (jobs_root / "invalid.json").write_bytes(invalid_bytes)
    unreadable_bytes = b"\xff\xfe"
    (jobs_root / "unreadable.json").write_bytes(unreadable_bytes)
    invalid_schema_bytes = b'{"schema_version": "one"}'
    (jobs_root / "invalid-schema.json").write_bytes(invalid_schema_bytes)
    future_bytes = json.dumps(
        {
            **legacy,
            "schema_version": 2,
            "attempt": 1,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "queue_sequence": 0,
            "future": True,
        }
    ).encode()
    (jobs_root / "future.json").write_bytes(future_bytes)

    store = FileJobRecordStore(jobs_root)
    records = store.load()

    assert records == [
        {
            **legacy,
            "schema_version": 1,
            "attempt": 1,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "request": {
                **legacy["request"],
                "term_map": None,
                "dynamic_terminology_enabled": True,
                "subtitle_terminology_filter_enabled": True,
                "output_suffix": "zh-Hans",
                "output_conflict_policy": "append-number",
            },
            "queue_sequence": 0,
        }
    ]
    assert "status_history" not in records[0]
    assert (
        json.loads((jobs_root / "legacy-job.json").read_text())["schema_version"] == 1
    )
    assert not (jobs_root / "invalid.json").exists()
    assert not (jobs_root / "unreadable.json").exists()
    assert not (jobs_root / "invalid-schema.json").exists()
    assert not (jobs_root / "future.json").exists()
    assert (jobs_root / "corrupt" / "invalid.json").read_bytes() == invalid_bytes
    assert (jobs_root / "corrupt" / "unreadable.json").read_bytes() == unreadable_bytes
    assert (
        jobs_root / "corrupt" / "invalid-schema.json"
    ).read_bytes() == invalid_schema_bytes
    assert (jobs_root / "unsupported" / "future.json").read_bytes() == future_bytes
    assert store.health().corrupt_count == 3
    assert store.health().unsupported_count == 1


def test_record_store_migrates_a_legacy_record_with_a_noncanonical_filename(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    legacy = persisted_job_record("actual-id")
    (jobs_root / "legacy-name.json").write_text(json.dumps(legacy), encoding="utf-8")

    records = FileJobRecordStore(jobs_root).load()

    assert [record["id"] for record in records] == ["actual-id"]
    assert (jobs_root / "actual-id.json").is_file()
    assert not (jobs_root / "legacy-name.json").exists()


def test_record_store_keeps_last_value_for_unrelated_duplicate_job_fields(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    record = persisted_job_record("duplicate-status")
    raw_record = json.dumps(record).replace(
        '"status": "Failed"', '"status": "Failed", "status": "Completed"'
    )
    (jobs_root / "duplicate-status.json").write_text(raw_record, encoding="utf-8")

    records = FileJobRecordStore(jobs_root).load()

    assert records[0]["status"] == "Completed"
    assert not (jobs_root / "corrupt" / "duplicate-status.json").exists()


def test_record_store_quarantines_duplicate_term_map_content_keys(tmp_path: Path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    record = persisted_job_record("duplicate-term-map")
    request = record["request"]
    assert isinstance(request, dict)
    request["term_map"] = {
        "id": "map-1",
        "name": "Terms",
        "content": {"Source": "one"},
    }
    raw_record = json.dumps(record).replace(
        '"Source": "one"', '"Source": "one", "Source": "two"'
    )
    record_path = jobs_root / "duplicate-term-map.json"
    record_path.write_text(raw_record, encoding="utf-8")

    assert FileJobRecordStore(jobs_root).load() == []
    assert (jobs_root / "corrupt" / record_path.name).read_text() == raw_record


def test_record_store_quarantines_a_symlink_as_regular_record_bytes(tmp_path: Path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    outside = tmp_path / "outside.json"
    outside_bytes = b"not-json"
    outside.write_bytes(outside_bytes)
    (jobs_root / "linked.json").symlink_to(outside)

    FileJobRecordStore(jobs_root).load()

    quarantine = jobs_root / "corrupt" / "linked.json"
    assert quarantine.read_bytes() == outside_bytes
    assert not quarantine.is_symlink()
    assert not (jobs_root / "linked.json").exists()
    assert FileJobRecordStore(jobs_root).health().corrupt_count == 1


def test_record_store_quarantines_a_legacy_record_with_an_unsafe_id(tmp_path: Path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    (jobs_root / "unsafe.json").write_text(
        json.dumps(
            {
                "id": "../outside",
                "status": "Failed",
                "request": {
                    "media_path": "Movie.mkv",
                    "subtitle_path": "Movie.en.srt",
                    "target_language_code": "zh-Hans",
                    "output_path": "Movie.zh-Hans.srt",
                    "source_format": "srt",
                },
            }
        ),
        encoding="utf-8",
    )

    FileJobRecordStore(jobs_root).load()

    assert not (tmp_path / "outside.json").exists()
    assert (jobs_root / "corrupt" / "unsafe.json").is_file()


def test_record_store_keeps_the_canonical_record_when_a_duplicate_legacy_file_exists(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    canonical = {
        "schema_version": 1,
        "id": "actual-id",
        "status": "Completed",
        "attempt": 1,
        "created_at": "2026-08-13T12:00:00Z",
        "started_at": "2026-08-13T12:00:01Z",
        "finished_at": "2026-08-13T12:00:02Z",
        "request": {
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": "zh-Hans",
            "output_path": "Movie.zh-Hans.srt",
            "source_format": "srt",
        },
        "error": None,
        "queue_sequence": 0,
    }
    legacy = {key: value for key, value in canonical.items() if key != "schema_version"}
    (jobs_root / "actual-id.json").write_text(json.dumps(canonical), encoding="utf-8")
    legacy_path = jobs_root / "legacy-name.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")

    records = FileJobRecordStore(jobs_root).load()

    assert [record["status"] for record in records] == ["Completed"]
    assert not legacy_path.exists()
    assert (jobs_root / "corrupt" / "legacy-name.json").read_text() == json.dumps(
        legacy
    )


def test_record_store_protects_a_future_canonical_record_from_a_legacy_alias(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    legacy = persisted_job_record("future-job")
    future = {
        **legacy,
        "schema_version": 2,
        "attempt": 1,
        "created_at": "2026-08-13T12:00:00Z",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "queue_sequence": 0,
    }
    (jobs_root / "a-legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    (jobs_root / "future-job.json").write_text(json.dumps(future), encoding="utf-8")

    records = FileJobRecordStore(jobs_root).load()

    assert records == []
    assert (jobs_root / "corrupt" / "a-legacy.json").is_file()
    assert (jobs_root / "unsupported" / "future-job.json").is_file()


def test_record_store_quarantines_an_invalid_future_shape_as_corrupt(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    raw_future = b'{"schema_version":2,"id":"future-job","status":"CancelledV2","request":{"new_shape":true}}'
    (jobs_root / "future-job.json").write_bytes(raw_future)

    records = FileJobRecordStore(jobs_root).load()

    assert records == []
    assert (jobs_root / "corrupt" / "future-job.json").read_bytes() == raw_future
    assert not (jobs_root / "unsupported" / "future-job.json").exists()


@pytest.mark.parametrize(
    "missing_field",
    ["created_at", "started_at", "finished_at", "error", "queue_sequence"],
)
def test_record_store_quarantines_v1_records_missing_required_fields(
    tmp_path: Path, missing_field: str
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    record = {
        **persisted_job_record("missing-field"),
        "schema_version": 1,
        "attempt": 1,
        "created_at": "2026-08-13T12:00:00Z",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "queue_sequence": 1,
    }
    del record[missing_field]
    raw_record = json.dumps(record).encode()
    (jobs_root / "missing-field.json").write_bytes(raw_record)

    assert FileJobRecordStore(jobs_root).load() == []
    assert (jobs_root / "corrupt" / "missing-field.json").read_bytes() == raw_record


def test_record_store_protects_a_mismatched_future_path_from_a_legacy_alias(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    legacy = persisted_job_record("actual-id")
    future = {
        **legacy,
        "id": "other-id",
        "schema_version": 2,
        "attempt": 1,
        "created_at": "2026-08-13T12:00:00Z",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "queue_sequence": 0,
    }
    (jobs_root / "a-alias.json").write_text(json.dumps(legacy), encoding="utf-8")
    (jobs_root / "actual-id.json").write_text(json.dumps(future), encoding="utf-8")

    records = FileJobRecordStore(jobs_root).load()

    assert [record["id"] for record in records] == ["actual-id"]
    assert records[0]["schema_version"] == 1
    assert (jobs_root / "actual-id.json").is_file()
    assert not (jobs_root / "corrupt" / "a-alias.json").exists()
    assert (jobs_root / "unsupported" / "actual-id.json").is_file()


@pytest.mark.parametrize("alias_name", ["a-alias.json", "z-alias.json"])
def test_record_store_preserves_a_different_job_in_a_canonical_path_conflict(
    tmp_path: Path, alias_name: str
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    alias_record = persisted_job_record("actual-id")
    other_record = {
        **alias_record,
        "id": "other-id",
        "status": "Completed",
    }
    (jobs_root / alias_name).write_text(json.dumps(alias_record), encoding="utf-8")
    (jobs_root / "actual-id.json").write_text(
        json.dumps(other_record), encoding="utf-8"
    )

    records = FileJobRecordStore(jobs_root).load()

    assert [record["id"] for record in records] == ["other-id"]
    assert records[0]["status"] == "Completed"
    assert (jobs_root / "other-id.json").is_file()
    assert (jobs_root / "corrupt" / alias_name).is_file()


def test_record_store_migrates_an_alias_once_when_the_canonical_record_is_corrupt(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    legacy = persisted_job_record("z-job")
    (jobs_root / "a-legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    (jobs_root / "z-job.json").write_bytes(b"broken-canonical")

    records = FileJobRecordStore(jobs_root).load()

    assert len(records) == 1
    assert records[0]["id"] == "z-job"
    assert json.loads((jobs_root / "z-job.json").read_text())["schema_version"] == 1
    assert (jobs_root / "corrupt" / "z-job.json").read_bytes() == b"broken-canonical"


def test_record_store_quarantines_a_broken_canonical_symlink_before_migration(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    legacy = persisted_job_record("z-job")
    (jobs_root / "a-legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    missing = tmp_path / "missing.json"
    (jobs_root / "z-job.json").symlink_to(missing)

    records = FileJobRecordStore(jobs_root).load()

    assert len(records) == 1
    assert records[0]["id"] == "z-job"
    assert (jobs_root / "z-job.json").is_file()
    assert (jobs_root / "corrupt" / "z-job.json").is_symlink()
    assert FileJobRecordStore(jobs_root).health().corrupt_count == 1


def test_record_store_does_not_overwrite_a_dangling_quarantine_collision(
    tmp_path: Path,
):
    jobs_root = tmp_path / "jobs"
    quarantine = jobs_root / "corrupt"
    quarantine.mkdir(parents=True)
    dangling_target = tmp_path / "missing.json"
    existing = quarantine / "broken.json"
    existing.symlink_to(dangling_target)
    source = jobs_root / "broken.json"
    source.write_bytes(b"broken")

    FileJobRecordStore(jobs_root).load()

    assert existing.is_symlink()
    assert (quarantine / "broken.2.json").read_bytes() == b"broken"
    assert FileJobRecordStore(jobs_root).health().corrupt_count == 2


def test_record_store_rejects_path_traversal_job_ids(tmp_path: Path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"keep")
    store = FileJobRecordStore(jobs_root)

    with pytest.raises(ServiceError, match="Job ID is invalid"):
        store.write("../outside", {})
    with pytest.raises(ServiceError, match="Job ID is invalid"):
        store.remove("../outside")

    assert outside.read_bytes() == b"keep"


def test_new_job_records_include_schema_version_one(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(), media_root, work_root)

    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh-Hans"))

    assert queued["schema_version"] == 1
    assert (
        json.loads(
            (work_root / "jobs" / f"{queued['id']}.json").read_text(encoding="utf-8")
        )["schema_version"]
        == 1
    )
    jobs.close()


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


def create_term_map_job(client: TestClient):
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
    return term_map, queued


def create_failed_external_job(tmp_path: Path, translator: FakeTranslator):
    media_root, work_root, media, subtitle = make_roots(tmp_path)
    jobs = Jobs(translator, media_root, work_root)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh-Hans"))
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    return media_root, work_root, media, subtitle, jobs, queued


def create_failed_embedded_job(tmp_path: Path, translator: FakeTranslator):
    media_root, work_root, media, _subtitle = make_roots(tmp_path)
    media_adapter = MediaExtractorFixture([{"index": 3, "codec_name": "subrip"}])
    extraction = Extraction(media_adapter, AtomicOutputPublisher())
    jobs = Jobs(translator, media_root, work_root, extraction=extraction)
    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            None,
            "zh-Hans",
            stream_index=3,
            source_format="srt",
        )
    )
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    return media_root, work_root, media, media_adapter, jobs, queued


def persisted_failed_embedded_job(tmp_path: Path, translator: FakeTranslator):
    _media_root, work_root, media, media_adapter, jobs, queued = (
        create_failed_embedded_job(tmp_path, translator)
    )
    jobs.close()
    record_path = work_root / "jobs" / f"{queued['id']}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    return _media_root, work_root, media, media_adapter, queued, record_path, record


def failed_embedded_job_for_fallback(tmp_path: Path, translator: FakeTranslator):
    _media_root, work_root, media, media_adapter, jobs, queued = (
        create_failed_embedded_job(tmp_path, translator)
    )
    (work_root / "jobs" / str(queued["id"]) / "source.srt").write_bytes(b"tampered")
    translator.error = None
    return work_root, media, media_adapter, jobs, queued


def failed_embedded_work_directory(tmp_path: Path, translator: FakeTranslator):
    _media_root, work_root, media, media_adapter, jobs, queued = (
        create_failed_embedded_job(tmp_path, translator)
    )
    return work_root, media, media_adapter, jobs, queued


def assert_failed_record_persisted(jobs: Jobs, work_root: Path, job_id: str) -> None:
    assert jobs.get(job_id)["status"] == "Failed"
    persisted = json.loads(
        (work_root / "jobs" / f"{job_id}.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "Failed"


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


def finish_shutdown(
    jobs: Jobs, job_id: str, release: threading.Event
) -> dict[str, object]:
    jobs.close()
    release.set()
    jobs._worker.join(timeout=5)
    return jobs.get(job_id)


def create_queued_job(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(), media_root, work_root)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    return media_root, work_root, jobs, queued


def create_blocked_jobs(tmp_path: Path):
    media_root, work_root, _media, subtitle = make_roots(tmp_path)
    release = threading.Event()
    started = threading.Event()
    translator = FakeTranslator(started=started, release=release)
    jobs = Jobs(translator, media_root, work_root)
    running = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    assert started.wait(timeout=5)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "ja"))
    return jobs, translator, running, queued, release, subtitle


def test_jobs_uses_a_falsy_injected_record_store_for_all_record_operations(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    store = FalsyRecordStore()
    jobs = Jobs(FakeTranslator(), media_root, work_root, record_store=store)

    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh-Hans"))
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")
    jobs.delete(str(queued["id"]))

    assert store.calls[0] == "load"
    assert "write" in store.calls
    assert store.calls[-1] == "remove"
    assert not (work_root / "jobs" / f"{queued['id']}.json").exists()
    jobs.close()


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


def test_job_persists_status_history_and_exposes_it_on_detail(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    release = threading.Event()
    started = threading.Event()
    translator = FakeTranslator(started=started, release=release)
    with make_client(media_root, work_root, translator) as client:
        queued = create_job(client).json()
        assert queued["status_history"]
        assert queued["status_history"][-1] == {
            "status": "Queued",
            "attempt": 1,
            "started_at": queued["created_at"],
            "finished_at": None,
        }

        assert started.wait(timeout=5)
        translating = client.get(f"/api/jobs/{queued['id']}").json()
        assert [entry["status"] for entry in translating["status_history"]] == [
            "Queued",
            "Translating",
        ]
        assert translating["status_history"][0]["finished_at"]
        assert translating["status_history"][1]["finished_at"] is None

        release.set()
        completed = wait_for_status(client, queued["id"], "Completed")

    assert [entry["status"] for entry in completed["status_history"]] == [
        "Queued",
        "Translating",
        "Completed",
    ]
    assert all(
        set(entry) == {"status", "attempt", "started_at", "finished_at"}
        for entry in completed["status_history"]
    )
    assert completed["status_history"][-1]["started_at"]
    assert completed["status_history"][-1]["finished_at"]
    persisted = json.loads(
        (work_root / "jobs" / f"{queued['id']}.json").read_text(encoding="utf-8")
    )
    assert persisted["status_history"] == completed["status_history"]


def test_retry_adds_a_new_attempt_to_status_history(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    release = threading.Event()
    release.set()
    translator = FakeTranslator(error=RuntimeError("boom"))
    jobs = Jobs(translator, media_root, work_root)

    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh-Hans"))
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    failed = jobs.get(str(queued["id"]))
    failed_finished_at = failed["status_history"][2]["finished_at"]
    translator.error = None
    retried = jobs.retry(str(queued["id"]))
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    retried_history = retried["status_history"]
    assert retried_history[-1]["status"] == "Queued"
    assert retried_history[-1]["attempt"] == 2
    assert isinstance(retried_history[-1]["started_at"], str)
    assert retried_history[-1]["finished_at"] is None
    assert [
        (entry["status"], entry["attempt"]) for entry in completed["status_history"]
    ] == [
        ("Queued", 1),
        ("Translating", 1),
        ("Failed", 1),
        ("Queued", 2),
        ("Translating", 2),
        ("Completed", 2),
    ]
    assert completed["status_history"][2]["finished_at"] == failed_finished_at
    assert all(entry["finished_at"] for entry in completed["status_history"])
    jobs.close()


def test_cancel_adds_a_finished_cancelled_status_history_entry(tmp_path: Path):
    jobs, _translator, _running, queued, _release, _subtitle = create_blocked_jobs(
        tmp_path
    )

    cancelled = jobs.cancel(str(queued["id"]))

    assert [
        (entry["status"], entry["attempt"]) for entry in cancelled["status_history"]
    ] == [
        ("Queued", 1),
        ("Cancelled", 1),
    ]
    assert all(entry["finished_at"] for entry in cancelled["status_history"])
    jobs.close()


def test_restart_appends_interrupted_without_fabricating_earlier_history(
    tmp_path: Path,
):
    media_root, work_root, queued, record_path, record = persisted_external_job(
        tmp_path
    )
    record["status"] = "Translating"
    record["finished_at"] = None
    record["error"] = None
    record["status_history"] = record["status_history"][:-1]
    record["status_history"][-1]["finished_at"] = None
    record_path.write_text(json.dumps(record), encoding="utf-8")

    restarted = Jobs(FakeTranslator(), media_root, work_root)

    recovered = restarted.get(queued["id"])
    assert [entry["status"] for entry in recovered["status_history"]] == [
        "Queued",
        "Translating",
        "Interrupted",
    ]
    assert recovered["status_history"][-1]["attempt"] == 1
    assert recovered["status_history"][-1]["started_at"]
    assert recovered["status_history"][-1]["finished_at"]
    restarted.close()


@pytest.mark.parametrize(
    "status_history",
    [
        None,
        {},
        [{"status": "Queued"}],
        [
            {
                "status": "Queued",
                "attempt": True,
                "started_at": None,
                "finished_at": None,
            }
        ],
    ],
)
def test_record_store_quarantines_an_invalid_optional_status_history(
    tmp_path: Path, status_history: object
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    record = {
        **persisted_job_record("invalid-history"),
        "schema_version": 1,
        "attempt": 1,
        "created_at": "2026-08-13T12:00:00Z",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "queue_sequence": 1,
        "status_history": status_history,
    }
    raw_record = json.dumps(record).encode()
    (jobs_root / "invalid-history.json").write_bytes(raw_record)

    assert FileJobRecordStore(jobs_root).load() == []
    assert (jobs_root / "corrupt" / "invalid-history.json").read_bytes() == raw_record


@pytest.mark.parametrize(
    "status, attempt, status_history",
    [
        ("Failed", 1, [status_history_entry("Completed")]),
        ("Failed", 2, [status_history_entry("Failed")]),
        (
            "Failed",
            1,
            [status_history_entry("Failed", started_at=None)],
        ),
        (
            "Failed",
            1,
            [status_history_entry("Failed", finished_at=None)],
        ),
        (
            "Translating",
            1,
            [status_history_entry("Translating")],
        ),
        (
            "Completed",
            1,
            [
                status_history_entry("Queued", finished_at=None),
                status_history_entry("Completed"),
            ],
        ),
    ],
)
def test_record_store_quarantines_inconsistent_status_history(
    tmp_path: Path,
    status: str,
    attempt: int,
    status_history: list[dict[str, object]],
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    record = {
        **persisted_job_record("inconsistent-history", status),
        "schema_version": 1,
        "attempt": attempt,
        "created_at": "2026-08-13T12:00:00Z",
        "started_at": None if status == "Queued" else "2026-08-13T12:00:00Z",
        "finished_at": (
            None
            if status in {"Queued", "Extracting", "Translating"}
            else "2026-08-13T12:00:01Z"
        ),
        "error": None,
        "queue_sequence": 1,
        "status_history": status_history,
    }
    raw_record = json.dumps(record).encode()
    (jobs_root / "inconsistent-history.json").write_bytes(raw_record)

    assert FileJobRecordStore(jobs_root).load() == []
    assert (
        jobs_root / "corrupt" / "inconsistent-history.json"
    ).read_bytes() == raw_record


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


def test_failed_external_job_retries_in_place_and_reuses_work_directory(
    tmp_path: Path,
):
    media_root, work_root, _media, subtitle = make_roots(tmp_path)
    release = threading.Event()
    release.set()
    translator = RecordingTranslator(release)
    translator.error = RuntimeError("boom")
    jobs = Jobs(translator, media_root, work_root)

    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh-Hans"))
    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    work_directory = work_root / "jobs" / str(queued["id"])
    checkpoint_marker = work_directory / "checkpoint-marker"
    checkpoint_marker.write_text("keep", encoding="utf-8")
    original_request = json.loads(json.dumps(failed["request"]))

    translator.error = None
    retried = jobs.retry(str(queued["id"]))

    assert retried["id"] == queued["id"]
    assert retried["attempt"] == 2
    assert retried["status"] == "Queued"
    assert retried["request"] == original_request
    assert checkpoint_marker.is_file()

    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    assert completed["id"] == queued["id"]
    assert completed["attempt"] == 2
    assert translator.sources == [subtitle, subtitle]
    assert (
        translator.calls[0]["work_directory"] == translator.calls[1]["work_directory"]
    )
    assert not work_directory.exists()
    jobs.close()


def test_failed_embedded_job_retries_in_place_and_reuses_extraction(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    work_root, media, media_adapter, jobs, queued = failed_embedded_work_directory(
        tmp_path, translator
    )
    work_directory = work_root / "jobs" / str(queued["id"])
    extracted = work_directory / "source.srt"
    assert extracted.is_file()

    translator.error = None
    retried = jobs.retry(str(queued["id"]))
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    assert retried["id"] == queued["id"]
    assert retried["attempt"] == 2
    assert completed["attempt"] == 2
    assert media_adapter.probe_calls == [media]
    assert len(media_adapter.extract_calls) == 1
    assert translator.sources == [extracted, extracted]
    assert not work_directory.exists()
    jobs.close()


def test_embedded_retry_reextracts_when_the_intermediate_is_tampered(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    work_root, media, media_adapter, jobs, queued = failed_embedded_work_directory(
        tmp_path, translator
    )
    work_directory = work_root / "jobs" / str(queued["id"])
    (work_directory / "source.srt").write_bytes(b"tampered")

    translator.error = None
    jobs.retry(str(queued["id"]))
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    assert completed["status"] == "Completed"
    assert media_adapter.probe_calls == [media, media]
    assert len(media_adapter.extract_calls) == 2
    assert not work_directory.exists()
    jobs.close()


def test_embedded_retry_reextracts_when_the_intermediate_is_a_symlink(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    _media_root, work_root, media, media_adapter, jobs, queued = (
        create_failed_embedded_job(tmp_path, translator)
    )
    source = work_root / "jobs" / str(queued["id"]) / "source.srt"
    outside = tmp_path / "outside.srt"
    outside.write_bytes(SRT)
    source.unlink()
    source.symlink_to(outside)

    translator.error = None
    jobs.retry(str(queued["id"]))
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    assert media_adapter.probe_calls == [media, media]
    assert len(media_adapter.extract_calls) == 2
    assert outside.read_bytes() == SRT
    jobs.close()


def test_embedded_retry_keeps_intermediate_and_marker_when_reprobe_fails(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    work_root, media, media_adapter, jobs, queued = failed_embedded_job_for_fallback(
        tmp_path, translator
    )
    work_directory = work_root / "jobs" / str(queued["id"])
    source = work_directory / "source.srt"
    record_path = work_root / "jobs" / f"{queued['id']}.json"
    original_marker = json.loads(record_path.read_text(encoding="utf-8"))["extraction"]
    media_adapter.streams = []

    jobs.retry(str(queued["id"]))
    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")

    assert failed["error"]["code"] == "stream_not_found"
    assert source.read_bytes() == b"tampered"
    persisted = json.loads(record_path.read_text(encoding="utf-8"))
    assert persisted["extraction"] == original_marker
    assert media_adapter.probe_calls == [media, media]
    assert len(media_adapter.extract_calls) == 1
    jobs.close()


def test_embedded_retry_quarantines_a_directory_intermediate_after_reextraction(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    work_root, _media, _media_adapter, jobs, queued = failed_embedded_work_directory(
        tmp_path, translator
    )
    work_directory = work_root / "jobs" / str(queued["id"])
    source = work_directory / "source.srt"
    source.unlink()
    source.mkdir()

    jobs.retry(str(queued["id"]))
    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")

    assert failed["error"]["code"] == "translation_failed"
    assert source.read_bytes() == SRT
    assert len(list(work_directory.glob("source.srt.invalid.*"))) == 1
    jobs.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [("path", "source.ass"), ("format", "ass"), ("content_digest", "0" * 64)],
)
def test_embedded_retry_reextracts_when_completion_marker_is_tampered(
    tmp_path: Path, field: str, value: str
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    media_root, work_root, media, media_adapter, queued, record_path, record = (
        persisted_failed_embedded_job(tmp_path, translator)
    )
    record["extraction"][field] = value
    record_path.write_text(json.dumps(record), encoding="utf-8")
    translator.error = None

    restarted = Jobs(
        translator,
        media_root,
        work_root,
        extraction=Extraction(media_adapter, AtomicOutputPublisher()),
    )
    restarted.retry(str(queued["id"]))
    wait_for_status_from_jobs(restarted, str(queued["id"]), "Completed")

    assert media_adapter.probe_calls == [media, media]
    assert len(media_adapter.extract_calls) == 2
    restarted.close()


def test_embedded_retry_keeps_terminal_context_when_the_stream_disappears(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    work_root, media, media_adapter, jobs, queued = failed_embedded_job_for_fallback(
        tmp_path, translator
    )
    media_adapter.streams = []

    jobs.retry(str(queued["id"]))
    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")

    assert failed["error"] == {
        "code": "stream_not_found",
        "message": "Embedded subtitle stream was not found",
        "media_path": "Movie.mkv",
        "stream_index": 3,
    }
    assert failed["attempt"] == 2
    assert media_adapter.probe_calls == [media, media]
    assert len(media_adapter.extract_calls) == 1
    assert (work_root / "jobs" / str(queued["id"])).is_dir()
    jobs.close()


def test_embedded_retry_rejects_missing_media_and_keeps_the_job_terminal(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    _media_root, _work_root, media, _media_adapter, jobs, queued = (
        create_failed_embedded_job(tmp_path, translator)
    )
    media.unlink()

    with pytest.raises(ServiceError) as raised:
        jobs.retry(str(queued["id"]))

    assert raised.value.error_code == "media_not_found"
    current = jobs.get(str(queued["id"]))
    assert current["status"] == "Failed"
    assert current["attempt"] == 1
    assert current["error"] == {
        "code": "media_not_found",
        "message": "Media does not exist",
        "path": "Movie.mkv",
    }
    jobs.close()


def test_embedded_retry_reports_a_changed_codec_after_fallback_extraction(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    _work_root, media, media_adapter, jobs, queued = failed_embedded_job_for_fallback(
        tmp_path, translator
    )
    media_adapter.streams = [{"index": 3, "codec_name": "ass"}]

    jobs.retry(str(queued["id"]))
    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")

    assert failed["error"] == {
        "code": "format_mismatch",
        "message": "Output format must match the Embedded subtitle stream format",
        "media_path": "Movie.mkv",
        "stream_index": 3,
    }
    assert media_adapter.probe_calls == [media, media]
    assert len(media_adapter.extract_calls) == 1
    jobs.close()


def test_interrupted_embedded_job_retries_using_its_verified_extraction(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    media_root, work_root, media, media_adapter, queued, record_path, record = (
        persisted_failed_embedded_job(tmp_path, translator)
    )
    set_record_status(record, "Interrupted", finished_at=record["finished_at"])
    record_path.write_text(json.dumps(record), encoding="utf-8")
    translator.error = None

    restarted = Jobs(
        translator,
        media_root,
        work_root,
        extraction=Extraction(media_adapter, AtomicOutputPublisher()),
    )
    assert restarted.get(str(queued["id"]))["status"] == "Interrupted"

    restarted.retry(str(queued["id"]))
    completed = wait_for_status_from_jobs(restarted, str(queued["id"]), "Completed")

    assert completed["attempt"] == 2
    assert media_adapter.probe_calls == [media]
    assert len(media_adapter.extract_calls) == 1
    restarted.close()


def test_embedded_retry_rejects_a_job_work_directory_symlink(
    tmp_path: Path,
):
    translator = FakeTranslator(error=RuntimeError("boom"))
    media_root, work_root, media, media_adapter, jobs, queued = (
        create_failed_embedded_job(tmp_path, translator)
    )
    jobs.close()
    work_directory = work_root / "jobs" / str(queued["id"])
    backup = work_root / "backup-job-work"
    work_directory.rename(backup)
    work_directory.symlink_to(media_root, target_is_directory=True)
    original_media = media.read_bytes()
    restarted = Jobs(
        translator,
        media_root,
        work_root,
        extraction=Extraction(media_adapter, AtomicOutputPublisher()),
    )

    with pytest.raises(ServiceError) as raised:
        restarted.retry(str(queued["id"]))

    assert raised.value.error_code == "invalid_work_directory"
    assert media.read_bytes() == original_media
    restarted.close()
    work_directory.unlink()
    shutil.rmtree(backup)


def test_embedded_extraction_failure_can_be_retried_successfully(
    tmp_path: Path,
):
    media_root, work_root, media, _subtitle = make_roots(tmp_path)
    media_adapter = MediaExtractorFixture(
        [{"index": 3, "codec_name": "subrip"}], error=RuntimeError("ffmpeg")
    )
    extraction = Extraction(media_adapter, AtomicOutputPublisher())
    translator = FakeTranslator()
    jobs = Jobs(translator, media_root, work_root, extraction=extraction)
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
    assert failed["error"]["code"] == "extraction_failed"
    media_adapter.error = None

    retried = jobs.retry(str(queued["id"]))
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    assert retried["attempt"] == 2
    assert completed["attempt"] == 2
    assert media_adapter.probe_calls == [media, media]
    assert len(media_adapter.extract_calls) == 2
    assert (media_root / "Movie.zh-Hans.srt").read_bytes() == SRT
    assert not (work_root / "jobs" / str(queued["id"])).exists()
    jobs.close()


@pytest.mark.parametrize("output_conflict_policy", ["append-number", "overwrite"])
def test_embedded_retry_reuses_checkpoint_and_output_policy(
    tmp_path: Path, output_conflict_policy: str
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    output = media_root / "Movie.zh-Hans.srt"
    output.write_bytes(b"existing")
    release = threading.Event()
    release.set()
    translator = RecordingTranslator(release)
    translator.error = RuntimeError("boom")
    media_adapter = MediaExtractorFixture([{"index": 3, "codec_name": "subrip"}])
    jobs = Jobs(
        translator,
        media_root,
        work_root,
        extraction=Extraction(media_adapter, AtomicOutputPublisher()),
    )

    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            None,
            "zh-Hans",
            output_conflict_policy=output_conflict_policy,
            stream_index=3,
            source_format="srt",
        )
    )
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    work_directory = work_root / "jobs" / str(queued["id"])
    (work_directory / "checkpoint-marker").write_text("keep", encoding="utf-8")
    translator.error = None
    release.clear()
    translator.started.clear()

    jobs.retry(str(queued["id"]))
    checkpoint_marker = work_directory / "checkpoint-marker"
    assert checkpoint_marker.read_text(encoding="utf-8") == "keep"
    assert translator.started.wait(timeout=5)
    release.set()
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    expected_output = (
        "Movie.zh-Hans.srt"
        if output_conflict_policy == "overwrite"
        else "Movie.zh-Hans.2.srt"
    )
    assert completed["request"]["output_conflict_policy"] == output_conflict_policy
    assert completed["request"]["output_path"] == expected_output
    assert output.read_bytes() == (
        SRT if output_conflict_policy == "overwrite" else b"existing"
    )
    assert (
        translator.calls[0]["work_directory"] == translator.calls[1]["work_directory"]
    )
    assert not work_directory.exists()
    jobs.close()


def test_retry_source_validation_keeps_failed_job_terminal_and_updates_error(
    tmp_path: Path,
):
    media_root, work_root, _media, subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(error=RuntimeError("boom")), media_root, work_root)

    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh-Hans"))
    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    subtitle.unlink()

    with pytest.raises(ServiceError) as raised:
        jobs.retry(str(queued["id"]))

    assert raised.value.error_code == "invalid_external_subtitle"
    current = jobs.get(str(queued["id"]))
    assert current["status"] == "Failed"
    assert current["attempt"] == failed["attempt"] == 1
    assert current["error"] == {
        "code": "invalid_external_subtitle",
        "message": "External subtitle does not exist",
        "path": "Movie.en.srt",
    }
    assert (work_root / "jobs" / str(queued["id"])).is_dir()
    jobs.close()


def test_retry_revalidates_media_and_external_subtitle_paths(tmp_path: Path):
    media_root, work_root, media, subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(error=RuntimeError("boom")), media_root, work_root)

    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh-Hans"))
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    media.unlink()

    with pytest.raises(ServiceError) as raised:
        jobs.retry(str(queued["id"]))

    assert raised.value.error_code == "media_not_found"
    assert raised.value.context == {"path": "Movie.mkv"}
    assert jobs.get(str(queued["id"]))["status"] == "Failed"
    assert (work_root / "jobs" / str(queued["id"])).is_dir()
    media.write_bytes(b"media")
    subtitle.unlink()
    with pytest.raises(ServiceError) as raised:
        jobs.retry(str(queued["id"]))
    assert raised.value.error_code == "invalid_external_subtitle"
    jobs.close()


def test_retry_uses_the_retained_term_map_snapshot(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    release = threading.Event()
    release.set()
    translator = RecordingTranslator(release)
    translator.error = RuntimeError("boom")

    with make_client(media_root, work_root, translator) as client:
        term_map, queued = create_term_map_job(client)
        wait_for_status(client, queued["id"], "Failed")
        client.put(
            f"/api/term-maps/{term_map['id']}",
            json={"content": {"Captain": "船长"}},
        )
        translator.error = None

        client.post(f"/api/jobs/{queued['id']}/retry")
        wait_for_status(client, queued["id"], "Completed")

    assert translator.calls[0]["user_overrides"] == {"Captain": "队长"}
    assert translator.calls[1]["user_overrides"] == {"Captain": "队长"}


def test_completed_job_rejects_retry_with_a_structured_conflict(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(), media_root, work_root)

    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh-Hans"))
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    with pytest.raises(ServiceError) as raised:
        jobs.retry(str(queued["id"]))

    assert raised.value.error_code == "job_retry_conflict"
    jobs.close()


def test_cancel_queued_job_persists_history_and_allows_terminal_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    jobs, translator, running, queued, release, subtitle = create_blocked_jobs(tmp_path)
    work_root = tmp_path / "work"
    worker_errors: list[tuple[str, Exception]] = []

    def record_worker_error(instance: Jobs, job_id: str, error: Exception) -> None:
        worker_errors.append((job_id, error))

    monkeypatch.setattr(Jobs, "_mark_failed_after_worker_error", record_worker_error)

    cancelled = jobs.cancel(str(queued["id"]))

    assert cancelled["id"] == queued["id"]
    assert cancelled["status"] == "Cancelled"
    assert cancelled["queue_position"] is None
    assert jobs.get(str(queued["id"]))["status"] == "Cancelled"
    history = jobs.list_page()["history_jobs"]
    assert len(history) == 1
    assert history[0]["id"] == queued["id"]
    assert history[0]["status"] == "Cancelled"
    persisted = json.loads(
        (work_root / "jobs" / f"{queued['id']}.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "Cancelled"

    with pytest.raises(ServiceError) as retry_error:
        jobs.retry(str(queued["id"]))
    assert retry_error.value.error_code == "job_retry_conflict"
    assert jobs.clear_completed() == {"deleted": [], "failed": []}
    assert jobs.get(str(queued["id"]))["status"] == "Cancelled"

    jobs.delete(str(queued["id"]))
    assert not (work_root / "jobs" / f"{queued['id']}.json").exists()
    follow_up = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "ko"))
    release.set()
    wait_for_status_from_jobs(jobs, str(running["id"]), "Completed")
    wait_for_status_from_jobs(jobs, str(follow_up["id"]), "Completed")
    assert translator.sources == [subtitle, subtitle]
    assert worker_errors == []
    jobs.close()


def test_cancel_rejects_non_queued_jobs(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(), media_root, work_root)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    with pytest.raises(ServiceError) as raised:
        jobs.cancel(str(queued["id"]))

    assert completed["status"] == "Completed"
    assert raised.value.error_code == "job_cancel_conflict"
    assert raised.value.context == {"status": "Completed"}
    jobs.close()


def test_cancel_skips_a_stale_queue_item_without_executing_it(tmp_path: Path):
    jobs, translator, running, queued, release, subtitle = create_blocked_jobs(tmp_path)
    jobs.cancel(str(queued["id"]))

    release.set()
    wait_for_status_from_jobs(jobs, str(running["id"]), "Completed")

    assert translator.sources == [subtitle]
    assert jobs.get(str(queued["id"]))["status"] == "Cancelled"
    jobs.close()


def test_cancel_persistence_failure_keeps_job_queued_and_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _media_root, work_root, jobs, queued = create_queued_job(tmp_path)
    job_id = str(queued["id"])
    original_write = Jobs._write_record

    def fail_cancel_write(
        instance: Jobs, record_id: str, record: dict[str, object]
    ) -> None:
        if record_id == job_id and record["status"] == "Cancelled":
            raise OSError("record unavailable")
        original_write(instance, record_id, record)

    monkeypatch.setattr(Jobs, "_write_record", fail_cancel_write)

    with pytest.raises(OSError):
        jobs.cancel(job_id)

    assert jobs.get(job_id)["status"] == "Queued"
    persisted = json.loads(
        (work_root / "jobs" / f"{job_id}.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "Queued"
    jobs.close()


def test_retry_persistence_failure_keeps_job_terminal_and_unqueued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _media_root, work_root, _media, _subtitle, jobs, queued = (
        create_failed_external_job(tmp_path, FakeTranslator(error=RuntimeError("boom")))
    )
    original_write = Jobs._write_record

    def fail_retry_write(jobs: Jobs, job_id: str, record: dict[str, object]) -> None:
        if record["status"] == "Queued":
            raise OSError("record unavailable")
        original_write(jobs, job_id, record)

    monkeypatch.setattr(Jobs, "_write_record", fail_retry_write)

    with pytest.raises(OSError):
        jobs.retry(str(queued["id"]))

    assert_failed_record_persisted(jobs, work_root, str(queued["id"]))
    jobs.close()


def test_retry_restores_terminal_record_when_directory_sync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _media_root, work_root, _media, _subtitle, jobs, queued = (
        create_failed_external_job(tmp_path, FakeTranslator(error=RuntimeError("boom")))
    )
    original_fsync = os.fsync
    fsync_calls = 0

    def fail_directory_sync(file_descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("directory sync failed")
        original_fsync(file_descriptor)

    monkeypatch.setattr(
        "cueweaver.application.jobs.store.os.fsync", fail_directory_sync
    )

    with pytest.raises(OSError):
        jobs.retry(str(queued["id"]))

    assert_failed_record_persisted(jobs, work_root, str(queued["id"]))
    jobs.close()


def test_retry_redacts_paths_from_a_malformed_persisted_record(tmp_path: Path):
    media_root, work_root, _media, _subtitle, jobs, queued = create_failed_external_job(
        tmp_path, FakeTranslator(error=RuntimeError("boom"))
    )
    record_path = work_root / "jobs" / f"{queued['id']}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["request"]["media_path"] = str(tmp_path / "private" / "Movie.mkv")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    jobs.close()

    restarted = Jobs(FakeTranslator(), media_root, work_root)
    with pytest.raises(ServiceError) as raised:
        restarted.retry(str(queued["id"]))

    assert raised.value.context == {"media_path": "Movie.mkv"}
    restarted.close()


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
    assert [entry["status"] for entry in extracting_record["status_history"]] == [
        "Queued",
        "Extracting",
    ]
    assert "subtitle_path" not in extracting_record["request"]
    assert extracting_record["request"]["stream_index"] == 3
    assert extracting_record["extraction"] is None

    release.set()
    assert translating.wait(timeout=5)
    translating_record = jobs.get(str(queued["id"]))
    assert translating_record["status"] == "Translating"
    assert [entry["status"] for entry in translating_record["status_history"]] == [
        "Queued",
        "Extracting",
        "Translating",
    ]
    extraction_record = translating_record["extraction"]
    assert isinstance(extraction_record, dict)
    assert extraction_record["status"] == "Completed"
    assert extraction_record["path"] == f"source.{source_format}"
    assert extraction_record["format"] == source_format
    assert isinstance(extraction_record["content_digest"], str)
    translation_release.set()
    deadline = time.monotonic() + 5
    while jobs.get(str(queued["id"]))["status"] != "Completed":
        if time.monotonic() >= deadline:
            pytest.fail("Embedded Job did not complete")
        time.sleep(0.01)

    completed = jobs.get(str(queued["id"]))
    assert [entry["status"] for entry in completed["status_history"]] == [
        "Queued",
        "Extracting",
        "Translating",
        "Completed",
    ]
    assert completed["request"]["output_path"] == f"Movie.zh-Hans.{source_format}"
    assert media_adapter.probe_calls == [_media]
    assert media_adapter.extract_calls[0][:2] == (_media, 3)
    extracted_path = media_adapter.extract_calls[0][2]
    assert extracted_path.parent == work_root / "jobs" / str(queued["id"])
    assert extracted_path.name != f"source.{source_format}"
    assert extracted_path.suffix == f".{source_format}"
    assert translator.sources == [
        work_root / "jobs" / str(queued["id"]) / f"source.{source_format}"
    ]
    assert (media_root / f"Movie.zh-Hans.{source_format}").read_bytes() == SRT
    assert not (work_root / "jobs" / str(queued["id"])).exists()
    jobs.close()


def test_embedded_phase_persistence_failure_marks_worker_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root, work_root, media, _subtitle = make_roots(tmp_path)
    media_adapter = MediaExtractorFixture([{"index": 3, "codec_name": "subrip"}])
    jobs = Jobs(
        FakeTranslator(),
        media_root,
        work_root,
        extraction=Extraction(media_adapter, AtomicOutputPublisher()),
    )
    original_write = Jobs._write_record
    failed_once = False

    def fail_translating_write(
        current_jobs: Jobs, job_id: str, record: dict[str, object]
    ) -> None:
        nonlocal failed_once
        if not failed_once and record["status"] == "Translating":
            failed_once = True
            raise OSError("record unavailable")
        original_write(current_jobs, job_id, record)

    monkeypatch.setattr(Jobs, "_write_record", fail_translating_write)
    queued = jobs.create(
        CreateJobRequest(
            media.name,
            None,
            "zh-Hans",
            stream_index=3,
            source_format="srt",
        )
    )

    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")

    assert failed["error"]["code"] == "job_worker_failed"
    assert jobs.get(str(queued["id"]))["status"] == "Failed"
    assert len(media_adapter.extract_calls) == 1
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
        assert client.get("/api/jobs").json() == {
            "active_jobs": [],
            "history_jobs": [],
            "next_cursor": None,
        }
        assert list((work_root / "jobs").glob("*.json")) == []


def test_job_list_separates_active_jobs_and_redacts_term_map_content(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    release = threading.Event()
    translator = RecordingTranslator(release)
    with make_client(media_root, work_root, translator) as client:
        term_map = client.post(
            "/api/term-maps",
            json={"name": "Characters", "content": {"Captain": "队长"}},
        ).json()
        running = client.post(
            "/api/jobs",
            json={
                "media_path": "Movie.mkv",
                "subtitle_path": "Movie.en.srt",
                "target_language_code": "zh-Hans",
                "term_map_id": term_map["id"],
            },
        ).json()
        assert "content" not in running["request"]["term_map"]
        assert translator.started.wait(timeout=5)
        queued = create_job(client, "ja").json()

        page = client.get("/api/jobs").json()
        assert [job["status"] for job in page["active_jobs"]] == [
            "Translating",
            "Queued",
        ]
        assert all(
            job["request"]["term_map"] is None
            or "content" not in job["request"]["term_map"]
            for job in page["active_jobs"]
        )
        assert page["history_jobs"] == []
        assert page["next_cursor"] is None

        release.set()
        wait_for_status(client, running["id"], "Completed")
        wait_for_status(client, queued["id"], "Completed")
        detail = client.get(f"/api/jobs/{running['id']}").json()
        assert "content" not in detail["request"]["term_map"]

    persisted = json.loads(
        (work_root / "jobs" / f"{running['id']}.json").read_text(encoding="utf-8")
    )
    assert persisted["request"]["term_map"]["content"] == {"Captain": "队长"}


def test_job_history_uses_bounded_stable_cursor_pagination(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    with make_client(media_root, work_root, FakeTranslator()) as client:
        jobs = [create_job(client, target).json() for target in ("zh", "ja", "ko")]
        for job in jobs:
            wait_for_status(client, job["id"], "Completed")

        first_page = client.get("/api/jobs?limit=2").json()
        assert first_page["active_jobs"] == []
        assert len(first_page["history_jobs"]) == 2
        assert first_page["next_cursor"] is not None
        assert first_page["next_cursor"] not in {
            first_page["history_jobs"][-1]["id"],
            first_page["history_jobs"][-1]["created_at"],
        }
        cursor = first_page["next_cursor"]
        tampered_cursor = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
        assert (
            client.get("/api/jobs", params={"cursor": tampered_cursor}).status_code
            == 400
        )

        second_page = client.get(
            "/api/jobs", params={"limit": 2, "cursor": cursor}
        ).json()
        assert len(second_page["history_jobs"]) == 1
        assert second_page["next_cursor"] is None
        assert {
            job["id"]
            for job in first_page["history_jobs"] + second_page["history_jobs"]
        } == {job["id"] for job in jobs}


@pytest.mark.parametrize("query", ["?limit=0", "?limit=101", "?cursor=malformed"])
def test_job_history_rejects_invalid_pagination_parameters(tmp_path: Path, query: str):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    with make_client(media_root, work_root, FakeTranslator()) as client:
        response = client.get(f"/api/jobs{query}")

    assert response.status_code == 400
    assert response.json()["error_code"] in {"invalid_request", "invalid_job_cursor"}


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
        }
        assert queued["request"]["term_map"] == {
            "id": queued_term_map["id"],
            "name": "Ships",
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
        }
        assert client.get(f"/api/jobs/{queued['id']}").json()["request"][
            "term_map"
        ] == {
            "id": queued_term_map["id"],
            "name": "Ships",
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
        _term_map, queued = create_term_map_job(client)

        failed = wait_for_status(client, queued["id"], "Failed")

        assert "content" not in failed["request"]["term_map"]
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
        "cueweaver.application.jobs.execution.Translation.translate", fail_translation
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


@pytest.mark.parametrize(
    "write_failure",
    [OSError("terminal record unavailable"), RuntimeError("store down")],
)
def test_terminal_persistence_failure_is_classified_as_worker_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_failure: Exception
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    started = threading.Event()
    release = threading.Event()
    jobs = Jobs(FakeTranslator(started=started, release=release), media_root, work_root)
    original_write = Jobs._write_record
    first_id: str | None = None

    def fail_completed_write(jobs, job_id, record):
        if job_id == first_id and record["status"] in {"Completed", "Failed"}:
            raise write_failure
        original_write(jobs, job_id, record)

    monkeypatch.setattr(Jobs, "_write_record", fail_completed_write)
    first = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    first_id = str(first["id"])
    assert started.wait(timeout=5)
    second = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "ja"))
    release.set()

    failed = wait_for_status_from_jobs(jobs, first_id, "Failed")
    completed = wait_for_status_from_jobs(jobs, str(second["id"]), "Completed")

    assert failed["error"] == {
        "code": "job_worker_failed",
        "message": "Job execution could not be persisted",
    }
    assert completed["status"] == "Completed"
    jobs.close()


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


@pytest.mark.parametrize("terminal_status", ["Completed", "Failed"])
def test_delete_terminal_job_removes_history_and_work_without_touching_media(
    tmp_path: Path, terminal_status: str
):
    media_root, work_root, media, subtitle = make_roots(tmp_path)
    translator = FakeTranslator(
        error=RuntimeError("boom") if terminal_status == "Failed" else None
    )
    jobs = Jobs(translator, media_root, work_root)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    terminal = wait_for_status_from_jobs(jobs, str(queued["id"]), terminal_status)
    work_directory = work_root / "jobs" / str(queued["id"])
    work_directory.mkdir(exist_ok=True)
    (work_directory / "diagnostic.txt").write_text(
        "keep until delete", encoding="utf-8"
    )
    output = media_root / str(terminal["request"]["output_path"])
    media_before = media.read_bytes()
    subtitle_before = subtitle.read_bytes()
    output_before = output.read_bytes() if output.exists() else None

    assert jobs.delete(str(queued["id"])) == {"id": queued["id"], "deleted": True}
    assert jobs.list() == []
    assert not (work_root / "jobs" / f"{queued['id']}.json").exists()
    assert not work_directory.exists()
    assert media.read_bytes() == media_before
    assert subtitle.read_bytes() == subtitle_before
    if output_before is None:
        assert not output.exists()
    else:
        assert output.read_bytes() == output_before
    jobs.close()


def test_delete_interrupted_job_removes_history_and_retained_work(tmp_path: Path):
    media_root, work_root, media, _subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(error=RuntimeError("boom")), media_root, work_root)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    jobs.close()
    record_path = work_root / "jobs" / f"{queued['id']}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    set_record_status(record, "Interrupted", finished_at=record["finished_at"])
    record_path.write_text(json.dumps(record), encoding="utf-8")
    work_directory = work_root / "jobs" / str(queued["id"])
    (work_directory / "checkpoint").write_text("checkpoint", encoding="utf-8")

    restarted = Jobs(FakeTranslator(), media_root, work_root)
    assert restarted.get(str(queued["id"]))["status"] == "Interrupted"
    media_before = media.read_bytes()
    restarted.delete(str(queued["id"]))

    assert restarted.list() == []
    assert not work_directory.exists()
    assert not record_path.exists()
    assert media.read_bytes() == media_before
    restarted.close()


def test_delete_rejects_queued_and_translating_jobs(tmp_path: Path):
    jobs, _translator, running, queued, release, _subtitle = create_blocked_jobs(
        tmp_path
    )

    with pytest.raises(ServiceError) as running_error:
        jobs.delete(str(running["id"]))
    with pytest.raises(ServiceError) as queued_error:
        jobs.delete(str(queued["id"]))

    assert running_error.value.error_code == "job_delete_conflict"
    assert running_error.value.context == {"status": "Translating"}
    assert queued_error.value.error_code == "job_delete_conflict"
    assert queued_error.value.context == {"status": "Queued"}
    release.set()
    wait_for_status_from_jobs(jobs, str(running["id"]), "Completed")
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")
    jobs.close()


def test_delete_rejects_extracting_jobs(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    release = threading.Event()
    extracting = threading.Event()
    media_adapter = MediaExtractorFixture(
        [{"index": 3, "codec_name": "subrip"}],
        started=extracting,
        release=release,
    )
    jobs = Jobs(
        FakeTranslator(),
        media_root,
        work_root,
        extraction=Extraction(media_adapter, AtomicOutputPublisher()),
    )
    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            None,
            "zh",
            stream_index=3,
            source_format="srt",
        )
    )
    assert extracting.wait(timeout=5)

    with pytest.raises(ServiceError) as raised:
        jobs.delete(str(queued["id"]))

    assert raised.value.error_code == "job_delete_conflict"
    assert raised.value.context == {"status": "Extracting"}
    release.set()
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")
    jobs.close()


def test_delete_cleanup_failure_keeps_record_and_preserves_published_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root, work_root, media, _subtitle = make_roots(tmp_path)
    jobs = Jobs(FakeTranslator(), media_root, work_root)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")
    work_directory = work_root / "jobs" / str(queued["id"])
    work_directory.mkdir(exist_ok=True)
    output = media_root / str(completed["request"]["output_path"])
    output_before = output.read_bytes()
    original_rmtree = shutil.rmtree

    def fail_cleanup(path: Path) -> None:
        if path == work_directory:
            raise OSError("permission denied")
        original_rmtree(path)

    monkeypatch.setattr("cueweaver.application.jobs.shutil.rmtree", fail_cleanup)

    with pytest.raises(ServiceError) as raised:
        jobs.delete(str(queued["id"]))

    assert raised.value.error_code == "job_work_cleanup_failed"
    assert raised.value.context == {"path": f"jobs/{queued['id']}"}
    assert jobs.get(str(queued["id"]))["status"] == "Completed"
    assert (work_root / "jobs" / f"{queued['id']}.json").exists()
    assert work_directory.exists()
    assert output.read_bytes() == output_before
    assert media.exists()
    jobs.close()


def test_delete_record_sync_failure_restores_history_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _media_root, work_root, jobs, queued = create_queued_job(tmp_path)
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")
    work_directory = work_root / "jobs" / str(queued["id"])
    work_directory.mkdir()
    record_path = work_root / "jobs" / f"{queued['id']}.json"
    original_fsync = os.fsync

    def fail_directory_sync(_file_descriptor: int) -> None:
        raise OSError("directory sync failed")

    monkeypatch.setattr(
        "cueweaver.application.jobs.store.os.fsync", fail_directory_sync
    )

    with pytest.raises(ServiceError) as raised:
        jobs.delete(str(queued["id"]))

    assert raised.value.error_code == "job_record_delete_failed"
    assert record_path.exists()
    assert jobs.get(str(queued["id"]))["status"] == "Completed"
    assert not work_directory.exists()
    monkeypatch.setattr("cueweaver.application.jobs.store.os.fsync", original_fsync)
    jobs.close()


def test_clear_completed_is_deterministic_and_retains_partial_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    translator = FakeTranslator(error=RuntimeError("boom"))
    jobs = Jobs(translator, media_root, work_root)
    failed = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "failed"))
    wait_for_status_from_jobs(jobs, str(failed["id"]), "Failed")
    translator.error = None
    first = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "first"))
    second = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "second"))
    wait_for_status_from_jobs(jobs, str(first["id"]), "Completed")
    wait_for_status_from_jobs(jobs, str(second["id"]), "Completed")
    for job in (first, second):
        directory = work_root / "jobs" / str(job["id"])
        directory.mkdir()
        (directory / "marker").write_text("retained", encoding="utf-8")
    failed_cleanup_id = max(str(first["id"]), str(second["id"]))
    deleted_id = min(str(first["id"]), str(second["id"]))
    original_rmtree = shutil.rmtree

    def fail_one_cleanup(path: Path) -> None:
        if path == work_root / "jobs" / failed_cleanup_id:
            raise OSError("permission denied")
        original_rmtree(path)

    monkeypatch.setattr("cueweaver.application.jobs.shutil.rmtree", fail_one_cleanup)

    result = jobs.clear_completed()

    assert result == {
        "deleted": [deleted_id],
        "failed": [
            {
                "id": failed_cleanup_id,
                "error_code": "job_work_cleanup_failed",
                "message": "Job Work data could not be cleaned up",
                "path": f"jobs/{failed_cleanup_id}",
            }
        ],
    }
    assert jobs.get(str(failed["id"]))["status"] == "Failed"
    assert jobs.get(failed_cleanup_id)["status"] == "Completed"
    assert not (work_root / "jobs" / deleted_id).exists()
    assert (work_root / "jobs" / failed_cleanup_id).exists()
    assert not (work_root / "jobs" / f"{deleted_id}.json").exists()
    assert (work_root / "jobs" / f"{failed_cleanup_id}.json").exists()
    jobs.close()


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


def test_retry_recomputes_append_number_from_original_output_name(tmp_path: Path):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    original_output = media_root / "Movie.zh.srt"
    original_output.write_bytes(b"keep")
    translator = FakeTranslator(error=RuntimeError("boom"))
    jobs = Jobs(translator, media_root, work_root)

    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    assert failed["request"]["output_path"] == "Movie.zh.2.srt"
    original_output.unlink()
    translator.error = None

    jobs.retry(str(queued["id"]))
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    assert completed["request"]["output_path"] == "Movie.zh.srt"
    assert original_output.read_bytes() == SRT
    assert not (media_root / "Movie.zh.2.srt").exists()
    jobs.close()


def test_retry_preserves_overwrite_output_policy_and_atomic_replacement(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    output = media_root / "Movie.zh.srt"
    output.write_bytes(b"old")
    translator = FakeTranslator(error=RuntimeError("boom"))
    jobs = Jobs(translator, media_root, work_root)

    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            "Movie.en.srt",
            "zh",
            output_conflict_policy="overwrite",
        )
    )
    wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")
    translator.error = None

    retried = jobs.retry(str(queued["id"]))
    completed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Completed")

    assert retried["request"]["output_conflict_policy"] == "overwrite"
    assert completed["request"]["output_path"] == "Movie.zh.srt"
    assert output.read_bytes() == SRT
    jobs.close()


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
    set_record_status(record, active_status, finished_at=None)
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
    set_record_status(record, "Translating", finished_at=None)
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


def test_external_job_retries_after_restart_recovery(tmp_path: Path):
    media_root, work_root, queued, record_path, record = persisted_external_job(
        tmp_path
    )
    set_record_status(record, "Translating", finished_at=None)
    record["error"] = None
    record_path.write_text(json.dumps(record), encoding="utf-8")
    work_directory = work_root / "jobs" / queued["id"]
    work_directory.mkdir()
    (work_directory / "checkpoint-marker").write_text("keep", encoding="utf-8")
    translator = FakeTranslator(error=RuntimeError("must not run before retry"))

    restarted = Jobs(translator, media_root, work_root)

    recovered = restarted.get(queued["id"])
    assert recovered["status"] == "Interrupted"
    assert recovered["id"] == queued["id"]
    assert recovered["attempt"] == 1
    assert recovered["error"] == {
        "code": "job_interrupted",
        "message": "Job was interrupted when CueWeaver stopped",
    }
    assert (work_directory / "checkpoint-marker").read_text(encoding="utf-8") == "keep"
    translator.error = None

    retried = restarted.retry(queued["id"])
    completed = wait_for_status_from_jobs(restarted, queued["id"], "Completed")

    assert retried["id"] == queued["id"]
    assert retried["attempt"] == 2
    assert completed["attempt"] == 2
    assert (media_root / "Movie.zh-Hans.srt").read_bytes() == SRT
    assert not work_directory.exists()
    restarted.close()


@pytest.mark.parametrize("terminal_status", ["Completed", "Failed"])
def test_restart_preserves_terminal_job_records(tmp_path: Path, terminal_status: str):
    media_root, work_root, queued, record_path, record = persisted_external_job(
        tmp_path
    )
    set_record_status(record, terminal_status, finished_at="2026-08-14T00:00:00Z")
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
    set_record_status(record, "Translating", finished_at=None)
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
    set_record_status(record, active_status, finished_at=None)
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

    jobs = Jobs(FakeTranslator(started=started, release=release), media_root, work_root)
    jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    assert started.wait(timeout=5)

    closed = threading.Event()
    shutdown = threading.Thread(target=lambda: (jobs.close(), closed.set()))
    shutdown.start()
    assert closed.wait(timeout=0.5)

    release.set()
    shutdown.join(timeout=5)
    jobs._worker.join(timeout=5)


def test_shutdown_marks_blocked_translation_interrupted_at_safe_point(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    started = threading.Event()
    release = threading.Event()

    jobs = Jobs(FakeTranslator(started=started, release=release), media_root, work_root)
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    assert started.wait(timeout=5)

    interrupted = finish_shutdown(jobs, str(queued["id"]), release)
    assert interrupted["status"] == "Interrupted"
    assert interrupted["error"] == {
        "code": "job_interrupted",
        "message": "Job was interrupted when CueWeaver stopped",
    }
    assert not (media_root / "Movie.zh.srt").exists()


def test_shutdown_marks_failed_blocked_translation_interrupted(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    started = threading.Event()
    release = threading.Event()
    jobs = Jobs(
        FakeTranslator(
            started=started,
            release=release,
            error=RuntimeError("translation failed after stop"),
        ),
        media_root,
        work_root,
    )
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    assert started.wait(timeout=5)

    assert finish_shutdown(jobs, str(queued["id"]), release)["status"] == (
        "Interrupted"
    )


def test_shutdown_marks_blocked_extraction_interrupted_before_translation(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    started = threading.Event()
    release = threading.Event()
    extractor = MediaExtractorFixture(
        [{"index": 3, "codec_name": "subrip"}],
        started=started,
        release=release,
    )
    translator = FakeTranslator()
    jobs = Jobs(
        translator,
        media_root,
        work_root,
        extraction=Extraction(extractor, AtomicOutputPublisher()),
    )
    queued = jobs.create(
        CreateJobRequest(
            "Movie.mkv",
            None,
            "zh",
            stream_index=3,
            source_format="srt",
        )
    )
    assert started.wait(timeout=5)

    interrupted = finish_shutdown(jobs, str(queued["id"]), release)
    assert interrupted["status"] == "Interrupted"
    assert translator.sources == []
    assert not (media_root / "Movie.zh.srt").exists()


def test_shutdown_recovers_after_interrupted_record_write_failure(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    started = threading.Event()
    release = threading.Event()
    store = InterruptedWriteFailureStore(
        work_root / "jobs", OSError("record unavailable"), once=True
    )
    jobs = Jobs(
        FakeTranslator(started=started, release=release),
        media_root,
        work_root,
        record_store=store,
    )
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    assert started.wait(timeout=5)

    assert finish_shutdown(jobs, str(queued["id"]), release)["status"] == (
        "Interrupted"
    )
    persisted = json.loads(
        (work_root / "jobs" / f"{queued['id']}.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "Translating"

    restarted = Jobs(FakeTranslator(), media_root, work_root, record_store=store)
    assert restarted.get(str(queued["id"]))["status"] == "Interrupted"
    restarted.close()


def test_non_oserror_interrupted_persistence_failure_is_worker_failure(
    tmp_path: Path,
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    started = threading.Event()
    release = threading.Event()
    store = InterruptedWriteFailureStore(
        work_root / "jobs", RuntimeError("record store unavailable")
    )
    jobs = Jobs(
        FakeTranslator(started=started, release=release),
        media_root,
        work_root,
        record_store=store,
    )
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    assert started.wait(timeout=5)

    jobs.close()
    release.set()
    failed = wait_for_status_from_jobs(jobs, str(queued["id"]), "Failed")

    assert failed["error"] == {
        "code": "job_worker_failed",
        "message": "Job execution could not be persisted",
    }


def test_shutdown_after_publish_persists_completed_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root, work_root, _media, _subtitle = make_roots(tmp_path)
    published = threading.Event()
    release = threading.Event()
    close_attempted = threading.Event()

    class BlockingPublisher(AtomicOutputPublisher):
        def publish(self, output_path: Path, write, *, overwrite: bool = False) -> None:
            super().publish(output_path, write, overwrite=overwrite)
            published.set()
            release.wait(timeout=5)

    class InstrumentedLock:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.observe_acquire = False

        def acquire(self, *args, **kwargs) -> bool:
            if self.observe_acquire:
                close_attempted.set()
            return self._lock.acquire(*args, **kwargs)

        def release(self) -> None:
            self._lock.release()

        def __enter__(self) -> "InstrumentedLock":
            self.acquire()
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            self.release()

    monkeypatch.setattr(
        "cueweaver.application.jobs.AtomicOutputPublisher", BlockingPublisher
    )
    jobs = Jobs(FakeTranslator(), media_root, work_root)
    lifecycle_lock = InstrumentedLock()
    jobs._lifecycle_lock = lifecycle_lock
    queued = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh"))
    assert published.wait(timeout=5)
    lifecycle_lock.observe_acquire = True

    def close_jobs() -> None:
        jobs.close()

    close_thread = threading.Thread(target=close_jobs)
    close_thread.start()
    assert close_attempted.wait(timeout=5)
    assert close_thread.is_alive()

    release.set()
    close_thread.join(timeout=5)
    assert not close_thread.is_alive()
    record = json.loads(
        (work_root / "jobs" / f"{queued['id']}.json").read_text(encoding="utf-8")
    )
    assert record["status"] == "Completed"
    assert not (work_root / "jobs" / str(queued["id"])).exists()
