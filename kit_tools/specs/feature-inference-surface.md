<!-- Template Version: 2.5.0 -->
---
feature: inference-surface
status: active
session_ready: true
depends_on: [inference-device]
vision_ref: "T3 — run the classifier where the hardware is: CPU and GPU from one image"
type: epic-child
size: L
epic: forage-inference-backends
epic_seq: 2
epic_final: false
created: 2026-10-08
updated: 2026-10-08
---

# Feature Spec: Inference Surface — Device on `/health`, `/metrics`, the Revision and the Cache Key

## Overview

Spec 1 gives the classifier `device_state()`, a consistent snapshot of:
- active device and requested device;
- `failed_over` and `oom_refused`;
- precision mode and effective batch size;
- the counters `oom_batch_reductions`, `device_failovers` and `oom_refusals`.

This spec makes that state visible, keeping invariant 5 (degradation is loud, never silent).

**`/health` gains two fields:**
- `promptguard_device`: the **active** device, `null` until the classifier is loaded.
- `promptguard_requested_device`: the requested device, always present.

**`/health` gains two degraded reasons:**
- `promptguard_device_failover`: requested `cuda`, active `cpu`.
- `promptguard_device_oom`: `oom_refused` is latched under `refuse`.

These fields and reasons make contract **1.5.0**, a MINOR.

**State table** for `/health`:

| Loaded | Requested | Active | failed_over | oom_refused | `promptguard_device` | Degraded reasons added |
|---|---|---|---|---|---|---|
| no | any | — | — | — | `null` | `promptguard_unavailable` (existing) |
| yes | cpu | cpu | no | no | `cpu` | none |
| yes | cuda | cuda | no | no | `cuda` | none |
| yes | cuda | cpu | yes | no | `cpu` | `promptguard_device_failover` |
| yes | cuda | cuda | no | yes | `cuda` | `promptguard_device_oom` |

`promptguard_device_failover` never accompanies `promptguard_unavailable`. `oom_refused` only latches
under `refuse` with active `cuda`, so it never co-occurs with a failover. With active `cpu` it is
ignored.

**Field sources and fallbacks:**
- `promptguard_requested_device` comes from `app.state.promptguard_requested_device`, set at
  lifespan.
- The helper `_resolved_requested_device()` (beside `_resolved_promptguard_model`, ~:382) falls back
  to `requested_device_token(os.environ)` for lifespan-less test apps, mapping `"invalid"` to
  `"cpu"`.
- The active device and flags come from `classifier.device_state()` through a **tolerant accessor**,
  `_device_snapshot(classifier) -> DeviceState | None`:
  ```
  snap = getattr(classifier, "device_state", None)
  result = snap() if callable(snap) and getattr(classifier, "loaded", False) is True else None
  return result if isinstance(result, DeviceState) else None
  ```
  - The `isinstance` check matters: `MagicMock` classifiers (`tests/fakes.py:45`
    `make_mock_classifier`, `tests/test_app.py:259` and others) have every attribute, so without it a
    mock would reach the cache key and `/health`.
  - **An extra table row:** loaded, with snapshot `None` (stub or mock classifiers, the corpus
    `ReplayClassifier`). `promptguard_device` is `null`, and no device reason is added.

**Other surfaces:**
- **`/metrics` `model`:**
  - adds `device_failovers`, `oom_batch_reductions`, `oom_refusals` and `effective_batch_size`;
  - the bench records both device fields.
- **Revision and cache identity:**
  - **`sanitizer_revision`** hashes `device@cuda` **only when the requested device is `cuda`**, so
    the default CPU revision does not move because of this input;
  - **the content-cache fingerprint** (`cache.cache_policy_fingerprint`, unhashed) adds the
    **active** device next to `classifier_loaded`, so CPU-scored and GPU-scored results never share
    cache keys, even after a failover.

## Goals

- `/health` follows the state table for every row, with stubbed classifier states.
- **Contract 1.5.0.** `CONTRACT_VERSION == "1.5.0"`, a held golden
  `tests/golden/contract_1_5_0.json`, and an `_EXPECTED_ONE_FIVE_ZERO_DIFF` set. Goldens
  1.0.0–1.4.0 are byte-unchanged.
- **The revision.** It differs between requested `cpu` and `cuda`, and is unchanged for `cpu` by the
  device input itself. An `invalid` token never reaches a serving process, because the lifespan
  refuses it.
