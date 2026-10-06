<!-- Template Version: 2.5.0 -->
---
feature: structural-closeout
status: active
session_ready: true
depends_on: [structural-wire-closure]
vision_ref: "T2.3 follow-up — close the injection corpus's structural findings"
type: epic-child
size: S
epic: forage-structural-hardening
epic_seq: 4
epic_final: true
created: 2026-10-06
updated: 2026-10-06
---

# Feature Spec: Structural Close-out — Ratchet the Floors, Re-file the Findings, Record the Epic

## Overview

Specs 1–3 each regenerate `tests/corpus/baseline.json` and record their own rotations. This spec
locks in the gains and leaves the records true:
- raise every catch floor that improved, so the next regression is red rather than silent;
- re-file `kit_tools/AUDIT_FINDINGS.md`'s corpus entries from the final baseline: resolve what
  closed, keep what remains with a reason, file the accepted residuals;
- bring the security, corpus and architecture docs up to the new stage-2 behaviour.

**It must run on the owner checkout.** `AUDIT_FINDINGS.md` is gitignored, so the findings checks
in `tests/test_corpus_docs.py` only run where the file exists. An execution worktree's copy dies
with the worktree (GOTCHAS).

## Goals

- Every `min_catch` / `min_block` floor in `tests/corpus/floors.json` whose measured value rose
  during the epic is raised to the new measurement. No floor is lowered and no `max_fpr` is
  raised. The diff is reviewed cell by cell.
- Every corpus finding 2026-10-04-004 … -025, -040, -041 and -043 … -045 is `resolved` with the
  closing spec and story, or is still `open` with a stated reason. -042 (`title_stuffing`) and
  every remaining stage-2 residual is filed as an accepted residual with its out-of-scope
  rationale.
- `tests/test_corpus_docs.py` passes on the owner checkout with `AUDIT_FINDINGS.md` present.

## User Stories

### US-001: Ratchet the corpus floors to the epic's measurements

**Priority:** P1

**Description:** As a maintainer, I want every catch floor raised to what the epic now measures,
so that a future change that reopens a closed leak fails CI instead of passing quietly.

**Independent Test:** `tests/test_corpus_gate.py` is green with the raised floors. Reverting any
one of the epic's fixes (for example spec 1 US-001's case flags) turns a floor test red.

**Implementation Hints:**
- `uv run python -m scripts.corpus.report --write-floors` scaffolds `floors.json` from the live
  report and **overwrites hand edits** (`docs/corpus.md` "The gate", :180-186). Diff it against
  the pre-epic file. Keep a scaffold change only where it **raises** a floor; restore every hand
  edit and every cell where the scaffold would lower or loosen.
- The cell set must still match the scaffold exactly (`tests/test_corpus_gate.py:363-380`).
- `test_every_measured_cell_clears_its_floor` (:264-266) is the guard.
- Prove the ratchet works: temporarily revert one fix (a read-only experiment, not committed),
  run the gate, and see red. Record the result in Implementation Notes.

**Acceptance Criteria:**
- [ ] Every floor in `tests/corpus/floors.json` is ≥ its pre-epic value, and every cell whose
      measured catch rose has its floor raised to the measurement. A test or the review
      checklist lists the raised cells.
- [ ] No `max_fpr` is raised, and no core-genre benign cell's measured FPR is above its pre-epic
      value.
- [ ] A recorded experiment shows reverting one epic fix turns `tests/test_corpus_gate.py` red.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: Re-file the findings and bring the records up to date

**Priority:** P1

**Description:** As a maintainer, I want the audit findings, docs and decision records to
describe the defences as they now are, so that the next reader neither re-fixes a closed leak
nor trusts a stage-2 guarantee that doesn't exist.

**Independent Test:** On the owner checkout with `AUDIT_FINDINGS.md` present,
`tests/test_corpus_docs.py` is green, and every in-scope finding is resolved or justified.

