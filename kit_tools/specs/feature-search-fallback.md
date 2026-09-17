<!-- Template Version: 2.5.0 -->
---
feature: search-fallback
status: active
session_ready: true
depends_on: [brave-provider]
vision_ref: "T2.1 — Search-provider abstraction & reliable search"
type: epic-child
size: L
epic: search-providers
epic_seq: 3
epic_final: false
execution_order: [US-001, US-003, US-002, US-004]
created: 2026-09-14
updated: 2026-09-14
---

# Feature Spec: Free-First → Paid-on-Failure Fallback

> **Epic 3 of `epic-search-providers`.** Make `run_search_pipeline` traverse the provider chain
> free-first and fall back to the paid backend **only when the free path actually fails** — a
> `ProviderFailure`, or SearXNG's 200-with-zero-results-plus-`unresponsive_engines` shape —
> never on a clean zero-result, with no in-request retries and with fail-loud telemetry on the
> response and on `/metrics`. Then prove every provider's results (snippet or chunk) get
> identical sanitization.
> Context: Poppy's `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (rows F4, F5, F6); epic rulings 8, 11, 12,
> 13, 14, 17, 18, 21, 22 and round-2 rulings 23, 25, 26, 27, 28 and 32 are binding here.

## Overview

Spec 1 gave `run_search_pipeline` a chain it uses as `chain[0]`; spec 2 gave the chain a paid
provider. This spec makes the chain *traverse*: call providers in configured order, advance on a
classified failure, stop at the first provider that succeeds, and refuse with a 422 when every
provider has failed — never a 200 with an empty list because providers failed (ruling 8).

The load-bearing decision is what counts as a failure, and it is written against the failure the
epic exists to fix. A rate-limited SearXNG does **not** raise: it answers HTTP 200 with
`results: []` and the engine names in `unresponsive_engines` (`kit_tools/docs/GOTCHAS.md`,
"SearXNG :latest rots" — "web search is dead with no error anywhere"). Ruling 17 therefore
classifies that shape as a `rate_limited` failure, judges sufficiency on **raw provider results
before sanitization**, and keeps a clean zero-result from a healthy provider a 200. Ruling 18
removes in-request retries: advancing the chain *is* the retry, so traversal is bounded by the sum
of the per-provider timeouts. Ruling 12 records that there is **no spend ceiling** in this epic;
the observability floor is two `/metrics` counters (ruling 21).

The wire additions — `provider_used`, `fallback_fired`, `provider_errors` on `SearchResponse`,
`domain` on `SearchResult`, `search.fallback_fired` and `search.paid_calls` on `/metrics` — are
additive under the contract version spec 1 already moved to 1.2.0, and are metadata only: no raw
paid-provider body is ever persisted (owner decision 6).

Two definitions hold everywhere in this spec. **One identifier per provider (ruling 23):**
`SearchProvider.name` — `searxng`, `brave` — is the token carried by `FORAGE_SEARCH_PROVIDERS`,
`provider_used`, every `provider_errors` entry and the `search_unavailable` reason. The per-result
`engine` field is *provenance*: `brave-api` for the Brave provider, SearXNG's engine name
otherwise, so SearXNG's own `brave` sub-engine stays distinguishable; `brave-api` appears in no
token, reason or log line. **`fallback_fired`** means the traversal advanced past the first
provider — a later provider was called because an earlier one failed. It is one event at two
granularities: the per-response bool and the per-process `search.fallback_fired` count.

## Goals

- Ordered chain traversal in `run_search_pipeline`: free-first, advance on failure,
  replace-not-merge, exactly one provider call per provider per request; an exhausted chain is a
  **422** raised in `pipeline/orchestrator.py` — today's `searxng_*` codes when the configured
  chain is exactly `[searxng]`, `search_unavailable` with a closed-vocabulary reason for every
  other configured chain.
- Failure classification per ruling 17: `ProviderFailure` or SearXNG's 200-empty-plus-
  `unresponsive_engines` shape triggers fallback; a clean zero-result never does; sufficiency is
  judged before sanitization; no in-request retries; no tunable threshold.
- Typed telemetry: `provider_used: str`, `fallback_fired: bool`, `provider_errors: list[str]` on
  `SearchResponse`; `domain: str` on every `SearchResult`; `search.fallback_fired` and
  `search.paid_calls` counters on `/metrics`; contract regenerated under 1.2.0; metadata only.
- Proven sanitization parity: a poisoned meta-description snippet (SearXNG) and a poisoned chunk
  (Brave) take the identical stage-2 / stage-3 route with identical telemetry, and a fallback-served
  result set is sanitized exactly as a primary-served one.

## User Stories

### US-001: Chain traversal (free-first, replace-not-merge)

**Priority:** P1

**Description:** As an operator, I want search to try providers in configured order and serve from
the first that succeeds, so free search is preferred and the paid backend is reached only after a
free provider has failed — and I want an exhausted chain to refuse loudly rather than return an
empty 200.

**Independent Test:** With a chain of two fake `SearchProvider`s (`tests/fakes.py::
FakeSearchProvider`, names `searxng` and `brave`, `paid` `False` / `True`, recording every
`max_results` it is asked for) standing in for `FORAGE_SEARCH_PROVIDERS=searxng,brave`: when the
first returns a `ProviderFailure`, `run_search_pipeline` calls the second — asking it for
`request.num_results` where the first was asked for `fetch_limit` — and returns its (sanitized)
results; when the first succeeds, the second is never called; when both fail, the call raises
`PipelineError(error="search_unavailable")` whose `reason` is exactly
`"searxng: rate_limited; brave: timeout"` for those two failures; when the first *raises* instead
of returning, the second is still called. A configured one-provider `searxng` chain makes exactly
one provider call, raises today's `searxng_*` codes on failure, and the pre-existing orchestrator
search tests pass unchanged.

**Implementation Hints:**
- Consume the chain the `/search` handler already threads into `run_search_pipeline` (spec 1
  US-003, resolution map). Iterate it in order, calling `await provider.search(query,
  max_results)` with the per-provider candidate budget spec 1 US-002 writes (ruling 25):
  `max_results = request.num_results if provider.paid else fetch_limit` — referenced here, never
  rewritten; the orchestrator still re-applies its own slice after the return. On a
  `ProviderFailure`, append `f"{provider.name}: {failure.failure_class}"` to a local
  `provider_errors` list and advance. On a `ProviderSearchResult`, stop. This story lands only
  the `ProviderFailure`-driven advance; US-002 adds the second branch (the
  200-empty-plus-`unresponsive_engines` shape) at the same branch point. Each provider is called
  at most once per request; there is no retry (ruling 18). The fakes live in `tests/fakes.py`
  (`FakeSearchProvider`, beside `FakeContentCache`), imported by `tests/test_orchestrator.py` and
  `tests/test_app.py` alike; the traversal tests sit in `tests/test_orchestrator.py` beside the
  existing search tests, so no new module and no TESTING_GUIDE row.
