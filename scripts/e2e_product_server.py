"""Test server for exercising the complete built Web product."""

import tempfile
from collections.abc import Mapping
from pathlib import Path

import uvicorn

from cueweaver.product import create_product_app


class UnavailableTranslator:
    available = False

    def translate(
        self,
        _source: Path,
        _target_language: str,
        *,
        user_overrides: Mapping[str, str] | None = None,
        work_directory: Path,
        dynamic_terminology_enabled: bool = True,
        subtitle_terminology_filter_enabled: bool = True,
    ) -> bytes:
        return b"1\n00:00:00,000 --> 00:00:01,000\nTranslated\n"


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="cueweaver-e2e-") as directory:
        root = Path(directory)
        media_root = root / "media"
        media_root.mkdir()
        app = create_product_app(media_root, root / "work", UnavailableTranslator())
        uvicorn.run(app, host="127.0.0.1", port=8765, workers=1)


if __name__ == "__main__":
    run()
