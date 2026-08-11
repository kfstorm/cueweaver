import json
import signal
from types import SimpleNamespace

import pytest

from cueweaver.cli import build_parser, main
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
    assert "[progress] discovered" in captured.err
    assert "Source selected (automatic): Movie.zh.srt" in captured.err
    assert "[progress] published" in captured.err


def test_terminal_flow_passes_debug_and_reports_trace_path(tmp_path, capsys):
    media = tmp_path / "Movie.mkv"
    trace = tmp_path / "trace.jsonl"
    candidate = SubtitleCandidate(
        path=tmp_path / "Movie.en.srt",
        subtitle_format=SubtitleFormat.SRT,
        language="en",
    )

    class DebugRunner:
        def run(self, media, *, target_language, source, source_language, debug):
            assert debug is True
            return JobResult(
                state=JobState.PUBLISHED,
                lifecycle=(JobState.DISCOVERED, JobState.PUBLISHED),
                media=media,
                target_language=target_language,
                source=candidate,
                published_path=tmp_path / "Movie.zh.srt",
                no_op=False,
                trace_path=trace,
            )

    exit_code = main(
        ["run", str(media), "--target-language", "zh", "--debug"],
        runner=DebugRunner(),
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"  trace: {trace}" in captured.out


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
    assert "[progress] discovered" in captured.err
    assert "Source selected (automatic): Movie.zh.srt" in captured.err


def test_dynamic_terminology_cli_switches_are_mutually_exclusive():
    parser = build_parser()

    assert (
        parser.parse_args(
            ["Movie.mkv", "--dynamic-terminology"]
        ).dynamic_terminology_enabled
        is True
    )
    assert (
        parser.parse_args(
            ["Movie.mkv", "--no-dynamic-terminology"]
        ).dynamic_terminology_enabled
        is False
    )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "Movie.mkv",
                "--dynamic-terminology",
                "--no-dynamic-terminology",
            ]
        )


def test_episode_terminology_filter_cli_switches_are_mutually_exclusive():
    parser = build_parser()

    assert (
        parser.parse_args(
            ["Movie.mkv", "--episode-terminology-filter"]
        ).episode_terminology_filter_enabled
        is True
    )
    assert (
        parser.parse_args(
            ["Movie.mkv", "--no-episode-terminology-filter"]
        ).episode_terminology_filter_enabled
        is False
    )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "Movie.mkv",
                "--episode-terminology-filter",
                "--no-episode-terminology-filter",
            ]
        )


def test_cli_passes_episode_terminology_filter_setting_to_the_job(tmp_path):
    media = tmp_path / "Movie.mkv"
    candidate = SubtitleCandidate(
        path=tmp_path / "Movie.en.srt",
        subtitle_format=SubtitleFormat.SRT,
        language="en",
    )

    class RecordingRunner:
        episode_terminology_filter_enabled = None

        def run(
            self,
            media,
            *,
            target_language,
            source,
            source_language,
            episode_terminology_filter_enabled,
        ):
            self.episode_terminology_filter_enabled = episode_terminology_filter_enabled
            return JobResult(
                state=JobState.PUBLISHED,
                lifecycle=(JobState.PUBLISHED,),
                media=media,
                target_language=target_language,
                source=candidate,
                published_path=media,
                no_op=True,
            )

    runner = RecordingRunner()
    assert (
        main(
            [
                str(media),
                "--target-language",
                "zh",
                "--no-episode-terminology-filter",
            ],
            runner=runner,
        )
        == 0
    )
    assert runner.episode_terminology_filter_enabled is False


def test_cli_passes_no_metadata_fetch_to_the_job(tmp_path):
    media = tmp_path / "Movie.mkv"
    candidate = SubtitleCandidate(
        path=tmp_path / "Movie.en.srt",
        subtitle_format=SubtitleFormat.SRT,
        language="en",
    )

    class RecordingRunner:
        no_metadata_fetch = None

        def run(
            self,
            media,
            *,
            target_language,
            source,
            source_language,
            no_metadata_fetch,
        ):
            self.no_metadata_fetch = no_metadata_fetch
            return JobResult(
                state=JobState.PUBLISHED,
                lifecycle=(JobState.PUBLISHED,),
                media=media,
                target_language=target_language,
                source=candidate,
                published_path=media,
                no_op=True,
            )

    runner = RecordingRunner()
    assert (
        main(
            [str(media), "--target-language", "zh", "--no-metadata-fetch"],
            runner=runner,
        )
        == 0
    )
    assert runner.no_metadata_fetch is True


