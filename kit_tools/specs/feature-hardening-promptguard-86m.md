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
execution_order: [US-001, US-006, US-002, US-007, US-003, US-005, US-004]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: PromptGuard 86M Opt-In + Contiguity Gating + Benchmark

> **Spec 7 of `epic-forage-hardening`.** Make the classifier *selectable* (`FORAGE_MODEL_ID`, a closed
> allowlist, 22M stays the default — owner decision 4), thread the selected id **and its revision**
> through every consumer including the classifier's own `from_pretrained` calls and a per-model weights
> manifest (ruling R29 as corrected in round 2), add **contiguity gating** beside the existing max-score
> rule — shipped **off** until the corpus epic measures it (ruling R17) — as a behaviour-preserving
> classifier refactor (US-002) plus the stage-3 rule (US-007, ruling R37), ship a host-side **benchmark
> harness** that measures the running service (ruling R18), then two owner gates in order: **US-005
> vendors the 86M weights** (and only then adds the 86M to the allowlist) and **US-004 runs the
> benchmark** on the reference container. Binding: owner decision 4; rulings 5, 6, R17, R18, R29,
> R32, R33, R34, R36, R37, R39, R40. Context: Poppy's `WEB_ACCESS_FAMILY.md` § "PromptGuard research
> (2026-08-30)". Validation rounds 1 and 2 (2026-09-19) applied — see Clarifications.
>
> **Assumed landed** (upstream seams this spec consumes by name): spec 3 US-003's `KNOWN_CONFIG_KEYS`
> registry, spec 3 US-005's per-route threshold resolver, spec 6 US-002's sizing table in
> `docs/configuration.md`, and spec 1 US-004's `_EXPECTED_ONE_THREE_ZERO_DIFF` set in
> `tests/test_contract_schema.py` (ruling R36). If any has landed with a different shape, the
> implementer matches the shape that exists and records the difference in Implementation Notes.

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
`_acquire_and_load()` refuses with `weights_pin_unusable` (`:1617`) when it blesses nothing. Two
things that matter for this spec are split today: `resolve_revision()` (`:882-902`) reads only
`FORAGE_MODEL_REVISION` and the module constant `DEFAULT_MODEL_REVISION` (`:174`) — it opens no file —
while `verify_weights` hashes the directory at the **manifest's** revision (`:795`) and
`_load_verified` (`:1474`) loads at the **resolved** one, with nothing comparing the two. `classify()`
(`:154-207`) folds its per-window `scores` (`:183-199`) into `max_score` (`:204-205`) and stage 3
decides on `score > threshold` alone (`pipeline/stage3_promptguard.py:136`).

Seven stories, in execution order `US-001 → US-006 → US-002 → US-007 → US-003 → US-005 → US-004`:

- **US-001** — the model id **and revision** become parameters of the whole acquisition and loading
  path, the manifest becomes a per-model allowlist, and the verifier and the loader are made to agree
  on one directory — with **no new environment variable and no behaviour change at the default**
  (rulings R29, R37).
- **US-006** — `FORAGE_MODEL_ID` on top: allowlisted resolver (22M only until US-005 vendors the 86M),
  refuse-boot in the lifespan, the `id2label` assertion, `/health.promptguard_model` (inside the 1.3.0
  window — this line rotates, ruling R32), the configured id in the `sanitizer_revision` hash.
- **US-002** — per-window scores on the classifier (`classify_windows`) as a behaviour-preserving
  refactor; not hashed; the test doubles migrate here.
- **US-007** — the contiguity rule in stage 3 on all three routes, `promptguard_contiguity_windows`
  default `0` (disabled; ruling R17), one counter per `/metrics` section, both residuals documented
  (the evasion that survives the rule and the adversarial trip of it). Rotates.
- **US-003** — `scripts/bench_promptguard.py`, run **on the host** against a running container,
  measuring service-level latency and container RSS (ruling R18), reusing `contract_smoke.py`'s
  driver pieces; hermetic tests; the committed `bench/config.yaml`.
- **US-005 (owner gate)** — vendor the 86M weights: licence check, the vendoring run with every
  credential in one 0600 env file, the generated per-model manifest entry, the mirror tag, **and the
  allowlist entry** — committed together; nothing unverified ever loads.
- **US-004 (owner gate)** — the benchmark matrix on the reference container, the sizing-table column,
  the opt-in recipe and its caveats. Execution halts at US-005 if the gates have not run.

## Goals

- With `FORAGE_MODEL_ID` unset or set to the 22M id, the model-identity hash input
  `f"{model_id}@{revision}"` is byte-identical to today's (pinned relatively by
  `tests/test_sanitizer_revision.py::test_the_hashed_model_identity_is_model_id_at_revision`, `:95-118`,
  extended with the id); a second allowlisted id produces a different `sanitizer_revision`; any value
  outside the allowlist refuses boot with a closed reason and the value is echoed 0 times.
- With `FORAGE_MODEL_ID=<a second allowlisted id>` and a stubbed auto-class, the lifespan passes the
  resolved id to `acquire_and_load` and **both** `from_pretrained` calls receive it (asserted on the
  mock through the ASGI lifespan, not only through the parameter); a warm snapshot of a different
  model, or of the same model at a different revision, on the same volume is never loaded.
- A model id with no manifest entry refuses to acquire (closed reason `manifest_model_unknown`) even
  when `FORAGE_MODEL_REVISION` is set; a `FORAGE_MODEL_REVISION` that is not the selected model's
  manifest revision refuses to verify (closed reason `weights_revision_unpinned`); `ALLOWED_SUFFIXES`,
  `ALLOW_PATTERNS` and `use_safetensors=True` are byte-unchanged by this spec.
- With `promptguard_contiguity_windows: 2` and `promptguard_contiguity_threshold: 0.5`, per-window
  scores `[0.6, 0.6]` are `INJECTION_DETECTED` with both chunks flagged and `rule == "contiguity"`;
  `[0.6, 0.2, 0.6]` is `SAFE`; `[0.86]` fires the max rule exactly as before; at the shipped default
  (`0` windows) `[0.6, 0.6]` is `SAFE` and every pre-existing stage-3 verdict and score is unchanged; a
  maximum-length `/search` result spans at least two classifier windows, so the rule **can** fire on
  `/search` when enabled (pinned by a test).
- `uv run python -m scripts.bench_promptguard --help` exits 0 with no weights, no Docker and no
  network; its unit tests run under the socket guard with injected fakes; p50/p95 are nearest-rank
  (`[1..20]` → `10`, `19`); every failure path exits non-zero with a closed message and writes no
  partial JSON.
- Either the 86M manifest entry, the mirror tag and the allowlist entry are committed by the vendoring
  gate and the benchmark table is recorded here and in `docs/configuration.md`'s sizing table — or each
  gate is recorded as not run, naming its prerequisites (US-005, US-004), which is the sanctioned
  autonomous terminal state.

## User Stories

### US-001: The model id and revision are parameters — acquisition, loading, and a per-model manifest

**Priority:** P1

**Description:** As a maintainer, I want the model identity and its revision to flow as parameters
through the weights fetcher, the verifier, the classifier loader and the vendoring script, with the
manifest holding one exact-set allowlist per model, and with the verifier and the loader agreeing on
one directory — so a second model can be selected later without any path silently falling back to the
22M or loading a directory that was never hashed, and so nothing changes for today's deployments.

**Independent Test:** With the default id everywhere, `uv run pytest tests/test_model_fetcher.py
tests/test_vendor_weights.py tests/test_sanitizer_revision.py tests/test_app.py` passes and
`derive_sanitizer_revision({})` equals the pre-story value (recorded in Implementation Notes); with a
fake `SupportsWeightLoad` double, `acquire_and_load(..., model_id="acme/other")` calls the double's
`load(model_id="acme/other", revision=<that model's manifest revision>, ...)` and `verify_weights`
selects the `acme/other` manifest entry; a manifest with no entry for the requested id yields
`manifest_model_unknown` and nothing is loaded; with two snapshot directories planted under one repo
dir (via `tests/fakes.py::materialize_hub_snapshot`, `:47-81`) and `FORAGE_MODEL_REVISION` naming the
unpinned one, verification refuses with `weights_revision_unpinned` and the unpinned directory is
never loaded; `resolve_revision("acme/unvendored")` returns the literal `unpinned` and
`derive_sanitizer_revision({})` still returns a value.

