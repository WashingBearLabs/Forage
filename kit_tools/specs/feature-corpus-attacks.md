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
- **The `marker` must survive its own variant** (added 2026-09-19, validation round 1 — this was a
  leak-check false negative in the flattering direction). A record's `marker` is compared against the
  wire text *after the pipeline has already de-obfuscated it*: `pipeline/stage1_extraction.py:92-110`
  deletes U+200B / U+200C / U+200D / U+FEFF / U+00AD outright, BeautifulSoup decodes HTML entities
  during parsing, and `get_text` rejoins `split_tags` markup. An authored marker that overlaps the
  obfuscated token therefore never matches the wire text, and the record scores `neutralised` while
  the payload arrived intact. Two rules, both enforced by spec 1's lint: (a) the normalised marker
  must be a substring of the **post-pipeline** form of its payload, not merely of the raw payload;
  (b) in practice, choose the marker from a stretch of payload the variant does not touch — the
  obfuscation belongs on the trigger token, the marker on the sentence around it. Spec 1's
  per-variant test is the backstop. The Edge Cases entry about an `entity` variant that stage 1
  decodes one level is about a route-dependent *outcome* difference and is a separate matter from
  this.
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
- **Sweep bases must be stage-2-clean — stage 3 does not run when stage 2 blocks.**
  `pipeline/orchestrator.py:181-188` skips `run_promptguard` entirely on a BLOCKED verdict
  (`skip_reason="structural_block"`), and the `/search` loop at `:997-1014` `continue`s past the
  classifier. A record carrying a BLOCK-category trigger therefore produces **no classifier call, no
  cassette entry and no score at all** — it returns a constant `blocked` at every sweep level and
  contributes nothing to the curve. Binding on the two sweep families below, whose entire purpose is
  to find where the *classifier* stops catching: **every base payload used in a sweep is
  stage-2-clean**, verified by a lint rule (`scan_structural(payload) == []` for any record whose
  category is `density_thinned`, `repetition_camouflage`, `boundary_straddle` or `sustained_midband`)
  with a test that fails on a stage-2-shaped base. Stage-2-shaped payloads keep their own pinned
  records in the regex-floor categories, where a constant `blocked` is the point. *(Added 2026-09-19,
  validation round 1 — as drafted, half of `density_thinned` and the stage-2-shaped half of
  `repetition_camouflage` measured nothing, and ruling 11's offline pooler evaluation was dead on
  those records for want of scores.)*
- `density_thinned` (`params.density`: `1/1`, `1/2`, `1/4`, `1/8` payload sentences per window;
  `params.placement`: `head`, `tail`, `interleave`; finding 9): 4 × 3 = 12 combinations × 2 base
  payloads (**both stage-2-clean** — one `natural_language`-shaped, one `authority_seo`-shaped;
  the earlier `instruction_override`-shaped base is moved to the regex-floor categories) → 24
  records, `windows_min` ≥ 4 for the `1/4` and `1/8` levels.
- `repetition_camouflage` (`params.repeat`: `1`, `2`, `3`, `5`; finding 10): 4 base payloads × 4
  levels = 16 records, all inside one window (`windows_min: 1`), half camouflaged as cookie-banner /
  footer boilerplate repeated across a `page`. **All four bases are stage-2-clean**, so every one of
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
      `sustained_midband`) has a stage-2-clean base — a lint rule asserts `scan_structural` returns
      no span for it, and a test feeds a stage-2-shaped base and asserts the lint rejects it.
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
  samplers refuse an `--input` path that resolves inside the repository root, with a test; and
  `tests/corpus/README.md` says where inputs live and that they are never committed. Only the
  sampled, re-rendered, capped records reach `tests/corpus/`.
