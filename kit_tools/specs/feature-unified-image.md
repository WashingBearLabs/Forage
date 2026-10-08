<!-- Template Version: 2.5.0 -->
---
feature: unified-image
status: active
session_ready: true
depends_on: [inference-device]
vision_ref: "T3 — run the classifier where the hardware is: CPU and GPU from one image"
type: epic-child
size: L
epic: forage-inference-backends
epic_seq: 3
epic_final: false
created: 2026-10-08
updated: 2026-10-08
---

# Feature Spec: Unified Image — CUDA Torch on amd64, CPU Torch on arm64, One Tag

## Overview

**Today.** The lock pins torch to the CPU index on Linux. Three guards enforce "no `nvidia-*` in
the lock or the environment":
- `tests/test_dependency_lock.py` ~:34-58;
- CI lint `.github/workflows/ci.yml` ~:155-180, pinned by `tests/test_ci_workflow.py` ~:691;
- DECISIONS.md :522.

The image is one `Dockerfile` with **no build arguments** (CLAUDE.md invariant 2), and
`tests/test_dockerfile.py` pins its text.

**The owner wants one image, repo and tag** that installs on CPU or GPU hosts (ruling 2026-10-08).
Measured on thelab, the CUDA-built torch wheel runs CPU inference at parity with the CPU wheel
(1,172 ms against 1,196 ms per window, 86M, 8 CPUs, no GPU), so CPU installs lose nothing.

**Design:**
- **Lock.** Two mutually exclusive extras, `cpu` and `cuda`, through uv's conflicting extras.
  - `cuda` resolves torch from the `pytorch-cu130` index on `sys_platform == 'linux' and platform_machine == 'x86_64'`.
  - `cpu` resolves torch from the `pytorch-cpu` index on Linux.
  - Developers and CI install `--extra dev --extra cpu`, so no CUDA download in CI.
- **Image.** The single Dockerfile picks the extra by architecture inside `RUN`, using
  `dpkg --print-architecture`, as the oras step already does: `amd64` takes `cuda`, `arm64` takes
  `cpu`. Still no `ARG`.
- **Guards are scoped, not deleted.** `nvidia-*` may appear in the lock only as dependencies of the
  `cuda` extra's x86_64 torch. The synced dev/CI environment must still contain none.
- **Compose.** `compose/gpu.yml` is an overlay (`-f compose/minimal.yml -f compose/gpu.yml`) that
  adds `gpus: all` and `FORAGE_DEVICE=cuda`. The base fragments stay CPU and unchanged.

## Goals

- `uv sync --extra dev --extra cpu` on Linux x86_64 installs no `nvidia-*` package. CI asserts it,
  and CI install time does not grow by more than 10%.
- The amd64 image contains `torch==2.14.0+cu130`; the arm64 image contains `torch==2.14.0+cpu`.
  The amd64 size is recorded, with an expected ceiling of 5 GB.
- `tests/test_dockerfile.py` still asserts no `ARG`, one `FROM` and digest pins, and newly asserts
  the arch-to-extra mapping.
- **CI smoke (no GPU on the runner):**
  - the default boot behaves as today;
  - `FORAGE_DEVICE=cuda` with fallback `cpu` boots degraded with `promptguard_device: "cpu"`;
  - `FORAGE_DEVICE=cuda` with fallback `refuse` exits non-zero before serving.
- An install guide documents CPU and GPU installs from the same tag.

## User Stories

### US-001: One lock with arch-split torch, guards scoped

**Priority:** P1

**Description:** As a maintainer, I want a single lock that carries CUDA torch for amd64 images and
CPU torch for everything else, with the CPU-only guards narrowed rather than removed, so that the
image can ship both while developer and CI installs stay small.

**Independent Test:** `uv lock --check` passes. Syncing with `--extra cpu` on Linux x86_64
installs `torch==…+cpu` and no `nvidia-*`. The lock contains `nvidia-*` only under the `cuda`
extra's x86_64 marker. The updated guard tests pass, and fail on a planted violation.

