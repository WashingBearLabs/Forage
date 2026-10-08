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

  The floor is (Ollama's largest model need + the candidate's measured peak, with US-001's first
  run measuring the peak). Abort if free VRAM is below it.
- **Mid-run guard.** Poll `nvidia-smi` free VRAM and the production containers' `StartedAt` every
  5 s. Kill the candidate or process if free VRAM drops below the floor or any production container
  restarts.
- **Isolation.** Run under `nice -n 10`, as the `claude` user.
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
  - If the weights are missing, the owner acquires them into the scratch cache, with the token read
    via `read -rs`, never on argv.
- **Image.** Built on thelab from the checkout (`nice docker build`). Record the image ID and commit.
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
    `(warm_p50_ms_budget − warm_p50_ms_1w) / (budget_windows − 1)`, is ≤ 25 ms;
  - `warm_p95_ms_budget` < 2,000 ms, with `budget_windows == 64`, over at least 10 runs;
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
- budget p95 < 2 s with 64 windows;
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
  - run `uv run python scripts/bench_promptguard.py --base-url http://127.0.0.1:18020 --container forage-gpu-candidate --tokenizer-dir … --model-id … --runs 20 --json …`;
  - use **a fresh container per `--input`** (`1w`, then `budget`), per the tool's design.
- **Record:**
  - cold load time;
  - the warm 1w and budget p50/p95;
  - `budget_windows`;
  - the computed marginal per-window cost;
  - VRAM peak, and host RSS peak (for the overlay's `mem_limit`);
  - GPU utilisation during the run.
  - The 22M runs the same way via `FORAGE_MODEL_ID`. Its figures are recorded, with no bar.
- **If a bar is missed:** record the figures; the owner rules, and the release waits.

**Acceptance Criteria:**
- [ ] The safety pre-flight (floor, quiet window, attestation hash), the mid-run guard and teardown
      are recorded, and the attestation hash is equal before and after.
- [ ] `/health` was healthy with `promptguard_device: "cuda"` for the 86M.
- [ ] Marginal per-window ≤ 25 ms, and `warm_p95_ms_budget` < 2,000 with `budget_windows == 64`
      over 10+ runs. 1w p50/p95, VRAM peak and host RSS peak are recorded. The 22M figures are
      recorded.
- [ ] The host RSS figure is compared with the overlay's provisional 3,072 MiB default, and the
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
  under `nice`, with the mid-run guard active.
- Then `--batch-size 1`, then `--device cpu`, for both models.
- **If the CPU control shows drift,** the cuda drift is not purely GPU-attributable. Record it, and
  the owner rules.
- **On a verdict change at batch 16 with none at batch 1:** the cause is padding, and the pre-agreed
  option is to ship `promptguard_cuda_batch_size: 1`, then re-run.
- **On a change at both:** the cause is the device. The owner rules.
- Record the results by id only in `docs/bootstrap-notes.md`.

**Acceptance Criteria:**
- [ ] 22M and 86M on cuda at batch 16 give exit 0 (zero verdict changes, valid run). Max and p99
      window drift and the long-text drift are recorded.
- [ ] The batch-1 pair and the CPU control are recorded, and the control's drift is stated.
- [ ] The checkout commit equals the audited head, and the attestation hash is equal before and
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
- [ ] The fallback `cpu` case shows the three fields after load, and the `refuse` case exits
      non-zero with the fixed message (both recorded).
- [ ] The safety rules were followed, and the attestation hash is equal before and after.

### US-004: GPU sizing docs

**Priority:** P2

**Description:** As an operator, I want measured GPU sizing in the docs, so that I can set the
budget and wait for a GPU host.

**Independent Test:**
- `docs/configuration.md` "Measured per-window cost" has the GPU rows.
- A GPU envelope gives `retrieve.max_promptguard_chunks` and `promptguard_wait_seconds` values from
  the stated formula.

**Implementation Hints:**
- **Formula** (single holder), with `p` = recorded p95 per-window cost:
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
