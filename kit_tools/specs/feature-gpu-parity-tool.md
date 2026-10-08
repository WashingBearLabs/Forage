<!-- Template Version: 2.5.0 -->
---
feature: gpu-parity-tool
status: active
session_ready: true
depends_on: [unified-image]
vision_ref: "T3 — run the classifier where the hardware is: CPU and GPU from one image"
type: epic-child
size: L
epic: forage-inference-backends
epic_seq: 4
epic_final: false
created: 2026-10-08
updated: 2026-10-08
---

# Feature Spec: GPU Parity Tool — Prove Any Backend Against the CPU Cassettes

## Overview

A cassette stores no text. `scripts/corpus/replay.py` keeps only
`sha256(stage-3 text) -> {scores, windows}`. Proving a backend therefore means **re-driving the
corpus** through the real pipeline (`scripts/corpus/drivers.py` `drive_all`), as `record.py` does,
with a live classifier on the chosen device. The live drive is compared with a replay drive of the
same cassette: verdict signals by record, and scores by sha.

This spec builds the tool and its hermetic tests. Spec 5 runs it on thelab's GPU (owner gates).

## Goals

- **One shared loader.** It resolves, probes, configures the device and batch size, then loads.
  `record.py` uses it with its behaviour unchanged (`tests/test_corpus_record.py` passes unmodified).
- **`scripts/corpus/parity.py`** reports:
  - verdict changes per (record, route, config) over the stage-3 signal tuple;
  - window-count mismatches;
  - drift;
  - near-threshold ids;
  - cassette/live sha differences;
  - the classifier's effective batch size and OOM/failover counters.

  It exits 1 on any change, mismatch, unrecorded sha, OOM event or failover. All of this is proven
  with fake classifiers.
- **A long-text live-vs-live check.** A synthetic benign text of more than 2 × batch windows is
  classified at batch 1 and at the requested batch on the chosen device, and its window drift is
  reported. This covers the second-batch boundary the corpus never reaches (max 11 windows).

## User Stories

### US-001: A shared live-classifier loader

**Priority:** P1

**Description:** As the owner, I want the corpus recorder's resolve-and-load refusals extracted into
one helper that also applies the device and batch size before loading, so that the recorder and the
parity tool load classifiers identically and safely.

**Independent Test:** `tests/test_corpus_record.py` passes unchanged. The new helper's tests show:
- each refusal reason;
- the call order: resolve, probe, `configure_device`, `configure_batch_size`, `acquire_and_load`;
- that `FORAGE_DEVICE` in the environment is refused, never honoured.

**Implementation Hints:**
- **New `scripts/corpus/live.py`:** `resolve_and_load(model_id, *, device_settings=None, batch_size=None) -> LoadedClassifier | Refusal`.
  - The defaults reproduce `record.py`: CPU, no batch override.
  - **Order:**
    1. Refuse if the model env is set: `FORAGE_MODEL_ID` / `FORAGE_MODEL_REVISION`, and **also**
       `FORAGE_DEVICE` / `FORAGE_DEVICE_FALLBACK`, with the new reason `device_env_set`. The
       device is applied only via the argument.
    2. Check the allowlist and the manifest pin.
    3. Run `probe_cuda()` when the device is cuda.
    4. `PromptGuardClassifier()`, then `configure_device(device_settings, boot_probe_failed)`
       (spec 1).
    5. `configure_batch_size(batch_size)` if given.
    6. `model_fetcher.acquire_and_load`.
    7. Refuse if not loaded.
  - Keep `record.py`'s reason words and `EXIT_REFUSED`.
- **`record.py` `main()`** (~:222-244) calls the helper, with byte-identical behaviour.
- **`scripts/corpus/drivers.py` `_SCRUBBED_ENV`:** add `FORAGE_DEVICE` and `FORAGE_DEVICE_FALLBACK`
  (belt and braces).

**Acceptance Criteria:**
- [ ] `scripts/corpus/live.py` implements the ordered loader. `record.py` uses it, and
      `tests/test_corpus_record.py` passes unmodified.
- [ ] Tests cover:
  - each refusal, including `device_env_set`;
  - the call order with fakes;
  - the device and batch arguments applied before load.
- [ ] `_SCRUBBED_ENV` includes both device variables.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: The parity tool

**Priority:** P1

**Description:** As the owner, I want a tool that drives the whole corpus with a live classifier,
compares it with a replay of the CPU cassette by the real stage-3 rules, and refuses runs that did
not really happen at the requested device and batch size. Then any backend's accuracy is proven, or
disproven, mechanically.

**Independent Test:** With fake live classifiers:
- an offset reports matching drift;
- an offset that flips one boundary record reports exactly that id and exits 1;
- a window-count mismatch fails;
- a cassette missing one sha reports it and exits 1;
- a fake that reports an OOM batch reduction exits 1.

