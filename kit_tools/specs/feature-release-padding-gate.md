<!-- Template Version: 2.5.0 -->
---
feature: release-padding-gate
status: active
session_ready: true
depends_on: []
vision_ref: "T2 hardening follow-through — bound the remaining CPU costs and cut v1.3.0"
type: epic-child
size: L
epic: forage-v1-3-0-release
epic_seq: 2
epic_final: false
created: 2026-10-07
updated: 2026-10-07
---

# Feature Spec: Release Padding Gate — a Refused Look-alike Fold Blocks, at 2×

## Overview

Stage 2 scans a confusable fold of each page: `PRE_NFKC_TABLE` → NFKC → `FOLD_TABLE`, under both
readings of the I/l class (`pipeline/stage2_structural.py` `fold_scan_forms`, ~:430-497). NFKC
can expand a character up to 18× (U+FDFA), and `/retrieve` text is not capped. So a fold that
would exceed **4×** the decoded text is refused, never truncated.

Two findings remain against this design:

- **Residual 2026-10-04-060, the padding gate.** A refusal only **flags** the page:
  `encoded_payload` SUSPICIOUS (`scan_structural_forms` ~:556-575, and `/search`'s loop at
  `orchestrator.py` ~:1900-1917). An attacker forces the refusal with NFKC-expanding padding, and
  the look-alike payload is then never folded. On the TRUSTED tier stage 3 is skipped, so a flag
  is the only control left and it does not stop the page.
- **Finding 2026-10-07-002, fold cost.** At the largest accepted expansion (3.83×), a 2 MiB
  `/extract` upload builds and scans two ~8 M-character forms: **8.49 s on the CI runner**, about
  19× a plain scan, holding the GIL.

**Fix (owner ruling, 2026-10-07):**
- The refusal multiple drops to **2×**.
- A refusal is a **BLOCK** on every route and trust tier. Stage-2 BLOCK is already tier-independent
  (`orchestrator.py` ~:338 skips stage 3 on BLOCKED). `/search` omits the result.
- Ordinary text expands about 1.0–1.1× under NFKC, so no benign page is expected to move. That is
  measured, not assumed.

## Goals

- Every page whose fold would exceed 2× its decoded length is **BLOCKED** on `/retrieve`,
  `/extract` and `/search`. A unit test per route proves it, including on the TRUSTED tier.
- The worst accepted fold on a 2 MiB input takes ≤ 50% of its pre-story time on the same machine
  (complexity sweep), and the sweep's fold ceilings are re-calibrated down.
- The exact benign false-positive rate is unchanged on every (genre, route, model, config):
  `scripts.corpus.floors_diff --baseline-fpr` against the pre-story baseline reports 0 rises.
- No corpus attack record moves from caught to uncaught (`floors_diff` with baselines reports 0
  problems), and both cassettes are byte-unchanged.

## User Stories

### US-001: Lower the fold refusal multiple to 2× and make a refusal BLOCK

**Priority:** P1

**Description:** As an operator, I want a page whose look-alike fold would expand past 2× to be
blocked rather than flagged, on every route and tier, so that NFKC padding can no longer switch
the confusable scan off for a page the service still serves.

**Independent Test:** Scan a decoded text built from one U+FDFA plus look-alike payload text
(expansion > 2×) through `scan_structural_forms(structural_scan_forms(...))` and get BLOCKED. Run
the same text through `/retrieve`, `/extract` and `/search` test harnesses on the TRUSTED tier and
get a quarantined/omitted outcome.

**Implementation Hints:**
- **The refusal multiple:** `_FOLD_EXPANSION_LIMIT = 4` → `2` (`stage2_structural.py` ~:382).
  Update the comment and `fold_scan_forms`' docstring ("four times").
- **The refusal outcome:** in `scan_structural_forms` (~:556-575), a refusal now returns
  `StructuralScanResult(verdict=Stage2Verdict.BLOCKED, ...)`, keeping `expansion_refused_flag()`
  in the flags.
  - Its category stays `encoded_payload`, so no new flag category appears on the wire. Category
    names are not contract vocabulary (they are not in `contract.py`/`models.py`); confirm this
    with a grep before relying on it.
  - The penalty for a BLOCK: follow what `scan_structural` returns for a blocking match.
  - Check that `stage4_structuring.py` reads the **verdict** and does not re-derive it from
    categories (`encoded_payload` is in `_SUSPICIOUS_CATEGORIES`, not `_BLOCKING_CATEGORIES`).
