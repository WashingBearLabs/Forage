<!-- Template Version: 2.5.0 -->
---
feature: unified-image
status: active
session_ready: true
depends_on: [inference-surface]
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

**Today.** The lock pins torch to the CPU index on Linux. Three guards enforce "no `nvidia-*`":
- `tests/test_dependency_lock.py` ~:34-58;
- the CI lint at `.github/workflows/ci.yml` ~:155-180, pinned by `tests/test_ci_workflow.py` ~:691;
- DECISIONS.md :522.

`Dockerfile` takes **no build arguments** (invariant 2), and `tests/test_dockerfile.py` pins its
text.

**The goal** (owner ruling 2026-10-08) is one image, repo and tag for CPU and GPU hosts.
- Measured on thelab, the CUDA-built torch wheel runs CPU inference at speed parity with the CPU
  wheel: 1,172 ms vs 1,196 ms per window (86M, 8 CPUs).
- Measured in validation (uv 0.9.28, scratch copy), the design below locks and resolves, with these
  corrections already applied:
  - torch lives **only in extras**;
  - the `cuda` extra on non-x86_64 Linux maps to the **CPU** index (otherwise arm64 pulls PyPI's
    CUDA torch);
  - the CUDA payload is `nvidia-*` **plus `cuda-*` and `triton`**.

**Design:**
- **Lock.** Conflicting extras `cpu` and `cuda`, each `torch==2.14.0`. Three sources:
  - `cpu` on Linux: `pytorch-cpu`;
  - `cuda` on Linux x86_64: `pytorch-cu130`;
  - `cuda` on Linux non-x86_64: `pytorch-cpu`.

  macOS and Windows use PyPI. Developers and CI use `--extra dev --extra cpu`.
- **Image.** The single Dockerfile picks the extra by `dpkg --print-architecture` inside `RUN`:
  amd64 → `cuda`, arm64 → `cpu`. No `ARG`. A build-time check verifies the torch suffix per
  architecture.
- **Guards.** A **behavioural** checker runs `uv export` for each install profile. It asserts:
  - the CUDA payload appears only in the `cuda` profile, and only for x86_64 Linux;
  - every torch and CUDA-payload wheel comes from an allowed index, with hashes.

  The existing "synced environment has no CUDA payload" CI step stays as an independent second
  guard.
- **Compose.** A `compose/gpu.yml` overlay requests **one** GPU and sets `FORAGE_DEVICE=cuda`.
  The base fragments are unchanged.

## Goals

- **Dev and CI installs stay CPU-only.** `uv sync --extra dev --extra cpu` on Linux x86_64 installs
  no CUDA payload (CI asserts it). CI sync time is recorded before and after, and grows by no more
  than 10%.
- **Each architecture gets the right torch.** The amd64 image has torch `+cu130`, the arm64 image
  `+cpu`, both checked at build time.
- **Size is gated.** The amd64 **compressed** registry size is ≤ 5 GB, recorded with the
  uncompressed size.
- **CPU numerics are proven on amd64.** In the candidate image, a seeded tiny DeBERTa on CPU gives
  logits equal to the committed CPU goldens (spec 1) within 1e-6. This proves the +cu130 CPU path
  scores like +cpu.
- **CI smoke without a GPU** covers three cases:
  - default boot works as today;
  - `cuda` with fallback `cpu` reports `promptguard_requested_device: "cuda"`;
  - `cuda` with fallback `refuse` exits non-zero before serving.

## User Stories

### US-001: Extras-only torch with three arch-scoped sources

**Priority:** P1

**Description:** As a maintainer, I want torch declared only in conflicting `cpu`/`cuda` extras,
with arch-scoped sources, so that the image can install CUDA torch on amd64 while every other install
stays CPU-only and a missing extra fails loudly.

**Independent Test:**
- `uv lock --check` passes.
- `uv export --frozen --extra dev --extra cpu` contains `torch==2.14.0+cpu` and no CUDA payload.
- `--extra cuda` resolves `+cu130` for x86_64 Linux and `+cpu` for aarch64 Linux.
- A bare `uv sync` without an extra makes `import torch` fail, with the conftest guard naming the fix.

