<!-- Template Version: 2.5.0 -->
---
feature: corpus-attacks
status: active
session_ready: true
depends_on: [corpus-harness]
vision_ref: "T2.3 — Injection regression corpus (CI)"
type: epic-child
size: L
epic: forage-injection-corpus
epic_seq: 2
epic_final: false
execution_order: [US-001, US-002, US-003, US-004, US-005]
created: 2026-09-19
updated: 2026-09-24
---

# Feature Spec: Attack Corpus — Sixteen Categories, Three Surfaces, Permissive Third-Party Samples

> Spec 2 of 6 (spec 0 added 2026-09-24) in `epic-forage-injection-corpus` (rulings 3, 6a, 8, 14).
> Builds on spec 1's record format, lint and drivers. Every record here is data (ruling 8): this
> spec names categories, counts, variants and sources — never a payload.

## Overview

The seed corpus proves the instrument on one record per category. This spec grows it into a
measurement: structural families with the obfuscations that actually appear in the wild, the eight
in-the-wild carriers the Zscaler campaigns used, the two published Prompt Guard 2 evasions as
parameter sweeps rather than single examples (Prompt Overflow density thinning, Zenity repetition),
hardening spec 7's two residual shapes, the classifier-only natural-language and "authoritative
source" poisoning shapes Meta's model card says PG2 does not target — and, under owner decision 3,
sampled records from the three sets whose licences were read and found permissive (AgentDojo,
LLMail-Inject, CyberSecEval), re-rendered into web form with the URLs rewritten to reserved names.

## Goals

- ≥ 200 attack records over all 16 categories, ≥ 5 per category, across `search`, `page` and `text`
  surfaces, lint-clean, every URL reserved, no secret shape (ruling 14; lint floors are asserted from
  spec 5 US-002).
- Every category that is a *technique* (density, repetition, carriers, obfuscation) is a sweep with
  its parameter recorded in `params`, so the report can show where a defence stops working.
- Third-party samples enter only from sets whose licence was resolved at the directory of the
  files taken (MIT for all three), pinned by commit SHA or dataset revision, capped, stratified where
  the source offers labels, with a `NOTICE` entry each; BIPIA and WASP are recorded as rejected on
  licence so nobody re-litigates them.
- ≥ 6 languages among attack records; every borrowed user-turn payload is re-homed into a page
  body, snippet or metadata field (`source.framing = rehomed_direct`).

## User Stories

Execution order: `[US-001, US-002, US-003, US-004, US-005]` (document order; US-005 consumes
US-004's samplers).

### US-001: Structural families with obfuscation variants

**Priority:** P1

**Description:** As a maintainer, I want every stage-2 category, plus the two audit bypass shapes,
represented across the three surfaces in the forms attackers actually publish — plain, re-cased,
entity-encoded, zero-width-split, tag-split, confusable-substituted, second-paragraph, URL-borne — so
the corpus measures the regexes' real reach instead of their happy path.

**Independent Test:** `uv run pytest tests/test_corpus_lint.py tests/test_corpus_harness.py` passes;
`load_corpus()` yields ≥ 8 records for each of the nine categories `instruction_override`,
`authority_impersonation`, `prompt_boundary`, `encoded_payload`, `suspicious_url`, `exfil_beacon`,
`envelope_breakout`, `line_anchored_role`, `url_borne_envelope`, each with `params.variant` set;
driven with `ReplayClassifier(fallback=0.0)`, every **pinned** `plain` record has its pinned outcome
(the pin table under Implementation Hints — `plain` is defined per regex × surface as the form that
reaches stage 2 *as text* on that route, and `url_borne_envelope` is pinned per URL shape, not as one
class), while the obfuscated variants' outcomes, and the unpinned `plain` (regex, surface) pairs', are
recorded (not asserted) in the story's Implementation Notes as a count per variant. *(Round 4,
2026-09-24: the round-3 wording asserted every `plain` BLOCK record `blocked` and every `plain`
SUSPICIOUS record `flagged` by expectation; measured at 403e9c5 that fails for tag-shaped triggers on
`page` / `search` and for most URL-borne envelope shapes — see the pin table.)*

**Implementation Hints:**
- Pattern list: `_PATTERNS` (`pipeline/stage2_structural.py`, ~:61-241 at 403e9c5; byte-unchanged
  since `20ddb2a`) (six `instruction_override` regexes, six
  `authority_impersonation` including the three line-anchored `^System:` / `^assistant:` / `^POPPY:`
  with `re.MULTILINE`, three `encoded_payload`, four `prompt_boundary`, `suspicious_url` incl. the
  private-IP `href`/`src` regex, `exfil_beacon` markdown-image-with-template, `envelope_breakout` with
  the entity-encoded `<` forms). Author each record to one regex and name it in `params.variant`'s
  companion `notes` (e.g. `regex: ignore-previous`).
- **Variants** (`params.variant`, closed list in `vocab.py`): `plain`, `case`, `entity` (HTML entities
  for `<`, `[`, backticks), `zwsp` (U+200B / U+200C inside the trigger token), `split_tags`
  (`<b>ig</b>nore` in `page` HTML), `confusable` (Cyrillic а/е/о substituted in a trigger word),
  `second_paragraph` (content with a paragraph or line break at or inside the trigger, any surface;
  a record whose break falls *inside* the trigger adds the `notes` line `break-inside-trigger` —
  widened round 5, see the dual-form hint), `url_query`, `url_path`,
  `title_field`. Not every variant applies to every category (e.g. `url_*` for `envelope_breakout`,
  `suspicious_url`, `url_borne_envelope`); the lint's `params` allowlist admits `variant` for all nine.
- **`plain` means "reaches stage 2 as text on this route"** (added 2026-09-24, validation round 4 —
  measured at 403e9c5 by driving `extract_html`, `_scan_forms_for_search_text` and `scan_structural`
  directly). Both HTML routes now parse markup before stage 2: `/retrieve` through `extract_html`,
  and — new with hardening spec 1 — `/search` too, because `_scan_forms_for_search_text`
  (`pipeline/orchestrator.py`, ~:940 at 403e9c5) runs every title and snippet through `extract_html`
  and then `html.unescape`, and derives the wire form from that parsed text. A *literal* tag-shaped
  trigger — the `instruction_override` system-tag regex, a literal retrieval-envelope tag
  (`envelope_breakout`), an `href` / `src` attribute inside a real tag (the `suspicious_url` regexes)
  — is consumed as markup and scans CLEAN on `page` and `search` (measured: both tag shapes CLEAN on
  both routes, while scanning BLOCKED / SUSPICIOUS as `text`). `/extract` does not parse HTML
  (`pipeline/stage1_upload.py` calls `normalize_text` only), so there the literal form is `plain`.
  So, per surface: on `text`, `plain` is the literal trigger; on `page` / `search`, `plain` for a
  tag-shaped regex is its **entity form** (measured to survive one decode level on `/retrieve` and
  fire on all three routes' scanners for the envelope regex; the system-tag regex fires on
  `page` / `search` in entity form but *not* on `text`, whose scanner sees the undecoded entity). A
  literal tag-shaped trigger authored for `page` / `search` is a legitimate record, but its variant
  is `plain` with `notes: tag-consumed` and it is unpinned — with the marker on the surrounding
  sentence (rule (b) below) it will score `leaked`, and that is a measurement, not a failure.
