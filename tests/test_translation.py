from pathlib import Path
from types import SimpleNamespace

from cueweaver.translation import PySubtransTranslator


class Event:
    def connect(self, *_args, **_kwargs) -> None:
        pass

    def disconnect(self, *_args, **_kwargs) -> None:
        pass


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

    project.subtitles.SaveTranslation = save_translation
    monkeypatch.setattr("cueweaver.translation.init_options", init_options)
    monkeypatch.setattr(
        "cueweaver.translation.init_project", lambda *_args, **_kwargs: project
    )
    monkeypatch.setattr(
        "cueweaver.translation.init_translation_provider", lambda *_args: object()
    )
    monkeypatch.setattr("cueweaver.translation.SubtitleTranslator", Engine)

    result = PySubtransTranslator().translate(
        source,
        "zh-Hans",
        user_overrides={"Jon": "琼恩", "Absent": "忽略"},
        work_directory=work_directory,
    )

    assert result == source.read_bytes()
    assert captured["settings"] == {
        "target_language": "zh-Hans",
        "prompt": "Translate these subtitles to zh-Hans",
        "preprocess_subtitles": False,
        "postprocess_translation": False,
        "build_terminology_map": True,
        "stop_on_error": True,
        "project_file": True,
    }
    assert captured["terminology_map"] == {"Jon": "琼恩"}
    assert list(work_directory.glob("translation/*/source.srt"))
