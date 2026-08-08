#!/usr/bin/env python
"""Prototype: PySubtrans resume behaviour (never-re-bill check).

Phase 1: translate, abort right after batch 1 finishes -> count posts.
Phase 2: reload .subtrans project, resume -> count posts, verify only batches 2-4 re-sent.
"""

import os
import sys
import time
import shutil

from PySubtrans import (
    init_options,
    init_project,
    init_translator,
    init_translation_provider,
)
from PySubtrans.SubtitleTranslator import SubtitleTranslator

SAMPLE = "/tmp/opencode/pysubtrans-proto/workdir/jitc-e11.sample.srt"
WORKDIR = "/tmp/opencode/pysubtrans-proto/workdir/resume"


def make_opts(key):
    return init_options(
        provider="DeepSeek",
        api_key=key,
        model="deepseek-v4-flash",
        target_language="Simplified Chinese (zh-CN)",
        prompt="Translate these subtitles to Simplified Chinese (zh-CN)",
        scene_threshold=60.0,
        min_batch_size=10,
        max_batch_size=30,
        preprocess_subtitles=False,
        build_terminology_map=False,
    )


def instrument(translator):
    counter = {"posts": 0}
    client = translator.client
    orig = client._make_request

    def counted(request, temperature=None):
        counter["posts"] += 1
        print(f"    [HTTP post #{counter['posts']}]", flush=True)
        return orig(request, temperature)

    client._make_request = counted
    return counter


def main() -> int:
    key = os.environ["DEEPSEEK_API_KEY"]
    if os.path.exists(WORKDIR):
        shutil.rmtree(WORKDIR)
    os.makedirs(WORKDIR)
    src = os.path.join(WORKDIR, "jitc-e11.sample.srt")
    shutil.copy(SAMPLE, src)

    print("== PHASE 1: translate, abort after batch 1 ==", flush=True)
    opts = make_opts(key)
    project = init_project(opts, filepath=src, persistent=True)
    translator = init_translator(opts)
    counter1 = instrument(translator)

    aborted = {"done": False}

    def after_batch(tr, **kw):
        if not aborted["done"]:
            aborted["done"] = True
            print("  -> aborting after batch 1", flush=True)
            tr.StopTranslating()

    translator.events.batch_translated.connect(after_batch, weak=False)
    project.TranslateSubtitles(translator)
    project.SaveProject()
    print(f"  phase 1 posts: {counter1['posts']}", flush=True)

    print("\n== PHASE 2: reload project, resume ==", flush=True)
    opts2 = make_opts(key)
    project2 = init_project(
        opts2,
        filepath=src,
        persistent=True,
        settings_precedence=__import__("PySubtrans").SettingsPrecedence.User,
    )
    sub_title = project2.subtitles
    translator2 = SubtitleTranslator(
        opts2,
        init_translation_provider("DeepSeek", opts2),
        resume=True,
        terminology_map=sub_title.terminology_map,
    )
    counter2 = instrument(translator2)
    print(
        f"  reloaded scenes: {project2.subtitles.scenecount}, existing_project={project2.existing_project}",
        flush=True,
    )
    project2.TranslateSubtitles(translator2)
    project2.SaveProject()
    print(f"  phase 2 posts: {counter2['posts']}", flush=True)
    print(
        f"\nTOTAL posts across both phases: {counter1['posts'] + counter2['posts']} (expect 4 if no re-bill)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
