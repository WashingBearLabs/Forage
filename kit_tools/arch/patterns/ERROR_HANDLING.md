<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: architecture, tech-stack
  required_sections:
    - "Overview"
  skip_if: never
-->
# ERROR_HANDLING.md

> **TEMPLATE_INTENT:** Document error handling patterns and conventions.

> Last updated: 2026-09-16
> Updated by: Claude (seed-project)

## Overview

Forage's error handling rests on five conventions. **The wire vocabulary is closed and
versioned**: eighteen error codes live in `pipeline/contract.py` as nested `Literal`
types, each bound to a fixed HTTP status, and adding one is a contract change governed by
`contract/GOVERNANCE.md`. **Exceptions are typed and mapped once**: stages raise their own
domain exceptions, `pipeline/orchestrator.py` translates them into a `PipelineError` that
carries a code, and a single FastAPI exception handler in `retrieval_app.py` renders it as
JSON. **Codes, not prose, are the contract**: consumers branch on `error`; `reason` is
descriptive text that may change without a bump. **Degradation is loud, never silent**
(`CLAUDE.md` invariant 5): `/health` is always 200 with the truth in the body, a
configured-but-unreachable cache is reported as `cache_unavailable` rather than quietly
replaced by memory, and an absent classifier blocks content by default. **The content path
fails closed**: a structural block or a PromptGuard verdict yields a content-free quarantine
body, and the PDF worker is killed rather than trusted to finish.

Three things look like errors and are not: a quarantine is HTTP 200; a missing model is
HTTP 200 with `promptguard_state` saying so; a cache outage is a miss. Forage reports, it
does not decide (`kit_tools/arch/CODE_ARCH.md`, "Patterns That Matter").

---

## Error Categories

Every row is an emission site in the code today. "Logged as" is the application's own
logging; note that nothing in the repo configures logging, so INFO lines are dropped in
the container (`kit_tools/arch/patterns/LOGGING.md`). The operator-facing table with
first-thing-to-check per code is `kit_tools/docs/TROUBLESHOOTING.md` "Error-Code
Reference"; the consumer-facing one is `kit_tools/docs/API_GUIDE.md` "Error responses".

| Category | Codes | Route / status | Emission site | Logged as |
|----------|-------|----------------|---------------|-----------|
| Request schema validation | none (FastAPI `HTTPValidationError`) | any POST route, 422 | FastAPI/Pydantic, before any handler runs | not logged by the app |
| Upload metadata refusal | `invalid_filename`, `invalid_mime_hint`, `invalid_request_id` | `/extract` 422 | `retrieval_app._sanitize_upload_metadata` | INFO `document extraction completed`, verdict `failure`, reason = code |
| URL refused | `invalid_url`, `private_ip`, `blocked_domain` | `/retrieve` 422 | `url_validator.validate_url`, mapped in `run_retrieve_pipeline`; re-checked per redirect hop inside `fetch_url` | not logged |
| Fetch failure | `fetch_timeout`, `content_too_large`, `fetch_error` | `/retrieve` 422 | `pipeline/stage5_url_audit.fetch_url`, mapped in `run_retrieve_pipeline` | not logged |
| Document failure | `content_too_large`, `content_too_large_to_classify`, `extraction_failed`, `pdf_encrypted`, `pdf_no_text`, `unsupported_format` | `/extract` 422 | `document_failure()` in `run_extract_pipeline_from_file`; `_spool_upload` (size re-check); `UnsupportedFormatError` from the handler for an invalid `promptguard_threshold` | INFO `document extraction completed`, verdict `failure` |
| Search backend failure (lone `searxng` chain) | `searxng_error`, `searxng_unavailable` | `/search` 422 | `run_search_pipeline` → `_searxng_pipeline_error` | WARNING `search_provider_failed provider=searxng failure_class=<class> detail=<token>` from the orchestrator, paired with the provider's own WARNING (`pipeline/search_providers/searxng.py`) |
| Search provider chain exhausted (any other chain) | `search_unavailable` | `/search` 422 | `run_search_pipeline` → `_search_unavailable_error`; reason is one closed `<provider_name>: <failure_class>` entry per failed provider, in chain order, joined by `"; "` (e.g. `searxng: rate_limited; brave: timeout` — `kit_tools/docs/TROUBLESHOOTING.md` shows the composite) | one WARNING `search_provider_failed provider=… failure_class=… detail=…` per failed provider, in chain order, each paired with that provider's own WARNING |
| Search chain policy-excluded | `search_unavailable` | `/search` 422 | `retrieval_app.search`, before `run_search_pipeline` is called: `apply_request_policy` narrows the configured chain to empty for this request; reason is the fixed literal `policy_excluded_all_providers` — no provider was tried (`search-policy-and-health` US-010) | not logged |
| Capacity / admission | `busy` | `/extract` 429 | `ExtractionAdmissionMiddleware` (queue depth 1, 50 MiB queued-bytes reservation) | not logged; counted in `/metrics.extraction.busy_rejections` |
| Upload size, streaming | `content_too_large` | `/extract` 413 declared, **400 observed** (see Observed rough edges) | `DocumentSizeLimitMiddleware` via `_RequestBodyTooLargeError` | not logged |
| Route disabled / not wired | none (bare `detail`) | `/extract` 404 / 503 | `ExtractionAdmissionMiddleware`; the handler repeats the 404 as `HTTPException` | not logged |
| Boot-time configuration | none (no HTTP) | process refuses to serve | `ExtractionConfigurationError`, `CacheConfigurationError` raised out of `lifespan` | the exception leaves `lifespan`; uvicorn reports it |
| Internal | none | 500, Starlette default body | no `Exception` handler is registered; the recorded cases are `/health` failing response validation on an unlisted `degraded_reasons` value and an unmodeled `/metrics` counter (`extra="forbid"`) | not logged by the app |

