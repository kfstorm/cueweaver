"""Ownership and safety contract for the configured Work root."""

from __future__ import annotations

import tempfile
from pathlib import Path


class WorkRoot:
    """Own the stable Work layout and per-Job directory boundaries."""

    def __init__(self, path: Path) -> None:
        path = Path(path)
        if not path.is_absolute():
            raise ValueError("Work root must be an absolute path")
        self.path = path.resolve()
        self.jobs_directory = self.path / "jobs"
        self.term_maps_directory = self.path / "term-maps"

    def prepare(self) -> None:
        """Create the root and verify the capabilities required by the product."""
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            if not self.path.is_dir():
                raise OSError
            with tempfile.TemporaryDirectory(
                prefix=".cueweaver-check-", dir=self.path
            ) as raw:
                probe_directory = Path(raw)
                source = probe_directory / "source"
                destination = probe_directory / "destination"
                source.write_bytes(b"ready")
                if source.read_bytes() != b"ready":
                    raise OSError
                destination.write_bytes(b"replace")
                source.replace(destination)
                if destination.read_bytes() != b"ready":
                    raise OSError
        except OSError as error:
            raise ValueError(
                "Work root must support reading, writing, directory creation, and atomic replacement"
            ) from error

    def job_directory(self, job_id: str) -> Path:
        if not is_safe_job_identifier(job_id):
            raise ValueError("Job ID is invalid")
        self._ensure_jobs_directory()
        directory = self.jobs_directory / job_id
        if directory.is_symlink():
            raise ValueError("Job Work directory must not be a symbolic link")
        try:
            resolved = directory.resolve()
        except OSError as error:
            raise ValueError("Job Work directory cannot be resolved") from error
        if not resolved.is_relative_to(self.path):
            raise ValueError("Job Work directory must remain inside the Work root")
        return directory

    def translation_directory(self, job_id: str) -> Path:
        directory = self.job_directory(job_id) / "translation"
        if directory.is_symlink():
            raise ValueError("Job translation directory must not be a symbolic link")
        try:
            if not directory.resolve().is_relative_to(self.path):
                raise ValueError(
                    "Job translation directory must remain inside the Work root"
                )
        except OSError as error:
            raise ValueError("Job translation directory cannot be resolved") from error
        return directory

    def _ensure_jobs_directory(self) -> None:
        if self.jobs_directory.is_symlink():
            raise ValueError("Job Work root must not be a symbolic link")
        try:
            self.jobs_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError("Job Work root cannot be created") from error


def is_safe_job_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and "\\" not in value
        and "\x00" not in value
        and not Path(value).is_absolute()
        and Path(value).name == value
    )


__all__ = ["WorkRoot", "is_safe_job_identifier"]
