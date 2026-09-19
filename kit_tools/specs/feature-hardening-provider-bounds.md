<!-- Template Version: 2.5.0 -->
---
feature: hardening-provider-bounds
status: active
session_ready: true
depends_on: [hardening-cache-integrity]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 5
epic_final: false
execution_order: [US-001, US-002, US-004, US-005]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Provider Seam Bounds — Streamed Caps, Wall-Clock Budgets, Policy by Construction

> **Epic 5 of `epic-forage-hardening`.** Close the resource and cost gaps the search epic's
> validation runs found at the provider seam: both providers stream and cap their bodies chunk by
> chunk (decoded bytes, never a whole buffer), every provider call has one wall-clock budget, the free
> provider gets the same outbound query cap the paid one has, the per-request policy is cost-monotonic
> by construction rather than by today's one-paid-provider coincidence, and `run_search_pipeline`'s
> provider loop is refactored once, in one recorded rotation, with its wire output pinned. Planning
> rulings 13, 14 and 15 **as revised in validation round 1 (R13, R14, R15)** are binding; ruling 6
> governs every rotation; ruling 5 governs the two window changes. Source findings:
> `kit_tools/AUDIT_FINDINGS.md` 2026-09-16-012, -020, -021, -044 (bounds), -052, -060 (policy), -054
> (recorded as accepted risk, R14), -035, -036, -038, -039, -043, -045, -011 (orchestrator cleanup).
> Validation round 1 split the old US-002 into US-002 and US-004 and renumbered the cleanup to
> US-005; no story carries the id US-003.

## Overview

The search epic shipped a provider seam whose *contract* is closed — `ProviderSearchResult` or
`ProviderFailure`, closed `failure_class` and `detail` tokens (`pipeline/search_providers/base.py:
21-76`) — but whose *resource* posture still trusts the peer. `SearxngProvider` buffers the whole
response with `client.get` and only then compares `len(resp.content)` to its 1 MiB bound
(`searxng.py:57-62,162-175`); `BraveApiProvider` counts bytes incrementally (`brave.py:331-346`) but
counts them *after* httpx has transparently decompressed them, so a ~200 KB gzip stream once produced
a 67 MB first chunk (finding -020, a transient allocation — the body never accumulated past 1 MiB);
both clients pass a bare float as `timeout=` (`searxng.py:160`, `brave.py:317`), which httpx applies
per socket operation, so a trickling peer never trips it (finding -021); and SearXNG receives the
caller's `query` verbatim while Brave truncates its copy to `search_brave_query_max_chars`
(`brave.py:310-314`), so a URI-length failure at SearXNG can buy a paid call (finding -044).

The load-bearing decision is **R13**: stream both providers, count *decoded* bytes chunk by chunk
against the existing 1 MiB caps with the fast-reject-then-count shape `stage5_url_audit.py:200-219`
already uses for fetches, request `Accept-Encoding: identity` — and **never refuse a compressed body**.
`SEARXNG_URL` is operator-supplied and routinely sits behind a proxy that gzips regardless of the
request header; a refusal there would be a total outage of the key-less free floor (or, on a
`[searxng, brave]` chain, a silent conversion of every free call into a paid one). A compressed body is
therefore decoded incrementally, bounded at the same cap, and *counted* on `/metrics`
(`search.provider_compressed_body`) so an operator can see the proxy setting. The per-provider timeout
becomes a wall-clock budget through one `asyncio.timeout(...)` around the HTTP interaction; the httpx
`timeout=` stays as the inner per-operation guard. SearXNG gains a `SearxngSettings` dataclass mirroring
`BraveSettings` so the budget (and, in US-002, the query cap) has a real seam instead of a module
constant. None of this changes a wire byte of `SearchResponse`; the one contract movement is the
additive `/metrics` counter, inside the open 1.3.0 window (ruling 5).

The second decision is **R14**: `apply_request_policy` keeps only a *prefix* of the configured paid
providers, so cost-monotonicity — "no request can cause a paid call the configured chain would not
already have made" (`policy.py:3-6`) — holds for any chain, not just today's single-paid one (finding
-052). The counter-amplification finding (-054) is **not** fixed by changing the counter's unit:
per-entry counting stays (no contract, no `MONITORING.md` change) and the amplification is recorded as
an accepted risk bounded by request-body size. The prefix rule is stated in `SearchRequest.providers`'
description (a window change).

The third is **R15**: the orchestrator's provider loop is cleaned up in *one* story and *one*
rotation — extracted, deduplicated, its legacy `searxng_url=` parameter retired, its omission logs
stripped of result URLs, and its `provider_errors` composition made to enforce the closed vocabulary
at the point it reaches the wire — with the wire output pinned on committed synthetic fixtures
(compared with `request_id` excluded, the one non-deterministic field) as the proof that nothing else
moved, and the two deliberate deltas stated as their own criteria.

## Goals

- Neither provider holds more than its bound in memory: a body of `max_response_bytes + 1` decoded
  bytes — plain or compressed — returns `ProviderFailure("hard_error", "body_too_large")` on both
  providers with at most one chunk past the bound ever pulled from the stream; a compressed body under
  the bound is served normally and increments `search.provider_compressed_body` by exactly 1.
- Every provider HTTP interaction completes or fails within its wall-clock budget: a fake peer that
  trickles bytes past a 0.5 s budget yields `ProviderFailure("timeout", "timeout")` after more than
  0.5 s and in under 1.0 s, on both providers.