- **The loop is total.** After ruling 27 every provider ends its mapping in `except Exception` →
  `ProviderFailure(hard_error, "unexpected")`, so an exception escaping `provider.search()`
  cannot happen. The orchestrator guards anyway: `except Exception` around the await treats the
  outcome as `hard_error` for that provider (entry `"<name>: hard_error"`, `detail=unexpected`
  in the log line) and advances, so a defect in provider *n* can never become a 500 or skip the
  free floor at *n+1*. An empty chain is a programming error before spec 4
  (`build_provider_chain` never yields one): `run_search_pipeline` raises `ValueError`, never a
  422 with an empty reason; spec 4 US-001 intercepts the policy-emptied chain before the loop
  with `policy_excluded_all_providers`.
- **Replace, don't merge.** The serving provider's raw results are the result set; nothing from a
  failed provider survives — not its partial results, not its `unresponsive_engines`. A provider
  returns exactly one of `ProviderSearchResult` or `ProviderFailure` (spec 1 US-001), so "partial
  results then an error" is by construction a `ProviderFailure` with nothing to keep. The failed
  provider's engine names — the diagnostic the GOTCHAS runbook keys on — are deliberately
  dropped from the response and the log; the SearXNG container's own logs remain that
  diagnostic.
- **The serving provider's results go through the existing per-result sanitization loop**
  (`pipeline/orchestrator.py` ~708–796) unchanged. Only the query step moves.
- **Exhausted chain → 422 (ruling 8), raised from `pipeline/orchestrator.py`** (ruling 14: the
  raise-site sweep in `tests/test_contract_errors.py` reads only `orchestrator.py` and
  `retrieval_app.py`; a raise anywhere else reddens it). Which code is decided by the
  **configured** chain, never the per-request effective chain (ruling 28), so no status code and
  no `/metrics` key can vary with spec 4's policy params. The predicate is one module-level
  helper in `pipeline/orchestrator.py`, `_legacy_searxng_codes(chain)`, defined as
  `len(chain) == 1 and chain[0].name == "searxng"`, with one call site at the raise. Its argument
  is the configured chain: in this spec that is `providers` as the handler threads it (identical
  to the configured chain until spec 4 filters it); spec 4 keeps evaluating this helper on the
  configured chain (resolution map, ruling 28) and the helper's docstring says so. Spec 1 US-004
  already pins the same name-token predicate on the configured chain (no `isinstance`), so this
  story moves that check into the helper without changing its answer for any chain. The helper
  receives the configured chain through a new keyword on `run_search_pipeline`,
  `configured_chain: Sequence[SearchProvider] | None = None` (defaulting to `providers` when not
  supplied), which spec 4 passes explicitly so the per-request effective chain never reaches
  the predicate.
  - predicate true → the existing `searxng_error` / `searxng_unavailable` `PipelineError`s with
    the `reason` strings **exactly as spec 1 US-002 leaves them**:
    `f"SearXNG returned HTTP error ({detail})"` and
    `f"SearXNG not reachable at {provider.origin}: {detail}"` — a closed `detail` token, no
    `str(exc)`, and `provider.origin` (spec 1 contract point 7: scheme, hostname and port with
    userinfo stripped) rather than `base_url`, which is not on the protocol and would put
    `SEARXNG_URL` credentials back into an unauthenticated 422 body. "Today" in this spec
    always means that post-spec-1 text, never the `{exc}` interpolation at pre-epic HEAD.
  - predicate false (two or more providers, or a single non-SearXNG provider) →
    `PipelineError(error="search_unavailable", reason=<closed-vocabulary reason>)`. Spec 2
    US-003 pins the Brave-only chain's `reason == f"brave: {failure_class}"` under this same
    rule; this story generalises the reason to every provider traversed without changing that
    pinned string.
- **The `search_unavailable` reason is a closed vocabulary.** It is the `provider_errors` entries
  in chain order joined by `"; "`, each entry `"<provider.name>: <failure_class>"` with
  `failure_class` ∈ `{rate_limited, timeout, hard_error, auth, quota}` (ruling 13). Never
  exception text, never a provider URL, never `ProviderFailure.detail`, never query text.
  `ERROR_HANDLING.md` calls today's `str(exc)` passthrough on `/search` "the documented exception,
  not a precedent to extend", and a credential now sits in the environment of this path
  (`FORAGE_BRAVE_API_KEY`).
