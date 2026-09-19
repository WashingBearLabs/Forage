<!-- Template Version: 2.5.0 -->
---
feature: hardening-cache-integrity
status: active
session_ready: true
depends_on: [hardening-hostname-and-config, hardening-retrieve-parity, hardening-search-sanitization]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 4
epic_final: false
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Cache Integrity — Signed Valkey Values, Loud When Unsigned

> **Spec 4 of `epic-forage-hardening`.** Give the content cache an integrity check: with an
> operator-supplied `FORAGE_CACHE_HMAC_KEY`, every value written to the Valkey backend is
> HMAC-signed **together with the cache key it lives under**, every value read is bounded and
> verified before it is parsed, and a planted, relocated, oversized or unparseable entry becomes a
> miss, not model input. Without a key on an external Valkey, `/health` says so
> (`cache_unauthenticated`) instead of pretending the cache is trustworthy. The in-memory backend
> needs no key and reports nothing.
> Context: WA-B punch list ("Valkey content-cache is poisonable from any poppy-net container";
> "cache hit re-enters the model without re-running any sanitization stage"); owner decision 2 and
> rulings 5, 6, R21, R32, R33 are binding here; spec 2 US-003 (corrupt entry is a miss) and spec 1
> US-004 (the 1.3.0 window) are assumed landed.

## Overview

A cache hit on `/retrieve` is served without re-running any sanitization stage
(`pipeline/orchestrator.py:304`, `cache.py:757`), and the key for an entry is computable from
public code (`cache_key` `cache.py:106`, `cache_policy_fingerprint` `:120`; `sanitizer_revision` is
on `/health`). Anything that can `SET` on the Valkey the operator wired through `VALKEY_URL` can
therefore plant fabricated "already sanitized" content under a URL the consumer will ask for. The
WA-B review named the committed `poppy_dev` password and the shared network as the enablers; those
are the consumer's to fix. What Forage can do on its own side is refuse to trust bytes it cannot
prove it wrote **for this key** — that is owner decision 2 as sharpened by ruling R21.

Two decisions carry the spec. First, what the MAC covers. A MAC over the payload alone proves
"Forage wrote these bytes", not "for this URL, mode and policy": the same adversary can `GET`,
so they ask the unauthenticated `/retrieve` for a page they control (a third-party domain lands in
`STANDARD`, which is cacheable — `_NO_CACHE_TIERS` is only `UNTRUSTED`/`BLOCKED`, `cache.py:62`),
let Forage sign it, and copy the envelope verbatim under the computed key for `docs.python.org`;
`ContentCache.get` never compares `source_url` to the requested URL. So the MAC input is the
version tag, the cache key and the payload (R21), and a relocated envelope is `bad_mac`.

Second, what happens when no key is configured. "Sign with a random per-process key" would make
every restart a cold cache; "sign only when a key is set, and stay quiet" would let a keyless
external Valkey look exactly like a keyed one. Decision 2 picks *optional key, loud when absent*:
the feature is opt-in, but a Valkey-backed cache without a key is a `degraded` state with a named
reason, the same way an absent classifier is. The in-memory backend (`cache.py:546
InMemoryStorage`, selected when `VALKEY_URL` is fully unset) is process-private and needs neither
the key nor the reason — the stock `compose/minimal.yml` deployment is untouched. A Valkey-backed
`contract_smoke.py --expect-status healthy` run **is** affected: without the key it never reaches
`healthy`, so its docstring says so and spec 8's `v1.2.0` smoke runs the keyed configuration
(ruling R30).

Mechanically the envelope lives in `ContentCache` (`cache.py:~702`): `put` writes
`v1.<hex-mac>.<json>`; `get` bounds the read, splits, verifies with `hmac.compare_digest`, and only
then hands the JSON to `RetrievedContent.model_validate_json` (`cache.py:784`, guarded since spec 2
US-003). Every rejection deletes, counts, and logs a closed-vocabulary reason — the key itself is
never logged, never on `/health`, never on `/metrics`, exactly as `FORAGE_BRAVE_API_KEY` is
handled (`retrieval_app.py:196 _resolve_brave_key`, `brave.py:81 brave_key_present`).

## Goals

- With a key configured, a Valkey value planted through `tests/fakes.py::FakeStorage.entries`
  (unsigned; signed with another key; one flipped payload byte; a valid envelope copied from
  another cache key; a value over `cache.max_value_bytes`) is never returned by `ContentCache.get`:
  the key is deleted and `CacheMetrics.integrity_rejects` advances by one per case.
- A round trip through `put` then `get` under the same key and cache key returns the identical
  `RetrievedContent` and advances no integrity counter.
- With `VALKEY_URL` set and no key, `/health` lists `cache_unauthenticated` in `degraded_reasons`;
  with the key set it does not and `capabilities.cache_hmac_key == 1`; with `VALKEY_URL` unset
  neither appears — three real-lifespan starts, three assertions.
- The key value appears zero times in `/health`, `/metrics`, any log record, a `/retrieve` error
  body produced while the key is configured, `repr(app.state.cache)`, and any file under
  `tests/fixtures/`.
