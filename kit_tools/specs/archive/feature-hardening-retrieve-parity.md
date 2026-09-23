<!-- Template Version: 2.5.0 -->
---
feature: hardening-retrieve-parity
status: completed
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
updated: 2026-09-22
completed: 2026-09-22
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
> pipeline-side Protocol, bodies released before the wait, characters-only budget; corrected again
> in round 4: the controller is used as it is — constructor and handoff unchanged, no timer around
> its acquisition, `AdmissionSlot` with `async def release` — `build_retrieved_content` untouched,
> a lazily created 0700 spool directory, no cache write on a fail-open wait timeout, the
> `suspicious` description corrected; corrected again in round 5, final: `admission` is defaulted
> to `None` in US-001 and required from US-002, the chunk budget ships off (`0`) for one release
> behind a boot WARNING, `fetch_concurrency` is stated as the one-body bound, the wait-timeout
> seam is named in `stage3_promptguard.py`), R24, R32, R33, R34, R36 (corrected in round 4: only
> added fields and enum members enter the diff set), R37–R43 are binding here.
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

- **The chunk budget is a refusal, not a clamp (R22) — and it ships off, loudly, for one release
  (R23 corrected in round 5).** `PromptGuardClassifier.classify` raises
  `PromptGuardBudgetExceededError` above `max_chunks` (`promptguard/classifier.py:179-182`;
  `kit_tools/arch/SECURITY.md:143` says so). `/extract`'s file route survives it with a pre-check
  and a typed catch. `/retrieve` gets **its own, larger budget** (`retrieve.max_promptguard_chunks`;
  64 chunks is ~112 KB of text against a 10 MB fetch cap and ordinary long articles exceed it), a
  **characters-only** pre-check (`len(raw_text) > max_extracted_characters(n)`; the byte limb
  `/extract` carries cannot bind below 293 chunks and `max_extracted_characters` already bounds
  bytes ×4 — round 3 dropped it), and refuses over-budget pages with the **existing**
  `content_too_large` code and a new closed reason `promptguard_budget`. Honestly stated: today
  `/retrieve` passes no budget, so an over-budget page is classified in full and served; with the
  key set it is refused — a new refusal condition on an accepting route. Its governance lane is
  `contract/GOVERNANCE.md`'s worked example 6 (`:150-167`) followed **by step**, not argued
  around. Step 1 — ship the tightening compatibly: the key ships with default **`0`, meaning no
  pre-check and no `max_chunks` passed (today's call, byte for byte)**; the shipped `config.yaml`
  carries `max_promptguard_chunks: 0` with a comment naming the coming default; and the lifespan
  logs one WARNING with the closed token `retrieve_budget_unset` (naming the coming default, 256)
  on every boot where the key is `0` or absent, so the window is loud (invariant 5), never
  silent. Step 2 — the window is stated in `docs/releases.md` and the Release body: one minor
  release. The release that ships contract 1.3.0 keeps default `0`; the next MINOR release flips
  the default to 256; `0` **stays a legal, loud value** thereafter (the operator's explicit
  opt-out), so step 3's MAJOR is never needed because the old behaviour is never removed. Round
  4's argument — "the knob is the window; the default moves without one" — is withdrawn: a
  `config.yaml` key the consumer cannot set and the operator can change only by rebuilding the
  image (the `retrieve:` block is `config.yaml`-only until spec 6's bind-mount lands —
  Assumptions) is not a compatibility window, and calling it one was step 4's "expedited MINOR
  wearing a disguise". The docstring line announces the reason and the window (ruling (b)'s
  announcement obligation covers `promptguard_budget`, `busy` and `extraction_failed` — each
  story appends its own line and spec 8 US-002's record is the owner of the whole).
- **Parity by reuse: the controller, never the middleware (R23, corrected in round 3).**
  `ExtractionAdmissionController` (`retrieval_app.py:939-1007`) is a plain, path-free, status-free
  class that bounds active slots, queue depth and conservatively reserved queued bytes and counts
  saturation; every objection the first draft raised — path gating, the `route_enabled` 404, the
  429 — belongs to `ExtractionAdmissionMiddleware` (`:1080-1126`), a different class. `/retrieve`
  gets a **second controller instance** built from a `retrieve:` config block through an adapter
  (the constructor's `(settings, metrics)` shape and the eight existing construction sites are
  untouched — R23 corrected in round 4; the instance is built in US-002, so US-001 declares the
  pipeline's `admission` parameter `AdmissionSlot | None = None` — no admission when `None` — and
  ships green alone, and US-002 removes the default — R23 corrected in round 5), acquired
  **before the fetch** exactly as the controller
  is written — no `asyncio.timeout` around `acquire()`: its bounded queue depth and reserved
  queued bytes are the backpressure, a full queue refuses at once, and a timer would leak a slot,
  because `release()`'s handoff (`retrieval_app.py:1000-1007`) pops a waiter and returns without
  decrementing `_active`, so a waiter cancelled after its grant takes the slot with it — released
  at the end of stage 1 **together with the fetched body**, and a refused admission is a 422 `busy`
  — the same closed literal `/extract` emits as a 429. Two things round 3 pinned: the pipeline sees
  the controller only through a pipeline-side `AdmissionSlot` Protocol (`async def acquire() ->
  bool` / `async def release() -> None` — `release` is a coroutine today, `retrieval_app.py:998`;
  `pipeline/` never imports `retrieval_app`; `SearchMetricsSink` is the precedent), and the
  app-wide `pipeline_error_handler` (`retrieval_app.py:1411-1426`) picks 429 for `busy` by *code
  alone* today (`:1424`), so it becomes route-aware — `/extract` keeps 429, `/retrieve` answers
  422 — with both halves tested (R42).
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
`retrieve.semaphore_saturation`, `retrieve.busy_rejections` and `busy` on `/retrieve` (422, reason
`admission_queue_full`) (US-002); `extraction_failed` on `/retrieve` with its reasons (US-003);
`CacheMetrics.corrupt_entries` (US-004); `effective_promptguard_fail_closed` on `RetrievedContent`
and `SearchResponse`, and `effective_promptguard_threshold` on `RetrievedContent` (US-005). Every
one follows the R36 block (docstring line, regenerate, golden via `_SCHEMA_MODELS`, anchor pages,
`--check`), and each block says which half of R36 (corrected) applies: `_added_paths`
(`tests/test_contract_schema.py:130`) sees new `properties` keys and new `enum` members of the six
`_SCHEMA_MODELS` only, so the `busy` and `extraction_failed` enum members
(`Pipeline422ErrorResponse.error[enum]=<code>`) and US-005's three fields append their paths to
`_EXPECTED_ONE_THREE_ZERO_DIFF`, while every `/metrics` counter (the `*MetricsResponse` models are
not in `_SCHEMA_MODELS` — verified), every reason string and every description rewrite appends
**nothing** and is gated by `export_contract --check`, the two `/metrics` order guards
(`tests/test_contract_metrics.py:135`, `:161`) or `test_contract_schema_matches_golden`. One more
consequence stated up front: `Pipeline422ErrorCode` is `Literal[RetrieveErrorCode,
SearchErrorCode]` (`pipeline/contract.py:280-283`) and `Pipeline422ErrorResponse` is the declared
422 body of both `/retrieve` (`retrieval_app.py:1568`) and `/search` (`:1772`), so `busy` and
`extraction_failed` widen the `/search` 422 enum in `contract/openapi.yaml` as well — one diff
path covers both routes — and the field's description (`:723`, which enumerates codes per route)
is rewritten by US-002 and US-003 to say the two new codes arrive on `/retrieve` only. The golden
was created by spec 1 US-004; this spec runs after it per `depends_on`.

## Goals

- With `retrieve.max_promptguard_chunks` set (256 in the tests), a fetched HTML page whose
  extracted text exceeds `max_extracted_characters(retrieve.max_promptguard_chunks)` characters
  is refused with 422 `content_too_large` / reason `promptguard_budget` before any inference,
  never a 500; a page under the budget classifies every window; at the shipped default `0` the
  same page is classified in full and served exactly as today and every boot logs one
  `retrieve_budget_unset` WARNING naming the coming default; `/extract`'s budget, output and
  tests are byte-for-byte unchanged.
- Two concurrent `/retrieve` classifications, a `/retrieve` and a `/search` classification, or an
  `/extract` file-route classification and either fetch route, never run at the same time
  (semaphore size 1 by default; the `/extract` bytes route `run_extract_pipeline` has no HTTP caller
  and stays unguarded); the permit count is identical before and after an over-budget refusal, a raised
  classifier, a timed-out wait and a cancellation; a waiter that exceeds `promptguard_wait_seconds`
  follows its route's classifier-unavailable branch under the effective fail-closed policy and is
  counted once per request; a cache hit never waits; with no classifier loaded nothing is acquired;
  a trusted-tier request acquires nothing (`run_promptguard` returns `skip_reason="trusted_tier"`
  before any inference, `pipeline/stage3_promptguard.py:77-88`); a body served unscanned because
  the wait expired is never written to the content cache.
- During a slow fetched-page extraction (fake extractor blocking for 2 s), five sequential `/health`
  requests each complete in under 1 s (the shape of `tests/test_app.py::
  test_health_answers_while_the_fetch_is_in_flight`); at most **one** fetched body is held, from
  fetch through stage 1, and **none** while a request waits on classification —
  `retrieve.fetch_concurrency` is pinned 1–1, which is the reason the bound reads "one" (the body
  reference is dropped with the slot; a waiter holds at most the extracted text) — so at the
  shipped defaults `/retrieve` is single-flight through fetch and stage 1 with at most
  `admission_queue_depth` queued behind it (the byte bound admits three) and a 422 `busy` for the
  fifth concurrent request, a worst-case queue latency of `admission_queue_depth /
  fetch_concurrency × max(30 s fetch timeout, PDF wall clock)` = 120 s — the throughput cost of
  the memory bound, stated here because Goals must carry it (round 5); a
  queued `/retrieve` holds no body, and a request beyond the bounded queue (depth or reserved
  bytes) is refused 422 `busy` / `admission_queue_full` within one event-loop turn. Every wait in
  this spec is bounded: the classification wait by the `promptguard_wait_seconds` timer, the
  admission queue by construction — at most `admission_queue_depth / fetch_concurrency` slot
  holds ahead of a waiter, each hold bounded by the 30 s fetch timeout plus stage 1 (the PDF
  worker's wall clock; HTML extraction by the 10 MB cap) — not by a timer (R23 corrected).
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
arithmetic is `(512 − 64) × n × 4`, `pipeline/extraction_limits.py:34-36`), under
`retrieve.max_promptguard_chunks: 256`, against a `MagicMock(spec=PromptGuardClassifier)` with
`loaded = True` (the `tests/test_orchestrator.py:1489-1491` idiom US-006 also uses — a real
instance is never `loaded` in the hermetic suite, `promptguard/classifier.py:43` / `:107`, so
`run_promptguard` would short-circuit at `stage3_promptguard.py:89` before `classify` and a
spy-that-raises would pass vacuously; round 5) whose `classify` is a spy that raises
`AssertionError` if invoked; assert a `PipelineError` with `error == "content_too_large"` and
`reason == "promptguard_budget"`. With a page exactly at the limit, assert it classifies. With a
page under the budget and a classifier double whose `classify` raises
`PromptGuardBudgetExceededError`, assert the same 422 (belt and braces). With a page under the
budget, assert every window is classified and the response is byte-identical to today's. With
the key absent, and again with it `0`, assert the over-budget page is classified in full and
served byte-identically to today, and that booting the app logs exactly one WARNING whose message
contains `retrieve_budget_unset` (and none with `256`). Boot the app with
`retrieve.max_promptguard_chunks: 2048` and assert the lifespan refuses with the
closed-vocabulary message. (R41: every value above was computed against the tree; there is no
byte-limb case — see Decisions Made.)

**Implementation Hints:**
- New un-hashed module `pipeline/retrieve_limits.py` with a frozen `RetrieveSettings` dataclass and
  `retrieve_settings_from_config(config) -> RetrieveSettings`, modelled on
  `pipeline/extraction_limits.py` (`:48-58` dataclass, `:98-151` reader, `:100-102` the top-level-key
  read precedent). Keys, all in a new `config.yaml` `retrieve:` block: `max_promptguard_chunks`
  (default **0**, range 0–1024; `0` means no pre-check and no `max_chunks` passed — today's call
  — and the derived `max_extracted_characters` property is `None` for it; the shipped
  `config.yaml` carries `0` with a comment naming the coming default 256 and the release that
  flips it; the lifespan logs exactly one WARNING `retrieve_budget_unset coming_default=256` —
  closed token, nothing beyond the integer — when the key is `0` or absent, and the module-level
  fallback logs nothing, it exists for lifespan-free tests; R23 corrected in round 5),
  `fetch_concurrency` (default **1**, range **1–1** — pinned exactly as
  `extraction.extraction_concurrency` is (`pipeline/extraction_limits.py:153-166`;
  `docs/configuration.md:488`: the memory reservation assumes exactly one worker), because a
  fetched PDF spawns the same worker `/extract` does under the same
  `child_address_space_bytes` rlimit, so N fetch slots would put N × 384 MiB of worker address
  space in a 1 GiB container — the constraint is worker address space, not fetched-body size.
  That reason is the **PDF branch's**; the HTML branch spawns no worker (`orchestrator.py:355`)
  and inherits the same slot for the stage-1 memory peak the `retrieve:` table states (body +
  decoded copy + extraction result per slot), so an HTML-only deployment is throttled to single
  flight by a bound sized for PDFs — accepted and stated, so a later reader does not re-derive
  it; the memory sentence therefore reads "at most one fetched body is held, from fetch through
  stage 1; none while waiting on classification" (round 5); spec 6 widens the range when it
  sizes the envelope; consumed by US-002), `admission_queue_depth`
  (default 4, range 0–16; US-002), `max_queued_fetch_bytes` (default 31457280 = 3 ×
  `DEFAULT_MAX_CONTENT_BYTES`, range 10485760–167772160 (16 ×); US-002 — the default is deliberately
  **not** `admission_queue_depth × 10 MB`, so the byte bound binds before the depth bound at the
  shipped defaults and both bounds are exercisable). There is no `admission_wait_seconds`: the
  admission queue is bounded by its depth and bytes and by the slot hold, not by a timer (R23
  corrected; US-002 says why). Plus two top-level keys read by the same function because it
  owns the boot-validated fetch-route policy (US-005 wires them; US-001 only reads and stores them
  at their defaults): `promptguard_fail_closed_floor` (bool, default `false`) and
  `promptguard_threshold_ceiling` (float, 0.0–1.0, default `1.0`), and one top-level key for both
  fetch routes (US-006 wires it): `promptguard_wait_seconds` (**float**, default `30.0`, range
  0.05–300.0 — a float so `sanitize_and_structure`'s `classification_wait_seconds: float | None`
  and US-006's sub-second test values are in range). Ranges: the `extraction:` block is bounded
  at its defaults on every key but two because its memory reservation assumes exactly one worker
  (`pipeline/extraction_limits.py:153-166`); of the `retrieve:` keys, `fetch_concurrency` is pinned
  for the same worker reason and the other three are raisable (they bound queued and classified
  text, which the 10 MB fetch cap already bounds per body; spec 6 owns sizing) — say so in the
  table preamble.
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
  reservation — **which at the shipped defaults is 2 × 384 MiB + 512 MiB = 1 280 MiB against the
  1 GiB container the same file describes** (`config.yaml:29-31`). The comment says so plainly,
  names `extract_route_enabled: false` (`config.yaml:27`) as the thing that currently prevents
  two workers from running at once (nobody can upload while a fetched PDF parses), and
  cross-references spec 6, which sizes the envelope; a boot-time refusal when the sum exceeds
  the envelope is spec 6's call, not a doc sentence here (round 5).
- Publish `app.state.retrieve_settings` in the lifespan beside `extraction_settings`
  (`retrieval_app.py:1213-1216`) with the module-level fallback the file route has (`:1378-1379`);
  the boot-refusal test follows `tests/test_app.py::test_lifespan_refuses_an_out_of_range_cache_bound`
  (`:1208`).
- **The whole new signature lands here (R40).** `run_retrieve_pipeline` gains five keyword-only
  parameters: `settings: RetrieveSettings`, `retrieve_metrics: RetrieveMetricsSink`,
  `classification_semaphore: asyncio.Semaphore` and `extraction_settings: ExtractionSettings`,
  all **required**, exactly as `run_extract_pipeline_from_file` (`:485-500`) — a defaulted limits
  parameter would be the second, unbounded limits path this spec exists to prevent — and
  `admission: AdmissionSlot | None = None`, defaulted for exactly one story (R23 corrected in
  round 5): nothing creates `app.state.retrieve_admission` in this story — the lifespan
  (`retrieval_app.py:1216-1225`) and the module fallback (`:1379-1400`) publish
  `extraction_settings`, `extraction_admission`, `retrieve_metrics` and
  `classification_semaphore`, verified, and no `retrieve_admission` — so a required parameter
  here would leave every `/retrieve` request dying on `AttributeError` until US-002 and this
  story's own "full suite passes" criterion unsatisfiable. With `None` the pipeline acquires
  nothing (today's behaviour); the handler passes `admission=None` explicitly; US-002 builds the
  controller, removes the default and makes the handler pass `app.state.retrieve_admission` —
  that removal is a US-002 criterion, so the default cannot outlive the story that needs it. The
  other three `app.state` objects the handler passes exist today (`:1223`, `:1225`,
  `extraction_settings`). `AdmissionSlot` is a consumer-side `Protocol` declared in `pipeline/orchestrator.py`
  beside `SearchMetricsSink` (`:747-757`) — `async def acquire(self) -> bool` / `async def
  release(self) -> None` — which `retrieval_app.ExtractionAdmissionController` satisfies
  structurally **without edits**: `:972` is `async def acquire(self) -> bool` and `:998` is `async
  def release(self) -> None`, awaited at `retrieval_app.py:1124`, `tests/test_app.py:456`, `:458`
  and `tests/test_contract_errors.py:464` (verified; round 3's text calling `release` synchronous
  is withdrawn — pyright strict rejects a `Coroutine`-returning member against a `-> None`
  Protocol, and a sync-typed call site would leak the slot on every request). The release site in
  `run_retrieve_pipeline` is `await admission.release()` inside `finally`. **`pipeline/`
  never imports `retrieval_app`**: `retrieval_app.py:65` imports the pipeline, nothing under
  `pipeline/` imports back (`grep -rn "retrieval_app" pipeline/` finds only `contract.py`'s
  docstring prose), and `SearchMetricsSink`'s docstring ("neither module imports the other") and
  `cache.py:253-257` both record the rule; a `TYPE_CHECKING` back-reference would be new to the
  repo and is not used. This story only *uses* `settings` and `retrieve_metrics`; US-006, US-002
  and US-003 wire behaviour to the rest, so the sixteen pre-existing call sites
  (`grep -c 'run_retrieve_pipeline(' tests/test_orchestrator.py` = 16 at story start; none
  elsewhere) are rewritten once. The `client` fixture (`tests/test_orchestrator.py:1470-1506`)
  already publishes `app.state.classification_semaphore` and `extraction_settings`; add
  `retrieve_settings` beside them (`retrieve_metrics` is already published at `:1223` / `:1386`;
  `retrieve_admission` is US-002's), and a shared helper that builds the five kwargs — passing
  `admission=None` until US-002 swaps in the fixture's controller — so every site — pre-existing
  and the ones this story adds — passes them through one line.
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
  `promptguard_budget`: with `retrieve.max_promptguard_chunks` set, a fetched page over it is
  refused rather than classified in full — a security tightening shipped by GOVERNANCE worked
  example 6 step 1: the key defaults to `0` (today's behaviour) for one minor release, the boot
  WARNING `retrieve_budget_unset` names the coming default 256, and `0` remains a legal opt-out
  after the flip". Record the classification in `contract/GOVERNANCE.md`'s recorded rulings as
  the epic's ruling for security tightenings that arrive as new refusal conditions on accepting
  routes — as its own `### (<letter>) `
  section with a `**Source:**` line (the next free marker letter at story start; spec 1 adds `(e)`
  and `(f)` first per the epic's order, so `(g)` is the expected letter), appended to
  `_RULING_MARKERS` in `tests/test_governance_docs.py:93` with `contract/GOVERNANCE.md:174`'s count
  sentence updated, because `TestTheRecordedRulings` (`:354-389`) gates every marker in that tuple
  and a ruling outside it is ungated — citing `contract/GOVERNANCE.md:150-167` by step (step 1:
  shipped compatibly, off by default with the knob; step 2: the window is one minor release,
  named in `docs/releases.md` and the Release body; step 3: never reached, because `0` stays
  legal and the old behaviour is never removed; step 4: not the "cannot be fixed compatibly"
  case), and stating the **lanes** of the two later refusals so the consumer note spec 8 carries
  lists all three with their lanes: `busy` (US-002) is a capacity refusal — a new 422 code under
  ruling (b), with `admission_queue_depth` and `max_queued_fetch_bytes` as the operator's knobs
  (`fetch_concurrency` is pinned and is **not** a knob) — and `extraction_failed` (US-003) turns
  a 500 into a coded 422 under ruling (b); neither is an example-6 tightening (round 5 corrects
  round 4's "same ruling").
- Docs: `docs/configuration.md` gains the `retrieve:` block table (`| Key | Default | Allowed range
  | Purpose |`) and the three top-level rows, each row saying which route it governs
  (`promptguard_wait_seconds`: both fetch routes); `docs/configuration.md:487-489` keep describing
  `extraction.max_promptguard_chunks` as `/extract`'s and fetched PDFs' (US-003) ceiling;
  `kit_tools/arch/SECURITY.md:143` gains the sentence that `/retrieve` pre-checks under
  `retrieve.max_promptguard_chunks` and refuses with `content_too_large` / `promptguard_budget`;
  `kit_tools/docs/API_GUIDE.md:197-199` (which tells consumers `reason` "echoes the requested
  URL") now states that `/retrieve`'s `content_too_large` carries either the fetch-cap prose reason
  or the fixed literal `promptguard_budget` — two reason shapes under one code, in the shape of
  `SearchErrorCode`'s two-reason-forms row in `TROUBLESHOOTING.md` — and `kit_tools/docs/
  TROUBLESHOOTING.md:168`'s `/retrieve` 422 table names the new reason; `docs/releases.md`
  gains the window line beside the open-window line spec 1 US-004 added (the release that ships
  contract 1.3.0 keeps default `0`; the next MINOR flips it to 256; `0` stays legal), and
  `docs/configuration.md`'s `max_promptguard_chunks` row says `0` = no pre-check and names the
  WARNING and the coming default (round 5).
  `kit_tools/testing/TESTING_GUIDE.md`'s `test_mapping` block is exhaustive (every source file has
  a row; a gap makes the orchestrator fall back to a heuristic glob) and gains two rows:
  `pipeline/retrieve_limits.py` → `tests/test_app.py` (the boot-refusal and reader tests live
  beside `test_lifespan_refuses_an_out_of_range_cache_bound`) and `pipeline/config_bounds.py` →
  `tests/test_stage1_extraction.py` (beside `extraction_limits.py`'s row at `:262`, where the
  migrated bound tests land). How an operator sets any of these in a deployed container is the
  bind-mount procedure spec 6 documents (`config.yaml` is copied into the image; there is no env
  override — say so in the table's preamble and cross-reference spec 6).
- Rotates `sanitizer_revision` (`orchestrator.py` — signature, pre-check, sink; `contract.py` —
  constant and docstring line), ruling 6 / R32: revert each in turn with a both-reverted control;
  the control baseline is the previous story's post-state (measure in execution order).

**Acceptance Criteria:**
- [x] `pipeline/retrieve_limits.py` defines `RetrieveSettings` (with a derived
      `max_extracted_characters` property), `RetrieveConfigurationError` and
      `retrieve_settings_from_config` with the seven keys, defaults and ranges above
      (`max_promptguard_chunks` default 0, range 0–1024, `0` = no pre-check and a `None` derived
      ceiling; `fetch_concurrency` default 1, range 1–1, with the PDF-branch worker reason and the
      HTML-branch inheritance in the table preamble; `promptguard_wait_seconds` a float, range
      0.05–300.0; no `admission_wait_seconds`);
      `pipeline/config_bounds.py` provides `bounded_int` and `bounded_float` and
      `pipeline/extraction_limits.py` uses them (its private copy is gone; `cache.py`'s stays with
      its comment); an out-of-range or wrong-type value for any key refuses boot with a
      closed-vocabulary message (a parametrised test per key, modelled on `tests/test_app.py:1208`);
      the lifespan publishes `app.state.retrieve_settings` and the module-level fallback exists; the
      lifespan logs exactly one WARNING containing `retrieve_budget_unset` when the key is `0` or
      absent and none otherwise (tested both ways; the module-level fallback logs nothing); the
      shipped `config.yaml` carries the block with `max_promptguard_chunks: 0` and the
      coming-default comment, the three keys at their defaults, and its worker-memory comment
      states the combined worker ceiling, its 1 280 MiB sum at the defaults, and that
      `extract_route_enabled: false` is what keeps two workers from coexisting.
- [x] `run_retrieve_pipeline` takes the five new keyword-only parameters — four required,
      `admission: AdmissionSlot | None = None` typed against the pipeline-side `AdmissionSlot`
      Protocol and defaulted in this story only (US-002 removes the default; with `None` nothing
      is acquired, tested); `AdmissionMetrics`, `RetrieveMetricsSink` and `_NullRetrieveMetrics`
      exist beside the search sinks; `RetrieveMetrics` carries the three new counters (not yet
      mirrored); the handler passes all five, `admission=None`; no module under `pipeline/` imports
      `retrieval_app` (`grep -rn "^from retrieval_app\|^import retrieval_app" pipeline/` is empty —
      path set `pipeline/`; empty today, verified, while `grep -rn "retrieval_app" pipeline/` finds
      only docstring prose in `contract.py`, `orchestrator.py:750` and `search_providers/`); all 16
      pre-existing `run_retrieve_pipeline(` sites in `tests/test_orchestrator.py` (16 today,
      verified; `grep -l` finds no other test file; re-counted at story start) and every site this
      story adds pass the kwargs through one shared helper — no site passes them inline; a test
      asserts the kwargs reach the pipeline.
- [x] Under `retrieve.max_promptguard_chunks: 256`, a fetched page one character over
      `max_extracted_characters(256)` is refused with 422 `content_too_large` /
      `promptguard_budget` before `classify` is called (a spy-that-raises on a
      `MagicMock(spec=PromptGuardClassifier)` with `loaded = True`); a page exactly at the limit
      classifies; a classifier that raises `PromptGuardBudgetExceededError` yields the same 422; a
      page under the budget classifies every window with a byte-identical response; with the key
      absent or `0` the over-budget page is classified in full and served byte-identically to
      today (the default is today's behaviour); the `/extract` file route's call and tests are
      unchanged.
- [x] `PROMPTGUARD_BUDGET` is a constant in `pipeline/contract.py` beside
      `POLICY_EXCLUDED_ALL_PROVIDERS`; the governance ruling (example 6 by step: shipped off by
      default with the knob, a one-minor-release window named in `docs/releases.md` and the
      Release body, `0` legal after the flip so no MAJOR; `busy` and `extraction_failed` placed
      under ruling (b) with their knobs, not under example 6) is recorded in
      `contract/GOVERNANCE.md` as a `### (<letter>) ` section with a `**Source:**` line, the letter
      is appended to `_RULING_MARKERS` (`tests/test_governance_docs.py:93`) and the count sentence
      at `contract/GOVERNANCE.md:174` is updated; `kit_tools/testing/TESTING_GUIDE.md`'s
      `test_mapping` carries rows for `pipeline/retrieve_limits.py` and `pipeline/config_bounds.py`
      naming the test modules above; `API_GUIDE.md:197-199` states the two reason shapes under
      `content_too_large`.
- [x] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; **nothing** appended to `_EXPECTED_ONE_THREE_ZERO_DIFF` (a reason string is
      free text on `Pipeline422ErrorResponse.reason`, not a property or enum member — R36
      corrected; the docstring line and `--check` are the gate); the four anchor-quoting pages
      refreshed; `uv run python -m scripts.export_contract --check` green.
- [x] `grep -n 'promptguard_budget' docs/configuration.md kit_tools/arch/SECURITY.md
      kit_tools/docs/API_GUIDE.md kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per
      file (R39); `grep -n 'retrieve_budget_unset' docs/releases.md docs/configuration.md` hits
      both (path set: those two files; zero hits today) and the `docs/releases.md` line names the
      release that flips the default; `docs/configuration.md`'s `retrieve:` table preamble names
      the bind-mount procedure and spec 6.
- [x] `sanitizer_revision` rotation measured (revert `orchestrator.py` and `contract.py` each in
      turn, both-reverted control — the `orchestrator.py` measurement is whole-file and attributes
      the signature, the pre-check and the sink to one rotation; accepted, and stated in the
      record; every measurement in this spec starts from a clean tree — `git status --porcelain`
      empty before the revert, stated in the record — round 5) and recorded at the five sites:
      `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`,
      `kit_tools/docs/GOTCHAS.md` (divergence table), `kit_tools/arch/CODE_ARCH.md`.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

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
  at a call site. The in-repo precedent for the shape — a fixed-deadline `async with
  asyncio.timeout(…)` paired with a closed-token WARNING — is `cache.py:405-425`
  (`_attempt_connect`, routed through `_closed_vocabulary_reason` at `:297-305`); this helper reads
  as that pattern, not a new one.
- Scope: stage 3 only, on all three routes. `sanitize_and_structure` (`:160-176`) gains
  `classification_semaphore: asyncio.Semaphore | None = None` and `classification_wait_seconds:
  float | None = None` and wraps its `run_promptguard` call (`:23` of the body) in `_bounded_permit`
  when given one (`None` seconds = wait without a deadline) — **acquired only under the condition
  `run_promptguard` itself classifies on**: the classifier is loaded **and** the tier is not
  `TRUSTED`. `pipeline/stage3_promptguard.py:77-88` returns `skip_reason="trusted_tier"` before the
  `classifier is None` branch and before any inference, so a `trusted_domains` `/retrieve` must
  never queue behind a 256-chunk page for work it will not do; with either condition false nothing
  is acquired and no counter moves. On `False` it takes the classifier-unavailable outcome
  **without calling `run_promptguard` with `classifier=None`** — that call emits the "PromptGuard
  unavailable" line this story forbids for a timeout. The seam is named (round 5): the
  unavailable branch at `pipeline/stage3_promptguard.py:89-125` is a three-way decision —
  fail-closed and tier in {STANDARD, UNTRUSTED} → `INJECTION_DETECTED` with the literal
  flagged-chunk string `"[PromptGuard unavailable — content blocked as precaution]"` and
  `INJECTION_PENALTY`; VERIFIED or fail-open → `SAFE` with a −0.1 penalty; both
  `skip_reason="model_unavailable"` — and every value flows to the wire through stage 4, so it
  must not be duplicated in `orchestrator.py`, where it would drift. Extract it into
  `unavailable_result(tier_value, *, fail_closed) -> PromptGuardResult` in
  `stage3_promptguard.py` (pure — no logging; `run_promptguard` keeps its two `logger.warning`
  lines at `:96` / `:111` and calls the helper), and the timeout path calls the same helper under
  the effective policy. `stage3_promptguard.py` is a `_REVISION_SOURCES` member, so **this
  story's rotation set is three files** — `orchestrator.py`, `stage3_promptguard.py`,
  `contract.py` — each reverted in turn with a both-reverted control; the existing
  `promptguard_state` `unavailable_blocked` / `unavailable_allowed` derivation
  (`pipeline/stage4_structuring.py:96-119`; `tests/test_orchestrator.py:618,653`) is unchanged,
  and a test pins `unavailable_result` against `run_promptguard(classifier=None)`'s result for
  all three tier/flag combinations so the two cannot drift. **And** the timeout site logs one
  WARNING with the closed token `classification_wait_timeout route=<retrieve|search>` and
  nothing caller-derived — the classifier is loaded and busy, not absent, and the existing
  "PromptGuard unavailable" lines would send an operator to the model loader. `/retrieve`
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
  the handler (`:1811-1817`) always passes both. The acquisition guard on `/search` is `classifier
  is not None and classifier.loaded` — `:1014` calls `run_promptguard` directly, never through
  `sanitize_and_structure`, and a warming model is `not None` and not loaded, which is exactly
  when the other routes are also contending (round 5; the same condition `run_promptguard`
  classifies on). One **deadline** per request, not one timeout
  scope: compute `deadline = loop.time() + classification_wait_seconds` before the result loop and,
  **before** each per-result acquisition (`:1014`), test `loop.time() >= deadline` explicitly —
  once it is true, that result and every remaining one take the classifier-unavailable branch
  **unconditionally**, without touching the semaphore. The explicit test is what makes that
  sentence true: `asyncio.Semaphore.acquire()` returns without yielding when a permit is free, so
  `asyncio.timeout(0)` around it never fires, and a permit released mid-loop after the deadline
  would otherwise be taken and the result classified (round-3 finding — the Independent Test's
  held-permit fixture could not see the divergence). Otherwise call `_bounded_permit(semaphore,
  deadline - loop.time())` — `asyncio.timeout` appears in exactly one place, inside
  `_bounded_permit`, scoped to a single `acquire()`, and never around the result loop (an
  `asyncio.timeout` around the loop would raise `TimeoutError` out of whatever is awaiting and
  unwind the loop, the opposite of the stated behaviour). The branch itself is the existing one at
  `:1012-1046` under the **effective** fail-closed value — fail-closed → omitted
  `OMIT_PROMPTGUARD_UNAVAILABLE` (`:1028-1029`); fail-open → served unscanned with
  `suspicious=True`, `unscanned_results += 1`, `promptguard_unavailable = True` (`:1043`) — and
  `search_metrics.classification_wait_timeouts += 1` once. `SearchMetricsSink` and
  `_NullSearchMetrics` (`:747-772`) gain the field; `SearchMetrics` (`retrieval_app.py:875`) and
  `SearchMetricsResponse` (`:488`) mirror it. Until US-005 lands, the effective value is the request
  value (the floor defaults `false`); US-005's `model_copy` makes it the floored value with no change
  here.
- **A wait-timeout body never enters the content cache.** On the fail-open timeout path the body
  carries `promptguard_state == "unavailable_allowed"` (`pipeline/stage4_structuring.py:119`) with
  a −0.1 penalty — not `injection_detected`, tier not `UNTRUSTED`/`BLOCKED` — so Step 8 of
  `run_retrieve_pipeline` (`orchestrator.py:392-410`) would store it under a key whose
  `classifier_loaded=True`. `cache.py:128-133` records why that is exactly the entry the key
  exists to keep out: today an unscanned body can only exist while `classifier_loaded` is
  `False`, and the model loading orphans it; after this story a saturation event lasting
  `promptguard_wait_seconds` would let an in-network caller pin an attacker-chosen unscanned body
  for `cache_ttl_hours` and replay it to every later request, including ones the free permit would
  have classified (round-3 security finding). Step 8 therefore gains the condition `not
  (content.promptguard_state == "unavailable_allowed" and classifier_loaded)` — the combination
  only a wait timeout produces (`classifier_loaded` is the same `classifier is not None and
  classifier.loaded` the fingerprint already computes) — so the absent-classifier fail-open body
  is still cached under its `classifier_loaded=False` key exactly as today. The reasoning is
  written beside `cache.py:128-133`'s note, and `SECURITY.md`'s Security Considerations paragraph
  on the wait-timeout route names the interaction.
- **`SearchResult.suspicious` says what it means, and partial classification is stated.** Today's
  fail-open pass-through serves an unscanned result with `suspicious=True`, `unscanned_results +=
  1` and `promptguard_unavailable = True` (`orchestrator.py:1040-1044`, verified) — all or nothing,
  because the classifier is either loaded or not. This story's per-request deadline makes that
  state **partial** for the first time (two results scanned, eight unscanned), and the field's
  description (`models.py:365-368`, "Whether Stage 2 or Stage 3 flagged this result as
  suspicious") mis-describes a result that was never scanned. No per-result marker is added
  (Decisions Made): the description is rewritten to say the flag is also set for every result
  PromptGuard did not scan — classifier absent, or the classification wait expired — with
  `promptguard_unavailable` saying whether any result in the response was unscanned and
  `unscanned_results` how many, and the consumer rule stated in the description and in
  `kit_tools/docs/API_GUIDE.md`: on `promptguard_unavailable: true`, treat every `suspicious`
  result as unscanned. A description move appends nothing to the diff set (R36 corrected);
  `test_contract_schema_matches_golden` is the gate; spec 8's consumer note names the partial case.
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
  `test_metrics_mirror_round_trips_the_served_body` (`:161`) — **and**
  `tests/test_contract_schema.py:368`, `test_search_metrics_response_1_2_0_field_set_is_pinned_
  exactly`, which asserts `set(SearchMetricsResponse.model_fields)` equals seven literal names
  and does not read the golden, so spec 1 US-004's re-pointing leaves it untouched and it goes
  red on this story: its literal set gains `classification_wait_timeouts` (the `_1_2_0_` in its
  name is left as history, the way US-002 renames `:339`'s pin only for its count); there is no
  retrieve twin (`RetrieveMetricsResponse` has no field-set pin — verified), so the two order
  guards are its gates. Named here as `feature-hardening-provider-bounds.md:458` names it
  (round 5).
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
  raise `promptguard_wait_seconds` or lower `retrieve.max_promptguard_chunks` — and, until spec 7
  measures, a conservative provisional pairing is stated (the arithmetic at an assumed per-window
  latency, with the instruction that an operator who cannot meet it lowers
  `retrieve.max_promptguard_chunks` rather than raising the wait), plus the honest window
  statement (round 5): while `retrieve.max_promptguard_chunks` is `0` the worst-case permit hold
  is bounded only by the 10 MB fetch cap, so an operator who wants the sizing rule to hold sets
  the key explicitly — the `retrieve_budget_unset` WARNING says so — and the shipped default pair
  is recorded under Known risks as one that makes wait timeouts likely under modest concurrency
  on a CPU-bound classifier;
  `kit_tools/arch/SECURITY.md:196-198`'s admission table already claims the classification semaphore
  covers "all routes" — a pre-existing inaccuracy this story makes true — and gains the sentence
  under Security Considerations below; `MONITORING.md` gains both counter rows and the head-of-line
  note (a 256-chunk `/retrieve` holds the single permit longer than any `/search` snippet; a
  wait-timeout spike under mixed traffic correlates with large fetched pages); `TROUBLESHOOTING.md`
  cross-references the counters from the `promptguard_state` `unavailable_*` row.
- Rotates `sanitizer_revision` (`orchestrator.py`, `stage3_promptguard.py` — the
  `unavailable_result` seam — and `contract.py` docstring line), ruling 6 / R32: three files,
  each reverted in turn with a both-reverted control.

**Acceptance Criteria:**
- [x] Every acquisition on all three routes goes through `_bounded_permit`: `grep -n
      'semaphore.acquire()' pipeline/orchestrator.py` returns exactly one match and it is inside
      `_bounded_permit` (a test monkeypatches `_bounded_permit` and shows every acquisition on
      `/retrieve`, `/search` and the `/extract` file route passes through it); `grep -c 'async with
      classification_semaphore' pipeline/orchestrator.py` is 0 (the outer `/extract` acquisition at
      `:539` is gone); the permit count is unchanged after an over-budget refusal, a backstop raise,
      a timed-out wait, a cancelled wait and a late grant (the N+1 tests above).
- [x] The `/extract` file route acquires exactly once, inside `sanitize_and_structure`, with no
      deadline; a test drives the file route under a size-1 semaphore and completes; `/extract`
      output is unchanged (existing tests pass without edits).
- [x] A timed-out wait logs exactly one WARNING containing `classification_wait_timeout
      route=<retrieve|search>` and nothing caller-derived (sentinel assertion); the existing
      "PromptGuard unavailable" line is **not** emitted for a timeout; the timeout path calls
      `stage3_promptguard.unavailable_result`, which is pinned against
      `run_promptguard(classifier=None)` for all three tier/flag combinations (fail-closed
      STANDARD/UNTRUSTED, VERIFIED, fail-open) and is the only producer of the
      `model_unavailable` result in either module (`grep -c 'model_unavailable'
      pipeline/orchestrator.py` is 0 — path set: that file); `/search`'s acquisition guard is
      `classifier is not None and classifier.loaded`; `TROUBLESHOOTING.md`'s `unavailable_*` row
      distinguishes classifier-absent from permit-contention by that line.
- [x] Two concurrent classifications (`/retrieve`+`/retrieve`, `/retrieve`+`/search`,
      `/extract`+`/retrieve`) serialise through the shared semaphore; a cache hit is served without
      acquiring; with `classifier is None` the semaphore is never acquired and the counters stay
      zero; a `trusted_domains` `/retrieve` under a held permit is served at once with
      `skip_reason="trusted_tier"` and no counter moves (tested).
- [x] A fail-open `/retrieve` whose classification wait timed out is served, marked
      `unavailable_allowed`, and **not** written to the content cache (`cache.put` asserted
      uncalled); a following request for the same URL with the permit free reaches `run_promptguard`
      and is classified; the absent-classifier fail-open body is still cached under its
      `classifier_loaded=False` key as today; the reasoning is recorded beside `cache.py:128-133`
      and in `SECURITY.md`.
- [x] `SearchResult.suspicious`'s description states that the flag is also set for results
      PromptGuard did not scan (absent classifier or expired wait) and the consumer rule on
      `promptguard_unavailable: true`; `API_GUIDE.md` carries the rule; the re-created golden pins
      the description (`test_contract_schema_matches_golden`); nothing is appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF` for it.
- [x] A `/retrieve` waiter exceeding `promptguard_wait_seconds` reports `unavailable_blocked`
      (effective fail-closed) or `unavailable_allowed` (effective fail-open), never a 500, and
      `retrieve.classification_wait_timeouts` increments once; `/search` spends one wait budget per
      request, and after it expires every remaining result follows the classifier-unavailable branch
      under the effective policy (omitted, or served marked) **unconditionally** — a test releases
      the permit mid-loop after the deadline and asserts the remaining results are still unscanned —
      with `search.classification_wait_timeouts` incremented once per request; the Independent
      Test's 3-of-10 case asserts `len(results)`, `omitted_by_reason["promptguard_unavailable"]` and
      the fail-open `suspicious` / `promptguard_unavailable` / `unscanned_results` shape.
- [x] `run_search_pipeline`'s two new parameters are defaulted, so no pre-existing call site is
      edited: the story's diff touches none of the 47 / 14 / 12 existing `run_search_pipeline(`
      sites in `tests/test_orchestrator.py`, `tests/test_search_providers.py` and
      `tests/test_brave_provider.py` (asserted by reviewing the diff; sites this story's own tests
      add are expected); one deadline per request is computed from `loop.time()` and
      `asyncio.timeout` occurs exactly once in `pipeline/orchestrator.py` (inside
      `_bounded_permit`); the handler passes both; `SearchMetricsSink`, `_NullSearchMetrics`,
      `SearchMetrics` and `SearchMetricsResponse` carry `classification_wait_timeouts`; both
      counters appear on `/metrics`.
- [x] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; **nothing** appended to `_EXPECTED_ONE_THREE_ZERO_DIFF` (the `/metrics`
      models are not in `_SCHEMA_MODELS` — R36 corrected; the counters' gates are `--check`, the
      two `/metrics` order guards, `tests/test_contract_metrics.py:135`, `:161`, and
      `tests/test_contract_schema.py:368`'s field-set pin, whose literal set gains
      `classification_wait_timeouts`); the four anchor-quoting pages refreshed; `uv run python -m
      scripts.export_contract --check` green.
- [x] `grep -n 'classification_wait_timeouts' docs/configuration.md kit_tools/arch/SECURITY.md
      kit_tools/docs/MONITORING.md kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per
      file; `SECURITY.md` carries the Security Considerations sentence about a saturated semaphore
      and fail-open requests, the interim-posture sentence, and the one-directional `/extract`
      coupling sentence (`grep -n 'no wait timeout' kit_tools/arch/SECURITY.md kit_tools/docs/MONITORING.md`
      hits both); `docs/configuration.md` carries the `promptguard_wait_seconds` /
      `retrieve.max_promptguard_chunks` sizing rule and the provisional pairing; every grep in this
      story names its file list explicitly (no recursive sweep; nothing under `kit_tools/specs/`,
      `tests/golden/` or `kit_tools/.seed_cache/`).
- [x] `sanitizer_revision` rotation measured from a clean tree (revert `orchestrator.py`,
      `stage3_promptguard.py` and `contract.py` each in turn, all-reverted control) and recorded at
      the five sites.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Stages 1, 2 and 4 off the event loop, with fetch and stage 1 under a bounded `/retrieve` admission controller

**Priority:** P1

**Description:** As an operator, I want HTML extraction, the structural scan and the structuring
pass to run off the event loop, with `/retrieve`'s fetch-and-extract work bounded by a second
admission controller that queues without holding bodies behind a queue bounded in depth and bytes,
refuses beyond it with a 422 the route already declares, and lets go of the fetched body
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
request; cancel a queued request while it is still queued and assert `controller.active`,
`controller.queued` and `controller.queued_bytes` are back at their pre-request values and the next
`/retrieve` still fetches; drive the queue-full refusal, a fetch error, an extraction exception, a
cancellation while holding the slot and a cancellation while queued N+1 = 5 times each against
`fetch_concurrency: 1` and assert the same three counters after each and that the next `/retrieve`
still fetches (US-006's discipline, applied to the gate this story adds); assert the
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
  the queue is full, and its `async def acquire() -> bool` / `async def release() -> None` are
  status-free. **Use it as it is (R23 corrected in round 4).** Its `__init__` (`:942-956`) takes
  `(settings: ExtractionSettings, metrics: ExtractionMetrics)` positionally and reads exactly four
  fields off the settings object (`extraction_concurrency`, `admission_queue_depth`,
  `max_queued_upload_bytes`, `max_input_bytes`, `:947-950`). It is constructed at **eight** sites —
  `retrieval_app.py:1218` (lifespan), `:1381` (module-level fallback) and six positional test
  sites: `tests/test_app.py:99`, `:397`, `:446`, `tests/test_orchestrator.py:1498`,
  `tests/test_contract_errors.py:112`, `tests/test_contract_metrics.py:100` (verified by grep) —
  so the round-3 "five primitives" restructure would have broken every test site while claiming
  they pass unedited. Instead: a classmethod `from_retrieve_settings(cls, settings:
  RetrieveSettings, metrics: AdmissionMetrics) -> ExtractionAdmissionController` builds the
  `ExtractionSettings`-shaped view the controller reads —
  `dataclasses.replace(extraction_settings_from_config({}), extraction_concurrency=settings.
  fetch_concurrency, admission_queue_depth=settings.admission_queue_depth,
  max_queued_upload_bytes=settings.max_queued_fetch_bytes, max_input_bytes=DEFAULT_MAX_CONTENT_BYTES
  (`pipeline/stage5_url_audit.py:30`))` — and calls `cls(view, metrics)`; the only edit to the
  class is widening `__init__`'s `metrics` annotation from the concrete `ExtractionMetrics` to the
  `AdmissionMetrics` Protocol US-001 declared (`RetrieveMetrics` is not an `ExtractionMetrics`, and
  pyright strict would reject it), which changes neither arity nor order, so all eight sites are
  untouched. The view is a field carrier for the controller's four reads, **not** a validated
  `extraction:` configuration (round 5): `ExtractionSettings` has no `__post_init__`
  (`pipeline/extraction_limits.py:39-58`; every bound it has lives in the reader's `_bounded_int`
  calls, which `dataclasses.replace` bypasses), so the view may legitimately hold
  `admission_queue_depth` up to 16 against `_MAX_ADMISSION_QUEUE_DEPTH = 4` and
  `max_queued_upload_bytes` up to 160 MB against `MAX_QUEUED_UPLOAD_BYTES` —
  `retrieve_settings_from_config` is the gate that bounded those values; say exactly that in
  `from_retrieve_settings`'s docstring (a criterion). Build `app.state.retrieve_admission =
  ExtractionAdmissionController.from_retrieve_settings(retrieve_settings, retrieve_metrics)` in
  the lifespan plus the module-level fallback beside `:1381-1384`. `run_retrieve_pipeline` took
  it as `admission: AdmissionSlot | None = None` in US-001 — **this story removes the default**
  (`admission: AdmissionSlot`, required; `grep -c 'AdmissionSlot | None' pipeline/orchestrator.py`
  is 0 afterwards — path set: that file), makes the handler pass
  `request.app.state.retrieve_admission`, and switches the shared test helper from
  `admission=None` to the `client` fixture's controller; the pipeline never names the concrete
  class (R23 corrected in round 5).
- Acquire **after the cache read and before the fetch** (a hit never waits; a waiter holds no body),
  **unwrapped — no timer around `acquire()`**. Round 3 proposed `asyncio.timeout(settings.
  admission_wait_seconds)` around the acquisition and a test that the waiter list is empty after a
  timeout; both are withdrawn, because the reviewer ran it: `release()` (`:1000-1007`) does a
  **handoff** — it pops a waiter, sets its result and returns *without* decrementing `_active`, the
  woken waiter inheriting the slot — and `acquire()`'s `except BaseException` (`:991-996`) restores
  accounting only for a waiter still in `_waiters`. A waiter cancelled after its grant (the timer
  firing in that window, or a future already cancelled when `release()` pops it) therefore leaves
  `active == limit` with nobody holding a slot, and at `fetch_concurrency: 1` one occurrence wedges
  `/retrieve` for the life of the process. The controller is not modified (R23 corrected: used as
  it is); its bounded queue depth and reserved queued bytes are the backpressure, a full queue
  refuses at once — `acquire()` returning `False` raises `PipelineError(error="busy",
  reason=RETRIEVE_ADMISSION_QUEUE_FULL)`, counted under `busy_rejections` by the controller — and a
  queued request's wait is bounded by construction (at most `admission_queue_depth /
  fetch_concurrency` slot holds ahead of it, each bounded by the 30 s fetch timeout plus stage 1).
  The handoff race itself is **pre-existing and recorded, not fixed here** — and it is wider than
  a post-grant cancellation (round 5, verified against `:972-1007`): `Task.cancel()` marks the
  awaited future done at once, so a waiter cancelled while still **queued** has `waiter.done()`
  before its own `except BaseException` runs; if the holder's `release()` takes the lock in that
  window it pops the cancelled future, decrements `_queued_bytes`, skips `set_result`, and
  returns **without** decrementing `_active` — the woken task then finds itself gone from
  `_waiters` and restores nothing. Net `active == limit` with nobody holding a slot: at
  `fetch_concurrency: 1` one occurrence wedges `/retrieve` for the life of the process, reachable
  by a plain queued cancellation racing a normal release, not only by a post-grant cancellation.
  Latent on `/extract` (the route ships disabled) and reachable on `/retrieve` only by task
  cancellation — server shutdown; Starlette does not cancel a handler task when an HTTP client
  disconnects — never by a timer, because this story adds none. It goes into
  `kit_tools/docs/GOTCHAS.md` and `SECURITY.md` as an accepted residual **described that way**
  with the fix direction (make the handoff idempotent: `release()` decrements and the woken
  waiter re-increments under the lock) and into Open Questions for spec 6's envelope work,
  flagged for the epic wrapper. Hold the slot through stage 1 (HTML extraction,
  or US-003's PDF worker), then `await admission.release()` in `finally` — every path, including a
  fetch error, an extraction exception and cancellation while holding; a single delivered
  `CancelledError` does not interrupt an `await` inside `finally` (asyncio delivers it once) and
  the lock is uncontended on the release path, so no `asyncio.shield` is needed — and the
  cancellation tests assert the controller's three counters afterwards, not merely that no
  exception escaped. **Release the body with the slot**: `build_retrieved_content` already takes
  the three post-stage-1 values as keyword-only scalars (`pipeline/stage4_structuring.py:219-230`:
  `final_url`, `redirect_chain`, `domain_changed_on_redirect`, beside `sanitization`, which carries
  the text) — it has never taken `fetch_result`; only the call site (`orchestrator.py:385-393`)
  reads them off `fetch_result` at call time (also read at `:358`, `:376`). So read the three into
  locals before the release, then `del fetch_result` and the decoded `html_text` local (`:354`, a
  second `str`-sized copy of the body) — **no signature change, and `pipeline/stage4_structuring.py`
  is untouched**: it is a `_REVISION_SOURCES` member (`pipeline/sanitizer_revision.py:12-21`), and
  editing it would add an unmeasured third hashed file to a story whose rotation criterion names
  two. A request parked on the classification permit (US-006, up to `promptguard_wait_seconds`)
  then holds at most the extracted text (≤ `max_extracted_characters(retrieve.
  max_promptguard_chunks)` characters).
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
  `busy`". The one reason literal, `RETRIEVE_ADMISSION_QUEUE_FULL = "admission_queue_full"`, sits
  beside `PROMPTGUARD_BUDGET`. `Pipeline422ErrorResponse.error`'s description (`retrieval_app.py:
  723`, "The fetch and URL-validation codes arrive on /retrieve, the searxng_* codes and
  search_unavailable on /search") is part of the frozen document and goes stale here: rewrite it
  to add that `busy` arrives on `/retrieve` only (US-003 adds `extraction_failed` to the same
  sentence), and say in the window block that the `/search` 422 enum widens as a consequence of
  the shared model.
- **The memory sentence, honestly.** At most `retrieve.fetch_concurrency` bodies are alive during
  fetch and stage 1; a queued request holds nothing; the queue is bounded in depth and reserved
  bytes; a request waiting on the classification permit holds only its extracted text. What is
  **not** bounded is the population of classification waiters: `uvicorn` runs with no
  `--limit-concurrency` (`Dockerfile:227`) and both middlewares are `/extract`-gated, so admission
  bounds the rate through stage 1, not the number of requests past it; with bodies released, each
  such waiter costs at most `max_extracted_characters(256)` ≈ 459 KB of text for at most
  `promptguard_wait_seconds`. Document exactly that in the `retrieve:` table, with the stage-1
  peak stated honestly: `fetch_concurrency × (10 MB body + its decoded `str` + the
  `ExtractionResult`'s `raw_text` and `main_content`)` — `orchestrator.py:352-355` holds the body
  and its decoded copy simultaneously, so an in-flight page is three to five times the body term
  while stage 1 runs, and the 10 MB cap is the body term only — plus `arrival rate ×
  promptguard_wait_seconds × ≤ 0.5 MB` for waiters; name `--limit-concurrency` as the envelope
  knob spec 6 owns, and add the measurement as a criterion (below). `asyncio.to_thread` is
  uncancellable and the default executor is shared with stage 3 (`min(32, cpu + 4)` threads); the
  slot is what keeps hostile pages from starving the classifier. A wall clock on HTML extraction
  is deliberately **not** added (the 30 s fetch timeout and 10 MB cap bound the input); spec 6
  sizes the slot. Worst-case queue latency is a derived number in the table:
  `admission_queue_depth / fetch_concurrency × max(fetch timeout 30 s, PDF wall clock)`.
- **The counter name is kept, and the row disambiguates.** After this story `/retrieve` has two
  gates and `retrieve.semaphore_saturation` counts the admission controller, not the
  classification semaphore (that one is `retrieve.classification_wait_timeouts`). The name stays —
  the controller increments `metrics.semaphore_saturation` by attribute (R23: used as it is), and
  `extraction.semaphore_saturation` counts the same admission class on `/extract`, so the two
  routes read alike — and the `MONITORING.md` row says in its first sentence which gate each
  counter watches, so an operator reading a climbing `semaphore_saturation` is sent to
  `fetch_concurrency`, not to PromptGuard.
- **The admission tests own their controller.** The module-level fallback is one process-wide
  instance shared by every lifespan-free test in the session (`retrieval_app.py:1381-1384` is
  exactly that for `/extract`), and this story's tests deliberately saturate it: each constructs
  its own controller or resets `app.state.retrieve_admission` in an autouse fixture (the shape the
  suite already uses for `app.state`), and asserts `active`, `queued` and `queued_bytes` back at
  zero at the end, so an off-by-one cannot present as an unrelated downstream timeout.
- Contract-test edits for the `busy` member: `tests/test_contract_errors.py:241` (6 → 7 here, 7 → 8
  after US-003), the intersection assertion and its `10 + 6 + 3` comment at `:244-247`
  (`{"content_too_large"} == EXTRACT & RETRIEVE` gains `busy`), `tests/test_contract_schema.py:339`'s
  pin (renamed from `…_nine_members` to its new count), `RetrieveErrorCode`'s docstring
  (`contract.py:250-258`), and the three old-premise sites above. The second refusal this story adds
  (load-triggered `busy` on traffic served today) is a capacity refusal recorded under ruling (b)
  — a new 422 code — with `admission_queue_depth` (up to 16) and `max_queued_fetch_bytes` (up to
  160 MB) as the operator's knobs; `retrieve.fetch_concurrency` is pinned 1–1 until spec 6 and is
  **not** listed as a knob (round 5 corrects round 4's "under US-001's example-6 ruling" and its
  three-knob list); named in the consumer note spec 8 carries with the honest shipped-default
  statement — single flight, at most four queued (three by bytes), a 422 for the fifth concurrent
  request.
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
  three admission rows, the honest memory sentence, the queue-latency number and the disk note;
  `MONITORING.md` gains `retrieve.semaphore_saturation` / `busy_rejections` rows (each naming its
  gate); `API_GUIDE.md` and `TROUBLESHOOTING.md`'s `/retrieve` 422 tables gain the `busy` row with
  its reason; the `Pipeline422ErrorResponse.error` description is rewritten as above.
- Rotates `sanitizer_revision` (`orchestrator.py`, `contract.py`), ruling 6 / R32.

**Acceptance Criteria:**
- [x] `extract_html`, `scan_structural` and `structure_sanitization_result` are invoked via
      `asyncio.to_thread` on both routes; no synchronous call to them remains in
      `run_retrieve_pipeline` or `sanitize_and_structure`; a test asserts each runs on a
      non-event-loop thread.
- [x] Five sequential `/health` requests each complete in under 1 s while a fetched-page
      `extract_html` is blocked (the `:958` shape, connected cache).
- [x] `ExtractionAdmissionController`'s constructor keeps its `(settings, metrics)` arity and order
      (the `metrics` annotation is the `AdmissionMetrics` Protocol), `from_retrieve_settings` builds
      `/retrieve`'s instance, and the eight construction sites are untouched (`grep -n
      'ExtractionAdmissionController(' retrieval_app.py tests/*.py` lists the same eight lines as
      at story start — path set: those files; today `retrieval_app.py:1218`, `:1381`,
      `tests/test_app.py:99`, `:397`, `:446`, `tests/test_orchestrator.py:1498`,
      `tests/test_contract_errors.py:112`, `tests/test_contract_metrics.py:100`, verified);
      `/extract`'s admission behaviour and tests are unchanged; `/retrieve` acquires
      `app.state.retrieve_admission` after the cache read and before the fetch with **no timer**
      around `acquire()` (`grep -c 'asyncio.timeout' pipeline/orchestrator.py` is 1 — the
      `_bounded_permit` site), releases it via `await admission.release()` in `finally` on every
      path (success, fetch error, extraction exception, cancellation while holding — each tested
      N+1 = 5 times with `active`, `queued` and `queued_bytes` asserted back at their pre-request
      values and the next `/retrieve` fetching); a cancellation while queued — with the cancelled
      task allowed to run its `except BaseException` before the holder releases, an ordering the
      test pins explicitly because the queued-cancel-racing-release interleaving is the recorded
      residual and is not deterministic — leaves the queue empty with the counters restored; a
      queued `/retrieve` has not fetched; a request beyond the depth
      bound and one beyond the byte bound (under `max_queued_fetch_bytes: 10485760`, depth 4) are
      each refused 422 `busy` / `admission_queue_full` within one loop turn;
      `retrieve.semaphore_saturation` and `retrieve.busy_rejections` increment and appear on
      `/metrics`; the admission tests construct their own controller or reset
      `app.state.retrieve_admission` per test and assert the counters at zero afterwards; the
      handoff residual is recorded in `GOTCHAS.md` and `SECURITY.md` — as reachable by a queued
      cancellation racing a release, not only post-grant — with its fix direction;
      `from_retrieve_settings`'s docstring says its `ExtractionSettings` view is a field carrier
      the `retrieve:` reader already bounded, not a validated `extraction:` configuration;
      `admission` is a required `AdmissionSlot` after this story (no `| None` on it in
      `pipeline/orchestrator.py`) and the handler passes `app.state.retrieve_admission`.
- [x] The fetched body is released with the slot: `build_retrieved_content`'s signature and
      `pipeline/stage4_structuring.py` are unchanged (`git diff --stat` shows no change to it — this
      story touches no hashed file other than `orchestrator.py` and `contract.py`); the three
      scalars are read into locals and `fetch_result` and `html_text` are deleted before the
      classification wait; a weak reference to a `bytearray`-subclass body is dead while the
      request waits on a held classification permit; the `retrieve:` table states the stage-1 peak
      honestly (body + decoded copy + extraction result per slot) and the waiter term, and names
      `--limit-concurrency` as spec 6's knob.
- [x] `pipeline_error_handler` is route-aware: `/retrieve`'s admission refusal is 422 with a
      `Pipeline422ErrorResponse` body and no `sanitizer_revision`; `/extract`'s middleware refusal is
      429 with a byte-identical `RateLimit429Response`; `tests/test_contract_errors.py:567` asserts
      both at runtime; the three old-premise docstrings/comments are rewritten.
- [x] `busy` is a member of `RetrieveErrorCode`; `RETRIEVE_ADMISSION_QUEUE_FULL` is a constant in
      `pipeline/contract.py`; `Pipeline422ErrorResponse.error`'s description names `busy` as
      `/retrieve`-only at 422;
      `tests/test_contract_errors.py:241` and `:244-247` (and its comment),
      `tests/test_contract_schema.py:339`'s pin and `RetrieveErrorCode`'s docstring are updated; the
      deduplicated `ERROR_CODES` total stays eighteen (`:234`); `retrieve.busy_rejections`'
      description does not say 429; the refusal is recorded under ruling (b) with its two operator
      knobs, `fetch_concurrency` named as pinned rather than as a knob.
- [x] `/extract` responses on the existing fixture corpus are byte-identical before and after
      (existing tests unchanged).
- [x] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; `Pipeline422ErrorResponse.error[enum]=busy` appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF` — one path, covering the `/search` 422 as well through the
      shared model — while the two counters (`/metrics` models are not in `_SCHEMA_MODELS`), the
      reason literal and the description rewrite append nothing (R36 corrected); the four
      anchor-quoting pages refreshed; `--check` green.
- [x] `grep -n 'busy_rejections\|admission_queue_full' docs/configuration.md
      kit_tools/arch/SECURITY.md kit_tools/docs/MONITORING.md kit_tools/docs/API_GUIDE.md
      kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per file (path set: those five
      files; zero hits today); the `SECURITY.md:198` row for `/retrieve` no longer reads "none | not
      applicable"; the queue-latency number is in the `retrieve:` table; the `MONITORING.md`
      `semaphore_saturation` row names the admission gate.
- [x] `sanitizer_revision` rotation measured (revert-and-reproduce) and recorded at the five sites.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Fetched PDFs parsed in the rlimited worker, with a typed `/retrieve` failure vocabulary

**Priority:** P1

**Description:** As an operator, I want fetched PDFs parsed in the same spawned, rlimited worker
uploads use — from a temp file in a named spool directory that never outlives the request — and
every PDF failure, including a failed spool write, to be a coded 422 instead of the 500 it is today,
so a hostile PDF can neither stall the service nor crash the request.

**Independent Test:** With `fetch_url` patched to return `application/pdf` bytes and
`extract_pdf_in_subprocess` patched to record its arguments, assert it is called with a `Path` under
`pipeline.pdf_subprocess.spool_dir()` (the process-private `forage-spool-<uid>` directory) and
`app.state.extraction_settings`, that the file has mode `0600` while the worker runs, that
`spool_dir()` created the directory with mode `0700` owned by the process on first use (the
lifespan calls it once, so a bad directory refuses boot; a lifespan-free `httpx.ASGITransport`
client gets it on its first spool) and refuses — at boot and at use time — when the path exists as
a symlink, a non-directory, another user's directory, or one with any group or other bit set
(tested with a pre-created 0755 directory, which is refused, never repaired), and that no
`forage-retrieve-*` file
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
  `spool_dir() -> Path` returning `Path(tempfile.gettempdir()) / f"forage-spool-{os.geteuid()}"` —
  resolved **per call**, not at import, so a `TMPDIR` set after import still applies and the tests'
  monkeypatch seam is `tempfile.gettempdir` (a module-level constant would freeze `/extract`'s
  upload path at import, a behaviour change the story must not make). **The directory is created
  lazily and verified on every call, never repaired (R23 corrected in round 4).** `spool_dir()`
  does `path.mkdir(mode=0o700)` with `exist_ok=False` inside a `try` — umask can only clear bits,
  so the directory is never wider than 0700 at any instant, which is what closes the
  mkdir-then-chmod window round 3's `mkdir(exist_ok=True)` + `chmod` left open (a dirfd opened in
  that window survives the chmod); on `FileExistsError` it runs `st = os.lstat(path)` (never
  `stat`, which follows a planted symlink) and raises `SpoolDirectoryError(OSError)` with a closed
  token — `spool_dir_symlink` if `stat.S_ISLNK(st.st_mode)`, `spool_dir_not_directory` if not
  `stat.S_ISDIR`, `spool_dir_foreign_owner` if `st.st_uid != os.geteuid()`, `spool_dir_mode` if
  `st.st_mode & 0o077` — and **never `chmod`s** an existing directory: repair-then-verify would make
  the pre-created-0755 test pass by repairing what it should refuse, and a directory already
  present with the wrong mode is evidence, not a state to fix. The lifespan calls `spool_dir()`
  once and re-raises `SpoolDirectoryError` as `RetrieveConfigurationError(str(exc))` so boot
  refusals keep one closed vocabulary; at use time the same check re-runs on every call (the
  round-3 note: a directory verified at boot inside a world-writable, non-sticky `TMPDIR` can be
  removed and re-created by another local user afterwards — the per-call `lstat` re-establishes
  owner and mode before each spool, and `docs/configuration.md` states the parent requirement:
  `TMPDIR` must be sticky or not writable by other users), and a use-time failure on `/retrieve`
  reaches the `pdf_spool_error` row through the existing `except OSError`. "Process-private" is
  therefore enforced by construction: the child re-opens the spool file by path inside a directory
  nobody else can enter, which closes the re-open window without depending on the sticky bit of
  `/tmp`. `_spool_upload` (`retrieval_app.py:1127-1152`) passes the same `dir=spool_dir()` (its
  `poppy-extract-` prefix is pre-existing and out of scope), so both routes' spool files share the
  private directory — and its two lifespan-free callers pass **unedited** because the directory is
  created on first use: `tests/test_app.py:366-387`
  (`test_bounded_upload_read_rejects_file_over_limit`, a direct call with no lifespan) and `:411`
  (`test_extract_benign_text_over_classification_budget_is_not_injection`, a real multipart upload
  through the plain `client` fixture, whose `httpx.ASGITransport` fires no lifespan events —
  `retrieval_app.py:1380-1383` documents exactly that hazard). A `/extract` spool failure
  propagates as it does today (out of scope).
  `tempfile.NamedTemporaryFile(prefix="forage-retrieve-", dir=spool_dir(), delete=False)`,
  `os.fchmod(fd, 0o600)`, write, close, call the path variant, and `path.unlink(missing_ok=True)`
  in a `finally` that also covers `asyncio.CancelledError` and a worker exception; an `OSError`
  from `spool_dir()`, the create or the write is mapped by the caller to the `pdf_spool_error` row
  (the file, if created, is still unlinked). `TMPDIR` is an operator input: `docs/configuration.md`
  states the requirement (tmpfs recommended; the private subdirectory is created for you; the
  parent must be sticky or not other-writable) **and why**, in the confidentiality dimension too —
  the spool file holds fetched third-party content, possibly from an internal or authenticated
  URL the agent was asked to read; it is 0600 inside a 0700 directory and unlinked on every normal
  exit path; a tmpfs `TMPDIR` keeps fetched content off durable storage; orphans after a SIGKILL
  are content-bearing — beside the **combined**
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
  rewritten; `Pipeline422ErrorResponse.error`'s description (`retrieval_app.py:723`) adds
  `extraction_failed` to its `/retrieve`-only list beside US-002's `busy`, and the `/search` 422
  enum widens through the shared model). Reasons are token-shaped constants beside
  `PROMPTGUARD_BUDGET` following
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
- The spool row is the one operational failure of the **host** in the table (ENOSPC, EACCES, a
  read-only or vanished temp dir), and `RetrieveMetrics.record_error` keys `retrieve.errors` by
  `PipelineError.error` alone (`retrieval_app.py:915-917`), so on `/metrics` it is
  indistinguishable from an encrypted-PDF caller. Round 5: the spool row logs one WARNING with
  the closed token `retrieve_spool_error` (nothing path- or content-derived) so a log alert can
  fire on host failure; `MONITORING.md`'s row states the alert (any occurrence — it is a host
  fault, not an input); a per-reason `/metrics` map is deferred (Known risks).
- Docs: `kit_tools/docs/MONITORING.md:163` (`retrieve.errors` key list) gains `extraction_failed`
  (and `busy` from US-002 if not yet there) and the `retrieve_spool_error` alert sentence; `kit_tools/docs/API_GUIDE.md:197-199` and `:421`'s
  `/retrieve` 422 table and `kit_tools/docs/TROUBLESHOOTING.md:168`'s table gain the row with its
  reasons and the ceiling sentence; `docs/configuration.md` gains the spool-directory requirement,
  the disk ceiling and a latency note (a fetched PDF now pays the worker's process-spawn cost on
  first fetch; repeat fetches of the same URL are served by the content cache);
  `kit_tools/arch/SECURITY.md:198`'s `/retrieve` row (edited by US-002) says PDF parsing runs in the
  worker under `/extract`'s rlimits; `CODE_ARCH.md` says both routes parse PDFs in the worker.
- Rotates `sanitizer_revision` (`orchestrator.py`, `contract.py`), ruling 6 / R32.

**Acceptance Criteria:**
- [x] Fetched `application/pdf` bodies are parsed by `extract_pdf_bytes_in_subprocess` →
      `extract_pdf_in_subprocess` inside `asyncio.to_thread`, with `app.state.extraction_settings`
      (asserted argument), inside the admission slot; `pipeline.pdf_subprocess.spool_dir()` resolves
      per call under `tempfile.gettempdir()`, creates the `forage-spool-<uid>` directory with
      `mkdir(mode=0o700, exist_ok=False)` on first use and, when it exists, verifies it with
      `os.lstat` (symlink, non-directory, foreign owner, any group/other bit → refusal with a closed
      token; no `chmod` anywhere in the function — `grep -c 'chmod' pipeline/pdf_subprocess.py`
      counts only the `os.fchmod(fd, 0o600)` on the spool file); the lifespan's call refuses boot
      with `RetrieveConfigurationError` on a pre-created 0755 directory (refused, not repaired) and
      on a symlink; `_spool_upload` uses `dir=spool_dir()` and `tests/test_app.py:366-387` and
      `:411` pass unedited; the `except` chain is most-specific-first (a
      `PDFClassifiableTextLimitError` yields the `content_too_large` row, never `extraction_failed`).
- [x] The spool file has mode `0600` while the worker runs and no `forage-retrieve-*` file remains
      under the spool directory after success, after each table row, after a simulated kill, after a
      spool `OSError` and after cancellation (a test scans the directory after each).
- [x] Each row of the five-row table yields the stated `error` and `reason` as a 422, never a 500;
      the spool row logs exactly one WARNING containing `retrieve_spool_error` (sentinel assertion)
      and no other row does; a fetched-PDF `/retrieve` with `extract_route_enabled` at its default
      is 200; a fetched PDF
      over `max_extracted_characters(extraction.max_promptguard_chunks)` is refused
      `content_too_large` / `promptguard_budget` while the same text as HTML is served under
      `retrieve.max_promptguard_chunks`.
- [x] `RetrieveErrorCode` gains exactly `extraction_failed` (beside US-002's `busy`); the four reason
      literals are constants in `pipeline/contract.py` with the same-literal comment;
      `tests/test_contract_errors.py:241` (→ 8), `:245-247` and its comment, `:233` (stays 18),
      `tests/test_contract_schema.py:339`'s renamed pin and `RetrieveErrorCode`'s docstring are
      updated.
- [x] `ExtractionAdmissionMiddleware`, the worker's IPC vocabulary and `/extract`'s behaviour are
      untouched (existing tests pass without edits, the two lifespan-free spool callers named
      above included); `SECURITY.md:50` still holds; `Pipeline422ErrorResponse.error`'s description
      names `extraction_failed` as `/retrieve`-only.
- [x] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; `Pipeline422ErrorResponse.error[enum]=extraction_failed` appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF` (one path, both routes' 422); the reason literals and the
      description rewrite append nothing (R36 corrected); the four anchor-quoting pages refreshed;
      `--check` green.
- [x] `grep -n 'pdf_spool_error' docs/configuration.md kit_tools/docs/MONITORING.md
      kit_tools/docs/API_GUIDE.md kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per
      file and `grep -n 'retrieve_spool_error' kit_tools/docs/MONITORING.md` hits the alert row; `docs/configuration.md` carries the spool-directory requirement, the combined disk
      footprint, the orphan-on-SIGKILL sentence and the spawn-latency order of magnitude
      (`grep -n 'forage-spool\|spawn' docs/configuration.md` hits — path set: that file; zero hits
      today), the parent-directory (sticky or not other-writable) requirement and the
      confidentiality sentence (`grep -n 'sticky' docs/configuration.md` hits); `CODE_ARCH.md` and
      `SECURITY.md:198` carry the worker sentences.
- [x] `sanitizer_revision` rotation measured (revert `orchestrator.py` and `contract.py` each in
      turn, both-reverted control) and recorded at the five sites.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

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
- [x] `ContentCache.get` returns `None` for a value that fails `RetrievedContent` validation or JSON
      parsing, deletes the key and increments `corrupt_entries`; the next request repopulates it.
- [x] The WARNING carries the closed token `cache_entry_corrupt` and the key digest only; a
      sentinel string in the corrupt value appears in no log record.
- [x] No `/retrieve` request returns 500 for any cache content (a test drives three malformed shapes:
      invalid JSON, wrong schema, wrong `retrieved_at` type).
- [x] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; nothing appended to `_EXPECTED_ONE_THREE_ZERO_DIFF` (`CacheMetricsResponse`
      is not a `_SCHEMA_MODELS` member — R36 corrected; the gates are `--check` and the `/metrics`
      order guards `tests/test_contract_metrics.py:135`, `:161`); the four anchor-quoting pages
      refreshed; `--check` green; `/metrics` serves the counter.
- [x] `grep -n 'corrupt_entries\|cache_entry_corrupt' kit_tools/docs/MONITORING.md
      kit_tools/docs/TROUBLESHOOTING.md` returns at least one hit per file;
      `grep -n 'not authenticity' kit_tools/arch/SECURITY.md` hits the residual sentence.
- [x] `sanitizer_revision` rotation (`contract.py` docstring line) measured and recorded at the five
      sites.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

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
- [x] `promptguard_fail_closed_floor` and `promptguard_threshold_ceiling` are read at boot with the
      defaults above; a non-boolean floor or an out-of-range ceiling refuses boot with a
      closed-vocabulary `RetrieveConfigurationError` message.
- [x] With the floor `true`, `/retrieve` and `/search` behave fail-closed regardless of the request
      flag for STANDARD- and UNTRUSTED-tier content; with the ceiling below a `/retrieve` request's
      threshold, classification uses the ceiling; with the defaults, behaviour and every existing test
      are unchanged; the VERIFIED and trusted-tier exemptions behave exactly as today (tested).
- [x] The value reaching `cache_policy_fingerprint` is the effective value (a test asserts a
      fail-open entry is not served to a floored request and that the fingerprint inputs equal the
      effective values); the `model_copy` update keys are pinned against `model_fields`.
- [x] `RetrievedContent` carries both `effective_*` fields and `SearchResponse` carries
      `effective_promptguard_fail_closed`, stamped by the handler on every 200 response, cache hit
      or miss (a test serves a hit under a floored request and asserts the floored values; a
      hand-seeded `FakeStorage` entry without the fields — the only way such an entry can exist,
      since every story here rotates the cache key — is served with the stamped values); 422 bodies
      carry no policy fields.
- [x] Window mechanics (R36): the `* ``1.3.0`` — …` docstring line is appended; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; the three field paths — `RetrievedContent.effective_promptguard_fail_closed`,
      `RetrievedContent.effective_promptguard_threshold`,
      `SearchResponse.effective_promptguard_fail_closed` — appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF` (added properties: the half of R36 that does append); the four
      anchor-quoting pages refreshed; `--check` green.
- [x] The consumer note spec 8 carries states that a caller sending `promptguard_fail_closed:
      false` is, from this spec forward, exposed to an unscanned-but-marked response whenever the
      classification permit is contended for longer than `promptguard_wait_seconds`, that
      `promptguard_state` / `suspicious` / `promptguard_unavailable` / `unscanned_results` are the
      per-response signals, and that `promptguard_fail_closed_floor: true` is the operator-side
      control (`config.yaml`-only until spec 6's bind-mount procedure) — one sentence in the
      existing deliverable, pinned by a grep on the note's file once spec 8 names it.
- [x] The field descriptions, `API_GUIDE.md` rows and `SECURITY.md` sentence state that the fields
      report the policy applied, not whether content was scanned, and name both exemptions
      (`grep -n 'trusted_tier\|VERIFIED' kit_tools/arch/SECURITY.md kit_tools/docs/API_GUIDE.md`
      hits the new sentences).
- [x] `docs/configuration.md`'s two rows carry their Purpose text and the deployed-container
      cross-reference, and both `docs/configuration.md` and `kit_tools/arch/SECURITY.md` state the
      negative scope: `promptguard_threshold_ceiling` bounds `/retrieve` only — `/search` classifies
      at a fixed 0.85 until `SearchRequest.promptguard_threshold` lands (spec 3 US-005) — and the
      floor does not reach `/extract` (permanently fail-closed) or the trusted-tier / VERIFIED
      exemptions (`grep -n 'retrieve only' docs/configuration.md kit_tools/arch/SECURITY.md` hits).
- [x] `sanitizer_revision` rotation (`contract.py` docstring line) measured and recorded at the five
      sites.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- A fetched page that yields zero windows classifies nothing and reports `promptguard_state ==
  "scanned"` as today (US-001).
- A page exactly at `max_extracted_characters(retrieve.max_promptguard_chunks)` classifies; one
  character over is refused `content_too_large` / `promptguard_budget`; there is no byte limb on
  this route (the character bound already bounds bytes ×4) (US-001).
- A page that was served on `main` (over 256 chunks, classified in full) is still served at the
  shipped default `0` for one release, with the boot WARNING naming the coming default; once the
  default flips to 256 it is refused unless the operator raises the key or keeps `0` (US-001).
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
- A `trusted_domains` `/retrieve` under a held permit is served at once with
  `skip_reason="trusted_tier"`; nothing is acquired and no counter moves (US-006).
- A fail-open wait-timeout body is served, marked `unavailable_allowed`, and never cached; the next
  request for the URL classifies; an absent-classifier fail-open body is cached under its
  `classifier_loaded=False` key as today (US-006).
- On `/search`, once the deadline has passed a freed permit is not taken: the remaining results are
  unscanned even if the permit is released mid-loop (US-006).
- A queued `/retrieve` holds no body; a request beyond `admission_queue_depth` or
  `max_queued_fetch_bytes` (the byte bound binds first at the shipped defaults — three 10 MB
  reservations fit in 30 MB, the depth is 4; the byte bound is tested under
  `max_queued_fetch_bytes: 10485760`, the depth bound under `admission_queue_depth: 1` — round 5
  corrected the inverted sentence) is refused `busy` / `admission_queue_full`; a queued request
  cancelled before its grant, with the cancellation processed before any release, is dequeued
  with the counters restored; a queued waiter whose cancellation races the holder's release, or
  one cancelled after its grant, is the recorded pre-existing handoff residual, reachable only by
  task cancellation (US-002).
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
  orphan file; a spool directory that exists with the wrong mode or owner, or as a symlink, refuses
  boot — and refuses the spool at use time if it changed after boot, because the check runs on
  every call (US-003).
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
- Widening `retrieve.fetch_concurrency` past 1, and fixing `ExtractionAdmissionController`'s
  post-grant cancellation handoff (recorded residual) — spec 6's envelope work; both flagged for
  the epic wrapper.
- A per-result "unscanned" marker on `SearchResult` — declined on the record (Decisions Made); the
  corrected `suspicious` description and the consumer rule are the signal.
- A timer around the admission acquisition (`admission_wait_seconds`) — withdrawn in round 4; the
  queue is bounded by construction.

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
  `tempfile.gettempdir()`, created 0700 on first use (the lifespan's call is the boot check) and
  verified with `os.lstat` on every call; disk cost per fetched PDF is at most the 10 MB fetch cap;
  `TMPDIR` is an operator input documented as such, including the parent-directory requirement.
- No timer bounds the admission queue; its bound is the queue depth, the reserved bytes and the
  slot hold, and the controller is used exactly as written.
- The seven `retrieve:` / policy keys are `config.yaml`-only until spec 6's bind-mount and env
  procedure lands — an interim state the epic wrapper tracks (the shipped `extraction:` block has the
  same shape today). During that interim the budget key's `0` default is what keeps the
  tightening compatible (example 6 step 1); the bind-mount is not what provides the window.
- No new dependency: `asyncio`, `tempfile`, `os.fchmod` are stdlib.

## Technical Considerations

- **Rotations (R32).** All six stories rotate `sanitizer_revision`: US-001 (`orchestrator.py`,
  `contract.py`), US-006 (`orchestrator.py`, `stage3_promptguard.py` — the `unavailable_result`
  seam, round 5 — `contract.py`), US-002 (`orchestrator.py`, `contract.py`), US-003
  (`orchestrator.py`, `contract.py`), US-004 (`contract.py`), US-005 (`contract.py`). Each
  records its own before/after at the five sites (ruling 6); a story touching two or three hashed
  files reverts each in turn with an all-reverted control; the control baseline is the previous
  story's post-state, so rotations are measured in execution order; every measurement starts
  from a clean tree (`git status --porcelain` empty before the revert, stated in the record).
- **Wire changes in the window.** `content_too_large` reason `promptguard_budget` (US-001);
  `retrieve.classification_wait_timeouts`, `search.classification_wait_timeouts` (US-006);
  `retrieve.semaphore_saturation`, `retrieve.busy_rejections`, `busy` on `/retrieve` (422) with
  reason `admission_queue_full` — and, through the shared `Pipeline422ErrorResponse`, on the
  `/search` 422 enum (US-002); `extraction_failed` on `/retrieve` with four reasons, likewise on
  the `/search` enum (US-003); `CacheMetrics.corrupt_entries` (US-004);
  `effective_promptguard_fail_closed` on both responses and `effective_promptguard_threshold` on
  `RetrievedContent` (US-005); description rewrites of `Pipeline422ErrorResponse.error` (US-002,
  US-003) and `SearchResult.suspicious` (US-006). Each follows the R36 block and says which half
  applies — diff-set entries: `Pipeline422ErrorResponse.error[enum]=busy`,
  `…=extraction_failed`, and US-005's three field paths; everything else (counters, reasons,
  descriptions) is gated by `--check`, the `/metrics` order guards or golden equality; the epic
  wrapper's window list is updated by the parent (it still names two `busy` reasons).
- **Gate scopes.** Admission controller: cache read → (acquire — the controller's bounded queue,
  no timer) → fetch → stage 1 → (release slot **and** body). Classification
  semaphore: stage 3 only on all three routes, acquired inside `sanitize_and_structure` via
  `_bounded_permit` (`/extract` passes no deadline). Stages 2 and 4: `asyncio.to_thread`, bounded by
  neither gate. The three routes serialise behind `classification_concurrency`; `/extract`'s wait is
  untimed and uncounted (recorded). The worst-case permit hold is one 256-chunk `/retrieve`
  classification with the key set — bounded only by the 10 MB fetch cap while the key is `0` —
  which is what `promptguard_wait_seconds` (30 s) must exceed — the sizing rule in
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
  text and in time to `promptguard_wait_seconds`) until spec 6 sets `--limit-concurrency`. Two
  round-4 additions: a body left unscanned by a wait timeout is never written to the content
  cache (otherwise a momentary saturation would become a durable, attacker-chosen classifier
  bypass for `cache_ttl_hours`), and `ExtractionAdmissionController`'s post-grant cancellation
  handoff is a recorded residual — no timer touches the acquisition, so it is reachable only by
  task cancellation.
- **Event-loop proof.** The responsiveness test follows `tests/test_app.py:958-993` (real
  `ASGITransport` client over the real lifespan, connected cache, `threading.Event` release); a
  mocked loop proves nothing.
- **Signatures.** `run_retrieve_pipeline` gains four required keyword-only parameters plus
  `admission: AdmissionSlot | None = None` in US-001 (16 pre-existing test sites, once, through
  one helper), and US-002 makes `admission` required; `run_search_pipeline` gains two defaulted
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
  status choice at `:1424`, and the middleware's 429 is minted at `:1112`. Verified in round 4:
  `ExtractionAdmissionController.acquire` is `:972` and `release` `:998` (both `async def`), the
  handoff is `:1000-1007`, the constructor sites are `:1218` and `:1381` plus six test sites,
  `_spool_upload` is `:1127-1152` with no `dir=`, `Pipeline422ErrorResponse.error` is `:723`, and
  `SearchResult.suspicious` is `models.py:365-368`.
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

### US-005 implementation (2026-09-22)

- Reused US-001's validated `RetrieveSettings` reader without changing its boot
  semantics. Added exact closed-message boot cases for mistyped floors and
  mistyped/out-of-range/non-finite ceilings, plus accepted endpoints `0` and `1`.
- `_apply_promptguard_policy` in `retrieval_app.py` resolves both routes' flag and
  only `/retrieve`'s threshold by copying the request, asserting update keys against
  its model fields. The request itself feeds classification and cache fingerprinting;
  no parallel pipeline policy parameters were added. Responses are stamped after the
  pipeline, so stored defaults or absent/stale policy fields never determine the report.
- 81 new policy cases cover immutable request resolution, both floor inputs, standard
  and untrusted absent/wait-timeout outcomes, real trust-list exemptions, a score-0.7
  ceiling test, cache separation for each bound, stamped hits and hand-seeded old
  entries, unchanged `/search` threshold and `/extract` policy, and unchanged 422 shape.
  The app fixture now resets retrieve settings so a prior lifespan cannot leak its
  policy into another handler test.
- Re-created the held golden via `_SCHEMA_MODELS`; exactly the three expected
  effective-field paths are new, and older goldens are untouched. Regenerated OpenAPI
  and all four anchor quotations (`62c1efe2…b25a22`); export drift check passes.
  `/extract`, health and 422 schemas and the `/extract`/admission/lifespan ASTs
  compare identically to clean base `0e71157`.
- Revision `664ee603…c04b` -> `d98f7dbe…69359`: only the contract continuation
  moves a hashed source. Read-only whole-file revert reproduces the base exactly
  under `{}` and shipped configuration; all five rotation records updated.
- Operator/API/security text names both trust-tier exemptions and the retrieve-only
  ceiling. `docs/releases.md` is the existing spec-8 consumer-note deliverable:
  the required timeout, signal and floor tokens are grep-verified there, with a
  carry-forward note in spec 8's Implementation Notes. Spec 6's bind-mount procedure
  is cross-referenced as pending rather than claimed as shipped.
- 1,055 related tests passed (1,002 runtime/contract tests plus 53 governance tests);
  repository-wide Ruff lint/format and strict Pyright pass. Full-suite execution
  remains the orchestrator/end-of-epic gate because the implementer instructions
  explicitly prohibit it. No story definition or acceptance checkbox was changed.

### US-005 retry: oversized numeric configuration (2026-09-22)

- Restored the reviewed `bcbc928` implementation on clean `0e71157`, then corrected
  the verifier's sole failing case: `bounded_float` now compares the original number
  to its bounds before conversion. No broad catch, fallback, or value-bearing error
  was added; valid integer endpoints still widen to floats and bool/NaN/infinity
  remain refused.
- Four new real-YAML regressions (positive and negative 401-digit integers through
  the settings reader and lifespan) reproduced `OverflowError` before the fix and
  now require the exact `RetrieveConfigurationError` class and closed range message,
  with no value echoed in the error or logs. The shared helper gains five cases for
  non-finite floats and oversized integers.
- Re-measured the same `664ee603…c04b` -> `d98f7dbe…69359` rotation and read-only
  revert control under both default and shipped configuration. `config_bounds.py`
  is not hashed; only `contract.py` still moves a source input. Re-generated contract
  and golden artifacts match the prior attempt exactly, including the anchor.
- The related policy/app/config/cache/orchestrator/contract run passed all 972 cases;
  another 94 mapped contract-smoke cases passed, with all 53 documentation guards
  re-run after the notes update (1,066 distinct cases total). Repository-wide Ruff
  lint/format, strict Pyright and contract drift checks pass.
  The full-suite gate remains deferred to the orchestrator as explicitly instructed.

### US-004 implementation (2026-09-22)

- `ContentCache._parse_entry` is the one guarded parse seam: catches only `ValueError`,
  counts `corrupt_entries`, logs `cache_entry_corrupt` plus the one-way key digest, and
  attempts deletion before returning a miss. Storage retains ownership of operation
  failures; even a Valkey delete failure returns a miss and reports its own closed token.
- Added the counter in all three places, in matching order: `CacheMetrics`,
  `CacheMetricsResponse`, and the handler's explicit cache dict. The existing byte/order
  guards cover it; `/retrieve` recovery tests also assert the nonzero `/metrics` value.
- Re-created the held golden using `_SCHEMA_MODELS`: byte-identical, because metrics
  models are not members. No addition to `_EXPECTED_ONE_THREE_ZERO_DIFF`, no older
  golden changes. Regenerated OpenAPI and its anchor, updating all four quoting pages.
- Measured revision `464b6ad5…fead2` -> `664ee603…c04b`; the only changed hashed input
  is the contract docstring. Its read-only whole-file revert to clean base `9200a76`
  reproduces the before value under both `{}` and shipped config, recorded at all five
  sites. No sanitization algorithm changed; parse success is still not authenticity.
- The implementation instructions prohibit a full-suite run here, so that acceptance
  gate remains for the orchestrator. All 668 related tests pass; repository-wide Ruff
  lint/format, strict Pyright and contract export checks pass. Story definitions and
  acceptance checkboxes are unchanged.

### US-003 handoff checkpoint (2026-09-22)

- Claude's recovered attempt `371d254` is preserved on
  `backup/forage-hardening-pdf-us003-20260922`, but is **not accepted**. Independent
  Copilot verification reproduced actual task cancellation leaving the PDF worker
  and spool file alive after the request released its admission permit. Raising
  `CancelledError` inside a synchronous worker stub did not test that lifecycle.
- Resume US-003 with the candidate as reusable work, fix cancellation ownership,
  and add a real `Task.cancel()` regression before verification. No criterion is
  waived and the seven previously completed stories remain completed.
- The controlled handoff retains guarded mode (three retries), the existing
  worktree, PR completion and owner gates. GPT-6 Astra replaces Claude model aliases
  for all roles. Previous attempt history and ignored execution artifacts are
  backed up; the new retry round does not erase that history.

### US-003 corrected implementation (2026-09-22)

- Reused the preserved `371d254` diff on the attempt branch, not the backup or handoff
  branches. The new asynchronous regression failed on that candidate: after
  `Task.cancel()` the request was already done while its worker and spool remained live.
- `/retrieve` now owns the `asyncio.to_thread` task until it completes, using
  `asyncio.wait` without forwarding cancellation. Repeated cancellation is deferred until
  the existing bounded worker is reaped and its spool unlinked; its outcome is retrieved
  (a spool fault still logs the closed WARNING), then cancellation propagates before
  classification. No change to worker IPC, rlimits, admission middleware/controller,
  or `/extract` cancellation behavior.
- The regression exercises actual task cancellation once and three times, worker success
  and worker/spool failure, rejection of a replacement request while cleanup is pending,
  and restored counters with no spool after cancellation completes. Additional cases
  exercise real spawned-child failure mapping and a partial write followed by ENOSPC.
- Contract artifacts and the held `1.3.0` golden regenerated from `_SCHEMA_MODELS`.
  Only the shared 422 enum gains `extraction_failed`; four PDF tokens are reasons, not
  new retrieve codes. Older goldens are untouched.
- Corrected revision: `f654be77…c92fb` -> `464b6ad5…fead2`; orchestrator reverted
  `a018345e…c6c73`, contract reverted `80b39055…1f039`, both reverted
  `f654be77…c92fb`. The five records replace the unaccepted candidate values;
  `docs/bootstrap-notes.md` records the read-only git-blob measurement.
- Full-suite execution is deferred to the orchestrator/end-of-epic gate as instructed;
  698 mapped and directly related documentation tests pass. Repository-wide Ruff lint,
  format check, strict Pyright and `export_contract --check` are clean; AST comparisons
  confirm the worker/IPC, middleware/controller, `/extract` pipelines and two named
  legacy callers are unchanged. The pre-existing queued-admission
  handoff race remains the recorded spec-6 residual, not part of this correction.

### US-001 (2026-09-20)

- **Stale line numbers in the hints.** `SearchMetricsSink` is at `pipeline/orchestrator.py:1050`,
  not `:747-757` (that range is now `_reject_search_url` / `_block_search_url`). The new Protocols
  and `_NullRetrieveMetrics` were placed beside the real `SearchMetricsSink`. `_bounded_int` in
  `pipeline/extraction_limits.py` is at `:79-96` as stated.
- **Seventeen call sites, not sixteen.** `grep -c 'run_retrieve_pipeline(' tests/test_orchestrator.py`
  returns 17 at story start (the sixteen the hints count plus the one inside the
  `test_search_hands_the_scanner_a_newline_preserving_form` neighbourhood helper at `:1367`). All
  seventeen go through the one `_retrieve_kwargs()` helper.
- **A third `_bounded_int` copy exists** — `pipeline/search_providers/brave.py:224`. The hints name
  only `extraction_limits.py` and `cache.py`, so only `extraction_limits.py` was migrated; the
  brave copy is untouched (out of scope, and it is the search provider's own settings reader).
  `config_bounds` also gained `bounded_bool`, which the hints do not name: the `retrieve:` reader
  needs one for `promptguard_fail_closed_floor` and restating it inline would have been a fourth
  copy of the same idea.
- **`_NullRetrieveMetrics` is unused by the pipeline in this story** (nothing increments the three
  counters until US-002/US-006), and pyright strict's `reportUnusedClass` flags a private class
  nobody touches. Resolved with a module-level annotated binding,
  `_NULL_RETRIEVE_METRICS: RetrieveMetricsSink = _NullRetrieveMetrics()`, which is also the
  structural-conformance check: a counter added to the Protocol without a matching field on the
  null sink is a type error at the seam rather than an `AttributeError` in a later story.
- **`AdmissionSlot` is satisfied by `ExtractionAdmissionController` without edits**, as the hints
  predicted: `acquire` and `release` are both `async def`, pinned by
  `test_the_admission_protocol_is_satisfied_by_the_app_controller`.
- **The boot WARNING lives in the lifespan, not the reader.** `retrieve_settings_from_config` stays
  pure so the module-level fallback (`app.state.retrieve_settings = retrieve_settings_from_config({})`)
  emits nothing, which is what the hints require of it.
- **The contract export did not move.** `PROMPTGUARD_BUDGET` is a `reason` *value*, not a schema
  field, so `uv run python -m scripts.export_contract` leaves `contract/openapi.yaml`, its sha256
  anchor and `tests/golden/contract_1_3_0.json` byte-identical. Only the `CONTRACT_VERSION`
  docstring moved.
- **Rotation measured last, after the final hashed byte landed.** `6f0fa2de…66671` →
  `e55b5f06…4d3c0`; `orchestrator.py` alone reverted gives `965e22dd…c0ff4`, `contract.py` alone
  gives `0a95a190…4da0b`, and the both-reverted control reproduces `6f0fa2de…66671` exactly.
  Recorded at all six sites (CLAUDE.md, GOTCHAS.md, CODE_ARCH.md, DECISIONS.md,
  `docs/bootstrap-notes.md` ledger row + section, SERVICE_MAP.md's divergence count, now twenty).

### US-002

- **`grep -c 'asyncio.timeout' pipeline/orchestrator.py` was 4 at story start, not 1** — three
  comments/docstrings named it beside the one `_bounded_permit` code site. The three were reworded
  ("the deadline", "the deadline context") so the count is exactly 1 as the criterion states.
- **Construction-site grep lists nine lines, not eight.** `tests/test_orchestrator.py:5157` was
  added by an earlier story in this epic; the property the criterion is after — the sites at story
  start are untouched — holds: no `ExtractionAdmissionController(` line was added or edited. Every
  new controller in this story is built through `from_retrieve_settings`.
- **`test_pipeline_422_error_code_is_pinned_to_ten_members` now reads the held 1.3.0 golden**,
  not the frozen 1.2.0 one: the 1.2.0 fixture can never carry `busy`, and editing it is forbidden.
- **The body-release test needs a plain function as `fetch_url`, not an `AsyncMock`:** a mock's
  `return_value` keeps the `FetchResult` — and so the body — alive for the life of the mock.
- **The discipline tests pin the safe cancel-while-queued ordering** (the cancelled waiter's
  `except BaseException` runs before the holder releases); the racing interleaving is the recorded
  residual (GOTCHAS.md, SECURITY.md, spec 6 Open Questions) and is not a tested property.
- **Rotation** `d0433876…fc88e` → `f654be77…c92fb`; `orchestrator.py` alone reverted gives
  `16b9631f…d8932`, `contract.py` alone `646b4f27…3fd81`, both-reverted control reproduces
  `d0433876…fc88e` exactly. Recorded at CLAUDE.md, GOTCHAS.md, CODE_ARCH.md, DECISIONS.md,
  `docs/bootstrap-notes.md` (ledger row + section) and SERVICE_MAP.md's count (now twenty-two).

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
- Validation round 5 (2026-09-19, final fix pass, not re-reviewed): US-001's `admission` became
  `AdmissionSlot | None = None` (required from US-002) so the story ships green alone; the budget
  key's default became `0` with the `retrieve_budget_unset` boot WARNING and a one-release window
  in `docs/releases.md` (example 6 step 1, `0` legal after the flip); the Independent Test moved
  to the `MagicMock(spec=PromptGuardClassifier)` double; `fetch_concurrency`'s sentence names the
  PDF-branch reason and the HTML-branch inheritance, Goals carry the single-flight cost, and the
  `config.yaml` comment states the 1 280 MiB overcommit and its guard; US-006 named the
  `unavailable_result` seam (three hashed files), the `/search` `loaded` guard and
  `tests/test_contract_schema.py:368`; US-002 widened the handoff residual to queued
  cancellations racing a release, pinned the test ordering, dropped `fetch_concurrency` from the
  knob list and placed `busy` under ruling (b), and documented the adapter view's validation gap;
  US-003 gained the `retrieve_spool_error` WARNING; the inverted byte/depth Edge Case was
  corrected; every rotation states a clean-tree precondition; a Known-risks section closes the
  spec. No story split.

### Decisions Made

- Cache hits bypass both gates (US-006, US-002).
- The `/retrieve` budget is its own key (`retrieve.max_promptguard_chunks`, default 256) with a
  characters-only pre-check (round 3 dropped the byte limb: it cannot bind below 293 chunks and the
  character bound already bounds bytes ×4); an over-budget page is refused under the existing
  `content_too_large` code, never a new code (R22). This is a new refusal on an accepting route,
  classified on the record under GOVERNANCE worked example 6 and shipped by its step 1 (round 5):
  the key defaults to `0` — no pre-check, today's behaviour — for one minor release, loudly (one
  boot WARNING naming the coming default), with the window named in `docs/releases.md` and the
  Release body and `0` legal after the flip. Chosen over a MAJOR (the fix *is* compatible) and
  over rounds 3–4's "the knob is the window" (a `config.yaml` key the consumer cannot set and the
  operator cannot change without rebuilding the image is not a window). The cost — the DoS
  vector stays open by default for one release on deployments that ignore the WARNING — is
  accepted because a silently refusing default would be the disguised MINOR step 4 forbids.
- `/retrieve`'s admission is the existing controller class **used as it is** (round 4: constructor
  and handoff unchanged, `from_retrieve_settings` adapts a `RetrieveSettings` into the view the
  controller reads, the `metrics` annotation widened to the Protocol), acquired before the fetch
  with no timer — the round-3 `asyncio.timeout` wrapper and `admission_wait_seconds` are withdrawn
  because the handoff would leak a slot on a post-grant cancellation, and the bounded queue is the
  backpressure; a refused admission is 422 `busy` (the existing literal) with the one reason
  `admission_queue_full`, because adding a 429 to `/retrieve` would be a new observable status
  (MAJOR) while a new 422 code inside the window is MINOR — and because round 3 found
  `pipeline_error_handler` maps `busy` to 429 by code alone, the handler becomes route-aware with
  both halves tested (R42). The fetched body is released with the slot so a classification waiter
  holds only text; the waiter population is bounded in time and per-waiter size, not in count, and
  the docs say so. **Flagged for the epic wrapper**: ruling 23 and the window list name `busy` and
  two reasons (now one, `admission_queue_full`, and no `admission_wait_seconds` key — seven keys);
  the epic's success criterion "a 65-chunk page classifies at most 64 chunks" is
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
  loop-wrapping timeout that would unwind the loop); round 4 made the deadline check explicit
  before each acquisition, because a free permit is taken synchronously and a zero timeout never
  fires.
- **Round 4.** `fetch_concurrency` is pinned 1–1 (the round-3 1–8 range overcommitted worker
  address space against the one-worker memory reservation; the rationale sentence about body size
  was wrong and is replaced). `retrieve.semaphore_saturation` keeps its name (overrules the rename
  to `admission_saturation`: the controller writes the attribute by name, `/extract`'s counter of
  the same name counts the same admission class, and the MONITORING row disambiguates both gates).
  No per-result unscanned marker is added to `SearchResult` (overrules the reviewer's option (a):
  the `suspicious` flag already marks every unscanned result, `unscanned_results` counts them, and
  the description plus the consumer rule state it; a boolean that duplicates `suspicious` on the
  scanned-and-flagged case would not separate the two either). `content_too_large_to_classify` is
  not reused for a fetched PDF over the worker's ceiling: one refusal shape for "over the
  classification budget" across the HTML and PDF branches of `/retrieve` beats matching
  `/extract`'s code, and the `reason` names the budget. The GOVERNANCE example-6 lane is
  reconciled by making the knob the stated window (through at least 1.4.0) rather than claiming
  "no window" against step 2. The controller's handoff race is recorded, not fixed (R23: used as
  it is; no timer makes it reachable). A trusted-tier request acquires no permit. A wait-timeout
  fail-open body is never cached. `/metrics` counters are not in `_SCHEMA_MODELS`, so no counter
  in this spec appends to the diff set (code fact; round 3 said they did). The ruling's phrase
  "results that time out carry `suspicious` unset … as today" contradicts the code —
  `orchestrator.py:1040-1044` sets `suspicious = True` on the fail-open pass-through — so the
  code's behaviour is kept and the description is corrected to it.
- **Round 5 (final, not re-reviewed).** `admission` is defaulted to `None` in US-001 and made
  required in US-002 — the one exception to "no defaulted parameter", because US-001 alone has
  no controller to pass and a required parameter with no object is not a shippable story
  boundary; the criterion that removes the default lives in the story that creates the object.
  The budget ships off by default with a loud, one-release window (above); round 4's
  "through at least 1.4.0" knob guarantee is superseded by the explicit window. `busy` is a
  capacity refusal under ruling (b), not an example-6 tightening, and `fetch_concurrency` is not
  a knob while it is pinned — the honest shipped-default statement (single flight, four queued,
  422 at the fifth) is in Goals and the consumer note. The wait-timeout path calls a named,
  pure `unavailable_result` seam in `stage3_promptguard.py` rather than duplicating a three-way
  security decision in the orchestrator — accepted at the cost of a third hashed file in US-006's
  rotation (measured, not assumed). US-001's budget test uses the `MagicMock` double because a
  real classifier is never `loaded` in the hermetic suite. The handoff residual is described as
  the code has it (queued cancellation racing a release also leaks) and the queued-cancel
  criterion pins its ordering; the idempotent-handoff fix stays with spec 6. The spool row gets
  a closed WARNING token rather than a per-reason `/metrics` map (a wire change for one host
  fault). The 1 280 MiB worker overcommit is stated with its guard rather than refused at boot
  (spec 6's envelope). Warnings not applied are under Known risks.
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

### Session 2026-09-19 (validation round 4)
- Rulings applied: R23 (corrected — controller constructor and handoff unchanged with the eight
  construction sites named; `from_retrieve_settings` adapter; `AdmissionSlot` with `async def
  release`; no `asyncio.timeout` around the admission acquisition and `admission_wait_seconds`
  withdrawn; `build_retrieved_content` unchanged with `fetch_result` and `html_text` dropped before
  the wait; lazy `mkdir(mode=0o700, exist_ok=False)` spool directory verified with `os.lstat` on
  every call, no repair; cache write skipped on a fail-open wait timeout; `SearchResult.suspicious`
  description corrected), R36 (corrected — enum members and fields append, counters, reasons and
  descriptions do not; the `/metrics` models are not golden models), R43 (every grep names its
  path set and was run), R41 (counts re-verified: 16 / 47 / 14 / 12 / 0, eight constructor sites).
  Round-3 warnings applied: the trusted-tier permit skip, the explicit `/search` deadline check,
  the `/search` 422 enum widening and `Pipeline422ErrorResponse.error` description, the
  `fetch_concurrency` 1–1 pin, the honest stage-1 peak sentence, the gated GOVERNANCE `### (<letter>) `
  section and example-6 reconciliation, the `TESTING_GUIDE.md` rows, the two-reason-shape
  `API_GUIDE.md` sentence, the `cache.py:405-425` precedent, the provisional sizing pairing, the
  per-test controller reset, the parent-directory and confidentiality sentences for `TMPDIR`, the
  consumer-note sentence on fail-open exposure. Findings overruled or narrowed are in Decisions
  Made (Round 4).

### Session 2026-09-19 (validation round 5, final)
- Rulings applied without a re-run: R23 (corrected — `admission: AdmissionSlot | None = None` in
  US-001, required from US-002; the one-body `fetch_concurrency` sentence with the 1–1 range as
  the reason; the budget shipped at `0` with the `retrieve_budget_unset` WARNING and a
  one-release window per GOVERNANCE example 6 step 1, `0` legal after the flip; clean-tree
  precondition on every rotation), `test_search_metrics_response_1_2_0_field_set_is_pinned_
  exactly` named in US-006, the handoff race recorded as accepted with its wider description.
  Round-4 warnings applied as one-to-three-line edits: the `MagicMock` double, the PDF-branch /
  HTML-branch sentence and the knob list, the `config.yaml` overcommit statement, the
  `unavailable_result` seam and the `/search` `loaded` guard, the queued-cancel ordering, the
  `retrieve_spool_error` WARNING, the adapter-view docstring, the byte/depth Edge Case. Everything
  else is recorded under Known risks; the epic proceeds with this spec marked ready.

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
- [ ] Whether `ExtractionAdmissionController.release()`'s handoff should be made idempotent
      (decrement in `release()`, re-increment in the woken waiter) so a post-grant cancellation
      cannot leak a slot (non-blocking; pre-existing, reachable only by task cancellation, recorded
      in GOTCHAS and SECURITY; spec 6's envelope work is the natural owner).

## Known risks (validation close-out)

Round-4 findings not applied in the final pass, each with its reviewer, the finding in one line,
and why it is carried rather than fixed here.

- **Salty engineer — the shipped default pair makes wait timeouts likely under modest
  concurrency.** With the classifier on CPU, 256 windows at a plausible 20–80 ms each is 5–20 s of
  a 30 s wait behind one permit; two concurrent large pages and the second times out, and while
  the budget key is `0` the hold is bounded only by the fetch cap. The fail-open outcome reaches
  only callers who opt out of the request default (`promptguard_fail_closed: true`); the
  fail-closed outcome is a content-free blocked response after the wait. Deferred because the
  per-window latency is spec 7 US-004's measurement, the operator floor is off by default by
  owner decision 3, and the pairing is documented as provisional with the instruction to lower
  the budget rather than raise the wait. Owner: spec 7 US-004 (the number), spec 6 (the
  envelope).
- **Salty engineer — the worker address-space ceiling overcommits the container.** At the
  shipped defaults `(1 + 1) × 384 MiB + 512 MiB = 1 280 MiB` in a 1 GiB envelope; the only thing
  preventing two concurrent workers today is `extract_route_enabled: false`, and spec 8 exists to
  flip that gate. The `config.yaml` comment now says so; a boot-time refusal is not added here.
  Owner: spec 6 (sizing) and spec 8 (the route flip must re-check the sum).
- **Salty engineer / codebase fit — an HTML-only deployment is throttled by a PDF-sized bound.**
  `fetch_concurrency` 1–1 makes `/retrieve` single-flight through fetch and stage 1 with a 120 s
  worst-case queue latency; the HTML branch spawns no worker and inherits the bound only for the
  stage-1 memory peak. Accepted and stated in Goals; splitting the gate (PDF worker at 1, HTML at
  N) is spec 6's envelope work.
- **Salty engineer — the controller's handoff can leak a slot on a queued cancellation racing a
  release.** Pre-existing, now described as the code has it; at `fetch_concurrency: 1` one
  occurrence wedges `/retrieve` until restart. Reachable only by task cancellation (shutdown);
  the four-line idempotent-handoff fix stays with spec 6 per R23 (the controller is used as it
  is), recorded in GOTCHAS, SECURITY and Open Questions.
- **Salty engineer — `pdf_spool_error` is not separable on `/metrics`.** A closed WARNING token
  (`retrieve_spool_error`) is the alert signal instead; a `retrieve.extraction_failed_by_reason`
  map would be a `/metrics` wire addition for one host fault and is deferred to the per-cause
  breakdown Open Question.
- **Salty engineer (INFO) — the `/search` deadline is consumed by inference time as well as wait
  time.** A request whose permit is never contended can still hit the deadline on a slow
  classifier and take the unavailable branch for its remaining results. Accepted: the deadline
  is a per-request budget by design; the counter and the per-result markers say what happened.
- **Salty engineer (INFO) — the route-aware 429 branch is dead code for `/extract`.** Nothing
  raises `PipelineError(error="busy")` on `/extract` (the middleware mints its 429), so the
  handler's `/extract` branch is reachable only by a future raise. Kept, with both halves tested
  (R42), because the handler must not silently 429 a future `/retrieve`-style raise elsewhere.
- **Salty engineer (INFO) — `_spool_upload` gains a failure mode it did not have.** With
  `dir=spool_dir()` a bad spool directory can now fail an `/extract` upload where today's
  `NamedTemporaryFile` with no `dir=` could not; the `/extract` spool failure propagates as today
  (out of scope) and the directory is verified at boot, so the exposure is a use-time change
  after boot on a hostile `TMPDIR`, documented as the sticky-parent requirement.

### US-006 implementation notes (2026-09-20)

- **`_bounded_permit` has exactly one `acquire()`, because `asyncio.timeout(None)` is the
  no-deadline form.** The hints describe an `if seconds is None: await semaphore.acquire()`
  branch beside the timed one, which makes `grep -n 'semaphore.acquire()'` return **two**
  matches and fails the first acceptance criterion as written. `asyncio.timeout` accepts
  `None` and means "no deadline", so the untimed `/extract` acquisition is the same single
  statement. The criterion's number is achievable exactly this way.
- **The criterion's `grep -c 'model_unavailable' pipeline/orchestrator.py` is 0 was never
  true.** The `/search` loop has *read* `pg_result.skip_reason == "model_unavailable"` at
  two sites since long before this epic (verified against the merge-base blob: count 2).
  The property the criterion is actually after is that the orchestrator never
  **constructs** the result, so the test asserts that structurally — an AST walk over every
  `PromptGuardResult(...)` call in the file — and then asserts the surviving matches are
  comparisons or comments.
- **The `/retrieve` wait-timeout counter is derived, not threaded back.**
  `sanitize_and_structure` has no metrics sink and the hints give it only two new
  parameters, so `run_retrieve_pipeline` derives the event from the result:
  `classifier_loaded and content.promptguard_state in {"unavailable_blocked",
  "unavailable_allowed"}`. That is sound because `run_promptguard` returns
  `model_unavailable` **only** when the classifier is absent or unloaded, so a loaded
  classifier plus an `unavailable_*` state is the wait timeout and nothing else. It also
  means the counter and the new cache condition read the same fact rather than two.
- **The `route=retrieve` token in `sanitize_and_structure` is a literal, with a comment.**
  Only `/retrieve` passes a deadline into that function; `/extract` passes
  `classification_wait_seconds=None`, which cannot time out, and `/search` never calls it.
  A third `route=` parameter would exist solely to be passed one value.
- **`/search` reads `promptguard_wait_seconds` off `app.state.retrieve_settings`.** It is a
  top-level config key that US-001's reader happens to land in `RetrieveSettings`; the
  handler coupling is noted here rather than duplicating the reader.
- **Two pre-existing test doubles needed the new counter**, both structural
  `SearchMetricsSink` implementations that pyright checks at the call site:
  `_RecordingMetrics` in `tests/test_orchestrator.py` and the inline `search` section
  assertion in `tests/test_app.py::test_metrics_covers_search_retrieve_and_cache_sections`.
  Neither is a `run_search_pipeline(` call-site edit.
- **Rotation measured last, from a clean tree, reading reverted bytes from `HEAD` blobs:**
  `e55b5f06…4d3c0` → `d0433876…fc88e`. `orchestrator.py` alone `64257b22…73c79`,
  `stage3_promptguard.py` alone `201ac2c8…fd451`, `contract.py` alone `5ee16308…bcbdb`,
  all-three-reverted control reproduces `e55b5f06…4d3c0` exactly. Recorded at all five
  sites plus `SERVICE_MAP.md`'s divergence count (now twenty-one).
- **`export_contract` moved the frozen surface**, because the `SearchResult.suspicious`
  description reaches `contract/openapi.yaml`; the four anchor-quoting pages
  (`API_GUIDE.md`, `CI_CD.md`, `DEPLOYMENT.md`, `SERVICE_MAP.md`) were refreshed to the new
  sha256 and `tests/test_governance_docs.py` is green. Nothing was appended to
  `_EXPECTED_ONE_THREE_ZERO_DIFF`; the golden's only change is that one description.
