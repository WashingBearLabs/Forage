<!-- Template Version: 2.5.0 -->
---
feature: corpus-gates
status: active
session_ready: true
depends_on: [corpus-recording]
vision_ref: "T2.3 — Injection regression corpus (CI)"
type: epic-child
size: L
epic: forage-injection-corpus
epic_seq: 5
epic_final: true
execution_order: [US-001, US-002, US-003, US-004, US-005]
created: 2026-09-19
updated: 2026-09-24
---

# Feature Spec: Gates and Reporting — Report, Generated Baseline + Floors, CI Summary, Decision Table, Docs and Close-Out

> Spec 5 of 6 (final; spec 0 added 2026-09-24) in `epic-forage-injection-corpus` (owner decisions 4, 17; rulings 6a, 11, 12, 16).
> Turns the recorded corpus into a number on every PR and closes the epic.

## Overview

With records, drivers and cassettes in place, this spec makes the measurement a gate: a report over
every record × route × live rule config × cassette, attributing each catch to the stage that made it
and each false positive to its genre and provenance; a generated baseline that must match exactly;
reviewed floors and ceilings set from the first measured numbers; the CI step summary that shows the
table on every PR; the offline pooling sweep that produces the decision inputs hardening deferred
here; and the documentation that makes "measured" a word other people can check.

## Goals

- One command produces the report deterministically; the JSON is the baseline, the Markdown is the
  CI summary; neither contains payload text.
- The gate is exact-match plus floors, plus pins, completeness and count floors (ruling 12); the
  full replay runs inside the `test` job in ≤ 60 s (ruling 16, measured).
- Floors and ceilings are **set from the measured baseline**, never aspirational (landscape finding
  8); stage-2 and stage-3 catch are reported separately per category (finding 6).
- The decision table for contiguity (R17) and the model default (owner decision 4) exists, with the
  offline poolers hardening handed over — and no default flips (owner decision 4). **The model half
  is delivered only if spec 0 enabled the 86M** (owner decision 17) and spec 4 US-003 recorded an 86M
  cassette; if spec 0 closed at a recorded `gate not run`, the table ships the contiguity half only
  and names the model half **pending** with its prerequisites (ruling 6a) — an all-`unmeasured` 86M
  column is never presented as an answer.
- Docs, README, SECURITY.md, vision and roadmap say what is measured and where; every `leaked`
  attack under `default` / 22M is filed as an audit finding.

## User Stories

Execution order: `[US-001, US-002, US-003, US-004, US-005]` (document order).

### US-001: The report — dimensions, stage attribution, provenance split, deterministic renderers

**Priority:** P1

**Description:** As a maintainer, I want `scripts.corpus.report` to run the whole corpus through the
drivers for every cassette and live rule config and render a deterministic JSON and Markdown report
— per category × route the four outcomes with the stage that produced each catch, per genre ×
route × provenance the false-positive rate, the classifier-only view, and a per-record outcome map —
so the gate has something exact to compare and a reviewer has something readable to judge.

**Independent Test:** `uv run pytest tests/test_corpus_report.py` passes hermetically on a
fourteen-record mini corpus (the original twelve plus the two flagged `/search` cases below) with
two synthetic cassettes: `render_json` output is byte-identical
across two runs and sorts every key; the attack table attributes a `structural_blocked` omit to
`stage2`, an `injection_detected` omit with `promptguard_state == scanned` to `stage3`, an
`invalid_url` / `blocked_url` omit to `url`, a `/extract` 422 `unsupported_format` (a real drive of
a lint-valid attack `text` record that carries its ≥ 12-character marker **plus a raw NUL** — spec
1's `BLOCKING_ERRORS` `unsupported_format` row lists NUL and > 5% `Cc` beside "no visible text",
and only the first two can coexist with a marker) to `refused`, a **flagged `/search` result**
(`suspicious == true`, no omission) replayed at 0.6 with no stage-2 hit to `stage3`, a flagged
`/search` result whose only signal is a SUSPICIOUS-class stage-2 pattern (replayed at 0.0) to
`stage2`, and the per-category stage rates sum to `catch` — totality is tested on flagged records
as well as blocked ones; the `rule` column reads `max_score` for a single-window 0.9 replay under
`default`, `contiguity` for a `[0.6, 0.6]` replay under `contiguity`, and `both` for a
`[0.9, 0.6]` replay under `contiguity`; a synthetic `skipped_trusted` result
raises naming its record id; the benign table splits `external` from `synthetic` and
never pools `over_defence_probe` into the headline FPR; `render_markdown` contains every record id
of a `leaked` attack and no marker substring; `uv run python -m scripts.corpus.report --help` exits
0 offline.

**Implementation Hints:**
- `scripts/corpus/report.py`: `build_report(records, cassettes: Sequence[ReplayClassifier], configs:
  Sequence[RuleConfig]) -> Report` (frozen dataclasses); `render_json(report) -> str`
  (`sort_keys=True`, `indent=2`, floats rounded to 4 dp, trailing newline); `render_markdown(report)
  -> str`; CLI `--json` / `--markdown` / `--write-baseline` / `--check` / `--sweep` (US-004), the
  `scripts/export_contract.py` `build_parser` / `main` / `REGEN_COMMAND` idiom (`REGEN_COMMAND`
  ~:105, `build_parser` ~:293, `main` ~:310 at 403e9c5).
- **Dimensions**: `models` (from cassettes; `unmeasured` column when a model has no cassette) ×
  `configs` (`default`, `contiguity`) × `routes` × `categories`: counts `blocked`, `flagged`,
  `neutralised`, `leaked`, rates `catch` (= blocked + flagged / n) and `block`; **stage attribution**
  per caught record: `stage2` (search `omit_reason == structural_blocked`; retrieve / extract
  `promptguard_state == structural_blocked` or `structural_flags != []` with no `injection_detected`),
  `stage3` (`omit_reason == injection_detected`; `injection_detected` with `promptguard_state ==
  scanned`), `url` (`/search` omit `invalid_url` / `blocked_url` — a 200 omission; `/retrieve`'s
  422 URL refusals cannot come from a record, because spec 1's driver patches `validate_url` and
  `fetch_url`, and they are harness errors there), `unavailable` (fail-closed:
  `promptguard_unavailable` omit / `unavailable_blocked`), **`refused`** (a `blocked` result with
  `signals["refusal"] is True` — exactly the rows of spec 1's `BLOCKING_ERRORS` table,
  `scripts/corpus/outcomes.py`, which the report **imports and never restates**: at 403e9c5
  `/extract` 422 `unsupported_format`, `/extract` 422 `content_too_large_to_classify`, and
  `/retrieve` 422 `content_too_large` with reason `promptguard_budget`; the last is inert at the
  shipped `retrieve.max_promptguard_chunks: 0`, `config.yaml` ~:87) — reported as
  `catch_stage2`, `catch_stage3`, `catch_url`, `catch_unavailable`, `catch_refused` rates per
  category (finding 6: a class carried only by stage 3, or only by stage 2, is visible). Busy /
  admission refusals, 400 / 404 / 503 / 5xx, request-validation 422s and `extraction_failed` never
  reach the report: spec 1's harness raises on them and stops the drive, which is louder than any
  column (invariant 5). **Attribution is a total function**: a report test asserts the per-category
  stage rates sum to `catch`, and a caught record matching no bucket raises naming its id, route
  and signals (never text); a test also asserts every `BLOCKING_ERRORS` row lands in `refused`, so
  a row spec 1 adds later cannot fall through. *(Added 2026-09-24, validation round 4: the
  four-bucket rule predated hardening's non-200 refusals. Round 5: the bucket's own code list
  contradicted spec 1's error map — it counted harness errors as catches and missed
  `unsupported_format` — so it now derives from that map.)*
