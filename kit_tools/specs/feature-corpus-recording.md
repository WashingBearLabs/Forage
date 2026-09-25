<!-- Template Version: 2.5.0 -->
---
feature: corpus-recording
status: active
session_ready: true
depends_on: [corpus-attacks, corpus-benign, corpus-86m-enablement]
vision_ref: "T2.3 — Injection regression corpus (CI)"
type: epic-child
size: L
epic: forage-injection-corpus
epic_seq: 4
epic_final: false
execution_order: [US-001, US-004, US-002, US-003]
created: 2026-09-19
updated: 2026-09-24
---

# Feature Spec: Score Recording — Cassette Format, File-Backed Replay, the Host-Side Recorder, the 22M and 86M Recordings

> Spec 4 of 6 (spec 0 added 2026-09-24) in `epic-forage-injection-corpus` (owner decisions 2, 17;
> rulings 6a, 10, 13). Two owner gates (US-002, US-003): they need a Hugging Face token on an
> approved account or the mirror read token, and US-003 needs the 86M **enabled by spec 0**
> (`feature-corpus-86m-enablement.md`: vendored, label-pinned, allowlisted, released). Both
> recordings may run on the owner's lab host (decision 17). Tokens enter the environment only
> (`read -rs`), never argv, never a log line (CLAUDE.md invariant 6). Anchors re-verified at
> `main` = `403e9c5` (post-hardening `v1.2.1`) on 2026-09-24; cite symbols, re-grep lines.

## Overview

CI has no weights and will not get them. What it can have is the classifier's *answers*: for every
text stage 3 sends to the model, the per-window scores, recorded once on a host that has the weights
and replayed in CI through the real stage-3 rules. This spec defines the cassette file, makes the
replay classifier read it, wraps the real classifier so a recording is just a drive of the corpus
with the wrapper in place, and runs the two recordings. Because the model is pinned by revision and
per-file hash, the scores cannot change without a re-vendoring — the event that requires a
re-recording; because the key is the text itself, a pipeline change that alters what reaches stage 3
is a miss, and a miss is a hard error.

## Goals

- A cassette is a deterministic JSON file with no payload text: scores and window counts keyed by
  sha256, plus model identity, revision, and recording metadata (ruling 10).
- The recorder is the harness in record mode: same drivers, same routes, both live rule configs,
  every record; it refuses a model id outside `ALLOWED_MODEL_IDS`, a model with no manifest pin and
  an unloaded classifier, and binds the recording to the manifest pin (resolved once, passed to the
  loader — which refuses any other revision — and written into the cassette from the same value).
- Live-vs-replay equivalence is proven hermetically with the real loader on
  `tests/fixtures/tiny_model`.
- The 22M cassette is recorded and committed; the 86M cassette is recorded if spec 0 enabled the
  86M, else the gap is recorded as `not recorded — 86M not enabled` (ruling 6a); `windows_min`
  promises hold on the recorded window counts.
- `NOTICE` names the model, revision and licence beside the cassette files (landscape finding 15).

## User Stories

Execution order: `[US-001, US-004, US-002, US-003]` — the procedure doc (US-004) is written before
the owner runs it.

### US-001: Cassette format, file-backed replay, the recording wrapper and the recorder CLI

**Priority:** P1

**Description:** As a maintainer, I want a cassette file the replay classifier loads and the recorder
writes, and a `RecordingClassifier` that wraps the real one so the recorder is just `drive_all` with
the wrapper — proven on the tiny fixture model so the whole record → replay loop is tested without
weights or network.

**Independent Test:** `uv run pytest tests/test_corpus_record.py` passes under the socket guard: a
three-record mini corpus driven with `RecordingClassifier(PromptGuardClassifier loaded from
tests/fixtures/tiny_model)` writes a cassette; the same corpus driven with
`ReplayClassifier.from_cassette(path)` yields byte-identical `RouteResult`s; the cassette file
contains no record marker substring; deleting one entry and replaying raises
`UnrecordedRecordError` naming the record id; `uv run python -m scripts.corpus.record --help` exits 0
offline; a recorder run whose classifier fails to load exits 2 with `not_loaded` and writes nothing;
a `--model-id` outside `model_fetcher.ALLOWED_MODEL_IDS` exits 2 with `model_id_not_allowed` before
any acquisition is attempted.

**Implementation Hints:**
- **File**: `tests/corpus/cassettes/<slug>@<revision>.json` where `slug =
  model_fetcher.repo_dirname(model_id)` (`repo_dirname`, `model_fetcher.py` ~:490 at 403e9c5; it
  returns the `models--meta-llama--Llama-Prompt-Guard-2-22M` form — use it as is, or strip the
  `models--` prefix for the bare `meta-llama--…` form — pick one and pin it with a test) and
  `revision` is the full commit SHA. Content:
  `{"format": 1, "model_id": …, "revision": …, "recorded_at": ISO-8601 UTC, "sanitizer_revision": …,
  "torch": …, "transformers": …, "configs": ["default", "contiguity"], "records": {sha256hex:
  {"scores": [floats as repr], "windows": n}}}`; `json.dumps(sort_keys=True, indent=2)` + trailing
  newline; written to a temp file in the same directory then `os.replace` (atomic). Cap: a cassette
  ≤ 2 MB (a lint test). `sanitizer_revision` is `derive_sanitizer_revision({})` — the default-config
  value, computed with `FORAGE_MODEL_ID` / `FORAGE_MODEL_REVISION` unset (the recorder refuses to run
  with either set, below), so it is the same value ruling 6 asserts and it hashes the default model's
  identity even in the 86M cassette; the cassette's own `model_id` / `revision` fields carry the
  recorded model. Informational only (US-004). *(Pinned 2026-09-24, round 4: the field was
  undefined as written — `derive_sanitizer_revision` hashes the contiguity keys, so the two recorded
  configs give two values, and it reads the model env vars through `resolve_model_id()` /
  `resolve_revision()`.)*
