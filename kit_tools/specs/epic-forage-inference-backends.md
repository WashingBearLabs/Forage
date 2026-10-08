<!-- Template Version: 2.1.0 -->
---
epic: forage-inference-backends
status: active
vision_ref: "T3 — run the classifier where the hardware is: CPU and GPU from one image"
created: 2026-10-08
updated: 2026-10-08
---

# Epic: Forage Inference Backends — CPU and GPU PromptGuard from One Image

> Planned 2026-10-08 with `/kit-tools:plan-epic`. Runs **after v1.3.0 is tagged**: PR #44
> (`epic-forage-v1-3-0-release`) must be merged first, because these specs build on its tree
> (contract 1.4.0, `promptguard_wait_seconds` 90, sanitizer revision `2c6d0382…`). Validate with
> `/kit-tools:validate-epic` before executing. Tag and publish (v1.4.0) are owner gates.

## Goal

On real x86 servers, PromptGuard on CPU costs about 1 s per 512-token window and stops getting
faster at about 8 CPUs. On a GPU the same window costs about 15 ms. This epic lets every install
choose its device at install time, from **one image, one repo and one tag scheme**, so the
adversarial lab and production can never drift between variants. Concretely:

- **The `forage` image carries CUDA torch on amd64 and CPU torch on arm64**, from one Dockerfile and
  one lock, with no build arguments.
- **`FORAGE_DEVICE=cpu|cuda`** selects the device; the default is `cpu`, today's behaviour.
- **`FORAGE_DEVICE_FALLBACK=cpu|refuse`** sets the failover policy:
  - `cpu` (default): a missing or unusable GPU at boot runs the classifier on CPU, with `/health`
    reporting degraded. A GPU that runs out of memory mid-run halves the batch, then migrates the
    model to CPU.
  - `refuse`: the service will not start without a usable GPU.
- **GPU-only batching** of a page's windows. The CPU path stays at batch 1, byte-for-byte today's
  scores.
- **Visible device state.** The device is reported on `/health` (contract 1.5.0), counted on
  `/metrics`, and hashed into `sanitizer_revision`.
- **Proven accuracy on GPU.** Every corpus text is classified on CUDA and compared with the
  owner-recorded CPU cassettes. The bar is zero verdict flips at the threshold.

## Measured basis (2026-10-07/08, thelab: Threadripper 2970WX, RTX 4070 Ti, driver 590)

Weights-free harness: random-init models of the real shapes, batch 1 × 512 tokens.

| | 86M ms/window | 22M ms/window |
|---|---|---|
| CPU torch, 8 CPUs (published `forage:1.2.2`) | 1,077–1,212 | 457–493 |
| CPU, 8 CPUs, **CUDA-built torch wheel, no GPU attached** | 1,172 | 408 |
| ONNX Runtime fp32, 8 CPUs | 876 | 380 |
| **CUDA torch, RTX 4070 Ti** | **15.4** | **15.5** |

The CUDA-built wheel runs CPU inference at parity with the CPU-only wheel, so one image costs CPU
installs nothing in speed. The only cost is size: amd64 grows from about 350 MB to an estimated
4 GB.

## Decomposition

| Seq | Feature Spec | Status | Dependencies |
|-----|-------------|--------|--------------|
| 1 | [feature-inference-device.md](feature-inference-device.md) — device configuration and failover at boot, GPU window batching, out-of-memory failover | Planned | v1.3.0 merged |
| 2 | [feature-inference-surface.md](feature-inference-surface.md) — `/health` device and degraded reason (contract 1.5.0), `/metrics` counters, `device@` revision input, device-aware bench | Planned | 1 |
| 3 | [feature-unified-image.md](feature-unified-image.md) — one lock with arch-split torch, one Dockerfile, scoped guards, CI budgets and smokes, `compose/gpu.yml`, install docs | Planned | 1 |
| 4 | [feature-gpu-validation.md](feature-gpu-validation.md) — corpus parity tool, owner GPU smoke and sizing on thelab, v1.4.0 release prep | Planned | 2, 3 |

## Completion Criteria

- [ ] All four specs completed and archived.
- [ ] One `forage` tag serves both device classes:
  - a CPU-only host with default settings behaves exactly as v1.3.0 (same scores, same corpus
    baseline);
  - a GPU host with `FORAGE_DEVICE=cuda` classifies on the GPU.
- [ ] CPU-path classifier scores are byte-identical to v1.3.0 for every corpus text, and both
      cassettes are unchanged.
- [ ] GPU parity on thelab:
  - zero verdict flips at the 0.85 threshold across every corpus text;
  - maximum score drift recorded;
  - real-weights 86M per-window latency recorded and at most 25 ms;
  - a 64-chunk page classifies in under 2 s.
- [ ] CI is green, including a no-GPU smoke for each failover policy:
  - `cuda` + `cpu` fallback boots degraded on CPU;
  - `cuda` + `refuse` fallback refuses to start.
- [ ] **Owner gates:** the real-weights GPU smoke and parity run on thelab, merge, tag `v1.4.0`,
      publish, and an anonymous pull.

## Notes

- **Engine:** torch, with a seam (owner ruling 2026-10-07).
  - ONNX Runtime measured 1.4–1.6× on CPU but needs per-site conversion of gated weights, which
    count as Llama derivatives with naming and attribution rules. It stays on the backlog as a
    future backend behind the same seam.
  - int8 is opt-in only, never the default.
- **Single image** (owner ruling 2026-10-08), to avoid drift and duplicated maintenance across
  repos. A second repo or tag suffix is rejected.
- **Execution:** guarded mode, every spec size L or M.
  - Stories that regenerate the corpus need L.
  - Spec 4 stories US-002 and US-003 are **owner gates**. Claude may run them on thelab via
    `ssh thelab-claude` only with explicit owner approval at the time.
- **Out of epic:** ONNX Runtime and OpenVINO backends, int8, multi-GPU, GPU on arm64, automatic
  re-probe back to GPU after a runtime failover (a restart restores GPU), and Apple MPS.