@pytest.mark.parametrize(
    ("option", "environment_value", "expected"),
    [
        ("--dynamic-terminology", "false", True),
        ("--no-dynamic-terminology", "true", False),
    ],
)
def test_cli_passes_explicit_dynamic_terminology_setting_to_the_job(
    tmp_path, monkeypatch, option, environment_value, expected
):
    media = tmp_path / "Movie.mkv"
    monkeypatch.setenv("CUEWEAVER_DYNAMIC_TERMINOLOGY_MAP", environment_value)
    candidate = SubtitleCandidate(
        path=tmp_path / "Movie.en.srt",
        subtitle_format=SubtitleFormat.SRT,
        language="en",
    )

    class RecordingRunner:
        dynamic_terminology_enabled = None

        def run(
            self,
            media,
            *,
            target_language,
            source,
            source_language,
            dynamic_terminology_enabled,
        ):
            self.dynamic_terminology_enabled = dynamic_terminology_enabled
            return JobResult(
                state=JobState.PUBLISHED,
                lifecycle=(JobState.PUBLISHED,),
                media=media,
                target_language=target_language,
                source=candidate,
                published_path=media,
                no_op=True,
            )

    runner = RecordingRunner()
    assert (
        main(
            [
                str(media),
                "--target-language",
                "zh",
                option,
            ],
            runner=runner,
        )
        == 0
    )
    assert runner.dynamic_terminology_enabled is expected


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
    assert "Source selection required" in captured.err
    assert "I/O cost 0" in captured.err
    assert "disabled; needs Subtitle OCR" in captured.err
    assert "Source selected (interactive): Movie.fr.srt" in captured.err
    assert "source: Movie.fr.srt" in captured.out


def test_terminal_flow_reports_explicit_embedded_selection_before_extraction(
    tmp_path, monkeypatch, capsys
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"container")
    monkeypatch.setenv("CUEWEAVER_TRANSLATION_API_KEY", "fixture-key")
    monkeypatch.setattr(
        "cueweaver.job.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "index": 1,
                            "codec_name": "subrip",
                            "tags": {"language": "eng"},
                        }
                    ]
                }
            )
        ),
    )

    def extract(_self, _media, _candidate, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(SRT, encoding="utf-8")
        return destination

    monkeypatch.setattr("cueweaver.job.SeconvExtractor.extract", extract)
    monkeypatch.setattr(
        "cueweaver.translation.PySubtransTranslator.translate",
        lambda _self, _source, _target_language: SRT.replace("Hello", "你好"),
    )

    exit_code = main(
        [
            "run",
            str(media),
            "--target-language",
            "zh",
            "--source",
            "embedded:2",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Source selected (explicit): Movie.mkv" in captured.err
    assert "[embedded, I/O cost 1]" in captured.err
    assert captured.err.index("Source selected") < captured.err.index(
        "[progress] extracting"
    )


def test_terminal_flow_reports_selection_failure_with_candidates(
    tmp_path, monkeypatch, capsys
):
    media = tmp_path / "Movie.mp4"
    media.write_bytes(b"container")
    monkeypatch.setattr(
        "cueweaver.job.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "index": 1,
                            "codec_name": "hdmv_pgs_subtitle",
                            "tags": {"language": "eng"},
                        }
                    ]
                }
            )
        ),
    )

    exit_code = main(["run", str(media), "--target-language", "zh"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "[progress] discovered" in captured.err
    assert "[progress] failed" in captured.err
    assert "Available Sources" in captured.err
    assert "disabled; needs Subtitle OCR" in captured.err


def test_terminal_flow_reports_publishing_failure_progress(
    tmp_path, monkeypatch, capsys
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    monkeypatch.setenv("CUEWEAVER_TRANSLATION_API_KEY", "fixture-key")
    monkeypatch.setattr(
        "cueweaver.translation.PySubtransTranslator.translate",
        lambda _self, _source, _target_language: SRT.replace("Hello", "你好"),
    )

    def fail_replace(_temporary_path, _final_path):
        raise OSError("disk full")

    monkeypatch.setattr("cueweaver.publishing.os.replace", fail_replace)

    exit_code = main(["run", str(media), "--target-language", "zh"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "[progress] publishing" in captured.err
    assert "[progress] failed" in captured.err
    assert "Job failed: disk full" in captured.err


def test_terminal_flow_reports_sigint_cancellation_during_source_selection(
    tmp_path, monkeypatch, capsys
):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"media")
    (tmp_path / "Movie.en.srt").write_text(SRT, encoding="utf-8")
    (tmp_path / "Movie.fr.srt").write_text(SRT, encoding="utf-8")

    def interrupt(_prompt):
        signal.raise_signal(signal.SIGINT)
        return "1"

    monkeypatch.setattr("builtins.input", interrupt)

    exit_code = main(["run", str(media), "--target-language", "zh"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Job canceled: Job canceled" in captured.err
    assert "[progress] discovered" in captured.err
    assert "[progress] canceled" in captured.err
    assert "Source selected" not in captured.err


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
