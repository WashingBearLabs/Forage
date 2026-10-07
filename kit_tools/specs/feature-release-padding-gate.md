<!-- Template Version: 2.5.0 -->
---
feature: release-padding-gate
status: active
session_ready: true
depends_on: [release-resource-bounds]
vision_ref: "T2 hardening follow-through — bound the remaining CPU costs and cut v1.3.0"
type: epic-child
size: L
epic: forage-v1-3-0-release
epic_seq: 2
epic_final: false
created: 2026-10-07
updated: 2026-10-07
---

# Feature Spec: Release Padding Gate — a Refused Look-alike Fold Blocks, at 2× Plus Slack

## Overview

Stage 2 scans a confusable fold of each page: `PRE_NFKC_TABLE` → NFKC → `FOLD_TABLE`, under
both readings of the I/l class (`pipeline/stage2_structural.py` `fold_scan_forms`, ~:430-497).
NFKC expands some characters up to 18× (U+FDFA), so a fold that would exceed **4×** the decoded
text is refused, never truncated.

Two findings remain:

- **Residual 2026-10-04-060.** A refusal only **flags** the page (`encoded_payload` SUSPICIOUS:
  `scan_structural_forms` ~:556-575; `/search` ~:1900-1917). Padding with expanding characters
  forces the refusal, so the look-alike payload is never folded. On `/retrieve`'s TRUSTED tier,
  stage 3 is skipped and nothing stops the page.
- **Finding 2026-10-07-002.** At the largest accepted expansion (3.83×), a 2 MiB `/extract` upload
  takes 8.49 s on the CI runner (4.10 s on the dev Mac), holding the GIL.

**Fix (owner rulings 2026-10-07):**

- **The limit becomes** `max(2 × len(decoded), len(decoded) + 256)`.
  - The constant slack keeps short real-world fields from being refused. A 6-character Arabic
    title ending in U+FDFA expands 3.83× and would otherwise be blocked.
  - Text inside the slack is still folded and scanned, so the slack opens no bypass, and the
    bound on large inputs stays linear.
- **A refusal is a BLOCK** on every route. Stage-2 BLOCK is already tier-independent
  (`orchestrator.py` ~:338 skips stage 3 on BLOCKED), and `/search` omits the result.

Validation measured the end-to-end gain honestly at 2 MiB: 4.10 s → 2.29 s (56%), because the
as-is and decoded scans and the pass-one NFKC do not shrink.

## Goals

