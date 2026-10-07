<!-- Template Version: 2.5.0 -->
---
feature: release-resource-bounds
status: active
session_ready: true
depends_on: []
vision_ref: "T2 hardening follow-through — bound the remaining CPU costs and cut v1.3.0"
type: epic-child
size: L
epic: forage-v1-3-0-release
epic_seq: 1
epic_final: false
created: 2026-10-07
updated: 2026-10-07
---

# Feature Spec: Release Resource Bounds — HTML Parse in the Rlimited Worker, `/search` Parse off the Loop

## Overview

Finding **2026-10-06-001**: stage 1 (`extract_html`: a BeautifulSoup tree on lxml, the visibility
pass, the inline walk and trafilatura) has no ceiling on `/retrieve`.
- An element-dense 10 MB page costs about **150 s**.
- 50k nested spans cost about 17 s (finding 2026-10-06-009).
- The parse runs in a thread, but it holds the GIL and the single `/retrieve` admission slot the
  whole time. One hostile page stalls the service for minutes.
- `/search` is worse in kind: it calls `extract_html` **on the event loop** for every title and
  snippet (`orchestrator.py` `_scan_forms_for_search_text`, ~:1028-1075; the result loop
  ~:1853/:1888). A hostile provider response blocks every request in flight.

The fix bounds the work rather than speeding it up:
- `/retrieve` HTML bodies **above a measured size threshold** parse in the CPU- and
  memory-rlimited spawn worker that fetched PDFs already use. Overrun is a coded 422.
- Bodies at or under the threshold parse in-thread exactly as today, so ordinary pages pay no
  spawn cost (a cold spawn costs hundreds of ms, per `docs/configuration.md:176-180`).
- `/search`'s parse loop moves into a thread.
- Stage-1 output stays **byte-identical**, so stage-3 input and both cassettes are untouched.

## Goals

- **Worker path bound.** An element-dense `/retrieve` HTML body above the threshold is refused
  `422 extraction_failed` / `html_extraction_error`. With the test's lowered
  `child_cpu_seconds: 1`, the refusal comes within the child CPU limit plus 2 s, measured by a
  real-worker test.
- **In-thread path bound.** The worst-case in-thread parse is the threshold-sized element-dense
  body, and it takes ≤ 2 s on the CI runner. That is how the threshold default is chosen;
  measured and recorded.
- **Output unchanged.** For every corpus `/retrieve` record, the extraction fields and the
  structural verdict/penalty/categories are identical whether the page parses in-thread or in the
  worker (equivalence test). `tests/corpus/baseline.json` regenerates with zero outcome changes.
- **`/search`.** It makes zero `extract_html` calls on the event loop thread (asserted by thread
  identity in a test), and its six pinned wire/counter captures from `hardening-provider-bounds`
  US-005 are byte-unchanged.

## User Stories

### US-001: An HTML extraction worker beside the PDF one

**Priority:** P1

**Description:** As an operator, I want HTML extraction to run in a spawned child under the same
CPU, address-space and wall-clock limits as the PDF worker, returning only plain data, so that a
hostile page's parse cost is bounded by the OS rather than by its element count.

**Independent Test:** Call the new bytes entry point directly: with real pages it returns the same
`ExtractionResult` and combined stage-2 scan result as the in-process
`_extract_html_and_scan_inline`; with an element-dense page and `child_cpu_seconds=1` it raises
the HTML extraction error.

**Implementation Hints:**
- **New module** `pipeline/html_subprocess.py`. It is **not** a `_REVISION_SOURCES` member, so it
  does not rotate the hash on its own; keep it that way.
  - Mirror `pipeline/pdf_subprocess.py` end to end: `multiprocessing.get_context("spawn")`,
    `_apply_child_limits` (reuse it; import it, do not copy it), a one-way `Pipe(duplex=False)`,
    one JSON frame, parent polling with a deadline, and `kill()` / `join()` / close in `finally`.
  - Spool to `spool_dir()` exactly as `extract_pdf_bytes_in_subprocess` does (:270-299): 0600
    `fchmod` before write, unlink in `finally`.
