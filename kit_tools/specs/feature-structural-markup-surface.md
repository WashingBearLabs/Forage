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
  keyword split by `<b></b>`, `<i>`, `<code>`, or *any* element reaches stage 2 broken across
  lines. `/search` has the same artefact, because its scan form goes through `extract_html`.
- **`plain` / `tag-consumed` (4 records).** The trigger *is* markup, a tag or an attribute, so
  the parser consumes it before any text scan.

**Validation measured three traps in the obvious recipes** (bs4 4.15.0, 2026-10-06):
- `unwrap()` + `get_text("\n")` catches **0/8**, because unwrapped text stays as separate
  nodes. Adding `soup.smooth()` catches 8/8.
- Re-serialising a parsed tree drops stray closing tags, so `atk-0160` and `atk-0161` are
  lost. The markup scan must run on the **raw source string**.
- `envelope_breakout` backtracks quadratically on `<` followed by long whitespace (19.4 s on
  80k characters). Collapse whitespace runs first.

Both new surfaces are **scan-only**. `raw_text` and `/search` wire forms (stage-3 input) stay
byte-identical, so there is no re-record.

## Goals

- The 8 `split_tags` records (`atk-0076`, `atk-0120`, `atk-0167`, `atk-0041`, `atk-0092`,
  `atk-0105`, `atk-0138` on page routes; `atk-0042` on `/search`) and the 4 tag-consumed `plain`
  records (`atk-0033`, `atk-0160`, `atk-0161`, `atk-0132`) are caught in the regenerated
  baseline.
- Class-level properties:
  - every HTML-escaped probe, split at each interior boundary by **any non-block element**
    (named, unknown or custom), is caught;
  - every markup-subset probe written as literal markup anywhere in a page is caught, with no
    head-only bound.
- An adversarial page (a `<` plus 1 MB of whitespace, 10 MB of padding) scans in bounded time.
- Zero cassette misses, zero core-genre benign movement from `passed`, all pins green.

## User Stories

### US-001: An inline-joined scan form for HTML inputs

**Priority:** P1

**Description:** As an operator, I want stage 2 to also scan HTML text with every non-block
element joined into its surrounding text, so that any tag splitting a trigger word can't hide it
behind a line break.

**Independent Test:** Drive `/retrieve` and `/search` with the 8 `split_tags` leaks: all are
caught. `raw_text`, and therefore the cassettes, are unchanged, and
`extract_html('<p>ab<b></b>cd</p>').scan_text_inline` contains `abcd`.

**Implementation Hints:**
- **Stage 1:** add `scan_text_inline: str | None = None` to `ExtractionResult`
  (`pipeline/stage1_extraction.py:75`). The default keeps `pipeline/pdf_subprocess.py:140,260`,
  `stage1_upload.py` and the four test constructors unchanged. It is not on the wire.
- **Recipe:**
  1. Take a soup copy.
  2. Drop `_DANGEROUS_TAGS` and comments exactly as `_extract_raw_text` (:249-265) does.
  3. `decompose()` every `wbr`.
  4. `unwrap()` **every element not in a closed block or line-breaking set:** `address,
     article, aside, blockquote, body, br, caption, dd, details, dialog, div, dl, dt, fieldset,
     figcaption, figure, footer, form, h1-h6, head, header, hr, html, legend, li, main, nav, ol,
     p, pre, section, summary, table, tbody, td, tfoot, th, thead, title, tr, ul`.
  5. **`soup.smooth()`**, which merges the adjacent text nodes. Without it the form catches
     nothing.
  6. `get_text(separator="\n")`, then `_normalize_text`.
  Unknown and custom elements (`font`, `nobr`, `x-foo`) are therefore *joined*. That is the
  closed direction for catch: an allowlist of block elements, not of inline ones.
- **Never change `raw_text`:** it is stage 3's input.
- **Wiring:** spec 1's builder derives forms from a base text. Pass `scan_text_inline` (when not
  `None`) as a second base text with `html_parsed=True`, so it gets the decode and fold forms
  too. Add an `extra_texts` keyword to `structural_scan_forms` if spec 1 didn't. Hashed files:
  `stage1_extraction.py`, `orchestrator.py`, and `stage2_structural.py` only if the builder
  signature changes. List exactly what moved in the rotation record.