- **`suspicious_url` private-IP probes live in visible text**, never in a result URL or a live tag
  attribute (added 2026-09-24, validation round 4). A private literal in a `search` record's result
  URL is omitted by `_canonicalize_search_url`'s `address_class` rule (omission reason
  `blocked_url`, host class `private_literal`, measured) before stage 2 sees it — `blocked`, but not by the
  regex the record names; inside a real `<a href>` it is consumed by extraction and scans CLEAN. Put
  the `href="http://10.…"`-shaped text in snippet prose or a `page` code sample, with
  `params.url_exception: "private_ip"` (spec 1's lint).
- **The `marker` must survive its own variant** (added 2026-09-19, validation round 1 — this was a
  leak-check false negative in the flattering direction). A record's `marker` is compared against the
  wire text *after the pipeline has already de-obfuscated it*: `_collapse_invisible` /
  `_normalize_text` (`pipeline/stage1_extraction.py`, ~:92-109 at 403e9c5)
  delete U+200B / U+200C / U+200D / U+FEFF / U+00AD outright, BeautifulSoup decodes HTML entities
  during parsing, and `extract_html` inserts a separator at tag boundaries, so `split_tags` markup
  comes out as fragments — measured at 403e9c5: a newline between them on `/retrieve`, a space on
  the `/search` wire form. `split_tags` therefore *breaks* the trigger token rather than rejoining
  it (expect `leaked` / `neutralised`, not `blocked` — an extractor artefact, not a stage-2 miss;
  corrected 2026-09-24, round 4, from "`get_text` rejoins"). An authored marker that overlaps the
  obfuscated token therefore never matches the wire text, and the record scores `neutralised` while
  the payload arrived intact. Two rules, both enforced by spec 1's lint: (a) the normalised marker
  must be a substring of the **post-pipeline** form of its payload, not merely of the raw payload;
  (b) in practice, choose the marker from a stretch of payload the variant does not touch — the
  obfuscation belongs on the trigger token, the marker on the sentence around it. Spec 1's
  per-variant test is the backstop. The Edge Cases entry about an `entity` variant that the routes
  decode to different depths is about a route-dependent *outcome* difference and is a separate matter from
  this.
- Surfaces: ≥ 3 `search`, ≥ 3 `page`, ≥ 2 `text` per category so each route sees each family —
  **except `url_borne_envelope`, which is `search`-only**: its ≥ 8 records spread over the three
  URL shapes (i)–(iii) below and over path and query placement. The category is a property of
  `/search` result-URL canonicalisation (audit -032); on `page` / `text` there is no result URL, a
  URL in visible prose is not percent-decoded by stage 2, and a URL in a live `href` is consumed by
  `extract_html` — so a `page` / `text` record would have no defined meaning and no pin. An
  envelope tag in visible prose is an `envelope_breakout` record. The rule was written for the regex
  families; this is a one-route category. *(Round 5.)*
- **Pins** (rewritten 2026-09-24, validation round 4 — the round-3 rule pinned every `plain`
  BLOCK-category and every `url_borne_envelope` record `["blocked"]` / "stage-2 regex", which the
  shipped tree contradicts). The table below is the *hypothesis*, measured at scanner level at
  403e9c5; the story's first run through the real routes sets the pins (Decisions Made), and any
  row that measures differently is pinned to what it measured, with the gap noted:
  - `plain` (per the per-surface definition above) of the three BLOCK categories and of
    `line_anchored_role` → `["blocked"]`, `pinned_reason: "stage-2 regex, deterministic"` / the
    audit id.
  - `plain` of the four SUSPICIOUS categories → `["flagged"]`, same reason.
  - `url_borne_envelope`, pinned **per URL shape**, `notes` recording the omission reason or
    verdict so spec 5 can attribute the stage. `_canonicalize_search_url` (`pipeline/orchestrator.py`,
    ~:1235 at 403e9c5) runs the `_SEARCH_URL_RULES` chain on the raw value and only a URL that clears
    every rule reaches stage 2, as its two `scan_texts` (entity-decoded; then once percent-decoded):
    (i) a **BLOCK token** in path or query (a percent-encoded system tag, a `%0A`-prefixed role line)
    scans BLOCKED on the percent-decoded text → `["blocked"]`, `pinned_reason` the audit id (-032);
    (ii) a **percent- or entity-encoded retrieval-envelope tag** clears canonicalisation and scans
    SUSPICIOUS, because `envelope_breakout` is a SUSPICIOUS category → `["flagged"]`, not
    `["blocked"]`; (iii) a **raw `<` / `>`** never reaches stage 2 — rule (1)
    `_url_rule_raw_character_class` rejects it as `invalid_url` / `raw_chars` → `["blocked"]` under
    ruling 9, with `pinned_reason` naming that canonicalisation rule, **not** stage 2. The IPv6
    zone-id shape is measured `invalid_url` (`zone_id` / `unparseable`) and its `fe80::` / `2001:db8::`
    host is neither RFC 2606 nor RFC 1918, so spec 1's lint has no exception for it: this spec
    authors **no** zone-id records and leaves that shape to spec 1's seed record.
  - Everything else — the obfuscated variants and the `tag-consumed` `plain` records — unpinned; the
    obfuscation reach is the measurement.
- **`second_paragraph` and the dual-form scan** (rewritten 2026-09-24, validation round 4). The
  round-3 text cited hardening spec 1's Assumption that non-line-anchored patterns verdict
  identically on collapsed and newline-preserving forms; that Assumption is struck through in the
  archived `feature-hardening-search-sanitization.md` and marked **FALSE** by that spec's own
  validation run. Two of the 24 patterns are compiled without `re.DOTALL` and match across a space
  but not across a newline: `disregard.*instructions` (`instruction_override`, BLOCK) and the
  `exfil_beacon` Markdown-image pattern (SUSPICIOUS). The shipped remedy is that `run_search_pipeline`'s
  per-result loop (`pipeline/orchestrator.py`, ~:1805-1846 at 403e9c5) scans **both** the
  newline-preserving scan form and the collapsed wire form of `title` and `snippet` (plus both URL
  scan texts) and the worse verdict wins. So a `second_paragraph` record whose trigger sits whole
  after `\n\n` is still expected to match `plain` on every route — for the dual-scan reason, not the
  identical-verdict one. Author additionally, for each of those two regexes, at least one record
  whose line break falls **inside** the trigger, on all three surfaces (variant `second_paragraph`,
  `notes: break-inside-trigger`): measured at scanner level at
  403e9c5 it is BLOCKED on `/search` (wire form) but CLEAN on the `/retrieve` and `/extract` scanners,
  which see one newline-preserving form. Pin the `search` record after measuring; record the other
  two, and if they come back `leaked` list them under "for AUDIT_FINDINGS (spec 5 US-005)" — a
  finding, not a fix (ruling 6).
- `encoded_payload`: base64 runs ≥ 40 characters of `[A-Za-z0-9+/]` — keep them free of the secret
  lint's keyword-adjacency (no `key`/`token`/`secret` within the same line) and never a JWT shape
  (`eyJ…`); `\x`-escape runs ≥ 4; `rot13`.
- `exfil_beacon`: a Markdown image whose URL query carries a double-brace template opener, a `${…}` form, and the `%7B` percent-encoded form; bait uses the
  `FAKEKEY-` prefix rule (spec 1 US-001).
- Ids continue from the seed (`atk-0023` …); one file per category (append).

**Acceptance Criteria:**
- [ ] ≥ 72 new records across the nine categories, ≥ 8 per category, every record with
      `params.variant` from the closed list and a `notes` line naming the targeted regex.
- [ ] Surfaces per category as above; every URL under RFC 2606; lint-clean.
- [ ] Pins exactly as specified; the generic pinned-outcome test passes with `fallback=0.0`.
- [ ] Implementation Notes record, per category × variant, the count of `blocked` / `flagged` /
      `neutralised` / `leaked` under `fallback=0.0` (numbers only).
- [ ] `tests/corpus/README.md` gains the variant vocabulary with a one-line definition each.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-002: Hidden-markup carriers — the eight in-the-wild placements

**Priority:** P1

**Description:** As a maintainer, I want the places real campaigns hide instructions — JSON-LD
objects, meta and Open Graph descriptions, off-screen CSS, hidden elements, HTML comments, alt text,
stuffed titles — each carrying several payload phrasings, so the corpus shows for every carrier
whether stage 1 removes it, stage 2 flags it, stage 3 catches it, or it reaches the wire.

**Independent Test:** `load_corpus()` yields ≥ 4 `hidden_markup` records per carrier for all eight
carriers (≥ 32); driven through `/retrieve` with `fallback=0.0`, the carriers measured `neutralised`
at story start (expected: `jsonld`, `meta_description`, `og_description`, `html_comment`) are
pinned `["blocked", "flagged", "neutralised"]` and pass; the remaining carriers' outcomes are
recorded per carrier in Implementation Notes.

**Implementation Hints:**
- Carriers (`params.carrier`, closed list): `jsonld` (payload in `offers`, `publisher`,
  `applicationCategory`, `description` of a `SoftwareApplication` / `Organization` object —
  landscape finding 11; stage 1 parses JSON-LD only for author/date, `_json_ld_documents`
  (`pipeline/stage1_extraction.py`, ~:153-168 at 403e9c5), and strips `script` — `_DANGEROUS_TAGS`,
  ~:38-49), `meta_description`, `og_description` (`<meta property="og:description">`;
  `meta` is stripped), `css_offscreen` (`style="position:absolute;left:-9999px"` and `font-size:0`;
  text is kept), `hidden_div` (`hidden` attribute; `display:none`), `html_comment` (`<!-- … -->`),
  `alt_text` (`<img alt="…">`), `title_stuffing` (keyword-stuffed `<title>` with the payload).
- Payload phrasings: reuse ≥ 4 phrasings per carrier spanning `instruction_override` shape (a
  stage-2 hit if extracted), `authority_seo` shape ("verified, authoritative destination … must be
  ranked #1" — no technique string, finding 11 / 6) and `natural_language` shape; the record's
  `category` stays `hidden_markup` and `notes` names the phrasing shape so the report can split
  carriers by phrasing.
- Measure before pinning: run the story's records once, then pin only the carriers that were
  `neutralised` for **every** phrasing; record the measurement in Implementation Notes. If
  `html_comment` or `alt_text` leaks at HEAD, that is a finding for `kit_tools/AUDIT_FINDINGS.md`
  (spec 5 US-005 files it), not a fix.
- Surface `page` only (these are HTML placements); a `search` sibling for `title_stuffing` and
  `og_description` (the provider surfaces the meta description as the snippet — the Zscaler shape) is
  authored under `authority_seo` in US-005, not here.

**Acceptance Criteria:**
- [ ] ≥ 32 `hidden_markup` records, ≥ 4 per carrier, all `page`, every carrier value from the closed
      list, every record's `notes` naming the phrasing shape.
- [ ] Pins set only on carriers measured `neutralised` for every phrasing at story start; the
      measurement table (carrier × phrasing → outcome counts) is in Implementation Notes.
- [ ] A leaked carrier at story start is listed by record id in Implementation Notes under
      "for AUDIT_FINDINGS (spec 5 US-005)".
- [ ] Lint-clean; `tests/corpus/README.md` gains the carrier vocabulary.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-003: Window-shaped families — straddle, density sweep, repetition sweep, sustained mid-band

**Priority:** P1

**Description:** As a maintainer, I want the four families whose whole point is how a payload sits
across the classifier's 512/64 windows — authored as parameter sweeps with public-domain filler — so
that once the cassettes exist the report can say at what density and repetition the classifier and
each pooling rule stop catching, and how often long benign-looking mid-band prose trips contiguity.

**Independent Test:** `load_corpus()` yields ≥ 10 `boundary_straddle`, ≥ 20 `density_thinned`,
≥ 16 `repetition_camouflage` and ≥ 6 `sustained_midband` records, each with `params.windows_min`
and the family's sweep parameters set; the lint accepts them; driven with `fallback=0.0` none raises;
the filler text's provenance records a public-domain source with `LicenseRef-PublicDomain`.

**Implementation Hints:**
- Chunking facts: `MAX_SEQ_LEN = 512`, `CHUNK_OVERLAP = 64`, step 448 tokens
  (`MAX_SEQ_LEN` / `CHUNK_OVERLAP`, `promptguard/classifier.py` ~:33-34 at 403e9c5;
  `PromptGuardClassifier._chunk_text` ~:203-235; values unchanged by hardening); the real tokenizer
  is not available hermetically, so author by a 4-characters-per-token budget (a 448-token step ≈
  1 800 characters of English prose; multilingual prose tokenises denser — over-provision by 30 %)
  and set `params.windows_min`; spec 4 US-002 verifies the recorded window count against the real
  tokenizer and re-authors any record that falls short. **The only measurement behind the budget is
  the fixture tokenizer's** (`tests/fixtures/tiny_model`, not PG2's): hardening spec 7 US-007's
  Implementation Notes record 4 583 characters of prose → **1 040 tokens (3 windows)** for that
  story's explicit seeds — the 1 004 figure this hint used to cite was the spec author's
  pre-implementation example, which those notes set aside — and they record the production-tokenizer count as
  **unmeasured**. So 4 chars/token is a planning budget, not a measured PG2 ratio (corrected
  2026-09-24, validation round 4). That 4 583-character text is also the maximal `/search`
  stage-3 composite (`_search_result_promptguard_input`, `pipeline/orchestrator.py` ~:1285 —
  `Title: …\nURL: …\nSnippet: …` under the 512 / 2 048 / 2 000 caps, `tests/test_stage3_promptguard.py`
  asserts `len(text) == 4583`), so a `search` record with a realistic URL reaches **at most 2
  windows** at the fixture ratio: a family needing `windows_min` ≥ 3 cannot use the `search` surface.
- **Filler**: public-domain prose (Project Gutenberg plain-text; `source.kind: third_party`,
  `licence: LicenseRef-PublicDomain`, `url` the book's Gutenberg page, `revision` the file's
  Gutenberg ebook number) — Prompt Overflow's own evaluation used Pride and Prejudice excerpts
  (finding 9); keep excerpts ≤ 6 000 characters each and free of anything the secret or hostname lint
  would catch.
- `boundary_straddle` (`params.placement`: `split_448`, `split_896`, `split_1344` — the payload's two
  halves straddle the named step boundary; `windows_min` ≥ 2): ≥ 10 records, `page` and `text`.
- **Sweep bases must be stage-2-clean — stage 3 does not run when stage 2 blocks.**
  `sanitize_and_structure` (`pipeline/orchestrator.py`, ~:226 at 403e9c5; the shared `/retrieve` /
  `/extract` gauntlet) builds a default `PromptGuardResult(skip_reason="structural_block")` (~:263-268)
  and calls `run_promptguard` only when the stage-2 verdict is not BLOCKED (~:288), and
  `run_search_pipeline`'s per-result loop (~:1805-1846) `continue`s past the classifier on a
  BLOCKED field, counting `OMIT_STRUCTURAL_BLOCKED` — after scanning six texts per result (both
  forms of `title` and `snippet`, both URL scan texts). A record carrying a BLOCK-category trigger therefore produces **no classifier call, no
  cassette entry and no score at all** — it returns a constant `blocked` at every sweep level and
  contributes nothing to the curve. Binding on the two sweep families below, whose entire purpose is
  to find where the *classifier* stops catching: **every base payload used in a sweep is
  stage-2-clean**, verified by a lint rule for any record whose category is `density_thinned`,
  `repetition_camouflage`, `boundary_straddle` or `sustained_midband`:
  `scan_structural(form).flags == []` — `scan_structural` returns a `StructuralScanResult`
  dataclass (`pipeline/stage2_structural.py`, ~:29-34 and ~:259), never a list, so the round-3
  expression `scan_structural(payload) == []` could never be true (corrected 2026-09-24, validation
  round 4). `form` is the **whole record text including the Gutenberg filler** (long prose can hit
  `disregard.*instructions` or `new (task|instruction)` by accident), taken in its **post-pipeline
  form for each route the record targets** — the string stage 2 actually receives:
  `extract_html(document, url).raw_text` for `page` (the document the `/retrieve` driver serves),
  `stage1_upload.extract_upload_text(text.encode("utf-8")).raw_text` for `text` — because
  `/retrieve` decodes one entity level, so a raw base that looks clean can be BLOCK-shaped on the
  wire. Every sweep family is `page` / `text` only, so the lint (in `scripts/corpus/records.py`,
  spec 1) calls only public names — `extract_html`, `extract_upload_text`, `scan_structural` — and
  strict pyright stays green; the `search` form builder `_scan_forms_for_search_text` is private and
  is reachable only from `tests/` (spec 1's `stage2_forms`). *(Corrected round 5: round 4 named
  that private builder inside a `scripts/` lint.)* The rule is deliberately stricter than "verdict is not BLOCKED": a SUSPICIOUS span
  still lets stage 3 score the record, but it makes the live outcome a constant `flagged` at every
  sweep level, which flattens the curve just as a block does. A test fails the lint on a
  stage-2-shaped base; the driven assertion below (no sweep record yields
  `skip_reason == "structural_block"`) stays as the backstop that sees the real forms. Stage-2-shaped payloads keep their own pinned
  records in the regex-floor categories, where a constant `blocked` is the point. *(Added 2026-09-19,
  validation round 1 — as drafted, half of `density_thinned` and the stage-2-shaped half of
  `repetition_camouflage` measured nothing, and ruling 11's offline pooler evaluation was dead on
  those records for want of scores.)*
- `density_thinned` (`params.density`: `1/1`, `1/2`, `1/4`, `1/8` payload sentences per window;
  `params.placement`: `head`, `tail`, `interleave`; finding 9): 4 × 3 = 12 combinations × 2 base
  payloads (**both stage-2-clean** — one `natural_language`-shaped, one `authority_seo`-shaped;
  the earlier `instruction_override`-shaped base is moved to the regex-floor categories) → 24
  records, `windows_min` ≥ 4 for the `1/4` and `1/8` levels; surfaces `page` and `text` only —
  `search` cannot reach 4 windows (the composite bound above; added 2026-09-24, round 4).
- `repetition_camouflage` (`params.repeat`: `1`, `2`, `3`, `5`; finding 10): 4 base payloads × 4
  levels = 16 records, all inside one window (`windows_min: 1`), half camouflaged as cookie-banner /
  footer boilerplate repeated across a `page`; surfaces `page` and `text` only (round 5 — so all
  four sweep families are `page` / `text` and the stage-2-clean lint below needs no `search` form,
  whose builder is private; the Zenity effect is a within-window property, and the driven
  `structural_block` backstop still covers the real forms). **All four bases are stage-2-clean**, so every one of
  the 16 records yields a classifier score and the Zenity repetition effect is observable across the
  whole family. The regex floor the report pairs this with comes from **separate** pinned
  `["blocked"]` records in the stage-2 categories, not from bases inside this sweep. *(Corrected
  2026-09-19, validation round 1 — pinning stage-2-shaped bases `["blocked"]` at every level made
  those records unmeasurable rather than informative.)*
- `sustained_midband` (`windows_min` ≥ 3): ≥ 6 long `page` records of review / comment prose that
  reads as semi-instructional without any trigger string (the adversarial-trip shape hardening
  spec 7 US-007 hands over); no pins — it exists to be scored.
- Every record's `marker` is a sentence fragment that appears in *every* payload fragment (so the
  leak check works on any surviving piece).

