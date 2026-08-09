# CueWeaver

CueWeaver is a self-hosted media subtitle translation tool. The first
vertical slice runs one Media through a single External subtitle Job.

## Run

Use `uv` for the project environment:

```bash
uv sync
uv run cueweaver run /path/to/Movie.mkv --target-language zh
```

The Target language is required and has no product default. It can also be
configured for a shell session with `CUEWEAVER_TARGET_LANGUAGE`. A subtitle
named after the Media, such as `Movie.zh.srt`, `Movie.en.ass`, or
`Movie.vtt`, is discovered automatically when it is the only eligible
External subtitle.

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
checkpoint files are kept in CueWeaver's per-Job `.cueweaver` work directory so
a completed batch is not sent again on a later Job.

The adapter also accepts PySubtrans's provider-native settings when present:
`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, and `DEEPSEEK_API_BASE` for DeepSeek, or
`CUSTOM_API_KEY`, `CUSTOM_MODEL`, `CUSTOM_SERVER_ADDRESS`, and
`CUSTOM_ENDPOINT` for an OpenAI-compatible server.

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
