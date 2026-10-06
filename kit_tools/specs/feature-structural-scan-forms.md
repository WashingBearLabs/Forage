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

# Feature Spec: Structural Scan Forms — Case, Entities, Newline Gaps and Confusables on Every Route

## Overview

Stage 2 (`pipeline/stage2_structural.py`, 24 patterns in `_PATTERNS`) applies **no
normalisation of its own**. It matches the patterns directly against `ExtractionResult.raw_text`.

- **Stage 1** (`_normalize_text`, `stage1_extraction.py:97-110`) applies NFC, strips 9 invisible
  code points and collapses whitespace within lines.
- **`/search`** additionally scans a decoded, control-stripped pair of forms
  (`_scan_forms_for_search_text`, `orchestrator.py:940-982`).
- **`/retrieve` and `/extract`** get nothing more.

The corpus measured the cost:
- **`case` (5 records):** seven patterns are case-sensitive.
- **`entity` (8 records):** `/extract` uploads are never decoded, and the `/retrieve` HTML
  parser decodes one level.
- **`second_paragraph` (4 records):** `disregard_instructions` and `exfil_image` cannot cross
  a newline.
- **`confusable` (9 records):** nothing folds look-alike characters on any route.

This spec closes all four on every route by giving stage 2 extra **derived forms** of the same
text. Stage-3 input stays byte-identical, so the replayed cassettes need no re-recording. The
newline-gap leak is closed by **bounding the two patterns' gaps** rather than by a whitespace
collapse form, which validation measured as quadratic: 15.8 s on 200 KB.

## Goals

- All 26 attack records in the Research Findings leak table (`case`, `entity`,
  `second_paragraph`, `confusable`) are `blocked` or `flagged` in the regenerated
  `tests/corpus/baseline.json` on every route where they leaked.
- Class-level property tests show that every one of the 24 patterns catches its registered probe
  (`scripts/corpus/vocab.py` `STAGE2_REGEX_PROBES`) in each of these variants:
  - case-varied;
  - entity-encoded at one and two levels (pinned encoders);
  - newline-split (the two gap patterns);
  - single-position confusable substitution.
- An adversarial 2 MiB input scans in bounded time on every form: no stage-2 form scan exceeds
  the ceiling stated in US-002.
- Zero cassette misses: both cassette files are byte-unchanged. Zero core-genre benign records
  move from `passed`, and every pinned benign record's pin holds.

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
  - `instructions_banner` :101 (no IGNORECASE)
  - `poppy_line` :129, literally `^POPPY:`, MULTILINE only
  - `system_line` :136, literally `^System:`, MULTILINE only
  - `hex_escape` :164 (lowercase `\x`)
  - `im_start` :185
  - `endoftext` :191
  - `exfil_image` :223 (lowercase `https?`)
- Add `re.IGNORECASE`. Don't touch `base64_run` :151: its character class is deliberately
  case-bearing.
- **Don't rename `poppy_line`.** The names are pinned by `scripts/corpus/vocab.py`
  `STAGE2_REGEX_NAMES` (:224-249) and `tests/test_corpus_lint.py:443-461`. CLAUDE.md
  invariant 1 forbids only *new* "poppy" names.
- **Decided false-positive cost (owner, 2026-10-06; see Clarifications):** a line starting with
  `system:` or `poppy:` in any case now BLOCKs. That includes a YAML key `system:` at column 0
  and a transcript line `Poppy:`. Narrowing it would reopen `atk-0070` and `atk-0086`.
  - Pin the cost as explicit benign unit fixtures asserted **BLOCKED**, so it is a recorded
    decision, not an accident.
  - The corpus ratchet still applies: if a corpus *core-genre* benign record moves, stop and
    raise it rather than narrow silently.
- `tests/test_corpus_attacks.py` `_is_re_cased` (:176-192) defines a `case` record as one that
  matches only with IGNORECASE forced. Re-read it and the `_VARIANT_SHAPE` assertions
  (:194-209), and update their premise to "re-cased relative to the probe" if needed.
- Reuse the existing probe table: `tests/test_corpus_lint.py:443-461` already pins `NAMES` and
  `PROBES` alignment.
