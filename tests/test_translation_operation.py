from pathlib import Path

import pytest

from cueweaver.application.errors import ServiceError
from cueweaver.application.translation import TranslateRequest, Translation

SRT = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"


class TranslatorFixture:
    def __init__(self, content: bytes = SRT, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.request: dict[str, object] | None = None

    def translate(self, source: Path, target_language: str, **kwargs) -> bytes:
        self.request = {"source": source, "target_language": target_language, **kwargs}
        if self.error is not None:
            raise self.error
        return self.content


class OutputFixture:
    def __init__(self) -> None:
        self.output_path: Path | None = None

    def publish(self, output_path: Path, write, *, overwrite: bool = False) -> None:
        self.output_path = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write(output_path)


class ServiceErrorOutputFixture:
    def publish(self, _output_path: Path, _write, *, overwrite: bool = False) -> None:
        raise ServiceError(
            "output_exists", "Output path already exists", path="output.srt"
        )


def test_translation_passes_request_options_and_publishes_result(tmp_path):
    subtitle = tmp_path / "Movie.en.SRT"
    subtitle.write_bytes(SRT)
    terms = tmp_path / "terms.json"
    terms.write_text('{"Hello":"你好"}')
    translator = TranslatorFixture(SRT.replace(b"Hello", b"\xe4\xbd\xa0\xe5\xa5\xbd"))
    output = OutputFixture()
    request = TranslateRequest(
        subtitle,
        "zh-Hans-SG",
        tmp_path / "media" / "Movie.zh.srt",
        tmp_path / "work" / "job-123",
        terms,
        False,
        False,
    )

    result = Translation(translator, output).translate(request)

    assert result.format == "srt"
    assert output.output_path == request.output_path
    assert request.output_path.read_bytes() == translator.content
    assert translator.request == {
        "source": subtitle,
        "target_language": "zh-Hans-SG",
        "user_overrides": {"Hello": "你好"},
        "work_directory": request.work_directory,
        "dynamic_terminology_enabled": False,
        "subtitle_terminology_filter_enabled": False,
    }
    assert request.work_directory.is_dir()


@pytest.mark.parametrize(
    ("subtitle_name", "output_name", "expected_error_code"),
    [
        ("Movie.SRT", "output.srt", None),
        ("Movie.srt", "output.txt", "unsupported_subtitle_format"),
        ("Movie.txt", "output.txt", "unsupported_subtitle_format"),
        ("Movie.srt", "output.ass", "format_mismatch"),
    ],
)
def test_translation_enforces_subtitle_extension_contract(
    tmp_path, subtitle_name, output_name, expected_error_code
):
    subtitle = tmp_path / subtitle_name
    subtitle.write_bytes(b"\xff")
    translator = TranslatorFixture()

    if expected_error_code is None:
        Translation(translator, OutputFixture()).translate(
            TranslateRequest(subtitle, "zh", tmp_path / output_name, tmp_path / "work")
        )
        assert translator.request is not None
    else:
        with pytest.raises(ServiceError) as error:
            Translation(translator, OutputFixture()).translate(
                TranslateRequest(
                    subtitle, "zh", tmp_path / output_name, tmp_path / "work"
                )
            )
        assert error.value.error_code == expected_error_code
        assert translator.request is None


@pytest.mark.parametrize(
    ("term_map_content", "expected_code"),
    [
        (None, "subtitle_not_found"),
        ("[]", "invalid_term_map"),
        ('{"":"x"}', "invalid_term_map"),
        ("{", "invalid_term_map"),
        ('{"Hello":""}', "invalid_term_map"),
    ],
)
def test_translation_rejects_missing_subtitle_and_invalid_term_maps(
    tmp_path, term_map_content, expected_code
):
    subtitle = tmp_path / "Movie.srt"
    subtitle.write_bytes(SRT)
    terms = tmp_path / "terms.json"
    if term_map_content is not None:
        terms.write_text(term_map_content)
    request = TranslateRequest(
        subtitle if term_map_content is not None else tmp_path / "missing.srt",
        "zh",
        tmp_path / "output.srt",
        tmp_path / "work",
        terms if term_map_content is not None else None,
    )

    with pytest.raises(ServiceError) as error:
        Translation(TranslatorFixture(), OutputFixture()).translate(request)

    assert error.value.error_code == expected_code


def test_translation_maps_translator_failures_without_publishing(tmp_path):
    subtitle = tmp_path / "Movie.srt"
    subtitle.write_bytes(SRT)
    output = OutputFixture()

    with pytest.raises(ServiceError) as error:
        Translation(
            TranslatorFixture(error=RuntimeError("cannot parse")), output
        ).translate(
            TranslateRequest(subtitle, "zh", tmp_path / "output.srt", tmp_path / "work")
        )

    assert error.value.error_code == "translation_failed"
    assert output.output_path is None


def test_translation_preserves_structured_publisher_errors(tmp_path):
    subtitle = tmp_path / "Movie.srt"
    subtitle.write_bytes(SRT)

    with pytest.raises(ServiceError) as error:
        Translation(TranslatorFixture(), ServiceErrorOutputFixture()).translate(
            TranslateRequest(subtitle, "zh", tmp_path / "output.srt", tmp_path / "work")
        )

    assert error.value.error_code == "output_exists"
    assert error.value.context == {"path": "output.srt"}
