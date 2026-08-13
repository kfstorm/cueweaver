from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueweaver.adapters.term_maps import FileTermMapStore
from cueweaver.application.errors import ServiceError
from cueweaver.product import create_product_app


class TranslatorFixture:
    available = True


def make_client(tmp_path: Path) -> TestClient:
    media_root = tmp_path / "media"
    media_root.mkdir(exist_ok=True)
    return TestClient(
        create_product_app(
            media_root,
            tmp_path / "work",
            TranslatorFixture(),
            static_root=_static_root(tmp_path),
        )
    )


def _static_root(tmp_path: Path) -> Path:
    static_root = tmp_path / "static"
    static_root.mkdir(exist_ok=True)
    (static_root / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    return static_root


def create_term_map(client: TestClient, name: str = "Characters") -> dict[str, object]:
    return client.post(
        "/api/term-maps", json={"name": name, "content": {"a": "b"}}
    ).json()


def test_term_map_can_be_created_inspected_and_recovered_after_restart(tmp_path: Path):
    client = make_client(tmp_path)
    created = client.post(
        "/api/term-maps",
        json={"name": "Characters", "content": {"Captain": "队长", "Ship": "舰船"}},
    )

    assert created.status_code == 200
    summary = created.json()
    assert summary["name"] == "Characters"
    assert summary["entry_count"] == 2
    assert summary["id"]
    detail = client.get(f"/api/term-maps/{summary['id']}")
    assert detail.json()["content"] == {"Captain": "队长", "Ship": "舰船"}

    restarted = make_client(tmp_path)
    assert restarted.get("/api/term-maps").json()["term_maps"] == [summary]
    assert restarted.get(f"/api/term-maps/{summary['id']}").json()["content"] == {
        "Captain": "队长",
        "Ship": "舰船",
    }


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"name": "Empty", "content": {}}, "non-empty"),
        ({"name": "Empty", "content": {"": "target"}}, "source keys"),
        ({"name": "Empty", "content": {"source": ""}}, "target values"),
        ({"name": "Empty", "content": {"Source": "a", "source": "b"}}, "regardless"),
        ({"name": "Empty", "content": []}, "JSON object"),
    ],
)
def test_term_map_creation_rejects_invalid_content(
    tmp_path: Path, body: dict[str, object], message: str
):
    response = make_client(tmp_path).post("/api/term-maps", json=body)

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_term_map"
    assert message.lower() in response.json()["message"].lower()


def test_term_map_creation_rejects_malformed_json_and_duplicate_names(tmp_path: Path):
    client = make_client(tmp_path)
    malformed = client.post(
        "/api/term-maps",
        content='{"name":"Broken","content":',
        headers={"content-type": "application/json"},
    )
    assert malformed.json()["error_code"] == "invalid_term_map"

    client.post("/api/term-maps", json={"name": "Names", "content": {"a": "b"}})
    duplicate = client.post(
        "/api/term-maps", json={"name": "nAmEs", "content": {"c": "d"}}
    )
    assert duplicate.json()["error_code"] == "duplicate_term_map_name"


def test_term_map_creation_rejects_a_non_object_upload(tmp_path: Path):
    response = make_client(tmp_path).post("/api/term-maps", json=[])

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_term_map"


