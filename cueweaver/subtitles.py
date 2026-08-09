"""Supported subtitle formats and structural Validation."""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path


class SubtitleFormat(str, Enum):
    SRT = "srt"
    ASS = "ass"
    VTT = "vtt"

    @property
    def extension(self) -> str:
        return f".{self.value}"

    @classmethod
    def from_path(cls, path: Path) -> SubtitleFormat:
        suffix = path.suffix.casefold()
        for subtitle_format in cls:
            if subtitle_format.extension == suffix:
                return subtitle_format
        raise UnsupportedSubtitleFormat(
            f"Unsupported subtitle format: {path.suffix or '<none>'}"
        )


class SubtitleValidationError(Exception):
    """Raised when a subtitle cannot pass structural Validation."""


class UnsupportedSubtitleFormat(SubtitleValidationError):
    """Raised for formats outside the v0.1 External subtitle scope."""


_SRT_TIMESTAMP = re.compile(
    r"^\s*(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(\d{2}:\d{2}:\d{2},\d{3})(?:\s+.*)?$"
)
_ASS_TIMESTAMP = re.compile(r"^\d+:\d{2}:\d{2}\.\d{2}$")
_VTT_TIMESTAMP = re.compile(
    r"^\s*((?:\d{2,}:)?\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"((?:\d{2,}:)?\d{2}:\d{2}\.\d{3})(?:\s+(.*))?$"
)


def validate_subtitle_pair(
    source_content: bytes,
    delivered_content: bytes,
    subtitle_format: SubtitleFormat,
) -> None:
    """Validate both documents and ensure their timed structure is unchanged."""

    source_text = _decode(source_content)
    delivered_text = _decode(delivered_content)
    source_structure = _parse(source_text, subtitle_format)
    delivered_structure = _parse(delivered_text, subtitle_format)
    if source_structure != delivered_structure:
        raise SubtitleValidationError(
            f"{subtitle_format.value.upper()} structure changed during translation"
        )


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SubtitleValidationError("Subtitle is not valid UTF-8") from error


def _normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _parse(text: str, subtitle_format: SubtitleFormat) -> tuple[tuple[str, ...], ...]:
    normalised = _normalise_newlines(text)
    if subtitle_format is SubtitleFormat.SRT:
        return _parse_srt(normalised)
    if subtitle_format is SubtitleFormat.ASS:
        return _parse_ass(normalised)
    if subtitle_format is SubtitleFormat.VTT:
        return _parse_vtt(normalised)
    raise UnsupportedSubtitleFormat(f"Unsupported subtitle format: {subtitle_format}")


def _blocks(text: str) -> list[list[str]]:
    stripped = text.strip()
    if not stripped:
        raise SubtitleValidationError("Subtitle is empty")
    return [block.split("\n") for block in re.split(r"\n[ \t]*\n", stripped)]


def _parse_srt(text: str) -> tuple[tuple[str, ...], ...]:
    structures: list[tuple[str, ...]] = []
    seen_indexes: set[int] = set()
    for block in _blocks(text):
        index_text = block[0].strip()
        if len(block) < 3 or re.fullmatch(r"[0-9]+", index_text) is None:
            raise SubtitleValidationError("Invalid SRT cue index")
        index = int(index_text)
        if index < 1 or index in seen_indexes:
            raise SubtitleValidationError("SRT cue indexes must be positive and unique")
        seen_indexes.add(index)
        timestamp = _SRT_TIMESTAMP.fullmatch(block[1])
        if timestamp is None:
            raise SubtitleValidationError("Invalid SRT cue timestamp")
        _validate_interval(
            _srt_milliseconds(timestamp[1]),
            _srt_milliseconds(timestamp[2]),
            "SRT cue timestamp",
        )
        if not any(line.strip() for line in block[2:]):
            raise SubtitleValidationError("SRT cue has no text")
        structures.append((index_text, timestamp[1], timestamp[2]))
    return tuple(structures)


