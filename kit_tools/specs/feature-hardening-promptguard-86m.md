<!-- Template Version: 2.5.0 -->
---
feature: hardening-promptguard-86m
status: active
session_ready: true
depends_on: [hardening-resource-envelope]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 7
epic_final: false
execution_order: [US-001, US-006, US-002, US-003, US-005, US-004]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: PromptGuard 86M Opt-In + Contiguity Gating + Benchmark

> **Spec 7 of `epic-forage-hardening`.** Make the classifier *selectable* (`FORAGE_MODEL_ID`, a closed
> allowlist, 22M stays the default — owner decision 4), thread the selected id through **every**
> consumer including the classifier's own `from_pretrained` calls and the per-model weights manifest
> (ruling R29), add **contiguity gating** beside the existing max-score rule — shipped **off** until the
> corpus epic measures it (ruling R17) — ship a host-side **benchmark harness** that measures the
> running service (ruling R18), then two owner gates in order: **US-005 vendors the 86M weights** (the
> manifest is an exact-set allowlist; verification is never skipped) and **US-004 runs the benchmark**
> on the reference container. Binding: owner decision 4; rulings 5 (the 1.3.0 window), 6 (rotations
> recorded), R17, R18, R29, R31, R32, R33. Context: Poppy's `WEB_ACCESS_FAMILY.md` § "PromptGuard
> research (2026-08-30)". Validation round 1 (2026-09-19) applied — see Clarifications.

## Overview

The 2026-08-30 research pass concluded nothing beats Prompt Guard 2 at Forage's constraints, that the
86M variant catches more (multilingual AUC .942 → .995) at roughly 350–560 MB fp32, and that "Prompt
Overflow" — fragments interleaved with benign prose across windows — defeats max-pooling outright. The
first two are code changes; the third (does 86M fit the 1 vCPU / 1 GB reference envelope *as a running
service*?) has no published CPU numbers, so the epic measures it rather than guesses.

Today the model identity is a module constant. `promptguard/classifier.py:23` hard-codes `MODEL_ID`
and `load()` passes it to **both** `from_pretrained` calls (`classifier.py:84-85`, `:98-99`);
`model_fetcher.py:139` imports it (module scope — so `classifier.py` can never import from
`model_fetcher` without a cycle) and uses it at `snapshot_path(...)` sites (`:1108`, `:1525`);
`pipeline/sanitizer_revision.py:10,40` hashes `f"{MODEL_ID}@{resolve_revision()}"`;
`scripts/vendor_weights.py:107,411-429,710` vendors it. `weights_manifest.json` is **not a revision
pin**: it is an exact-set allowlist — `model_id`, `revision`, and a `files[]` list with `path`,
`sha256` and `size` for each of the five weight files — that `model_fetcher.verify_weights()`
(`:764`) enforces on every acquisition leg and that `_load_manifest()` (`:566`) refuses when `files`
is empty (`REASON_MANIFEST_EMPTY`); `read_manifest_pin()` (`:924`) returns one manifest and
`_acquire_and_load()` refuses with `weights_pin_unusable` (`:1617`) when it blesses nothing. The
acquisition pipeline (`acquire_and_load` `:1530`, `_acquire_and_load` `:1586`, `_verify_cached`
`:1511`, `_try_source` `:1665`, `_load_verified` `:1474`, `_download_from_hub` `:1080`) is
model-id-free by construction. `classify()` (`:154-207`) folds its per-window `scores` (`:183-199`)
into `max_score` (`:204-205`) and stage 3 decides on `score > threshold` alone
(`pipeline/stage3_promptguard.py:136`).

Six stories, in execution order `US-001 → US-006 → US-002 → US-003 → US-005 → US-004`:

- **US-001** — the model id becomes a parameter of the whole acquisition and loading path and the
  manifest becomes a per-model allowlist, with **no new environment variable and no behaviour change
  at the default** (the mechanical half; ruling R29, R31).
- **US-006** — `FORAGE_MODEL_ID` on top: allowlisted resolver, refuse-boot in the lifespan, the
  `id2label` and loaded-identity assertions, `/health.promptguard_model` (inside the 1.3.0 window —
  this line rotates, ruling R32), the configured id in the `sanitizer_revision` hash.
- **US-002** — per-window scores on the classifier and the contiguity rule in stage 3 on **all three
  routes**, `promptguard_contiguity_windows` default `0` (disabled; ruling R17), one counter per route,
  the residual spaced-fragment bypass documented.
- **US-003** — `scripts/bench_promptguard.py`, run **on the host** against a running container,
  measuring service-level latency and container RSS (ruling R18); hermetic tests.
- **US-005 (owner gate)** — vendor the 86M weights: licence check, `scripts/vendor_weights.py` for the
  86M, the generated per-model manifest entry committed; nothing unverified ever loads.
- **US-004 (owner gate)** — the benchmark matrix on the reference container, the sizing-table column,
  the opt-in recipe and its resilience caveat. Execution halts at US-005 if the gates have not run.

## Goals

- With `FORAGE_MODEL_ID` unset or set to the 22M id, the model-identity hash input
  `f"{model_id}@{revision}"` is byte-identical to today's (pinned relatively by
  `tests/test_sanitizer_revision.py::test_the_hashed_model_identity_is_model_id_at_revision`, `:95-118`,
  extended with the id); the 86M id produces a different `sanitizer_revision`; any other value refuses
  boot with a closed reason and the value is echoed 0 times.
- With `FORAGE_MODEL_ID=<86M>` and a stubbed auto-class, **both** `from_pretrained` calls receive the
  86M id (asserted on the mock), and a warm 22M snapshot on the same volume is never loaded under the
  86M id.
- A model id with no manifest entry refuses to acquire (closed reason `manifest_model_unknown`) even
  when `FORAGE_MODEL_REVISION` is set; `ALLOWED_SUFFIXES`, `ALLOW_PATTERNS` and `use_safetensors=True`
  are byte-unchanged by this spec.
- With `promptguard_contiguity_windows: 2` and `promptguard_contiguity_threshold: 0.5`, per-window
  scores `[0.6, 0.6]` are `INJECTION_DETECTED` with both chunks flagged and `rule == "contiguity"`;
  `[0.6, 0.2, 0.6]` is `SAFE`; `[0.86]` fires the max rule exactly as before; at the shipped default
  (`0` windows) `[0.6, 0.6]` is `SAFE` and every pre-existing stage-3 verdict and score is unchanged.
- `uv run python -m scripts.bench_promptguard --help` exits 0 with no weights, no Docker and no
  network; its unit tests run under the socket guard with injected fakes; p50/p95 are nearest-rank
  (`[1..20]` → `10`, `19`).
