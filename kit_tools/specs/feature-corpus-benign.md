<!-- Template Version: 2.5.0 -->
---
feature: corpus-benign
status: active
session_ready: true
depends_on: [corpus-harness, corpus-attacks]
vision_ref: "T2.3 — Injection regression corpus (CI)"
type: epic-child
size: L
epic: forage-injection-corpus
epic_seq: 3
epic_final: false
execution_order: [US-001, US-002, US-003, US-004]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Benign Corpus — Externally Sourced Genres, Over-Defence Probes, Multilingual, Long-Form, Search-Shaped

> Spec 3 of 5 in `epic-forage-injection-corpus` (rulings 3, 8, 14). Builds on spec 1. A false
> positive silently discards a real result, so this corpus is what keeps the attack numbers honest.

## Overview

The service's false-positive rate has never been measured, and PIDS-Bench's central finding is that
hand-written "hard benign" look-alikes flatter a detector — externally sourced benign text shows the
real over-blocking. This spec builds the counter-corpus from permissively licensed real web text
where such text exists (Wikinews, the Python documentation, the Rust book, public-domain books,
CC-BY arXiv papers, NotInject), labels anything synthetic as synthetic so the report can split FPR by
provenance, gives the two over-defence slices their own genres so they are never averaged into the
headline number, covers the multilingual axis on which the default 22M model is documented weakest,
and authors the search-shaped benigns that hit stage 2's known structural false positives (a
40-hex git SHA is an `encoded_payload` match).

## Goals

- ≥ 250 benign records over 9 genres, ≥ 15 per genre (≥ 30 `over_defence_probe`), ≥ 6 languages,
  ≥ 20 records with `params.windows_min ≥ 3`, lint-clean (ruling 14; asserted from spec 5 US-002).
- External provenance wherever a permissive source exists; every third-party record pinned by
  revision with a `NOTICE` entry; synthetic records labelled `source.kind: synthetic`.
- `security_prose` and `over_defence_probe` are separate genres; `multilingual` is a named genre so
  its FPR is visible.
- Every stage-2 regex has at least two benign records authored to match it, so the structural
  false-positive rate is a per-regex number.

## User Stories

Execution order: `[US-001, US-002, US-003, US-004]` (document order).

### US-001: External benign sources and the core genres — news, docs, code, forum, e-commerce

**Priority:** P1

**Description:** As a maintainer, I want the everyday web genres represented by real, permissively
licensed text rendered into the three surfaces, with the sourcing tooling that pins and attributes
each source, so the headline false-positive rate is measured on the text the service actually meets.

**Independent Test:** `uv run pytest tests/test_corpus_ingest.py -k benign tests/test_corpus_lint.py`
passes hermetically; `load_corpus()` yields ≥ 15 records for each of `news`, `docs`, `code`, `forum`,
`ecommerce`; `news`, `docs` and `code` are `third_party` with a permitted licence id and a revision
**where the source was reachable**, and `synthetic` otherwise with the reason recorded (the offline fallback
below — the ratio is asserted, not the absolute); `forum` / `ecommerce` likewise; driven with
`fallback=0.0` **every record's outcome is in its `pinned` list**, and untagged records (no `pinned`)
are `clean` — a tagged record carries `pinned: ["flagged", "blocked"]` with `pinned_reason` naming
the regex, which is spec 1's existing mechanism rather than a new field. The assertion is per-record and declared, not a blanket "all clean", because the
sampler triages every candidate before it is written (below) and real external text legitimately
trips regexes. *(Corrected 2026-09-19, validation round 2: the blanket assertion contradicted the
offline fallback and made organic false positives unrepresentable.)*

