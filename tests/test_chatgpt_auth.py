"""ChatGPT credentials belong to the deployment, never a Model Profile."""

import json
import time
from pathlib import Path

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from PySubtrans.Auth.ChatGPTAuth import PLAN_USAGE_SCOPE, ChatGPTAuth, ChatGPTAuthError

from cueweaver.application import CueWeaverApplication
from cueweaver.application.jobs import CreateJobRequest
from cueweaver.chatgpt_auth import main
from cueweaver.http import create_app
from cueweaver.translation import PySubtransTranslator


def session_data():
    return {
        "host_id": "urn:uuid:browser-machine",
        "client_id": "synthetic-client",
        "credentials": {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "id_token": "synthetic-id",
            "scope": PLAN_USAGE_SCOPE,
            "expires_at": time.time() + 7200,
            "email": "subscriber@example.com",
        },
    }


def save_session(auth, *, expired=False):
    data = auth.store.Load()
    data.update(
        {key: value for key, value in session_data().items() if key != "host_id"}
    )
    if expired:
        data["credentials"]["expires_at"] = 0
    auth.store.Save(data)
    return data


def test_application_preserves_protected_chatgpt_identity_across_restarts(tmp_path):
    work = tmp_path / "work"
    for _ in range(2):
        application = CueWeaverApplication(work_root=work)
        try:
            auth = ChatGPTAuth()
            assert auth.app_name == "CueWeaver"
            path = Path(auth.store.path)
            assert path == work / "pysubtrans" / "chatgpt-auth.json"
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
            data = json.loads(path.read_text())
            if _ == 0:
                host_id = data["host_id"]
                data["client_id"] = "synthetic-client"
                data["credentials"] = {"refresh_token": "synthetic-refresh"}
                auth.store.Save(data)
            else:
                assert data["host_id"] == host_id
                assert auth.is_signed_in
        finally:
            application.close()


def test_offline_import_preserves_server_host_and_excludes_concurrent_access(
    tmp_path, capsys
):
    work = tmp_path / "work"
    source = tmp_path / "transfer.json"
    source.write_text(json.dumps(session_data()))
    assert main(["--work-root", str(work), "init"]) == 0
    path = work / "pysubtrans" / "chatgpt-auth.json"
    host_id = json.loads(path.read_text())["host_id"]
    assert main(["--work-root", str(work), "import", str(source)]) == 0
    imported = json.loads(path.read_text())
    assert imported["host_id"] == host_id
    assert imported["host_id"] != session_data()["host_id"]
    assert imported["credentials"] == json.loads(source.read_text())["credentials"]
    assert main(["--work-root", str(work), "import", str(source)]) == 1
    application = CueWeaverApplication(work_root=work)
    try:
        assert main(["--work-root", str(work), "sign-out"]) == 1
        assert ChatGPTAuth().is_signed_in
    finally:
        application.close()
    assert "synthetic-access" not in capsys.readouterr().out


def test_cli_uses_upstream_sign_in_and_reports_unconfirmed_revocation(
    tmp_path, monkeypatch, capsys
):
    def sign_in(auth):
        save_session(auth)

    monkeypatch.setattr(ChatGPTAuth, "SignIn", sign_in)
    assert main(["--work-root", str(tmp_path), "sign-in"]) == 0
    assert main(["--work-root", str(tmp_path), "status"]) == 0

    def unavailable(*_args, **_kwargs):
        raise ChatGPTAuthError("synthetic-access must not be logged")

    monkeypatch.setattr(ChatGPTAuth, "_get_json", unavailable)
    assert main(["--work-root", str(tmp_path), "sign-out"]) == 1
    output = capsys.readouterr()
    assert "Revocation unconfirmed" in output.out
    assert "synthetic-access" not in output.out + output.err
    assert not ChatGPTAuth().is_signed_in


@pytest.mark.parametrize("invalid", ["not-json", "[]", '{"credentials": {}}'])
def test_invalid_import_does_not_replace_destination_identity(
    tmp_path, capsys, invalid
):
    assert main(["--work-root", str(tmp_path), "init"]) == 0
    path = tmp_path / "pysubtrans" / "chatgpt-auth.json"
    before = path.read_bytes()
    source = tmp_path / "transfer.json"
    source.write_text(invalid)
    assert main(["--work-root", str(tmp_path), "import", str(source)]) == 1
    assert path.read_bytes() == before
    assert invalid not in capsys.readouterr().err


@pytest.mark.parametrize("link_directory", [False, True])
def test_auth_storage_cannot_follow_a_symlink_outside_work_root(
    tmp_path, link_directory
):
    outside = tmp_path / "outside"
    outside.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    if link_directory:
        (work / "pysubtrans").symlink_to(outside, target_is_directory=True)
    else:
        (work / "pysubtrans").mkdir()
        (work / "pysubtrans" / "chatgpt-auth.json").symlink_to(outside / "secret.json")
    with pytest.raises(ValueError, match="symbolic link"):
        CueWeaverApplication(work_root=work)
    assert not list(outside.iterdir())