- Test location: `tests/test_stage2_structural.py`.
- Leaked records: `atk-0071` (`poppy_line`), `atk-0070` and `atk-0086` (`system_line`),
  `atk-0116` (`hex_escape`), `atk-0147` (`exfil_image`).

**Acceptance Criteria:**
- [ ] A parametrised test over exactly the seven named patterns asserts `scan_structural` gives
      each probe, rendered upper-case, lower-case and alternating-case, the same verdict as the
      unvaried probe. The test fails on the pre-story file.
- [ ] Benign unit fixtures, a column-0 YAML `system:` key and a `Poppy:` transcript line, are
      asserted BLOCKED, with a comment citing this spec's decision.
- [ ] `atk-0071`, `atk-0070`, `atk-0086`, `atk-0116` and `atk-0147` are not `leaked` in the
      baseline regenerated with `uv run python -m scripts.corpus.report --write-baseline`.
- [ ] No core-genre benign record (`news`/`docs`/`forum`/`ecommerce`/`code`) moves from
      `passed` in the regenerated baseline, and `tests/test_corpus_gate.py` pin tests are green.
- [ ] Both files under `tests/corpus/cassettes/` are byte-unchanged (`git diff --exit-code
      tests/corpus/cassettes/`), and the cassette-replay tests are green.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated baseline's
      `offline` section, and `tests/test_corpus_docs.py` is green.
- [ ] The `sanitizer_revision` rotation is recorded in CLAUDE.md, `docs/bootstrap-notes.md` and
      the `kit_tools/docs/GOTCHAS.md` table, with a read-only reversal control reproducing the
      prior value under default and shipped config.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: Shared decoded scan forms and bounded newline gaps on all routes

**Priority:** P1

**Description:** As an operator, I want `/retrieve` and `/extract` to scan an entity-decoded form,
and the two non-DOTALL patterns to match across a bounded newline gap, so that double-encoded
entities and newline-split triggers can't pass stage 2. I also want an adversarial page not to be
able to stall it.

**Independent Test:** Drive `/retrieve` and `/extract` through the corpus drivers with the 8
`entity` and 4 `second_paragraph` leaks: all are caught. The adversarial timing test passes, and
`tests/test_search_pipeline_pins.py` is unchanged.

**Implementation Hints:**
- **Bounded gaps.** Rewrite `disregard_instructions` (:73, `disregard.*instructions`) and
  `exfil_image` (:223, `!\[.*?\]\(https?://[^)]*(...)`) with **bounded, newline-crossing**
  quantifiers. For example:
  - `disregard[\s\S]{0,N}?instructions`
  - `!\[[^\]]{0,N}\]\(https?://[^)]{0,M}(?:…)`
  Choose N and M so **no corpus record loses a match** (the baseline diff proves it), and
  record them.
  - This replaces the planned collapse form. Every other multi-token pattern already crosses
    newlines through `\s+` or `[^)]*` (validation, second opinion).
- **Builder.** Put it in `pipeline/stage2_structural.py`, which is already hashed. A new module
  deciding what stage 2 sees would otherwise have to join `_REVISION_SOURCES`.
  - Signature: `structural_scan_forms(text: str, *, html_parsed: bool) -> tuple[str, ...]`.
  - Forms are deduplicated, in this order:
    1. **the as-is text;**
    2. **its decode:** `html.unescape` applied **exactly once** when `html_parsed=True` (the
       parser already decoded one level, giving two effective levels, the depth `/search` has),
       or **exactly twice** when `False` (PDF and upload text). Then strip C0/C1 control
       characters, mirroring `orchestrator.py:927,976,980`. No fixed-point loop.
  - US-004 appends the folded form.
- **Size cap:** a derived form longer than 4× its input (the `/search` parser-input precedent)
  is truncated to that length and scanned. Log the closed WARNING token
  `stage2_scan_form_truncated` with lengths only, never text. The as-is form is always scanned
  in full.
- **Combine.** `scan_structural_forms(forms) -> StructuralScanResult`:
  - The verdict is the worst across forms (BLOCKED > SUSPICIOUS > CLEAN).
  - `flags` and `penalty` come **from the as-is form if it already has the worst verdict**,
    otherwise from the first form that reaches it.
  - So a record whose verdict doesn't move keeps **byte-identical** `structural_flags`
    (`stage4_structuring.py:166`) and penalty. Accepted: a penalty reflects one form's spans.
