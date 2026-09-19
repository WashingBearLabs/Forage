<!-- Template Version: 2.5.0 -->
---
feature: hardening-cache-integrity
status: active
session_ready: true
depends_on: [hardening-hostname-and-config]
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
> HMAC-signed and every value read is verified before it is parsed, so a poisoned entry becomes a
> miss, not model input. Without a key on an external Valkey, `/health` says so
> (`cache_unauthenticated`) instead of pretending the cache is trustworthy. The in-memory backend
> needs no key and reports nothing.
> Context: WA-B punch list ("Valkey content-cache is poisonable from any poppy-net container";
> "cache hit re-enters the model without re-running any sanitization stage"); owner decision 2
> and rulings 5, 6 are binding here; spec 2 US-003 (corrupt entry is a miss) is assumed landed.

## Overview

A cache hit on `/retrieve` is served without re-running any sanitization stage
(`cache.py:757-805`), and the key for an entry is computable from public code (`cache_key`
`cache.py:106-117`, `cache_policy_fingerprint` `:120-164`). Anything that can `SET` on the Valkey
the operator wired through `VALKEY_URL` can therefore plant fabricated "already sanitized" content
under a URL the consumer will ask for. The WA-B review named the committed `poppy_dev` password and
the shared network as the enablers; those are the consumer's to fix. What Forage can do on its own
side is refuse to trust bytes it cannot prove it wrote — that is **owner decision 2**.

The load-bearing choice is what happens when no key is configured. "Sign with a random per-process
key" would make every restart a cold cache; "sign only when a key is set, and stay quiet" would let
a keyless external Valkey look exactly like a keyed one. Decision 2 picks *optional key, loud when
absent*: the feature is opt-in, but a Valkey-backed cache without a key is a `degraded` state with a
named reason, the same way an absent classifier is. The in-memory backend
(`cache.py:546 InMemoryStorage`, selected when `VALKEY_URL` is fully unset) is process-private and
needs neither the key nor the reason — the stock `compose/minimal.yml` deployment and the
`contract_smoke.py --expect-status healthy` run are untouched.

Mechanically the envelope lives in `ContentCache` (`cache.py:702`), not in the storage classes: `put`
writes `v1.<hex-mac>.<json>` where the MAC is HMAC-SHA256 over the JSON bytes; `get` splits, verifies
with `hmac.compare_digest`, and only then hands the JSON to `RetrievedContent.model_validate_json`
(`cache.py:784`, guarded since spec 2 US-003). An unsigned, mis-signed, or unparseable value is
deleted, counted, and reported as a miss with a closed-vocabulary log reason — the key itself is
never logged, never on `/health`, never on `/metrics`, exactly as `FORAGE_BRAVE_API_KEY` is handled
(`retrieval_app.py:197-224`, `brave.py:81 usable_brave_key`).

## Goals

- With a key configured, a Valkey value planted by a test through `tests/fakes.py::FakeStorage`
  (unsigned, or signed with a different key, or with one flipped payload byte) is never returned
  by `ContentCache.get`: the key is deleted and `CacheMetrics.integrity_rejects` advances by one.
- A round trip through `put` then `get` with the same key returns the identical `RetrievedContent`
  and advances no integrity counter.
- With `VALKEY_URL` set and no key, `/health` lists `cache_unauthenticated` in `degraded_reasons`;
  with the key set it does not and `capabilities.cache_hmac_key == 1`; with `VALKEY_URL` unset
  neither appears — three tests, three starts.
- The key value appears zero times in `/health`, `/metrics`, any log record, any 4xx/5xx body, and
  any committed fixture (sentinel test + a fixture-tree grep, as the Brave key had).

## User Stories

### US-001: Signed values on the Valkey backend

**Priority:** P1

**Description:** As an operator running Forage against a shared Valkey, I want cached content to be
signed with a secret only Forage holds, so that a planted entry cannot be served to the model as
sanitized content.

