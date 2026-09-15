<!-- Template Version: 2.5.0 -->
---
feature: search-provider-abstraction
status: active
session_ready: true
depends_on: []
vision_ref: "T2.1 — Search-provider abstraction & reliable search"
type: epic-child
size: L
epic: search-providers
epic_seq: 1
epic_final: false
execution_order: [US-001, US-002, US-003, US-004]
created: 2026-09-14
updated: 2026-09-14
---

# Feature Spec: Search Provider Abstraction

> **Epic 1 of `epic-search-providers` (Forage half of Web Access family Epic 3).** Foundation: turn
> the hardwired SearXNG call into a pluggable `SearchProvider` seam with a typed failure surface,
> resolve an ordered provider chain from the environment, and align the result schema (LiteLLM shape
> + `content_kind`) with the epic's single contract bump to 1.2.0 — which also lands the
> `search_unavailable` error code (ruling 14). No new backend and no fallback yet — those are specs
> 2 and 3. Revised 2026-09-14 after two rounds of `/kit-tools:validate-epic`; the epic's rulings 9,
> 11, 13, 14, 17, 19 and 22, and from round 2 rulings 23, 25, 26, 27, 28, 29 and 32, are binding
> here. Canonical planning context: Poppy's `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (rows F1, F2, F12).

## Overview

Today `pipeline/orchestrator.py:run_search_pipeline` queries SearXNG with an inline `httpx` call
(the block around lines 669–699) against `_DEFAULT_SEARXNG_URL` (~line 555), collapses every failure
into `searxng_error` / `searxng_unavailable`, and hands the raw dicts to a per-result sanitization
loop (~708–796) that is already provider-agnostic. There is no seam for a second backend and no way
to tell a 429 from a DNS failure from an empty result. This spec introduces a `SearchProvider`
protocol whose `search()` returns either a `ProviderSearchResult` or a `ProviderFailure` (closed
failure classes, closed `detail` tokens — ruling 13), extracts the SearXNG logic into a
`SearxngProvider` behind it without changing what the wire sees, resolves a provider chain from
`FORAGE_SEARCH_PROVIDERS` in the lifespan (ruling 9), and lands the additive wire changes —
`content_kind`, a strictly validated `date` (ruling 19), and the `search_unavailable` code — under
the contract bump 1.1.0 → 1.2.0.

## Goals

- A `SearchProvider` protocol (`name`, `paid`, `search(query, max_results)`) plus the internal
  `ProviderSearchResult` / `ProviderFailure` types that every backend implements and specs 2–4
  consume.
- SearXNG served **through** that protocol (`SearxngProvider`), with identical wire behavior: same
  codes, same status, same scan budget, the operator's `SEARXNG_URL` still honoured.
- An ordered provider chain resolved from `FORAGE_SEARCH_PROVIDERS` (comma-separated, default
  `searxng`), read once in the lifespan through the lifespan-or-fallback pattern, passed by the
  `/search` handler into `run_search_pipeline`, used single-provider for now (`chain[0]`).
- The wire `SearchResult` aligned to LiteLLM's `{title,url,snippet,date}` shape plus a
  `content_kind` discriminator; `search_unavailable` added to the `/search` vocabulary; contract
  regenerated and bumped to 1.2.0 with the full documentation fan-out.

## User Stories

### US-001: Define the `SearchProvider` seam

**Priority:** P1

**Description:** As a Forage maintainer, I want a provider-agnostic interface with a typed success
*and* failure surface, so additional search backends can be added — and their failures classified —
without touching the pipeline's orchestration logic.

**Independent Test:** A unit test implements a fake provider against the protocol and drives it
through a populated result, an empty result, and every one of the five failure classes; it asserts
the two return types carry exactly the specified fields, that an empty `results` list comes back as
a `ProviderSearchResult` (a success), and that `ProviderFailure.detail` is a fixed token. A second
test asserts no module under `pipeline/search_providers/` imports a sanitization stage or the
cache. No pipeline change is exercised yet.

**Implementation Hints:**
- New package `pipeline/search_providers/` (`__init__.py` + `base.py`). `Dockerfile` line 167 (`COPY
  pipeline/ ./pipeline/`) and `pyproject.toml`'s package include already ship a subpackage — no
  build change. Do **not** add it to `pipeline/sanitizer_revision.py`'s `_REVISION_SOURCES` (ruling
  11: provider code changes what is fetched, not how it is sanitized; search results are never
  cached). `tests/test_governance_docs.py` pins that tuple's length at eight — leaving it is the
  no-work path. It is the first nested package under `pipeline/`; see Technical Considerations for
  the `CLAUDE.md` invariant 3 reading.
