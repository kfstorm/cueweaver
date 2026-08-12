"""Shared Media facts used by subtitle operations."""

from pathlib import Path

from .errors import ServiceError


def require_readable_media(media_path: Path) -> None:
    if not media_path.is_file():
        raise ServiceError("media_not_found", "Media does not exist", path=media_path)
    try:
        with media_path.open("rb"):
            pass
    except OSError as error:
        raise ServiceError(
            "media_unreadable", "Media cannot be read", path=media_path
        ) from error


def stream_index(stream: dict[str, object]) -> int | None:
    index = stream.get("index")
    if isinstance(index, bool):
        return None
    if isinstance(index, int):
        return index
    if isinstance(index, str):
        try:
            return int(index)
        except ValueError:
            return None
    return None
