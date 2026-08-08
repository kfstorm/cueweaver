# Research — Automatic Context / Glossary data-source chain

- **Ticket:** GitHub issue **#5** “Automatic Context/Glossary data-source chain” (part of #1; feeds #10, #11). The branch was created for ticket #14 as named in the task; #14 does not exist in `kfstorm/cueweaver` (the ticket body matches #5 exactly), so #5 is the canonical ticket.
- **Kind:** Research only. No product code, no prototype, no decision on v0.1 in/out of scope. Facts from primary sources + a recommendation (marked `DECISION GAP` where the source material ends).
- **Method:** Primary sources only — TMDb API docs, MediaWiki/Wikidata/Wikipedia docs, W3C WebVTT spec, DeepSeek API docs — plus live, reproducible API probes (Wikidata `wbsearchentities`/`wbgetentities`, WDQS SPARQL, Wikipedia REST summary, TMDb examples). Every verified claim below carries its source URL.

---

## TL;DR — recommended chain and roles

| Step | Layer | Provides | Blocks if it fails? |
| --- | --- | --- | --- |
| 0 | Media identification (filename → title+year, or user pick) | The *query* TMDb needs | No — nothing else starts without an identity, but that identity is obtained from the Media/Discovery side, not from this chain. |
| 1 | **TMDb** (optional; needs API key) | **Context**: `overview` (plot synopsis), `tagline`, `genres`. Also `imdb_id` (stable bridge), credits with cast `character` names. | No — skip Context; try the Wikipedia fallback below |
| 2 | **Wikidata** | **Terms**: film Q → `cast member (P161)` → character Qs → `en`/`zh` labels+aliases; `wbsearchentities` for places/orgs found by the pre-scan. | No — skip the Glossary |
| 3 | **Wikipedia** | Extra Context in target language (zh) + disambiguation signal (`type`), canonical `extract`. | No — drop it |
| 4 | **Subtitle pre-scan** | Salience/hint gate on Terms (only names that appear in the Source text) **and** fallback term candidates when steps 1–3 are empty. | No — it degrades to being unused (an empty filter is harmless) |

**Fallback baseline:** if any/all provider hits fail (error, `429`, timeout, no match), the translation still runs with **no Context and no Glossary**. Context/Terms are additive enhancements; they must never be a hard dependency of a Job. Because WDQS itself is documented as a *low-availability* service (≈95% SLO, <10 min lag, throttles), treat every API layer as best-effort with timeouts + circuit-breaking, not as a required step. (Sources below.)

---

## 1. Confirmed facts

### 1.1 TMDb — the Context layer

