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

Two leak families come from how stage 1 turns HTML into text:

- **`split_tags` (8 records).** `_extract_raw_text` calls `soup.get_text(separator="\n")`
  (`pipeline/stage1_extraction.py:265`), which puts a newline at every text-node boundary. A
  keyword split by any element reaches stage 2 broken across lines. `/search` has the same
  artefact, because its scan form goes through `extract_html`.
- **`plain` / `tag-consumed` (4 records).** The trigger *is* markup, so the parser consumes it
  before any text scan.

**Validation measured four traps** (2026-10-06):
- `unwrap()` + `get_text` catches 0/8. `unwrap()` + `smooth()` catches 8/8 but is **quadratic
  in sibling count** (54 s at 40k sibling `<b>`).
- Re-serialising a parsed tree drops stray closing tags.
- `envelope_breakout` backtracks on whitespace runs. Spec 1 US-002 linearises the pattern.
- A whole-body `finditer` with per-match line lookup is quadratic on match-dense pages (56.8 s
  at 4 MB).

This spec uses a **single linear tree walk** for the inline form, and a **first-match-per-pattern**
scan of the raw source for the markup subset. Both run inside the stage-1 thread, so **only scan
results**, never text or markup, leave it. `raw_text` and `/search` wire forms (stage-3 input)
stay byte-identical: no re-record.

## Goals

- The 8 `split_tags` records (`atk-0076`, `atk-0120`, `atk-0167`, `atk-0041`, `atk-0092`,
  `atk-0105`, `atk-0138` on page routes; `atk-0042` on `/search`) and the 4 tag-consumed `plain`
  records (`atk-0033`, `atk-0160`, `atk-0161`, `atk-0132`) are caught in the regenerated
  baseline.
- Class-level properties:
  - every HTML-escaped probe, split at each interior boundary by any non-block element (named,
    unknown or custom), is caught;
  - every markup-subset probe written as literal markup anywhere in a page is caught.
- Both new scans are linear: their 10 MB time is ≤ 3× their 5 MB time on element-dense,
  match-dense and whitespace-bomb pages, and the absolute times are recorded.
- Zero cassette misses and zero core-genre benign movement from `passed`, with all pins green.
  The two parser-strip benign controls (`ben-0288`, `ben-0289`) move by decision (US-002).

## User Stories

### US-001: An inline-joined scan form built by one linear tree walk

**Priority:** P1

**Description:** As an operator, I want stage 2 to also scan HTML text with every non-block
element joined into its surrounding text, so that any tag splitting a trigger word can't hide it
behind a line break, without the scan itself becoming a CPU-exhaustion vector.

**Independent Test:** Drive `/retrieve` and `/search` with the 8 `split_tags` leaks: all are
caught. A page of 80,000 sibling `<b>` elements builds the form in linear time. `raw_text` and the
cassettes are unchanged.

**Implementation Hints:**
- **Recipe: one iterative walk, no tree mutation, no `unwrap()`, no `smooth()`.** Walk the soup
  (or the same copy `_extract_raw_text` uses, `stage1_extraction.py:249-265`) in document order:
  - skip `_DANGEROUS_TAGS` subtrees and comments;
  - append each `NavigableString`'s text to a list;
  - emit `"\n"` on entering and leaving any element in the **closed block set**: `address,
    article, aside, blockquote, body, br, caption, dd, details, dialog, div, dl, dt, fieldset,
    figcaption, figure, footer, form, h1-h6, head, header, hr, html, legend, li, main, nav, ol,
    p, pre, section, summary, table, tbody, td, tfoot, th, thead, title, tr, ul`;
  - emit nothing for any other element, so `wbr`, `font` and custom elements are joined.
  Then `"".join`, then `_normalize_text`.
- **Never change `raw_text`:** it is stage 3's input.
- **Scan inside the stage-1 thread, carry only the result.**
  - On `/retrieve`, wrap `extract_html` in an orchestrator-local function, run inside the
    existing `to_thread` call, that also builds the inline text, feeds it to spec 1's builder
    (`html_parsed=True`, so it gets the decode and fold forms too), scans, and returns
    `(ExtractionResult, StructuralScanResult)`.
  - The orchestrator deletes `html_text` early (around :585) so a queued request holds only
    extracted text. Carrying the inline text would break that; carrying a scan result doesn't.
