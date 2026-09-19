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
execution_order: [US-001, US-002, US-003, US-004]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: PromptGuard 86M Opt-In + Contiguity Gating + Benchmark

> **Spec 7 of `epic-forage-hardening`.** Make the classifier *selectable* (`FORAGE_MODEL_ID`, a closed
> allowlist, 22M stays the default — owner decision 4), add **contiguity gating** beside the existing
> max-score rule so a payload interleaved across 512-token windows cannot slip under it (ruling 17),
> ship a hermetic **benchmark harness**, and then — **US-004 is the owner gate** (ruling 18) — run it
> on the reference container for both models and record the numbers the sizing table and the corpus
> epic need. Binding: owner decision 4; rulings 5 (the 1.3.0 window), 6 (rotations recorded), 17, 18.
> Context: Poppy's `WEB_ACCESS_FAMILY.md` § "PromptGuard research (2026-08-30)".

## Overview

The 2026-08-30 research pass concluded nothing beats Prompt Guard 2 at Forage's constraints, that the
86M variant catches more (multilingual AUC .942 → .995) at roughly 350–560 MB fp32, and that "Prompt
Overflow" — fragments interleaved with benign prose across windows — defeats max-pooling outright. Two
of those three are code changes Forage can make without waiting on anyone; the third (does 86M fit
the 1 vCPU / 1 GB reference envelope?) has no published CPU numbers, so the epic measures it rather
than guesses.

Today `promptguard/classifier.py:23` hard-codes `MODEL_ID`, three consumers import that constant
(`model_fetcher.py:139`, `pipeline/sanitizer_revision.py:10`, `scripts/vendor_weights.py:107`),
`classify()` (`classifier.py:154-207`) folds its per-window `scores` list into one `max_score`, and
stage 3 decides on `score > threshold` alone (`pipeline/stage3_promptguard.py:136`). This spec:

- **US-001** — one resolver for the model id, read once from `FORAGE_MODEL_ID`, allowlisted, fed to all
  three consumers and to a new `/health` field `promptguard_model` (inside the 1.3.0 window, ruling 5);
  the model's `id2label` is asserted at load instead of trusting a positional index.
- **US-002** — a per-window score path on the classifier and the contiguity rule in stage 3, both
  tunable from `config.yaml`, both entering the `sanitizer_revision` hash like the threshold does.
- **US-003** — `scripts/bench_promptguard.py`, deterministic inputs, JSON output, hermetic tests.
- **US-004 (owner gate)** — the benchmark matrix on the reference container, the 86M revision pin, the
  sizing-table column, the opt-in recipe. Execution halts here if the gate has not run.

The order `US-001 → US-002 → US-003 → US-004` puts every autonomous story before the human gate.

## Goals

- `FORAGE_MODEL_ID` unset or set to the 22M id produces a byte-identical `sanitizer_revision` to
  today's (`tests/test_sanitizer_revision.py::test_sanitizer_revision_is_stable_at_the_committed_pin`
  stays green); the 86M id produces a different value; any other value refuses boot.
- A synthetic document whose injection is split across two adjacent windows scoring `[0.6, 0.6]` is
  `INJECTION_DETECTED` with the defaults (`windows=2`, `contiguity_threshold=0.5`); `[0.6, 0.2, 0.6]`
  is not; a single window at `0.86` still fires through the unchanged max rule.
- `uv run python -m scripts.bench_promptguard --help` works with no weights present, and the harness's
  unit tests run without network, torch inference, or weights.
- The benchmark table (both models × `FORAGE_CPUS` 1 and 4) is recorded in this file's Implementation
  Notes and `docs/configuration.md`'s sizing table before the spec is archived.

## User Stories

### US-001: Configurable model id (`FORAGE_MODEL_ID`), allowlisted, on `/health`

**Priority:** P1

**Description:** As an operator, I want to select the 86M classifier with one environment variable
and see which model is loaded on `/health`, so the upgrade is an opt-in I can measure, not a fork of
the image — while a typo or an unknown model can never be loaded.

**Independent Test:** With `FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M` and a stubbed
loader, `/health` reports `promptguard_model` equal to that id and `derive_sanitizer_revision({})`
differs from the unset value; with `FORAGE_MODEL_ID=evil/model` the lifespan refuses to start with the
closed reason `model_id_not_allowed` and the value is never echoed.

