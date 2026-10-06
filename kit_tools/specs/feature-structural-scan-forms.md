<!-- Template Version: 2.5.0 -->
---
feature: structural-scan-forms
status: active
session_ready: true
depends_on: []
vision_ref: "T2.3 follow-up — close the injection corpus's structural findings"
type: epic-child
size: L
epic: forage-structural-hardening
epic_seq: 1
epic_final: false
created: 2026-10-06
updated: 2026-10-06
---

# Feature Spec: Structural Scan Forms — Case, Entities, Collapse and Confusables on Every Route

## Overview

Stage 2 (`pipeline/stage2_structural.py`, 24 patterns) applies **no normalisation of its own**.
It matches the patterns directly against `ExtractionResult.raw_text`. Stage 1's
`_normalize_text` applies NFC, strips 9 invisible code points and collapses whitespace within
lines. Nothing else happens on `/retrieve` or `/extract`. `/search` already scans a second,
better-prepared pair of forms (`_scan_forms_for_search_text`, `orchestrator.py:940-982`):
- two levels of entity decoding,
- control-character stripping,
- a newline-preserving scan form plus its whitespace-collapsed wire form.

The corpus shows what the gap costs. These leaks all come from the same missing step:
- **`case` (5 records):** seven patterns are case-sensitive.
- **`entity` (8 records):** `/extract` uploads are never entity-decoded, and `/retrieve`'s
  parser decodes one level, so double encoding survives.
- **`second_paragraph` (4 records):** two non-DOTALL patterns break on a newline, and these
  routes have no collapsed form.
- **`confusable` (9 records):** nothing folds Cyrillic or other look-alikes on any route.

This spec gives all three routes one shared scan-form builder. It feeds stage 2 **extra derived
forms** while stage 3's input stays byte-identical, so the replayed cassettes need no
re-recording.

## Goals

- All 26 attack records listed in the Research Findings leak table under `case`, `entity`,
  `second_paragraph` and `confusable` are caught (outcome `blocked` or `flagged` in the
  regenerated `tests/corpus/baseline.json`), on every route where they leaked.
- **Every one of the 24 patterns** matches a case-varied, an entity-encoded (one and two
  levels), a newline-split (for the two non-DOTALL patterns) and a confusable-substituted
  variant of its own registered probe (`scripts/corpus/vocab.py` `STAGE2_REGEX_PROBES`). This is
  asserted by a class-level property test, not by corpus rows.
- Zero cassette misses. Both cassette files are byte-unchanged, so no record's stage-3 input text
  changes.
- Zero core-genre benign records move from `passed` to `flagged` or `blocked`. Every pinned
  benign record's pin holds.

## User Stories

### US-001: Make the seven case-sensitive patterns case-insensitive

**Priority:** P1

**Description:** As an operator relying on stage 2, I want every structural pattern to ignore
letter case, so that re-casing a trigger (`SYSTEM:`, `\X41`, `HTTPS://`) cannot evade it.

**Independent Test:** Run the class-level case property test plus the corpus gate. The five
`case` leaks flip to caught, with no other benign movement. This delivers value with no other
story done.

**Implementation Hints:**
- `pipeline/stage2_structural.py`, `_PATTERNS` (:61-241). The case-sensitive entries are:
  - `instructions_banner` :101 (no `re.IGNORECASE`)
  - `poppy_line` :129 (MULTILINE only)
  - `system_line` :136 (MULTILINE only)
  - `hex_escape` :164 (lowercase `\x` only)
  - `im_start` :185 and `endoftext` :191
  - `exfil_image` :223 (lowercase `https?` only)
- Add `re.IGNORECASE` where it is missing. Where a character class encodes case
  (`\\x`, `https?`), widen it or rely on the flag; don't add an alternation.
- **Do not rename** `poppy_line`: the names are pinned by `scripts/corpus/vocab.py`
  `STAGE2_REGEX_NAMES` (:224-249), and `tests/test_corpus_lint.py:443-445` asserts the lengths
  agree. CLAUDE.md invariant 1 forbids *new* "poppy" names only.
