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
> R32, R33, R34, R36, R37, R39, R40, R41, R43. Context: Poppy's `WEB_ACCESS_FAMILY.md` § "PromptGuard research
> (2026-08-30)". Validation rounds 1–5 (2026-09-19) applied — see Clarifications (round 5 is the
> final, un-reviewed close-out; its unapplied warnings are listed in § Known risks).
>
> **Assumed landed** (upstream seams this spec consumes by name): spec 3 US-003's `KNOWN_CONFIG_KEYS`
> registry, spec 3 US-005's per-route threshold resolver, spec 6 US-002's sizing table in
> `docs/configuration.md`, and spec 1 US-004's `_EXPECTED_ONE_THREE_ZERO_DIFF` set in
> `tests/test_contract_schema.py` (ruling R36) — **and the 1.3.0 window is already open**
> (`CONTRACT_VERSION == "1.3.0"` in `pipeline/contract.py`, bumped by spec 1 US-004; this spec's two
> window stories append to that entry and never bump). If any has landed with a different shape, the
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
  path, the manifest becomes a per-model allowlist, and **an existing defect is fixed**: today
  `verify_weights` hashes one directory (`model_fetcher.py:795`, the manifest's revision) while
  `_load_verified` loads another (`:1474`, the resolved revision) with no equality check, so an
  operator override plus two snapshot directories loads a never-hashed tree. The fix rides here by
  design (not as a side effect of the refactor) and survives any reshuffle. **No new environment
  variable and no behaviour change at the default**: the revision chain keeps `_REVISION_RE`'s shape
  check and `DEFAULT_MODEL_REVISION` as the default model's last resort (rulings R29 as corrected in
  round 3, R37).
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

**The gates-unrun end state, named** (salty, round 2): an autonomous run of this spec legitimately
stops at 5/7 — US-005 and US-004 record `gate not run`. In that state the tree ships the seams and
none of the payload: no 86M weights, no benchmark numbers, `ALLOWED_MODEL_IDS` with exactly one
member (`grep -c 'Llama-Prompt-Guard-2-86M' model_fetcher.py` is 0), `promptguard_contiguity_windows`
at `0`, `/health.promptguard_model` reporting the one configured id, and the `docs/configuration.md`
/ `ENV_REFERENCE.md` rows still reading "pending vendoring". Whoever finishes it later runs US-005
then US-004 as written; nothing else is outstanding. The archived spec must not be read as "the 86M
shipped" unless US-005's record says so.

## Goals

- With `FORAGE_MODEL_ID` unset or set to the 22M id, the model-identity hash input
  `f"{model_id}@{revision}"` is byte-identical to today's (pinned relatively by
  `tests/test_sanitizer_revision.py::test_the_hashed_model_identity_is_model_id_at_revision`, `:95-118`,
  extended with the id); a second allowlisted id produces a different `sanitizer_revision`; any value
  outside the allowlist refuses boot with a closed reason and the value is echoed 0 times.
- With `FORAGE_MODEL_ID=<a second allowlisted id>` and a stubbed auto-class, the lifespan constructs
  `model_fetcher.WeightAcquisition(..., model_id=<resolved>)` (`retrieval_app.py:1322-1329` — the
  lifespan never calls `acquire_and_load` itself; `attempt_once` does, `model_fetcher.py:1830-1837`),
  `acquire_and_load` receives that id and **both** `from_pretrained` calls receive it (asserted on the
  mock through the ASGI lifespan, not only through the parameter); a warm snapshot of a different
  model, or of the same model at a different revision, on the same volume is never loaded.
- A model id with no manifest entry refuses to acquire (closed reason `manifest_model_unknown`) even
  when `FORAGE_MODEL_REVISION` is set; a well-formed `FORAGE_MODEL_REVISION` that is not the selected
  model's manifest revision refuses **before any snapshot path is built or any source is tried**
  (closed reason `weights_revision_unpinned`); a malformed one keeps today's `model_revision_invalid`
  ERROR-and-fall-back-to-pin; an unreadable manifest at runtime never changes the default model's
  `sanitizer_revision`; `ALLOWED_SUFFIXES`, `ALLOW_PATTERNS` and `use_safetensors=True` are
  byte-unchanged by this spec.
- `/health` reports `promptguard_model` and `/metrics` carries `promptguard_contiguity_detections` in
  the `retrieve`, `search` and `extraction` sections; both land inside the unreleased 1.3.0 window
  with the golden and `_EXPECTED_ONE_THREE_ZERO_DIFF` updated and `export_contract --check` clean.
- With `promptguard_contiguity_windows: 2` and `promptguard_contiguity_threshold: 0.5`, per-window
  scores `[0.6, 0.6]` are `INJECTION_DETECTED` with both chunks flagged and `rule == "contiguity"`;
  `[0.6, 0.2, 0.6]` is `SAFE`; `[0.86]` fires the max rule exactly as before; at the shipped default
  (`0` windows) `[0.6, 0.6]` is `SAFE` and every pre-existing stage-3 verdict and score is unchanged;
  the rule **can** fire on `/search` when enabled — window count is content-dependent, not
  length-dependent (a maximum-length prose result tokenises to ≥ 2 windows, a repeated-run text of the
  same length to 1), and both shapes are pinned.
- `uv run python -m scripts.bench_promptguard --help` exits 0 with no weights, no Docker and no
  network; its unit tests run under the socket guard with injected fakes; p50/p95 are nearest-rank
  (`[1..20]` → `10`, `19`); the harness **refuses to measure an unloaded classifier** — the health
  wait must end at `status: healthy` with `promptguard_loaded: true` or the run is a configuration
  failure (exit 2, no JSON, no rows); both benchmark documents are sized in **tokens** with the
  model's own tokenizer, never by a characters-per-token constant; every row records
  `promptguard_loaded`, `promptguard_model` and `sanitizer_revision` from `/health`; a configuration
  failure exits 2 with a closed message and no JSON; a **service** failure during measurement writes
  a JSON carrying `"outcome"`, the samples collected so far and null percentiles, and exits non-zero
  — the row the epic was commissioned for is never lost.
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
one directory — **fixing the live defect where `verify_weights` hashes the manifest's revision
directory and `_load_verified` loads the resolved one with no comparison** — so a second model can be
selected later without any path silently falling back to the 22M or loading a directory that was
never hashed, and so nothing changes for today's deployments.

**Independent Test:** With the default id everywhere, `uv run pytest tests/test_model_fetcher.py
tests/test_vendor_weights.py tests/test_sanitizer_revision.py tests/test_app.py` passes and
`derive_sanitizer_revision({})` equals the pre-story value (recorded in Implementation Notes); with a
fake `SupportsWeightLoad` double, `acquire_and_load(..., model_id="acme/other")` calls the double's
`load(model_id="acme/other", revision=<that model's manifest revision>, ...)` and `verify_weights`
selects the `acme/other` manifest entry; a manifest with no entry for the requested id yields
`manifest_model_unknown` and nothing is loaded; with two snapshot directories planted under one repo
dir (via `tests/fakes.py::materialize_hub_snapshot`, `:47-81`) and `FORAGE_MODEL_REVISION` naming the
unpinned one, verification refuses with `weights_revision_unpinned` and the unpinned directory is
never loaded — and `_download_from_hub` is never called and no `snapshot_path` is computed for the
unpinned revision (recording doubles); `resolve_revision("acme/unvendored")` returns the literal
`unpinned` and `derive_sanitizer_revision({})` still returns a value; with the manifest unreadable,
`resolve_revision(DEFAULT_MODEL_ID)` returns `DEFAULT_MODEL_REVISION` with one WARNING and
`derive_sanitizer_revision({})` equals the pre-story value.

**Implementation Hints:**
- **Landing order inside the story — a green suite at each step** (salty, round 4; ruling R37 forbids
  a split, so the midpoints are stated instead): (1) the per-model manifest format, `tests/fakes.py`,
  the ~40 manifest-shaped assertion sites and the generator — suite green; (2) `(model_id, revision)`
  threaded through acquisition and loading, `WeightAcquisition` included — suite green; (3) the
  top-of-function order in `_acquire_and_load` and the `_load_verified` agreement check with their
  dangerous-case tests — suite green; (4) docs and greps. Implementation Notes record which step was
  reached, so a run that dies at hour two is resumable at a known midpoint rather than bisected.
- **`DEFAULT_MODEL_ID`.** Rename `MODEL_ID` (`promptguard/classifier.py:23`) to `DEFAULT_MODEL_ID` and
  keep `MODEL_ID = DEFAULT_MODEL_ID` as a deprecated alias for one story so the importers
  (`model_fetcher.py:139`, `pipeline/sanitizer_revision.py:10`, `scripts/vendor_weights.py:107`,
  `tests/test_model_fetcher.py:120`, `tests/test_sanitizer_revision.py:12`, `tests/test_vendor_weights.py:55`,
  **and `tests/test_app.py:66`**, used at `:883` in the lifespan manifest fixture) migrate in this
  story and the alias is deleted at the end of it. **The closing check is scoped and anchored**
  (ruling R29 as corrected in round 4, R43; salty, round 3 — the round-3 whole-tree `grep -rn
  'MODEL_ID' | grep -v DEFAULT_MODEL_ID` returned 100 hits, matching `_MODEL_ID_RE` and every
  test-local `_MODEL_ID` name): `grep -n '\bMODEL_ID\b' model_fetcher.py pipeline/sanitizer_revision.py
  scripts/vendor_weights.py promptguard/classifier.py` — the four production consumers — returns
  **nothing** (the `\b` anchor excludes `DEFAULT_MODEL_ID` because `_` is a word character, and it
  excludes `_MODEL_ID_RE` for the same reason; executed 2026-09-19 before the story: **20 hits**
  across the four files — `model_fetcher.py:139,1108,1525`, `pipeline/sanitizer_revision.py:10,27,40`,
  `promptguard/classifier.py:23,85,99` and eleven in `scripts/vendor_weights.py` — every one of which
  this story rewrites); and `grep -rnw MODEL_ID tests --include='*.py'` returns nothing (pre-story
  47 hits, all imports and the assertions below; test-local `_MODEL_ID` names never match `-w`). The
  three prose mentions — `pipeline/sanitizer_revision.py:27`, `scripts/vendor_weights.py:29`,
  `tests/test_sanitizer_revision.py:102` — are rewritten too, and the `tests/test_vendor_weights.py:228`
  identity assertion `vendor_weights.MODEL_ID is MODEL_ID` is rewritten, not just re-imported.
- **The loader takes the id.** `PromptGuardClassifier.load(*, model_id: str | None = None, revision,
  cache_dir, local_files_only)` (`classifier.py:50-56`), defaulting to `DEFAULT_MODEL_ID`, and **both**
  `from_pretrained` calls (`:84-85`, `:98-99`) use it. `model_fetcher.SupportsWeightLoad.load`
  (`:1060-1077`) gains the same keyword; `_load_verified()` (`:1474`) and `acquire_and_load()` /
  `_acquire_and_load()` (`:1530`, `:1586`) pass it down; `_verify_cached()` (`:1511`, the
  `snapshot_path(cache_root, MODEL_ID, revision)` at `:1525`) and `_download_from_hub()` (`:1080`,
  `:1108`) take it as a parameter. **The lifespan seam is `WeightAcquisition`, not
  `acquire_and_load`** (ruling R29 as corrected in round 4; codebase-fit, round 3): the lifespan
  constructs `model_fetcher.WeightAcquisition(classifier, metrics=app.state.model_metrics)`
  (`retrieval_app.py:1322-1329`) and `attempt_once` (`model_fetcher.py:1811`) is the only caller of
  `acquire_and_load` (`asyncio.to_thread(acquire_and_load, self._classifier, cache_root=…,
  revision=…, manifest_path=…, metrics=…)`, `:1830-1837`). `WeightAcquisition.__init__`
  (`:1773-1781`) gains `model_id: str | None = None` stored as `self._model_id` and `attempt_once`
  forwards `model_id=self._model_id`; US-006 passes the resolved id at construction. `classifier.py`
  must **not** import `model_fetcher` (cycle via `model_fetcher.py:139`). Extend
  `tests/test_model_fetcher.py:790-791` (today asserts both `from_pretrained` calls receive `MODEL_ID`)
  to assert they receive the id passed to `load`, and add one `WeightAcquisition` test asserting
  `attempt_once` forwards `model_id` (a recording `acquire_and_load` double, the
  `tests/test_app.py:1183` `monkeypatch.setattr(model_fetcher, "acquire_and_load", _record)` shape).
- **The verifier and the loader agree on one directory (security, round 1).** `verify_weights(cache_root,
  *, manifest_path, metrics, model_id, revision)` verifies the snapshot at the **requested**
  `(model_id, revision)` pair and refuses with the new closed reason `weights_revision_unpinned` when
  `revision` is not that model's manifest entry revision — it never verifies one directory and hands
  another to `_load_verified`. **The loaded-identity check is an existence-plus-equality check in
  `_load_verified`, and `_load_verified` derives both paths itself** (ruling R29 as corrected in
  round 5; codebase-fit, round 4 — the round-4 `verified_path` parameter could only be supplied by
  the `_verify_cached` call site, `:1609`: the hub and mirror legs return a bare outcome string
  through `_try_source` (`:1660`; `_fetch_from_mirror` `:1311`; the hub leg `:1700-1709`) and discard
  their `VerificationResult`, so passing the observed directory would have meant changing three
  return shapes the hint never budgeted): `_load_verified(classifier, *, cache_root, model_id,
  revision, manifest_path)` (`:1474-1479` today takes `cache_root` and `revision` only; both call
  sites, `:1609` and `:1636`, already hold every argument) computes the manifest-derived path
  `snapshot_path(cache_root, model_id, _manifest_entry(manifest_path, model_id).revision)` (`:484`;
  the memoised entry from the revision hint below) and the resolved path `snapshot_path(cache_root,
  model_id, revision)` and, **before** calling `load()`, asserts the two are equal and that the
  directory `.is_dir()`; on either failure it logs the closed reason `model_identity_mismatch` (no
  path in the message), returns `False`, and `load()` is never called. No `_try_source` or mirror
  signature changes; the `VerificationResult` stays where it is. **What the two halves buy, said
  honestly** (salty, round 4): the equality half re-derives the top-of-function refusal at the load
  site, so a future reorder of `_acquire_and_load` cannot load an unpinned revision; the existence
  half is the live check for a snapshot that vanished or was never the requested model's. The
  dangerous-case tests below therefore call `_load_verified` **directly**, once with a `model_id`
  whose directory does not exist and once with a `revision` that is not the entry's, and assert
  `model_identity_mismatch` with `load()` uncalled in both. `_walk_snapshot` (`:653`) is unchanged.
- **The classifier side is an existence check only — and the residual is written down** (ruling R29
  as corrected in round 4; security, round 3). `load()` (`classifier.py:63-116`) wraps its body in one
  `except Exception` that logs the generic "PromptGuard model not available". The classifier does
  **not** recompute the snapshot path (no private `repo_dirname` copy, no parity test — the round-3
  design is withdrawn; `classifier.py` cannot import `model_fetcher`, and a second path derivation
  would be a second thing to keep in step): its only pre-flight is `Path(cache_dir).is_dir()` before
  the first `from_pretrained`, an explicit early-return block outside the blanket `except`
  (`logger.warning("model_cache_dir_missing"); return False`, a closed reason, no path). The identity
  guarantee lives in `_load_verified` (previous hint), and `kit_tools/arch/SECURITY.md`'s stage-3
  paragraph records the residual honestly: **"the classifier trusts the path the verifier handed
  it"** — the verifier/loader agreement is one check in one function, not two independent
  observations. **Path comparison only** (security, round 2): `model.config._name_or_path` is read
  out of the very directory the check distrusts and is commonly set from the `from_pretrained`
  argument, so it is not an alternative and not a layer. US-006's label check keeps the
  early-return-block pattern inside `load()`. Pin the dangerous case in `tests/test_model_fetcher.py`
  against `_load_verified` with `tests/fakes.py::materialize_hub_snapshot(cache_root, files, *,
  model_id, revision, symlinks)` (`:47-81`): a warm `acme/tiny-guard` snapshot on the volume with
  `model_id="acme/other"` requested → `load()` never called (recording double),
  `model_identity_mismatch` logged, no path in the record.