The WARNING lines that accompany non-error degradations: quarantine on `/retrieve`
(`pipeline/orchestrator.py`), PromptGuard unavailable in fail-closed or fail-open mode
(`pipeline/stage3_promptguard.py`), Valkey connect/operation failures in a closed
vocabulary (`cache.py`), and `weights_retry_scheduled` (`model_fetcher.py`).

---

## Error Response Format

There is no envelope and no correlation-id header. Five body shapes exist, each mirrored by
a Pydantic model in `retrieval_app.py` that documents (never emits) the bytes; the emission
sites are the middleware and the handler above. Field names below are the verified ones.

```text
Extract422ErrorResponse      /extract 422
  error              : Extract422ErrorCode   -- stable, machine-readable; branch on this
  reason             : str                   -- fixed, content-free text from DOCUMENT_FAILURE_REASONS
  request_id         : str                   -- uuid4 hex, or the caller's id once validated
  sanitizer_revision : str                   -- added by pipeline_error_handler on /extract only

Pipeline422ErrorResponse     /retrieve and /search 422
  error              : Pipeline422ErrorCode
  reason             : str                   -- NOT content-free: may echo the URL, resolved IP, or upstream text
  request_id         : str

Admission413Response         /extract 413 (declared; shadowed by a 400 in practice)
  error              : Literal["content_too_large"]
  reason             : str
                                             -- no request_id: refused mid-stream, before any handler

RateLimit429Response         /extract 429
  error              : Literal["busy"]
  reason             : str
  request_id         : str                   -- minted by the middleware
  sanitizer_revision : str

DetailResponse               /extract 404, 503, and the observed 400
  detail             : str                   -- "Not Found" | "Unavailable" | "There was an error parsing the body"

HTTPValidationError          any POST route, 422 (schema-invalid request)
  detail             : list[ValidationErrorDetail]   -- FastAPI default: loc, msg, type
```

`error` is the contract: the eighteen values are enumerated in `contract/openapi.yaml`
and pinned by `tests/golden/`. `reason` is not: it is documented as descriptive, and
`contract/GOVERNANCE.md` ruling (d) records that redacting it would itself be a wire change.
`request_id` is a per-call uuid4 hex minted in the orchestrator, in the admission middleware
per 429, and in `_sanitize_upload_metadata` per refusal; `/extract` accepts a caller-supplied
value only after it passes the 128-character, `_REQUEST_ID_RE` check. Nothing propagates it
through headers.

---

## Implementation

### The exception hierarchy

- **Wire-bound** (`pipeline/orchestrator.py`): `PipelineError(Exception)` holds `error`,
  `reason`, `request_id` and exposes `to_dict()`. `UnsupportedFormatError(PipelineError)`
  fixes `error="unsupported_format"`. The `document_failure(code, request_id)` factory
  refuses any code outside `DOCUMENT_FAILURE_CODES` (seven, including `busy`) and always
  uses the fixed text in `DOCUMENT_FAILURE_REASONS`, which is how `/extract` stays
  content-free.
