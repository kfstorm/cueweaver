import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import ClassVar

from cueweaver.job import JobRunner, JobState
from cueweaver.metadata import Glossary, MetadataCache, Term
from cueweaver.translation import PySubtransTranslator
from tests.test_helpers import write_user_override

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
    fail_transport_first_request: bool = False,
    block_scene: str | None = None,
    use_terminology: bool = False,
    include_dynamic_terminology: bool = False,
) -> tuple[ThreadingHTTPServer, Thread]:
    ProviderFixtureHandler.requests = []
    ProviderFixtureHandler.fail_after_first_request = fail_after_first_request
    ProviderFixtureHandler.fail_transport_first_request = fail_transport_first_request
    ProviderFixtureHandler.block_scene = block_scene
    ProviderFixtureHandler.use_terminology = use_terminology
    ProviderFixtureHandler.include_dynamic_terminology = include_dynamic_terminology
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
    fail_transport_first_request: ClassVar[bool] = False
    block_scene: ClassVar[str | None] = None
    use_terminology: ClassVar[bool] = False
    include_dynamic_terminology: ClassVar[bool] = False
    blocked_request_started: ClassVar[Event] = Event()
    release_block: ClassVar[Event] = Event()

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        self.requests.append(request)
        messages = request.get("messages", [])
        prompt = "\n".join(message.get("content", "") for message in messages)
        if type(self).fail_transport_first_request and len(type(self).requests) == 1:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"temporary provider failure"}')
            return
        numbers = re.findall(r"^#(\d+)$", prompt, flags=re.MULTILINE)
        if self.fail_after_first_request and len(type(self).requests) > 1:
            translation = "#999\nTranslation>\n失败"
        else:
            match = re.search(r"Jon Snow::([^\n]+)", prompt)
            terminology_target = match.group(1).strip() if match else "你好"
            if re.search(r"Original>\s*Pinellia", prompt):
                terminology_target = "半夏"
            translation = "\n\n".join(
                f"#{number}\nTranslation>\n"
                f"{terminology_target if type(self).use_terminology else '你好'}"
                for number in numbers
            )
            if type(self).include_dynamic_terminology:
                translation += "\n<terminology>Pinellia::半夏</terminology>"
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
        if request.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            split_at = max(1, len(translation) // 2)
            chunks = [translation[:split_at], translation[split_at:]]
            for index, chunk in enumerate(chunks):
                payload = {
                    "model": "fixture-model",
                    "choices": [
                        {
                            "delta": {"content": chunk},
                            "finish_reason": "stop"
                            if index == len(chunks) - 1
                            else None,
                        }
                    ],
                }
                if index == len(chunks) - 1:
                    payload["usage"] = {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    }
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return

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


class SwitchableMetadata:
    def __init__(self, glossary: Glossary) -> None:
        self.glossary = glossary

    def get_series_overview(self, series_id: str) -> str:
        return "Series overview"

    def get_episode_overview(
        self, series_id: str, season_number: int, episode_number: int
    ) -> str:
        return "Episode overview"

    def get_glossary(self, series_id: str, target_language: str) -> Glossary:
        return self.glossary


class BilingualContextMetadata:
    def get_series_title(self, series_id: str, language: str) -> str:
        return "Dong Yi" if language == "en" else "同伊"

    def get_series_overview(self, series_id: str, language: str) -> str:
        return "English series overview" if language == "en" else "中文剧集简介"

    def get_episode_title(
        self, series_id: str, season_number: int, episode_number: int, language: str
    ) -> str:
        return "Episode One" if language == "en" else "第一集"

    def get_episode_overview(
        self,
        series_id: str,
        season_number: int,
        episode_number: int,
        language: str,
    ) -> str:
        return "English episode overview" if language == "en" else "中文单集简介"

    def get_glossary(self, series_id: str, target_language: str) -> Glossary:
        return Glossary()


def test_pysubtrans_adapter_uses_resume_and_disabled_thinking(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    work_directory = tmp_path / "job-work"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    monkeypatch.setenv("CUEWEAVER_WORK_DIRECTORY", str(work_directory))
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
        assert list(work_directory.rglob("*.subtrans"))
        assert len(ProviderFixtureHandler.requests) == 2
        assert not list(work_directory.rglob("trace-*.jsonl"))
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


def test_debug_trace_records_default_deepseek_streaming_chunks(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    server, thread = start_provider_server()

    try:
        translator = PySubtransTranslator(
            provider="deepseek",
            api_key="fixture-secret",
            api_base=f"http://127.0.0.1:{server.server_port}",
            model="fixture-model",
        )

        result = JobRunner(translator=translator).run(
            media,
            target_language="zh",
            source=source,
            debug=True,
        )

        assert result.state is JobState.PUBLISHED
        assert result.trace_path is not None
        events = [
            json.loads(line)
            for line in result.trace_path.read_text(encoding="utf-8").splitlines()
        ]
        chunks = [event for event in events if event["event"] == "response_chunk"]
        completed = [
            event for event in events if event["event"] == "response_completed"
        ]
        assert chunks
        assert completed
        assert all(
            event["request_body"]["stream"] is True
            for event in events
            if event["event"] == "attempt_started"
        )
        assert completed[-1]["token_usage"] == {
            "prompt_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 5,
            "reasoning_tokens": None,
        }
        assert completed[-1]["response"]["text"]
        assert "fixture-secret" not in result.trace_path.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_debug_trace_correlates_transport_retry(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    server, thread = start_provider_server(fail_transport_first_request=True)
    monkeypatch.setattr(
        "PySubtrans.Providers.Clients.CustomClient.time.sleep",
        lambda _seconds: None,
    )

    try:
        translator = PySubtransTranslator(
            provider="openai-compatible",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )
        result = JobRunner(translator=translator).run(
            media,
            target_language="zh",
            source=source,
            debug=True,
        )

        assert result.state is JobState.PUBLISHED
        assert result.trace_path is not None
        events = [
            json.loads(line)
            for line in result.trace_path.read_text(encoding="utf-8").splitlines()
        ]
        failed = [event for event in events if event["event"] == "attempt_failed"]
        retries = [event for event in events if event["event"] == "retry_scheduled"]
        retry_attempts = [
            event
            for event in events
            if event["event"] == "attempt_started"
            and event.get("attempt_kind") == "transport_retry"
        ]
        assert failed
        assert retries
        assert retry_attempts
        assert retries[0]["failed_request_id"] == failed[0]["request_id"]
        assert retry_attempts[0]["operation_id"] == failed[0]["operation_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_debug_trace_records_non_streaming_request_response_and_final_state(
    tmp_path,
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    server, thread = start_provider_server()

    try:
        translator = PySubtransTranslator(
            provider="openai-compatible",
            api_key="fixture-secret",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )

        result = JobRunner(translator=translator).run(
            media,
            target_language="zh",
            source=source,
            debug=True,
        )

        assert result.state is JobState.PUBLISHED
        assert result.trace_path is not None
        events = [
            json.loads(line)
            for line in result.trace_path.read_text(encoding="utf-8").splitlines()
        ]
        assert events[0]["event"] == "run_started"
        assert events[-1]["event"] == "run_finished"
        assert [event["event"] for event in events[1:-1]] == [
            item
            for _ in ProviderFixtureHandler.requests
            for item in ("attempt_started", "response_completed")
        ]
        attempt = events[1]
        assert attempt["request_body"]["thinking"] == {"type": "disabled"}
        assert attempt["prompt"] == ProviderFixtureHandler.requests[0]["messages"]
        assert events[2]["response"]["text"]
        assert events[-1]["state"] == "completed"
        assert "fixture-secret" not in result.trace_path.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_bilingual_metadata_is_passed_to_pysubtrans_description(tmp_path):
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
        result = JobRunner(
            translator=translator,
            metadata_provider=BilingualContextMetadata(),
            metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
        ).run(
            media,
            target_language="zh",
            source=source,
            series_id="1399",
            season_number=1,
            episode_number=1,
        )

        assert result.state is JobState.PUBLISHED
        prompt = "\n".join(
            message.get("content", "")
            for message in ProviderFixtureHandler.requests[0]["messages"]
        )
        assert "Series title (source: en)" in prompt
        assert "Series title (target: zh)" in prompt
        assert "Episode overview (source: en)" in prompt
        assert "Episode overview (target: zh)" in prompt
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_job_seeds_pysubtrans_with_override_precedence_and_keeps_dynamic_learning(
    tmp_path,
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(
        SRT.replace("Hello", "Pinellia").replace("Goodbye", "Jon Snow"),
        encoding="utf-8",
    )
    server, thread = start_provider_server(
        use_terminology=True,
        include_dynamic_terminology=True,
    )
    overrides, _ = write_user_override(
        tmp_path / "overrides",
        "1399",
        {"Jon Snow": "用户名称"},
    )

    class MetadataGlossary:
        def get_series_overview(self, series_id: str) -> str:
            return "Series overview"

        def get_episode_overview(
            self, series_id: str, season_number: int, episode_number: int
        ) -> str:
            return "Episode overview"

        def get_glossary(self, series_id: str, target_language: str) -> Glossary:
            return Glossary.from_terms(
                [
                    Term(
                        source="Jon Snow",
                        target="琼恩·雪诺",
                        provider="wikidata",
                        source_url="https://www.wikidata.org/wiki/Q1",
                        entity_id="Q1",
                    )
                ]
            )

    try:
        translator = PySubtransTranslator(
            provider="openai-compatible",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )
        result = JobRunner(
            translator=translator,
            metadata_provider=MetadataGlossary(),
            metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
            user_override_store=overrides,
        ).run(
            media,
            target_language="zh",
            source=source,
            series_id="1399",
            season_number=1,
            episode_number=1,
        )

        assert result.state is JobState.PUBLISHED
        prompt = "\n".join(
            message.get("content", "")
            for message in ProviderFixtureHandler.requests[0]["messages"]
        )
        assert "Jon Snow" in prompt
        assert "用户名称" in prompt
        assert "琼恩·雪诺" not in prompt
        second_prompt = "\n".join(
            message.get("content", "")
            for message in ProviderFixtureHandler.requests[1]["messages"]
        )
        assert "Pinellia::半夏" in second_prompt
        assert "Jon Snow::用户名称" in second_prompt
        assert result.glossary.terms[0].target == "琼恩·雪诺"
        assert result.user_overrides == {"Jon Snow": "用户名称"}
        assert result.published_path is not None
        published = result.published_path.read_text(encoding="utf-8")
        assert published.count("半夏") == 1
        assert published.count("用户名称") == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_changed_user_override_does_not_resume_an_old_translation(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT.split("\n\n", 1)[0] + "\n", encoding="utf-8")
    server, thread = start_provider_server()
    overrides, override_path = write_user_override(
        tmp_path / "overrides",
        "Movie",
        {"Jon Snow": "first name"},
    )

    try:
        translator = PySubtransTranslator(
            provider="openai-compatible",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )
        runner = JobRunner(
            translator=translator,
            user_override_store=overrides,
        )
        first = runner.run(media, target_language="zh", source=source)

        override_path.write_text(
            json.dumps({"Jon Snow": "second name"}),
            encoding="utf-8",
        )
        second = runner.run(media, target_language="zh", source=source)

        assert first.state is JobState.PUBLISHED
        assert second.state is JobState.PUBLISHED
        assert len(ProviderFixtureHandler.requests) == 2
        second_prompt = "\n".join(
            message.get("content", "")
            for message in ProviderFixtureHandler.requests[1]["messages"]
        )
        assert "second name" in second_prompt
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


def test_resume_after_metadata_recovery_does_not_resend_committed_batches(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    source_content = srt_with_cues(31)
    media.write_bytes(b"media")
    source.write_text(source_content, encoding="utf-8")
    server, thread = start_provider_server(fail_after_first_request=True)
    cache = MetadataCache(tmp_path / "metadata-cache")
    metadata = SwitchableMetadata(Glossary())
    automatic = Glossary.from_terms(
        [
            Term(
                source="Line 1",
                target="第一行",
                provider="wikidata",
                source_url="https://www.wikidata.org/wiki/Q1",
                entity_id="Q1",
            )
        ]
    )

    try:
        translator = PySubtransTranslator(
            provider="openai-compatible",
            server_address=f"http://127.0.0.1:{server.server_port}",
            endpoint="/v1/chat/completions",
            model="fixture-model",
        )
        runner = JobRunner(
            translator=translator,
            metadata_provider=metadata,
            metadata_cache=cache,
        )
        first = runner.run(
            media,
            target_language="zh",
            source=source,
            series_id="1399",
            season_number=1,
            episode_number=1,
        )

        metadata.glossary = automatic
        ProviderFixtureHandler.fail_after_first_request = False
        second = runner.run(
            media,
            target_language="zh",
            source=source,
            series_id="1399",
            season_number=1,
            episode_number=1,
            refresh_metadata=True,
        )

        assert first.state is JobState.FAILED
        assert second.state is JobState.PUBLISHED
        request_numbers = provider_request_numbers(max_number=31)
        assert request_numbers[0][0] == "1"
        assert request_numbers[1][0] == "11"
        assert request_numbers[2][0] == "11"
        assert all("1" not in numbers for numbers in request_numbers[2:]), (
            request_numbers
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_resume_clears_stale_metadata_context_after_degradation(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(srt_with_cues(31), encoding="utf-8")
    server, thread = start_provider_server(fail_after_first_request=True)

    class MetadataFixture:
        def get_series_overview(self, series_id: str) -> str:
            return "The stale series overview."

        def get_episode_overview(
            self, series_id: str, season_number: int, episode_number: int
        ) -> str:
            return "The stale episode overview."

    translator = PySubtransTranslator(
        provider="openai-compatible",
        server_address=f"http://127.0.0.1:{server.server_port}",
        endpoint="/v1/chat/completions",
        model="fixture-model",
    )

    try:
        first_result = JobRunner(
            translator=translator,
            metadata_provider=MetadataFixture(),
            metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
        ).run(
            media,
            target_language="zh",
            source=source,
            series_id="1399",
            season_number=1,
            episode_number=2,
        )

        initial_prompt = "\n".join(
            message.get("content", "")
            for message in ProviderFixtureHandler.requests[0]["messages"]
        )
        assert "The stale series overview." in initial_prompt
        assert "The stale episode overview." in initial_prompt

        ProviderFixtureHandler.fail_after_first_request = False
        second_result = JobRunner(translator=translator).run(
            media,
            target_language="zh",
            source=source,
        )

        assert first_result.state is JobState.FAILED
        assert second_result.state is JobState.PUBLISHED
        resumed_prompt = "\n".join(
            message.get("content", "")
            for message in ProviderFixtureHandler.requests[-1]["messages"]
        )
        assert "The stale series overview." not in resumed_prompt
        assert "The stale episode overview." not in resumed_prompt
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
