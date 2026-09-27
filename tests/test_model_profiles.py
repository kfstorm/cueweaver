"""Public Model Profile semantics across persistence and inheritance."""

import threading

import pytest
from fastapi.testclient import TestClient

from cueweaver.application import CueWeaverApplication
from cueweaver.application.database import SqliteDatabase
from cueweaver.application.errors import ServiceError
from cueweaver.application.jobs import CreateJobRequest, Jobs
from cueweaver.application.model_profiles import ModelProfiles
from cueweaver.http import create_app


def literal(key: str, value: object) -> dict[str, object]:
    return {"key": key, "kind": "literal", "value": value}


def unset(key: str) -> dict[str, object]:
    return {"key": key, "kind": "unset", "value": None}


def test_inheritance_types_and_tombstones_round_trip(tmp_path):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    root = profiles.create(
        "Shared",
        None,
        False,
        [
            literal("provider", "OpenAI"),
            literal("max_threads", 2),
            literal(
                "provider_settings", {"OpenAI": {"model": "small", "temperature": 0.5}}
            ),
        ],
    )
    child = profiles.create(
        "Shared",
        root["id"],
        True,
        [
            unset("max_threads"),
            literal("provider_settings", {"OpenAI": {"api_key": "synthetic-value"}}),
            literal("enabled", True),
            literal("tags", ["a", "b"]),
        ],
    )

    assert len(child["id"]) == 32
    assert profiles.resolve(child["id"]) == {
        "provider": "OpenAI",
        "provider_settings": {"OpenAI": {"api_key": "synthetic-value"}},
        "enabled": True,
        "tags": ["a", "b"],
    }
    assert next(
        item
        for item in profiles.get(child["id"])["effective_settings"]
        if item["key"] == "provider"
    )["source"] == {"id": root["id"], "name": "Shared"}
    with pytest.raises(ServiceError) as error:
        profiles.delete(root["id"])
    assert error.value.error_code == "model_profile_in_use"

    profiles.replace(child["id"], "Shared", root["id"], True, [])
    assert profiles.resolve(child["id"])["max_threads"] == 2


def test_replacement_rolls_back_invalid_provider_and_cycle(tmp_path):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    root = profiles.create("Root", None, False, [literal("provider", "OpenAI")])
    child = profiles.create("Child", root["id"], True, [])
    with pytest.raises(ServiceError) as error:
        profiles.replace(root["id"], "Root", None, False, [unset("provider")])
    assert error.value.error_code == "invalid_model_profile_provider"
    assert profiles.resolve(child["id"])["provider"] == "OpenAI"

    with pytest.raises(ServiceError) as error:
        profiles.replace(root["id"], "Root", child["id"], False, root["settings"])
    assert error.value.error_code == "model_profile_cycle"


def test_selectable_requires_local_registry_provider_but_no_model(tmp_path):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    base = profiles.create("Incomplete base", None, False, [])
    with pytest.raises(ServiceError) as error:
        profiles.create("Child", base["id"], True, [])
    assert error.value.error_code == "invalid_model_profile_provider"

    selected = profiles.create(
        "Ready", base["id"], True, [literal("provider", "OpenAI")]
    )
    assert profiles.resolve(selected["id"]) == {"provider": "OpenAI"}

    with pytest.raises(ServiceError) as error:
        profiles.replace(
            selected["id"],
            "Ready",
            base["id"],
            True,
            [literal("provider", "Unknown provider")],
        )
    assert error.value.error_code == "invalid_model_profile_provider"
    assert profiles.resolve(selected["id"]) == {"provider": "OpenAI"}


@pytest.mark.parametrize(
    "settings",
    [
        [literal("prompt", "override")],
        [literal("provider", None)],
        [unset("provider"), literal("provider", "OpenAI")],
        [{"key": "provider", "kind": "future", "value": "OpenAI"}],
        [literal("temperature", float("nan"))],
    ],
)
def test_invalid_settings_rejected(tmp_path, settings):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    with pytest.raises(ServiceError) as error:
        profiles.create("Example", None, False, settings)
    assert error.value.error_code == "invalid_model_profile"


