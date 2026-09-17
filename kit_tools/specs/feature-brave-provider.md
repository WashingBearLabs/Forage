<!-- Template Version: 2.5.0 -->
---
feature: brave-provider
status: active
session_ready: true
depends_on: [search-provider-abstraction]
vision_ref: "T2.1 — Search-provider abstraction & reliable search"
type: epic-child
size: M
epic: search-providers
epic_seq: 2
epic_final: false
execution_order: [US-010, US-011, US-002, US-003, US-012, US-013]
created: 2026-09-14
updated: 2026-09-16
---

# Feature Spec: Brave Search Provider

> **Epic 2 of `epic-search-providers`.** Add the first paid backend — a `BraveApiProvider` over
> Brave's LLM-Context (chunks) endpoint — behind the spec-1 `SearchProvider` seam, registered only
> when `FORAGE_BRAVE_API_KEY` is set so the key-less SearXNG floor is untouched. Brave is exercised
> only as `chain[0]` here (`FORAGE_SEARCH_PROVIDERS=brave`); `FORAGE_SEARCH_PROVIDERS=searxng,brave`
> is a registration-only assertion until spec 3 makes the chain traverse. **US-001 carries this
> spec's one human gate** (epic ruling 24): the owner captures the Brave sample before execution
> starts; if the fixture is absent, execution stops at US-001 and reports. Context: Poppy's
> `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (row F3). **Brave has no free tier (Feb 2026); it is the paid
> backend at $5/1,000 queries — no spec text may imply otherwise.**

## Overview

Brave Search's LLM-Context endpoint returns ranked extracted **content chunks** rather than one-line
SERP snippets (owner decision 2). This spec implements `BraveApiProvider` against the seam spec 1
defined — `name = "brave"`, `paid = True`,
`async def search(query, max_results) -> ProviderSearchResult | ProviderFailure` — normalizes
Brave's response into the `{title, url, content, engine, date}` result dicts the sanitization loop
already consumes, with `content_kind = "chunk"` and `engine = "brave-api"`, registers the provider
only when its key is present, and maps every Brave outcome to a `ProviderFailure` class from ruling
13's closed set so that a failed Brave-only chain is a loud **422 `search_unavailable`** (ruling 8;
the code landed in spec 1 US-004 per ruling 14), never a 200 with an empty list.

**Two identifiers, stated once (ruling 23).** `BraveApiProvider.name == "brave"` is the chain
token — the value `FORAGE_SEARCH_PROVIDERS`, `/health` `search_providers`, per-request `providers`,
`provider_used`, `provider_errors` and the `search_unavailable` reason (`brave: auth`) all carry.
The per-result `engine == "brave-api"` is *provenance*, and is deliberately not `brave` because
SearXNG already has a sub-engine literally named `brave`; the two must never be normalized into
each other. Nothing else names the provider.

The Brave API surface is **pinned from one real response envelope** captured by the owner outside
the test suite (ruling 24). The suite is hermetic (an autouse `pytest-socket` guard), so a parser
and a mock written from the same guess would pass every gate and return nothing against the live
API; the saved envelope is what stops that. The envelope-only rule is also what reconciles the
fixture with owner decision 6: the committed file keeps Brave's field names, nesting, types and
counts, but every chunk body, title, URL and date is replaced by an obviously synthetic value, so
no Brave-authored text is committed — and none is persisted at runtime either, because search
results are never cached (ruling 11), which this spec pins with a test rather than a per-provider
caching flag (ruling 22).

## Goals

- A `BraveApiProvider` over the LLM-Context endpoint whose parser and mock fixture both derive from
  the owner-captured envelope; results normalized to the spec-1 shape with `content_kind="chunk"`,
  `engine="brave-api"`, `date` through spec 1's ISO-8601 validator; the request asks Brave for
  exactly `request.num_results` results — the paid candidate budget spec 1 US-002 wrote (ruling
  25), asserted here.
- A hardened, bounded client: constant `https://` endpoint, `trust_env=False`,
  `follow_redirects=False`, default TLS verification; the Brave timeout, the per-result chunk cap
  and the outbound query cap read from `config.yaml` (rulings 9 and 30); the response body bounded
  before any JSON parse.
- Env-gated registration on `FORAGE_BRAVE_API_KEY` through one shared presence helper,
  `brave_key_present()`, read once in the lifespan through the lifespan-or-fallback pattern; an
  absent key means SearXNG-only with a WARNING, never an error; the variable and the three
  `config.yaml` keys are documented in the same change that reads them.
- Brave outcomes mapped to `ProviderFailure` classes with a catch-all (ruling 27); a failed
  Brave-only chain is 422 `search_unavailable` with reason `brave: <failure_class>`; the key is
  provably absent from logs, wire bodies, `/metrics` and exception text on every failure path;
  chunk sanitization parity; `/search` never writes to the content cache.

## User Stories

### US-001: [SPLIT — see US-010, US-011]

> Split by supervisor: Retries exhausted at the M-size 900 s budget: attempts 1 and 3 timed out during implementation, attempt 2 finished with two minutes to spare and was judged correct in substance, failing verification only on test-hardening details. Scope too large for one session — split into the provider module + core tests (US-010) and the bound/budget/provenance tests + doc rows (US-011). The owner gate is already satisfied at 9794cba.

### US-010: `BraveApiProvider` core — client, settings, parser over the pinned sample (split of US-001, part 1)

**Priority:** P1

**Description:** As an operator with a Brave key, I want Forage to query Brave's LLM-Context endpoint and return its chunks in the standard result shape so they flow through the same sanitization pipeline as SearXNG results. This is the first half of the original US-001, split by the supervisor after the 900 s session budget was exhausted twice: it delivers the **complete provider module** and its **core tests**; US-011 carries the remaining hardening tests and the doc rows. **The owner gate (ruling 24) is already satisfied** — `tests/fixtures/brave/llm_context_sample.json`, its `tests/fixtures/README.md` section and the `kit_tools/arch/SECURITY.md` "Fixtures" sentence were committed at `9794cba`. Do not modify the fixture or its provenance note. Budget the session: implement the module fully, write only the tests this story's criteria name, and stop.