- **The child runs the whole of today's in-thread work.** That is
  `orchestrator._extract_html_and_scan_inline` (:236-260):
  `extract_html(html, url, with_inline=True)`, the over-budget short-circuit, `scan_raw_markup`,
  and `scan_structural_forms(structural_scan_forms(inline, html_parsed=True))` combined via
  `combine_scan_results`.
  - Move that function's body somewhere both the parent's in-thread path and the child can call
    it, so the two paths cannot drift. Moving it out of `orchestrator.py` rotates the hash; that
    is acceptable here and recorded (see US-002, which also edits `orchestrator.py`, so record a
    single rotation if both land in the same story order — otherwise one each).
- **Frame contents:**
  - `title`, `author`, `date`, `raw_text`, `main_content`, `word_count`,
    `main_content_is_fallback`.
  - The combined scan as `verdict`, `penalty`, and a list of `category` strings plus line numbers.
  - **Never** `scan_text_inline`, and **never** `FlaggedSpan.matched_text`. That is page text,
    and nothing downstream of stage 2 reads it (`stage4_structuring.py:141, 167-168`). Rebuild
    `FlaggedSpan`s in the parent with an empty or placeholder `matched_text`, and check nothing
    logs or serves it.
- **Frame cap.** The PDF worker's `max_ipc_result_bytes` (~466 KB) is far too small: raw text plus
  main content from a 10 MB body can be ~20 M characters. Derive an HTML frame cap from the fetch
  byte cap (`stage5_url_audit.DEFAULT_MAX_CONTENT_BYTES`) so the largest admissible result always
  fits:
  - JSON `ensure_ascii=False`;
  - worst case 6 bytes per character for escaped controls, unless stage 1 provably strips them —
    measure, don't assume.
  - A test builds the worst-case frame and asserts that it fits.
  - Parent re-validation of types and sizes mirrors `pdf_subprocess.py:251-259`.
- **Failure vocabulary.** `ok` | `failed`, deliberately indistinguishable: parse error, rlimit
  kill, wall clock, oversized frame and bad JSON are all `failed`, matching
  `contract.py:531-534`. Raise one `HTMLExtractionError` in the parent. Parser exception text
  never crosses the pipe.
- **Memory.** `RLIMIT_AS` is enforced on Linux only (`pdf_subprocess.py:88-95`); on macOS the
  parent's supervision is the only memory bound (DECISIONS.md:104/116).
  - Measure the child's peak RSS parsing a realistic 10 MB page (paragraphs, links, nested
    lists — not single-text-node padding) in a Linux container. Record the figure.
  - If 384 MiB (`child_address_space_bytes`) leaves under 25% headroom, add
    `extraction.html_child_address_space_bytes`, validated through `config_bounds.bounded_int`
    in `extraction_limits.py`, and document it.
- **Test-time rule** (archived `feature-structural-markup-surface.md:177`): never push a 10 MB
  element-dense page through `extract_html` in a test. For the kill test, use a ~1 MB
  element-dense body with `child_cpu_seconds=1`. A real `RLIMIT_CPU` kill is reproducible on both
  macOS and Linux; the PDF suite only simulated it.
- **GOTCHAS:**
  - "Cancelling a PDF await does not stop its worker thread": callers must wrap in
    `completed_thread` (`stage3_promptguard.py:68`).
  - Linearity sweeps disable GC while timing (GOTCHAS:56-64).
- Real-worker tests live beside `tests/test_stage1_pdf.py::TestExtractPdfBytesInSubprocess`
  (:464). New file `tests/test_html_subprocess.py`.

**Acceptance Criteria:**
- [ ] `pipeline/html_subprocess.py` exposes a bytes entry point that spawns a `spawn`-context
      child under `_apply_child_limits`. It spools the body under `spool_dir()` with 0600
      permissions, and unlinks the spool and reaps the child on every exit path, including
      exceptions.
- [ ] The in-thread function and the child call one shared implementation. A test asserts that
      for at least 20 corpus `/retrieve` HTML records, the worker's `ExtractionResult` fields and
      scan `verdict`/`penalty`/categories equal the in-thread result.