**Implementation Hints:**
- **`pyproject.toml`** (~:37, :50-68):
  - Remove `torch>=2.2.0` from base dependencies.
  - Add `[project.optional-dependencies] cpu = ["torch==2.14.0"]` and `cuda = ["torch==2.14.0"]`.
  - Add `[tool.uv] conflicts = [[{ extra = "cpu" }, { extra = "cuda" }]]`.
  - Indexes `pytorch-cpu` and `pytorch-cu130` (`https://download.pytorch.org/whl/cu130`), both
    `explicit = true`.
  - Sources:
    ```
    torch = [
      { index = "pytorch-cpu",   extra = "cpu",  marker = "sys_platform == 'linux'" },
      { index = "pytorch-cu130", extra = "cuda", marker = "sys_platform == 'linux' and platform_machine == 'x86_64'" },
      { index = "pytorch-cpu",   extra = "cuda", marker = "sys_platform == 'linux' and platform_machine != 'x86_64'" },
    ]
    ```
  - Replace the stale comment at ~:55-59 ("the image … never reads this lock" is false: the
    Dockerfile runs `uv sync --locked`).
  - Source: https://docs.astral.sh/uv/guides/integration/pytorch/. Validation measured this
    layout locking under uv 0.9.28.
- **Lock.** Re-run `uv lock`. Expect three torch builds (PyPI, `+cpu`, `+cu130`) with
  conflict-scoped markers.
- **Guard for a forgotten extra.** In `tests/conftest.py`, at import time: if `import torch` fails,
  fail the session with "install with `uv sync --extra dev --extra cpu`". The image's build-time
  check (US-004) covers the runtime.
- Keep `tests/test_dependency_lock.py::test_pyproject_pins_the_cpu_torch_index`, extended to the
  cu130 index, the third source and the `conflicts` table.
  `test_lock_contains_no_cuda_wheels` is replaced in US-002; mark it in this story by moving it to
  US-002's checker, so the suite stays green across commits.

**Acceptance Criteria:**
- [ ] torch is only in the `cpu` and `cuda` extras (pinned `==2.14.0`), and the conflicts table and
      three sources are declared. `uv lock --check` passes, and the stale comment is replaced.
- [ ] These hold, as recorded command outputs in Implementation Notes:
  - `uv export --frozen --extra dev --extra cpu` resolves `torch==2.14.0+cpu`;
  - `--extra cuda` resolves `+cu130` under an x86_64 Linux marker;
  - `--extra cuda` resolves `+cpu` under an aarch64 Linux marker.
- [ ] A missing torch fails the test session with the install hint (test that simulates the import
      failure).
- [ ] The pyproject pin test covers the cu130 index, the third source and the conflicts table.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: A behavioural CUDA-scope checker, with sources and hashes

**Priority:** P1

**Description:** As a maintainer, I want a checker that proves the CUDA payload reaches only amd64
`cuda` installs from allowed indexes with hashes, used by both the tests and CI, so that a lock
mistake can never put CUDA wheels into dev or CI environments, or an unhashed binary into the image.

**Independent Test:** The checker passes on the real lock and fails on each planted violation.

**Implementation Hints:**
- **New `scripts/check_lock_cuda_scope.py`.**
  - Define the **CUDA payload** once: package names matching `^(nvidia-|cuda-)` or exactly
    `triton`.
  - Run `uv export --frozen --no-hashes --format requirements-txt` for these profiles:
    - `--extra dev --extra cpu`: no payload;
    - `--extra cpu`: no payload;
    - `--extra cuda`: payload allowed, but every payload line's marker must imply
      `platform_machine == 'x86_64'` and `sys_platform == 'linux'`.
  - Behavioural export avoids hand-parsing uv's internal conflict markers, which validation found
    fragile (e.g. `extra == 'extra-1-<project>-cuda'` and impossible conjunctions).
  - **Provenance check.** Parse `uv.lock` as TOML. For every `torch` and payload package:
    - its `source.registry` must be in the allowlist {pytorch-cpu, pytorch-cu130, PyPI}, where
      PyPI applies only to the macOS/Windows torch and the nvidia payload the cu130 index
      redirects to (check how uv records it);
    - every wheel must carry a non-empty `hash`;
    - payload names must match a committed allowlist (`scripts/cuda_payload_allowlist.txt`), so a
      new CUDA package cannot appear silently.
  - Print names only, and exit non-zero on any violation.
