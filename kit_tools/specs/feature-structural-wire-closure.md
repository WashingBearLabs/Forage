<!-- Template Version: 2.5.0 -->
---
feature: structural-wire-closure
status: active
session_ready: true
depends_on: [structural-markup-surface]
vision_ref: "T2.3 follow-up — close the injection corpus's structural findings"
type: epic-child
size: M
epic: forage-structural-hardening
epic_seq: 3
epic_final: false
created: 2026-10-06
updated: 2026-10-06
---

# Feature Spec: Structural Wire Closure — Quarantined Titles and Hidden Content Off the Served Body

## Overview

Two leak families are about **what reaches the wire**:

- **Blocked-but-leaked (`atk-0059`, `atk-0211`, `atk-0212`, `/retrieve`).** `finalize_quarantine`
  (`pipeline/stage4_structuring.py:179-211`) replaces the body and spans but carries
  `title=result.title` (:197), so the hostile title ships. This contradicts its own docstring:
  "a quarantine response exposes only stable diagnostic labels".
- **Hidden-markup carriers `css_offscreen` and `hidden_div` (`atk-0017`, `atk-0197`,
  `atk-0198`, `atk-0018`, `atk-0201`, `atk-0202`).** Stage 1 has no hidden-content handling, so
  the text is served in `body`. All six go through trafilatura, and `atk-0202` leaks despite an
  inline `display:none`. Forage's pass must be complete on its own.

**Owner decisions (2026-10-06):**
- Hidden content is removed from the **served body only**; stage 2 and 3 still scan it, and
  nothing is re-recorded.
- The `hidden` attribute prunes, except `hidden="until-found"`.
- A null title on a quarantined response is a **sanitizer outcome with no contract bump**
  (ruling (m)).

`title_stuffing` without a marker is stage 3's job, an accepted residual (spec 4).

## Goals

- `atk-0059`, `atk-0211` and `atk-0212` report `marker_on_wire` false in the regenerated
  baseline. **No quarantined response on any route carries a non-null `title`, including a
  cache-hit replay.**
- The 6 `css_offscreen` and `hidden_div` records are no longer `leaked`.
- Every signal in the visibility set prunes its target, with a positive and a negative fixture
  per rule.
- Benign corpus pages without a listed signal have **byte-identical** `main_content`. The
  benign body loss for pages with a signal is reported as a number.
- Zero cassette misses, zero core-genre benign movement from `passed`, pin tests green, and the
  contract stays `1.3.0` (`contract/openapi.yaml.sha256` unchanged).

## User Stories

### US-001: Quarantine the title of every quarantined response

**Priority:** P1

**Description:** As an agent consuming Forage, I want a blocked or injection-detected response to
carry no document-derived title, so that quarantine really removes all hostile document text from
the wire.

**Independent Test:** Route-level tests show a null title on every quarantined response, and the
three blocked-but-leaked records report `marker_on_wire` false. Non-quarantined responses keep
their title.

**Implementation Hints:**
- `pipeline/stage4_structuring.py:197`: `title=result.title` becomes `title=None`. `title` is
  `str | None` with default `None` on both response models (`models.py:83`, `:192`), so the
  shape is unchanged.
- **All three quarantine causes** go through the same function (:186-195): stage-2 BLOCKED,
  stage-3 INJECTION_DETECTED and `unavailable_blocked`. Unit-test each in
  `tests/test_stage4_structuring.py`, whose helpers at :39-45 already take `title=`.
- **Route-level tests:**
  - `/retrieve` with a stage-2 block, `/retrieve` with a stage-3 block, and `/extract` with a
    block;
  - a **cache-hit replay** of a blocked `/retrieve` body.
  Cached pre-change bodies can't replay, because the rotation invalidates their keys. The test
  pins the post-change replay.
- **Governance (pre-resolved, owner 2026-10-06):**
  - Record a new lettered ruling **(m)** in `contract/GOVERNANCE.md` "Recorded rulings": a
    quarantined response's `title` becoming `null` is a **sanitizer outcome, no bump**.
  - The title is document text, and which document text a quarantined response carries is
    decided by the sanitizer, like the body it already replaces.
  - Distinguish it explicitly from ruling (e) (:307), which classed a *metadata* field's
    normalisation as MINOR.
  - Include a `**Source:**` line.
  - **No description edit:** `contract/openapi.yaml` and its anchor stay byte-identical.