- **False-positive watch:** a case-insensitive line-start `system:` / `assistant:` pattern
  can match prose such as "System: Linux" in docs. Read the regenerated baseline's benign
  columns. If a core-genre benign record starts to flag or block, narrow the pattern (for
  example, require the canonical role spelling with case-insensitive matching only on
  non-initial letters) and record why in Implementation Notes.
- The probes to vary live in `scripts/corpus/vocab.py` `STAGE2_REGEX_PROBES` (:250-268).
- Leaked records to watch: `atk-0071` (`poppy_line`), `atk-0070` and `atk-0086`
  (`system_line`), `atk-0116` (`hex_escape`), `atk-0147` (`exfil_image`).
- `stage2_structural.py` is a hashed source (`pipeline/sanitizer_revision.py:13-22`). The edit
  rotates `sanitizer_revision`; record it per CLAUDE.md "Coexistence" practice (read-only
  whole-file reversal control).

**Acceptance Criteria:**
- [ ] All 24 patterns in `_PATTERNS` match their `STAGE2_REGEX_PROBES` probe rendered fully
      upper-case, fully lower-case and in alternating case. This is one parametrised test over
      every pattern name, not a list of the seven.
- [ ] `atk-0071`, `atk-0070`, `atk-0086`, `atk-0116` and `atk-0147` are no longer `leaked` in
      the regenerated baseline (`uv run python -m scripts.corpus.report --write-baseline`).
- [ ] The regenerated baseline's diff moves no core-genre benign record from `passed`, and
      `test_every_pinned_record_holds_*` stays green.
- [ ] Both cassette files under `tests/corpus/cassettes/` are byte-unchanged, and
      `test_every_cassette_answers_every_record_*` is green.
- [ ] The `sanitizer_revision` rotation is recorded in CLAUDE.md, `docs/bootstrap-notes.md` and
      the `kit_tools/docs/GOTCHAS.md` table. The before/after values and a read-only reversal
      control reproduce the prior value under default and shipped config.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated baseline's `offline` section (newly blocked texts leave the stage-3 pool), and `tests/test_corpus_docs.py` is green
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: One shared scan-form builder for stage 2 on all three routes

**Priority:** P1

**Description:** As an operator, I want `/retrieve` and `/extract` to scan the same decoded and
collapsed forms `/search` already scans, so that double-encoded entities and triggers broken by a
newline cannot pass stage 2 on the page and upload routes.

**Independent Test:** Drive `/retrieve` and `/extract` (hermetic corpus drivers) with the 8
`entity` and 4 `second_paragraph` leaks: all are caught. `/search`'s pinned wire and counter
captures (`tests/test_search_pipeline_pins.py`) are unchanged.

**Implementation Hints:**
- **Put the builder in `pipeline/stage2_structural.py`.** It is already hashed, so no new
  revision source is needed. A new module that decides what stage 2 sees would otherwise have to
  join `_REVISION_SOURCES`; CLAUDE.md's `url_validator.py` precedent applies.
  - Suggested shape: `structural_scan_forms(text: str) -> tuple[str, ...]`, returning
    deduplicated forms, plus `scan_structural_forms(forms) -> StructuralScanResult`.
- **Forms:**
  1. the input as-is;
  2. its entity decode, `html.unescape` applied to a fixed point and **bounded at two levels**
     (the depth `/search` uses), then C0/C1 control characters stripped;
  3. the whitespace collapse of (2).
  Form (3) is what lets `disregard_instructions` (:73) and `exfil_image` (:223), the two
  patterns without `re.DOTALL`, match across a line break.
- **Combining rule:**
  - worst verdict across forms wins (BLOCKED > SUSPICIOUS > CLEAN);
  - `flags` are the union, deduplicated by `(category, matched_text)`;
  - the SUSPICIOUS penalty uses the **largest per-form suspicious count**, not the sum, so the
    same span seen in three forms cannot triple `max(-0.45, -0.15×count)` (:298).
  - `FlaggedSpan.matched_text` never reaches the wire. `structural_flags` carries category
    names only (`models.py:101-103`).
- **Wiring:** `sanitize_and_structure` (`pipeline/orchestrator.py:263`) calls
  `scan_structural(extraction.raw_text)`. Switch it to the forms scan. The stage-3 call at
  `:278` keeps passing `extraction.raw_text` unchanged; that is the no-re-record guarantee.
  This one call site serves `/retrieve` (HTML and PDF) and `/extract`.
