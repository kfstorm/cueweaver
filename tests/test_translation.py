from pathlib import Path
from types import SimpleNamespace

import pytest
from PySubtrans.Options import SettingsType
from PySubtrans.Providers.Clients.CustomClient import CustomClient
from PySubtrans.SubtitleBatch import SubtitleBatch
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.SubtitleScene import SubtitleScene
from PySubtrans.TranslationEvents import TranslationEvents
from PySubtrans.TranslationProvider import TranslationProvider

from cueweaver.application.translation import TranslationProgress
from cueweaver.translation import PySubtransTranslator, _disable_thinking

_SUPPORTED_PROVIDERS = {
    "Azure",
    "Bedrock",
    "Claude",
    "Custom Server",
    "DeepSeek",
    "Gemini",
    "Mistral",
    "OpenAI",
    "OpenRouter",
}


class Event:
    def connect(self, *_args, **_kwargs) -> None:
        pass

    def disconnect(self, *_args, **_kwargs) -> None:
        pass


class MinimalEngine:
    def __init__(self, *_args, **_kwargs) -> None:
        self.aborted = False
        self.resume = True
        self.errors: list[object] = []
        self.terminology_map: dict[str, str] = {}
        self.events = SimpleNamespace(
            batch_translated=Event(), terminology_updated=Event()
        )


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("observer_fails", [False, True])
@pytest.mark.parametrize("saved_errors", [False, True])
def test_progress_uses_actual_batch_state_without_double_counting(
    tmp_path, monkeypatch, resumed, observer_fails, saved_errors
):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    first = SubtitleBatch({"originals": [SubtitleLine({"number": 1, "text": "Hello"})]})
    second = SubtitleBatch(
        {
            "originals": [
                SubtitleLine({"number": 2, "text": "Welcome"}),
                SubtitleLine({"number": 3, "text": "Goodbye"}),
            ]
        }
    )
    scenes = [SubtitleScene({"batches": [first]}), SubtitleScene({"batches": [second]})]
    if resumed:
        first.translated = first.originals
        second.translated = second.originals if saved_errors else second.originals[:1]
        second.errors = ["Saved validation failure"] if saved_errors else []
    snapshots = []
    checkpoints = []
    engine = MinimalEngine()
    engine.events = TranslationEvents()

    class Project:
        subtitles = SimpleNamespace(terminology_map={}, all_translated=True)

        def TranslateSubtitles(self, translator):
            translator.events.preprocessed.send(translator, scenes=scenes)
            first.translated = first.originals
            translator.events.batch_translated.send(translator, batch=first)
            translator.events.batch_translated.send(translator, batch=first)
            second.translated = second.originals
            second.errors = ["Synthetic validation failure"]
            translator.events.batch_translated.send(translator, batch=second)
            translator.events.scene_translated.send(translator, scene=scenes[1])
            second.errors.clear()
            translator.events.batch_translated.send(translator, batch=second)
            translator.events.scene_translated.send(translator, scene=scenes[1])

        def SaveProjectFile(self):
            checkpoints.append(True)

    _patch_translation_dependencies(
        monkeypatch,
        Project(),
        lambda *_args, **_kwargs: engine,
    )

    def observe(progress):
        snapshots.append(progress)
        if observer_fails:
            raise RuntimeError("Synthetic observer failure")

    assert (
        PySubtransTranslator().translate(
            source, "fr", work_directory=tmp_path / "work", on_progress=observe
        )
        == b"translated"
    )
    assert snapshots == [
        TranslationProgress(
            (2 if saved_errors else 1) if resumed else 0,
            2,
            (2 if saved_errors else 1) if resumed else 0,
            2,
            (3 if saved_errors else 2) if resumed else 0,
            3,
        ),
        *[
            TranslationProgress(
                (2 if resumed and saved_errors else 1),
                2,
                (2 if resumed and saved_errors else 1),
                2,
                (3 if saved_errors else 2) if resumed else 1,
                3,
            )
        ]
        * 2,
        *[TranslationProgress(2, 2, 2, 2, 3, 3)] * 4,
    ]
    assert len(checkpoints) == 5
    assert not engine.events.preprocessed.receivers
    assert not engine.events.batch_translated.receivers
    assert not engine.events.scene_translated.receivers


