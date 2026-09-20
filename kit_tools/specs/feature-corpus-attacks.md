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
updated: 2026-09-19
---

# Feature Spec: Attack Corpus — Sixteen Categories, Three Surfaces, Permissive Third-Party Samples

> Spec 2 of 5 in `epic-forage-injection-corpus` (rulings 3, 8, 14). Builds on spec 1's record
> format, lint and drivers. Every record here is data (ruling 8): this spec names categories,
> counts, variants and sources — never a payload.

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
driven with `ReplayClassifier(fallback=0.0)`, every `plain` variant of a BLOCK category is `blocked`
and every `plain` variant of a SUSPICIOUS category is `flagged`, while the obfuscated variants'
outcomes are recorded (not asserted) in the story's Implementation Notes as a count per variant.

**Implementation Hints:**
- Pattern list: `pipeline/stage2_structural.py:62-248` (six `instruction_override` regexes, six
  `authority_impersonation` including the three line-anchored `^System:` / `^assistant:` / `^POPPY:`
  with `re.MULTILINE`, three `encoded_payload`, four `prompt_boundary`, `suspicious_url` incl. the
  private-IP `href`/`src` regex, `exfil_beacon` markdown-image-with-template, `envelope_breakout` with
  the entity-encoded `<` forms). Author each record to one regex and name it in `params.variant`'s
  companion `notes` (e.g. `regex: ignore-previous`).
- **Variants** (`params.variant`, closed list in `vocab.py`): `plain`, `case`, `entity` (HTML entities
  for `<`, `[`, backticks), `zwsp` (U+200B / U+200C inside the trigger token), `split_tags`
  (`<b>ig</b>nore` in `page` HTML), `confusable` (Cyrillic а/е/о substituted in a trigger word),
  `second_paragraph` (`search` content with the trigger after `\n\n`), `url_query`, `url_path`,
  `title_field`. Not every variant applies to every category (e.g. `url_*` for `envelope_breakout`,
  `suspicious_url`, `url_borne_envelope`); the lint's `params` allowlist admits `variant` for all nine.
- Surfaces: ≥ 3 `search`, ≥ 3 `page`, ≥ 2 `text` per category so each route sees each family.
- **Pins**: only `plain` variants of the three BLOCK categories and of `line_anchored_role` /
  `url_borne_envelope` are `pinned: ["blocked"]` (`pinned_reason: "stage-2 regex, deterministic"` /
  the audit id); everything else unpinned — the obfuscation reach is the measurement.
- Hardening spec 1's `Assumptions` says non-line-anchored patterns verdict identically on collapsed
  and newline-preserving forms; the `second_paragraph` variants of the non-anchored categories test
  that assumption on the wire (expect `blocked` / `flagged` as `plain`).
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
  landscape finding 11; stage 1 parses JSON-LD only for author/date, `pipeline/stage1_extraction.py:154-165`,
  and strips `script` — `:38-49`), `meta_description`, `og_description` (`<meta property="og:description">`;
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
  (`promptguard/classifier.py:23-25`, `_chunk_text :133-148`); the real tokenizer is not available
  hermetically, so author by a 4-characters-per-token budget (a 448-token step ≈ 1 800 characters of
  English prose; multilingual prose tokenises denser — over-provision by 30 %) and set
  `params.windows_min`; spec 4 US-002 verifies the recorded window count and re-authors any record
  that falls short. Hardening spec 7's measurement: 4 583 characters of prose → 1 004 tokens (3
  windows) against the fixture tokenizer.
- **Filler**: public-domain prose (Project Gutenberg plain-text; `source.kind: third_party`,
  `licence: LicenseRef-PublicDomain`, `url` the book's Gutenberg page, `revision` the file's
  Gutenberg ebook number) — Prompt Overflow's own evaluation used Pride and Prejudice excerpts
  (finding 9); keep excerpts ≤ 6 000 characters each and free of anything the secret or hostname lint
  would catch.
- `boundary_straddle` (`params.placement`: `split_448`, `split_896`, `split_1344` — the payload's two
  halves straddle the named step boundary; `windows_min` ≥ 2): ≥ 10 records, `page` and `text`.
