# SESSION_SCRATCH.md

> Auto-generated. Append notes as you work. Processed on session close.

## Active Feature

**Working on:** [Feature name or "General" if not feature-specific]
**Feature Spec:** [e.g., `feature-auth.md` or "N/A" if not feature-specific]

---

## Notes

[16:19] US-001: stage 1 linear (no soup copies); rotation 52->53 (46b8d1bb->0ace27ca)
- Files: pipeline/stage1_extraction.py, tests/stage1_shapes.py, tests/test_stage1_complexity.py, tests/golden/stage1_equivalence.json
- Decision: raw-text is a non-mutating walk; digests (not text) frozen; kept out of tests/fixtures (token-shape scan)

[now] US-005: HTML extraction worker (shared stage-1 fn, strict frame, 96 MiB frame cap, Linux envelope); no rotation (0ace27ca)
- Files: pipeline/html_subprocess.py, pipeline/worker_entry.py, tests/test_html_subprocess.py, docs/configuration.md, docs/bootstrap-notes.md
- Decision: stage-2 category set copied as a pinned literal (accessor would edit a hashed file)
[now] US-006: /retrieve HTML bodies > retrieve.html_worker_threshold_bytes (512 KiB) parse in the worker; rotation 53rd->54th (0ace27ca -> 54aa9649)
- Files: pipeline/orchestrator.py, contract.py, retrieve_limits.py, retrieval_app.py, tests/test_html_worker_routing.py, tests/test_html_threshold_calibration.py, docs
- Decision: default 512 KiB (worst shape 1.19 s / +149 MiB; 1 MiB fails the 2 s axis so it is the max); internal counters excluded from the metrics-vs-model test until US-007

[--:--] US-007 held contract 1.4.0 cut (reason, counters, golden, export, rotation 55th)
- Files: pipeline/contract.py, retrieval_app.py, tests/golden/contract_1_4_0.json
[17:59] US-008: /search parse+markup scans moved to one to_thread per result; rotation 56th d582f8da
- Files: pipeline/orchestrator.py, tests/test_search_parse_thread.py, docs
[--:--] US-001 release-padding-gate: fold limit max(2n,n+256), refusal BLOCKs on all routes; rotation 57th d582f8da -> 23444fe4
- Files: pipeline/stage2_structural.py, pipeline/orchestrator.py, tests/test_stage2_fold_forms.py, docs
- Decision: /search refusal uses early continue (not hoisted blocked); per-source tests patch _scan_search_result_fields

[--:--] US-005: v1.3.0 release tree prepared (releases.md entry, pins 1.2.2->1.3.0, tracking docs, counts 5557)
- Files: docs/releases.md, compose/*.yml, tests/test_compose_fragments.py, contract_smoke.py, README, kit_tools docs
- Decision: README/docs say 'prepared, not yet published' with v1.2.2 as latest published

[19:43] validate-implementation feature-release-1-3-0: no criticals; 3 warnings + 4 info logged (2026-10-07-003..009) to worktree AUDIT_FINDINGS.md
- Decision: contract.py "256 is the announced next default" wording not fixed (hashed; would rotate post-release-record)

[14:06] --- Context compacted, session continuing ---

[14:06] --- Context compacted, session continuing ---

[now] validate-epic round 3 (focused, 14 agents) applied to forage-inference-backends specs
- Files: kit_tools/specs/feature-{inference-device,inference-surface,unified-image,gpu-parity-tool,gpu-validation}.md
- Decision: parity replay drive filters records by live shas (drive_all aborts on unrecorded); never-produced shas informational; batch size per-instance; Step 8 keeps not wait_timed_out AND adds unavailable_allowed+loaded; 64-window p95 projected (bench yields ~55); arm64 checks publish-lane only + static lock rule
[17:31] inference-device US-001: device settings + boot probe in lifespan
- Files: promptguard/device.py, retrieval_app.py, tests/test_promptguard_device.py, docs
- Decision: probe runs right after model-id check (fail-fast, before classifier/acquisition)

[now] inference-device US-002: classifier CUDA load, failover, fp32, tiny-model builder
- Files: promptguard/classifier.py, scripts/promptguard_tiny_model.py, typings/transformers, retrieval_app.py, tests/test_promptguard_cuda_load.py
- Decision: `_model` kept as a property over `_active` so tests that install a model directly keep working
[now] inference-device US-003: CUDA batching (promptguard_cuda_batch_size, configure_batch_size, _score_batched)
- Files: promptguard/classifier.py, retrieval_app.py, configs, docs/configuration.md, typings/transformers, tests/test_promptguard_batching.py
- Decision: page tokens moved to device once then sliced; measured batched-vs-batch-1 max diff 0.0 on tiny model
[17:58] inference-device US-004: GPU OOM handling (halve/failover/refuse), stage3 mapping, step 8 cache guard, rotation 60 recorded
- Files: promptguard/classifier.py, pipeline/stage3_promptguard.py, pipeline/orchestrator.py, tests/test_promptguard_oom.py
- Decision: counters as properties, stage3 log has tier not route