def test_openai_options_switch_modes_without_exposing_credentials(
    tmp_path, monkeypatch
):
    application = CueWeaverApplication(work_root=tmp_path)
    save_session(ChatGPTAuth())
    monkeypatch.setattr(ChatGPTAuth, "ListModels", lambda _self: ["gpt-synthetic"])
    try:
        with TestClient(create_app(application)) as client:
            response = client.post(
                "/api/model-profile-options",
                json={
                    "provider": "OpenAI",
                    "settings": {"use_chatgpt_plan": True, "api_key": "old-api-key"},
                },
            )
            assert response.status_code == 200
            body = response.json()
            keys = {option["key"] for option in body["options"]}
            assert keys == {"use_chatgpt_plan", "model", "reasoning_effort"}
            assert body["setting_updates"] == {"model": "gpt-synthetic"}
            saved = client.post(
                "/api/model-profiles",
                json={
                    "name": "Subscription",
                    "provider": "OpenAI",
                    "settings": [
                        {"key": "use_chatgpt_plan", "value": True},
                        {"key": "model", "value": "gpt-synthetic"},
                    ],
                },
            )
            assert saved.status_code == 200
            for key in (
                "access_token",
                "refresh_token",
                "id_token",
                "code_verifier",
                "chatgpt_account",
                "api_key",
            ):
                invalid = client.post(
                    "/api/model-profiles",
                    json={
                        "name": "Invalid",
                        "provider": "OpenAI",
                        "settings": [
                            {"key": "use_chatgpt_plan", "value": True},
                            {"key": key, "value": "synthetic-secret"},
                        ],
                    },
                )
                assert invalid.status_code == 400
            monkeypatch.setenv("OPENAI_USE_CHATGPT_PLAN", "true")
            api = client.post(
                "/api/model-profile-options",
                json={"provider": "OpenAI", "settings": {}},
            )
            assert "api_key" in {option["key"] for option in api.json()["options"]}
            for result in (response, saved, client.get("/api/model-profiles")):
                for secret in (
                    "synthetic-access",
                    "synthetic-refresh",
                    "synthetic-id",
                    "subscriber@example.com",
                ):
                    assert secret not in result.text
    finally:
        application.close()


@pytest.mark.parametrize("signed_in", [False, True])
def test_missing_or_expired_auth_fails_before_project_creation(
    tmp_path, monkeypatch, signed_in
):
    application = CueWeaverApplication(work_root=tmp_path)
    if signed_in:

        def expired(*_args, **_kwargs):
            raise ChatGPTAuthError("synthetic-refresh")

        monkeypatch.setattr(ChatGPTAuth, "GetAccessToken", expired)
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    try:
        with pytest.raises(ValueError, match="ChatGPT session unavailable") as failure:
            PySubtransTranslator().translate(
                source,
                "fr",
                settings={"provider": "OpenAI", "use_chatgpt_plan": True},
                work_directory=tmp_path / "translation",
            )
        assert "synthetic-refresh" not in str(failure.value)
        assert not (tmp_path / "translation").exists()
    finally:
        application.close()


@pytest.fixture
def plan_responses(monkeypatch):
    requests = []
    text = "#1\nOriginal>\nHello\nTranslation>\nBonjour\n"

    def respond(request):
        assert request.url == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        assert body["stream"] is True
        if request.headers["authorization"] == "Bearer synthetic-access":
            assert body["store"] is False
        else:
            assert request.headers["authorization"] == "Bearer synthetic-api-key"
            assert "store" not in body
        assert isinstance(body["input"], list)
        assert (
            not {"temperature", "max_output_tokens", "previous_response_id"}
            & body.keys()
        )
        requests.append(body)
        events = [
            {
                "type": "response.output_text.delta",
                "delta": text,
                "sequence_number": 0,
                "item_id": "msg_synthetic",
                "output_index": 0,
                "content_index": 0,
            },
            {
                "type": "response.completed",
                "sequence_number": 1,
                "response": {
                    "id": "resp_synthetic",
                    "object": "response",
                    "created_at": 0,
                    "status": "completed",
                    "model": "gpt-synthetic",
                    "usage": None,
                    "output": [
                        {
                            "id": "msg_synthetic",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {"type": "output_text", "text": text, "annotations": []}
                            ],
                        }
                    ],
                },
            },
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(event)}\n\n" for event in events),
        )

    original = openai.OpenAI
    monkeypatch.setattr(openai, "api_key", None)
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: original(
            **{
                **kwargs,
                "http_client": httpx.Client(transport=httpx.MockTransport(respond)),
            },
        ),
    )
    monkeypatch.setattr(ChatGPTAuth, "ListModels", lambda _self: ["gpt-synthetic"])
    return requests


