from pathlib import Path

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
