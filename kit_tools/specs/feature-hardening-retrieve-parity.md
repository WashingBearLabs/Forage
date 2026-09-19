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
execution_order: [US-001, US-006, US-002, US-003, US-004, US-005]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: `/retrieve` Parity With `/extract`

> **Spec 2 of `epic-forage-hardening`.** Give the web-fetch path the guarantees the upload path
> already has — a pre-checked PromptGuard chunk budget, the classification semaphore with a bounded
> wait and permit discipline (on `/search` too), fetch and stage-1 work bounded by a second
> `ExtractionAdmissionController`, stages 1, 2 and 4 off the event loop, fetched PDFs parsed in the
> spawned rlimited worker with a typed failure vocabulary — make a corrupt cache entry a miss
> instead of a 500, and add the operator's policy floors (owner decision 3, R10): a fail-closed
> floor on both fetch routes and a threshold ceiling on `/retrieve`, with the effective policy
> reported on the response.
> Context: holistic review WA-D (`/retrieve` vs `/extract` hardening asymmetry); epic rulings 3, 5,
> 6 and validation-round rulings R10, R16, R22, R23 (corrected in round 3: route-aware status,
> pipeline-side Protocol, bodies released before the wait, characters-only budget), R24, R32, R33,
> R34, R36–R42 are binding here.
> Execution order is declared in the frontmatter: the semaphore story (US-006) runs second because
> US-002 and US-003 build on the signature and sink it finishes.

## Overview

The 2026-08-28 review found that "the upload path got hardened; the web-fetch path that runs on
*more* hostile input did not." Three of its five WA-D items are still live at `main` today, and the
validation panel found three more the review missed.