- [ ] The frame never contains `scan_text_inline` or any `matched_text`. A test inspects a real
      frame's keys and asserts that a marker string present only in a matched span is absent from
      the raw frame bytes.
- [ ] The frame cap is derived from the fetch byte cap. A test shows the largest admissible
      result fits, and that an over-cap frame becomes `failed`, not a truncated result.
- [ ] A real-worker test sends a ~1 MB element-dense body with `child_cpu_seconds=1` and gets
      `HTMLExtractionError` within 1 s + 2 s of wall time. No child process survives (checked via
      `multiprocessing.active_children()` or the pid).
- [ ] Child peak RSS on a realistic 10 MB page is measured on Linux and recorded in this spec's
      Implementation Notes. Either the 384 MiB default leaves ≥ 25% headroom, or the new
      `extraction.html_child_address_space_bytes` key exists, is bounded, and is documented in
      `docs/configuration.md` and `kit_tools/docs/ENV_REFERENCE.md`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Route large `/retrieve` HTML bodies through the worker, with a coded 422

**Priority:** P1

**Description:** As an operator, I want `/retrieve` HTML bodies above a configurable size to parse
in the rlimited worker inside the admission slot, refused with a coded 422 on overrun, so that a
hostile page costs at most the worker's CPU limit while ordinary pages keep today's latency.

**Independent Test:** Drive `run_retrieve_pipeline` with a stubbed fetch:
- a body under the threshold never spawns a worker;
- a body over it calls the worker entry point;
- a worker failure maps to `422 extraction_failed` / `html_extraction_error` and releases
  admission only after the worker is reaped.

**Implementation Hints:**
- **Branch site:** the HTML branch of `run_retrieve_pipeline` (`orchestrator.py` ~:635-647)
  sits beside the PDF branch (~:585-634). Mirror the PDF branch:
  `completed_thread(asyncio.to_thread(<html bytes entry point>, body, settings...))` **inside**
  the admission slot (acquired ~:528, released in `finally` ~:655-656). Never put a timer around
  `acquire()` (GOTCHAS:873-884).
- **Threshold knob:** `retrieve.html_worker_threshold_bytes` in `pipeline/retrieve_limits.py`
  (unhashed), validated with `bounded_int`.
  - Range `0 … DEFAULT_MAX_CONTENT_BYTES`. `0` means every HTML body goes to the worker.
  - Compare the **fetched byte length**, before decode, so the decision cannot be gamed by
    encoding.
  - Default: the largest power-of-two KiB value whose element-dense body parses in-thread in
    ≤ 2 s on the CI runner (expected around 128 KiB). Measure it with GC disabled and record
    machine and figure in Implementation Notes.
  - Add the key to `config.yaml` and `bench/config.yaml`.
- **Contract:**
  - New reason literal `RETRIEVE_HTML_EXTRACTION_ERROR = "html_extraction_error"` in
    `pipeline/contract.py` beside `RETRIEVE_PDF_*` (:527-546), under the existing
    `extraction_failed` code. It is a new reason, not a new code.
  - Update the `RetrieveErrorCode` docstring (:382-401) and the `/retrieve` 422 description.
  - `contract.py` is hashed, so this rotates the revision. Add a **provisional** line under a new
    `* ``1.4.0`` —` entry in the `CONTRACT_VERSION` docstring. Do **not** bump `CONTRACT_VERSION`
    itself; spec 3 US-003 does that and finalises the entry.
  - The tense guard in `tests/test_ci_workflow.py:2410-2430` rejects "held … pending" phrasing,
    so check it.
  - Regenerate with `uv run python -m scripts.export_contract` if the OpenAPI document moves.
  - **Frozen-golden rule.** `tests/golden/contract_1_3_0.json` is frozen: v1.2.x has published
    1.3.0, and the golden pins `Pipeline422ErrorResponse` among six models.
    `test_contract_schema_matches_golden` compares the current models against the golden named
    by `CONTRACT_VERSION`. If adding the reason moves any pinned schema, which happens if
    `reason` is an enum or its description changes, then **this story** bumps
    `CONTRACT_VERSION` to `"1.4.0"` and creates `tests/golden/contract_1_4_0.json`, instead of
    editing the 1.3.0 golden.
    - The 1.4.0 golden stays *held*, so later stories regenerate it until the tag, as the 1.3.0
      golden was during its window.
    - Spec 3 US-002 then finalises the entry rather than bumping.
    - Record which path was taken in Implementation Notes.
