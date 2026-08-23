from pathlib import Path
from types import SimpleNamespace

import pytest
from PySubtrans.Options import SettingsType
from PySubtrans.Providers.Clients.CustomClient import CustomClient
from PySubtrans.TranslationProvider import TranslationProvider

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

_PROVIDER_ENVIRONMENT = {
    "Azure": {
        "AZURE_API_KEY": "azure-key",
        "AZURE_API_BASE": "https://azure.example.test",
        "AZURE_API_VERSION": "2024-10-21",
        "AZURE_DEPLOYMENT_NAME": "translation",
    },
    "Bedrock": {
        "AWS_ACCESS_KEY_ID": "access-key",
        "AWS_SECRET_ACCESS_KEY": "secret-key",
        "AWS_REGION": "us-east-1",
        "BEDROCK_MODEL": "amazon.nova-lite-v1:0",
    },
    "Claude": {"CLAUDE_API_KEY": "claude-key"},
    "Custom Server": {"CUSTOM_SERVER_ADDRESS": "http://server.example.test"},
    "DeepSeek": {"DEEPSEEK_API_KEY": "deepseek-key"},
    "Gemini": {"GEMINI_API_KEY": "gemini-key"},
    "Mistral": {"MISTRAL_API_KEY": "mistral-key"},
    "OpenAI": {"OPENAI_API_KEY": "openai-key"},
    "OpenRouter": {"OPENROUTER_API_KEY": "openrouter-key"},
}

_PROVIDER_ENVIRONMENT_KEYS = {
    key for values in _PROVIDER_ENVIRONMENT.values() for key in values
}
_OPTIONAL_PROVIDER_ENVIRONMENT_KEYS = {
    "AZURE_PROXY",
    "BEDROCK_MAX_TOKENS",
    "BEDROCK_TEMPERATURE",
    "BEDROCK_RATE_LIMIT",
    "BEDROCK_PROXY",
    "CLAUDE_MODEL",
    "CLAUDE_STREAM_RESPONSES",
    "CLAUDE_THINKING",
    "CLAUDE_MAX_TOKENS",
    "CLAUDE_MAX_THINKING_TOKENS",
    "CLAUDE_TEMPERATURE",
    "CLAUDE_RATE_LIMIT",
    "CLAUDE_PROXY",
    "CUSTOM_ENDPOINT",
    "CUSTOM_SUPPORTS_CONVERSATION",
    "CUSTOM_SUPPORTS_SYSTEM_MESSAGES",
    "CUSTOM_PROMPT_TEMPLATE",
    "CUSTOM_TEMPERATURE",
    "CUSTOM_MAX_TOKENS",
    "CUSTOM_MAX_COMPLETION_TOKENS",
    "CUSTOM_TIMEOUT",
    "CUSTOM_API_KEY",
    "CUSTOM_MODEL",
    "CUSTOM_SUPPORTS_PARALLEL_THREADS",
    "CUSTOM_REPETITION_PENALTY",
    "CUSTOM_MIN_P",
    "DEEPSEEK_API_BASE",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_MAX_TOKENS",
    "DEEPSEEK_TEMPERATURE",
    "DEEPSEEK_RATE_LIMIT",
    "DEEPSEEK_PROXY",
    "GEMINI_MODEL",
    "GEMINI_STREAM_RESPONSES",
    "GEMINI_ENABLE_THINKING",
    "GEMINI_THINKING_BUDGET",
    "GEMINI_TEMPERATURE",
    "GEMINI_RATE_LIMIT",
    "GEMINI_PROXY",
    "MISTRAL_SERVER_URL",
    "MISTRAL_MODEL",
    "MISTRAL_TEMPERATURE",
    "MISTRAL_RATE_LIMIT",
    "MISTRAL_PROXY",
    "OPENAI_API_BASE",
    "OPENAI_MODEL",
    "OPENAI_TEMPERATURE",
    "OPENAI_RATE_LIMIT",
    "OPENAI_FREE_PLAN",
    "MAX_INSTRUCT_TOKENS",
    "OPENAI_USE_HTTPX",
    "OPENAI_REASONING_EFFORT",
    "OPENAI_STREAM_RESPONSES",
    "OPENAI_PROXY",
    "OPENROUTER_SERVER_ADDRESS",
    "OPENROUTER_MODEL",
    "OPENROUTER_MODEL_FAMILY",
    "OPENROUTER_STREAM_RESPONSES",
    "OPENROUTER_MAX_TOKENS",
    "OPENROUTER_TEMPERATURE",
    "OPENROUTER_RATE_LIMIT",
    "OPENROUTER_PROXY",
}
_PROVIDER_CONFIGURATION_KEYS = (
    _PROVIDER_ENVIRONMENT_KEYS | _OPTIONAL_PROVIDER_ENVIRONMENT_KEYS
)
_PROVIDER_ENVIRONMENT_WITH_REQUIRED_VALUES = {
    provider: values
    for provider, values in _PROVIDER_ENVIRONMENT.items()
    if values and provider != "Custom Server"
}