**Independent Test:** With a real `ContentCache` over `tests/fakes.py::FakeStorage` constructed with
`hmac_key=b"sentinel-key"`: `put` stores a value beginning with `v1.` whose MAC verifies with
`hmac.new(key, payload, "sha256")`; `get` returns the content; a value written directly into the
fake storage as bare JSON, as `v1.<mac of another key>.<json>`, and as a valid envelope with one
payload byte flipped, each produce `get → None`, one `delete` call on the fake, and
`integrity_rejects` incremented once per case; with `hmac_key=None` the stored value is bare JSON as
today and every existing `tests/test_cache.py` test passes unchanged.

**Implementation Hints:**
- `ContentCache.__init__` (`cache.py:713`) gains `hmac_key: bytes | None = None`; store it privately
  and never include it in `__repr__`/`__str__` (add a `__repr__` that omits it, on the
  `BraveApiProvider` precedent whose `repr` hides the key — check `brave.py` for how it is done).
- `put` (`cache.py:821-860`): when a key is set, serialize with `content.model_dump_json()` as
  today, compute `hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()`, and pass
  `f"v1.{mac}.{payload}"` to `self._storage.set(...)`. The `CacheStorage` protocol (`:313-355`,
  `set(key, value: str, *, ttl_seconds)`, `get -> bytes | None`) is unchanged; the envelope is a
  string like any other value, so `ValkeyStorage` (`:375`) and `InMemoryStorage` (`:546`) need no
  edit and the `TestPolicyParityAcrossStorages` harness (`tests/test_cache.py:888-941`) keeps
  running both.
- `get` (`cache.py:757-805`): after `raw = await self._storage.get(key)` and before
  `model_validate_json`, call a new `_unwrap(raw) -> bytes | None`: with a key, require the `v1.`
  prefix, split on the first two dots, verify with `hmac.compare_digest`, return the payload; with
  no key, a value that starts with `v1.` cannot be verified and is also rejected (a keyed writer's
  value read by a keyless process is a miss, not a parse of attacker-shaped text). On any
  rejection: `await self._storage.delete(key)`, `self.metrics.integrity_rejects += 1`,
  `logger.warning("cache_integrity_reject — reason=%s", reason)` with `reason` ∈
  `{"unsigned", "bad_mac", "malformed_envelope"}` — tokens in the message, never `extra=`, never
  the key, never the raw value. Extend `_closed_vocabulary_reason`'s docstring (`:297-305`) or add
  the three literals beside it so `tests/test_cache.py`'s closed-vocabulary assertions cover them.