- **`matched_text` is never logged or counted.** Assert it in a test. Derived forms put decoded
  attacker text in it.
- **Wiring.** `sanitize_and_structure` (`orchestrator.py:263`) switches to
  `scan_structural_forms(structural_scan_forms(extraction.raw_text, html_parsed=…))`.
  - Stage 3 at :278 keeps receiving `extraction.raw_text`. That is the no-re-record guarantee.
  - `html_parsed` is true for HTML pages and false for PDF and upload. Derive it at the call
    site from the content type the orchestrator already knows, or from a new
    `ExtractionResult` field with a default (`pipeline/pdf_subprocess.py:140,260` constructs
    `ExtractionResult` too).
- **`/search`.** Make `_scan_forms_for_search_text` (:940-982) delegate its control-strip and
  second-level decode to the shared helpers, keeping its wire form (stage-3 input,
  :1285-1287) byte-identical. The six-entry field loop (:1810-1846) stays as is: its scan/wire
  pair already covers this story's variants. `tests/test_search_pipeline_pins.py` is the guard.
- **Corpus mirrors must use the builder.** `tests/corpus_stage2.py` `stage2_forms()` and
  `stage2_record_hits()`, `scripts/corpus/records.py` `_stage2_form()` and
  `rule_sweep_stage2_clean()`, and `scripts/corpus/ingest/render.py` `assign_category()` all
  hand-mirror "what stage 2 sees" and scan only the as-is form. Point them at the shared
  builder through a public name, so lint and the "stage two never sees it" assertions stay
  truthful.
- Stage 2 runs off-loop via `asyncio.to_thread` (:263), but the GIL means a slow regex still
  stalls the process. That is why the gaps are bounded.
- Leaked records:
  - `entity`: `atk-0075`, `atk-0119`, `atk-0047`, `atk-0090`, `atk-0103`, `atk-0136`,
    `atk-0149` (`/extract`), `atk-0049` (`/retrieve`)
  - `second_paragraph`: `atk-0053`, `atk-0152` (`/extract`), `atk-0054`, `atk-0153`
    (`/retrieve`)
- Hashed files that move: `stage2_structural.py` and `orchestrator.py`.

**Acceptance Criteria:**
- [ ] Both newline-gap patterns match their probe split by a newline at every interior
      whitespace position, through `sanitize_and_structure`, on `/retrieve` and `/extract`
      (parametrised).
- [ ] An adversarial **single-line** 2 MiB input (repeated `disregard` with no terminator;
      repeated `![` with no `)`) scans through every form in under 2 s per pattern on the test
      host, with a hard test timeout. The measured times are recorded in Implementation Notes.
- [ ] Each of the 24 probes is caught when every character is encoded as a numeric-hex entity,
      at one level and at two levels, and when its punctuation is named-entity-encoded. This
      holds on `/retrieve` (HTML) and `/extract` (text). A three-level payload is asserted
      **not** caught and documented as an accepted out-of-scope gap.
- [ ] Stage 3 receives exactly `extraction.raw_text` (argument-identity test). Both cassette
      files are byte-unchanged, with zero misses.
- [ ] A record whose verdict doesn't move has byte-identical `structural_flags` and penalty
      before and after (test over the corpus records). A derived-form-only catch takes its
      flags from that form (unit test).
- [ ] A derived form over 4× its input is truncated and logged with
      `stage2_scan_form_truncated` (lengths only). No log line or metric contains
      `matched_text` (log-capture test).
- [ ] `tests/corpus_stage2.py`, `scripts/corpus/records.py` and `scripts/corpus/ingest/render.py`
      obtain forms from the shared builder, and `tests/test_corpus_attacks.py`,
      `tests/test_corpus_lint.py` and `tests/test_corpus_ingest.py` are green.
- [ ] The 12 `entity` and `second_paragraph` records are not `leaked` in the regenerated
      baseline. No corpus record that matched before loses its match (baseline diff), no
      core-genre benign record moves from `passed`, and the pin tests are green.
