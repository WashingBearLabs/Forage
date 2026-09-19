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
> already has — a PromptGuard chunk budget and the classification semaphore, stage-1/stage-2 work
> off the event loop, spawn-isolated PDF parsing under the same rlimits — make a corrupt cache entry
> a miss instead of a 500, and add the operator's fail-closed floor (owner decision 3).
> Context: holistic review WA-D (`/retrieve` vs `/extract` hardening asymmetry); epic rulings 3, 5,
> 6 are binding here.

## Overview

The 2026-08-28 review found that "the upload path got hardened; the web-fetch path that runs on
*more* hostile input did not." Three of its five WA-D items are still live at `main` today.

`run_retrieve_pipeline` (`pipeline/orchestrator.py:208-`) calls `sanitize_and_structure` at
`:368-377` with **no** `max_promptguard_chunks` and **no** semaphore; the file route of `/extract`
does both (`:539` `async with classification_semaphore:`, `:549`
`max_promptguard_chunks=settings.max_promptguard_chunks`). A 10 MB fetched page therefore yields
unbounded 512-token windows of CPU inference with nothing serialising it against the next request.
Stage 1 and stage 2 run **synchronously on the event loop** for fetched content: `extract_pdf`
(`:352`), `extract_html` (`:355`) and `scan_structural` (`:174`, shared by both routes) — only
stage 3 is threaded — so a pathological page or PDF stalls the single-process service, health checks
included. `/extract` parses PDFs in a spawned worker with CPU and address-space rlimits
(`pipeline/pdf_subprocess.py:156`, `:32-48`); `/retrieve` uses in-process `pypdf`.

Separately, `ContentCache.get` calls `RetrievedContent.model_validate_json(raw)` with no guard
(`cache.py:784`): a schema-drifted or poisoned value 500s that URL until its TTL expires — the
review's "corrupted cache entry → HTTP 500, not a miss" — and it compounds the integrity work
spec 4 does.

The load-bearing decision is **parity by reuse**: `/retrieve` takes the same `ExtractionSettings`
and the same semaphore the file route takes, calls the same subprocess worker for PDFs, and threads
the same calls — no second set of limits, no new semaphore. The one policy addition is decision 3:
an operator-side `promptguard_fail_closed_floor` (default `false`, so the contract is unchanged for
every existing caller) that overrides a caller's `false`, with `effective_promptguard_fail_closed`
on both responses so the consumer can see which policy applied. That field is the only wire change
here and lands inside the 1.3.0 window spec 1 opened (ruling 5).

## Goals

- `/retrieve` classifies at most `extraction.max_promptguard_chunks` windows per page (64 by
  default) and two concurrent `/retrieve` requests never classify at the same time (semaphore size 1
  by default); `/extract` behaviour and its tests are byte-for-byte unchanged.
- During a slow fetched-page extraction (fake extractor blocking for 2 s), a concurrent `/health`
  request completes in under 500 ms; a fetched PDF is parsed in the spawned worker under the same
  `child_cpu_seconds` / `child_address_space_bytes` limits as an uploaded one.
- A cache value that fails to parse is served as a miss, deleted, and counted; zero `/retrieve`
  responses are 500 because of cache content.
- With `promptguard_fail_closed_floor: true`, a request carrying `promptguard_fail_closed: false`
  behaves as fail-closed on both routes and reports `effective_promptguard_fail_closed: true`; with
  the default `false`, every existing test passes unchanged.

## User Stories

### US-001: Chunk budget and classification semaphore on `/retrieve`

**Priority:** P1

**Description:** As an operator, I want fetched pages classified under the same chunk budget and the
same concurrency gate as uploads, so one hostile page cannot burn unbounded CPU or run inference in
parallel with another request.

**Independent Test:** Drive `run_retrieve_pipeline` with a patched `fetch_url` returning a page whose
text yields 65 windows and a mock classifier that records `max_chunks`; assert `classify` was called
with `max_chunks == 64`; then start two `/retrieve` calls against a fake classifier whose `classify`
blocks on an event and assert the second call's `classify` has not started while the first holds
the semaphore, and completes once the event is set.

