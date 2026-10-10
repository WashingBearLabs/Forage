<!-- Template Version: 2.5.0 -->
---
feature: gpu-validation
status: active
session_ready: true
depends_on: [gpu-parity-tool]
vision_ref: "T3 — run the classifier where the hardware is: CPU and GPU from one image"
type: epic-child
size: M
epic: forage-inference-backends
epic_seq: 5
epic_final: true
created: 2026-10-08
updated: 2026-10-08
---

# Feature Spec: GPU Validation — Owner Runs on thelab, Sizing, v1.4.0 Prep

## Overview

**Execute this spec in supervised mode, not by the autonomous orchestrator.**
- US-001 to US-003 are **owner gates** on a production host (thelab: Threadripper 2970WX, RTX 4070 Ti
  12 GB shared with Ollama at ~7.4 GB, driver 590, production Poppy running).
- Claude may run each gate through `ssh thelab-claude` only with explicit owner approval, which
  covers that story's listed sequence.
- A gate is complete only when its results are recorded in this spec's Implementation Notes and in
  `docs/bootstrap-notes.md`.
- US-004 and US-005 are ordinary stories that use only recorded figures, never invented ones.

**Shared production-safety rules for US-001 to US-003:**
- **Quiet window.** Run during an owner-chosen window when Poppy is idle.
- **VRAM floor.** Before the run, record:
  - Ollama's loaded models, and the VRAM its largest model needs;
  - free VRAM.

  The floor is the free VRAM the candidate must leave untouched for Ollama:
  `floor = Ollama's largest model need + candidate peak + 512 MiB margin`.
  - Until US-001's first run measures the candidate peak, use the estimate **2.5 GiB** (86M fp32
    weights plus batch-16 activations and the CUDA context).
  - Abort if free VRAM is below the floor before starting.
- **Mid-run guard.** Poll `nvidia-smi` free VRAM and the production containers' `StartedAt` every
  5 s. Kill the candidate or process if free VRAM drops below the floor or any production container
  restarts.
- **Isolation.** As the `claude` user.
  - Host processes (`docker build`, `uv run` parity runs) run under `nice -n 10`.
  - `nice` does not reach a container's processes, so containers are constrained with
    `--cpus 8 --cpu-shares 256` instead.
- **Production attestation.**
  - Committed: a SHA-256 over the sorted list of production `(ID, StartedAt)` pairs, plus the
    count, before and after. The two must be equal.
  - Kept only in the session scratchpad: the raw list. The repo is public.
- **Commit only:** host class, driver, image ID and commit, figures, run counts and timestamps.
  Never `docker inspect`, env or compose-config output.
- **Scratch area** (`~/forage-validation`, mode 0700, owned by `claude`):
  - a checkout of the epic head (commit recorded, equal to the audited branch head);
  - a uv env (`uv sync --extra dev --extra cuda`);
  - a scratch HF cache: weights copied, mounted read-only into containers, and never production's
    cache.
    - The container runs as the image's non-root user, not `claude`. Read its uid first
      (`docker run --rm --entrypoint id <image>`).
    - Make the cache subtree readable to that uid: directories 0755, files 0644. The 0700 parent
      does not block a bind mount.
    - Confirm with a read-only `ls` from inside a throwaway container before the first gate.
  - If the weights are missing, the owner acquires them into the scratch cache, with the token read
    via `read -rs`, never on argv.
- **Image.** Built on thelab from the checkout (`nice -n 10 docker build`). Record the image ID and
  commit.
- **Teardown:**
  - remove containers;
  - confirm the candidate's process is absent from `nvidia-smi --query-compute-apps`;
  - confirm used VRAM is within 200 MiB of the pre-run baseline, or the difference is attributed to
    Ollama;
  - delete the checkout, venv and scratch cache unless the owner keeps them;
  - record the deletions.

## Goals