- **Admission and cancellation:** keep the PDF branch's cancellation semantics. Admission is
  released only after the worker is reaped and the spool unlinked (GOTCHAS:856-871). The
  regression test blocks the worker with a threading event and calls `cancel()` on the real task.
- **Existing tests:** ~26 test sites patch `pipeline.orchestrator.extract_html` or call
  `_extract_html_and_scan_inline` directly (`test_orchestrator.py`, `test_app.py:1527`,
  `test_retrieve_admission.py:282/394/564-572`, `test_inline_scan_form.py`). Their bodies are tiny,
  so they stay below the threshold and keep working. A spawned child would not see those patches,
  so the new tests patch the parent-side worker seam, the way `extract_pdf_bytes_in_subprocess` is
  patched today.
- **Docs that become false** (`docs/configuration.md`): "HTML-only deployment … spawns no worker"
  (:975-977), "No wall clock is put on HTML extraction" (:1022-1023), "The HTML path writes
  nothing to disk" (:1013). Also `RetrieveSettings`' docstring "the HTML branch spawns no worker"
  (`retrieve_limits.py:67-79`), and the worst-case queue latency formula (:992-996).
- **Rotation record** (`orchestrator.py` and `contract.py` move, and `stage1_extraction.py` if
  US-001 moved the shared function there):
  - Measure each file reverted alone, read-only, from a clean tree, plus an all-reverted control
    reproducing the previous value, under default and shipped config.
  - Record in `CLAUDE.md` (new paragraph), `docs/bootstrap-notes.md` (new heading), and the
    GOTCHAS rotation table and tally (`kit_tools/docs/GOTCHAS.md` ~:719/:782).
  - Bump every "fifty-two" count site: GOTCHAS, DEPLOYMENT:125, TROUBLESHOOTING:804 (two sites),
    SERVICE_MAP:210, CODE_ARCH:499, DECISIONS:1154/1163, CLAUDE.md, `docs/releases.md` Unreleased.
  - This is **not** a text-sanitization change.

**Acceptance Criteria:**
- [ ] `retrieve.html_worker_threshold_bytes` exists, is bounded, and is documented in
      `docs/configuration.md`, `kit_tools/docs/ENV_REFERENCE.md`, `config.yaml` and
      `bench/config.yaml`. Its default is the measured value, recorded with the machine in
      Implementation Notes.
- [ ] A body at exactly the threshold parses in-thread. A body one byte over calls the worker.
      The decision uses the fetched byte length (tests for both, and for `0`).
- [ ] Every `HTMLExtractionError` and spool `OSError` from the worker path maps to
      `422 extraction_failed`.
  - The reason is `html_extraction_error`, or the existing spool reason for spool failure.
  - Tests cover each case.
  - No raw exception text reaches the response or a log record.
- [ ] Admission is released only after the worker is reaped and the spool unlinked, even when the
      request task is cancelled mid-parse. The test is real-task `cancel()` with the worker held
      by a threading event.
- [ ] For every corpus `/retrieve` HTML record, the response is identical with the threshold at
      its default and at `0` (all-worker). The corpus baseline regenerates with zero outcome
      changes, and both cassettes are byte-unchanged.
- [ ] `RETRIEVE_HTML_EXTRACTION_ERROR` is in `contract.py`, with a provisional 1.4.0 docstring
      line. The 1.3.0 golden is byte-unchanged.
  - `CONTRACT_VERSION` is still `"1.3.0"` unless a pinned schema moved, in which case it is
    `"1.4.0"` with a new held `tests/golden/contract_1_4_0.json`.
  - The OpenAPI file and anchor are regenerated if the document moved
    (`tests/test_contract_export.py` green).