- **Domain, mapped at the orchestrator boundary**: `PrivateIPError`, `BlockedDomainError`
  and plain `ValueError` (`url_validator.py`); `ContentTooLargeError`,
  `TooManyRedirectsError` (`pipeline/stage5_url_audit.py`); `httpx.TimeoutException`,
  `httpx.HTTPStatusError`; `PDFExtractionError` with `PDFTooLargeError`,
  `PDFEncryptedError`, `PDFNoTextError` (`pipeline/stage1_pdf.py`) and
  `PDFClassifiableTextLimitError`, `PDFPageLimitError` (`pipeline/pdf_subprocess.py`);
  `UnsupportedUploadFormatError`, `UploadTextClassifiableLimitError`
  (`pipeline/stage1_upload.py`, both `ValueError`); `PromptGuardBudgetExceededError`
  (`promptguard/classifier.py`, a `ValueError`).
- **Boot-time, refuse to serve**: `ExtractionConfigurationError`
  (`pipeline/extraction_limits.py`) and `CacheConfigurationError` (`cache.py`), both
  `ValueError`, raised from `lifespan` before the server yields.
- **ASGI seam**: `_RequestBodyTooLargeError` (`retrieval_app.py`), raised out of the
  wrapped `receive` callable by `DocumentSizeLimitMiddleware`.

### One handler

`@app.exception_handler(PipelineError)` on `pipeline_error_handler` in `retrieval_app.py`
is the only registered exception handler. It calls `exc.to_dict()`, adds
`sanitizer_revision` from `app.state` when `request.url.path == "/extract"`, and answers
`429 if exc.error == "busy" else 422`. The live emitter of `busy` is the middleware, which
never enters the handler; the arm keeps the taxonomy consistent. The only `HTTPException`
in the codebase is `extract()`'s 404 when the route is disabled, a second guard behind the
middleware.

### Stages raise, the orchestrator maps, the handler renders

Stages know nothing about HTTP. `run_retrieve_pipeline` maps `validate_url` at step 1
(`PrivateIPError` to `private_ip`, `BlockedDomainError` to `blocked_domain`, `ValueError`
to `invalid_url`), then wraps `fetch_url` at step 3 with the same two plus
`httpx.TimeoutException` to `fetch_timeout`, `ContentTooLargeError` to
`content_too_large`, and a final `except Exception` to `fetch_error`. Every arm is
`raise PipelineError(...) from exc`. `run_extract_pipeline_from_file` routes each PDF and
upload exception through `document_failure`, adds `OSError` to `extraction_failed` and
`PromptGuardBudgetExceededError` to `content_too_large_to_classify`, and raises
`content_too_large_to_classify` itself when `raw_text` exceeds the classifiable budget.
`run_search_pipeline` no longer catches HTTP exceptions at all: `SearxngProvider.search()`
(`pipeline/search_providers/searxng.py`) classifies them behind the seam and returns a
`ProviderFailure`, and the orchestrator maps its closed `detail` token — a status-derived
`http_<code>` to `searxng_error`, everything else (`timeout`, `connect_error`,
`body_too_large`, `bad_json`, `malformed_body`, `unexpected`) to `searxng_unavailable`.

That legacy pair is selected by the **configured chain**, not by the failing provider:
`_legacy_searxng_codes` is true only for a chain of exactly one provider whose `name`
is `searxng`, compared as a name and never with `isinstance` (ruling 28). Every other
chain refuses with `search_unavailable` (contract `1.2.0`), whose reason is composed from
two closed vocabularies — one `<provider_name>: <failure_class>` entry per failed
provider, in chain order, joined by `"; "` — so no endpoint, credential or upstream text
can reach the body through it.

The route handlers add bookkeeping, not decisions: `/retrieve` calls
`RetrieveMetrics.record_error(exc.error)` and re-raises; `/extract` records the verdict
and emits its single INFO line, then re-raises. `_sanitize_upload_metadata`, the
`promptguard_threshold` check and `_spool_upload` all run inside that same `try`, so their
refusals are counted and logged identically.

### Degraded results are returned, never raised

