"""PySubtrans integration for CueWeaver's translation stage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
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
        dynamic_terminology_enabled: bool = True,
    ) -> bytes:
        """Translate *source* and return the engine-produced subtitle bytes."""

        self.intermediate_path = None
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

        engine.events.batch_translated.connect(save_checkpoint, weak=False)
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
            try:
                project.SaveProjectFile()
            finally:
                engine.events.batch_translated.disconnect(save_checkpoint)
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
