"""Public standalone Model Profile behavior."""

import threading
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from PySubtrans.SettingsType import SettingsType
from PySubtrans.TranslationProvider import TranslationProvider

from cueweaver.application import CueWeaverApplication
from cueweaver.application.database import SqliteDatabase
from cueweaver.application.errors import ServiceError
from cueweaver.application.jobs import CreateJobRequest, Jobs
from cueweaver.application.model_profiles import ModelProfiles
from cueweaver.http import create_app


def setting(key: str, value: object) -> dict[str, object]:
    return {"key": key, "value": value}


@pytest.fixture
def runtime_provider(monkeypatch):
    class SyntheticProvider:
        def __init__(self, settings):
            self.settings = SettingsType(
                {"model": settings.get("model", "model-a"), "temperature": 0.5}
            )
            self.refresh_when_changed = ["mode", "model"]
            self.ResetAvailableModels = Mock()
            self.UpdateSettings = Mock(side_effect=self.settings.update)
            self.selected_model = Mock(return_value=self.settings.get("model"))
            self.available_models = []

        def GetCombinedSettings(self, settings):
            return SettingsType({**self.settings, **settings})

        def GetOptions(self, settings):
            options = {
                "mode": (["simple", "advanced"], "Mode"),
                "temperature": (float, "Temperature"),
                "retries": (int, "Retries"),
                "enabled": (bool, "Enabled"),
                "notes": ("multiline", "Notes"),
                "model": (str, "Model"),
            }
            if settings.get("mode") == "advanced":
                options["detail"] = (str, "Detail")
            return options

    monkeypatch.setattr(
        TranslationProvider,
        "get_providers",
        lambda: {"Synthetic": SyntheticProvider},
    )
    return SyntheticProvider


@pytest.mark.usefixtures("runtime_provider")
def test_save_validates_current_runtime_options_and_keeps_valid_values(tmp_path):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    valid = [
        setting("mode", "advanced"),
        setting("detail", "visible"),
        setting("model", "model-b"),
        setting("temperature", 0.25),
        setting("retries", 3),
        setting("enabled", True),
        setting("notes", "first\nsecond"),
    ]
    profile = profiles.create("Example", "Synthetic", valid)
    assert profile["settings"] == sorted(valid, key=lambda item: item["key"])

    for invalid in [
        [setting("secret_constructor_key", "value")],
        [setting("mode", "other")],
        [setting("retries", True)],
        [setting("retries", 1.5)],
        [setting("temperature", True)],
        [setting("temperature", float("inf"))],
        [setting("enabled", "true")],
        [setting("notes", 5)],
        [setting("model", [])],
        [setting("mode", "simple"), setting("detail", "hidden")],
    ]:
        with pytest.raises(ServiceError) as error:
            profiles.replace(profile["id"], "Example", "Synthetic", invalid)
        assert error.value.error_code == "invalid_model_profile"
        assert profiles.get(profile["id"])["settings"] == profile["settings"]


def test_save_preserves_existing_model_when_model_discovery_fails(
    tmp_path, monkeypatch
):
    class AvailableProvider:
        def __init__(self, settings):
            self.settings = SettingsType({"model": settings.get("model", "gpt-5-mini")})
            self.refresh_when_changed = []
            self.selected_model = self.settings.get("model")
            self.available_models = ["gpt-5-mini"]

        def GetCombinedSettings(self, overrides):
            return SettingsType({**self.settings, **overrides})

        def ResetAvailableModels(self):
            pass

        def UpdateSettings(self, settings):
            self.settings.update(settings)

        def GetOptions(self, _settings):
            return {"model": (self.available_models, "Model")}

    class UnavailableProvider(AvailableProvider):
        def __init__(self, settings):
            super().__init__(settings)
            self.available_models = []

        def GetOptions(self, _settings):
            return {
                "model": (
                    ["Unable to retrieve models"],
                    "Check API key and base URL and try again",
                )
            }

    monkeypatch.setattr(
        TranslationProvider, "get_providers", lambda: {"Synthetic": AvailableProvider}
    )
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    profile = profiles.create("Original", "Synthetic", [setting("model", "gpt-5-mini")])

    monkeypatch.setattr(
        TranslationProvider, "get_providers", lambda: {"Synthetic": UnavailableProvider}
    )
    updated = profiles.replace(
        profile["id"], "Renamed", "Synthetic", [setting("model", "gpt-5-mini")]
    )
    assert updated["name"] == "Renamed"
    assert updated["settings"] == [setting("model", "gpt-5-mini")]

    with pytest.raises(ServiceError) as error:
        profiles.replace(
            profile["id"],
            "Renamed",
            "Synthetic",
            [setting("model", "Unable to retrieve models")],
        )
    assert error.value.error_code == "invalid_model_profile"


