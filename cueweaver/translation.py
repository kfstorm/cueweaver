"""PySubtrans integration for CueWeaver's translation stage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from threading import Event, Lock
from typing import Any, ClassVar

from PySubtrans import (
    SubtitleTranslator,
    init_options,
    init_project,
    init_translation_provider,
)

from .metadata import Glossary
from .trace import TraceWriter
from .workspaces import default_work_root


class TranslationProviderConfigurationError(ValueError):
    """Raised when a provider outside the v0.1 scope is requested."""


class TranslationCanceled(RuntimeError):
    """Raised when PySubtrans stops because the active Job was canceled."""


class PySubtransTranslator:
    """Adapt one CueWeaver Source to PySubtrans's persistent engine contract."""

    _PROVIDER_ALIASES: ClassVar[dict[str, str]] = {
        "deepseek": "DeepSeek",
        "deepseek v4": "DeepSeek",
        "deepseek-v4": "DeepSeek",
        "custom server": "Custom Server",
        "openai-compatible": "Custom Server",
        "openai compatible": "Custom Server",
    }

    def __init__(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        api_base: str | None = None,
        server_address: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        configured_provider = provider or os.environ.get(
            "CUEWEAVER_TRANSLATION_PROVIDER",
            "DeepSeek",
        )
        self.provider = self._normalize_provider(configured_provider)
        self.api_key: str | None = None
        self.model: str | None = None
        self.api_base: str | None = None
        self.server_address: str | None = None
        self.endpoint: str | None = None

        if self.provider == "DeepSeek":
            self.api_key = api_key or os.environ.get(
                "CUEWEAVER_TRANSLATION_API_KEY",
                os.environ.get("DEEPSEEK_API_KEY"),
            )
            self.model = model or os.environ.get(
                "CUEWEAVER_TRANSLATION_MODEL",
                os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            )
            self.api_base = api_base or os.environ.get(
                "CUEWEAVER_TRANSLATION_API_BASE",
                os.environ.get("DEEPSEEK_API_BASE"),
            )
            self.server_address = None
        else:
            self.api_key = api_key or os.environ.get(
                "CUEWEAVER_TRANSLATION_API_KEY",
                os.environ.get("CUSTOM_API_KEY"),
            )
            self.model = model or os.environ.get(
                "CUEWEAVER_TRANSLATION_MODEL",
                os.environ.get("CUSTOM_MODEL"),
            )
            self.api_base = None
            self.server_address = server_address or os.environ.get(
                "CUEWEAVER_TRANSLATION_SERVER_ADDRESS",
                os.environ.get("CUSTOM_SERVER_ADDRESS"),
            )
        self.endpoint = endpoint or os.environ.get("CUEWEAVER_TRANSLATION_ENDPOINT")
        if self.endpoint is None and self.provider == "Custom Server":
            self.endpoint = os.environ.get("CUSTOM_ENDPOINT")
        self.intermediate_path: Path | None = None
        self.token_usage: dict[str, object] | None = None
        self._cancel_requested = Event()
        self._state_lock = Lock()
        self._active_engine: SubtitleTranslator | None = None

    def cancel(self) -> None:
        """Stop the active PySubtrans request and retain its checkpoint."""

        self._cancel_requested.set()
        with self._state_lock:
            engine = self._active_engine
        if engine is not None:
            engine.StopTranslating()

    def reset_for_job(self) -> None:
        """Clear cancellation from a previous terminal Job before a new one."""

        with self._state_lock:
            if self._active_engine is not None:
                raise RuntimeError("Cannot reset an active translation")
            self._cancel_requested.clear()
            self.intermediate_path = None
            self.token_usage = None

    def validate_configuration(self) -> None:
        """Fail before a Job gathers metadata when provider credentials are absent."""

        if self.provider == "DeepSeek" and not self.api_key:
            raise TranslationProviderConfigurationError(
                "DeepSeek API key is required; set CUEWEAVER_TRANSLATION_API_KEY "
                "or DEEPSEEK_API_KEY"
            )
        if self.provider == "Custom Server":
            if not self.server_address:
                raise TranslationProviderConfigurationError(
                    "Custom Server address is required; set "
                    "CUEWEAVER_TRANSLATION_SERVER_ADDRESS or CUSTOM_SERVER_ADDRESS"
                )
            if not self.endpoint:
                raise TranslationProviderConfigurationError(
                    "Custom Server endpoint is required; set "
                    "CUEWEAVER_TRANSLATION_ENDPOINT or CUSTOM_ENDPOINT"
                )

    def translate(
        self,
        source: Path,
        target_language: str,
        *,
        context: str = "",
        glossary: Glossary | None = None,
        user_overrides: Mapping[str, str] | None = None,
        work_directory: PathLike[str] | None = None,
        trace_writer: TraceWriter | None = None,
        dynamic_terminology_enabled: bool = True,
    ) -> bytes:
        """Translate *source* and return the engine-produced subtitle bytes."""

        self.intermediate_path = None
        self.token_usage = None
        source = Path(source).expanduser().resolve()
        static_terminology = _build_static_terminology(glossary, user_overrides)
        working_source = _prepare_working_source(
            source,
            target_language,
            user_overrides,
            work_directory=work_directory,
        )
        settings: dict[str, Any] = {
            "provider": self.provider,
            "target_language": target_language,
            "prompt": f"Translate these subtitles to {target_language}",
            # An empty value must clear a persisted description when a resumed
            # Job falls back to baseline translation after metadata degradation.
            "description": context,
            "scene_threshold": 60.0,
            "min_batch_size": 10,
            "max_batch_size": 30,
            "max_context_summaries": 10,
            "preprocess_subtitles": False,
            "postprocess_translation": False,
            "build_terminology_map": dynamic_terminology_enabled,
            "stop_on_error": True,
            "project_file": True,
        }
        if self.api_key is not None:
            settings["api_key"] = self.api_key
        if self.model is not None:
            settings["model"] = self.model
        if self.api_base is not None:
            settings["api_base"] = self.api_base
        if self.server_address is not None:
            settings["server_address"] = self.server_address
        if self.endpoint is not None:
            settings["endpoint"] = self.endpoint

        options = init_options(**settings)
        project = init_project(
            options,
            filepath=str(working_source),
            persistent=True,
        )
        project.write_translation = False
        provider = init_translation_provider(self.provider, options)
        persisted_terminology = (
            getattr(project.subtitles, "terminology_map", None)
            if dynamic_terminology_enabled
            else None
        )
        terminology_map, static_terminology = _build_terminology_seed(
            persisted_terminology,
            glossary,
            user_overrides,
        )
        engine = SubtitleTranslator(
            options,
            provider,
            resume=True,
            terminology_map=terminology_map,
        )
        _disable_thinking(engine)
        trace_session: _TraceSession | None = None
        if trace_writer is not None:
            trace_session = _install_trace_hooks(engine, trace_writer)

        with self._state_lock:
            self._active_engine = engine
            canceled = self._cancel_requested.is_set()
        if canceled:
            engine.StopTranslating()

        def save_checkpoint(_sender: Any, **_kwargs: Any) -> None:
            project.SaveProjectFile()

        def preserve_static_terminology(_sender: Any, update: Any) -> None:
            with engine.lock:
                for source, target in static_terminology.items():
                    _overlay_terminology(engine.terminology_map, source, target)
                update.terminology_map = dict(engine.terminology_map)

        def record_batch_errors(_sender: Any, batch: Any) -> None:
            if trace_session is not None:
                trace_session.record_batch_errors(batch)

        engine.events.batch_translated.connect(save_checkpoint, weak=False)
        if trace_session is not None:
            engine.events.batch_translated.connect(record_batch_errors, weak=False)
        if static_terminology:
            engine.events.terminology_updated.connect(
                preserve_static_terminology,
                weak=False,
            )
        try:
            project.TranslateSubtitles(engine)
            if engine.aborted or self._cancel_requested.is_set():
                raise TranslationCanceled("PySubtrans translation was canceled")
            if engine.errors:
                raise RuntimeError(
                    f"PySubtrans reported {len(engine.errors)} translation error(s)"
                )
            if not project.subtitles.all_translated:
                raise RuntimeError("PySubtrans did not translate every subtitle")

            return _save_translation_bytes(project, source.suffix)
        except Exception:
            _discard_incomplete_batches(project)
            if project.subtitles.any_translated:
                self.intermediate_path = self._save_intermediate_result(
                    project,
                    working_source,
                    target_language,
                )
            raise
        finally:
            if trace_session is not None:
                self.token_usage = trace_session.aggregate_usage()
            try:
                project.SaveProjectFile()
            finally:
                engine.events.batch_translated.disconnect(save_checkpoint)
                if trace_session is not None:
                    engine.events.batch_translated.disconnect(record_batch_errors)
                if static_terminology:
                    engine.events.terminology_updated.disconnect(
                        preserve_static_terminology
                    )
                with self._state_lock:
                    self._active_engine = None

    @classmethod
    def _normalize_provider(cls, provider: str) -> str:
        normalized = provider.strip().casefold()
        try:
            return cls._PROVIDER_ALIASES[normalized]
        except KeyError as error:
            raise TranslationProviderConfigurationError(
                "Unsupported translation provider; v0.1 supports DeepSeek "
                "and OpenAI-compatible providers"
            ) from error

    def _save_intermediate_result(
        self,
        project: Any,
        working_source: Path,
        target_language: str,
    ) -> Path:
        intermediate_path = working_source.with_name(
            f"{working_source.stem}.{target_language}.partial{working_source.suffix}"
        )
        project.subtitles.SaveTranslation(str(intermediate_path))
        return intermediate_path


def _disable_thinking(engine: SubtitleTranslator) -> None:
    """Inject the v0.1 fast/low-cost request option without forking PySubtrans."""

    # PySubtrans 1.6.0 has no public request-body hook; keep this seam isolated.
    client = engine.client
    original_generate_request_body = client._generate_request_body

    def generate_request_body(*args: Any, **kwargs: Any) -> dict[str, Any]:
        request_body = original_generate_request_body(*args, **kwargs)
        if not isinstance(request_body, dict):
            raise TypeError("PySubtrans returned an invalid provider request body")
        request_body["thinking"] = {"type": "disabled"}
        return request_body

    client._generate_request_body = generate_request_body


class _TraceSession:
    def __init__(self, writer: TraceWriter) -> None:
        self.writer = writer
        self._operation_number = 0
        self._request_number = 0
        self._operations: dict[Any, dict[str, Any]] = {}
        self._requests: dict[int, dict[str, Any]] = {}
        self._current_request: Any | None = None
        self._current_batch: Any | None = None
        self._current_kind = "initial"
        self._current_parent: str | None = None
        self._usage_totals: dict[str, object] | None = None

    def bind_batch(self, batch: Any) -> tuple[Any | None, str, str | None]:
        previous = self._current_batch, self._current_kind, self._current_parent
        self._current_batch = batch
        self._current_kind = "initial"
        self._current_parent = None
        return previous

    def restore_batch(self, previous: tuple[Any | None, str, str | None]) -> None:
        self._current_batch, self._current_kind, self._current_parent = previous

    def use_kind(self, kind: str) -> tuple[str, str | None]:
        previous = self._current_kind, self._current_parent
        self._current_kind = kind
        self._current_parent = self._current_operation_id()
        return previous

    def restore_kind(self, previous: tuple[str, str | None]) -> None:
        self._current_kind, self._current_parent = previous

    def set_current_request(self, request: Any | None) -> Any | None:
        previous = self._current_request
        self._current_request = request
        return previous

    def start_request(self, request: Any, request_body: Mapping[str, Any]) -> None:
        operation = self._operation_for(request)
        self._request_number += 1
        request_id = f"request-{self._request_number}"
        request_data = {
            "operation_id": operation["operation_id"],
            "request_id": request_id,
            "attempt": operation["attempt"] + 1,
            "attempt_kind": operation["kind"],
            "started_at": time.monotonic(),
        }
        operation["attempt"] += 1
        self._requests[id(request)] = request_data
        self.writer.write(
            "attempt_started",
            operation_id=operation["operation_id"],
            request_id=request_id,
            attempt=request_data["attempt"],
            attempt_kind=operation["kind"],
            parent_operation_id=operation.get("parent_operation_id"),
            **self._batch_fields(),
            prompt=list(getattr(request.prompt, "messages", [])),
            request_body=dict(request_body),
        )

    def complete_request(
        self, request: Any, response: Mapping[str, Any] | None
    ) -> None:
        request_data = self._requests.get(id(request))
        if request_data is None:
            return
        if response is None:
            self.writer.write(
                "attempt_failed",
                operation_id=request_data["operation_id"],
                request_id=request_data["request_id"],
                attempt=request_data["attempt"],
                error_type="TranslationCanceled",
                error="The provider returned no response because translation was canceled",
                canceled=True,
                retryable=False,
                duration_ms=round(
                    (time.monotonic() - request_data["started_at"]) * 1000, 3
                ),
            )
            return
        response_data = dict(response) if isinstance(response, Mapping) else response
        self.writer.write(
            "response_completed",
            operation_id=request_data["operation_id"],
            request_id=request_data["request_id"],
            attempt=request_data["attempt"],
            response=response_data,
            token_usage=_token_usage(response_data),
            duration_ms=round(
                (time.monotonic() - request_data["started_at"]) * 1000, 3
            ),
        )
        self._add_usage(_token_usage(response_data))

    def fail_request(self, request: Any, error: BaseException) -> None:
        request_data = self._requests.get(id(request))
        if request_data is None:
            return
        response = getattr(error, "response", None)
        self.writer.write(
            "attempt_failed",
            operation_id=request_data["operation_id"],
            request_id=request_data["request_id"],
            attempt=request_data["attempt"],
            error_type=type(error).__name__,
            error=str(error),
            http_status=getattr(response, "status_code", None),
            retryable=type(error).__name__
            in {"ServerResponseError", "ConnectError", "NetworkError", "ReadTimeout"},
            canceled=bool(getattr(error, "aborted", False)),
            duration_ms=round(
                (time.monotonic() - request_data["started_at"]) * 1000, 3
            ),
        )

    def aggregate_usage(self) -> dict[str, object] | None:
        if self._usage_totals is None:
            return None
        return dict(self._usage_totals)

    def _add_usage(self, usage: dict[str, object]) -> None:
        if self._usage_totals is None:
            self._usage_totals = {
                "prompt_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "reasoning_tokens": None,
            }
        for key, value in usage.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            previous = self._usage_totals.get(key)
            self._usage_totals[key] = (
                value + previous
                if isinstance(previous, (int, float)) and not isinstance(previous, bool)
                else value
            )

    def record_retry(self, message: str) -> None:
        request = self._current_request
        if request is None:
            return
        request_data = self._requests.get(id(request))
        if request_data is None:
            return
        delay = _retry_delay(message)
        operation = self._operations.get(request.prompt)
        if operation is not None:
            operation["kind"] = "transport_retry"
        self.writer.write(
            "retry_scheduled",
            operation_id=request_data["operation_id"],
            failed_request_id=request_data["request_id"],
            attempt_kind="transport_retry",
            reason="transport",
            retry_delay_seconds=delay,
            **self._batch_fields(),
        )

    def after_translation(self, request: Any, translation: Any) -> None:
        if getattr(translation, "reached_token_limit", False):
            operation = self._operations.get(request.prompt)
            if operation is not None:
                self.record_logical_retry(
                    operation["operation_id"],
                    reason="token_limit",
                    attempt_kind="token_limit_retry",
                )
                operation["kind"] = "token_limit_retry"

    def _operation_for(self, request: Any) -> dict[str, Any]:
        prompt_key = request.prompt
        existing = self._operations.get(prompt_key)
        if existing is not None and self._current_kind in {
            "initial",
            "transport_retry",
        }:
            if (
                self._current_kind == "initial"
                and existing["kind"] != "token_limit_retry"
            ):
                return existing
            if self._current_kind == "transport_retry":
                existing["kind"] = "transport_retry"
                return existing
            self._current_kind = existing["kind"]
            self._current_parent = existing["operation_id"]

        self._operation_number += 1
        operation = {
            "operation_id": f"operation-{self._operation_number}",
            "attempt": 0,
            "kind": self._current_kind,
            "batch_number": getattr(self._current_batch, "number", None),
            "scene": getattr(self._current_batch, "scene", None),
        }
        self._operations[prompt_key] = operation
        if self._current_kind != "initial":
            operation["parent_operation_id"] = (
                self._current_parent or self._current_operation_id()
            )
        return operation

    def _current_operation_id(self) -> str | None:
        if self._current_batch is None:
            return None
        for operation in self._operations.values():
            if operation.get("batch_number") == getattr(
                self._current_batch, "number", None
            ) and operation.get("scene") == getattr(self._current_batch, "scene", None):
                return str(operation["operation_id"])
        return None

    def record_logical_retry(
        self,
        operation_id: str | None,
        *,
        reason: str,
        attempt_kind: str,
        errors: list[object] | None = None,
    ) -> None:
        if operation_id is None:
            return
        self.writer.write(
            "retry_scheduled",
            operation_id=operation_id,
            attempt_kind=attempt_kind,
            reason=reason,
            validation_errors=[str(error) for error in errors] if errors else None,
            **self._batch_fields(),
        )

    def record_batch_errors(self, batch: Any) -> None:
        errors = list(getattr(batch, "errors", []))
        if not errors:
            return
        matching_operations = [
            operation
            for operation in self._operations.values()
            if operation.get("batch_number") == getattr(batch, "number", None)
            and operation.get("scene") == getattr(batch, "scene", None)
        ]
        if not matching_operations:
            return
        operation = matching_operations[-1]
        matching_requests = [
            request
            for request in self._requests.values()
            if request.get("operation_id") == operation["operation_id"]
        ]
        request_data = matching_requests[-1] if matching_requests else {}
        self.writer.write(
            "attempt_failed",
            operation_id=operation["operation_id"],
            request_id=request_data.get("request_id"),
            attempt=request_data.get("attempt"),
            error_type="ValidationError",
            error="; ".join(str(error) for error in errors),
            validation_errors=[str(error) for error in errors],
            retryable=False,
            canceled=False,
        )

    def _batch_fields(self) -> dict[str, Any]:
        if self._current_batch is None:
            return {}
        return {
            "scene": getattr(self._current_batch, "scene", None),
            "batch_number": getattr(self._current_batch, "number", None),
        }


def _install_trace_hooks(
    engine: SubtitleTranslator, writer: TraceWriter
) -> _TraceSession:
    """Observe PySubtrans 1.6.0's private request seams without changing defaults."""

    trace = _TraceSession(writer)
    client = engine.client

    original_translate_batch = engine.TranslateBatch

    def translate_batch(batch: Any, *args: Any, **kwargs: Any) -> Any:
        previous = trace.bind_batch(batch)
        try:
            return original_translate_batch(batch, *args, **kwargs)
        finally:
            trace.restore_batch(previous)

    engine.TranslateBatch = translate_batch

    original_retranslation = engine.RequestRetranslation

    def request_retranslation(batch: Any, *args: Any, **kwargs: Any) -> Any:
        previous = trace.use_kind("validation_retry")
        trace.record_logical_retry(
            trace._current_parent,
            reason="validation",
            attempt_kind="validation_retry",
            errors=list(getattr(batch, "errors", [])),
        )
        try:
            return original_retranslation(batch, *args, **kwargs)
        finally:
            trace.restore_kind(previous)

    engine.RequestRetranslation = request_retranslation

    original_split = engine._translate_split_batch

    def translate_split(batch: Any, *args: Any, **kwargs: Any) -> Any:
        previous = trace.use_kind("batch_split")
        trace.record_logical_retry(
            trace._current_parent,
            reason="batch_split",
            attempt_kind="batch_split",
            errors=list(getattr(batch, "errors", [])),
        )
        try:
            return original_split(batch, *args, **kwargs)
        finally:
            trace.restore_kind(previous)

    engine._translate_split_batch = translate_split

    original_make_request = client._make_request

    original_request_translation = client.RequestTranslation

    def request_translation(request: Any, *args: Any, **kwargs: Any) -> Any:
        translation = original_request_translation(request, *args, **kwargs)
        trace.after_translation(request, translation)
        return translation

    client.RequestTranslation = request_translation

    def make_request(request: Any, *args: Any, **kwargs: Any) -> Any:
        previous = trace.set_current_request(request)
        try:
            return original_make_request(request, *args, **kwargs)
        finally:
            trace.set_current_request(previous)

    client._make_request = make_request

    original_non_streaming = client._handle_non_streaming_request

    original_process_api_response = client._process_api_response

    def process_api_response(content: Mapping[str, Any], response: Any) -> Any:
        processed = original_process_api_response(content, response)
        _merge_provider_usage(processed, content)
        return processed

    client._process_api_response = process_api_response

    def handle_non_streaming(request_body: Mapping[str, Any]) -> Any:
        request = trace._current_request
        if request is None:
            return original_non_streaming(request_body)
        trace.start_request(request, request_body)
        try:
            response = original_non_streaming(request_body)
        except Exception as error:
            trace.fail_request(request, error)
            raise
        trace.complete_request(request, response)
        return response

    client._handle_non_streaming_request = handle_non_streaming

    original_streaming = client._handle_streaming_request

    def handle_streaming(request: Any, request_body: dict[str, Any]) -> Any:
        request_body["stream"] = True
        trace.start_request(request, request_body)
        try:
            response = original_streaming(request, request_body)
        except Exception as error:
            trace.fail_request(request, error)
            raise
        trace.complete_request(request, response)
        return response

    client._handle_streaming_request = handle_streaming

    original_chunk = client._process_streaming_chunk

    def process_chunk(
        request: Any,
        chunk: Mapping[str, Any],
        accumulated_response: dict[str, Any],
    ) -> None:
        original_chunk(request, chunk, accumulated_response)
        _merge_provider_usage(accumulated_response, chunk)

    client._process_streaming_chunk = process_chunk

    original_warning = client._emit_warning

    def emit_warning(message: str, *args: Any, **kwargs: Any) -> None:
        if message.startswith("Retrying in "):
            trace.record_retry(message)
        original_warning(message, *args, **kwargs)

    client._emit_warning = emit_warning
    return trace


def _token_usage(response: object) -> dict[str, object]:
    if not isinstance(response, Mapping):
        response = {}
    usage = response.get("usage")
    if isinstance(usage, Mapping):
        output_tokens = usage.get("completion_tokens")
        if output_tokens is None:
            output_tokens = usage.get("output_tokens")
        token_usage = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "output_tokens": output_tokens,
            "total_tokens": usage.get("total_tokens"),
            "reasoning_tokens": _reasoning_tokens(usage),
        }
        _copy_cache_usage(token_usage, usage)
        return token_usage
    token_usage = {
        "prompt_tokens": response.get("prompt_tokens"),
        "output_tokens": response.get("output_tokens"),
        "total_tokens": response.get("total_tokens"),
        "reasoning_tokens": response.get("reasoning_tokens"),
    }
    _copy_cache_usage(token_usage, response)
    return token_usage


