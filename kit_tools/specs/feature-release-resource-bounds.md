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

Finding **2026-10-06-001**: stage 1 (`extract_html`, which builds a BeautifulSoup tree on lxml
and runs the visibility pass, the inline walk and trafilatura) has no ceiling on `/retrieve`.

- **Cost depends on shape, and some shapes are superlinear.** Measured during validation
  (2026-10-07, macOS arm64, GC off, `_extract_html_and_scan_inline`):
  - Sibling-dense runs: 0.29 s at 128 KiB → 3.16 s at 1 MiB (linear).
  - **Deep nesting: 0.76 s at 128 KiB → 11.1 s at 512 KiB → 42.9 s at 1 MiB (quadratic).**
  - Element-dense at 10 MB: about 150 s (the finding's own figure).
- **Why it hurts.** The parse runs in a thread, but it holds the GIL and the single `/retrieve`
  admission slot the whole time.
- **`/search`.** It calls `extract_html` **on the event loop** for every title and snippet
  (`orchestrator.py` `_scan_forms_for_search_text` ~:1028-1075; result loop ~:1853/:1888).

The fix bounds the work rather than speeding it up:

- **Above a calibrated size threshold**, `/retrieve` HTML bodies parse in a spawned child under
  the same CPU, address-space and wall-clock rlimits as the fetched-PDF worker. Overrun is a coded
  422.
- **At or below the threshold** they parse in-thread exactly as today, so ordinary pages pay no
  spawn cost.
- `/search`'s parse moves into a worker thread.
- **Both workers run with a scrubbed environment.** No cache URL, HMAC key or provider credentials
  reach a process that parses hostile input with native code.
- Stage-1 output is **byte-identical**, so stage-3 input and both cassettes are untouched.

**Accepted envelope (owner, 2026-10-07).** Realistic markup costs about 130 MiB of RSS per MB in
the child (1 MB → 225 MiB, 4 MB → 594 MiB, measured), and CPU grows faster than linear (1.6 s at
1 MB, 9.1 s at 4 MB). Under the 384 MiB / 20 s limits, realistic pages above roughly 2 MB are
therefore **refused** `html_extraction_error`. That is intended: under spec 3's 256-chunk default
a realistic 1 MB page (~481k characters) already exceeds the 458,752-character budget. US-002
measures the largest realistic page that fits, and the docs state it.

## Goals

- **Worker path.** A deep-nesting `/retrieve` body above the threshold is refused
  `422 extraction_failed` / `html_extraction_error`, and no child process survives. This is
  measured with a real worker and the test's `child_cpu_seconds: 1`.
- **In-thread path.** The worst of four hostile shapes (sibling-dense, deep nesting,
  attribute-heavy, unclosed-tag soup) at the default threshold parses in-thread within a
  machine-calibrated ceiling. The default is chosen so that worst case is ≤ 2 s on the
  development machine. A regression test guards it.
- **Output unchanged.** For **every** corpus `/retrieve` HTML record, the worker result equals the
  in-thread result. The corpus baseline regenerates with zero outcome changes, and both cassettes
  are byte-unchanged.
- **Credentials.** The child's environment contains none of the credential-bearing variables
  (test).
- **`/search`.** Zero `extract_html` calls on the event-loop thread (thread-identity test). A
  concurrent ticker's maximum loop lag during a hostile provider response stays below the lag
  recorded before the story. The six `hardening-provider-bounds` US-005 captures are unchanged.

## User Stories

### US-001: Shared worker plumbing — supervise, spool, scrubbed environment, stale-spool sweep

**Priority:** P1

**Description:** As an operator, I want the PDF worker's process supervision and spool handling
factored into reusable helpers, every worker child started with a credential-free environment,
and stale spool files swept at startup, so that a second worker can reuse reviewed code and a
parser compromise reaches no secrets.

**Independent Test:** The existing PDF worker tests pass unchanged on the refactored helpers. A
real PDF worker child reports an environment without the credential variables. A planted stale
spool file (owner euid, Forage prefix) is removed at startup while a symlink or foreign-prefix
file is left alone.

**Implementation Hints:**
- **Extract the generic parts of `pipeline/pdf_subprocess.py`** (unhashed, so no rotation):
  - The spawn-and-supervise loop (:204-243): `get_context("spawn")`, one-way `Pipe`, poll
    deadline, `recv_bytes(cap)`, and `kill`/`join`/close in `finally`. It returns the decoded
    frame dict, or `None` on any failure.
  - The 0600 spool context manager (:270-299), taking a filename prefix.
  - A frame sender parameterised by `ensure_ascii`. `_send_child_result` (:150-159) hardcodes
    `ensure_ascii=True` and swaps an oversized payload for `{"status":"failed"}`; keep that
    oversize behaviour.
  - The PDF worker then calls these. Its tests (`tests/test_stage1_pdf.py`
    `TestExtractPdfBytesInSubprocess` :464 and `TestSpoolDir` :346, plus the orchestrator PDF
    mapping ~2620-2830) are the regression guard, and must pass with no edits beyond imports.
- **Environment scrub (owner ruling, 2026-10-07: both workers).**
  - `spawn` inherits `os.environ`. Set the child environment to an **allowlist** of what the
    interpreter and imports need: `PATH`, `HOME`, `LANG`/`LC_*`, `TMPDIR`, `PYTHONPATH`, `VIRTUAL_ENV`,
    `PYTHONHASHSEED` if set, and HF cache dirs only if an import touches them (check).
  - Credential variables to exclude explicitly, as a test fixture list: `VALKEY_URL`,
    `FORAGE_CACHE_HMAC_KEY`, `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `BRAVE_API_KEY`, and any
    `*_API_KEY`/`*_TOKEN`/`*_SECRET`/`*_PASSWORD` pattern. Grep `os.environ`/`getenv` across the
    repo for the full set.
  - **Mechanism.** `multiprocessing` spawn has no per-process `env=` argument. Options: (a)
    temporarily swap `os.environ` around `Process.start()` under a module lock; (b) have the
    child clear its own environment as the first statement of its entry function, before
    importing parsers.
    - (b) is simpler and race-free. The secrets are still readable in `/proc/<pid>/environ` of
      the child's initial image on Linux, but are gone from `os.environ` before any hostile byte
      is parsed.
    - Measure which of (a) and (b) actually prevents `/proc/self/environ` exposure. Choose, and
      record the choice and its residual in `kit_tools/arch/SECURITY.md`.
  - **Test.** The child reports `sorted(os.environ)` through a test-only frame field or a test
    seam. Assert that no excluded name is present.
- **Stale-spool sweep.**
  - Give spools a per-worker prefix (`forage-retrieve-pdf-`, `forage-retrieve-html-`; check the
    existing `forage-retrieve-` prefix and keep compatibility).
  - At startup (where the lifespan already checks `spool_dir()`, `retrieval_app.py:1713-1716`),
    remove regular files directly in the spool dir that start with a Forage prefix and are owned
    by the euid.
  - Use `os.scandir` + `lstat`. Never follow symlinks, never recurse.
  - Log one closed token with a count only (CLAUDE.md invariant 6: never a path that could carry
    a value). Document the at-rest behaviour in SECURITY.md and `docs/configuration.md`.
- **GOTCHAS to respect.** "Cancelling a PDF await does not stop its worker thread" (:856-871). The
  helpers must keep the parent reaping on every path. Mock contexts are process-wide (:243-253).

**Acceptance Criteria:**
- [ ] `pdf_subprocess.py` exposes reusable supervise, spool and frame-send helpers, and
      `extract_pdf_in_subprocess`/`extract_pdf_bytes_in_subprocess` use them. Every existing PDF
      worker test passes with at most import edits.
- [ ] A real PDF worker child's `os.environ` contains none of the excluded credential names
      (fixture list plus pattern) when the parent's environment has all of them set (test). The
      chosen mechanism and its `/proc` residual are recorded in `kit_tools/arch/SECURITY.md`.
- [ ] Startup removes a stale euid-owned file carrying a Forage spool prefix. It does not remove
      a symlink, a foreign-prefix file, or a file in a subdirectory (tests). The log record
      carries only a closed token and a count.
- [ ] `docs/configuration.md` and `SECURITY.md` describe the spool's at-rest lifecycle, including
      the sweep.
- [ ] `sanitizer_revision` is unchanged (no hashed file edited). Before and after values are
      recorded in Implementation Notes.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: The HTML extraction worker — shared stage-1 function, strict frame, measured envelope

**Priority:** P1

**Description:** As an operator, I want an HTML worker that runs today's in-thread stage-1 work
(extraction plus the raw-markup and inline scans) in a rlimited child, returning a strictly
validated plain-data frame, so that the parse cost of a hostile page is bounded by the OS.

**Independent Test:** Call the new bytes entry point directly.
- For every corpus `/retrieve` HTML record, its result equals the untouched
  `orchestrator._extract_html_and_scan_inline`.
- A deep-nesting body with `child_cpu_seconds=1` raises `HTMLExtractionError`.
- A forged frame for each field is rejected.

**Implementation Hints:**
- **Module.** `pipeline/html_subprocess.py`, new and **unhashed**. It holds the shared function
  `extract_html_and_scan(html: str, url: str, budget_characters: int | None) -> tuple[ExtractionResult, StructuralScanResult | None]`.
  - Its body is a copy of `orchestrator._extract_html_and_scan_inline` (:236-260).
  - It does **not** live in `stage1_extraction.py`: `stage2_structural.py:19` imports
    `normalize_text` from there, so that would be an import cycle.
  - The child cannot import `orchestrator.py`, which pulls in httpx, cache, models and providers.
  - **This story does not edit `orchestrator.py`.** The equivalence test compares against the
    orchestrator's original function. US-003 re-points the orchestrator to the shared function
    and owns the rotation.
- **Entry point.**
  `extract_html_bytes_in_subprocess(body: bytes, url: str, budget_characters: int | None, settings: ExtractionSettings) -> tuple[ExtractionResult, StructuralScanResult | None]`.
  - Spool with the US-001 helper (prefix `forage-retrieve-html-`), supervise with the US-001
    helper, and use the scrubbed environment.
  - The child decodes `body.decode("utf-8", errors="replace")`, the same decode the parent does
    today (`orchestrator.py:636`), then calls `extract_html_and_scan`.
- **The frame** (`ensure_ascii=False`). `status`, then:
  - **Extraction fields:** `title`, `author`, `date`, `raw_text`, `main_content`, `word_count`,
    `main_content_is_fallback`.
  - **The scan, or null:** `verdict`, `penalty`, and `flags` as a list of `[category, line_number]`.
  - **Never sent:** `scan_text_inline`, and never `matched_text`. Both are page text, and nothing
    downstream of stage 2 reads them (`stage4_structuring.py:141, 167-168`).
  - The parent rebuilds each `FlaggedSpan` with `matched_text=""`. Before relying on that, grep
    that no log line or response serves `matched_text` from a `/retrieve` HTML scan.
  - **Enumerate** every field of `ExtractionResult` (`stage1_extraction.py:130-148`) and
    `StructuralScanResult`/`FlaggedSpan` (`stage2_structural.py:28-43`) in the module docstring,
    and say how each crosses or is rebuilt.
- **Strict parent validation.** The frame now carries a security verdict, so the pipe is a trust
  boundary. Exact key set (no extras):
  - `verdict` is in the `Stage2Verdict` enum.
  - `penalty` is a float in [-0.45, 0.0].
  - Every category is in `_BLOCKING_CATEGORIES ∪ _SUSPICIOUS_CATEGORIES` plus the markup
    categories (`_MARKUP_CATEGORIES` values).
  - `line_number` is an int ≥ 0.
  - String fields are within size, and `word_count` is an int ≥ 0.
  - Any violation raises `HTMLExtractionError` (fail closed). One test per field forges a frame
    through the parent's decode/validate seam.
- **Frame cap.** The PDF cap (~466 KB, `extraction_limits.py:92-98`) is far too small. Define
  `MAX_HTML_FRAME_BYTES` in `html_subprocess.py`, derived from
  `stage5_url_audit.DEFAULT_MAX_CONTENT_BYTES`, so that the largest result an admissible body can
  produce fits.
  - Measure the bytes per character on the worst case rather than assuming 6: does stage 1 strip
    C0 controls before JSON?
  - Also measure the **parent's** peak RSS while receiving, validating and decoding a worst-case
    frame, and record it. If it would exceed about 25% of the 1536 MiB default, lower the cap to
    what the budget can produce (since a 256-chunk budget refuses anything larger) and say so.
- **Failure vocabulary.** The child reports `ok` | `failed`, deliberately indistinguishable (as
  `contract.py:531-534` does for PDFs). The parent raises one `HTMLExtractionError`. Spawn failure
  (`OSError`/`EAGAIN` from `Process.start()`) is mapped to `HTMLExtractionError` too. Parser
  exception text never crosses the pipe or reaches a log.
- **Kill test.** Use the **deep-nesting** shape: about 256 KiB of nested `<span>`s costs about 3 s
  in-thread on a fast machine, so it overruns `child_cpu_seconds=1` everywhere. Assert the outcome
  (`HTMLExtractionError`, no surviving child via `multiprocessing.active_children()`, elapsed
  < `wall_clock_seconds`). Do not assert a tight wall time: child import cost varies on CI.
  Record the measured spawn-plus-import time. **Never** push 10 MB element-dense HTML through
  `extract_html` in a test (archived `feature-structural-markup-surface.md:177`).
- **Envelope measurement** (record only, in Implementation Notes and `docs/configuration.md`):
  - Run in a Linux container (`docker run --cpus 1 -m 1536m` on the repo image or a
    `python:3.13` image with `uv sync`).
  - Use realistic pages (div/h2/p/a/em/nested-ul blocks) at 0.5, 1, 2 and 4 MB.
  - Record child **VmPeak** (from `/proc/self/status`; `RLIMIT_AS` bounds virtual size, not
    RSS), plus CPU seconds including import.
  - State the largest realistic page that parses under 384 MiB / 20 s. Pages above it are refused
    by design (owner, 2026-10-07).
  - macOS does not enforce `RLIMIT_AS` (`pdf_subprocess.py:88-95`); note it.

**Acceptance Criteria:**
- [ ] `pipeline/html_subprocess.py` holds `extract_html_and_scan` and
      `extract_html_bytes_in_subprocess`, using the US-001 helpers and the scrubbed environment.
      `orchestrator.py` is not edited by this story.
- [ ] For every corpus `/retrieve` HTML record, plus one synthetic non-UTF-8 body and one
      over-budget body with a budget set, the worker's `ExtractionResult` and scan
      `verdict`/`penalty`/categories/line numbers equal
      `orchestrator._extract_html_and_scan_inline`'s result (test).
- [ ] The raw frame bytes contain neither `scan_text_inline` nor a marker string that occurs only
      inside a matched span (test). The module docstring enumerates every field's crossing or
      rebuild.
- [ ] The parent rejects a forged frame for each of: an extra key, a bad verdict, an out-of-range
      penalty, an unknown category, a negative line number, an oversized string, and a non-int
      word count. Each rejection raises `HTMLExtractionError` (one test each).
- [ ] `MAX_HTML_FRAME_BYTES` is derived and documented. A test shows the largest admissible
      result fits, and that an over-cap frame becomes `HTMLExtractionError`, not a truncated
      result. The parent's peak RSS on a worst-case frame is recorded.
- [ ] A real worker given the deep-nesting body with `child_cpu_seconds=1` raises
      `HTMLExtractionError` within `wall_clock_seconds`, and no child survives. A spawn
      `OSError` maps to `HTMLExtractionError` (test).
- [ ] The Linux-container envelope (VmPeak and CPU per realistic size, and the largest page that
      fits) is recorded in Implementation Notes and stated in `docs/configuration.md`.
- [ ] `sanitizer_revision` is unchanged (no hashed file edited), with the values recorded.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-003: Route large `/retrieve` HTML bodies through the worker, with telemetry

**Priority:** P1

**Description:** As an operator, I want `/retrieve` HTML bodies above a calibrated byte threshold
to parse in the worker inside the admission slot, refused with a coded 422 on overrun, and counted
on `/metrics`, so that a hostile page costs at most the worker's limits and I can see how often
the path fires.

**Independent Test:** Drive `run_retrieve_pipeline` with a stubbed fetch:
- a body at the threshold never spawns;
- a body one byte over calls the worker seam;
- a worker failure maps to `422 extraction_failed` / `html_extraction_error`, increments the
  refusal counter, and releases admission only after the worker is reaped.

**Implementation Hints:**
- **Re-point the in-thread path.** `orchestrator._extract_html_and_scan_inline` becomes a thin
  call to `html_subprocess.extract_html_and_scan`, or is removed and its callers updated.
  - About 26 test sites patch `pipeline.orchestrator.extract_html` or call the function directly
    (`test_orchestrator.py`, `test_app.py:1527`, `test_retrieve_admission.py:282/394/564-572`,
    `test_inline_scan_form.py`).
  - Keep `extract_html` imported where those patches expect it, or update the patches. Their
    bodies are tiny, so they stay in-thread.
- **Branch.** The HTML branch of `run_retrieve_pipeline` (~:635-647) mirrors the PDF branch
  (~:585-634): `completed_thread(asyncio.to_thread(extract_html_bytes_in_subprocess, ...))`
  **inside** the admission slot (acquired ~:528, released in `finally` ~:655-656).
  - Never put a timer around `acquire()` (GOTCHAS:873-884).
  - The decision compares the **fetched byte length** before decode, so encoding cannot game it.
- **Contract constant.** Add `RETRIEVE_HTML_EXTRACTION_ERROR = "html_extraction_error"` to
  `pipeline/contract.py` beside `RETRIEVE_PDF_*` (:527-546).
  - It is a constant only. This story does **not** edit any response description or
    `CONTRACT_VERSION` (US-004 does), so the frozen 1.3.0 golden stays green.
  - `contract.py` is hashed, so it joins this story's rotation.
- **Failure mapping.**
  - `HTMLExtractionError` → 422 `extraction_failed` / `html_extraction_error`.
  - Spool `OSError` / `SpoolDirectoryError` → the existing spool reason, exactly as the PDF branch
    maps it.
  - Log one closed WARNING token per class (`retrieve_html_extraction_failed`,
    `retrieve_spool_error` reused). Never exception text.
- **Threshold knob.** `retrieve.html_worker_threshold_bytes` in `pipeline/retrieve_limits.py`
  (unhashed), validated with `config_bounds.bounded_int`.
  - Range `0 … 1 MiB`. `0` means every HTML body goes to the worker. The maximum is deliberately
    far below the 10 MB fetch cap, so an operator cannot silently disable the bound.
  - Document that raising the threshold weakens the bound.
  - Add the key to `config.yaml`, `bench/config.yaml`, `docs/configuration.md` and
    `kit_tools/docs/ENV_REFERENCE.md`.
- **Calibration** (record per shape in Implementation Notes).
  - Four hostile shapes: sibling-dense `<b>g</b>`, deep `<span>` nesting, attribute/style-heavy
    elements, and unclosed-tag soup. GC disabled.
  - The default is the largest power-of-two KiB value at which the **worst** shape parses
    in-thread in ≤ 2 s on this machine. Validation measured about 128 KiB for nesting on an
    M-series laptop.
  - Add a regression test that runs each shape at the default threshold in-thread under a
    machine-calibrated ceiling, using the `_calibration()`/`_ceiling()` pattern from
    `tests/test_stage2_complexity.py`.
- **Telemetry.** Two `/metrics` counters, following the existing `retrieve.*` counter pattern in
  `retrieval_app.py`'s metrics mirror:
  - `retrieve.html_worker_spawns`
  - `retrieve.html_worker_refusals`

  `/metrics` keys are contract surface (`tests/test_contract_metrics.py`), so they are announced
  in US-004's 1.4.0 entry. Check that the metrics schema test passes with additive keys under the
  1.3.0 golden. If it pins the key set, move the counters into US-004.
- **Cancellation.** Real-task `cancel()` with the worker held by a threading event: admission is
  released only after reap and unlink (GOTCHAS:856-871).
- **Docs that become false** (`docs/configuration.md`): "HTML-only deployment … spawns no worker"
  (:975-977), "No wall clock is put on HTML extraction" (:1022-1023), "The HTML path writes
  nothing to disk" (:1013), and the queue-latency formula (:992-996). Also the
  `RetrieveSettings` docstring (`retrieve_limits.py:67-79`).
- **Rotation.** `orchestrator.py` and `contract.py` move. Follow the **Rotation record procedure**
  in Technical Considerations.

**Acceptance Criteria:**
- [ ] The in-thread path calls `html_subprocess.extract_html_and_scan`, and there is no second
      copy of the extract-and-scan body in the repo (grep).
- [ ] A body exactly at the threshold parses in-thread, and one byte over calls the worker. `0`
      routes every HTML body to the worker. The decision uses fetched byte length (tests).
- [ ] `retrieve.html_worker_threshold_bytes` is bounded to `0 … 1 MiB` and documented in
      `config.yaml`, `bench/config.yaml`, `docs/configuration.md` and `ENV_REFERENCE.md`, with
      the weakening note. Its default is the calibrated value, recorded per shape.
- [ ] A regression test runs all four hostile shapes at the default threshold in-thread under a
      machine-calibrated ceiling.
- [ ] `HTMLExtractionError` maps to 422 `extraction_failed` / `html_extraction_error`, and spool
      errors map to the existing spool reason. Each logs one closed token and never exception
      text (tests per class).
- [ ] `retrieve.html_worker_spawns` and `retrieve.html_worker_refusals` increment on the worker
      path and the refusal path (tests).
- [ ] Admission is released only after reap and unlink under real-task cancellation (test).
- [ ] For every corpus `/retrieve` HTML record, the response at the default threshold equals the
      response at `0`. The corpus baseline regenerates with zero outcome changes, and both
      cassettes are byte-unchanged.
- [ ] `tests/golden/contract_1_3_0.json` and `CONTRACT_VERSION` are unchanged.
- [ ] The rotation is recorded per the procedure, and the four false `docs/configuration.md`
      statements and the `RetrieveSettings` docstring are corrected.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-004: Contract surface — announce `html_extraction_error`, cut held 1.4.0

**Priority:** P1

**Description:** As a consumer, I want the `/retrieve` 422 description to name
`html_extraction_error`, and the new counters announced, under a held contract 1.4.0, so that the
published surface matches the new refusal.

**Independent Test:**
- `CONTRACT_VERSION == "1.4.0"`.
- `tests/golden/contract_1_4_0.json` matches the live schema.
- `contract/openapi.yaml` matches its regenerated anchor.
- The 1.3.0 golden is byte-unchanged.

**Implementation Hints:**
- **Why the bump is decided now.** The `/retrieve` 422 error description is pinned in
  `tests/golden/contract_1_3_0.json` (~:292). It is sourced from `retrieval_app.py:1039-1042`
  ("extraction_failed arrives on /retrieve only, as a fetched PDF…"). Adding
  `html_extraction_error` there moves a pinned schema, and `test_contract_schema.py:22` selects
  the golden by `CONTRACT_VERSION`. So this story bumps to `"1.4.0"` and creates the golden. It
  is **held**: later stories (spec 3) regenerate it until the tag, as the 1.3.0 golden was during
  its window.
- **`pipeline/contract.py`.**
  - `CONTRACT_VERSION = "1.4.0"` (:22).
  - A new `* ``1.4.0`` —` docstring bullet, written as an in-progress record that spec 3 US-003
    finalises: `html_extraction_error` under `extraction_failed`, the two counters, and the
    worker-threshold refusal of large realistic pages (a served-outcome change).
  - Column 0, continuation lines indented two spaces, no blank lines.
  - Tense guard: `tests/test_ci_workflow.py:2410-2430`.
  - Update the `RetrieveErrorCode` docstring (:382-401).
- **Golden.** Create `tests/golden/contract_1_4_0.json` with the same six models (pattern:
  `tests/test_contract_schema.py:22-30`). The 1.0.0–1.3.0 goldens stay byte-unchanged.
- **Export.** Run `uv run python -m scripts.export_contract`, which writes `contract/openapi.yaml`,
  its `.sha256` and `tests/fixtures/contract/unregenerated_openapi.yaml`. Never hand-edit them.
  - Update the four doc anchor quotes that `tests/test_contract_export.py` checks.
  - Pin `tests/test_bench_promptguard.py:83, :307` (`== "1.3.0"`).
  - Fix `contract/GOVERNANCE.md:29` (`test_governance_docs.py:281-296`) and the "currently
    **1.3.0**" line in `CLAUDE.md` invariant 4.
- **Rotation.** `contract.py` moves; follow the procedure.

**Acceptance Criteria:**
- [ ] The `/retrieve` 422 description and the `RetrieveErrorCode` docstring name
      `html_extraction_error` (with the PDF reasons).
- [ ] `CONTRACT_VERSION == "1.4.0"`, with a 1.4.0 docstring bullet covering the reason, the
      counters and the large-page refusal. The tense guard passes.
- [ ] `tests/golden/contract_1_4_0.json` exists and matches, and goldens 1.0.0–1.3.0 are
      byte-unchanged (`git diff --stat`).
- [ ] The OpenAPI file, its anchor and the fixture twin are regenerated by the export script.
      `tests/test_contract_export.py`, the doc anchor quotes and the `test_bench_promptguard.py`
      pins are green.
- [ ] The rotation is recorded per the procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-005: `/search` parses titles and snippets off the event loop

**Priority:** P2

**Description:** As an operator, I want `/search`'s per-result scan-form building to run in a
worker thread, so that a hostile provider response cannot block every other request on the event
loop thread.

**Independent Test:** With a stub provider returning several results:
- record the thread identity of every `extract_html` call during `run_search_pipeline`; none is
  the loop's thread;
- the response bytes equal the pre-story capture;
- a concurrent ticker's maximum lag is below the pre-story figure.

**Implementation Hints:**
- **Call sites.** `_scan_forms_for_search_text` (~:1028-1075), its callers in the result loop
  (~:1853, ~:1888), and the raw-markup scans (~:1960-1964).
- **One `asyncio.to_thread` per result**, building both fields' scan forms and fold forms, wrapped
  in `completed_thread` (`stage3_promptguard.py:68`). One per field doubles the handoffs; one per
  response keeps the loop blocked until the batch ends.
- **What this does and does not fix.** CPython's GIL still serialises the parse with the loop's
  bytecode, so total CPU is unchanged and other requests still slow under contention. What changes
  is that the loop thread regains control at the interpreter's switch interval instead of being
  held for a whole parse. Prove it with a measurement:
  - A ticker task records its maximum inter-tick lag while a stub provider returns 10 results of
    hostile 2,048-character titles and 8,000-character snippets (the existing input caps).
  - Record the pre and post maximum lag, and assert that post < pre.
- **Byte-identical.** The six captures pinned in `hardening-provider-bounds` US-005's first commit
  (`8e449fc`) and the `/search` corpus outcomes stay unchanged.
- **Resource-envelope counters** (`hardening-resource-envelope` US-004, `search_targets.py`):
  - With a stub parse delay and no semaphore contention, the classification-wait counter and the
    high-water mark equal the pre-story capture (test).
  - `docs/configuration.md` states which of the two includes thread-hop time.
- **Rotation.** `orchestrator.py` moves; follow the procedure.

**Acceptance Criteria:**
- [ ] No `extract_html` or `scan_raw_markup` call inside `run_search_pipeline` runs on the event
      loop thread (thread-identity test with ≥ 3 results).
- [ ] The maximum ticker lag under the hostile 10-result provider is lower after the story than
      before. Both values are recorded in Implementation Notes and asserted by a test with a
      calibrated margin.
- [ ] The six `hardening-provider-bounds` US-005 captures are byte-unchanged, and `/search` corpus
      outcomes are unchanged.
- [ ] A cancelled `/search` request does not return before its in-flight parse thread finishes
      (`completed_thread` test).
- [ ] With a stub parse delay and no contention, the classification-wait counter and the
      high-water mark equal the pre-story values (test). `docs/configuration.md` states which of
      them includes thread-hop time.
- [ ] The rotation is recorded per the procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- **Threshold boundaries.** A body exactly at the threshold parses in-thread. `0` sends everything
  to the worker. The 1 MiB maximum is enforced at boot. (US-003)
- **Worker failure modes.** The worker running out of address space (Linux), the CPU limit, the
  wall clock, spawn `EAGAIN`, an oversized frame and bad JSON all become `HTMLExtractionError`,
  which maps to 422. (US-002, US-003)
- **Forged or buggy frames** are rejected field by field and fail closed. (US-002)
- **The spool directory missing or unsafe at request time** takes the existing
  `SpoolDirectoryError` path. (US-003)
- **Leftover spool files from a SIGKILLed process** are swept at the next startup. (US-001)
- **Cancellation mid-parse.** The child is killed and reaped, the spool unlinked, and only then
  is admission released. (US-003)
- **Non-UTF-8 bodies.** The child applies the parent's exact `errors="replace"` decode, covered
  by the equivalence test. (US-002)
- **Over-budget pages.** The over-budget short-circuit behaves identically in the child, covered
  by the equivalence test. (US-002)
- **Realistic pages above the measured envelope** are refused `html_extraction_error` by design,
  and the docs say so. (US-002, US-004)
- **A `/search` provider returning zero results** makes no thread hop. (US-005)

## Out of Scope

- A compiled or faster HTML parser (selectolax/lexbor, Rust). It changes stage-1 output, so it
  needs a cassette re-record. Backlog item.
- A warm worker pool. The owner chose the threshold (2026-10-07).
- Raising worker memory for HTML. The owner accepted the refusal envelope (2026-10-07).
- `/extract`. It never parses HTML (`stage1_upload.py:78-158`).
- Changing `fetch_concurrency` (1), or the PDF worker's limits.

## Assumptions

- Reusing `child_cpu_seconds` (20), `child_address_space_bytes` (384 MiB) and
  `wall_clock_seconds` (90) for HTML is correct. The bound is the point, and large realistic pages
  are refused by design.
- A spawn per above-threshold page (hundreds of ms) is acceptable for pages that are already
  large.
- Clearing the child environment does not break any import (US-001 verifies this).

## Technical Considerations

- **Hashed vs unhashed.** `orchestrator.py` and `contract.py` are hashed; `html_subprocess.py`,
  `pdf_subprocess.py`, `extraction_limits.py`, `retrieve_limits.py` and `retrieval_app.py` are
  not. US-001 and US-002 must not rotate. US-003, US-004 and US-005 each rotate once.
- **Rotation record procedure** (shared by every rotating story in this epic):
  1. Compute `derive_sanitizer_revision()` under default and shipped config before and after.
  2. For each hashed file the story changed, reconstruct the pre-story bytes **read-only** (load
     the module from `git show <pre-story>:<path>` into a temporary directory; never revert the
     working tree). Recompute with only that file reverted, and once with all of them reverted.
     The all-reverted control must reproduce the pre-story value exactly.
  3. Record a new paragraph in `CLAUDE.md` (Coexistence section), a new heading in
     `docs/bootstrap-notes.md` (next ordinal), and a row in the GOTCHAS rotation table plus the
     tally (`kit_tools/docs/GOTCHAS.md` ~:719/~:782).
  4. Bump every count site: `kit_tools/docs/GOTCHAS.md` (table intro and tally),
     `kit_tools/docs/DEPLOYMENT.md:125`, `kit_tools/docs/TROUBLESHOOTING.md:804` (two sites),
     `kit_tools/arch/SERVICE_MAP.md:210` (also names the current value),
     `kit_tools/arch/CODE_ARCH.md:499`, `kit_tools/arch/DECISIONS.md:1154/1163`,
     `CLAUDE.md`, and `docs/releases.md` Unreleased.
  5. Verify with `grep -rn "fifty-<n>"` for the old and new ordinal words.
- **Stage-3 input** (`raw_text`) must be byte-identical on both paths, or the cassettes stop
  replaying.

## Related Documentation

- [CODE_ARCH.md](../arch/CODE_ARCH.md), [SECURITY.md](../arch/SECURITY.md),
  [GOTCHAS.md](../docs/GOTCHAS.md)
- `docs/configuration.md` (`extraction:` ~:931, `retrieve:` ~:960-1034)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Spawn-per-call, reusing the PDF worker's reviewed plumbing through extracted
helpers.
**Rationale:** Spool safety, reaping and cancellation are already reviewed. Copying them would
create drift.
**Source:** `pipeline/pdf_subprocess.py:80-299` (codebase-fit review, 2026-10-07).

**Decision:** The shared function lives in the unhashed `html_subprocess.py`.
**Rationale:** `stage1_extraction.py` would be an import cycle (`stage2_structural.py:19`). A
spawn child cannot import `orchestrator.py`.
**Source:** Second-opinion and codebase-fit reviews, 2026-10-07.

**Decision:** Calibrate the threshold on four hostile shapes and take the worst.
**Rationale:** Nesting is quadratic (0.76 s at 128 KiB → 42.9 s at 1 MiB). Sibling-only
calibration would pick 512 KiB, where nesting costs 11 s.
**Source:** Second-opinion prototype, 2026-10-07.

**Decision:** Strict frame validation, failing closed.
**Rationale:** The frame carries a stage-2 verdict, so the pipe is a trust boundary.
**Source:** Security review, 2026-10-07.

### Scope Adjustments

- Validation round 1 split the original three stories into five:
  - plumbing and environment scrub;
  - the worker;
  - routing and telemetry;
  - the contract surface;
  - `/search`.
- The environment scrub covers the PDF worker too (owner ruling).
- Telemetry counters were added.

### Decisions Made

- The contract bump to 1.4.0 happens here (US-004), because the pinned 422 description must
  change. Spec 3 finalises the entry.

## Clarifications

### Session 2026-10-07
- **Q:** Would rewriting the workers in C fix the slowdown?
  **A:** No. Only an OS-enforced limit bounds hostile input, and C on attacker HTML is a
  memory-safety risk. A compiled parser goes to the backlog.
- **Q:** How should `/retrieve` route HTML?
  **A:** A size threshold.
- **Q:** Move `/search` parsing off the event loop?
  **A:** Yes, with `to_thread`.
- **Q:** Accept refusal of realistic pages above about 2 MB?
  **A:** Accept, and document it.
- **Q:** Scrub the worker environment for the PDF worker too?
  **A:** Both workers.