- The 86M manifest entry (revision + five-file digest set) is committed by the vendoring gate, and the
  benchmark table (22M and 86M × `FORAGE_CPUS` 1 and 4, one row per run, plus container RSS) is
  recorded in this file's Implementation Notes and in `docs/configuration.md`'s sizing table before
  the spec is archived.

## User Stories

### US-001: The model id is a parameter — acquisition, loading, and a per-model manifest

**Priority:** P1

**Description:** As a maintainer, I want the model identity to flow as a parameter through the weights
fetcher, the verifier, the classifier loader and the vendoring script, with the manifest holding one
exact-set allowlist per model, so a second model can be selected later without any path silently
falling back to the 22M — and so nothing changes for today's deployments.

**Independent Test:** With the default id everywhere, `uv run pytest tests/test_model_fetcher.py
tests/test_vendor_weights.py tests/test_sanitizer_revision.py` passes and `derive_sanitizer_revision({})`
equals the pre-story value (recorded in Implementation Notes); with a fake `SupportsWeightLoad` double,
`acquire_and_load(..., model_id="acme/other")` calls the double's `load(model_id="acme/other", ...)`
and `verify_weights` selects the `acme/other` manifest entry; a manifest with no entry for the requested
id yields `manifest_model_unknown` and nothing is loaded.

**Implementation Hints:**
- **`DEFAULT_MODEL_ID`.** Rename `MODEL_ID` (`promptguard/classifier.py:23`) to `DEFAULT_MODEL_ID` and
  keep `MODEL_ID = DEFAULT_MODEL_ID` as a deprecated alias for one story so the three importers
  (`model_fetcher.py:139`, `pipeline/sanitizer_revision.py:12`, `scripts/vendor_weights.py:107`,
  `tests/test_model_fetcher.py:120`, `tests/test_sanitizer_revision.py:12`, `tests/test_vendor_weights.py:55`)
  migrate in this story and the alias is deleted at the end of it (`grep -rn '\bMODEL_ID\b' --include='*.py' .`
  returns only the `DEFAULT_MODEL_ID` definition afterwards — the `tests/test_vendor_weights.py:228`
  identity assertion `vendor_weights.MODEL_ID is MODEL_ID` is rewritten, not just re-imported).
- **The loader takes the id.** `PromptGuardClassifier.load(*, model_id: str | None = None, revision,
  cache_dir, local_files_only)` (`classifier.py:50-56`), defaulting to `DEFAULT_MODEL_ID`, and **both**
  `from_pretrained` calls (`:84-85`, `:98-99`) use it. `model_fetcher.SupportsWeightLoad.load`
  (`:1060-1077`) gains the same keyword; `_load_verified()` (`:1474`) and `acquire_and_load()` /
  `_acquire_and_load()` (`:1530`, `:1586`) pass it down; `_verify_cached()` (`:1511`, the
  `snapshot_path(cache_root, MODEL_ID, revision)` at `:1525`) and `_download_from_hub()` (`:1080`,
  `:1108`) take it as a parameter. `classifier.py` must **not** import `model_fetcher` (cycle via
  `model_fetcher.py:139`). Extend `tests/test_model_fetcher.py:790-791` (today asserts both
  `from_pretrained` calls receive `MODEL_ID`) to assert they receive the id passed to `load`.
- **Loaded-identity assertion.** After a successful `from_pretrained`, `load()` verifies the loaded
  model is the requested one: the resolved snapshot directory lies under
  `repo_dirname(model_id)` (`model_fetcher.py:479-481` — the classifier has `cache_dir` and
  `revision`, so it can compute `Path(cache_dir) / HUB_DIRNAME / repo_dirname(model_id) / "snapshots" /
  revision` itself with a private copy of the two-line helper, or `model.config._name_or_path` names
  the id); on mismatch `loaded` stays `False` and a WARNING `model_identity_mismatch` (closed reason,
  no path echo) is logged. Pin the dangerous case: a warm `acme/tiny-guard` snapshot on the volume with
  `model_id="acme/other"` requested → not loaded.
- **Per-model manifest.** `weights_manifest.json` becomes `{"_comment": [...], "models": {"<model
  id>": {"revision": ..., "files": [...]}}}` — the 22M entry is today's content unchanged. `_load_manifest()`
  (`:566-630`) validates the map and returns the entry for a requested id (`read_manifest_pin(path,
  model_id=...)` `:924`); a document with no `models` map, an entry with no `files`, or a file entry
  missing `path`/`sha256`/`size` keeps today's `manifest_invalid` / `manifest_empty` reasons; a requested
  id with no entry is the new closed reason `manifest_model_unknown`. `verify_weights(cache_root, *,
  manifest_path, metrics, model_id)` (`:764`; today derives model and revision from the manifest at
  `:794-795`) selects that entry and **never falls back to another model's entry**; `_acquire_and_load`
  keeps `weights_pin_unusable` (`:1617`) for an entry with an empty file set. `mirror_reference(repository,
  revision)` (`:1014`) stays revision-keyed — two models coexist in one mirror repository because their
  revisions differ; `docs/weights.md` § "The three places the revision appears" (`:27`) says so.
- **The generator writes the map.** `scripts/vendor_weights.py` gains `--model-id` (default
  `DEFAULT_MODEL_ID`), `generate_manifest()` (`:446`) writes or replaces that model's entry and keeps the
  others byte-stable; the manifest `_comment` describes the map; a round-trip test asserts generated
  output parses back through `_load_manifest`. `tests/test_vendor_weights.py:121-129,269,1366-1383` and
  `tests/test_model_fetcher.py:196,307,1139,1329-1333` (manifest-shaped assertions — ~40 sites) move
  with the format.
- **Supply-chain closure unchanged (non-goal, pinned).** `ALLOWED_SUFFIXES` (`model_fetcher.py:249`),
  `ALLOW_PATTERNS` (`:255`) and the loader's `use_safetensors=True` are not edited; a test asserts the
  two constants' values byte-for-byte. A model whose repo carries files outside the suffix set is
  *refused* at vendoring (US-005), never accommodated.
- **`resolve_revision()` stays total** (`:882-902`: logs, returns a value, never raises); it gains a
  `model_id` argument that selects the manifest entry's revision as the default for that model.
  `derive_sanitizer_revision()` runs per request (`retrieval_app.py:292,1421,1728` fallbacks), so no
  function on that path may raise for an allowlisted id.
- Docs: `docs/weights.md` (§ "What the artifact is", § "Vendoring: the procedure" — the `--model-id`
  argument, § "The three places the revision appears" now per model); `kit_tools/docs/ENV_REFERENCE.md:52`'s
  `FORAGE_MODEL_REVISION` row says "the selected model's committed pin"; `kit_tools/arch/CODE_ARCH.md`
  `model_fetcher.py` row.

