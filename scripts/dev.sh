#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
if [[ -n "${CUEWEAVER_MEDIA_ROOT:-}" ]]; then
  MEDIA_ROOT="$CUEWEAVER_MEDIA_ROOT"
  USING_DEFAULT_MEDIA_ROOT=false
else
  MEDIA_ROOT="$ROOT_DIR/.cueweaver/dev/media"
  USING_DEFAULT_MEDIA_ROOT=true
fi
WORK_ROOT="${CUEWEAVER_WORK_ROOT:-$ROOT_DIR/.cueweaver/dev/work}"

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' "uv is required; install dependencies with: uv sync" >&2
  exit 1
fi
if ! command -v pnpm >/dev/null 2>&1; then
  printf '%s\n' "pnpm is required; install it with your Node package manager" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  printf '%s\n' "curl is required to check API readiness" >&2
  exit 1
fi
if [[ ! -d web/node_modules ]]; then
  printf '%s\n' "web/node_modules is missing; install dependencies with: pnpm --dir web install" >&2
  exit 1
fi

port_is_in_use() {
  (echo >/dev/tcp/127.0.0.1/"$1") >/dev/null 2>&1
}

if port_is_in_use "$API_PORT"; then
  printf '%s\n' "API_PORT ${API_PORT} is already in use" >&2
  exit 1
fi
if port_is_in_use "$WEB_PORT"; then
  printf '%s\n' "WEB_PORT ${WEB_PORT} is already in use" >&2
  exit 1
fi

if [[ "$MEDIA_ROOT" != /* || "$WORK_ROOT" != /* ]]; then
  printf '%s\n' "CUEWEAVER_MEDIA_ROOT and CUEWEAVER_WORK_ROOT must be absolute paths" >&2
  exit 1
fi

if [[ "$USING_DEFAULT_MEDIA_ROOT" == true ]]; then
  mkdir -p "$MEDIA_ROOT"
elif [[ ! -d "$MEDIA_ROOT" ]]; then
  printf '%s\n' "CUEWEAVER_MEDIA_ROOT must point to an existing directory" >&2
  exit 1
fi
mkdir -p "$WORK_ROOT"

api_pid=""
cleanup() {
  if [[ -n "$api_pid" ]] && kill -0 "$api_pid" 2>/dev/null; then
    kill -- "-$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

printf '%s\n' "Starting API on http://127.0.0.1:${API_PORT}"
CUEWEAVER_MEDIA_ROOT="$MEDIA_ROOT" \
  CUEWEAVER_WORK_ROOT="$WORK_ROOT" \
  setsid uv run --no-sync uvicorn cueweaver.product:create_development_app_from_env \
  --factory --reload --host 127.0.0.1 --port "$API_PORT" \
  > >(sed 's/^/[api] /') 2> >(sed 's/^/[api] /' >&2) &
api_pid=$!

ready=false
for _attempt in {1..30}; do
  if curl --fail --silent "http://127.0.0.1:${API_PORT}/api/status" >/dev/null; then
    ready=true
    break
  fi
  if ! kill -0 "$api_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done

if [[ "$ready" != true ]]; then
  printf '%s\n' "API failed to become ready" >&2
  kill -- "-$api_pid" 2>/dev/null || true
  wait "$api_pid" 2>/dev/null || true
  exit 1
fi

printf '%s\n' "Starting Web on http://0.0.0.0:${WEB_PORT}"
export API_PORT WEB_PORT
pnpm --dir web dev --host 0.0.0.0 --port "$WEB_PORT" --strictPort
