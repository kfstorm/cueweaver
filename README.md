# CueWeaver

CueWeaver is a self-hosted media subtitle translation tool. The first
vertical slice runs one Media through a single Source Job.

## Run

Use `uv` for the project environment:

```bash
uv sync
uv run cueweaver run /path/to/Movie.mkv --target-language zh
```

Add `--debug` to record the PySubtrans translation interaction:

```bash
uv run cueweaver run /path/to/Movie.mkv --target-language zh --debug
```

CueWeaver writes one durable `trace-<UTC timestamp>-<random suffix>.jsonl`
file in the Job workspace and reports its path when the Job succeeds, fails,
or is canceled. Trace files are retained with the workspace and are not
automatically cleaned up. Debug tracing covers the built-in PySubtrans
translation requests only; metadata requests and custom translators are not
traced.

Trace files use schema version 1. Each line has `schema_version`, `event`,
`timestamp`, and `run_id`. Events are `run_started`, `attempt_started`,
`response_completed`, `attempt_failed`, `retry_scheduled`, and `run_finished`.
Request attempts can be correlated
with `operation_id`, `request_id`, `batch_number`, `scene`, `attempt`, and
`attempt_kind`; logical retries and batch splits include a parent operation.
Request bodies, prompts, parsed responses, final assembled responses, token
usage, and structured errors may be recorded. API keys,
authorization headers, settings, and other transport credentials are
excluded. The trace is not a redacted subtitle copy: subtitle content,
Context, Glossary, and provider response text may be written to it. Protect
trace files with the same care as the source subtitles.

The Target language is required and has no product default. It can also be
configured for a shell session with `CUEWEAVER_TARGET_LANGUAGE`. A subtitle
named after the Media, such as `Movie.zh.srt`, `Movie.en.ass`, or
`Movie.vtt`, is discovered automatically when it is the only eligible
External subtitle. MKV and MP4 Embedded subtitles are listed from container
metadata with `ffprobe`; install FFmpeg so `ffprobe` is available. Text
Embedded Sources require an explicit selection and are
materialized lazily through `seconv` into the configured Job work directory's
Extraction cache. Bitmap
Sources are listed as disabled because Subtitle OCR is outside v0.1. Durable
Job files are kept outside the Media directory under
`$XDG_CACHE_HOME/cueweaver/jobs` or `~/.cache/cueweaver/jobs`; set
`CUEWEAVER_WORK_DIRECTORY` to choose another root. Set `CUEWEAVER_SECONV` when
`seconv` is not on `PATH`.
Use `--language-priority en,ja` or `CUEWEAVER_SOURCE_LANGUAGE_PRIORITY` to
break same-cost Source ties without content sniffing.

The terminal lists every discovered Source with its label, subtype, I/O cost,
and availability. It then reports the selected Source exactly once, including
whether the choice was explicit, automatic, or interactive. Automatic choices
also state the primary reason, such as the lowest I/O cost or a language
priority. Candidate lists, selection messages, and lifecycle progress are
written to stderr; the final successful Job summary is written to stdout.
During a Job, progress reports each lifecycle transition once: `discovered`,
`extracting`, `metadata`, `translating`, `validating`, `publishing`, and the
terminal `published`, `failed`, or `canceled` state when applicable. Progress
does not include prompts, API keys, or subtitle payloads. A canceled Job is not
published automatically; when an intermediate result is retained, its path is
reported for an explicit follow-up decision.

For an episode Job, pass `--tmdb-series-id`, `--season`, and `--episode` to
gather the full TMDb series and episode overviews as Context before
translation. Set `CUEWEAVER_TMDB_API_KEY` (or `TMDB_API_KEY`) for TMDb access.
Successful Context is cached by the resolved series QID when available (with
the provider series ID as a fallback) in the long-lived user cache at
`$XDG_CACHE_HOME/cueweaver/metadata` or `~/.cache/cueweaver/metadata`; set
`CUEWEAVER_METADATA_CACHE` to choose another location. The cache has no expiry
or polling. Use `--refresh-metadata` for an explicit refresh. Use
`--no-metadata-fetch` to skip the entire automatic metadata stage; this ignores
both cached and provider-supplied Context and Glossary Terms while preserving
User overrides. A missing key or provider failure is reported as a metadata
degradation hint and the Job continues with baseline translation and no fetched
metadata.
For an already-published degraded Job, `JobRunner.retry_metadata` refreshes the
metadata cache without invoking translation again or changing the published
baseline artifact.

