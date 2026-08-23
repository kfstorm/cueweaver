#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE="cueweaver-e2e"
CONTAINER="cueweaver-e2e-$$"
ROOTS="$(mktemp -d)"
mkdir "$ROOTS/media" "$ROOTS/work" "$ROOTS/corrupt-work"
chmod 755 "$ROOTS" "$ROOTS/media" "$ROOTS/work"
RUN_USER="$(id -u):$(id -g)"
printf '%s' '<movie><title>Example movie</title><year>2024</year></movie>' \
  >"$ROOTS/media/Example.nfo"
printf '%s\n' \
  '1' \
  '00:00:00,000 --> 00:00:01,000' \
  'Example subtitle' \
  >"$ROOTS/media/Example.en.srt"
printf '%s' 'not a sqlite database' >"$ROOTS/corrupt-work/jobs.sqlite3"
cp "$ROOTS/corrupt-work/jobs.sqlite3" "$ROOTS/corrupt-work/expected.sqlite3"
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
cp "$ROOTS/media/Example.mkv" "$ROOTS/media/Second.mkv"
cp "$ROOTS/media/Example.en.srt" "$ROOTS/media/Second.en.srt"
docker run --detach --name "$CONTAINER" \
  --user "$RUN_USER" \
  --publish 127.0.0.1:8765:8000 \
  --env CUEWEAVER_MEDIA_ROOT=/media \
  --env CUEWEAVER_WORK_ROOT=/work \
  --volume "$ROOTS/media:/media" \
  --volume "$ROOTS/work:/work" \
  "$IMAGE" uvicorn cueweaver.e2e:create_e2e_app_from_env --factory \
  --host 0.0.0.0 --port 8000 >/dev/null

for _attempt in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8765/api/status >/dev/null; then
    CUEWEAVER_E2E_BASE_URL=http://127.0.0.1:8765 \
      pnpm --dir web test:e2e
    set +e
    CORRUPT_LOG="$ROOTS/corrupt-startup.log"
    timeout 30s docker run --rm --user "$RUN_USER" \
      --env CUEWEAVER_MEDIA_ROOT=/media \
      --env CUEWEAVER_WORK_ROOT=/work \
      --volume "$ROOTS/media:/media" \
      --volume "$ROOTS/corrupt-work:/work" \
      "$IMAGE" uvicorn cueweaver.e2e:create_e2e_app_from_env --factory \
      --host 0.0.0.0 --port 8000 >"$CORRUPT_LOG" 2>&1
    _corrupt_status=$?
    set -e
    if [[ $_corrupt_status -eq 0 || $_corrupt_status -eq 124 ]] ||
      ! grep --quiet "Job database cannot be opened" "$CORRUPT_LOG"; then
      cat "$CORRUPT_LOG" >&2
      printf '%s\n' 'Corrupt SQLite database unexpectedly allowed startup' >&2
      exit 1
    fi
    cmp --silent "$ROOTS/corrupt-work/jobs.sqlite3" \
      "$ROOTS/corrupt-work/expected.sqlite3"
    docker kill --signal KILL "$CONTAINER" >/dev/null
    docker start "$CONTAINER" >/dev/null
    for _restart_attempt in {1..30}; do
      if curl --fail --silent http://127.0.0.1:8765/api/status >/dev/null; then
        CUEWEAVER_E2E_BASE_URL=http://127.0.0.1:8765 \
          CUEWEAVER_E2E_PHASE=restart \
          pnpm --dir web exec playwright test --grep "production restart recovers"
        exit 0
      fi
      sleep 1
    done
    docker logs "$CONTAINER"
    exit 1
  fi
  sleep 1
done

docker logs "$CONTAINER"
exit 1