**Implementation Hints:**
- `run_retrieve_pipeline` (`pipeline/orchestrator.py:208-222`, keyword-only `cache`, `classifier`,
  `config`, `sanitizer_revision`) gains `settings: ExtractionSettings` and
  `classification_semaphore: asyncio.Semaphore`, mirroring `run_extract_pipeline_from_file`'s
  signature (`:485-500`). Pass `max_promptguard_chunks=settings.max_promptguard_chunks` at `:368-377`
  and wrap the `sanitize_and_structure` await in `async with classification_semaphore:` exactly as
  `:539` does. Keep the cache read (`:82-88` in the function body) outside the semaphore — a cache
  hit must not wait on a classification.
- The handler at `retrieval_app.py:1595-1601` already has both objects in reach: `settings` from
  `extraction_settings_from_config` (`pipeline/extraction_limits.py:98-151`, published on
  `app.state` in the lifespan) and `app.state.classification_semaphore` (`retrieval_app.py:
  1226-1228`, module-level fallback `:1400-1402` for lifespan-less transports). Thread both through;
  read them the way the `/extract` handler does (`:1732`).
- `MAX_PROMPTGUARD_CHUNKS = 64` (`pipeline/extraction_limits.py:17`) is the default of
  `ExtractionSettings.max_promptguard_chunks` (`:54`); `config.yaml:44` and
  `docs/configuration.md:484` document it as an `/extract` limit today — reword both to "extraction
  and retrieval".
- Tests: extend `tests/test_orchestrator.py::test_retrieve_full_pipeline_happy_path` (`:244`) to
  assert the kwargs; add the two-caller semaphore test beside it using the `MagicMock(spec=
  PromptGuardClassifier)` idiom at `:1489`. `tests/test_app.py::test_post_retrieve_endpoint`
  (`:1523`) proves the handler threads the objects (patch `run_retrieve_pipeline` and assert the
  kwargs).
- `pipeline/orchestrator.py` is hashed: rotation recorded (ruling 6).

**Acceptance Criteria:**
- [ ] `run_retrieve_pipeline` accepts `settings` and `classification_semaphore`; the `/retrieve`
      handler passes the lifespan's `ExtractionSettings` and semaphore; a test asserts both kwargs.
- [ ] A 65-window fetched page results in `classify(..., max_chunks=64)`; the `/extract` file route's
      call is unchanged (its existing tests pass without edits).
- [ ] Two concurrent `/retrieve` calls serialise classification through the shared semaphore; a
      cache hit is served without acquiring it.
- [ ] `config.yaml` and `docs/configuration.md` describe `max_promptguard_chunks` and
      `classification_concurrency` as applying to `/retrieve` and `/extract`.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce) and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Stage 1 and stage 2 off the event loop; PDF isolation parity

**Priority:** P1

**Description:** As an operator, I want HTML extraction, the structural scan and fetched-PDF parsing
to run off the event loop — PDFs in the same rlimited worker uploads use — so a pathological page
cannot stall `/health` or any other request.

**Independent Test:** With `extract_html` patched to block 2 s and a `/retrieve` in flight, a
concurrent `/health` returns within 500 ms; with `fetch_url` returning `application/pdf` bytes,
`extract_pdf_in_subprocess` (patched to record its arguments) is called with the lifespan's
`ExtractionSettings`, and a worker that exceeds `child_cpu_seconds` surfaces as the same
`extraction_failed` error `/extract` reports.

**Implementation Hints:**
- Sync call sites: `extract_pdf` at `pipeline/orchestrator.py:352`, `extract_html` at `:355`,
  `scan_structural` at `:174` inside `sanitize_and_structure` (shared with `/extract`, so
  threading it there benefits both routes — keep `/extract` output identical). Use
  `asyncio.to_thread` as stage 3 already does at `pipeline/stage3_promptguard.py:130`.
