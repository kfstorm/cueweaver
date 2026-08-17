from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

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
    term_map = create_term_map(client)
    bind_series(client, term_map["id"])

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


def test_directory_term_map_rejects_a_work_term_maps_symlink(tmp_path: Path):
    work_root = tmp_path / "work"
    outside = tmp_path / "outside"
    work_root.mkdir()
    outside.mkdir()
    (work_root / "term-maps").symlink_to(outside, target_is_directory=True)

    client = make_client(tmp_path)

    response = directory_request(client, "GET")

    assert response.status_code == 400
    assert response.json()["error_code"] == "directory_term_maps_unavailable"
    assert not (outside / "directory-bindings.json").exists()


def test_directory_term_map_get_uses_one_bindings_snapshot(tmp_path: Path):
    class SnapshotStore:
        def snapshot_bindings(self) -> dict[str, str]:
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

    state = DirectoryTermMaps(
        SnapshotStore(),
        Resolver(),
        tmp_path / "media",
    ).get("Series/Season 1")

    assert state.local is None
    assert state.effective is not None
    assert state.effective.id == "series"
    assert state.source_directory == "Series"


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


def test_directory_term_map_writes_are_serialized_last_successful_write_wins(
    tmp_path: Path,
):
    client = make_client(tmp_path)
    (tmp_path / "media" / "Series").mkdir(parents=True)
    first = create_term_map(client, "First")
    second = create_term_map(client, "Second")

    ready = Barrier(2)
    first_write_done = Event()

    def bind(term_map_id: object, final: bool) -> list[int]:
        with make_client(tmp_path) as concurrent_client:
            first_response = directory_request(
                concurrent_client, "PUT", "Series", term_map_id=term_map_id
            )
            ready.wait()
            if final:
                final_response = directory_request(
                    concurrent_client, "PUT", "Series", term_map_id=first["id"]
                )
                first_write_done.set()
            else:
                first_write_done.wait()
                final_response = directory_request(
                    concurrent_client, "PUT", "Series", term_map_id=second["id"]
                )
            return [first_response.status_code, final_response.status_code]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(bind, first["id"], True),
            executor.submit(bind, second["id"], False),
        ]
        statuses = [future.result() for future in futures]

    assert statuses == [[200, 200], [200, 200]]
    assert (
        directory_request(client, "GET", "Series").json()["local"]["id"] == second["id"]
    )
