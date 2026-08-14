# CueWeaver

CueWeaver is a local Web product for subtitle translation. Its official
single-worker server hosts a responsive Web shell and HTTP API from one ASGI
application. During the product expansion, it also retains three synchronous
explicit-path operations:

- `POST /api/discover`
- `POST /api/extract`
- `POST /api/translate`

The legacy synchronous operations still return one final JSON response. The Web
product additionally exposes durable Job APIs; there are no event-stream or
cancellation APIs.

## Run

Build the image, then run the supported single-container product with Media and
Work volumes:

```bash
docker build -t cueweaver .
docker run --rm -p 127.0.0.1:8000:8000 \
  -e CUEWEAVER_MEDIA_ROOT=/media \
  -e CUEWEAVER_WORK_ROOT=/work \
  -v /path/to/media:/media \
  -v cueweaver-work:/work \
  cueweaver
```

Open `http://localhost:8000`. The Media root must already be a readable and
writable directory: Jobs publish translated subtitles beside the source Media,
and Job creation fails with `output_directory_unwritable` when it cannot write
there. CueWeaver creates the Work root when absent and verifies it supports the
filesystem operations required for persistent Job state. Keep the Work volume
across container replacements and restarts; Queued, Extracting, and Translating
Jobs found during startup become `Interrupted` and can be retried, while
terminal Job history remains available.

This product is a trusted-local service with no authentication. Bind port 8000
only to trusted local access or place it behind your own authenticated reverse
proxy before exposing it to another network.

For local development, run the API-only backend behind Vite with:

```bash
scripts/dev.sh
```

The script requires `uv`, an already-installed Python environment from `uv sync`,
and `web/node_modules` from `pnpm --dir web install`. By default it preserves
development data in `.cueweaver/dev/media` and `.cueweaver/dev/work`. Set
`CUEWEAVER_MEDIA_ROOT` and/or `CUEWEAVER_WORK_ROOT` to use other absolute paths.
`API_PORT` and `WEB_PORT` override the default ports `8000` and `5173`. Vite is
available to the local network at `http://<host-ip>:5173`; its `/api` requests
are proxied to the loopback-only backend.

An unconfigured PySubtrans provider does not prevent startup. The Web shell
remains available and explains how to enable Translation submission.

For embedding and automated tests, use
`cueweaver.create_product_app(media_root, work_root, translator)`. The original
`cueweaver.create_app(cueweaver.CueWeaverApplication())` seam remains available.

Translation provider configuration remains PySubtrans service-process
configuration. CueWeaver does not add provider configuration request fields or
CueWeaver-specific environment-variable fallbacks.

## HTTP Contract

Requests with JSON bodies use `Content-Type: application/json`. Successful API
responses return JSON, normally with HTTP 200, and use the shapes below.
Failures return JSON with at least `error_code` and
`message`; path and stream context may also be included. Clients must not rely
on a specific status code or fine-grained error-code string.

Unknown request fields are rejected. In particular, there are no `media_path`,
`source_language`, or `no_op` fields on translation requests.

### Product Operations

The production shell uses these APIs.

- `GET /api/status` reports API, roots, Translation provider, and single-worker
  readiness. It never includes absolute root paths or provider credentials. A
  provider can be unavailable while browsing and Job history remain usable;
  Translation submission is disabled until PySubtrans is configured and the
  container is restarted.
- `POST /api/media/browse` accepts a Media-relative `path` and returns the
  readable directory entries. `POST /api/media/discover` accepts a Media-relative
  `path` and returns usable External and Embedded subtitle candidates.
- `GET /api/term-maps` lists Term maps. `POST /api/term-maps` creates one from
  `{"name":"...","content":{"source":"target"}}`; the detail endpoint is
  `/api/term-maps/{id}` and supports `GET`, `PATCH`, `PUT`, and `DELETE` for
  inspection and management.
- `POST /api/jobs` creates an External or Embedded subtitle Job. External Jobs
  provide `media_path` and `subtitle_path`; Embedded Jobs provide `media_path`,
  `stream_index`, and `source_format` (`srt`, `ass`, or `vtt`). Both require
  `target_language_code` and may set `term_map_id`, terminology flags,
  `output_suffix`, and `output_conflict_policy` (`append-number` or
  `overwrite`).
- `GET /api/jobs` lists durable Job history and `GET /api/jobs/{id}` returns one
  Job. Jobs move through `Queued`, `Extracting`, `Translating`, and a terminal
  `Completed`, `Failed`, or `Interrupted` state. `POST /api/jobs/{id}/retry`
  retries a Failed or Interrupted Job; `DELETE /api/jobs/{id}` deletes an
  eligible terminal Job; and `DELETE /api/jobs/completed` clears Completed
  history. Legacy
  `/api/discover`, `/api/extract`, and `/api/translate` remain available.

