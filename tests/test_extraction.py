from pathlib import Path

import pytest

from cueweaver.adapters.output import AtomicOutputPublisher
from cueweaver.application.errors import ServiceError
from cueweaver.application.extraction import Extraction, ExtractRequest


class MediaFixture:
    def __init__(self, streams: list[dict[str, object]]) -> None:
        self.streams = streams
        self.extraction: tuple[Path, int, Path] | None = None

    def probe_subtitle_streams(self, _media_path: Path) -> list[dict[str, object]]:
        return self.streams

    def extract_subtitle(
        self, media_path: Path, stream_index: int, output_path: Path
    ) -> None:
        self.extraction = (media_path, stream_index, output_path)
        output_path.write_text("subtitle")


class OutputFixture:
    def __init__(self) -> None:
        self.output_path: Path | None = None

    def publish(self, output_path: Path, write) -> None:
        self.output_path = output_path
        write(output_path)


class TemporaryOutputFixture(OutputFixture):
    def publish(self, output_path: Path, write) -> None:
        self.output_path = output_path
        temporary_path = output_path.parent / f".{output_path.name}.temporary"
        temporary_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text("already exists")
        write(temporary_path)
        temporary_path.replace(output_path)


@pytest.mark.parametrize(
    ("codec", "extension", "format"),
    [("subrip", "srt", "srt"), ("ssa", "ass", "ass"), ("webvtt", "vtt", "vtt")],
)
def test_extraction_publishes_matching_text_stream(tmp_path, codec, extension, format):
    media_path = tmp_path / "Movie.mkv"
    media_path.write_bytes(b"container")
    output_path = tmp_path / f"Movie.{extension}"
    media = MediaFixture([{"index": 3, "codec_name": codec}])
    output = OutputFixture()

    result = Extraction(media, output).extract(
        ExtractRequest(media_path, 3, output_path)
    )

    assert result.format == format
    assert output.output_path == output_path
    assert media.extraction == (media_path, 3, output_path)


def test_extraction_writes_to_a_precreated_temporary_output_path(tmp_path):
    media_path = tmp_path / "Movie.mkv"
    media_path.write_bytes(b"container")
    output_path = tmp_path / "Movie.srt"
    media = MediaFixture([{"index": 3, "codec_name": "subrip"}])

    Extraction(media, TemporaryOutputFixture()).extract(
        ExtractRequest(media_path, 3, output_path)
    )

    assert output_path.read_text() == "subtitle"


def test_extraction_never_overwrites_an_existing_output(tmp_path):
    media_path = tmp_path / "Movie.mkv"
    media_path.write_bytes(b"container")
    output_path = tmp_path / "Movie.srt"
    output_path.write_text("keep")
    media = MediaFixture([{"index": 3, "codec_name": "subrip"}])

    with pytest.raises(ServiceError) as error:
        Extraction(media, AtomicOutputPublisher()).extract(
            ExtractRequest(media_path, 3, output_path)
        )

    assert error.value.error_code == "output_exists"
    assert output_path.read_text() == "keep"


@pytest.mark.parametrize(
    ("stream", "output_name", "error_code"),
    [
        ([], "Movie.srt", "stream_not_found"),
        (
            [{"index": 3, "codec_name": "hdmv_pgs_subtitle"}],
            "Movie.srt",
            "unsupported_stream",
        ),
        ([{"index": 3, "codec_name": "mov_text"}], "Movie.srt", "unsupported_stream"),
        ([{"index": 3, "codec_name": "ass"}], "Movie.srt", "format_mismatch"),
        (
            [{"index": 3, "codec_name": "subrip"}],
            "Movie.txt",
            "unsupported_output_format",
        ),
    ],
)
def test_extraction_rejects_invalid_stream_or_output(
    tmp_path, stream, output_name, error_code
):
    media_path = tmp_path / "Movie.mkv"
    media_path.write_bytes(b"container")

    with pytest.raises(ServiceError) as error:
        Extraction(MediaFixture(stream), OutputFixture()).extract(
            ExtractRequest(media_path, 3, tmp_path / output_name)
        )

    assert error.value.error_code == error_code