- **Seam into `sanitize_and_structure`:** add a keyword-only `extra_scans:
  Sequence[StructuralScanResult] = ()` parameter. Combine with spec 1's rule through a
  result-level helper `combine_scan_results(*results)`, which spec 1 exposes:
  - the worst verdict wins;
  - flags and penalty come from the as-is scan when it already has the worst verdict, otherwise
    from the first result that reaches it.
  Update its three call sites (`orchestrator.py:653`, `:780`, `:864`); PDF and upload pass
  nothing.
- **`/search`:** `_scan_forms_for_search_text` already calls `extract_html` per field. Have that
  **same call** also produce the inline text, by returning a small named tuple instead of the
  pair. Pass it through spec 1's builder (decode and fold forms) and scan it as an extra entry
  per field in the loop (:1810-1846). Never parse twice. Update `tests/corpus_stage2.py`
  `stage2_forms()` to match. Wire forms are unchanged.
- **Splitter strings for the property test, exactly:** `<b></b>` inserted at the boundary;
  `<span>`+second-half+`</span>`; `<wbr>`; `<font>`+second-half+`</font>`;
  `<x-custom>`+second-half+`</x-custom>`. The probe is passed through `html.escape` first.
- Hashed files: `orchestrator.py`, and `stage1_extraction.py` if the walker lives there. List
  exactly what moved in the rotation record.

**Acceptance Criteria:**
- [ ] For `<p>ab<b></b>cd</p>`, the inline form contains `abcd`. For `<p>ab</p><p>cd</p>`, it keeps
      `ab` and `cd` on separate lines (unit tests).
- [ ] Building the inline form for 80,000 sibling `<b>` elements takes ≤ 3× the time for 40,000
      (linearity), and the time for a 10 MB element-dense page is recorded.
- [ ] `raw_text` is byte-identical to its pre-story value for every corpus page record.
- [ ] For each of the 24 probes in `STAGE2_REGEX_PROBES`, HTML-escaped and split at every
      interior character boundary by each of the five splitter strings, the result is caught on
      `/retrieve` and `/search`. A split probe whose letters are also double-entity-encoded is
      caught on `/search` (decode forms apply to the inline text).
- [ ] Only `StructuralScanResult` values leave the stage-1 thread: no inline text or markup is
      held by the request after `extract_html` returns (test asserting the returned types).
- [ ] The 8 `split_tags` records are not `leaked` in the regenerated baseline. No core-genre
      benign record (watch `code`) moves from `passed`, and the pin tests are green.
- [ ] `tests/test_search_pipeline_pins.py` is unchanged and green. Both cassette files are
      byte-unchanged, with zero misses.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated, and `tests/test_corpus_docs.py` is
      green.
- [ ] The rotation is recorded with per-file reversal controls.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: A first-match raw-markup scan for markup-consumed triggers

**Priority:** P2

**Description:** As an operator, I want the triggers that live *in markup* (fake system tags,
envelope-breakout tags, private-IP `href` and `src` attributes) to be matched on the raw source
anywhere in the page, so that writing a trigger as markup, or padding it, can't hide it. The
scan must stay linear on hostile pages.

**Independent Test:** Drive `/retrieve` and `/search` with the 4 tag-consumed `plain` leaks: all
are caught. A trigger after 9 MB of padding is caught. Match-dense, whitespace-bomb and
`&lt;`-flood pages scan in linear time.

**Implementation Hints:**
- **The subset is defined by reference:** `_MARKUP_PATTERNS: tuple[re.Pattern[str], ...]` in
  `pipeline/stage2_structural.py` references the compiled `system_tag` (:94, the literal
  `<system>`), `envelope_breakout` (:236, linearised by spec 1 US-002) and `private_ip_href`
  (:212) objects. A test maps each to `vocab.STAGE2_REGEX_NAMES` through `_PATTERNS`' index.
  The set is closed: additions require a corpus record id cited in Implementation Notes.
- **First match per pattern, with no per-match line lookup.** Use `pattern.search()`, not
  `finditer`, and build a `FlaggedSpan` with `line_number=0`. Line numbers mean nothing after
  whitespace collapse, only the category reaches the wire, and the penalty saturates at 3
  flags. This removes the measured quadratic, which grew from 3.6 s at 1 MB to 56.8 s at 4 MB.
