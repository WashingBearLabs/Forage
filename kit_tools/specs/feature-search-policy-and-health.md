<!-- Template Version: 2.5.0 -->
---
feature: search-policy-and-health
status: active
session_ready: true
depends_on: [search-fallback]
vision_ref: "T2.1 — Search-provider abstraction & reliable search"
type: epic-child
size: L
epic: search-providers
epic_seq: 4
epic_final: false
execution_order: [US-012, US-011, US-002, US-003]
created: 2026-09-14
updated: 2026-09-14
---

# Feature Spec: Per-Request Policy + Provider Health

> **Epic 4 of `epic-search-providers`.** Give the consumer the two things a policy UI needs:
> per-request policy params on `SearchRequest` (restrict the configured chain per call — never
> keys, never a promotion) and provider status on `/health`. Then finish the 1.2.0 contract: the
> `/search`↔`/retrieve` boundary text, the golden coverage sweep, and the version record.
> Context: Poppy's `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (rows F7, F8, F9, F11). Epic rulings 12,
> 13, 15, 16, 21, 22, 26, 28, 29 and 32 are binding here and are not re-opened.

## Overview

Poppy's settings UI will let an operator set search *policy* per request — which of the
configured providers may run, and whether the paid step may run at all — and display provider
status. It must do both without ever holding or sending keys: keys are Forage's environment,
policy is per-request. This spec adds two concrete-default fields to `SearchRequest` that can
only *narrow* what the deployment already permits (ruling 16, simplified by ruling 29), extends
`/health` with the resolved chain and a per-provider key-presence entry in the existing
`capabilities` map (ruling 15), then closes the 1.2.0 contract: the `/search`↔`/retrieve`
boundary written into the route descriptions, a sweep proving every 1.2.0 addition is
golden-pinned, and the version record in the `CONTRACT_VERSION` docstring.

Two properties run through every story. **Cost-monotonic policy (ruling 29):** a request may
exclude only `paid=True` providers. The effective chain is the configured chain with a subset of
its paid providers removed — free providers are never removed, nothing is added, reordered or
keyed — so no request can cause a paid call the configured chain would not have made from the
same free-path outcome, on any chain; `allow_paid_fallback` is one-way. **No second channel for
key presence:** key presence is public on the private network — `/health` publishes it
deliberately — so the "no oracle" rule is not secrecy; it is that `/search`'s status code and
response field set never vary with key presence for the same request body.

## Goals

- `SearchRequest.providers: list[str] = []` and `SearchRequest.allow_paid_fallback: bool = True`
  — restrict-only (paid providers only), order-preserving, applied before spec 3's traversal;
  the items carry no pydantic pattern or length validation; the policy function normalises,
  keeps the first eight entries and ignores-and-counts every other entry identically on
  `/metrics` `search.policy_unknown_provider`; never a key field.