- **Credentials for the downloads** (added 2026-09-19, validation round 1 — story quality). The
  LLMail-Inject shard and any gated source are fetched with a token **from the environment only**:
  `huggingface_hub` reads `HF_TOKEN` itself, so no token is ever passed as an argument, and the
  "record the exact download command in Implementation Notes" instruction above means the command
  **without** its environment — a pasted `HF_TOKEN=hf_…` in a spec's notes is a committed secret.
  Any error text the samplers surface follows `model_fetcher.py:937-950`'s `_fetch_reason()` pattern:
  a closed vocabulary of reason codes, never the URL or the exception's raw message, which can carry
  a signed URL. A test asserts a sampler failure message contains no `hf_`-shaped substring.
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
      inside the repository root (test: a path under the repo is rejected by reason code, a path
      outside is accepted); `.gitignore` carries `/corpus-inputs/`; `tests/corpus/README.md` states
      where inputs live and that they are never committed. *(Added 2026-09-19, validation round 3 —
      the rule existed only in Implementation Hints, so this story's checklist could pass green while
      a ~462k-row set the epic swore off vendoring sat staged for commit.)*
- [ ] **No credential can reach the corpus or a log**: tokens are read from the environment by
      `huggingface_hub` only, never passed as an argument; a test asserts a simulated sampler failure
      message carries no `hf_`-shaped substring and no URL, following `model_fetcher.py:937-950`'s
      closed-vocabulary `_fetch_reason()` pattern; the recorded download commands in Implementation
      Notes carry no environment assignment.
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

- **Anchor drift, and two categories that do not exist yet** (ruling 5; added 2026-09-19, validation
  round 1 — sibling spec 1 carries the equivalent bullet and this spec was missing it). Every
  `file:line` anchor in US-001, US-002 and US-003 was read against `main` `20ddb2a` and is accurate
  *today*, but all of them predate `epic-forage-hardening`, which this spec `depends_on` transitively
  and which rewrites some of the files they point into. Re-verify before relying on any of them.
  **Which files actually move** (measured 2026-09-19, validation round 2, by reading all eight
  hardening specs — round 1's version of this bullet named the wrong ones):
  - `pipeline/orchestrator.py` — **the one to distrust.** All eight hardening specs touch it, and
    hardening spec 1's entire search-text and URL work lands here (`_normalize_search_text`
    `:591-598`, `_sanitize_search_text` `:601-608`, the new `_scan_forms_for_search_text`, the
    `:969` / `:979` call sites). Every `orchestrator.py` anchor in this spec predates all of it.
  - `promptguard/classifier.py:23-25`, `:133-148` — hardening spec 7's model selection and
    contiguity gating.
  - `pipeline/stage2_structural.py:62-248` and `pipeline/stage1_extraction.py` — **stable, and
    deliberately so.** Hardening spec 1 carries an explicit acceptance criterion that both are
    "untouched (`git diff --stat` shows no change to either)". The regex table this spec authors
    against is the one that will still be there, so a stale warning must not send an implementer
    hunting for changes the hardening epic promised not to make.
  **More than drift:** `line_anchored_role` and `url_borne_envelope` are two of the nine categories
  US-001's Independent Test requires, and **neither is detectable on `main` today**. They are
  *corpus* category names (spec 1's `vocab.py`), not `stage2_structural.py` category names — do not
  expect to find them by grepping that file, before or after hardening. What makes them work is a
  hardening change to what the scanner *sees*, with the regex table unchanged:
  `feature-hardening-search-sanitization.md` US-001 (audit 2026-09-16-016) makes the search-text
  scan newline-preserving, so `^System:` / `^POPPY:`-shaped lines after `\n\n` reach the
  line-anchored patterns that today sit under `authority_impersonation`; US-002 (audit -032) scans
  the URL in wire form so the envelope shape is caught. Both land in `orchestrator.py`. Until those
  two stories ship, records in these two categories will not come back `blocked` — which is why this
  spec pins them and why the epic sequences after hardening. Check those two stories first; if their
  wire codes changed, this spec's pins follow.
- Character-per-token budgeting can under-provision multilingual filler; spec 4 US-002 re-authors.
- A source repository can vanish or change licence after the pin; the pinned revision and the local
  input hash keep the corpus reproducible, and the NOTICE entry records what was taken and when.
