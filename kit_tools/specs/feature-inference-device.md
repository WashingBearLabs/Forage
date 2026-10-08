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

`promptguard/classifier.py` is CPU-only by assumption:
- its docstring says "Runs on CPU only";
- there is no `.to(device)` and no `torch.device` anywhere;
- `classify_windows` (~:269-314) runs **one forward pass per window at batch 1**.

This spec adds a device seam, an install-time device choice with a failover policy, GPU-only
batching, and failover when the GPU runs out of memory mid-run. The CPU path stays
**byte-identical** in scores, so both corpus cassettes and the baseline are unchanged.

Configuration, owner rulings 2026-10-07/08:

| `FORAGE_DEVICE` | `FORAGE_DEVICE_FALLBACK` | Boot with no usable GPU | GPU out of memory at batch 1 mid-run |
|---|---|---|---|
| `cpu` (default) | ignored | n/a: runs on CPU, today's behaviour | n/a |
| `cuda` | `cpu` (default) | loads on CPU, device marked **failed over** (spec 2 reports degraded) | model migrated to CPU, marked failed over |
| `cuda` | `refuse` | **startup refuses**, with `DeviceConfigurationError` | the request gets the existing classifier-unavailable result; the model stays on GPU |

There is no `auto` value: a GPU host that loses its driver must never go quietly slow.

## Goals

- **Default stays today's behaviour.** With default settings, every corpus text gets the same
  classifier scores as v1.3.0, compared as exact floats by an in-process test. Both cassettes and
  `tests/corpus/baseline.json` are unchanged.
- **`cuda` + `cpu` fallback with no GPU:** the service boots, classifies on CPU, and the classifier
  reports `device="cpu"` and `failed_over=True`. Tested with mocked `torch.cuda`.
- **`cuda` + `refuse` with no GPU:** startup raises `DeviceConfigurationError` before serving
  (test).
- **Batching is correct.** With batching forced on in a CPU test, the batched path matches the
  batch-1 path within 1e-5 per window and keeps window order. The CPU production path never
  batches.
- **Out-of-memory failover:**
  - on GPU OOM the batch size halves until it reaches 1;
  - OOM at batch 1 migrates the model to CPU (fallback `cpu`), or returns the unavailable result
    (fallback `refuse`);
  - each transition is counted;
  - all of it is tested with injected `torch.cuda.OutOfMemoryError`.

## User Stories

### US-001: Install-time device selection and boot failover

**Priority:** P1

**Description:** As an operator, I want to choose the classifier's device at install time with
`FORAGE_DEVICE` and `FORAGE_DEVICE_FALLBACK`, so that a GPU host classifies on the GPU and a missing
GPU either fails over to CPU visibly or stops the service, as I chose.

**Independent Test:** With `torch.cuda.is_available` patched False, booting with
`FORAGE_DEVICE=cuda`:
- yields a loaded classifier on CPU with `failed_over=True` under fallback `cpu`;
- raises `DeviceConfigurationError` under `refuse`.

Default settings load exactly as today.

**Implementation Hints:**
- **Parsing.** Add a small module `promptguard/device.py` (not a `_REVISION_SOURCES` member;
  `promptguard/` is unhashed) with:
  - `resolve_device_settings(environ) -> DeviceSettings(device: Literal["cpu","cuda"], fallback: Literal["cpu","refuse"])`;
  - any other value raises `DeviceConfigurationError`, which refuses boot, on the
    `ModelConfigurationError` precedent (`retrieval_app.py` ~:1675-1678);
  - `FORAGE_DEVICE_FALLBACK` is ignored, with an INFO log, when the device is `cpu`.
  - Env only, the way `FORAGE_MODEL_ID` is. If `model_fetcher.py` has an AST test asserting its env
    names (~:188-193), keep these names out of it, or update that test deliberately.
- **Classifier state.** `PromptGuardClassifier.configure_device(settings)`, called next to
  `configure_threads` (`retrieval_app.py` ~:1895).
  - New state: `_device` (the active `"cpu"`/`"cuda"`), `_requested_device`, `_fallback`,
    `_failed_over: bool`, `_device_lock` (a `threading.Lock`).
  - Public read-only properties `device`, `requested_device` and `failed_over` for spec 2.
