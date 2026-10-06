<!-- Template Version: 2.5.0 -->
---
feature: structural-markup-surface
status: active
session_ready: true
depends_on: [structural-scan-forms]
vision_ref: "T2.3 follow-up — close the injection corpus's structural findings"
type: epic-child
size: M
epic: forage-structural-hardening
epic_seq: 2
epic_final: false
created: 2026-10-06
updated: 2026-10-06
---

# Feature Spec: Structural Markup Surface — Inline-Tag Splits and Markup-Consumed Triggers

## Overview

Two leak families come from how stage 1 turns HTML into text, not from the text itself:

- **`split_tags` (8 records).** `_extract_raw_text` calls `soup.get_text(separator="\n")`
  (`pipeline/stage1_extraction.py:265`), which puts a newline at **every** tag boundary,
  inline ones included. A keyword split by an empty or wrapping `<b>`, `<i>` or `<code>`
  therefore reaches stage 2 broken across lines. `/search` has the same artefact: its scan form
  goes through `extract_html`. Unwrapping inline elements before `get_text` catches all 8.
  Deleting newlines instead catches only 7 and destroys line anchors.
- **`plain` / `tag-consumed` (4 records).** The trigger *is* markup (a tag name or an
  attribute). The parser consumes it, so the regex matches the raw HTML but never the extracted
  text. `tests/test_corpus_attacks.py:330-345` currently asserts that consumption.

This spec adds two scan-only forms through spec 1's shared builder, keeping `raw_text` (the
stage-3 input) byte-identical:
- an inline-unwrapped text form;
- a bounded raw-markup scan limited to the patterns that target markup.

## Goals

- The 8 `split_tags` records (`atk-0076`, `atk-0120`, `atk-0167`, `atk-0041`, `atk-0092`,
  `atk-0105`, `atk-0138` on page routes; `atk-0042` on `/search`) and the 4 tag-consumed
  `plain` records (`atk-0033`, `atk-0160`, `atk-0161`, `atk-0132`) are caught in the
  regenerated baseline.
- Every pattern's probe, split at **each** interior character boundary by each inline element
  in the unwrap set, is caught on `/retrieve` and `/search`. This is a class-level property test.
- Zero cassette misses, zero core-genre benign movement from `passed`, all pins hold.

## User Stories

### US-001: An inline-unwrapped scan form for HTML inputs

**Priority:** P1

**Description:** As an operator, I want stage 2 to also scan HTML text with inline elements
unwrapped, so that an inline tag splitting a trigger word cannot hide it behind a line break.

**Independent Test:** Drive `/retrieve` and `/search` with the 8 `split_tags` leaks: all are
caught. `raw_text`, and therefore the cassettes, are unchanged.

**Implementation Hints:**
- **Stage 1:** add one field to the internal `ExtractionResult` dataclass
  (`pipeline/stage1_extraction.py`), e.g. `scan_text_inline: str`. It is **not** on the wire.
  Build it from a copy of the soup:
  - remove the dangerous tags and comments exactly as `_extract_raw_text` (:249-265) does;
  - `unwrap()` every element in a fixed inline set (`a, abbr, b, bdi, bdo, cite, code, data,
    del, dfn, em, i, ins, kbd, mark, q, s, samp, small, span, strong, sub, sup, time, u, var`);
  - then `get_text(separator="\n")` and `_normalize_text`.
  Block-level boundaries still become newlines, so line-anchored patterns keep working.
- **Never change `raw_text`:** it is stage 3's input. `extract_html` (:296-337) returns both
  fields.
- **Non-HTML paths** (`stage1_pdf.py`, `stage1_upload.py`): set the field equal to `raw_text`.
  The builder deduplicates forms, so this costs nothing.
- **Wiring:** spec 1 US-002's `structural_scan_forms` takes the extra text. Pass
  `scan_text_inline` from `sanitize_and_structure` (`pipeline/orchestrator.py:263`). For
  `/search`, `_scan_forms_for_search_text` (:940-982) needs the same unwrap applied to the
  provider value's HTML parse. The field loop (:1810-1846) gains these forms. `/search` wire
  forms (stage-3 input, :1285-1287) must stay unchanged:
  `tests/test_search_pipeline_pins.py` is the guard.
- **False-positive watch:** unwrapping joins adjacent inline text with no separator, which
  matches what a browser renders (`foo<b>bar</b>` → `foobar`). The benign `code` genre,
  heavy on `<code>` and `<span>`, is the genre to watch in the baseline diff.
