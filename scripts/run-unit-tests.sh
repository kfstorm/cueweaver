#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

uv run pytest -q

if command -v pnpm >/dev/null 2>&1; then
  pnpm --dir web test
fi