- **Tests** (`tests/test_dependency_lock.py`): run the checker on the committed lock. Add planted
  violations in temp copies:
  - a payload under a base dependency;
  - a payload under the `cpu` extra;
  - a payload under `cuda` without the x86_64 marker;
  - a torch `+cu130` resolving on aarch64;
  - a wheel without a hash;
  - an unknown payload name.

  Each must fail. Delete `test_lock_contains_no_cuda_wheels`.
- **CI lint** (~:155-180): replace the lock grep with `uv run python -m scripts.check_lock_cuda_scope`.
  - Keep the synced-environment step, but widen its pattern to the payload regex.
  - Pin both in `tests/test_ci_workflow.py` (~:691).

**Acceptance Criteria:**
- [ ] `scripts/check_lock_cuda_scope.py` passes on the committed lock and enforces three things:
      payload scope per profile, allowed sources with hashes, and the payload-name allowlist.
- [ ] Each of the six planted violations makes it fail (one test each).
- [ ] CI lint runs the checker, and the CI synced-environment step uses the widened payload regex.
      Both are pinned in `tests/test_ci_workflow.py`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-003: Sweep install commands and CPU-only claims

**Priority:** P1

**Description:** As a maintainer, I want every install command to name the `cpu` extra, and every
"CPU-only" claim corrected, so that no environment silently lacks torch and the docs describe the
real image.

**Independent Test:**
- `grep -rn "uv sync"` across the repo, excluding archives, finds no invocation lacking an extra,
  except where intentional and listed.
- A grep for `CPU-only|pytorch-cpu|no nvidia` finds only accurate statements.

**Implementation Hints:**
- **Sync sites** (all of them):
  - `.github/workflows/ci.yml` lint (~:152), typecheck (~:279), test (~:316), smoke (~:648) and
    searxng-smoke (~:1480): decide searxng-smoke explicitly (it needs `--extra cpu` if it imports
    torch);
  - `tests/test_ci_workflow.py:3464` (the literal `uv sync --extra dev --locked`) and the ~:685
    region;
  - `kit_tools/worktree.yaml` `env_bootstrap` (**critical**: without it the orchestrator installs
    no torch);
  - `CLAUDE.md` Development, `README.md` ~:163, `kit_tools/docs/LOCAL_DEV.md`,
    `kit_tools/testing/TESTING_GUIDE.md`, `kit_tools/docs/CI_CD.md` ~:162/:420.
- **Claim sweep:**
  - `Dockerfile` header comment (~:22-23, "exactly the CPU-only torch uv.lock pins");
  - `kit_tools/arch/INFRA_ARCH.md` :96 and :270;
  - `kit_tools/arch/CODE_ARCH.md` :56 and :62;
  - `kit_tools/docs/LOCAL_DEV.md` ~:383;
  - `kit_tools/docs/GOTCHAS.md` ~:943-952;
  - `kit_tools/docs/CI_CD.md`;
  - `docs/releases.md` (size statements).
- **DECISIONS.md:** a dated entry superseding :522, recording the single-image ruling and cu130.
- Docs and config only, plus the CI pin test. No rotation.

**Acceptance Criteria:**
- [ ] Every listed sync site uses `--extra dev --extra cpu`, or `--extra cpu`, and
      `tests/test_ci_workflow.py` pins the new strings. A repo grep (excluding `kit_tools/specs/archive/`)
      finds no extra-less `uv sync`, and the grep is recorded.
