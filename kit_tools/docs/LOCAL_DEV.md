<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: tech-stack, infrastructure
  required_sections:
    - "Prerequisites"
    - "Quick Start"
  skip_if: never
-->
# LOCAL_DEV.md

> **TEMPLATE_INTENT:** Complete local development setup guide. Get a new developer running quickly.

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-cache-integrity US-003)

---

## Prerequisites

Forage is a single uv-managed Python project with no database and no cloud dependency.
Nothing beyond Python and uv is needed to run the test suite; Docker is needed only for
the container path, which is the documented way to *run* the service.

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12 (`requires-python = ">=3.12"` in `pyproject.toml`) | There is no `.python-version`; uv picks or downloads a managed 3.12 during `uv sync`. The image runs `python:3.12-slim` (digest-pinned). |
| uv | 0.9.28 is the tested version | The same version is pinned in CI (`UV_VERSION`), in the `Dockerfile` (`COPY --from=ghcr.io/astral-sh/uv:0.9.28`), and created the local `.venv`. `pyproject.toml` declares no `required-version`, so a newer uv that reads `uv.lock` (lock revision 3) should work, but 0.9.28 is the only one exercised anywhere. |
| Docker with the `docker compose` plugin | no version pinned | For `docker build`, `docker run`, the two `compose/` fragments and the SearXNG smoke. CI validates the fragments with `docker compose config`. |

### Optional but Recommended

- `curl` and `jq`: every document in this repo reads `/health` with
  `curl -s localhost:8020/health | jq`. Read the body, never just the status code.
