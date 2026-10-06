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

# Feature Spec: Structural Scan Forms — Case, Linear Patterns, Entities and Confusables on Every Route

## Overview

Stage 2 (`pipeline/stage2_structural.py`, 24 patterns in `_PATTERNS`) applies **no
normalisation of its own**. Stage 1 (`_normalize_text`, `stage1_extraction.py:97-110`) applies
NFC, strips 9 invisible code points and collapses whitespace within lines. `/search` also scans
a decoded, control-stripped pair (`_scan_forms_for_search_text`, `orchestrator.py:940-982`).
`/retrieve` and `/extract` get nothing more.

The corpus measured four leak families:
- **`case` (5 records):** seven patterns are case-sensitive.
- **`entity` (8 records):** uploads are never decoded; the HTML parser decodes one level.
- **`second_paragraph` (4 records):** two patterns can't cross a newline.
- **`confusable` (9 records):** nothing folds look-alikes.

Validation also measured **three quadratic patterns**:
- `disregard.*instructions` (:73)
- the lazy `exfil_image` (:223)
- `envelope_breakout`'s adjacent `\s*/?\s*` (:236)

A whitespace run in any scanned form triggers the third one.

This spec closes the four leak families on every route with extra **derived scan forms**, and
first makes every pattern linear. Stage-3 input stays byte-identical, so the replayed cassettes
need no re-recording.

## Goals

- All 26 attack records in the Research Findings leak table are `blocked` or `flagged` in the
  regenerated `tests/corpus/baseline.json` on every route where they leaked.
- Class-level property tests show that every one of the 24 patterns catches its probe
  (`scripts/corpus/vocab.py` `STAGE2_REGEX_PROBES`) in each of these variants:
  - case-varied;
  - entity-encoded at one and two levels (pinned encoders);
  - newline-split (the two gap patterns);
  - single-position confusable substitution, including both readings of the ambiguous I/l
    class.
- **Every pattern is linear:** an all-patterns adversarial sweep scales sub-quadratically
  (2 MiB time ≤ 3× 1 MiB time per pattern) and stays under a total ceiling per form.
- Zero cassette misses (both cassette files byte-unchanged). Zero core-genre benign records move
  from `passed`, and every pinned benign record's pin holds.

## User Stories

### US-001: Make the seven case-sensitive patterns case-insensitive

**Priority:** P1

**Description:** As an operator relying on stage 2, I want every structural pattern to ignore
letter case, so that re-casing a trigger cannot evade it.

**Independent Test:** The seven-pattern case test fails before the change and passes after. The
regenerated baseline shows the 5 `case` leaks caught, with no other benign movement.

**Implementation Hints:**
- **Only `pipeline/stage2_structural.py` moves**, so the reversal control reverts that file
  alone.
- The seven patterns:
  - `instructions_banner` :101
  - `poppy_line` :129, literally `^POPPY:`
  - `system_line` :136, literally `^System:`
  - `hex_escape` :164
  - `im_start` :185
  - `endoftext` :191
  - `exfil_image` :223
- Add `re.IGNORECASE`. Don't touch `base64_run` :151: its class is deliberately case-bearing.
- **Don't rename `poppy_line`.** The names are pinned by `vocab.py` `STAGE2_REGEX_NAMES`
  (:224-249) and `tests/test_corpus_lint.py:443-461`. CLAUDE.md invariant 1 forbids only *new*
  "poppy" names.
- **Decided false-positive cost** (Clarifications): a line starting `system:` or `poppy:` in any
  case now BLOCKs, including a column-0 YAML `system:` key and a `Poppy:` transcript line.
  Narrowing would reopen `atk-0070` and `atk-0086`. Pin the cost as benign unit fixtures
  asserted **BLOCKED**. If a corpus *core-genre* benign record moves, stop and raise it.
- `tests/test_corpus_attacks.py` `_is_re_cased` (:176-192) should be unaffected: it tests the
  record against the forced-IGNORECASE regex. Re-read it and the `_VARIANT_SHAPE` checks
  (:194-209) to confirm.
- Reuse the probe table pinned by `tests/test_corpus_lint.py:443-461`. Test location:
  `tests/test_stage2_structural.py`.
