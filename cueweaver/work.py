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
            ) as temporary_directory:
                probe_directory = Path(temporary_directory)
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
        return self._safe_directory(
            self.jobs_directory / job_id,
            "Job Work directory",
        )

    def translation_directory(self, job_id: str) -> Path:
        return self._safe_directory(
            self.job_directory(job_id) / "translation",
            "Job translation directory",
        )

    def ensure_translation_directory(self, job_id: str) -> Path:
        return self._ensure_directory(
            self.translation_directory(job_id), "Job translation directory"
        )

    def ensure_term_maps_directory(self) -> Path:
        return self._ensure_directory(self.term_maps_directory, "Term map directory")

    def _ensure_directory(self, directory: Path, label: str) -> Path:
        directory = self._safe_directory(directory, label)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(f"{label} cannot be created") from error
        return self._safe_directory(directory, label)

    def _safe_directory(self, directory: Path, label: str) -> Path:
        if directory.is_symlink():
            raise ValueError(f"{label} must not be a symbolic link")
        try:
            resolved = directory.resolve()
        except OSError as error:
            raise ValueError(f"{label} cannot be resolved") from error
        if not resolved.is_relative_to(self.path):
            raise ValueError(f"{label} must remain inside the Work root")
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
