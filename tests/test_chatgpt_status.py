"""Read-only credential observability while the deployment owns its lease."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PySubtrans.Auth.ChatGPTAuth import ChatGPTAuth

from cueweaver.application import CueWeaverApplication
from cueweaver.http import create_app


@pytest.fixture
def status_client(tmp_path, monkeypatch):
    application = CueWeaverApplication(work_root=tmp_path)
    path = tmp_path / "pysubtrans" / "chatgpt-auth.json"

    def forbidden(*args, **kwargs):
        pytest.fail("Status must not refresh tokens, write credentials, or use HTTP")

    monkeypatch.setattr(ChatGPTAuth, "GetAccessToken", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(type(ChatGPTAuth().store), "Save", forbidden)
    with TestClient(create_app(application)) as client:
        yield client, path


@pytest.mark.parametrize(
    ("data", "status"),
    [
        ({"host_id": "synthetic-host"}, "not_configured"),
        (
            {
                "client_id": "synthetic-registration",
                "credentials": {
                    "refresh_token": "synthetic-refresh",
                    "access_token": "synthetic-access",
                    "id_token": "synthetic-id",
                    "email": "subscriber@example.com",
                    "expires_at": 0,
                },
            },
            "credentials_present",
        ),
        ({"credentials": {"refresh_token": ""}}, "unavailable"),
        ({"credentials": "synthetic-invalid"}, "unavailable"),
        ([], "unavailable"),
    ],
)
def test_status_reports_only_local_presence(status_client, data, status):
    client, path = status_client
    path.write_text(json.dumps(data))
    before = path.stat()
    response = client.get("/api/chatgpt-auth/status")
    assert response.status_code == 200
    assert response.json() == {"status": status}
    assert response.headers["cache-control"] == "no-store"
    assert path.read_text() == json.dumps(data)
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.stat().st_mode == before.st_mode


def test_status_handles_missing_and_partially_written_credentials(status_client):
    client, path = status_client
    path.unlink()
    assert client.get("/api/chatgpt-auth/status").json() == {"status": "not_configured"}
    path.write_text('{"credentials":')
    assert client.get("/api/chatgpt-auth/status").json() == {"status": "unavailable"}
    path.write_text('{"credentials":{"refresh_token":"synthetic-refresh"}}')
    assert client.get("/api/chatgpt-auth/status").json() == {
        "status": "credentials_present"
    }


def test_status_rejects_symlink_and_exposes_no_management_routes(
    status_client, tmp_path
):
    client, path = status_client
    path.unlink()
    source = tmp_path / "synthetic.json"
    source.write_text('{"credentials":{"refresh_token":"synthetic-refresh"}}')
    path.symlink_to(source)
    assert client.get("/api/chatgpt-auth/status").json() == {"status": "unavailable"}
    paths = client.get("/openapi.json").json()["paths"]
    assert {key for key in paths if "chatgpt" in key} == {"/api/chatgpt-auth/status"}
    assert set(paths["/api/chatgpt-auth/status"]) == {"get"}


@pytest.mark.parametrize("error", [PermissionError, OSError, UnicodeDecodeError])
def test_status_recovers_from_transient_read_failures(
    status_client, monkeypatch, error
):
    client, path = status_client
    read_text = Path.read_text

    def failed_read(self, *args, **kwargs):
        if self == path:
            if error is UnicodeDecodeError:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
            raise error("synthetic private details")
        return read_text(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", failed_read)
        assert client.get("/api/chatgpt-auth/status").json() == {
            "status": "unavailable"
        }
    assert client.get("/api/chatgpt-auth/status").json() == {"status": "not_configured"}