- A decoded text whose fold would exceed `max(2n, n + 256)` is **BLOCKED** on all three routes:
  - `/retrieve` on its TRUSTED and default tiers (the residual's threat);
  - `/extract`, which is always UNTRUSTED;
  - `/search` at its fixed tier.
- A 6-character Arabic title ending in U+FDFA, built from code-point escapes, is accepted and
  folded on `/search` (benign fixture).
- **Cost.** The worst accepted fold at 2 MiB takes ≤ 65% of the pre-story worst accepted fold, on
  the same machine (pre shape: U+FDFA + 5 ASCII at 4×; post shape: 16 ASCII + U+FDFA at 2×). The
  sweep's fold ceiling is recalibrated down.
- **Benign rate.** The exact benign false-positive rate is unchanged on every
  (genre, route, model, config): `scripts.corpus.floors_diff --baseline-fpr` reports 0 rises.
- **Attacks.** No attack record moves from caught to uncaught (`floors_diff` reports 0 problems).
  Both cassettes are byte-unchanged.

## User Stories

### US-001: Limit the fold at 2× plus slack, and make a refusal BLOCK

**Priority:** P1

**Description:** As an operator, I want a page whose look-alike fold would exceed 2× (plus a
constant slack) to be blocked rather than flagged, on every route and tier. Then NFKC padding can
no longer switch the confusable scan off for a page the service still serves, while short
real-world fields still fold normally.

**Independent Test:** Scan a decoded text built from one U+FDFA plus look-alike payload text past
the limit through `scan_structural_forms(structural_scan_forms(...))` and get BLOCKED. Scan a
6-character Arabic title ending in U+FDFA and get a normal fold (not refused).

**Implementation Hints:**

**Step order matters.** Do the measurement first, before any code change.

1. **Measure before changing code.**
   - Write a throwaway scratchpad script, not committed. For every corpus record's stage-2 text
     on every route form (`/search` per field), compute `n`, the NFKC length and the full fold
     length.
   - Record per genre, numbers only: the maximum benign ratio, and the count of benign fields over
     `max(2n, n+256)`.
   - Validation found no Arabic and no U+FDF0-FDFF in the benign corpus, so zero is expected.
   - Also record the pre-story 2 MiB timing of the **pre** shape (U+FDFA + 5 ASCII, accepted at
     4×), GC disabled, median of 3.
2. **The limit.** In `fold_scan_forms` (~:442), set
   `limit = max(_FOLD_EXPANSION_LIMIT * len(decoded), len(decoded) + _FOLD_SLACK)` with
   `_FOLD_EXPANSION_LIMIT = 2` and `_FOLD_SLACK = 256`.
   - Both passes use the same `limit`: pass one checks NFKC length, pass two checks the exact
     total.
   - Update the comment (~:376-381) and the docstring ("four times").
   - Refusal stays strictly `>`.
3. **The refusal outcome.** In `scan_structural_forms` (~:556-575), a refusal returns
   `StructuralScanResult(verdict=Stage2Verdict.BLOCKED, flags=[*best.flags, expansion_refused_flag()], penalty=0.0)`.
   - Penalty `0.0` is what `scan_structural` returns for BLOCKED (~:338).
   - The category stays `encoded_payload`. Categories are not contract vocabulary (they appear in
     no `contract.py`/`models.py` enum); confirm with a grep.
   - Confirm `stage4_structuring.py` reads the **verdict** and does not re-derive it from
     categories, since `encoded_payload` is in `_SUSPICIOUS_CATEGORIES`.
4. **`/search`** (`orchestrator.py` ~:1900-1960).
   - Today the four refusal checks set `suspicious = True` **before** `blocked = False` is
     initialised, and outside the scan loop. That loop is what logs
     `search_result_omitted reason=… field=…` and increments
     `omitted_by_reason[OMIT_STRUCTURAL_BLOCKED]`.
   - Hoist `blocked = False` above the checks. On any refusal, set `blocked = True` and emit the
     **same** log line and counter increment, with `field=title` for `title_fold` or
     `title_inline_forms` and `field=snippet` for the snippet pair. Then skip the scan loop for
     that result.
   - Update the comment ("A refused fold (expansion past 4x) is flagged SUSPICIOUS").
   - **Precedence** when several sources refuse: title before snippet, then fold before inline,
     matching the scan loop's own field order. Each per-source test refuses exactly one source, so
     the expected `field` is unambiguous.
5. **Tests** (`tests/test_stage2_fold_forms.py`).
   - `test_a_ratio_under_the_limit_is_folded_normally` (~:216, `_ratio_text(3.9)`) moves under the
     new limit, for example `_ratio_text(1.9)` on a text long enough that the slack is not the
     binding term.
   - Add boundary tests with `_ratio_text`: exactly at the limit is accepted, one character over
     is refused. Cover both the 2n branch (long text) and the n+256 branch (short text).
   - `test_a_refusal_is_flagged_encoded_payload_suspicious` (~:236) becomes the BLOCKED assertion
     (rename it).
   - `test_a_refusal_keeps_the_flags_of_a_suspicious_as_is_form` (~:244) asserts BLOCKED with the
     suspicious flags retained.
   - `test_padding_does_not_bypass_the_refusal` (~:256): its "drops below four" comment becomes
     wrong. Re-derive the padded variant against 2× plus slack, and assert BLOCKED for the
     refused form.
   - `test_a_refused_page_reaches_the_sanitization_result_suspicious` (~:289) asserts
     quarantine (title `None`, ruling (m)); rename it.
   - `test_ten_mib_of_fdfa_is_refused_within_the_memory_budget` (~:284) asserts
     `verdict == SUSPICIOUS` today. It **must flip to BLOCKED**, with its memory bound unchanged.
     That makes **six** updated tests.
   - In `test_padding_does_not_bypass_the_refusal`, tighten the unpadded assertion from
     `!= CLEAN` to `== BLOCKED`.
   - **Boundary tests need an exact-length builder.**
     - `_ratio_text` only brings the ratio to *at most* the target, so add a helper that builds a
       decoded text whose NFKC (pass one) or fold (pass two) length lands exactly at
       `max(2n, n+256)`, or one character over.
     - The **pass-two** boundary needs a source that `FOLD_TABLE` maps to several characters while
       NFKC leaves it at one, so that pass one accepts and pass two refuses. Round 3 confirmed
       that U+00E6 maps to `ae` (length 2) and that 114 such NFKC-stable sources exist. Write it as
       a code-point escape.
     - The builder asserts its own postcondition: it recomputes the NFKC and fold lengths and
       checks they equal the target. A later `FOLD_TABLE` change then cannot silently turn a
       boundary test into a non-boundary one.
   - Add one `/retrieve` benign fixture at a plausible density: one U+FDFA per ~150 Arabic letters
     across a few KiB, built from code-point escapes, asserting it is folded and not refused. It
     documents the margin the 2n branch relies on.
   - Test both refusal paths: pass-one NFKC, and the pass-two exact total.
6. **Route tests.**
   - `/retrieve` TRUSTED and default tier, `/extract`, and `/search` with one test **per refusal
     source**: title fold, snippet fold, title inline and snippet inline.
   - Each asserts the omission log token and counter.
   - Place them beside the existing fold route test and the `/search` orchestrator tests.
7. **Sweep** (`tests/test_stage2_complexity.py` ~:241-270).
   - `maximal_accepted_expansion` becomes `"a" * 16 + "\ufdfa"`, **ASCII first**. `_fill`
     truncates the repeated unit (2097152 mod 17 = 15), and a trailing U+FDFA with fewer than 16
     ASCII after it pushes the total over 2×, so the shape would time the refusal. Validation
     reproduced this.
   - Add an assertion inside the sweep that `fold_scan_forms(large).refused is False` for that
     shape.
   - **Ceiling:** set the multiple so that `ceiling ≈ 2 × measured post-shape median` on this
     machine, keeping the `floor=` argument at its current form, and update the comment.
   - **Cost bar:** measure the pre shape (U+FDFA + 5 ASCII, run against the pre-story limit via a
     read-only copy of the old module) and the post shape **back to back in one process**. GC off,
     median of 5.
   - If the ratio lands between 65% and 70%, re-measure with 9 samples on a quiet machine and
     record both runs. If it is still over 65%, record the numbers and leave the criterion failing
     for the owner. **Never** tune the limit or the shape to pass.
   - Validation measured 56% (2.29 s / 4.10 s) and 56% (2.45 s / 4.37 s) on two runs.
8. **Corpus.**
   - Run `uv run python -m scripts.corpus.report --write-baseline`, then `--write-floors`.
   - Run `uv run python -m scripts.corpus.floors_diff OLD NEW --old-baseline … --new-baseline …`
     (expect 0 problems).
   - Run `uv run python -m scripts.corpus.floors_diff --baseline-fpr OLD NEW --exempt ben-0288,ben-0289`
     (expect 0 rises). OLD is `git show <pre-story>:tests/corpus/…`.
   - A newly BLOCKED page skips stage 3. Both cassette files must be byte-unchanged.
   - **If the FPR check reports a rise:** do not add exemptions and do not tune the limit. Record
     the record ids and numbers in Implementation Notes and leave the story failing. Guarded mode
     then pauses for the owner. Spec 3 depends on this spec, so the release waits for that
     decision.
9. **Rotation.** `stage2_structural.py` and `orchestrator.py` move. This is a
   sanitization-behaviour-changing rotation; count the GOTCHAS tally for its ordinal. Follow the
   **Rotation record procedure** in `feature-release-resource-bounds.md` → Technical
   Considerations:
   - read-only reversal of each file alone, plus an all-reverted control, under default and
     shipped config;
   - records in `CLAUDE.md`, `docs/bootstrap-notes.md`, and the GOTCHAS table and tally;
   - count sites at GOTCHAS, DEPLOYMENT:125, TROUBLESHOOTING:804 (×2), SERVICE_MAP:210,
     CODE_ARCH:499, DECISIONS:1154/1163, `CLAUDE.md`, and `docs/releases.md` Unreleased.
10. **Docs.**
    - `kit_tools/arch/SECURITY.md` "Stage 2 scan forms: measured, bounded, not exhaustive": the
      refusal is a BLOCK at `max(2n, n+256)`, and the slack is not a bypass.
    - `docs/corpus.md` if it mentions the refusal.
    - The `docs/releases.md` Unreleased bullet.
    - A GOTCHAS entry: "a refused fold blocks; raising `_FOLD_EXPANSION_LIMIT` or `_FOLD_SLACK`
      widens the accepted cost, so re-run the sweep, and keep the ASCII-first maximal shape".
11. **Corpus hygiene.** Never quote record payload text. New fixtures are built only from
    code-point escapes (`_FOLD_SHAPES` is the pattern).
12. **Findings.** Do not edit `kit_tools/AUDIT_FINDINGS.md` (it is gitignored and owner-only).
    List 2026-10-04-060 and 2026-10-07-002 as ready to resolve in Implementation Notes.

**Acceptance Criteria:**
- [ ] The pre-change measurement (benign ratio maximum and over-limit count per genre, and the
      pre-shape 2 MiB timing) is recorded in Implementation Notes before the first code commit.
- [ ] The limit is `max(2 * n, n + 256)`. Boundary tests show that exactly-at-limit is accepted
      and one character over is refused, on both the 2n branch and the n+256 branch, through
      both the pass-one and the pass-two refusal paths.
- [ ] `scan_structural_forms` returns BLOCKED, with penalty `0.0` and the `encoded_payload`
      refusal flag (plus any suspicious flags already found), whenever the fold is refused. The
      six listed fold-form tests are updated or renamed: the 10 MiB test now asserts BLOCKED, and
      the unpadded padding assertion is `== BLOCKED`.
- [ ] Refused pages are quarantined or omitted on every route: `/retrieve` (TRUSTED and default
      tier) and `/extract` quarantine (title `None`). `/search` omits the result for each of the
      four refusal sources, with the existing `search_result_omitted` token, the right `field`
      value and the `omitted_by_reason` structural-blocked counter (one test each).
- [ ] A 6-character Arabic title ending in U+FDFA (code-point escapes) is folded, not refused,
      on `/search` (test).
- [ ] The sweep's `maximal_accepted_expansion` is `"a" * 16 + "\ufdfa"`, with an in-sweep
      assertion that the large input is not refused. The ceiling is about 2× the measured
      post-shape median. The back-to-back post/pre median ratio is ≤ 65%, and every run is
      recorded.
- [ ] The corpus baseline and floors are regenerated, `floors_diff` reports 0 problems and
      `--baseline-fpr` reports 0 rises. Both cassettes are byte-unchanged.
- [ ] The rotation is recorded per the procedure: each file reverted alone plus an all-reverted
      control, under default and shipped config. Every count site in step 9 shows the new
      ordinal, and a grep for the previous ordinal word over those files finds no stale
      current-count hit.
- [ ] `SECURITY.md`, `docs/releases.md` Unreleased and GOTCHAS describe the rule.
      `tests/test_corpus_docs_payloads.py` passes, and new fixtures use code-point escapes only.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- **Pure ASCII.** The fold is skipped (`decoded.isascii()`), so it is never refused. (US-001)
- **Short fields.** Inputs below 256 characters can expand up to `n + 256` before refusal.
  (US-001)
- **Exactly at the limit** is accepted. One character over is refused, so BLOCKED. (US-001)
- **Pass-one versus pass-two refusal.** Both return a refused `FoldForms`, and both BLOCK.
  (US-001)
- **As-is form already BLOCKED.** The scan stops before the fold, as it does today. (US-001)
- **`/search` where only the inline form refuses.** The result is omitted with that field's name.
  (US-001)
- **`/retrieve` TRUSTED tier refusal.** BLOCKED, and stage 3 is skipped as for any stage-2 block.
  (US-001)

## Out of Scope

- A windowed fold (owner chose block, 2026-10-07).
- An absolute fold-size cap. It would block large non-Latin pages.
- Normalising stage-3 input (residual 2026-10-04-061, an owner recording gate).

## Assumptions

- Benign web text expands well under 2× beyond the 256-character slack. US-001 measures this on
  the corpus first.
- Blocking is a sanitizer outcome under GOVERNANCE ruling (m) (the quarantine shape is
  unchanged). There is no bump and no golden; spec 3's 1.4.0 entry may mention it in one
  sentence.

## Technical Considerations

- `stage2_structural.py` and `orchestrator.py` are hashed. `confusables.py` and the `unicodedata@`
  input are unchanged.
- Stage-3 input is unchanged, and blocked pages skip stage 3.
- `/search` omission uses the existing reason, token and counter. No new vocabulary.

## Related Documentation

- [SECURITY.md](../arch/SECURITY.md), [docs/corpus.md](../../docs/corpus.md),
  [GOTCHAS.md](../docs/GOTCHAS.md)
- Archived `feature-structural-scan-forms.md` US-005

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** The limit is `max(2n, n + 256)`, not pure 2×.
**Rationale:** Short `/search` fields with everyday Arabic ligatures (U+FDFA expands 3.83× in a
6-character title; U+FDFB 2.17×) would be blocked. The benign corpus has no such code points, so
its gate cannot see this. A constant slack keeps the cost linear and opens no bypass.
**Source:** Second-opinion prototype, 2026-10-07; owner ruling, same day.

**Decision:** The maximal accepted shape is ASCII first.
**Rationale:** `_fill` truncation leaves a trailing U+FDFA, which refuses the U+FDFA-first unit at
both sweep sizes.
**Source:** Second-opinion reproduction, 2026-10-07.

**Decision:** The cost target is ≤ 65%, not 50%.
**Rationale:** Measured 56% (4.10 s → 2.29 s). The as-is and decoded scans and pass-one NFKC do
not halve.
**Source:** Codebase-fit and second-opinion measurements, 2026-10-07.

### Scope Adjustments

- The fold-cap lowering moved here from spec 1, so the multiple and the block share one rotation.
- Round 2 updated the tests and the measurement: the 10 MiB test flips to BLOCKED, the
  exact-length and pass-two boundary builders were added, the ceiling formula and the 65-70% rule
  were set, and `depends_on` now names spec 1 (rotation procedure and ordinal).
- On the worker path, spec 1 US-005 carries `fold_refused` in the frame and the parent re-emits
  the token. This spec changes only the verdict a refusal produces.
- Validation round 1 added: the slack, the shape fix, the per-route tier wording, the explicit
  `/search` omission mechanics, and the penalty value.

### Decisions Made

- Kept as one story, with an ordered step list. The measurement comes first; the FPR gate fails
  the story rather than pausing mid-session.

## Clarifications

### Session 2026-10-07
- **Q:** What do we do about the look-alike padding gate? **A:** Fix it in v1.3.0.
- **Q:** The cap, and BLOCK everywhere? **A:** 2×, block on every route and tier (`/search`
  omits).
- **Q:** Pure 2× or 2× plus slack? **A:** 2× + 256-character slack.
