#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE="cueweaver-e2e"
CONTAINER="cueweaver-e2e-$$"
ROOTS="$(mktemp -d)"
mkdir "$ROOTS/media" "$ROOTS/work"
printf '%s' '<movie><title>Example movie</title><year>2024</year></movie>' \
  >"$ROOTS/media/Example.nfo"
printf '%s' 'media' >"$ROOTS/media/Example.mkv"

# shellcheck disable=SC2329 # Invoked indirectly by the EXIT trap.
cleanup() {
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$ROOTS"
}
trap cleanup EXIT

docker build --tag "$IMAGE" .
docker run --detach --name "$CONTAINER" \
  --publish 127.0.0.1:8765:8000 \
  --env CUEWEAVER_MEDIA_ROOT=/media \
  --env CUEWEAVER_WORK_ROOT=/work \
  --volume "$ROOTS/media:/media:ro" \
  --volume "$ROOTS/work:/work" \
  "$IMAGE" >/dev/null

for _attempt in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8765/api/status >/dev/null; then
    CUEWEAVER_E2E_BASE_URL=http://127.0.0.1:8765 \
      pnpm --dir web test:e2e
    exit 0
  fi
  sleep 1
done

docker logs "$CONTAINER"
exit 1
