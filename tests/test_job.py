import json
from pathlib import Path
from threading import Event, Thread

import pytest

from cueweaver import publishing
from cueweaver.job import (
    JobCanceled,
    JobError,
    JobRunner,
    JobState,
    SeconvExtractor,
    SourceSelectionMode,
    SubtitleCandidate,
    SubtitleFormat,
    SubtitleSubtype,
    discover_subtitles,
)

SRT = """1
00:00:01,000 --> 00:00:02,000
Hello
"""
ASS_TEMPLATE = """[Script Info]
Title: Test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{text}
"""
VTT_SHORT_SOURCE = """WEBVTT

00:01.000 --> 00:02.000
Hello
"""
VTT_NORMALIZED_TRANSLATION = """WEBVTT

00:00:01.000 --> 00:00:02.000
你好
"""


class TranslatorMustNotBeCalled:
    def translate(self, source: Path, target_language: str) -> bytes:
        raise AssertionError("the translator must not be called for a no-op Job")


class ProviderContractFixture:
    def __init__(self, translated: str):
        self.translated = translated
        self.calls: list[tuple[Path, str]] = []

    def translate(self, source: Path, target_language: str) -> str:
        self.calls.append((source, target_language))
        return self.translated


class ExtractionFixture:
    def __init__(self, content: str = SRT):
        self.content = content
        self.calls: list[tuple[Path, int | None, Path]] = []

    def extract(self, media: Path, candidate, destination: Path) -> Path:
        self.calls.append((media, candidate.container_index, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.content, encoding="utf-8")
        return destination


class BlockingCancellableTranslator:
    def __init__(self, intermediate_path: Path, translated: str):
        self.intermediate_path = intermediate_path
        self.translated = translated
        self.started = Event()
        self.released = Event()
        self.cancelled = False

    def translate(self, source: Path, target_language: str) -> str:
        self.started.set()
        assert self.released.wait(timeout=5)
        self.intermediate_path.parent.mkdir(parents=True, exist_ok=True)
        self.intermediate_path.write_text(self.translated, encoding="utf-8")
        return self.translated

    def cancel(self) -> None:
        self.cancelled = True
        self.released.set()


def create_media_and_source(tmp_path, source_content=SRT):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(source_content, encoding="utf-8")
    return media, source


def ffprobe_subtitle_streams(monkeypatch, streams):
    def run(command, **kwargs):
        if "-select_streams" not in command:
            return type(
                "CompletedProcessFixture",
                (),
                {"stdout": json.dumps({"streams": []})},
            )()
        assert command[:5] == [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "s",
        ]
        return type(
            "CompletedProcessFixture",
            (),
            {"stdout": json.dumps({"streams": streams})},
        )()

    monkeypatch.setattr("cueweaver.job.subprocess.run", run)


def test_discovery_lists_external_text_and_embedded_bitmap_candidates(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container metadata is not subtitle content")
    (tmp_path / "Movie.en.srt").write_text(SRT, encoding="utf-8")
    ffprobe_subtitle_streams(
        monkeypatch,
        [
            {
                "index": 2,
                "codec_name": "subrip",
                "tags": {"language": "eng", "title": "English"},
            },
            {
                "index": 3,
                "codec_name": "hdmv_pgs_subtitle",
                "tags": {"language": "zho", "title": "Chinese signs"},
            },
        ],
    )

    candidates = discover_subtitles(media)

    assert [candidate.subtype for candidate in candidates] == [
        SubtitleSubtype.EXTERNAL,
        SubtitleSubtype.EMBEDDED,
        SubtitleSubtype.BITMAP,
    ]
    assert [candidate.io_cost for candidate in candidates] == [0, 1, 2]
    assert candidates[1].language == "en"
    assert candidates[2].language == "zh"
    assert candidates[1].container_index == 2
    assert candidates[2].selectable is False


def test_embedded_source_is_extracted_only_after_explicit_selection_and_cached(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    ffprobe_subtitle_streams(
        monkeypatch,
        [
            {
                "index": 1,
                "codec_name": "subrip",
                "tags": {"language": "eng"},
            }
        ],
    )
    extractor = ExtractionFixture()
    translated = SRT.replace("Hello", "你好")
    provider = ProviderContractFixture(translated)
    candidate = discover_subtitles(media)[0]
    states = []
    selections = []

    first = JobRunner(
        translator=provider,
        extractor=extractor,
        progress_observer=states.append,
        selection_observer=selections.append,
    ).run(
        media,
        target_language="zh",
        source=candidate,
    )
    second = JobRunner(translator=provider, extractor=extractor).run(
        media,
        target_language="zh",
        source=candidate,
    )

    assert first.state is JobState.PUBLISHED
    assert first.lifecycle == (
        JobState.DISCOVERED,
        JobState.EXTRACTING,
        JobState.TRANSLATING,
        JobState.VALIDATING,
        JobState.PUBLISHING,
        JobState.PUBLISHED,
    )
    assert states == list(first.lifecycle)
    assert len(selections) == 1
    assert selections[0].mode is SourceSelectionMode.EXPLICIT
    assert selections[0].candidate.subtype is SubtitleSubtype.EMBEDDED
    assert selections[0].reason is None
    assert first.source is not None
    assert first.source.subtype is SubtitleSubtype.EMBEDDED
    assert first.source.path == extractor.calls[0][2]
    assert provider.calls == [
        (first.source.path, "zh"),
        (second.source.path, "zh"),
    ]
    assert len(extractor.calls) == 1
    assert second.state is JobState.PUBLISHED
    assert second.published_path is not None
    assert second.published_path.read_text(encoding="utf-8") == translated


def test_job_work_files_do_not_pollute_the_media_directory(tmp_path, monkeypatch):
    media_directory = tmp_path / "media"
    cache_home = tmp_path / "cache"
    work_directory = cache_home / "cueweaver" / "jobs"
    media_directory.mkdir()
    media = media_directory / "Movie.mkv"
    media.write_bytes(b"container")
    monkeypatch.delenv("CUEWEAVER_WORK_DIRECTORY", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    ffprobe_subtitle_streams(
        monkeypatch,
        [{"index": 1, "codec_name": "subrip", "tags": {"language": "eng"}}],
    )
    extractor = ExtractionFixture()
    translated = SRT.replace("Hello", "你好")

    result = JobRunner(
        translator=ProviderContractFixture(translated),
        extractor=extractor,
    ).run(
        media,
        target_language="zh",
        source=discover_subtitles(media)[0],
    )

    assert result.state is JobState.PUBLISHED
    assert extractor.calls[0][2].is_relative_to(work_directory)
    assert not (media_directory / ".cueweaver").exists()
    assert sorted(path.name for path in media_directory.iterdir()) == [
        "Movie.mkv",
        "Movie.zh.srt",
    ]


def test_embedded_source_without_confirmation_does_not_extract(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    ffprobe_subtitle_streams(
        monkeypatch,
        [{"index": 1, "codec_name": "subrip", "tags": {"language": "eng"}}],
    )
    extractor = ExtractionFixture()

    result = JobRunner(extractor=extractor).run(media, target_language="zh")

    assert result.state is JobState.FAILED
    assert "Explicit Source selection" in (result.error or "")
    assert extractor.calls == []


def test_bitmap_only_media_fails_with_no_eligible_source(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mp4"
    media.write_bytes(b"container")
    ffprobe_subtitle_streams(
        monkeypatch,
        [
            {
                "index": 1,
                "codec_name": "dvd_subtitle",
                "tags": {"language": "eng"},
            }
        ],
    )

    result = JobRunner().run(media, target_language="zh")

    assert result.state is JobState.FAILED
    assert result.error is not None
    assert "No eligible Source" in result.error
    assert "Available Sources" in result.error
    assert "Movie.mp4" in result.error


def test_media_primary_language_breaks_external_ties_without_configuration(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    english = tmp_path / "Movie.en.srt"
    japanese = tmp_path / "Movie.ja.srt"
    media.write_bytes(b"media")
    english.write_text(SRT, encoding="utf-8")
    japanese.write_text(SRT, encoding="utf-8")

    def run(command, **kwargs):
        if "-select_streams" in command:
            streams = []
        else:
            streams = [
                {
                    "codec_type": "audio",
                    "tags": {"language": "eng"},
                    "disposition": {"default": 0},
                },
                {
                    "codec_type": "audio",
                    "tags": {"language": "jpn"},
                    "disposition": {"default": 1},
                },
            ]
        return type(
            "CompletedProcessFixture",
            (),
            {"stdout": json.dumps({"streams": streams})},
        )()

    monkeypatch.setattr("cueweaver.job.subprocess.run", run)
    provider = ProviderContractFixture(SRT.replace("Hello", "こんにちは"))

    result = JobRunner(translator=provider).run(media, target_language="zh")

    assert result.state is JobState.PUBLISHED
    assert result.source is not None
    assert result.source.path == japanese


def test_unknown_embedded_codec_is_not_an_eligible_source(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mp4"
    media.write_bytes(b"container")
    ffprobe_subtitle_streams(
        monkeypatch,
        [{"index": 1, "codec_name": "teletext", "tags": {"language": "eng"}}],
    )

    result = JobRunner().run(media, target_language="zh")

    assert result.state is JobState.FAILED
    assert result.error is not None
    assert "No eligible Source" in result.error


def test_embedded_discovery_failure_is_explicit_without_an_external_source(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")

    def fail_probe(*_args, **_kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("cueweaver.job.subprocess.run", fail_probe)

    result = JobRunner().run(media, target_language="zh")

    assert result.state is JobState.FAILED
    assert result.error is not None
    assert "ffprobe" in result.error


def test_external_source_survives_missing_ffprobe(tmp_path, monkeypatch):
    media, source = create_media_and_source(tmp_path)
    provider = ProviderContractFixture(SRT.replace("Hello", "你好"))

    def fail_probe(*_args, **_kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("cueweaver.job.subprocess.run", fail_probe)

    result = JobRunner(translator=provider).run(
        media,
        target_language="zh",
        source=source,
    )

    assert result.state is JobState.PUBLISHED
    assert result.lifecycle == (
        JobState.DISCOVERED,
        JobState.TRANSLATING,
        JobState.VALIDATING,
        JobState.PUBLISHING,
        JobState.PUBLISHED,
    )
    assert provider.calls == [(source, "zh")]
    assert result.published_path is not None
    assert result.published_path.read_text(encoding="utf-8") == SRT.replace(
        "Hello", "你好"
    )


def test_bitmap_only_failure_reports_completed_discovery(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mp4"
    media.write_bytes(b"container")
    ffprobe_subtitle_streams(
        monkeypatch,
        [{"index": 1, "codec_name": "dvd_subtitle", "tags": {"language": "eng"}}],
    )

    result = JobRunner().run(media, target_language="zh")

    assert result.state is JobState.FAILED
    assert result.lifecycle == (JobState.DISCOVERED, JobState.FAILED)
    assert "No eligible Source" in (result.error or "")


@pytest.mark.parametrize(
    ("provider", "missing_settings", "expected_error"),
    [
        (
            "DeepSeek",
            ("CUEWEAVER_TRANSLATION_API_KEY", "DEEPSEEK_API_KEY"),
            "DeepSeek API key is required",
        ),
        (
            "openai-compatible",
            (
                "CUEWEAVER_TRANSLATION_SERVER_ADDRESS",
                "CUSTOM_SERVER_ADDRESS",
                "CUEWEAVER_TRANSLATION_ENDPOINT",
                "CUSTOM_ENDPOINT",
            ),
            "Custom Server address is required",
        ),
    ],
    ids=["deepseek", "custom-server"],
)
def test_missing_provider_configuration_fails_before_translation(
    tmp_path, monkeypatch, provider, missing_settings, expected_error
):
    media, source = create_media_and_source(tmp_path)
    monkeypatch.setenv("CUEWEAVER_TRANSLATION_PROVIDER", provider)
    for name in missing_settings:
        monkeypatch.delenv(name, raising=False)

    result = JobRunner().run(media, target_language="zh", source=source)

    assert result.state is JobState.FAILED
    assert result.lifecycle == (JobState.DISCOVERED, JobState.FAILED)
    assert result.error is not None
    assert expected_error in result.error


def test_external_source_without_language_signal_requires_confirmation(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")

    result = JobRunner().run(media, target_language="zh")

    assert result.state is JobState.FAILED
    assert "Explicit Source selection" in (result.error or "")


def test_language_unknown_external_is_not_auto_selected_over_embedded_source(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.srt"
    media.write_bytes(b"container")
    source.write_text(SRT, encoding="utf-8")
    ffprobe_subtitle_streams(
        monkeypatch,
        [{"index": 1, "codec_name": "subrip", "tags": {"language": "eng"}}],
    )

    result = JobRunner().run(media, target_language="zh")

    assert result.state is JobState.FAILED
    assert "Explicit Source selection" in (result.error or "")


def test_language_priority_breaks_an_external_source_tie(tmp_path):
    media = tmp_path / "Movie.mkv"
    first_source = tmp_path / "Movie.en.srt"
    preferred_source = tmp_path / "Movie.ja.srt"
    media.write_bytes(b"media")
    first_source.write_text(SRT, encoding="utf-8")
    preferred_source.write_text(SRT, encoding="utf-8")
    provider = ProviderContractFixture(SRT.replace("Hello", "こんにちは"))
    selections = []

    result = JobRunner(
        translator=provider,
        language_priority=("ja", "en"),
        selection_observer=selections.append,
    ).run(media, target_language="zh")

    assert result.state is JobState.PUBLISHED
    assert result.source is not None
    assert result.source.path == preferred_source
    assert provider.calls == [(preferred_source, "zh")]
    assert selections[0].reason == "configured language priority: ja"


def test_automatic_selection_reports_reason_and_lifecycle_progress(tmp_path):
    media, source = create_media_and_source(tmp_path)
    provider = ProviderContractFixture(SRT.replace("Hello", "你好"))
    states = []
    selections = []

    result = JobRunner(
        translator=provider,
        progress_observer=states.append,
        selection_observer=selections.append,
    ).run(media, target_language="zh")

    assert result.state is JobState.PUBLISHED
    assert states == [
        JobState.DISCOVERED,
        JobState.TRANSLATING,
        JobState.VALIDATING,
        JobState.PUBLISHING,
        JobState.PUBLISHED,
    ]
    assert len(selections) == 1
    assert selections[0].mode is SourceSelectionMode.AUTOMATIC
    assert selections[0].candidate.path == source
    assert selections[0].reason == "only eligible Source"


def test_observer_failures_do_not_change_job_result(tmp_path):
    media, _source = create_media_and_source(tmp_path)
    provider = ProviderContractFixture(SRT.replace("Hello", "你好"))

    def fail_observer(_event):
        raise RuntimeError("terminal is unavailable")

    result = JobRunner(
        translator=provider,
        progress_observer=fail_observer,
        selection_observer=fail_observer,
    ).run(media, target_language="zh")

    assert result.state is JobState.PUBLISHED


def test_cancel_during_published_notification_keeps_published_result(tmp_path):
    media, _source = create_media_and_source(tmp_path)
    provider = ProviderContractFixture(SRT.replace("Hello", "你好"))

    def cancel_on_published(state):
        if state is JobState.PUBLISHED:
            raise JobCanceled("Job canceled")

    result = JobRunner(
        translator=provider,
        progress_observer=cancel_on_published,
    ).run(media, target_language="zh")

    assert result.state is JobState.PUBLISHED
    assert result.published_path is not None


def test_ambiguous_sources_use_one_explicit_selection_callback(tmp_path):
    media = tmp_path / "Movie.mkv"
    english = tmp_path / "Movie.en.srt"
    french = tmp_path / "Movie.fr.srt"
    media.write_bytes(b"media")
    english.write_text(SRT, encoding="utf-8")
    french.write_text(SRT, encoding="utf-8")
    provider = ProviderContractFixture(SRT.replace("Hello", "Bonjour"))
    selections = []

    def select(candidates):
        selections.append(candidates)
        return candidates[1]

    result = JobRunner(
        translator=provider,
        source_selector=select,
    ).run(media, target_language="zh")

    assert result.state is JobState.PUBLISHED
    assert len(selections) == 1
    assert result.source is not None
    assert result.source.path == french


def test_same_cost_sources_with_different_languages_require_selection(
    tmp_path,
):
    media = tmp_path / "Movie.mkv"
    english = tmp_path / "Movie.en.srt"
    french = tmp_path / "Movie.fr.ass"
    media.write_bytes(b"media")
    english.write_text(SRT, encoding="utf-8")
    french.write_text(ASS_TEMPLATE.format(text="Bonjour"), encoding="utf-8")
    selections = []

    def select(candidates):
        selections.append(candidates)
        return candidates[1]

    result = JobRunner(
        translator=ProviderContractFixture(ASS_TEMPLATE.format(text="你好")),
        source_selector=select,
    ).run(media, target_language="zh")

    assert result.state is JobState.PUBLISHED
    assert len(selections) == 1
    assert result.source is not None
    assert result.source.path == french


def test_seconv_extractor_materializes_the_confirmed_embedded_source(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    candidate = SubtitleCandidate(
        path=media,
        subtitle_format=SubtitleFormat.SRT,
        language="en",
        subtype=SubtitleSubtype.EMBEDDED,
        container_index=4,
    )
    destination = tmp_path / ".cueweaver" / "extraction" / "Movie.srt"

    def run(command, **kwargs):
        assert command[:4] == ["seconv", str(media), "srt", "--track-number:4"]
        output_directory = Path(command[-1])
        output_directory.mkdir(parents=True, exist_ok=True)
        (output_directory / "Movie.eng.srt").write_text(SRT, encoding="utf-8")
        return object()

    monkeypatch.setattr("cueweaver.job.subprocess.run", run)

    extracted = SeconvExtractor().extract(media, candidate, destination)

    assert extracted == destination
    assert destination.read_text(encoding="utf-8") == SRT


def test_non_target_external_source_is_translated_and_published(tmp_path):
    media, source = create_media_and_source(tmp_path)
    translated = """1
00:00:01,000 --> 00:00:02,000
你好
"""
    provider = ProviderContractFixture(translated)

    result = JobRunner(translator=provider).run(
        media,
        target_language="zh",
    )

    published = tmp_path / "Movie.zh.srt"
    assert result.state is JobState.PUBLISHED
    assert result.no_op is False
    assert provider.calls == [(source, "zh")]
    assert result.lifecycle == (
        JobState.DISCOVERED,
        JobState.TRANSLATING,
        JobState.VALIDATING,
        JobState.PUBLISHING,
        JobState.PUBLISHED,
    )
    assert published.read_text(encoding="utf-8") == translated


def test_cancel_is_terminal_retains_intermediate_result_and_does_not_publish(
    tmp_path,
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    intermediate = tmp_path / ".cueweaver" / "Movie.zh.partial.srt"
    translated = """1
00:00:01,000 --> 00:00:02,000
你好
"""
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    translator = BlockingCancellableTranslator(intermediate, translated)
    results = []
    states = []
    runner = JobRunner(translator=translator, progress_observer=states.append)

    thread = Thread(
        target=lambda: results.append(
            runner.run(media, target_language="zh", source=source)
        )
    )
    thread.start()
    assert translator.started.wait(timeout=5)

    runner.cancel()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert translator.cancelled is True
    assert len(results) == 1
    result = results[0]
    assert result.state is JobState.CANCELED
    assert result.lifecycle == (
        JobState.DISCOVERED,
        JobState.TRANSLATING,
        JobState.CANCELED,
    )
    assert states == list(result.lifecycle)
    assert result.published_path is None
    assert result.intermediate_path == intermediate
    assert intermediate.read_text(encoding="utf-8") == translated
    assert not (tmp_path / "Movie.zh.srt").exists()
    with pytest.raises(JobError, match="Explicit confirmation"):
        runner.publish_intermediate(result)
    assert runner.publish_intermediate(result, confirmed=True) == (
        tmp_path / "Movie.zh.srt"
    )
    assert (tmp_path / "Movie.zh.srt").read_text(encoding="utf-8") == translated


def test_fresh_job_after_cancellation_can_publish_a_complete_result(tmp_path):
    media, source = create_media_and_source(tmp_path)
    translated = """1
00:00:01,000 --> 00:00:02,000
你好
"""
    canceled_translator = BlockingCancellableTranslator(
        tmp_path / ".cueweaver" / "Movie.zh.partial.srt", translated
    )
    canceled_runner = JobRunner(translator=canceled_translator)
    results = []
    thread = Thread(
        target=lambda: results.append(
            canceled_runner.run(media, target_language="zh", source=source)
        )
    )
    thread.start()
    assert canceled_translator.started.wait(timeout=5)
    canceled_runner.cancel()
    thread.join(timeout=5)

    provider = ProviderContractFixture(translated)
    result = JobRunner(translator=provider).run(
        media,
        target_language="zh",
        source=source,
    )

    assert results[0].state is JobState.CANCELED
    assert result.state is JobState.PUBLISHED
    assert result.published_path == tmp_path / "Movie.zh.srt"
    assert result.published_path.read_text(encoding="utf-8") == translated
    assert provider.calls == [(source, "zh")]


def test_non_target_source_uses_the_default_pysubtrans_adapter(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    provider = ProviderContractFixture(
        """1
00:00:01,000 --> 00:00:02,000
你好
"""
    )
    monkeypatch.setenv("CUEWEAVER_TRANSLATION_API_KEY", "fixture-key")

    monkeypatch.setattr(
        "cueweaver.translation.PySubtransTranslator.translate",
        provider.translate,
    )

    result = JobRunner().run(media, target_language="zh")

    assert result.state is JobState.PUBLISHED
    assert provider.calls == [(source, "zh")]


@pytest.mark.parametrize(
    ("suffix", "source_content", "translated_content"),
    [
        (
            "ass",
            ASS_TEMPLATE.format(text="Hello"),
            ASS_TEMPLATE.format(text="你好"),
        ),
        (
            "vtt",
            VTT_SHORT_SOURCE,
            VTT_NORMALIZED_TRANSLATION,
        ),
    ],
)
def test_non_target_source_preserves_supported_external_subtitle_format(
    tmp_path, suffix, source_content, translated_content
):
    media = tmp_path / "Movie.mp4"
    source = tmp_path / f"Movie.en.{suffix}"
    media.write_bytes(b"media")
    source.write_text(source_content, encoding="utf-8")
    provider = ProviderContractFixture(translated_content)

    result = JobRunner(translator=provider).run(
        media,
        target_language="zh",
    )

    assert result.state is JobState.PUBLISHED
    assert result.published_path == tmp_path / f"Movie.zh.{suffix}"
    assert result.published_path.read_text(encoding="utf-8") == translated_content
    assert provider.calls == [(source, "zh")]


def test_target_language_source_is_validated_and_published_without_translation(
    tmp_path,
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.zh.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")

    result = JobRunner(translator=TranslatorMustNotBeCalled()).run(
        media,
        target_language="zh",
    )

    published = tmp_path / "Movie.zh.srt"
    assert result.state is JobState.PUBLISHED
    assert result.no_op is True
    assert result.source.path == source
    assert result.published_path == published
    assert published.read_text(encoding="utf-8") == SRT
    assert result.lifecycle[-1] is JobState.PUBLISHED
    assert JobState.VALIDATING in result.lifecycle
    assert JobState.PUBLISHING in result.lifecycle


def test_missing_target_language_fails_before_discovery_or_translation(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    monkeypatch.delenv("CUEWEAVER_TARGET_LANGUAGE", raising=False)

    result = JobRunner(translator=TranslatorMustNotBeCalled()).run(media)

    assert result.state is JobState.FAILED
    assert result.error == (
        "Target language is required; set --target-language or "
        "CUEWEAVER_TARGET_LANGUAGE."
    )
    assert result.lifecycle == (JobState.FAILED,)
    assert not (tmp_path / "Movie.zh.srt").exists()


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (
            "ass",
            ASS_TEMPLATE.format(text="Hello"),
        ),
        (
            "vtt",
            """WEBVTT

00:00:01.000 --> 00:00:02.000
Hello
""",
        ),
    ],
)
def test_no_op_preserves_supported_external_subtitle_format(tmp_path, suffix, content):
    media = tmp_path / "Movie.mp4"
    source = tmp_path / f"Movie.zh.{suffix}"
    media.write_bytes(b"media")
    source.write_text(content, encoding="utf-8")

    result = JobRunner(translator=TranslatorMustNotBeCalled()).run(
        media,
        target_language="zh-CN",
    )

    assert result.state is JobState.PUBLISHED
    assert result.no_op is True
    assert result.published_path == tmp_path / f"Movie.zh-CN.{suffix}"
    assert result.published_path.read_text(encoding="utf-8") == content


class InvalidTranslator:
    def translate(self, source: Path, target_language: str) -> str:
        return """1
00:00:03,000 --> 00:00:04,000
Wrong timing
        """


class IncompleteTranslator:
    def translate(self, source: Path, target_language: str) -> str:
        return """1
00:00:01,000 --> 00:00:02,000
"""


class FailingTranslator:
    def translate(self, source: Path, target_language: str) -> str:
        raise RuntimeError("provider unavailable")


def test_provider_failure_is_visible_and_does_not_publish(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")

    result = JobRunner(translator=FailingTranslator()).run(
        media,
        target_language="zh",
    )

    assert result.state is JobState.FAILED
    assert result.error == "Translation failed: provider unavailable"
    assert result.lifecycle == (
        JobState.DISCOVERED,
        JobState.TRANSLATING,
        JobState.FAILED,
    )
    assert not (tmp_path / "Movie.zh.srt").exists()


def test_validation_failure_does_not_replace_existing_published_artifact(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    destination = tmp_path / "Movie.zh.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    destination.write_text("previous complete artifact", encoding="utf-8")

    result = JobRunner(translator=InvalidTranslator()).run(
        media,
        target_language="zh",
        source=source,
    )

    assert result.state is JobState.FAILED
    assert result.error == "SRT structure changed during translation"
    assert JobState.VALIDATING in result.lifecycle
    assert JobState.PUBLISHING not in result.lifecycle
    assert destination.read_text(encoding="utf-8") == "previous complete artifact"


def test_incomplete_translation_fails_before_creating_a_media_artifact(tmp_path):
    media, source = create_media_and_source(tmp_path)
    destination = tmp_path / "Movie.zh.srt"

    result = JobRunner(translator=IncompleteTranslator()).run(
        media,
        target_language="zh",
        source=source,
    )

    assert result.state is JobState.FAILED
    assert result.lifecycle == (
        JobState.DISCOVERED,
        JobState.TRANSLATING,
        JobState.VALIDATING,
        JobState.FAILED,
    )
    assert result.translated_content == (b"1\n00:00:01,000 --> 00:00:02,000\n")
    assert not destination.exists()


def test_job_publishing_replaces_an_existing_artifact_as_one_complete_write(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.zh.srt"
    destination = tmp_path / "Movie.zh-CN.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    destination.write_text("old artifact", encoding="utf-8")

    result = JobRunner(translator=TranslatorMustNotBeCalled()).run(
        media,
        target_language="zh-CN",
        source=source,
    )

    assert result.state is JobState.PUBLISHED
    assert destination.read_text(encoding="utf-8") == SRT
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_publishing_failure_is_recoverable_at_the_job_boundary(
    tmp_path, monkeypatch
):
    media, source = create_media_and_source(tmp_path)
    destination = tmp_path / "Movie.zh.srt"
    destination.write_text("previous complete artifact", encoding="utf-8")
    translated = """1
00:00:01,000 --> 00:00:02,000
你好
"""
    provider = ProviderContractFixture(translated)
    real_replace = publishing.os.replace
    replace_attempts = 0

    def fail_first_replace(temporary_path, final_path):
        nonlocal replace_attempts
        replace_attempts += 1
        if replace_attempts == 1:
            raise OSError("disk full")
        return real_replace(temporary_path, final_path)

    monkeypatch.setattr("cueweaver.publishing.os.replace", fail_first_replace)
    states = []
    runner = JobRunner(translator=provider, progress_observer=states.append)

    failed = runner.run(media, target_language="zh", source=source)

    assert failed.state is JobState.FAILED
    assert failed.lifecycle == (
        JobState.DISCOVERED,
        JobState.TRANSLATING,
        JobState.VALIDATING,
        JobState.PUBLISHING,
        JobState.FAILED,
    )
    assert states == list(failed.lifecycle)
    assert failed.intermediate_path is not None
    assert failed.intermediate_path.read_text(encoding="utf-8") == translated
    assert destination.read_text(encoding="utf-8") == "previous complete artifact"
    assert provider.calls == [(source, "zh")]

    retried = runner.retry_publishing(failed)

    assert retried.state is JobState.PUBLISHED
    assert retried.published_path == destination
    assert destination.read_text(encoding="utf-8") == translated
    assert provider.calls == [(source, "zh")]
    assert failed.intermediate_path.exists() is False