- **The cache fingerprint.** It differs between active `cpu` and `cuda`, and changes after a
  failover (test).

## User Stories

### US-001: `/health` device fields and reasons (contract 1.5.0)

**Priority:** P1

**Description:** As an operator, I want `/health` to show the active and requested device and to go
degraded on failover or latched OOM refusal, so that a GPU host that lost its GPU, or is refusing on
OOM, is visible on my dashboards.

**Independent Test:** With a stub classifier per state-table row, `/health` returns the row's device
value, reasons and status.

**Implementation Hints:**
- **`HealthResponse`** (`retrieval_app.py` ~:518-600): add `promptguard_device: Literal["cpu","cuda"] | None` and `promptguard_requested_device: Literal["cpu","cuda"]` next to `promptguard_model`
  (~:529-538). Write descriptions, since they reach OpenAPI.
  - Resolve both from the classifier's `device_state()` and from `app.state` (the requested device
    resolved at boot), as `_resolved_promptguard_model` (~:382) does.
- **Reasons.** Add `promptguard_device_failover` and `promptguard_device_oom` to the `DegradedReason`
  Literal and constants in `pipeline/contract.py` (~:217-237). Raise them in the handler (~:2117-2123)
  following the state table.
  - These are the first degraded reasons with a loaded classifier beyond the cache ones; say so in
    the docstrings.
- **The bump** (GOVERNANCE.md :347-379):
  - `CONTRACT_VERSION = "1.5.0"`, with an in-progress `* ``1.5.0`` —` bullet;
  - `tests/golden/contract_1_5_0.json`;
  - in `tests/test_contract_schema.py`, add `_diff_against_1_4_0` and `_EXPECTED_ONE_FIVE_ZERO_DIFF`
    in the shape of the 1.3.0 sweep (~:462). It must contain the two `HealthResponse` properties
    and both `degraded_reasons` enum members;
  - run `uv run python -m scripts.export_contract`, which regenerates `contract/openapi.yaml`, its
    `.sha256` and `tests/fixtures/contract/unregenerated_openapi.yaml`;
  - update the doc anchor quotes checked by `tests/test_contract_export.py`;
  - update `contract/GOVERNANCE.md` :29 (`tests/test_governance_docs.py`), `CLAUDE.md` invariant 4,
    and `tests/test_bench_promptguard.py`'s version pins.
- **Rotation.** `contract.py` moves; follow the epic's **Rotation record procedure**.

**Acceptance Criteria:**
- [x] `/health` matches every state-table row, plus the loaded-with-no-snapshot row: device value,
      reasons and status (one stubbed test per row). `promptguard_device_failover` never appears
      together with `promptguard_unavailable`.
- [x] A bare `MagicMock` classifier yields `None` from `_device_snapshot` (test).
- [x] Both reasons are in the `DegradedReason` Literal and `DEGRADED_REASONS`.
- [x] `CONTRACT_VERSION == "1.5.0"`, with an in-progress bullet naming both fields and both reasons.
      `tests/golden/contract_1_5_0.json` and `_EXPECTED_ONE_FIVE_ZERO_DIFF` exist, and goldens
      1.0.0–1.4.0 are byte-unchanged.
- [x] The OpenAPI file, its anchor and the fixture twin are regenerated. These pass:
      `tests/test_contract_export.py`, `tests/test_contract_schema.py`,
      `tests/test_governance_docs.py` and `tests/test_bench_promptguard.py`.
