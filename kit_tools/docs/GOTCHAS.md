<!-- Template Version: 2.0.0 -->
# GOTCHAS.md

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-hostname-and-config US-005)

## Overview

Known issues, quirks, and non-obvious behaviours. Landmines to avoid.

The first three entries **travelled with the code** out of Poppy's `kit_tools/docs/GOTCHAS.md`
at extraction time — they are properties of this service, not of the monorepo it used to
live in, and losing them in the move was an identified risk.

---

## Active Gotchas

### A cache read bound depends on binary replies, not a client keyword alone

redis-py's URL query options override `from_url` keyword arguments. Therefore
`decode_responses=False` alone does not pin byte mode. `ValkeyStorage` refuses
the query keys `decode_responses`, `encoding`, `encoding_errors` and `protocol`
before connecting, logging only the option name. Socket timeout options remain
operator tunable. A malformed URL still follows the guarded connect's degraded
path; lifespan-less `/health` never constructs storage and remains non-raising.

`GETRANGE key 0 max_value_bytes` is atomic and returns at most bound + 1 bytes.
Empty means miss, not corrupt or unsigned; `WRONGTYPE` means reject, not
disconnected. The signed prefix is **68 bytes**, not the spec hint's 67.
Measure serialized UTF-8 bytes, and delete a superseded entry before an
oversize write skip. The 4 MiB default leaves headroom above the 2 MiB extraction
budget, but JSON escaping can still inflate a pathological value past it;
that is a write skip, never an integrity signal. Keep the same bound across
replicas; lowering it rejects old larger writes without proving tampering.

### Concurrent requests cannot own overlapping global mock contexts

`unittest.mock.patch` changes a module attribute process-wide, not per task.
Two overlapping `_retrieve_under` contexts exited out of order and restored
each other's mocks, leaving `orchestrator.fetch_url` mocked for the following
admission tests. The US-005 retry moves both fetch and validation patches into
one test-scoped fixture and drains every concurrent request before teardown.
Keep that ownership outside the coroutines. Use bounded entered-event/waiter
synchronization for threaded classification; fixed `sleep(0)` iteration counts
do not establish that a worker has started. Always release gates and drain
tasks on assertion failure, before restoring their mocked dependencies.

### A YAML boolean threshold disables blocking on `/extract` only

`promptguard_threshold: true` is invalid for the shared `/retrieve` and `/search`
default: boot logs one `config_invalid_value` WARNING naming the key, falls back
to 0.85 and publishes that validated default separately from the raw config.
The WARNING explicitly says `/extract reads the raw value through its own guard`.
That route retains `float(raw_value)` plus a range check; `float(True) == 1.0`
passes, so no classifier score can exceed it. The operator ceiling does not
reach `/extract`. This known divergence is pinned by
`test_boolean_threshold_keeps_extracts_raw_coercion_only`; closing it remains an
open question under ruling R10, not part of US-005. Use a numeric threshold.

Both fetch routes now default from the configured key, then cap against the
operator ceiling. Operators who previously raised it just for uploads now loosen
fetch blocking too unless capped; lowering it tightens both. The raw configured
value remains a revision input; the active threshold also enters the cache
fingerprint. Null and an explicit equal default therefore share a cache key.

### Hostname matching is a hashed cache-key input, not an unhashed helper

`url_validator.py` is a cache-key input in substance **and in the actual hash**:
search-sanitization US-003 added `_ROOT_REVISION_SOURCES = ("url_validator.py",)`.
The hostname spec's earlier warning that `_REVISION_SOURCES` alone does not hash it
is historically true but incomplete; both tuples feed the revision now. A
matcher-only change rotates automatically, so old-policy cached content is not
served until its TTL. Do not remove the root tuple to avoid a rotation.
The old open question is resolved: the cost of hashing this whole file is that
even comment-only edits invalidate all cached sanitizations, accepted to prevent
stale privilege decisions. US-001 therefore measures three changed hashed files,
not the two the older spec assumed.

### JSON surrogate escapes reach the handler as Python strings

FastAPI parses JSON before validating the Python object; unlike
`RetrieveRequest.model_validate_json`, this admits lone surrogate escapes in a
`list[str]`. A strict UTF-8 encode in the byte-budget helper therefore raised a
500 instead of dropping an invalid entry. `domain_list_bytes` uses `surrogatepass`
**only for sizing**, charging three bytes per surrogate; canonicalisation still
rejects the entry and the handler counts it. The regression sends escaped JSON
through the actual ASGI route. Do not "fix" it with lossy replacement before the
matcher or by logging the bad entry.

### YAML domain lists need type checks before string normalisation

`seed_blocklist: [null, true, 123]` must warn and drop those members, not crash
in `.strip()`. A scalar `seed_blocklist: evil.com` must not become a list of
single-character hostnames. The lifespan checks both list containers and string
members before calling the string-only normaliser. Non-string members log only
`[non-string]`; non-list containers publish `[]` and warn once with
`dropped=1 entries=[invalid-container]`. The same boundary applies to `news_domains`.
Never stringify arbitrary YAML values into these logs or broaden this fallback to
non-mapping whole documents or other subsystem blocks: their refusal behavior is unchanged.

### YAML integers can overflow a float before validation

`pipeline/config_bounds.bounded_float` must compare the original numeric value with
its bounds **before** widening it to float. YAML accepts 401-digit decimal integers;
converting first raises `OverflowError` instead of the caller's closed-vocabulary
configuration error. US-005's retry pins both signs through the settings reader and
lifespan, including the exact message and absence of the supplied value in logs.

### PromptGuard model absent → the service reports **degraded**, and you must treat it as unscanned

**Location:** `promptguard/classifier.py`, `pipeline/stage3_promptguard.py`, `/health`
**Severity:** 🔴 High
**Added:** 2026-09-07 (travelled from Poppy; post-Epic-1 wording)

**What happens:**
Stage 3 runs the gated `meta-llama/Llama-Prompt-Guard-2-22M`. **Since
`forage-ci-and-image` US-003 every image is weights-free**: the build-time bake is gone,
and since `feature-forage-model-bootstrap` US-001 a *runtime* fetch replaces it — which
needs `HF_TOKEN`, because the repository is gated. So a container started without one is
weightless by design, not by accident. Forage reports `status: "degraded"` with
`promptguard_unavailable` in `degraded_reasons` and `promptguard_loaded: false`, and the
ML injection scan does not happen. Stage 2's deterministic regex pass still runs; stage 3
does not.

**The tell, since US-001, is one log line at start:** `weights_fetch_skipped — no
HF_TOKEN in the environment`. If instead you see `weights_pin_unusable`, the token is not
the problem — the committed `weights_manifest.json` is still the placeholder that pins no
files (US-003 replaces it), and the fetch is refused before it spends the bandwidth.
`weights_verification_failed` means bytes arrived and were refused; read its reason codes.

**Why it matters:**
Before the honest-health contract existed, this failure was *silent* — `/health` reported
`healthy` regardless of `promptguard_loaded`, and a fail-closed consumer discarded every
standard-tier search result, which read to a user as "no matches." It ran unnoticed for
**nine days** in production. That shape is fixed here: the degradation is loud and
machine-readable. Keep it that way.

**Rules:**
- Read the `/health` **body**, not the status code — `/health` is always 200.
- Never make `/health` report `healthy` when `promptguard_loaded` is false.
- Never "resolve" a fail-closed consumer by turning fail-closed off. The correct fix is
  supplying the weights.

