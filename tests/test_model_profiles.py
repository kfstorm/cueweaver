"""Public standalone Model Profile behavior."""

import threading

import pytest
from fastapi.testclient import TestClient

from cueweaver.application import CueWeaverApplication
from cueweaver.application.database import SqliteDatabase
from cueweaver.application.errors import ServiceError
from cueweaver.application.jobs import CreateJobRequest, Jobs
from cueweaver.application.model_profiles import ModelProfiles
from cueweaver.http import create_app


def setting(key: str, value: object) -> dict[str, object]:
    return {"key": key, "value": value}


def test_sparse_profile_create_edit_and_resolve(tmp_path):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))

    profile = profiles.create("Fast", "OpenAI", [])

    assert len(profile["id"]) == 32
    assert profile["name"] == "Fast"
    assert profile["provider"] == "OpenAI"
    assert profile["settings"] == []
    assert profiles.resolve(profile["id"]) == {"provider": "OpenAI"}
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
        "Claude",
        [setting("model", "synthetic-model"), setting("temperature", 0.25)],
    )

    assert edited["provider"] == "Claude"
    assert edited["settings"] == [
        setting("model", "synthetic-model"),
        setting("temperature", 0.25),
    ]
    assert profiles.resolve(profile["id"]) == {
        "provider": "Claude",
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
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8"
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


def test_each_translation_attempt_resolves_latest_profile_without_a_snapshot(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "example.mkv").write_bytes(b"media")
    (media / "example.en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8"
    )
    database = SqliteDatabase(tmp_path / "work" / "cueweaver.sqlite3")
    profiles = ModelProfiles(database)
    profile = profiles.create("Selected", "OpenAI", [setting("model", "first")])
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
            profile["id"], "Selected", "OpenAI", [setting("model", "latest")]
        )
        assert captured[0] == {"provider": "OpenAI", "model": "first"}
        release_first.set()
        assert second_started.wait(5)
        assert captured[1] == {"provider": "OpenAI", "model": "latest"}
    finally:
        release_first.set()
        jobs.wait_closed()