- Leaked records: `atk-0071`, `atk-0070`, `atk-0086`, `atk-0116`, `atk-0147`.

**Acceptance Criteria:**
- [ ] A parametrised test over exactly the seven named patterns asserts `scan_structural` gives
      each probe, rendered upper-case, lower-case and alternating-case, the same verdict as the
      unvaried probe. The test fails on the pre-story file.
- [ ] Benign unit fixtures, a column-0 YAML `system:` key and a `Poppy:` transcript line, are
      asserted BLOCKED, with a comment citing this spec's decision.
- [ ] The 5 `case` records are not `leaked` in the baseline regenerated with
      `uv run python -m scripts.corpus.report --write-baseline`.
- [ ] No core-genre benign record (`news`/`docs`/`forum`/`ecommerce`/`code`) moves from
      `passed`, and the pin tests in `tests/test_corpus_gate.py` are green.
- [ ] `git diff --exit-code tests/corpus/cassettes/` exits 0, and the cassette-replay tests are
      green.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated `offline`
      section, and `tests/test_corpus_docs.py` is green.
- [ ] The rotation is recorded in CLAUDE.md, `docs/bootstrap-notes.md` and the GOTCHAS table,
      with a read-only reversal control under default and shipped config.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: Make every stage-2 pattern linear, and let the gap patterns cross newlines

**Priority:** P1

**Description:** As an operator, I want no stage-2 pattern to be slow on hostile input, and the
two gap patterns to match across a newline at any distance, so that newline-split triggers are
caught and no page can stall the process.

**Independent Test:** The all-patterns adversarial timing sweep passes. The 4 `second_paragraph`
leaks are caught on `/retrieve` and `/extract`. No other corpus record changes verdict.

**Implementation Hints:**
- **Tempered-token rewrites**, measured 0.03–0.09 s on 2 MiB adversarial input against 0.65–3 s
  for a bounded gap:
  - `disregard_instructions`: from `disregard.*instructions` to roughly
    `disregard(?:(?!disregard)[\s\S])*?instructions`. Each attempt stops at the next start
    token. It still matches an arbitrarily long same-line or multi-line gap, so there is **no
    padding bypass**.
  - `exfil_image`: temper the same way on `![`, so that `[^\]]` and `[^)]` don't scan past the
    next `![`. Alt text may span a newline. Keep the `(?:\{\{|\$\{|%7[Bb])` tail and the
    IGNORECASE from US-001.
  - `envelope_breakout` (:236): change the adjacent `\s*/?\s*` to `\s*(?:/\s*)?`. The match set
    is the same and backtracking becomes linear.
- **All-patterns sweep** (`tests/test_stage2_complexity.py`). For **every** entry in
  `_PATTERNS`, generate adversarial inputs:
  - the pattern's start token repeated with no terminator;
  - each prefix followed by a long whitespace run;
  - mixed newlines.
  Generate each at 1 MiB and 2 MiB. Assert per pattern that 2 MiB time ≤ 3× 1 MiB time (a
  machine-independent quadratic detector), and a **total ceiling of 2 s** for all 24 patterns on
  one 2 MiB form, with a hard test timeout. 2 MiB is `MAX_EXTRACTED_OUTPUT_BYTES`, the largest
  stage-2 input. Record the measurements.
- **Line-number lookup must be linear too.** `_line_number_of` (:249-251) is O(n) per match,
  so a match-dense input is quadratic. Precompute newline offsets once per scanned text and use
  `bisect`, with the same values as today.
- **Flags identity:** records matched by a rewritten pattern may see a different `matched_text`
  span. Compare `structural_flags` and penalty for every corpus record before and after, and
  list any record whose flags change (expected: none or few) in Implementation Notes.
- **Newline test:** the `exfil_image` probe has no interior whitespace, so build a dedicated
  probe whose alt text contains spaces, and split it at each one.
- Leaked records: `atk-0053`, `atk-0152` (`/extract`), `atk-0054`, `atk-0153` (`/retrieve`).
- Only `stage2_structural.py` moves.

**Acceptance Criteria:**
- [ ] `disregard_instructions`, `exfil_image` and `envelope_breakout` are rewritten as
      described. Both gap patterns match their probe (and the constructed spaced-alt-text
      exfil probe) split by a newline at every interior whitespace position, and with a
      10,000-character same-line gap.
