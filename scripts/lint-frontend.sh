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
  corepack pnpm --dir web format:check
  corepack pnpm --dir web lint
  corepack pnpm --dir web typecheck
  corepack pnpm --dir web build
else
  corepack pnpm --dir web lint:fix
  corepack pnpm --dir web format
  corepack pnpm --dir web typecheck
  corepack pnpm --dir web build
fi