- [x] The rotation is recorded per the epic procedure.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .` and `uv run ruff format --check .` pass
- [x] `uv run pyright` passes with zero errors

### US-002: Device counters on `/metrics`, and a device-aware bench

**Priority:** P2

**Description:** As an operator, I want failovers, batch reductions, OOM refusals and the effective
batch size on `/metrics`, and the bench to record the device, so that GPU behaviour is measurable.

**Independent Test:** `/metrics.model` mirrors the four values from a stub `device_state()`. The
bench output contains `promptguard_device` and `promptguard_requested_device`.

**Implementation Hints:**
- **`ModelMetricsResponse`** (`retrieval_app.py` ~:942) gains `device_failovers: int`,
  `oom_batch_reductions: int`, `oom_refusals: int` and `effective_batch_size: int | None`.
  - `effective_batch_size` is `None` when the active device is `cpu` or no snapshot exists. The
    counters are `0` without a snapshot.
  - **The model is never constructed.** `/metrics` builds a dict field by field (~:2246-2255, e.g.
    `'retries_scheduled': model_metrics.retries_scheduled`). Add the four keys there, read from
    `_device_snapshot(app.state.classifier)`, which is a different source from the acquisition
    metrics.
  - `tests/test_contract_metrics.py::test_dataclass_counters_and_their_models_carry_the_same_fields`
    (~:668-686) compares `ModelMetrics` with `ModelMetricsResponse`. Exclude a named
    `_DEVICE_FIELDS` frozenset from the model side, and assert that those four are exactly the
    device-sourced ones.
- **`tests/test_contract_metrics.py`:**
  - add a model-section "later" set to `test_every_1_3_0_metric_addition_is_named_in_the_contract_entry`
    (~:221);
  - add a 1.4.0 model baseline frozenset;
  - add `test_every_1_5_0_metric_addition_is_named_in_the_contract_entry`, slicing the 1.5.0 entry;
  - name the four fields in the 1.5.0 bullet.
- **Regenerate** the OpenAPI file and the anchor. The metrics models are **not** in `_SCHEMA_MODELS`
  (verified: Health, SearchRequest, SearchResponse, RetrievedContent, ExtractedContent,
  Pipeline422ErrorResponse), so the golden and `_EXPECTED_ONE_FIVE_ZERO_DIFF` do not change for
  these fields. Assert that.
- **Bench.** In `scripts/bench_promptguard.py` (~:460-470) copy both device fields. Update
  `tests/test_bench_promptguard.py` (~:71).
- **Docs.** `kit_tools/docs/MONITORING.md` lists the four fields with their meaning.
- `contract.py` moves again, so this is a rotation; follow the epic procedure.

**Acceptance Criteria:**
- [x] `/metrics.model` exposes the four fields, mirroring `device_state()` (test). The 1.5.0
      metric-addition test passes and names them.
- [x] The OpenAPI file and anchor are regenerated. The 1.5.0 golden and `_EXPECTED_ONE_FIVE_ZERO_DIFF`
      are unchanged by the metrics fields, and export and schema tests pass.
- [x] Without a classifier snapshot, `/metrics.model` reports zero counters and a null
      `effective_batch_size` (test).
- [x] Bench output includes both device fields (test).
- [x] MONITORING.md lists the four fields with meanings.
- [x] The rotation is recorded per the epic procedure.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .` and `uv run ruff format --check .` pass
- [x] `uv run pyright` passes with zero errors

### US-003: `device@cuda` revision input, and the active device in the cache fingerprint

**Priority:** P1

**Description:** As an operator, I want GPU deployments to carry a distinct revision, and CPU-scored
and GPU-scored results to never share cache keys even after a failover, so that cached verdicts
always match the device that produced them.

**Independent Test:**
- `derive_sanitizer_revision` is unchanged for requested `cpu` and differs for `cuda`.
- `cache_policy_fingerprint` differs for active `cpu` and `cuda`.
- A failover changes the fingerprint and not the process revision.

**Implementation Hints:**
- **The revision** (`pipeline/sanitizer_revision.py` ~:80-93). After the model identity (~:83-84),
  add `if token == "cuda": digest.update(b"device@cuda")`, with `token` from spec 1's non-raising
  `requested_device_token(os.environ)`. `"invalid"` hashes nothing; the lifespan refuses such a
  process anyway.
  - **`sanitizer_revision.py` is not a `_REVISION_SOURCES` member**, so this is a **new input with
    no rotation of the default**. The cpu (default, `config.yaml`, `bench/config.yaml`) revision is
    unchanged by it. Record a no-rotation entry, following the `hardening-promptguard-86m` US-001
    precedent, and record the cuda value.
  - **Call sites, all reading the same environment:**
    - the lifespan `app.state` value (`retrieval_app.py` ~:1771);
    - `/health` (~:2114, via `_resolved_sanitizer_revision` ~:373);
    - the `/extract` success body (~:2518);
    - the `/extract` 422 body (~:2073);
    - `/retrieve`, through the fingerprint (~:2372).

    Test the lifespan app, `/health` and both `/extract` bodies under `FORAGE_DEVICE` unset, `cpu`
    and `cuda`.