**Acceptance Criteria:**
- [ ] `load(*, model_id=...)` exists on `PromptGuardClassifier` and on `SupportsWeightLoad`; a test with
      a stubbed auto-class asserts both `from_pretrained` calls receive the requested id
      (`tests/test_model_fetcher.py:790-791` extended); `acquire_and_load(..., model_id=...)` threads it
      through `_verify_cached`, `_download_from_hub`, `_try_source` and `_load_verified` (asserted on a
      recording double).
- [ ] The loaded-identity assertion refuses a warm snapshot of a different model under the requested
      id (`model_identity_mismatch`, `loaded is False`, no path in the log record).
- [ ] `weights_manifest.json` is a per-model map; the 22M entry's `revision` and `files` are
      byte-identical to today's; `_load_manifest` / `read_manifest_pin(model_id=...)` / `verify_weights(...,
      model_id=...)` select the entry; a missing entry yields `manifest_model_unknown` even with
      `FORAGE_MODEL_REVISION` set (test); `manifest_invalid` / `manifest_empty` semantics unchanged (tests).
- [ ] `scripts/vendor_weights.py --model-id` writes the map form, preserves other entries, and a
      round-trip test parses the output with `_load_manifest`.
- [ ] `ALLOWED_SUFFIXES == frozenset({".safetensors", ".json", ".txt", ".model"})` and `ALLOW_PATTERNS`
      are asserted unchanged; `grep -n 'use_safetensors=True' promptguard/classifier.py` returns 1 hit.
- [ ] `grep -rn '\bMODEL_ID\b' --include='*.py' .` returns only the `DEFAULT_MODEL_ID` definition line
      (alias removed); `derive_sanitizer_revision({})` equals the pre-story value (recorded in
      Implementation Notes; no `_REVISION_SOURCES` file is edited by this story — verify with
      `git diff --stat` against the eight listed files).
- [ ] `docs/weights.md` and `ENV_REFERENCE.md` describe the per-model manifest and pin (`grep -c
      'per model' docs/weights.md` ≥ 1).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-006: `FORAGE_MODEL_ID` — allowlisted, refuse-boot, asserted at load, reported on `/health`

**Priority:** P1

**Description:** As an operator, I want to select the 86M classifier with one environment variable
and see which model is actually loaded on `/health`, so the upgrade is an opt-in I can measure, not a
fork of the image — while a typo or an unknown model can never be loaded.

**Independent Test:** With `FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M` and a stubbed loader,
`/health` reports `promptguard_model` equal to that id and `derive_sanitizer_revision({})` differs from
the unset value; with `FORAGE_MODEL_ID=evil/model` the lifespan raises `ModelConfigurationError`
before the app serves, the log carries the closed reason `model_id_not_allowed` and a sentinel value
0 times; with the id unset the hash input is byte-identical to explicitly setting the 22M id.

**Implementation Hints:**
- **One read site, like the revision.** `resolve_model_id() -> str` in `model_fetcher.py` beside
  `resolve_revision()` (`:882`): read `FORAGE_MODEL_ID` once, strip, compare against a closed
  `ALLOWED_MODEL_IDS = ("meta-llama/Llama-Prompt-Guard-2-22M", "meta-llama/Llama-Prompt-Guard-2-86M")`;
  default `DEFAULT_MODEL_ID`. The resolver is **total** for allowlisted values and returns a
  `(model_id, ok)` shape (or the default plus a WARNING `model_id_not_allowed`, never echoing the value)
  so the per-request `derive_sanitizer_revision` fallbacks (`retrieval_app.py:292,1421,1728`) can never
  500; the **lifespan** performs the one refusing check and raises `ModelConfigurationError(ValueError)`
  (new, in `model_fetcher.py`, on the `CacheConfigurationError` `cache.py:232` /
  `SearchProviderConfigurationError` `pipeline/search_providers/__init__.py:50` pattern —
  `tests/test_app.py:1208-1221` shows the lifespan-refusal test shape). `_MODEL_ID_RE` (`:324`) stays a
  shape check.
- **Feed every consumer** through the resolved id: `acquire_and_load(..., model_id=resolved)` from the
  lifespan; `pipeline/sanitizer_revision.py:40` hashes `f"{resolved}@{resolve_revision(resolved)}"`
  (the module is not in `_REVISION_SOURCES` `:12-21`, so the source edit rotates nothing; the *input*
  changes only when a non-default id is configured); `scripts/vendor_weights.py` defaults `--model-id`
  from the resolver. Update `tests/test_sanitizer_revision.py:51-61` (monkeypatches
  `sanitizer_revision.MODEL_ID` — a name that no longer exists) and `:95-118` (recomputes the digest —
  extend with the 86M id).
- **Assert `id2label` at load, don't assume index 1.** `classifier.py:27-29` says "the older 86M had 3
  classes" — that was Prompt Guard *1*. In `load()`, read `model.config.id2label`: exactly one label
  `INJECTION` and one `BENIGN` (case-insensitive); set `_injection_label_index` from it; otherwise
  `loaded` stays `False`, WARNING `model_labels_unexpected`. `tests/fixtures/tiny_model/config.json`
  already declares `{"0": "BENIGN", "1": "INJECTION"}`, so the real-loader tests keep passing; the
  negative case uses a fake config on the mocked auto-class.
- **`/health`**: `HealthResponse.promptguard_model: str = Field(description=...)` (the shape at
  `retrieval_app.py:393-399`), naming the **loaded** model id when `promptguard_loaded` is true and the
  configured id otherwise (the description says so); stored on `app.state` by the lifespan, never
  re-read in the handler (`_resolved_*` pattern `:295-310`). The contiguity settings are deliberately
  **not** on `/health` (US-002; reconnaissance boundary). Additive → ruling 5: append a `* ``1.3.0```-
  format line (ruling R34; read `pipeline/contract.py:26-68`) to the `CONTRACT_VERSION` docstring, run
  `uv run python -m scripts.export_contract`, refresh the four anchor-quoting pages
  (`tests/test_governance_docs.py:60-66`), and re-create `tests/golden/contract_1_3_0.json` by hand
  from `tests/test_contract_schema.py::_SCHEMA_MODELS` (`:28`; `HealthResponse` is golden-pinned).
  `contract.py` is hashed → **this story rotates** (ruling R32): measure by revert-and-reproduce, record.
  `contract_smoke.py` must not assert the field (a `1.1.0` image lacks it).
- **Fan-out**: `tests/conftest.py::_CLEARED_ENV_VARS` (`:45-54`) + `tests/test_hermeticity.py:129-138`;
  `compose/minimal.yml` (`:75-90`) and `compose/full.yml` (`:50-60`) pass `FORAGE_MODEL_ID` and
  `FORAGE_MODEL_REVISION` through as bare names with the `HF_TOKEN` comment style;
  `tests/test_compose_fragments.py::TestSearchProviderPassthrough` (`:533`) idiom for the test;
  `docs/configuration.md` row (`:108` format), `kit_tools/docs/ENV_REFERENCE.md` row (`:52-53` format)
  and its `:73` cleared-variable sentence; MONITORING/API_GUIDE `/health` tables; `NOTICE:16-18` is
  edited only by US-005 (licence).

**Acceptance Criteria:**
- [ ] `FORAGE_MODEL_ID` unset → `/health.promptguard_model == "meta-llama/Llama-Prompt-Guard-2-22M"`; the
      model-identity hash input is byte-identical to explicitly setting the 22M id (relative
      assertion in `tests/test_sanitizer_revision.py`); the 86M id → a different
      `derive_sanitizer_revision({})` (asserted).
- [ ] A value outside the allowlist: the lifespan raises `ModelConfigurationError`, the log carries
      `model_id_not_allowed`, a sentinel value appears 0 times in captured logs; `derive_sanitizer_revision`
      never raises for any allowlisted id (test).
- [ ] `load()` derives the injection index from `id2label` and refuses `{LABEL_0, LABEL_1}` (test);
      `/health` reports the loaded id when loaded and the configured id otherwise (both tested).
- [ ] The docstring line, regenerate, four-page anchor refresh and golden re-creation are done;
      `uv run python -m scripts.export_contract --check` clean; the rotation (contract.py) measured and
      recorded in `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md` and the
      `kit_tools/docs/GOTCHAS.md` rotation table (`:410-434`).
- [ ] `_CLEARED_ENV_VARS` and the exact-set test include `FORAGE_MODEL_ID`; `grep -c FORAGE_MODEL_ID
      compose/minimal.yml compose/full.yml docs/configuration.md kit_tools/docs/ENV_REFERENCE.md` ≥ 1 each.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-002: Contiguity gating beside the max rule — shipped off, measurable when on

**Priority:** P1

**Description:** As an operator, I want an injection spread across adjacent classifier windows to be
caught even when no single window crosses the 0.85 threshold, so the documented "Prompt Overflow"
evasion of max-pooling is narrowed — without changing what the max rule catches today, and without
turning a rule with an unmeasured false-positive rate on for every deployment.

**Independent Test:** With a mocked classifier whose per-window scores are `[0.6, 0.6]` and config
`promptguard_contiguity_windows: 2`, `promptguard_contiguity_threshold: 0.5`, `run_promptguard` returns
`INJECTION_DETECTED`, `rule == "contiguity"`, both chunks in `flagged_chunks`; `[0.6, 0.2, 0.6]` →
`SAFE`; `[0.86]` → `INJECTION_DETECTED`, `rule == "max_score"`, exactly as before; with the shipped
default (`0` windows) `[0.6, 0.6]` → `SAFE`; `[0.9, 0.6, 0.6]` → both rules, `flagged_chunks` is the
union in document order.

**Implementation Hints:**
- **Per-window scores.** `classify()` already builds `scores` (`classifier.py:183-199`) before pooling
  at `:204-205`. Add `classify_windows(text, *, max_chunks) -> tuple[list[float], list[str]]` (scores
  and chunk texts, same budget check `:179-182`, same `(…, [])` fallback when unloaded) and re-implement
  `classify()` on top of it. The *verdicts and scores* of every pre-existing test stay unchanged, but
  the doubles move: `tests/test_stage3_promptguard.py:32-40` `_make_mock_classifier` (sets
  `mock.classify.return_value`) gains a `classify_windows` side effect, and the four `classifier.classify`
  doubles in `tests/test_orchestrator.py:712` (`assert_not_called`), `:757`, `:1491`, `:1837` are
  updated in the same change. Anchors: `TestClassifierUnit` is `:279-297`, `TestChunking` `:300-348`;
  the boundary tests are `test_score_exactly_at_threshold_is_safe` (`:63`, `TestSafeVerdicts`) and
  `test_score_just_above_threshold` (`:101`, `TestInjectionVerdicts`).
- **The rule lives in stage 3** (`pipeline/stage3_promptguard.py:128-150`, hashed → ruling 6). Verdict
  is `INJECTION_DETECTED` when `max_score > threshold` **or** when any run of at least
  `contiguity_windows` consecutive scores is `>= contiguity_threshold`. `PromptGuardResult` (internal,
  `:35`) gains `rule: Literal["max_score", "contiguity", "both"] | None`; `flagged_chunks` is the
  max-scoring chunks, the contiguous run, or their union in document order (deduplicated); `score`
  stays `max_score`. The orchestrator's omission log at `orchestrator.py:1032` names the rule.
- **Config keys** (`config.yaml` beside `promptguard_threshold` `:15`): `promptguard_contiguity_windows`
  (int, **default `0` = disabled**, range 0–8) and `promptguard_contiguity_threshold` (float, default
  `0.5`, range 0.0–1.0, **absolute and server-side** — it does not track a per-request
  `promptguard_threshold`, and there is no `<=` cross-key constraint; the docs say a caller who lowers
  the max threshold below the contiguity threshold gets a contiguity rule that is the stricter of the
  two, and that this is why the rule ships off). Read through the bounded helpers (`_bounded_int`
  `pipeline/extraction_limits.py:79`, `_bounded_float` `pipeline/search_providers/brave.py:206`),
  registered in `KNOWN_CONFIG_KEYS` (spec 3 US-003). Configuration-only by design: they enter the
  content-cache key through `derive_sanitizer_revision` (append both values to the digest the way
  `promptguard_threshold` is at `pipeline/sanitizer_revision.py:41`; a per-request override would have
  to join `cache_policy_fingerprint()` — pin that with a comment and a test on the fingerprint inputs).
- **All three routes.** Thread both values through the threshold seam spec 3 US-005 creates
  (`_resolved_promptguard_threshold`-style resolver) to `run_promptguard(...)` (`:42-48`) from
  `sanitize_and_structure` (`orchestrator.py:160-207` — serves `/retrieve` `:368` and both `/extract`
  paths `:464`, `:540`) and the `/search` loop (`:969-997`). `/search` inputs are
  `_search_result_promptguard_input(title, url, snippet)` (`:665`, `:1015`) — length-capped to about
  one window, so the rule is **inert on snippets today**; the `/search` end-to-end test is labelled a
  plumbing test in its docstring, and the wiring exists for chunked `content_kind` results.
- **Telemetry.** One additive counter per route section on `/metrics` —
  `retrieve.promptguard_contiguity_detections`, `search.promptguard_contiguity_detections`,
  `extract.promptguard_contiguity_detections` — incremented when `rule` is `contiguity` or `both`
  (window change: docstring line, `tests/test_contract_metrics.py` pins, ruling 5; the response
  models are `extra="forbid"` — GOTCHAS "Adding a `/metrics` counter without adding it to the model
  500s the endpoint"), plus one INFO log at a contiguity verdict naming the run length and scores
  (never the text).
- **Rotation (ruling 6):** stage 3 + orchestrator source bytes + contract.py docstring line + the two
  new hash inputs — one measured, recorded rotation for everyone (the default changes because the
  hash inputs grow, even with the rule off).
- **Residual documented.** A run rule narrows the evasion; fragments separated by one benign ~448-token
  window still pass both rules. `kit_tools/arch/SECURITY.md`'s stage-3 paragraph states the residual
  and the overlap correlation (adjacent windows share `CHUNK_OVERLAP = 64` tokens, `classifier.py:25`),
  `kit_tools/docs/GOTCHAS.md` gains an entry, and the spaced-fragment shape is listed in this spec's
  handoff to `epic-forage-injection-corpus` as a required corpus case.

**Acceptance Criteria:**
- [ ] `classify_windows` exists and `classify` is implemented through it; every pre-existing stage-3
      and orchestrator verdict/score assertion passes with the doubles updated to the new seam.
- [ ] The six verdict cases in the Independent Test plus `[0.2, 0.6, 0.6]` (run at the end fires),
      a score exactly at `contiguity_threshold` counts (`>=`), a score exactly at `promptguard_threshold`
      does not fire the max rule (`>`), and an empty text (`SAFE`) are pinned by tests.
- [ ] Both keys validated at boot (out-of-range refuses like
      `tests/test_app.py::test_lifespan_refuses_an_out_of_range_cache_bound`), defaults `0` / `0.5`,
      documented in `docs/configuration.md` with the enabling recipe and the absolute-threshold note.
- [ ] All three routes apply the rule (one end-to-end test per route through the ASGI app with the
      mocked classifier; the `/search` test docstring says "plumbing — snippets are single-window").
- [ ] The three `/metrics` counters exist, are pinned by `tests/test_contract_metrics.py`, the
      docstring line is appended, regenerate + anchor refresh + golden re-created, `--check` clean.
- [ ] `derive_sanitizer_revision` includes both values; the rotation is measured and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md` and the GOTCHAS rotation table.
- [ ] SECURITY.md stage-3 paragraph names both rules and the spaced-fragment residual; GOTCHAS entry
      present; the corpus-epic handoff line in Implementation Notes.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-003: Benchmark harness — host-side, against the running service

