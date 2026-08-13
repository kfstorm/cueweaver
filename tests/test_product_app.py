from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueweaver.product import create_product_app, create_product_app_from_env


class TranslatorFixture:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
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

        def fail_replace(source: Path, destination: Path) -> None:
            if source.parent.parent == work_root:
                raise OSError("replace probe failed")
            source.replace(destination)

        monkeypatch.setattr("cueweaver.product.os.replace", fail_replace)

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
    }
    serialized = response.text
    assert str(media_root) not in serialized
    assert str(work_root) not in serialized
    assert translator.secret not in serialized


def test_unconfigured_provider_keeps_product_available_with_actionable_status(
    tmp_path: Path,
):
    client = TestClient(product_app(tmp_path, TranslatorFixture(available=False)))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["translation_provider"] == {
        "ready": False,
        "message": (
            "Configure a provider in PySubtrans service settings, then restart "
            "CueWeaver."
        ),
    }
    assert response.json()["api"] == {"ready": True}


@pytest.mark.parametrize("path", ["/", "/translate", "/jobs", "/term-maps"])
def test_product_serves_client_routes_from_the_spa(tmp_path: Path, path: str):
    response = TestClient(product_app(tmp_path)).get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text


@pytest.mark.parametrize(
    ("path", "body", "error_code"),
    [
        (
            "/api/discover",
            {"media_path": "missing.mkv"},
            "media_not_found",
        ),
        (
            "/api/extract",
            {"media_path": "missing.mkv", "stream_index": 1, "output_path": "x.srt"},
            "media_not_found",
        ),
        (
            "/api/translate",
            {
                "subtitle_path": "missing.srt",
                "target_language_code": "zh-Hans",
                "output_path": "output.srt",
                "work_directory": "work",
            },
            "subtitle_not_found",
        ),
    ],
)
def test_product_keeps_explicit_path_business_routes(
    tmp_path: Path, path: str, body: dict[str, object], error_code: str
):
    media_root, work_root = configured_roots(tmp_path)
    response = TestClient(
        create_product_app(
            media_root,
            work_root,
            TranslatorFixture(),
            static_root=static_fixture(tmp_path),
        )
    ).post(path, json=body)

    assert response.status_code == 400
    assert response.json()["error_code"] == error_code


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
