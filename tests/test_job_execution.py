from pathlib import Path

import pytest

from cueweaver.application.errors import ServiceError
from cueweaver.application.jobs.execution import (
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
