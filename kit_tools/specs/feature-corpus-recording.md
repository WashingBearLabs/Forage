<!-- Template Version: 2.5.0 -->
---
feature: corpus-recording
status: active
session_ready: true
depends_on: [corpus-attacks, corpus-benign]
vision_ref: "T2.3 — Injection regression corpus (CI)"
type: epic-child
size: L
epic: forage-injection-corpus
epic_seq: 4
epic_final: false
execution_order: [US-001, US-004, US-002, US-003]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Score Recording — Cassette Format, File-Backed Replay, the Host-Side Recorder, the 22M and 86M Recordings

> Spec 4 of 5 in `epic-forage-injection-corpus` (owner decision 2; rulings 10, 13). Two owner gates
> (US-002, US-003): they need a Hugging Face token on an approved account or the mirror read token,
> and the vendored 86M weights from hardening spec 7 US-005. Tokens enter the environment only
> (`read -rs`), never argv, never a log line (CLAUDE.md invariant 6).

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
  every record; it refuses an unloaded classifier and asserts the loaded revision equals the
  manifest pin.
- Live-vs-replay equivalence is proven hermetically with the real loader on
  `tests/fixtures/tiny_model`.
- The 22M cassette is recorded and committed; the 86M cassette is recorded if the weights were
  vendored, else the gap is recorded; `windows_min` promises hold on the recorded window counts.
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
offline; a recorder run whose classifier fails to load exits 2 with `not_loaded` and writes nothing.

**Implementation Hints:**
- **File**: `tests/corpus/cassettes/<slug>@<revision>.json` where `slug =
  model_fetcher.repo_dirname(model_id)` (`model_fetcher.py:479`; e.g.
  `models--meta-llama--Llama-Prompt-Guard-2-22M`, or the bare `meta-llama--…` form — pick one and pin
  it with a test) and `revision` is the full commit SHA. Content:
  `{"format": 1, "model_id": …, "revision": …, "recorded_at": ISO-8601 UTC, "sanitizer_revision": …,
  "torch": …, "transformers": …, "configs": ["default", "contiguity"], "records": {sha256hex:
  {"scores": [floats as repr], "windows": n}}}`; `json.dumps(sort_keys=True, indent=2)` + trailing
  newline; written to a temp file in the same directory then `os.replace` (atomic). Cap: a cassette
  ≤ 2 MB (a lint test).
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
  [--configs default,contiguity]`: (1) `classifier = PromptGuardClassifier()`; acquire through the
  same path the lifespan uses — `model_fetcher.acquire_and_load(...)` (`model_fetcher.py:1530`;
  the lifespan's call site `retrieval_app.py:1315-1330` shows the arguments; `HF_HOME`, `HF_TOKEN`,
  `FORAGE_WEIGHTS_MIRROR`, `FORAGE_MIRROR_TOKEN` are read by that function from the environment
  exactly as in the container — nothing token-shaped is parsed here; `FORAGE_MODEL_ID` selects the
  model after hardening spec 7 US-006, so `--model-id` is an alias that sets the same environment
  key before acquisition); (2) refuse unless `classifier.loaded` (exit 2, stderr `not_loaded`,
  nothing written — a fail-closed run measures nothing; hardening ruling R29); (3) assert the loaded
  revision equals `model_fetcher.read_manifest_pin(...)` for the model id (`:924`; exit 2 `revision_mismatch`);
  (4) `drive_all(load_corpus(), RecordingClassifier(classifier), configs=[…])`; (5) write the
  cassette; (6) print `records=N texts=M windows_histogram={1: a, 2: b, …} misses=0` — ids and
  numbers only. `scripts/vendor_weights.py:403-425` is the precedent for reading `HF_TOKEN` from the
  environment and never echoing it.
- **Tiny-model test**: load `tests/fixtures/tiny_model` through the real loader — `tests/fakes.py`'s
  `materialize_hub_snapshot` + `PromptGuardClassifier.load(revision=…, cache_dir=…,
  local_files_only=True)` (`promptguard/classifier.py:47-70`; `tests/test_model_fetcher.py` uses the
  same fixture end to end); random weights are fine — the test asserts *equivalence*, not values.
  Patch the recorder's acquisition seam with a function that performs that load so the CLI path is
  covered too.
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
      raised the budget error both live and in replay.
- [ ] Live-vs-replay equivalence on the tiny model across all three routes and both configs;
      cassette contains no marker text; deleting an entry → `UnrecordedRecordError` with the id.
- [ ] Recorder CLI: `--help` offline; `not_loaded` and `revision_mismatch` exits write nothing;
      output line format pinned; no token value can reach stdout / stderr (a test injects a fake
      token into the environment and asserts it is absent from captured output).
- [ ] `NOTICE` paragraph present; coverage test green.
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
  read -rs HF_TOKEN && export HF_TOKEN          # or FORAGE_MIRROR_TOKEN + FORAGE_WEIGHTS_MIRROR
  export HF_HOME="$HOME/.cache/forage-weights"  # any writable cache; the manifest verifies the files
  uv run python -m scripts.corpus.record --model-id meta-llama/Llama-Prompt-Guard-2-22M
  unset HF_TOKEN
  uv run pytest tests/test_corpus_record.py tests/test_corpus_harness.py -q
  git add tests/corpus/cassettes/ && git commit -m "corpus: record 22M cassette @<revision>"
  ```
  with the `--env-file`-by-path alternative and the rule that the token never appears in argv or a
  log (CLAUDE.md invariant 6; `docs/weights.md` "Tokens, and least privilege for each").
