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

# Feature Spec: Inference Surface — Device on `/health`, `/metrics` and the Revision

## Overview

Spec 1 gives the classifier `device`, `requested_device`, `failed_over` and two counters. This spec
makes them visible, keeping the service's honest-health rule (CLAUDE.md invariant 5: degradation
is loud, never silent):

- **`/health`** gains `promptguard_device` (`"cpu"` | `"cuda"`, or `null` before the device is
  resolved) and a new degraded reason, `promptguard_device_failover`, raised whenever the requested
  device was `cuda` and the active device is `cpu`. This is a **contract 1.5.0** MINOR: one new
  field and one new enum member.
- **`/metrics` `model`** gains `device_failovers` and `oom_batch_reductions`.
- **`sanitizer_revision`** gains an input `device@<requested device>`. GPU and CPU deployments keep
  separate cache keys, because float math differs between devices.
- **`scripts/bench_promptguard.py`** records the device from `/health`.

## Goals

- **`/health.promptguard_device` and the degraded reason:**
  - it equals the classifier's active device;
  - `cuda` requested and running on CPU means `status: "degraded"` with
    `promptguard_device_failover` in `degraded_reasons`, while `promptguard_loaded` stays `true`;
  - default CPU installs report `"cpu"`, and their status is unchanged from v1.3.0.
- **Contract:** `CONTRACT_VERSION == "1.5.0"`, a held `tests/golden/contract_1_5_0.json`, and the
  OpenAPI file and anchor regenerated. Goldens 1.0.0–1.4.0 are byte-unchanged.
- **Revision:** `derive_sanitizer_revision` differs between `FORAGE_DEVICE=cpu` and `cuda`. The
  rotation is recorded per the epic procedure, and the default (`cpu`) value is recorded too.

## User Stories

### US-001: `/health` reports the device and a failover reason (contract 1.5.0)

**Priority:** P1

**Description:** As an operator, I want `/health` to say which device the classifier is running on,
and to go degraded when it failed over from the GPU, so that a GPU host that lost its GPU is
visible on my dashboards.

**Independent Test:** With a classifier stub reporting each state, `/health` shows:
- device `cpu`, healthy;
- device `cuda`, healthy;
- requested `cuda` running on `cpu`: degraded, `promptguard_device_failover`, loaded true.

**Implementation Hints:**
- **Model.** `HealthResponse` (`retrieval_app.py` ~:518-600): add `promptguard_device: Literal["cpu","cuda"] | None` next to `promptguard_model` (~:529-538), resolved from
  `app.state` the same way `_resolved_promptguard_model` (~:382) works. Write the field
  description; it lands in OpenAPI.
- **Vocabulary.** `DegradedReason` Literal plus a constant, in `pipeline/contract.py` ~:217-237. A
  reason missing from the Literal causes a 500, so add it there first. Raise it in the health
  handler (~:2117-2123).
  - Semantics: this is the first degraded reason with a loaded classifier other than the cache
    reasons. State that in the field and handler docstrings.
- **The contract bump** (GOVERNANCE.md :347-379; precedent `promptguard_model` in 1.3.0,
  `tests/test_contract_schema.py` ~:462-470 `_EXPECTED_ONE_THREE_ZERO_DIFF`):
  - `CONTRACT_VERSION = "1.5.0"`;
  - a `* ``1.5.0`` —` in-progress docstring bullet;
  - `tests/golden/contract_1_5_0.json`, and an expected-diff set for 1.5.0;
  - run `uv run python -m scripts.export_contract`;
  - update GOVERNANCE.md :29, `CLAUDE.md` invariant 4, `tests/test_bench_promptguard.py`
    version pins, and the doc anchor quotes.
  - `contract.py` is hashed, so this rotates. Follow the rotation record procedure: read-only
    reversal and every count site (see the epic Notes, and `feature-release-resource-bounds.md`
    Technical Considerations for the site list).
- `contract_smoke.py` follows `HealthResponse.model_fields` automatically.

**Acceptance Criteria:**
- [ ] `/health` returns `promptguard_device`. A default install returns `"cpu"` with an unchanged
      status. A successful cuda device returns `"cuda"` (stubbed tests).
- [ ] Requested `cuda` running on `cpu` gives `status: "degraded"`,
      `promptguard_device_failover` in `degraded_reasons` and `promptguard_loaded: true` (test).
      The reason is in the `DegradedReason` Literal and `DEGRADED_REASONS`.
- [ ] `CONTRACT_VERSION == "1.5.0"`, with an in-progress 1.5.0 bullet naming the field and the
      reason. `tests/golden/contract_1_5_0.json` exists, and goldens 1.0.0–1.4.0 are
      byte-unchanged.