- [ ] `kit_tools/worktree.yaml` `env_bootstrap` is `uv sync --extra dev --extra cpu`.
- [ ] Every listed CPU-only claim is corrected, and a recorded grep shows only accurate statements.
- [ ] The DECISIONS.md entry supersedes :522.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

### US-004: One Dockerfile that installs CUDA torch on amd64 and CPU torch on arm64

**Priority:** P1

**Description:** As an operator, I want the same `forage:<version>` tag to run on CPU or GPU hosts,
with the image proving at build time that it carries the right torch, so that I install once and
pick the device with an environment variable.

**Independent Test:**
- The amd64 build's check prints `+cu130`, and the arm64 build's prints `+cpu`.
- In the amd64 image, a seeded tiny model's CPU logits equal the spec-1 goldens within 1e-6.
- `tests/test_dockerfile.py` passes, with no `ARG`.

**Implementation Hints:**
- **The sync RUN** (`Dockerfile` ~:137-143). Use the oras pattern (~:106-119), `set -eu`:
  `arch="$(dpkg --print-architecture)"`, then
  `case "$arch" in amd64) extra=cuda ;; arm64) extra=cpu ;; *) echo "unsupported architecture" >&2; exit 1 ;; esac`,
  then `uv sync --locked --no-dev --no-install-project --extra "$extra"`.
  - Never `ARG TARGETARCH`.
  - Keep `rm -rf /root/.cache/uv`, or use `--no-cache`, to limit layer size.
- **Build-time torch check.** Extend the existing import smoke (~:214) to assert
  `torch.__version__` ends with `+cu130` on amd64 and `+cpu` on arm64. The same `case` mapping makes
  a mismatch fail the build.
- **Base image.** Keep `python:3.12-slim` (digest-pinned). The cu130 wheels carry the CUDA runtime as
  pip packages, so the host needs only the driver and the NVIDIA Container Toolkit.
- **`tests/test_dockerfile.py`.** Pin the arch-to-extra mapping, using the oras mapping test as the
  template. Check that the `uv sync --locked` assertion (~:480-500) still matches on the joined
  RUN; read how `instructions` is built.
- **CPU numeric parity.** Add a CI step after the build:
  - mount `tests/golden/promptguard_cpu_scores.json` and the tiny-model fixture script into the
    amd64 candidate;
  - run the seeded tiny DeBERTa on CPU;
  - compare with the goldens at 1e-6;
  - record the max difference.
- **Size.**
  - Record the **compressed** size (sum of `docker manifest inspect` layer sizes, or the
    zstd-tarball size) as the gate, ≤ 5 GB.
  - Record the uncompressed size and `docker history` per-layer sizes.
  - If over the gate, stop and record for the owner; do not drop CUDA.

**Acceptance Criteria:**
- [ ] The Dockerfile selects `cuda` on amd64 and `cpu` on arm64 inside `RUN`, with no `ARG`.
      `tests/test_dockerfile.py` pins the mapping, and every existing assertion passes.
- [ ] The build-time check asserts the torch suffix per architecture and fails on mismatch (test
      on the Dockerfile text; CI build).
- [ ] The CI CPU-parity step passes in the amd64 candidate within 1e-6, with the max difference
      recorded.
- [ ] Compressed and uncompressed amd64 sizes and per-layer sizes are recorded in
      Implementation Notes and `kit_tools/docs/CI_CD.md`, with compressed size ≤ 5 GB.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-005: CI budgets and no-GPU failover smokes

**Priority:** P1

**Description:** As a maintainer, I want CI to build, scan, smoke and publish the larger image within
recorded budgets, and to prove both failover policies on a GPU-less runner, so that the release lane
stays reliable and the failover contract is tested on every PR.

**Independent Test:** On a CI run of the branch:
- build-amd64, secret-grep, smoke and the new failover smokes are green;
- the measured durations, artifact size and disk headroom are recorded;
- `tests/test_ci_workflow.py` pins the smoke steps.