- **Per-model manifest.** `weights_manifest.json` becomes `{"_comment": [...], "models": {"<model
  id>": {"revision": ..., "files": [...]}}}` — the 22M entry's `revision` and `files` are today's
  content unchanged. `_load_manifest()` (`:566-630`) validates the map and returns the entry for a
  requested id (`read_manifest_pin(path, model_id=...)` `:924`); a document with no `models` map, an
  entry with no `files`, or a file entry missing `path`/`sha256`/`size` keeps today's `manifest_invalid`
  / `manifest_empty` reasons; a requested id with no entry is the new closed reason
  `manifest_model_unknown`. **Scope of a rejection** (security, round 3): a document-level defect (no
  `models` map, unparseable JSON) is `manifest_invalid` / `manifest_unparseable` for **every** model;
  an entry-level defect (a file entry missing `path`/`sha256`/`size`, an empty `files`) refuses only
  the model whose entry is defective and leaves the other entries loadable — tested both ways.
  `verify_weights` selects that entry and **never falls back to another model's entry**; `_acquire_and_load` keeps `weights_pin_unusable` (`:1617`) for an entry with an
  empty file set. `mirror_reference(repository, revision)` (`:1014`) stays revision-keyed — two models
  coexist in one mirror repository because their revisions differ; `docs/weights.md` § "The three
  places the revision appears" (`:27`) says so, **per model**.
- **The shared builders move with the format** (codebase-fit, round 1): `tests/fakes.py::
  weights_manifest_document(files, *, model_id, revision)` (`:157-171`) is the one helper both suites
  build manifests through — callers at `tests/test_model_fetcher.py:175` and `tests/test_app.py:881`
  (`tests/test_app.py:878-892` writes a manifest for the lifespan/acquisition tests, a third affected
  file). It gains the map shape (and accepts several models). The manifest-shaped assertions at
  `tests/test_vendor_weights.py:121-129,269,1366-1383` and `tests/test_model_fetcher.py:196,307,1139,
  1329-1333` (~40 sites) move with it; record the starting `grep -c '"revision"'` for those files
  (ruling R40) and report it beside the ending count in Implementation Notes.
- **Revision resolution is per model and total — the chain, exactly** (ruling R29 as corrected in
  round 3; salty, codebase-fit and security, round 2). `resolve_revision(model_id: str) -> str`:
  (1) the `FORAGE_MODEL_REVISION` override, **shape-checked against `_REVISION_RE` first, exactly as
  today** (`model_fetcher.py:892-902`; the docstring's reason stands — the value is interpolated into a
  filesystem path): a malformed value logs the existing ERROR `model_revision_invalid` (no echo) and
  falls through to step 2, today's loud fallback; a well-formed override is **returned as-is by the
  resolver** (total, exactly as today — so `derive_sanitizer_revision` never raises and a deployment
  with an unpinned override hashes `model_id@<override>` while serving degraded, the honest value);
  the **refusal** of a well-formed override that is not the selected model's pin lives in **exactly
  one place — the top of `_acquire_and_load`**, which compares `resolve_revision(model_id)` with the
  manifest entry's revision and refuses with the closed reason `weights_revision_unpinned` before any
  `snapshot_path()` is computed and before any source is tried (the same placement as
  `manifest_model_unknown`; salty, round 3 — the round-3 "in the resolver / at the top" wording named
  two places, which contradicted "total") — `FORAGE_MODEL_REVISION=main` never reaches a path or a
  download. **The new order at the top of `_acquire_and_load`, exactly** (salty, round 4 — today its
  first statement is `_verify_cached(...)`, `:1594`, which computes `snapshot_path(cache_root,
  MODEL_ID, revision)` at `:1525` before the manifest is read at `:1608`, where a comment justifies
  that placement): (i) `_manifest_entry(manifest_path, model_id)` — a document-level failure or an
  entry that blesses nothing → `weights_pin_unusable` (`:1617`'s message, now emitted **before**
  `_verify_cached` rather than after it; `_load_manifest`'s closed reason is logged as today), a
  missing entry → `manifest_model_unknown`; (ii) `resolve_revision(model_id) != entry.revision` →
  `weights_revision_unpinned`; (iii) only then `_verify_cached(...)`, which takes `model_id` and can
  no longer see a manifest failure the top did not — its `cached.manifest_failure` early return
  **stays as written** (unreachable through the memoised entry, harmless, not worth a diff) and the
  `read_manifest_pin` call at `:1608` is replaced by the entry from (i). The consequence is pinned
  by a test: for a **warm cache plus an unreadable manifest** the reason the operator sees is
  `weights_pin_unusable` (today `_verify_cached` logs the manifest reason first and
  `cached.manifest_failure` returns silently) and `_verify_cached` is never called (recording
  double); (2) the manifest entry's revision for that model; (3) `DEFAULT_MODEL_REVISION`
  (`:174`, stays the 22M constant) **for the default model only**, with one WARNING
  `manifest_pin_unavailable — reason=<manifest_missing|manifest_unreadable|manifest_unparseable|manifest_invalid|manifest_empty>`
  (the closed `_load_manifest` reasons, `:260-265`), so an unreadable manifest at runtime **never
  rotates the default model's `sanitizer_revision`**; (4) the literal closed token **`unpinned`** for
  a non-default model with no entry. The manifest is read at most **once per `(manifest_path,
  model_id)` for the process lifetime** (memoised at module level — the lifespan reads it;
  `derive_sanitizer_revision` runs per request through the `retrieval_app.py:292,1421,1728` fallbacks,
  and already reads the eight hashed source files on every call, `pipeline/sanitizer_revision.py:38-39`,
  so "no manifest read on that path" is the whole claim, never "no I/O"). `unpinned` never reaches a
  path: `_acquire_and_load` refuses `manifest_model_unknown` before any snapshot or hub call, and the
  hash input `f"{model_id}@unpinned"` is deterministic and honest. **The memo has a reset seam**
  (salty, round 3): the manifest read is a private `_manifest_entry(manifest_path: Path, model_id:
  str)` under `functools.lru_cache`, and `tests/conftest.py` gains an autouse fixture (beside the two
  at `:57` and `:63`) calling `model_fetcher._manifest_entry.cache_clear()` so no test sees another
  test's manifest; the open-counting test names the seam. A test asserts
  `DEFAULT_MODEL_REVISION == <the 22M manifest entry's revision>`. `tests/test_model_fetcher.py::
  TestRevisionPin` (eight tests, `:1279+`): the two shape-check tests
  (`test_an_unusable_override_falls_back_to_the_pin`, `test_an_invalid_override_is_reported_without_echoing_it`)
  **survive unchanged**; the pin-equality tests become per-model; say in Implementation Notes which
  of the eight moved. `tests/test_vendor_weights.py::TestSingleSourceOfTruth` moves to the manifest
  entry. `kit_tools/docs/ENV_REFERENCE.md:52`'s `FORAGE_MODEL_REVISION` row reads "the selected
  model's committed pin; a malformed value falls back to the pin with `model_revision_invalid`; a
  well-formed value that is not that pin refuses to verify (`weights_revision_unpinned`)" — and the
  same sentence lands at the four pages that describe the override today
  (`kit_tools/arch/SERVICE_MAP.md:157`, `kit_tools/arch/patterns/LOGGING.md:30,61,101`,
  `kit_tools/docs/TROUBLESHOOTING.md:100` and `docs/configuration.md:108`; ruling R39). **The grep is
  scoped** (R43; codebase-fit, round 3 — the round-3 `docs kit_tools README.md` form pulled in
  `kit_tools/specs/` and 23 files): `grep -rln 'FORAGE_MODEL_REVISION' --include='*.md' docs
  kit_tools/docs kit_tools/arch README.md` — executed 2026-09-19: **13 files** — of which
  `docs/bootstrap-notes.md`, `kit_tools/arch/DECISIONS.md` and the GOTCHAS rotation row are history
  (classified, untouched); the six pages that *describe the override* (`ENV_REFERENCE.md`,
  `SERVICE_MAP.md`, `LOGGING.md`, `TROUBLESHOOTING.md`, `docs/configuration.md`, `docs/weights.md`)
  each carry the sentence (`grep -c weights_revision_unpinned <page>` ≥ 1 each); the remaining hits
  (`DEPLOYMENT.md`, `LOCAL_DEV.md`, `MONITORING.md`, `kit_tools/arch/SECURITY.md`, GOTCHAS outside the
  table) are classified in Implementation Notes as mention-only or rewritten.
- **The generator writes the map — and diffs it.** `scripts/vendor_weights.py` gains `--model-id`
  (default `DEFAULT_MODEL_ID` — the supply-chain tool does **not** read `FORAGE_MODEL_ID`; an explicit
  flag always wins), `generate_manifest()` (`:446`) — which today neither reads nor merges an existing
  document (codebase-fit, round 4) — is taught to read the committed document first, then write or
  replace that model's entry and keep the others byte-stable; `read_manifest_document` (`:486`), `manifest_diff` (`:501`, run at `:981` in
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
  `model_fetcher.py` row; **`kit_tools/docs/TROUBLESHOOTING.md`** (completionist, round 2): `:270-271`
  ("check `FORAGE_MODEL_REVISION` against the `revision` in `weights_manifest.json`") becomes
  per-model and names `weights_revision_unpinned`, and the grep recipes at `:87-100` gain
  `manifest_model_unknown` and `weights_revision_unpinned` (US-006 adds its own three);
  `kit_tools/arch/SECURITY.md`'s non-vulnerabilities table gains the row "verifier/loader directory
  agreement — fixed by `hardening-promptguard-86m` US-001" so the fix is on the record.

**Acceptance Criteria:**
- [x] `load(*, model_id=...)` exists on `PromptGuardClassifier` and on `SupportsWeightLoad`; a test with
      a stubbed auto-class asserts both `from_pretrained` calls receive the requested id
      (`tests/test_model_fetcher.py:790-791` extended); `WeightAcquisition(..., model_id=...)` stores
      the id and `attempt_once` forwards it to `acquire_and_load` (recording double, the
      `tests/test_app.py:1183` monkeypatch shape); `acquire_and_load(..., model_id=...)` threads the
      id and the resolved revision through `_verify_cached`, `_download_from_hub`, `_try_source` and
      `_load_verified` (asserted on a recording double).
- [x] `verify_weights(..., model_id=, revision=)` verifies the requested pair; a `FORAGE_MODEL_REVISION`
      that is not the pin yields `weights_revision_unpinned` (closed reason, no path in the log) from
      the top of `_acquire_and_load` — the one refusal site — and `_load_verified(...,
      manifest_path=)` derives both paths itself and asserts the manifest-derived
      `snapshot_path(...)` equals the resolved-revision one and exists before `load()` (no
      `_try_source` / `_fetch_from_mirror` return-shape change — their `-> str` annotations are
      untouched in `git diff`) — the two-directory test from the Independent Test passes and the
      unpinned directory's files are never opened.
- [x] The loaded-identity check lives in `_load_verified` (existence plus equality): a warm snapshot
      of a different model under the requested id yields `model_identity_mismatch`, `load()` is never
      called (recording double), and a test asserts that specific message; a direct `_load_verified`
      call with a `revision` that is not the entry's yields the same (second test); the classifier's own
      pre-flight is the `cache_dir` existence check as an early-return block outside the blanket
      `except` (`model_cache_dir_missing`, tested); `kit_tools/arch/SECURITY.md`'s stage-3 paragraph
      carries the residual sentence "the classifier trusts the path the verifier handed it"
      (`grep -c 'trusts the path' kit_tools/arch/SECURITY.md` is 1).
- [x] `weights_manifest.json` is a per-model map; the 22M entry's `revision` and `files` are
      byte-identical to today's; `_load_manifest` / `read_manifest_pin(model_id=...)` /
      `verify_weights(..., model_id=...)` select the entry; a missing entry yields `manifest_model_unknown`
      even with `FORAGE_MODEL_REVISION` set (test); `manifest_invalid` / `manifest_empty` semantics
      unchanged (tests); `tests/fakes.py::weights_manifest_document` builds the map and
      `tests/test_app.py:878-892` passes against it.
- [x] `resolve_revision(model_id)` is total in the four-step order above (a well-formed override is
      returned, never refused, by the resolver); a test asserts `DEFAULT_MODEL_REVISION == <the 22M
      manifest entry's revision>`; `resolve_revision` reads the manifest at most once per
      `(manifest_path, model_id)` through `_manifest_entry` under `lru_cache` — a test counts opens
      **of the manifest file** (not all opens: `pipeline/sanitizer_revision.py:38-39` reads eight
      sources per call by design) across two `derive_sanitizer_revision({})` calls, and
      `tests/conftest.py`'s autouse `cache_clear()` fixture is the reset seam (named); `derive_sanitizer_revision({})` never
      raises for any id; with the manifest unreadable, the default model resolves to
      `DEFAULT_MODEL_REVISION` with one `manifest_pin_unavailable` WARNING and the hash value is the
      pre-story value (test).
- [x] `FORAGE_MODEL_REVISION` set to a non-40-hex value keeps `model_revision_invalid` + fall-back-to-pin
      (the two existing `TestRevisionPin` tests pass unchanged); set to a 40-hex value that is not the
      selected model's pin, the refusal `weights_revision_unpinned` fires before any `snapshot_path`
      is computed and `_download_from_hub` is never called (recording doubles) — both tests named;
      the top-of-function order (entry → `manifest_model_unknown` / `weights_pin_unusable` →
      `weights_revision_unpinned` → `_verify_cached`) is pinned: a warm cache plus an unreadable
      manifest yields `weights_pin_unusable` with `_verify_cached` uncalled (test).
- [x] `scripts/vendor_weights.py --model-id` writes the map form, preserves other entries, and
      `manifest_diff` scoped to that entry reports only the added model for a one-model → two-model
      diff (test); the seven diff tests at `tests/test_vendor_weights.py:559-616` pass against the
      keyed shape; a round-trip test parses the output with `_load_manifest`.
- [x] `ALLOWED_SUFFIXES == frozenset({".safetensors", ".json", ".txt", ".model"})` and `ALLOW_PATTERNS`
      are asserted unchanged; `grep -c 'use_safetensors=True' promptguard/classifier.py` is **2** (the
      explanatory comment at `:90` and the call-site keyword at `:100` — executed 2026-09-19; the
      round-4 "1 hit" was un-executed, R43) and `grep -n '^ *use_safetensors=True,'
      promptguard/classifier.py` returns exactly the call site.
- [x] `grep -n '\bMODEL_ID\b' model_fetcher.py pipeline/sanitizer_revision.py scripts/vendor_weights.py
      promptguard/classifier.py` returns nothing (pre-story 20 hits, recorded) and `grep -rnw MODEL_ID
      tests --include='*.py'` returns nothing (pre-story 47) — alias removed, the three prose sites
      rewritten; `derive_sanitizer_revision({})` equals the pre-story value at the default model
      (revert-and-reproduce, recorded in Implementation Notes; `git diff --stat` against the eight
      `_REVISION_SOURCES` files is empty); the loaded-identity check compares paths only and no
      `repo_dirname` copy exists outside `model_fetcher.py` (`grep -rn 'def repo_dirname' --include='*.py'
      . --exclude-dir=.venv` returns the one definition).
- [x] `docs/weights.md` and `ENV_REFERENCE.md:52` describe the per-model manifest, the per-model pin
      and the `weights_revision_unpinned` refusal (`grep -c 'per model' docs/weights.md` ≥ 1,
      `grep -c weights_revision_unpinned docs/weights.md kit_tools/docs/ENV_REFERENCE.md` ≥ 1 each);
      the scoped grep `grep -rln 'FORAGE_MODEL_REVISION' --include='*.md' docs kit_tools/docs
      kit_tools/arch README.md` (13 files pre-story) has every hit classified — the six
      override-describing pages each `grep -c weights_revision_unpinned` ≥ 1, history sites untouched,
      the rest classified in Implementation Notes; `kit_tools/docs/TROUBLESHOOTING.md` names
      `manifest_model_unknown` and `weights_revision_unpinned` (`grep -c` ≥ 1 each) and `:270-271` is
      per-model; the SECURITY.md non-vulnerabilities row is present; the R40 start and end counts are
      in Implementation Notes.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-006: `FORAGE_MODEL_ID` — allowlisted, refuse-boot, asserted at load, reported on `/health`

**Priority:** P1

**Description:** As an operator, I want to select the classifier with one environment variable and
see which model the service is configured with on `/health`, so the 86M upgrade is an opt-in I can
measure, not a fork of the image — while a typo or an unknown model can never be loaded, and while an
allowlist entry that nothing can serve is never shipped.

**Independent Test:** With the allowlist monkeypatched to include a second id (`acme/second-guard`,
so the test does not depend on US-005) and a recording `SupportsWeightLoad` double,
`FORAGE_MODEL_ID=acme/second-guard` makes the ASGI lifespan construct `WeightAcquisition(...,
model_id="acme/second-guard")` whose first `attempt_once` calls `acquire_and_load(...,
model_id="acme/second-guard")` (recorded through the `tests/test_app.py:1183` monkeypatch), both
stubbed `from_pretrained` calls receive it, `/health` reports
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
  `DEFAULT_MODEL_ID`; **set-but-blank is identical to unset** (`os.environ.get(...,"").strip()` then
  the default — the `resolve_revision` / `resolve_cache_root` / `_resolve_token` convention in the
  same module), never a refusal. **The return shape, exactly** (salty, round 3): `resolve_model_id()
  -> tuple[str, bool]` — `(<id>, True)` for unset, blank or an allowlisted value; `(DEFAULT_MODEL_ID,
  False)` plus one WARNING `model_id_not_allowed` (never echoing the value) for anything else — so
  the resolver is total and the per-request `derive_sanitizer_revision` fallbacks
  (`retrieval_app.py:292,1421,1728`), which read element 0 only, can never 500; the
  **lifespan** performs the one refusing check and raises `ModelConfigurationError(ValueError)` (new, in
  `model_fetcher.py`, on the `CacheConfigurationError` `cache.py:232` /
  `SearchProviderConfigurationError` `pipeline/search_providers/__init__.py:50` pattern —
  `tests/test_app.py:1208-1221` shows the lifespan-refusal test shape). `_MODEL_ID_RE` (`:324`) stays a
  shape check. Tests that need a second allowlisted id monkeypatch `ALLOWED_MODEL_IDS` (parametrised),
  so US-005's later addition changes no test.
- **Feed every consumer through the lifespan** (completionist, round 1; seam corrected in round 4):
  the lifespan passes `model_id=resolved` to `model_fetcher.WeightAcquisition(...)`
  (`retrieval_app.py:1322-1329`, the constructor US-001 extended) — it never calls `acquire_and_load`
  itself; `pipeline/sanitizer_revision.py:40` hashes
  `f"{resolved}@{resolve_revision(resolved)}"` (the module is not in `_REVISION_SOURCES` `:12-21`, so
  the source edit rotates nothing; the *input* changes only when a non-default id is configured);
  `scripts/vendor_weights.py` keeps `DEFAULT_MODEL_ID` as its `--model-id` default (US-001). Update
  `tests/test_sanitizer_revision.py:51-61` (monkeypatches `sanitizer_revision.MODEL_ID` — a name that no
  longer exists) and `:95-118`. The end-to-end assertion runs through the ASGI lifespan with a
  recording double in the `tests/test_app.py:1171-1183` shape (`_record(classifier, *, metrics=None,
  **kwargs)` monkeypatched over `model_fetcher.acquire_and_load`; assert `kwargs["model_id"]`), not
  only through the parameter. **Allowlisted ⇒ serveable is a test, not a convention** (security,
  round 3): `test_every_allowlisted_model_has_a_manifest_entry` iterates `ALLOWED_MODEL_IDS` and
  asserts `read_manifest_pin(<committed manifest>, model_id=...)` returns an entry for each — one
  member today; US-005's commit keeps it green by adding the entry and the id together.
- **Assert `id2label` at load, don't assume index 1** — and say how `load()` is restructured so the
  check is an early return rather than a branch inside the blanket `except` (salty, round 4: today
  the whole body — both `from_pretrained` calls, `model.eval()`, the assignment and the success
  return — sits inside one `try`, `classifier.py:71-116`, and `model` exists only there, so US-001's
  pre-flight pattern, which runs *before* the `try`, does not transfer): the imports and the two
  `from_pretrained` calls stay in the `try`, which now binds `model` and `tokenizer` to names
  declared before it and falls through instead of returning; the label check runs **after** the
  `try` block on the bound `model` as an explicit early-return block
  (`logger.warning("model_labels_unexpected"); return False`); `model.eval()`, the assignments and
  the success `return True` move below the check. A `model.config` with no `id2label`, a `None`, or a
  non-mapping value is a **third negative case** producing `model_labels_unexpected`, never the
  generic "PromptGuard model not available" (tested beside the two below). `classifier.py:27-29` says "the
  older 86M had 3 classes" — that was Prompt Guard *1*. Read `model.config.id2label`: exactly one label
  `INJECTION` and one `BENIGN` (case-insensitive); set the instance attribute
  `_injection_label_index` from it — the module constant `_INJECTION_LABEL_INDEX` (`classifier.py:29`)
  becomes that attribute's default, the read at `:198` moves with the loop into `classify_windows`
  (US-002), and the stale comment at `:27-28` is corrected in the same edit; **cardinality is part of
  the check** (ruling R29 as corrected in round 4; security, round 3): `len(id2label) == 2` exactly —
  a three-label config with `INJECTION` and `BENIGN` present is refused too; otherwise `loaded` stays
  `False`, WARNING `model_labels_unexpected` (a test asserts that specific message; negative cases:
  `{LABEL_0, LABEL_1}` and a three-label `{BENIGN, INJECTION, JAILBREAK}`).
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
  `kit_tools/arch/CODE_ARCH.md`'s `retrieval_app.py` row; `NOTICE:16-18` is edited only by US-005;
  **`kit_tools/docs/TROUBLESHOOTING.md`** (completionist, round 2): `:635-636`'s "nothing else refuses
  boot" sentence names `ModelConfigurationError` beside the two existing classes, the grep recipes at
  `:87-100` gain `model_id_not_allowed`, `model_identity_mismatch` and `model_labels_unexpected`, and
  § "Weights verified but `promptguard_loaded` stays `false`" (`:280`) lists the two load-time
  refusals; `kit_tools/docs/GOTCHAS.md`'s "PromptGuard model absent → degraded" entry gains one
  sentence on the allowlist refusal. **The per-rule counters are a differential channel** (security,
  round 2): a prober who can post content and read `/metrics` can recover the contiguity settings by
  bisection even though `/health` withholds them — the repo's rule is "no second differential channel
  is *claimed*, not secrecy", so `kit_tools/arch/SECURITY.md`'s non-vulnerabilities table gains a row
  saying the settings are not published but are inferable from `promptguard_contiguity_detections`,
  and the `promptguard_model` description says "not published", never "not inferable".

**Acceptance Criteria:**
- [x] `FORAGE_MODEL_ID` unset → `/health.promptguard_model == "meta-llama/Llama-Prompt-Guard-2-22M"`; the
      model-identity hash input is byte-identical to explicitly setting the 22M id (relative assertion
      in `tests/test_sanitizer_revision.py`); a second allowlisted id (monkeypatched) → a different
      `derive_sanitizer_revision({})` (asserted).
- [x] A value outside the allowlist — including the 86M id at the shipped allowlist — makes the
      lifespan raise `ModelConfigurationError`; the log carries `model_id_not_allowed`; a sentinel value
      appears 0 times in captured logs; `derive_sanitizer_revision` never raises for any allowlisted
      id (test); `ALLOWED_MODEL_IDS` contains exactly the 22M id at the end of this story.
- [x] An app-startup test with `FORAGE_MODEL_ID=<second id>` asserts the lifespan's
      `WeightAcquisition` carries the resolved id and `acquire_and_load` received it (the
      `tests/test_app.py:1183` monkeypatch shape), and a recording `SupportsWeightLoad` double shows
      both `from_pretrained` calls saw it (through the ASGI lifespan); `resolve_model_id()` returns
      `tuple[str, bool]` as specified (tests for unset, blank, allowlisted, disallowed).
- [x] `load()` derives the injection index from `model.config.id2label` (exactly two labels: one
      `INJECTION` and one `BENIGN`, case-insensitive) and refuses `{LABEL_0, LABEL_1}` and a
      three-label config and a missing / `None` / non-mapping `id2label` — `loaded` stays `False`,
      WARNING `model_labels_unexpected`, the specific message asserted (three negative tests); the
      check and the success return sit after the `try` block, not inside it.
- [x] `test_every_allowlisted_model_has_a_manifest_entry` exists and passes against the committed
      `weights_manifest.json` for every `ALLOWED_MODEL_IDS` member.
- [x] `/health.promptguard_model` reports the configured id unconditionally, read from `app.state` and
      never re-read in the handler (test); its description carries the publishable-identity clause
      ("not published", never "not inferable"); `kit_tools/arch/SECURITY.md`'s `/health` paragraph
      carries the same sentence and its non-vulnerabilities table carries the differential-channel
      row (`grep -c promptguard_model kit_tools/arch/SECURITY.md` ≥ 2); `FORAGE_MODEL_ID` set-but-blank
      behaves as unset (test).
- [x] Window block done: docstring line appended; regenerated; `tests/golden/contract_1_3_0.json`
      re-created from `_SCHEMA_MODELS`; `HealthResponse.promptguard_model` appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF`; the 1.2.0 pair untouched and green; four anchor pages refreshed;
      `uv run python -m scripts.export_contract --check` clean; the rotation (`contract.py`) measured
      and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`,
      `kit_tools/docs/GOTCHAS.md`'s rotation table (`:410-434`) and `kit_tools/arch/CODE_ARCH.md`.
- [x] `_CLEARED_ENV_VARS` and the exact-set test include `FORAGE_MODEL_ID`; `grep -c FORAGE_MODEL_ID
      compose/minimal.yml compose/full.yml docs/configuration.md kit_tools/docs/ENV_REFERENCE.md` ≥ 1
      each; `grep -c promptguard_model kit_tools/docs/API_GUIDE.md kit_tools/docs/MONITORING.md` ≥ 1
      each; `docs/configuration.md`'s row states the 86M is added by the vendoring gate;
      `kit_tools/docs/TROUBLESHOOTING.md:636` names `ModelConfigurationError` and each of
      `grep -c model_id_not_allowed`, `grep -c model_identity_mismatch` and `grep -c
      model_labels_unexpected` over `kit_tools/docs/TROUBLESHOOTING.md` is ≥ 1 (three separate
      counts — `grep -c` with an alternation counts lines, not tokens; salty, round 3); the GOTCHAS
      sentence is present.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

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
  the trusted-tier-skip guards) and `:172` (`classify.assert_called_once()`). **Timing, decided**
  (completionist, round 2): stage 3 keeps calling `classify()` in this story, so `:172` stays exactly
  as it is here and is re-pointed by US-007 when the call seam moves; `:151` and `:164` are extended
  in this story to assert on **both** attributes (`classify` and `classify_windows` not called), so
  they cannot pass vacuously in either story; a starting `grep -c 'classify\.' tests/` count is
  recorded. Anchors: `TestClassifierUnit` `:279-297`, `TestChunking` `:300-348`;
  `test_score_exactly_at_threshold_is_safe` (`:63`), `test_score_just_above_threshold` (`:101`).
