<!-- Template Version: 2.5.0 -->
---
feature: gpu-validation
status: active
session_ready: true
depends_on: [inference-surface, unified-image]
vision_ref: "T3 — run the classifier where the hardware is: CPU and GPU from one image"
type: epic-child
size: M
epic: forage-inference-backends
epic_seq: 4
epic_final: true
created: 2026-10-08
updated: 2026-10-08
---

# Feature Spec: GPU Validation — Corpus Parity, Real-Weights Smoke, Sizing, v1.4.0 Prep

## Overview

The suite has no GPU, and CI has no GPU runners, so the GPU path is proven on thelab:
- AMD Threadripper 2970WX;
- RTX 4070 Ti, 12 GB, shared with Ollama;
- driver 590.

The corpus allows one cassette per model, recorded on CPU, so GPU accuracy is proven as **parity**:
every corpus text is classified on CUDA with the real weights, and the scores are compared with the
recorded CPU scores.

**Stories US-002 and US-003 are owner gates.** Claude may run them on thelab through
`ssh thelab-claude` only with the owner's explicit approval at that time. They need the gated
weights; thelab's Poppy already has an acquired model cache.

## Goals

- **Parity on CUDA, real weights, all corpus texts:**
  - zero verdict flips at the configured threshold (0.85);
  - maximum absolute score drift recorded;
  - texts within 0.01 of the threshold listed by id, with no payload text.
- **Real-weights 86M per-window latency on the RTX 4070 Ti ≤ 25 ms.** A 64-chunk page classifies
  in < 2 s. Both are recorded.
- **GPU sizing in `docs/configuration.md`:**
  - per-window latency for both models;
  - VRAM used;
  - a recommended budget and wait for a GPU host.
- **A v1.4.0 release entry**, NOT YET PUBLISHED, with contract 1.5.0 final and pins at 1.4.0.

## User Stories

### US-001: A corpus parity tool

**Priority:** P1

**Description:** As the owner, I want a script that classifies every cassette text with a live
classifier on a chosen device and compares the scores with the recorded ones, so that GPU (or any
backend) accuracy is proven against the CPU recordings.

**Independent Test:** Run the tool with a fake classifier that returns recorded scores plus a known
offset. It reports max drift equal to the offset, flags the records that cross the threshold by
id, and exits non-zero on any flip.

**Implementation Hints:**
- **New** `scripts/corpus/parity.py`, reusing:
  - the cassette loader (`scripts/corpus/replay.py`: `format=1`, records keyed by the sha256 of
    the stage-3 text);
  - the record-time refusals of `scripts/corpus/record.py` ~:222-244 (model id and revision must
    match the cassette; the model must be loaded).
- **The live classifier** is `PromptGuardClassifier` with spec 1's `configure_device`. The tool
  takes `--device cpu|cuda` and `--batch-size`.
- **Output, as JSON:**
  - device and torch version;
  - records compared;
  - max and p99 absolute drift per window and per page max;
  - the number of verdict flips at the threshold, with the record ids;
  - ids within 0.01 of the threshold.
  - **Never print text.** The corpus rule: cite ids only.
- **Exit code:** non-zero on any flip, or on a missing or mismatched cassette.
- **Tests** (`tests/test_corpus_parity.py`): use a fake classifier and the committed cassettes.
  The tests are hermetic, with no weights.
- **Docs:** `docs/corpus.md` gets a "Backend parity" section with the owner procedure, mirroring
  the recording procedure at ~:108-152.

