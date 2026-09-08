<!-- Template Version: 2.5.0 -->
---
feature: forage-cache-fallback
status: active
session_ready: true
depends_on: [forage-model-bootstrap]
vision_ref: Secure Web Retrieval / provider-independent web access
type: epic-child
size: M
epic: forage-extraction-forage-side
epic_seq: 3
epic_final: false
created: 2026-09-02
updated: 2026-09-07
---

# Feature Spec: Forage Cache Fallback — Optional Valkey via a Storage-Backend Abstraction

> **Executes in the Forage repo**, after `forage-model-bootstrap` (sequential — both touch
> `/metrics` and the golden fixtures).

## Overview

Make the Valkey content cache optional (owner decision 2026-09-02): when `VALKEY_URL` is
deliberately unset, Forage runs a bounded in-memory backend and reports **healthy**; a
*configured-but-unreachable* Valkey still reports `degraded: cache_unavailable` (Epic 1
semantics unchanged). The mechanism is a **storage-backend abstraction underneath the existing
`ContentCache`** — its security policy (untrusted/blocked-tier refusal, read-time freshness
revalidation with news-domain shortening, tz-naive rejection, zero-TTL purge,
`policy_fingerprint` key derivation) stays single-sourced and runs identically over both
backends; only raw get/set/delete storage is swapped (validation round 1: a standalone
"in-memory twin" would have duplicated five security behaviors). Scope honesty (validation
critical): **the cache serves `/retrieve` only — `/search` has never been cached** and this
spec does not add search caching. `/health` gains one additive field (`cache_backend`),
bumping `CONTRACT_VERSION` to `1.1.0` before spec 5 freezes it.

## Goals

- `docker run -e HF_TOKEN=... forage` (no Valkey anywhere) reaches `status: "healthy"` and a
  repeated `/retrieve` of the same URL is served from the in-memory cache (observable via the
  new storage counters in `/metrics`).
- Configured-but-down Valkey behavior is byte-identical to today: `degraded_reasons` contains
  `cache_unavailable`, requests still succeed (cache-miss semantics).
- Every `ContentCache` policy behavior (tier refusal, freshness revalidation, tz-naive
  rejection, zero-TTL purge, fingerprint keying) passes an identical parametrized test run
  against **both** backends.
- The in-memory backend is bounded (entries and bytes) and its defaults fit the container's
  real memory budget (~128 MiB headroom after the 512+384 MiB pipeline reservations inside
  `mem_limit: 1024m`).
- Zero behavior change for Poppy's deployment path is **delegated and checkable**: spec 6's
  live checklist asserts prod runs `cache_backend: "valkey"`.

## User Stories

### US-001: Storage-backend abstraction + bounded in-memory backend

**Priority:** P1

**Description:** As a Forage maintainer, I want `ContentCache`'s raw storage extracted behind
a small interface with an in-memory implementation, so the cache's security policy has exactly
one implementation over two storages.

**Independent Test:** The parametrized suite — the five policy behaviors (tier refusal,
freshness revalidation, tz-naive rejection, zero-TTL purge, fingerprint keying) **plus the
one behavior that genuinely differs between backends, storage-level TTL expiry** (Valkey
`ex=`-based at `cache.py:406-410` vs the in-memory clock — round-2 finding: policy-only
parity is identical by construction and cannot fail) — passes against both a new
`FakeStorage` test double and the real in-memory storage.

**Implementation Hints:**
- `cache.py` (425 lines) is `ContentCache` over `redis.asyncio`. Extract only the raw
  operations (get/set/delete/ping) into a `CacheStorage` protocol; keep ALL policy in
  `ContentCache`: tier refusal `cache.py:49-55,393-395` (defense-in-depth partner of
  `orchestrator.py:373-380`), read-time TTL/news-domain revalidation `:142-153,332-339`,
  tz-naive rejection `:327-330`, zero-TTL purge `:313-315,389-391`, `policy_fingerprint`
  keying `:94-139` (encodes `classifier_loaded`). The existing reconnect/backoff machinery
  (`:234-271`) stays with the Valkey storage.
- `InMemoryStorage`: dict + monotonic-clock TTL + LRU, bounded by entry count AND total
  bytes; store **serialized JSON bytes** (matches Valkey semantics, exact byte accounting, no
  shared mutable objects — validation security note). Keep mutations synchronous (no awaits
  mid-mutation) for asyncio safety; single-process by design (uvicorn one worker) — say so in
  the docstring.
