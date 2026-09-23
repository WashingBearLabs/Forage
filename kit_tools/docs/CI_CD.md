<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: infrastructure, operations
  required_sections:
    - "Pipeline Overview"
  skip_if: no-ci
-->
# CI_CD.md

> **TEMPLATE_INTENT:** Document build pipelines, deployment triggers, and automation. How code gets to production.

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-resource-envelope US-004)

---

## Overview

**CI/CD Platform:** GitHub Actions — GitHub-hosted `ubuntu-latest` runners, no matrix, no self-hosted runners
**Pipeline Config:** `.github/workflows/ci.yml` — one workflow file, about 1,700 lines, ten jobs in two lanes
**Dashboard:** https://github.com/WashingBearLabs/Forage/actions (there is no status badge in `README.md`)

Everything lives in one file on purpose: `needs:` cannot span workflow files, and the whole
point of the chain is that `publish` hangs off every gate at once. There is no continuous
*deployment*. CI ends at a published image on GHCR (`ghcr.io/washingbearlabs/forage`), and
putting that image into service is the consumer's pull-and-pin, described in
`docs/releases.md` and `kit_tools/docs/DEPLOYMENT.md`. The workflow's shape is itself under
test: `tests/test_ci_workflow.py` evaluates its `if:` expressions and asserts roughly 190
properties (SHA pins, permissions, gate edges, banned triggers) on every `uv run pytest`.

Related docs, cross-referenced rather than repeated here: `kit_tools/arch/INFRA_ARCH.md`
(image, registry, tag scheme), `kit_tools/arch/SECURITY.md` (supply chain),
`kit_tools/docs/MONITORING.md` (the smoke probes as operator tools),
`kit_tools/docs/LOCAL_DEV.md` (running the gates locally), and
`kit_tools/testing/TESTING_GUIDE.md` (the suite itself).

---

## Pipeline Overview

```
   PR  /  push to main  /  tag v*                            tag searxng-v*  (also PR + main)
┌──────┐ ┌───────────┐ ┌──────┐ ┌─────────────┐              ┌───────────────┐
│ lint │ │ typecheck │ │ test │ │ build-amd64 │              │ searxng-build │
└──┬───┘ └─────┬─────┘ └──┬───┘ └──────┬──────┘              └───────┬───────┘
   │           │          │     artifact: forage-amd64-image         │  artifact: forage-searxng-image
   │           │          │      ┌──────┴───────┐                    ▼
   │           │          │      ▼              ▼            ┌───────────────┐
   │           │          │ ┌─────────────┐ ┌───────┐        │ searxng-smoke │
   │           │          │ │ secret-grep │ │ smoke │        └───────┬───────┘
   │           │          │ └──────┬──────┘ └───┬───┘                │
   └───────────┴──────────┴────────┴────────────┘                    │
                          ▼  push to main, or tag v*                 ▼  tag searxng-v* (+ test)
                    ┌───────────┐                            ┌─────────────────┐
                    │  publish  │                            │ searxng-publish │
                    └───────────┘                            └─────────────────┘
```

In order:

1. **A PR is opened or updated.** The six gates run: `lint`, `typecheck`, `test` and
   `build-amd64` are siblings (none reads another's output, so a failure in one does not
   delay the others' reports); `secret-grep` and `smoke` consume `build-amd64`'s artifact.
   `publish` does not run on PRs.
2. **Merge to `main`.** The same six, then `publish` pushes one tag, `sha-<short>`, to GHCR.
   No GitHub Release.
3. **Tag `vX.Y.Z` on `main`.** The same six, then `publish` pushes `X.Y.Z`, `X.Y` and
   `latest`, creates a GitHub Release carrying `contract/openapi.yaml` and its `.sha256`,
   then reads the assets back from the API and verifies them against the committed anchor.
4. **Tag `vX.Y.Z-rc`** (any `v*` tag containing `-`). The exact tag only, and a Release
   flagged pre-release.
5. **Tag `searxng-vX.Y.Z`.** The companion lane: `test`, `searxng-build` and
   `searxng-smoke` gate `searxng-publish`. The service lane's `build-amd64`,
   `secret-grep`, `smoke` and `publish` are skipped by their `if:`.

The image travels between jobs as an **artifact, never a rebuild**: each job runs on a fresh
runner with an empty image store, so `build-amd64` ends with `docker save` and every
consumer `docker load`s the tarball and asserts the loaded image ID equals the one
`build-amd64` recorded. Two builds of "the same" Dockerfile are not the same image; one
artifact with an asserted identity is.

---

## Triggers

The `on:` block, verbatim in shape:

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened]
  push:
    branches: [main]
    tags:
      - "v*"
      - "searxng-v*"
