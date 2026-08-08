# PySubtrans incremental-value comparison — prototype findings

Prototype ticket: [#8 PySubtrans incremental-value comparison](https://github.com/kfstorm/cueweaver/issues/8) (part of [wayfinder map #1](https://github.com/kfstorm/cueweaver/issues/1)).

Date: 2026-08-08.

## Question

Measured against the seconv-only baseline (prototype #7), does PySubtrans bring enough incremental value — translation quality/consistency, cost, resume, validation, terminology — to justify becoming a runtime dependency in v0.1? Can it cleanly integrate as an engine over a seconv-extracted Source?

## Setup

- **Same Sample / Same Provider as the #7 baseline**: `jitc-e11.sample.srt`, the 60-sub held-out en→zh-CN slice extracted from `Jewel in the Crown` S01E11, translated on `deepseek-v4-flash` via the same DeepSeek API.
- **Runtime**: PySubtrans 1.6.0 (pip), Python 3.13 venv. Library API only (no CLI — matches research #3).
- **Same options as the baseline where comparable** (60s scene threshold / 30 max batch). Two configs exercised:
  - pipeline A: scene batching + rolling context + terminology-learning (`build_terminology_map=True`) — the full PySubtrans machinery.
  - pipeline B: same, but resume/terminology disabled where the experiment isolates a single axis.
- Every probe lives in this directory: `full_run.py` (end-to-end), `thinking_probe.py` (DeepSeek thinking on/off), `resume_probe.py` (never-re-bill check).

## Results

### 1. Translation: batching + context, 4 requests, format-faithful

| Metric | seconv-only (#7) | PySubtrans (this) |
| ------- | ---------------- | ----------------- |
| Extraction | 1m13s (Embedded→SRT) | n/a (consumes the seconv-extracted SRT) |
| Translation (60-sub slice) — as-is defaults | ~73s (thinking **off** by default) | 139.5s (thinking **on** by default) |
| Translation (same slice, thinking off both) | ~73s | **17.7s** |
| HTTP requests (60 subs) | ~handful (merge-bounded, unmeasured) | **4** (batches 12/20/13/15) |
| Translated subs | 60/60 (one line #24 left in English) | 60/60, none dropped |
| Timestamp/format fidelity | clean SRT round-trip | clean SRT round-trip (60 blocks / 60 timestamps) |

Two corrections to the #7 baseline before comparing:

1. **seconv does *not* post per line.** Its `MergeAndSplitHelper` merges contiguous lines up to `min(MaxCharacters=1500, AutoTranslateMaxBytes=2000)` chars per request (verified in `subtitleedit/src/libuilogic/Translate/MergeAndSplitHelper.cs:103`), so it also batches — the "1.2s/sub sequential per-line posts" claim in the #7 findings was not accurate, and the seconv-side request count is unmeasured, not "60".
2. **The seconv 73s number was already run on a thinking-disabled session** (seconv sets `thinking: disabled`), so the apples-to-apples comparison slashes PySubtrans from 139.5s default to **17.7s** with the same one-line patch. Net: PySubtrans is the *faster* engine at the same thinking setting.

What seconv's merge does not provide: rolling scene/batch summaries as prompt context (its context is a fixed prompt with no intra-file memory) — that's PySubtrans' real batching/context advantage, not raw latency.

### 2. The thinking-mode catch — PySubtrans leaves DeepSeek thinking ON

**seconv explicitly disables DeepSeek V4 thinking** (`"thinking": {"type": "disabled"}`, per research #2). PySubtrans **does not** — `CustomClient._generate_request_body` sets model/temperature/stream/messages only, no `thinking` field, and `DeepSeekClient` even flags `supports_reasoning=True`.

Measured on the **same 60-sub sample through PySubtrans's own full pipeline** — one run with the stock body (thinking on), one with a one-line patch adding `"thinking": {"type": "disabled"}` to `_generate_request_body` (seconv-style):

| Config | Translation time | output | format noise |
| ------ | ---------------- | ------ | ------------ |
| A: PySubtrans default (no `thinking`) | **139.5s** (~2.3 s/sub) | 60/60, clean | none |
| B: `thinking: {"type":"disabled"}` (seconv-style) | **17.7s** (~0.3 s/sub) | 60/60, clean | none |

→ Same input, same provider, same library code: leaving thinking ON costs **~8× wall time** in this end-to-end run (consistent with the 8.63s vs 3.44s seen on a single 12-line batch in `thinking_probe.py`, where the disabled arm consumed 422 output tokens vs 1010, of which 719 were reasoning). seconv explicitly disables it (`DeepSeekTranslate` sends `thinking: disabled`); PySubtrans does not — `CustomClient._generate_request_body` sets model/temperature/stream/messages only, and `DeepSeekClient` even flags `supports_reasoning=True`. That default is the opposite of the standing "fast/cheap" principle. The quality trade-off: on the held-out slice both arms translated 60/60 with clean output and an identical 18-term terminology map, so within this sample the disabled arm did **not** cost visible quality — but a rubric-grade comparison is out of scope here and the trade should be re-measured on #10's quality sample.

Probe-noise note: an earlier hand-built prompt in `thinking_probe.py` produced `Original>/Translation>` scaffold noise in the no-thinking arm; a full-library run (identical code except the `thinking` patch) does **not**, so that noise was a probe artifact, not a PySubtrans behavior. The thinking on/off cost ratio holds in both harnesses.

### 3. Resume = never-re-bill: confirmed at batch/scene granularity

`resume_probe.py`: run batch 1 (1 HTTP post), abort; reload the `.subtrans` project with `resume=True`, continue → **3 more posts, total 4**. That equals a clean uninterrupted run of 4 batches: **completed work is never re-sent or re-billed** for clean run boundaries. Caveats:

- `init_translator()` does **not** expose the `resume` flag (hardcoded `False`); resume only engages through a direct `SubtitleTranslator(..., resume=True)` construction. An integrator must wire that, or the resume is silently a full re-run (the difference was literally 4 posts vs 5 in the probe).
- The scratch prototype partial-batch case stayed unanswered: the probe aborts between HTTP posts, never mid-stream; the "partially-translated batch" edge from research #3 still needs the interrupted-stream variant.

### 4. Validation — structural, batch-level, not semantic quality

`SubtitleValidator` performs mechanical checks only (numbered lines present, non-empty text, line-count, ≤120 chars, ≤2 newlines). It does not judge quality/hallucination. That matches the map Notes on Validation: automatic checks, not a human-level gate.

### 5. Terminology learning — works well at small scale

Pipeline A's `terminology_map` accumulated 18 terms with correct provenance (counts verified in the `.subtrans` project file): proper-noun style entries only (My Lord→大人, Lady Jang→张夫人, Dongyi→东伊, pinellia→半夏 …), no cast/crew spam, no hallutions. That's the raw material #11 needs for a CueWeaver Glossary, and it interleaves per-batch. The `terminology_updated` event hands a snapshot to a caller for persistence — a clean seam for User override precedence.

## Integration cleanliness

- **Library-only, light deps** (dotenv, srt, pysubs2, regex, babel, appdirs, blinker, requests, httpx); Python ≥3.10; DeepSeek provider + Custom Server work with the base install — consistent with research #3: a self-hostable runtime dependency that proves its value (batching/context/resume/terminology exist for real).
- **Consumes only an already-extracted External subtitle** (SRT/ASS/VTT). Extraction/OCR/container stay with seconv — the seam is clean: `seconv extract` → (SRT) → PySubtrans engine → CueWeaver owns publish/validity/atomicity. Matches research #3.
- The library does **not** own cost accounting, atomicity, or the never-re-bill *guarantee* — resume works at its granularity but CueWeaver still has to drive the job state machine + user permission on partial Publishing (per ticket #6). So "never-re-bill" is delegated to engine checkpoint/resume exactly as #6 specified, with CueWeaver as policy owner.

## Verdict (evidence, not the #9 decision)

- PySubtrans **proves** its incremental value as the engine: real scene-batched translation, running-context history, working automatic terminology, and a tested never-re-bill resume path — with light deps and a clean one-way seam over seconv-extracted SRT.
- Two things #9 must carry into the engine decision:
  1. **thinking mode is ON by default** — a one-line patch (`thinking: disabled` in the request body) drops end-to-end translation time from 139.5s to 17.7s on the same 60-sub sample (~8×) and avoids ~2.4× the output tokens. Must be applied or the current default is uncompetitively slow/costly.
  2. resume requires wiring `resume=True` explicitly; the convenience API (`init_translator`) silently no-ops it.
- Quality-on-terminology was visibly better than the seconv baseline on the same lines (consistent 大人/陛下/张夫人 via the 18-term map vs seconv's drifting 我的大人/先生/张司钥/张夫人), which is the strongest pro-PySubtrans pillar — and it held under `thinking: disabled`, so it does **not** require buying the thinking-arm cost.

## Assets

- `full_run.py` — end-to-end translate + save (terminology learning on). `full_run_nothinking.py` — same, with a one-line `thinking: disabled` patch.
- `thinking_probe.py` — single-batch with/without `thinking`, latency+usage dump (prompt-assembly harness; the noise there is a probe artifact, see §2).
- `resume_probe.py` — abort-and-resume, HTTP-post counting.
- `sample/jitc-e11.sample.srt` — the exact input; `sample/pysubtrans-thinkingon-output.srt` — default run output; `sample/pysubtrans-thinkingoff-output.srt` — thinking-disabled run output; `sample/terminology-map.json` — the 18-term map as persisted after the run.

Full raw logs (run1.log, thinking_probe.log) preserved in the probe workdir for reference.