**Implementation Hints:**
- **Candidate triage — the step that makes the `clean` assertion honest, and the honesty caveat that
  goes with it** (added 2026-09-19, validation round 1; two reviewers, one of whom ran the scanner).
  Real external text does not arrive clean: an ordinary deep documentation link such as
  `href="https://docs.python.example/3/library/collections/abc/index/html"` trips `encoded_payload`
  (the class `[A-Za-z0-9+/]{40,}` includes `/`, so any ≥ 40-character run of dotless path segments
  matches), and a long docs page with a `## New task:` heading is `blocked` outright by
  `instruction_override`. Wikinews political and legal copy contains "new directive", "new task
  force", "ignore previous" and "disregard … instructions" often enough to matter. So the sampler
  **renders each accepted candidate into its surface form first** — the full HTML document for
  `page`, the title + URL + snippet join for `search`, the plain paragraphs for `text` — extracts it
  exactly as stage 1 will (`extract_html` / `normalize_text`), and runs the real `scan_structural()`
  on **that** text before writing the record. Scanning the raw candidate is a different test: stage 2
  never sees raw text, and rendering both introduces hits (a URL that exists only in the rendered
  `href`) and removes them (markup stage 1 strips). *(Corrected 2026-09-19, validation round 3.)*
  It then takes **one** documented branch: **keep it in its organic genre** and tag `params.variant` with the
  regex it hit. A real news article that trips `instruction_override` because a politician said
  "ignore previous guidance" **is** a structural false positive, and it is counted as one in that
  genre's FPR — that is the number this epic exists to measure. The triage step exists to make the
  outcome *declared* rather than discovered at drive time, so the hermetic test states each record's
  expected outcome explicitly instead of asserting a blanket "clean" that depends on which articles
  the seed happened to draw (which would flake the moment `--seed` or the upstream corpus changed —
  `CLAUDE.md`'s hermeticity policy does not tolerate that).
  *(Owner decision, 2026-09-19, validation round 2. Round 1 had a second branch that moved any
  tripping candidate into `over_defence_probe`. That was an over-correction: since tripping a regex
  was exactly what removed a record from its genre, the five headline genres' structural FPR became
  **zero by construction**, and spec 5 US-001's `fpr_external` — the epic's headline over-blocking
  number — could only ever report 0. Rejecting-and-resampling instead was the other option and was
  declined: it is the detector-flattering selection PIDS-Bench warns about, applied to the corpus
  built to refute it.)*
  **A candidate is rejected only for reasons unrelated to stage 2** (licence, length, non-prose,
  duplicate). Rejections are counted per genre and per reason and reported (below); a stage-2 hit is
  never a rejection reason, and a test asserts the sampler's reject path is never reached with
  `reason == "stage2"`.
- **Coverage records are separate from organic hits.** The ≥ 2-records-per-regex coverage floor is
  satisfied **only** by deliberately-authored `over_defence_probe` records, which are excluded from
  the headline FPR (the genre already exists for exactly that reason). Organic hits count toward
  FPR, never toward coverage. Keeping coverage deliberate makes it stable across re-seeds: an
  organic hit that satisfied a floor by luck would drop below it on the next `--seed` and turn the
  gate red for a reason unrelated to any regression. *(Owner decision, 2026-09-19, round 2.)*
- `scripts/corpus/ingest/benign.py`: one sampler with a `--source` switch and per-source adapters;
  same CLI idiom as spec 2 US-004 (`--input`, `--revision`, `--seed`, `--limit`, `--out
  tests/corpus/benign/`), same "local download, hash-pinned, ids and counts only" rules.
- **Sources with licences read at the page / directory** (record the URL and the licence text
  location in `NOTICE`): Wikinews articles — CC-BY-2.5 (attribution: article title + Wikinews
  URL per record; `news`, and other-language editions for US-003); the Python documentation —
  PSF-2.0 (`docs`; the `Doc/` tree at a CPython tag); *The Rust Programming Language* book — MIT /
  Apache-2.0 dual (`docs`, `code`); Apache-2.0 / MIT project READMEs and CHANGELOGs pinned by SHA
  (`code`, `docs`); US federal publications — public domain (`news` / `docs`,
  `LicenseRef-PublicDomain`). **Excluded** (record in the rejected table): Wikipedia, Stack Exchange,
  MDN prose, OWASP — CC-BY-SA; Reddit, Hacker News — no licence grant.
- `forum` / `ecommerce`: look for a permissive source first (an Apache-2.0 project's GitHub
  Discussions export, an open product catalogue under CC0); if none is found within the story,
  author synthetic records (`source.kind: synthetic`) and say so in Implementation Notes — the report
  splits FPR by provenance (spec 5 US-001; landscape finding 8), so synthetic never masquerades as
  external.
- **Rendering**: `page` = a **full HTML document**, not a bare fragment —
  `<html><head><title>{title}</title>{head_html}</head><body>{body_html}</body></html>` (the record
  schema already separates `head_html` from `body_html` for exactly this). The template lives in
  `scripts/corpus/ingest/render.py`, **shared with spec 2**, so this shape is binding on the attack
  corpus too. *(Corrected 2026-09-19, validation round 1: the template was
  `<article><h1>title</h1><p>…</p></article>`, with no `<title>` element.
  `pipeline/stage1_extraction.py:123-128` (`_extract_title()`) reads the title exclusively from a
  `<title>` tag and never from `<h1>`, so every rendered `page` record would have extracted
  `title: None` regardless of its declared title — defeating the genre's realism goal and any
  declared-vs-extracted comparison. Every existing HTML fixture in the repo that expects a non-null
  title wraps content in a full document, e.g. `tests/test_orchestrator.py:64-67`.)* A test renders
  one record and asserts the extracted title equals the declared one; `search` = title +
  reserved URL + the first ≤ 300 characters as `content`; `text` = the plain paragraphs. Every
  external URL in the text is rewritten to a reserved host (ruling 8) — attribution URLs live in
  `source.url`, not in the payload.
- **Deliberately-authored regex probes live in the `over_defence_probe` genre, never in the headline
  genres** (corrected 2026-09-19, validation round 1; scope narrowed in round 2). The spec already
  establishes the principle — NotInject probes get their own genre so they are "never pooled into the
  headline FPR" (Goals, US-002) — and then broke it: ~48 records authored to *guarantee* a stage-2
  match were scattered into `news` / `docs` / `code` / `forum` / `ecommerce`, the very genres whose
  FPR is the headline number. With core-genre floors at ≥ 15, a genre could carry 5 deliberate
  matches against 15 records — a 33 % "false-positive rate" that is an authoring artefact, which
  spec 5 US-002 would then freeze into per-genre FPR *ceilings*, hiding a real over-blocking
  regression underneath. These records keep their realistic provenance (`source.kind` is unchanged —
  a real README with a commit SHA is still `third_party`); they are simply counted in their own
  genre. **This applies to records authored to hit a regex, not to real text that happens to hit
  one** — an organic hit stays in its genre and counts toward that genre's FPR (see the triage
  bullet above). `params.variant` naming the regex is set in both cases; `kind`/genre is what
  separates them.
