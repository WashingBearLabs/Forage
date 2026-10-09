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

**Today:**
- the lock pins torch to the CPU index on Linux;
- three guards enforce "no `nvidia-*`": `tests/test_dependency_lock.py` ~:34-58, CI lint
  `.github/workflows/ci.yml` ~:155-180 (pinned by `tests/test_ci_workflow.py` ~:691), and
  DECISIONS.md :522;
- `Dockerfile` takes **no build arguments** (invariant 2), and `tests/test_dockerfile.py` pins its
  text.

**The goal:** one image, repo and tag for CPU and GPU hosts (owner ruling 2026-10-08). Measured on
thelab, the CUDA-built torch wheel runs CPU inference at speed parity with the CPU wheel (86M,
8 CPUs):

| torch wheel | ms per window |
|---|---|
| CUDA build (`+cu130`) | 1,172 |
| CPU build (`+cpu`) | 1,196 |

**Measured by validation (uv 0.9.28, scratch copy).** The layout below locks and exports correctly.
- `uv.lock` records `source.registry` as URLs:
  - torch `+cpu` from `https://download.pytorch.org/whl/cpu`;
  - torch `+cu130` from `https://download.pytorch.org/whl/cu130`;
  - the CUDA payload (`nvidia-*`, `cuda-*`, `triton`, about 18 packages) from `https://pypi.org/simple`;
  - macOS/Windows torch from PyPI.
- After the re-lock, `uv.lock` always contains the payload.
- A plain `uv sync --extra dev` **removes torch**, because sync is exact. So the install commands
  change in the **same story** as the re-lock.

**Design:**
- **Lock.** Conflicting extras `cpu`/`cuda`, each `torch==2.14.0`, with three sources:
  - `cpu` on Linux: `pytorch-cpu`;
  - `cuda` on Linux x86_64: `pytorch-cu130`;
  - `cuda` on Linux non-x86_64: `pytorch-cpu`.

  Dev and CI use `--extra dev --extra cpu`.
- **Image.** One Dockerfile picks the extra by `dpkg --print-architecture` inside `RUN` (amd64 →
  `cuda`, arm64 → `cpu`), with no `ARG`. At build time it verifies the torch suffix and that the
  installed CUDA payload matches the allowlist (none on arm64).
- **Guards.** A behavioural checker on `uv export` profiles, using `packaging.markers` evaluation,
  plus provenance per package class. The CI "synced environment has no CUDA payload" step remains an
  independent second guard.
- **Compose.** A `compose/gpu.yml` overlay requests **one** GPU and sets `FORAGE_DEVICE=cuda`.

## Goals

- **Dev and CI keep their CPU install set.** The `--extra dev --extra cpu` export after the change
  lists exactly the same packages and versions as the pre-change `--extra dev` export (the
  install-set proof, instead of a timing bar). CI's synced environment has no CUDA payload.
- **Build-time checks.** The amd64 image has torch `+cu130` and the allowlisted payload. The arm64
  image has `+cpu` and no payload.
- **Size gate.** The amd64 **sum of per-layer gzip sizes** (computed in CI) is ≤ 5 GB, with the
  largest layer recorded.
- **CPU numerics on amd64.** The tiny-model builder (spec 1) gives `==` scores in the amd64 candidate
  (`+cu130`, CPU) and in CI's `+cpu` environment. Any mismatch stops for an owner decision.
- **No-GPU CI smoke:** default boot works; `cuda` + `cpu` reports the requested device; `cuda` +
  `refuse` exits non-zero.

## User Stories

### US-001: Extras-only torch, arch-scoped sources, and every install command in one commit

**Priority:** P1

**Description:** As a maintainer, I want torch moved into conflicting `cpu`/`cuda` extras with
arch-scoped sources, and every install command and lock guard updated in the same story, so that no
commit leaves CI, the orchestrator worktree or a developer without torch, or red.

**Independent Test:**
- `uv lock --check` passes.
- The `--extra dev --extra cpu` export equals the pre-change `--extra dev` export.
- `--extra cuda` resolves `+cu130` for x86_64 Linux and `+cpu` for aarch64 Linux.
- The suite and CI lint pass at this story's commit.

