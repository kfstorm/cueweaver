from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueweaver.product import (
    create_development_app_from_env,
    create_product_app,
    create_product_app_from_env,
)


class TranslatorFixture:
    def __init__(
        self, *, available: bool = True, availability_message: str | None = None
    ) -> None:
        self.available = available
        self.availability_message = availability_message
        self.secret = "provider-secret-sentinel"

    def translate(self, *_args, **_kwargs) -> bytes:
        return b"1\n00:00:00,000 --> 00:00:01,000\nTranslated\n"


def configured_roots(tmp_path: Path) -> tuple[Path, Path]:
    media_root = tmp_path / "media"
    media_root.mkdir()
    return media_root, tmp_path / "work"


def static_fixture(tmp_path: Path) -> Path:
    static_root = tmp_path / "static"
    static_root.mkdir(exist_ok=True)
    (static_root / "index.html").write_text(
        '<!doctype html><div id="root"></div>', encoding="utf-8"
    )
    return static_root


def product_app(tmp_path: Path, translator: TranslatorFixture | None = None):
    media_root, work_root = configured_roots(tmp_path)
    return create_product_app(
        media_root,
        work_root,
        TranslatorFixture() if translator is None else translator,
        static_root=static_fixture(tmp_path),
    )


@pytest.mark.parametrize(
    ("media_value", "work_value", "message"),
    [
        (None, "/work", "CUEWEAVER_MEDIA_ROOT is required"),
        ("media", "/work", "CUEWEAVER_MEDIA_ROOT must be an absolute path"),
        ("/media", None, "CUEWEAVER_WORK_ROOT is required"),
        ("/media", "work", "CUEWEAVER_WORK_ROOT must be an absolute path"),
    ],
)
def test_product_startup_requires_absolute_root_environment_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    media_value: str | None,
    work_value: str | None,
    message: str,
):
    if media_value is None:
        monkeypatch.delenv("CUEWEAVER_MEDIA_ROOT", raising=False)
    else:
        monkeypatch.setenv("CUEWEAVER_MEDIA_ROOT", media_value)
    if work_value is None:
        monkeypatch.delenv("CUEWEAVER_WORK_ROOT", raising=False)
    else:
        monkeypatch.setenv("CUEWEAVER_WORK_ROOT", work_value)

    with pytest.raises(ValueError, match=message):
        create_product_app_from_env(
            translator=TranslatorFixture(), static_root=static_fixture(tmp_path)
        )


def test_product_startup_validates_media_and_creates_work_root(tmp_path: Path):
    missing_media = tmp_path / "missing"
    work_root = tmp_path / "work"

    with pytest.raises(ValueError, match="Media root must be a readable directory"):
        create_product_app(
            missing_media,
            work_root,
            TranslatorFixture(),
            static_root=static_fixture(tmp_path),
        )

    media_root = tmp_path / "media"
    media_root.mkdir()
    create_product_app(
        media_root,
        work_root,
        TranslatorFixture(),
        static_root=static_fixture(tmp_path),
    )

    assert work_root.is_dir()
    assert list(work_root.iterdir()) == []


@pytest.mark.parametrize("operation", ["read", "write", "mkdir", "replace"])
def test_product_startup_rejects_work_root_capability_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
):
    media_root, work_root = configured_roots(tmp_path)
    static_root = static_fixture(tmp_path)
    original_read_bytes = Path.read_bytes
    original_write_bytes = Path.write_bytes

    if operation == "read":

        def fail_read(path: Path) -> bytes:
            if path.parent.parent == work_root:
                raise OSError("read probe failed")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", fail_read)
    elif operation == "write":

        def fail_write(path: Path, data: bytes) -> int:
            if path.parent.parent == work_root:
                raise OSError("write probe failed")
            return original_write_bytes(path, data)

        monkeypatch.setattr(Path, "write_bytes", fail_write)
    elif operation == "mkdir":
        original_mkdir = Path.mkdir

        def fail_mkdir(path: Path, *args: object, **kwargs: object) -> None:
            if path == work_root:
                raise OSError("mkdir probe failed")
            original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    else:
        original_replace = Path.replace

        def fail_replace(source: Path, destination: Path) -> Path:
            if source.parent.parent == work_root:
                raise OSError("replace probe failed")
            return original_replace(source, destination)

        monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(ValueError, match="Work root must support"):
        create_product_app(
            media_root, work_root, TranslatorFixture(), static_root=static_root
        )