`sanitize_and_structure` does not raise for content. A stage 2 `BLOCKED` verdict skips
stage 3 and `finalize_quarantine` builds a content-free 200 body with one diagnostic label.
An absent classifier produces a `PromptGuardResult` with `skipped=True` and a
`skip_reason`; stage 4 turns that into `promptguard_state` `unavailable_blocked` (default,
fail-closed) or `unavailable_allowed` with a -0.1 penalty. `/search` omits rather than
fails, counting into `omitted_by_reason`. Every `ValkeyStorage` operation returns `None` or
`False` on outage and schedules a reconnect; the caller sees a miss.

### FastAPI and Pydantic validation

A schema-invalid body never reaches the pipeline; FastAPI answers 422 with
`HTTPValidationError`. Because each POST route declares its own 422, FastAPI's automatic
declaration is suppressed, so the declared model is a union:
`Pipeline422ErrorResponse | HTTPValidationError` on `/retrieve` and `/search`,
`Extract422ErrorResponse | HTTPValidationError` on `/extract`. Both arms are real and
tested (`test_declaring_422_suppresses_fastapis_automatic_one`,
`test_validation_arm_of_the_422_union_is_real`). `responses=` names exactly the route/status
pairs that emit a body today; `/health` and `/metrics` declare none.

---

## Patterns to Follow

- **Add a code to the vocabulary; never invent an ad-hoc status.** New code goes into the
  right `Literal` in `pipeline/contract.py` (the composites are nested, so one edit lands it
  everywhere), reaches the wire through `PipelineError` or `document_failure`, is mirrored in
  `retrieval_app.py`, and is followed by `uv run python -m scripts.export_contract`. It is
  MINOR under `contract/GOVERNANCE.md`, with a new golden fixture beside the old one, never a
  replacement.
- **Never make a degradation silent.** `/health` stays 200 and says `degraded`; a missing
  dependency gets a `degraded_reasons` entry, a `/metrics` counter, or a per-response state
  field. The nine-day silent outage that motivated invariant 5 is the reason.
- **No fallback the operator did not choose.** `VALKEY_URL` unset selects memory; any value,
  even empty, selects Valkey and stays there. A typo must not become an unshared cache.
- **Reason codes over prose, on the wire and in logs.** Bucket into closed vocabularies
  (`ERROR_CODES`, `DegradedReason`, `cache._closed_vocabulary_reason`, `model_fetcher`
  `REASON_*` and `OUTCOME_*`), and let unknown keys fold into `METRICS_OTHER_BUCKET`.
- **Keep exception text out of wire bodies unless the vocabulary already allows it.**
  `/extract` reasons are fixed strings by construction; `/search` joined them in
  `search-provider-abstraction` US-002 (closed provider `detail` tokens); `/retrieve`'s
  `fetch_error` is the one remaining `str(exc)` on the wire — the documented exception,
  not a precedent to extend.
- **Retries only for background acquisition, never for user-facing requests.** Weights and
  the cache reconnect back off and retry; a page fetch or a SearXNG query gets one attempt
  and a coded refusal. The caller decides whether to try again.
- **Fail closed on the content path.** `promptguard_fail_closed` defaults to `True` on
  `/retrieve` and `/search`, is hard-coded for uploads, and a structural block never
  consults the model. Kill the PDF child on any abnormal outcome (`SIGKILL` then `join`).
- **Refuse boot on bad configuration; tolerate absent dependencies at runtime.** Config
  validation is the one place an exception is allowed to stop the process.
- **Raise domain exceptions in stages; map them at the orchestrator.** A stage that knows
  its HTTP status is a stage that cannot be reused by another route.
- **Never log a credential-bearing value, and never log document content from `/extract`.**
  See `kit_tools/docs/CONVENTIONS.md` "Logging" and `CLAUDE.md` invariant 6.

---

## Retry and Timeout Table

