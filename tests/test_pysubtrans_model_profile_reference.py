"""Runtime PySubtrans option discovery used by Model Profiles."""

from unittest.mock import Mock

from fastapi.testclient import TestClient
from PySubtrans.SettingsType import SettingsType
from PySubtrans.TranslationProvider import TranslationProvider

from cueweaver.application import CueWeaverApplication
from cueweaver.http import create_app


class Translator:
    def translate(self, *_args, **_kwargs):
        raise AssertionError("translation is not used by Model Profile endpoints")


def test_reference_endpoint_returns_installed_version_and_registry_names(
    tmp_path, monkeypatch
):
    class SyntheticProvider:
        pass

    monkeypatch.setattr(
        TranslationProvider,
        "get_providers",
        lambda: {"Zulu": SyntheticProvider, "Alpha": SyntheticProvider},
    )
    application = CueWeaverApplication(Translator(), tmp_path / "work")
    try:
        with TestClient(create_app(application)) as client:
            response = client.get("/api/model-profile-reference")
    finally:
        application.close()

    assert response.status_code == 200
    assert response.json() == {
        "pysubtrans_version": "1.6.0",
        "providers": ["Alpha", "Zulu"],
    }


def test_options_endpoint_invokes_real_combination_and_dynamic_discovery(
    tmp_path, monkeypatch
):
    calls: list[tuple[str, dict[str, object]]] = []

    class SyntheticProvider:
        name = "Synthetic"

        def __init__(self, settings):
            calls.append(("init", dict(settings)))
            self.settings = SettingsType(
                {
                    "api_key": settings.get("api_key"),
                    "mode": settings.get("mode", "simple"),
                    "count": settings.get("count", 2),
                    "ratio": settings.get("ratio", 0.5),
                    "enabled": settings.get("enabled", False),
                    "notes": settings.get("notes"),
                }
            )
            self.refresh_when_changed = ["api_key", "mode"]
            self.ResetAvailableModels = Mock()
            self.UpdateSettings = Mock()
            self.selected_model = None
            self.available_models = ()

        def GetCombinedSettings(self, overrides):
            calls.append(("combined", dict(overrides)))
            combined = SettingsType(self.settings.copy())
            combined.update(overrides)
            return combined

        def GetOptions(self, settings):
            calls.append(("options", dict(settings)))
            modes = ["simple", "advanced"] if settings["api_key"] else ["simple"]
            return {
                "mode": (modes, "Mode"),
                "count": (int, "Count"),
                "ratio": (float, "Ratio"),
                "enabled": (bool, "Enabled"),
                "notes": ("multiline", "Notes"),
            }

    monkeypatch.setattr(
        TranslationProvider,
        "get_providers",
        lambda: {SyntheticProvider.name: SyntheticProvider},
    )
    application = CueWeaverApplication(Translator(), tmp_path / "work")
    try:
        with TestClient(create_app(application)) as client:
            response = client.post(
                "/api/model-profile-options",
                json={
                    "provider": "Synthetic",
                    "settings": {"api_key": "synthetic-secret", "mode": "advanced"},
                },
            )
    finally:
        application.close()

    assert response.status_code == 200
    assert calls == [
        ("init", {"api_key": "synthetic-secret", "mode": "advanced"}),
        ("combined", {"api_key": "synthetic-secret", "mode": "advanced"}),
        (
            "options",
            {
                "api_key": "synthetic-secret",
                "mode": "advanced",
                "count": 2,
                "ratio": 0.5,
                "enabled": False,
                "notes": None,
            },
        ),
    ]
    assert response.json() == {
        "provider": "Synthetic",
        "options": [
            {
                "key": "mode",
                "type": "choice",
                "description": "Mode",
                "choices": ["simple", "advanced"],
                "value": "advanced",
            },
            {
                "key": "count",
                "type": "integer",
                "description": "Count",
                "choices": None,
                "value": 2,
            },
            {
                "key": "ratio",
                "type": "number",
                "description": "Ratio",
                "choices": None,
                "value": 0.5,
            },
            {
                "key": "enabled",
                "type": "boolean",
                "description": "Enabled",
                "choices": None,
                "value": False,
            },
            {
                "key": "notes",
                "type": "multiline",
                "description": "Notes",
                "choices": None,
                "value": None,
            },
        ],
        "refresh_when_changed": ["api_key", "mode"],
        "setting_updates": {},
    }


