<!-- Template Version: 2.5.0 -->
---
feature: gpu-validation
status: active
session_ready: true
depends_on: [unified-image]
vision_ref: "T3 — run the classifier where the hardware is: CPU and GPU from one image"
type: epic-child
size: M
epic: forage-inference-backends
epic_seq: 4
epic_final: true
created: 2026-10-08
updated: 2026-10-08
---

# Feature Spec: GPU Validation — Parity Tool, Owner GPU Runs on thelab, Sizing, v1.4.0 Prep

## Overview

The suite and CI have no GPU, so the GPU path is proven on thelab:
- Threadripper 2970WX;
- RTX 4070 Ti, 12 GB, shared with Ollama (about 7.4 GB used);
- driver 590;
- **production Poppy is running there.**

**What a cassette holds.** A cassette stores no text: `scripts/corpus/replay.py` keeps only
`sha256(stage-3 text) -> {scores, windows}`. Parity therefore **re-drives the corpus** through the
real pipeline (`scripts/corpus/drivers.py` `drive_all`), the way the recorder does, but with a live
classifier on the chosen device. Each live score is compared with the recorded score by sha, and the
**real stage-3 rules** judge the verdicts.

**Owner gates (US-002 to US-004).** These need the gated weights and touch a production host. Claude
may run them through `ssh thelab-claude` only with explicit owner approval, which covers the listed
sequence for that story. They run from a **checkout of the epic head on thelab** with
`uv sync --extra dev --extra cuda`, the same lock as the image, because `.dockerignore` keeps
`scripts/` and `tests/` out of the image. The image itself is tested separately in the smoke story.

## Goals

- **Parity** for the 22M and 86M, real weights, CUDA, at the shipped batch size (16):
  - **zero stage-3 verdict changes** across all records, routes and rule configs, computed by the
    real rules;
  - window counts equal to the cassette for every sha;
  - max and p99 window drift recorded;
  - a batch-1 diagnostic pass recorded beside it.
- **Latency.** Real-weights 86M per-window latency on the RTX 4070 Ti: p50 ≤ 25 ms over at least 20
  warm runs, with p95 recorded. A 64-window page classifies in < 2 s at p95 over 10 runs. GPU
  utilisation and free VRAM are recorded during the run.
- **Failover on real hardware.** A hidden GPU gives degraded `promptguard_device_failover` with
  `promptguard_device: "cpu"` after load. `refuse` with a hidden GPU exits non-zero.
- **Production stays untouched.** Production containers' IDs and `StartedAt` are identical before
  and after every owner run.

## User Stories

### US-001: A corpus parity tool that re-drives the corpus with a live classifier

**Priority:** P1

**Description:** As the owner, I want a tool that drives the whole corpus through the pipeline with
a live classifier on a chosen device, and compares every stage-3 score and verdict with the recorded
CPU cassette, so that any backend's accuracy is proven against the recordings.

**Independent Test:** With a fake live classifier:
- returning recorded scores plus an offset, it reports drift equal to the offset;
- with an offset that pushes one boundary record across the threshold, it reports exactly that
  record's id as a verdict change and exits non-zero;
- with a window-count mismatch, it fails hard.

**Implementation Hints:**
- **Shared loader.** Extract the inline refusals and loading from `scripts/corpus/record.py` `main()`
  (~:222-244: model env set, id not allowed, not pinned, acquire and load, not loaded) into a shared
  `resolve_and_load(model_id) -> (classifier, revision) | refusal` helper, in
  `scripts/corpus/live.py`.
  - `record.py` uses it too.
  - Keep record.py's reason words and `EXIT_REFUSED`, and keep `tests/test_corpus_record.py` green.
- **New `scripts/corpus/parity.py`:**
  - `ParityClassifier`, a `ReplayClassifier` subclass mirroring `RecordingClassifier`. It calls the
    live classifier unbudgeted (`max_chunks=None`), looks the sha up in the cassette, and records
    `(live, recorded)` per sha.
  - Drive it with `drive_all` over `load_corpus()` for every rule config the cassette lists
    (`configs`).
  - Run a second drive with the plain `ReplayClassifier`.
  - **A verdict change** is any `RouteResult` outcome that differs between the live drive and the
    replay drive, for a (record id, route, config). This uses the real stage-3 rules, including
    contiguity where configured.
  - Also report window-level threshold crossings.
