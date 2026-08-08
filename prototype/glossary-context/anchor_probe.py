#!/usr/bin/env python
"""Controlled probe for issue #10: does the TMDB context block and/or the
terminology system block make the model echo the system prompt's example
numbering (#200...) instead of the real batch line numbers (1..12)?

Same 12-line batch, n trials each, four configs:
  base       : no context, no terminology system block   (like arm A's batch 1)
  ctx        : TMDB context block prepended              (like arm B/C)
  terms      : no context, terminology system block      (like arm C w/o B)
  ctx+terms  : context + terminology system block        (like arm C)

Reports per-config count of responses starting at #200 (example echo) vs #1.
No .subtrans project is created; requests go straight through the client.
Throwaway.
"""

import os
import re
import time

from PySubtrans import init_options, init_translator
from PySubtrans.SubtitleLine import SubtitleLine

SAMPLE = "/tmp/cw-glossary/prototype/glossary-context/sample/jitc-e11.sample.srt"
CONTEXT_F = "/tmp/cw-glossary/prototype/glossary-context/workdir/context.txt"
TRIALS = int(os.environ.get("TRIALS", "4"))


def parse_first_batch(n: int = 12) -> list[SubtitleLine]:
    import srt

    subs = list(srt.parse(open(SAMPLE, encoding="utf-8").read()))[:n]
    return [SubtitleLine.Construct(s.index, s.start, s.end, s.content) for s in subs]


def load_context() -> str:
    with open(CONTEXT_F, encoding="utf-8") as fh:
        return fh.read().strip()


def line_number_prefixes(text: str) -> list[str]:
    return re.findall(r"^#(\d+)", text, re.M)


def main() -> int:
    lines = parse_first_batch()
    context = load_context()
    key = open("/tmp/deepseek_api_key.txt").read().strip()
    base_prompt = "Translate these subtitles to Simplified Chinese (zh-CN)"

    configs = {
        "base": dict(
            prompt=base_prompt,
            build_terminology=False,
        ),
        "ctx": dict(
            prompt=base_prompt
            + "\n\n### 剧情背景（Context，来自 TMDB 元数据）\n"
            + context,
            build_terminology=False,
        ),
        "terms": dict(
            prompt=base_prompt,
            build_terminology=True,
        ),
        "ctx+terms": dict(
            prompt=base_prompt
            + "\n\n### 剧情背景（Context，来自 TMDB 元数据）\n"
            + context,
            build_terminology=True,
        ),
    }

    for cfg, params in configs.items():
        opts = init_options(
            provider="DeepSeek",
            api_key=key,
            model="deepseek-v4-flash",
            target_language="Simplified Chinese (zh-CN)",
            prompt=params["prompt"],
            scene_threshold=60.0,
            min_batch_size=10,
            max_batch_size=30,
            preprocess_subtitles=False,
            build_terminology_map=params["build_terminology"],
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
        for trial in range(TRIALS):
            prompt = translator.client.BuildTranslationPrompt(
                params["prompt"],
                translator.system_instructions,
                lines,
                {"scene": "Scene 1", "batch": "Scene 1 batch 1"},
            )
            translation = translator.client.RequestTranslation(prompt)
            if translation is None or not translation.text:
                details.append("no-response")
                continue
            nums = line_number_prefixes(translation.text)
            if not nums:
                details.append("no-numbers")
                continue
            if nums[0] == "200":
                echo += 1
                details.append("echo")
            elif nums == [str(i) for i in range(1, 13)]:
                correct += 1
                details.append("correct")
            else:
                other += 1
                details.append("other(" + ",".join(nums[:4]) + ")")
        print(
            f"=== {cfg:10s}: echo={echo}/{TRIALS} correct={correct}/{TRIALS} "
            f"other={other} details={details} ===",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
