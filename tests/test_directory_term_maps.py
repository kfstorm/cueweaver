import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from test_term_map_helpers import make_client

from cueweaver.application.directory_term_maps import DirectoryTermMaps
from cueweaver.application.term_maps import TermMapDetail


def create_term_map(client: TestClient, name: str = "Characters") -> dict[str, object]:
    response = client.post(
        "/api/term-maps", json={"name": name, "content": {"Captain": "队长"}}
    )
    assert response.status_code == 200
    return response.json()


def bind_series(client: TestClient, term_map_id: object) -> None:
    assert (
        directory_request(client, "PUT", "Series", term_map_id=term_map_id).status_code
        == 200
    )


def directory_request(client: TestClient, method: str, path: str = "", **body: object):
    return client.request(
        method,
        "/api/term-maps/directory",
        params={"path": path} if method == "GET" else None,
        json={"path": path, **body} if method != "GET" else None,
    )


def test_directory_term_map_supports_root_local_inherited_and_remove(tmp_path: Path):
    client = make_client(tmp_path)
    (tmp_path / "media" / "Series" / "Season 1").mkdir(parents=True)
    root_map = create_term_map(client)
    child_map = create_term_map(client, "Season")

    root_binding = directory_request(client, "PUT", term_map_id=root_map["id"])
    assert root_binding.status_code == 200
    assert root_binding.json()["directory"] == ""
    assert root_binding.json()["local"]["id"] == root_map["id"]
    assert root_binding.json()["effective"]["id"] == root_map["id"]
    assert root_binding.json()["source_directory"] == ""

    inherited = directory_request(client, "GET", "Series/Season 1")
    assert inherited.status_code == 200
    assert inherited.json()["local"] is None
    assert inherited.json()["effective"]["id"] == root_map["id"]
    assert inherited.json()["source_directory"] == ""

    overridden = directory_request(
        client, "PUT", "Series/Season 1", term_map_id=child_map["id"]
    )
    assert overridden.json()["effective"]["id"] == child_map["id"]
    assert overridden.json()["source_directory"] == "Series/Season 1"

    removed = directory_request(client, "DELETE", "Series/Season 1")
    assert removed.status_code == 200
    assert (
        directory_request(client, "GET", "Series/Season 1").json()["effective"]["id"]
        == root_map["id"]
    )


def test_directory_term_map_persists_and_delete_cleans_bindings(tmp_path: Path):
    client = make_client(tmp_path)
    (tmp_path / "media" / "Series").mkdir(parents=True)
    (tmp_path / "media" / "Other").mkdir()
    term_map = create_term_map(client)
    bind_series(client, term_map["id"])
    assert (
        directory_request(
            client, "PUT", "Other", term_map_id=term_map["id"]
        ).status_code
        == 200
    )

    restarted = make_client(tmp_path)
    assert (
        directory_request(restarted, "GET", "Series").json()["effective"]["id"]
        == (term_map["id"])
    )
    assert (
        restarted.request(
            "DELETE",
            f"/api/term-maps/{term_map['id']}",
            json={"name": term_map["name"]},
        ).status_code
        == 200
    )
    assert directory_request(restarted, "GET", "Series").json() == {
        "directory": "Series",
        "local": None,
        "effective": None,
        "source_directory": None,
    }
    assert directory_request(restarted, "GET", "Other").json()["effective"] is None
    with sqlite3.connect(tmp_path / "work" / "cueweaver.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM directory_term_map_bindings"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM term_map_entries WHERE term_map_id = ?",
            (term_map["id"],),
        ).fetchone() == (0,)
    restarted_again = make_client(tmp_path)
    assert restarted_again.get(f"/api/term-maps/{term_map['id']}").status_code == 400
    assert (
        directory_request(restarted_again, "GET", "Series").json()["effective"] is None
    )


def test_directory_binding_follows_term_map_rename(tmp_path: Path):
    client = make_client(tmp_path)
    (tmp_path / "media" / "Series").mkdir(parents=True)
    term_map = create_term_map(client)
    bind_series(client, term_map["id"])

    renamed = client.patch(f"/api/term-maps/{term_map['id']}", json={"name": "People"})

    assert renamed.status_code == 200
    state = directory_request(client, "GET", "Series").json()
    assert state["local"]["id"] == term_map["id"]
    assert state["local"]["name"] == "People"
    assert state["effective"]["name"] == "People"