| Operation | Timeout | Retries | Backoff | Source |
|-----------|---------|---------|---------|--------|
| Outbound page fetch (`fetch_url`) | 30 s (`DEFAULT_TIMEOUT`) per request; 10 MiB body cap; max 5 manual redirect hops | none | none | `pipeline/stage5_url_audit.py` |
| SearXNG query | 10 s (`httpx.AsyncClient(timeout=10.0)`) | none | none | `pipeline/search_providers/searxng.py` |
| Brave LLM-Context query | `search_brave_timeout_seconds` (`config.yaml`, default 15 s) | none | none | `pipeline/search_providers/brave.py` |
| Provider chain traversal (`run_search_pipeline`) | sum of the per-provider timeouts (10 s SearXNG + `search_brave_timeout_seconds` when configured) | none | none | `pipeline/orchestrator.py` |
| Valkey connect and reconnect | 2 s per attempt (`_RECONNECT_TIMEOUT_S`) | on the next operation once the backoff elapses; forever | 1 s doubling to 30 s (`_RECONNECT_INITIAL_BACKOFF_S`, `_RECONNECT_MAX_BACKOFF_S`); single-flight `_reconnect_lock`, concurrent callers get an immediate miss; `ping_if_due` from `/health` detects recovery in idle windows | `cache.py` |
| Weights acquisition (`WeightAcquisition.run`) | 1800 s for an `oras` pull (`ORAS_TIMEOUT_S`) | forever until loaded; cancellable | 30 s doubling to 600 s, plus or minus 20% jitter applied to the sleep only (`RETRY_INITIAL_BACKOFF_S`, `RETRY_MAX_BACKOFF_S`, `RETRY_JITTER_FRACTION`); single-flight, a second caller is refused not queued | `model_fetcher.py` |
| PDF extraction child | 90 s wall (`ITIMER_REAL`), 20 s CPU (`RLIMIT_CPU`), 384 MiB address space (`RLIMIT_AS`, Linux only) | none | none; `process.kill()` and `process.join()` in `finally` on every abnormal outcome | `pipeline/pdf_subprocess.py` |

The two retry loops are the only ones, and both are normative constants rather than
environment settings.

---

## Degradation Matrix

The full failure-impact view is `kit_tools/arch/SERVICE_MAP.md` "Failure Impact Matrix";
health semantics are in `kit_tools/docs/MONITORING.md` "Health Checks".

| Dependency absent or failing | What the code does | How it is reported |
|------------------------------|--------------------|--------------------|
| PromptGuard weights (no token, download pending, verification refused) | No substitute classifier. Fail-closed default: standard and untrusted content is quarantined (`unavailable_blocked`), search results are omitted (`promptguard_unavailable`). Fail-open callers get `unavailable_allowed` with a -0.1 penalty. Background loop keeps retrying. | `/health` `status: degraded`, `degraded_reasons` contains `promptguard_unavailable`, `promptguard_loaded: false`; `/metrics.model` (`fetch_in_progress`, `retries_scheduled`, `fetch_failures`, `verify_failures`, `quarantines`); `promptguard_state` per response; WARNING `weights_*` lines |
| Valkey configured but unreachable | No fallback to memory. Every cache operation is a miss; requests proceed uncached; reconnect on backoff. | `/health` `degraded_reasons` contains `cache_unavailable`, `cache_connected: false`; `/metrics.cache.reconnect_*`; WARNING with a closed-vocabulary reason |
| `VALKEY_URL` fully unset | Bounded in-memory LRU. This is selection, not fallback; memory mode cannot degrade. | `/health` `cache_backend: "memory"`, `cache_connected: true` |
| SearXNG unreachable or erroring | The next provider in the configured chain serves; `/search` is a 422 only when the chain is exhausted. | `/search` 422 `searxng_unavailable` or `searxng_error` on the default lone-`searxng` chain, `search_unavailable` on any other; not a `/health` field |
| Target site slow, oversized, private, or over-redirecting | No fallback. | `/retrieve` 422 with `fetch_timeout`, `content_too_large`, `private_ip`, `blocked_domain`, `invalid_url`, or `fetch_error` |
| Extraction capacity exhausted | Refused, not queued beyond depth 1. | `/extract` 429 `busy`; `/metrics.extraction.busy_rejections` |
| `config.yaml` missing | Every key falls to its code default. | WARNING `config.yaml not found at ...` |
| `config.yaml` extraction or cache block invalid | Boot refused. | `ExtractionConfigurationError` or `CacheConfigurationError` out of `lifespan` |

---

## Observed Rough Edges

Recorded for the owner as observations, not decisions. Each has a place in
`contract/GOVERNANCE.md` before anyone touches it.

