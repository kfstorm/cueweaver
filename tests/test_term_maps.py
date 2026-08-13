from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
