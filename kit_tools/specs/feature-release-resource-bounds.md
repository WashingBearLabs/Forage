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

# Feature Spec: Release Resource Bounds — Linear Stage 1, HTML Parse in a Scrubbed Rlimited Worker, `/search` off the Loop

## Overview

Finding **2026-10-06-001**: stage 1 (`extract_html`) has no ceiling on `/retrieve`. The parse runs
in a thread, but it holds the GIL and the single `/retrieve` admission slot the whole time.
`/search` calls `extract_html` **on the event loop** for every title and snippet.

**What validation measured** (2026-10-07, macOS arm64, GC off, `_extract_html_and_scan_inline`):

| Shape | 64 KiB | 128 KiB | 256 KiB | 1 MiB |
|---|---|---|---|---|
| sibling `<b>g</b>` | 0.15 s | 0.29 s | — | 3.16 s |
| deep `<span>` nesting | 0.29 s | 0.73 s | 2.95 s | 42.9 s |
| repeated unclosed `<span>x` | 1.28 s | 4.71 s | 18.8 s | ≈300 s (extrapolated) |
| attribute-heavy | 0.02 s | 0.04 s | — | — |

**The superlinear part is not lxml or BeautifulSoup parsing.** It is Forage's own
`copy.copy(soup)` in `stage1_extraction._extract_raw_text` (:318) and in the visibility pass
(:529). bs4's `__deepcopy__` inserts each node at O(depth). At 128 KiB of unclosed spans: parse
0.07 s, `copy.copy` 4.62 s.

The fix, in order:

1. **Make stage 1 linear.** Replace both soup copies with a re-parse of the input string (or a
   non-mutating walk), keeping output byte-identical.
2. **Launch workers safely.** A shared worker launcher (`subprocess.Popen`, explicit allowlisted
   environment, pipe passed by fd, parent non-dumpable on Linux) replaces `multiprocessing`
   spawn for the PDF worker and hosts the new HTML worker. No credential reaches a process that
   parses hostile input with native code, and the child cannot read the parent's environment
   through `/proc`.
3. **Sweep stale spools** at startup: age-gated and `dir_fd`-relative.
4. **Route large pages to the worker.** `/retrieve` HTML bodies above a threshold calibrated on
   four hostile shapes parse in the worker under the PDF worker's rlimits. Overrun is a coded 422.
5. **Announce the new surface** under a held contract 1.4.0, with two `/metrics` counters.
6. **Take `/search`'s parse off the event-loop thread.**

Stage-1 output stays **byte-identical** throughout, so stage-3 input and both cassettes are
untouched.

**Accepted envelope** (owner, 2026-10-07): realistic markup costs about 130 MiB RSS per MB in the
child. Under 384 MiB / 20 s, realistic pages above roughly 2 MB are refused
`html_extraction_error`. Spec 3's 64-chunk budget default (114,688 characters) refuses far
smaller pages anyway.

**What the worker does not provide** (accepted residual, recorded in SECURITY.md): the worker
bounds **resource cost**. It does not protect verdict integrity against a *compromised* parser.
A child under native-code control can return a well-formed CLEAN frame with scrubbed text. Frame
validation catches malformed frames, not lies.

## Goals

- **Stage 1 is linear.** The four hostile shapes at 64 KiB and 256 KiB stay within the linearity
  ratio bound of the stage-2 sweep (`_RATIO_BOUND`). Corpus outcomes and both cassettes are
  byte-unchanged.
- **Worker environment.** No credential reaches a worker child. On Linux,
  `/proc/self/environ` holds only allowlisted names, and the child's read of
  `/proc/<ppid>/environ` fails. Tests cover both.
- **Worker refusal.** A hostile body above the threshold is refused `422 extraction_failed` /
  `html_extraction_error` with `child_cpu_seconds: 1`, and no child survives.
- **In-thread bound.** At the default threshold, the worst of the four pinned shapes parses
  in-thread within a machine-calibrated ceiling. The default is chosen so that worst case is
  ≤ 2 s on the development machine, recorded per shape.
- **Corpus unchanged.** For every corpus `/retrieve` HTML record, the worker path equals the
  in-thread path (in-process frame round trip, plus real spawns on a fixed sample). The baseline
  regenerates with zero outcome changes.
- **`/search`.** Zero `extract_html` calls on the event-loop thread. The six
  `hardening-provider-bounds` US-005 captures are unchanged.

## User Stories

### US-001: Make stage 1 linear — replace the quadratic soup copies

**Priority:** P1

**Description:** As an operator, I want stage 1 to stop deep-copying BeautifulSoup trees, so that
deeply nested or unclosed-tag pages cost time linear in their size and the in-thread threshold
can be set meaningfully.

**Independent Test:** Before/after timings on the four pinned shapes show linear scaling.
`tests/corpus/baseline.json` regenerates with zero outcome changes, and both cassettes are
byte-unchanged.

**Implementation Hints:**
- **The copy sites** are `pipeline/stage1_extraction.py` :316-318 (`_extract_raw_text`) and
  :522-529 (the visibility pass, `pruned_soup = copy.copy(soup)`). The :316 comment chose copy
  "to avoid re-parse overhead". That is backwards on deep trees: parse is about 0.07 s where the
  copy is 4.6 s at 128 KiB.