@pytest.mark.parametrize("saved_errors", [False, True])
def test_progress_prefers_native_completion_properties(
    tmp_path, monkeypatch, saved_errors
):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    # Deliberately disagree with the fallback to prove the native contract wins.
    batch = SimpleNamespace(
        number=1,
        translation_complete=False,
        all_translated=True,
        errors=[],
        size=3,
        originals=[
            SimpleNamespace(number=number, text="Example") for number in range(1, 4)
        ],
    )
    batch.translated = [
        batch.originals[0],
        batch.originals[0],
        SimpleNamespace(number=2, text=None),
        SimpleNamespace(number=99, text="Unmatched translation"),
    ]
    if saved_errors:
        batch.translated = batch.originals
        batch.errors = ["Saved validation failure"]
    scene = SimpleNamespace(translation_complete=False, batches=[batch])
    engine = MinimalEngine()
    engine.events = TranslationEvents()
    snapshots = []

    class Project:
        subtitles = SimpleNamespace(terminology_map={}, all_translated=True)

        def TranslateSubtitles(self, translator):
            translator.events.preprocessed.send(translator, scenes=[scene])
            batch.translation_complete = True
            scene.translation_complete = True
            batch.translated = batch.originals
            batch.all_translated = False
            translator.events.scene_translated.send(translator, scene=scene)

        def SaveProjectFile(self):
            pass

    _patch_translation_dependencies(
        monkeypatch,
        Project(),
        lambda *_args, **_kwargs: engine,
    )
    PySubtransTranslator().translate(
        source, "fr", work_directory=tmp_path / "work", on_progress=snapshots.append
    )
    assert snapshots == [
        TranslationProgress(0, 1, 0, 1, 3 if saved_errors else 1, 3),
        TranslationProgress(1, 1, 1, 1, 3, 3),
    ]


