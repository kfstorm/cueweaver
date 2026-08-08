# PySubtrans: real capabilities and integration boundary

Research ticket: [cueweaver#3](https://github.com/kfstorm/cueweaver/issues/3) — part of the v0.1 wayfinder map ([#1](https://github.com/kfstorm/cueweaver/issues/1)).

Established **2026-08-08** against primary sources:

- PyPI project page — <https://pypi.org/project/pysubtrans/> (package **1.6.0**, released 2026-04-26)
- Source clone of the upstream repo `machinewrapped/llm-subtrans` @ commit [`084a574`](https://github.com/machinewrapped/llm-subtrans) (the package lives in its `PySubtrans/` subdirectory) — file paths below are relative to that repo root
- PySubtrans README — `PySubtrans/README.md`
- PySubtrans `pyproject.toml`, `Options.py`, `SubtitleTranslator.py`, `SubtitleProject.py`, `SubtitleBatcher.py`, `SubtitleValidator.py`, `Helpers/ContextHelpers.py`, `TranslationPrompt.py`, `TranslationEvents.py`, `SubtitleSerialisation.py`, `Providers/*`, `CHANGELOG.md`
- GitHub API data for `machinewrapped/llm-subtrans` (stars/forks/issues/releases)
- DeepSeek API docs — <https://api-docs.deepseek.com/news/news260424/> (V4 release note) and pricing page

Fields are `confirmed-fact` (verified directly in code/doc) or `needs-a-prototype` (behavioral quality/granularity that only running the integration can settle).

---

## 1. Translation machinery

### Scene-aware batching — CONFIRMED

- `SubtitleBatcher.BatchSubtitles()` splits lines into scenes whenever the gap between consecutive subtitle end/start times exceeds `scene_threshold` (seconds). (`PySubtrans/SubtitleBatcher.py:14-47`)
- Each scene is uniformly subdivided into batches no larger than `max_batch_size`, by recursing at the largest time-gap between at least `min_batch_size` lines. (`PySubtrans/SubtitleBatcher.py:65-97`)
- Settings flow from `Options.default_settings` (`scene_threshold` default 60.0s, `min_batch_size` default 10, `max_batch_size` default 30; all configurable) (`PySubtrans/Options.py:48-102`) and are applied via `batch_subtitles()` / `init_subtitles` / `init_project`. (`PySubtrans/__init__.py:422-467`)
- `prevent_overlapping_times` (default False) can nudge overlapping times during batching. (`PySubtrans/Options.py:65`, `SubtitleBatcher.py:30-31`)

### Configurable max-batch-size — CONFIRMED

`max_batch_size` is a first-class option; README notes default 30 is "very conservative … Gemini 2.5 Flash can easily handle batches of 150 lines or more". (`PySubtrans/README.md`, `Max batch size` section)

### Context injection — CONFIRMED

Per batch, `GetBatchContext()` supplies: current scene number + summary, current batch number + summary, `movie_name`, `description` (synopsis), `names` (character list), and a rolling `history` of prior scene/batch summaries capped by `max_context_summaries` (default 10). (`PySubtrans/Helpers/ContextHelpers.py:14-63`)
After each successful batch, the model's `<summary>/<scene>/<synopsis>` blocks update the context for subsequent batches (`SubtitleTranslator.py:288-293`). The same context is rendered into the prompt via `<description>/<names>/<terminology>/<history>/<scene>/<summary>/<batch>` tags (`TranslationPrompt.py:7-10, 181-194`).

**CueWeaver boundary note:** `description`/`movie_name`/`names` are the hooks for narrative Context (`plot synopsis`) and can be set per job via options; no automatic narrative-extraction engine ships with PySubtrans.

### Multiline glossary / terminology-map — CONFIRMED

Two distinct mechanisms exist:

1. **Seeded terminology map**: `init_translator(..., terminology_map=...)` accepts a dict, a `list[str]` of `key::value`, a newline-separated string, or a file path of `key::value` lines; the seed is always injected into prompt context regardless of learning. (`PySubtrans/README.md`, "Terminology map"; `PySubtrans/__init__.py:236-297`)
2. **Terminology-learning** (opt-in `build_terminology_map=True`): the system instructions append `terminology_instructions` asking the model to report names/titles/technical terms as `original::translation` pairs after each batch (`PySubtrans/Instructions.py` `default_terminology_instructions`; `SubtitleTranslator.py:66-67`). The returned `<terminology>` blocks are parsed and accumulate into `translator.terminology_map`, with hallucination guards: a pair is kept only if the source term appears in the batch's original text and the proposed translation appears in the batch's translated text (`SubtitleTranslator.py:572-627`, `TranslationParser.py`).
3. When enabled, the growing map is injected into every subsequent batch's `terminology` context tag (`SubtitleTranslator.py:174-180`) and a `terminology_updated` event fires per batch (`PySubtrans/TranslationEvents.py:14-22`).

**Needs-a-prototype : numerical quality** — whether auto-learning, first-seen-wins resolution (`_update_terminology_map` preserves existing entries on conflict, `SubtitleTranslator.py:610-615`), actually improves inter-batch consistency, and how much prompt-bloat it costs.

### Validation — CONFIRMED, structural only, not semantic quality

`SubtitleValidator.ValidateBatch()` performs *mechanical* checks (each translated line matched to a number, non-empty text, ≤ `max_characters` (default 120), ≤ `max_newlines` (default 2), and that every original in the batch has a translation). (`PySubtrans/SubtitleValidator.py:10-68`) It is NOT a quality/hallucination judge; violations feed the error path below.

### autosplit_on_error — CONFIRMED, opt-in

`autosplit_on_error=False` by default (`SubtitleTranslator.py:50`; `Options.py:84`). When a batch fails validation, `SubtitleTranslator._translate_split_batch()` splits the batch at the largest time-gap near the midpoint (`FindBestSplitIndex`, `SubtitleHelpers.py:95-114`) and re-translates each half, merging results — two extra requests. Takes priority over `retry_on_error`. (`SubtitleTranslator.py:270-285`, `SubtitleTranslator.py:432-505`)
`retry_on_error` (default True) retransmits a failed batch once with retry instructions; `max_retries` (default 1) governs HTTP-level retries. (`Options.py:83-87`)

---

## 2. Providers

### DeepSeek — CONFIRMED out of the box, OpenAI-compatible client

- Built-in `DeepSeekProvider` (`Providers/Provider_DeepSeek.py`) hits `https://api.deepseek.com` `/v1/chat/completions` with `deepseek-chat` as default **model** name (`Provider_DeepSeek.py:16,31,37`). Its client is a subclass of `CustomClient` (the OpenAI-compatible chat client) (`Clients/DeepSeekClient.py:5-16`).
- DeepSeek V4 is **accessed by simply setting the model name**: as DeepSeek's V4 release note says, "Keep base_url, just update model to deepseek-v4-pro or deepseek-v4-flash" (<https://api-docs.deepseek.com/news/news260424/>). So V4 works out of the box, but **requires explicitly configuring the model `deepseek-v4-pro` / `deepseek-v4-flash`** (or env `DEEPSEEK_MODEL`) — LLM-Subtrans's default `deepseek-chat` is the retired-legacy V3 alias and will stop routing in 2026-07-24 (per same note). **needs-a-prototype**: verify the V4 `thinking`/`reasoning` params and 1M-context behavior through PySubtrans' `CustomClient` request body (`PySubtrans/Providers/Clients/CustomClient.py` `_generate_request_body`), which currently only sets temperature/stream/max_tokens/max_completion/messages/model (no `thinking` field).
- DeepSeek is also usable via the OpenAI-compatible "Custom Server" provider without API key (`Provider_Custom.py`).

### OpenAI-compatible — CONFIRMED

Multiple routes, no plugin needed for common cases:

- `OpenAiProvider` (native, via openai SDK, optional extra) with custom `api_base` (`Provider_OpenAI.py`)
- `Custom Server` provider — any server with an OpenAI-compatible chat endpoint (e.g. LM Studio), via httpx directly, no SDK (`Provider_Custom.py:16-22`)
- `OpenRouterProvider` and `RequestyProvider` (OpenAI-compatible gateways), `LiteLLMProvider` (unified gateway) — all in `Providers/__init__.py:12-22`
- **eleven built-in providers** registered on import: Azure, Bedrock, Claude, Custom Server, DeepSeek, Gemini, LiteLLM, Mistral, OpenAI, OpenRouter, Requesty (`PySubtrans/Providers/__init__.py`)

### Custom provider hook — CONFIRMED

Pluggable by subclassing `TranslationProvider`, overriding `GetTranslationClient` and `ValidateSettings`, registering the name; `TranslationProvider.get_provider` resolves by case-insensitive name (`PySubtrans/TranslationProvider.py:113-157`).

### Config mechanism — CONFIRMED

All above is **code-driven + env vars** (`init_options(...)`, env `*_API_KEY`/`DEEPSEEK_MODEL` etc., `Options.py:48-101`). The upstream GUI also persists a JSON settings file and some of these options are consumed from it, but the pip **library** API is purely programmatic — no config file input for providers. There's also no **CLI** entry point in the pip package itself (no console scripts in `pyproject.toml`); CLIs live in the upstream repo's `scripts/` (`llm-subtrans.py`, `deepseek-subtrans.py`, …) and are not shipped in `pip install pysubtrans`.

---

## 3. State & resume

### `.subtrans` project file — CONFIRMED

- `SubtitleProject` writes a JSON project file named `<input>.subtrans` next to the source when `persistent=True` (`SubtitleProject.py:275-281`, `WriteProjectToFile`), and auto-saves progress after batches (project marked `needs_writing` on `batch_translated`/`scene_translated`, `SubtitleProject.py:552-560`; the project file is flushed in `TranslateSubtitles`' finally block, `SubtitleProject.py:446-457`). A `.subtrans-backup` is written on writes. (`SubtitleProject.py:347-354`)
- Serialization stores the full scene/batch hierarchy with per-line original *and* translated text, per-batch context and the accumulated terminology map — i.e., **line-level translated state persisted in the project file**, batched into logical scenes (`SubtitleSerialisation.py:42-97`).
- `init_project(settings, filepath, persistent=True)` reloads the existing `.subtrans`; `settings_precedence` (User|Project) controls whether saved settings or caller settings win (`__init__.py:300-394`). Reloaded projects skip re-preprocessing and re-batching so originals/translated stay in sync (`__init__.py:378-392`).

### Resume after interruption — CONFIRMED at scene/batch granularity

- On resume, `SubtitleTranslator` skips any scene where `scene.all_translated`, and re-requests only batches whose `translated` list is empty (`SubtitleTranslator.py:113-136`, 236). So resume re-invokes: **untranslated batches only; a batch where all lines already have a translation is reused.**
- Events: `batch_translated`, `batch_updated` (streaming), `scene_translated`, `preprocessed`, `terminology_updated`, plus blinker-based `info/warning/error` logging hooks (`PySubtrans/TranslationEvents.py:25-81`). Granularity is per-batch/per-scene, *not* per-line.

**needs-a-prototype:**

- Whether previously *partially-translated* batches (some lines streamed, then crash) actually resume — a partially-translated batch has a non-empty `translated` list, so by the current filter (`if not batch.translated` → only empty lists re-requested) such batches appear **excluded** from re-translation. This is the sharpest resume/robustness question to probe.
- Whether the `max_tokens`/context growth on resend (growth > new lines) causes surprising cost increases on large long-context files.

### Re-billing risk — CONFIRMED mechanism, needs proto for cost

On resume, completed lines/scenes are skipped (no re-bill), but a batch that was sent and failed validation — or a crash after the request was issued but before successful parse — is not recorded as translated and is re-sent (re-billed). `retry_on_error`/`autosplit_on_error` each trigger further requests.

---

## 4. Integration surface

### Library vs CLI — CONFIRMED: library on pip; CLIs live upstream

`pip install pysubtrans` installs a **Python library** only (no `console_scripts` entry points in `pyproject.toml`). CLI tools (`llm-subtrans.py`, `scripts/deepseek-subtrans.py`, `batch-translate.py`) are code in the upstream repo — `pip install pysubtrans` does not give you a CLI. You embed it.

### Dependency weight — CONFIRMED, light for a translation engine

`dependencies: python-dotenv, srt, pysubs2, regex, babel, appdirs, blinker, requests, setuptools, httpx, httpx[socks]`; **requires Python ≥3.10**; no numpy/torch. (`PySubtrans/pyproject.toml`)
Provider SDKs are all optional extras (`openai`, `azure`, `gemini`, `claude`, `mistral`, `bedrock`) (`pyproject.toml` extras; the DeepSeek + Custom Server + OpenRouter providers do not need the SDK). Core translation engines (DeepSeek, Custom Server) work with the base install.

### Consumes an already-extracted External subtitle — CONFIRMED

- Input is a *subtitle file path* or `content` string (`init_subtitles(filepath=...)`). Supported formats: **SRT, ASS, SSA, VTT** (`PySubtrans/README.md`; `Formats/` handlers: SrtFileHandler, SSAFileHandler, VttFileHandler).
- It **does not read containers** (no MKV/MP4/TS triage, no ffmpeg/libass). `SubtitleFormatRegistry` only knows subtitle text handlers, auto-detects by content/extension (`SubtitleFormatRegistry.py:15-100`).
- → In CueWeaver terms: PySubtrans operates on an **External subtitle** produced by `seconv` (or any extracted SRT/ASS/VTT). Extraction + Bitmap-subtitle OCR remain out of PySubtrans' scope; `seconv` would be responsible for delivering an extracted text track. `SaveTranslation()` writes `basename.language.ext` next to the source (`Helpers/__init__.py` `GetOutputPath`) — that output naming coincides with CueWeaver's "publish sidecar" (`Movie.zh.srt`), but atomicity/validity handling lives in CueWeaver, not PySubtrans.

---

## 5. Maintenance posture

From GitHub API + PyPI (checked 2026-08):

- **Single maintainer**: `machinewrapped` authored ~44 of the last 50 commits; occasional external PRs (e.g. Requesty #424, LiteLLM #419).
- **Release cadence strong historically, slowing**: 30 releases 2023, 28 in 2024, 21 in 2025, **3 in 2026** (dates: v1.5.7 2025-11-23 → v1.6.0 2026-04-26 → v1.6.1 2026-07-13). The pip package lags the monorepo: latest PyPI 1.6.0 (2026-04-26) vs GitHub v1.6.1 (2026-07-13).
- **Activity-healthy**: repo created 2023-03-23, last push 2026-07-22; ~82 tagged release total; 634 stars / 61 forks; **3 open issues** (very few), with the most recent closed issue fixed within the same release cycle.
- **Maturity signals**: extensive unit-test suite for PySubtrans (`tests/PySubtransTests/*`, incl. translator/project/provider/handler tests), Keep-a-Changelog + SemVer (`CHANGELOG.md`), MIT license (`PySubtrans/LICENSE`). Terminology map arrived only in v1.6.0 (2026-04-26) — it's a young feature.

**needs-a-prototype:** if adopting as a runtime dependency, verify upgrade velocity actually continues (2026 cadence slowed to ~quarterly).

---

## Summary for CueWeaver decision

- `pysubtrans` is a **library** (pip, Python ≥3.10), light deps (no torch/SDK needed for DeepSeek/Custom Server), MIT.
- It has genuinely scene-batched translation, configurable `max_batch_size`, per-batch Context injection (scene/summary/history/names + seeded or learned terminology), and capped (structural) Validation + `autosplit_on_error`/`retry_on_error` recovery. None of those five machinery claims is invented upstream.
- **DeepSeek V4 and OpenAI-compatible providers work out of the box** — but you must set `model=deepseek-v4-pro|deepseek-v4-flash` yourself (default `deepseek-chat` is a retiring alias), and V4 thinking-mode control needs proto verification.
- `.subtrans` project file + resume work at scene/batch granularity; the partially-translated-batch resume edge is the thing to probe, along with re-billing on retry/split.
- It consumes an **External subtitle file** (SRT/ASS/SSA/VTT) only; container extraction/OCR belongs to `seconv`, and atomic Publishing belongs to CueWeaver.
- **The integration boundary**: PySubtrans provides the translation engine; CueWeaver supplies Job/scene scheduling, Glossary-overrides-precedence + atomic Publishing + cost-semantics overlay, because PySubtrans' vocabulary (Glossary via terminology-map only, Validation is structural) differs from CueWeaver's product vocabulary.

Open items that only a CueWeaver-vs-PySubtrans integration prototype can settle are collected in the next section.

---

## What only the integration prototype can settle

1. **Resume robustness**: the partially-translated-batch resume behavior (whether `batch.translated` non-empty blocks re-translation of unfinished line), interruptions mid-stream, and cost of retries (re-billing of re-sent batches).
2. **Terminology-learning quality**: actual inter-batch consistency gain of `build_terminology_map` vs prompt-bloat, and how Auto Glossary could map onto it — vs CueWeaver Glossary that must always override.
3. **DeepSeek V4 specifics**: thinking mode/pro params, 1M-context behavior, temperature=1.3 default for translation quality, and responses-parse correctness through the CustomChat client.
4. **Dependency/runtime weight in a self-hosted deployment**: install size/build time with pip from wheels vs source, and whether ~all of it (babel/appdirs/blinker/httpx) is acceptable to vendor in CueWeaver's runtime.