- [ ] The all-patterns sweep passes: each pattern's 2 MiB/1 MiB ratio is ≤ 3, and all 24
      patterns finish one 2 MiB form within 2 s. A **match-dense** 2 MiB input (thousands of
      `private_ip_href` and `base64_run` matches) is included. The numbers are recorded in
      Implementation Notes.
- [ ] Line numbers come from a precomputed newline index, and every corpus record's
      `FlaggedSpan.line_number` values are unchanged (test).
- [ ] The 4 `second_paragraph` records are not `leaked` in the regenerated baseline. No corpus
      record that matched before loses its match, and any changed `structural_flags` are listed.
- [ ] No core-genre benign record moves from `passed`, and the pin tests are green. Both
      cassette files are byte-unchanged.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated, and `tests/test_corpus_docs.py` is
      green.
- [ ] The rotation is recorded with a reversal control.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-003: A shared decoded scan form on all routes

**Priority:** P1

**Description:** As an operator, I want `/retrieve` and `/extract` to scan an entity-decoded form
of their text, so that single- and double-encoded triggers can't pass stage 2.

**Independent Test:** Drive `/retrieve` and `/extract` with the 8 `entity` leaks: all are caught.
`tests/test_search_pipeline_pins.py` is unchanged, and the timing sweep still passes with
entity-encoded whitespace input.

**Implementation Hints:**
- **Builder:** put it in `pipeline/stage2_structural.py`, which is hashed. Signature:
  `structural_scan_forms(text: str, *, html_parsed: bool) -> tuple[str, ...]`. Forms are
  deduplicated, in this order:
  1. **the as-is text;**
  2. **its decode:** `html.unescape` applied **exactly once** when `html_parsed`, or **exactly
     twice** otherwise. Then C0/C1 control-strip, then **`_normalize_text` again** (stage 1's
     whitespace collapse; expose it publicly). Re-normalising is what keeps encoded whitespace
     from becoming a long run, as `/search` already does. No fixed-point loop.
- **No truncation cap:** a cap is a padding bypass. Linear patterns (US-002) make full-length
  derived forms safe. Add entity-encoded whitespace and entity-dense inputs to the timing sweep.
- **`html_parsed`** is `content_type == "html"` at the `sanitize_and_structure` call site. The
  orchestrator already knows the content type, so no new `ExtractionResult` field is needed.
  `/extract` uploads and PDFs are `False`.
- **Control-character regex, single owner:** move `_CONTROL_CHARS_RE` (`orchestrator.py:927`)
  into `stage2_structural.py` and import it in the orchestrator. Keep one copy.
- **Combine:** `scan_structural_forms(forms) -> StructuralScanResult`, built on a public
  result-level helper `combine_scan_results(*results)` with the same rule. Spec 2 reuses it
  through a keyword-only `extra_scans` parameter on `sanitize_and_structure`.
  - The verdict is the worst across forms.
  - `flags` and `penalty` come **from the as-is form if it already has the worst verdict**,
    otherwise from the first form that reaches it.
  - So a record whose verdict doesn't move keeps **byte-identical** `structural_flags`
    (`stage4_structuring.py:166`) and penalty. Accepted: the penalty reflects one form.
- **`matched_text` is never logged or counted** (a test asserts it).
- **Wiring:** `sanitize_and_structure` (`orchestrator.py:263`) scans the forms. Stage 3 at :278
  keeps receiving `extraction.raw_text`, which is the no-re-record guarantee. `/search`'s
  `_scan_forms_for_search_text` delegates its control-strip and second decode to the shared
  helpers, and its wire form (stage-3 input, :1285-1287) stays byte-identical.
- **Corpus mirrors use the builder.** Update every one:
  - `tests/corpus_stage2.py` `stage2_forms()` and `stage2_record_hits()`;
  - `scripts/corpus/records.py` `_stage2_form()`, which **becomes a tuple of forms**, and
    `rule_sweep_stage2_clean()`;
  - `scripts/corpus/ingest/render.py` `assign_category()`;
  - `tests/test_corpus_attacks.py` at :330-342, :385, :1163, :1326 (re-check each assertion's
    premise against the union of forms).