**Priority:** P2

**Description:** As the owner, I want a repeatable script that drives a running Forage container with
fixed synthetic documents and reports service-level latency and container memory, so the 22M-vs-86M
question is answered for the *service*, not a bare interpreter, by a table anyone can regenerate.

**Independent Test:** `uv run pytest tests/test_bench_promptguard.py` passes under the socket guard with
injected fakes (no Docker, no network, no weights) and `uv run python -m scripts.bench_promptguard
--help` exits 0; the percentile test pins p50 = 10 and p95 = 19 for samples `[1..20]`.

**Implementation Hints:**
- **Location and shape**: `scripts/bench_promptguard.py` beside `scripts/export_contract.py` and
  `scripts/vendor_weights.py` — a module with `main(argv)` and injectable seams (`post_fn`, `stats_fn`,
  `clock`) like `vendor_weights.py`'s phase functions. It is **never copied into the image**
  (`.dockerignore:26` excludes `scripts/`; `Dockerfile:158-168`'s enumerated COPY list and
  `tests/test_dockerfile.py:719-740` guard that) — it runs on the host against a container the owner
  started. `scripts/` is type-checked under the strict root (`pyproject.toml`), so no private access
  (`_chunk_text`) and no suppression comments.
- **Arguments**: `--base-url` (default `http://127.0.0.1:8020`), `--container <name>` (for
  `docker stats`), `--runs` (default 20, minimum 5 — below it p95 is `null`), `--json <path>`,
  `--label` (free text copied into the JSON, e.g. `22m-cpus1`).