- **`classify()` is retained on purpose** as the classifier's public single-score entry point: its
  callers after US-007 are the real-loader tests (`tests/test_model_fetcher.py:2040,2370,3827`,
  `tests/test_stage3_promptguard.py:288`); stage 3 is its only removed production caller (US-007).

**Acceptance Criteria:**
- [x] `classify_windows` exists and `classify` is implemented through it; every pre-existing stage-3
      and orchestrator verdict/score assertion passes with the doubles updated; `:151` and `:164`
      assert on both attributes; `:172` is untouched (US-007 re-points it); stage 3 still calls
      `classify()` (`grep -c 'classifier.classify,' pipeline/stage3_promptguard.py` is 1).
- [x] `git diff --stat` against the eight `_REVISION_SOURCES` files is empty;
      `derive_sanitizer_revision({})` equals the pre-story value (recorded).
- [x] `kit_tools/arch/CODE_ARCH.md`'s `promptguard/` row names `classify_windows`.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

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
union in document order; with the rule enabled, a classifier double returning `[0.6, 0.6]` for a
`/search` result makes the ASGI `/search` end-to-end test omit that result with `injection_detected`
(the behaviour this story owns); and the window-count facts are pinned as **content-dependent**
against the repo's only real tokenizer (`tests/fixtures/tiny_model`): a maximum-length prose result
(title 512, url 2,048, snippet 2,000 characters — the `orchestrator.py:583-585` caps, sum 4,583)
tokenises to 1,004 tokens → 3 windows, and a repeated-run text of the same length tokenises to 442
tokens → 1 window (measured, round 2).

**Implementation Hints:**
- **The rule lives in stage 3** (`pipeline/stage3_promptguard.py:128-150`, hashed → ruling 6): call
  `classify_windows` (US-002); verdict is `INJECTION_DETECTED` when `max_score > threshold` **or** when
  any run of at least `contiguity_windows` consecutive scores is `>= contiguity_threshold`.
  `PromptGuardResult` (`:31`) gains `rule: Literal["max_score", "contiguity", "both"] | None`;
  `flagged_chunks` is the max-scoring chunks, the contiguous run, or their union in document order
  (deduplicated); `score` stays `max_score`. `PromptGuardResult` is **not** internal:
  `pipeline/stage4_structuring.py:165` copies `flagged_chunks` into `injection_spans`, and only
  `finalize_quarantine` (`:178-210`) keeps hostile text off the wire by replacing the spans with a
  closed diagnostic label on any `INJECTION_DETECTED` verdict — a `contiguity` or `both` verdict must
  produce the diagnostic label only (extend `tests/test_orchestrator.py:2038,2068` to a multi-chunk
  union). `stage4_structuring.py` is byte-unchanged (`git diff --stat`), so the ledger's three-file claim
  is checked, not assumed. The orchestrator's omission log at `orchestrator.py:1032` names the rule.