def test_profile_api_reconciles_model_and_translation_uses_the_saved_selection(
    tmp_path, runtime_provider, monkeypatch
):
    class AvailableModelsProvider(runtime_provider):
        def __init__(self, settings):
            super().__init__(settings)
            self.settings["model"] = settings.get("model", "old-model")
            self.selected_model = Mock(return_value=self.settings["model"])
            self.available_models = ["model-a", "model-b"]

        def GetOptions(self, settings):
            return {
                **super().GetOptions(settings),
                "model": (["model-a", "model-b"], "Model"),
            }

    monkeypatch.setattr(
        TranslationProvider,
        "get_providers",
        lambda: {"Synthetic": AvailableModelsProvider},
    )
    media = tmp_path / "media"
    media.mkdir()
    (media / "example.mkv").write_bytes(b"media")
    (media / "example.en.srt").write_text("1\nhello\n", encoding="utf-8")
    translated = threading.Event()
    translated_settings: list[dict[str, object]] = []

    class Translator:
        def translate(self, _source, _target, *, settings, **_kwargs):
            translated_settings.append(dict(settings))
            translated.set()
            return b"1\n00:00:00,000 --> 00:00:01,000\nTranslated\n"

    application = CueWeaverApplication(Translator(), tmp_path / "work", media)
    try:
        with TestClient(create_app(application, media)) as client:
            invalid_create = client.post(
                "/api/model-profiles",
                json={
                    "name": "Invalid",
                    "provider": "Synthetic",
                    "settings": [setting("constructor_only", "hidden")],
                },
            )
            assert invalid_create.status_code == 400
            assert invalid_create.json()["error_code"] == "invalid_model_profile"
            created = client.post(
                "/api/model-profiles",
                json={"name": "Example", "provider": "Synthetic", "settings": []},
            )
            assert created.status_code == 200
            profile = created.json()
            assert profile["settings"] == [setting("model", "model-a")]
            assert application.model_profiles.resolve(profile["id"]) == {
                "provider": "Synthetic",
                "model": "model-a",
            }
            job = client.post(
                "/api/jobs",
                json={
                    "media_path": "example.mkv",
                    "subtitle_path": "example.en.srt",
                    "target_language_code": "zh",
                    "term_map_mode": "none",
                    "output_conflict_policy": "append-number",
                    "model_profile_id": profile["id"],
                },
            )
            assert job.status_code == 200
            assert translated.wait(5)
            assert translated_settings == [
                {"provider": "Synthetic", "model": "model-a"}
            ]
            rejected = client.put(
                f"/api/model-profiles/{profile['id']}",
                json={
                    "name": "Example",
                    "provider": "Synthetic",
                    "settings": [setting("model", "not-available")],
                },
            )
            assert rejected.status_code == 400
            assert rejected.json()["error_code"] == "invalid_model_profile"
            assert client.get(f"/api/model-profiles/{profile['id']}").json()[
                "settings"
            ] == [setting("model", "model-a")]
    finally:
        application.close()


def test_sparse_profile_create_edit_and_resolve(tmp_path, runtime_provider):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))

    profile = profiles.create("Fast", "Synthetic", [])

    assert len(profile["id"]) == 32
    assert profile["name"] == "Fast"
    assert profile["provider"] == "Synthetic"
    assert profile["settings"] == []
    assert profiles.resolve(profile["id"]) == {"provider": "Synthetic"}
    assert {
        "parent_id",
        "selectable",
        "kind",
        "effective_settings",
        "source",
        "unset",
    }.isdisjoint(profile)

    edited = profiles.replace(
        profile["id"],
        "Detailed",
        "Synthetic",
        [setting("model", "synthetic-model"), setting("temperature", 0.25)],
    )

    assert edited["provider"] == "Synthetic"
    assert edited["settings"] == [
        setting("model", "synthetic-model"),
        setting("temperature", 0.25),
    ]
    assert profiles.resolve(profile["id"]) == {
        "provider": "Synthetic",
        "model": "synthetic-model",
        "temperature": 0.25,
    }


