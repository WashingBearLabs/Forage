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
execution_order: [US-001, US-002, US-004, US-003]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Cache Integrity — Signed Valkey Values, Loud When Unsigned

> **Spec 4 of `epic-forage-hardening`.** Give the content cache an integrity check: with an
> operator-supplied `FORAGE_CACHE_HMAC_KEY`, every value written to the Valkey backend is
> HMAC-signed **together with the cache key it lives under**, every value read is bounded in one
> atomic command and verified before it is parsed, and a planted, relocated, oversized or
> unparseable entry becomes a miss, not model input. Without a key on an external Valkey,
> `/health` says so (`cache_unauthenticated`) instead of pretending the cache is trustworthy. The
> in-memory backend needs no key and reports nothing.
> Context: WA-B punch list ("Valkey content-cache is poisonable from any poppy-net container";
> "cache hit re-enters the model without re-running any sanitization stage"); owner decision 2 and
> rulings 5, 6, 21, 32, 33, 36, 37, 39, 40 are binding here; spec 2 US-004 (a corrupt entry is a
> miss) and spec 1 US-004 (the 1.3.0 window) are assumed landed.

## Overview

A cache hit on `/retrieve` is served without re-running any sanitization stage
(`pipeline/orchestrator.py:304`, `cache.py:757`), and the key for an entry is computable from
public code (`cache_key` `cache.py:106`, `cache_policy_fingerprint` `:120`; `sanitizer_revision` is
on `/health`). Anything that can `SET` on the Valkey the operator wired through `VALKEY_URL` can
therefore plant fabricated "already sanitized" content under a URL the consumer will ask for. The
WA-B review named the committed `poppy_dev` password and the shared network as the enablers; those
are the consumer's to fix. What Forage can do on its own side is refuse to trust bytes it cannot
prove it wrote **for this key** — that is owner decision 2 as sharpened by ruling 21.

Three decisions carry the spec. First, what the MAC covers. A MAC over the payload alone proves
"Forage wrote these bytes", not "for this URL, mode and policy": the same adversary can `GET`,
so they ask the unauthenticated `/retrieve` for a page they control (a third-party domain lands in
`STANDARD`, which is cacheable — `_NO_CACHE_TIERS` is only `UNTRUSTED`/`BLOCKED`, `cache.py:62`),
let Forage sign it, and copy the envelope verbatim under the computed key for `docs.python.org`;
`ContentCache.get` never compares `source_url` to the requested URL. So the MAC input is the
version tag, the cache key and the payload (ruling 21), and a relocated envelope is `bad_mac`.

Second, how the read is bounded. The adversary who can `SET` can also swap a small value for a
512 MiB one between two commands, so a `STRLEN`-then-`GET` probe is a race the attacker wins.
The read is therefore one atomic `GETRANGE key 0 max` (ruling 21, corrected in round 2): Forage
never allocates more than the bound plus one byte, and a full-length return *is* the oversize
verdict. The same bound applies on the write side — `put` refuses to store a value it would
reject — so `integrity_rejects` keeps meaning "someone other than Forage wrote this", which is
what the operator guidance in US-003 needs it to mean.

Third, what happens when no key is configured. "Sign with a random per-process key" would make
every restart a cold cache; "sign only when a key is set, and stay quiet" would let a keyless
external Valkey look exactly like a keyed one. Decision 2 picks *optional key, loud when absent*:
the feature is opt-in, but a Valkey-backed cache without a key is a `degraded` state with a named
reason, the same way an absent classifier is. The in-memory backend (`cache.py:546
InMemoryStorage`, selected when `VALKEY_URL` is fully unset) is process-private and needs neither
the key nor the reason — the stock `compose/minimal.yml` deployment is untouched, and a key
configured there is reported once at boot as unused rather than silently discarded. A
Valkey-backed `contract_smoke.py --expect-status healthy` run **is** affected: without the key it
never reaches `healthy`, so its docstring says so and spec 8 US-003 runs the keyed configuration
(ruling 30).

Mechanically the envelope lives in `ContentCache` (`cache.py:702`): `put` writes
`v1.<hex-mac>.<json>`; `get` reads at most the bound, splits on bytes, verifies with
`hmac.compare_digest`, and only then hands the JSON to `RetrievedContent.model_validate_json`
(`cache.py:783`, guarded since spec 2 US-004). Every rejection deletes, counts, and logs a
closed-vocabulary reason beside the `ret:<sha256>` cache-key digest — the secret and the raw value
are never logged, never on `/health`, never on `/metrics`, exactly as `FORAGE_BRAVE_API_KEY` is
handled (`retrieval_app.py:196 _resolve_brave_key`, `pipeline/search_providers/brave.py:81
brave_key_present`).

## Goals

- With a key configured, four values planted through `tests/fakes.py::FakeStorage.entries`
  (bare JSON; signed with another key; one flipped payload byte; a valid envelope copied from
  another cache key) and one planted through a Valkey client double (a value longer than
  `cache.max_value_bytes`) are never returned by `ContentCache.get`: the key is deleted and
  `CacheMetrics.integrity_rejects` advances by one per case; every malformed byte string
  (non-UTF-8, empty, bare `v1.`, one dot, non-hex MAC) is `malformed_envelope` and nothing raises.
- A round trip through `put` then `get` under the same key and cache key returns the identical
  `RetrievedContent`, and `integrity_rejects` is still 0 afterwards.
- Forage never writes a value it would reject: a serialised value over `cache.max_value_bytes`
  is skipped on the write side and counted on `storage_oversize_skips`, never on
  `integrity_rejects`.
- With `VALKEY_URL` set and no key, `/health` lists `cache_unauthenticated` in `degraded_reasons`;
  with the key set it does not and `capabilities.cache_hmac_key == 1`; with `VALKEY_URL` unset
  neither appears — three real-lifespan starts, three assertions.
- The key value appears zero times in `/health`, `/metrics`, any log record, a `/retrieve` error
  body produced while the key is configured, `repr(app.state.cache)`, and any file under
  `tests/fixtures/`.
- An operator can turn integrity on with a two-line change to `compose/.env`, and every surface
  that names the health, metrics or log vocabulary names the new reason, capability, counter and
  markers — with no stale "two keys" / "two reasons" / "exactly three strings" / "reachable Valkey
  is healthy" sentence left anywhere (verified by the greps in US-003).

## User Stories

### US-001: Signed, key-bound, size-bounded values on the Valkey backend

**Priority:** P1

**Description:** As an operator running Forage against a shared Valkey, I want cached content to be
signed with a secret only Forage holds, bound to the key it is stored under, and bounded in size
in one atomic read before it is verified, so that a planted, relocated or oversized entry can never
be served to the model as sanitized content — and so that Forage's own large pages never trip the
tampering signal.

**Independent Test:** With a real `ContentCache` over `tests/fakes.py::FakeStorage` constructed with
`hmac_key=b"x" * 32`: `put` stores a value beginning with `v1.` whose MAC equals
`hmac.new(key, b"v1\0" + cache_key.encode() + b"\0" + payload, "sha256").hexdigest()`; `get`
returns the content and `integrity_rejects` is still 0. Four planted values — bare JSON;
`v1.<mac under another key>.<json>`; a valid envelope with one payload byte flipped; a valid
envelope produced by `put` for a different URL written under this cache key — each produce
`get → None`, one `delete` call on the fake, and `integrity_rejects` incremented once, with reasons
`unsigned`, `bad_mac`, `bad_mac`, `bad_mac`. Five malformed byte strings (`b"\xff\xfe"`, `b""`,
`b"v1."`, `b"v1.abc"`, `b"v1." + b"g" * 64 + b".{}"`) each produce `malformed_envelope`, one
delete, one increment, and no exception escaping `get`. Separately, against
`tests/test_cache.py::_connected_valkey_storage(_mock_valkey_client())` whose `getrange` returns
`cache.max_value_bytes + 1` bytes: `ValkeyStorage.get` issues exactly one `getrange` and no `get`,
returns `None`, issues one `delete` on the client, and `integrity_rejects` advances by one with
reason `oversize`. With `hmac_key=None` the stored value is bare JSON as today and every existing
`tests/test_cache.py` test passes unchanged apart from the two named doubles gaining `getrange`.