- **The route**: drive `POST /extract` (multipart, `extract_mode=full`) — `/retrieve` cannot fetch a
  host-local fixture server (the URL validator refuses private and loopback IPs by design). `/extract`
  is release-gated: verify the flag name in `pipeline/extraction_limits.py` / `config.yaml` (`route_enabled`)
  and document that the benchmark container mounts a benchmark config with it enabled
  (`-v "$PWD/bench/config.yaml:/app/config.yaml:ro"`), never a code change.
- **Inputs are deterministic and synthetic**: generated in code from a fixed seed — one plain-text
  document of ≈512 tokens (≈2,000 characters of seeded lorem) and one sized to `/extract`'s
  classification budget (`extraction.max_promptguard_chunks` × ~448 tokens ≈ 64 chunks; report the
  byte size). No fixture files, no third-party text. The unit test asserts the generator is
  deterministic and the sizes are within ±5 %.
- **Measurements**: cold = wall-clock of the first request after container start; warm p50/p95 =
  nearest-rank percentiles (`sorted(samples)[math.ceil(p / 100 * n) - 1]`) over `--runs` further
  requests per input, `time.perf_counter`; container RSS = `docker stats --no-stream --format
  '{{.MemUsage}}' <container>` sampled after the warm-up loop, parsed to MiB (label: "container
  memory after warm-up"); the exit code and `docker inspect --format '{{.State.OOMKilled}}
  {{.State.ExitCode}}' <container>` are recorded by US-004, not the harness.
- **Output**: one JSON object with fixed keys — `label`, `base_url`, `runs`, `cold_ms_512`,
  `warm_p50_ms_512`, `warm_p95_ms_512`, `cold_ms_budget`, `warm_p50_ms_budget`, `warm_p95_ms_budget`,
  `container_mem_mib`, `promptguard_model` and `contract_version` (read from `/health` so a row can
  never be mislabelled).
- Docs: a "Benchmarking the classifier" subsection in `docs/configuration.md` under the sizing table
  (spec 6 US-002 created it) with the exact host-side command sequence; `kit_tools/testing/TESTING_GUIDE.md`
  row for the new module.

**Acceptance Criteria:**
- [ ] `scripts/bench_promptguard.py` accepts the arguments above, reads `promptguard_model` and
      `contract_version` from `/health` into the JSON, and writes the fixed-key JSON.
- [ ] Both inputs are generated from a seed; the unit test asserts determinism and size; percentile
      maths is nearest-rank and pinned (`[1..20]` → 10 / 19; `--runs 4` → p95 `null`).
- [ ] Unit tests cover argument validation, JSON shape, the `docker stats` parser and the percentile
      maths with injected fakes — no Docker, no network, no weights.
- [ ] `.dockerignore`, `Dockerfile` and `tests/test_dockerfile.py` are unchanged (`git diff --stat` on
      the three is empty).
- [ ] `docs/configuration.md` names the host-side command and the benchmark config mount; TESTING_GUIDE
      lists the module.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-005: Vendor the 86M weights — licence, manifest entry, mirror (owner gate)

**Priority:** P1

**Description:** As the owner, I want the 86M weights downloaded, verified and recorded in the
per-model manifest through the same vendoring run the 22M went through, so selecting 86M loads a
digest-verified snapshot and never an unverified one. **Execution halts here for the owner**: the run
needs the gated repo, an HF token, egress and GHCR write access. The implementer records that the gate
has not run (`### US-005 — gate not run, <date>`, naming the prerequisites) and stops.

**Independent Test:** `weights_manifest.json` carries an entry for `meta-llama/Llama-Prompt-Guard-2-86M`
with a 40-hex `revision` and a non-empty `files[]` set; a boot with `FORAGE_MODEL_ID=<86M>` and a warm
volume reaches `weights_verified` (test against the committed entry with a synthetic snapshot);
`NOTICE` names both ids; the vendoring transcript in Implementation Notes contains no token value.

**Implementation Hints:**
- **Licence first (a model-card read; no weights needed).** The 86M model card must carry the same
  Llama licence family as the 22M (`NOTICE:14-20`). Expected outcome: it does. If it differs: stop,
  record, and **revert the opt-in** in the same PR — remove the 86M id from `ALLOWED_MODEL_IDS`, the
  docs rows and the compose comments (US-006) — no NOTICE edit, no vendoring.
- **Credential handling** (ruling R33): `umask 077; f="$(mktemp)"; trap 'rm -f "$f"' EXIT; printf
  'HF_TOKEN='; read -rs T; echo; printf 'HF_TOKEN=%s\n' "$T" > "$f"; unset T` — mode 0600, deleted on
  exit, referenced only as `--env-file "$f"` / sourced by the vendoring shell; never on a command line,
  never in this file. Record the file mode and deletion, never the path's contents.
- **The run** is `docs/weights.md` § "Vendoring: the procedure" (`:75-147`) with `--model-id
  meta-llama/Llama-Prompt-Guard-2-86M --revision <sha from the model card>`: download (allow-patterns
  only — a file outside `ALLOWED_SUFFIXES` is refused; then stop and record, do not widen the set),
  hash, `generate_manifest` for the 86M entry, push to `ghcr.io/washingbearlabs/forage-weights:<86M
  revision>` (revision-keyed tag; the 22M tag is untouched), and the "commit all together" step
  (`:137-147`) — the manifest entry and `docs/weights.md`'s pin sentences in one commit.
  `DEFAULT_MODEL_REVISION` stays the 22M pin.
- **Failure paths, recorded not improvised**: HF 403 (gated access not granted) → stop, record
  `access pending`, US-004's 86M rows read `not measured — access pending`; 429 or a partial download →
  clear the partial snapshot from the working cache and re-run, recording the retry.
- **Record** in `### US-005 — 86M vendored, <date>`: licence outcome, revision, file count and total
  bytes, the manifest diff summary, the mirror tag, the verification transcript (`weights_verified`),
  and `NOTICE`'s edit (both ids named, "Built with Llama" line unchanged).

**Acceptance Criteria:**
- [ ] If the gate has not run: Implementation Notes carry `### US-005 — gate not run, <date>` naming
      the missing prerequisites, and nothing else in the tree changes (the verifier accepts this state).
- [ ] The 86M licence check is recorded with its outcome; on mismatch the allowlist and docs are
      reverted in the same PR and the remaining criteria are void.
- [ ] `weights_manifest.json` carries the 86M entry (revision + files with `path`/`sha256`/`size`);
      `ALLOWED_SUFFIXES` unchanged; a test with a synthetic snapshot matching the committed entry
      reaches `weights_verified` under `FORAGE_MODEL_ID=<86M>`.
- [ ] The mirror holds `forage-weights:<86M revision>`; `docs/weights.md` states both pins and that
      the mirror tag is the revision.
- [ ] `NOTICE` names both model ids; the token file was mode 0600 and is recorded as deleted;
      `grep -nE 'hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}' kit_tools/specs/feature-hardening-promptguard-86m.md docs/weights.md NOTICE`
      returns nothing.

### US-004: Run the benchmark on the reference container and record it (owner gate)

**Priority:** P2

**Description:** As the owner, I want the 22M and 86M numbers measured on the actual running container
at `FORAGE_CPUS=1` and `4`, recorded here and in the sizing table, with the opt-in recipe and its
resilience caveat documented — so the corpus epic can measure catch rate against a model that is known
to fit. **Execution halts here for the owner** (ruling 18): the run needs the vendored 86M (US-005),
an HF token, Docker and the reference envelope. The implementer records that the gate has not run and
stops.

**Independent Test:** This file's Implementation Notes carry `### US-004 — benchmark, <date>` with one
row per run (4 required: {22M, 86M} × {1, 4} CPUs; 2 optional at 2 CPUs), each row's
`promptguard_model` read from `/health`, plus the container memory column and the OOM/exit-code check;
`docs/configuration.md`'s sizing table has the classifier column filled for 1 and 4 vCPU (the 2 vCPU
row reads `not measured` unless run); the recorded commands reference `--env-file` only.

