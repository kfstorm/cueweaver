import json
from types import SimpleNamespace

from cueweaver.cli import main
from cueweaver.job import JobResult, JobState, SubtitleCandidate, SubtitleFormat

SRT = """1
00:00:01,000 --> 00:00:02,000
Hello
"""


def test_terminal_flow_publishes_a_target_language_source(tmp_path, capsys):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.zh.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")

    exit_code = main(["run", str(media), "--target-language", "zh"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Job published" in captured.out
    assert "discovered -> validating -> publishing -> published" in captured.out
    assert captured.err == ""


def test_terminal_flow_reports_missing_target_language(tmp_path, monkeypatch, capsys):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    monkeypatch.delenv("CUEWEAVER_TARGET_LANGUAGE", raising=False)

    exit_code = main([str(media)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert (
        "Target language is required; set --target-language or "
        "CUEWEAVER_TARGET_LANGUAGE."
    ) in captured.err


def test_terminal_flow_accepts_the_global_target_language_environment_setting(
    tmp_path, monkeypatch, capsys
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.zh.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    monkeypatch.setenv("CUEWEAVER_TARGET_LANGUAGE", "zh")

    exit_code = main([str(media)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Job published" in captured.out
    assert captured.err == ""


def test_terminal_flow_reports_cancellation_without_asserting_a_published_path(
    tmp_path, capsys
):
    media = tmp_path / "Movie.mkv"
    intermediate = tmp_path / "Movie.zh.partial.srt"

    class CanceledRunner:
        def run(self, media, *, target_language, source, source_language):
            return JobResult(
                state=JobState.CANCELED,
                lifecycle=(
                    JobState.DISCOVERED,
                    JobState.TRANSLATING,
                    JobState.CANCELED,
                ),
                media=media,
                target_language=target_language,
                source=None,
                published_path=None,
                no_op=False,
                error="Job canceled",
                intermediate_path=intermediate,
            )

    exit_code = main(
        ["run", str(media), "--target-language", "zh"],
        runner=CanceledRunner(),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Job canceled: Job canceled" in captured.err
    assert "discovered -> translating -> canceled" in captured.err
    assert str(intermediate) in captured.err


def test_terminal_flow_prompts_for_ambiguous_sources_and_marks_bitmap_disabled(
    tmp_path, monkeypatch, capsys
):
    media = tmp_path / "Movie.mkv"
    english = tmp_path / "Movie.en.srt"
    french = tmp_path / "Movie.fr.srt"
    media.write_bytes(b"container")
    english.write_text(SRT, encoding="utf-8")
    french.write_text(SRT, encoding="utf-8")
    monkeypatch.setenv("CUEWEAVER_TRANSLATION_API_KEY", "fixture-key")
    monkeypatch.setattr(
        "cueweaver.job.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "index": 3,
                            "codec_name": "hdmv_pgs_subtitle",
                            "tags": {"language": "eng"},
                        }
                    ]
                }
            )
        ),
    )
    monkeypatch.setattr(
        "cueweaver.translation.PySubtransTranslator.translate",
        lambda _self, _source, _target_language: SRT.replace("Hello", "Bonjour"),
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    exit_code = main(["run", str(media), "--target-language", "zh"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Source selection required" in captured.out
    assert "I/O cost 0" in captured.out
    assert "disabled; needs Subtitle OCR" in captured.out
    assert "source: Movie.fr.srt" in captured.out


def test_terminal_flow_surfaces_metadata_degradation_for_a_published_baseline(
    tmp_path, capsys
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    published = tmp_path / "Movie.zh.srt"
    candidate = SubtitleCandidate(
        path=source,
        subtitle_format=SubtitleFormat.SRT,
        language="en",
    )

    class DegradedRunner:
        def run(self, media, *, target_language, source, source_language):
            return JobResult(
                state=JobState.PUBLISHED,
                lifecycle=(
                    JobState.DISCOVERED,
                    JobState.METADATA,
                    JobState.TRANSLATING,
                    JobState.VALIDATING,
                    JobState.PUBLISHING,
                    JobState.PUBLISHED,
                ),
                media=media,
                target_language=target_language,
                source=candidate,
                published_path=published,
                no_op=False,
                metadata_degradation="Metadata degraded: TMDb API key is missing",
            )

    exit_code = main(
        ["run", str(media), "--target-language", "zh"],
        runner=DegradedRunner(),
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Job published" in captured.out
    assert "metadata: degraded: Metadata degraded: TMDb API key is missing" in (
        captured.err
    )