- The hashed files touched are stage 1, the orchestrator and possibly stage 2. Record the
  rotation with per-file reversal controls.

**Acceptance Criteria:**
- [ ] `extract_html` returns `scan_text_inline` with every element of the inline set unwrapped.
      `raw_text` is byte-identical to before the story for every corpus page record (test over
      the corpus page inputs).
- [ ] For each of the 24 patterns, its probe split at every interior character boundary by
      `<b></b>`, by `<i>…</i>` wrapping the second half, and by `<code></code>` is caught on
      `/retrieve` and `/search`. This is a parametrised test.
- [ ] The 8 `split_tags` records are no longer `leaked` in the regenerated baseline. No
      core-genre benign record (notably the `code` genre) moves from `passed`, and all pins
      hold.
- [ ] `tests/test_search_pipeline_pins.py` is unchanged and green. Both cassette files are
      byte-unchanged with zero misses.
- [ ] The `sanitizer_revision` rotation is recorded (CLAUDE.md, `docs/bootstrap-notes.md`,
      GOTCHAS table), with reversal controls.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated baseline's `offline` section (newly blocked texts leave the stage-3 pool), and `tests/test_corpus_docs.py` is green
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: A bounded raw-markup scan for markup-consumed triggers

**Priority:** P2

**Description:** As an operator, I want the triggers that live *in markup* (fake system tags,
envelope-breakout tags, private-IP `href`/`src` attributes) to be matched on the raw HTML before
the parser consumes them, so that writing the trigger as a tag or attribute cannot hide it.

**Independent Test:** Drive `/retrieve` and `/search` with the 4 tag-consumed `plain` leaks: all
are caught. Benign pages full of ordinary markup (inline images, scripts, base64 data URIs) don't
change outcome.

**Implementation Hints:**
- **Restrict the pattern subset by name.** A full-pattern scan of raw HTML would false-positive
  on every page: `base64_run` matches inline-image data URIs, and scripts carry everything. Use
  a closed, named subset, defined beside `_PATTERNS` in `pipeline/stage2_structural.py`, of the
  patterns whose target *is* markup:
  - `system_tag` (:94)
  - `envelope_breakout` (:236)
  - `private_ip_href` (:212)
  - possibly `im_start` / `endoftext` if a corpus record needs them
  The subset is a stage-2 constant, so it is hashed.
- **Input:** the raw HTML string with `script`, `style`, `template`, `noscript` and comments
  removed. Reuse `_DANGEROUS_TAGS` (`stage1_extraction.py:38-50`) on a soup copy and serialise,
  or strip with the parser rather than a regex.
  - Bound it: `/retrieve` bodies are capped at 10 MB (`stage5_url_audit.py`), so scan at most
    the first `MAX_RAW_MARKUP_SCAN_BYTES` characters. Choose and justify the bound, and record
    it.
  - For `/search`, the raw provider value of each field before `extract_html`.
- The raw-markup result joins the same worst-verdict combine (spec 1 US-002), as another form.
- **`tests/test_corpus_attacks.py:330-345`** asserts tag-consumed triggers are invisible to
  extracted text. That stays true for extracted text. Add the converse assertion: the raw-markup
  scan sees them. Don't delete the old assertion.
- **`/extract` is out:** uploads are plain text, so a literal tag in the upload is already in
  the scanned text.
- Leaked records: `atk-0033`, `atk-0160` (page), `atk-0161`, `atk-0132` (`/search`).

**Acceptance Criteria:**
- [ ] A named, closed subset of patterns runs over bounded raw markup (`/retrieve`) and raw
      provider field values (`/search`), with script, style, template, noscript and comments
      removed. A test pins the subset's names.
- [ ] Inputs longer than the bound are scanned up to the bound only. A test covers a payload
      just inside and just outside it, and the bound is documented in Implementation Notes
      with its rationale.
- [ ] The 4 tag-consumed `plain` records are no longer `leaked` in the regenerated baseline. No
      core-genre benign record moves from `passed`, and all pins hold.
- [ ] `tests/test_corpus_attacks.py` keeps its extracted-text assertion and gains the raw-markup
      converse.
- [ ] Both cassette files are byte-unchanged with zero misses. `tests/test_search_pipeline_pins.py`
      is green.
- [ ] The rotation is recorded with reversal controls.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated baseline's `offline` section (newly blocked texts leave the stage-3 pool), and `tests/test_corpus_docs.py` is green
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

## Edge Cases

- Nested inline elements (`<b><i>x</i></b>`): unwrapping is recursive, so all levels go (US-001).
- An inline element containing a block element (invalid but common HTML): lxml repairs it. Block
  boundaries still produce newlines (US-001).