**Implementation Hints:**
- **Credential handling**: the recipe in US-005 (`umask 077`, `mktemp`, `trap`), `--env-file "$f"`.
- **The container per run** (named, no `--rm`, so it can be inspected): `docker run -d --name
  bench-<model>-<cpus> --cpus "$cpus" --memory "${FORAGE_MEM_LIMIT:-1024m}" --env-file "$f"
  -e FORAGE_MODEL_ID=<id> -e FORAGE_CPUS=$cpus -v forage-model-cache:/app/model-cache -v
  "$PWD/bench/config.yaml:/app/config.yaml:ro" -p 127.0.0.1:8020:8020 forage:bench`, built with
  `docker build -t forage:bench .` (no build args). Wait for `/health` `promptguard_loaded: true`
  (the first 86M start downloads through the verified path — record the cold-start time separately),
  then `uv run python -m scripts.bench_promptguard --container bench-<model>-<cpus> --label
  <model>-cpus<cpus> --json bench/<model>-<cpus>.json`, then `docker inspect --format
  '{{.State.OOMKilled}} {{.State.ExitCode}}' bench-<model>-<cpus>`, then `docker rm -f`.
- **The table** (columns fixed; copy the JSON keys): `label | promptguard_model (from /health) | cpus |
  cold 512-tok (ms) | warm p50/p95 512-tok (ms) | cold budget (ms) | warm p50/p95 budget (ms) |
  container memory after warm-up (MiB) | OOMKilled | exit code`. Four required rows. A separate line
  records the measured size the 86M set adds to the `forage-model-cache` volume (`docker run --rm -v
  forage-model-cache:/c alpine du -sm /c`).