The same metadata stage builds an automatic Glossary from series-linked
Wikidata entities in the configured Target language. It includes only
structured entity evidence for characters, organizations/factions, locations,
and species, and records each Term's provider, source URL, and entity ID.
Missing target labels may use an exact structured Wikipedia `langlinks`/
`pageprops` lookup; article prose, tables, and subtitle content are never
scraped. Ambiguous or unsupported mappings are dropped, so a series with no
usable relations continues with an empty Glossary and baseline translation.
Glossary Terms are cached at the same series scope and seeded into PySubtrans;
dynamic terminology learning remains enabled for uncovered terms. Use
`--no-dynamic-terminology` or set
`CUEWEAVER_DYNAMIC_TERMINOLOGY_MAP=false` to disable it for a Job. The paired
`--dynamic-terminology` option explicitly enables it, and an explicit CLI value
overrides the environment variable. The environment variable accepts
`true`/`false`, `yes`/`no`, and `1`/`0`.

Dynamic terminology discovery can make later prompts grow as new mappings are
learned, increasing token usage. Disabling it keeps prompts deterministic from
the static Glossary and User override seeds, which can improve consistency and
DeepSeek prefix-cache reuse, but uncovered terms will not be learned between
batches. The two modes use separate Job checkpoints.

User overrides are loaded from one JSON file per scope. By default, files live
under `$XDG_CONFIG_HOME/cueweaver/overrides` or `~/.config/cueweaver/overrides`;
set `CUEWEAVER_USER_OVERRIDE_DIRECTORY` or pass
`--user-override-directory` to choose another directory. Name a series file
`<series-id>.json`; a film uses its Media stem, such as `Movie.json` (scoped
names that need filesystem sanitization receive a short digest suffix). Each
file is a JSON object mapping Source terms to Target-language terms:
There is no global override file.
Without an explicitly selected directory, a missing scope file means that no
User override is defined. When a directory is explicitly selected, the scope
file is required; use `{}` for a scope with no mappings.

```json
{
  "Jon Snow": "Custom name"
}
```

The User override seed wins over automatic Terms and PySubtrans's dynamic
learning. Automatic Term provenance remains available in the Job result. A
malformed override file fails the Job with the file path and validation error;
the automatic Glossary is not discarded.

If the Source language already matches the Target language, the Job skips the
translator, validates the subtitle structure, and atomically publishes the
result beside the Media. SRT, ASS, and VTT are supported. A non-Target-language
Source uses PySubtrans 1.6.0 with scene/batch translation, rolling context, and
resume checkpoints before the same Validation and Publishing stages.

The default provider is DeepSeek V4 flash. Set `DEEPSEEK_API_KEY`, or use the
CueWeaver-specific `CUEWEAVER_TRANSLATION_*` variables. An OpenAI-compatible
server can be selected with `CUEWEAVER_TRANSLATION_PROVIDER=openai-compatible`,
`CUEWEAVER_TRANSLATION_SERVER_ADDRESS`, and optionally
`CUEWEAVER_TRANSLATION_ENDPOINT`, `CUEWEAVER_TRANSLATION_MODEL`, and
`CUEWEAVER_TRANSLATION_API_KEY`. CueWeaver sends
`thinking: {"type": "disabled"}` for the v0.1 fast/low-cost seam. PySubtrans
checkpoint files are kept in CueWeaver's per-Job user cache workspace so a
completed batch is not sent again on a later Job. No temporary CueWeaver files
are written beside the Media.

The adapter also accepts PySubtrans's provider-native settings when present:
`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, and `DEEPSEEK_API_BASE` for DeepSeek, or
`CUSTOM_API_KEY`, `CUSTOM_MODEL`, `CUSTOM_SERVER_ADDRESS`, and
`CUSTOM_ENDPOINT` for an OpenAI-compatible server.
Before a translated Job gathers metadata, CueWeaver verifies that the selected
provider has the required credentials. A Target-language no-op does not require
translation provider credentials.

## Test

```bash
uv run pytest
```

## Development checks

Install the development dependencies and the local Git hook:

```bash
uv sync --group dev
uv run pre-commit install
```

Run every pre-commit check manually:

```bash
uv run pre-commit run --all-files
```

Run the quality checks without changing files:

```bash
./scripts/lint.sh --check
```