- **Refactor `/search`** (`_scan_forms_for_search_text`, `orchestrator.py:940-982`; field loop
  `:1810-1846`) onto the shared helpers **only where the output is provably identical**. Its
  `extract_html` first-level decode and wire form are stage-3 input (`:1285-1287`) and must not
  change. `tests/test_search_pipeline_pins.py` (four wire/counter pins, two exhaustion pins) is
  the guard. If a clean refactor isn't possible, leave `/search` as is and say so in
  Implementation Notes. `/search` already catches these variants.
- Stage 2 runs off-loop via `asyncio.to_thread` (`orchestrator.py:263`); keep the forms scan
  inside that one thread call. Cost is about 3× the regex work per text. Check
  `/metrics` `search_latency` targets aren't affected, and note the measured cost.
- Leaked records:
  - `entity`: `atk-0075`, `atk-0119`, `atk-0047`, `atk-0090`, `atk-0103`, `atk-0136`,
    `atk-0149` (`/extract`), `atk-0049` (`/retrieve`)
  - `second_paragraph`: `atk-0053`, `atk-0152` (`/extract`), `atk-0054`, `atk-0153`
    (`/retrieve`)
- Hashed files touched: `stage2_structural.py` and `orchestrator.py`. Record the rotation with
  per-file reversal controls.

**Acceptance Criteria:**
- [ ] `/retrieve` and `/extract` scan the as-is, decoded (≤ 2 levels, control-stripped) and
      collapsed forms. A unit test proves a double-entity-encoded probe and a probe with a
      newline inside a non-DOTALL trigger are each caught through `sanitize_and_structure`.
- [ ] Stage 3 receives exactly `extraction.raw_text`. A test asserts the argument identity, and
      both cassette files are byte-unchanged with zero cassette misses.
- [ ] One attack seen in several forms yields one flag per `(category, matched_text)`. The
      penalty equals that of the single worst form (unit test with a SUSPICIOUS span present
      in all three forms).
- [ ] Every pattern's probe is caught when entity-encoded at one and at two levels, on both
      routes. This is a parametrised test over all 24 names.
- [ ] The 12 `entity` and `second_paragraph` records listed in the hints are no longer
      `leaked` in the regenerated baseline. No core-genre benign record moves from `passed`,
      and all pins hold.
- [ ] `tests/test_search_pipeline_pins.py` is unchanged and green.
- [ ] The rotation is recorded (CLAUDE.md, `docs/bootstrap-notes.md`, GOTCHAS table), with
      each hashed file reverted alone and a both-reverted control reproducing the prior value.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated baseline's `offline` section (newly blocked texts leave the stage-3 pool), and `tests/test_corpus_docs.py` is green
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-003: A generated confusable fold as one more scan form

**Priority:** P1

**Description:** As an operator, I want stage 2 to see look-alike characters (Cyrillic `а`, `е`,
`о`, full-width Latin, and similar) folded to their ASCII prototypes, so that swapping one letter
for a homoglyph cannot evade any pattern.

**Independent Test:** Run the confusable property test plus the corpus gate. The 9 `confusable`
leaks flip to caught on every route, ASCII-only text is unchanged by the fold, and multilingual
benign records don't move.

**Implementation Hints:**
- **Data:**
  - Vendor Unicode's `confusables.txt` (UTS #39 data, current version 18.0.0, published
    2026-08-27, <https://www.unicode.org/reports/tr39/>) under a repo path such as
    `scripts/data/unicode/confusables.txt`, with its sha256 recorded.
  - Licence: Unicode License v3 (permissive). Add the attribution to `NOTICE`.
- **Generator:** `scripts/generate_confusables.py` is operator-only, like the other `scripts/`.
  It writes a **generated, committed** Python module such as `pipeline/confusables.py` with a
  "generated — do not hand-edit" header naming the Unicode version and source sha256.
  Generation rules:
  - keep only entries whose **source is non-ASCII** and whose prototype is entirely ASCII
    (letters, digits, punctuation), so ASCII text can never fold into something else;
  - expose a `str.maketrans`-style table plus the version constant.