def test_term_map_creation_rejects_oversized_content(tmp_path: Path):
    response = make_client(tmp_path).post(
        "/api/term-maps",
        json={"name": "Large", "content": {"source": "x" * (1024 * 1024)}},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_term_map"
    assert "1 MiB" in response.json()["message"]


def test_term_map_creation_rejects_oversized_raw_content_with_whitespace(
    tmp_path: Path,
):
    raw_content = "{\n" + (" " * (1024 * 1024)) + '"source":"target"\n}'
    response = make_client(tmp_path).post(
        "/api/term-maps",
        content=f'{{"name":"Large","content":{raw_content}}}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_term_map"


def test_term_map_creation_rejects_duplicate_source_keys(tmp_path: Path):
    response = make_client(tmp_path).post(
        "/api/term-maps",
        content='{"name":"Duplicate","content":{"Source":"one","Source":"two"}}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_term_map"


def test_term_map_creation_rejects_trailing_commas(tmp_path: Path):
    response = make_client(tmp_path).post(
        "/api/term-maps",
        content='{"name":"Trailing","content":{"a":"b"},}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_term_map"


def test_term_map_name_uniqueness_is_serialized_under_concurrent_creation(
    tmp_path: Path,
):
    def create() -> int:
        with make_client(tmp_path) as client:
            return client.post(
                "/api/term-maps",
                json={"name": "Concurrent", "content": {"a": "b"}},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: create(), range(2)))

    assert sorted(statuses) == [200, 400]


def test_term_map_can_be_renamed_without_changing_identity(tmp_path: Path):
    client = make_client(tmp_path)
    created = create_term_map(client)

    renamed = client.patch(f"/api/term-maps/{created['id']}", json={"name": "People"})

    assert renamed.status_code == 200
    assert renamed.json() == {
        **created,
        "name": "People",
        "updated_at": renamed.json()["updated_at"],
    }
    assert client.get(f"/api/term-maps/{created['id']}").json()["name"] == "People"


def test_term_map_replacement_is_persistent_and_updates_metadata(tmp_path: Path):
    client = make_client(tmp_path)
    created = create_term_map(client)

    replaced = client.put(
        f"/api/term-maps/{created['id']}", json={"content": {"Captain": "队长"}}
    )

    assert replaced.status_code == 200
    assert replaced.json()["id"] == created["id"]
    assert replaced.json()["name"] == "Characters"
    assert replaced.json()["entry_count"] == 1
    assert client.get(f"/api/term-maps/{created['id']}").json()["content"] == {
        "Captain": "队长"
    }
    restarted = make_client(tmp_path)
    assert restarted.get(f"/api/term-maps/{created['id']}").json()["content"] == {
        "Captain": "队长"
    }


def test_term_map_replacement_rejects_invalid_content_without_changing_old_content(
    tmp_path: Path,
):
    client = make_client(tmp_path)
    created = create_term_map(client)

    invalid = client.put(
        f"/api/term-maps/{created['id']}",
        json={"content": {"Source": "one", "source": "two"}},
    )

    assert invalid.status_code == 400
    assert invalid.json()["error_code"] == "invalid_term_map"
    assert client.get(f"/api/term-maps/{created['id']}").json()["content"] == {"a": "b"}


@pytest.mark.parametrize("failure_kind", ["content", "index"])
def test_term_map_replacement_failure_keeps_old_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str
):
    client = make_client(tmp_path)
    created = create_term_map(client)
    original_write = FileTermMapStore._write_json

    def fail_write(_store: object, path: Path, payload: object) -> None:
        fails = (
            path.name == "index.json"
            if failure_kind == "index"
            else path.name.startswith(f"{created['id']}.")
        )
        if fails:
            raise ServiceError("term_map_write_failed", "replacement write failed")
        original_write(path, payload)

    monkeypatch.setattr(FileTermMapStore, "_write_json", fail_write)

    response = client.put(
        f"/api/term-maps/{created['id']}", json={"content": {"c": "d"}}
    )

    assert response.json()["error_code"] == "term_map_write_failed"
    assert client.get(f"/api/term-maps/{created['id']}").json()["content"] == {"a": "b"}


def test_term_map_delete_requires_current_name_and_removes_resource(tmp_path: Path):
    client = make_client(tmp_path)
    created = client.post(
        "/api/term-maps", json={"name": "Characters", "content": {"a": "b"}}
    ).json()

    not_confirmed = client.request(
        "DELETE", f"/api/term-maps/{created['id']}", json={"name": "People"}
    )
    deleted = client.request(
        "DELETE", f"/api/term-maps/{created['id']}", json={"name": "Characters"}
    )
    after_delete = client.get(f"/api/term-maps/{created['id']}")

    assert not_confirmed.json()["error_code"] == "term_map_delete_confirmation_required"
    assert deleted.status_code == 200
    assert after_delete.json()["error_code"] == "term_map_not_found"


def test_term_map_rename_and_replacement_concurrently_preserve_both_changes(
    tmp_path: Path,
):
    client = make_client(tmp_path)
    created = client.post(
        "/api/term-maps", json={"name": "Characters", "content": {"a": "b"}}
    ).json()

    def rename() -> int:
        with make_client(tmp_path) as concurrent_client:
            return concurrent_client.patch(
                f"/api/term-maps/{created['id']}", json={"name": "People"}
            ).status_code

    def replace() -> int:
        with make_client(tmp_path) as concurrent_client:
            return concurrent_client.put(
                f"/api/term-maps/{created['id']}",
                json={"content": {"Captain": "队长", "Ship": "舰船"}},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda operation: operation(), (rename, replace)))

    detail = client.get(f"/api/term-maps/{created['id']}").json()
    assert sorted(statuses) == [200, 200]
    assert detail["name"] == "People"
    assert detail["content"] == {"Captain": "队长", "Ship": "舰船"}


def test_term_map_rename_rejects_duplicate_name_without_changing_resource(
    tmp_path: Path,
):
    client = make_client(tmp_path)
    first = client.post(
        "/api/term-maps", json={"name": "Characters", "content": {"a": "b"}}
    ).json()
    client.post("/api/term-maps", json={"name": "People", "content": {"c": "d"}})

    response = client.patch(f"/api/term-maps/{first['id']}", json={"name": "pEoPle"})

    assert response.json()["error_code"] == "duplicate_term_map_name"
    assert client.get(f"/api/term-maps/{first['id']}").json()["name"] == "Characters"


def test_term_map_rename_name_uniqueness_is_serialized_under_concurrency(
    tmp_path: Path,
):
    client = make_client(tmp_path)
    first = client.post(
        "/api/term-maps", json={"name": "First", "content": {"a": "b"}}
    ).json()
    second = client.post(
        "/api/term-maps", json={"name": "Second", "content": {"c": "d"}}
    ).json()

    def rename(term_map_id: str) -> int:
        with make_client(tmp_path) as concurrent_client:
            return concurrent_client.patch(
                f"/api/term-maps/{term_map_id}", json={"name": "Shared"}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(rename, (first["id"], second["id"])))

    assert sorted(statuses) == [200, 400]


def test_operations_after_delete_return_not_found(tmp_path: Path):
    client = make_client(tmp_path)
    created = client.post(
        "/api/term-maps", json={"name": "Characters", "content": {"a": "b"}}
    ).json()
    client.request(
        "DELETE", f"/api/term-maps/{created['id']}", json={"name": "Characters"}
    )

    for response in (
        client.patch(f"/api/term-maps/{created['id']}", json={"name": "People"}),
        client.put(f"/api/term-maps/{created['id']}", json={"content": {"c": "d"}}),
        client.request(
            "DELETE", f"/api/term-maps/{created['id']}", json={"name": "Characters"}
        ),
    ):
        assert response.json()["error_code"] == "term_map_not_found"