- **Ruling counts move from thirteen to fourteen:**
  - `contract/GOVERNANCE.md:182` ("Thirteen rulings…");
  - `tests/test_governance_docs.py:442-465`, which asserts the count sentence and the
    registered headings;
  - CLAUDE.md invariant 4 ("records the thirteen rulings");
  - `kit_tools/AGENT_README.md`, `kit_tools/SYNOPSIS.md`, `kit_tools/arch/CODE_ARCH.md`.
  Find them with `grep -rn "thirteen rulings\|thirteen recorded"`.
- **Findings test:** `tests/test_corpus_docs.py::test_every_blocked_but_leaked_record_is_filed`
  asserts `ids != []`. Change it to "every blocked-but-leaked id in the baseline is filed;
  the set may be empty". Keep a non-vacuity guard: assert the baseline's `records` mapping is
  non-empty, so the test still proves it read real data.
- `stage4_structuring.py` is hashed, so record the rotation.

**Acceptance Criteria:**
- [ ] `finalize_quarantine` returns `title=None` for stage-2 BLOCKED, stage-3 INJECTION_DETECTED
      and `unavailable_blocked` (three unit tests). A non-quarantined result keeps its title
      (unit test).
- [ ] Route-level tests assert `title` is `null` on the wire for a `/retrieve` stage-2 block, a
      `/retrieve` stage-3 block, an `/extract` block, and a cache-hit replay of a blocked
      `/retrieve` body.
- [ ] `atk-0059`, `atk-0211` and `atk-0212` report `marker_on_wire` false in the baseline
      regenerated with `uv run python -m scripts.corpus.report --write-baseline`.
- [ ] `contract/GOVERNANCE.md` records ruling (m) with a `**Source:**` line, distinguished from
      ruling (e). Every "thirteen rulings" count reads "fourteen", and
      `tests/test_governance_docs.py` is green.
- [ ] `contract/openapi.yaml` and `contract/openapi.yaml.sha256` are byte-unchanged, and
      `tests/test_contract_export.py` is green.
- [ ] `test_every_blocked_but_leaked_record_is_filed` accepts an empty id set and keeps a
      non-vacuity assertion on the baseline's records.
- [ ] Both cassette files are byte-unchanged, with zero misses. No core-genre benign record
      moves, and the pin tests are green.
- [ ] The rotation is recorded (CLAUDE.md, `docs/bootstrap-notes.md`, GOTCHAS table) with a
      reversal control.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: A Forage-owned visibility pass on the served body

**Priority:** P2

**Description:** As an agent consuming Forage, I want text a browser would not show removed from
the served body, so that a page can't deliver instructions to me that its human reader never sees.

**Independent Test:** Drive `/retrieve` with the 6 `css_offscreen` and `hidden_div` records:
none has its marker in `body`. Every benign corpus page without a listed signal has
byte-identical `main_content`, and summary mode on the fallback path behaves as before.

**Implementation Hints:**
- **Where:** `extract_html` (`pipeline/stage1_extraction.py:296-337`). Read the current line
  numbers, since spec 2 edits this function first.
  - `raw_text` stays built from the original soup, unchanged (stage-3 input).
  - A prune helper returns the pruned soup plus a `pruned: bool`.
  - **Only if `pruned`:** trafilatura gets `str(pruned_soup)`. Otherwise it gets the
    **original `html` string**, unchanged; re-serialising every page would move benign bodies.
  - **Fallback** (trafilatura returns `None`): the body is
    `_normalize_text(_extract_raw_text(pruned_soup))`, calling the existing helper, not a
    parallel flattener. If nothing was pruned, that equals `raw_text`, as today.
- **The explicit fallback flag:** `pipeline/smart_extraction.py:176` infers the fallback path
  from `main_content == raw_text`, which pruning breaks.
  - Add `main_content_is_fallback: bool | None = None` to `ExtractionResult`. `extract_html`
    sets it `True` or `False`. `None` means "infer by equality", which preserves the
    behaviour of `pipeline/pdf_subprocess.py:140,260`, `stage1_upload.py` and existing test
    constructors.
  - `extract_summary` takes the flag and falls back to the equality test only when it is
    `None`. The stage-4 caller passes it through.