@pytest.mark.parametrize("native_completion", [False, True])
@pytest.mark.parametrize("empty_selection", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_progress_snapshots_resume_skips_before_mutation_and_resets_on_retry(
    tmp_path, monkeypatch, native_completion, empty_selection, resume
):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    batches = [
        SubtitleBatch(
            {
                "number": number,
                "originals": [
                    SubtitleLine({"number": number * 2 + offset, "text": "Example"})
                    for offset in range(2)
                ],
            }
        )
        for number in (1, 2)
    ]
    first, second = batches
    first.translated = first.originals[:1]
    first.errors = ["Saved validation failure"]
    second.translated = second.originals[:1] if empty_selection else []
    scene = SubtitleScene({"batches": batches})
    if native_completion:
        # Newer engines select incomplete batches even when they have saved translations.
        for batch in batches:
            batch.translation_complete = False
        scene.translation_complete = False
    snapshots = []
    checkpoints = []
    attempt = 0
    engine = MinimalEngine()
    engine.resume = resume
    engine.events = TranslationEvents()

    class Project:
        subtitles = SimpleNamespace(terminology_map={}, all_translated=True)

        def TranslateSubtitles(self, translator):
            nonlocal attempt
            attempt += 1
            translator.events.preprocessed.send(translator, scenes=[scene])
            if attempt == 1:
                second.translated = second.originals[:1]
                second.errors = ["Synthetic validation failure"]
                translator.events.batch_translated.send(translator, batch=second)
                translator.events.batch_translated.send(translator, batch=second)
                second.translated = second.originals
                second.errors.clear()
                if native_completion:
                    second.translation_complete = True
                translator.events.scene_translated.send(translator, scene=scene)
                raise RuntimeError("Synthetic attempt failure")
            for batch in batches:
                batch.translated = batch.originals
                batch.errors.clear()
                if native_completion:
                    batch.translation_complete = True
            if native_completion:
                scene.translation_complete = True
            translator.events.scene_translated.send(translator, scene=scene)

        def SaveProjectFile(self):
            checkpoints.append(True)

    _patch_translation_dependencies(
        monkeypatch,
        Project(),
        lambda *_args, **_kwargs: engine,
    )
    translator = PySubtransTranslator()
    with pytest.raises(RuntimeError, match="Synthetic attempt failure"):
        translator.translate(
            source, "fr", work_directory=tmp_path / "work", on_progress=snapshots.append
        )
    translator.translate(
        source, "fr", work_directory=tmp_path / "work", on_progress=snapshots.append
    )
    skips_partial = resume and not native_completion and not empty_selection
    assert snapshots == [
        TranslationProgress(
            0,
            1,
            int(skips_partial),
            2,
            2 if skips_partial else 1 + int(empty_selection),
            4,
        ),
        *[
            TranslationProgress(
                0, 1, int(skips_partial), 2, 3 if skips_partial else 2, 4
            )
        ]
        * 2,
        TranslationProgress(
            int(skips_partial),
            1,
            2 if skips_partial else 1,
            2,
            4 if skips_partial else 3,
            4,
        ),
        # Both batches now have translations: legacy TranslateScene's empty selection
        # falls back to all batches, so the partial batch is no longer skipped.
        TranslationProgress(0, 1, 1, 2, 3, 4),
        TranslationProgress(1, 1, 2, 2, 4, 4),
    ]
    assert len(checkpoints) == 4


def test_all_pysubtrans_provider_extras_are_registered():
    assert set(TranslationProvider.get_providers()) == _SUPPORTED_PROVIDERS


def test_typed_profile_settings_are_passed_to_pysubtrans(tmp_path, monkeypatch):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class Project:
        write_translation = False
        subtitles = SimpleNamespace(terminology_map={}, all_translated=True)

        def TranslateSubtitles(self, _engine) -> None:
            pass

        def SaveProjectFile(self) -> None:
            pass

    def init_translation_provider(provider, options):
        captured["provider"] = provider
        options.InitialiseProviderSettings(
            "OpenAI", SettingsType({"model": "", "api_key": "", "temperature": 0.0})
        )
        captured["settings"] = dict(options.provider_settings["OpenAI"])
        captured["max_threads"] = options.get("max_threads")
        return object()

    def save_translation(path: str) -> None:
        Path(path).write_bytes(source.read_bytes())

    Project.subtitles.SaveTranslation = save_translation
    monkeypatch.setattr(
        "cueweaver.translation.init_translation_provider",
        init_translation_provider,
    )
    monkeypatch.setattr(
        "cueweaver.translation.init_project",
        lambda *_args, **_kwargs: Project(),
    )
    monkeypatch.setattr("cueweaver.translation.SubtitleTranslator", MinimalEngine)

    PySubtransTranslator().translate(
        source,
        "zh-Hans",
        work_directory=tmp_path / "work",
        settings={
            "provider": "OpenAI",
            "model": "synthetic-model",
            "api_key": "synthetic-key",
            "max_threads": 2,
            "temperature": 0.5,
        },
    )

    assert captured == {
        "provider": "OpenAI",
        "settings": {
            "model": "synthetic-model",
            "api_key": "synthetic-key",
            "max_threads": 2,
            "temperature": 0.5,
        },
        "max_threads": 4,
    }


def test_disable_thinking_adds_explicit_request_body_option():
    client = CustomClient(
        SettingsType(
            {
                "server_address": "http://127.0.0.1:1234",
                "endpoint": "/v1/chat/completions",
                "instructions": "Translate the subtitles.",
                "supports_conversation": True,
                "model": "deepseek-v4-flash",
            }
        )
    )
    request = SimpleNamespace(
        prompt=SimpleNamespace(
            messages=[{"role": "user", "content": "Translate this."}],
            content="Translate this.",
        )
    )
    engine = SimpleNamespace(client=client)

    _disable_thinking(engine)

    request_body = engine.client._generate_request_body(request, 0.0)

    assert request_body == {
        "temperature": 0.0,
        "stream": False,
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "Translate this."}],
        "thinking": {"type": "disabled"},
    }