- `CacheMetrics` (`cache.py:187-210`) gains `integrity_rejects: int = 0`. Check whether
  `CacheMetrics` is mirrored on `/metrics` (`retrieval_app.py`'s `MetricsResponse` and its cache
  section — `grep -n "storage_hits" retrieval_app.py`); if it is, the mirror model uses
  `extra="forbid"` (GOTCHAS "Adding a `/metrics` counter without adding it to the model 500s the
  endpoint"), so add the field there, append a docstring line to the 1.3.0 `CONTRACT_VERSION`
  entry, regenerate, and re-create `tests/golden/contract_1_3_0.json` (ruling 5). If it is not
  mirrored, no contract step applies — record which in the Implementation Notes.
- Constant-time compare (`hmac.compare_digest`), stdlib only (`hmac`, `hashlib`); the MAC covers
  the exact serialized bytes, so `model_dump_json` must be called once and its output reused for
  both the MAC and the stored payload.
- `cache.py` is not a `_REVISION_SOURCES` member; nothing rotates.
- Tests: new class in `tests/test_cache.py` (e.g. `TestSignedValues`) beside
  `TestContentCacheGetPut` (`:294`), driving `FakeStorage` directly; parametrise the three
  rejection shapes; assert `storage.delete_calls` advances (the fake's counters are the ones spec 2
  US-003 already uses for the corrupt-entry case).

**Acceptance Criteria:**
- [ ] `ContentCache(hmac_key=...)` accepts `bytes | None`, defaults to `None`, and with `None` the
      stored value and every existing `tests/test_cache.py` test are unchanged.
- [ ] With a key, `put` stores `v1.<64 hex chars>.<json>` and the MAC verifies as HMAC-SHA256 over
      exactly the JSON bytes; `get` returns a `RetrievedContent` equal to what was put.
- [ ] Unsigned bare JSON, an envelope signed with another key, and an envelope with one flipped
      payload byte each yield `get → None`, exactly one `delete` on the storage, and
      `CacheMetrics.integrity_rejects` advancing by one; a keyless cache reading a `v1.` envelope
      also rejects it.
- [ ] Every rejection logs exactly one WARNING whose `getMessage()` contains
      `cache_integrity_reject` and one of `unsigned` / `bad_mac` / `malformed_envelope`; the key
      bytes, the raw value and the Valkey URL appear in no record (`caplog.text`).
- [ ] `repr(cache)` and `str(cache)` do not contain the key bytes.
- [ ] `CacheMetrics.integrity_rejects` exists; if `CacheMetrics` is mirrored on `/metrics`, the
      mirror carries the field, a docstring line is appended to the 1.3.0 entry, the contract is
      regenerated, `tests/golden/contract_1_3_0.json` is re-created and `uv run python -m
      scripts.export_contract --check` is green; either way the Implementation Notes say which.
- [ ] `docs/bootstrap-notes.md`'s latest `sanitizer_revision` record still matches
      `derive_sanitizer_revision()` (no `_REVISION_SOURCES` file changes).
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: The key at boot, and loud when it is absent

**Priority:** P1

**Description:** As an operator, I want Forage to read `FORAGE_CACHE_HMAC_KEY` once at start, sign
with it when present, and tell me on `/health` when my external Valkey is running unsigned — so
the state of the cache is never a guess.

**Independent Test:** Three lifespan starts with `_load_config` and the storage factory patched as
`tests/test_app.py:1196-1208` do it: (a) `VALKEY_URL` set, key unset → `/health` has
`cache_unauthenticated` in `degraded_reasons`, `status == "degraded"`, no `cache_hmac_key`
capability, one WARNING `cache_hmac_key_missing` in `caplog`; (b) `VALKEY_URL` set, key set to a
sentinel → no `cache_unauthenticated`, `capabilities["cache_hmac_key"] == 1`, `app.state.cache`
signs (a `put` through the app produces a `v1.` value in the patched storage); (c) `VALKEY_URL`
unset → neither the reason nor the capability, and `app.state.cache` has no key. In all three the
sentinel appears zero times in `/health`, `/metrics`, `caplog.text` and the `repr` of
`app.state.cache`.

**Implementation Hints:**
- One read site, `_resolve_cache_hmac_key() -> bytes | None` in `retrieval_app.py`, on
  `_resolve_brave_key`'s pattern (`:197-224`): `os.environ.get(CACHE_HMAC_KEY_ENV_VAR)`; strip;
  a blank-after-strip value is silently absent (the `FORAGE_CACHE_HMAC_KEY=` compose-renders-unset
  shape); a value with interior whitespace or control characters is treated as absent after a
  WARNING `cache_hmac_key_invalid — %s is set but is not usable` naming the variable, never the
  value (reuse `usable_brave_key`'s rules from `brave.py:81` through a shared helper rather than a
  copy — move the character rules into a small `usable_secret(raw)` if both can call it). Require
  at least 16 bytes after encoding; shorter is `invalid` too (say so in the docs row).
- Wire it where the lifespan builds the cache (`retrieval_app.py:~1280`, next to
  `_configured_valkey_url()` `:157-171`): pass `hmac_key=` into `ContentCache` **only when the
  backend is Valkey** (`_configured_cache_backend()` `:228`); the in-memory backend gets `None`.
- `/health` (`retrieval_app.py:1446-1487`): add `DEGRADED_CACHE_UNAUTHENTICATED: DegradedReason =
  "cache_unauthenticated"` to `pipeline/contract.py`'s `DegradedReason` literal (`:82-100` — the
  alias is a response-validation gate; a reason not in it 500s `/health`) and append it after
  `cache_unavailable` when the backend is Valkey and no key was resolved. Publish the verdict
  once from the lifespan (`app.state.cache_hmac_key_present: bool`) and read it in the handler —
  never re-read the environment per request, the same discipline as
  `_resolved_search_key_capabilities` (`:313-326`). `CAPABILITY_CACHE_HMAC_KEY = "cache_hmac_key"`
  beside `CAPABILITY_BRAVE_API_KEY` (`:351`), added to `capabilities` as `1` when present, absent
  otherwise (the `capabilities` presence-map convention).