**Implementation Hints:**
- **One read site, like the revision.** Add `resolve_model_id() -> str` to `model_fetcher.py` beside
  `resolve_revision()` (`:882`): read `FORAGE_MODEL_ID` once, strip, compare against a closed
  `ALLOWED_MODEL_IDS = ("meta-llama/Llama-Prompt-Guard-2-22M", "meta-llama/Llama-Prompt-Guard-2-86M")`;
  the default is the 22M id. `_MODEL_ID_RE` (`model_fetcher.py:324`) is a shape check, not the
  allowlist — keep both. Never log the rejected value (the `model_revision_invalid` log at the
  `FORAGE_MODEL_REVISION` read site is the model: closed reason, no echo).
- **Feed the three consumers.** `MODEL_ID` in `promptguard/classifier.py:23` becomes
  `DEFAULT_MODEL_ID`; `model_fetcher.py:139,1108,1525` (`snapshot_path(cache_root, model_id, …)`),
  `pipeline/sanitizer_revision.py:10,40` and `scripts/vendor_weights.py:107,414,429,710,907` call the
  resolver instead of importing the constant. `pipeline/sanitizer_revision.py` is **not** in
  `_REVISION_SOURCES` (`:12-21`), so the source edit alone rotates nothing — the hash input
  `f"{model_id}@{resolve_revision()}"` is what moves, and only when a non-default id is configured.
  Extend `tests/test_sanitizer_revision.py::test_the_hashed_model_identity_is_model_id_at_revision`
  with the 86M id; keep `test_sanitizer_revision_is_stable_at_the_committed_pin` untouched and green.
- **Per-model revision pins.** `DEFAULT_MODEL_REVISION` / `weights_manifest.json` pin the **22M**
  weights (`kit_tools/docs/ENV_REFERENCE.md:52`). Turn the manifest into a per-model map keyed by
  model id; the 86M entry is filled by US-004 (the owner has the HF access to look it up). Until
  then, selecting a model with no committed pin and no `FORAGE_MODEL_REVISION` refuses boot with the
  closed reason `model_revision_required`. `resolve_revision()` takes the resolved id.
- **Refuse-boot pattern.** Follow the lifespan's existing refusal for an unknown provider name
  (`search-provider-abstraction` US-003; `retrieval_app.py` ~`:189` for the blank-chain log shape):
  raise before the app serves, one WARNING with the closed reason, no value.
- **Assert `id2label` at load, don't assume index 1.** `classifier.py:27-29` documents the 22M's two
  classes and notes "the older 86M had 3 classes" — that was Prompt Guard *1*; the implementer checks
  the loaded `model.config.id2label` in `load()` (`classifier.py:52-127`): it must map exactly one
  label to `INJECTION` (case-insensitive) and one to `BENIGN`; set `_injection_label_index` from it and
  fail the load (`loaded` stays `False`, WARNING `model_labels_unexpected`) otherwise. Expected outcome
  for both PG2 models: `{0: "BENIGN", 1: "INJECTION"}`.
- **`/health`**: `HealthResponse.promptguard_model: str` (`retrieval_app.py:360-368` region), populated
  from the lifespan's resolved id (store it on `app.state`, never re-read the environment in the
  handler — the `_resolved_*` pattern at `:295-310`). Additive → ruling 5: append a line to the
  `CONTRACT_VERSION` docstring's `1.3.0` entry (`pipeline/contract.py:22-68`; the entry exists since
  spec 1 US-004), regenerate with `uv run python -m scripts.export_contract`, re-create
  `tests/golden/contract_1_3_0.json`; `contract_smoke.py` must not assert the field (a `1.1.0` image
  lacks it).
- **Test fan-out**: `tests/conftest.py::_CLEARED_ENV_VARS` (`:45-54`) gains `FORAGE_MODEL_ID`;
  `tests/test_hermeticity.py::test_the_cleared_environment_is_the_expected_exact_set` (`:129-138`) is
  updated in the same change. Docs: `docs/configuration.md` row (format at `:108`),
  `kit_tools/docs/ENV_REFERENCE.md` row (format at `:52-53`) and its `:73` cleared-variable sentence,
  `docs/weights.md` § "The three places the revision appears" (now per model), `NOTICE:16-18`
  parenthetical names both ids once US-004 confirms the licence.