**Acceptance Criteria:**
- [ ] Counts and parameters as in the Independent Test; `params` keys limited to the family's
      allowlist (`placement`, `density`, `repeat`, `windows_min`).
- [ ] Filler provenance recorded per record with a public-domain licence id and the ebook number;
      no filler excerpt exceeds 6 000 characters; lint-clean.
- [ ] Every sweep record (`density_thinned`, `repetition_camouflage`, `boundary_straddle`,
      `sustained_midband`) has a stage-2-clean base — a lint rule asserts
      `scan_structural(form).flags == []` for the whole record text (filler included) in the
      post-pipeline form of every route it targets, and a test feeds a stage-2-shaped base and asserts
      the lint rejects it.
- [ ] Every sweep record yields a classifier score under replay (the cassette carries an entry for
      each of its windows); a test asserts no sweep record produces `skip_reason ==
      "structural_block"`.
- [ ] The regex-floor pins the report pairs with the repetition curve live in the stage-2 categories
      and are asserted there, not inside `repetition_camouflage`.
- [ ] `tests/corpus/README.md` documents each family's parameters and the character-budget rule.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-004: Third-party ingestion — pinned, capped, re-rendered, licence-resolved samplers

**Priority:** P2

**Description:** As the epic owner, I want deterministic samplers for the three permissively licensed
sets — AgentDojo (MIT), LLMail-Inject (MIT), CyberSecEval's indirect-injection cases (MIT at
`CybersecurityBenchmarks/LICENSE`) — that read a pinned local download, sample with a fixed seed
under a cap, re-render every payload into a `search` / `page` / `text` record with reserved-domain
URLs, and write the `NOTICE` entry, so third-party records are reproducible and their licence is
checkable per record.