**Acceptance Criteria:**
- [ ] `scripts/corpus/parity.py` compares a live classifier against a cassette and reports drift,
      flips and near-threshold ids. It never prints corpus text (test that scans its output for a
      record's text).
- [ ] It exits non-zero on any verdict flip and on a model or revision mismatch (tests with a fake
      classifier).
- [ ] `docs/corpus.md` documents the owner parity procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Owner gate — real-weights GPU smoke, parity and sizing on thelab

**Priority:** P1

**Description:** As the owner, I want the epic's image proven on a real GPU with the real weights,
the corpus parity run, and the GPU sizing measured, so that the GPU path ships with evidence.

**Independent Test:** The recorded thelab run shows:
- `/health` healthy with `promptguard_device: "cuda"`;
- parity with zero flips for both models;
- latency and VRAM figures written into `docs/configuration.md` and `docs/bootstrap-notes.md`.

**Implementation Hints:**
- **Owner gate.** Run on thelab (`ssh thelab-claude`) only with explicit owner approval.
  - Build or pull the candidate image from the epic branch head.
  - Run it with `gpus: all` through the overlay, `FORAGE_DEVICE=cuda`, and thelab's existing
    model cache mounted read-only. **Never print or pass the HF token on argv** (secret-handling
    rule): reuse the cached weights.
- **Do not disturb production.**
  - Use a separate container name and port, e.g. `127.0.0.1:18020`.
  - Ollama holds about 7.4 GB of VRAM; record the free VRAM before and after.
  - Never stop the `poppy-forage` container.
- **Measure:**
  - cold load time;
  - `/health`;
  - `scripts/bench_promptguard.py` against the container, for one window and for the budget;
  - per-window p50/p95 for both models (the 22M via `FORAGE_MODEL_ID`);
  - VRAM via `nvidia-smi`;
  - a 64-chunk page end to end.
- **Parity:** run US-001's tool on thelab, inside the candidate image, for both models.
- **Failover proof on real hardware:**
  - restart with the GPU hidden (`CUDA_VISIBLE_DEVICES=`): degraded with `promptguard_device: "cpu"`;
  - restart with fallback `refuse` and the GPU hidden: the container exits.
- **Docs:**
  - `docs/configuration.md`: a "GPU" row in the measured table, and a recommended GPU envelope.
    Budget: with ~15 ms per window, 256 chunks hold about 4 s; recommend whether the default can
    rise on GPU hosts, as a documented operator setting only (the default stays 64).
  - `docs/bootstrap-notes.md`: the full record (host, driver, image digest, figures, parity
    results by id).

**Acceptance Criteria:**
- [ ] The thelab record shows healthy `promptguard_device: "cuda"` with the real 86M, 86M p50 ≤ 25
      ms per window, and a 64-chunk page < 2 s, with VRAM recorded.
- [ ] Parity for the 22M and 86M shows zero verdict flips, and max drift is recorded.
- [ ] Failover is proven on real hardware: a hidden GPU gives degraded on CPU, and `refuse` exits.
- [ ] `docs/configuration.md` has the GPU measurements and the recommended GPU envelope.
      `docs/bootstrap-notes.md` carries the full record. Production containers were untouched.

### US-003: v1.4.0 release prep (owner tag remains a gate)

**Priority:** P2

**Description:** As the owner, I want the v1.4.0 release entry, the final contract 1.5.0 entry and
the image pins prepared, so that tagging is the only step left.

**Independent Test:**
- `docs/releases.md` has a `### v1.4.0` NOT YET PUBLISHED entry with contract 1.5.0, the
  anchor, and its revision equal to `derive_sanitizer_revision()`;
- pins name 1.4.0;
- the 1.5.0 docstring bullet is final.

**Implementation Hints:**
- Follow the v1.3.0 release-prep precedent: archived `feature-release-1-3-0.md` US-005 and
  `git show c933673`.
  - Pins: compose fragments, the compose test, README and `contract_smoke.py`.
  - Run a `1\.3\.0` grep with every hit classified as rewritten or kept.
  - Use the NOT YET PUBLISHED placeholder wording.
- **Finalise the 1.5.0 entry** in `pipeline/contract.py`, reconciled against
  `git diff <epic base>..HEAD -- pipeline/contract.py contract/openapi.yaml`. The base is the
  v1.3.0 merge commit; record it. This edits a hashed file, so it rotates: record it per the
  procedure.
- **Release-entry items:**
  - one image for CPU and GPU, and the arch split;
  - `FORAGE_DEVICE`/`FORAGE_DEVICE_FALLBACK`;
  - GPU batching;
  - out-of-memory failover;
  - the `/health` device and reason;
  - the counters;
  - the `device@` revision input;
  - `compose/gpu.yml`;
  - image size growth on amd64;
  - the driver floor;
  - the parity results.
- **Owner sequence.** Write it at the end of Implementation Notes:
  1. real-weights smoke (US-002 done);
  2. merge;
  3. tag `v1.4.0`;
  4. publish;
  5. anonymous pull;
  6. GPU smoke of the published image on thelab;
  7. fill the placeholders.

**Acceptance Criteria:**
- [ ] `docs/releases.md` has a v1.4.0 NOT YET PUBLISHED entry with `contract: 1.5.0`, the
      current anchor and the final revision (equality checked by a recorded command), covering
      every listed item.
- [ ] The 1.5.0 bullet is final and reconciled against the recorded diff, and the rotation is
      recorded.
- [ ] Pins name 1.4.0, and the classified `1\.3\.0` grep table is in Implementation Notes.
      `tests/test_compose_fragments.py` passes.
- [ ] Implementation Notes end with the owner sequence. No tag is pushed.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

## Edge Cases

- **Parity finds a flip:** the release is blocked. Record the ids and drift, and the owner
  decides (for example disabling batching, or a precision change). (US-001, US-002)
- **Ollama holds too much VRAM to load the model:** the run exercises the failover path. Record
  it, and retry when VRAM is free. (US-002)
- **A cassette is stale against the manifest pin:** the tool refuses, and the owner re-records
  first. (US-001)

## Out of Scope

- CI GPU runners; recording GPU cassettes; changing the default budget or wait for GPU hosts
  (documentation only).

## Assumptions

- thelab's model cache holds verified 22M and 86M weights at the manifest pins. If not, the owner
  acquires them with the token on thelab, never passing it on argv.
- Owner approval is given per run.

## Technical Considerations

- **Secret handling (memory):** tokens are never on argv or printed, and captured vendor text is
  never committed.
- **Corpus rule:** never quote payload text. Cite ids only.

## Related Documentation

- [docs/corpus.md](../../docs/corpus.md), `scripts/corpus/`, [docs/releases.md](../../docs/releases.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Prove accuracy by parity against the CPU cassettes; no GPU cassettes.
**Rationale:** The one-cassette-per-model lint (`docs/corpus.md` :144-150) stays. Parity at the
threshold is what matters.

**Decision:** The thelab run is an owner gate, executable by Claude with approval.
**Rationale:** It needs gated weights and touches a production host.

## Clarifications

### Session 2026-10-08
- **Q:** How is GPU accuracy proven? **A:** Parity against the CPU cassettes, with zero verdict
  flips at the threshold.