**Implementation Hints:**
- **Baseline first.** Record the current (v1.3.0) durations for build-amd64, secret-grep, smoke and
  the artifact upload and download, the artifact size, and `uv sync` time, from a recent `main` run.
- **Image handoff.** `build-amd64` runs `docker save | zstd` and uploads an artifact (~:488-540) that
  secret-grep, smoke and publish each download and load. Record the artifact size and the
  per-consumer transfer and load times.
  - Add a disk-free step pre-emptively to build-amd64 and the consumers: remove preinstalled
    toolchains, and assert ≥ 20 GB free with `df`.
  - Set explicit `timeout-minutes` from the measurements × 1.5.
  - Check the GHA cache size after a `main` push.
- **Smokes:**
  - Extend `contract_smoke.py` with an `--expect-requested-device` / `--expect-device` mode, and
    update `tests/test_contract_smoke.py`.
  - Reuse the smoke job's container naming, cleanup and health-wait.
  - **Run 2:** `FORAGE_DEVICE=cuda` (fallback `cpu`, no GPU, no weights). Expect
    `promptguard_requested_device: "cuda"`, `promptguard_device: null` (unloaded) and
    `promptguard_unavailable`, plus the log token `promptguard_device_probe result=unavailable`.
  - **Run 3:** `FORAGE_DEVICE=cuda` with `FORAGE_DEVICE_FALLBACK=refuse`. `docker wait` returns a
    non-zero exit within the smoke timeout, and the logs contain the fixed
    `DeviceConfigurationError` message, not a traceback with values.
  - Pin these in `tests/test_ci_workflow.py`.
- **Publish.** arm64 builds under qemu, unchanged in size. Confirm the layer-identity (diff_ids)
  gate still passes with the large CUDA layer.

**Acceptance Criteria:**
- [ ] Baseline and post-change durations, artifact size and `uv sync` time are recorded in
      Implementation Notes and CI_CD.md. CI `uv sync` time grows by no more than 10%.
- [ ] build-amd64 and the image consumers have a disk-free step asserting ≥ 20 GB free, and
      explicit `timeout-minutes` set to measurement × 1.5, pinned in `tests/test_ci_workflow.py`.
- [ ] Smoke run 2 asserts the requested device, a null active device and the probe token. Run 3
      asserts a non-zero exit and the fixed error message. `contract_smoke.py` and its tests are
      extended.
- [ ] A branch CI run is green across lint, typecheck, test, build-amd64, secret-grep and smoke.
      The run URL is recorded.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

### US-006: The `compose/gpu.yml` overlay and the GPU install guide

**Priority:** P2

**Description:** As an operator, I want a one-line way to enable one GPU in Compose and a clear
install guide, so that installing on a GPU host uses the same tag and a documented switch.

**Independent Test:**
- `docker compose -f compose/minimal.yml -f compose/gpu.yml config` renders a single-GPU device
  request and `FORAGE_DEVICE: cuda`, with ports unchanged.
- The same holds with `compose/full.yml`.
- The base fragments are byte-unchanged.

**Implementation Hints:**
- **`compose/gpu.yml`.** Request **one** GPU, not all of them:
  `deploy.resources.reservations.devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]`
  (or `gpus` with `count: 1`; check which the pinned Compose spec renders). Add
  `environment: { FORAGE_DEVICE: cuda }`.
  - The service name must match the base fragments.
  - No image pin, and no port change.
  - Source: https://github.com/compose-spec/compose-spec/blob/main/05-services.md.
- **Tests** (`tests/test_compose_fragments.py`). Keep `_FRAGMENT_PATHS` (~:69) for the base
  fragments, with their identical-envelope tests unmodified. Add overlay tests:
  - it parses;
  - it sets only the device request and `FORAGE_DEVICE`;
  - it pins no image;
  - it merges onto both base fragments without changing ports or the `127.0.0.1` binding.