```

- **No `workflow_dispatch`** and no schedule: there is no manual trigger. Re-running a
  failed run from the Actions UI is the only manual action, and for `publish` that is
  documented as safe (see the failure playbook).
- **`pull_request_target` is banned** outright (a guard test in `tests/test_ci_workflow.py`).
  Fork PRs run the gates as ordinary `pull_request` events on a hosted runner with a
  read-only token and no repository secrets.
- **Both tag patterns are load-bearing**: `v*` does not match `searxng-v*`, so without the
  second pattern the companion image has no publish path.

**Concurrency:** group `ci-<workflow>-<ref>` (from `github.workflow` and `github.ref`), with
`cancel-in-progress` true **only for `pull_request` events**. Superseded PR pushes collapse to
the newest commit; `main` and tag runs are never cancelled, because a tag run is the gate
chain for a publish.

**Permissions:** top level is `contents: read` only. `publish` raises its own job-scoped
`contents: write` (for `gh release create`) and `packages: write` (for the GHCR push);
`searxng-publish` raises `packages: write` only. Nothing else inherits either. Every
`actions/checkout` sets `persist-credentials: false`.

### On Pull Request

- `lint`, `typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke` (the six required checks)
- `searxng-build`, `searxng-smoke` also run (their `if:` only excludes `v*` tags) but are not in the required-check set

### On Merge to Main

- The same eight jobs
- `publish` → `ghcr.io/washingbearlabs/forage:sha-<short>` (e.g. `sha-a92d373` for the current HEAD)

### On Tag Push

- `v*`: the six gates, then `publish` (semver tags, `latest` for non-pre-releases, Release with contract assets)
- `searxng-v*`: `lint`, `typecheck`, `test`, `searxng-build`, `searxng-smoke`, then `searxng-publish`

---

## Jobs

All jobs run on `ubuntu-latest`. Every `uses:` is pinned to a 40-hex commit SHA with a
`# vX.Y.Z` comment (`actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.0.1,
`docker/setup-buildx-action` v4.3.0, `docker/setup-qemu-action` v4.3.0,
`docker/build-push-action` v7.3.0, `docker/login-action` v4.6.0,
`docker/metadata-action` v6.2.0, `actions/upload-artifact` v7.0.1,
`actions/download-artifact` v8.0.1). Durations are from the `forage-ci-and-image` US-007
measurement run; "cold" means an empty GHA build cache.

| Job | Purpose | Blocking | `needs:` | `if:` | Timeout | Measured |
|-----|---------|----------|----------|-------|---------|----------|
| `lint` | ruff, lock hygiene, actionlint, compose validation | yes, required check | none | always | 15 min | 17 s |
| `typecheck` | `uv run pyright` (strict, zero errors) | yes, required check | none | always | 15 min | 28 s |
| `test` | full hermetic pytest suite | yes, required check | none | always | 20 min | 38 s |
| `build-amd64` | reproducible amd64 image, saved as artifact | yes, required check | none | not a `searxng-v*` tag | 45 min | 1m39s (3m00s cold) |
| `secret-grep` | grep the image's layer history for secrets | yes, required check | `build-amd64` | not a `searxng-v*` tag | 20 min | 47 s |
| `smoke` | run the candidate image, verify `/health`, `/metrics`, in-image contract | yes, required check | `build-amd64` | not a `searxng-v*` tag | 20 min | 1m11s |
| `publish` | multi-arch push to GHCR, layer parity, Release + asset verification | consumer of all six gates | `lint, typecheck, test, build-amd64, secret-grep, smoke` | `push` to `main` or `refs/tags/v*` | 60 min | 1m02s warm, 4m37s cold |
| `searxng-build` | companion image (amd64), saved as artifact | gates `searxng-publish` | none | not a `v*` tag | 20 min | seconds (no `RUN` steps) |
| `searxng-smoke` | `searxng_smoke.py` hermetic phases, plus advisory live probe | gates `searxng-publish` | `searxng-build` | not a `v*` tag | 30 min | not measured |
| `searxng-publish` | multi-arch push of the companion image, layer parity | consumer | `test, searxng-build, searxng-smoke` | `push` of a `searxng-v*` tag | 30 min | not measured |

### `lint`

1. `astral-sh/setup-uv` at `UV_VERSION: "0.9.28"` (the same pin the `Dockerfile` copies
   from `ghcr.io/astral-sh/uv:0.9.28`), `enable-cache: true`, `cache-dependency-glob: uv.lock`.