- Contract window (ruling 5): the new `DegradedReason` member is a MINOR enum addition — docstring
  line in the 1.3.0 `CONTRACT_VERSION` entry, `uv run python -m scripts.export_contract`, re-create
  `tests/golden/contract_1_3_0.json`, `--check` green; `pipeline/contract.py` is hashed, so measure
  and record the rotation (ruling 6).
- Boot WARNING when Valkey is configured and no key: `cache_hmac_key_missing — %s is unset; cached
  content is served unsigned (/health reports cache_unauthenticated)` naming the variable.
- Hermeticity: add `FORAGE_CACHE_HMAC_KEY` to `tests/conftest.py::_CLEARED_ENV_VARS` (`:45-54`)
  and update the exact-set assertion in `tests/test_hermeticity.py::
  test_the_cleared_environment_is_the_expected_exact_set` (`:129-138`); the canary at `:141` then
  covers it.
- Key-never-leaks, in the style of `tests/test_cache.py::TestReconnect::
  test_connect_failure_never_logs_url_or_secret` and the Brave spec's sentinel tests: drive a
  signed put, a rejected get, a Valkey connect failure and a `/health` + `/metrics` read with a
  sentinel key, and assert the sentinel is absent from `caplog.text` (all loggers), both bodies,
  and `repr(app.state.cache)`. Add the variable name to the fixture-tree token guard that spec 2 of
  the search epic introduced (`tests/` guard walking `tests/fixtures/` for header names — locate
  with `grep -rn "X-Subscription-Token" tests/` and extend its name list with
  `FORAGE_CACHE_HMAC_KEY`).

**Acceptance Criteria:**
- [ ] `FORAGE_CACHE_HMAC_KEY` is read exactly once per start in `retrieval_app.py`
      (`grep -c FORAGE_CACHE_HMAC_KEY retrieval_app.py` counts the constant's definition and its
      single read); blank → absent silently; interior whitespace/control characters or fewer than
      16 bytes → absent after one `cache_hmac_key_invalid` WARNING naming the variable only.
- [ ] `cache_unauthenticated` is a member of `pipeline/contract.py`'s `DegradedReason` and appears
      in `degraded_reasons` iff the backend is Valkey and no usable key was resolved; `/health`
      still returns HTTP 200 in that state.
- [ ] `capabilities["cache_hmac_key"] == 1` iff a usable key was resolved; the key is absent from
      the map otherwise; the in-memory backend produces neither the reason nor the capability.
- [ ] The lifespan passes the key to `ContentCache` only for the Valkey backend; a start with
      `VALKEY_URL` unset and the key set logs nothing about the key and signs nothing.
- [ ] Sentinel test: the key value appears zero times in `/health`, `/metrics`, every log record and
      `repr(app.state.cache)` across a signed put, a rejected get, a connect failure and both reads.
- [ ] `_CLEARED_ENV_VARS` contains `FORAGE_CACHE_HMAC_KEY` and the exact-set test is updated; the
      fixture-tree token guard names the variable.
- [ ] 1.3.0 window: docstring line appended, contract regenerated, `tests/golden/contract_1_3_0.json`
      re-created, `uv run python -m scripts.export_contract --check` green; `contract_1_2_0.json`
      unchanged.