def test_stale_directory_binding_can_be_removed_without_recreating_directory(
    tmp_path: Path,
):
    client = make_client(tmp_path)
    directory = tmp_path / "media" / "Series"
    directory.mkdir(parents=True)
    term_map = create_term_map(client)
    bind_series(client, term_map["id"])
    directory.rename(tmp_path / "media" / "Other")

    removed = directory_request(client, "DELETE", "Series")

    assert removed.status_code == 200
    assert removed.json()["local"] is None
    assert directory_request(client, "GET", "Series").json()["effective"] is None


def test_stale_directory_binding_becomes_effective_when_path_returns(tmp_path: Path):
    client = make_client(tmp_path)
    directory = tmp_path / "media" / "Series"
    directory.mkdir(parents=True)
    term_map = create_term_map(client)
    bind_series(client, term_map["id"])
    directory.rmdir()

    missing = directory_request(client, "GET", "Series")
    directory.mkdir()
    returned = directory_request(client, "GET", "Series")

    assert missing.status_code == 200
    assert missing.json()["local"]["id"] == term_map["id"]
    assert returned.json()["effective"]["id"] == term_map["id"]


def test_unsafe_stale_directory_removal_does_not_change_safe_bindings(tmp_path: Path):
    client = make_client(tmp_path)
    (tmp_path / "media" / "Series").mkdir(parents=True)
    term_map = create_term_map(client)
    bind_series(client, term_map["id"])

    rejected = directory_request(client, "DELETE", "../Series")

    assert rejected.status_code == 400
    assert rejected.json()["error_code"] == "invalid_media_path"
    assert (
        directory_request(client, "GET", "Series").json()["local"]["id"]
        == term_map["id"]
    )


def test_directory_term_map_get_uses_one_bindings_snapshot(tmp_path: Path):
    class SnapshotStore:
        calls = 0

        def snapshot_bindings(self) -> dict[str, str]:
            self.calls += 1
            return {"": "root", "Series": "series"}

        def bind(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("not used")

        def remove(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("not used")

    details = {
        item.id: item
        for item in (
            TermMapDetail("root", "Root", 1, "2026-08-17T00:00:00Z", {}),
            TermMapDetail("series", "Series", 1, "2026-08-17T00:00:00Z", {}),
        )
    }

    class Resolver:
        def get(self, term_map_id: str) -> TermMapDetail:
            return details[term_map_id]

    store = SnapshotStore()
    state = DirectoryTermMaps(
        store,
        Resolver(),
        tmp_path / "media",
    ).get("Series/Season 1")

    assert state.local is None
    assert state.effective is not None
    assert state.effective.id == "series"
    assert state.source_directory == "Series"
    assert store.calls == 1


def test_directory_term_map_rejects_unsafe_missing_and_unknown_values(tmp_path: Path):
    client = make_client(tmp_path)
    invalid_paths = ["../outside", "/absolute", r"Series\\Season", "Series/../Season"]
    for path in invalid_paths:
        response = directory_request(client, "GET", path)
        assert response.status_code == 400
        assert response.json()["error_code"] == "invalid_media_path"

    missing_directory = directory_request(client, "PUT", "Missing", term_map_id="map")
    assert missing_directory.json()["error_code"] == "directory_not_found"
    missing_map = directory_request(client, "PUT", term_map_id="missing")
    assert missing_map.json()["error_code"] == "term_map_not_found"


def test_directory_term_map_uses_canonical_symlink_paths(tmp_path: Path):
    client = make_client(tmp_path)
    media_root = tmp_path / "media"
    (media_root / "Series").mkdir()
    (media_root / "alias").symlink_to(media_root / "Series", target_is_directory=True)
    term_map = create_term_map(client)

    assert (
        directory_request(client, "PUT", "alias", term_map_id=term_map["id"]).json()[
            "directory"
        ]
        == "Series"
    )
    assert (
        directory_request(client, "GET", "Series").json()["local"]["id"]
        == (term_map["id"])
    )


def test_directory_term_map_resolution_is_independent_of_target_language(
    tmp_path: Path,
):
    client = make_client(tmp_path)
    (tmp_path / "media" / "Series").mkdir(parents=True)
    term_map = create_term_map(client)
    bind_series(client, term_map["id"])

    english = client.get(
        "/api/term-maps/directory",
        params={"path": "Series", "target_language_code": "en"},
    )
    chinese = client.get(
        "/api/term-maps/directory",
        params={"path": "Series", "target_language_code": "zh-Hans"},
    )

    assert english.json() == chinese.json()