- **Config keys** (`config.yaml` beside `promptguard_threshold` `:15`): `promptguard_contiguity_windows`
  (int, **default `0` = disabled**, otherwise **2–8** — `1` refuses boot with
  `PromptGuardConfigurationError`, because a one-window run is nothing but a second max rule at a
  lower threshold, which the max rule's own key already expresses; salty and security, round 3) and
  `promptguard_contiguity_threshold` (float, default
  `0.5`, range 0.0–1.0, **absolute and server-side** — it does not track a per-request
  `promptguard_threshold`; the docs say a caller who lowers the max threshold below the contiguity
  threshold gets the stricter of the two, and that this is why the rule ships off). Read through the
  house triple (codebase-fit, round 2): a `promptguard_settings_from_config(config) ->
  PromptGuardSettings` builder and a `PromptGuardConfigurationError(ValueError)` **in
  `pipeline/stage3_promptguard.py`** (the rule's home), called once from the lifespan beside
  `retrieval_app.py:1215/1242/1282`, **re-stating the bounded-read idiom locally** the way
  `brave.py`'s `_bounded_float` (`:206`) and `_bounded_int` (`:224`) do — the `_bounded_*` helpers
  are private per module by decision and pyright strict forbids cross-module private use —
  registered in `KNOWN_CONFIG_KEYS` (spec 3 US-003).
  Configuration-only by design: both values enter the content-cache key through
  `derive_sanitizer_revision` — appended to the digest **after** `promptguard_threshold`
  (`pipeline/sanitizer_revision.py:41`), windows then threshold, as ASCII — and the independent
  recomputation in `tests/test_sanitizer_revision.py::test_the_hashed_model_identity_is_model_id_at_revision`
  (`:95-116`; `expected.update(b"0.85")` at `:111`) is extended with the same two updates in the
  same order (codebase-fit, round 3); a test pins `cache_policy_fingerprint()`'s inputs so a future
  per-request override cannot slip past the cache key.
- **All three routes.** Thread both values through the threshold seam spec 3 US-005 creates to
  `run_promptguard(...)` (`:42-48`) from `sanitize_and_structure` (`orchestrator.py:160-207`, the call
  at `:182` — serves `/retrieve` `:368` and both `/extract` paths `:464`, `:540`) and the `/search`
  loop's call (`orchestrator.py:1012-1037`, `run_promptguard` at `:1014`).
  **The rule can fire on `/search`** (codebase-fit, round 1): `_search_result_promptguard_input`
  (`:665-667`) concatenates title, url and snippet capped at 512 / 2,048 / 2,000 characters
  (`orchestrator.py:583-585`, sum 4,583). **Window count is a property of tokenisation density, not
  length** (codebase-fit and salty, round 2): `_chunk_text` returns `[text]` whenever the token count
  is ≤ 512 (`classifier.py:133-136`), and the same 4,583 characters measure 442 tokens (1 window) as
  repeated runs and 1,004 tokens (3 windows) as prose against `tests/fixtures/tiny_model`. So the
  tests are: (a) hermetic — a double returning ≥ 2 window scores makes the rule fire on the `/search`
  path end to end; (b) both tokenisation shapes pinned against the fixture tokenizer (prose → ≥ 2
  windows, repeated runs → exactly 1, the rule inert by construction); (c) the real-tokenizer window
  count of a maximum-length result is **measured at US-004's gate**, not pinned here. The docs and the
  corpus-epic handoff say `/search` coverage is content-dependent; no "plumbing / single-window"
  docstring anywhere.
- **Telemetry.** One additive counter per existing section model on `/metrics` —
  `retrieve.promptguard_contiguity_detections` (`RetrieveMetricsResponse`),
  `search.promptguard_contiguity_detections` (`SearchMetricsResponse`) and
  **`extraction.promptguard_contiguity_detections`** (`ExtractionMetricsResponse`; the section is
  `extraction`, `retrieval_app.py:668`, never `extract`) — incremented when `rule` is `contiguity` or
  `both`; window change (`tests/test_contract_metrics.py` pins; the section models are `extra="forbid"`
  — GOTCHAS "Adding a `/metrics` counter without adding it to the model 500s the endpoint"); plus one
  **WARNING** `promptguard_contiguity_verdict — run=<n> windows=<m>` at a contiguity verdict (run length
  and window count only, never a score list or text — INFO never renders in the container, GOTCHAS
  "Nothing configures logging", so a rule with a documented false-positive residual must be visible
  when it fires; security, round 2); the docs say `/metrics` is the aggregate signal and the WARNING
  the per-event one. **MONITORING fan-out, by value** (salty, round 3; R39/R43):
  `kit_tools/docs/MONITORING.md`'s three section tables (`### extraction` `:121`, `### search`
  `:138`, `### retrieve` `:156`) each gain the counter row; its `sanitizer_revision` row (`:64`,
  "`MODEL_ID@revision` plus `promptguard_threshold`") reads "`model_id@revision` plus
  `promptguard_threshold` plus the two contiguity values"; and its log-lines table (`:285-290`)
  gains `promptguard_contiguity_verdict` — `grep -c promptguard_contiguity_detections
  kit_tools/docs/MONITORING.md` is 3 and `grep -c promptguard_contiguity_verdict` ≥ 1 (both 0 today).
  Decision
  recorded: dedicated counters rather than a new key in `retrieve.blocked_by_reason` /
  `search.omitted_by_reason` (`retrieval_app.py:554-559`, `:500-506`), following the flat-counter
  precedent of `fallback_fired` / `paid_calls` / `policy_unknown_provider` (`:510-535`) and avoiding a
  closed-vocabulary growth.
- **`promptguard_threshold`'s meaning moves** (salty, round 2): the per-request field (`models.py:258`)
  governs the max-score rule only; the server-side contiguity rule can block independently of it, so
  a caller who raises the threshold to be permissive can still see `injection_detected` when the
  rule is on. The field description says so in one clause (a description edit — the window block's
  regenerate covers the export; classification recorded in this story as a description-only change
  under GOVERNANCE row 3, no property moves), and `docs/configuration.md`'s row says the same.
- **Window block (ruling R36):** docstring line (R34 format); `uv run python -m scripts.export_contract`;
  `tests/golden/contract_1_3_0.json` re-created from `_SCHEMA_MODELS` (the counters are pinned by
  `tests/test_contract_metrics.py`, not the golden, and the `promptguard_threshold` description edit
  is description-only — so the golden moves by that description and nothing else);
  **this story appends nothing to `_EXPECTED_ONE_THREE_ZERO_DIFF`** (ruling R36 as corrected in round
  4: `_added_paths` sees new properties and enum members only; a description or bound move appends
  nothing, and its gate is `test_contract_schema_matches_golden` after the golden is re-created —
  US-006, which adds a field, is the story that appends a path); four anchor pages; `--check`.
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
- [x] The six verdict cases in the Independent Test plus `[0.2, 0.6, 0.6]` (run at the end fires), a
      score exactly at `contiguity_threshold` counts (`>=`), a score exactly at `promptguard_threshold`
      does not fire the max rule (`>`), and an empty text (`SAFE`) are pinned by tests.
- [x] Both keys validated at boot (out-of-range refuses like
      `tests/test_app.py::test_lifespan_refuses_an_out_of_range_cache_bound`; `windows: 1` refuses
      with `PromptGuardConfigurationError`, tested), defaults `0` / `0.5`,
      registered in `KNOWN_CONFIG_KEYS`, documented in `docs/configuration.md` with the enabling recipe
      and the absolute-threshold note.
- [x] All three routes apply the rule (one end-to-end test per route through the ASGI app with the
      mocked classifier); the `/search` firing case (double returning ≥ 2 window scores) is tested;
      both tokenisation shapes are pinned against `tests/fixtures/tiny_model` (prose → ≥ 2 windows,
      repeated runs → 1 window, rule inert); `tests/test_stage3_promptguard.py:172` is re-pointed to
      `classify_windows.assert_called_once()` here; `models.py`'s `promptguard_threshold` description
      and `docs/configuration.md` state the max-rule-only scope.
- [x] A `contiguity` or `both` verdict returns only the diagnostic label in `injection_spans` (the
      `tests/test_orchestrator.py:2038,2068` shape extended to a multi-chunk union);
      `stage4_structuring.py` is byte-unchanged.
- [x] The three counters exist on the named section models, are pinned by
      `tests/test_contract_metrics.py`; the window block is done (docstring line, regenerate, golden
      re-created, **nothing appended** to `_EXPECTED_ONE_THREE_ZERO_DIFF` — stated in Implementation
      Notes — anchor pages, `--check` clean).
- [x] `derive_sanitizer_revision` includes both values after `promptguard_threshold`;
      `tests/test_sanitizer_revision.py:95-116` recomputes them in the same order; a test pins
      `cache_policy_fingerprint()`'s inputs; the rotation is measured and recorded at the five sites (`docs/bootstrap-notes.md`,
      `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md`'s rotation table,
      `kit_tools/arch/CODE_ARCH.md`).
- [x] SECURITY.md's stage-3 paragraph names both rules and both residual directions; GOTCHAS entry
      present and states that `/metrics` is the aggregate signal and the WARNING the per-event one;
      `kit_tools/docs/MONITORING.md` carries the three counter rows, the `:64` hash-input sentence and
      the WARNING row (the two `grep -c` counts above); the corpus-epic handoff line in Implementation Notes names both shapes and says `/search`
      coverage is content-dependent; `kit_tools/arch/CODE_ARCH.md`'s stage-3 row names the rule; the
      config triple (`promptguard_settings_from_config`, `PromptGuardConfigurationError`, one lifespan
      call) exists and an out-of-range value refuses boot with that error.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-003: Benchmark harness — host-side, against the running service

**Priority:** P2

**Description:** As the owner, I want a repeatable script that drives a running Forage container with
fixed synthetic documents and reports service-level latency and container memory, with a committed
benchmark configuration and every failure path defined, so the 22M-vs-86M question is answered for the
*service*, not a bare interpreter, by a table anyone can regenerate.

**Independent Test:** `uv run pytest tests/test_bench_promptguard.py` passes under the socket guard with
injected fakes (no Docker, no network, no weights) and `uv run python -m scripts.bench_promptguard
--help` exits 0; the percentile test pins p50 = 10 and p95 = 19 for samples `[1..20]`; a fake
`/extract` returning 404 (the gated route's status, `retrieval_app.py:1688`, documented at
`:1624-1628` — never 403) makes `main` exit 2 with a stderr line naming the status and the benchmark
config mount, writing no JSON; a fake `/health` that never reports `healthy` with
`promptguard_loaded: true` makes `main` exit 2 (`never_healthy`) writing no JSON; both documents
sized against `tests/fixtures/tiny_model` are within their token bounds by the harness's arithmetic
and by `PromptGuardClassifier._chunk_text`; `bench/config.yaml` parses through `_load_config` and
every key is a `KNOWN_CONFIG_KEYS` member.