- [ ] The `sanitizer_revision` rotation (`pipeline/contract.py`) is measured (revert-and-reproduce)
      and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Operator recipe and the doc fan-out

**Priority:** P2

**Description:** As an operator reading the reference, I want one place that says how to generate
the key, where to put it, what `/health` shows without it, and which compose fragment carries it,
so that turning integrity on is a two-line change.

**Independent Test:** `tests/test_compose_fragments.py` passes with a new case asserting
`compose/full.yml`'s forage service lists `FORAGE_CACHE_HMAC_KEY` as a bare passthrough (unset by
default) and `compose/minimal.yml` does not carry it; the doc-scanning tests pass with the new
rows; a reader can follow `docs/configuration.md` to a working keyed deployment without opening
the code.

**Implementation Hints:**
- `compose/full.yml` `environment:` (`:50-60`): add `- FORAGE_CACHE_HMAC_KEY` as a bare name with a
  comment on the `FORAGE_BRAVE_API_KEY` pattern (credential → `compose/.env`, never inline).
  `compose/minimal.yml` is Valkey-free (`TestMinimalIsGenuinelyValkeyFree`,
  `tests/test_compose_fragments.py:553`) and must not carry it. Add the variable to the passthrough
  test the way `TestSearchProviderPassthrough` (`:533-550`) is parametrised, but for `full` only.
- `docs/configuration.md`: a row in `### Runtime` (`:98`, `| Variable | Default | Purpose |`) and a
  new `### Credential handling for FORAGE_CACHE_HMAC_KEY` subsection modelled on the Brave one
  (`:200`): generate with `head -c 32 /dev/urandom | base64` into `compose/.env`, never on a command
  line, never printed; what `/health` shows without it; that rotating the key invalidates every
  signed entry (a miss, not an error). `kit_tools/docs/ENV_REFERENCE.md` `### Runtime service`
  table (`:36-45`, seven columns) gains the row with `Secret: **yes**` and read site
  `retrieval_app._resolve_cache_hmac_key(), start`.
- `kit_tools/docs/MONITORING.md`: `degraded_reasons` row (`:68`) lists the third reason and the
  sentence at `:70` ("The two reasons are the complete set") becomes three; the runbook item at
  `:99` gains "`cache_unauthenticated` is a configuration signal: set the key or accept unsigned
  caching". `kit_tools/docs/TROUBLESHOOTING.md` gains rows for `cache_hmac_key_missing`,
  `cache_hmac_key_invalid` and `cache_integrity_reject`.
- `kit_tools/arch/SECURITY.md`: the cache-poisoning risk (the WA-B item; locate the paragraph
  with `grep -n -i "poison\|VALKEY_URL" kit_tools/arch/SECURITY.md`) moves from accepted-risk to
  mitigated-with-key, with the residual stated: without the key the risk stands and `/health`
  says so; the credential-handling section that covers `FORAGE_BRAVE_API_KEY` (`:200`-area of the
  arch doc, and the `Fixtures` paragraph) names the new variable and the sentinel test.
  `kit_tools/arch/patterns/LOGGING.md`'s inventory gains the three markers.
- `kit_tools/docs/DEPLOYMENT.md` and `README.md`'s configuration section mention the variable in
  the same sentence that mentions `VALKEY_URL`.
- Doc-only story: no test criteria beyond the compose test; nothing rotates.

**Acceptance Criteria:**
- [ ] `compose/full.yml` passes `FORAGE_CACHE_HMAC_KEY` through as a bare name; `compose/minimal.yml`
      does not carry it; `tests/test_compose_fragments.py` asserts both.
- [ ] `docs/configuration.md` carries the runtime row and the credential-handling subsection
      (generation recipe, `/health` behaviour without it, rotation consequence); `kit_tools/docs/
      ENV_REFERENCE.md` carries the seven-column row.