- **Visibility set.** Inline signals only; no stylesheet or class resolution (owner,
  2026-10-06). Only descendants of `<body>` are pruned: never `<head>`, `<title>`, `<meta>`,
  or attributes on `<html>`/`<body>` themselves.
  - **Non-overridable (the whole subtree is removed):**
    - the `hidden` attribute, **except `hidden="until-found"`**;
    - `aria-hidden="true"`;
    - `display:none`;
    - `opacity` equal to zero (`0`, `0.0`, `0%`);
    - `clip:rect(…)` with all four components zero (any separator, any unit);
    - `text-indent` ≤ −999px;
    - (`position:absolute` or `fixed`) AND (`left` ≤ −999px OR `top` ≤ −999px);
    - `overflow:hidden` AND (`width` zero OR `height` zero).
  - **Inherited (overridable):** `visibility:hidden|collapse` and `font-size` equal to zero
    (any unit). The element's own text is removed, but a descendant carrying an inline re-show
    (`visibility:visible`, or a non-zero `font-size`) is **kept with its subtree**, as a
    browser renders it.
  - Offsets match **px only**. Other units don't match; they are residuals. Zero tests accept
    any unit. `overflow:hidden` alone never prunes.
- **Style parsing:**
  - A linear-time declaration splitter: split on `;` then the first `:`, case-insensitive,
    whitespace- and `!important`-tolerant, no backtracking regex.
  - For a repeated property, the **last declaration wins**.
  - Malformed declarations are ignored, and the element is kept.
  - Traversal is **iterative**, not recursive.
- **`/search` skips pruning:** it calls `extract_html` per field (`orchestrator.py:978`) and
  reads only `raw_text`. Add `prune_hidden: bool = True` to `extract_html`, pass `False` there,
  and save the CPU.
- **Corpus tests hard-code these carriers as leaked**, and they must move:
  - `tests/test_corpus_harness.py:1417-1418` `_STRUCTURAL_ONLY_BY_CARRIER` (`css_offscreen`,
    `hidden_div` → their new outcome; confirm per record);
  - the per-seed assertion around :1530;
  - the `marker_on_wire` assertion around :1730.
  `tests/test_corpus_docs.py:315-326` asserts the exact carrier set among findings *entries*.
  Entries persist when resolved, so it stays green (spec 4 re-files).
- **Benign-loss fixtures,** so the cost is a decision rather than an accident:
  - an ARIA tab panel using `hidden` (asserted pruned);
  - a `hidden="until-found"` section (asserted kept);
  - an `opacity:0` animated hero (asserted pruned, documented cost);
  - a `font-size:0` layout container with re-shown children (asserted kept).
  Report how many benign corpus bodies changed in Implementation Notes.
- **Framing:** this pass is defence in depth, best effort. The real control remains stage 2 and
  3 scanning `raw_text`. Unlisted techniques go to spec 4 as residuals: class or stylesheet
  rules, `transform:scale(0)`, colour camouflage, non-px offsets, tiny non-zero font sizes.

**Acceptance Criteria:**
- [ ] Each non-overridable signal prunes its subtree, and each inherited signal prunes its own
      text while keeping an inline re-shown descendant. There is one positive and one negative
      fixture per rule, including `overflow:hidden` alone (kept), `hidden="until-found"` (kept),
      a non-px offset (kept), and a repeated-declaration last-wins case.
- [ ] Every benign corpus page with no listed signal has `main_content` byte-identical to its
      pre-story value (test over the corpus pages). The count of benign pages whose body changed
      is recorded in Implementation Notes.
- [ ] `raw_text` is byte-identical for every corpus page. Both cassette files are
      byte-unchanged, with zero misses.
- [ ] `ExtractionResult.main_content_is_fallback` is set by `extract_html`, and `extract_summary`
      uses it. Summary-mode output on the fallback path is unchanged for an unpruned page and
      defined (tested) for a pruned one. PDF and upload results (`None`) behave as before.
- [ ] The 6 `css_offscreen` and `hidden_div` records are not `leaked` in the regenerated
      baseline. `tests/test_corpus_harness.py` carrier expectations are updated to the measured
      outcomes. No core-genre benign record moves from `passed`, and the pin tests are green.
- [ ] Style parsing tolerates mixed case, whitespace, `!important` and malformed declarations.
      A page with 10,000 levels of nesting and a 1 MB style value is processed without raising,
      within a recorded time.
- [ ] An empty pruned body still returns a well-formed `/retrieve` 200 in both extract modes
      (route test).
- [ ] The rotation is recorded with a reversal control.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

## Edge Cases

- A quarantined response where stage 1 found no title: `None` either way (US-001).
- `/search` omits blocked results entirely, so there's no title to quarantine (US-001; noted).
- A hidden ancestor with a visible descendant: removed for non-overridable signals, and the
  re-shown descendant is kept for inherited ones (US-002).
- `aria-hidden="true"` on decorative icons inside visible text: the icon's text is removed,
  which is correct (US-002).