- **A flagged `/search` result has one bucket, by precedence** (added 2026-09-24, validation round
  6). Spec 1's `/search` `flagged` is the one served result with `suspicious == true` and **no**
  `omit_reason`, so none of the omit-keyed buckets above matches it. `run_search_pipeline` sets
  `suspicious` from two reachable sources (`pipeline/orchestrator.py` at 403e9c5): a stage-2
  `SUSPICIOUS` verdict on any scanned field (the stage-2 loop, ~:1842-1843) and a stage-3 score
  `> 0.5` (~:1924-1925); the third, fail-open pass-through (~:1917-1921), is unreachable because
  every driver sends `promptguard_fail_closed: true`. The rule: a flagged `/search` result is
  **`stage3` when `signals["score"] > 0.5`, otherwise `stage2`** — strict `>` to match the
  orchestrator. When both sources fire the record counts once, under `stage3`, so the rates cannot
  double-count. The report (`scripts/`) never calls the tests-side `stage2_record_hits`; the
  invariant that a flagged `/search` result with `score <= 0.5` carries a stage-2 hit is asserted
  by a **test** (`tests/test_corpus_report.py`, using spec 1's `stage2_record_hits`), not raised by
  the report. On `/retrieve` / `/extract` a `flagged` result is already `stage2`
  (`structural_flags != []`): those routes expose no sub-threshold stage-3 signal (spec 1's
  route-asymmetry rule), and `unavailable_allowed` is unreachable under fail-closed.
- **`rule` column — stage-3 catches split by the rule that fired** (added 2026-09-24, validation
  round 6; spec 1's `RouteResult.signals` list already names this spec as its home). For every
  `stage3`-attributed record, `rule ∈ {max_score, contiguity, both}` — the values of
  `PromptGuardResult.rule` (`pipeline/stage3_promptguard.py` ~:104, ~:267-273), which is **not on
  the wire**. **Source: the replay classifier's call log** (spec 4 US-001: `calls`, each `sha`
  resolved to its cassette window list). Under `drive_all`'s sequential drive, the entries a record's
  drive appends are that record's. The rule is recomputed at the drive's config — `default`:
  `max_score` iff max `> 0.85`; `contiguity`: additionally a run of ≥ 2 consecutive windows `>= 0.5`
  — with the same predicates US-004's `poolers.py` pins, so this story creates `poolers.py` with
  `max_score` and `contiguity` and US-004 adds the rest. A flagged `/search` result attributed
  `stage3` at a score in (0.5, 0.85] has no firing rule: its `rule` is `sub_threshold`, the one
  extra value. The report carries `rule_max_score`, `rule_contiguity`, `rule_both` and
  `rule_sub_threshold` counts per category × route × config × model (the per-record map's shape is
  unchanged). The call log is the only source. The
  `promptguard_contiguity_detections` `/metrics` delta was considered and not used: it needs a
  per-record metrics read, and US-004's offline/live consistency test already covers any
  live/offline divergence it would catch.
- **`skipped_trusted` is out of the corpus's reach, and loud if it appears.** Contract 1.3.0's
  `PromptGuardState` (`pipeline/contract.py` ~:286) has a fifth member, returned by
  `_derive_promptguard_state` (`pipeline/stage4_structuring.py` ~:96) when a `/retrieve`
  `trusted_domains` entry matches — stage 3 never runs, by the caller's explicit choice. Spec 1's
  drivers never set `trusted_domains` / `verified_domains`, so a `skipped_trusted` record means a
  driver changed: `build_report` raises naming the record rather than inventing a bucket. Measuring a
  caller-opted-out trust tier is not a bypass measurement (Out of Scope).
- **Benign**: per genre × route × provenance (`external` = `third_party`, `synthetic` = `synthetic`
  / `owned`): `fpr` (= blocked + flagged / n), `blocked_rate`; headline `fpr_external` over every
  genre except `over_defence_probe` and `security_prose` (both reported on their own lines);
  `fpr_multilingual` named; and **`candidate_rejection_rate`** per genre — printed beside
  `fpr_external` so a reader can see how much the sampler had to discard (for reasons unrelated to
  stage 2) to assemble the corpus. **Formula:** `rejected / examined` per genre, where `examined` is
  every candidate the sampler drew and `rejected` is the sum over reasons; both come from spec 3
  US-001's emitted `{genre: {examined: n, rejections: {reason: count}}}`, and the per-reason
  breakdown is printed under the rate so a high number from `duplicate` is not read as a high number
  from `non_prose`. A rate is reported as `—` for a genre whose records are entirely synthetic
  (nothing was drawn). *(Added 2026-09-19, validation
  round 2: spec 3 discharged this honesty mechanism to "spec 5's report", which did not carry it.)*
- **Classifier-only view**: from each replay classifier's call log (spec 4 US-001) — for benign
  texts, the share whose max window score > 0.85 (`stage3_fpr`) and whose windows would fire the
  contiguity recipe; for attacks, the share whose max score > 0.85 (`stage3_recall`) — independent of
  what stage 2 did first (stage 3 is skipped after a structural block, so the live tables cannot show
  it; the call log can only show texts that reached stage 3 — say so in the report header).
- **Per-record map**: `records: {id: {route: {config: {model: [outcome, marker_on_wire]}}}}` — the
  part of the baseline that makes a drift diff name records. The second element is spec 1's
  `signals["marker_on_wire"]` boolean, carried verbatim. *(Added 2026-09-19, validation round 2: the
  round-1 leak-check fix had spec 1 promise "spec 5's report carries a blocked-but-leaked column
  sourced from this signal" and had US-005 file those records — but nothing here produced the signal,
  so the leak class was observed once at drive time and never gated. Three reviewers found this from
  both ends; a fourth caught that the round-2 edit itself never reached the file.)*
- **Blocked-but-leaked counts**: alongside the four outcome counts, `blocked_but_leaked` per
  (category × route × config × model) — the count of records whose outcome is `blocked` while
  `marker_on_wire` is true. A half-worked defence is not a success: US-005 files each one, and the
  per-record map above is what makes a new one turn the drift test red.
- **Warnings section**: `cassette_versions_differ` (spec 4 US-004 soft guard), `unmeasured_models`.
- **Never payload**: the Markdown lists ids; a test drives a record whose marker is a sentinel and
  asserts the sentinel is absent from both renderers' output.
- Boot once per (config, cassette) — `drive_all` (spec 1) — and keep the run ≤ 60 s (ruling 16;
  measure here and record in Implementation Notes).

**Acceptance Criteria:**
- [ ] `build_report` / `render_json` / `render_markdown` / CLI as specified; determinism test;
      stage-attribution (total — the sum invariant, the `refused` bucket and the flagged-`/search`
      precedence), `rule` column, provenance-split and classifier-only tests as in the Independent
      Test.
- [ ] `unmeasured` handling for a missing cassette; warnings section rendered.
- [ ] No payload text in either renderer (sentinel test).
- [ ] Wall time of the full corpus report on the developer machine recorded in Implementation Notes.
- [ ] `kit_tools/testing/TESTING_GUIDE.md` row for `tests/test_corpus_report.py`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-002: The gate — generated baseline, measured floors, pins, completeness, counts

**Priority:** P1

**Description:** As a maintainer, I want `tests/test_corpus_gate.py` to be red the moment the
measured numbers move, a floor is breached, a pinned record slips, a cassette entry is missing or
the corpus shrinks — and to say which record, which route, which model, in ids and numbers — so a
sanitizer change cannot land without its effect being seen.

**Independent Test:** `uv run python -m scripts.corpus.report --write-baseline` writes
`tests/corpus/baseline.json`; `uv run pytest tests/test_corpus_gate.py -q` is green; editing one
outcome in the baseline turns the drift test red with a unified diff naming the record id and the
regeneration command; lowering one floor in `tests/corpus/floors.json` below the measured value is
green and raising it above is red naming category / route / model; flipping one record's
`marker_on_wire` in the baseline turns the drift test red naming that record; copying a cassette to a temp
dir with one entry removed and pointing the gate at it is red with `UnrecordedRecordError` naming
the record id (finding 14); the `MIN_RECORDS` test from spec 1 is un-skipped and green.

**Implementation Hints:**
- **Baseline**: `tests/corpus/baseline.json` = `render_json(build_report(...))`, header comment
  impossible in JSON so the first key is `"_regenerate": "uv run python -m scripts.corpus.report
  --write-baseline"`; the drift test compares the live render to the file and, on mismatch, fails
  with `difflib.unified_diff` (capped at 200 lines) plus the command — the `drift_report` idiom
  (`drift_report`, `scripts/export_contract.py` ~:222-256 at 403e9c5; on the test side
  `TestTheCommittedArtifacts.test_the_committed_contract_is_what_the_app_generates`,
  `tests/test_contract_export.py` ~:245-249, and the `assert REGEN_COMMAND in report` pair at
  ~:409-412 / ~:435-438 — the shape US-002's drift test copies). *(Re-anchored 2026-09-24,
  validation round 4: the old `:130-136` citation now lands inside an unrelated config-knob
  description assertion.)*
- **Floors** (`tests/corpus/floors.json`, hand-edited, reviewed): `{"attacks": {category: {route:
  {model: {config: {"min_catch": x, "min_block": y}}}}}, "benign": {genre: {route: {model: {config:
  {"max_fpr": z}}}}}, "headline": {"max_fpr_external": …, "min_catch_all": …}}` — **set from the
  first baseline**: `min_catch` = measured catch rounded *down* to the nearest 0.05, `max_fpr` =
  measured rounded *up* to 0.05 (finding 8: never aspirational; PIDS-Bench found no operating point
  meeting F1 ≥ 0.95 and FPR ≤ 0.10 together). Structural categories' `plain`-variant behaviour is
  already pinned per record, so a category floor can sit below 1.0 without losing the regex
  promise. A PR that lowers a floor edits this file — visible in review.
  **Scaffold it, do not type it** (added 2026-09-19, validation round 1). With 16 categories × 3
  routes × up to 2 models × 2 configs on the attack side and 9 genres on the benign side, this is a
  hundred-plus-cell structure, and hand-entering it invites a transposed cell that silently weakens a
  floor nobody notices. `scripts/corpus/report.py` grows `--write-floors`, which emits the whole
  file from the first baseline with the two rounding rules already applied; the human step is
  *reviewing* the generated diff and deliberately tightening or loosening individual cells, which is
  where the judgement actually is. A completeness test asserts every (category|genre) × route ×
  model × config cell the corpus can produce has a floor — a missing cell is a gate that silently
  passes, which is the same failure as a too-low one.
- **Pins**: the generic pinned-outcome test from spec 1 now runs with the real cassettes and no
  fallback, on every config.
- **Completeness**: for every cassette × config, zero `UnrecordedRecordError` — a separate test
  from the drift test so a miss is reported as a miss.
- **Counts**: un-skip spec 1's `MIN_RECORDS` test (ruling 14 floors: ≥ 200 / ≥ 5 per category;
  ≥ 250 / ≥ 15 per genre / ≥ 30 probes; ≥ 6 languages; ≥ 20 `windows_min ≥ 3` verified against the
  cassette window counts).
