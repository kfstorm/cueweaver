# Prototype #10 — Auto-Glossary quality-benefit: reproduced defect & fix

Ticket: [#10 Automatic Glossary quality-benefit prototype](https://github.com/kfstorm/cueweaver/issues/10)
(wayfinder map #1). Worktree branch `prototype/glossary-context`.

Date: 2026-08-08.

## Symptom

Arm C (TMDB Context + `build_terminology_map=True`, the dynamic self-learning
arm) occasionally drops the whole first batch: its output SRT has **48 blocks
instead of 60**, and `WARNING:root:Failed to translate 2 lines` /
`UntranslatedLinesError: No translation found for 12 lines` accompany the run.
Arms A and B (no auto-terminology) always keep 60/60.

## Root cause (reproduced 3× in clean runs + 2 probe series)

Two independent defects stack:

1. **The unmarked TMDB Context block is mistaken for in-band subtitles.**
   The system prompt instructs the model to translate "the subtitles" and
   shows a numbered example. Arm C's user message places a long bilingual
   (mostly Chinese) TMDB synopsis immediately after "Translate these
   subtitles..." and before the `#1..#12` numbered lines. Unmarked, the model
   treats that Chinese block as subtitles to translate and answers by echoing
   the format example's numbering — `#200..#212` instead of `#1..#12`.

   Controlled probe (12-line batch, n=4 then n=8 then n=6, same provider/model):

   | config                                | echo #200 (mirror) | correct #1..#12 |
   |---------------------------------------|--------------------|-----------------|
   | base   (no context, no terminology)   | 0/8                | 8/8             |
   | ctx    (context block only)           | 0/8                | 8/8             |
   | terms  (terminology system block)     | 0/8                | 8/8             |
   | ctx+terms (arm C combination)         | **7/8, then 6/6**  | 1/8, 0/6        |
   | ctx+terms + one-line "not subtitles" notice | **0/6**      | 6/6             |
   | ctx+terms + `<background>` wrapper    | **0/6**           | 6/6             |

   → The echo only fires when the TMDB Context and the terminology system
   block are present together, and it disappears as soon as the context is
   explicitly framed as background/non-subtitle content.

2. **PySubtrans has no recovery from number mismatches.** The `#200` reply
   is parsed fine by `TranslationParser` but `MatchTranslations` keys by the
   real line number, finds nothing for `#200..#214` and raises
   `UntranslatedLinesError`. The intended fallback `TryFuzzyMatches` is dead
   code in v1.6.0: it finds the 12 fuzzy matches (logs
   `Found fuzzy match for line N`) but `#unmatched.remove(item)` is commented
   out (`TranslationParser.py:178`), so the lines are never actually moved
   back into the translated pool. The batch is then joined as fully
   untranslated and dropped from the output.

## Fixes used for the final evidence run

1. Use PySubtrans's native `description` context slot instead of appending
   TMDB text to `prompt`. The library places this value in a tagged
   `<context><description>...</description></context>` block before the
   subtitle instruction. A direct probe found 0/6 numbering echoes with the
   native slot versus 4/6 when the same text was manually spliced into the
   user prompt.
2. Apply the same small parser patch to all three arms. PySubtrans 1.6.0's
   `TryFuzzyMatches` cannot return recovered lines to `MatchTranslations`, and
   also reuses one candidate when duplicate source captions occur. The
   harness replacement matches by original text, consumes each candidate once,
   and preserves recovered lines. This is explicitly a v1.6.0 library defect
   workaround, not a product feature.

With both fixes, the final repeated checks were complete: A 2/2, B 3/3, and
C 2/2 clean runs were 60/60 timestamp-aligned blocks. The final canonical
A/B/C run was also 60/60 for every arm.

## Added D arm (Glossary-only isolation)

The original A/B/C design only isolated the Context increment (B-A) and the
Glossary-on-top-of-Context increment (C-B). It never measured the native
Glossary alone against baseline, which is the #14 question. A fourth run,
D = A + `build_terminology_map=True` with no injected Context, produced 60/60
blocks in 15.7s and scored 3/9 on the gold rubric — identical to baseline A
(A 3/9, B 6/9, C 6/9, D 3/9). D learned 13 entries but only 1 of the 6
gold-overlapping forms was authoritative-correct, re-confirming the variant
forms (`东医`, `姜大人`, `警察局`) the Context arms had already
disambiguated.

So the rubric benefit traces to the Context block, not to the native dynamic
terminology self-learning. The dynamic Glossary is not a quality aid on its
own at this sample size; with no Context it can actively cement wrong forms.

## Remaining notes

- The Library bug 2 (no-op `TryFuzzyMatches`) should be reported upstream
  (and/or fixed as a vendored patch) regardless of the harness framing; it
  silently drops whole batches on any numbering hiccup.
- Exact token counts are not available from the DeepSeek response parser;
  use prompt-length and wall-time as proxies.
- Final canonical run: A 14.6s, B 16.6s, C 16.1s. Serialized prompt
  character proxies were A 10,904, B 17,516, C 19,471; these are not provider
  token/cost measurements.