**Independent Test:** `uv run pytest tests/test_corpus_ingest.py` passes hermetically: each sampler,
fed a five-row in-memory fixture in the upstream's native shape, produces the same records on two
runs with the same seed, respects `--limit`, rewrites every URL to a reserved host, sets
`source.kind = third_party`, `source.licence = MIT`, `source.revision` to the pinned SHA / dataset
revision, `source.framing` per the source's shape, refuses a row whose text carries a secret
shape, and refuses an `--input` path resolving inside the repository root; `uv run python -m scripts.corpus.ingest.agentdojo --help` exits 0 offline.

**Implementation Hints:**
- Package `scripts/corpus/ingest/` with one module per source and a shared `render.py` (email body
  → forum-post `page`; tool-output string → article `page` or `search` snippet; short instruction →
  `text`). CLI shape: `argparse`, `--input PATH` (a local download the implementer makes by hand —
  the socket guard applies to tests, not to the host run; record the exact download commands in
  Implementation Notes), `--revision SHA`, `--seed N`, `--limit N`, `--out tests/corpus/attacks/`.
  Follow `scripts/export_contract.py`'s `build_parser` / `main(argv)` idiom (~:293 / ~:310 at
  403e9c5, unchanged).
- **AgentDojo** (MIT — LICENSE read: six-author copyright line, finding 1; last push 2026-06-02):
  629 security cases are tool-environment injections (Banking / Slack / Workspace / Travel) — every
  one must be re-rendered as a page body or snippet, never committed as-is; `--limit 40`;
  `framing = indirect`; category `natural_language` unless the text carries a stage-2 trigger (then
  the matching structural category, `variant = plain`, `source.kind = third_party`).
