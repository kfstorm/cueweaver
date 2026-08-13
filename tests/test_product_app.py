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
        create_product_app_from_env(translator=TranslatorFixture())


def test_product_startup_validates_media_and_creates_work_root(tmp_path: Path):
    missing_media = tmp_path / "missing"
    work_root = tmp_path / "work"

    with pytest.raises(ValueError, match="Media root must be a readable directory"):
        create_product_app(missing_media, work_root, TranslatorFixture())

    media_root = tmp_path / "media"
    media_root.mkdir()
    create_product_app(media_root, work_root, TranslatorFixture())

    assert work_root.is_dir()
    assert list(work_root.iterdir()) == []


def test_product_status_is_ready_and_redacts_runtime_configuration(tmp_path: Path):
    media_root, work_root = configured_roots(tmp_path)
    translator = TranslatorFixture()
    client = TestClient(create_product_app(media_root, work_root, translator))

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
    media_root, work_root = configured_roots(tmp_path)
    client = TestClient(
        create_product_app(media_root, work_root, TranslatorFixture(available=False))
    )

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
    media_root, work_root = configured_roots(tmp_path)
    response = TestClient(
        create_product_app(media_root, work_root, TranslatorFixture())
    ).get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text


def test_product_keeps_explicit_path_business_routes(tmp_path: Path):
    media_root, work_root = configured_roots(tmp_path)
    response = TestClient(
        create_product_app(media_root, work_root, TranslatorFixture())
    ).post("/api/discover", json={"media_path": str(media_root / "missing.mkv")})

    assert response.status_code == 400
    assert response.json()["error_code"] == "media_not_found"


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
        create_product_app_from_env(translator=FalsyTranslator(available=False))
    ).get("/api/status")

    assert response.json()["translation_provider"]["ready"] is False