- `SearchProvider` is a `typing.Protocol` — the `cache.py` `CacheStorage` precedent (line 313). The
  repo has no ABCs; do not introduce one. Members, exactly:
  - `name: str` — the chain token (`"searxng"`; spec 2's provider is `"brave"`). It is the **one**
    identifier per provider (ruling 23): `FORAGE_SEARCH_PROVIDERS`, the registry key, spec 3's
    `provider_used` and `provider_errors`, the `search_unavailable` reason, and spec 4's `/health`
    `search_providers` and per-request `providers` all carry this token and nothing else.
  - `paid: bool` — `False` for SearXNG; spec 4's `allow_paid_fallback` stops before the first
    `paid=True` provider.
  - `async def search(self, query: str, max_results: int) -> ProviderSearchResult |
    ProviderFailure`.
- The per-result `engine` field is **provenance**, never identity: SearXNG passes its sub-engine
  through (its own `brave` sub-engine included) and spec 2's provider stamps `engine = "brave-api"`,
  so the two stay distinguishable while the provider's `name` stays `brave` (ruling 23).
- `max_results` is the literal candidate budget the orchestrator chose for this call — a request,
  never a trusted bound. US-002 writes the one line that computes it (ruling 25): `max_results =
  request.num_results if provider.paid else fetch_limit`, with `fetch_limit =
  min(request.num_results * 2, _MAX_SEARCH_RESULTS_SCANNED)` exactly as today
  (`orchestrator.py:667`). Spec 2 consumes and asserts that line; it does not write it. The
  orchestrator re-applies its slice to whatever the provider returns, so
  `_MAX_SEARCH_RESULTS_SCANNED` stays orchestrator-enforced whatever a provider does (US-002 tests
  it).
- `origin: str | None` — the operator-configured endpoint reduced to scheme, hostname and port
  with userinfo stripped, computed once in `__init__` (contract point 7 below); `None` for a
  provider whose endpoint is never echoed (Brave). It is the only endpoint-shaped value the
  orchestrator may read through the protocol; `base_url` is a `SearxngProvider` attribute, not a
  protocol member, and nothing outside the provider reads it.
- `ProviderSearchResult` (frozen dataclass; internal, **not** wire): `provider_name: str`, `results:
  list[dict[str, Any]]` where each dict is in the `{title, url, content, engine, date}` shape (the
  loop at ~708–796 reads `title`/`url`/`content`/`engine` today; `date` is consumed from US-004),
  and `unresponsive_engines: list[str]`. The last is SearXNG's vocabulary on purpose: every other
  provider returns `[]`, and ruling 17 keys on it for SearXNG alone. It is a seam field like any
  other and is bounded before it reaches the wire (US-002: list cap, element cap, control-character
  strip, applied orchestrator-side). An empty `results` list is a success.
- `ProviderFailure` (frozen dataclass; internal): `provider_name: str`, `failure_class:
  FailureClass`, `detail: str`. `FailureClass` is `Literal["rate_limited", "timeout", "hard_error",
  "auth", "quota"]` with a derived `FAILURE_CLASSES` frozenset (`get_args`, the `DegradedReason`
  pattern in `pipeline/contract.py`). `detail` is a token from the provider's own closed vocabulary
  — never `str(exc)`, never a URL, never a header or parameter value. `FailureClass` stays internal
  to `pipeline/search_providers/`; spec 3 composes the wire strings (`provider_errors` entries, the
  `search_unavailable` reason) in `pipeline/orchestrator.py` from `name` and `failure_class` and
  touches `pipeline/contract.py` for nothing — the only later literal that lands there is spec 4's
  `POLICY_EXCLUDED_ALL_PROVIDERS`.
- The provider contract, stated in the protocol docstring and preserved by every provider:
  1. Endpoints come only from operator configuration read at start, never from request data, and on
     that basis alone are exempt from `validate_url` (the `kit_tools/arch/SECURITY.md`
     review-checklist rule for outbound requests). Results are attacker-controlled and trusted by
     nothing. A paid provider truncates the outbound copy of `query` before egress (ruling 30, spec
     2); `SearchRequest.query` keeps its wire shape.
  2. Every provider client is constructed with `follow_redirects` at its `False` default, TLS
     verification on, and `trust_env=False` (ambient `HTTP_PROXY` / `HTTPS_PROXY` / `.netrc` /
     `SSL_CERT_FILE` never redirect provider egress or swap the CA bundle), and bounds the response
     body **before** `resp.json()` runs — an overrun is a `ProviderFailure` (`hard_error`,
     `body_too_large`), never a parse.
  3. Providers return raw dicts. They never construct a wire `SearchResult`, never normalize, bound,
     or scan text — the orchestrator loop is the only path to the wire. (This is what keeps ruling
     11 true; the import test below makes it mechanical — its forbidden list names the stage
     modules, `promptguard`, `cache` **and `models`**, so a provider cannot construct a wire
     `SearchResult` even by accident.)
  4. Failures are `ProviderFailure` with a fixed `detail`; a provider log line carries the class and
     the token only (CLAUDE.md invariant 6). Every provider's mapping ends in a catch-all (ruling
     27): `except Exception` → `ProviderFailure(failure_class="hard_error", detail="unexpected")`,
     and a valid-JSON body of the wrong shape (`results` not a list, an element not an object) →
     `hard_error` / `malformed_body`. **Providers never raise into the orchestrator.**
  5. Provider result bodies are never cached, logged, or persisted.
  6. Providers are stateless per call: `httpx.AsyncClient` is opened inside `search()` and closed
     before it returns (today's `async with httpx.AsyncClient(timeout=10.0)`), so a chain of
     provider objects held for the life of the process needs no shutdown hook.
  7. Every provider exposes `origin: str | None` (see the member list above), computed once in
     `__init__` from its configured endpoint: `urlsplit(...)` rebuilt as scheme, `hostname` and
     `port`. `SplitResult.port` **raises** `ValueError` on a malformed port
     (`http://host:notaport`, `http://host:99999`); the constructor catches it — and
     `urlsplit()`'s own `ValueError` (`http://[::1`) and a `hostname` of `None`
     (`SEARXNG_URL=searxng:8080`, no scheme) — and falls back to scheme and hostname when both
     parse, otherwise to the closed token `unparseable-endpoint` (never the raw string, which
     could carry userinfo), so a typo in `SEARXNG_URL` still yields a 422 that names what it can
     rather than a 500 from inside the error handler. `None` when the endpoint is never echoed.
- A mechanical guard for the contract points is welcome wherever the suite already intercepts
  `httpx.AsyncClient`: the precedent is `tests/test_stage5_url_audit.py:492`, which reads the
  constructor kwargs off the patched class (`follow_redirects is False`, `verify` present). US-002
  adds that assertion for `SearxngProvider`; spec 2 adds it for Brave.
- The import test follows the suite's AST-sweep idiom (`tests/test_contract_errors.py::
  _swept_error_codes`, `tests/test_model_fetcher.py::_env_names_read`), including its habit of
  failing on an import shape the sweep cannot see.
- Tests live in a new `tests/test_search_providers.py` carrying the module-docstring `test_mapping:`
  block the newer test modules carry (`tests/test_hermeticity.py:33`). Add `test_mapping` rows to
  `kit_tools/testing/TESTING_GUIDE.md` keyed on concrete paths, as every existing row is
  (`pipeline/search_providers/__init__.py`, `pipeline/search_providers/base.py`, and from US-002
  `pipeline/search_providers/searxng.py`), a `pipeline/search_providers/` row to
  `kit_tools/arch/CODE_ARCH.md`'s module table (AGENT_README's session-end rule for a new module),
  and the subpackage form to `kit_tools/docs/CONVENTIONS.md:52`'s Modules row.

**Acceptance Criteria:**
- [ ] `SearchProvider` (a `typing.Protocol` with `name: str`, `paid: bool`, `origin: str | None`, and `async search(query:
      str, max_results: int) -> ProviderSearchResult | ProviderFailure`), `ProviderSearchResult`,
      `ProviderFailure`, `FailureClass`, and `FAILURE_CLASSES` are defined in
      `pipeline/search_providers/` with the fields and types above; the protocol docstring states
      that `name` is the chain token and `engine` is provenance.
- [ ] `FAILURE_CLASSES == {"rate_limited", "timeout", "hard_error", "auth", "quota"}` is asserted by
      a test; `_REVISION_SOURCES` is unchanged (`tests/test_governance_docs.py`'s eight-file count
      passes).
- [ ] A unit-test fake provider satisfies the protocol under `uv run pyright` (strict) and is driven
      through the success path (populated and empty `results`, both `ProviderSearchResult`) and the
      failure path (one `ProviderFailure` per `failure_class`); the test asserts an empty result
      list is a success, never a failure.
- [ ] The protocol docstring states the six-point provider contract including the catch-all rule
      (`hard_error` / `unexpected`; wrong-shape body `hard_error` / `malformed_body`; providers
      never raise), `trust_env=False`, and the pre-parse body bound; a test asserts that no module
      under `pipeline/search_providers/` imports `pipeline.stage1_extraction`,
      `pipeline.stage2_structural`, `pipeline.stage3_promptguard`, `promptguard`, or `cache`.
- [ ] `kit_tools/testing/TESTING_GUIDE.md`'s `test_mapping`, `kit_tools/arch/CODE_ARCH.md`'s
      module table, and `kit_tools/docs/CONVENTIONS.md`'s Modules row carry the new package and
      test module.
- [ ] No wire model, no `run_search_pipeline` line, and no pre-existing test changes in this story.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-002: Extract `SearxngProvider` (behavior-preserving)

**Priority:** P1

**Description:** As a Forage maintainer, I want the existing SearXNG query moved behind the protocol
so the pipeline calls a provider instead of inline HTTP — with the operator's `SEARXNG_URL` still
honoured, the `searxng_error` / `searxng_unavailable` codes byte-identical on the wire, and the scan
budget still enforced by the orchestrator.

**Independent Test:** Every pre-existing search test in `tests/test_orchestrator.py` — the sixteen
that go through `_searxng_client_patch` and the three direct `patch(...)` sites at ~1725, ~1744 and
~2092, nineteen in all — passes with its assertions and its `run_search_pipeline(...)` arguments
untouched. New tests in `tests/test_search_providers.py` assert that `SearxngProvider.search()`
reproduces the raw result dicts, the `unresponsive_engines` list, and the `publishedDate` → `date`
mapping for a mocked SearXNG response, and returns the specified `ProviderFailure` for a timeout, a
429, a 500, a connection error, a non-JSON body, a wrong-shape body, an oversized body, and an
unexpected exception — never raising; one test asserts a non-default base URL reaches the outbound
request; one asserts a base URL carrying `user:pass@` never appears in the `searxng_unavailable`
reason; one asserts a provider returning 500 results for `num_results=5` drives exactly
`fetch_limit` loop iterations, beside `test_search_promptguard_work_is_capped_at_twenty_results`
(`tests/test_orchestrator.py:1268`), the pre-provider relative.

**Implementation Hints:**
- New `pipeline/search_providers/searxng.py`: `class SearxngProvider` with `name = "searxng"`, `paid
  = False`, `__init__(self, base_url: str = DEFAULT_SEARXNG_URL)` and a `base_url` attribute. Move
  the `httpx` block (`orchestrator.py` ~669–699) into `search()` unchanged in behavior: the
  `f"{base_url}/search"` GET with `params={"q", "format": "json", "pageno": 1, "engines":
  SEARXNG_ENGINES}`, `httpx.AsyncClient(timeout=10.0, trust_env=False)`, `raise_for_status()`,
  `results[:max_results]`, and the list-or-tuple `unresponsive_engines` handling. Each raw dict
  additionally carries `date` = the raw `publishedDate` value (or `None`). No normalization of
  anything — hint 3 of the contract. `trust_env=False` is the one recorded deviation from
  behavior preservation (contract point 2): an operator who reached SearXNG through an ambient
  `HTTP_PROXY` loses that, and the `SEARXNG_URL` row in `kit_tools/docs/ENV_REFERENCE.md` says so.
- **Client construction is load-bearing for the test suite.** The provider module does a
  module-level `import httpx` and looks up `httpx.AsyncClient(...)` at call time inside `search()` —
  not `from httpx import AsyncClient`, and not a client held in `__init__`.
  `patch("pipeline.orchestrator.httpx.AsyncClient")` resolves through `pipeline.orchestrator`'s
  `httpx` attribute to the shared `httpx` module object and replaces `AsyncClient` on *it*, so the
  old and new dotted paths patch the same thing and the mocks keep intercepting from the new module.
  `orchestrator.py:23`'s `import httpx` stays (it is still used at ~313 on the retrieve path) — do
  not tidy it away, or all nineteen tests fail at once. The **one** permitted edit to pre-existing
  tests is `_searxng_client_patch`'s target string (`tests/test_orchestrator.py:878`), which moves
  from `pipeline.orchestrator.httpx.AsyncClient` to
  `pipeline.search_providers.searxng.httpx.AsyncClient` for accuracy. The three direct patch sites
  keep their string and keep working; the seventeen `searxng_url=` call sites are untouched.
- **Constants (ruling 26a).** The provider module defines **public** `DEFAULT_SEARXNG_URL` and
  `SEARXNG_ENGINES`; `pipeline/orchestrator.py` keeps `_DEFAULT_SEARXNG_URL = DEFAULT_SEARXNG_URL`
  and `_SEARXNG_ENGINES = SEARXNG_ENGINES` as private aliases **assigned** from the public names. An
  assignment declares a new module-level name, whereas importing a private name from another
  module fails `uv run pyright` strict's `reportPrivateUsage` (the carve-out is for `tests/` only),
  so the redundant-alias import form is not available. Three test modules import the private
  names from `pipeline.orchestrator` and pass unmodified: `tests/test_searxng_docker.py:37` (the
  `searxng/config/settings.yml` sync guard — `kit_tools/docs/GOTCHAS.md`, "engine list in two
  places"), `tests/test_compose_fragments.py:57`, and `tests/test_searxng_smoke.py:36`. One
  definition, two assigned aliases, never a second copy of the engine string.
- Failure mapping inside the provider, closed by construction (`_SEARXNG_FAILURE_DETAILS` frozenset
  plus one status family): `httpx.TimeoutException` → (`timeout`, `timeout`);
  `httpx.HTTPStatusError` → (`rate_limited` when the status is 429, otherwise `hard_error`; `detail`
  = `http_` followed by the integer status code, e.g. `http_429`, `http_500`); any other
  `httpx.HTTPError` or transport failure → (`hard_error`, `connect_error`); a body longer than
  `_MAX_SEARXNG_RESPONSE_BYTES` (1 MiB, checked as `len(resp.content)` before `resp.json()` — a
  `MagicMock` response reports length 0, which is why the sixteen mocked tests need no edit) →
  (`hard_error`, `body_too_large`); `resp.json()` raising, or a body that is not an object →
  (`hard_error`, `bad_json`); an object whose `results` is not a list or contains a non-object
  element → (`hard_error`, `malformed_body`); and, last, `except Exception` → (`hard_error`,
  `unexpected`) — the mapped replacement for today's blanket `except Exception` at
  `orchestrator.py:696` (ruling 27), so `search()` never raises. One WARNING log line per failure
  carrying `failure_class` and `detail` only — no `exc_info`, no `str(exc)`, no URL (an `httpx`
  message embeds the request URL, which carries the query string and any userinfo in `SEARXNG_URL`).
- `run_search_pipeline` keeps its signature in this story (`searxng_url=` kwarg, same default) and
  constructs `SearxngProvider(searxng_url)` internally — no call site changes until US-003 adds
  `providers=`. It computes `fetch_limit` exactly as today and writes the candidate budget (ruling
  25): `max_results = request.num_results if provider.paid else fetch_limit`, calls
  `provider.search(request.query, max_results)`, re-applies `results[:max_results]` itself, and
  maps a `ProviderFailure` from the SearXNG provider to today's codes: a status-derived `detail`
  (`http_…`) → `PipelineError(error="searxng_error", reason=f"SearXNG returned HTTP error
  ({detail})")`; every other `detail` (`timeout`, `connect_error`, `body_too_large`, `bad_json`,
  `malformed_body`, `unexpected`) → `PipelineError(error="searxng_unavailable", reason=f"SearXNG
  not reachable at {origin}: {detail}")`, where `origin` is `provider.origin` — the
  **userinfo-stripped** scheme, hostname and port the provider computed at construction (contract
  point 7) — never `provider.base_url`, and never any attribute the protocol does not declare
  (pyright strict `reportAttributeAccessIssue` on a `SearchProvider`-typed value). `request_id`
  stays orchestrator-owned. The pre-existing assertions
  `"500" in reason` (`test_orchestrator.py:956`) and `"unreachable:8080" in reason` (`:935`, host
  and port only) hold.
- Reason-text ruling (written down so nobody re-derives it): `str(exc)` leaves the
  `searxng_unavailable` reason — ruling 13 forbids exception text past the seam, and
  `kit_tools/arch/SECURITY.md` lists "exception text on the wire" as an observed absence. The
  host:port echo **stays** (a pre-existing assertion pins it; GOVERNANCE ruling (d) treats such an
  echo as a documented caveat) and userinfo is stripped from it, so a credential in `SEARXNG_URL`
  no longer reaches a 422 body on an unauthenticated route; `kit_tools/docs/ENV_REFERENCE.md`'s
  `SEARXNG_URL` trap (~151, "never put userinfo in it") is reworded to say the echo is host:port
  only. GOVERNANCE (d) classifies a `reason`-text change as riding a bump: both changes here — the
  `searxng_error` form (`http_<status>` detail) and the `searxng_unavailable` form (no exception
  text, no userinfo) — are recorded by US-004's 1.2.0 docstring entry, and nothing publishes
  between the two stories (spec 5 publishes). `tests/test_app.py:707–714` hand-writes the old
  `searxng_unavailable` reason as a fixture for a test that patches `run_search_pipeline`; it stays
  green and untouched here and US-004 refreshes its text.
- `unresponsive_engines` is bounded where the orchestrator copies it onto `SearchResponse` (~834),
  not in the provider (contract point 3 keeps the provider out of it; the bound lives in hashed
  code): the list is capped at sixteen entries and each entry passes through
  `_normalize_search_text(max_length=64)` (control-character strip and length cap). The engine set
  is four names, so no honest response is touched; the wire type stays `list[str]`.
- The per-result sanitization loop (~708–796) stays in the orchestrator, byte-for-byte:
  `_sanitize_search_text`, `_canonicalize_search_url`, the three-field Stage 2 tuple, and
  `_search_result_promptguard_input` are untouched. Update `run_search_pipeline`'s docstring (it
  says "through SearXNG") and `kit_tools/arch/CODE_ARCH.md:91`'s `pipeline/orchestrator.py` row,
  which currently says it owns the engine list and `_DEFAULT_SEARXNG_URL`.
