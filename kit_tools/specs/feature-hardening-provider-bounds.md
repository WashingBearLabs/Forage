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
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Provider Seam Bounds — Streamed Caps, Wall-Clock Budgets, Policy by Construction

> **Epic 5 of `epic-forage-hardening`.** Close the resource and cost gaps the search epic's
> validation runs found at the provider seam: bodies are capped *before* decode on both providers,
> every provider call has one wall-clock budget, the free provider gets the same outbound query cap
> the paid one has, the per-request policy is cost-monotonic by construction rather than by today's
> one-paid-provider coincidence, and `run_search_pipeline`'s provider loop is refactored once, in one
> recorded rotation. Planning rulings 13, 14 and 15 are binding; ruling 6 governs the rotation story.
> Source findings: `AUDIT_FINDINGS.md` 2026-09-16-012, -020, -021, -044 (bounds), -052, -054, -060
> (policy), -035, -036, -038, -039, -043, -045, -011 (orchestrator cleanup).

## Overview

The search epic shipped a provider seam whose *contract* is closed — `ProviderSearchResult` or
`ProviderFailure`, closed `failure_class` and `detail` tokens (`pipeline/search_providers/base.py:
25-76`) — but whose *resource* posture still trusts the peer. `SearxngProvider` buffers the whole
response and only then compares `len(resp.content)` to its 1 MiB bound (`searxng.py:57-60,172`);
`BraveApiProvider` counts bytes incrementally (`brave.py:339-346`) but counts them *after* httpx has
transparently decompressed them, so a ~200 KB gzip stream once produced a 67 MB first chunk (finding
-020); both clients pass a bare float as `timeout=` (`searxng.py:160`, `brave.py:317`), which httpx
applies per socket operation, so a trickling peer never trips it (finding -021); and SearXNG receives
the caller's `query` verbatim while Brave truncates its copy to `search_brave_query_max_chars`
(`brave.py:314`), so a URI-length failure at SearXNG can buy a paid call (finding -044).

The load-bearing decision is **ruling 13**: bound bytes before they are decoded, on both providers,
with the shape `stage5_url_audit.py:200-219` already uses for fetches — fast-reject on
`Content-Length`, then an incremental counter over `aiter_bytes()` — plus `Accept-Encoding: identity`
so the counter measures wire bytes, and one `asyncio.timeout(...)` around the whole call so the
per-provider timeout is a budget, not a per-read allowance. None of this changes a wire byte: an
over-size or over-time provider is still a `ProviderFailure` with today's tokens.

The second decision is **ruling 14**: `apply_request_policy` keeps only a *prefix* of the configured
paid providers, so cost-monotonicity — "no request can cause a paid call the configured chain would
not already have made" (`policy.py:3-6`) — holds for any chain, not just today's single-paid one
(finding -052). The same story caps `policy_unknown_provider` at one increment per request (finding
-054: one 800 KB body moved the counter by 200,000) and lands the documented-unit change inside the
open 1.3.0 window (ruling 5).

The third is **ruling 15**: the orchestrator's provider loop is cleaned up in *one* story and *one*
rotation — extracted, deduplicated, its legacy `searxng_url=` parameter retired, its omission logs
stripped of result URLs, and its `provider_errors` composition made to enforce the closed vocabulary
at the point it reaches the wire — with byte-identical wire output on the existing fixtures as the
proof that nothing else moved.

## Goals

- Neither provider decodes more than its bound: a 1 MiB + 1 byte body, compressed or not, returns
  `ProviderFailure(hard_error, body_too_large)` on both providers without the whole body being held
  in memory; a `Content-Encoding` other than `identity` is refused before any body read.
- Every provider call completes or fails within `timeout_seconds` of wall-clock time: a fake peer
  that trickles one byte per second past the budget yields `ProviderFailure(timeout, timeout)` at
  `timeout_seconds` ± 10 %, on both providers.
- The outbound SearXNG `q` never exceeds `search_searxng_query_max_chars` (default 400) characters,
  exactly as Brave's does today; no wire change.
- For every configured chain and every request, the set of paid providers the effective chain can
  call is a prefix of the configured paid sequence — proven by a property test over two paid fakes.
- `search.policy_unknown_provider` grows by at most 1 per `/search` request.
- `run_search_pipeline` after the cleanup produces byte-identical `SearchResponse` JSON for the
  existing fixtures, with `pipeline/orchestrator.py`'s provider loop under 120 lines and no
  `searxng_url=` parameter left in code or tests.