- **Revision input:** the generated module **joins `_REVISION_SOURCES`** (it decides what
  stage 2 sees). That covers both the Unicode version and the filtering rules (owner answer
  2026-10-06: "its Unicode version is hashed into `sanitizer_revision`"). It moves the hashed-file
  count from nine to ten:
  - `tests/test_governance_docs.py:773` asserts the number word in prose, so update every
    "nine hashed sources" sentence the test reads;
  - CLAUDE.md invariant 3's note on `_REVISION_SOURCES` stays true.
- **The form:** NFKC, then the fold table, applied to form (2) from US-002's builder (decoded,
  control-stripped), and its collapse. Pattern literals are ASCII, so they need no skeleton
  pass. That is the TR39 "apply to both sides" requirement satisfied trivially.
- A drift test re-runs the generator from the vendored data into a temp path and asserts the
  output is byte-identical to the committed module. This is hermetic: no network.
- **Don't adopt a library.** `disarm` (MIT, 2026-09-26) has one maintainer and 2 stars, with
  aarch64 wheels unverified (the image is multi-arch). `confusable-homoglyphs` appears archived,
  and PyICU is a heavy native build. Research Findings has the sources.
- Leaked records:
  - page: `atk-0073`, `atk-0168`, `atk-0043`, `atk-0091`, `atk-0104`, `atk-0137`
  - text: `atk-0045`
  - search: `atk-0121`, `atk-0044`
- `/search` gets the folded form as well: add it to the field loop's forms. `/search` stage-3
  input is unchanged.

**Acceptance Criteria:**
- [ ] `pipeline/confusables.py` is generated by `scripts/generate_confusables.py` from the
      vendored `confusables.txt`, whose sha256 is pinned in a test. A hermetic drift test
      regenerates the module and asserts byte equality.
- [ ] Folding any pure-ASCII string returns it unchanged (property test over the corpus's
      benign ASCII texts and random ASCII).
- [ ] For each of the 24 patterns, its probe with each Latin letter replaced by every table
      entry that folds to that letter is caught. The sample is deterministic and covers every
      Cyrillic and Greek look-alike of every probe letter.
- [ ] The 9 `confusable` records listed in the hints are no longer `leaked` in the regenerated
      baseline. The `multilingual` benign genre's outcomes are unchanged, as is every
      core-genre benign record, and all pins hold.
- [ ] `pipeline/confusables.py` is in `_REVISION_SOURCES`, and every prose count of hashed
      sources reads "ten" (`tests/test_governance_docs.py` green).
- [ ] `NOTICE` carries the Unicode data attribution.
- [ ] Both cassette files are byte-unchanged, with zero misses.
- [ ] The rotation is recorded (CLAUDE.md, `docs/bootstrap-notes.md`, GOTCHAS table) as the
      first rotation that **adds a hashed source** since `url_validator.py`. Include reversal
      controls: module removed plus each edited file reverted.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated baseline's `offline` section (newly blocked texts leave the stage-3 pool), and `tests/test_corpus_docs.py` is green
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

## Edge Cases

- Text containing literal `&amp;amp;amp;…` chains: decoding stops at two levels, never loops
  unbounded (US-002).
- An entity that decodes to a control character: it is stripped after decoding, matching
  `/search` (US-002).
- Empty extraction text (`raw_text == ""`): the builder returns a single empty form, giving
  CLEAN with no exception (US-002).
- Two forms are identical (plain ASCII prose): they are deduplicated, so each is scanned once
  (US-002).
- PDF-extracted text (`/retrieve` PDF and `/extract`) goes through the same forms. PDFs carry no
  entities, so the decode form usually equals the as-is form (US-002).
- Confusables that fold to multi-character ASCII prototypes (for example `ﬁ` → `fi`): allowed.
  Length changes in a scan-only form are harmless because spans are not mapped back to the wire
  (US-003).
- A legitimate Cyrillic-language page: the fold produces Latin-looking gibberish that matches no
  pattern. The `multilingual` benign genre is the guard (US-003).
- A NFKC-sensitive full-width trigger (`ＳＹＳＴＥＭ`): caught through NFKC in the fold form
  (US-003).

## Out of Scope