- **Benign cost fixture:** a tutorial page that shows escaped markup (`&lt;system&gt;` as visible
  text) decodes to a tag in form 2 and now flags or blocks. Pin it as an accepted cost, like
  US-001's YAML fixture.
- Leaked records: `atk-0075`, `atk-0119`, `atk-0047`, `atk-0090`, `atk-0103`, `atk-0136`,
  `atk-0149` (`/extract`), `atk-0049` (`/retrieve`).
- Hashed files: `stage2_structural.py` and `orchestrator.py`.

**Acceptance Criteria:**
- [ ] Each of the 24 probes is caught when every character is numeric-hex entity-encoded at one
      level and at two levels, and when its punctuation is named-entity-encoded. This holds on
      `/retrieve` (HTML) and `/extract` (text). A three-level payload is asserted not caught and
      documented as an accepted gap.
- [ ] The timing sweep includes entity-encoded whitespace runs and entity-dense 2 MiB input
      through the decoded form, and passes US-002's ratio and ceiling.
- [ ] Stage 3 receives exactly `extraction.raw_text` (argument-identity test). Both cassette
      files are byte-unchanged, with zero misses (`UnrecordedTextError` would surface as
      `UnrecordedRecordError` in the drivers).
- [ ] A record whose verdict doesn't move has byte-identical `structural_flags` and penalty (test
      over all corpus records). A decoded-form-only catch takes its flags from that form (unit
      test). No log line or metric contains `matched_text` (log-capture test).
- [ ] `_CONTROL_CHARS_RE` exists once, in `stage2_structural.py`.
- [ ] All listed corpus mirrors obtain forms from the builder, and `tests/test_corpus_attacks.py`,
      `tests/test_corpus_lint.py` and `tests/test_corpus_ingest.py` are green.
- [ ] The tutorial-page benign fixture is pinned with its decided outcome.
- [ ] The 8 `entity` records are not `leaked`, no core-genre benign record moves from `passed`,
      and the pin tests are green. `tests/test_search_pipeline_pins.py` is unchanged.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated, and `tests/test_corpus_docs.py` is
      green.
- [ ] The rotation is recorded. Each hashed file is reverted alone, and a both-reverted control
      reproduces the prior value.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-004: Vendor Unicode confusables and generate the fold tables

**Priority:** P2

**Description:** As a maintainer, I want fold tables generated reproducibly from Unicode's own
data, so that confusable folding is auditable, pinned and regenerable.

**Independent Test:** `scripts/generate_confusables.py --check` reproduces the committed module
byte for byte, and the fold-table tests pass. Nothing is wired into stage 2 yet.