- PDF isolation: `/extract`'s file route calls `pipeline.pdf_subprocess.extract_pdf_in_subprocess`
  (`pipeline/pdf_subprocess.py:156`) inside `asyncio.to_thread` at `:503-507`; rlimits come from
  `_apply_child_limits` (`pdf_subprocess.py:32-48`, `RLIMIT_CPU` / `RLIMIT_AS` from
  `settings.child_cpu_seconds` / `child_address_space_bytes`). Route the `detect_content_type`
  branch at `:346-351` through the same worker for fetched bytes, under the admission controller
  (`extraction_concurrency`, `pipeline/extraction_limits.py:18,55,153-156`) rather than a new slot —
  check how the file route acquires it and reuse that path; `semaphore_saturation`
  (`retrieval_app.py:448,864,979`) counts admission refusals and should count `/retrieve`'s too.
- The `/retrieve` fetch cap is 10 MB streamed (`pipeline/stage5_url_audit.py:200-219`); the worker's
  address-space limit bounds the parse. Confirm that a PDF over the worker's limits maps to the
  existing `extraction_failed` error, not a 500, and that the response `content_type` is still
  reported as it is today.
- Tests: `tests/test_orchestrator.py::test_retrieve_full_pipeline_happy_path` (`:244`) and the
  `/retrieve` cases in `tests/test_app.py` (`:1523`, `:1593`, `:1734`) cover the happy path; add the
  responsiveness test in `tests/test_app.py` with an `ASGITransport` client and `asyncio.gather`;
  add a PDF case that patches `extract_pdf_in_subprocess`. `tests/test_pdf_subprocess.py` (if
  present) is the model for the rlimit case.
- Rotates `sanitizer_revision` (`orchestrator.py`), ruling 6.
- Docs: `kit_tools/arch/CODE_ARCH.md`'s pipeline narrative and `kit_tools/arch/SECURITY.md`'s
  "`/retrieve` vs `/extract`" statement say both routes parse PDFs in the rlimited worker and run
  stage 1/2 off the loop.

**Acceptance Criteria:**
- [ ] `extract_html`, `extract_pdf` (fetched) and `scan_structural` are invoked via
      `asyncio.to_thread` or the subprocess worker; no synchronous call to them remains in
      `run_retrieve_pipeline` or `sanitize_and_structure` (a test asserts the patched functions are
      called from a non-event-loop thread).
- [ ] A 2 s blocking `extract_html` does not delay a concurrent `/health` beyond 500 ms.
- [ ] Fetched PDFs are parsed by `extract_pdf_in_subprocess` with the lifespan's
      `ExtractionSettings`; a worker exceeding `child_cpu_seconds` yields the same error code
      `/extract` yields.
- [ ] `/extract` responses on the existing fixture corpus are byte-identical before and after
      (existing tests unchanged).
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce) and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: A corrupt cache entry is a miss, not a 500

**Priority:** P1

**Description:** As an operator, I want a cache value that does not parse as `RetrievedContent` to be
dropped, counted and treated as a miss, so a schema drift or a poisoned value can never turn one URL
into a 500 for the rest of its TTL.

**Independent Test:** Seed `FakeStorage` with a key whose value is `{"not": "content"}` and drive
`ContentCache.get`; assert it returns `None`, the key was deleted, `CacheMetrics.corrupt_entries ==
1`, exactly one WARNING with message text `cache_entry_corrupt` was logged with no payload bytes in
it, and a following `/retrieve` for the same URL runs the pipeline and re-populates the key.

**Implementation Hints:**
- The unguarded parse is `cache.py:784` inside `ContentCache.get` (`:776-790`). Catch
  `pydantic.ValidationError` and `ValueError` (malformed JSON raises the latter), call
  `self._storage.delete(key)` the way the tz-naive branch at `:786-788` already does, increment a
  new `CacheMetrics.corrupt_entries` (`cache.py:186-210`), and return `None`.