**Since US-005 this is machine-enforced, not a rule you have to remember.** CI's `smoke`
job runs the built image with no Hugging Face token on every push and PR and asserts the
degraded contract through `contract_smoke.py`: HTTP 200, `status: "degraded"`,
`promptguard_unavailable` in `degraded_reasons`, no `search_sanitization` capability, a
derived `sanitizer_revision`, and `contract_version` equal to this tree's
`pipeline/contract.py`. A commit that made `/health` claim `healthy` without weights now
turns the workflow red. Verified against a live container both ways — the honest image
passes, and the same image with the break-glass `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION=1` (pre-rename: `FORAGE_LEGACY_CAPABILITY`) armed fails
with exactly the capability violation.

**Verify after any rebuild:**
`docker logs <container>` → one of the three lines above, and
`curl -s localhost:8020/health | jq .promptguard_loaded` → `true` once a token and the
real manifest are both in place. Expect `false` from a stock image and treat the content
as unscanned — that is the honest answer, not a broken deploy.

**A `false` that is on its way to `true` looks identical on `/health`.** Since US-001 the
fetch runs behind a serving app, so a container that is three minutes into a ~270 MiB
download reports exactly what a token-less one reports. `/metrics`' `model` section is
what separates them: `fetch_in_progress: true` means downloading, and the
`fetch_failures` / `verify_failures` / `quarantines` counters say whether anything has
gone wrong yet. Poppy's deploy readiness wait reads that field for this reason.

Since US-005 there is a **third** state behind the same `false`: waiting out a backoff
between retries. `retries_scheduled > 0` with `fetch_in_progress: false` is that one, and
the `weights_retry_scheduled` WARNING names the delay. Both-at-zero on a degraded
container means the first attempt simply has not finished.

---

### A plain `snapshot_download` fails verification — pass `ALLOW_PATTERNS`

**Location:** `model_fetcher.py`, `weights_manifest.json`
**Severity:** 🟡 Medium
**Added:** 2026-09-10 (`feature-forage-model-bootstrap` US-002)

**What happens:**
`model_fetcher.verify_weights()` is an **exact-set** check with a format allowlist: every
file the manifest pins must be present with the right sha256 and size, and the snapshot
must contain *nothing else*. A plain
`snapshot_download("meta-llama/Llama-Prompt-Guard-2-22M", revision=...)` also pulls
`README.md` and `.gitattributes`, neither of which is an allowlisted format. The download
succeeds, the files land, and verification then refuses the whole set — `file_extra` /
`disallowed_format` — and quarantines it. The failure looks like corruption and is not.

**Why it matters:**
It bites at exactly the two moments nobody wants a surprise: the first real HF fetch
(US-001) and the vendoring run that generates the real manifest (US-003). And the
quarantine makes it look destructive.

**Mitigation:**
Pass `allow_patterns=model_fetcher.ALLOW_PATTERNS` to every `snapshot_download`, and
apply `model_fetcher.is_allowed_filename()` when generating a manifest. `ALLOW_PATTERNS`
is derived from `ALLOWED_SUFFIXES` — one constant, so the fetch filter and the
verification rule cannot drift apart. Widening the allowlist to admit README files is the
wrong fix: `.bin` is excluded by the *same* rule, and that exclusion is the RCE closure
(`from_pretrained` can never be handed a pickle).

**Mitigation, generation side (US-003).** `scripts/vendor_weights.py` applies
`is_allowed_filename()` **at manifest-generation time** and refuses the whole run if the
snapshot carries anything else — a manifest blessing a `.bin` is not merely unusable, it
cannot be produced. It also passes `ALLOW_PATTERNS` to its own `snapshot_download`, so the
vendoring run and the container fetch land the same set by construction.

**Related:** `weights_manifest.json` is a fail-closed placeholder with `"files": []` until
**US-003's supervised ops run** (the one holding the real token) commits the generated
one; `verify_weights()` refuses everything with `manifest_empty` until then. US-003's
build half deliberately left it that way and made the tests state-agnostic across the
swap, so the ops commit is a pure manifest replacement. Since US-001 the boot path *does*
consult it — and short-circuits on it: a manifest that pins nothing could never bless a
download, so the fetch is refused before it starts (`weights_pin_unusable`) rather than
after ~270 MiB. A stock image's `/health` is unchanged either way. `docs/weights.md` is
the vendoring procedure.

---

### Nothing configures logging, so every `logger.info` in this repo is invisible in the container

**Location:** repo-wide; most visibly `model_fetcher.py`'s acquisition lines
**Severity:** 🟡 Medium
**Added:** 2026-09-10 (`feature-forage-model-bootstrap` US-004)

**What happens:**
No module calls `logging.basicConfig()` and the entrypoint sets no log level, so the root
logger keeps Python's default — **WARNING**. Verified in a running container:
`logging.getLogger("model_fetcher").getEffectiveLevel()` is `30`. Every `logger.info` in
the service is therefore emitted and then dropped. `weights_verified`, `weights_fetched`,
`weights_loaded` and `weights_fetch_attempt` are all INFO, so the entire *success*
narrative of a weights acquisition is invisible to `docker logs` while every failure is
loud.

**Why it matters:**
It reads as a bug in the wrong direction. An operator watching a cold start sees nothing
at all until either the ERROR or a `promptguard_loaded: true` on `/health` — which makes
"is it downloading or is it wedged?" a question the logs cannot answer, and is precisely
why `/metrics` carries `model.fetch_in_progress`. It also quietly weakens any acceptance
criterion phrased as "logged": the record is emitted, and a `caplog`-based test sees it,
but a deployment does not.

**Mitigation:**
Use `/metrics`' `model` section — not the logs — to observe a fetch in flight. Failures
are ERROR and do surface, with a closed reason vocabulary. If you need the success lines
while debugging, `uvicorn --log-level info` does NOT work — that flag configures
uvicorn's own loggers, not the root (measured at US-004 verification: `weights_fetch_attempt`
still never appears). The working fallback is `logging.basicConfig(level=...)` from a
Python entry point; note that this changes output for *every* lane at once, which is why
US-004 recorded it here rather than changing it in passing.

---

### `huggingface_hub` 1.x phones home while building request headers — and `HF_HUB_OFFLINE=1` does not stop it at run time

**Location:** `promptguard/classifier.py`'s load path, `model_fetcher.py`
**Severity:** 🟡 Medium
**Added:** 2026-09-10 (`feature-forage-model-bootstrap` US-001)
**Updated:** 2026-09-10 (US-005 — **the mitigation below was wrong, and is corrected**)

**What happens:**
`huggingface_hub` 1.30's `build_hf_headers()` calls `detect_agent()`, which fetches a
"harness registry" from `{ENDPOINT}/api/agent-harnesses` to decide what to put in the
user-agent string. It runs on **every** hub call, including `from_pretrained(...,
local_files_only=True)` on a fully-populated cache — the flag governs *file* resolution,
not header construction. Found by the suite's socket guard, which flagged
`socket.getaddrinfo` in a test that had no business touching the network.

**Why it matters:**
Two places, for two different reasons.

- **US-005's "zero network on a warm start" is measured against this.** A socket-guarded
  warm-start test will see an attempt unless something suppresses it, and "zero network"
  has to mean zero.
