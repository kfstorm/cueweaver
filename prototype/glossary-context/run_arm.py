#!/usr/bin/env python
"""Prototype #10: automatic Context/Glossary quality-benefit.

Three arms on the same 60-sub sample, same provider & model as the #8 engine
prototype, all with `thinking: disabled` (the #8 seam):

  A (baseline)  : plain translate prompt; no Context, no auto-terminology
  B (+Context)  : A + narrative Context block (built from TMDB metadata)
  C (+Context+G): B + PySubtrans dynamically self-learned terminology —
                  the engine's own auto-Glossary, learned and re-injected
                  batch-by-batch (build_terminology_map=True).

Throwaway code. ARM env selects the arm. NO #8 static terminology snapshot is
used anywhere (that would leak the previous experiment's learned output).
"""

import copy
import os
import sys
import time

from PySubtrans import init_options, init_project, init_translator
from PySubtrans.Helpers.Text import IsTextContentEqual


def _patch_parser_matching() -> None:
    """Fix PySubtrans 1.6.0's dead fuzzy-match fallback in MatchTranslations.

    The stock MatchTranslations raises UntranslatedLinesError for any line the
    model did not number correctly, and the intended TryFuzzyMatches fallback
    never moves matches back into the translated pool (`#unmatched.remove(item)`
    is commented out at TranslationParser.py:178). So a numbering hiccup (the
    model echoing the system prompt's example `#200` instead of the real line
    number) silently drops whole batches/lines from the output. This patch
    makes the documented behavior happen in the main loop: after the numbered
    lookup fails, match by original text and keep the found translation.
    Applied to all arms alike so A/B/C stay comparable. See workdir/diagnosis.md.
    """
    from datetime import timedelta
    from PySubtrans.SubtitleError import UntranslatedLinesError
    from PySubtrans.TranslationParser import TranslationParser as _TP

    def match_translations(self, originals):
        if not originals:
            raise ValueError("Original subtitles not provided")

        matched = []
        unmatched = []

        available = list(self.translations.values())

        for item in originals:
            translation = self.translations.get(item.key)
            if translation:
                if translation in available:
                    available.remove(translation)
                translation.number = item.number
                translation.start = item.start or timedelta(seconds=0)
                translation.end = item.end or timedelta(seconds=0)
                translation.metadata = item.metadata

                if translation.original and IsTextContentEqual(
                    translation.text, item.text
                ):
                    translation.text, translation.original = (
                        translation.original,
                        translation.text,
                    )

                item.translation = translation.text
                matched.append(translation)
                continue

            # Numbered lookup failed — try matching on the original text, which
            # is what the dead TryFuzzyMatches was supposed to do.
            found = None
            for candidate in available:
                if candidate.original and IsTextContentEqual(
                    candidate.original, item.text
                ):
                    found = candidate
                    break
                if candidate.text and IsTextContentEqual(candidate.text, item.text):
                    found = candidate
                    if candidate.original:
                        candidate.text, candidate.original = (
                            candidate.original,
                            candidate.text,
                        )
                    break

            if found:
                self.warnings.append(
                    f"Found fuzzy match for line {item.number} in translations"
                )
                found.number = item.number
                found.start = item.start or timedelta(seconds=0)
                found.end = item.end or timedelta(seconds=0)
                found.metadata = item.metadata
                if found.original and IsTextContentEqual(found.text, item.text):
                    found.text, found.original = (
                        found.original,
                        found.text,
                    )
                item.translation = found.text
                matched.append(found)
                available.remove(found)
                continue

            item.translation = None
            unmatched.append(item)

        if unmatched:
            self.errors.append(
                UntranslatedLinesError(
                    f"No translation found for {len(unmatched)} lines",
                    lines=list(unmatched),
                )
            )

        return matched, unmatched

    _TP.MatchTranslations = match_translations