1. **`fetch_error` interpolates `str(exc)` into the wire body.**
   `run_retrieve_pipeline`'s catch-all builds `Failed to fetch <url>: <exc>`, so httpx
   exception text reaches the consumer. It is the last such case: `searxng_unavailable`
   used to build `SearXNG not reachable at <url>: <exc>` and now builds
   `SearXNG not reachable at <scheme>://<host>:<port>: <detail>` from
   `SearxngProvider.origin` and a closed token (`search-provider-abstraction` US-002).
   The other `/retrieve` reasons pass the validator's
   own fixed-format messages through, including the resolved private IP that ruling (d)
   documents as a DNS-oracle caveat for deployments that break the private-network posture.
   Redaction is a `reason` change and therefore a contract decision.
2. **`TooManyRedirectsError` has no code of its own.** It falls into the `except Exception`
   arm and surfaces as generic `fetch_error`. Whether that is intended is undocumented.
3. **The documented `/extract` 413 is unreachable.** `DocumentSizeLimitMiddleware` emits it,
   but FastAPI's `request.form()` wraps the `receive`-side exception into
   `HTTPException(400, "There was an error parsing the body")` before the middleware's
   `except` runs; the byte cap itself works. Ruling (a2): documenting both was no bump,
   correcting 400 to 413 is a MAJOR owned by the next contract bump. Do not fix it in
   passing.

---

## Testing

- **Vocabulary and handler parity**: `tests/test_contract_errors.py` (25 tests) drives
  every emission site through real routes and asserts byte-for-byte parity with the mirror
  models. Load-bearing names: `test_error_vocabulary_is_the_documented_eighteen`,
  `test_all_eighteen_codes_render_as_enums_in_the_schema`,
  `test_every_raise_site_in_the_repo_is_in_the_vocabulary`,
  `test_extract_vocabulary_matches_poppys_pinned_allowlist`,
  `test_declared_error_statuses_match_the_emission_map`,
  `test_extract_413_body_is_mirrored_and_carries_no_request_id`,
  `test_extract_oversized_upload_actually_receives_400`,
  `test_health_degraded_reasons_survive_response_validation`.
- **Golden fixtures**: `tests/golden/contract_1_0_0.json`, `contract_1_1_0.json` and
  `contract_1_2_0.json` are
  `model_json_schema()` snapshots checked by
  `tests/test_contract_schema.py::test_contract_schema_matches_golden`; older files are
  retained, never edited (ruling (c)). `tests/test_contract_export.py` is red whenever a
  response model or `responses=` declaration moves `contract/openapi.yaml` without a
  regeneration.
- **Mapping at the orchestrator**: `tests/test_orchestrator.py`
  (`test_retrieve_private_ip_raises_pipeline_error`,
  `test_retrieve_blocked_domain_raises_pipeline_error`,
  `test_retrieve_invalid_url_raises_pipeline_error`,
  `test_search_searxng_unavailable_raises_pipeline_error`,
  `test_search_searxng_http_error_raises_pipeline_error`, plus the quarantine and
  `unavailable_blocked` / `unavailable_allowed` cases).
- **Fetch bounds**: `tests/test_stage5_url_audit.py` (`test_too_many_redirects`,
  `test_redirect_to_private_ip_rejected`, `test_default_timeout_is_30`,
  `test_timeout_exception_propagates`, `test_content_length_fast_reject`,
  `test_redirect_hop_large_body_not_buffered`) and `tests/test_url_validator.py`.
- **Loud degradation**: `tests/test_app.py`
  (`test_health_missing_cache_reports_unavailable_never_raises`,
  `test_health_degraded_reports_promptguard_unavailable`,
  `test_a_credential_less_boot_stays_degraded_and_says_so_once`,
  `test_extract_release_gate_returns_404_when_disabled`).
- **Retry loops**: `tests/test_cache.py` (`test_backoff_limits_repeated_connection_attempts`,
  `test_reconnect_is_bounded_by_a_two_second_deadline`,
  `TestReconnect::test_connect_failure_never_logs_url_or_secret`) and
  `tests/test_model_fetcher.py` (`test_it_never_raises_into_the_background_thread`,
  `test_a_transient_failure_converges_on_a_later_retry`,
  `test_every_retry_says_so_at_warning`, `test_the_jitter_is_not_fed_back_into_the_base`,
  `test_the_loop_cancels_cleanly_while_waiting_to_retry`).

The suite is hermetic (`pytest-socket` autouse guard in `tests/conftest.py`); every
network failure above is produced by mocking at the seam, never by reaching the network.