- **Input: the raw source string, never a re-serialised tree.**
  1. **Default: no span cutting.** Scan the raw source with whitespace runs collapsed to one
     space. Use `re.sub(r"\s+", " ", …)` on `str`, which is Unicode-aware, so non-breaking-space
     runs collapse too. `envelope_breakout` and `private_ip_href` tolerate the collapse; the
     literal `<system>` is unaffected.
  2. **Measure the regenerated baseline.** If no core-genre benign record moves, ship with no
     cut. That avoids a tokenizer and any `html.parser`/lxml span disagreement.
  3. **Only if a core-genre record moves** because of `<script>`/`<style>`/comment content,
     cut those three span kinds:
     - use an offset-reporting tokenizer (stdlib `html.parser.HTMLParser`). Absolute offsets
       need a line-start table counting `\n` only. `getpos()` reports span *starts*, so find
       each end tag's offset explicitly;
     - **never cut `template` or `noscript`**: their text reaches `raw_text` and the wire;
     - **fail toward scanning:** an unterminated span is not cut;
     - put the cutter in `stage2_structural.py` (hashed);
     - add a parametrised agreement test against lxml on tricky cases: unterminated
       `script`/comment, CDATA, `--!>`, nested comment-like text.
     Record which branch shipped.
- **No bound:** scan the whole body (≤ 10 MB, `DEFAULT_MAX_CONTENT_BYTES`,
  `stage5_url_audit.py:31`). A head-only bound is a padding bypass.
- **Runs inside the stage-1 thread** with US-001's function, returning one more
  `StructuralScanResult` through `extra_scans`. Never carry the markup.
- **`/search`:** run the subset on each field's raw provider value, using the same truncated
  string `_scan_forms_for_search_text` feeds to `extract_html`.
- **`/extract` is out:** uploads are plain text.
- **Benign controls that will flip:** `ben-0288` and `ben-0289` are `/search` parser-strip
  controls, pinned "drives clean" by `tests/test_corpus_ingest.py:1661` and `:1718-1730`. The raw
  scan blocks `ben-0288` and flags `ben-0289`, which has the same shape as `atk-0132`.
  **Decision:** they now trip, because they are literally the markup-consumed shape this story
  exists to catch. Update their pins and those tests, with a comment citing this spec.
- **Corpus mirrors:**
  - keep `tests/corpus_stage2.py` `stage2_forms`/`stage2_record_hits` as the extracted-text
    view, and add `stage2_markup_hits`;
  - `tests/test_corpus_attacks.py:330-345` keeps its extracted-text assertion and gains the
    converse;
  - also check `scripts/corpus/records.py:489-511` and `tests/test_corpus_attacks.py:385,1163`.
- **Logging:** the scan emits category tokens only, never `matched_text`.
- **Residuals for spec 4:** entity-encoded attribute values; `javascript:` in `href`/`src`,
  which the parser consumes but which isn't in the subset; block elements restyled
  `display:inline`, which still split text.
- Leaked records: `atk-0033`, `atk-0160` (page), `atk-0161`, `atk-0132` (`/search`).
- Hashed files: `stage2_structural.py` and `orchestrator.py`.

**Acceptance Criteria:**
- [ ] `_MARKUP_PATTERNS` references exactly the compiled `system_tag`, `envelope_breakout` and
      `private_ip_href` patterns, pinned by name through `_PATTERNS`' index.
- [ ] The scan uses `search()` (first match per pattern) on whitespace-collapsed raw source
      (Unicode `\s`). A test shows a stray closing `envelope_breakout` tag in raw HTML caught,
      and a trigger in an `iframe` or `form` attribute caught.
- [ ] Each subset probe written as literal markup at the start, the middle and after 9 MB of
      single-text-node padding in a 10 MB page is caught on `/retrieve`. Each is also caught in
      a `/search` raw field value.
- [ ] Linearity: on a match-dense page (private-IP links), a whitespace-bomb page and an
      `&lt;`-flood page, the scan's 10 MB time is ≤ 3× its 5 MB time. Absolute times are
      recorded.
- [ ] Implementation Notes records which branch shipped (no cut, or cut with the parser
      agreement test), with the baseline evidence for the choice.
- [ ] `ben-0288` and `ben-0289` are re-pinned to their new outcomes with a comment citing this
      decision. `tests/test_corpus_ingest.py` is green.
- [ ] The 4 tag-consumed `plain` records are not `leaked` in the regenerated baseline. No
      core-genre benign record moves from `passed`, and the pin tests are green.
- [ ] Both cassette files are byte-unchanged, with zero misses. `tests/test_search_pipeline_pins.py`
      is green.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated, and `tests/test_corpus_docs.py` is
      green.
- [ ] The rotation is recorded with reversal controls.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

## Edge Cases

- Nested non-block elements: the walk emits no separator, so the text is joined (US-001).
- An inline element containing a block element (invalid HTML): lxml repairs it, and the block
  emits newlines (US-001).