SAMPLE = os.environ.get(
    "SAMPLE",
    "/tmp/cw-glossary/prototype/glossary-context/sample/jitc-e11.sample.srt",
)
WORKDIR = os.path.dirname(os.path.abspath(__file__))

BASE_PROMPT = "Translate these subtitles to Simplified Chinese (zh-CN)"


def load_context() -> str:
    with open(os.path.join(WORKDIR, "workdir", "context.txt"), encoding="utf-8") as fh:
        return fh.read().strip()


def build_prompt() -> str:
    return BASE_PROMPT


def main() -> int:
    arm = os.environ.get("ARM", "A")
    key = open("/tmp/deepseek_api_key.txt").read().strip()

    _patch_parser_matching()

    # Isolate the arm's persistent project: copy the sample into a per-arm
    # dir so the .subtrans project file / persisted terminology never leaks
    # between arms (the stale sample/.subtrans from earlier runs is excluded).
    import shutil

    arm_dir = os.path.join(WORKDIR, f"run.{arm}")
    os.makedirs(arm_dir, exist_ok=True)
    sample_path = os.path.join(arm_dir, "jitc-e11.sample.srt")
    shutil.copy(SAMPLE, sample_path)
    for stray in ("jitc-e11.sample.subtrans", "jitc-e11.sample.translated.srt"):
        sp = os.path.join(arm_dir, stray)
        if os.path.exists(sp):
            os.remove(sp)

    out = os.environ.get(
        "OUT",
        os.path.join(WORKDIR, f"jitc-e11.sample.zh.arm{arm}.srt"),
    )
    dyn_terminology = arm == "C"
    use_context = arm in ("B", "C")

    opts = init_options(
        provider="DeepSeek",
        api_key=key,
        model="deepseek-v4-flash",
        target_language="Simplified Chinese (zh-CN)",
        prompt=build_prompt(),
        description=load_context() if use_context else None,
        scene_threshold=60.0,
        min_batch_size=10,
        max_batch_size=30,
        preprocess_subtitles=False,
        build_terminology_map=dyn_terminology,
        max_context_summaries=10,
    )
    project = init_project(opts, filepath=sample_path, persistent=True)
    translator = init_translator(
        opts,
        terminology_map=getattr(project.subtitles, "terminology_map", None) or {},
    )

    orig = translator.client._generate_request_body
    events_seen = []

    def no_thinking(request, temperature=None):
        body = orig(request, temperature)
        body["thinking"] = {"type": "disabled"}
        return body

    translator.client._generate_request_body = no_thinking

    def on_event(name):
        def handler(tr, **kw):
            batch = kw.get("batch")
            scene = kw.get("scene")
            lines = kw.get("lines_num") or getattr(batch, "linecount", None)
            update = kw.get("update")
            if update is not None:
                tmap = update.terminology_map
                events_seen.append(tmap)
                print(
                    f"  [event {name}] scene={update.scene} batch={update.batch} "
                    f"new={list(update.new_terms.items())} "
                    f"conflicts={update.conflict_terms}",
                    flush=True,
                )
            else:
                print(
                    f"  [event {name}] scene={getattr(scene, 'number', None)} "
                    f"batch={getattr(batch, 'number', None)} lines={lines}",
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

    print(f"\n=== arm {arm} elapsed: {elapsed:.1f}s ===")
    print(
        f"lines: {project.subtitles.linecount}, scenes: {project.subtitles.scenecount}"
    )

    project.SaveProject()
    project.subtitles.SaveTranslation(out)

    # Final learned terminology map snapshot (arm C)
    final = dict(getattr(project.subtitles, "terminology_map", None) or {})
    if final:
        import json

        with open(
            os.path.join(WORKDIR, f"workdir/learned-terminology-arm.{arm}.json"), "w"
        ) as fh:
            json.dump(events_seen or final, fh, ensure_ascii=False, indent=2)
        print(f"learned terminology events: {len(events_seen)}, snapshots -> workdir")

    print(f"saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