- A page whose entire visible body is hidden: `main_content` is empty, the word count is 0, and
  the response is a well-formed 200. Stage 2/3 still scan `raw_text` (US-002).
- `hidden` on `<html>` or `<body>`: ignored, since pruning applies only to body descendants
  (US-002).
- Malformed inline style: ignored, and the element is kept (US-002).
- Pruning removes everything trafilatura would extract: trafilatura returns `None`, and the
  fallback uses the pruned flattened text with `main_content_is_fallback=True` (US-002).
- A PDF or upload result: the flag is `None`, so summary mode infers by equality as before
  (US-002).

## Out of Scope

- Stylesheet, class or `<style>`-rule hiding, colour camouflage, `transform:scale(0)` and
  non-px offsets. These are residuals for spec 4.
- Capping titles, and unmarked `title_stuffing` (stage 3, an accepted residual).
- Removing hidden text from what stage 2/3 scan, which forces a re-record.
- A "hidden content present" signal or counter on the wire, which is a contract change. Debug
  logging of prune counts is allowed (closed token, counts only).

## Assumptions

- Removing elements from the served body is a sanitizer outcome, not a contract change: `body`
  is free text.
- The six target records use inline signals within the visibility set (validation confirmed it
  from style attributes, without payload text).

## Technical Considerations

- **Hashed files:** `stage4_structuring.py` (US-001); `stage1_extraction.py` and possibly
  `smart_extraction.py` (US-002). `smart_extraction.py` isn't a `_REVISION_SOURCES` member;
  confirm, and list exactly what moved.
- **The decision-input tables** only move if new blocks occur. US-001 and US-002 add none, so
  expect no change, but verify `tests/test_corpus_docs.py` stays green.
- **Findings checks skip in worktrees** (gitignored `AUDIT_FINDINGS.md`). Re-filing is spec 4's
  owner step.

## Related Documentation

- Governance: [contract/GOVERNANCE.md](../../contract/GOVERNANCE.md), rulings (e) and (j)
- Corpus "Reading the results" (`marker_on_wire`): [docs/corpus.md](../../docs/corpus.md)
- Security posture: [SECURITY.md](../arch/SECURITY.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Null the title in `finalize_quarantine`; ruling (m), no bump.
**Rationale:** The title is document text (`scripts/corpus/outcomes.py:207-244` finds the marker
in it on all three records), and the function's docstring promises diagnostic labels only. The
owner classified the change as a sanitizer outcome (2026-10-06).
**Alternatives considered:** MINOR per ruling (e). Rejected: (e) concerns a metadata field's
normalisation, not which document text a quarantine carries.
**Source:** `pipeline/stage4_structuring.py:179-211`; `contract/GOVERNANCE.md:307-345`.

**Decision:** Prune `main_content` only, with an explicit fallback flag, re-serialising only
when pruned.
**Rationale:** `raw_text` is stage-3 input. `smart_extraction.py:176`'s equality test breaks
under pruning. Re-serialising unpruned pages would move benign bodies.
**Source:** validation reviewers 1, 3, 4 and 6, 2026-10-06.

**Decision:** Split signals into non-overridable and inherited.
**Rationale:** `visibility` and `font-size` are inherited and can be overridden by a child, so
removing the subtree drops visible text.
**Source:** validation second opinion.

**Landscape:** trafilatura's hidden-content xpaths cover only inline `display:none`,
`aria-hidden` and "hidden" in an id or style
(<https://raw.githubusercontent.com/adbar/trafilatura/master/trafilatura/xpaths.py>, fetched
2026-10-06). Unit 42 (<https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/>,
2026-03-03) lists more than 20 techniques. Headless rendering was rejected (arXiv 2509.05831).

### Scope Adjustments
- 2026-10-06 validation:
  - Added the fallback flag, the inherited-signal split, `until-found`, re-serialise-only-when-
    pruned, route-level title tests, the carrier test updates and the `/search` prune skip.
  - The governance classification was pre-resolved.

### Decisions Made
- See Research Findings.

## Clarifications

### Session 2026-10-06
- Q: What should happen to hidden content? → A: drop it from the served body only; stage 2/3 still scan it.
- Q: How far should the title fix go? → A: quarantine the title. Unmarked stuffing is stage 3's job, an accepted residual.
- Q (validation): How should the `hidden` attribute be treated? → A: prune it, except `hidden="until-found"`.
- Q (validation): How is the null title classified? → A: a sanitizer outcome, no bump. Recorded as ruling (m), distinguished from (e).