- **`/search`:** `_scan_forms_for_search_text` (:940-982) returns `(wire_form, scan_form)`.
  Add a separate helper that builds the inline form from the **same truncated provider value**
  fed to `extract_html`, and scan it as an extra entry per field in the loop (:1810-1846).
  Update `tests/corpus_stage2.py` `stage2_forms()` to match. The wire form must not change.
- **Cost:** one extra soup copy, plus `smooth()` and `get_text`, inside the existing threaded
  stage-1 call. Record a timing for the largest corpus page and a 10 MB synthetic page in
  Implementation Notes.

**Acceptance Criteria:**
- [ ] `extract_html('<p>ab<b></b>cd</p>').scan_text_inline` contains `abcd`. A companion test
      asserts the same recipe without `smooth()` does not, guarding the step.
- [ ] `raw_text` is byte-identical to its pre-story value for every corpus page record.
- [ ] For each of the 24 probes in `STAGE2_REGEX_PROBES`, HTML-escaped with `html.escape`
      before splicing, split at every interior character boundary by each of `<b></b>`,
      `<span>…</span>`, `<wbr>`, `<font>…</font>` and `<x-custom>…</x-custom>`, the result is
      caught on `/retrieve` and `/search` (parametrised; the pattern list is derived from
      `_PATTERNS`).
- [ ] A probe split by a block element from the block set (`<p>`, `<li>`, `<br>`) still yields
      separate lines in `scan_text_inline`, so the line anchors keep working (test).
- [ ] The 8 `split_tags` records are not `leaked` in the regenerated baseline. No core-genre
      benign record (watch `code`) moves from `passed`, and the pin tests are green.
- [ ] `tests/test_search_pipeline_pins.py` is unchanged and green. Both cassette files are
      byte-unchanged, with zero misses.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated `offline`
      section, and `tests/test_corpus_docs.py` is green.
- [ ] The `sanitizer_revision` rotation is recorded with per-file reversal controls.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

### US-002: A whole-body raw-markup scan for markup-consumed triggers

**Priority:** P2

**Description:** As an operator, I want the triggers that live *in markup* (fake system tags,
envelope-breakout tags, private-IP `href` and `src` attributes) to be matched on the raw source
before the parser consumes them, anywhere in the page, so that writing a trigger as markup, or
padding it past a bound, can't hide it.

**Independent Test:** Drive `/retrieve` and `/search` with the 4 tag-consumed `plain` leaks: all
are caught. A trigger placed after 9 MB of padding is caught. The whitespace-bomb timing test
passes.

**Implementation Hints:**
- **The subset is defined by reference, not name.** `_PATTERNS` holds unnamed
  `(category, compiled)` tuples, and the names live only in `scripts/corpus/vocab.py`, which
  `pipeline/` can't import.
  - Define `_MARKUP_PATTERNS: tuple[re.Pattern[str], ...]` in `pipeline/stage2_structural.py`
    by referencing the same compiled objects as the `system_tag` (:94), `envelope_breakout`
    (:236) and `private_ip_href` (:212) entries. Index or bind them where they're built.
  - A test maps each entry to `vocab.STAGE2_REGEX_NAMES` through `_PATTERNS`' index and pins
    the three names.
  - **The set is closed:** an addition requires a corpus record id cited in Implementation
    Notes.
- **Input: the raw source string, never a re-serialised tree.** lxml drops stray closing tags.
  - Cut out the character spans of `script`, `style`, `template` and `noscript` elements and
    of comments, using an offset-reporting tokenizer: a stdlib `html.parser.HTMLParser`
    subclass using `getpos()`.
  - **Keep** `iframe`, `embed`, `object`, `form`, `link`, `meta` and `svg`. Their
    `src`/`href`/`action` attributes are exactly what `private_ip_href` targets.
  - Then **collapse every whitespace run to one space.** All three subset patterns use `\s*`,
    so no match changes, and it removes the quadratic backtracking validation measured.
  - The subset scan does **not** go through spec 1's decode or fold forms:
    `envelope_breakout` already matches entity spellings.