`run_retrieve_pipeline` (`pipeline/orchestrator.py:208-215`; keyword-only `cache`, `classifier`,
`config`, `sanitizer_revision`) calls `sanitize_and_structure` at `:368-377` with **no**
`max_promptguard_chunks` and **no** semaphore; the file route of `/extract` does both (`:539`
`async with classification_semaphore:`, `:549` `max_promptguard_chunks=settings.
max_promptguard_chunks`). `/search` acquires no semaphore either (`:1014`). Stages 1, 2 and 4 run
**synchronously on the event loop** for fetched content: `extract_pdf` (`:352`), `extract_html`
(`:355`), `scan_structural` (`:174`, shared by both routes) and `structure_sanitization_result`
(`:190`, whose summary mode runs `pipeline/smart_extraction.py`'s regex set over every paragraph) —
only stage 3 is threaded — so a pathological page or PDF stalls the single-process service, health
checks included. `/extract` parses PDFs in a spawned worker with CPU, address-space and wall-clock
limits (`pipeline/pdf_subprocess.py:156`, `:32-48`); `/retrieve` uses in-process `pypdf` **with no
exception handling at all** — a fetched encrypted or text-free PDF is an HTTP 500 on `main` today.

Separately, `ContentCache.get` calls `RetrievedContent.model_validate_json(raw)` with no guard
(`cache.py:784`): a schema-drifted or poisoned value 500s that URL until its TTL expires — the
review's "corrupted cache entry → HTTP 500, not a miss" — and it compounds the integrity work
spec 4 does.

The load-bearing decisions, stated once because two validation rounds showed each one is a trap
when left implicit:

- **The chunk budget is a refusal, not a clamp (R22).** `PromptGuardClassifier.classify` raises
  `PromptGuardBudgetExceededError` above `max_chunks` (`promptguard/classifier.py:179-182`;
  `kit_tools/arch/SECURITY.md:143` says so). `/extract`'s file route survives it with a pre-check
  and a typed catch. `/retrieve` gets **its own, larger budget** (`retrieve.max_promptguard_chunks`,
  default 256 — 64 chunks is ~112 KB of text against a 10 MB fetch cap and ordinary long articles
  exceed it), a **characters-only** pre-check (`len(raw_text) > max_extracted_characters(n)`; the
  byte limb `/extract` carries cannot bind below 293 chunks and `max_extracted_characters` already
  bounds bytes ×4 — round 3 dropped it), and refuses over-budget pages with the **existing**
  `content_too_large` code and a new closed reason `promptguard_budget`. Honestly stated: today
  `/retrieve` passes no budget, so an over-budget page is classified in full and served; after
  US-001 it is refused. That is a new refusal condition on an accepting route. Its governance
  lane is written down, not waved off: `contract/GOVERNANCE.md`'s worked example 6 (`:150-167`)
  requires a security tightening to ship as an expedited MINOR with a stated compatibility window
  or to be a MAJOR. This one is an expedited MINOR whose compatibility mechanism is the knob
  (`retrieve.max_promptguard_chunks` up to 1024 ≈ 1.8 M characters) and whose window is
  explicitly **none**, for a reason the story records: the refused class — a page whose extracted
  text exceeds the classification budget — is a resource-exhaustion vector of the same kind the
  existing 10 MiB `content_too_large` refusal already closes, pages above the knob's ceiling
  cannot be served safely under any configuration, and keeping the old behaviour available would
  keep the vector open; the docstring line announces it (ruling (b)'s announcement obligation
  covers `promptguard_budget`, `busy` and `extraction_failed` — each story appends its own line
  and spec 8 US-002's record is the owner of the whole).
- **Parity by reuse: the controller, never the middleware (R23, corrected in round 3).**
  `ExtractionAdmissionController` (`retrieval_app.py:939-1007`) is a plain, path-free, status-free
  class that bounds active slots, queue depth and conservatively reserved queued bytes and counts
  saturation; every objection the first draft raised — path gating, the `route_enabled` 404, the
  429 — belongs to `ExtractionAdmissionMiddleware` (`:1080-1126`), a different class. `/retrieve`
  gets a **second controller instance** built from a `retrieve:` config block, acquired **before
  the fetch** with a bounded wait, released at the end of stage 1 **together with the fetched
  body**, and a refused admission is a 422 `busy` — the same closed literal `/extract` emits as a
  429. Two things round 3 pinned: the pipeline sees the controller only through a pipeline-side
  `AdmissionSlot` Protocol (`pipeline/` never imports `retrieval_app`; `SearchMetricsSink` is the
  precedent), and the app-wide `pipeline_error_handler` (`retrieval_app.py:1411-1426`) picks 429
  for `busy` by *code alone* today (`:1424`), so it becomes route-aware — `/extract` keeps 429,
  `/retrieve` answers 422 — with both halves tested (R42).
- **Three gates, three scopes.** The admission slot covers the fetch and stage 1 (HTML extraction or
  the PDF worker). The classification semaphore covers **stage 3 only**: `sanitize_and_structure`
  takes it as a parameter and acquires it around `run_promptguard`; the `/extract` file route keeps
  its outer `async with` at `:539` and passes `None` inward, so it never acquires twice (a
  double acquisition on a size-1 semaphore is a deadlock). Stages 2 and 4 run on the default
  executor bounded by neither gate — regex and parsing over ≤ 10 MB of already-admitted input.
- **A wait timeout is a policy event, not a crash (R22).** Every acquisition is `async with` a
  small context manager, the permit count is asserted unchanged after every failure path, and a
  timed-out wait follows the route's classifier-unavailable branch under the **effective**
  fail-closed policy: fail-closed → blocked / omitted, fail-open → served unscanned and marked —
  logged at the timeout site with its own closed token (`classification_wait_timeout`), because the
  classifier is loaded and busy, not absent, and the existing "PromptGuard unavailable" line would
  send an operator to the model loader. That second outcome is a load-triggerable route around the
  classifier on an unauthenticated service; it is named in Security Considerations, counted on
  `/metrics`, and closed by the operator floor (US-005, P1 for exactly that reason). Not a
  `/health` degraded reason — see Decisions Made. The `/extract` file route's own acquisition moves
  inward to the same stage-3 seam (its outer `async with` wrapped stages 2–4, and US-002's threading
  would have lengthened the permit hold) but stays untimed and uncounted, a recorded, one-directional
  coupling.
- **Fetched PDFs are spooled to a file and parsed by the worker (R23).** The worker takes a `Path`;
  a new bytes entry point in the un-hashed `pipeline/pdf_subprocess.py` owns a 0600 temp file in a
  process-private spool directory — `<tmpdir>/forage-spool-<uid>`, created 0700 at boot and
  verified owned-and-private (that construction, not a docs sentence, is what closes the child's
  re-open-by-path window and makes the tmpfs recommendation enforceable) — and its unlink. The worker surfaces **four** outcomes
  (`PDFEncryptedError`, `PDFNoTextError`, `PDFClassifiableTextLimitError`, `PDFExtractionError` —
  the child's IPC vocabulary at `:132-151` folds a page-limit hit, an rlimit kill and a wall-clock
  kill into the last), plus the spool write itself can fail. `RetrieveErrorCode` is a closed
  six-member Literal (`pipeline/contract.py:243-259`), so **`extraction_failed` joins it** inside
  the 1.3.0 window with closed reasons per outcome. Fetched PDFs run under `/extract`'s
  `ExtractionSettings` — including its 64-chunk classifiable-text ceiling — deliberately: untrusted
  binary parsed in a child whose rlimits were sized for that ceiling. A fetched HTML page therefore
  gets `retrieve.max_promptguard_chunks` and a fetched PDF gets `extraction.max_promptguard_chunks`;
  both refuse with `content_too_large` / `promptguard_budget`, and the docs say which ceiling each
  branch runs under.
- **Policy floors are resolved once, in the handler, by replacing the request (R10, R24).** The
  request's `promptguard_fail_closed` is read at three pipeline sites (`:258` fingerprint, `:373`
  sanitize, `:1019` search) and neither pipeline takes it as a parameter, so the handler hands the
  pipeline `body.model_copy(update=...)` carrying the effective values; the cache fingerprint picks
  them up for free, and the handler stamps `effective_promptguard_fail_closed` on every `/retrieve`
  and `/search` response and `effective_promptguard_threshold` on every `/retrieve` response — hit
  or miss — after the pipeline returns. `SearchRequest` has **no** `promptguard_threshold` field
  (`models.py:272-327`), so the `/search` half of the ceiling and `effective_promptguard_threshold`
  on `SearchResponse` belong to spec 3 US-005, which adds the field.

Wire changes in the 1.3.0 window from this spec: the `promptguard_budget` reason (US-001);
`retrieve.classification_wait_timeouts` and `search.classification_wait_timeouts` (US-006);
`retrieve.semaphore_saturation`, `retrieve.busy_rejections` and `busy` on `/retrieve` (422, reasons
`admission_queue_full` / `admission_wait_timeout`) (US-002); `extraction_failed` on `/retrieve`
with its reasons (US-003); `CacheMetrics.corrupt_entries` (US-004);
`effective_promptguard_fail_closed` on `RetrievedContent` and `SearchResponse`, and
`effective_promptguard_threshold` on `RetrievedContent` (US-005). Every one follows the R36 block
(docstring line, regenerate, golden via `_SCHEMA_MODELS`, `_EXPECTED_ONE_THREE_ZERO_DIFF`, anchor
pages, `--check`). The golden was created by spec 1 US-004; this spec runs after it per `depends_on`.

## Goals

- A fetched HTML page whose extracted text exceeds `max_extracted_characters(retrieve.
  max_promptguard_chunks)` characters is refused with 422 `content_too_large` / reason
  `promptguard_budget` before any inference, never a 500; a page under the budget classifies every
  window; `/extract`'s budget, output and tests are byte-for-byte unchanged.
- Two concurrent `/retrieve` classifications, a `/retrieve` and a `/search` classification, or an
  `/extract` file-route classification and either fetch route, never run at the same time
  (semaphore size 1 by default; the `/extract` bytes route `run_extract_pipeline` has no HTTP caller
  and stays unguarded); the permit count is identical before and after an over-budget refusal, a raised
  classifier, a timed-out wait and a cancellation; a waiter that exceeds `promptguard_wait_seconds`
  follows its route's classifier-unavailable branch under the effective fail-closed policy and is
  counted once per request; a cache hit never waits; with no classifier loaded nothing is acquired.
- During a slow fetched-page extraction (fake extractor blocking for 2 s), five sequential `/health`
  requests each complete in under 1 s (the shape of `tests/test_app.py::
  test_health_answers_while_the_fetch_is_in_flight`); at most `retrieve.fetch_concurrency` fetched
  bodies are alive during fetch and stage 1 and **none** while a request waits on classification
  (the body reference is dropped with the slot; a waiter holds at most the extracted text), a
  queued `/retrieve` holds no body, a request beyond the bounded queue is refused 422 `busy` within
  one event-loop turn, and a queued request that outlives `retrieve.admission_wait_seconds` is
  refused 422 `busy` / `admission_wait_timeout` — no wait in this spec is unbounded.
- A fetched PDF is parsed in the spawned worker under the same `child_cpu_seconds` /
  `child_address_space_bytes` / `wall_clock_seconds` limits as an uploaded one, from a 0600 temp
  file in the named spool directory that is gone on every exit path; every fetched-PDF failure
  (encrypted, no text, classifiable-text ceiling, extraction error incl. page limit / rlimit /
  wall-clock kill, spool I/O error) is a 422 with a closed reason; zero `/retrieve` 500s on the PDF
  branch.
- A cache value that fails to parse is served as a miss, deleted, and counted; zero `/retrieve`
  responses are 500 because of cache content.
- With `promptguard_fail_closed_floor: true`, a request carrying `promptguard_fail_closed: false`
  behaves as fail-closed on both fetch routes; with `promptguard_threshold_ceiling: 0.5`, a
  `/retrieve` request carrying `1.0` is classified at `0.5`; every `/retrieve` **200** response
  reports both effective values and every `/search` 200 response reports the effective fail-closed
  value, cache hit or miss (422 bodies are `Pipeline422ErrorResponse` and carry no policy fields);
  with the defaults (`false`, `1.0`), every existing test passes unchanged.

## User Stories

### US-001: `RetrieveSettings`, the new pipeline signature, and the pre-checked chunk budget on `/retrieve`

**Priority:** P1

**Description:** As an operator, I want fetched pages classified under a budget that refuses cleanly
instead of throwing, read from a `retrieve:` config block with the same boot validation the
`extraction:` block has, so one hostile page cannot burn unbounded CPU — and I want the pipeline's
new dependencies introduced once, so the sixteen test call sites are rewritten once.

**Independent Test:** Drive `run_retrieve_pipeline` with a patched `fetch_url` returning a page whose
extracted text is one character over `max_extracted_characters(256)` (458 752 characters; the
arithmetic is `(512 − 64) × n × 4`, `pipeline/extraction_limits.py:34-36`) and a real
`PromptGuardClassifier` instance whose `classify` is patched with a spy that raises `AssertionError`
if invoked; assert a `PipelineError` with `error == "content_too_large"` and `reason ==
"promptguard_budget"`. With a page exactly at the limit, assert it classifies. With a page under
the budget and a classifier double whose `classify` raises `PromptGuardBudgetExceededError`, assert
the same 422 (belt and braces). With a page under the budget, assert every window is classified and
the response is byte-identical to today's. Boot the app with `retrieve.max_promptguard_chunks:
2048` and assert the lifespan refuses with the closed-vocabulary message. (R41: every value above
was computed against the tree; there is no byte-limb case — see Decisions Made.)

**Implementation Hints:**
- New un-hashed module `pipeline/retrieve_limits.py` with a frozen `RetrieveSettings` dataclass and
  `retrieve_settings_from_config(config) -> RetrieveSettings`, modelled on
  `pipeline/extraction_limits.py` (`:48-58` dataclass, `:98-151` reader, `:100-102` the top-level-key
  read precedent). Keys, all in a new `config.yaml` `retrieve:` block: `max_promptguard_chunks` (default 256, range 1–1024), `fetch_concurrency` (default **1**, range
  1–8; consumed by US-002 — default 1 until spec 6 sizes it, because a fetched PDF spawns the same
  pypdf worker `/extract` does, so the worker population is `extraction.extraction_concurrency +
  retrieve.fetch_concurrency` against a memory reservation written for one worker), `admission_queue_depth`
  (default 4, range 0–16; US-002), `max_queued_fetch_bytes` (default 31457280 = 3 ×
  `DEFAULT_MAX_CONTENT_BYTES`, range 10485760–167772160 (16 ×); US-002 — the default is deliberately
  **not** `admission_queue_depth × 10 MB`, so the byte bound binds before the depth bound at the
  shipped defaults and both bounds are exercisable), `admission_wait_seconds` (float, default
  `30.0`, range 0.05–300.0; US-002). Plus two top-level keys read by the same function because it
  owns the boot-validated fetch-route policy (US-005 wires them; US-001 only reads and stores them
  at their defaults): `promptguard_fail_closed_floor` (bool, default `false`) and
  `promptguard_threshold_ceiling` (float, 0.0–1.0, default `1.0`), and one top-level key for both
  fetch routes (US-006 wires it): `promptguard_wait_seconds` (**float**, default `30.0`, range
  0.05–300.0 — a float so `sanitize_and_structure`'s `classification_wait_seconds: float | None`
  and US-006's sub-second test values are in range). Ranges: the `extraction:` block is bounded
  at its defaults on every key but two because its memory reservation assumes exactly one worker
  (`pipeline/extraction_limits.py:153-166`); the `retrieve:` keys are deliberately raisable
  (fetched bodies are 10 MB, not 50 MiB, and spec 6 owns sizing) — say so in the table preamble.
  Bound checking: there is no bounded-float helper in the repo and two private `_bounded_int`
  copies already exist (`pipeline/extraction_limits.py:79-96`, `cache.py:244-264`) — add
  `pipeline/config_bounds.py` with `bounded_int` and `bounded_float` taking the exception class as a
  parameter (the `isinstance(value, bool)` rejection from `:79-96` kept), used by
  `retrieve_limits.py` with a new `RetrieveConfigurationError(ValueError)`, and **migrate
  `pipeline/extraction_limits.py`'s copy onto it** in this story (same package, mechanical);
  `cache.py`'s copy stays with its recorded reason (`cache.py:253-257`: the dependency runs the
  other way, so importing from `pipeline` would close a loop) — two implementations, each with a
  written reason, not three. The shipped `config.yaml` gains the `retrieve:` block and the three
  top-level keys at their defaults with the same commented rationale the `extraction:` block
  carries (`config.yaml:34-48`), so the shipped file stays the complete example, and its
  `:39-41` worker-memory comment is rewritten to the combined ceiling `(extraction.
  extraction_concurrency + retrieve.fetch_concurrency) × child_address_space_bytes` plus the parent
  reservation, cross-referencing spec 6.
- Publish `app.state.retrieve_settings` in the lifespan beside `extraction_settings`
  (`retrieval_app.py:1213-1216`) with the module-level fallback the file route has (`:1378-1379`);
  the boot-refusal test follows `tests/test_app.py::test_lifespan_refuses_an_out_of_range_cache_bound`
  (`:1208`).
- **The whole new signature lands here (R40).** `run_retrieve_pipeline` gains, all **required**
  keyword-only: `settings: RetrieveSettings`, `retrieve_metrics: RetrieveMetricsSink`,
  `classification_semaphore: asyncio.Semaphore`, `admission: AdmissionSlot`,
  `extraction_settings: ExtractionSettings`. Required, exactly as `run_extract_pipeline_from_file`
  (`:485-500`) — a defaulted parameter would be the second, unbounded limits path this spec exists
  to prevent. `AdmissionSlot` is a consumer-side `Protocol` declared in `pipeline/orchestrator.py`
  beside `SearchMetricsSink` (`:747-757`) — `async def acquire(self) -> bool` / `def release(self)
  -> None` — which `retrieval_app.ExtractionAdmissionController` (`:939-1006`; its `acquire()` /
  `release()` are already exactly that shape and status-free) satisfies structurally. **`pipeline/`
  never imports `retrieval_app`**: `retrieval_app.py:65` imports the pipeline, nothing under
  `pipeline/` imports back (`grep -rn "retrieval_app" pipeline/` finds only `contract.py`'s
  docstring prose), and `SearchMetricsSink`'s docstring ("neither module imports the other") and
  `cache.py:253-257` both record the rule; a `TYPE_CHECKING` back-reference would be new to the
  repo and is not used. This story only *uses* `settings` and `retrieve_metrics`; US-006, US-002
  and US-003 wire behaviour to the rest, so the sixteen pre-existing call sites
  (`grep -c 'run_retrieve_pipeline(' tests/test_orchestrator.py` = 16 at story start; none
  elsewhere) are rewritten once. The `client` fixture (`tests/test_orchestrator.py:1470-1506`)
  already publishes `app.state.classification_semaphore` and `extraction_settings`; add
  `retrieve_settings`, `retrieve_metrics` and `retrieve_admission` beside them, and a shared helper
  that builds the five kwargs so every site — pre-existing and the ones this story adds — passes
  them through one line.
- The metrics seam is the one `/search` already has: declare a narrow `AdmissionMetrics(Protocol)`
  (`semaphore_saturation: int`, `busy_rejections: int` — the two counters the controller
  increments) and `RetrieveMetricsSink(AdmissionMetrics, Protocol)` adding
  `classification_wait_timeouts: int`, both beside `SearchMetricsSink`
  (`pipeline/orchestrator.py:747-757`), and `_NullRetrieveMetrics` beside `_NullSearchMetrics`
  (`:759-772`) — one Protocol for the two shared counters, not two (US-002's restructured
  controller takes `AdmissionMetrics`); `RetrieveMetrics` (`retrieval_app.py:903-935`) gains the three
  counters and satisfies it structurally; the `/retrieve` handler (`:1591-1604`) passes
  `retrieve_metrics=request.app.state.retrieve_metrics`. The counters are mirrored on
  `RetrieveMetricsResponse` (`:538`, `extra="forbid"`) by the stories that increment them (US-006,
  US-002), not here — this story adds the seam and the sink fields only.
- Pre-check, characters only: `if len(extraction.raw_text) > max_extracted_characters(settings.
  max_promptguard_chunks): raise PipelineError(error="content_too_large", reason=PROMPTGUARD_BUDGET,
  request_id=...)`; `max_extracted_characters` is `pipeline/extraction_limits.py:34-36`. `/extract`'s
  file route (`:531-536`) carries a second, byte limb through the derived
  `settings.max_extracted_output_bytes` (`extraction_limits.py:74-76`, `min(2 MiB, chars × 4)`);
  it is **not** copied here — at the default 256 chunks the character limb (458 752) admits at most
  1 835 008 UTF-8 bytes, under 2 MiB, so a byte limb could never bind below 293 chunks, and the
  character bound already bounds bytes ×4 (R23, corrected). Give `RetrieveSettings` a derived
  `max_extracted_characters` property mirroring `ExtractionSettings` (`:61-72`) so the call site
  reads the same way on both routes. Add `PROMPTGUARD_BUDGET = "promptguard_budget"` to `pipeline/contract.py` beside
  `POLICY_EXCLUDED_ALL_PROVIDERS` (`:318`) — token-shaped, the one existing reason-literal precedent,
  not the sentence-shaped `DOCUMENT_FAILURE_REASONS` (`orchestrator.py:111-122`). Pass
  `max_promptguard_chunks=settings.max_promptguard_chunks` at `:368-377` and catch
  `PromptGuardBudgetExceededError` (`promptguard/classifier.py:210`) around the
  `sanitize_and_structure` await, mapping it to the same 422 — the pre-check is the primary control,
  the catch is the backstop.
- Governance note for the docstring line: "`/retrieve` 422 `content_too_large` gains the reason
  `promptguard_budget`; a fetched page over `retrieve.max_promptguard_chunks` (default 256) is now
  refused rather than classified in full — an expedited security tightening (GOVERNANCE worked
  example 6) shipped with no compatibility window; operators who need larger pages raise the key
  (up to 1024)". Record the classification in `contract/GOVERNANCE.md`'s recorded rulings as the
  epic's ruling for new refusal conditions on accepting routes, stating the three reasons the
  Overview gives for choosing the expedited-MINOR lane over a MAJOR, and naming the two later
  refusals that fall under the same ruling (`busy` in US-002, `extraction_failed` in US-003) so the
  consumer note spec 8 carries lists all three.
- Docs: `docs/configuration.md` gains the `retrieve:` block table (`| Key | Default | Allowed range
  | Purpose |`) and the three top-level rows, each row saying which route it governs
  (`promptguard_wait_seconds`: both fetch routes); `docs/configuration.md:487-489` keep describing
  `extraction.max_promptguard_chunks` as `/extract`'s and fetched PDFs' (US-003) ceiling;
  `kit_tools/arch/SECURITY.md:143` gains the sentence that `/retrieve` pre-checks under
  `retrieve.max_promptguard_chunks` and refuses with `content_too_large` / `promptguard_budget`;
  `kit_tools/docs/API_GUIDE.md:197-199` and `kit_tools/docs/TROUBLESHOOTING.md:168`'s `/retrieve`
  422 table name the new reason. How an operator sets any of these in a deployed container is the
  bind-mount procedure spec 6 documents (`config.yaml` is copied into the image; there is no env
  override — say so in the table's preamble and cross-reference spec 6).
- Rotates `sanitizer_revision` (`orchestrator.py` — signature, pre-check, sink; `contract.py` —
  constant and docstring line), ruling 6 / R32: revert each in turn with a both-reverted control;
  the control baseline is the previous story's post-state (measure in execution order).

**Acceptance Criteria:**
- [ ] `pipeline/retrieve_limits.py` defines `RetrieveSettings` (with a derived
      `max_extracted_characters` property), `RetrieveConfigurationError` and
      `retrieve_settings_from_config` with the eight keys, defaults and ranges above (`fetch_concurrency`
      default 1; `promptguard_wait_seconds` and `admission_wait_seconds` floats, range 0.05–300.0);
      `pipeline/config_bounds.py` provides `bounded_int` and `bounded_float` and
      `pipeline/extraction_limits.py` uses them (its private copy is gone; `cache.py`'s stays with
      its comment); an out-of-range or wrong-type value for any key refuses boot with a
      closed-vocabulary message (a parametrised test per key, modelled on `tests/test_app.py:1208`);
      the lifespan publishes `app.state.retrieve_settings` and the module-level fallback exists; the
      shipped `config.yaml` carries the block and the three keys at their defaults and its
      worker-memory comment states the combined worker ceiling.
- [ ] `run_retrieve_pipeline` takes the five new required keyword-only parameters, `admission` typed
      as the pipeline-side `AdmissionSlot` Protocol; `AdmissionMetrics`, `RetrieveMetricsSink` and
      `_NullRetrieveMetrics` exist beside the search sinks; `RetrieveMetrics` carries the three new
      counters (not yet mirrored); the handler passes all five; no module under `pipeline/` imports
      `retrieval_app` (`grep -rn "^from retrieval_app\|^import retrieval_app" pipeline/` is empty);
      all 16 pre-existing `run_retrieve_pipeline(` sites in `tests/test_orchestrator.py` (count
      recorded at story start) and every site this story adds pass the kwargs through one shared
      helper — no site passes them inline; a test asserts the kwargs reach the pipeline.
- [ ] A fetched page one character over `max_extracted_characters(retrieve.max_promptguard_chunks)`
      is refused with 422 `content_too_large` / `promptguard_budget` before `classify` is called
      (spy-that-raises on a real classifier); a page exactly at the limit classifies; a classifier
      that raises `PromptGuardBudgetExceededError` yields the same 422; a page under the budget
      classifies every window with a byte-identical response; the `/extract` file route's call and
      tests are unchanged.
- [ ] `PROMPTGUARD_BUDGET` is a constant in `pipeline/contract.py` beside
      `POLICY_EXCLUDED_ALL_PROVIDERS`; the governance ruling (expedited security-tightening MINOR,
      no compatibility window, the three reasons, the two later refusals it also covers) is
      recorded in `contract/GOVERNANCE.md`'s recorded rulings.
- [ ] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; the reason recorded in `_EXPECTED_ONE_THREE_ZERO_DIFF` in
      `tests/test_contract_schema.py`; the four anchor-quoting pages refreshed;
      `uv run python -m scripts.export_contract --check` green.
- [ ] `grep -n 'promptguard_budget' docs/configuration.md kit_tools/arch/SECURITY.md
      kit_tools/docs/API_GUIDE.md kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per
      file (R39), and `docs/configuration.md`'s `retrieve:` table preamble names the bind-mount
      procedure and spec 6.
- [ ] `sanitizer_revision` rotation measured (revert `orchestrator.py` and `contract.py` each in
      turn, both-reverted control — the `orchestrator.py` measurement is whole-file and attributes
      the signature, the pre-check and the sink to one rotation; accepted, and stated in the
      record) and recorded at the five sites: `docs/bootstrap-notes.md`, `CLAUDE.md`,
      `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md` (divergence table),
      `kit_tools/arch/CODE_ARCH.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-006: The classification semaphore on `/retrieve` and `/search` — bounded wait, permit discipline, wait counters

**Priority:** P1

**Description:** As an operator, I want both fetch routes classifying under the same concurrency
gate as uploads, with a bounded wait that can never strand the permit and a timeout that follows the
route's existing classifier-unavailable policy, so one request cannot run inference in parallel with
another, queue forever, or take the only classification slot down with it.

**Independent Test:** Start two `/retrieve` calls against a fake classifier whose `classify` blocks
on a `threading.Event`; assert the second call's `classify` has not started while the first holds
the semaphore and completes once the event is set; repeat with one `/retrieve` and one `/search`,
and with an `/extract` file-route request holding the permit while a `/retrieve` waits. With
`promptguard_wait_seconds: 0.1` (a float inside the declared 0.05–300.0 range) and the permit held:
a fail-closed `/retrieve` returns `promptguard_state == "unavailable_blocked"`, a fail-open one
`"unavailable_allowed"`, exactly one WARNING whose message contains `classification_wait_timeout
route=retrieve` is emitted, and `retrieve.classification_wait_timeouts == 1` on `/metrics`; a ten-result `/search` whose budget
expires on result 3 returns two classified results, omits eight under `promptguard_unavailable`
(fail-closed) or serves them with `suspicious=True` and `promptguard_unavailable: true`
(fail-open), and `search.classification_wait_timeouts == 1` — once per request, not per result.
Drive the over-budget refusal, the backstop raise, a timed-out wait and a cancelled wait N+1 = 5
times each against a size-1 semaphore and assert the next request still classifies (permit count
unchanged). With `classifier is None`, assert the semaphore is never acquired and the counter stays
at zero. A cache hit is served without acquiring.

**Implementation Hints:**
- One async context manager in `pipeline/orchestrator.py` — `_bounded_permit(semaphore, seconds)`
  yielding `True` when acquired, `False` on `TimeoutError` — built on `async with
  asyncio.timeout(seconds): await semaphore.acquire()` inside a `try`, with the release in
  `finally` **only when acquired** (a timeout cancels the `acquire()` before it succeeds; a late grant
  after cancellation is the case the "timeout-then-late-grant" test pins). Never a bare `acquire()`
  at a call site.
- Scope: stage 3 only, on all three routes. `sanitize_and_structure` (`:160-176`) gains
  `classification_semaphore: asyncio.Semaphore | None = None` and `classification_wait_seconds:
  float | None = None` and wraps its `run_promptguard` call (`:23` of the body) in `_bounded_permit`
  when given one (`None` seconds = wait without a deadline); on `False` it proceeds exactly as
  `classifier is None` (the existing `promptguard_state` `unavailable_blocked` /
  `unavailable_allowed` derivation in `pipeline/stage4_structuring.py:96-119`;
  `tests/test_orchestrator.py:618,653`) **and** logs one WARNING at the timeout site with the closed
  token `classification_wait_timeout route=<retrieve|search>` and nothing caller-derived — the
  classifier is loaded and busy, not absent, and the existing "PromptGuard unavailable" lines
  (`stage3_promptguard.py:96`, `:111`) would send an operator to the model loader. `/retrieve`
  passes its semaphore and `settings.promptguard_wait_seconds` (the top-level key, read into
  `RetrieveSettings` by US-001's reader). The `/extract` file route **moves its acquisition inward
  too**: the outer `async with classification_semaphore:` at `:539` wrapped stages 2, 3 and 4, and
  US-002's threading of stages 2 and 4 would lengthen that permit hold; it now passes the semaphore
  and `classification_wait_seconds=None` into `sanitize_and_structure` (stage 3 only, still untimed
  and uncounted — never both an outer and an inner acquisition: double acquisition on size 1
  deadlocks). `run_extract_pipeline` (bytes route, `:422`) has no HTTP caller, acquires nothing
  today and stays that way. The cache read (`:286-300`, absolute) stays outside every gate.
- `/search` (R16, R38): `run_search_pipeline` gains `classification_semaphore: asyncio.Semaphore |
  None = None` and `classification_wait_seconds: float | None = None` — **defaulted**, matching
  every other collaborator on that signature (`:775-782`; `search_metrics` at `:781` is the
  precedent), so the 73 test call sites (`grep -c 'run_search_pipeline(' tests/test_orchestrator.py`
  = 47, `tests/test_search_providers.py` = 14, `tests/test_brave_provider.py` = 12) do not change;
  the handler (`:1811-1817`) always passes both. One **deadline** per request, not one timeout
  scope: compute `deadline = loop.time() + classification_wait_seconds` before the result loop and,
  at each per-result acquisition (`:1014`), call `_bounded_permit(semaphore, max(0.0, deadline -
  loop.time()))` — `asyncio.timeout` appears in exactly one place, inside `_bounded_permit`, scoped to
  a single `acquire()`, and never around the result loop (an `asyncio.timeout` around the loop would
  raise `TimeoutError` out of whatever is awaiting and unwind the loop, the opposite of the stated
  behaviour); once the deadline has passed, that result and every remaining one take the
  classifier-unavailable branch at
  `:1012-1046` under the **effective** fail-closed value — fail-closed → omitted
  `OMIT_PROMPTGUARD_UNAVAILABLE` (`:1028-1029`); fail-open → served unscanned with
  `suspicious=True`, `unscanned_results += 1`, `promptguard_unavailable = True` (`:1043`) — and
  `search_metrics.classification_wait_timeouts += 1` once. `SearchMetricsSink` and
  `_NullSearchMetrics` (`:747-772`) gain the field; `SearchMetrics` (`retrieval_app.py:875`) and
  `SearchMetricsResponse` (`:488`) mirror it. Until US-005 lands, the effective value is the request
  value (the floor defaults `false`); US-005's `model_copy` makes it the floored value with no change
  here.
- With `classifier is None` no route touches the semaphore (the `unavailable_*` path is unchanged
  and the counter stays zero). The three routes now serialise behind `classification_concurrency`
  (`config.yaml:46`, size 1) — `/extract`'s admission bound no longer bounds its classification wait
  when fetch traffic holds the permit, and `/extract`'s acquisition has no timeout and no counter:
  unauthenticated `/retrieve` and `/search` traffic can block `/extract`'s classification for an
  unbounded time. That one-directional coupling is accepted, recorded (Decisions Made), and given
  its signal in prose: `SECURITY.md` and `MONITORING.md` state the direction and that a hanging
  `/extract` under fetch load has no counter of its own (`extract.route_enabled` ships `false`, so
  the exposure is latent until an operator turns the route on); spec 6 widens the knob. The
  interim posture before US-005 lands is also stated: the request default is
  `promptguard_fail_closed: true`, so the load-triggerable fail-open outcome reaches only callers
  that explicitly opt into fail-open.
- Counters: `retrieve.classification_wait_timeouts` (`RetrieveMetrics` field from US-001, now
  mirrored on `RetrieveMetricsResponse` `:538`) and `search.classification_wait_timeouts` — window
  additions; the `/metrics` order guards are
  `tests/test_contract_metrics.py::test_served_metrics_are_the_handlers_dict_serialized` (`:135`) and
  `test_metrics_mirror_round_trips_the_served_body` (`:161`).
- Tests: `tests/test_orchestrator.py::test_retrieve_full_pipeline_happy_path` (`:244`),
  `::test_post_retrieve_endpoint` (`:1523`), the `MagicMock(spec=PromptGuardClassifier)` idiom
  (`:1489`) for the blocking double; the `/search` cases beside the `promptguard_unavailable`
  fixtures in `tests/test_orchestrator.py` (grep `OMIT_PROMPTGUARD_UNAVAILABLE`); the `/extract`
  contention case beside `tests/test_app.py`'s file-route tests. R40: `grep -c 'sanitize_and_structure('
  tests/*.py` is **0** today — no test calls it directly, so the two defaulted parameters are
  exercised only through `run_retrieve_pipeline`, the `/extract` file route
  (`grep -c 'run_extract_pipeline_from_file(' tests/test_app.py` recorded at story start) and the
  `@patch("pipeline.orchestrator.run_promptguard")` sites (`tests/test_orchestrator.py:242-243`
  idiom); add direct-call coverage of `sanitize_and_structure` with and without a semaphore here.
- Docs: `docs/configuration.md:487-489` say `extraction.classification_concurrency` now sizes all
  three routes and `promptguard_wait_seconds` bounds the two fetch routes' wait, with the sizing
  rule the two defaults imply: `promptguard_wait_seconds` must exceed the worst-case single permit
  hold, which is `retrieve.max_promptguard_chunks` (256) × the per-window classify latency on the
  operator's CPU (spec 7 US-004 fills the per-window number for the reference envelope) — a wait
  timeout under ordinary mixed traffic means the permit holder exceeded the wait, and the fix is to
  raise `promptguard_wait_seconds` or lower `retrieve.max_promptguard_chunks`;
  `kit_tools/arch/SECURITY.md:196-198`'s admission table already claims the classification semaphore
  covers "all routes" — a pre-existing inaccuracy this story makes true — and gains the sentence
  under Security Considerations below; `MONITORING.md` gains both counter rows and the head-of-line
  note (a 256-chunk `/retrieve` holds the single permit longer than any `/search` snippet; a
  wait-timeout spike under mixed traffic correlates with large fetched pages); `TROUBLESHOOTING.md`
  cross-references the counters from the `promptguard_state` `unavailable_*` row.
- Rotates `sanitizer_revision` (`orchestrator.py`, `contract.py` docstring line), ruling 6 / R32.

**Acceptance Criteria:**
- [ ] Every acquisition on all three routes goes through `_bounded_permit`: `grep -n
      'semaphore.acquire()' pipeline/orchestrator.py` returns exactly one match and it is inside
      `_bounded_permit` (a test monkeypatches `_bounded_permit` and shows every acquisition on
      `/retrieve`, `/search` and the `/extract` file route passes through it); `grep -c 'async with
      classification_semaphore' pipeline/orchestrator.py` is 0 (the outer `/extract` acquisition at
      `:539` is gone); the permit count is unchanged after an over-budget refusal, a backstop raise,
      a timed-out wait, a cancelled wait and a late grant (the N+1 tests above).
- [ ] The `/extract` file route acquires exactly once, inside `sanitize_and_structure`, with no
      deadline; a test drives the file route under a size-1 semaphore and completes; `/extract`
      output is unchanged (existing tests pass without edits).
- [ ] A timed-out wait logs exactly one WARNING containing `classification_wait_timeout
      route=<retrieve|search>` and nothing caller-derived (sentinel assertion); the existing
      "PromptGuard unavailable" line is **not** emitted for a timeout; `TROUBLESHOOTING.md`'s
      `unavailable_*` row distinguishes classifier-absent from permit-contention by that line.
- [ ] Two concurrent classifications (`/retrieve`+`/retrieve`, `/retrieve`+`/search`,
      `/extract`+`/retrieve`) serialise through the shared semaphore; a cache hit is served without
      acquiring; with `classifier is None` the semaphore is never acquired and the counters stay zero.
- [ ] A `/retrieve` waiter exceeding `promptguard_wait_seconds` reports `unavailable_blocked`
      (effective fail-closed) or `unavailable_allowed` (effective fail-open), never a 500, and
      `retrieve.classification_wait_timeouts` increments once; `/search` spends one wait budget per
      request, and after it expires every remaining result follows the classifier-unavailable branch
      under the effective policy (omitted, or served marked) with
      `search.classification_wait_timeouts` incremented once per request; the Independent Test's
      3-of-10 case asserts `len(results)`, `omitted_by_reason["promptguard_unavailable"]` and the
      fail-open `suspicious` / `promptguard_unavailable` shape.
- [ ] `run_search_pipeline`'s two new parameters are defaulted, so no pre-existing call site is
      edited: the story's diff touches none of the 47 / 14 / 12 existing `run_search_pipeline(`
      sites in `tests/test_orchestrator.py`, `tests/test_search_providers.py` and
      `tests/test_brave_provider.py` (asserted by reviewing the diff; sites this story's own tests
      add are expected); one deadline per request is computed from `loop.time()` and
      `asyncio.timeout` occurs exactly once in `pipeline/orchestrator.py` (inside
      `_bounded_permit`); the handler passes both; `SearchMetricsSink`, `_NullSearchMetrics`,
      `SearchMetrics` and `SearchMetricsResponse` carry `classification_wait_timeouts`; both
      counters appear on `/metrics`.
- [ ] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; both counters appended to `_EXPECTED_ONE_THREE_ZERO_DIFF` in
      `tests/test_contract_schema.py`; the four anchor-quoting pages refreshed;
      `uv run python -m scripts.export_contract --check` green.
- [ ] `grep -n 'classification_wait_timeouts' docs/configuration.md kit_tools/arch/SECURITY.md
      kit_tools/docs/MONITORING.md kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per
      file; `SECURITY.md` carries the Security Considerations sentence about a saturated semaphore
      and fail-open requests, the interim-posture sentence, and the one-directional `/extract`
      coupling sentence (`grep -n 'no wait timeout' kit_tools/arch/SECURITY.md kit_tools/docs/MONITORING.md`
      hits both); `docs/configuration.md` carries the `promptguard_wait_seconds` /
      `retrieve.max_promptguard_chunks` sizing rule.
- [ ] `sanitizer_revision` rotation measured (revert `orchestrator.py` and `contract.py` each in
      turn, both-reverted control) and recorded at the five sites.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Stages 1, 2 and 4 off the event loop, with fetch and stage 1 under a bounded `/retrieve` admission controller

**Priority:** P1

**Description:** As an operator, I want HTML extraction, the structural scan and the structuring
pass to run off the event loop, with `/retrieve`'s fetch-and-extract work bounded by a second
admission controller that queues without holding bodies, waits no longer than a configured bound,
refuses beyond a bounded queue with a 422 the route already declares, and lets go of the fetched body
before the classification wait, so a pathological page cannot stall `/health` and N slow pages cannot
hold N × 10 MB in memory.

**Independent Test:** Using `tests/test_app.py::test_health_answers_while_the_fetch_is_in_flight`
(`:958`) as the model — `_running_app(cache_connected=True)`, a `threading.Event`-gated blocking
`extract_html`, five sequential `/health` GETs — assert `max(latencies) < 1.0` while the extraction
is provably still in flight; with `retrieve.fetch_concurrency: 1`, `admission_queue_depth: 1` and
one extraction blocked, assert a second `/retrieve` has not called `fetch_url` while queued, that a
third is refused **422** `busy` / `admission_queue_full` within one loop turn with a
`Pipeline422ErrorResponse` body (`{error, reason, request_id}`, no `sanitizer_revision`), and that
`retrieve.semaphore_saturation == 2` and `retrieve.busy_rejections == 1` on `/metrics`; with
`max_queued_fetch_bytes: 10485760` and depth 4, assert the byte bound refuses the second queued
request; with `admission_wait_seconds: 0.1`, assert a queued request is refused 422 `busy` /
`admission_wait_timeout` and the controller's waiter list no longer references it; assert the
patched `extract_html`, `scan_structural` and `structure_sanitization_result` each run on a
non-event-loop thread; with the fake fetch returning a body that is a `bytearray` subclass (weak-
referenceable) and the classification permit held by another request, assert the weak reference to
the body is dead while the request waits on the permit; drive `/extract`'s middleware refusal and
assert it is still 429 with a byte-identical `RateLimit429Response`; `/extract` responses on the
existing fixture corpus are byte-identical.

**Implementation Hints:**
- Sync call sites: `extract_html` at `pipeline/orchestrator.py:355`, `scan_structural` at `:174`
  and `structure_sanitization_result` at `:191` (both inside `sanitize_and_structure`, shared with
  `/extract` — threading them there benefits both routes; keep `/extract` output identical). Use
  `asyncio.to_thread` as stage 3 already does at `pipeline/stage3_promptguard.py:130`. `extract_pdf`
  at `:352` is US-003's (it moves to the worker). `/search`'s own synchronous sites (the per-result
  `scan_structural` at `:997`, bounded fields of ≤ 2 000 characters) stay on the loop — Out of Scope.
- The gate is a second `ExtractionAdmissionController` (`retrieval_app.py:939-1007`) — read it
  before rebuilding anything: it bounds active slots, queue depth and reserved queued bytes, counts
  `semaphore_saturation` on every acquisition that found no free slot and `busy_rejections` when
  the queue is full, and its `acquire() -> bool` / `release()` are status-free. Its `__init__`
  (`:942-956`) takes `(settings: ExtractionSettings, metrics: ExtractionMetrics)` and reads four
  fields off the settings object, so **restructure `__init__` to take the five primitives**
  (`limit`, `queue_depth`, `max_queued_bytes`, `reservation_bytes`, `metrics: AdmissionMetrics` —
  the narrow Protocol US-001 declared) and add a `from_settings(settings, metrics)` classmethod
  for `/extract`'s construction site (which moves by one line; the criterion is that `/extract`'s
  admission *behaviour* is unchanged — its tests pass without edits — not that the line is). Build
  `app.state.retrieve_admission = ExtractionAdmissionController(limit=retrieve_settings.
  fetch_concurrency, queue_depth=…admission_queue_depth, max_queued_bytes=…max_queued_fetch_bytes,
  reservation_bytes=DEFAULT_MAX_CONTENT_BYTES (`pipeline/stage5_url_audit.py:30`),
  metrics=retrieve_metrics)` in the lifespan plus the module-level fallback; `run_retrieve_pipeline`
  already takes it as `admission: AdmissionSlot` (US-001) and never names the concrete class.
- Acquire **after the cache read and before the fetch** (a hit never waits; a waiter holds no body),
  **with a bound**: `acquire()` awaits its waiter future with no timeout (`retrieval_app.py:971-996`
  was written for a middleware whose request lifecycle was the bound), so wrap the acquisition in
  `asyncio.timeout(settings.admission_wait_seconds)`; on expiry the controller must **dequeue the
  cancelled waiter** (check the future's cancellation path in `acquire()` and add the removal if it
  is missing — a test asserts the waiter list is empty after a timed-out wait) and the request is
  refused `PipelineError(error="busy", reason=RETRIEVE_ADMISSION_WAIT_TIMEOUT)`, counted under
  `busy_rejections`. Hold the slot through stage 1 (HTML extraction, or US-003's PDF worker), then
  release in `finally` — every path, including a fetch error, an extraction exception and
  cancellation — and **release the body with the slot**: destructure the three post-stage-1 fields
  (`final_url`, `redirect_chain`, `domain_changed_on_redirect`, read at `:379-396`) into locals
  before the release, `del fetch_result` (and the `response_body` local), and change
  `build_retrieved_content` to take the extracted text and those three values rather than the
  `fetch_result` object, so a request parked on the classification permit (US-006, up to
  `promptguard_wait_seconds`) holds at most the extracted text (≤ `max_extracted_characters(
  retrieve.max_promptguard_chunks)` characters). `acquire()` returning `False` (queue full in depth
  or bytes) raises `PipelineError(error="busy", reason=RETRIEVE_ADMISSION_QUEUE_FULL)`.
- **The status is route-aware (R23 corrected, R42).** `busy` is the existing `RateLimit429ErrorCode`
  literal (`pipeline/contract.py:225`) and the app-wide `pipeline_error_handler`
  (`retrieval_app.py:1411-1426`) picks its status **by code alone** — `status_code=429 if
  exc.error == "busy" else 422` at `:1424` — so a `PipelineError(error="busy")` raised inside
  `run_retrieve_pipeline` would leave `/retrieve` as a 429, the new observable status this story
  must not add (GOVERNANCE: a new status on a route is MAJOR); today nothing raises that error (the
  `/extract` 429 is minted by the middleware at `:1112`), so that branch is dead code until this
  story. Make the handler route-aware: `429 if exc.error == "busy" and request.url.path ==
  "/extract" else 422`, and declare it per route — `busy` joins `RetrieveErrorCode` here (this story
  runs before US-003, which adds `extraction_failed`) with a docstring line saying the literal is
  intentionally the same as `/extract`'s 429 `busy` and answers 422 on `/retrieve`, in the comment
  shape of `contract.py:109-111`; `tests/test_contract_errors.py:567` (which pins `"/retrieve":
  ["200", "422"]` from the *generated declaration*, not runtime) gains a runtime assertion per
  route: `/retrieve`'s admission refusal is 422 with the `Pipeline422ErrorResponse` shape and
  `/extract`'s middleware refusal is 429 with a byte-identical `RateLimit429Response`. Rewrite the
  three sites that state the old premise: `Extract422ErrorCode`'s docstring (`contract.py:196-206`,
  "the handler answers it 429, not 422"), `RateLimit429ErrorCode`'s (`:211-213`, `:225`, "the single
  code `ExtractionAdmissionMiddleware` emits, at 429") and `tests/test_contract_errors.py:271-273`'s
  comment. `retrieve.busy_rejections`' description must **not** copy `extraction.busy_rejections`'
  "Requests refused with 429" wording (`contract/openapi.yaml:327-329`); it says "refused 422
  `busy`". Reason literals `RETRIEVE_ADMISSION_QUEUE_FULL = "admission_queue_full"` and
  `RETRIEVE_ADMISSION_WAIT_TIMEOUT = "admission_wait_timeout"` sit beside `PROMPTGUARD_BUDGET`.
- **The memory sentence, honestly.** At most `retrieve.fetch_concurrency` bodies are alive during
  fetch and stage 1; a queued request holds nothing; the queue is bounded in depth and reserved
  bytes; a request waiting on the classification permit holds only its extracted text. What is
  **not** bounded is the population of classification waiters: `uvicorn` runs with no
  `--limit-concurrency` (`Dockerfile:227`) and both middlewares are `/extract`-gated, so admission
  bounds the rate through stage 1, not the number of requests past it; with bodies released, each
  such waiter costs at most `max_extracted_characters(256)` ≈ 459 KB of text for at most
  `promptguard_wait_seconds`. Document exactly that in the `retrieve:` table (`fetch_concurrency ×
  10 MB` for bodies, plus `arrival rate × promptguard_wait_seconds × ≤ 0.5 MB` for waiters), name
  `--limit-concurrency` as the envelope knob spec 6 owns, and add the measurement as a criterion
  (below). `asyncio.to_thread` is uncancellable and the default executor is shared with stage 3
  (`min(32, cpu + 4)` threads); the slot is what keeps hostile pages from starving the classifier.
  A wall clock on HTML extraction is deliberately **not** added (the 30 s fetch timeout and 10 MB
  cap bound the input); spec 6 sizes the slot. Worst-case queue latency is a derived number in the
  table: `min(admission_wait_seconds, admission_queue_depth / fetch_concurrency × max(fetch timeout
  30 s, PDF wall clock))`.
- Contract-test edits for the `busy` member: `tests/test_contract_errors.py:241` (6 → 7 here, 7 → 8
  after US-003), the intersection assertion and its `10 + 6 + 3` comment at `:244-247`
  (`{"content_too_large"} == EXTRACT & RETRIEVE` gains `busy`), `tests/test_contract_schema.py:339`'s
  pin (renamed from `…_nine_members` to its new count), `RetrieveErrorCode`'s docstring
  (`contract.py:250-258`), and the three old-premise sites above. The second refusal this story adds
  (load-triggered `busy` on traffic served today) is recorded under US-001's GOVERNANCE ruling for
  new refusal conditions, with `retrieve.fetch_concurrency`, `admission_queue_depth` and
  `admission_wait_seconds` as the compatibility knobs, and named in the consumer note spec 8 carries.
- Tests: the responsiveness test mirrors `tests/test_app.py:958-993` and its `_running_app` (`:797`)
  / `_blocking_acquisition` (`:898`) helpers (connected cache required — its docstring says why); a
  thread-identity test (`threading.get_ident()` differs from the loop's) for all three threaded
  calls; the controller tests reuse the shapes in `tests/test_app.py` that already cover
  `ExtractionAdmissionController` for `/extract` (grep `semaphore_saturation`); the body-release
  test uses a `bytearray` subclass body (weak-referenceable; `bytes` is not) and asserts the
  `weakref` is dead while the request is parked on a held permit.
- Docs: `kit_tools/arch/CODE_ARCH.md`'s pipeline narrative says stages 1, 2 and 4 run off the loop
  on both routes; `kit_tools/arch/SECURITY.md:196-198`'s admission table row "`/retrieve`,
  `/search` … none | not applicable" is **edited** (not a row added): `/retrieve` is bounded by a
  second admission controller (`retrieve.fetch_concurrency`, queue depth, queued bytes, a bounded
  wait) with a 422 `busy` refusal, and the waiter-population sentence; `SECURITY.md:50` (the two
  middlewares gate on `/extract`) stays true; `docs/configuration.md` `retrieve:` table gains the
  four admission rows, the honest memory sentence, the queue-latency number and the disk note;
  `MONITORING.md` gains `retrieve.semaphore_saturation` / `busy_rejections` rows; `API_GUIDE.md` and
  `TROUBLESHOOTING.md`'s `/retrieve` 422 tables gain the `busy` row with both reasons.
- Rotates `sanitizer_revision` (`orchestrator.py`, `contract.py`), ruling 6 / R32.

**Acceptance Criteria:**
- [ ] `extract_html`, `scan_structural` and `structure_sanitization_result` are invoked via
      `asyncio.to_thread` on both routes; no synchronous call to them remains in
      `run_retrieve_pipeline` or `sanitize_and_structure`; a test asserts each runs on a
      non-event-loop thread.
- [ ] Five sequential `/health` requests each complete in under 1 s while a fetched-page
      `extract_html` is blocked (the `:958` shape, connected cache).
- [ ] `ExtractionAdmissionController.__init__` takes the five primitives with `metrics:
      AdmissionMetrics`, `from_settings` builds `/extract`'s instance, and `/extract`'s admission
      behaviour is unchanged (its middleware and tests pass without edits); `/retrieve` acquires
      `app.state.retrieve_admission` after the cache read and before the fetch under
      `admission_wait_seconds`, releases it on every path (success, fetch error, extraction
      exception, cancellation — each tested), and a timed-out waiter is removed from the controller's
      queue; a queued `/retrieve` has not fetched; a request beyond the depth bound and one beyond
      the byte bound (under `max_queued_fetch_bytes: 10485760`, depth 4) are each refused 422 `busy`
      / `admission_queue_full` within one loop turn; a wait past the bound is refused 422 `busy` /
      `admission_wait_timeout`; `retrieve.semaphore_saturation` and `retrieve.busy_rejections`
      increment and appear on `/metrics`.
- [ ] The fetched body is released with the slot: `build_retrieved_content` takes the extracted text
      and the three post-stage-1 fields, not `fetch_result`; a weak reference to a `bytearray`-subclass
      body is dead while the request waits on a held classification permit; the `retrieve:` table
      states the two-term memory sentence and names `--limit-concurrency` as spec 6's knob.
- [ ] `pipeline_error_handler` is route-aware: `/retrieve`'s admission refusal is 422 with a
      `Pipeline422ErrorResponse` body and no `sanitizer_revision`; `/extract`'s middleware refusal is
      429 with a byte-identical `RateLimit429Response`; `tests/test_contract_errors.py:567` asserts
      both at runtime; the three old-premise docstrings/comments are rewritten.
- [ ] `busy` is a member of `RetrieveErrorCode`; `RETRIEVE_ADMISSION_QUEUE_FULL` and
      `RETRIEVE_ADMISSION_WAIT_TIMEOUT` are constants in `pipeline/contract.py`;
      `tests/test_contract_errors.py:241` and `:244-247` (and its comment),
      `tests/test_contract_schema.py:339`'s pin and `RetrieveErrorCode`'s docstring are updated; the
      deduplicated `ERROR_CODES` total stays eighteen (`:234`); `retrieve.busy_rejections`'
      description does not say 429; the refusal is recorded under the GOVERNANCE new-refusal ruling
      with its three knobs.
- [ ] `/extract` responses on the existing fixture corpus are byte-identical before and after
      (existing tests unchanged).
- [ ] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; the two counters and the `busy` member appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four anchor-quoting pages refreshed; `--check` green.
- [ ] `grep -n 'busy_rejections\|admission_queue_full\|admission_wait_timeout' docs/configuration.md
      kit_tools/arch/SECURITY.md kit_tools/docs/MONITORING.md kit_tools/docs/API_GUIDE.md
      kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per file; the `SECURITY.md:198`
      row for `/retrieve` no longer reads "none | not applicable"; the queue-latency number is in
      the `retrieve:` table.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce) and recorded at the five sites.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Fetched PDFs parsed in the rlimited worker, with a typed `/retrieve` failure vocabulary

**Priority:** P1

**Description:** As an operator, I want fetched PDFs parsed in the same spawned, rlimited worker
uploads use — from a temp file in a named spool directory that never outlives the request — and
every PDF failure, including a failed spool write, to be a coded 422 instead of the 500 it is today,
so a hostile PDF can neither stall the service nor crash the request.

**Independent Test:** With `fetch_url` patched to return `application/pdf` bytes and
`extract_pdf_in_subprocess` patched to record its arguments, assert it is called with a `Path` under
`pipeline.pdf_subprocess.spool_dir()` (the process-private `forage-spool-<uid>` directory) and
`app.state.extraction_settings`, that the file has mode `0600` while the worker runs, that the
lifespan created the spool directory with mode `0700` owned by the process and refuses boot when
the directory exists with other permissions or another owner, and that no `forage-retrieve-*` file
remains under the spool directory after a success, after each of the four worker outcomes, after a simulated rlimit kill, after
`tempfile.NamedTemporaryFile` raising `OSError`, and after `asyncio.CancelledError` raised inside the
worker call; for each row of the table assert a 422 with the stated `error` and `reason`; a
fetched-PDF `/retrieve` with `extract_route_enabled` at its default `false` is 200; a fetched PDF
whose text exceeds `max_extracted_characters(extraction.max_promptguard_chunks)` is refused
`content_too_large` / `promptguard_budget` while the same text as HTML under
`retrieve.max_promptguard_chunks` is served.

| Worker outcome | `error` | `reason` |
|---|---|---|
| `PDFEncryptedError` | `extraction_failed` | `pdf_encrypted` |
| `PDFNoTextError` | `extraction_failed` | `pdf_no_text` |
| `PDFClassifiableTextLimitError` | `content_too_large` | `promptguard_budget` (US-001's reason; the worker's own 64-chunk ceiling) |
| `PDFExtractionError` (page limit, rlimit kill, wall-clock kill, corrupt parse — the child's `failed` status) | `extraction_failed` | `pdf_extraction_error` |
| `OSError` from the spool write (ENOSPC, EACCES, read-only or missing temp dir) | `extraction_failed` | `pdf_spool_error` |

**Implementation Hints:**
- The worker takes a `Path` (`pipeline/pdf_subprocess.py:156-159`; the child opens the file itself
  at `:55` inside `_extract_pdf_path` (`:51`) — `:173` is the parent's `receiver.recv_bytes`).
  `/retrieve` holds bytes (`pipeline/stage5_url_audit.py:221`, `orchestrator.py:352`), already
  fully buffered in the parent (the rationale for spooling is reuse of the worker's entry point
  and rlimit wiring — not memory isolation, which `/retrieve` cannot have). Add
  `extract_pdf_bytes_in_subprocess(data: bytes, settings: ExtractionSettings) ->
  ExtractionResult` to the **un-hashed** `pipeline/pdf_subprocess.py`, and a function
  `spool_dir() -> Path` returning `Path(tempfile.gettempdir()) / f"forage-spool-{os.getuid()}"` —
  resolved **per call**, not at import, so a `TMPDIR` set after import still applies and the tests'
  monkeypatch seam is `tempfile.gettempdir` (a module-level constant would freeze `/extract`'s
  upload path at import, a behaviour change the story must not make). The lifespan creates that
  directory with `mode=0o700` (`mkdir(exist_ok=True)`, then `chmod`) and verifies it is a
  directory, owned by the process, with no group or other bits — refusing boot with a
  closed-vocabulary `RetrieveConfigurationError` otherwise — so "process-private" is enforced by
  construction: the child re-opens the spool file by path inside a directory nobody else can enter,
  which closes the re-open window without depending on the sticky bit of `/tmp`. `_spool_upload`
  (`retrieval_app.py:1127-1152`) passes the same `dir=spool_dir()` (its `poppy-extract-` prefix is
  pre-existing and out of scope), so both routes' spool files share the private directory.
  `tempfile.NamedTemporaryFile(prefix="forage-retrieve-", dir=spool_dir(), delete=False)`,
  `os.fchmod(fd, 0o600)`, write, close, call the path variant, and `path.unlink(missing_ok=True)`
  in a `finally` that also covers `asyncio.CancelledError` and a worker exception; an `OSError`
  from the create or the write is mapped by the caller to the `pdf_spool_error` row (the file, if
  created, is still unlinked). `TMPDIR` is an operator input: `docs/configuration.md` states the
  requirement (tmpfs recommended; the private subdirectory is created for you), the **combined**
  worst-case spool footprint — `/extract`'s existing reservation (`extraction_concurrency ×
  max_input_bytes` + `admission_queue_depth × max_input_bytes`, ≈ 100 MB at the defaults) plus
  `/retrieve`'s (`fetch_concurrency × 10 MB` active + `max_queued_fetch_bytes` queued) — beside the
  memory ceiling, that on a non-tmpfs `TMPDIR` orphaned `forage-retrieve-*` files survive a SIGKILL
  until the next manual clear (the sweep is an Open Question), and the order of magnitude of the
  first-fetch cost: the worker spawns a fresh interpreter per call (`multiprocessing.get_context(
  "spawn")`, `:161`), hundreds of milliseconds on a cold spawn, paid once per unique fetched PDF
  (repeat fetches are cache hits) — so the consumer sets its request timeout accordingly.
- Route the `detect_content_type` branch (`:346-351`) through `await asyncio.to_thread(
  extract_pdf_bytes_in_subprocess, fetch_result.response_body, extraction_settings)` inside
  US-002's admission slot. `run_retrieve_pipeline` already takes `extraction_settings` (US-001);
  the handler passes `app.state.extraction_settings`. **Never** touch
  `ExtractionAdmissionMiddleware` (`retrieval_app.py:1080-1126`): path-gated to `/extract`, 404s on
  `route_enabled` (`config.yaml:27` ships `false`). A test drives a fetched-PDF `/retrieve` with
  `extract_route_enabled` absent/false and asserts 200.
- **Which ceiling (R23).** The worker enforces `settings.max_extracted_characters`
  (`pdf_subprocess.py:70-76`, `:80-86`, parent re-check `:208-209`), which under
  `app.state.extraction_settings` is `max_extracted_characters(64)` = 114,688 characters. That is
  deliberate: the child's rlimits (`child_address_space_bytes`, `child_cpu_seconds`) were sized for
  that ceiling, and `extraction_settings_from_config` bounds the key at 64
  (`pipeline/extraction_limits.py:148-154`). A fetched PDF over it is refused `content_too_large` /
  `promptguard_budget` — the same code and reason as an over-budget HTML page, under a smaller
  ceiling. Goals, Edge Cases, `docs/configuration.md:487` and `API_GUIDE.md` say so in one sentence
  each ("fetched PDFs run under `extraction.max_promptguard_chunks`; fetched HTML under
  `retrieve.max_promptguard_chunks`"). Raising the PDF ceiling means re-sizing the child's rlimits —
  spec 6's envelope work, not this story.
- Error mapping: wrap the worker call in the table above — four worker outcomes, one spool outcome.
  `PDFPageLimitError` (raised in the child at `:61`) and every other exception fall through
  `_pdf_child`'s `except Exception` (`:146-151`) to `{"status": "failed"}`, which the parent raises
  as a plain `PDFExtractionError` (`:190-191`); `PDFTooLargeError` (`pipeline/stage1_pdf.py:83`,
  `:113-116`, the in-process 50 MB check) is unreachable on the worker path and doubly so under the
  10 MB fetch cap — neither gets a row or a reason literal. Every PDF failure is a
  `PDFExtractionError` subclass (`pipeline/stage1_pdf.py:79-92`; `pipeline/pdf_subprocess.py:24-29`),
  so the `except` order is most-specific-first: `PDFClassifiableTextLimitError`, then
  `PDFEncryptedError`, then `PDFNoTextError`, then `PDFExtractionError`, then `OSError` — the
  **file** route's mapping at `orchestrator.py:516-529` is the model (it has both the
  `PDFClassifiableTextLimitError` clause at `:518-519` and the `except OSError` at `:528-529`; the
  bytes route at `:445-456` has neither and would swallow the classifiable-text row into
  `extraction_failed`). `RetrieveErrorCode`
  (`pipeline/contract.py:243-259`) gains **`extraction_failed`** (already an `Extract422ErrorCode`
  member, `:196-206`, so the deduplicated `ERROR_CODES` total stays eighteen —
  `tests/test_contract_errors.py:233` — while `RETRIEVE_ERROR_CODES` moves 7 → 8 after US-002's
  `busy`, at `:241`; the intersection assertion at `:245-247` becomes `{content_too_large,
  extraction_failed, busy}` and its `10 + 6 + 3` comment is rewritten; `tests/test_contract_schema.py:
  339`'s pin is renamed to its new member count; `RetrieveErrorCode`'s docstring at `:250-258` is
  rewritten). Reasons are token-shaped constants beside `PROMPTGUARD_BUDGET` following
  `POLICY_EXCLUDED_ALL_PROVIDERS` (`:318`); `pdf_encrypted` / `pdf_no_text` are intentionally the
  same literals as `/extract`'s codes for the same failures — carry the "Intentionally the same
  literal" comment shape of `contract.py:109-111`, and the docstring line says they are reasons
  under `extraction_failed`, not members of `RetrieveErrorCode`. `document_failure` (`:149`) is
  `/extract`-only, so build the `/retrieve` `PipelineError` directly.
- Docstring line: "`/retrieve` 422 gains `extraction_failed` (fetched-PDF worker and spool
  failures) with reasons `pdf_encrypted` | `pdf_no_text` | `pdf_extraction_error` |
  `pdf_spool_error` — reasons named after `/extract`'s codes for the same failures, not
  `RetrieveErrorCode` members; a fetched PDF over `extraction.max_promptguard_chunks` is
  `content_too_large` / `promptguard_budget`". MINOR under GOVERNANCE ruling (b).
- Tests: `kit_tools/testing/TESTING_GUIDE.md:255` maps `pipeline/pdf_subprocess.py` to
  `tests/test_stage1_pdf.py` — the bytes entry point, the spool lifecycle (including the `OSError`
  and cancellation cases) and the mapping table go there (there is no `tests/test_pdf_subprocess.py`
  and no test drives the worker today; a real `RLIMIT_CPU` kill is not reproducible in the hermetic
  suite, so the kill case patches the worker to raise `PDFExtractionError`). Route-level cases go
  beside `tests/test_orchestrator.py::test_post_retrieve_error_response` (`:1734`).
- Docs: `kit_tools/docs/MONITORING.md:163` (`retrieve.errors` key list) gains `extraction_failed`
  (and `busy` from US-002 if not yet there); `kit_tools/docs/API_GUIDE.md:197-199` and `:421`'s
  `/retrieve` 422 table and `kit_tools/docs/TROUBLESHOOTING.md:168`'s table gain the row with its
  reasons and the ceiling sentence; `docs/configuration.md` gains the spool-directory requirement,
  the disk ceiling and a latency note (a fetched PDF now pays the worker's process-spawn cost on
  first fetch; repeat fetches of the same URL are served by the content cache);
  `kit_tools/arch/SECURITY.md:198`'s `/retrieve` row (edited by US-002) says PDF parsing runs in the
  worker under `/extract`'s rlimits; `CODE_ARCH.md` says both routes parse PDFs in the worker.
- Rotates `sanitizer_revision` (`orchestrator.py`, `contract.py`), ruling 6 / R32.

**Acceptance Criteria:**
- [ ] Fetched `application/pdf` bodies are parsed by `extract_pdf_bytes_in_subprocess` →
      `extract_pdf_in_subprocess` inside `asyncio.to_thread`, with `app.state.extraction_settings`
      (asserted argument), inside the admission slot; `pipeline.pdf_subprocess.spool_dir()` resolves
      per call under `tempfile.gettempdir()` and `_spool_upload` uses it; the lifespan creates the
      `forage-spool-<uid>` directory 0700 and refuses boot on wrong ownership or permissions (tested
      with a pre-created 0755 directory); the `except` chain is most-specific-first (a
      `PDFClassifiableTextLimitError` yields the `content_too_large` row, never `extraction_failed`).
- [ ] The spool file has mode `0600` while the worker runs and no `forage-retrieve-*` file remains
      under the spool directory after success, after each table row, after a simulated kill, after a
      spool `OSError` and after cancellation (a test scans the directory after each).
- [ ] Each row of the five-row table yields the stated `error` and `reason` as a 422, never a 500;
      a fetched-PDF `/retrieve` with `extract_route_enabled` at its default is 200; a fetched PDF
      over `max_extracted_characters(extraction.max_promptguard_chunks)` is refused
      `content_too_large` / `promptguard_budget` while the same text as HTML is served under
      `retrieve.max_promptguard_chunks`.
- [ ] `RetrieveErrorCode` gains exactly `extraction_failed` (beside US-002's `busy`); the four reason
      literals are constants in `pipeline/contract.py` with the same-literal comment;
      `tests/test_contract_errors.py:241` (→ 8), `:245-247` and its comment, `:233` (stays 18),
      `tests/test_contract_schema.py:339`'s renamed pin and `RetrieveErrorCode`'s docstring are
      updated.
- [ ] `ExtractionAdmissionMiddleware`, the worker's IPC vocabulary and `/extract`'s behaviour are
      untouched (existing tests pass without edits); `SECURITY.md:50` still holds.
- [ ] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; the member and reasons appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four
      anchor-quoting pages refreshed; `--check` green.
- [ ] `grep -n 'pdf_spool_error' docs/configuration.md kit_tools/docs/MONITORING.md
      kit_tools/docs/API_GUIDE.md kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per
      file; `docs/configuration.md` carries the spool-directory requirement, the combined disk
      footprint, the orphan-on-SIGKILL sentence and the spawn-latency order of magnitude
      (`grep -n 'forage-spool\|spawn' docs/configuration.md` hits); `CODE_ARCH.md` and
      `SECURITY.md:198` carry the worker sentences.
- [ ] `sanitizer_revision` rotation measured (revert `orchestrator.py` and `contract.py` each in
      turn, both-reverted control) and recorded at the five sites.
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
  (`cache.py:187-210`), and return `None`. Keep the guard as one function the HMAC check (spec 4)
  can call first: an unverifiable value is *also* a miss.
- Logging: `_closed_vocabulary_reason` (`:297-305`) is an exception-to-reason mapper, not a token
  registry; log the fixed literal `cache_entry_corrupt` directly in the `logger.warning("…(%s)",
  token)` shape used at `:418-421` / `:481-485`, plus the cache key (`cache_key`, `:106-117`, is a
  `ret:<sha256>` digest — one-way, credential-free, and what an operator needs to correlate
  repeats); never the raw value or `str(exc)`. `tests/test_cache.py` asserts the vocabulary today
  and the new test extends it with a sentinel-in-value assertion.
- `CacheMetrics` **is** mirrored on `/metrics` (`CacheMetricsResponse`, `retrieval_app.py:568-600`,
  `extra="forbid"`): the counter is a window addition. Order guards:
  `tests/test_contract_metrics.py:135`, `:161`.
- `cache.py` is not hashed, but the docstring line in `contract.py` is (R32): this story rotates.
- Docs: `kit_tools/docs/MONITORING.md` `/metrics` cache table gains the `corrupt_entries` row;
  `kit_tools/docs/TROUBLESHOOTING.md` notes that a repeated `cache_entry_corrupt` WARNING for one
  key digest means an external writer (spec 4's `integrity_rejects` is the stronger signal); and
  `kit_tools/arch/SECURITY.md`'s cache paragraph states the residual plainly: **parse success is not
  authenticity** — a value that validates is served as written until spec 4's HMAC lands, and
  `corrupt_entries` counts values that fail to parse, not values that were tampered with; this story
  makes the poisoning path quieter (a miss instead of a 500), which is why spec 4 follows it.

**Acceptance Criteria:**
- [ ] `ContentCache.get` returns `None` for a value that fails `RetrievedContent` validation or JSON
      parsing, deletes the key and increments `corrupt_entries`; the next request repopulates it.
- [ ] The WARNING carries the closed token `cache_entry_corrupt` and the key digest only; a
      sentinel string in the corrupt value appears in no log record.
- [ ] No `/retrieve` request returns 500 for any cache content (a test drives three malformed shapes:
      invalid JSON, wrong schema, wrong `retrieved_at` type).
- [ ] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; `corrupt_entries` appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four
      anchor-quoting pages refreshed; `--check` green; `/metrics` serves the counter.
- [ ] `grep -n 'corrupt_entries\|cache_entry_corrupt' kit_tools/docs/MONITORING.md
      kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per file;
      `grep -n 'not authenticity' kit_tools/arch/SECURITY.md` hits the residual sentence.
- [ ] `sanitizer_revision` rotation (`contract.py` docstring line) measured and recorded at the five
      sites.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-005: Operator policy floors — fail-closed floor on both fetch routes, threshold ceiling on `/retrieve`, reported on every response

**Priority:** P1 (raised in round 3: it is the one control that closes the load-triggerable
fail-open route US-006 introduces, so it cannot be the story most likely to be descoped; it stays
last in `execution_order` because it stamps the fields every earlier story's responses carry)

**Description:** As an operator, I want a server-side fail-closed floor on `/retrieve` and `/search`
and a threshold ceiling on `/retrieve` that a caller cannot lower or raise past, off by default,
with the effective policy reported on every response, so the security floor for web content is mine
to set while today's contract holds for every existing caller.

**Independent Test:** With `promptguard_fail_closed_floor: true` and no classifier loaded, a
`/retrieve` request for a STANDARD-tier domain with `promptguard_fail_closed: false` returns
`promptguard_state == "unavailable_blocked"` and `effective_promptguard_fail_closed is True`, and a
`/search` request with the same flag omits every result under `promptguard_unavailable` and reports
`effective_promptguard_fail_closed is True`; with `promptguard_threshold_ceiling: 0.5` and a
classifier double scoring `0.7`, a `/retrieve` request carrying `promptguard_threshold: 1.0` is
blocked and reports `effective_promptguard_threshold == 0.5`; a cache hit under a floored request
reports the floored values; a `/retrieve` naming the domain in `verified_domains` under the floor
with no classifier is served (the VERIFIED tier degrades open by design) and still reports
`effective_promptguard_fail_closed is True`; with the defaults (`false`, `1.0`), both routes behave
exactly as today and report the request's own values; a 422 body carries no `effective_*` field
(`Pipeline422ErrorResponse` is unchanged); a `model_copy(update=)` whose key is not a model field
fails the unit test that pins the update keys.

**Implementation Hints:**
- Decision 3 + R10: the two keys are read by `retrieve_settings_from_config` (US-001) into
  `RetrieveSettings.promptguard_fail_closed_floor` and `.promptguard_threshold_ceiling`, bounded by
  `pipeline/config_bounds.bounded_float` / a bool check, refusing boot with
  `RetrieveConfigurationError` on a wrong type or range (these are new keys, so refusal is no
  upgrade break; a security floor that silently fell back would be a fail-open — Decisions Made).
  `/extract` keeps its hard-pinned fail-closed (`orchestrator.py:469`, `:545`) and its own threshold
  read (`retrieval_app.py:1704`) — untouched.
- Resolve **once, in the handler, by replacing the request (R24).** The pipelines read
  `request.promptguard_fail_closed` at `pipeline/orchestrator.py:258` (cache fingerprint), `:373`
  (`sanitize_and_structure`) and `:1019` (`/search`), and neither takes it as a parameter; a
  parallel kwarg would leave the fingerprint reading the caller's value — the exact bypass to avoid.
  One helper in `retrieval_app.py` (not hashed; `pipeline/search_providers/policy.py:35`
  `apply_request_policy` is the precedent for handler-side request policy) computes
  `effective_fail_closed = body.promptguard_fail_closed or floor` for both routes and, on
  `/retrieve` only, `effective_threshold = min(body.promptguard_threshold, ceiling)`, and hands the
  pipeline `body.model_copy(update={...})`. `model_copy` validates nothing and accepts unknown keys
  silently, so the helper builds its update dict from names asserted against
  `type(body).model_fields` (a unit test pins it). `cache_policy_fingerprint` (`cache.py:120-164`)
  already takes both fields from the request, so a floored request keys to the fail-closed /
  ceiling'd entry with no further change. US-006's wait-timeout path reads the effective value
  through the same replaced request.
- **`/search` and the ceiling.** `SearchRequest` (`models.py:272-327`) has no `promptguard_threshold`
  field and the handler (`retrieval_app.py:1811-1817`) passes none; `run_search_pipeline` classifies
  at its own `promptguard_threshold: float = 0.85` (`orchestrator.py:782-783`). Spec 3 US-005 adds
  the field and makes `/search` honour it, defaults it from `config.yaml`, applies the ceiling and
  adds `effective_promptguard_threshold` to `SearchResponse` (it runs after this spec). Here
  `SearchResponse` gains only `effective_promptguard_fail_closed`; a field must never report a
  policy the route did not apply.
- Report **after the pipeline, on every 200 response.** Refusals are `Pipeline422ErrorResponse`
  bodies raised before any stamping and carry no policy fields — the criteria say "every 200
  response". The helper carries a comment stating the convention for the future: any field that is
  both caller-controlled and operator-boundable is resolved here, never threaded as a pipeline
  parameter. The handler returns
  `content.model_copy(update={"effective_promptguard_fail_closed": …,
  "effective_promptguard_threshold": …})` for `/retrieve` and the fail-closed field alone for
  `/search`, so a cache hit (whose stored object was built before any stamping) reports this
  request's effective values — which are also its fingerprint inputs, so hit and miss always agree.
  Field definitions: `RetrievedContent` (`models.py:59`, beside `promptguard_state` `:110`) gains
  `effective_promptguard_fail_closed: bool = True` and `effective_promptguard_threshold: float =
  0.85`; `SearchResponse` (`:395`, beside `promptguard_unavailable`) gains
  `effective_promptguard_fail_closed: bool = True` (defaults equal to the request defaults so a
  1.2.0 golden reader and a pre-upgrade cached entry still validate; the stored value is always
  overwritten by the stamp). Docstring line: "`RetrievedContent` gains
  `effective_promptguard_fail_closed` and `effective_promptguard_threshold`; `SearchResponse` gains
  `effective_promptguard_fail_closed`; `/extract` is permanently fail-closed and carries neither".
  `models.py` is not hashed; `contract.py` is (R32).
- **What the fields mean, stated honestly — both exemptions named.** `effective_promptguard_fail_
  closed` is the fail-closed policy applied to this request's `promptguard_fail_closed` flag — it
  decides behaviour only when the classifier is unavailable to the request (absent, or the wait
  timed out, US-006). `effective_promptguard_threshold` is the block threshold applied. Neither says
  "content was scanned": `promptguard_state` (`/retrieve`) and per-result omissions / `suspicious`
  (`/search`) say that. Two caller-controlled exemptions bypass the flag entirely and the floor does
  not reach them: a `trusted_domains` match skips classification
  (`pipeline/stage3_promptguard.py:77-88`, `skip_reason="trusted_tier"`), and a `verified_domains`
  match degrades **open** when the classifier is unavailable (the fail-closed branch at `:90-95`
  applies to STANDARD and UNTRUSTED only; VERIFIED takes the −0.1 penalty). The field descriptions,
  `kit_tools/docs/API_GUIDE.md` rows and the `kit_tools/arch/SECURITY.md` sentence ("the floor is the
  operator's, the request is the consumer's; the floor bounds the `promptguard_fail_closed` flag and
  the ceiling bounds `promptguard_threshold`; neither overrides the caller-supplied trust tiers,
  which decide whether the flag is consulted at all — the trusted-tier skip and the VERIFIED
  fail-open exemption") all carry that scope. An operator-side bound on the request trust lists is
  spec 3's leading-dot rule (Out of Scope here).
- The seven boundary-text copies (spec 3 US-004 enumerates them) do not need to name the floors —
  they are operator config, not request knobs; the two request fields' descriptions do gain
  "bounded by the operator's floor/ceiling".
- Aggregate telemetry (a `policy_floor_applied` counter) is deliberately out of scope; the
  per-response fields are the signal.
- Docs: `docs/configuration.md` rows (US-001 created them; this story fills their Purpose text and
  the "how to set it in a deployed container" cross-reference to spec 6's bind-mount procedure),
  `kit_tools/docs/API_GUIDE.md` field rows, `kit_tools/arch/SECURITY.md` sentence above.

**Acceptance Criteria:**
- [ ] `promptguard_fail_closed_floor` and `promptguard_threshold_ceiling` are read at boot with the
      defaults above; a non-boolean floor or an out-of-range ceiling refuses boot with a
      closed-vocabulary `RetrieveConfigurationError` message.
- [ ] With the floor `true`, `/retrieve` and `/search` behave fail-closed regardless of the request
      flag for STANDARD- and UNTRUSTED-tier content; with the ceiling below a `/retrieve` request's
      threshold, classification uses the ceiling; with the defaults, behaviour and every existing test
      are unchanged; the VERIFIED and trusted-tier exemptions behave exactly as today (tested).
- [ ] The value reaching `cache_policy_fingerprint` is the effective value (a test asserts a
      fail-open entry is not served to a floored request and that the fingerprint inputs equal the
      effective values); the `model_copy` update keys are pinned against `model_fields`.
- [ ] `RetrievedContent` carries both `effective_*` fields and `SearchResponse` carries
      `effective_promptguard_fail_closed`, stamped by the handler on every 200 response, cache hit
      or miss (a test serves a hit under a floored request and asserts the floored values; a
      hand-seeded `FakeStorage` entry without the fields — the only way such an entry can exist,
      since every story here rotates the cache key — is served with the stamped values); 422 bodies
      carry no policy fields.
- [ ] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; the three fields appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four
      anchor-quoting pages refreshed; `--check` green.
- [ ] The field descriptions, `API_GUIDE.md` rows and `SECURITY.md` sentence state that the fields
      report the policy applied, not whether content was scanned, and name both exemptions
      (`grep -n 'trusted_tier\|VERIFIED' kit_tools/arch/SECURITY.md kit_tools/docs/API_GUIDE.md`
      hits the new sentences).
- [ ] `docs/configuration.md`'s two rows carry their Purpose text and the deployed-container
      cross-reference, and both `docs/configuration.md` and `kit_tools/arch/SECURITY.md` state the
      negative scope: `promptguard_threshold_ceiling` bounds `/retrieve` only — `/search` classifies
      at a fixed 0.85 until `SearchRequest.promptguard_threshold` lands (spec 3 US-005) — and the
      floor does not reach `/extract` (permanently fail-closed) or the trusted-tier / VERIFIED
      exemptions (`grep -n 'retrieve only' docs/configuration.md kit_tools/arch/SECURITY.md` hits).
- [ ] `sanitizer_revision` rotation (`contract.py` docstring line) measured and recorded at the five
      sites.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- A fetched page that yields zero windows classifies nothing and reports `promptguard_state ==
  "scanned"` as today (US-001).
- A page exactly at `max_extracted_characters(retrieve.max_promptguard_chunks)` classifies; one
  character over is refused `content_too_large` / `promptguard_budget`; there is no byte limb on
  this route (the character bound already bounds bytes ×4) (US-001).
- A page that was served on `main` (over 256 chunks, classified in full) is refused after US-001; the
  operator raises `retrieve.max_promptguard_chunks` to keep accepting it (US-001).
- A cache hit is served without acquiring the classification semaphore or the admission slot
  (US-006, US-002).
- With no classifier loaded, no route acquires the semaphore and the wait counters stay zero
  (US-006).
- `/extract` holds the permit: a `/retrieve` waits, times out and follows its route's
  classifier-unavailable branch; `/extract`'s own acquisition has no timeout (accepted) (US-006).
- A classification-wait timeout on `/retrieve` under an effective fail-open request reports
  `unavailable_allowed` — unscanned content, marked; under fail-closed, `unavailable_blocked`; never
  a 500 (US-006).
- On `/search` the wait budget expires on result 3 of 10: two classified results; eight omitted
  `promptguard_unavailable` (fail-closed) or served with `suspicious=True` and
  `promptguard_unavailable: true` (fail-open); the counter increments once (US-006).
- A timed-out acquisition that is granted after cancellation releases the permit (US-006).
- A queued `/retrieve` holds no body; a request beyond `admission_queue_depth` or
  `max_queued_fetch_bytes` (the byte bound binds first at the shipped defaults; the depth bound is
  tested under `max_queued_fetch_bytes: 10485760`) is refused `busy` / `admission_queue_full`; a
  request queued longer than `admission_wait_seconds` is refused `busy` / `admission_wait_timeout`
  and dequeued (US-002).
- A fetch error or an extraction exception releases the admission slot and the body; a request
  parked on the classification permit holds only its extracted text (US-002).
- `/extract`'s admission refusal stays 429; `/retrieve`'s is 422 — the same literal, route-aware
  (US-002).
- Bytes served as `application/pdf` that sniff as HTML follow `detect_content_type`'s verdict, as
  today (US-003).
- A fetched encrypted PDF, a text-free PDF, a PDF over the worker's classifiable-text ceiling, a PDF
  over `max_pages` (arrives as `PDFExtractionError`), and a worker killed by `RLIMIT_CPU` /
  `RLIMIT_AS` / the wall clock each map to the table's `error` / `reason` (US-003).
- A read-only or full spool directory yields `extraction_failed` / `pdf_spool_error` with no
  orphan file; a spool directory that exists with the wrong mode or owner refuses boot (US-003).
- The spool file is gone after cancellation mid-parse (US-003).
- A cache value that parses but carries a different `sanitizer_revision` is already a miss through
  the key; US-004 covers only values that fail to parse (US-004).
- Three malformed shapes are tested: invalid JSON, wrong schema, wrong `retrieved_at` type (US-004).
- Floor `true` with request `true`: field reports `true`, nothing overridden (US-005).
- Floor `true` with a classifier loaded: no behavioural change; the field still reports `true`
  (US-005).
- A hand-seeded cached entry without the `effective_*` fields is served with the handler's stamped
  values; a 422 carries none (US-005).
- A `trusted_domains` match skips classification and a `verified_domains` match degrades open under
  the floor, exactly as today; the `effective_*` fields report the policy, `promptguard_state`
  reports what happened (US-005).

## Out of Scope

- HMAC signing of cache values and the `cache_unauthenticated` degraded reason — spec 4 (which builds
  on US-004's miss branch).
- Changing `/extract`'s limits, its admission middleware, the untimed nature of its classification
  acquisition (the acquisition itself moves to the stage-3 seam in US-006), or its hard-pinned
  fail-closed policy.
- `/search`'s own synchronous `extract_html` / `scan_structural` sites on ≤ 2 000-character fields.
- A wall clock on `/retrieve` HTML extraction; sizing `retrieve.fetch_concurrency`,
  `classification_concurrency` and the PDF worker's rlimits; the `config.yaml` bind-mount procedure
  and any env override for config keys — spec 6.
- `SearchRequest.promptguard_threshold`, the `/search` half of the threshold ceiling and
  `effective_promptguard_threshold` on `SearchResponse` — spec 3 US-005.
- Hostname semantics for `blocked_domains` / trust tiers and the leading-dot allowlist rule that
  bounds the trusted-tier surface — spec 3.
- Making the floors per-route or per-caller; aggregate floor telemetry; a `/health` degraded reason
  for classification-wait timeouts (Decisions Made).
- A bytes-accepting worker entry point that avoids the spool write; a startup sweep of stale spool
  files (Open Questions).
- Migrating `cache.py`'s `_bounded_int` onto `pipeline/config_bounds.py` (its comment records why
  it cannot import from `pipeline`; `extraction_limits.py`'s copy is migrated in US-001).
- Raising the default `classification_concurrency` (kept at 1; R16 — the envelope changes nothing at
  the defaults — and spec 6 widens the range).
- Bounding the population of classification waiters (`--limit-concurrency`) — spec 6's envelope.

## Assumptions

- Poppy is the only consumer; every wire addition is defaulted so a 1.2.0 client still validates.
- `/retrieve` gets its own `RetrieveSettings` (budget, admission limits, wait, policy floors);
  `ExtractionSettings` stays `/extract`'s and supplies the PDF worker's rlimits and 64-chunk ceiling
  for both routes.
- `RetrievedContent` cached under the old value shape stays readable; a value that fails to parse is
  a miss, never an error; a value without the `effective_*` fields is stamped on read.
- The `1.3.0` golden is mutable until spec 8 US-002 freezes it (ruling 5); it exists because spec 1
  US-004 created it (`depends_on`).
- The spool directory is a process-private `forage-spool-<uid>` subdirectory of
  `tempfile.gettempdir()`, created 0700 at boot and resolved per call; disk cost per fetched PDF is
  at most the 10 MB fetch cap; `TMPDIR` is an operator input documented as such.
- The eight `retrieve:` / policy keys are `config.yaml`-only until spec 6's bind-mount and env
  procedure lands — an interim state the epic wrapper tracks (the shipped `extraction:` block has the
  same shape today).
- No new dependency: `asyncio`, `tempfile`, `os.fchmod` are stdlib.

## Technical Considerations

- **Rotations (R32).** All six stories rotate `sanitizer_revision`: US-001 (`orchestrator.py`,
  `contract.py`), US-006 (`orchestrator.py`, `contract.py`), US-002 (`orchestrator.py`,
  `contract.py`), US-003 (`orchestrator.py`, `contract.py`), US-004 (`contract.py`), US-005
  (`contract.py`). Each records its own before/after at the five sites (ruling 6); a story touching
  two hashed files reverts each in turn with a both-reverted control; the control baseline is the
  previous story's post-state, so rotations are measured in execution order.
- **Wire changes in the window.** `content_too_large` reason `promptguard_budget` (US-001);
  `retrieve.classification_wait_timeouts`, `search.classification_wait_timeouts` (US-006);
  `retrieve.semaphore_saturation`, `retrieve.busy_rejections`, `busy` on `/retrieve` (422) with
  reasons `admission_queue_full` / `admission_wait_timeout` (US-002); `extraction_failed` on `/retrieve` with four reasons (US-003);
  `CacheMetrics.corrupt_entries` (US-004); `effective_promptguard_fail_closed` on both responses and
  `effective_promptguard_threshold` on `RetrievedContent` (US-005). Each follows the R36 block; the
  epic wrapper's window list is updated by the parent.
- **Gate scopes.** Admission controller: cache read → (acquire, bounded by
  `admission_wait_seconds`) → fetch → stage 1 → (release slot **and** body). Classification
  semaphore: stage 3 only on all three routes, acquired inside `sanitize_and_structure` via
  `_bounded_permit` (`/extract` passes no deadline). Stages 2 and 4: `asyncio.to_thread`, bounded by
  neither gate. The three routes serialise behind `classification_concurrency`; `/extract`'s wait is
  untimed and uncounted (recorded). The worst-case permit hold is one 256-chunk `/retrieve`
  classification, which is what `promptguard_wait_seconds` (30 s) must exceed — the sizing rule in
  `docs/configuration.md`.
- **Security considerations.** A saturated classification semaphore degrades an effective fail-open
  fetch request to unscanned, marked content — a load-triggerable route around the classifier on an
  unauthenticated service. It is bounded by `promptguard_wait_seconds`, counted on `/metrics`
  (`classification_wait_timeouts` per route), visible per response (`promptguard_state`,
  `suspicious` / `promptguard_unavailable`), and closed by `promptguard_fail_closed_floor: true`
  (US-005); the head-of-line risk is a 256-chunk `/retrieve` against per-result `/search` calls. The
  new counters and `effective_*` fields are an unauthenticated saturation and policy oracle — a
  posture note reinforcing the private-network deployment requirement, recorded in `SECURITY.md`.
  The `/retrieve` admission refusal (`busy`) is a caller-visible load signal by design. Two more
  recorded facts: unauthenticated fetch traffic can block `/extract`'s untimed acquisition (the
  one-directional coupling, with no counter of its own — its signal is the prose); and the
  population of classification waiters is unbounded in count (bounded per waiter to the extracted
  text and in time to `promptguard_wait_seconds`) until spec 6 sets `--limit-concurrency`.
- **Event-loop proof.** The responsiveness test follows `tests/test_app.py:958-993` (real
  `ASGITransport` client over the real lifespan, connected cache, `threading.Event` release); a
  mocked loop proves nothing.
- **Signatures.** `run_retrieve_pipeline` gains five required keyword-only parameters in US-001
  (16 pre-existing test sites, once, through one helper); `run_search_pipeline` gains two defaulted
  parameters in US-006 (73 sites unchanged); `sanitize_and_structure` gains two defaulted parameters
  in US-006 (no test calls it directly today). The module-level fallbacks (`retrieval_app.py:1378-
  1379`, `:1399-1401`) keep lifespan-free test clients working.
- **Import direction.** `pipeline/` never imports `retrieval_app`; the admission gate and the two
  metrics sinks cross the boundary as consumer-side Protocols (`AdmissionSlot`, `AdmissionMetrics`,
  `RetrieveMetricsSink`), the `SearchMetricsSink` precedent.
- **Pyright strict.** New parameters and Protocols typed; no `type: ignore`; the three Protocols are
  the only structural-typing additions.
- **Line anchors** are measured against the pre-epic tree and drift by story (`orchestrator.py`,
  `retrieval_app.py`, `contract.py`, `SECURITY.md`); resolve by symbol and re-grep at story start.
  Verified in round 3: `structure_sanitization_result` is called at `orchestrator.py:191` and the
  `/retrieve` `sanitize_and_structure` call is `:368`; `tests/test_contract_errors.py`'s counts are
  `:234` (18) / `:241` (6) with the comment at `:244`; `extraction_limits.py`'s `_bounded_int` is
  `:79` and its 64-chunk bound `:146`; `pipeline_error_handler` is `retrieval_app.py:1411` with the
  status choice at `:1424`, and the middleware's 429 is minted at `:1112`.
- Related: `kit_tools/arch/SECURITY.md:50` (middlewares gate on `/extract` — unchanged), `:143`
  (chunk budget semantics — US-001), `:196-198` (admission table — US-006, US-002, US-003);
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

**Decision:** `/retrieve` gets its own `RetrieveSettings` (a larger pre-checked chunk budget,
admission limits, a bounded classification wait, the policy floors), a second
`ExtractionAdmissionController`, and shares the classification semaphore and the PDF worker; it
never touches `ExtractionAdmissionMiddleware`.
**Rationale:** The chunk budget is a refusal (`promptguard/classifier.py:179-182`), so reusing
`/extract`'s 64 verbatim would turn ordinary long pages into refusals; the controller
(`retrieval_app.py:939-1007`) is path-free and bounds slots, queue depth and reserved bytes, while
the middleware (`:1080-1126`) is the class that is path-gated, `route_enabled`-gated and answers 429.
**Alternatives considered:** Reusing `extraction.max_promptguard_chunks` (too small for fetched
pages); a clamp that classifies a prefix (a classifier change and an unscanned tail); a bare
semaphore with an unbounded FIFO (each waiter holding a 10 MB body — not a ceiling); the middleware
(coupling, 404 on default config, 429 on a route that declares only 200/422).
**Source:** `pipeline/orchestrator.py:352-377`, `:531-549`; `promptguard/classifier.py:158-182,
210`; `retrieval_app.py:939-1007`, `:1080-1126`; `pipeline/extraction_limits.py:12-76,98-151`.

**Decision:** Fetched PDFs are spooled to a 0600 temp file in an explicit `SPOOL_DIR` by a bytes
entry point in `pipeline/pdf_subprocess.py`, run under `/extract`'s `ExtractionSettings`, and the
worker's four outcomes plus a spool failure map to one new `/retrieve` code, `extraction_failed`,
with closed reasons — no reason for outcomes the worker cannot surface.
**Rationale:** The worker takes a `Path` by design; `/retrieve` 500s on every PDF error today;
`_pdf_child`'s IPC vocabulary (`:132-151`) folds page-limit and kill outcomes into `failed`;
`PDFTooLargeError` is unreachable on the worker path; the child's rlimits were sized for the 64-chunk
ceiling.
**Alternatives considered:** Widening the worker's IPC vocabulary for a `page_limit` status (touches
the shared worker for a distinction nothing needs); mapping to `fetch_error` (a lie about where the
failure happened); passing a `dataclasses.replace`d settings object with the 256 budget (bypasses the
reader's 64 bound and the rlimit sizing — spec 6's call).
**Source:** `pipeline/pdf_subprocess.py:24-28,51-99,114-153,156-173,190-209`;
`pipeline/stage1_pdf.py:79-91,113-116`; `pipeline/contract.py:196-259,318`;
`tests/test_contract_errors.py:233,241,245-247`; `tests/test_contract_schema.py:339`.

**Decision:** A corrupt cache value is a miss with a counter and a closed-vocabulary WARNING that
carries the key digest.
**Rationale:** `cache.py:784` parses without a guard; the closed log vocabulary is asserted by
`tests/test_cache.py`; the `ret:<sha256>` key is a one-way digest an operator can correlate.
**Alternatives considered:** Raising a typed error to the handler (still a failed request).
**Source:** `cache.py:106-117`, `:776-790`, `:187-210`, `:297-305`; `retrieval_app.py:568-600`.

**Decision:** Policy floors are resolved once in the handler by replacing the request, the effective
values are stamped on every response after the pipeline returns, and the threshold half is
`/retrieve`-only here because `SearchRequest` carries no threshold.
**Rationale:** Neither pipeline takes the flag as a parameter and both read it at three sites; a
parallel kwarg would leave the cache fingerprint on the caller's value; stamping after the pipeline
makes cache hits honest without touching the hashed `stage4_structuring.py` builder; a field must
never report a policy the route did not apply.
**Alternatives considered:** A pipeline kwarg (rotation plus the fingerprint bypass); stamping inside
the pipeline (the cached copy would carry a stale value for a different request); bounding `/search`'s
fixed 0.85 by the ceiling here (a behaviour change on a field that does not yet exist — spec 3 owns
the field and the change together).
**Source:** `pipeline/orchestrator.py:258,373,782-783,1019`; `models.py:272-327`; `cache.py:120-164`;
`retrieval_app.py:1591-1604,1811-1817`; `pipeline/search_providers/policy.py:35`.

**Decision:** The classification semaphore covers stage 3 only and is acquired through one
`_bounded_permit` context manager; a timeout follows the route's classifier-unavailable branch under
the effective policy.
**Rationale:** Wrapping the whole `sanitize_and_structure` call would serialise stage 2 at
`classification_concurrency` on both routes; a bare `acquire()` under `asyncio.timeout` can strand
the single permit; the unavailable branches already exist on both routes and are the honest
degradation.
**Alternatives considered:** A distinct `promptguard_state` value for contention (a wire enum
addition for a distinction the counters and the floor already cover); a wait timeout that fails
closed regardless of the request flag (silently overrides a consented policy — the operator floor is
the explicit version of that choice).
**Source:** `pipeline/orchestrator.py:160-207,539,1012-1046`; `pipeline/stage4_structuring.py:96-119`;
`config.yaml:46`.

### Scope Adjustments

- The review's "corrupted cache entry" item moved here from the cache-integrity spec because the
  parse guard is the branch spec 4's signature check reuses.
- Validation round 1 (2026-09-19): the old US-002 split into US-002 (off-loop + bounded slot) and
  US-003 (PDF worker + failure vocabulary); the old US-003/US-004 became US-004/US-005; US-001
  gained the pre-check, the bounded wait and `/search` semaphore parity (R16); US-005 gained the
  threshold ceiling (R10) and the handler-stamping mechanism (R24).
- Validation round 2 (2026-09-19): US-001 split into US-001 (`RetrieveSettings`, the whole new
  pipeline signature, the pre-checked budget) and US-006 (the classification semaphore on both fetch
  routes, permit discipline, wait counters) — R37; `execution_order` declared so US-006 runs second.
  US-002's bare semaphore became a second `ExtractionAdmissionController` acquired before the fetch,
  with a 422 `busy` refusal (R23 corrected); `structure_sanitization_result` joined the threaded
  calls. US-003's table lost the two rows the worker cannot surface and gained the spool-failure row;
  fetched PDFs run under `/extract`'s ceiling by decision. US-005's threshold half became
  `/retrieve`-only; the `/search` half moved to spec 3 US-005 (R10 corrected). The counters gained
  their sink (`RetrieveMetricsSink`), `run_search_pipeline`'s parameters became defaulted, and the
  wait knob became the top-level `promptguard_wait_seconds`.

### Decisions Made

- Cache hits bypass both gates (US-006, US-002).
- The `/retrieve` budget is its own key (`retrieve.max_promptguard_chunks`, default 256) with a
  characters-only pre-check (round 3 dropped the byte limb: it cannot bind below 293 chunks and the
  character bound already bounds bytes ×4); an over-budget page is refused under the existing
  `content_too_large` code, never a new code (R22). This is a new refusal on an accepting route,
  classified on the record under GOVERNANCE worked example 6 as an expedited security-tightening
  MINOR with **no** compatibility window, for three stated reasons (the refused class is a
  resource-exhaustion vector of the same kind the 10 MiB refusal closes; pages above the knob's
  ceiling cannot be served safely under any configuration; keeping the old behaviour open keeps the
  vector open) — chosen over a MAJOR and over shipping the pre-check disabled by default.
- `/retrieve`'s admission is the existing controller class (its `__init__` restructured to five
  primitives, `from_settings` for `/extract`), acquired before the fetch under a bounded wait; a
  refused admission is 422 `busy` (the existing literal) with reasons `admission_queue_full` /
  `admission_wait_timeout`, because adding a 429 to `/retrieve` would be a new observable status
  (MAJOR) while a new 422 code inside the window is MINOR — and because round 3 found
  `pipeline_error_handler` maps `busy` to 429 by code alone, the handler becomes route-aware with
  both halves tested (R42). The fetched body is released with the slot so a classification waiter
  holds only text; the waiter population is bounded in time and per-waiter size, not in count, and
  the docs say so. **Flagged for the epic wrapper**: ruling 23 and the window list name `busy` and
  both reasons; the epic's success criterion "a 65-chunk page classifies at most 64 chunks" is
  stale (this spec gives `/retrieve` its own 256-chunk budget and refuses rather than clamps) and
  its contract list should assign `effective_promptguard_threshold` on `SearchResponse` to spec 3
  US-005; the interim "hardening knobs are `config.yaml`-only until spec 6" state is tracked there.
- `run_search_pipeline`'s new parameters are defaulted (every collaborator on that signature is;
  `search_metrics` is the precedent), so the 73 test call sites stay untouched; `run_retrieve_pipeline`'s
  are required and land in one story so the 16 sites are rewritten once (overrules "three passes").
- `/search` spends one classification-wait budget per request; the counter is per request; the
  fail-open outcome is served-unscanned-and-marked, stated rather than described as an omission.
- The wait knob is the top-level `promptguard_wait_seconds` (both fetch routes), not a `retrieve:`
  key governing `/search`; `retrieve.fetch_concurrency` avoids colliding with
  `extraction.extraction_concurrency`.
- Classification-wait timeouts and admission saturation are not `/health` degraded reasons:
  `degraded_reasons` is a closed set describing dependency and configuration state, not transient
  load; the counters and per-response state are the signal, and the floor is the control (records
  the invariant-5 reasoning; the Open Question stays non-blocking).
- Serialising `/extract`, `/retrieve` and `/search` behind `classification_concurrency` is
  intentional; `/extract`'s acquisition moves to the stage-3 seam (so US-002's threading does not
  lengthen its permit hold) but stays untimed and uncounted — a one-directional coupling given its
  signal in prose; `classification_concurrency` stays 1 (R16: the envelope changes nothing at the
  defaults; overrules "raise it to 2 here"); spec 6 widens the range. The 256-chunk / 30 s pair is
  reconciled as a documented sizing rule rather than measured here (the hermetic suite cannot time
  the real classifier; spec 7 US-004 fills the per-window number).
- Fetched PDFs run under `/extract`'s `ExtractionSettings` and its 64-chunk classifiable-text
  ceiling — deliberately smaller than the HTML budget, because the child's rlimits were sized for it;
  the old "it is the same ceiling" claim is withdrawn.
- The spool write+read for fetched PDFs is accepted (≤ 10 MB); its rationale is reuse of the
  worker's entry point and rlimit wiring, not memory isolation; a bytes-accepting worker is a later
  optimisation; a startup sweep of stale spool files is deferred. The spool directory is a
  process-private 0700 subdirectory created and verified at boot (a mechanism, not a docs
  sentence — overrules "document the TMPDIR requirement" alone), resolved per call so `TMPDIR`
  semantics on the `/extract` upload path do not change.
- `pdf_encrypted` / `pdf_no_text` are intentionally the same literals as `/extract`'s codes; the
  reasons are token-shaped after `POLICY_EXCLUDED_ALL_PROVIDERS`, not sentence-shaped.
- `pipeline/config_bounds.py` (`bounded_int`, `bounded_float` taking the exception class) absorbs
  `extraction_limits.py`'s copy in US-001 (same package, mechanical); `cache.py`'s copy stays with
  its recorded import-direction reason — two implementations, each with a written reason, not three
  (round 3 corrected the earlier "avoids a third copy" claim).
- The two policy-floor keys refuse boot on a wrong type or range (new keys, no upgrade break; a
  security floor must not fall back silently) — a deliberate difference from R10's fallback rule for
  the pre-existing `promptguard_threshold` key.
- The `effective_*` fields report the policy applied, not whether content was scanned; both
  caller-controlled exemptions (trusted-tier skip, VERIFIED fail-open) are named beside them rather
  than floored here (spec 3's leading-dot rule bounds the trusted surface).
- Aggregate floor telemetry is out of scope (overrules the completionist suggestion); the
  per-response fields are the signal, on 200 responses only.
- US-005 is P1 (round 3): it is the control that closes the fail-open route US-006 opens; the
  interim posture (request default fail-closed) is stated in SECURITY.md for the window between
  the two stories.
- The round-2 proposal to split US-001 again (settings module out) is overruled once (R37
  corrected): already litigated, single concern, session-fit at size L; the rotation measurement
  is whole-file and the record says so.
- The `/search` deadline is a monotonic per-request deadline consulted per acquisition;
  `asyncio.timeout` lives only inside `_bounded_permit` (round 3; the round-2 sentence described a
  loop-wrapping timeout that would unwind the loop).
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

### Session 2026-09-19 (validation round 2)
- Rulings applied: R10 (corrected — `/search` ceiling and `SearchResponse.effective_promptguard_
  threshold` moved to spec 3 US-005; `/retrieve` keeps both), R22 (corrected — `async with` permit
  discipline, permit-count criteria, the bounded-wait branch in Security Considerations), R23
  (corrected — `ExtractionAdmissionController` reused, four-row worker table plus the spool row,
  the lower PDF ceiling documented), R36 (uniform window block), R37 (US-001 → US-001 + US-006;
  `execution_order`), R38 (`/search` under the semaphore; head-of-line risk recorded), R39 (doc
  lists as grep criteria), R40 (call-site counts: 16 / 47 / 14 / 12). Findings overruled and why
  are in Decisions Made.

### Session 2026-09-19 (validation round 3)
- Rulings applied: R23 (corrected — route-aware `pipeline_error_handler`, the `AdmissionSlot` and
  `AdmissionMetrics` Protocols, bodies released with the slot, characters-only budget, the
  GOVERNANCE example-6 lane stated with reasons), R42 (per-route status declared and tested),
  R41 (byte-limb fixtures removed as unconstructible; every value computed), R40 (call-site
  criteria as properties, not raw counts; `sanitize_and_structure` has no direct test callers),
  R37 (no further splits). Additions from the round-2 findings: a bounded admission wait
  (`admission_wait_seconds`), the `classification_wait_timeout` WARNING, `/extract`'s acquisition
  moved to the stage-3 seam, the process-private spool directory, `fetch_concurrency` default 1
  with the combined worker ceiling, the most-specific-first PDF `except` order, US-005 at P1, the
  200-only wording, the `retrieve only` negative scope, the parse-success-is-not-authenticity
  sentence. Findings overruled and why are in Decisions Made.

## Open Questions

- [ ] Whether the new wait and saturation counters should gain a per-route or per-cause breakdown
      on `/metrics` (non-blocking; one counter per route today).
- [ ] Whether `/extract`'s classification acquisition should gain a wait timeout and a counter of its
      own now that it shares the permit with unauthenticated traffic (non-blocking; the direction is
      recorded in SECURITY.md and `extract.route_enabled` ships `false`).
- [ ] Whether `cache.py`'s `_bounded_int` should move to a root-level module so both copies can
      share one implementation (non-blocking; its import-direction comment is the reason it stays).
- [ ] Whether a wall clock on `/retrieve` HTML extraction is needed once spec 6 sizes the slot
      (non-blocking; the fetch timeout and cap bound the input today).
- [ ] Whether a startup sweep of stale `forage-retrieve-*` spool files after a SIGKILL is worth a
      story (non-blocking; a container restart on a tmpfs clears them).
- [ ] Whether sustained classification-wait timeouts should ever surface on `/health` (non-blocking;
      decided "no" here with the reasoning recorded — revisit if operators cannot see the counters).