**Independent Test:** Construct `BraveApiProvider` directly (a test key plus settings built from a `config.yaml` dict) and patch `pipeline.search_providers.brave.httpx.AsyncClient` to answer with the saved envelope `tests/fixtures/brave/llm_context_sample.json`. `provider.search("q", 5)` returns a `ProviderSearchResult` whose every dict carries `engine="brave-api"`; `run_search_pipeline` with the provider injected as `chain[0]` through the chain parameter spec 1 US-003 gave it (not through `FORAGE_SEARCH_PROVIDERS`, which is US-002's registration work) calls `provider.search(query, request.num_results)` and returns sanitized results with `content_kind="chunk"` and `title`/`url`/`snippet`/`date` populated from the envelope's synthetic values — the wire `snippet` being the chunk body carried as the internal `content` key — with at least one result not omitted.

**Acceptance Criteria:**
- [x] Pre-flight gate (already satisfied at `9794cba`): `tests/fixtures/brave/llm_context_sample.json` and its `tests/fixtures/README.md` section are present and left byte-identical by this story.
- [x] A test in `tests/test_brave_provider.py` walks `tests/fixtures/` for `X-Subscription-Token` / `Authorization` and walks `tests/fixtures/brave/` for a token-shaped literal (modelled on `test_no_token_shaped_literal_anywhere`, scoped so the pre-existing model fixtures' long identifiers cannot trip it); the `kit_tools/arch/SECURITY.md` "Fixtures" paragraph already names the fixture.
- [x] The wire names (endpoint path, auth header, result-budget parameters, response envelope) are recorded in this spec's Implementation Notes exactly as the sample shows them, extending the existing 2026-09-16 note and claiming only what the capture (`q` and `count=3`) actually confirmed — `maximum_number_of_urls` and the per-source parameters are docs-derived names the capture did not exercise, and the note says so; a test parses the fixture end-to-end through `BraveApiProvider.search()`.
- [x] `BraveApiProvider.name == "brave"` — the same string `build_provider_chain`'s registry keys it under (US-002) — and `paid is True`; `search(query, max_results)` returns a `ProviderSearchResult` with `content_kind="chunk"` whose every dict carries `engine="brave-api"` and `{title, url, content, date}` from the sample; `repr(provider)` and `str(provider)` do not contain the key.
- [x] With the provider injected as `chain[0]`, `run_search_pipeline` returns `SearchResult`s with `content_kind="chunk"`, `engine="brave-api"`, and `snippet` equal to the sanitized chunk `content` (assert equality, not `startswith`), with at least one result not omitted; a chunk whose date is not an ISO 8601 calendar date — including a regex-shaped but invalid one such as `2026-02-30` — yields `date=None` through `run_search_pipeline`.
- [x] A test captures the `httpx.AsyncClient` constructor kwargs and asserts `trust_env=False`, `follow_redirects=False`, a `verify` value that is an `ssl.SSLContext`, and `timeout=search_brave_timeout_seconds`; the request goes to `_BRAVE_LLM_CONTEXT_URL` (scheme, host and path), and the key appears only in the auth header.
- [x] `search_brave_timeout_seconds`, `search_brave_chunk_max_chars` and `search_brave_query_max_chars` ship in `config.yaml` with a comment and are read by `brave_settings_from_config` with the stated defaults and ranges; the helper is called unconditionally in the lifespan beside `cache_settings_from_config`, and an out-of-range value refuses boot (a test boots the lifespan with a chain that omits `brave`).
- [x] The provider implements the body bound (`_BRAVE_MAX_RESPONSE_BYTES`, streamed with a running byte cap before any `json.loads`), the outbound query truncation to `search_brave_query_max_chars`, the per-chunk cap `search_brave_chunk_max_chars`, and maps at most `max_results` sources; the exhaustive tests for those bounds and for the `max_results` budget belong to US-011, and this story leaves `pipeline/orchestrator.py` byte-identical.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

**Implementation Hints:**
- **Wire names — pinned by the sample.** Endpoint `GET https://api.search.brave.com/res/v1/llm/context`; auth header `X-Subscription-Token`; the capture used only `q` and `count=3`. Brave's docs also list `maximum_number_of_urls` (default 20, max 50) for the result budget and `maximum_number_of_snippets_per_url` / `maximum_number_of_tokens_per_url` for the per-source payload — docs-derived, not capture-confirmed; say so in the Implementation Notes rather than claiming the capture verified them. Observed envelope (see the 2026-09-16 Implementation Note and `tests/fixtures/README.md`): top level `grounding` (with `generic`, a list of `{url, title, snippets}` where `snippets` is a list of strings, and `map`, an empty list; no `poi` key) and `sources` (a map keyed by URL, same order as `generic`, each value `{title, hostname, age, snippet}` where `age` is a **list of four strings** — long-form date, ten-character ISO date, relative "N days ago", ISO-8601 timestamp — and `snippet` is a short string). The parser follows the sample: `date` comes from the ten-character ISO element of `age`; tolerate an absent `poi`; ignore `map`. Brave bounds `q` (documented at 400 characters plus a word limit); ruling 30's cap is sized to it.
- New `pipeline/search_providers/brave.py` implementing the spec-1 `SearchProvider`: `name = "brave"`, `paid = True` (ruling 23; the `engine` value is the only place `brave-api` appears). Hold the key in a non-repr field (`dataclasses.field(repr=False)` or the equivalent) and define `__repr__` to emit the name only. The key travels only in the auth header, never in the URL or query string, so no URL-bearing log line can carry it.
- The client mirrors `pipeline/stage5_url_audit.py` (~136): `httpx.AsyncClient(timeout=<brave timeout>, follow_redirects=False, trust_env=False, verify=ssl.create_default_context())` against a module constant `_BRAVE_LLM_CONTEXT_URL: Final` (an `https://` URL; no env override — the constant is also the single patch seam for tests). `trust_env=False` keeps ambient `HTTPS_PROXY`/`.netrc` away from a credential-bearing call — the Brave call needs direct HTTPS egress and ignores proxy variables by design (US-002's doc row says so); `follow_redirects=False` keeps httpx from replaying a custom auth header to a redirect target.
- **Bound the body before parsing**, mirroring stage 5 (`stage5_url_audit.py` ~200–215): use `client.stream(...)`, fast-reject on `Content-Length` over `_BRAVE_MAX_RESPONSE_BYTES: Final = 1_048_576`, then stream `aiter_bytes()` (decoded bytes, so a compressed body cannot expand past the cap) with a running byte cap, and only then `json.loads`. An over-cap body is `ProviderFailure(failure_class="hard_error", detail="body_too_large")`; US-003 owns the full mapping. The constant must be at least ten times the captured 30,344 bytes.
- **Outbound query bound (ruling 30).** The provider truncates *its outbound copy* of `query` to `search_brave_query_max_chars` before egress. `SearchRequest.query` keeps its wire shape (adding `max_length` would be a MAJOR), so the bound lives here and is no contract change.
- **Tunables live in `config.yaml`** (ruling 9) as three top-level scalars (the `promptguard_threshold` shape, not a block), read from the `_load_config()` dict by a `brave_settings_from_config(config)` helper in `brave.py` on the `cache_settings_from_config` model: `search_brave_timeout_seconds` (float, `1.0` to `60.0`, default `15.0`), `search_brave_chunk_max_chars` (int, `200` to `2000`, default `2000`) and `search_brave_query_max_chars` (int, `50` to `400`, default `400`). The helper is called **unconditionally** in the lifespan beside `cache_settings_from_config` (`retrieval_app.py` ~1086) so a wrong-typed or out-of-range value refuses boot on every deployment, chained or not, exactly as the `cache:` block does.
- **The chunk cap, reconciled with spec 1.** Providers never normalize, bound or scan *text on its way to the wire* — the orchestrator loop is the only path there. This cap is a *payload bound* like `_BRAVE_MAX_RESPONSE_BYTES`: the provider applies `search_brave_chunk_max_chars` to its own raw response before the dict leaves the provider. The loop still NFC-normalizes, whitespace-collapses and truncates every `content` to `_MAX_SEARCH_SNIPPET_LENGTH = 2_000` (`pipeline/orchestrator.py` ~567, applied ~724), then scans it; the range's ceiling equals the loop constant so the two bounds can never disagree.
- **`max_results` (ruling 25).** `run_search_pipeline` chooses `max_results = request.num_results if provider.paid else fetch_limit` — written by spec 1 US-002, so this story edits no `_REVISION_SOURCES` file. The Brave request carries `max_results` in its result-budget parameter(s); only the first `max_results` sources of a response are mapped. US-011 asserts this with a spy.
- **Mapping.** One result dict per `grounding.generic` element (one per source URL): `{title, url, content, engine: "brave-api", date}` with `content` the element's `snippets` joined in order by a blank line and capped as above, and `date` from the ten-character ISO element of `sources[url].age`, else `None`. A missing or non-string field maps to `""` (`date` to `None`) and the loop's existing omission rules decide; an element that is not a JSON object is skipped (record that choice in the Implementation Notes). `date` goes through spec 1's ISO-8601 calendar-date validator (ruling 19). Set `content_kind="chunk"` on the `ProviderSearchResult`, where spec 1 US-004 reads it. This story does not edit the loop and adds no wire field, so there is no contract regeneration.
- **Tests** go in a new `tests/test_brave_provider.py` (CONVENTIONS naming; US-011 adds its TESTING_GUIDE row). For the constructor-kwargs test follow `tests/test_stage5_url_audit.py::TestTimeoutEnforcement::test_timeout_propagated` (~471): patch `pipeline.search_providers.brave.httpx.AsyncClient`, read `call_args.kwargs`. Copy `_make_stream_cm` / `_stream_side_effect` / `_make_response` (`tests/test_stage5_url_audit.py:43–82`) into the new module for the streamed-envelope fixture path. `tests/test_orchestrator.py` is the reference for the end-to-end `run_search_pipeline` assertions. The boot-refusal test patches `_load_config` (or the config path) to return an out-of-range value and asserts the lifespan raises, with a chain that omits `brave`.

### US-011: `BraveApiProvider` hardening tests, budget assertions and doc rows (split of US-001, part 2)

**Priority:** P1

**Description:** Second half of the original US-001 (split by the supervisor). US-010 delivered `pipeline/search_providers/brave.py` and its core tests; this story pins the three payload bounds, the candidate budget and the engine-provenance rule with tests, and lands the documentation rows. It changes no provider behaviour unless a test exposes a defect (fix minimally in `brave.py` and say so in the Implementation Notes), and it leaves `pipeline/orchestrator.py` byte-identical.

**Independent Test:** `uv run pytest tests/test_brave_provider.py` exercises every case below against the US-010 module with `pipeline.search_providers.brave.httpx.AsyncClient` patched, and the three doc tables carry their rows.

**Acceptance Criteria:**
- [x] With the provider as `chain[0]`, a provider spy sees `provider.search(query, request.num_results)` (not `min(num_results * 2, 20)`) for `num_results` of `1`, `5` and `20`, and the mocked outbound request's result-budget parameter(s) carry the same value; a response carrying more sources than `max_results` yields at most `max_results` dicts; `pipeline/orchestrator.py` is byte-identical to its US-010 state (the branch is spec 1 US-002's).
- [x] A response whose `Content-Length` exceeds `_BRAVE_MAX_RESPONSE_BYTES`, a streamed body that exceeds it with **no** `Content-Length` header (the test asserts `response.headers.get("content-length") is None` so the precondition cannot silently regress), and a compressed body whose decoded length exceeds it are all abandoned before any `json.loads` call (asserted on a `json.loads` spy) and return a `ProviderFailure`; `_BRAVE_MAX_RESPONSE_BYTES` is at least ten times the raw byte size the provenance note records (30,344). US-003 pins the `hard_error` / `body_too_large` pair.
- [x] A 50,000-character chunk leaves the provider truncated to `search_brave_chunk_max_chars`; a 5,000-character query leaves the provider truncated to `search_brave_query_max_chars` (the mocked request's `q` has exactly that many characters) while the wire request is accepted unchanged (no 422).
- [x] A SearXNG result with `engine="brave"` and a Brave result with `engine="brave-api"` stay distinct through the loop, asserted in both directions in one test — a SearXNG-side provider (a `FakeSearchProvider` or a mocked `SearxngProvider`) returning `engine="brave"` yields a wire result whose `engine == "brave"`, and the Brave-served result's `engine == "brave-api"` — so neither direction of normalization can pass; `kit_tools/docs/API_GUIDE.md`'s `results` row names both values.
- [x] `kit_tools/arch/INFRA_ARCH.md`'s egress table (~245–248) carries the Brave endpoint row (per `/search` request, only when `FORAGE_BRAVE_API_KEY` is set, direct HTTPS with no proxy); `kit_tools/arch/CODE_ARCH.md`'s module table carries `pipeline/search_providers/brave.py` beside spec 1's package row; `kit_tools/testing/TESTING_GUIDE.md`'s module map lists `tests/test_brave_provider.py`.
- [x] This spec's Implementation Notes record any bound-test learning and any minimal `brave.py` fix the tests forced.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

**Implementation Hints:**
- **Body-bound tests.** Use the streaming precedent, not `tests/test_orchestrator.py`'s `client.get()` helpers: `tests/test_stage5_url_audit.py:69–82` (`_make_stream_cm`, `_stream_side_effect`, `_make_response` at ~43 — US-010 copied them into `tests/test_brave_provider.py`) and `TestStreamingByteCap` (~526) as the template. For the no-`Content-Length` case build the response so it truly has no such header — `httpx.Response(200, stream=<an httpx.AsyncByteStream subclass yielding chunks>, request=...)`, or delete the header after construction — and assert `response.headers.get("content-length") is None` inside the test. For the compressed case, `httpx.Response(content=<gzip-compressed bytes>, headers={"content-encoding": "gzip", ...})` decodes through `aiter_bytes()` even when constructed manually, which is what makes a realistic "decoded length exceeds the cap" test possible. Spy on `pipeline.search_providers.brave.json.loads` and assert it was never called in all three cases.
- **Budget spy (ruling 25).** Inject a spying provider as `chain[0]` and drive `run_search_pipeline` with `num_results` 1, 5 and 20; assert the spy's `max_results` argument equals `request.num_results` each time and that the mocked outbound request's result-budget parameter(s) carry the same value; feed an envelope with more sources than `max_results` and assert the dict count. Assert `pipeline/orchestrator.py` is unchanged by comparing its bytes (or `git diff --stat`) against the US-010 commit.
- **Caps.** Patch the envelope with one 50,000-character chunk body and assert the provider's outgoing `content` length equals `search_brave_chunk_max_chars`; send a 5,000-character query and read the mocked request's `q` from the patched client's call (query params), asserting its length equals `search_brave_query_max_chars` and that the request was accepted (no 422 from the app when driven end-to-end).
- **Provenance test, both directions.** Run `run_search_pipeline` once with a SearXNG-shaped provider returning a result with `engine="brave"` and once with the Brave provider; assert the two wire `engine` values verbatim. The point is that neither the loop nor the provider normalizes `brave` to `brave-api` or back.
- **Docs.** INFRA_ARCH egress table: host `api.search.brave.com`, direct HTTPS (`trust_env=False`, no proxy), only when `FORAGE_BRAVE_API_KEY` is set, one request per `/search`. CODE_ARCH module table: `pipeline/search_providers/brave.py` beside the spec-1 package row. API_GUIDE `results` row: `engine` is `searxng`'s engine name or `brave-api`. TESTING_GUIDE module map: `tests/test_brave_provider.py` with a one-line summary; leave the suite-count totals to spec 5 US-003 (ruling 32).


### US-002: Env-gated conditional registration (key-less floor)

**Priority:** P1

**Description:** As an operator, I want Brave to activate only when I have supplied a key, so a
deployment without one keeps working on free engines with no error and no new required secret — and
I want the variable documented the day it ships.

**Independent Test:** With `FORAGE_SEARCH_PROVIDERS=searxng,brave` and no `FORAGE_BRAVE_API_KEY`,
the lifespan resolves the chain to `[SearxngProvider]`, `/search` works, and `caplog` (at WARNING)
holds exactly one `brave_skipped_missing_key` record that names `FORAGE_BRAVE_API_KEY` and does not
contain a sentinel key value. With the key set, the chain is `[SearxngProvider, BraveApiProvider]`
and the registered provider carries the non-default `search_brave_timeout_seconds` the test's
`config.yaml` dict set. With `FORAGE_SEARCH_PROVIDERS=brave` and no key, the chain is
`[SearxngProvider]` and a second WARNING `search_chain_defaulted_to_searxng` is emitted. With the
key set and `FORAGE_SEARCH_PROVIDERS=searxng`, the chain is `[SearxngProvider]` and `caplog` holds
no Brave record.

**Implementation Hints:**
- `BRAVE_API_KEY_ENV_VAR: Final = "FORAGE_BRAVE_API_KEY"` in `brave.py`, on root-level
  `model_fetcher.py`'s `*_ENV_VAR` constant pattern (~179–183). **No alias** (ruling 10), no vault,
  no runtime key API — Forage is 12-factor and the consumer injects the key into the environment.
- **One presence helper, shared (ruling 28).** `brave_key_present(raw: str | None) -> bool` in
  `brave.py` is the single definition of "a key is present": `raw.strip()` is non-empty,
  ASCII-only and printable, with no interior whitespace or control character. Registration here
  and `/health`'s `capabilities["brave_api_key"]` in spec 4 US-002 both consume it, so the two can
  never disagree. Why each clause: empty after strip is absent (compose routinely renders
  `FORAGE_BRAVE_API_KEY=` from an unset shell variable, and file-backed secrets carry a trailing
  newline — the `model_fetcher._resolve_token()` rule, ~912); a CR/LF in a header value is a
  client-side construction exception rather than an HTTP outcome, and its message could carry the
  value; httpx encodes header values as latin-1, so a non-ASCII key (a pasted smart quote or em
  dash) would raise `UnicodeEncodeError` at request construction with the offending character
  quoted in the message. All three shapes are refused here, before any client exists.
- The lifespan's `_resolve_brave_key()` (beside `_configured_provider_names()`) reads the variable
  exactly once: it returns the stripped value when `brave_key_present` holds; when the stripped
  value is non-empty but fails the predicate it logs `brave_key_invalid` (WARNING, naming the
  variable, never the value — the `model_revision_invalid` / `weights_mirror_invalid` precedent,
  `model_fetcher.py` ~898 and ~1003) and returns `None`; otherwise `None`.
- **Read once, in the lifespan**, next to where spec 1 US-003 reads `FORAGE_SEARCH_PROVIDERS`
  (`retrieval_app.py` lifespan ~1052–1100), through the lifespan-or-fallback pattern (ruling 22):
  the lifespan publishes the resolved chain on `app.state`; the module-scope mirror (~1182–1200,
  the `model_task`/`model_acquisition` precedent) declares the attribute so the six lifespan-free
  `ASGITransport` clients keep working; requests read it through `_resolved_search_providers()`
  (~198–221). The fallback path builds the default one-element chain holding
  `SearxngProvider(DEFAULT_SEARXNG_URL)` (ruling 28) and never reads the key or the Brave settings;
  no request path reads `os.environ` for it. Not the import-time `SEARXNG_URL` read at
  `retrieval_app.py:82`.
- **Registration and settings plumbing.** Extend spec 1 US-003's registry call to
  `build_provider_chain(names, *, searxng_url, brave_api_key, brave_settings)`; the lifespan calls
  `brave_settings_from_config(config)` unconditionally beside `cache_settings_from_config` (~1086)
  and passes the result in, so the `config.yaml` knobs reach the provider the lifespan actually
  builds and cannot ship dead. `"brave"` with a present key registers
  `BraveApiProvider(key, settings)`; `"brave"` without one is skipped with one WARNING,
  `brave_skipped_missing_key — no FORAGE_BRAVE_API_KEY in the environment, so the paid provider is
  not registered`, modelled on `model_fetcher.py`'s `weights_fetch_skipped` (~1693–1699). WARNING,
  not INFO: the root logger sits at WARNING and INFO is invisible in the container
  (`kit_tools/docs/GOTCHAS.md`). Never raise for a missing key. If the resulting chain is empty,
  use `[SearxngProvider]` and emit a second WARNING `search_chain_defaulted_to_searxng` so the
  substitution is visible — log-only until spec 4 US-002 puts `search_providers` on `/health`,
  where ERROR_HANDLING's three-channel rule is satisfied. An unknown provider name still refuses
  boot (spec 1 US-003). A present key with `brave` absent from the chain registers nothing and
  logs nothing: an unused key is not a misconfiguration, and spec 4's `capabilities` entry is
  where it becomes visible.
- Add `FORAGE_BRAVE_API_KEY` to `_CLEARED_ENV_VARS` in `tests/conftest.py` and to the
  cleared-variable sentence in `kit_tools/docs/ENV_REFERENCE.md` (~71). **Required edit:**
  `tests/test_hermeticity.py::test_the_cleared_environment_is_the_expected_exact_set` pins that
  tuple as an exact set (ruling 26b) and goes red the moment a name is added; add the name there
  in the same change.
- **Docs in the same change** (the AGENT_README session-end rule): a `FORAGE_BRAVE_API_KEY` row in
  `docs/configuration.md`'s variable table (~83–91: default unset; "carries a credential — supply
  it the same way as `VALKEY_URL`", with `--env-file` or an explicit `environment:` entry until
  spec 5 US-004 adds the compose passthrough; with it set, the caller's query text — whatever the
  calling agent put in it, truncated to `search_brave_query_max_chars` — leaves the deployment to
  Brave's API under the operator's account and terms; the call needs direct HTTPS egress and
  ignores proxy variables by design; a key-less `brave` entry is skipped and an all-skipped chain
  falls back to SearXNG with the `search_chain_defaulted_to_searxng` marker; no key means
  SearXNG-only, fully supported), and in `kit_tools/docs/ENV_REFERENCE.md`'s per-variable table
  (~38–52: no alias, secret **yes**, read once by the lifespan, unset → provider skipped with
  `brave_skipped_missing_key`), plus a row in `kit_tools/arch/SECURITY.md`'s secrets inventory
  (~214–221). The three `config.yaml` keys get rows in **both** `config.yaml` references — the
  "Top-level keys" table in `docs/configuration.md` (~347; Type / Code default / Shipped /
  Purpose, and that an out-of-range value refuses boot — that file claims completeness) and
  ENV_REFERENCE's `config.yaml` table (~81–87) — with the note that `search_brave_timeout_seconds`
  is `/search`'s worst-case latency on a Brave-only chain until spec 3's fallback exists. Spec 5
  US-001 owns the `README.md` prose and the credential-handling subsection; spec 5 US-004 owns
  compose passthrough and the CI secret-grep patterns (ruling 20 c–d).

**Acceptance Criteria:**
- [x] `FORAGE_BRAVE_API_KEY` is read exactly once per process, in the lifespan, via
      `BRAVE_API_KEY_ENV_VAR`; a test sets the variable after startup and asserts the resolved
      chain is unchanged; the chain attribute is declared in the module-scope `app.state` mirror
      and the existing lifespan-free route tests pass unchanged.
- [x] `brave_key_present()` is defined once in `pipeline/search_providers/brave.py` and is the
      only presence rule: `""`, `"  "`, `"ke y"`, `"key\r"` and a key containing a non-ASCII
      character are not present; `"key\n"` is present and resolves to `"key"`; a non-empty value
      that fails the predicate logs `brave_key_invalid` (variable name, never the value, never the
      offending character) and registers nothing; no `UnicodeEncodeError` can be raised by the
      provider for any environment value (test).
- [x] `"brave"` in the chain registers `BraveApiProvider` only with a present key; otherwise
      `build_provider_chain` emits exactly one WARNING containing `brave_skipped_missing_key` and
      `FORAGE_BRAVE_API_KEY`, and `caplog.text` does not contain a sentinel key value.
- [x] `build_provider_chain(names, *, searxng_url, brave_api_key, brave_settings)` is called from
      the lifespan with the result of an unconditional `brave_settings_from_config(config)`; a
      real-lifespan test with a non-default `search_brave_timeout_seconds` in the config dict
      asserts the registered provider's `httpx.AsyncClient` receives that timeout; an out-of-range
      value refuses boot on a chain without `brave`.
- [x] `FORAGE_SEARCH_PROVIDERS=searxng,brave` without a key runs SearXNG-only with no error;
      `FORAGE_SEARCH_PROVIDERS=brave` without a key resolves to `[SearxngProvider]` and emits the
      `search_chain_defaulted_to_searxng` WARNING.
- [x] With the key set and `brave` absent from `FORAGE_SEARCH_PROVIDERS`, the resolved chain
      contains no `BraveApiProvider` and `caplog` holds no Brave record (test).
- [x] `FORAGE_BRAVE_API_KEY` is in `tests/conftest.py::_CLEARED_ENV_VARS` and in the exact set
      pinned by `tests/test_hermeticity.py::test_the_cleared_environment_is_the_expected_exact_set`,
      which passes.
- [x] `docs/configuration.md` (the variable table and the `config.yaml` "Top-level keys" table),
      `kit_tools/docs/ENV_REFERENCE.md` (variable table, cleared-variable sentence, and the
      `config.yaml` table) and `kit_tools/arch/SECURITY.md`'s secrets inventory carry the new rows
      for `FORAGE_BRAVE_API_KEY`, `search_brave_timeout_seconds`, `search_brave_chunk_max_chars`
      and `search_brave_query_max_chars` (grep-verifiable); the configuration row names the proxy
      posture and the `search_chain_defaulted_to_searxng` marker.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-003: [SPLIT — see US-012, US-013]

> Split by supervisor: Retries exhausted at the M-size 900 s budget: attempt 1 timed out, attempt 2 finished and was judged correct in substance but failed verification on test shape (log-token placement, a 12-token parametrization, zero-source variants, a fail-open mock), attempt 3 exhausted the last retry. Scope too large for one session — split into failure taxonomy + 422 + key-never-leaks (US-012) and sanitization parity + no-persistence pin + operator docs (US-013).

### US-012: Brave failure taxonomy — closed-vocabulary mapping, loud 422, key-never-leaks (split of US-003, part 1)

**Priority:** P1

**Description:** As an operator, I want a Brave failure to be a loud 422 — never a 200 that looks like an empty search — with the key provably absent from every failure surface. First half of the original US-003 (split by the supervisor after its 900 s budget was exhausted): the closed-vocabulary failure mapping, the log line, the wire 422, the zero-source success, and the key-never-leaks proof. US-013 carries sanitization parity, the no-persistence pin and the operator docs. Budget the session: implement the mapping and log line, write only the tests named below, and stop.

**Independent Test:** With `BraveApiProvider` published as `chain[0]` on `app.state` and a sentinel key, mocked Brave answers of `401`, `403`, `429`, a timeout, a `503`, a truncated JSON body, a valid JSON body of the wrong shape, a `301` and an arbitrary non-httpx exception raised inside the call each make `POST /search` return 422 `{"error": "search_unavailable", "reason": "brave: <class>"}` with `auth`, `auth`, `rate_limited`, `timeout`, `hard_error`, `hard_error`, `hard_error`, `hard_error`, `hard_error` respectively; a mocked `200` with zero sources returns 200 with `results: []`. After the failures, `caplog.text`, every response body, `GET /metrics`, and `str()`/`repr()` of the `ProviderFailure` contain neither the sentinel key, nor the endpoint host, nor the injected exception's message.

**Acceptance Criteria:**
- [ ] `BraveApiProvider.search()` maps `401`/`403` → `auth`, `429` → `rate_limited`, `httpx.TimeoutException` → `timeout`, and `5xx`, other 4xx, 3xx, other `httpx.HTTPError`, non-JSON (`bad_json`), wrong-shape JSON (`malformed_body`), over-cap bodies and an arbitrary non-httpx exception (`unexpected`) → `hard_error`, each with its fixed `detail` token; `search()` raises for none of them; one parametrized test covers all twelve `detail` tokens (`http_401`, `http_403`, `http_429`, `http_4xx`, `http_5xx`, `redirect_refused`, `timeout`, `transport_error`, `bad_json`, `malformed_body`, `body_too_large`, `unexpected`) asserting class, detail, no raise, and `detail in _BRAVE_FAILURE_DETAILS`, with a set-equality check that the cases exercise every token.
- [ ] Every failure emits exactly one WARNING whose `record.getMessage()` contains `brave_search_failed` and the fixed `detail` token in the message itself (tokens as `%s` arguments, per `kit_tools/arch/patterns/LOGGING.md` ~138), and contains no `str(exc)` text, no `https://`, no endpoint host and no header value (`caplog` assertions in the same parametrization).
- [ ] A `200` with zero sources — no `grounding`, no `generic`, `generic` null, and `generic` `[]` — is a `ProviderSearchResult(results=[], unresponsive_engines=[])` asserted directly (no `ProviderFailure`), and `POST /search` returns 200 with `results == []`, `omitted_by_reason == {}`, `omitted_results == 0`, `unresponsive_engines == []`; the same three omission fields are asserted on a sample-served `POST /search` 200.
- [ ] With `BraveApiProvider` as `chain[0]`, every `ProviderFailure` makes `POST /search` return 422 `search_unavailable` with `reason == f"brave: {failure_class}"`; the raise site is in `pipeline/orchestrator.py`; `pipeline/search_providers/brave.py` contains no `PipelineError`; `tests/test_contract_errors.py`'s raise-site sweep passes.
- [ ] A sentinel key, the endpoint host, and the message of an exception injected into the catch-all path appear in no log record, no `/search` response body, no `/metrics` body, and no `str()`/`repr()` of the provider or the `ProviderFailure`, across all failure classes (test modelled on `test_connect_failure_never_logs_url_or_secret`); the test is listed in `kit_tools/arch/SECURITY.md`'s "Coverage by control" `Secrets` row.
- [ ] `docs/bootstrap-notes.md`'s latest `sanitizer_revision` record matches `derive_sanitizer_revision()` after this story (a new record only if `pipeline/orchestrator.py` changed; `brave.py` is not a `_REVISION_SOURCES` member).
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

**Implementation Hints:**
- **Mapping lives in `brave.py`**, into ruling 13's closed `failure_class` set, with `detail` one of the fixed tokens `http_401`, `http_403`, `http_429`, `http_4xx`, `http_5xx`, `redirect_refused`, `timeout`, `transport_error`, `bad_json`, `malformed_body`, `body_too_large`, `unexpected` — never `str(exc)`, never a URL. `model_fetcher._fetch_reason` (~937) and `cache._closed_vocabulary_reason` (~297) are the models. `401`/`403` → `auth`; `429` → `rate_limited` (Brave answers 429 for both a per-second limit and plan exhaustion; `quota` stays unused by this provider); `httpx.TimeoutException` → `timeout`; `5xx`, other 4xx, any 3xx (redirects are not followed), any other `httpx.HTTPError` (`transport_error` for `ConnectError`), non-JSON (`bad_json`), `grounding.generic` present but not a list or elements that are not objects (`malformed_body`, ruling 27), over-cap (`body_too_large`) → `hard_error`. The mapping ends in `except Exception` → `hard_error` / `unexpected`. A body with no `grounding` or no `generic`, or `generic` null or empty, is a **clean zero-result success**.
- **The log line.** `search()` never raises; every failure becomes a `ProviderFailure` with exactly one WARNING per failure, e.g. `logger.warning("brave_search_failed — %s (%s)", detail, failure_class)` — the tokens go in the message itself so `record.getMessage()` carries them; no exception text, URL, host or header value ever reaches the record.
- **The wire outcome.** A `ProviderFailure` from a `chain[0]` that is not SearXNG raises `PipelineError(error="search_unavailable", reason=f"{provider_name}: {failure_class}")` from `pipeline/orchestrator.py` (landed in spec 1 US-004, ruling 14); SearXNG-only chains keep `searxng_error`/`searxng_unavailable` byte-for-byte (ruling 8). `brave.py` raises no `PipelineError`. If the generic branch turns out unwritten, write it (a few lines) and record the `sanitizer_revision` rotation in `docs/bootstrap-notes.md`; otherwise `orchestrator.py` stays untouched and the revision is unchanged — confirm either way.
- **Do not fold provider failures into `omitted_by_reason`** or `unresponsive_engines` (which already include a SearXNG sub-engine literally named `brave`). No Brave-specific omission token exists.
- **Key-never-leaks**, in the style of `tests/test_cache.py::TestReconnect::test_connect_failure_never_logs_url_or_secret`: a sentinel key drives every failure class end-to-end through `POST /search`; assert absence from `caplog.text` (all loggers, WARNING), the 422 body, `GET /metrics`, and `str()`/`repr()` of the `ProviderFailure` and the provider — and the same for the endpoint host and for the exception-message surface (the catch-all case injects an exception whose message contains the sentinel key). Add the test to the `Secrets` row of `kit_tools/arch/SECURITY.md`'s "Coverage by control" table (~313).
- **Tests** extend `tests/test_brave_provider.py` using its existing stream helpers; drive the 422 cases through the app with the provider on `app.state` as `chain[0]`.

### US-013: Brave sanitization parity, no-persistence pin and operator docs (split of US-003, part 2)

**Priority:** P1

**Description:** Second half of the original US-003 (split by the supervisor). US-012 delivered the failure mapping, the log line, the wire 422 and the key-never-leaks proof; this story pins that Brave chunks are sanitized identically to SearXNG snippets, that no raw Brave body is persisted at runtime, and lands the operator documentation. It changes no provider behaviour unless a test exposes a defect (fix minimally in `brave.py` and say so in the Implementation Notes).

**Independent Test:** The same poisoned text as a Brave chunk and as a SearXNG snippet produces identical `omitted_by_reason` and `suspicious` outcomes through `POST /search`; a real `ContentCache` over `tests/fakes.py::FakeStorage` on `app.state.cache` ends a Brave-served `/search` with `get_calls == set_calls == delete_calls == 0`; the three doc files carry their rows.

**Acceptance Criteria:**
- [ ] The same poisoned text as a SearXNG snippet and as a Brave chunk yields identical `omitted_by_reason` and identical `suspicious` for structural-BLOCKED, classifier-flagged and SUSPICIOUS-only inputs (for the SUSPICIOUS-only case mock `run_promptguard` to return a scanned SAFE result with `skipped=False` so the fail-open branch cannot set `suspicious`, and add a clean-text control asserting `suspicious is False` for both providers); a poisoned Brave `title` is omitted under `structural_blocked` and a degenerate Brave `url` (`javascript:` scheme, embedded userinfo, interior whitespace) under `invalid_url`, exactly as the SearXNG equivalents (test).
- [ ] A regression test with a real `ContentCache` over `tests/fakes.py::FakeStorage` on `app.state.cache` asserts `get_calls == set_calls == delete_calls == 0` after a Brave-served `/search` whose chunk carries a unique sentinel string, and that the sentinel is absent from `caplog.text` (capture at INFO); no bare `MagicMock` stands in for the cache.
- [ ] `kit_tools/docs/TROUBLESHOOTING.md`'s error table (~173) carries the four `brave: <class>` rows with the `detail` tokens named; `kit_tools/arch/SERVICE_MAP.md` carries a `### Brave Search API` subsection under `## External Integrations` (after `### SearXNG`) with the document's attribute rows, plus a row in the `## Failure Impact Matrix` (~309); `kit_tools/arch/SECURITY.md`'s outbound-request review-checklist item (~378) carries the provider carve-out.
- [ ] `docs/bootstrap-notes.md`'s latest `sanitizer_revision` record still matches `derive_sanitizer_revision()` (this story edits no `_REVISION_SOURCES` file).
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

**Implementation Hints:**
- **Sanitization parity.** Chunks are `content` like any other result and traverse `pipeline/stage2_structural.py`, `pipeline/stage3_promptguard.py` and `promptguard/classifier.py` unchanged. Feed the same poisoned text as a SearXNG `content` and as a Brave chunk: a structurally BLOCKED text is omitted under `structural_blocked` in both; a classifier-flagged text (mock classifier) under `injection_detected` in both; a SUSPICIOUS-only text is returned with `suspicious=True` in both — mock `run_promptguard` to a scanned SAFE result (`skipped=False`, low score) for the clean control so the fail-open path cannot masquerade as suspicious. The loop scans `title` and `url` too (`orchestrator.py` ~731–742). `date` needs no scan (ruling 19).
- **ToS / no persistence.** Search results are never cached: `cache.py` is `/retrieve`-only (`ret:` keys) and `run_search_pipeline` takes no cache. Pin it (ruling 26c) with a real `ContentCache` over `tests/fakes.py::FakeStorage` — `ContentCache`'s public surface is `get` (~757), `delete` (~806) and `put` (~821, writes through `storage.set`); there is no `ContentCache.set`, which is why a bare `MagicMock` would pass unconditionally. Result URLs stay loggable on the existing omission paths (`LOGGING.md` ~137); chunk text never is.
- **Docs.** TROUBLESHOOTING rows: `brave: auth` (key wrong, revoked or not entitled — replace the key **and restart the container**, it is read once at start), `brave: rate_limited` (Brave answered 429 — retry after a pause; if it persists check the plan, since a per-second limit and plan exhaustion share the status), `brave: timeout` (raise `search_brave_timeout_seconds` or check egress), `brave: hard_error` (grep the `brave_search_failed` line for the `detail` token: `transport_error` → egress/DNS/proxy, `redirect_refused` → endpoint moved, `bad_json`/`malformed_body` → capture a fresh sample, `body_too_large` → the response bound, `http_5xx` → Brave-side, `unexpected` → a bug report). SERVICE_MAP `### Brave Search API`: purpose, client/protocol (constant endpoint, `X-Subscription-Token` header, `trust_env=False`, no redirects), configuration (`FORAGE_BRAVE_API_KEY`, the three `config.yaml` keys), timeouts and retries (`search_brave_timeout_seconds`, zero retries), health signal (none until spec 4), failure impact (`brave: <class>` 422s), rate limits (paid per query, no ceiling — ruling 12) — plus the Failure Impact Matrix row. SECURITY checklist carve-out: operator-configured or constant endpoints are exempt from `validate_url` on that basis alone, redirects never followed, results trusted by nothing — pointing at the `SearchProvider` protocol docstring. No contract field description moves.


## Edge Cases

- SearXNG's own `brave` sub-engine (in `_SEARXNG_ENGINES` and `searxng/config/settings.yml`) and
  the Brave provider's `engine` value `brave-api` must stay distinguishable in provenance — never
  normalize one into the other; the chain token is `brave` in both places (ruling 23); both
  `engine` values are documented in `kit_tools/docs/API_GUIDE.md`. US-001.
- Zero sources (valid empty) versus an error: empty is a `ProviderSearchResult` and a 200 with
  `results: []`; every failure is a `ProviderFailure` and a 422. Spec 3's fallback discrimination
  depends on it. US-003.
- A body that is not JSON → `hard_error` / `bad_json`; valid JSON whose `grounding.generic` is
  present but not a list, or whose elements are not objects → `hard_error` / `malformed_body` (a
  missing or null `generic` is a clean zero); anything the
  mapping did not foresee → `hard_error` / `unexpected` — never an unhandled exception, never
  exception text. US-003.
- An oversized response body → `body_too_large` before any parse; a chunk far larger than expected
  → capped to `search_brave_chunk_max_chars` before the loop, so PromptGuard cost is bounded.
  US-001.
- A caller query longer than `search_brave_query_max_chars` → the outbound copy is truncated
  before egress; the wire request is accepted exactly as today. A query within the cap that
  exceeds Brave's own word limit is a Brave-side 4xx → `hard_error` / `http_4xx`; Forage does not
  pre-validate words. US-001 / US-003.
- A `3xx` from the endpoint: redirects are never followed; `hard_error` / `redirect_refused`, and
  the auth header is never replayed to the `Location` target. US-003.
- A key with interior whitespace, a control character, or a non-ASCII character → not present per
  `brave_key_present()`; `brave_key_invalid` names the variable, never the value or the character;
  no header is ever constructed from it. US-002.
- Key set but `brave` absent from `FORAGE_SEARCH_PROVIDERS` → nothing is registered and nothing is
  logged (criterion); spec 4's `/health` `capabilities["brave_api_key"]` is where an unused key
  becomes visible. US-002.
- A present-but-rejected key (rotated or revoked): every `/search` on a Brave-only chain is 422
  `brave: auth` until spec 3 adds fallback and spec 4 adds `/health` status — loud by design,
  never a silent downgrade; the key is read once, so the fix is replace-and-restart. US-003.

## Out of Scope

- Chain traversal, `provider_used` / `fallback_fired` / `provider_errors`, and the `/metrics`
  counters `search.paid_calls` / `search.fallback_fired` (spec 3; ruling 21). Here Brave is used
  only as `chain[0]`.
- Per-request policy params, `/health` `search_providers` and `capabilities["brave_api_key"]`
  (spec 4 — which reuses `brave_key_present()` rather than redefining presence).
- `README.md` prose, credential-handling guidance, compose env passthrough, CI secret-grep
  patterns, and the release cut (spec 5; ruling 20).
- Any spend ceiling, budget or rate limit — ruling 12 accepts the uncapped paid path as a known
  risk; ruling 21's counters are the observability floor. Any in-request retry (ruling 18).
- A per-provider caching flag on the `SearchProvider` protocol (ruling 22): search results are
  never cached, and the no-cache-write test in US-003 replaces it.
- Raising `_MAX_SEARCH_SNIPPET_LENGTH` or adding a PromptGuard `max_chunks` budget to `/search`
  (Epic 4, the resource envelope).
- Writing the paid candidate budget branch in `run_search_pipeline` (spec 1 US-002, ruling 25) —
  this spec asserts it.
- Bounding `SearchRequest.query` on the wire (a MAJOR under GOVERNANCE); the outbound copy is
  bounded inside the provider instead (US-001, ruling 30).
- A shared bounded-JSON-fetch helper for future paid providers: one caller today, so the logic
  stays inline in `brave.py` and is factored out when a second paid provider lands.

## Assumptions

- Spec 1 has landed: the seam (`SearchProvider`, `ProviderSearchResult`, `ProviderFailure` with the
  closed `failure_class` set and the catch-all contract of ruling 27), `build_provider_chain` plus
  the lifespan-or-fallback chain read, the paid candidate budget branch in `run_search_pipeline`
  (ruling 25), `content_kind` and `date` on `SearchResult` with the loop constructing them, and
  `search_unavailable` in `pipeline/contract.py` under contract 1.2.0.
- **The owner gate has run before this spec executes** (ruling 24): the owner made one live
  LLM-Context call with a real key, supplied through the environment, and committed the envelope
  fixture and its provenance note. Execution stops at US-001 and reports if the fixture is absent,
  exactly like spec 5 US-002; nothing in the six gates ever reaches Brave.
- The consumer supplies the key through the environment (no vault in Forage).

## Technical Considerations

- **Cost.** $5/1,000 queries with a ~1k-query/mo credit and no free tier; billing is per query,
  not per chunk. This spec adds no ceiling (ruling 12) and no retries (ruling 18); a Brave-only
  chain makes at most one paid call per `/search`. The operator's spend view is
  `search.paid_calls`, landing in spec 3 US-003 (ruling 21); before that, the `brave_search_failed`
  WARNING and `/metrics` `search.errors.search_unavailable` are the only signals.
- **Privacy boundary.** With Brave configured, the caller's query text — bounded to
  `search_brave_query_max_chars`, otherwise as the calling agent wrote it — leaves the deployment
  to a commercial API from the Forage container itself, under the operator's account (every other
  search egress leaves via the companion SearXNG container). That is the trade the key buys;
  US-002 states it in the `docs/configuration.md` row and the provider module's docstring.
- **PromptGuard cost is unchanged.** The chunk cap's ceiling equals `_MAX_SEARCH_SNIPPET_LENGTH`,
  so `/search` PromptGuard inputs are no larger than today's and the uncaught
  `PromptGuardBudgetExceededError` path on `/search` stays unreachable. The "PromptGuard is stronger
  on chunks" rationale is an expectation pending Epic 6's injection corpus, not a measured finding.
- **`sanitizer_revision`.** `pipeline/search_providers/` is not hashed (ruling 11). US-001 touches
  no `_REVISION_SOURCES` file — the paid budget branch it asserts is spec 1 US-002's edit — and
  neither does US-002. US-003's orchestrator edit, if spec 1 did not already write the generic
  branch, is the only possible rotation in this spec; it is recorded like every other.
- **Logging.** The root logger sits at WARNING, so every new marker here
  (`brave_skipped_missing_key`, `brave_key_invalid`, `search_chain_defaulted_to_searxng`,
  `brave_search_failed`) is WARNING with a fixed clause and a `caplog` test, per
  `kit_tools/arch/patterns/LOGGING.md`.
- **A third Forage-read secret.** `FORAGE_BRAVE_API_KEY` joins `HF_TOKEN` and
  `FORAGE_MIRROR_TOKEN` as a runtime-only credential: it never appears in the `Dockerfile`
  (invariant 2), the fixture tree is guarded by US-001's test because no image-side guard can see
  `tests/`, and the CI secret-grep pattern lists gain it in spec 5 US-004 (ruling 20 d).

## Related Documentation

- Poppy `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (F3); Brave Search API docs for the LLM-Context
  endpoint and ToS (the docs URL is recorded in `tests/fixtures/README.md` at capture time); epic
  rulings 23–30.
- `pipeline/stage5_url_audit.py` (client hardening and body bound), `model_fetcher.py`
  (`*_ENV_VAR` constants, `_resolve_token`, `weights_fetch_skipped`, `_fetch_reason`,
  `model_revision_invalid`), `cache.py` (closed log vocabulary; `ContentCache.get`/`put`/`delete`).
- Tests: `tests/test_cache.py` (the leak-test style), `tests/test_stage5_url_audit.py` (the
  streaming-client helpers), `tests/fakes.py` (`FakeStorage` counters), `tests/test_hermeticity.py`
  (the exact-set gate), `tests/test_dockerfile.py` (the token-shaped-literal sweep).
- `docs/configuration.md`, `kit_tools/docs/ENV_REFERENCE.md`, `kit_tools/arch/SECURITY.md`,
  `kit_tools/arch/INFRA_ARCH.md`, `kit_tools/arch/SERVICE_MAP.md`, `kit_tools/arch/CODE_ARCH.md`,
  `kit_tools/docs/TROUBLESHOOTING.md`, `kit_tools/arch/patterns/LOGGING.md`,
  `kit_tools/arch/patterns/ERROR_HANDLING.md`, `kit_tools/docs/GOTCHAS.md`.
- `pipeline/stage2_structural.py`, `pipeline/stage3_promptguard.py`, `promptguard/classifier.py`.

## Refinement Notes

Brave is independently valuable and testable as `chain[0]` before fallback exists (spec 3). The
sequencing is deliberate: US-001 is gated on the owner's envelope capture and builds the provider
(its tests inject the provider directly, so it does not borrow US-002's registration); US-002
wires the key, the settings, the registration and the documentation; US-003 makes failure loud and
proves the security properties. The pinned-sample rule exists because a hermetic suite cannot tell
a right parser from a wrong one; the envelope-only rule (ruling 24) keeps that guarantee — real
names, nesting, types and counts — without committing a byte of Brave-authored text. The
per-provider caching flag was dropped by ruling 22 and replaced by the no-cache-write test.

## Clarifications

_None outstanding._

## Open Questions

_None (session_ready). The endpoint path, auth header name, query parameters and response field
names are recorded in US-001 as expected values from Brave's public documentation; the captured
envelope verifies them, and any difference is recorded in this spec's Implementation Notes — a
documented integration step, not a design question._

## Implementation Notes

- **2026-09-16 — US-001 owner gate satisfied (ruling 24).** The owner captured the Brave
  LLM-Context sample from a shell (`curl -K` config under `umask 077`, key from the
  environment, config deleted afterwards; query `history of the bicycle`, `count=3`) and
  the envelope-only fixture is committed as `tests/fixtures/brave/llm_context_sample.json`
  with provenance in `tests/fixtures/README.md`. **Observed shape vs the "expected now"
  list in US-001's hints:** (1) `grounding` carried `generic` and `map` (an empty list);
  no `poi` key was present. (2) `generic[*]` is `{url, title, snippets}` with `snippets` a
  list of strings, as expected — 35 / 27 / 22 chunks for the three sources, 74–608
  characters each. (3) `sources` is keyed by URL in the same order as `generic`, and each
  value is `{title, hostname, age, snippet}`: `age` is a **list of four strings** (a
  long-form date, a ten-character ISO date, a relative "N days ago", an ISO-8601
  timestamp), not a single string, and `snippet` (about 100–200 characters) is present
  though the docs page lists an optional `description` instead. The parser follows the
  sample: treat `age` as a list (the ten-character ISO element is the natural `date`
  source), tolerate an absent `poi`, and ignore `map`.

- **2026-09-16 — US-010 landed: `BraveApiProvider` core (client, settings, parser).**
  `pipeline/search_providers/brave.py` implements the seam; core tests in
  `tests/test_brave_provider.py` (26 tests, all pass; full suite 1834 passed;
  `pipeline/orchestrator.py` left byte-identical, confirmed via `git diff --stat`).
  **Wire names, stated precisely against what the capture actually confirmed** (the 2026-09-16
  owner capture used only `q=history+of+the+bicycle&count=3` — nothing else): the endpoint
  `GET https://api.search.brave.com/res/v1/llm/context`, the auth header
  `X-Subscription-Token`, and the result-budget parameter `count` are **capture-confirmed** —
  the provider sends `count=<max_results>` because that is the literal parameter the capture
  used, not a guess. `maximum_number_of_urls` and the per-source parameters
  (`maximum_number_of_snippets_per_url`, `maximum_number_of_tokens_per_url`) named in Brave's
  documentation are **docs-derived only**; the capture never exercised them, and this provider
  does not send them. The response envelope shape (`grounding.generic`, `sources` keyed by URL,
  `age` as a four-element list) is exactly the observed shape recorded in the note above; this
  story's parser and its `tests/test_brave_provider.py::TestParsesThePinnedSample` tests are
  written against that shape and nothing else.
  **The non-object `generic` element choice.** US-001's hints said a non-object element of
  `grounding.generic` is "skipped"; US-003's hints (ruling 27) instead list "elements that are
  not objects" under `malformed_body` for the whole response. The two disagree. This story
  implements the `malformed_body`-for-the-whole-response reading (`_build_result` in
  `brave.py`) because ruling 27 is the more specific, later-cited source and because a
  per-element skip would silently narrow a response the caller asked `max_results` sources
  from without any signal that one was dropped. US-003, which owns the failure taxonomy, is the
  story to revisit this in if that reading turns out wrong.
  **Config.** `search_brave_timeout_seconds` (1.0–60.0, default 15.0),
  `search_brave_chunk_max_chars` (200–2000, default 2000) and `search_brave_query_max_chars`
  (50–400, default 400) ship in `config.yaml` as top-level scalars with a comment;
  `brave_settings_from_config` is called unconditionally in the lifespan beside
  `cache_settings_from_config` (`retrieval_app.py`), so an out-of-range value refuses boot
  whether or not `"brave"` is in the resolved chain — asserted by
  `TestLifespanCallsBraveSettingsUnconditionally` with the default (`"brave"` not yet in
  `build_provider_chain`'s registry, since registration is US-002's job).
  **Not a `_REVISION_SOURCES` rotation:** `pipeline/search_providers/` is not hashed (ruling
  11) and `pipeline/orchestrator.py` was not touched, so `derive_sanitizer_revision()` is
  unchanged by this story.

- **2026-09-16 — US-011 landed: hardening tests, budget assertions, doc rows.** All
  eleven new tests in `tests/test_brave_provider.py` (37 total, up from 26) pass against
  the US-010 module **unmodified** — no defect surfaced, so `brave.py` needed no fix.
  `git diff --stat 05af5c6 -- pipeline/orchestrator.py` is empty, confirming the file is
  still byte-identical to its US-010 state.
  **Candidate budget.** A `_SpyingBraveProvider(BraveApiProvider)` subclass (records
  `(query, max_results)` then delegates for real) driven through `run_search_pipeline`
  with `num_results` 1, 5 and 20 confirms `search()` receives `request.num_results`
  verbatim (never `min(num_results * 2, 20)`) and that the mocked `client.stream` call's
  `params["count"]` carries the same value each time — an instance-attribute spy
  (`provider.search = spy`) was avoided in favour of a subclass override, since pyright
  strict has no carve-out for reassigning a bound method's type on an instance.
  **Body bounds — the no-`Content-Length` case needed a real stream.** `_make_response`
  (the `content=` helper) always yields a real, correct `Content-Length` header even when
  a caller passes an inflated one only for the *fast-reject* case; the *no-header,
  streamed-overrun* case needed a genuine `httpx.AsyncByteStream` subclass
  (`_ChunkStream`, yielding chunks with no `content=` at all) passed via `stream=` on a
  hand-built `httpx.Response`, with `response.headers.get("content-length") is None`
  asserted as the test's own precondition per the story's hint. The **compressed** case
  needed no such workaround: `httpx.Response(content=gzip.compress(raw), headers=
  {"content-encoding": "gzip"})` reports a `Content-Length` equal to the *compressed*
  size (well under the cap), while `aiter_bytes()` transparently decodes it back to the
  oversized *raw* size — exactly the gap between the fast-reject path and the running
  streamed cap that makes this a distinct test from the other two. All three assert a
  `pipeline.search_providers.brave.json.loads` spy was never called.
  **Caps.** A 50,000-character single chunk truncates to `chunk_max_chars` (2000);
  a 5,000-character query truncates to `query_max_chars` (400) in the outbound `q` the
  mocked client received, while `run_search_pipeline` completes with results rather than
  raising — "no 422 from the app when driven end-to-end" is read, consistently with this
  file's own `TestRunSearchPipelineIntegration` precedent, as "no `PipelineError` out of
  `run_search_pipeline`" rather than a real ASGI request (this test module never spins up
  the FastAPI app or a `TestClient`).
  **Provenance, both directions.** One test runs `run_search_pipeline` once with a
  `FakeSearchProvider` (`tests/fakes.py`) returning `engine="brave"` (SearXNG's own
  sub-engine) and once with the real `BraveApiProvider` over the pinned sample, asserting
  the wire `engine` values `"brave"` and `"brave-api"` both survive and differ — proving
  neither the loop (`raw.get("engine")` is copied verbatim, `pipeline/orchestrator.py`
  ~820) nor either provider normalizes one into the other.
  **Docs.** Added the Brave egress row to `kit_tools/arch/INFRA_ARCH.md`'s table, the
  `pipeline/search_providers/brave.py` row to `kit_tools/arch/CODE_ARCH.md`'s module
  table, the `engine` clause naming both values to `kit_tools/docs/API_GUIDE.md`'s
  `results` row, and a `tests/test_brave_provider.py` row plus `test_mapping` entry to
  `kit_tools/testing/TESTING_GUIDE.md` — no suite-count totals elsewhere were touched
  (ruling 32, spec 5 US-003's job). Full suite: 1845 passed; `ruff check`, `ruff format
  --check` and `pyright` (strict) all clean.

- **2026-09-16 — US-002 landed: env-gated conditional registration (3rd attempt).** The
  prior attempt's verifier flagged the story hint's `raw.strip()` as contradicting its own
  worked example (`"key\r"` must be *not present*, but a plain `str.strip()` removes a
  trailing CR, leaving a present-looking `"key"`). Resolved by narrowing the strip:
  `brave_key_present` and `_resolve_brave_key()` both strip only `KEY_STRIP_CHARS = " \t\n"`
  (space, tab, LF — deliberately not CR) before validating ASCII/printable/no-interior-
  whitespace. A lone trailing CR, or the CR half of a CRLF once the LF is stripped, survives
  as an embedded control character and is refused by `str.isprintable()`. Parametrized cases
  pin the resolution: `"key\r"` → not present, `"key\n"` → present and resolves to `"key"`.
  **Shared logic, not shared strip call.** `brave_key_present(raw: str | None) -> bool` lives
  once in `pipeline/search_providers/brave.py`; `retrieval_app._resolve_brave_key()` imports
  both it and the `KEY_STRIP_CHARS` constant rather than re-deriving the strip set, so the
  two can never quietly diverge.
  **Registry shape.** `"brave"` is tracked as a known name (`_KNOWN_PROVIDER_NAMES`) separate
  from `build_provider_chain`'s registry dict — the registry only gains a `"brave"` entry
  when a key is present, but an unknown-name boot refusal must never fire for `"brave"` even
  key-less (that case is a skip-plus-WARNING, not a refusal). Under pyright strict, the
  conditional `brave` factory closure needed `brave_api_key` reassigned to a fresh local
  before capture — pyright does not propagate a parameter's `is not None` narrowing into a
  nested lambda, but a freshly assigned local's inferred type is unaffected.
  **Ordering.** `brave_settings_from_config(config)` (already unconditional since US-010)
  moved earlier in the lifespan, immediately before the chain build, since
  `build_provider_chain` now consumes the resolved `BraveSettings` — it is still a single,
  unconditional call, just relocated ahead of `cache_settings_from_config`.
  **Retry-feedback tests added.** A real-lifespan test drives `FORAGE_SEARCH_PROVIDERS=brave`
  with a key and a patched `_load_config` returning a non-default
  `search_brave_timeout_seconds`, asserting the value reaches the patched
  `pipeline.search_providers.brave.httpx.AsyncClient`'s `timeout=` kwarg; a second boots the
  lifespan, then mutates `FORAGE_BRAVE_API_KEY`/`FORAGE_SEARCH_PROVIDERS` post-boot and
  asserts `app.state.search_providers` (read through `_resolved_search_providers`) is
  unchanged — the read-once guarantee, verified rather than assumed; a third parametrizes
  non-ASCII, CR/LF, interior-whitespace and valid values through
  `_resolve_brave_key → build_provider_chain → provider.search()` against a patched client,
  asserting no `UnicodeEncodeError` anywhere on the path (a NUL-byte case was dropped from
  this round-trip test — POSIX environment variables cannot contain one at all, so
  `monkeypatch.setenv` itself raises `ValueError`; `brave_key_present`'s own parametrized
  cases cover that shape directly instead); a fourth and fifth assert exactly one
  `brave_skipped_missing_key` WARNING record for a key-less `brave` entry and that an
  invalid sentinel key never appears in `caplog.text`, both driven through the real lifespan
  rather than `build_provider_chain` alone.
  **Docs.** `FORAGE_BRAVE_API_KEY` rows added to `docs/configuration.md`'s variable table
  (naming both `brave_skipped_missing_key` and `search_chain_defaulted_to_searxng`),
  `kit_tools/docs/ENV_REFERENCE.md`'s per-variable table and its cleared-variable sentence,
  and `kit_tools/arch/SECURITY.md`'s secrets inventory; the three `search_brave_*`
  `config.yaml` tunables (already shipped since US-010) got their first doc rows, in both
  `docs/configuration.md`'s and `ENV_REFERENCE.md`'s "Top-level keys" tables.
  Full targeted suite (`test_brave_provider.py`, `test_search_providers.py`, `test_app.py`,
  `test_hermeticity.py`, `test_orchestrator.py`): 376 passed; `ruff check`, `ruff format
  --check` and `pyright` (strict) all clean.

- **2026-09-16 — US-012 landed: failure taxonomy log line, 12-token parametrization,
  zero-source variants, key-never-leaks (split of US-003, part 1).** The mapping, the wire
  `search_unavailable` outcome, and the 429→`rate_limited` classification were already
  correct and already landed (spec 1 US-004, this feature's US-010/US-011) — the only
  production defect this story fixed was the log line itself: `_failure()` was passing
  `failure_class`/`detail` as `extra={...}`, which `kit_tools/arch/patterns/LOGGING.md`
  (~138) says nothing ever renders (no handler is configured, so first-party WARNING output
  falls to `logging.lastResort`, whose format is bare `%(message)s`). An operator following
  the `hard_error` row in `kit_tools/docs/TROUBLESHOOTING.md` would see only
  `brave_search_failed` and nothing else. Fixed to
  `logger.warning("brave_search_failed — %s (%s)", detail, failure_class)` — the tokens are
  now in the message itself. `brave.py` is not a `pipeline/sanitizer_revision.py`
  `_REVISION_SOURCES` member, so `derive_sanitizer_revision()` was measured unchanged
  (`b7871b20…ea6f2b`, same as US-004) before and after.
  **Tests added to `tests/test_brave_provider.py`.** One `_FailureCase` table (a dataclass
  of `id`/`failure_class`/`detail`/a zero-arg context-manager factory) drives all three new
  test classes: `TestFailureTaxonomy` (the 12-token parametrization — class, detail, no
  raise, `detail in _BRAVE_FAILURE_DETAILS`, exactly one WARNING whose `getMessage()` carries
  both tokens and no exception text/URL/host/key, plus a set-equality check against all
  twelve), `TestSearchUnavailableWireOutcome` (every case reaches `POST /search` as 422
  `search_unavailable` with `reason == f"brave: {failure_class}"`, plus a source-text check
  that `brave.py` names no `PipelineError`), `TestZeroSourceIsACleanSuccess` (no-`grounding`,
  no-`generic`, null-`generic`, empty-`generic` all assert
  `ProviderSearchResult(results=[], unresponsive_engines=[])` directly, plus `POST /search`
  200 with `results == [], omitted_by_reason == {}, omitted_results == 0,
  unresponsive_engines == []` on both a zero-source and a sample-served response), and
  `TestKeyNeverLeaks` (same 12 cases, driven through a real `POST /search` with the provider
  on `app.state.search_providers` — a local `_borrowed_search_providers` save/restore
  context manager, copied from `tests/test_app.py`'s helper of the same name since each test
  module in this repo owns its own `app`-wiring fixtures rather than sharing one — asserting
  a sentinel key and the endpoint host are absent from `caplog.text`, the 422 body, `GET
  /metrics`, and `str()`/`repr()` of both the provider and a directly-obtained
  `ProviderFailure`; the catch-all case's injected `RuntimeError` message embeds the sentinel
  and the host to prove `_failure()`'s `except Exception:` branch never reads `str(exc)`).
  Added the new test class to `kit_tools/arch/SECURITY.md`'s "Coverage by control" `Secrets`
  row. Full targeted suite (`test_brave_provider.py`, `test_app.py`, `test_contract_errors.py`,
  `test_orchestrator.py`): all passed; `ruff check`, `ruff format --check` and `pyright`
  (strict) all clean. US-013 carries sanitization parity, the no-persistence pin, and the
  operator docs.