## User Stories

### US-001: Ingress bounds — streamed caps, identity encoding, wall-clock budgets

**Priority:** P1

**Description:** As an operator, I want each search provider to refuse over-size and over-time
responses before they cost memory or hold a request open, so a misbehaving or hostile peer degrades
into a classified provider failure instead of a memory spike or a hung `/search`.

**Independent Test:** With `httpx.MockTransport` handlers that stream bodies (an async byte
iterator, no `Content-Length`), each provider called directly returns
`ProviderFailure(hard_error, body_too_large)` for a 1 MiB + 1 byte stream with fewer than 1 MiB + one
chunk of bytes ever accumulated (assert on the handler's chunk count), returns the same failure for
a response carrying `Content-Encoding: gzip`, and returns `ProviderFailure(timeout, timeout)` for a
stream that yields one byte every 0.2 s against `timeout_seconds=0.5` (measured with
`time.perf_counter`, under 0.6 s). Every existing SearXNG and Brave test passes unchanged.

**Implementation Hints:**
- `SearxngProvider.search` (`pipeline/search_providers/searxng.py:128-205`): replace `client.get(...)`
  + `len(resp.content)` (`:164-175`) with `client.stream("GET", ...)` and the fast-reject-then-count
  shape of `pipeline/stage5_url_audit.py:200-219` (`Content-Length` check, then `aiter_bytes()` with a
  running total; return `self._failure("hard_error", "body_too_large")` the moment the total exceeds
  `_MAX_SEARXNG_RESPONSE_BYTES`, closing the response). The comment at `:57-59` that explains the
  old buffer-then-check order is rewritten. `resp.raise_for_status()` semantics stay: status mapping
  (`:182-188`, `HTTP_STATUS_DETAIL_PREFIX`, `_RATE_LIMITED_STATUS`) runs before the body is read.
- `BraveApiProvider.search` (`brave.py:278-360`): the counter at `:339-346` already streams; add the
  request header `Accept-Encoding: identity` on both clients, and treat a response whose
  `Content-Encoding` header is present and not `identity` as `self._failure("hard_error",
  "body_too_large")` **before** iterating (rationale in the comment at `brave.py:147-150`, which
  currently claims the cap is pre-read — correct it to say "wire bytes, identity encoding forced").
  Add the new detail token to `_SEARXNG_FAILURE_DETAILS` (`searxng.py:69-`) and Brave's equivalent
  set only if a new token is introduced; prefer reusing `body_too_large`.
- Wall-clock budget: wrap each provider's entire client interaction in
  `async with asyncio.timeout(self.settings.timeout_seconds)` (Brave) /
  `asyncio.timeout(_SEARXNG_TIMEOUT_SECONDS)` (SearXNG) and map `TimeoutError` to
  `self._failure("timeout", "timeout")` beside the existing `httpx.TimeoutException` arm
  (`searxng.py:180`, `brave.py:354`). Keep the httpx `timeout=` too — it bounds a single stalled
  socket operation earlier than the budget would.
- `docs/configuration.md:439` (`search_brave_timeout_seconds` row): reword "per-request timeout"
  to "wall-clock budget for the whole Brave call — connect, headers and body together"; the
  `kit_tools/arch/patterns/ERROR_HANDLING.md` Brave timeout row (added by the 2026-09-19
  housekeeping batch) gets the same sentence.
- The provider package must not import a wire model or a sanitization stage (`tests/
  test_search_providers.py`'s import sweep); `asyncio` and `httpx` are already imported.
- No hashed file is touched: `pipeline/search_providers/*` is outside `_REVISION_SOURCES`.

**Acceptance Criteria:**
- [ ] `SearxngProvider` reads its response via `client.stream` with a `Content-Length` fast-reject
      and an incremental byte counter; a streamed body of `_MAX_SEARXNG_RESPONSE_BYTES + 1` bytes
      returns `ProviderFailure("hard_error", "body_too_large")` and the test's handler observes at
      most one chunk past the bound.
- [ ] Both providers send `Accept-Encoding: identity`; a response with any other `Content-Encoding`
      is refused before its body is iterated, on both providers, with a pinned test each.
- [ ] Both providers return `ProviderFailure("timeout", "timeout")` within `timeout_seconds` + 0.1 s
      of wall-clock time for a trickling body (test measures with `time.perf_counter`).
- [ ] `docs/configuration.md`'s `search_brave_timeout_seconds` row and `ERROR_HANDLING.md`'s Brave
      timeout row say "wall-clock budget for the whole call"; `grep -c 'wall-clock' docs/
      configuration.md` ≥ 1.
- [ ] The failure taxonomy is unchanged: `FAILURE_CLASSES` and both providers' detail sets gain no
      new token (or exactly one, named in the story's commit message and added to
      `kit_tools/docs/TROUBLESHOOTING.md`'s token map).
- [ ] `git diff --stat` shows no change under `pipeline/orchestrator.py`, `pipeline/contract.py`,
      `models.py` or `contract/`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Egress bound + cost-monotonic policy by construction

**Priority:** P1

**Description:** As an operator, I want the free provider's outbound query capped like the paid one
and the per-request policy unable to promote a later paid provider over an earlier one, so a caller
cannot spend money through a URI-length failure or through a second paid backend the configured
chain would not have reached first.

**Independent Test:** `SearxngProvider` called with a 5,000-character query sends a `q` of exactly
`search_searxng_query_max_chars` characters (asserted on the transport's request URL). A property
test over a configured chain `[free, paidA, paidB]` (two `FakeSearchProvider`s with `paid=True`)
asserts for every `providers` subset and both `allow_paid_fallback` values that the effective
chain's paid providers are a prefix of `[paidA, paidB]` — `providers: ["paidb"]` yields `[free]`,
never `[free, paidB]`. A `/search` body with 200,000 unknown `providers` entries moves
`search.policy_unknown_provider` by exactly 1.

**Implementation Hints:**
- New `config.yaml` key `search_searxng_query_max_chars` (default `400`, range 1–2000), read the way
  `brave_settings_from_config` reads `search_brave_query_max_chars` (`brave.py:245-275`,
  `_bounded_int` `:224`); give `SearxngProvider` a `query_max_chars` constructor argument with the
  default, wired from the lifespan where `SearxngProvider(DEFAULT_SEARXNG_URL)` is built
  (`retrieval_app.py` `_resolved_search_providers`, ~`:295`) and registered in the config key
  registry spec 3 US-003 created (ruling 12). Truncate the outbound copy only (`brave.py:310-314`
  is the pattern); `SearchRequest.query` keeps its wire shape (search epic ruling 30).
- `apply_request_policy` (`pipeline/search_providers/policy.py:38-77`): rewrite step 3 so the kept
  paid providers are the longest prefix of the configured paid sequence whose every member is in the
  named set (a named paid provider after an un-named one is dropped, not promoted); express steps
  1–4 as four statements that mirror the docstring (finding -060) and update the docstring's
  algorithm to state the prefix rule. Extend `tests/test_search_policy.py::
  TestApplyRequestPolicyIsCostMonotonic` with the two-paid property test (`hypothesis` is not a
  dependency — enumerate the 2³ × 2 cases explicitly) and add
  `test_a_second_paid_provider_cannot_be_reached_by_skipping_the_first`.
- `retrieval_app.py:1799` (`search_metrics.policy_unknown_provider += ignored_count`): change to one
  increment per request when `ignored_count > 0`; keep `apply_request_policy`'s return shape. Update
  the `SearchRequest.providers` description (`models.py:302-315`, "ignored and counted on /metrics")
  and `SearchMetricsResponse.policy_unknown_provider` (`retrieval_app.py:528`) to say "requests with
  at least one ignored entry". This moves `contract/openapi.yaml`: append a line to the 1.3.0
  `CONTRACT_VERSION` docstring entry, regenerate with `uv run python -m scripts.export_contract`,
  and re-create `tests/golden/contract_1_3_0.json` (ruling 5). `pipeline/contract.py` is hashed:
  measure and record the rotation (ruling 6).
- `kit_tools/docs/MONITORING.md`'s `policy_unknown_provider` row states the new unit; `kit_tools/
  arch/SECURITY.md`'s cost section (the "operator spend" non-vulnerabilities row from the
  2026-09-19 housekeeping batch) names the caller-triggerable failure paths that can now no longer
  buy a paid call: URI-length at SearXNG (capped here), post-sanitization omission (search epic
  ruling 17), unknown policy names (ignored, never a 422).

**Acceptance Criteria:**
- [ ] `search_searxng_query_max_chars` exists in `config.yaml` (default 400), is bounded 1–2000 at
      boot (an out-of-range value refuses boot with the same message shape as
      `search_brave_query_max_chars`), has a `docs/configuration.md` row and is in the key registry;
      the outbound SearXNG `q` is truncated to it and the request's `query` field is unchanged.
- [ ] `apply_request_policy` keeps only a prefix of the configured paid providers; the enumerated
      two-paid test and `test_a_second_paid_provider_cannot_be_reached_by_skipping_the_first` pass;
      every existing `tests/test_search_policy.py` case passes unchanged.
- [ ] The four docstring steps of `apply_request_policy` correspond one-to-one to four statements in
      the function body (a reviewer can point at each).
- [ ] `search.policy_unknown_provider` increments at most once per `/search` request; a test posts
      2,000 unknown entries and asserts a delta of 1; `MONITORING.md` and the two wire descriptions
      state the per-request unit.
- [ ] Contract regenerated inside the 1.3.0 window: docstring line appended, `contract/openapi.yaml`
      + `.sha256` regenerated, `tests/golden/contract_1_3_0.json` re-created, `uv run python -m
      scripts.export_contract --check` clean; `tests/golden/contract_1_2_0.json` untouched.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce) and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: The `run_search_pipeline` cleanup — one rotation, behaviour preserved

**Priority:** P2

**Description:** As a maintainer, I want the provider loop in `run_search_pipeline` extracted,
deduplicated and stripped of its legacy parameter and of result-URL logging, in a single recorded
rotation, so the next hardening change to search lands in a function a reviewer can hold in their
head — with proof that the wire output did not move.

**Independent Test:** Before the change, capture the serialized `SearchResponse` for at least three
representative `tests/test_orchestrator.py` search fixtures (a clean SearXNG success, a fallback
to Brave with `provider_errors`, and a run with omissions under every `omitted_by_reason` token);
after the change, the same inputs produce byte-identical JSON (`model_dump_json()` compared as
strings), every pre-existing search test passes with only the `searxng_url=` → `providers=`
migration in the diff, and an omitted result's URL appears in no log record.

**Implementation Hints:**
- Extract the loop at `pipeline/orchestrator.py:874-938` into a module-level
  `async def _query_provider_chain(...) -> _ServedChain` (a small dataclass: serving provider,
  raw results, `provider_errors`, `fallback_fired`, `paid_calls` delta) so `run_search_pipeline`
  reads as query → sanitize → respond. One `_log_provider_failure(provider, failure_class, detail)`
  replaces the two WARNING statements at `:907-912` and `:925-930` (finding -035), with
  `_UNRESPONSIVE_ENGINES_DETAIL = "unresponsive_engines"` beside it.
- `_legacy_searxng_codes(configured_chain)` is evaluated once before the loop and reused at both
  raise sites (`:906`, `:946`; finding -038); the two identical `ValueError("...empty provider
  chain...")` sites (`:842-846`, `:941-945`) collapse to the upfront guard; the comment at `:903`
  says "configured chain of exactly one `searxng` (search epic ruling 28)" (finding -039).
- Retire `searxng_url=` (`:777` signature, `:817-824` docstring, `:856-858` branch) and migrate the
  19 `tests/test_orchestrator.py` call sites (lines 937, 968, 989, 1027, 1076, 1104, 1130, 1151,
  1166, 1189, 1211, 1253, 1291, 1329, 1366, 1428, 2133, 2462, 2690 at planning time) to
  `providers=[SearxngProvider(url)]` or the `_searxng_client_patch` helper (`tests/
  test_orchestrator.py:897`) — finding -011.
- Omission INFO lines (`:999-1003`, `:1023-1027`, `:1031-1035`) log the closed reason token and the
  validated `domain` (`SearchResult.domain` — lower-cased hostname), never the result URL
  (finding -043: on a fallback the URL comes from the Brave body, and telemetry is metadata only —
  search epic owner decision 6). Extend the sentinel test from search epic spec 2 US-012
  (`tests/test_brave_provider.py`, the key-never-leaks family) so an omitted result's URL is a
  sentinel and is asserted absent from every record's `getMessage()`.
- `provider_errors` composition (`:924`): map a `failure_class` not in `FAILURE_CLASSES` to
  `hard_error` and a `detail` not matching `^[a-z0-9_]{1,32}$` to `unexpected` before the entry is
  built (finding -045); a test registers a fake returning `ProviderFailure("weird", "x y")` and
  asserts the entry `"<name>: hard_error"` and the log token `unexpected`.
- `pipeline/orchestrator.py` is hashed: this is the one rotation the whole cleanup gets; the
  `provider_errors` and omission-log changes are deliberately inside it. Record per ruling 6.

**Acceptance Criteria:**
- [ ] `_query_provider_chain` exists at module level in `pipeline/orchestrator.py`; the provider loop
      body inside `run_search_pipeline` is gone; the function under `def run_search_pipeline` is
      shorter than 200 lines (`wc -l` on the function span).
- [ ] Exactly one `search_provider_failed` WARNING statement remains in `pipeline/orchestrator.py`;
      `_legacy_searxng_codes` is called once per request; one empty-chain `ValueError` site remains.
- [ ] `grep -rn 'searxng_url' pipeline/orchestrator.py tests/` returns nothing.
- [ ] No omission log line contains a result URL: the sentinel test passes; the lines carry the
      reason token and `domain=`.
- [ ] A `ProviderFailure` with an out-of-vocabulary class or detail reaches the wire as
      `"<name>: hard_error"` with `detail=unexpected` in the log, pinned by a test.
- [ ] Behaviour preservation: the three captured fixtures produce byte-identical `SearchResponse`
      JSON before and after (the captures are committed as `tests/fixtures/search/*.json` and the
      comparison test stays in the suite); all pre-existing search tests in
      `tests/test_orchestrator.py` and `tests/test_app.py` pass with no assertion changed.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce) and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- A response with `Content-Length` under the bound whose body then exceeds it is still capped by the
  running counter (US-001).
- A `Content-Length` header that is not a digit string is ignored and the counter decides (US-001).
- `Content-Encoding: identity` explicitly present is accepted; absent is accepted; `gzip, identity`
  is refused (US-001).
- The wall-clock budget expiring after headers but before the first body byte maps to `timeout`,
  not `body_too_large` (US-001).
- A query shorter than the cap is sent untouched; a cap equal to the query length sends it whole;
  truncation counts characters, not bytes (US-002).
- `providers` naming a paid provider that is configured *first* keeps it; naming only a later paid
  provider drops every paid provider (US-002).
- A request whose ignored entries are all duplicates of one unknown name still counts 1 (US-002).
- Zero ignored entries counts 0; the metric is monotonic per process (US-002).
- The cleanup must keep the search epic's ruling 28 predicate on the *configured* chain — a
  policy-filtered `[searxng]` effective chain with a two-provider configured chain still raises
  `search_unavailable`, never `searxng_*` (US-003; pinned by `tests/test_app.py`'s
  `test_exhaustion_code_follows_the_configured_not_the_effective_chain`).
- An omitted result with an unparseable host has no `domain`; the log line carries the reason token
  only (US-003).

## Out of Scope

- A spend ceiling or budget breaker (search epic ruling 12 stands; Poppy's P4 is the only cap).
- New providers, LiteLLM adapters, or any change to the `SearchProvider` protocol's members.
- Changing the 1 MiB bounds themselves or making them configurable.
- Structural or PromptGuard behaviour on search text — spec 1 of this epic.
- `SearchResult.engine` bounding — spec 1 US-004.

## Assumptions

- Poppy is the only consumer; the `policy_unknown_provider` unit change is announced in the 1.3.0
  window and its description line, and no consumer graphs the old per-entry unit.
- `hmac`/`hashlib` and `asyncio.timeout` are stdlib (Python ≥ 3.11 per `pyproject.toml`); no new
  dependency.
- Both providers speak HTTP/1.1 to peers that honour `Accept-Encoding`; a peer that ignores it and
  compresses anyway is refused, which is the conservative failure.
- The 1.3.0 contract window is open when this spec executes (spec 1 US-004 opened it).
- The three behaviour-preservation fixtures are representative enough; they are kept as regression
  fixtures for later rotations of `orchestrator.py`.

## Technical Considerations

- `pipeline/search_providers/*` is not in `_REVISION_SOURCES`; US-001 rotates nothing. US-002 rotates
  through `contract.py` (description text); US-003 rotates through `orchestrator.py`. Two rotations
  for this spec, both recorded (ruling 6).
- `httpx.MockTransport` handlers may return `httpx.Response(200, stream=<AsyncByteStream>)`; use an
  async generator wrapper so the test can count chunks the provider actually pulled.
- `asyncio.timeout` nests inside the `httpx.AsyncClient` context so the client closes on expiry;
  order the `except TimeoutError` arm before `except Exception` (the catch-all, search epic ruling
  27) so the class stays `timeout`.
- The `/metrics` order guards (`test_served_metrics_are_the_handlers_dict_serialized`,
  `test_metrics_mirror_round_trips_the_served_body` in `tests/test_app.py`) do not care about the
  unit change; only the description text and the golden move.
- Behaviour-preservation fixtures must be captured from the *pre-change* tree in the same story
  (first commit of the story, before the refactor), so the comparison is honest.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md) (search pipeline section)
- Security: [SECURITY.md](../arch/SECURITY.md) (cost section, non-vulnerabilities table)
- Patterns: [ERROR_HANDLING.md](../arch/patterns/ERROR_HANDLING.md), [LOGGING.md](../arch/patterns/LOGGING.md)
- Configuration: `docs/configuration.md` (`search_*` rows)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("Nothing configures logging")
- Rotations: `docs/bootstrap-notes.md`, [DECISIONS.md](../arch/DECISIONS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Mirror `stage5_url_audit.py`'s fast-reject-then-count shape in `SearxngProvider` rather
than lowering the bound or adding a config knob.
**Rationale:** The shape already exists and is tested for fetches (`stage5_url_audit.py:200-219`);
Brave uses half of it (`brave.py:331-346`). One idiom, three call sites.
**Alternatives considered:** `httpx`'s `max_content_length` — not a client option; a per-provider
config bound — no operator has asked for one.
**Source:** `pipeline/search_providers/searxng.py:57-60,164-175`; `brave.py:147-150,331-346`.

**Decision:** Force `Accept-Encoding: identity` instead of decompressing incrementally with a cap.
**Rationale:** Incremental decompression needs a `zlib.decompressobj(max_length=...)` loop per
encoding; identity makes the wire-byte counter exact and the failure mode explicit.
**Alternatives considered:** `max_length` decompression — more code for a peer we control (SearXNG)
and a vendor that supports identity (Brave).
**Source:** finding 2026-09-16-020 (67 MB transient chunk observed).

**Decision:** Prefix rule for paid providers (ruling 14) rather than scoping the docstring to "one
paid provider".
**Rationale:** The guarantee should hold when T3.1 adds a second paid backend; scoping it would
make that epic re-open policy.
**Alternatives considered:** Docstring scoping plus a tripwire test — rejected as deferring the fix.
**Source:** `policy.py:57-77`; finding 2026-09-16-052.

**Decision:** One cleanup story, one rotation, with committed byte-identical fixtures.
**Rationale:** Seven findings touch the same 70 lines; each on its own would rotate the revision and
need its own record (ruling 6).
**Source:** findings -035, -036, -038, -039, -043, -045, -011; `orchestrator.py:842-946, 999-1035`.

### Scope Adjustments

- Finding -014 (`engine` bound) moved to spec 1 US-004 because it is wire-adjacent and belongs with
  the contract window opening.

### Decisions Made

- No new failure tokens unless a story proves one necessary; `body_too_large` and `timeout` cover
  the new refusals.
- `policy_unknown_provider`'s unit change rides the open 1.3.0 window rather than waiting for spec 8.

## Clarifications

### Session 2026-09-19
- Q: Cap provider bodies before or after decode? → A: Before; force identity encoding (ruling 13).
- Q: Is the per-provider timeout a per-operation or a wall-clock budget? → A: Wall-clock, via
  `asyncio.timeout` (ruling 13); the httpx per-operation timeout stays as an inner guard.
- Q: Cost-monotonicity with a second paid provider — restrict by construction or by docstring? → A:
  By construction, paid-prefix rule (ruling 14).
- Q: How many increments may one request add to `policy_unknown_provider`? → A: One (ruling 14).
- Q: How many rotations does the orchestrator cleanup get? → A: One story, one rotation (ruling 15).

## Open Questions

- [ ] Should the 1 MiB provider bounds become `config.yaml` keys in the resource-envelope spec
      (spec 6)? Non-blocking; default answer is no until an operator hits the bound.
- [ ] Whether Brave's LLM-Context endpoint honours `Accept-Encoding: identity` for every plan tier —
      confirm at the next owner-run capture; non-blocking (a non-identity response is refused, which
      is safe).
