<!-- Template Version: 2.0.0 -->
# GOTCHAS.md

> Last updated: 2026-09-11
> Updated by: Claude (forage-contract US-001)

## Overview

Known issues, quirks, and non-obvious behaviours. Landmines to avoid.

The first three entries **travelled with the code** out of Poppy's `kit_tools/docs/GOTCHAS.md`
at extraction time — they are properties of this service, not of the monorepo it used to
live in, and losing them in the move was an identified risk.

---

## Active Gotchas

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

**Also note:** the engine list appears in two places — `pipeline/orchestrator.py`'s engine
constant and `searxng/config/settings.yml`. Both live in this repo now, and
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
`derive_sanitizer_revision()` hashes eight source files plus the model identity and the
active threshold. Forage's revision has moved six times, each time at a boundary and each
time deliberately:

| When | Value | What moved it |
|---|---|---|
| At split | `e6b2b56d…` | — (identical to Poppy) |
| `forage-repo-bootstrap` US-002 | `2b8d7e9a…` | vault-free hostname defaults in `orchestrator.py` |
| `forage-ci-and-image` US-001 | `cd00a8b4…` | `ruff format` gate reformatted `stage2_structural.py` |
| `forage-ci-and-image` US-006 | `0537316d…e3e253` | pyright-strict burn-down retyped `stage1_extraction.py` + `stage2_structural.py` |
| `forage-model-bootstrap` US-001 | `5927038d…19d111` | the hashed model identity became `MODEL_ID@revision` — **no source byte moved** |
| `forage-cache-fallback` US-003 | `fa4691c5…93547c` | `contract.py` bumped to `1.1.0` **and** `orchestrator.py` threaded the revision into the cache key — the rotation whose *point* is the invalidation |
| `forage-contract` US-001 | `8b1b7f78…196d7c` | `contract.py` gained the 17-code error vocabulary and `DegradedReason` as derived Literals — documentation only, contract still `1.1.0`, and the **one** rotation the whole of `feature-forage-contract` gets |

Poppy's in-tree copy stayed on the original value throughout. Four of the eight sources (audit-measured 2026-09-11: contract.py, stage1_extraction.py, stage2_structural.py and orchestrator.py all differ now; an earlier count said five)
are still byte-identical between the repos; the revision is not.

**None of the six rotations changed sanitization behaviour** — but the fourth and fifth
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
wire byte (per-site parity tests prove it) and left `CONTRACT_VERSION` at `1.1.0` — and
the cache flush it causes is the *correct* consequence, not a cost to apologise for.
That spec acknowledged **one** rotation for all five of its stories, and this was it: the
remaining stories must stay out of `_REVISION_SOURCES` or be content-neutral there.

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
| the `secret-grep` CI job | the **built image's** `docker history --no-trunc`, for `HF_TOKEN` and `hf_[A-Za-z0-9]{20,}` | `.github/workflows/ci.yml`, on the artifact `build-amd64` produced |

Both were mutation-verified: re-adding `ARG HF_TOKEN` fails three of the source guards,
and a deliberately-leaking canary image built with a synthetic token matched both grep
patterns.

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

**Severity: high** · **Found: 2026-09-11 (v0.9.2-rc incident)** · **Spec-4 blocker on v1.0.0**

The image build is not byte-reproducible: the uv-sync/COPY layers embed timestamps, so a
rebuild only matches the gated tarball when buildx reuses cached layers. Three publishes
passed warm; the first tag cut hours after its commit's main build (cache evicted by PR
churn) failed the diff_ids parity gate twice — correctly. Worse, the failed rebuild wrote
its wrong layers to the `publish` GHA-cache scope, after which even main-push publishes
failed: publish's `cache-from` prefers its own scope. Recovery that worked: delete the
`index-publish-*` cache entries (`gh cache list/delete`) so publish falls through to the
gated `buildkit` scope, re-run, then cut tags immediately after a green main build.
The durable fix (reproducible builds via SOURCE_DATE_EPOCH-style normalization, or pushing
the gated artifact for the amd64 leg) is spec 4's opening work — `v1.0.0` must not depend
on warm-cache ritual. The withdrawn-tag rule lives in `docs/releases.md`.
