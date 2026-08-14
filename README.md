# CueWeaver

CueWeaver is a locally deployed Web product for translating subtitles. The
official production container serves the built responsive Web shell and the
constrained product API from one single-worker ASGI process. The Web workflow
browses a mounted Media root, discovers subtitle candidates, and runs durable
Jobs. There is no authentication, event stream, or cancellation API.

## Run

Build and run the supported product with Media and persistent Work mounts:

```bash
docker build -t cueweaver .
docker run --rm -p 127.0.0.1:8000:8000 \
  -e CUEWEAVER_MEDIA_ROOT=/media \
  -e CUEWEAVER_WORK_ROOT=/work \
  -v /path/to/media:/media \
  -v cueweaver-work:/work \
  cueweaver
```

Open `http://localhost:8000`. `CUEWEAVER_MEDIA_ROOT` must be an absolute,
readable directory. A Job needs its selected Media directory to be writable
when CueWeaver publishes an output beside the source Media. The Work root is
created when absent and must support reading, writing, directory creation, and
atomic replacement. Keep the Work volume across container replacements and
restarts: active Jobs recovered at startup become `Interrupted`, and terminal
history remains available.

The product is trusted-local software with no authentication. Bind port 8000
only to trusted local access or put it behind an authenticated reverse proxy
before exposing it to another network.

For local development, run the API-only backend behind Vite:

```bash
scripts/dev.sh
```

The script expects an environment prepared with `uv sync` and
`pnpm --dir web install`. It uses `.cueweaver/dev/media` and
`.cueweaver/dev/work` by default. `CUEWEAVER_MEDIA_ROOT`,
`CUEWEAVER_WORK_ROOT`, `API_PORT`, and `WEB_PORT` override those defaults.
Vite proxies `/api` to the loopback-only backend; production needs neither a
Node server nor CORS configuration.

An unconfigured PySubtrans provider does not prevent startup. The shell,
Media browsing, Discovery, Term map management, and Job history remain
available, while new Job submission is disabled until the provider is
configured and CueWeaver is restarted.

## HTTP API

All JSON mutations require `Content-Type: application/json`. Unknown request
fields are rejected. Successful responses are JSON and normally use HTTP 200.
Failures are structured JSON with at least `error_code` and `message`; safe
path, stream, and field context may be included. Absolute Media and Work root
paths, provider credentials, and tracebacks are never returned.

The product accepts Media-relative paths only. Paths must not be absolute,
contain `..` components, backslashes, or resolve outside the mounted Media
root. API timestamps are RFC 3339 strings in UTC, using a `Z` suffix. Clients
should display them in the user's local time zone where appropriate, while
preserving the UTC value for API and history comparisons.

### Status

`GET /api/status` reports readiness without revealing runtime configuration:

```json
{
  "api": {"ready": true},
  "roots": {"ready": true},
  "translation_provider": {"ready": true},
  "worker": {"ready": true, "mode": "single"}
}
```

When the provider is unavailable, `translation_provider` includes an
actionable `message` and the rest of the product remains usable.

### Media and Discovery

`POST /api/media/browse` accepts `{"path":"Shows"}` and returns readable
directory entries relative to Media. Media entries include supported media
files and optional NFO title/year metadata. `POST /api/media/discover`
accepts `{"path":"Shows/Movie.mkv"}` and returns usable External and text
Embedded subtitle candidates plus unsupported candidates. External candidates
are same-stem `.srt`, `.ass`, or `.vtt` files. Embedded candidates include
their ffprobe stream index, format, and raw language/title tags.

### Term Maps

`GET /api/term-maps` lists persistent Term maps. Create one with:

```json
{
  "name": "Characters",
  "content": {"Captain": "队长", "Ship": "舰船"}
}
```

`POST /api/term-maps` returns the new identifier. The detail endpoint
`/api/term-maps/{id}` supports `GET`, `PATCH` for the name, `PUT` to replace
the complete content, and `DELETE`. Names are unique case-insensitively and
each map entry has a non-empty source and target term.

### Jobs

`POST /api/jobs` creates one durable Job. External subtitle Jobs provide
`media_path` and `subtitle_path`; Embedded subtitle Jobs provide
`media_path`, `stream_index`, and `source_format` (`srt`, `ass`, or `vtt`).
Both require `target_language_code` and may provide `term_map_id`, terminology
flags, `output_suffix`, and `output_conflict_policy` (`append-number` or
`overwrite`). A Term map is snapshotted into the Job request at creation, so
later edits do not change a queued or historical Job.

Jobs are processed serially and move through `Queued`, `Extracting`, and
`Translating` to `Completed`, `Failed`, or `Interrupted`. `GET /api/jobs`
returns durable history and `GET /api/jobs/{id}` returns one Job, including
`created_at`, `started_at`, and `finished_at` UTC timestamps when available.
Queued Jobs include a `queue_position`.

`POST /api/jobs/{id}/retry` retries a Failed or Interrupted Job in place,
preserving its identifier and incrementing its attempt. Embedded Jobs repeat
Extraction before Translation. `DELETE /api/jobs/{id}` deletes an eligible
terminal Job and its durable record. `DELETE /api/jobs/completed` clears
Completed history and reports any records that could not be removed.

Output naming uses the Media stem, the requested suffix, and the discovered
source format. `append-number` selects the first available numbered output;
`overwrite` replaces an existing output atomically only after successful
translation. Failed translations never replace an existing output.

## Integration Seams

The public product factory is
`cueweaver.create_product_app(media_root, work_root, translator)`. It is the
embedding and test seam for supplying temporary roots and a deterministic
Translator. `cueweaver.create_app(application, media_root)` remains the
dependency-injection seam for composing the HTTP adapters around an application
double or another host. These seams do not restore the removed explicit-path
HTTP endpoints; the product API is the only supported Web interface.

PySubtrans provider configuration remains the provider library's process
configuration. CueWeaver does not add provider request fields or provider
credential environment-variable fallbacks.

## Verification

The release checks use temporary roots and a deterministic fake Translator; no
provider credentials or real LLM calls are needed:

```bash
scripts/test-backend.sh
scripts/test-frontend.sh
scripts/lint-backend.sh --check
scripts/lint-frontend.sh --check
scripts/test-e2e.sh
```

The Docker Playwright matrix runs the production SPA and ASGI app and covers
External and Embedded subtitle Jobs, serial queueing, failure and retry,
Term map snapshots, numbered and overwrite output, restart history, deletion,
and desktop/mobile workflows. Focused PySubtrans adapter tests remain the
provider integration boundary.