- **Loading** (`load()`, ~:94-206).
  - Keep the CPU path byte-identical: no `.to("cpu")` call, no dtype change.
  - When `cuda` is requested:
    - check `torch.cuda.is_available()` and a trivial allocation probe;
    - set full fp32 precision before moving the model: `torch.backends.cuda.matmul.fp32_precision = "ieee"` and `torch.backends.cudnn.conv.fp32_precision = "ieee"` (torch 2.14 API; fall back to `allow_tf32 = False` if the attributes are absent), recording which was used;
    - call `model.to("cuda")` after `model.eval()` (~:201).
  - **On any CUDA failure**:
    - fallback `cpu`: keep the CPU model, set `_failed_over = True`, and log a closed WARNING token
      `promptguard_device_failover reason=<unavailable|load_error|oom>`, with no exception text;
    - fallback `refuse`: raise `DeviceConfigurationError`.
  - Load failure handling today returns False, giving degraded status (~:163-169). A CUDA failure
    is **not** a load failure under fallback `cpu`.
- **Where refuse happens.** Weights load asynchronously in `WeightAcquisition.run()` (`model_fetcher.py`
  ~:1884, started at `retrieval_app.py` ~:1896-1902), so the device probe must run **before
  serving**, at lifespan start: `torch.cuda.is_available()` plus the probe, with no weights needed.
  - `refuse` raises there.
  - A load-time CUDA failure after weights arrive is treated as `oom`/`load_error`. Under `refuse`
    the classifier stays unloaded, which is the existing degraded `promptguard_unavailable`; record
    that in the docstring.
- **Weights path.** `SupportsWeightLoad` (`model_fetcher.py` ~:1141-1159) keeps its signature,
  because device is classifier state. `_load_verified` (~:1561-1601) is unchanged.
- **Inputs to device.** In `classify_windows`, tokenizer outputs move to `self._device`
  (`{k: v.to(dev)}`) only when the device is `cuda`. On CPU nothing changes.
- **Tests.** Patch `torch.cuda.is_available`, and patch `model.to` to raise. Never require a real
  GPU in the suite (it is hermetic and has no GPU). New file `tests/test_promptguard_device.py`.
- **Docs.** `docs/configuration.md`: two new env rows, plus a "Device selection" subsection with
  the table from the Overview. Also `kit_tools/docs/ENV_REFERENCE.md`, and the docstring "Runs on
  CPU only" line.

**Acceptance Criteria:**
- [ ] `FORAGE_DEVICE` accepts `cpu|cuda` (default `cpu`) and `FORAGE_DEVICE_FALLBACK` accepts
      `cpu|refuse` (default `cpu`). Any other value refuses boot with `DeviceConfigurationError`,
      and the message names the variable but not its value (tests per variable).
- [ ] With default settings, `load()` makes no `.to()` call, and the classifier's scores for
      every corpus text equal the pre-story scores exactly (in-process test over the cassette
      texts, using the replay classifier's text set and a stub model whose weights are seeded
      identically before and after; or an equivalent exact-equality test that the implementer
      records).
- [ ] With CUDA unavailable (mocked), `cuda` + `cpu` gives a loaded CPU classifier with
      `device == "cpu"`, `requested_device == "cuda"`, `failed_over is True`, and one closed
      `promptguard_device_failover reason=unavailable` WARNING (test).
- [ ] With CUDA unavailable (mocked), `cuda` + `refuse` raises `DeviceConfigurationError` during
      lifespan startup, before the app serves (test via the app factory).
- [ ] With CUDA available (mocked) and `model.to("cuda")` succeeding, the fp32 precision settings
      are applied before the move, and tokenizer inputs are moved to the device (test with a
      recording fake model).
- [ ] The configuration doc and ENV_REFERENCE document both variables and the failover table.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Batch a page's windows on the GPU

**Priority:** P1

**Description:** As an operator on a GPU host, I want a page's windows classified in batches, so
that a long page costs a few forward passes instead of one per window.

**Independent Test:** With batching forced on a CPU test model, `classify_windows` returns the same
window order and scores within 1e-5 of the batch-1 path. With the device on `cpu`, the production
path never batches.