- An operator can turn integrity on with a two-line change to `compose/.env`, and every surface
  that names the health, metrics or log vocabulary names the new reason, capability, counter and
  markers — with no stale "two keys" / "two reasons" sentence left anywhere.

## User Stories

### US-001: Signed, key-bound, size-bounded values on the Valkey backend

**Priority:** P1

**Description:** As an operator running Forage against a shared Valkey, I want cached content to be
signed with a secret only Forage holds, bound to the key it is stored under, and bounded in size
before it is verified, so that a planted, relocated or oversized entry can never be served to the
model as sanitized content.

**Independent Test:** With a real `ContentCache` over `tests/fakes.py::FakeStorage` constructed with
`hmac_key=b"x" * 32`: `put` stores a value beginning with `v1.` whose MAC equals
`hmac.new(key, b"v1\0" + cache_key.encode() + b"\0" + payload, "sha256").hexdigest()`; `get`
returns the content. Five planted values — bare JSON; `v1.<mac under another key>.<json>`; a valid
envelope with one payload byte flipped; a valid envelope produced by `put` for a different URL
written under this cache key; a value of `cache.max_value_bytes + 1` bytes — each produce
`get → None`, one `delete` call on the fake, and `integrity_rejects` incremented once, with reasons
`unsigned`, `bad_mac`, `bad_mac`, `bad_mac`, `oversize`; with `hmac_key=None` the stored value is
bare JSON as today and every existing `tests/test_cache.py` test passes unchanged.

**Implementation Hints:**
- `ContentCache.__init__` (`cache.py:~713`) gains `hmac_key: bytes | None = None`; store it privately
  (`self._hmac_key`) and add a `__repr__` that omits it (mirror how `BraveApiProvider` hides its
  key — read `pipeline/search_providers/brave.py` for the shape). Counters go through
  `self._metrics` (`cache.py:720`); there is no `metrics` property.
- `put` (`cache.py:821`): when a key is set, `payload = content.model_dump_json()` once; `mac =
  hmac.new(self._hmac_key, b"v1\0" + key.encode() + b"\0" + payload.encode(), hashlib.sha256)
  .hexdigest()`; store `f"v1.{mac}.{payload}"`. `key` is the `cache_key(...)` already computed at
  the top of `put`/`get`, so the binding costs one concatenation. The `CacheStorage` protocol
  (`cache.py:344-354`, `set(key, value: str, *, ttl_seconds)`, `get -> bytes | None`) is unchanged
  for the envelope; `InMemoryStorage` (`:546`) needs no edit and the `_ParityHarness` /
  `TestPolicyParityAcrossStorages` harness (`tests/test_cache.py:888`, `:941`) keeps running both.
- Size bound **before** any MAC work (R21): `ValkeyStorage.get` (`cache.py:496`) gains
  `max_value_bytes` from `CacheSettings` (new `config.yaml` key `cache.max_value_bytes`, default
  2 MiB, range 64 KiB – 16 MiB, read by `cache_settings_from_config` `:~267` with `_bounded_int`;
  registered in spec 3 US-003's `KNOWN_CONFIG_KEYS` and documented in `docs/configuration.md`'s
  cache table): `STRLEN` first; over the bound → `DEL`, `self._metrics.integrity_rejects += 1`,
  WARNING with reason `oversize`, return `None`. `InMemoryStorage` is already write-bounded by
  `max_bytes` (`:648`). The parity harness's memory side never produces an oversize value; say so
  in the test.
