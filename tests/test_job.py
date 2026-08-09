from pathlib import Path

import pytest

from cueweaver.job import JobRunner, JobState

SRT = """1
00:00:01,000 --> 00:00:02,000
Hello
"""


class TranslatorMustNotBeCalled:
    def translate(self, source: Path, target_language: str) -> bytes:
        raise AssertionError("the translator must not be called for a no-op Job")


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
            """[Script Info]
Title: Test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello
""",
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