**Acceptance Criteria:**
- [ ] `FORAGE_MODEL_ID` unset → `/health.promptguard_model == "meta-llama/Llama-Prompt-Guard-2-22M"`
      and `derive_sanitizer_revision({})` equals the pre-story value (recorded in the Implementation
      Notes); the 86M id → a different value, asserted by test.
- [ ] A value outside the allowlist refuses boot with closed reason `model_id_not_allowed`; a sentinel
      value appears 0 times in the captured log output (test).
- [ ] A model with no committed pin and no `FORAGE_MODEL_REVISION` refuses boot with
      `model_revision_required`; with `FORAGE_MODEL_REVISION` set it proceeds to acquisition.
- [ ] `load()` derives the injection label index from `id2label` and refuses a model whose labels are
      not exactly `{BENIGN, INJECTION}` (test with a fake config on the mocked auto-class).
- [ ] `model_fetcher`, `sanitizer_revision` and `scripts/vendor_weights.py` obtain the id from the
      resolver (`grep -n 'import MODEL_ID' model_fetcher.py pipeline/sanitizer_revision.py
      scripts/vendor_weights.py` returns nothing).
- [ ] `promptguard_model` documented in the `1.3.0` docstring entry, golden re-created,
      `uv run python -m scripts.export_contract --check` clean; MONITORING/API_GUIDE `/health` tables
      gain the row.
- [ ] `_CLEARED_ENV_VARS` and the hermeticity exact-set test include `FORAGE_MODEL_ID`; the
      `configuration.md`, `ENV_REFERENCE.md` and `weights.md` rows/sections exist (`grep -c
      FORAGE_MODEL_ID` ≥ 1 in each).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-002: Contiguity gating beside the max rule

**Priority:** P1

**Description:** As an operator, I want an injection spread across adjacent classifier windows to be
caught even when no single window crosses the 0.85 threshold, so the documented "Prompt Overflow"
evasion of max-pooling stops working against Forage — without changing what the max rule catches
today.

**Independent Test:** With a mocked classifier whose per-window scores are `[0.6, 0.6]`,
`run_promptguard` returns `INJECTION_DETECTED` with both chunks in `flagged_chunks`; with
`[0.6, 0.2, 0.6]` it returns `SAFE`; with `[0.86]` it returns `INJECTION_DETECTED` exactly as before;
with `promptguard_contiguity_windows: 0` the first case returns `SAFE`.

**Implementation Hints:**
- **Per-window scores without touching `classify()`'s contract.** `classify()` already builds
  `scores: list[float]` (`classifier.py:183-199`) before pooling at `:204-205`. Add
  `classify_windows(text, *, max_chunks) -> tuple[list[float], list[str]]` (scores and the chunk texts,
  same budget check, same `(…, [])` fallback when unloaded) and re-implement `classify()` on top of it
  so the existing tests at `tests/test_stage3_promptguard.py:303-354` (`TestClassifierUnit`) stay
  green unchanged.
- **The rule lives in stage 3** (`pipeline/stage3_promptguard.py:128-150`, a `_REVISION_SOURCES` file
  → ruling 6). Replace the single `asyncio.to_thread(classifier.classify, …)` with `classify_windows`;
  `max_score = max(scores)`; verdict is `INJECTION_DETECTED` when `max_score > threshold` **or** when
  any run of at least `contiguity_windows` consecutive scores is `>= contiguity_threshold`
  (ruling 17). `flagged_chunks` carries the max-scoring chunks in the first case and the contiguous
  run in the second; `score` stays `max_score` (no wire change — `PromptGuardResult` is internal).
- **Config keys** `promptguard_contiguity_windows` (int, default `2`, range 0–8, `0` disables) and
  `promptguard_contiguity_threshold` (float, default `0.5`, must be `<= promptguard_threshold`) in
  `config.yaml` beside `promptguard_threshold` (`:15`), read through the bounded-helper pattern
  (`pipeline/extraction_limits.py:98-151`) and registered in `KNOWN_CONFIG_KEYS` (spec 3 US-003).
  Thread them to `run_promptguard(...)` (`stage3_promptguard.py:42-48`) from `sanitize_and_structure`
  (`orchestrator.py:160-207`) and the `/search` loop (`:969-997`) — `orchestrator.py` is hashed, so
  this story is one rotation covering both files.