- [ ] The rotation is measured (each hashed file reverted alone, plus an all-reverted control
      under default and shipped config) and recorded in `CLAUDE.md`, `docs/bootstrap-notes.md`
      and the GOTCHAS table. Every rotation count site is updated.
- [ ] The three false `docs/configuration.md` statements and the `RetrieveSettings` docstring are
      corrected.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-003: `/search` parses titles and snippets off the event loop

**Priority:** P2

**Description:** As an operator, I want `/search`'s per-result scan-form building (the
`extract_html` and `scan_raw_markup` calls on each title and snippet) to run in a worker thread,
so that a hostile provider response cannot block every other request on the event loop.

**Independent Test:** With a stub provider returning several results, record the thread identity
of every `extract_html` call during `run_search_pipeline`. None is the event loop's thread, and
the response bytes equal the pre-story capture.

**Implementation Hints:**
- **Call sites:** `_scan_forms_for_search_text` (`orchestrator.py` ~:1028-1075) and its callers in
  the result loop (~:1853, ~:1888), plus the raw-markup scans (~:1960-1964).
- The simplest correct shape is one `asyncio.to_thread` per **result**, building both fields' scan
  forms and fold forms. One per field doubles the handoffs; one per response keeps the loop
  blocked until the whole batch finishes. The stage-2 scans already go through `to_thread`.
- **Cancellation:** use `completed_thread` (`stage3_promptguard.py:68`) so a cancelled request
  does not leave a thread running against freed state.
- **Byte-identical output:** the six captures pinned in `hardening-provider-bounds` US-005's first
  commit (`8e449fc`, four full wire/counter runs and two exhaustion runs) must stay unchanged. So
  must `/search` corpus outcomes.
- **Classification-deadline accounting:** `/search` has a one-deadline-per-request classification
  budget and observational targets (`hardening-resource-envelope` US-004, `search_targets.py`).
  Check that moving the parse into a thread does not count parse time against the semaphore wait,
  or change the high-water mark's meaning; document it if it does.
- **Rotation:** `orchestrator.py` moves. Record it as in US-002. If US-002 and US-003 land
  back-to-back, they are still two rotations, one per story.

**Acceptance Criteria:**
- [ ] No `extract_html` or `scan_raw_markup` call in `run_search_pipeline` runs on the event loop
      thread (thread-identity test with ≥ 3 results).
- [ ] The six `hardening-provider-bounds` US-005 captures are byte-unchanged, and `/search` corpus
      outcomes are unchanged.
- [ ] A cancelled `/search` request does not return before its in-flight parse thread finishes
      (`completed_thread` test).
- [ ] The `/search` resource-envelope counters keep their documented meaning. A test or a
      documented note covers parse time versus classification wait.
- [ ] The rotation is measured and recorded, and count sites are updated (as US-002).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- A body exactly at the threshold parses in-thread; threshold `0` sends everything to the worker.
  (US-002)
- The worker runs out of memory under `RLIMIT_AS` on Linux; on macOS only CPU and wall clock bind.
  Both become `failed` → 422. (US-001)
- The spool directory is missing or unsafe at request time. This is the existing
  `SpoolDirectoryError` / `OSError` path, mapped as for PDFs. (US-002)
- The request is cancelled while the child is parsing. The child is killed and reaped, the spool
  unlinked, and admission released, in that order. (US-002)
- A non-UTF-8 body. The parent today decodes with `errors="replace"` before parsing; the child
  must apply the identical decode, so the equivalence test includes one non-UTF-8 record or a
  synthetic one. (US-001)
- An over-budget page (`max_promptguard_chunks` > 0). The short-circuit that skips scans when
  `raw_text` is over budget must behave identically in the child. (US-001)
- A `/search` provider returning zero results means no thread hop is needed; that path is
  unchanged. (US-003)

## Out of Scope