- [ ] `kit_tools/docs/MONITORING.md` lists three `degraded_reasons` and the runbook sentence;
      `kit_tools/docs/TROUBLESHOOTING.md` carries the three marker rows;
      `kit_tools/arch/patterns/LOGGING.md`'s inventory names them.
- [ ] `kit_tools/arch/SECURITY.md` records the poisoning risk as mitigated-with-key with the
      residual stated, and names the sentinel test.
- [ ] `grep -rn "FORAGE_CACHE_HMAC_KEY" README.md kit_tools/docs/DEPLOYMENT.md` each return at
      least one line.
- [ ] Full test suite passes (`uv run pytest`).

## Edge Cases

- Key configured, storage holds a legacy bare-JSON value from before the key was set → `unsigned`
  reject, delete, miss; the next `put` re-signs. Rotating the key → every old entry is `bad_mac`
  once, then gone. US-001.
- No key configured, storage holds a `v1.` envelope (written by a keyed peer) → reject as
  `unsigned`-class (cannot verify), delete, miss; never parsed. US-001.
- Envelope with the right prefix but a non-hex MAC, a MAC of the wrong length, or fewer than two
  dots → `malformed_envelope`. US-001.
- Payload that verifies but fails `model_validate_json` → spec 2 US-003's corrupt-entry path
  (delete, `corrupt_entries`), not an integrity reject; the two counters never double-count one
  value. US-001.
- Valkey unreachable *and* no key → `degraded_reasons == ["cache_unavailable",
  "cache_unauthenticated"]` in that order; both are true. US-002.
- `VALKEY_URL` set to an empty string (configured-and-invalid, per `_configured_valkey_url`) and no
  key → today's `cache_unavailable` plus `cache_unauthenticated` (the backend is Valkey-shaped).
  US-002.
- Key set but `VALKEY_URL` unset → in-memory backend; the key is read, unused, unreported;
  no WARNING (nothing is misconfigured). US-002.
- Key with a trailing newline (a `read -rs` artefact) → stripped, usable; a key with an interior
  space → `cache_hmac_key_invalid`, absent. US-002.
- `ttl_hours <= 0` → the existing zero-TTL purge runs before any envelope work; nothing signed,
  nothing verified. US-001.

## Out of Scope

- Encrypting cached values (integrity, not confidentiality; the Valkey password and network
  placement are the consumer's controls).
- Managing or rotating the Valkey password, or vault integration (12-factor: the consumer gets the
  secret into the environment).
- Signing anything on `/search` (`/search` never writes the content cache; spec 2 of the search
  epic pinned it).
- A per-process random key fallback (owner decision 2 rejected it: every restart would be cold).
- Making `cache_unauthenticated` fatal or refusing to boot without a key.

## Assumptions

- Spec 2 US-003 landed the guarded parse (`cache.get` treats a `ValidationError` as a miss and
  counts `corrupt_entries`), so this spec adds verification *before* that guard and never
  double-counts.
- Spec 1 US-004 opened the 1.3.0 window; the `DegradedReason` addition and any `/metrics` mirror
  field are additive MINOR lines in that entry.
- Poppy is the only consumer; it treats `cache_unauthenticated` as a configuration signal, not an
  outage (mirroring how `cache_unavailable` is documented).
- `hmac`/`hashlib` from the standard library are sufficient; no new dependency.
- The `CacheStorage` protocol and both storage classes are unchanged; the envelope is a string
  value like any other.

## Technical Considerations

- **One rotation** (`pipeline/contract.py`, US-002). `cache.py`, `retrieval_app.py`, `models.py`
  and the tests are not hashed. Record the rotation once (ruling 6).
- **MAC over exact bytes.** `model_dump_json()` output is used verbatim for both the MAC and the
  payload; `get` verifies the stored bytes before decoding, so canonicalisation differences cannot
  create false rejects.
- **Ordering in `get`:** zero-TTL purge → storage read → envelope verification (this spec) → parse
  guard (spec 2 US-003) → tz/TTL checks → return. Each rejection path deletes and returns `None`.
- **Constant-time comparison** via `hmac.compare_digest`; the reject log carries only the closed
  reason token (`kit_tools/arch/patterns/LOGGING.md`; GOTCHAS "Nothing configures logging" — the
  token goes in the message).
- **Health truthfulness** (CLAUDE.md invariant 5): the new reason is appended, never used to change
  the HTTP status; the compose healthcheck keeps reading only the status code.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md), [SECURITY.md](../arch/SECURITY.md)
