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

### The pre-CI Dockerfile bakes an HF token — never push its image anywhere

**Location:** `Dockerfile` (`ARG HF_TOKEN`)
**Severity:** 🔴 High
**Added:** 2026-09-07 (travelled from Poppy at extraction)

**What happens:**
The Hugging Face token reaches the image as a Docker **build ARG**, which makes it
readable from the built image's layer history via
`docker history --no-trunc forage:<tag>` — and from any registry the image is pushed to —
even though it never appears in the running container's environment.

**Why it matters:**
This is *the* blocker on publishing a Forage image, and it is why this repository is
**private** until `feature-forage-ci-and-image` lands. An image built *with* a token is a
leaked token the moment it is pushed.

**The rule, until the build-arg path is gone:**
Never push an image built from this Dockerfile to any registry, public or private.
Local builds and local runs are fine.

**Resolution:** `feature-forage-model-bootstrap` replaces bake-at-build with
download-at-start; `feature-forage-ci-and-image` removes the build-arg path and gates
publishing. When both have landed, this entry closes — and the public flip may proceed.

---

### PromptGuard model absent → the service reports **degraded**, and you must treat it as unscanned

**Location:** `promptguard/classifier.py`, `pipeline/stage3_promptguard.py`, `/health`
**Severity:** 🔴 High
**Added:** 2026-09-07 (travelled from Poppy; post-Epic-1 wording)

**What happens:**
Stage 3 runs the gated `meta-llama/Llama-Prompt-Guard-2-22M`. A token-less build produces
an image with no weights. Forage then reports `status: "degraded"` with
`promptguard_unavailable` in `degraded_reasons` and `promptguard_loaded: false`, and the
ML injection scan does not happen. Stage 2's deterministic regex pass still runs; stage 3
does not.

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

**Verify after any rebuild:**
`docker logs <container> | grep -i promptguard` → "model loaded", and
`curl -s localhost:8020/health | jq .promptguard_loaded` → `true`.

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
Forage publishes a companion `forage-searxng` image (pinned base + baked config) in
`feature-forage-ci-and-image`; **that CI cadence is what keeps this from recurring**, and
a bump job that stops running re-arms the gotcha. Rolling our own search image was
considered and rejected — the community maintaining engine definitions weekly *is* the
value.

**Also note:** the engine list appears in two places — `pipeline/orchestrator.py`'s engine
constant and `searxng/config/settings.yml`. Both live in this repo now; keep them in sync.

---

### `sanitizer_revision` has deliberately diverged from Poppy's

**Location:** `pipeline/sanitizer_revision.py`
**Severity:** 🟡 Medium
**Added:** 2026-09-07 (forage-repo-bootstrap US-002)

**What happens:**
`derive_sanitizer_revision()` hashes eight source files. The vault-free configuration work
edited `pipeline/orchestrator.py`'s default-hostname lines, moving Forage's revision from
`e6b2b56d…` to `2b8d7e9a…`; installing the `ruff format --check` CI gate then reformatted
`pipeline/stage2_structural.py` — also one of the eight — moving it again to
`cd00a8b4…c96b9a`. Poppy's in-tree copy stayed on the original value throughout. Six of
the eight sources are still byte-identical between the repos; the revision is not.

**Note the second rotation was format-only.** No sanitization behaviour changed — the hash
is over bytes, so `ruff format` moves it. Any reformat of a `_REVISION_SOURCES` file
rotates the revision and invalidates every cached sanitization keyed on it. Do it
deliberately, at a gate boundary, with the before/after recorded (as
`docs/bootstrap-notes.md` does) — never as a drive-by inside a behavioural change, where
it would be indistinguishable from a real sanitizer change.

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
deliberate act. It immediately caught three real DNS calls in the cache tests. Removing it
would silently un-hermeticize the suite and the loss would not show up as a failure.

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

### `sys.modules['app']` collision

The service module was renamed `app.py` → `retrieval_app.py` in Poppy (2026-08-04) to
avoid a `sys.modules['app']` collision with the host application. In a standalone repo the
collision cannot occur, but the flat filename is load-bearing for the Dockerfile and for
`sanitizer_revision`'s hashed paths — **do not rename it back.**