- **Cold-boot latency.** The fetch is best-effort with a 3 s timeout and every failure is
  swallowed, so nothing breaks — but on a container with no egress it is 3 s spent on
  every process start until the response is cached.

**The correction (US-005, measured both ways).** `HF_HUB_OFFLINE=1` *does* disable the
call — but only if it is set **before `huggingface_hub` is imported**. The variable is
sampled once at import into `huggingface_hub.constants.HF_HUB_OFFLINE`
(`constants.py:192`) and `_fetch_registry` reads that constant, so setting the environment
variable from a running process changes nothing at all. Measured on a cold `HF_HOME`:
with `os.environ["HF_HUB_OFFLINE"] = "1"` the warm load still made one
`create_connection(('huggingface.co', 443))` (httpx/httpcore; an earlier draft recorded getaddrinfo); with `constants.HF_HUB_OFFLINE = True` it made none,
and loaded identically.

**Mitigation:** `model_fetcher._offline_hub()` sets **the constant**, scoped to the
classifier load and restored in a `finally`. Scoping matters as much as the mechanism: the
same flag disables `snapshot_download`, so a process-wide pin (an `ENV` line in the
Dockerfile, say) would turn every cold start into a permanent degraded mode. By the time
the load runs, the download has already finished — which is why the pin is safe on the
cold path and load-bearing on the warm one. `tests/test_model_fetcher.py`'s
`TestWarmStartTouchesNoNetwork` pins all of it, including a negative control that removes
the pin and asserts the attempt reappears; the suite's plain socket guard cannot see this
on its own, because `huggingface_hub` swallows the error — a blocked attempt and no
attempt look identical unless you *count* attempts
(`tests.fakes.record_network_attempts`).

**The other cache.** The registry response is also cached at
`$HF_HOME/.agent_harnesses.json` for 24 h, which means a machine that has ever reached the
Hub will not make the call again and any test of this must clear both that file and the
module-level `_registry` global to mean anything. The file lands in `$HF_HOME`, a sibling
of `hub/` and `quarantine/`; `verify_weights()` walks `hub/models--…/snapshots/<rev>/` and
never sees it, so it is not an "extra file" — but any future check that assumes the volume
contains only what we put there will be surprised by it.

---

### An over-sized upload gets **400**, not the 413 the size middleware emits

**Location:** `retrieval_app.py` (`DocumentSizeLimitMiddleware`), the `/extract` route
**Severity:** 🟡 Medium
**Added:** 2026-09-11 (`feature-forage-contract` US-001; measured on FastAPI 0.141.1)

**What happens:**
`DocumentSizeLimitMiddleware` refuses an over-sized body by raising
`_RequestBodyTooLargeError` out of the ASGI `receive` callable it wraps, and catches it
to emit `413 {"error": "content_too_large", "reason": ...}`. On `/extract` that
exception is never its own to catch. The route is a multipart form route, so the first
thing to pull from `receive` is FastAPI's `await request.form()` — and
`fastapi/routing.py` wraps any exception out of that call — other than
`json.JSONDecodeError` (→ 422) and `HTTPException` (re-raised as-is), neither of which
applies to `_RequestBodyTooLargeError` (audit precision 2026-09-11: an earlier draft said
"*any*") — in
`HTTPException(400, "There was an error parsing the body")`. The 400 response is
produced and sent inside the middleware's `await self._app(...)`, which then returns
normally, so the `except _RequestBodyTooLargeError` block never runs.

Measured three ways against the running app:

| Request | Result |
|---|---|
| multipart upload over the limit | **400** `{"detail":"There was an error parsing the body"}` |
| `application/x-www-form-urlencoded` body over the limit | **400**, same body |
| `application/json` body over the limit | **422** validation error — starlette never streams the body for a non-form content type, so the byte counter never runs at all |

**Why it matters:**
The 413 reads like live behaviour in the source and in every design document that
describes the admission path, and it is not reachable from outside. A consumer writing a
handler for 413 will never see one; the code it must actually handle for an over-sized
upload is a 400 whose body carries no `error` field at all. The byte *cap* still works —
nothing over the limit is spooled or extracted — so this is a wrong-status bug, not a
missing-limit one.

**Mitigation:**
Both statuses are declared on `/extract` in the OpenAPI contract, with the 413's
description saying it is shadowed, so the document does not promise a response the
service cannot send. Two tests hold the line:
`tests/test_contract_errors.py::test_extract_oversized_upload_actually_receives_400`
pins the real behaviour (a FastAPI change would make it red), and
`test_extract_413_body_is_mirrored_and_carries_no_request_id` exercises the middleware at
the ASGI seam, which is the only place the 413 shape can be produced.

**If this is ever fixed**, it is a **wire change** — a status a consumer sees moves from
400 to 413 — and it belongs to whoever owns the next contract bump, not to a passing
refactor. `feature-forage-contract` is explicitly a zero-wire-byte spec and deliberately
left the behaviour alone.

---

### Adding a `/metrics` counter without adding it to the model **500s the endpoint**

**Location:** `retrieval_app.py` (`MetricsResponse` and its five section models)
**Severity:** 🟡 Medium
**Added:** 2026-09-11 (`feature-forage-contract` US-005)

**What happens:**
`/metrics` is a typed response since US-005, and every one of its six models sets
`extra="forbid"`. FastAPI validates a handler's return against the response model, so a
counter added to the handler dict and not to the model raises `ResponseValidationError`
— a 500 on an endpoint an operator reaches for precisely when something is wrong. The
whole `/metrics` route goes down, not just the new field.

**Why it was built that way — read this before "fixing" it by relaxing the model.**
The alternative is worse and silent. A permissive response model makes FastAPI *filter*
the unmodeled key out of the response and answer 200: the counter is added, reviewed,
merged, deployed, and never appears on the wire, and the first person to notice is
whoever is debugging an incident without the number they were promised.
`tests/test_contract_metrics.py` pins both halves —
`test_an_unmodeled_counter_fails_loudly` for ours,
`test_a_permissive_response_model_would_have_dropped_it_instead` for the counterfactual,
measured on the locked FastAPI rather than asserted.

**Mitigation:**
Add the field to its section model in the same commit, **in the same position** — the
parity test compares the served bytes against the handler dict's own serialization, so
order is part of the contract, not a formatting detail. Then classify the addition:
`/metrics` was outside the frozen response surface when its `model` section was added
additively, and it is inside that surface now.

**Related:** the three cgroup keys are flat inside `extraction` because
`**_cgroup_memory_snapshot()` splats them there. Nesting them under a `memory` object is
a wire change, not tidying; `tests/test_app.py::test_metrics_expose_saturation_and_oom_proximity`
is the older fossil guard for that decision and `tests/test_contract_metrics.py` adds the
schema half.

---

### SearXNG `:latest` rots: stale scraper fingerprints get bot-blocked by every engine

**Location:** `searxng/config/settings.yml`, the companion SearXNG image
**Severity:** 🟡 Medium
**Added:** 2026-09-07 (travelled from Poppy; recurred on a ~9-day cycle there)

**What happens:**
A months-old `searxng/searxng` image gets CAPTCHA / 429 / access-denied from every
upstream engine while a plain `curl` from the same IP succeeds. It is a **staleness
fingerprint**, not an IP block — SearXNG already rotates real-browser user agents, so the
rotted *engine definitions* are the tell. Worse, SearXNG's own healthcheck only loads
`/`, so the container reports "healthy" with 100% of its engines blocked.