**Implementation Hints:**
- **`pyproject.toml`** (~:37, :50-68):
  - remove `torch>=2.2.0` from base dependencies;
  - add `[project.optional-dependencies] cpu = ["torch==2.14.0"]` and `cuda = ["torch==2.14.0"]`;
  - add a new `[tool.uv]` table with `conflicts = [[{ extra = "cpu" }, { extra = "cuda" }]]` (none
    exists today);
  - indexes `pytorch-cpu` and `pytorch-cu130`, both `explicit = true`;
  - torch sources:
    ```
    { index = "pytorch-cpu",   extra = "cpu",  marker = "sys_platform == 'linux'" },
    { index = "pytorch-cu130", extra = "cuda", marker = "sys_platform == 'linux' and platform_machine == 'x86_64'" },
    { index = "pytorch-cpu",   extra = "cuda", marker = "sys_platform == 'linux' and platform_machine != 'x86_64'" },
    ```
  - replace the stale comment at ~:55-59.
  - Source: https://docs.astral.sh/uv/guides/integration/pytorch/.
- **Same-commit install commands** (sync is exact):
  - **Sync sites:**
    - `.github/workflows/ci.yml` lint (~:152), typecheck (~:279), test (~:316), smoke (~:648) and
      searxng-smoke (~:1480). Give searxng-smoke `--extra cpu` too if it imports torch; check and
      record.
    - `kit_tools/worktree.yaml` `env_bootstrap`.
    - `CLAUDE.md` Development, `README.md` ~:163, `kit_tools/docs/LOCAL_DEV.md`,
      `kit_tools/testing/TESTING_GUIDE.md`, `kit_tools/docs/CI_CD.md` ~:162/:420.
  - **Pins:** `tests/test_ci_workflow.py` ~:3464 (the literal `uv sync --extra dev --locked`) and
    ~:685.
- **Same-commit guard change.**
  - Delete `tests/test_dependency_lock.py::test_lock_contains_no_cuda_wheels`, and delete the CI
    lint "Assert the committed lock is CPU-only" grep step.
  - Add a temporary inline test: `uv export --frozen --extra dev --extra cpu` contains no line
    matching `^(nvidia-|cuda-|triton==)`. US-002 lifts it into the checker.
  - Widen the CI synced-environment step's pattern now. That step reads installed-package
    listings (`name version`), not export lines, so its regex is `^(nvidia-|cuda-|triton\b)`.
- **Missing-extra guard.** In `tests/conftest.py`, if `import torch` fails, fail the session with
  "install with `uv sync --extra dev --extra cpu`".
- **Install-set proof.** Before changing anything, record `uv export --frozen --extra dev` (the
  pre-change export) to the scratchpad. After the re-lock, diff it against
  `--extra dev --extra cpu`; the result must be identical.
- **Pin test.** Extend `test_pyproject_pins_the_cpu_torch_index` to the cu130 index, the third
  source and the `conflicts` table.

**Acceptance Criteria:**
- [x] torch is only in the `cpu` and `cuda` extras (`==2.14.0`), with the conflicts table and three
      sources. `uv lock --check` passes, and the stale comment is replaced.
- [x] The post-change `--extra dev --extra cpu` export is identical to the pre-change `--extra dev`
      export, with the diff command recorded. `--extra cuda` gives `+cu130` under the x86_64 Linux
      marker and `+cpu` under the aarch64 Linux marker (recorded outputs).
- [x] Every listed sync site uses the `cpu` extra in this commit. `tests/test_ci_workflow.py` pins
      the new strings, and `kit_tools/worktree.yaml` `env_bootstrap` is
      `uv sync --extra dev --extra cpu`. A repo grep (excluding archives) finds no extra-less
      `uv sync`; the grep is recorded.
- [x] The old lock-grep test and CI grep step are gone, the temporary export-based assertion passes,
      and the widened CI environment pattern is pinned.
