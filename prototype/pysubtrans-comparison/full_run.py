#!/usr/bin/env python
"""Prototype: PySubtrans full-pipeline translation of the same 60-sub sample.

Throwaway code for issue #8 (PySubtrans incremental-value comparison).
Same Source + same provider as the seconv-only prototype (#7).
"""

import sys
import time
import os

from PySubtrans import init_options, init_project, init_translator

SAMPLE = "/tmp/opencode/pysubtrans-proto/workdir/jitc-e11.sample.srt"


def main() -> int:
    key = os.environ["DEEPSEEK_API_KEY"]
    out = os.environ.get(
        "OUT",
        "/tmp/opencode/pysubtrans-proto/workdir/jitc-e11.sample.zh.pysubtrans.srt",
    )
    opts = init_options(
        provider="DeepSeek",
        api_key=key,
        model="deepseek-v4-flash",
        target_language="Simplified Chinese (zh-CN)",
        prompt="Translate these subtitles to Simplified Chinese (zh-CN)",
        scene_threshold=60.0,
        min_batch_size=10,
        max_batch_size=30,
        preprocess_subtitles=False,
        build_terminology_map=True,
        max_context_summaries=10,
    )

    project = init_project(opts, filepath=SAMPLE, persistent=True)
    translator = init_translator(
        opts, terminology_map=getattr(project.subtitles, "terminology_map", None) or {}
    )

    def on_event(name):
        def handler(tr, **kw):
            batch = kw.get("batch")
            scene = kw.get("scene")
            lines = kw.get("lines_num") or getattr(batch, "linecount", None)
            print(
                f"  [event {name}] scene={getattr(scene, 'number', None)} batch={getattr(batch, 'number', None)} lines={lines}",
                flush=True,
            )

        return handler

    for ev in (
        "batch_translated",
        "batch_updated",
        "scene_translated",
        "terminology_updated",
        "preprocessed",
    ):
        getattr(translator.events, ev).connect(on_event(ev), weak=False)

    start = time.monotonic()
    project.TranslateSubtitles(translator)
    elapsed = time.monotonic() - start

    print(f"\n=== elapsed: {elapsed:.1f}s ({elapsed / 60.0:.2f} min) ===")
    print(
        f"lines: {project.subtitles.linecount}, scenes: {project.subtitles.scenecount}"
    )

    project.SaveProject()
    project.subtitles.SaveTranslation(out)
    print(f"saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
