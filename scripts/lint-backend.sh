#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

CHECK_ONLY=false
case "${1:-}" in
"") ;;
--check)
  CHECK_ONLY=true
  ;;
*)
  printf 'Usage: %s [--check]\n' "$0" >&2
  exit 2
  ;;
esac

if [[ "${CI:-}" == "true" ]]; then
  CHECK_ONLY=true
fi

if [[ "$CHECK_ONLY" == true ]]; then
  uv run ruff check cueweaver tests
  uv run ruff format --check cueweaver tests
else
  uv run ruff check --fix cueweaver tests
  uv run ruff format cueweaver tests
  uv run ruff check cueweaver tests
fi

uv run mypy
uv run vulture cueweaver vulture_whitelist.py
uv run vulture
uv run tach check-external
uv run pymarkdown -d MD013,MD036 scan -r AGENTS.md CONTEXT.md README.md docs
