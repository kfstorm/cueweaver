#!/usr/bin/env python
"""Probe v2: does marking the TMDB context as "background, not subtitles"
stop the model from echoing the example numbering (#200...)?

All four configs keep build_terminology_map=True + TMDB context (the arm-C
combination that echoed ~7/8). They vary only how the context is framed:
  orig   : context pasted raw after "Translate these subtitles"  (current)
  decl   : a one-line disclaimer says the context is background info only
  wrap   : context wrapped in <background>...</background>
  both   : disclaimer + wrap

If decl/wrap suppress the echo, the user's hypothesis holds: the LLM treats
the unmarked Chinese block as part of the subtitles to translate.
Throwaway.
"""

import os
import re

from PySubtrans import init_options, init_translator
from PySubtrans.SubtitleLine import SubtitleLine

SAMPLE = "/tmp/cw-glossary/prototype/glossary-context/sample/jitc-e11.sample.srt"
CONTEXT_F = "/tmp/cw-glossary/prototype/glossary-context/workdir/context.txt"
TRIALS = int(os.environ.get("TRIALS", "6"))

DISCLAIMER = (
    "（以下内容为剧集背景信息，供翻译参考；它不是需要翻译的字幕行，请勿将其"
    "与字幕混同，也不要翻译或罗列它的任何内容。）"
)


def parse_first_batch(n: int = 12) -> list[SubtitleLine]:
    import srt

    subs = list(srt.parse(open(SAMPLE, encoding="utf-8").read()))[:n]
    return [SubtitleLine.Construct(s.index, s.start, s.end, s.content) for s in subs]


def load_context() -> str:
    with open(CONTEXT_F, encoding="utf-8") as fh:
        return fh.read().strip()


def cnt(text: str) -> tuple[str, str]:
    nums = re.findall(r"^#(\d+)", text, re.M)
    if not nums:
        return "none", text[:60]
    return nums[0], ",".join(nums[:4])


def main() -> int:
    lines = parse_first_batch()
    context = load_context()
    key = open("/tmp/deepseek_api_key.txt").read().strip()
    base = "Translate these subtitles to Simplified Chinese (zh-CN)"

    def ctx_label(kind: str) -> str:
        return {
            "orig": context,
            "decl": DISCLAIMER + "\n" + context,
            "wrap": "<background>\n" + context + "\n</background>",
            "both": DISCLAIMER + "\n<background>\n" + context + "\n</background>",
        }[kind]

    configs = {
        "orig": ctx_label("orig"),
        "decl": ctx_label("decl"),
        "wrap": ctx_label("wrap"),
        "both": ctx_label("both"),
    }

    for cfg, ctx_block in configs.items():
        prompt_with_ctx = (
            base + "\n\n### 剧情背景（Context，来自 TMDB 元数据）\n" + ctx_block
        )
        opts = init_options(
            provider="DeepSeek",
            api_key=key,
            model="deepseek-v4-flash",
            target_language="Simplified Chinese (zh-CN)",
            prompt=prompt_with_ctx,
            scene_threshold=60.0,
            min_batch_size=10,
            max_batch_size=30,
            preprocess_subtitles=False,
            build_terminology_map=True,
            max_context_summaries=10,
        )
        translator = init_translator(opts, terminology_map={})
        orig = translator.client._generate_request_body

        def no_thinking(request, temperature=None):
            body = orig(request, temperature)
            body["thinking"] = {"type": "disabled"}
            return body

        translator.client._generate_request_body = no_thinking

        echo = 0
        correct = 0
        other = 0
        details = []
        for _ in range(TRIALS):
            prompt = translator.client.BuildTranslationPrompt(
                prompt_with_ctx,
                translator.system_instructions,
                parse_first_batch(),
                {"scene": "Scene 1", "batch": "Scene 1 batch 1"},
            )
            translation = translator.client.RequestTranslation(prompt)
            if translation is None or not translation.text:
                details.append("no-response")
                continue
            first, sample = cnt(translation.text)
            if first == "200":
                echo += 1
                details.append("echo")
            elif first == "1":
                correct += 1
                details.append("correct")
            else:
                other += 1
                details.append(f"other:{first}")
        print(
            f"=== {cfg:10s}: echo={echo}/{TRIALS} correct={correct}/{TRIALS} "
            f"other={other} details={details} ===",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