- **Latency** (86M, real weights, RTX 4070 Ti):
  - the **marginal per-window cost**,
    `m50 = (warm_p50_ms_budget − warm_p50_ms_1w) / (budget_windows − 1)`, is ≤ 25 ms;
  - the **p95 per-window cost**, `p = (warm_p95_ms_budget − warm_p50_ms_1w) / (budget_windows − 1)`,
    is recorded (US-004's formula uses it);
  - the **projected 64-window p95**, `warm_p95_ms_budget + (64 − budget_windows) × p`, is
    < 2,000 ms, over at least 10 runs. The bench's budget document yields about 55 windows, not 64,
    so the figure is projected rather than measured;
  - `warm_p50_ms_1w` (end-to-end) and VRAM peak are recorded.
- **Parity:**
  - 22M and 86M at batch 16: zero verdict changes and a valid run (no OOM, no failover);
  - a batch-1 run recorded as a pair with the batch-16 run;
  - a `--device cpu` control on thelab recorded, expected to show zero drift.
- **Failover on real hardware:**
  - a hidden GPU with fallback `cpu` reports `promptguard_device: "cpu"` and
    `promptguard_device_failover`;
  - `refuse` exits non-zero.
- **Production unaffected:** the attestation hash is equal before and after every gate.

## User Stories

### US-001: Owner gate — GPU smoke and latency

**Priority:** P1

**Description:** As the owner, I want the candidate image proven on the real GPU with real weights,
with latency and memory measured, without disturbing production, so that the GPU path ships with
evidence.

**Independent Test:** The recorded run shows:
- healthy `/health` with `promptguard_device: "cuda"`;
- marginal per-window ≤ 25 ms;
- projected 64-window p95 < 2 s;
- VRAM and host RSS recorded;
- an equal attestation hash.

**Implementation Hints:**
- **Benchmark procedure:** `docs/weights.md` "Benchmarking the classifier" (~:52-80).
- **Container:**
  - `--name forage-gpu-candidate`, `--restart no`, one GPU;
  - `-p 127.0.0.1:18020:8020`;
  - env: `FORAGE_DEVICE=cuda`, the model id, and `HF_HOME` set to the read-only scratch cache.
    Minimal in **env only**: no `VALKEY_URL`, no `HF_TOKEN`;
  - mounts:
    - the scratch cache, read-only;
    - **`bench/config.yaml` at `/app/config.yaml`, read-only** (it enables `/extract` and sets the
      64-chunk budget);
  - the overlay's memory default.
- **Bench:**
  - copy the tokenizer snapshot out of the scratch cache for `--tokenizer-dir`;
  - run `nice -n 10 uv run python scripts/bench_promptguard.py --base-url http://127.0.0.1:18020 --container forage-gpu-candidate --tokenizer-dir … --model-id … --input 1w|budget --runs 20 --json …`;
  - use **a fresh container per `--input`** (`1w`, then `budget`), per the tool's design;
  - **sanity check** before using the figures: the `1w` JSON reports 1 window and the `budget` JSON
    reports `budget_windows` > 1 (expected about 55). Otherwise the run is void.
- **Record:**
  - cold load time;
  - the warm 1w and budget p50/p95;
  - `budget_windows`;
  - the computed `m50`, `p` and projected 64-window p95;
  - VRAM peak, and host RSS peak (for the overlay's `mem_limit`);
  - GPU utilisation during the run.
  - The 22M runs the same way via `FORAGE_MODEL_ID`. Its figures are recorded, with no bar.
- **If a bar is missed:** record the figures; the owner rules, and the release waits.

**Acceptance Criteria:**
- [x] The safety pre-flight (floor, quiet window, attestation hash), the mid-run guard and teardown
      are recorded, and the attestation hash is equal before and after.
- [x] `/health` was healthy with `promptguard_device: "cuda"` for the 86M.
- [x] `m50` ≤ 25 ms, and the projected 64-window p95 < 2,000 ms over 10+ runs, with the window-count
      sanity check passing. 1w p50/p95, VRAM peak and host RSS peak are recorded. The 22M figures are
      recorded.
- [x] The host RSS figure is compared with the overlay's provisional 3,072 MiB default, and the
      default is confirmed or a change is recorded.

### US-002: Owner gate — parity runs

**Priority:** P1

**Description:** As the owner, I want the parity tool run on thelab for both models, with a CPU
control, so that the release has proof of zero verdict changes on the GPU.

**Independent Test:** The recorded parity JSONs show:
- 22M and 86M at batch 16 on cuda: exit 0;
- the batch-1 pair recorded;
- the `--device cpu` control with zero drift.

**Implementation Hints:**
- From the scratch checkout:
  `uv run python -m scripts.corpus.parity --model-id <id> --device cuda --batch-size 16 --json …`
  under `nice -n 10`, with the mid-run guard active.
- Then `--batch-size 1`, then `--device cpu`, for both models.
- **If the CPU control shows drift,** the cuda drift is not purely GPU-attributable. Record it, and
  the owner rules.
- **On a verdict change at batch 16 with none at batch 1:** the cause is padding, and the pre-agreed
  option is to ship `promptguard_cuda_batch_size: 1`, then re-run.
- **On a change at both:** the cause is the device. The owner rules.
- Record the results by id only in `docs/bootstrap-notes.md`.

**Acceptance Criteria:**
- [x] 22M and 86M on cuda at batch 16 give exit 0 (zero verdict changes, valid run). Max and p99
      window drift and the long-text drift are recorded.
- [x] The batch-1 pair and the CPU control are recorded, and the control's drift is stated.
- [x] The checkout commit equals the audited head, and the attestation hash is equal before and
      after.

### US-003: Owner gate — failover proof

**Priority:** P2

**Description:** As the owner, I want both failover policies shown on the real host, so that the
documented behaviour is proven where it matters.

**Independent Test:**
- With `CUDA_VISIBLE_DEVICES=` and fallback `cpu`, `/health` shows `promptguard_device: "cpu"`,
  `promptguard_requested_device: "cuda"` and `promptguard_device_failover` after load.
- With `refuse`, the container exits non-zero with the fixed message.

**Implementation Hints:**
- Same container rules as US-001, with the scratch cache mounted read-only.
- Record the `/health` fields, the exit code and the one-line message.

**Acceptance Criteria:**
- [x] The fallback `cpu` case shows the three fields after load, and the `refuse` case exits
      non-zero with the fixed message (both recorded).
- [x] The safety rules were followed, and the attestation hash is equal before and after.

### US-004: GPU sizing docs

**Priority:** P2

**Description:** As an operator, I want measured GPU sizing in the docs, so that I can set the
budget and wait for a GPU host.

**Independent Test:**
- `docs/configuration.md` "Measured per-window cost" has the GPU rows.
- A GPU envelope gives `retrieve.max_promptguard_chunks` and `promptguard_wait_seconds` values from
  the stated formula.

**Implementation Hints:**
- **Formula** (single holder), with `p` = US-001's p95 per-window cost (defined in Goals):
  - `budget = min(1024, floor(0.5 × 90 / p))`, so a worst-case page uses at most half of the
    default 90 s wait;
  - `wait` stays 90.
  - Show the arithmetic.
- **Sections to edit:** `docs/configuration.md` "Measured per-window cost" and the sizing table;
  `docs/weights.md` "Benchmarking the classifier", which should mention device-aware output.
- The **defaults stay** 64 and 90. The GPU values are a documented operator setting.
- If US-001 is not recorded, this story is blocked.

**Acceptance Criteria:**
- [ ] The GPU rows (both models: marginal per-window, 1w and budget p50/p95, VRAM) are present.
- [ ] The envelope shows the formula and the resulting budget. The defaults are unchanged.

### US-005: v1.4.0 release prep

**Priority:** P2

**Description:** As the owner, I want the v1.4.0 release entry, the final 1.5.0 contract entry and
the pins prepared, so that tagging is the only step left.

**Independent Test:**
- `docs/releases.md` has a `### v1.4.0` NOT YET PUBLISHED entry. Its anchor equals
  `contract/openapi.yaml.sha256`, and its revisions equal `derive_sanitizer_revision()` for cpu
  (default and shipped) and for cuda.
- Pins name 1.4.0.
- The 1.5.0 bullet is final.

**Implementation Hints:**
- **Precedent:** the archived `kit_tools/specs/archive/feature-release-1-3-0.md` US-005, commit
  `33a6431`. Classify every hit of `git grep -n '1\.3\.0'` as rewritten or history, and record the
  table.
- **Order:**
  1. Finalise the 1.5.0 bullet in `pipeline/contract.py`, reconciled against
     `git diff 35a393c..HEAD -- pipeline/contract.py contract/openapi.yaml` (35a393c is the v1.3.0
     merge).
  2. Run `uv run python -m scripts.export_contract`. The docstring is not in the document, so
     record the result.
  3. Derive the revisions.
  4. Record the rotation per the epic procedure.
  5. Write the entry.
- **Entry items:**
  - one image, and the arch split;
  - `FORAGE_DEVICE` / `FORAGE_DEVICE_FALLBACK`;
  - GPU batching;
  - OOM handling, and the refuse/fail-open note;
  - the `/health` fields and reasons;
  - the four metrics;
  - the `device@cuda` input and the active-device fingerprint;
  - `compose/gpu.yml` and its memory default;
  - the amd64 size;
  - the driver floor of 580;
  - parity results;
  - latency figures.
  - Use `NOT YET MEASURED` for anything not yet recorded.
- **Owner sequence:**
  1. US-001 to US-003 recorded;
  2. merge;
  3. tag `v1.4.0`;
  4. publish;
  5. anonymous pull;
  6. GPU smoke of the published image on thelab;
  7. fill the placeholders.

**Acceptance Criteria:**
- [ ] The 1.5.0 bullet is final and reconciled. The `export_contract` result and the rotation are
      recorded.
- [ ] The v1.4.0 NOT YET PUBLISHED entry contains each item, from recorded figures or
      `NOT YET MEASURED`, with the anchor and the cpu/cuda revisions verified by a recorded command.
- [ ] Pins name 1.4.0, the classified table is recorded, and `tests/test_compose_fragments.py`
      passes.
- [ ] Implementation Notes end with the owner sequence. No tag is pushed.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

## Edge Cases

- **VRAM below the floor, or Ollama busy:** abort, record it, and reschedule in a quiet window.
  Never stop production without separate approval. (US-001 to US-003)
- **A missed bar or a verdict change:** record it, apply the pre-agreed option, or the owner rules.
  The release waits. (US-001, US-002)
- **Weights missing:** the owner acquires them into the scratch cache. (all gates)

## Out of Scope

- CI GPU runners, GPU cassettes, changing the defaults for GPU hosts, and concurrent-load GPU
  sizing.

## Assumptions

- The owner approves each gate and chooses the quiet window.
- `scripts/bench_promptguard.py` works against a cuda container unchanged, apart from spec 2's device
  fields.

## Technical Considerations

- **Secret handling:** tokens never on argv and never printed. **Corpus rule:** cite ids only.
- **Rotation:** `contract.py` (US-005).

## Related Documentation

- [docs/weights.md](../../docs/weights.md), [docs/corpus.md](../../docs/corpus.md),
  [docs/releases.md](../../docs/releases.md)

## Implementation Notes

### US-001 — GPU smoke and latency (owner gate, run 2026-10-09)

- **Approval:** the owner approved the US-001 sequence in-session on 2026-10-09. The run went
  through `ssh thelab-claude` as the `claude` user. The window ran from 21:02 to 23:09 UTC, and
  Poppy was idle (load average 0.7).
- **Host:** RTX 4070 Ti (12,282 MiB), driver 590.48.01, Threadripper 2970WX (48 threads), 31 GiB
  RAM.
- **Image:** `forage:gpu-cand-2c9b28f`, image ID `8e2394d06cfa`, built on thelab from `main` at
  `2c9b28f` (PR #47's merge). The build-time check printed `image content check: amd64 ok, torch
  2.14.0+cu130`; inside, torch is `2.14.0+cu130` (CUDA 13.0) and the runtime uid is 999 (`poppy`).
- **First build was unusable, and why.** The scratch checkout was cloned under `umask 077`. Its files
  were owner-only, `COPY` carries modes into the image, and the non-root user could not execute
  `/app/docker-entrypoint.sh` (exit 126). Restoring git's modes (`chmod -R u=rwX,go=rX`, then 0755
  for every mode-100755 file) and rebuilding fixed it. CI builds from a default-umask checkout and
  is unaffected. The image's runtime permissions depend on the builder's umask, which is worth a
  Dockerfile hardening note.
- **Weights:** the owner fetched both pinned revisions with their own read token
  (`~/forage-validation/fetch-weights.sh`: hidden prompt, token via the environment only). They went
  into a scratch cache, never production's, with every file's SHA-256 checked against
  `weights_manifest.json`. The cache is mounted read-only at `/app/model-cache` with
  `HF_HUB_OFFLINE=1`.
- **Container:** `--restart no --gpus device=0 --cpus 8 --cpu-shares 256 --memory 3072m`,
  `FORAGE_DEVICE=cuda`, the model id, `-p 127.0.0.1:18020:8020`, and `bench/config.yaml` at
  `/app/config.yaml:ro`. No `VALKEY_URL` and no `HF_TOKEN`. There was a fresh container per input.
- **Operative VRAM floor (owner ruling, 2026-10-09).** Read literally, the floor formula cannot be
  met while Ollama's only model (`qwen3-embedding`, 5,954 MiB) is already resident, because it counts
  that model twice. The operative rule:
  - start only if free VRAM ≥ the candidate peak + 512 MiB;
  - stop the candidate if free VRAM drops below 512 MiB or any production container restarts.

  The rule started from the 2.5 GiB estimate; the **measured 86M peak is 2,879 MiB**, so the floor
  for later gates is **3,391 MiB free**. Free VRAM before the run was 4,501 MiB.
- **Mid-run guard:** polled free VRAM and a hash of the production containers' start times every
  5 s. **0 alerts.** Minimum free VRAM was 1,622 MiB, during the 86M budget run.

| | 86M | 22M |
|---|---|---|
| `/health` | healthy, `promptguard_device: "cuda"`, `requested: "cuda"`, contract 1.5.0 | same |
| `sanitizer_revision` (cuda) | `280511c1…` | — |
| cold load (start → loaded) | 19.8 s / 17.9 s | 16.3 s / 16.5 s |
| `budget_windows` (sanity: > 1) | 55 | 40 |
| warm p50 / p95, 1w (20 runs) | 61.3 / 83.2 ms | 61.2 / 79.0 ms |
| warm p50 / p95, budget (20 runs) | 926.9 / 951.3 ms | 404.4 / 420.0 ms |
| `m50` = (p50 budget − p50 1w) / (windows − 1) | **16.0 ms** (bar ≤ 25 ms: met) | 8.8 ms |
| `p` = (p95 budget − p50 1w) / (windows − 1) | **16.5 ms** | 9.2 ms |
| projected 64-window p95 = p95 budget + (64 − windows) × p | **1,100 ms** (bar < 2,000 ms: met) | 641 ms |
| VRAM peak over the 7,376 MiB baseline | **2,879 MiB** (budget) | 1,311 MiB |
| host: container RSS / cgroup peak | 1,155 / 2,185 MiB | 1,032 / 1,294 MiB |

- `p50` is the 1-window warm median. `cold load` is wall time from `docker run` to
  `promptguard_loaded`; the two values per model are the 1w and budget containers.
- **Overlay `mem_limit`:** the highest cgroup reading, 2,185 MiB, is 71% of the provisional
  3,072 MiB, leaving about 0.9 GiB of headroom. **The default is confirmed** and stays `3072m`.
- **GPU utilisation** during the runs was not sampled (the guard recorded free VRAM only). It is
  recorded as not measured.
- **Teardown:** the candidate was removed and is absent from `nvidia-smi --query-compute-apps`.
  Used VRAM is back to exactly 7,376 MiB, the baseline. The scratch checkout, venv and cache are
  **kept** for US-002 and US-003.
- **Production attestation:** before (21:02:46 UTC) and after (23:08:45 UTC), 23 containers, SHA-256
  `5f7ae38680f1e40269fb5fc885c1f16a59eca1dafa8a1d5bcaf1b1ba4680cc72`. **Equal.**

### US-003 — failover proof (owner gate, run 2026-10-10)

- **Approval:** the owner approved US-002 and US-003 together in-session on 2026-10-10. The window
  ran from 18:51 to 18:52 UTC with the host idle (load 0.3). The image is the same as US-001
  (`8e2394d06cfa`, `main` at `2c9b28f`), with the read-only scratch cache and
  `--gpus device=0 -e CUDA_VISIBLE_DEVICES=`, so the toolkit was present and the GPU hidden.
- **Fallback `cpu`:** `/health` after load gave:
  - `status: "degraded"` and `degraded_reasons: ["promptguard_device_failover"]`;
  - `promptguard_device: "cpu"` and `promptguard_requested_device: "cuda"`;
  - `promptguard_loaded: true` (86M).

  The log tokens were `promptguard_device_probe result=unavailable` and
  `promptguard_device_failover reason=unavailable`. The candidate never appeared among the GPU
  processes.
- **`refuse`:** the container exited with **code 3** before serving. The log line is the fixed
  `DeviceConfigurationError: FORAGE_DEVICE=cuda with FORAGE_DEVICE_FALLBACK=refuse requires a usable
  GPU`. The traceback that precedes it carries no value beyond that fixed message.
- **Production attestation:** before (18:51:26 UTC) and after (18:52:14 UTC), 23 containers,
  `5f7ae38680f1e40269fb5fc885c1f16a59eca1dafa8a1d5bcaf1b1ba4680cc72`. **Equal**, and identical to
  US-001's.

### US-002 — parity runs (owner gate, run 2026-10-10)

- **Setup:** the scratch checkout at `2c9b28f`, the audited head (PR #47's merge plus nothing else),
  and the venv from `uv sync --extra dev --extra cuda`. Each run was `nice -n 10` on the host with
  `HF_HOME` set to the scratch cache and `HF_HUB_OFFLINE=1`, under the mid-run guard. The guard was
  extended to also kill the parity process.
- **First attempt stopped by the guard.** `86M --device cuda --batch-size 16`, with no allocator
  setting, grew to about 4.27 GiB of VRAM within 16 s (free fell to 230 MiB). The guard killed it
  (exit 143); Ollama stayed resident and production was untouched.
  - **Cause:** the corpus feeds pages of many lengths, and PyTorch's caching allocator keeps a
    reserved block per shape, so reserved VRAM climbs with shape variety. US-001's bench repeats one
    55-window page and never showed it.
  - **Owner ruling:** retry with
    `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,garbage_collection_threshold:0.6`.
  - That run peaked at **2,834 MiB** (sampled every 1 s), and all six runs below use the setting.
  - **The setting now ships in the image** (`Dockerfile` `ENV`, pinned in
    `tests/test_dockerfile.py`). This was the operative production risk: a GPU host shared with
    another workload would otherwise see Forage's reservation creep with real traffic.
- **Results** (760 records, 1,520 results compared, configs `default` and `contiguity`, torch
  `2.14.0+cu130`). Every run: exit 0, 0 verdict changes, 0 unrecorded shas, 0 window-count
  mismatches, 0 OOM reductions, 0 failovers, effective batch equal to requested. The **25
  never-produced shas** are the known stale cassette entries (informational).

| run | max window drift | p99 window drift | long-text check (windows, max drift, crossings) | VRAM peak | wall |
|---|---|---|---|---|---|
| 86M cuda b16 | 2.80e-05 | 4.86e-06 | 35 = 35, 4.17e-06, 0 | 2,834 MiB | 90 s |
| 86M cuda b1 | 2.80e-05 | 5.38e-06 | 5 = 5, 0, 0 | 1,522 MiB | 97 s |
| 86M cpu (control) | **0** | 0 | 5 = 5, 0, 0 | — | 1,204 s |
| 22M cuda b16 | 1.25e-06 | 8.05e-07 | 35 = 35, 3.17e-08, 0 | 1,296 MiB | 76 s |
| 22M cuda b1 | 1.13e-06 | 7.30e-07 | 5 = 5, 0, 0 | 588 MiB | 84 s |
| 22M cpu (control) | **0** | 0 | 5 = 5, 0, 0 | — | 546 s |

- **Reading:**
  - The CPU controls reproduce the cassettes exactly, so the GPU drift is GPU-attributable.
  - Batch 16 and batch 1 drift alike, so padding contributes nothing measurable.
  - The 86M's single **near-threshold id is `atk-0284`** (within 0.01 of the threshold), on every
    86M run including the CPU control. It is a property of the record, not of the device.
- **Production attestation:** before (the US-003 reading, 18:51:26 UTC) and after (19:37:41 UTC),
  23 containers, `5f7ae386…`. **Equal.** VRAM is back to the 7,376 MiB baseline.
- **Scratch area:** kept until US-004 and US-005 are done (the owner deletes it, or asks for deletion,
  afterwards).

## Refinement Notes

### Research Findings

**Decision:** Owner gates live in their own supervised spec.
**Rationale:** An autonomous orchestrator must never reach a production-host gate (round 2).

**Decision:** The latency bar is the marginal per-window cost derived from the bench's budget and 1w
runs.
**Rationale:** The bench measures end-to-end `/extract`. The 15 ms basis is a bare forward pass
(round 2).

## Clarifications

### Session 2026-10-08
- **Q:** How is GPU accuracy proven? **A:** By parity against the CPU cassettes.