- **Regex probes** (genre `over_defence_probe`, `params.variant` = the regex name, ≥ 2 per
  stage-2 regex across this story and US-004): a `code` README with a 40-hex commit SHA in a link (`[A-Za-z0-9+/]{40,}`), a
  networking tutorial with `href="http://192.168.1.1/"` (private-IP `href`), a docs page whose
  heading is "New task: …" (`new\s+(directive|instruction|task|objective)`), a changelog line
  "disregard the previous instructions in section 2" (`disregard.*instructions`), a `data:image/png`
  inline icon (`data:` scheme), a Markdown docs page with `![logo](https://cdn.example/${version}/logo.png)`
  (`exfil_beacon`). Their expected outcome under `fallback=0.0` is `flagged` / `blocked` — the story
  asserts it and Implementation Notes count them: this is the structural false-positive number.
- **Offline fallback for the externally-required genres, and what `session_ready` means here**
  (added 2026-09-19, validation round 1). US-001 requires `news`, `docs` and `code` to be *entirely*
  `third_party`, and US-003 requires every `multilingual` / `long_form` record to be `third_party`,
  but a synthetic fallback was granted only to `forum` / `ecommerce`. The repo is hermetic by design
  (the autouse `pytest-socket` guard, `CLAUDE.md`), so these downloads are a manual host pre-step
  outside the suite — an execution session without network access, or without reachable Wikinews /
  CPython / the Rust book / Gutenberg / arXiv / Hugging Face, could not complete either story at all.
  Adopt spec 2 US-004's escape hatch verbatim for **every** genre in this spec: if a source cannot be
  fetched, Implementation Notes record `not ingested — <reason>`, the story proceeds with
  `source.kind: synthetic` records for that genre, and the per-genre floors still hold. The report
  splits FPR by provenance, so a synthetic-heavy genre is visible as such rather than silently
  standing in for external text. `session_ready: true` is therefore accurate **only with this
  fallback in place** — it is what makes the stories completable in one session.