**Implementation Hints:**
- **`pyproject.toml`** (~:50-68). Follow the uv PyTorch guide: two optional extras, each listing
  `torch==2.14.0`.
  - `[tool.uv] conflicts = [[{ extra = "cpu" }, { extra = "cuda" }]]`.
  - Indexes `pytorch-cpu` (`https://download.pytorch.org/whl/cpu`) and `pytorch-cu130`
    (`https://download.pytorch.org/whl/cu130`), both `explicit = true`.
  - `[tool.uv.sources] torch = [{ index = "pytorch-cpu", extra = "cpu", marker = "sys_platform == 'linux'" }, { index = "pytorch-cu130", extra = "cuda", marker = "sys_platform == 'linux' and platform_machine == 'x86_64'" }]`.
  - macOS and Windows resolve from PyPI as today.
  - Remove torch from the base dependencies, or keep a base requirement that both extras satisfy.
    Test which uv accepts.
  - Fix the stale comment at ~:55-59 ("the image … never reads this lock" is false: the
    Dockerfile runs `uv sync --locked`). Fix the same claim in GOTCHAS.md ~:943-952.
  - Source: https://docs.astral.sh/uv/guides/integration/pytorch/ (landscape research 2026-10-08).
- **Guards.**
  - `tests/test_dependency_lock.py`: replace `test_lock_contains_no_cuda_wheels` with a test that
    every `nvidia-*` package in the lock is reachable only through the `cuda` extra's torch.
    Parse `uv.lock` as TOML, walk the `torch` package entries, and check their
    `source.registry`/`marker`.
  - Keep `test_pyproject_pins_the_cpu_torch_index`, extended to the cu130 index and the
    `conflicts` table.
  - CI lint (~:155-180): replace the "lock is CPU-only" grep with the same structural check (a
    small script, e.g. `scripts/check_lock_cuda_scope.py`, shared by the test and CI). Keep the
    "synced environment has no nvidia-*" step unchanged after `uv sync --extra dev --extra cpu`.
  - Update every `uv sync` invocation in `.github/workflows/ci.yml`, `kit_tools/worktree.yaml`
    (`env_bootstrap`), CLAUDE.md "Development", `kit_tools/docs/LOCAL_DEV.md` and
    TESTING_GUIDE to `--extra dev --extra cpu`. Pin the CI change in `tests/test_ci_workflow.py`.
- **DECISIONS.md:** add a dated entry superseding :522 ("the image never needs a GPU").

**Acceptance Criteria:**
- [ ] `pyproject.toml` declares conflicting `cpu`/`cuda` extras, both indexes, and the marker-scoped
      sources. `uv lock --check` passes, and the stale comments are corrected.
- [ ] Every `nvidia-*` package in `uv.lock` is reachable only from the `cuda` extra on
      `platform_machine == 'x86_64'`. A shared checker script asserts this, used by a test and by
      CI, and a planted violation fails it (test).
- [ ] CI and the docs install with `--extra dev --extra cpu`. CI's synced-environment check still
      finds no `nvidia-*`. `kit_tools/worktree.yaml` `env_bootstrap` is updated.
- [ ] The DECISIONS.md entry records the ruling and supersedes :522.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: One Dockerfile that installs CUDA torch on amd64 and CPU torch on arm64

**Priority:** P1

**Description:** As an operator, I want the same `forage:<version>` tag to run on a CPU host or a
GPU host, so that I install once and pick the device with an environment variable.

**Independent Test:** Build the image for amd64: `python -c "import torch; print(torch.__version__)"`
prints `+cu130`, and with no GPU the default boot is healthy-degraded exactly as today. The
arm64 build prints `+cpu`. `tests/test_dockerfile.py` passes, including no-`ARG`.