**Why it matters:**
It presents as "web search is dead" with no error anywhere, and it comes back on a cadence
rather than once.

**In a multi-provider chain** (`search-fallback` US-002), this exact shape — a 200 with
zero raw results and a non-empty `unresponsive_engines` — is a classified failure that
advances to the next provider only when zero raw results come back; a partial answer
(results plus a non-empty list) is served as-is and no fallback fires. A configured chain
of exactly one `searxng` provider has nothing to fall back to, so it is still served as the
200 above.

**Mitigation and the pin-bump cadence:**
The image must be **pulled and recreated on a schedule**, not pinned once and forgotten.
Forage now publishes a companion `forage-searxng` image (digest-pinned base + baked
config) — US-004, shipped 2026-09-08 — and **the pin bump is the schedule**: bump the
digest, smoke it, tag `searxng-v*`. The runbook is `docs/searxng.md`.

Be honest about what that did and did not fix. Pinning removes the *silent* rot (nobody
could say which SearXNG was in production) and it removes the drive-by engine changes.
It does **not** make fixes arrive on their own — that is what `:latest` was buying. Two
signals replace it, and neither repairs anything: the advisory live-engine probe in
`searxng-smoke` goes red at bump time (deliberately non-blocking, so it never fails a
release over someone else's rate limiter), and Poppy's runtime web probes notice in
production. **A bump nobody performs re-arms this gotcha**, exactly as a bump job that
stopped running would have. Rolling our own search image was considered and rejected —
the community maintaining engine definitions weekly *is* the value.

**Also note:** the engine list appears in two places — `SEARXNG_ENGINES` in
`pipeline/search_providers/searxng.py` and `searxng/config/settings.yml`. Both live in this repo now, and
`tests/test_searxng_docker.py` now asserts the two sets are equal rather than asking
anyone to keep them in sync by hand.

---

### SearXNG's limiter refuses API clients — turning it on is an outage, not protection

**Location:** `searxng/config/settings.yml`, `searxng/config/limiter.toml`
**Severity:** 🔴 High
**Added:** 2026-09-08 (US-004; measured against `searxng/searxng` 2026.9.7-3e454637f)

**What happens:**
`server.limiter: true` with a working Valkey backend does not rate-limit abuse of the
JSON API. It refuses the JSON API. Measured, three ways:

| Client | Result |
|---|---|
| Forage's own httpx client | **429 on the first request** — `http_accept_language` blocks anything without an `Accept-Language` header, and httpx sends none |
| `curl` | **429 on the first request** — `http_accept_encoding`, then `http_user_agent` (its regex matches curl, wget, python-requests) |
| a perfectly browser-shaped client | 200, 200, 200, **429** — `ip_limit.API_MAX = 4` per `API_WINDOW = 3600 s`, per client network, for any `format != html` |

Neither `API_MAX` nor `API_WINDOW` is configurable from `limiter.toml`; they are module
constants in `searx/botdetection/ip_limit.py`. botdetection is a *browser* detector, and
an API consumer is not a browser.

**Why it matters:**
The failure is intermittent, not total — four searches an hour succeed — so it presents
as "web search is flaky" with a 429 that never reaches a Forage log line as anything but
`searxng_error`. And the switch reads as a security improvement, so it is exactly the
thing a well-meaning hardening pass turns on.

The trap has a second face: `limiter: true` with **no** backend is inert theatre.
Upstream logs `The limiter requires Valkey` at ERROR and serves everything unthrottled.
So the config that *looks* protected is either unprotected or broken, depending on
whether someone wired up Valkey.

**Mitigation:**
The baked config ships `limiter: false` and says why, at length, in place.
`tests/test_searxng_docker.py::TestSearxngLimiter::test_limiter_is_off_in_the_baked_config`
fails if it comes back, and `searxng_smoke.py` exercises all three states in CI — off,
on-with-Valkey (which must throttle) and on-without (which must log and serve). The image
is protected by *not being exposed*: it is a sidecar on a private network with one client.

A deployment that must expose it opts in with `SEARXNG_LIMITER=true` plus
`SEARXNG_VALKEY_URL`, and then needs a `pass_ip` entry for its consumer's network — a
scoped IP-trust relaxation, made by the operator who needs it. `docs/searxng.md` has the
recipe.

---

### `sanitizer_revision` has deliberately diverged from Poppy's

**Location:** `pipeline/sanitizer_revision.py`
**Severity:** 🟡 Medium
**Added:** 2026-09-07 (forage-repo-bootstrap US-002)

**What happens:**
`derive_sanitizer_revision()` hashes nine source files — the eight under `pipeline/` plus
repo-root `url_validator.py` — plus the model identity, the `idna` version
(`idna@<version>`: UTS-46 tables decide which hosts are dropped) and the active
threshold. Forage's revision has moved twenty-nine times. The twenty-sixth was
reconciled from the preceding validation commit during US-001's pre-flight; the rest
were recorded at their implementation boundaries:

| When | Value | What moved it |
|---|---|---|
| At split | `e6b2b56d…` | — (identical to Poppy) |
| `forage-repo-bootstrap` US-002 | `2b8d7e9a…` | vault-free hostname defaults in `orchestrator.py` |
| `forage-ci-and-image` US-001 | `cd00a8b4…` | `ruff format` gate reformatted `stage2_structural.py` |
| `forage-ci-and-image` US-006 | `0537316d…e3e253` | pyright-strict burn-down retyped `stage1_extraction.py` + `stage2_structural.py` |
| `forage-model-bootstrap` US-001 | `5927038d…19d111` | the hashed model identity became `MODEL_ID@revision` — **no source byte moved** |
| `forage-cache-fallback` US-003 | `fa4691c5…93547c` | `contract.py` bumped to `1.1.0` **and** `orchestrator.py` threaded the revision into the cache key — the rotation whose *point* is the invalidation |
| `forage-contract` US-001 | `8b1b7f78…196d7c` | `contract.py` gained the then-17-code error vocabulary and `DegradedReason` as derived Literals — documentation only, contract still `1.1.0` then, and the **one** rotation the whole of `feature-forage-contract` gets |
| `search-provider-abstraction` US-002 | `ee4450d9…f63c3da` | the inline SearXNG call left `orchestrator.py` for `SearxngProvider` — the provider module is **not** a hashed filename, so `orchestrator.py` is the only file that moved; wire codes unchanged, `reason` text narrowed |
| `search-provider-abstraction` US-003 | `e7038672…3ce0cbf` | `run_search_pipeline` gained the `providers=` chain seam; `providers=None` is the previous behaviour unchanged |
| `search-provider-abstraction` US-004 | `b7871b20…ea6f2b` | contract `1.2.0` — **two** hashed files: `contract.py` (`ContentKind`, `search_unavailable`, the version) and `orchestrator.py` (the chain-shaped failure predicate, the `content_kind`/`date` copy) |
| `search-fallback` US-001 | `55e2af1b…bf47e4` | `run_search_pipeline` gained free-first chain traversal in `orchestrator.py`: calls providers in order, advances on a `ProviderFailure`, stops at the first success; a one-provider chain is byte-identical to before |
| `search-fallback` US-003 | `5249def6…89f24a` | `run_search_pipeline` gained fallback telemetry and per-result provenance in `orchestrator.py` (`SearchResult.domain`, `provider_used`/`fallback_fired`/`provider_errors`, the `SearchMetricsSink` Protocol); `models.py` gained the matching wire fields but is not a `_REVISION_SOURCES` member |
| `search-fallback` US-002 | `f0b93318…70d62` | `run_search_pipeline` classifies a zero-result, non-empty-`unresponsive_engines` `ProviderSearchResult` as a failure in `orchestrator.py` — SearXNG's real production failure shape; a lone-`searxng` chain is carved out and unaffected |
| `search-policy-and-health` US-010 | `dc3ff92a…eded9` | `contract.py` gained `POLICY_EXCLUDED_ALL_PROVIDERS`, the fixed-literal `reason` the `/search` handler raises when the new `apply_request_policy` narrows a request's effective chain to empty; `retrieval_app.py`, where the raise site lives, is not a `_REVISION_SOURCES` member |
| `search-policy-and-health` US-003 | `41ac98ca…b4e318` | `contract.py`'s `CONTRACT_VERSION` docstring gained the completed 1.2.0 change record (every field, counter and enum member specs 1-4 added, all additive) plus a note that the `/search`/`/retrieve` boundary text rides the same unpublished window; `retrieval_app.py` and `models.py`, where that boundary text lives, are not `_REVISION_SOURCES` members |
| `hardening-search-sanitization` US-001 | `b0ca8d9a…aed73` | **the first rotation that changes sanitization behaviour.** `orchestrator.py` gained `_scan_forms_for_search_text`, which returns `(wire_form, scan_form)` for `title` and `snippet`: the scan form keeps line breaks so Stage 2's `^System:` / `^POPPY:` / `^assistant:` patterns fire on any line, and the wire form is its whitespace collapse. Two entity decode levels before the scan, two control strips (one before the parser for raw bytes, one after the decodes), truncation once on the scan form, and a `_SEARCH_PARSER_INPUT_MULTIPLIER * max_length` parser-input bound. `orchestrator.py` is the only hashed file that moved, measured from a clean tree |
| `hardening-search-sanitization` US-002 | `42485686…ec17f` | **the second rotation that changes sanitization behaviour.** `orchestrator.py`'s `_canonicalize_search_url` became `_SEARCH_URL_RULES`, an ordered registry of named pure rule functions run over the **raw** provider URL, first rejection wins, returning a frozen `SearchUrlOutcome` that carries the omission reason and a closed `SearchUrlRule` log token: presence/length (rejection, never truncation, at 2 048 characters), raw character class (controls, whitespace and RFC 3986 excluded characters rejected, never deleted), parse (`urlsplit` and the `parsed.port` read each in their own `try`), host code points (WHATWG forbidden set, IPv6 colons exempt, `%25` zone id its own token), then a structural scan of **both** `html.unescape(value)` and `unquote(html.unescape(value))`. `_sanitize_search_text` — which routed the URL through `extract_html`, and so ate tag-shaped text before the scan saw it — is deleted. `orchestrator.py` is the only hashed file that moved, measured from a clean tree |
| `hardening-search-sanitization` US-004 | `05dbbb5c…82c0b` | contract `1.3.0` — **two** hashed files: `contract.py` (`OMIT_BLOCKED_URL`, the version bump) and `orchestrator.py` (`_MAX_SEARCH_ENGINE_LENGTH = 64`, routing `SearchResult.engine` through the same `_normalize_search_text` call `title`/`snippet` already use). Bounds and normalizes a field rather than scanning one, so **not** a third rotation that changes sanitization behaviour; both-reverted control reproduces `42485686…ec17f`, measured |
| `hardening-search-sanitization` US-003 | `840c78fa…ee4be` | the **search-time URL audit**, and the first rotation that adds *inputs*. Two hashed files — `orchestrator.py` (rules (3a)–(3c) of `_SEARCH_URL_RULES`, `SearchHostClass`, `domain` from `CanonicalHost.host`) and `contract.py` (the `1.3.0` continuation line) — plus two new inputs: repo-root `url_validator.py` as `_ROOT_REVISION_SOURCES` and `idna@<version>`. All four measured alone; the control that reverts both files *and* removes both inputs reproduces `05dbbb5c…82c0b`. **Changes sanitization behaviour** (the third of the epic) |
| `hardening-search-sanitization` validation fix | `6f0fa2de…66671` | **the fourth rotation that changes sanitization behaviour.** The `/search` scan loop now scans **both** forms of each text field, not only the newline-preserving one. Two of the twenty-four Stage 2 patterns carry no `re.DOTALL`, so a payload split across a newline scanned clean on the scanned form and blocked on the served one — a bypass US-001 introduced and spec-level validation caught. `orchestrator.py` alone; the revert reproduces `840c78fa…ee4be`. |
| `hardening-retrieve-parity` US-001 | `e55b5f06…4d3c0` | **not** a behaviour-changing rotation. `run_retrieve_pipeline` gained five keyword-only dependencies (`settings`, `retrieve_metrics`, `classification_semaphore`, `extraction_settings` required; `admission` defaulted for US-002 only) plus the character pre-check that refuses an over-budget fetched page `content_too_large` / `promptguard_budget`; `contract.py` gained `PROMPTGUARD_BUDGET` and the `1.3.0` continuation line. Two hashed files, each reverted in turn; the both-reverted control reproduces `6f0fa2de…66671`. The shipped default `retrieve.max_promptguard_chunks: 0` means no pre-check at all, so no served byte moves. |
| `hardening-retrieve-parity` US-006 | `d0433876…fc88e` | **not** a behaviour-changing rotation. `orchestrator.py` gained `_bounded_permit` (the one place `asyncio.timeout` and `semaphore.acquire()` appear), the two defaulted classification parameters on `sanitize_and_structure` and `run_search_pipeline`, the `/extract` file route's acquisition moving inward to the stage-3 seam, and step 8's refusal to cache a wait-timeout body; `stage3_promptguard.py` gained the pure `unavailable_result` seam; `contract.py` gained the `1.3.0` continuation line. **Three** hashed files, each reverted in turn; the all-reverted control reproduces `e55b5f06…4d3c0`. No sanitization behaviour moved — what moved is when stage 3 runs and what happens when the permit wait expires. |
| `hardening-retrieve-parity` US-002 | `f654be77…c92fb` | **not** a behaviour-changing rotation. Two hashed files, each reverted alone (`orchestrator.py` → `16b9631f…`, `contract.py` → `646b4f27…`), both-reverted control landing exactly on `d0433876…`. `orchestrator.py`: `extract_html`, `scan_structural` and `structure_sanitization_result` moved onto `asyncio.to_thread`, `admission` became a required `AdmissionSlot` acquired after the cache read and released in `finally` after stage 1, and `fetch_result` / `html_text` are deleted before the classification wait; `contract.py`: `busy` in `RetrieveErrorCode`, `RETRIEVE_ADMISSION_QUEUE_FULL`, the `1.3.0` continuation line. `stage4_structuring.py` untouched. |
| `hardening-retrieve-parity` US-003 | `464b6ad5…fead2` | **not** a rotation that changes how text is sanitized, but it moves a served outcome at the shipped defaults. Two hashed files, each reverted alone (`orchestrator.py` → `a018345e…`, `contract.py` → `80b39055…`), both-reverted control landing exactly on `f654be77…`. `orchestrator.py`: fetched PDFs go through `asyncio.to_thread(extract_pdf_bytes_in_subprocess, …)` inside the admission slot, retaining ownership through cleanup under repeated task cancellation, mapped most-specific first to `content_too_large` / `promptguard_budget` or `extraction_failed` with four reasons; `contract.py`: `extraction_failed` in `RetrieveErrorCode`, the `RETRIEVE_PDF_*` literals, the `1.3.0` continuation line. Supersedes the unaccepted `6fd320da…` candidate's cancellation bug. `pdf_subprocess.py` (`spool_dir()`, the bytes entry point) is not hashed. A PDF within bounds serves identical text; one over 114,688 characters or the worker's rlimits is now refused 422 rather than served or answered 500. |
| `hardening-retrieve-parity` US-004 | `664ee603…c04b` | **not** a sanitization-behaviour change. Only `contract.py`'s 1.3.0 continuation line for `cache.corrupt_entries` moves the hash; its read-only whole-file revert reproduces `464b6ad5…fead2` exactly. `cache.py`'s guarded parse and `retrieval_app.py`'s metrics mirror/emission are not hashed. Invalid cached JSON/schema is counted, logged without payload bytes, deleted and treated as a miss rather than a 500; parse success is still not authenticity. |
| `hardening-retrieve-parity` US-005 | `d98f7dbe…69359` | **not** a sanitization-behaviour change at shipped defaults. Only `contract.py`'s 1.3.0 continuation line for the three effective-policy fields moves the hash; its read-only whole-file revert reproduces `664ee603…c04b` exactly under default and shipped config. Request replacement and post-pipeline stamping in `retrieval_app.py`, and the fields in `models.py`, are not hashed. Opt-in bounds reach the fingerprint through the replaced request, never a parallel pipeline kwarg; trusted-tier skip and VERIFIED fail-open remain exempt. |
| `hardening-retrieve-parity` validation (`fe211e3`) | `5a470872…bf623` | Twenty-sixth: `orchestrator.py` and `stage3_promptguard.py` gained cancellation ownership, absolute fetch deadline and timeout accounting; not a sanitization-algorithm change. Read-only pre-validation control reproduces `d98f7dbe…`. |
| `hardening-hostname-and-config` US-001 | `328d386c…93286` | Twenty-seventh, **fifth sanitization-behaviour change**: directional matching and canonical private-name precedence. `orchestrator.py`, `contract.py`, and already-hashed root `url_validator.py` each move the revision; all-reverted control reproduces `5a470872…` under default and shipped config. Leading-dot trust skips classification for subdomains; multi-label denylists block them. |
| `hardening-hostname-and-config` US-007 | `c8a907cf…546b8` | Twenty-eighth, **sixth policy-driven sanitization-behaviour change**: over-budget allowlist tails can no longer grant trust, and denylists are refused whole; in-budget matching/text scanning remain unchanged. `orchestrator.py` removes the entry pass, merges operator-first and counts wildcard resolutions; `contract.py` announces counters/reason; already-hashed `url_validator.py` removes its pass and safely sizes surrogate escapes before rejecting them. All three individual reversals were measured; all-reverted reproduces `328d386c…` under default and shipped config. |
| `hardening-hostname-and-config` US-002 | `de1cea65…6be91` | Twenty-ninth, **seventh policy-driven sanitization-behaviour change**: `/search` merges the operator seed list first, then canonical `blocked_domains=` entries, and omits matches after URL auditing but before content scans. The existing blocked outcome emits `blocked_url` and `host_class=policy_blocklist`; raw sufficiency prevents paid fallback. Only `orchestrator.py` and `contract.py` move; both individual read-only reversals were measured and both-reverted reproduces `c8a907cf…` under default and shipped config. The empty-seed/no-new-field baseline is unchanged. |
| `hardening-hostname-and-config` US-005 | `e00049c4…7ed5c` | Thirtieth, **eighth policy-driven sanitization-behaviour change**, for tuned deployments: both fetch routes default from validated config before the ceiling, with a new caller threshold on search. `orchestrator.py` passes a required resolved float to classification and cache fingerprint; `contract.py` announces the additions/defaults. Only these two hashed files move; individual read-only reversals measured, both-reverted reproduces `de1cea65…` under default and shipped config. Shipped 0.85 behavior, text-scanning algorithms, raw configured hash input and `/extract`'s raw guard remain unchanged. |
| `hardening-cache-integrity` US-001 | `aa288bc5…5b39c` | Thirty-first, **not a text-sanitization change**. Only `contract.py` moves, announcing `cache.integrity_rejects` and widened `storage_oversize_skips` producers. Read-only reversal against clean `b79504d` reproduces `e00049c4…` under default and shipped config; all eight other sources are unchanged. The HMAC/bounds and wiring are in unhashed root modules. Old keys are orphaned; full measurements in `docs/bootstrap-notes.md`. |

Poppy's in-tree copy stayed on the original value throughout. Four of the eight sources (audit-measured 2026-09-11: contract.py, stage1_extraction.py, stage2_structural.py and orchestrator.py all differ now; an earlier count said five)
are still byte-identical between the repos; the revision is not.

**Twenty-two of the thirty rotations changed no sanitization policy or algorithm; the
fifteenth, sixteenth, eighteenth and nineteenth (`hardening-search-sanitization`
US-001, US-002, US-003 and its validation fix) and the twenty-seventh
through thirtieth (`hardening-hostname-and-config` US-001, US-007, US-002 and US-005) are the eight
that did, and the seventeenth
(US-004, contract `1.3.0`) does not join them** — hostname policy can now skip
classification on an opted-in trusted suffix; search-sanitization US-001's
is that `/search` scans `title` and `snippet` newline-preserved now, so line-anchored Stage 2
patterns fire on any line rather than at character 0 only, and a rising `structural_blocked`
after it is expected; US-003's is that `/search` now drops results whose host is a private,
embedded-private or blocklisted one and serves `domain` as the canonicalised ASCII host;
the validation fix scans both the newline-preserving and collapsed wire forms.
Hostname/config US-002 closes search's bypass of operator and caller domain
policy without changing raw-result sufficiency or the text-scanning algorithm.
US-005 shares configured threshold policy across both fetch routes, before the
operator ceiling; its behavior change is for tuned deployments, not shipped 0.85.
US-004 bounds and normalizes `SearchResult.engine` without routing it through that same scan.
Among the other twenty-two, the fourth and fifth
are different *kinds* of rotation and worth reading as such. The first three moved because
the hash is over bytes and someone reformatted or retyped a hashed file. The fourth moved
because an **input changed**: weights are a runtime, per-deployment thing now
(`FORAGE_MODEL_REVISION`), so two containers running identical code can scan with
different weights, and the identity had to grow the revision to stay honest. All eight
sources are byte-identical across it, verified by re-deriving with the old formula.

The fifth is the first one where the invalidation is the **objective** rather than the
price. `contract.py`'s docstring has always said a contract change must invalidate cached
extractions; nothing inside Forage enforced it, because `cache_policy_fingerprint()` mixed
in every caller-supplied policy knob but not the pipeline's own revision. US-003 made the
revision an input to that fingerprint, so the bump to contract `1.1.0` is also the first
rotation that actually flushes Forage's own cache. Read it that way: a rotation is now a
deliberate cache-invalidation lever, not only a hash that happened to move.

That is exactly why *when* you take a rotation matters. Any edit to a `_REVISION_SOURCES`
file rotates the revision and invalidates every cached sanitization keyed on it. Do it
deliberately, at a boundary, with the before/after recorded (as
`docs/bootstrap-notes.md` does) — never as a drive-by inside a behavioural change, where
it would be indistinguishable from a real sanitizer change.

The sixth is the fifth's mechanism used as intended, one spec later:
`feature-forage-contract` US-001 added the error vocabulary to `contract.py`, changed no
wire byte (per-site parity tests prove it) and left `CONTRACT_VERSION` at `1.1.0` then — and
the cache flush it causes is the *correct* consequence, not a cost to apologise for.
That spec acknowledged **one** rotation for all five of its stories, and this was it: the
remaining stories must stay out of `_REVISION_SOURCES` or be content-neutral there.

The seventh, eighth and ninth are `search-provider-abstraction`'s, and the ninth is the
one to read: it is the second rotation whose invalidation is part of the *point*. The
contract bump to `1.2.0` added `content_kind` and `date` to every search result, so a
cached extraction sanitized before it carries neither — serving one beside a `1.2.0`
response is exactly the silent mix the revision key exists to prevent. It is also the
epic's only rotation with two hashed files moving, and the attribution was measured
(revert each in turn; the both-reverted control must land on the previous shipped value)
rather than argued. `docs/bootstrap-notes.md` carries that table.

US-006's rotation is the one to read carefully: `stage2_structural.py` took an annotation
only (`field(default_factory=list[FlaggedSpan])`), but `stage1_extraction.py` took a real
refactor — the `<meta>`/JSON-LD strategies now go through `_attr_text()` and
`_json_ld_documents()`. That refactor is behaviour-preserving *except* for one genuine fix
it made unavoidable: a multi-valued attribute (bs4's `AttributeValueList`) used to raise
`AttributeError` from `tag[name].strip()`, and now falls through to the next strategy.

**Why it matters:**
Any cross-repo work that assumes Poppy↔Forage revision parity will be wrong. The
consuming-side spec must compare contracts, not revisions.

---

### Cancelling a PDF await does not stop its worker thread

`asyncio.to_thread` cancellation cancels the await, not the running thread. A
`finally: await admission.release()` around that await alone therefore frees capacity
while a PDF worker and its content-bearing spool remain live. A stub that raises
`CancelledError` synchronously cannot test this: it unwinds the thread normally.

`hardening-retrieve-parity` US-003 keeps the fetched-PDF task and waits without forwarding
cancellation to it (`asyncio.wait`), deferring even repeated cancellation until the
existing bounded worker is reaped and the spool unlinked. The worker's outcome is retrieved
(and a spool fault still logs `retrieve_spool_error`), then pending cancellation propagates.
No classification or cache write follows a cancelled parse. The regression blocks the
path worker with a threading event, calls the actual pipeline task's `cancel()`, refuses
replacement admission, and checks cleanup before cancellation completes. This is separate
from the pre-existing queued-waiter handoff residual below; neither the controller nor
`/extract`'s cancellation behavior changes here.

### The admission controller's handoff leaks a slot on a racing cancellation

The controller's `release()` does a **handoff**: it pops the first waiter, sets its result and returns *without* decrementing `_active`, the woken waiter inheriting the slot; `acquire()`'s `except BaseException` restores accounting only for a waiter still in `_waiters`. So a waiter cancelled after its grant loses the slot — and the window is wider than that: `Task.cancel()` marks the awaited future done at once, so a waiter cancelled while still **queued** has `waiter.done()` before its own `except` runs, and if the holder's `release()` takes the lock in that window it pops the cancelled future, decrements `_queued_bytes`, skips `set_result` and returns without decrementing `_active`; the woken task then finds itself gone from `_waiters` and restores nothing. Net: `active == limit` with nobody holding a slot, and at `fetch_concurrency: 1` one occurrence wedges `/retrieve` for the life of the process — reachable by a plain queued cancellation racing a normal release, not only by a post-grant cancellation. It is latent on `/extract` (the route ships disabled) and reachable on `/retrieve` only by task cancellation — server shutdown; Starlette does not cancel a handler task when an HTTP client disconnects — and never by a timer, because no timer wraps `acquire()` (`hardening-retrieve-parity` US-002 declined one for exactly this reason). **Accepted residual, pre-existing, not fixed here.** Fix direction: make the handoff idempotent — `release()` always decrements, and the woken waiter re-increments under the lock. Open question for the resource-envelope spec.

**Why it matters:** never wrap `ExtractionAdmissionController.acquire()` in a timer
(`asyncio.timeout`, `wait_for`) until the handoff is fixed — a timeout firing in the grant
window is the reliable way to reach the leak. The `/retrieve` tests pin the *safe*
interleaving (the cancelled waiter runs its `except` before the holder releases) and say so;
the racing one is not deterministic and is not a tested property. `retrieve.semaphore_saturation`
climbing while `retrieve.requests` flatlines after a shutdown-less cancellation is the shape.

---

### The test suite is hermetic on purpose — don't relax the socket guard

**Location:** `tests/conftest.py`
**Severity:** 🟡 Medium
**Added:** 2026-09-07 (forage-repo-bootstrap US-004)

**What happens:**
An autouse `pytest-socket` fixture blocks real network access for every test
(`allow_unix_socket=True`, because asyncio's event-loop self-pipe needs it). A test that
reaches the network fails with `SocketBlockedError` instead of passing slowly and flakily.

**Why it matters:**
Poppy's equivalent guard lived in a `conftest.py` that did **not** move; porting it was a
deliberate act. It immediately caught three real DNS calls in the cache tests.

**Removing it used to be silent — since `forage-ci-and-image` US-002 it is not.**
`tests/test_hermeticity.py` is an executing canary: eight tests that assert TCP, UDP,
IPv6, `getaddrinfo`, `gethostbyname` and `create_connection` all raise
`SocketBlockedError` while `AF_UNIX` socketpairs and async tests still work. Delete the
fixture, drop `autouse`, or lose the Unix-socket exemption and those tests go red.
`TestHermeticityCanaryIsEnforced` in `tests/test_ci_workflow.py` closes the one gap a
canary cannot close about itself — that it is committed and not skipped — and it lives in
a different module for exactly that reason.

**If a new test needs the network:** it doesn't. Mock at the seam (httpx, `getaddrinfo`,
transformers/torch).

---

### `uv.lock` must stay CPU-pinned — `grep nvidia- uv.lock` has to come back empty

**Location:** `pyproject.toml` (`[[tool.uv.index]]` + `[tool.uv.sources]`), `uv.lock`
**Severity:** 🟡 Medium
**Added:** 2026-09-07 (forage-repo-bootstrap US-004)

**What happens:**
Resolving torch from plain PyPI drags in **15 `nvidia-*` CUDA packages (~2.7 GB)** that no
Forage lane needs. The pinned `pytorch-cpu` index plus a `sys_platform == 'linux'` marker
keeps them out.

**Why it matters:**
The *image* is unaffected — its Dockerfile pip-installs from the CPU index and never reads
the lock. The cost lands entirely on developer `uv sync`s and every CI job. Re-locking
without the index config silently reintroduces it.

**Check after any dependency change:** `grep nvidia- uv.lock` → no output.

---

## Historical / Closed

### The Dockerfile bakes an HF token — build-arg recoverable via `docker history`

**Was:** `Dockerfile` (`ARG HF_TOKEN` + the conditional `from_pretrained` bake block)
**Closed:** 2026-09-07, `forage-ci-and-image` US-003 — **on the Forage side only,** read the
two carve-outs below before treating this as finished.

The token used to reach the image as a Docker **build ARG**, which made it readable from
the built image's layer history via `docker history --no-trunc` — and from any registry
the image was pushed to — even though it never appeared in the running container's
environment. It was *the* blocker on publishing a Forage image and the reason this
repository is private.

**What closed it.** US-003 deleted the path outright rather than working around it: no
`ARG HF_TOKEN`, no bake step, no credential of any kind in the build. Two mechanical
guards keep it deleted, and both run on every PR:

| Guard | Scope | Where |
|---|---|---|
| `tests/test_dockerfile.py` (20 tests) | the Dockerfile's **text** — no secret-shaped ARG/ENV/RUN assignment, no `from_pretrained`, no token-shaped literal | the `test` lane, and every local `uv run pytest` |
| the `secret-grep` CI job | the **built image's** `docker history --no-trunc`, for `HF_TOKEN`, `hf_[A-Za-z0-9]{20,}` and `FORAGE_BRAVE_API_KEY` | `.github/workflows/ci.yml`, on the artifact `build-amd64` produced |

Both were mutation-verified: re-adding `ARG HF_TOKEN` fails three of the source guards,
and a deliberately-leaking canary image built with a synthetic token matched both Hugging
Face grep patterns.

**Two things this closure does NOT say.**

1. **It is not permission to push.** The image is secret-free, and since US-007
   (2026-09-08) it does get pushed — but only through the `publish` lane, behind all six
   gates and a check that the published amd64 layers are the ones `smoke` executed. The
   repository and its packages have been PUBLIC since US-008's 2026-09-10 flip. "No longer
   a leak", "gated" and "public" are three different claims; see `docs/releases.md`.
2. **Poppy's copy is still armed.** `services/retrieval/Dockerfile` in the Poppy monorepo
   still carries `ARG HF_TOKEN`; the two copies coexist until spec 6 pins Poppy to a
   published Forage image. The Poppy-side closure of this gotcha rides that spec, and
   the rule there is unchanged: never push an image built from *that* Dockerfile.

The consequence of the deletion — a weights-free image — is live and tracked separately in
the PromptGuard entry above, until `feature-forage-model-bootstrap` adds the runtime fetch.

---

### `sys.modules['app']` collision

The service module was renamed `app.py` → `retrieval_app.py` in Poppy (2026-08-04) to
avoid a `sys.modules['app']` collision with the host application. In a standalone repo the
collision cannot occur, but the flat filename is load-bearing for the Dockerfile and for
`sanitizer_revision`'s hashed paths — **do not rename it back.**

## Bare `.venv/bin/pyright` reports 34 phantom errors — always `uv run pyright`

**Severity: medium** · **Found: 2026-09-13 (cache-fallback US-003)**

Running the project's *own* venv binary directly (`./.venv/bin/pyright`) reports exactly
34 errors that `uv run pyright` (0 errors, the CI gate) does not — foreign stubs resolve
differently outside the uv environment. CONVENTIONS.md's "never a system-installed
binary" rule reads as if the project venv binary were safe; it is not. Reproduced
independently in a clean clone at verification. The only supported invocations are
`uv run pyright` locally and the CI typecheck job.

## A cold-cache publish fails its own parity gate — and poisons the publish scope

**Severity: high** · **Found: 2026-09-11 (v0.9.2-rc incident)** · **Durable fix landed
2026-09-11 (`feature-forage-contract` US-004); the recovery below still applies if a
publish ever fails again**

The image build was not byte-reproducible: the apt, uv-sync and COPY layers embedded
timestamps, so a rebuild only matched the gated tarball when buildx reused cached layers.
Three publishes passed warm; the first tag cut hours after its commit's main build (cache
evicted by PR churn) failed the diff_ids parity gate twice — correctly. Worse, the failed
rebuild wrote its wrong layers to the `publish` GHA-cache scope, after which even
main-push publishes failed: publish's `cache-from` prefers its own scope. **Recovery that
worked, and is still the recovery:** delete the `index-publish-*` cache entries (`gh cache
list/delete`) so publish falls through to the gated `buildkit` scope, then re-run. The
withdrawn-tag rule lives in `docs/releases.md`.

**The fix, and why it is in two files.** `SOURCE_DATE_EPOCH` (the commit's committer date)
plus `rewrite-timestamp=true` on every building job's exporter rewrites the file
timestamps in every exported layer to one value. That is necessary and **not sufficient**:
it rewrites the layer tar's *headers*, and this image also wrote timestamps *inside* files,
where no exporter can reach them —

| Inside-the-file timestamp | Fix, in the Dockerfile |
|---|---|
| `/var/log/apt/{history,term}.log`, `/var/log/dpkg.log`, `/var/cache/ldconfig/aux-cache` | removed in the same `RUN` as the install |
| ~580 `.pyc` written by the build-time import check, each recording its source's mtime | `PYTHONDONTWRITEBYTECODE=1` on that `RUN` |

Measured, two `--no-cache` builds from two checkouts with different file mtimes, layers
compared one at a time: **7 of 19 layers identical** with neither half, **16 of 18** with
the exporter attribute alone, **19 of 19** with both. `tests/test_ci_workflow.py::
TestReproducibleExports` and `tests/test_dockerfile.py::TestReproducibleImageContents`
hold each half, because both look like tidy-up-able noise to someone who does not know
this entry exists.

**Two things this does not say.** The image no longer ships a bytecode cache: importing
the app costs ~2.3 s instead of ~0.9 s at container start (it shipped one before
`rewrite-timestamp`, and that cache is *void* under timestamp rewriting anyway, so the
choice was between dead files and no files). And reproducibility is bounded by one UTC
day: `useradd` stamps a day count into `/etc/shadow`, so two builds either side of
midnight differ in that layer. The parity gate compares two builds of one run and is
unaffected; a "same digest a year later" claim would be, and is not made.

**Still unproven:** a live cold publish. The evidence above is local, plus the
workflow-shape tests. The first tag cut after this change is the first real exercise —
watch `publish`'s verification step rather than only the tags.

### `kit_tools/hooks/*.py` sit inside the zero-tolerance ruff / pyright gates

The KitTools automation hooks are plain Python files under `kit_tools/hooks/`, and
`uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` (strict) walk
the whole tree with **no excludes** (`CLAUDE.md` Development section;
`tests/test_pyright_policy.py` pins the pyright config exactly, so an `exclude` is not an
option). The plugin ships them unannotated: committing them as copied (2026-09-15,
`41e01d8`) turned all three CI gates red until they were annotated and wrapped
(`fix(kit_tools): make hook scripts pass ruff and pyright strict`). Two consequences:

- `ruff format --check .` also reflows Python fences inside Markdown under `kit_tools/`,
  so a seeded doc with a code block can fail the format gate on its own.
- Re-running `/kit-tools:init-project` re-copies the plugin's hook scripts *unconditionally*
  (hooks are installed regardless of the skip/merge/replace choice) and will reintroduce the
  breakage. After any re-run, run the three gates before committing, or upstream the
  annotated versions into the kit-tools plugin so the copies arrive clean.