- Contract governance: [`contract/GOVERNANCE.md`](../../contract/GOVERNANCE.md)
- Operator reference: [`docs/configuration.md`](../../docs/configuration.md),
  [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md), [MONITORING.md](../docs/MONITORING.md)
- Logging vocabulary: [LOGGING.md](../arch/patterns/LOGGING.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Envelope and verification live in `ContentCache`, not in the storage classes.
**Rationale:** `CacheStorage` (`cache.py:313-355`) is a bytes-in/bytes-out protocol shared by
`ValkeyStorage` and `InMemoryStorage`; keeping both untouched preserves the
`TestPolicyParityAcrossStorages` harness and lets the lifespan choose signing by backend.
**Alternatives considered:** Signing inside `ValkeyStorage.set` — rejected, the memory backend
would then need a no-op twin and the parity harness would diverge.
**Source:** explorer report 2026-09-19, items 5–7.

**Decision:** Loud when absent on Valkey, silent on memory (owner decision 2).
**Rationale:** The in-memory backend is process-private (`compose/minimal.yml`, `/health`
`cache_backend: "memory"`); only an external Valkey is writable by a third party.
**Alternatives considered:** Quiet-when-absent; per-process random key — both rejected in the
planning Q&A.
**Source:** planning session 2026-09-19.

**Decision:** Reuse the Brave key's boot pattern and sentinel tests.
**Rationale:** `_resolve_brave_key` (`retrieval_app.py:197-224`) already encodes read-once,
warn-by-name, never-log-the-value; `usable_brave_key` (`brave.py:81`) encodes the character rules;
the hermeticity exact-set test (`tests/test_hermeticity.py:129-141`) is the guard that a new secret
variable cannot leak into tests.
**Alternatives considered:** A generic secrets module — deferred; two callers do not justify it
yet (non-blocking open question).
**Source:** explorer report items 6–7; archived `feature-brave-provider.md` US-012.

### Scope Adjustments

- The WA-B "cache keys omit the sanitizer revision" item is already closed
  (`cache_policy_fingerprint` takes `sanitizer_revision`, `cache.py:120-164`); it is not
  re-planned here.
- The corrupt-entry guard was placed in spec 2 US-003 (it is a `/retrieve` parity fix that needs
  no key); this spec depends on it.

### Decisions Made

- Envelope format `v1.<hex-mac>.<json>`; the version prefix exists so a later envelope change is a
  `malformed_envelope` miss, never a parse of the wrong shape.
- Minimum key length 16 bytes; shorter is treated as invalid with a WARNING.
- The reason order on `/health` is `promptguard_unavailable`, `cache_unavailable`,
  `cache_unauthenticated`.

## Clarifications

### Session 2026-09-19
- Q: How should cache integrity work when the operator does not set an HMAC secret? → A: Optional
  key, loud when absent: sign when set; on an external Valkey without a key, `/health` lists
  `cache_unauthenticated`; the in-memory backend needs no key (owner decision 2).
- Q: Where does the signature live, storage or cache? → A: In `ContentCache`, so both storage
  classes and the parity harness stay untouched.

## Open Questions

- [ ] Whether `usable_brave_key` and the new key check should become one `usable_secret` helper
      now or when a third secret arrives (non-blocking).
- [ ] Whether `contract_smoke.py` should grow a `--expect-degraded-reason` flag to prove
      `cache_unauthenticated` against `compose/full.yml` at the next release (non-blocking; spec 8
      can pick it up).
