from pathlib import Path
from threading import Lock

from fastapi.testclient import TestClient

from cueweaver.product import create_product_app


class TranslatorFixture:
    available = True


_clients: dict[Path, TestClient] = {}
_clients_lock = Lock()


def make_client(tmp_path: Path) -> TestClient:
    media_root = tmp_path / "media"
    media_root.mkdir(exist_ok=True)
    with _clients_lock:
        previous = _clients.pop(tmp_path, None)
        if previous is not None:
            previous.app.state.application.close()
            previous.close()
        client = TestClient(
            create_product_app(
                media_root,
                tmp_path / "work",
                TranslatorFixture(),
                static_root=_static_root(tmp_path),
            )
        )
        _clients[tmp_path] = client
        return client


def _static_root(tmp_path: Path) -> Path:
    static_root = tmp_path / "static"
    static_root.mkdir(exist_ok=True)
    (static_root / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    return static_root