def _parse_ass(text: str) -> tuple[tuple[str, ...], ...]:
    sections = {
        match.group(1).casefold()
        for line in text.split("\n")
        if (match := re.fullmatch(r"\s*\[([^\]]+)\]\s*", line))
    }
    if "events" not in sections:
        raise SubtitleValidationError("ASS subtitle has no [Events] section")

    structures: list[tuple[str, ...]] = []
    in_events = False
    event_format: tuple[str, ...] | None = None
    for line in text.split("\n"):
        section = re.fullmatch(r"\s*\[([^\]]+)\]\s*", line)
        if section is not None:
            in_events = section.group(1).casefold() == "events"
            continue
        if not in_events:
            continue

        stripped_line = line.lstrip()
        if stripped_line.casefold().startswith("format:"):
            if event_format is not None:
                raise SubtitleValidationError("ASS [Events] has duplicate Format rows")
            event_format = tuple(
                field.strip().casefold()
                for field in stripped_line.split(":", 1)[1].split(",")
            )
            if not {"start", "end", "text"}.issubset(event_format):
                raise SubtitleValidationError("ASS Events Format lacks required fields")
            continue
        if not stripped_line.casefold().startswith("dialogue:"):
            continue
        if event_format is None:
            raise SubtitleValidationError("ASS [Events] has no Format row")
        fields = (
            stripped_line.split(":", 1)[1].lstrip().split(",", len(event_format) - 1)
        )
        if len(fields) != len(event_format):
            raise SubtitleValidationError("ASS Dialogue row has too few fields")
        field_indexes = {name: index for index, name in enumerate(event_format)}
        start = fields[field_indexes["start"]].strip()
        end = fields[field_indexes["end"]].strip()
        if not _ASS_TIMESTAMP.fullmatch(start) or not _ASS_TIMESTAMP.fullmatch(end):
            raise SubtitleValidationError("Invalid ASS Dialogue timestamp")
        _validate_interval(
            _ass_milliseconds(start),
            _ass_milliseconds(end),
            "ASS Dialogue timestamp",
        )
        text_index = field_indexes["text"]
        if not fields[text_index].strip():
            raise SubtitleValidationError("ASS Dialogue row has no text")
        structures.append(
            tuple(
                field.strip()
                for index, field in enumerate(fields)
                if index != text_index
            )
        )
    if not structures:
        raise SubtitleValidationError("ASS subtitle has no Dialogue rows")
    assert event_format is not None
    return (("format", *event_format), *structures)


def _parse_vtt(text: str) -> tuple[tuple[str, ...], ...]:
    lines = text.split("\n")
    first_content = next((line.strip() for line in lines if line.strip()), "")
    if re.fullmatch(r"WEBVTT(?:\s.*)?", first_content) is None:
        raise SubtitleValidationError("VTT subtitle has no WEBVTT header")

    structures: list[tuple[str, ...]] = []
    for block in _blocks_after_header(text):
        if not block:
            continue
        first = block[0].strip()
        if re.fullmatch(r"(?:NOTE|STYLE|REGION)(?:\s.*)?", first) is not None:
            continue
        timestamp_index = 0
        timestamp = _VTT_TIMESTAMP.fullmatch(block[0])
        if timestamp is None and len(block) > 1:
            timestamp_index = 1
            timestamp = _VTT_TIMESTAMP.fullmatch(block[1])
        if timestamp is None:
            raise SubtitleValidationError("Invalid VTT cue timestamp")
        _validate_interval(
            _vtt_milliseconds(timestamp[1]),
            _vtt_milliseconds(timestamp[2]),
            "VTT cue timestamp",
        )
        if not any(line.strip() for line in block[timestamp_index + 1 :]):
            raise SubtitleValidationError("VTT cue has no text")
        cue_id = block[0].strip() if timestamp_index == 1 else ""
        settings = (timestamp[3] or "").strip()
        structures.append((cue_id, timestamp[1], timestamp[2], settings))
    if not structures:
        raise SubtitleValidationError("VTT subtitle has no cues")
    return tuple(structures)


def _blocks_after_header(text: str) -> list[list[str]]:
    blocks = _blocks(text)
    header_index = next(
        index
        for index, block in enumerate(blocks)
        if block and re.fullmatch(r"WEBVTT(?:\s.*)?", block[0].strip())
    )
    return blocks[header_index + 1 :]


def _validate_interval(start: int, end: int, label: str) -> None:
    if end <= start:
        raise SubtitleValidationError(f"{label} must have a positive duration")


def _srt_milliseconds(timestamp: str) -> int:
    hours, minutes, seconds_milliseconds = timestamp.split(":")
    seconds, milliseconds = seconds_milliseconds.split(",")
    if int(minutes) > 59 or int(seconds) > 59:
        raise SubtitleValidationError("Invalid SRT cue timestamp")
    return (int(hours) * 60 * 60 + int(minutes) * 60 + int(seconds)) * 1000 + int(
        milliseconds
    )


def _ass_milliseconds(timestamp: str) -> int:
    hours, minutes, seconds_centiseconds = timestamp.split(":")
    seconds, centiseconds = seconds_centiseconds.split(".")
    if int(minutes) > 59 or int(seconds) > 59:
        raise SubtitleValidationError("Invalid ASS Dialogue timestamp")
    return (int(hours) * 60 * 60 + int(minutes) * 60 + int(seconds)) * 1000 + int(
        centiseconds
    ) * 10


def _vtt_milliseconds(timestamp: str) -> int:
    clock, milliseconds = timestamp.rsplit(".", 1)
    parts = [int(part) for part in clock.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        hours = 0
    else:
        hours, minutes, seconds = parts
    if minutes > 59 or seconds > 59:
        raise SubtitleValidationError("Invalid VTT cue timestamp")
    return (hours * 60 * 60 + minutes * 60 + seconds) * 1000 + int(milliseconds)