**Implementation Hints:**
- **Knob.** `promptguard_cuda_batch_size`, a top-level config key read like `promptguard_threads`
  (`promptguard/classifier.py` ~:51-60, `bounded_int`).
  - Range 1–64, default 16.
  - Register it in `retrieval_app.KNOWN_CONFIG_KEYS` and classify it **not security-relevant**:
    it is a performance knob, and scores stay within parity tolerance. Update the partition test
    (`tests/test_contract_metrics.py` ~:800-850) and the shipped-defaults test.
  - Add it to `config.yaml` and `bench/config.yaml`.
- **Where.** `classify_windows` (~:269-314), only when `self._device == "cuda"`:
  - tokenize the windows as one list with `padding=True`, `truncation=True`, `max_length=512` and
    `return_tensors="pt"`;
  - run the forward pass in slices of the batch size, then softmax;
  - read `[:, injection_index]` with `.tolist()` (one host sync per slice).
  - Padding is masked by `attention_mask`, and the drift is checked in spec 4.
- **Budget.** The `max_chunks` refusal (~:291-294) stays **before** any inference, unchanged.
- **Testability.** Factor the batched path as a private method taking the batch size, so a test
  can run it on a tiny CPU DeBERTa (random init, 2 layers, small vocab, seeded) and compare
  against batch 1.
  - Tolerance 1e-5. Record the measured max difference.
  - Never construct the 86M in tests.
- **Concurrency.** Unchanged: each request still holds the classification permit for its whole
  page.

**Acceptance Criteria:**
- [ ] `promptguard_cuda_batch_size` is bounded 1–64 (default 16), registered and classified in
      `KNOWN_CONFIG_KEYS`, present in both configs, and documented.
- [ ] With the device on `cpu`, `classify_windows` runs exactly one forward pass per window, as
      today (test counting forward calls).
- [ ] The batched path on a tiny seeded CPU model returns the same number of scores, in window
      order, each within 1e-5 of batch 1, for page sizes 1, batch−1, batch, batch+1 and 3×batch
      (tests). The measured max difference is recorded in Implementation Notes.
- [ ] The `max_chunks` refusal still fires before any forward pass on the batched path (test).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-003: Fail over when the GPU runs out of memory mid-run

**Priority:** P2

**Description:** As an operator sharing a GPU with other services (e.g. Ollama), I want an
out-of-memory error during classification to shrink the batch, then fail over per my policy,
counted and logged. Then a crowded GPU degrades the service visibly instead of failing requests at
random.

**Independent Test:** Inject `torch.cuda.OutOfMemoryError` into the fake model's forward pass, then
check each outcome:
- the batch halves and the request still succeeds;
- at batch 1 under fallback `cpu`, the model moves to CPU and the request succeeds;
- under `refuse`, the request gets the unavailable result.

Each path increments its counter.

**Implementation Hints:**
- **Sequence**, in `classify_windows` on the cuda path:
  1. Catch `torch.cuda.OutOfMemoryError` (only that class) and call `torch.cuda.empty_cache()`.
  2. Retry the remaining windows at half the batch size.
  3. At batch 1, OOM again means:
     - fallback `cpu`: under `_device_lock`, run `model.to("cpu")` once, set `_device = "cpu"` and
       `_failed_over = True`, log `promptguard_device_failover reason=oom`, and re-run the
       remaining windows on CPU at batch 1;
     - fallback `refuse`: raise a dedicated exception that `stage3_promptguard` maps to the
       existing `unavailable_result` (`pipeline/stage3_promptguard.py`, the seam used for the
       wait timeout). That path is in a hashed file, so prefer raising
       `PromptGuardUnavailableError` from the classifier and catching it in the unhashed caller
       if one exists. Check where `classify_windows` is called (`stage3_promptguard.py` ~:228)
       and decide whether a rotation is unavoidable; if so, record it per the epic's procedure.
- **Concurrency.** With `classification_concurrency > 1`, two threads may OOM together. The
  migration happens once: `_device_lock` plus a re-check of `_device`. A thread that finds the
  model already on CPU moves its inputs to CPU.
