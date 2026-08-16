"""Safe parsing of Kodi NFO metadata from already-read bytes."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Literal, cast
from xml.parsers import expat

MAX_NFO_BYTES = 1024 * 1024


@dataclass(frozen=True)
class NfoMetadata:
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None


def parse_nfo(  # noqa: PLR0911
    content: bytes, expected_kind: Literal["media", "tvshow"]
) -> NfoMetadata | None:
    """Parse one Kodi NFO document without accessing the filesystem."""
    try:
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
            return NfoMetadata(title, season=season, episode=episode)

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
        return NfoMetadata(title, year=year)
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


__all__ = ["MAX_NFO_BYTES", "NfoMetadata", "parse_nfo"]