- **When to re-record** (README): any `UnrecordedRecordError` in CI; a model re-vendoring (manifest
  revision change); a corpus addition (new texts); a sanitizer change that alters stage-3 inputs
  (search-text normalisation, extraction, the stage-3 join) — a refactor rotation of
  `sanitizer_revision` that leaves the inputs unchanged needs no re-record (the miss check is the
  real guard; `sanitizer_revision` in the cassette is informational).
- **Guards**: `revision == read_manifest_pin(model_id)` (hard, `tests/test_corpus_record.py`);
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

### US-003: Record the 86M cassette if vendored (owner gate)

**Priority:** P1

**Description:** As the owner, I want the 86M cassette recorded with the same procedure when
hardening spec 7 US-005 vendored the 86M weights, so the model decision table has both columns —
and, when it did not, the gap recorded so the report shows `unmeasured` rather than nothing.

**Independent Test:** *Gate run* → either `tests/corpus/cassettes/<slug>@<86M revision>.json` is
committed with zero misses and the `windows_min` list empty (the 86M tokenizer differs; counts are
re-checked), and Implementation Notes carry `### US-003 — 86M recording, <date>`; or
`weights_manifest.json` carries no 86M entry and Implementation Notes read `### US-003 — not
recorded — access pending` naming the hardening story that gates it.

**Implementation Hints:**
- `--model-id meta-llama/Llama-Prompt-Guard-2-86M`; the `FORAGE_MODEL_ID` allowlist (hardening spec 7
  US-006) must contain it; the manifest pin for it comes from hardening US-005.
- The 86M windows differ from the 22M's (different tokenizer, model card: multilingual pre-training);
  a record that satisfied `windows_min` under 22M may not under 86M — re-author to satisfy both
  and re-record **both** cassettes if a record changes (record texts are the keys).
- Memory: the 86M working set is larger; run on the host, not in the 1 GiB container.

**Acceptance Criteria:**
- [ ] One of the two Independent-Test outcomes, recorded in Implementation Notes.
- [ ] If recorded: zero misses, `windows_min` list empty under both cassettes, `NOTICE` names the
      86M model, both cassettes' revisions equal their manifest pins.
- [ ] Zero runtime change (ruling 6) re-asserted at spec end: the `git diff --stat` set is empty,
      `derive_sanitizer_revision({})` unchanged, `scripts.export_contract --check` green.
- [ ] Full test suite passes (`uv run pytest`)

## Edge Cases

- Two records with identical stage-3 text: one cassette entry; the recorder counts texts, not
  records (US-001).
- A record whose text exceeds the route's `max_chunks` during recording: the wrapper records the
  full list, raises the budget error, and the drive returns the error outcome; replay reproduces it
  (US-001).
- The classifier loads but `/health` would be `degraded` for another reason (cache) — irrelevant to
  the recorder; it checks `classifier.loaded` only (US-001).
- The recorder is run with a fallback-bearing replay by mistake — impossible: it constructs the
  wrapper over a real `PromptGuardClassifier` (US-001).
- A cassette file edited by hand — the exact-match baseline (spec 5) drifts and the `revision`
  guard still holds; there is no signature, and none is needed for a test fixture (US-004).
- The 86M allowlist entry missing — the recorder exits 2 with the allowlist reason from the loader
  (US-003).

## Out of Scope

- The baseline and floors (spec 5); any stage-3 rule change (ruling 6).
- Recording in a container or in CI.
- Chunk text in cassettes (ruling 10).

## Assumptions

- `model_fetcher.acquire_and_load` can be called from a host script with the same environment
  variables the container uses and the same verified cache layout under `HF_HOME`.
- Hardening spec 7 US-006 selects the model by `FORAGE_MODEL_ID` and exposes the manifest pin per
  model through `read_manifest_pin` (or its successor); the recorder uses whatever that spec shipped.
- Float scores from CPU inference are stable across runs on the same host and torch version; a
  re-record on a different torch minor may move borderline scores — the soft guard reports it and
  the baseline diff shows it.

## Technical Considerations

- A cassette is ~100 bytes per text; ~1 500 texts ≈ 150 KB — well under the 2 MB cap.
- The recorder imports the drivers, which import `retrieval_app`; `--help` must still exit 0
  offline (nothing at module scope reads config or the network — `scripts/bench_promptguard`
  precedent, hardening spec 7 US-003).
- The Llama licence attribution for cassettes is a `NOTICE` paragraph, not a per-file header.

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
**Source:** `promptguard/classifier.py:179-182`; `docs/configuration.md:487`.

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

## Open Questions

- [ ] Exact `acquire_and_load` / `read_manifest_pin` signatures after hardening spec 7 — non-blocking;
      the recorder adapts to what shipped.

## Known risks (planning)

- Meta's gated-repo approval is not instant; the owner gate may wait on it — the mirror read token is
  the alternative path.
- The 86M tokenizer changes window counts; re-authoring may ripple into the 22M cassette.
