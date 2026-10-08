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
