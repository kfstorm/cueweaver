import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import ClassVar

from cueweaver.job import JobRunner, JobState
from cueweaver.translation import PySubtransTranslator

SRT = """1
00:00:01,000 --> 00:00:02,000
Hello

2
00:01:10,000 --> 00:01:11,000
Goodbye
"""


class ProviderFixtureHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        self.requests.append(request)
        messages = request.get("messages", [])
        prompt = "\n".join(message.get("content", "") for message in messages)
        numbers = re.findall(r"^#(\d+)$", prompt, flags=re.MULTILINE)
        translation = "\n\n".join(
            f"#{number}\nTranslation>\n你好" for number in numbers
        )
        translation += (
            f"\n\n<summary>fixture summary {numbers[-1]}</summary>"
            f"\n<scene>fixture scene {numbers[-1]}</scene>"
        )
        response = {
            "choices": [
                {
                    "message": {"content": translation},
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
        }
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


def test_pysubtrans_adapter_uses_resume_and_disabled_thinking(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    ProviderFixtureHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderFixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        translator = PySubtransTranslator(
            provider="openai-compatible",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )

        first_result = JobRunner(translator=translator).run(
            media,
            target_language="zh",
            source=source,
        )
        second_result = JobRunner(translator=translator).run(
            media,
            target_language="zh",
            source=source,
        )

        assert first_result.state is JobState.PUBLISHED
        assert second_result.state is JobState.PUBLISHED
        assert (
            first_result.published_path.read_text(encoding="utf-8").count("你好") == 2
        )
        assert not (tmp_path / "Movie.en.subtrans").exists()
        assert list((tmp_path / ".cueweaver").glob("*/*.subtrans"))
        assert len(ProviderFixtureHandler.requests) == 2
        second_prompt = "\n".join(
            message.get("content", "")
            for message in ProviderFixtureHandler.requests[1]["messages"]
        )
        assert "fixture scene 1" in second_prompt
        assert ProviderFixtureHandler.requests[0]["thinking"] == {"type": "disabled"}
        assert ProviderFixtureHandler.requests[0]["model"] == "fixture-model"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