- **CLI:**
  - `--model-id`, required; the cassette is selected by the manifest pin, and refused on mismatch;
  - `--device cpu|cuda`, applied through spec 1's `configure_device`;
  - `--batch-size`, setting `promptguard_cuda_batch_size` on the classifier.
  - Refuse (non-zero) if a cuda run's classifier ends with `failed_over`, so a CPU run can never
    pass as cuda.
- **Output** (JSON):
  - device, torch version, precision mode and batch size;
  - records driven;
  - max and p99 window drift;
  - page-max drift;
  - verdict changes, by record id, route and config;
  - ids within 0.01 of the threshold;
  - shas present in the cassette but never produced, and the reverse.
- **Never print corpus text** (the corpus rule).
- **Exit codes:**
  - 0: clean;
  - 1: verdict change, window-count mismatch or failover;
  - 2: refusal (record.py's `EXIT_REFUSED`).
- **Tests** (`tests/test_corpus_parity.py`): fake live classifiers over the committed cassettes,
  hermetic.
- **Docs.** A `docs/corpus.md` "Backend parity" section: the owner procedure, mirroring recording
  (~:108-152).

**Acceptance Criteria:**
- [ ] `scripts/corpus/live.py` holds the shared loader. `record.py` uses it, and
      `tests/test_corpus_record.py` passes unchanged.
- [ ] `parity.py` re-drives the corpus, judges verdict changes by comparing live and replay
      `RouteResult` outcomes, and fails hard on window-count mismatch. It reports drift, changes by
      id and near-threshold ids (tests with fake classifiers: an offset, a boundary flip and a
      count mismatch).
- [ ] A cuda run that failed over exits 1. Exit codes 0/1/2 are as specified (tests).
- [ ] No output contains corpus text (test scanning output for a record's text).
- [ ] `docs/corpus.md` documents the parity procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Owner gate — GPU smoke and latency on thelab

**Priority:** P1

**Description:** As the owner, I want the candidate image proven on the real GPU with real weights,
and its latency measured, without disturbing production, so that the GPU path ships with evidence.

**Independent Test:** A recorded thelab run in `docs/bootstrap-notes.md` shows:
- healthy `/health` with `promptguard_device: "cuda"`;
- 86M p50/p95 per-window latency and the 64-window page p95;
- VRAM;
- unchanged production `StartedAt`.

**Implementation Hints:**
- **Pre-flight**, recorded:
  - `docker ps` IDs and `StartedAt` for every production container;
  - `nvidia-smi` free VRAM, with an abort floor of ≥ 2 GB free;
  - a check that 22M and 86M weights at the manifest pins exist in a cache readable without a
    token.
    - **Copy** what is needed into a scratch HF cache; never mount production's cache writable.
    - If weights are missing, stop. The owner acquires them into the scratch cache, with the token
      read via `read -rs`, never on argv.
- **The candidate container:**
  - built or pulled from the epic head;
  - `--name forage-gpu-candidate`, `--restart no`;
  - one GPU via the overlay or `--gpus device=0`;
  - `-p 127.0.0.1:18020:8020` (loopback only);
  - an **explicit minimal env**: `FORAGE_DEVICE=cuda`, the model id and `HF_HOME` pointing at the
    scratch cache;
  - no `VALKEY_URL`, no `HF_TOKEN`, no inherited host env.
- **Measure:**
  - cold load time and `/health`;
  - `scripts/bench_promptguard.py` for `--input 1w` and `--input budget`, a fresh container per
    input per the tool's design. Warm-up is discarded, `--runs 20` gives p50/p95;
  - the 22M, via `FORAGE_MODEL_ID`;
  - a 64-window synthetic benign page timed over 10 runs (p95);
  - `nvidia-smi` utilisation and VRAM before and during.
- **Teardown:** remove the container and confirm VRAM returns to baseline. Production `StartedAt`
  must be unchanged.
- **Never stop Ollama or any production container** without separate owner approval. If VRAM never
  frees, the run is blocked; record it and the owner decides.
- **Commit only** host, driver, image digest, figures, run counts and timestamps. Never paste
  `docker inspect`, env or compose-config output.

**Acceptance Criteria:**
- [ ] The pre-flight is recorded (production IDs and `StartedAt`, free VRAM ≥ 2 GB, weights present
      in a scratch cache).
- [ ] The candidate ran loopback-only with an explicit minimal env and `--restart no`, and was torn
      down. VRAM returned to baseline, and production `StartedAt` is identical before and after.
- [ ] `/health` was healthy with `promptguard_device: "cuda"` (86M).
- [ ] 86M per-window p50 ≤ 25 ms over at least 20 warm runs, with p95 recorded. The 64-window page
      p95 is < 2 s over 10 runs. The 22M figures are recorded, with no bar. VRAM and utilisation
      are recorded.
- [ ] A missed latency bar is recorded with figures and goes to an owner ruling; the release
      waits.

### US-003: Owner gate — parity runs on thelab

**Priority:** P1

**Description:** As the owner, I want US-001's tool run on thelab's GPU for both models, so that the
release has evidence of zero verdict changes.

**Independent Test:** The parity JSON for 22M and 86M at batch 16 shows zero verdict changes and
equal window counts. A batch-1 diagnostic is recorded beside it.

**Implementation Hints:**
- **Environment.** A checkout of the epic head on thelab (`git clone` to a scratch dir, with the
  commit recorded and equal to the audited branch head), `uv sync --extra dev --extra cuda`, and the
  scratch HF cache.
- **Run** `uv run python -m scripts.corpus.parity --model-id <id> --device cuda --batch-size 16`,
  then the same with `--batch-size 1`, for both models.
- **Record** the JSON results by id only, in `docs/bootstrap-notes.md`.
- **If any verdict changes**, the release is blocked. The pre-agreed owner options, each requiring a
  re-run:
  - set `promptguard_cuda_batch_size: 1` as the shipped default;
  - accept with a ruling.
- Same pre-flight and teardown rules as US-002. No container is needed; the process runs as the
  `claude` user and is killed at the end.

**Acceptance Criteria:**
- [ ] The parity JSON for 22M and 86M at batch 16 shows zero verdict changes, equal window counts and
      a non-failed-over cuda run, with max and p99 drift recorded.
- [ ] Batch-1 diagnostic results are recorded beside them.
- [ ] The checkout commit equals the audited branch head, and production is unchanged (pre and
      post `StartedAt`).

### US-004: Owner gate — failover proof on real hardware

**Priority:** P2

**Description:** As the owner, I want both failover policies shown on the real host, so that the
documented behaviour is proven where it matters.

**Independent Test:**
- With `CUDA_VISIBLE_DEVICES=` (GPU hidden) and fallback `cpu`, the candidate reports
  `promptguard_device: "cpu"` after load, `promptguard_requested_device: "cuda"` and
  `promptguard_device_failover`.
- With `refuse`, the container exits non-zero, with the fixed error message.

**Implementation Hints:**
- Same container rules as US-002 (loopback, minimal env, `--restart no`, teardown).
- Record the `/health` JSON fields named above, the exit code, and the one-line log token or
  message.

**Acceptance Criteria:**
- [ ] A hidden GPU with fallback `cpu` shows `promptguard_device: "cpu"`,
      `promptguard_requested_device: "cuda"`, and `promptguard_device_failover` in
      `degraded_reasons`, after load.
- [ ] A hidden GPU with `refuse` gives a non-zero container exit, with the fixed
      `DeviceConfigurationError` message, recorded.

### US-005: GPU sizing docs

**Priority:** P2

**Description:** As an operator, I want measured GPU sizing in the docs, so that I can set the
budget and wait for a GPU host.

**Independent Test:** `docs/configuration.md` "Measured per-window cost" includes the GPU rows from
US-002, and a GPU envelope stating the chunk budget and wait values derived from the recorded p95.

**Implementation Hints:**
- Edit the existing sections:
  - `docs/configuration.md` "Measured per-window cost" and the sizing table;
  - `docs/weights.md` "Benchmarking the classifier".
- **The envelope.** Budget and wait from the p95 per-window figure for a single holder. State the
  VRAM headroom assumed alongside Ollama.
- The **default** stays 64 chunks and 90 s. The GPU values are a documented operator setting.
- If US-002 has not run, this story is blocked. It uses recorded numbers only and never invents
  figures.

**Acceptance Criteria:**
- [ ] The GPU rows (both models, p50/p95, VRAM) are in "Measured per-window cost". A GPU envelope
      gives a budget value and a wait value, each derived from the recorded p95, with the
      derivation shown.
- [ ] The defaults are unchanged, and `docs/weights.md` mentions device-aware benchmarking.

### US-006: v1.4.0 release prep

**Priority:** P2

**Description:** As the owner, I want the v1.4.0 release entry, the final contract 1.5.0 entry and
the pins prepared, so that tagging is the only step left.

**Independent Test:**
- `docs/releases.md` has a `### v1.4.0` NOT YET PUBLISHED entry. Its anchor equals
  `contract/openapi.yaml.sha256`, and its revision values equal `derive_sanitizer_revision()` for
  `cpu` (default and shipped config) and for `cuda`.
- The pins name 1.4.0.
- The 1.5.0 bullet is final.

**Implementation Hints:**
- **Precedent.** The archived `kit_tools/specs/archive/feature-release-1-3-0.md` US-005 and its
  commit `33a6431`.
  - The pin sites come from a classified `git grep -n '1\.3\.0'` table: every hit marked rewrite
    or history, recorded in Implementation Notes.
  - Use the NOT YET PUBLISHED placeholder wording.
- **Order:**
  1. Finalise the 1.5.0 bullet in `pipeline/contract.py`, reconciled against
     `git diff 35a393c..HEAD -- pipeline/contract.py contract/openapi.yaml` (35a393c is the v1.3.0
     merge).
  2. Run `uv run python -m scripts.export_contract`. The docstring is not in the document, so expect
     no OpenAPI change; record it.
  3. Derive the revisions.
  4. Record the rotation per the epic procedure.
  5. Write the entry.
- **Entry items:**
  - one image for CPU and GPU, and the arch split;
  - `FORAGE_DEVICE` and `FORAGE_DEVICE_FALLBACK`;
  - GPU batching;
  - OOM handling;
  - the `/health` device fields and reasons;
  - the four metrics;
  - the `device@cuda` revision input and the cache fingerprint;
  - `compose/gpu.yml`;
  - the amd64 size growth;
  - the driver floor of 580;
  - the parity results;
  - the latency figures.
  - If US-002 or US-003 have not been recorded, write `NOT YET MEASURED` placeholders, which the
    owner sequence fills. Never invent figures.
- **Owner sequence**, at the end of Implementation Notes:
  1. US-002 to US-004 done;
  2. merge;
  3. tag `v1.4.0`;
  4. publish;
  5. anonymous pull;
  6. GPU smoke of the published image on thelab;
  7. fill the placeholders.

**Acceptance Criteria:**
- [ ] The 1.5.0 bullet is final and reconciled against the recorded diff. `export_contract`'s result
      is recorded, and the rotation is recorded per the epic procedure.
- [ ] The `docs/releases.md` v1.4.0 NOT YET PUBLISHED entry contains each listed item, either from
      recorded figures or as `NOT YET MEASURED`, with the anchor and both device revisions verified
      by a recorded command.
- [ ] Pins name 1.4.0, and the classified `1\.3\.0` table is recorded.
      `tests/test_compose_fragments.py` passes.
- [ ] Implementation Notes end with the owner sequence. No tag is pushed.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

## Edge Cases

- **A parity verdict change:** the release is blocked, and the pre-agreed options apply.
  (US-001, US-003)
- **A missed latency bar:** recorded, and the owner rules. (US-002)
- **VRAM below the floor:** the run aborts and is recorded; Ollama is not stopped without approval.
  (US-002)
- **Weights missing on thelab:** the run stops, and the owner acquires them into the scratch cache.
  (US-002)
- **Cassette shas never produced by the drive:** reported; a stale cassette means re-record first.
  (US-001)

## Out of Scope

- CI GPU runners, GPU cassettes, changing defaults for GPU hosts, and concurrent-load GPU sizing.

## Assumptions

- `drive_all` can run against a live classifier, as `record.py` already does.
- Owner approval is given per owner-gate story and covers its listed sequence.

## Technical Considerations

- **Secret handling:** tokens never on argv and never printed; no captured env in commits.
- **Corpus rule:** cite ids only, never payload text.
- **Rotation:** `contract.py` (US-006).

## Related Documentation

- [docs/corpus.md](../../docs/corpus.md), `scripts/corpus/`, [docs/releases.md](../../docs/releases.md),
  [docs/weights.md](../../docs/weights.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Parity re-drives the corpus with a live classifier, and judges verdicts by comparing
live and replay `RouteResult`s.
**Rationale:** Cassettes hold no text, only sha → scores. The real rules include contiguity and
tiers (validation round 1).

**Decision:** Owner runs use a thelab checkout with the cuda extra, not the image.
**Rationale:** The image excludes `scripts/` and `tests/` (`.dockerignore`), and the lock is the same.

### Scope Adjustments

- Round 1 split the spec into six stories: the tool; three owner gates (smoke and latency, parity,
  failover); sizing docs; release prep.
- It also added: production-safety rules, the scratch weights cache, precise latency measures, and
  the release order with placeholders.

## Clarifications

### Session 2026-10-08
- **Q:** How is GPU accuracy proven? **A:** By parity against the CPU cassettes, with zero verdict
  changes at the shipped batch size.
