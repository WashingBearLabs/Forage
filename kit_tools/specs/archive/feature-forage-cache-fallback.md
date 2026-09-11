<!-- Template Version: 2.5.0 -->
---
feature: forage-cache-fallback
status: completed
session_ready: true
depends_on: [forage-model-bootstrap]
vision_ref: Secure Web Retrieval / provider-independent web access
type: epic-child
size: M
epic: forage-extraction-forage-side
epic_seq: 3
epic_final: false
created: 2026-09-02
updated: 2026-09-11
completed: 2026-09-11
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
- [x] `CacheStorage` protocol (incl. `connect()`/`close()` — the lifespan calls them) +
      `InMemoryStorage` landed; `ContentCache` policy code paths unchanged and
      single-sourced (no policy logic in either storage).
- [x] Parametrized suite covers the five policy behaviors AND storage-level TTL expiry,
      against `FakeStorage` + `InMemoryStorage`; the moved test_cache/test_orchestrator
      fixtures rewired to the storage seam with assertions preserved.
- [x] Entry-count and byte bounds enforced with expired-first-then-LRU eviction (both
      bounds + the ordering tested); oversized entries skipped-not-stored with the counter
      incremented; defaults 256/32 MiB via the `cache:` config block (extraction_limits
      pattern); storage counters (hits/misses/evictions/oversize_skips) maintained.
- [x] A repeated `/retrieve` of the same URL in memory mode is served from the cache,
      asserted at the route level (round-2 completionist gap: unit-level suites alone could
      pass with a backend `/retrieve` never consults).
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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
- [x] Fully-unset → memory; set-and-working → valkey; unreachable, unparseable, and
      empty-string each → valkey-selected + `degraded: cache_unavailable` (all five cases
      tested).
- [x] No baked `VALKEY_URL` **env** default remains (behavioral test: env fully unset
      selects memory mode, no connection attempt); `cache.py:192` constructor default
      explicitly exempted.
- [x] The new parse/selection path logs no URL under any of the five cases (test-asserted).
- [x] `docs/configuration.md` updated: `VALKEY_URL` semantics, `cache:` config keys, the
      prod-side valkey assertion cross-ref.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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
- [x] `cache_backend` in `/health` + storage counters in `/metrics` (at `:669-673`, layer
      distinction documented); the run matrix test-covered with named per-run assertions:
      memory-mode → `status`/`cache_backend`/`cache_connected` = `"healthy"`/`"memory"`/
      `true`; valkey-up → `"healthy"`/`"valkey"`/`true`; valkey-down → `"degraded"` with
      `cache_unavailable`/`"valkey"`/`false`; **and the `app.state.cache is None` case →
      `/health` responds non-500** (micro-verify: the fourth case lived only in a hint).
- [x] `cache_connected` clarified semantics carried in the field description +
      `docs/configuration.md`.
- [x] `cache_policy_fingerprint()` includes the derived sanitizer revision (kwarg added,
      test-covered: revision rotation invalidates cache hits).
- [x] `CONTRACT_VERSION == "1.1.0"`; `golden/contract_1_1_0.json` present via the
      derivation mechanism, `contract_1_0_0.json` retained; rotation acknowledged in the
      commit message.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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
- [x] Both fragments committed with code-matching service hostnames; `config -q` green in
      CI for both; loopback-only port bindings; manual smoke transcript recorded (this AC
      is a **human gate** — marked supervised in the epic wrapper, round-2 finding).
- [x] README mode matrix + posture line; fragments referenced from the quickstart.
- [x] Tests written/updated for new functionality (the CI `config -q` step counts)
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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

### US-001 — Storage-backend abstraction + bounded in-memory backend (2026-09-10)

**Shape landed.** `cache.py` (456 → 847 lines) now carries four things where it carried
one: the `CacheStorage` protocol, `ValkeyStorage`, `InMemoryStorage`, and a `ContentCache`
that is *only* policy. All four live in `cache.py` per the flat-layout rule, which also
kept the nine `patch("cache.aioredis")` targets valid with no re-export needed (the
round-3 location finding held).

- **`CacheStorage`** — `connected` (property), `connect()`, `close()`, `ping_if_due()`,
  `get(key) -> bytes | None`, `set(key, value, *, ttl_seconds) -> bool`,
  `delete(key) -> bool`. `connect()`/`close()` are in because the lifespan calls them;
  `ping_if_due()` is in because `/health` does, and a storage with no connection to lose
  answers it for free.
- **`ContentCache`** kept `get`/`put`/`delete` signatures byte-identical and lost every
  connection concern: the `_ensure_client()` pre-checks are gone (a storage that cannot
  serve returns `None`/`False` on its own), `_delete_key()` collapsed into
  `self._storage.delete(key)`. What stayed: tier refusal, read-time revalidation with
  news-domain shortening, tz-naive rejection, zero-TTL purge, fingerprint keying. **No
  policy logic exists in either storage** — mutation M5 below proves the refusal has a
  single home.