- **Replay** (`scripts/corpus/replay.py`, spec 1): `ReplayClassifier.from_cassette(path)` reads the
  file, sets `model_id` / `revision`, and keeps a **call log** (`calls: list[tuple[sha, n_windows,
  max_score]]`) the report reads for `rule` attribution (spec 5).
- **Recording wrapper** (`scripts/corpus/record.py`): `RecordingClassifier(inner:
  PromptGuardClassifier)` — `loaded` mirrors `inner.loaded`; `classify_windows(text, *, max_chunks)`
  calls `inner.classify_windows(text, max_chunks=None)` (**always unbudgeted**, so the cassette holds
  the full window list and the replay applies the route's `max_chunks` — ruling 10), stores `(sha →
  scores)`, then applies the budget itself exactly as the real method would (raise
  `PromptGuardBudgetExceededError` when `max_chunks is not None and len(scores) > max_chunks`) so
  the drive's outcome during recording equals the replay's. `classify` re-implements max-pool.
- **Recorder CLI**: `uv run python -m scripts.corpus.record --model-id <id> [--out DIR]
  [--configs default,contiguity]`. Every refusal below is exit 2 with one fixed reason word on
  stderr and nothing written:
  (0) **refuse a model-selection environment** — if `FORAGE_MODEL_ID` or `FORAGE_MODEL_REVISION` is
  set, exit `model_env_set`. `--model-id` is **not** an alias for `FORAGE_MODEL_ID` and the recorder
  never sets it: `acquire_and_load` takes the model as its `model_id=` keyword and never reads that
  variable (it defaults to `DEFAULT_MODEL_ID`), while the lifespan `drive_all` boots *does* read it
  (`resolve_model_id()`) and `derive_sanitizer_revision` hashes it — so a stray export would load one
  model and hash another. `scripts/vendor_weights.py`'s `--model-id` is the precedent (its module
  docstring: "no model-selection environment variable is read").
  (1) **refuse an unallowlisted id** — `if model_id not in model_fetcher.ALLOWED_MODEL_IDS`: exit
  `model_id_not_allowed`, before any network or cache access. The recorder enforces the allowlist
  itself because `acquire_and_load` does not consult it: the only check is `resolve_model_id()`
  (`model_fetcher.py` ~:938 at 403e9c5, env-driven, which falls back to the default rather than
  refusing) and the lifespan's `ModelConfigurationError("model_id_not_allowed")` (`lifespan`,
  `retrieval_app.py` ~:1644-1646), neither of which the recorder's acquisition passes through. A
  membership test on the shipped constant, not a runtime change; the reason word matches the
  lifespan's so the host tool and the service refuse the same ids.
  (2) **resolve the pin once** — `pin = model_fetcher.read_manifest_pin(model_id=model_id)`
  (`read_manifest_pin`, ~:997; keyword-only `model_id`, first positional is `manifest_path`; returns
  `WeightsManifest | None`). `None` → exit `not_pinned`. `revision = pin.revision` is the **one**
  local that is passed to the loader and written into the cassette — never recomputed.
  (3) `classifier = PromptGuardClassifier()`; acquire by calling
  `model_fetcher.acquire_and_load(classifier, model_id=model_id, revision=revision)` **directly**
  (`acquire_and_load`, ~:1630) — a deliberately synchronous, one-shot, no-retry path that never
  raises (every failure is a logged `False`), chosen *because* this is a host CLI rather than a
  long-lived service. It must run **before** `drive_all`, which patches
  `model_fetcher.acquire_and_load` inside `corpus_app` (spec 1). *(Corrected 2026-09-19, validation
  round 1: this read "the same path the lifespan uses", which is false on the current tree and was
  already caught and corrected once in this epic family the same day —
  `feature-hardening-promptguard-86m.md` states "**The lifespan seam is `WeightAcquisition`, not
  `acquire_and_load`**" (ruling R29 as corrected in round 4). The lifespan (`lifespan`,
  `retrieval_app.py` ~:1642, acquisition block ~:1864-1873 at 403e9c5) constructs
  `model_fetcher.WeightAcquisition(classifier, model_id=model_id, metrics=app.state.model_metrics)`
  and schedules `acquisition.run()` as a task — an async, jittered-backoff retry loop with a
  single-flight lock (`WeightAcquisition` ~:1878, `run()` ~:1978, `attempt_once()` ~:1949), and
  `attempt_once` is the only caller of the module-level `acquire_and_load`, wrapped in
  `asyncio.to_thread`. If retry fidelity is ever wanted here, that is a deliberate change: the CLI
  would need an event loop and a definition of "give up" for a one-shot script.)* The environment
  contract for credentials is the container's — `HF_HOME`, `HF_TOKEN`, `FORAGE_WEIGHTS_MIRROR`,
  `FORAGE_MIRROR_TOKEN` are read by that function from the environment exactly as in the container;
  nothing token-shaped is parsed here.
  (4) refuse unless `classifier.loaded` (exit `not_loaded` — a fail-closed run measures nothing;
  hardening ruling R29). **This is the revision guard.** `PromptGuardClassifier` retains neither
  the model id nor the revision it loaded (`load()` forwards them to `from_pretrained` and keeps
  neither), so there is no post-load readback to assert against, and the recorder must not add one
  (ruling 6). Instead the binding is structural: `_acquire_and_load` refuses any `revision` other
  than the manifest entry's (`weights_revision_unpinned`, `_acquire_and_load` ~:1712-1722) and a
  `model_id` with no entry (`manifest_model_unknown`), so `loaded is True` after step 3 implies
  "exactly `(model_id, revision)` from step 2 is resident", and step 2's single local is what the
  cassette records. *(Redesigned 2026-09-24, round 4: this step read "assert the loaded revision
  equals `read_manifest_pin(...)`, exit `revision_mismatch`" — there is nothing loaded to read, and
  through the real path the mismatch was unreachable. `revision_mismatch` is removed; `not_pinned`
  covers the one case it could have meant.)*
  (5) `drive_all(load_corpus(), RecordingClassifier(classifier), configs=[…])`; count
  **unscanned** results, defined positively — drive results the classifier was expected to see and
  did not: `promptguard_state` in `{"unavailable_blocked", "unavailable_allowed"}` on `/retrieve` /
  `/extract`, or `omit_reason == "promptguard_unavailable"` on `/search` (all three are the
  `skip_reason="model_unavailable"` path — hardening's classification-wait deadline,
  `promptguard_wait_seconds`, or a classifier fault; that text never reaches `classify_windows` and
  so is never recorded); a non-zero count → exit `unscanned`, nothing written. **Not** unscanned:
  `structural_blocked` (stage 2 blocked first; replay never asks for that text, and the attack
  corpus is meant to produce it) and `skipped_trusted` (the drivers set no trust lists; spec 5
  raises on it); a `BLOCKING_ERRORS` refusal (spec 1 — `blocked` with `refusal = True`, no
  `promptguard_state` at all) is an outcome outside this count. *(Corrected round 5: round 4
  counted every non-`scanned` state, so every structurally blocked attack would have refused the
  write.)* (6) write the cassette. (7) print
  `records=N texts=M windows_histogram={1: a, 2: b, …} unscanned=0` — ids and numbers only.
  *(Changed 2026-09-24, round 4: this printed `misses=0`, which is meaningless while recording —
  the wrapper cannot miss; the real failure, a text that never reached the classifier, would only
  have surfaced as a CI miss after the commit.)* `scripts/vendor_weights.py`'s `run_plan` (~:1031-1043
  at 403e9c5, `token=env.get(HF_TOKEN_ENV_VAR)`) is the precedent for reading `HF_TOKEN` from the
  environment and never echoing it.
- **Tiny-model test**: load `tests/fixtures/tiny_model` through the real loader — `tests/fakes.py`'s
  `materialize_hub_snapshot` + `PromptGuardClassifier.load(model_id=…, revision=…, cache_dir=…,
  local_files_only=True)` (`PromptGuardClassifier.load`, `promptguard/classifier.py` ~:85 at
  403e9c5; `tests/test_model_fetcher.py` uses the same fixture end to end, e.g.
  `test_a_mocked_fetch_reaches_a_really_loaded_classifier`); random weights are fine — the test
  asserts *equivalence*, not values. The fixture's `BENIGN`/`INJECTION` labels pass v1.2.1's label
  check without a `_PINNED_GENERIC_LABEL_INDICES` entry. Patch the recorder's acquisition seam with
  a function that performs that load **and asserts the `model_id=` / `revision=` keywords it was
  called with equal the manifest pin the test supplied** — that assertion is what tests step 4's
  structural revision guard. The nearest precedent for driving the fixture's real tokenizer is
  `tests/test_stage3_promptguard.py`'s `test_search_window_count_is_content_dependent` (sets
  `HF_HUB_OFFLINE`, loads the tokenizer with `local_files_only=True`); the equivalence test still
  goes through the real loader.
  **The fixture cannot classify a full-length chunk — drive it with a reduced window** (added
  2026-09-19, validation round 1, verified empirically against the committed fixture). Its
  `config.json` sets `max_position_embeddings: 64` while `promptguard/classifier.py` chunks at
  `MAX_SEQ_LEN = 512` and tokenizes each chunk with `max_length=512`; a 512-token chunk raises
  `RuntimeError("The size of tensor a (512) must match the size of tensor b (64) at non-singleton
  dimension 1")`. No existing test drives text long enough to exceed the fixture's 64-token
  position ceiling — `tests/test_model_fetcher.py` does classify with it (e.g.
  `test_a_mocked_fetch_reaches_a_really_loaded_classifier`), but only on a short phrase well inside
  one window — so the ceiling has never bitten, but every test in this story classifies multi-window
  text. *(Corrected 2026-09-24, round 4: this said nothing classifies with the fixture, which is
  false on 403e9c5.)* So these tests monkeypatch **both** `promptguard.classifier.MAX_SEQ_LEN` **and**
  `promptguard.classifier.CHUNK_OVERLAP` — to **32 and 8** — for the duration, which makes a
  "two-window text" ~56 tokens rather than >512 and keeps every window inside the fixture's
  position budget. *(Corrected 2026-09-19, validation round 2: round 1 patched `MAX_SEQ_LEN` alone,
  which is unsound. `_chunk_text` computes `step = MAX_SEQ_LEN - CHUNK_OVERLAP`
  (`_chunk_text`, `promptguard/classifier.py` ~:203, the `step` line ~:221 at 403e9c5), so
  `MAX_SEQ_LEN = 32` against the unpatched `CHUNK_OVERLAP = 64` gives `step = -32`, `range(start, len, -32)` is empty, `chunks == []`, and `classify()` /
  `classify_windows()` return `(0.0, [])` — zero windows. The budget-error criterion would have been
  unreachable and the equivalence tests would have passed vacuously. There is no admissible value of
  `MAX_SEQ_LEN` alone: it must be ≤ 64 to fit the fixture, and any value ≤ 64 makes `step` ≤ 0
  against the default overlap. Both constants move together, and `step > 0` is asserted in the test
  helper that applies the patch.)* The reduced window is a property of the *fixture*, not of the format:
  the cassette shape, the budget behaviour and the equivalence claim are all window-count-relative,
  so nothing about the assertion weakens. A comment in the test says why, and the real-model
  recordings (US-002, US-003) run at the real `MAX_SEQ_LEN`.
- **Determinism**: float `repr` round-trips exactly; sorted keys; no timestamps inside `records`.
- **NOTICE**: append to the "Third-party model weights — Llama Prompt Guard 2" section one paragraph
  stating that `tests/corpus/cassettes/` holds numeric outputs of the named model(s) at the named
  revision(s), under the Llama licence `NOTICE` already cites, with no model internals and no input
  text (finding 15); a governance-style test asserts the paragraph names every cassette's
  `model_id`.

**Acceptance Criteria:**
- [ ] Cassette format as specified; `from_cassette` round-trips; the 2 MB cap lint exists.
- [ ] `RecordingClassifier` records unbudgeted and applies the budget itself; a test with
      `max_chunks=1` on a two-window text asserts the recorded list has two scores and the drive
      raised the budget error both live and in replay. The tiny-model tests monkeypatch
      `MAX_SEQ_LEN` to 32 **and `CHUNK_OVERLAP` to 8** so every window fits the fixture's
      `max_position_embeddings: 64` and `step` stays positive; the patch helper asserts
      `MAX_SEQ_LEN - CHUNK_OVERLAP > 0`, and a test asserts the unpatched fixture raises on a
      full-length chunk, so both constraints are pinned rather than rediscovered.
- [ ] Live-vs-replay equivalence on the tiny model across all three routes and both configs;
      cassette contains no marker text; deleting an entry → `UnrecordedRecordError` with the id.
- [ ] Recorder CLI: `--help` offline; the `model_env_set`, `model_id_not_allowed`, `not_pinned`,
      `not_loaded` and `unscanned` exits each write nothing (and a mini corpus holding a
      structurally blocked record still writes, with `unscanned=0`), and `model_id_not_allowed` /
      `model_env_set` / `not_pinned` are reached with the acquisition seam never called; a test
      asserts the seam receives `model_id=` and `revision=` equal to the manifest pin and the
      written cassette carries the same pair; output line format pinned; no token value can reach
      stdout / stderr (a test injects a fake token into the environment and asserts it is absent
      from captured output).
- [ ] `NOTICE` paragraph present; coverage test green.
- [ ] **The two tests the owner gates assert against are authored here, not at the gate** (added
      2026-09-19, validation round 1). US-002's and US-003's Independent Tests require (a) a
      full-corpus replay drive with `fallback=None` over every record × both configs reporting
      **zero misses**, and (b) a test asserting every record with `params.windows_min` has a recorded
      window count ≥ it, listing offenders by id. Neither existed in any story's criteria — US-001
      covered the mini-corpus tiny-model tests plus the CLI, US-004 the revision/versions guards and
      the one-cassette-per-model lint, and spec 5's gate criterion (d) is a later spec. Both are
      written **here**, in `tests/test_corpus_record.py`, and both skip with the reason
      `"no cassette recorded yet"` until US-002 commits one. An owner gate must be "run the documented
      procedure, paste the numbers" — never "author two new tests first".
- [ ] `kit_tools/testing/TESTING_GUIDE.md` rows for `tests/test_corpus_record.py` and
      `tests/corpus/cassettes/`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-004: The recording procedure and the staleness guards

**Priority:** P1

**Description:** As the owner about to record, I want the procedure written down once — environment,
tokens, cache, command, verification, commit — and the two cheap guards that catch a stale cassette
(a re-vendored model without a re-record; a cassette whose torch / transformers differ from the lock),
so the recordings are reproducible and a silent staleness cannot happen.

**Independent Test:** `tests/corpus/README.md` has a "Recording and re-recording" section whose
commands are copy-pasteable and put no token on a command line; `uv run pytest
tests/test_corpus_record.py -k staleness` passes: a cassette whose `revision` differs from the
manifest pin for its `model_id` fails a test naming both values; a cassette whose `torch` or
`transformers` differs from the locked versions produces a WARNING line in the report (spec 5) and
does not fail.

**Implementation Hints:**
- Procedure (README, later lifted into `docs/corpus.md` by spec 5 US-005):
  ```
  unset FORAGE_MODEL_ID FORAGE_MODEL_REVISION   # the recorder refuses to run with either set
  read -rs HF_TOKEN && export HF_TOKEN          # or FORAGE_MIRROR_TOKEN + FORAGE_WEIGHTS_MIRROR
  export HF_HOME="$HOME/.cache/forage-weights"  # any writable cache; the manifest verifies the files
  uv run python -m scripts.corpus.record --model-id meta-llama/Llama-Prompt-Guard-2-22M
  unset HF_TOKEN
  uv run pytest tests/test_corpus_record.py tests/test_corpus_harness.py -q
  git add tests/corpus/cassettes/ && git commit -m "corpus: record 22M cassette @<revision>"
  ```
  with the file-by-path alternative — the one-file credential recipe `docs/weights.md` carries after
  spec 0 US-001 (`umask 077`, `mktemp`, `trap`; the file loaded with `set -a; . "$f"; set +a`, never
  `export NAME=<value>`) — and the rule that the token never appears in argv or a log (CLAUDE.md
  invariant 6; `docs/weights.md` "Tokens, and least privilege for each"). *(Corrected 2026-09-24,
  round 4: this named an `--env-file` alternative, which is a `docker run` flag — neither the
  recorder nor `scripts/vendor_weights.py` has one.)* The procedure names the host it ran on; both
  recordings may run on the owner's lab host (owner decision 17).
- **When to re-record** (README): any `UnrecordedRecordError` in CI; a model re-vendoring (manifest
  revision change); a corpus addition (new texts); a sanitizer change that alters stage-3 inputs
  (search-text normalisation, extraction, the stage-3 join) — a refactor rotation of
  `sanitizer_revision` that leaves the inputs unchanged needs no re-record (the miss check is the
  real guard; `sanitizer_revision` in the cassette is informational).
- **Guards**: `cassette.revision == model_fetcher.read_manifest_pin(model_id=cassette.model_id).revision`
  (hard, `tests/test_corpus_record.py`; keyword `model_id` — the first positional parameter is
  `manifest_path`, so `read_manifest_pin(model_id)` would read a nonexistent manifest and return
  `None` for every cassette; a `None` pin fails naming the model id, and a cassette whose
  `model_id` is not in `ALLOWED_MODEL_IDS` fails too);
  torch / transformers versions vs `uv.lock` (soft: the report prints `cassette_versions_differ`
  with both values; spec 5 US-001 renders it). `tests/test_dependency_lock.py` shows how the lock
  is parsed in tests.
- Cassette size and count: one file per model; a lint test refuses two cassettes for one model id.

**Acceptance Criteria:**
- [ ] README section present with the exact commands, the token rule, and the re-record triggers.
- [ ] Hard guard (revision vs manifest pin) and soft guard (versions) implemented and tested; the
      one-cassette-per-model lint exists.
- [ ] `kit_tools/docs/GOTCHAS.md` gains "A cassette miss is the guard; `sanitizer_revision` in a
      cassette is a note" with the re-record triggers.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-002: Record the 22M cassette (owner gate)

**Priority:** P1

**Description:** As the owner, I want the default model's cassette recorded on the full corpus,
verified complete, its window-count promises checked, and committed — so spec 5 can generate the
first baseline. **Execution halts here for the owner** (ruling 13): the run needs the weights.

**Independent Test:** *Gate run* → `tests/corpus/cassettes/<slug>@<22M revision>.json` is committed;
`uv run pytest tests/test_corpus_record.py tests/test_corpus_harness.py -q` is green with
`fallback=None` (no fallback) on every record and both configs — zero misses; every record with
`params.windows_min` has a recorded window count ≥ it (a test lists offenders by id; the list is
empty); this file's Implementation Notes carry `### US-002 — 22M recording, <date>` with revision,
record / text counts, the window histogram, wall time, host CPU, and the cassette's size.

**Implementation Hints:**
- Run US-004's procedure. Record a **first pass**, run the `windows_min` test; for every offender
  (spec 2 US-003's straddle / density / sustained records, spec 3 US-003's `long_form`), lengthen
  the filler (same public-domain source, more paragraphs) and re-record — the recorder overwrites
  the cassette; repeat until the offender list is empty. Record the number of passes.
- Expect minutes, not hours: ~500 records × 3 surfaces' worth of texts, single-threaded CPU
  inference at 22M.
- `promptguard_threshold` and the contiguity keys do not affect *recording* (scores are rule-free);
  both configs are driven so the recorder's call log matches what the report will replay.
- The commit contains the cassette and any re-authored records only; message
  `corpus: record 22M cassette @<revision>`.

**Acceptance Criteria:**
- [ ] Cassette committed; zero misses on every record × both configs; `windows_min` offender list
      empty; re-authored records (if any) listed by id in Implementation Notes with the pass count.
- [ ] Implementation Notes section as in the Independent Test (numbers only).
- [ ] The cassette's `revision` equals the manifest pin (US-004 guard green); `NOTICE` names the
      22M model.
- [ ] Full test suite passes (`uv run pytest`)

### US-003: Record the 86M cassette if spec 0 enabled it (owner gate)

**Priority:** P1

**Description:** As the owner, I want the 86M cassette recorded with the same procedure when spec 0
(`feature-corpus-86m-enablement.md`) enabled the 86M — vendored, label-pinned, allowlisted,
released — so the model decision table has both columns; and, when spec 0 stopped at a recorded
`gate not run` state, the gap recorded so the report shows `unmeasured` rather than nothing.
**Depends on spec 0 having completed** (either outcome). *(Changed 2026-09-24, round 4, owner
decision 17: this story assumed vendoring was the only gate. On v1.2.1 it is not —
`_PINNED_GENERIC_LABEL_INDICES` (`promptguard/classifier.py` ~:30 at 403e9c5) has one entry, the
22M's, and `PromptGuardClassifier.load()` refuses a generic-label config whose exact
`(model_id, revision)` is not pinned (`model_labels_unexpected`); vendored 86M weights alone would
verify and then fail to load. The label pin is new semantic-evidence work, and spec 0 US-002 owns
it.)*

**Independent Test:** *Gate run* → either `tests/corpus/cassettes/<slug>@<86M revision>.json` is
committed with zero misses and the `windows_min` list empty (the 86M tokenizer differs; counts are
re-checked), and Implementation Notes carry `### US-003 — 86M recording, <date>`; or spec 0 ended in
a recorded `gate not run` state, `model_fetcher.ALLOWED_MODEL_IDS` does not contain the 86M, and
Implementation Notes read `### US-003 — not recorded — 86M not enabled` naming the spec 0 story and
reason that stopped it (ruling 6a).

**Implementation Hints:**
- `--model-id meta-llama/Llama-Prompt-Guard-2-86M`. Preconditions, all delivered by spec 0 and all
  checked by the recorder or the loader: the id is in `ALLOWED_MODEL_IDS` (else `model_id_not_allowed`,
  from the recorder's own step-1 check), `weights_manifest.json` pins it (else `not_pinned`), and its
  `(model_id, revision)` is in `_PINNED_GENERIC_LABEL_INDICES` or its config carries named labels
  (else `load()` logs `model_labels_unexpected` and the recorder exits `not_loaded`).
- Run on the host spec 0 used (the owner's lab host) when convenient; the 22M recording may run there
  too (owner decision 17). Record the host in Implementation Notes.
- The 86M windows differ from the 22M's (different tokenizer, model card: multilingual pre-training);
  a record that satisfied `windows_min` under 22M may not under 86M — re-author to satisfy both
  and re-record **both** cassettes if a record changes (record texts are the keys).
- Memory: the 86M working set is larger; run on the host, not in the 1 GiB container.

**Acceptance Criteria:**
- [ ] One of the two Independent-Test outcomes, recorded in Implementation Notes.
- [ ] If recorded: zero misses, `windows_min` list empty under both cassettes, `NOTICE` names the
      86M model, both cassettes' revisions equal their manifest pins.
- [ ] Zero runtime change (rulings 6, 6a) re-asserted at spec end: the ruling-6 `git diff --stat`
      set, taken against the epic branch's merge base with `main` (`git merge-base main HEAD`, ruling 6a), is empty; `derive_sanitizer_revision({})` unchanged;
      `scripts.export_contract --check` green.
- [ ] Full test suite passes (`uv run pytest`)

## Edge Cases

- Two records with identical stage-3 text: one cassette entry; the recorder counts texts, not
  records (US-001).
- A record whose text exceeds the route's `max_chunks` during recording: the wrapper records the
  full list, raises the budget error, and the drive returns the error outcome (`blocked`,
  `refusal = True` — a row of spec 1's `BLOCKING_ERRORS`, not an `unscanned` result); replay
  reproduces it (US-001).
- The classifier loads but `/health` would be `degraded` for another reason (cache) — irrelevant to
  the recorder; it checks `classifier.loaded` only (US-001).
- The recorder is run with a fallback-bearing replay by mistake — impossible: it constructs the
  wrapper over a real `PromptGuardClassifier` (US-001).
- A cassette file edited by hand — the exact-match baseline (spec 5) drifts and the `revision`
  guard still holds; there is no signature, and none is needed for a test fixture (US-004).
- The 86M allowlist entry missing (spec 0 did not enable it) — the recorder exits 2
  `model_id_not_allowed` from its **own** membership check, before acquisition; the loader has no
  allowlist reason to give (`acquire_and_load` never consults `ALLOWED_MODEL_IDS`, and without the
  check it would fail later on `manifest_model_unknown` and surface as `not_loaded`). US-003 then
  takes its `not recorded — 86M not enabled` branch (US-001, US-003). *(Corrected 2026-09-24,
  round 4.)*
- The 86M allowlisted and pinned but its labels unpinned (a spec 0 defect) — `load()` refuses with
  `model_labels_unexpected`, the recorder exits `not_loaded`; fix spec 0, never the recorder
  (US-003).
- `FORAGE_MODEL_ID` or `FORAGE_MODEL_REVISION` exported in the owner's shell — the recorder exits
  `model_env_set` before anything else (US-001).

## Out of Scope

- The baseline and floors (spec 5); any stage-3 rule change (ruling 6).
- Recording in a container or in CI.
- Chunk text in cassettes (ruling 10).

## Assumptions

- `model_fetcher.acquire_and_load` can be called from a host script with the same environment
  variables the container uses and the same verified cache layout under `HF_HOME`.
- Hardening spec 7 US-006 shipped the per-model seams the recorder uses: `acquire_and_load(classifier,
  *, model_id=, revision=, …)` and `read_manifest_pin(manifest_path=…, *, model_id=…) ->
  WeightsManifest | None` (verified at 403e9c5). `FORAGE_MODEL_ID` selects the model for the
  *service* only (`resolve_model_id()`); the recorder selects by keyword and refuses the variable.
- Float scores from CPU inference are stable across runs on the same host and torch version; a
  re-record on a different torch minor may move borderline scores — the soft guard reports it and
  the baseline diff shows it.

## Technical Considerations

- A cassette is ~100 bytes per text; ~1 500 texts ≈ 150 KB — well under the 2 MB cap.
- The recorder imports the drivers, which import `retrieval_app`; `--help` must still exit 0
  offline (nothing at module scope reads config or the network — `scripts/bench_promptguard`
  precedent, hardening spec 7 US-003).
- The Llama licence attribution for cassettes is a `NOTICE` paragraph, not a per-file header.
- **Cassettes are a finer bypass map than the outcome table.** Per-window scores show each record's
  margin to threshold, not only which side it lands on. Ruling 14b (publish the corpus and its bypass
  catalog) covers them — the corpus texts and both models are public, so the scores are reproducible
  by anyone with the same access — but 14b names the corpus and spec 5's outcome table, not
  cassettes; spec 5 US-005's `docs/corpus.md` disclosure paragraph should name them so the trade-off
  travels with the artifact that creates it. *(Added 2026-09-24, round 4, security info.)*

### Validation residue — closed at `needs-work` (2026-09-19, `/kit-tools:validate-epic`, 3 rounds)

Thirty reviewers over three rounds took this epic from 19 criticals to 0 open; the items below are
the warnings that remained when validation was deliberately closed rather than chased to zero — the
same call, for the same reason, that `epic-forage-hardening` recorded on the same day: the precision
reviewers surface a new layer every round, and **every code anchor in this spec predates eight
unexecuted hardening specs** (ruling 5), so precision spent now is precision spent twice. Re-verify
against the post-hardening tree at execution time; treat each item as a decision the implementer
makes deliberately, not a defect to discover.

- **The tiny fixture proves structure, not semantics.** `tests/fixtures/tiny_model`'s tokenizer is a
  toy WordPiece vocabulary and its weights are random, so the live-vs-replay equivalence test shows
  the record/replay loop is faithful — it says nothing about scores. That is the correct scope; do
  not let it be read as model validation.
- **The `MAX_SEQ_LEN` / `CHUNK_OVERLAP` patch must stay narrow, and the trap next to it is silent.**
  Patching either constant alone gives a non-positive step and zero windows, which passes tests
  vacuously rather than failing loudly. The helper asserts `step > 0`; keep that assertion and keep
  the patch scoped to the tiny-model tests.
- **The cassette filename embeds the revision (`<slug>@<revision>.json`) while US-004's lint refuses
  two cassettes for one model id.** Those two rules collide the first time a revision rotates;
  decide whether the lint is per (model id, revision) or whether old cassettes are deleted.
- **Cassettes are unsigned**, which is sound only while `ReplayClassifier` stays confined to
  tests and CI. Nothing enforces that boundary; if it ever loads outside a test, revisit.
- All four stories are `P1`, and US-001 and US-004 each bundle several concerns under one title.

### Validation round 4 — 2026-09-24 (post-hardening re-anchor)

Six reviewers against `main` = `403e9c5` (`v1.2.1`). Every finding below was re-verified on that
tree before it was applied.

**Fixed.**
- *86M gate (critical, salty; owner decision 17):* US-003 depends on spec 0 (vendor + label pin +
  allowlist + release); its gap branch is `not recorded — 86M not enabled`, replacing `access
  pending`; `depends_on` names `corpus-86m-enablement`; Known risks names the label pin as the
  likelier blocker; both recordings may run on the lab host.
- *Model selection:* `--model-id` is no longer a `FORAGE_MODEL_ID` alias (that variable does not
  reach `acquire_and_load`, and the lifespan and `derive_sanitizer_revision` do read it); the
  recorder passes `model_id=` / `revision=` by keyword and refuses `model_env_set`.
- *Allowlist (security):* the recorder checks `ALLOWED_MODEL_IDS` itself (`model_id_not_allowed`,
  before acquisition), because `acquire_and_load` does not; no runtime change.
- *Revision guard:* `revision_mismatch` removed — the classifier retains no loaded id/revision. The
  pin is resolved once and passed to a loader that refuses any other revision, and the same local
  is written into the cassette; `not_pinned` covers a missing entry; the CLI test asserts the seam's
  keywords.
- *Signatures:* `read_manifest_pin(model_id=…)` returns `WeightsManifest | None` → `.revision` with
  a `None` path, in US-001 and US-004's guard; Open Question closed.
- *Edge Case:* the "allowlist reason from the loader" bullet rewritten (the loader has none); two
  new edge cases (label pin missing; model env exported).
- *Factual:* the "nothing classifies with the tiny fixture" claim corrected; the lifespan's
  `WeightAcquisition(…, model_id=…)` call quoted as shipped.
- *Anchors:* every `file:line` re-cited by symbol with the `403e9c5` line as a hint (model_fetcher,
  classifier, retrieval_app, docs/configuration.md, vendor_weights); Known-risks bullet rewritten.
- *Info applied:* `sanitizer_revision` pinned to `derive_sanitizer_revision({})` with the model env
  unset; `misses=0` replaced by an `unscanned` count that refuses the write; env-list duplicate
  removed; procedure gains `unset FORAGE_MODEL_ID FORAGE_MODEL_REVISION` and a real file-by-path
  recipe in place of the `--env-file` (docker) wording; cassette-score disclosure note (ruling 14b)
  in Technical Considerations; `test_search_window_count_is_content_dependent` named as precedent.

**Not applied (superseded).** Reviewer 1's "name the hardening US-005 gate" phrase and reviewer 6's
"expect `access pending` on v1.2.1" — both replaced by owner decision 17 / ruling 6a's
`86M not enabled`.

**Carried.**
- The four items deliberately not fixed this round — (a) an `injection_spans` exposure counter,
  (b) the hermetic gate's blindness to a real-model load failure, (c) a `rehomed_direct` framing
  criterion, (d) a PII scrub rule for LLMail-Inject rows — belong to specs 1, 5, 2 and 2; none
  changes this spec.
- Epic ruling 13 still reads `otherwise not recorded — access pending`; this spec follows ruling
  6a's `86M not enabled`. The wrapper wording is outside this spec's edit. *(Closed — wrapper
  aligned in round 6.)*

**Round 5 (same day):** the round-4 `unscanned` count was too broad — "not `scanned` for any reason
but the budget" swept in `structural_blocked` and `skipped_trusted`, so the real attack corpus would
always exit `unscanned`, and it mixed a state test with an outcome test (the budget error carries no
`promptguard_state`). Now defined positively as the `model_unavailable` states
(`unavailable_blocked` / `unavailable_allowed`, `/search` omit `promptguard_unavailable`), verified
against `_derive_promptguard_state` (`pipeline/stage4_structuring.py` ~:96) at 403e9c5; the CLI
test gains a structurally-blocked record that must still write. Still carried for the wrapper:
reviewer 3's note that the Decomposition table's spec-4 Dependencies cell should list
`corpus-86m-enablement`, matching this spec's frontmatter.

**Round 6 (same day):** both carried wrapper items are closed: wrapper aligned in round 6. Epic
ruling 13's spec-4 text now reads `not recorded — 86M not enabled` (wrapper ~:105-107, ~:161).
The Decomposition table's spec-4 Dependencies cell now reads "`corpus-attacks`, `corpus-benign`;
US-003 on `corpus-86m-enablement`" (~:208). Neither needs a change here.

## Related Documentation

- `docs/weights.md` (tokens, mirror, manifest); `docs/configuration.md` (`FORAGE_MODEL_ID`,
  `HF_HOME`); `tests/corpus/README.md`; `kit_tools/docs/GOTCHAS.md`.

## Implementation Notes

<!-- US-002 / US-003 gate sections as specified. Numbers only. -->

## Refinement Notes

### Research Findings

**Decision:** Record unbudgeted, apply the budget at replay.
**Rationale:** A route's `max_chunks` can change without the model changing; storing the full window
list keeps one recording valid across budget edits.
**Source:** `PromptGuardClassifier.classify_windows` (`promptguard/classifier.py` ~:260, the
`max_chunks` check before inference ~:282 at 403e9c5); the `max_promptguard_chunks` rows in
`docs/configuration.md` (~:920 extraction, ~:1010 retrieve at 403e9c5). *(Re-cited 2026-09-24,
round 4: the old `:179-182` / `:487` now land in the label-pin block and the weights section.)*

**Decision:** Prove equivalence on the tiny fixture model with the real loader.
**Rationale:** `tests/fixtures/tiny_model` is a real, loadable DeBERTa classifier; the model-fetcher
tests already load it end to end, so the record → replay loop is testable without weights.
**Source:** `tests/fixtures/README.md`; `tests/test_model_fetcher.py`.

**Decision:** A cassette miss is a hard error; a `NOTICE` paragraph covers the model outputs.
**Rationale:** Every cassette practitioner's lesson is that a miss must be loud (landscape finding
14, `search_snippet`); committing model outputs at scale to a public repo makes the attribution
cheap now and expensive to retrofit (finding 15).
**Source:** https://github.com/cheneeheng/mcp-cassette; https://raw.githubusercontent.com/facebookresearch/PurpleLlama/main/LICENSE.

### Scope Adjustments

- The procedure story (US-004) runs before the gates so the owner follows a written procedure.

### Decisions Made

- One cassette per model id; revision must equal the manifest pin (hard); versions are soft.

## Clarifications

### Session 2026-09-19
- Q: Weights in CI? → A: No — cassettes (owner decision 2).
- Q: Both models from the start? → A: Yes when the 86M is vendored (landscape finding 7); otherwise
  `unmeasured`.

### Session 2026-09-24
- Q: Who makes the 86M recordable? → A: Spec 0 — vendoring, label pin, allowlist, release (owner
  decision 17). US-003 depends on it; its gap branch reads `not recorded — 86M not enabled` (ruling
  6a). Both recordings may run on the owner's lab host.

## Open Questions

- [x] Exact `acquire_and_load` / `read_manifest_pin` signatures after hardening spec 7 — **closed
      2026-09-24** (round 4): `acquire_and_load(classifier, *, model_id=None, cache_root=None,
      revision=None, manifest_path=None, metrics=None) -> bool`; `read_manifest_pin(manifest_path=
      MANIFEST_PATH, *, model_id=DEFAULT_MODEL_ID) -> WeightsManifest | None`. US-001 and US-004 are
      written against these.

## Known risks (planning)

- **Anchor drift** (ruling 5; added 2026-09-19, validation round 2; **re-verified at `main` =
  `403e9c5` on 2026-09-24**, validation round 4). The round-2 anchors, read at `20ddb2a`, had all
  moved — by 9 to 500+ lines — when hardening landed; this spec now cites symbols with the
  `403e9c5` line as a hint. Re-grep by symbol before relying on a line. Current hints:
  `model_fetcher.py` — `RETRY_INITIAL_BACKOFF_S` ~:243 (now in the top constants block),
  `repo_dirname` ~:490, `resolve_model_id` ~:938, `read_manifest_pin` ~:997, `_fetch_reason` ~:1013,
  `acquire_and_load` ~:1630 / `_acquire_and_load` ~:1690, `WeightAcquisition` ~:1878,
  `attempt_once` ~:1949, `run` ~:1978; `promptguard/classifier.py` — `_PINNED_GENERIC_LABEL_INDICES`
  ~:30, `MAX_SEQ_LEN` / `CHUNK_OVERLAP` ~:33-34, `load` ~:85, `_chunk_text` ~:203 (`step` ~:221),
  `classify_windows` ~:260 (budget check ~:282); `retrieval_app.py` — `lifespan` ~:1642 (allowlist
  refusal ~:1644-1646, `WeightAcquisition(…, model_id=model_id, …)` ~:1867). Values the spec leans
  on are unchanged (512 / 64, the chunk-then-budget order, the fixture's 64-position ceiling). The
  hardening spec's own correction still holds: **the lifespan seam is `WeightAcquisition`, not
  `acquire_and_load`** (its ruling R29 as corrected in round 4).
- **The 86M gate is spec 0, not access alone** (round 4). The likelier blocker is not Meta's
  gated-repo approval but the label pin: vendored 86M weights with generic labels would verify and
  then be refused at `load()` until spec 0 US-002 pins `(model_id, revision)` from evidence. If spec
  0 stops (licence, access pending, inconclusive evidence), US-003 records `not recorded — 86M not
  enabled`. For the 22M, the mirror read token remains the alternative to an HF token.
- The 86M tokenizer changes window counts; re-authoring may ripple into the 22M cassette.
