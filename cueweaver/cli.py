"""Command-line interaction surface for one Media per Job."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType
from typing import TextIO

from .job import (
    JobCanceled,
    JobRunner,
    JobState,
    SourceSelection,
    SourceSelectionError,
    SubtitleCandidate,
    format_candidates,
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
    parser.add_argument(
        "--tmdb-series-id",
        "--series-id",
        dest="series_id",
        help="TMDb series ID for Context gathering",
    )
    parser.add_argument(
        "--season-number",
        "--season",
        dest="season_number",
        type=int,
        help="Series season number for TMDb Context",
    )
    parser.add_argument(
        "--episode-number",
        "--episode",
        dest="episode_number",
        type=int,
        help="Series episode number for TMDb Context",
    )
    parser.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="Ignore cached TMDb Context and fetch it again",
    )
    parser.add_argument(
        "--no-metadata-fetch",
        action="store_true",
        help="Skip automatic Context and Glossary fetching, including the cache",
    )
    dynamic_terminology = parser.add_mutually_exclusive_group()
    dynamic_terminology.add_argument(
        "--dynamic-terminology",
        dest="dynamic_terminology_enabled",
        action="store_true",
        help="Enable dynamic terminology discovery (default)",
    )
    dynamic_terminology.add_argument(
        "--no-dynamic-terminology",
        dest="dynamic_terminology_enabled",
        action="store_false",
        help="Disable dynamic terminology discovery",
    )
    parser.set_defaults(dynamic_terminology_enabled=None)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write a durable JSONL trace of PySubtrans translation requests",
    )
    parser.add_argument(
        "--user-override-directory",
        "--override-directory",
        dest="user_override_directory",
        type=Path,
        help=(
            "Directory containing one <series-id>.json User override file "
            "per series (or one <media-stem>.json file per film)"
        ),
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
    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.series_id is None and (
        args.season_number is not None
        or args.episode_number is not None
        or args.refresh_metadata
    ):
        parser.error(
            "--tmdb-series-id is required with season, episode, or metadata refresh"
        )
    active_runner = runner or JobRunner(
        source_selector=_prompt_for_source,
        discovery_observer=_display_candidates,
        progress_observer=_display_progress,
        selection_observer=_display_selection,
        language_priority=args.language_priority,
        user_override_directory=args.user_override_directory,
    )

    def request_cancel(_signal_number: int, _frame: FrameType | None) -> None:
        active_runner.cancel()
        raise JobCanceled("Job canceled")

    handler_installed = False
    previous_handler = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, request_cancel)
        handler_installed = True
        run_options = {
            "target_language": args.target_language,
            "source": args.source,
            "source_language": args.source_language,
        }
        if args.dynamic_terminology_enabled is not None:
            run_options["dynamic_terminology_enabled"] = (
                args.dynamic_terminology_enabled
            )
        if args.series_id is not None:
            run_options.update(
                series_id=args.series_id,
                season_number=args.season_number,
                episode_number=args.episode_number,
                refresh_metadata=args.refresh_metadata,
            )
        if args.debug:
            run_options["debug"] = True
        if args.no_metadata_fetch:
            run_options["no_metadata_fetch"] = True
        result = active_runner.run(args.media, **run_options)
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
        if result.trace_path is not None:
            print(f"  trace: {result.trace_path}", file=sys.stderr)
        if args.debug:
            _display_usage(result.token_usage, file=sys.stderr)
        print("  published: no", file=sys.stderr)
        return 1
    if result.state is JobState.FAILED:
        print(f"Job failed: {result.error}", file=sys.stderr)
        print(
            f"  lifecycle: {' -> '.join(state.value for state in result.lifecycle)}",
            file=sys.stderr,
        )
        if result.trace_path is not None:
            print(f"  trace: {result.trace_path}", file=sys.stderr)
        if args.debug:
            _display_usage(result.token_usage, file=sys.stderr)
        return 1

    if result.metadata_degradation is not None:
        print(
            f"  metadata: degraded: {result.metadata_degradation}",
            file=sys.stderr,
        )

    assert result.source is not None
    assert result.published_path is not None
    print("Job published")
    print(f"  source: {result.source.label}")
    print(f"  target: {result.target_language}")
    print(f"  lifecycle: {' -> '.join(state.value for state in result.lifecycle)}")
    print(f"  output: {result.published_path}")
    print(f"  no-op: {'yes' if result.no_op else 'no'}")
    if result.trace_path is not None:
        print(f"  trace: {result.trace_path}")
    if args.debug:
        _display_usage(result.token_usage)
    return 0


def _prompt_for_source(
    candidates: tuple[SubtitleCandidate, ...],
) -> SubtitleCandidate:
    print("Source selection required", file=sys.stderr)
    print("Choose a Source number: ", file=sys.stderr, end="", flush=True)
    try:
        choice = input("").strip()
    except EOFError as error:
        raise SourceSelectionError(
            "Source selection requires an interactive choice"
        ) from error
    except KeyboardInterrupt as error:
        raise JobCanceled("Job canceled") from error
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
    print(format_candidates(candidates), file=sys.stderr)


def _display_progress(state: JobState) -> None:
    print(f"[progress] {state.value}", file=sys.stderr)


def _display_selection(selection: SourceSelection) -> None:
    message = (
        f"Source selected ({selection.mode.value}): {selection.candidate.label} "
        f"[{selection.candidate.subtype.value}, "
        f"I/O cost {selection.candidate.io_cost}"
    )
    if selection.reason is not None:
        message += f"; {selection.reason}"
    print(f"{message}]", file=sys.stderr)


def _display_usage(
    token_usage: dict[str, object] | None, *, file: TextIO | None = None
) -> None:
    if token_usage is None:
        return
    output = sys.stdout if file is None else file
    usage_labels = {
        "prompt_tokens": "input",
        "output_tokens": "output",
        "reasoning_tokens": "reasoning",
    }
    fields = [
        f"{label}={token_usage.get(key, 'unknown')}"
        for key, label in usage_labels.items()
    ]
    fields.extend(
        f"{key}={value}"
        for key, value in token_usage.items()
        if key
        not in {"prompt_tokens", "output_tokens", "reasoning_tokens", "total_tokens"}
        and value is not None
    )
    print(f"  usage: {' '.join(fields)}", file=output)


if __name__ == "__main__":
    raise SystemExit(main())