class Event:
    def connect(self, *_args, **_kwargs) -> None:
        pass

    def disconnect(self, *_args, **_kwargs) -> None:
        pass


class MinimalEngine:
    def __init__(self, *_args, **_kwargs) -> None:
        self.aborted = False
        self.errors: list[object] = []
        self.terminology_map: dict[str, str] = {}
        self.events = SimpleNamespace(
            batch_translated=Event(), terminology_updated=Event()
        )


def test_all_pysubtrans_provider_extras_are_registered():
    assert set(TranslationProvider.get_providers()) == _SUPPORTED_PROVIDERS


@pytest.mark.parametrize("provider_name", sorted(_PROVIDER_ENVIRONMENT))
def test_provider_is_available_with_complete_local_configuration(
    monkeypatch, provider_name
):
    for key in _PROVIDER_CONFIGURATION_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROVIDER", provider_name)
    for key, value in _PROVIDER_ENVIRONMENT[provider_name].items():
        monkeypatch.setenv(key, value)

    translator = PySubtransTranslator()

    assert translator.available is True
    assert translator.availability_message is None


def test_custom_server_accepts_pysubtrans_default_address(monkeypatch):
    for key in _PROVIDER_CONFIGURATION_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROVIDER", "Custom Server")

    translator = PySubtransTranslator()

    assert translator.available is True


@pytest.mark.parametrize(
    ("provider_name", "missing_key"),
    [
        (provider_name, next(iter(environment)))
        for provider_name, environment in _PROVIDER_ENVIRONMENT_WITH_REQUIRED_VALUES.items()
    ],
)
def test_provider_is_unavailable_when_required_local_configuration_is_missing(
    monkeypatch, provider_name, missing_key
):
    for key in _PROVIDER_CONFIGURATION_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROVIDER", provider_name)
    for key, value in _PROVIDER_ENVIRONMENT[provider_name].items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing_key)

    translator = PySubtransTranslator()

    assert translator.available is False
    assert missing_key in translator.availability_message


@pytest.mark.parametrize("provider_name", ["", "Not a provider"])
def test_unknown_or_missing_provider_is_unavailable(monkeypatch, provider_name):
    monkeypatch.setenv("PROVIDER", provider_name)

    translator = PySubtransTranslator()

    assert translator.available is False
    assert translator.availability_message


def test_bedrock_environment_settings_are_passed_to_pysubtrans(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER", "Bedrock")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_MODEL", "amazon.nova-lite-v1:0")
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "4096")
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
        captured["settings"] = dict(options.provider_settings["Bedrock"])
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
        source, "zh-Hans", work_directory=tmp_path / "work"
    )

    assert captured == {
        "provider": "Bedrock",
        "settings": {
            "access_key": "access-key",
            "secret_access_key": "secret-key",
            "aws_region": "us-east-1",
            "model": "amazon.nova-lite-v1:0",
            "max_tokens": "4096",
        },
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
    monkeypatch, project, engine, save_translation, init_options
):
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
        user_overrides={"Jon": "琼恩", "Absent": "忽略"},
        work_directory=work_directory,
    )

    assert result == source.read_bytes()
    assert captured["settings"] == {
        "provider": "",
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
