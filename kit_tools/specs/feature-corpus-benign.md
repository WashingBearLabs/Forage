<!-- Template Version: 2.5.0 -->
---
feature: corpus-benign
status: active
session_ready: true
depends_on: [corpus-harness]
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
`ecommerce`; `news`, `docs` and `code` are entirely `third_party` with a permitted licence id and a
revision; `forum` / `ecommerce` records are `third_party` where a source was found and otherwise
`synthetic`; driven with `fallback=0.0` every record is `clean` except the ones whose `params.variant`
names a stage-2 regex (those are `flagged` or `blocked`, asserted).

**Implementation Hints:**
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
- **Rendering**: `page` = a minimal article template (`<article><h1>title</h1><p>…</p></article>`;
  the template lives in `scripts/corpus/ingest/render.py`, shared with spec 2); `search` = title +
  reserved URL + the first ≤ 300 characters as `content`; `text` = the plain paragraphs. Every
  external URL in the text is rewritten to a reserved host (ruling 8) — attribution URLs live in
  `source.url`, not in the payload.
- **Regex-targeted benigns** (`params.variant` = the regex name, ≥ 2 per stage-2 regex across this
  story and US-004): a `code` README with a 40-hex commit SHA in a link (`[A-Za-z0-9+/]{40,}`), a
  networking tutorial with `href="http://192.168.1.1/"` (private-IP `href`), a docs page whose
  heading is "New task: …" (`new\s+(directive|instruction|task|objective)`), a changelog line
  "disregard the previous instructions in section 2" (`disregard.*instructions`), a `data:image/png`
  inline icon (`data:` scheme), a Markdown docs page with `![logo](https://cdn.example/${version}/logo.png)`
  (`exfil_beacon`). Their expected outcome under `fallback=0.0` is `flagged` / `blocked` — the story
  asserts it and Implementation Notes count them: this is the structural false-positive number.
- Excerpt caps: ≤ 6 000 characters per record except `long_form` (US-003); `search` within the caps.
- Ids `ben-0011` … continue the seed.

**Acceptance Criteria:**
- [ ] `scripts/corpus/ingest/benign.py` with adapters for at least Wikinews, CPython docs, the Rust
      book and a README/CHANGELOG source; hermetic tests for determinism, caps, URL rewriting,
      provenance fields, secret-shape refusal, no payload in output.
- [ ] ≥ 15 records per core genre; provenance as in the Independent Test; every external record has
      `source.url`, `licence`, `revision`; `NOTICE` gains one entry per source and the coverage test
      passes.
- [ ] ≥ 2 benign records per stage-2 regex across US-001 + US-004, each with `params.variant`; their
      `flagged` / `blocked` outcome under `fallback=0.0` asserted by a generic test over
      `params.variant`.
- [ ] Rejected-sources table in `tests/corpus/README.md` gains the share-alike / no-grant entries.
- [ ] Implementation Notes record per genre: source, revision, seed, limit, records written,
      synthetic count (numbers and names only).
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
languages (≥ 4 each) and ≥ 20 `long_form` records with `params.windows_min ≥ 3`, all `third_party`
public-domain or CC-BY / PSF sources; lint-clean; every record `clean` under `fallback=0.0`.

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
  otherwise; the regex-name list lives in `scripts/corpus/vocab.py` as `STAGE2_REGEX_NAMES` (the
  human names from spec 2 US-001's `notes` convention) — the coverage test is over that list, not
  over the private `_PATTERNS` (strict pyright; `reportPrivateUsage` is relaxed only in `tests/`, and
  the corpus code lives in `scripts/`).
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
- [ ] ≥ 40 `search` benign records; ≥ 24 regex-tagged; every `STAGE2_REGEX_NAMES` entry covered ≥ 2
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
- The `STAGE2_REGEX_NAMES` list must be kept in step with `stage2_structural.py` by hand; a test
  asserts its length equals the number of compiled patterns (read through the public
  `scan_structural` behaviour on one probe per name, not through `_PATTERNS`).

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