- **Log each provider failure as one message-carried WARNING line** —
  `search_provider_failed provider=%s failure_class=%s detail=%s`, `%`-formatted into the message
  the way `weights_fetch_skipped` is (`model_fetcher.py`), never an `extra=` dict: nothing renders
  `extra=` in-container (`kit_tools/arch/patterns/LOGGING.md`; `kit_tools/docs/GOTCHAS.md`
  "Nothing configures logging"), and this line is the **only** place `ProviderFailure.detail` is
  observable. All three values are closed vocabulary; the orchestrator-classified failure US-002
  adds logs `detail=unresponsive_engines`, a token defined beside the reason composition in
  `pipeline/orchestrator.py`. The line is deliberately a second WARNING beside the provider's
  own (`brave_search_failed`, spec 2; SearXNG's per-failure line, spec 1 US-002): cause at the
  provider, effect on the chain.
- `search_unavailable` already exists: spec 1 US-004 added it to `SearchErrorCode`, declared it
  on `/search`, regenerated the contract and created `tests/golden/contract_1_2_0.json`. This
  story adds **no** vocabulary, no `responses=` change and no contract regeneration; it only
  raises the code. Its Release-body announcement is mechanical (ruling 20a, spec 5 US-004).
- Docs this story owns, in the same change: `kit_tools/docs/TROUBLESHOOTING.md`'s error table —
  the `search_unavailable` row spec 1 US-004 adds (spec 2 seeds `brave: auth` / `brave: rate_limited`)
  gains the composite format: chain-order `<name>: <failure_class>` entries joined by `"; "`,
  with `searxng: rate_limited; brave: timeout` as the worked example.
- `pipeline/orchestrator.py` is in `_REVISION_SOURCES`, so this story rotates
  `sanitizer_revision` (ruling 11): record before/after in `docs/bootstrap-notes.md`'s rotation
  table, `CLAUDE.md`'s coexistence narrative and `kit_tools/arch/DECISIONS.md`'s rotation table
  (ruling 32), following the sixth-rotation entry's format.

**Acceptance Criteria:**
- [x] `run_search_pipeline` calls providers in chain order; a `ProviderFailure` from provider *n*
      results in a call to provider *n+1*; a success stops traversal and no later provider is
      called. Each provider is called at most once per request, and the fakes record that the
      free provider was asked for `fetch_limit` and the paid provider for `request.num_results`
      (ruling 25).
- [x] A fake whose `search()` raises is recorded as `"<name>: hard_error"` and the next provider
      is called; no `/search` request returns 500 because a provider raised.
- [x] Replace-not-merge: the response's `results` and `unresponsive_engines` come only from the
      serving provider; a failed provider contributes nothing but its entry in the
      orchestrator-local `provider_errors` list that composes the 422 reason (the wire field
      itself arrives in US-003).
- [x] A one-provider chain makes exactly one `search()` call; for the `searxng`-only chain every
      pre-existing search test in `tests/test_orchestrator.py` passes unchanged.
- [x] Exhausted chain returns 422: `searxng_error` / `searxng_unavailable` with the `reason`
      strings exactly as spec 1 US-002 leaves them (`SearXNG returned HTTP error (<detail>)`,
      `SearXNG not reachable at <origin>: <detail>` with `origin` userinfo-stripped — a test with
      `http://user:pass@unreachable:8080` asserts `"unreachable:8080" in reason` and
      `"pass" not in reason`; no exception text) when the configured
      chain is exactly `[searxng]`; `search_unavailable` for every other configured chain.
      `_legacy_searxng_codes` is the only place the choice is made (one call site in
      `pipeline/orchestrator.py`, fed by `configured_chain` when supplied, else `providers`). No
      path returns
      200 with an empty list because providers failed.
- [x] The `search_unavailable` `reason` is exactly the chain-order entries
      `"<provider.name>: <failure_class>"` joined by `"; "` (test pins
      `"searxng: rate_limited; brave: timeout"`), and a test asserts every entry splits on
      `": "` into a name present in `[p.name for p in chain]` and a class in `FAILURE_CLASSES`.
- [x] A test in the style of
      `tests/test_cache.py::TestReconnect::test_connect_failure_never_logs_url_or_secret` drives a
      SearXNG failure and a Brave failure with a sentinel `FORAGE_BRAVE_API_KEY` value and asserts
      neither the SearXNG URL, the sentinel, nor any exception text appears in the 422 body or
      in any log record; the same test drives the configured `[searxng]` chain's 422 and asserts
      no exception text in its body or logs (the base-URL echo is the one named exception).
- [x] Each provider failure emits one WARNING whose `getMessage()` contains
      `search_provider_failed`, `provider=<name>`, `failure_class=<class>` and `detail=<token>`
      (asserted on the message text, not on `record.__dict__`).
- [x] `search_unavailable` is raised only from `pipeline/orchestrator.py` in this spec (spec 4 adds
      the policy raise in `retrieval_app.py`; both files are inside the raise-site sweep);
      `pipeline/contract.py`,
      the `/search` `responses=` declaration and `contract/openapi.yaml` are untouched by this
      story (`git diff --stat` shows none of them).
- [x] `grep -n 'searxng: rate_limited; brave: timeout' kit_tools/docs/TROUBLESHOOTING.md` returns
      the composite-format row.
- [x] The `sanitizer_revision` rotation is recorded in `docs/bootstrap-notes.md`, `CLAUDE.md`
      and `kit_tools/arch/DECISIONS.md`.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-002: Failure-class discrimination (per-request cost guard)

**Priority:** P1

**Description:** As an operator paying per Brave query, I want the paid fallback to fire when the
free path genuinely failed — including the way SearXNG actually fails in production, a 200 with
no results and every engine listed as unresponsive — and never on a clean empty result, a
poisoned result set, or a degraded container, so a single query cannot buy a paid call unless
free search really did fail. This story is a per-request guard; it is **not** a spend ceiling
(ruling 12).

**Independent Test:** With a two-provider chain whose first provider is a `SearxngProvider` over a
mocked SearXNG and whose second is a fake paid provider: (a) SearXNG answers 200 with
`results: []` and `unresponsive_engines: [["duckduckgo", "CAPTCHA"], ["brave", "429"]]` → the
paid provider is called, the response carries `provider_errors == ["searxng: rate_limited"]`;
(b) SearXNG answers 200 with `results: []` and `unresponsive_engines: []` → the paid provider is
not called, the response is 200 with `results == []`, `fallback_fired == false`,
`provider_errors == []`; (c) SearXNG answers 429 → `ProviderFailure(rate_limited)` → the paid
provider is called; (d) SearXNG answers 200 with three raw results every one of which
sanitization omits (structural block) → the paid provider is not called, the response is 200 with
`results == []` and `omitted_by_reason == {"structural_blocked": 3}`; (e) body (a) against the
configured chain `[searxng]` → 200, `results == []`, `unresponsive_engines` populated,
`provider_used == "searxng"`, `fallback_fired is False`, `provider_errors == []`, and one
`search_provider_failed` WARNING. In every case `SearxngProvider.search()` is awaited exactly
once. The mocked envelopes are built with the existing
`tests/test_orchestrator.py::_mock_searxng_response(results, *, unresponsive_engines=...)`.

**Implementation Hints:**
- The classification lives in `run_search_pipeline` (`pipeline/orchestrator.py`), applied to each
  provider's outcome before any sanitization. A provider outcome is a **failure** when it is a
  `ProviderFailure`, **or** when it is a `ProviderSearchResult` with zero raw results **and** a
  non-empty `unresponsive_engines` list — recorded as
  `"<provider.name>: rate_limited"` and logged through US-001's `search_provider_failed` line
  with `detail=unresponsive_engines`. This is the headline rule (ruling 17): the recurring
  production failure in `kit_tools/docs/GOTCHAS.md` "SearXNG :latest rots" never raises, passes
  `raise_for_status()` (~678), and is visible only through the `unresponsive_engines` the
  orchestrator already parses (~684). The rule is written against `ProviderSearchResult`, so it
  is provider-agnostic in form; a provider with no engines (Brave) always has an empty list and
  never matches. Only the list's emptiness is read: its entries are unsanitized, provider-asserted
  text (no stage scans them) and stay that way on the wire.
- **Sufficiency is judged on raw provider results, before sanitization.** A provider that returned
  results is a success even if the sanitization loop later omits every one of them
  (`invalid_url`, `structural_blocked`, `injection_detected`, `promptguard_unavailable`). A
  poisoned SERP must not buy a paid call, and a weights-free container running fail-closed
  (which omits every result) must not pay on every query.
- **A clean zero is a success.** Zero raw results with an empty `unresponsive_engines` is served
  as a 200 with an empty list, `fallback_fired: false`, in every chain shape. Results plus a
  non-empty `unresponsive_engines` is a partial answer, also served.
- **The SearXNG-only chain is frozen.** When the configured chain is exactly `[searxng]`
  (US-001's `_legacy_searxng_codes`), the 200-empty-plus-`unresponsive_engines` shape is served
  exactly as today (200, `results: []`, `unresponsive_engines` populated) — it is a fallback
  *trigger*, and with nothing to fall back to there is nothing to trigger. Its new fields are
  pinned: `provider_used == "searxng"`, `fallback_fired is False`, `provider_errors == []` —
  `provider_errors` records every provider the traversal failed past, including the last one when
  a chain is exhausted, and a `[searxng]` chain served as today has failed past nothing. The
  `search_provider_failed` WARNING (`detail=unresponsive_engines`) is still emitted: the response
  is unchanged, and that line is the one first-party signal a key-less deployment gets for the
  nine-day silent failure (`CLAUDE.md` invariant 5). In a multi-provider chain the shape is a
  failure wherever it occurs, including as the last provider, so a chain that ends on it raises
  `search_unavailable` with `"searxng: rate_limited"` as that provider's entry (ruling 8: an
  exhausted chain is never a 200-empty).
- **No in-request retries** (ruling 18). No sleep, no second attempt against the same provider;
  `model_fetcher.py`'s background retry helpers are not imported here. Each provider keeps its
  own per-call timeout (SearXNG 10 s; Brave's is `search_brave_timeout_seconds`, spec 2 /
  ruling 9), so the whole traversal is bounded by their sum.
- **No threshold knob.** The former `config.yaml` insufficiency threshold is dropped (ruling 17);
  "sufficient" means "at least one raw result, or zero raw results with no unresponsive engine".
  `run_search_pipeline` reads nothing new from `config`.
- Docs this story owns, in the same change (ruling 32):
  - `kit_tools/docs/API_GUIDE.md` ~239, the `unresponsive_engines` row ("a partial answer, not an
    error"): with zero results it is the free-path failure signal that advances a multi-provider
    chain; its entries are unsanitized provider-asserted text and must not be rendered into a
    model prompt. The closing paragraph at ~242–245 (spec 1 US-002 has already removed its
    exception-text clause) loses "One request, 10 s timeout, no retries" and says instead: one
    call per provider in configured order, no retries, bounded by the sum of the per-provider
    timeouts; `search_unavailable`'s `reason` is the closed composite of US-001.
  - `kit_tools/arch/patterns/ERROR_HANDLING.md`: the Retry and Timeout Table gains a "Provider
    chain traversal" row (retries: none; bound: sum of per-provider timeouts); the Degradation
    Matrix row at ~250 ("SearXNG unreachable or erroring | No fallback engine.") now says the
    next provider in the configured chain serves, and `/search` is a 422 only when the chain is
    exhausted; the "retries only for background acquisition" bullet stands unchanged.
  - `kit_tools/docs/GOTCHAS.md` "SearXNG :latest rots" gains: in a multi-provider chain this
    shape advances to the next provider only when zero raw results come back; a partial answer
    (results plus a non-empty list) is served as-is and no fallback fires.
  - `kit_tools/arch/SERVICE_MAP.md`: the SearXNG Purpose row (~103, "The only search backend")
    and the Failure Impact Matrix SearXNG row (~318) are qualified — terminal for `/search` only
    when the configured chain is `[searxng]`. (Rows 104 and 109 are spec 1 US-002's.)
  - `docs/configuration.md`, beside spec 2's `search_brave_timeout_seconds` row, one sentence of
    caller guidance: a caller's `/search` timeout must exceed the sum of the configured chain's
    per-provider timeouts (10 s + `search_brave_timeout_seconds` for `searxng,brave`); lower the
    Brave timeout rather than raising the caller's.
- This story edits `pipeline/orchestrator.py` and rotates `sanitizer_revision` again; record it
  in `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md` (ruling 32).

**Acceptance Criteria:**
- [ ] A SearXNG 200 with zero raw results and a non-empty `unresponsive_engines` list advances a
      multi-provider chain and is recorded as `"searxng: rate_limited"` in `provider_errors`
      (test with the exact mocked body from the Independent Test); the WARNING for it carries
      `detail=unresponsive_engines`.
- [ ] A SearXNG 200 with zero raw results and an empty `unresponsive_engines` list never advances
      the chain: the response is 200, `results == []`, `fallback_fired == false`,
      `provider_errors == []`, and no later provider is called.
- [ ] A `ProviderFailure` of any class (`rate_limited`, `timeout`, `hard_error`, `auth`, `quota`)
      advances the chain (parametrised test over all five).
- [ ] Sufficiency is pre-sanitization: a provider whose raw results are all omitted by the
      sanitization loop does not advance the chain; the response is 200 with `results == []` and
      the existing omission telemetry, and no later provider is called. The same holds when the
      classifier is unavailable and `promptguard_fail_closed` is `true`.
- [ ] Results plus a non-empty `unresponsive_engines` list is served as a success with the list
      passed through; no later provider is called.
- [ ] For the configured chain `[searxng]`, the 200-empty-plus-`unresponsive_engines` shape is
      served as today's 200 with `provider_used == "searxng"`, `fallback_fired is False`,
      `provider_errors == []` and one `search_provider_failed` WARNING (the existing
      `test_search_unresponsive_engines_forwarded` and
      `test_search_no_unresponsive_engines_empty_list` pass unchanged); in a multi-provider chain
      that ends on this shape the result is 422 `search_unavailable`.
- [ ] Every provider is awaited exactly once per request in every case above; no test patches a
      sleep because there is none.
- [ ] No `config.yaml` key is added; `docs/configuration.md` changes only by the caller-timeout
      sentence named in the hints.
- [ ] `grep -n 'No fallback engine' kit_tools/arch/patterns/ERROR_HANDLING.md`,
      `grep -n 'One request, 10 s timeout, no retries' kit_tools/docs/API_GUIDE.md` and
      `grep -n 'the exception text' kit_tools/docs/API_GUIDE.md` all return nothing;
      `grep -n 'The only search backend' kit_tools/arch/SERVICE_MAP.md` returns nothing.
- [ ] `kit_tools/docs/API_GUIDE.md` (`unresponsive_engines` row and closing paragraph),
      `kit_tools/arch/patterns/ERROR_HANDLING.md` (both tables), `kit_tools/docs/GOTCHAS.md`
      ("SearXNG :latest rots"), `kit_tools/arch/SERVICE_MAP.md` (the two SearXNG rows) and
      `docs/configuration.md` carry the sentences named in the hints; the rotation is recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-003: Fallback telemetry + provenance (metadata only)

**Priority:** P1

**Description:** As a consumer, I want each search response to tell me which provider served it,
whether the chain advanced, and which providers failed on the way, with a per-result `domain` I
can key on; and as an operator, I want `/metrics` to count fallbacks and paid calls so I can see
spend without scraping response bodies — all without ever persisting a raw paid-provider body.

**Independent Test:** A SearXNG-served response reports `provider_used == "searxng"`,
`fallback_fired == false`, `provider_errors == []`; a Brave-served-after-SearXNG-failed response
reports `provider_used == "brave"`, `fallback_fired == true`,
`provider_errors == ["searxng: rate_limited"]`; every result carries `domain` equal to the
lower-cased hostname of its `url`. After one such fallback request through the app, `GET /metrics`
reports `search.fallback_fired == 1` and `search.paid_calls == 1`; after a request in which Brave
was called and also failed, both counters have advanced again although the request returned 422.
`tests/test_contract_export.py` is green against the regenerated `contract/openapi.yaml` and
`tests/golden/contract_1_2_0.json`.

**Implementation Hints:**
- **Wire `SearchResponse`** (`models.py` ~278–322, beside `omitted_by_reason`):
  - `provider_used: str` — **required**; the serving provider's `provider.name`, which for the
    shipped providers is the same token `FORAGE_SEARCH_PROVIDERS` names (`searxng`, `brave`;
    ruling 23). An open `str`, not a `Literal`, for the same reason `omitted_by_reason` is a dict
    and not an enum: a third provider must be an additive MINOR, not a validation failure on an
    old client. Say so in the field description.
  - `fallback_fired: bool` — default `False`; `True` iff the traversal advanced past the first
    provider (the Overview's one definition). `provider_errors` carries one entry per failed
    provider *including the last*, so `fallback_fired` is not derivable from it (an exhausted
    single-provider chain has one entry and no advance); the field
    is set from the local flag that also increments `search.fallback_fired`. It records chain
    advancement, not "a paid provider ran": for a paid-first chain it is `True` when the free
    provider serves. The description must not say "paid"; it says the field is the per-response
    face of the `search.fallback_fired` counter.
  - `provider_errors: list[str]` — default empty; the chain-order entries
    `"<provider.name>: <failure_class>"` from US-001 (closed vocabulary, bounded by the chain
    length, never exception text, a URL or `ProviderFailure.detail` — `detail` reaches only the
    `search_provider_failed` WARNING). This is the **only** home for provider-level failures:
    they never increment `omitted_results` / `omitted_by_reason` (those count examined candidate
    results the pipeline withheld) and never appear in `unresponsive_engines` (the serving
    provider's SearXNG engines; empty on a Brave-served response). Spec 2 US-003's Brave
    failures arrive as `ProviderFailure`s (ruling 13) and land here.
- **Wire `SearchResult.domain: str`** — required, `min_length=1` like `RetrievedContent.domain`.
  Derive it inside `_canonicalize_search_url` (`pipeline/orchestrator.py:593–634`) as
  `domain = parsed.hostname.lower()`, bound **before** the IPv6 re-bracketing: the existing
  `host` local is reassigned to `f"[{host}]"` four lines later (~620–621), so returning that
  local would ship the bracketed form. Widen the return to a three-tuple
  `(canonical, scanned, domain)` and update the single call site (~716). `urlsplit().hostname`
  carries no userinfo and no port (the canonicalizer already rejects URLs with userinfo as
  `invalid_url`, so they never reach this point) and is already lower-cased by `urllib` — the
  `.lower()` is belt-and-braces. An IDN host is carried as the canonical URL carries it (no
  decoding — decoding is how a homograph defeats a downstream list). For an IPv6 literal
  `domain` is the unbracketed `2001:db8::1` while `url` shows `[2001:db8::1]` — the one case
  where `domain` is not a substring of `url`; the field description names it. This is the
  **hostname, not eTLD+1**: eTLD+1 needs the Public Suffix List (a new dependency plus a data
  file that rots), the consumer's trust lists are hostname-keyed, and `/retrieve` already
  exposes `RetrievedContent.domain` as `parsed_final.hostname` — the two routes must agree. A
  consumer can derive eTLD+1 from a hostname; the reverse is impossible. The field description
  says "provenance signal, not a trust decision".
- **Per-result `engine`** stays what each provider sets — provenance, never a chain token
  (ruling 23): `brave-api` for the Brave provider, SearXNG's engine name otherwise.
  `provider_used` and every failure token use `provider.name` (`brave`), so SearXNG's own `brave`
  engine stays distinguishable from the Brave provider.
- **`/metrics` counters (ruling 21)** — `search.fallback_fired: int` and `search.paid_calls: int`:
  - `retrieval_app.SearchMetrics.__init__` (~721) gains both counters;
    `SearchMetricsResponse` (~361–372, `extra="forbid"`) gains both fields with non-empty
    descriptions (the counter description says it is the per-process count of the per-response
    `fallback_fired` bool); the `/metrics` handler's `"search"` dict (~1307–1312) emits both,
    **appended in the same position** in the served dict and in the mirror —
    `kit_tools/docs/GOTCHAS.md:296–299`: order is part of the contract. The guards are
    `test_served_metrics_are_the_handlers_dict_serialized` and
    `test_metrics_mirror_round_trips_the_served_body` (ruling 26e; both compare key order), plus
    `test_every_section_the_handler_emits_has_a_model` and
    `test_metrics_schema_is_fully_rendered` — **not**
    `test_dataclass_counters_and_their_models_carry_the_same_fields`, which is parametrised over
    the two dataclass counter sets and cannot see the plain-class `SearchMetrics`. Nothing
    guards a counter added to `SearchMetrics` but never emitted, so a test asserts the `search`
    section of `GET /metrics` carries both keys after one fallback request through the app.
  - **Increment sites are in `run_search_pipeline`'s traversal**, so the 422 path is counted:
    `paid_calls += 1` immediately before each `await provider.search(...)` on a provider with
    `paid=True` (a paid call that times out is still a billed call); `fallback_fired += 1` once
    per request, at the moment traversal first advances past a failed provider. The wire
    `fallback_fired` field is set from the same local flag, so the two can never disagree.
  - Thread the metrics object in as a keyword parameter of `run_search_pipeline`
    (`search_metrics=...`), typed as a two-attribute `Protocol` declared in
    `pipeline/orchestrator.py` that `retrieval_app.SearchMetrics` satisfies structurally — no
    import in either direction. The default is a module-private null object satisfying the
    Protocol (the `metrics if metrics is not None else CacheMetrics()` idiom, `cache.py:393`),
    so the increment sites carry no `is not None` branch under pyright strict. The `/search`
    handler passes the **annotated local** it already binds —
    `search_metrics: SearchMetrics = request.app.state.search_metrics` at
    `retrieval_app.py:1550` — never `request.app.state.search_metrics` directly: Starlette's
    `State` returns `Any`, which would let pyright skip the structural check. The state
    attribute exists both from the lifespan and from the module-scope mirror (ruling 22), so
    the lifespan-free test clients keep working; direct callers in `tests/test_orchestrator.py`
    are unaffected by the default.
- **Metadata only** (owner decision 6): every new field is a token, a bool, a count or a
  hostname. No raw provider result body is written to the content cache, to a log line, or to
  `/metrics`; spec 2's no-cache-write regression test stays green on the fallback path.
- **Contract:** version stays **1.2.0**. Run `uv run python -m scripts.export_contract` — it
  writes three files, `contract/openapi.yaml`, `contract/openapi.yaml.sha256` and the self-test
  twin `tests/fixtures/contract/unregenerated_openapi.yaml` (`test_the_self_test_twin_is_current`
  is red without the third) — and regenerate `tests/golden/contract_1_2_0.json` in place:
  nothing publishes between spec 1 and spec 5, so the 1.2.0 fixture accumulates the epic's
  additions until the `v1.1.0` cut (epic "Contract versioning"); `contract_1_1_0.json` and
  `contract_1_0_0.json` are never edited (GOVERNANCE ruling (c)). Do **not** move
  `CONTRACT_VERSION`.
- **Doc fan-out this story owns**, in the same change (ruling 32): `kit_tools/docs/MONITORING.md`
  `search` metrics table — two new rows stating what increments each counter, what a rising
  value means (`paid_calls` rising faster than `fallback_fired` means the paid provider is first
  in someone's effective chain), that the bool and the counter are one event at two
  granularities, the caveat that both counters move only when a chain can advance (a
  `searxng`-only deployment's first-party signal is the `search_provider_failed` WARNING, not a
  counter), one alert condition (a `paid_calls` rate over a window, sized against Brave's
  monthly query credit from owner decision 2) and the remedy: remove the paid provider from
  `FORAGE_SEARCH_PROVIDERS` and restart, because the chain is resolved at boot; its
  failure-signals row for SearXNG says the same; `kit_tools/docs/API_GUIDE.md` `/search`
  response-field table (`provider_used`, `fallback_fired`, `provider_errors`, and `domain` in the
  results shape, with the IPv6 note); and every document that embeds the contract anchor,
  because regeneration changes it: `kit_tools/docs/API_GUIDE.md` (~431),
  `kit_tools/docs/CI_CD.md` (~416), `kit_tools/docs/DEPLOYMENT.md` (~115),
  `kit_tools/arch/SERVICE_MAP.md:202`.
- **Test blast radius:** `SearchResponse(...)` is constructed directly in `tests/test_models.py`
  (five sites) and `tests/test_app.py` (two sites), and `SearchResult(...)` in
  `tests/test_models.py` (five sites); each gains the required `provider_used` / `domain`. Tests
  that set `app.state.*` on the module singleton use the save/`delattr`/restore idiom at
  `tests/test_app.py` ~1504–1517 (ruling 26d).
- This story edits `pipeline/orchestrator.py` (populating the fields, the increment sites) and
  rotates `sanitizer_revision`; record it in `docs/bootstrap-notes.md`, `CLAUDE.md` and
  `kit_tools/arch/DECISIONS.md` (ruling 32).

**Acceptance Criteria:**
- [ ] `SearchResponse` carries `provider_used: str` (required, the serving provider's `name`),
      `fallback_fired: bool` (default `False`, `True` iff the traversal advanced past the first
      provider — set from the same local flag that increments `search.fallback_fired`, and not
      derivable from `provider_errors`, which also records an exhausted last provider) and
      `provider_errors: list[str]`
      (default empty, chain-order `"<provider.name>: <failure_class>"` entries); tests cover the
      SearXNG-served, Brave-after-SearXNG-failed and paid-first-then-free shapes, and no field
      description contains the word "paid".
- [ ] Provider-level failures appear only in `provider_errors`: a response served after a SearXNG
      failure has `omitted_results == 0` from that failure and `unresponsive_engines == []` when
      Brave served (test).
- [ ] Every `SearchResult` carries `domain: str` equal to the lower-cased `urlsplit(url).hostname`
      of its canonical `url`, without userinfo or port; tests cover an upper-case host, a host with
      a port, a userinfo URL (omitted as `invalid_url`, so no `domain` is ever derived from it),
      and an IPv6 literal, asserting deliberately that `url` carries `[2001:db8::1]` while
      `domain == "2001:db8::1"`. The field description states it is a hostname (not eTLD+1), a
      provenance signal, not a trust decision, and names the IPv6 case.
- [ ] `/metrics` `search` carries `fallback_fired` and `paid_calls`, appended in the same position
      in the served dict and the mirror; `paid_calls` increments per call to a `paid=True`
      provider whether or not it served (a paid call ending in 422 is counted); `fallback_fired`
      increments once per request in which traversal advanced, including requests that end in
      422; a `searxng`-only chain never moves either counter. A test asserts both keys are
      present in the `search` section after one fallback request through the app;
      `tests/test_contract_metrics.py` passes, including its two key-order guards.
- [ ] `run_search_pipeline` takes `search_metrics` typed as the orchestrator-side `Protocol` with
      a null-object default; the `/search` handler passes the annotated local `search_metrics`,
      and `uv run pyright` checks `retrieval_app.SearchMetrics` against the Protocol at that call.
- [ ] Contract regenerated under 1.2.0 (`CONTRACT_VERSION` unchanged); `contract/openapi.yaml`,
      `.sha256`, `tests/fixtures/contract/unregenerated_openapi.yaml` and
      `tests/golden/contract_1_2_0.json` updated; `contract_1_1_0.json` and
      `contract_1_0_0.json` byte-identical to before; `tests/test_contract_export.py` green.
- [ ] Telemetry is metadata-only: a test drives a fallback-served request with a sentinel string
      in the Brave result body and asserts the sentinel appears in no cache write, no log record,
      and no `/metrics` field.
- [ ] `kit_tools/docs/MONITORING.md` (`search` table rows with the caveat, alert condition and
      remedy; failure-signals row), `kit_tools/docs/API_GUIDE.md` (`/search` response fields)
      and the four anchor-embedding documents named in the hints are updated in the same change;
      the rotation is recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and
      `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-004: Sanitization parity across providers + poisoned-metadata edge case

**Priority:** P1

**Description:** As a security-conscious operator, I want proof that fallback never bypasses
sanitization: a poisoned SERP snippet (from JSON-LD / OG metadata, the Zscaler-documented vector)
and a poisoned Brave chunk must take the identical stage-2 / stage-3 route with identical
telemetry, whether the provider served as `chain[0]` or after a fallback.

**Independent Test:** A test parametrised over the SearXNG path (`content_kind="snippet"`) and the
Brave path (`content_kind="chunk"`), using fake providers as the seam: a `content` payload that
`scan_structural` returns BLOCKED for is omitted on both paths with
`omitted_by_reason == {"structural_blocked": 1}` and `results == []`; a payload it returns
SUSPICIOUS for is returned on both paths with `suspicious == true`; with `run_promptguard` mocked
to return INJECTION_DETECTED, the same result is omitted on both paths with
`omitted_by_reason == {"injection_detected": 1}`, and the mock received the identical
`_search_result_promptguard_input` string on both paths. The same poisoned result set served by
Brave *after* a SearXNG failure yields `results`, `omitted_by_reason` and `suspicious` flags equal
to those from Brave serving as `chain[0]`.

**Implementation Hints:**
- **Tests first, not tests only.** This story's deliverable is the parity proof, and its default
  footprint is `tests/` alone. If the proof finds a bypass — a field that does not reach
  `scan_structural`, a path that skips `run_promptguard`, a fallback-served set that diverges
  from the `chain[0]`-served one — the fix lands **in this story**, in `pipeline/orchestrator.py`,
  with the failing parity test as its regression test and the rotation recorded in the three
  rotation tables (ruling 32): the vision's "100% of results classified" is load-bearing, and a
  known bypass may not ship behind a backlog item. Record what was found and what closed it in
  this spec's Implementation Notes.
- `unresponsive_engines` is deliberately outside the parity claim: it is unsanitized,
  provider-asserted text that no stage scans (US-002's `kit_tools/docs/API_GUIDE.md` sentence
  says so); the claim covers `title`, `url` and `content` on both paths.
- The SearXNG half largely exists:
  `tests/test_orchestrator.py::test_search_blocked_snippet_omitted`,
  `::test_search_suspicious_snippet_flagged`,
  `::test_search_scans_title_url_and_snippet_before_exposure` and
  `::test_search_injection_detected_with_loaded_classifier_counts_omission`. Extend or
  parametrise those over the provider rather than duplicating them; the genuinely new assertions
  are the Brave / chunk half, the identical stage-3 input string, and the fallback-served path.
- **The seam is the provider, not `httpx`.** After spec 1 the SearXNG call lives behind
  `SearxngProvider`; build these tests on fake `SearchProvider`s that return crafted
  `ProviderSearchResult`s, not on `_searxng_client_patch`.
- **Claim what is provable in a hermetic suite.** Stage 2 (`scan_structural`) is deterministic and
  its verdicts are asserted for real. Stage 3 runs against a mock (weights are a runtime input the
  suite never has), so the stage-3 assertions prove *routing* — the poisoned `content` reaches
  `run_promptguard` inside `_search_result_promptguard_input` with the same call shape on both
  paths — not detection efficacy, which is `epic-forage-injection-corpus` (family Epic 6).
- The scanned text and the returned text are the same string bounded by
  `_MAX_SEARCH_SNIPPET_LENGTH` (2,000) on both paths; assert that a chunk longer than the bound is
  truncated to the same string that is scanned. Do not raise the bound.

**Acceptance Criteria:**
- [ ] A stage-2 BLOCKED payload is omitted on both the SearXNG and Brave paths with
      `omitted_by_reason == {"structural_blocked": 1}` and `results == []`.
- [ ] A stage-2 SUSPICIOUS payload is returned on both paths with `suspicious == true`.
- [ ] With `run_promptguard` mocked to INJECTION_DETECTED, the result is omitted on both paths with
      `omitted_by_reason == {"injection_detected": 1}`, and the mock received the identical
      `_search_result_promptguard_input` string on both paths.
- [ ] A fallback-served result set (Brave after a SearXNG failure) produces `results`,
      `omitted_by_reason` and per-result `suspicious` flags equal to the same set served as
      `chain[0]`.
- [ ] A chunk longer than `_MAX_SEARCH_SNIPPET_LENGTH` is returned and scanned as the same
      truncated string.
- [ ] The story closes with zero open parity gaps: every criterion above is green against the
      shipped `pipeline/orchestrator.py`, and this spec's Implementation Notes record each bypass
      the tests found together with the production change that closed it (an explicit "none
      found" entry when the diff touches only `tests/`).
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

## Edge Cases

- All providers fail → **422**: `searxng_error` / `searxng_unavailable` with spec 1 US-002's
  reason text when the configured chain is `[searxng]`; `search_unavailable` for every other
  configured chain, `reason` listing every provider in chain order. Never a 200 with an empty
  list: that changes a status code a client observes (MAJOR) and recreates the nine-day
  silent-failure shape. US-001.
- Paid provider also fails after fallback → 422 `search_unavailable` with both tokens, e.g.
  `"searxng: rate_limited; brave: timeout"`. The 422 body shape `{error, reason, request_id}` is
  unchanged; `provider_errors` is a field of the success body only. `search.paid_calls` and
  `search.fallback_fired` still advance. US-001 / US-003.
- A provider returns partial results then errors mid-stream → its `search()` returns a
  `ProviderFailure` (a provider returns exactly one of the two types); the partial results are
  discarded and the chain advances. US-001.
- A provider's `search()` raises (a defect, not an upstream outcome — ruling 27 makes it
  unreachable) → the orchestrator's own `except Exception` records `"<name>: hard_error"` and
  advances; never a 500. An empty chain passed directly is a `ValueError` (programming error
  before spec 4). US-001.
- SearXNG 200 with `{"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ...]}`
  (list-of-pairs or list-of-strings, as the orchestrator already accepts) → a `rate_limited`
  failure in a multi-provider chain; served as today's 200 in the configured `[searxng]` chain
  with `provider_used: "searxng"`, `fallback_fired: false`, `provider_errors: []` and one
  `search_provider_failed` WARNING. US-002.
- SearXNG 200 with results **and** a non-empty `unresponsive_engines` → a partial answer; served,
  no fallback. US-002.
- SearXNG 200 with `results: []` and `unresponsive_engines: []` → clean zero; 200 with an empty
  list, `fallback_fired: false`, in every chain shape. US-002.
- Free provider returns results that sanitization omits entirely (poisoned SERP, or fail-closed
  with the classifier unavailable) → 200 with an empty list and the omission telemetry; no
  fallback, no paid call. US-002.
- Single paid-only chain (`FORAGE_SEARCH_PROVIDERS=brave`) exhausted → 422 `search_unavailable`
  (spec 2 US-003 owns it; the US-001 rule produces the same answer). US-001.
- Paid-first chain (`brave,searxng`) where Brave fails and SearXNG serves → 200,
  `provider_used: "searxng"`, `fallback_fired: true`, `provider_errors: ["brave: timeout"]`;
  `search.paid_calls` advanced by one. US-003.

## Out of Scope

- Per-request policy params and `/health` provider status (spec 4); the release cut (spec 5).
- A Forage-side spend ceiling, per-process paid-call cap or budget breaker (ruling 12: accepted
  as a known risk for this epic); the global daily paid-fallback circuit breaker (Poppy consumer).
- An in-process breaker that remembers a failing provider across requests — every request
  traverses the chain afresh, so during a sustained free-path outage every request pays the
  free provider's timeout before advancing.
- Bounding `SearchRequest.query` on the wire — a `max_length` would refuse a value accepted today
  (MAJOR). The egress bound this spec relies on is the provider-side one: the outbound copy of
  `query` is truncated to `search_brave_query_max_chars` (default 400) inside the paid provider
  (spec 2 US-001, ruling 30).
- Adding `search_unavailable` to the vocabulary, its `/search` declaration and its error-table
  rows (spec 1 US-004); its Release-body announcement (spec 5 US-004, ruling 20a).
- Detection efficacy of stage 3 on real weights (`epic-forage-injection-corpus`, family Epic 6).

## Assumptions

- Spec 1 delivered the seam: `SearchProvider` with `name: str`, `paid: bool` and
  `search(query, max_results) -> ProviderSearchResult | ProviderFailure`; `ProviderSearchResult`
  carries the raw `{title,url,content,engine}` dicts, `unresponsive_engines` and `provider_name`;
  `ProviderFailure` carries `provider_name`, a closed `failure_class` and a closed `detail`
  (ruling 13). The `/search` handler threads the resolved chain into `run_search_pipeline` (spec 1
  US-003), and spec 1 US-002 wrote the per-provider candidate budget
  `max_results = request.num_results if provider.paid else fetch_limit` (ruling 25).
- Spec 2 delivered `BraveApiProvider` with `name == "brave"`, `paid == True`, per-result
  `engine == "brave-api"` (ruling 23), every Brave failure mapped to a `ProviderFailure` through
  a catch-all (ruling 27), the Brave-only chain's 422 pinned as `reason == f"brave:
  {failure_class}"` (spec 2 US-003), and the outbound query truncated to
  `search_brave_query_max_chars` before egress (ruling 30).
- `search_unavailable` is already in `SearchErrorCode`, declared on `/search`, and
  `tests/golden/contract_1_2_0.json` exists (spec 1 US-004, ruling 14).
- The consumer treats `provider_used`, `fallback_fired`, `provider_errors`, `engine` and `domain`
  as provenance signals, never as trust decisions.
- Naming a paid provider in `FORAGE_SEARCH_PROVIDERS` is the operator's consent for query text to
  leave the host whenever the free path fails; spec 5 US-001's env-contract docs state it.

## Technical Considerations

- **No in-request retries (ruling 18), and the latency that buys.** Each provider is awaited at
  most once per request, so the worst-case traversal is the **sum of the per-provider
  timeouts**: for `searxng,brave` that is SearXNG's fixed 10 s plus
  `search_brave_timeout_seconds` (default 15 s, ceiling 60 s) — 25 s by default, 70 s at the
  ceiling — then sanitization, before a 422. The exact trigger is the rotted-SearXNG shape
  US-002 is written against, so it is not rare, and with no cross-request breaker (Out of Scope)
  every request in an outage pays it.
  `/search` has no admission control (`ExtractionAdmissionMiddleware` gates `/extract` only) and
  no concurrency cap, so concurrent slow-path requests are bounded only by the ASGI server. No
  overall request deadline is added; the per-provider timeouts are the bound, and the operator
  guidance is one sentence US-002 owns in `docs/configuration.md`: a caller's `/search` timeout
  must exceed the sum of the configured chain's per-provider timeouts, and the lever is lowering
  `search_brave_timeout_seconds`, not raising the caller's. The fallback path knowingly exceeds
  the repo's own `_TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS` (5 s, `pipeline/orchestrator.py:569`)
  by 5×; traversal duration is deliberately not timed in this epic, and that constant plus the
  `search_promptguard_local_latency_target_exceeded` WARNING are the pattern a later epic
  extends if it adds a traversal timer. `kit_tools/arch/patterns/ERROR_HANDLING.md`'s "retries
  only for background acquisition" rule stands, and `model_fetcher.py`'s retry helpers stay
  where they are.
- **The cost guard is per-request and there is no ceiling (ruling 12).** Failure-class
  discrimination stops one query from buying a paid call; it does nothing against volume. A flood
  against the unauthenticated `/search` that rate-limits the free path makes every subsequent
  request a paid call until the limiter clears. The observability floor is `search.paid_calls` and
  `search.fallback_fired`; the deployment posture is the control: a key-bearing Forage must not
  be reachable by untrusted callers (private network only — no auth exists). Network placement
  bounds *inbound* reach only, never outbound egress: the egress bound is spec 2's
  `search_brave_query_max_chars` (ruling 30), applied inside the paid provider.
- **The classification input is the provider's own envelope**, accepted deliberately by ruling
  17: `unresponsive_engines` is what SearXNG asserts, and a hostile upstream engine can influence
  it. The exposure is bounded by "one paid call per request per paid provider" in cost terms; in
  confidentiality terms a persistently degraded free provider — a non-empty list on every
  response — routes every query to the paid provider, and query text leaves the host on each.
  Spec 4's `allow_paid_fallback` is the per-request opt-out; until it lands, the
  message-carried `search_provider_failed` WARNING and `search.fallback_fired` are what tell an
  operator it is happening. The rule also has a false-negative side: an upstream whose CAPTCHA
  pages SearXNG parses to zero results *without* marking the engine unresponsive reads as a
  clean zero and serves a 200 with no fallback (`searxng_smoke.py::evaluate_live_results` keys
  on empty results alone). Ruling 17 accepts that: a clean zero is never a paid call.
- **The new fields are reconnaissance-grade**: `provider_errors` and the 422 reason let a caller
  enumerate the chain and confirm a paid key is attached, and the unauthenticated `/metrics`
  counters expose cumulative paid-call volume and other callers' activity to anyone who can
  reach the port without issuing a search. This is the fail-loud posture working as intended,
  identical to what spec 4 publishes on `/health` and to ruling 15's deliberate key-presence
  disclosure; its safety rests on network placement, not secrecy.
- **Three rotations.** US-001, US-002 and US-003 each edit `pipeline/orchestrator.py`, so each
  rotates `sanitizer_revision` if landed separately; each rotation cold-starts the `/retrieve`
  content cache (the revision is a cache-key input since `forage-cache-fallback` US-003). Record
  each in `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md` (rulings 11
  and 32). US-004 touches no hashed source unless its proof finds a bypass, in which case its
  fix rotates too and is recorded the same way.
- **Contract holds at 1.2.0.** Only US-003 regenerates; `contract_1_2_0.json` is regenerated in
  place because nothing publishes before spec 5's `v1.1.0` cut. `provider_used` and `domain` are
  required on the server model because the orchestrator always knows them; `fallback_fired` and
  `provider_errors` default to their truthful "nothing happened" values.
- **Closed vocabulary on the wire.** The `search_unavailable` reason and `provider_errors` are
  composed only from `provider.name` and `failure_class`; `ProviderFailure.detail` reaches the
  message-carried `search_provider_failed` WARNING and nothing else. Query text, provider URLs
  and exception text never reach a response body, a log line or `/metrics` on this path; the
  one retained echo is the SearXNG base URL in the `[searxng]`-chain `searxng_unavailable`
  reason, spec 1 US-002's documented caveat.

## Related Documentation

- Poppy `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (F4/F5/F6); landscape finding on cost-exhaustion +
  snippet injection.
- `kit_tools/docs/GOTCHAS.md` "SearXNG :latest rots" (the failure shape US-002 is written
  against) and "Nothing configures logging"; `kit_tools/arch/patterns/LOGGING.md` (why `extra=`
  is invisible in-container); `kit_tools/docs/API_GUIDE.md` (`/search` fields);
  `kit_tools/docs/MONITORING.md` (`search` metrics table); `kit_tools/arch/patterns/
  ERROR_HANDLING.md` (Retry and Timeout Table, Degradation Matrix, the closed-vocabulary rule);
  `kit_tools/arch/SERVICE_MAP.md` (SearXNG entry, contract anchor); `kit_tools/arch/SECURITY.md`
  (exception-text-on-the-wire limitation); `docs/configuration.md` (caller-timeout guidance).
- `contract/GOVERNANCE.md` (rulings (b) and (c)); `docs/bootstrap-notes.md`, `CLAUDE.md` and
  `kit_tools/arch/DECISIONS.md` (rotation records); `tests/test_contract_errors.py` (raise-site
  sweep); `tests/test_contract_metrics.py` (the two `/metrics` key-order guards);
  `tests/fakes.py` (`FakeSearchProvider`).

## Refinement Notes

US-002 is the load-bearing security/cost story, and the validation pass showed why it had to be
rewritten around the actual SearXNG envelope rather than HTTP status codes: the failure the epic
exists to fix arrives as a 200. US-001 shrank when ruling 14 moved the `search_unavailable`
vocabulary entry to spec 1 — it is now traversal plus the raise. US-003 grew the `/metrics` half
(ruling 21) because without a ceiling the counters are the only aggregate spend signal a
standalone operator gets. US-004 is tests-first and claims routing parity, not detection
efficacy.

Round 2 settled identity and totality. Ruling 23 fixed one identifier per provider (`name` is
the token, `engine` is provenance), which removed the two definitions of `provider_used` and the
false claim that spec 2 pinned its reason under the engine name. Ruling 28 put the predicate on
the configured chain, threaded as `configured_chain` by spec 4, so no status code follows a
request parameter. `execution_order` runs US-003 before US-002 because US-002's criteria assert
`provider_used`, `fallback_fired` and `provider_errors`, which US-003 adds; nothing in US-003
depends on US-002. Ruling 25 left the paid candidate budget in spec 1 US-002 and this spec only
references it; ruling 27 made providers never raise, and the orchestrator guards anyway so the
loop is total. The `search_provider_failed` line became message-carried because `extra=` is
invisible in-container and it is the only place `detail` is observable; the frozen `[searxng]`
200 got its new fields pinned; the `/metrics` guards were corrected to the two key-order tests;
the doc fan-out absorbed "No fallback engine", "One request, 10 s timeout, no retries", the
`SERVICE_MAP.md:202` anchor and the three rotation tables (ruling 32); and US-004 fixes a found
bypass in-story rather than filing it.

## Clarifications

_None outstanding._

## Open Questions

_None (session_ready)._