**Implementation Hints:**
- `ContentCache.__init__` (`cache.py:713`) gains `hmac_key: bytes | None = None`, stored privately
  as `self._hmac_key`. Do **not** add a `__repr__` to satisfy criterion 7: `ContentCache` has none
  today, so `object.__repr__` already omits the key; the criterion is a regression guard against a
  future dataclass conversion (state that in the test's docstring). Counters go through
  `self._metrics` (`cache.py:720`); there is no `metrics` property.
- `put` (`cache.py:821`): when a key is set, `payload = content.model_dump_json().encode()` once;
  `mac = hmac.new(self._hmac_key, b"v1\0" + key.encode() + b"\0" + payload, hashlib.sha256)
  .hexdigest()`; store `f"v1.{mac}.".encode() + payload`. `key` is the `cache_key(...)` already
  computed at the top of `put`/`get`, so the binding costs one concatenation. **Write-side bound:**
  before `self._storage.set`, if `len(value) > self._max_value_bytes` → `self._metrics
  .storage_oversize_skips += 1` (the existing counter `cache.py:210`, the same one
  `InMemoryStorage.set` uses at `:648-650`), log nothing, return `False`; the request is served
  uncached exactly as an in-memory oversize is today. Never `integrity_rejects`.
- The `CacheStorage` protocol (`cache.py:344-354`, `set(key, value: str, *, ttl_seconds)`,
  `get -> bytes | None`) is unchanged for the envelope (the envelope is passed as `str` through
  `set` — it is ASCII prefix + JSON — and comes back as `bytes` from `get`); `InMemoryStorage`
  (`:546`) needs no edit and the `_ParityHarness` / `TestPolicyParityAcrossStorages` harness
  (`tests/test_cache.py:888`, `:941`) keeps running both.
- **Bounded read (ruling 21):** `ValkeyStorage.get` (`cache.py:496`) replaces its `GET` with one
  `GETRANGE key 0 max_value_bytes` — a single round trip, atomic by construction. If the returned
  length is `max_value_bytes + 1` (Valkey's end index is inclusive), the value is oversize: issue
  `DEL`, `self._metrics.integrity_rejects += 1`, WARNING `cache_integrity_reject — reason=oversize
  key=ret:<digest>`, return `None`. Plumbing this hint names in full: (a) `_ValkeyClient`
  (`cache.py:357-372`) gains `async def getrange(self, name: str, start: int, end: int) -> bytes:
  ...` and its docstring stops saying "five operations" (say six; widening is the deliberate act
  its docstring asks for); (b) `ValkeyStorage.__init__` (`cache.py:385`) gains `max_value_bytes:
  int = DEFAULT_CACHE_MAX_VALUE_BYTES` (keyword-only, beside `metrics`); (c) both construction
  sites pass it — `retrieval_app._select_cache_storage` (`retrieval_app.py:240-266`, which today
  hands `CacheSettings` only to `InMemoryStorage`) passes `settings.max_value_bytes`, and the
  `ContentCache(valkey_url=...)` convenience constructor (`cache.py:724`) passes the default;
  (d) both AsyncMock doubles stub it — `tests/test_cache.py:85 _mock_valkey_client()` (docstring
  "The five-command Valkey surface" → six) and `tests/test_app.py:1248 _valkey_double()`; (e)
  `ContentCache.get` additionally rejects `len(raw) > self._max_value_bytes` as `oversize` inside
  `_unwrap` — defence in depth for a backend that cannot pre-bound (the fake, a future storage),
  not the enforcement point: `FakeStorage` is "deliberately unbounded" (`tests/fakes.py:200`) and
  `InMemoryStorage` is write-bounded by `max_bytes` (`:648`). The parity harness's memory side
  never produces an oversize value; say so in the test.
- `cache.max_value_bytes` is a new `config.yaml` key in the `cache:` block (the block comment
  currently frames the block as in-memory-only — reword it), read by `cache_settings_from_config`
  (`cache.py:267`) with `_bounded_int` (`:244`): default **4 MiB**, range 512 KiB – 16 MiB. The
  default sits above the largest value Forage can author — `MAX_EXTRACTED_OUTPUT_BYTES` is 2 MiB
  (`pipeline/extraction_limits.py:12`) plus JSON escaping, metadata and the 67-byte prefix — so a
  legitimate `full`-mode page is never skipped at the default; the `docs/configuration.md` cache
  table row states that relationship and that `cache.max_value_bytes` must stay below
  `cache.max_bytes` (the in-memory total, 32 MiB). `CacheSettings` (`cache.py:237`) gains the field
  and its docstring widens from "one `InMemoryStorage`" to "one cache storage";
  `tests/test_cache.py:869 test_the_shipped_config_yaml_pins_the_documented_defaults` asserts the
  third field; spec 3 US-003's `KNOWN_CONFIG_KEYS` registers the key.
- `get` (`cache.py:757`): after `raw = await self._storage.get(key)` and before
  `model_validate_json`, call `_unwrap(raw: bytes, key: str) -> tuple[bytes | None, str | None]`
  operating on **bytes end to end**: with a key, require the `b"v1."` prefix, `raw.split(b".", 2)`
  into exactly three parts, validate the MAC part is 64 lowercase-hex ASCII bytes before
  comparing, recompute over `b"v1\0" + key.encode() + b"\0" + payload`, compare with
  `hmac.compare_digest(mac_bytes, expected.encode())`; any decode error, wrong part count, wrong
  MAC length or non-hex MAC is `malformed_envelope`; a value without the prefix is `unsigned`; with
  no key configured, a value that starts with `b"v1."` is `unexpected_envelope` (a keyed writer's
  envelope read by a keyless process; never parsed — it is signed, so calling it `unsigned` would
  be backwards) and a bare value passes through as today. Nothing inside `_unwrap` may raise: the
  test in criterion 4 asserts that for arbitrary bytes.
- Reasons are a module-level `CACHE_INTEGRITY_REASONS = frozenset({"unsigned", "bad_mac",
  "malformed_envelope", "oversize", "unexpected_envelope"})` beside `_closed_vocabulary_reason`
  (`cache.py:297`) — that function maps *exceptions* to reasons and is unchanged. The reject line
  is `logger.warning("cache_integrity_reject — reason=%s key=%s", reason, key)` where `key` is the
  `ret:<sha256hex>` cache key (one-way, credential-free, the same digest spec 2 US-004 logs with
  `cache_entry_corrupt`, so an operator can tell a first-enable burst spread across the working set
  from one URL being hammered); tokens in the message, never `extra=`, never the secret, never the
  raw value, never the URL. There are no closed-vocabulary assertions in `tests/test_cache.py`
  today; write a **new** `caplog` test in `TestSignedValues` asserting the marker, the token and
  the `ret:` digest appear and the secret / raw value / URL do not
  (`kit_tools/arch/patterns/LOGGING.md`, "Patterns to Follow").
- `/metrics` (ruling 21 — mirrored, not conditional): `CacheMetrics` (`cache.py:187`) gains
  `integrity_rejects: int = 0`; `CacheMetricsResponse` (`retrieval_app.py:568`, `extra="forbid"`)
  gains the matching `Field(description=...)` and its docstring gains a sentence (it currently
  claims the section holds only storage-operation counters); the handler's `"cache"` dict
  (`retrieval_app.py:1537`) gains `"integrity_rejects": cache_metrics.integrity_rejects`.
  `tests/test_contract_metrics.py::test_dataclass_counters_and_their_models_carry_the_same_fields`
  (`:305`) and `::test_metrics_schema_is_fully_rendered` enforce it. One counter, five reasons: the
  counter cannot separate a first-enable/rotation burst (`unsigned`, `bad_mac` on old entries)
  from tampering (`bad_mac` on fresh ones); the log line's reason token and key digest are the
  discriminator, and US-003 says so in as many words (a per-reason `/metrics` breakdown is
  recorded as a non-blocking question).
- Key material: the stripped environment value is used as UTF-8 bytes, **never base64-decoded**;
  the documented recipe (`head -c 32 /dev/urandom | base64`, ~44 characters) therefore yields a
  44-byte key. US-002 enforces the 32-byte floor at boot.
- Contract window (ruling 36 — copy exactly): append one `* ``1.3.0`` — …` docstring line to
  `pipeline/contract.py`'s `CONTRACT_VERSION` entry (read `:26-68` for the bullet shape) for
  `cache.integrity_rejects`; `uv run python -m scripts.export_contract`; re-create
  `tests/golden/contract_1_3_0.json` through `tests/test_contract_schema.py::_SCHEMA_MODELS`
  (`:28`, the golden's producer); append the field to `_EXPECTED_ONE_THREE_ZERO_DIFF` in the same
  file; refresh the four anchor-quoting pages `tests/test_governance_docs.py::_ANCHOR_QUOTING_PAGES`
  names; `uv run python -m scripts.export_contract --check` green; `contract_1_2_0.json` unchanged.
- Rotation (ruling 32): `cache.py` and `retrieval_app.py` are not hashed; `pipeline/contract.py`
  **is**, and this story appends a docstring line to it, so this story rotates — measure by
  revert-and-reproduce and record at the five sites the repo's protocol names
  (`docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`,
  `kit_tools/docs/GOTCHAS.md`, `kit_tools/arch/CODE_ARCH.md`; `kit_tools/EXECUTION_LOG.md:266`
  records the practice).
- Tests (ruling 40): new `TestSignedValues` in `tests/test_cache.py` beside `TestContentCacheGetPut`
  (`:294`), driving `FakeStorage` directly and planting values through `FakeStorage.entries`
  (`tests/fakes.py:196-268`, `(payload_bytes, expiry)` against the fake's clock); parametrise the
  four envelope shapes and the five malformed strings; assert `storage.delete_calls` advances
  (spec 2 US-004 uses the same counters for the corrupt-entry case). The `oversize` case is a
  Valkey-level test on the `_mock_valkey_client` / `_connected_valkey_storage` seam
  (`tests/test_cache.py:85`, `:96` — the file's stated home for "the failure modes that are
  Valkey's own"). The write-side skip is a third test asserting `storage_oversize_skips` advances,
  `integrity_rejects` does not, and `storage.entries` stays empty. Seam migration: `grep -c
  "five-command\|five operations" cache.py tests/test_cache.py tests/test_app.py` starts at 2
  (`cache.py:357` docstring, `tests/test_cache.py:86`) and ends at 0.

**Acceptance Criteria:**
- [ ] `ContentCache(hmac_key=...)` accepts `bytes | None`, defaults to `None`, and with `None` the
      stored value and every existing `tests/test_cache.py` test are unchanged apart from the two
      Valkey doubles gaining `getrange`.
- [ ] With a key, `put` stores `v1.<64 hex chars>.<json>` and the MAC verifies as HMAC-SHA256 over
      `b"v1\0" + cache_key + b"\0" + payload`; `get` returns a `RetrievedContent` equal to what was
      put; a test asserts `model_dump_json()` is called once per `put` and `integrity_rejects` is
      still 0 after the round trip.
- [ ] The four envelope shapes (unsigned; another key; flipped byte; relocated envelope) planted in
      `FakeStorage` each yield `get → None`, exactly one `delete` on the storage, and
      `integrity_rejects` advancing by one, with the reasons `unsigned` / `bad_mac` / `bad_mac` /
      `bad_mac`; a keyless cache reading a value that starts with `v1.` yields `get → None`, one
      delete, one increment and the reason `unexpected_envelope`.
- [ ] Each of `b"\xff\xfe"`, `b""`, `b"v1."`, `b"v1.abc"` and `b"v1." + b"g" * 64 + b".{}"` yields
      `malformed_envelope` with exactly one delete and one increment, and no exception escapes
      `ContentCache.get` (`_unwrap` operates on bytes end to end).
- [ ] `ValkeyStorage.get` issues one `getrange(key, 0, max_value_bytes)` and no `get`; a
      `max_value_bytes + 1`-byte return yields `None`, one client `delete`, `integrity_rejects` + 1
      and the reason `oversize`, asserted on the `_mock_valkey_client` seam; `_ValkeyClient` has
      six methods and neither double's docstring says "five".
- [ ] `put` never stores a serialised value longer than `cache.max_value_bytes`: the skip advances
      `storage_oversize_skips`, leaves `integrity_rejects` and the storage untouched, and the
      request is served uncached.
- [ ] `cache.max_value_bytes` exists in `config.yaml`'s `cache:` block (default 4 MiB, range
      512 KiB – 16 MiB, `_bounded_int`), reaches `ValkeyStorage.__init__` from both construction
      sites, is registered in `KNOWN_CONFIG_KEYS`, is asserted by
      `test_the_shipped_config_yaml_pins_the_documented_defaults`, and its `docs/configuration.md`
      row states the `MAX_EXTRACTED_OUTPUT_BYTES` and `cache.max_bytes` relationships.
- [ ] Every rejection logs exactly one WARNING whose `getMessage()` contains
      `cache_integrity_reject`, one member of `CACHE_INTEGRITY_REASONS` and the `ret:` cache-key
      digest; the secret bytes, the raw value, the URL and the Valkey URL appear in no record
      (`caplog.text`).
- [ ] `repr(cache)` and `str(cache)` do not contain the key bytes (regression guard; no `__repr__`
      is added).
- [ ] `CacheMetrics.integrity_rejects`, the `CacheMetricsResponse` field and the `/metrics` handler
      dict entry exist; the field-parity test passes; the `/metrics` body carries the key.
- [ ] 1.3.0 window (ruling 36): docstring line appended, contract regenerated,
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`, the field appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF`, the four anchor-quoting pages refreshed, `uv run python -m
      scripts.export_contract --check` green, `tests/golden/contract_1_2_0.json` unchanged.
- [ ] The `sanitizer_revision` rotation (`pipeline/contract.py`) is measured by revert-and-reproduce
      and recorded at the five protocol sites.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: The key at boot, loud when absent — the `/health` contract change

**Priority:** P1

**Description:** As an operator, I want Forage to read `FORAGE_CACHE_HMAC_KEY` once at start,
refuse a key that is too short or malformed, sign with it when present, tell me on `/health` when my
external Valkey is running unsigned, and tell me once at boot when I configured a key the in-memory
backend cannot use — so the state of the cache is never a guess.

**Independent Test:** Three real-lifespan starts through `tests/test_app.py::_started_with_valkey_url`
(`:1270`, keeping the real `ContentCache` and doubling only the Valkey socket via `_valkey_double`
`:1248` — **never** `_running_app` `:797`, which patches `ContentCache` away): (a) `VALKEY_URL`
set, key unset → `degraded_reasons` contains `cache_unauthenticated`, `status == "degraded"`, no
`cache_hmac_key` capability, one WARNING `cache_hmac_key_missing`; (b) `VALKEY_URL` set, key set to
a 44-character sentinel → no `cache_unauthenticated`, `capabilities["cache_hmac_key"] == 1`, and
`app.state.cache` was constructed with the key (`cache._hmac_key is not None`); (c) `VALKEY_URL`
unset, key set → neither the reason nor the capability, `app.state.cache._hmac_key is None`, one
WARNING `cache_hmac_key_unused`. A fourth start with a 20-byte key raises
`cache.CacheConfigurationError` from the lifespan, `str(exc)` contains `FORAGE_CACHE_HMAC_KEY` and
not the sentinel, and exactly one WARNING `cache_hmac_key_too_short` is emitted. In every case the
sentinel appears zero times in `/health`, `/metrics`, `caplog.text` and `repr(app.state.cache)`.

**Implementation Hints:**
- One read site: `CACHE_HMAC_KEY_ENV_VAR = "FORAGE_CACHE_HMAC_KEY"` and
  `_resolve_cache_hmac_key() -> bytes | None` in `retrieval_app.py` on `_resolve_brave_key`'s
  pattern (`:196`): `os.environ.get(CACHE_HMAC_KEY_ENV_VAR)`; strip `" \t\n"` (the
  `BRAVE_KEY_STRIP_CHARS` set, `pipeline/search_providers/brave.py:78`, which deliberately keeps
  `\r`); blank after strip → absent silently (the `FORAGE_CACHE_HMAC_KEY=` compose-renders-unset
  shape). The rules are stated here, not inherited: printable ASCII, no interior whitespace or
  control characters (a shape rule carried over from `brave_key_present` `:81-112`, whose
  rationale is header transport — say so in the docstring), and **at least 32 bytes as UTF-8**
  (ruling 21: anyone who can plant a value can read a signed one and attack the key offline; the
  recipe yields 44). The floor is a **length** check, not an entropy check — the docstring of the
  refusal says a same-length passphrase is not an acceptable substitute and points at the recipe.
- Boot refusal, mechanically: a too-short or malformed value raises `cache.CacheConfigurationError`
  (`cache.py:232`; reuse it — every configuration error in this repo is owned by the module that
  owns the setting: `ExtractionConfigurationError`, `BraveConfigurationError`,
  `SearchProviderConfigurationError`; add it to `retrieval_app.py`'s existing `from cache import
  (...)` block at `:30` and widen its docstring from "in-memory cache configuration" to cache
  configuration and credentials) from `_resolve_cache_hmac_key()` during the lifespan, so startup
  fails the way a bad `cache.max_entries` already does (`tests/test_app.py:1219`); the message
  names the variable only, and the marker logged first is `cache_hmac_key_too_short` /
  `cache_hmac_key_invalid`; never the value.
- Wire it where the lifespan already holds the answer: `storage, backend = _select_cache_storage(...)`
  (`retrieval_app.py:1284`) → pass `hmac_key=` into `ContentCache` only when `backend == "valkey"`
  (gate on that local, not on a second `_configured_cache_backend()` call — its docstring at
  `:228-238` says it exists for the lifespan-less `/health` caller). When a key resolved and the
  backend is `memory`, log one WARNING `cache_hmac_key_unused — %s is set but the in-memory backend
  is process-private; signing is not applicable` (CLAUDE.md invariant 5: the operator asked for a
  control and is not getting it, so it is said once). Publish
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
  1.2.0" at `:378`) and the block comment at `:341-351` are part of the exported document —
  updating them is the contract change; `HealthResponse`'s constants list follows.
- **Existing tests this reason moves (ruling 40)** — name them, do not discover them:
  `tests/test_app.py:1354 test_a_working_valkey_url_selects_valkey_and_stays_healthy` asserts
  `status == "healthy"` and `degraded_reasons == []` on a keyless Valkey — give its start the
  sentinel key (it then still "stays healthy") and add a sibling asserting the keyless start is
  `degraded` with the reason; `tests/test_app.py:1383
  test_a_broken_valkey_url_degrades_and_never_falls_back_to_memory` asserts
  `degraded_reasons == ["cache_unavailable"]` exactly over three parametrisations — its expected
  list becomes `["cache_unavailable", "cache_unauthenticated"]` (keyless) and the test keeps its
  name; `kit_tools/arch/SECURITY.md:293` cites the second by name and needs no change unless it is
  renamed. `grep -c 'degraded_reasons"\] == \[' tests/test_app.py` before and after is the
  starting-count criterion.
- Boot WARNING when Valkey is configured and no key: `cache_hmac_key_missing — %s is unset; cached
  content is served unsigned (/health reports cache_unauthenticated)` naming the variable.
- `contract_smoke.py`'s healthy-mode docstring (`:44`, "and a reachable cache when `VALKEY_URL` is
  set") names `cache_unauthenticated` as a second reason a Valkey-backed container will not reach
  `healthy`; spec 8 US-003 runs the keyed configuration (ruling 30).
- Contract window (ruling 36 — copy exactly): one `* ``1.3.0`` — …` docstring line for the new
  `DegradedReason` member and the capability, `uv run python -m scripts.export_contract`, re-create
  `tests/golden/contract_1_3_0.json` via `_SCHEMA_MODELS` keeping US-001's line, append
  `cache_unauthenticated` / `cache_hmac_key` to `_EXPECTED_ONE_THREE_ZERO_DIFF`, refresh the four
  anchor-quoting pages, `--check` green. `pipeline/contract.py` rotates — measure and record at the
  five protocol sites.

**Acceptance Criteria:**
- [ ] `retrieval_app.py` contains exactly one `os.environ.get(CACHE_HMAC_KEY_ENV_VAR)` call, inside
      `_resolve_cache_hmac_key()`, and the lifespan calls it once (`grep -c
      "_resolve_cache_hmac_key()" retrieval_app.py` returns 2); blank → absent silently.
- [ ] Interior whitespace / control characters or fewer than 32 UTF-8 bytes → the lifespan raises
      `cache.CacheConfigurationError` (`pytest.raises` holds), `str(exc)` names
      `FORAGE_CACHE_HMAC_KEY` and does not contain the value, and exactly one WARNING carrying
      `cache_hmac_key_too_short` or `cache_hmac_key_invalid` is emitted; the refusal docstring
      states that the floor is length, not entropy, and names the CSPRNG recipe.
- [ ] `cache_unauthenticated` is a member of `pipeline/contract.py`'s `DegradedReason`, the
      exact-set test in `tests/test_contract_errors.py` includes it, and it appears in
      `degraded_reasons` iff the backend is Valkey and signing is not active; `/health` still
      returns HTTP 200 in that state.
- [ ] `capabilities["cache_hmac_key"] == 1` iff a usable key was resolved **and** the backend is
      Valkey; the key is absent from the map otherwise; the in-memory backend produces neither the
      reason nor the capability and logs `cache_hmac_key_unused` once when a key is set; `/health`
      on a lifespan-less transport does not raise.
- [ ] The `capabilities` Field description and the `:341-351` block comment name three keys;
      `grep -n "Two keys are defined" retrieval_app.py` returns nothing; `cache_hmac_key` appears in
      `contract/openapi.yaml` after export.
- [ ] `test_a_working_valkey_url_selects_valkey_and_stays_healthy` starts with the sentinel key and
      a new sibling asserts the keyless Valkey start is `degraded` with `cache_unauthenticated`;
      `test_a_broken_valkey_url_degrades_and_never_falls_back_to_memory` expects
      `["cache_unavailable", "cache_unauthenticated"]`; `SECURITY.md:293`'s citation still resolves.
- [ ] Sentinel: the key value appears zero times in `/health`, `/metrics`, every log record and
      `repr(app.state.cache)` across the four starts.
- [ ] `contract_smoke.py`'s healthy-mode docstring names `cache_unauthenticated`.
- [ ] 1.3.0 window (ruling 36): docstring line appended, contract regenerated,
      `tests/golden/contract_1_3_0.json` re-created with US-001's line intact, the two names
      appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`, the four anchor-quoting pages refreshed,
      `--check` green, `contract_1_2_0.json` unchanged.
- [ ] The `sanitizer_revision` rotation (`pipeline/contract.py`) is measured by revert-and-reproduce
      and recorded at the five protocol sites.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Operator recipe and the documentation fan-out

**Priority:** P2

**Description:** As an operator reading the reference, I want one place that says how to generate
the key, where to put it, what `/health` shows without it, what a rising `integrity_rejects` means
and what it cannot tell me, what a boot refusal looks like, and how to rotate safely — and no page
left claiming there are two capability keys, two degraded reasons, three cache log strings, or that
a reachable Valkey is `healthy` on its own.

**Independent Test:** The `### Credential handling for FORAGE_CACHE_HMAC_KEY` subsection of
`docs/configuration.md` contains the generation command, the CSPRNG-not-passphrase sentence, the
UTF-8-never-decoded rule, the 32-byte floor, the `/health` behaviour without the key, the boot
refusal, and the rotation consequence; the by-value grep of criterion 6 returns exactly the
allowed remainder; the doc-scanning tests pass.

**Implementation Hints:**
- `docs/configuration.md`: a row in `### Runtime` (`| Variable | Default | Purpose |`) and the new
  credential subsection modelled on the Brave one: generate with `head -c 32 /dev/urandom |
  base64` into `compose/.env`, never on a command line, never printed; the value must come from a
  CSPRNG — a passphrase of the same length passes the boot check and is not an acceptable
  substitute; the value is used as UTF-8 bytes and never base64-decoded; at least 32 bytes or the
  service refuses to start; what `/health` shows without it; rotation: **stop every replica,
  change the key, start** — two replicas over one Valkey with different keys delete each other's
  entries for as long as both run, and a mixed keyed/keyless fleet does the same. Also the cache
  backend selection table (`:129-130`, "Set and reachable | Valkey | `valkey` | `healthy`" splits
  into keyed → `healthy` and key-less → `degraded`, `cache_unauthenticated`), `:467-468` (cache
  metrics rows gain `integrity_rejects`), `:539` ("two keys" / "two reasons"), and the
  `cache.max_value_bytes` row (if US-001 has not already added it) with its two relationships.
- `kit_tools/docs/ENV_REFERENCE.md` `### Runtime service` table (seven columns, the
  `FORAGE_BRAVE_API_KEY` row at `:43` is the shape) gains the row with `Secret: **yes**` and read
  site `retrieval_app._resolve_cache_hmac_key(), start`.
- `kit_tools/docs/MONITORING.md`: the `capabilities` row (`:63`), the `degraded_reasons` row (`:68`)
  and "The two reasons are the complete set" (`:70`), the body-shape block (`:81`), the runbook item
  (`:99`), the cache counter table (`:170-177`) with a row for `cache.integrity_rejects`, the
  `### Startup lines you may see` table (`:229`) with all four markers (`cache_hmac_key_missing`,
  `cache_hmac_key_unused`, `cache_hmac_key_too_short`, `cache_hmac_key_invalid` — the last two are
  what an operator whose container will not start is decoding), and `### Closed vocabularies` →
  `cache.py` (`:242`, "exactly one of `connect_failed`, …") extended with the five
  `cache_integrity_reject` reasons. Incident guidance: a one-time burst bounded by the working set
  is expected when the key is first set or rotated; a sustained non-zero `integrity_rejects` rate
  with a key set and no rotation in flight means something other than Forage is writing to the
  Valkey — treat it as a security event (rotate the key, audit Valkey ACL and network placement),
  not a cache-health blip; **the counter cannot separate the two by itself** — the reason token
  and the `ret:` key digest in the `cache_integrity_reject` log line are the discriminator
  (`unsigned` spread across many keys = migration; `bad_mac` on fresh keys = tampering); and a
  Forage-authored page over the bound is a `storage_oversize_skips` increment, never an integrity
  reject, so it is not this signal. Wording for `cache_unauthenticated`: cached `/retrieve` content
  cannot be proven to be Forage's own and is served without re-sanitization; on a shared Valkey
  treat it as an open cache-poisoning path until a key is set (not "a configuration signal").
- `kit_tools/docs/TROUBLESHOOTING.md`: `:56` ("Exactly two values exist") and `:60`; a narrative
  `### cache_unauthenticated: Valkey configured without a signing key` section beside the
  `cache_unavailable` one (`:345`); and a `### The container exits at start: FORAGE_CACHE_HMAC_KEY
  refused` section — what the operator sees (exit, no `/health`), which marker to grep for, the
  32-byte floor, the strip rules, the recipe. This file has no log-marker table.
- `kit_tools/docs/API_GUIDE.md:105` and `:109` (the "two keys" capability sentence).
- `kit_tools/arch/SERVICE_MAP.md`: `:108` ("`DegradedReason` … is exactly `promptguard_unavailable`
  and `cache_unavailable`"), the `/health` signal row (`:135`) and the Valkey-mode table
  (`:147-149`); `kit_tools/arch/CODE_ARCH.md:198` (the exhaustive parenthetical).
- `README.md:115` (the cache-mode matrix's "`/health` when the cache is fine" row splits keyed /
  key-less) and the configuration section's mention of the variable in the same sentence as
  `VALKEY_URL`; `kit_tools/docs/DEPLOYMENT.md:220` and the runnable block at `:239-241`
  (`--expect-status healthy` on a Valkey-backed container now needs the key in the env file) plus
  the variable beside `VALKEY_URL`.
- `kit_tools/arch/SECURITY.md`: a `FORAGE_CACHE_HMAC_KEY` row in the Secrets inventory table
  (`:216-224`) stating the CSPRNG requirement; follow and cite the four steps of `### Adding a new
  secret` (`:248-253`); `:233`'s "three patterns" becomes four; `:291`'s `degraded_reasons`
  enumeration gains the third; a new cache-poisoning entry (there is none today — `grep -i poison`
  is empty) stating: with a key, integrity and authenticity of cached values are assured; three
  residuals remain and stay the consumer's controls — availability (an attacker can still delete or
  overwrite, forcing misses and outbound fetches plus classifier inference; the concurrency and
  latency bounds of spec 6 are the control that bounds it), confidentiality, and replay (a captured
  valid envelope restored under the same key pins one Forage-authored snapshot; the signed
  `retrieved_at` plus the TTL check bound the window to `cache_ttl_hours`, which is a reason not to
  raise that value casually on a shared Valkey); without the key the poisoning path stands and
  `/health` says so; and that the posture is advertised on an unauthenticated `/health` because
  honest health outranks obscurity and the same fact is observable by anyone who can write to the
  cache.
- `kit_tools/arch/patterns/LOGGING.md`: the `### cache.py` table and its "Exactly three strings"
  sentence (`:80` area) gain the five integrity reasons; refresh the three `cache.py:NNN-NNN`
  citations in that section (`_closed_vocabulary_reason`, `_attempt_connect`, `_mark_disconnected`)
  after US-001 shifted them; the `cache` Logger Inventory row; the four boot markers.
- `kit_tools/PRODUCT_VISION.md` Success Criteria: the "Fails loud, never silent" row ("a missing
  paid key is a supported mode, never a degraded state") and the "Key-less floor" row ("no new
  required secret, no degradation of the default path") are scoped to the paid *search* key and to
  the default (memory-backed) path, so the vision and the shipped `/health` contract agree.
- Doc-only story: no code; the doc-scanning suite is the gate; nothing rotates.

**Acceptance Criteria:**
- [ ] `docs/configuration.md` carries the runtime row and the credential subsection (generation
      recipe, CSPRNG-not-passphrase, UTF-8-never-decoded, 32-byte floor, `/health` behaviour, boot
      refusal, stop-all-replicas rotation) and the split Valkey row in the backend-selection table;
      `kit_tools/docs/ENV_REFERENCE.md` carries the seven-column row.
- [ ] `kit_tools/docs/MONITORING.md` names three capability keys, three degraded reasons (row, body
      shape, runbook), the `cache.integrity_rejects` counter row with the burst-versus-sustained
      guidance and the "the log line is the discriminator" sentence, the four startup markers, and
      the extended `cache.py` closed vocabulary; the `cache_unauthenticated` wording states the
      consequence.
- [ ] `kit_tools/docs/TROUBLESHOOTING.md` and `kit_tools/docs/API_GUIDE.md` no longer say two values /
      two keys; TROUBLESHOOTING has both new sections.
- [ ] `kit_tools/arch/SECURITY.md` has the Secrets-inventory row, the four-pattern sentence, the
      three-reason enumeration, and the cache-poisoning entry with three residuals, the spec 6
      cross-reference and the disclosure trade-off; `kit_tools/arch/patterns/LOGGING.md` names the
      five reasons, the four boot markers and refreshed `cache.py` citations;
      `kit_tools/arch/SERVICE_MAP.md`, `kit_tools/arch/CODE_ARCH.md`, `README.md`,
      `kit_tools/docs/DEPLOYMENT.md` and `kit_tools/PRODUCT_VISION.md` carry the named edits.
- [ ] By-value sweep (ruling 39): `grep -rn -iE "two keys|the two reasons|exactly two values|exactly
      one of .connect_failed|exactly three strings|three patterns|is exactly .promptguard_unavailable|
      Set and reachable \| Valkey \| .valkey. \| .healthy|when the cache is fine \| .healthy. \|
      .healthy" docs kit_tools README.md` is run at the story's start (record the count) and at its
      end returns only hits outside the `/health` capability, `degraded_reasons`, `cache.py`
      closed-vocabulary, secret-grep and cache-mode vocabularies (list each remaining hit in
      Implementation Notes); `grep -rn FORAGE_CACHE_HMAC_KEY README.md kit_tools/docs/DEPLOYMENT.md`
      each return at least one line.
- [ ] Full test suite passes (`uv run pytest`).

### US-004: Distribution fan-out — compose on-switch, hermeticity, CI secret grep, leak sentinels

**Priority:** P1

**Description:** As an operator and as the maintainer of the release lane, I want the documented
full-stack compose fragment able to carry the key, the test environment to clear it, CI's secret
grep to know its name, and a sentinel suite proving the value never leaks — so the feature has a
supported switch and the same guards every other runtime credential has.

**Independent Test:** `docker compose -f compose/full.yml config --env-file /dev/null` with a
complete placeholder set (`HF_TOKEN`, `SEARXNG_SECRET`, `FORAGE_BRAVE_API_KEY`,
`FORAGE_CACHE_HMAC_KEY`, `VALKEY_URL`) from a scratch project directory renders the bare-name
passthrough on the `forage` service and `compose/minimal.yml` does not carry the name
(`tests/test_compose_fragments.py` asserts both); `tests/test_hermeticity.py`'s exact-set test
includes the variable; `tests/test_ci_workflow.py` finds the name in both `ci.yml` grep copies;
a signed `put`, a rejected `get`, a Valkey connect failure, a `/retrieve` 422 raised while the key
is set, and a `/health` + `/metrics` read all leave the sentinel absent from `caplog.text`, every
body and `repr(app.state.cache)`; the fixture-tree walk finds neither the variable name nor the
sentinel under `tests/fixtures/`.

**Implementation Hints:**
- The on-switch: `compose/full.yml` `environment:` gains `- FORAGE_CACHE_HMAC_KEY` as a bare name
  with a comment on the `FORAGE_BRAVE_API_KEY` pattern (credential → `compose/.env`, never inline)
  and a note that a healthy status now needs it; the header quickstart recipe (`:11-16`, the
  `{ echo HF_TOKEN=...; echo SEARXNG_SECRET=...; } > .env` lines) gains the
  `FORAGE_CACHE_HMAC_KEY=` line so the documented happy path still reaches `healthy`
  (`feature-hardening-release` US-004 adds the *upgrade* sentence to the same files — different
  edit, no collision). `compose/minimal.yml` is Valkey-free (`TestMinimalIsGenuinelyValkeyFree`,
  `tests/test_compose_fragments.py:553`) and must not carry it; extend
  `TestSearchProviderPassthrough`'s idiom (`:533`) for `full` only. Any render an implementer
  records is a placeholder-only excerpt per ruling 33 — `env -i` still loads `compose/.env`, so use
  `--env-file /dev/null` and a scratch directory.
- Hermeticity: add `FORAGE_CACHE_HMAC_KEY` to `tests/conftest.py::_CLEARED_ENV_VARS` (`:45`) and
  update the exact-set assertion in
  `tests/test_hermeticity.py::test_the_cleared_environment_is_the_expected_exact_set` (`:129-138`);
  the canary at `:141` then covers it.
- CI's secret grep (the Brave precedent's CI half): add `FORAGE_CACHE_HMAC_KEY` to
  `tests/test_ci_workflow.py::_REQUIRED_GREP_PATTERNS` (`:1072`) and to both copies in
  `.github/workflows/ci.yml` (the `secret-grep` heredoc and the publish config grep at `:998`);
  `kit_tools/arch/SECURITY.md:233`'s "three patterns" prose is US-003's.
- Key-never-leaks, on `tests/test_brave_provider.py::TestKeyNeverLeaks` (`:1478`): drive a signed
  put, a rejected get, a Valkey connect failure (`_valkey_double` raising on `ping`), a `/retrieve`
  refusal raised while the key is set — a blocked-domain or invalid-URL 422 from
  `pipeline/orchestrator.py:268-284` (a cache outage never produces a `/retrieve` error body:
  `cache.py:496-537` swallow every storage exception and `orchestrator.py:286-304` continues
  uncached), and a `/health` + `/metrics` read with the sentinel; assert it is absent from
  `caplog.text` (all loggers), every body, and `repr(app.state.cache)`. Fixture-tree guard: a
  **new** walk in `tests/test_cache.py` asserting the literal `FORAGE_CACHE_HMAC_KEY` and the
  sentinel value appear in no file under `tests/fixtures/` (do not touch
  `tests/test_brave_provider.py:80 _AUTH_HEADER_NAMES` — that is a header-name tuple scoped to
  Brave fixtures).
- Nothing here moves the document or a hashed file: no window block, no rotation.

**Acceptance Criteria:**
- [ ] `compose/full.yml` passes `FORAGE_CACHE_HMAC_KEY` through as a bare name with the comment and
      its header recipe includes the `FORAGE_CACHE_HMAC_KEY=` line; `compose/minimal.yml` does not
      carry the name; `tests/test_compose_fragments.py` asserts all three; no rendered config or
      env value is recorded anywhere (ruling 33).
- [ ] `_CLEARED_ENV_VARS` contains `FORAGE_CACHE_HMAC_KEY` and the exact-set test is updated;
      `_REQUIRED_GREP_PATTERNS` and both `ci.yml` grep copies carry the name and
      `tests/test_ci_workflow.py` passes.
- [ ] Sentinel suite: the key value appears zero times in `/health`, `/metrics`, a `/retrieve` 422
      body raised while the key is configured, every log record across the five drives, and
      `repr(app.state.cache)`; the fixture-tree walk finds neither the variable name nor the
      sentinel under `tests/fixtures/`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- Key configured, storage holds a legacy bare-JSON value → `unsigned` reject, delete, miss; the
  next `put` re-signs. Key rotated (single process) → every old entry is `bad_mac` once, then gone.
  US-001.
- Two replicas over one Valkey with different keys (a rolling rotation), or a mixed keyed/keyless
  fleet → each deletes the other's entries for as long as both run; the cache never warms; the docs
  say stop-all-then-rotate. US-003.
- A valid envelope for URL A copied under URL B's key → `bad_mac` (the MAC binds the key). US-001.
- No key configured, storage holds a `v1.` envelope → `unexpected_envelope`, delete, miss; never
  parsed. US-001.
- Non-UTF-8 bytes, an empty value, a bare `v1.`, a `v1.` with one dot, or a 64-character non-hex
  MAC → `malformed_envelope`; nothing raises. US-001.
- Value longer than `cache.max_value_bytes` on Valkey → one `GETRANGE` returns the bound plus one
  byte → `oversize`, `DEL`, never allocated beyond the bound; a writer racing the read cannot
  widen it because the read is one command. US-001.
- Forage's own serialised value longer than `cache.max_value_bytes` → not written,
  `storage_oversize_skips` + 1, `integrity_rejects` unchanged, request served uncached. US-001.
- A valid envelope captured and restored later under the same key (replay) → verifies; bounded by
  the signed `retrieved_at` and the TTL check to `cache_ttl_hours`; documented residual. US-003.
- Payload that verifies but fails `model_validate_json` → spec 2 US-004's corrupt-entry path
  (delete, `corrupt_entries`), not an integrity reject; one value never counts twice. US-001.
- Valkey unreachable *and* no key → `degraded_reasons == ["cache_unavailable",
  "cache_unauthenticated"]` in that order. US-002.
- `VALKEY_URL` set to an empty string (configured-and-invalid) and no key → `cache_unavailable`
  plus `cache_unauthenticated`. US-002.
- Key set but `VALKEY_URL` unset → in-memory backend; the key is read, validated, unused, and one
  WARNING `cache_hmac_key_unused` says so; no capability, no reason. US-002.
- Key with a trailing newline (a `read -rs` artefact) → stripped, usable; a key with an interior
  space, a control character, or 31 bytes → `CacheConfigurationError`, variable named, value never
  logged. US-002.
- A 32-character passphrase → boots (the floor is length); the docs say it is not an acceptable
  key. US-003.
- `ttl_hours <= 0` → the existing zero-TTL purge runs before any envelope work. US-001.

## Out of Scope

- Encrypting cached values (integrity, not confidentiality; the Valkey password and network
  placement are the consumer's controls).
- Managing or rotating the Valkey password, or vault integration (12-factor: the consumer gets the
  secret into the environment).
- A `FORAGE_CACHE_HMAC_KEY_PREVIOUS` pair for rolling rotation (non-blocking open question; the
  documented procedure is stop-all-then-rotate).
- Freshness inside the MAC (a nonce or sequence): replay is bounded by the signed `retrieved_at`
  and the TTL and is recorded as a residual, not closed.
- Signing anything on `/search` (`/search` never writes the content cache).
- A per-process random key fallback (owner decision 2 rejected it: every restart would be cold).
- Making `cache_unauthenticated` fatal or refusing to boot without a key (a too-short or malformed
  key does refuse boot — that is a misconfiguration, not an absence).
- A per-reason breakdown of `integrity_rejects` on `/metrics` (non-blocking question; the log line
  carries the discriminator).
- Comparing `source_url` to the requested URL after a verified parse (the key-bound MAC closes the
  relocation attack; a second check is belt-and-braces and is recorded as a non-blocking question).

## Assumptions

- Spec 2 US-004 landed the guarded parse (`cache.get` treats a `ValidationError` as a miss and
  counts `corrupt_entries`), so this spec adds verification *before* that guard and never
  double-counts. Spec 1 US-004 opened the 1.3.0 window. Spec 3 US-003 added `KNOWN_CONFIG_KEYS`,
  which this spec extends with `cache.max_value_bytes`.
- Poppy is the only consumer; it reads `cache_unauthenticated` as "cached content is unverified
  on a writable store" and decides its own policy from that honest description.
- `hmac`/`hashlib` from the standard library are sufficient; no new dependency. `GETRANGE` is
  available on every Valkey/Redis the cache already supports (it is a Redis 2.4 command).
- The `CacheStorage` protocol's signature is unchanged; `ValkeyStorage.get` swaps `GET` for a
  bounded `GETRANGE` behind it; `InMemoryStorage` is untouched; `_ValkeyClient` widens by one
  method as a deliberate act.
- `CacheMetrics` is mirrored on `/metrics` under `extra="forbid"` with exact field parity enforced
  by test (verified: `retrieval_app.py:568`, `tests/test_contract_metrics.py:305`).
- No healthcheck ships in this repo's `Dockerfile` or compose fragments today
  (`kit_tools/arch/SERVICE_MAP.md:74`); the consumer's compose check, and the liveness check spec 6
  adds, read only the HTTP status, which this spec never changes.

## Technical Considerations

- **Rotation ledger (ruling 32).** US-001 and US-002 each append a `CONTRACT_VERSION` docstring
  line, so both rotate `sanitizer_revision` through `pipeline/contract.py`; `cache.py`,
  `retrieval_app.py`, `models.py`, the compose fragments, `ci.yml` and the tests are not hashed;
  US-003 and US-004 rotate nothing. Record each rotation at the five protocol sites.
- **MAC input.** `b"v1\0" + cache_key + b"\0" + payload`: the version tag domain-separates future
  envelope formats, the cache key (`ret:<sha256hex>`, a fixed alphabet, so the separator is
  unambiguous) binds the value to its URL / mode / policy fingerprint, and the payload is the exact
  `model_dump_json()` bytes, so canonicalisation differences cannot create false rejects.
- **Ordering in `get`:** zero-TTL purge → bounded `GETRANGE` read (Valkey) → `_unwrap` (length
  defence, prefix, split, hex check, `compare_digest`) → parse guard (spec 2 US-004) → tz/TTL
  checks → return. Each rejection path deletes, counts, logs the reason and the key digest, and
  returns `None`; nothing in the path raises on attacker-controlled bytes.
- **Round trips.** `GETRANGE` replaces `GET` one-for-one, so a Valkey-backed cache hit costs the
  same single round trip as today with or without a key; spec 6's latency envelope is unaffected.
- **Both sides bounded.** The write-side skip keeps `integrity_rejects` meaning "someone other than
  Forage wrote this"; the default 4 MiB exceeds anything Forage can author at the 2 MiB extraction
  ceiling, and the floor of 512 KiB is a memory knob an operator must reconcile with `full`-mode
  content sizes (the config row says so).
- **Constant-time comparison** via `hmac.compare_digest` on bytes; the reject log carries only the
  closed reason token and the key digest (`kit_tools/arch/patterns/LOGGING.md`; GOTCHAS "Nothing
  configures logging" — the token goes in the message).
- **Health truthfulness** (CLAUDE.md invariant 5): the new reason is appended, never used to change
  the HTTP status; a configured-but-unusable key on the memory backend is said once at boot. The
  posture is advertised on an unauthenticated `/health` deliberately (recorded in SECURITY.md).
- **Key rotation is fleet-wide** and cold; the docs say so.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md), [SECURITY.md](../arch/SECURITY.md)
  (`### Adding a new secret`, Secrets inventory), [SERVICE_MAP.md](../arch/SERVICE_MAP.md)
- Contract governance: [`contract/GOVERNANCE.md`](../../contract/GOVERNANCE.md)
- Operator reference: [`docs/configuration.md`](../../docs/configuration.md),
  [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md), [MONITORING.md](../docs/MONITORING.md),
  [TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md), [DEPLOYMENT.md](../docs/DEPLOYMENT.md)
- Logging vocabulary: [LOGGING.md](../arch/patterns/LOGGING.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Envelope and verification live in `ContentCache`; the pre-read bound lives in
`ValkeyStorage.get` as one atomic `GETRANGE`, with a defence-in-depth length check in `_unwrap`.
**Rationale:** `CacheStorage` (`cache.py:344-354`) is a bytes-in/bytes-out protocol shared by
`ValkeyStorage` and `InMemoryStorage`; keeping the envelope above it preserves the
`TestPolicyParityAcrossStorages` harness. Only the Valkey client can refuse to pull an oversized
value off the wire, and only a single command does that against a writer who can race two; the
length check in `_unwrap` covers backends that cannot pre-bound and costs nothing.
**Alternatives considered:** Signing inside `ValkeyStorage.set` — rejected, the memory backend
would need a no-op twin; `STRLEN` then `GET` — rejected in round 2, a two-command race the
adversary wins; bounding only after the read — rejected, the allocation is the attack.
**Source:** explorer report 2026-09-19, items 5–7; validation rounds 1 and 2 (security, salty
engineer, codebase fit, second opinion).

**Decision:** The MAC binds the cache key (ruling 21).
**Rationale:** Four independent reviewers reproduced the relocation attack: a payload-only MAC
lets a legitimately signed envelope be copied under another key, and `get` never checks
`source_url` (`cache.py:757`).
**Alternatives considered:** A post-parse `source_url` comparison — kept as an optional second
check; payload-only MAC — rejected.
**Source:** validation round 1 (salty engineer, security, codebase fit, second opinion).

**Decision:** Both sides are bounded by the same number, and the default exceeds what Forage can
author.
**Rationale:** A read-only bound turns Forage's own large `full`-mode pages into a
write/reject/refetch loop that advances the counter the runbook calls a security event
(`MAX_EXTRACTED_OUTPUT_BYTES` is 2 MiB, `pipeline/extraction_limits.py:12`; the stored value is
larger than the content). Refusing the write on `storage_oversize_skips` — what `InMemoryStorage`
already does — keeps `integrity_rejects` honest.
**Alternatives considered:** A distinct reason for self-authored oversize — rejected, the existing
counter already means exactly this.
**Source:** validation round 1 (completionist, salty engineer, security, second opinion).

**Decision:** Loud when absent on Valkey, said once on memory (owner decision 2, ruling 21); a
too-short or malformed key refuses boot.
**Rationale:** Only an external Valkey is writable by a third party; a weak key is equivalent to
no key against an adversary who can read a signed envelope and attack offline; an operator who
configured the control and is not getting it must hear so once (invariant 5).
**Alternatives considered:** Quiet-when-absent; per-process random key; treating a short key as
absent; silence on the memory backend — all rejected.
**Source:** planning session 2026-09-19; validation rounds 1 and 2 (security, salty engineer,
second opinion).

**Decision:** Reuse the Brave key's boot pattern, `cache.CacheConfigurationError`, the sentinel
tests and the CI grep set.
**Rationale:** `_resolve_brave_key` (`retrieval_app.py:196`) encodes read-once, warn-by-name,
never-log-the-value; every configuration error is owned by the module that owns the setting, and
this one is a cache credential; `TestKeyNeverLeaks` (`tests/test_brave_provider.py:1478`) is the
sentinel shape; `_REQUIRED_GREP_PATTERNS` (`tests/test_ci_workflow.py:1072`) is the CI half every
runtime credential joins.
**Alternatives considered:** A generic secrets module — deferred (non-blocking); a new error class
in `retrieval_app.py` — rejected, the repo owns errors per setting module.
**Source:** explorer report items 6–7; validation rounds 1 and 2 (codebase fit, story quality).

### Scope Adjustments

- The WA-B "cache keys omit the sanitizer revision" item is already closed
  (`cache_policy_fingerprint` takes `sanitizer_revision`); not re-planned here.
- The corrupt-entry guard was placed in spec 2 US-004; this spec depends on it.
- Validation round 1: the `compose/full.yml` passthrough moved from US-003 into the boot story;
  the `/metrics` mirror work in US-001 became unconditional; `cache.max_value_bytes` and the read
  bound were added (ruling 21).
- Validation round 2 (ruling 37): US-002 split — the boot read, the `/health` contract change and
  the two named `/health` tests stay in US-002; the compose on-switch, hermeticity, CI grep set,
  sentinel suite and fixture-tree walk are US-004 (`execution_order` runs it before the docs story;
  `feature-hardening-release.md:576`'s "spec 4 US-002" reference to the on-switch now points at
  US-004 — a one-line edit for that spec's owner, noted here). The `STRLEN` probe became one
  `GETRANGE`; the write side gained the same bound; the vocabulary gained `unexpected_envelope`.

### Decisions Made

- Envelope format `v1.<hex-mac>.<json>` with the MAC over `b"v1\0" + cache_key + b"\0" + payload`,
  parsed on bytes end to end.
- Key material is the stripped environment value as UTF-8 bytes, never base64-decoded; minimum 32
  bytes (length, not entropy); shorter or malformed raises `cache.CacheConfigurationError`.
- Reasons `unsigned`, `bad_mac`, `malformed_envelope`, `oversize`, `unexpected_envelope` in a
  module-level frozenset; the relocation case is `bad_mac`; the keyless-reads-an-envelope case is
  `unexpected_envelope` (a signed value cannot honestly be called `unsigned`).
- The reject line carries the reason token and the `ret:` key digest (spec 2 US-004's shape) and
  never the secret, the raw value or the URL.
- The reason order on `/health` is `promptguard_unavailable`, `cache_unavailable`,
  `cache_unauthenticated`.
- Overruled: story-quality's suggestion to move all window work into US-002 — each story that
  moves the document appends its own line and re-creates the golden (rulings 5, 36); US-002 keeps
  US-001's line.
- Overruled: the second opinion's optional `source_url` check as a criterion — the key-bound MAC
  closes the attack; the check is a non-blocking question.
- Overruled: the salty engineer's per-reason `/metrics` breakdown — one counter plus the log
  line's reason token and key digest is the discriminator, and US-003 says so; a breakdown is a
  non-blocking question because it widens the `/metrics` model by five fields for a signal the log
  already carries.
- Overruled: adding a `__repr__` to `ContentCache` — none exists, so `object.__repr__` already
  omits the key; the criterion stays as a regression guard.
- Overruled: the story-quality suggestion to lift `cache.max_value_bytes` out of US-001 — the
  bound and the verification order are one concern ("bounded before verified"); the config surface
  is three lines.
- The `_AUTH_HEADER_NAMES` tuple is not touched; the fixture-tree guard is a new walk.
- `GETRANGE`'s end index is inclusive, so a request for `0..max_value_bytes` returns at most
  `max_value_bytes + 1` bytes and a return of that length is the oversize verdict.

## Clarifications

### Session 2026-09-19
- Q: How should cache integrity work when the operator does not set an HMAC secret? → A: Optional
  key, loud when absent: sign when set; on an external Valkey without a key, `/health` lists
  `cache_unauthenticated`; the in-memory backend needs no key (owner decision 2).
- Q: Where does the signature live, storage or cache? → A: In `ContentCache`, so both storage
  classes and the parity harness stay untouched (the Valkey bounded read is the one storage-level
  addition).

### Session 2026-09-19 (validation round 1)
- Rulings applied: 21 (key-bound MAC, 32-byte floor, bounded read, `cache.max_value_bytes`,
  mirrored `CacheMetrics`, tampering guidance, `_started_with_valkey_url` precedent), 30 (the keyed
  `v1.2.0` smoke), 32 (both window stories rotate), 33 (no captured values), 34 (docstring bullet
  format).
- Q: What does the MAC cover? → A: The version tag, the cache key and the payload; a relocated
  envelope is `bad_mac`.
- Q: What happens with a 20-byte key? → A: Boot refused, variable named, value never logged.

### Session 2026-09-19 (validation round 2)
- Rulings applied: 21 (corrected — atomic `GETRANGE`, write-side bound on
  `storage_oversize_skips`, 4 MiB default / 512 KiB floor, bytes end to end with every malformed
  shape → `malformed_envelope`, `oversize` tested on the Valkey seam and the four envelope shapes on
  `FakeStorage`, length-not-entropy, `cache_hmac_key_unused`, the two named `/health` tests, spec 2
  US-004), 33 (corrected capture rule for the compose render), 36 (uniform window block), 37
  (US-002 → US-002 + US-004), 39 (by-value doc sweep), 40 (named test seams with starting counts).
- Q: Why not `STRLEN` then `GET`? → A: Two commands against a writer who can race them; `GETRANGE`
  is one command and one round trip.
- Q: Why does the keyless-reads-a-`v1.`-value case get its own token? → A: The value is signed;
  `unsigned` would be its opposite, and every other case is pinned to a literal.
- Q: What does a Forage-authored value over the bound do? → A: It is never written; the skip is
  counted on `storage_oversize_skips`, so `integrity_rejects` keeps meaning tampering.

## Open Questions

- [ ] Whether `brave_key_present`'s shape rules and the new key check should become one
      `usable_secret(raw, *, min_bytes)` helper in a new root module now or when a third secret
      arrives (non-blocking; `pipeline/search_providers/brave.py` is the wrong home for a cache
      credential).
- [ ] Whether `get` should additionally compare `normalize_url(content.source_url)` with the
      requested URL after a verified parse (non-blocking belt-and-braces).
- [ ] Whether a `FORAGE_CACHE_HMAC_KEY_PREVIOUS` verify-only key should support rolling rotation
      (non-blocking; only if stop-all rotation becomes an operational pain).
- [ ] Whether `/metrics` should carry a per-reason breakdown of `integrity_rejects` (non-blocking;
      the log line carries the discriminator today).