**Implementation Hints:**
- **Sync step** (`Dockerfile` ~:137-143): wrap `uv sync --locked --no-dev --no-install-project` so
  the extra comes from the architecture.

  ```
  arch="$(dpkg --print-architecture)"
  case "$arch" in
    amd64) extra=cuda ;;
    arm64) extra=cpu ;;
    *) echo "unsupported arch" >&2; exit 1 ;;
  esac
  uv sync ... --extra "$extra"
  ```

  This follows the oras precedent (~:106-119). Never `ARG TARGETARCH`.
- **Base image.** Keep `python:3.12-slim` (digest-pinned). The cu130 wheels carry their CUDA
  runtime through `nvidia-*` pip packages, so no `nvidia/cuda` base image is needed. The host only
  needs the driver and the NVIDIA Container Toolkit.
- **`tests/test_dockerfile.py`:** add an assertion pinning the arch-to-extra mapping text. Keep
  every existing assertion: no ARG, one FROM, digest pins, `uv sync --locked`.
- **CI** (`.github/workflows/ci.yml`):
  - `build-amd64` (comment "~200 MB of CPU torch", timeout 45 min) now pulls about 3 GB. Raise the
    timeout if needed. GitHub runners have about 14 GB free; add a disk-free step if the build or
    the smoke tarball runs short (measure).
  - `secret-grep` scans larger layers; check its time.
  - `publish` builds arm64 under qemu, which is unchanged in size.
  - Record the measured amd64 image size and build time in Implementation Notes and
    `kit_tools/docs/CI_CD.md`.
- **Smoke** (`smoke` job ~:605, `contract_smoke.py`): add two runs of the candidate image with no
  GPU:
  - `FORAGE_DEVICE=cuda` with fallback `cpu`: expect `promptguard_device: "cpu"` and the failover
    reason. With no weights, `promptguard_unavailable` is also present. Assert the device field,
    and the reason if the classifier loaded. Spec 2 must have landed; this spec depends on spec 1
    only, so if spec 2 is not merged yet, assert on the log token
    `promptguard_device_failover reason=unavailable` instead. Decide by checking the branch and
    record which.
  - `FORAGE_DEVICE=cuda` with `FORAGE_DEVICE_FALLBACK=refuse`: the container exits non-zero
    within the smoke timeout.
  - Pin both in `tests/test_ci_workflow.py`.
- **Size ceiling.** If amd64 exceeds 5 GB, stop and record it for the owner. Do not drop CUDA.

**Acceptance Criteria:**
- [ ] The Dockerfile selects `cuda` on amd64 and `cpu` on arm64 from `dpkg --print-architecture`,
      with no `ARG`. `tests/test_dockerfile.py` pins the mapping, and all its existing assertions
      pass.
- [ ] The built amd64 image reports `torch` `+cu130` and arm64 reports `+cpu` (CI build-step
      checks). The amd64 size and build time are recorded, size ≤ 5 GB.
- [ ] CI smoke covers default boot, cuda with fallback `cpu`, and cuda with fallback `refuse`,
      with the expected outcomes, pinned in `tests/test_ci_workflow.py`.
- [ ] `secret-grep` and `publish` still pass. Any timeout or disk changes are recorded in
      CI_CD.md.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-003: `compose/gpu.yml` overlay and the install guide

**Priority:** P2

**Description:** As an operator, I want a one-line way to enable the GPU in Compose and a clear
install guide for CPU and GPU hosts, so that installing on either takes the same tag and a
documented switch.

**Independent Test:** Run `docker compose -f compose/minimal.yml -f compose/gpu.yml config`: it
renders `gpus: all` and `FORAGE_DEVICE: cuda` for the forage service. The base fragments render
unchanged. The install guide covers the driver, the Container Toolkit, the overlay and failover.