**Implementation Hints:**
- **Location and shape — reuse the existing driver** (codebase-fit, round 1): the repo already has
  two host-side drivers built for a running container with this exact architecture.
  `contract_smoke.py` provides `HttpResponse` (`:191`), `CommandResult` (`:205`), `http_get(url)`
  (`:223`), `wait_for_health(...)` (`:256` — polls `/health` until `status` matches, with a deadline:
  US-004's "wait for `promptguard_loaded`" step), `run_command(argv)` (`:434`) and a `main(argv)` +
  argparse shape (`:669`) — **public names only**: `_json_object` (`:302`) is private and `scripts/`
  sits under the strict pyright root where `reportPrivateUsage` is not relaxed (`pyproject.toml`;
  only `tests/` has the carve-out), so the harness parses `/health` and `/metrics` bodies itself
  (`json.loads` + `isinstance(..., dict)`, three lines; codebase-fit, round 4); `searxng_smoke.py:493` already shells `docker inspect
  --format '{{.State.Status}} {{.State.ExitCode}}'`. **Scope the reuse honestly** (salty and
  codebase-fit, round 2): `http_get` and `wait_for_health` are reused for the `/health` reads only
  (`http_get` is urllib, GET-only, with a module-level `REQUEST_TIMEOUT_SECONDS = 10.0` at `:171` and no
  parameter), `run_command` for `docker stats`; the multipart `POST /extract` is **new code** in this
  module — **`httpx.post(url, files={"file": (name, bytes, "text/plain")}, data={"filename": name,
  "extract_mode": "full"}, timeout=...)`**, the `tests/test_orchestrator.py:1891-1898` `files=`/`data=`
  shape (codebase-fit, round 3; `httpx` is a runtime dependency, `pyproject.toml:14`, already imported
  by `pipeline/search_providers/searxng.py`, so no hand-built boundary and no encoded-body test) —
  carrying the `file` part (`retrieval_app.py:1664`), the required `filename` form field (`:1665`)
  and `extract_mode=full` (`:1667`) — behind the injectable seam `post_fn(url, *, file_name: str,
  file_bytes: bytes, fields: dict[str, str], timeout_seconds: float) -> HttpResponse`; the unit tests
  inject a fake `post_fn` and never construct an `httpx` client. Importing `contract_smoke` pulls `retrieval_app`, the
  `pipeline` package, pydantic and `scripts.export_contract` in transitively (`contract_smoke.py:129-134`);
  that is accepted (nothing at module scope reads config or the network, so `--help` still exits 0
  offline) and `searxng_smoke.py`'s self-contained `run_command` (`:196`) is a known divergence, not the
  precedent. `scripts/bench_promptguard.py` imports those pieces (`from contract_smoke import ...` —
  the repo root is on `sys.path` under `uv run python -m`); the injectable seams are `post_fn`,
  `stats_fn` and `clock`, and
  the `clock` double is `tests/fakes.py::ManualClock` (`:174-193`). **The harness does its own health
  wait** (codebase-fit, round 3): `--health-timeout-seconds` (default 900) feeds one call
  `wait_for_health(base_url, expect_status=STATUS_HEALTHY, timeout_seconds=<that>,
  poll_interval_seconds=5, fetch=<the injected GET>)` (signature `contract_smoke.py:256-266`; the
  default `expect_status` is `degraded`, `:154`, which would return on the first 200 before the model
  loads) before the cold request — **and checks what came back** (ruling R29 as corrected in round 5;
  salty, round 4: `wait_for_health` does not raise on timeout, it returns the last response seen,
  `contract_smoke.py:292-298`; and a degraded service does not answer non-2xx on `/extract` — the
  route hard-codes `promptguard_fail_closed=True`, `orchestrator.py:545`, stage 3 returns
  `INJECTION_DETECTED` / `skip_reason=model_unavailable` in microseconds, `stage3_promptguard.py:99-107`,
  and the handler answers 200 with the diagnostic label, so an unloaded classifier would yield a
  clean-looking row that measured nothing but FastAPI): the harness parses the returned body and
  requires `status == "healthy"` **and** `promptguard_loaded is True`; anything else is the
  configuration failure `never_healthy` — exit 2, a stderr line naming the timeout, the last `status`
  seen and `--container` when given, **no JSON, no rows**. A unit test with an injected `fetch` that
  never reports healthy asserts exit 2 and no file; a second asserts a body with `status: healthy`
  but `promptguard_loaded: false` (impossible today, cheap to pin) is refused the same way; a third
  asserts the call's `expect_status == STATUS_HEALTHY`; US-004's recipe relies on it.
  `promptguard_loaded`, `promptguard_model`, `sanitizer_revision` and `contract_version` are read from
  that same `/health` body into the row. `tests/test_contract_smoke.py` is
  the hermetic test pattern ("pure evaluators plus an injectable fetcher"). The module goes in
  `scripts/` because it is owner tooling like `vendor_weights.py`; both `scripts/` (`.dockerignore:26`)
  and the root drivers (`Dockerfile:158-160`'s enumerated COPY list, guarded by
  `tests/test_dockerfile.py:559-603`) stay out of the image. `scripts/` is type-checked under the strict
  root (`pyproject.toml`): no private access, no suppression comments. A module-docstring
  `test_mapping:` block like `contract_smoke.py:112`.
- **Arguments**: `--tokenizer-dir <path>` (required for a measurement run — argparse `required=True`
  keeps `--help` working; validated `Path(...).is_dir()` before any request; the selected model's
  snapshot directory, of which only the tokenizer files are read), `--model-id` (default
  `DEFAULT_MODEL_ID`, imported from `promptguard.classifier` as `scripts/vendor_weights.py` does;
  checked against `/health.promptguard_model` — `model_mismatch`, exit 2, otherwise — so the host
  tokenizer and the container's classifier are the same model by construction), `--base-url` (default
  `http://127.0.0.1:8020`, must match `^https?://`), `--container
  <name>` (optional; validated against `^[A-Za-z0-9][A-Za-z0-9_.-]*$` before it reaches an argv),
  `--runs` (default 20; **no hard floor** — a value below 5 is accepted and runs, and `warm_p95_*` is
  then `null` because a nearest-rank p95 needs at least five samples), `--json <path>`, `--label`
  (free text copied into the JSON, e.g. `22m-cpus1`), `--timeout-seconds` (per request, on the new
  `post_fn`; default 300 — sized for the budget document of the 86M on 1 vCPU),
  `--health-timeout-seconds` (default 900, the health wait above). Every subprocess is a
  list argv with `shell=False`.
- **The route**: drive `POST /extract` (multipart, `extract_mode=full`) — `/retrieve` cannot fetch a
  host-local fixture server (the URL validator refuses private and loopback IPs by design). `/extract`
  is release-gated by `extract_route_enabled: false` (`config.yaml:27`, `pipeline/extraction_limits.py:100`),
  so the benchmark container mounts the **committed** `bench/config.yaml` over `/app/config.yaml`
  (`-v "$PWD/bench/config.yaml:/app/config.yaml:ro"`) — never a code change.
- **`bench/config.yaml` is committed by this story**: a full copy of the shipped `config.yaml` with
  `extract_route_enabled: true`, the two contiguity keys at their shipped defaults plus a commented
  line for US-004's optional FPR smoke, and as its **first comment line**: "BENCHMARK ONLY — enables
  the release-gated upload route on a throwaway, loopback-bound container; never a deployment config."
  **The test cannot go through `_load_config()`** — it takes no path (`retrieval_app.py:329`;
  codebase-fit, round 3) — so it `yaml.safe_load`s `bench/config.yaml`, runs the house validators
  over the mapping (`extraction_settings_from_config`, `cache_settings_from_config`,
  `brave_settings_from_config` — `retrieval_app.py:1215,1242,1282` — and US-007's
  `promptguard_settings_from_config`), asserts every key is a `KNOWN_CONFIG_KEYS` member, asserts the
  key set equals `config.yaml`'s, and asserts the **only** key whose value differs is
  `extract_route_enabled`. `bench/*.json` and `bench/tokenizer-*/` are gitignored (the outputs and
  the copied-out tokenizer snapshots live beside it; `.gitignore` has no `bench` line today). The mount replaces the file wholesale, so a partial file would silently benchmark
  a non-reference configuration — that is why it is a full copy. `bench/` joins `.dockerignore`
  (one line, `bench/`, beside `scripts/` at `:26`; security, round 3) so it is outside the build
  context as well as outside the enumerated COPY list (`tests/test_dockerfile.py:559-603`;
  `_dockerignore_patterns()` at `:626` reads the file).
- **Inputs are deterministic, synthetic — and sized in tokens with the model's own tokenizer**
  (ruling R29 as corrected in round 5; salty and codebase-fit, round 4: `max_extracted_characters`
  is `(MAX_SEQ_LEN - CHUNK_OVERLAP) * max_chunks * CHARACTERS_PER_PROMPTGUARD_TOKEN` with the constant
  `4`, `pipeline/extraction_limits.py:22,34` — a heuristic — while the real window count is
  `_chunk_text`'s arithmetic over the token ids, `promptguard/classifier.py:133-146`; US-007's own
  measurement spans 4.56 chars/token for prose to 10.4 for repeated runs, so a document generated at
  the character ceiling can pass the pre-gate at `orchestrator.py:532` and blow the chunk budget
  inside `classify()`, which `:551` turns into a 422 `content_too_large_to_classify` — the one row
  the epic was commissioned for, misread as a service failure). Two documents, generated in code
  from a fixed seed with **no characters-per-token constant anywhere in the module** (`grep -c
  CHARACTERS_PER_PROMPTGUARD_TOKEN scripts/bench_promptguard.py` is 0): the harness loads the
  tokenizer from `--tokenizer-dir` (`transformers.AutoTokenizer.from_pretrained(<dir>,
  local_files_only=True)`; `transformers` is a runtime dependency, `pyproject.toml`, so the repo venv
  has it on the host) and counts tokens exactly as `_chunk_text` does (`encode(text,
  add_special_tokens=False)`), reproducing its window arithmetic from the public `MAX_SEQ_LEN` /
  `CHUNK_OVERLAP` constants. The **one-window document** is the longest seeded prefix whose token
  count is ≤ `MAX_SEQ_LEN` (exactly one window, `classifier.py:133-134`); the **budget document** is
  the longest seeded prefix admitted by **both** gates the service applies — `len(text) ≤
  max_extracted_characters(max_chunks)` (the character pre-gate at `orchestrator.py:532`, read from
  the same function `pdf_subprocess.py:71,81,208` use, never a literal) **and** window count ≤
  `max_chunks` (the budget inside `classify()`) — so neither gate can refuse it; the JSON records
  `budget_tokens`, `budget_windows` and `budget_chars` as measured, and the latency keys are named
  `_1w` / `_budget`, not `_512` (a token count is a measurement, not a name). The unit tests use
  `tests/fixtures/tiny_model` as `--tokenizer-dir` (the repo's only real tokenizer, already loaded
  offline by `tests/test_stage3_promptguard.py:288`) and assert determinism, that the one-window
  document is one window and the budget document is within both bounds by the harness's arithmetic
  **and** by `PromptGuardClassifier._chunk_text` itself (private access is allowed under `tests/`),
  and that one more seeded word breaches a bound. US-004's recipe copies each model's tokenizer out
  of the shared volume. No fixture files, no third-party text.
- **Measurements**: cold = wall-clock of the first request after container start; warm p50/p95 =
  nearest-rank percentiles (`sorted(samples)[math.ceil(p / 100 * n) - 1]`) over `--runs` further
  requests per input, `time.perf_counter`; **memory from the service's own cgroup reading first**
  (salty, round 3): after the warm-up loop the harness GETs `/metrics` through `http_get` and copies
  `extraction.cgroup_memory_current_bytes` (to MiB) and `extraction.oom_proximity_ratio`
  (`retrieval_app.py:468,480`, computed at `:1010-1028`; `null` outside a cgroup) into the JSON as
  `cgroup_mem_mib` / `oom_proximity_ratio`; `docker stats --no-stream --format '{{.MemUsage}}'
  <container>`, parsed to MiB, is the optional cross-check (`container_mem_mib`, only with
  `--container`). Both are labelled "after warm-up, not peak" — the OOM/exit-code line US-004
  records is the peak signal; the harness never claims one. The
  published numbers are **single-in-flight latency** — the harness fires one request at a time and
  never exercises `classification_concurrency`; the JSON carries `"concurrency": 1` and the docs say
  the table does not characterise behaviour at `classification_concurrency > 1` (second opinion,
  round 1; a `--concurrent N` mode is a follow-up, not this story).
- **Failure behaviour is defined — two taxonomies** (story quality, round 1; salty, round 2).
  *Configuration failures*, before any successful sample: the health wait ending anywhere but
  `healthy` + `promptguard_loaded: true` (`never_healthy`), `/health.promptguard_model` not equal to
  `--model-id` (`model_mismatch`), a non-2xx from the very first `/extract` request (a **404** means
  the bench config was not mounted — `retrieval_app.py:1688` raises 404, not 403, for the disabled
  route; a **422** whose `reason` is `content_too_large_to_classify` on the first budget request
  means the host tokenizer did not match the container's model and is named as such, never as a bare
  status), a connection error at `--base-url`, a bad argument — exit 2, a stderr line naming the
  status / the reason / the URL / the timeout and last status / the `bench/config.yaml` mount, **no
  JSON**. *Service failures during measurement*, after at least one successful sample: a non-2xx, a
  timed-out sample, the container gone — the harness **writes the JSON** with `"outcome":
  "<non_2xx|timeout|container_gone>"` (plus `non_2xx_reason`: the pipeline 422 body's closed `reason`
  when there is one, else `null`, so an oversized input can never be read as a service failure), the
  samples collected so far, `null` for every percentile it could not compute, and exits non-zero; the row is a *result* ("86M at 1 vCPU timed out on the budget
  document" is the most informative row in the matrix), never a silent exclusion. `--container`
  omitted, or `docker stats` failing or unparseable, leaves `container_mem_mib` as `null`, logs one
  WARNING and completes the run. No path echoes a token or an environment value.
- **Output**: one JSON object with fixed keys — `label`, `base_url`, `model_id`, `runs`,
  `concurrency`, `outcome` (`"ok"` or the closed service-failure token), `non_2xx_reason`,
  `cold_ms_1w`, `warm_p50_ms_1w`, `warm_p95_ms_1w`, `cold_ms_budget`, `warm_p50_ms_budget`,
  `warm_p95_ms_budget`, `budget_tokens`, `budget_windows`, `budget_chars`, `samples_collected`,
  `cgroup_mem_mib`, `oom_proximity_ratio`, `container_mem_mib`, `promptguard_loaded`,
  `promptguard_model`, `sanitizer_revision` and `contract_version` (the last four read from the
  `/health` body the health wait returned). `promptguard_model` is **configuration, not evidence** —
  US-006 reports the configured id unconditionally — so it cannot keep a row from being mislabelled
  on its own (salty, round 4); the evidence that a row was measured against a loaded classifier is
  `promptguard_loaded: true` (always `true` in a written row, since `false` is `never_healthy` and
  writes nothing) together with `sanitizer_revision`, which moves with the model identity.
- Docs: the "Benchmarking the classifier" subsection lives in **`docs/weights.md`** (owner tooling,
  next to the vendoring procedure), not the operator-facing `docs/configuration.md` — it states in-line
  that the benchmark config enables a release-gated route on a throwaway loopback-bound container and
  must never be a deployment config; `docs/configuration.md`'s sizing table (spec 6 US-002) gains one
  cross-reference sentence and the single-in-flight caveat; `kit_tools/testing/TESTING_GUIDE.md` row
  for the new module.

**Acceptance Criteria:**
- [x] `scripts/bench_promptguard.py` reuses `contract_smoke.py`'s `http_get` and `wait_for_health` for
      the `/health` reads, `run_command` and the dataclasses (imports, not copies), implements the
      multipart POST behind `post_fn` with `httpx.post(files=, data=)` (no hand-built boundary),
      waits for `healthy` through `wait_for_health(..., expect_status=STATUS_HEALTHY,
      poll_interval_seconds=5)` under `--health-timeout-seconds` (unit test on the injected `fetch`),
      accepts the arguments above (`--runs 4` runs and reports p95 `null`), validates `--base-url` and
      `--container` shapes, runs subprocesses as list argv with `shell=False`, requires the health
      wait's returned body to carry `status: healthy` **and** `promptguard_loaded: true` (else
      `never_healthy`, exit 2, no JSON — two unit tests) and `promptguard_model == --model-id` (else
      `model_mismatch`), reads `promptguard_loaded`, `promptguard_model`, `sanitizer_revision` and
      `contract_version` from that body and `cgroup_mem_mib` / `oom_proximity_ratio` from `/metrics`
      (parsed in the module — `grep -c '_json_object' scripts/bench_promptguard.py` is 0) into the
      JSON, and writes the fixed-key JSON including `"concurrency": 1`, `"outcome"`,
      `non_2xx_reason` and the three `budget_*` measurements.
- [x] Both inputs are generated from a seed and sized by tokenising with `--tokenizer-dir`
      (`tests/fixtures/tiny_model` in the unit tests; `grep -c CHARACTERS_PER_PROMPTGUARD_TOKEN
      scripts/bench_promptguard.py` is 0); the unit tests assert determinism, that the one-window
      document is exactly one window, that the budget document satisfies **both** `len(text) ≤
      max_extracted_characters(settings.max_promptguard_chunks)` and window count ≤
      `max_promptguard_chunks` — by the harness's arithmetic and by
      `PromptGuardClassifier._chunk_text` — and that one more word breaches a bound; percentile
      maths is nearest-rank and pinned (`[1..20]` → 10 / 19; `--runs 4` → p95 `null`).
- [x] Failure paths are pinned by unit tests with injected fakes: health wait not ending at
      `healthy` + `promptguard_loaded: true` → `never_healthy`, exit 2 naming the timeout and the
      last status, no JSON; `promptguard_model ≠ --model-id` → `model_mismatch`, exit 2, no JSON;
      first-request non-2xx (the 404 case and the `content_too_large_to_classify` 422 case
      included, the latter named as a tokenizer mismatch) → exit 2 naming the status / reason and
      the config mount, no JSON; connection error → exit 2 naming the URL, no JSON; a non-2xx or timeout **after** a successful sample → JSON written with `"outcome"`, the
      samples so far and null percentiles, non-zero exit; `--container` omitted / `docker stats`
      failure → `container_mem_mib: null` + one WARNING and a complete run; no token or env value in
      any message.
- [x] `bench/config.yaml` is committed with the first-line warning, `extract_route_enabled: true`, the
      contiguity keys, and a test that `yaml.safe_load`s it, runs the four `*_settings_from_config`
      validators over it, asserts only `KNOWN_CONFIG_KEYS` members, the same key set as `config.yaml`
      and `extract_route_enabled` as the only differing value; `bench/*.json` and
      `bench/tokenizer-*/` are gitignored.
- [x] `Dockerfile` and `tests/test_dockerfile.py` are unchanged (`git diff --stat` on the two is
      empty); `.dockerignore` gains exactly the line `bench/` (`grep -c '^bench/$' .dockerignore` is 1;
      `git diff --stat .dockerignore` is one line); positively, `grep -c bench Dockerfile` is 0 (today
      0) and `grep -rl 'bench/config' compose/` is empty (today empty) — the benchmark config can
      never be deployed (security, rounds 2 and 3).
- [x] `docs/weights.md` carries the benchmark subsection with the throwaway/loopback warning;
      `docs/configuration.md`'s sizing table carries the cross-reference and the single-in-flight caveat;
      TESTING_GUIDE lists the module; the module docstring carries a `test_mapping:` block.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

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
  `export NAME=<value>` typed interactively, never in this file. **Both** `docs/weights.md` blocks
  that type a value after `export` — `:92-98` (`HF_TOKEN=hf_…`, `GHCR_TOKEN=ghp_…`, `GITHUB_TOKEN=ghp_…`)
  and `:155-157` (`GHCR_TOKEN=ghp_…` for the `oras login`; salty, round 3) — are rewritten to this
  form in this story. Record the file mode and deletion for every credential, never a value.
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
- [x] If the gate has not run: Implementation Notes carry `### US-005 — gate not run, <date>` naming
      the missing prerequisites, and nothing else in the tree changes (the verifier accepts this state).
- [x] The 86M licence check is recorded with both identifier strings and its outcome; on mismatch
      nothing is vendored and the remaining criteria are void.
- [x] `weights_manifest.json` carries the 86M entry (revision + files with `path`/`sha256`/`size`);
      `ALLOWED_MODEL_IDS` names the 86M in the same commit; `ALLOWED_SUFFIXES` unchanged; a test with a
      synthetic snapshot matching the committed entry reaches `weights_verified` under
      `FORAGE_MODEL_ID=<86M>`; the scoped `manifest_diff` recorded shows the 22M entry untouched.
- [x] The mirror holds `forage-weights:<86M revision>`; `docs/weights.md` states both pins, that the
      mirror tag is the revision, and carries the one-file credential recipe (no `export …=` of a value).
- [x] `NOTICE` names both model ids; the credential file was mode 0600 and is recorded as deleted;
      the secret grep is **token shapes only over the story's produced artifacts** (ruling R29 as
      corrected in round 4, R43 — the spec file and `kit_tools/` are excluded because the round-3
      form self-matched its own pattern text): `grep -nE 'hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}'
      weights_manifest.json docs/weights.md docs/configuration.md NOTICE model_fetcher.py` returns
      nothing (executed 2026-09-19: nothing — `docs/weights.md`'s `hf_...`/`ghp_...` placeholders do
      not match the shapes); `grep -c 'export GHCR_TOKEN=' docs/weights.md` is 0.

### US-004: Run the benchmark on the reference container and record it (owner gate)

**Priority:** P2

**Description:** As the owner, I want the 22M and 86M numbers measured on the actual running container
at `FORAGE_CPUS=1` and `4`, recorded here and in the sizing table, with the opt-in recipe and its
caveats documented — so the corpus epic can measure catch rate against a model that is known to fit.
**Execution halts here for the owner** (ruling 18): the run needs the vendored 86M (US-005), an HF
token, Docker and the reference envelope.

