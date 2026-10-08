<!-- Template Version: 2.5.0 -->
---
feature: inference-device
status: active
session_ready: true
depends_on: []
vision_ref: "T3 — run the classifier where the hardware is: CPU and GPU from one image"
type: epic-child
size: L
epic: forage-inference-backends
epic_seq: 1
epic_final: false
created: 2026-10-08
updated: 2026-10-08
---

# Feature Spec: Inference Device — Install-Time Device, Failover, GPU Batching

## Overview

`promptguard/classifier.py` is CPU-only by assumption: its docstring says "Runs on CPU only", there is
no `.to(device)` anywhere, and `classify_windows` (~:269-314) runs one forward pass per window at
batch 1. Its only caller is `run_promptguard` in the hashed `pipeline/stage3_promptguard.py` (~:228,
`asyncio.to_thread` inside `completed_thread`).

This spec adds:
- an install-time device choice with a failover policy;
- CUDA loading at full fp32;
- GPU-only window batching;
- out-of-memory (OOM) handling that never races requests already in flight.

The CPU path stays **byte-identical**, so both cassettes and `tests/corpus/baseline.json` are
unchanged.

**Configuration** (owner rulings 2026-10-07/08). Env only, like `FORAGE_MODEL_ID`. Values are
case-insensitive and stripped, then normalised to lowercase.

| `FORAGE_DEVICE` | `FORAGE_DEVICE_FALLBACK` | No usable GPU at boot (probe) | CUDA failure while loading weights | GPU OOM at batch 1 mid-run |
|---|---|---|---|---|
| `cpu` (default) | ignored | n/a | n/a | n/a |
| `cuda` | `cpu` (default) | model loads on CPU, **failed over** | model loads on CPU, **failed over** | switch to a CPU copy, **failed over** |
| `cuda` | `refuse` | **startup refuses** (`DeviceConfigurationError`) | `load()` returns False, so degraded `promptguard_unavailable`; weight acquisition retries on its normal schedule | the request gets `unavailable_result` per its tier; latched `oom_refused` state until a later GPU classification succeeds |

There is no `auto` value.

## Goals

- **Default `cpu` changes nothing.** A tiny seeded DeBERTa's classifier scores equal golden floats
  committed before the change (`==`), and `load()` makes no `Module.to` call. Corpus baseline and
  cassettes are unchanged.
- **Failover and refusal work as the table says.** Every row is tested with mocked `torch.cuda` and
  fake models, with no GPU needed.
- **Batching is correct.** On a tiny seeded CPU model, forced batching matches batch 1 within 1e-5
  per window, in order. The production CPU path never batches.
- **The OOM path is safe under concurrency:**
  - a thread already mid-forward on the GPU when another thread fails over completes or retries on
    the CPU copy, and never raises a device-mismatch error (threaded test);
  - an OOM-refused, unscanned body is never written to the content cache.

## User Stories

### US-001: Device settings and the boot-time probe

**Priority:** P1

**Description:** As an operator, I want `FORAGE_DEVICE` and `FORAGE_DEVICE_FALLBACK` parsed and
validated at startup, with the GPU probed before the app serves, so that bad settings or a missing GPU
under `refuse` stop the service immediately and loudly.

**Independent Test:**
- `resolve_device_settings` returns normalised settings for valid values.
- Invalid values raise `DeviceConfigurationError` with a fixed message per variable.
- With the probe mocked to fail, lifespan startup under `cuda` + `refuse` raises before serving.

**Implementation Hints:**
- **New module `promptguard/device.py`.** Unhashed. Contents:
  - `DeviceSettings` (a frozen dataclass: `device`, `fallback`);
  - `DeviceConfigurationError(ValueError)`;
  - `resolve_device_settings(environ) -> DeviceSettings`, which strips and lowercases values and
    accepts `cpu|cuda` and `cpu|refuse`, with defaults `cpu`/`cpu`.
    - Blank counts as unset.
    - Any other value raises with a **fixed** message naming the variable, never echoing its value.
  - `requested_device_token(environ) -> str`, the **non-raising** twin for the revision hash (spec 2):
    `"cpu"` or `"cuda"` for valid values, `"invalid"` otherwise. Both share one parse helper, so
    they cannot disagree.