@pytest.mark.parametrize(
    ("provider", "settings", "error_code"),
    [
        ("Unknown", [], "invalid_model_profile_provider"),
        ("OpenAI", [setting("provider", "Claude")], "invalid_model_profile"),
        ("OpenAI", [setting("prompt", "override")], "invalid_model_profile"),
        ("OpenAI", [setting("model", None)], "invalid_model_profile"),
        (
            "OpenAI",
            [setting("temperature", float("nan"))],
            "invalid_model_profile",
        ),
        (
            "OpenAI",
            [setting("model", "one"), setting("model", "two")],
            "invalid_model_profile",
        ),
    ],
)
def test_profile_save_rejects_invalid_provider_and_settings(
    tmp_path, provider, settings, error_code
):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))

    with pytest.raises(ServiceError) as error:
        profiles.create("Example", provider, settings)

    assert error.value.error_code == error_code


def test_profile_api_accepts_every_existing_profile_for_a_job(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "example.mkv").write_bytes(b"media")
    (media / "example.en.srt").write_text(
        "1\nsecond synthetic subtitle\n", encoding="utf-8"
    )

    class Translator:
        def translate(self, _source, _target, **_kwargs):
            return b"1\n00:00:00,000 --> 00:00:01,000\nTranslated\n"

    app = CueWeaverApplication(Translator(), tmp_path / "work", media)
    try:
        with TestClient(create_app(app, media)) as client:
            body = {"name": "Sparse", "provider": "OpenAI", "settings": []}
            response = client.post("/api/model-profiles", json=body)
            assert response.status_code == 200
            profile = response.json()
            assert profile["provider"] == "OpenAI"
            assert profile["settings"] == []

            payload = {
                "media_path": "example.mkv",
                "subtitle_path": "example.en.srt",
                "target_language_code": "zh",
                "term_map_mode": "none",
                "output_conflict_policy": "append-number",
                "model_profile_id": profile["id"],
            }
            created = client.post("/api/jobs", json=payload)
            assert created.status_code == 200
            assert created.json()["request"]["model_profile_id"] == profile["id"]
            assert (
                client.get(f"/api/model-profiles/{profile['id']}").json()["deletable"]
                is False
            )
    finally:
        app.close()


def test_each_translation_attempt_resolves_latest_profile_without_a_snapshot(
    tmp_path, runtime_provider
):
    media = tmp_path / "media"
    media.mkdir()
    (media / "example.mkv").write_bytes(b"media")
    (media / "example.en.srt").write_text("synthetic subtitle", encoding="utf-8")
    database = SqliteDatabase(tmp_path / "work" / "cueweaver.sqlite3")
    profiles = ModelProfiles(database)
    profile = profiles.create("Selected", "Synthetic", [setting("model", "first")])
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    captured: list[dict[str, object]] = []

    class Translator:
        def translate(self, _source, _target, *, settings, **_kwargs):
            captured.append(dict(settings))
            if len(captured) == 1:
                first_started.set()
                assert release_first.wait(5)
            else:
                second_started.set()
            return b"1\n00:00:00,000 --> 00:00:01,000\nTranslated\n"

    jobs = Jobs(
        Translator(),
        media,
        tmp_path / "work",
        model_profiles=profiles,
        database=database,
    )
    try:
        request = CreateJobRequest(
            "example.mkv",
            "example.en.srt",
            "zh",
            "none",
            profile["id"],
            output_conflict_policy="append-number",
        )
        jobs.create(request)
        assert first_started.wait(5)
        jobs.create(request)
        profiles.replace(
            profile["id"], "Selected", "Synthetic", [setting("model", "latest")]
        )
        assert captured[0] == {"provider": "Synthetic", "model": "first"}
        release_first.set()
        assert second_started.wait(5)
        assert captured[1] == {"provider": "Synthetic", "model": "latest"}
    finally:
        release_first.set()
        jobs.wait_closed()
