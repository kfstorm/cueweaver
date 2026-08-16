import hashlib
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from cueweaver.application.errors import ServiceError
from cueweaver.application.extraction import Extraction
from cueweaver.application.jobs.execution import (
    EmbeddedExecutionInput,
    JobExecution,
    JobExecutionInput,
)

SRT = b"1\n00:00:00,000 --> 00:00:01,000\nTranslated\n"


class TranslatorFixture:
    available = True

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.error = error

    def translate(self, source: Path, target_language: str, **kwargs: object) -> bytes:
        self.calls.append(
            {"source": source, "target_language": target_language, **kwargs}
        )
        if self.error is not None:
            raise self.error
        return SRT


class OutputFixture:
    def __init__(self) -> None:
        self.overwrite = False

    def publish(self, output_path: Path, write, *, overwrite: bool = False) -> None:
        self.overwrite = overwrite
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write(output_path)


def embedded_extractor() -> Mock:
    extractor = Mock()
    extractor.configure_mock(
        **{
            "probe_subtitle_streams.return_value": [
                {"index": 3, "codec_name": "subrip"}
            ],
            "extract_subtitle.side_effect": lambda _media, _stream, output: (
                output.write_bytes(SRT)
            ),
        }
    )
    return extractor


class OrderedTranslator(TranslatorFixture):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    def translate(self, source: Path, target_language: str, **kwargs: object) -> bytes:
        self.order.append("translation")
        return super().translate(source, target_language, **kwargs)


def embedded_input(
    media: Path,
    work_directory: Path,
    *,
    extraction_marker: dict[str, object] | None = None,
) -> JobExecutionInput:
    return JobExecutionInput(
        subtitle_path=None,
        target_language_code="zh-Hans",
        output_path=media.parent / "Media.zh-Hans.srt",
        work_directory=work_directory,
        embedded=EmbeddedExecutionInput(
            media,
            3,
            "srt",
            extraction_marker,
        ),
    )


def embedded_marker(digest: str) -> dict[str, object]:
    return {
        "status": "Completed",
        "path": "source.srt",
        "format": "srt",
        "content_digest": digest,
    }


def test_job_execution_runs_external_subtitle_without_a_worker(tmp_path: Path):
    subtitle = tmp_path / "Movie.en.srt"
    subtitle.write_bytes(SRT)
    output = tmp_path / "Movie.zh-Hans.srt"
    work_directory = tmp_path / "jobs" / "job-1"
    translator = TranslatorFixture()

    result = JobExecution(translator, OutputFixture()).execute(
        JobExecutionInput(
            subtitle_path=subtitle,
            target_language_code="zh-Hans",
            output_path=output,
            work_directory=work_directory,
        )
    )

    assert result.output_path == output
    assert result.target_language_code == "zh-Hans"
    assert result.format == "srt"
    assert output.read_bytes() == SRT
    assert translator.calls == [
        {
            "source": subtitle,
            "target_language": "zh-Hans",
            "user_overrides": {},
            "work_directory": work_directory,
            "dynamic_terminology_enabled": True,
            "subtitle_terminology_filter_enabled": True,
        }
    ]


def test_job_execution_extracts_embedded_subtitle_and_reports_progress(
    tmp_path: Path,
):
    media = tmp_path / "Media.mkv"
    media.write_bytes(b"media")
    work_directory = tmp_path / "jobs" / "job-1"
    extractor = embedded_extractor()
    events: list[str] = []
    translator = OrderedTranslator(events)
    progress = []

    result = JobExecution(
        translator,
        OutputFixture(),
        extraction=Extraction(extractor, OutputFixture()),
    ).execute(
        embedded_input(media, work_directory),
        on_progress=lambda event: (
            events.append("progress"),
            progress.append(event),
            True,
        )[-1],
    )

    assert result is not None
    assert events == ["progress", "translation"]
    assert progress[0].phase == "Translating"
    assert progress[0].reused is False
    assert progress[0].embedded_subtitle.path == work_directory / "source.srt"
    assert (
        progress[0].embedded_subtitle.content_digest == hashlib.sha256(SRT).hexdigest()
    )
    assert extractor.probe_subtitle_streams.call_args_list == [call(media)]
    assert extractor.extract_subtitle.call_args.args[:2] == (media, 3)
    assert translator.calls[0]["source"] == work_directory / "source.srt"