- Log through the closed vocabulary: `_closed_vocabulary_reason` (`cache.py:297-305`) returns fixed
  tokens only; add `cache_entry_corrupt` as another literal and never include the raw value, the key
  or `str(exc)` — `tests/test_cache.py` asserts the vocabulary today and the new test extends it.
- `CacheMetrics` is served on `/metrics`; the metrics models use `extra="forbid"` (search epic
  ruling 21), so the new counter is a wire addition: append a docstring line to the `1.3.0` entry in
  `pipeline/contract.py`, regenerate, re-create `tests/golden/contract_1_3_0.json` (ruling 5). Check
  `tests/test_app.py`'s `/metrics` order guards
  (`test_served_metrics_are_the_handlers_dict_serialized`,
  `test_metrics_mirror_round_trips_the_served_body`) — key order matters.
- `cache.py` is not hashed; no rotation. Spec 4 builds its signature check on this same branch
  (an unverifiable value is *also* a miss), so keep the guard as one function the HMAC check can
  call first.
- Docs: `kit_tools/docs/MONITORING.md` `/metrics` cache table gains the `corrupt_entries` row;
  `kit_tools/docs/TROUBLESHOOTING.md` notes that a repeated `cache_entry_corrupt` WARNING for one
  key means an external writer.

**Acceptance Criteria:**
- [ ] `ContentCache.get` returns `None` for a value that fails `RetrievedContent` validation or JSON
      parsing, deletes the key and increments `corrupt_entries`; the next request repopulates it.
- [ ] The WARNING carries only the closed token `cache_entry_corrupt`; a sentinel string in the
      corrupt value appears in no log record.
- [ ] `/metrics` serves `corrupt_entries` in the cache section; the docstring line is appended,
      `uv run python -m scripts.export_contract` run, `tests/golden/contract_1_3_0.json` re-created,
      `uv run python -m scripts.export_contract --check` green.
- [ ] No `/retrieve` request returns 500 for any cache content (a test drives three malformed shapes:
      invalid JSON, wrong schema, wrong `retrieved_at` type).
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: Operator fail-closed floor (`promptguard_fail_closed_floor`)

**Priority:** P2

**Description:** As an operator, I want a server-side floor for fail-closed behaviour on `/retrieve`
and `/search` that a caller cannot lower, off by default, with the effective policy reported on the
response, so the security floor for web content is mine to set while today's contract holds for
every existing caller.

**Independent Test:** With `promptguard_fail_closed_floor: true` in the loaded config and no
classifier loaded, a `/retrieve` request with `promptguard_fail_closed: false` returns
`promptguard_state == "unavailable_blocked"` and `effective_promptguard_fail_closed is True`, and a
`/search` request with the same flag omits every result under `promptguard_unavailable` and reports
`effective_promptguard_fail_closed is True`; with the floor at its default `false`, both routes
behave exactly as today and report the request's own value.

**Implementation Hints:**
- Decision 3 verbatim: new `config.yaml` key `promptguard_fail_closed_floor` (bool, default `false`);
  when `true`, a caller's `promptguard_fail_closed: false` on `/retrieve` or `/search` is overridden
  to `true`; both responses gain an additive `effective_promptguard_fail_closed: bool`.
- Resolve in the handlers, not the pipeline: one helper in `retrieval_app.py` (not hashed) that reads
  the floor from `app.state.config` (`_load_config`, `:330-337`, dict) and returns the effective
  value; `/retrieve` (`:1595-1601`) and `/search` (`:1778-1815`) pass it as `promptguard_fail_closed`
  in place of the request's value. Validate the key at boot like the extraction bounds
  (`pipeline/extraction_limits.py:98-151`, `_bounded_int` pattern; a non-bool refuses boot with a
  test modelled on `tests/test_app.py::test_lifespan_refuses_an_out_of_range_cache_bound`, `:1208`).