- **`probe_cuda() -> Literal["ok","unavailable","oom"]`:**
  - `torch.cuda.is_available()`, then `torch.empty(1, device="cuda:0")` inside `try`;
  - `torch.cuda.OutOfMemoryError` maps to `"oom"`, any other exception to `"unavailable"`;
  - log the closed token `promptguard_device_probe result=<ok|unavailable|oom>`, never exception
    text;
  - import torch lazily, as `load()` does (~:125).
- **Lifespan wiring.** Follow `ModelConfigurationError` (`retrieval_app.py` ~:1675-1678) and
  `promptguard_threads_from_config` (~:1895).
  - Resolve and probe **before** `app.state.classifier` is created (~:1894) and before
    `asyncio.create_task(acquisition.run())` (~:1902).
  - `cuda` + `refuse` with a non-ok probe raises `DeviceConfigurationError`.
  - `cuda` + `cpu` with a non-ok probe records `boot_probe_failed=True` for US-002.
  - `FORAGE_DEVICE_FALLBACK` with device `cpu`: an INFO log, otherwise ignored.
- **The env-name AST test.** If `model_fetcher.py`'s test asserts the env-name set (~:188-193),
  these names live in `promptguard/device.py`, outside it.
- **Tests** go in `tests/test_promptguard_device.py`. Lifespan tests use the app-factory pattern
  from `tests/test_app.py`'s `ModelConfigurationError` tests.
- **Docs:**
  - `docs/configuration.md`: two env rows, plus a "Device selection" subsection containing the
    Overview table;
  - `kit_tools/docs/ENV_REFERENCE.md`.

**Acceptance Criteria:**
- [ ] `resolve_device_settings` accepts `cpu`/`cuda` and `cpu`/`refuse`, case-insensitively and
      stripped, with defaults `cpu`/`cpu`, and treats blank as unset. An invalid value raises
      `DeviceConfigurationError`, whose message names the variable and not the value. One test per
      variable checks that a sentinel value is absent from the message.
- [ ] `requested_device_token` returns `"cpu"`, `"cuda"` or `"invalid"`, never raises, and agrees
      with `resolve_device_settings` on every valid input (parametrised test).
- [ ] `probe_cuda` maps a mocked unavailable GPU, an OOM and a generic error to the three results,
      and logs only the closed token. A caplog test injects an exception carrying a sentinel and
      asserts the sentinel is absent from every log record.
- [ ] `cuda` + `refuse` with a failing probe raises during lifespan startup, before serving. `cuda`
      + `cpu` with a failing probe starts the app (app-factory tests).
- [ ] The configuration doc and ENV_REFERENCE document both variables and the table.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Load the classifier on CUDA, with failover and fp32

**Priority:** P1

**Description:** As an operator, I want the classifier loaded onto the GPU at full fp32 precision
when I ask for `cuda`, and loaded on CPU with a failover mark when the GPU is unusable under
fallback `cpu`, so that a GPU host classifies on the GPU and a broken one keeps working visibly.

**Independent Test:** With fake models:
- default settings make no `Module.to` call and score equal to committed goldens;
- `cuda` with a working mocked GPU moves the model, applies fp32 settings first and moves inputs;
- a `.to("cuda")` that moves half the parameters and then raises leaves an all-CPU model marked
  failed over.

**Implementation Hints:**
- **Classifier state.** `PromptGuardClassifier.configure_device(settings, boot_probe_failed)`, called
  next to `configure_threads`. The state:
  - `_active: tuple[model, device]`, a **single reference swapped atomically** (US-004 relies on it);
  - `_requested_device`, `_fallback`, `_failed_over`;
  - `_state_lock` (a `threading.Lock`).
  - Read-only properties, plus `device_state() -> DeviceState`, a frozen snapshot of `device`,
    `requested_device`, `failed_over`, `oom_refused` and the counters, read under `_state_lock` so
    `/health` can never see a torn read.