- **Documentation fan-out in the same change (ruling 32).** The reason-text change and the constant
  relocation falsify these statements, each of which is rewritten here: `kit_tools/arch/
  SECURITY.md:354` (the "exception text on the wire" observed absence narrows to `/retrieve`
  `fetch_error` alone), `kit_tools/arch/patterns/ERROR_HANDLING.md:158` and `:262–266` (Observed
  Rough Edge 1 — same narrowing; the mapping is now provider `detail` → code),
  `kit_tools/docs/TROUBLESHOOTING.md:172–173` (both reason formats), `kit_tools/arch/
  SERVICE_MAP.md:104` (the engine list's home) and `:109` (both reason formats; "echoes the URL and
  exception text" becomes "echoes scheme, host and port"), `kit_tools/arch/patterns/LOGGING.md:183`
  ("two response bodies interpolate exception text" becomes one, and the `orchestrator.py:697`
  anchor goes), `kit_tools/docs/GOTCHAS.md:343` (the engine list's home for the sync guard),
  `kit_tools/docs/MONITORING.md:143` (the `SearXNG returned HTTP 429` example), and
  `kit_tools/docs/API_GUIDE.md:242–243` (the `/search` failure sentence). AGENT_README's session-end
  rows for `SERVICE_MAP.md` (a failure behaviour changed), `LOGGING.md` (a log vocabulary changed —
  the new provider WARNING lines) and `SECURITY.md` are the rule being applied.
- `orchestrator.py` is in `_REVISION_SOURCES`, so this story rotates `sanitizer_revision`
  (expected). Record before/after and the reason in the three rotation tables (ruling 32):
  `docs/bootstrap-notes.md`, `CLAUDE.md`'s coexistence narrative, and `kit_tools/arch/DECISIONS.md`
  (~612–613).

**Acceptance Criteria:**
- [ ] The SearXNG HTTP call lives in `SearxngProvider.search()`
      (`pipeline/search_providers/searxng.py`); no `httpx` search call remains in
      `run_search_pipeline`; the provider opens `httpx.AsyncClient(timeout=10.0, trust_env=False)`
      per call through the `httpx` module attribute (no `from httpx import AsyncClient`, no client
      stored on the instance); a test modelled on `tests/test_stage5_url_audit.py:492` reads the
      constructor kwargs and asserts `trust_env is False`, `follow_redirects` absent or `False`,
      and `verify` not disabled.
- [ ] `SearxngProvider(base_url)` sends the request to `f"{base_url}/search"`; a test with a
      non-default base URL asserts it appears in the outbound request URL. `DEFAULT_SEARXNG_URL` and
      `SEARXNG_ENGINES` are public in the provider module; `_DEFAULT_SEARXNG_URL` and
      `_SEARXNG_ENGINES` are assigned aliases in `pipeline/orchestrator.py` (no import of a private
      name); `tests/test_searxng_docker.py`, `tests/test_compose_fragments.py` and
      `tests/test_searxng_smoke.py` pass unmodified.
- [ ] Every pre-existing `tests/test_orchestrator.py` search test keeps its assertions and its
      `run_search_pipeline(...)` arguments byte-for-byte; the only test-side edit in the story is
      `_searxng_client_patch`'s target string; `run_search_pipeline`'s docstring no longer
      describes the call as going through SearXNG directly.
- [ ] `run_search_pipeline` keeps its signature (`searxng_url` kwarg, default
      `_DEFAULT_SEARXNG_URL`), computes `max_results = request.num_results if provider.paid else
      fetch_limit` (a test with a `paid=True` fake receives `request.num_results`, with `paid=False`
      `fetch_limit`), and maps a SearXNG `ProviderFailure` to `searxng_error` (status-derived
      `detail`) or `searxng_unavailable` (every other `detail`) — same codes, same 422, same body
      shape; the `searxng_error` reason contains the status code, the `searxng_unavailable` reason
      contains scheme, host and port of the base URL and neither exception text nor userinfo — a
      test with `http://user:pass@unreachable:8080` asserts `"unreachable:8080" in reason` and
      `"pass" not in reason`.
- [ ] `SearxngProvider` returns `ProviderFailure` with the specified (`failure_class`, `detail`)
      pair for a timeout, a 429, another HTTP status, a transport error, an oversized body, a
      non-JSON body, a wrong-shape body (`{"results": {}}`), and a response whose `json()` raises
      an unrelated exception — eight tests, none of which sees an exception escape `search()`;
      every `detail` is a member of `_SEARXNG_FAILURE_DETAILS` or of the `http_` status family; a
      test with `caplog` asserts no log record carries the exception text or the base URL.
- [ ] `ProviderSearchResult.results` dicts carry `date` from `publishedDate`; `unresponsive_engines`
      is populated exactly as today for both the list and the tuple forms (tests); a test feeding
      forty entries, one of them 500 characters with embedded control characters, sees at most
      sixteen entries of at most 64 clean characters on `SearchResponse.unresponsive_engines`.
- [ ] The scan budget stays orchestrator-side: `fetch_limit` is computed as today and the slice
      re-applied after the provider returns; a test whose provider returns 500 results for
      `num_results=5`, every one blocked by Stage 2 so nothing is appended and the loop's
      `num_results` early exit never fires, counts exactly ten loop iterations (Stage 2 scans) and
      no Stage 3 pass — an exact count, not a ceiling.
- [ ] `docs/bootstrap-notes.md`'s rotation table, `CLAUDE.md`'s coexistence narrative and
      `kit_tools/arch/DECISIONS.md`'s rotation table record the new `sanitizer_revision` (before,
      after, reason); `kit_tools/arch/CODE_ARCH.md`'s `pipeline/orchestrator.py` row no longer
      claims ownership of the two constants.
- [ ] The documentation fan-out is applied: a grep of `kit_tools/` finds no statement that
      `/search` or `searxng_unavailable` interpolates `str(exc)` or exception text, no
      `SearXNG returned HTTP <n>` quoted as the current reason, and every mention of the engine
      list's home names `pipeline/search_providers/searxng.py` (`SEARXNG_ENGINES`); the nine
      anchors listed in the hints are rewritten and `SECURITY.md:354`, `LOGGING.md:183` and
      ERROR_HANDLING's Rough Edge 1 name `/retrieve` `fetch_error` as the only remaining case;
      `kit_tools/docs/ENV_REFERENCE.md`'s `SEARXNG_URL` row records `trust_env=False` and the
      host:port-only echo.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-003: Provider chain resolved from the environment

**Priority:** P1

**Description:** As an operator, I want to declare an ordered provider chain in the environment so
search backends are configurable at container start — defaulting to SearXNG-only, refusing to boot
on a name Forage does not know, and visible in the startup log.

**Independent Test:** With `FORAGE_SEARCH_PROVIDERS` unset, the lifespan resolves a chain whose
names are `["searxng"]` and a `POST /search` against a mocked SearXNG serves through it; with the
variable set to `searxng`, identical; with ` searxng, ` (whitespace, trailing comma) identical; set
but blank, identical plus a WARNING; set to `searxng,nope`, the lifespan raises
`SearchProviderConfigurationError` naming the entry's position and the known names — never `nope`
— and the process never serves. A separate test sets `app.state.search_providers = [fake]` on the
lifespan-free client (save/restore idiom) and asserts `POST /search` served through the fake.
(Multi-provider ordering is exercised in spec 3.)

**Implementation Hints:**
- In `pipeline/search_providers/__init__.py`: `parse_provider_names(raw: str | None) -> list[str]`
  splits on commas, strips, lower-cases, drops empty tokens, collapses duplicates keeping the first
  occurrence, and returns `["searxng"]` when nothing remains. `build_provider_chain(names:
  Sequence[str], *, searxng_url: str) -> list[SearchProvider]` resolves each name through a
  **static** registry — a dict literal in code, `"searxng"` → `SearxngProvider(searxng_url)` — by
  plain dictionary lookup. No `importlib`, `getattr`, `eval`, or entry-point discovery participates
  in name resolution. A name absent from the registry raises `SearchProviderConfigurationError`, a
  `ValueError` subclass defined in `pipeline/search_providers/__init__.py` (imported by
  `retrieval_app.py`, never the reverse — `pipeline/` does not import the app module). Its message
  names the variable, the 1-based position of the offending entry in the parsed list, and the
  sorted known names — **never the token and never the raw value**: the token is operator-supplied
  text heading for a log line, and the repo's ruling is `model_fetcher.resolve_revision()`, whose
  regression test `tests/test_model_fetcher.py:1307` feeds a newline-bearing value and asserts it is
  not echoed. Spec 2 registers `brave` in the same registry and extends the keyword-only surface
  with its key — one keyword per provider is deliberate at N=2; a third provider replaces the
  keywords with a settings mapping passed once.
- In `retrieval_app.py`: `SEARCH_PROVIDERS_ENV_VAR = "FORAGE_SEARCH_PROVIDERS"` and
  `_configured_provider_names()` (beside `_configured_valkey_url()`), the one read site
  (`kit_tools/docs/ENV_REFERENCE.md`, "Adding a New Variable", step 2). The lifespan, next to
  `app.state.config` (~1057), calls `build_provider_chain(_configured_provider_names(),
  searxng_url=SEARXNG_URL)`, publishes `app.state.search_providers`, and lets the error propagate —
  the `extraction_settings_from_config` (~1058) and `cache_settings_from_config` (~1086) precedent:
  a typo fails the boot loudly. `SEARXNG_URL` stays the import-time read it is today (documented
  restart-to-apply); its default literal becomes `os.environ.get("SEARXNG_URL",
  DEFAULT_SEARXNG_URL)` so the repo holds one copy of it and the compose guard
  (`tests/test_compose_fragments.py:310`) covers both; the read site stays at `retrieval_app.py`
  ~82, so ENV_REFERENCE's row needs only its line number checked.
- **The state seam (ruling 28).** `app.state.search_providers` holds `list[SearchProvider]` — the
  chain **objects** in chain order, never names. Any name list is derived from it with `[p.name for
  p in chain]`: the startup log line here, `/health` `search_providers` in spec 4. The module-scope
  sentinel is `app.state.search_providers = None`, declared beside `model_task` (~1192–1195) so the
  attribute exists for a transport that never fires lifespan events — the `httpx.ASGITransport`
  clients (`tests/test_app.py:100`, `tests/test_orchestrator.py:1470`) keep working with **no
  fixture edit**.
- Why refuse boot rather than degrade (ENV_REFERENCE step 3's either/or, decided): the chain selects
  code paths, there is no honest `/health` surface for it until spec 4, and silently running the
  default chain would be the nine-day-silent-failure shape CLAUDE.md invariant 5 exists to prevent.
  So `searxng,brave` on an image built from this spec refuses boot (`brave` is unknown until spec
  2); after spec 2 a *known* but key-less name is skipped with a WARNING (spec 2 US-002), which is a
  different case.
- Startup signal: `logger.info("Search providers resolved: %s", ", ".join(p.name for p in chain))`
  beside the config and cache lines — names only, never any other environment value. When the
  variable is set but contains no token, log a WARNING (`search_providers_blank`) saying the default
  applies.
- Lifespan-or-fallback (ruling 22, shaped by ruling 28): add `_resolved_search_providers(state)`
  beside `_resolved_cache_backend` / `_resolved_sanitizer_revision` (~198–221): return
  `state.search_providers` when it is not `None`, otherwise the default one-element chain — a
  list holding only `SearxngProvider(DEFAULT_SEARXNG_URL)`. The fallback is total — it cannot
  raise — so a configuration error can only ever surface at boot; the refuse-boot rule belongs to
  the lifespan alone. Its docstring carries the same defensive-and-production-unreachable note as
  `_resolved_cache_backend`'s.
- Handler wiring: `run_search_pipeline` gains `providers: Sequence[SearchProvider] | None = None`
  and detects "not supplied" with `providers is None` — **never** a falsy check (ruling 29): an
  empty non-`None` sequence is a caller programming error and raises `ValueError` at the top of the
  function (no wire code exists for it; spec 4 raises `policy_excluded_all_providers` in the
  handler *before* the call, and a falsy check would have silently masked that). When given,
  `chain[0]` serves and `searxng_url` is unused; when `None`, the default chain
  `[SearxngProvider(searxng_url)]` is built — the seventeen test call sites keep passing
  `searxng_url=`. The `/search` handler passes
  `providers=_resolved_search_providers(request.app.state)`, replacing the `searxng_url=SEARXNG_URL`
  thread (~1553–1558). `run_search_pipeline` never reads the environment. The failure mapping stays
  as US-002 left it and is reached only through `SearxngProvider`; this story's fakes are driven
  through the success path, and US-004 adds the other branch keyed on `provider.name`.
- Tests that set `app.state.search_providers` on the module singleton — the fake-chain test, the
  fallback test, and the real-lifespan `_running_app` test, which publishes onto the same object —
  use the save/`delattr`/`finally`-restore idiom at `tests/test_app.py` ~1504–1517 (ruling 26d), so
  no test leaves a chain behind for `test_post_search_endpoint_success` (~1725) and its neighbours,
  which expect the real `SearxngProvider`.
- Documentation in the **same change** (ENV_REFERENCE step 6; AGENT_README's "both, same change"
  rule): a `FORAGE_SEARCH_PROVIDERS` row in `docs/configuration.md`'s runtime table beside
  `SEARXNG_URL` (~84) and in `kit_tools/docs/ENV_REFERENCE.md`'s "Runtime service" table (~41), plus
  the conftest-cleared list there (~71). Each row states: default `searxng`; ordered,
  comma-separated; the known names (`searxng` only until spec 2); an unknown name refuses boot;
  blank resolves to the default; and that any entry other than `searxng` sends the caller's query to
  that provider. No compose change — the fragments gain the passthrough in spec 5 US-004 (ruling
  20c). README prose is spec 5 US-001.
- Add `FORAGE_SEARCH_PROVIDERS` to `_CLEARED_ENV_VARS` in `tests/conftest.py` (step 5) **and** to
  the exact-set assertion in
  `tests/test_hermeticity.py::test_the_cleared_environment_is_the_expected_exact_set` (~128), which
  pins the tuple as an exact set on purpose (ruling 26b) — the story's one permitted pre-existing
  test edit — with a sentence in conftest's module docstring saying what the variable changes (it
  selects code paths, matching the `VALKEY_URL` paragraph there).

**Acceptance Criteria:**
- [ ] `parse_provider_names`, `build_provider_chain(names, *, searxng_url)` and
      `SearchProviderConfigurationError(ValueError)` are defined in
      `pipeline/search_providers/__init__.py` (imported by `retrieval_app.py`; `pipeline/` imports
      nothing from the app module); resolution is a static-dictionary lookup with no dynamic
      import; tests cover unset, blank, whitespace-and-stray-comma, explicit `searxng`, duplicate,
      and unknown-name inputs.
- [ ] The lifespan reads `FORAGE_SEARCH_PROVIDERS` once through `SEARCH_PROVIDERS_ENV_VAR` /
      `_configured_provider_names()`, publishes `app.state.search_providers` as a
      `list[SearchProvider]` in chain order, and logs the resolved names (and nothing else from the
      environment); a real-lifespan test (the `_running_app` pattern in `tests/test_app.py`, with
      save/restore) asserts the published chain objects and the log line; the set-but-blank WARNING
      is tested.
- [ ] An unknown name raises `SearchProviderConfigurationError` out of the lifespan (boot refused);
      the message names the entry's position and the known names and contains neither the token
      nor the raw value — a value with an embedded newline yields a one-line message that does not
      contain the text after the newline (precedent `tests/test_model_fetcher.py:1307`); tested
      with `pytest.raises` around `lifespan(probe_app)` on a throwaway `probe_app = FastAPI()` (the
      `CacheConfigurationError` precedent at `tests/test_app.py:1162–1175`), never the module-global
      `app`.
- [ ] `_resolved_search_providers(state)` and the module-scope `None` sentinel exist; every
      pre-existing lifespan-free `/search` test passes with no fixture edit; a test with the
      sentinel in place pins the fallback to a one-element chain whose only provider has
      `name == "searxng"` and `origin == "http://searxng:8080"` (read through the protocol; no
      `isinstance`, no `base_url` access).
- [ ] The `/search` handler passes the resolved chain as `providers=` into `run_search_pipeline`,
      which tests `providers is None` (a `[]` argument raises `ValueError` — one test), uses
      `chain[0]`, and never reads the environment; a `POST /search` test with
      `app.state.search_providers = [fake]` (save/restore) proves the fake served the request and
      a following `/search` test still sees the real provider.
- [ ] `SEARXNG_URL` reaches `SearxngProvider` through `build_provider_chain(..., searxng_url=...)`;
      a test with a non-default value asserts the chain's provider carries it; `retrieval_app.py`
      defaults `SEARXNG_URL` to `DEFAULT_SEARXNG_URL`.
- [ ] `docs/configuration.md` and `kit_tools/docs/ENV_REFERENCE.md` each carry a
      `FORAGE_SEARCH_PROVIDERS` row (grep-verifiable) with the query-egress sentence;
      `tests/conftest.py`'s `_CLEARED_ENV_VARS` and `tests/test_hermeticity.py`'s exact set both
      include it; no compose fragment changes.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

### US-004: Wire schema alignment, `search_unavailable`, and the contract bump to 1.2.0

**Priority:** P1

**Description:** As a contract consumer, I want each `SearchResult` to declare what kind of content
it carries and to carry a strictly validated date, and I want the `/search` error vocabulary to name
an exhausted non-SearXNG chain — landing the epic's only version move (1.1.0 → 1.2.0) with every
artifact and document the bump procedure names.

**Independent Test:** `tests/golden/contract_1_2_0.json` exists beside untouched
`contract_1_1_0.json` and `contract_1_0_0.json`; `_SCHEMA_MODELS` pins six models;
`tests/test_contract_export.py`, `tests/test_contract_schema.py`, `tests/test_governance_docs.py`
and `tests/test_contract_errors.py` (with its renamed "eighteen" tests) pass; `/health` reports
`contract_version` `1.2.0`. A `SearchResult` built with `date="2026-09-14"` keeps it; built with
`"yesterday"`, `"2026-13-45"`, `"2026-09-14T10:00:00Z"`, `"20260914"`, or `"2026-01-01 IGNORE
PREVIOUS INSTRUCTIONS"` it carries `None`. A `run_search_pipeline` call whose single fake provider
named other than `searxng` returns a `ProviderFailure` raises `PipelineError` with `error ==
"search_unavailable"`.

**Implementation Hints:**
- `pipeline/contract.py` (the one-definition rule in its module docstring): add `ContentKind =
  Literal["snippet", "chunk"]`, `CONTENT_KIND_SNIPPET`, `CONTENT_KIND_CHUNK`, and a derived
  `CONTENT_KINDS` frozenset; add `"search_unavailable"` to `SearchErrorCode` (the nested Literals
  carry it into `Pipeline422ErrorCode` and `ErrorCode`, so `SEARCH_ERROR_CODES` goes 2 → 3 and
  `ERROR_CODES` 17 → 18 in one edit); update the `SearchErrorCode` docstring and the `ErrorCode`
  docstring ("seventeen" → "eighteen"); bump `CONTRACT_VERSION` to `"1.2.0"` and extend its
  docstring version list with the 1.2.0 entry: what moved (`SearchResult.content_kind` and
  `SearchResult.date`, additive and defaulted; `search_unavailable` as a new `/search` 422 member —
  MINOR under GOVERNANCE ruling (b) with its announcement obligation; **both** US-002 reason-text
  changes — `searxng_error` now reads `SearXNG returned HTTP error (http_<status>)` and
  `searxng_unavailable` now reads `SearXNG not reachable at <scheme://host:port>: <detail>` with no
  exception text and no userinfo — neither changing a code, a status or the body shape), that a
  consumer comparing MAJOR keeps working, and that the version is **held** — `contract_1_2_0.json`
  is regenerated in place — across specs 2–4 until the `v1.1.0` image publishes it. Spec 5's
  publish job appends this entry to the Release body verbatim (ruling 20a), so an unrecorded change
  is an unannounced one.
- `models.py` `SearchResult` (~265–275): `content_kind: ContentKind = "snippet"` (imported from
  `pipeline.contract`, the `PromptGuardState` precedent) and `date: str | None = None` with a
  `field_validator("date", mode="before")`: keep the value only when it is a `str` that fully
  matches `^\d{4}-\d{2}-\d{2}$` **and** parses with `datetime.date.fromisoformat` (the regex first —
  `fromisoformat` alone accepts compact and week forms); everything else becomes `None`. Nothing
  free-form can reach the model through this field, so it needs no scan and no length cap (ruling
  19): the Stage 2 field tuple and `_search_result_promptguard_input` are unchanged. Both fields
  carry a `description=` — the consumer-facing surface of a headless service, exported into
  `contract/openapi.yaml` — stating the closed value set for `content_kind` (`snippet` for
  SearXNG snippets, `chunk` for provider chunks), the strict calendar-date rule and `None` on
  anything else for `date`, and "Added in contract 1.2.0" on both (the `cache_backend` precedent).
  The model tests extend `tests/test_models.py::TestSearchResult` (~270), the mirror of `models.py`
  per `kit_tools/docs/CONVENTIONS.md:52`; provider-side tests stay in
  `tests/test_search_providers.py`. `engine` keeps today's `isinstance` pass-through; bounding it
  is out of scope, and the accepted risk is written down below.
- Transport: `ProviderSearchResult` gains `content_kind: ContentKind = "snippet"` (one field on the
  US-001 type; update US-001's field-exactness test to include it). The loop copies `content_kind`
  from the result set and `date` from `raw.get("date")` onto every `SearchResult` it builds. SearXNG
  results are `"snippet"`; spec 2's Brave provider sets `"chunk"` on its `ProviderSearchResult`.
  `"chunk"` is declared now because the enum freezes at 1.2.0; it has no producer in this spec, its
  length cap is spec 2 US-001's, and both kinds traverse the same loop bounded by
  `_MAX_SEARCH_SNIPPET_LENGTH` today.
- The raise site (`pipeline/orchestrator.py` only — `tests/test_contract_errors.py` sweeps
  `orchestrator.py` and `retrieval_app.py` for `PipelineError(error="…")` literals and requires
  every vocabulary member to have one): the legacy `searxng_*` codes apply when the configured
  chain is exactly one provider whose `name == "searxng"` — a name comparison on the chain token,
  never `isinstance` (ruling 28; the `CacheBackend` Literal idiom at `retrieval_app.py` ~88). For
  every other chain, a `ProviderFailure` from `chain[0]` raises `PipelineError(error=
  "search_unavailable", reason=f"{failure.provider_name}: {failure.failure_class}", request_id=...)`
  — a closed-vocabulary reason. The predicate is evaluated on the `providers` sequence the handler
  passes (the configured chain); spec 4 keeps it there, never on the per-request effective chain.
  The branch has no production producer until spec 2 registers `brave`; spec 2 uses exactly this
  path for a failed Brave-only chain, and spec 3 extends the reason to every provider traversed.
  SearXNG-only chains keep `searxng_error` / `searxng_unavailable` byte-for-byte (US-002).
- `retrieval_app.py`: the `Pipeline422ErrorResponse.error` description (~570–574, "the searxng_*
  codes on /search") and the `SearchMetricsResponse.errors` description (~367, which lists the two
  codes) name the third code; the `/search` `responses=` declaration keeps declaring 422 through
  `Pipeline422ErrorResponse | HTTPValidationError` — its `error` Literal grows through
  `SearchErrorCode`, so `test_declared_error_statuses_match_the_emission_map` and
  `test_each_declaration_points_at_its_mirror_model` are unchanged; the `/search` route docstring
  (~1549, "through SearXNG") is reworded to the provider chain while the document is already moving.
  `tests/test_app.py:707–714`'s hand-written `searxng_unavailable` fixture text is refreshed to the
  US-002 format here.
- `tests/test_contract_errors.py`: `len(contract.ERROR_CODES) == 18`,
  `len(contract.SEARCH_ERROR_CODES) == 3`, the "10 + 6 + 2 … seventeen rather than eighteen"
  comment, the module docstring (line 8) and section comment (~161), and the two test names —
  `test_error_vocabulary_is_the_documented_seventeen` → `…_eighteen` and
  `test_all_seventeen_codes_render_as_enums_in_the_schema` → `test_all_eighteen_…`.
- `tests/test_contract_schema.py::_SCHEMA_MODELS` gains `SearchRequest` (`models.py`) and
  `Pipeline422ErrorResponse` (`retrieval_app.py`), so the request-model additions of spec 4 and the
  error-body vocabulary are pinned by the golden, not just the four response models.
- The bump procedure, `contract/GOVERNANCE.md` "Bumping the contract" steps 1–7, lifted rather than
  paraphrased: (1) classify — MINOR; (2) bump + the docstring line above; (3) **add**
  `tests/golden/contract_1_2_0.json` as a new file — never edit `contract_1_1_0.json` or
  `contract_1_0_0.json` (ruling (c); `test_the_fixture_retention_ruling_matches_the_tree` guards
  it); (4) `uv run python -m scripts.export_contract`, which writes **three** files —
  `contract/openapi.yaml`, `contract/openapi.yaml.sha256`, and
  `tests/fixtures/contract/unregenerated_openapi.yaml` — consistent only as a set, committed
  together; (5) run the suite; (6) prose: GOVERNANCE's "current contract version" sentence, one
  sentence under ruling (c) stating that an as-yet-unpublished current version's fixture is
  regenerated in place until the version ships (the six-example table gains no row —
  `test_there_are_exactly_six`), `CLAUDE.md` invariant 4 (line 82), `README.md` lines 56 and 230;
  (7) the announcement is mechanical at spec 5 (ruling 20a) from the docstring entry.
- Documentation fan-out, in the same change — every file that states `1.1.0` as the current
  contract, claims seventeen (or 17) codes, embeds the `00b1dbaa…` anchor, or names the current
  `sanitizer_revision`: `kit_tools/docs/TROUBLESHOOTING.md` (61; 71, the `jq .search` comment's
  two keys; 151–154 plus a `search_unavailable` row; 395, the rotation prose; 575),
  `kit_tools/docs/API_GUIDE.md` (43; 108–110; 210, "queries SearXNG"; 236, the `/search` result
  shape gains `content_kind` and `date` with the date-or-`None` rule; 373 plus the row; 414; the
  anchor at 431 and the "currently 1.1.0" statements), `kit_tools/arch/patterns/ERROR_HANDLING.md`
  (18; 102; 285 — the renamed test names — plus the row; ~292, the retained-golden list gains
  `contract_1_2_0.json`), `kit_tools/arch/SERVICE_MAP.md` (75; 193; 195, the rotation prose; 202,
  the embedded anchor — ruling 32 puts it in every regeneration's fan-out; the failure matrix at
  311 plus the row), `kit_tools/docs/MONITORING.md` (65; 82; 143, the third `search.errors` key),
  `kit_tools/testing/TESTING_GUIDE.md` (132), `kit_tools/AGENT_README.md` (29; 140; 199),
  `README.md` and `CLAUDE.md` contract mentions, `kit_tools/docs/CI_CD.md` (297; the anchor at 416;
  483) and `kit_tools/docs/DEPLOYMENT.md` (the anchor at 115; 117; 189; 207). Also sweep
  `kit_tools/arch/CODE_ARCH.md` (108, the bold `**1.1.0**`; the rotation narrative at 131–135),
  `kit_tools/arch/INFRA_ARCH.md` (196), `kit_tools/arch/DECISIONS.md` (612–613) and
  `kit_tools/docs/GOTCHAS.md` (413–414), whose rotation tables mark `8b1b7f78…` as current.
  `kit_tools/arch/SECURITY.md`'s accepted-risk list gains one entry: `SearchResult.engine` is an
  unbounded `isinstance` pass-through whose provenance widens from the operator's SearXNG to any
  chained provider (spec 2's third-party API) — carried forward, not fixed. Suite-count
  bookkeeping (TESTING_GUIDE, AGENT_README, `kit_tools/SYNOPSIS.md`) is spec 5 US-003's close-out
  (ruling 32), not this story's.
- `contract.py` and `orchestrator.py` are both in `_REVISION_SOURCES`; this story rotates
  `sanitizer_revision` a second time. Record it exactly as US-002 did in the three rotation tables
  — `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md` (ruling 32) — and give
  the CODE_ARCH / INFRA_ARCH / GOTCHAS sweeps both rows.

**Acceptance Criteria:**
- [ ] `SearchResult` carries `content_kind: ContentKind` (default `"snippet"`; the Literal,
      constants and `CONTENT_KINDS` are defined in `pipeline/contract.py`) and `date: str | None`
      (default `None`) with the before-validator, each with a `description` that names its rule and
      "Added in contract 1.2.0"; tests in `tests/test_models.py::TestSearchResult` cover the
      accepted form and the five rejected forms above; `ProviderSearchResult.content_kind` defaults
      to `"snippet"` (the US-001 exactness test includes it) and the loop copies `content_kind`
      and `date` onto every result; SearXNG results carry `content_kind="snippet"`; the Stage 2
      field tuple and `_search_result_promptguard_input` are unchanged.
- [ ] `search_unavailable` is a member of `SearchErrorCode`; `len(SEARCH_ERROR_CODES) == 3` and
      `len(ERROR_CODES) == 18`; the renamed "eighteen" tests, the raise-site sweep, and the parity
      tests pass; `run_search_pipeline` raises it (422) for a failing single provider whose `name`
      is not `searxng`, with reason `provider_name: failure_class`, and the discriminator is the
      `name` token (no `isinstance` against `SearxngProvider` in the failure branch); SearXNG-only
      chains still raise `searxng_error` / `searxng_unavailable`; the
      `Pipeline422ErrorResponse.error` and `SearchMetricsResponse.errors` descriptions name it; the
      `/search` route docstring describes the provider chain; a test shows `/metrics`
      `search.errors` counting it under its own key.
- [ ] `CONTRACT_VERSION == "1.2.0"` with the docstring entry described above (both reason-text
      changes and the held-version note); `tests/golden/contract_1_2_0.json` is added and
      `contract_1_1_0.json` and `contract_1_0_0.json` are byte-identical to before;
      `_SCHEMA_MODELS` includes `SearchRequest` and `Pipeline422ErrorResponse`; the three generated
      files are regenerated in the same commit; `tests/test_contract_export.py`,
      `tests/test_contract_schema.py` and `tests/test_governance_docs.py` pass.
- [ ] `contract/GOVERNANCE.md` states `current contract version is **1.2.0**` and ruling (c) carries
      the held-version sentence; the worked-examples table still has exactly six rows; the
      `## Two semvers` section (lines 46–88) gains one forward-looking sentence naming `1.2.0`
      while its historical `v1.0.0` / `1.1.0` worked example stays intact, because
      `tests/test_governance_docs.py:402` asserts both `v1.0.0` and the current `CONTRACT_VERSION`
      appear inside that section.
- [ ] The documentation fan-out is applied: a grep of `README.md`, `CLAUDE.md`,
      `contract/GOVERNANCE.md`, `docs/`, `kit_tools/docs/`, `kit_tools/arch/`, `kit_tools/testing/`
      and `kit_tools/AGENT_README.md` finds no statement that `1.1.0` is the current contract
      (`kit_tools/arch/CODE_ARCH.md:108`'s bold included), no seventeen-code or 17-code claim
      *about the current vocabulary* (the rotation-history records at `CLAUDE.md:194`,
      `docs/bootstrap-notes.md:225` and `kit_tools/docs/GOTCHAS.md:414` describe a past rotation
      and stay as written), no
      `00b1dbaa…` anchor (`kit_tools/arch/SERVICE_MAP.md:202` included), and no statement — table
      or prose — naming `8b1b7f78…` as the current revision; the four error tables
      (TROUBLESHOOTING, API_GUIDE, ERROR_HANDLING, SERVICE_MAP) carry a `search_unavailable` row;
      `MONITORING.md:143` and `TROUBLESHOOTING.md:71` list three `search.errors` keys;
      `API_GUIDE.md:236` shows `content_kind` and `date` in the result shape; ERROR_HANDLING names
      the renamed tests and the three retained goldens; `SECURITY.md` carries the `engine`
      accepted-risk entry.
- [ ] `docs/bootstrap-notes.md`'s rotation table, `CLAUDE.md`'s coexistence narrative and
      `kit_tools/arch/DECISIONS.md`'s rotation table record this story's `sanitizer_revision`
      (before, after, reason) alongside US-002's.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright` (strict) pass.

## Edge Cases

- `FORAGE_SEARCH_PROVIDERS` set but blank, or only commas and whitespace → `["searxng"]` plus a
  WARNING; never an empty chain that returns nothing. US-003.
- Interior empty tokens (`searxng,,searxng` or `searxng, ,searxng`) are dropped, not treated as
  unknown names; duplicates collapse to the first occurrence. US-003.
- Wrong casing or surrounding whitespace → normalized; a name still unknown after normalization
  refuses boot with the entry's position and the known names in the message — never a silent drop,
  never the token, never the raw value. US-003.
- `searxng,brave` on an image built from this spec → boot refused (`brave` is registered by spec 2);
  after spec 2 a known, key-less name is skipped with a WARNING instead (spec 2 US-002). US-003.
- `POST /search` on a transport that never ran the lifespan (the suite's `ASGITransport` clients) →
  `_resolved_search_providers` returns the default one-element chain; no `AttributeError`, and no
  configuration error can surface per request. US-003.
- `run_search_pipeline(providers=[])` → `ValueError` (a caller bug, never masked as "use the
  default"); `providers=None` → the default chain. US-003.
- `SEARXNG_URL` carrying userinfo → never logged at start (names only), never in a provider log
  line, and stripped from the `searxng_unavailable` reason, which echoes scheme, host and port
  only. US-002.
- A provider returns more results than `max_results` (a flooding engine or a misbehaving backend) →
  the orchestrator's slice bounds Stage 1–3 work at `_MAX_SEARCH_RESULTS_SCANNED` regardless.
  US-002.
- SearXNG answers valid JSON of the wrong shape (`{"results": {}}`, `{"results": [null]}`) or the
  provider hits any unforeseen exception → `ProviderFailure` (`hard_error` / `malformed_body` or
  `unexpected`), mapped to `searxng_unavailable` as today's blanket `except` did; never a 500.
  US-002.
- SearXNG answers a body over `_MAX_SEARXNG_RESPONSE_BYTES` → `hard_error` / `body_too_large`
  before any JSON parse. US-002.
- An oversized or control-character-bearing `unresponsive_engines` list → capped and cleaned on
  `SearchResponse`; the honest four-name case is untouched. US-002.
- A raw result dict missing keys → the loop's `raw.get(...)` defaults apply exactly as today; a
  missing or junk `date` becomes `None`. US-002 / US-004.
- SearXNG answers 200 with zero results and a non-empty `unresponsive_engines` → still a
  `ProviderSearchResult` and a 200 in this spec; spec 3 US-002 classifies it (`rate_limited`).
  US-002.
- `date` carrying anything but a strict calendar date, including an instruction-override string →
  `None`; the result is otherwise processed as today. US-004.
- `content_kind` absent from an older sidecar's response (consumer side) → defaults to `"snippet"`
  (additive-safe). US-004.

## Out of Scope

- Any second backend / Brave (spec 2) and fallback traversal, `provider_used`, `fallback_fired`,
  `provider_errors`, the `/metrics` search counters (spec 3).
- Per-request policy params and `/health` provider status (spec 4).
- README prose for the env contract, the compose passthrough (ruling 20c), striking the stale
  "degraded when SearXNG is unreachable" claim, and publishing an image (spec 5).
- In-request retries or backoff on any provider (ruling 18) and any spend ceiling, budget, or rate
  limit (ruling 12).
- Bounding or scanning `SearchResult.engine` (today's `isinstance` pass-through is carried forward;
  it predates this spec, and US-004 records it in `kit_tools/arch/SECURITY.md`'s accepted-risk
  list with its widened provenance).
- The outbound bound on a paid provider's copy of `query` (ruling 30 — spec 2's provider).

## Assumptions

- The per-result sanitization loop already consumes `{title,url,content,engine}` dicts (verified at
  `orchestrator.py` ~708–796), so the provider seam only needs to add `date` to that shape.
- `pipeline/search_providers/` ships with no build change: `Dockerfile` copies `pipeline/` whole and
  `pyproject.toml` includes the package (verified by the codebase-fit review).
- `unittest.mock.patch("pipeline.orchestrator.httpx.AsyncClient")` replaces the attribute on the
  shared `httpx` module object, so a provider that looks up `httpx.AsyncClient` at call time is
  intercepted by the existing patches (the reason US-002 can promise zero edits beyond the helper's
  target string); the suite's mocked response is a `MagicMock` whose `len(resp.content)` is 0, so
  the pre-parse body bound is invisible to it.
- No *Release* is cut between US-002's reason-text change and US-004's bump. A push to `main`
  does publish a `sha-<short>` image on GHCR (`.github/workflows/ci.yml`; `docs/releases.md`: a
  `main` push produces a `sha-` image and no Release), but the announcement channel (ruling 20a)
  rides the `v*` Release only, and spec 5 cuts the only one.

## Technical Considerations

- **Behavior preservation** in US-002 is pinned by the nineteen pre-existing search tests (sixteen
  through `_searxng_client_patch`, three direct patch sites) running with their assertions and call
  arguments untouched — that is the control, and the one permitted test edit is the helper's
  patch-target string. `trust_env=False` and the userinfo strip are the two recorded deviations,
  neither visible to the control.
- **Layout.** `pipeline/search_providers/` is the first nested package under `pipeline/`.
  `CLAUDE.md` invariant 3's sentence that `pipeline/` and `promptguard/` are "the only package
  directories" refers to top-level packages — it governs the flat root modules and the deferred
  `forage/` rename — and no new top-level package is added, so the invariant holds as written;
  nothing in `_REVISION_SOURCES` (an explicit tuple hashed by filename), the `Dockerfile` `COPY`,
  or the wheel include changes.
- **Security properties the seam must preserve**, so the next reviewer sees they were decided:
  results from any provider are untrusted input and reach the wire only through the orchestrator
  loop (and `unresponsive_engines` through the orchestrator's bound at ~834); the scan budget
  (`fetch_limit`, `_MAX_SEARCH_RESULTS_SCANNED`) is orchestrator-enforced after the provider
  returns; provider endpoints are operator-configured, read at start, and exempt from
  `validate_url` on that basis alone; `follow_redirects` stays `False`, TLS verification on,
  `trust_env=False`, and the body is bounded before it is parsed; provider failures are closed
  tokens ending in a catch-all (an `httpx` exception message carries the request URL, query string
  and any userinfo, which is why `str(exc)` leaves the wire and the logs, and why the base-URL echo
  is host:port only); provider bodies are never cached or persisted; the `_REVISION_SOURCES`
  exclusion is backed by the US-001 import test so sanitization logic cannot migrate into a
  provider unnoticed. Walk `kit_tools/arch/SECURITY.md`'s review checklist in the PR description.
- **Query egress.** Adding any entry other than `searxng` to `FORAGE_SEARCH_PROVIDERS` sends the
  caller's query to that provider — a third party once spec 2 lands. The variable's documentation
  says so at the point it is introduced (US-003); per-response attribution (`provider_used`) is spec
  3's.
- **Two `sanitizer_revision` rotations** (US-002 via `orchestrator.py`, US-004 via `contract.py` and
  `orchestrator.py`). By design of the hash, not a bug; each invalidates the `/retrieve` content
  cache. In production both land in the single `v1.1.0` image spec 5 cuts, so the operator sees one
  cold cache, not two. Each rotation is recorded where the previous six are:
  `docs/bootstrap-notes.md`, `CLAUDE.md`, and `kit_tools/arch/DECISIONS.md` (ruling 32).
- **Env-read timing asymmetry**, deliberate: `SEARXNG_URL` keeps its documented import-time read and
  restart-to-apply rule; `FORAGE_SEARCH_PROVIDERS` is read in the lifespan. The lifespan hands the
  former's value to `build_provider_chain`, which is the one place the two meet.
- The contract bump is a single clean MINOR; re-running `export_contract` (three files) is mandatory
  or the drift gate reddens CI; the golden for 1.2.0 is a new file and is regenerated in place by
  specs 3–4 under the held version, per the sentence US-004 adds to ruling (c).

## Related Documentation

- Poppy `kit_tools/specs/EPIC3_SEARCH_RELIABILITY_SPLIT.md` (F1/F2/F12); `epic-search-providers.md`
  rulings 9, 11, 13, 14, 17, 19, 20, 22, 23, 25, 26, 27, 28, 29, 30, 32 and the resolution map.
- `contract/GOVERNANCE.md` (bump procedure, rulings (b), (c), (d));
  `kit_tools/testing/TESTING_GUIDE.md` (commands, `test_mapping`); `kit_tools/docs/ENV_REFERENCE.md`
  ("Adding a New Variable"); `kit_tools/arch/SECURITY.md` (review checklist, observed absences);
  `kit_tools/docs/GOTCHAS.md` (engine-list sync guard, rotation table).
- Code anchors: `pipeline/orchestrator.py`, `models.py`, `pipeline/contract.py`,
  `pipeline/sanitizer_revision.py`, `retrieval_app.py`; tests `tests/test_orchestrator.py`,
  `tests/test_contract_errors.py`, `tests/test_contract_schema.py`, `tests/test_searxng_docker.py`,
  `tests/test_compose_fragments.py`, `tests/test_searxng_smoke.py`, `tests/test_hermeticity.py`,
  `tests/test_governance_docs.py`, `tests/test_cache.py`, `tests/conftest.py`.

## Refinement Notes

Decomposition escalates internal → wire: US-001 (types, including the failure surface specs 2–4
consume) → US-002 (behavior-preserving refactor) → US-003 (configuration and handler wiring) →
US-004 (wire + contract). Only US-004 touches `pipeline/contract.py`, keeping the version move — and
the `search_unavailable` raise site that rides it — to exactly one point in the epic.

All four stories are P1 on purpose. The epic is fully sequential and spec 2's `depends_on` is this
whole spec: `BraveApiProvider` needs the chain registry (US-003) to be reachable at all and the
`content_kind` transport (US-004) to mark its chunks, and spec 2 US-003 raises the
`search_unavailable` code US-004 lands. Marking US-003 or US-004 P2 would signal that spec 2 could
start without them, which is false; the orchestrator executes `execution_order` regardless.

## Clarifications

_None outstanding._

## Open Questions

_None (session_ready)._
