"""Persistent Job workspaces kept outside Media directories."""

from __future__ import annotations

import hashlib
import os
from os import PathLike
from pathlib import Path


def default_work_root() -> Path:
    """Return the user-level root for CueWeaver's durable Job files."""

    configured = os.environ.get("CUEWEAVER_WORK_DIRECTORY")
    if configured:
        return Path(configured).expanduser().resolve()
    cache_home = os.environ.get("XDG_CACHE_HOME")
    root = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return root / "cueweaver" / "jobs"


def job_work_directory(
    media: PathLike[str] | str,
    target_language: str,
    source_identity: str,
    *,
    dynamic_terminology_enabled: bool = True,
    episode_terminology_filter_enabled: bool = True,
) -> Path:
    """Return the stable workspace for one Media/Source/Target Job."""

    media_path = Path(media).expanduser().resolve()
    try:
        stat = media_path.stat()
        media_version = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        media_version = "unknown"
    key_material = "\0".join(
        (
            str(media_path),
            media_version,
            target_language,
            source_identity,
            str(dynamic_terminology_enabled),
            str(episode_terminology_filter_enabled),
        )
    ).encode("utf-8")
    digest = hashlib.sha256(key_material).hexdigest()[:16]
    return default_work_root() / digest


def extraction_cache_path(
    media: PathLike[str] | str,
    *,
    track_identity: str,
    codec: str | None,
    extension: str,
) -> Path:
    """Return the reusable Extraction cache path for one Embedded Source."""

    media_path = Path(media).expanduser().resolve()
    try:
        stat = media_path.stat()
        media_version = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        media_version = "unknown"
    key_material = "\0".join(
        (
            str(media_path),
            media_version,
            track_identity,
            codec or "",
            extension,
        )
    ).encode("utf-8")
    digest = hashlib.sha256(key_material).hexdigest()[:16]
    return default_work_root() / "extraction" / f"{media_path.stem}.{digest}{extension}"
