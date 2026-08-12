"""Subtitle filename and media codec format mappings."""

from pathlib import Path

from .application.errors import ServiceError

EXTERNAL_FORMATS = {".srt": "srt", ".ass": "ass", ".vtt": "vtt"}
TEXT_CODEC_FORMATS = {
    "ass": "ass",
    "ssa": "ass",
    "subrip": "srt",
    "srt": "srt",
    "webvtt": "vtt",
    "mov_text": "srt",
    "text": "srt",
    "hdmv_text_subtitle": "srt",
    "substation_alpha": "ass",
}
EXTRACT_CODEC_FORMATS = {
    "ass": "ass",
    "ssa": "ass",
    "subrip": "srt",
    "srt": "srt",
    "webvtt": "vtt",
}
BITMAP_CODECS = frozenset({"dvd_subtitle", "hdmv_pgs_subtitle", "pgssub"})


def output_format(output_path: Path) -> str:
    subtitle_format = EXTERNAL_FORMATS.get(output_path.suffix.casefold())
    if subtitle_format is None:
        raise ServiceError(
            "unsupported_output_format",
            "Output path must use a supported subtitle extension",
            path=output_path,
        )
    return subtitle_format


def matching_format(subtitle_path: Path, output_path: Path) -> str:
    input_format = EXTERNAL_FORMATS.get(subtitle_path.suffix.casefold())
    output_format = EXTERNAL_FORMATS.get(output_path.suffix.casefold())
    if input_format is None or output_format is None:
        raise ServiceError(
            "unsupported_subtitle_format",
            "Subtitle paths must use supported extensions",
        )
    if input_format != output_format:
        raise ServiceError(
            "format_mismatch", "Input and output subtitle formats must match"
        )
    return input_format