- **Optional FPR smoke** (cheap, the container is running): enable the contiguity rule via the mounted
  benchmark config (`promptguard_contiguity_windows: 2`), post the harness's long benign document
  20 times to each model and record how many verdicts were `injection_detected` — a number for the
  corpus epic, not a gate.
- **Fill the sizing table** (`docs/configuration.md`, spec 6 US-002): classifier column for the
  1 vCPU / 1 GB and 4 vCPU rows from the 22M numbers with the 86M numbers as a second line per row; the
  2 vCPU row reads `not measured` unless the optional runs were done. State the opt-in recipe once
  (`FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M`) with its caveat: **86M is served from Hugging
  Face and the `forage-weights` mirror at its own revision tag; a deployment that selects it needs the
  same acquisition posture as the 22M** (`docs/configuration.md` and `docs/weights.md`, one sentence
  each).
- **Do not flip the default** (owner decision 4). If 86M container memory exceeds 900 MiB at 1 GB or
  warm p50 at 1 CPU exceeds 2× the 22M figure, say so and leave the open question open.

**Acceptance Criteria:**
- [ ] If the gate has not run: Implementation Notes carry `### US-004 — gate not run, <date>` naming
      the missing prerequisites (the vendored 86M, HF token, Docker, the reference envelope).
- [ ] Implementation Notes carry the populated table (four required rows, `promptguard_model` from
      `/health` per row), the OOM/exit-code line per run, the volume-size line, and every command as
      run with `--env-file "$f"` and no token value.
- [ ] `docs/configuration.md` sizing table's classifier column is filled for 1 and 4 vCPU with both
      models (2 vCPU marked `not measured` or filled); the opt-in recipe and its acquisition caveat
      appear once each (`grep -c 'FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M' docs/configuration.md` ≥ 1).
- [ ] The optional FPR smoke, if run, is recorded with its counts and the config used.
- [ ] `grep -nE 'hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}'` over this file, `docs/configuration.md`
      and `docs/weights.md` returns nothing; the token file is recorded as mode 0600 and deleted.

## Edge Cases

- `FORAGE_MODEL_ID` set to the 22M id explicitly — identical to unset, including the hash input (US-006).
- `FORAGE_MODEL_ID` with surrounding whitespace — stripped, then allowlisted (US-006).
- 86M selected before US-005 vendored it — `manifest_model_unknown`, service degraded
  (`promptguard_unavailable`), `/health.promptguard_model` names the configured id (US-001, US-006).
- 86M selected with a warm 22M snapshot on the volume — never loaded under the 86M id
  (`model_identity_mismatch`) (US-001).
- A loaded model whose `id2label` is `{0: "LABEL_0", 1: "LABEL_1"}` — refused, `loaded` stays `False`,
  closed reason logged (US-006).
- `contiguity_windows` larger than the chunk count — the rule cannot fire; the max rule still can (US-002).
- A per-request `promptguard_threshold` below `promptguard_contiguity_threshold` — the contiguity rule is
  the stricter one; documented, and the rule is off by default (US-002).
- Both rules fire on `[0.9, 0.6, 0.6]` — `rule == "both"`, all three chunks flagged in document order (US-002).
- An empty text — zero windows, `SAFE`, as today (US-002).
- `--runs` below 5 — p95 reported as `null` (US-003).
- The 86M repo carries a file outside `ALLOWED_SUFFIXES` — vendoring refuses; stop and record (US-005).
- HF returns 403 for the 86M repo — recorded as `access pending`; the 22M rows still land (US-005, US-004).
- 86M OOM-killed at 1 GB — recorded as a row with `OOMKilled: true`, exit code 137; the default stays
  22M (US-004).

## Out of Scope

- Flipping the default to 86M (owner decision 4; revisited after `epic-forage-injection-corpus`).
- Turning contiguity gating on by default (ruling R17; the corpus epic decides after measuring FPR).
- int8 / ONNX Runtime builds of either model (new dependency; open question only).
- Widening `ALLOWED_SUFFIXES`, `ALLOW_PATTERNS` or dropping `use_safetensors=True` — pinned unchanged.
- Datamarking / spotlighting of retrieved text (Poppy family Epic 5).
- Any model outside Prompt Guard 2 (the 2026-08-30 verdict stands; the allowlist is the mechanism).
- The corpus that measures catch rate and false-positive rate (`epic-forage-injection-corpus`), which
  receives the spaced-fragment case from US-002 as a required vector.
- Reporting the contiguity settings on `/health` (deliberate reconnaissance boundary; US-006 records it).

## Assumptions

- Prompt Guard 2 86M is a two-class DeBERTa sequence classifier like the 22M; the `id2label` check in
  US-006 turns this into a load-time fact.
- The 86M weights live in the same gated Hugging Face org under the same licence family; US-005 verifies
  before vendoring and reverts the opt-in otherwise.
- Poppy is the only consumer; `promptguard_model` and the three counters are additive, so a 1.2.0
  client still validates.
- Docker Desktop (macOS) or Docker Engine (Linux) is where the owner gates run; `docker stats` is available.
- Two models share one `forage-model-cache` volume and one mirror repository, distinguished by revision.

## Technical Considerations

- **Rotation ledger (rulings 6, R32):** US-001 — none (no `_REVISION_SOURCES` file; the hash input is
  unchanged at the default); US-006 — `contract.py` (docstring line) → rotates for everyone, plus the
  hash *input* changes only when a non-default id is configured; US-002 — `stage3_promptguard.py`,
  `orchestrator.py`, `contract.py` and two new hash inputs → one rotation for everyone; US-003, US-004,
  US-005 — none (the manifest and scripts are not hashed).
- **Contract window (ruling 5):** `promptguard_model` (US-006) and the three counters (US-002) move
  the document; each story appends its `* ``1.3.0``` line, regenerates, refreshes the four anchor
  pages and re-creates the golden by hand from `_SCHEMA_MODELS`; spec 8 US-002 freezes it.
- **Memory:** the reference envelope is `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` (spec 6); 86M fp32 at
  up to ~560 MB plus the 512 MiB parent reservation (`compose/minimal.yml:105-109`) is what US-004
  measures at the service level rather than assumes.
- **Threads:** `promptguard_threads` (spec 6 US-001, default `0` = torch default) — the benchmark
  records `FORAGE_CPUS` only; thread tuning is a spec 6 concern.
- **Hermeticity:** every new test runs under the `pytest-socket` guard with injected fakes; the harness
  and the vendoring run are only ever executed for real by the owner.