- **Hash the new behaviour config** the way `promptguard_threshold` is hashed
  (`pipeline/sanitizer_revision.py:41`): append both keys' values to the digest. This changes the
  default `sanitizer_revision` for everyone — expected; measure before/after per ruling 6.
- **The mock is the test double**: `tests/test_stage3_promptguard.py:32-40` `_make_mock_classifier`
  (a `MagicMock(spec=PromptGuardClassifier)`) — give it a `classify_windows` side effect returning the
  sequences above. Also cover `TestScoreExactlyAtThreshold` (`:63,101`) semantics: `>` for the max
  rule, `>=` for the contiguity rule, both stated in the config row.
- Docs: `docs/configuration.md` rows for both keys with the two operators spelled out; `kit_tools/arch/
  SECURITY.md` stage-3 paragraph names both rules; `kit_tools/docs/GOTCHAS.md` gains no entry (nothing
  surprising) unless the implementer finds one.

**Acceptance Criteria:**
- [ ] `classify_windows` exists, `classify` is implemented through it, and every pre-existing
      classifier and stage-3 test passes unchanged.
- [ ] The four verdict cases in the Independent Test are pinned by tests, plus: a run that starts at
      the last window (`[0.2, 0.6, 0.6]`) fires; scores exactly at `contiguity_threshold` count (`>=`);
      a score exactly at `promptguard_threshold` does not fire the max rule (`>`, unchanged).
- [ ] Both keys validated at boot (out-of-range refuses like `test_lifespan_refuses_an_out_of_range_
      cache_bound`), defaults `2` / `0.5`, documented in `docs/configuration.md`.
- [ ] `derive_sanitizer_revision` includes both values; the rotation (stage 3 + orchestrator + the two
      new hash inputs) is measured and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and
      `kit_tools/arch/DECISIONS.md` (ruling 6).
- [ ] `/search` and `/retrieve` both apply the rule (one end-to-end test per route through the ASGI app
      with the mocked classifier).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-003: Benchmark harness (`scripts/bench_promptguard.py`)

**Priority:** P1

**Description:** As the owner, I want a repeatable script that loads a named model and reports RSS
and classify latency on fixed synthetic inputs, so the 22M-vs-86M question is answered by a table
anyone can regenerate, not by a one-off measurement.

**Independent Test:** `uv run pytest tests/test_bench_promptguard.py` passes with the network blocked
and no weights on disk (the loader and classifier are injected fakes), and `uv run python -m
scripts.bench_promptguard --help` exits 0.

