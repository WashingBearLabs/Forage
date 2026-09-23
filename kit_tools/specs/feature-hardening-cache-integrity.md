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
> rulings 5, 6, 21, 32, 33, 36, 37, 39, 40, 41, 43 are binding here; spec 2 US-004 (a corrupt entry is a
> miss) and spec 1 US-004 (the 1.3.0 window) are assumed landed. Every line number in this spec was
> measured before its `depends_on` specs landed (spec 2 US-004 edits `cache.py` above every anchor
> here); re-locate every anchor by the symbol or quoted string beside it, never by the number.

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
The read is therefore one atomic `GETRANGE key 0 max` (ruling 21, corrected in rounds 2–5):
one read never allocates more than the bound plus one byte, and a full-length return *is* the
oversize verdict. `GETRANGE` differs from `GET` in one way that matters: on a missing key it
returns an empty string, never nil — so an empty return is a **miss** (counted on
`storage_misses`, no delete, no reject), and because every envelope Forage writes is at least 69
bytes long, "empty" is unambiguously "absent" (an adversary who plants `SET key ""` buys one
refetch and nothing else). A `WRONGTYPE` reply (the key holds a list or a hash) proves the
connection is healthy and the value is not ours: it is an integrity reject, never a disconnect.
The same byte bound applies on the write side — `put` refuses to store a value it would reject,
measured in UTF-8 bytes on both sides, and deletes the superseded entry before it skips (round 4) —
so `integrity_rejects` keeps meaning "someone other than Forage wrote this" **for a fleet that
keeps `cache.max_value_bytes` equal across replicas and does not lower it**: the bound is not a
cache-key input, so lowering it turns Forage's own larger past writes into `oversize` rejects, which
the runbook in US-003 names as the one reason that is not tampering; the bound is per read, and
Technical Considerations states the aggregate.

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
`v1.<hex-mac>.<json>`; `get` reads at most the bound, treats an empty read as a miss, splits on
bytes, verifies with `hmac.compare_digest`, and only then hands the JSON to
`RetrievedContent.model_validate_json` (`cache.py:784`, guarded since spec 2 US-004). Every rejection deletes, counts, and logs a
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
  (non-UTF-8, bare `v1.`, one dot, non-hex MAC, wrong version tag) is `malformed_envelope` and
  nothing raises; an empty read is a miss on every backend, and a `WRONGTYPE` reply on Valkey is
  a `wrong_type` reject that leaves the connection up.
- A round trip through `put` then `get` under the same key and cache key returns the identical
  `RetrievedContent`, and `integrity_rejects` is still 0 afterwards.
- Forage never writes a value it would reject: a serialised value over `cache.max_value_bytes`
  **UTF-8 bytes** is skipped on the write side — the existing entry under that key is deleted
  first, so a stale signed value is never served in the new one's place — and counted on
  `storage_oversize_skips` (which therefore moves on both backends — the exported description that
  says "always 0 on Valkey" is corrected in the same window and the widening is a recorded
  GOVERNANCE ruling, documentation-only, no bump), never on `integrity_rejects`.
- A `FORAGE_CACHE_HMAC_KEY` under 32 UTF-8 bytes, or carrying interior whitespace, control or
  non-printable-ASCII characters, refuses boot with `cache.CacheConfigurationError` naming the
  variable and never the value.
- With `VALKEY_URL` set and no key, `/health` lists `cache_unauthenticated` in `degraded_reasons`;
  with the key set it does not and `capabilities.cache_hmac_key == 1`; with `VALKEY_URL` unset
  neither appears — three real-lifespan starts, three assertions.
- The key value appears zero times in `/health`, `/metrics`, any log record, a `/retrieve` error
  body produced while the key is configured, `repr(app.state.cache)`, and any file under
  `tests/fixtures/`.
- An operator can turn integrity on with one line in `compose/.env` (the `compose/full.yml`
  passthrough ships), and every surface
  that names the health, metrics or log vocabulary names the new reason, capability, counter and
  markers — with no stale "two keys" / "two reasons" / "exactly three strings" / "reachable Valkey
  is healthy" sentence left anywhere (verified by the greps in US-003).

## User Stories

### US-001: Signed, key-bound, size-bounded values on the Valkey backend

**Priority:** P1

**Description:** As an operator running Forage against a shared Valkey, I want cached content to be
signed with a secret only Forage holds, bound to the key it is stored under, and bounded in size
in one atomic read before it is verified, so that a planted, relocated, oversized or wrongly-typed
entry can never be served to the model as sanitized content — and so that neither an ordinary
cache miss nor Forage's own large pages ever trip the tampering signal.

**Independent Test:** With a real `ContentCache` over `tests/fakes.py::FakeStorage` constructed with
`hmac_key=b"x" * 32` and a **small** explicit `max_value_bytes=4096` (the constructor takes any
`int`; the 512 KiB floor lives in `cache_settings_from_config`, so the drives never allocate
megabytes to test an inequality — round 5; the 4 MiB default is asserted by the defaults test and
the construction-site criterion): `put` stores a value beginning with `v1.`
whose MAC equals `hmac.new(key, b"v1\0" + cache_key.encode() + b"\0" + payload, "sha256").hexdigest()`;
`get` returns the content and `integrity_rejects` is still 0. Four planted values — bare JSON;
`v1.<mac under another key>.<json>`; a valid envelope with one payload byte flipped; a valid
envelope produced by `put` for a different URL written under this cache key — each produce
`get → None`, one `delete` call on the fake, and `integrity_rejects` incremented once, with reasons
`unsigned`, `bad_mac`, `bad_mac`, `bad_mac`. Five malformed byte strings (`b"\xff\xfe"`, `b"v1."`,
`b"v1.abc"`, `b"v1." + b"g" * 64 + b".{}"`, `b"v2." + b"0" * 64 + b".{}"`) each produce
`malformed_envelope`, one delete, one increment, and no exception escaping `get`; a planted value
of `max_value_bytes + 1` bytes on the fake produces `oversize`, one delete, one increment; a
planted `b""` produces `get → None` with **no** delete and no increment (a miss). Separately, on
`tests/test_cache.py::_connected_valkey_storage(_mock_valkey_client())`: (i) `getrange` returning
`max_value_bytes + 1` bytes → `ValkeyStorage.get` issues exactly one `getrange` and no `get`, returns
`None`, issues one `delete` on the client, and `integrity_rejects` advances by one with reason
`oversize` — neither `storage_hits` nor `storage_misses` moves; (ii) `getrange` returning `b""` →
`None`, `storage_misses` + 1, `storage_hits` unchanged, no delete, no reject, no WARNING; (iii)
`getrange` raising `redis.exceptions.ResponseError("WRONGTYPE …")` → `None`, one `delete`,
`integrity_rejects` + 1 with reason `wrong_type`, `connected` still true, `operation_failures`,
`storage_hits` and `storage_misses` unchanged; (iv) `aioredis.from_url` is called with
`decode_responses=False`, a lifespan start with `VALKEY_URL=redis://valkey:6379/4?decode_responses=1`
(likewise `?encoding=latin1`, `?protocol=3`) raises `cache.CacheConfigurationError` whose message
and `caplog.text` name the option and never the host or URL, and for a query-free URL the real
client built without connecting reports `connection_pool.connection_kwargs["decode_responses"] is
False`. A `put` over the bound for a key that already holds a small valid
envelope deletes that entry before skipping (`storage.entries` no longer has the key,
`storage.delete_calls` + 1, `storage_oversize_skips` + 1, `integrity_rejects` unchanged). With
`hmac_key=None` the stored value is bare JSON as today and every existing `tests/test_cache.py` test passes apart from the named
Valkey doubles gaining `getrange` (the seam list below).