**Implementation Hints:**
- **`DEFAULT_MODEL_ID`.** Rename `MODEL_ID` (`promptguard/classifier.py:23`) to `DEFAULT_MODEL_ID` and
  keep `MODEL_ID = DEFAULT_MODEL_ID` as a deprecated alias for one story so the importers
  (`model_fetcher.py:139`, `pipeline/sanitizer_revision.py:10`, `scripts/vendor_weights.py:107`,
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
- **The verifier and the loader agree on one directory (security, round 1).** `verify_weights(cache_root,
  *, manifest_path, metrics, model_id, revision)` verifies the snapshot at the **requested**
  `(model_id, revision)` pair and refuses with the new closed reason `weights_revision_unpinned` when
  `revision` is not that model's manifest entry revision — it never verifies one directory and hands
  another to `_load_verified`. `_load_verified` receives the verified snapshot path and asserts
  `loaded_path == verified_path` before `from_pretrained`. `_walk_snapshot` (`:653`) is unchanged.
- **Loaded-identity assertion, outside the blanket `except`.** `load()` (`classifier.py:63-116`) wraps
  its body in one `except Exception` that logs the generic "PromptGuard model not available". The
  identity check and US-006's label check must be **explicit early-return blocks after
  `from_pretrained` succeeds** (`if not ok: logger.warning("model_identity_mismatch"); return False`),
  placed so the broad `except` cannot relabel them; each has a test asserting the *specific* log
  message, not just `loaded is False`. The check: the resolved snapshot directory lies under
  `repo_dirname(model_id)` at `revision` (`model_fetcher.py:479-481` — the classifier has `cache_dir`
  and `revision`, so it computes `Path(cache_dir) / HUB_DIRNAME / repo_dirname(model_id) / "snapshots" /
  revision` with a private two-line copy of the helper, or `model.config._name_or_path` names the id).
  Pin the dangerous case with `tests/fakes.py::materialize_hub_snapshot(cache_root, files, *, model_id,
  revision, symlinks)` (`:47-81`): a warm `acme/tiny-guard` snapshot with `model_id="acme/other"`
  requested → not loaded, `model_identity_mismatch` logged, no path in the record.
- **Per-model manifest.** `weights_manifest.json` becomes `{"_comment": [...], "models": {"<model
  id>": {"revision": ..., "files": [...]}}}` — the 22M entry's `revision` and `files` are today's
  content unchanged. `_load_manifest()` (`:566-630`) validates the map and returns the entry for a
  requested id (`read_manifest_pin(path, model_id=...)` `:924`); a document with no `models` map, an
  entry with no `files`, or a file entry missing `path`/`sha256`/`size` keeps today's `manifest_invalid`
  / `manifest_empty` reasons; a requested id with no entry is the new closed reason
  `manifest_model_unknown`. `verify_weights` selects that entry and **never falls back to another
  model's entry**; `_acquire_and_load` keeps `weights_pin_unusable` (`:1617`) for an entry with an
  empty file set. `mirror_reference(repository, revision)` (`:1014`) stays revision-keyed — two models
  coexist in one mirror repository because their revisions differ; `docs/weights.md` § "The three
  places the revision appears" (`:27`) says so, **per model**.
- **The shared builders move with the format** (codebase-fit, round 1): `tests/fakes.py::
  weights_manifest_document(files, *, model_id, revision)` (`:157-171`) is the one helper both suites
  build manifests through — callers at `tests/test_model_fetcher.py:175` and `tests/test_app.py:881`
  (`tests/test_app.py:878-892` writes a manifest for the lifespan/acquisition tests, a third affected
  file). It gains the map shape (and accepts several models). The manifest-shaped assertions at
  `tests/test_vendor_weights.py:121-129,269,1366-1383` and `tests/test_model_fetcher.py:196,307,1139,
  1329-1333` (~40 sites) move with it; record the starting `grep -c` for `"revision"` in those files
  (ruling R40).
- **Revision resolution is per model and total.** `resolve_revision(model_id: str) -> str`: the
  `FORAGE_MODEL_REVISION` override if set (validated later by `verify_weights` against the pin — a
  mismatch is `weights_revision_unpinned`, never a silent fallback), else the manifest entry's
  revision for that model, else the literal closed token **`unpinned`** (a model with no entry). The
  manifest is read **once** and memoised (the lifespan reads it; `derive_sanitizer_revision` runs per
  request through the `retrieval_app.py:292,1421,1728` fallbacks, so no manifest I/O may sit on that
  path — cache the pin by `(manifest_path, model_id)` at module level and state it). `unpinned` never
  reaches a path: `_acquire_and_load` refuses `manifest_model_unknown` before any snapshot or hub call,
  and the hash input `f"{model_id}@unpinned"` is deterministic and honest. `DEFAULT_MODEL_REVISION`
  (`:174`) stays the 22M constant **and** a test asserts it equals the 22M manifest entry's revision
  — the replacement for `tests/test_model_fetcher.py::TestRevisionPin` (`:1281`, `:1332-1333`) and
  `tests/test_vendor_weights.py::TestSingleSourceOfTruth`; `kit_tools/docs/ENV_REFERENCE.md:52`'s
  `FORAGE_MODEL_REVISION` row reads "the selected model's committed pin; a value that is not that pin
  refuses to verify (`weights_revision_unpinned`)".
- **The generator writes the map — and diffs it.** `scripts/vendor_weights.py` gains `--model-id`
  (default `DEFAULT_MODEL_ID` — the supply-chain tool does **not** read `FORAGE_MODEL_ID`; an explicit
  flag always wins), `generate_manifest()` (`:446`) writes or replaces that model's entry and keeps the
  others byte-stable; `read_manifest_document` (`:486`), `manifest_diff` (`:501`, run at `:981` in
  the manifest phase — the supply-chain review step whose output US-005 records) and `_files_by_path`
  (`:546`) are taught the keyed shape: the diff is **scoped to the `--model-id` entry** and reports
  another model's entry only as "untouched"; a diff of a one-model manifest against a two-model
  manifest reports only the added model (test). `tests/test_vendor_weights.py:559-616` (seven diff
  tests) move with it; a round-trip test asserts generated output parses back through `_load_manifest`.
- **Supply-chain closure unchanged (non-goal, pinned).** `ALLOWED_SUFFIXES` (`model_fetcher.py:249`),
  `ALLOW_PATTERNS` (`:255`) and the loader's `use_safetensors=True` are not edited; a test asserts the
  two constants' values byte-for-byte. A model whose repo carries files outside the suffix set is
  *refused* at vendoring (US-005), never accommodated.
- Docs: `docs/weights.md` (§ "What the artifact is", § "Vendoring: the procedure" — the `--model-id`
  argument, § "The three places the revision appears" now per model, `:92-98`'s credential block
  rewritten by US-005); `kit_tools/docs/ENV_REFERENCE.md:52`; `kit_tools/arch/CODE_ARCH.md`
  `model_fetcher.py` row.

**Acceptance Criteria:**
- [ ] `load(*, model_id=...)` exists on `PromptGuardClassifier` and on `SupportsWeightLoad`; a test with
      a stubbed auto-class asserts both `from_pretrained` calls receive the requested id
      (`tests/test_model_fetcher.py:790-791` extended); `acquire_and_load(..., model_id=...)` threads
      the id and the resolved revision through `_verify_cached`, `_download_from_hub`, `_try_source`
      and `_load_verified` (asserted on a recording double).
- [ ] `verify_weights(..., model_id=, revision=)` verifies the requested pair; a `FORAGE_MODEL_REVISION`
      that is not the pin yields `weights_revision_unpinned` (closed reason, no path in the log) and
      `_load_verified` asserts the loaded path equals the verified path — the two-directory test from
      the Independent Test passes and the unpinned directory's files are never opened.
- [ ] The loaded-identity check is an explicit early-return block outside the blanket `except`; a warm
      snapshot of a different model under the requested id yields `model_identity_mismatch`,
      `loaded is False`, and a test asserts that specific message.
- [ ] `weights_manifest.json` is a per-model map; the 22M entry's `revision` and `files` are
      byte-identical to today's; `_load_manifest` / `read_manifest_pin(model_id=...)` /
      `verify_weights(..., model_id=...)` select the entry; a missing entry yields `manifest_model_unknown`
      even with `FORAGE_MODEL_REVISION` set (test); `manifest_invalid` / `manifest_empty` semantics
      unchanged (tests); `tests/fakes.py::weights_manifest_document` builds the map and
      `tests/test_app.py:878-892` passes against it.
- [ ] `resolve_revision(model_id)` is total (override → manifest pin → `unpinned`), memoises the pin,
      and a test asserts `DEFAULT_MODEL_REVISION == <the 22M manifest entry's revision>`;
      `derive_sanitizer_revision({})` never raises for any id and performs no file I/O per call (a
      test counts `open` calls across two invocations).
- [ ] `scripts/vendor_weights.py --model-id` writes the map form, preserves other entries, and
      `manifest_diff` scoped to that entry reports only the added model for a one-model → two-model
      diff (test); the seven diff tests at `tests/test_vendor_weights.py:559-616` pass against the
      keyed shape; a round-trip test parses the output with `_load_manifest`.
- [ ] `ALLOWED_SUFFIXES == frozenset({".safetensors", ".json", ".txt", ".model"})` and `ALLOW_PATTERNS`
      are asserted unchanged; `grep -n 'use_safetensors=True' promptguard/classifier.py` returns 1 hit.
- [ ] `grep -rn '\bMODEL_ID\b' --include='*.py' .` returns only the `DEFAULT_MODEL_ID` definition line
      (alias removed); `derive_sanitizer_revision({})` equals the pre-story value (recorded in
      Implementation Notes; `git diff --stat` against the eight `_REVISION_SOURCES` files is empty).
- [ ] `docs/weights.md` and `ENV_REFERENCE.md:52` describe the per-model manifest, the per-model pin
      and the `weights_revision_unpinned` refusal (`grep -c 'per model' docs/weights.md` ≥ 1,
      `grep -c weights_revision_unpinned docs/weights.md kit_tools/docs/ENV_REFERENCE.md` ≥ 1 each).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-006: `FORAGE_MODEL_ID` — allowlisted, refuse-boot, asserted at load, reported on `/health`

**Priority:** P1

**Description:** As an operator, I want to select the classifier with one environment variable and
see which model the service is configured with on `/health`, so the 86M upgrade is an opt-in I can
measure, not a fork of the image — while a typo or an unknown model can never be loaded, and while an
allowlist entry that nothing can serve is never shipped.

**Independent Test:** With the allowlist monkeypatched to include a second id (`acme/second-guard`,
so the test does not depend on US-005) and a recording `SupportsWeightLoad` double,
`FORAGE_MODEL_ID=acme/second-guard` makes the ASGI lifespan call `acquire_and_load(...,
model_id="acme/second-guard")`, both stubbed `from_pretrained` calls receive it, `/health` reports
`promptguard_model == "acme/second-guard"`, and `derive_sanitizer_revision({})` differs from the unset
value; with `FORAGE_MODEL_ID=evil/model` the lifespan raises `ModelConfigurationError` before the app
serves, the log carries the closed reason `model_id_not_allowed` and a sentinel value 0 times; with the
id unset the hash input is byte-identical to explicitly setting the 22M id; at the shipped allowlist
(22M only until US-005) the 86M id is refused like any other value.

**Implementation Hints:**
- **One read site, like the revision.** `resolve_model_id() -> str` in `model_fetcher.py` beside
  `resolve_revision()` (`:882`): read `FORAGE_MODEL_ID` once, strip, compare against a closed
  `ALLOWED_MODEL_IDS` **that ships with the 22M only** — US-005's vendoring commit adds the 86M id in
  the same commit as its manifest entry, so the allowlist never names a model the service cannot serve
  (salty, round 1; this refines the *mechanism* of owner decision 4, not its intent). Default
  `DEFAULT_MODEL_ID`. The resolver is **total** for allowlisted values and returns `(model_id, ok)`
  (or the default plus a WARNING `model_id_not_allowed`, never echoing the value) so the per-request
  `derive_sanitizer_revision` fallbacks (`retrieval_app.py:292,1421,1728`) can never 500; the
  **lifespan** performs the one refusing check and raises `ModelConfigurationError(ValueError)` (new, in
  `model_fetcher.py`, on the `CacheConfigurationError` `cache.py:232` /
  `SearchProviderConfigurationError` `pipeline/search_providers/__init__.py:50` pattern —
  `tests/test_app.py:1208-1221` shows the lifespan-refusal test shape). `_MODEL_ID_RE` (`:324`) stays a
  shape check. Tests that need a second allowlisted id monkeypatch `ALLOWED_MODEL_IDS` (parametrised),
  so US-005's later addition changes no test.
- **Feed every consumer through the lifespan** (completionist, round 1): the lifespan calls
  `acquire_and_load(..., model_id=resolved)`; `pipeline/sanitizer_revision.py:40` hashes
  `f"{resolved}@{resolve_revision(resolved)}"` (the module is not in `_REVISION_SOURCES` `:12-21`, so
  the source edit rotates nothing; the *input* changes only when a non-default id is configured);
  `scripts/vendor_weights.py` keeps `DEFAULT_MODEL_ID` as its `--model-id` default (US-001). Update
  `tests/test_sanitizer_revision.py:51-61` (monkeypatches `sanitizer_revision.MODEL_ID` — a name that no
  longer exists) and `:95-118`. The end-to-end assertion runs through the ASGI lifespan with a
  recording double (not only through the parameter).
- **Assert `id2label` at load, don't assume index 1** — an explicit early-return block after
  `from_pretrained`, outside the blanket `except` (US-001's pattern). `classifier.py:27-29` says "the
  older 86M had 3 classes" — that was Prompt Guard *1*. Read `model.config.id2label`: exactly one label
  `INJECTION` and one `BENIGN` (case-insensitive); set `_injection_label_index` from it; otherwise
  `loaded` stays `False`, WARNING `model_labels_unexpected` (a test asserts that specific message).
  `tests/fixtures/tiny_model/config.json` already declares `{"0": "BENIGN", "1": "INJECTION"}`, so the
  real-loader tests keep passing; the negative case uses a fake config on the mocked auto-class.
- **`/health.promptguard_model` reports the configured id, unconditionally** (second opinion and salty,
  round 1: US-001's identity assertion guarantees the loaded model *is* the configured one whenever
  `promptguard_loaded` is true, and `acquire_and_load` runs detached in a worker thread
  (`model_fetcher.py:1546-1552`), so the lifespan cannot know a loaded id when it writes `app.state`).
  `HealthResponse.promptguard_model: str = Field(description=...)` (the shape at
  `retrieval_app.py:393-399`), mirroring the `cache_connected` / `cache_backend` split: `promptguard_loaded`
  says whether it serves, `promptguard_model` says which model was selected. The description states in
  one clause why the identity is publishable while the contiguity settings are not (the identity is
  contract-relevant and inferable from behaviour; the thresholds are tuning an attacker would otherwise
  have to guess) — recorded as a decision, and SECURITY.md's `/health` paragraph says the same. Stored
  on `app.state` by the lifespan, never re-read in the handler (`_resolved_*` pattern `:295-310`).
- **Window block (ruling R36), copied verbatim into this story's criteria:** append a `* ``1.3.0``` —
  format line (ruling R34; read `pipeline/contract.py:26-68`) to the `CONTRACT_VERSION` docstring; run
  `uv run python -m scripts.export_contract`; re-create `tests/golden/contract_1_3_0.json` by hand from
  `tests/test_contract_schema.py::_SCHEMA_MODELS` (`:28`; `HealthResponse` is golden-pinned); append
  `HealthResponse.promptguard_model` to `_EXPECTED_ONE_THREE_ZERO_DIFF` in `tests/test_contract_schema.py`
  (spec 1 US-004 created the set and pinned the 1.2.0 pair to the literal `contract_1_2_0.json`, so
  `test_the_1_1_0_to_1_2_0_diff_has_no_unlisted_additions` stays green and is **not** loosened — salty,
  round 1); refresh the four anchor-quoting pages (`tests/test_governance_docs.py:60-66`); run
  `uv run python -m scripts.export_contract --check`. `contract.py` is hashed → **this story rotates**
  (ruling R32): measure by revert-and-reproduce, record at the five sites. `contract_smoke.py` must not
  assert the field (a `1.1.0` image lacks it; it asserts `/health` by membership, `:392-415`).
- **Fan-out** (ruling R39 — the criterion is the grep): `tests/conftest.py::_CLEARED_ENV_VARS`
  (`:45-54`) + `tests/test_hermeticity.py:129-138`; `compose/minimal.yml` (`:75-90`) and
  `compose/full.yml` (`:50-60`) pass `FORAGE_MODEL_ID` and `FORAGE_MODEL_REVISION` through as bare names
  with the `HF_TOKEN` comment style; `tests/test_compose_fragments.py::TestSearchProviderPassthrough`
  (`:533`) idiom for the test; `docs/configuration.md` row (`:108` format) carrying the sentence "the
  allowlist ships with the 22M; the 86M id is added by the vendoring gate" until US-005 rewrites it;
  `kit_tools/docs/ENV_REFERENCE.md` row (`:52-53` format) and its `:73` cleared-variable sentence; the
  `/health` tables in `kit_tools/docs/MONITORING.md` and `kit_tools/docs/API_GUIDE.md` gain the field;
  `kit_tools/arch/CODE_ARCH.md`'s `retrieval_app.py` row; `NOTICE:16-18` is edited only by US-005.

**Acceptance Criteria:**
- [ ] `FORAGE_MODEL_ID` unset → `/health.promptguard_model == "meta-llama/Llama-Prompt-Guard-2-22M"`; the
      model-identity hash input is byte-identical to explicitly setting the 22M id (relative assertion
      in `tests/test_sanitizer_revision.py`); a second allowlisted id (monkeypatched) → a different
      `derive_sanitizer_revision({})` (asserted).
- [ ] A value outside the allowlist — including the 86M id at the shipped allowlist — makes the
      lifespan raise `ModelConfigurationError`; the log carries `model_id_not_allowed`; a sentinel value
      appears 0 times in captured logs; `derive_sanitizer_revision` never raises for any allowlisted
      id (test); `ALLOWED_MODEL_IDS` contains exactly the 22M id at the end of this story.
- [ ] An app-startup test with `FORAGE_MODEL_ID=<second id>` and a recording `SupportsWeightLoad`
      double asserts `acquire_and_load` received the resolved id and both `from_pretrained` calls saw
      it (through the ASGI lifespan).
- [ ] `load()` derives the injection index from `model.config.id2label` (exactly one `INJECTION` and
      one `BENIGN`, case-insensitive) and refuses `{LABEL_0, LABEL_1}` — `loaded` stays `False`, WARNING
      `model_labels_unexpected`, the specific message asserted (test).
- [ ] `/health.promptguard_model` reports the configured id unconditionally, read from `app.state` and
      never re-read in the handler (test); its description carries the publishable-identity clause;
      `kit_tools/arch/SECURITY.md`'s `/health` paragraph carries the same sentence (`grep -c
      promptguard_model kit_tools/arch/SECURITY.md` ≥ 1).
- [ ] Window block done: docstring line appended; regenerated; `tests/golden/contract_1_3_0.json`
      re-created from `_SCHEMA_MODELS`; `HealthResponse.promptguard_model` appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF`; the 1.2.0 pair untouched and green; four anchor pages refreshed;
      `uv run python -m scripts.export_contract --check` clean; the rotation (`contract.py`) measured
      and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`,
      `kit_tools/docs/GOTCHAS.md`'s rotation table (`:410-434`) and `kit_tools/arch/CODE_ARCH.md`.
- [ ] `_CLEARED_ENV_VARS` and the exact-set test include `FORAGE_MODEL_ID`; `grep -c FORAGE_MODEL_ID
      compose/minimal.yml compose/full.yml docs/configuration.md kit_tools/docs/ENV_REFERENCE.md` ≥ 1
      each; `grep -c promptguard_model kit_tools/docs/API_GUIDE.md kit_tools/docs/MONITORING.md` ≥ 1
      each; `docs/configuration.md`'s row states the 86M is added by the vendoring gate.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-002: Per-window scores — the `classify_windows` seam on the classifier (behaviour-preserving)

**Priority:** P1

**Description:** As a maintainer, I want the classifier to expose its per-window scores through one
new method, with `classify()` re-implemented on top of it and every test double moved to the new seam,
so the contiguity rule (US-007) can be written against scores that already exist — with no behaviour
change, no new config and no hashed file edited.

**Independent Test:** `classify_windows(text, *, max_chunks)` returns the per-window scores and chunk
texts for a mocked model; `classify(text, ...)` returns exactly the `(max_score, flagged_chunks)` it
returns today for every existing fixture; `uv run pytest tests/test_stage3_promptguard.py
tests/test_orchestrator.py` passes with the doubles migrated; `git diff --stat` against the eight
`_REVISION_SOURCES` files is empty; `derive_sanitizer_revision({})` equals the pre-story value.

**Implementation Hints:**
- `classify()` already builds `scores` (`promptguard/classifier.py:183-199`) before pooling at
  `:204-205`. Add `classify_windows(text, *, max_chunks) -> tuple[list[float], list[str]]` (scores and
  chunk texts, same budget check `:179-182`, same `(…, [])` fallback when unloaded) and re-implement
  `classify()` on top of it. `promptguard/classifier.py` is not a `_REVISION_SOURCES` member
  (`pipeline/sanitizer_revision.py:12-21`), so this story rotates nothing — assert it.
- **Every double that stands in for the old seam moves here** (ruling R40; codebase-fit, round 1):
  `tests/test_stage3_promptguard.py:32-40` `_make_mock_classifier` (sets `mock.classify.return_value`)
  gains a `classify_windows` side effect; the four `classifier.classify` doubles in
  `tests/test_orchestrator.py:712` (`assert_not_called`), `:757`, `:1491`, `:1837`; **and the three
  call-site assertions** `tests/test_stage3_promptguard.py:151` and `:164` (`classify.assert_not_called()`,
  the trusted-tier-skip guards) and `:172` (`classify.assert_called_once()`) — once stage 3 calls
  `classify_windows` (US-007), `:172` fails outright and `:151`/`:164` pass vacuously, so they are
  re-pointed at the new seam **in this story**, with a starting `grep -c 'classify\.' tests/` count
  recorded. Anchors: `TestClassifierUnit` `:279-297`, `TestChunking` `:300-348`;
  `test_score_exactly_at_threshold_is_safe` (`:63`), `test_score_just_above_threshold` (`:101`).
- Stage 3 keeps calling `classify()` in this story; US-007 switches it to `classify_windows`.

**Acceptance Criteria:**
- [ ] `classify_windows` exists and `classify` is implemented through it; every pre-existing stage-3
      and orchestrator verdict/score assertion **and** the three call-site assertions
      (`tests/test_stage3_promptguard.py:151,164,172`) pass against the new seam with the doubles updated.
- [ ] `git diff --stat` against the eight `_REVISION_SOURCES` files is empty;
      `derive_sanitizer_revision({})` equals the pre-story value (recorded).
- [ ] `kit_tools/arch/CODE_ARCH.md`'s `promptguard/` row names `classify_windows`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-007: Contiguity gating in stage 3 — rule, config keys, routes, telemetry (shipped off)

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
union in document order; a maximum-length `/search` result (title 512, url 2,048, snippet 2,000
characters) yields ≥ 2 windows from `_chunk_text`, and with the rule enabled a `/search` end-to-end
test through the ASGI app omits such a result with `injection_detected`.

**Implementation Hints:**
- **The rule lives in stage 3** (`pipeline/stage3_promptguard.py:128-150`, hashed → ruling 6): call
  `classify_windows` (US-002); verdict is `INJECTION_DETECTED` when `max_score > threshold` **or** when
  any run of at least `contiguity_windows` consecutive scores is `>= contiguity_threshold`.
  `PromptGuardResult` (`:35`) gains `rule: Literal["max_score", "contiguity", "both"] | None`;
  `flagged_chunks` is the max-scoring chunks, the contiguous run, or their union in document order
  (deduplicated); `score` stays `max_score`. `PromptGuardResult` is **not** internal:
  `pipeline/stage4_structuring.py:165` copies `flagged_chunks` into `injection_spans`, and only
  `finalize_quarantine` (`:178-210`) keeps hostile text off the wire by replacing the spans with a
  closed diagnostic label on any `INJECTION_DETECTED` verdict — a `contiguity` or `both` verdict must
  produce the diagnostic label only (extend `tests/test_orchestrator.py:2038,2068` to a multi-chunk
  union). `stage4_structuring.py` is byte-unchanged (`git diff --stat`), so the ledger's three-file claim
  is checked, not assumed. The orchestrator's omission log at `orchestrator.py:1032` names the rule.
- **Config keys** (`config.yaml` beside `promptguard_threshold` `:15`): `promptguard_contiguity_windows`
  (int, **default `0` = disabled**, range 0–8) and `promptguard_contiguity_threshold` (float, default
  `0.5`, range 0.0–1.0, **absolute and server-side** — it does not track a per-request
  `promptguard_threshold`; the docs say a caller who lowers the max threshold below the contiguity
  threshold gets the stricter of the two, and that this is why the rule ships off). Read through the
  bounded helpers (`_bounded_int` `pipeline/extraction_limits.py:79`, `_bounded_float`
  `pipeline/search_providers/brave.py:206`), registered in `KNOWN_CONFIG_KEYS` (spec 3 US-003).
  Configuration-only by design: both values enter the content-cache key through
  `derive_sanitizer_revision` (append them to the digest the way `promptguard_threshold` is at
  `pipeline/sanitizer_revision.py:41`); a test pins `cache_policy_fingerprint()`'s inputs so a future
  per-request override cannot slip past the cache key.
- **All three routes.** Thread both values through the threshold seam spec 3 US-005 creates to
  `run_promptguard(...)` (`:42-48`) from `sanitize_and_structure` (`orchestrator.py:160-207` — serves
  `/retrieve` `:368` and both `/extract` paths `:464`, `:540`) and the `/search` loop (`:969-997`).
  **The rule can fire on `/search`** (codebase-fit, round 1): `_search_result_promptguard_input`
  (`:665-667`) concatenates title, url and snippet capped at 512 / 2,048 / 2,000 characters
  (`orchestrator.py:583-585`), ~4,580 characters ≈ 2–3 windows at `step = 448` tokens
  (`classifier.py:135-148`) — pin the worst-case window count with a test and test one `/search`
  firing case; no "plumbing / single-window" docstring anywhere.
- **Telemetry.** One additive counter per existing section model on `/metrics` —
  `retrieve.promptguard_contiguity_detections` (`RetrieveMetricsResponse`),
  `search.promptguard_contiguity_detections` (`SearchMetricsResponse`) and
  **`extraction.promptguard_contiguity_detections`** (`ExtractionMetricsResponse`; the section is
  `extraction`, `retrieval_app.py:668`, never `extract`) — incremented when `rule` is `contiguity` or
  `both`; window change (`tests/test_contract_metrics.py` pins; the section models are `extra="forbid"`
  — GOTCHAS "Adding a `/metrics` counter without adding it to the model 500s the endpoint"); plus one
  INFO log at a contiguity verdict naming the run length and scores (never the text). Decision
  recorded: dedicated counters rather than a new key in `retrieve.blocked_by_reason` /
  `search.omitted_by_reason` (`retrieval_app.py:554-559`, `:500-506`), following the flat-counter
  precedent of `fallback_fired` / `paid_calls` / `policy_unknown_provider` (`:510-535`) and avoiding a
  closed-vocabulary growth.
- **Window block (ruling R36):** docstring line (R34 format); `uv run python -m scripts.export_contract`;
  `tests/golden/contract_1_3_0.json` re-created from `_SCHEMA_MODELS` (the counters are pinned by
  `tests/test_contract_metrics.py`, not the golden — say "golden unchanged, expected" if so);
  `_EXPECTED_ONE_THREE_ZERO_DIFF` gains any golden-visible addition (none expected); four anchor pages;
  `--check`.
- **Rotation (ruling 6):** `stage3_promptguard.py` + `orchestrator.py` + the `contract.py` docstring
  line + the two new hash inputs — one measured, recorded rotation for everyone (the default changes
  because the hash inputs grow, even with the rule off); `stage4_structuring.py` untouched.
- **Both residuals documented** (security, round 1). A run rule narrows the evasion: fragments
  separated by one benign ~448-token window still pass both rules. It also opens the opposite
  direction once enabled: adjacent windows share `CHUNK_OVERLAP = 64` tokens (`classifier.py:25`), so an
  attacker who can place sustained mid-band text on a page (a comment, a review) can aim to have that
  page blocked or a result omitted. `kit_tools/arch/SECURITY.md`'s stage-3 paragraph states both
  directions and the overlap correlation; `kit_tools/docs/GOTCHAS.md` gains an entry; both shapes are
  listed in this spec's handoff to `epic-forage-injection-corpus` as required corpus cases (the corpus
  epic measures the adversarial trip alongside the evasion before the default flips).

**Acceptance Criteria:**
- [ ] The six verdict cases in the Independent Test plus `[0.2, 0.6, 0.6]` (run at the end fires), a
      score exactly at `contiguity_threshold` counts (`>=`), a score exactly at `promptguard_threshold`
      does not fire the max rule (`>`), and an empty text (`SAFE`) are pinned by tests.
- [ ] Both keys validated at boot (out-of-range refuses like
      `tests/test_app.py::test_lifespan_refuses_an_out_of_range_cache_bound`), defaults `0` / `0.5`,
      registered in `KNOWN_CONFIG_KEYS`, documented in `docs/configuration.md` with the enabling recipe
      and the absolute-threshold note.
- [ ] All three routes apply the rule (one end-to-end test per route through the ASGI app with the
      mocked classifier); the `/search` worst-case window count (≥ 2) is pinned and one `/search`
      firing case is tested.
- [ ] A `contiguity` or `both` verdict returns only the diagnostic label in `injection_spans` (the
      `tests/test_orchestrator.py:2038,2068` shape extended to a multi-chunk union);
      `stage4_structuring.py` is byte-unchanged.
- [ ] The three counters exist on the named section models, are pinned by
      `tests/test_contract_metrics.py`; the window block is done (docstring line, regenerate, golden,
      diff set, anchor pages, `--check` clean).
- [ ] `derive_sanitizer_revision` includes both values; a test pins `cache_policy_fingerprint()`'s
      inputs; the rotation is measured and recorded at the five sites (`docs/bootstrap-notes.md`,
      `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md`'s rotation table,
      `kit_tools/arch/CODE_ARCH.md`).
- [ ] SECURITY.md's stage-3 paragraph names both rules and both residual directions; GOTCHAS entry
      present; the corpus-epic handoff line in Implementation Notes names both shapes;
      `kit_tools/arch/CODE_ARCH.md`'s stage-3 row names the rule.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-003: Benchmark harness — host-side, against the running service

**Priority:** P2

**Description:** As the owner, I want a repeatable script that drives a running Forage container with
fixed synthetic documents and reports service-level latency and container memory, with a committed
benchmark configuration and every failure path defined, so the 22M-vs-86M question is answered for the
*service*, not a bare interpreter, by a table anyone can regenerate.

**Independent Test:** `uv run pytest tests/test_bench_promptguard.py` passes under the socket guard with
injected fakes (no Docker, no network, no weights) and `uv run python -m scripts.bench_promptguard
--help` exits 0; the percentile test pins p50 = 10 and p95 = 19 for samples `[1..20]`; a fake
`/extract` returning 403 makes `main` exit 2 with a stderr line naming the status and the benchmark
config mount, writing no JSON; `bench/config.yaml` parses through `_load_config` and every key is a
`KNOWN_CONFIG_KEYS` member.

**Implementation Hints:**
- **Location and shape — reuse the existing driver** (codebase-fit, round 1): the repo already has
  two host-side drivers built for a running container with this exact architecture.
  `contract_smoke.py` provides `HttpResponse` (`:191`), `CommandResult` (`:205`), `http_get(url)`
  (`:223`), `wait_for_health(...)` (`:256` — polls `/health` until `status` matches, with a deadline:
  US-004's "wait for `promptguard_loaded`" step), `run_command(argv)` (`:434`), `_json_object` (`:302`)
  and a `main(argv)` + argparse shape (`:669`); `searxng_smoke.py:493` already shells `docker inspect
  --format '{{.State.Status}} {{.State.ExitCode}}'`. `scripts/bench_promptguard.py` imports those
  pieces (`from contract_smoke import ...` — the repo root is on `sys.path` under `uv run python -m`)
  rather than re-implementing them; the injectable seams are `post_fn`, `stats_fn` and `clock`, and
  the `clock` double is `tests/fakes.py::ManualClock` (`:174-193`). `tests/test_contract_smoke.py` is
  the hermetic test pattern ("pure evaluators plus an injectable fetcher"). The module goes in
  `scripts/` because it is owner tooling like `vendor_weights.py`; both `scripts/` (`.dockerignore:26`)
  and the root drivers (`Dockerfile:158-160`'s enumerated COPY list, guarded by
  `tests/test_dockerfile.py:559-603`) stay out of the image. `scripts/` is type-checked under the strict
  root (`pyproject.toml`): no private access, no suppression comments. A module-docstring
  `test_mapping:` block like `contract_smoke.py:112`.
- **Arguments**: `--base-url` (default `http://127.0.0.1:8020`, must match `^https?://`), `--container
  <name>` (optional; validated against `^[A-Za-z0-9][A-Za-z0-9_.-]*$` before it reaches an argv),
  `--runs` (default 20, minimum 5 — below it p95 is `null`), `--json <path>`, `--label` (free text
  copied into the JSON, e.g. `22m-cpus1`), `--timeout-seconds` (per request; default 300 — sized for
  the budget document of the 86M on 1 vCPU, not httpx's 5 s). Every subprocess is a list argv with
  `shell=False`.
- **The route**: drive `POST /extract` (multipart, `extract_mode=full`) — `/retrieve` cannot fetch a
  host-local fixture server (the URL validator refuses private and loopback IPs by design). `/extract`
  is release-gated by `extract_route_enabled: false` (`config.yaml:27`, `pipeline/extraction_limits.py:100`),
  so the benchmark container mounts the **committed** `bench/config.yaml` over `/app/config.yaml`
  (`-v "$PWD/bench/config.yaml:/app/config.yaml:ro"`) — never a code change.
- **`bench/config.yaml` is committed by this story**: a full copy of the shipped `config.yaml` with
  `extract_route_enabled: true`, the two contiguity keys at their shipped defaults plus a commented
  line for US-004's optional FPR smoke, and as its **first comment line**: "BENCHMARK ONLY — enables
  the release-gated upload route on a throwaway, loopback-bound container; never a deployment config."
  A test parses it through `_load_config` and asserts every key is a `KNOWN_CONFIG_KEYS` member and
  the only differences from `config.yaml` are those keys. `bench/*.json` is gitignored (the outputs
  live beside it). The mount replaces the file wholesale, so a partial file would silently benchmark
  a non-reference configuration — that is why it is a full copy. `bench/` is inside the build context
  but the Dockerfile's enumerated COPY list never copies it (`tests/test_dockerfile.py:559-603`).
- **Inputs are deterministic and synthetic**: generated in code from a fixed seed — one plain-text
  document of ≈512 tokens (≈2,000 characters of seeded lorem) and one sized **at or just under the
  derived classification ceiling** — `(512 - 64) * 64 * 4 = 114,688` characters at the default
  `extraction.max_promptguard_chunks: 64` (`config.yaml:43`; read the constant, do not hard-code it —
  a document over the ceiling is refused with `content_too_large_to_classify`,
  `pipeline/orchestrator.py:551`). No fixture files, no third-party text. The unit test asserts the
  generator is deterministic and the budget document is within 1 % under the ceiling.
- **Measurements**: cold = wall-clock of the first request after container start; warm p50/p95 =
  nearest-rank percentiles (`sorted(samples)[math.ceil(p / 100 * n) - 1]`) over `--runs` further
  requests per input, `time.perf_counter`; container RSS = `docker stats --no-stream --format
  '{{.MemUsage}}' <container>` sampled after the warm-up loop, parsed to MiB (label: "container
  memory after warm-up"); the exit code and OOM state are recorded by US-004, not the harness. The
  published numbers are **single-in-flight latency** — the harness fires one request at a time and
  never exercises `classification_concurrency`; the JSON carries `"concurrency": 1` and the docs say
  the table does not characterise behaviour at `classification_concurrency > 1` (second opinion,
  round 1; a `--concurrent N` mode is a follow-up, not this story).
- **Failure behaviour is defined** (story quality, round 1): a non-2xx from `/extract` aborts before
  any measurement with exit code 2 and a stderr line naming the status and the `bench/config.yaml`
  mount; a connection error at `--base-url` exits 2 with a closed message naming the URL; a timed-out
  sample aborts the row with exit 2 (never a silent exclusion from the percentiles); `--container`
  omitted, or `docker stats` failing or unparseable, leaves `container_mem_mib` as `null`, logs one
  WARNING and completes the run (the latency numbers stand); no failure path writes a partial JSON
  file or echoes a token or an environment value.
- **Output**: one JSON object with fixed keys — `label`, `base_url`, `runs`, `concurrency`,
  `cold_ms_512`, `warm_p50_ms_512`, `warm_p95_ms_512`, `cold_ms_budget`, `warm_p50_ms_budget`,
  `warm_p95_ms_budget`, `container_mem_mib`, `promptguard_model` and `contract_version` (read from
  `/health` so a row can never be mislabelled).
- Docs: the "Benchmarking the classifier" subsection lives in **`docs/weights.md`** (owner tooling,
  next to the vendoring procedure), not the operator-facing `docs/configuration.md` — it states in-line
  that the benchmark config enables a release-gated route on a throwaway loopback-bound container and
  must never be a deployment config; `docs/configuration.md`'s sizing table (spec 6 US-002) gains one
  cross-reference sentence and the single-in-flight caveat; `kit_tools/testing/TESTING_GUIDE.md` row
  for the new module.

**Acceptance Criteria:**
- [ ] `scripts/bench_promptguard.py` reuses `contract_smoke.py`'s `http_get`, `wait_for_health`,
      `run_command` and dataclasses (imports, not copies), accepts the arguments above, validates
      `--base-url` and `--container` shapes, runs subprocesses as list argv with `shell=False`, reads
      `promptguard_model` and `contract_version` from `/health` into the JSON, and writes the fixed-key
      JSON including `"concurrency": 1`.
- [ ] Both inputs are generated from a seed; the unit test asserts determinism and that the budget
      document is within 1 % under the derived ceiling read from the config constant; percentile
      maths is nearest-rank and pinned (`[1..20]` → 10 / 19; `--runs 4` → p95 `null`).
- [ ] Failure paths are pinned by unit tests with injected fakes: non-2xx → exit 2 naming the status
      and the config mount; connection error → exit 2 naming the URL; timed-out sample → exit 2;
      `--container` omitted / `docker stats` failure → `container_mem_mib: null` + one WARNING and a
      complete run; no partial JSON on any failure; no token or env value in any message.
- [ ] `bench/config.yaml` is committed with the first-line warning, `extract_route_enabled: true`, the
      contiguity keys, and a test proving it parses through `_load_config` with only
      `KNOWN_CONFIG_KEYS` members; `bench/*.json` is gitignored.
- [ ] `.dockerignore`, `Dockerfile` and `tests/test_dockerfile.py` are unchanged (`git diff --stat` on
      the three is empty).
- [ ] `docs/weights.md` carries the benchmark subsection with the throwaway/loopback warning;
      `docs/configuration.md`'s sizing table carries the cross-reference and the single-in-flight caveat;
      TESTING_GUIDE lists the module; the module docstring carries a `test_mapping:` block.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-005: Vendor the 86M weights — licence, manifest entry, mirror, allowlist (owner gate)

**Priority:** P1

**Description:** As the owner, I want the 86M weights downloaded, verified and recorded in the
per-model manifest through the same vendoring run the 22M went through, and the 86M id added to the
allowlist in that same commit, so selecting 86M loads a digest-verified snapshot and never an
unverified one — and so the allowlist never names a model the service cannot serve. **Execution halts
here for the owner**: the run needs the gated repo, an HF token, egress and GHCR write access.

**Independent Test:** *Gate run* → `weights_manifest.json` carries an entry for
`meta-llama/Llama-Prompt-Guard-2-86M` with a 40-hex `revision` and a non-empty `files[]` set;
`ALLOWED_MODEL_IDS` names the 86M; a boot with `FORAGE_MODEL_ID=<86M>` and a synthetic snapshot
matching the committed entry reaches `weights_verified` (test); `NOTICE` names both ids; the vendoring
transcript in Implementation Notes contains no credential value. *Gate not run* → Implementation
Notes carry `### US-005 — gate not run, <date>` naming the missing prerequisites and `git status` shows
no other change; the story stops and reports, and nothing is asserted.

**Implementation Hints:**
- **Licence first (a model-card read; no weights needed).** The concrete check: the 86M model card's
  licence identifier string equals the one `NOTICE:14-20` already names for the 22M — quote both in
  the record; any other identifier, or an added use-restriction clause, is a mismatch. Expected
  outcome: they match. On mismatch: stop, record, do not vendor and do not add the id — nothing to
  revert, because the allowlist ships without the 86M (US-006); the docs rows keep "pending vendoring".
- **Credential handling — every credential, one file** (ruling R33; security, round 1): the run
  needs `HF_TOKEN`, `GHCR_USER`, `GHCR_TOKEN` (write:packages) and `GITHUB_TOKEN`. `umask 077;
  f="$(mktemp)"; trap 'rm -f "$f"' EXIT`, then for each name `printf 'NAME='; read -rs V; echo;
  printf 'NAME=%s\n' "$V" >> "$f"; unset V` — mode 0600, deleted on exit, sourced by the vendoring
  shell (`set -a; . "$f"; set +a`) or passed as `--env-file "$f"`; never on a command line, never
  `export NAME=<value>` typed interactively, never in this file. `docs/weights.md:92-98`'s
  `export GHCR_TOKEN=ghp_…` block is rewritten to this form in this story. Record the file mode and
  deletion for every credential, never a value.
- **The run** is `docs/weights.md` § "Vendoring: the procedure" (`:75-147`) with `--model-id
  meta-llama/Llama-Prompt-Guard-2-86M --revision <sha from the model card>`: download (allow-patterns
  only — a file outside `ALLOWED_SUFFIXES` is refused; then stop and record, do not widen the set),
  hash, `generate_manifest` for the 86M entry, the scoped `manifest_diff` output reviewed and recorded
  (it must report the 22M entry as untouched), push to `ghcr.io/washingbearlabs/forage-weights:<86M
  revision>` (revision-keyed tag; the 22M tag is untouched), and the "commit all together" step
  (`:137-147`) — the manifest entry, `ALLOWED_MODEL_IDS` gaining the 86M id, `docs/weights.md`'s pin
  sentences, `docs/configuration.md` / `ENV_REFERENCE.md` rows losing "pending vendoring", and
  `NOTICE` in one commit. `DEFAULT_MODEL_REVISION` stays the 22M pin; the 86M's pin lives only in its
  manifest entry.
- **Failure paths, recorded not improvised**: HF 403 (gated access not granted) → stop, record
  `access pending`, US-004's 86M rows read `not measured — access pending`; 429 or a partial download →
  clear the partial snapshot from the working cache and re-run, recording the retry.
- **Record** in `### US-005 — 86M vendored, <date>`: licence outcome (both identifier strings),
  revision, file count and total bytes, the scoped manifest diff, the mirror tag, the verification
  transcript (`weights_verified`), the allowlist edit, and `NOTICE`'s edit (both ids named, "Built with
  Llama" line unchanged).

**Acceptance Criteria:**
- [ ] If the gate has not run: Implementation Notes carry `### US-005 — gate not run, <date>` naming
      the missing prerequisites, and nothing else in the tree changes (the verifier accepts this state).
- [ ] The 86M licence check is recorded with both identifier strings and its outcome; on mismatch
      nothing is vendored and the remaining criteria are void.
- [ ] `weights_manifest.json` carries the 86M entry (revision + files with `path`/`sha256`/`size`);
      `ALLOWED_MODEL_IDS` names the 86M in the same commit; `ALLOWED_SUFFIXES` unchanged; a test with a
      synthetic snapshot matching the committed entry reaches `weights_verified` under
      `FORAGE_MODEL_ID=<86M>`; the scoped `manifest_diff` recorded shows the 22M entry untouched.
- [ ] The mirror holds `forage-weights:<86M revision>`; `docs/weights.md` states both pins, that the
      mirror tag is the revision, and carries the one-file credential recipe (no `export …=` of a value).
- [ ] `NOTICE` names both model ids; the credential file was mode 0600 and is recorded as deleted;
      `grep -nE 'hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|(HF_TOKEN|GHCR_TOKEN|GITHUB_TOKEN)=[^ ]'
      kit_tools/specs/feature-hardening-promptguard-86m.md docs/weights.md NOTICE` returns nothing.

### US-004: Run the benchmark on the reference container and record it (owner gate)

**Priority:** P2

**Description:** As the owner, I want the 22M and 86M numbers measured on the actual running container
at `FORAGE_CPUS=1` and `4`, recorded here and in the sizing table, with the opt-in recipe and its
caveats documented — so the corpus epic can measure catch rate against a model that is known to fit.
**Execution halts here for the owner** (ruling 18): the run needs the vendored 86M (US-005), an HF
token, Docker and the reference envelope.

**Independent Test:** *Gate run* → this file's Implementation Notes carry `### US-004 — benchmark,
<date>` with one row per run (4 required: {22M, 86M} × {1, 4} CPUs; 2 optional at 2 CPUs; 86M rows
read `not measured — access pending` if US-005 recorded a 403), each row's `promptguard_model` read
from `/health`, the container-memory column and the OOM/exit-code line per run, and the wall-clock of
the whole matrix; `docs/configuration.md`'s sizing table has the classifier column filled for 1 and 4
vCPU (the 2 vCPU row reads `not measured` unless run); the recorded commands reference `--env-file`
only. *Gate not run* → Implementation Notes carry `### US-004 — gate not run, <date>` naming the
missing prerequisites (the vendored 86M, HF token, Docker, the reference envelope) and nothing else in
the tree changes; the story stops and reports.

**Implementation Hints:**
- **Credential handling**: the one-file recipe from US-005 (`umask 077`, `mktemp`, `trap`) holding
  `HF_TOKEN` only; `--env-file "$f"`. The file's contents, `docker inspect` of any container started
  with it, `docker ps --no-trunc` and `docker compose config` output are **never** pasted into the
  record (`--env-file` puts the token in `Config.Env`).
- **The container per run** (named, no `--rm`, so it can be inspected; loopback-bound): `docker run -d
  --name bench-<model>-<cpus> --cpus "$cpus" --memory "${FORAGE_MEM_LIMIT:-1024m}" --env-file "$f"
  -e FORAGE_MODEL_ID=<id> -e FORAGE_CPUS=$cpus -v forage-model-cache:/app/model-cache -v
  "$PWD/bench/config.yaml:/app/config.yaml:ro" -p 127.0.0.1:8020:8020 forage:bench`, built with
  `docker build -t forage:bench .` (no build args). **Bounded wait**: poll `/health` every 5 s for up
  to 15 minutes (`contract_smoke.wait_for_health` does this) for `promptguard_loaded: true` — the
  first 86M start downloads through the verified path; record the cold-start time separately. On
  timeout, record the row with `promptguard_loaded: false`, run the `docker inspect --format
  '{{.State.OOMKilled}} {{.State.ExitCode}}' bench-<model>-<cpus>` step anyway, and enter the result as
  the row's outcome rather than aborting the matrix. Then `uv run python -m scripts.bench_promptguard
  --container bench-<model>-<cpus> --label <model>-cpus<cpus> --json bench/<model>-<cpus>.json`, then
  the inspect step, then `docker rm -f bench-<model>-<cpus>`.
- **Wall-clock estimate**: 42 requests per row (2 inputs × (1 cold + 20 warm)) × 4 required rows; at
  the 1 vCPU 86M budget-document worst case this is minutes per row, not hours — record the actual
  matrix wall-clock so the next owner knows.
- **The table** (columns fixed; copy the JSON keys): `label | promptguard_model (from /health) | cpus |
  cold 512-tok (ms) | warm p50/p95 512-tok (ms) | cold budget (ms) | warm p50/p95 budget (ms) |
  container memory after warm-up (MiB) | OOMKilled | exit code`. Four required rows. A separate line
  records the measured size the 86M set adds to the `forage-model-cache` volume (`docker run --rm -v
  forage-model-cache:/c alpine du -sm /c`).
- **Optional FPR smoke** (cheap, the container is running): enable the contiguity rule via the
  committed benchmark config's commented line (`promptguard_contiguity_windows: 2`), post the harness's
  long benign document 20 times to each model and record how many verdicts were `injection_detected`
  — a number for the corpus epic, not a gate. It measures the accident rate only; the adversarial
  trip rate is the corpus epic's (US-007).
- **Fill the sizing table** (`docs/configuration.md`, spec 6 US-002): classifier column for the
  1 vCPU / 1 GB and 4 vCPU rows from the 22M numbers with the 86M numbers as a second line per row; the
  2 vCPU row reads `not measured` unless the optional runs were done; the single-in-flight caveat
  stays beside it. State the opt-in recipe once (`FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M`)
  with its acquisition caveat: **86M is served from Hugging Face and the `forage-weights` mirror at its
  own revision tag; a deployment that selects it needs the same acquisition posture as the 22M**
  (`docs/configuration.md` and `docs/weights.md`, one sentence each).
- **Do not flip the default** (owner decision 4). If 86M container memory exceeds 900 MiB at 1 GB or
  warm p50 at 1 CPU exceeds 2× the 22M figure, say so and leave the open question open.

**Acceptance Criteria:**
- [ ] If the gate has not run: Implementation Notes carry `### US-004 — gate not run, <date>` naming
      the missing prerequisites; nothing else in the tree changes.
- [ ] Implementation Notes carry the populated table (four required rows, `promptguard_model` from
      `/health` per row; `not measured — access pending` where US-005 recorded a 403), the OOM/exit-code
      line per run, the volume-size line, the matrix wall-clock, and every command as run with
      `--env-file "$f"`, `-p 127.0.0.1:8020:8020` and `docker rm -f` — no token value, no `docker
      inspect` / `docker ps --no-trunc` / `docker compose config` output.
- [ ] `docs/configuration.md` sizing table's classifier column is filled for 1 and 4 vCPU with both
      models (2 vCPU marked `not measured` or filled); the opt-in recipe and its acquisition caveat
      appear once each (`grep -c 'FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M' docs/configuration.md` ≥ 1).
- [ ] The optional FPR smoke, if run, is recorded with its counts and the config used.
- [ ] `grep -nE 'hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|HF_TOKEN=[^ ]'` over this
      file, `docs/configuration.md` and `docs/weights.md` returns nothing; the token file is recorded
      as mode 0600 and deleted.

## Edge Cases

- `FORAGE_MODEL_ID` set to the 22M id explicitly — identical to unset, including the hash input (US-006).
- `FORAGE_MODEL_ID` with surrounding whitespace — stripped, then allowlisted (US-006).
- The 86M id before US-005 — refused by the allowlist like any other value (`model_id_not_allowed`);
  after US-005, an allowlisted id whose manifest entry is somehow missing — `manifest_model_unknown`,
  service degraded (`promptguard_unavailable`), `/health.promptguard_model` names the configured id
  (US-001, US-006).
- `FORAGE_MODEL_REVISION` set to a sha that is not the selected model's manifest pin —
  `weights_revision_unpinned`, nothing loaded, degraded, closed reason (US-001).
- A warm snapshot of a different model, or of the same model at another revision, on the volume —
  never loaded under the requested pair (`model_identity_mismatch` / the path equality assertion) (US-001).
- `resolve_revision` for a model with no entry — the literal `unpinned`; never reaches a path (US-001).
- A loaded model whose `id2label` is `{0: "LABEL_0", 1: "LABEL_1"}` — refused, `loaded` stays `False`,
  closed reason logged (US-006).
- `contiguity_windows` larger than the chunk count — the rule cannot fire; the max rule still can (US-007).
- A per-request `promptguard_threshold` below `promptguard_contiguity_threshold` — the contiguity rule is
  the stricter one; documented, and the rule is off by default (US-007).
- Both rules fire on `[0.9, 0.6, 0.6]` — `rule == "both"`, all three chunks flagged in document order,
  `injection_spans` carries the diagnostic label only (US-007).
- A maximum-length `/search` result — ≥ 2 windows; the rule can fire when enabled (US-007).
- An empty text — zero windows, `SAFE`, as today (US-007).
- `--runs` below 5 — p95 reported as `null` (US-003).
- `/extract` answers 403 because the benchmark config was not mounted — exit 2 naming the mount (US-003).
- `docker stats` unavailable — `container_mem_mib: null`, run completes, one WARNING (US-003).
- The 86M repo carries a file outside `ALLOWED_SUFFIXES` — vendoring refuses; stop and record (US-005).
- HF returns 403 for the 86M repo — recorded as `access pending`; the 22M rows still land (US-005, US-004).
- 86M never reaches `promptguard_loaded: true` within 15 minutes — the row records the timeout and the
  inspect result; the matrix continues (US-004).
- 86M OOM-killed at 1 GB — recorded as a row with `OOMKilled: true`, exit code 137; the default stays
  22M (US-004).

## Out of Scope

- Flipping the default to 86M (owner decision 4; revisited after `epic-forage-injection-corpus`).
- Turning contiguity gating on by default (ruling R17; the corpus epic decides after measuring both
  the accident and the adversarial false-positive rates).
- A `--concurrent N` benchmark mode (the published numbers are single-in-flight and say so).
- int8 / ONNX Runtime builds of either model (new dependency; open question only).
- Widening `ALLOWED_SUFFIXES`, `ALLOW_PATTERNS` or dropping `use_safetensors=True` — pinned unchanged.
- Datamarking / spotlighting of retrieved text (Poppy family Epic 5).
- Any model outside Prompt Guard 2 (the 2026-08-30 verdict stands; the allowlist is the mechanism).
- The corpus that measures catch rate and false-positive rate (`epic-forage-injection-corpus`), which
  receives both stage-3 residual shapes from US-007 as required vectors.
- Reporting the contiguity settings on `/health` (deliberate reconnaissance boundary; US-006 records
  why the model identity is published and the settings are not).

## Assumptions

- Prompt Guard 2 86M is a two-class DeBERTa sequence classifier like the 22M; the `id2label` check in
  US-006 turns this into a load-time fact.
- The 86M weights live in the same gated Hugging Face org under the same licence identifier; US-005
  verifies before vendoring and does not add the id otherwise.
- Poppy is the only consumer; `promptguard_model` and the three counters are additive, so a 1.2.0
  client still validates.
- Docker Desktop (macOS) or Docker Engine (Linux) is where the owner gates run; `docker stats` is available.
- Two models share one `forage-model-cache` volume and one mirror repository, distinguished by revision;
  each model's revision lives in its own manifest entry, and `FORAGE_MODEL_REVISION` applies to the
  selected model only.
- The upstream seams named in the header note ("Assumed landed") exist with the shapes those specs
  describe.

## Technical Considerations

- **Rotation ledger (rulings 6, R32):** US-001 — none (no `_REVISION_SOURCES` file; the hash input is
  unchanged at the default; asserted by `git diff --stat`); US-006 — `contract.py` (docstring line) →
  rotates for everyone, plus the hash *input* changes only when a non-default id is configured;
  US-002 — none (`promptguard/classifier.py` is not hashed; asserted); US-007 —
  `stage3_promptguard.py`, `orchestrator.py`, `contract.py` and two new hash inputs → one rotation for
  everyone, `stage4_structuring.py` byte-unchanged; US-003, US-004, US-005 — none (the manifest,
  `bench/config.yaml` and scripts are not hashed). Every rotation is recorded at the five sites
  (`docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md`'s
  rotation table, `kit_tools/arch/CODE_ARCH.md`) with the count sentences updated by value.
- **Contract window (rulings 5, R36):** `promptguard_model` (US-006) and the three counters (US-007)
  move the document; each story runs the uniform window block (docstring line, regenerate, golden from
  `_SCHEMA_MODELS`, `_EXPECTED_ONE_THREE_ZERO_DIFF`, four anchor pages, `--check`); spec 8 US-002
  freezes it.
- **Memory:** the reference envelope is `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` (spec 6); 86M fp32 at
  up to ~560 MB plus the 512 MiB parent reservation (`compose/minimal.yml:105-109`) is what US-004
  measures at the service level rather than assumes; its RSS delta is the "classifier working set" term
  spec 6's memory rule leaves as a placeholder.
- **Threads:** `promptguard_threads` (spec 6 US-001, default `0` = torch default) — the benchmark
  records `FORAGE_CPUS` only; thread tuning is a spec 6 concern.
- **Hermeticity:** every new test runs under the `pytest-socket` guard with injected fakes; the harness
  and the vendoring run are only ever executed for real by the owner.
- **Supply chain:** `verify_weights()` is the single verification entry point for every acquisition
  leg and verifies the requested `(model_id, revision)` pair; this spec never adds a path around it.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md) (stage 3, `promptguard/`, `model_fetcher.py`)
- Security: [SECURITY.md](../arch/SECURITY.md) (stage-3 paragraph, `/health` paragraph, non-vulnerabilities table)
- Weights: [`docs/weights.md`](../../docs/weights.md), [`docs/configuration.md`](../../docs/configuration.md)
- Drivers: `contract_smoke.py` (the host-side driver pattern), `tests/test_contract_smoke.py`
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("PromptGuard model absent → degraded", the rotation table)
- Research: Poppy `kit_tools/specs/WEB_ACCESS_FAMILY.md` § "PromptGuard research (2026-08-30)"

## Implementation Notes

<!-- Populated during execution. US-001 and US-002 record the unchanged default revision value; US-006
and US-007 record their rotations; US-005 records the licence check, the vendoring transcript, the
scoped manifest diff and the allowlist edit; US-004 records the benchmark table, the matrix wall-clock
and the sizing-table fill. -->

## Refinement Notes

### Research Findings

**Decision:** The model id and its revision are parameters of the acquisition/loading path, and the
verifier and the loader agree on one directory.
**Rationale:** `load()` passes the module constant to both `from_pretrained` calls
(`classifier.py:84-85,98-99`); the acquisition functions are id-free (`model_fetcher.py:1474-1665`);
and today `verify_weights` hashes the manifest's revision directory (`:795`) while `_load_verified`
loads the resolved one (`:1474`) with no equality check — a `FORAGE_MODEL_REVISION` override plus two
snapshot directories would load a never-hashed tree (validation round 1, security reviewer).
**Alternatives considered:** Importing the resolver into the classifier — rejected, `model_fetcher.py:139`
imports the classifier at module scope (cycle). Verifying the requested revision against the pin's
digests — rejected: a different revision has different files, so the honest outcome is a closed
refusal, `weights_revision_unpinned`.
**Source:** `promptguard/classifier.py:23,50-56,63-116`; `model_fetcher.py:139,174,653,764-830,882-902,
1060-1077,1474-1665`.

**Decision:** The manifest is a per-model exact-set allowlist with a per-model revision; verification
is never skipped; `resolve_revision(model_id)` is total (override → pin → `unpinned`) and memoised.
**Rationale:** `weights_manifest.json` carries per-file `sha256` + `size`, generated by
`scripts/vendor_weights.py` (`:446`) and diffed by `manifest_diff` (`:501`, run at `:981`); a revision
read off a model card cannot produce the digests; `derive_sanitizer_revision` runs per request
(`retrieval_app.py:292,1421,1728`), so the pin is read once (validation rounds 1 and 2).
**Alternatives considered:** "Verified if known, unverified if new" — rejected as a supply-chain
fail-open. Returning `DEFAULT_MODEL_REVISION` for an unvendored model — rejected: it would hash a
`(model, revision)` pair that never existed (validation round 1, salty).
**Source:** `weights_manifest.json` header; `model_fetcher.py:566-630,764-830,882-935,1617`;
`scripts/vendor_weights.py:486-546,981`; `tests/fakes.py:157-171`.

**Decision:** The allowlist ships with the 22M only; the vendoring commit adds the 86M id.
**Rationale:** An allowlist entry nothing can serve would pass boot and then degrade the service's only
ML control (`manifest_model_unknown` → `promptguard_unavailable`) — an accepted archive state of the
gate would ship it; adding the id with its manifest entry makes "allowlisted ⇒ serveable" a property
and removes the licence-revert branch (validation round 1, salty and completionist). This refines the
mechanism of owner decision 4, not its intent (22M default, 86M opt-in).
**Source:** `model_fetcher.py:1617`; `feature-hardening-release.md` (depends only on this spec archiving).

**Decision:** Contiguity gating is a second rule in stage 3 over per-window scores, shipped off, in two
stories (the seam, then the rule).
**Rationale:** The pooling happens inside `classify()` (`:204-205`) and the scores list already exists
(`:183-199`); the seam refactor is behaviour-preserving and not hashed (`promptguard/classifier.py` is
not a `_REVISION_SOURCES` member), so the split costs no rotation (validation round 1, story quality;
ruling R37). The false-positive rate is unmeasured and adjacent windows overlap by 64 tokens, so the
rule is opt-in until the corpus epic measures both directions (ruling R17).
**Alternatives considered:** Lowering the single threshold (the research's stated failure mode);
ensembling a second model ("don't ensemble"); a k-windows-anywhere rule — a corpus-epic candidate.
**Source:** `pipeline/stage3_promptguard.py:128-150`; `promptguard/classifier.py:25,135-148,183-205`;
`pipeline/stage4_structuring.py:165,178-210`; `pipeline/orchestrator.py:583-585,665-667`.

**Decision:** The benchmark drives the running service from the host, reusing `contract_smoke.py`.
**Rationale:** A bare interpreter measures the classifier, not the 1 GiB service; `scripts/` is
excluded from the image by design (`.dockerignore:26`, `Dockerfile:158-168`); `/retrieve` cannot fetch a
loopback fixture, so `/extract` is the route under a committed benchmark config (ruling R18);
`contract_smoke.py` already provides the driver pieces and `tests/test_contract_smoke.py` the hermetic
pattern (validation round 1, codebase-fit).
**Source:** `.dockerignore:26`; `tests/test_dockerfile.py:559-603`; `url_validator.py:73-102`;
`contract_smoke.py:112,191,205,223,256,434,669`; `config.yaml:27,43`.

### Scope Adjustments

- Validation round 1 (2026-09-19): US-001 split into US-001 (mechanical id threading + per-model
  manifest, no env var) and US-006 (`FORAGE_MODEL_ID`, refusal, `id2label`, `/health`); US-005
  (vendoring gate) added before US-004; the benchmark moved host-side; contiguity gating default changed
  from `2` windows to `0`; the `<= promptguard_threshold` cross-key constraint dropped.
- Validation round 2 (2026-09-19): US-002 split into US-002 (the `classify_windows` seam, not hashed)
  and US-007 (the rule; rotates) — ruling R37; the revision became per model with the verifier/loader
  agreement (`weights_revision_unpinned`); the allowlist ships 22M-only and US-005 adds the 86M; the
  benchmark config is committed (`bench/config.yaml`), the harness reuses `contract_smoke.py` and
  defines its failure paths; the "inert on `/search`" claim was withdrawn (the caps sum to 2–3 windows);
  the third counter is `extraction.…`; the credential recipe covers every vendoring credential.
- The outline's "verify whether `sanitizer_revision.py` is hashed" resolved to *not hashed*; the earlier
  "US-001 rotates nothing" claim was wrong because the `/health` line edits `contract.py` — that line
  lives in US-006, which records its rotation.

### Decisions Made

- Owner decision 4 (22M default, allowlisted opt-in); rulings R17, R18, R29, R32, R33, R34, R36, R37,
  R39, R40 as stated in the epic wrapper and its round-1 and round-2 addenda.
- `/health.promptguard_model` reports the **configured** id unconditionally (the loaded model is the
  configured one by construction, US-001; the lifespan cannot know a loaded id from a detached
  worker) — the round-1 two-branch criterion was dropped (second opinion, salty).
- `scripts/vendor_weights.py --model-id` defaults to `DEFAULT_MODEL_ID`, never to `FORAGE_MODEL_ID`: a
  supply-chain tool must not silently depend on the runtime environment; an explicit flag always wins.
- Dedicated `promptguard_contiguity_detections` counters rather than new keys in the closed
  `blocked_by_reason` / `omitted_by_reason` vocabularies (flat-counter precedent).
- Overruled: "split US-001 three ways" (story quality) — two ways suffices; the manifest and the loader
  share one concern (identity threading), and the split costs no rotation either way.
- Overruled: "report the contiguity settings on `/health`" — deliberately not exposed; the decision and
  the asymmetry with `promptguard_model` are recorded in the field description and SECURITY.md.
- Overruled: "add a `--concurrent N` mode now" (second opinion) — the caveat is stated beside every
  published number; the mode is a follow-up once the corpus epic needs it.
- Overruled: "the 100 % `/search` inertness" (this spec's own round-1 text) — withdrawn on the measured
  caps; the rule can fire on `/search` and a test says so.
- The 2 vCPU sizing row is `not measured` unless the owner runs the optional pair.

## Clarifications

### Session 2026-09-19
- Q: How should the 86M upgrade land? → A: Configurable model, 22M stays default; the benchmark
  publishes numbers on the reference envelope; 86M is opt-in until the corpus epic measures it.
- Q: Does the contiguity rule replace the max rule? → A: No — beside it, both tunable, `windows=0`
  disables the new rule (ruling 17).

### Session 2026-09-19 (validation round 1)
- Rulings applied: R17, R18, R29, R31, R32, R33, R34.
- Q: Is `promptguard_contiguity_threshold` relative to a per-request threshold? → A: No — absolute and
  server-side; the cross-key constraint is dropped.
- Q: Where does the refuse-boot live? → A: In the lifespan (`ModelConfigurationError`); the resolver is
  total so the per-request `derive_sanitizer_revision` fallbacks can never raise.

### Session 2026-09-19 (validation round 2)
- Rulings applied: R29 (corrected — per-model revision, verifier/loader agreement, `manifest_diff`
  keyed shape, `_EXPECTED_ONE_THREE_ZERO_DIFF`, `extraction` section, committed `bench/config.yaml`,
  every credential in one file, the benchmark recipe in `docs/weights.md`, `/search` can fire), R37
  (US-002/US-007 split and execution order), R36 (uniform window block), R39 (grep-defined fan-out),
  R40 (named test seams and `grep -c` starts), R33.
- Q: What does `resolve_revision` return for an allowlisted model with no manifest entry? → A: The
  closed literal `unpinned`; acquisition refuses `manifest_model_unknown` before any path is built.
- Q: What does `FORAGE_MODEL_REVISION` mean when it is not the selected model's pin? → A: A refusal
  (`weights_revision_unpinned`); the override never selects a directory the pin did not hash.
- Q: Does the allowlist name the 86M before it is vendored? → A: No — US-005 adds it with the manifest
  entry, so "allowlisted" always means "serveable".

## Open Questions

- [ ] If US-004 shows 86M does not fit 1 vCPU / 1 GB as a service, is an int8/ONNX build worth a new
      dependency? Non-blocking; only asked if the numbers force it.
- [ ] Whether a `--concurrent N` benchmark mode should land with the corpus epic (it needs the same
      container recipe) — non-blocking; the single-in-flight caveat holds the line meanwhile.
