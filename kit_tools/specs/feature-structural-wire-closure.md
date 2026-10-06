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

Two leak families are about **what reaches the wire**, not what stage 2 sees.

- **Blocked-but-leaked (3 records: `atk-0059`, `atk-0211`, `atk-0212`, all on `/retrieve`).**
  Stage 2 blocks the page. `finalize_quarantine` (`pipeline/stage4_structuring.py:179-211`)
  replaces the body with the quarantine text and the spans with a diagnostic label, but carries
  `title=result.title` through (:197). The title is the hostile text, so it ships. This
  contradicts the function's own docstring: "a quarantine response exposes only stable
  diagnostic labels".
- **Hidden-markup carriers `css_offscreen` and `hidden_div` (6 records: `atk-0017`, `atk-0197`,
  `atk-0198`, `atk-0018`, `atk-0201`, `atk-0202`).** Stage 1 has no hidden-content handling.
  The text sits in both `raw_text` and trafilatura's `main_content`, so it is served in `body`.
  Trafilatura's built-in heuristics catch only inline `display:none`, `aria-hidden` and
  "hidden" in an id or style attribute.

The owner ruled (2026-10-06) that hidden content is removed **from the served body only**.
Stages 2 and 3 still scan it, `raw_text` stays byte-identical, and nothing is re-recorded.
`title_stuffing` that is *not* blocked (`atk-0019`, `atk-0213`, `atk-0214`) has no structural
marker. It is stage 3's job and is recorded as an accepted residual in spec 4.

## Goals

- `atk-0059`, `atk-0211` and `atk-0212` are no longer blocked-but-leaked (`marker_on_wire` false)
  in the regenerated baseline. **No blocked or injection-detected response, on any route, carries
  a non-null `title`.**
- The 6 `css_offscreen` and `hidden_div` records are no longer `leaked` (their marker no longer
  reaches `body`).
- Every inline concealment technique in the visibility set (listed under US-002) removes its
  element from the served body. This is asserted per technique.
- Zero cassette misses, zero core-genre benign movement from `passed`, all pins hold.

## User Stories

### US-001: Quarantine the title of a blocked response

**Priority:** P1

**Description:** As an agent consuming Forage, I want a blocked or injection-detected response
to carry no document-derived title, so that the quarantine really removes all hostile document
text from the wire.

**Independent Test:** `finalize_quarantine` unit tests plus the corpus gate. The three
blocked-but-leaked records report `marker_on_wire` false; non-blocked responses keep their title.

**Implementation Hints:**
- **`pipeline/stage4_structuring.py:197`:** `title=result.title` becomes `title=None`. `title`
  is `str | None` with default `None` on both response models (`models.py:83`, `:192`), so the
  shape is unchanged.
- The same function serves stage-2 BLOCKED and stage-3 INJECTION_DETECTED /
  `unavailable_blocked` (:186-195). All three must drop the title; test all three.
- `build_retrieved_content` (:265) and `build_extracted_content` (:293) copy from the
  `SanitizationResult`, so the fix at :197 covers both. `/extract`'s title is always `None`
  today, but keep the test so it stays that way.
- **Governance check (required):** a blocked response's `title` changes from "page title" to
  `null`. The shape doesn't change, but a consumer-visible value does. Classify it against
  `contract/GOVERNANCE.md`'s table and worked examples before merging. The expectation is
  security tightening with no shape change. If the published `title` description or the
  quarantine description in `contract/openapi.yaml` needs a sentence, regenerate with
  `uv run python -m scripts.export_contract` and follow the ruling the table gives. Record the
  classification (and a lettered ruling if the table calls for one) in GOVERNANCE.md's rulings
  list.
- `stage4_structuring.py` is hashed, so record the rotation.
- **Cache:** the rotation invalidates old cache keys, so no cached blocked response with a title
  survives.

**Acceptance Criteria:**
- [ ] `finalize_quarantine` returns `title=None` for stage-2 BLOCKED, stage-3 INJECTION_DETECTED
      and `unavailable_blocked`. Each has a unit test. Non-quarantined results keep their title
      (test).
- [ ] `atk-0059`, `atk-0211` and `atk-0212` report `marker_on_wire` false in the regenerated
      baseline.