- The request fields are `models.py:264-270` (`RetrieveRequest`) and `:295-300` (`SearchRequest`);
  the response fields go on `RetrievedContent` (`:59`, beside `promptguard_state` `:110`) and
  `SearchResponse` (`:395`, beside `promptguard_unavailable` `:463`) with `default=` equal to the
  request default so a 1.2.0 golden reader still validates. Docstring line + regenerate + golden
  re-created (ruling 5). Update the four boundary-text copies (`models.py:225-236`, `:277-288`,
  `retrieval_app.py:1578-1590`, `:1778-1793`) — spec 3's parity guard will assert they name the knob.
- The cache policy fingerprint (`cache.py:120-164`) takes `promptguard_fail_closed`; pass the
  **effective** value so a floored request never reads a fail-open entry.
- `/extract` is already hard-pinned (`pipeline/orchestrator.py:469`, `:545`) and is untouched.
- Docs: `docs/configuration.md` row (`| Key | Default | Allowed range | Purpose |`), `docs/API_GUIDE.md`
  field rows, `kit_tools/arch/SECURITY.md`: "the floor is the operator's, the request is the
  consumer's" — Forage still makes no trust decision (vision assumption).

**Acceptance Criteria:**
- [ ] `promptguard_fail_closed_floor` is read from `config.yaml` at boot, defaults to `false`, and a
      non-boolean value refuses boot with a closed-vocabulary message.
- [ ] With the floor `true`, `/retrieve` and `/search` behave fail-closed regardless of the request
      flag; with the floor `false`, behaviour and every existing test are unchanged.
- [ ] `RetrievedContent.effective_promptguard_fail_closed` and
      `SearchResponse.effective_promptguard_fail_closed` report the applied value on every response;
      docstring line appended, `uv run python -m scripts.export_contract` run,
      `tests/golden/contract_1_3_0.json` re-created, `uv run python -m scripts.export_contract --check`
      green.
- [ ] The cache key for a floored request is the fail-closed key (a test proves a fail-open entry is
      not served to a floored request).
- [ ] The four boundary-text copies name the floor; `docs/configuration.md`, `docs/API_GUIDE.md` and
      `kit_tools/arch/SECURITY.md` carry the rows and sentence named above.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- A fetched page that yields zero windows classifies nothing and reports `promptguard_state ==
  "scanned"` as today (US-001).
- A cache hit is served without acquiring the classification semaphore (US-001).
- Bytes served as `application/pdf` that sniff as HTML follow `detect_content_type`'s verdict, as
  today (US-002).
- A worker killed by `RLIMIT_CPU` or `RLIMIT_AS` maps to `/extract`'s existing error code, never a
  500 (US-002).
- A cache value that parses but carries a different `sanitizer_revision` is already a miss through
  the key; US-003 covers only values that fail to parse (US-003).
- Three malformed shapes are tested: invalid JSON, wrong schema, wrong `retrieved_at` type (US-003).
- Floor `true` with request `true`: field reports `true`, nothing overridden (US-004).
- Floor `true` with a classifier loaded: no behavioural change; the field still reports `true`
  (US-004).

## Out of Scope