- **The cache fingerprint.**
  - `cache.cache_policy_fingerprint` (`cache.py` ~:127, unhashed) gains a required keyword
    `active_device: str | None`. Keep `classifier_loaded` as an independent input.
  - Its **only** call site is `pipeline/orchestrator.py` ~:442-453 (`run_retrieve_pipeline`), where
    `classifier_loaded` is derived. Compute
    `active_device = snap.device if (snap := _device_snapshot(classifier)) else None` there,
    putting the accessor in an unhashed helper module importable by both the orchestrator and the
    app.
  - **Mid-request failover.** At Step 8, beside spec 1's guard (`not wait_timed_out`, plus the
    `unavailable_allowed`-while-loaded condition), re-read the snapshot. Skip the cache write when
    the active device at Step 8 differs from the one used for the fingerprint, including `None` vs a
    value. That is conservative, and rare.
  - **`orchestrator.py` is hashed, so this story rotates.** Follow the epic procedure: the
    orchestrator reverted alone, with an all-reverted control.
- **Docs.** In `docs/configuration.md`, state that `FORAGE_DEVICE=cuda` is a revision input, the
  active device is a cache-key input, and `promptguard_cuda_batch_size` is neither.
- **A residual to document.** Batch size affects GPU scores within parity tolerance, but it is not
  in the cache key.

**Acceptance Criteria:**
- [x] With `FORAGE_DEVICE` unset and with `cpu`, the revision is identical and equal to the
      pre-story value. With `cuda` it differs (tests). The lifespan app, `/health` and both
      `/extract` bodies agree for each environment (tests).
- [x] `cache_policy_fingerprint` takes `active_device`. Active `cpu`, active `cuda` and `None` give
      different fingerprints, and existing mock-classifier tests pass through the tolerant accessor
      (tests).
- [x] A failover during a request (a simulated snapshot change between fingerprint and Step 8) skips
      the cache write. `/health`'s `sanitizer_revision` does not change (tests).
- [x] Using spec 1's threaded failover fixture, a request whose windows straddle the swap returns a
      scanned body, and no cache write happens (test).
- [x] The `orchestrator.py` rotation is measured per the epic procedure. The device input is recorded
      as no-rotation for cpu, with default, `config.yaml`, `bench/config.yaml` and cuda values
      recorded.
- [x] `docs/configuration.md` states which settings are revision inputs and which are cache-key
      inputs.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .` and `uv run ruff format --check .` pass
- [x] `uv run pyright` passes with zero errors

## Edge Cases

- **Before the classifier loads:** `promptguard_device` is `null`, the requested device is present,
  and the reason is `promptguard_unavailable`. (US-001)
- **`refuse` plus a load failure:** row 1 (unloaded), with no failover reason. (US-001)
- **A mid-run failover:** `/health` turns degraded immediately; the fingerprint changes and the
  revision does not. (US-001, US-003)
- **An invalid env value at a request path:** impossible in a serving process, because the lifespan
  refused it. The revision hashes no device input. (US-003)

## Out of Scope

- How the classifier runs (spec 1), images (spec 3) and measurement (spec 4).

## Assumptions

- New optional fields and new reason members are a MINOR, following the `promptguard_model` and
  `cache_unauthenticated` precedents.
- Hashing the device only for `cuda` keeps CPU installs' cache keys stable across this epic, apart
  from the hashed-source byte rotations.

## Technical Considerations

- `contract.py` and `sanitizer_revision.py` are hashed. Each story records its rotation per the
  epic procedure.
- Spec 4's release prep finalises the 1.5.0 bullet.

## Related Documentation

- [contract/GOVERNANCE.md](../../contract/GOVERNANCE.md), `cache.py`,
  [MONITORING.md](../docs/MONITORING.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** The active device goes in the cache fingerprint; the requested device (cuda only) goes
in the revision.
**Rationale:** Keying on the requested device alone mixes CPU-scored results under GPU keys after a
failover (validation round 1).
**Source:** `cache.py` `classifier_loaded` precedent.

**Decision:** `promptguard_device` is the active device or `null`; the requested device is a separate
field.
**Rationale:** Honest health shows what is actually running. Both fields ship in one MINOR.

## Clarifications

### Session 2026-10-08
- **Q:** Is failover degraded? **A:** Yes (owner, 2026-10-07).