- `<br>` inside a trigger: a line break, not an inline split. It stays a newline. Spec 1's
  collapse form covers non-DOTALL patterns (US-001).
- A page with no HTML body (plain-text response on `/retrieve`): the unwrap form equals
  `raw_text`, deduplicated (US-001).
- A trigger attribute inside a `<script>` or `<style>` block: excluded by design. Those
  elements never reach the wire (US-002).
- A malformed tag cut off by the scan bound: whatever falls inside the bound is scanned. No
  error (US-002).
- Benign pages linking to RFC 1918 hosts (intranet docs): `private_ip_href` is SUSPICIOUS
  (flag), not BLOCK. Watch the `docs` genre (US-002).

## Out of Scope

- Rendering or CSS evaluation. Hidden content is spec 3.
- Tag splits inside `/extract` plain-text uploads (no HTML parsing there).
- Attribute channels the corpus didn't exercise (`aria-label`, `data-*`), noted by landscape
  research (Unit 42, arXiv 2509.05831). These are a follow-up with corpus rows first.

## Assumptions

- The inline set above covers every inline element the corpus and common pages use to split
  text. Unknown elements are treated as block-level (separator kept), which is the safe
  direction.
- `ExtractionResult` is internal. Adding a field changes no response model and no contract.

## Technical Considerations

- **Decision-input tables move.** `tests/test_corpus_docs.py::test_decision_tables_are_rederived_from_the_baseline_offline_section` re-derives `docs/corpus.md`'s tables from the baseline's `offline` section, which pools only texts that reached stage 3. Every new stage-2 block shrinks that pool. Update the tables, and any prose citing their numbers (DECISIONS.md 2026-10-06, `docs/releases.md` Unreleased), in the same story. That test runs in CI.
- **Findings checks skip in worktrees.** `kit_tools/AUDIT_FINDINGS.md` is gitignored, so `tests/test_corpus_docs.py`'s findings checks skip in an execution worktree and in CI. Leave re-filing to spec 4, which runs on the owner checkout (GOTCHAS: "Gitignored files written inside an execution worktree die with the worktree").
- **Hashed files:** `stage1_extraction.py` (US-001), `orchestrator.py`, `stage2_structural.py`
  (subset constant, US-002). Expect a rotation per story.
- Cost: one extra soup copy and `get_text` per page (US-001), plus one bounded regex pass over
  markup (US-002).
- The no-re-record guarantee rests on `raw_text` and `/search` wire forms staying unchanged.
  Cassette misses are a design error, never a reason to re-record.

## Related Documentation

- Spec 1: [feature-structural-scan-forms.md](feature-structural-scan-forms.md) — the shared builder and combine rule
- Corpus guide: [docs/corpus.md](../../docs/corpus.md)
- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Unwrap inline elements in a scan-only copy, not newline deletion.
**Rationale:** Measured on the leaked records: unwrapping catches 8/8; deleting newlines catches
7/8 and breaks line-anchored patterns (`atk-0092`).
**Alternatives considered:** changing `separator` for `raw_text`. Rejected: it changes stage-3
input and forces a re-record.
**Source:** `pipeline/stage1_extraction.py:265`; exploration run 2026-10-06.

**Decision:** The raw-markup scan uses a named pattern subset.
**Rationale:** Raw HTML is full of `base64_run`-shaped data URIs and script text. Only patterns
whose target is markup are meaningful there.
**Alternatives considered:** all 24 patterns on markup. Rejected as certain over-defence.
**Source:** `pipeline/stage2_structural.py:151,94,212,236`.

**Landscape:** the tag-split mechanism is confirmed in code (a newline at the tag boundary, not
the space a researcher recalled). Unit 42's in-the-wild catalogue
(<https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/>, 2026-03-03) lists markup and
attribute channels beyond this corpus. They are candidates for new corpus rows before any new
surface.

### Scope Adjustments
- `/extract` was excluded from US-002 (plain-text uploads have no parser to consume markup).

### Decisions Made
- See Research Findings.

## Clarifications

### Session 2026-10-06
- Q: Should normalised text reach stage 3? → A: no. Stage 2 only (spec 1 Clarifications has the full epic Q&A).

## Open Questions

- [ ] The exact `MAX_RAW_MARKUP_SCAN_BYTES` value: measure against the largest corpus page and
      the regex cost, and record it. (Not blocking: any bound at or above the largest corpus
      page satisfies the criteria.)
