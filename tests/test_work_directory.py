import os
import selectors
import subprocess
import sys
from pathlib import Path

import pytest

from cueweaver.adapters.term_maps import FileTermMapStore
from cueweaver.application.jobs import Jobs
from cueweaver.work import WorkRoot, WorkRootLease


def test_work_root_exposes_the_stable_layout(tmp_path: Path):
    work = WorkRoot(tmp_path / "work")

    assert work.jobs_directory == tmp_path / "work" / "jobs"
    assert work.term_maps_directory == tmp_path / "work" / "term-maps"
    assert work.job_directory("job-1") == tmp_path / "work" / "jobs" / "job-1"
    assert work.translation_directory("job-1") == (
        tmp_path / "work" / "jobs" / "job-1" / "translation"
    )


@pytest.mark.parametrize("job_id", ["", ".", "..", "../outside", "a\\b", "/tmp/job"])
def test_work_root_rejects_unsafe_job_identifiers(tmp_path: Path, job_id: str):
    with pytest.raises(ValueError, match="Job ID is invalid"):
        WorkRoot(tmp_path / "work").job_directory(job_id)


def test_work_root_rejects_symlinked_job_directory(tmp_path: Path):
    root = WorkRoot(tmp_path / "work")
    root.jobs_directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root.jobs_directory / "job-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symbolic link"):
        root.job_directory("job-1")


def test_work_root_rejects_symlinked_translation_directory(tmp_path: Path):
    root = WorkRoot(tmp_path / "work")
    job_directory = root.job_directory("job-1")
    job_directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (job_directory / "translation").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symbolic link"):
        root.translation_directory("job-1")


def test_term_map_store_requires_the_work_root_policy(tmp_path: Path):
    with pytest.raises(TypeError, match="requires a WorkRoot"):
        FileTermMapStore(tmp_path)  # type: ignore[arg-type]


def test_work_root_lease_blocks_another_process_until_release(tmp_path: Path):
    lease_path = tmp_path / "work" / ".cueweaver.lease"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys; from cueweaver.work import WorkRootLease; "
                "lease = WorkRootLease(__import__('pathlib').Path(sys.argv[1])); "
                "lease.acquire(); print('ready', flush=True); sys.stdin.read(); "
                "lease.release()"
            ),
            str(lease_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        selector = selectors.DefaultSelector()
        try:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5), "Lease child did not become ready"
            assert child.stdout.readline().strip() == "ready"
        finally:
            selector.close()
        with pytest.raises(ValueError, match="already in use"):
            WorkRootLease(lease_path).acquire()
    finally:
        if child.stdin is not None:
            child.stdin.close()
        child.wait(timeout=5)

    released = WorkRootLease(lease_path)
    released.acquire()
    released.release()


def test_sigkill_releases_lease_and_restart_marks_active_job_interrupted(
    tmp_path: Path,
):
    media_root = tmp_path / "media"
    work_root = tmp_path / "work"
    media_root.mkdir()
    (media_root / "Movie.mkv").write_bytes(b"media")
    (media_root / "Movie.en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nSource\n", encoding="utf-8"
    )
    child_code = """
import sys
import threading
import time
from pathlib import Path
from cueweaver.application.jobs import CreateJobRequest, Jobs

started = threading.Event()

class Translator:
    available = True

    def translate(self, _source, _target_language, **_kwargs):
        started.wait()
        print("started", flush=True)
        while True:
            time.sleep(1)

jobs = Jobs(Translator(), Path(sys.argv[1]), Path(sys.argv[2]))
job = jobs.create(CreateJobRequest("Movie.mkv", "Movie.en.srt", "zh", "none"))
print(job["id"], flush=True)
started.set()
time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(media_root), str(work_root)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        pending = b""

        def read_line() -> str:
            nonlocal pending
            while b"\n" not in pending:
                assert selector.select(timeout=5), "Job child did not emit a line"
                chunk = os.read(child.stdout.fileno(), 4096)
                assert chunk, "Job child exited before emitting a complete line"
                pending += chunk
            line, pending = pending.split(b"\n", maxsplit=1)
            return line.decode()

        selector = selectors.DefaultSelector()
        try:
            selector.register(child.stdout, selectors.EVENT_READ)
            job_id = read_line()
            assert job_id
            assert read_line() == "started"
        finally:
            selector.close()
        child.kill()
        assert child.wait(timeout=5) < 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)

    class RecoveryTranslator:
        available = True

        def translate(self, _source, _target_language, **_kwargs) -> bytes:
            return b"1\n00:00:00,000 --> 00:00:01,000\nRecovered\n"

    jobs = Jobs(RecoveryTranslator(), media_root, work_root)
    try:
        assert jobs.get(job_id)["status"] == "Interrupted"
    finally:
        jobs.close()
