"""Execution of one Job's subtitle workflow."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..translation import (
    OutputPublisher,
    TranslateRequest,
    TranslateResult,
    Translation,
    Translator,
)


@dataclass(frozen=True)
class JobExecutionInput:
    """The read-only inputs needed to translate one subtitle for a Job."""

    subtitle_path: Path
    target_language_code: str
    output_path: Path
    work_directory: Path
    term_map: Mapping[str, str] | None = None
    dynamic_terminology_enabled: bool = True
    subtitle_terminology_filter_enabled: bool = True
    overwrite: bool = False


class JobExecution:
    """Run one Job's execution steps behind a synchronous interface."""

    def __init__(self, translator: Translator, output: OutputPublisher) -> None:
        self._translator = translator
        self._output = output

    def execute(self, execution_input: JobExecutionInput) -> TranslateResult:
        term_map_path = _write_term_map(
            execution_input.work_directory, execution_input.term_map
        )
        result = Translation(self._translator, self._output).translate(
            TranslateRequest(
                subtitle_path=execution_input.subtitle_path,
                target_language_code=execution_input.target_language_code,
                output_path=execution_input.output_path,
                work_directory=execution_input.work_directory,
                term_map_path=term_map_path,
                dynamic_terminology_enabled=execution_input.dynamic_terminology_enabled,
                subtitle_terminology_filter_enabled=(
                    execution_input.subtitle_terminology_filter_enabled
                ),
                overwrite=execution_input.overwrite,
            )
        )
        return result


def _write_term_map(
    work_directory: Path, term_map: Mapping[str, str] | None
) -> Path | None:
    if term_map is None:
        return None
    work_directory.mkdir(parents=True, exist_ok=True)
    term_map_path = work_directory / "term-map.json"
    term_map_path.write_text(
        json.dumps(dict(term_map), ensure_ascii=False), encoding="utf-8"
    )
    return term_map_path


__all__ = ["JobExecution", "JobExecutionInput"]
