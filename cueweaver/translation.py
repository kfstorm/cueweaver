"""PySubtrans adapter for explicit HTTP translation requests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any

import langcodes
from PySubtrans import (
    SubtitleTranslator,
    init_options,
    init_project,
    init_translation_provider,
)
from PySubtrans.SettingsType import SettingsError, SettingsType
from PySubtrans.TranslationProvider import TranslationProvider

from .terminology import filter_terminology_for_text

_PROVIDER_REQUIRED_ENVIRONMENT = {
    "Azure": (
        "AZURE_API_KEY",
        "AZURE_API_BASE",
        "AZURE_API_VERSION",
        "AZURE_DEPLOYMENT_NAME",
    ),
    "Bedrock": (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "BEDROCK_MODEL",
    ),
    "Claude": ("CLAUDE_API_KEY",),
    # PySubtrans supplies a localhost default for servers running in the container.
    "Custom Server": (),
    "DeepSeek": ("DEEPSEEK_API_KEY",),
    "Gemini": ("GEMINI_API_KEY",),
    "Mistral": ("MISTRAL_API_KEY",),
    "OpenAI": ("OPENAI_API_KEY",),
    "OpenRouter": ("OPENROUTER_API_KEY",),
}

_PROVIDER_ENVIRONMENT_SETTINGS = {
    "Bedrock": {
        "access_key": "AWS_ACCESS_KEY_ID",
        "secret_access_key": "AWS_SECRET_ACCESS_KEY",
        "aws_region": "AWS_REGION",
        "model": "BEDROCK_MODEL",
        "max_tokens": "BEDROCK_MAX_TOKENS",
        "temperature": "BEDROCK_TEMPERATURE",
        "rate_limit": "BEDROCK_RATE_LIMIT",
        "proxy": "BEDROCK_PROXY",
    },
    "Claude": {"thinking": "CLAUDE_THINKING"},
}


class PySubtransTranslator:
    """Translate one explicit subtitle file using PySubtrans configuration."""

    @property
    def available(self) -> bool:
        """Report provider configuration without exposing its value."""
        return self.availability_message is None

    @property
    def availability_message(self) -> str | None:
        """Explain why local provider configuration is not ready."""
        provider = os.environ.get("PROVIDER", "").strip()
        if not provider:
            return "Set PROVIDER and the matching provider environment variables."

        providers = TranslationProvider.get_providers()
        if provider not in providers:
            supported = ", ".join(sorted(providers))
            return (
                f"Unsupported PROVIDER '{provider}'. Supported providers: {supported}."
            )

        missing = [
            name
            for name in _PROVIDER_REQUIRED_ENVIRONMENT[provider]
            if not os.environ.get(name, "").strip()
        ]
        if missing:
            return (
                f"Set {', '.join(missing)} for PROVIDER={provider}, then restart "
                "CueWeaver."
            )

        try:
            TranslationProvider.create_provider(
                provider,
                SettingsType(_provider_settings_from_environment(provider)),
            )
        except (SettingsError, TypeError, ValueError):
            return f"Check the {provider} provider environment variables."
        return None

    def translate(
        self,
        source: Path,
        target_language: str,
        *,
        user_overrides: Mapping[str, str] | None = None,
        work_directory: PathLike[str],
        dynamic_terminology_enabled: bool = True,
        subtitle_terminology_filter_enabled: bool = True,
    ) -> bytes:
        source = Path(source).expanduser().resolve()
        source_text = source.read_text(encoding="utf-8-sig", errors="replace")
        working_source = _prepare_working_source(
            source, target_language, user_overrides, work_directory
        )
        provider = os.environ.get("PROVIDER", "").strip()
        provider_settings = _provider_settings_from_environment(provider)
        option_overrides: dict[str, Any] = {"provider": provider}
        if provider_settings:
            option_overrides["provider_settings"] = {provider: provider_settings}
        options = init_options(
            **option_overrides,
            target_language=target_language,
            prompt=(
                "Translate these subtitles to "
                f"{_prompt_language_description(target_language)}"
            ),
            preprocess_subtitles=False,
            postprocess_translation=False,
            build_terminology_map=dynamic_terminology_enabled,
            stop_on_error=True,
            project_file=True,
        )
        if not options.provider:
            raise ValueError("PySubtrans translation provider is not configured")
        project = init_project(options, filepath=str(working_source), persistent=True)
        project.write_translation = False
        provider = init_translation_provider(options.provider, options)
        terminology_map, static_terminology = _build_terminology_seed(
            getattr(project.subtitles, "terminology_map", None),
            user_overrides,
        )
        if subtitle_terminology_filter_enabled:
            terminology_map = filter_terminology_for_text(
                terminology_map, source_text
            ).terminology
            static_terminology = {
                source_key: target
                for source_key, target in static_terminology.items()
                if terminology_map.get(source_key) == target
            }
        engine = SubtitleTranslator(
            options, provider, resume=True, terminology_map=terminology_map
        )
        if _should_disable_thinking(options.provider, getattr(options, "model", None)):
            _disable_thinking(engine)

        def save_checkpoint(_sender: Any, **_kwargs: Any) -> None:
            project.SaveProjectFile()

        def preserve_static_terminology(_sender: Any, update: Any) -> None:
            for source_key, target in static_terminology.items():
                _overlay_terminology(engine.terminology_map, source_key, target)
            update.terminology_map = dict(engine.terminology_map)

        engine.events.batch_translated.connect(save_checkpoint, weak=False)
        if static_terminology:
            engine.events.terminology_updated.connect(
                preserve_static_terminology, weak=False
            )
        try:
            project.TranslateSubtitles(engine)
            if engine.aborted:
                raise RuntimeError("PySubtrans translation was aborted")
            if engine.errors:
                raise RuntimeError(
                    f"PySubtrans reported {len(engine.errors)} translation error(s)"
                )
            if not project.subtitles.all_translated:
                raise RuntimeError("PySubtrans did not translate every subtitle")
            return _save_translation_bytes(project, source.suffix)
        finally:
            project.SaveProjectFile()
            engine.events.batch_translated.disconnect(save_checkpoint)
            if static_terminology:
                engine.events.terminology_updated.disconnect(
                    preserve_static_terminology
                )


def _disable_thinking(engine: SubtitleTranslator) -> None:
    """Explicitly select non-thinking mode for OpenAI-compatible providers."""

    # PySubtrans 1.6.0 has no public request-body hook.
    client = engine.client
    original_generate_request_body = client._generate_request_body

    def generate_request_body(*args: Any, **kwargs: Any) -> dict[str, Any]:
        request_body = original_generate_request_body(*args, **kwargs)
        if not isinstance(request_body, dict):
            raise TypeError("PySubtrans returned an invalid provider request body")
        request_body["thinking"] = {"type": "disabled"}
        return request_body

    client._generate_request_body = generate_request_body


def _should_disable_thinking(provider: str, model: str | None) -> bool:
    if provider == "DeepSeek":
        return True
    return (
        provider == "Custom Server"
        and model is not None
        and model.strip().casefold().startswith("deepseek-")
    )


def _provider_settings_from_environment(provider: str) -> dict[str, str]:
    settings = _PROVIDER_ENVIRONMENT_SETTINGS.get(provider, {})
    return {
        setting: value
        for setting, environment_name in settings.items()
        if (value := os.environ.get(environment_name))
    }


def _build_terminology_seed(
    persisted: object, user_overrides: Mapping[str, str] | None
) -> tuple[dict[str, str], dict[str, str]]:
    terminology_map = (
        {str(source): str(target) for source, target in persisted.items()}
        if isinstance(persisted, dict)
        else {}
    )
    static_terminology: dict[str, str] = {}
    if user_overrides is not None:
        for source, target in user_overrides.items():
            _overlay_terminology(static_terminology, source, target)
            _overlay_terminology(terminology_map, source, target)
    return terminology_map, static_terminology


_SUBTITLE_LANGUAGE_ALIASES = {
    "big5": "zh-Hant",
    "chs": "zh-Hans",
    "cht": "zh-Hant",
    "gb": "zh-Hans",
    "gb18030": "zh-Hans",
    "gb2312": "zh-Hans",
    "gbk": "zh-Hans",
    "in": "id",
    "iw": "he",
    "ji": "yi",
    "latam": "es-419",
    "pob": "pt-BR",
    "spl": "es-419",
    "zhs": "zh-Hans",
    "zht": "zh-Hant",
    "chi": "zh",
    "cze": "cs",
    "dut": "nl",
    "esla": "es-419",
    "fre": "fr",
    "ger": "de",
    "gre": "el",
    "mac": "mk",
    "may": "ms",
    "per": "fa",
    "rum": "ro",
    "slo": "sk",
    "tib": "bo",
    "wel": "cy",
}


def _prompt_language_description(target_language: str) -> str:
    language_tag = _SUBTITLE_LANGUAGE_ALIASES.get(
        target_language.casefold(), target_language
    )
    if not langcodes.tag_is_valid(language_tag):
        return target_language
    description = langcodes.Language.get(language_tag).display_name("en")
    return (
        target_language if description.startswith("Unknown language") else description
    )


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
    user_overrides: Mapping[str, str] | None,
    work_directory: PathLike[str],
) -> Path:
    source_content = source.read_bytes()
    key_material = b"\0".join(
        (str(source).encode("utf-8"), target_language.encode("utf-8"), source_content)
    )
    if user_overrides:
        key_material += b"\0" + json.dumps(
            sorted(user_overrides.items()), ensure_ascii=False
        ).encode("utf-8")
    translation_directory = (
        Path(work_directory).expanduser().resolve()
        / (hashlib.sha256(key_material).hexdigest()[:16])
    )
    translation_directory.mkdir(parents=True, exist_ok=True)
    working_source = translation_directory / source.name
    if not working_source.exists() or working_source.read_bytes() != source_content:
        working_source.write_bytes(source_content)
    return working_source


def _save_translation_bytes(project: Any, suffix: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="cueweaver-translation-") as directory:
        translated_path = Path(directory) / f"translated{suffix}"
        project.subtitles.SaveTranslation(str(translated_path))
        return translated_path.read_bytes()
