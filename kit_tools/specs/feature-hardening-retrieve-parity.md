<!-- Template Version: 2.5.0 -->
---
feature: hardening-retrieve-parity
status: active
session_ready: true
depends_on: [hardening-search-sanitization]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 2
epic_final: false
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: `/retrieve` Parity With `/extract`

> **Spec 2 of `epic-forage-hardening`.** Give the web-fetch path the guarantees the upload path
> already has — a pre-checked PromptGuard chunk budget and the classification semaphore (on
> `/search` too), stage-1/stage-2 work off the event loop under a bounded slot, fetched PDFs parsed
> in the spawned rlimited worker with a typed failure vocabulary — make a corrupt cache entry a miss
> instead of a 500, and add the operator's policy floors (owner decision 3, R10): a fail-closed floor
> and a threshold ceiling, with the effective policy reported on the response.
> Context: holistic review WA-D (`/retrieve` vs `/extract` hardening asymmetry); epic rulings 3, 5,
> 6 and validation-round rulings R10, R16, R22, R23, R24, R32, R33, R34 are binding here.

## Overview

The 2026-08-28 review found that "the upload path got hardened; the web-fetch path that runs on
*more* hostile input did not." Three of its five WA-D items are still live at `main` today, and the
validation panel found two more the review missed.

`run_retrieve_pipeline` (`pipeline/orchestrator.py:208-215`; keyword-only `cache`, `classifier`,
`config`, `sanitizer_revision`) calls `sanitize_and_structure` at `:368-377` with **no**
`max_promptguard_chunks` and **no** semaphore; the file route of `/extract` does both (`:539`
`async with classification_semaphore:`, `:549` `max_promptguard_chunks=settings.
max_promptguard_chunks`). `/search` acquires no semaphore either (`:1014`). Stage 1 and stage 2 run
**synchronously on the event loop** for fetched content: `extract_pdf` (`:352`), `extract_html`
(`:355`) and `scan_structural` (`:174`, shared by both routes) — only stage 3 is threaded — so a
pathological page or PDF stalls the single-process service, health checks included. `/extract`
parses PDFs in a spawned worker with CPU, address-space and wall-clock limits
(`pipeline/pdf_subprocess.py:156`, `:32-48`); `/retrieve` uses in-process `pypdf` **with no
exception handling at all** — a fetched encrypted or text-free PDF is an HTTP 500 on `main` today.

Separately, `ContentCache.get` calls `RetrievedContent.model_validate_json(raw)` with no guard
(`cache.py:784`): a schema-drifted or poisoned value 500s that URL until its TTL expires — the
review's "corrupted cache entry → HTTP 500, not a miss" — and it compounds the integrity work
spec 4 does.

The load-bearing decisions, stated once because the validation panel showed each one is a trap
when left implicit:

- **The chunk budget is a refusal, not a clamp (R22).** `PromptGuardClassifier.classify` raises
  `PromptGuardBudgetExceededError` above `max_chunks` (`promptguard/classifier.py:179-182`;
  `kit_tools/arch/SECURITY.md:143` says so). `/extract` survives it with a character pre-check
  (`orchestrator.py:459-462`) and a typed catch (`:475-476`). `/retrieve` gets **its own, larger
  budget** (`retrieve.max_promptguard_chunks`, default 256 — 64 chunks is ~112 KB of text against a
  10 MB fetch cap and ordinary long articles exceed it), the same pre-check, and refuses over-budget
  pages with the **existing** `content_too_large` code and a new closed reason `promptguard_budget`.
  No new error code for this.
- **Parity by reuse, bounded by its own slots.** `/retrieve` shares the classification semaphore
  (with a bounded wait) but never the `/extract` admission middleware — that middleware is
  path-gated, enforces the `/extract`-only `route_enabled` 404, reserves 50 MiB per waiter and
  answers with a 429 `busy` that `/retrieve` does not declare. `/retrieve` gets a
  `retrieve.extraction_concurrency` slot of its own for stage-1 work (R23).
- **Fetched PDFs are spooled to a file and parsed by the worker (R23).** The worker takes a `Path`;
  a new bytes entry point in the un-hashed `pipeline/pdf_subprocess.py` owns the 0600 temp file
  and its unlink. The worker's typed errors need a `/retrieve` code that does not exist:
  `RetrieveErrorCode` is a closed six-member Literal (`pipeline/contract.py:243-259`), so
  **exactly one member, `extraction_failed`, joins it** inside the 1.3.0 window, with closed reasons
  per error class.
- **Policy floors are resolved once, in the handler, by replacing the request (R10, R24).** The
  request's `promptguard_fail_closed` is read at three pipeline sites (`:258` fingerprint, `:373`
  sanitize, `:1019` search) and neither pipeline takes it as a parameter, so the handler hands the
  pipeline `body.model_copy(update=...)` carrying the effective values; the cache fingerprint picks
  them up for free, and the handler stamps `effective_promptguard_fail_closed` /
  `effective_promptguard_threshold` on every response — hit or miss — after the pipeline returns.

Wire changes in the 1.3.0 window from this spec: `extraction_failed` on `/retrieve` (US-003), two
`/metrics` counters (US-001), `CacheMetrics.corrupt_entries` (US-004), and the two `effective_*`
fields (US-005). Every one appends a continuation line to the `1.3.0` docstring bullet (R34) and
re-creates `tests/golden/contract_1_3_0.json` (created by spec 1 US-004; this spec runs after it per
`depends_on`).

## Goals

- A fetched page whose extracted text exceeds `max_extracted_characters(retrieve.
  max_promptguard_chunks)` is refused with 422 `content_too_large` / reason `promptguard_budget`,
  never a 500; a page under the budget classifies every window; `/extract`'s budget, behaviour and
  tests are byte-for-byte unchanged.
- Two concurrent `/retrieve` classifications, or a `/retrieve` and a `/search` classification, never
  run at the same time (semaphore size 1 by default); a waiter that exceeds
  `retrieve.classification_wait_seconds` proceeds under the classifier-unavailable policy and is
  counted; a cache hit never waits.
- During a slow fetched-page extraction (fake extractor blocking for 2 s), five sequential `/health`
  requests each complete in under 1 s (the shape of `tests/test_app.py::
  test_health_answers_while_the_fetch_is_in_flight`); a fetched PDF is parsed in the spawned worker
  under the same `child_cpu_seconds` / `child_address_space_bytes` / `wall_clock_seconds` limits as
  an uploaded one, from a 0600 temp file that is gone on every exit path.
