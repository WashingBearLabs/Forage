<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: operations, infrastructure
  required_sections:
    - "Logging"
  skip_if: no-infrastructure
-->
# MONITORING.md

> **TEMPLATE_INTENT:** Document logs, metrics, alerts, and dashboards. How to observe the system.

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-hostname-and-config US-001)

---

## Overview

Forage's observability surface is deliberately small and honest. `GET /health` always answers HTTP 200 and puts the truth in the body: `status` is `healthy` or `degraded`, and `degraded_reasons` says why in a closed vocabulary. Nothing that is broken is allowed to look healthy — that is `CLAUDE.md` invariant 5, written after a silent version of exactly this failure ran unnoticed in production for nine days.

Everything an operator has is reachable with `curl`, `docker logs`, and two scripts:

- `GET /health` — the honest-health body (this page, "Health Checks").
- `GET /metrics` — typed JSON counters in five sections (this page, "Metrics").
- Container stdout/stderr via `docker logs` — WARNING and ERROR lines only, with grep-able markers (this page, "Logging").
- `contract_smoke.py` and `searxng_smoke.py` — post-deploy probes (this page, "Post-Deploy Probes").

What does **not** exist, so nobody goes looking: no Prometheus exporter, no OpenTelemetry, no StatsD, no Sentry, no Datadog, no dashboards, no alert rules, no log shipping, no request tracing, no status page, and no `HEALTHCHECK` in the `Dockerfile` or in `compose/minimal.yml` / `compose/full.yml`. A grep of source, config, and docs for any of those integrations finds nothing. Whatever monitoring exists is the operator's own tooling polling `/health` and `/metrics`.

Related pages: `kit_tools/arch/SERVICE_MAP.md` (failure impact matrix per dependency), `kit_tools/arch/INFRA_ARCH.md`, `kit_tools/arch/SECURITY.md` (security-relevant logging), `kit_tools/docs/TROUBLESHOOTING.md` (symptom to remedy), `kit_tools/docs/DEPLOYMENT.md`, `kit_tools/arch/patterns/LOGGING.md` (logging conventions), and the canonical `docs/configuration.md` health-field reference.

---

## Quick Reference

Forage listens on `0.0.0.0:8020` inside the container; the compose fragments publish it on `127.0.0.1:8020` only (no auth exists on any endpoint, including `/docs`, `/redoc`, `/openapi.json`).

| Resource | How to reach it |
|----------|-----------------|
| Health body | `curl -s http://127.0.0.1:8020/health \| jq` |
| Metrics | `curl -s http://127.0.0.1:8020/metrics \| jq` |
| Contract version served | `curl -s http://127.0.0.1:8020/openapi.json \| jq -r .info.version` |
| Service log | `docker logs <container> 2>&1` |
| Process state / restarts | `docker inspect --format '{{json .State}}' <container>` |
| Post-deploy contract probe | `uv run python contract_smoke.py --base-url http://127.0.0.1:8020` |
| Companion SearXNG probe | `uv run python searxng_smoke.py --image <ref>` |

---

## Health Checks

### The endpoint

`GET /health` is unauthenticated, always returns HTTP 200, and is modelled by `retrieval_app.HealthResponse`. The HTTP status code tells you only that the process is up and serving; **check the body, not the status code**. `status` is `degraded` exactly when `degraded_reasons` is non-empty.

### Field reference

| Field | Type | Possible values | Meaning |
|-------|------|-----------------|---------|
| `status` | string | `healthy`, `degraded` | `degraded` iff `degraded_reasons` is non-empty. |
| `promptguard_loaded` | bool | `true`, `false` | Whether the PromptGuard classifier is loaded (`app.state.classifier.loaded`). Always honest; the break-glass override does not touch it. |
| `cache_connected` | bool | `true`, `false` | Valkey mode: a live ping via `cache.ping_if_due()`, subject to reconnect backoff. Memory mode: always `true` (the backend is in-process). Not a statement that Valkey is present; read `cache_backend` for that. |
| `capabilities` | dict | `{"search_sanitization": 1, "brave_api_key": 1}`, any subset, or `{}` | Presence map, two keys as of contract 1.2.0. `search_sanitization` is present when the classifier is loaded **or** when break-glass is armed (`FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION=1`, alias `POPPY_RETRIEVAL_LEGACY_CAPABILITY=1`, exact string `1`) — break-glass lies only for this key. `brave_api_key` is present when this start resolved a usable `FORAGE_BRAVE_API_KEY` (`brave_key_present()`), independently of whether `brave` is in `search_providers` and untouched by break-glass. |
| `sanitizer_revision` | string | 64-hex sha256 | `derive_sanitizer_revision(config)`: hash of eight `pipeline/*.py` sources plus `MODEL_ID@revision` plus `promptguard_threshold`. The literal `unknown` appears only when no lifespan ran (test transports). |
| `contract_version` | string | `1.3.0` | `pipeline.contract.CONTRACT_VERSION`; identical to `/metrics.contract_version` and `/openapi.json` `info.version`. |
| `cache_backend` | string | `valkey`, `memory` | Decided once at start: `VALKEY_URL` fully unset gives `memory`; set to anything else, including the empty string, gives `valkey`. |
| `search_providers` | list | `["searxng"]`, `["searxng", "brave"]`, ... | The resolved provider chain's names, in traversal order, after key-gated skips (contract 1.2.0, `search-policy-and-health` US-002). Configuration echo fixed for the life of the process — not a liveness probe, and not a statement that any provider is reachable right now. The check is to compare it against `FORAGE_SEARCH_PROVIDERS`: a configured `brave` that is missing here was skipped at boot for want of a usable key, and the startup signal for that is the WARNING `brave_skipped_missing_key`; `/health` itself cannot tell "configured but skipped" from "never configured" — by design, key presence is published, never a second differential channel (ruling 15). |
| `degraded_reasons` | list | `promptguard_unavailable`, `cache_unavailable` | Closed vocabulary (`pipeline/contract.py` `DegradedReason`). Ordered `promptguard_unavailable` first. Empty iff `status` is `healthy`. |

The two reasons are the complete set. `promptguard_unavailable` means the classifier is not loaded (no token, download in flight, verification refused, or load failed). `cache_unavailable` means `VALKEY_URL` is configured (set, even empty or unparseable) and the ping fails; it never appears in memory mode. The response is validated against `DegradedReason` on the way out, so a reason added to the handler without being added to the Literal fails loudly (500) rather than reaching a consumer unannounced.

### Body shape

Schema-style listing (field set and value domains as confirmed in `retrieval_app.HealthResponse`; not a captured payload):

```text
{
  "status":             "healthy" | "degraded",
  "promptguard_loaded": true | false,
  "cache_connected":    true | false,
  "capabilities":       {"search_sanitization": 1, "brave_api_key": 1} | {} | ...,
  "sanitizer_revision": "<64-hex sha256>",
  "contract_version":   "1.3.0",
  "cache_backend":      "valkey" | "memory",
  "search_providers":   ["searxng"] | ["searxng", "brave"] | ...,
  "degraded_reasons":   [] | ["promptguard_unavailable"] | ["cache_unavailable"] | ["promptguard_unavailable", "cache_unavailable"]
}
```