- `/health` gains `search_providers: list[str]` (the resolved chain's names) and one
  `capabilities` entry per keyed paid provider (`brave_api_key`); no other `/health` change, no
  new `DegradedReason`, no probe, no 500 on any transport.
- Contract still 1.2.0 with the drift gate and golden green after each story; the
  `/search`↔`/retrieve` boundary in the route descriptions and docs; every 1.2.0 addition
  golden-pinned and no unlisted addition possible; the 1.2.0 entry in the `CONTRACT_VERSION`
  docstring complete.

## User Stories

### US-001: [SPLIT — see US-010, US-011]

> Split by supervisor: Retries exhausted at the M-size 900 s budget: attempts 1 and 2 timed out during implementation (17 criteria spanning wire model, pure policy function, handler wiring, metrics, a new 422 path, contract regeneration, six doc files and a revision rotation record). Scope too large for one session — split into code + contract regeneration + tests (US-010) and docs + anchor refresh + rotation record (US-011).

### US-010: [SPLIT — see US-012]

> Split by supervisor: Retries exhausted, but attempt 3 implemented the story completely and failed verification only on a test-widening and a docstring; its commit (8f7dd0f) was recovered from the reflog, fast-forwarded onto epic/search-providers with all four gates green (2028 tests), so the remaining work is a single short follow-up story rather than a re-implementation.

### US-012: Finish per-request policy — widen the reason-format test and close US-010's verifier items (follow-up to US-010)

**Priority:** P1

**Description:** Follow-up to US-010, created by the supervisor. US-010's third attempt implemented the per-request policy completely — `SearchRequest` fields, `apply_request_policy`, handler wiring, counter, policy 422, regenerated contract, 838 lines across twelve files — and failed verification only on two narrow items. That implementation is **already on this branch** (commit `8f7dd0f`, fast-forwarded by the supervisor after all four gates passed: ruff, format, pyright, 2028 tests). **Do not reimplement anything.** Make the two required fixes below, the recommended hardening if time allows, re-run the gates, and record the outcome in the Implementation Notes. Budget: this is a fifteen-minute story.

**Independent Test:** `uv run pytest tests/test_orchestrator.py tests/test_app.py tests/test_search_policy.py tests/test_models.py tests/test_brave_provider.py -q` passes with the widened reason-format test exercising both reason forms, and every US-010 criterion below still holds on the branch.

**Acceptance Criteria:**
- [x] Spec 3's reason-format test (`tests/test_orchestrator.py::test_search_unavailable_reason_is_chain_order_provider_errors`) is widened — or a sibling test is added beside it — so it accepts exactly two forms: the reason either equals `contract.POLICY_EXCLUDED_ALL_PROVIDERS`, or every `'; '`-separated entry is `'<chain name>: <FAILURE_CLASSES member>'`; anything else fails; both forms are exercised, the literal through the `/search` handler on a paid-only chain; the existing assertions are not deleted.
- [x] The stale "orchestrator.py alone" docstring in `tests/test_brave_provider.py` is corrected to describe what the test now measures.
- [x] Recommended (do if the budget allows, otherwise record why not): one clause on the `allow_paid_fallback` description pointing at the `providers` normalise/ignore/count rule (then re-run `uv run python -m scripts.export_contract` and regenerate the golden); `test_providers_naming_an_unknown_provider_leaves_only_searxng` given a failing SearXNG so it proves Brave was removed; the legacy `searxng_error` reason bytes asserted in the byte-for-byte test.
- [x] `SearchRequest` carries `providers: list[str]` (default `[]`) and `allow_paid_fallback: bool` (default `True`); neither is `Optional`; the items carry no pydantic constraint of any kind (a test asserts the field's JSON schema carries no `maxItems`, no `maxLength` and no regular expression); no request field carries a key; both descriptions state the paid-only restrict rule, the normalise-then-ignore-and-count rule, and "honoured from contract 1.2.0"; `tests/test_models.py::TestSearchRequest` covers the defaults, a round-trip, and that an arbitrary string item validates.
- [x] `apply_request_policy(chain, request)` in `pipeline/search_providers/policy.py` is a pure function returning the effective chain and the ignored count; a property-style test in `tests/test_search_policy.py` over configured chains of free and paid fakes (one to four providers, any mix, any order) and request policies asserts the effective chain equals the configured chain with a subset of its `paid=True` providers removed — every `paid=False` provider present, in configured position, the effective chain a subsequence of the configured chain — and that `allow_paid_fallback: false` leaves no paid provider.
- [x] On configured chain `["searxng", "brave"]`: omitted params traverse the configured chain (same `provider_used` / `fallback_fired` as spec 3's baseline); `providers: ["searxng"]` never calls Brave; `providers: ["brave"]` yields effective chain `["searxng", "brave"]`; `providers: ["tavily"]` yields `["searxng"]`; `allow_paid_fallback: false` with a failing SearXNG returns `search_unavailable` with reason `"searxng: <failure_class>"` and never calls Brave (mock call logs asserted).
- [x] Normalisation and ignoring: `[" SearXNG "]` matches `searxng` with the counter unmoved; nine entries leave the ninth ignored and counted; a 33-character entry, an entry with interior whitespace, and a hostile entry are each ignored and counted; `["tavily", "exa", "tavily"]` adds three; every one of these requests reaches the handler and is served by SearXNG with status 200 — none is a 422.
- [x] `providers: ["brave"]` on a key-less deployment (configured chain `["searxng"]`) returns the same status code and the same response field set as on the keyed deployment; a test asserts equality of the status and of `set(body)` between the two.
- [x] `search.policy_unknown_provider` rises by exactly one per ignored entry, unknown and key-absent alike, and no entry is stored; `/metrics` `search` carries `policy_unknown_provider` at the same position in `SearchMetricsResponse` and in the handler's dict; `tests/test_contract_metrics.py` passes in full.
- [x] On a paid-only configured chain, `allow_paid_fallback: false` and, separately, `providers` naming no registered provider each return 422 `search_unavailable` with `reason` equal to `policy_excluded_all_providers`, call no provider, and never return 200 with an empty list — the 422 is raised by the `/search` handler before `run_search_pipeline` is called, and a test asserts it increments `search.errors.search_unavailable` exactly once per refused request (the handler calls `record_error` in the policy branch); `run_search_pipeline(providers=[])` still raises `ValueError` (spec 1's test unchanged) while `providers=None` builds spec 1's default chain; the count assertions in `tests/test_contract_errors.py` are unchanged.
- [x] The exhaustion code follows the configured chain (ruling 28), which the handler passes as `configured_chain=` beside the effective `providers=`: `providers: ["searxng"]` on `["searxng", "brave"]` with SearXNG failing is `search_unavailable`, never `searxng_error`; on configured `["searxng"]` the legacy codes are byte-for-byte as today.
- [x] `POLICY_EXCLUDED_ALL_PROVIDERS` is defined in `pipeline/contract.py` and imported by `retrieval_app.py` (the policy raise site); `search_unavailable` is raised from `pipeline/orchestrator.py` (exhaustion) and `retrieval_app.py` (policy), both inside the raise-site sweep; spec 3's reason-format test accepts exactly the two forms (chain-order list, fixed literal).
- [x] A hostile `providers` entry appears in no response body, no `/metrics` key and no log record (test in the style of `test_connect_failure_never_logs_url_or_secret`).
- [x] `contract/openapi.yaml` + `.sha256` and the current golden `tests/golden/contract_1_2_0.json` are regenerated under 1.2.0 (`uv run python -m scripts.export_contract`); `tests/test_contract_export.py` and `tests/test_contract_schema.py` pass. The four doc anchor refreshes, the doc rows and the rotation record are US-011's.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

**Implementation Hints:**
- Start with `git log --oneline -3` and `git show --stat 8f7dd0f`: the implementation is there. Read `pipeline/search_providers/policy.py`, the `/search` handler changes in `retrieval_app.py`, and the new tests in `tests/test_app.py` and `tests/test_search_policy.py` before touching anything.
- Fix 1: in `tests/test_orchestrator.py`, find `test_search_unavailable_reason_is_chain_order_provider_errors`; keep its assertions and add the second accepted form (`reason == POLICY_EXCLUDED_ALL_PROVIDERS`), then add a case that drives the literal through `POST /search` on a paid-only configured chain (`FORAGE_SEARCH_PROVIDERS=brave` with a key) with `allow_paid_fallback: false`, and a negative case proving a third form fails.
- Fix 2: grep `tests/test_brave_provider.py` for `orchestrator.py alone` and rewrite that docstring to match the current measurement (the revision now rotates on `contract.py` too).
- If you take the recommended items, regenerate the contract and the golden `tests/golden/contract_1_2_0.json` in the same change so `tests/test_contract_export.py` and `tests/test_contract_schema.py` stay green.
- Record in this spec's Implementation Notes that US-010's implementation landed at `8f7dd0f` via supervisor fast-forward and what this story changed. US-011 owns the doc rows, the four anchor refreshes and the `sanitizer_revision` rotation record — leave those alone.


### US-011: Per-request policy — doc rows, contract anchor refresh and `sanitizer_revision` rotation record (split of US-001, part 2)

**Priority:** P1

**Description:** Second half of the original US-001 (split by the supervisor). US-010 delivered the per-request policy fields, the policy function, the handler wiring, the counter, the policy 422 and the regenerated contract. This story lands the documentation that makes the change legible: the doc anchor refreshes, the API, monitoring, troubleshooting, error-handling and security rows, the test-mapping row, and the `sanitizer_revision` rotation record. It changes no code unless a doc claim exposes a defect (fix minimally and say so in the Implementation Notes).

**Independent Test:** Every claim below is grep-verifiable: the anchor hash in the four docs equals the committed `contract/openapi.yaml.sha256`; each named table carries its row; `docs/bootstrap-notes.md`'s latest record equals `derive_sanitizer_revision()`; the full suite and the three static gates stay green.

**Acceptance Criteria:**
- [x] The anchor hash embedded in `kit_tools/docs/API_GUIDE.md`, `kit_tools/docs/CI_CD.md`, `kit_tools/docs/DEPLOYMENT.md` and `kit_tools/arch/SERVICE_MAP.md` (~202) matches the committed `contract/openapi.yaml.sha256` (ruling 32).
- [x] `kit_tools/docs/API_GUIDE.md`'s `SearchRequest` field table under `### POST /search` gains both rows plus one sentence that a consumer sources its selectable names from `/health` `search_providers` and reconciles there; `kit_tools/docs/MONITORING.md`'s `/metrics` `search` table gains `policy_unknown_provider` with the unit (one per ignored entry) and the disambiguation procedure (compare the consumer's names against `/health` `search_providers`; a rising counter with no `brave_api_key` entry means the key, not the name); `kit_tools/arch/SECURITY.md`'s "Input Validation and Resource Bounds" inventory states that the items carry no pydantic bound by design and that the bound (first eight, matched or ignored) is in the policy function.
- [x] The `search_unavailable` rows in `kit_tools/docs/TROUBLESHOOTING.md`, `kit_tools/arch/patterns/ERROR_HANDLING.md` and API_GUIDE's `/search` 422 row each name `policy_excluded_all_providers` as the second reason form (policy-excluded, not provider-failure); `kit_tools/testing/TESTING_GUIDE.md`'s `test_mapping` gains `pipeline/search_providers/policy.py` → `tests/test_search_policy.py` (suite counts stay spec 5 US-003's, ruling 32).
- [x] The `sanitizer_revision` rotation (before, after, cause: the policy literal in `contract.py`; `retrieval_app.py`, where the raise lives, is not hashed) is recorded in `docs/bootstrap-notes.md`, the three `kit_tools/` rotation tables (GOTCHAS, DECISIONS, CODE_ARCH) and `CLAUDE.md`'s "Coexistence with Poppy" paragraph, and the recorded after-value equals `derive_sanitizer_revision()` on this branch.
- [x] Tests written/updated for new functionality (documentation-only stories satisfy this with the existing suite green).
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

**Implementation Hints:**
- Read US-010's Implementation Notes first: it records the `sanitizer_revision` before/after values and the exact contract anchor; verify both with `uv run python -c "from pipeline.sanitizer_revision import derive_sanitizer_revision; print(derive_sanitizer_revision())"` and `cat contract/openapi.yaml.sha256` rather than trusting the note.
- Anchor refresh: the four docs quote the sha256 of `contract/openapi.yaml`; replace the old hash with the committed one in all four (grep for the old value to find every site, including any prose sentence that quotes it).
- Rotation record: follow the existing entry shape in `docs/bootstrap-notes.md` (before, after, cause, the story) and the three `kit_tools/` tables; `CLAUDE.md`'s "Coexistence with Poppy" paragraph gets one more clause in the running sentence. `ruff format` reflows Python fences inside Markdown, so keep any code fence you touch formatted.
- Doc rows: API_GUIDE's `SearchRequest` table (both fields, wire-text descriptions consistent with `models.py`); MONITORING's `/metrics` `search` table; SECURITY's request-bounds inventory; TROUBLESHOOTING / ERROR_HANDLING / API_GUIDE 422 rows naming `policy_excluded_all_providers`; TESTING_GUIDE `test_mapping` row for `policy.py`. Keep placeholder-checker-safe prose (no bracketed template tokens).


### US-002: `/health` provider status

**Priority:** P1

**Description:** As the Poppy consumer, I want `/health` to report the resolved provider chain
and, per keyed paid provider, whether its key is present, so the settings page can show
provider status without ever seeing a key value — and without `/health` gaining a probe, a new
degraded reason, or a 500 on any transport.

**Independent Test:** With the lifespan run under `FORAGE_SEARCH_PROVIDERS=searxng,brave` and a
dummy `FORAGE_BRAVE_API_KEY`, `GET /health` returns 200 with `search_providers` equal to
`["searxng", "brave"]` and `capabilities["brave_api_key"] == 1`. With the key unset,
`search_providers` is `["searxng"]` and `"brave_api_key" not in capabilities`; the same for
`FORAGE_BRAVE_API_KEY=` (empty, the shape a compose passthrough renders), a whitespace-only
value, and a value carrying a control character (spec 2's `brave_key_invalid`). With the key
set but `FORAGE_SEARCH_PROVIDERS=searxng`, `search_providers` is `["searxng"]` and
`capabilities["brave_api_key"] == 1`. With the key present and PromptGuard unloaded,
`capabilities == {"brave_api_key": 1}`; with break-glass armed and no key, `capabilities ==
{"search_sanitization": 1}`. The dummy key value appears in no `/health` body and no log record
in any case. A lifespan-free `httpx.ASGITransport` client gets 200 with `search_providers`
equal to `["searxng"]` and no `brave_api_key` entry. `status`, `degraded_reasons`,
`promptguard_loaded`, `cache_connected` and `cache_backend` are unchanged in every case.

**Implementation Hints:**
- `HealthResponse` (`retrieval_app.py`) gains `search_providers: list[str]` — **required**, as
  `cache_backend` is (the handler always has a value through the `_resolved_*` fallback) —
  description: the `SearchProvider.name` tokens of the resolved chain in traversal order, i.e.
  the names registered at start from `FORAGE_SEARCH_PROVIDERS` after spec 2's key-gated skips;
  configuration echo fixed for the life of the process, not a liveness probe; added in
  contract 1.2.0. Key presence goes into the **existing** `capabilities: dict[str, int]`
  presence map (ruling 15; the field's own description at ~264–275 explains why a dict: a new
  key is an additive-safe MINOR). Add `CAPABILITY_BRAVE_API_KEY = "brave_api_key"` beside
  `CAPABILITY_SEARCH_SANITIZATION` and rewrite the comment block that introduces it (~236–241,
  "The one capability key") for two keys; `contract_smoke.py` keeps importing only the
  sanitization constant (it derives the expected field set and asserts nothing about the new
  key). Pin the new literal in `tests/test_app.py` the way the existing one is pinned. Rewrite
  the `capabilities` description: it says "Sanitization capabilities ... One key is defined in
  contract 1.1.0" and must say two keys are defined in 1.2.0 and what each means — and that
  `search_sanitization` is a runtime claim the break-glass override can force, while
  `brave_api_key` is an environment fact no override touches. The two keys are computed
  independently: the handler's single all-or-nothing expression (~1262–1266) becomes the
  sanitization entry as today plus the key entry from state. There is no generic key-presence
  boolean: keys are per-provider (ruling 10) and so is their presence.
- **State seam (ruling 28).** `app.state.search_providers` is exactly spec 1's: it holds
  `list[SearchProvider]`, published by the lifespan, `None` at module scope, and read through
  `_resolved_search_providers(state)`, whose fallback is spec 1's default one-element chain
  holding `SearxngProvider(DEFAULT_SEARXNG_URL)` and reads no environment. `/health` derives
  `search_providers = [p.name for p in _resolved_search_providers(state)]` — there is **no**
  separate name list on state and no module-scope `["searxng"]` string default; the
  lifespan-free value `["searxng"]` is the name of spec 1's default chain, not a literal here.
  No drift test is needed for the names (the idiom at `tests/test_app.py:1528` guards two
  functions encoding one rule): the name is read off the object that serves, so there is
  nothing to drift. Key presence: spec 2 US-002's lifespan resolution evaluates the shared
  helper `brave_key_present()` (strip, non-empty, ASCII) exactly once, and that single verdict
  feeds both the chain build and `app.state.search_key_capabilities: tuple[str, ...]` —
  `("brave_api_key",)` when present, `()` otherwise. A tuple, not a set, so `/health`'s key
  order is stable across `PYTHONHASHSEED` (the discipline `tests/test_contract_export.py`
  documents). Declare it `None` at module scope beside `app.state.model_task = None`
  (~1182–1200); `_resolved_search_key_capabilities(state)` returns `()` for `None`, modelled on
  `_resolved_cache_backend` / `_resolved_sanitizer_revision` (~198–221), and never reads
  `os.environ`. Because advertisement and registration come from one evaluation, an empty
  `FORAGE_BRAVE_API_KEY=` from spec 5's compose passthrough, a whitespace-only value and a
  `brave_key_invalid` value all report absent exactly as the chain does. The guard for that
  pairing, modelled on `tests/test_app.py:1554`
  (`test_the_lifespan_publishes_the_backend_it_selected`): a real-lifespan test with `brave`
  configured asserts `("brave" in search_providers) == ("brave_api_key" in capabilities)` for a
  present key and for an absent one. Never re-read `os.environ` in the handler.
- **Test mechanics (ruling 26d).** `app` is a module singleton and nothing resets `app.state`
  between tests; this story's real-lifespan cases overwrite both attributes for every later
  test in the session. Every test that exercises the lifespan-free fallback saves, `delattr`s
  and restores `app.state.search_providers` and `app.state.search_key_capabilities` in a
  `finally`, on the idiom at `tests/test_app.py` ~1504–1517
  (`test_health_without_a_cache_still_names_a_backend_and_never_500s`), so the lifespan-free
  criterion holds in any test order.
- No probe, no new `DegradedReason`: a missing paid key is a supported mode and never a
  `degraded_reasons` value; `search_providers` is configuration echo, not liveness. The
  distinction between "`brave` configured but skipped for want of a key" and "never configured"
  is not on `/health` (ruling 15 bounds the additions to these two); the operator's procedure
  is to compare `search_providers` against the configured `FORAGE_SEARCH_PROVIDERS`, and the
  startup signal is spec 2 US-002's WARNING `brave_skipped_missing_key`. The stale claim that
  `/health` degrades when SearXNG is unreachable is struck by spec 5 US-001, not here.
- Regenerate the contract (still 1.2.0, additive) and `tests/golden/contract_1_2_0.json`
  (`HealthResponse` is pinned there). `contract_smoke.py` needs no edit for the shape: its
  expected `/health` field set is `set(HealthResponse.model_fields)` (~261), so the addition
  flows into the CI smoke gate automatically, and its `EXPECTED_STATUS = "degraded"` is
  unaffected because no degraded reason was added. Refresh the anchor hash embedded in
  `kit_tools/docs/API_GUIDE.md`, `kit_tools/docs/CI_CD.md`, `kit_tools/docs/DEPLOYMENT.md` and
  `kit_tools/arch/SERVICE_MAP.md` (~202).
- Doc fan-out this story owns, in the same change: `kit_tools/docs/MONITORING.md` — the
  `/health` field table (~57–67) gains a `search_providers` row **and** the existing
  `capabilities` row's value-domain cell is rewritten to list both keys and when each is
  present, the schema-style body listing (~73–84) and the stock-image sentence (~88) gain
  `search_providers: ["searxng"]` with an unchanged `capabilities: {}`, the failure-matrix row
  "SearXNG failures | no signal" stays true but says `search_providers` is configuration echo,
  and the compare-against-configured procedure above is written down;
  `kit_tools/docs/API_GUIDE.md` — the `GET /health` field table (~100–115);
  `docs/configuration.md` — the "Verifying a running instance" field table (~449–456,
  `search_providers` only: that table lists no
  `capabilities`) and the break-glass section (~189), whose "Only `capabilities` lies" is
  scoped to `search_sanitization`, as is `retrieval_app.py`'s `_break_glass_arming_env_var`
  docstring; `README.md` — the `GET /health` row's field list (~50, `search_providers`);
  `kit_tools/arch/SECURITY.md` — the unauthenticated-disclosure statement gains "the resolved
  search chain and paid-key presence, by design", and the "Documented non-vulnerabilities"
  table gains a row recording that a keyed deployment is identifiable from `/health` (accepted
  under rulings 12 and 15); `kit_tools/arch/SERVICE_MAP.md` — the Health Check row (~74) gains
  `search_providers`, and the two `capabilities` statements (~150, ~321) read as a presence map
  that may also carry `brave_api_key`, not a one-key claim (ruling 32).

**Acceptance Criteria:**
- [x] `HealthResponse` carries `search_providers: list[str]` as a required field;
      `capabilities` carries `brave_api_key: 1` exactly when `brave_key_present()` was true at
      start — an empty, whitespace-only or control-character `FORAGE_BRAVE_API_KEY` yields no
      entry — and no `/health` field other than these two changes; the key value appears in
      no `/health` body and no log record (test in the
      `test_connect_failure_never_logs_url_or_secret` style).
- [x] Every lifespan case in the Independent Test (key present and chained; key absent; the
      three absent-shaped values; key present but not chained; key present with PromptGuard
      unloaded; break-glass armed with no key) returns the stated `search_providers` and
      `capabilities` values; the existing break-glass tests (`tests/test_app.py` ~242–271)
      pass unchanged.
- [x] `app.state.search_providers` keeps spec 1's shape (`list[SearchProvider]`, `None` at
      module scope) and `/health` derives its names as
      `[p.name for p in _resolved_search_providers(state)]`; no name list exists on state; a
      lifespan-free `ASGITransport` client's `GET /health` returns 200 with `search_providers`
      equal to `["searxng"]` (spec 1's default chain) and no `brave_api_key` entry, and that
      test saves, `delattr`s and restores both attributes so it passes when run after a
      real-lifespan test in the same session.
- [x] `app.state.search_key_capabilities` is a tuple filled from the lifespan's single
      `brave_key_present()` evaluation, `None` at module scope, read through
      `_resolved_search_key_capabilities(state)`; a real-lifespan test with `brave` configured
      asserts `("brave" in search_providers) == ("brave_api_key" in capabilities)` with and
      without a key.
- [x] `DegradedReason` still has exactly two members; `status`, `degraded_reasons`,
      `promptguard_loaded`, `cache_connected` and `cache_backend` are computed exactly as
      before (existing `/health` tests pass unchanged).
- [x] `contract/openapi.yaml` + `.sha256` and `tests/golden/contract_1_2_0.json` regenerated
      under 1.2.0; `tests/test_contract_export.py`, `tests/test_contract_schema.py` and
      `tests/test_contract_smoke.py` pass with no edit to `contract_smoke.py`; the embedded
      anchor hash in the four pages matches the committed `.sha256`.
- [x] `search_providers` is named in `kit_tools/docs/MONITORING.md`, `kit_tools/docs/API_GUIDE.md`,
      `docs/configuration.md`, `README.md`, `kit_tools/arch/SECURITY.md` and
      `kit_tools/arch/SERVICE_MAP.md` (~74); `brave_api_key` is named wherever `capabilities` is
      documented (MONITORING, API_GUIDE, SECURITY, SERVICE_MAP ~150 and ~321) as one key of a
      presence map; MONITORING states configuration echo and the compare-against-configured
      procedure; `docs/configuration.md`'s break-glass section and the
      `_break_glass_arming_env_var` docstring scope the lie to `search_sanitization`;
      SECURITY's documented-non-vulnerabilities table carries the new row (all
      grep-verifiable).
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-003: Contract governance finalize + `/search`↔`/retrieve` boundary doc

**Priority:** P1

**Description:** As a maintainer, I want the 1.2.0 contract closed in the right order — the
`/search`↔`/retrieve` boundary written into the descriptions that feed the OpenAPI document
first, then one regeneration, then a sweep proving every 1.2.0 addition is golden-pinned and
that nothing unlisted was added, then the version record — so spec 5 publishes a wire that is
documented, pinned and classified.

**Independent Test:** `uv run pytest tests/test_contract_export.py tests/test_contract_schema.py
tests/test_governance_docs.py` passes, and reverting any of these turns a gate red: the
`/search` and `/retrieve` operation descriptions in `contract/openapi.yaml` each name the other
route and neither says "through SearXNG" or "via SearXNG" (new assertions in
`tests/test_contract_export.py`); every addition in the 1.2.0 list in hint (3) is present in
`tests/golden/contract_1_2_0.json` **and** the golden's key-path diff against
`contract_1_1_0.json` equals that list (a new two-half test in
`tests/test_contract_schema.py`); the anchor embedded in the four documentation pages equals
`contract/openapi.yaml.sha256` (a new test in `tests/test_governance_docs.py`). The
worked-examples table still has exactly six rows, `contract_1_0_0.json` / `contract_1_1_0.json`
are byte-identical to `main`, and the `CONTRACT_VERSION` docstring's `1.2.0` entry names each
addition (human-reviewed prose, not a gate).

**Implementation Hints:**
- Do the work in this order; steps (1)–(2) move `contract/openapi.yaml` and the golden, so a
  sweep done before them would be invalidated by them.
- **(1) Boundary text.** FastAPI takes each operation's `description` from the route handler's
  docstring: `/search` is the `search()` docstring in `retrieval_app.py` (~1549, as spec 1
  US-004 left it — that story already removed "through SearXNG"; this one replaces the
  docstring with the boundary text) and `/retrieve` is the `retrieve()` docstring (~1361).
  Write the boundary into both, and into the `SearchRequest` / `RetrieveRequest` model
  docstrings in `models.py`: `/search` finds and returns provider-*extracted* content for a
  query across sources — snippets or chunks, per result `content_kind` — from the configured
  provider chain, every result sanitized, never cached; `/retrieve` fetches and sanitizes one
  caller-named URL through the full pipeline, cached by `sanitizer_revision`. State the
  trust-policy difference a consumer needs, as observed today, with the shared knob named as
  shared: `promptguard_fail_closed` is honoured on **both** routes (`RetrieveRequest` carries it
  at `models.py:240`); `/retrieve` additionally honours `promptguard_threshold`,
  `trusted_domains`, `verified_domains`, `blocked_domains` and `cache_ttl_hours`; `/search`
  additionally honours `providers` and `allow_paid_fallback` (from contract 1.2.0) and scans
  every result at the fixed 0.85 default at trust tier `standard` (`config.yaml`
  `promptguard_threshold` is not applied on this route). This documents the divergence;
  changing it belongs to `epic-forage-hardening`. Mirror the same two sentences in
  `README.md`'s HTTP-surface rows for `POST /search` / `POST /retrieve` (~52–53),
  `kit_tools/docs/API_GUIDE.md`'s endpoint-summary row for `/search` (~58) and its
  `### POST /search` intro, and as a one-line note under `docs/configuration.md`'s endpoint
  table (~21–27). The API_GUIDE row's adjacent cells move with it: "10 s SearXNG timeout"
  becomes "10 s per provider call" and the gate "SearXNG must be reachable" becomes "at least
  one provider of the configured chain (`/health` `search_providers`) reachable", so the row
  no longer names SearXNG as *the* backend.
- **(2) Regenerate** with `uv run python -m scripts.export_contract`; `CONTRACT_VERSION` stays
  `1.2.0`. That script writes exactly three files — `contract/openapi.yaml`, its `.sha256` and
  the unregenerated twin — and never a golden. Step (1)'s `SearchRequest` docstring is that
  model's schema `description`, and `SearchRequest` is in `_SCHEMA_MODELS`, so
  `tests/golden/contract_1_2_0.json` moves too and is regenerated in place (the current
  version's fixture, never a new file). These description edits are no separate PATCH:
  GOVERNANCE's worked example 3 makes a post-freeze description fix a PATCH, but 1.2.0 has not
  been published (spec 5 cuts the image after this spec), so there is no vendored copy to
  re-vendor and the edits are subsumed by the unreleased MINOR. Refresh the anchor hash
  embedded in `kit_tools/docs/API_GUIDE.md`, `kit_tools/docs/CI_CD.md`,
  `kit_tools/docs/DEPLOYMENT.md` and `kit_tools/arch/SERVICE_MAP.md` (~202) after this, the
  last regeneration before the release cut — and make that refresh mechanical from now on: a
  test in `tests/test_governance_docs.py` reads the 64-hex anchor out of each of the four
  pages and asserts it equals `contract/openapi.yaml.sha256`. Add the assertions to
  `tests/test_contract_export.py` that the committed document's `/search` and `/retrieve`
  descriptions each name the other and that `/search`'s contains neither "through SearXNG" nor
  "via SearXNG".
- **(3) Golden coverage sweep, in two halves.** The pin is `tests/golden/contract_1_2_0.json`
  (created by spec 1 US-004 and regenerated by every wire-touching story since; GOVERNANCE
  ruling (a)) via `tests/test_contract_schema.py::_SCHEMA_MODELS`, which spec 1 US-004 extended
  with `SearchRequest` and the 422 error model. **Presence half:** enumerate the fourteen 1.2.0
  additions and assert each is present in the golden: `SearchResult.content_kind`,
  `SearchResult.date`, `SearchResult.domain`, `SearchResponse.provider_used`,
  `SearchResponse.fallback_fired`, `SearchResponse.provider_errors`, `search_unavailable` in
  the 422 error model's enum, `SearchRequest.providers`, `SearchRequest.allow_paid_fallback`,
  `HealthResponse.search_providers`, `brave_api_key` named in the `capabilities` description,
  and the three `/metrics` `search` counters `fallback_fired`, `paid_calls` and
  `policy_unknown_provider`. **Completeness half:** a key-path diff — every `properties` entry
  and every enum member, per schema and per `$defs` entry — of `contract_1_2_0.json` against
  `contract_1_1_0.json` must equal exactly the golden-visible additions in that list
  (`SearchResult.content_kind` / `date` / `domain`, `SearchResponse.provider_used` /
  `fallback_fired` / `provider_errors`, `HealthResponse.search_providers`; `RetrievedContent`
  and `ExtractedContent` add nothing). `SearchRequest` and the 422 error model have no 1.1.0
  golden entry to diff against, so the test pins their 1.2.0 shape exactly: the property set
  `{query, num_results, promptguard_fail_closed, providers, allow_paid_fallback}` and the
  nine-member `Pipeline422ErrorCode` enum (eight today plus `search_unavailable`; the
  document-wide eighteen is `ERROR_CODES`, a different gate owned by spec 1 US-004 — never widen
  `Pipeline422ErrorCode` to match that number). The three `/metrics` counters are **not** only drift-gated:
  `tests/test_contract_metrics.py::test_every_section_the_handler_emits_has_a_model` (~182–188)
  and `::test_metrics_schema_is_fully_rendered` (~272–292) already pin `SearchMetricsResponse`'s
  field set against the handler and the served document, with a mandatory description per
  field — so `MetricsResponse` is **not** added to `_SCHEMA_MODELS`; the sweep asserts
  `set(SearchMetricsResponse.model_fields)` equals the pinned 1.2.0 set exactly (1.1.0's fields
  plus the three). An addition specs 1–3 landed that the list omits fails the gate; the fix is
  to extend the list and its count, never to loosen the diff. The current version's fixture is
  regenerated, never a new file, and `contract_1_0_0.json` / `contract_1_1_0.json` are never
  edited.
- **(4) Version record.** The per-version change entry lives in the `CONTRACT_VERSION`
  docstring list in `pipeline/contract.py` (GOVERNANCE "Bumping the contract" step 2; the
  `1.0.0` / `1.1.0` lines are the format): complete the `1.2.0` line spec 1 US-004 opened so
  it names every addition in hint (3), says all are additive so a consumer comparing MAJOR
  keeps working, and notes that the description edits landed inside the unpublished window.
  Verify `contract/GOVERNANCE.md`'s "The current contract version is **1.2.0**" sentence (set
  by spec 1 US-004; `tests/test_governance_docs.py` checks it against `contract.py`). **Do
  not** add a row to the six-worked-examples table (`test_there_are_exactly_six` pins it) or a
  new ruling section (`_RULING_MARKERS` pins those); the docstring entry is the record. The
  `search_unavailable` announcement is mechanical — ruling 20a has spec 5 US-004 append this
  docstring entry to the Release body — so nothing is drafted here. `contract.py` is in
  `_REVISION_SOURCES`, so this edit rotates `sanitizer_revision` (Technical Considerations).
- Not this story: `SearchMetricsResponse.errors`' description and the "seventeen" test names
  (spec 1 US-004 owns them; the sweep only confirms `search_unavailable` is named there).

**Acceptance Criteria:**
- [ ] The `/search` and `/retrieve` operation descriptions in `contract/openapi.yaml` state
      the boundary and the per-endpoint policy knobs as written in hint (1), name
      `promptguard_fail_closed` as shared by both routes, neither names SearXNG as *the*
      backend, and each names the other route; tests in `tests/test_contract_export.py` assert
      the mutual naming and that `/search`'s description contains neither "through SearXNG" nor
      "via SearXNG".
- [ ] `README.md`, `kit_tools/docs/API_GUIDE.md` and `docs/configuration.md` carry the same
      boundary statement in the places named in hint (1) (grep-verifiable: each mentions
      `content_kind` beside `/search` and "one URL" beside `/retrieve`); the API_GUIDE `/search`
      summary row's limits and gate cells no longer contain "SearXNG must be reachable".
- [ ] `contract/openapi.yaml` + `.sha256` regenerated and `tests/golden/contract_1_2_0.json`
      regenerated in place; `CONTRACT_VERSION` is `1.2.0` and no `1.2.1` fixture or version
      exists; the anchor hash in the four documentation pages matches the committed `.sha256`
      and the new test in `tests/test_governance_docs.py` asserts it;
      `tests/test_contract_export.py` passes.
- [ ] A test in `tests/test_contract_schema.py` asserts both halves of hint (3): each of the
      fourteen additions is present, and the key-path diff of `contract_1_2_0.json` against
      `contract_1_1_0.json` (plus the exact pins for `SearchRequest`, the 422 enum and
      `SearchMetricsResponse.model_fields`) equals the enumerated set, so an unlisted addition
      fails; `MetricsResponse` is not in `_SCHEMA_MODELS`; `contract_1_0_0.json` and
      `contract_1_1_0.json` are byte-identical to `main`.
- [ ] The `CONTRACT_VERSION` docstring's `1.2.0` entry names every addition in hint (3);
      `contract/GOVERNANCE.md`'s current-version sentence reads 1.2.0; the worked-examples
      table has exactly six rows and the five ruling sections are untouched;
      `tests/test_governance_docs.py` passes in full.
- [ ] The `sanitizer_revision` rotation (before, after, cause: the `CONTRACT_VERSION` docstring
      entry in `contract.py`) is recorded in `docs/bootstrap-notes.md`, the three `kit_tools/`
      rotation tables (GOTCHAS, DECISIONS, CODE_ARCH) and `CLAUDE.md`'s "Coexistence with
      Poppy" paragraph.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

## Edge Cases

- A `providers` entry matches no provider in the resolved chain — unknown (`tavily`),
  misspelled, malformed (a 33-character token, interior whitespace, a hostile string), past the
  eighth entry, or configured-but-skipped for want of a key (`brave` on a key-less
  deployment): one rule. The entry is ignored and `search.policy_unknown_provider` rises by
  one per ignored entry, duplicates included; the effective chain is the configured chain minus
  the paid providers not named; status code and response field set are byte-identical across
  every cause. Ignoring an entry never by itself produces a 422 — a 422 arises only when the
  effective chain ends up empty (below). US-001.
- Case and surrounding whitespace never matter: `[" SearXNG "]` normalises to `searxng` and
  matches; nothing is counted. US-001.
- `providers: ["brave"]` on configured chain `["searxng", "brave"]` → effective chain
  `["searxng", "brave"]` in configured order: free providers are never excluded, so Brave still
  runs only as spec 3's fallback. The same body on a key-less deployment → `["searxng"]`, one
  counter increment, the same response shape. US-001.
- `providers: ["tavily"]` on `["searxng", "brave"]` → every entry was ignored, so every paid
  provider is removed and the free providers remain → `["searxng"]`. US-001.
- `providers` naming no registered provider on a paid-only configured chain
  (`FORAGE_SEARCH_PROVIDERS=brave` with the key present; `providers: ["tavily"]` or
  `["searxng"]`, `allow_paid_fallback` at its default) → every paid provider removed, no free
  provider to keep → empty effective chain → 422 `search_unavailable` with `reason`
  `policy_excluded_all_providers`, no provider called, the counter up by one. US-001.
- `allow_paid_fallback: false` on a paid-only configured chain → empty effective chain → 422
  `search_unavailable` with `reason` `policy_excluded_all_providers`, no provider called, never
  200 with an empty list. An empty effective chain arises only from a configured chain with no
  free provider — which `/health` `search_providers: ["brave"]` already publishes — by either
  of these two routes. US-001.
- `allow_paid_fallback: false` on an operator-configured paid-first chain (`brave,searxng`) →
  effective chain `["searxng"]`: the flag removes paid providers, never a free one, and the
  free provider keeps its configured position. US-001.
- A request restricted to `searxng` on configured chain `["searxng", "brave"]` whose SearXNG
  call fails → the exhaustion code follows the *configured* chain (ruling 28): 422
  `search_unavailable`, reason `"searxng: <failure_class>"`, never `searxng_error`. The legacy
  `searxng_*` codes apply only when the configured chain is exactly `["searxng"]`, so no status
  varies with policy. US-001.
- `providers` is not a list, or an item is not a string → pydantic's type 422, the same as
  every other field today; no string value of any length or shape is ever rejected. US-001.
- Key present but `brave` not in `FORAGE_SEARCH_PROVIDERS` → `search_providers: ["searxng"]`
  and `capabilities.brave_api_key: 1`: the two fields together show "keyed but not chained".
  US-002.
- `brave` in `FORAGE_SEARCH_PROVIDERS` but no key → `search_providers: ["searxng"]` and no
  `brave_api_key` entry, indistinguishable on `/health` from a never-configured free
  deployment by design (ruling 15); spec 2 US-002's WARNING skip log is the operator's signal.
  US-002.
- `FORAGE_BRAVE_API_KEY=` (empty, as a compose passthrough renders it), a whitespace-only
  value, or a value spec 2 refuses as `brave_key_invalid` → `brave_key_present()` is false, the
  provider is not registered and `/health` shows no `brave_api_key` entry: advertisement and
  registration come from one evaluation and cannot disagree. US-002.
- Key present with PromptGuard unloaded → `capabilities == {"brave_api_key": 1}`; break-glass
  armed with no key → `{"search_sanitization": 1}`: the two keys are computed independently
  and the override touches only `search_sanitization`. US-002.
- `GET /health` on a transport that never ran the lifespan (the suite's six `ASGITransport`
  clients; production-unreachable, since the lifespan publishes both values before it yields)
  → 200 with `search_providers: ["searxng"]` (spec 1's default chain), no `brave_api_key`
  entry, `status` and `degraded_reasons` computed as today, no new `DegradedReason`; the test
  restores whatever a real-lifespan test published. US-002.
- The completeness half of US-003's sweep reports a key path the list omits → a wire addition
  specs 1–3 landed without a record: extend the enumerated list and its count, never loosen
  the diff; never create a `1_2_1` fixture and never edit `contract_1_0_0.json` /
  `contract_1_1_0.json`. US-003.

## Out of Scope

- Everything inside Poppy: the settings UI, badge, vault→env key plumbing, and the global
  paid-fallback budget breaker (`epic-search-policy`).
- A spend ceiling or per-process paid-call cap (ruling 12: v2, accepted known risk here).
- A provider liveness probe on `/health`: `search_providers` is configuration echo; SearXNG
  failures stay a per-request 422, and the stale "degrades without SearXNG" claim is struck by
  spec 5 US-001.
- `search.fallback_fired` / `search.paid_calls` on `/metrics` (spec 3 US-003, ruling 21);
  `SearchMetricsResponse.errors`' description and the "seventeen" test names (spec 1 US-004).
- Release plumbing — the mechanical Release-body announcement, `contract_smoke.py` flags,
  compose pins, secret-grep patterns (spec 5 US-004, ruling 20) — and third-party env-contract
  docs (spec 5 US-001).
- Changing `/search`'s fixed 0.85 threshold or `standard` trust tier: US-003 documents the
  divergence; `epic-forage-hardening` owns changing it.
- Suite-count bookkeeping in TESTING_GUIDE, AGENT_README and SYNOPSIS (spec 5 US-003, ruling
  32); US-001 adds only the `test_mapping` row for the new module.

## Assumptions

- Providers declare `name: str` and `paid: bool` (ruling 13; spec 1 US-001), the chain is
  resolved once in the lifespan with key-gated registration (spec 1 US-003, spec 2 US-002),
  and the `/search` handler passes it to `run_search_pipeline` (ruling 22), so policy has a
  chain to filter and a `paid` flag to read without any new provider attribute.
- `app.state.search_providers` holds `list[SearchProvider]`, is `None` at module scope, and
  `_resolved_search_providers(state)` falls back to spec 1's default one-element chain
  without reading the environment (ruling 28); `run_search_pipeline` detects "not supplied"
  with `providers is None`, never a falsy check (ruling 29).
- Spec 2 US-002 resolves `FORAGE_BRAVE_API_KEY` in the lifespan unconditionally — before and
  independently of whether `brave` appears in `FORAGE_SEARCH_PROVIDERS` — through the shared
  `brave_key_present()` helper; that is what makes "keyed but not chained" observable.
- Spec 3's traversal applies its rules to whatever chain it is handed; handing it the
  effective chain changes no traversal code, and its exhaustion-code predicate reads the
  configured chain (ruling 28).
- Spec 1 US-004 created `tests/golden/contract_1_2_0.json` and extended `_SCHEMA_MODELS` with
  `SearchRequest` and the 422 error model; spec 3 US-003 added the two `search.*` counters.
- `contract_smoke.py` derives its expected `/health` field set from
  `HealthResponse.model_fields` (verified at ~261), so it needs no edit for US-002.

## Technical Considerations

- **Threat model, stated once.** Forage has no authentication (`kit_tools/arch/SECURITY.md`,
  "Authentication and Authorization"): the party sending policy params is anyone with network
  reach to port 8020, plus a buggy or compromised consumer — not the operator. The standing
  rule every request field inherits: per-request policy may only narrow what the deployment's
  configuration already permits. Ruling 29 makes the invariant simple enough to test directly:
  the effective chain is always the configured chain minus a subset of its `paid=True`
  providers. Free providers are never removed, so the free path a request takes is the free
  path the configured chain takes, and the paid step can only be absent — never earlier, never
  added, never keyed. Hence no request can cause a paid call the configured chain would not
  have made from the same free-path outcome, on any chain, with any number of free providers,
  and spec 3's cost-exhaustion guard holds under every policy value. There is **no spend
  ceiling** in this epic (ruling 12: accepted known risk; `/metrics` `search.paid_calls` from
  ruling 21 is the observability floor) and no retries (ruling 18); reviewers do not re-raise
  either.
- **Key presence is public by design; the "no oracle" rule is about a second channel.**
  `/health` is unauthenticated like every route and publishes `search_providers` and the
  `brave_api_key` capability entry deliberately (ruling 15); `search_providers` alone already
  implies key presence, since spec 2 registers `brave` only when the key is present. What the
  rule forbids is a *differential* channel on `/search`: for the same request body, the status
  code and the response field set never vary with key presence — which is why unknown and
  key-absent entries are handled identically, why the only signal is an operator-facing counter
  that reports what `/health` already shows, why the exhaustion code follows the configured
  chain rather than the effective one, and why an empty effective chain can only arise from a
  configured chain `/health` already shows to have no free provider. A timing difference
  between an ignored entry and a rate-limited provider is out of scope: there is nothing left
  for it to reveal.
- **Malformed input is ignored, never rejected.** `providers` items carry no pydantic
  constraint because a FastAPI 422 echoes the offending value verbatim under `detail[].input`
  — an unbounded reflector of caller text on a service whose contract is that every returned
  string was sanitized. Normalising and ignoring in the policy function closes that path;
  the only 422 the field can produce is pydantic's type error, shared with every field today.
- **Caller strings never reach the wire or the logs.** Nothing sent through `providers` reaches
  `provider_errors`, a 422 `reason`, a `/metrics` key or a log record; those carry registered
  names only, and ignored entries are counted, not stored. The `searxng_unavailable` reason's
  `SEARXNG_URL` and exception-text passthrough on the `searxng`-only configured chain is the
  documented exception (`ERROR_HANDLING.md`); it echoes configuration, not caller input, and
  stays as it is.
- **Version skew fails open, and that is accepted.** `models.py` declares no `model_config`, so
  Pydantic's default `extra="ignore"` applies: an image older than contract 1.2.0 drops
  `providers` and `allow_paid_fallback` silently — additive semantics — and a consumer sending
  them to keep a query away from a paid third party gets the configured behaviour instead, with
  a 200 indistinguishable from the honoured case. The field descriptions and the boundary text
  say "honoured from contract 1.2.0"; a consumer gates on `contract_version` and that gate is
  the mitigation. `extra="forbid"` is not the fix — an accepted request value that stops being
  accepted is MAJOR under GOVERNANCE.
- **`capabilities` mixes environment facts with runtime claims, by design.**
  `search_sanitization` is a runtime claim the break-glass override can force for a transition
  window (SECURITY's documented non-vulnerability); `brave_api_key` is an environment fact
  from one `brave_key_present()` evaluation that no override touches. The rewritten field
  description says which is which, and the two entries are computed independently.
- **Two `sanitizer_revision` rotations**, both expected: US-001 edits `pipeline/contract.py`
  (the policy literal; the empty-chain raise lives in `retrieval_app.py`, which is not hashed),
  and US-003 edits the `CONTRACT_VERSION` docstring in `pipeline/contract.py`; all are in
  `_REVISION_SOURCES`, while `models.py` and `retrieval_app.py` are not. Each rotation
  cold-starts every `/retrieve` cache entry (the revision is a cache-key input since
  `forage-cache-fallback` US-003). Note for US-002: `tests/test_contract_smoke.py::_health_body`
  (lines 67–86) indexes `HealthResponse` fields by name and needs the new `search_providers` key,
  or it fails with a bare `KeyError`. Record each rotation the way `kit_tools/docs/GOTCHAS.md`
  ("sanitizer_revision has deliberately diverged") prescribes: the three `kit_tools/` rotation
  tables (`kit_tools/docs/GOTCHAS.md` ~403–414, `kit_tools/arch/DECISIONS.md` ~605–613,
  `kit_tools/arch/CODE_ARCH.md` ~131–135), `CLAUDE.md`'s "Coexistence with Poppy" paragraph,
  and `docs/bootstrap-notes.md`'s pin record (ruling 32) — and sweep the prose rotation counts
  in the same change (`MONITORING.md` ~94, `DEPLOYMENT.md` ~121, `TROUBLESHOOTING.md` ~610,
  `SERVICE_MAP.md` ~195, `GOTCHAS.md` ~403 and ~417).
- **The 1.2.0 wire is frozen after US-003.** Spec 5 regenerates nothing; it publishes.

## Related Documentation

- `kit_tools/specs/epic-search-providers.md` — rulings 12, 13, 15, 16, 20–22, 26, 28, 29, 32
  and the resolution map rows for spec 4; Poppy's `EPIC3_SEARCH_RELIABILITY_SPLIT.md`
  (F7/F8/F9/F11).
- `contract/GOVERNANCE.md` (classification table, "Bumping the contract" steps 2 and 6, ruling
  (a)); `pipeline/contract.py` (`CONTRACT_VERSION` docstring list, `DegradedReason`,
  `POLICY_EXCLUDED_ALL_PROVIDERS`).
- `tests/test_contract_schema.py` (`_SCHEMA_MODELS`, golden), `tests/test_contract_export.py`
  (drift), `tests/test_governance_docs.py` (`test_there_are_exactly_six`, `_RULING_MARKERS`,
  the anchor guard), `tests/test_contract_metrics.py` (`/metrics` key-set and key-order
  parity), `tests/test_models.py::TestSearchRequest`, `tests/test_app.py` (~1504–1517
  save/restore idiom, 1528 and 1554 drift guards), `contract_smoke.py`.
- `retrieval_app.py` — `_resolved_cache_backend` / `_resolved_sanitizer_revision` (~198–221),
  `HealthResponse.capabilities` (~264–275), the module-scope `app.state` block (~1182–1200),
  `SearchMetrics` / `SearchMetricsResponse`, the `/search` handler, the capability constants
  (~236–241).
- `kit_tools/docs/MONITORING.md`, `kit_tools/docs/API_GUIDE.md`, `docs/configuration.md`,
  `README.md`, `kit_tools/arch/SECURITY.md`, `kit_tools/arch/SERVICE_MAP.md` (74, 150, 202,
  321), `kit_tools/arch/DECISIONS.md` (rotation table), `kit_tools/testing/TESTING_GUIDE.md`
  (`test_mapping`), `kit_tools/docs/GOTCHAS.md` (rotation record; the root-logger-at-WARNING
  trap that is why the unknown-name signal is a counter, not a log).

## Implementation Notes

### US-010 — Per-request policy: wire fields, `apply_request_policy`, handler wiring, counter, policy 422, contract regeneration (2026-09-16)

`SearchRequest` gained `providers: list[str] = Field(default_factory=list)` and
`allow_paid_fallback: bool = Field(default=True)` (`models.py` ~258–290), neither carrying any
pydantic constraint. `apply_request_policy(chain, request)`
(`pipeline/search_providers/policy.py`, new file) implements ruling 29's normalise → match →
filter → allow_paid_fallback algorithm as a pure function. Its second parameter is typed
`RequestPolicy`, a local `Protocol` declaring only `providers: list[str]` and
`allow_paid_fallback: bool` — **not** `models.SearchRequest` — because
`tests/test_search_providers.py::test_no_search_provider_module_imports_a_sanitization_stage_or_the_cache`
mechanically sweeps every module under `pipeline/search_providers/` for a `models` import
(ruling 11) and fails on it even under `TYPE_CHECKING`, since the sweep is a plain AST walk of
import statements. `models.SearchRequest` satisfies the protocol structurally, the same seam
shape `SearchProvider` itself already uses (`SearchMetricsSink` in `orchestrator.py` is the
precedent for a consumer-side Protocol instead of an import).

The `/search` handler (`retrieval_app.py`) now resolves the configured chain, applies the
policy, folds the ignored count into `SearchMetrics.policy_unknown_provider`, and — only when
the effective chain is empty — raises `PipelineError(error="search_unavailable",
reason=POLICY_EXCLUDED_ALL_PROVIDERS)` itself with a fresh `request_id`, before
`run_search_pipeline` is ever called, recording the error the same way the existing `except
PipelineError` arm does. Otherwise it calls `run_search_pipeline` with `providers=` the
effective chain and `configured_chain=` the full resolved chain, so spec 3's exhaustion-code
predicate (ruling 28) keeps reading the configured shape regardless of any per-request
narrowing. `POLICY_EXCLUDED_ALL_PROVIDERS = "policy_excluded_all_providers"` lives in
`pipeline/contract.py` beside `METRICS_OTHER_BUCKET`. `policy_unknown_provider: int` was added
to `SearchMetricsResponse` and the `/metrics` handler's dict in the same position (end of the
`search` section, after `paid_calls`) in the same edit.

No existing reason-format test needed widening: the two `search_unavailable` shapes never
overlap in one code path — `pipeline/orchestrator.py`'s chain-order list is built and returned
entirely inside `run_search_pipeline`, which the handler's policy branch never reaches (it
raises first). `tests/test_orchestrator.py::test_search_unavailable_reason_is_chain_order_provider_errors`
and `tests/test_search_providers.py`'s reason-format test both still exercise `run_search_pipeline`
directly and are unaffected.

**`sanitizer_revision` rotation (US-011 to record):** before `f0b93318ecb03e6348481a42341a8a8b66f95b3ca601f9f60de3d6437cb70d62`,
after `dc3ff92a876885e8a8c1d9b0c5601818a6e4ccc208a87ab01f776482bf4eded9`. Cause: `pipeline/contract.py`
gained `POLICY_EXCLUDED_ALL_PROVIDERS`. `orchestrator.py` — every other `_REVISION_SOURCES`
member — is byte-identical (measured: `git diff --stat HEAD` against all eight hashed files
shows only `contract.py` changed); `retrieval_app.py`, where the policy raise site actually
lives, is not a hashed file. Both values were independently confirmed by hashing `git
show HEAD:pipeline/contract.py` (before) and the working-tree file (after) through the same
algorithm as `derive_sanitizer_revision`, and by calling `derive_sanitizer_revision({})`
directly on the finished tree.

Contract regenerated (`uv run python -m scripts.export_contract`); still `1.2.0`. Golden
`tests/golden/contract_1_2_0.json` updated in place (additive diff: `providers` and
`allow_paid_fallback` under `SearchRequest`). The anchor hash embedded in the four docs
(`kit_tools/docs/API_GUIDE.md`, `kit_tools/docs/CI_CD.md`, `kit_tools/docs/DEPLOYMENT.md`,
`kit_tools/arch/SERVICE_MAP.md`) was deliberately left untouched here, per this story's
Implementation Hints — that refresh, the doc rows, and the rotation-table entries are US-011's.

### US-012 — Finish per-request policy: widen the reason-format test, close US-010's verifier items (2026-09-16)

US-010's third attempt implemented the per-request policy completely and landed at `8f7dd0f`
via supervisor fast-forward from the reflog (all four gates green, 2028 tests) after failing
verification on two narrow items. This story made those two fixes plus the recommended
hardening, with no reimplementation.

**Fix 1 (widened reason-format test).** Added
`_is_valid_search_unavailable_reason(reason, chain_names)` to `tests/test_orchestrator.py`,
accepting exactly the two documented shapes: the fixed `contract.POLICY_EXCLUDED_ALL_PROVIDERS`
literal, or a `"; "`-joined list of `"<chain name>: <FAILURE_CLASSES member>"` entries.
`test_search_unavailable_reason_is_chain_order_provider_errors` keeps every existing assertion
and gained one more calling this helper. Three sibling tests were added beside it:
`test_search_unavailable_reason_accepts_the_policy_literal` (form 2 directly),
`test_search_unavailable_reason_rejects_a_third_form` (four malformed shapes, all rejected),
and `test_policy_literal_reaches_post_search_on_a_paid_only_chain`, which drives form 2 through
a real `POST /search` — a single-provider paid-only chain (`FakeSearchProvider(name="brave",
paid=True)` monkeypatched onto `app.state.search_providers`, the same restore-via-`monkeypatch`
idiom already used at `tests/test_orchestrator.py`'s cache-rotation test) with
`allow_paid_fallback: false`, asserting the 422, the reason, and `brave.calls == []`.

**Fix 2 (stale docstring).** `tests/test_brave_provider.py::test_brave_module_never_raises_a_pipeline_error`
claimed the `search_unavailable` raise site "lives in `orchestrator.py` alone" — no longer true
since US-010 added the policy raise site in `retrieval_app.py`. Reworded to name both raise
sites; the test body (asserting `brave.py` never contains `PipelineError`) was already correct
and untouched.

**Recommended items, all taken.** (1) `allow_paid_fallback`'s description gained one clause —
"Applied after `providers`' own normalise-then-ignore-and-count filtering (ruling 29)." —
followed by `uv run python -m scripts.export_contract` and an in-place regeneration of
`tests/golden/contract_1_2_0.json` (still `1.2.0`; documentation-only, per
`contract/GOVERNANCE.md`). (2) `test_providers_naming_an_unknown_provider_leaves_only_searxng`
(`tests/test_app.py`) was given a failing SearXNG (`rate_limited`/`http_429`) instead of a
succeeding one — a succeeding SearXNG proves nothing, since free-first traversal never reaches
Brave either way; forcing exhaustion is what makes `brave.calls == []` actually demonstrate the
policy removed it. (3) `test_legacy_codes_are_byte_for_byte_on_a_searxng_only_configured_chain`
now also asserts the exact legacy reason bytes (`"SearXNG returned HTTP error (http_500)"`).

None of the changed files (`models.py`, three test files, `contract/openapi.yaml` +
`.sha256`, the golden fixture) are `pipeline/sanitizer_revision.py`'s `_REVISION_SOURCES`
members, so no rotation occurred and no rotation-table entry is needed. Doc rows, the four
anchor refreshes, and the rotation record remain US-011's, untouched here. Gates: 2031 tests
(2028 + 3 new), `ruff check .`, `ruff format --check .`, and strict `pyright` all green.

### US-011 — Doc rows, contract anchor refresh and `sanitizer_revision` rotation record (2026-09-16)

Verified rather than trusted US-010's recorded values before writing anything:
`derive_sanitizer_revision({})` on this branch returns
`dc3ff92a876885e8a8c1d9b0c5601818a6e4ccc208a87ab01f776482bf4eded9`, matching the "after"
value US-010 recorded; `contract/openapi.yaml.sha256` is
`e6be668f51eeaa80ba0830727e05bcfa5c87ef9283e9625210514b44bc7cdfdc`. Both were stale in the
docs (the anchor was still the pre-US-002 value `aa5e94058b8d05de7e45c96145886928a2d31aa755e18c2c81e5ef486bf0cee8`).

**Anchor refresh.** Replaced the stale anchor in all four named sites
(`kit_tools/docs/API_GUIDE.md`, `kit_tools/docs/CI_CD.md`, `kit_tools/docs/DEPLOYMENT.md`,
`kit_tools/arch/SERVICE_MAP.md`) with the current committed hash — a single literal string,
found by grepping for the old hex value rather than trusting line numbers.

**Doc rows.** API_GUIDE's `SearchRequest` table gained `providers` and
`allow_paid_fallback` rows plus a sentence that a consumer sources selectable provider
names from `/health` `search_providers` and reconciles there. MONITORING's `/metrics`
`search` table gained `policy_unknown_provider` (unit: one per ignored entry) with the
disambiguation procedure: compare the consumer's `providers` names against `/health`
`search_providers`; for a missing `brave`, `/health` `capabilities` decides — no
`brave_api_key` entry is a key problem (absent or invalid), `brave_api_key: 1` with no
`brave` in `search_providers` is keyed-but-not-chained (`FORAGE_SEARCH_PROVIDERS` leaves it
out); any other missing name is a bad name; entries past the eighth count whatever they
name. Both `/health` fields land in US-002, so the row says so rather than implying they
exist today. SECURITY's "Request models" paragraph no longer claims pydantic bounds every
request field, and gained the reason `providers` carries no pydantic bound (a validation
422 echoes the caller's bytes under `detail[].input`) and where the bound lives (first
eight, matched or ignored, in `apply_request_policy`). TROUBLESHOOTING's, ERROR_HANDLING's
and API_GUIDE's `search_unavailable` rows each name `policy_excluded_all_providers` as the
second reason form (policy-excluded, not provider failure); ERROR_HANDLING gained its own
row because the raise site differs (`retrieval_app.search`, before `run_search_pipeline`
is called). TESTING_GUIDE's `test_mapping` gained `pipeline/search_providers/policy.py` →
`tests/test_search_policy.py`; suite counts left alone (ruling 32).

**Attempt 2 (after a verifier FAIL on the MONITORING procedure).** Attempt 1's row swapped
the criterion's `brave_api_key` signal for "no `brave` in `search_providers`", which
misdiagnoses a keyed-but-unchained deployment as a missing key; rewritten as above. The
same pass fixed stale claims sitting in the tables this story edits, none of them code:
MONITORING's `paid_calls` row said a per-request policy "can reorder" the chain (ruling 29:
it only removes paid providers, so paid-first is a configured-chain property); the
"both counters" caveat now names `fallback_fired` and `paid_calls` and says
`policy_unknown_provider` moves on a `searxng`-only deployment; MONITORING's `errors` row
and API_GUIDE's route-summary row and `/search` failure prose each gained the policy
form; the thirteenth rotation record in `docs/bootstrap-notes.md` no longer says "only
files under `pipeline/` are hashed" (eight named files are). Re-measured, not copied:
reverting `contract.py` alone to `8f7dd0f^` reproduces `f0b93318…`, reverting
`orchestrator.py` alone leaves `dc3ff92a…`.

**Rotation record.** Recorded in `docs/bootstrap-notes.md` (new "thirteenth rotation"
section plus the summary table's "Current" marker moved), `kit_tools/arch/DECISIONS.md`
(new table row, "(current)" marker moved), and `CLAUDE.md`'s "Coexistence with Poppy"
paragraph (new clause). `kit_tools/docs/GOTCHAS.md` and `kit_tools/arch/CODE_ARCH.md`'s
rotation tables/prose had fallen three rotations behind (missing all of `search-fallback`
US-001/US-002/US-003 — they still stopped at `search-provider-abstraction` US-004's
`b7871b20…`, saying "moved nine times" and "a ninth"), a gap that predates this story. Left
uncorrected in place it would have made this story's own addition either wrong (labeling
`dc3ff92a…` "a tenth" when other docs call it the thirteenth) or silently incomplete
(appending without updating the count), so both were backfilled with the three missing
`search-fallback` entries plus this story's own, bringing every rotation-history doc back
into agreement at "thirteen" / "a thirteenth". No code changed; this is a documentation
gap closed while touching the same paragraphs the story already required editing, not a
new investigation.

No pydantic, handler, or contract code changed — the `contract/openapi.yaml` /
`.sha256` / golden fixture were already correct from US-010/US-012 and are untouched.
Full suite, `ruff check .`, `ruff format --check .`, and strict `pyright` all green.

### US-002 — `/health` provider status (2026-09-16)

`HealthResponse` gained `search_providers: list[str]` (required, description covering the
key-gated-skip and configuration-echo-not-liveness points) and a rewritten `capabilities`
description naming both `search_sanitization` (1.1.0, runtime claim, break-glass-forceable)
and the new `brave_api_key` (1.2.0, environment fact, break-glass-immune). `retrieval_app.py`
gained `CAPABILITY_BRAVE_API_KEY = "brave_api_key"` beside `CAPABILITY_SEARCH_SANITIZATION`,
with the introducing comment block rewritten for two keys.

**State seam.** The lifespan now evaluates `_resolve_brave_key()` once into a local `brave_key`
and derives `app.state.search_key_capabilities: tuple[str, ...]` — `(CAPABILITY_BRAVE_API_KEY,)`
when `brave_key is not None`, `()` otherwise — from that same value, alongside building
`search_providers` with it. Since `_resolve_brave_key()` already returns `None` for absent,
blank, and `brave_key_invalid` values (via `brave_key_present()`), this is the "one evaluation"
the spec requires: advertisement and registration can never disagree. Declared `None` at module
scope beside `app.state.search_providers = None`; read through a new
`_resolved_search_key_capabilities(state)`, modelled on `_resolved_cache_backend` /
`_resolved_sanitizer_revision`, returning `()` for `None` and never reading `os.environ`. The
`/health` handler builds `search_providers` as `[p.name for p in
_resolved_search_providers(state)]` (no separate name list) and folds
`_resolved_search_key_capabilities(state)` into `capabilities` as a second, independent loop
after the existing sanitization entry.

**No rotation.** `retrieval_app.py` and `models.py` are not `pipeline/sanitizer_revision.py`
`_REVISION_SOURCES` members, and this story touched no hashed file — `derive_sanitizer_revision({})`
was re-measured before and after and stayed `dc3ff92a876885e8a8c1d9b0c5601818a6e4ccc208a87ab01f776482bf4eded9`
throughout (US-011's recorded value). No rotation-table edit was made anywhere.

**Contract.** Regenerated via `uv run python -m scripts.export_contract`; still `1.2.0`,
purely additive (`search_providers` added to `HealthResponse` and its `required` list, the
`capabilities` description text widened). `tests/golden/contract_1_2_0.json` regenerated in
place by re-dumping `model_json_schema()` for `_SCHEMA_MODELS`. New anchor
`10e6cfc65abf5a56c342b8952630f5b270198e29a058001b031b1158e5d602e8`, replacing the stale
`e6be668f51eeaa80ba0830727e05bcfa5c87ef9283e9625210514b44bc7cdfdc` in all four anchor-embedding
docs (found by grepping the old hex string, not by trusting the hints' line numbers, which were
again off by 10-15 lines throughout this story). `tests/test_contract_smoke.py::_health_body`
gained a `search_providers` key so its fixture bodies keep validating against the now-required
field; `contract_smoke.py` itself needed no edit, as the spec predicted.

**Tests.** `tests/test_app.py::_borrowed_search_providers` was extended to save/restore
`app.state.search_key_capabilities` alongside `app.state.search_providers`, since the lifespan
always publishes the two together and the existing brave-lifespan tests already rely on this
helper to avoid leaking chain state across the shared `app` singleton — without this, the new
capability tuple would leak across tests the same way an unrestored chain would. Twelve new
tests cover every Independent Test case (key present and chained; key absent; empty/whitespace/
control-character-invalid key shapes; keyed-but-not-chained; key present with PromptGuard
unloaded; break-glass armed with no key; the key value never appearing in the body or logs; the
chain-membership/capability pairing invariant with and without a key; the lifespan-free
`ASGITransport` fallback via the existing client fixture's save/`delattr`/restore idiom; and
that `status`/`degraded_reasons`/`promptguard_loaded`/`cache_connected`/`cache_backend` are
unaffected). All built on the existing `_running_app()` + `_borrowed_search_providers(None)` +
`_park_the_retry` real-lifespan idiom already used by the `feature-brave-provider` US-002
lifespan tests, so PromptGuard's natural unloaded-by-default state served the "PromptGuard
unloaded" case with no extra mocking.

**Docs.** `kit_tools/docs/MONITORING.md` (field table row + rewritten `capabilities` row,
schema listing, stock-image sentence, SearXNG-failures row wording, and the
`policy_unknown_provider` disambiguation clause's forward-looking "(gains ... in US-002)"
parenthetical resolved to present tense now that the fields exist), `kit_tools/docs/API_GUIDE.md`
(`GET /health` field table), `docs/configuration.md` (`search_providers`-only "Verifying a
running instance" row per the hint, and the break-glass "Only `capabilities` lies" line scoped
to `capabilities.search_sanitization`), `README.md` (`GET /health` field list),
`kit_tools/arch/SECURITY.md` (unauthenticated-disclosure sentence gained the search-chain/
paid-key clause, a new "Documented non-vulnerabilities" row, and the Honest Degradation field
list), `kit_tools/arch/SERVICE_MAP.md` (Health Check row, and the two `capabilities: {}`
statements reworded as a presence map that may also carry `brave_api_key` per ruling 32). The
`_break_glass_arming_env_var` docstring's "Only `capabilities` lies" was scoped to
`capabilities['search_sanitization']` to match.

Gates: 2043 tests (2031 + 12 new), `ruff check .`, `ruff format --check .`, and strict `pyright`
all green.

## Refinement Notes

Policy-not-keys is the invariant that lets Poppy build a settings UI safely, and restrict-only
is what keeps that invariant cost-monotonic: caller-chosen ordering was dropped because a
caller who controls the order can put the paid provider first and bypass spec 3's guard on
every call. Round 2 simplified the filter further (ruling 29): the earlier restoration step
guaranteed only that one free provider survived, which left a chain with two free providers
exposed; "a request may exclude paid providers only" is one sentence, holds on
any chain, and is what the property test now asserts. The same round removed the pydantic
pattern and length bounds from `providers`, because a validation 422 reflects the caller's
bytes — the shape rule moved into the policy function as normalise-then-ignore. The
unknown-name signal is a `/metrics` counter rather than a log line (the root logger sits at
WARNING and nothing calls `basicConfig()`, per GOTCHAS) or a response field (a new wire field
carrying a caller-controlled name). US-003 stays one story despite touching three artifact sets
because its parts are order-dependent — the boundary text moves the OpenAPI document and the
golden, so the sweep must follow it, and the version record follows the sweep — and its hints
number the steps so the dependency is explicit.

## Clarifications

_None outstanding._

## Open Questions

_None (session_ready). The policy param set is fixed by ruling 16, simplified by ruling 29, and
does not tighten further._
