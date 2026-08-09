"""Command-line interaction surface for one Media per Job."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from .job import (
    JobRunner,
    JobState,
    SourceSelectionError,
    SubtitleCandidate,
    SubtitleSubtype,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cueweaver",
        description="Run one Media through a single External subtitle Job.",
    )
    parser.add_argument("media", type=Path, help="Media path")
    parser.add_argument(
        "--target-language",
        help="Required Target language (or CUEWEAVER_TARGET_LANGUAGE)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="External subtitle path or Embedded subtitle identifier",
    )
    parser.add_argument(
        "--language-priority",
        help="Comma-separated Source language priority, for example en,ja",
    )
    parser.add_argument(
        "--source-language",
        help="Override language inferred from the Source filename",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: JobRunner | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "run":
        arguments = arguments[1:]
    args = build_parser().parse_args(arguments)
    active_runner = runner or JobRunner(
        source_selector=_prompt_for_source,
        discovery_observer=_display_candidates,
        language_priority=args.language_priority,
    )

    def request_cancel(_signal_number: int, _frame: FrameType | None) -> None:
        active_runner.cancel()

    handler_installed = False
    previous_handler = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, request_cancel)
        handler_installed = True
        result = active_runner.run(
            args.media,
            target_language=args.target_language,
            source=args.source,
            source_language=args.source_language,
        )
    finally:
        if handler_installed:
            signal.signal(signal.SIGINT, previous_handler)
    if result.state is JobState.CANCELED:
        print(f"Job canceled: {result.error}", file=sys.stderr)
        print(
            f"  lifecycle: {' -> '.join(state.value for state in result.lifecycle)}",
            file=sys.stderr,
        )
        if result.intermediate_path is not None:
            print(f"  intermediate: {result.intermediate_path}", file=sys.stderr)
        return 1
    if result.state is JobState.FAILED:
        print(f"Job failed: {result.error}", file=sys.stderr)
        print(
            f"  lifecycle: {' -> '.join(state.value for state in result.lifecycle)}",
            file=sys.stderr,
        )
        return 1

    assert result.source is not None
    assert result.published_path is not None
    print("Job published")
    print(f"  source: {result.source.label}")
    print(f"  target: {result.target_language}")
    print(f"  lifecycle: {' -> '.join(state.value for state in result.lifecycle)}")
    print(f"  output: {result.published_path}")
    print(f"  no-op: {'yes' if result.no_op else 'no'}")
    return 0


def _prompt_for_source(
    candidates: tuple[SubtitleCandidate, ...],
) -> SubtitleCandidate:
    print("Source selection required")
    try:
        choice = input("Choose a Source number: ").strip()
    except EOFError as error:
        raise SourceSelectionError(
            "Source selection requires an interactive choice"
        ) from error
    if not choice.isdigit():
        raise SourceSelectionError("Source selection must be a candidate number")
    index = int(choice) - 1
    if index < 0 or index >= len(candidates):
        raise SourceSelectionError("Source selection is outside the candidate list")
    selected = candidates[index]
    if not selected.selectable:
        raise SourceSelectionError(
            "Bitmap Sources are visible but disabled and cannot be selected"
        )
    return selected


def _display_candidates(candidates: tuple[SubtitleCandidate, ...]) -> None:
    print("Discovered Sources")
    for index, candidate in enumerate(candidates, start=1):
        if candidate.subtype is SubtitleSubtype.BITMAP:
            status = "disabled; needs Subtitle OCR"
        elif candidate.subtype is SubtitleSubtype.EMBEDDED:
            status = "needs Extraction"
        else:
            status = "ready"
        print(
            f"  {index}. {candidate.label} "
            f"[{candidate.subtype.value}, I/O cost {candidate.io_cost}; {status}]"
        )


if __name__ == "__main__":
    raise SystemExit(main())
