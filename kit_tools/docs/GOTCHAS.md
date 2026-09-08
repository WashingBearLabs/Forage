<!-- Template Version: 2.0.0 -->
# GOTCHAS.md

> Last updated: 2026-09-07
> Updated by: Claude (forage-repo-bootstrap US-005)

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
`forage-ci-and-image` US-003 every image is weights-free** — the build-time bake is gone
and the runtime fetch that replaces it is `feature-forage-model-bootstrap`, which has not
landed — so this is the *default* state of a built image, not an accident of a forgotten
token. Forage reports `status: "degraded"` with `promptguard_unavailable` in
`degraded_reasons` and `promptguard_loaded: false`, and the ML injection scan does not
happen. Stage 2's deterministic regex pass still runs; stage 3 does not.

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
`docker logs <container> | grep -i promptguard` → "model loaded", and
`curl -s localhost:8020/health | jq .promptguard_loaded` → `true`. Until spec 3 lands,
expect `false` from a stock image and treat the content as unscanned — that is the honest
answer, not a broken deploy. The refusal is a `GatedRepoError` (HTTP 401) from
`huggingface.co`, which fails in seconds rather than hanging; the app is serving within
~6 s of `docker run` even under emulation.

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
`derive_sanitizer_revision()` hashes eight source files. Forage's revision has moved three
times, each time at a gate boundary and each time deliberately:

| When | Value | What moved it |
|---|---|---|
| At split | `e6b2b56d…` | — (identical to Poppy) |
| `forage-repo-bootstrap` US-002 | `2b8d7e9a…` | vault-free hostname defaults in `orchestrator.py` |
| `forage-ci-and-image` US-001 | `cd00a8b4…` | `ruff format` gate reformatted `stage2_structural.py` |
| `forage-ci-and-image` US-006 | `0537316d…e3e253` | pyright-strict burn-down retyped `stage1_extraction.py` + `stage2_structural.py` |

Poppy's in-tree copy stayed on the original value throughout. Five of the eight sources
are still byte-identical between the repos; the revision is not.

**None of the three rotations changed sanitization behaviour.** The hash is over bytes, so
a reformat or a type annotation moves it just as a real rule change would — and that is
exactly why *when* you take a rotation matters. Any edit to a `_REVISION_SOURCES` file
rotates the revision and invalidates every cached sanitization keyed on it. Do it
deliberately, at a gate boundary, with the before/after recorded (as
`docs/bootstrap-notes.md` does) — never as a drive-by inside a behavioural change, where
it would be indistinguishable from a real sanitizer change.

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
   repository and its packages stay private until US-008's human flip. "No longer a leak",
   "gated" and "public" are three different claims; see `docs/releases.md`.
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
