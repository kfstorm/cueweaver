import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
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


def srt_with_cues(count: int) -> str:
    return "\n\n".join(
        f"{number}\n"
        f"00:00:{number:02d},000 --> 00:00:{number + 1:02d},000\n"
        f"Line {number}"
        for number in range(1, count + 1)
    )


def start_provider_server(
    *,
    fail_after_first_request: bool = False,
    block_scene: str | None = None,
) -> tuple[ThreadingHTTPServer, Thread]:
    ProviderFixtureHandler.requests = []
    ProviderFixtureHandler.fail_after_first_request = fail_after_first_request
    ProviderFixtureHandler.block_scene = block_scene
    ProviderFixtureHandler.blocked_request_started = Event()
    ProviderFixtureHandler.release_block = Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderFixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def provider_request_numbers(max_number: int | None = None) -> list[list[str]]:
    request_numbers = []
    for request in ProviderFixtureHandler.requests:
        numbers = re.findall(
            r"^#(\d+)$",
            "\n".join(message.get("content", "") for message in request["messages"]),
            flags=re.MULTILINE,
        )
        if max_number is not None:
            numbers = [number for number in numbers if int(number) <= max_number]
        request_numbers.append(numbers)
    return request_numbers


class ProviderFixtureHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []
    fail_after_first_request: ClassVar[bool] = False
    block_scene: ClassVar[str | None] = None
    blocked_request_started: ClassVar[Event] = Event()
    release_block: ClassVar[Event] = Event()

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        self.requests.append(request)
        messages = request.get("messages", [])
        prompt = "\n".join(message.get("content", "") for message in messages)
        numbers = re.findall(r"^#(\d+)$", prompt, flags=re.MULTILINE)
        if self.fail_after_first_request and len(type(self).requests) > 1:
            translation = "#999\nTranslation>\n失败"
        else:
            translation = "\n\n".join(
                f"#{number}\nTranslation>\n你好" for number in numbers
            )
        if numbers and numbers[-1] == self.block_scene:
            type(self).blocked_request_started.set()
            type(self).release_block.wait(timeout=5)
        last_number = numbers[-1] if numbers else "unknown"
        translation += (
            f"\n\n<summary>fixture summary {last_number}</summary>"
            f"\n<scene>fixture scene {last_number}</scene>"
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
        try:
            self.wfile.write(encoded)
        except BrokenPipeError:
            pass

    def log_message(self, format, *args):
        return


def test_pysubtrans_adapter_uses_resume_and_disabled_thinking(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    server, thread = start_provider_server()

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


def test_job_resume_skips_a_committed_batch_after_provider_interruption(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    source_content = srt_with_cues(31)
    media.write_bytes(b"media")
    source.write_text(source_content, encoding="utf-8")
    server, thread = start_provider_server(fail_after_first_request=True)

    try:
        first_translator = PySubtransTranslator(
            provider="openai-compatible",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )
        first_result = JobRunner(translator=first_translator).run(
            media,
            target_language="zh",
            source=source,
        )

        ProviderFixtureHandler.fail_after_first_request = False
        second_translator = PySubtransTranslator(
            provider="openai-compatible",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )
        second_result = JobRunner(translator=second_translator).run(
            media,
            target_language="zh",
            source=source,
        )

        assert first_result.state is JobState.FAILED
        assert first_result.intermediate_path is not None
        assert first_result.intermediate_path.exists()
        assert second_result.state is JobState.PUBLISHED
        assert (
            second_result.published_path.read_text(encoding="utf-8").count("你好") == 31
        )
        assert len(ProviderFixtureHandler.requests) == 4

        request_numbers = provider_request_numbers(max_number=31)
        assert request_numbers[0][0] == "1"
        assert request_numbers[0][-1] == "10"
        assert request_numbers[1][0] == "11"
        assert request_numbers[1][-1] == "31"
        assert request_numbers[3][0] == "11"
        assert request_numbers[3][-1] == "31"
        assert sum("1" in numbers for numbers in request_numbers) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cancelled_pysubtrans_job_keeps_committed_batches_for_a_fresh_job(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    source_content = """1
00:00:01,000 --> 00:00:02,000
Hello

2
00:01:10,000 --> 00:01:11,000
Goodbye
"""
    media.write_bytes(b"media")
    source.write_text(source_content, encoding="utf-8")
    server, server_thread = start_provider_server(block_scene="2")

    runner = JobRunner(
        translator=PySubtransTranslator(
            provider="openai-compatible",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )
    )
    results = []
    job_thread = Thread(
        target=lambda: results.append(
            runner.run(media, target_language="zh", source=source)
        )
    )
    job_thread.start()

    try:
        assert ProviderFixtureHandler.blocked_request_started.wait(timeout=5)
        runner.cancel()
        ProviderFixtureHandler.release_block.set()
        job_thread.join(timeout=5)

        assert not job_thread.is_alive()
        canceled_result = results[0]
        assert canceled_result.state is JobState.CANCELED
        assert canceled_result.intermediate_path is not None
        assert (
            canceled_result.intermediate_path.read_text(encoding="utf-8").count("你好")
            == 1
        )
        assert not (tmp_path / "Movie.zh.srt").exists()

        ProviderFixtureHandler.block_scene = None
        resumed_result = JobRunner(
            translator=PySubtransTranslator(
                provider="openai-compatible",
                server_address=f"http://127.0.0.1:{server.server_port}",
                endpoint="/v1/chat/completions",
                model="fixture-model",
            )
        ).run(media, target_language="zh", source=source)

        assert resumed_result.state is JobState.PUBLISHED
        assert (
            resumed_result.published_path.read_text(encoding="utf-8").count("你好") == 2
        )
        request_numbers = provider_request_numbers()
        assert sum("1" in numbers for numbers in request_numbers) == 1
    finally:
        ProviderFixtureHandler.release_block.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