**Implementation Hints:**
- **`compose/gpu.yml`:** `services: forage: { gpus: all, environment: { FORAGE_DEVICE: cuda } }`.
  The service name must match the base fragments; check them. Compose v2.30+ supports `gpus:`
  (https://github.com/compose-spec/compose-spec/blob/main/05-services.md). Leave
  `FORAGE_DEVICE_FALLBACK` to the operator, defaulting to `cpu`.
- **Tests** (`tests/test_compose_fragments.py`): `_FRAGMENT_PATHS` (~:69) covers the base
  fragments, and their "identical envelope" tests must not include the overlay. Add separate
  overlay tests:
  - it parses;
  - it sets only `gpus` and `FORAGE_DEVICE`;
  - it pins no image.
- **Docs:**
  - **`docs/configuration.md`:** an "Installing on a GPU host" section. Cover the NVIDIA driver
    ≥ 580 (cu130), the NVIDIA Container Toolkit, the overlay command, `FORAGE_DEVICE_FALLBACK`,
    what `/health` shows, and sharing the card with Ollama (memory is not reserved; halve-then-
    failover behaviour).
  - **`README.md`:** the quickstart notes the overlay.
  - **`kit_tools/arch/INFRA_ARCH.md`:** rewrite :270 ("Torch is CPU-only by construction").
  - **`kit_tools/docs/DEPLOYMENT.md`:** GPU install steps.

**Acceptance Criteria:**
- [ ] `compose/gpu.yml` exists. The merged config renders `gpus: all` and `FORAGE_DEVICE: cuda`
      for the forage service, and the base fragments are unchanged (tests).
- [ ] The configuration doc's GPU section covers the driver ≥ 580, the Container Toolkit, the
      overlay, the fallback policy, `/health` and Ollama sharing. README, INFRA_ARCH :270 and
      DEPLOYMENT are updated.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

## Edge Cases

- **A macOS or Windows developer syncing:** torch comes from PyPI, with no CUDA. (US-001)
- **Someone syncs `--extra cuda` on a Mac:** the source marker does not apply, so they get the PyPI
  build. Document it. (US-001)
- **An unsupported architecture in the Dockerfile:** the build fails loudly. (US-002)
- **An old NVIDIA driver (< 580) on a GPU host:** CUDA reports unavailable, so the fallback policy
  applies, and the docs name the driver floor. (US-002, US-003)
- **A Compose version without `gpus:`:** documented as needing v2.30+. (US-003)

## Out of Scope

- A separate CUDA repo or tag suffix (rejected by the owner), and GPU on arm64.
- A CUDA base image, TensorRT, ONNX Runtime, and multi-GPU.

## Assumptions

- uv's conflicting extras work for this source layout, per the uv PyTorch guide. If they do not,
  stop and record it, rather than splitting into two images.
- GitHub-hosted runners can build and smoke a 4–5 GB image within raised timeouts.

## Technical Considerations

- **Invariant 2:** no build args, ever. Architecture selection happens inside `RUN`.
- **Size.** CPU-only amd64 users now pull about 4 GB. This is accepted by the owner's
  single-image ruling, and documented.

## Related Documentation

- `Dockerfile`, `.github/workflows/ci.yml`, [CI_CD.md](../docs/CI_CD.md),
  [DECISIONS.md](../arch/DECISIONS.md) :522, `compose/`

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** One lock with conflicting `cpu`/`cuda` extras and an arch split in `RUN`.
**Rationale:** The uv PyTorch guide supports this. Invariant 2 forbids build args, so the
`dpkg --print-architecture` precedent applies.
**Source:** https://docs.astral.sh/uv/guides/integration/pytorch/; `Dockerfile` ~:106-119.

**Decision:** cu130.
**Rationale:** It covers Ada and Blackwell, and needs driver ≥ 580 (thelab has 590). cu126 lacks
Blackwell.

**Decision:** A CUDA-built wheel in a CPU-only install is acceptable.
**Rationale:** Measured at parity on CPU (1,172 ms against 1,196 ms per window).

## Clarifications

### Session 2026-10-08
- **Q:** A separate repo, or a tag suffix? **A:** Neither. One image, configured at install, to
  avoid drift and maintenance; size is accepted.
- **Q:** Which CUDA build? **A:** cu130.