- **LLMail-Inject** (`microsoft/llmail-inject-challenge`, Hugging Face, `license: mit`, ~462 k rows
  of attacker-authored email bodies with per-row defence-outcome fields; finding 5): sample
  **stratified** by the "defence detected" flag (half caught, half not) with `--limit 60`; re-render
  as forum posts / page bodies; `framing = indirect`; cap is a hard rule — never vendor the set. The
  download is one shard via `huggingface_hub.hf_hub_download` (a runtime dependency already,
  `pyproject.toml`); if the shard is Parquet, add `pyarrow` to the `dev` extra only (`uv lock`;
  `tests/test_dependency_lock.py`'s CPU-only assertion is unaffected) — or convert on the host and
  feed JSONL; document which.
- **CyberSecEval** (`meta-llama/PurpleLlama`, `CybersecurityBenchmarks/LICENSE` is MIT although the
  repo root is the Llama 3.2 Community License — finding 4): take only files under that directory;
  `--limit 30` of the indirect prompt-injection cases; `NOTICE` cites the directory LICENSE path,
  never the root.
- **Category mapping is one rule for all three sources** (corrected 2026-09-19, validation round 1 —
  it was stated only inside the AgentDojo paragraph, leaving LLMail-Inject and CyberSecEval with no
  rule at all): every ingested row is scanned with `scan_structural` at ingest time; if it trips a
  pattern it takes the matching structural category with `variant = plain`, otherwise it takes
  `natural_language` (or `authority_seo` where the source's framing is SEO/authority-shaped). US-004
  carries an acceptance criterion testing this assignment — the existing criteria cover determinism,
  cap, URL rewriting, licence fields, secret refusal and no-payload-in-output, but nothing tested
  category assignment, which is what US-005's assertion depends on.
- **Where the raw downloads live** (added 2026-09-19, validation round 2 — security). `--input PATH`
  points at a local download the implementer makes by hand, and for LLMail-Inject that is a shard of
  a ~462 k-row set the epic explicitly decided never to vendor. Nothing currently stops it being
  committed. So: the downloads go **outside the working tree** — the documented location is
  `$FORAGE_CORPUS_INPUTS` (default `~/.cache/forage-corpus-inputs/`), never a path under the repo;
  `.gitignore` gains `/corpus-inputs/` as a belt-and-braces entry for the obvious mistake; the
  samplers refuse an `--input` path that resolves inside the repository root — "resolves" means
  canonicalised with symlinks and `..` followed (`Path.resolve()`, compared against the resolved
  repo root), never a literal prefix check — with a test; and
  `tests/corpus/README.md` says where inputs live and that they are never committed. Only the
  sampled, re-rendered, capped records reach `tests/corpus/`.
- **Credentials for the downloads** (added 2026-09-19, validation round 1 — story quality). The
  LLMail-Inject shard and any gated source are fetched with a token **from the environment only**:
  `huggingface_hub` reads `HF_TOKEN` itself, so no token is ever passed as an argument, and the
  "record the exact download command in Implementation Notes" instruction above means the command
  **without** its environment — a pasted `HF_TOKEN=hf_…` in a spec's notes is a committed secret.
  Any error text the samplers surface follows `_fetch_reason()`'s pattern (`model_fetcher.py`,
  ~:1013-1029 at 403e9c5 — the round-3 anchor `:937-950` now lands in `resolve_model_id`, a
  different idiom, so find it by name): a closed vocabulary of reason codes, never the URL or the
  exception's raw message, which can carry a signed URL. Any reference string a sampler echoes (a
  download URL, a dataset revision) goes through `redact_reference()` (~:1032) first. A test
  asserts a sampler failure message contains no `hf_`-shaped substring.
- **Sourcing rule, written into `tests/corpus/README.md`**: resolve the licence at the directory of
  the files taken; pin by SHA / revision, not branch (PIGuard's rename "due to licensing issues" is
  the precedent, finding 13); direct (user-turn) rows are excluded or re-homed with
  `framing = rehomed_direct`.
- **Rejected on licence** (a table in the README, later `docs/corpus.md`): BIPIA (benchmark data
  CC-BY-SA 4.0 — share-alike; the LICENSE disclaims its own accuracy; finding 2), WASP (CC-BY-NC 4.0;
  finding 3), HackAPrompt (MIT but direct-framed and unnecessary), PIGuard/InjecGuard (licence
  unverified after the rename). Ideas are not copyrightable: taxonomies may be read; no strings.