**Implementation Hints:**
- **`kit_tools/AUDIT_FINDINGS.md` (owner checkout only):**
  - Mark closed findings `resolved`, each with a one-line resolution naming the spec and story.
  - Records still leaking keep their finding, with the reason.
  - File the residuals:
    - `title_stuffing` without a marker (stage 3);
    - class or stylesheet hiding (no CSS evaluation);
    - attribute channels (`aria-label`, `data-*`);
    - body-level percent/base64 decoding;
    - the deferred stage-3 normalisation epic.
  - Keep the entry heading formats the tests parse: `^\*\*2026-10-04-\d{3}\*\* — …`,
    `` `cat` on `route`: `` and `Blocked-but-leaked: … `id` ``
    (`tests/test_corpus_docs.py:251-330`).
- **Docs:**
  - `docs/corpus.md` "Reading the results": the stage-2 scan forms and what each closes.
  - `kit_tools/arch/SECURITY.md`: stage-2 normalisation, the raw-markup subset, the visibility
    pass, and the residuals. Phrase it as evidence, not robustness (landscape: adaptive attacks,
    arXiv 2510.09023). `tests/test_corpus_docs.py::test_security_md_states_the_corpus_and_keeps_the_two_gaps`
    reads this file.
  - `kit_tools/arch/CODE_ARCH.md`: the scan-form builder and `confusables.py`.
  - `kit_tools/docs/GOTCHAS.md`: "derived scan forms must never change stage-3 input" and
    "regenerate `confusables.py`, never hand-edit".
  - `kit_tools/arch/DECISIONS.md`: one entry for the epic's choices (derived forms; generated
    fold table; body-only pruning; title quarantine).
  - `docs/releases.md` Unreleased: the behaviour changes.
  - `kit_tools/PRODUCT_VISION.md`: a T2.4 line or the T2.3 follow-up status.
- **Rotation prose:** confirm every "N rotations" / "N hashed sources" count across CLAUDE.md,
  GOTCHAS, TROUBLESHOOTING, DEPLOYMENT and SERVICE_MAP agrees with the last
  `docs/bootstrap-notes.md` heading. This is the house count preflight.
- `tests/test_corpus_docs.py::test_numbers_appear_only_in_the_decision_inputs_table`: new
  corpus numbers belong only in that table.

**Acceptance Criteria:**
- [ ] Each in-scope corpus finding (-004 … -025, -040, -041, -043 … -045) reads `resolved` with
      spec and story, or `open` with a reason. -042 and the five residuals above are filed as
      accepted residuals.
- [ ] `tests/test_corpus_docs.py` passes on the owner checkout with `AUDIT_FINDINGS.md` present
      (no skips).
- [ ] `docs/corpus.md`, `kit_tools/arch/SECURITY.md`, `kit_tools/arch/CODE_ARCH.md`,
      `kit_tools/docs/GOTCHAS.md`, `kit_tools/arch/DECISIONS.md`, `docs/releases.md` and
      `kit_tools/PRODUCT_VISION.md` describe the shipped behaviour and residuals.
- [ ] Every rotation and hashed-source count in CLAUDE.md, GOTCHAS, TROUBLESHOOTING, DEPLOYMENT
      and SERVICE_MAP matches the final `docs/bootstrap-notes.md` heading ordinal.
- [ ] `contract/openapi.yaml.sha256` is unchanged from the epic's start, or the change is the one
      spec 3 US-001's governance ruling recorded.
- [ ] Full test suite passes (`uv run pytest`)

## Edge Cases

- A finding whose cell partly closed (for example 2 of 3 records fixed): it stays `open` with the
  remaining ids and their reason (US-002).
- The scaffold proposes a lower floor because a cell's denominator changed (newly blocked texts
  leave the stage-3 pool): keep the old floor if the measured value still clears it. Otherwise
  stop and explain; never lower silently (US-001).
- A new cell appears in the scaffold (none expected, since no new categories or routes are
  added): investigate before accepting (US-001).

## Out of Scope

- Cutting a release. The version decision (MINOR windows) is separate.
- Fixing any residual.

## Assumptions

- Specs 1–3 left their own rotation records and baseline regenerations. This spec only verifies
  the counts agree.
- The owner runs this spec on the main checkout, not in an execution worktree, because
  `AUDIT_FINDINGS.md` must persist.

## Technical Considerations

- No hashed source moves here, so there is no rotation.
- US-001 is a test-data change (`floors.json`). US-002 is mostly docs plus the gitignored
  findings file.

## Related Documentation

- Corpus gate: [docs/corpus.md](../../docs/corpus.md) "The gate"
- Findings format: `tests/test_corpus_docs.py`
- Epic: [epic-forage-structural-hardening.md](epic-forage-structural-hardening.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Floors are ratcheted once, at the end.
**Rationale:** Each spec's improvement changes cells another spec may move again. A single
reviewed `--write-floors` diff at the end avoids churning floors three times.
**Alternatives considered:** per-story floor raises. Rejected as review noise.
**Source:** `tests/test_corpus_gate.py:264-266,363-380`; `docs/corpus.md:180-186`.

### Scope Adjustments
- None.

### Decisions Made
- This spec runs on the owner checkout (gitignored findings file).

## Clarifications

### Session 2026-10-06
- Q: How should catch be traded against false positives? → A: ratchet both ways. Catch floors
  only rise; core-genre false-positive rates never rise.

## Open Questions

- [ ] Should the corpus findings tests (`tests/test_corpus_docs.py`, filtered to
      `_MODEL_22M`) follow the default model (now the 86M)? Recommendation: switch the filter
      to `DEFAULT_MODEL_ID` in US-002, re-deriving the leaked sets for the 86M. (Not blocking:
      both models' stage-2 outcomes are identical, so structural findings don't differ.)