def test_product_startup_rejects_a_work_root_that_is_not_a_directory(
    tmp_path: Path,
):
    media_root, work_root = configured_roots(tmp_path)
    work_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="Work root must support"):
        create_product_app(
            media_root,
            work_root,
            TranslatorFixture(),
            static_root=static_fixture(tmp_path),
        )


def test_product_status_is_ready_and_redacts_runtime_configuration(tmp_path: Path):
    media_root, work_root = configured_roots(tmp_path)
    translator = TranslatorFixture()
    client = TestClient(
        create_product_app(
            media_root,
            work_root,
            translator,
            static_root=static_fixture(tmp_path),
        )
    )

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "api": {"ready": True},
        "roots": {"ready": True},
        "translation_provider": {"ready": True},
        "worker": {"ready": True, "mode": "single"},
        "job_records": {
            "corrupt": {"count": 0, "location": "jobs/corrupt"},
            "unsupported": {"count": 0, "location": "jobs/unsupported"},
        },
    }
    serialized = response.text
    assert str(media_root) not in serialized
    assert str(work_root) not in serialized
    assert translator.secret not in serialized


def test_product_status_reports_quarantined_job_record_counts(tmp_path: Path):
    media_root, work_root = configured_roots(tmp_path)
    jobs_root = work_root / "jobs"
    jobs_root.mkdir(parents=True)
    (jobs_root / "broken.json").write_bytes(b"broken")
    future_bytes = (
        b'{"schema_version": 2, "id": "future", "status": "Failed", '
        b'"request": {"media_path": "Movie.mkv", '
        b'"subtitle_path": "Movie.en.srt", "target_language_code": "zh-Hans", '
        b'"output_path": "Movie.zh-Hans.srt", "source_format": "srt"}, '
        b'"attempt": 1, "created_at": "2026-08-13T12:00:00Z", '
        b'"started_at": null, "finished_at": null, "error": null, '
        b'"queue_sequence": 0}'
    )
    (jobs_root / "future.json").write_bytes(future_bytes)

    client = TestClient(
        create_product_app(
            media_root,
            work_root,
            TranslatorFixture(),
            static_root=static_fixture(tmp_path),
        )
    )

    assert client.get("/api/status").json()["job_records"] == {
        "corrupt": {"count": 1, "location": "jobs/corrupt"},
        "unsupported": {"count": 1, "location": "jobs/unsupported"},
    }
    assert (jobs_root / "corrupt" / "broken.json").read_bytes() == b"broken"
    assert (jobs_root / "unsupported" / "future.json").read_bytes() == future_bytes


def test_unconfigured_provider_keeps_product_available_with_actionable_status(
    tmp_path: Path,
):
    client = TestClient(product_app(tmp_path, TranslatorFixture(available=False)))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["translation_provider"] == {
        "ready": False,
        "message": (
            "Set PROVIDER and the matching provider environment variables, then "
            "restart CueWeaver."
        ),
    }
    assert response.json()["api"] == {"ready": True}


def test_provider_status_exposes_specific_local_configuration_message(tmp_path: Path):
    client = TestClient(
        product_app(
            tmp_path,
            TranslatorFixture(
                available=False,
                availability_message="Set GEMINI_API_KEY for PROVIDER=Gemini, then restart CueWeaver.",
            ),
        )
    )

    assert client.get("/api/status").json()["translation_provider"] == {
        "ready": False,
        "message": "Set GEMINI_API_KEY for PROVIDER=Gemini, then restart CueWeaver.",
    }


@pytest.mark.parametrize("path", ["/", "/translate", "/jobs", "/term-maps"])
def test_product_serves_client_routes_from_the_spa(tmp_path: Path, path: str):
    response = TestClient(product_app(tmp_path)).get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text


@pytest.mark.parametrize("path", ["/api", "/api/unknown"])
def test_product_does_not_fallback_api_paths_to_spa(tmp_path: Path, path: str):
    response = TestClient(product_app(tmp_path)).get(path)

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "not_found",
        "message": "Resource not found",
    }


@pytest.mark.parametrize("path", ["/api", "/api/unknown"])
def test_product_rejects_unknown_api_head_paths(tmp_path: Path, path: str):
    response = TestClient(product_app(tmp_path)).head(path)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/api/media/browse"), ("POST", "/api/status")]
)
def test_product_preserves_method_mismatch_for_known_api_paths(
    tmp_path: Path, method: str, path: str
):
    response = TestClient(product_app(tmp_path)).request(method, path)

    assert response.status_code == 400
    assert response.json() == {
        "error_code": "invalid_request",
        "message": "Request failed",
    }


