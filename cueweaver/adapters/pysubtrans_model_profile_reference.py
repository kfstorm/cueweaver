"""Best-effort static metadata for PySubtrans Model Profile settings."""

from __future__ import annotations

import ast
import importlib.metadata
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GETTER_TYPES = {
    "get_str": "string",
    "get_int": "integer",
    "get_float": "number",
    "get_bool": "boolean",
    "get_list": "array",
}
_PLACEHOLDER_TEXT = (
    "unable to",
    "no models",
    "check api",
    "try again",
    "try a different",
)
_PROVIDER_SOURCE_PART_COUNT = 3
_OPTION_TUPLE_LENGTH = 2
_MIN_STATIC_CHOICE_COUNT = 2


@dataclass(frozen=True)
class SettingReference:
    key: str
    type: str | None
    description: str | None = None
    choices: list[str] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "type": self.type,
            "description": self.description,
            "choices": self.choices,
        }


@dataclass(frozen=True)
class ProviderReference:
    name: str
    settings: dict[str, SettingReference]


@dataclass(frozen=True)
class PySubtransModelProfileReference:
    version: str
    providers: dict[str, ProviderReference]

    def as_dict(self) -> dict[str, object]:
        return {
            "pysubtrans_version": self.version,
            "providers": {
                name: [setting.as_dict() for setting in provider.settings.values()]
                for name, provider in self.providers.items()
            },
        }


def parse_provider_source(source: str) -> ProviderReference | None:
    """Extract literal provider keys and the static parts of GetOptions()."""
    tree = ast.parse(source)
    for class_node in ast.walk(tree):
        if not isinstance(class_node, ast.ClassDef):
            continue
        name = _class_provider_name(class_node)
        if name is None:
            continue
        methods = {
            node.name: node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        keys = _constructor_settings(methods.get("__init__"))
        options = _get_options(methods.get("GetOptions"))
        settings = {
            key: SettingReference(
                key=key,
                type=setting_type,
                description=_choose_description(
                    options.get(key, {}).get("descriptions", [])
                ),
                choices=options.get(key, {}).get("choices"),
            )
            for key, setting_type in keys.items()
        }
        return ProviderReference(name=name, settings=settings)
    return None


@lru_cache(maxsize=1)
def pysubtrans_model_profile_reference() -> PySubtransModelProfileReference:
    """Read the installed source once and return metadata for local providers."""
    version = "unknown"
    extracted: dict[str, ProviderReference] = {}
    try:
        distribution = importlib.metadata.distribution("PySubtrans")
        version = distribution.version
        files = distribution.files
        if files is None:
            logger.warning(
                "PySubtrans distribution has no file manifest for setting references"
            )
        else:
            provider_files = sorted(
                (
                    file
                    for file in files
                    if len(file.parts) == _PROVIDER_SOURCE_PART_COUNT
                    and file.parts[:2] == ("PySubtrans", "Providers")
                    and file.name.startswith("Provider_")
                    and file.suffix == ".py"
                ),
                key=lambda file: file.name,
            )
            for package_file in provider_files:
                source_path = Path(str(distribution.locate_file(package_file)))
                try:
                    source = source_path.read_text(encoding="utf-8")
                    provider = parse_provider_source(source)
                except (OSError, UnicodeError, SyntaxError) as error:
                    logger.warning(
                        "Could not parse PySubtrans provider source %s (%s)",
                        package_file.name,
                        type(error).__name__,
                    )
                    continue
                if provider is None:
                    logger.warning(
                        "No provider settings found in PySubtrans source %s",
                        package_file.name,
                    )
                    continue
                extracted[provider.name] = provider
    except Exception as error:
        logger.warning(
            "Could not read installed PySubtrans sources for setting references (%s)",
            type(error).__name__,
        )

    registered_names = _registered_provider_names()
    provider_names = registered_names or sorted(extracted)
    providers: dict[str, ProviderReference] = {}
    for name in provider_names:
        reference = extracted.get(name)
        if reference is None:
            logger.warning(
                "No AST setting reference was extracted for provider %s", name
            )
            reference = ProviderReference(name=name, settings={})
        providers[name] = reference
    return PySubtransModelProfileReference(version=version, providers=providers)


def _registered_provider_names() -> list[str]:
    try:
        # Keep provider registry loading out of application startup.
        from PySubtrans.TranslationProvider import TranslationProvider  # noqa: PLC0415

        return sorted(TranslationProvider.get_providers())
    except Exception as error:
        logger.warning(
            "Could not read the local PySubtrans provider registry (%s)",
            type(error).__name__,
        )
        return []


def _class_provider_name(class_node: ast.ClassDef) -> str | None:
    for node in class_node.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "name"
            for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def _constructor_settings(
    method: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> dict[str, str | None]:
    settings: dict[str, str | None] = {}
    if method is None:
        return settings
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not _is_super_init(node):
            continue
        for argument in node.args:
            for nested in ast.walk(argument):
                if not (
                    isinstance(nested, ast.Call)
                    and _call_name(nested.func) == "SettingsType"
                    and nested.args
                    and isinstance(nested.args[0], ast.Dict)
                ):
                    continue
                for key_node, value_node in zip(
                    nested.args[0].keys, nested.args[0].values, strict=True
                ):
                    key = _literal_string(key_node)
                    if key is None:
                        continue
                    getters = {
                        call.func.attr
                        for call in ast.walk(value_node)
                        if isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "settings"
                        and call.func.attr in _GETTER_TYPES
                    }
                    setting_type = (
                        _GETTER_TYPES[next(iter(getters))]
                        if len(getters) == 1
                        else None
                    )
                    settings[key] = setting_type
    return settings


def _get_options(
    method: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> dict[str, dict[str, Any]]:
    options: dict[str, dict[str, Any]] = {}
    if method is None:
        return options
    nodes = sorted(ast.walk(method), key=lambda node: getattr(node, "lineno", 0))
    for node in nodes:
        option_dict: ast.Dict | None = None
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
            node.value, ast.Dict
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "options"
                for target in targets
            ):
                option_dict = node.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "options"
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            option_dict = node.args[0]

        if option_dict is not None:
            for key_node, value_node in zip(
                option_dict.keys, option_dict.values, strict=True
            ):
                key = _literal_string(key_node)
                if key is not None:
                    _record_option(options, key, value_node)

        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, (ast.Tuple, ast.List))
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].value, ast.Name)
            and node.targets[0].value.id == "options"
        ):
            key = _literal_string(node.targets[0].slice)
            if key is not None:
                _record_option(options, key, node.value)
    return options


