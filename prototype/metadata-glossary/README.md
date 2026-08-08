# Metadata-only Glossary prototype

Throwaway prototype for [CueWeaver #14](https://github.com/kfstorm/cueweaver/issues/14).

It accepts a TMDb TV id plus season and episode, then tries:

1. TMDb episode credits for English character names.
2. Wikidata labels for character entities related to the series with `P1441` or `P674`.
3. Wikipedia's structured `langlinks` API as a conservative fallback for
   unresolved roles. `pageprops` is retained when available as entity evidence.

Wikipedia article-body extraction is deliberately not used. Its prose and
tables are not structured consistently enough to establish an auditable
role-to-translation mapping.

Run it with Python 3.10+:

```bash
TMDB_API_KEY=... python prototype/metadata-glossary/build_glossary.py \
  --tmdb-id 1399 --season 1 --episode 1 --output /tmp/got-s01e01.json
```

The output contains Terms with provider, URL, confidence, and a summary of
coverage and provider failure triggers. Network access is intentionally
explicit and the script never reads a Media or subtitle.

This is evidence code, not production code. The generated report belongs in
the issue discussion; no v0.1 scope decision is made here.
