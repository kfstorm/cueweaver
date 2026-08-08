#!/usr/bin/env python
"""Probe v3: does PySubtrans' NATIVE context-injection slot (`description`
option -> <context><description>...</description></context>) avoid the
#200-example-numbering echo that hand-splicing the TMDB block into the user
prompt triggers?

All configs use build_terminology_map=True (the arm-C combination):
  manual : TMDB block hand-spliced into the prompt string (current run_arm.py)
  native : prompt stays plain; TMDB block passed via init_options(description=)
  native+names : description AND names (ParseNames style) populated

Expect: incumbent echoes (#200) at high rate (as probed); native renders the
block inside <context> before the instruction and should echo ~0/6.
Throwaway.
"""

import os
import re

from PySubtrans import init_options, init_translator
from PySubtrans.SubtitleLine import SubtitleLine

SAMPLE = "/tmp/cw-glossary/prototype/glossary-context/sample/jitc-e11.sample.srt"
CONTEXT_F = "/tmp/cw-glossary/prototype/glossary-context/workdir/context.txt"
TRIALS = int(os.environ.get("TRIALS", "6"))


def parse_first_batch(n: int = 12) -> list[SubtitleLine]:
    import srt

    subs = list(srt.parse(open(SAMPLE, encoding="utf-8").read()))[:n]
    return [SubtitleLine.Construct(s.index, s.start, s.end, s.content) for s in subs]


def load_context() -> str:
    with open(CONTEXT_F, encoding="utf-8") as fh:
        return fh.read().strip()


def cnt(text: str) -> str:
    import re

    nums = re.findall(r"^#(\d+)", text, re.M)
    if not nums:
        return "none"
    return nums[0]


def main() -> int:
    lines = parse_first_batch()
    context = load_context()
    key = open("/tmp/deepseek_api_key.txt").read().strip()
    base_prompt = "Translate these subtitles to Simplified Chinese (zh-CN)"

    configs = {
        "manual": dict(
            prompt=base_prompt
            + "\n\n### 剧情背景（Context，来自 TMDB 元数据）\n"
            + context,
            desc=None,
        ),
        "native": dict(
            prompt=base_prompt,
            desc=context,
        ),
        "native+notice-desc": dict(
            prompt=base_prompt,
            desc="以下为剧集背景信息，仅供翻译参考，不是需要翻译的字幕行。\n" + context,
        ),
    }

    for cfg, params in configs.items():
        kw = dict(
            provider="DeepSeek",
            api_key=key,
            model="deepseek-v4-flash",
            target_language="Simplified Chinese (zh-CN)",
            prompt=params["prompt"],
            scene_threshold=60.0,
            min_batch_size=10,
            max_batch_size=30,
            preprocess_subtitles=False,
            build_terminology_map=True,
            max_context_summaries=10,
        )
        if params["desc"] is not None:
            kw["description"] = params["desc"]
        opts = init_options(**kw)
        translator = init_translator(opts, terminology_map={})
        orig = translator.client._generate_request_body

        def no_thinking(request, temperature=None):
            body = orig(request, temperature)
            body["thinking"] = {"type": "disabled"}
            return body

        translator.client._generate_request_body = no_thinking

        echo = 0
        correct = 0
        other = []
        for trial in range(TRIALS):
            prompt = translator.client.BuildTranslationPrompt(
                params["prompt"],
                translator.system_instructions,
                parse_first_batch(),
                {
                    "description": params["desc"],
                    "scene": "Scene 1",
                    "batch": "Scene 1 batch 1",
                },
            )
            translation = translator.client.RequestTranslation(prompt)
            if translation is None or not translation.text:
                other.append("no-response")
                continue
            first = cnt(translation.text)
            if first == "200":
                echo += 1
            elif first == "1":
                correct += 1
            else:
                other.append(first)
        print(
            f"=== {cfg:10s}: echo={echo}/{TRIALS} correct={correct}/{TRIALS} other={other} ===",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
