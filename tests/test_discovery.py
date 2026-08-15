from pathlib import Path

import pytest

from cueweaver.application.discovery import DiscoverRequest, Discovery
from cueweaver.application.errors import ServiceError


class MediaProbeFixture:
    def __init__(self, streams: list[dict[str, object]]) -> None:
        self.streams = streams
        self.media_path: Path | None = None

    def probe_subtitle_streams(self, media_path: Path) -> list[dict[str, object]]:
        self.media_path = media_path
        return self.streams


def test_discovery_lists_local_and_embedded_subtitles(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    (tmp_path / "Movie.en.forced.srt").write_text("subtitles")
    (tmp_path / "Movie.zh-Hans.default.ass").write_text("subtitles")
    probe = MediaProbeFixture(
        [
            {
                "index": "3",
                "codec_name": "ass",
                "tags": {"language": "zhs", "title": "Chinese Simplified"},
                "disposition": {
                    "default": 1,
                    "forced": 1,
                    "hearing_impaired": 1,
                    "visual_impaired": 1,
                    "comment": 1,
                    "lyrics": 1,
                    "karaoke": 1,
                    "original": 1,
                    "dub": 1,
                    "clean_effects": 1,
                    "attached_pic": 1,
                },
            },
            {"index": 4, "codec_name": "hdmv_pgs_subtitle"},
            {"index": 5, "codec_name": "dvb_subtitle"},
            {"index": 6, "codec_name": "mov_text"},
        ]
    )

    result = Discovery(probe).discover(DiscoverRequest(media))

    assert probe.media_path == media
    assert [
        (
            candidate.kind,
            candidate.format,
            candidate.path,
            candidate.stream_index,
            candidate.tags,
            candidate.dispositions,
        )
        for candidate in result.candidates
    ] == [
        (
            "external",
            "srt",
            tmp_path / "Movie.en.forced.srt",
            None,
            {"language": "en", "title": ""},
            [],
        ),
        (
            "external",
            "ass",
            tmp_path / "Movie.zh-Hans.default.ass",
            None,
            {"language": "zh-Hans", "title": ""},
            [],
        ),
        (
            "embedded",
            "ass",
            None,
            3,
            {"language": "zhs", "title": "Chinese Simplified"},
            [
                "default",
                "forced",
                "hearing_impaired",
                "visual_impaired",
                "comment",
                "lyrics",
                "karaoke",
                "original",
                "dub",
                "clean_effects",
            ],
        ),
    ]
    assert [
        (candidate.stream_index, candidate.reason)
        for candidate in result.unsupported_candidates
    ] == [
        (4, "bitmap subtitle"),
        (5, "unsupported subtitle codec: dvb_subtitle"),
        (6, "unsupported subtitle codec: mov_text"),
    ]


def test_discovery_rejects_missing_media_before_probing(tmp_path):
    probe = MediaProbeFixture([])

    with pytest.raises(ServiceError, match="Media does not exist"):
        Discovery(probe).discover(DiscoverRequest(tmp_path / "missing.mkv"))

    assert probe.media_path is None