def test_development_factory_does_not_require_static_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root, work_root = configured_roots(tmp_path)
    monkeypatch.setenv("CUEWEAVER_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("CUEWEAVER_WORK_ROOT", str(work_root))

    client = TestClient(create_development_app_from_env(translator=TranslatorFixture()))

    response = client.get("/api/status")

    assert response.status_code == 200


def test_development_factory_returns_structured_api_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_root, work_root = configured_roots(tmp_path)
    monkeypatch.setenv("CUEWEAVER_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("CUEWEAVER_WORK_ROOT", str(work_root))

    response = TestClient(
        create_development_app_from_env(translator=TranslatorFixture())
    ).get("/api/unknown")

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "not_found",
        "message": "Resource not found",
    }


@pytest.mark.parametrize("path", ["/api/discover", "/api/extract", "/api/translate"])
def test_product_removes_explicit_path_business_routes(tmp_path: Path, path: str):
    media_root, work_root = configured_roots(tmp_path)
    response = TestClient(
        create_product_app(
            media_root,
            work_root,
            TranslatorFixture(),
            static_root=static_fixture(tmp_path),
        )
    ).post(path, json={})

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "not_found",
        "message": "Resource not found",
    }


@pytest.mark.parametrize("path", ["/api/discover", "/api/extract", "/api/translate"])
def test_development_app_removes_explicit_path_business_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
):
    media_root, work_root = configured_roots(tmp_path)
    monkeypatch.setenv("CUEWEAVER_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("CUEWEAVER_WORK_ROOT", str(work_root))
    response = TestClient(
        create_development_app_from_env(translator=TranslatorFixture())
    ).post(path, json={})

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "not_found",
        "message": "Resource not found",
    }


def test_environment_factory_preserves_a_falsy_injected_translator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class FalsyTranslator(TranslatorFixture):
        def __bool__(self) -> bool:
            return False

    media_root, work_root = configured_roots(tmp_path)
    monkeypatch.setenv("CUEWEAVER_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("CUEWEAVER_WORK_ROOT", str(work_root))
    response = TestClient(
        create_product_app_from_env(
            translator=FalsyTranslator(available=False),
            static_root=static_fixture(tmp_path),
        )
    ).get("/api/status")

    assert response.json()["translation_provider"]["ready"] is False


def test_product_browse_api_returns_relative_entries_and_rejects_traversal(
    tmp_path: Path,
):
    media_root, work_root = configured_roots(tmp_path)
    (media_root / "Show 2").mkdir()
    (media_root / "Movie 10.mkv").write_bytes(b"media")
    (media_root / "Movie 2.MP4").write_bytes(b"media")
    (media_root / "Movie 2.nfo").write_text(
        "<movie><title>Movie label</title></movie>",
        encoding="utf-8",
    )
    (media_root / "movie.nfo").write_text(
        "<movie><title>Fallback label</title><premiered>2023-01-01</premiered></movie>",
        encoding="utf-8",
    )
    (media_root / "inside").mkdir()
    (media_root / "inside" / "Episode.mkv").write_bytes(b"media")
    (media_root / "inside-link").symlink_to(
        media_root / "inside", target_is_directory=True
    )
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"secret")
    (media_root / "outside.mkv").symlink_to(outside)
    client = TestClient(
        create_product_app(
            media_root,
            work_root,
            TranslatorFixture(),
            static_root=static_fixture(tmp_path),
        )
    )

    response = client.post("/api/media/browse", json={"path": ""})
    traversal = client.post("/api/media/browse", json={"path": "../"})
    absolute = client.post("/api/media/browse", json={"path": str(media_root)})
    linked = client.post("/api/media/browse", json={"path": "inside-link"})

    assert response.status_code == 200
    assert response.json()["path"] == ""
    assert [entry["name"] for entry in response.json()["entries"]] == [
        "inside",
        "inside-link",
        "Show 2",
        "Movie 2.MP4",
        "Movie 10.mkv",
    ]
    assert "outside.mkv" not in [entry["name"] for entry in response.json()["entries"]]
    assert all(
        not value.startswith("/")
        for entry in response.json()["entries"]
        for value in (entry["path"],)
    )
    assert traversal.json()["error_code"] == "invalid_media_path"
    assert absolute.json()["error_code"] == "invalid_media_path"
    movie = next(
        entry for entry in response.json()["entries"] if entry["name"] == "Movie 2.MP4"
    )
    assert movie["title"] == "Fallback label"
    assert movie["year"] == 2023
    assert linked.json()["path"] == "inside-link"
    assert linked.json()["entries"][0]["path"] == "inside-link/Episode.mkv"