A stock image with no `HF_TOKEN` and no `VALKEY_URL` reports `status: degraded`, `promptguard_loaded: false`, `cache_connected: true`, `cache_backend: memory`, `capabilities: {}`, `search_providers: ["searxng"]`, `degraded_reasons: ["promptguard_unavailable"]` — that is the supported token-less mode, and it is what `contract_smoke.py` asserts by default.

### Gating on it

A consumer or deploy script should gate in this order:

1. **Contract.** Compare the MAJOR of `contract_version` against the vendored contract and refuse activation on mismatch (`CLAUDE.md` invariant 4, `contract/GOVERNANCE.md`). Compare contracts, never `sanitizer_revision` — Poppy and Forage revisions have diverged deliberately six times.
2. **Sanitization readiness.** Gate on `promptguard_loaded: true` (or on `capabilities.search_sanitization` if the consumer's own capability gate is what you are exercising, remembering break-glass can force that key on).
3. **Downloading versus wedged.** `/health` alone cannot distinguish "still downloading" from "retrying forever". Read `/metrics` `model.fetch_in_progress` and `model.retries_scheduled` (see "Reading `promptguard_loaded: false`" under Metrics). `fetch_in_progress` lives under `/metrics`, not `/health`. Poppy's deploy readiness wait reads `model.fetch_in_progress`; its exact behaviour lives in the other repo and is out of scope here.
4. **Cache.** Treat `cache_unavailable` as a performance signal, not an outage: `/retrieve` keeps working uncached.

### What `/health` does not tell you

- **SearXNG.** `/health` never probes SearXNG, and there is no degraded reason for it. SearXNG absence surfaces only per request as a `/search` 422 (`searxng_unavailable` or `searxng_error`). `README.md` and `docs/configuration.md` document this correctly.
- **Whether the fetch is progressing.** See item 3 above.
- **Anything about the consumer.** `/health` reports Forage's own state only.

### Startup budget

Measured boot on 1 vCPU / 1 GB (`docs/configuration.md`): 19 s cold (weights download) and 9 s warm (verified set already on the `HF_HOME` volume, zero network). Startup never blocks on weights: the lifespan yields immediately and `/health` answers `degraded` while `WeightAcquisition` runs as a background task. CI's smoke job and `contract_smoke.py` allow 120 s for `/health` to answer 200.

---

## Metrics

### Format and lifetime

`GET /metrics` is unauthenticated JSON modelled by `retrieval_app.MetricsResponse`: a `contract_version` string plus five sections — `extraction`, `search`, `retrieve`, `cache`, `model`. All six models are `extra="forbid"`, so an unmodelled counter makes the endpoint 500 rather than leak an unannounced field. It is JSON only: not Prometheus text format, not OpenTelemetry.

Every value is an **in-process counter or gauge**. They reset to zero on restart, are per container (one uvicorn worker), and are not scraped by anything in this repo. If you want history, poll and store them yourself.

### `extraction`

Backed by `retrieval_app.ExtractionMetrics`, `ExtractionAdmissionController`, and `_cgroup_memory_snapshot()`. Note the `/extract` route ships disabled (`extract_route_enabled: false` gives 404), so these stay at zero unless it is enabled.

| Counter | Kind | Increments when | A rising value means |
|---------|------|-----------------|----------------------|
| `requests` | counter | Every `/extract` that reached the handler, success or `PipelineError` (`record_verdict`). | Load on the extract route. |
| `busy_rejections` | counter | `ExtractionAdmissionController.acquire()` rejects: queue full (`admission_queue_depth`) or upload-byte reservation exceeded. Each is a 429 `busy`. | Callers are being turned away; raise capacity or slow the caller. |
| `semaphore_saturation` | counter | `acquire()` found all slots busy, whether the request then queued or was rejected. Always ≥ `busy_rejections`. | Sustained contention, even before anything is rejected. |
| `active` | gauge | Extraction slots in use now. | — |
| `queued` | gauge | Requests waiting for a slot now (`len(controller._waiters)`). | — |
| `queued_bytes` | gauge | Reserved bytes = waiters × `max_input_bytes` (conservative). | — |
| `verdicts` | map | Per outcome: `success`, `injection_detected`, or an `/extract` 422 error code. | Injection or failure mix on uploaded documents. |
| `cgroup_memory_current_bytes` | gauge or null | `/sys/fs/cgroup/memory.current`; `null` on macOS or a plain host. | — |
| `cgroup_memory_max_bytes` | gauge or null | `/sys/fs/cgroup/memory.max`; `null` when unreadable or the literal `max`. | — |
| `oom_proximity_ratio` | ratio or null | `current / max`; flat inside `extraction`, not nested (wire contract). | Approaching 1.0 means the container is near its `mem_limit`. |

### `search`

Backed by `retrieval_app.SearchMetrics`.

| Counter | Kind | Increments when | A rising value means |
|---------|------|-----------------|----------------------|
| `policy_invalid_domain_entry` | counter (per entry) | Reserved at zero until US-002 adds `/search` domain policy, then once per invalid denylist entry dropped; the offending entry is never stored or logged. Unlike `/retrieve`, this route has no allowlist byte-budget drops. An over-budget denylist will be a 422 `search_unavailable` / `policy_domain_list_too_large`, never a partial list or a drop count. | Malformed consumer denylist entries; check the consumer's raw list, not provider health. The two `/retrieve` causes are intentionally combined there, but only invalid entries apply here. |
| `policy_suffix_trusted_skip` | counter (reserved) | Always zero: reserved for parity with `/retrieve`, which covers wildcard **trusted and verified** resolutions. `/search` has no trust tiers. | A non-zero value is an instrumentation defect, not a search classifier skip. |
| `requests` | counter | Every `/search` that reached the handler. | Load. |
| `errors` | map | `record_error(exc.error)`; three keys. `searxng_error` (SearXNG answered non-2xx) and `searxng_unavailable` (connection refused, DNS, 10 s timeout, bad JSON) are raised only when the configured chain is a lone `searxng`; `search_unavailable` (contract `1.2.0`) covers every other chain, with reason `<provider_name>: <failure_class>` — or, raised by the handler before any provider is called, the fixed literal `policy_excluded_all_providers` when a request's `providers` / `allow_paid_fallback` leaves a paid-only chain empty (`search-policy-and-health` US-010). | The companion is down or throttling. `searxng_error` with reason "SearXNG returned HTTP error (http_429)" means the SearXNG limiter was turned on. `search_unavailable` names its provider in the 422 `reason`, not in the counter key — read the logs or the response to tell which one failed; a `policy_excluded_all_providers` reason is a consumer's policy, not a provider failure. |
| `omitted_by_reason` | map | Results dropped before return, keyed by `contract.OMISSION_REASONS`: `invalid_url`, `structural_blocked`, `injection_detected`, `promptguard_unavailable`, and — added in contract `1.3.0` — `blocked_url`; anything else lands in `other`. | `promptguard_unavailable` rising: fail-closed omissions on a degraded container — the consumer sees thin or empty results. `structural_blocked` rising after `hardening-search-sanitization` US-001 is **expected** — see the caveat below. A rising `blocked_url` count means a provider is returning internal addresses; `provider` plus `host_class` on the `search_url_blocked` log line is the aggregation key for that alert. |
| `unscanned_results` | counter | `+= response.unscanned_results` — fail-open results returned without an ML scan. | Unsanitized results are reaching the consumer. |
| `fallback_fired` | counter | Once per `/search` request whose provider chain advances past the first provider (`search-fallback` US-003; the per-process count of the per-response `fallback_fired` bool) — including a request that ends in a 422. | Free search is failing often enough that the chain is advancing; correlate with `search_provider_failed` WARNINGs and the `errors` map to see which provider is unreliable. |
| `paid_calls` | counter | Once per call to a `paid=True` configured provider, incremented before the call so a call that times out is still counted — whether or not it served the response. | Spend. `paid_calls` rising **faster** than `fallback_fired` means a paid provider is first in the configured chain — `FORAGE_SEARCH_PROVIDERS` names it ahead of every free provider, or names no free provider — so it is called without the chain advancing. A per-request `providers` / `allow_paid_fallback` policy cannot cause this: it only removes paid providers, never adds or reorders one. Rising together at 1:1 means a standard free-first chain is falling back to the paid provider every time it advances. |
| `classification_wait_timeouts` | counter | Once per `/search` request whose per-request PromptGuard wait budget expired (`hardening-retrieve-parity` US-006) — **once per request**, however many of its results went on unscanned. Never moves when the classifier is absent or still warming: that path does not touch the permit. | Permit contention, not a missing model. Read it with `/health` `promptguard_loaded` — `true` plus this counter rising is a busy classifier, `false` is the degraded-model shape instead. Fail-closed requests show up as `omitted_by_reason.promptguard_unavailable`; fail-open ones as `unscanned_results`. |
| `policy_unknown_provider` | counter | Once per ignored entry in a request's `providers` list: one for every entry past the first eight, plus one for every entry among the first eight that, after `strip()` and lower-casing, names no provider in the configured chain (duplicates each count). The `/search` handler adds the ignored count `apply_request_policy` returns, before any provider is called; the offending name itself is never stored (`search-policy-and-health` US-010). | A consumer is sending `providers` entries this deployment ignores (ignored, not rejected). To find which, compare the consumer's `providers` names against `/health` `search_providers`, the resolved chain. If a missing name is `brave`, check `/health` `capabilities`: no `brave_api_key` entry means the key is absent or invalid — a key problem, not a name problem; `brave_api_key: 1` with no `brave` in `search_providers` means keyed but not chained — `FORAGE_SEARCH_PROVIDERS` leaves it out. Any other missing name is a bad name on the consumer's side. Entries past the eighth also count, whatever they name, so a consumer whose names all appear in `search_providers` but who sends more than eight entries still moves the counter. |

**Caveat — a rising `structural_blocked` after `hardening-search-sanitization` US-001 is expected, and no counter separates it from a true block.** Before that story `/search` collapsed every newline in `title` and `snippet` *before* the Stage 2 scan, so the three line-anchored BLOCK patterns (`^assistant:` under `MULTILINE | IGNORECASE`, `^System:` and `^POPPY:` under `MULTILINE`, `pipeline/stage2_structural.py`) could only fire at a field's first character. They now fire on **any** line — the parity `/retrieve` has always had — and chat transcripts, Q&A pages, API docs and release notes routinely carry a line beginning `assistant:` or `System:`. Every such result is dropped whole into the same `structural_blocked` bucket, indistinguishable there from a genuine injection: `omitted_by_reason` carries no sub-reason and no counter can tell the two apart. The rollback signal is not a counter but the **Stage-2 block log** (`Omitting blocked search result (%s structural): %s` in `run_search_pipeline`), aggregated by pattern name: a rise concentrated on `authority_impersonation` with benign-looking URLs is the yield cost; a spread across patterns is not.

**Caveat — the yield cost of `hardening-search-sanitization` US-002's URL rules is visible only in the logs, not in `/metrics`.** US-002 rejects two classes of URL that were served before it: one carrying an unencoded RFC 3986 excluded character (`|`, `{`, `}`, `^`, backtick — some engines return these unencoded in query strings) and one longer than 2 048 characters (served *shortened*, pointing at a different resource, before the story). Both land in the same `omitted_by_reason.invalid_url` bucket as every other URL rejection, so the counter cannot separate them. The signal is the **rejection log** — one content-free `search_url_rejected rule=<token> provider=<name>` record per rejection, INFO, where `<token>` is one of `missing`, `too_long`, `raw_chars`, `unparseable`, `invalid_port`, `parse`, `userinfo`, `host_code_point`, `zone_id` — aggregated by `provider` **plus** `rule`. `rule=raw_chars` or `rule=too_long` climbing on one provider is the yield cost; the other seven tokens are URLs that were already being rejected. Neither the URL nor its host is ever in the record (invariant 6), so there is nothing to correlate against but the pair. There is no `/metrics` counter for this and none is planned: a per-rule counter would be a wire change for a yield question.

**Caveat — `blocked_url` and `invalid_url` short-circuit the content scan.** The URL rules run before Stage 2 sees a result's title and snippet, and the first reason wins, so a result dropped under `blocked_url` or `invalid_url` is never scanned: `structural_blocked` and `injection_detected` **undercount** by exactly the payloads that happened to ride a rejected or blocked URL. Read the three counters together rather than treating a falling `structural_blocked` as a quieter web. `hardening-search-sanitization` US-003 adds two tokens to the `search_url_rejected` vocabulary — `numeric_host` (a digit-run host that is not a canonical dotted quad) and `idna` (a host the UTS-46 encode refuses) — and a second, deliberately disjoint record, `search_url_blocked host_class=<token> provider=<name>`, INFO, with `<token>` one of `private_literal`, `embedded_private` or `blocklisted_name`. The two vocabularies never share a token, so an aggregation on one never silently picks up the other, and neither record carries the URL or its host.

**Caveat — `fallback_fired` and `paid_calls` stay at zero on a `searxng`-only deployment.** `run_search_pipeline` increments both during traversal — `fallback_fired` only when the chain advances past its first provider, `paid_calls` only when it calls a `paid=True` provider — and a `searxng`-only deployment (the default, no paid key configured) has no second provider to advance to and no paid provider to call, so both stay at zero forever. `policy_unknown_provider` is not in this caveat: the handler increments it before traversal, so it moves on any deployment — on a `searxng`-only one, every `brave` entry a consumer sends is ignored and counted. A `searxng`-only deployment's first-party signal for free-search trouble is the `search_provider_failed` WARNING per failed call and `errors.searxng_unavailable` / `errors.searxng_error`, not a counter — see the `search` `errors` row above and the "SearXNG failures" row in Signals Worth Watching below.

**Alert condition.** Watch `paid_calls`' rate over a rolling window (e.g. per day), sized against Brave's included query credit — roughly 1,000 queries/month per owner decision 2 (`kit_tools/specs/epic-search-providers.md`), i.e. an average budget of about 33/day before the $5/1,000 overage rate applies. A sustained rate above that budget means the paid provider is absorbing more of the traffic than the credit covers. **Remedy:** remove the paid provider's token from `FORAGE_SEARCH_PROVIDERS` and restart the container — the chain is resolved once, at boot, in the lifespan, so there is no live toggle.

### `retrieve`

Backed by `retrieval_app.RetrieveMetrics`.

| Counter | Kind | Increments when | A rising value means |
|---------|------|-----------------|----------------------|
| `policy_invalid_domain_entry` | counter (per entry) | Once per dropped request-list entry, not per request: invalid entries in any of the three lists, or every entry in an allowlist's over-budget remainder. The offending entry is never stored or logged. Invalid and over-budget allowlist drops deliberately share this counter because both only narrow privilege; a denylist is never truncated — over budget it is refused whole, 422 `content_too_large` / `policy_domain_list_too_large`, with no drop count. | Check consumer spellings and raw UTF-8 bytes (including newline separators) against `policy_domain_entries_max_bytes`, separately per list. More than 64 entries alone is not a cause; there is no entry-count cap. |
| `policy_suffix_trusted_skip` | counter (per resolution) | Once when an uncached retrieval resolves to **trusted or verified** via a leading-dot entry. Trusted skips classification; verified may degrade open when classification is unavailable, and is counted even if the model is available. A bare exact-entry match adds zero. Cached responses do not resolve tiers again. | Wildcard privilege is being used. Audit caller suffixes, especially multi-tenant apexes; this measures both tiers, not only actual skipped classifications. |
| `requests` | counter | Every `/retrieve` that reached the handler. | Load. |
| `semaphore_saturation` | counter | Watches the **`/retrieve` admission gate** (`retrieve.fetch_concurrency`), **not** the classification permit — that one is `classification_wait_timeouts` below. Moves when `acquire()` found every fetch slot busy, whether the request then queued or was refused; always ≥ `busy_rejections` (`hardening-retrieve-parity` US-002). | Fetch-and-extract contention. Look at `fetch_concurrency`, the queue bounds and the size of what `/retrieve` is pointed at — not at PromptGuard. |
| `busy_rejections` | counter | The **`/retrieve` admission gate** refused a request 422 `busy` / `admission_queue_full`: the queue was at `retrieve.admission_queue_depth` or its byte reservation would have exceeded `retrieve.max_queued_fetch_bytes`. Also counted under `errors["busy"]`. | Callers are being turned away at the fetch gate. Raise `admission_queue_depth` or `max_queued_fetch_bytes`, or slow the caller; `fetch_concurrency` is pinned. |
| `errors` | map | Keyed by `RetrieveErrorCode`: `blocked_domain`, `busy`, `content_too_large`, `extraction_failed`, `fetch_error`, `fetch_timeout`, `invalid_url`, `private_ip`. Keyed by code alone, never by reason: `extraction_failed` (`hardening-retrieve-parity` US-003) counts every fetched-PDF failure together — `pdf_encrypted`, `pdf_no_text`, `pdf_extraction_error` and the host fault `pdf_spool_error`. | Which 422 the callers are hitting; `private_ip` and `invalid_url` are validator refusals, `extraction_failed` is the PDF worker or its spool, the rest are fetch outcomes. `extraction_failed` rising is usually the input (encrypted or scanned PDFs); the one host fault inside it is visible only in the logs — see the `retrieve_spool_error` alert below. |
| `cache_hits` / `cache_misses` | counters | Request outcome from `content.cache_hit` (the content-cache layer, distinct from `cache.storage_*` below). | Hit ratio of the content cache. |
| `blocked_by_reason` | map | First injection span's diagnostic if it is in `contract.DIAGNOSTICS`: `structural_injection_detected`, `promptguard_injection_detected`, `promptguard_unavailable`; otherwise `other`. | Quarantine rate by cause. |
| `promptguard_state` | map | Per-response `promptguard_state`: `scanned`, `skipped_trusted`, `structural_blocked`, `unavailable_blocked`, `unavailable_allowed`, `other`. | `unavailable_blocked` rising is the nine-day-silent-failure shape: the consumer receives content-free responses while everything "works". Since `hardening-retrieve-parity` US-006 the two `unavailable_*` states have a second cause — a classification wait that expired — so read them with `classification_wait_timeouts` below. |
| `classification_wait_timeouts` | counter | Once per `/retrieve` request that waited `promptguard_wait_seconds` for the classification permit and gave up, taking the classifier-unavailable outcome under its own `promptguard_fail_closed` (`hardening-retrieve-parity` US-006). Never moves when the classifier is absent or still warming, or on a `trusted_domains` page: none of those touch the permit. | Permit contention, not a missing model. Zero with `unavailable_*` rising means the model is genuinely absent; non-zero means requests are queueing behind classification. |

**Head-of-line blocking: which route holds the permit.** All three classifying routes share one permit (`extraction.classification_concurrency`, shipped at 1), and they do not hold it for comparable lengths of time. A `/retrieve` classifying a 256-chunk fetched page holds it for orders of magnitude longer than any `/search` snippet, and `/extract` holds it for one bounded document. So a spike in either `classification_wait_timeouts` counter under mixed traffic correlates with **large fetched pages**, not with search volume: check `retrieve.requests` and the size of what `/retrieve` is being pointed at before touching the search path. The sizing rule (`docs/configuration.md`) is that `promptguard_wait_seconds` must exceed `retrieve.max_promptguard_chunks` × the per-window classify latency; while that key is `0` — the shipped default — the hold is bounded only by the 10 MB fetch cap and timeouts are expected under modest concurrency.

**`/extract` has no wait timeout and no counter of its own.** Its acquisition is untimed by design (it is the authenticated route and would rather wait than serve unscanned), so a hanging `/extract` under fetch load produces no counter movement anywhere — only latency. The coupling is one-directional: `/retrieve` and `/search` traffic can block `/extract` classification, never the reverse. `extract.route_enabled` ships `false`, so this is latent until the route is turned on.

### `cache`

Backed by `cache.CacheMetrics` — storage operations and policy-layer parse failures,
not request outcomes.

| Counter | Kind | Increments when | A rising value means |
|---------|------|-----------------|----------------------|
| `reconnect_attempts` / `reconnect_successes` / `reconnect_failures` | counters | `ValkeyStorage._ensure_client`, gated by backoff (1 s doubling to a 30 s cap, 2 s connect+ping deadline). Also driven by `/health`'s `ping_if_due()`, so polling `/health` itself exercises these. | `reconnect_failures` climbing: Valkey unreachable; `attempts` climbing with `successes` climbing: flapping. |
| `operation_failures` | counter | `ValkeyStorage._mark_disconnected` after a `get` / `set` / `delete` raised. | Valkey dropping mid-run; `/retrieve` continues uncached. |
| `storage_hits` / `storage_misses` | counters | Key lookups, both backends. | — |
| `storage_evictions` / `storage_oversize_skips` | counters | `InMemoryStorage` only; always 0 on Valkey. | Memory-mode cache bounds being hit. |
| `corrupt_entries` | counter | `ContentCache` cannot parse stored bytes as `RetrievedContent`; the value is treated as a miss and deletion is attempted. Both backends. | Schema drift or invalid values from another writer. Not an authenticity or tampering counter: parseable values are still served. Correlate the WARNING `cache_entry_corrupt` by its `ret:<sha256>` key digest. |

A corrupt value is a `storage_hits` increment but a `retrieve.cache_misses` outcome.
If deletion fails, the ordinary `operation_failures` counter and closed WARNING also
move; the request still proceeds as a miss.

### `model`

Backed by `model_fetcher.ModelMetrics`.

| Counter | Kind | Increments when | A rising value means |
|---------|------|-----------------|----------------------|
| `fetch_failures` | counter | Per source leg that was reached and failed (not skipped, not refused by the verifier); +1 when **no** leg was attempted at all (token-less container); +1 on an acquisition crash. | Weights are not arriving; read the `weights_*` log markers for the code. |
| `verify_failures` | counter | Per `verify_weights()` refusal. | The download does not match `weights_manifest.json` (hash, size, extra or missing file). |
| `quarantines` | counter | Per successful move of a refused set to `$HF_HOME/quarantine/`. | Same as above, and disk is being consumed by refused sets. |
| `fetch_in_progress` | bool | `true` during `snapshot_download` or the oras pull + extract + verify. | A download is happening now. |
| `retries_scheduled` | counter | Each time `WeightAcquisition.run()` arms a retry (30 s doubling to 600 s, ±20 % jitter, forever). | The loop is waiting out backoff between failed rounds. |

### Reading `promptguard_loaded: false`

`/health` says the classifier is not loaded; `/metrics.model` says why it is not loaded *yet*:

| `fetch_in_progress` | `retries_scheduled` | State |
|---------------------|---------------------|-------|
| `true` | any | Downloading or verifying right now. Wait. |
| `false` | > 0 | A round failed; waiting out backoff before the next attempt. Read `docker logs` for the last `weights_unavailable` line and its `Attempts: huggingface=<code>, mirror=<code>`. |
| `false` | 0 | The first attempt has not finished (or just started). If this persists past the cold-start budget, read the log. |

### Viewing

```bash
curl -s http://127.0.0.1:8020/metrics | jq
curl -s http://127.0.0.1:8020/metrics | jq .model
curl -s http://127.0.0.1:8020/metrics | jq '.cache | {reconnect_failures, operation_failures}'
curl -s http://127.0.0.1:8020/metrics | jq '.retrieve.promptguard_state'
```

---

## Logging

### Configuration: what reaches `docker logs` and why INFO does not

Nothing in the repo configures logging — no `logging.basicConfig`, no `dictConfig`, no log-level environment variable, and the `Dockerfile` `CMD` is `uvicorn retrieval_app:app --host 0.0.0.0 --port 8020` with no `--log-level` or `--log-config`. The root logger therefore keeps Python's default effective level, **WARNING (30)**, verified in a running container (`kit_tools/docs/GOTCHAS.md`, "Nothing configures logging"). Consequences:

- Every first-party `logger.info(...)` and `logger.debug(...)` is dropped inside the container. Only WARNING, ERROR, and `logger.exception` lines reach the log.
- `uvicorn --log-level info` does **not** raise first-party output (measured at model-bootstrap US-004); the only working lever is `logging.basicConfig(level=...)` from a Python entry point, and that is deliberately not done.
- Logs go to stdout/stderr of PID 1. `docker-entrypoint.sh` is `set -euo pipefail; exec "$@"` and prints nothing — deliberately, because `VALKEY_URL` may carry a password and a chatty entrypoint would be the one place it leaked. Read them with `docker logs <container>`.
- CI's `smoke` job dumps `docker inspect --format '{{json .State}}'` and `docker logs` on failure (`.github/workflows/ci.yml`).

Line format (**inferred from stdlib behaviour, not observed** — the exploration verified only the effective level): with no handler configured, first-party WARNING/ERROR lines fall to `logging.lastResort`, a stderr handler whose format is `%(message)s`, so expect the bare message with no timestamp, level, or logger-name prefix. `extra={...}` dicts on log calls are never rendered. Uvicorn installs its own default config — `uvicorn.error` to stderr and `uvicorn.access` to stdout, both INFO, `propagate=False` — so access lines like `INFO: 127.0.0.1:x - "GET /health HTTP/1.1" 200 OK` do appear per request (also inferred from uvicorn's defaults). They are the only per-request record: the app has no request-id middleware, and the `request_id` minted per request and returned in every response body is never written to a visible log line.

### Logger names

All are `logging.getLogger(__name__)`: `retrieval_app`, `cache`, `model_fetcher`, `promptguard.classifier`, `pipeline.orchestrator`, `pipeline.stage3_promptguard`, `pipeline.stage5_url_audit` (declared, no emit sites), and `url_validator` (DEBUG only, never visible).

### Startup lines you may see

| Level | Line | When |
|-------|------|------|
| WARNING | `config.yaml not found at <path>` | `/app/config.yaml` missing; all code defaults apply. |
| WARNING | `config_invalid_value — key=<list> dropped=<n> entries=<entries>` (domain-list form names dropped entries) | Invalid `seed_blocklist` or `news_domains` entries dropped at boot; one WARNING per list with drops, none for valid lists. Misplaced credential/URL-shaped entries are redacted. Request counters are not incremented. |
| WARNING | `break_glass_advertisement_active — <var>=1 is forcing /health to advertise search_sanitization regardless of classifier state; ...` | Break-glass armed. `capabilities` will lie; `status` and `promptguard_loaded` stay honest. |
| WARNING | `Content cache not available at startup` | `VALKEY_URL` set and the 2 s connect+ping deadline failed. |
| WARNING | `PromptGuard model not available — ML injection detection disabled` (with traceback, `exc_info=True`) | The verified weight set failed to load (torch/transformers); followed by ERROR `weights_load_failed`. |

Dropped (INFO): `Sidecar config loaded (<n> keys); contract_version=<v>`, `Content cache connected (valkey|memory)`, `PromptGuard 2 model loaded successfully`. A boot that fails outright (bad `extraction:` or `cache:` value in `config.yaml`) exits the container with a traceback in `docker logs`.

### Closed vocabularies

**`cache.py`** — `_closed_vocabulary_reason()` yields exactly one of `connect_failed`, `operation_failed`, `timeout` (the last when the exception is a `TimeoutError`). The parse guard logs `cache_entry_corrupt` directly, not through that exception mapper:

| Level | Line | Reason values |
|-------|------|---------------|
| WARNING | `Valkey connection failed for content cache (<reason>)` | `connect_failed`, `timeout` — from `_attempt_connect`. |
| WARNING | `Content cache operation failed (<reason>)` | `operation_failed`, `timeout` — from `_mark_disconnected`. |
| WARNING | `Content cache entry rejected (cache_entry_corrupt) key=ret:<sha256>` | Fixed token and one-way key digest only, never the value, URL or exception text. |

**`pipeline/orchestrator.py`** — the classification-wait timeout has its own closed token, deliberately distinct from stage 3's "PromptGuard unavailable" lines, because the classifier in this case is *loaded and busy* rather than missing and those lines would send an operator to the model loader.

| Level | Line | Values |
|-------|------|--------|
| WARNING | `classification_wait_timeout route=<route>` | `route=retrieve`, `route=search`. Nothing caller-derived: no URL, no query, no host. One per `/retrieve` request; one per `/search` request, not per result. |

**`model_fetcher.py`** — every line carries a grep-able marker prefix. Levels as emitted; INFO markers are listed because they exist in code, but they are dropped in the container.

| Level | Marker | Meaning |
|-------|--------|---------|
| ERROR | `weights_unavailable` | The one terminal line per failed round: `no source produced verified weights at revision=<rev> ... Attempts: huggingface=<code>, mirror=<code>`. |
| ERROR | `weights_fetch_failed` | One source leg failed: `source=<huggingface|mirror> revision|reference=<ref> reason=<code> ...`. |
| ERROR | `weights_verification_failed` | `refusing to load; reasons: <codes>`. |
| ERROR | `weights_quarantined` | `refused weight set moved to <path>`. |
| ERROR | `weights_quarantine_failed` | Could not move the refused set out of the hub tree; it must not be loaded. |
| ERROR | `weights_pin_unusable` | `weights_manifest.json` pins no verifiable file set; no download can be blessed. Rebuild the image from `main`. |
| ERROR | `weights_load_failed` | The verified set did not load; PromptGuard stays unavailable. |
| ERROR | `weights_acquisition_crashed` | `logger.exception` with traceback; `/health` stays degraded. |
| ERROR | `weights_mirror_invalid` | `FORAGE_WEIGHTS_MIRROR` is malformed; the value is echoed redacted (`***@host/...`). |
| ERROR | `model_revision_invalid` | `FORAGE_MODEL_REVISION` is not a 40-character sha; falls back to the committed pin. The value is not echoed. |
| WARNING | `weights_fetch_skipped` | No `HF_TOKEN` in the environment; the gated repo cannot be reached. |
| WARNING | `weights_mirror_skipped` | No `FORAGE_MIRROR_TOKEN`; the private mirror cannot be reached. |
| WARNING | `weights_retry_scheduled` | `no verified weights yet; retry <n> in <s>s (base <s>s, jittered +/-20%)`. |
| WARNING | `weights_acquisition_in_flight` | A second acquisition was requested while one runs; not started. |
| INFO (dropped) | `weights_fetch_attempt`, `weights_fetched`, `weights_verified`, `weights_loaded` | Progress lines; invisible in the container. |

Reason and outcome codes that appear after `reason=`:

- Hugging Face leg (`_fetch_reason`): `http_<status>` (401/403 = token lacks access, 404 = revision gone), `timeout`, `io_failed`, `fetch_failed`.
- Mirror leg: `skipped_no_token`, `misconfigured`, `oras_missing`, `insufficient_space`, `pull_failed`, `timeout`, `no_artifact`, `artifact_oversized`, `extract_failed`, `install_failed`, `refused_verification`.
- Verifier (`REASON_*`): `manifest_missing`, `manifest_unreadable`, `manifest_empty`, `manifest_unparseable`, `manifest_invalid`, `manifest_disallowed_format`, `snapshot_missing`, `file_missing`, `file_extra`, `disallowed_format`, `size_mismatch`, `hash_mismatch`, `unreadable_file`, `symlink_escape`, `disallowed_entry`.

A token-less container logs `weights_fetch_skipped`, `weights_mirror_skipped`, `weights_unavailable ... huggingface=skipped_no_token, mirror=skipped_no_token`, then `weights_retry_scheduled`, and repeats roughly every ten minutes forever. That is by design (supported degraded mode), not a fault.

### Per-request lines on a degraded container

These are WARNING, so they are visible, and they arrive once per `/retrieve` or per `/search` result while PromptGuard is unavailable — expect volume:

| Logger | Line | When |
|--------|------|------|
| `pipeline.stage3_promptguard` | `PromptGuard unavailable — fail-closed for <tier> tier` | `standard` / `untrusted` tier with `fail_closed=True`; the content is quarantined as a precaution. |
| `pipeline.stage3_promptguard` | `PromptGuard unavailable — <lenient fallback|fail-open> for <tier> tier` | `verified` tier, or fail-open configuration. |
| `pipeline.orchestrator` | `Content quarantined for <url> — returning content-free response` | Any injection verdict, degraded or not. Carries the requested URL. |
| `pipeline.orchestrator` | `search_promptguard_local_latency_target_exceeded` | PromptGuard time on a `/search` exceeded the 1000 ms local target; the `extra` dict is not rendered, so this is the whole line. |
| `promptguard.classifier` | `classify() called but model not loaded — returning safe fallback` | Classifier invoked while unloaded. |

### What is never logged

`VALKEY_URL`, its password or host, `HF_TOKEN`, `FORAGE_MIRROR_TOKEN`, `str(exc)` on cache failures, oras stdout/stderr, and `huggingface_hub` exception text. Mirror references pass through `redact_reference()` before logging. No document text and no query text is logged; visible request-derived values include the requested URL in the quarantine WARNING and the one-way key digest in the `cache_entry_corrupt` WARNING (result URLs appear only at INFO). Convention (`kit_tools/docs/CONVENTIONS.md`, "Logging"): never log a credential-bearing value; closed reason vocabularies over prose; machine-readable codes in anything a consumer parses. See `kit_tools/arch/patterns/LOGGING.md` for the full conventions and `kit_tools/arch/SECURITY.md` for the security-relevant logging section.

### Leakage assertions

The vocabulary is enforced by tests, not by review:

- `tests/test_cache.py` (around lines 755-807) captures the `cache` logger at WARNING and asserts `"secret" not in caplog.text`, `"unreachable" not in caplog.text`; on the startup path with `VALKEY_URL` set it asserts `Content cache not available at startup` is present while the password, the full URL, and the hostname are absent.
- `tests/test_model_fetcher.py` asserts marker names present or absent per scenario (`weights_fetch_skipped`, `weights_fetch_failed`, `weights_verification_failed`, `weights_quarantine_failed`, `weights_pin_unusable`, `weights_load_failed`, `weights_acquisition_crashed`), `assert token not in caplog.text`, `assert "http_401" in caplog.text`, `assert "gated repo" not in caplog.text`, and that the mirror secret never appears.

### Searching logs

There is no query language; it is `docker logs` and `grep`. Because `request_id` is never written to a visible line, you cannot find a request by ID in the log — correlate by the uvicorn access line's timestamp instead.

```bash
# Everything the service has said (stdout + stderr)
docker logs <container> 2>&1

# Weights acquisition: terminal lines and the per-leg codes behind them
docker logs <container> 2>&1 | grep -E 'weights_unavailable|weights_fetch_failed|weights_fetch_skipped|weights_mirror_skipped'

# Verifier refusals and quarantines
docker logs <container> 2>&1 | grep -E 'weights_verification_failed|weights_quarantined|weights_pin_unusable'

# Cache trouble (closed vocabulary; the URL never appears)
docker logs <container> 2>&1 | grep -E 'Valkey connection failed|Content cache (operation failed|not available)|cache_entry_corrupt'

# Quarantines and fail-closed decisions
docker logs <container> 2>&1 | grep -E 'Content quarantined|PromptGuard unavailable'

# Per-request access lines from uvicorn
docker logs <container> 2>&1 | grep -E '"(POST|GET) /(retrieve|search|extract|health)'

# Break-glass armed?
docker logs <container> 2>&1 | grep break_glass_advertisement_active
```

### Log retention

Nothing in this repo configures log rotation, retention, or a Docker logging driver (neither the `Dockerfile` nor `compose/*.yml` sets one), so the Docker daemon's default driver and its defaults apply. If you need history beyond what the daemon keeps, rotate or ship at the host level; Forage will not do it for you.

---

## Signals Worth Watching

These are **suggested watch points**, not configured alerts. No thresholds are defined anywhere in the repo; the values below are the exploration's inference and need an operator's judgement for the deployment in front of them.

| Condition | `/health` | `/metrics` | `docker logs` |
|-----------|-----------|------------|---------------|
| Weights never arrive | `promptguard_loaded: false` and `promptguard_unavailable` past the cold-start budget (19 s cold measured; CI allows 120 s) | `model.fetch_failures` rising with `model.retries_scheduled` climbing and `fetch_in_progress: false` | ERROR `weights_unavailable ... Attempts: huggingface=<code>, mirror=<code>`, preceded by `weights_fetch_failed` (token lacks access: `http_401` / `http_403`; revision gone: `http_404`) or `weights_fetch_skipped` (no token) |
| Download refused by the verifier | same as above | `model.verify_failures` and `model.quarantines` rising; `fetch_failures` unchanged for `refused_verification` | ERROR `weights_verification_failed — reasons: file_extra|hash_mismatch|...`, then `weights_quarantined` |
| Verified set will not load | same as above | — | WARNING `PromptGuard model not available` with traceback, then ERROR `weights_load_failed`; check `mem_limit` (torch OOM) |
| Cache flapping or down | `cache_unavailable` appearing and disappearing; `cache_connected` toggling (Valkey mode only) | `cache.reconnect_attempts` / `reconnect_failures` climbing; `cache.operation_failures` rising on mid-run drops | WARNING `Valkey connection failed for content cache (connect_failed|timeout)`; `Content cache operation failed (operation_failed|timeout)` |
| Quarantine rate | — | `retrieve.blocked_by_reason.*` rising; `retrieve.promptguard_state.unavailable_blocked` rising on a degraded container (consumer sees content-free responses — the nine-day shape) | WARNING `Content quarantined for <url>`; `PromptGuard unavailable — fail-closed for <tier> tier` |
| Search results silently thinning | — | `search.omitted_by_reason.promptguard_unavailable` rising (fail-closed) or `search.unscanned_results` rising (fail-open) | same `PromptGuard unavailable` WARNING per result |
| Classification permit contention | — | `retrieve.classification_wait_timeouts` or `search.classification_wait_timeouts` rising while `/health` reports `promptguard_loaded: true` | WARNING `classification_wait_timeout route=<retrieve\|search>` |
| Fetched-PDF spool failing (host fault) | — | `retrieve.errors.extraction_failed` rising — but that count cannot tell a spool failure from an encrypted-PDF caller, so it is not the signal | WARNING `retrieve_spool_error` — the closed token alone, nothing path- or content-derived. **Alert on any occurrence**: it is a host fault, not an input (ENOSPC, EACCES, a read-only or vanished `TMPDIR`, or a spool directory refused as a symlink, foreign-owned or group/other-accessible). Each one is a `/retrieve` 422 `extraction_failed` / `pdf_spool_error`. Check free space and permissions on `TMPDIR` and `<TMPDIR>/forage-spool-<uid>` (`docs/configuration.md`, "The spool directory"). A per-reason `/metrics` map is deferred |
| Admission pressure on `/retrieve` | — | `retrieve.busy_rejections` rising (callers get 422 `busy` / `admission_queue_full`); `retrieve.semaphore_saturation` rising first | — |
| Admission pressure on `/extract` | — | `extraction.busy_rejections` rising (callers get 429 `busy`); `semaphore_saturation` and `queued` rising first; `oom_proximity_ratio` approaching 1.0 (explorer suggested watching above 0.9) | — |
| SearXNG failures | **no signal** — `/health` does not probe SearXNG; `search_providers` is configuration echo, not liveness, so it reads the same whether or not SearXNG is currently reachable | `search.errors.searxng_unavailable` / `searxng_error` rising; per-request 422 only. On a `searxng`-only chain (the default), `search.fallback_fired` and `search.paid_calls` never move — there is no second provider to advance to — so they are not a signal here either. | `search_provider_failed` WARNING per failed call (nothing else first-party; the 422 `reason` echoes the scheme, host and port of `SEARXNG_URL` — userinfo stripped — plus a closed `detail` token, never exception text) |
| Paid provider absorbing spend | — | `search.paid_calls` rate over a window climbing past Brave's ~1,000-query/month included credit (~33/day) | `search_provider_failed` WARNINGs naming the free provider precede a rising `paid_calls`; remedy is removing the paid provider from `FORAGE_SEARCH_PROVIDERS` and restarting (chain resolves once, at boot) |
| Break-glass left armed | `capabilities.search_sanitization` present while `promptguard_loaded: false` | — | WARNING `break_glass_advertisement_active` at startup |
| Boot failure | no answer on 8020 | — | traceback from `ExtractionConfigurationError` or the cache-settings validator; `docker inspect` shows the exit |

For symptom-to-remedy detail (the empty-string `VALKEY_URL` trap, the SearXNG limiter 429, the 400-not-413 `/extract` behaviour, re-vendoring weights) see `kit_tools/docs/TROUBLESHOOTING.md`; for the per-dependency blast radius see `kit_tools/arch/SERVICE_MAP.md`.

---

## Post-Deploy Probes

Both scripts live at the repo root, run from a checkout via `uv run`, and are **not** shipped in the image. Both are what CI's `smoke` and `searxng-smoke` jobs run.

### `contract_smoke.py`

```bash
uv run python contract_smoke.py --base-url http://127.0.0.1:8020 [--expect-status {healthy,degraded}] [--anchor <path>] [--timeout-seconds 120] [--poll-interval-seconds 2] [--image <ref>]

# Typical CI shape
docker run -d --name forage-smoke -p 127.0.0.1:8020:8020 forage:ci   # loopback only when run by hand
uv run python contract_smoke.py --base-url http://127.0.0.1:8020 --image forage:ci
```

It polls `/health` until it answers 200 with the expected `status` (default budget `DEFAULT_TIMEOUT_SECONDS = 120`, the same number as CI's `SMOKE_TIMEOUT_SECONDS`), validates the body against `HealthResponse`, and asserts, under the default `--expect-status degraded` (a **weights-free** container):

- `status == "degraded"` and `promptguard_unavailable` in `degraded_reasons`;
- no `search_sanitization` capability;
- `contract_version` equals `pipeline.contract.CONTRACT_VERSION` of the checkout you ran it from;
- `sanitizer_revision` present and not `unknown`;
- `/metrics` answers with the same `contract_version`;
- with `--image`: the in-image `/app/contract/openapi.yaml` `info.version` equals the served `contract_version`, the in-image `openapi.yaml.sha256` equals the `--anchor` file, and the document hashes to it.

Exit 0 prints a `Contract smoke PASSED` line naming the mode it checked (under the default, `Contract smoke PASSED: degraded, honest, and on-contract.`); exit 1 prints one `::error::<violation>` line per failure.

**Two modes.** `--expect-status` takes `healthy` or `degraded` (default `degraded`, what CI runs). Under `healthy` the three PromptGuard-coupled checks invert — `status == "healthy"`, `promptguard_unavailable` absent from `degraded_reasons`, `search_sanitization` present in `capabilities` — and every other check (contract version, sanitizer revision, `/metrics`, in-image contract and anchor) is identical. The wait is status-aware: it polls until `/health` answers 200 *and* the body's `status` equals the expected one, or the deadline passes (returning the last response, which then fails on `status`). `/health` answers 200 the moment uvicorn binds while PromptGuard loads in the background, so under `healthy` it waits through the load rather than failing on the first 200; raise `--timeout-seconds` for a cold weights fetch. Match the flag to the container: `--expect-status degraded` for a container started with no HF token and no weights (CI's weights-free image), `--expect-status healthy` for a container started with weights (e.g. `--env-file` carrying `HF_TOKEN`). `--anchor` defaults to the committed `contract/openapi.yaml.sha256`; to verify a release image from another checkout, pass a file holding the committed anchor at the tag (what `git show v1.1.0:contract/openapi.yaml.sha256` prints, or a clean checkout of it) — never from the Release assets and never from the image: both are mutable copies, and a tampered document-plus-anchor pair verifies against itself.

### `searxng_smoke.py`

```bash
docker build -t forage-searxng:ci searxng/
uv run python searxng_smoke.py --image forage-searxng:ci [--live] [--keep]
```

`--image` is required. Without `--live` it runs five hermetic phases against the companion image on an internal network:

1. `secret` — the container must refuse to start with `SEARXNG_SECRET` unset.
2. `envelope` — `format=json` answers 200 with results.
3. `budget` — more than four JSON requests all answer 200.
4. `limiter-on` — with `SEARXNG_LIMITER=true` and a Valkey backend, it throttles and writes keys.
5. `limiter-inert` — with the limiter on and no backend, the log contains `The limiter requires Valkey` and it still serves.

`--live` runs an advisory live-engine probe instead (continue-on-error in CI); `--keep` leaves the containers and network up for inspection. Exit 0 or 1.

---

## Container-Level Checks

```bash
# Is the process up, and has Docker restarted it?
docker inspect --format '{{json .State}}' <container>      # what CI dumps on smoke failure
docker inspect --format '{{.RestartCount}}' <container>     # Docker's restart counter

# What it said
docker logs <container> 2>&1 | tail -n 100
docker logs -f <container>
```

`compose/minimal.yml` and `compose/full.yml` set `restart: unless-stopped` on the `forage` service, so a boot failure shows up as a climbing restart count and a repeating traceback rather than a stopped container.

**There is no `HEALTHCHECK`.** The `Dockerfile` installs `curl` with a comment saying it is "for the container healthcheck", but declares no `HEALTHCHECK` instruction, and neither compose fragment declares a `healthcheck:` block. The "bare `curl -f`, 10 s × 5 retries" check referenced in code comments is **Poppy's** compose, not this repo's. If you want Docker to track liveness, one suggestion (not shipped, not tested here) is:

```yaml
# Suggestion only — not present in compose/*.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://127.0.0.1:8020/health"]
```

Keep in mind what that proves: `/health` always returns 200, so a `curl -f` probe only says the process is serving. It will never turn a container "unhealthy" for missing weights or a dead Valkey — the body is where the truth lives, and reading the body is your tooling's job.

---

## Alerting, Dashboards, and SLOs

None exist. There are no alert rules, no paging integration, no dashboards, no SLOs, no error budgets, and no uptime or status-page service configured anywhere in this repo, and no thresholds are defined in code or docs. Anything of that kind is built by the operator or the consumer on top of the `/health` body and the `/metrics` JSON, using the "Signals Worth Watching" table above as the starting list of conditions. Do not read a number in this document as a tuned threshold; the only numbers here are measured boot times, coded timeouts and backoffs, and the CI smoke budget.

---

## Adding New Monitoring

### Adding a counter to `/metrics`

1. Add the field to the in-process dataclass — `retrieval_app.ExtractionMetrics`, `SearchMetrics`, `RetrieveMetrics`, `cache.CacheMetrics`, or `model_fetcher.ModelMetrics` — and increment it at the seam.
2. Add the same field to the matching `*MetricsResponse` model in `retrieval_app.py`. The models are `extra="forbid"`; a counter that exists in the dataclass but not in the model makes `/metrics` return 500.
3. A response-shape change is a contract change (`CLAUDE.md` invariant 4). Read `contract/GOVERNANCE.md` to classify it (an additive field is the MINOR case), then run `uv run python -m scripts.export_contract` — `tests/test_contract_export.py` is red until you do. Never hand-edit `contract/openapi.yaml` or its `.sha256`.

### Adding a degraded reason to `/health`

Add the member to `DegradedReason` in `pipeline/contract.py` first. `HealthResponse.degraded_reasons` is validated against that Literal on the way out, so a reason appended in the handler without the Literal change fails response validation (500) instead of reaching a consumer unannounced. It is a contract change; follow the same governance and export steps. This is the path a SearXNG probe would take if the maintainers decide `/health` should report it.

### Adding a log line

Keep the vocabulary closed: fixed reason strings or a `weights_*`-style marker, never an interpolated URL, exception text, or token. Emit at WARNING or above if an operator needs to see it in the container — INFO is invisible there. Add a `caplog` assertion in the module's test that the marker appears and that any secret in the scenario does not. Conventions are in `kit_tools/arch/patterns/LOGGING.md`.