### Discover

`POST /api/discover` accepts:

```json
{"media_path":"/media/Movie.mkv"}
```

It returns:

```json
{
  "media_path":"/media/Movie.mkv",
  "candidates":[
    {
      "kind":"external",
      "path":"/media/Movie.en.forced.srt",
      "format":"srt",
      "tags":{"language":"en","title":""}
    },
    {
      "kind":"embedded",
      "stream_index":3,
      "format":"ass",
      "tags":{"language":"zhs","title":"Chinese Simplified"}
    }
  ],
  "unsupported_candidates":[
    {"kind":"embedded","stream_index":4,"reason":"bitmap subtitle"}
  ]
}
```

Candidates are usable External or text Embedded subtitles. External subtitles
are same-stem `.srt`, `.ass`, or `.vtt` files beside the Media. Their language
tag is the first non-empty dot-separated suffix after the Media stem and
subtitle extension, and their title is `""`. Embedded tags are raw ffprobe
`language` and `title` values; missing values are `""`. Unsupported subtitle
streams, including Bitmap subtitles, appear separately in
`unsupported_candidates`. ffprobe failure fails the whole request.

### Extract

`POST /api/extract` accepts:

```json
{
  "media_path":"/media/Movie.mkv",
  "stream_index":3,
  "output_path":"/work/Movie.en.ass"
}
```

It returns:

```json
{"output_path":"/work/Movie.en.ass","format":"ass"}
```

Extraction probes the requested Embedded stream then performs lossless
same-format ffmpeg extraction. Supported mappings are `subrip` and `srt` to
`.srt`, `ass` and `ssa` to `.ass`, and `webvtt` to `.vtt`. Bitmap/OCR and other
codecs are rejected. The output extension must match the stream format; missing
output parent directories are created; existing outputs are never overwritten.

### Translate

`POST /api/translate` accepts:

```json
{
  "subtitle_path":"/work/Movie.en.srt",
  "target_language_code":"zh-Hans",
  "output_path":"/media/Movie.zh.srt",
  "work_directory":"/work/requests/123",
  "term_map_path":"/work/terms.json",
  "dynamic_terminology_enabled":true,
  "subtitle_terminology_filter_enabled":true
}
```

`subtitle_path`, `target_language_code`, `output_path`, and `work_directory`
are required non-empty strings. `term_map_path` is optional. Both terminology
flags default to `true`. A term map is an explicit JSON object whose keys and
values are non-empty strings; CueWeaver performs no automatic or network term
map lookup.

It returns only after translation, subtitle validation, and writing the output:

```json
{
  "output_path":"/media/Movie.zh.srt",
  "target_language_code":"zh-Hans",
  "format":"srt"
}
```

Input and output must use the same supported subtitle extension and valid
content. Missing output parents and the work directory are created. Existing
outputs are never overwritten. CueWeaver forwards `target_language_code`
unchanged to PySubtrans and output metadata. It only uses an English language
description in the LLM prompt. Standard, valid BCP 47 tags (including ISO 639-3
codes) use their CLDR English display name. Unknown values retain their raw
prompt value rather than being rejected or guessed.

For common subtitle-file conventions, these exact aliases are used only for
the LLM prompt:

| Alias | Prompt language tag |
| --- | --- |
| `chs`, `zhs`, `gb`, `gbk`, `gb2312`, `gb18030` | `zh-Hans` |
| `cht`, `zht`, `big5` | `zh-Hant` |
| `pob` | `pt-BR` |
| `spl`, `esla`, `latam` | `es-419` |
| `chi` | `zh` |
| `cze` | `cs` |
| `dut` | `nl` |
| `fre` | `fr` |
| `ger` | `de` |
| `gre` | `el` |
| `mac` | `mk` |
| `may` | `ms` |
| `per` | `fa` |
| `rum` | `ro` |
| `slo` | `sk` |
| `tib` | `bo` |
| `wel` | `cy` |
| `iw` | `he` |
| `in` | `id` |
| `ji` | `yi` |

Subtitle attributes such as `forced`, `sdh`, `hi`, `cc`, and `default` are not
language aliases and must not be supplied as `target_language_code`.

## Test

```bash
scripts/test-backend.sh
scripts/test-frontend.sh
scripts/test-e2e.sh
```

## Development Checks

```bash
scripts/lint-backend.sh --check
scripts/lint-frontend.sh --check
```