- `density_thinned` (`params.density`: `1/1`, `1/2`, `1/4`, `1/8` payload sentences per window;
  `params.placement`: `head`, `tail`, `interleave`; finding 9): 4 × 3 = 12 combinations × 2 base
  payloads (one `instruction_override`-shaped, one `natural_language`-shaped) → 24 records,
  `windows_min` ≥ 4 for the `1/4` and `1/8` levels.
- `repetition_camouflage` (`params.repeat`: `1`, `2`, `3`, `5`; finding 10): 4 base payloads × 4
  levels = 16 records, all inside one window (`windows_min: 1`), half camouflaged as cookie-banner /
  footer boilerplate repeated across a `page`; the stage-2 `plain`-shaped base payloads are pinned
  `["blocked"]` at every repeat level — the regex floor the report pairs with the classifier's
  repetition drop.
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
- [ ] `repetition_camouflage` stage-2-shaped bases pinned `["blocked"]` at all four levels and
      passing under `fallback=0.0`.
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
revision, `source.framing` per the source's shape, and refuses a row whose text carries a secret
shape; `uv run python -m scripts.corpus.ingest.agentdojo --help` exits 0 offline.

**Implementation Hints:**
- Package `scripts/corpus/ingest/` with one module per source and a shared `render.py` (email body
  → forum-post `page`; tool-output string → article `page` or `search` snippet; short instruction →
  `text`). CLI shape: `argparse`, `--input PATH` (a local download the implementer makes by hand —
  the socket guard applies to tests, not to the host run; record the exact download commands in
  Implementation Notes), `--revision SHA`, `--seed N`, `--limit N`, `--out tests/corpus/attacks/`.
  Follow `scripts/export_contract.py`'s `build_parser` / `main(argv)` idiom (`:293-310`).
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
  language per category. 22M's multilingual AUC is .942 vs 86M's .995 (model card; finding 7) — the
  multilingual slice is what makes the 22M-vs-86M decision table meaningful.
- Ingested records from US-004 land in these two categories by the mapping rule in US-004; this
  story's counts are for **owned** records so the floor holds even if ingestion was skipped.
- Every record's `marker` is a short phrase present verbatim; keep markers distinct across languages.

**Acceptance Criteria:**
- [ ] ≥ 24 owned `natural_language` and ≥ 16 owned `authority_seo` records; ≥ 6 languages; all
      three surfaces represented in each category.
- [ ] Every record `leaked` under `fallback=0.0` (a generic test over the two categories).
- [ ] Attack corpus totals after this story: ≥ 200 records, ≥ 5 per category, recorded in
      Implementation Notes as a category × surface table (counts only).
- [ ] Lint-clean; `tests/corpus/README.md` lists the languages present.
- [ ] Zero runtime change (ruling 6) re-asserted at spec end: the `git diff --stat` set is empty,
      `derive_sanitizer_revision({})` unchanged, `scripts.export_contract --check` green.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

## Edge Cases

- A `second_paragraph` variant of a non-anchored category: expected identical to `plain` (US-001).
- An `entity` variant that stage 1 decodes one level on `/retrieve` but not on `/search` (search
  text goes through `extract_html` + `html.unescape` post-hardening — spec 1 R26): outcomes may
  differ by route; both are recorded, neither pinned (US-001).
- A `hidden_markup` phrasing that trips stage 2 after extraction (e.g. `[SYSTEM]` inside a hidden
  div): `blocked`, attributed to stage 2 by the report (US-002).
- A density record whose recorded window count is below `windows_min`: spec 4 US-002 lists it and
  it is re-authored; the lint cannot check window counts (US-003).
- A repetition record at `repeat = 5` exceeding the `search` content cap: use `page` for high repeat
  levels (US-003).
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
`pipeline/stage1_extraction.py:38-49`, `:154-165`.

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

- Character-per-token budgeting can under-provision multilingual filler; spec 4 US-002 re-authors.
- A source repository can vanish or change licence after the pin; the pinned revision and the local
  input hash keep the corpus reproducible, and the NOTICE entry records what was taken and when.
