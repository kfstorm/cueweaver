"""Safe one-directory-at-a-time browsing of the configured Media root."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..errors import ServiceError

MEDIA_EXTENSIONS = frozenset(
    {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts"}
)
MAX_NFO_BYTES = 1024 * 1024


@dataclass(frozen=True)
class BrowseRequest:
    path: Path


@dataclass(frozen=True)
class BrowseEntry:
    name: str
    path: Path
    kind: Literal["directory", "media"]
    title: str | None = None
    year: int | None = None


@dataclass(frozen=True)
class BrowseResult:
    path: Path
    entries: list[BrowseEntry] = field(default_factory=list)


class MediaBrowser:
    def __init__(self, media_root: Path) -> None:
        self._media_root = media_root.resolve()

    def browse(self, request: BrowseRequest) -> BrowseResult:
        relative_directory = _requested_relative_path(request.path)
        self._resolve_requested_directory(relative_directory)
        try:
            children = list((self._media_root / relative_directory).iterdir())
        except OSError as error:
            raise ServiceError(
                "directory_unreadable",
                "Media directory cannot be read",
                path=str(request.path),
            ) from error

        entries = [
            entry
            for child in children
            if not child.name.startswith(".")
            and (entry := self._entry_for(child)) is not None
        ]
        entries.sort(
            key=lambda entry: (entry.kind != "directory", _natural_key(entry.name))
        )
        return BrowseResult(relative_directory, entries)

    def _resolve_requested_directory(self, requested: Path) -> Path:
        raw = str(requested)
        if not raw or raw == ".":
            raw = "."
        if "\\" in raw or "\x00" in raw or Path(raw).is_absolute():
            raise ServiceError("invalid_media_path", "Media path must be relative")
        if ".." in Path(raw).parts:
            raise ServiceError(
                "invalid_media_path",
                "Media path must stay inside Media root",
                path=raw,
            )
        directory = (self._media_root / Path(raw)).resolve()
        self._require_inside_root(directory, raw)
        if not directory.is_dir():
            raise ServiceError(
                "directory_not_found", "Media directory does not exist", path=raw
            )
        return directory

    def _entry_for(self, child: Path) -> BrowseEntry | None:
        try:
            resolved = child.resolve()
        except (OSError, RuntimeError):
            return None
        if not resolved.is_relative_to(self._media_root):
            return None
        relative = _relative_path(child, self._media_root)
        if resolved.is_dir():
            title, year = _read_nfo(child / "tvshow.nfo", self._media_root)
            return BrowseEntry(child.name, relative, "directory", title, year)
        if not resolved.is_file() or child.suffix.casefold() not in MEDIA_EXTENSIONS:
            return None
        title, year = _media_nfo(child, self._media_root)
        return BrowseEntry(child.name, relative, "media", title, year)

    def _require_inside_root(self, path: Path, requested: str) -> None:
        if not path.is_relative_to(self._media_root):
            raise ServiceError(
                "invalid_media_path",
                "Media path must stay inside Media root",
                path=requested,
            )


def _relative_path(path: Path, root: Path) -> Path:
    return path.relative_to(root)


def _requested_relative_path(requested: Path) -> Path:
    return Path(".") if not str(requested) else requested


def _natural_key(name: str) -> tuple[tuple[int, str | int], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", name)
        if part
    )


def _media_nfo(media: Path, root: Path) -> tuple[str | None, int | None]:
    candidates = (
        () if media.stem.casefold() == "tvshow" else (media.with_suffix(".nfo"),)
    )
    for candidate in (*candidates, media.parent / "movie.nfo"):
        metadata = _read_nfo(candidate, root)
        if metadata != (None, None):
            return metadata
    return None, None


def _read_nfo(path: Path, root: Path) -> tuple[str | None, int | None]:
    try:
        if not path.resolve().is_relative_to(root):
            return None, None
        if not path.is_file() or path.stat().st_size > MAX_NFO_BYTES:
            return None, None
        content = path.read_bytes()
        if len(content) > MAX_NFO_BYTES:
            return None, None
        decoded = _decode_xml(content)
        if re.search(r"<!\s*(?:doctype|entity)\b", decoded, re.IGNORECASE):
            return None, None
        document = ET.fromstring(content)
        values = {
            element.tag.rsplit("}", 1)[-1].casefold(): (element.text or "").strip()
            for element in document.iter()
        }
        title = values.get("title", "")
        year_text = values.get("year", "")
        if not title or not year_text:
            return None, None
        year = int(year_text)
        if year <= 0:
            return None, None
        return title, year
    except (ET.ParseError, OSError, RuntimeError, UnicodeError, ValueError):
        return None, None


def _decode_xml(content: bytes) -> str:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    return content.decode("utf-8-sig")


__all__ = ["BrowseEntry", "BrowseRequest", "BrowseResult", "MediaBrowser"]