- Changing what stage 3 classifies (owner, 2026-10-06). That is a later epic with an owner
  re-recording gate.
- Inline-tag splits and markup-consumed triggers: spec 2 (`structural-markup-surface`).
- Percent-decoding and base64 "decode then rescan" of whole bodies. `/search` URLs already have
  their own percent-decoded scan (`_SEARCH_URL_RULES`). Body-level decoding is a candidate
  follow-up with its own false-positive measurement.
- Typoglycemia or fuzzy keyword matching (OWASP lists it; its false-positive cost is
  unmeasured).
- Reducing over-defence on `security_prose` / `over_defence_probe`.

## Assumptions

- Stage 2 never rewrites text, and only its verdict, categories and penalty reach the response.
  So derived forms change outcomes, never served bytes.
- The corpus's `case`, `entity`, `confusable` and `second_paragraph` variants are representative
  of their technique classes. The property tests generalise beyond them.
- No contract change: no new `Stage2Verdict` member and no new `structural_flags` category, and
  `structural_flags` is an open list of category names.
- No release is cut by this epic.

## Technical Considerations

- **Decision-input tables move.** `tests/test_corpus_docs.py::test_decision_tables_are_rederived_from_the_baseline_offline_section` re-derives `docs/corpus.md`'s tables from the baseline's `offline` section, which pools only texts that reached stage 3. Every new stage-2 block shrinks that pool. Update the tables, and any prose citing their numbers (DECISIONS.md 2026-10-06, `docs/releases.md` Unreleased), in the same story. That test runs in CI.
- **Findings checks skip in worktrees.** `kit_tools/AUDIT_FINDINGS.md` is gitignored, so `tests/test_corpus_docs.py`'s findings checks skip in an execution worktree and in CI. Leave re-filing to spec 4, which runs on the owner checkout (GOTCHAS: "Gitignored files written inside an execution worktree die with the worktree").
- **Hashed files:** `stage2_structural.py` and `orchestrator.py` in every story, plus the new
  `confusables.py` in US-003. Expect three rotations in this spec. Each needs the house
  rotation record.
- **The no-re-record guarantee is mechanical.** Any change to stage-3 input turns
  `test_every_cassette_answers_every_record_*` red with `UnrecordedRecordError`. Treat that
  failure as a design error, never as a cue to re-record.
- **Baseline:** `tests/corpus/baseline.json` is exact-match
  (`tests/test_corpus_gate.py:163-166`). Each story regenerates it with
  `uv run python -m scripts.corpus.report --write-baseline`, and the PR diff is the review
  surface. Floors are raised in spec 4, not here.
- Performance: stage 2 runs about 3× (US-002), then about 5× (US-003) regex passes per text.
  `/extract` texts can reach `MAX_EXTRACTED_OUTPUT_BYTES` (2 MiB). Measure one worst-case
  input and record it.

## Related Documentation