- Excerpt caps: ≤ 6 000 characters per record except `long_form` (US-003); `search` within the caps.
- Ids `ben-0011` … continue the seed.

**Acceptance Criteria:**
- [ ] `scripts/corpus/ingest/benign.py` with adapters for at least Wikinews, CPython docs, the Rust
      book and a README/CHANGELOG source; hermetic tests for determinism, caps, URL rewriting,
      provenance fields, secret-shape refusal, no payload in output.
- [ ] ≥ 15 records per core genre; provenance as in the Independent Test; every external record has
      `source.url`, `licence`, `revision`; `NOTICE` gains one entry per source and the coverage test
      passes.
- [ ] ≥ 2 **`over_defence_probe`** records per stage-2 regex across US-001 + US-004 (except the three
      in `STAGE2_REGEX_NO_BENIGN`), each with `params.variant`; their `flagged` / `blocked` outcome
      under `fallback=0.0` asserted by a generic test over `params.variant`. A test asserts the
      coverage floor is computed over `over_defence_probe` records only, so an organic hit in a
      headline genre can never satisfy it.
- [ ] **The sampler triages before writing**: a test asserts `scan_structural()` is called on every
      candidate and that a tripping candidate is written with `params.variant` set and its genre
      unchanged — never moved to `over_defence_probe`, never rejected. A second test asserts the
      reject path is never reached with `reason == "stage2"`.
- [ ] **The rejection count has a home, and a denominator**: the sampler emits
      `{genre: {examined: n, rejections: {reason: count}}}` to a committed sidecar,
      **`tests/corpus/benign/sampler_stats.json`** — `rejections` is not a record and cannot ride in
      the `.jsonl`, so it needs its own file; `examined` is every candidate drawn, so spec 5 can
      compute a *rate* rather than an uninterpretable raw count. `scripts/corpus/report.py` reads it
      by path (absent file ⇒ the rate column prints `—`, never a crash) and the lint asserts every
      genre holding `third_party` records has an entry. *(Transport specified 2026-09-19, validation
      round 3 — round 2 named the payload at neither end.)* `scripts/corpus/report.py` (spec 5 US-001) carries it as *candidate
      rejection rate* beside the headline FPR, and a test asserts the field survives into the
      rendered report. *(Added 2026-09-19, validation round 2 — round 1 put the honesty mechanism in
      one prose paragraph with no criterion, no test and no consumer; three reviewers found it
      discharged to a spec that never mentioned it.)*
- [ ] **The offline fallback is a criterion, not an aside**: for every genre, if a source cannot be
      fetched, Implementation Notes carry `not ingested — <reason>` and the records are written
      `source.kind: synthetic`; the per-genre floors still hold; a test asserts a genre's records are
      either all-third_party-with-revision or carry a recorded synthetic reason — never a silent mix.
- [ ] Rejected-sources table in `tests/corpus/README.md` gains the share-alike / no-grant entries.
- [ ] Implementation Notes record per genre: source, revision, seed, limit, records written,
      synthetic count, **rejection count by reason** (numbers and names only).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-002: Security prose and over-defence probes

**Priority:** P1

**Description:** As a maintainer, I want the text most likely to be wrongly blocked — articles and
papers *about* prompt injection that quote attack phrases, defence documentation with fenced
examples, and NotInject's benign queries re-homed as web text — measured in two genres of their own,
so over-defence is a visible number rather than a shrug.

**Independent Test:** `load_corpus()` yields ≥ 20 `security_prose` and ≥ 30 `over_defence_probe`
records; every `over_defence_probe` from NotInject has `source.framing = rehomed_direct`,
`licence = MIT`, a dataset revision; driven with `fallback=0.0` the outcomes are recorded per genre
(not asserted — quoting `[SYSTEM]` in a code block *is* a stage-2 BLOCK today, and that is the
measurement).

**Implementation Hints:**
- `security_prose` (external where possible): arXiv papers on prompt injection whose per-paper
  licence is CC-BY-4.0 (the arXiv abstract page states the licence; take abstract + one section;
  pin by arXiv id and version), CC-BY / MIT-licensed defence READMEs and docs; owned records: a
  "how we defend against prompt injection" docs page that quotes trigger phrases inside fenced code
  blocks, a changelog entry naming an `[admin]` UI label, an FAQ that mentions `rot13`.