- **Docs:**
  - **`docs/configuration.md` "Installing on a GPU host":**
    - the NVIDIA driver ≥ 580 (cu130) and the NVIDIA Container Toolkit;
    - the overlay command and `FORAGE_DEVICE_FALLBACK`;
    - the `/health` state table (spec 2);
    - GPU sharing (memory is not reserved; Ollama coexistence);
    - that the GPU is a shared, non-isolated resource: do not use it on multi-tenant hosts, and
      keep the loopback binding;
    - that a host without the Container Toolkit fails at `compose up` (a runtime error, not a
      degraded boot);
    - that Compose ≥ 2.30 is required.
  - **`README.md`:** the quickstart shows the overlay command.
  - **`kit_tools/docs/DEPLOYMENT.md`:** the GPU steps.

**Acceptance Criteria:**
- [ ] `compose/gpu.yml` requests one GPU and sets `FORAGE_DEVICE: cuda`. Merged with each base
      fragment, ports and binding are unchanged, and the base fragments are byte-unchanged
      (tests).
- [ ] The `docs/configuration.md` GPU section covers each listed topic.
- [ ] README shows the exact overlay command, and DEPLOYMENT lists the driver, Toolkit and overlay
      steps.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

## Edge Cases

- **`--extra cuda` on a Mac:** PyPI torch is used (CPU on macOS). Documented. (US-001)
- **A bare `uv sync` without an extra:** no torch, so the tests fail with the hint. The image always
  passes an extra. (US-001)
- **An unsupported architecture:** the build fails loudly. (US-004)
- **A driver older than 580:** CUDA is unavailable, so the fallback policy applies. Documented.
  (US-004, US-006)
- **No Container Toolkit:** `compose up` fails. Documented. (US-006)
- **Runner disk exhaustion:** the disk-free assertion fails first, with a clear message. (US-005)

## Out of Scope

- A separate repo or tag (rejected), GPU on arm64, CUDA base images, TensorRT, ONNX Runtime and
  multi-GPU.

## Assumptions

- The uv conflicting-extras layout behaves as validation measured (uv 0.9.28). If the CI uv
  version differs, the lock check catches it.
- GitHub-hosted runners can build and transfer a 4–5 GB image with the added disk cleanup.

## Technical Considerations

- **Invariant 2.** Architecture selection happens only inside `RUN`.
- **Size.** CPU-only amd64 users pull about 3 GB compressed. This is accepted under the
  single-image ruling, and documented.
- **No hashed source changes** in this spec.

## Related Documentation

- `Dockerfile`, `.github/workflows/ci.yml`, [CI_CD.md](../docs/CI_CD.md),
  [DECISIONS.md](../arch/DECISIONS.md) :522, `compose/`

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** torch only in extras, with three sources (arm64 `cuda` maps to the CPU index).
**Rationale:** Validation measured that without the third source `--extra cuda` on aarch64 pulls
PyPI's CUDA torch and the whole payload.

**Decision:** A behavioural checker via `uv export` profiles, a payload regex including `cuda-*` and
`triton`, and a provenance check with hashes.
**Rationale:** uv's conflict markers are internal and fragile to parse. torch+cu130 also pulls
`cuda-toolkit`, `cuda-bindings` and `triton` (248 MB). The image is public, so binary provenance
matters.

**Decision:** cu130.
**Rationale:** It covers Ada and Blackwell, with driver ≥ 580 (thelab has 590).

**Decision:** The overlay requests one GPU.
**Rationale:** Least privilege on shared hosts (validation round 1, security).

### Scope Adjustments

- Round 1 split the spec into six stories (lock; checker; sweep; Dockerfile; CI; Compose). It also:
  - made spec 2 a dependency, so the smokes can assert `/health` device fields;
  - added the CPU numeric-parity check and the compressed-size gate.

## Clarifications

### Session 2026-10-08
- **Q:** Separate repo or tag suffix? **A:** Neither. One image, configured at install.
- **Q:** Which CUDA build? **A:** cu130.
