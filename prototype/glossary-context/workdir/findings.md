# Prototype #10 Findings

## Decision evidence

The native PySubtrans `description` context slot is required for this
prototype. Manually concatenating the TMDB synopsis into the user prompt made
the model treat the synopsis as subtitle input when dynamic terminology
instructions were enabled. The model then echoed the system example numbers
(`#200`...) instead of the source block numbers, and PySubtrans 1.6.0 dropped
the affected lines. Native `description=` renders the synopsis inside tagged
`<context><description>` content and removes the high-frequency failure.

The harness also applies the same parser recovery workaround to A/B/C. It
fixes PySubtrans 1.6.0's incomplete fuzzy matching and consumes duplicate
caption candidates exactly once. This keeps output integrity from being a
confounder in the quality comparison; it does not alter translation text.

## Final run

| Arm | Configuration | Elapsed | Structural output |
| --- | --- | ---: | --- |
| A | No Context, no dynamic Glossary | 14.6s | 60/60 |
| B | TMDB Context via native `description` | 16.6s | 60/60 |
| C | B + dynamic `build_terminology_map=True` | 16.1s | 60/60 |
| D | dynamic Glossary only, no Context | 15.7s | 60/60 |

Repeated integrity checks after the final workaround were clean: A 2/2, B
3/3, and C 2/2 runs produced 60/60 timestamp-aligned blocks. The D arm
added later also produced 60/60.

Serialized prompt-character proxies for the canonical run were A 10,904, B
17,516, C 19,471, and D 13,093. These are prompt-size proxies, not provider
token or cost measurements.

## Quality rubric

The deterministic source-aligned scorer used a small TMDB-derived gold set,
not English/Chinese string similarity. Final gold occurrences were:

- A: 3/9
- B: 6/9
- C: 6/9
- D: 3/9

The single-sample result supports a Context benefit over baseline. It does not
show an additional C-over-B quality benefit on this rubric: B and C tied.

The D arm (dynamic Glossary, no Context) scored the same 3/9 as baseline A,
which is the isolation the original A/B/C design was missing: the rubric
benefit comes from the Context block, not from PySubtrans's native dynamic
terminology learning. On this sample the native Glossary alone does not lift
quality without Context.

## Dynamic Glossary

The final C snapshot contained 7 learned entries. Four overlapped the
authoritative gold set used here; 2 of those 4 exactly matched the gold form.
The other learned values were unresolved or variant forms (`司宪府`, `张氏`),
so the prototype should report them as ambiguous rather than silently count
them as correct. Dynamic learning also exposed title inconsistency across
runs (`殿下`, `陛下`, `大王`, etc.).

The D arm learned 13 entries but only 1 of the 6 gold-overlapping forms was
authoritative-correct; the others re-confirmed variant forms (`东医`, `姜大人`,
`警察局`) that the Context arm had already disambiguated. When dynamic
terminology runs without the disambiguating Context, it can cement the wrong
forms instead of fixing them.

## Signal

Go for Context as a v0.1 quality aid, but do not claim that automatic dynamic
Glossary learning has demonstrated an incremental quality benefit yet. The
added D arm isolates the native Glossary on baseline (3/9, same as A), so the
current evidence for a product Glossary must rely on the disambiguating
Context rather than on self-learning alone. Keep Glossary terms
provenance/confidence-aware, require ambiguity handling, and track the
PySubtrans 1.6.0 parser defect separately from the product decision.