def _merge_provider_usage(
    response: dict[str, Any], provider_response: Mapping[str, Any]
) -> None:
    usage = provider_response.get("usage")
    if not isinstance(usage, Mapping):
        return
    response.update(_token_usage(provider_response))


def _reasoning_tokens(usage: Mapping[str, Any]) -> object:
    reasoning = usage.get("reasoning_tokens")
    if reasoning is not None:
        return reasoning
    for details_key in ("completion_tokens_details", "output_tokens_details"):
        details = usage.get(details_key)
        if isinstance(details, Mapping) and "reasoning_tokens" in details:
            return details["reasoning_tokens"]
    return None


def _copy_cache_usage(token_usage: dict[str, object], usage: Mapping[str, Any]) -> None:
    for key, value in usage.items():
        if str(key).casefold() == "prompt_cache_write_tokens":
            continue
        if (
            "cache" in str(key).casefold()
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            token_usage[str(key)] = value
    for details_key in ("prompt_tokens_details", "input_tokens_details"):
        details = usage.get(details_key)
        if not isinstance(details, Mapping):
            continue
        for key, value in details.items():
            if (
                "cache" in str(key).casefold()
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                token_usage[f"{details_key}.{key}"] = value


def _retry_delay(message: str) -> float | None:
    try:
        return float(message.removeprefix("Retrying in ").removesuffix(" seconds..."))
    except ValueError:
        return None


def _build_terminology_seed(
    persisted: object,
    glossary: Glossary | None,
    user_overrides: Mapping[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    terminology_map = (
        {str(source): str(target) for source, target in persisted.items()}
        if isinstance(persisted, dict)
        else {}
    )
    static_terminology = _build_static_terminology(glossary, user_overrides)
    for source, target in static_terminology.items():
        _overlay_terminology(terminology_map, source, target)
    return terminology_map, static_terminology


def _build_static_terminology(
    glossary: Glossary | None,
    user_overrides: Mapping[str, str] | None,
) -> dict[str, str]:
    static_terminology: dict[str, str] = {}
    if glossary is not None:
        for source, target in glossary.mapping.items():
            _overlay_terminology(static_terminology, source, target)
    if user_overrides is not None:
        for source, target in user_overrides.items():
            _overlay_terminology(static_terminology, source, target)
    return static_terminology


def _overlay_terminology(
    terminology_map: dict[str, str], source: str, target: str
) -> None:
    source_key = source.casefold()
    for existing_source in tuple(terminology_map):
        if existing_source.casefold() == source_key:
            del terminology_map[existing_source]
    terminology_map[source] = target


def _prepare_working_source(
    source: Path,
    target_language: str,
    user_overrides: Mapping[str, str] | None = None,
    *,
    work_directory: PathLike[str] | None = None,
) -> Path:
    """Place a stable Source copy in the Job work directory used by PySubtrans."""

    source_content = source.read_bytes()
    key_material = b"\0".join(
        (
            str(source).encode("utf-8"),
            target_language.encode("utf-8"),
            source_content,
        )
    )
    # Metadata can recover between attempts; only an explicit User override
    # changes the translation contract enough to invalidate a checkpoint.
    if user_overrides:
        key_material += b"\0" + json.dumps(
            sorted(
                user_overrides.items(), key=lambda item: (item[0].casefold(), item[0])
            ),
            ensure_ascii=False,
        ).encode("utf-8")
    job_key = hashlib.sha256(key_material).hexdigest()[:16]
    work_root = (
        Path(work_directory).expanduser().resolve()
        if work_directory is not None
        else default_work_root()
    )
    translation_directory = work_root / "translation" / job_key
    translation_directory.mkdir(parents=True, exist_ok=True)
    working_source = translation_directory / source.name
    if not working_source.exists() or not _same_file_content(
        working_source, source_content
    ):
        working_source.write_bytes(source_content)
    return working_source


def _same_file_content(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def _save_translation_bytes(project: Any, suffix: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="cueweaver-translation-") as directory:
        translated_path = Path(directory) / f"translated{suffix}"
        project.subtitles.SaveTranslation(str(translated_path))
        return translated_path.read_bytes()


def _discard_incomplete_batches(project: Any) -> None:
    """Keep only complete batch anchors in a durable checkpoint."""

    for scene in project.subtitles.scenes:
        for batch in scene.batches:
            if batch.all_translated and not batch.errors:
                continue
            batch.translated = []
            batch.translation = None
            batch.errors = []