- `<br>` inside a trigger: a line break by design. Spec 1's tempered patterns cover the two gap
  patterns (US-001).
- A non-HTML `/retrieve` response: no inline or markup scan (US-001, US-002).
- A subset trigger inside `<script>`: scanned under the no-cut default, cut only under the
  fallback branch (US-002).
- Malformed or unterminated markup: scanned as-is (US-002).
- Benign intranet links to RFC 1918 hosts: `private_ip_href` is SUSPICIOUS, a flag only. Watch
  the `docs` genre (US-002).

## Out of Scope

- Rendering or CSS evaluation (hidden content is spec 3).
- Tag splits inside `/extract` plain-text uploads.
- Attribute channels the corpus didn't exercise, entity-encoded attribute values, and
  `javascript:` URLs. These are residuals for spec 4.
- **Stage 1's own cost on element-dense pages:** `extract_html` takes about 150 s on a 10 MB
  element-dense page. This is a pre-existing exposure, filed separately as an audit finding
  (planning, 2026-10-06), and not fixed here. The new tests use single-text-node padding to stay
  CI-fast.

## Assumptions

- The block set above is the complete set of separating elements for scanning purposes.
- Spec 1 exposes `combine_scan_results` and the builder. If it doesn't, US-001 adds the
  result-level helper to `stage2_structural.py`.

## Technical Considerations

- **Hashed files:** `orchestrator.py` (US-001 and US-002), `stage1_extraction.py` (if the walker
  lives there) and `stage2_structural.py` (US-002). Expect one rotation per story.
- The no-re-record guarantee rests on `raw_text` and `/search` wire forms staying unchanged.
- **Decision-input tables** move with new blocks: update `docs/corpus.md` in the same story.
- **Findings checks skip in worktrees.** Re-filing is spec 4's owner step.

## Related Documentation

- Spec 1: [feature-structural-scan-forms.md](feature-structural-scan-forms.md) — builder, combine rule, linear patterns
- Corpus guide: [docs/corpus.md](../../docs/corpus.md)
- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** A single iterative walk with block-boundary separators, not `unwrap()` + `smooth()`.
**Rationale:**
- `unwrap()` alone catches 0/8.
- `unwrap()` + `smooth()` catches 8/8 but is quadratic in sibling count (13 s at 20k, 54 s at
  40k, 213 s at 80k).
- A walk that appends text and emits newlines only at block boundaries is linear, and catches
  the same records.
- A block allowlist joins unknown elements, which is the closed direction for catch.
**Source:** `pipeline/stage1_extraction.py:265`; validation rounds 1–2.

**Decision:** Scan raw source, first match per pattern, whitespace-collapsed, no cut by default.
**Rationale:**
- lxml round-trips drop stray end tags.
- Per-match line lookup is quadratic on match-dense pages, and only the category reaches the
  wire.
- Cutting needs a tokenizer whose span boundaries can drift from lxml's (Python 3.12 patch
  changes). Only three markup patterns run, so try with no cut first.
- `template` and `noscript` reach the wire and are never cut.
**Source:** validation round 2 (security, codebase fit, second opinion).

**Decision:** Scans run inside the stage-1 thread, and only results leave it.
**Rationale:** Preserves "a queued request holds only extracted text" (`orchestrator.py`, around
:585).
**Source:** validation rounds 1–2.

**Landscape:** Unit 42's in-the-wild catalogue
(<https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/>, 2026-03-03): add corpus rows
for further channels before adding surfaces.

### Scope Adjustments
- Round 1: a block allowlist; a raw-source whole-body scan.
- Round 2:
  - the linear walk replaced `unwrap()`/`smooth()`;
  - first-match scanning;
  - no cut by default, with a guarded fallback;
  - `template`/`noscript` never cut;
  - scans run in the stage-1 thread through `extra_scans`;
  - `/search` reuses its single parse;
  - `ben-0288`/`ben-0289` flip by decision;
  - the stage-1 cost was split out as a separate finding.

### Decisions Made
- See Research Findings.

## Clarifications

### Session 2026-10-06
- Q: Should normalised text reach stage 3? → A: no. Stage 2 only (spec 1 has the full epic Q&A).
- Q (validation): Should the raw-markup scan have a bound? → A: no. The whole body is scanned linearly.
- Q (validation round 2): `ben-0288` and `ben-0289` are parser-strip benign controls that the raw scan trips. → A: they flip by decision, because they are the exact shape the story exists to catch (planner decision; override at review if wanted).
