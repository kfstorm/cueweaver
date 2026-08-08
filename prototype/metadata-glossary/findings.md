# Metadata-only Glossary findings

Prototype ticket: [#14 Metadata-only auto-Glossary construction](https://github.com/kfstorm/cueweaver/issues/14).

Date: 2026-08-08.

## Runs

| Sample | Episode cast roles | Wikidata-resolved roles | Accepted Terms | Coverage |
| --- | ---: | ---: | ---: | ---: |
| Game of Thrones, TMDb 1399 S01E01 | 19 | 12 | 13 | 13/19 (68.4%) |
| Dong Yi, TMDb 38852 S01E11 | 18 | 0 | 0 | 0/18 (0%) |

The Game of Thrones terms were primarily character entities with bilingual
Wikidata labels, including Jon Snow -> 琼恩·雪诺, King's-world characters, and
the Targaryen/Stark/Lannister cast. One additional unresolved role was found
through a structured Wikipedia `langlinks` mapping. A manual spot check found
the accepted labels semantically correct; Chinese regional/script variants
remain a canonical-form concern rather than a lookup failure.

Dong Yi's series item had no usable `P1441`/`P674` character relations for
this path. Wikipedia `langlinks` is queried only as a structured fallback;
article-body extraction is not used. Any result still needs manual precision
review because an English search result can be ambiguous.

## Verdict

- Metadata-only construction is useful for mainstream series with populated
  Wikidata character links: this sample reached 68.4% episode-role coverage
  after the structured Wikipedia fallback.
- It is not a dependable standalone source for niche series: Dong Yi reached
  0% and needs an explicit empty-Glossary degradation.
- Wikipedia prose/table extraction is excluded from this data-source chain.
  Structured `langlinks`/`pageprops` may supplement Wikidata, but unresolved
  or ambiguous mappings must be dropped rather than guessed.
- Terms need provider/entity provenance and a target-language variant policy.
  These results support the #11 decision to keep the Wikidata layer in v0.1
  without claiming universal coverage or a reliable article-body cast fallback.

The measurements are repeatable with `build_glossary.py` and a TMDb v3 key or
v4 Bearer token. The script never reads a Media, subtitle, or audio file.