def _patch_translation_dependencies(
    monkeypatch, project, engine, save_translation=None, init_options=None
):
    if save_translation is None:

        def save_translation(path):
            Path(path).write_bytes(b"translated")

    if init_options is None:

        def init_options(**_kwargs):
            return SimpleNamespace(provider="OpenAI", model="synthetic-model")

    monkeypatch.delenv("PROVIDER", raising=False)
    project.subtitles.SaveTranslation = save_translation
    monkeypatch.setattr("cueweaver.translation.init_options", init_options)
    monkeypatch.setattr(
        "cueweaver.translation.init_project", lambda *_args, **_kwargs: project
    )
    monkeypatch.setattr(
        "cueweaver.translation.init_translation_provider", lambda *_args: object()
    )
    monkeypatch.setattr("cueweaver.translation.SubtitleTranslator", engine)


@pytest.mark.parametrize("aborted", [False, True])
def test_translation_preserves_provider_errors_even_when_aborted(
    tmp_path, monkeypatch, aborted
):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    class Project:
        subtitles = SimpleNamespace(terminology_map={}, all_translated=False)

        def TranslateSubtitles(self, engine):
            engine.errors = [
                RuntimeError("Client error: 401 Synthetic account missing.")
            ]
            engine.aborted = aborted

        def SaveProjectFile(self):
            pass

    _patch_translation_dependencies(
        monkeypatch,
        Project(),
        MinimalEngine,
        lambda _path: pytest.fail("Failed translation must not be published"),
        lambda **_kwargs: SimpleNamespace(
            provider="OpenRouter", model="synthetic-model"
        ),
    )

    with pytest.raises(
        RuntimeError, match="Client error: 401 Synthetic account missing"
    ):
        PySubtransTranslator().translate(
            source, "zh-Hans", work_directory=tmp_path / "work"
        )


@pytest.mark.parametrize(
    ("provider_name", "model", "thinking_disabled"),
    [
        ("DeepSeek", "any-model", True),
        ("Custom Server", "deepseek-v4-flash", True),
        ("Custom Server", "unrelated-model", False),
    ],
)
def test_translation_uses_provider_specific_thinking_policy(
    tmp_path, monkeypatch, provider_name, model, thinking_disabled
):
    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nJon arrives.\n", encoding="utf-8"
    )
    work_directory = tmp_path / "work" / "translation"
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
            self.client = SimpleNamespace(
                _generate_request_body=lambda *_args, **_kwargs: {"model": "test-model"}
            )
            captured["client"] = self.client
            self.aborted = False
            self.errors = []
            self.terminology_map = terminology_map
            self.events = SimpleNamespace(
                batch_translated=Event(), terminology_updated=Event()
            )

    project = Project()

    def init_options(**settings):
        captured["settings"] = settings
        return SimpleNamespace(provider=provider_name, model=model)

    def save_translation(path: str) -> None:
        Path(path).write_bytes(source.read_bytes())

    _patch_translation_dependencies(
        monkeypatch, project, Engine, save_translation, init_options
    )

    result = PySubtransTranslator().translate(
        source,
        "zh-Hans",
        settings={"provider": provider_name, "max_threads": 2},
        user_overrides={"Jon": "琼恩", "Absent": "忽略"},
        work_directory=work_directory,
    )

    assert result == source.read_bytes()
    assert captured["settings"] == {
        "provider": provider_name,
        "provider_settings": {provider_name: {"max_threads": 2}},
        "target_language": "zh-Hans",
        "prompt": "Translate these subtitles to Chinese (Simplified)",
        "preprocess_subtitles": False,
        "postprocess_translation": False,
        "build_terminology_map": True,
        "stop_on_error": True,
        "project_file": True,
    }
    assert captured["terminology_map"] == {"Jon": "琼恩"}
    request_body = captured["client"]._generate_request_body()
    assert ("thinking" in request_body) is thinking_disabled
    if thinking_disabled:
        assert request_body["thinking"] == {"type": "disabled"}
    assert list(work_directory.glob("*/source.srt"))


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
    monkeypatch.setattr("cueweaver.translation.SubtitleTranslator", MinimalEngine)

    result = PySubtransTranslator().translate(
        source, "zh-Hans", work_directory=tmp_path / "work"
    )

    assert result == b"translated"
    assert Path(captured["filepath"]).read_bytes() == b"\xff"