- [ ] The change is classified against `contract/GOVERNANCE.md`, with the classification
      recorded there. `contract/openapi.yaml` and its anchor are regenerated only if a
      description changed, and `tests/test_contract_export.py` is green.
- [ ] Both cassette files are byte-unchanged with zero misses. No core-genre benign record moves,
      and all pins hold.
- [ ] `tests/test_corpus_docs.py::test_every_blocked_but_leaked_record_is_filed` accepts an
      empty set. Its `assert ids != []` becomes "every blocked-but-leaked id is filed, possibly
      none", so the fix doesn't turn the owner-checkout findings check red.
- [ ] The rotation is recorded (CLAUDE.md, `docs/bootstrap-notes.md`, GOTCHAS table), with a
      reversal control.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: A Forage-owned visibility pass on the served body

**Priority:** P2

**Description:** As an agent consuming Forage, I want text a browser would not show
(inline-hidden or off-screen elements) removed from the served body, so that a page cannot
deliver instructions to me that its human reader never sees.

**Independent Test:** Drive `/retrieve` with the 6 `css_offscreen` and `hidden_div` records:
none has its marker in `body`. Stage 2 and 3 inputs (`raw_text`) and the cassettes are unchanged.

**Implementation Hints:**
- **Where:** `extract_html` (`pipeline/stage1_extraction.py:296-337`).
  - `raw_text` stays built from the original soup, unchanged: it is stage 3's input, and stage 2
    still scans hidden text.
  - `main_content` is built from a **visibility-pruned** HTML string. Prune a soup copy, then
    `str()` it, then `_extract_main_content(pruned_html, url)`.
  - **Fallback path:** when trafilatura returns `None`, `main_content = raw_text` (:327-329)
    would put the hidden text back. The fallback must instead use the pruned soup's flattened
    text (built the same way as `_extract_raw_text`). That is a body-only value, never stage-3
    input.
- **Visibility set** (inline signals only; no stylesheet or class resolution, owner 2026-10-06):
  - the `hidden` attribute;
  - `aria-hidden="true"`;
  - inline `style` with any of:
    - `display:none`
    - `visibility:hidden|collapse`
    - `opacity:0` (including `0.0`)
    - `font-size:0` (any zero unit)
    - `clip:rect(0…)` or `clip-path:inset(50%)` combined with zero size
    - `position:absolute|fixed` together with `left`/`top` ≤ −999px or `text-indent` ≤ −999px
    - `width:0` / `height:0` together with `overflow:hidden`
  - **Not** `overflow:hidden` alone: legitimate content uses it, and trafilatura's own
    heuristic already over-matches here (landscape research).
- Parse inline styles with a small, tolerant declaration splitter (`;` / `:`, case-insensitive,
  whitespace and `!important` tolerant). Don't add a CSS library.
