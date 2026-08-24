import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_term_map_helpers import make_client


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
    restarted = make_client(tmp_path)
    assert restarted.get(f"/api/term-maps/{created['id']}").json()["name"] == "People"


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


def test_term_maps_are_persisted_in_sqlite_without_json_snapshots(tmp_path: Path):
    client = make_client(tmp_path)
    created = create_term_map(client)

    with sqlite3.connect(tmp_path / "work" / "cueweaver.sqlite3") as connection:
        assert connection.execute(
            "SELECT name, entry_count FROM term_maps WHERE id = ?",
            (created["id"],),
        ).fetchone() == ("Characters", 1)
    assert not (tmp_path / "work" / "term-maps").exists()


def test_term_map_listing_preserves_creation_order(tmp_path: Path):
    client = make_client(tmp_path)
    for name in ("First", "Second", "Third"):
        create_term_map(client, name)

    assert [
        item["name"] for item in client.get("/api/term-maps").json()["term_maps"]
    ] == ["First", "Second", "Third"]


def test_legacy_term_map_json_is_imported_and_retired(tmp_path: Path):
    work_root = tmp_path / "work"
    term_maps_root = work_root / "term-maps"
    term_maps_root.mkdir(parents=True)
    (tmp_path / "media" / "Series").mkdir(parents=True)
    term_map_id = "legacy-map"
    (term_maps_root / f"{term_map_id}.json").write_text(
        '{"Captain":"队长"}', encoding="utf-8"
    )
    (term_maps_root / "index.json").write_text(
        (
            '[{"id":"legacy-map","name":"Legacy",'
            '"entry_count":1,"updated_at":"2026-08-24T00:00:00Z",'
            '"content_file":"legacy-map.json"}]'
        ),
        encoding="utf-8",
    )
    (term_maps_root / "directory-bindings.json").write_text(
        '{"Series":"legacy-map"}', encoding="utf-8"
    )

    client = make_client(tmp_path)

    assert client.get("/api/term-maps/legacy-map").json()["content"] == {
        "Captain": "队长"
    }
    assert (
        client.get("/api/term-maps/directory", params={"path": "Series"}).json()[
            "effective"
        ]["id"]
        == term_map_id
    )
    assert not any(term_maps_root.glob("*.json"))


def test_unknown_term_map_api_path_remains_a_structured_not_found(tmp_path: Path):
    response = make_client(tmp_path).post("/api/term-maps/map-1/unknown")

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "not_found",
        "message": "Resource not found",
    }


def test_unknown_term_map_item_post_remains_a_structured_not_found(tmp_path: Path):
    response = make_client(tmp_path).post("/api/term-maps/map-1")

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "not_found",
        "message": "Resource not found",
    }


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
    assert make_client(tmp_path).get(f"/api/term-maps/{created['id']}").json() == {
        "error_code": "term_map_not_found",
        "message": "Term map does not exist",
        "id": created["id"],
    }


@pytest.mark.parametrize("first_operation", ["rename", "replace"])
def test_term_map_ordered_operations_preserve_both_committed_fields(
    tmp_path: Path, first_operation: str
):
    client = make_client(tmp_path)
    created = create_term_map(client)

    operations = {
        "rename": lambda: client.patch(
            f"/api/term-maps/{created['id']}", json={"name": "People"}
        ),
        "replace": lambda: client.put(
            f"/api/term-maps/{created['id']}",
            json={"content": {"Captain": "队长", "Ship": "舰船"}},
        ),
    }
    second_operation = "replace" if first_operation == "rename" else "rename"
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(operations[first_operation])
        assert first.result().status_code == 200
        assert executor.submit(operations[second_operation]).result().status_code == 200

    detail = client.get(f"/api/term-maps/{created['id']}").json()
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