- **No bound:** scan the whole body. It is already capped at `DEFAULT_MAX_CONTENT_BYTES` (10 MB,
  `stage5_url_audit.py:30`). After the whitespace collapse a full scan measured about 0.7 s.
  A head-only bound is a padding bypass.
- **Where it runs (memory property):** the retrieve path deletes `html_text` right after
  `extract_html` (`orchestrator.py`, around :585), so a queued request holds no HTML. Run the
  subset scan **inside the same `to_thread` call** as `extract_html`, through a small
  orchestrator-local function returning `(ExtractionResult, StructuralScanResult)`. Carry only
  that result into the spec 1 worst-verdict combine. Never carry the markup.
- **`/search`:** run the subset on each field's raw provider value, using the same truncated
  string `_scan_forms_for_search_text` feeds to `extract_html`, as one more loop entry.
- **`/extract` is out:** uploads are plain text, already scanned as text.
- **Corpus mirrors:** keep `tests/corpus_stage2.py` `stage2_forms`/`stage2_record_hits` as the
  extracted-text view, and add `stage2_markup_hits`.
  - `tests/test_corpus_attacks.py:330-345` asserts that tag-consumed triggers are invisible to
    extracted text. Keep it, and add the converse through `stage2_markup_hits`.
  - Decide whether `split_tags` pins in `test_a_pinned_record_fires_the_regex_it_names` read
    extracted forms or the union, and state it.
- Leaked records: `atk-0033`, `atk-0160` (page), `atk-0161`, `atk-0132` (`/search`).
- Hashed files: `stage2_structural.py` and `orchestrator.py`.

**Acceptance Criteria:**
- [ ] `_MARKUP_PATTERNS` references exactly the compiled `system_tag`, `envelope_breakout` and
      `private_ip_href` patterns. A test pins the three names through `_PATTERNS`' index
      against `STAGE2_REGEX_NAMES`.
- [ ] The scan input is built from the raw source, with script, style, template, noscript and
      comment spans removed by offset and whitespace runs collapsed. A test shows a stray
      closing `envelope_breakout` tag in raw HTML is caught, and a trigger in an `iframe` or
      `form` attribute is caught.
- [ ] Each of the three subset probes, written as literal markup at the start, the middle and
      after 9 MB of benign padding in a 10 MB page, is caught on `/retrieve`. Each is also
      caught in a `/search` raw field value.
- [ ] A page holding `<` followed by 1 MB of whitespace finishes the markup scan in under
      100 ms. A full 10 MB benign page's markup scan time is recorded in Implementation Notes.
- [ ] The markup string is not referenced after the stage-1 thread returns: only a
      `StructuralScanResult` reaches the combine (test or code-review assertion).
- [ ] The 4 tag-consumed `plain` records are not `leaked` in the regenerated baseline. No
      core-genre benign record moves from `passed`, and the pin tests are green.
- [ ] `tests/test_corpus_attacks.py` keeps its extracted-text assertion and gains the
      raw-markup converse through `stage2_markup_hits`.
- [ ] Both cassette files are byte-unchanged, with zero misses. `tests/test_search_pipeline_pins.py`
      is green.
- [ ] `docs/corpus.md` "Decision inputs" tables are updated from the regenerated `offline`
      section, and `tests/test_corpus_docs.py` is green.