- Every fetched-PDF failure (encrypted, no text, page limit, classifiable-text limit, extraction
  error, rlimit or wall-clock kill) is a 422 with a closed reason; zero `/retrieve` 500s on the
  PDF branch.
- A cache value that fails to parse is served as a miss, deleted, and counted; zero `/retrieve`
  responses are 500 because of cache content.
- With `promptguard_fail_closed_floor: true`, a request carrying `promptguard_fail_closed: false`
  behaves as fail-closed on both routes; with `promptguard_threshold_ceiling: 0.5`, a request
  carrying `1.0` is classified at `0.5`; both responses report the effective values on every
  response, cache hit or miss; with the defaults (`false`, `1.0`), every existing test passes
  unchanged.

## User Stories

### US-001: Pre-checked chunk budget and the classification semaphore on `/retrieve` and `/search`

**Priority:** P1

**Description:** As an operator, I want fetched pages classified under a budget that refuses cleanly
instead of throwing, and both fetch routes classifying under the same concurrency gate as uploads
with a bounded wait, so one hostile page cannot burn unbounded CPU, run inference in parallel with
another request, or queue forever.

**Independent Test:** Drive `run_retrieve_pipeline` with a patched `fetch_url` returning a page whose
extracted text is one character over `max_extracted_characters(256)` and a real (unloaded-tokenizer
is fine) `PromptGuardClassifier`; assert a `PipelineError` with `error == "content_too_large"` and
`reason == "promptguard_budget"`, and that `classify` was never called; with a page under the
budget and a classifier double whose `classify` raises `PromptGuardBudgetExceededError`, assert the
same 422 (belt and braces). Then start two `/retrieve` calls against a fake classifier whose
`classify` blocks on a `threading.Event`, assert the second call's `classify` has not started while
the first holds the semaphore and completes once the event is set; repeat with one `/retrieve` and
one `/search`. With `retrieve.classification_wait_seconds: 0.1` and the semaphore held, assert the
waiter returns `promptguard_state == "unavailable_blocked"` (fail-closed request) and
`retrieve.classification_wait_timeouts == 1` on `/metrics`.

**Implementation Hints:**
- New un-hashed module `pipeline/retrieve_limits.py` mirroring `pipeline/extraction_limits.py`
  (`:54-57` dataclass, `:98-151` `extraction_settings_from_config` with `_bounded_int`): a
  `RetrieveSettings` dataclass read from a new `config.yaml` `retrieve:` block —
  `max_promptguard_chunks` (default 256, range 1–1024), `classification_wait_seconds` (default 30,
  range 1–300), `extraction_concurrency` (default 2, range 1–8; consumed by US-002). Published on
  `app.state.retrieve_settings` in the lifespan beside `extraction_settings`
  (`retrieval_app.py:1213-1216`) with the module-level fallback the file route has (`:1378-1379`).
- `run_retrieve_pipeline` gains **required keyword-only** `settings: RetrieveSettings` and
  `classification_semaphore: asyncio.Semaphore`, exactly as `run_extract_pipeline_from_file`
  (`:485-500`) has them — defaulted parameters would be the second, unbounded limits path this spec
  exists to prevent. All 16 `await run_retrieve_pipeline(` call sites in `tests/test_orchestrator.py`
  are updated mechanically; the `client` fixture there (`:1470-1506`) already publishes
  `app.state.classification_semaphore` and `extraction_settings`, so add `retrieve_settings` beside
  them.
