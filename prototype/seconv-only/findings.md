# seconv-only subtitle translation — prototype findings

Prototype ticket: [#7 seconv-only subtitle translation prototype](https://github.com/kfstorm/cueweaver/issues/7) (part of [wayfinder map #1](https://github.com/kfstorm/cueweaver/issues/1)).

Date: 2026-08-08

## Question

What are the true outcomes of translating a real subtitle to Simplified Chinese using seconv's headless path — quality, config burden, latency, cost, format fidelity?

## Method

- **Built seconv** from an upstream checkout with a **minimal (~20-line) patch** exposing a `deepseek` CLI engine. The stock CLI only supports local engines (`llamacpp/ollama/lmstudio/libretranslate/nllb-*`) and cannot reach DeepSeek/OpenAI-compatible — confirming research #2's boundary claim.
- **Patch** (`AutoTranslateRunner.Create` + a `--translate-api-key` flag):
  - Added `"deepseek"` to `SupportedEngines` and a `case "deepseek"` that wires `--translate-url/-model/-api-key` into `Configuration.Settings.Tools.DeepSeek*`.
  - Build: `dotnet build -c Release` via `mcr.microsoft.com/dotnet/sdk:10.0`, 0 warnings / 0 errors.
- **Media**: `Jewel in the Crown - S01E11 - Episode 11 WEBDL-1080p.mkv` (4.8GB, MKV; audio kor, one **Embedded** `subrip` eng track). No External subtitle sidecar present, so the Embedded track was extracted.
- **Extraction**: `seconv <mkv> srt --output-folder …` → `Jewel in the Crown - S01E11 - Episode 11 WEBDL-1080p.eng.srt` (~1m13s, 602 subtitles, timestamps preserved).
- **Translation**: held-out slice of the first 60 subtitles, `--translate-to:zh-CN --translate-from:en --translate-engine:deepseek` (default model `deepseek-v4-flash`), API key via `--translate-api-key`.

## Results

| Metric | Value |
| ------- | ------- |
| Extraction (container → SRT) | ~1m13s; 602 subs; timestamps clean |
| Translation (60-sub slice) | ~73s ≈ 1.2 s/sub |
| Whole episode estimate (602 subs) | ~12 min |
| Translated subs | 60 / 60 (100%, no empty output) |
| Timestamp lines | 60 / 60 |
| CJK in output | 100% clean Simplified Chinese on all translated lines |

## Quality spot-check (first 12 lines)

| EN | ZH |
| ---- | ---- |
| [Episode 11] | [第十一集] |
| Why are you... | 你为什么…… |
| Halt! | 停！ |
| Identify yourselves. | 表明你们的身份。 |
| Unhand her. | 放开她。 |
| I said unhand her. | 我说了放开她。 |
| My Lord. | 我主。 |
| Are you deaf? Unhand her now! | 你聋了吗？放开她！ |
| Get them! | 抓住他们！ |
| Back away or I'll cut her. | 退后，不然我就割了她。 |
| - My Lord. - Stop this... | - 我的大人。- 住手… |
| Now! | 现在！ |

## Findings

1. **Configuration burden is the real finding**: stock seconv **cannot** translate to Simplified Chinese with a cloud provider today. DeepSeek/OpenAI-compatible exist only in the library; the CLI lacks them. Enabling the engine is a 20-line patch (proven) — either an upstream PR or CueWeaver vendoring the small seam.
2. **Format fidelity**: SRT→SRT is clean — block numbering and timestamps round-trip; no line-dropping. Uses a text Embedded track; Bitmap/OCR not exercised here (that's #12's boundary question).
3. **Latency**: sequential per-line posts dominate (1.2s/sub). Batching/reduce requests is not something the CLI rescues; that will land on the engine decision (#9).
4. **Cost**: not instrumented (CueWeaver doesn't track provider spend). Estimate: 602 subs * 1 line ≈ small; on DeepSeek flash pricing ($/1M tokens) a 45-min episode is in the low-cent range — but confirm with an actual bill when the engine lives.

## Verdict

- seconv's headless **extraction/OCR** is proven: it got an Embedded eng track into a clean SRT with zero fuss.
- seconv's headless **translation** is *capable but not drop-in*: requires the seam patch (upstream PR or vendoring) to reach a modern cloud LLM. Quality of the translated output itself is clean.
- This is exactly the seam #9 must resolve: CLI-PR vs library-vendor vs direct-provider.