def test_api_key_translation_remains_default_even_with_plan_environment(
    tmp_path, monkeypatch, plan_responses
):
    monkeypatch.setenv("OPENAI_USE_CHATGPT_PLAN", "true")

    def unexpected_auth(*_args, **_kwargs):
        pytest.fail("API key translation must not load ChatGPT credentials")

    monkeypatch.setattr(ChatGPTAuth, "GetAccessToken", unexpected_auth)
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    result = PySubtransTranslator().translate(
        source,
        "fr",
        settings={
            "provider": "OpenAI",
            "api_key": "synthetic-api-key",
            "model": "gpt-synthetic",
            "stream_responses": True,
        },
        work_directory=tmp_path / "translation",
    )
    assert b"Bonjour" in result
    assert len(plan_responses) == 1


def test_revoked_session_returns_safe_actionable_http_error(
    tmp_path, monkeypatch, caplog
):
    application = CueWeaverApplication(work_root=tmp_path)
    data = save_session(ChatGPTAuth(), expired=True)

    def revoked(*_args, **_kwargs):
        raise ChatGPTAuthError("synthetic-refresh", error_code="invalid_grant")

    monkeypatch.setattr(ChatGPTAuth, "_post_form", revoked)
    try:
        with TestClient(create_app(application)) as client:
            response = client.post(
                "/api/model-profile-options",
                json={"provider": "OpenAI", "settings": {"use_chatgpt_plan": True}},
            )
        assert response.status_code == 400
        assert response.json()["error_code"] == "chatgpt_session_unavailable"
        assert "cueweaver-chatgpt" in response.json()["message"]
        assert "synthetic-refresh" not in response.text + caplog.text
        assert not ChatGPTAuth().is_signed_in
        assert ChatGPTAuth().store.Load()["host_id"] == data["host_id"]
    finally:
        application.close()


def test_plan_translation_refreshes_after_restart_and_checkpoints_without_tokens(
    tmp_path, monkeypatch, plan_responses, caplog
):
    application = CueWeaverApplication(work_root=tmp_path / "work")
    save_session(ChatGPTAuth(), expired=True)
    application.close()
    refreshes = []

    def refresh(_auth, _url, form, **_kwargs):
        refreshes.append(form)
        assert form["grant_type"] == "refresh_token"
        assert form["client_id"] == "synthetic-client"
        return {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-rotated",
            "expires_in": 7200,
        }

    monkeypatch.setattr(ChatGPTAuth, "_post_form", refresh)
    application = CueWeaverApplication(work_root=tmp_path / "work")
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    snapshots = []
    try:
        for _ in range(2):
            result = PySubtransTranslator().translate(
                source,
                "fr",
                settings={
                    "provider": "OpenAI",
                    "use_chatgpt_plan": True,
                    "model": "gpt-synthetic",
                },
                work_directory=tmp_path / "translation",
                on_progress=snapshots.append,
            )
            assert b"Bonjour" in result
        assert (
            len(plan_responses) == 1
        )  # Completed checkpoints are resumed without inference.
        assert len(refreshes) == 1
        assert ChatGPTAuth().credentials.refresh_token == "synthetic-rotated"
        assert snapshots[-1].completed_lines == snapshots[-1].total_lines == 1
        checkpoint = next((tmp_path / "translation").rglob("*.subtrans")).read_text()
        for secret in (
            "synthetic-access",
            "synthetic-refresh",
            "synthetic-rotated",
            "synthetic-id",
        ):
            assert secret not in checkpoint + caplog.text
    finally:
        application.close()


def test_job_translates_through_chatgpt_profile(tmp_path, plan_responses):
    media = tmp_path / "media"
    media.mkdir()
    (media / "Example.mkv").touch()
    (media / "Example.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    application = CueWeaverApplication(work_root=tmp_path / "work", media_root=media)
    save_session(ChatGPTAuth())
    try:
        profile = application.model_profiles.create(
            "Plan",
            "OpenAI",
            [
                {"key": "use_chatgpt_plan", "value": True},
                {"key": "model", "value": "gpt-synthetic"},
            ],
        )
        job = application.jobs.create(
            CreateJobRequest(
                media_path="Example.mkv",
                subtitle_path="Example.en.srt",
                target_language_code="fr",
                model_profile_id=profile["id"],
                term_map_mode="none",
            )
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            detail = application.jobs.get(job["id"])
            if detail["status"] in {"Completed", "Failed"}:
                break
            time.sleep(0.01)
        assert detail["status"] == "Completed", detail
        assert b"Bonjour" in (media / "Example.fr.srt").read_bytes()
        assert len(plan_responses) == 1
        with TestClient(create_app(application)) as client:
            payload = (
                client.get(f"/api/jobs/{job['id']}").text
                + client.get("/api/model-profiles").text
            )
        assert "synthetic-access" not in payload
        assert "synthetic-refresh" not in payload
    finally:
        application.close()