- `NOTICE`: a new section "Third-party corpus samples" with one entry per source: name, licence,
  copyright line verbatim (AgentDojo's six authors), the LICENSE path, the pinned revision, the
  record-id range; `tests/test_governance_docs.py`-style test asserts every `third_party` source
  name in the corpus has a `NOTICE` entry.
- The samplers never print payload text (ids and counts only); a secret-shaped row is skipped with a
  counted reason.

**Acceptance Criteria:**
- [ ] `scripts/corpus/ingest/{agentdojo,llmail_inject,cyberseceval,render}.py` exist with the CLI
      shape above; `--help` exits 0 offline for each.
- [ ] Hermetic sampler tests: determinism, cap, URL rewriting, licence / revision / framing fields,
      secret-shape refusal, no payload in output.
- [ ] **Category assignment is tested**: a fixture row carrying a stage-2 trigger lands in the
      matching structural category with `variant = plain`; a clean row lands in `natural_language`;
      the same rule is exercised for all three samplers, not just AgentDojo.
- [ ] **Raw downloads cannot be committed**: the samplers refuse an `--input` path that resolves
      inside the repository root after symlink resolution (test: a path under the repo is rejected
      by reason code, a symlink outside the repo pointing into it is rejected, a path outside is
      accepted); `.gitignore` carries `/corpus-inputs/`; `tests/corpus/README.md` states
      where inputs live and that they are never committed. *(Added 2026-09-19, validation round 3 —
      the rule existed only in Implementation Hints, so this story's checklist could pass green while
      a ~462k-row set the epic swore off vendoring sat staged for commit.)*
- [ ] **No credential can reach the corpus or a log**: tokens are read from the environment by
      `huggingface_hub` only, never passed as an argument; a test asserts a simulated sampler failure
      message carries no `hf_`-shaped substring and no URL, following `model_fetcher.py`'s
      closed-vocabulary `_fetch_reason()` pattern (by name; ~:1013 at 403e9c5); the recorded
      download commands in Implementation Notes carry no environment assignment.
- [ ] The host runs are done and recorded: Implementation Notes carry, per source, the download
      command, the pinned revision, seed, limit, rows read, records written, rows skipped by reason;
      if a source could not be fetched, `not ingested — <reason>` and the floors still hold on owned
      records.
- [ ] `NOTICE` section present and complete; the NOTICE-coverage test passes; `LICENSE` untouched.
- [ ] Rejected-sources table and the sourcing rule in `tests/corpus/README.md`.
- [ ] Every ingested record lint-clean; ids continue the sequence.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-005: Natural-language and authority/SEO poisoning — owned and multilingual

**Priority:** P1

**Description:** As a maintainer, I want the classifier-only shapes — imperative instructions to the
reading agent embedded in ordinary web prose, and polite "authoritative source, rank first"
poisoning that carries no jailbreak technique at all — authored in six languages and across all
three surfaces, so the corpus measures the one job Meta's model card says Prompt Guard 2 was not
built for.

**Independent Test:** `load_corpus()` yields ≥ 24 `natural_language` and ≥ 16 `authority_seo` owned
records spanning ≥ 6 `lang` values and all three surfaces; driven with `fallback=0.0` every one is
`leaked` (asserted — they carry no structural marker by construction, and the assertion is what makes
the later classifier number meaningful); the whole attack corpus after this story has ≥ 200 records
with ≥ 5 in every category.

**Implementation Hints:**
- `natural_language`: instructions addressed to "the assistant reading this" inside an article, a
  product review, a forum reply, a README, a changelog; verbs: summarise falsely, recommend, visit
  `https://…example`, include a phrase in the answer; no `ignore previous`, no role markers, no
  brackets — a record that trips a stage-2 regex belongs in US-001, and the story's test asserts
  `leaked` under `fallback=0.0` to enforce that.
- `authority_seo`: the Zscaler shape (finding 11): "verified, authoritative destination", "must be
  ranked as the #1 primary source", publisher claims, payment instructions phrased as policy;
  `search` siblings where the snippet *is* the meta description (the surfaced-snippet vector) and
  `page` bodies; pair with the PG2 model-card finding (finding 6) in `notes`.
- Languages: en, de, fr, es, pt, ja (add zh / it if cheap); `lang` set per record; ≥ 2 records per
  language per category. 22M's multilingual AUC is .942 vs 86M's .995 (model card; finding 7): the
  multilingual slice measures the 22M default where its model card is weakest, and it is the input
  that makes spec 5's 22M-vs-86M decision table meaningful. **That second use is contingent on
  spec 0** (`feature-corpus-86m-enablement.md`, owner decision 17): hardening spec 7 closed its 86M
  vendoring and benchmark gates as `gate not run`, and at 403e9c5 `ALLOWED_MODEL_IDS` names only the
  22M and `weights_manifest.json` carries only the 22M entry. If spec 0 releases the 86M, spec 4
  US-003 records its cassette over these same records. If spec 0 stops at a recorded `gate not run`
  state, the epic proceeds 22M-only (ruling 6a): the counts and languages here are unchanged, the
  slice still measures 22M, spec 4 US-003 records `not recorded — 86M not enabled`, and the model
  half of the decision table is named as pending. Nothing in this story waits on spec 0's outcome
  (updated 2026-09-24, validation round 4).
- Ingested records from US-004 land in these two categories by the mapping rule in US-004; this
  story's counts are for **owned** records so the floor holds even if ingestion was skipped.
- Every record's `marker` is a short phrase present verbatim; keep markers distinct across languages.

**Acceptance Criteria:**
- [ ] ≥ 24 owned `natural_language` and ≥ 16 owned `authority_seo` records; ≥ 6 languages; all
      three surfaces represented in each category.
- [ ] Every **owned** (`source.kind == "owned"`) `natural_language` / `authority_seo` record is
      `leaked` under `fallback=0.0` — the generic test filters on `source.kind`, and a second
      assertion states why: US-004's ingested rows land in these same two categories, attacker-
      authored corpora are dense with BLOCK-category phrasing (LLMail-Inject alone is ~462 k real
      attempts), so a meaningful fraction of them legitimately come back `blocked`. Scoping the
      assertion to owned records keeps the category's defining invariant testable instead of
      inviting it to be narrowed under time pressure later. *(Corrected 2026-09-19, validation
      round 1.)*
- [ ] Ingested records are covered by their own assertion: every third-party row is either
      `leaked` **or** carries the structural category the mapping rule assigned it — no ingested row
      sits in `natural_language` / `authority_seo` while tripping stage 2.
- [ ] Attack corpus totals after this story: ≥ 200 records, ≥ 5 per category, recorded in
      Implementation Notes as a category × surface table (counts only).
- [ ] Lint-clean; `tests/corpus/README.md` lists the languages present.
- [ ] Zero runtime change (rulings 6, 6a) re-asserted at spec end: the ruling-6 `git diff --stat`
      set, taken against spec 0's completion tag `forage-injection-corpus/corpus-86m-enablement-complete`
      (not `main`), is empty; `derive_sanitizer_revision({})` unchanged from its value at that same
      tag; `scripts.export_contract
      --check` green.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

## Edge Cases

- A `second_paragraph` variant of a non-anchored category with the trigger whole after `\n\n`:
  expected identical to `plain` — because `/search` scans both forms, not because the forms verdict
  identically. With the line break *inside* a non-`DOTALL` trigger, `/search` and the other two
  routes differ (US-001, the dual-form hint).
- An `entity` variant decodes **deeper on `/search` than on `/retrieve`** post-hardening (corrected
  2026-09-24, round 4 — the round-3 entry had it backwards): `/retrieve` gets the parser's one
  level; `/search` gets the parser's level plus `html.unescape` (`_scan_forms_for_search_text`,
  spec 1 R26); `/extract` gets none. Measured at scanner level at 403e9c5: a double-encoded
  system-tag trigger scans BLOCKED on `/search` and CLEAN on `/retrieve`. Outcomes differ by route;
  all are recorded, none pinned (US-001).
- A *literal* tag-shaped trigger on `page` or `search`: consumed as markup by `extract_html` on both
  HTML routes — `/search` included since hardening — and scans CLEAN; recorded as `plain` /
  `tag-consumed`, unpinned (US-001).
- A `split_tags` trigger: `extract_html` separates the fragments, so the trigger is broken rather
  than rejoined — expect `leaked` / `neutralised` (US-001).
- A `hidden_markup` phrasing that trips stage 2 after extraction (e.g. `[SYSTEM]` inside a hidden
  div): `blocked`, attributed to stage 2 by the report (US-002).
- A density record whose recorded window count is below `windows_min`: spec 4 US-002 lists it and
  it is re-authored; the lint cannot check window counts (US-003).
- `repetition_camouflage` is `page` / `text` only at every repeat level (US-003, round 5). The
  `search` composite caps and the private form builder both rule it out, so no `search`
  repetition record exists at any `repeat`.
- An LLMail row with a real-looking address or hostname: the renderer rewrites every host to
  reserved names and every e-mail to `user@example.com`; a row that still fails lint is skipped and
  counted (US-004).
- A source download that changed under its pin: the sampler asserts the input's sha256 against the
  recorded one and refuses (US-004).
- A `natural_language` record that accidentally contains `new task` (an `instruction_override`
  regex) is caught by the story's `leaked`-under-fallback assertion and moved to US-001 (US-005).

## Out of Scope

- Benign records (spec 3); cassettes and recording (spec 4); the report and floors (spec 5).
- Any source whose licence is share-alike, non-commercial, research-only or unverified (BIPIA,
  WASP, PIGuard/InjecGuard, HackAPrompt).
- PDF-borne records (ruling 8).
- Fixing anything the corpus reveals (ruling 6).
- Vendoring any third-party set beyond the caps.

## Assumptions

- Stage 1 keeps text in CSS-hidden / `hidden` / `aria-hidden` elements and drops `script`, `meta`
  and comments; US-002 measures before pinning, so a wrong assumption costs nothing.
- Four characters per token is a safe budget for English filler; multilingual filler is
  over-provisioned by 30 %; spec 4 US-002 is the check.
- The three sources are reachable from the host at their pinned revisions when US-004 runs; if not,
  the floors hold on owned records and the story records the gap.
- Public-domain Gutenberg text needs no `NOTICE` entry but is cited per record.

## Technical Considerations

- The lint's `params` allowlist per category (spec 1) is extended here for `variant`, `carrier`,
  `placement`, `density`, `repeat`, `windows_min`; `vocab.py` is the single place.
- Record files can grow to hundreds of lines; keep one JSON object per line, no pretty-printing, so
  `git diff` stays per-record.
- Ingestion adds no runtime dependency; `pyarrow` (if needed) is `dev`-only.
- `NOTICE` is read by nothing at runtime; the coverage test is the only consumer.