- A Hugging Face read token (`HF_TOKEN`) for the gated `meta-llama/Llama-Prompt-Guard-2-22M`
  repository: optional. Without it the service runs in a supported, loudly degraded mode.
  The walk-through (account, Meta's access approval, token scope) is
  [`docs/weights.md`](../../docs/weights.md) § "Bring your own token".
- `oras` 1.3.4: only for `scripts/vendor_weights.py` (operator re-vendoring of the private
  weights mirror). Never needed for local development; the image installs its own copy.

### macOS vs Linux

- **Where torch comes from.** `[tool.uv.sources]` in `pyproject.toml` routes `torch`
  through the explicit `pytorch-cpu` index (`https://download.pytorch.org/whl/cpu`) with
  `marker = "sys_platform == 'linux'"`. Linux therefore resolves `torch 2.14.0+cpu`; macOS
  resolves the default PyPI `torch 2.14.0`, which is already CPU-only. Either way
  `grep nvidia- uv.lock` must come back empty (see Troubleshooting).
- **Cold sync cost.** `kit_tools/worktree.yaml` budgets "~5 minutes" for a cold
  `uv sync --extra dev` on Linux (the torch CPU wheel is ~152 MiB on aarch64). The macOS
  figure was not measured.
- **Runtime differences on macOS.** `pipeline/pdf_subprocess.py` applies `RLIMIT_AS` to
  the PDF worker only on Linux; the `/metrics` cgroup fields
  (`cgroup_memory_current_bytes`, `cgroup_memory_max_bytes`, `oom_proximity_ratio`) read
  `/sys/fs/cgroup/memory.*` and are `null` off Linux; use `shasum -a 256` wherever a doc
  says `sha256sum`. The test suite is verified green on both macOS and linux/amd64.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/WashingBearLabs/Forage.git
cd Forage

# 2. Environment: creates .venv with runtime + dev extras (~5 min cold on Linux)
uv sync --extra dev

# 3. The four blocking CI gates -- all must be green before you push
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright

# 4. Build and run the container (no token needed; this is the degraded runtime)
docker build -t forage .
docker run --rm -p 127.0.0.1:8020:8020 forage

# 5. In another shell: read the /health BODY, not the status code
curl -s localhost:8020/health | jq
```

After step 4 the service is at `http://127.0.0.1:8020`. `/health` returns HTTP 200 with
`status: "degraded"`, `degraded_reasons: ["promptguard_unavailable"]` and
`promptguard_loaded: false` — that is the correct, honest state for a token-less run
([CLAUDE.md](../../CLAUDE.md) invariant 5), not a broken install. There is no
`.env.example`, no database, and no setup step beyond `uv sync`.

---

## Detailed Setup

### 1. Environment: `uv sync`, and why everything goes through `uv run`

```bash
uv sync --extra dev    # once per checkout or worktree; CI runs the same with --locked
```

`uv.lock` pins the whole toolchain — ruff 0.16.6, pyright 1.1.411, pytest 9.1.1 — and
**every command in this repo runs as `uv run <tool>`**. A system-installed ruff or pyright
reports numbers that do not reproduce in CI, and even the project's own
`./.venv/bin/pyright` run directly reports 34 phantom errors that `uv run pyright` does
not ([GOTCHAS.md](GOTCHAS.md) § "Bare `.venv/bin/pyright` reports 34 phantom errors").
The only supported invocations are `uv run …` locally and the CI jobs.

[`kit_tools/worktree.yaml`](../worktree.yaml) is the environment contract for
orchestrator worktrees: `/kit-tools:execute-epic` runs in a fresh git worktree that does
not share the gitignored `.venv`, and the file tells it `env_bootstrap: uv sync --extra dev`,
`run_prefix: uv run`, `env_link: []`, `path_links: []` (there is no repo-local env file to
link). Keep it accurate — without `run_prefix` the detached orchestrator runs system
Python and reports pytest collection crashes as false regressions.

### 2. Configuration

Forage is 12-factor: configuration is **environment variables at process start plus
`config.yaml`** at the repo root (loaded from beside `retrieval_app.py`; `/app/config.yaml`
in the image; a missing file logs a WARNING and every key falls back to its code default).
Both are read once in the FastAPI lifespan, so a change needs a restart. Nothing is
required for a local run.

The variables you will actually touch locally:

| Variable | Default | Purpose |
|----------|---------|---------|
| `HF_TOKEN` | unset (a supported, degraded mode) | Hugging Face read token for the gated PromptGuard repo. Read only on a cold weights acquisition, never logged. Runtime only — never a build argument. |
| `VALKEY_URL` | **unset** — bounded in-memory content cache | A standard `redis://` URL selects the Valkey backend. Only a *fully unset* value means memory mode; an empty string is "configured and broken" and reports `degraded: cache_unavailable`. May carry a password — supply it from an env file, never inline `-e`. |
| `FORAGE_CACHE_HMAC_KEY` | unset | Optional runtime signing secret for Valkey; without it Valkey is `degraded: cache_unauthenticated`, even if reachable. Memory needs no key. Generate with the [CSPRNG env-file recipe](../../docs/configuration.md#credential-handling-for-forage_cache_hmac_key), never use a passphrase or inline value; stop all replicas before enabling or rotating. |
| `SEARXNG_URL` | `http://searxng:8080` | Base URL of the SearXNG instance behind `POST /search`. The default assumes a compose network with a service literally named `searxng`. |
| `HF_HOME` | `/app/model-cache` (image `ENV`; the same default is `model_fetcher.DEFAULT_CACHE_ROOT`) | Weights cache root. Mount the `forage-model-cache` volume here or the weights are re-fetched on every container recreate. |

`config.yaml` keys worth knowing: `promptguard_threshold` (`0.85`), `extract_route_enabled`
(`false` — `POST /extract` returns 404 until you flip it and restart),
`cache.max_entries` and `cache.max_bytes` (256 entries / 32 MiB, the in-memory bounds),
`cache.max_value_bytes` (4 MiB per value, both backends; range 512 KiB-8 MiB), and
the `extraction.*` limits, which are validated at boot and refuse to start on a bad value.
Override it in the container with `-v "$PWD/config.yaml:/app/config.yaml:ro"`.

Legacy `POPPY_*` names survive only as back-compat aliases —
`POPPY_RETRIEVAL_LEGACY_CAPABILITY` is the deprecated alias of
`FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` — and no new name may contain "poppy"
(CLAUDE.md invariant 1). The full reference — every variable, every `config.yaml` key with
its allowed range, the cache-backend selection table and the weights acquisition sequence
— is [`docs/configuration.md`](../../docs/configuration.md).

### 3. Tests, lint, format, typecheck

```bash
uv run pytest                                  # whole suite, hermetic
uv run pytest tests/test_cache.py -q           # one module
uv run pytest tests/test_cache.py -q -k name   # one test, by -k expression
uv run ruff check .                            # lint -- CI gate
uv run ruff format --check .                   # format check -- CI gate (uv run ruff format . to fix)
uv run pyright                                 # strict, zero errors, no baseline -- CI gate
```

All four are blocking jobs in `.github/workflows/ci.yml` (`lint`, `typecheck`, `test`).
The suite is **hermetic**: an autouse `pytest-socket` guard in `tests/conftest.py`
(`disable_socket(allow_unix_socket=True)`) fails any test that reaches the real network,
and a second autouse fixture clears `HF_TOKEN`, `HF_HOME`, `FORAGE_MODEL_REVISION`,
`FORAGE_WEIGHTS_MIRROR`, `FORAGE_MIRROR_TOKEN` and `VALKEY_URL` before every test so your
shell exports cannot leak in. Mock at the seam; never relax the guard.

Test structure, the current test count (it is a tracked gate), the per-module mapping and
the rules for new tests live in
[`kit_tools/testing/TESTING_GUIDE.md`](../testing/TESTING_GUIDE.md). Code style, the
pyright policy (one carve-out, `reportPrivateUsage` for `tests/`; `# type: ignore` is
disabled outright) and the `typings/` stub rule are in [CONVENTIONS.md](CONVENTIONS.md)
and [`typings/README.md`](../../typings/README.md).

### 4. Running the service

#### The documented path: the container

```bash
docker build -t forage .                        # no build arguments exist, by invariant
docker run --rm -p 127.0.0.1:8020:8020 forage   # loopback only -- Forage has no auth
curl -s localhost:8020/health | jq
```

**What "degraded runtime" means.** The image ships no weights and `docker build` needs
no token. A token-less container starts, serves `/retrieve` and `/search` immediately,
and reports `status: "degraded"`, `degraded_reasons: ["promptguard_unavailable"]`,
`promptguard_loaded: false`, `capabilities: {}` on `/health` — indefinitely, and
honestly. `/health` is always HTTP 200; the truth is in the body. Treat a degraded Forage
as *unscanned* ([GOTCHAS.md](GOTCHAS.md) § "PromptGuard model absent").

**Supplying `HF_TOKEN` — at runtime, only.** Pass it through the container environment
from a file. Never as a build argument: a Docker `ARG` is recoverable from the image's
layer history, the `Dockerfile` takes no arguments at all, and both
`tests/test_dockerfile.py` and CI's `secret-grep` job enforce that
([CLAUDE.md](../../CLAUDE.md) invariant 2). Never inline `-e` either (shell history,
`ps`). Mount the named weights volume at `HF_HOME` so the ~270 MiB download happens once
(`.env` at the repo root is gitignored):

```bash
# .env holds HF_TOKEN=hf_...; the volume persists $HF_HOME/hub/ across recreates;
# the config mount is optional.
docker run --rm -p 127.0.0.1:8020:8020 \
  --env-file ./.env \
  -v forage-model-cache:/app/model-cache \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  forage
```

While the download converges, watch `/metrics` (`model.fetch_in_progress`, then
`model.retries_scheduled`), not `docker logs` — the success narrative is logged at INFO
and nothing configures logging (see Troubleshooting). `promptguard_loaded` flips to
`true` in place. A warm start from the volume needs no network (about 9 s warm, 19 s cold
on the 1 vCPU / 1 GB reference envelope). Volume layout and the full walk-through:
[`docs/weights.md`](../../docs/weights.md).

#### Compose fragments

[`compose/minimal.yml`](../../compose/minimal.yml) (forage + searxng) and
[`compose/full.yml`](../../compose/full.yml) (the same plus a Valkey under the content
cache) are standalone, publish only `127.0.0.1:8020`, and read `compose/.env`
automatically when run from that directory:

```bash
cd compose
# HF_TOKEN is optional; SEARXNG_SECRET is required (upstream SearXNG exits 1 without it)
{
  echo "HF_TOKEN=hf_your_token_here"
  echo "SEARXNG_SECRET=$(head -c 32 /dev/urandom | base64)"
} > .env
docker compose -f minimal.yml up -d     # or full.yml
curl -s localhost:8020/health | jq
```

Two caveats. First, **the fragments pull published images; they do not build your
working tree.** Both pin `ghcr.io/washingbearlabs/forage:1.1.0` and
`ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc`; both pins resolve (`v1.1.0` published
2026-09-18 — the `manifest unknown` a `docker compose up` returned before then was
sequencing, not breakage). To run the image you just built, use the `docker run` form
above. Second,
neither fragment declares a `healthcheck:` and the `Dockerfile` has no `HEALTHCHECK` — the
"10 s x 5 retries" check that source comments mention belongs to Poppy's compose, not this repo.

#### Bare host (inferred — not a documented workflow)

Every document in this repo runs the container. `retrieval_app.py` has no `__main__`
block and nothing imports `uvicorn`; the only launch command anywhere is the Dockerfile's
`CMD ["uvicorn", "retrieval_app:app", "--host", "0.0.0.0", "--port", "8020"]`, so the
local equivalent below is derived from that line alone and has not been exercised as a
supported path:

```bash
HF_HOME=/path/you/can/write uv run uvicorn retrieval_app:app --host 127.0.0.1 --port 8020
```

`config.yaml` is read from the repo root. `HF_HOME` defaults in code to `/app/model-cache`
(`model_fetcher.DEFAULT_CACHE_ROOT`), which does not exist on a dev box, so set it to a
writable directory; the behaviour of a bare run left on the unwritable default was not
traced.

### 5. Optional companions

**SearXNG** backs `POST /search`, wired by `SEARXNG_URL` (default `http://searxng:8080`).
Without it Forage still boots and `/retrieve` works; `/search` fails with
`searxng_unavailable` / `searxng_error` (422) until it is reachable. The companion image
`ghcr.io/washingbearlabs/forage-searxng` needs exactly one variable, `SEARXNG_SECRET`, and
must keep `SEARXNG_LIMITER` off — turning it on refuses Forage's own client. To build and
probe the companion from this tree:
`docker build -t forage-searxng:ci searxng/ && uv run python searxng_smoke.py --image forage-searxng:ci`.
Everything else — engines, pin bumps, the tag lane — is
[`docs/searxng.md`](../../docs/searxng.md).

**Valkey** backs the content cache, wired by `VALKEY_URL`. Leave it fully unset and the
cache runs in a bounded in-memory store (`cache.max_entries` and `cache.max_bytes`); set it
and the Valkey backend is chosen at start regardless of reachability, with an unreachable
server reported as `degraded: cache_unavailable` and reconnected on a 1 s → 30 s backoff.
Both backends also enforce `cache.max_value_bytes` per value; keep it equal across
replicas, and expect `oversize` rejects against old larger values if you lower it.
Valkey without `FORAGE_CACHE_HMAC_KEY` additionally reports `cache_unauthenticated`;
connectivity is not authenticity, and cached content is served without re-sanitization.
`compose/full.yml` runs `valkey/valkey:8` with the literal `VALKEY_URL=redis://valkey:6379/4`
(DB index 4 is the `ContentCache` default). Which mode you are in is `cache_backend`
(`memory` or `valkey`) on `/health`; the selection rules are in
[`docs/configuration.md`](../../docs/configuration.md) § "Cache backend selection".

---

## Common Local Development Tasks

### Regenerate the contract

Any change that moves the OpenAPI document — a response model in `models.py` or
`retrieval_app.py`, a `responses=` declaration, a field description, a FastAPI bump —
leaves `tests/test_contract_export.py` red until you run:

```bash
uv run python -m scripts.export_contract           # writes contract/openapi.yaml, .sha256, and the golden fixture
uv run python -m scripts.export_contract --check   # verify only; exit 1 on drift
```

Commit all three generated artefacts together, and read
[`contract/GOVERNANCE.md`](../../contract/GOVERNANCE.md) first: a changed response shape is
a contract change (CLAUDE.md invariant 4).

### Run CI's smoke job locally

```bash
docker build -t forage:ci .
docker run -d --name forage-smoke -p 127.0.0.1:8020:8020 forage:ci   # loopback only -- no auth exists
uv run python contract_smoke.py --base-url http://127.0.0.1:8020
docker rm -f forage-smoke
```

`contract_smoke.py` asserts the `/health` contract in one of two modes:
`--expect-status degraded` (the default, and what CI runs) for a *token-less, weights-free*
container like the one above, and `--expect-status healthy` for a container started with
weights and an operational cache (e.g. `--env-file` carrying `HF_TOKEN`, plus
`FORAGE_CACHE_HMAC_KEY` if Valkey is selected at contract 1.3.0).
The wait is status-aware — under `healthy`
it keeps polling through the background PromptGuard load — so raise `--timeout-seconds` for a
cold weights fetch.

### Read the contract shipped inside an image

```bash
docker run --rm --entrypoint cat forage /app/contract/openapi.yaml | shasum -a 256
cat contract/openapi.yaml.sha256
```

The two must match; `contract/openapi.yaml.sha256` in git is the trust root every other
copy is verified against.

### Clear caches

- **Content cache, memory mode:** process-local; a restart clears it. **Valkey mode:** it
  lives in the Valkey database named by `VALKEY_URL` (DB 4 in `compose/full.yml`).
- **Weights:** persist in the `forage-model-cache` volume under `$HF_HOME/hub/`. Remove the
  volume to force a cold acquisition; a download that fails manifest verification is
  quarantined to `$HF_HOME/quarantine/`, not loaded.
- **Tool caches:** `.ruff_cache/` and `.pytest_cache/` are disposable.

---

## Troubleshooting Local Setup

The deeper landmines — with measurements and the tests that guard them — are in
[GOTCHAS.md](GOTCHAS.md). These are the ones that bite on a fresh checkout.

### `pyright` reports errors that CI does not

**Symptom:** dozens of errors locally (34 is the known figure) while the `typecheck` job is green.

**Cause:** you ran a system `pyright` or `./.venv/bin/pyright` directly; foreign stubs resolve differently outside the uv environment.

**Fix:**
```bash
uv run pyright
```

---

### A test fails with `SocketBlockedError`

**Symptom:** `pytest_socket.SocketBlockedError` from a test that seemed unrelated to networking.

**Cause:** the code under test reached the real network; the autouse `pytest-socket` guard in `tests/conftest.py` is doing its job. If tests also behave differently from CI, the same file clears `HF_TOKEN`, `VALKEY_URL` and friends before every test — your shell exports never reach the suite.

**Fix:** mock at the seam (`httpx`, the classifier, the cache) as the existing tests do. Never relax the guard.

---

### `/health` says `degraded` with `promptguard_unavailable`

**Symptom:** `promptguard_loaded: false`, `capabilities: {}`, and `docker logs` shows only WARNING/ERROR lines (or nothing).

**Cause:** no `HF_TOKEN` was supplied (log event `weights_fetch_skipped`), or the download is still in flight, or it is waiting out a retry backoff. INFO lines are invisible because nothing calls `logging.basicConfig()` — `uvicorn --log-level info` does not change that.

**Fix:** supply the token from an env file (see § 4) and watch `/metrics`:
```bash
curl -s localhost:8020/metrics | jq .model    # fetch_in_progress, retries_scheduled, fetch_failures
```

---

### `uv sync` is slow, huge, or `nvidia-*` packages appear in `uv.lock`

**Symptom:** a multi-gigabyte sync on Linux, or `tests/test_dependency_lock.py` fails.

**Cause:** the lock was re-resolved without the `pytorch-cpu` index, so plain PyPI dragged in the CUDA wheels (~2.7 GB).

**Fix:** restore the `[[tool.uv.index]]` / `[tool.uv.sources]` block in `pyproject.toml`, re-lock, and confirm:
```bash
grep nvidia- uv.lock    # must print nothing
```

---

### `ruff format --check` is red in CI but clean locally

**Symptom:** the `lint` job reports formatting drift you cannot see.

**Cause:** a system ruff at a different version than the locked 0.16.6.

**Fix:**
```bash
uv run ruff format .
uv run ruff format --check .
```

---

### `docker run` fails because port 8020 is already allocated

**Symptom:** Docker refuses to bind `127.0.0.1:8020`.

**Cause:** another Forage is holding the port — a compose project (`forage-minimal` / `forage-full`), a leftover `forage-smoke` container, or a bare-host uvicorn.

**Fix:**
```bash
docker ps                      # find it
docker rm -f forage-smoke      # or: cd compose && docker compose -f minimal.yml down
```

---

### `docker compose up` refuses to start, or reports `manifest unknown`

**Symptom:** compose aborts with the `set SEARXNG_SECRET in compose/.env` message, the `searxng` container exits 1, or the pull fails with `manifest unknown`.

**Cause:** `compose/.env` is missing (the fragments use `${SEARXNG_SECRET:?...}` on purpose), or the pinned pre-release tag has not been published to GHCR.

**Fix:** create `compose/.env` as shown in § 4 and run from the `compose/` directory. For `manifest unknown`, check the `image:` pin against the tags on `ghcr.io/washingbearlabs/forage` — or run your own build with `docker run`.

---

### `tests/test_contract_export.py` is red

**Symptom:** the export drift test fails after you touched a response model, a `responses=` declaration, a field description, or bumped FastAPI.

**Cause:** `contract/openapi.yaml` and its `.sha256` anchor are generated and no longer match the app.

**Fix:**
```bash
uv run python -m scripts.export_contract
```
Commit `contract/openapi.yaml`, `contract/openapi.yaml.sha256` and the golden fixture together; consult `contract/GOVERNANCE.md` for whether the change needs a version bump.

---

### `POST /extract` returns 404

**Symptom:** `{"detail": "Not Found"}` from a route the OpenAPI document lists.

**Cause:** `extract_route_enabled: false` is the shipped default in `config.yaml`; the admission middleware hides the route.

**Fix:** set `extract_route_enabled: true` in `config.yaml` (mount it with `-v "$PWD/config.yaml:/app/config.yaml:ro"` in the container) and restart.

---

### `degraded: cache_unavailable` when you meant memory mode

**Symptom:** `/health` reports `cache_backend: "valkey"` and `cache_connected: false` with no Valkey anywhere.

**Cause:** `VALKEY_URL` is set to an empty string — an unfilled `${VALKEY_URL}` in a compose file or a blank line in an env file renders as empty, and empty means "configured and broken", never memory mode.

**Fix:** remove the variable entirely (not `VALKEY_URL=`), then recreate the container. `compose/minimal.yml` deliberately does not mention it at all.

---

## Project Layout

```
.
├── retrieval_app.py       # FastAPI app: routes, lifespan, /health, /metrics (filename is load-bearing)
├── models.py              # Pydantic request/response models
├── cache.py               # Valkey/Redis + bounded in-memory content cache (closed log vocabulary)
├── url_validator.py       # SSRF defense
├── model_fetcher.py       # runtime weights acquisition, manifest verification, retry loop
├── weights_manifest.json  # committed weight pin (generated by scripts/vendor_weights.py)
├── config.yaml            # UA pool, news domains, blocklist, threshold, extraction limits
├── contract_smoke.py      # CI-only /health contract probe (not in the image)
├── searxng_smoke.py       # CI-only companion-image probe (not in the image)
├── Dockerfile             # single stage, no ARG, digest-pinned; docker-entrypoint.sh is `exec "$@"`
├── pyproject.toml         # uv / hatchling / ruff / pyright / pytest config; uv.lock beside it
├── pipeline/              # the sanitization stages, orchestrator, contract.py (CONTRACT_VERSION)
├── promptguard/           # Llama Prompt Guard 2 classifier wrapper
├── contract/              # openapi.yaml + openapi.yaml.sha256 (generated), GOVERNANCE.md
├── compose/               # minimal.yml, full.yml (+ gitignored .env)
├── searxng/               # companion image: Dockerfile + config/ (settings.yml, limiter.toml)
├── scripts/               # operator-only: export_contract.py, vendor_weights.py (not in the image)
├── typings/               # minimal pyright stubs for third-party gaps (read its README first)
├── tests/                 # flat test_*.py modules, conftest.py, golden/, fixtures/
├── docs/                  # configuration.md, weights.md, releases.md, searxng.md, bootstrap-notes.md
└── kit_tools/             # this documentation framework, feature specs, worktree.yaml
```

The flat layout is a decision, not an accident ([CLAUDE.md](../../CLAUDE.md) invariant 3):
the Dockerfile's `COPY` lines, `pipeline/sanitizer_revision.py`'s `_REVISION_SOURCES`
(which hashes files by relative path) and the entire test suite depend on the current
paths, and `retrieval_app.py` keeps its name for the same reason. Do not create a
`forage/` package, and remember that a new top-level module is invisible to the image
until it is named in a `COPY` line. The per-module map and the patterns that matter are
in [`kit_tools/arch/CODE_ARCH.md`](../arch/CODE_ARCH.md); the one-screen orientation is
[`kit_tools/SYNOPSIS.md`](../SYNOPSIS.md).