def test_nested_settings_accept_string_lists_and_null(tmp_path):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    nested = {"provider": {"labels": ["one", "two"], "optional": None}}
    profile = profiles.create("Example", None, False, [literal("options", nested)])

    assert profiles.resolve(profile["id"])["options"] == nested


@pytest.mark.parametrize("items", [[1], [True], [{}], ["valid", 1]])
def test_nested_settings_reject_non_string_lists(tmp_path, items):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    with pytest.raises(ServiceError) as error:
        profiles.create(
            "Example", None, False, [literal("options", {"deep": {"items": items}})]
        )
    assert error.value.error_code == "invalid_model_profile"


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nested_settings_reject_non_finite_numbers(tmp_path, number):
    profiles = ModelProfiles(SqliteDatabase(tmp_path / "app.sqlite3"))
    with pytest.raises(ServiceError) as error:
        profiles.create("Example", None, False, [literal("options", {"value": number})])
    assert error.value.error_code == "invalid_model_profile"


def test_profile_api_and_job_reference(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "example.mkv").write_bytes(b"media")
    (media / "example.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")

    class Translator:
        def translate(self, _source, _target, **_kwargs):
            return b"1\n00:00:00,000 --> 00:00:01,000\nTranslated\n"

    app = CueWeaverApplication(Translator(), tmp_path / "work", media)
    try:
        with TestClient(create_app(app, media)) as client:
            base = {
                "name": "Base",
                "parent_id": None,
                "selectable": False,
                "settings": [
                    literal("provider", "OpenAI"),
                    literal("api_key", "synthetic-key"),
                ],
            }
            profile = client.post("/api/model-profiles", json=base).json()
            assert (
                client.get(f"/api/model-profiles/{profile['id']}").json()["settings"][
                    1
                ]["value"]
                == "OpenAI"
            )
            child = client.post(
                "/api/model-profiles",
                json={
                    "name": "Derived",
                    "parent_id": profile["id"],
                    "selectable": True,
                    "settings": [],
                },
            ).json()
            payload = {
                "media_path": "example.mkv",
                "subtitle_path": "example.en.srt",
                "target_language_code": "zh",
                "term_map_mode": "none",
                "output_conflict_policy": "append-number",
                "model_profile_id": child["id"],
            }
            created = client.post("/api/jobs", json=payload)
            assert created.status_code == 200
            assert created.json()["request"]["model_profile_id"] == child["id"]
            assert (
                client.get(f"/api/model-profiles/{child['id']}").json()["deletable"]
                is False
            )
            assert (
                client.delete(
                    f"/api/model-profiles/{child['id']}",
                    headers={"content-type": "application/json"},
                ).json()["error_code"]
                == "model_profile_in_use"
            )
            assert (
                client.post(
                    "/api/jobs", json={**payload, "model_profile_id": profile["id"]}
                ).json()["error_code"]
                == "model_profile_not_selectable"
            )
    finally:
        app.close()


def test_queued_attempt_resolves_latest_parent_and_keeps_settings_fixed(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "example.mkv").write_bytes(b"media")
    (media / "example.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    database = SqliteDatabase(tmp_path / "work" / "cueweaver.sqlite3")
    profiles = ModelProfiles(database)
    base = profiles.create(
        "Base", None, False, [literal("provider", "OpenAI"), literal("max_threads", 2)]
    )
    selected = profiles.create("Selected", base["id"], True, [])
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
            selected["id"],
            output_conflict_policy="append-number",
        )
        first = jobs.create(request)
        assert first_started.wait(5)
        second = jobs.create(request)
        profiles.replace(
            base["id"],
            "Base",
            None,
            False,
            [literal("provider", "OpenAI"), literal("max_threads", 4)],
        )
        assert captured[0]["max_threads"] == 2
        release_first.set()
        assert second_started.wait(5)
        assert captured[1]["max_threads"] == 4
        assert jobs.get(first["id"])["request"]["model_profile_id"] == selected["id"]
        assert jobs.get(second["id"])["request"]["model_profile_id"] == selected["id"]
    finally:
        release_first.set()
        jobs.wait_closed()