- Defaults sized to the budget (validation finding): **256 entries / 32 MiB**, configurable
  via a new `config.yaml` `cache:` block. Follow the in-repo config precedent
  `pipeline/extraction_limits.py` (frozen dataclass, `_bounded_int()` validation,
  `*_from_config()` factory, wired in the lifespan at `retrieval_app.py:500-501`).
- Oversized single entry (bigger than the byte bound) → skip caching, serve uncached, count
  it (see US-003 counters).
- Blast-radius honesty (round-2 measurement): the moved cache tests reach **inside**
  `ContentCache` — both `cache` fixtures set `c._client = mock_redis` (48 `mock_redis`
  uses), 9 `patch("cache.aioredis")` blocks, 7 `._client` + 8 `._metrics` refs, plus
  `test_orchestrator.py:339,710`, and **19** `ContentCache()` construction sites. The
  fixture rewire (to the `FakeStorage` seam, assertions unchanged) is part of THIS story's
  scope; keep the constructor backward-compatible (storage injected with a default) and
  consider re-exporting `aioredis` from `cache.py` so the 9 patch targets survive.
- Storage-level counters (hits/misses/evictions/oversize_skips) are **landed here by
  extending the existing `CacheMetrics` dataclass** (`cache.py:161-168`) — the same
  instance is already injected into the cache (`retrieval_app.py:521-522`) and read by
  `/metrics` via `app.state.cache_metrics` (`:642`); pass it through to the storage via the
  `ContentCache(metrics=...)` seam so the route→metrics path that already exists carries
  the new fields (round-3 critical: storage-*owned* counters have no route to `/metrics`,
  500 under `FakeContentCache`, and break the exact-set assertion at
  `test_app.py:421-426` — which gets updated for the new fields instead). Both storages
  (and `tests/fakes.py`'s `FakeContentCache`) carry the fields.
- The storage protocol + `InMemoryStorage` live **in `cache.py`** (flat-layout rule; also
  keeps the 9 `patch("cache.aioredis")` targets valid with no re-export needed — round-3
  location finding).
- Eviction order: purge already-TTL-expired entries before LRU-evicting live ones (round-2
  finding: pure LRU can evict a fresh entry while a dead one lingers under mixed TTLs).
- Hand-rolled over `cachetools.TTLCache` (decided: no new dependency for ~100 lines whose
  eviction semantics we must test anyway).

**Acceptance Criteria:**
- [ ] `CacheStorage` protocol (incl. `connect()`/`close()` — the lifespan calls them) +
      `InMemoryStorage` landed; `ContentCache` policy code paths unchanged and
      single-sourced (no policy logic in either storage).
- [ ] Parametrized suite covers the five policy behaviors AND storage-level TTL expiry,
      against `FakeStorage` + `InMemoryStorage`; the moved test_cache/test_orchestrator
      fixtures rewired to the storage seam with assertions preserved.
- [ ] Entry-count and byte bounds enforced with expired-first-then-LRU eviction (both
      bounds + the ordering tested); oversized entries skipped-not-stored with the counter
      incremented; defaults 256/32 MiB via the `cache:` config block (extraction_limits
      pattern); storage counters (hits/misses/evictions/oversize_skips) maintained.
- [ ] A repeated `/retrieve` of the same URL in memory mode is served from the cache,
      asserted at the route level (round-2 completionist gap: unit-level suites alone could
      pass with a backend `/retrieve` never consults).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-002: Backend selection + default removal

**Priority:** P1

**Description:** As a third-party operator, I want unset `VALKEY_URL` to mean in-memory mode
so the minimum viable deployment is one container plus SearXNG — with configured-but-broken
Valkey still failing loud.

**Independent Test:** Four starts — `VALKEY_URL` fully unset, valid, unreachable, and
**empty-string** — select memory/valkey/valkey/valkey backends respectively, with the last
two reporting `degraded: cache_unavailable`.

**Implementation Hints:**
- Selection (round-2 revision): only a **fully unset** `VALKEY_URL` means in-memory mode.
  **Empty string = configured-and-invalid → degraded** — an empty value is a realistic
  partial-render outcome on the Poppy side and must not silently select memory mode
  (round-2 security finding reversing the round-1 "empty = unset" call). Remove the
  transitional env default (`retrieval_app.py:62`, per spec 1's supersession note); the AC
  is behavioral. `cache.py:192`'s constructor default is the **named exception** — it stays
  (test-compat, spec 1 note) and is out of this AC's scope.
- Garbage/unparseable `VALKEY_URL` → configured-and-failing → degraded `cache_unavailable`,
  never a crash, never silent memory-mode; the parse/selection path must preserve
  `cache.py`'s closed-log-vocabulary invariant (`_closed_vocabulary_reason`) — no code path
  may log the URL (test-asserted; round-2 security finding).
- The moved `test_cache.py` reconnect/degraded suite must stay green untouched — it is the
  regression net for "configured case unchanged".
- Prod-safety cross-ref: Poppy's env file is `required: true` (spec 6) and spec 6 US-007's
  checklist asserts `cache_backend == "valkey"` live — the "missing env file silently drops
  prod to memory-mode" path is closed on the Poppy side, note it here.

**Acceptance Criteria:**
- [ ] Fully-unset → memory; set-and-working → valkey; unreachable, unparseable, and
      empty-string each → valkey-selected + `degraded: cache_unavailable` (all five cases
      tested).
- [ ] No baked `VALKEY_URL` **env** default remains (behavioral test: env fully unset
      selects memory mode, no connection attempt); `cache.py:192` constructor default
      explicitly exempted.
- [ ] The new parse/selection path logs no URL under any of the five cases (test-asserted).
- [ ] `docs/configuration.md` updated: `VALKEY_URL` semantics, `cache:` config keys, the
      prod-side valkey assertion cross-ref.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-003: Health/metrics surface + contract 1.1.0

**Priority:** P1

**Description:** As a Forage consumer, I want `/health` and `/metrics` to say which backend is
active and how the cache is performing, with the additive wire change carried as a minor
contract bump.

**Independent Test:** The four-case matrix reports `"healthy"`/`memory`,
`"healthy"`/`valkey`, `"degraded: cache_unavailable"`/`valkey`, and — with
`app.state.cache` absent — a non-500 `/health` response; the golden schema for 1.1.0
passes.

**Implementation Hints:**
- Wire literal (family-wide validation critical): the status values are
  `Literal["healthy", "degraded"]` — there is no `"ok"`.
- `HealthResponse` is declared **in `retrieval_app.py:106-121`** (not `models.py` —
  validation finding; `tests/test_contract_schema.py:15` imports it from there). Add
  `cache_backend: Literal["valkey", "memory"]`, **sourced from `app.state` at startup on
  the `sanitizer_revision` precedent** (computed once at `:512`, read with a `getattr`
  fallback at `:605-610`) — and cover the `app.state.cache is None` path the handler
  already supports (`:603`) with a fourth matrix case asserting `/health` never 500s
  (`test_app.py:141-154` guards exactly that; round-3 critical). `cache_connected` keeps
  meaning "the selected backend is operational" (in-memory: always true) — documented as a
  clarification alongside the new field.
- Selection must be a **callable, not an import-time constant** (the
  `_legacy_capability_advertisement_enabled()` pattern at `:68-77`) or the per-start cases
  can't be tested; the route-level cache round-trip test's template is
  `test_orchestrator.py:320-400` (round-3 findings).
- `/metrics`: expose US-001's counters via the extended `CacheMetrics` read at
  `app.state.cache_metrics` (`:642`), rendered under the `cache` section at the insertion
  point `retrieval_app.py:669-673`.
  Round-2 correction: pipeline-level `retrieve.cache_hits`/`cache_misses` **already exist**
  (`retrieval_app.py:194-208,664-665`) and keep working in memory mode — the new storage
  counters are a *different layer* (per-operation storage stats vs per-request pipeline
  outcomes); document the distinction in `docs/configuration.md` so the coexisting names
  don't read as duplicates.
- Bump `CONTRACT_VERSION` `1.0.0 → 1.1.0` (`pipeline/contract.py:21`); golden fixture via the
  filename derivation in `tests/test_contract_schema.py:17-21` (`golden/contract_1_1_0.json`
  — dots to underscores). Keep `contract_1_0_0.json` until spec 5 rules on retention.
- `contract.py` is a `sanitizer_revision` source — this edit rotates the revision. Round-2
  correction of the blast-radius claim: Forage's own Valkey keys do **not** include the
  revision (`cache_key()`/`cache_policy_fingerprint()`, `cache.py:94-139`), so old entries
  would serve stale-stamped payloads for up to a TTL — **fix it here** by adding the derived
  revision as an input to the existing pure `cache_policy_fingerprint()` (one kwarg; the
  cheap fix round 2 prescribed, honoring `contract.py`'s own docstring that a contract
  change must invalidate cached extractions). The *Poppy-side* rotation cost
  (`stored_file_extractions` unique-key re-extraction) is real and noted in spec 6.
- `cache_connected` semantics ("selected backend operational"; always true in memory mode)
  gets an explicit AC + doc home: field description in the response model AND
  `docs/configuration.md` — recorded as a documented clarification for spec 5's freeze
  (round-2 finding: it lived only in a hint).
- Golden retention: keep `golden/contract_1_0_0.json` alongside the new 1_1_0 fixture;
  spec 5's GOVERNANCE records the retention rule (this spec just doesn't delete it).
- Poppy-side impact: none required (major-only comparison; minor drift logs); spec 6 vendors
  the 1.1.0 contract.

**Acceptance Criteria:**
- [ ] `cache_backend` in `/health` + storage counters in `/metrics` (at `:669-673`, layer
      distinction documented); the run matrix test-covered with named per-run assertions:
      memory-mode → `status`/`cache_backend`/`cache_connected` = `"healthy"`/`"memory"`/
      `true`; valkey-up → `"healthy"`/`"valkey"`/`true`; valkey-down → `"degraded"` with
      `cache_unavailable`/`"valkey"`/`false`; **and the `app.state.cache is None` case →
      `/health` responds non-500** (micro-verify: the fourth case lived only in a hint).
- [ ] `cache_connected` clarified semantics carried in the field description +
      `docs/configuration.md`.
- [ ] `cache_policy_fingerprint()` includes the derived sanitizer revision (kwarg added,
      test-covered: revision rotation invalidates cache hits).
- [ ] `CONTRACT_VERSION == "1.1.0"`; `golden/contract_1_1_0.json` present via the
      derivation mechanism, `contract_1_0_0.json` retained; rotation acknowledged in the
      commit message.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-004: Example compose fragments + mode docs

**Priority:** P2

**Description:** As a third-party adopter, I want ready-made compose files for both modes so
`docker compose up` works on the first try.

**Independent Test:** `docker compose -f compose/minimal.yml config -q` passes in CI; a
recorded manual smoke run of `minimal.yml` with only `HF_TOKEN` (+ `SEARXNG_SECRET`) set
reaches a working `/retrieve` + `/search` round-trip.

**Implementation Hints:**
- `compose/minimal.yml`: `forage` (weights via spec 3's download-at-start, named volume
  `forage-model-cache:/app/model-cache` — the shared literal `docs/weights.md` pins) +
  **service named `searxng`** running the spec-2 image (`SEARXNG_SECRET` passthrough —
  **required**: spec 2 verified that an unset one is a hard start failure, exit 1, so
  the fragment must pass it or `docker compose up` dies on the first try). **Corrected
  by spec 2 US-004, 2026-09-08:** the verified backend env var is **`SEARXNG_VALKEY_URL`**
  (`valkey://valkey:6379/0`), not the working name `SEARXNG_REDIS_URL` this hint used to
  carry — upstream renamed the setting family to Valkey and marks `redis.url` deprecated;
  the old name still works but warns. **And do not wire a limiter into these fragments
  at all**: spec 2 measured that `SEARXNG_LIMITER=true` with a working backend refuses
  Forage's own httpx client with HTTP 429 on the *first* request, so a compose fragment
  that turned it on would fail its own `/search` round-trip. The spec-2 image ships
  `limiter: false`; leave it. See `docs/searxng.md` — service names MUST match the code's
  neutral defaults
  (`http://searxng:8080`; round-2 finding: a `forage-searxng` service name breaks the
  fragment's own round-trip with `SEARXNG_URL` unset) — no Valkey for the content cache.
- `compose/full.yml`: adds pinned `valkey/valkey:8` + explicit `VALKEY_URL` wiring; service
  hostnames match the code's expectations when configured.
- CI gate: a `compose-validate` step running `docker compose -f ... config -q` on both
  fragments (cheap, machine-checkable — validation finding that "smoke-tested" alone is
  unverifiable); the full live smoke is a recorded manual transcript in Implementation Notes.
- README mode matrix: memory vs Valkey (persistence, per-process caveat), and repeat the
  deployment posture line (no auth — private network only; ports published to loopback in the
  fragments: `127.0.0.1:8020:8020`, never `0.0.0.0` — validation security finding).
- This is the epic completion criterion's proof artifact ("third party can compose up with
  only `HF_TOKEN`") — header comment says so.

**Acceptance Criteria:**
- [ ] Both fragments committed with code-matching service hostnames; `config -q` green in
      CI for both; loopback-only port bindings; manual smoke transcript recorded (this AC
      is a **human gate** — marked supervised in the epic wrapper, round-2 finding).
- [ ] README mode matrix + posture line; fragments referenced from the quickstart.
- [ ] Tests written/updated for new functionality (the CI `config -q` step counts)
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

## Edge Cases

- Garbage `VALKEY_URL` → degraded, not crash, not silent memory-mode (US-002).
- Valkey dies after connect → existing reconnect/degraded behavior unchanged — covered by the
  moved `test_cache.py` suite (US-002).
- Concurrent async requests on in-memory storage → synchronous mutations, no torn state
  (US-001).
- Oversized entry → skip + counter (US-001/US-003).
- Prod env file missing → Poppy-side `required: true` fails the deploy loudly; live drill
  asserts `cache_backend == "valkey"` (spec 6; cross-referenced in US-002).

## Out of Scope

- **Search-result caching** — `/search` has never been cached (`run_search_pipeline` takes no
  cache; `pipeline/orchestrator.py:633-640`) and this spec does not add it; a deliberate
  search-cache is Epic 4 material if ever.
- Cache integrity/HMAC and contract-version-aware cache keys — Epic 4.
- Multi-process/shared in-memory cache; persistence for memory mode.
- Changing Poppy's deployment mode (stays Valkey; spec 6 asserts it).

## Assumptions

- Single uvicorn worker (current CMD) — in-memory storage is per-process by design.
- The 1.1.0 minor bump lands before spec 5's freeze (enforced by the sequential Forage-local
  order: model-bootstrap → this → contract).

## Technical Considerations

- Runs after `forage-model-bootstrap` (shared `/metrics` + golden-fixture surface; sequential
  ordering removes the parallel-edit collision flagged in round 1).
- Memory budget arithmetic: pipeline reservations 512 MiB parent + 384 MiB child inside
  `mem_limit: 1024m` leave ~128 MiB; the 32 MiB default cache bound spends a quarter of it.

## Related Documentation

- Epic (this repo): [epic-forage-extraction-forage-side.md](epic-forage-extraction-forage-side.md)
- Planned in Poppy (canonical planning record, one-way sync — see the wrapper's Notes):
  [`epic-forage-extraction.md`](https://github.com/WashingBearLabs/Poppy/blob/main/kit_tools/specs/epic-forage-extraction.md) ·
  [`WEB_ACCESS_FAMILY.md`](https://github.com/WashingBearLabs/Poppy/blob/main/kit_tools/specs/WEB_ACCESS_FAMILY.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Storage-backend abstraction under `ContentCache`, not a parallel in-memory twin
**Rationale:** `cache.py` carries five security-relevant policy behaviors (tier refusal,
freshness revalidation, tz-naive rejection, zero-TTL purge, fingerprint keying); a twin
implementation duplicates enforcement and drifts. Parametrized tests over both storages make
the parity a proof.
**Source:** validation round 1 security review of this spec (behaviors verified at
`cache.py:49-55,94-139,142-153,313-315,327-339,389-395`).

**Decision:** Reframe verification around `/retrieve` — no search caching exists or is added
**Rationale:** `/search` never touches the cache (`orchestrator.py:633-640`); the original
Goal 1 asserted a flow that does not exist.
**Source:** validation round 1 critical (completionist/salty/codebase-fit, independently).

**Decision:** In-memory bounds 256 entries / 32 MiB, JSON-serialized values
**Rationale:** real headroom is ~128 MiB after documented pipeline reservations; serialized
storage matches Valkey semantics and gives exact byte accounting.

### Scope Adjustments

- 2026-09-02 validation round 1: restructured 3 → 4 stories around the storage abstraction;
  fixed the `status: "ok"` literal and the `HealthResponse` location; replaced the
  search-caching premise; added storage counters, concrete bounds, the garbage-URL case,
  compose `config -q` CI gate, loopback port bindings; `depends_on` corrected to
  `forage-model-bootstrap` (sequential) so the minimal-compose smoke can actually load
  weights.

### Decisions Made

## Clarifications

### Session 2026-09-02
- Q: Keep Valkey a hard dependency (defer optionality to Epic 4) or make it optional now? →
  A: **Make it optional now** (owner overrode the defer recommendation) — implemented after
  round 1 as a storage-backend swap under the single policy layer; Epic 1 degraded semantics
  preserved for the configured case; additive `/health` field bumps the contract to 1.1.0
  before the spec 5 freeze.