- The outbound SearXNG `q` never exceeds `search_searxng_query_max_chars` (default 400, range 50–400,
  the same as Brave's) characters; `SearchRequest.query` keeps its wire shape.
- For every configured chain and every request, the paid providers the effective chain can call are
  a prefix of the configured paid sequence — proven over every chain shape in
  `tests/test_search_policy.py::_CHAIN_SHAPES` crossed with a later-paid-only selection.
- After the cleanup, `run_search_pipeline` contains no provider loop, and the committed synthetic
  fixtures produce identical `SearchResponse.model_dump()` output (with `request_id` excluded) before
  and after; `pipeline/orchestrator.py` no longer mentions `searxng_url`.

## User Stories

### US-001: Ingress bounds — streamed caps, identity requested, wall-clock budgets

**Priority:** P1

**Description:** As an operator, I want each search provider to bound over-size responses chunk by
chunk and to fail over-time responses at a wall-clock budget, without ever refusing a compressed body,
so a misbehaving or hostile peer degrades into a classified provider failure instead of a memory spike
or a hung `/search` — and a compressing reverse proxy in front of my SearXNG is a counter I can read,
not an outage.

**Independent Test:** Using the shared streaming double promoted into `tests/fakes.py` from
`tests/test_brave_provider.py:715` (`_ChunkStream`, with `_make_response` `:230`, `_make_stream_cm`
`:247`, `_client_patch` `:259`): each provider called directly returns
`ProviderFailure("hard_error", "body_too_large")` for a `max_response_bytes + 1` byte stream with no
`Content-Length` and the double's chunk counter shows at most one chunk past the bound; a stream
carrying `Content-Encoding: gzip` whose *decoded* length is under the bound is served (SearXNG:
results returned; Brave: chunks returned) and the provider's metrics sink shows
`provider_compressed_body == 1`; a stream that yields one byte every 0.2 s against
`SearxngSettings(timeout_seconds=0.5)` / `BraveSettings(timeout_seconds=0.5)` returns
`ProviderFailure("timeout", "timeout")` after more than 0.5 s and in under 1.0 s (`time.perf_counter`).
The SearXNG mocking seam is rewritten as part of this story — `_searxng_client_patch` and
`_mock_searxng_response` in `tests/test_orchestrator.py`, `_SEARXNG_CLIENT` and `_response` in
`tests/test_search_providers.py`, `tests/test_app.py:1828`'s `inner.get.side_effect` — so every
pre-existing *assertion* is preserved while the transport double changes.

**Implementation Hints:**
- `SearxngSettings` first (this story), mirroring `brave.py:193-275` exactly: a frozen dataclass
  `SearxngSettings(timeout_seconds: float = 10.0, max_response_bytes: int = 1_048_576)`, a
  module-owned `SearxngConfigurationError(ValueError)`, a module-local `_bounded_*` copy (the
  re-statement is deliberate — `brave.py:231-233`; provider modules import nothing from the app's
  other configuration surfaces), and `searxng_settings_from_config(config)` reading a new top-level
  `config.yaml` key `search_searxng_timeout_seconds` (float, default `10.0`, range 1–60; the range
  is refused at boot with `SearxngConfigurationError`). US-002 adds `query_max_chars` to the same
  dataclass. `SearxngProvider.__init__` (`searxng.py:146`) gains `settings: SearxngSettings | None =
  None` (default = the dataclass defaults, so every direct construction keeps working). Wire it the
  way Brave is wired: the lifespan calls the builder unconditionally beside
  `brave_settings_from_config` (`retrieval_app.py:1242`), stores `app.state.searxng_settings`, and
  `build_provider_chain` (`pipeline/search_providers/__init__.py:83-89`) gains `searxng_settings=`
  beside `brave_settings=`, used at both construction sites (`:114` registry lambda, `:154` all-skipped
  fallback). `retrieval_app.py:310` (`_resolved_search_providers`, documented production-unreachable)
  and `pipeline/orchestrator.py:857` (removed by US-005) keep default construction. Add a module-level
  `app.state.searxng_settings` default beside `retrieval_app.py:1378-1402` so lifespan-less
  `ASGITransport` tests still have the attribute. Register the key with spec 3's `KNOWN_CONFIG_KEYS`
  (ruling 12) and add its `docs/configuration.md` top-level row (`:430` table) and its
  `kit_tools/docs/ENV_REFERENCE.md` `### Top-level keys` row (`:81-94`).
- `SearxngProvider.search` (`searxng.py:154-205`): replace `client.get(...)` + `len(resp.content)`
  + `resp.json()` (`:162-175`) with `async with client.stream("GET", ...)`, the `Content-Length`
  fast-reject (bound the header string to 20 characters before `int()` — a 5,000-digit
  `Content-Length` passes `isdigit()` and raises inside `int()`; reject it as `body_too_large`), then
  an incremental counter over `aiter_bytes()` (decoded bytes) returning
  `self._failure("hard_error", "body_too_large")` the moment the total exceeds
  `settings.max_response_bytes`, closing the response; parse with `json.loads(body)` the way
  `brave.py:346-349` does (a streamed `httpx.Response` cannot be `.json()`ed). Status mapping
  (`:182-188`, `HTTP_STATUS_DETAIL_PREFIX`, `_RATE_LIMITED_STATUS`) runs before the body is read.
  Rewrite the comment at `:57-59` (it describes the buffer-then-check order this story removes).
- Compression (both providers): send `Accept-Encoding: identity` on the request; **never refuse** a
  response whose `Content-Encoding` is non-identity — keep streaming through `aiter_bytes()` (httpx
  decodes chunk by chunk, so the counter measures decoded bytes and the cap still holds with at most
  one chunk of overshoot) and increment the sink's `provider_compressed_body` once per such response.
  Correct the false clause in the Brave comment at `brave.py:146-150` ("so a compressed body cannot
  expand past it either") to say: decoded bytes are counted chunk by chunk; identity is requested;
  a compressed reply is bounded, counted, and served. `tests/test_brave_provider.py:781-811`
  (`test_compressed_body_whose_decoded_length_exceeds_cap_is_rejected`) stays valid as written —
  the decoded-byte counter is still what rejects it.
- The counter on the wire (`search.provider_compressed_body`, additive, ruling 5): the provider
  needs a sink. Give `SearchProvider.search` no new parameter; instead `ProviderSearchResult` gains
  `compressed: bool = False` (`base.py:57`, internal dataclass, not a wire model) and the
  orchestrator's provider loop increments `metrics.provider_compressed_body` when it is set — five
  sites: `SearchMetricsSink` Protocol (`pipeline/orchestrator.py:747-756`, and its "two counters"
  docstring sentence), `_NullSearchMetrics.__init__` (`:759-771`), `SearchMetrics.__init__`
  (`retrieval_app.py:875-886`), the `/metrics` handler dict (`:1517-1524`, appended after
  `policy_unknown_provider`), and `SearchMetricsResponse` (`:488-533`, appended last, description
  naming the header and the proxy cause). `extra="forbid"` on the metrics models means the model and
  the class move in the same commit (`GOTCHAS.md` "Adding a /metrics counter"). The order guards are
  `tests/test_contract_metrics.py:135` and `:161`; that module hosts the new counter's tests. This
  moves `contract/openapi.yaml` (`/metrics` has a `response_model`): append the line to the 1.3.0
  `CONTRACT_VERSION` docstring entry in the `* ``1.3.0`` — …` bullet format (`pipeline/contract.py:
  26-68`; R34), regenerate with `uv run python -m scripts.export_contract`; `tests/golden/
  contract_1_3_0.json` is **unchanged** by this story — `/metrics` models are not in `_SCHEMA_MODELS`
  (`tests/test_contract_schema.py:28-35`) and are pinned by `tests/test_contract_metrics.py` instead.
  `pipeline/contract.py` is hashed: this is the story's rotation (ruling 6). Register the counter in
  spec 8 US-002's 1.3.0 coverage sweep when it exists (`tests/test_contract_schema.py:99,189-236` is
  the 1.2.0 precedent).
- Wall-clock budget: wrap **only the HTTP interaction** — from opening the stream to the last body
  chunk — in `async with asyncio.timeout(settings.timeout_seconds)` on both providers (`json.loads`
  and result building sit outside it; `asyncio.timeout` can only fire at an `await`, so including a
  synchronous parse would discard a good result). Map `TimeoutError` to
  `self._failure("timeout", "timeout")` in an arm placed before the `except Exception` catch-all
  (search epic ruling 27) so the class stays `timeout`. Keep the httpx `timeout=` (per-operation).
- Test seam (the migration is this story's): promote `_ChunkStream`, `_make_response`,
  `_make_stream_cm` and `_client_patch` from `tests/test_brave_provider.py:230-259,715-723` into
  `tests/fakes.py` (the shared-double home: `FakeStorage` `:196`, `FakeSearchProvider` `:327`) and
  make `tests/test_stage5_url_audit.py:43-84` and `tests/test_brave_provider.py` import them; then
  rewrite `tests/test_orchestrator.py:881-905` (`_mock_searxng_response`, `_searxng_client_patch`) to
  return a stream double whose body is `json.dumps(data).encode()`, migrate the direct
  `pipeline.search_providers.searxng.httpx.AsyncClient` patches at `tests/test_orchestrator.py:2417`,
  `:2439` and the `.get.call_count` assertion at `:2505`, `tests/test_search_providers.py:272-300`
  (`_SEARXNG_CLIENT`, `_response` — its docstring "the body bound is read off len() before json()
  runs" is rewritten) and the `client.get.call_args` assertions at `:378,:388` (now
  `client.stream.call_args`), and `tests/test_app.py:1828-1833`. Never introduce
  `httpx.MockTransport` (it appears nowhere in the suite and neither provider takes an injectable
  transport). The chunk-count assertion belongs on the double, not on a handler.
- Timed tests: the suite's only real sleeps are 0.05 s (`tests/test_app.py:978,1007,1123`) and
  `tests/fakes.py:174` `ManualClock` exists to avoid them; this story knowingly adds two ~0.6 s
  timed tests (one per provider) with the generous one-sided bar in the Goals, and names that
  departure in the test module docstring.
- Docs: `docs/configuration.md:439` (`search_brave_timeout_seconds`) becomes "wall-clock budget for
  one Brave call — connect, headers and body together; a request on an N-provider chain can take the
  sum of the configured budgets"; the new `search_searxng_timeout_seconds` row says the same for
  SearXNG; `kit_tools/arch/patterns/ERROR_HANDLING.md:245` (Brave timeout row) and `:246` (chain
  traversal row — it becomes *true* here) and `kit_tools/docs/TROUBLESHOOTING.md:178` ("10 s timeout")
  say wall-clock; `kit_tools/docs/MONITORING.md` gains a `search.provider_compressed_body` row: "a
  steady non-zero count means a compressing proxy in front of the provider; the body was bounded and
  served".
- `pipeline/search_providers/*` is outside `_REVISION_SOURCES`; the only hashed edit is the
  `contract.py` docstring line.

**Acceptance Criteria:**
- [ ] `SearxngSettings`, `SearxngConfigurationError` and `searxng_settings_from_config` exist in
      `pipeline/search_providers/searxng.py`; `search_searxng_timeout_seconds` (default `10.0`,
      range 1–60) refuses boot out of range with `SearxngConfigurationError`; `build_provider_chain`
      takes `searxng_settings=` and both of its construction sites use it; the lifespan reads the
      builder unconditionally and `app.state.searxng_settings` has a module-level default.
- [ ] `SearxngProvider` reads its response via `client.stream` with a `Content-Length` fast-reject
      (header string bounded to 20 characters) and an incremental decoded-byte counter; a streamed
      body of `max_response_bytes + 1` bytes returns `ProviderFailure("hard_error",
      "body_too_large")` with at most one chunk past the bound pulled; a 5,000-digit `Content-Length`
      classifies as `body_too_large`, pinned.
- [ ] Both providers send `Accept-Encoding: identity`; a `Content-Encoding: gzip` response under the
      bound is served and sets `ProviderSearchResult.compressed`; the orchestrator increments
      `search.provider_compressed_body` once per such response; a compressed response over the bound
      is `body_too_large`; pinned on both providers.
- [ ] Both providers return `ProviderFailure("timeout", "timeout")` for a trickling body: more than
      the budget and under twice the budget elapsed (`time.perf_counter`), with the budget scope
      covering the HTTP interaction only (a test proves a slow *parse* after a fast body is not a
      timeout).
- [ ] `search.provider_compressed_body` is on `/metrics` (`SearchMetricsSink`, `_NullSearchMetrics`,
      `SearchMetrics`, the handler dict, `SearchMetricsResponse`); the two order guards in
      `tests/test_contract_metrics.py` pass with the key appended.
- [ ] Contract regenerated inside the 1.3.0 window: docstring line appended in the `* ``1.3.0`` — …`
      format, `contract/openapi.yaml` + `.sha256` regenerated, `uv run python -m
      scripts.export_contract --check` clean; `tests/golden/contract_1_3_0.json` and
      `contract_1_2_0.json` byte-identical to before (the `/metrics` models are not golden-pinned —
      state that in Implementation Notes).
- [ ] `FAILURE_CLASSES` and both providers' detail sets are byte-identical to their pre-story
      contents (`git diff pipeline/search_providers/base.py` shows no member added; the encoding path
      adds no token; the budget reuses `timeout`).
- [ ] The shared streaming double lives in `tests/fakes.py`; `tests/test_stage5_url_audit.py` and
      `tests/test_brave_provider.py` import it; `grep -rn MockTransport tests/` returns nothing; every
      pre-existing SearXNG assertion in `tests/test_orchestrator.py`, `tests/test_search_providers.py`
      and `tests/test_app.py` is preserved with only the transport double changed (the diff of those
      modules touches helper bodies and `client.get`→`client.stream` argument lookups only).
- [ ] The doc rows named in the hints say "wall-clock budget" (`grep -c 'wall-clock'
      docs/configuration.md` ≥ 2) and state the N-provider sum; `MONITORING.md` documents the counter;
      the new config key has `docs/configuration.md` and `ENV_REFERENCE.md` rows and is in
      `KNOWN_CONFIG_KEYS`.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce on `pipeline/contract.py`) and
      recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Outbound SearXNG query cap — `search_searxng_query_max_chars`

**Priority:** P1

**Description:** As an operator, I want the free provider's outbound query capped exactly like the
paid one, so a caller cannot buy a paid call by making SearXNG fail on request-URI length.

**Independent Test:** Following `tests/test_brave_provider.py:833-857`
(`test_a_five_thousand_character_query_is_truncated_to_the_query_cap`): a 5,000-character query
through `run_search_pipeline` on a `[searxng]` chain built by `build_provider_chain` (not a directly
constructed provider) reaches the transport as `client.stream.call_args.kwargs["params"]["q"]` of
exactly `search_searxng_query_max_chars` characters, the request is accepted end to end, and
`SearchResponse.query` echoes the original; a 400-character CJK query is sent whole (400 characters,
≈ 3.6 KB percent-encoded — under the 8 KB request-line limit the cap is sized against).

**Implementation Hints:**
- `SearxngSettings` (from US-001) gains `query_max_chars: int = 400`; `searxng_settings_from_config`
  reads `search_searxng_query_max_chars` with range **50–400** — the same `_MIN_/_MAX_BRAVE_QUERY_MAX_
  CHARS` bounds Brave uses (`brave.py:189-190`), refused at boot with `SearxngConfigurationError`.
  Rationale for 400 on the free floor: 400 characters of 4-byte UTF-8 percent-encode to at most
  4,800 bytes, under the common 8 KB request-line limit, so the cap is a guardrail, not routine
  truncation; the alternative (a byte cap on the encoded `q`) is rejected as harder to document.
- Truncate the outbound copy only, `outbound_query = query[: self.settings.query_max_chars]` beside
  the existing `params=` at `searxng.py:164` (the `brave.py:310-314` pattern); `SearchRequest.query`
  keeps its wire shape (search epic ruling 30) and `SearchResponse.query` still echoes the caller's
  string — the caller sees no flag; say so in the `docs/configuration.md` row ("results reflect the
  first N characters; the echoed `query` is the caller's").
- Wiring is already through `build_provider_chain(searxng_settings=...)` (US-001); this story only
  adds the field, the key, the `KNOWN_CONFIG_KEYS` entry (ruling 12), the `docs/configuration.md`
  top-level row and the `ENV_REFERENCE.md` `### Top-level keys` row.
- `kit_tools/arch/SECURITY.md:352` (operator-spend row): add the sentence that a URI-length failure at
  SearXNG can no longer buy a paid call (this story), beside the already-closed paths
  (post-sanitization omission — search epic ruling 17; unknown policy names — ignored, never a 422).
- No hashed file, no contract movement (`pipeline/search_providers/*` and `retrieval_app.py` are not
  in `_REVISION_SOURCES`).

**Acceptance Criteria:**
- [ ] `search_searxng_query_max_chars` exists in `config.yaml` (default 400), is bounded 50–400 at
      boot (`SearxngConfigurationError` out of range), has `docs/configuration.md` and
      `ENV_REFERENCE.md` rows and a `KNOWN_CONFIG_KEYS` entry.
- [ ] The outbound SearXNG `q` is truncated to the cap on the chain `build_provider_chain` builds
      (asserted on `client.stream.call_args`); `SearchRequest.query` is unchanged on the wire;
      a query shorter than the cap is sent untouched; a 400-character multi-byte query is sent whole.
- [ ] `SECURITY.md`'s operator-spend row names the SearXNG URI-length path as closed by this story.
- [ ] `git diff --stat` shows no change under `pipeline/orchestrator.py`, `pipeline/contract.py`,
      `models.py` or `contract/`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: Cost-monotonic policy by construction — the paid-prefix rule

**Priority:** P1

**Description:** As an operator, I want the per-request policy unable to promote a later configured
paid provider over an earlier one, so cost-monotonicity holds when a second paid backend is
configured, not just today when there is one.

**Independent Test:** With `tests/test_search_policy.py:44` `_REQUEST_PROVIDER_SELECTIONS` extended
by a later-paid-only selection (the configured paid names minus the first), the existing
`test_effective_chain_is_a_bounded_subset_of_the_configured_chain` loop (`:53`) over every
`_CHAIN_SHAPES` entry and both `allow_paid_fallback` values additionally asserts the effective chain's
paid providers are a prefix of the configured paid sequence; the named
`test_a_second_paid_provider_cannot_be_reached_by_skipping_the_first` shows `providers: ["paidb"]` on
`[free, paidA, paidB]` yields `[free]`, never `[free, paidB]`; every existing case passes unchanged.

**Implementation Hints:**
- `apply_request_policy` (`pipeline/search_providers/policy.py:35-77`): rewrite step 3 so the kept
  paid providers are the longest prefix of the configured paid sequence whose every member is in the
  named set (a named paid provider after an un-named one is dropped, never promoted); keep steps 1,
  2 and 4 as they are. Write the four steps as four statements each preceded by a `# step N —`
  comment whose text matches the docstring's numbered step (finding -060); a test asserts the
  docstring enumerates exactly the `# step N` markers present in the body (`inspect.getsource`), so
  the mirror is checkable, not a review preference. Update the docstring's algorithm and its
  "output is always *chain* minus a subset of its paid providers" sentence to state the prefix rule.
- Observability of a prefix drop: **none by design** (Decisions Made). The drop is the documented
  meaning of `providers` — restrict-only, order-preserving, and now "a request may omit paid
  providers from the end of the configured paid sequence, never select a later one while skipping an
  earlier one". That sentence lands in `SearchRequest.providers`' description (`models.py:302-315`),
  which moves `contract/openapi.yaml` and the golden (`SearchRequest` is in `_SCHEMA_MODELS`): append
  the line to the 1.3.0 `CONTRACT_VERSION` docstring entry (R34 format), regenerate, re-create
  `tests/golden/contract_1_3_0.json` (ruling 5). `pipeline/contract.py` is hashed: measure and record
  the rotation (ruling 6). `kit_tools/docs/API_GUIDE.md`'s `providers` sentence says the same.
- `policy_unknown_provider` keeps per-entry counting (R14): `retrieval_app.py:1799` and
  `MONITORING.md:150` are **not** edited. Record finding -054 as an accepted risk: a new row in
  `kit_tools/arch/SECURITY.md`'s non-vulnerabilities table — "`policy_unknown_provider` grows per
  ignored entry; a large `providers` body moves it proportionally; the amplification is bounded by
  the request body a caller can send and the counter is a misconfiguration signal, not a rate
  limiter; `/search` carries no body cap (the size middlewares are `/extract`-only,
  `retrieval_app.py:1043,1088`) — accepted, network placement is the control."
- Tests: `tests/test_search_policy.py` only, plus the contract regeneration tests. `hypothesis` is not
  a dependency (`pyproject.toml:32-39`); the existing enumeration machinery (`_CHAIN_SHAPES` `:34`,
  `_chain` `:28`, `_is_subsequence` `:22`) is the property harness.

**Acceptance Criteria:**
- [ ] `apply_request_policy` keeps only a prefix of the configured paid providers; the extended
      property loop and `test_a_second_paid_provider_cannot_be_reached_by_skipping_the_first` pass;
      every existing `tests/test_search_policy.py` case passes unchanged.
- [ ] The four docstring steps correspond to four `# step N —` marked statements, and a test asserts
      the docstring's numbered steps and the body's markers agree.
- [ ] `SearchRequest.providers`' description states the prefix rule; `API_GUIDE.md` matches.
- [ ] Contract regenerated inside the 1.3.0 window: docstring line appended, `contract/openapi.yaml`
      + `.sha256` regenerated, `tests/golden/contract_1_3_0.json` re-created, `uv run python -m
      scripts.export_contract --check` clean; `contract_1_2_0.json` untouched.
- [ ] `policy_unknown_provider`'s unit is unchanged (`retrieval_app.py`'s increment site and
      `MONITORING.md`'s row unedited); `SECURITY.md` carries the accepted-risk row for finding -054.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce on `pipeline/contract.py`) and
      recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-005: The `run_search_pipeline` cleanup — one rotation, wire output pinned

**Priority:** P2

**Description:** As a maintainer, I want the provider loop in `run_search_pipeline` extracted,
deduplicated and stripped of its legacy parameter and of result-URL logging, in a single recorded
rotation, so the next hardening change to search lands in a function a reviewer can hold in their
head — with the wire output pinned so the two deliberate deltas are the only ones.

**Independent Test:** Before the change, capture `SearchResponse.model_dump()` (with `request_id`
removed — the one non-deterministic field, `pipeline/orchestrator.py:848`) for three representative
runs built from the in-repo synthetic fixtures and the stream double (a clean SearXNG success, a
fallback to Brave with `provider_errors`, and a run with omissions under every `omitted_by_reason`
token — never from a live provider); commit them under `tests/fixtures/search/`; after the change the
same inputs produce identical dumps (`tests/test_search_pipeline_pins.py`), every pre-existing search
test passes with only the `searxng_url=` → `providers=` migration in the diff, and an omitted result's
URL appears in no log record.

**Implementation Hints:**
- Extract the loop at `pipeline/orchestrator.py:874-946` into a module-level
  `async def _query_provider_chain(...) -> _ServedChain` (a small dataclass: serving provider, raw
  results, `provider_errors`, `fallback_fired`, `paid_calls` delta, `compressed` count) so
  `run_search_pipeline` reads as query → sanitize → respond. One
  `_log_provider_failure(provider, failure_class, detail)` replaces the two WARNING statements at
  `:907-912` and `:925-930` (finding -035), with `_UNRESPONSIVE_ENGINES_DETAIL =
  "unresponsive_engines"` beside it.
- `_legacy_searxng_codes(configured_chain)` (`:670`) is evaluated once before the loop and reused at
  both raise sites (`:906`, `:946`; finding -038); the two identical `ValueError("...empty provider
  chain...")` sites (`:842-846`, `:941-945`) collapse to the upfront guard; the comment at `:903` says
  "configured chain of exactly one `searxng` (search epic ruling 28)" (finding -039).
- Retire `searxng_url=` from `run_search_pipeline` only (`:777` signature, `:817-824` docstring,
  `:856-858` branch). `build_provider_chain(*, searxng_url=)` (`pipeline/search_providers/
  __init__.py:86`) is **deliberately untouched** — it is the production factory's keyword. Migrate
  every `run_search_pipeline(..., searxng_url=...)` call site: `grep -c 'searxng_url=' tests/
  test_orchestrator.py` is the authority (19 at planning time — do not trust the line numbers of an
  earlier draft), plus the multi-line calls in `tests/test_search_providers.py` (find them with
  `grep -n -B6 'searxng_url=' tests/test_search_providers.py | grep run_search_pipeline`; the two
  tests that exist only to exercise the legacy parameter —
  `test_a_malformed_searxng_url_still_yields_a_422` and
  `test_a_supplied_chain_makes_searxng_url_unused` — are removed, since malformed-URL coverage
  already lives provider-side in `_compute_origin`, `searxng.py:93-125`). Migrate to
  `providers=[SearxngProvider(url)]` through the stream-capable `_searxng_client_patch` US-001 left
  behind (US-001 rewrites the helper; this story migrates the call sites — the ordering is on paper).
- Omission INFO lines (`:999-1003`, `:1023-1027`, `:1031-1035`) log the closed reason token and the
  validated `domain` (`SearchResult.domain`, lower-cased hostname), never the result URL (finding
  -043: on a fallback the URL comes from the Brave body, and telemetry is metadata only — search epic
  owner decision 6). Extend `tests/test_orchestrator.py::
  test_provider_failures_leak_no_url_credential_or_exception_text` (grep the name; the orchestrator's
  no-leak family) with the per-record `getMessage()` sweep pattern of
  `tests/test_search_providers.py:676-703`, so an omitted result's sentinel URL is asserted absent
  from every record.
- `provider_errors` composition (`:924`): map a `failure_class` not in `FAILURE_CLASSES` to
  `hard_error`, a `detail` not matching `^[a-z0-9_]{1,32}$` to `unexpected`, **and** a
  `provider.name` not matching the same pattern to `unknown` before the entry is built (finding
  -045; the name half is hardening for a future operator-pluggable provider — say so in
  `_query_provider_chain`'s docstring); a test registers a fake returning
  `ProviderFailure("weird", "x y")` and asserts the entry `"<name>: hard_error"` and the log token
  `unexpected`.
- Wire pins: the three captures are built from `tests/fixtures/brave/llm_context_sample.json` and
  the synthetic SearXNG dicts the tests already use — never a live capture (Brave's ToS forbids
  persisting result payloads). Add a `tests/fixtures/README.md` section for `search/` (provenance
  + the guard: the existing fixture-tree token walk covers it) and a
  `kit_tools/testing/TESTING_GUIDE.md:153-156` row. The comparison lives in a new
  `tests/test_search_pipeline_pins.py`; the closed exclusion list is `{"request_id"}` — the
  implementer sweeps `SearchResponse` for any other non-deterministic field before the capture and
  records the sweep result in Implementation Notes. Captures are taken in the story's **first**
  commit, before the refactor.
- `pipeline/orchestrator.py` is hashed: this is the one rotation the whole cleanup gets; the
  `provider_errors` and omission-log deltas are deliberately inside it. Record per ruling 6.

**Acceptance Criteria:**
- [ ] `_query_provider_chain` exists at module level in `pipeline/orchestrator.py`;
      `run_search_pipeline` contains no `for provider in` loop; exactly one `search_provider_failed`
      WARNING statement remains; `_legacy_searxng_codes` is called once per request; one empty-chain
      `ValueError` site remains.
- [ ] `grep -n 'searxng_url' pipeline/orchestrator.py` returns nothing; no `run_search_pipeline(`
      call in `tests/` passes `searxng_url=`; `build_provider_chain`'s `searxng_url=` keyword and its
      callers are unchanged.
- [ ] No omission log line contains a result URL: the extended no-leak test passes; the lines carry
      the reason token and `domain=`. **This is a deliberate delta**, pinned by that test.
- [ ] A `ProviderFailure` with an out-of-vocabulary class, detail or provider name reaches the wire
      as `"<name>: hard_error"` / `"unknown: …"` with `detail=unexpected` in the log, pinned by a
      test. **This is a deliberate delta** for inputs the pins do not contain.
- [ ] Wire output pinned: for inputs whose provider outcomes are in-vocabulary, the three committed
      captures produce identical `model_dump()` (exclusion list `{"request_id"}`) before and after;
      `tests/test_search_pipeline_pins.py` stays in the suite; `tests/fixtures/README.md` and
      `TESTING_GUIDE.md` document the directory; all pre-existing search tests in
      `tests/test_orchestrator.py`, `tests/test_search_providers.py` and `tests/test_app.py` pass with
      no assertion changed apart from the two removed legacy-parameter tests.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce) and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- A response with `Content-Length` under the bound whose body then exceeds it is still capped by the
  running counter (US-001).
- A `Content-Length` that is not a digit string is ignored and the counter decides; one longer than
  20 characters is rejected as `body_too_large` before `int()` (US-001).
- `Content-Encoding: identity` present or absent: not counted; `gzip`, `br`, `gzip, identity`:
  served, bounded on decoded bytes, counted once (US-001).
- A compressed body whose decoded length exceeds the bound is `body_too_large`, not a compression
  failure (US-001).
- The wall-clock budget expiring after headers but before the first body byte maps to `timeout`, not
  `body_too_large`; a slow `json.loads` after a fast body is never a timeout (US-001).
- A query shorter than the cap is sent untouched; a cap equal to the query length sends it whole;
  truncation counts characters, not bytes; the echoed `SearchResponse.query` is the caller's (US-002).
- `providers` naming a paid provider that is configured *first* keeps it; naming only a later paid
  provider drops every paid provider — silently by design, as the field's description now states
  (US-004).
- A request whose ignored entries are all duplicates of one unknown name counts each entry
  (unchanged, R14) (US-004).
- The cleanup keeps the search epic's ruling 28 predicate on the *configured* chain — a
  policy-filtered `[searxng]` effective chain with a two-provider configured chain still raises
  `search_unavailable`, never `searxng_*` (US-005; pinned by `tests/test_app.py`'s
  `test_exhaustion_code_follows_the_configured_not_the_effective_chain`).
- An omitted result with an unparseable host has no `domain`; the log line carries the reason token
  only (US-005).

## Out of Scope

- A spend ceiling or budget breaker (search epic ruling 12 stands; Poppy's P4 is the only cap).
- A body-size cap on `/search` or `/retrieve` request bodies, or a `max_length` on
  `SearchRequest.providers` (recorded as accepted risk in US-004; a rejection change belongs to a
  GOVERNANCE ruling of its own).
- Changing `policy_unknown_provider`'s unit (R14).
- New providers, LiteLLM adapters, or any change to the `SearchProvider` protocol's members.
- Changing the 1 MiB bounds' defaults or exposing them as knobs beyond `SearxngSettings.
  max_response_bytes` (no config key for it; the field exists for tests and parity with Brave).
- A request-level (chain-wide) deadline — the budget is per provider call; the N-provider sum is
  documented.
- Structural or PromptGuard behaviour on search text — spec 1; `SearchResult.engine` bounding — spec 1
  US-004.

## Assumptions

- Poppy is the only consumer; both window changes (the counter, the `providers` description line) are
  additive and announced in the 1.3.0 docstring entry.
- `asyncio.timeout` is stdlib (Python ≥ 3.11 per `pyproject.toml`); no new dependency.
- httpx's `aiter_bytes()` decodes `gzip`/`br`/`deflate` chunk by chunk, so a decoded-byte counter
  bounds accumulation to the cap plus one chunk even for a compressed body (the same property Brave's
  existing counter relies on — `tests/test_brave_provider.py:781-811`).
- The 1.3.0 contract window is open when this spec executes (spec 1 US-004 opened it) and spec 3
  US-003's `KNOWN_CONFIG_KEYS` registry exists.
- The three wire pins are representative enough; they are kept as regression fixtures for later
  rotations of `orchestrator.py`.

## Technical Considerations

- Rotation ledger (ruling 6, R32): US-001 rotates through `pipeline/contract.py` (the counter's
  docstring line); US-002 rotates nothing; US-004 rotates through `pipeline/contract.py` (the
  `providers` description line); US-005 rotates through `pipeline/orchestrator.py`. Three rotations,
  each measured and recorded.
- Golden movement: US-001 moves `contract/openapi.yaml` and the anchor but **not** the golden
  (`/metrics` models are outside `_SCHEMA_MODELS`); US-004 moves the golden (`SearchRequest`).
- Settings precedent: `brave.py:193-275` (`BraveConfigurationError`, frozen `BraveSettings`,
  module-local `_bounded_int`, `brave_settings_from_config`) — `cache.py:244-267` and
  `pipeline/extraction_limits.py:79-106` are the other two copies; the re-statement is a recorded
  decision, not an accident.
- `asyncio.timeout` nests inside the `httpx.AsyncClient` context so the client closes on expiry; the
  `except TimeoutError` arm precedes the catch-all (search epic ruling 27).
- The `/metrics` order guards are `tests/test_contract_metrics.py:135` and `:161`; `SearchMetrics`
  (`retrieval_app.py:875-886`) is a plain class, not a dataclass, so
  `test_dataclass_counters_and_their_models_carry_the_same_fields` (`tests/test_contract_metrics.py:
  305`) needs no new parametrisation.
- The timed tests are the suite's first real waits above 0.05 s; they are one-sided (more than the
  budget, under twice it) so a contended runner cannot flake them on the tight side.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md) (search pipeline section)
- Security: [SECURITY.md](../arch/SECURITY.md) (cost section, non-vulnerabilities table)
- Patterns: [ERROR_HANDLING.md](../arch/patterns/ERROR_HANDLING.md), [LOGGING.md](../arch/patterns/LOGGING.md)
- Configuration: `docs/configuration.md` (`search_*` rows), [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md)
- Monitoring: [MONITORING.md](../docs/MONITORING.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("Nothing configures logging", "/metrics counter without the model 500s")
- Rotations: `docs/bootstrap-notes.md`, [DECISIONS.md](../arch/DECISIONS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Mirror `stage5_url_audit.py`'s fast-reject-then-count shape in `SearxngProvider` and
count decoded bytes on both providers.
**Rationale:** The shape already exists and is tested for fetches (`stage5_url_audit.py:200-219`);
Brave uses it (`brave.py:331-346`). One idiom, three call sites; the existing streaming double
(`tests/test_brave_provider.py:715`) is the test harness.
**Alternatives considered:** `httpx`'s `max_content_length` — not a client option; a per-provider
config bound — no operator has asked for one.
**Source:** `pipeline/search_providers/searxng.py:57-62,162-175`; `brave.py:146-150,331-346`.

**Decision:** Request identity encoding, never refuse a compressed reply; count it (R13).
**Rationale:** `SEARXNG_URL` is operator-supplied and commonly proxied; a refusal is a free-floor
outage or a silent free→paid conversion. The decoded-byte counter already bounds a compressed body
(finding -020 says so: "the body still never accumulates past 1 MiB"); the transient allocation was
the bug, and per-chunk counting is the fix.
**Alternatives considered:** Refuse non-identity (round-0 plan) — rejected in validation round 1 by
three reviewers for the outage/cost path; opt-in refusal behind a key — surface for no user.
**Source:** finding 2026-09-16-020; `tests/test_brave_provider.py:781-811`.

**Decision:** `SearxngSettings` mirroring `BraveSettings`, threaded through `build_provider_chain`.
**Rationale:** The timeout needs a per-instance seam for a 0.5 s test; the query cap needs a boot
refusal with a module-owned error class; the production chain is built by `build_provider_chain`
(`retrieval_app.py:1258`), not by the production-unreachable fallback at `:310`.
**Alternatives considered:** bare constructor arguments — the only provider without a validated
settings object; monkeypatching the module constant — untestable in production wiring.
**Source:** `brave.py:193-275`; `pipeline/search_providers/__init__.py:83-89,114,154`.

**Decision:** Prefix rule for paid providers (R14) rather than scoping the docstring to "one paid
provider".
**Rationale:** The guarantee should hold when T3.1 adds a second paid backend; scoping it would make
that epic re-open policy. The existing enumeration harness (`_CHAIN_SHAPES`) is the property test.
**Alternatives considered:** Docstring scoping plus a tripwire test — rejected as deferring the fix.
**Source:** `policy.py:35-77`; `tests/test_search_policy.py:22-53`; finding 2026-09-16-052.

**Decision:** One cleanup story, one rotation, with committed wire pins compared modulo `request_id`.
**Rationale:** Seven findings touch the same 70 lines; each on its own would rotate the revision and
need its own record (ruling 6). `request_id = uuid.uuid4().hex` (`orchestrator.py:848`) is the only
non-deterministic field, so the exclusion list is closed and written down.
**Source:** findings -035, -036, -038, -039, -043, -045, -011; `orchestrator.py:842-946, 999-1035`.

### Scope Adjustments

- Finding -014 (`engine` bound) moved to spec 1 US-004 because it is wire-adjacent and belongs with
  the contract window opening.
- Validation round 1: the old US-002 (egress bound + policy + counter unit) split into US-002
  (egress bound) and US-004 (policy); the counter-unit change was dropped (R14); the cleanup was
  renumbered US-005. No story carries the id US-003.
- Validation round 1: the "refuse non-identity encoding" design replaced by "serve, bound, count"
  (R13); `SearxngSettings` added; the SearXNG test-seam migration made explicit in US-001.

### Decisions Made

- No new failure tokens: `body_too_large` and `timeout` cover every new refusal; compression is
  never a failure.
- A prefix drop is not signalled (no counter, no log): it is the documented semantics of
  `providers`, stated on the wire description; recorded here so the next reviewer sees it was chosen.
- Overruled (validation round 1, per R14): the proposals to cap `policy_unknown_provider` at one per
  request, to add a `/search` body cap, or a `max_length` on `providers` — the first hides evidence of
  an abuse it does not bound, the other two are rejection changes that need their own GOVERNANCE
  ruling; the amplification is recorded as accepted risk instead.
- Overruled: "promote the Brave `Accept-Encoding` question to blocking" — moot, since a compressed
  reply is now served rather than refused.
- Overruled: a `--notes`-style line-count bar on `run_search_pipeline` — replaced by structural
  criteria (no loop in the function; helper at module level).
- The `provider.name` guard at composition is added (security review) even though every name is a
  registry constant today; the docstring says why.

## Clarifications

### Session 2026-09-19
- Q: Cap provider bodies before or after decode? → A: Count decoded bytes chunk by chunk on both
  providers; identity is requested, never enforced (ruling 13 as revised).
- Q: Is the per-provider timeout a per-operation or a wall-clock budget? → A: Wall-clock, via
  `asyncio.timeout` around the HTTP interaction only; the httpx per-operation timeout stays.
- Q: Cost-monotonicity with a second paid provider — restrict by construction or by docstring? → A:
  By construction, paid-prefix rule (ruling 14).
- Q: How many increments may one request add to `policy_unknown_provider`? → A: One per ignored
  entry, as today (R14); the amplification is an accepted, recorded risk.
- Q: How many rotations does the orchestrator cleanup get? → A: One story, one rotation (ruling 15).

### Session 2026-09-19 (validation round 1)
- Rulings applied: R13 (serve-bound-count for compressed bodies; `SearxngSettings`; wall-clock scope
  = HTTP interaction; test-seam migration owned by US-001; no `MockTransport`), R14 (per-entry
  counting unchanged; -054 accepted risk), R15 (`model_dump()` minus `request_id`; `searxng_url`
  scoped to `orchestrator.py`; structural criteria instead of line counts), R31 (split and
  renumbering; `execution_order`), R32 (rotation ledger names US-001 and US-004 as `contract.py`
  rotations), R34 (docstring bullet format), ruling 5 (window criteria on US-001 and US-004).
- Q: Which story owns `SearxngSettings`? → A: US-001 creates it (timeout, response bound); US-002
  adds `query_max_chars`.
- Q: Does US-001 move the contract? → A: Yes — the `/metrics` counter; the golden does not move.

## Open Questions

- [ ] Should `SearxngSettings.max_response_bytes` get a `config.yaml` key in a later epic? Non-blocking;
      default no until an operator hits the bound (spec 6's Out of Scope agrees).
- [ ] Whether Brave's LLM-Context endpoint ever answers with a non-identity encoding — informational
      now (a compressed reply is served and counted); confirm at the next owner-run capture.