**Independent Test:** *Gate run* → this file's Implementation Notes carry `### US-004 — benchmark,
<date>` with one row per run (4 required: {22M, 86M} × {1, 4} CPUs; 2 optional at 2 CPUs; 86M rows
read `not measured — access pending` if US-005 recorded a 403), each row's `promptguard_model` and
`sanitizer_revision` read from `/health`, the after-warm-up and `memory.peak` memory columns and
the OOM/exit-code line per run, and the wall-clock of
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
  `docker build -t forage:bench .` (no build args). **Bounded wait — the harness's own** (codebase-fit,
  rounds 2 and 3): `scripts/bench_promptguard.py --health-timeout-seconds 900` performs
  `wait_for_health(base_url, expect_status=STATUS_HEALTHY, timeout_seconds=900,
  poll_interval_seconds=5)` itself (US-003) — the helper compares only the top-level `status`
  (`contract_smoke.py:286`) and its default is `degraded` (`:154`), which would return on the first
  200 before the model loads.
  `healthy` is a valid proxy for `promptguard_loaded: true` **only because the bench container sets no
  `VALKEY_URL`** (the in-memory backend's `ping_if_due()` is unconditionally true, `cache.py:598-600`),
  so the recipe must never gain one. The first 86M start downloads through the verified path; record
  the cold-start time separately. **Tokenizer copy-out, once per model** (US-003 sizes the documents
  with the model's own tokenizer): after that model's first container reports `promptguard_loaded:
  true` (`curl -s http://127.0.0.1:8020/health`; the shared volume is populated by then), `docker run
  --rm -v forage-model-cache:/c -v "$PWD/bench:/out" alpine cp -rL
  /c/hub/models--<org>--<model>/snapshots/<manifest revision> /out/tokenizer-<label>` (`-L`
  dereferences the hub cache's blob symlinks; the weights come along — harmless, gitignored), then
  `--tokenizer-dir bench/tokenizer-<label> --model-id <id>` on every harness run for that model. On
  timeout the harness exits 2 with **no JSON** (`never_healthy`, US-003 — it never measures an
  unloaded classifier): record the row by hand as `never healthy within 900 s`, run the `docker
  inspect --format '{{.State.OOMKilled}} {{.State.ExitCode}}' bench-<model>-<cpus>` step anyway, and
  enter the result as the row's outcome rather than aborting the matrix. A harness exit with
  `"outcome"` other than `ok` (a service failure mid-measurement, US-003) is likewise entered as the
  row's outcome with the samples it did collect — the matrix continues. Then `uv run python -m
  scripts.bench_promptguard --tokenizer-dir bench/tokenizer-<label> --model-id <id> --container
  bench-<model>-<cpus> --label <model>-cpus<cpus> --json bench/<model>-<cpus>.json`, then the inspect
  step **and the peak reading** — `docker exec bench-<model>-<cpus> cat /sys/fs/cgroup/memory.peak`
  (cgroup v2; `n/a` where the file is absent; owner gate only, no contract change — salty, round 4:
  the after-warm-up figure is steady state, and the moment a 1 GiB cgroup kills the process is
  `from_pretrained`, which only a peak reading or the OOM line can show) — then `docker rm -f
  bench-<model>-<cpus>`.
- **Wall-clock estimate**: 42 requests per row (2 inputs × (1 cold + 20 warm)) × 4 required rows; at
  the 1 vCPU 86M budget-document worst case this is minutes per row, not hours — record the actual
  matrix wall-clock so the next owner knows.
- **The table** (columns fixed; copy the JSON keys): `label | promptguard_model (from /health) |
  sanitizer_revision | cpus | cold 1-window (ms) | warm p50/p95 1-window (ms) | cold budget (ms) |
  warm p50/p95 budget (ms) | budget tokens/windows | memory after warm-up (MiB) | memory.peak (MiB) |
  OOMKilled | exit code` (`promptguard_loaded` is `true` on every written row by construction — a
  row exists only because the harness saw it). Four required rows. A separate line
  records the measured size the 86M set adds to the `forage-model-cache` volume (`docker run --rm -v
  forage-model-cache:/c alpine du -sm /c`). **Real-tokenizer window counts, per model** (salty,
  round 3; US-007 hint (c)): while each container is up, `docker exec bench-<model>-<cpus> python -c
  '…'` loads `PromptGuardClassifier` from `/app/model-cache` at the running revision and prints
  `len(classify_windows(text, max_chunks=64)[0])` for three texts — the harness's one-window
  document, its budget document, and a maximum-length prose `/search`-shaped text (title 512, url
  2,048, snippet 2,000 characters) — one line per model under the table (`22M: a / n / m`, `86M: …`);
  the two models tokenise differently, so the count is measured per model, never copied; for the
  two benchmark documents the harness's host-side `budget_windows` (and 1 for the one-window
  document) is the expectation and the in-container count the confirmation — a disagreement is a
  tokenizer copy-out mistake: stop and redo the copy-out.
- **Optional FPR smoke** (cheap, the container is running): enable the contiguity rule via the
  committed benchmark config's commented line (`promptguard_contiguity_windows: 2`), post the harness's
  long benign document 20 times to each model and record how many verdicts were `injection_detected`
  — a number for the corpus epic, not a gate. It measures the accident rate only; the adversarial
  trip rate is the corpus epic's (US-007).
- **Fill the sizing table** (`docs/configuration.md`, spec 6 US-002): classifier column for the
  1 vCPU / 1 GB and 4 vCPU rows from the 22M numbers with the 86M numbers as a second line per row; the
  2 vCPU row reads `not measured` unless the optional runs were done; the single-in-flight caveat
  stays beside it; **and a staleness line** (second opinion, round 2): "measured at manifest revision
  `<22M rev>` / `<86M rev>` on `forage:bench` from commit `<sha>`; re-run `scripts/bench_promptguard`
  after any envelope-default or model-revision change". State the opt-in recipe once (`FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M`)
  with its acquisition caveat: **86M is served from Hugging Face and the `forage-weights` mirror at its
  own revision tag; a deployment that selects it needs the same acquisition posture as the 22M**
  (`docs/configuration.md` and `docs/weights.md`, one sentence each).
- **Do not flip the default** (owner decision 4). If 86M `memory.peak` exceeds 900 MiB at 1 GB (the
  peak column, not the steady-state one — the question is headroom at load time) or warm p50 at
  1 CPU exceeds 2× the 22M figure, say so and leave the open question open.

**Acceptance Criteria:**
- [ ] If the gate has not run: Implementation Notes carry `### US-004 — gate not run, <date>` naming
      the missing prerequisites; nothing else in the tree changes.
- [ ] Implementation Notes carry the populated table (four required rows, `promptguard_model` and
      `sanitizer_revision` from `/health` per row, the `memory.peak` column; `not measured — access
      pending` where US-005 recorded a 403), the OOM/exit-code line per run, the volume-size line,
      the matrix wall-clock, the tokenizer copy-out command per model (with `git status --porcelain
      bench/` empty afterwards), and every command as run with
      `--env-file "$f"`, `-p 127.0.0.1:8020:8020` and `docker rm -f` — no token value, no `docker
      inspect` / `docker ps --no-trunc` / `docker compose config` output.
- [ ] `docs/configuration.md` sizing table's classifier column is filled for 1 and 4 vCPU with both
      models (2 vCPU marked `not measured` or filled), with the staleness line naming both manifest
      revisions and the commit; the opt-in recipe and its acquisition caveat appear once each
      (`grep -c 'FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M' docs/configuration.md` ≥ 1); the
      wait was `wait_for_health(..., expect_status=STATUS_HEALTHY, ...)` and the recipe carried no
      `VALKEY_URL` (both recorded).
- [ ] The optional FPR smoke, if run, is recorded with its counts and the config used.
- [ ] The per-model window-count line (three texts per model, measured in the container with the
      real tokenizer) is under the table and agrees with the harness's host-side counts for the two
      benchmark documents.
- [ ] `grep -nE 'hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}' docs/configuration.md docs/weights.md
      bench/config.yaml` returns nothing (token shapes only over the story's artifacts; the spec
      file and `kit_tools/` excluded — ruling R29 as corrected in round 4, R43; executed 2026-09-19:
      nothing); the token file is recorded as mode 0600 and deleted.

## Edge Cases

- `FORAGE_MODEL_ID` set to the 22M id explicitly — identical to unset, including the hash input (US-006).
- `FORAGE_MODEL_ID` with surrounding whitespace — stripped, then allowlisted (US-006).
- `FORAGE_MODEL_ID` set but blank — identical to unset, never a refusal (US-006).
- The manifest is unreadable at runtime (missing, unparseable, invalid, empty) — the default model
  resolves to `DEFAULT_MODEL_REVISION` with one `manifest_pin_unavailable` WARNING and its
  `sanitizer_revision` is unchanged; a non-default model refuses `manifest_model_unknown` (US-001).
- `FORAGE_MODEL_REVISION` malformed (not 40 hex) — `model_revision_invalid` ERROR and today's fallback
  to the pin; no path is ever built from the value (US-001).
- The 86M id before US-005 — refused by the allowlist like any other value (`model_id_not_allowed`);
  after US-005, an allowlisted id whose manifest entry is somehow missing — `manifest_model_unknown`,
  service degraded (`promptguard_unavailable`), `/health.promptguard_model` names the configured id
  (US-001, US-006).
- `FORAGE_MODEL_REVISION` set to a sha that is not the selected model's manifest pin —
  `weights_revision_unpinned`, nothing loaded, degraded, closed reason (US-001).
- A warm snapshot of a different model, or of the same model at another revision, on the volume —
  never loaded under the requested pair (`model_identity_mismatch` — `_load_verified`'s
  existence-plus-equality check; `load()` is never called) (US-001).
- The verified cache directory vanishes between verification and load — the classifier's `cache_dir`
  existence pre-flight returns `False` with `model_cache_dir_missing`; never the blanket `except` (US-001).
- `resolve_revision` for a model with no entry — the literal `unpinned`; never reaches a path (US-001).
- A loaded model whose `id2label` is `{0: "LABEL_0", 1: "LABEL_1"}` — refused, `loaded` stays `False`,
  closed reason logged (US-006).
- `contiguity_windows` larger than the chunk count — the rule cannot fire; the max rule still can (US-007).
- `promptguard_contiguity_windows: 1` — refused at boot with `PromptGuardConfigurationError`; the legal
  values are `0` (off) and `2–8` (US-007).
- A per-request `promptguard_threshold` below `promptguard_contiguity_threshold` — the contiguity rule is
  the stricter one; documented, and the rule is off by default (US-007).
- Both rules fire on `[0.9, 0.6, 0.6]` — `rule == "both"`, all three chunks flagged in document order,
  `injection_spans` carries the diagnostic label only (US-007).
- A maximum-length `/search` result — 1 or 3 windows depending on content (measured); the rule can
  fire when enabled on the multi-window shape and is inert by construction on the single-window one
  (US-007).
- A caller raises `promptguard_threshold` to be permissive while the contiguity rule is on — the rule
  can still block; the field description says so (US-007).
- An empty text — zero windows, `SAFE`, as today (US-007).
- `--runs` below 5 — accepted, runs, p95 reported as `null` (US-003).
- `/extract` times out or answers non-2xx after a successful sample — the JSON is written with
  `outcome` and the partial samples; US-004 enters it as the row's result (US-003, US-004).
- `/extract` answers 404 because the benchmark config was not mounted (`retrieval_app.py:1688`) — exit 2
  naming the mount (US-003).
- `docker stats` unavailable — `container_mem_mib: null`, run completes, one WARNING (US-003).
- The 86M repo carries a file outside `ALLOWED_SUFFIXES` — vendoring refuses; stop and record (US-005).
- HF returns 403 for the 86M repo — recorded as `access pending`; the 22M rows still land (US-005, US-004).
- 86M never reaches `promptguard_loaded: true` within 15 minutes — the harness exits 2 with no JSON
  (`never_healthy`); the row records the timeout and the inspect result by hand; the matrix
  continues (US-003, US-004).
- The host tokenizer is not the container's model — `--model-id` against `/health.promptguard_model`
  refuses (`model_mismatch`); a copy-out from the wrong snapshot shows as a window-count
  disagreement at the gate, or as a first-request `content_too_large_to_classify` named as such
  (US-003, US-004).
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
  `bench/config.yaml` and scripts are not hashed). **Cost stated** (salty, round 2): US-006 and
  US-007 each invalidate every content-cache entry once as they land, and at the shipped defaults both
  payloads are inert; they are not batched because the R36 window block requires each story's verifier
  to see a green regenerate and a recorded rotation for its own docstring line — two small,
  attributable invalidations over one unattributable one. Every rotation is recorded at the five sites
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

### US-003 host-side benchmark, retry 2 — 2026-09-22 (Copilot)

Starting commit `14d548d`, clean worktree. Added `scripts/bench_promptguard.py`,
the complete `bench/config.yaml`, build-context/output ignores, the weights
runbook subsection, sizing cross-reference and test mapping. The harness imports
only public smoke-driver names, uses offline tokenizer loading and multipart
`httpx.post`, and has injected GET/POST/command/clock seams. The health wait's
returned body is checked independently for healthy + loaded before any sample.
No service, classifier, dependency, Dockerfile, Compose or contract file changed.

Restored the reviewed candidate `929e74e` without committing, then fixed its
verifier blocker rather than relabelling it. That candidate sent the first budget
request after the entire one-window loop; documenting "first-for-input" did not
meet the process-cold requirement. The harness now **requires `--input 1w` or
`--input budget`**, posts only that document and populates only its cold/warm
latency fields. Each input runs against an owner-started fresh service. There is
no combined mode, no inference probe before the cold POST and no automatic
restart of an owner's container. Health polling never classifies text. The owner
must give each process exclusive use with no earlier inference traffic; the
harness does not claim to attest to other clients' activity.

The fixed JSON carries raw successful millisecond samples in
`samples_collected: {"1w": [...], "budget": [...]}` (first/cold sample then warm
samples). The unselected input has an empty array and three null latency fields,
never a warmed value called cold. Partial warm batches keep raw samples but null
both percentiles. Optional memory failures warn and retain the latency row. A
configuration failure creates no output and leaves any older output path untouched;
the runbook requires fresh paths and checking the exit status.

**US-004 handoff:** `docs/weights.md` supplies the owner-controlled two-container
procedure and downstream table assembly, not just an implementation-note caveat.
One fresh container plus `--input 1w`, record its JSON/OOM/exit/peak, remove it;
a separately fresh container plus `--input budget`, record independently.
The existing matrix columns get their `_1w` and `_budget` measurements from the
corresponding files only after matching provenance/configuration. Memory, peak,
OOM and exit cells carry labelled `1w` / `budget` pairs, never an average.
Preserve both inputs' outcomes and record a no-JSON configuration failure by hand,
retaining the other input's row. Four required model/CPU rows mean **eight**
fresh containers/harness invocations; total requests remain 42 per successful
pair at 20 warm runs. Window-count/FPR probes occur after timed and memory
measurements. Story definitions/checkboxes and owner gates are unchanged.

The live upload
422 uses `error: content_too_large_to_classify` and a fixed sentence in `reason`,
not the token in `reason`; both shapes are recognized as a tokenizer/config
mismatch on the selected input's first request. Only public fixed reasons/codes
can enter failure rows. The configuration/service-failure boundary still uses
the first successful sample of each invocation.

Validation: **115 new tests**, **327 related tests** total across
`test_bench_promptguard`, `test_contract_smoke`, `test_dockerfile` and
`test_contract_metrics`; all pass under the socket guard. Tests trace every
GET/POST/command, assert the selected document is the first inference request
for both modes, and exercise success/initial failure/partial failure on a second
fresh fake service without overwriting the first artifact. Safe Ruff fixes/formatting
were limited to the two new Python files. Repository Ruff lint and format checks,
strict Pyright (zero errors), `git diff --check`, and offline module `--help` pass.
The full suite was **not run**, as this invocation explicitly prohibits it;
that acceptance gate remains for end-of-epic validation.

All hashed sources and hash definition are byte-unchanged. Measured default,
shipped and benchmark-config revision is the same
`b641e6a51ef7cb45a5209a42321a5fff135f432264d256dd8eec9fe9e62698f5`.
`Dockerfile` and `tests/test_dockerfile.py` have empty diffs; `.dockerignore` adds
exactly `bench/`; both output ignore patterns are effective. No owner gate,
weight download, benchmark against a real service, checkbox update, tag, release
or publication occurred.

### US-001 landing checkpoints — 2026-09-22 (Copilot)

Starting commit: `5ced1e94aedc088e34d4805e8394cefb76c2bd17`, clean worktree.
Baseline and step 1 (per-model map, shared builders, manifest assertions and
generator) each passed the 768 related tests in `test_model_fetcher`,
`test_vendor_weights`, `test_sanitizer_revision` and `test_app`. Full-suite
execution is prohibited by this invocation; these are related-suite midpoints.
Starting R40 `grep -c '"revision"'`: model_fetcher tests **15**, vendor tests
**6**, app tests **0**. Production anchored constant grep: **20** lines;
test whole-word grep: **54** lines (seven more than the pre-spec 47, from the
resource-envelope sizing wiring). Baseline default and shipped revision:
`4913fdc1982cb48ba2db9c6972fcea10107408349970c45c9dc6b3ae5c1aa1fb`.

Step 2 (model/revision parameters, both auto-class calls, vendor CLI and
`WeightAcquisition` forwarding) passed **772** related tests. Two additional
production importers now exist versus the spec's snapshot: `retrieval_app.py`
and `pipeline/extraction_limits.py` use the default id in the memory advisory;
both and their test consumers migrated, without changing the sizing rule.

Step 3 (memoised per-model revision resolution, manifest-first refusal, requested
pair verification and the direct loader identity guards) passed **862** related
tests, including `test_stage3_promptguard`. The new tests exercise both default
auto-class arguments and a synthetic model, warm/cold/hub/mirror paths, two
snapshot directories, entry-local versus document-wide rejection, no path/hash/
source calls on an unpinned revision, and the unreadable-manifest ordering.