- [x] A missing torch fails the session with the install hint (test).
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .` and `uv run ruff format --check .` pass
- [x] `uv run pyright` passes with zero errors

### US-002: A behavioural CUDA-scope checker with per-class provenance

**Priority:** P1

**Description:** As a maintainer, I want one checker that proves the torch variant and the CUDA
payload per install profile, plus provenance and hashes per package class, used by both the tests
and CI. Then a lock mistake can never put CUDA into dev or CI environments, or an unexpected binary
into the public image.

**Independent Test:** The checker passes on the real lock and fails on each planted violation.

**Implementation Hints:**
- **New `scripts/check_lock_cuda_scope.py`** (stdlib, `tomllib`, `packaging`):
  - **Payload:** names matching `^(nvidia-|cuda-)`, or exactly `triton`, compared **after PEP 503
    normalisation** (`re.sub(r"[-_.]+", "-", name).lower()`) on both sides, including the
    allowlist.
  - **Profiles:** run `uv export --frozen --no-hashes --format requirements-txt` for
    `--extra dev --extra cpu`, `--extra cpu` and `--extra cuda`.
  - **Marker evaluation:** use `packaging.markers.Marker(...).evaluate(env)` under explicit
    environments: linux/x86_64, linux/aarch64, darwin/arm64 and win32/AMD64. Never reason about
    "implies" textually.
  - **Rules:**
    1. **Payload scope.** `dev+cpu` and `cpu` export no payload line. In `cuda`, every payload line
       evaluates true only on linux/x86_64.
    2. **Torch variant per profile.**
       - `cpu`: on any Linux environment the selected torch line is `+cpu`.
       - `cuda`: on linux/x86_64 it is `+cu130`, and on linux/aarch64 it is `+cpu`.
       - On macOS/Windows it is plain PyPI.
       - A torch line without a local suffix must evaluate false on every Linux environment.
    3. **Provenance** (parse `uv.lock` as TOML; registry **URLs**, measured):
       - Linux torch comes from `https://download.pytorch.org/whl/cpu` or `/cu130`, and PyPI
         torch only for non-Linux;
       - payload packages come from `https://pypi.org/simple`;
       - every wheel has a non-empty `hash`;
       - wheel-URL hosts are not checked (`download-r2.pytorch.org` serves `+cpu`). Say so in the
         docstring.
    4. **Allowlist.** Payload names match a committed `scripts/cuda_payload_allowlist.txt`.
  - Print names only, and exit non-zero on any violation.