**Implementation Hints:**
- **Data:**
  - Vendor `confusables.txt` (UTS #39, Unicode 18.0.0, 2026-08-27,
    <https://www.unicode.org/reports/tr39/>) at `scripts/data/unicode/confusables.txt`.
  - Add a `README` there recording the URL, date and sha256.
  - `.dockerignore` already keeps `scripts/` data out of the image (guard at
    `tests/test_dockerfile.py:724`).
  - Licence: Unicode License v3. Add the attribution to `NOTICE`.
- **Generator:** `scripts/generate_confusables.py`, modelled on `scripts/export_contract.py`.
  - A `--check` mode, with a drift test like `tests/test_contract_export.py`.
  - **Refuse** input whose sha256 differs from the pinned value.
  - Output: `pipeline/confusables.py`, "generated — do not hand-edit", naming the Unicode
    version and sha. Keys and values are `\uXXXX`-escaped (ruff `RUF001`-`RUF003`), and the
    file is `ruff format`-clean.
- **Rules:**
  - Keep entries with a **single-code-point, non-ASCII source** and an all-ASCII prototype.
    `str.maketrans` keys must be single characters, so record the number of multi-code-point
    sources skipped.
  - ASCII text never changes.
- **The I/l ambiguity, two tables, no override:** TR39 maps capital-I look-alikes (Cyrillic
  `U+0406`, Greek `U+0399`, palochka `U+04C0`, and others in that prototype class) to `l`, but
  the same glyphs also read as `I`. Emit:
  - `FOLD_TABLE`, the TR39 prototypes (→ `l`);
  - `AMBIGUOUS_IL`, the set of sources in that class.
  US-005 builds a second folded form that maps `AMBIGUOUS_IL` to `i`. Neither reading breaks
  the other.
- **Don't adopt a library** (Research Findings).

**Acceptance Criteria:**
- [ ] `scripts/generate_confusables.py --check` exits 0 against the committed
      `pipeline/confusables.py`. A hermetic drift test runs it, and the generator refuses a data
      file with the wrong sha256 (test).
- [ ] Folding never changes ASCII: 500 fixed-seed strings over `chr(0)`-`chr(127)`, plus every
      benign corpus ASCII text.
- [ ] An **independent** hand-written oracle of Cyrillic and Greek look-alikes for every Latin
      letter in any probe (both cases) folds each entry to its intended letter
      case-insensitively, under `FOLD_TABLE`, or under the `i` reading for `AMBIGUOUS_IL`
      members.
- [ ] `pipeline/confusables.py` passes `ruff check` and `ruff format --check`, contains no
      literal non-ASCII character, and its header records the skipped multi-code-point count.
- [ ] `NOTICE` carries the Unicode attribution, and `scripts/data/unicode/README` records the
      URL, date and sha256.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-005: Add the confusable fold forms and hash them into the revision

**Priority:** P2

**Description:** As an operator, I want stage 2 to see look-alike characters folded to Latin on
every route, under both readings of the I/l class, so that swapping one letter for a homoglyph
cannot evade any pattern.

**Independent Test:** Requires US-003's builder and US-004's tables. The confusable property test
passes, and the 9 `confusable` leaks are caught on every route without moving `multilingual`
benign records.

**Implementation Hints:**
- **Forms:** NFKC, then `FOLD_TABLE`, applied to US-003's decoded form, then `_normalize_text`,
  appended as the third form. If the text contains any `AMBIGUOUS_IL` member, add a fourth
  form with those mapped to `i`. Deduplication drops it otherwise.
- **`/search`:** add the fold forms of each field's scan form to the loop at
  `orchestrator.py:1810-1846`, **immediately after that field's existing scan and wire
  entries**. Update `tests/corpus_stage2.py` `stage2_forms()` in the same order. Wire forms are
  unchanged.
- **Revision:**
  - `confusables.py` joins `_REVISION_SOURCES` (`pipeline/sanitizer_revision.py:13-22`),
    taking the hashed-source count from nine to ten;
  - **`unicodedata.unidata_version` joins the hashed inputs**, as `unicodedata@<version>`
    beside `idna@<version>`. NFKC's tables depend on Python's Unicode database, so a Python bump
    must rotate the revision.
  - Update `tests/test_sanitizer_revision.py` (:254, :297-306), `tests/test_governance_docs.py:773`,
    and every "nine hashed" prose site (`grep -rni "nine hashed\|nine of the\|nine sources"`).
- **Timing:** add a maximal-NFKC-expansion 2 MiB input to US-002's sweep, through the fold form.
- Record the rotation as the first to **add a hashed source and an input** since
  `url_validator.py` and `idna`.
- Leaked records:
  - page: `atk-0073`, `atk-0168`, `atk-0043`, `atk-0091`, `atk-0104`, `atk-0137`
  - text: `atk-0045`
  - search: `atk-0121`, `atk-0044`

**Acceptance Criteria:**
- [ ] For each of the 24 probes, at each Latin-letter position, each oracle look-alike (both
      readings for `AMBIGUOUS_IL`) substituted at that single position is non-CLEAN through
      `sanitize_and_structure` (`/retrieve`, `/extract`) and the `/search` field scan.
      Variants are generated in sorted order.
- [ ] The 9 `confusable` records are not `leaked` in the regenerated baseline. The
      `multilingual` benign genre and every core-genre benign record are unchanged, and the pin
      tests are green.
- [ ] `pipeline/confusables.py` is in `_REVISION_SOURCES`, and `unicodedata@<version>` is a
      hashed input (test: changing the reported version changes the revision).
      `tests/test_sanitizer_revision.py` and `tests/test_governance_docs.py` are green with
      "ten" in every prose count.
- [ ] US-002's timing sweep passes, including the maximal-NFKC-expansion input through the fold
      forms.
- [ ] Both cassette files are byte-unchanged, with zero misses. `tests/test_search_pipeline_pins.py`
      is green.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated, and `tests/test_corpus_docs.py` is
      green.
- [ ] The rotation is recorded, with controls: module removed, the input removed, and each
      edited hashed file reverted alone.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

## Edge Cases

- A literal three-level entity chain: decoded twice at most. Three levels is an asserted,
  documented gap (US-003).
- An entity decoding to a control character: stripped after decoding (US-003).
- Entity-encoded whitespace runs: re-normalised in the decoded form, never long runs (US-003).
- Empty `raw_text`: one empty form, CLEAN, no exception (US-003).
- Identical forms (plain ASCII prose): deduplicated (US-003).
- Very long single lines and repeated start tokens: linear by construction, pinned by the sweep
  (US-002).
- A fold to a multi-character prototype: allowed for single-code-point sources. Multi-code-point
  sources are skipped and counted (US-004).
- A legitimate Cyrillic or Greek page: folds to Latin-looking gibberish. The `multilingual`
  genre is the guard (US-005).
- A full-width trigger: caught by NFKC in the fold form (US-005).
- A YAML `system:` key or `Poppy:` line at column 0: BLOCKED by decision (US-001). A tutorial
  page showing escaped markup: flagged or blocked by decision (US-003).

## Out of Scope

- Changing what stage 3 classifies (owner, 2026-10-06). That is a later epic with a recording
  gate.
- Inline-tag splits and markup-consumed triggers: spec 2.
- Body-level percent or base64 decoding, typoglycemia matching, NFD combining-mark stripping,
  and three-level entity encoding.
- Reducing over-defence.

## Assumptions

- Stage 2 never rewrites text, and only verdict, categories and penalty reach the response.
- The corpus variants are representative of their technique classes; the property tests
  generalise.
- No contract change: no new `Stage2Verdict` member and no new category.
- **All rotations in this epic land in one unreleased window,** which is a single cache
  invalidation at release. Note it in the release entry.

## Technical Considerations

- **Hashed files per story:**
  - US-001 and US-002: `stage2_structural.py`.
  - US-003: `stage2_structural.py` and `orchestrator.py`.
  - US-004: none.
  - US-005: `confusables.py` joins, plus a new `unicodedata@` input, `stage2_structural.py`
    and `orchestrator.py`.
- **The no-re-record guarantee is mechanical:** a stage-3 input change raises
  `UnrecordedTextError` in the replay classifier (`scripts/corpus/replay.py`), which the drivers
  re-raise as `UnrecordedRecordError`. Treat that as a design error, never as a cue to re-record.
- **Exact-match baseline:** each story regenerates it, and the PR diff is the review surface.
  Floors are spec 4.
- **Decision-input tables move** as new blocks shrink the stage-3 pool. Update `docs/corpus.md`
  and the prose citing it in the same story (CI test).
- **Findings checks skip in worktrees** (gitignored `AUDIT_FINDINGS.md`). Re-filing is spec 4's
  owner step.

## Related Documentation

- Corpus guide and gate: [docs/corpus.md](../../docs/corpus.md)
- Generator precedent: `scripts/export_contract.py`, `tests/test_contract_export.py`
- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Security posture: [SECURITY.md](../arch/SECURITY.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Leak root causes (codebase, verified on each leaked record, 2026-10-06):**

| Variant | Records | Root cause | Fix |
|---|---|---|---|
| `case` | atk-0071, 0070, 0086, 0116, 0147 | seven case-sensitive patterns | IGNORECASE (US-001) |
| `second_paragraph` | atk-0053, 0152 (`/extract`); 0054, 0153 (`/retrieve`) | `disregard.*instructions` and `exfil_image` can't cross a newline | tempered-token rewrites (US-002) |
| `entity` | atk-0075, 0119, 0047, 0090, 0103, 0136, 0149 (`/extract`); 0049 (`/retrieve`) | uploads are never decoded; double encoding survives the parser | decoded form (US-003) |
| `confusable` | atk-0073, 0168, 0043, 0091, 0104, 0137, 0045, 0121, 0044 | no fold anywhere | fold forms (US-004/005) |

**Decision:** Derived scan forms for stage 2 only; stage-3 input byte-identical.
**Rationale:** Cassettes are keyed by the sha256 of stage-3 input (`scripts/corpus/replay.py:82-84`).
Re-recording needs weights CI doesn't have.
**Source:** `pipeline/orchestrator.py:263,278`.

**Decision:** Tempered-token patterns, not a collapse form or bounded gaps.
**Rationale:** Validation measured:
- the collapse form quadratic (15.8 s on 200 KB);
- bounded gaps at 0.65–3.07 s on 2 MiB, with a padding bypass past N;
- tempered tokens at 0.03–0.09 s with no bypass.
`envelope_breakout`'s `\s*/?\s*` was quadratic on whitespace runs (4.7 s on 40k), and
`\s*(?:/\s*)?` is linear with the same match set.
**Source:** validation rounds 1–2 (second opinion, codebase fit).

**Decision:** Re-normalise derived forms; no truncation cap.
**Rationale:** Decoding entity-encoded whitespace recreates long runs (`/search` re-normalises
for this reason). A truncation cap is a padding bypass, and linear patterns make it unnecessary.
**Source:** validation round 2.

**Decision:** Flags and penalty come from one form (as-is on ties).
**Rationale:** The wire carries one `structural_flags` entry per span (`stage4_structuring.py:166`),
so cross-form merging would move flags on unchanged records.
**Source:** `pipeline/stage2_structural.py:290-304`.

**Decision:** Decode depth per route via `content_type == "html"`.
**Rationale:** `/search`'s two levels are the parser plus one unescape (`orchestrator.py:975-978`).
**Source:** `pipeline/stage1_extraction.py:296-337`.

**Decision:** Generated tables; two fold readings for the I/l class; hash the module and
`unicodedata@<version>`.
**Rationale:**
- TR39 maps capital-I look-alikes to `l`. Overriding to `i` breaks their `l` reading, so scan
  both.
- NFKC depends on Python's Unicode database, so it is an input like `idna`.
**Source:** <https://www.unicode.org/reports/tr39/> (v18.0.0); validation rounds 1–2.

**Landscape (2026-10-06):**
- Decode, normalise, rescan:
  <https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html>.
- TR39: <https://www.unicode.org/reports/tr39/>.
- Library options: `disarm` (<https://pypi.org/project/disarm/>, 2026-09-26; one maintainer).
  `confusable-homoglyphs` appears archived (snippet).
- Prompt Guard is evaded about 70% of the time by character injection
  (<https://arxiv.org/html/2504.11168v1>), which motivates the deferred stage-3 normalisation
  epic.
- Write criteria per technique class: <https://arxiv.org/abs/2510.09023> (snippet).

### Scope Adjustments
- Round 1: collapse form replaced; US-003 split; corpus mirrors in scope.
- Round 2:
  - the old US-002 split into pattern linearity (US-002) and decoded forms (US-003);
  - bounded gaps replaced by tempered tokens;
  - `envelope_breakout` linearised;
  - an all-patterns sweep;
  - derived forms re-normalised, and the truncation cap dropped;
  - the I/l override replaced by two fold readings;
  - `unicodedata@<version>` hashed.
  Five stories.

### Decisions Made
- See Research Findings and Clarifications.

## Clarifications

### Session 2026-10-06
- Q: Which finding groups does the epic cover? → A: structural leaks, blocked-but-leaked, hidden-markup carriers. Over-defence reduction is out.
- Q: How should the epic trade catch against false positives? → A: ratchet both ways.
- Q: Run landscape research? → A: yes (folded above).
- Q: Classifier-only categories? → A: out of scope.
- Q: Should normalised text reach stage 3? → A: no. Stage 2 only; no re-record.
- Q: How should confusables be folded? → A: generated from Unicode `confusables.txt`, non-ASCII only, hashed into `sanitizer_revision`.
- Q (validation): Should `system_line` / `poppy_line` be case-insensitive despite YAML and transcript false positives? → A: yes, with the cost pinned as fixtures (planner decision under the ratchet rule; override at review if wanted).
- Q (validation): Collapse form, bounded gaps, or tempered tokens? → A: tempered tokens (measured linear, no padding bypass).