**Implementation Hints:**
- **Location and shape**: `scripts/bench_promptguard.py` next to `scripts/export_contract.py` and
  `scripts/vendor_weights.py` (module with a `main(argv)` and injectable `load_fn` / `classify_fn`
  seams, like `vendor_weights.py`'s phase functions). Arguments: `--model-id` (validated through
  `resolve_model_id`'s allowlist), `--revision`, `--cache-dir` (default `HF_HOME`), `--threads`
  (applies `torch.set_num_threads` — the same knob spec 6 US-001 added as `promptguard_threads`),
  `--runs` (default 20), `--json <path>`.
- **Inputs are deterministic and synthetic**: generate them in code from a fixed seed — one text that
  tokenises to ≈512 tokens and one that yields exactly 64 chunks under `MAX_SEQ_LEN=512` /
  `CHUNK_OVERLAP=64` (`classifier.py:24-25`; use `_chunk_text` to confirm the count at run time and
  report it). No fixture files, no third-party text.
- **Measurements**: RSS after `load()` via `resource.getrusage(RUSAGE_SELF).ru_maxrss` normalised to
  MiB (kilobytes on Linux, bytes on macOS — branch on `sys.platform`); "cold" = the first `classify`
  after load; "warm" = p50/p95 over `--runs` further calls, per input; wall-clock via
  `time.perf_counter`. Report also `torch.get_num_threads()` and the chunk count.
- **Output**: one JSON object with fixed keys (`model_id`, `revision`, `threads`, `rss_mib`,
  `cold_ms_512`, `warm_p50_ms_512`, `warm_p95_ms_512`, `cold_ms_64chunk`, `warm_p50_ms_64chunk`,
  `warm_p95_ms_64chunk`, `chunks_64chunk`) — the columns US-004's table copies.
- **Runs inside the image**: US-004 runs it in the reference container. Check the `Dockerfile` `COPY`
  lines (invariant 3) — if `scripts/` is not copied, add the single file to the copy set (no build
  argument, invariant 2) and extend `tests/test_dockerfile.py` accordingly.
- Docs: a "Benchmarking the classifier" subsection in `docs/configuration.md` under the sizing table
  (spec 6 US-002 created the table) with the exact command; `kit_tools/testing/TESTING_GUIDE.md` row
  for the new test module.

**Acceptance Criteria:**
- [ ] `scripts/bench_promptguard.py` accepts the arguments above, rejects a model id outside the
      allowlist, and writes the fixed-key JSON.
- [ ] The two synthetic inputs are generated from a seed; the 64-chunk input yields exactly 64 chunks
      through `_chunk_text` (asserted with the real tokenizer stub in tests via an injected chunker).
- [ ] Unit tests cover argument validation, RSS normalisation on both platforms, percentile maths and
      JSON shape with injected fakes — no torch inference, no network, no weights.
- [ ] `docs/configuration.md` names the command; TESTING_GUIDE lists the module; the image carries the
      script (`docker run --rm --entrypoint python <image> -m scripts.bench_promptguard --help`
      documented as the container invocation).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-004: Run the benchmark on the reference container and record it (owner gate)

**Priority:** P1

**Description:** As the owner, I want the 22M and 86M numbers measured on the actual container at
`FORAGE_CPUS=1` and `4`, recorded here and in the sizing table, with the 86M revision pinned and the
opt-in recipe documented — so the corpus epic can measure catch rate against a model that is known to
fit. **Execution halts here for the owner** (ruling 18): the run needs the gated weights, an HF
token, Docker and the reference envelope; the implementer records that the gate has not run and
stops.

**Independent Test:** This file's Implementation Notes carry a `### US-004 — benchmark, <date>`
section with the table below fully populated (both models × two CPU settings) and the 86M revision
sha; `docs/configuration.md`'s sizing table has the classifier column filled from it; `grep -nE
'hf_[A-Za-z0-9]{20,}' kit_tools/specs/feature-hardening-promptguard-86m.md` returns nothing.

**Implementation Hints:**
- **Credential handling**: the HF token goes into `$TMPDIR/hf.env` via `printf 'HF_TOKEN='; read -rs
  T; echo; printf 'HF_TOKEN=%s\n' "$T" > "$TMPDIR/hf.env"; unset T` and reaches the container only as
  `--env-file "$TMPDIR/hf.env"`; the file is deleted after the last run and is never printed. No
  token on any command line, in this file, or in the recorded commands.
- **The matrix**, run from the tagged-tree-equivalent image of this branch (`docker build -t
  forage:bench .`, no build args): for each `model` in {22M, 86M} and each `cpus` in {1, 4}:
  `docker run --rm --cpus "$cpus" --memory 1024m --env-file "$TMPDIR/hf.env" -e FORAGE_MODEL_ID=<id>
  -v forage-model-cache:/app/model-cache --entrypoint python forage:bench -m scripts.bench_promptguard
  --model-id <id> --threads <cpus> --json /app/model-cache/bench-<model>-<cpus>.json`. For 86M the
  first run also needs `-e FORAGE_MODEL_REVISION=<sha>` (US-001's `model_revision_required`) — look
  the sha up on the model card, then commit it as the 86M manifest pin in this story.
- **Before the 86M run, check the licence**: the 86M model card must carry the same Llama 4 Community
  License as the 22M (expected outcome: it does); then `NOTICE:16-18` names both ids and the "Built
  with Llama" line is unchanged. If the licence differs, stop and record — no NOTICE edit, no pin.
- **The table** (columns fixed; copy the JSON keys): `model | cpus | threads | revision (12 hex) |
  weights on volume (MiB) | RSS after load (MiB) | cold 512-tok (ms) | warm p50/p95 512-tok (ms) |
  cold 64-chunk (ms) | warm p50/p95 64-chunk (ms)`. Eight rows minimum. Record the container memory
  cap and whether any run was OOM-killed (`docker inspect --format '{{.State.OOMKilled}}'`).
- **Fill the sizing table** (`docs/configuration.md`, created by spec 6 US-002) classifier column for
  the 1 vCPU / 1 GB and 4 vCPU rows from the 22M numbers, and add the 86M numbers as a second line per
  row; state the opt-in recipe once: `FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M` (+ the
  volume note — a second model on the same `forage-model-cache` volume adds its weights beside the
  22M set; record the measured size).
- **Do not flip the default** (owner decision 4). If 86M RSS exceeds 700 MiB or warm p50 at 1 CPU
  exceeds 2× the 22M figure, say so in the notes and leave the open question below open.

**Acceptance Criteria:**
- [ ] Implementation Notes carry the populated table (≥ 8 rows), the memory cap, the OOM check, the
      86M revision sha, and every command as run with `--env-file "$TMPDIR/hf.env"` and no token value.
- [ ] The 86M pin is committed to the per-model manifest; a boot with `FORAGE_MODEL_ID=<86M>` and no
      `FORAGE_MODEL_REVISION` no longer refuses (test updated); `docs/weights.md` states the two pins.
- [ ] The 86M licence check is recorded with its outcome; `NOTICE` names both model ids only if the
      licence matched.
- [ ] `docs/configuration.md` sizing table's classifier column is filled for 1 and 4 vCPU with both
      models, and the opt-in recipe appears once (`grep -c 'FORAGE_MODEL_ID=meta-llama/Llama-Prompt-
      Guard-2-86M' docs/configuration.md` ≥ 1).
- [ ] `grep -nE 'hf_[A-Za-z0-9]{20,}'` over this file, `docs/configuration.md` and `docs/weights.md`
      returns nothing; `$TMPDIR/hf.env` is recorded as deleted.

## Edge Cases

- `FORAGE_MODEL_ID` set to the 22M id explicitly — identical to unset, including the revision hash
  (US-001).
- `FORAGE_MODEL_ID` with surrounding whitespace — stripped, then allowlisted (US-001).
- 86M selected, weights absent, no egress — the existing degraded path (`promptguard_unavailable`)
  applies unchanged; `/health.promptguard_model` still names the configured id (US-001).
- A loaded model whose `id2label` is `{0: "LABEL_0", 1: "LABEL_1"}` — refused, `loaded` stays `False`,
  service degraded, closed reason logged (US-001).
- `contiguity_windows` larger than the chunk count — the rule cannot fire; the max rule still can
  (US-002).
- `contiguity_threshold` greater than `promptguard_threshold` — refused at boot (US-002).
- An empty text — zero windows, `SAFE`, as today (US-002).
- Benchmark on macOS (`ru_maxrss` in bytes) vs Linux (kilobytes) — normalised (US-003).
- The 64-chunk input does not produce 64 chunks with a future tokenizer — the harness reports the
  actual count instead of asserting it (US-003).
- 86M OOM-killed at 1 GB — recorded as a row with `OOMKilled: true`, the default stays 22M (US-004).

## Out of Scope

- Flipping the default to 86M (owner decision 4; revisited after `epic-forage-injection-corpus`).
- int8 / ONNX Runtime builds of either model (new dependency; open question only).
- Datamarking / spotlighting of retrieved text (Poppy family Epic 5).
- Any model outside Prompt Guard 2 (the 2026-08-30 verdict stands; the allowlist is the mechanism).
- The corpus that measures catch rate and false-positive rate (`epic-forage-injection-corpus`).

## Assumptions

- Prompt Guard 2 86M is a two-class DeBERTa sequence classifier like the 22M; the `id2label` check in
  US-001 is what turns this from an assumption into a load-time fact.
- The 86M weights live in the same gated Hugging Face org under the same licence family; US-004
  verifies before pinning.
- Poppy is the only consumer; `promptguard_model` is defaulted-additive, so a 1.2.0 client still
  validates.
- `resource.getrusage` is available in the container (Debian-based image, Linux).
- Vendoring 86M to the GHCR mirror is not required for this epic (open question).

## Technical Considerations

- **Rotations (ruling 6):** US-001 rotates nothing at the default; US-002 rotates for everyone
  (stage 3 + orchestrator source bytes + two new hash inputs) — one measured, recorded rotation.
- **Contract window (ruling 5):** only `promptguard_model` moves the document here; spec 8 US-002
  closes the window.
- **Memory:** the reference envelope is 1 GB with `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` (spec 6);
  86M fp32 at up to ~560 MB plus the 512 MiB parent budget noted in `compose/minimal.yml:105-109` is
  the number US-004 measures rather than assumes.
- **Threads:** `promptguard_threads` (spec 6 US-001) is what `--threads` exercises; without it torch
  sees every host core behind the CFS quota.
- **Hermeticity:** every new test runs under the `pytest-socket` guard with injected fakes; the harness
  is only ever executed for real by the owner.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md) (stage 3, `promptguard/`)