- **Runtime**: the gate replays once and caches the `Report` at module scope for the other tests in
  the file; record the measured wall time on a GitHub runner from the first CI run in Implementation
  Notes (ruling 16; if > 60 s, the story records it and proposes the split, it does not silently
  sample).
- Message hygiene: every failure names ids, routes, models, configs and numbers — never text; reuse
  `RouteResult.summary()`.

**Acceptance Criteria:**
- [ ] `tests/corpus/baseline.json` and `tests/corpus/floors.json` committed; the five gate tests
      (drift, floors, pins, completeness, counts) green; the delete-an-entry test green.
- [ ] Floors set from the measured baseline by the rounding rule; Implementation Notes carry the
      first measured headline numbers (catch per category, `fpr_external`, `fpr_multilingual`,
      `over_defence_probe` FPR, per model × config) — numbers only.
- [ ] Failure messages verified payload-free (sentinel test on a forced drift).
- [ ] Wall time on a GitHub runner recorded.
- [ ] `tests/corpus/README.md` explains what each red means and the two commands (regenerate
      baseline; re-record).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-003: CI publication and the PR checklist

**Priority:** P1

**Description:** As a reviewer, I want the corpus table in the PR's job summary on every run and a
checklist line in the PR template, so a sanitization change shows its numbers where the review
happens — with the gate itself already inside the `test` job.