Step 4 is reached: docs, exact-scope greps and read-only revision controls complete.
R40 ending counts are **20 / 8 / 0**, respectively (starting **15 / 6 / 0**).
Both closing old-name greps return zero; safetensors has exactly two textual
hits and one call-site keyword; `repo_dirname` has one definition; the classifier
residual has one `trusts the path` occurrence; all six override-describing pages
contain `weights_revision_unpinned`. `ALLOWED_SUFFIXES`, `ALLOW_PATTERNS` and
`_walk_snapshot` are unchanged, with literal-value tests for both constants.

Of `TestRevisionPin`'s original eight methods, five migrated:
`test_the_default_is_the_committed_constant`,
`test_the_environment_overrides_it` (now both model ids),
`test_the_pin_is_locked_to_the_committed_manifest`,
`test_the_manifest_pin_is_readable_without_verifying_anything`, and
`test_a_real_manifest_yields_its_pin`. The commit-sha-shape test and the two
required malformed-override tests survive unchanged. The existing warm/pinless
acquisition assertion now expects `weights_pin_unusable`, the intentional
manifest-first diagnostic change.

Implementation details matching the current tree: `_manifest_entry` retains
the parser's `(manifest, failures)` tuple so closed reasons are not lost.
Ruff UP033 mechanically selects `functools.cache`, the unbounded
`lru_cache(maxsize=None)` equivalent, retaining the required `cache_clear()` seam.
The autouse `clear_manifest_entry_cache` fixture resets it. Vendoring's plan
reader uses the mutable raw document rather than populating the runtime memo
before generation; the self-check then verifies the generated entry. Its
default revision comes from the selected entry, never the 22M fallback for a
non-default model. Existing malformed documents are refused, not discarded.

The scoped override inventory remains **13 files**. Rewritten override guidance:
`docs/weights.md`, `docs/configuration.md`, `kit_tools/docs/ENV_REFERENCE.md`,
`kit_tools/arch/SERVICE_MAP.md`, `kit_tools/arch/patterns/LOGGING.md`,
`kit_tools/docs/TROUBLESHOOTING.md`. Also rewritten: `DEPLOYMENT.md`'s re-vendor
command and mismatch diagnosis; `SECURITY.md`'s supply-chain description.
Mention-only and unchanged: `LOCAL_DEV.md`'s cleared-environment list,
`MONITORING.md`'s still-correct malformed-override log row. History unchanged:
`DECISIONS.md`, the GOTCHAS rotation discussion (its only hit is outside the
table), and all prior `docs/bootstrap-notes.md` records; a new no-rotation
record was appended to the latter.

Default/shipped before, after, and the read-only three-module reversal against
`5ced1e9` all reproduce `4913fdc1…aa1fb`, including an unreadable-manifest run.
All nine hashed source files are byte-identical; the eight-file pipeline diff
is empty. Full measurements and the unchanged 22M payload digest are in
`docs/bootstrap-notes.md` and the invariant summary is in `CLAUDE.md`.

Validation: **1116 related tests passed**, strict Pyright zero errors, repository
Ruff lint/format clean, contract-export tests and exact greps pass. The prior
learning's inherited formatting failures are absent at this starting commit;
no unrelated fix was needed. The full-suite criterion remains **unverified**:
this invocation explicitly prohibits running it. No definition or acceptance
checkbox was changed, no owner gate ran, and no dependency, response shape,
generated contract or historical golden changed.

### US-006 implementation — 2026-09-22 (Copilot)

Starting commit `06a56b289fb49121a4d5e38c42d2c4544ced09a8`, clean tree.
`resolve_model_id()` is a total `(id, allowed)` resolver with one environment
read site; unset and stripped-blank mean 22M, and every refusal emits only
`model_id_not_allowed`. The lifespan checks the boolean before constructing
resources, raises `ModelConfigurationError` on refusal, and publishes the
selection for health and acquisition. The allowlist still contains only
`meta-llama/Llama-Prompt-Guard-2-22M`; the manifest-entry test iterates it without
pinning its size, so the owner gate can add the second id and manifest together.

The actual tree has one additional identity consumer versus the original hint:
the resource-envelope memory advisory. It now receives the same selected id,
and its synthetic larger-resident-set test selects through the allowlisted
environment seam instead of replacing `retrieval_app.DEFAULT_MODEL_ID`.
The ASGI lifespan regression records `acquire_and_load`, uses real verified
acquisition with a tiny synthetic manifest, and proves both auto-class calls
receive the selected id and pin. Default/blank/unloaded and post-boot environment
mutation cases pin health's configuration echo; invalid ids, including the
not-yet-vendored 86M, refuse boot without logging their values.

The classifier binds model/tokenizer locally inside the load `try`, validates
`id2label` afterwards, and only then calls `eval` and publishes the loaded
instance. Exactly two indexed labels BENIGN/INJECTION are required; swapped
and mixed-case labels drive the correct inference index. Missing, null,
non-mapping, generic, duplicated, out-of-range and three-class labels produce
the specific `model_labels_unexpected` WARNING and leave it unloaded.
No third-party stub or suppression was needed.

Window block complete: one `* ``1.3.0``` continuation, regenerated OpenAPI /
anchor / drift fixture, manual held-golden update matched against every
`_SCHEMA_MODELS` entry, and one added-path entry
`HealthResponse.promptguard_model`. Historical goldens and the 1.2.0 pair are
unchanged. All four anchor pages now quote
`cefbd601b0c7223ef9f05b973325b62944385899c4571917ce4d3e3741f28a22`.
The spec's smoke hint understates the current driver: it already validates the
entire shared `HealthResponse`, not just membership, and rejects other contract
versions. `contract_smoke.py` remains byte-identical, with no new model-specific
assertion; its synthetic current-schema test body gains the new required field.
Do not read this as new backward compatibility for an old-image smoke.

Thirty-eighth rotation, measured under default and shipped config:
`4913fdc1982cb48ba2db9c6972fcea10107408349970c45c9dc6b3ae5c1aa1fb`
to `85394a954e32ec00bb499d08c01811dc4d00b363708e3f839311ae8fde33d0c0`.
Only `contract.py` differs among all nine hashed sources; read-only whole-file
substitution from the starting commit reproduces the old value exactly.
Repeating with the previous default identity explicitly supplied does too.
The other eight sources are byte-identical; the selected-id hash input changes
only for a non-default model. Full measurements, default behavior rationale
and consumer handoff are recorded at all five required sites.

All fan-out greps pass: model-selection counts are 1/1/1/2 across the two
Compose fragments/configuration/ENV_REFERENCE; health model counts are 1/2/2
across API_GUIDE/MONITORING/SECURITY; each troubleshooting refusal-token count
is 2. `model_fetcher.py` contains zero 86M-id literals. The security decision
explicitly withholds publication, not inferability: the future per-rule
contiguity counters offer a differential channel. Neither owner gate ran.

Validation: **1436 related tests passed**, with 10 non-failing existing
torch/socket warnings; repository-wide Ruff lint and formatting pass,
strict Pyright has zero errors, and `export_contract --check` is clean.
The full-suite criterion is **unverified**, because this invocation explicitly
prohibits running it: result is partial / needs-work for that reason alone.
No story definition/checkbox, weights manifest, vendoring default, NOTICE,
dependency manifest, historical golden, branch, tag or release was changed.

### US-002 implementation — 2026-09-22 (Copilot)

Starting commit `b8c0cbd128d0182c7780d873bb91fc08d91a61de`, clean tree.
`classify_windows(text, *, max_chunks=None)` owns the unchanged chunking,
budget check and inference loop, returning aligned score/text lists in document
order. `classify()` delegates once and retains max pooling, every tied chunk
(including duplicate texts), and `(0.0, [])` for no scores. The window seam
returns `([], [])` when unavailable; the existing WARNING is byte-identical.
Loading, tokenizer locks, chunking and thread settings are unchanged.

The starting recursive `grep -c 'classify\.' tests/ --include='*.py'` count
was **53**: stage3 **4**, orchestrator **34**, app **8**, policy **6**, admission
**1**. Spec line numbers predate those additional policy/concurrency doubles.
All inference doubles now use the shared `tests.fakes.make_mock_classifier`:
window scores/side effects drive the real `classify` pooling implementation,
including blocking, cancellation and budget-error doubles. A default double
uses the input as its one chunk, as real inference does; stage 3 still suppresses
flags on safe results. No pre-existing verdict/score assertion was weakened.
Ending count is **39**: stage3 **4**, orchestrator **25**, app **4**, policy **5**,
admission **0**, shared fakes **1** (the real-method wrapper).
Both trusted-tier skip assertions cover both methods; the existing
`test_standard_not_skipped` is AST-identical to baseline. Stage 3 retains its
one `classifier.classify,` call; moving it remains US-007.

Default and shipped revision before and after:
`85394a954e32ec00bb499d08c01811dc4d00b363708e3f839311ae8fde33d0c0`.
Byte comparisons against the starting commit cover all eight pipeline sources,
the ninth root source and the hash definition; the hashed-source `git diff
--stat` is empty. Configuration, model manifest, dependencies and generated
contract artifacts are unchanged. `CODE_ARCH.md` names the new seam.

Validation: the nine related modules passed **1,312** tests before the change
and **1,347** afterwards (35 new cases), including real-loader, tokenizer
concurrency, route/cancellation, policy and frozen search-pin coverage.
New cases pin ordered window scores/texts, both label indices, all max ties,
budget boundaries before inference, unloaded warning/fallback, empty text,
empty chunks and exact-threshold behavior. Final post-format rerun passes;
repository Ruff lint/format and strict Pyright pass with zero errors.
The three existing torch/socket warnings remain non-failing.
Full-suite success is **unverified** because this invocation explicitly
prohibits running it; partial / needs-work records only that outstanding gate.
No story definition/checkbox, response shape or owner-gate state changed;
no weights were downloaded, no owner benchmark ran, and nothing was published.

<!-- Populated during execution. US-001 and US-002 record the unchanged default revision value; US-006
and US-007 record their rotations; US-005 records the licence check, the vendoring transcript, the
scoped manifest diff and the allowlist edit; US-004 records the benchmark table, the matrix wall-clock
and the sizing-table fill. -->

### Resource-envelope US-001 handoff — 2026-09-22

The boot advisory now uses `PARENT_RESERVATION_BYTES = 512 MiB` (22M resident,
no classification running), `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` (22M row
`0` only), and `PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES = 64 MiB`, all in
`pipeline/extraction_limits.py`. US-004 must measure two distinct quantities:
idle loaded RSS for each model (86M minus 22M fills the resident-delta row) and
the concurrency 1→2 RSS delta (replaces the provisional classification coefficient).
Shared weights belong in the parent, never the per-classification term.
The sizing table is resource-envelope **US-003**, not US-002; add its per-model
column. US-006 must route the selected `FORAGE_MODEL_ID` into
`retrieval_app._warn_if_envelope_memory_rule_unmet`, which currently receives the
fixed `MODEL_ID`. Keep the 1024m shipped ceiling even if the measured coefficient
exceeds 96 MiB; state the measured minimum and let under-sized boots warn.
Neither owner benchmark nor weight-vendoring gate has been performed here.
Reuse `pipeline.config_bounds` for the forthcoming settings triple; this story's
`PromptGuardThreadsConfigurationError` leaves `PromptGuardConfigurationError`
unambiguous for that reader.

### US-007 implementation — 2026-09-22 (Copilot)

Starting commit `967748d6edb4f8f87c47d69cac73e297fbff7bd0`, clean tree.
Stage 3 now calls `classify_windows` once, retaining its completed-thread
cancellation ownership, budget propagation, trusted skip and unavailable-model
policy. `score` stays the maximum; `rule` is `max_score`, `contiguity`, `both`
or `None`. Strict `>` max pooling is unchanged. Every qualifying `>=` run is
included, including trailing and disjoint runs; the union deduplicates window
indices in document order rather than deleting distinct equal-text windows
(preserving the previous max-tie behavior).

The local frozen settings/error/builder triple validates once in lifespan,
including while the rule is off: windows `0` or 2–8, threshold 0.0–1.0,
no bool/string/non-finite acceptance and no float conversion before the
out-of-range huge-integer check. Shipped values remain `0` / `0.5`.
The immutable settings object follows the existing threshold seam through
retrieve, both extract entry points, and both search permit branches, with
scalar values passed to stage 3. No per-request contiguity field exists.

Telemetry uses dedicated flat counters, not new keys in the closed
`blocked_by_reason`/`omitted_by_reason` vocabularies. A shared structural
`PromptGuardMetricsSink` lets retrieve and extraction count before stage 4
without importing the app; search counts each omitted result. Exactly one
WARNING carries the longest qualifying run and total windows, not text or
scores. The INFO omission line additionally names the rule. ASGI tests start
through the real lifespan, assert the builder runs once, drive all three
routes and `/metrics`, and verify contiguity-only, both, max-only and disabled
cases. Raising the caller max threshold to 1.0 does not suppress contiguity.
Quarantine tests cover the multi-window union on both fetch/upload responses;
`stage4_structuring.py` is byte-unchanged.

The two maximum-length fixture-tokenizer shapes both measure **4,583
characters**. This story's explicit seeds measure **1,040 tokens / 3 windows**
for prose and **15 tokens / 1 window** for a repeated character. These differ
from the spec author's unspecified example seeds (1,004 / 442 tokens), but
pin the required content-dependent window facts: prose at least two,
repeated runs exactly one, and the rule inert for the latter. The real
production-tokenizer count remains **unmeasured**, reserved for US-004.
**Handoff to `epic-forage-injection-corpus`:** require both fragments separated
by a benign roughly 448-token window (residual evasion) and sustained mid-band
comment/review text (adversarial false-positive page blocking/result omission,
with 64-token overlap correlation). `/search` coverage is content-dependent.
Measure both directions before any default flip.

