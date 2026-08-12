import json
import subprocess

import pytest

from cueweaver.adapters.media import FfmpegMediaAdapter
from cueweaver.application.errors import ServiceError


def test_media_adapter_probes_json_streams_and_builds_ffmpeg_command(
    monkeypatch, tmp_path
):
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return type(
                "Completed",
                (),
                {"stdout": json.dumps({"streams": [{"index": 3}, "ignored"]})},
            )()
        return type("Completed", (), {"stdout": ""})()

    monkeypatch.setattr("cueweaver.adapters.media.subprocess.run", run)
    media = tmp_path / "Movie.mkv"
    output = tmp_path / "Movie.srt"
    adapter = FfmpegMediaAdapter()

    assert adapter.probe_subtitle_streams(media) == [{"index": 3}]
    adapter.extract_subtitle(media, 3, output)

    assert commands == [
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_streams",
            "-of",
            "json",
            str(media),
        ],
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(media),
            "-map",
            "0:3",
            "-c:s",
            "copy",
            str(output),
        ],
    ]


@pytest.mark.parametrize("stdout", ["{", "[]", '{"streams": {}}'])
def test_media_adapter_maps_invalid_probe_data_to_service_error(
    monkeypatch, tmp_path, stdout
):
    monkeypatch.setattr(
        "cueweaver.adapters.media.subprocess.run",
        lambda *_args, **_kwargs: type("Completed", (), {"stdout": stdout})(),
    )

    with pytest.raises(ServiceError) as error:
        FfmpegMediaAdapter().probe_subtitle_streams(tmp_path / "Movie.mkv")

    assert error.value.error_code == "discovery_failed"


@pytest.mark.parametrize(
    ("method", "error_code"),
    [
        ("probe_subtitle_streams", "discovery_failed"),
        ("extract_subtitle", "extraction_failed"),
    ],
)
def test_media_adapter_maps_process_failures(monkeypatch, tmp_path, method, error_code):
    monkeypatch.setattr(
        "cueweaver.adapters.media.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "ffmpeg")
        ),
    )
    adapter = FfmpegMediaAdapter()

    with pytest.raises(ServiceError) as error:
        if method == "probe_subtitle_streams":
            adapter.probe_subtitle_streams(tmp_path / "Movie.mkv")
        else:
            adapter.extract_subtitle(tmp_path / "Movie.mkv", 3, tmp_path / "Movie.srt")

    assert error.value.error_code == error_code