**Independent Test:** `uv run pytest tests/test_ci_workflow.py tests/test_governance_docs.py` passes
with new assertions: the `test` job has a step named `Publish the corpus report` after the `pytest`
step that runs `uv run python -m scripts.corpus.report --markdown` into `${GITHUB_STEP_SUMMARY}`
conditioned `if: always()` (the workflow's existing idiom); its index in the `test` job's run list
is greater than `runs.index(_FULL_SUITE_RUN)`; no new action, secret or permission;
`.github/pull_request_template.md` carries the corpus checklist line.

**Implementation Hints:**
- `.github/workflows/ci.yml` `test` job (`test:` ~:283-324 at 403e9c5; `timeout-minutes: 20`
  ~:289): hardening made it five steps — Checkout, Install uv, Sync dev environment, **Verify
  sanitizer revision derivation** (`uv run pytest -q tests/test_sanitizer_revision.py`), then the
  step named `pytest` (`uv run pytest -q`). Append one step **after the step named `pytest`** (not
  merely after "a pytest step" — two now exist): `{ echo "### Injection corpus"; uv run python -m
  scripts.corpus.report --markdown; } >> "${GITHUB_STEP_SUMMARY}"` with `if: always()` so a drift
  failure still publishes the table. The precedent is the `searxng-smoke` job's step **"Record the
  advisory outcome"** (~:1500-1521), which appends to `GITHUB_STEP_SUMMARY` under `if: always()`;
  `always()` appears three times in the file and `!cancelled()` nowhere, so this reuses the idiom
  rather than introducing one. *(Corrected 2026-09-24, validation round 4: the hint prescribed
  `!cancelled()` and called the searxng step its precedent; it is not.)* Keep the job's
  `timeout-minutes`; `actionlint` runs in `lint`.
- `tests/test_ci_workflow.py`: extend the `test`-job assertions — the class is **`TestTestJob`**
  (~:993-1121 at 403e9c5, before `TestBuildAmd64Job`) and the idiom is `_run_text(jobs, "test")`;
  locate the full-suite step the way `test_sanitizer_revision_step_runs_before_the_full_suite` does
  (`runs.index(_FULL_SUITE_RUN)`, `_FULL_SUITE_RUN` ~:990) and assert the corpus step's index is
  greater. `test_test_job_applies_no_selection_filters` inspects only lines starting `uv run pytest`,
  so the new step does not collide with it. *(Corrected 2026-09-19, validation round 1: the hint
  cited `:670-678`, which is inside `TestLintJob` (~:651) and asserts `uv run ruff check .` —
  following it would have grown the lint-job class. Re-anchored again 2026-09-24: hardening moved
  the class ~40 lines.)* Keep `test_no_repository_secrets_referenced` (~:482) and the permissions
  tests green (nothing new needs write).
- `.github/pull_request_template.md`: as a new bullet under **`## Standing invariants`** — the
  template's four sections are `What and why`, `Contract`, `Standing invariants` and `Gates`; there is
  no "sanitization checklist" (corrected 2026-09-19, validation round 1) — "- [ ] If this PR changes
  what reaches stage 3 or how stage 2 / 3 decide: `uv run python -m scripts.corpus.report
  --write-baseline`, reviewed the baseline diff; re-recorded cassettes if CI reported a miss";
  `tests/test_governance_docs.py` asserts the line (its PR-template checks are the pattern). **This
  story discharges ruling 6's PR-template checkbox**, which the wrapper's ruling 6 text assigns to
  "spec 5 US-005" — it lives here, beside the CI step, so a close-out verifier should look for it
  in US-003.
- `contract/GOVERNANCE.md` unchanged (no wire change); `SECURITY.md` (root) unchanged.

**Acceptance Criteria:**
- [ ] The step exists, ordered after the step named `pytest`, conditioned as specified, pinned by tests; the
      workflow stays free of new actions / secrets / permissions (existing tests green).
- [ ] PR template line present and pinned.
- [ ] A PR run shows the table in the job summary (screenshot not required; the run URL recorded in
      Implementation Notes).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-004: The decision table — offline pooling sweep for contiguity and the model default

**Priority:** P2

**Description:** As the owner, I want the numbers hardening deferred here: for each recorded model,
how max-score, contiguity (over a small grid), k-windows-anywhere, mean-aggregate and a smoothed
window pool over the recorded per-window scores — catch on the window-shaped attack families and
FPR on `long_form` / `multilingual` / external benign — so the contiguity and 86M rulings are made on
a table, not a hunch. **No default flips here** (owner decision 4). The 86M rows exist only if
spec 0 enabled the 86M and spec 4 US-003 recorded it (owner decision 17); otherwise this story
delivers the contiguity half and names the model half pending (ruling 6a).

**Independent Test:** `uv run python -m scripts.corpus.report --sweep --json` renders an `offline`
section, deterministic, with rows for `max@{0.5,…,0.95}`, `live_contiguity` (the shipped composite,
US-004 hints), `contiguity(k∈{2,3}, t∈{0.4,0.5,0.6,0.7})`,
`k_anywhere(k∈{2,3}, t as above)`, `mean@{0.3,…,0.7}`, `smoothed(2)@{…}` per model; hermetic tests
on synthetic cassettes pin each pooler's arithmetic (e.g. `[0.6, 0.2, 0.6]` fires `k_anywhere(2,
0.5)` and not `contiguity(2, 0.5)`); the `offline` section is part of the baseline (exact match).

**Implementation Hints:**
- `scripts/corpus/poolers.py` — **extends** the module US-001 created with `max_score` and
  `contiguity` (those two stay as US-001 wrote them; this story adds the other three and the
  `live_contiguity` composite): pure functions over `Sequence[float]`; `max_score(t)` (strict
  `> t`, as live stage 3's `max_fired = score > threshold`), `contiguity(k, t)` (mirrors hardening
  spec 7 US-007's rule: any run of ≥ k consecutive scores `>= t` — the run loop in
  `run_promptguard`, `pipeline/stage3_promptguard.py` ~:237-244 at 403e9c5), `k_anywhere(k, t)`
  (≥ k windows ≥ t, anywhere — hardening spec 7's Decisions "a k-windows-anywhere rule — a
  corpus-epic candidate"), `mean_aggregate(t)` (mean of window scores ≥ t — the aggregate Prompt
  Overflow's authors say per-window rules cannot supply; landscape finding 9), `smoothed(w, t)`
  (moving average over `w` windows ≥ t — the "smoothed sliding-window score" hardening overruled
  into this epic's tuning).
- **Comparison operators are pinned** (added 2026-09-24, validation round 4): `max_score` strict
  `>`, the other four inclusive `>=`, each with an exact-threshold unit case; plus one consistency
  test — offline `max@0.85` equals the live `default` stage-3 catch (the live side counting
  `/search` stage-3 catches whose `rule` is not `sub_threshold`, so 0.5–0.85 flags are excluded),
  and offline
  `live_contiguity` = `max_score(0.85) OR contiguity(2, 0.5)` equals the live `contiguity` stage-3
  catch, over the stage-3 call log on the same texts, so a pooler cannot drift from the rule it
  claims to mirror. The composite is needed because the live rule is not contiguity alone:
  `run_promptguard` fires on `max_fired or contiguous` (`pipeline/stage3_promptguard.py`
  ~:233-256 at 403e9c5) and spec 1's `contiguity` boot keeps the 0.85 max threshold — a lone
  window above 0.85 with no qualifying run is a live catch the bare pooler misses. `live_contiguity`
  is also a named row in the sweep, so the decision table carries the exact rule a default flip
  would ship. *(Corrected round 5.)*
- Inputs: the cassette entries for every text that reached stage 3 in the `default` config (use the
  replay call log so texts blocked by stage 2 are excluded — the sweep is about stage 3).
- Output per model: for each pooler setting — attack catch overall and for `boundary_straddle`,
  `density_thinned` (by density level), `repetition_camouflage` (by repeat level), `sustained_midband`,
  `natural_language`, `authority_seo`; benign FP overall external, `long_form`, `multilingual`,
  `over_defence_probe` (separately). The table is written into `docs/corpus.md` "Decision inputs"
  (US-005) with a reading guide that states the two questions (does contiguity at `2 @ 0.5` add
  catch on the window families without raising `long_form` / `multilingual` FP; does 86M change the
  multilingual and natural-language rows) and explicitly does **not** answer them. If no 86M
  cassette exists, the guide prints the second question as **pending** with its prerequisites
  (spec 0's enablement gates, then spec 4 US-003's 86M recording) rather than beside an
  all-`unmeasured` column (ruling 6a).
- `--sweep` is part of `--write-baseline` (the `offline` section drifts like everything else).

**Acceptance Criteria:**
- [ ] `poolers.py` completed to the five poolers (US-001's two plus three added here), unit-pinned; `--sweep` renders the `offline` section
      deterministically; part of the baseline.
- [ ] Per-model rows for every pooler setting and every named family / genre; `unmeasured` when a
      model has no cassette.
- [ ] Implementation Notes carry the 22M (and 86M, if spec 0 enabled it and spec 4 recorded it)
      headline rows for `default` vs `live_contiguity` (and bare `contiguity(2, 0.5)`) vs
      `mean_aggregate` (numbers only); if
      not, they record "not recorded — 86M not enabled" and the model half as pending (ruling 6a).
- [ ] Pooler comparison operators unit-pinned at the threshold; offline/live consistency test green.
- [ ] No change to `config.yaml` defaults, stage 3, or any hashed file (rulings 6, 6a). "Hashed"
      means the current `pipeline/sanitizer_revision.py` set, read from the code, not from ruling
      6's prose: the eight `_REVISION_SOURCES` (`contract.py`, `stage1_extraction.py`,
      `stage1_pdf.py`, `stage1_upload.py`, `stage2_structural.py`, `stage3_promptguard.py`,
      `stage4_structuring.py`, `orchestrator.py`) **plus `url_validator.py`** via
      `_ROOT_REVISION_SOURCES` (added by `hardening-search-sanitization` US-003).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

### US-005: Documentation, findings, and epic close-out

**Priority:** P1

**Description:** As the owner, I want the epic to end with the documentation that lets anyone check
the claim "injection defence is measured" — the corpus guide, README, SECURITY.md, vision and roadmap
— every leaked attack filed as a finding, the Poppy handoff stated, the **disclosure posture stated
in the open**, and the zero-runtime-change promise asserted one last time.

**Disclosure (owner decision, 2026-09-19 — wrapper ruling 14b):** this story publishes a permanent,
per-record catalog of exactly which injection categories and carriers reach the consumer under the
live default config, alongside a tuned evasion corpus. That is deliberate and it is published in
full. `docs/corpus.md`'s "Reading the results" section must carry the reasoning — public injection
corpora are standard defensive practice and the ingested ones are themselves public; Forage is
public, unauthenticated by design, and documented as **not a trust boundary**, so the catalog
discloses no guarantee Forage ever made; and an unpublished gate is one nobody can check. A test
asserts the section exists, the same way the other `docs/corpus.md` sections are asserted.

**Independent Test:** `docs/corpus.md` exists with the sections listed below; `README.md` has a
"Measured injection defence" paragraph linking it; in `kit_tools/arch/SECURITY.md` the
"Observations" sentence "No fuzz harness or adversarial corpus exists …" is **replaced, not
deleted**: the new text states that the adversarial corpus exists (linking `docs/corpus.md`) and
still states that there is no fuzz harness and that the PDF parser is outside the corpus (ruling 8)
— a test asserts both the corpus claim and the two retained gaps; the "Security Testing" section
describes the corpus gate and its "Coverage by control" "Injection signalling and quarantine" row
lists the `tests/test_corpus_*.py` files; `kit_tools/PRODUCT_VISION.md` marks T2.3 shipped; MILESTONES / BACKLOG updated;
`kit_tools/AUDIT_FINDINGS.md` has one entry per (category, route) with any `leaked` record under
`default` / 22M listing the ids; the ruling-6 assertions hold on the epic branch.

**Implementation Hints:**
- `docs/corpus.md` sections: What is measured and why (outcome vocabulary, ruling 9); The record
  format (lift from `tests/corpus/README.md`, keep the README as the short form); Adding a record
  (checklist, content rules, lint); Recording and re-recording (lift spec 4 US-004's procedure);
  The gate (what each red means, the two commands); Sources and licences (the accepted table with
  NOTICE pointers; the rejected table — BIPIA, WASP, HackAPrompt, PIGuard, Wikipedia, Stack
  Exchange, MDN, OWASP, Reddit, HN — with the reason); Decision inputs (US-004's table + reading
  guide); **Reading the results** (what the headline numbers do and do not claim: the disclosure
  reasoning from ruling 14b; the candidate rejection rate beside the FPR; the route-asymmetry note
  from spec 1 — `flagged` is not comparable across routes, cross-route comparison uses recorded
  scores; `blocked_but_leaked` as a half-worked defence rather than a success; stage 3 is skipped
  after a structural block so the classifier-only view covers only texts that reached it);
  Consumers (Poppy's `epic-web-injection-regression-suite` may vendor records one way; the
  format is versioned by `"format"` in cassettes and by the README's field list; Forage never reads
  Poppy). Numbers appear only in the Decision-inputs table and are dated with the baseline's commit.
- `README.md`: one paragraph under the pipeline table: what the corpus is, where the numbers live
  (`tests/corpus/baseline.json`, the CI job summary), link to `docs/corpus.md`; no numbers copied
  into README (they rot).
- `kit_tools/arch/SECURITY.md`: locate by content (`grep -n "No fuzz harness or adversarial
  corpus exists"`; ~:567 at 403e9c5, under `## Security Testing` ~:541 → `### Observations` ~:565 —
  the old `:329` now lands in PDF/upload process-isolation prose). That sentence covers three
  surfaces (stage-2 regexes, HTML parser, PDF parser) and denies two things (a fuzz harness, an
  adversarial corpus); the corpus answers only part of it, so **replace it without overclaiming**:
  an adversarial corpus now exists for stage 2 / stage 3 over HTML and plain-text carriers
  (`docs/corpus.md`); there is still no fuzz harness, and PDF-borne text is outside the corpus
  (ruling 8). "Security Testing" gains the corpus gate, the cassette design and the
  no-weights-in-CI reasoning; the "Coverage by control" table's "Injection signalling and
  quarantine" row (~:552) gains the `tests/test_corpus_*.py` files. "Observed absences" (~:629)
  stays unchanged (not this epic's) — and stays true, because its "**Fuzzing.** None found (see
  "Security Testing")" line (~:637) still points at a section that still states the fuzz gap.
