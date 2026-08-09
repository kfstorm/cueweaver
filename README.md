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
result beside the Media. SRT, ASS, and VTT are supported. Translation of a
non-Target-language Source is deliberately not configured in this first slice;
that provider integration is the next delivery step.

## Test

```bash
uv run pytest
```