2. `uv sync --extra dev --locked` — `--locked` fails if `uv.lock` is stale relative to
   `pyproject.toml`, so a forgotten re-lock is a red job rather than a drifting environment.
3. Two CUDA-wheel guards: `uv.lock` must contain no `nvidia-` string, and the synced
   environment's package list must contain no `nvidia-*` package. Resolving torch from
   plain PyPI drags in fifteen CUDA packages; `pyproject.toml`'s `pytorch-cpu` index is what
   keeps the image at ~348 MB.
4. `uv run ruff check .` and `uv run ruff format --check .`.
5. `actionlint` 1.7.12, downloaded from GitHub releases and verified against
   `ACTIONLINT_SHA256` before it runs, then `./actionlint -color` on the workflow itself.
   actionlint checks that the file is *valid*; `tests/test_ci_workflow.py` checks that it is *safe*.
6. `docker compose -f compose/minimal.yml config -q` and the same for `compose/full.yml`,
   with a placeholder `SEARXNG_SECRET` so the required-or-fail interpolation resolves.

### `typecheck`

Same setup-uv and `--locked` sync, then `uv run pyright`. Strict mode, zero errors, no
baseline, no excludes, `enableTypeIgnoreComments=false` — the policy is asserted by
`tests/test_pyright_policy.py`. Third-party gaps are stubbed in `typings/`.

### `test`

Same setup-uv and `--locked` sync, then two steps: a named
`uv run pytest -q tests/test_sanitizer_revision.py` (so an unintended rotation of
`derive_sanitizer_revision()` is the first line of the failure, not one of many), then
`uv run pytest -q` for the whole suite. The suite is hermetic: an autouse `pytest-socket`
guard in `tests/conftest.py` fails any test that touches the network. Several CI-relevant
guards run *inside* this job rather than as separate jobs:

- `tests/test_dockerfile.py` — the `Dockerfile` declares zero `ARG`s, a single `FROM`,
  digest-pinned base and `COPY --from`, per-arch oras checksums, no `HF_TOKEN` or
  `hf_…` literal anywhere, `uv sync --locked`, every `COPY`'d file exists, `contract/` is copied.
- `tests/test_ci_workflow.py` — the workflow's own posture (pins, permissions, `needs:` edges, tag rules).
- `tests/test_contract_export.py` — the committed `contract/openapi.yaml` and its anchor are current (see Contract Gate below).
- `tests/test_governance_docs.py` — `.github/pull_request_template.md`'s list of required checks matches `publish`'s `needs:`.
- `tests/test_compose_fragments.py`, `tests/test_searxng_docker.py`, `tests/test_contract_smoke.py`, `tests/test_searxng_smoke.py`.

See `kit_tools/testing/TESTING_GUIDE.md` for the suite's layout and current count.

### `build-amd64`

