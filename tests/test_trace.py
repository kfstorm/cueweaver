import json

from cueweaver.trace import TraceWriter


def test_trace_writer_writes_versioned_events_and_flushes_terminal_state(tmp_path):
    writer = TraceWriter.create(tmp_path)

    writer.write("attempt_started", request_id="request-1", prompt={"text": "Hello"})
    writer.finish("completed")

    trace_paths = list(tmp_path.glob("trace-*.jsonl"))
    assert len(trace_paths) == 1
    events = [json.loads(line) for line in trace_paths[0].read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "run_started",
        "attempt_started",
        "run_finished",
    ]
    assert all(event["schema_version"] == 1 for event in events)
    assert all(event["run_id"] == events[0]["run_id"] for event in events)
    assert events[-1]["state"] == "completed"
    assert events[1]["prompt"] == {"text": "Hello"}


def test_trace_writer_removes_transport_credentials_but_keeps_prompt_content(tmp_path):
    writer = TraceWriter.create(tmp_path)

    writer.write(
        "attempt_started",
        request_body={
            "messages": [{"role": "user", "content": "api_key=subtitle text"}],
            "api_key": "secret",
            "headers": {"Authorization": "Bearer secret"},
            "total_tokens": 3,
        },
        error="Authorization: Bearer secret; api_key=secret",
    )
    writer.finish("completed")

    events = [
        json.loads(line)
        for line in next(tmp_path.glob("trace-*.jsonl")).read_text().splitlines()
    ]
    event = events[1]
    assert event["request_body"] == {
        "messages": [{"role": "user", "content": "api_key=subtitle text"}],
        "total_tokens": 3,
    }
    assert "secret" not in json.dumps(event)
    assert "subtitle text" in json.dumps(event)