- **`ValkeyStorage`** took the whole reconnect stack unchanged (bounded 2 s connect
  deadline, doubling backoff, single-flight lock, `_closed_vocabulary_reason`). The
  `TestReconnect` suite — the "configured case unchanged" regression net US-002 leans on —
  needed changes in *two* of its eight tests (supervisor correction: the notes first
  claimed one; the verifier's diff found the second).
- **Constructor stayed backward-compatible**: `ContentCache(valkey_url=..., metrics=...,
  storage=None)` still builds a Valkey-backed cache. Nothing in `retrieval_app.py`
  selects a backend — that is US-002.

**Blast radius, remeasured (round-2 hint numbers vs. actual).** The hints' counts were
close but not exact, and the seam absorbed most of them:

| Hint (round 2) | Measured at implementation | What it cost |
|---|---|---|
| 48 `mock_redis` uses | 48 | 38 gone — the two policy fixtures now inject `FakeStorage`; 10 legitimately remain in the Valkey failure-path tests (supervisor correction: "all gone" was overcounted) |
| 9 `patch("cache.aioredis")` blocks | 9 | **0 changed** (module-level `aioredis` still the target) |
| 7 `._client` + 8 `._metrics` refs | 9 + 8 | 5 `._client` rewired to a `ValkeyStorage`; all 8 `._metrics` survive verbatim |
| 19 `ContentCache()` sites | 21 in tests (24 repo-wide incl. this spec's own prose) | 4 rewired, the rest untouched |
| `test_orchestrator.py:339,710` | `:316,687` | Both replaced by `ContentCache(storage=FakeStorage())` — ~26 lines of hand-rolled dict plumbing deleted |

**Delta-losslessness verified mechanically.** `pytest --collect-only` node IDs were
diffed against `main`: **zero removed, 63 added** (1251 → 1314). Every rewired test kept
its name and its assertion's meaning — `mock_redis.set.call_args.kwargs["ex"]` became
`storage.last_ttl_seconds`, `mock_redis.delete.assert_awaited_once()` became
`storage.delete_calls == 1`.

**Counter naming — a deliberate departure.** The hint names the counters
"hits/misses/evictions/oversize_skips"; they landed as `storage_hits`, `storage_misses`,
`storage_evictions`, `storage_oversize_skips` on `CacheMetrics`. `/metrics`' `cache`
section would otherwise carry a bare `hits` two lines below `retrieve.cache_hits`, and the
round-2 correction is explicit that these are *different layers* (per-operation storage
stats vs. per-request pipeline outcomes). The prefix makes the distinction unmissable at
the point of confusion rather than only in prose. US-003's doc AC still stands.

**`/metrics` exact-set assertion updated, not loosened** (`test_app.py`) — the four new
names were added to the set. `/health` and `tests/golden/` are **byte-unchanged**;
`CONTRACT_VERSION` stays `1.0.0`; `contract_smoke` green. The bump is US-003's.

**`sanitizer_revision` did not rotate** — verified before and after:
`5927038d64ed54a619e52b94899148f56bfede37d38f3c1a53c435edc719d111` both times. `cache.py`
is not a `_REVISION_SOURCES` member and `derive_sanitizer_revision()` reads only
`promptguard_threshold` out of `config.yaml`, so the new `cache:` block is invisible to it.

**In-memory design decisions.**

- **Serialised JSON bytes**, not objects — exact byte accounting *and* the security
  property that no caller ever holds a live reference to a cached value.
- **Synchronous mutations.** No `await` sits inside any mutation, so concurrent requests
  on the one event loop cannot interleave into torn state; a 64-way `asyncio.gather`
  test pins the byte accounting.
- **Eviction is expired-first, then LRU, and only under pressure.** `_enforce_bounds()`
  returns immediately when both bounds hold, which keeps `storage_evictions` meaning
  "removed to make room". An entry that merely aged out and was noticed on the next read
  is a `storage_misses` — that distinction is asserted.
- **Oversized entry** (larger than the whole byte bound) is skipped, counted, *and*
  discards any entry it supersedes at that key, so a skipped write can never leave the
  previous payload serving.
- A non-positive `ttl_seconds` stores nothing (Valkey would raise on `EX=0`;
  `ContentCache` never calls it, the guard is defensive).

**Config.** `CacheSettings` / `cache_settings_from_config()` live in `cache.py`, not under
`pipeline/`: the import dependency runs `pipeline.orchestrator → cache`, so importing
`pipeline.extraction_limits._bounded_int` from here would close that loop for twelve
lines. The idiom is re-stated locally with that reasoning in its docstring. Defaults
**256 entries / 32 MiB**, ranges 1–4096 and 1–128 MiB, wired in the lifespan as
`app.state.cache_settings` and validated there whichever storage is active — a bad
`cache:` block fails the boot, exactly as `extraction:` does.

`docs/configuration.md` gained a `cache:` block section (keys, ranges, the two-layer
counter distinction, the `/retrieve`-only scope honesty). US-002's doc AC — `VALKEY_URL`
semantics and the prod-side valkey cross-ref — is untouched and still owed.

**Mutation verification — 13 mutants, 13 killed**, each by a named test:

| Mutant | Killed by |
|---|---|
| M1 expired-first → pure LRU | `test_expired_entries_are_purged_before_a_live_one_is_evicted` |
| M2 drop the byte bound | `test_the_byte_bound_evicts_until_the_total_fits` |
| M3 drop the entry-count bound | `test_the_entry_count_bound_evicts_the_least_recently_used` |
| M4 store the oversized entry | `test_an_entry_larger_than_the_byte_bound_is_skipped_not_stored` |
| M5 drop tier refusal from the policy layer | `test_put_untrusted_skipped` |
| M6 drop the in-memory TTL check | `test_the_storage_expires_an_entry_on_its_own_clock[in_memory_storage]` |
| M7 drop the tz-naive rejection | `test_a_tz_naive_entry_is_refused_and_purged[fake_storage]` |
| M8 drop the zero-TTL read purge | `test_get_ttl_zero_skips_read_and_deletes_variant` |
| M9 stop validating `cache:` at startup | `test_lifespan_publishes_the_validated_cache_settings` |
| M10 drop the storage counters from `/metrics` | `test_metrics_covers_search_retrieve_and_cache_sections` |
| M11 in-memory storage never hits | `test_cacheable_tiers_are_stored[in_memory_storage-standard]` |
| M11r same, scoped to the route suite | `test_post_retrieve_repeat_is_served_from_the_in_memory_cache` |
| M12 `/retrieve` never writes to the storage | `test_post_retrieve_repeat_is_served_from_the_in_memory_cache` |

M11r and M12 exist to answer the round-2 completionist gap directly: the route-level test
is a real gate, not decoration — it goes red on a backend `/retrieve` never consults *and*
on one it never writes to.

**Gates.** 1314 passed (baseline 1251 + 63); `ruff check` / `ruff format --check` /
`pyright --strict` / `actionlint` all zero. `CacheStorage` conformance is asserted
statically — `test_every_storage_satisfies_the_cache_storage_protocol` annotates a
`list[CacheStorage]` holding all three implementations, which is where pyright checks a
structural protocol.

### US-002 — Backend selection + default removal (2026-09-10)

**What landed.** `retrieval_app.py` lost its `VALKEY_URL` module constant and gained two
functions: `_configured_valkey_url()` (reads the env var, returns `None` only when it is
*fully unset*) and `_select_cache_storage(settings=…, metrics=…)`, which returns
`(storage, backend_name)`. The lifespan injects the result —
`ContentCache(storage=storage, metrics=…)` — so the service never touches
`ContentCache`'s own `valkey_url` default. That default is untouched, exactly as the AC
exempts it: it is now *only* a test-facing constructor convenience, which is what the
spec-1 note said it was.

Selection is a **callable, not an import-time constant** (US-003's prescribed pattern,
adopted here because this is the story that builds the thing). Production reads the
variable once per start either way; the difference is that five starts are now testable
through the real env, rather than through a module attribute no operator has.

**The five cases, all driven through the real `lifespan` and the real `ContentCache`:**

| `VALKEY_URL` | Storage selected | `/health` |
|---|---|---|
| fully unset | `InMemoryStorage` | `healthy`, `degraded_reasons == []` |
| valid, reachable | `ValkeyStorage` | `healthy` |
| unreachable | `ValkeyStorage` | `degraded`, `["cache_unavailable"]` |
| unparseable (`http://…`) | `ValkeyStorage` | `degraded`, `["cache_unavailable"]` |
| empty string | `ValkeyStorage` | `degraded`, `["cache_unavailable"]` |

The tests deliberately do **not** patch `ContentCache` (unlike `_running_app`, the
existing lifespan harness) — a patched cache would answer both questions the story
asks. Only Valkey's socket is a double; the unreachable case is caught by the suite's own
socket guard, which is the honest shape of "unreachable" in a hermetic suite.

**`/health` needed no change, and that is the story boundary.** Memory mode reports
healthy *through Epic 1's existing handler*: `InMemoryStorage.ping_if_due()` is `True`, so
`cache_connected` is true and `cache_unavailable` is never appended. The status flip is
therefore a consequence of selection, not a separate edit, and this story asserts it
behaviourally. What is left for US-003 is the additive **`cache_backend` wire field** and
the `1.1.0` bump — genuinely new surface. `_select_cache_storage` already returns the
backend name (the startup log consumes it), so US-003's plumbing is
`app.state.cache_backend = backend` plus the response model.

**Deliberately not done here:** `app.state.cache_backend` (US-003's field has no consumer
yet), and the compose fragments / README mode matrix (US-004's).

**The one frozen-suite line that had to move.** `TestReconnect::
test_connect_failure_never_logs_url_or_secret` patched `retrieval_app.VALKEY_URL` — the
attribute this story's AC deletes — so `monkeypatch.setattr` would have raised
`AttributeError`. It is now `monkeypatch.setenv("VALKEY_URL", startup_url)`: one line,
every assertion and the canary unchanged, and the test is strictly stronger for it (it
exercises the operator's real path instead of a test seam). Nothing else in
`TestReconnect`'s eight tests moved, and the suite stayed green throughout — it did its
job as the "configured case unchanged" net.

**The CI smoke needed no change, and the `degraded_reasons` question was checked, not
assumed.** `contract_smoke.py` asserts `status == "degraded"` and
`promptguard_unavailable in degraded_reasons` — it never asserts `cache_unavailable`, and
it enumerates no field list of its own (everything comes from `HealthResponse` and
`pipeline.contract`). The smoke job runs the image with no `-e` of any kind, so after this
story it runs in **memory mode**: `cache_connected: true`, `degraded_reasons:
["promptguard_unavailable"]`, `status: "degraded"` — still degraded, for the weights, which
is the contract that job exists to pin. The PRIOR_LEARNINGS note that it "expects
`cache_unavailable`" did not hold against the source.

**Hermeticity: `VALKEY_URL` joined the cleared environment.** It now *selects a backend*,
so a developer with one exported would run every lifespan test down the Valkey path while
CI ran them down the memory path — and it is the one variable that routinely carries a
password, inside a suite whose assertions read the log. `tests/conftest.py`'s
`_ACQUISITION_ENV_VARS` became `_CLEARED_ENV_VARS` and gained it. That guard was
initially unenforced (mutant M6 below passed), so it got the same treatment the socket
guard got: an exact-set gate plus an executing canary in `tests/test_hermeticity.py`,
which is the module `test_mapping` already points `conftest.py` at.

**Mutation verification — 6 mutants, 6 killed:**

| Mutant | Killed by |
|---|---|
| M1 reinstate the env default `os.environ.get("VALKEY_URL", "redis://valkey:6379/4")` | `test_unset_valkey_url_runs_in_memory_healthy_and_never_connects` (+ `test_only_a_fully_unset_valkey_url_reads_as_absent`) |
| M2 collapse empty into unset (`… or None`) | `test_a_broken_valkey_url_degrades_and_never_falls_back_to_memory[empty-string]` |
| M3 fall back to memory when the configured Valkey fails to connect | all three `…never_falls_back_to_memory` cases (+ `TestReconnect::test_connect_failure_never_logs_url_or_secret`) |
| M4 log the URL on the selection path | `test_no_selection_path_logs_the_valkey_url[valid/unreachable/unparseable]` (+ the same `TestReconnect` test) |
| M5 "helpfully" route an unparseable URL to memory | `…never_falls_back_to_memory[unparseable]` and `[empty-string]` |
| M6 drop `VALKEY_URL` from the cleared environment | `test_the_cleared_environment_is_the_expected_exact_set` |

M4 is the AC's closed-log-vocabulary requirement as a gate: the selection path hands the
raw URL straight to `ValkeyStorage` and never parses, splits or interpolates it, because
a parse attempt at this layer would be a second place for a password to reach a log line.
The five-case log test asserts the URL, its password token and both hostnames are absent
from a `DEBUG`-level capture of the whole start.

**Docs.** `docs/configuration.md` gained a **"Cache backend selection"** section (the
five-case table, why empty is configured-and-invalid, "no connection attempt at all when
unset", the memory-mode consequences list, and the `cache_connected` clarification), plus
corrections to the `VALKEY_URL` row (default is now *unset*), the `degraded_reasons` row,
and the "read once at import time" line. The prod-side cross-ref is a callout there:
Poppy's env file is `required: true`, so a missing one fails that deploy loudly rather
than dropping prod to memory mode, and spec 6 US-007's live checklist asserts the running
backend is Valkey. It is phrased without naming the not-yet-shipped `cache_backend` field.
`README.md`'s quickstart, companion paragraph and `VALKEY_URL` bullet were corrected for
the same reason — they stated a default that no longer exists — leaving the full mode
matrix to US-004.

**Live container evidence, from the PR's own smoke job.** The `smoke` job is now the
memory-mode deployment running for real — a container with no `VALKEY_URL` (indeed no
`-e` at all), and its `/health` body is the story's goal in one line:

```json
{"status":"degraded","promptguard_loaded":false,"cache_connected":true,
 "capabilities":{},"sanitizer_revision":"5927038d…19d111",
 "contract_version":"1.0.0","degraded_reasons":["promptguard_unavailable"]}
```

`cache_connected: true` with **no `cache_unavailable`** — on `main` that same container
reported both `false` and the reason, because the deleted default sent it at a Valkey
that was never there. The remaining degradation is the weights, which is that job's
whole point. `docker run -e HF_TOKEN=… forage` reaching `healthy` (Goal 1) is now down
to the token alone.

**Gates.** 1327 passed (1314 + 13); `ruff check` / `ruff format --check` /
`pyright --strict` / `actionlint` all zero; PR CI green including `smoke`.
`sanitizer_revision` verified **unrotated** before and after:
`5927038d64ed54a619e52b94899148f56bfede37d38f3c1a53c435edc719d111`
(neither `retrieval_app.py` nor `conftest.py` is a `_REVISION_SOURCES` member).

### US-003 — Health/metrics surface + contract 1.1.0 (2026-09-10)

**What landed on the wire.** `HealthResponse` (still in `retrieval_app.py`, not
`models.py`) gained `cache_backend: CacheBackend` — the `Literal["valkey", "memory"]`
alias US-002 already declared, so the two strings have one definition across the
selection, the startup log and the response. `cache_connected` gained the field
description its semantics had been living in prose: *"the selected backend is
operational… not a statement that Valkey is present — read `cache_backend` for that."*
`CONTRACT_VERSION` is `1.1.0`, additive.

`cache_backend` is **published once by the lifespan** (`app.state.cache_backend =
backend`, the name `_select_cache_storage` already returned) and read per request, on the
`sanitizer_revision` precedent. Two small helpers do the reading —
`_resolved_cache_backend(state)` and `_resolved_sanitizer_revision(state)` — and the
second one exists because `/retrieve` now needs the revision too, so `/health`'s inline
fallback became a function rather than a second copy. `/extract`'s own
`getattr(..., derive_sanitizer_revision(...))` call was left alone: it is out of this
story's scope, and it is worth knowing that its default argument evaluates *eagerly* on
every request. The new helper does not.

**The fallback is load-bearing, not defensive.** `cache_backend` is a **required** field,
so a request that found nothing on `app.state` would fail model validation and 500 the one
endpoint that must never return non-200. That is the fourth matrix case, and it is the
reason the field resolves through `_configured_cache_backend()` — the same question the
lifespan asked, answered from the environment — rather than a hard-coded `"memory"`.

**The four runs, each asserted by name:**

| Run | `status` | `cache_backend` | `cache_connected` | `degraded_reasons` |
|---|---|---|---|---|
| `VALKEY_URL` unset | `healthy` | `memory` | `true` | `[]` |
| set + reachable | `healthy` | `valkey` | `true` | `[]` |
| set + unreachable | `degraded` | `valkey` | `false` | `["cache_unavailable"]` |
| `app.state.cache` absent | (200, not 500) | `memory` → `valkey` as the env changes | `false` | `cache_unavailable` |

The first three run through US-002's `_started_with_valkey_url` harness — the real
lifespan and the real `ContentCache`, only Valkey's socket doubled. The fourth runs on the
lifespan-less `client` fixture with `app.state.cache = None` **and** the published
`cache_backend` deleted, which is the only way to exercise the fallback at all: `app` is a
module singleton, so a lifespan test earlier in the session leaves a value behind.

**The stale-stamped-payload window, closed.** `cache_policy_fingerprint()` takes
`sanitizer_revision` as a required kwarg and folds it into the hashed inputs;
`run_retrieve_pipeline()` takes it as a required keyword-only parameter and `/retrieve`
passes the one this process derived. Both were made *required* rather than defaulted —
a default would have let the production caller forget and leave the fix inert with every
test still green. The cost was mechanical: 6 fingerprint call sites in `test_cache.py`,
16 pipeline call sites in `test_orchestrator.py`, all one line each, and pyright-strict
caught the set exhaustively.

The pipeline is where the value comes from because it is where it already came from:
`run_extract_pipeline` has taken `sanitizer_revision: str` from the route since Epic 1.
Deriving it inside the orchestrator instead would have put eight file reads and a sha256
on every `/retrieve`.

**Rotation, taken and attributed.** Two `_REVISION_SOURCES` files moved:

```
before: 5927038d64ed54a619e52b94899148f56bfede37d38f3c1a53c435edc719d111
after:  fa4691c57449c52fe367208bdeeb650cbe474d93485c3b90cc8989b5e593547c
```

Attribution was **measured**, not assumed — re-deriving with each edit reverted in turn
gives `b9b716c3…` (only `contract.py` reverted) and `7bfbeed5…` (only `orchestrator.py`
reverted), so both edits are load-bearing and neither alone produces the shipped value.
Recorded in `docs/bootstrap-notes.md` as the fifth rotation, with the same table;
`kit_tools/docs/GOTCHAS.md`, `CLAUDE.md` and `kit_tools/arch/CODE_ARCH.md` were
grep-updated to the new value, and the commit message carries the before/after per the AC.

This is the **first rotation whose point is the invalidation rather than its price**.
`pipeline/contract.py`'s docstring has always said a contract change must invalidate
cached extractions sanitized under the old contract; nothing inside Forage enforced it,
because the key mixed in every caller-supplied policy knob and not the pipeline's own
revision. Now it does, so the bump is also the flush. Forage-side cost: entries become
unreachable at the next start and age out on their own TTL — free in memory mode. The
Poppy-side cost (`stored_file_extractions` re-extraction) is unchanged from the fourth
rotation and is spec 6's, already noted there; nothing new is owed.

**Golden fixtures.** `tests/golden/contract_1_1_0.json` was produced by the derivation
mechanism itself (`json.dumps(_current_schemas(), indent=2, sort_keys=True)`, which
reproduces `contract_1_0_0.json` byte-for-byte on the old tree). The diff against 1.0.0 is
purely additive and entirely inside `HealthResponse`: the new property, the new
`cache_connected` description, one more entry in `required`. `contract_1_0_0.json` is
**retained** — spec 5's GOVERNANCE rules on retention; this story just does not delete it.

**The CI smoke: confirmed, not assumed.** `contract_smoke.py` itself needed **no change**
— it derives its expected field set from `HEALTH_MODEL.model_fields` and reads
`CONTRACT_VERSION` from `pipeline/contract.py` at job time, so the new field and the new
version arrived on their own. What it *did* catch immediately is that
`tests/test_contract_smoke.py`'s `_health_body()` fixture is a hand-written payload: ten
of its tests went red until `cache_backend` was added to it, including
`test_field_expectations_track_the_model`, which deletes each declared field in turn. The
value in the fixture is `"memory"`, because the smoke job runs the image with no `-e` of
any kind — the same memory-mode container US-002's live evidence recorded.

**Live container evidence, from the PR's own `smoke` job** (PR #14, run 34568618988):

```
Expecting contract_version 1.1.0 (pipeline/contract.py)
{"status":"degraded","promptguard_loaded":false,"cache_connected":true,
 "capabilities":{},"sanitizer_revision":"fa4691c5…93547c",
 "contract_version":"1.1.0","cache_backend":"memory",
 "degraded_reasons":["promptguard_unavailable"]}
Contract smoke PASSED: degraded, honest, and on-contract.
```

Three things at once: the new field is on the wire from a real container, the bump reached
the image, and the revision the built image derives is byte-identical to the one derived
locally — which is the only end-to-end check that the rotation recorded here is the
rotation that ships.

**Already satisfied by US-001, verified rather than re-implemented.** AC1's `/metrics`
clause: the four `storage_*` counters render under the `cache` section (now
`retrieval_app.py:869-878`, the hint's `:669-673` being the pre-US-001 insertion point),
the exact-set assertion in `test_app.py` covers them, and `docs/configuration.md`'s
`cache:` section carries the two-layer distinction (`retrieve.cache_hits` = request
outcomes, `cache.storage_*` = storage operations). AC2's doc half was likewise landed by
US-002; this story added the *field description* — the half that was still missing — and
named `cache_backend` in the three places `configuration.md` had to hedge around it while
the field did not exist yet, including the prod cross-ref, which now reads
`cache_backend == "valkey"` as spec 6's checklist states it.

**Mutation verification — 10 mutants, 10 killed:**

| Mutant | Killed by |
|---|---|
| M1 drop `cache_backend` from `HealthResponse` | `test_contract_schema_matches_golden` |
| M2 hardcode `cache_backend="valkey"` in the handler | `test_health_names_the_backend_it_selected_for_this_start[memory-healthy]` |
| M3 read the environment per request instead of `app.state` | `test_the_lifespan_publishes_the_backend_it_selected` |
| M4 drop the no-cache fallback | `test_health_without_a_cache_still_names_a_backend_and_never_500s` |
| M5 invert `_configured_cache_backend`'s rule | `test_the_backend_name_never_drifts_from_the_storage_actually_built` |
| M6 drop the revision from the fingerprint inputs | `test_a_rotated_sanitizer_revision_changes_the_fingerprint`, `…misses_the_cached_entry[fake_storage/in_memory_storage]` |
| M7 pass a constant revision from `run_retrieve_pipeline` | `test_a_rotated_sanitizer_revision_invalidates_the_cached_entry` |
| M8 revert `CONTRACT_VERSION` to `1.0.0` | `test_contract_schema_matches_golden` (the filename derivation) |
| M9 delete `golden/contract_1_1_0.json` | `test_contract_schema_matches_golden` |
| M10 drop the `cache_connected` description | `test_contract_schema_matches_golden` |

**M5 is the one worth reading.** Inverting `_configured_cache_backend()` survives the
first **three** cases of the matrix (they go through the lifespan, which takes its name
from `_select_cache_storage`, not the helper) — *(supervisor correction 2026-09-13: the
notes first claimed it survived all four and only the drift test caught it; the
verifier's re-run shows case 4 — the no-cache path, which routes through the fallback —
kills it too, so it is double-pinned: drift test + matrix case 4. The design is safer
than first advertised.)* Two functions encode one rule — one for the lifespan, which
needs a storage object, one for `/health`'s fallback, which has nowhere to put one — and
the drift test asserts they agree for both environments.

M7 is the wiring gate: `cache_policy_fingerprint` taking the revision is inert unless
`/retrieve` passes the one this process derived, so the kill is at the route level (three
requests: miss, cached hit as the control, then a re-fetch after the rotation).

**Deliberately not done here:** the compose fragments and the README mode matrix
(US-004's); stamping `RetrievedContent` with `sanitizer_revision` (the model has no such
field — `ExtractedContent` does, and adding one to the retrieve response would be a wire
change this story's minor bump does not cover); and any Poppy-side work, which the spec
correctly says is none — the comparison is major-only and a minor drift logs.

**Gates.** 1338 passed (1327 + 11); `ruff check` / `ruff format --check` /
`pyright --strict` / `actionlint` all zero. One process note: run pyright through
`uv run`, as `CONVENTIONS.md` says — a bare `.venv/bin/pyright` in this tree reports 34
phantom errors from a different environment's stubs.

### US-004 — Example compose fragments + mode docs (2026-09-10, build half)

**This story ships in two halves.** Everything below is the build half: the two
fragments, the CI gate, the committed guards and the README. The AC's *"manual smoke
transcript recorded"* clause is a **human gate** — marked supervised in the epic wrapper
— and its runbook is the last section here, deliberately unexecuted.

**What landed.** `compose/minimal.yml` (Forage + SearXNG, no Valkey) and
`compose/full.yml` (the same, plus a digest-pinned `valkey/valkey:8` under the content
cache). Both are self-contained and both `docker compose config -q` green.

| | `minimal.yml` | `full.yml` |
|---|---|---|
| Services | `forage`, `searxng` | `forage`, `searxng`, `valkey` |
| `VALKEY_URL` | **absent entirely** | `redis://valkey:6379/4`, a literal |
| Published ports | `127.0.0.1:8020:8020` only | same |
| Weights volume | `forage-model-cache` → `/app/model-cache` | same |
| Compose project | `forage-minimal` | `forage-full` |

**Four decisions worth reading.**

1. **`full.yml` repeats `minimal.yml` instead of extending it.** An overlay that only
   adds a service cannot be `config`-validated or started on its own, and a reader
   copying an example should be able to read one file. The duplication is real, so it is
   asserted rather than trusted: `TestTheDuplicationDoesNotDrift` fails if the two
   fragments pin different images, publish differently, or mount the weights
   differently.
2. **The weights volume takes an explicit `name: forage-model-cache`.** Without it
   Compose namespaces per project (`forage-minimal_forage-model-cache`), which would
   both lose docs/weights.md's canonical spelling and make switching modes a ~270 MiB
   re-download. The cost is stated in place: `down -v` from either fragment removes the
   weights both use.
3. **`HF_TOKEN` is a bare-name passthrough, `SEARXNG_SECRET` is `${…:?}`.** The two
   credentials need opposite treatment and compose has a form for each. A bare
   `- HF_TOKEN` passes a value through when set and leaves the variable *genuinely
   unset* when not — measured, `docker compose config` renders `HF_TOKEN: null` — which
   matters because no-token is a supported mode and an empty string is a different
   thing. `SEARXNG_SECRET` gets the required-or-fail form because upstream's own schema
   exits 1 without it: `:?` moves that failure from a container's exit code into a
   Compose message naming the variable, before anything starts.
4. **Neither fragment wires a limiter, and `full.yml` says so where the temptation
   is.** The `SEARXNG_VALKEY_URL` note sits in the `searxng` service of the fragment
   that has a Valkey running three lines below it — because that is where "we have one
   now, wire it up" happens, and the result would be Forage's own client 429'd on its
   first request. Two guards cover it: no `SEARXNG_LIMITER` and no limiter backend, on
   any service of either file.

**`minimal.yml`'s header names the completion criterion**, and a guard asserts that it
does. The spec's last hint asked for the header; the guard is because a file nobody
knows is load-bearing gets edited as if it were not.

**Image pins.** `ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc` is published —
confirmed by enumerating the GHCR package versions at writing.
`ghcr.io/washingbearlabs/forage:0.9.3-rc` is NOT published yet and no registry check can
confirm it: it is pre-registered here and cut warm immediately after this PR merges
(supervisor sequencing — 0.9.1-rc predates contract 1.1.0, and 0.9.2-rc failed the
publish parity gate on a cold cache and was withdrawn). A registry enumeration at
writing returns `[0.9.1-rc, 0.9.2-rc]` for forage; the fragments intentionally cite
neither. Worth knowing: **`docs/searxng.md` cites
`forage-searxng:0.1.0` in three places and that tag has never existed** (the tags pushed
were `searxng-v0.1.0-rc` and `searxng-v0.1.1-rc`, which publish `0.1.0-rc` / `0.1.1-rc`).
Out of this story's scope, left alone, recorded here — it is a copy-paste-and-fail for a
third party, exactly the shape these fragments exist to remove. The guard test refuses
`latest` (which does not exist for either image yet), a bare repository, and the
`sha-<short>` tags a main push produces, and cross-checks the repository names against
`ci.yml`'s own `IMAGE_NAME` / `SEARXNG_IMAGE_NAME`.

**The CI gate is a step in `lint`, not a `compose-validate` job — and that is the
decision, not an implementation detail.** The six contexts registered as required on
`main` are `lint`, `typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke` (read from
the branch protection API, not assumed). A new job would be green, visible and **not
required** — a gate nobody has to pass, and nobody would notice it was not one. Inside
`lint` it is required by construction, costs seconds on a runner that is already checked
out, and sits beside `actionlint`, which is the same kind of check: static validation of
a config file this repository ships for other people to run. `lint` also carries no
`if:`, so it runs on both tag lanes. **Flagged for the supervisor:** if this should
instead be its own context, it needs a branch-protection change, and
`test_the_validation_runs_in_a_required_job` is the test that will go red first.

The step supplies `SEARXNG_SECRET: compose-config-validation-placeholder`. That is not a
dodge — interpolation is part of what `config` validates, so the required variable must
have *a* value or the step would be exercising the error path; nothing is started, so
which value is irrelevant. `test_every_required_variable_is_supplied_to_the_check`
derives the required set from the fragments themselves, so adding a `${…:?}` variable
and forgetting CI is a suite failure naming the variable rather than a red push.

**`tests/test_compose_fragments.py` (new, 56 tests; `TestComposeFragmentValidation` adds
the other 8) parses; it does not run Docker** —
the `tests/test_dockerfile.py` precedent, and the reason is that shelling out to the
Compose CLI would make the whole hermetic suite depend on a daemon. Three layers cover
the fragments and they answer different questions: CI's `config -q` says *Compose accepts
them*, this module says *they say the right things*, and the manual smoke says *they
serve traffic*. Only the middle one runs on every commit and can fail on the line that
moved.

The two guards most worth their weight are the ones tying a fragment to code:
`_DEFAULT_SEARXNG_URL`'s host must be a service name in both files (a service renamed
`forage-searxng` validates fine and then fails its own `/search` round-trip — the
round-2 finding, now mechanical), and `full.yml`'s database index must equal the one
`ContentCache.__init__`'s default carries, read through `inspect.signature`. That
constructor default is US-002's named exemption from "no baked default"; tying the
fragment to it means a deployment moved between the two cannot land on a different
keyspace and silently see an empty cache.

**Mutation verification — 18 mutants, 18 killed**, each by a named test:

| Mutant | Killed by |
|---|---|
| M1 publish `8020:8020` on every interface | `test_every_published_port_binds_to_loopback` |
| M2 rename the service to `forage-searxng` | `test_the_searxng_service_is_named_for_the_code_default` |
| M3 drop the `SEARXNG_SECRET` passthrough | `test_the_secret_is_passed_through` |
| M4 `${SEARXNG_SECRET:-changeme}` instead of `:?` | `test_the_secret_is_required_not_defaulted` |
| M5 wire `SEARXNG_LIMITER: "true"` | `test_no_service_enables_the_limiter` |
| M6 add `VALKEY_URL=${VALKEY_URL}` to `minimal.yml` | `test_no_service_carries_valkey_url` |
| M7 interpolate `full.yml`'s `VALKEY_URL` | `test_valkey_url_is_a_literal_not_an_interpolation` |
| M8 move the database index to `/0` | `test_the_database_index_matches_the_codes_own_default` |
| M9 rename the weights volume | `test_the_volume_is_declared_under_the_documented_name` |
| M10 pull `forage:latest` | `test_every_image_is_pinned` |
| M11 drop the valkey digest | `test_third_party_images_are_digest_pinned` |
| M12 delete the CI validation step | `test_a_step_validates_the_fragments` |
| M13 move it into a `compose-validate` job | `test_the_validation_runs_in_a_required_job` |
| M14 drop the placeholder the required variable needs | `test_every_required_variable_is_supplied_to_the_check` |
| M15 point `VALKEY_URL` at a host not in the file | `test_valkey_url_names_the_service_in_this_file` |
| M16 let Compose namespace the weights volume | `test_the_volume_is_declared_under_the_documented_name` |
| M17 mount the weights off `HF_HOME` | `test_forage_mounts_it_at_the_images_hf_home` |
| M18 bump one fragment's pin and not the other | `test_the_shared_services_run_the_same_image` |

**README.** The quickstart is now `docker compose -f minimal.yml up -d` with a two-line
`.env`, and the `docker run` form is kept below it as the direct-image alternative. A
**mode matrix** compares memory and Valkey on selection, the `cache_backend` wire value,
both `/health` outcomes, restart survival, replica sharing and bounds, and links each
row's worked example. The posture line is repeated over the fragments (loopback only,
nothing else published), and the two easy-to-get-wrong facts are stated under the table:
only a *fully unset* `VALKEY_URL` means memory mode, and the cache serves `/retrieve`
only. One stale line was corrected in passing — Status still listed the optional
in-memory cache as "still to come" after US-001–003 shipped it.

**Gates.** 1402 passed (1338 + 64); `ruff check` / `ruff format --check` /
`pyright --strict` / `actionlint` all zero; `docker compose config -q` green on both
fragments locally. `sanitizer_revision` verified **unrotated** before and after:
`fa4691c57449c52fe367208bdeeb650cbe474d93485c3b90cc8989b5e593547c` — nothing this story
touches is a `_REVISION_SOURCES` member.

---

> **What the guards do NOT prove:** every fragment test in
> `tests/test_compose_fragments.py` is parse-only — tag *shape*, service names, bindings —
> with no registry call anywhere. A green suite is not a pullable pin; the pull happens
> here, in this smoke, which is part of why it is a gate.

#### Manual smoke — executed 2026-09-11 (human gate)

> The AC's *"manual smoke transcript recorded"* clause. **Not executed by the
> implementer.** The supervisor runs this with the owner and pastes the transcript
> under "Recorded transcript" below. Mirrors the US-003-of-model-bootstrap pattern.

**Before you start.** SEQUENCING: this PR must be MERGED and the `v0.9.3-rc` tag cut and
its publish lane green BEFORE this smoke — the fragments pin `forage:0.9.3-rc`, which does
not exist until then; running early fails at the pull with `manifest unknown`. Then: you
need a Hugging Face token with `meta-llama/Llama-Prompt-Guard-2-22M`
access approved. Nothing else: the repository and both packages have been PUBLIC since the
2026-09-10 flip (supervisor correction — the first draft of this runbook said a GHCR
login was needed; the verifier's anonymous pull disproved it). The completion criterion
*"a third party can `docker compose up` with only `HF_TOKEN`"* is literally true today;
record that the pull needed no login when you run it.

```bash
cd ~/Documents/GitHub/Forage/compose

# 1. No registry login — the images are public; an anonymous pull is part of
#    what this smoke proves.

# 2. The only configuration a reader supplies. HF_TOKEN is the real one;
#    SEARXNG_SECRET is any long random string.
{
  echo "HF_TOKEN=hf_REPLACE_ME"
  echo "SEARXNG_SECRET=$(head -c 32 /dev/urandom | base64)"
} > .env
chmod 600 .env

# 3. Bring up the two-container deployment. RECORD: does `up -d` succeed with
#    no further edits to the fragment? That question is the AC.
docker compose -f minimal.yml up -d
docker compose -f minimal.yml ps
```

```bash
# 4. Watch the weights converge (~270 MiB; /health stays 200 throughout).
#    RECORD: model.fetch_in_progress true at least once, then false.
curl -s localhost:8020/metrics | jq '.model'

# 5. Wait for the flip, then RECORD the whole body.
until curl -sf localhost:8020/health | jq -e '.promptguard_loaded == true' >/dev/null
do sleep 10; done
curl -s localhost:8020/health | jq
```

Expected at step 5 — this is the memory-mode goal in one body:

```json
{"status":"healthy","promptguard_loaded":true,"cache_connected":true,
 "capabilities":{"search_sanitization":1},
 "sanitizer_revision":"fa4691c5…93547c","contract_version":"1.1.0",
 "cache_backend":"memory","degraded_reasons":[]}
```

```bash
# 6. /retrieve round-trip, twice. RECORD both `cache_hit` values: the first
#    must be false and the second true — that is the in-memory cache serving a
#    repeat, which is Goal 1 of this spec observed from outside.
for i in 1 2; do
  curl -s -X POST localhost:8020/retrieve \
    -H 'content-type: application/json' \
    -d '{"url":"https://example.com","extract_mode":"summary"}' \
  | jq '{attempt:'"$i"', source_url, cache_hit, trust_tier: .trust.tier?}'
done

# 7. /search round-trip through the companion image. RECORD the result count
#    and that no 429 appears — a non-empty list is the limiter decision
#    (absent, deliberately) being the right one.
curl -s -X POST localhost:8020/search \
  -H 'content-type: application/json' \
  -d '{"query":"raccoon","num_results":3}' \
| jq '{results: (.results | length), first: .results[0].title?}'

# 8. Storage counters. RECORD cache.storage_hits ≥ 1 and the retrieve section
#    beside it — the two layers US-003 documented, seen together for once.
curl -s localhost:8020/metrics | jq '{cache, retrieve}'
```

```bash
# 9. Switch modes. RECORD: cache_backend flips to "valkey", and the weights do
#    NOT re-download (the shared `forage-model-cache` volume) — so
#    promptguard_loaded should reach true in seconds, not minutes.
docker compose -f minimal.yml down
docker compose -f full.yml up -d
sleep 20 && curl -s localhost:8020/health | jq '{status, cache_backend, cache_connected, promptguard_loaded}'

# 10. Tear down WITHOUT -v, so the verified weight set survives for next time.
docker compose -f full.yml down
rm -f .env
```

**What the transcript must contain**, at minimum: whether `up -d` worked unedited, the
step-5 `/health` body, both `cache_hit` values from step 6, the step-7 result count, the
step-8 `cache.storage_*` counters, and the step-9 `cache_backend: "valkey"` with the
observed warm-start time. Anything that needed a manual fix is the most valuable line in
it — the fragment is wrong, not the run.

**Recorded transcript** (2026-09-11, supervisor + owner; fragments at `ce6351a`,
image `forage:0.9.3-rc`, anonymous pull — no registry login performed):

- **Steps 1–3:** `.env` written (`HF_TOKEN` + random `SEARXNG_SECRET`, mode 600);
  `docker compose -f minimal.yml up -d` succeeded **with no edits to the fragment**
  (the AC's question); both containers Up; images pulled anonymously.
- **Step 4:** `/metrics.model` mid-fetch: `fetch_in_progress: true`, all failure
  counters 0.
- **Step 5:** `/health` reached the memory-mode goal body exactly:
  `status: "healthy"`, `promptguard_loaded: true`, `cache_connected: true`,
  `capabilities: {"search_sanitization": 1}`, `sanitizer_revision: fa4691c5…93547c`,
  `contract_version: "1.1.0"`, `cache_backend: "memory"`, `degraded_reasons: []`.
- **Step 6:** `/retrieve` ×2 on example.com: `cache_hit` **false then true** — the
  in-memory cache serving a repeat, observed from outside.
- **Step 7:** `/search` "raccoon": **3 results**, first "Raccoon - Wikipedia",
  **no 429** — the shipped limiter-off decision working live.
- **Step 8:** `cache.storage_hits: 1`, `storage_misses: 1`, evictions/oversize 0;
  `retrieve.cache_hits: 1`, `cache_misses: 1`, `promptguard_state.scanned: 2` —
  both counter layers coherent side by side.
- **Step 9:** mode switch to `full.yml`: `cache_backend: "valkey"`,
  `cache_connected: true`, `status: "healthy"`, `promptguard_loaded: true` observed
  at **T+24 s** from `up -d` — no re-download (shared `forage-model-cache` volume).
- **Step 10:** teardown without `-v`; volume preserved; `.env` and the owner's token
  file deleted.

Nothing needed a manual fix. The completion criterion — a third party with only
`HF_TOKEN` (+ `SEARXNG_SECRET`) reaches a working `/retrieve` + `/search` — held
end to end, anonymously.

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