- **Tests** (`tests/test_dependency_lock.py`; replaces US-001's temporary assertion). Each planted
  violation lives in a temp copy and must fail:
  1. a payload under a base dependency;
  2. a payload under `cpu`;
  3. a payload under `cuda` without the x86_64 marker;
  4. torch `+cu130` resolving on aarch64;
  5. Linux torch from PyPI;
  6. a wheel without a hash;
  7. an unknown payload name;
  8. a lock resolving both extras together (conflicts table removed).
- **CI lint.** Run `uv run python -m scripts.check_lock_cuda_scope`, pinned in
  `tests/test_ci_workflow.py`.

**Acceptance Criteria:**
- [x] The checker enforces all four rules with marker evaluation under explicit environments, and
      passes on the committed lock.
- [x] Each of the eight planted violations fails it (one test each). The temporary US-001 assertion
      is removed.
- [x] CI lint runs the checker (pinned).
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .` and `uv run ruff format --check .` pass
- [x] `uv run pyright` passes with zero errors

### US-003: Correct the CPU-only claims in the docs

**Priority:** P2

**Description:** As a maintainer, I want every "CPU-only" statement about the image corrected, so
that the docs describe the image that ships.

**Independent Test:** A grep for `CPU-only|pytorch-cpu|no nvidia|nvidia-` (excluding archives)
equals the recorded list of expected surviving matches.

**Implementation Hints:**
- **Correct these sites:**
  - `Dockerfile` header comment (~:22-23);
  - `kit_tools/arch/INFRA_ARCH.md` :96 and :270;
  - `kit_tools/arch/CODE_ARCH.md` :56 and :62;
  - `kit_tools/docs/LOCAL_DEV.md` ~:383;
  - `kit_tools/docs/GOTCHAS.md` ~:943-952 (its "image never reads the lock" claim is false);
  - `kit_tools/docs/CI_CD.md`;
  - `docs/releases.md` (size statements).
- **Expected surviving matches** (record them): `pyproject.toml` (index name), the checker and its
  tests, the CI step names, and the new DECISIONS entry.
- **DECISIONS.md:** a dated entry superseding :522 (single image, cu130, three sources).
- Docs only. No rotation.

**Acceptance Criteria:**
- [x] Every listed site is corrected, and the grep output equals the recorded expected list.
- [x] The DECISIONS.md entry supersedes :522.
- [x] Full test suite passes (`uv run pytest`)

### US-004: One Dockerfile with arch-selected torch and build-time content checks

**Priority:** P1

**Description:** As an operator, I want the same `forage:<version>` tag to run on CPU or GPU hosts,
with the image proving at build time that it carries the right torch and payload, and that CPU scores
are unchanged on amd64. Then I install once and pick the device at install time.

**Independent Test:**
- amd64 build checks: `+cu130`, with the allowlisted payload installed.
- arm64 build checks: `+cpu`, with no payload.
- The CI CPU-parity step gives `==` scores between the amd64 candidate and the CI `+cpu`
  environment.
- `tests/test_dockerfile.py` passes, with no `ARG`.

**Implementation Hints:**
- **Sync RUN** (`Dockerfile` ~:137-143), oras pattern (~:106-119), `set -eu`:
  - `arch="$(dpkg --print-architecture)"`;
  - `case` maps amd64 → `cuda`, arm64 → `cpu`, and anything else fails with "unsupported
    architecture";
  - then `uv sync --locked --no-dev --no-install-project --extra "$extra"`, with `--no-cache` or the
    existing `rm -rf /root/.cache/uv`.
  - Never `ARG TARGETARCH`.
- **Build-time checks.** Extend the import smoke (~:214) with a small inline Python step that:
  - asserts `torch.__version__` ends `+cu130` (amd64) or `+cpu` (arm64), using the same mapping;
  - lists installed distributions via `importlib.metadata`: on arm64 none may match the payload
    regex, and on amd64 the matches must equal `scripts/cuda_payload_allowlist.txt`. Both sides are
    PEP 503-normalised. COPY that file into a build-only location and remove it after.
- **Where the arm64 checks run.** PR CI builds amd64 only. The arm64 build-time checks run in the
  publish lane's multi-arch build, where a failing assertion fails the build before any push. The
  PR-time arm64 guarantee is static: US-002's checker rule 2 (linux/aarch64 resolves `+cpu`, no
  payload). No qemu build is added to PR CI. Record this split in CI_CD.md.
- **`tests/test_dockerfile.py`.** Pin the mapping and the checks, using the oras mapping test as the
  template, and check that the `uv sync --locked` assertion (~:480-500) still matches the joined
  RUN.
- **CPU parity CI step.**
  - In the same job, run `scripts/promptguard_tiny_model.py` (spec 1, pytest-free; its `__main__`
    prints one `float.hex()` score per line) twice:
    - in the amd64 candidate, with `--no-dev` dependencies only:
      `docker run --rm --entrypoint /app/.venv/bin/python -e CUDA_VISIBLE_DEVICES= -v "$PWD/scripts:/work/scripts:ro" -w /work <candidate> scripts/promptguard_tiny_model.py > image.txt`
      (confirm the venv path against the Dockerfile and record it);
    - in CI's `+cpu` environment: `uv run python scripts/promptguard_tiny_model.py > ci.txt`.
  - `diff image.txt ci.txt`.
  - **Expect `==`.** If they differ, record the max difference and **stop for an owner
    decision**: accept a 1e-6 tolerance, and correct the epic's "byte-identical" wording for amd64
    CPU installs.
- **Size.** In CI, sum the per-layer gzip sizes (`docker save`, gzip each layer tar, sum) as the
  gate (≤ 5 GB). Record the uncompressed size and the largest layer. If over the gate, stop for the
  owner; do not drop CUDA.

**Acceptance Criteria:**
- [x] The Dockerfile selects the extra inside `RUN`, with no `ARG`. `tests/test_dockerfile.py` pins
      the mapping and the build-time checks, and its existing assertions pass.
- [x] The build-time checks assert the torch suffix and the payload set per architecture, and fail
      on mismatch.
- [x] The CI CPU-parity step compares the amd64 candidate with the CI `+cpu` environment and passes
      on `==`, or the mismatch is recorded with an owner decision.
- [x] The gzip-sum size, uncompressed size and largest layer are recorded in Implementation Notes and
      CI_CD.md, with gzip-sum ≤ 5 GB.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .` and `uv run ruff format --check .` pass
- [x] `uv run pyright` passes with zero errors

### US-005: CI disk, timeout and cache budgets for the larger image

**Priority:** P1

**Description:** As a maintainer, I want CI's image jobs given explicit disk, timeout and cache
budgets for a ~3 GB dependency layer, so that the release lane stays reliable.

**Independent Test:** `tests/test_ci_workflow.py` pins the disk-free steps, the timeout values and
the cache decision. The baseline figures are recorded.

**Implementation Hints:**
- **Baseline.** From a recent `main` run, record the durations of build-amd64, secret-grep and smoke,
  the artifact size, and the upload and download times. The image handoff (~:488-540) is
  `docker save | zstd`, uploaded as an artifact, then downloaded and loaded by secret-grep, smoke
  and publish.
- **Disk.** Add a disk-free step (remove the preinstalled toolchains) to build-amd64 and each image
  consumer, asserting ≥ 20 GB free with `df`.
- **Timeouts.** This story cannot measure the new image before it exists, so:
  - set initial generous values (build-amd64 90 min; consumers 45 min; **publish ≥ 90 min**, since
    it rebuilds both architectures);
  - publish gets its **own** disk-free step (≥ 20 GB), since it builds rather than only loading;
  - after the PR's first green run, set each to `ceil(1.5 × measured)`, pinned;
  - record the measured values beside them.
- **Cache.**
  - build-amd64 writes `type=gha,mode=min` on main (~:441); publish writes
    `type=gha,mode=max,scope=publish` (~:963).
  - Decision rule: if the projected cache, **summed across both scopes** (build-amd64's and
    `scope=publish`, which holds both architectures at `mode=max`), exceeds 80% of the 10 GB GHA
    limit, drop build-amd64's `cache-to` for the dependency layer or move publish to `mode=min`,
    and record the choice.
- **Publish.** Confirm the diff_ids layer-identity gate still matches with the large layer.

**Acceptance Criteria:**
- [ ] The baseline figures are recorded in Implementation Notes and CI_CD.md.
- [ ] Disk-free steps asserting ≥ 20 GB exist on build-amd64, the consumers and publish. Initial
      `timeout-minutes` values are set (publish ≥ 90), and the cache decision covering both scopes
      is recorded, all pinned in `tests/test_ci_workflow.py`.
- [ ] The post-PR-run timeout adjustment procedure is documented in CI_CD.md. The measured values
      are filled in at PR time.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

### US-006: No-GPU failover smokes in CI

**Priority:** P1

**Description:** As a maintainer, I want CI to prove both failover policies on a GPU-less runner on
every PR, so that the install-time contract cannot regress unnoticed.

**Independent Test:** The smoke job runs the candidate three ways:
1. default;
2. `cuda` + `cpu`, which asserts the requested device, a null active device and the probe token;
3. `cuda` + `refuse`, which asserts a non-zero exit and the fixed message.

All are pinned in `tests/test_ci_workflow.py`.

**Implementation Hints:**
- **`contract_smoke.py`:** add `--expect-requested-device` and `--expect-device` (accepts `null`),
  and update `tests/test_contract_smoke.py`. Reuse the smoke job's container naming, cleanup and
  health-wait.
- **Run 2:** `FORAGE_DEVICE=cuda`, no weights, no GPU. Expect `promptguard_requested_device: "cuda"`,
  `promptguard_device: null` and `promptguard_unavailable`, plus the log token
  `promptguard_device_probe result=unavailable`.
- **Run 3:** `FORAGE_DEVICE=cuda` with `FORAGE_DEVICE_FALLBACK=refuse`. `docker wait` returns a
  non-zero exit within the smoke timeout, and the logs contain the fixed `DeviceConfigurationError`
  message, with no traceback that includes values.

**Acceptance Criteria:**
- [ ] `contract_smoke.py` supports the two device expectations (tests).
- [ ] The smoke job runs runs 2 and 3 with the stated assertions, pinned in `tests/test_ci_workflow.py`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-007: The `compose/gpu.yml` overlay and the GPU install guide

**Priority:** P2

**Description:** As an operator, I want a one-line way to enable one GPU in Compose and a clear
install guide, so that installing on a GPU host uses the same tag and a documented switch.

**Independent Test:** Rendering the overlay with each base fragment
(`docker compose -f compose/minimal.yml -f compose/gpu.yml config`, and the same with `full.yml`)
shows a single-GPU device request, `FORAGE_DEVICE: cuda` and the overlay's memory default. Ports and
binding are unchanged, and the base fragments are byte-unchanged.

**Implementation Hints:**
- **`compose/gpu.yml`:**
  - `deploy.resources.reservations.devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]`
    (or the `gpus` equivalent the pinned Compose spec renders);
  - `environment: { FORAGE_DEVICE: cuda, FORAGE_DEVICE_FALLBACK: "${FORAGE_DEVICE_FALLBACK:-cpu}" }`,
    so the operator picks the failover policy from `.env` without editing the overlay;
  - `mem_limit: ${FORAGE_MEM_LIMIT:-3072m}`. This is **provisional**: the CUDA context and libraries
    add host RSS. Spec 5 measures it on thelab and adjusts the default.
  - No image pin and no port change.
  - Source: https://github.com/compose-spec/compose-spec/blob/main/05-services.md.
- **Tests** (`tests/test_compose_fragments.py`). Leave `_FRAGMENT_PATHS` (~:69) and the base tests
  unmodified. Overlay tests:
  - it parses;
  - it sets only the device request, `FORAGE_DEVICE`, `FORAGE_DEVICE_FALLBACK` and `mem_limit`;
  - `FORAGE_DEVICE_FALLBACK` renders `cpu` by default and `refuse` when the variable is set;
  - it pins no image;
  - it merges onto both bases without changing ports or the `127.0.0.1` binding.
- **Docs:**
  - **`docs/configuration.md` "Installing on a GPU host":**
    - driver ≥ 580 (cu130) and the NVIDIA Container Toolkit;
    - the overlay command and `FORAGE_DEVICE_FALLBACK`;
    - the `/health` state table (spec 2);
    - GPU memory is not reserved, and how to coexist with Ollama;
    - the GPU is a shared, non-isolated resource: no multi-tenant hosts, keep the loopback binding;
    - a missing Toolkit fails at `compose up`, not as a degraded boot;
    - Compose ≥ 2.30;
    - the provisional memory default.
  - **`README.md`:** the overlay command.
  - **`kit_tools/docs/DEPLOYMENT.md`:** the GPU steps.

**Acceptance Criteria:**
- [ ] `compose/gpu.yml` requests one GPU and sets `FORAGE_DEVICE: cuda`, a
      `FORAGE_DEVICE_FALLBACK` defaulting to `cpu`, and the provisional `mem_limit` default. Merged with each base fragment, ports and binding are unchanged, and the
      base fragments are byte-unchanged (tests).
- [ ] The `docs/configuration.md` GPU section covers each listed topic. README shows the overlay
      command, and DEPLOYMENT lists the steps.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

## Edge Cases

- **`--extra cuda` on macOS:** PyPI torch. Documented. (US-001)
- **A bare `uv sync`:** no torch, so the session fails with the hint. (US-001)
- **An unsupported architecture:** the build fails. (US-004)
- **A driver older than 580, or no Toolkit:** fallback, or a `compose up` error. Documented.
  (US-007)
- **Runner disk:** the `df` assertion fails first, with a clear message. (US-005)

## Out of Scope

- A separate repo or tag, GPU on arm64, CUDA base images, TensorRT, ONNX Runtime and multi-GPU.

## Assumptions

- The uv layout behaves as measured (0.9.28). CI's `uv lock --check` catches version drift.
- GitHub-hosted runners have about 14 GB free before cleanup; cleanup reclaims enough for ≥ 20 GB.

## Technical Considerations

- **Invariant 2:** architecture selection happens only inside `RUN`.
- **No hashed source changes** in this spec.
- CPU-only amd64 users pull a few GB more; this is accepted under the single-image ruling, and
  documented.

## Related Documentation

- `Dockerfile`, `.github/workflows/ci.yml`, [CI_CD.md](../docs/CI_CD.md),
  [DECISIONS.md](../arch/DECISIONS.md) :522, `compose/`

## Implementation Notes

### US-001 (landed by the supervisor, 2026-10-09)

- **Commit:** `bddc120`, implemented in attempt 1. Its verifier session, and attempts 2 and 3,
  were killed by session timeouts while the host was asleep overnight (`pmset` shows Deep Idle
  sleep from ~19:14 PDT). It was not a defect in the change. The supervisor verified the commit
  independently in a separate worktree with its own environment, then fast-forwarded the epic
  branch to it.
- **Lock:** `uv lock --check` passes (uv 0.9.28, 98 packages).
- **Install-set proof:** the pre-change `uv export --frozen --no-hashes --extra dev
  --format requirements-txt --no-emit-project` (from `6e6fb72`) and the post-change export with
  `--extra dev --extra cpu` were stripped of comments and blank lines, sorted, and compared with
  `diff`. They are **identical** (77 lines each).
- **Per-architecture torch** (`uv export --frozen --no-hashes --extra cuda`):
  `torch==2.14.0+cu130 ; platform_machine == 'x86_64' and sys_platform == 'linux'`,
  `torch==2.14.0+cpu ; platform_machine != 'x86_64' and sys_platform == 'linux'`,
  `torch==2.14.0 ; sys_platform != 'linux'`.
- **Payload scope:** `--extra dev --extra cpu` exports 0 lines matching
  `^(nvidia-|cuda-|triton==)`. `--extra cuda` exports 19, every one under a
  `platform_machine == 'x86_64' and sys_platform == 'linux'` marker.
- **Sync-site grep:** `git grep -nE 'uv sync'` (excluding spec archives, specs, `SESSION_LOG.md`
  and `bootstrap-notes.md`), filtered for lines without `--extra cpu|cuda`. Every CI sync site
  (`ci.yml` lines 152, 265, 302, 634 and 1466) reads `uv sync --extra dev --extra cpu --locked`.
  The remaining hits are:
  - the Dockerfile's `uv sync --locked --no-dev`, which installs no torch until **US-004**
    selects the extra per architecture;
  - prose in `kit_tools/arch/*`, `CI_CD.md`, `GOTCHAS.md` and `LOCAL_DEV.md`, left for
    **US-003**'s claims sweep.
- **Gates:** `ruff check`, `ruff format --check` and `pyright` (0 errors) are clean. The full suite
  gives 5664 passed and 6 skipped (5670 collected) in 377 s.

### US-003 (attempt 2, 2026-10-09)

- **Corrected sites:** the `Dockerfile` header (point 2); `INFRA_ARCH.md` dependency-install
  row, size row, sizing paragraph and disk row (per-arch: arm64 ~348 MB recorded with CPU torch,
  amd64 an estimated ~4 GB with CUDA torch, to be measured by US-004/US-005); `CODE_ARCH.md`
  Dockerfile and `uv.lock` tree lines; `LOCAL_DEV.md` "Where torch comes from" (rewrapped) and
  the Troubleshooting entry; the `GOTCHAS.md` lock entry, rewritten around the scope checker,
  including its false "the image never reads the lock" claim; `CI_CD.md` lint step 3 and its
  red-job row; and, outside the listed sites but making the same claim, `DEPLOYMENT.md`'s image
  row, `SECURITY.md`'s dependencies paragraph and PR checklist line, `SERVICE_MAP.md`'s runtime
  row, `TESTING_GUIDE.md`'s two lock descriptions, `docs/configuration.md`'s GPU bullet and the
  `kit_tools/worktree.yaml` `path_links` comment (now naming the three torch sources).
- **`docs/releases.md`:** checked, and it has **no image-size statement**. Its MB/GiB figures are
  memory and weights sizes, plus one `aarch64` torch wheel timing that is still accurate, so
  nothing there changed.
- **DECISIONS:** `kit_tools/arch/DECISIONS.md` gains the 2026-10-09 entry (one image, cu130 on
  amd64, three torch sources). The 2026-09-07 torch-CPU-index entry is marked
  `Superseded by 2026-10-09`, and its body is kept as history.
- **Grep gate:** `git grep -nE 'CPU-only|pytorch-cpu|no nvidia|nvidia-' -- ':!kit_tools/specs/archive'`.
  Every surviving match is expected. Lines per file, with the reason:
  - `uv.lock` (43): the `cuda` extra's payload packages. This is legitimate since US-001, and the
    checker scopes it.
  - `pyproject.toml` (5): the `pytorch-cpu` index name, its three source rows, and the source
    comment ("macOS/Windows wheels are CPU-only on PyPI").
  - `scripts/check_lock_cuda_scope.py` (3) and `scripts/cuda_payload_allowlist.txt` (15): the
    checker, its payload regex and the allowlist names.
  - `tests/test_dependency_lock.py` (18): the checker's planted-violation tests and source pins.
  - `tests/test_ci_workflow.py` (3): the CI regex pins and the assertion message "hermetic and
    CPU-only" (the test suite is CPU-only, not the image).
  - `.github/workflows/ci.yml` (4): the lint step's installed-package grep and echo, plus the
    `test` job comment "hermetic and CPU-only" (about the suite).
  - `kit_tools/arch/DECISIONS.md` (10): the superseded 2026-09-07 entry (4, kept as history) and
    the new 2026-10-09 entry (6).
  - `kit_tools/arch/INFRA_ARCH.md` (1), `kit_tools/docs/CI_CD.md` (2), `kit_tools/docs/GOTCHAS.md` (2),
    `kit_tools/docs/LOCAL_DEV.md` (6): corrected sentences that now describe the three sources,
    the `cpu`-profile export check and the cpu-environment troubleshooting entry.
  - `kit_tools/worktree.yaml` (2): the corrected `path_links` comment naming the sources.
  - Specs: `epic-forage-inference-backends.md` (2, the CPU-only *wheel* and *host*),
    `feature-gpu-validation.md` (2, `nvidia-smi`), and this spec itself (23 after these notes; it defines the work and records this list).
  - `kit_tools/SESSION_LOG.md` does not match the pattern. Its "CPU-pinned" lines are history.
- **Left for later stories:** the `ci.yml` build-job comment "~200 MB of CPU torch" (budgets:
  US-005) does not match the pattern. The Dockerfile's `uv sync --locked --no-dev` still selects
  no extra, so it installs no torch until **US-004**. The header now describes the image US-004
  delivers.
- No hashed source moved, so there is no rotation.

### US-004 (attempt 1, 2026-10-09)

- **Dockerfile.** The sync RUN maps `dpkg --print-architecture` amd64 -> `cuda`, arm64 -> `cpu`,
  anything else exits 1 ("unsupported architecture"), then `uv sync --locked --no-dev
  --no-install-project --extra "${extra}"`. No ARG. The content check is
  `scripts/image_content_check.py` (torch suffix `+cu130`/`+cpu` per arch; installed
  `nvidia-*`/`cuda-*`/`triton` distributions, PEP 503-normalised, equal the allowlist on amd64
  and are empty on arm64), COPYed with `scripts/cuda_payload_allowlist.txt` to
  `/tmp/image-check/`, run, and removed in one RUN. `.dockerignore` re-includes those two files
  (`scripts/` stays excluded). `tests/test_vendor_weights.py`'s "no scripts in Dockerfile" guard
  now permits exactly those two names.
- **Local builds (Apple Silicon, docker).** Native arm64: `arm64 ok, torch 2.14.0+cpu`.
  `--platform linux/amd64` (emulated): `amd64 ok, torch 2.14.0+cu130`. So both legs of the check
  passed for real, not just statically.
- **Image venv path:** `/app/.venv/bin/python` (`UV_PROJECT_ENVIRONMENT=/app/.venv`).
- **Size (amd64, measured locally on the emulated build, gzip -6 per layer):** gzip sum
  3,153,366,807 B (~3.15 GB, under the 5 GB gate); uncompressed layers 3,166,057,432 B; largest
  layer 3,087,698,222 B (the dependency layer). CUDA libs barely compress. CI's own numbers land
  in the job summary of the `smoke` job and should replace these on the first run.
- **CPU parity.** CI steps added to `smoke` (parity + size). Locally, the emulated amd64 image
  and this Mac's arm64 `+cpu` venv printed identical `float.hex()` lines (7 scores), but that is
  *not* the CI comparison (x86 image vs x86 `+cpu` env); the real verdict is the first CI run.
  If CI diffs, record the max difference and stop for the owner.
- **Split recorded** in CI_CD.md: arm64 build-time checks run in the publish multi-arch build;
  PR-time arm64 guarantee is the checker's rule 2.

### US-004 (attempt 2, 2026-10-09) — CI verdicts

- Attempt 1's commit was recovered unchanged and run in CI through a throwaway draft PR
  (#46, branch `ci-probe/inference-backends-us-004`, closed afterwards). Run 37962323278:
  every job green (`lint`, `typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke`).
- **Build-time check (x86 runner):** `image content check: amd64 ok, torch 2.14.0+cu130`.
- **CPU parity:** `==`. `diff image.txt ci.txt` empty, `CPU parity: 7 scores identical`
  (amd64 candidate with `CUDA_VISIBLE_DEVICES=` vs CI's `+cpu` env). No owner decision needed;
  the epic's "byte-identical" wording stands for amd64 CPU installs.
- **Size (CI, `docker save`, gzip -6 per layer):** gzip sum **3,143,866,537 B (~3.14 GB)**, under
  the 5 GB gate; uncompressed **5,961,397,760 B (~5.96 GB)**; largest layer **5,751,717,376 B**
  (the dependency sync). Attempt 1's local "uncompressed" figure (3.17 GB) was wrong, because the
  local containerd store saves already-compressed layer blobs. The CI figures supersede it, and
  CI_CD.md now carries them in place of the "~4 GB estimated" text.

## Refinement Notes

### Research Findings

**Decision:** torch only in extras, with three sources.
**Rationale:** Validation measured that without the third source, arm64 `--extra cuda` pulls PyPI's
CUDA torch.

**Decision:** Change the install commands and guards in the same story as the re-lock.
**Rationale:** Validation measured that `uv sync --extra dev` removes torch after the re-lock, and
that the old grep goes red.

**Decision:** Provenance by registry URL and package class, with marker evaluation via
`packaging.markers`.
**Rationale:** Measured lock layout: the payload registry is PyPI, and markers are uv-internal.

**Decision:** Install-set equality, not a timing bar.
**Rationale:** Cold `uv sync` timing noise exceeds 10% (round 2).

### Scope Adjustments

- Round 2 brought the spec to seven stories:
  - install commands moved into US-001;
  - US-003 narrowed to docs;
  - CI budgets (US-005) separated from the failover smokes (US-006);
  - eight checker violations;
  - the in-image payload scan;
  - the gzip-sum size gate;
  - `==` CPU parity with an owner fallback;
  - the provisional overlay memory default.

## Clarifications

### Session 2026-10-08
- **Q:** A separate repo or tag suffix? **A:** Neither. One image.
- **Q:** Which CUDA build? **A:** cu130.
