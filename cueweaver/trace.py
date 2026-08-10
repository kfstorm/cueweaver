"""Durable, credential-free JSONL tracing for translation Jobs."""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class TraceWriteError(OSError):
    """Raised when an enabled trace cannot be persisted."""


class TraceWriter:
    """Append versioned trace events to one durable Job artifact."""

    schema_version = 1

    def __init__(self, path: Path, run_id: str, handle: Any) -> None:
        self.path = path
        self.run_id = run_id
        self._handle = handle
        self._lock = Lock()
        self._closed = False

    @classmethod
    def create(cls, work_directory: Path) -> TraceWriter:
        work_directory.mkdir(parents=True, exist_ok=True)
        started = datetime.now(timezone.utc)
        run_id = f"{started.strftime('%Y%m%dT%H%M%S.%fZ')}-{secrets.token_hex(4)}"
        path = work_directory / f"trace-{run_id}.jsonl"
        try:
            handle = path.open("x", encoding="utf-8")
        except OSError as error:
            raise TraceWriteError(f"Unable to create debug trace: {path}") from error
        writer = cls(path, run_id, handle)
        try:
            writer.write("run_started")
        except TraceWriteError:
            handle.close()
            raise
        return writer

    def write(self, event: str, **payload: Any) -> None:
        """Write one event and flush it before returning."""

        record = {
            "schema_version": self.schema_version,
            "event": event,
            "timestamp": _timestamp(),
            "run_id": self.run_id,
            **{
                key: _json_safe(value, redact_text=key in {"error", "message"})
                for key, value in payload.items()
                if value is not None
            },
        }
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            if self._closed:
                raise TraceWriteError("Debug trace is already closed")
            try:
                self._handle.write(encoded)
                self._handle.flush()
            except (OSError, TypeError, ValueError) as error:
                raise TraceWriteError(
                    f"Unable to write debug trace: {self.path}"
                ) from error

    def finish(self, state: str, **payload: Any) -> None:
        """Write the terminal event, fsync it, and close the trace."""

        try:
            self.write("run_finished", state=state, **payload)
            with self._lock:
                try:
                    self._handle.flush()
                    self._handle.fileno()
                    os.fsync(self._handle.fileno())
                except OSError as error:
                    raise TraceWriteError(
                        f"Unable to finalize debug trace: {self.path}"
                    ) from error
        finally:
            with self._lock:
                if not self._closed:
                    self._closed = True
                    self._handle.close()


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "headers",
        "password",
        "proxy",
        "secret",
        "settings",
        "token",
        "access_token",
        "refresh_token",
    }
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[=:]\s*)[^\s,;]+"
)


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _json_safe(value: Any, *, redact_text: bool = False) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(
                item,
                redact_text=redact_text or str(key).casefold() in {"error", "message"},
            )
            for key, item in value.items()
            if _normalise_key(str(key)) not in _SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, redact_text=redact_text) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return _redact_text(value) if redact_text else value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return _redact_text(text) if redact_text else text


def _normalise_key(key: str) -> str:
    return key.casefold().replace("-", "_")


def _redact_text(value: str) -> str:
    value = _BEARER_PATTERN.sub(r"\1<redacted>", value)
    return _KEY_VALUE_PATTERN.sub(r"\1<redacted>", value)