def test_options_reconciles_missing_or_invalid_models_without_materializing_defaults(
    tmp_path, monkeypatch
):
    class SyntheticProvider:
        name = "Synthetic"

        def __init__(self, settings):
            self.settings = SettingsType(
                {"model": settings.get("model", "old-model"), "retries": 2}
            )
            self.refresh_when_changed = ["api_key", "model"]
            self.ResetAvailableModels = Mock()
            self.UpdateSettings = Mock(side_effect=self.settings.update)
            self.selected_model = self.settings.get("model")
            self.available_models = ["model-a", "model-b"]

        def GetCombinedSettings(self, overrides):
            return SettingsType({**self.settings, **overrides})

        def GetOptions(self, settings):
            options = {
                "model": (["model-a", "model-b"], "Model"),
                "retries": (int, "Retries"),
            }
            if settings.get("model") == "model-a":
                options["model_a_detail"] = (str, "Model A detail")
            return options

    monkeypatch.setattr(
        TranslationProvider, "get_providers", lambda: {"Synthetic": SyntheticProvider}
    )
    application = CueWeaverApplication(Translator(), tmp_path / "work")
    try:
        with TestClient(create_app(application)) as client:
            for settings, expected_updates in [
                ({}, {"model": "model-a"}),
                ({"model": None}, {"model": "model-a"}),
                ({"model": "missing"}, {"model": "model-a"}),
                ({"model": "model-b"}, {}),
            ]:
                result = client.post(
                    "/api/model-profile-options",
                    json={"provider": "Synthetic", "settings": settings},
                )
                assert result.status_code == 200
                body = result.json()
                assert body["setting_updates"] == expected_updates
                assert body["options"][0]["value"] == (
                    expected_updates.get("model") or settings["model"]
                )
                assert body["options"][1]["value"] == 2
                assert (
                    "model_a_detail" in {option["key"] for option in body["options"]}
                ) == (body["options"][0]["value"] == "model-a")
    finally:
        application.close()


def test_options_endpoint_reports_unknown_unsupported_and_runtime_errors_safely(
    tmp_path, monkeypatch
):
    class UnsupportedProvider:
        name = "Unsupported"

        def __init__(self, settings):
            self.settings = settings
            self.refresh_when_changed = []

        def GetCombinedSettings(self, overrides):
            return overrides

        def ResetAvailableModels(self):
            pass

        def UpdateSettings(self, settings):
            pass

        selected_model = None
        available_models = ()

        def GetOptions(self, _settings):
            return {"bad": (object, "Bad")}

    class FailingProvider(UnsupportedProvider):
        name = "Failing"

        def GetOptions(self, settings):
            raise RuntimeError(f"failure with {settings['api_key']}")

    monkeypatch.setattr(
        TranslationProvider,
        "get_providers",
        lambda: {
            UnsupportedProvider.name: UnsupportedProvider,
            FailingProvider.name: FailingProvider,
        },
    )
    application = CueWeaverApplication(Translator(), tmp_path / "work")
    try:
        with TestClient(create_app(application)) as client:
            unknown = client.post(
                "/api/model-profile-options",
                json={"provider": "Missing", "settings": {}},
            )
            unsupported = client.post(
                "/api/model-profile-options",
                json={"provider": "Unsupported", "settings": {}},
            )
            failed = client.post(
                "/api/model-profile-options",
                json={
                    "provider": "Failing",
                    "settings": {"api_key": "never-expose-this"},
                },
            )
    finally:
        application.close()

    assert unknown.json()["error_code"] == "invalid_model_profile_provider"
    assert unsupported.json()["error_code"] == "unsupported_model_profile_option"
    assert failed.json()["error_code"] == "model_profile_runtime_failed"
    assert "never-expose-this" not in failed.text