def _record_option(
    options: dict[str, dict[str, Any]], key: str, value: ast.expr
) -> None:
    if (
        not isinstance(value, (ast.Tuple, ast.List))
        or len(value.elts) < _OPTION_TUPLE_LENGTH
    ):
        return
    metadata = options.setdefault(key, {"descriptions": [], "choices": None})
    for description in _description_strings(value.elts[1]):
        if (
            not _is_placeholder_text(description)
            and description not in metadata["descriptions"]
        ):
            metadata["descriptions"].append(description)
    choices = _static_choices(value.elts[0])
    if choices is not None:
        metadata["choices"] = choices


def _static_choices(node: ast.expr) -> list[str] | None:
    if not isinstance(node, ast.List) or len(node.elts) < _MIN_STATIC_CHOICE_COUNT:
        return None
    values = [_literal_string(item) for item in node.elts]
    if any(value is None for value in values):
        return None
    choices = [value for value in values if value is not None]
    if len(set(choices)) != len(choices) or any(
        _is_placeholder_text(value) for value in choices
    ):
        return None
    return choices


def _description_strings(node: ast.expr) -> list[str]:
    if isinstance(node, ast.IfExp):
        return _description_strings(node.body) + _description_strings(node.orelse)
    value = _literal_string(node)
    return [value] if value is not None else []


def _choose_description(descriptions: list[str]) -> str | None:
    return descriptions[0] if descriptions else None


def _literal_string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Call)
        and _call_name(node.func) == "_"
        and len(node.args) == 1
    ):
        return _literal_string(node.args[0])
    return None


def _is_placeholder_text(value: str) -> bool:
    folded = value.casefold()
    return any(marker in folded for marker in _PLACEHOLDER_TEXT)


def _is_super_init(node: ast.Call) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "__init__"
        and isinstance(node.func.value, ast.Call)
        and _call_name(node.func.value.func) == "super"
    )


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None