- Security: [SECURITY.md](../arch/SECURITY.md) (stage-3 paragraph, non-vulnerabilities table)
- Weights: [`docs/weights.md`](../../docs/weights.md), [`docs/configuration.md`](../../docs/configuration.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("PromptGuard model absent → degraded")
- Research: Poppy `kit_tools/specs/WEB_ACCESS_FAMILY.md` § "PromptGuard research (2026-08-30)"

## Implementation Notes

<!-- Populated during execution. US-001 records the unchanged default revision value; US-002 records
the rotation; US-004 records the benchmark table, licence check and pin. -->

## Refinement Notes

### Research Findings

**Decision:** Model identity is resolved once in `model_fetcher.py` and consumed by three modules.
**Rationale:** `MODEL_ID` is imported at `model_fetcher.py:139`, `pipeline/sanitizer_revision.py:10`
and `scripts/vendor_weights.py:107`; a single resolver keeps the download, the hash and the vendoring
on one id.
**Alternatives considered:** A `config.yaml` key — rejected; deployment wiring is environment
(search epic ruling 9), and the id decides which secret-gated download happens.
**Source:** `model_fetcher.py:139,324,484,882,1108,1525`; `pipeline/sanitizer_revision.py:12-21,40`.

**Decision:** Contiguity gating is a second rule in stage 3 over per-window scores; `classify()`'s
`(max_score, flagged_chunks)` contract is unchanged.
**Rationale:** The pooling happens inside `classify()` (`classifier.py:204-205`); the scores list
already exists (`:183-199`). Re-implementing `classify()` over `classify_windows()` keeps
`TestClassifierUnit` green.
**Alternatives considered:** Lowering the single threshold — raises false positives on every window
(the research's stated failure mode); ensembling a second model — memory and FPR (research: "don't
ensemble").
**Source:** `pipeline/stage3_promptguard.py:128-150`; `tests/test_stage3_promptguard.py:32-40,303-354`.

**Decision:** The benchmark run is a human gate; the harness is autonomous.
**Rationale:** Weights are gated behind `HF_TOKEN`, the suite is hermetic (`tests/conftest.py`
`forbid_network`), and the reference envelope is a container the orchestrator does not have.
**Alternatives considered:** A CI benchmark job — no weights in CI by design (invariant 2).
**Source:** `kit_tools/specs/archive/feature-search-release.md` US-002 (the owner-gate pattern).

### Scope Adjustments

- The outline's "verify whether `sanitizer_revision.py` is hashed" resolved to *not hashed*; US-001
  therefore asserts an unchanged default value rather than recording a rotation.
- Per-model revision pins were added to US-001/US-004 after finding the manifest pins only the 22M.

### Decisions Made

- Owner decision 4 (22M default, allowlisted opt-in); rulings 17 (contiguity beside max) and 18
  (benchmark is the gate).

## Clarifications

### Session 2026-09-19
- Q: How should the 86M upgrade land? → A: Configurable model, 22M stays default; the benchmark
  publishes numbers on the reference envelope; 86M is opt-in until the corpus epic measures it.
- Q: Does the contiguity rule replace the max rule? → A: No — beside it, both tunable, `windows=0`
  disables the new rule (ruling 17).

## Open Questions

- [ ] Should 86M be vendored to the `forage-weights` mirror (a second mirror tag per model id) —
      decided by the owner at US-004 from the measured volume size. Non-blocking.
- [ ] If US-004 shows 86M does not fit 1 vCPU / 1 GB, is an int8/ONNX build worth a new dependency?
      Non-blocking; only asked if the numbers force it.
