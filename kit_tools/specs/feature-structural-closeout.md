<!-- Template Version: 2.5.0 -->
---
feature: structural-closeout
status: active
session_ready: true
depends_on: [structural-wire-closure]
vision_ref: "T2.3 follow-up — close the injection corpus's structural findings"
type: epic-child
size: M
epic: forage-structural-hardening
epic_seq: 4
epic_final: true
created: 2026-10-06
updated: 2026-10-06
---

# Feature Spec: Structural Close-out — Ratchet the Floors, Re-file the Findings, Record the Epic

## Overview

Specs 1–3 each regenerate `tests/corpus/baseline.json` and record their own rotations. This spec
does three things:
- locks in the gains by raising every catch floor that improved, checked mechanically;
- makes the gitignored findings file true by resolving what closed and filing the residuals;
- brings the docs and record counts up to date.

**Pre-epic reference commit: `03f7a96`** (the commit that added this epic's specs). Every
"pre-epic" comparison below means `git show 03f7a96:<path>`.

**US-002 is an owner step.** `kit_tools/AUDIT_FINDINGS.md` is gitignored. In an execution
worktree the findings checks skip, and any edit dies with the worktree (GOTCHAS). US-002 runs on
the owner's main checkout, supervised, never through `/kit-tools:execute-epic`'s worktree.

## Goals

- Every `min_catch`/`min_block` floor in `tests/corpus/floors.json`, across all
  model × config variants (22M/86M × `default`/`contiguity`), is ≥ its `03f7a96` value, and every
  `max_fpr` is ≤ its `03f7a96` value. A committed script proves it.
- `test_every_measured_cell_clears_its_floor` goes red when spec 1 US-001's fix is reverted and
  the baseline regenerated, so the ratchet demonstrably guards it.
- Every in-scope finding is `resolved`, or still `open` with a reason, and residuals are filed.
  `uv run pytest -rs tests/test_corpus_docs.py` reports **zero skipped**.
- Docs and record counts match the shipped behaviour, with no payload text in tracked files.

## User Stories

### US-001: Ratchet the corpus floors, with a mechanical monotonicity check

**Priority:** P1

**Description:** As a maintainer, I want every catch floor raised to what the epic now measures,
and a script that proves no floor loosened, so that a future change reopening a closed leak
fails CI.

**Independent Test:** `scripts/corpus/floors_diff.py` reports only tightenings between
`03f7a96` and the new `floors.json`. With spec 1 US-001 reverted and the baseline regenerated,
the floor test fails on named cells.

**Implementation Hints:**
- `uv run python -m scripts.corpus.report --write-floors` scaffolds `floors.json` from the live
  report. `build_floors()` (`scripts/corpus/report.py`, around :1138) writes
  `floor_down(round(caught/n, 4))` for minimums and `ceil_up` for `max_fpr`, on a **0.05
  grid**. A "raised" floor is therefore the measurement rounded down, not the measurement.
  - `floors.json` at `03f7a96` equals the scaffold exactly (validated: 414 values), so there
    are no hand edits to restore.
- **Add `scripts/corpus/floors_diff.py OLD NEW`.** It walks every cell and variant and exits
  non-zero on any lowered `min_catch`/`min_block`, any raised `max_fpr`, or a changed cell set,
  and prints the tightened cells. Add a unit test for it. Run it as
  `floors_diff.py <(git show 03f7a96:tests/corpus/floors.json) tests/corpus/floors.json` and
  paste the output into Implementation Notes.
- **If a variant would loosen** (for example a denominator change on a contiguity cell): keep
  the old value if the measurement still clears it. Otherwise stop and record why. Never lower
  silently.
- **Ratchet proof:**
  1. In a temporary worktree (`git worktree add`, removed afterwards; the main tree stays
     clean), revert spec 1 US-001's `stage2_structural.py` change.
  2. Regenerate the baseline.
  3. Run `uv run pytest tests/test_corpus_gate.py::test_every_measured_cell_clears_its_floor`.
  4. Record the failing cells: at least the `/search` and `/extract` cells containing
     `atk-0070`, `atk-0071` and `atk-0116`.
  The exact-match baseline test going red doesn't count. The *floor* test must.
- **Epic completion checks also live here:**
  - `git diff --exit-code 03f7a96 -- tests/corpus/cassettes/` (no re-record across the epic);
  - over-defence cells (`security_prose`, `over_defence_probe`) are not worse than at
    `03f7a96`;
  - the benign pin tests are green.

**Acceptance Criteria:**
- [ ] `scripts/corpus/floors_diff.py` exists with a unit test covering a lowered `min_catch`, a
      raised `max_fpr` and a changed cell set (all non-zero exit).
- [ ] Its run against `03f7a96` exits 0, and its output, listing every tightened cell across all
      model × config variants, is pasted in Implementation Notes.
- [ ] The recorded ratchet-proof run shows `test_every_measured_cell_clears_its_floor` failing
      on named cells with spec 1 US-001 reverted and the baseline regenerated. The temporary
      worktree is removed, and `git status` is clean afterwards.
- [ ] `git diff --exit-code 03f7a96 -- tests/corpus/cassettes/` exits 0.
- [ ] No `security_prose` or `over_defence_probe` cell's measured FPR is above its `03f7a96`
      value, and the benign pin tests in `tests/test_corpus_gate.py` are green.
- [ ] `tests/test_corpus_gate.py` passes against the new `floors.json`, including the cell-set
      equality check (:363-380).
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: Re-file the corpus findings (owner checkout, supervised)

**Priority:** P1

**Description:** As the owner, I want `AUDIT_FINDINGS.md`'s corpus entries re-derived from the
final baseline, so that the record says exactly which leaks closed, which remain, and why.

**Independent Test:** On the owner checkout, `uv run pytest -rs tests/test_corpus_docs.py`
passes with **zero skipped**, and Implementation Notes carries a before/after status for every
in-scope id.

**Implementation Hints:**
- **Run on the owner's main checkout only,** never in an execution worktree. Mark the story
  supervised. Since the file's diff isn't in git, Implementation Notes is the audit trail:
  record each id's before and after status.
- **In-scope ids:**
  - -004 … -025 (structural leaked cells);
  - **-033** (the `hidden_markup` on `/retrieve` aggregate, 9 of 38). Re-scope it to the
    remaining leaked ids with the `title_stuffing` (stage 3) reason;
  - -040, -041 (carriers);
  - -042 (`title_stuffing`);
  - -043 … -045 (blocked-but-leaked).
- **Statuses** use the file's existing vocabulary: `open`, `resolved` or `dismissed`.
  - Closed findings are `resolved`, each with a one-line resolution naming the spec and story.
  - Partly closed cells stay `open`, with the "N of M leaked" text and ids re-derived from the
    final baseline.
  - Accepted residuals are `dismissed`, with the reason "accepted residual: <why>".
- **Residual entries continue the id series** from `2026-10-04-056` upward, in a
  `#### Accepted residuals (structural-hardening)` subsection:
  - `title_stuffing` without a marker (stage 3);
  - class or stylesheet hiding;
  - other unlisted inline techniques (`transform:scale(0)`, colour camouflage, non-px offsets,
    tiny font sizes);
  - attribute channels (`aria-label`, `data-*`) and entity-encoded attribute values;
  - body-level percent/base64 decoding and three-level entity encoding;
  - the deferred stage-3 normalisation epic.
- **Keep the heading formats the tests parse** (`tests/test_corpus_docs.py:251-330`):
  `^\*\*2026-10-04-\d{3}\*\* — …`, `` `cat` on `route`: … leaked``, and
  `Blocked-but-leaked: … `id``. Resolved entries keep their headings, so the exact carrier-set
  assertion (:315-326) still holds.
- **Model filter (decided):** keep `_MODEL_22M` in `tests/test_corpus_docs.py` for this epic.
  Stage-2 outcomes are model-independent, and the 86M default's stage-3 differences are outside
  this epic. Add one line noting that.
- **Record ids and technique classes only.** No payload text, here or anywhere.

**Acceptance Criteria:**
- [ ] Every in-scope id (-004 … -025, -033, -040 … -045) reads `resolved` with spec and story,
      `open` with a re-derived count and reason, or `dismissed` as an accepted residual.
- [ ] The accepted residuals listed in the hints are filed as `2026-10-04-056`+ entries, by
      technique class and record id only.
- [ ] `uv run pytest -rs tests/test_corpus_docs.py` on the owner checkout reports 0 skipped and
      0 failed, and the output is pasted in Implementation Notes.
- [ ] Implementation Notes has a before/after status table for every in-scope id.

### US-003: Update the docs for the new stage-2 behaviour

**Priority:** P2

**Description:** As a reader of Forage's docs, I want the security, corpus and architecture docs
to describe the defences as they now are, including what they don't do, so that nobody trusts a
stage-2 guarantee that doesn't exist.

**Independent Test:** Each per-file checklist item below is present. The doc tests
(`tests/test_corpus_docs.py`, `tests/test_governance_docs.py`) are green, and `grep` finds no
"prevents" or "guarantees" claim about stage 2 in the edited sections.

**Implementation Hints and per-file checklist:**
- `docs/corpus.md` "Reading the results": names each stage-2 scan form (as-is, decoded,
  folded, inline-joined, raw-markup subset) and the corpus categories it closes.
  - Respect `test_numbers_appear_only_in_the_decision_inputs_table` (numbers only in that
    table), plus the section-order and disclosure-reasoning tests.
- `kit_tools/arch/SECURITY.md`:
  - states the measured-corpus basis;
  - names the fold table and the visibility pass as **bounded heuristics**;
  - lists every residual from US-002 as unmitigated;
  - makes no "prevents" or "guarantees" claim (landscape: adaptive attacks, arXiv 2510.09023).
  `test_security_md_states_the_corpus_and_keeps_the_two_gaps` reads this file.
- `kit_tools/arch/CODE_ARCH.md`: the scan-form builder, `_MARKUP_PATTERNS`, `confusables.py`
  and its generator, and the visibility pass.
- `kit_tools/docs/GOTCHAS.md`:
  - "derived scan forms must never change stage-3 input";
  - "regenerate `confusables.py` with the generator, never hand-edit";
  - "`unwrap()` needs `smooth()`";
  - "scan raw markup as source, never re-serialised".
- `kit_tools/arch/DECISIONS.md`: one entry for the epic's choices, which are:
  - derived forms;
  - bounded gaps;
  - the generated fold table with its override;
  - the block-allowlist unwrap;
  - raw-source markup scan;
  - body-only pruning;
  - title quarantine and ruling (m).
- `docs/releases.md` Unreleased: the behaviour changes, and that all rotations land in one
  cache-invalidating window.
- `kit_tools/PRODUCT_VISION.md`: add a **T2.4 — Structural hardening** heading under Tier 2,
  with status.
- **No payload text** in any tracked file. Record ids and technique classes only.

**Acceptance Criteria:**
- [ ] Each per-file checklist item above is present in its file.
- [ ] `grep -n -i "prevent\|guarantee" kit_tools/arch/SECURITY.md` shows no claim that stage 2
      prevents or guarantees anything (checked in the review).
- [ ] `tests/test_corpus_docs.py` and `tests/test_governance_docs.py` are green.
- [ ] Full test suite passes (`uv run pytest`)

### US-004: Verify the rotation and count records

**Priority:** P2

**Description:** As a maintainer, I want every rotation from specs 1–3 recorded with its controls,
and every count in the prose agreeing with the source of truth, so that the next rotation's
author starts from a correct record.

**Independent Test:** A grep-based preflight shows every count site agreeing with the source of
truth, and each spec 1–3 rotation has its three records.

**Implementation Hints:**
- **Two counts, kept separate:**
  1. **Rotations.** Source of truth: the last `### The <ordinal> rotation` heading in
     `docs/bootstrap-notes.md`.
  2. **Hashed sources.** Source of truth: `len(_REVISION_SOURCES) + len(_ROOT_REVISION_SOURCES)`,
     ten after spec 1 US-004.
- **Preflight:**
  `grep -rn -E "rotation(s)? .*(forty|fifty)|(forty|fifty)-[a-z]+ (rotation|times)|[a-z]+ hashed source" CLAUDE.md kit_tools/docs/GOTCHAS.md kit_tools/docs/TROUBLESHOOTING.md kit_tools/docs/DEPLOYMENT.md kit_tools/arch/SERVICE_MAP.md`.
  Every current-state count must match. **Dated historical sentences are exempt**, for
  example CLAUDE.md's "the last heading ordinal and GOTCHAS table now agree on forty-two
  rotations", which describes the fortieth rotation's moment.
- **Per rotation from specs 1–3:** a bootstrap-notes heading with its controls, a GOTCHAS table
  row, and a CLAUDE.md paragraph. Fill any gap here, from the story's Implementation Notes.
- This spec moves no hashed source: `derive_sanitizer_revision` at story end must equal the
  spec 3 final value.
- `contract/openapi.yaml.sha256` is unchanged since `03f7a96` (ruling (m), no bump).

**Acceptance Criteria:**
- [ ] The preflight output, pasted in Implementation Notes, shows every current-state rotation
      count equal to the last bootstrap-notes ordinal and every hashed-source count equal to
      ten. Historical sentences are listed as exempt.
- [ ] Each spec 1–3 rotation has a bootstrap-notes heading with reversal controls, a GOTCHAS
      row and a CLAUDE.md paragraph.
- [ ] `derive_sanitizer_revision` for default and shipped config equals the value spec 3's
      last rotation recorded.
- [ ] `git diff --exit-code 03f7a96 -- contract/openapi.yaml contract/openapi.yaml.sha256`
      exits 0.
- [ ] Full test suite passes (`uv run pytest`)

## Edge Cases

- A finding whose cell partly closed: it stays `open`, with re-derived ids and count (US-002).
- The scaffold proposes a looser floor on one variant: keep the old floor if the measurement
  clears it, otherwise stop and record why (US-001).
- A new cell appears in the scaffold (unexpected, since no categories or routes are added):
  `floors_diff.py` exits non-zero. Investigate (US-001).
- A spec 1–3 rotation is missing a record: filled in US-004 from that story's notes, or
  execution stops (US-004).

## Out of Scope

- Cutting a release. The version decision is separate.
- Fixing any residual.
- Switching the findings tests to the 86M (decided: keep 22M for this epic).

## Assumptions

- Specs 1–3 regenerated the baseline and recorded their rotations. This spec verifies them.
- The owner runs US-002 on the main checkout.

## Technical Considerations

- No hashed source moves here, so there is no rotation.
- US-001 adds one script and a test, and changes `floors.json`. US-002 touches only the
  gitignored findings file. US-003 and US-004 are docs.

## Related Documentation

- Corpus gate: [docs/corpus.md](../../docs/corpus.md) "The gate"
- Findings format: `tests/test_corpus_docs.py`
- Epic: [epic-forage-structural-hardening.md](epic-forage-structural-hardening.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Floors are ratcheted once, at the end, with a committed monotonicity script.
**Rationale:** `--write-floors` overwrites hand edits, and a human diff over 414 values across
four variants is unreliable. The exact-match baseline test going red on a revert proves nothing
about floors.
**Source:** `tests/test_corpus_gate.py:264-266,363-380`; `scripts/corpus/report.py`
`build_floors`; validation reviewers 3, 4 and 6.

**Decision:** Findings are re-filed as a supervised owner step.
**Rationale:** Gitignored file. Skipped tests look green. The worktree copy dies.
**Source:** GOTCHAS "Gitignored files written inside an execution worktree die with the
worktree"; validation second opinion.

### Scope Adjustments
- 2026-10-06 validation:
  - The old US-002 was split into the findings re-file (owner), docs, and the records preflight.
  - Added -033, the reference commit, the floors script, the epic completion checks, the
    residual id scheme and the zero-skip requirement.
  - The model-filter question was resolved.

### Decisions Made
- See Research Findings.

## Clarifications

### Session 2026-10-06
- Q: How should catch be traded against false positives? → A: ratchet both ways. Floors only tighten, and core-genre false-positive rates never rise.
- Q (validation): Should the findings tests use the 22M or the 86M? → A: keep 22M for this epic. Stage-2 outcomes are model-independent.
