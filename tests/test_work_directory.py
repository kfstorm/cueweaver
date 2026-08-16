from pathlib import Path

import pytest

from cueweaver.adapters.term_maps import FileTermMapStore
from cueweaver.work import WorkRoot


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