- A compiled or faster HTML parser (`selectolax`/lexbor, Rust). It changes stage-1 output, which
  forces a cassette re-record and a corpus re-baseline. Backlog item.
- A warm, persistent worker pool. The owner chose the size threshold (2026-10-07).
- `/extract`. It never parses HTML (uploads are strict UTF-8 text or PDF, via
  `stage1_upload.py:78-158`).
- Changing `fetch_concurrency` (still 1) or the PDF worker's limits.

## Assumptions

- Reusing the PDF worker's `child_cpu_seconds` (20) and `wall_clock_seconds` (90) is acceptable
  for HTML. A legitimate 10 MB page parses well under 20 s of CPU; US-001 measures this and raises
  it if not.
- The threshold measured on the CI runner (linux/amd64) is a sound default for the reference
  envelope. Operators re-tune it with the documented key.
- A spawn per above-threshold page (hundreds of ms) is acceptable latency for pages that are
  already large.

## Technical Considerations

- **Hashed versus unhashed.** `orchestrator.py`, `contract.py` and `stage1_extraction.py` are
  hashed. `html_subprocess.py`, `pdf_subprocess.py`, `extraction_limits.py` and
  `retrieve_limits.py` are not. Every rotation is measured with read-only whole-file reversals,
  never assumed.
- Stage-3 input (`raw_text`) must be byte-identical on both paths, or the cassettes stop
  replaying. The equivalence test is the guard.
- Parent memory: receiving a ~100 MB worst-case frame allocates that much in the parent, inside
  the 1536 MiB container. Fetch concurrency 1 bounds it to one frame at a time.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md), [SECURITY.md](../arch/SECURITY.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)
- Config: `docs/configuration.md` (`extraction:` ~:931, `retrieve:` ~:960-1034)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Reuse the spawn-per-call PDF worker design (`pdf_subprocess.py`), not a pool.
**Rationale:** It is already reviewed for spool safety, reaping and cancellation. A pool adds
recycling-on-kill and cancellation states.
**Alternatives considered:** A warm pool, rejected by the owner (complexity). Always-worker,
rejected (latency on every page).
**Source:** `pipeline/pdf_subprocess.py:80-299`; `pipeline/orchestrator.py:585-656`.

**Decision:** A new HTML frame cap derived from the fetch cap.
**Rationale:** The PDF `max_ipc_result_bytes` is `min(2 MiB, chars*4+8 KiB)` ≈ 466 KB
(`extraction_limits.py:92-98`). Normal large pages exceed it and would fail.
**Source:** Research 2026-10-07.

**Decision:** The child performs the raw-markup and inline scans, and returns verdicts and
categories only.
**Rationale:** Downstream reads only `verdict`, `penalty` and categories
(`stage4_structuring.py:141,167-168`). `scan_text_inline` and `matched_text` are page text and
should not cross a process boundary.

**Decision:** `/search` gets a thread, not the process worker.
**Rationale:** Up to 40 fields × a spawn of hundreds of ms is unacceptable. The input is already
capped at 4× the field length (`orchestrator.py:977-1013`), so the cost per field is bounded; it
is the event-loop placement that is wrong.

### Scope Adjustments

- The absolute fold-size cap from the original decomposition is dropped (spec 2 explains why).
  The fold-cap work moved to spec 2.

### Decisions Made

- `/extract` is excluded: it does not parse HTML.

## Clarifications

### Session 2026-10-07
- Q: Would rewriting the workers in C fix the slowdown? → A: No, not for this release. A faster
  engine lowers the constant, but only an OS-enforced limit bounds hostile input. Hand-written C
  on attacker HTML is a memory-safety risk, and a crash in-process takes the service down. A
  compiled parser goes to the backlog as a performance item.
- Q: How should `/retrieve` route HTML into the worker? → A: A size threshold. Small pages stay
  in-thread; above a measured size they go to the worker.
- Q: Move `/search` parsing off the event loop in v1.3.0? → A: Yes, via `to_thread`.

## Open Questions

- [ ] Whether HTML needs its own address-space knob. US-001 measures and decides (non-blocking).