- **Corpus checklist:** Unit 42's in-the-wild catalogue
  (<https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/>, 2026-03-03). Promptfoo's
  write-up (<https://promptfoo.dev/blog/indirect-prompt-injection-web-agents>, 2026-02-06) adds
  edge cases: class-rule hiding (out of scope, record it as a residual) and negative
  `text-indent`.
- `word_count` is computed on `main_content` (:331), so it drops for pruned pages. That is
  expected.
- `stage1_extraction.py` is hashed, so record the rotation.

**Acceptance Criteria:**
- [ ] Each visibility signal in the set removes its element (and descendants) from `main_content`
      on both the trafilatura path and the fallback path. This is one parametrised test per
      signal, plus a test that `overflow:hidden` alone does not remove anything.
- [ ] `raw_text` is byte-identical to before the story for every corpus page record. Both
      cassette files are byte-unchanged with zero misses.
- [ ] The 6 `css_offscreen` and `hidden_div` records are no longer `leaked` in the regenerated
      baseline. No core-genre benign record moves from `passed` (its served `body` may shrink
      only by elements with a listed signal), and all pins hold.
- [ ] Inline-style parsing tolerates mixed case, extra whitespace, `!important` and malformed
      declarations without raising (tests).
- [ ] The rotation is recorded with a reversal control.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

## Edge Cases

- A blocked response where stage 1 found no title: `None` either way, no change (US-001).
- `/search` omits blocked results entirely (`OMIT_STRUCTURAL_BLOCKED`), so it has no title to
  quarantine (US-001; noted for completeness).
- Cached pre-change blocked responses: invalidated by the `sanitizer_revision` rotation, so they
  are never served (US-001).
- A hidden ancestor with a visible descendant (`display:none` on a wrapper): the whole subtree
  goes, as a browser would render it (US-002).
- `aria-hidden="true"` on decorative icons inside visible text: removing the icon's text is
  correct; its label is not body text (US-002).
- A page whose entire `<body>` is hidden: `main_content` is empty, so the word count is 0. That
  is not an error, and stage 2/3 still scan `raw_text` (US-002).
- Malformed inline style (`style="display:"`, unterminated declarations): ignored, element kept
  (US-002).
- Pruning removes the only content trafilatura would have found: trafilatura returns `None`, and
  the fallback uses the pruned flattened text, never `raw_text` (US-002).

## Out of Scope

- Stylesheet, class or `<style>`-rule hiding, colour camouflage and `data-*` attributes. These
  need CSS evaluation and are recorded as residuals in spec 4.
- Capping or truncating titles (owner, 2026-10-06), and unmarked `title_stuffing` (stage 3,
  accepted residual).
- Removing hidden text from what stage 2/3 scan, which would change stage-3 input and force a
  re-record.
- A new structural flag or signal for "hidden content present". That is a contract change; it is
  a candidate follow-up.

## Assumptions

- Removing elements from the served body is a content change, not a contract change: `body` is
  a free-text field.
- The `hidden_div` and `css_offscreen` corpus records use inline signals within the visibility
  set. If one uses a class rule, it stays leaked and is filed as a residual (spec 4).

## Technical Considerations

- **Hashed files:** `stage4_structuring.py` (US-001) and `stage1_extraction.py` (US-002). Expect
  one rotation each.
- US-002 parses the page a second time (a soup copy) for the pruned body. That is acceptable
  next to trafilatura's own parse.
- The contract may move in US-001 only if a description is edited. The governance check decides.

## Related Documentation

- Governance: [contract/GOVERNANCE.md](../../contract/GOVERNANCE.md)
- Corpus "Reading the results" (`marker_on_wire`): [docs/corpus.md](../../docs/corpus.md)
- Security posture: [SECURITY.md](../arch/SECURITY.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Null the title in `finalize_quarantine`.
**Rationale:** The title is document text. `scripts/corpus/outcomes.py:207-244` finds the marker
in it on all three records, and the function's own docstring promises diagnostic labels only.
**Alternatives considered:** scanning or sanitising the title separately. Rejected because the
quarantine's contract is "no document text", which nulling satisfies outright.
**Source:** `pipeline/stage4_structuring.py:179-211`; `models.py:83,192`.

**Decision:** Visibility pruning affects `main_content` only.
**Rationale:** `raw_text` is stage 3's input (`orchestrator.py:278`). Changing it forces a
re-record, and stage 2 should keep seeing hidden text as evidence.
**Alternatives considered:** pruning before both. Rejected (owner, 2026-10-06).
**Source:** `pipeline/stage1_extraction.py:296-337`.

**Landscape:** trafilatura's hidden-content xpaths cover only inline `display:none`,
`aria-hidden` and "hidden" in an id or style (<https://raw.githubusercontent.com/adbar/trafilatura/master/trafilatura/xpaths.py>,
fetched 2026-10-06). Unit 42 (2026-03-03) lists more than 20 concealment techniques. Headless
rendering was rejected (arXiv 2509.05831, 2025-11-11) as out of proportion for a CPU-only
sidecar.

### Scope Adjustments
- Title capping was dropped (owner, 2026-10-06).

### Decisions Made
- See Research Findings.

## Clarifications

### Session 2026-10-06
- Q: What should happen to hidden content? → A: drop it from the served body only; stage 2/3 still scan it; no re-record.
- Q: How far should the title fix go? → A: quarantine the title on block. Unmarked stuffing is stage 3's job, an accepted residual.

## Open Questions

- [ ] What will the governance classification of `title: null` on blocked responses be?
      Expected: security tightening with no shape change. US-001 decides and records it.
      (Not blocking: every outcome of the table is handled by US-001's criteria.)