**Implementation Hints:**
- **Anchors.** Every `file:line` below was measured before spec 2 US-004 landed; locate by symbol.
- `ContentCache.__init__` (`cache.py:713`) gains **two** keyword-only parameters: `hmac_key: bytes |
  None = None` (stored as `self._hmac_key`) and `max_value_bytes: int = DEFAULT_CACHE_MAX_VALUE_BYTES`
  (stored as `self._max_value_bytes`). New module constants beside `DEFAULT_CACHE_MAX_BYTES`
  (`cache.py:224-229`): `DEFAULT_CACHE_MAX_VALUE_BYTES = 4 * 2**20`, `_MIN_CACHE_MAX_VALUE_BYTES =
  512 * 2**10`, `_MAX_CACHE_MAX_VALUE_BYTES = 8 * 2**20`. Construction sites: the lifespan
  (`retrieval_app.py:1288`, `ContentCache(storage=storage, metrics=app.state.cache_metrics)`) passes
  `max_value_bytes=settings.max_value_bytes` from the `app.state.cache_settings` it resolved at
  `:1282` (US-002 adds `hmac_key=` to the same call); the `ContentCache(valkey_url=...)` convenience
  constructor (`cache.py:724`) and the `tests/test_cache.py:934` parity fixture take the default. Do
  **not** add a `__repr__` to satisfy the repr criterion: `ContentCache` has none today, so
  `object.__repr__` already omits the key; the criterion is a regression guard against a future
  dataclass conversion (state that in the test's docstring). Counters go through `self._metrics`
  (`cache.py:720`); there is no `metrics` property.
- `put` (`cache.py:821`): `json_text = content.model_dump_json()` once and `payload =
  json_text.encode()` for the MAC input; when a key is set, `mac = hmac.new(self._hmac_key, b"v1\0"
  + key.encode() + b"\0" + payload, hashlib.sha256).hexdigest()` and the value handed to
  `CacheStorage.set` is the **`str`** `f"v1.{mac}." + json_text` — `set(key, value: str, *,
  ttl_seconds)` at `cache.py:348` fixes the type, and the storage layer is what encodes
  (`InMemoryStorage.set`, `cache.py:643-644`); the round-3 hint's `bytes` expression would have been
  a pyright-strict error against that signature (round 4, codebase fit). `key` is the
  `cache_key(...)` already computed at the top of `put`/`get`, so the binding costs one
  concatenation. **Write-side bound, in bytes:** measure `len(b"v1." + mac.encode() + b".") +
  len(payload)` in the keyed path and `len(payload)` in the keyless path (the precedent is
  `InMemoryStorage.set`'s `payload = value.encode(); size = len(payload)`; a `str` length counts
  characters and a CJK page is ~2.6× more bytes than characters); over `self._max_value_bytes` →
  **delete the existing entry for that key first** (`await self._storage.delete(key)` — the
  invariant `InMemoryStorage.set`'s docstring states, "a superseded payload can never be served in
  the new one's place"; without it a page that was 3 MiB yesterday and 5 MiB today keeps serving
  the stale signed envelope for the rest of its TTL — round 4), then
  `self._metrics.storage_oversize_skips += 1` (the existing counter, `cache.py:210`), log nothing,
  return `False`; the request is served uncached exactly as an in-memory oversize is today. Never
  `integrity_rejects`. After this story the counter has **two producers**: `ContentCache.put` at
  `cache.max_value_bytes` (the one reachable through the cache on both backends) and
  `InMemoryStorage.set` at `cache.max_bytes` (reachable through the cache only when an operator sets
  `max_value_bytes` above `max_bytes` — see the WARNING below — and from the direct-drive tests at
  `tests/test_cache.py:1302`, `:1320`); US-003's `MONITORING.md` / `docs/configuration.md` wording
  names the `max_value_bytes` threshold, not just "Forage's own write-side refusals".
- The `CacheStorage` protocol (`cache.py:344-354`, `set(key, value: str, *, ttl_seconds)`,
  `get -> bytes | None`) is unchanged for the envelope (ASCII prefix + JSON passes through `set` as
  `str` and comes back as `bytes`); `InMemoryStorage` (`:546`) needs no edit and the
  `_ParityHarness` / `TestPolicyParityAcrossStorages` harness (`tests/test_cache.py:888`, `:941`)
  keeps running both.
- **Bounded read (ruling 21, corrected in round 3):** `ValkeyStorage.get` (`cache.py:496`) replaces
  its `GET` with one `GETRANGE key 0 max_value_bytes` — a single round trip, atomic by construction.
  **Miss rule:** `GETRANGE` on a missing key returns `b""`, never nil, so an empty return is a
  miss — `storage_misses += 1`, return `None`, exactly what the `raw is None` branch does today
  (`cache.py:505-509`), with a code comment saying `GETRANGE` cannot express nil and Forage never
  writes an empty value; a non-empty return counts `storage_hits`. If the returned length is
  `max_value_bytes + 1` (Valkey's end index is inclusive), the value is oversize: issue `DEL`,
  `integrity_rejects += 1`, WARNING `cache_integrity_reject — reason=oversize key=ret:<digest>`,
  return `None` — and **neither `storage_hits` nor `storage_misses` moves** for `oversize` or
  `wrong_type` (the read served nothing and it was not an absence; only `integrity_rejects` moves),
  while the cache-layer `_unwrap` rejects never touch the storage counters (whatever the storage
  counted stands) — round 5. **Type rule:** a `redis.exceptions.ResponseError` whose message starts with
  `WRONGTYPE` is caught **before** the blanket `except Exception` that calls `_mark_disconnected`
  (`cache.py:500-503`, `:475-486`) and handled as an integrity reject (`wrong_type`: `DEL`, count,
  WARNING, `None`) — the reply proves the connection is healthy; every other exception keeps
  today's disconnect path. Plumbing, in full: (a) `_ValkeyClient` (`cache.py:357-372`) gains `async
  def getrange(self, name: str, start: int, end: int) -> bytes: ...` and its docstring (`:358`, "The
  five Valkey operations this cache actually issues") says six — keep `name` for consistency with
  the protocol's other members, but redis-py's real parameter is `key`, the connection is a `cast`
  and every double is an `AsyncMock`, so the call site **must be positional**
  (`await client.getrange(key, 0, self._max_value_bytes)`); a keyword call would pass strict typing
  and the whole suite and raise `TypeError` only against a real Valkey (round 5); (b) `ValkeyStorage.__init__`
  (`cache.py:385`) gains `max_value_bytes: int = DEFAULT_CACHE_MAX_VALUE_BYTES` (keyword-only,
  beside `metrics`); (c) both storage construction sites pass it — `retrieval_app._select_cache_storage`
  (`retrieval_app.py:240-266`, which today hands `CacheSettings` only to `InMemoryStorage`) passes
  `settings.max_value_bytes`, and the `ContentCache(valkey_url=...)` convenience constructor forwards
  **`self._max_value_bytes`** to the `ValkeyStorage` it builds (never the module default — otherwise
  `ContentCache(valkey_url=..., max_value_bytes=512 * 2**10)` reads at 4 MiB and rejects at 512 KiB,
  a split-brain bound; a criterion asserts the two bounds are equal after construction through both
  paths — round 4); (d) the Valkey doubles stub `getrange` **returning `b""` for a miss, never `None`**
  (a `None` stub would hide the miss bug in every test): `tests/test_cache.py:85 _mock_valkey_client()`
  (docstring "The five-command Valkey surface" → six) and `tests/test_app.py:1248 _valkey_double()`;
  (e) `ContentCache.get` additionally treats `raw == b""` from any storage as a miss (no count at
  this layer — the storage counted it) and rejects `len(raw) > self._max_value_bytes` as `oversize`
  inside `_unwrap` — defence in depth for a backend that cannot pre-bound (`FakeStorage` is
  "deliberately unbounded", `tests/fakes.py:200`; `InMemoryStorage` is write-bounded by `max_bytes`,
  `:648`), tested on `FakeStorage`; the parity harness's memory side never produces an oversize
  value — say so in the test; (f) **the byte mode is refused into existence, not overridden** (R21
  corrected, round 5). redis-py's `ConnectionPool.from_url` is `url_options = parse_url(url);
  kwargs.update(url_options)` — "querystring arguments always win", its own docstring says, and it
  was verified on the pinned redis 8.1.0: `from_url("redis://h:6379/4?decode_responses=1",
  decode_responses=False)` yields `connection_kwargs["decode_responses"] == "1"`, decoding **on** —
  so the round-4 explicit kwarg pins nothing against the URL, and the failure it was written to
  prevent (`len(reply)` as a character count against a server-side byte index, a
  `UnicodeDecodeError` on a truncated multibyte boundary raised inside redis-py's parser, caught
  by the blanket `except Exception` and flapping the cache disconnected on every oversize read)
  still stood. The pin is therefore a URL rule enforced by the module that owns the setting:
  `ValkeyStorage.__init__` (`cache.py:385`) reads `urlsplit(valkey_url).query` through `parse_qs` —
  **query keys only**, never the netloc, userinfo or values — and, when any of `decode_responses`,
  `encoding`, `encoding_errors` or `protocol` is present, logs one WARNING
  `valkey_url_option_forbidden — option=%s` (the key name only; the operator's only diagnostic for
  a container that exits at start, so it goes to stderr before the raise, as US-002's key refusal
  does) and raises `cache.CacheConfigurationError` naming `VALKEY_URL` and the option, never the
  URL. It lives in `ValkeyStorage.__init__` rather than in `retrieval_app._configured_valkey_url()`
  because the lifespan-less `/health` path calls the latter (`retrieval_app.py:228-238`) and must
  never raise (US-002's criterion); `_select_cache_storage`'s docstring ("Nothing here inspects,
  splits or logs it") stays true and gains one sentence saying the query-key check lives in
  `cache.py` beside the guarded connect. `_attempt_connect` still passes `decode_responses=False`
  explicitly (belt), `ValkeyStorage.get` still encodes a non-`bytes` reply to UTF-8 before the
  length check (braces), and the tests assert the **effect**, not the call: a lifespan start with
  `VALKEY_URL=redis://valkey:6379/4?decode_responses=1` raises `CacheConfigurationError`
  (`pytest.raises`), `str(exc)` and `caplog.text` carry `decode_responses` and never the host or
  URL (the `tests/test_cache.py:753 test_connect_failure_never_logs_url_or_secret` idiom); the same
  for `?encoding=latin1` and `?protocol=3`; for a query-free URL a test constructs the real client
  **without connecting** (`aioredis.from_url` is lazy — no socket is opened, so the hermeticity
  guard is untouched) and asserts `client.connection_pool.connection_kwargs["decode_responses"] is
  False`, pinning the library precedence the rule relies on; the `call_args.kwargs` assertion stays
  as the belt's own check. `socket_timeout` / `socket_connect_timeout` in the query are
  **deliberately operator-overridable** — tuning, not a correctness input, and the
  `asyncio.timeout(_RECONNECT_TIMEOUT_S)` around the connect (`cache.py:408`) is untouched by them;
  Technical Considerations records it. A `VALKEY_URL` carrying one of the four options boots
  today, so the refusal is an upgrade note in `docs/configuration.md`'s `VALKEY_URL` row, written so
  spec 8 can lift it.
- `cache.max_value_bytes` is a new `config.yaml` key in the `cache:` block (the block comment
  currently frames the block as in-memory-only — reword it), read by `cache_settings_from_config`
  (`cache.py:267`) with `_bounded_int` (`:244`): default **4 MiB**, range 512 KiB – **8 MiB**;
  when `max_value_bytes > max_bytes`, `cache_settings_from_config` **boots** and logs one WARNING
  `cache_bounds_inverted — cache.max_value_bytes exceeds cache.max_bytes; the in-memory storage
  applies cache.max_bytes` (keys in the message, never values; the marker joins MONITORING's
  startup-lines table and LOGGING.md's inventory) — round 4 replaced the round-3 boot refusal,
  which would have stopped every deployment that pinned `cache.max_bytes` under the new 4 MiB
  default (a documented, in-range value) on a key it never configured, and which coupled a Valkey
  read bound to an in-memory total that is dead weight on Valkey. The default
  sits above the largest value Forage can author — `MAX_EXTRACTED_OUTPUT_BYTES` is 2 MiB
  (`pipeline/extraction_limits.py:12`) plus JSON escaping, metadata and the 67-byte prefix — so a
  legitimate `full`-mode page is never skipped at the default; the `docs/configuration.md` cache
  table row states that relationship, the `max_bytes` relationship (a WARNING, not a refusal),
  **the container-memory one** (peak cache-read allocation is `max_value_bytes × in-flight
  /retrieve requests`, and cache reads sit under no concurrency bound; cross-reference
  `feature-hardening-resource-envelope`'s sizing section — a **deliberate forward reference** spec 6
  closes: its memory rule carries the `+ cache.max_value_bytes` (one in-flight read) term when the
  Valkey backend is selected, recorded on spec 6's own page in round 5; the section does not exist
  when this story runs (`epic_seq` 4 against 6), so the criterion gates the sentence written here,
  never the target),
  **and the fleet one** (round 4): the bound is not a cache-key input (`derive_sanitizer_revision`
  hashes sources, the model identity and the threshold — `pipeline/sanitizer_revision.py:38-42` —
  never the cache block), so every replica over one shared Valkey must carry the same
  `cache.max_value_bytes` for the same reason every replica must carry the same key, and lowering
  the value produces a bounded burst of `oversize` rejects against Forage's own larger past writes
  — a tuning consequence the row states, not tampering. `CacheSettings` (`cache.py:237`) gains the
  field and its docstring widens from "one `InMemoryStorage`" to "one cache storage";
  `tests/test_cache.py:869 test_the_shipped_config_yaml_pins_the_documented_defaults` asserts the
  third field; spec 3 US-003's `KNOWN_CONFIG_KEYS` registers the key.
- `get` (`cache.py:757`): after `raw = await self._storage.get(key)` — `None` or `b""` → miss —
  and before `model_validate_json`, call `_unwrap(raw: bytes, key: str) -> tuple[bytes | None, str |
  None]` operating on **bytes end to end**: with a key, require the `b"v1."` prefix (any other
  `bN.` version tag is `malformed_envelope`), `raw.split(b".", 2)` into exactly three parts,
  validate the MAC part is 64 lowercase-hex ASCII bytes before comparing, recompute over
  `b"v1\0" + key.encode() + b"\0" + payload`, compare with `hmac.compare_digest(mac_bytes,
  expected.encode())`; any decode error, wrong part count, wrong MAC length or non-hex MAC is
  `malformed_envelope`; a value without the prefix is `unsigned`; with no key configured, a value
  that starts with `b"v1."` is `unexpected_envelope` (a keyed writer's envelope read by a keyless
  process; never parsed — it is signed, so calling it `unsigned` would be backwards) and a bare
  value passes through as today. Nothing inside `_unwrap` may raise: the test in criterion 4
  asserts that for arbitrary bytes.
- Reasons are a module-level `CACHE_INTEGRITY_REASONS = frozenset({"unsigned", "bad_mac",
  "malformed_envelope", "oversize", "unexpected_envelope", "wrong_type"})` beside
  `_closed_vocabulary_reason` (`cache.py:297`) — that function maps *exceptions* to reasons and is
  unchanged. The reject line is `logger.warning("cache_integrity_reject — reason=%s key=%s", reason,
  key)` where `key` is the `ret:<sha256hex>` cache key (an opaque, credential-free correlation
  handle — one-way but confirmable against a guessed URL, which US-003's wording says; the same
  digest spec 2 US-004 logs with `cache_entry_corrupt`, so an operator can tell a first-enable
  burst spread across the working set from one URL being hammered); tokens in the message, never
  `extra=`, never the secret, never the raw value, never the URL. There are no closed-vocabulary
  assertions in `tests/test_cache.py` today; write a **new** `caplog` test in `TestSignedValues`
  asserting the marker, the token and the `ret:` digest appear and the secret / raw value / URL do
  not (`kit_tools/arch/patterns/LOGGING.md`, "Patterns to Follow").
- `/metrics` (ruling 21 — mirrored, not conditional): `CacheMetrics` (`cache.py:187`) gains
  `integrity_rejects: int = 0`; `CacheMetricsResponse` (`retrieval_app.py:568`, `extra="forbid"`)
  gains the matching `Field(description=...)`; the handler's `"cache": {...}` dict
  (`retrieval_app.py:1537`) gains `"integrity_rejects": cache_metrics.integrity_rejects`. **Two
  existing description strings become false in the same edit and are corrected in the same
  export:** `CacheMetricsResponse`'s class docstring (`retrieval_app.py:574-577`, "Only the in-memory
  storage can move `storage_evictions`/`storage_oversize_skips` — Valkey … has no byte bound of
  ours") and the `storage_oversize_skips` Field description (`:600-604`, "Always 0 on Valkey") —
  both ship in `contract/openapi.yaml` (`:82`, `:118-119`); after this story
  `storage_oversize_skips` counts Forage's own write-side refusals on both backends and
  `storage_evictions` stays memory-only. The widening is recorded as a **GOVERNANCE recorded
  ruling** (round 4): the counter's meaning — values Forage itself refused to store as over a
  per-entry byte bound — is unchanged; what changes is the set of backends that can produce one and
  the description that said only one could; a counter documented as always 0 on Valkey moving off
  0 is additive behaviour, not a redefinition of the name, so it is documentation-only within the
  1.3.0 window and carries no bump — the ruling names Example 4 as considered and says why it does
  not apply. The ruling is its own `### (<next free letter>) ` section with a `**Source:**` line
  (spec 1 US-003 takes `(e)` and `(f)` first; reconcile the letter at merge the way a rotation
  ordinal is), appended to `tests/test_governance_docs.py::_RULING_MARKERS` (`:93`) — a ruling
  outside the tuple is ungated — with the count words at `contract/GOVERNANCE.md:174` ("Five
  rulings this epic already made"), `CLAUDE.md:89` ("records the five rulings") and
  `tests/test_governance_docs.py:90` / `:355` incremented **as found**; and a new test in
  `tests/test_governance_docs.py` asserts `GOVERNANCE.md`'s rulings sentence carries
  `_NUMBER_WORDS[len(_RULING_MARKERS)]`, the file's existing mechanism for the hashed-source and
  required-check counts (`:96-109`), so the count can never go stale again (round 5 — the
  by-value sweep's path set excludes `contract/` and `tests/` by construction). `tests/test_contract_metrics.py::
  test_dataclass_counters_and_their_models_carry_the_same_fields` (`:305`) and
  `::test_metrics_schema_is_fully_rendered` enforce the field. One counter, six reasons: the
  counter cannot separate a key-enable/rotation burst on a running fleet (`unsigned`, `bad_mac` on
  old entries) or a bound reduction (`oversize`) from tampering (`bad_mac` on fresh ones); the log line's reason token and key digest are the
  discriminator, and US-003 says so in as many words (a per-reason `/metrics` breakdown is
  recorded as a non-blocking question).
- Key material: the stripped environment value is used as UTF-8 bytes, **never base64-decoded**;
  the documented recipe (`head -c 32 /dev/urandom | base64`, ~44 characters) therefore yields a
  44-byte key. US-002 enforces the 32-byte floor at boot.
- Contract window (ruling 36 — copy exactly): append one `* ``1.3.0`` — …` docstring line to
  `pipeline/contract.py`'s `CONTRACT_VERSION` entry (read `:26-68` for the bullet shape) covering
  `cache.integrity_rejects` **and** the widened `storage_oversize_skips` semantics; `uv run python -m
  scripts.export_contract`; re-create `tests/golden/contract_1_3_0.json` through
  `tests/test_contract_schema.py::_SCHEMA_MODELS` (`:28`, the golden's producer); append the field
  to `_EXPECTED_ONE_THREE_ZERO_DIFF` in the same file; refresh the four anchor-quoting pages
  `tests/test_governance_docs.py::_ANCHOR_QUOTING_PAGES` names; `uv run python -m
  scripts.export_contract --check` green; `contract_1_2_0.json` unchanged.
- Rotation (ruling 32): `cache.py` and `retrieval_app.py` are not hashed; `pipeline/contract.py`
  **is**, and this story appends a docstring line to it, so this story rotates — measure by
  revert-and-reproduce and record at the five sites the repo's protocol names
  (`docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`,
  `kit_tools/docs/GOTCHAS.md`, `kit_tools/arch/CODE_ARCH.md`; `kit_tools/EXECUTION_LOG.md:266`
  records the practice).
- Tests (ruling 40): new `TestSignedValues` in `tests/test_cache.py` beside `TestContentCacheGetPut`
  (`:294`), driving `FakeStorage` directly and planting values through `FakeStorage.entries`
  (`tests/fakes.py:196-268`, `(payload_bytes, expiry)` against the fake's clock); parametrise the
  four envelope shapes, the five malformed strings, the fake-side oversize value and the planted
  `b""`; assert `storage.delete_calls` advances (spec 2 US-004 uses the same counters for the
  corrupt-entry case). The three Valkey-level cases run on the `_mock_valkey_client` /
  `_connected_valkey_storage` seam (`tests/test_cache.py:85`, `:96` — the file's stated home for
  "the failure modes that are Valkey's own"). The write-side skip is a further test asserting
  `storage_oversize_skips` advances, `integrity_rejects` does not, and `storage.entries` stays
  empty — with an ASCII payload and a non-ASCII payload whose character count is under the bound
  but whose byte count is over it. **Seam migration, named in full:** the two helper doubles above,
  plus the inline `AsyncMock()` Valkey clients in `tests/test_cache.py`'s reconnect tests that stub
  `.get` and depend on its return — `:593` (`side_effect=ConnectionError("dropped")` → the same
  side effect on `getrange`, so the drop still marks the cache disconnected), `:611` and `:734`
  (`return_value=None` → `return_value=b""`), `:667` (`return_value=stale.model_dump_json().encode()`
  → the same on `getrange`); the ping-only inline clients (`:549`, `:572`, `:625`, `:646`) need no
  edit — say so. Starting counts: `grep -c "\.get = AsyncMock" tests/test_cache.py` is 5 before and
  0 after; `grep -rcE 'five[ -](Valkey )?(operations|command)' cache.py tests/test_cache.py
  tests/test_app.py` sums to 2 before (`cache.py:358`, `tests/test_cache.py:86`) and 0 after.
  Three further tests (round 4): an oversize `put` for a key already holding a small valid envelope
  deletes it before skipping; `ContentCache(valkey_url=..., max_value_bytes=N)._storage` carries the
  same `max_value_bytes` as the cache, and so does the lifespan-built pair; `_attempt_connect` calls
  `aioredis.from_url` with `decode_responses=False` (patch `cache.aioredis` as `tests/test_app.py`'s
  Valkey tests do and read `call_args.kwargs`).

**Acceptance Criteria:**
- [ ] `ContentCache(hmac_key=..., max_value_bytes=...)` accepts `bytes | None` / `int`, defaults to
      `None` / `DEFAULT_CACHE_MAX_VALUE_BYTES`, and both reach the instance from the lifespan
      construction site (`retrieval_app.py:1288`, `settings.max_value_bytes`) as well as the
      convenience constructor and the parity fixture (defaults); the cache's bound and its
      `ValkeyStorage`'s bound are equal after construction through both paths; with `hmac_key=None` the stored
      value and every existing `tests/test_cache.py` test are unchanged apart from the named seam
      migration.
- [ ] With a key, `put` stores `v1.<64 hex chars>.<json>` and the MAC verifies as HMAC-SHA256 over
      `b"v1\0" + cache_key + b"\0" + payload`; `get` returns a `RetrievedContent` equal to what was
      put, and `integrity_rejects` is still 0 after the round trip.
- [ ] The four envelope shapes (unsigned; another key; flipped byte; relocated envelope) planted in
      `FakeStorage` each yield `get → None`, exactly one `delete` on the storage, and
      `integrity_rejects` advancing by one, with the reasons `unsigned` / `bad_mac` / `bad_mac` /
      `bad_mac`; a keyless cache reading a value that starts with `v1.` yields `get → None`, one
      delete, one increment and the reason `unexpected_envelope`; a planted `b""` yields `get →
      None` with no delete and no increment on any storage.
- [ ] Each of `b"\xff\xfe"`, `b"v1."`, `b"v1.abc"`, `b"v1." + b"g" * 64 + b".{}"` and `b"v2." +
      b"0" * 64 + b".{}"` yields `malformed_envelope` with exactly one delete and one increment, a
      planted `max_value_bytes + 1`-byte value on `FakeStorage` yields `oversize`, and no exception
      escapes `ContentCache.get` (`_unwrap` operates on bytes end to end).
- [ ] `ValkeyStorage.get` issues one `getrange(key, 0, max_value_bytes)` and no `get`; on the
      `_mock_valkey_client` seam a `max_value_bytes + 1`-byte return yields `None`, one client
      `delete`, `integrity_rejects` + 1 and the reason `oversize`; a `b""` return yields `None`,
      `storage_misses` + 1, `storage_hits` unchanged, no delete, no reject and no WARNING; a
      `ResponseError("WRONGTYPE …")` from `getrange` yields `None`, one client `delete`,
      `integrity_rejects` + 1 with reason `wrong_type`, `connected` still true and
      `operation_failures` unchanged; neither `storage_hits` nor `storage_misses` moves for
      `oversize` or `wrong_type`; both doubles' `getrange` stubs return `b""` for a miss.
- [ ] `_ValkeyClient` declares six methods, and neither its own docstring nor either Valkey
      double's docstring describes the surface as "five" (the corrected grep returns 0); the five
      inline `.get = AsyncMock` stubs in `tests/test_cache.py` are migrated to `getrange` as named;
      the `getrange` call site is positional.
- [ ] `put` never stores a value longer than `cache.max_value_bytes` **UTF-8 bytes**: the skip
      deletes any existing entry under that key first (a previously cached small value is gone after
      an oversize `put` for the same key), advances `storage_oversize_skips`, leaves
      `integrity_rejects` untouched and stores nothing, and the request is served uncached — asserted with an ASCII payload and with a non-ASCII payload
      that is under the bound in characters and over it in bytes.
- [ ] `cache.max_value_bytes` exists in `config.yaml`'s `cache:` block (default 4 MiB, range
      512 KiB – 8 MiB, `_bounded_int`), `cache_settings_from_config` boots and logs one
      `cache_bounds_inverted` WARNING (keys only) when it exceeds `cache.max_bytes` — a shipped-range
      `cache.max_bytes: 1048576` with the default `max_value_bytes` is a supported, warned start —
      it reaches `ValkeyStorage.__init__` and `ContentCache.__init__` from their construction sites,
      is registered in `KNOWN_CONFIG_KEYS`, is asserted by
      `test_the_shipped_config_yaml_pins_the_documented_defaults`, and its `docs/configuration.md`
      row states the `MAX_EXTRACTED_OUTPUT_BYTES`, `cache.max_bytes` (WARNING), container-memory
      and same-across-replicas relationships, the lowering consequence, and the forward
      cross-reference to spec 6's sizing section (written here; spec 6 closes it — not gated on that
      section existing yet).
- [ ] `ValkeyStorage.__init__` refuses a `VALKEY_URL` whose query carries `decode_responses`,
      `encoding`, `encoding_errors` or `protocol`: a lifespan start with `?decode_responses=1`
      raises `cache.CacheConfigurationError` (`pytest.raises`) after one WARNING
      `valkey_url_option_forbidden` naming the option, and neither `str(exc)` nor `caplog.text`
      carries the host, the URL or a password; the same for `?encoding=latin1` and `?protocol=3`;
      `?socket_timeout=90` boots; for a query-free URL a real client built without connecting has
      `connection_pool.connection_kwargs["decode_responses"] is False`;
      `ValkeyStorage._attempt_connect` still calls `aioredis.from_url` with `decode_responses=False`
      (asserted on `call_args.kwargs`), `ValkeyStorage.get` measures and parses bytes even when a
      double returns `str`, `_select_cache_storage`'s docstring names where the check lives, and
      `docs/configuration.md`'s `VALKEY_URL` row carries the upgrade note.
- [ ] Every rejection logs exactly one WARNING whose `getMessage()` contains
      `cache_integrity_reject`, one member of `CACHE_INTEGRITY_REASONS` and the `ret:` cache-key
      digest; the secret bytes, the raw value, the URL and the Valkey URL appear in no record
      (`caplog.text`); a Valkey miss logs nothing.
- [ ] `repr(cache)` and `str(cache)` do not contain the key bytes (regression guard; no `__repr__`
      is added).
- [ ] `CacheMetrics.integrity_rejects`, the `CacheMetricsResponse` field and the `/metrics` handler
      dict entry exist; the field-parity test passes; the `/metrics` body carries the key; the
      class docstring and the `storage_oversize_skips` description no longer say the counter is
      memory-only — `grep -c 'bound. Always 0 on Valkey' retrieval_app.py` returns 0 (1 today,
      `retrieval_app.py:603`; the `storage_evictions` description at `:597` keeps its own "Always 0
      on Valkey" because that counter **stays** memory-only, and the exported `contract/openapi.yaml`
      folds descriptions across lines, so a single-line grep on it sees neither — R43) and the
      `storage_oversize_skips` Field description no longer contains `in-memory storage`; the
      GOVERNANCE recorded ruling on the widened counter is written as its own `### (<letter>) `
      section with a `**Source:**` line, `_RULING_MARKERS` carries its marker, the four count words
      are incremented as found, and the new `_NUMBER_WORDS[len(_RULING_MARKERS)]` test passes.
- [ ] 1.3.0 window (ruling 36): docstring line appended (covering `integrity_rejects` and the
      widened `storage_oversize_skips`), contract regenerated, `tests/golden/contract_1_3_0.json`
      re-created via `_SCHEMA_MODELS`, the field appended to `_EXPECTED_ONE_THREE_ZERO_DIFF` (the
      description change is golden-only — R36 corrected), the
      four anchor-quoting pages refreshed, `uv run python -m scripts.export_contract --check` green,
      `tests/golden/contract_1_2_0.json` unchanged.
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
  pattern (`:196`) and `usable_brave_key`'s strip-and-return shape (`pipeline/search_providers/
  brave.py:114`; `brave_key_present` at `:81` is the boolean twin): `os.environ.get(CACHE_HMAC_KEY_ENV_VAR)`;
  strip `" \t\n"` (the `BRAVE_KEY_STRIP_CHARS` set, `:78`, which deliberately keeps `\r`); blank
  after strip → absent silently (the `FORAGE_CACHE_HMAC_KEY=` compose-renders-unset shape). The
  rules are stated here, not inherited: printable ASCII, no interior whitespace or control
  characters (a shape rule carried over from `brave_key_present`, whose rationale is header
  transport — say so in the docstring), and **at least 32 bytes as UTF-8**
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
  `cache_hmac_key_invalid`; never the value. That WARNING is the operator's only diagnostic for a
  container that exits at start, so it goes through the module logger to stderr before the raise
  (the Independent Test also asserts it on `caplog`; a note says which sink an operator reads).
- Hermeticity lands **here**, with the first test that depends on the variable's absence (round 3):
  add `FORAGE_CACHE_HMAC_KEY` to `tests/conftest.py::_CLEARED_ENV_VARS` (`:45`) and update the
  exact-set assertion in `tests/test_hermeticity.py::test_the_cleared_environment_is_the_expected_exact_set`
  (`:129-138`); the canary at `:141` then covers it. Without this, case (a) reads whatever the
  developer exported in their shell.
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
  module-scope `app.state` initialisation in the lifespan-less block (the module-scope `app.state`
  initialisation block, `retrieval_app.py:1379-1402`, which sits **before** the route handlers —
  `/health` at `:1444`, `/metrics` at `:1490`; round 5), on
  `_resolved_search_key_capabilities`'s pattern (`:313`). On the lifespan-less path
  `_resolved_cache_backend` re-reads `VALKEY_URL` (`:228-238`) while signing defaults to `False`,
  so a lifespan-less `/health` reports `cache_unauthenticated` whenever the backend resolves to
  Valkey — correct, because no cache object exists to be signing; state it in the docstring.
- `/health`: `DEGRADED_CACHE_UNAUTHENTICATED: DegradedReason = "cache_unauthenticated"` in
  `pipeline/contract.py`'s `DegradedReason` literal (`:82-100`, a response-validation gate) appended
  after `cache_unavailable` when the backend is Valkey and signing is not active;
  `tests/test_contract_errors.py::test_degraded_reasons_derive_from_one_source` (`:282`) is an
  exact set and gains the member in the same edit, and so does
  `::test_degraded_reasons_and_dict_vocabularies_are_documented` (`:643`), which pins the
  **generated** enum list (`health["degraded_reasons"]["items"]["enum"]`) to the two members and
  asserts `CAPABILITY_SEARCH_SANITIZATION` is in the `capabilities` description — the rewrite of
  that description must keep it (round 4, codebase fit). `CAPABILITY_CACHE_HMAC_KEY = "cache_hmac_key"`
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
  name; `tests/test_app.py:1501 test_health_names_the_backend_it_selected_for_this_start` runs the
  same real lifespan across three parametrisations and compares `data["degraded_reasons"]` against
  a parametrised `expected_reasons` (`:1528-1532`) — its `valkey-up-healthy` param
  (`_WORKING_VALKEY_URL`, expects `"healthy"` / `[]`, `:1481-1489`) gets the sentinel key so it
  stays healthy, and its `valkey-down-degraded` param (`:1491-1498`) expects
  `["cache_unavailable", "cache_unauthenticated"]`; the `Case N of 5` docstring series in the same
  file (`:1332`, `:1357`, `:1387` — the last reads `Cases 3-5 of 5`, plural, which the round-4
  single-wildcard anchor never counted) is renumbered to six cases with the new keyless sibling as
  its own case, and so are the two sites outside the series: the section header comment `# Five
  starts, one per configuration the operator can produce:` (`:1228`) with its five-row enumeration
  of exactly the configurations a sixth joins, and the docstring `All five starts keep the closed
  log vocabulary` (`:1424`); the widened anchored grep `grep -nE 'Cases? [0-9-]+ of 5|[Ff]ive
  starts' tests/test_app.py` returns 5 today (`:1228`, `:1332`, `:1357`, `:1387`, `:1424` — the
  round-4 `Case . of 5` anchor returned 2, not 3, and the naive `'of 5'` also catches
  `tests/test_app.py:179`'s "500ing") and 0 after; `tests/test_app.py:1510` / `:1540`'s `Cases 1-3
  of 4` / `Case 4 of 4` belong to `test_health_names_the_backend_it_selected_for_this_start`'s own
  series and stay (R43, corrected round 5); `kit_tools/arch/SECURITY.md:293` cites the second test by name and needs no change
  unless it is renamed. Starting count: `grep -n 'degraded_reasons"\]' tests/test_app.py` (14 hits
  today, which also catches the parametrised form that `== \[` misses).
- Boot WARNING when Valkey is configured and no key: `cache_hmac_key_missing — %s is unset; cached
  content is served unsigned (/health reports cache_unauthenticated)` naming the variable.
- `contract_smoke.py`'s healthy-mode docstring (`:44`, "and a reachable cache when `VALKEY_URL` is
  set") names `cache_unauthenticated` as a second reason a Valkey-backed container will not reach
  `healthy`; spec 8 US-003 runs the keyed configuration (ruling 30).
- Contract window (ruling 36 — copy exactly): one `* ``1.3.0`` — …` docstring line for the new
  `DegradedReason` member and the capability, `uv run python -m scripts.export_contract`, re-create
  `tests/golden/contract_1_3_0.json` via `_SCHEMA_MODELS` keeping US-001's line, append
  `cache_unauthenticated` (a new enum member) to `_EXPECTED_ONE_THREE_ZERO_DIFF` — `cache_hmac_key`
  is a key inside the `capabilities` dict, not a schema path, and is not appended (R36 corrected)
  — refresh the four
  anchor-quoting pages, `--check` green. `pipeline/contract.py` rotates — measure and record at the
  five protocol sites.

**Acceptance Criteria:**
- [ ] `retrieval_app.py` contains exactly one `os.environ.get(CACHE_HMAC_KEY_ENV_VAR)` call, inside
      `_resolve_cache_hmac_key()`, and the lifespan calls it once (`grep -c
      "_resolve_cache_hmac_key()" retrieval_app.py` returns 2); blank → absent silently.
- [ ] A value that is not printable ASCII, contains interior whitespace or control characters, or
      is fewer than 32 UTF-8 bytes → the lifespan raises `cache.CacheConfigurationError`
      (`pytest.raises` holds), `str(exc)` names
      `FORAGE_CACHE_HMAC_KEY` and does not contain the value, and exactly one WARNING carrying
      `cache_hmac_key_too_short` or `cache_hmac_key_invalid` is emitted; the refusal docstring
      states that the floor is length, not entropy, and names the CSPRNG recipe.
- [ ] `cache_unauthenticated` is a member of `pipeline/contract.py`'s `DegradedReason`, both
      exact-set tests in `tests/test_contract_errors.py` (`:282`, `:643`) include it and the `:643`
      capability-description assertion still holds, and it appears in
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
      `["cache_unavailable", "cache_unauthenticated"]`;
      `test_health_names_the_backend_it_selected_for_this_start`'s two Valkey params are updated as
      named; the `Case N of 5` series, the `Five starts` header enumeration and the `All five
      starts` docstring are renumbered to six (`grep -nE 'Cases? [0-9-]+ of 5|[Ff]ive starts'
      tests/test_app.py` returns nothing; 5 today) and the `of 4` series at `:1510` / `:1540` is
      untouched; `SECURITY.md:293`'s citation still resolves.
- [ ] `_CLEARED_ENV_VARS` contains `FORAGE_CACHE_HMAC_KEY` and the exact-set test in
      `tests/test_hermeticity.py` is updated in this story.
- [ ] Sentinel: the key value appears zero times in `/health`, `/metrics`, every log record and
      `repr(app.state.cache)` across the four starts.
- [ ] `contract_smoke.py`'s healthy-mode docstring names `cache_unauthenticated`.
- [ ] 1.3.0 window (ruling 36): docstring line appended, contract regenerated,
      `tests/golden/contract_1_3_0.json` re-created with US-001's line intact,
      `cache_unauthenticated` appended to `_EXPECTED_ONE_THREE_ZERO_DIFF` as a new enum member
      (`cache_hmac_key` is a dict key, not a schema path — R36 corrected), the four anchor-quoting
      pages refreshed,
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
refusal, and the rotation consequence; the by-value sweep (ruling 39, criterion 4) returns nothing; the two automated doc checks that do touch these pages (the anchor check over the four anchor-quoting pages, spec 3's config-key parity test) pass.

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
  into keyed → `healthy` and key-less → `degraded`, `cache_unauthenticated`), `:467-470` (the cache
  metrics rows gain `integrity_rejects`, and "Only the in-memory storage can move the last two"
  becomes "`storage_evictions` is memory-only; `storage_oversize_skips` counts Forage's own
  write-side refusals on both backends" — US-001 widened it), `:539` (the `degraded_reasons` row —
  the "two reasons" half only; `docs/configuration.md` carries no "two keys" sentence, its `/health`
  field table at `:536-545` has no `capabilities` row — round 5), the `### The `cache:` block`
  intro at `:445-449` ("Bounds for `InMemoryStorage`, the bounded in-process content-cache
  storage…" — the block now also bounds the Valkey read, so it stops framing itself as
  in-memory-only; the same reword `config.yaml`'s block comment gets in US-001), and the
  `cache.max_value_bytes` row (if US-001 has not already added it) with its four relationships and
  the same-across-replicas / lowering sentence.
- **The cache bounds are enumerated as a closed pair on six further pages** (round 5, codebase
  fit), each of which gains `cache.max_value_bytes`: `kit_tools/docs/ENV_REFERENCE.md:102-103` (a
  third row in the `config.yaml` key table — distinct from the `### Runtime service` table the
  variable's row joins), `kit_tools/arch/SERVICE_MAP.md:133` (the cache's Configuration row, both
  keys with their ranges) and `:269-270`, `kit_tools/docs/LOCAL_DEV.md:132` and `:267`, and
  `README.md:119` ("Bounded by | `cache.max_entries` / `cache.max_bytes`"); the sweep's new
  patterns cover them.
- `kit_tools/docs/ENV_REFERENCE.md` `### Runtime service` table (seven columns, the
  `FORAGE_BRAVE_API_KEY` row at `:43` is the shape) gains the row with `Secret: **yes**` and read
  site `retrieval_app._resolve_cache_hmac_key(), start`; the `VALKEY_URL` row notes the four
  refused query options.
- `kit_tools/docs/MONITORING.md`: the `capabilities` row (`:63`), the `degraded_reasons` row (`:68`)
  and "The two reasons are the complete set" (`:70`), the body-shape block (`:81`), the runbook item
  (`:99`), the cache counter table (`:170-177`) with a row for `cache.integrity_rejects`, the
  `### Startup lines you may see` table (`:229`) with all five markers (`cache_hmac_key_missing`,
  `cache_hmac_key_unused`, `cache_hmac_key_too_short`, `cache_hmac_key_invalid` — the last two are
  what an operator whose container will not start is decoding — and `cache_bounds_inverted`), the
  `storage_oversize_skips` row (`:177`, "`InMemoryStorage` only; always 0 on Valkey" → both
  backends, Forage's own write-side refusals at `cache.max_value_bytes`, plus `InMemoryStorage`'s
  own `cache.max_bytes` check),
  and `### Closed vocabularies` → `cache.py` (`:242`, "exactly one of `connect_failed`, …") extended
  with the six `cache_integrity_reject` reasons. Incident guidance (round 4 — two scenarios,
  stated separately, because the round-3 text told operators to shrug off the one signal that can
  only mean a foreign writer): **(a) upgrading to the 1.3.0 image** — US-001 and US-002 append to
  `pipeline/contract.py`, a `_REVISION_SOURCES` member, so `sanitizer_revision` rotates,
  `cache_policy_fingerprint` changes and **every cache key changes**; legacy bare-JSON entries are
  orphaned under keys the new code never asks for, not rejected; there is **no first-enable
  burst** on this path — expect a cold cache and a near-zero `unsigned` count, and after the
  upgrade an `unsigned` or `bad_mac` reject is a foreign writer; **(b) enabling or rotating
  `FORAGE_CACHE_HMAC_KEY` on an already-running 1.3.0 fleet with no code change** — the keys do
  not change, so a one-time burst bounded by the working set follows (`unsigned` on first enable,
  `bad_mac` on rotation), and the stop-all rotation procedure is what bounds it. In either case a
  sustained non-zero `integrity_rejects` rate with a key set and no rotation in flight means
  something other than Forage is writing to the Valkey — treat it as a security event (rotate the
  key, audit Valkey ACL and network placement), not a cache-health blip; **the counter cannot
  separate the cases by itself** — the reason token and the `ret:` key digest in the
  `cache_integrity_reject` log line are the discriminator (`unsigned` spread across many keys
  right after a key enable on a running fleet = migration; `bad_mac` on fresh keys = tampering;
  `wrong_type` = someone planted a non-string; **`oversize` = a value larger than
  `cache.max_value_bytes`** — a burst bounded by the working set follows a bound reduction, a
  sustained rate means a replica with a mismatched bound, and it is the weakest of the six as
  tamper evidence; the security-event sentence names `unsigned`, `bad_mac` and `wrong_type` only);
  a Forage-authored page over the bound is a `storage_oversize_skips` increment at write time,
  never an integrity reject, so a new write is not this signal; and **a
  flat `integrity_rejects` is not evidence of an unpoisoned cache when key compromise is
  suspected** — a forged envelope under the real key verifies and counts nothing (the fourth
  residual in SECURITY.md). The digest is described as an opaque, credential-free correlation
  handle, not as one that conceals the URL (it is confirmable against a guessed URL). Wording for
  `cache_unauthenticated`: cached `/retrieve` content cannot be proven to be Forage's own and is
  served without re-sanitization; on a shared Valkey treat it as an open cache-poisoning path
  until a key is set (not "a configuration signal").
- `kit_tools/docs/TROUBLESHOOTING.md`: `:56` ("Exactly two values exist"), `:60` and `:121` (the
  Secret-free check row's "the same three patterns" → four, naming the variable — round 4); a
  narrative
  `### cache_unauthenticated: Valkey configured without a signing key` section beside the
  `cache_unavailable` one (`:345`); and a `### The container exits at start: FORAGE_CACHE_HMAC_KEY
  refused` section — what the operator sees (exit, no `/health`), which marker to grep for, the
  32-byte floor, the strip rules, the recipe. This file has no log-marker table.
- `kit_tools/docs/API_GUIDE.md:105` and `:109` (the "two keys" capability sentence).
- `kit_tools/arch/SERVICE_MAP.md`: `:108` ("`DegradedReason` … is exactly `promptguard_unavailable`
  and `cache_unavailable`"), the `/health` signal row (`:135`), the Valkey-mode table's `set and
  reachable` row (`:146`, which splits keyed → `healthy` / key-less → `degraded`; `:147-149` are the
  unreachable / unparseable / empty-string rows and do not change — round 5), and `:132` (the
  client/protocol row — the prose twin of the two in-code "five operations" strings US-001 tracks:
  the command surface becomes `ping`, `getrange`, `set(ex=)`, `delete`, `aclose`, and the quoted
  `from_url(url, socket_connect_timeout=2.0, socket_timeout=2.0)` gains `decode_responses=False`
  plus the refused query options); `kit_tools/arch/CODE_ARCH.md:198` (the exhaustive parenthetical).
- `README.md:115` (the cache-mode matrix's "`/health` when the cache is fine" row splits keyed /
  key-less) and the configuration section's mention of the variable in the same sentence as
  `VALKEY_URL`; `kit_tools/docs/DEPLOYMENT.md:100` ("The history grep uses the same three patterns as" → four,
  round 4), `:220` and the runnable block at `:239-241`
  (`--expect-status healthy` on a Valkey-backed container now needs the key in the env file) plus
  the variable beside `VALKEY_URL`.
- `kit_tools/arch/SECURITY.md`: a `FORAGE_CACHE_HMAC_KEY` row in the Secrets inventory table
  (`:216-224`) stating the CSPRNG requirement; follow and cite the four steps of `### Adding a new
  secret` (`:248-253`); `:233`'s "three patterns" becomes four; `:246`'s Repository-hygiene bullet
  listing `tests/conftest.py`'s cleared variables (already two names behind) is brought up to the
  full `_CLEARED_ENV_VARS` set including this key; `:291`'s `degraded_reasons` enumeration gains
  the third; a new cache-poisoning entry (there is none today — `grep -i poison` is empty)
  stating: with an **uncompromised, CSPRNG-generated** key, integrity and authenticity of cached
  values are assured; **four** residuals remain — availability (an attacker can still delete or
  overwrite, forcing misses and outbound fetches plus classifier inference, **and log volume**: every
  rejection logs one un-rate-limited WARNING, so a writer who can `SET` can drive one
  attacker-chosen line per request for as long as they like; the concurrency and
  latency bounds of spec 6 are the control that bounds both — spec 6 is later in the epic, so until
  it lands the residual is stated as unbounded — round 5), confidentiality, replay (a captured
  valid envelope restored under the same key pins one Forage-authored snapshot; the signed
  `retrieved_at` plus the TTL check bound the window to `cache_ttl_hours`, which is a reason not to
  raise that value casually on a shared Valkey), and **key compromise** (a leaked or weak key lets
  an adversary forge envelopes that verify for any computable cache key with zero
  `integrity_rejects`; the counter cannot see it; the response is the documented stop-all rotation
  from a fresh CSPRNG value, which invalidates every entry — the envelope carries no key id, so no
  narrower revocation exists, recorded as an open question); without the key the poisoning path
  stands and `/health` says so; and that the posture is advertised on an unauthenticated `/health`
  because honest health outranks obscurity and the same fact is observable by anyone who can write
  to the cache.
- `kit_tools/docs/CI_CD.md:217` and `:310` ("three patterns"), `:552` and `kit_tools/docs/GOTCHAS.md:555`
  (the enumerated three-pattern set) become four with `FORAGE_CACHE_HMAC_KEY` named (US-004 owns the
  `ci.yml` comment; these are the prose consumers).
- `kit_tools/arch/patterns/LOGGING.md`: the `### cache.py` table and its "Exactly three strings"
  sentence (`:80` area) gain the six integrity reasons; refresh the three `cache.py:NNN-NNN`
  citations in that section (`_closed_vocabulary_reason`, `_attempt_connect`, `_mark_disconnected`)
  after US-001 shifted them; the `cache` Logger Inventory row; the four boot markers.
- `kit_tools/PRODUCT_VISION.md` Success Criteria (round 5 — repointed at the clauses that actually
  go stale): `:63` ("Key-less floor") needs **no edit** — it is already scoped to the paid search
  key and to the default path, and this spec adds no required secret and does not degrade the
  memory-backed default; `:65` ("Fails loud, never silent"): the clause that goes stale is the
  parenthetical enumeration `(weights absent, cache unreachable)`, which becomes two-of-three the
  moment `cache_unauthenticated` lands — it is extended (`cache unreachable or unsigned`), while
  "a missing paid key is a supported mode" is already scoped to *paid* and stays; `:173` ("a
  key-less deployment is a first-class supported mode") is scoped — key-less on the memory-backed
  default path is first-class, key-less on Valkey is `degraded` with a named reason (none of the
  sweep's patterns would have caught that line).
- **The vision edit is an owner gate, not a note.** This story does **not** edit
  `kit_tools/PRODUCT_VISION.md` itself: it writes the exact replacement text for `:65` and `:173`
  into its Implementation Notes and stops; the owner applies the two lines (the pattern of
  `feature-hardening-release` US-003 / US-005), so an autonomous lane never rewrites the Success
  Criteria table and then records that it did (round 5). The by-value sweep's path set excludes
  the file by construction.
- Doc-only story: no code; the by-value sweep in criterion 4 is the gate — the only automated doc
  checks that touch these pages are `tests/test_governance_docs.py`'s anchor check over the four
  anchor-quoting pages and spec 3 US-003's `KNOWN_CONFIG_KEYS` parity test over
  `docs/configuration.md`, so `uv run pytest` will **not** catch a missed page (round 5); nothing
  rotates.

**Acceptance Criteria:**
- [ ] `docs/configuration.md` carries the runtime row and the credential subsection (generation
      recipe, CSPRNG-not-passphrase, UTF-8-never-decoded, 32-byte floor, `/health` behaviour, boot
      refusal, stop-all-replicas rotation) and the split Valkey row in the backend-selection table;
      `kit_tools/docs/ENV_REFERENCE.md` carries the seven-column row.
- [ ] `kit_tools/docs/MONITORING.md` names three capability keys, three degraded reasons (row, body
      shape, runbook), the `cache.integrity_rejects` counter row with the two-scenario guidance
      (upgrade: cold cache, no burst; key enable or rotation on a running fleet: a bounded burst),
      the `oversize` discriminator entry, the "the log line is the discriminator" sentence and the
      "a flat counter is not evidence under suspected key compromise" sentence, the corrected
      `storage_oversize_skips` row naming the threshold, the five startup markers, and the extended
      `cache.py` closed vocabulary (six reasons); the `cache_unauthenticated` wording states the
      consequence.
- [ ] `kit_tools/docs/TROUBLESHOOTING.md` and `kit_tools/docs/API_GUIDE.md` no longer say two values /
      two keys; TROUBLESHOOTING has both new sections; TROUBLESHOOTING `:121` and DEPLOYMENT `:100`
      say four patterns and name the variable.
- [ ] `kit_tools/arch/SECURITY.md` has the Secrets-inventory row, the four-pattern sentence, the
      corrected conftest cleared-variable bullet, the three-reason enumeration, and the
      cache-poisoning entry with four residuals (key compromise included), the spec 6
      cross-reference and the disclosure trade-off; `kit_tools/arch/patterns/LOGGING.md` names the
      six reasons, the four boot markers and refreshed `cache.py` citations;
      `kit_tools/arch/SERVICE_MAP.md`, `kit_tools/arch/CODE_ARCH.md`, `README.md`,
      `kit_tools/docs/DEPLOYMENT.md`, `kit_tools/docs/CI_CD.md`, `kit_tools/docs/GOTCHAS.md`,
      `kit_tools/docs/ENV_REFERENCE.md:102-103`, `kit_tools/docs/LOCAL_DEV.md:132` / `:267`,
      `README.md:119`, `kit_tools/arch/SERVICE_MAP.md:132` / `:133` / `:146` / `:269-270` and
      `docs/configuration.md:445-449` carry the named edits; `kit_tools/PRODUCT_VISION.md` is **not**
      edited by the story — the replacement text for `:65` and `:173` is in Implementation Notes
      and the edit is held for the owner (owner gate); SECURITY.md's availability residual names log
      volume.
- [ ] By-value sweep (ruling 39), run as one command at the story's start (record the count) and at
      its end: `grep -rn -iE -e 'two keys' -e 'the two reasons' -e 'exactly two values' -e 'exactly
      one of .connect_failed' -e 'exactly three strings' -e 'three patterns' -e 'is exactly
      .promptguard_unavailable' -e 'Always 0 on Valkey' -e 'Only the in-memory storage can move' -e
      'in-memory storage refused' -e 'InMemoryStorage. only' -e 'Set and reachable' -e 'when the
      cache is fine' -e 'Bounds for .InMemoryStorage' -e 'max_entries. / .cache.max_bytes' -e
      'ping., .get., .set' docs kit_tools/docs kit_tools/arch README.md .github/workflows/ci.yml
      contract/GOVERNANCE.md CLAUDE.md` returns **nothing** at the end (22 hits at the start,
      measured in round 5 — the round-4 seventeen plus the five the three new patterns add:
      `docs/configuration.md:447`, `kit_tools/docs/LOCAL_DEV.md:132`, `:267`, `README.md:119`,
      `kit_tools/arch/SERVICE_MAP.md:132`, whose `ping., .get., .set` pattern stops matching once
      `get` becomes `getrange`; the two added paths contribute zero hits today and exist so the
      sweep covers the document the recorded ruling lands in — the rulings count word itself is
      gated by US-001's `_NUMBER_WORDS` test, not by a grep; every hit is inside this story's named
      edit list; the path set excludes `kit_tools/specs/`, `kit_tools/.seed_cache/`,
      `kit_tools/AUDIT_FINDINGS.md`, `kit_tools/PRODUCT_VISION.md` and the `.validate_epic_*.json`
      artifacts by construction — R43; a hit that is genuinely outside the vocabularies is listed in
      Implementation Notes with the reason it stays);
      `grep -rn FORAGE_CACHE_HMAC_KEY README.md kit_tools/docs/DEPLOYMENT.md` each return at least
      one line.
- [ ] Full test suite passes (`uv run pytest`).

### US-004: Distribution fan-out — compose on-switch, CI secret grep, leak sentinels

**Priority:** P1

**Description:** As an operator and as the maintainer of the release lane, I want the documented
full-stack compose fragment able to carry the key, the test environment to clear it, CI's secret
grep to know its name, and a sentinel suite proving the value never leaks — so the feature has a
supported switch and the same guards every other runtime credential has.

**Independent Test:** `docker compose -f compose/full.yml config --env-file /dev/null` with a
complete placeholder set (`HF_TOKEN`, `SEARXNG_SECRET`, `FORAGE_BRAVE_API_KEY`,
`FORAGE_CACHE_HMAC_KEY`, `VALKEY_URL`) from a scratch project directory renders the bare-name
passthrough on the `forage` service and `compose/minimal.yml` does not carry the name
(`tests/test_compose_fragments.py` asserts both); `tests/test_ci_workflow.py` finds the name in both
`ci.yml` grep copies and `grep -n 'three patterns' .github/workflows/ci.yml` returns nothing; a
signed `put`, a rejected `get`, a Valkey connect failure, a `/retrieve` 422 raised while the key is
set, and a `/health` + `/metrics` read all leave the sentinel absent from `caplog.text`, every body
and `repr(app.state.cache)`; the fixture-tree walk finds the sentinel **value** nowhere under
`tests/fixtures/` and the variable **name** nowhere under `tests/fixtures/` except
`tests/fixtures/contract/` (the generated contract twin, which US-002's capability description
legitimately names — `scripts/export_contract.py:263-270` writes it on every export).

**Implementation Hints:**
- The on-switch: `compose/full.yml` `environment:` gains `- FORAGE_CACHE_HMAC_KEY` as a bare name
  with a comment on the `FORAGE_BRAVE_API_KEY` pattern (credential → `compose/.env`, never inline)
  and a note that a healthy status now needs it; the header quickstart recipe (`:11-16`, the
  `{ echo HF_TOKEN=...; echo SEARXNG_SECRET=...; } > .env` lines) gains a
  `FORAGE_CACHE_HMAC_KEY=$(head -c 32 /dev/urandom | base64)` line — a generated value, never a
  literal and never an empty assignment, which US-002 treats as absent and would leave the
  documented happy path `degraded` (round 4) — so the documented happy path still reaches `healthy`
  (`feature-hardening-release` US-004 adds the *upgrade* sentence to the same files — different
  edit, no collision). `compose/minimal.yml` is Valkey-free (`TestMinimalIsGenuinelyValkeyFree`,
  `tests/test_compose_fragments.py:553`) and must not carry it; extend
  `TestSearchProviderPassthrough`'s idiom (`:533`) for `full` only. Any render an implementer
  records is a placeholder-only excerpt per ruling 33 — `env -i` still loads `compose/.env`, so use
  `--env-file /dev/null` and a scratch directory.
- Hermeticity moved to US-002 (round 3): the `_CLEARED_ENV_VARS` line lands with the first test
  that depends on the variable's absence; this story only relies on it.
- CI's secret grep (the Brave precedent's CI half): add `FORAGE_CACHE_HMAC_KEY` to
  `tests/test_ci_workflow.py::_REQUIRED_GREP_PATTERNS` (`:1072`) and to both copies in
  `.github/workflows/ci.yml` (the `secret-grep` heredoc and the publish config grep at `:998`); the
  comment block at `ci.yml:532-539` gains a `FORAGE_CACHE_HMAC_KEY` sentence on the Brave one's
  name-only pattern and "the same three patterns" (`:539`) becomes four —
  `feature-hardening-release.md` watches that comment at the `v1.2.0` cut; the prose consumers
  (`kit_tools/arch/SECURITY.md:233`, `kit_tools/docs/CI_CD.md:217`, `:310`, `:552`,
  `kit_tools/docs/GOTCHAS.md:555`) are US-003's.
- Key-never-leaks, on `tests/test_brave_provider.py::TestKeyNeverLeaks` (`:1478`): drive a signed
  put, a rejected get, a Valkey connect failure (`_valkey_double` raising on `ping`), a `/retrieve`
  refusal raised while the key is set — a blocked-domain or invalid-URL 422 from
  `pipeline/orchestrator.py:268-284` (a cache outage never produces a `/retrieve` error body:
  `cache.py:496-537` swallow every storage exception and `orchestrator.py:286-304` continues
  uncached), and a `/health` + `/metrics` read with the sentinel; assert it is absent from
  `caplog.text` (all loggers), every body, and `repr(app.state.cache)`. Fixture-tree guard: a
  **new** walk in `tests/test_cache.py` on the shape of
  `tests/test_brave_provider.py::TestFixtureCarriesNoSecret` (`:95-117`, `_FIXTURES_DIR.rglob`;
  cite it in the docstring) asserting the sentinel value appears in no file under
  `tests/fixtures/`, and the literal `FORAGE_CACHE_HMAC_KEY` in no file under `tests/fixtures/`
  **excluding `tests/fixtures/contract/`** (generated contract prose names the variable by design;
  it is not a credential) — do not touch `tests/test_brave_provider.py:80 _AUTH_HEADER_NAMES`, a
  header-name tuple scoped to Brave fixtures.
- Nothing here moves the document or a hashed file: no window block, no rotation.

**Acceptance Criteria:**
- [ ] `compose/full.yml` passes `FORAGE_CACHE_HMAC_KEY` through as a bare name with the comment and
      its header recipe includes a `FORAGE_CACHE_HMAC_KEY=$(…)` line that generates the value (the
      test asserts the line is not an empty assignment); `compose/minimal.yml` does not
      carry the name; `tests/test_compose_fragments.py` asserts all three; no rendered config or
      env value is recorded anywhere (ruling 33).
- [ ] `_REQUIRED_GREP_PATTERNS` and both `ci.yml` grep copies carry the name, the `ci.yml:532-539`
      comment names it and says four patterns (`grep -n 'three patterns' .github/workflows/ci.yml`
      returns nothing), and `tests/test_ci_workflow.py` passes.
- [ ] Sentinel suite: the key value appears zero times in `/health`, `/metrics`, a `/retrieve` 422
      body raised while the key is configured, every log record across the five drives, and
      `repr(app.state.cache)`; the fixture-tree walk finds the sentinel value nowhere under
      `tests/fixtures/` and the variable name nowhere under `tests/fixtures/` outside
      `tests/fixtures/contract/`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- Key configured on an already-running 1.3.0 fleet, storage holds a bare-JSON value under a current
  key → `unsigned` reject, delete, miss; the next `put` re-signs. Upgrading to the 1.3.0 image →
  `sanitizer_revision` rotates and every cache key changes, so legacy entries are orphaned, never
  read, never rejected (no burst). Key rotated (single process) → every old entry is `bad_mac`
  once, then gone. US-001, US-003.
- `cache.max_value_bytes` lowered (or one replica carrying a lower value) → Forage's own past writes
  between the two bounds come back as `oversize` rejects (deleted, counted) — bounded by the working
  set after a lowering, sustained while a replica's bound differs; not tampering. US-001, US-003.
- `put` of a value over the bound for a key that already holds a small valid envelope → the old
  entry is deleted first, then the skip; the stale value is never served. US-001.
- `cache.max_bytes` (in-memory total) below `cache.max_value_bytes` — a config that is legal today →
  boots with one `cache_bounds_inverted` WARNING; on the memory backend the storage's own
  `max_bytes` check is the effective per-entry bound. US-001.
- `VALKEY_URL` carrying `?decode_responses=1`, `?encoding=…`, `?encoding_errors=…` or `?protocol=…`
  → boot refused with `CacheConfigurationError` and one `valkey_url_option_forbidden` WARNING
  naming the option, never the URL (redis-py lets the query string beat every kwarg, so the option
  cannot be pinned — only refused); `?socket_timeout=…` is honoured as operator tuning. US-001.
- `compose/full.yml`'s header recipe with an empty `FORAGE_CACHE_HMAC_KEY=` → absent, `degraded`;
  the shipped recipe generates the value. US-004.
- Two replicas over one Valkey with different keys (a rolling rotation), or a mixed keyed/keyless
  fleet → each deletes the other's entries for as long as both run; the cache never warms; the docs
  say stop-all-then-rotate. US-003.
- A valid envelope for URL A copied under URL B's key → `bad_mac` (the MAC binds the key). US-001.
- No key configured, storage holds a `v1.` envelope → `unexpected_envelope`, delete, miss; never
  parsed. US-001.
- Non-UTF-8 bytes, a bare `v1.`, a `v1.` with one dot, a 64-character non-hex MAC, or a `v2.` tag →
  `malformed_envelope`; nothing raises. US-001.
- A Valkey miss → `GETRANGE` returns `b""` → a miss (`storage_misses` + 1, no delete, no reject, no
  WARNING); an adversary's `SET key ""` is indistinguishable from a miss and is treated as one
  (costs one refetch). An empty value read from any other storage is likewise a miss. US-001.
- A planted non-string type (`LPUSH ret:<digest> x`) → `WRONGTYPE` → `wrong_type` reject, `DEL`,
  counted; the connection stays up and `operation_failures` does not move. US-001.
- Value longer than `cache.max_value_bytes` on Valkey → one `GETRANGE` returns the bound plus one
  byte → `oversize`, `DEL`, never allocated beyond the bound on that read; a writer racing the read
  cannot widen it because the read is one command. US-001.
- Forage's own serialised value longer than `cache.max_value_bytes` **UTF-8 bytes** (including a
  non-ASCII page that is under the bound in characters) → not written, `storage_oversize_skips` + 1,
  `integrity_rejects` unchanged, request served uncached. US-001.
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
- A compromised or leaked key → forged envelopes verify, `integrity_rejects` stays flat; the only
  response is the fleet-wide stop-all rotation from a fresh CSPRNG value (fourth residual). US-003.
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
- Deleting the existing entry on `ContentCache.put`'s `_NO_CACHE_TIERS` early return
  (`cache.py:847`): a page cached as `STANDARD` that later classifies `UNTRUSTED` or `BLOCKED`
  keeps serving its correctly signed envelope until its TTL — pre-existing, outside the oversize
  branch this spec fixes, and recorded as an open question (round 5).
- Rate-limiting the `cache_integrity_reject` WARNING (the request rate bounds it and spec 6's
  admission bounds that; a non-blocking open question — round 5).

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
  bounded `GETRANGE` behind it and maps an empty return to a miss; `InMemoryStorage` is untouched;
  `_ValkeyClient` widens by one method as a deliberate act.
- `VALKEY_URL` is a wider configuration surface than host/port/db/password because redis-py forwards
  unknown query arguments to the connection constructor **and lets them beat explicit kwargs**
  (`kwargs.update(url_options)` in `ConnectionPool.from_url` — verified on redis 8.1.0), which is
  why the byte mode is a URL rule enforced at construction rather than a kwarg.
- `CacheMetrics` is mirrored on `/metrics` under `extra="forbid"` with exact field parity enforced
  by test (verified: `retrieval_app.py:568`, `tests/test_contract_metrics.py:305`).
- No healthcheck ships in this repo's `Dockerfile` or compose fragments today
  (`kit_tools/arch/SERVICE_MAP.md:74`); the consumer's compose check, and the liveness check spec 6
  adds, read only the HTTP status, which this spec never changes.

## Technical Considerations

- **Rotation ledger (ruling 32).** US-001 and US-002 each append a `CONTRACT_VERSION` docstring
  line, so both rotate `sanitizer_revision` through `pipeline/contract.py`; `cache.py`,
  `retrieval_app.py`, `models.py`, the compose fragments, `ci.yml` and the tests are not hashed;
  US-003 and US-004 rotate nothing. Record each rotation at the five protocol sites. **The rotation
  is also the invalidation** (round 4): because every cache key changes, every pre-epic entry is
  orphaned at upgrade — the content cache cold-starts once for the epic, an operational consequence
  US-003's runbook states, not only a ledger entry.
- **`storage_oversize_skips` has two producers** after US-001: `ContentCache.put` at
  `cache.max_value_bytes` (reachable through the cache on both backends) and `InMemoryStorage.set`
  at `cache.max_bytes` (reachable through the cache only under an inverted configuration, and from
  the direct-drive tests); the documentation names the threshold each fires at.
- **Generated files at merge.** Every window story in this epic regenerates `contract/openapi.yaml`,
  re-derives `contract/openapi.yaml.sha256` and re-creates `tests/golden/contract_1_3_0.json`. On
  any merge that conflicts in one of them, take neither side: re-run `uv run python -m
  scripts.export_contract` and re-create the golden from `_SCHEMA_MODELS` on the merged tree, then
  `--check`. A hand-resolved `.sha256` is a silently wrong trust anchor (CLAUDE.md invariant 4).
- **MAC input.** `b"v1\0" + cache_key + b"\0" + payload`: the version tag domain-separates future
  envelope formats, the cache key (`ret:<sha256hex>`, a fixed alphabet, so the separator is
  unambiguous) binds the value to its URL / mode / policy fingerprint, and the payload is the exact
  `model_dump_json()` bytes, so canonicalisation differences cannot create false rejects.
- **Ordering in `get`:** zero-TTL purge → bounded `GETRANGE` read (Valkey; empty → miss;
  `WRONGTYPE` → `wrong_type` reject) → `_unwrap` (length defence, prefix, split, hex check,
  `compare_digest`) → parse guard (spec 2 US-004) → tz/TTL checks → return. Each rejection path
  deletes, counts, logs the reason and the key digest, and returns `None`; a miss counts only
  `storage_misses`; nothing in the path raises on attacker-controlled bytes.
- **Round trips.** `GETRANGE` replaces `GET` one-for-one, so a Valkey-backed cache hit costs the
  same single round trip as today with or without a key; spec 6's latency envelope is unaffected.
- **Both sides bounded, in bytes.** The write-side skip keeps `integrity_rejects` meaning "someone
  other than Forage wrote this"; the default 4 MiB exceeds anything Forage can author at the 2 MiB
  extraction ceiling, the floor of 512 KiB is a memory knob an operator must reconcile with
  `full`-mode content sizes, and the 8 MiB ceiling keeps the aggregate survivable (the config row
  says so). **The read bound is per read**: peak cache-read allocation is `max_value_bytes ×
  in-flight /retrieve requests`, cache reads sit under no concurrency bound (the classification
  semaphore covers stage 3, which a hit short-circuits), and `cache.py:214-222` documents ~128 MiB
  of headroom — so spec 6's memory rule carries a `+ cache.max_value_bytes` cache-read term
  (recorded on its own page in round 5) and the config row forward-references it. The bound is a
  per-deployment input, not a
  cache-key input: keep it equal across replicas; lowering it is a self-inflicted `oversize` burst
  the runbook names (round 4).
- **Constant-time comparison** via `hmac.compare_digest` on bytes; the reject log carries only the
  closed reason token and the key digest (`kit_tools/arch/patterns/LOGGING.md`; GOTCHAS "Nothing
  configures logging" — the token goes in the message).
- **Health truthfulness** (CLAUDE.md invariant 5): the new reason is appended, never used to change
  the HTTP status; a configured-but-unusable key on the memory backend is said once at boot. The
  posture is advertised on an unauthenticated `/health` deliberately (recorded in SECURITY.md).
- **Key rotation is fleet-wide** and cold; the docs say so.
- **`VALKEY_URL` query options (round 5).** `from_url` applies URL options over kwargs, so the
  four reply-shaping options (`decode_responses`, `encoding`, `encoding_errors`, `protocol`) are
  refused at `ValkeyStorage.__init__` with a closed marker rather than overridden; the check reads
  query keys only and lives in `cache.py`, the URL's owner, so `_select_cache_storage`'s no-parse
  rule and the lifespan-less `/health` path are untouched. `socket_timeout` and
  `socket_connect_timeout` remain operator-overridable through the URL on purpose — they are
  tuning, and the `asyncio.timeout` connect deadline is independent of them.
- **The reject WARNING is not rate-limited** (round 5): one line per rejected read is the loud
  posture; its volume is bounded by the request rate, which spec 6's admission bounds. SECURITY.md
  names log volume in the availability residual; a rate limit is an open question.

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

### US-001 — signed, key-bound and byte-bounded cache values (2026-09-22)

- `ContentCache` signs exact JSON bytes with the version/key/payload HMAC and
  verifies before parsing; keyless JSON remains supported. Writes enforce
  UTF-8 byte size and delete superseded entries before skipping. Both backends
  share the policy; `InMemoryStorage` was not changed.
- Valkey reads are one positional `getrange(key, 0, bound)`. Empty replies are
  misses; oversize and `WRONGTYPE` replies delete/count/log without moving
  storage hit/miss counters. Other operation failures still disconnect.
  Query reply-shaping options refuse construction with a key-only diagnostic;
  socket timeout tuning and malformed-URL degradation remain supported.
- All five `.get = AsyncMock` stubs in `test_cache.py` migrated, including the
  reconnect return/side-effect seams; the app helper migrated too. Ping-only
  inline clients need no edit. FakeStorage drives planted and arbitrary bytes;
  real-memory parity, real-lifespan wiring, lazy redis client configuration,
  byte boundaries and actual `/retrieve` uncached responses are covered.
- **Measured spec corrections:** the prefix is 68 bytes, not 67. The code
  measures it rather than trusting either number. A 2 MiB body dominated by
  JSON escapes can exceed 4 MiB after serialization and metadata, so the
  stated default cannot guarantee caching every full page. A regression covers
  ordinary and inflated 2 MiB bodies; inflated values are served uncached
  without an integrity increment. The specified default/range are unchanged,
  and operator docs state the real relationship.
- **Golden producer accommodation:** `_SCHEMA_MODELS` did not include cache
  metrics at all. Added `CacheMetricsResponse` to the held golden, with an
  explicit eight-field 1.2.0 baseline verified against the published OpenAPI
  at `06b01b145d592787b32eb0425061fa8c1914d31f`. The diff sweep now records
  both this counter and the previously unpinned `corrupt_entries`; every
  historical golden is byte-identical. OpenAPI, anchor and drift twin were
  generated and all four anchor pages refreshed.
- GOVERNANCE ruling (j) records the producer widening; eleven rulings are
  now counted and the actual headings are checked against `_RULING_MARKERS`.
  Corrected ruling (i)'s source link to the prerequisite spec's archive
  location after the governance gate exposed that stale reference.
- Thirty-first sanitizer rotation: `e00049c4…7ed5c` -> `aa288bc5…5b39c`.
  Only `pipeline/contract.py` moves among the nine hashed sources. Read-only
  whole-file reversal against clean `b79504d` reproduces the prior value under
  both default and shipped config. All five protocol sites record it.
  This changes no text-sanitization algorithm and orphans old cache keys.
- Validation: **1,164 related tests pass** in one process (cache, app,
  contract metrics/schema/export/errors, governance, sanitizer revision and
  orchestrator). Repository Ruff lint/format, strict Pyright, exporter check,
  whitespace, protocol/migration and historical-artifact checks pass.
  Six non-failing upstream/socket-guard warnings remain unsuppressed.
  **Full `uv run pytest` is deferred**, as this implementer invocation
  explicitly prohibits it; no full-suite acceptance or owner gate is claimed.
  Environment-key wiring and its health signal remain US-002's work.

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
  the two named `/health` tests stay in US-002; the compose on-switch, hermeticity (moved to US-002 in round 3), CI grep set,
  sentinel suite and fixture-tree walk are US-004 (`execution_order` runs it before the docs story;
  `feature-hardening-release.md`'s reference to the on-switch was repointed at US-004 by the epic
  session on 2026-09-19). The `STRLEN` probe became one `GETRANGE`; the write side gained the same
  bound; the vocabulary gained `unexpected_envelope`.
- Validation round 3: the `GETRANGE` miss rule and the `WRONGTYPE` reject were added; `b""` left the
  malformed shapes; `ContentCache` gained `max_value_bytes`; the write bound became bytes; the
  ceiling dropped to 8 MiB with the aggregate stated; the hermeticity line moved into US-002; the
  fixture walk exempts the generated contract twin; the fourth residual (key compromise) and the
  `ci.yml` comment joined the fan-out.
- Validation round 4 (R21 corrected, R43): the write-side skip deletes the superseded entry first;
  the `max_value_bytes > max_bytes` boot refusal became a WARNING (`cache_bounds_inverted`); the
  runbook states the two scenarios (upgrade: no burst; key enable/rotation on a running fleet: a
  bounded burst) and `oversize` joined the discriminator list; `decode_responses=False` is pinned;
  the convenience constructor forwards the cache's bound; the widened counter is a recorded
  GOVERNANCE ruling; the `put` hint's value is the `str` the protocol accepts; the by-value sweep's
  path set and the two counted greps were corrected to measured values; `:643` joined the named
  tests; TROUBLESHOOTING `:121`, DEPLOYMENT `:100` and PRODUCT_VISION `:173` joined the fan-out;
  the compose recipe generates the key value.
- Validation round 5 (final, not re-reviewed — R21 corrected): the `decode_responses` kwarg became
  a URL refusal (`valkey_url_option_forbidden`); the `Case . of 5` baseline was corrected to 2 and
  the widened grep to 5; the recorded ruling gained its marker, the count-word fan-out and the
  `_NUMBER_WORDS` test; `SERVICE_MAP.md:132` and the six bounds-pair pages joined US-003's fan-out
  with three sweep patterns; the spec 6 cross-reference became a declared forward reference; the
  PRODUCT_VISION edit became an owner gate; the round-4 salty / codebase-fit warnings that were
  one-to-three-line edits were applied and the rest recorded under "Known risks (validation
  close-out)".

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
  `max_value_bytes + 1` bytes and a return of that length is the oversize verdict; `GETRANGE` cannot
  express nil, so an empty return is a miss (round 3, three reviewers).
- Round 3: a `WRONGTYPE` reply is an integrity reject, never a disconnect — it proves the connection
  is healthy and the value is not ours (security review).
- Round 3: `ContentCache` owns `max_value_bytes` too (the write bound and the defence-in-depth
  length check need it); the bound is measured in UTF-8 bytes on both sides (`InMemoryStorage.set`'s
  precedent); `cache_settings_from_config` refuses an inverted `max_value_bytes > max_bytes`.
- Round 3: the ceiling is 8 MiB, not 16 — the read bound is per read and cache reads sit under no
  concurrency bound; the aggregate is stated and cross-referenced to spec 6's sizing table.
- Round 3: the `_CLEARED_ENV_VARS` line lands in US-002 with the first test that needs it.
- Round 3: the fixture-tree walk checks the sentinel value everywhere and the variable name
  everywhere but `tests/fixtures/contract/`, which the exporter writes and which legitimately
  names the variable.
- Round 3: key compromise is the fourth documented residual; the opening claim names its
  precondition; MONITORING says a flat counter is not evidence when compromise is suspected.
- Overruled (round 3, R37 corrected): the second opinion's split of US-003 into two doc stories —
  it is one concern (the fan-out of one vocabulary) at size L, and the by-value sweep is its gate;
  its P2 label stands because no code depends on it. Overruled: pulling
  `FORAGE_CACHE_HMAC_KEY_PREVIOUS` into scope — a verify-only second key doubles the accepted-MAC
  surface for an operational convenience no operator has yet asked for; stays an open question.
  Dropped: the `model_dump_json()` call-count assertion (a global patch on a pydantic method for a
  performance property); the observable round-trip half stays.
- Round 4 (R21 corrected): the write-side skip deletes the superseded entry first
  (`InMemoryStorage.set`'s stated invariant) — a bound that refused an update while serving the
  stale signed value was a correctness hole no counter would show.
- Round 4 (R21 corrected): `max_value_bytes > max_bytes` is a WARNING, not a refusal — a refusal
  would have stopped every deployment that pinned `cache.max_bytes` under 4 MiB on a key it never
  configured. Overruled: the salty engineer's alternative of raising `_MIN_CACHE_MAX_BYTES` to the
  new default, which would itself refuse today's legal configs.
- Round 4 (R21 corrected): the bound is documented as fleet-uniform and `oversize` is the one
  discriminator entry that is not tampering; the security-event sentence names `unsigned`,
  `bad_mac` and `wrong_type` only. The runbook's round-3 "expect a burst when the key is first
  set" sentence is gone from the upgrade path, where the rotation orphans every legacy entry and
  the only reachable `unsigned` is a foreign writer; the burst is real only for a key enable or
  rotation on an already-running 1.3.0 fleet, and the runbook says which is which.
- Round 4 (R21 corrected): `decode_responses=False` is passed explicitly — the byte bound's
  correctness rested on an unstated assumption a `VALKEY_URL` query string could silently break.
- Round 4: the widened `storage_oversize_skips` is a recorded GOVERNANCE ruling, documentation-only
  and no bump: the name's meaning (Forage's own write-side refusals) is unchanged and the set of
  backends that can produce one widened; Example 4 was considered and does not apply because no
  consumer reading the name learns something false from it — a counter that was always 0 on one
  backend can now move there.
- Round 4 (R43): the by-value sweep's path set drops the `kit_tools/` machine trees (the spec, the
  archive, the seed cache, this epic's own validation artifacts were 52 of 69 hits), the `Case N of
  5` grep is anchored (`tests/test_app.py:179`'s "500ing" matched the naive form), and the
  `Always 0 on Valkey` grep is scoped to the counter it means because the `storage_evictions`
  description legitimately keeps the phrase.
- Round 4 (R36 corrected): US-001 appends `integrity_rejects` (a property); US-002 appends
  `cache_unauthenticated` (an enum member); the description changes and the `cache_hmac_key`
  capability key are golden-only.
- Round 4: the compose header recipe generates the key value; an empty assignment is the absent
  shape and would ship the quickstart `degraded`.
- Round 5 (R21 corrected): the explicit `decode_responses=False` kwarg does **not** pin the byte
  mode — `ConnectionPool.from_url` applies URL options over kwargs (verified on redis 8.1.0) — so
  the four reply-shaping options are refused at `ValkeyStorage.__init__` with a closed marker and
  `CacheConfigurationError`; the kwarg stays as belt. Placed in `cache.py`, not
  `_configured_valkey_url()`, because the lifespan-less `/health` calls that function and must never
  raise; the round-4 "ignored" edge case and the "`parse_url` forwards unknown parameters" rationale
  were false as written and are gone. The criterion asserts the effect (a refusal, and the
  constructed pool's setting), not the call alone.
- Round 5: `oversize` and `wrong_type` move neither `storage_hits` nor `storage_misses` — a read that
  served nothing was not a hit, and it was not an absence either; the hit/miss ratio stays legible
  during the one event the feature exists to make legible.
- Round 5: the recorded ruling on the widened counter is gated — its `### (<letter>) ` marker joins
  `_RULING_MARKERS`, and a `_NUMBER_WORDS[len(_RULING_MARKERS)]` test pins the count word the
  by-value sweep could never reach (`contract/` and `tests/` are outside its path set by design).
- Round 5: the PRODUCT_VISION scoping is an owner gate — the story writes the replacement text and
  stops; `:63` needs no edit, `:65`'s stale clause is the `(weights absent, cache unreachable)`
  enumeration, not the paid-key sentence.
- Round 5: the spec 6 sizing cross-reference is a declared forward reference; spec 6's page carries
  the `+ cache.max_value_bytes` term (its round-5 directive), so the promise is recorded on both
  sides.
- Round 5: the FakeStorage drives use a small explicit bound; the 4 MiB default is asserted where
  it is configured, not materialised per test.

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

### Session 2026-09-19 (validation round 3)
- Rulings applied: 21 (corrected — empty `GETRANGE` return is a miss; `wrong_type`; `ContentCache
  .max_value_bytes` and its lifespan construction site; the legacy-`v1.` literal pinned; the grep
  baseline corrected), 37 (corrected — no further splits), 39 (the sweep as one runnable command
  with the oversize-skip and pattern-count phrases), 40 (the five inline `.get` stubs named with a
  starting count; the third `/health` test named), 41, and the round-3 per-spec directives
  (`storage_oversize_skips` on both backends; the fixture walk exempts `tests/fixtures/contract/`;
  the `Case N of 5` series renumbered).
- Q: What does an empty `GETRANGE` return mean? → A: A miss — Forage never writes an empty value,
  so empty is unambiguously absent; it is counted on `storage_misses` and never reaches `_unwrap`.
- Q: What if the key itself leaks? → A: Forged envelopes verify and the counter stays flat; the
  response is the fleet-wide stop-all rotation; a key id in the envelope is the open question that
  would make scoped revocation possible.

### Session 2026-09-19 (validation round 4)
- Rulings applied: 21 (corrected — no first-enable burst on the upgrade path, the two scenarios
  stated; delete-before-skip; the fleet-uniform bound and `oversize` in the discriminator list; the
  inverted-bounds WARNING; `decode_responses=False`; the counter widening as a recorded GOVERNANCE
  ruling; the corrected grep baselines and sweep path set), 36 (corrected — properties and enum
  members append, descriptions and dict keys do not), 43 (greps scoped and executed), and the
  round-3 salty / codebase-fit / security findings (the `str`-typed `put` value; the `:643` test;
  the bound forwarded by the convenience constructor; the three fan-out lines; the generated
  recipe value).
- Q: Does enabling the key on an existing deployment produce an `unsigned` burst? → A: Not on the
  upgrade path — the rotation orphans every legacy entry. Only a key enable or rotation on an
  already-running 1.3.0 fleet produces the bounded burst.
- Q: What does a sustained `oversize` rate mean? → A: A replica with a lower `cache.max_value_bytes`
  than its peers, or a recent lowering — a configuration fact, not tampering.
- Q: Does a `cache.max_bytes` under 4 MiB still boot? → A: Yes, with one `cache_bounds_inverted`
  WARNING; the memory backend's own `max_bytes` check is then the effective per-entry bound.

### Session 2026-09-19 (validation round 5, final)
- Rulings applied: 21 (corrected — `decode_responses` refused, not overridden: the four
  reply-shaping `VALKEY_URL` query options refuse boot at `ValkeyStorage.__init__` with
  `valkey_url_option_forbidden`, never echoing the URL; `Case . of 5` baseline 2 and the widened
  five-site grep; the recorded ruling's marker, count-word fan-out and `_NUMBER_WORDS` test;
  `kit_tools/arch/SERVICE_MAP.md:132` in US-003's fan-out; spec 6's memory rule carries the
  cache-read term this spec promised), plus the round-4 salty-engineer and codebase-fit warnings
  applied as one-to-three-line edits: the `oversize` / `wrong_type` counter rule; the six
  bounds-pair pages and three sweep patterns; the forward-reference wording; the PRODUCT_VISION
  repointing and owner gate; the positional `getrange` call; the "doc-scanning suite" sentence
  replaced by the real gate; the three citation slips (`:1379-1402` before the handlers,
  `SERVICE_MAP.md:146`, `configuration.md:539` two-reasons only); the log-volume residual; the
  small test bound; the `_NO_CACHE_TIERS` hole recorded. This round was not re-reviewed.
- Q: Why not pin `decode_responses=False` with the kwarg? → A: `from_url` applies the URL's query
  options over every kwarg ("querystring arguments always win"), so the kwarg cannot win; the only
  honest pin is to refuse a URL that carries the option. The check reads query keys only, in
  `cache.py`, so no password can reach a log line and the lifespan-less `/health` never raises.
- Q: Does an `oversize` or `wrong_type` read count as a hit or a miss? → A: Neither — only
  `integrity_rejects` moves.
- Q: Who edits `kit_tools/PRODUCT_VISION.md`? → A: The owner, from the replacement text US-003
  writes into its Implementation Notes; the story itself does not touch the file.

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
- [ ] Whether the envelope should carry a key id (`v1.<kid>.<mac>.<payload>`) so a compromised key
      can be revoked without a fleet-wide cold restart (non-blocking; the stop-all rotation is the
      documented response).
- [ ] Whether `ContentCache.put`'s `_NO_CACHE_TIERS` early return should delete the existing entry
      under that key, so a page that later classifies `UNTRUSTED` / `BLOCKED` stops serving its
      stale signed envelope before the TTL (non-blocking; pre-existing, outside this spec's oversize
      branch — round 5).
- [ ] Whether the `cache_integrity_reject` WARNING should be rate-limited, given a writer who can
      `SET` can drive one line per request (non-blocking; the request rate and spec 6's admission
      bound it today — round 5).

## Known risks (validation close-out)

Round-4 warnings not applied in round 5 (the final, un-reviewed fix pass), each with the reviewer,
the finding and why it is deferred rather than fixed:

- **Salty engineer — the `VALKEY_URL` query refusal is a boot-behaviour change.** An operator whose
  URL carries `?decode_responses=1` (or `encoding` / `encoding_errors` / `protocol`) boots today and
  will not after US-001. Accepted: the option would silently break the byte bound and there is no
  way to pin it; the refusal is loud, names the option, and ships as an upgrade note rather than a
  compatibility window (no wire change, so GOVERNANCE is not engaged).
- **Salty engineer — `socket_timeout` / `socket_connect_timeout` stay operator-overridable through
  the URL.** The bounded 2 s deadline `ValkeyStorage` advertises for operations can be raised by a
  query option. Accepted as tuning; the `asyncio.timeout` connect deadline is unaffected; recorded
  in Technical Considerations rather than refused.
- **Salty engineer — the spec 6 sizing cross-reference is a forward reference.** The
  `docs/configuration.md` row points at a section two specs away; spec 6's page carries the
  `+ cache.max_value_bytes` term by its own round-5 directive, applied concurrently. Risk: if spec
  6's page did not land the term, the promise is one-sided — verify it at execution.
- **Salty engineer — the PRODUCT_VISION scoping waits on the owner.** Until the owner applies the
  two lines from US-003's Implementation Notes, the vision's degraded-state enumeration is
  two-of-three. Accepted: an owner-level table is not rewritten by an autonomous lane.
- **Salty engineer — the reject WARNING is un-rate-limited.** A writer who can `SET` can drive one
  attacker-chosen log line per request. Deferred as an open question; the residual is now written
  into SECURITY.md and bounded by spec 6's admission.
- **Salty engineer — the `_NO_CACHE_TIERS` early return keeps serving a stale signed envelope.** A
  page cached as `STANDARD` that later classifies `UNTRUSTED` / `BLOCKED` is served until its TTL
  with the MAC verifying and the counter flat. Deferred: pre-existing behaviour outside the oversize
  branch this spec fixes; recorded in Out of Scope and Open Questions so the round-4 rationale is
  not read as having closed both.