- **`load()`** (~:94-206).
  - **CPU path is byte-identical:** no `.to()`, no dtype change.
  - **For `cuda` with no boot-probe failure:**
    1. Before the move, set `torch.backends.cuda.matmul.fp32_precision = "ieee"` and
       `torch.backends.cudnn.conv.fp32_precision = "ieee"`. Verify these attribute names against
       the **locked** torch (2.14); use `allow_tf32 = False` if they are absent.
    2. Record which precision mode was used in `device_state()`.
    3. Call `model.to("cuda")` after `model.eval()` (~:201).
  - **On a CUDA failure during the move:**
    1. Call `model.to("cpu")`.
    2. Call `torch.cuda.empty_cache()` inside its own `try`, so a dead driver cannot raise.
    3. Verify every parameter's device is CPU; if not, return False and let acquisition retry the
       load.
    4. Then:
       - fallback `cpu`: mark failed over and log `promptguard_device_failover reason=<unavailable|oom|load_error>`;
       - fallback `refuse`: return False, which is degraded `promptguard_unavailable`. Log the
         closed token `promptguard_device_load_failed reason=…`, distinct from a weights failure.
         `WeightAcquisition.run()` retries on its existing schedule (~:1984). That is the
         intended self-healing; document it.
  - **`boot_probe_failed`** with fallback `cpu`: load on CPU directly, marked failed over with
    reason `unavailable`.
- **Inputs:** in `classify_windows`, take the `(model, device)` snapshot at entry and move the
  tokenizer outputs to that device. On CPU nothing moves.
- **Golden test.** A tiny seeded CPU DeBERTa (2 layers, small vocab, random init, `torch.manual_seed`)
  and a fixed tokenizer stub.
  - Generate golden scores at the **pre-change** commit, and commit them under `tests/golden/`
    (e.g. `promptguard_cpu_scores.json`).
  - Assert `==` after the change.
  - Never construct the 86M.

**Acceptance Criteria:**
- [ ] With default settings, `load()` makes no `Module.to` call, and the tiny seeded model's scores
      equal the committed golden floats exactly (test). The goldens were generated before the
      change, and the generating commit is noted in the test file.
- [ ] `cuda` with a working mocked GPU applies the fp32 settings before `.to("cuda")`, records the
      precision mode, and moves the inputs to cuda (test with a recording fake model).
- [ ] A move that relocates half the parameters and then raises ends with every parameter on CPU,
      `failed_over` true and one closed `promptguard_device_failover` WARNING under fallback
      `cpu` (test).
- [ ] Under `refuse` it returns False, with a `promptguard_device_load_failed` token, and the
      classifier stays unloaded (test).
- [ ] A boot-probe failure under fallback `cpu` loads on CPU, marked failed over with reason
      `unavailable` (test).
- [ ] `device_state()` returns a consistent snapshot taken under the lock (test).
- [ ] No log record from the load path contains exception text (sentinel caplog test).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-003: Batch a page's windows on the GPU

**Priority:** P1

**Description:** As an operator on a GPU host, I want a page's windows classified in batches, so
that a long page costs a few forward passes instead of one per window.

**Independent Test:** With batching forced on a tiny seeded CPU model, `classify_windows` returns
the same window order and scores within 1e-5 of batch 1. With the device on `cpu`, production makes
exactly one forward pass per window.

**Implementation Hints:**
- **Knob.** `promptguard_cuda_batch_size`, a top-level key read like `promptguard_threads`
  (`classifier.py` ~:51-60, `bounded_int`), range 1–64, default 16.
  - Register it in `retrieval_app.KNOWN_CONFIG_KEYS` as **not security-relevant**.
  - Update the partition test and the shipped-defaults test (`tests/test_contract_metrics.py`
    ~:800-850).
  - Add it to `config.yaml`, `bench/config.yaml` and `docs/configuration.md`.
- **Effective batch size.** Process-wide `_effective_batch`, initialised to the knob. It only
  **shrinks** (US-004) and is restored only by a restart. This is consistent with no re-probe, and
  it is reported in `device_state()`.
- **The batched path** runs only when the snapshot device is `cuda`:
  1. tokenize the window list in one call **under `_tokenizer_lock`** (the tokenizer is not
     thread-safe), with `padding=True`, `truncation=True`, `max_length=512` and
     `return_tensors="pt"`;
  2. run the forward pass in slices of `_effective_batch`;
  3. softmax each slice and read it with `.tolist()`.
  - Factor this as a private method taking the batch size, so a CPU test can force it.
- **Budget.** The `max_chunks` refusal (~:291-294) stays before any inference.
- **Tolerance.** 1e-5 on the tiny model. Record the measured max difference. The real-model drift is
  measured in spec 4.