- **`/search`:** `orchestrator.py` ~:1900-1917 sets `suspicious = True` on `title_fold.refused`,
  `snippet_fold.refused` and both `*_inline_forms.expansion_refused`. Each becomes the **blocked**
  outcome: the result is omitted through the existing blocked path, with the existing omission
  reason. Update the comment ("A refused fold (expansion past 4x) is flagged SUSPICIOUS").
- **`ScanForms.__next__`:** a caller that stops early (BLOCKED from an earlier form) never
  computes the fold. That is fine, because BLOCKED is the maximum.
- **Existing tests to update** (`tests/test_stage2_fold_forms.py`):
  - `test_a_refusal_is_flagged_encoded_payload_suspicious` (~:236) now asserts BLOCKED.
  - `test_a_refusal_keeps_the_flags_of_a_suspicious_as_is_form` (~:244) changes meaning; keep
    the flag-retention part.
  - `test_a_refused_page_reaches_the_sanitization_result_suspicious` (~:289) now asserts
    quarantine.
  - `test_a_ratio_under_the_limit_is_folded_normally` needs inputs under 2×.
  - `test_ten_mib_of_fdfa_is_refused_within_the_memory_budget` must still hold.
- **Complexity sweep** (`tests/test_stage2_complexity.py` ~:241-270):
  - `maximal_accepted_expansion` becomes the largest input the fold accepts at 2×: one U+FDFA plus
    16 ASCII (34/17 = 2.0, accepted because refusal is strictly `>`). Verify that arithmetic
    against the code.
  - Re-measure on this machine and the CI runner. Lower its multiple from 40 and the floor from
    `4 * _CEILING_SECONDS` to measured × ~2 headroom, and update the comment. Record pre/post
    seconds in Implementation Notes.
  - Disable GC while timing (GOTCHAS:56-64).
- **Benign measurement before changing code:**
  - Write a throwaway script (scratchpad, not committed) that computes `nfkc_total / len(decoded)`
    and the full fold ratio for every corpus record's stage-2 text, attack and benign, on every
    route form.
  - Report the maximum benign ratio. If any benign record exceeds 2.0, **stop and ask the owner**
    before proceeding. That is a decision, not a fix.
  - Record the distribution summary (max and p99, per genre, numbers only) in Implementation
    Notes.
- **Corpus:**
  - Regenerate with `uv run python -m scripts.corpus.report --write-baseline`, then
    `--write-floors`.
  - Run `uv run python -m scripts.corpus.floors_diff OLD NEW --old-baseline … --new-baseline …`
    (expect 0 problems) and `--baseline-fpr OLD NEW --exempt ben-0288,ben-0289` (expect 0 rises).
  - OLD is `git show <pre-story>:tests/corpus/...`.
  - Cassettes are keyed by the stage-3 input; a newly BLOCKED page simply skips stage 3. Both
    cassette files must be byte-unchanged.
- **Corpus hygiene:** never quote record payload text in code, tests, docs or commit messages.
  Cite ids only. New attack fixtures for the padding shape go in unit tests and are built from
  code-point escapes, as `_FOLD_SHAPES` does.
- **Rotation:** `stage2_structural.py` and `orchestrator.py` move. This is the **eighteenth
  sanitization-behaviour-changing rotation** (count the tally in GOTCHAS to confirm).
  - Measure each file reverted alone, plus an all-reverted control, under default and shipped
    config.
  - Record in `CLAUDE.md`, `docs/bootstrap-notes.md` and the GOTCHAS table and tally. Update every
    rotation count site (see `feature-release-resource-bounds.md` US-002 for the list).
- **Docs:**
  - `kit_tools/arch/SECURITY.md` "Stage 2 scan forms: measured, bounded, not exhaustive": the
    refusal is now a BLOCK at 2×.
  - `docs/corpus.md` if it mentions the refusal.
  - `docs/releases.md` Unreleased bullet.
  - A GOTCHAS entry: "a refused fold blocks; raising `_FOLD_EXPANSION_LIMIT` widens the accepted
    cost linearly, so re-run the sweep".
- **Findings** (`kit_tools/AUDIT_FINDINGS.md`, gitignored): **do not edit it in the worktree.**
  Note in Implementation Notes that 2026-10-04-060 and 2026-10-07-002 are ready to resolve; the
  owner edits the main-checkout file.

**Acceptance Criteria:**
- [ ] Before any code change, the benign fold-ratio distribution over the corpus is measured, and
      its max and p99 per genre are recorded in Implementation Notes. If any benign record
      exceeds 2.0, execution stops for an owner decision.
- [ ] `_FOLD_EXPANSION_LIMIT == 2`. A fold exceeding 2× is refused, and exactly 2× is accepted
      (boundary tests on both sides).