- **Supply chain:** `verify_weights()` is the single verification entry point for every acquisition
  leg; this spec never adds a path around it.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md) (stage 3, `promptguard/`, `model_fetcher.py`)
- Security: [SECURITY.md](../arch/SECURITY.md) (stage-3 paragraph, non-vulnerabilities table)
- Weights: [`docs/weights.md`](../../docs/weights.md), [`docs/configuration.md`](../../docs/configuration.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("PromptGuard model absent → degraded", the rotation table)
- Research: Poppy `kit_tools/specs/WEB_ACCESS_FAMILY.md` § "PromptGuard research (2026-08-30)"

## Implementation Notes

<!-- Populated during execution. US-001 records the unchanged default revision value; US-006 and
US-002 record their rotations; US-005 records the licence check, the vendoring transcript and the
manifest entry; US-004 records the benchmark table and the sizing-table fill. -->

## Refinement Notes

### Research Findings

**Decision:** The model id is a parameter of the acquisition/loading path, not a constant three modules
import.
**Rationale:** `load()` passes the module constant to both `from_pretrained` calls
(`classifier.py:84-85,98-99`) and the acquisition functions are id-free (`model_fetcher.py:1474-1665`);
rewiring only the importers would fetch one model and load another (validation round 1, completionist,
codebase-fit and security reviewers, independently).
**Alternatives considered:** Importing the resolver into the classifier — rejected, `model_fetcher.py:139`
imports the classifier at module scope (cycle).
**Source:** `promptguard/classifier.py:23,50-56,84-99`; `model_fetcher.py:139,1060-1077,1474-1665`.

**Decision:** The manifest is a per-model exact-set allowlist; verification is never skipped.
**Rationale:** `weights_manifest.json` carries per-file `sha256` + `size`, generated by
`scripts/vendor_weights.py` (`:446`), and `verify_weights()` is the single gate; a revision read off a
model card cannot produce the digests (validation round 1).
**Alternatives considered:** "Verified if known, unverified if new" — rejected as a supply-chain
fail-open on the service's only ML control.
**Source:** `weights_manifest.json` header; `model_fetcher.py:566-630,764-830,924-934,1617`.

**Decision:** Contiguity gating is a second rule in stage 3 over per-window scores, shipped off.
**Rationale:** The pooling happens inside `classify()` (`:204-205`); the scores list already exists
(`:183-199`). Its false-positive rate is unmeasured and adjacent windows overlap by 64 tokens, so the
rule is opt-in until the corpus epic measures it (ruling R17).
**Alternatives considered:** Lowering the single threshold (the research's stated failure mode);
ensembling a second model ("don't ensemble"); a k-windows-anywhere rule — recorded as a corpus-epic
candidate, not built here.
**Source:** `pipeline/stage3_promptguard.py:128-150`; `promptguard/classifier.py:25,138-148`.

**Decision:** The benchmark drives the running service from the host.
**Rationale:** A bare interpreter measures the classifier, not the 1 GiB service (`config.yaml:39-41`
reserves ≥512 MiB for parent + PromptGuard); `scripts/` is excluded from the image by design
(`.dockerignore:26`, `Dockerfile:158-168`); `/retrieve` cannot fetch a loopback fixture, so `/extract`
is the route (ruling R18).
**Source:** `.dockerignore:26`; `tests/test_dockerfile.py:719-740`; `url_validator.py:73-102`.

### Scope Adjustments

- Validation round 1 (2026-09-19): US-001 split into US-001 (mechanical id threading + per-model manifest,
  no env var) and US-006 (`FORAGE_MODEL_ID`, refusal, `id2label`, `/health`); US-005 (vendoring gate)
  added before US-004; the benchmark moved host-side; contiguity gating default changed from `2`
  windows to `0`; the `<= promptguard_threshold` cross-key constraint dropped (absolute, server-side).
- The outline's "verify whether `sanitizer_revision.py` is hashed" resolved to *not hashed*; the
  earlier "US-001 rotates nothing" claim was wrong because the `/health` line edits `contract.py` —
  that line now lives in US-006, which records its rotation.

### Decisions Made

- Owner decision 4 (22M default, allowlisted opt-in); rulings R17, R18, R29, R31, R32, R33 as stated in
  the epic wrapper's validation-round-1 addendum.
- Overruled: "split US-001 three ways" (story-quality) — two ways suffices once the manifest and the
  loader share one concern (identity threading); the `id2label` assertion rides with the env-var story
  because it is the load-time half of "the configured model is the loaded model".
- Overruled: "report the contiguity settings on `/health`" (salty) — deliberately not exposed
  (reconnaissance boundary; security info finding); the rotation and the counters are the signal.
- Overruled: "add `k` windows anywhere" (security) — handed to the corpus epic as a candidate; this spec
  documents the residual instead of widening a rule whose FPR is unmeasured.
- Overruled: "run the licence check in US-001" (salty) — it stays at the vendoring gate, where the
  outcome can revert the opt-in in the same PR; the docs describe 86M as "pending vendoring" until then.
- The 2 vCPU sizing row is `not measured` unless the owner runs the optional pair (completionist info).

## Clarifications

### Session 2026-09-19
- Q: How should the 86M upgrade land? → A: Configurable model, 22M stays default; the benchmark
  publishes numbers on the reference envelope; 86M is opt-in until the corpus epic measures it.
- Q: Does the contiguity rule replace the max rule? → A: No — beside it, both tunable, `windows=0`
  disables the new rule (ruling 17).

### Session 2026-09-19 (validation round 1)
- Rulings applied: R17 (contiguity off by default), R18 (host-side service benchmark), R29 (id reaches
  the loader; manifest per model; verification never skipped; US-005 vendoring gate; `/health` reports
  the loaded id), R31 (US-001/US-006 split, execution order), R32 (honest rotation ledger), R33 (no
  secret-bearing capture; `umask 077` + `mktemp` + `trap` recipe), R34 (docstring line format).
- Q: Is `promptguard_contiguity_threshold` relative to a per-request threshold? → A: No — absolute and
  server-side; the cross-key constraint is dropped.
- Q: Where does the refuse-boot live? → A: In the lifespan (`ModelConfigurationError`); the resolver is
  total so the per-request `derive_sanitizer_revision` fallbacks can never raise.

## Open Questions

- [ ] If US-004 shows 86M does not fit 1 vCPU / 1 GB as a service, is an int8/ONNX build worth a new
      dependency? Non-blocking; only asked if the numbers force it.
- [ ] Whether `promptguard_model` should also appear in the `v1.2.0` handoff table's model-posture row as
      "loaded id" vs "configured id" wording — spec 8 US-003 decides; non-blocking.