- [ ] `contract/openapi.yaml`, its anchor and the fixture twin are regenerated. Export,
      anchor-quote, governance and bench-pin tests pass.
- [ ] The rotation is recorded per the procedure, with every count site updated.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Device counters on `/metrics`, `device@` revision input, device-aware bench

**Priority:** P2

**Description:** As an operator, I want failovers and out-of-memory batch reductions counted, cache
keys split by device, and the bench to record the device, so that GPU behaviour is measurable and
GPU-scored results never mix with CPU-scored ones in the cache.

**Independent Test:** `/metrics.model` shows `device_failovers` and `oom_batch_reductions` mirroring
the classifier counters. `derive_sanitizer_revision` gives different values for
`FORAGE_DEVICE=cpu` and `cuda`, and the cpu value is recorded. The bench output contains
`promptguard_device`.

**Implementation Hints:**
- **Counters.** `ModelMetricsResponse` (`retrieval_app.py` ~:942) gains both fields, read from the
  classifier (spec 1 US-003).
  - Metric additions must be named in the contract entry. Extend the 1.4.0 analogue of
    `test_every_1_3_0_metric_addition_is_named_in_the_contract_entry` (`tests/test_contract_metrics.py`)
    to 1.5.0, and add the counters to the 1.5.0 bullet.
- **Revision input.** In `pipeline/sanitizer_revision.py`, add
  `digest.update(f"device@{requested_device}".encode())` after the model identity (~:83-84),
  resolving the requested device from the environment through `promptguard/device.py` (spec 1).
  - Use the **requested** device, not the active one. A runtime failover then does not change the
    revision mid-process; document that.
  - This is a new **input**: `sanitizer_revision.py` itself, plus the input. Measure the rotation
    with the input removed, read-only, and record it per the procedure.
  - The default `cpu` value is recorded as the new default revision.
- **Bench.** `scripts/bench_promptguard.py` ~:460-470 copies `/health` provenance; add
  `promptguard_device`. Update `tests/test_bench_promptguard.py` ~:71.
- **Docs.** Add the two counters to `kit_tools/docs/MONITORING.md`. In `docs/configuration.md`,
  state that `FORAGE_DEVICE` is a revision input and `promptguard_cuda_batch_size` is not.

**Acceptance Criteria:**
- [ ] `/metrics.model.device_failovers` and `.oom_batch_reductions` mirror the classifier counters
      (test). Both are named in the 1.5.0 entry, and the metric-addition test passes.
- [ ] `derive_sanitizer_revision` differs between `FORAGE_DEVICE` `cpu` and `cuda` (test). The
      rotation is measured: reverting `sanitizer_revision.py` and removing the input reproduces
      the pre-story value under default, `config.yaml` and `bench/config.yaml`. It is recorded per
      the procedure, and the new default value is in `docs/releases.md` Unreleased.
- [ ] A runtime failover does not change the process's reported revision (test).
- [ ] The bench output includes `promptguard_device` (test).
- [ ] MONITORING.md and the configuration doc are updated.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- **`/health` before the classifier has loaded:** `promptguard_device` reports the requested
  device if it was resolved at boot, otherwise `null`. Document which. (US-001)
- **`refuse` with a failed load-time move:** the classifier is unloaded, so the reason is
  `promptguard_unavailable`, not failover. (US-001)
- **A failover mid-run:** `/health` turns degraded immediately; the revision is unchanged.
  (US-001, US-002)

## Out of Scope

- Any change to how the classifier runs (spec 1); images (spec 3); measurement (spec 4).

## Assumptions

- A new optional field and a new degraded-reason member are a MINOR under GOVERNANCE, as with
  `promptguard_model` and `cache_unauthenticated`.
- Keying the revision on the requested device is enough to separate the caches, because
  failover is rare and visible.

## Technical Considerations

- `contract.py` and `sanitizer_revision.py` are hashed or define the hash. Every rotation is
  measured with read-only reversals.
- Spec 4's release prep finalises the 1.5.0 entry; this spec writes it as in progress.

## Related Documentation

- [contract/GOVERNANCE.md](../../contract/GOVERNANCE.md),
  [MONITORING.md](../docs/MONITORING.md), `docs/configuration.md`

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Report the device in `/health` and the cache key.
**Rationale:** The landscape research recommends making the backend visible and part of cache
identity, because CPU and GPU results are not bit-identical.
**Source:** https://docs.pytorch.org/docs/2.14/notes/numerical_accuracy.html.

## Clarifications

### Session 2026-10-08
- **Q:** Does a failover count as degraded? **A:** Yes. A GPU that was requested but is not in
  use is loud (owner, 2026-10-07).