- `get` (`cache.py:757`): after `raw = await self._storage.get(key)` and before
  `model_validate_json`, call `_unwrap(raw, key) -> bytes | None`: with a key, require the `v1.`
  prefix, split on the first two dots, recompute over `b"v1\0" + key.encode() + b"\0" + payload`,
  `hmac.compare_digest`; with no key, a value that starts with `v1.` is rejected too (a keyed
  writer's value read by a keyless process is a miss, never a parse of attacker-shaped text). On any
  rejection: `await self._storage.delete(key)`, `self._metrics.integrity_rejects += 1`,
  `logger.warning("cache_integrity_reject — reason=%s", reason)` — tokens in the message, never
  `extra=`, never the key, never the raw value.
- Reasons are a module-level `CACHE_INTEGRITY_REASONS = frozenset({"unsigned", "bad_mac",
  "malformed_envelope", "oversize"})` beside `_closed_vocabulary_reason` (`cache.py:297`) — that
  function maps *exceptions* to reasons and is unchanged. There are no closed-vocabulary
  assertions in `tests/test_cache.py` today; write a **new** `caplog` test in `TestSignedValues`
  asserting the marker and the token appear and the key / raw value / URL do not
  (`kit_tools/arch/patterns/LOGGING.md`, "Patterns to Follow").
- `/metrics` (R21 — mirrored, not conditional): `CacheMetrics` (`cache.py:~187`) gains
  `integrity_rejects: int = 0`; `CacheMetricsResponse` (`retrieval_app.py:568`, `extra="forbid"`)
  gains the matching `Field(description=...)` and its docstring gains a sentence (it currently
  claims the section holds only storage-operation counters); the handler's `"cache"` dict
  (`retrieval_app.py:~1545`) gains `"integrity_rejects": cache_metrics.integrity_rejects`.
  `tests/test_contract_metrics.py::test_dataclass_counters_and_their_models_carry_the_same_fields`
  (`:305`) and `::test_metrics_schema_is_fully_rendered` enforce it. Contract window (ruling 5): one
  `* ``1.3.0`` — …` docstring line (read `pipeline/contract.py:26-68` for the bullet shape),
  `uv run python -m scripts.export_contract`, re-create `tests/golden/contract_1_3_0.json`,
  `--check` green. US-002 re-runs the export after its own line; it must keep this one.
- Key material: the stripped environment value is used as UTF-8 bytes, **never base64-decoded**;
  the documented recipe (`head -c 32 /dev/urandom | base64`, ~44 characters) therefore yields a
  44-byte key. US-002 enforces the 32-byte floor at boot.
- Rotation (R32): `cache.py` and `retrieval_app.py` are not hashed; `pipeline/contract.py` **is**,
  and this story appends a docstring line to it, so this story rotates — measure by
  revert-and-reproduce and record on the five-file protocol (`docs/bootstrap-notes.md`,
  `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md`,
  `kit_tools/arch/CODE_ARCH.md`, the practice `kit_tools/EXECUTION_LOG.md` records).
- Tests: new `TestSignedValues` in `tests/test_cache.py` beside `TestContentCacheGetPut` (`:294`),
  driving `FakeStorage` directly and planting values through `FakeStorage.entries`
  (`tests/fakes.py:196-268`, `(payload_bytes, expiry)` against the fake's clock); parametrise the
  five rejection shapes; assert `storage.delete_calls` advances (spec 2 US-003 uses the same
  counters for the corrupt-entry case).

**Acceptance Criteria:**
- [ ] `ContentCache(hmac_key=...)` accepts `bytes | None`, defaults to `None`, and with `None` the
      stored value and every existing `tests/test_cache.py` test are unchanged.
- [ ] With a key, `put` stores `v1.<64 hex chars>.<json>` and the MAC verifies as HMAC-SHA256 over
      `b"v1\0" + cache_key + b"\0" + payload`; `get` returns a `RetrievedContent` equal to what was
      put; a test asserts `model_dump_json()` is called once per `put`.
- [ ] The five rejection shapes (unsigned; another key; flipped byte; relocated envelope;
      oversize) each yield `get → None`, exactly one `delete` on the storage, and
      `integrity_rejects` advancing by one, with the reasons `unsigned` / `bad_mac` / `bad_mac` /
      `bad_mac` / `oversize`; a malformed prefix or MAC length yields `malformed_envelope`; a keyless
      cache reading a `v1.` envelope rejects it.
- [ ] `ValkeyStorage.get` checks `STRLEN` before `GET` and never reads a value over
      `cache.max_value_bytes`; the key is registered, bounded, and documented in
      `docs/configuration.md`'s cache table.
- [ ] Every rejection logs exactly one WARNING whose `getMessage()` contains
      `cache_integrity_reject` and one member of `CACHE_INTEGRITY_REASONS`; the key bytes, the raw
      value and the Valkey URL appear in no record (`caplog.text`).
- [ ] `repr(cache)` and `str(cache)` do not contain the key bytes.
- [ ] `CacheMetrics.integrity_rejects`, the `CacheMetricsResponse` field and the `/metrics` handler
      dict entry exist; the field-parity test passes; the `/metrics` body carries the key.
- [ ] 1.3.0 window: docstring line appended, contract regenerated, `tests/golden/contract_1_3_0.json`
      re-created, `uv run python -m scripts.export_contract --check` green,
      `tests/golden/contract_1_2_0.json` unchanged.
- [ ] The `sanitizer_revision` rotation (`pipeline/contract.py`) is measured by revert-and-reproduce
      and recorded on the five-file protocol.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: The key at boot, loud when absent, and the on-switch in the full compose fragment

**Priority:** P1

**Description:** As an operator, I want Forage to read `FORAGE_CACHE_HMAC_KEY` once at start,
refuse a key that is too short or malformed, sign with it when present, tell me on `/health` when my
external Valkey is running unsigned, and have the documented full-stack compose fragment able to
carry the key — so the state of the cache is never a guess and the feature has a supported switch.

**Independent Test:** Three real-lifespan starts through `tests/test_app.py::_started_with_valkey_url`
(`:1270`, keeping the real `ContentCache` and doubling only the Valkey socket via `_valkey_double`
`:1248` — **never** `_running_app`, which patches `ContentCache` away): (a) `VALKEY_URL` set, key
unset → `degraded_reasons` contains `cache_unauthenticated`, `status == "degraded"`, no
`cache_hmac_key` capability, one WARNING `cache_hmac_key_missing`; (b) `VALKEY_URL` set, key set to
a 44-character sentinel → no `cache_unauthenticated`, `capabilities["cache_hmac_key"] == 1`, and
`app.state.cache` was constructed with the key (`cache._hmac_key is not None`); (c) `VALKEY_URL`
unset → neither the reason nor the capability, `app.state.cache._hmac_key is None`. A fourth start
with a 20-byte key refuses boot with a typed error naming the variable. In every case the sentinel
appears zero times in `/health`, `/metrics`, a `/retrieve` error body raised while the key is set,
`caplog.text`, and `repr(app.state.cache)`.

**Implementation Hints:**
- One read site: `CACHE_HMAC_KEY_ENV_VAR = "FORAGE_CACHE_HMAC_KEY"` and
  `_resolve_cache_hmac_key() -> bytes | None` in `retrieval_app.py` on `_resolve_brave_key`'s
  pattern (`:196`): `os.environ.get(CACHE_HMAC_KEY_ENV_VAR)`; strip `" \t\n"` (the
  `BRAVE_KEY_STRIP_CHARS` set, `brave.py:78`, which deliberately keeps `\r`); blank after strip →
  absent silently (the `FORAGE_CACHE_HMAC_KEY=` compose-renders-unset shape). The rules are stated
  here, not inherited: printable ASCII, no interior whitespace or control characters (a shape rule
  carried over from `brave_key_present` `:81-112`, whose rationale is header transport — say so in
  the docstring), and **at least 32 bytes as UTF-8** (R21: anyone who can plant a value can read a
  signed one and attack the key offline; the recipe yields 44). A too-short or malformed value
  **refuses boot** with a typed `CacheConfigurationError`-shaped error naming the variable only,
  logged as `cache_hmac_key_too_short` / `cache_hmac_key_invalid`; never the value.
- Wire it where the lifespan already holds the answer: `storage, backend = _select_cache_storage(...)`
  (`retrieval_app.py:1284`) → pass `hmac_key=` into `ContentCache` only when `backend == "valkey"`
  (gate on that local, not on a second `_configured_cache_backend()` call — its docstring at
  `:228-238` says it exists for the lifespan-less `/health` caller). Publish
  `app.state.cache_signing_active: bool = key resolved AND backend is valkey` beside
  `app.state.cache_backend` (`:1294`); read it in `/health` through
  `_resolved_cache_signing_active(state) -> bool` with a `getattr(..., False)` default and a
  module-scope `app.state` initialisation in the lifespan-less block (`:~1396-1402`), on
  `_resolved_search_key_capabilities`'s pattern (`:313`).
- `/health`: `DEGRADED_CACHE_UNAUTHENTICATED: DegradedReason = "cache_unauthenticated"` in
  `pipeline/contract.py`'s `DegradedReason` literal (`:82-100`, a response-validation gate) appended
  after `cache_unavailable` when the backend is Valkey and signing is not active;
  `tests/test_contract_errors.py::test_degraded_reasons_derive_from_one_source` (`:282`) is an
  exact set and gains the member in the same edit. `CAPABILITY_CACHE_HMAC_KEY = "cache_hmac_key"`
  beside `CAPABILITY_BRAVE_API_KEY` (`:351`), present as `1` iff signing is active. The
  `capabilities` Field description (`retrieval_app.py:374-390`, "Two keys are defined in contract
  1.2.0") and the block comment at `:341-351` are part of the exported document — updating them is
  the contract change; `HealthResponse`'s constants list follows.
- Contract window (ruling 5): one docstring line for the new `DegradedReason` member and the
  capability, `uv run python -m scripts.export_contract`, re-create `tests/golden/contract_1_3_0.json`
  keeping US-001's line, `--check` green; `pipeline/contract.py` rotates — measure and record on the
  five-file protocol.
- Boot WARNING when Valkey is configured and no key: `cache_hmac_key_missing — %s is unset; cached
  content is served unsigned (/health reports cache_unauthenticated)` naming the variable.
- The on-switch (moved here from the docs story): `compose/full.yml` `environment:` gains
  `- FORAGE_CACHE_HMAC_KEY` as a bare name with a comment on the `FORAGE_BRAVE_API_KEY` pattern
  (credential → `compose/.env`, never inline) and a note that a healthy status now needs it;
  `compose/minimal.yml` is Valkey-free (`TestMinimalIsGenuinelyValkeyFree`,
  `tests/test_compose_fragments.py:553`) and must not carry it; extend
  `TestSearchProviderPassthrough`'s idiom (`:533`) for `full` only.
- `contract_smoke.py`'s healthy-mode docstring (`:44`, "and a reachable cache when `VALKEY_URL` is
  set") names `cache_unauthenticated` as a second reason a Valkey-backed container will not reach
  `healthy`; spec 8 US-003 runs the keyed configuration (R30).
- Hermeticity: add `FORAGE_CACHE_HMAC_KEY` to `tests/conftest.py::_CLEARED_ENV_VARS` and update the
  exact-set assertion in `tests/test_hermeticity.py::test_the_cleared_environment_is_the_expected_exact_set`
  (`:129-138`); the canary at `:141` then covers it.
- CI's secret grep (the Brave precedent's CI half): add `FORAGE_CACHE_HMAC_KEY` to
  `tests/test_ci_workflow.py::_REQUIRED_GREP_PATTERNS` (`:1072`) and to both copies in
  `.github/workflows/ci.yml` (the `secret-grep` heredoc and the publish config grep at `:998`);
  `kit_tools/arch/SECURITY.md:233`'s "three patterns" prose becomes four (US-003 owns the prose).
- Key-never-leaks, on `tests/test_brave_provider.py::TestKeyNeverLeaks` (`:1478`): drive a signed
  put, a rejected get, a Valkey connect failure, a `/retrieve` request that fails while the key is
  configured (the cache-unavailable-and-keyed shape), and a `/health` + `/metrics` read with the
  sentinel; assert it is absent from `caplog.text` (all loggers), every body, and
  `repr(app.state.cache)`. Fixture-tree guard: a **new** walk in `tests/test_cache.py` asserting the
  literal `FORAGE_CACHE_HMAC_KEY` and the sentinel value appear in no file under `tests/fixtures/`
  (do not touch `tests/test_brave_provider.py:80 _AUTH_HEADER_NAMES` — that is a header-name tuple
  scoped to Brave fixtures).

**Acceptance Criteria:**
- [ ] `retrieval_app.py` contains exactly one `os.environ.get(CACHE_HMAC_KEY_ENV_VAR)` call, inside
      `_resolve_cache_hmac_key()`, and the lifespan calls it once (`grep -c
      "_resolve_cache_hmac_key()" retrieval_app.py` returns 2); blank → absent silently; interior
      whitespace / control characters or fewer than 32 UTF-8 bytes → boot refused with a typed
      error whose message names the variable and never the value (`cache_hmac_key_too_short` /
      `cache_hmac_key_invalid`).
- [ ] `cache_unauthenticated` is a member of `pipeline/contract.py`'s `DegradedReason`, the
      exact-set test in `tests/test_contract_errors.py` includes it, and it appears in
      `degraded_reasons` iff the backend is Valkey and signing is not active; `/health` still
      returns HTTP 200 in that state.
- [ ] `capabilities["cache_hmac_key"] == 1` iff a usable key was resolved **and** the backend is
      Valkey; the key is absent from the map otherwise; the in-memory backend produces neither the
      reason nor the capability; `/health` on a lifespan-less transport does not raise.
- [ ] The `capabilities` Field description and the `:341-351` block comment name three keys;
      `grep -n "Two keys are defined" retrieval_app.py` returns nothing; `cache_hmac_key` appears in
      `contract/openapi.yaml` after export.
- [ ] Sentinel test: the key value appears zero times in `/health`, `/metrics`, a `/retrieve` error
      body raised while the key is configured, every log record and `repr(app.state.cache)`; the
      fixture-tree walk finds neither the variable name nor the sentinel under `tests/fixtures/`.
- [ ] `_CLEARED_ENV_VARS` contains `FORAGE_CACHE_HMAC_KEY` and the exact-set test is updated;
      `_REQUIRED_GREP_PATTERNS` and both `ci.yml` grep copies carry the name.
- [ ] `compose/full.yml` passes `FORAGE_CACHE_HMAC_KEY` through as a bare name with the comment;
      `compose/minimal.yml` does not carry it; `tests/test_compose_fragments.py` asserts both.
- [ ] `contract_smoke.py`'s healthy-mode docstring names `cache_unauthenticated`.
- [ ] 1.3.0 window: docstring line appended, contract regenerated, `tests/golden/contract_1_3_0.json`
      re-created with US-001's line intact, `--check` green, `contract_1_2_0.json` unchanged.
- [ ] The `sanitizer_revision` rotation (`pipeline/contract.py`) is measured by revert-and-reproduce
      and recorded on the five-file protocol.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Operator recipe and the documentation fan-out

**Priority:** P2

**Description:** As an operator reading the reference, I want one place that says how to generate
the key, where to put it, what `/health` shows without it, what a rising `integrity_rejects` means,
and how to rotate safely — and no page left claiming there are two capability keys or two degraded
reasons.

**Independent Test:** The `### Credential handling for FORAGE_CACHE_HMAC_KEY` subsection of
`docs/configuration.md` contains the generation command, the UTF-8-never-decoded rule, the
32-byte floor, the `/health` behaviour without the key, and the rotation consequence;
`grep -rn -iE "two keys|the two reasons|exactly two values|exactly one of .connect_failed|three
patterns" docs kit_tools README.md` returns nothing stale; the doc-scanning tests pass.

**Implementation Hints:**
- `docs/configuration.md`: a row in `### Runtime` (`| Variable | Default | Purpose |`) and the new
  credential subsection modelled on the Brave one: generate with `head -c 32 /dev/urandom |
  base64` into `compose/.env`, never on a command line, never printed; the value is used as UTF-8
  bytes and never base64-decoded; at least 32 bytes or the service refuses to start; what
  `/health` shows without it; rotation: **stop every replica, change the key, start** — two
  replicas over one Valkey with different keys delete each other's entries for as long as both run,
  and a mixed keyed/keyless fleet does the same. Also `:467-468` (cache metrics rows gain
  `integrity_rejects`), `:539` ("two keys" / "two reasons"), and the cache table row for
  `cache.max_value_bytes` if US-001 has not already added it.
- `kit_tools/docs/ENV_REFERENCE.md` `### Runtime service` table (seven columns) gains the row with
  `Secret: **yes**` and read site `retrieval_app._resolve_cache_hmac_key(), start`.
- `kit_tools/docs/MONITORING.md`: the `capabilities` row (`:63`), the `degraded_reasons` row (`:68`)
  and "The two reasons are the complete set" (`:70`), the body-shape block (`:81`), the runbook item
  (`:99`), the cache counter table (`:170-177`) with a row for `cache.integrity_rejects`, the
  `### Startup lines you may see` table (`:229`) with `cache_hmac_key_missing`, and `### Closed
  vocabularies` → `cache.py` (`:242`, "exactly one of `connect_failed`, …") extended with the
  `cache_integrity_reject` reasons. Incident guidance: a one-time burst bounded by the working set
  is expected when the key is first set or rotated; a sustained non-zero `integrity_rejects` rate
  with a key set and no rotation in flight means something other than Forage is writing to the
  Valkey — treat it as a security event (rotate the key, audit Valkey ACL and network placement),
  not a cache-health blip. Wording for `cache_unauthenticated`: cached `/retrieve` content cannot
  be proven to be Forage's own and is served without re-sanitization; on a shared Valkey treat it
  as an open cache-poisoning path until a key is set (not "a configuration signal").
- `kit_tools/docs/TROUBLESHOOTING.md`: `:56` ("Exactly two values exist") and `:60`; a narrative
  `### cache_unauthenticated: Valkey configured without a signing key` section beside the
  `cache_unavailable` one (`:345`) — this file has no log-marker table.
- `kit_tools/docs/API_GUIDE.md:105` and `:109` (the "two keys" capability sentence).
- `kit_tools/arch/SECURITY.md`: a `FORAGE_CACHE_HMAC_KEY` row in the Secrets inventory table
  (`:216-224`); follow and cite the four steps of `### Adding a new secret` (`:248-253`); `:233`'s
  "three patterns" becomes four; `:291`'s `degraded_reasons` enumeration gains the third; a new
  cache-poisoning entry (there is none today — `grep -i poison` is empty) stating: with a key,
  integrity and authenticity of cached values are assured; availability (an attacker can still
  delete or overwrite, forcing misses and outbound fetches) and confidentiality are not and stay
  the consumer's controls; without the key the poisoning path stands and `/health` says so; and
  that the posture is advertised on an unauthenticated `/health` because honest health outranks
  obscurity and the same fact is observable by anyone who can write to the cache.
- `kit_tools/arch/patterns/LOGGING.md`: the `### cache.py` table and its "Exactly three strings"
  sentence (`:80` area) gain the integrity reasons; the `cache` Logger Inventory row; the two boot
  markers.
- `kit_tools/docs/DEPLOYMENT.md` and `README.md`'s configuration section mention the variable in
  the same sentence that mentions `VALKEY_URL`.
- Doc-only story: no code, no test criteria beyond the doc-scanning suite; nothing rotates.

**Acceptance Criteria:**
- [ ] `docs/configuration.md` carries the runtime row and the credential subsection (generation
      recipe, UTF-8-never-decoded, 32-byte floor, `/health` behaviour, stop-all-replicas rotation);
      `kit_tools/docs/ENV_REFERENCE.md` carries the seven-column row.
- [ ] `kit_tools/docs/MONITORING.md` names three capability keys, three degraded reasons (row, body
      shape, runbook), the `cache.integrity_rejects` counter row with the burst-versus-sustained
      guidance, the startup marker, and the extended `cache.py` closed vocabulary; the
      `cache_unauthenticated` wording states the consequence.
- [ ] `kit_tools/docs/TROUBLESHOOTING.md` and `kit_tools/docs/API_GUIDE.md` no longer say two values /
      two keys; TROUBLESHOOTING has the `### cache_unauthenticated` section.
- [ ] `kit_tools/arch/SECURITY.md` has the Secrets-inventory row, the four-pattern sentence, the
      three-reason enumeration, and the cache-poisoning entry with both residuals and the
      disclosure trade-off; `kit_tools/arch/patterns/LOGGING.md` names the four reasons and the
      two boot markers.
- [ ] `grep -rn -iE "two keys|the two reasons|exactly two values|three patterns" docs kit_tools
      README.md` returns nothing that describes `/health` capabilities, `degraded_reasons` or the
      secret-grep set; `grep -rn FORAGE_CACHE_HMAC_KEY README.md kit_tools/docs/DEPLOYMENT.md`
      each return at least one line.
- [ ] Full test suite passes (`uv run pytest`).

## Edge Cases

- Key configured, storage holds a legacy bare-JSON value → `unsigned` reject, delete, miss; the
  next `put` re-signs. Key rotated (single process) → every old entry is `bad_mac` once, then gone.
  US-001.
- Two replicas over one Valkey with different keys (a rolling rotation), or a mixed keyed/keyless
  fleet → each deletes the other's entries for as long as both run; the cache never warms; the docs
  say stop-all-then-rotate. US-003.
- A valid envelope for URL A copied under URL B's key → `bad_mac` (the MAC binds the key). US-001.
- No key configured, storage holds a `v1.` envelope → reject as `unsigned`-class, delete, miss;
  never parsed. US-001.
- Envelope with the right prefix but a non-hex MAC, wrong MAC length, or fewer than two dots →
  `malformed_envelope`. US-001.
- Value longer than `cache.max_value_bytes` → `oversize`, deleted without being read. US-001.
- Payload that verifies but fails `model_validate_json` → spec 2 US-003's corrupt-entry path
  (delete, `corrupt_entries`), not an integrity reject; one value never counts twice. US-001.
- Valkey unreachable *and* no key → `degraded_reasons == ["cache_unavailable",
  "cache_unauthenticated"]` in that order. US-002.
- `VALKEY_URL` set to an empty string (configured-and-invalid) and no key → `cache_unavailable`
  plus `cache_unauthenticated`. US-002.
- Key set but `VALKEY_URL` unset → in-memory backend; the key is read, validated, unused,
  unreported; no WARNING. US-002.
- Key with a trailing newline (a `read -rs` artefact) → stripped, usable; a key with an interior
  space, a control character, or 31 bytes → boot refused, variable named, value never logged. US-002.
- `ttl_hours <= 0` → the existing zero-TTL purge runs before any envelope work. US-001.

## Out of Scope

- Encrypting cached values (integrity, not confidentiality; the Valkey password and network
  placement are the consumer's controls).
- Managing or rotating the Valkey password, or vault integration (12-factor: the consumer gets the
  secret into the environment).
- A `FORAGE_CACHE_HMAC_KEY_PREVIOUS` pair for rolling rotation (non-blocking open question; the
  documented procedure is stop-all-then-rotate).
- Signing anything on `/search` (`/search` never writes the content cache).
- A per-process random key fallback (owner decision 2 rejected it: every restart would be cold).
- Making `cache_unauthenticated` fatal or refusing to boot without a key (a too-short or malformed
  key does refuse boot — that is a misconfiguration, not an absence).
- Comparing `source_url` to the requested URL after a verified parse (the key-bound MAC closes the
  relocation attack; a second check is belt-and-braces and is recorded as a non-blocking question).

## Assumptions

- Spec 2 US-003 landed the guarded parse (`cache.get` treats a `ValidationError` as a miss and
  counts `corrupt_entries`), so this spec adds verification *before* that guard and never
  double-counts. Spec 1 US-004 opened the 1.3.0 window. Spec 3 US-003 added `KNOWN_CONFIG_KEYS`,
  which this spec extends with `cache.max_value_bytes`.
- Poppy is the only consumer; it reads `cache_unauthenticated` as "cached content is unverified
  on a writable store" and decides its own policy from that honest description.
- `hmac`/`hashlib` from the standard library are sufficient; no new dependency.
- The `CacheStorage` protocol's signature is unchanged; `ValkeyStorage.get` gains a size probe
  behind it; `InMemoryStorage` is untouched.
- `CacheMetrics` is mirrored on `/metrics` under `extra="forbid"` with exact field parity enforced
  by test (verified: `retrieval_app.py:568`, `tests/test_contract_metrics.py:305`).

## Technical Considerations

- **Rotation ledger (R32).** US-001 and US-002 each append a `CONTRACT_VERSION` docstring line, so
  both rotate `sanitizer_revision` through `pipeline/contract.py`; `cache.py`, `retrieval_app.py`,
  `models.py` and the tests are not hashed. Record each on the five-file protocol.
- **MAC input.** `b"v1\0" + cache_key + b"\0" + payload`: the version tag domain-separates future
  envelope formats, the cache key binds the value to its URL / mode / policy fingerprint, and the
  payload is the exact `model_dump_json()` bytes, so canonicalisation differences cannot create
  false rejects.
- **Ordering in `get`:** zero-TTL purge → `STRLEN` bound → storage read → envelope verification →
  parse guard (spec 2 US-003) → tz/TTL checks → return. Each rejection path deletes and returns
  `None`.
- **Constant-time comparison** via `hmac.compare_digest`; the reject log carries only the closed
  reason token (`kit_tools/arch/patterns/LOGGING.md`; GOTCHAS "Nothing configures logging" — the
  token goes in the message).
- **Health truthfulness** (CLAUDE.md invariant 5): the new reason is appended, never used to change
  the HTTP status; the compose healthcheck keeps reading only the status code. The posture is
  advertised on an unauthenticated `/health` deliberately (recorded in SECURITY.md).
- **Key rotation is fleet-wide** and cold; the docs say so.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md), [SECURITY.md](../arch/SECURITY.md)
  (`### Adding a new secret`, Secrets inventory)
- Contract governance: [`contract/GOVERNANCE.md`](../../contract/GOVERNANCE.md)
- Operator reference: [`docs/configuration.md`](../../docs/configuration.md),
  [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md), [MONITORING.md](../docs/MONITORING.md),
  [TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md)
- Logging vocabulary: [LOGGING.md](../arch/patterns/LOGGING.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Envelope and verification live in `ContentCache`; only the size probe lives in
`ValkeyStorage.get`.
**Rationale:** `CacheStorage` (`cache.py:344-354`) is a bytes-in/bytes-out protocol shared by
`ValkeyStorage` and `InMemoryStorage`; keeping the envelope above it preserves the
`TestPolicyParityAcrossStorages` harness. The size bound must run before any bytes are pulled,
which only the Valkey client can do (`STRLEN`).
**Alternatives considered:** Signing inside `ValkeyStorage.set` — rejected, the memory backend
would need a no-op twin; bounding after the read — rejected, the allocation is the attack.
**Source:** explorer report 2026-09-19, items 5–7; validation round 1 (security).

**Decision:** The MAC binds the cache key (ruling R21).
**Rationale:** Four independent reviewers reproduced the relocation attack: a payload-only MAC
lets a legitimately signed envelope be copied under another key, and `get` never checks
`source_url` (`cache.py:757`).
**Alternatives considered:** A post-parse `source_url` comparison — kept as an optional second
check; payload-only MAC — rejected.
**Source:** validation round 1 (salty engineer, security, codebase fit, second opinion).

**Decision:** Loud when absent on Valkey, silent on memory (owner decision 2); a too-short or
malformed key refuses boot (R21).
**Rationale:** Only an external Valkey is writable by a third party; a weak key is equivalent to
no key against an adversary who can read a signed envelope and attack offline.
**Alternatives considered:** Quiet-when-absent; per-process random key; treating a short key as
absent — all rejected.
**Source:** planning session 2026-09-19; validation round 1 (security, second opinion).

**Decision:** Reuse the Brave key's boot pattern, sentinel tests and CI grep set.
**Rationale:** `_resolve_brave_key` (`retrieval_app.py:196`) encodes read-once, warn-by-name,
never-log-the-value; `TestKeyNeverLeaks` (`tests/test_brave_provider.py:1478`) is the sentinel
shape; `_REQUIRED_GREP_PATTERNS` (`tests/test_ci_workflow.py:1072`) is the CI half every runtime
credential joins.
**Alternatives considered:** A generic secrets module — deferred (non-blocking).
**Source:** explorer report items 6–7; validation round 1 (codebase fit).

### Scope Adjustments

- The WA-B "cache keys omit the sanitizer revision" item is already closed
  (`cache_policy_fingerprint` takes `sanitizer_revision`); not re-planned here.
- The corrupt-entry guard was placed in spec 2 US-003; this spec depends on it.
- Validation round 1: the `compose/full.yml` passthrough moved from US-003 into US-002 (the
  feature's only on-switch cannot be a P2 doc chore); the `/metrics` mirror work in US-001 became
  unconditional; `cache.max_value_bytes` and the `STRLEN` bound were added (R21).

### Decisions Made

- Envelope format `v1.<hex-mac>.<json>` with the MAC over `b"v1\0" + cache_key + b"\0" + payload`.
- Key material is the stripped environment value as UTF-8 bytes, never base64-decoded; minimum 32
  bytes; shorter or malformed refuses boot.
- Reasons `unsigned`, `bad_mac`, `malformed_envelope`, `oversize` in a module-level frozenset; the
  relocation case is `bad_mac`.
- The reason order on `/health` is `promptguard_unavailable`, `cache_unavailable`,
  `cache_unauthenticated`.
- Overruled: story-quality's suggestion to move all window work into US-002 — each story that
  moves the document appends its own line and re-creates the golden (ruling 5); US-002 keeps
  US-001's line.
- Overruled: the second opinion's optional `source_url` check as a criterion — the key-bound MAC
  closes the attack; the check is a non-blocking question.
- The `_AUTH_HEADER_NAMES` tuple is not touched; the fixture-tree guard is a new walk.

## Clarifications

### Session 2026-09-19
- Q: How should cache integrity work when the operator does not set an HMAC secret? → A: Optional
  key, loud when absent: sign when set; on an external Valkey without a key, `/health` lists
  `cache_unauthenticated`; the in-memory backend needs no key (owner decision 2).
- Q: Where does the signature live, storage or cache? → A: In `ContentCache`, so both storage
  classes and the parity harness stay untouched (the Valkey size probe is the one storage-level
  addition).

### Session 2026-09-19 (validation round 1)
- Rulings applied: R21 (key-bound MAC, 32-byte floor, `STRLEN`-bounded read,
  `cache.max_value_bytes`, mirrored `CacheMetrics`, tampering guidance,
  `_started_with_valkey_url` precedent), R30 (the keyed `v1.2.0` smoke), R32 (both window stories
  rotate), R33 (no captured values), R34 (docstring bullet format).
- Q: What does the MAC cover? → A: The version tag, the cache key and the payload; a relocated
  envelope is `bad_mac`.
- Q: What happens with a 20-byte key? → A: Boot refused, variable named, value never logged.

## Open Questions

- [ ] Whether `brave_key_present`'s shape rules and the new key check should become one
      `usable_secret(raw, *, min_bytes)` helper in a new root module now or when a third secret
      arrives (non-blocking; `pipeline/search_providers/brave.py` is the wrong home for a cache
      credential).
- [ ] Whether `get` should additionally compare `normalize_url(content.source_url)` with the
      requested URL after a verified parse (non-blocking belt-and-braces).
- [ ] Whether a `FORAGE_CACHE_HMAC_KEY_PREVIOUS` verify-only key should support rolling rotation
      (non-blocking; only if stop-all rotation becomes an operational pain).
