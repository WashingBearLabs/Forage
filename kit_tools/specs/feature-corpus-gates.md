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
updated: 2026-09-19
---

# Feature Spec: Gates and Reporting — Report, Generated Baseline + Floors, CI Summary, Decision Table, Docs and Close-Out

> Spec 5 of 5 (final) in `epic-forage-injection-corpus` (owner decision 4; rulings 11, 12, 16).
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
  offline poolers hardening handed over — and no default flips (owner decision 4).
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
twelve-record mini corpus with two synthetic cassettes: `render_json` output is byte-identical
across two runs and sorts every key; the attack table attributes a `structural_blocked` omit to
`stage2`, an `injection_detected` omit with `promptguard_state == scanned` to `stage3`, an
`invalid_url` / `blocked_url` omit to `url`; the benign table splits `external` from `synthetic` and
never pools `over_defence_probe` into the headline FPR; `render_markdown` contains every record id
of a `leaked` attack and no marker substring; `uv run python -m scripts.corpus.report --help` exits
0 offline.

**Implementation Hints:**
- `scripts/corpus/report.py`: `build_report(records, cassettes: Sequence[ReplayClassifier], configs:
  Sequence[RuleConfig]) -> Report` (frozen dataclasses); `render_json(report) -> str`
  (`sort_keys=True`, `indent=2`, floats rounded to 4 dp, trailing newline); `render_markdown(report)
  -> str`; CLI `--json` / `--markdown` / `--write-baseline` / `--check` / `--sweep` (US-004), the
  `scripts/export_contract.py` `build_parser` / `main` / `REGEN_COMMAND` idiom (`:105`, `:293-310`).
- **Dimensions**: `models` (from cassettes; `unmeasured` column when a model has no cassette) ×
  `configs` (`default`, `contiguity`) × `routes` × `categories`: counts `blocked`, `flagged`,
  `neutralised`, `leaked`, rates `catch` (= blocked + flagged / n) and `block`; **stage attribution**
  per caught record: `stage2` (search `omit_reason == structural_blocked`; retrieve / extract
  `promptguard_state == structural_blocked` or `structural_flags != []` with no `injection_detected`),
  `stage3` (`omit_reason == injection_detected`; `injection_detected` with `promptguard_state ==
  scanned`), `url` (`invalid_url`, `blocked_url`), `unavailable` (fail-closed) — reported as
  `catch_stage2`, `catch_stage3`, `catch_url` rates per category (finding 6: a class carried only by
  stage 3, or only by stage 2, is visible).
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
      stage-attribution, provenance-split and classifier-only tests as in the Independent Test.
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
  (`scripts/export_contract.py:222-260`; `tests/test_contract_export.py:130-136`).
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
step that runs `uv run python -m scripts.corpus.report --markdown` into `${GITHUB_STEP_SUMMARY}` conditioned on GitHub's `!cancelled()` expression; no new action, secret or permission; `.github/pull_request_template.md`
carries the corpus checklist line.

**Implementation Hints:**
- `.github/workflows/ci.yml` `test` job (`:272-313`): one step, `{ echo "### Injection corpus";
  uv run python -m scripts.corpus.report --markdown; } >> "${GITHUB_STEP_SUMMARY}"` — the
  `searxng-smoke` step summary is the precedent (`:1495-1507`); an `if:` on GitHub's `!cancelled()` expression so a drift
  failure still publishes the table; keep the job's `timeout-minutes`; `actionlint` runs in `lint`.
- `tests/test_ci_workflow.py`: extend the `test`-job assertions — the class is **`TestTestJob`
  (`:953-1040`)** and the idiom is `_run_text(jobs, "test")`. *(Corrected 2026-09-19, validation
  round 1: the hint cited `:670-678`, which is inside `TestLintJob` (`:651`) and asserts
  `uv run ruff check .` — following it would have grown the lint-job class.)* keep `test_no_repository_secrets_referenced` and the permissions tests green (nothing
  new needs write).