- `over_defence_probe`: NotInject (`leolee99/NotInject`, Hugging Face, `license: mit`, 339 benign
  samples built from injection trigger words; landscape finding 13) re-homed from user queries into
  page / snippet / text form (`framing = rehomed_direct`); sample ≥ 30 stratified across its four
  slices; it is **never pooled** into the headline FPR (spec 5 reports it separately). Owned probes:
  chat transcripts in a support forum formatted `User: … / Assistant: …` (line-start role markers
  — `^assistant:` is a BLOCK regex), a recipe step "ignore the previous step if the dough is
  sticky", a legal notice "disregard prior instructions issued before 1 May".
- `params.variant` names the regex a probe targets when it targets one; otherwise absent.
- `NOTICE` entries for NotInject and each CC-BY paper (author list as the paper states).

**Acceptance Criteria:**
- [ ] Counts, provenance and fields as in the Independent Test; NotInject sampler test hermetic
      (five-row fixture) with determinism and re-homing asserted.
- [ ] Implementation Notes carry the per-genre outcome counts under `fallback=0.0` and the list of
      record ids `blocked` by stage 2 (ids only) — the structural over-defence list.
- [ ] `NOTICE` complete for the sources used; rejected table updated (OWASP CC-BY-SA).
- [ ] Lint-clean; `tests/corpus/README.md` states that `over_defence_probe` is reported separately.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-003: Multilingual and long-form

**Priority:** P1

**Description:** As a maintainer, I want benign text in the languages a search sidecar actually
returns, and pages long enough to span several classifier windows, so the 22M-vs-86M decision has a
multilingual FPR to weigh and the contiguity rule has an accident rate to be judged by.