def test_job_execution_reuses_a_valid_embedded_source(tmp_path: Path):
    media = tmp_path / "Media.mkv"
    media.write_bytes(b"media")
    work_directory = tmp_path / "jobs" / "job-1"
    work_directory.mkdir(parents=True)
    source = work_directory / "source.srt"
    source.write_bytes(SRT)
    digest = hashlib.sha256(SRT).hexdigest()
    extractor = embedded_extractor()
    progress = []

    JobExecution(
        TranslatorFixture(),
        OutputFixture(),
        extraction=Extraction(extractor, OutputFixture()),
    ).execute(
        embedded_input(
            media,
            work_directory,
            extraction_marker=embedded_marker(digest),
        ),
        on_progress=lambda event: (progress.append(event), True)[-1],
    )

    assert progress[0].reused is True
    assert progress[0].embedded_subtitle.content_digest == digest
    assert extractor.probe_subtitle_streams.call_args_list == []
    assert extractor.extract_subtitle.call_args_list == []


def test_job_execution_reextracts_when_embedded_source_digest_mismatches(
    tmp_path: Path,
):
    media = tmp_path / "Media.mkv"
    media.write_bytes(b"media")
    work_directory = tmp_path / "jobs" / "job-1"
    work_directory.mkdir(parents=True)
    (work_directory / "source.srt").write_bytes(b"tampered")
    extractor = embedded_extractor()
    progress = []

    JobExecution(
        TranslatorFixture(),
        OutputFixture(),
        extraction=Extraction(extractor, OutputFixture()),
    ).execute(
        embedded_input(
            media,
            work_directory,
            extraction_marker=embedded_marker(hashlib.sha256(SRT).hexdigest()),
        ),
        on_progress=lambda event: (progress.append(event), True)[-1],
    )

    assert progress[0].reused is False
    assert extractor.probe_subtitle_streams.call_args_list == [call(media)]
    assert len(extractor.extract_subtitle.call_args_list) == 1


def test_job_execution_passes_term_map_and_translation_options(tmp_path: Path):
    subtitle = tmp_path / "Movie.en.srt"
    subtitle.write_bytes(SRT)
    output = OutputFixture()
    translator = TranslatorFixture()

    JobExecution(translator, output).execute(
        JobExecutionInput(
            subtitle_path=subtitle,
            target_language_code="zh-Hans",
            output_path=tmp_path / "Movie.zh-Hans.srt",
            work_directory=tmp_path / "jobs" / "job-1",
            term_map={"Captain": "队长"},
            dynamic_terminology_enabled=False,
            subtitle_terminology_filter_enabled=False,
            overwrite=True,
        )
    )

    assert output.overwrite is True
    assert translator.calls[0]["user_overrides"] == {"Captain": "队长"}
    assert translator.calls[0]["dynamic_terminology_enabled"] is False
    assert translator.calls[0]["subtitle_terminology_filter_enabled"] is False


def test_job_execution_preserves_translation_failure_as_service_error(
    tmp_path: Path,
):
    subtitle = tmp_path / "Movie.en.srt"
    subtitle.write_bytes(SRT)

    with pytest.raises(ServiceError) as error:
        JobExecution(
            TranslatorFixture(error=RuntimeError("boom")), OutputFixture()
        ).execute(
            JobExecutionInput(
                subtitle_path=subtitle,
                target_language_code="zh-Hans",
                output_path=tmp_path / "Movie.zh-Hans.srt",
                work_directory=tmp_path / "jobs" / "job-1",
            )
        )

    assert error.value.error_code == "translation_failed"
