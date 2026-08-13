#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE="cueweaver-e2e"
CONTAINER="cueweaver-e2e-$$"
ROOTS="$(mktemp -d)"
mkdir "$ROOTS/media" "$ROOTS/work"
chmod 755 "$ROOTS" "$ROOTS/media" "$ROOTS/work"
RUN_USER="$(id -u):$(id -g)"
printf '%s' '<movie><title>Example movie</title><year>2024</year></movie>' \
  >"$ROOTS/media/Example.nfo"
printf '%s\n' \
  '1' \
  '00:00:00,000 --> 00:00:01,000' \
  'Example subtitle' \
  >"$ROOTS/media/Example.en.srt"
# shellcheck disable=SC2329 # Invoked indirectly by the EXIT trap.
cleanup() {
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$ROOTS"
}
trap cleanup EXIT

docker build --tag "$IMAGE" .
docker run --rm --user "$RUN_USER" --volume "$ROOTS/media:/media" "$IMAGE" \
  ffmpeg -v error -f lavfi -i color=c=black:s=16x16:d=1 \
  -f srt -i /media/Example.en.srt \
  -map 0:v:0 -map 1:0 -c:v mpeg4 -t 1 -c:s srt \
  -metadata:s:s:0 language=en /media/Example.mkv
docker run --detach --name "$CONTAINER" \
  --user "$RUN_USER" \
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