**Acceptance Criteria:**
- [ ] `promptguard_cuda_batch_size` is bounded 1–64 (default 16), registered, classified, present
      in both configs and documented.
- [ ] On `cpu`, `classify_windows` makes exactly one forward pass per window (call-count test).
- [ ] The forced batched path returns the right count, in window order, each score within 1e-5 of
      batch 1, for page sizes 1, b−1, b, b+1 and 3b. The max difference is recorded.
- [ ] The `max_chunks` refusal fires before any forward pass on the batched path (test).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-004: Handle GPU out-of-memory safely

**Priority:** P2

**Description:** As an operator sharing a GPU with other services, I want an OOM during
classification to shrink the batch, then fail over to CPU, or refuse per my policy. It must never
race other in-flight requests, never cache an unscanned body, and always stay visible. Then a crowded
GPU degrades the service honestly instead of failing requests at random.

**Independent Test:** Inject `torch.cuda.OutOfMemoryError` into fake models, and check:
- the effective batch halves and stays halved;
- at batch 1 under fallback `cpu`, an atomically swapped CPU copy serves this request and later ones,
  while a concurrent in-flight request finishes without a device error;
- under `refuse`, the request gets `unavailable_result` per tier, nothing is cached, and
  `oom_refused` latches until a GPU success.

**Implementation Hints:**
- **Sequence**, in `classify_windows` on a cuda snapshot. Catch only `torch.cuda.OutOfMemoryError`,
  then call `torch.cuda.empty_cache()` (guarded).
  1. While `_effective_batch > 1`: halve it under `_state_lock`, increment `oom_batch_reductions`,
     and retry the remaining windows.
  2. At batch 1, under fallback `cpu`:
     - under `_state_lock`, if `_active` is still the cuda pair, build a **separate** CPU model
       (`copy.deepcopy(model).to("cpu")`, or reload the state dict into a fresh CPU module);
     - swap `_active` atomically and set `failed_over` with reason `oom`;
     - increment `device_failovers` and log `promptguard_device_failover reason=oom`;
     - re-run this request's remaining windows on the CPU snapshot at batch 1.
     - The old GPU model is released when its last in-flight user drops it.
     - Host RAM for the copy is about 1.1 GB for the 86M. Document it beside the memory advisory
       (`pipeline/extraction_limits.py` resident-delta table).
  3. At batch 1, under `refuse`: raise the new `PromptGuardUnavailableError` (defined in
     `promptguard/classifier.py`), set the latched `oom_refused` and increment `oom_refusals`. The
     model stays on cuda. Clear `oom_refused` on the next successful cuda classification.
- **In-flight safety.** Every call works on its entry snapshot. A thread whose snapshot is cuda and
  that gets a device-mismatch `RuntimeError` (impossible with a separate copy, but defend anyway) or
  an OOM after a swap re-reads `_active` and retries once on the current snapshot. This is a test
  case.
- **stage3 mapping. This rotation is certain.** `pipeline/stage3_promptguard.py` (hashed) is the
  only caller.
  - Catch `PromptGuardUnavailableError` around the `completed_thread` block (~:227-231) and return
    `unavailable_result(tier_value, fail_closed=fail_closed)`. The tier semantics are unchanged:
    fail-closed tiers get `unavailable_blocked`, fail-open tiers get `unavailable_allowed`.
  - Log the closed token `promptguard_oom_refused route=…`.
  - Never return empty scores.
- **Cache guard. A second hashed file, `pipeline/orchestrator.py`.** Step 8 (~:764-790) refuses to
  cache only a wait-timeout unscanned body while the classifier is loaded. Generalise the guard to
  "stage 3 skipped as model-unavailable while `classifier_loaded`", which covers both cases.
  - Test it with the same shape as the wait-timeout test, on every route that caches.
- **Rotation.** `stage3_promptguard.py` and `orchestrator.py` move. Follow the epic's **Rotation
  record procedure**: each file reverted alone, plus a both-reverted control.

**Acceptance Criteria:**
- [ ] An OOM at batch 16 retries at 8, and the request returns all scores in order. The effective
      batch stays 8 for the next request, and `oom_batch_reductions` increments (test).
- [ ] An OOM at batch 1 under fallback `cpu` swaps to a separate CPU copy exactly once, even with
      two concurrent callers. It sets `failed_over` and increments `device_failovers`, and the
      request returns scores (threaded test).
