from pathlib import Path
from types import SimpleNamespace

import pytest

from cueweaver.translation import PySubtransTranslator


class Event:
    def connect(self, *_args, **_kwargs) -> None:
        pass

    def disconnect(self, *_args, **_kwargs) -> None:
        pass


def _patch_translation_dependencies(
    monkeypatch, project, engine, save_translation, init_options
):
    project.subtitles.SaveTranslation = save_translation
    monkeypatch.setattr("cueweaver.translation.init_options", init_options)
    monkeypatch.setattr(
        "cueweaver.translation.init_project", lambda *_args, **_kwargs: project
    )
    monkeypatch.setattr(
        "cueweaver.translation.init_translation_provider", lambda *_args: object()
    )
    monkeypatch.setattr("cueweaver.translation.SubtitleTranslator", engine)


def test_translation_uses_the_explicit_work_directory_and_filters_term_map(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nJon arrives.\n", encoding="utf-8"
    )
    work_directory = tmp_path / "work"
    captured: dict[str, object] = {}

    class Project:
        def __init__(self):
            self.subtitles = SimpleNamespace(
                terminology_map={"Old term": "旧术语"}, all_translated=True
            )
            self.write_translation = True

        def TranslateSubtitles(self, _engine) -> None:
            pass

        def SaveProjectFile(self) -> None:
            pass

    class Engine:
        def __init__(self, _options, _provider, *, resume, terminology_map):
            captured["resume"] = resume
            captured["terminology_map"] = terminology_map
            self.aborted = False
            self.errors = []
            self.terminology_map = terminology_map
            self.events = SimpleNamespace(
                batch_translated=Event(), terminology_updated=Event()
            )

    project = Project()

    def init_options(**settings):
        captured["settings"] = settings
        return SimpleNamespace(provider="Test Provider")

    def save_translation(path: str) -> None:
        Path(path).write_bytes(source.read_bytes())

    _patch_translation_dependencies(
        monkeypatch, project, Engine, save_translation, init_options
    )

    result = PySubtransTranslator().translate(
        source,
        "zh-Hans",
        user_overrides={"Jon": "琼恩", "Absent": "忽略"},
        work_directory=work_directory,
    )

    assert result == source.read_bytes()
    assert captured["settings"] == {
        "target_language": "zh-Hans",
        "prompt": "Translate these subtitles to Chinese (Simplified)",
        "preprocess_subtitles": False,
        "postprocess_translation": False,
        "build_terminology_map": True,
        "stop_on_error": True,
        "project_file": True,
    }
    assert captured["terminology_map"] == {"Jon": "琼恩"}
    assert list(work_directory.glob("translation/*/source.srt"))


@pytest.mark.parametrize(
    ("target_language", "prompt_language"),
    [
        ("zh-Hans-SG", "Chinese (Simplified, Singapore)"),
        ("zht", "Chinese (Traditional)"),
        ("gbk", "Chinese (Simplified)"),
        ("pob", "Portuguese (Brazil)"),
        ("spl", "Spanish (Latin America)"),
        ("ger", "German"),
        ("iw", "Hebrew"),
        ("chs", "Chinese (Simplified)"),
        ("zhs", "Chinese (Simplified)"),
        ("gb", "Chinese (Simplified)"),
        ("gb18030", "Chinese (Simplified)"),
        ("gb2312", "Chinese (Simplified)"),
        ("cht", "Chinese (Traditional)"),
        ("big5", "Chinese (Traditional)"),
        ("esla", "Spanish (Latin America)"),
        ("latam", "Spanish (Latin America)"),
        ("chi", "Chinese"),
        ("cze", "Czech"),
        ("dut", "Dutch"),
        ("fre", "French"),
        ("gre", "Greek"),
        ("mac", "Macedonian"),
        ("may", "Malay"),
        ("per", "Persian"),
        ("rum", "Romanian"),
        ("slo", "Slovak"),
        ("tib", "Tibetan"),
        ("wel", "Welsh"),
        ("in", "Indonesian"),
        ("ji", "Yiddish"),
        ("qaa", "qaa"),
        ("und", "und"),
        ("x-private", "x-private"),
        ("not a language", "not a language"),
    ],
)
def test_translation_uses_an_explicit_english_language_description_in_the_prompt(
    tmp_path, monkeypatch, target_language, prompt_language
):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class Project:
        subtitles = SimpleNamespace(terminology_map={}, all_translated=True)
        write_translation = True

        def TranslateSubtitles(self, _engine) -> None:
            pass

        def SaveProjectFile(self) -> None:
            pass

    class Engine:
        def __init__(self, _options, _provider, *, resume, terminology_map):
            self.aborted = False
            self.errors = []
            self.terminology_map = terminology_map
            self.events = SimpleNamespace(
                batch_translated=Event(), terminology_updated=Event()
            )

    project = Project()

    def init_options(**settings):
        captured.update(settings)
        return SimpleNamespace(provider="Test Provider")

    def save_translation(path: str) -> None:
        Path(path).write_bytes(source.read_bytes())

    _patch_translation_dependencies(
        monkeypatch, project, Engine, save_translation, init_options
    )

    PySubtransTranslator().translate(
        source, target_language, work_directory=tmp_path / "work"
    )

    assert captured["target_language"] == target_language
    assert captured["prompt"] == f"Translate these subtitles to {prompt_language}"


def test_translation_does_not_reject_non_utf8_source_before_pysubtrans(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.srt"
    source.write_bytes(b"\xff")
    captured: dict[str, object] = {}

    class Project:
        subtitles = SimpleNamespace(terminology_map={}, all_translated=True)

        def SaveProjectFile(self) -> None:
            pass

        def TranslateSubtitles(self, _engine) -> None:
            pass

    class Engine:
        def __init__(self, *_args, **_kwargs) -> None:
            self.aborted = False
            self.errors: list[object] = []
            self.terminology_map: dict[str, str] = {}
            self.events = SimpleNamespace(
                batch_translated=Event(), terminology_updated=Event()
            )

    def init_project(_options, *, filepath, **_kwargs):
        captured["filepath"] = filepath
        return Project()

    def save_translation(path: str) -> None:
        Path(path).write_bytes(b"translated")

    Project.subtitles.SaveTranslation = save_translation
    monkeypatch.setattr(
        "cueweaver.translation.init_options",
        lambda **_kwargs: SimpleNamespace(provider="Test Provider"),
    )
    monkeypatch.setattr("cueweaver.translation.init_project", init_project)
    monkeypatch.setattr(
        "cueweaver.translation.init_translation_provider", lambda *_args: object()
    )
    monkeypatch.setattr("cueweaver.translation.SubtitleTranslator", Engine)

    result = PySubtransTranslator().translate(
        source, "zh-Hans", work_directory=tmp_path / "work"
    )

    assert result == b"translated"
    assert Path(captured["filepath"]).read_bytes() == b"\xff"
