"""Command-line interaction surface for one Media per Job."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from .job import JobRunner, JobState


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
        help="External subtitle to use when more than one is discovered",
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
    active_runner = runner or JobRunner()

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
    print(f"  source: {result.source.path}")
    print(f"  target: {result.target_language}")
    print(f"  lifecycle: {' -> '.join(state.value for state in result.lifecycle)}")
    print(f"  output: {result.published_path}")
    print(f"  no-op: {'yes' if result.no_op else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