- `kit_tools/AUDIT_FINDINGS.md`: one entry per (category, route) with `leaked` under `default` /
  22M — ids, counts, the stage that should have caught it, `info` / `warning` by whether the category
  is structural; **not fixed** (ruling 6). Also file any `hidden_markup` carrier that leaked (spec 2
  US-002's list). **And the other direction**: one entry per (genre, route) whose measured FPR is
  non-zero under `default` / 22M — the over-defence half of the epic's completion criterion ("any
  bypass **or over-defence** the corpus surfaces is filed"), which the leaked-only rule left with no
  filing path at all. Same shape, same not-fixed rule; `info` unless the genre is a core one, where a
  real false positive on ordinary web text is `warning`. **And the third**: every record whose
  `signals["marker_on_wire"]` is true while its outcome is `blocked` (spec 1's blocked-but-leaked
  column) — a half-worked defence is a finding, not a success. *(Added 2026-09-19, validation
  round 1.)*
- `kit_tools/PRODUCT_VISION.md` T2.3 shipped (and its stale "five specs, 21 stories" parenthetical
  corrected to the epic's real shape or dropped, and `feature-corpus-86m-enablement` added to its
  Feature Spec(s) list); `kit_tools/roadmap/MILESTONES.md` / `BACKLOG.md` — **the same stale count
  sits in both** (MILESTONES' T2.3 entry "five specs / 21 stories" ~:70 and its sequence summary
  "five specs, 21 stories" ~:97-98; BACKLOG's corpus entry "five specs, 21 stories" ~:54, which also
  lists only the five `feature-corpus-{harness,attacks,benign,recording,gates}.md` files).
  Correct all three files in the same pass to the epic's real shape: **six specs, 25 stories**
  (spec 0 `86m-enablement` 4, `harness` 3, `attacks` 5, `benign` 4, `recording` 4, `gates` 5 —
  re-count the `### US-` headings at close-out). Also correct MILESTONES' "ships no runtime change,
  so it cuts no release of its own" (~:98) to name spec 0's `v1.2.2` (decision 18). Then the
  corpus item closed and **two** follow-up items opened: "contiguity default ruling (inputs:
  `docs/corpus.md` Decision inputs)" and "86M default ruling" — the latter's inputs are the same
  table if spec 0 enabled the 86M and spec 4 recorded it, otherwise it is marked **blocked on** spec
  0's enablement gates and an 86M cassette (ruling 6a); `kit_tools/SYNOPSIS.md` and `kit_tools/AGENT_README.md`
  one line each.
