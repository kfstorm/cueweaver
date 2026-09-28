"""Runtime bridge for PySubtrans Model Profile provider options."""

from __future__ import annotations

import importlib.metadata
import math
from collections.abc import Mapping
from typing import cast

from PySubtrans.SettingsType import SettingsType
from PySubtrans.TranslationProvider import TranslationProvider

from ..application.errors import ServiceError

_OPTION_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}
_OPTION_TUPLE_LENGTH = 2


def pysubtrans_model_profile_reference() -> dict[str, object]:
    """Return the installed version and provider registry names."""
    try:
        version = importlib.metadata.version("PySubtrans")
        providers = sorted(TranslationProvider.get_providers())
    except Exception as error:
        raise ServiceError(
            "model_profile_runtime_failed",
            "Could not load installed Model Profile providers",
        ) from error
    return {"pysubtrans_version": version, "providers": providers}


def pysubtrans_model_profile_options(
    provider: str, settings: Mapping[str, object]
) -> dict[str, object]:
    """Run the selected provider's real option discovery."""
    try:
        providers = TranslationProvider.get_providers()
    except Exception as error:
        raise ServiceError(
            "model_profile_runtime_failed",
            "Could not load installed Model Profile providers",
        ) from error
    provider_class = providers.get(provider)
    if provider_class is None:
        raise ServiceError(
            "invalid_model_profile_provider",
            "Model Profile provider is not installed",
            field="provider",
        )
    try:
        overrides = SettingsType(dict(settings))
        runtime_provider = provider_class(overrides)
        combined = runtime_provider.GetCombinedSettings(SettingsType(dict(settings)))
        runtime_provider.ResetAvailableModels()
        runtime_provider.UpdateSettings(combined)
        selected_model = runtime_provider.selected_model
        available_models = runtime_provider.available_models
        setting_updates: dict[str, str] = {}
        runtime_options = runtime_provider.GetOptions(combined)
        if available_models and selected_model not in available_models:
            if "model" not in runtime_options:
                raise ServiceError(
                    "model_profile_runtime_failed",
                    f"Provider {provider} has available models but exposes no model option",
                    field="provider",
                )
            model = available_models[0]
            combined["model"] = model
            runtime_provider.UpdateSettings(SettingsType({"model": model}))
            setting_updates["model"] = model
            runtime_options = runtime_provider.GetOptions(combined)
        options = [
            _convert_option(key, option, combined)
            for key, option in runtime_options.items()
        ]
        refresh_when_changed = list(runtime_provider.refresh_when_changed)
    except ServiceError:
        raise
    except Exception as error:
        raise ServiceError(
            "model_profile_runtime_failed",
            f"Could not load options for provider {provider}",
            field="provider",
        ) from error
    return {
        "provider": provider,
        "options": options,
        "refresh_when_changed": refresh_when_changed,
        "setting_updates": setting_updates,
    }


def validate_model_profile_options(
    provider: str, settings: Mapping[str, object]
) -> dict[str, object]:
    """Validate explicit values against this complete draft's runtime options."""
    discovered = pysubtrans_model_profile_options(provider, settings)
    options = {
        option["key"]: option
        for option in cast(list[dict[str, object]], discovered["options"])
    }
    for key, value in settings.items():
        option = options.get(key)
        if option is None or not _valid_option_value(option, value):
            raise ServiceError(
                "invalid_model_profile",
                f"Setting {key} is not a valid current provider option",
                field="settings",
            )
    return cast(dict[str, object], discovered["setting_updates"])


def _valid_option_value(option: dict[str, object], value: object) -> bool:
    option_type = option["type"]
    if option_type in {"string", "multiline"}:
        return isinstance(value, str)
    if option_type == "choice":
        choices = option["choices"]
        return isinstance(value, str) and isinstance(choices, list) and value in choices
    if option_type == "boolean":
        return isinstance(value, bool)
    if option_type == "integer":
        return type(value) is int
    if option_type == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    return False


def _convert_option(
    key: object, option: object, combined: SettingsType
) -> dict[str, object]:
    if (
        not isinstance(key, str)
        or not isinstance(option, tuple)
        or len(option) != _OPTION_TUPLE_LENGTH
        or not isinstance(option[1], str)
    ):
        raise ServiceError(
            "unsupported_model_profile_option",
            "Provider returned an unsupported option",
        )
    option_kind, description = option
    choices: list[str] | None = None
    if isinstance(option_kind, list):
        if not all(isinstance(choice, str) for choice in option_kind):
            raise ServiceError(
                "unsupported_model_profile_option",
                f"Provider option {key} has unsupported choices",
                field="settings",
            )
        option_type = "choice"
        choices = list(option_kind)
    elif isinstance(option_kind, str):
        option_type = "multiline" if option_kind == "multiline" else "string"
    else:
        mapped_type = _OPTION_TYPES.get(option_kind)
        if mapped_type is None:
            raise ServiceError(
                "unsupported_model_profile_option",
                f"Provider option {key} has an unsupported type",
                field="settings",
            )
        option_type = mapped_type
    return {
        "key": key,
        "type": option_type,
        "description": description,
        "choices": choices,
        "value": combined.get(key),
    }
