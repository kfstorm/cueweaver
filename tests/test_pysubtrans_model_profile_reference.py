"""Static PySubtrans metadata used by the Model Profile editor."""

import os
import socket
from textwrap import dedent

from fastapi.testclient import TestClient
from PySubtrans.SettingsType import SettingsType
from PySubtrans.TranslationProvider import TranslationProvider

from cueweaver.adapters.pysubtrans_model_profile_reference import (
    parse_provider_source,
    pysubtrans_model_profile_reference,
)
from cueweaver.application import CueWeaverApplication
from cueweaver.http import create_app


def test_parser_extracts_supported_shapes_and_ignores_dynamic_choices():
    source = dedent(
        """\
        class SyntheticProvider(TranslationProvider):
            name = "Synthetic"

            def __init__(self, settings):
                super().__init__(self.name, SettingsType({
                    "api_key": settings.get_str("api_key"),
                    "tags": settings.get_list("tags"),
                    "retry_count": settings.get_int("retry_count"),
                    "temperature": settings.get_float("temperature"),
                    "enabled": settings.get_bool("enabled", False),
                    "proxy": settings.get_str("proxy") or os.getenv("SYNTHETIC_PROXY"),
                    "reasoning": settings.get_str("reasoning"),
                    "models": settings.get_str("models"),
                    "status": settings.get_str("status"),
                    "advanced": settings.get_str("advanced"),
                    "complex": build_setting(),
                }))

            def GetOptions(self, settings):
                options = {
                    "api_key": (str, _("Synthetic API key description")),
                }
                options.update({
                    "retry_count": (int, "Retry count description"),
                    "temperature": (float, _("Temperature description")),
                    "enabled": (bool, "Enabled description"),
                    "proxy": (str, "Proxy description"),
                    "reasoning": (["none", "low", "high"], "Reasoning description"),
                    "models": (models, "Dynamic model list"),
                    "status": (["Unable to retrieve models"], "Status text"),
                })
                options["advanced"] = (["basic", "advanced"], "Advanced description")
                return options
        """
    )

    provider = parse_provider_source(source)

    assert provider is not None
    assert provider.name == "Synthetic"
    assert {key: item.type for key, item in provider.settings.items()} == {
        "api_key": "string",
        "tags": "array",
        "retry_count": "integer",
        "temperature": "number",
        "enabled": "boolean",
        "proxy": "string",
        "reasoning": "string",
        "models": "string",
        "status": "string",
        "advanced": "string",
        "complex": None,
    }
    assert provider.settings["api_key"].description == "Synthetic API key description"
    assert provider.settings["reasoning"].choices == ["none", "low", "high"]
    assert provider.settings["advanced"].choices == ["basic", "advanced"]
    assert provider.settings["models"].choices is None
    assert provider.settings["status"].choices is None


def test_installed_provider_contract_is_complete_without_runtime_discovery(monkeypatch):
    pysubtrans_model_profile_reference.cache_clear()
    providers = TranslationProvider.get_providers()
    network_attempts: list[str] = []

    def deny_network(*_args, **_kwargs):
        network_attempts.append("socket")
        raise AssertionError("network access is not part of reference extraction")

    def deny_provider_discovery(*_args, **_kwargs):
        raise AssertionError("provider option/model discovery must not be called")

    monkeypatch.setattr(os, "getenv", lambda _key, default=None: default)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    for provider_class in providers.values():
        monkeypatch.setattr(provider_class, "GetOptions", deny_provider_discovery)
        monkeypatch.setattr(
            provider_class, "GetAvailableModels", deny_provider_discovery
        )

    reference = pysubtrans_model_profile_reference()

    assert set(reference.providers) == set(providers)
    for name, provider_class in providers.items():
        extracted = reference.providers[name].settings
        runtime = provider_class(SettingsType())
        assert set(extracted) == set(runtime.settings.keys())
        assert all(setting.type for setting in extracted.values())
    assert network_attempts == []
    assert reference.providers["OpenAI"].settings["reasoning_effort"].choices == [
        "none",
        "minimal",
        "low",
        "medium",
        "high",
    ]
    assert reference.providers["OpenAI"].settings["model"].choices is None
    assert (
        reference.providers["OpenAI"].settings["model"].description
        == "AI model to use as the translator"
    )
    assert reference.providers["DeepSeek"].settings["model"].choices is None
    assert reference.providers["OpenRouter"].settings["model_family"].choices is None
    assert reference.providers["OpenRouter"].settings["proxy"].description is None


def test_model_profile_reference_endpoint_returns_only_static_metadata(tmp_path):
    class Translator:
        def translate(self, *_args, **_kwargs):
            raise AssertionError("translation is not used by the reference endpoint")

    application = CueWeaverApplication(Translator(), tmp_path / "work")
    with TestClient(create_app(application)) as client:
        response = client.get("/api/model-profile-reference")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pysubtrans_version"] == "1.6.0"
    reasoning = next(
        item
        for item in payload["providers"]["OpenAI"]
        if item["key"] == "reasoning_effort"
    )
    assert reasoning == {
        "key": "reasoning_effort",
        "type": "string",
        "description": (
            "The level of reasoning effort to use for the model "
            "(valid options are model-dependent)"
        ),
        "choices": ["none", "minimal", "low", "medium", "high"],
    }
    assert all(
        set(item) == {"key", "type", "description", "choices"}
        for settings in payload["providers"].values()
        for item in settings
    )