- `GET /3/movie/{id}` returns `overview` (a plot synopsis), `tagline`, `genres`, `original_title`, `original_language`, `release_date`, `status`, and **`imdb_id`** such as `tt0076759`. This is the narrative/narrative-ish material → **Context** in CueWeaver terms. [TMDb movie details](https://developer.themoviedb.org/reference/movie-details)
- Search: `GET /3/search/movie?query=...` searches original/translated/alternative titles and returns a list of candidate movies (with `overview` per candidate). [TMDb search/movie](https://developer.themoviedb.org/reference/search-movie). TV analog: `search/tv`. A text query is *ambiguous*: multiple matches are normal (example “Fight Club” returns movies, documentaries, a Chinese film, …). So identification must resolve to a single TMDb ID before details. (ambiguity UX lives in issue #4.)
- **External-ID bridge:** `GET /3/find/{external_id}?...external_id=imdb` finds a TMDb record from a pre-existing IMDb ID. [TMDb find-by-id](https://developer.themoviedb.org/reference/find-by-id); [Finding data](https://developer.themoviedb.org/docs/finding-data). This matters: an IMDb ID can arrive from a metadata side union and is then usable as the bridge to Wikidata.
- **Credits:** `GET /3/movie/{id}/credits` returns a `cast` array with `character` (role/character name-string) and `order` (billing order) plus `crew` with `job`. [TMDb movie credits](https://developer.themoviedb.org/reference/movie-credits). Cast size: the endpoint’s `cast` is a flat list, not a fixed-size slice — dumping it grows with cast size, so it must be filtered (see §4).
- **One request packaging:** `append_to_response` lets `details,credits` be one HTTP call (movie/TV/season/episode/person namespaces; up to 20 appends). [TMDb append-to-response](https://developer.themoviedb.org/docs/append-to-response).
- **Auth & cost:** developer API key required (free, **non-commercial with attribution + TMDb logo**, no contractual SLA; attribution must appear in an “About/Credits” section). [TMDb FAQ](https://developer.themoviedb.org/docs/faq).
- **Rate limits:** the legacy 40 req/10 s limit was disabled (2019); current ceiling is “somewhere in the 40 requests per second range” — respond to `429` and back off. [TMDb rate limiting](https://developer.themoviedb.org/docs/rate-limiting). So a *zero-config default* cannot assume the key is present: TMDb is optional, from the user’s own API key config.

### 1.2 Wikidata — the Term mapping source

- **Identification:** WDQS SPARQL can resolve an IMDb ID to a film entity: `?film wdt:P345 "tt0076759"` → `wd:Q17738`. (SPARQL entry points and formats at [WDQS user manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual#SPARQL_endpoint); verified live.) TMDb’s `imdb_id` therefore anchors the TMDb→Wikidata bridge.
- **Cast → characters:** film entities carry `cast member` statements (`P161`). Q17738's `claims.P161` holds its 24 cast-member Qs. A WDQS probe count: **1,368,476 cast-member claims** on `P31`-derived movie items (live 2026-08-08), so a films→characters mapping exists at scale. [property P161](https://www.wikidata.org/wiki/Property:P161); [WDQS manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual).
- **Character entities are multilingual:** `wbgetentities&ids=Q51802` (Han Solo) returns `en` label "Han Solo", `zh` label "韓·蘇羅", `zh` alias "韓·索羅", descriptions, plus a `zhwiki` sitelink "韓·蘇羅". This is exactly the fixed character-mapping material (source term = en label/alias; the zh label/alias is the translation candidate). Live probe; API: `wbgetentities`, `wbsearchentities`.
- **Search is ambiguous by design:** `wbsearchentities&search=Han+Solo` returns >2 candidates; the first is “Han solo — species of trilobite”, the character is 2nd. So a free-text name → entity mapping needs disambiguation (see §3). [Data access best using search](https://www.wikidata.org/wiki/Wikidata:Data_access) (etiquette + WDQS-not-for-search) — text search is done via `wbsearchentities` in API, not WDQS.
- **Data-quality note (which props find names?):**
  - `wbsearchentities` searches labels/aliases/descriptions in ~all languages (incl. zh). Handles places/orgs that `P161` may not link directly (e.g. a fictional city is a separate Q with en/zh labels).
  - Entity quality: data is crowdsourced and uneven; a Q id + `description` + `sitelinks` helps; "instance of" (P31) values are noisy (e.g. P31 on Han Solo was the multiple items: fictional character, character, etc.). So **reliability: good for main fictional characters of blockbusters & studios, thinner for niche media** — a decision-gap must set a coverage threshold.
- **Rate limits & operations:** WDQS public: single query ≤60 s; ~60 s of computation / (user-agent+IP) / minute; 30 error queries / min; **5 parallel queries / IP**; `429` → `Retry-After`; abuse → temporary ban. [WDQS manual limits](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual#Query_limits). Availability SLO ~95% with <10 min replication lag; WDQS is explicitly documented as **not for user-synchronous flows** — if you still sync on it, add caching/circuit-breaking. [WDQS technical interactions](https://wikitech.wikimedia.org/wiki/Wikidata_Query_Service/Technical_interactions).
- **No key, free.** Requires a descriptive `User-Agent` (or gets blocked); [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_policy).
- **Bad-for-bulk:** if the desired data set is large, WDQS is the wrong tool — Wikidata provides weekly full dumps instead. [WDQS manual — Data set](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual#Data_set) and [Wikidata Data access](https://www.wikidata.org/wiki/Wikidata:Data_access).

### 1.3 Wikipedia — supplementary Context + disambiguator

- REST endpoint `GET https://en.wikipedia.org/api/rest_v1/page/summary/{title}` returns `extract` (lead plaintext), `type` ∈ {`standard`, `disambiguation`, `wikidata`}, `description` (from Wikidata), `wikibase_item`. Live probe for the episode title returned `type:"standard"`, an `extract` with the premise lead-in, and `wikibase_item: Q17738`. [Wikimedia REST API](https://www.mediawiki.org/wiki/Wikimedia_REST_API/en); OpenAPI: [summary.yaml](https://github.com/wikimedia/restbase/blob/master/v1/summary.yaml).
- `type:"disambiguation"` is an explicit flag — a cheap way to detect name-ambiguity pages and avoid presenting them as canonical Terms.
- The `extract` is by definition a text extract of the first several sentences — i.e. the *front matter* is biased to premise, not late-film twists (still not spoiler-proof — see §4).
- **Free, no key**, served from CDN cache; requires the User-Agent policy. <https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy>.

### 1.4 What the subtitle pre-scan can and cannot do

- Subtitle files are plain text cues (SRT/VTT/ASS). WebVTT: a file holds a sequence of text cues; cue text is free-form; dialogue is sometimes annotated by **voice span `<v Name>`** which is a first-class name signal. [WebVTT spec](https://www.w3.org/TR/webvtt1/).
- Useful signals from scanning Source text: tokens repeated in an upper-cased/Title-Cased form; tokens that appear both alone and in phrase form; subtitle-vendor name prefixes (`<v` spans). These are *candidate source terms*, not verified facts.
- **Risks (noise):**
  - All-caps words can be cries/emphasis (`"NO!"`, repeated-word) or non-name; SDH files contain `[MUSIC]`, `[THUNDER]` or scene-led labels not names.
  - Proper nouns are not always capitalized in transcripts/dialogue; some films use stylized casing.
  - Non-Latin / no-case scripts (e.g. CJK, Thai) provide **no case signal**.
  - Transcription/OCR errors in the subtitle propagate.
  - A name not in the dialogue at all won’t be found (coverage gap).
- Therefore the pre-scan is best used as a **salience/filter gate** and a **fallback candidate source**, not as a producer of verified term translations; its signal thresholds are `DECISION` (below).

### 1.5 Provenance & confidence building blocks that already exist in the sources

- Wikidata statements carry **rank** (`preferred`, `normal`, `deprecated`) and **references**; WDQS exposes the “truthy” (preferred-rank) view. [WDQS manual — Basics/truthy](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual#Basics_-_Understanding_SPO). This is a structured precedent for “which statement is most trusted”.
- Stable snapshots exist for all three: Wikidata entities have entity/page URNs + revision ids (in the `wbgetentities` API response); Wikipedia pages carry an ETag `{revid}/{tid}` and a `timestamp` on the REST summary. TMDb has no version field, so snapshot by fetch time. A Term can therefore carry `provenance = {provider, entity_id, revision_id, url, captured_at}` plus confidence flags.
- Chinese-specific: `wbgetentities&languages=en|zh&languagefallback=1` returns the entity’s zh label, but **China/Taiwan variants and per-region translation conventions differ** (e.g. `漢·索羅` vs other accepted renderings). The Wikidata zh label is a strong candidate, not the authority.
- **User override precedence is already a domain invariant** (the CONTEXT.md glossary: User override always takes precedence over the automatic Glossary). The model just needs to place `source=user` outside the ranked auto-layers. [repo CONTEXT.md](https://github.com/kfstorm/cueweaver/blob/main/CONTEXT.md).

### 1.6 Restraint: scale, tokens, cost anchors

- **Token math (DeepSeek V4 / OpenAI-compatible):** API docs: 1 English char ≈ 0.3 token, 1 Chinese char ≈ 0.6 token; per-1M-token billing (V4-Flash: $0.14/M in cache-miss, $0.0028/M cached-in, $0.28/M out). [DeepSeek token counting](https://api-docs.deepseek.com/quick_start/token_usage/), [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/). A 32-Term glossary is well under ~1k tokens and trivial vs a 90-min subtitle (~10k–20k tokens) — but **the glossary is re-injected on every chunk/retry**, and system-prompt caching economics make a stable prefix cheap only if the glossary is order/cache-stable. Restraint is therefore as much a *cache* design as a size design.
- **Whole-cast dumping is real:** a studio film's credits list 40+ names; resolving every P161 cast Q yields a character per actor; an entity fetch on 500 Qs blows both bandwidth and the injection budget. No source sets a "salient cast" number — that's `DECISION` (recommend top-billed order ≤ ~16 + subtitle-salient only, see §4).
- **Spoilers:** no provider flags spoilers; TMDb `overview` and Wikipedia entries simply don't guarantee it. Summaries often *do* reveal plot twists (e.g. the Star Wars IV lead already names the cast heroes and the Death Star mission). Mitigation is a product/UX decision (`DECISION`), not a data property.

### 1.7 Zero-config & key requirements

- TMDb = **key required** (free, non-commercial, with attribution) — zero-config default can only promise Wikidata/Wikipedia which need no key. TMDb configurability is a dependency; the rest of the chain is key-free.
- All Wikimedia services: **User-Agent required** (else 403/block); no hard read API rate limit but “request in series”, use `maxlag` + gzip + bulk with `titles=A|B|C`. [API:Etiquette](https://www.mediawiki.org/wiki/API:Etiquette), [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy), [Wikimedia APIs/rate limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits).

---

## 2. The recommended chain (integration order + roles)

1. **Identify (from filename / Discovery / User):** a concrete Media title. Not this ticket’s own data-source, but the chain’s starting token. (issue #4 covers ambiguity UX.)
2. **TMDb (optional, best-effort):** `search` → pick → `details`+`credits` in one call (append). Purpose split:
   - `overview` (+`tagline`/genres) → **Context** injected into the translation prompt (narrative metadata; also a spoiler/risk surface).
   - `imdb_id` → anchor the Wikidata bridge; `cast[name,character,order]` → Term candidates.
3. **Wikidata (main Terms provider):**
   - bridge: `imdb_id` via WDQS `P345` → film Q;
   - then `film Q` → its `P161` cast member Qs → for each: `wbgetentities` labels/aliases/descriptions (`languages=en|zh` + `languagefallback`) → Term = {source-term: en label/alias; candidate-translation: zh label/alias; provenance: Q + revision; class: character}.
   - non-cast terms (places, orgs, titles, slogans): candidate source from the pre-scan, resolved via `wbsearchentities` (with disambiguation via `description`).
4. **Wikipedia (optional enrichment/Context fallback):**
   - if TMDb Context missing or empty: `page/summary` of the selected title in `zh` (or en) → Context; also the `type` field to skip disambiguation pages.
   - optional per-Term: zh sitelink or `type` check.
5. **Subtitle pre-scan:** run token-level heuristics on the Source text; use as (a) a **salience gate** for steps 2–3 (keep only Terms that actually appear in the dialogue; removes whole-cast dumps) and (b) a **fallback candidate list** when TMDb/Wikidata are empty (feeds `wbsearchentities`). Always runs last — locally, zero-cost, no network.

**Degradation semantics (documented behavior, must hold):**

- TMDb fails (no key, 429, down, no match) → **no TMDb Context** → try Wikipedia as Context fallback → if that fails, no Context; Terms are still possible via pre-scan → Wikidata.
- Wikidata fails (WDQS 5xx/throttle, timeout, no match) → **no Glossary**; Context still injected.
- All fail (offline, no key, no WDQS) → **baseline translation** with Context+Glossary both empty. The translation is the product; the enrichment is enhancement.

---

## 3. Provenance & confidence — recommended model

- **Model shape (per Term):** a rank-able table
  - `priority`: `user_override` > `wikipedia/zh-sitelink` > `wikidata-preferred-rank` > `wikidata-normal` > `search-near-match`/`subtitle-scan`.
  - `provenance`: `{ provider: tmdb|wikidata|wikipedia|prescan|user; id: imdb|QID|page_title|(text-file|src-info); revision_id|etag|timestamp; capture URL }` — all exist (see §1.5).
  - `confidence` (0..1) composed of: exactness of match (exact zh label vs alias vs search+disambiguation-description-match), provider tier above, disambiguation desc class match (e.g. “fictional character from Star Wars” for a Character Term), presence of a target-wiki sitelink, and subtitle-salience (appears-scaled by frequency).
- **User override:** stored separately (source = user, priority-above-every auto) and merged at prompt-build time so the auto Term can never win — this satisfies the domain’s “Glossary / User override always takes precedence” (CONTEXT.md).
- **Disambiguation rules for text search (`wbsearchentities`):** candidate list returned with `display.description`; score candidates by description match to the expected class of term (character vs location vs org) derived from a named-entity pattern; reject any response whose `type == "disambiguation"` on Wikipedia. If the candidate set stays ambiguous → drop the Term, don’t guess.

---

## 4. Restraint (avoiding cast-dumps, spoilers, token bloat)

- **Filter the cast list by relevance:** keep a Term only if (a) it appears in the Source text (pre-scan salience, case-insensitive match) **or** (b) it is among top-N billing order (e.g. 8–16) with a zh label present. This turns a 40-name cast list into a “Terms to inject” handful. (Top-N thresholds are `DECISION` — pick in #10’s prototype.)
- **Hard budget:** cap the injected glossary (e.g. ≤ 32 Terms ≈ <500 tokens by DeepSeek's char→token ratios) — trivially small next to the translation body, cheap and cache-friendly when kept as a stable prefix (see §1.6).
- **One subset per Job/Media** — reuse a cached glossary across chunks and retries (document shrinks; `cache-hit` units).
- **Spoilers:**
  - Context = TMDb `overview` + optional Wikipedia lead; neither guarantees spoiler-freedom — the prototype (#10) should forbid spoiler-heavy material from evaluation data and measure whether Context injection leaks them (`DECISION`).
  - Option: inject only the premise portion (first 1–2 sentences from the Wikipedia lead) instead of the whole `overview`. Prototype evidence will decide.
- **Never dump:** no full cast text, no JSON blobs, no “all aliases” cards. Each Term is compact: `en-term → zh-term (provider)`.

---

## 5. Decision gaps (must be settled in #10 / via prototype; not answered by primary sources)

1. **Pre-scan heuristics threshold:** what qualifies as a viable term candidate (case/repetition/voice-span signals) — including target/source languages without case, which need a fallback.
2. **Glossary size budget** (top-N + salience threshold) — tune with real data in the prototype.
3. **Confidence model numerics** (weights, cutoff below which a Term is dropped).
4. **zh canonical form / variant choice**: choosing between the zh-Wikipedia rendering (region-dependent) and a custom registry.
5. **Spoiler posture**: whether to truncate Context/summary, and how to test it quantitatively (see Restraint).
6. **TMDb key configuration model** for a zero-config product (required key but user-configurable, or optional; default off unless present).
7. **TV-series vs episode semantics**: series-level cast (P161) + overview vs episode-level — decision needs the prototype’s sample Job type consistency.
8. Whether a second non-Wikidata Terms candidate (e.g. film-related lists) is even needed given the above coverage.

---

## Sources index (primary)

- TMDb: [movie details](https://developer.themoviedb.org/reference/movie-details) · [movie credits](https://developer.themoviedb.org/reference/movie-credits) · [search/movie](https://developer.themoviedb.org/reference/search-movie) · [find-by-id](https://developer.themoviedb.org/reference/find-by-id) · [append-to-response](https://developer.themoviedb.org/docs/append-to-response) · [rate limiting](https://developer.themoviedb.org/docs/rate-limiting) · [FAQ](https://developer.themoviedb.org/docs/faq) · [Finding data](https://developer.themoviedb.org/docs/finding-data) · [errors](https://developer.themoviedb.org/docs/errors)
- Wikidata/Wikimedia: [P161 cast member](https://www.wikidata.org/wiki/Property:P161) · [P345 IMDb ID](https://www.wikidata.org/wiki/Property:P345) · [Wikidata:Data access](https://www.wikidata.org/wiki/Wikidata:Data_access) · [WDQS manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual) · [WDQS technical interactions](https://wikitech.wikimedia.org/wiki/Wikidata_Query_Service/Technical_interactions) · [API:Etiquette](https://www.mediawiki.org/wiki/API:Etiquette) · [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy) · [Wikidata API (search/entities)](https://www.wikidata.org/w/api.php?action=help&modules=wbsearchentities) · [Wikimedia REST API](https://www.mediawiki.org/wiki/Wikimedia_REST_API/en) · [page summary OpenAPI](https://github.com/wikimedia/restbase/blob/master/v1/summary.yaml) · [WDQS SLO](https://wikitech.wikimedia.org/wiki/Wikidata_Query_Service/Runbook)
- Wikipedia: [Wikipedia:Disambiguation](https://en.wikipedia.org/wiki/Wikipedia:Disambiguation)
- Subtitle format: [WebVTT W3C CRD](https://www.w3.org/TR/webvtt1/)
- Cost: [DeepSeek token usage](https://api-docs.deepseek.com/quick_start/token_usage/) · [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- Domain invariant: [CONTEXT.md (User override precedence)](https://github.com/kfstorm/cueweaver/blob/main/CONTEXT.md)

*Live probes (Wikidata/WDQS/Wikipedia REST/WebVTT, run 2026-08-08) are captured above with their exact queries. Numbers may drift.*
