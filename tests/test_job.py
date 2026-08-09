from pathlib import Path
from threading import Event, Thread

import pytest

from cueweaver import publishing
from cueweaver.job import JobError, JobRunner, JobState

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
    runner = JobRunner(translator=translator)
    results = []

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
    runner = JobRunner(translator=provider)

    failed = runner.run(media, target_language="zh", source=source)

    assert failed.state is JobState.FAILED
    assert failed.lifecycle == (
        JobState.DISCOVERED,
        JobState.TRANSLATING,
        JobState.VALIDATING,
        JobState.PUBLISHING,
        JobState.FAILED,
    )
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