- [ ] A third thread already mid-forward on the cuda snapshot during the swap finishes without a
      device-mismatch error (threaded test with an event-gated fake forward).
- [ ] An OOM at batch 1 under `refuse` gives `unavailable_result` for that request's tier: one
      fail-closed and one fail-open tier tested. It sets `oom_refused` and increments
      `oom_refusals`. A later cuda success clears `oom_refused` (tests).
- [ ] An OOM-refused body is never written to the content cache on any caching route (test,
      mirroring the wait-timeout guard test).
- [ ] Only `torch.cuda.OutOfMemoryError` enters this path; other exceptions propagate as today. Logs
      are closed tokens only (caplog sentinel test).
- [ ] The rotation (`stage3_promptguard.py`, `orchestrator.py`) is measured and recorded per the
      epic procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- **A garbage, blank or mixed-case env value:** normalised, or refused without echoing it. (US-001)
- **`FORAGE_DEVICE=cpu` with a fallback set:** the fallback is ignored, with an INFO log. (US-001)
- **The probe passes but the move fails partway:** all parameters go back to CPU, or the load
  fails and retries. (US-002)
- **`refuse` with a load-time failure:** degraded and retrying, not a crash. Only the boot probe
  refuses startup. (US-002)
- **A one-window page:** batch of 1. The last window is padded and masked. (US-003)
- **A crowded card on every request:** the sticky effective batch avoids repeating the
  OOM-and-halve cycle. (US-004)
- **A thread in flight during a swap:** finishes on its own snapshot. (US-004)

## Out of Scope

- The `/health`, `/metrics`, contract and revision surface (spec 2).
- Images (spec 3).
- Measurement (spec 4).
- `auto`, re-probe, MPS, multi-GPU, fp16/bf16, ONNX Runtime and int8.

## Assumptions

- The locked torch (2.14) exposes `torch.cuda.OutOfMemoryError` on every wheel, including the CPU
  wheel. Verify this; if not, guard with `getattr`.
- One GPU, `cuda:0`. `CUDA_VISIBLE_DEVICES` selects which.
- Weights files are device-agnostic, so acquisition is unchanged.

## Technical Considerations

- `promptguard/` and `cache.py` are unhashed. `stage3_promptguard.py` and `orchestrator.py` are
  hashed; US-004 rotates them.
- The suite has no GPU. CUDA behaviour is proven by fakes here and on real hardware in spec 4.

## Related Documentation

- `promptguard/classifier.py`, `pipeline/stage3_promptguard.py`, `pipeline/orchestrator.py` (Step 8),
  `model_fetcher.py`, [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Fail over by building and atomically swapping a separate CPU copy, never with an
in-place `model.to("cpu")`.
**Rationale:** `nn.Module.to` swaps parameter storage in place, which breaks concurrent forwards
(validation round 1).

**Decision:** `PromptGuardUnavailableError` is caught in `stage3_promptguard.py`, and the cache guard
is generalised in `orchestrator.py`. Both rotate.
**Rationale:** No unhashed caller exists. An unscanned body served while the classifier is loaded
must not be cached (the existing wait-timeout precedent).

**Decision:** fp32, with TF32 off on CUDA.
**Source:** https://docs.pytorch.org/docs/2.14/notes/numerical_accuracy.html.

**Decision:** Batch on GPU only, with a sticky effective batch size.
**Source:** https://huggingface.co/docs/transformers/main_classes/pipelines. PyTorch's per-process
memory fraction is advisory: https://github.com/pytorch/pytorch/issues/69688.

### Scope Adjustments

- Round 1 split the spec into four stories (settings and probe; load; batching; OOM) and decided:
  - the refuse semantics;
  - the in-flight rule;
  - the cache guard;
  - sticky batching;
  - golden-float testing.

## Clarifications

### Session 2026-10-07/08
- **Q:** torch with CUDA or ONNX Runtime? **A:** torch, with a seam.
- **Q:** GPU requested but unusable? **A:** Fall back to CPU, degraded (or refuse, per the install
  option).
- **Q:** Batch on GPU? **A:** Yes, GPU only.
- **Q:** Config shape? **A:** `FORAGE_DEVICE=cpu|cuda` plus `FORAGE_DEVICE_FALLBACK=cpu|refuse`, with
  no `auto`.