**Held 1.3.0 window:** three additive metric counters and max-rule-only request
threshold descriptions (GOVERNANCE row 3, description-only, no property/bound
move). Export regenerated; anchor
`579c32ee93ec3b1c528ce6df4a8d6ba5b4eb285890ecd8119b74098f39e7a3a9`
is updated on all four quoting pages. The golden is re-created from
`_SCHEMA_MODELS` and moves only the SearchRequest threshold description.
**Nothing is appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`.** The newer
`test_contract_schema.py` also pins exact search/retrieve counter sets;
those two sets gain the counter, independently of the additions ledger.
All three section mirrors/emission paths are pinned in `test_contract_metrics`.
MONITORING's counter grep is exactly 3 and its WARNING grep is 1.
Historical goldens remain unchanged.

**Thirty-ninth revision rotation, default and shipped config:**
`85394a954e32ec00bb499d08c01811dc4d00b363708e3f839311ae8fde33d0c0` →
`b641e6a51ef7cb45a5209a42321a5fff135f432264d256dd8eec9fe9e62698f5`.
Only `stage3_promptguard.py`, `orchestrator.py` and `contract.py` change among
the nine hashed sources; windows then threshold are new ASCII inputs after
the max threshold. Every source/input was reversed independently read-only
against the starting commit, with all-reverted reproducing the old value
exactly; `docs/bootstrap-notes.md` records the complete nine-row matrix and
all five required rotation sites are current. The run rule ships off, but
adding its defaults still invalidates all old content-cache keys.
`cache_policy_fingerprint`'s exact seven inputs and both request models'
absence of contiguity fields are pinned.

Validation: **1,743 related tests pass**, with three existing non-failing
torch/socket warnings. Repository Ruff lint/format, strict Pyright (zero
errors), and `export_contract --check` pass. The full-suite criterion remains
**unverified** because this invocation explicitly forbids the full suite:
partial / needs-work records that remaining gate, not a functional defect.
No story definition/checkbox, owner-gate state, weights/dependency manifest,
classifier source, NOTICE, tag or release changed. Neither owner gate ran;
no weights downloaded, no push or publication occurred.

### US-007 retry — 2026-09-22 (Copilot)

Restored the preceding implementation from `ca269ab` onto its unchanged
pre-story baseline, then reproduced and fixed the verifier's shared-fake
failure. `RecordingSearchMetrics.counters` now exposes
`promptguard_contiguity_detections`; its exact dictionary regression increments
that counter and checks the nonzero value, alongside protocol-field completeness
and instance isolation. No production or contract bytes changed from that attempt.

All **635 prescribed T0/T1 tests pass**, plus **951 related route, metrics,
schema, policy, export, governance and search-pin tests**. The latter emit three
existing non-failing torch/socket warnings. Repository Ruff lint/format, strict
Pyright and contract drift checks pass. All nine default/shipped revision probes
reproduce the preceding measurement; stage 4 and historical goldens are unchanged.
The full-suite gate remains explicitly **deferred to end-of-epic validation**;
the result stays partial / needs-work for that unverified gate, not the now-fixed
fake regression. Owner gates and acceptance checkboxes remain untouched.

### US-005 — gate not run, 2026-09-22

**Blocked on the owner gate; not complete.** Starting commit `51fa1b4`,
clean tracked worktree. This non-interactive invocation has no owner-authorized
vendoring session or supplied gate evidence. Missing from the handoff:

- Confirmed owner acceptance of the 86M licence and approved access to its gated
  Hugging Face repository, with the model-card licence/restriction comparison
  and exact revision recorded before any weight download.
- Owner-provisioned `HF_TOKEN`, `GHCR_USER`, `GHCR_TOKEN` (`write:packages`)
  and `GITHUB_TOKEN` (`read:packages`) through the single temporary mode-0600
  credential file, with an owner present to supply the values and confirm cleanup.
- Confirmed egress to Hugging Face/GHCR/GitHub, `oras` availability, working-cache
  capacity, and explicit authorization to publish the revision-keyed mirror tag.

These prerequisites were not provisioned or verified here; this is not a claim
that the host lacks the tools, network or credentials. No secret store or
credential value was inspected, no credential file was created or sourced,
and no mode/deletion evidence is claimed.

The local `NOTICE` names `Llama 4 Community License Agreement` for the 22M.
The 86M model card was not fetched: its identifier, any additional restrictions,
licence outcome and revision remain **unverified**, not an assumed match.
No download was attempted, so there is no observed 403 (`access pending`),
429, partial snapshot or retry to record. There is no generated 86M entry,
scoped manifest diff, file-count/byte-total measurement, mirror publication,
fresh-pull verification or `weights_verified` transcript.

The gate-not-run criterion requires a notes-only tracked change. Accordingly,
the existing 22M manifest, `DEFAULT_MODEL_REVISION`, one-member
`ALLOWED_MODEL_IDS`, `ALLOWED_SUFFIXES`, `NOTICE` and both configuration rows
marked "Pending vendoring" remain unchanged. The two credential-recipe rewrites
in `docs/weights.md` are deferred to the authorized vendoring run along with the
manifest/allowlist/NOTICE/pin documentation commit; none of those changes is
claimed complete. No runtime, contract, sanitizer-revision input or benchmark
artifact changes. No weights, benchmark, push, tag, release or publication ran.
US-004 remains gated behind US-005; neither gate's checkboxes were changed.

Resume with the owner following US-005's licence-first procedure and failure
paths, recording the real evidence before enabling 86M. This invocation stops
at the sanctioned gates-unrun state, with result `partial` / `needs-work`.

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
fail-open. Returning `DEFAULT_MODEL_REVISION` for an *unvendored* model — rejected: it would hash a
`(model, revision)` pair that never existed (validation round 1, salty). Dropping
`DEFAULT_MODEL_REVISION` from the chain entirely — rejected in round 3: an unreadable manifest would
then rotate the **default** model's hash silently, the one failure this repo has an invariant against
(salty, round 2); the constant stays as the default model's last resort with a WARNING.
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
- Validation round 3 (2026-09-19): the revision chain keeps `_REVISION_RE` and `DEFAULT_MODEL_REVISION`
  (default model only) with the refusal placed before any path is built; the "no file I/O" criterion
  replaced by manifest-open counting plus revert-and-reproduce; the loaded-identity check is path-only
  with the helper copy pinned to the original; US-002 leaves `:172` to US-007; the `/search` window
  claim made content-dependent with both shapes pinned; the config triple named; the contiguity
  verdict logged at WARNING; the benchmark's reuse scoped honestly (new multipart POST), its failure
  taxonomy split, its inputs sized from `max_extracted_characters()`, the `wait_for_health` call
  named; the gates-unrun end state named; TROUBLESHOOTING joins the fan-out.
- Validation round 5 (2026-09-19, final, not re-reviewed): the harness refuses an unloaded
  classifier (`never_healthy`) and records `promptguard_loaded` / `sanitizer_revision` per row; both
  documents sized in tokens with the model's tokenizer (`--tokenizer-dir`, `--model-id`); the
  `_load_verified` check derives both paths itself; the top-of-`_acquire_and_load` order spelled
  out; `load()`'s restructuring for the `id2label` check; the owner-gate `memory.peak` reading; the
  landing order inside US-001; `_json_object` dropped from the reuse list; the `use_safetensors`
  count executed.

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
- Overruled (rounds 1–3): "split US-001 three ways" (story quality, salty) — ruling R37 as corrected in
  round 3: already litigated; the manifest format, its generator and the ~40 assertion sites cannot
  land green apart from the loader threading, so the diff is one reviewable unit at size L; the
  recorded overrule now names the size (≈600 lines across nine files) rather than the rotation cost.
- Overruled: "log the contiguity verdict at INFO" (this spec's own round-2 text) — WARNING, content-free;
  INFO never renders (GOTCHAS).
- Overruled: "`_name_or_path` as an alternative identity check" (this spec's own round-2 text) — path
  comparison only (security, round 2).
- Overruled: "extract `repo_dirname` into a leaf module" as the required shape (second opinion) —
  accepted as an alternative; the private copy plus the parity test is the minimum.
- Overruled: "a smoothed sliding-window score instead of a consecutive run" (second opinion) — handed
  to the corpus epic's tuning, where the false-positive data will exist.
- Overruled: "report the contiguity settings on `/health`" — deliberately not exposed; the decision and
  the asymmetry with `promptguard_model` are recorded in the field description and SECURITY.md.
- Overruled: "add a `--concurrent N` mode now" (second opinion) — the caveat is stated beside every
  published number; the mode is a follow-up once the corpus epic needs it.
- Overruled: "the 100 % `/search` inertness" (this spec's own round-1 text) — withdrawn on the measured
  caps; the rule can fire on `/search` and a test says so.
- The 2 vCPU sizing row is `not measured` unless the owner runs the optional pair.
- Overruled (round 4): "pass `expected_snapshot_path` from `_load_verified` into `load()`" (security,
  round 3, alternative (a)) and this spec's own round-3 private `repo_dirname` copy with its parity
  test — ruling R29 as corrected in round 4: the identity check is `_load_verified`'s
  existence-plus-equality on the manifest-derived path, the classifier side is an existence check
  only, and `kit_tools/arch/SECURITY.md` records the residual ("the classifier trusts the path the
  verifier handed it") rather than a second observation that would only re-derive the same path.
- Corrected (round 4): ruling R29's "the scoped `MODEL_ID` grep returns only the default constant's
  definition" — a `\b`-anchored grep cannot match `DEFAULT_MODEL_ID` (`_` is a word character), so
  the executed expectation is **nothing**; the ruling's intent (scoped to the four consumers,
  anchored, executed before the count is written) is kept and its pre-story count (20) recorded.
- Overruled (round 4): "state the memory figure as peak" (salty, round 3, alternative) — the harness
  reads the service's own cgroup figures from `/metrics` after warm-up and labels them as such; the
  OOM/exit-code line US-004 records is the only peak signal, and the harness never claims one.
- Overruled (round 4): "range 0–8 with `1` documented" (this spec's own round-3 text) — `1` refuses
  boot: a one-window run is a second max rule at a lower threshold (salty and security, round 3).
- Adopted, narrowing an earlier criterion (round 4): `.dockerignore` gains `bench/` (security, round
  3); the round-2 "three files unchanged" criterion now covers `Dockerfile` and its test only.
- Overruled (round 4): "`resolve_revision` refuses an unpinned override" (this spec's own round-3
  wording) — the resolver is total and returns the override; `_acquire_and_load` is the one refusal
  site (salty, round 3).
- Overruled (round 4): "include the spec file in the owner-gate secret greps" (this spec's own
  round-3 form) — ruling R29 as corrected: the greps run over the stories' produced artifacts with
  token shapes only, so they can return nothing; the epic-level rule that no token-shaped literal
  appears in a spec is held by the epic wrapper, not by a self-matching criterion.
- Corrected (round 5): the round-4 `verified_path` parameter on `_load_verified` (ruling R29 as
  corrected in round 5) — both paths are derived inside `_load_verified` from `(cache_root,
  model_id, revision, manifest_path)`; no acquisition-leg return shape changes.
- Corrected (round 5): the benchmark documents are sized in tokens with the model's own tokenizer,
  never by `CHARACTERS_PER_PROMPTGUARD_TOKEN`; the harness refuses to measure a container whose
  `/health` is not `healthy` with `promptguard_loaded: true`, and `promptguard_model` is recorded as
  configuration, not evidence.
- Adopted (round 5): the owner gate reads `memory.peak` per run and the go/no-go memory rule is
  stated against it; the harness itself still claims no peak figure (the round-4 overrule stands for
  the harness).

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

### Session 2026-09-19 (validation round 3)
- Rulings applied: R29 (corrected — the four-step revision chain with `_REVISION_RE` and
  `DEFAULT_MODEL_REVISION` retained, refusal before any path, path-only identity, `:172` to US-007,
  content-dependent `/search` windows, `TROUBLESHOOTING.md` fan-out), R37 (corrected — no further
  splits), R41 (every fixture executed once before it is written), R39, R40.
- Q: What does the default model resolve to when the manifest cannot be read? → A:
  `DEFAULT_MODEL_REVISION`, with one WARNING; its `sanitizer_revision` never moves for that reason.
- Q: Does a maximum-length `/search` result always span two windows? → A: No — content-dependent;
  both shapes are pinned and the real-tokenizer count is measured at the gate.
- Q: What does the harness do when the service fails mid-matrix? → A: Writes the JSON with `outcome`
  and the partial samples and exits non-zero; the row is a result.

### Session 2026-09-19 (validation round 4)
- Rulings applied: R29 (corrected — `WeightAcquisition` gains `model_id` and is named in US-001 and
  US-006 as the lifespan seam; the `\bMODEL_ID\b` closing grep scoped to the four consumer files
  and executed (20 pre-story hits, expected nothing); the owner-gate secret greps run over produced
  artifacts with token shapes only, the spec file and `kit_tools/` excluded; the loaded-identity
  check is `_load_verified`'s existence-plus-equality on the manifest-derived path, the classifier
  side an existence check, the residual recorded in SECURITY.md; `id2label` cardinality; a test that
  every `ALLOWED_MODEL_IDS` entry has a manifest entry), R43 (every grep criterion names its path
  set and exclusions and was executed on 2026-09-19 with its pre-story count recorded), R41, R37
  (no splits). Round-3 findings applied: the total resolver with one refusal site, the
  `resolve_model_id()` return shape, `windows: 1` refused, the `/metrics` cgroup memory figure, the
  MONITORING fan-out, per-model window counts at the gate, the `lru_cache` reset seam, 404 not 403,
  `httpx.post(files=, data=)`, `--health-timeout-seconds`, the `yaml.safe_load` bench-config test,
  `tests/test_sanitizer_revision.py:95-116` named, `docs/weights.md:155-157`, `bench/` in
  `.dockerignore`, the manifest rejection scope, the corrected citations (`PromptGuardResult` `:31`,
  `brave.py:206-242`, `orchestrator.py:1012-1037`).
- Q: Where does the `weights_revision_unpinned` refusal live? → A: At the top of `_acquire_and_load`,
  once; `resolve_revision` is total and returns a well-formed override as today.
- Q: What does `resolve_model_id()` return? → A: `tuple[str, bool]` — `(id, True)` for unset, blank
  or allowlisted; `(DEFAULT_MODEL_ID, False)` plus one WARNING otherwise; the lifespan raises on `False`.
- Q: Is `promptguard_contiguity_windows: 1` legal? → A: No — `0` or `2–8`; `1` refuses boot.
- Q: Who checks that the loaded directory is the verified one? → A: `_load_verified`, before `load()`;
  the classifier checks only that `cache_dir` exists, and SECURITY.md says so.

### Session 2026-09-19 (validation round 5, final)
- Rulings applied: R29 (corrected — the harness asserts `promptguard_loaded: true` and `status:
  healthy` before timing and records `promptguard_model` and `sanitizer_revision` per row, failing
  non-zero with no rows on a degraded container; documents sized by tokenising with the model's
  tokenizer, `tests/fixtures/tiny_model` in the hermetic tests, never a characters-per-token
  constant; `_load_verified` derives both paths itself so no `_try_source` / mirror signature
  changes), R43 (the `use_safetensors` count executed: 2). Round-4 warnings applied: the
  top-of-`_acquire_and_load` order with the warm-cache-plus-unreadable-manifest pin; `load()`'s
  restructuring for the `id2label` check with the missing/`None`/non-mapping third case; the
  owner-gate `memory.peak` reading and the peak-based memory rule; the landing order inside US-001;
  `_json_object` dropped from the reuse list; `generate_manifest` reads before it writes. This round
  was not re-reviewed; the unapplied warnings are in § Known risks.
- Q: What does the harness do when the container never becomes healthy? → A: Exits 2 with no JSON
  (`never_healthy`); US-004 records the row by hand. It never measures an unloaded classifier.
- Q: How are the benchmark documents sized? → A: In tokens, with the selected model's tokenizer
  copied out of the shared volume (`--tokenizer-dir`); the budget document satisfies both the
  character pre-gate and the chunk budget by construction.

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

## Known risks (validation close-out)

Round 5 was a final fix pass without a review round. Warnings and notes from round 4 that were not
applied, or were applied in a form the reviewer did not see, are recorded here so the implementer
reads them as open rather than closed.

- **salty (round 4) — the verifier/loader agreement is a re-derivation, not an observed hand-off.**
  Applied as ruling R29 (round 5) directs: `_load_verified` derives both paths itself, so the
  equality half restates the top-of-function refusal and the existence half is the live check; the
  `VerificationResult.snapshot_dir` is still discarded on the hub and mirror legs. Deferred because
  the alternative changes three acquisition-leg return shapes the spec never budgeted; the residual is
  stated in US-001's hint and in SECURITY.md's "trusts the path" sentence.
- **codebase-fit (round 4, info) — `resolve_model_id() -> tuple[str, bool]` is a third resolver
  shape** beside the raising lifespan resolver (`pipeline/search_providers/__init__.py`) and the
  total-with-fallback `resolve_revision`. Kept: the per-request `derive_sanitizer_revision` path must
  never raise (round 4 decision); the implementer may fold the boolean into a `ModelConfigurationError`
  raised only by the lifespan if the tuple reads badly, provided the per-request path stays total.
- **codebase-fit (round 4, info) — MONITORING.md's log-lines table row for `classify() called but
  model not loaded — returning safe fallback`** is not named in US-002's fan-out; US-002 re-implements
  `classify()` on `classify_windows`, so the implementer keeps that WARNING's text byte-identical (or
  updates the row). Not re-reviewed.
- **codebase-fit (round 4, info) — line citations drift.** Every `:line` in this spec was measured on
  2026-09-19 before specs 1–6 execute (e.g. the Goals cite of `tests/test_sanitizer_revision.py:95-118`);
  anchor by symbol and quoted text, never by offset. A mismatch is not a spec defect.
- **salty (round 4, info) — the orchestrator's omission log at `orchestrator.py:1032` is INFO** and
  never renders in the container; the contiguity WARNING (`promptguard_contiguity_verdict`) is the
  visible per-event signal. Left as is: the INFO line is pre-existing and the WARNING is the story's.
- **Owner-gate environment assumptions (round 5).** The tokenizer copy-out relies on the hub cache
  layout `hub/models--<org>--<model>/snapshots/<revision>` (`model_fetcher.snapshot_path`) and on
  `alpine cp -rL` dereferencing blob symlinks; the peak reading relies on cgroup v2 `memory.peak`
  (kernel ≥ 5.19). Both are owner-gate steps with an `n/a` / redo path, not criteria on the code.
- **US-001 stays one story** (salty, round 4, overall): ≈600 lines across nine files under one
  suite gate. The round-5 landing order gives it stated midpoints; it is still a single diff by
  ruling R37.