1. `docker/setup-buildx-action`.
2. Compute `SOURCE_DATE_EPOCH` from `git log -1 --format=%ct` (the commit's committer date);
   an empty value fails the step with an explicit error rather than producing an
   unreproducible build.
3. `docker/build-push-action` with `platforms: linux/amd64`, `push: false`,
   `outputs: type=docker,rewrite-timestamp=true`, `provenance: false`,
   `cache-from: type=gha` / `cache-to: type=gha,mode=max` (the default scope). No login,
   no registry reference — the image is tagged locally as `forage:ci` (`IMAGE_REF`).
4. Record the daemon image ID to `image-id.txt`, `docker save | gzip -1`, and upload as the
   artifact `forage-amd64-image` with `retention-days: 1`.

### `secret-grep`

Runs with **no checkout** — it downloads the artifact into an empty working directory,
`docker load`s it, asserts the loaded ID equals the recorded ID, then runs
`docker history --no-trunc forage:ci` and greps the output for four patterns: the literal
`HF_TOKEN` (the variable name the deleted bake path used), `hf_[A-Za-z0-9]{20,}` (the
shape of a Hugging Face token), `FORAGE_BRAVE_API_KEY` (Brave search), and
`FORAGE_CACHE_HMAC_KEY` (cache signing). The latter two match credential variable
names only, never key-shape regexes. The set is pinned by
`tests/test_ci_workflow.py::_REQUIRED_GREP_PATTERNS`. Scope is layer *metadata* —
build-arg, `ENV` and `RUN` lines — which is exactly where a build ARG lands and exactly
what `docker history` reads back out of any registry. It is not a filesystem scanner.
This is the second of the two mechanical guards behind `CLAUDE.md` invariant 2; the first
is `tests/test_dockerfile.py`.

### `smoke`

Checks the repository out (so `contract_smoke.py` imports the *same commit's*
`pipeline.contract.CONTRACT_VERSION`), downloads the artifact into `downloaded-image/`,
loads it and asserts identity, then:

```bash
docker run -d --name forage-smoke -p 127.0.0.1:8020:8020 forage:ci   # no environment at all; loopback only
uv run python contract_smoke.py \
  --base-url http://127.0.0.1:8020 \
  --timeout-seconds 120 \
  --image forage:ci
```

With no `HF_TOKEN` the container must come up **degraded and honest**: `/health` returns 200
with `status: degraded`, `degraded_reasons: ["promptguard_unavailable"]`, no
`search_sanitization` capability, and `contract_version` equal to the tree's; `/metrics`
must report the same version. `--image` adds the in-image contract leg: the script reads
`/app/contract/openapi.yaml` back out of the candidate image, checks its `info.version`, and
hashes it against the committed `contract/openapi.yaml.sha256`. The 120 s budget
(`SMOKE_TIMEOUT_SECONDS`) is deliberate: a cold start imports torch before uvicorn binds,
and a guard test ties the number to the script's own default. On failure the job dumps the
container log; the container is removed either way. `kit_tools/docs/MONITORING.md` covers
running the same probe against a deployment.

CI passes neither `--expect-status` nor `--anchor` and relies on their defaults:
`--expect-status degraded` (the weights-free contract above; `/health` is polled until it
answers 200 *and* reports that status) and `--anchor` pointing at the committed
`contract/openapi.yaml.sha256`. An operator probing a container started **with** weights
(an `--env-file` carrying `HF_TOKEN`, or the mirror) passes `--expect-status healthy`,
which inverts the three PromptGuard-coupled checks — `status: healthy`, no
`promptguard_unavailable`, `search_sanitization` present — and should raise
`--timeout-seconds` for a cold weights fetch. The rule is which flag matches which
container: `degraded` for one started with no token or weights, `healthy` for one started
with them and an operational cache. At contract 1.3.0 a Valkey-backed container also
needs `FORAGE_CACHE_HMAC_KEY` in the runtime env file; unsigned Valkey remains
`degraded: cache_unauthenticated` even with weights and connectivity.
When verifying a release image from a checkout other than its tag, `--anchor`
takes the file `git show vX.Y.Z:contract/openapi.yaml.sha256` prints — never a Release
asset or the image's own copy.

---

## The `publish` Job

**Runs when:** `github.event_name == 'push'` and the ref is `refs/heads/main` or starts with
`refs/tags/v`. Never on a PR, never on a `searxng-v*` tag.

**Gated by:** `needs: [lint, typecheck, test, build-amd64, secret-grep, smoke]` — every gate
in the file at once. A tag pushed onto a red tree runs the gates, they fail, and this job
never starts. Job permissions are `contents: write` and `packages: write`; the login is
`docker/login-action` to `ghcr.io` with `secrets.GITHUB_TOKEN`.

Steps, in order:

1. **QEMU** (`docker/setup-qemu-action`), only because `PUBLISH_PLATFORMS` is
   `linux/amd64,linux/arm64`. The arm64 leg is emulated and is the one lever to trim if
   the budget is ever exceeded; the platform list is single-sourced in `env:` because
   `docs/releases.md` documents it and a guard test reads it.
2. **Same `SOURCE_DATE_EPOCH` computation** as `build-amd64`.
3. **Download the gated artifact**, load it, assert identity, and record its
   `RootFS.Layers` (the ordered diff IDs) to `gated-layers.json`. This is the reference
   the published image will be compared against.
4. **Compute tags** with `docker/metadata-action`, `flavor: latest=false` and four explicit
   rules, each carrying its own `enable=` so the policy reads on its own:
   - `type=semver` full version — enabled when the ref starts with `refs/tags/v`
   - `type=semver` major.minor — enabled when the ref starts with `refs/tags/v` **and does not contain `-`**
   - `type=sha,prefix=sha-,format=short` — enabled when the ref is exactly `refs/heads/main`
   - `type=raw,value=latest` — enabled when the ref starts with `refs/tags/v` **and does not contain `-`**

   | You push | The registry gets | GitHub Release |
   |---|---|---|
   | `v1.2.3` | `1.2.3`, `1.2`, `latest` | yes |
   | `v1.2.3-rc`, `v0.9.0-rc.2`, any `v*` with a `-` | `1.2.3-rc` only | yes, flagged pre-release |
   | a commit on `main` | `sha-<short>` | no |
   | `searxng-v*` | nothing on this image | no |

5. **Build and push** multi-arch with `outputs: type=image,push=true,rewrite-timestamp=true`,
   `provenance: false`, `sbom: false` (attestations deliberately off), reading
   `cache-from: type=gha` (the gated amd64 layers) and `type=gha,scope=publish` (the arm64
   layers, kept in their own scope so `build-amd64`'s amd64-only manifest cannot evict them),
   writing `cache-to: type=gha,mode=max,scope=publish`.
6. **Layer-identity assertion.** `docker buildx imagetools inspect` on the published
   reference; the `linux/amd64` config's `rootfs.diff_ids` must equal `gated-layers.json`
   layer for layer, and the comparison is asserted non-vacuous. This is what makes the push
   a *release of the image the gates ran* rather than a rebuild that resembles it.
7. **Published-config secret grep.** The same four patterns as `secret-grep` (`HF_TOKEN`,
   `hf_[A-Za-z0-9]{20,}`, `FORAGE_BRAVE_API_KEY`, `FORAGE_CACHE_HMAC_KEY`), run over the published image config
   JSON for all platforms.
8. **On `v*` tags only — Release.** `CONTRACT_VERSION` is grepped out of the *tagged tree's*
   `pipeline/contract.py` (currently `1.3.0`; a non-semver read fails the step), and the
   same step copies that version's **per-version entry** — its bullet at column 0 in the
   `CONTRACT_VERSION` docstring plus the two-space-indented lines under it — into
   `${RUNNER_TEMP}/contract-entry.md` with a POSIX `awk` program; an empty file (a contract
   nobody announced) fails the step, and `$GITHUB_OUTPUT` still carries only `version=`.
   The job *reads* the tagged tree and executes nothing from it — no `python3`, no `uv`, no
   `scripts/`. The body is then written to `${RUNNER_TEMP}/release-notes.md`: the fixed
   heredoc text with its line `contract: X.Y.Z`, a `What changed in contract X.Y.Z:`
   heading, and the entry appended byte-for-byte with `cat` (never interpolated).
   `gh release create <tag> --notes-file` publishes it, with `--prerelease` when the tag
   contains `-` and the assets `contract/openapi.yaml` and `contract/openapi.yaml.sha256`
   uploaded by the same command. Ordering matters: the Release exists only after the push
   and its verification, so a Release can never advertise an image nobody can pull.
9. **Read the Release back.** `gh release view --json body` must match `^contract: X.Y.Z$`
   anchored, and every line of the per-version entry must appear in it (`grep -qF`); then
   `gh release download --pattern 'openapi.yaml*'` into a scratch directory,
   `cmp` the downloaded anchor against the committed `contract/openapi.yaml.sha256`, and
   `sha256sum -c openapi.yaml.sha256` beside the downloaded document.

**What a green `publish` proves** (`docs/releases.md` is canonical): all six gates were
green on that commit; the published amd64 filesystem is layer-identical to the image
`smoke` executed and `secret-grep` cleared; the published config carries no secret pattern;
the Release exists only after push and verification; its body's contract line matches the
tagged tree and carries that version's per-version entry; its assets verify against the
committed anchor. **What it does not prove:**
`linux/arm64` is built from the same commit and the same digest-pinned multi-arch base but
is never executed by CI — a consumer on arm64 should run `contract_smoke.py` against their
own container, with the `--expect-status` that matches how they started it. Two publishes have run green through this lane, both
verified against GHCR afterwards rather than assumed: `v1.0.0` (commit `f4c2b16`, 2026-09-12 UTC)
minted `latest`, `1.0` and `1.0.0` at index digest `sha256:d83639cc…`, and `v1.1.0` (commit
`06b01b14`, 2026-09-18 UTC) moved `latest` and minted `1.1` and `1.1.0` at `sha256:e1b875cc…`.
`docs/releases.md` § "Released versions" carries the full digests, anchors and tagged commits.

---

## The Companion Lane (`forage-searxng`)

`ghcr.io/washingbearlabs/forage-searxng` is upstream SearXNG plus a baked config
(`searxng/Dockerfile`, `searxng/config/`). It has its own build, smoke and publish, keyed
off `searxng-v*` tags, and shares nothing with the service lane except the `test` job —
which it does need, because the config-regression and engine-parity guards that would stop
a relaxation reaching a published image live in the suite.

- `searxng-build`: context `searxng/`, `linux/amd64`, same `SOURCE_DATE_EPOCH` and
  `rewrite-timestamp=true` exporter, cache scope `searxng`, artifact `forage-searxng-image`
  (1-day retention).
- `searxng-smoke`: `uv run python searxng_smoke.py --image forage-searxng:ci` — blocking
  hermetic phases on a Docker network created with `--internal` (no egress, so no
  third-party engine is contacted from a gate): the secret is required, the JSON envelope
  answers 200, six requests exceed `API_MAX` without a 429, and the limiter installs with
  Valkey but is inert without it. A live-engine probe (`--live`) follows with
  `continue-on-error: true` and its verdict written to the job summary.
- `searxng-publish`: `if: push && startsWith(github.ref, 'refs/tags/searxng-v')`,
  `needs: [test, searxng-build, searxng-smoke]`, `packages: write`. The version is the tag
  with `searxng-v` stripped, and the pre-release test runs on that stripped string — the
  service lane's `!contains(github.ref, '-')` would be always-false here because the prefix
  itself contains a hyphen. `searxng-v0.1.0` → `0.1.0`, `0.1`, `latest`;
  `searxng-v0.2.0-rc` → `0.2.0-rc` only. No `main`-push publish, no GitHub Release. The
  same `rootfs.diff_ids` parity check runs after the push; cache scope `searxng-publish`.

`docs/searxng.md` is the canonical reference for this lane, including the pin-bump procedure.

---

## Required Checks and Branch Protection

Branch protection is a GitHub setting, not a file in this repository. The record of what
was applied is `docs/releases.md` ("Required status checks"), measured against the
branch-protection API on 2026-09-11 after the public flip:

| Setting on `main` | Value |
|---|---|
| Required status checks | `lint`, `typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke` — the six `publish` hangs off |
| Pull request required | yes, with `required_approving_review_count: 0` (a solo maintainer cannot approve their own PR) |
| Force pushes and deletions | blocked |
| Strict (branch must be up to date before merge) | off |
| Secret scanning and push protection | enabled at the public flip |

Two things keep the list honest without the UI: `.github/pull_request_template.md` names
the six checks to contributors, and `tests/test_governance_docs.py` ties that sentence to
`publish`'s `needs:` so the document cannot fall behind a seventh gate. The complementary
control is that the gates are `needs:` edges rather than merge policy — nothing can be
*published* over a red gate whether or not a human can merge over one. No other branch is
protected.

---

## Local Equivalents

The CI gates are the same four commands developers run, and all of them must go through
`uv run` — the lock pins the toolchain, and a system-installed ruff or pyright reports
numbers that do not reproduce:

```bash
uv sync --extra dev             # once; CI adds --locked
uv run pytest                   # = the `test` job
uv run ruff check .             # = half of `lint`
uv run ruff format --check .    # = the other half of `lint` (fix with: uv run ruff format .)
uv run pyright                  # = the `typecheck` job
```

`.github/pull_request_template.md` carries the one-line form,
`uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright`.
The image side of the chain is `docker build -t forage .` (no token needed; the runtime
comes up degraded), and the companion is
`docker build -t forage-searxng:ci searxng/ && uv run python searxng_smoke.py --image forage-searxng:ci`.
`kit_tools/docs/LOCAL_DEV.md` covers environment setup and the macOS notes (cgroup metrics
are `null`, use `shasum -a 256`, Docker Desktop's containerd store makes builder and daemon
image IDs differ — a notice, not a failure).

---

## Contract Gate

`contract/openapi.yaml` and `contract/openapi.yaml.sha256` are **generated**; hand-editing
either is always wrong. Anything that moves the served OpenAPI document — a response model
in `models.py` or `retrieval_app.py`, a `responses=` declaration, a field description, a
FastAPI bump — must be followed by:

```bash
uv run python -m scripts.export_contract
```

which rewrites the document, the anchor and the current golden fixture. Until it runs,
`tests/test_contract_export.py` is red inside the `test` job: the committed contract must
be byte-for-byte what the app generates today, and the committed anchor must be the sha256
of those bytes. The same file also verifies the checker can fail (a committed
un-regenerated twin under `tests/fixtures/contract/`), that rendering is byte-stable across
`PYTHONHASHSEED`s, and that `/extract` is in the document even though the route is off by default.

The anchor (`9860c4d988295f39ee9e31ac65414dd1ce2c89c1031cd45c778b7fa8142923e4` at HEAD) is
the trust root every other copy is verified against: `smoke` hashes the in-image copy
against it, `publish` hashes the Release assets against it, and consumers verify the copy
they vendor against the anchor *at the same tag*, never against another copy. Whether a
change is PATCH, MINOR or MAJOR — and therefore whether `contract_version` in
`pipeline/contract.py` must move and a golden fixture be added under `tests/golden/` — is
decided by `contract/GOVERNANCE.md`; `CLAUDE.md` invariant 4 is the short form.

---

## Secrets in CI

The workflow references exactly one secret: **`secrets.GITHUB_TOKEN`**, in four places —
`docker/login-action`'s password in `publish` and `searxng-publish`, and `GH_TOKEN` for the
three `gh release` steps in `publish`. There are no repository secrets, no organisation
secrets, and no environment secrets; the header comment records this as the intended
posture for fork PRs. Nothing needs adding to run CI on a fork.

That is a consequence of `CLAUDE.md` invariant 2: **no secret may enter the image build**.
The `Dockerfile` takes no build arguments at all, so there is nothing for CI to pass in,
and the model weights are a *runtime* input (`HF_TOKEN` or the private OCI mirror, both
via the container environment — see `docs/configuration.md` and `docs/weights.md`). The
one human-only credential flow around the image, vendoring weights with
`scripts/vendor_weights.py`, never runs in CI.

---

## Caching and Reproducibility

| Cache | Where | Key / scope | Notes |
|---|---|---|---|
| uv environment | `astral-sh/setup-uv` `enable-cache: true` | `cache-dependency-glob: uv.lock` | used by `lint`, `typecheck`, `test`, `smoke`, `searxng-smoke` |
| amd64 image layers | GHA build cache (`type=gha,mode=max`) | default scope | written by `build-amd64`; read first by `publish` |
| arm64 image layers | GHA build cache | `scope=publish` | written and read by `publish` only, so the amd64-only manifest cannot evict the emulated layers |
| companion layers | GHA build cache | `scope=searxng`, `scope=searxng-publish` | same split for the companion lane |
| gated image | artifact `forage-amd64-image` | per run | `retention-days: 1`; identity asserted by every consumer |

Builds are reproducible so that `publish`'s rebuild-from-cache can be asserted identical to
the gated tarball even on a cold cache. Two halves, in two files: CI sets
`SOURCE_DATE_EPOCH` to the commit's committer date and `rewrite-timestamp=true` on every
exporter (rewrites the layer tar headers), and the `Dockerfile` removes the apt logs and
`ldconfig` aux-cache in the install `RUN` and sets `PYTHONDONTWRITEBYTECODE=1` on the
build-time import check (removes timestamps *inside* files, which no exporter can reach).
Measured: 19 of 19 layers identical across two `--no-cache` builds. Base image, `uv` and
`oras` are digest- or checksum-pinned. The known bound is one UTC day — `useradd` stamps a
day count into `/etc/shadow`, so a run that straddles midnight can trip the parity gate on
that one layer (re-run; not a finding). `kit_tools/docs/GOTCHAS.md` records the incident
that produced this design.

### Invalidating Cache

There is no cache-bust switch. The one case that needs it is a **poisoned `publish`
scope**: a failed publish rebuild writes its wrong layers to `scope=publish`, and because
`publish` prefers its own scope, later publishes keep failing parity. Recovery:

```bash
gh cache list                       # find the index-publish-* entries
gh cache delete <id>                # delete each index-publish-* entry
# re-run the failed publish; it falls through to the gated buildkit scope
```

---

## Cutting a Release and Rolling Back

The git tag **is** the version (`pyproject.toml`'s `version` is inert packaging metadata),
and the image tag and `contract_version` are independent semvers — image `v1.1.0` serves
contract `1.2.0` (`v1.0.0` served `1.1.0`).

```bash
git switch main && git pull
# confirm the six gates are green for the commit you are about to tag
git tag v1.0.1
git push origin v1.0.1
# then watch the run; publish is the last job
```

Never move or re-cut a tag; cut a new patch. Registry tags are treated as immutable by
policy. If the layer verification fails **after** a push, the tags point at an ungated image:
withdraw the git tag **and** delete the GHCR package version (needs `delete:packages`; the
`0.9.2-rc` precedent is recorded as pending deletion in `docs/releases.md`). A red publish is
not a release.

**Rollback** is entirely consumer-side — there is no in-repo rollback mechanism and no
deploy stage to revert. Re-pin the previous tag in the consumer's compose file
(`image: ghcr.io/washingbearlabs/forage:<previous>`) and `docker compose -f <file> up -d`.
`kit_tools/docs/DEPLOYMENT.md` has the operator view, including the pull/pin/verify
sequence and the note that the compose fragments in this repo pin `1.1.0`, published by
`v1.1.0`.

---

## Failure Playbook

Every cause below is one the workflow's own comments, `docs/releases.md`, or
`kit_tools/docs/GOTCHAS.md` documents.

| Red job | Symptom | Cause | Fix |
|---|---|---|---|
| `lint` | `ruff format --check` lists files | format drift | `uv run ruff format .` and commit |
| `lint` | `uv sync --locked` fails | `uv.lock` stale relative to `pyproject.toml` | update `uv.lock` and commit it; never edit it by hand |
| `lint` | `uv.lock contains nvidia-* CUDA wheels` | torch resolved from plain PyPI | re-lock with the `pytorch-cpu` index configured in `pyproject.toml` |
| `lint` | actionlint error | invalid workflow YAML or expression | fix the workflow; `tests/test_ci_workflow.py` will also fail on posture regressions |
| `typecheck` | errors CI reports but local pyright does not | local run used a system pyright, not the locked one | `uv run pyright` |
| `test` | `tests/test_contract_export.py` red | response model or route metadata changed without a regen | `uv run python -m scripts.export_contract`, commit the three outputs, classify per `contract/GOVERNANCE.md` |
| `test` | `tests/test_sanitizer_revision.py` red (the named first step) | a hashed source or the model identity changed | if deliberate, update the pinned revision and record before/after in `docs/bootstrap-notes.md`; otherwise revert |
| `test` | `tests/test_dockerfile.py` red | an `ARG`, a secret-shaped `ENV`, a lost digest pin, a second `FROM`, a missing `COPY` source | revert; the build takes no arguments, ever |
| `test` | `tests/test_ci_workflow.py` red | unpinned action, widened permissions, `pull_request_target`, changed `needs:`/`if:` | restore the guarded property; the test names it |
| `secret-grep` | `HF_TOKEN`, `hf_…`, `FORAGE_BRAVE_API_KEY` or `FORAGE_CACHE_HMAC_KEY` in `docker history` | a credential reached a build-arg, `ENV` or `RUN` line | remove it; secrets are runtime-only (`docs/configuration.md`) |
| `secret-grep` / `smoke` | loaded image ID differs from `image-id.txt` | artifact/identity mismatch | not a flake — investigate the artifact hand-off before re-running |
| `smoke` | `/health` never reaches 200 within 120 s | the container did not bind (config error, import failure) | read the container log the job dumps on failure |
| `smoke` | in-image contract sha256 or `info.version` mismatch | contract not re-exported, or `contract/` not copied | `uv run python -m scripts.export_contract`; check the `Dockerfile`'s `COPY contract/` line |
| `publish` | diff_ids parity fails after push | rebuild did not come from the gated cache (cold cache, poisoned `publish` scope, or UTC-midnight bound) | tags are untrusted: withdraw tag + delete package version; `gh cache delete` the `index-publish-*` entries; re-run |
| `publish` | push interrupted | orphan blobs, no tag moved | re-run the same tag; blobs already present are skipped |
| `publish` | Release step fails after a successful push | image live, Release missing | re-run; if `gh release create` refuses an existing tag, delete the Release first |
| `publish` | Release body does not match `^contract: X.Y.Z$` | body edited or wrong | `gh release edit <tag> --notes ...`, or delete the Release and re-run |
| `publish` | asset verification (`cmp` / `sha256sum -c`) fails | wrong or missing Release assets | `gh release upload --clobber`, or delete the Release and re-run |

---

## Troubleshooting CI

### "It works locally but fails in CI"

1. **Toolchain.** CI runs every gate through `uv run` with `--locked`; a green from a
   system `ruff` or `pyright` does not count. Re-run the exact command from the job.
2. **Platform.** CI is `linux/amd64`. On macOS the cgroup fields in `/metrics` are `null`
   and `sha256sum` is `shasum -a 256`; the suite is verified green on both, but an
   assertion you wrote against macOS-only behaviour will not be.
3. **Network.** The suite is hermetic — `pytest-socket` fails any real connection. A test
   that passed locally because something was cached will fail in CI. Mock at the seam;
   never relax the guard.
4. **Image identity.** Under Docker Desktop's containerd store the builder and daemon image
   IDs differ; that is a notice locally, but CI's identity assertion compares the daemon ID
   `build-amd64` recorded, so a local mismatch is not the CI failure.
5. **Logs.** The `smoke` job dumps the container log on failure; the same log is invisible
   at `INFO` in any container because nothing configures logging
   (`kit_tools/docs/GOTCHAS.md`, "Nothing configures logging").