### Validation residue — closed at `needs-work` (2026-09-19, `/kit-tools:validate-epic`, 3 rounds)

Thirty reviewers over three rounds took this epic from 19 criticals to 0 open; the items below are
the warnings that remained when validation was deliberately closed rather than chased to zero — the
same call, for the same reason, that `epic-forage-hardening` recorded on the same day: the precision
reviewers surface a new layer every round, and **every code anchor in this spec predates eight
unexecuted hardening specs** (ruling 5), so precision spent now is precision spent twice. Re-verify
against the post-hardening tree at execution time; treat each item as a decision the implementer
makes deliberately, not a defect to discover.

- **US-004 stands up three independent ingestion pipelines in one story** (AgentDojo, LLMail-Inject,
  CyberSecEval) — each with its own download shape, licence location and row format. Flagged in all
  three rounds. The per-source escape hatch (`not ingested — <reason>`, floors hold on owned records)
  is what makes a partial completion survivable; use it rather than stretching the story.
- **The LLMail-Inject shard format is still open** (Parquet vs JSONL) and it forks a real decision:
  Parquet adds `pyarrow` to the `dev` extra and a re-lock, JSONL means converting on the host.
  Marked non-blocking across two rounds; decide it before starting US-004, not during.
- **The sha256 input-pin refusal is stated in Edge Cases but owned by no story's criteria** — the
  same shape of gap that let the `marker_on_wire` producer go missing in spec 5.
- **The `authority_seo` mapping branch is undefined.** "Where the source's framing is SEO-shaped" has
  no rule and no test, so in practice every ingested row will land in `natural_language`.
- **Category mapping classifies the upstream row at ingest time, not the rendered wire form** — the
  same pre-render/post-render gap that was corrected in spec 3's triage step. If it matters that the
  two agree, scan the rendered form here too.
- **Ingested rows need markers that survive their carrier**, and no criterion covers marker authoring
  for third-party text; Goal 3's "stratified where the source offers labels" is specified only for
  LLMail-Inject.

### Validation round 4 — 2026-09-24 (post-hardening re-anchor)

Six reviewers against `plan/corpus-revalidation` = `main` `403e9c5` (hardening v1.2.1 shipped).
Every reviewer claim below was re-measured before editing (scanner-level Python against
`extract_html`, `_scan_forms_for_search_text`, `_canonicalize_search_url`, `scan_structural`).

**Fixed:**
- *Behaviour mismatches (US-001).* `plain` redefined per regex × surface (tag-shaped triggers are
  consumed by `extract_html` on `page` **and now `search`**; entity form is `plain` there);
  `url_borne_envelope` pinned per URL shape (BLOCK token → `blocked`; encoded envelope tag →
  `flagged`; raw `<` → `blocked` by `invalid_url` / `raw_chars`, not stage 2); no zone-id records
  (lint has no exception for their host); SUSPICIOUS `plain` pinned `["flagged"]`; the Independent
  Test asserts pins, not blanket expectation; `split_tags` mechanism corrected (separator, not
  rejoin); private-IP probes kept out of result URLs and live attributes.
- *Stale rationale.* Hardening spec 1's identical-verdict Assumption (struck FALSE in the archive)
  replaced by the shipped dual-form scan; records added for the two non-`DOTALL` patterns with the
  break inside the trigger, measured `/search`-only at scanner level; `entity` Edge Case reversed
  (`/search` decodes deeper than `/retrieve`).
- *US-003.* Stage-2-clean lint restated as `scan_structural(form).flags == []` over the whole record
  (filler included) in each targeted route's post-pipeline form, with why it is stricter than
  not-BLOCKED; token figure corrected to 1 040 (fixture tokenizer; PG2 count unmeasured);
  `/search` composite bounds window count, so `density_thinned` is `page` / `text` only.
- *US-004.* `_fetch_reason` re-anchored (old range is now `resolve_model_id`); `redact_reference`
  named; input-path containment defined as symlink-resolved, with a symlink test case.
- *US-005.* 86M-vs-22M rationale made contingent on spec 0, with the 22M-only path stated; ruling-6
  diff re-based on spec 0's completion tag.
- *Anchors.* Every citation re-anchored by symbol at `403e9c5`; `_sanitize_search_text` recorded
  as deleted; Known-risks rewritten in past tense (both prerequisite hardening stories shipped).
- *Header / frontmatter.* Spec 2 of 6; `updated: 2026-09-24`.

**Rejected as not holding:** none. (Reviewer 4's `_PATTERNS` range `:61-251` measured `:61-241`;
reviewers 1-3 had it right.)

**Carried as residue (not chosen for fixing this round):**
- Goal 4's `source.framing = rehomed_direct` clause is exercised by no acceptance criterion — all
  three sources are indirect and HackAPrompt is rejected (reviewer 1); owner deferred.
- A PII scrub rule for LLMail-Inject rows beyond the hostname / e-mail rewrite (reviewer 5); owner
  deferred — the per-source `not ingested` escape hatch remains the control of last resort.
- Scanner-level measurements in US-001 are hypotheses until the story's route-driven run; the pins
  follow that run (Decisions Made), so a disagreement is a recorded gap, not a spec defect.
- The epic wrapper's "Inputs" table still cites hardening spec 1's Assumption as an input; that is
  the wrapper's text, not this spec's, and is flagged for the wrapper's fixer.