- [ ] The rotation is recorded with reversal controls.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` reports 0 errors

## Edge Cases

- Nested non-block elements (`<b><i>x</i></b>`): all are unwrapped, then smoothed (US-001).
- An inline element containing a block element (invalid HTML): lxml repairs it, and block
  boundaries still produce newlines (US-001).
- `<br>` inside a trigger: a line break by design (it is in the block set). Spec 1's bounded
  gaps cover the two gap patterns (US-001).
- A non-HTML `/retrieve` response: `scan_text_inline` is `None`, so there is no extra form
  (US-001).
- A subset trigger inside `<script>` or `<style>`: excluded by design. Those elements never
  reach the wire (US-002).
- Malformed or unterminated tags in raw source: the tokenizer is tolerant. Unparsable spans are
  scanned as-is, never dropped (US-002).
- Entity-encoded attribute values (`href="http://&#49;92.168…"`): not decoded for the subset
  scan. This is an accepted residual, filed in spec 4 (US-002).
- Benign intranet links to RFC 1918 hosts: `private_ip_href` is SUSPICIOUS (a flag, not a
  block). Watch the `docs` genre (US-002).

## Out of Scope

- Rendering or CSS evaluation. Hidden content is spec 3.
- Tag splits inside `/extract` plain-text uploads.
- Attribute channels the corpus didn't exercise (`aria-label`, `data-*`), and entity-encoded
  attribute values. These are residuals for spec 4.

## Assumptions

- The block set above is the complete set of elements that should separate text for scanning.
  Everything else is joined, which is the safe direction for catch.
- `ExtractionResult` is internal, so adding a defaulted field changes no response model and no
  contract.

## Technical Considerations

- **Hashed files:** `stage1_extraction.py` and `orchestrator.py` (US-001); `stage2_structural.py`
  and `orchestrator.py` (US-002). Expect a rotation per story.
- The no-re-record guarantee rests on `raw_text` and `/search` wire forms staying unchanged.
  Cassette misses are a design error.
- **The decision-input tables move** with new blocks. Update `docs/corpus.md` and its dependent
  prose in the same story (CI test).
- **Findings checks skip in worktrees** (gitignored `AUDIT_FINDINGS.md`). Re-filing is spec 4's
  owner step.

## Related Documentation

- Spec 1: [feature-structural-scan-forms.md](feature-structural-scan-forms.md) — builder, combine rule, bounded gaps
- Corpus guide: [docs/corpus.md](../../docs/corpus.md)
- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Unwrap every non-block element, then `smooth()`.
**Rationale:** Measured on bs4 4.15.0: `unwrap()` + `get_text("\n")` catches 0/8 and adding
`smooth()` catches 8/8. A closed inline allowlist is bypassed by `wbr`, `font` or custom
elements; a closed block allowlist isn't.
**Alternatives considered:** deleting newlines (catches 7/8 and breaks line anchors); changing
`raw_text`'s separator (forces a re-record).
**Source:** `pipeline/stage1_extraction.py:265`; validation reviewers 3, 4 and 6, 2026-10-06.

**Decision:** Scan raw source with offset-cut spans and collapsed whitespace, over the whole body.
**Rationale:** lxml round-trips drop stray end tags (`atk-0160`, `atk-0161` missed).
`envelope_breakout` backtracks quadratically on whitespace runs (19.4 s on 80k characters),
and the whitespace collapse is match-preserving for `\s*` patterns. A head-only bound is a
padding bypass.
**Alternatives considered:** soup serialisation (misses records); a prefix bound (bypassable).
**Source:** `pipeline/stage2_structural.py:94,212,236`; validation reviewers 5 and 6.

**Decision:** The subset is defined by compiled-pattern reference, and the scan runs inside the
stage-1 thread.
**Rationale:** `_PATTERNS` carries no names. Carrying markup past stage 1 would break the
"queued request holds no HTML" property (`orchestrator.py`, around :585).
**Source:** validation reviewer 4; `pipeline/retrieve_limits.py:48`.

**Landscape:** Unit 42's in-the-wild catalogue
(<https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/>, 2026-03-03) lists further
markup and attribute channels. Add corpus rows for them before adding any new surface.

### Scope Adjustments
- 2026-10-06 validation:
  - The inline allowlist was inverted to a block allowlist, and `smooth()` was added.
  - The re-serialised markup input was replaced by raw source with offset cuts.
  - The head-only bound was replaced by a whole-body scan.
  - Whitespace collapse was added (ReDoS).
- `/extract` was excluded from US-002 (plain-text uploads).

### Decisions Made
- See Research Findings.

## Clarifications

### Session 2026-10-06
- Q: Should normalised text reach stage 3? → A: no. Stage 2 only (spec 1 Clarifications has the full epic Q&A).
- Q (validation): Should the raw-markup scan have a bound? → A: no bound beyond the 10 MB fetch cap. Whitespace collapse makes a full scan cheap, and any bound is a padding bypass.