- Final ruling-6 assertion (rulings 6, 6a): `git diff --stat
  "$(git merge-base main HEAD)" -- pipeline/ promptguard/ models.py
  retrieval_app.py cache.py url_validator.py model_fetcher.py contract/ config.yaml
  weights_manifest.json Dockerfile` empty on the epic branch — against **spec 0's completion tag,
  not `main`**, because spec 0 is the epic's one sanctioned runtime change (`model_fetcher.py`,
  `promptguard/classifier.py`, `weights_manifest.json`); `derive_sanitizer_revision({})` equals the
  value recorded in spec 1 Implementation Notes (the hashed set is the code's —
  `_REVISION_SOURCES` + `_ROOT_REVISION_SOURCES`, including `url_validator.py` — not ruling 6's
  prose list); `uv run python -m scripts.export_contract --check` green; the only workflow change is
  US-003's step. Also record `git diff --stat <tag> -- uv.lock pyproject.toml`: the revision rotates
  on an `idna` version bump too, so a dev-dependency re-lock would fail the equality at close-out
  rather than at the story that caused it — the recorded diff makes such a failure explain itself.

**Acceptance Criteria:**
- [ ] `docs/corpus.md` with all **nine** sections (the ninth, "Reading the results", is what ruling
      14b's disclosure decision and spec 1's route-asymmetry rule require a reader to have — round 1
      added the obligation while the list and this criterion still said eight); README paragraph; SECURITY.md updated as specified (sentence replaced with the fuzz and PDF gaps retained, coverage row extended, pinned by a test);
      vision / roadmap / synopsis / agent-readme updated — including the stale "five specs, 21
      stories" count in PRODUCT_VISION.md, MILESTONES.md and BACKLOG.md, all three corrected.
- [ ] `AUDIT_FINDINGS.md` entries for every leaked (category, route), every leaked carrier, **every
      non-zero-FPR (genre, route)** and **every blocked-but-leaked record** — ids and numbers only,
      none fixed.