- **Counters** (plain attributes on the classifier, read by spec 2's `/metrics`):
  - `oom_batch_reductions`
  - `device_failovers`
- **No re-probe.** Returning to GPU needs a restart (out of scope); document it.
- **Never log exception text** (CLAUDE.md invariant 6 spirit). Use closed tokens only.

**Acceptance Criteria:**
- [ ] An OOM at batch 16 retries at 8, and the request returns all scores in order. The
      `oom_batch_reductions` counter rises (test with an injected OOM on the first call).
- [ ] An OOM at batch 1 under fallback `cpu` migrates the model to CPU exactly once, even with
      two concurrent callers (threaded test). It sets `device == "cpu"` and `failed_over`,
      increments `device_failovers`, and the request returns scores.
- [ ] An OOM at batch 1 under `refuse` yields the existing classifier-unavailable outcome for
      that request, and the model stays on cuda (test).
- [ ] Only `torch.cuda.OutOfMemoryError` triggers the path. Other exceptions propagate as today
      (test). Log records carry closed tokens only (caplog test).
- [ ] Any `sanitizer_revision` rotation from touching a hashed file is recorded per the
      procedure (the epic Notes and `feature-release-resource-bounds.md` Technical
      Considerations), or none occurs (values recorded).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- **A garbage value in an env var** refuses boot without echoing the value. (US-001)
- **`FORAGE_DEVICE=cpu` with a fallback set:** the fallback is ignored, with an INFO log. (US-001)
- **The GPU is present but the allocation probe fails** (out of memory at boot): treated as
  unavailable, so the fallback applies. (US-001)
- **A one-window page on cuda:** a batch of 1, no special casing. (US-002)
- **The last window shorter than 512 tokens:** padded and masked. (US-002)
- **Two threads hit OOM together:** a single migration. (US-003)
- **OOM during the CPU re-run after failover:** impossible (CPU), but a `RuntimeError`
  propagates as today. (US-003)

## Out of Scope

- `/health`, `/metrics`, the contract and the revision input (spec 2).
- The image, lock and Compose (spec 3).
- Parity measurement and sizing (spec 4).
- An `auto` device, re-probing back to GPU, MPS, multi-GPU, fp16/bf16, ONNX Runtime and int8.

## Assumptions

- `torch.cuda.OutOfMemoryError` is the exception class in torch 2.14.
- One GPU, device index 0. `CUDA_VISIBLE_DEVICES` is the operator's tool for choosing which.
- The weights files are device-agnostic (safetensors), so acquisition is unchanged.

## Technical Considerations

- **Hashed files.** `promptguard/` is not hashed. `pipeline/stage3_promptguard.py` is (see
  US-003); prefer to avoid touching it.
- **The suite has no GPU.** Every CUDA behaviour is tested with mocks or fakes, and spec 4 proves
  the real device.

## Related Documentation

- `promptguard/classifier.py`, `model_fetcher.py`, `docs/configuration.md`,
  [GOTCHAS.md](../docs/GOTCHAS.md) (threads × concurrency ≤ CPUs, ~:202-210)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Explicit device plus a fallback policy; no `auto`.
**Rationale:** Owner ruling 2026-10-08, and invariant 5 (degradation is loud).

**Decision:** fp32 with TF32 disabled on CUDA; no half precision.
**Rationale:** PyTorch documents CPU/GPU result differences. TF32 shifts matmul precision, and the
threshold must stay stable.
**Source:** https://docs.pytorch.org/docs/2.14/notes/numerical_accuracy.html (landscape research
2026-10-08).

**Decision:** Batch on GPU only.
**Rationale:** The transformers docs recommend batching on GPU with uniform lengths and advise
against it on CPU. Windows are uniform at 512 tokens.
**Source:** https://huggingface.co/docs/transformers/main_classes/pipelines.

**Decision:** OOM handling is halve-then-failover.
**Rationale:** PyTorch's per-process memory fraction is advisory, not a reservation, so a shared
card can OOM at any time.
**Source:** https://github.com/pytorch/pytorch/issues/69688.

## Clarifications

### Session 2026-10-07/08
- **Q:** torch with CUDA or ONNX Runtime? **A:** torch, with a seam. ONNX Runtime is a later
  backend.
- **Q:** What if the GPU is requested but unusable? **A:** Fall back to CPU, degraded (or refuse,
  per the install option).
- **Q:** Batch on the GPU? **A:** Yes, GPU only.
- **Q:** Configuration shape? **A:** `FORAGE_DEVICE=cpu|cuda` plus
  `FORAGE_DEVICE_FALLBACK=cpu|refuse`, with no `auto`.
