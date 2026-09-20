<!-- Template Version: 2.1.0 -->
---
epic: forage-injection-corpus
status: active
vision_ref: "T2.3 — Injection regression corpus (CI)"
created: 2026-09-14
updated: 2026-09-19
---

# Epic: Forage Injection Regression Corpus — Measure Injection Defence in CI

> **Web Access family Epic 6, Forage half.** Re-planned 2026-09-19 with `/kit-tools:plan-epic`,
> replacing the 2026-09-14 stub (scorecard #10, §9 Q4). The Poppy half — the end-to-end
> firewall/quarantine suite and the tier-1 canary — is Poppy's
> `epic-web-injection-regression-suite` and consumes nothing from this epic except the corpus
> format, which `docs/corpus.md` documents so Poppy can vendor records one way (Forage never
> depends on Poppy; CLAUDE.md invariant 1). Runs **in Forage**, **after** `epic-forage-hardening`
> (spec 1 `depends_on: [hardening-release]`): the corpus measures what that epic builds.

## Goal

Turn "injection defence works" from an assertion into a **measured, regression-gated property**. A
curated, licence-clean corpus of indirect prompt-injection attacks in web form — search snippets,
titles and URLs; page bodies; JSON-LD / Open Graph metadata; chunk-boundary interleavings — and a
benign counter-corpus are driven **hermetically through the real HTTP routes** (`POST /search`,
`POST /retrieve`, `POST /extract`) on every CI run. Because CI has no model weights (the gated Meta
repository; the org's mirror is private by decision, `docs/weights.md`; the workflow pins "no
repository secrets", `tests/test_ci_workflow.py::test_no_repository_secrets_referenced`), the real
classifier is measured **once per model revision on a host by an owner** and its per-window scores
are committed as **cassettes** that CI replays through the real stage-3 rules deterministically.
Every change then reports its attack catch rate and benign false-positive rate per attack class,
route, model and rule configuration against a generated baseline — including the two numbers
`epic-forage-hardening` deferred to this epic: whether contiguity gating (ruling R17, shipped off)
and the 86M model (owner decision 4, opt-in) earn their defaults.

The epic ships **no runtime behaviour**: no `_REVISION_SOURCES` file, response model, handler,
contract document or `config.yaml` default changes; `derive_sanitizer_revision()` and
`contract/openapi.yaml.sha256` are byte-identical at the end of the epic (ruling 6). A bypass the
corpus surfaces is recorded as an audit finding for a follow-up, never fixed here.

## Owner decisions (2026-09-19, binding on all five specs)

1. **Five specs, by concern** — harness, attack corpus, benign corpus, recording, gates — with the
   owner-gated recording in its own spec so the hermetic specs never block on a token.
2. **Recorded-score cassettes**, not weights in CI. An owner runs the real classifier once per model
   revision on the host; per-window scores are committed keyed by text hash + model identity; CI
   replays them. A pipeline change that alters what reaches stage 3 misses the cassette and fails
   loudly with the re-record command. Pulling the private mirror into CI (`GITHUB_TOKEN`,
   `packages: read`) was considered and rejected: it changes the recorded "weights never in CI"
   stance, costs ~270 MiB per run, and float drift across torch versions can flip borderline
   scores. "Structural only" was rejected because it leaves scorecard #10 half-open.
3. **Research first; ingest only permissive sets.** The landscape researcher ran before stories
   were written (findings in spec 2 / spec 3 Research Findings). Third-party samples enter only
   from sets whose licence permits redistribution in a public Apache-2.0 repository
   (MIT / Apache-2.0 / BSD / CC0 / CC-BY), sampled with a fixed seed, re-rendered into the corpus
   format, direct (user-turn) injections excluded, with a `NOTICE` entry. Owned synthetic vectors
   are written regardless.
4. **Generated baseline + reviewed floors**, the repo's golden/drift idiom (`contract/openapi.yaml`,
   `scripts.export_contract --check`): a generated `tests/corpus/baseline.json` must match exactly
   (drift is red until regenerated in the PR, diff printed), plus committed per-class floors and
   per-genre ceilings that a PR must visibly edit to lower. Tolerance bands (Poppy stub: −2 pts /
   +0.5 pt) were rejected as hiding small regressions once replay makes numbers deterministic.
   **Neither the contiguity default nor the model default flips in this epic**: the report produces
   the decision table; a flip is a separate owner ruling and a hardening-style follow-up, because
   both rotate `sanitizer_revision`.

## Planning rulings (2026-09-19)

5. **Sequenced after `epic-forage-hardening`.** Spec 1 `depends_on: [hardening-release]`. The
   harness targets the post-hardening seams by name: `PromptGuardClassifier.classify_windows`
   (hardening spec 7 US-002), the `promptguard_contiguity_windows` / `promptguard_contiguity_threshold`
   keys and `PromptGuardResult.rule` (spec 7 US-007), `FORAGE_MODEL_ID` (spec 7 US-006), the
   `blocked_url` omit reason and the raw-form URL scan (spec 1 US-002/US-004), newline-preserving
   search-text scanning (spec 1 US-001). Every `file:line` anchor in these specs was read at
   `main` = `20ddb2a` (pre-hardening); implementers re-verify anchors against the post-hardening
   tree before relying on them, and each spec's Known-risks section names the anchors most likely
   to move.
6. **Zero runtime change, asserted.** Each spec's final story and the epic's completion criteria
   carry: `git diff --stat main -- pipeline/ promptguard/ models.py retrieval_app.py cache.py
   url_validator.py model_fetcher.py contract/ config.yaml weights_manifest.json Dockerfile` is
   empty; `uv run python -c "from pipeline.sanitizer_revision import derive_sanitizer_revision as d;
   print(d({}))"` prints the post-hardening value recorded in spec 1 Implementation Notes; `uv run
   python -m scripts.export_contract --check` is green. The one non-test, non-script file the epic
   edits outside docs is `.github/workflows/ci.yml` (one named step, spec 5 US-003) and
   `.github/pull_request_template.md` (one checkbox, spec 5 US-005).
7. **Layout.** Code in `scripts/corpus/` (a package under `scripts/`, which is on the strict pyright
   root with no carve-out and is excluded from the image by `.dockerignore:26`); data — records,
   cassettes, baseline, floors — in `tests/corpus/` (never shipped: `.dockerignore` excludes
   `tests/`); tests in `tests/test_corpus_*.py`. The gate rides the existing `test` job
   (`uv run pytest -q`, no selection filters); no new CI job.
8. **Corpus content rules.** Payload text is *data*: it is never quoted in docs, README, commit
   messages, test names, assertion messages, log lines or the CI summary (record ids and numbers
   only); never executed; every URL in a record uses RFC 2606 reserved names (`example.com`,
   `example.net`, `example.org`, `*.test`, `*.invalid`, `*.example`); no secret-shaped literal (the
   corpus lint's regex set, spec 1 US-001) — CI's full-history `gitleaks` scan makes a committed
   false positive permanent (`.gitleaksignore` holds exactly one triaged entry and gains none from
   this epic); PDF-borne text is out of scope (recorded gap: `tests/fixtures/` has no text-bearing
   PDF generator).
9. **Outcome vocabulary.** For an attack record on a route: `blocked` (the result is omitted on
   `/search`; `injection_detected` or the fail-closed quarantine on `/retrieve` / `/extract`),
   `flagged` (delivered with `suspicious=true`, a non-empty `structural_flags`, or a
   `promptguard_state` other than `scanned`), `neutralised` (no flag and the record's
   `marker` is absent from every wire text field), `leaked` (no flag and the marker is present).
   For a benign record: `blocked`, `flagged`, `clean`. **Catch rate** = blocked + flagged; **block
   rate** = blocked; **FPR** = blocked + flagged on benign. Leak detection reads every string field
   of the wire response except `injection_spans` (a quarantine diagnostic that carries the flagged
   text by design).
10. **Cassette keying.** Key = `sha256` of exactly the text stage 3 passes to `classify_windows`;
    value = the full per-window score list (floats as `repr`) and the window count; **no chunk
    text**. Replay applies `max_chunks` exactly as `classify_windows` does (raise
    `PromptGuardBudgetExceededError` when the window count exceeds it) and returns placeholder chunk
    labels (`window-<i>`) in place of chunk text. A miss raises `UnrecordedTextError` naming the
    record id, route and rule config — the gate turns red with the re-record command. A cassette
    carries `model_id`, `revision` (must equal `weights_manifest.json`'s pin for that model),
    `sanitizer_revision` at recording (informational), and the `torch` / `transformers` versions.
11. **Rule configurations.** Measured *live* (through the pipeline): `default` (max-score at
    `promptguard_threshold` 0.85, contiguity off) and `contiguity` (hardening spec 7's documented
    enabling recipe, `promptguard_contiguity_windows: 2` at `promptguard_contiguity_threshold: 0.5`).
    Candidate poolers hardening handed over — k-windows-anywhere and a smoothed sliding window —
    are evaluated *offline* over cassette scores and labelled `offline` in the report; they are
    numbers for a future ruling, not code in stage 3.
12. **Gate.** `tests/test_corpus_gate.py` asserts (a) the generated `tests/corpus/baseline.json`
    matches the live report byte-for-byte (drift → unified diff + `uv run python -m
    scripts.corpus.report --write-baseline`, the `drift_report` idiom); (b) every floor / ceiling in
    the committed `tests/corpus/floors.json` holds (per category × route minimum catch rate; per
    genre × route maximum FPR); (c) every `pinned` record has its pinned outcome; (d) every cassette
    has zero unrecorded texts; (e) record-count floors per category and genre hold (the corpus
    cannot silently shrink). An *improvement* also drifts — regenerate; the diff shows the direction.
13. **Owner gates.** Spec 4 US-002 (record the 22M cassette) and US-003 (record the 86M cassette if
    hardening spec 7 US-005 vendored it; otherwise `not recorded — access pending`). Tokens reach
    the recorder only through the environment (`read -rs` into a variable, or `--env-file` by path),
    never argv or a log line; the recorder refuses to record an unloaded classifier (a fail-closed
    run measures nothing).
14. **Corpus size floors (asserted by lint).** Attacks ≥ 200 records over the 13 categories with
    ≥ 5 per category; benign ≥ 250 records over 8 genres with ≥ 15 per genre; ≥ 6 languages;
    ≥ 20 records whose recorded window count is ≥ 3. Counts are floors, not targets.
15. **Drivers.** Every record goes through the app (`httpx.ASGITransport`) with the lifespan booted
    and `model_fetcher.acquire_and_load` patched to install the replay classifier; `/search` via
    `app.state.search_providers = [FakeSearchProvider(...)]`; `/retrieve` via patched
    `pipeline.orchestrator.validate_url` and `fetch_url`; `/extract` via a multipart text upload
    (`stage1_upload.detect_upload_content_type` accepts `pdf` or `text` only — an HTML upload is
    read as text, so page-shaped records target `/retrieve` and text-shaped records target
    `/extract`); the cache is a fresh `FakeContentCache` per record.
16. **Runtime budget.** The full replay (every record × every applicable route × both live rule
    configs × every cassette) runs inside the `test` job; the target is ≤ 60 s wall on a GitHub
    runner, measured and recorded at spec 5 US-002 (not asserted — a wall-clock assertion is
    flaky).

## Decomposition

| Seq | Feature Spec | Stories | Status | Dependencies |
|-----|-------------|---------|--------|--------------|
| 1 | [`feature-corpus-harness.md`](feature-corpus-harness.md) — record schema, loader + lint, replay classifier, the three route drivers, the outcome model, seed records | 3 | Planned | `hardening-release` (the whole hardening epic) |
| 2 | [`feature-corpus-attacks.md`](feature-corpus-attacks.md) — the attack corpus by category and surface; third-party ingestion | 5 | Planned | `corpus-harness` |
| 3 | [`feature-corpus-benign.md`](feature-corpus-benign.md) — the benign counter-corpus by genre, over-defence prose, multilingual and long-form | 4 | Planned | `corpus-harness` |
| 4 | [`feature-corpus-recording.md`](feature-corpus-recording.md) — cassette format, file-backed replay, the host-side recorder, the 22M and 86M recordings (owner gates) | 4 | Planned | `corpus-attacks`, `corpus-benign` |
| 5 | [`feature-corpus-gates.md`](feature-corpus-gates.md) — report, baseline + floors gate, CI summary, decision table, docs and close-out | 5 | Planned | `corpus-recording` |

Execution runs the specs in sequence. Human gates: spec 4 US-002 and US-003.

## Inputs this epic measures (the handoffs)

| Source | Required corpus content |
|--------|-------------------------|
| Audit 2026-09-16-016 | Line-anchored role markers (`System:` / `assistant:`) after a paragraph break inside a search chunk — `line_anchored_role`, pinned |
| Audit 2026-09-16-032 | Envelope tags inside a URL path, query and IPv6 zone id — `url_borne_envelope`, pinned |
| Audit 2026-09-16-025 | Parity records drive `POST /search` end to end with the classifier stage running (replay), never a loop-level `run_promptguard` mock |
| Hardening spec 7 US-007 | Both stage-3 residual shapes: fragments separated by one benign window (`boundary_straddle`) and sustained mid-band text that can trip the contiguity rule (`sustained_midband`) |
| Hardening spec 7 Out of Scope | The 86M-vs-22M and contiguity-on/off decision tables (spec 5 US-004) |
| Hardening spec 7 Decisions | k-windows-anywhere and smoothed-window poolers evaluated offline |
| Hardening spec 1 Assumptions | The existing fixture corpus's non-line-anchored patterns verdict identically on collapsed and newline-preserving forms (structural families, spec 2 US-001) |
| Stub (2026-09-14) | JSON-LD / Open Graph poisoning surfaced as snippets, answer-engine poisoning, classic overrides in page bodies |

## Completion Criteria

- [ ] All five feature specs completed and archived; the two owner gates recorded in spec 4
      Implementation Notes with the cassette file names and the model revisions.
- [ ] `uv run pytest` green with the corpus gate in it; `uv run ruff check .`, `uv run ruff format
      --check .`, `uv run pyright` clean.
- [ ] The corpus lint floors (ruling 14) hold; every record's URL uses a reserved name; the
      secret-shape lint is clean; `.gitleaksignore` is unchanged.
- [ ] `tests/corpus/baseline.json` and `tests/corpus/floors.json` are committed and the gate is
      exact-match; the CI `test` job's step summary shows the per-category table on a PR.
- [ ] Zero runtime change (ruling 6): the `git diff --stat` set is empty, `derive_sanitizer_revision`
      is unchanged, `scripts.export_contract --check` is green.
- [ ] `docs/corpus.md` documents the record format, the outcome vocabulary, the add-a-record and
      re-record procedures, and the decision table; `kit_tools/arch/SECURITY.md`'s "Injection
      coverage is example-based unit testing" observation is replaced by the measured numbers;
      README carries the measured-defence paragraph; `PRODUCT_VISION.md` T2.3, MILESTONES and
      BACKLOG record the epic as shipped.
- [ ] Any bypass or over-defence the corpus surfaces is filed in `kit_tools/AUDIT_FINDINGS.md`
      with its record ids — not fixed in this epic.

## Notes

- **Why cassettes are honest.** The model is pinned by revision *and* by the manifest's per-file
  hashes (`model_fetcher.verify_weights`), so its scores cannot change without a re-vendoring —
  which is exactly the event that requires a re-recording. What *can* change without a re-vendoring
  is the text stage 3 receives (stage 1 extraction, search normalisation, URL canonicalisation) and
  the stage-3 rules over the scores; the first is caught by a cassette miss, the second is what the
  replay measures.
- **Before/after numbers.** The baseline is recorded post-hardening. A one-off "before" run of the
  recorder and report at the pre-hardening commit (`20ddb2a`) is optional evidence for the
  hardening release notes; it is not a story.
- **Poppy.** Poppy's half stubs Forage with corpus pages; `docs/corpus.md` "Consumers" states the
  record format is stable and vendorable one way. No Forage story serves Poppy.