**Implementation Hints:**
- **`ParityClassifier`** (`scripts/corpus/parity.py`), mirroring `RecordingClassifier` (`record.py`
  ~:73-114):
  - calls the live classifier unbudgeted (`max_chunks=None`), then **re-applies `max_chunks`
    post hoc**, exactly as `RecordingClassifier` does;
  - looks the sha up in the cassette and records `(live, recorded)` per sha;
  - **on a miss**, records the sha as unrecorded and returns the live score;
  - `device_state()` delegates to the live classifier. Spec 2's tolerant accessor handles the plain
    `ReplayClassifier`.
- **Drives:**
  - a live drive with `ParityClassifier`;
  - a replay drive with `ReplayClassifier`, over `load_corpus()` for each `configs` entry in the
    cassette;
  - the replay drive skips shas recorded as unrecorded (catch `UnrecordedTextError` per record) and
    reports them.
- **A verdict change** is any difference in
  `(outcome, signals.injection_detected, signals.promptguard_state, signals.rule, signals.omit_reason, signals.refusal)`
  for a (record id, route, config). Score fields are excluded and reported separately as drift.
  Check the actual `RouteResult` / `Signals` field names in `scripts/corpus/outcomes.py` and
  `drivers.py`.
- **The run is valid only if it really happened as requested.** Read `device_state()` at the end. Exit
  1 if:
  - `failed_over`;
  - `oom_batch_reductions > 0`;
  - the effective batch size differs from `--batch-size`;
  - the device differs from `--device`.
- **The long-text check.** Build a synthetic benign text (generated, no corpus text) of
  `2 × batch + 3` windows. Classify it at batch 1 and at the requested batch on the device, and
  report max window drift and any threshold crossing.
- **CLI:**
  - `--model-id`, required; the cassette is chosen by the manifest pin, and refused on mismatch;
  - `--device cpu|cuda`;
  - `--batch-size N`;
  - `--json PATH`.
- **Output** (JSON):
  - device, torch version and precision mode;
  - requested and effective batch size;
  - OOM and failover counters;
  - records driven;
  - max and p99 window drift, and page-max drift;
  - verdict changes (ids, route, config, and which tuple fields changed);
  - near-threshold ids (within 0.01);
  - unrecorded and never-produced shas;
  - long-text drift.
  - **Never corpus text.**
- **Exit codes:**
  - 0: clean;
  - 1: verdict change, count mismatch, unrecorded sha, OOM event, failover, or device or batch
    mismatch;
  - 2: refusal.
- **Tests** (`tests/test_corpus_parity.py`): fakes over **a small corpus subset** to keep the suite
  fast (pass a filtered record list); hermetic.
- **Docs.** `docs/corpus.md` gains a "Backend parity" section, including the CPU control run (expect
  zero drift) and how to read batch 16 against batch 1.

**Acceptance Criteria:**
- [ ] The live and replay drives, the tuple comparison, post-hoc `max_chunks` and miss handling are
      implemented. Tests cover an offset, a boundary flip, a count mismatch and an unrecorded sha.
- [ ] Runs with `failed_over`, OOM reductions, a batch mismatch or a device mismatch exit 1 (tests).
- [ ] The long-text check is reported, and its synthetic text contains no corpus text (test).
- [ ] No output contains corpus text (test scanning output against a record's text). Exit codes are
      0/1/2 as specified.
- [ ] `docs/corpus.md` documents the procedure, the control run and the batch pairing.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- **A stale cassette:** shas that are unrecorded or never produced are reported, and the run exits
  1. Re-record first. (US-002)
- **An OOM mid-run:** the run exits 1 as invalid. (US-002)
- **`FORAGE_DEVICE` set in the shell:** refused as `device_env_set`. (US-001)

## Out of Scope

- Running on the GPU (spec 5), GPU cassettes, and CI GPU runners.

## Assumptions

- `drive_all` runs against any classifier object exposing `loaded`, `classify_windows`, `classify`
  and `calls`, as `RecordingClassifier` does today.

## Technical Considerations

- No hashed source changes. The corpus rule applies: cite ids, never text.

## Related Documentation

- `scripts/corpus/` (`record.py`, `replay.py`, `drivers.py`, `outcomes.py`),
  [docs/corpus.md](../../docs/corpus.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Re-drive with a live classifier, and compare a signal tuple against a replay drive.
**Rationale:** Cassettes hold no text. The outcome label alone can miss stage-3 flips on records that
are already blocked (round 2).

**Decision:** A run that OOM-halved or failed over is invalid.
**Rationale:** Otherwise a "batch 16, cuda" result could be partly batch 4 or partly CPU (round 2).

## Clarifications

### Session 2026-10-08
- **Q:** How is GPU accuracy proven? **A:** By parity against the CPU cassettes, with zero verdict
  changes at the shipped batch size.