- `.github/pull_request_template.md`: as a new bullet under **`## Standing invariants`** — the
  template's four sections are `What and why`, `Contract`, `Standing invariants` and `Gates`; there is
  no "sanitization checklist" (corrected 2026-09-19, validation round 1) — "- [ ] If this PR changes
  what reaches stage 3 or how stage 2 / 3 decide: `uv run python -m scripts.corpus.report
  --write-baseline`, reviewed the baseline diff; re-recorded cassettes if CI reported a miss";
  `tests/test_governance_docs.py` asserts the line (its PR-template checks are the pattern).
- `contract/GOVERNANCE.md` unchanged (no wire change); `SECURITY.md` (root) unchanged.

**Acceptance Criteria:**
- [ ] The step exists, ordered after `pytest`, conditioned as specified, pinned by tests; the
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
a table, not a hunch. **No default flips here** (owner decision 4).

**Independent Test:** `uv run python -m scripts.corpus.report --sweep --json` renders an `offline`
section, deterministic, with rows for `max@{0.5,…,0.95}`, `contiguity(k∈{2,3}, t∈{0.4,0.5,0.6,0.7})`,
`k_anywhere(k∈{2,3}, t as above)`, `mean@{0.3,…,0.7}`, `smoothed(2)@{…}` per model; hermetic tests
on synthetic cassettes pin each pooler's arithmetic (e.g. `[0.6, 0.2, 0.6]` fires `k_anywhere(2,
0.5)` and not `contiguity(2, 0.5)`); the `offline` section is part of the baseline (exact match).

**Implementation Hints:**
- `scripts/corpus/poolers.py`: pure functions over `Sequence[float]`; `max_score(t)`,
  `contiguity(k, t)` (mirrors hardening spec 7 US-007's rule: any run of ≥ k consecutive scores
  ≥ t), `k_anywhere(k, t)` (≥ k windows ≥ t, anywhere — hardening spec 7's Decisions "a
  k-windows-anywhere rule — a corpus-epic candidate"), `mean_aggregate(t)` (mean of window scores
  ≥ t — the aggregate Prompt Overflow's authors say per-window rules cannot supply; landscape finding
  9), `smoothed(w, t)` (moving average over `w` windows ≥ t — the "smoothed sliding-window score"
  hardening overruled into this epic's tuning).
- Inputs: the cassette entries for every text that reached stage 3 in the `default` config (use the
  replay call log so texts blocked by stage 2 are excluded — the sweep is about stage 3).
- Output per model: for each pooler setting — attack catch overall and for `boundary_straddle`,
  `density_thinned` (by density level), `repetition_camouflage` (by repeat level), `sustained_midband`,
  `natural_language`, `authority_seo`; benign FP overall external, `long_form`, `multilingual`,
  `over_defence_probe` (separately). The table is written into `docs/corpus.md` "Decision inputs"
  (US-005) with a reading guide that states the two questions (does contiguity at `2 @ 0.5` add
  catch on the window families without raising `long_form` / `multilingual` FP; does 86M change the
  multilingual and natural-language rows) and explicitly does **not** answer them.
- `--sweep` is part of `--write-baseline` (the `offline` section drifts like everything else).

**Acceptance Criteria:**
- [ ] `poolers.py` with the five poolers, unit-pinned; `--sweep` renders the `offline` section
      deterministically; part of the baseline.
- [ ] Per-model rows for every pooler setting and every named family / genre; `unmeasured` when a
      model has no cassette.
- [ ] Implementation Notes carry the 22M (and 86M, if recorded) headline rows for `default` vs
      `contiguity(2, 0.5)` vs `mean_aggregate` (numbers only).
- [ ] No change to `config.yaml` defaults, stage 3, or any hashed file (ruling 6).
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
"Measured injection defence" paragraph linking it; `kit_tools/arch/SECURITY.md` no longer says
"No fuzz harness or adversarial corpus exists" and its "Security Testing" section describes the
corpus gate; `kit_tools/PRODUCT_VISION.md` marks T2.3 shipped; MILESTONES / BACKLOG updated;
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
- `kit_tools/arch/SECURITY.md:329` observation replaced; "Security Testing" gains the corpus gate,
  the cassette design and the no-weights-in-CI reasoning; "Observed absences" unchanged (they are
  not this epic's).
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
- `kit_tools/PRODUCT_VISION.md` T2.3 shipped; `kit_tools/roadmap/MILESTONES.md` / `BACKLOG.md` the
  corpus item closed and a follow-up item "contiguity / 86M default rulings (inputs:
  `docs/corpus.md` Decision inputs)" opened; `kit_tools/SYNOPSIS.md` and `kit_tools/AGENT_README.md`
  one line each.
- Final ruling-6 assertion: `git diff --stat main -- pipeline/ promptguard/ models.py retrieval_app.py
  cache.py url_validator.py model_fetcher.py contract/ config.yaml weights_manifest.json Dockerfile`
  empty on the epic branch; `derive_sanitizer_revision({})` equals the value recorded in spec 1
  Implementation Notes; `uv run python -m scripts.export_contract --check` green; the only workflow
  change is US-003's step.

**Acceptance Criteria:**
- [ ] `docs/corpus.md` with all **nine** sections (the ninth, "Reading the results", is what ruling
      14b's disclosure decision and spec 1's route-asymmetry rule require a reader to have — round 1
      added the obligation while the list and this criterion still said eight); README paragraph; SECURITY.md updated as specified;
      vision / roadmap / synopsis / agent-readme updated.
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

## Out of Scope

- Flipping the contiguity or model default; changing thresholds, regexes or `config.yaml`
  (owner decision 4, ruling 6).
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

- **`rule` has the same producer/consumer gap that `marker_on_wire` had.** Spec 1 puts `rule` in
  `RouteResult.signals` while this spec's report wants a `rule` column sourced from the replay
  classifier's call log. One of the two must be chosen explicitly, or the column will be empty the
  same way the blocked-but-leaked column nearly was.
- **The contiguity pooler is needed by US-001 and built in US-004.** US-001's classifier-only view
  needs "whose windows would fire the contiguity recipe" — the run-of-k-above-threshold predicate
  that `poolers.py` implements in a later story. Build the predicate first or scope US-001's view.
- **The floor-breach failure message drops `config`** although floors are keyed by it, so a breach
  under `contiguity` and one under `default` read identically.
- **`--check` is declared in the CLI but never exercised** by an Independent Test or criterion, and
  **`--write-floors`'s two rounding rules are not unit-pinned** — the rounding is what sets every
  floor, so it deserves a test of its own rather than being verified by eye on the generated diff.
- **`candidate_rejection_rate` has no test on this spec's side** — spec 3 writes
  `tests/corpus/benign/sampler_stats.json`, and nothing here asserts the report reads it or that the
  `—` path works when the file is absent.

## Related Documentation

- `contract/GOVERNANCE.md` (unchanged — no wire change); `kit_tools/arch/SECURITY.md`;
  `docs/releases.md` (no release from this epic); `kit_tools/roadmap/MILESTONES.md`.

## Implementation Notes

<!-- Numbers only: first measured headline rows; wall times; the run URL for the CI summary; the
ruling-6 assertion output. -->

## Refinement Notes

### Research Findings

**Decision:** Exact-match generated baseline plus floors set from measurement.
**Rationale:** The repo's generated-file-plus-drift-test idiom (`contract/openapi.yaml`); PIDS-Bench
found no operating point meeting F1 ≥ 0.95 with FPR ≤ 0.10 for PG2-86M, so aspirational floors would
be red on day one — measured floors, rounded conservatively, are the honest gate.
**Source:** `scripts/export_contract.py:222-260`; https://arxiv.org/html/2609.15017.

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

- No `docs/releases.md` entry: the epic ships no runtime change and cuts no release.

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