- [ ] `tests/test_search_pipeline_pins.py` is unchanged and green.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated `offline`
      section, and `tests/test_corpus_docs.py` is green.
- [ ] The rotation is recorded (CLAUDE.md, `docs/bootstrap-notes.md`, GOTCHAS table). Each
      hashed file is reverted alone, and a both-reverted control reproduces the prior value.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-003: Vendor Unicode confusables and generate the fold table

**Priority:** P2

**Description:** As a maintainer, I want a fold table generated reproducibly from Unicode's own
data, so that confusable folding is auditable, pinned and regenerable rather than hand-curated.

**Independent Test:** `scripts/generate_confusables.py --check` reproduces the committed module
byte for byte from the vendored data, and the fold-table unit tests pass. Nothing is wired into
stage 2 yet.

**Implementation Hints:**
- **Data:**
  - Vendor `confusables.txt` (UTS #39, Unicode 18.0.0, 2026-08-27,
    <https://www.unicode.org/reports/tr39/>) at `scripts/data/unicode/confusables.txt`.
  - Add a `README` there recording the retrieval URL, date and sha256.
  - Make sure `.dockerignore` keeps `scripts/` data out of the image.
  - Licence: Unicode License v3. Add the attribution to `NOTICE`.
- **Generator:** `scripts/generate_confusables.py`, modelled on `scripts/export_contract.py`.
  - A `--check` mode, with a drift test like `tests/test_contract_export.py`.
  - **Refuse** input whose sha256 differs from the pinned value.
  - Output: `pipeline/confusables.py`, "generated — do not hand-edit", naming the Unicode
    version and source sha256. Keys and values are `\uXXXX`-escaped (ruff `RUF001`-`RUF003`
    flag literal confusables) and the file is `ruff format`-clean.
- **Rules:**
  - Keep entries whose **source is non-ASCII** and whose prototype is entirely ASCII, so ASCII
    text never changes.
  - Apply a small, reviewed **override table** for prototype classes whose ASCII
    representative isn't the letter a reader intends. In `confusables.txt`, capital-I
    look-alikes (Cyrillic `U+0406`, Greek capital iota `U+0399`, palochka `U+04C0`) fold to
    lowercase `l`, so patterns containing `i` would miss them. Override those to `i`, and
    document each override with its reason. Stage-2 patterns are case-insensitive after
    US-001, so lowercase targets are fine.
  - Expose `FOLD_TABLE` (a `str.maketrans` mapping) and `UNICODE_VERSION`.
- **Don't adopt a library.** `disarm` (MIT, 2026-09-26) has one maintainer and unverified
  aarch64 wheels; the image is multi-arch. `confusable-homoglyphs` appears archived, and PyICU
  is a heavy native build (Research Findings).

**Acceptance Criteria:**
- [ ] `scripts/generate_confusables.py --check` exits 0 against the committed
      `pipeline/confusables.py`. A hermetic drift test runs it, and the generator refuses a data
      file with the wrong sha256 (test).
- [ ] Folding never changes ASCII: for 500 strings over `chr(0)`-`chr(127)` with a fixed seed,
      plus every benign corpus ASCII text, `s.translate(FOLD_TABLE) == s`.
- [ ] An **independent** oracle, a hand-written list of Cyrillic and Greek look-alikes for every
      Latin letter that appears in any probe (upper and lower case, including the capital-I
      class), folds each entry to its intended Latin letter case-insensitively. This list is
      not derived from the table.
- [ ] `pipeline/confusables.py` passes `ruff check` and `ruff format --check`, and contains no
      literal non-ASCII character.
- [ ] `NOTICE` carries the Unicode attribution. `scripts/data/unicode/README` records the URL,
      date and sha256. The data file is not in the Docker image (`tests/test_dockerfile.py` or
      a `.dockerignore` assertion).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-004: Add the confusable fold as a scan form and hash it into the revision

**Priority:** P2

**Description:** As an operator, I want stage 2 to see look-alike characters folded to Latin on
every route, so that swapping one letter for a homoglyph cannot evade any pattern.

**Independent Test:** Requires US-002's builder and US-003's table. The confusable property test
passes, and the 9 `confusable` leaks are caught on every route without moving `multilingual`
benign records.

**Implementation Hints:**
- **The form:** NFKC, then `FOLD_TABLE`, applied to the decoded form from US-002's builder, and
  appended as the last form. Subject to the same 4× size cap.
- **`/search`:** also scan the fold of each field's scan form. Add it to the field loop at
  `orchestrator.py:1810-1846` in a fixed position, and update `tests/corpus_stage2.py`
  `stage2_forms()` to match. `/search` wire forms (stage-3 input) are unchanged.
- **Revision:** add `confusables.py` to `_REVISION_SOURCES` (`pipeline/sanitizer_revision.py:13-22`),
  which takes the hashed-source count from **nine to ten**. Update:
  - `tests/test_sanitizer_revision.py` (:254, :297-306);
  - `tests/test_governance_docs.py:773`, which asserts the count word in prose;
  - every prose site that says "nine hashed". Find them with
    `grep -rn "nine hashed\|nine of the\|nine sources" CLAUDE.md .github contract kit_tools docs`.
- Record the rotation as the first that **adds a hashed source** since `url_validator.py`
  (CLAUDE.md, eighteenth rotation).
- Leaked records:
  - page: `atk-0073`, `atk-0168`, `atk-0043`, `atk-0091`, `atk-0104`, `atk-0137`
  - text: `atk-0045`
  - search: `atk-0121`, `atk-0044`

**Acceptance Criteria:**
- [ ] For each of the 24 probes, at each Latin-letter position, each look-alike from US-003's
      independent oracle substituted at that single position is non-CLEAN through
      `sanitize_and_structure` (`/retrieve`, `/extract`) and the `/search` field scan.
      Variants are generated in sorted order, so the test is deterministic.
- [ ] The 9 `confusable` records are not `leaked` in the regenerated baseline. The
      `multilingual` benign genre's outcomes and every core-genre benign record are unchanged,
      and the pin tests are green.
- [ ] `pipeline/confusables.py` is in `_REVISION_SOURCES`. `tests/test_sanitizer_revision.py`
      and `tests/test_governance_docs.py` are green with "ten" in every prose count.
- [ ] Stage-2 time on the adversarial 2 MiB input, now including the fold form, stays within
      US-002's ceiling (same test).
- [ ] Both cassette files are byte-unchanged with zero misses. `tests/test_search_pipeline_pins.py`
      is green.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated `offline`
      section, and `tests/test_corpus_docs.py` is green.
- [ ] The rotation is recorded, with controls: module removed, plus each edited hashed file
      reverted alone.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

## Edge Cases

- A literal three-level entity chain: decoded twice at most, never looped. Three levels is an
  asserted, documented gap (US-002).
- An entity that decodes to a control character: stripped after decoding, as on `/search`
  (US-002).
- Empty `raw_text`: the builder returns one empty form, giving CLEAN and no exception (US-002).
- Identical forms (plain ASCII prose): deduplicated, so each is scanned once (US-002).
- A derived form expanding past 4× (NFKC of ligature-dense text, entity-dense text): truncated
  and logged. The as-is form is still scanned in full (US-002, US-004).
- Very long lines: the bounded gaps keep both patterns linear-bounded (US-002).
- A fold to a multi-character prototype (`ﬁ` → `fi`): allowed. Scan-only forms aren't mapped
  back to the wire (US-004).
- A legitimate Cyrillic or Greek page: folds to Latin-looking gibberish. The `multilingual`
  genre is the guard (US-004).
- A full-width trigger: caught by NFKC in the fold form (US-004).
- A YAML `system:` key or `Poppy:` transcript line at column 0: BLOCKED by decision (US-001).

## Out of Scope

- Changing what stage 3 classifies (owner, 2026-10-06). That is a later epic with an owner
  recording gate.
- Inline-tag splits and markup-consumed triggers: spec 2.
- Body-level percent or base64 decode-then-rescan, typoglycemia or fuzzy matching, and NFD
  combining-mark stripping. These are candidate follow-ups with their own false-positive
  measurement.
- Three-or-more-level entity encoding (an asserted gap).
- Reducing over-defence on `security_prose` / `over_defence_probe`.

## Assumptions

- Stage 2 never rewrites text, and only verdict, categories and penalty reach the response. So
  derived forms change outcomes, never served bytes.
- The corpus variants are representative of their technique classes. The class-level property
  tests generalise beyond them.
- No contract change: no new `Stage2Verdict` member and no new category.
- **All rotations in this epic land in one unreleased window,** so consumers see a single cache
  invalidation at the next release, not one per story. Note this in the release entry.

## Technical Considerations

- **Hashed files per story:**
  - US-001: `stage2_structural.py` only.
  - US-002: `stage2_structural.py` and `orchestrator.py`.
  - US-003: none. The generated module is not yet referenced by the revision.
  - US-004: `confusables.py` joins the revision, plus `stage2_structural.py` and
    `orchestrator.py`.
- **The no-re-record guarantee is mechanical.** Any change to stage-3 input turns the
  cassette-replay test red with `UnrecordedRecordError`. Treat that failure as a design error,
  never as a cue to re-record.
- **The baseline is exact-match** (`tests/test_corpus_gate.py:163-166`). Each story regenerates
  it with `uv run python -m scripts.corpus.report --write-baseline`, and the PR diff is the
  review surface. Floors are raised in spec 4.
- **The decision-input tables move.**
  `tests/test_corpus_docs.py::test_decision_tables_are_rederived_from_the_baseline_offline_section`
  re-derives `docs/corpus.md`'s tables from the `offline` section, which holds only texts that
  reached stage 3. New stage-2 blocks shrink that pool.
  - Update the tables in the same story, plus any prose citing their numbers
    (`kit_tools/arch/DECISIONS.md` 2026-10-06, `docs/releases.md` Unreleased).
  - The test runs in CI.
- **Findings checks skip in worktrees.** `kit_tools/AUDIT_FINDINGS.md` is gitignored, so
  `tests/test_corpus_docs.py`'s findings checks skip in an execution worktree and in CI.
  Re-filing is spec 4's owner step (GOTCHAS: "Gitignored files written inside an execution
  worktree die with the worktree").

## Related Documentation

- Corpus guide and gate: [docs/corpus.md](../../docs/corpus.md)
- Generator precedent: `scripts/export_contract.py`, `tests/test_contract_export.py`
- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Security posture: [SECURITY.md](../arch/SECURITY.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Leak root causes (codebase, verified by running stage 1 and stage 2 on each leaked record, 2026-10-06):**

| Variant | Records | Root cause | Fix |
|---|---|---|---|
| `case` | atk-0071, 0070, 0086, 0116, 0147 | `poppy_line` :129, `system_line` :136, `hex_escape` :164 and `exfil_image` :223 are case-sensitive (and so are `instructions_banner` :101, `im_start` :185 and `endoftext` :191) | IGNORECASE (US-001) |
| `entity` | atk-0075, 0119, 0047, 0090, 0103, 0136, 0149 (`/extract`); 0049 (`/retrieve`) | uploads are never decoded; the parser decodes one level, and double encoding survives | decoded form (US-002) |
| `second_paragraph` | atk-0053, 0152 (`/extract`); 0054, 0153 (`/retrieve`) | `disregard_instructions` :73 and `exfil_image` :223 can't cross a newline | bounded newline-crossing gaps (US-002) |
| `confusable` | atk-0073, 0168, 0043, 0091, 0104, 0137, 0045, 0121, 0044 | no fold anywhere; NFC and NFKC both leave Cyrillic | fold form (US-003/004) |

**Decision:** Derived scan forms for stage 2 only; stage-3 input byte-identical.
**Rationale:** Cassettes are keyed by the sha256 of stage-3 input (`scripts/corpus/replay.py:82-84`).
A miss raises `UnrecordedTextError`, and re-recording needs model weights CI doesn't have.
**Alternatives considered:** normalising `raw_text`. Rejected: it forces a re-record (owner,
2026-10-06).
**Source:** `pipeline/orchestrator.py:263,278`; `scripts/corpus/drivers.py:360-365`.

**Decision:** Bounded gaps, not a collapse form (validation, 2026-10-06).
**Rationale:** The collapse form makes `disregard.*instructions` and the lazy exfil pattern
quadratic on one long line. The second-opinion reviewer measured 15.8 s on 200 KB and 5.8 s on
80 KB. `to_thread` doesn't help, because the regex holds the GIL. Bounded gaps fix the leak and
the cost together, with fewer forms.
**Alternatives considered:** a collapse form with a length cap. Rejected: the cap is a bypass
and the cost is still superlinear.
**Source:** `.validate_epic_feature-structural-scan-forms_6.json` (deleted after consolidation;
summary in Clarifications).

**Decision:** Flags and penalty come from one form (the as-is form on ties).
**Rationale:** The wire carries one `structural_flags` entry per span
(`stage4_structuring.py:166`). Merging across forms would change flags on records whose verdict
didn't move, and `(category, matched_text)` deduplication doesn't collapse one attack seen
encoded, decoded and folded.
**Source:** `pipeline/stage2_structural.py:290-304`.

**Decision:** The decode depth is per route.
**Rationale:** `/search`'s "two levels" are the parser's one plus one `html.unescape`
(`orchestrator.py:975-978`). On `/retrieve` HTML the parser has already decoded once, so one
unescape gives the same depth. PDF and upload text needs two.
**Source:** `pipeline/stage1_extraction.py:296-337`; `stage1_upload.py:28-75`.

**Decision:** A generated fold table with a reviewed capital-I override, joining `_REVISION_SOURCES`.
**Rationale:** `confusables.txt` maps capital-I look-alikes to `l`, which patterns containing
`i` would miss. A table-derived property test can't see that, hence the independent oracle.
**Source:** <https://www.unicode.org/reports/tr39/> (v18.0.0, 2026-08-27); validation second
opinion.

**Landscape (2026-10-06):**
- *Decode, normalise, then rescan, before every detector.* <https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html> (fetched 2026-10-06).
- *TR39 skeleton is a comparison function; pin the table version.* <https://www.unicode.org/reports/tr39/>.
- *Weak library options.* `disarm` (MIT, one maintainer): <https://pypi.org/project/disarm/> (2026-09-26). `confusable-homoglyphs` appears archived (search snippet: <https://snyk.io/advisor/python/confusable-homoglyphs>).
- *Prompt Guard is evaded about 70% of the time by character injection,* which motivates the
  deferred stage-3 normalisation epic. <https://arxiv.org/html/2504.11168v1> (2025-04-15).
- *Write criteria per technique class.* <https://arxiv.org/abs/2510.09023> (2025-10-10, snippet).
- *Counting invisible or tag characters as a signal* is a follow-up, because it is a contract
  change. <https://protectai.github.io/llm-guard/input_scanners/prompt_injection/> (2025-05-19).

### Scope Adjustments
- 2026-10-06 validation:
  - The collapse form was replaced by bounded gaps (ReDoS).
  - The old US-003 was split into US-003 (vendor and generate) and US-004 (wire and hash).
  - US-003 and US-004 were demoted to P2.
  - The corpus mirrors were brought into US-002's scope.
- Percent and base64 body decoding, and NFD combining-mark stripping, were deferred (no leak
  depends on them; false-positive risk).

### Decisions Made
- See Research Findings and Clarifications.

## Clarifications

### Session 2026-10-06
- Q: Which finding groups does the epic cover? → A: structural leaks, blocked-but-leaked, hidden-markup carriers. Over-defence reduction is out.
- Q: How should the epic trade catch against false positives? → A: ratchet both ways. Catch only rises, core-genre false-positive rate never rises, and pins hold.
- Q: Run landscape research? → A: yes (folded above).
- Q: Classifier-only categories? → A: out of scope.
- Q: Should normalised text reach stage 3? → A: no. Stage 2 only; no re-record.
- Q: How should confusables be folded? → A: a table generated from Unicode `confusables.txt`, non-ASCII only, hashed into `sanitizer_revision`.
- Q (validation): Should `system_line` / `poppy_line` be case-insensitive despite blocking YAML `system:` keys and `Poppy:` transcripts? → A: yes. Narrowing would reopen atk-0070 and atk-0086. The cost is pinned as explicit benign fixtures (planner decision under the owner's ratchet rule; override at review if wanted).
- Q (validation): Collapse form or bounded gaps? → A: bounded gaps (ReDoS measured on the collapse form).