- HMAC signing of cache values and the `cache_unauthenticated` degraded reason — spec 4 (which builds
  on US-003's miss branch).
- Changing `/extract`'s limits or its hard-pinned fail-closed policy.
- Hostname semantics for `blocked_domains` / trust tiers — spec 3.
- Making the floor per-route or per-caller; it is one operator boolean.
- Widening `classification_concurrency` beyond 1 — spec 6.

## Assumptions

- Poppy is the only consumer; every wire addition is defaulted so a 1.2.0 client still validates.
- The `/retrieve` admission controller (`extraction_concurrency`) is reused for fetched PDFs; no new
  semaphore.
- `ExtractionSettings` as published on `app.state` at lifespan is the single source of limits for
  both routes.
- `RetrievedContent` cached under the old value shape stays readable; a value that fails to parse is
  a miss, never an error.
- The `1.3.0` golden is mutable until spec 8 US-002 freezes it (ruling 5).

## Technical Considerations

- **Rotations.** US-001 and US-002 edit `orchestrator.py` and rotate `sanitizer_revision` (ruling 6);
  US-003 (`cache.py`) and US-004 (`retrieval_app.py`, `models.py`) do not.
- **Wire changes in the window.** `corrupt_entries` (US-003) and `effective_promptguard_fail_closed`
  (US-004) each append a docstring line and re-create `contract_1_3_0.json`; `tests/test_contract_
  export.py` is red until they do.
- **Event-loop proof.** The responsiveness test needs a real `ASGITransport` client and
  `asyncio.gather`; a mocked loop proves nothing.
- **Pyright strict.** New `run_retrieve_pipeline` parameters are typed; the module-level semaphore
  fallback (`retrieval_app.py:1400-1402`) keeps lifespan-free test clients working.
- Related: `kit_tools/arch/SECURITY.md` (`/retrieve` vs `/extract`), `kit_tools/docs/MONITORING.md`
  (`/metrics`), `docs/configuration.md`.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Security: [SECURITY.md](../arch/SECURITY.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)
- Conventions: [CONVENTIONS.md](../docs/CONVENTIONS.md)
- Configuration: [configuration.md](../../docs/configuration.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** `/retrieve` reuses `ExtractionSettings`, the classification semaphore and the PDF
subprocess worker; no parallel set of limits.
**Rationale:** The file route already threads all three (`pipeline/orchestrator.py:485-549`); a second
set would drift.
**Alternatives considered:** A `/retrieve`-specific chunk cap in `config.yaml` (drift); an in-process
PDF page limit (does not bound CPU or memory).
**Source:** `pipeline/orchestrator.py:352-377`, `:485-549`; `pipeline/pdf_subprocess.py:32-48,156`;
`pipeline/extraction_limits.py:17-57,98-151`.

**Decision:** A corrupt cache value is a miss with a counter and a closed-vocabulary WARNING.
**Rationale:** `cache.py:784` parses without a guard; the closed log vocabulary
(`_closed_vocabulary_reason`, `:297-305`) is asserted by `tests/test_cache.py`.
**Alternatives considered:** Raising a typed error to the handler (still a failed request).
**Source:** `cache.py:776-790`, `:186-210`, `:297-305`.

**Decision:** The fail-closed floor is resolved in the handlers and reported on the response.
**Rationale:** `retrieval_app.py` is not hashed, `app.state.config` is already the config surface
(`:330-337`), and the consumer must be able to see which policy applied.
**Alternatives considered:** Hard-pinning `true` (a meaning change — MAJOR under GOVERNANCE);
leaving it to the consumer (the review's finding stands).
**Source:** `models.py:264-270`, `:295-300`; `pipeline/orchestrator.py:373,469,545`.

### Scope Adjustments

- The review's "corrupted cache entry" item moved here from the cache-integrity spec because the
  parse guard is the branch spec 4's signature check reuses.

### Decisions Made

- Cache hits bypass the classification semaphore (US-001).
- The cache fingerprint uses the effective fail-closed value (US-004).

## Clarifications

### Session 2026-09-19
- Q: Should Forage enforce a server-side floor for `promptguard_fail_closed`? → A: Operator floor,
  off by default, with the effective value reported on the response (decision 3).
- Q: Does the eight-spec decomposition match what the epic should carry? → A: Yes, all eight
  (decision 1).

## Open Questions

- [ ] Whether the file route's admission-controller acquisition can be lifted into a helper both
      routes call, or `/retrieve` acquires it inline — decided by the implementer on reading
      `run_extract_pipeline_from_file` (non-blocking).
- [ ] Whether `semaphore_saturation` should gain a per-route breakdown; this spec counts `/retrieve`
      refusals in the existing counter (non-blocking).