- [ ] Ruling-6 assertions recorded in Implementation Notes with the commands and their output.
- [ ] `tests/test_governance_docs.py`-style link check: every relative link in `docs/corpus.md`
      resolves.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pyright` passes

## Edge Cases

- A model with no cassette: `unmeasured` column, no floors apply, completeness test skipped for it
  with the reason (US-001, US-002).
- A category with zero records on a route (e.g. `hidden_markup` on `/search`): the cell is `n/a`,
  not 0 %; floors do not exist for empty cells (US-001, US-002).
- A baseline improvement: the drift test is red; regenerating shows the direction in the diff —
  intended (US-002).
- Two PRs regenerate the baseline concurrently: a merge conflict in JSON — resolved by
  regenerating on the merged tree (US-002; README says so).
- The CI summary exceeds GitHub's step-summary size limit (1 MiB): the Markdown renderer caps the
  per-record section to the `leaked` ids and the tables (US-001).
- `--sweep` on a cassette with single-window texts only: contiguity rows are `n/a` (US-004).
- The floors file names a category / route / model that no longer exists: the floors test fails
  naming it (US-002).
- A caught record whose signals match no attribution bucket, or a `skipped_trusted` state:
  `build_report` raises naming the record id and route — never a silent bucket (US-001; invariant
  5). An `/extract` 503, a busy refusal or any other non-`BLOCKING_ERRORS` response never gets this
  far: spec 1's drive raises first.
- The retrieve classification budget default moves from 0 to 256 in a later MINOR: at 256 the
  character ceiling (458 752) sits above spec 1's `page` size cap, so only a page that tokenises
  into more than 256 windows is refused; any that is moves into `catch_refused`
  (`promptguard_budget`, a `BLOCKING_ERRORS` row) and the drift test names it (US-001).

## Out of Scope

- Flipping the contiguity or model default; changing thresholds, regexes or `config.yaml`
  (owner decision 4, ruling 6).
- Measuring `/retrieve`'s caller-set trust tiers (`trusted_domains` → `skipped_trusted`; also
  `verified_domains`, which changes only the model-unavailable posture): a caller who names a trusted domain has opted that host out of stage 3 by
  design, so a "bypass" there is the documented behaviour, not a finding (US-001 raises if one
  appears).
- Fixing any finding filed in US-005.
- A separate CI job; weights in CI.
- Tolerance-band gating (owner decision 4).

## Assumptions

- Exact-match baselines are sustainable because replay is deterministic; a torch minor bump that
  moves borderline scores shows as a re-record event, not flakiness.
- GitHub's job summary accepts the Markdown tables at the sizes produced.
- The `test` job's timeout (20 min) absorbs the replay comfortably (target ≤ 60 s).

## Technical Considerations

- The gate module caches one `Report` for its tests; pytest-xdist is not used in this repo, so
  module-scope caching is safe.
- The JSON baseline is a few hundred KB (per-record map); it is a test fixture, not shipped.
- `docs/corpus.md` numbers are dated to the baseline commit so a reader can tell staleness.

### Validation residue — closed at `needs-work` (2026-09-19, `/kit-tools:validate-epic`, 3 rounds)

Thirty reviewers over three rounds took this epic from 19 criticals to 0 open; the items below are
the warnings that remained when validation was deliberately closed rather than chased to zero — the
same call, for the same reason, that `epic-forage-hardening` recorded on the same day: the precision
reviewers surface a new layer every round, and **every code anchor in this spec predates eight
unexecuted hardening specs** (ruling 5), so precision spent now is precision spent twice. Re-verify
against the post-hardening tree at execution time; treat each item as a decision the implementer
makes deliberately, not a defect to discover.

- **`rule` has the same producer/consumer gap that `marker_on_wire` had.** *(Closed round 6: the
  column is now defined in US-001, sourced from the replay classifier's call log, with an
  Independent Test assertion.)* Spec 1 puts `rule` in
  `RouteResult.signals` while this spec's report wants a `rule` column sourced from the replay
  classifier's call log. One of the two must be chosen explicitly, or the column will be empty the
  same way the blocked-but-leaked column nearly was. *(Round 4: the shipped tree offers a third
  source — the `promptguard_contiguity_detections` counter on `/metrics` (hardening US-007); a
  per-record delta under `drive_all`'s sequential drive would attribute contiguity blocks from
  observed behaviour. Still the implementer's explicit choice; the call-log recomputation is the
  simpler, hermetic one, and US-004's new consistency test covers the live/offline divergence the
  metrics option would have caught.)*
- **The contiguity pooler is needed by US-001 and built in US-004.** US-001's classifier-only view
  needs "whose windows would fire the contiguity recipe" — the run-of-k-above-threshold predicate
  that `poolers.py` implements in a later story. Build the predicate first or scope US-001's view.
  *Closed round 7:* US-001 creates `poolers.py` with `max_score` and `contiguity`; US-004 extends
  it with the other three and the composite.
- **The floor-breach failure message drops `config`** although floors are keyed by it, so a breach
  under `contiguity` and one under `default` read identically.
- **`--check` is declared in the CLI but never exercised** by an Independent Test or criterion, and
  **`--write-floors`'s two rounding rules are not unit-pinned** — the rounding is what sets every
  floor, so it deserves a test of its own rather than being verified by eye on the generated diff.
- **`candidate_rejection_rate` has no test on this spec's side** — spec 3 writes
  `tests/corpus/benign/sampler_stats.json`, and nothing here asserts the report reads it or that the
  `—` path works when the file is absent.

### Validation round 4 — 2026-09-24 (post-hardening re-anchor)

Six reviewers against `main` = `403e9c5` (v1.2.1, contract 1.3.0), after owner decision 17 added
spec 0. **Fixed:**

- **Anchors** — every `file:line` re-verified at 403e9c5 and re-cited by symbol with a `~:` hint:
  `drift_report` test idiom (was `:130-136`, now `TestTheCommittedArtifacts…` ~:245-249 and the
  `REGEN_COMMAND in report` pairs); `ci.yml` `test` job (~:283-324); searxng "Record the advisory
  outcome" (~:1500-1521); `TestTestJob` (~:993-1121); SECURITY.md sentence (~:567, located by
  content); `export_contract.py` symbols confirmed unchanged.
- **Behaviour** — stage attribution made total: `refused` bucket for `/extract` 422
  `content_too_large_to_classify`, 413 `content_too_large`, 429 `busy`, `/retrieve` 422 `busy`,
  `content_too_large`/`promptguard_budget`, `extraction_failed`; `/retrieve` URL refusals are 422
  codes, not omits; `skipped_trusted` raises (Out of Scope added); sum-to-catch invariant tested.
  CI condition is `if: always()` (the `!cancelled()` "precedent" did not exist); step placed after
  the step *named* `pytest` via `runs.index(_FULL_SUITE_RUN)` (hardening added a second pytest
  step). Pooler comparison operators pinned (`>` for max, `>=` otherwise) with a live/offline
  consistency test.
- **Rulings 6 / 6a / decision 17** — the hashed set is named from the code (eight
  `_REVISION_SOURCES` + `url_validator.py`), not ruling 6's prose; the final assertion diffs against
  the epic branch's merge base with `main` (ruling 6a), and records the `uv.lock` /
  `pyproject.toml` diff (idna rotation). The decision table's model half, US-004's 86M rows, the
  reading guide and the BACKLOG follow-up (now split in two) are conditional on spec 0 enabling the
  86M. Header now "Spec 5 of 6".
- **SECURITY.md** — the sentence is replaced, not deleted, keeping the fuzz and PDF gaps (the
  corpus is neither); coverage-table row extended; "Observed absences" stays true.
- **Ownership** — US-003 notes it discharges ruling 6's PR-template checkbox (the wrapper says
  US-005).

**Rejected / corrected in the fix:** the round-4 brief described the `/extract` budget refusal as a
413 and admission refusals as 503s. At 403e9c5 the `/extract` budget refusal is **422
`content_too_large_to_classify`** (`orchestrator.py`, `PromptGuardBudgetExceededError` catches);
413 is the streaming size cap; admission is 429 (`/extract`) / 422 `busy` (`/retrieve`); the 503s
fire only when app state lacks the admission controller, which cannot happen on a booted app. The
spec says what the tree does. Reviewer 3's `ci.yml` job end (~:484) and reviewer 5's (~:412) were
wrong; the job ends at ~:324 (`build-amd64:` at :326).

**Carried as residue (not chosen this round):**

- A disclosure that the hermetic gate cannot detect a real-model load failure (reviewer 5; the
  v1.2.0 label-index incident is the precedent) — owner deferred; `/health.promptguard_loaded`
  remains the runtime truth.
- An `injection_spans` exposure counter — harness-side (spec 1), deferred with it.
- The five round-3 items above still stand except where round 4's `refused` / pooler edits touch
  them; the `rule` source choice is annotated, not closed. *(Closed round 6: US-001 now defines the
  `rule` column from the replay classifier's call log, matching spec 1's reference.)*

**Round 5 (same day):** (1) *Critical — `refused` contradicted spec 1's error map.* Round 4 wrote
the bucket's code list here while spec 1 wrote a closed map the same round; they disagreed (busy /
413 / budget counted as catches here, harness errors there; `unsupported_format` in neither
bucket). Now one rule, owned by spec 1's `BLOCKING_ERRORS` table and imported: content-determined
refusals are `blocked` with `refusal = True` and land in `refused`; load- or harness-caused ones
stop the drive and never reach the report. Measured at 403e9c5 — `/extract` empty text → 422
`unsupported_format`, over-ceiling → 422 `content_too_large_to_classify`, oversize upload → 400 (the
413 is unreachable; the info finding held). `/retrieve` URL refusals left the `url` bucket (the
driver patches both URL seams). Independent Test case, Edge Cases and the 0→256 budget entry
rewritten to match. (2) *Offline/live consistency*: the live `contiguity` config fires on
`max_fired or contiguous`, so the test compares it to a new `live_contiguity` composite row
(`max_score(0.85) OR contiguity(2, 0.5)`), which the sweep and Implementation Notes also carry.
(3) *Info*: US-005's PRODUCT_VISION T2.3 flip also corrects the stale spec/story count.

**Round 6 (same day):** (1) *Critical — a flagged `/search` result matched no attribution bucket.*
Verified at 403e9c5: `run_search_pipeline` sets `suspicious` from a stage-2 `SUSPICIOUS` verdict
(~:1842) and a stage-3 score `> 0.5` (~:1924). Every omit-keyed bucket missed the served, flagged
result, so `build_report` would have raised on spec 1's own 0.6 `/search` case. US-001 now gives it
a precedence: `stage3` when `signals["score"] > 0.5`, else `stage2`, counted once. Two flagged
`/search` cases join the Independent Test (the mini corpus is now fourteen records). (2) *The
`unsupported_format` fixture could not pass spec 1's marker lint* (an attack with no visible text
has no ≥ 12-character marker). It is now a marker-bearing `text` record with a raw NUL, which
reaches the same row (`pipeline/stage1_upload.py` ~:47). (3) *`rule` column.* Spec 1's signals list
cited this spec as the home of a `rule` column that did not exist. US-001 now defines it from the
replay classifier's call log, with `sub_threshold` for flagged-`/search` stage-3 records and an
Independent Test assertion. The round-3 and round-4 `rule` residue items are marked closed. (4)
*Info:* US-005 now names the stale "five specs, 21 stories" in MILESTONES.md and BACKLOG.md beside
PRODUCT_VISION.md, with the real shape: six specs, 25 stories. The Related Documentation and
Scope Adjustments release lines were then corrected to name spec 0's `v1.2.2`. *Round 7:* the
flagged-`/search` invariant moved from a report raise to a test (scripts may not call the tests-side
helper), and the offline/live `max@0.85` consistency check excludes `sub_threshold` flags.

## Related Documentation

- `contract/GOVERNANCE.md` (unchanged — no wire change); `kit_tools/arch/SECURITY.md`;
  `docs/releases.md` (spec 0's `v1.2.2` is the epic's only release; specs 1–5 cut none);
  `kit_tools/roadmap/MILESTONES.md`.

## Implementation Notes

<!-- Numbers only: first measured headline rows; wall times; the run URL for the CI summary; the
ruling-6 assertion output. -->

## Refinement Notes

### Research Findings

**Decision:** Exact-match generated baseline plus floors set from measurement.
**Rationale:** The repo's generated-file-plus-drift-test idiom (`contract/openapi.yaml`); PIDS-Bench
found no operating point meeting F1 ≥ 0.95 with FPR ≤ 0.10 for PG2-86M, so aspirational floors would
be red on day one — measured floors, rounded conservatively, are the honest gate.
**Source:** `drift_report` (`scripts/export_contract.py` ~:222-256 at 403e9c5); https://arxiv.org/html/2609.15017.

**Decision:** Stage-2 / stage-3 / url attribution per category; over-defence genres never pooled.
**Rationale:** Meta's PG2 card says the injection sub-label was dropped — classes it does not target
must show as stage-2-carried or leaked, not be hidden by a pooled catch rate; NotInject rows are
direct-framed probes.
**Source:** https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M; https://huggingface.co/datasets/leolee99/NotInject.

**Decision:** Offline poolers include mean-aggregate and k-anywhere alongside contiguity.
**Rationale:** Prompt Overflow shows density thinning defeats any per-window rule — contiguity
included — and cassettes make an aggregate comparison free.
**Source:** https://arxiv.org/abs/2605.23196; hardening spec 7 Decisions ("k-windows-anywhere",
"smoothed sliding-window").

### Scope Adjustments

- No `docs/releases.md` entry from this spec: outside spec 0 (which releases `v1.2.2`, decision 18)
  the epic ships no runtime change and cuts no release.

### Decisions Made

- Floors rounding rule: catch down to 0.05, FPR up to 0.05.
- The `offline` sweep is part of the baseline.

## Clarifications

### Session 2026-09-19
- Q: Gate semantics? → A: Generated baseline + reviewed floors (owner decision 4).
- Q: Defaults? → A: Decision table only; flips are a later ruling (owner decision 4).

## Open Questions

- [ ] Whether the full replay fits ≤ 60 s on a GitHub runner — non-blocking; measured at US-002,
      with a split proposal if not.

## Known risks (planning)

- A large per-record baseline makes diffs long; the 200-line cap on the printed diff and the
  per-record map's stable ordering keep them readable.
- The CI summary step runs `scripts.corpus.report` a second time after pytest (a few seconds); if it
  becomes a cost, the gate could write the Markdown as a pytest artefact instead — noted, not done.
- **Anchor drift.** Every anchor was re-verified at `403e9c5` on 2026-09-24 (validation round 4)
  and is cited by symbol with a `~:` line hint. The ones most likely to move again before
  execution: `ci.yml`'s `test` job and `TestTestJob` (both moved twice), `kit_tools/arch/SECURITY.md`
  (grew ~240 lines in hardening — locate by content), and `pipeline/contract.py`'s refusal
  vocabulary (actively churning across MINORs). Re-grep by symbol, not by line.