- Corpus guide and gate: [docs/corpus.md](../../docs/corpus.md)
- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Security posture: [SECURITY.md](../arch/SECURITY.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) — "`sanitizer_revision` has deliberately diverged", "A cassette miss is the guard"
- Governance: [contract/GOVERNANCE.md](../../contract/GOVERNANCE.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Leak root causes (codebase, verified by running stage 1 and stage 2 on each leaked record, 2026-10-06):**

| Variant | Records | Root cause | Verified fix |
|---|---|---|---|
| `case` | atk-0071, 0070, 0086, 0116, 0147 | `poppy_line` :129, `system_line` :136, `hex_escape` :164, `exfil_image` :223 (plus `instructions_banner` :101, `im_start` :185, `endoftext` :191) are case-sensitive | ignore case |
| `entity` | atk-0075, 0119, 0047, 0090, 0103, 0136, 0149 (`/extract`); 0049 (`/retrieve`) | uploads are never decoded; the parser decodes one level, and double-encoding survives | `html.unescape` form |
| `second_paragraph` | atk-0053, 0152 (`/extract`); 0054, 0153 (`/retrieve`) | `disregard_instructions` :73 and `exfil_image` :223 have no DOTALL, and these routes have no collapsed form | collapse form |
| `confusable` | atk-0073, 0168, 0043, 0091, 0104, 0137, 0045, 0121, 0044 | no fold anywhere; NFC and NFKC both leave Cyrillic | confusable fold |

**Decision:** Derived scan forms for stage 2 only; stage-3 input byte-identical.
**Rationale:** Cassettes are keyed by the sha256 of stage-3 input (`scripts/corpus/replay.py:82-84`).
A miss raises `UnrecordedTextError`, and re-recording needs model weights CI doesn't have
(`docs/corpus.md:109-143`). Stage 2 never rewrites text (`stage2_structural.py:259-304`), so extra
forms change verdicts only.
**Alternatives considered:** normalise `raw_text` itself. Rejected: it forces an owner re-record of
both cassettes (owner, 2026-10-06).
**Source:** `pipeline/orchestrator.py:263,278`; `scripts/corpus/drivers.py:360-365`.

**Decision:** The builder lives in `stage2_structural.py`; the generated fold module joins
`_REVISION_SOURCES`.
**Rationale:** Anything deciding what stage 2 sees belongs in the revision, the same lesson
`url_validator.py` taught (CLAUDE.md, eighteenth rotation).
**Alternatives considered:** a version-string-only input like `idna@<version>`. Rejected because it
misses changes to the generator's filtering rules.
**Source:** `pipeline/sanitizer_revision.py:13-31`.

**Decision:** Penalty from the worst single form, flags deduplicated.
**Rationale:** The penalty is `max(-0.45, -0.15×count)` (:298). Summing across forms would change
SUSPICIOUS scores on records whose outcome otherwise didn't move.
**Source:** `pipeline/stage2_structural.py:290-304`.

**Landscape (2026-10-06, `kit_tools/.landscape_research.json`, folded here):**
- *Decode → normalise → rescan, applied before every detector,* is the 2025–26 norm; `/search`
  already does part of it. Source: <https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html> (fetched 2026-10-06).
- *TR39 skeleton is a comparison function; pin the table version.* Source: <https://www.unicode.org/reports/tr39/> (v18.0.0, 2026-08-27).
- *Library options are weak:* `disarm` (MIT; one maintainer). Source: <https://pypi.org/project/disarm/> (2026-09-26). `confusable-homoglyphs` appears archived (search snippet only: <https://snyk.io/advisor/python/confusable-homoglyphs>).
- *Character-injection evades Prompt Guard about 70% of the time* (emoji and Unicode-tag smuggling are the worst). This motivates the deferred stage-3 normalisation epic. Source: <https://arxiv.org/html/2504.11168v1> (2025-04-15).
- *Write criteria per technique class* (adaptive attacks broke 12 defences). Source: <https://arxiv.org/abs/2510.09023> (2025-10-10, search snippet only).
- *Make invisible or tag characters a counted signal, not only stripped.* Source: LLM Guard InvisibleText, <https://protectai.github.io/llm-guard/input_scanners/prompt_injection/> (2025-05-19). This is a candidate follow-up: a new signal is a contract change.

### Scope Adjustments
- Percent and base64 body decoding were deferred from US-002: they have a false-positive cost and
  no corpus leak depends on them.
- Combining-mark stripping (NFD) was deferred from US-003: no leak depends on it, and it carries a
  multilingual false-positive risk.

### Decisions Made
- See Research Findings. The owner rulings are under Clarifications.

## Clarifications

### Session 2026-10-06
- Q: Which finding groups does the epic cover? → A: structural leaks, blocked-but-leaked, hidden-markup carriers. Over-defence reduction is out.
- Q: How should the epic trade catch against false positives? → A: ratchet both ways. Catch only rises; core-genre false-positive rate never rises; pins hold.
- Q: Run landscape research? → A: yes (findings folded above).
- Q: Classifier-only categories? → A: out of scope.
- Q: Should normalised text reach stage 3? → A: no. Stage 2 only; no re-record.
- Q: How should confusables be folded? → A: a table generated from Unicode `confusables.txt`, non-ASCII only, hashed into `sanitizer_revision`.

## Open Questions

- [ ] Should a new `system_line` false positive (US-001) be narrowed or accepted? Recommendation:
      narrow it. Ratchet both ways means no core-genre false-positive rate may rise. (Not blocking.)