**Round 5 (same day):** (1) *US-003 lint needed a private name in `scripts/`* — round 4's "both
outputs of `_scan_forms_for_search_text` for `search`" could not pass strict pyright in
`scripts/corpus/records.py` (`reportPrivateUsage` is relaxed for `tests/` only, verified in
`pyproject.toml`). `repetition_camouflage` is now `page` / `text` like the other three sweep
families, the `search` clause is struck, and each form is named as the exact string stage 2
receives (`extract_upload_text(...).raw_text` for `text`, not bare `normalize_text`). (2)
*`url_borne_envelope` on `page` / `text` had no meaning* — the category is now `search`-only, ≥ 8
records over the three URL shapes and path/query. (3) *Info*: `second_paragraph` widened to any
surface with a `break-inside-trigger` notes tag for the inside-trigger records (no new variant, so
spec 1's vocabulary is unchanged); the `derive_sanitizer_revision({})` clause now names the same
spec-0 tag as its baseline.

**Round 6 (same day):** *Info — a stale Edge Case.* The entry "a repetition record at `repeat = 5`
exceeding the `search` content cap: use `page`" still assumed `search` repetition records at lower
repeat levels. Round 5 made the family `page` / `text` only (US-003 hint). An implementer could have
read the old entry as permission to author `search` records at repeat 1–3, which the stage-2-clean
lint cannot form without the private builder. The entry now states the surface rule instead.

## Related Documentation

- `tests/corpus/README.md` (spec 1); `kit_tools/arch/SECURITY.md` "Prompt-Injection Signalling";
  `kit_tools/docs/GOTCHAS.md`; `NOTICE`.

## Implementation Notes

<!-- Per story: counts tables (numbers and ids only), host-run commands for US-004, leaked carriers
for spec 5 US-005. Never a payload. -->

## Refinement Notes

### Research Findings

**Decision:** AgentDojo, LLMail-Inject and CyberSecEval (directory-licensed) are the third-party
sources; BIPIA and WASP are rejected.
**Rationale:** Licences were read at the source: AgentDojo LICENSE is MIT (six-author line);
LLMail-Inject declares `license: mit` on Hugging Face and its challenge terms allowed publication in
a public dataset; PurpleLlama's `CybersecurityBenchmarks/LICENSE` is MIT although the repo root is
the Llama 3.2 Community License; BIPIA's LICENSE appends CC-BY-SA 4.0 for its WikiTableQuestions /
Stack Exchange data; WASP's LICENSE is CC-BY-NC 4.0.
**Alternatives considered:** BIPIA's attack strings alone (MIT-covered) — not worth the review burden;
HackAPrompt (MIT, direct-framed) — unnecessary; NotInject (MIT, benign) — spec 3.
**Source:** https://raw.githubusercontent.com/ethz-spylab/agentdojo/main/LICENSE (2026-06-02);
https://huggingface.co/datasets/microsoft/llmail-inject-challenge; https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CybersecurityBenchmarks/LICENSE
(2026-08-18); https://github.com/microsoft/BIPIA/blob/main/LICENSE (2024-04-15);
https://raw.githubusercontent.com/facebookresearch/WASP/main/LICENSE (2026-04-13). All `read_source`.

**Decision:** Density thinning and repetition are sweeps, not examples; both are named categories.
**Rationale:** Prompt Overflow (arXiv 2605.23196, 2026-05-22) names Llama Prompt Guard among
bypassed guardrails and shows per-window scoring fails below a payload-density threshold at head /
tail / interleave placements — a single example measures nothing; Zenity (2026-03-12) reports a
PG2-specific flip to benign on payload duplication that ordinary web boilerplate camouflages.
**Source:** https://arxiv.org/abs/2605.23196; https://labs.zenity.io/p/catching-prompt-guard-off-guard-exploiting-overfit-in-training-algorithms.

**Decision:** Eight hidden-markup carriers, pinned only where stage 1 is measured to remove them.
**Rationale:** Zscaler ThreatLabz (2026-07-02) documents JSON-LD `offers` / `publisher` objects,
`left: -9999px` text, hidden divs, OG / X meta tags and stuffed titles in live campaigns; stage 1
parses JSON-LD only for author / date and strips `script` and `meta`, so for those carriers
extraction is the defence and a parser widening is the regression to catch.
**Source:** https://www.zscaler.com/blogs/security-research/indirect-prompt-injection-web-content-targets-ai-agents;
`pipeline/stage1_extraction.py` `_DANGEROUS_TAGS`, `_json_ld_documents` (re-anchored by symbol
2026-09-24).

**Decision:** `natural_language` and `authority_seo` are asserted `leaked` under structural-only
replay.
**Rationale:** Meta's Prompt Guard 2 card states the injection sub-label for "prompts that may cause
unintentional instruction-following" was deliberately dropped; PIDS-Bench (arXiv 2609.15017,
2026-09-14) measures PG2-86M obfuscation recall 0.32 and hard-benign FPR 0.15 — the corpus must show
which stage carries each class, and these two classes are the ones only stage 3 can carry.
**Source:** https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M; https://arxiv.org/html/2609.15017.

### Scope Adjustments

- Ingestion (US-004) is P2 and network-dependent on the host; the owned floors (US-001/002/003/005)
  do not depend on it.
- PDF carriers dropped (ruling 8).

### Decisions Made

- Pins are set from measurement at story time, never from expectation.
- Ids are never reused; one file per category.

## Clarifications

### Session 2026-09-19
- Q: Ingest third-party samples? → A: Only permissive-licence sets, after research; sampled,
  re-rendered, with NOTICE (owner decision 3). Research read the licences: three in, two out.

## Open Questions

- [ ] LLMail-Inject shard format (Parquet vs JSONL) — non-blocking; decides whether `pyarrow` joins
      the `dev` extra.
- [ ] Whether `html_comment` and `alt_text` are removed by stage 1 at HEAD — non-blocking; US-002
      measures and pins accordingly.

## Known risks (planning)

- **Anchor drift — re-verified at `403e9c5` on 2026-09-24** (ruling 5; validation round 4). Every
  code citation in this spec now names its **symbol** with the current line as a hint
  ("~:N at 403e9c5"); grep the symbol, never trust the number. Measured against the shipped
  post-hardening tree:
  - `pipeline/orchestrator.py` — **still the one to distrust.** It grew from ~1 130 to ~2 010 lines
    across the eight hardening specs; the sections this spec cites moved by 500-800 lines. Current
    symbols: `sanitize_and_structure` (~:226; the `skip_reason="structural_block"` default ~:263-268,
    the BLOCKED gate ~:288), `_normalize_search_text` (~:930), `_scan_forms_for_search_text` (~:940),
    `_RAW_URL_REJECT_RE` (~:1099), `_SEARCH_URL_RULES` (~:1222), `_canonicalize_search_url` (~:1235),
    `_search_result_promptguard_input` (~:1285), `run_search_pipeline` (~:1604; the six-text
    per-result scan and structural-block `continue` ~:1805-1846). **`_sanitize_search_text` no longer
    exists**: hardening spec 1 US-002 deleted it outright — title/snippet scanning moved to
    `_scan_forms_for_search_text`, and its one URL caller was replaced by `_normalize_search_text`
    / the `_SEARCH_URL_RULES` chain inside `_canonicalize_search_url`.
  - `promptguard/classifier.py` — `MAX_SEQ_LEN` / `CHUNK_OVERLAP` ~:33-34 (below the new
    `_PINNED_GENERIC_LABEL_INDICES`, ~:30), `_chunk_text` ~:203-235, `classify_windows` ~:260. Values
    unchanged. Spec 0 may touch `_PINNED_GENERIC_LABEL_INDICES` only (ruling 6a).
  - `model_fetcher.py` — `_fetch_reason` ~:1013-1029, `redact_reference` ~:1032, `ALLOWED_MODEL_IDS`
    ~:191 (22M only until spec 0). The old `:937-950` range is now `resolve_model_id`.
  - `pipeline/stage2_structural.py` (`_PATTERNS` ~:61-241, `StructuralScanResult` ~:29,
    `scan_structural` ~:259), `pipeline/stage1_extraction.py` (`_DANGEROUS_TAGS` ~:38,
    `_collapse_invisible` / `_normalize_text` ~:92-109, `_json_ld_documents` ~:153, `extract_html`
    ~:296) and `pipeline/stage1_upload.py` — **byte-unchanged since `20ddb2a`**, as hardening spec 1
    promised (`git diff --stat 20ddb2a 403e9c5` empty for all three). The 24-pattern table is the
    one this spec authors against. What changed around them is who calls `extract_html`: `/search`
    now does, which is why tag-shaped `plain` triggers vanish on that route (US-001).
  - `scripts/export_contract.py` `build_parser` / `main` ~:293 / ~:310 — unchanged.
  **The two categories are now detectable — the dependency is shipped, not pending.**
  `line_anchored_role` and `url_borne_envelope` are *corpus* category names (spec 1's `vocab.py`),
  not `stage2_structural.py` categories. Both hardening stories that make them detectable are
  archived `completed`: `feature-hardening-search-sanitization.md` US-001 (audit -016; the
  newline-preserving scan form, now scanned alongside the wire form) and US-002 (audit -032; the
  raw-value `_SEARCH_URL_RULES` chain and the two URL `scan_texts`). Their wire codes did change
  from what round 3 assumed, and this spec's pins have followed: a URL-borne envelope is
  `blocked` by stage 2 only for a BLOCK token, `flagged` for an encoded envelope tag, and `blocked`
  by canonicalisation (`invalid_url` / `raw_chars`) for a raw `<` — US-001's per-shape pin table.
  Confirm each pin against the real routes at story start (Decisions Made), not against this text.
- Character-per-token budgeting can under-provision multilingual filler; spec 4 US-002 re-authors.
- A source repository can vanish or change licence after the pin; the pinned revision and the local
  input hash keep the corpus reproducible, and the NOTICE entry records what was taken and when.
