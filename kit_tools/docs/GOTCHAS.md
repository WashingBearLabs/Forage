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

**Verify after any rebuild:**
`docker logs <container> | grep -i promptguard` → "model loaded", and
`curl -s localhost:8020/health | jq .promptguard_loaded` → `true`. Until spec 3 lands,
expect `false` from a stock image and treat the content as unscanned — that is the honest
answer, not a broken deploy.

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

1. **It is not permission to push.** The image is secret-free, but publishing still runs
   through US-007's gated lane, and the repository and its packages stay private until
   US-008's human flip. "No longer a leak" and "ready to publish" are different claims.
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