**Independent Test:** `load_corpus()` yields ≥ 30 `multilingual` records over ≥ 6 non-English
languages (≥ 4 each) and ≥ 20 `long_form` records with `params.windows_min ≥ 3`, `third_party`
public-domain or CC-BY / PSF sources **where reachable and `synthetic` with a recorded reason
otherwise** (US-001's offline fallback applies to this story too); lint-clean; **every record's
outcome is in its `pinned` list** under `fallback=0.0` — `clean` for untagged records (no `pinned`),
and the declared list for any record the triage step tagged with `params.variant` (spec 1's existing
`pinned` / `pinned_reason` mechanism; no new field). *(Corrected 2026-09-19,
validation round 2: "all `third_party`" and a blanket `clean` were the two statements the round-1
fallback and triage additions contradicted, and this story carried no criterion for either.)*

**Implementation Hints:**
- Multilingual sources: other-language Wikinews editions (CC-BY-2.5; de, fr, es, pt, it, ja, zh, ru
  as available), the Python documentation translations (PSF-2.0; ja, fr, es, pt-br, zh-cn, ko),
  non-English public-domain books from Project Gutenberg. `lang` per record; over-provision by 30 %
  on the character budget for CJK (spec 2 US-003's rule).
- `long_form`: public-domain book chapters and long documentation pages, 6 000–20 000 characters,
  `windows_min` 3–8; keep total corpus size reasonable (≤ 1.5 MB of JSONL across `tests/corpus/`;
  a lint test asserts the directory size).
- These are the records the contiguity accident rate is measured on (hardening spec 7 US-004's
  optional FPR smoke is superseded by this); no pins.

**Acceptance Criteria:**
- [ ] Counts, languages and window budgets as in the Independent Test; provenance and `NOTICE`
      complete.
- [ ] Directory-size lint (≤ 1.5 MB for `tests/corpus/attacks/` + `tests/corpus/benign/`) added and
      green.
- [ ] Implementation Notes record the language × surface table and the `windows_min` histogram.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-004: Search-shaped benigns — the structural false-positive families

**Priority:** P2

**Description:** As a maintainer, I want search results that look exactly like the real web —
commit SHAs, video ids, tracking parameters, prices, dates, hex escapes in code snippets, inline
data URIs, private-IP examples in networking docs — because every one of them is a stage-2 regex
waiting to fire, and the report should show which regexes cost real results.

**Independent Test:** `load_corpus()` yields ≥ 40 `search`-surface benign records across the genres
with `params.variant` naming a stage-2 regex on ≥ 24 of them, covering every regex in
`pipeline/stage2_structural.py` at least twice (a test enumerates the regex names from a committed
list and asserts coverage); driven with `fallback=0.0` the variant-tagged records' outcomes are
recorded per regex in Implementation Notes.

**Implementation Hints:**
- Author as `search` records (title / reserved URL with realistic query strings / snippet ≤ 2 000)
  derived from the external pages of US-001–003 where possible (`source` carried over) and synthetic
  otherwise. `STAGE2_REGEX_NAMES` and its companion `STAGE2_REGEX_PROBES` are **spec 1's deliverable**
  in `scripts/corpus/vocab.py` (US-001 acceptance criteria) — this story *consumes* them and creates
  nothing. *(Corrected 2026-09-19, validation round 1: this P2 story was the only place across all
  five specs that mentioned the constant, so a shared closed vocabulary was being invented as a side
  effect of one genre's coverage test.)* The coverage test runs over that list, not over the private
  `_PATTERNS` (strict pyright; `reportPrivateUsage` is relaxed only in `tests/`, and the corpus code
  lives in `scripts/`), and it identifies which pattern fired through spec 1's
  `stage2_hits(text) -> frozenset[str]` helper, which maps `FlaggedSpan.matched_text` back to a regex
  name. It cannot use `FlaggedSpan.category`: that field carries only 7 coarse values
  (`instruction_override`, `authority_impersonation`, `encoded_payload`, `prompt_boundary`,
  `suspicious_url`, `exfil_beacon`, `envelope_breakout`) and cannot distinguish `[SYSTEM]` from
  `<system>` from `---INSTRUCTIONS---`.
- **Three regexes cannot have an organic benign example, and are exempt by name.** `[poppy]`
  (poppy-bracket), `^POPPY:` (poppy-colon) and the `envelope_breakout` pattern (`<` or an
  entity-encoded `<` followed by `retrieved_content` / `retrieval_note` / `retrieval_warning` /
  `retrieval_cache_note`) match Forage/Poppy-internal tokens with no plausible occurrence in real
  external text — Wikinews, the CPython docs, the Rust book and Gutenberg will never contain them, and
  hand-authoring text that quotes internal envelope-tag names reads as attack-shaped, not benign,
  which undercuts what the corpus measures. They are listed in `vocab.py` as
  `STAGE2_REGEX_NO_BENIGN` with this reason, the ≥ 2-per-regex floor skips them, and a test asserts
  the exemption list is exactly those three so a fourth cannot be added quietly. For **every other name in
  `STAGE2_REGEX_NAMES`**, US-004 authors realistic carriers (a docs page documenting a chat-template
  format, a support-forum post quoting a log line, a tokenizer README) and names each in
  `params.variant`. The set is **derived, never enumerated here**: the coverage test walks
  `STAGE2_REGEX_NAMES`, subtracts `STAGE2_REGEX_NO_BENIGN`, and fails naming any member with fewer
  than two `over_defence_probe` records — so a pattern added to `stage2_structural.py` later turns
  the gate red instead of being silently uncovered. *(Added 2026-09-19, validation round 1 — nine of
  the 24 patterns had no named example anywhere in the spec. Hand-enumerating the gap was itself
  wrong twice: round 1's list of examples missed several patterns, and round 2's "six remaining"
  list omitted ```` ```system ```` and `<|im_start|>`, which are separate compiled patterns from
  ```` ```instructions ```` and `<|endoftext|>`. Deriving from the constant removes the class of
  error.)*
- Shapes: 40-hex SHAs and 44-character base64 ids in URLs and text (`encoded_payload` — keep them
  away from `key` / `token` / `secret` on the same line), `\x1b[0m` ANSI sequences in a terminal
  transcript (`\x` escapes), `rot13` in a puzzle forum, `data:image/svg+xml` favicons, `192.168.0.1`
  router-admin instructions with `href`, `javascript:void(0)` in an accessibility note, a jobs page
  titled "New objective: …", a Markdown docs snippet with `![](https://img.example/${branch}/badge.svg)`,
  a support snippet quoting `<|im_start|>` from a model's changelog (`prompt_boundary`), an
  e-commerce listing whose snippet ends with a long tracking token.
- Keep `lang` mostly `en` with a few multilingual siblings; these records count toward the genre
  floors of their genre, not toward a `search` genre (there is none).

**Acceptance Criteria:**
- [ ] ≥ 40 `search` benign records; ≥ 24 regex-tagged; every `STAGE2_REGEX_NAMES` entry **except the
      three in `STAGE2_REGEX_NO_BENIGN`** covered ≥ 2
      across the whole benign corpus (test); lint-clean.
- [ ] Implementation Notes: per-regex outcome counts under `fallback=0.0` and the final benign totals
      (≥ 250; ≥ 15 per genre; ≥ 30 `over_defence_probe`; ≥ 6 languages; ≥ 20 with `windows_min ≥ 3`).
- [ ] Zero runtime change (ruling 6) re-asserted at spec end: the `git diff --stat` set is empty,
      `derive_sanitizer_revision({})` unchanged, `scripts.export_contract --check` green.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

## Edge Cases

- A Wikinews article that quotes a politician saying "ignore previous …" — keep it: it is exactly
  the external hard benign; tag `params.variant` (US-001).
- A public-domain book with a real historical hostname or address — rewrite hosts to reserved names;
  postal addresses are fine (US-003).
- A CC-BY paper whose arXiv licence is "arXiv.org perpetual non-exclusive" (not CC) — excluded;
  the sampler reads the licence field and refuses anything but CC-BY-4.0 / CC0 (US-002).
- A NotInject row that is itself a real attack phrase (the set is built from trigger words) — it
  stays benign by the source's label; the report shows what stage 2 does with it (US-002).
- An external excerpt exceeding the `search` snippet cap — truncate at a sentence boundary before
  the cap; never mid-word (US-001, US-004).
- Duplicate text across two records (an excerpt sampled twice) — the sampler de-duplicates on a
  text hash; the lint asserts no two records share a payload hash (US-001).

## Out of Scope

- Attack records (spec 2); cassettes (spec 4); FPR floors and the report (spec 5).
- Share-alike or unlicensed sources (Wikipedia, Stack Exchange, MDN prose, OWASP, Reddit, HN).
- Tuning any regex or threshold (ruling 6).

## Assumptions

- Wikinews (CC-BY-2.5), the CPython docs (PSF-2.0), the Rust book (MIT/Apache-2.0), Gutenberg
  (public domain) and NotInject (MIT) are permissive enough for a public Apache-2.0 repository with
  attribution in `NOTICE`; a per-record `licence` field makes this checkable.
- Attribution for CC-BY sources is satisfied by the `NOTICE` entry plus `source.url` per record.
- Where no permissive `forum` / `ecommerce` source exists, synthetic records are acceptable because
  the report splits FPR by provenance.

## Technical Considerations

- The benign samplers share `render.py` and the CLI shape with spec 2's; one `NOTICE` section
  serves both.
- Directory-size lint keeps the corpus clone-friendly; cassettes (spec 4) have their own cap.
- `STAGE2_REGEX_NAMES` lives in spec 1's `scripts/corpus/vocab.py` and must be kept in step with
  `stage2_structural.py` by hand; **spec 1's** test asserts its length equals the number of compiled
  patterns, and per-pattern identification goes through spec 1's `stage2_hits()` helper (matching
  `FlaggedSpan.matched_text` against `STAGE2_REGEX_PROBES`), never through `_PATTERNS` and never
  through the 7-value `FlaggedSpan.category`.

### Validation residue — closed at `needs-work` (2026-09-19, `/kit-tools:validate-epic`, 3 rounds)

Thirty reviewers over three rounds took this epic from 19 criticals to 0 open; the items below are
the warnings that remained when validation was deliberately closed rather than chased to zero — the
same call, for the same reason, that `epic-forage-hardening` recorded on the same day: the precision
reviewers surface a new layer every round, and **every code anchor in this spec predates eight
unexecuted hardening specs** (ruling 5), so precision spent now is precision spent twice. Re-verify
against the post-hardening tree at execution time; treat each item as a decision the implementer
makes deliberately, not a defect to discover.

- **The coverage arithmetic does not close.** 24 compiled patterns minus the three in
  `STAGE2_REGEX_NO_BENIGN` is 21, at ≥ 2 probe records each = 42 — against a stated
  `over_defence_probe` floor of ≥ 30. Reconcile the floor with the derived requirement before
  authoring, and let the derived number win.
- **`params.variant` is a single value, but a record can trip several regexes.** The triage step tags
  "the regex it hit"; say which one when there are two, or make the field a list in spec 1's schema.
- **`STAGE2_REGEX_NO_BENIGN` is frozen at exactly three with no amendment path.** A test asserting
  the list is exactly those three turns a legitimate future discovery into a red gate with no
  documented way to extend it.
- **Per-genre × route FPR cells are small** — a genre floor of ≥ 15 records spread over three
  surfaces leaves roughly five per cell, so a single record moves a cell's rate by ~20 points. Spec 5
  US-002's `max_fpr` ceilings should be set per genre, not per genre × route, unless the floors rise.
- **No auth, rate-limit or retry story for the ~8 external hosts** the samplers reach (Wikinews and
  its non-English editions, python.org, the Rust book, Gutenberg, arXiv, Hugging Face). These are
  host-run steps outside the socket guard; a 429 mid-sample is the likely first failure.
- **PII**: the `forum` genre's preferred source is a real GitHub Discussions export carrying real
  usernames, and no scrubbing rule is stated for text committed permanently to a public repo.
  Decide handling before US-001 ingests, not after.
- US-003 bundles multilingual sourcing and long-form sourcing under one story.

## Related Documentation

- `tests/corpus/README.md`; `NOTICE`; `kit_tools/arch/SECURITY.md` "Prompt-Injection Signalling".

## Implementation Notes

<!-- Per story: source table (name, licence, revision, seed, limit, records), outcome counts under
fallback=0.0, structural over-defence ids. Numbers and ids only. -->

## Refinement Notes

### Research Findings

**Decision:** Externally sourced benign text wherever a permissive source exists; synthetic records
labelled and reported separately.
**Rationale:** PIDS-Bench (2026-09-14) finds "provenance-sensitive over-defense": curated benign
look-alikes flatter a detector while externally sourced text shows the real FPR (PG2-86M
hard-benign FPR 0.15, structural-shift FPR 0.33).
**Source:** https://arxiv.org/html/2609.15017.

**Decision:** `over_defence_probe` (NotInject re-homed) and `security_prose` are separate genres,
never pooled into the headline FPR.
**Rationale:** NotInject's rows are user queries (direct framing) built from trigger words; pooling
them changes what the FPR number means (finding 13).
**Source:** https://huggingface.co/datasets/leolee99/NotInject (MIT, 2025-04-04).

**Decision:** Multilingual is a named genre with its own ceiling.
**Rationale:** The Prompt Guard 2 card gives 22M multilingual AUC .942 vs 86M .995 — the axis on
which the default model is weakest and the one that decides the model question.
**Source:** https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M.

**Decision:** Every stage-2 regex gets ≥ 2 benign records authored to match it.
**Rationale:** `[A-Za-z0-9+/]{40,}` matches a 40-hex git SHA; `^assistant:` matches a quoted chat
transcript; `new\s+task` matches a jobs page — the structural over-defence is per regex.
**Source:** `pipeline/stage2_structural.py:62-248`.

### Scope Adjustments

- The hardening spec 7 US-004 "optional FPR smoke" is superseded by `long_form` + the contiguity
  config in spec 5.

### Decisions Made

- Corpus size cap 1.5 MB for records (cassettes capped separately in spec 4).

## Clarifications

### Session 2026-09-19
- Q: Sources? → A: Permissive only, research-verified; synthetic allowed when no source exists,
  labelled.

## Open Questions

- [ ] Whether a permissive `forum` / `ecommerce` source exists — non-blocking; synthetic fallback.

## Known risks (planning)

- Attribution stacking: many CC-BY sources means a long `NOTICE` section; acceptable, and the
  coverage test keeps it complete.
- Wikinews CC-BY-2.5 attribution wording differs from 4.0; record the article URL and title per
  record and the licence URL once in `NOTICE`.
