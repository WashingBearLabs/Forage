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

# Feature Spec: Structural Close-out — Ratchet the Floors, Record the Epic, Re-file the Findings

## Overview

Specs 1–3 each regenerate `tests/corpus/baseline.json` and record their own rotations. This spec:
- locks the gains in with tighter floors, checked mechanically against a reference commit;
- brings the docs and record counts up to date;
- **as a final owner step**, re-files the gitignored findings file.

**Pre-epic reference commit: `03f7a96`** (the commit that added this epic's specs). Every
"pre-epic" comparison means `git show 03f7a96:<path>`.

**How to execute this spec:**
- US-001 to US-003 are ordinary stories and can run under `/kit-tools:execute-epic`.
- **US-004 is an owner step on the main checkout,** never in an execution worktree:
  `kit_tools/AUDIT_FINDINGS.md` is gitignored, findings checks skip in worktrees, and
  worktree edits die with the worktree (GOTCHAS).
- Run this spec in **supervised** mode and stop the orchestrator before US-004. Alternatively,
  execute US-001 to US-003 and perform US-004 by hand. US-004's zero-skip criterion makes a
  worktree run fail loudly rather than pass silently.

## Goals

- Every `min_*` value in `tests/corpus/floors.json` (per cell *and* in the headline subtree) is
  ≥ its `03f7a96` value, and every `max_*` value is ≤ it. At least the cells holding the
  `case` leaks have tightened. A committed module proves both.
- Every measured benign false-positive rate (core genres, `security_prose`,
  `over_defence_probe`) in the final baseline is ≤ its `03f7a96` value, **exactly**, not on the
  0.05 floor grid.
- `test_every_measured_cell_clears_its_floor` fails when spec 1 US-001's case flags are removed
  and the baseline regenerated, so the ratchet demonstrably guards a closed leak.
- Docs and record counts match the shipped behaviour, and no corpus payload text appears in any
  tracked file (mechanical check).
- Every in-scope finding is `resolved`, `open` with a reason, or `dismissed` as an accepted
  residual, and `uv run pytest -rs tests/test_corpus_docs.py` reports **zero skipped**.

## User Stories

### US-001: Ratchet the floors and prove the epic's completion checks mechanically

**Priority:** P1

**Description:** As a maintainer, I want the floors tightened to what the epic now measures, and
scripts proving nothing loosened, so that a future change reopening a closed leak, or quietly
raising a false-positive rate, fails CI or review.

**Independent Test:** `python -m scripts.corpus.floors_diff` reports only tightenings (including
the headline subtree) against `03f7a96`, and `--baseline-fpr` reports no FPR rise. With the
seven case flags removed in a temporary worktree and the baseline regenerated, the floor test
fails on named cells.

**Implementation Hints:**
- **Regenerate the floors:** `uv run python -m scripts.corpus.report --write-floors`.
  `build_floors()` (`scripts/corpus/report.py`, around :1138) writes `floor_down` minimums and
  `ceil_up` `max_fpr` on a **0.05 grid**. `floors.json` at `03f7a96` equals the scaffold
  exactly, so there are no hand edits to restore. **Commit the regenerated file.**
- **Add `scripts/corpus/floors_diff.py`,** run as `uv run python -m scripts.corpus.floors_diff`
  like `report.py`.
  - **Floors mode** (`OLD NEW`): walk **every subtree**, including the headline
    (`min_catch_all`, `max_fpr_external`). Any key starting `min_` must not decrease and any
    `max_` key must not increase. Fail on an **unrecognised key** or a changed cell set. Print
    every tightened entry.
  - **`--baseline-fpr OLD_BASELINE NEW_BASELINE` mode:** compare the exact measured benign FPR
    per (genre, route, model, config) between two `baseline.json` files, and fail on any rise.
    The grid can hide a one-record rise in a 23–50-record cell.
    - **Named exemption (owner, 2026-10-06):** an `--exempt ben-0288,ben-0289` option removes
      exactly those records from both baselines before comparing, so the `/search`
      `over_defence_probe` cell is compared on the remaining records.
    - The exemption list is a module constant with a comment citing DECISIONS.md. Any other
      exemption fails code review.
  - Use `git show 03f7a96:<path>` into a temp file for the OLD inputs.
- **Ratchet proof (exact procedure):**
  1. `git worktree add /tmp/ratchet-proof HEAD`.
  2. In it, remove `re.IGNORECASE` from exactly the seven patterns spec 1 US-001 changed. A
     `git revert` won't apply cleanly once specs 1–3 have edited the same file.
  3. Run `uv run python -m scripts.corpus.report --write-baseline`, then
     `uv run pytest tests/test_corpus_gate.py::test_every_measured_cell_clears_its_floor`.
  4. Record the failing cells: expect those holding `atk-0070`, `atk-0071` and `atk-0116`. Those
     cells hold 6–7 records, so losing one moves a floor by more than one grid step.
  5. `git worktree remove /tmp/ratchet-proof`.
  - **If it doesn't go red,** stop and record why. Don't weaken the proof.
- **If any variant would loosen,** stop and record the reason. There is no "keep the old floor"
  branch: the scaffold already floors the measurement.

**Acceptance Criteria:**
- [ ] `tests/corpus/floors.json` is regenerated and committed. `floors_diff` against `03f7a96`
      exits 0, its output (pasted in Implementation Notes) lists every tightened entry
      including headline keys, and at least the cells holding `atk-0070`, `atk-0071` and
      `atk-0116` are tightened.
- [ ] `scripts/corpus/floors_diff.py` has unit tests covering a lowered `min_*`, a raised
      `max_*`, a loosened headline key, an unrecognised key, a changed cell set, and a one-record
      FPR rise in `--baseline-fpr` mode (all non-zero exit).
- [ ] `floors_diff --baseline-fpr --exempt ben-0288,ben-0289` between `03f7a96`'s and the final
      `baseline.json` exits 0. Without `--exempt` it fails, which proves the exemption is
      exactly what moved (both runs pasted).
- [ ] The recorded ratchet proof shows `test_every_measured_cell_clears_its_floor` failing on
      named cells with the seven case flags removed and the baseline regenerated. The temporary
      worktree is removed, and `git status` is clean.
- [ ] `git diff --exit-code 03f7a96 -- tests/corpus/cassettes/` exits 0 (no re-record across the
      epic).
- [ ] `tests/test_corpus_gate.py` passes against the new `floors.json`, including the cell-set
      equality check (:363-380).
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: Update the docs for the new stage-2 behaviour

**Priority:** P2

**Description:** As a reader of Forage's docs, I want the security, corpus and architecture docs
to describe the defences as they now are, including what they don't do, so that nobody trusts a
stage-2 guarantee that doesn't exist.

**Independent Test:** Each per-file checklist item is present, the doc tests are green, and the
payload-leak check over tracked docs passes.

**Implementation Hints and per-file checklist** (residuals are named by **technique class**,
not finding id, so this story doesn't depend on US-004):
- `docs/corpus.md` "Reading the results": names each stage-2 scan form (as-is, decoded,
  folded, inline-joined, raw-markup subset) and the categories it closes.
  - Respect `test_numbers_appear_only_in_the_decision_inputs_table`, plus the section-order and
    disclosure-reasoning tests.
- `kit_tools/arch/SECURITY.md`:
  - states the measured-corpus basis;
  - names the fold table and the visibility pass as **bounded heuristics**;
  - lists the residual technique classes as unmitigated;
  - makes no "prevents" or "guarantees" claim about stage 2 (landscape: arXiv 2510.09023).
  `test_security_md_states_the_corpus_and_keeps_the_two_gaps` reads it.
- `kit_tools/arch/CODE_ARCH.md`: the scan-form builder, the pattern hardening,
  `_MARKUP_PATTERNS`, `confusables.py` and its generator, and the visibility pass.
- `kit_tools/docs/GOTCHAS.md`:
  - derived scan forms must never change stage-3 input;
  - regenerate `confusables.py`, never hand-edit;
  - the inline form is a linear walk; never `unwrap()` + `smooth()` (quadratic);
  - scan raw markup as source, never re-serialised;
  - every new stage-2 pattern must pass the all-patterns timing sweep.
- `kit_tools/arch/DECISIONS.md`: one entry for the epic's choices.
- `docs/releases.md` Unreleased: the behaviour changes, the null-title consumer note, and one
  cache-invalidating window for all rotations.
- `kit_tools/PRODUCT_VISION.md`: a **T2.4 — Structural hardening** heading under Tier 2, with
  status.
- **Payload-leak check:** add a test that, for every attack record in `tests/corpus/attacks/`,
  asserts no substring of 24 or more characters of its payload field appears in any tracked
  `*.md` file. Cite record ids and technique classes only.

**Acceptance Criteria:**
- [ ] Each per-file checklist item above is present in its file.
- [ ] `kit_tools/arch/SECURITY.md` contains no sentence claiming stage 2 prevents or guarantees
      anything (`grep -ni "prevent\|guarantee"` output reviewed in Implementation Notes).
- [ ] The payload-leak test exists and passes over all tracked Markdown.
- [ ] `tests/test_corpus_docs.py` and `tests/test_governance_docs.py` are green.
- [ ] Full test suite passes (`uv run pytest`)

### US-003: Verify the rotation, count and tally records

**Priority:** P2

**Description:** As a maintainer, I want every rotation from specs 1–3 recorded with its controls,
and every count and tally in the prose agreeing with its source of truth, so that the next
rotation's author starts from a correct record.

**Independent Test:** The preflight greps show every current-state count agreeing with its source
of truth, and each spec 1–3 rotation has its three records.

**Implementation Hints:**
- **Three counts, kept separate:**
  1. **Rotations.** Source of truth: the last `### The <ordinal> rotation` heading in
     `docs/bootstrap-notes.md`.
  2. **Hashed sources.** Source of truth: `len(_REVISION_SOURCES) + len(_ROOT_REVISION_SOURCES)`,
     ten after spec 1.
  3. **Behaviour-change tally.** GOTCHAS's "N of the M rotations changed no sanitization
     policy …; the … are the K that did" sentence. Each sanitization-behaviour rotation from
     this epic joins K.
- **Preflight:**
  `grep -rn -E "(forty|fifty)(-[a-z]+)? (rotations|times)|rotations? (has|have) moved|[a-z]+ hashed sources?|of the [a-z-]+ rotations" CLAUDE.md kit_tools/docs/GOTCHAS.md kit_tools/docs/TROUBLESHOOTING.md kit_tools/docs/DEPLOYMENT.md kit_tools/arch/SERVICE_MAP.md`.
  Every current-state count must match. **Dated historical sentences are exempt,** for
  example CLAUDE.md's "now agree on forty-two rotations" from the fortieth rotation.
- **Per rotation from specs 1–3:** a bootstrap-notes heading with controls, a GOTCHAS row and a
  CLAUDE.md paragraph. Fill gaps from the story's Implementation Notes.
- This spec moves no hashed source.

**Acceptance Criteria:**
- [ ] The preflight output (pasted) shows every current-state rotation count equal to the last
      bootstrap-notes ordinal, every hashed-source count equal to ten, and the tally sentence
      consistent with the per-rotation classification. Historical sentences are listed as
      exempt.
- [ ] Each spec 1–3 rotation has a bootstrap-notes heading with reversal controls, a GOTCHAS
      row and a CLAUDE.md paragraph.
- [ ] `derive_sanitizer_revision` for default and shipped config equals the value spec 3's last
      rotation recorded.
- [ ] `git diff --exit-code 03f7a96 -- contract/openapi.yaml contract/openapi.yaml.sha256` exits
      0.
- [ ] Full test suite passes (`uv run pytest`)

### US-004: Re-file the corpus findings (owner step, main checkout)

**Priority:** P1

**Description:** As the owner, I want `AUDIT_FINDINGS.md`'s corpus entries re-derived from the
final baseline, so that the record says exactly which leaks closed, which remain, and why.

**Independent Test:** On the owner checkout, `uv run pytest -rs tests/test_corpus_docs.py` passes
with **zero skipped**, and Implementation Notes carries a before/after status for every in-scope
id.

**Implementation Hints:**
- **Owner's main checkout only.** The file's diff isn't in git, so Implementation Notes is the
  audit trail.
- **In-scope ids:**
  - -004 … -025 (structural leaked cells);
  - -033 (the `hidden_markup` on `/retrieve` aggregate). Re-scope it to the remaining
    `title_stuffing` ids, with the stage-3 reason;
  - -040, -041 (carriers);
  - -042 (`title_stuffing`, which becomes `dismissed` as an accepted residual; **don't** file a
    second title-stuffing entry);
  - -043 … -045 (blocked-but-leaked).
- **Statuses:** `open`, `resolved` or `dismissed` (the file's vocabulary).
  - `resolved` carries a one-line resolution naming spec and story.
  - Partly closed cells stay `open`, with "N of M leaked" and ids re-derived.
  - Accepted residuals are `dismissed` ("accepted residual: <why>").
- **New residual entries** continue the series from `2026-10-04-056`, under
  `#### Accepted residuals (structural-hardening)`. Their headings **must not** use the
  "`hidden_markup` carrier `x`" form, which would break the exact carrier-set test (:315-326),
  or the "`cat` on `route`: … leaked" form, which would falsely satisfy the leaked-cell test.
  The classes match US-002's technique list.
- Keep the parsed heading formats for existing entries (`tests/test_corpus_docs.py:251-330`).
- **Model filter (decided):** keep `_MODEL_22M`. Stage-2 outcomes are model-independent. Add one
  line noting the 86M default.
- Record ids and technique classes only, never payload text.

**Acceptance Criteria:**
- [ ] Every in-scope id reads `resolved` with spec and story, `open` with a re-derived count and
      reason, or `dismissed` as an accepted residual.
- [ ] Residual entries `2026-10-04-056`+ exist for every technique class in US-002's list, in
      headings that match neither the carrier nor the leaked-cell form.
- [ ] `uv run pytest -rs tests/test_corpus_docs.py` on the owner checkout reports 0 skipped and 0
      failed. The output is pasted in Implementation Notes.
- [ ] Implementation Notes has a before/after status table for every in-scope id.

## Edge Cases

- A partly closed finding: stays `open`, with re-derived ids and count (US-004).
- A floor would loosen on some variant: stop and record why (US-001).
- An unexpected new scaffold cell: `floors_diff` exits non-zero. Investigate (US-001).
- The ratchet proof doesn't go red: stop and record. Never weaken the proof (US-001).
- A spec 1–3 rotation is missing a record: filled from that story's notes, or execution stops
  (US-003).
- US-004 run in a worktree: the findings tests skip, so the zero-skip criterion fails loudly
  (US-004).

## Out of Scope

- Cutting a release.
- Fixing any residual.
- Switching the findings tests to the 86M.

## Assumptions

- Specs 1–3 regenerated the baseline and recorded their rotations. This spec verifies them.
- The owner performs US-004 on the main checkout.

## Technical Considerations

- No hashed source moves here.
- US-001 adds one module with tests and changes `floors.json`. US-002 adds one test plus docs.
  US-003 is docs. US-004 touches only the gitignored findings file.

## Related Documentation

- Corpus gate: [docs/corpus.md](../../docs/corpus.md) "The gate"
- Findings format: `tests/test_corpus_docs.py`
- Epic: [epic-forage-structural-hardening.md](epic-forage-structural-hardening.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Ratchet once at the end, with a committed module that checks floors (every `min_*`
and `max_*`, headline included) and exact baseline FPRs.
**Rationale:** `--write-floors` overwrites hand edits. The 0.05 grid hides one-record FPR rises.
The exact-match baseline test going red on a revert says nothing about floors.
**Source:** `tests/test_corpus_gate.py:264-266,363-380`; `scripts/corpus/report.py`
`build_floors`; validation rounds 1–2.

**Decision:** The findings re-file is the final owner step.
**Rationale:** Gitignored file, skipped tests look green, and the worktree copy dies. The docs
name residuals by class, so they don't depend on it.
**Source:** GOTCHAS; validation rounds 1–2.

### Scope Adjustments
- Round 1: the old US-002 was split three ways; added -033, the reference commit, the floors
  script, and a zero-skip requirement.
- Round 2: floors_diff covers the headline and all `min_*`/`max_*` keys and exact baseline FPR;
  the floors file must be committed; the ratchet proof became an exact procedure; the owner
  step moved last; the payload-leak test and the tally preflight were added; the residual
  heading rules were set.

### Decisions Made
- See Research Findings.

## Clarifications

### Session 2026-10-06
- Q: How should catch be traded against false positives? → A: ratchet both ways. Floors only tighten; FPRs never rise.
- Q (validation): Should the findings tests use the 22M or the 86M? → A: keep 22M for this epic.
- Q (validation round 3): The raw-markup scan flips over-defence probes `ben-0288` and `ben-0289`. → A: a named exemption for exactly those two, applied by id in `--baseline-fpr`.
