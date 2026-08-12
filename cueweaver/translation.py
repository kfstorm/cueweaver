"""PySubtrans adapter for explicit HTTP translation requests."""

from __future__ import annotations

import hashlib
import json
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

from .terminology import filter_terminology_for_text


class PySubtransTranslator:
    """Translate one explicit subtitle file using PySubtrans configuration."""

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
        source_text = source.read_text(encoding="utf-8-sig")
        working_source = _prepare_working_source(
            source, target_language, user_overrides, work_directory
        )
        options = init_options(
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
        / "translation"
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