- Pre-check exactly as the file route does at `:459-462`: after extraction, `if
  len(extraction.raw_text) > max_extracted_characters(settings.max_promptguard_chunks): raise
  PipelineError(error="content_too_large", reason=PROMPTGUARD_BUDGET, request_id=...)`;
  `max_extracted_characters` is in `pipeline/extraction_limits.py` (`:34-36`). Add the closed reason
  literal `PROMPTGUARD_BUDGET = "promptguard_budget"` to `pipeline/contract.py` beside the other
  reason constants (hashed; docstring continuation line: "`/retrieve` 422 `content_too_large` gains
  the reason `promptguard_budget`"). Pass `max_promptguard_chunks=settings.max_promptguard_chunks`
  at `:368-377` and wrap the `sanitize_and_structure` await in the semaphore; additionally catch
  `PromptGuardBudgetExceededError` (`promptguard/classifier.py:210`) around it and map to the same
  422 — the pre-check is the primary control, the catch is the backstop.
- Bounded wait: `async with asyncio.timeout(settings.classification_wait_seconds): await
  classification_semaphore.acquire()`; on `TimeoutError` the request proceeds exactly as if
  `classifier is None` (the existing `promptguard_state` `unavailable_blocked` /
  `unavailable_allowed` path, `tests/test_orchestrator.py:618,653`) and
  `RetrieveMetrics.classification_wait_timeouts` (`retrieval_app.py:903`, mirrored by
  `RetrieveMetricsResponse` `:538`, `extra="forbid"`) increments. Keep the cache read (`:82-88` in
  the function body) outside the semaphore — a hit must not wait on a classification.
- `/search` parity (R16): `run_search_pipeline` gains the same required keyword-only
  `classification_semaphore` and `classification_wait_seconds` (from `retrieve_settings` — one
  knob for both fetch routes), acquires the semaphore around each `run_promptguard` call at `:1014`
  (per result, so two requests interleave), and on timeout treats that result as
  `promptguard_unavailable` (existing omission reason) and increments
  `SearchMetrics.classification_wait_timeouts` (`:875`, `SearchMetricsResponse` `:488`). The
  `/search` handler (`:1811-1817`) threads both.
- Both counters are wire additions: one docstring continuation line naming
  `retrieve.classification_wait_timeouts` and `search.classification_wait_timeouts`, regenerate,
  re-create the golden. The `/metrics` order guards are
  `tests/test_contract_metrics.py::test_served_metrics_are_the_handlers_dict_serialized` (`:135`) and
  `test_metrics_mirror_round_trips_the_served_body` (`:161`) — key order matters.
- The `/retrieve` handler (`retrieval_app.py:1594-1600`) passes `settings=request.app.state.
  retrieve_settings` and `classification_semaphore=request.app.state.classification_semaphore`,
  read the way the `/extract` handler does (`:1731`).
- Tests live where the route tests live: `tests/test_orchestrator.py::
  test_retrieve_full_pipeline_happy_path` (`:244`), `::test_post_retrieve_endpoint` (`:1523`),
  `::test_post_retrieve_error_response` (`:1734`); the classifier double idiom is
  `MagicMock(spec=PromptGuardClassifier)` (`:1489`) — but the over-budget case must use a real
  `PromptGuardClassifier` or a double that *raises*, never a recording mock.
- Docs: `docs/configuration.md` gains the `retrieve:` block table (`| Key | Default | Allowed range
  | Purpose |`); `:487-489` keep describing `extraction.max_promptguard_chunks` and
  `classification_concurrency` as `/extract`'s and gain the sentence that `/retrieve` and `/search`
  now share the classification semaphore with a bounded wait; `kit_tools/arch/SECURITY.md:143`
  gains the sentence that `/retrieve` pre-checks under `retrieve.max_promptguard_chunks` and refuses
  with `content_too_large` / `promptguard_budget`; `kit_tools/docs/API_GUIDE.md:197-199` and
  `kit_tools/docs/TROUBLESHOOTING.md:168`'s `/retrieve` 422 table name the new reason;
  `MONITORING.md` gains both counter rows.
- Rotates `sanitizer_revision` (`orchestrator.py` and `contract.py`), ruling 6 / R32: revert each in
  turn with a both-reverted control; record in `docs/bootstrap-notes.md`, `CLAUDE.md`,
  `kit_tools/arch/DECISIONS.md`.

**Acceptance Criteria:**
- [ ] `pipeline/retrieve_limits.py` defines `RetrieveSettings` and `retrieve_settings_from_config`
      with the three keys, defaults and ranges above; an out-of-range value refuses boot with a
      closed-vocabulary message (test modelled on `tests/test_app.py::
      test_lifespan_refuses_an_out_of_range_cache_bound`, `:1208`); the lifespan publishes
      `app.state.retrieve_settings` and the module-level fallback exists.
- [ ] `run_retrieve_pipeline` and `run_search_pipeline` take the new required keyword-only
      parameters; both handlers pass them; every existing call site in `tests/test_orchestrator.py`
      is updated; a test asserts the kwargs reach the pipelines.
- [ ] A fetched page one character over `max_extracted_characters(retrieve.max_promptguard_chunks)`
      is refused with 422 `content_too_large` / `promptguard_budget` before `classify` is called; a
      classifier that raises `PromptGuardBudgetExceededError` yields the same 422; a page under the
      budget classifies every window; the `/extract` file route's call and tests are unchanged.
- [ ] Two concurrent classifications (`/retrieve`+`/retrieve`, `/retrieve`+`/search`) serialise
      through the shared semaphore; a cache hit is served without acquiring it.
- [ ] A waiter exceeding `retrieve.classification_wait_seconds` proceeds under the
      classifier-unavailable policy of its route (`promptguard_state` `unavailable_*` on
      `/retrieve`; `promptguard_unavailable` omission on `/search`) and the route's
      `classification_wait_timeouts` counter increments; both counters appear on `/metrics`.
- [ ] `PROMPTGUARD_BUDGET` is a constant in `pipeline/contract.py`; the docstring continuation
      lines for the reason and the two counters are appended in the `* ``1.3.0`` — …` bullet
      format; `uv run python -m scripts.export_contract` run; `tests/golden/contract_1_3_0.json`
      re-created; `uv run python -m scripts.export_contract --check` green.
- [ ] `docs/configuration.md`, `SECURITY.md:143`, `API_GUIDE.md`, `TROUBLESHOOTING.md` and
      `MONITORING.md` carry the rows and sentences named in the hints.
- [ ] `sanitizer_revision` rotation measured (revert `orchestrator.py` and `contract.py` each in
      turn, both-reverted control) and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and
      `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Stage 1 and stage 2 off the event loop, under a bounded `/retrieve` slot

**Priority:** P1

**Description:** As an operator, I want HTML extraction and the structural scan to run off the event
loop, with `/retrieve`'s stage-1 work bounded by its own concurrency slot, so a pathological page
cannot stall `/health` or any other request and N slow pages cannot starve the shared thread pool.

**Independent Test:** Using `tests/test_app.py::test_health_answers_while_the_fetch_is_in_flight`
(`:958`) as the model — `_running_app(cache_connected=True)`, a `threading.Event`-gated blocking
`extract_html`, five sequential `/health` GETs — assert `max(latencies) < 1.0` while the extraction
is provably still in flight; with `retrieve.extraction_concurrency: 1` and one extraction blocked,
assert a second `/retrieve` waits (its `extract_html` not called) until the first releases, and
`retrieve.extraction_queue_waits == 1` on `/metrics`; `/extract` responses on the existing fixture
corpus are byte-identical.

**Implementation Hints:**
- Sync call sites: `extract_html` at `pipeline/orchestrator.py:355` and `scan_structural` at
  `:174` inside `sanitize_and_structure` (shared with `/extract`, so threading it there benefits both
  routes — keep `/extract` output identical). Use `asyncio.to_thread` as stage 3 already does at
  `pipeline/stage3_promptguard.py:130`. `extract_pdf` at `:352` is US-003's (it moves to the worker).
- `/search`'s own synchronous sites (`_sanitize_search_text` → `extract_html` at `:604`, the
  per-result `scan_structural` at `:997`, bounded fields of ≤ 2 000 characters) stay on the loop —
  state that in Out of Scope; they are not the 10 MB problem.
- The bounded slot: `app.state.retrieve_extraction_semaphore = asyncio.Semaphore(
  retrieve_settings.extraction_concurrency)` in the lifespan (plus the module-level fallback),
  passed to `run_retrieve_pipeline` as a required keyword-only parameter beside US-001's, acquired
  around stage 1 (HTML extraction, and US-003's PDF worker) — never around the fetch or the cache
  read. The wait is FIFO and unbounded by design (a `/retrieve` has no refusal code to give);
  `RetrieveMetrics.extraction_queue_waits` (wire addition, docstring line, golden) counts every
  acquisition that had to wait, so saturation is visible on `/metrics` before it is fatal. The
  memory ceiling is `extraction_concurrency × 10 MB` fetched bodies in flight plus waiters holding
  theirs; document it in `docs/configuration.md`'s `retrieve:` table.
- `asyncio.to_thread` is uncancellable and the default executor is shared with stage 3
  (`min(32, cpu + 4)` threads): the slot above is what keeps hostile pages from starving the
  classifier. A wall clock on HTML extraction is deliberately **not** added here (the 30 s fetch
  timeout and 10 MB cap bound the input); spec 6's envelope work sizes the slot.
- Tests: the responsiveness test mirrors `tests/test_app.py:958-993` and its `_running_app` (`:797`)
  / `_blocking_acquisition` (`:898`) helpers — note the connected-cache requirement its docstring
  records (the 2 s reconnect floor hides the number otherwise); a test asserts the patched
  `extract_html` and `scan_structural` run on a non-event-loop thread (`threading.get_ident()`
  differs from the loop's); the slot test uses two blocked extractions.
- Docs: `kit_tools/arch/CODE_ARCH.md`'s pipeline narrative says stage 1/2 run off the loop on both
  routes; `kit_tools/arch/SECURITY.md:50` (the two middlewares gate on `/extract`) stays true and is
  left alone; `docs/configuration.md` `retrieve:` table gains `extraction_concurrency` with the
  memory-ceiling sentence; `MONITORING.md` gains the `extraction_queue_waits` row.
- Rotates `sanitizer_revision` (`orchestrator.py`, `contract.py` docstring line), ruling 6 / R32.

**Acceptance Criteria:**
- [ ] `extract_html` and `scan_structural` are invoked via `asyncio.to_thread` on both routes; no
      synchronous call to them remains in `run_retrieve_pipeline` or `sanitize_and_structure`; a
      test asserts the patched functions run on a non-event-loop thread.
- [ ] Five sequential `/health` requests each complete in under 1 s while a fetched-page
      `extract_html` is blocked (the `:958` shape, connected cache).
- [ ] `/retrieve` stage-1 work acquires `app.state.retrieve_extraction_semaphore`; with size 1 and
      one extraction blocked, a second `/retrieve`'s extraction does not start until the first
      releases; `retrieve.extraction_queue_waits` increments and appears on `/metrics`; the slot is
      released on every path including an extraction exception.
- [ ] `/extract` responses on the existing fixture corpus are byte-identical before and after
      (existing tests unchanged).
- [ ] The docstring continuation line for `extraction_queue_waits` is appended;
      `uv run python -m scripts.export_contract` run; `tests/golden/contract_1_3_0.json`
      re-created; `--check` green.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce) and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Fetched PDFs parsed in the rlimited worker, with a typed `/retrieve` failure vocabulary

**Priority:** P1

**Description:** As an operator, I want fetched PDFs parsed in the same spawned, rlimited worker
uploads use — from a temp file that never outlives the request — and every PDF failure to be a
coded 422 instead of the 500 it is today, so a hostile PDF can neither stall the service nor crash
the request.

**Independent Test:** With `fetch_url` patched to return `application/pdf` bytes and
`extract_pdf_in_subprocess` patched to record its arguments, assert it is called with a `Path`
inside the spool directory and `app.state.extraction_settings`, that the file has mode `0600`
while the worker runs, and that no file with the spool prefix remains after a success, after each
of the five typed worker errors, after a simulated rlimit kill, and after `asyncio.CancelledError`
raised inside the worker call; for each typed error assert a 422 with `error == "extraction_failed"`
and the closed reason from the table below; a request with `extract_route_enabled` at its default
`false` is not 404'd.

| Worker error | `error` | `reason` |
|---|---|---|
| `PDFEncryptedError` | `extraction_failed` | `pdf_encrypted` |
| `PDFNoTextError` | `extraction_failed` | `pdf_no_text` |
| `PDFPageLimitError` | `extraction_failed` | `pdf_page_limit` |
| `PDFClassifiableTextLimitError` | `content_too_large` | `promptguard_budget` (US-001's reason) |
| `PDFTooLargeError` | `content_too_large` | `pdf_too_large` |
| `PDFExtractionError` (incl. rlimit / wall-clock kill) | `extraction_failed` | `pdf_extraction_error` |

**Implementation Hints:**
- The worker takes a `Path` (`pipeline/pdf_subprocess.py:156-159`; the child opens the file
  itself so the parent never holds the bytes — `:173`). `/retrieve` holds bytes
  (`pipeline/stage5_url_audit.py:221`, `orchestrator.py:352`). Add
  `extract_pdf_bytes_in_subprocess(data: bytes, settings: ExtractionSettings) -> ExtractionResult`
  to the **un-hashed** `pipeline/pdf_subprocess.py`: `tempfile.NamedTemporaryFile(prefix=
  "forage-retrieve-", dir=<the same directory `_spool_upload` uses — read `retrieval_app.py:
  1127-1152`>, delete=False)`, `os.fchmod(fd, 0o600)`, write, close, call the path variant, and
  `path.unlink(missing_ok=True)` in a `finally` that also covers `asyncio.CancelledError` and a
  worker exception. Invariant 1: the prefix is `forage-…`, never `poppy-…` (the upload helper's
  `poppy-extract-` prefix is pre-existing and out of scope here). The extra write+read is accepted
  (bodies are capped at 10 MB); record it in Decisions Made.
- Route the `detect_content_type` branch (`:346-351`) through `await asyncio.to_thread(
  extract_pdf_bytes_in_subprocess, fetch_result.response_body, extraction_settings)` under
  US-002's `retrieve_extraction_semaphore`. `run_retrieve_pipeline` therefore also takes
  `extraction_settings: ExtractionSettings` (required keyword-only; the handler passes
  `app.state.extraction_settings`). **Never** touch `ExtractionAdmissionMiddleware`
  (`retrieval_app.py:1080-1126`): it is path-gated to `/extract`, 404s on `route_enabled`
  (`config.yaml:27` ships `false`), reserves 50 MiB per waiter and answers 429 `busy` — none of
  which `/retrieve` declares or wants. A test drives a fetched-PDF `/retrieve` with
  `extract_route_enabled` absent/false and asserts 200.
- Error mapping: wrap the worker call in the mapping above. `RetrieveErrorCode`
  (`pipeline/contract.py:243-259`) is a closed six-member Literal with no extraction-failure
  member, so add **exactly one**, `extraction_failed` (already in `Extract422ErrorCode`, so the
  deduplicated `ERROR_CODES` total stays eighteen — `tests/test_contract_errors.py:233` — while
  `RETRIEVE_ERROR_CODES` moves 6 → 7 at `:241`, and `tests/test_contract_schema.py`'s literal
  nine-member `/retrieve`+`/search` 422 enum pin becomes ten). Reasons are closed literals in
  `contract.py` beside `PROMPTGUARD_BUDGET`. The file route's own mapping (`orchestrator.py:
  441-456`) is the model; `document_failure` (`:149`) is `/extract`-only (`DOCUMENT_FAILURE_CODES`),
  so build the `/retrieve` `PipelineError` directly with the closed reason.
- Docstring continuation line: "`/retrieve` 422 gains `extraction_failed` (fetched-PDF worker
  failures) with reasons `pdf_encrypted` | `pdf_no_text` | `pdf_page_limit` |
  `pdf_extraction_error`, and `content_too_large` gains `pdf_too_large`"; regenerate; re-create the
  golden. MINOR under GOVERNANCE ruling (b) with the announcement obligation the Release body meets.
- Tests: `kit_tools/testing/TESTING_GUIDE.md:255` maps `pipeline/pdf_subprocess.py` to
  `tests/test_stage1_pdf.py` — the bytes entry point, the spool lifecycle and the mapping table go
  there (there is no `tests/test_pdf_subprocess.py`, and no test drives the worker today; a real
  `RLIMIT_CPU` kill is not reproducible in the hermetic suite, so the kill case patches the worker
  to raise `PDFExtractionError`). The route-level cases go beside
  `tests/test_orchestrator.py::test_post_retrieve_error_response` (`:1734`).
- Docs: `kit_tools/docs/MONITORING.md:163` (`retrieve.errors` key list) gains `extraction_failed`;
  `kit_tools/docs/API_GUIDE.md:197-199` and `:421`'s `/retrieve` 422 table and
  `kit_tools/docs/TROUBLESHOOTING.md:168`'s table gain the row with its reasons;
  `kit_tools/arch/SECURITY.md:198` (the middleware/admission table) gains a `/retrieve` line saying
  stage-1 work is bounded by `retrieve.extraction_concurrency`, not by admission control;
  `CODE_ARCH.md` says both routes parse PDFs in the worker.
- Rotates `sanitizer_revision` (`orchestrator.py`, `contract.py`), ruling 6 / R32.

**Acceptance Criteria:**
- [ ] Fetched `application/pdf` bodies are parsed by `extract_pdf_bytes_in_subprocess` →
      `extract_pdf_in_subprocess` inside `asyncio.to_thread`, with `app.state.extraction_settings`
      (asserted positional/keyword argument), under `retrieve_extraction_semaphore`.
- [ ] The spool file has mode `0600` while the worker runs and is unlinked on success, on every
      typed error, on a simulated kill and on cancellation (a test asserts the spool directory holds
      no `forage-retrieve-*` file after each).
- [ ] Each row of the mapping table yields the stated `error` and `reason` as a 422, never a 500; a
      fetched-PDF `/retrieve` with `extract_route_enabled` at its default is 200.
- [ ] `RetrieveErrorCode` gains exactly `extraction_failed`; the reason literals are constants in
      `pipeline/contract.py`; `tests/test_contract_errors.py` count assertions (`:241` → 7, total
      stays 18) and `tests/test_contract_schema.py`'s 422 enum pin are updated; the docstring
      continuation line is appended; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created; `--check` green.
- [ ] `ExtractionAdmissionMiddleware`, `_spool_upload` and `/extract`'s behaviour are untouched
      (existing tests pass without edits); `SECURITY.md:50` still holds.
- [ ] `MONITORING.md:163`, `API_GUIDE.md`, `TROUBLESHOOTING.md:168`, `SECURITY.md:198` and
      `CODE_ARCH.md` carry the rows and sentences named in the hints.
- [ ] `sanitizer_revision` rotation measured (revert `orchestrator.py` and `contract.py` each in
      turn, both-reverted control) and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and
      `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: A corrupt cache entry is a miss, not a 500

**Priority:** P1

**Description:** As an operator, I want a cache value that does not parse as `RetrievedContent` to be
dropped, counted and treated as a miss, so a schema drift or a poisoned value can never turn one URL
into a 500 for the rest of its TTL.

**Independent Test:** Seed `FakeStorage` with a key whose value is `{"not": "content"}` and drive
`ContentCache.get`; assert it returns `None`, the key was deleted, `CacheMetrics.corrupt_entries ==
1`, exactly one WARNING whose message contains `cache_entry_corrupt` and the `ret:<sha256>` key
digest was logged with no payload bytes in it, and a following `/retrieve` for the same URL runs
the pipeline and re-populates the key.

**Implementation Hints:**
- The unguarded parse is `cache.py:784` inside `ContentCache.get` (`:776-790`). Catch `ValueError`
  — `pydantic_core.ValidationError` (raised for both malformed JSON and a schema mismatch under the
  pinned pydantic 2.13) is a `ValueError` subclass — call `self._storage.delete(key)` the way the
  tz-naive branch at `:786-788` already does, increment a new `CacheMetrics.corrupt_entries`
  (`cache.py:187-210`), and return `None`.
- Logging: `_closed_vocabulary_reason` (`:297-305`) is an exception-to-reason mapper, not a token
  registry; log the fixed literal `cache_entry_corrupt` directly in the `logger.warning("…(%s)",
  token)` shape used at `:418-421` / `:481-485`, plus the cache key (`cache_key`, `:106-117`, is a
  `ret:<sha256>` digest — one-way, credential-free, and what an operator needs to correlate
  repeats); never the raw value or `str(exc)`. `tests/test_cache.py` asserts the vocabulary today
  and the new test extends it with a sentinel-in-value assertion.
- `CacheMetrics` **is** mirrored on `/metrics` (`CacheMetricsResponse`, `retrieval_app.py:568-600`,
  `extra="forbid"`): the counter is a wire addition — docstring continuation line, regenerate,
  re-create the golden (ruling 5). Order guards: `tests/test_contract_metrics.py:135`, `:161`.
- `cache.py` is not hashed, but the docstring line in `contract.py` is (R32): this story rotates.
  Spec 4 builds its signature check on this same branch (an unverifiable value is *also* a miss),
  so keep the guard as one function the HMAC check can call first.
- Docs: `kit_tools/docs/MONITORING.md` `/metrics` cache table gains the `corrupt_entries` row;
  `kit_tools/docs/TROUBLESHOOTING.md` notes that a repeated `cache_entry_corrupt` WARNING for one
  key digest means an external writer (spec 4's `integrity_rejects` is the stronger signal).

**Acceptance Criteria:**
- [ ] `ContentCache.get` returns `None` for a value that fails `RetrievedContent` validation or JSON
      parsing, deletes the key and increments `corrupt_entries`; the next request repopulates it.
- [ ] The WARNING carries the closed token `cache_entry_corrupt` and the key digest only; a
      sentinel string in the corrupt value appears in no log record.
- [ ] `/metrics` serves `corrupt_entries` in the cache section; the docstring line is appended,
      `uv run python -m scripts.export_contract` run, `tests/golden/contract_1_3_0.json` re-created,
      `uv run python -m scripts.export_contract --check` green.
- [ ] No `/retrieve` request returns 500 for any cache content (a test drives three malformed shapes:
      invalid JSON, wrong schema, wrong `retrieved_at` type).
- [ ] `sanitizer_revision` rotation (`contract.py` docstring line) measured and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-005: Operator policy floors — fail-closed floor and threshold ceiling, reported on every response

**Priority:** P2

**Description:** As an operator, I want a server-side fail-closed floor and a threshold ceiling on
`/retrieve` and `/search` that a caller cannot lower or raise past, off by default, with the
effective policy reported on every response, so the security floor for web content is mine to set
while today's contract holds for every existing caller.

**Independent Test:** With `promptguard_fail_closed_floor: true` and no classifier loaded, a
`/retrieve` request with `promptguard_fail_closed: false` returns `promptguard_state ==
"unavailable_blocked"` and `effective_promptguard_fail_closed is True`, and a `/search` request with
the same flag omits every result under `promptguard_unavailable` and reports the same; with
`promptguard_threshold_ceiling: 0.5` and a classifier double scoring `0.7`, a request carrying
`promptguard_threshold: 1.0` is blocked and reports `effective_promptguard_threshold == 0.5`; a
cache hit under a floored request reports the floored values; with the defaults (`false`, `1.0`),
both routes behave exactly as today and report the request's own values.

**Implementation Hints:**
- Decision 3 + R10: `config.yaml` keys `promptguard_fail_closed_floor` (bool, default `false`) and
  `promptguard_threshold_ceiling` (float 0.0–1.0, default `1.0`), validated at boot like the cache
  bounds (a non-bool / out-of-range value refuses boot with a closed-vocabulary message; test
  modelled on `tests/test_app.py:1208`). `/extract` keeps its hard-pinned fail-closed
  (`orchestrator.py:469`, `:545`) and its own threshold read (`retrieval_app.py:1704`) — untouched.
- Resolve **once, in the handler, by replacing the request (R24).** The pipelines read
  `request.promptguard_fail_closed` at `pipeline/orchestrator.py:258` (cache fingerprint), `:373`
  (`sanitize_and_structure`) and `:1019` (`/search`), and neither takes it as a parameter; a
  parallel kwarg would leave the fingerprint reading the caller's value — the exact bypass to avoid.
  One helper in `retrieval_app.py` (not hashed; `pipeline/search_providers/policy.py:35`
  `apply_request_policy` is the precedent for handler-side request policy) computes
  `effective_fail_closed = body.promptguard_fail_closed or floor` and `effective_threshold =
  min(body.promptguard_threshold, ceiling)` and hands the pipeline `body.model_copy(update={...})`.
  `cache_policy_fingerprint` (`cache.py:120-164`) already takes both fields from the request, so a
  floored request keys to the fail-closed / ceiling'd entry with no further change.
- Report **after the pipeline, on every response.** The handler returns
  `content.model_copy(update={"effective_promptguard_fail_closed": …,
  "effective_promptguard_threshold": …})` for `/retrieve` and the same for `/search`, so a cache hit
  (whose stored object was built by the pipeline before any stamping) reports this request's
  effective values — which are also its fingerprint inputs, so hit and miss always agree. Field
  definitions: `RetrievedContent` (`models.py:59`, beside `promptguard_state` `:110`) and
  `SearchResponse` (`:395`, beside `promptguard_unavailable`) — `effective_promptguard_fail_closed:
  bool = True` and `effective_promptguard_threshold: float = 0.85` (defaults equal to the request
  defaults so a 1.2.0 golden reader and a pre-upgrade cached entry still validate; the stored value
  is always overwritten by the stamp). Docstring line ("`RetrievedContent` and `SearchResponse`
  gain `effective_promptguard_fail_closed` and `effective_promptguard_threshold`; `/extract` is
  permanently fail-closed and carries neither") + regenerate + golden. `models.py` is not hashed;
  `contract.py` is (R32).
- **What the fields mean, stated honestly.** `effective_promptguard_fail_closed` is the fail-closed
  policy applied to this request — it decides behaviour only when the classifier is unavailable.
  `effective_promptguard_threshold` is the block threshold applied. Neither says "content was
  scanned": `promptguard_state` (`/retrieve`) and per-result omissions / `suspicious` (`/search`) say
  that, and trusted-tier content skips classification by design
  (`pipeline/stage3_promptguard.py:77-88`, `skip_reason="trusted_tier"`). The field descriptions,
  `kit_tools/docs/API_GUIDE.md` rows and the `kit_tools/arch/SECURITY.md` sentence ("the floor is
  the operator's, the request is the consumer's; the floor bounds `promptguard_fail_closed`, the
  ceiling bounds `promptguard_threshold`, and neither overrides the trusted-tier skip") all carry
  that scope. Spec 3's leading-dot allowlist rule bounds the trusted-tier surface.
- The seven boundary-text copies (spec 3 US-004 enumerates them) do not need to name the floors —
  they are operator config, not request knobs; the request fields' descriptions do gain "bounded by
  the operator's floor/ceiling".
- Aggregate telemetry (a `policy_floor_applied` counter) is deliberately out of scope; the
  per-response fields are the signal.
- Docs: `docs/configuration.md` rows (`| Key | Default | Allowed range | Purpose |`),
  `kit_tools/docs/API_GUIDE.md` field rows, `kit_tools/arch/SECURITY.md` sentence above.

**Acceptance Criteria:**
- [ ] `promptguard_fail_closed_floor` and `promptguard_threshold_ceiling` are read from `config.yaml`
      at boot with the defaults above; a non-boolean floor or an out-of-range ceiling refuses boot
      with a closed-vocabulary message.
- [ ] With the floor `true`, `/retrieve` and `/search` behave fail-closed regardless of the request
      flag; with the ceiling below the request's threshold, classification uses the ceiling; with the
      defaults, behaviour and every existing test are unchanged.
- [ ] The value reaching `cache_policy_fingerprint` is the effective value (a test asserts a
      fail-open entry is not served to a floored request and that the fingerprint inputs equal the
      effective values).
- [ ] `RetrievedContent` and `SearchResponse` carry `effective_promptguard_fail_closed` and
      `effective_promptguard_threshold`, stamped by the handler on every response, cache hit or miss
      (a test serves a hit under a floored request and asserts the floored values; a pre-upgrade
      cached entry without the fields is served with the stamped values).
- [ ] Docstring line appended; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created; `--check` green.
- [ ] The field descriptions, `API_GUIDE.md` rows and `SECURITY.md` sentence state that the fields
      report the policy applied, not whether content was scanned, and name the trusted-tier skip.
- [ ] `docs/configuration.md` carries both rows.
- [ ] `sanitizer_revision` rotation (`contract.py` docstring line) measured and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- A fetched page that yields zero windows classifies nothing and reports `promptguard_state ==
  "scanned"` as today (US-001).
- A page exactly at `max_extracted_characters(retrieve.max_promptguard_chunks)` classifies; one
  character over is refused `content_too_large` / `promptguard_budget` (US-001).
- A cache hit is served without acquiring the classification semaphore (US-001).
- A classification-wait timeout on `/retrieve` under a fail-open request reports
  `unavailable_allowed`, never a 500; on `/search` the result is omitted `promptguard_unavailable`
  (US-001).
- An extraction exception releases the `/retrieve` extraction slot (US-002).
- Bytes served as `application/pdf` that sniff as HTML follow `detect_content_type`'s verdict, as
  today (US-003).
- A fetched encrypted PDF, a text-free PDF, a PDF over `max_pages`, a PDF whose text exceeds the
  classifiable ceiling, and a worker killed by `RLIMIT_CPU` / `RLIMIT_AS` / the wall clock each map
  to the table's `error` / `reason` (US-003).
- The spool file is gone after cancellation mid-parse (US-003).
- A cache value that parses but carries a different `sanitizer_revision` is already a miss through
  the key; US-004 covers only values that fail to parse (US-004).
- Three malformed shapes are tested: invalid JSON, wrong schema, wrong `retrieved_at` type (US-004).
- Floor `true` with request `true`: field reports `true`, nothing overridden (US-005).
- Floor `true` with a classifier loaded: no behavioural change; the field still reports `true`
  (US-005).
- A pre-upgrade cached entry (no `effective_*` fields) is served with the handler's stamped values
  (US-005).
- A `trusted_domains` match skips classification exactly as today; the `effective_*` fields report
  the policy, `promptguard_state` reports the skip (US-005).

## Out of Scope

- HMAC signing of cache values and the `cache_unauthenticated` degraded reason — spec 4 (which builds
  on US-004's miss branch).
- Changing `/extract`'s limits, its admission middleware, or its hard-pinned fail-closed policy.
- `/search`'s own synchronous `extract_html` / `scan_structural` sites on ≤ 2 000-character fields.
- A wall clock on `/retrieve` HTML extraction; sizing `retrieve.extraction_concurrency` and
  `classification_concurrency` — spec 6.
- Hostname semantics for `blocked_domains` / trust tiers and the leading-dot allowlist rule — spec 3.
- Making the floors per-route or per-caller; aggregate floor telemetry.
- A bytes-accepting worker entry point that avoids the spool write (profiling may motivate it later).

## Assumptions

- Poppy is the only consumer; every wire addition is defaulted so a 1.2.0 client still validates.
- `/retrieve` gets its own `RetrieveSettings` (budget, wait, extraction slot); `ExtractionSettings`
  stays `/extract`'s and supplies the PDF worker's rlimits for both routes.
- `RetrievedContent` cached under the old value shape stays readable; a value that fails to parse is
  a miss, never an error; a value without the `effective_*` fields is stamped on read.
- The `1.3.0` golden is mutable until spec 8 US-002 freezes it (ruling 5); it exists because spec 1
  US-004 created it (`depends_on`).
- The spool directory is the one `_spool_upload` already uses; disk cost per fetched PDF is at most
  the 10 MB fetch cap.

## Technical Considerations

- **Rotations (R32).** All five stories rotate `sanitizer_revision`: US-001 (`orchestrator.py`,
  `contract.py`), US-002 (`orchestrator.py`, `contract.py`), US-003 (`orchestrator.py`,
  `contract.py`), US-004 (`contract.py`), US-005 (`contract.py`). Each records its own before/after
  (ruling 6); a story touching two hashed files reverts each in turn with a both-reverted control.
- **Wire changes in the window.** `content_too_large` reason `promptguard_budget`,
  `retrieve.classification_wait_timeouts`, `search.classification_wait_timeouts` (US-001);
  `retrieve.extraction_queue_waits` (US-002); `extraction_failed` on `/retrieve` with four reasons
  and `content_too_large` reason `pdf_too_large` (US-003); `CacheMetrics.corrupt_entries` (US-004);
  `effective_promptguard_fail_closed`, `effective_promptguard_threshold` (US-005). Each appends a
  continuation line to the `* ``1.3.0`` — …` bullet (R34) and re-creates `contract_1_3_0.json`;
  `tests/test_contract_export.py` is red until it does. The epic wrapper's window list is updated by
  the parent.
- **Event-loop proof.** The responsiveness test follows `tests/test_app.py:958-993` (real
  `ASGITransport` client over the real lifespan, connected cache, `threading.Event` release); a
  mocked loop proves nothing.
- **Required keyword-only parameters.** `run_retrieve_pipeline` gains `settings`,
  `classification_semaphore`, `retrieve_extraction_semaphore` and `extraction_settings`;
  `run_search_pipeline` gains `classification_semaphore` and `classification_wait_seconds`. All
  required; the module-level fallbacks (`retrieval_app.py:1378-1379`, `:1399-1401`) keep
  lifespan-free test clients working; every existing test call site is updated in the story that
  adds the parameter.
- **Pyright strict.** New parameters typed; no `type: ignore`.
- Related: `kit_tools/arch/SECURITY.md:50` (middlewares gate on `/extract` — unchanged), `:143`
  (chunk budget semantics — updated by US-001), `:198` (admission table — updated by US-003);
  `kit_tools/docs/MONITORING.md` (`/metrics`), `docs/configuration.md`.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Security: [SECURITY.md](../arch/SECURITY.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)
- Conventions: [CONVENTIONS.md](../docs/CONVENTIONS.md)
- API guide: [API_GUIDE.md](../docs/API_GUIDE.md)
- Configuration: [configuration.md](../../docs/configuration.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** `/retrieve` gets its own `RetrieveSettings` (a larger pre-checked chunk budget, a
bounded classification wait, an extraction slot) and shares the classification semaphore and the
PDF worker; it never shares `/extract`'s admission middleware.
**Rationale:** The chunk budget is a refusal (`promptguard/classifier.py:179-182`), so reusing
`/extract`'s 64 verbatim would turn ordinary long pages into 500s; the middleware is path-gated,
`route_enabled`-gated, over-reserves per waiter and emits a status `/retrieve` does not declare.
**Alternatives considered:** Reusing `extraction.max_promptguard_chunks` (too small for fetched
pages, and a refusal without a pre-check); a clamp that classifies a prefix (a classifier change
and an unscanned tail); the admission middleware (coupling, 404 on default config, 429 on a route
that declares only 200/422).
**Source:** `pipeline/orchestrator.py:352-377`, `:459-476`, `:485-549`; `promptguard/classifier.py:
158-182,210`; `retrieval_app.py:1080-1126`; `pipeline/extraction_limits.py:17-57,98-151`.

**Decision:** Fetched PDFs are spooled to a 0600 temp file by a bytes entry point in
`pipeline/pdf_subprocess.py`, and the worker's typed errors map to one new `/retrieve` code,
`extraction_failed`, with closed reasons.
**Rationale:** The worker takes a `Path` by design (the child reads the file); `/retrieve` 500s on
every PDF error today; `RetrieveErrorCode` has no member for it and adding one is a MINOR with an
announcement inside the window the epic already opened.
**Alternatives considered:** Widening the worker to accept bytes (loads input in the parent, the
thing the worker avoids); mapping to `fetch_error` (a lie about where the failure happened).
**Source:** `pipeline/pdf_subprocess.py:24-28,156-173`; `pipeline/stage1_pdf.py:79-91`;
`pipeline/contract.py:196-259`; `tests/test_contract_errors.py:233,241`.

**Decision:** A corrupt cache value is a miss with a counter and a closed-vocabulary WARNING that
carries the key digest.
**Rationale:** `cache.py:784` parses without a guard; the closed log vocabulary is asserted by
`tests/test_cache.py`; the `ret:<sha256>` key is a one-way digest an operator can correlate.
**Alternatives considered:** Raising a typed error to the handler (still a failed request).
**Source:** `cache.py:106-117`, `:776-790`, `:187-210`, `:297-305`; `retrieval_app.py:568-600`.

**Decision:** Policy floors are resolved once in the handler by replacing the request, and the
effective values are stamped on every response after the pipeline returns.
**Rationale:** Neither pipeline takes the flag as a parameter and both read it at three sites; a
parallel kwarg would leave the cache fingerprint on the caller's value; stamping after the pipeline
makes cache hits honest without touching the hashed `stage4_structuring.py` builder.
**Alternatives considered:** A pipeline kwarg (rotation plus the fingerprint bypass); stamping inside
the pipeline (the cached copy would carry a stale value for a different request).
**Source:** `pipeline/orchestrator.py:258,373,1019`; `cache.py:120-164`; `retrieval_app.py:
1594-1600,1811-1817`; `pipeline/search_providers/policy.py:35`.

### Scope Adjustments

- The review's "corrupted cache entry" item moved here from the cache-integrity spec because the
  parse guard is the branch spec 4's signature check reuses.
- Validation round 1 (2026-09-19): the old US-002 split into US-002 (off-loop + bounded slot) and
  US-003 (PDF worker + failure vocabulary); the old US-003/US-004 became US-004/US-005; US-001
  gained the pre-check, the bounded wait and `/search` semaphore parity (R16); US-005 gained the
  threshold ceiling (R10) and the handler-stamping mechanism (R24).

### Decisions Made

- Cache hits bypass the classification semaphore (US-001).
- The `/retrieve` budget is its own key (`retrieve.max_promptguard_chunks`, default 256); an
  over-budget page is refused under the existing `content_too_large` code, never a new code
  (R22).
- The classification wait is bounded and a timeout degrades to the classifier-unavailable path
  (never a 500, never a new status); the extraction slot's wait is unbounded FIFO with a counter
  (a `/retrieve` has no refusal status, and adding one would be a wire change without a consumer
  need).
- The spool write+read for fetched PDFs is accepted (≤ 10 MB); a bytes-accepting worker is a
  later optimisation.
- `extraction_failed` is the one new `/retrieve` code; `PDFClassifiableTextLimitError` maps to
  `content_too_large` / `promptguard_budget` (it is the same ceiling) and `PDFTooLargeError` to
  `content_too_large` / `pdf_too_large`.
- The `effective_*` fields report the policy applied, not whether content was scanned; the
  security review's option (a) was taken with the threshold ceiling added (R10), and the
  trusted-tier skip is named beside them rather than floored here (spec 3's leading-dot rule bounds
  it).
- Aggregate floor telemetry is out of scope (overrules the completionist suggestion); the
  per-response fields are the signal.
- The findings ledger (`kit_tools/AUDIT_FINDINGS.md`) is a gitignored run artifact; stories record
  the WA-D items they close in Implementation Notes.

## Clarifications

### Session 2026-09-19
- Q: Should Forage enforce a server-side floor for `promptguard_fail_closed`? → A: Operator floor,
  off by default, with the effective value reported on the response (decision 3).
- Q: Does the eight-spec decomposition match what the epic should carry? → A: Yes, all eight
  (decision 1).

### Session 2026-09-19 (validation round 1)
- Rulings applied: R10 (threshold ceiling + `effective_promptguard_threshold`), R16 (`/search`
  semaphore parity in US-001), R22 (pre-checked refusal under `retrieve.max_promptguard_chunks`),
  R23 (spooled file, own slot, one new code with closed reasons), R24 (handler replaces the request
  and stamps every response), R32 (all five stories rotate), R33 (no secret-bearing capture), R34
  (docstring bullet format). Story-quality split applied and stories renumbered.

## Open Questions

- [ ] Whether `semaphore_saturation` / the new wait counters should gain a per-route breakdown on
      `/metrics` (non-blocking; this spec adds one counter per route).
- [ ] Whether a wall clock on `/retrieve` HTML extraction is needed once spec 6 sizes the slot
      (non-blocking; the fetch timeout and cap bound the input today).