- **Replacement:** re-parse the *original input string* with the same parser (`"lxml"`) where a
  mutable private tree is needed, or replace the mutation with a non-mutating walk (the pattern of
  `_extract_inline_text`).
  - **Check what was mutated before each copy.** If the tree had already been changed when it was
    copied, a re-parse is not equivalent. Then either re-apply the same mutations, or walk
    instead.
  - Prove equivalence: for every corpus HTML record (and the stage-1 unit fixtures), every
    `ExtractionResult` field is equal before and after. Run in-process; this is a pure function.
- **Pinned hostile shapes,** shared with US-005's calibration. Put them in one test-helper module,
  e.g. `tests/stage1_shapes.py`, built from string multiplication, no corpus text:
  - sibling-dense `<b>g</b>`
  - deep `<span>` nesting (open N, text, close N)
  - attribute/style-heavy elements
  - **repeated unclosed `<span>x`** (no closing tags; validation's worst shape)
- **Linearity test** (`tests/test_stage1_complexity.py`, new), patterned on
  `tests/test_stage2_complexity.py`:
  - GC disabled, best of 3.
  - Each shape at 64 KiB and 256 KiB through `extract_html(..., with_inline=True)` plus the
    visibility pass.
  - Assert `t_large <= _RATIO_BOUND * t_small` (or the fast-floor escape), with machine-calibrated
    ceilings.
  - Record before and after seconds per shape in Implementation Notes.
- **Rotation:** `stage1_extraction.py` is hashed. Follow the **Rotation record procedure** in
  Technical Considerations. This is **not** a sanitization-behaviour change, because output is
  byte-identical.
- **`/search`'s field parse** benefits too: one hostile 42,000-character snippet measured
  354 ms per field. Record the before and after for a 8,000-character unclosed-span snippet.

**Acceptance Criteria:**
- [ ] `copy.copy` no longer appears in `pipeline/stage1_extraction.py` (grep), and `import copy`
      is removed if unused.
- [ ] For every corpus HTML record and every stage-1 unit fixture, all `ExtractionResult` fields
      equal the pre-story output (in-process equivalence test, comparing against frozen
      expected values generated before the change).
- [ ] `tests/stage1_shapes.py` defines the four pinned shapes, and `tests/test_stage1_complexity.py`
      shows each scales within `_RATIO_BOUND` between 64 KiB and 256 KiB under calibrated ceilings.
      Before and after seconds per shape are recorded.
- [ ] The corpus baseline regenerates with zero outcome changes, and both cassettes are
      byte-unchanged.
- [ ] The rotation is recorded per the procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: A shared worker launcher — explicit environment, fd-passed pipe, non-dumpable parent

**Priority:** P1

**Description:** As an operator, I want every extraction worker launched with only an allowlisted
environment and unable to read the parent's environment, so that a parser compromise reaches no
credential. I also want the PDF worker moved onto the shared launcher so a second worker reuses
reviewed code.

**Independent Test:**
- The existing PDF worker tests pass on the new launcher.
- With every credential variable set in the parent, a real PDF worker child reports
  `os.environ` holding only allowlisted names.
- On Linux, `/proc/self/environ` likewise holds only allowlisted names, and reading
  `/proc/<ppid>/environ` fails.

**Implementation Hints:**
- **Why not `multiprocessing` spawn.** It inherits `os.environ` and has no per-process `env=`.
  - Clearing the environment in the child leaves the secrets in `/proc/self/environ` and in the
    child's memory.
  - Swapping `os.environ` around `start()` mutates process-global state while request threads
    run.
  - Validation measured both on the published image: under either option the child could still
    read `/proc/<ppid>/environ`.
- **Design (decided):**
  - **Launch.** `subprocess.Popen([sys.executable, "-m", "pipeline.worker_entry", "<kind>", ...],
    env=<allowlist>, pass_fds=(write_fd,), close_fds=True, stdin=DEVNULL, stdout=DEVNULL,
    stderr=DEVNULL)`.
    - **New module** `pipeline/worker_entry.py` (unhashed). Its `main()` dispatches on kind
      (`pdf`, `html`), applies `_apply_child_limits` **first**, configures logging to drop
      everything (`logging.disable(logging.CRITICAL)`), then imports the parser modules and does
      the work.
    - The child writes one length-prefixed frame to the passed fd.
    - Argv carries only the spool path, the fd number and numeric limits. Never page content.
    - No `preexec_fn`: it is unsafe with threads.
  - **Allowlist** (authoritative): `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`,
    `PYTHONPATH`, `VIRTUAL_ENV`, `PYTHONHASHSEED`, and `PYTHONDONTWRITEBYTECODE` if set.
    - The test's excluded-name list starts from `tests/conftest.py` `_CLEARED_ENV_VARS` (:45-56),
      plus a `*_API_KEY|*_TOKEN|*_SECRET|*_PASSWORD` pattern check.
    - If an import needs another variable (check `HF_HOME`: nothing in the parse path should touch
      it), add it with a comment, and record the final allowlist.
  - **Parent non-dumpable.** On Linux, at lifespan start, call `prctl(PR_SET_DUMPABLE, 0)` via
    `ctypes` (validation measured it: the child's read of `/proc/<ppid>/environ` then fails with
    `PermissionError`).
    - Side effects to check: `/proc/self/*` becomes root-owned. Grep the parent for `/proc/self`
      reads and confirm memory metrics still work (`resource.getrusage` is unaffected).
    - Core dumps are disabled; document that.
    - No-op on macOS, recorded as a residual.
- **Move the PDF worker** (`pipeline/pdf_subprocess.py`, unhashed) onto the launcher.
  - Keep its public functions (`extract_pdf_in_subprocess`, `extract_pdf_bytes_in_subprocess`),
    the result vocabulary, the IPC cap, and the parent-side re-validation.
  - The generic pieces become shared helpers in a new `pipeline/worker_launch.py` (unhashed):
    - launch-and-supervise (deadline poll, bounded read, kill/wait in `finally`, returning the
      decoded frame or `None`);
    - the 0600 spool context manager (taking a prefix);
    - the frame encoder (parameterised by `ensure_ascii` and the cap; oversize becomes
      `{"status":"failed"}`).
  - The PDF tests (`tests/test_stage1_pdf.py` `TestExtractPdfBytesInSubprocess` :464,
    `TestSpoolDir` :346, the orchestrator PDF mapping ~2620-2830) are the regression guard. They
    must pass with at most import and seam-name edits.
- **Spawn failure.** `OSError`/`EAGAIN` from `Popen` maps to the worker's failure error.
- **GOTCHAS:** "Cancelling a PDF await does not stop its worker thread" (:856-871). The parent
  must kill and wait on every path.
- **Docs.** `kit_tools/arch/SECURITY.md` gets a "worker isolation" section: the allowlist, the
  non-dumpable parent, the macOS residual, and the verdict-integrity residual from the Overview.
  `kit_tools/arch/DECISIONS.md` gets a dated entry.

**Acceptance Criteria:**
- [ ] `pipeline/worker_launch.py` and `pipeline/worker_entry.py` exist, and the PDF worker runs
      through them. Every existing PDF worker test passes with at most import and seam edits.
- [ ] With every name in `_CLEARED_ENV_VARS` and a pattern-matching sentinel set in the parent, a
      real worker child's `os.environ` keys are a subset of the recorded allowlist (test). The
      child imports its parsers successfully under that environment.
- [ ] On Linux (CI), the child's `/proc/self/environ` contains no excluded name, and its read of
      `/proc/<ppid>/environ` raises `PermissionError`. Both are tested, and skipped with a recorded
      reason on non-Linux.
- [ ] The parent sets `PR_SET_DUMPABLE` to 0 on Linux at startup. Existing metrics and health
      tests pass.
- [ ] A `Popen` `OSError` maps to the PDF worker's existing failure error (test).
- [ ] SECURITY.md (worker isolation, plus both residuals) and DECISIONS.md are updated.
- [ ] `sanitizer_revision` is unchanged (no hashed file edited), with the values recorded.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-003: Sweep stale spool files at startup, safely

**Priority:** P2

**Description:** As an operator, I want spool files orphaned by a killed process removed at the
next startup, without ever touching a live process's in-flight spool, so that fetched pages and
uploads do not persist at rest.

**Independent Test:** At startup, a planted spool file older than the age gate is removed. A
fresh one, a symlink, a foreign-prefix file and a subdirectory entry all survive.

**Implementation Hints:**
- **Prefixes.**
  - Rename `/extract`'s upload spool prefix `poppy-extract-` (`retrieval_app.py:1569`) to
    `forage-extract-` (invariant 1: no new "poppy" names).
  - Per-worker retrieve prefixes: `forage-retrieve-pdf-`, then `forage-retrieve-html-` from
    US-004.
  - The sweep matches `forage-extract-`, `forage-retrieve-` (covers both new ones) **and the
    legacy** `poppy-extract-`.
- **Age gate.** Remove only files whose `mtime` is older than `wall_clock_seconds + 60`. No live
  spool can be that old, because the worker's wall clock kills it first. That makes the sweep
  safe even when several Forage processes share the per-euid spool directory (uvicorn
  `--workers`, a restart overlapping a draining process, a shared `/tmp` volume).
- **Mechanics.**
  - Open the spool dir with `os.open(..., O_RDONLY | O_DIRECTORY)`.
  - Iterate with `os.scandir(fd)`. For each entry: `os.stat(name, dir_fd=fd,
    follow_symlinks=False)`, require `S_ISREG`, owner euid and age, then
    `os.unlink(name, dir_fd=fd)`. This closes the swap-for-symlink race.
  - Never recurse.
- **Placement.** Run where the lifespan already verifies `spool_dir()`
  (`retrieval_app.py:1713-1716`).
- **Logging.** One closed token with a count only (`spool_sweep removed=<n>`). Never a name or
  path (CLAUDE.md invariant 6 spirit).
- **Docs.** The at-rest lifecycle goes in `docs/configuration.md` and SECURITY.md.

**Acceptance Criteria:**
- [ ] `/extract`'s spool prefix is `forage-extract-`, and the existing upload tests pass.
- [ ] Startup removes an aged euid-owned regular file for each of the `forage-extract-`,
      `forage-retrieve-` and `poppy-extract-` prefixes. It keeps a fresh prefixed file, a
      symlink, a foreign-prefix file and an entry in a subdirectory (one test each, with `mtime`
      set via `os.utime`).
- [ ] Unlinks are `dir_fd`-relative after a no-follow `stat` (asserted by a test that swaps a
      file for a symlink between scan and unlink, or by code review noted in Implementation
      Notes).
- [ ] The log record is a closed token plus a count (test).
- [ ] The configuration and security docs describe the lifecycle.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-004: The HTML extraction worker — shared stage-1 function, strict frame, measured envelope

**Priority:** P1

**Description:** As an operator, I want an HTML worker that runs today's in-thread stage-1 work
(extraction plus the raw-markup and inline scans) in a launched child, returning a strictly
validated plain-data frame, so that the parse cost of a hostile page is bounded by the OS.

**Independent Test:**
- For every corpus `/retrieve` HTML record, the frame round trip (shared function, then encode,
  then parent decode and validate, all in-process) equals the untouched
  `orchestrator._extract_html_and_scan_inline`.
- A real worker on a fixed sample matches too.
- A hostile body with `child_cpu_seconds=1` raises `HTMLExtractionError`.
- Forged frames are rejected.

**Implementation Hints:**
- **Module.** `pipeline/html_subprocess.py` (new, **unhashed**) holds the shared function
  `extract_html_and_scan(html: str, url: str, budget_characters: int | None) -> tuple[ExtractionResult, StructuralScanResult | None]`.
  - Its body is a copy of `orchestrator._extract_html_and_scan_inline` (:236-260).
  - It is not in `stage1_extraction.py`: `stage2_structural.py:19` imports from there, so that
    would be a cycle.
  - **This story does not edit `orchestrator.py`.** US-005 re-points it.
- **Entry point.**
  `extract_html_bytes_in_subprocess(body: bytes, url: str, budget_characters: int | None, settings: ExtractionSettings) -> tuple[ExtractionResult, StructuralScanResult | None]`.
  - It uses the US-002 launcher (kind `html`) and the spool helper (prefix
    `forage-retrieve-html-`).
  - The child decodes `body.decode("utf-8", errors="replace")`, exactly as the parent does today
    (`orchestrator.py:636`).
- **Frame** (`ensure_ascii=False`):
  - `status`;
  - `title`, `author`, `date`, `raw_text`, `main_content`, `word_count`,
    `main_content_is_fallback`;
  - `scan`: either null, or `verdict`, `penalty`, `flags` as `[category, line_number]` pairs, and
    `fold_refused: bool`.
  - **Never sent:** `scan_text_inline`, and never `matched_text` (page text; unread downstream of
    stage 2, `stage4_structuring.py:141, 167-168`). The parent rebuilds each `FlaggedSpan` with
    `matched_text=""`; grep that nothing logs or serves it for `/retrieve` HTML.
  - The module docstring enumerates every field of `ExtractionResult`
    (`stage1_extraction.py:130-148`), `StructuralScanResult` and `FlaggedSpan`
    (`stage2_structural.py:28-43`), and how each crosses or is rebuilt.
- **Child logging.**
  - The child's logging is disabled (US-002). `structural_scan_forms` logs the WARNING
    `stage2_fold_expansion_refused` (:423) inside the child, where it would be lost.
  - The frame therefore carries `fold_refused`, and the parent re-emits the **same** closed token
    when it is true.
  - A parity test asserts that the worker path and the in-thread path emit the same token for a
    refusing input.
  - Spec 2 changes what a refusal *does*, not this token.
- **Strict parent validation** (fail closed → `HTMLExtractionError`):
  - exact key sets;
  - `verdict` in `Stage2Verdict`;
  - `penalty` a float in [-0.45, 0.0];
  - each category in `_BLOCKING_CATEGORIES ∪ _SUSPICIOUS_CATEGORIES ∪ set(_MARKUP_CATEGORIES.values())`;
  - `line_number` an int ≥ 0;
  - `word_count` an int ≥ 0;
  - string fields within size;
  - `fold_refused` a bool.

  One forged-frame test per rule, through the parent's decode and validate seam.
- **Frame cap.** `MAX_HTML_FRAME_BYTES` in `html_subprocess.py`. Derive it for the **budget-off**
  case: `retrieve.max_promptguard_chunks: 0`, where both `raw_text` and `main_content` of a
  `DEFAULT_MAX_CONTENT_BYTES` body cross.
  - Measure bytes per character on the worst case (does stage 1 strip C0 controls?).
  - Measure the parent's peak RSS while receiving, decoding and validating a worst-case frame,
    for both the budget-off case and the 64-chunk case. Record both.
  - If the budget-off peak exceeds 25% of 1536 MiB, set the cap to what fits and document that
    over-cap pages are refused. Refusal, never truncation.
- **Failure vocabulary.** The child reports `ok` | `failed`, deliberately indistinguishable. The
  parent raises one `HTMLExtractionError`. Spawn `OSError` maps to it as well. Exception text
  never crosses the pipe or reaches a log.
- **Test cost.** A spawn plus imports costs about 0.3 s wall and 0.29 s child CPU. There are
  359 page-surface records. So run **all** records through the in-process round trip, and real
  spawns on a fixed sample: 10 records, plus a synthetic non-UTF-8 body and an over-budget body.
- **Kill test.** A real worker given 256 KiB of the pinned unclosed-span shape (or, if US-001
  made it linear, a size measured to exceed 1 s of CPU on the CI runner) with
  `child_cpu_seconds=1`.
  - Assert `HTMLExtractionError`, no surviving child, and elapsed < `wall_clock_seconds`.
  - Import CPU (about 0.3 s) counts against `RLIMIT_CPU`; state this in `docs/configuration.md`.
  - **Never** push 10 MB element-dense HTML through `extract_html` in a test.
- **Envelope measurement** (record only). In a Linux container (`docker run --cpus 1 -m 1536m`),
  run realistic pages (div/h2/p/a/em/nested-ul) at 0.5, 1, 2 and 4 MB.
  - Record child **VmPeak** (`RLIMIT_AS` bounds virtual size, not RSS) and CPU seconds including
    import.
  - State the largest realistic page that parses under 384 MiB / 20 s in `docs/configuration.md`.
  - Also record these figures under a `docs/bootstrap-notes.md` heading.

**Acceptance Criteria:**
- [ ] `pipeline/html_subprocess.py` holds `extract_html_and_scan` and
      `extract_html_bytes_in_subprocess`, using the US-002 launcher and the US-003 prefix.
      `orchestrator.py` is not edited.
- [ ] For every corpus `/retrieve` HTML record, plus a non-UTF-8 body and an over-budget body, the
      in-process frame round trip equals `orchestrator._extract_html_and_scan_inline`. That covers
      `ExtractionResult`, verdict, penalty, categories and line numbers. A real-spawn test on the
      fixed 12-body sample matches too.
- [ ] The raw frame bytes contain neither `scan_text_inline` nor a marker string that occurs only
      in a matched span (test). The docstring enumerates every field.
- [ ] The worker path re-emits `stage2_fold_expansion_refused` exactly when the in-thread path
      does (parity test).
- [ ] The parent rejects a forged frame for each validation rule with `HTMLExtractionError` (one
      test each).
- [ ] `MAX_HTML_FRAME_BYTES` is derived and documented. The largest admissible result fits, and
      an over-cap frame is refused, not truncated (tests). Parent peak RSS is recorded for the
      budget-off and 64-chunk cases.
- [ ] The kill test passes, a spawn `OSError` maps to `HTMLExtractionError`, and no child
      survives (tests).
- [ ] The Linux envelope (VmPeak and CPU per size, and the largest page that fits) is recorded in
      Implementation Notes, `docs/configuration.md` and `docs/bootstrap-notes.md`.
- [ ] `sanitizer_revision` is unchanged, with the values recorded.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-005: Route large `/retrieve` HTML bodies through the worker

**Priority:** P1

**Description:** As an operator, I want `/retrieve` HTML bodies above a calibrated byte threshold
to parse in the worker inside the admission slot, refused with a coded 422 on overrun. Then a
hostile page costs at most the worker's limits, while ordinary pages keep today's latency.

**Independent Test:** Drive `run_retrieve_pipeline` with a stubbed fetch:
- a body at the threshold never spawns;
- a body one byte over calls the worker seam;
- a worker failure maps to `422 extraction_failed` / `html_extraction_error` and releases
  admission only after the worker is reaped.

**Implementation Hints:**
- **Re-point.** `orchestrator._extract_html_and_scan_inline` becomes a thin call to
  `html_subprocess.extract_html_and_scan`, or it is removed and its callers updated.
  - About 26 test sites patch `pipeline.orchestrator.extract_html` or call the function directly:
    `test_orchestrator.py`, `test_app.py:1527`, `test_retrieve_admission.py:282/394/564-572`,
    and `test_inline_scan_form.py`.
  - Keep the patch target working, or update the patches. Their bodies are tiny, so they stay
    in-thread.
- **Branch.** The HTML branch (~:635-647) mirrors the PDF branch (~:585-634):
  `completed_thread(asyncio.to_thread(extract_html_bytes_in_subprocess, ...))` **inside** the
  admission slot (acquired ~:528, released in `finally` ~:655-656).
  - Never put a timer around `acquire()` (GOTCHAS:873-884).
  - Compare the **fetched byte length**, before decode.
- **Contract constant.** `RETRIEVE_HTML_EXTRACTION_ERROR = "html_extraction_error"` goes in
  `pipeline/contract.py` beside `RETRIEVE_PDF_*` (:527-546).
  - Only the constant. No description, no `CONTRACT_VERSION` change (US-006 does those), so the
    1.3.0 golden stays green.
- **Failure mapping.**
  - `HTMLExtractionError` → 422 `extraction_failed` / `html_extraction_error`, with the closed
    WARNING token `retrieve_html_extraction_failed`.
  - Spool `OSError` / `SpoolDirectoryError` → the existing spool reason and the
    `retrieve_spool_error` token, exactly as the PDF branch does it.
  - Never exception text.
- **Internal counters.** Count worker spawns and refusals as plain attributes on the metrics
  object. US-006 exposes them on `/metrics`.
  - Touch points: `RetrieveMetricsSink` (`orchestrator.py:1495`), `_NullRetrieveMetrics`
    (`:1506`) and `retrieval_app.RetrieveMetrics` (`:1243`).
  - **Not** `RetrieveMetricsResponse` (`:784`): adding response fields here would turn
    `tests/test_contract_metrics.py::test_every_1_3_0_metric_addition_is_named_in_the_contract_entry`
    red and move the 1.3.0 OpenAPI file.
- **Threshold knob.** `retrieve.html_worker_threshold_bytes` in `pipeline/retrieve_limits.py`
  (unhashed), validated with `bounded_int`.
  - Register it in `retrieval_app.KNOWN_CONFIG_KEYS` (~:453-463) and classify it
    **security-relevant**. Update the partition test and the shipped-defaults test
    (`tests/test_contract_metrics.py` ~:800-850).
  - Add it to `config.yaml`, `bench/config.yaml`, `docs/configuration.md` and
    `kit_tools/docs/ENV_REFERENCE.md`.
  - It joins neither the cache fingerprint nor the revision inputs, because output is
    byte-identical; say so.
  - Its **maximum is 4× the calibrated default.** Document the measured worst-case in-thread
    seconds at the maximum beside the key, and that raising it weakens the bound. `0` routes
    everything to the worker.
- **Calibration.**
  - Use the four pinned shapes from `tests/stage1_shapes.py`, GC off, median of 3, on the US-001
    linear code.
  - The default is the largest power-of-two KiB at which the **worst** shape parses in-thread
    in ≤ 2 s on this machine. Record per shape.
  - Regression test: each shape at the default runs in-thread under a calibrated ceiling.
  - Note in Implementation Notes what share of ordinary pages a spawn will now cost (median HTML
    is tens of KB) at the chosen default.
- **Cancellation.** Real-task `cancel()` with the worker held: admission is released only after
  reap and unlink (GOTCHAS:856-871).
- **Corpus comparison.** Run the default-versus-`0` comparison through the in-process seam for all
  records, with real spawns on the sample.
- **Docs that become false** (`docs/configuration.md`):
  - "HTML-only deployment … spawns no worker" (:975-977);
  - "No wall clock is put on HTML extraction" (:1022-1023);
  - "The HTML path writes nothing to disk" (:1013);
  - the queue-latency formula (:992-996).

  Also the `RetrieveSettings` docstring (`retrieve_limits.py:67-79`).
- **Rotation.** `orchestrator.py` and `contract.py` move; follow the procedure.

**Acceptance Criteria:**
- [ ] The in-thread path calls `html_subprocess.extract_html_and_scan`, with no second copy of its
      body (grep).
- [ ] A body exactly at the threshold parses in-thread, one byte over goes to the worker, and `0`
      sends everything to the worker. The decision uses fetched byte length (tests).
- [ ] `retrieve.html_worker_threshold_bytes` is bounded to `0 … 4 × default`, registered in
      `KNOWN_CONFIG_KEYS` as security-relevant, and documented in all four sites with the
      worst-case-at-maximum figure. The partition and shipped-default tests pass.
- [ ] The default is the calibrated value, recorded per shape, and a regression test runs all four
      shapes at the default under a calibrated ceiling.
- [ ] `HTMLExtractionError` maps to 422 `extraction_failed` / `html_extraction_error`, and spool
      errors map to the existing reason. Each logs its closed token, with no exception text
      (tests).
- [ ] Internal spawn and refusal counters increment on the worker and refusal paths (tests).
      `RetrieveMetricsResponse` is unchanged.
- [ ] Admission is released only after reap and unlink under real-task cancellation (test).
- [ ] Default-versus-`0` responses are equal for all corpus HTML records (in-process) and the
      real-spawn sample. The corpus baseline regenerates with zero outcome changes, and the
      cassettes are byte-unchanged.
- [ ] `tests/golden/contract_1_3_0.json` and `CONTRACT_VERSION` are unchanged.
- [ ] The rotation is recorded per the procedure, and the false docs and docstring are corrected.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-006: Contract surface — announce the reason and counters, cut held 1.4.0

**Priority:** P1

**Description:** As a consumer, I want the `/retrieve` 422 description to name
`html_extraction_error` and `/metrics` to expose the worker counters, under a held contract 1.4.0,
so that the published surface matches the new behaviour.

**Independent Test:**
- `CONTRACT_VERSION == "1.4.0"`;
- `tests/golden/contract_1_4_0.json` matches;
- `/metrics` carries `retrieve.html_worker_spawns` and `retrieve.html_worker_refusals`;
- `contract/openapi.yaml` matches its regenerated anchor;
- the 1.3.0 golden is byte-unchanged.

**Implementation Hints:**
- **Why the bump is here.** The `/retrieve` 422 error description is pinned in
  `tests/golden/contract_1_3_0.json` (~:292), sourced from `retrieval_app.py:1039-1042`. Adding
  `html_extraction_error` moves a pinned schema. `test_contract_schema.py:22` selects the golden
  by version. The new golden is **held**: spec 3 regenerates it until the tag.
- **`pipeline/contract.py`.**
  - `CONTRACT_VERSION = "1.4.0"` (:22).
  - A `* ``1.4.0`` —` bullet written as an in-progress record that spec 3 US-003 finalises. It
    covers the reason, the two counters, and the large-page refusal (a served-outcome change).
  - Column 0, two-space continuation, tense guard (`tests/test_ci_workflow.py:2410-2430`).
  - Update the `RetrieveErrorCode` docstring (:382-401).
- **Counters on `/metrics`.** Add the two fields to `RetrieveMetricsResponse`
  (`retrieval_app.py:784`) and wire the emission from US-005's internal counters.
  - `tests/test_contract_metrics.py` `test_every_1_3_0_metric_addition_is_named_in_the_contract_entry`
    (~:171-185) and the payload key-set pins (~:307-336) need a **1.4.0 analogue**: fields added
    since 1.3.0 must be named in the 1.4.0 entry. Extend the baseline map
    (`_ONE_TWO_ZERO_SECTION_FIELDS`-style) with a 1.3.0 set.
- **Golden.** Create `tests/golden/contract_1_4_0.json` (six models, pattern
  `tests/test_contract_schema.py:22-30`). Goldens 1.0.0–1.3.0 stay byte-unchanged.
- **Export.** Run `uv run python -m scripts.export_contract` (it writes `openapi.yaml`, `.sha256`
  and the fixture twin).
  - Update the doc anchor quotes that `tests/test_contract_export.py` checks.
  - Update `tests/test_bench_promptguard.py:83, :307`, `contract/GOVERNANCE.md:29`
    (`test_governance_docs.py:281-296`), and `CLAUDE.md` invariant 4's "currently **1.3.0**".
- **Rotation.** `contract.py` moves; follow the procedure.

**Acceptance Criteria:**
- [ ] The `/retrieve` 422 description and the `RetrieveErrorCode` docstring name
      `html_extraction_error`.
- [ ] `/metrics` emits both counters, wired from US-005's counters (test). A 1.4.0
      metric-addition test requires them in the 1.4.0 entry.
- [ ] `CONTRACT_VERSION == "1.4.0"`, and the 1.4.0 bullet covers the reason, the counters and the
      large-page refusal. The tense guard passes.
- [ ] `tests/golden/contract_1_4_0.json` exists and matches. Goldens 1.0.0–1.3.0 are
      byte-unchanged.
- [ ] The OpenAPI file, anchor and fixture twin are regenerated. Export, anchor-quote,
      bench-pin and GOVERNANCE tests are green.
- [ ] The rotation is recorded per the procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-007: `/search` parses titles and snippets off the event-loop thread

**Priority:** P2

**Description:** As an operator, I want `/search`'s per-result scan-form building to run in a
worker thread, so that the event-loop thread is not held for a whole parse.

**Independent Test:**
- Record the thread identity of every `extract_html` call during `run_search_pipeline`. None is
  the loop's thread.
- A ticker task makes progress while a stub parse sleeps.
- The response bytes equal the pre-story capture.

**Implementation Hints:**
- **Call sites:** `_scan_forms_for_search_text` (~:1028-1075), its callers in the result loop
  (~:1853, ~:1888), and the raw-markup scans (~:1960-1964).
- **One `asyncio.to_thread` per result**, inside `completed_thread` (`stage3_promptguard.py:68`).
- **What this proves.** The GIL still serialises CPU work. The fix gives the loop thread control
  at the switch interval instead of holding it for a whole parse.
  - The standing tests assert structural properties only: thread identity, and a ticker that
    progresses while a stub parse does a GIL-releasing `time.sleep`.
  - Record a one-off pre/post maximum ticker lag with a real hostile 10-result provider (2,048-char
    titles, 8,000-char snippets) in Implementation Notes only.
- **Byte-identical.** The six captures from `8e449fc` and the `/search` corpus outcomes stay
  unchanged.
- **Resource-envelope counters** (`hardening-resource-envelope` US-004):
  - With a stub parse delay and no contention, the classification-wait counter and the high-water
    mark equal the pre-story capture (test).
  - `docs/configuration.md` states which of them includes thread-hop time.
- **Rotation.** `orchestrator.py` moves; follow the procedure.

**Acceptance Criteria:**
- [ ] No `extract_html` or `scan_raw_markup` call in `run_search_pipeline` runs on the loop
      thread (thread-identity test, ≥ 3 results). A ticker progresses during a sleeping stub
      parse (test).
- [ ] The one-off pre/post lag measurement is recorded in Implementation Notes.
- [ ] The six `8e449fc` captures and the `/search` corpus outcomes are unchanged.
- [ ] A cancelled `/search` does not return before its parse thread finishes (test).
- [ ] The classification-wait counter and the high-water mark equal the pre-story values under a
      stub delay (test). `docs/configuration.md` states which includes thread-hop time.
- [ ] The rotation is recorded per the procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- **Threshold boundaries.** A body exactly at the threshold parses in-thread. `0` sends
  everything to the worker. The maximum is 4× the default, enforced at boot. (US-005)
- **Worker failures** all become `HTMLExtractionError`, which maps to 422: address space (Linux),
  CPU, wall clock, spawn `EAGAIN`, an oversized frame, bad JSON, and a forged frame. (US-004,
  US-005)
- **A compromised parser returning a valid CLEAN frame** is not detected. This is an accepted
  residual, recorded in SECURITY.md. (US-002)
- **Several Forage processes sharing a spool dir:** the age gate keeps live spools. (US-003)
- **A symlink swapped in during the sweep:** the no-follow stat plus `dir_fd` unlink handles it.
  (US-003)
- **Cancellation mid-parse:** the child is killed and reaped, the spool unlinked, and only then
  is admission released. (US-005)
- **Non-UTF-8 and over-budget bodies** are covered by the equivalence tests. (US-004)
- **A fold refusal inside the child** is re-emitted by the parent. (US-004)
- **macOS:** no `RLIMIT_AS` and no `PR_SET_DUMPABLE`. Both are residuals, documented. (US-002,
  US-004)
- **A `/search` provider returning zero results** makes no thread hop. (US-007)

## Out of Scope

- A compiled HTML parser (backlog).
- A warm worker pool.
- Raising worker memory for HTML (the owner accepted the envelope).
- `/extract` HTML. It parses none; only its spool prefix is renamed.
- Re-running stage-2 scans in the parent to defend verdict integrity against a compromised child.
  This is an accepted residual.

## Assumptions

- Reusing `child_cpu_seconds` (20), `child_address_space_bytes` (384 MiB) and
  `wall_clock_seconds` (90) for HTML is right; the bound is the point.
- A `Popen` launch costs about the same as `multiprocessing` spawn (hundreds of ms). US-002
  records it.
- After US-001, stage 1's worst shape is linear, so the calibrated threshold will be larger than
  the ~64 KiB the quadratic code allowed.

## Technical Considerations

- **Hashed vs unhashed.** Hashed: `stage1_extraction.py`, `orchestrator.py`, `contract.py`.
  Unhashed: `html_subprocess.py`, `worker_launch.py`, `worker_entry.py`, `pdf_subprocess.py`,
  `extraction_limits.py`, `retrieve_limits.py`, `retrieval_app.py`.
  - US-001, US-005, US-006 and US-007 each rotate once.
  - US-002, US-003 and US-004 must not rotate.
- **Rotation record procedure** (shared by every rotating story in this epic):
  1. Compute `derive_sanitizer_revision()` under default and shipped config, before and after.
  2. For each hashed file the story changed, reconstruct the pre-story bytes **read-only**: load
     the module from `git show <pre-story>:<path>` into a temp directory, never touching the
     working tree.
     - Recompute with only that file reverted, and once with all reverted.
     - The all-reverted control must reproduce the pre-story value exactly.
  3. Record the rotation in three places:
     - a new paragraph in `CLAUDE.md` (Coexistence section);
     - a new heading in `docs/bootstrap-notes.md` (next ordinal);
     - a row plus the tally in the GOTCHAS rotation table (`kit_tools/docs/GOTCHAS.md` ~:719/~:782).
  4. Update every count site to the new ordinal:
     - GOTCHAS: the table intro and the tally;
     - `kit_tools/docs/DEPLOYMENT.md:125`;
     - `kit_tools/docs/TROUBLESHOOTING.md:804` (two sites);
     - `kit_tools/arch/SERVICE_MAP.md:210` (also names the current value);
     - `kit_tools/arch/CODE_ARCH.md:499`;
     - `kit_tools/arch/DECISIONS.md:1154/1163`;
     - `CLAUDE.md`;
     - `docs/releases.md` Unreleased.
  5. Verify: `grep -rn "<previous ordinal word>"` over those files returns no stale "current
     count" hit.
- **Stage-3 input** (`raw_text`) is byte-identical on every path, or the cassettes stop
  replaying.

## Related Documentation

- [CODE_ARCH.md](../arch/CODE_ARCH.md), [SECURITY.md](../arch/SECURITY.md),
  [GOTCHAS.md](../docs/GOTCHAS.md)
- `docs/configuration.md` (`extraction:` ~:931, `retrieve:` ~:960-1034)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Fix the quadratic `copy.copy(soup)` before calibrating.
**Rationale:** Profiling: parse 0.07 s vs copy 4.62 s (128 KiB of unclosed spans); 18.5 s for the
copy at 256 KiB. The worst shape becomes linear, which raises the safe threshold and speeds up
`/search`.
**Source:** Round-2 second-opinion profile, 2026-10-07; `stage1_extraction.py:316-318, 522-529`.

**Decision:** Launch with `Popen(env=allowlist, pass_fds=…)` and a non-dumpable parent.
**Rationale:** Measured on the published image: clearing the environment in the child leaves
`/proc/self/environ` populated. Swapping `os.environ` mutates global state. Under either, the
child can read `/proc/<ppid>/environ`. `PR_SET_DUMPABLE 0` blocks that (measured).
**Source:** Round-2 second opinion and security reviews, 2026-10-07.

**Decision:** The stale-spool sweep is age-gated and `dir_fd`-relative, and includes the legacy
`poppy-extract-` prefix.
**Rationale:** The spool dir is per-euid, not per-process. An ungated sweep deletes live spools.
`/extract` uploads were missed by the original prefix list.

**Decision:** Counters are internal in US-005 and exposed in US-006.
**Rationale:** `test_every_1_3_0_metric_addition_is_named_in_the_contract_entry` pins response
fields to the 1.3.0 entry.

**Decision:** Equivalence runs in-process for every record; real spawns run on a 12-body sample.
**Rationale:** 359 records × ~0.3 s spawn would add minutes to the blocking gate.

### Scope Adjustments

- Round 1 split the spec into five stories. Round 2 brought it to seven:
  - added the copy fix (US-001);
  - split plumbing into the launcher (US-002) and the sweep (US-003);
  - replaced multiprocessing spawn with `Popen`;
  - moved the counters to the contract story;
  - pinned the four shapes and capped the knob at 4× the default.

### Decisions Made

- The contract bump to 1.4.0 happens in US-006. Spec 3 finalises the entry.

## Clarifications

### Session 2026-10-07
- **Q:** Rewrite the workers in C? **A:** No. Only an OS limit bounds hostile input. A compiled
  parser goes to the backlog.
- **Q:** How should `/retrieve` route HTML? **A:** A size threshold.
- **Q:** Move `/search` parsing off the event loop? **A:** Yes.
- **Q:** Accept refusal of realistic pages above about 2 MB? **A:** Yes, and document it.
- **Q:** Scrub the environment for the PDF worker too? **A:** Both workers.
