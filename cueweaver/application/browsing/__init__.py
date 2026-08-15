"""Safe one-directory-at-a-time browsing of the configured Media root."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from xml.parsers import expat

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
    season: int | None = None
    episode: int | None = None


@dataclass(frozen=True)
class BrowseResult:
    path: Path
    entries: list[BrowseEntry] = field(default_factory=list)


@dataclass(frozen=True)
class _NfoMetadata:
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None


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
            metadata = _read_nfo(child / "tvshow.nfo", self._media_root, "tvshow")
            return BrowseEntry(
                child.name,
                relative,
                "directory",
                metadata.title if metadata is not None else None,
                metadata.year if metadata is not None else None,
            )
        if not resolved.is_file() or child.suffix.casefold() not in MEDIA_EXTENSIONS:
            return None
        metadata = _media_nfo(child, self._media_root)
        return BrowseEntry(
            child.name,
            relative,
            "media",
            metadata.title if metadata is not None else None,
            metadata.year if metadata is not None else None,
            metadata.season if metadata is not None else None,
            metadata.episode if metadata is not None else None,
        )

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
    return Path() if not str(requested) else requested


def _natural_key(name: str) -> tuple[tuple[int, str | int], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", name)
        if part
    )


def _media_nfo(media: Path, root: Path) -> _NfoMetadata | None:
    candidates = (
        () if media.stem.casefold() == "tvshow" else (media.with_suffix(".nfo"),)
    )
    for candidate in (*candidates, media.parent / "movie.nfo"):
        metadata = _read_nfo(candidate, root, "media")
        if metadata is not None:
            return metadata
    return None


def _read_nfo(  # noqa: PLR0911
    path: Path,
    root: Path,
    expected_kind: Literal["media", "tvshow"],
) -> _NfoMetadata | None:
    try:
        if not path.resolve().is_relative_to(root):
            return None
        if not path.is_file() or path.stat().st_size > MAX_NFO_BYTES:
            return None
        content = path.read_bytes()
        if len(content) > MAX_NFO_BYTES:
            return None
        _reject_unsafe_xml(content)
        document = ET.fromstring(content)
        values = {
            element.tag.rsplit("}", 1)[-1].casefold(): (element.text or "").strip()
            for element in document.iter()
        }
        kind = document.tag.rsplit("}", 1)[-1].casefold()
        if expected_kind == "tvshow" and kind != "tvshow":
            return None
        if expected_kind == "media" and kind not in {"movie", "episodedetails"}:
            return None

        title = values.get("title", "")
        if not title:
            return None
        if kind == "episodedetails":
            season_text = values.get("season")
            episode_text = values.get("episode")
            season = int(season_text) if season_text else None
            episode = int(episode_text) if episode_text else None
            if (season is not None and season < 0) or (
                episode is not None and episode < 0
            ):
                return None
            return _NfoMetadata(title, season=season, episode=episode)

        year = _nfo_year(values.get("year"))
        if year is None:
            dates = [
                (value, parsed_year)
                for key in ("premiered", "aired")
                if (value := values.get(key))
                and (parsed_year := _nfo_year(value)) is not None
            ]
            if not dates:
                return None
            year = max(dates)[1]
        if year < 1:
            return None
        return _NfoMetadata(title, year=year)
    except (
        ET.ParseError,
        expat.ExpatError,
        LookupError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        return None


def _nfo_year(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.match(r"^(\d{4})", value.strip())
    return int(match.group(1)) if match is not None else None


def _reject_unsafe_xml(content: bytes) -> None:
    parser = expat.ParserCreate()

    def reject(*_args: object) -> None:
        raise ValueError("unsafe XML")

    parser.StartDoctypeDeclHandler = cast(Any, reject)
    parser.EntityDeclHandler = cast(Any, reject)
    parser.ExternalEntityRefHandler = cast(Any, reject)
    parser.Parse(content, True)


__all__ = ["BrowseEntry", "BrowseRequest", "BrowseResult", "MediaBrowser"]