- [ ] `scan_structural_forms` returns BLOCKED, with the `encoded_payload` refusal flag, whenever
      the fold is refused. This holds whether the as-is form was CLEAN or SUSPICIOUS.
- [ ] `/retrieve` and `/extract` quarantine a refused page (title `None`, per ruling (m)), and
      `/search` omits a result whose title, snippet or inline form refused. Each is tested on the
      TRUSTED tier and on the default tier.
- [ ] The complexity sweep's `maximal_accepted_expansion` shape is the 2× maximum. Its ceiling is
      re-calibrated, and pre/post timings (this machine, plus CI from the PR run) are recorded,
      showing ≤ 50% of the pre-story time.
- [ ] The corpus baseline and floors are regenerated. `floors_diff` reports 0 problems and
      `--baseline-fpr` reports 0 rises. Both cassettes are byte-unchanged.
- [ ] The rotation is measured (each file reverted alone, plus an all-reverted control under
      default and shipped config) and recorded in `CLAUDE.md`, `docs/bootstrap-notes.md` and the
      GOTCHAS table. All count sites are updated.
- [ ] `SECURITY.md`, the `docs/releases.md` Unreleased section and GOTCHAS describe the 2× BLOCK.
      No corpus payload text appears in any changed file (`tests/test_corpus_docs_payloads.py`
      green).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- **Pure-ASCII text:** the fold is skipped entirely (`decoded.isascii()`), so there is never a
  refusal. Unchanged. (US-001)
- **Exactly 2.0×:** accepted. **2.0× + 1 character:** refused, so BLOCKED. (US-001)
- **The NFKC pass-one refusal versus the exact-total pass-two refusal:** both paths return the
  same refused `FoldForms`, so both BLOCK. Test each. (US-001)
- **The as-is form is already BLOCKED:** `scan_structural_forms` stops before the fold, which is
  unchanged and correct. (US-001)
- **A `/search` result where only the inline form refuses** (title and snippet scan forms
  accepted): omitted. (US-001)
- **A refusal on the TRUSTED tier:** BLOCKED, and stage 3 is skipped as for any stage-2 block.
  (US-001)

## Out of Scope

- A windowed fold that avoids refusal altogether (owner chose block at 2×, 2026-10-07).
- An absolute fold-size cap. It is dropped from the original decomposition, because a large CJK
  page is non-ASCII and folds at about 1×; an absolute cap would block it. The relative 2× bound,
  times the bounded input size, already bounds the cost.
- Normalising stage-3 input (residual 2026-10-04-061; an owner recording gate).

## Assumptions

- Benign web text NFKC-expands well under 2× (fullwidth and compatibility forms map 1:1, and
  ligatures map to 2–3 characters only for those characters). US-001 verifies this on the corpus
  before changing code.
- Blocking is not a contract change. It is a sanitizer outcome under GOVERNANCE ruling (m)
  (quarantine shape unchanged), so there is no bump and no golden.

## Technical Considerations

- `stage2_structural.py` and `orchestrator.py` are hashed. `confusables.py` and the
  `unicodedata@` input are unchanged.
- Stage-3 input is unchanged. A blocked page skips stage 3, so cassette lookups for it simply do
  not happen.
- `/search` omission must use the existing blocked-omission reason and log token. There is no new
  vocabulary.

## Related Documentation

- [SECURITY.md](../arch/SECURITY.md) — "Stage 2 scan forms: measured, bounded, not exhaustive"
- [docs/corpus.md](../../docs/corpus.md), [GOTCHAS.md](../docs/GOTCHAS.md)
- Archived `feature-structural-scan-forms.md` US-005 (the fold's origin)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** BLOCKED verdict with the existing `encoded_payload` refusal flag; no new category.
**Rationale:** Categories are not contract vocabulary. The verdict is what drives quarantine and
`/search` omission.
**Source:** `stage2_structural.py:50-66, 525-575`; `orchestrator.py:338, 1900-1917`.

**Decision:** 2× rather than an absolute cap.
**Rationale:** An absolute cap blocks large non-Latin pages. The measured 4× cost is linear, so
halving the multiple roughly halves the worst case.
**Source:** Finding 2026-10-07-002; `tests/test_stage2_complexity.py:241-270`.

### Scope Adjustments

- The fold-cap lowering moved here from spec 1, so that one rotation and one corpus measurement
  cover both the multiple and the block.

### Decisions Made

- One story, because the multiple and the block are the same code, test file and rotation.

## Clarifications

### Session 2026-10-07
- Q: What do we need to do about the look-alike padding gate? → A: Fix in v1.3.0.
- Q: The cap, and is the refusal a BLOCK everywhere? → A: 2×, block on every route and tier
  (`/search` omits).
