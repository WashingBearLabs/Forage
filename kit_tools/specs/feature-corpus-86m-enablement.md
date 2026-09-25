<!-- Template Version: 2.5.0 -->
---
feature: corpus-86m-enablement
status: active
session_ready: false
depends_on: [hardening-release]
vision_ref: "T2.3 — Injection regression corpus (CI)"
type: epic-child
size: L
epic: forage-injection-corpus
epic_seq: 0
epic_final: false
execution_order: [US-001, US-002, US-003, US-004]
created: 2026-09-24
updated: 2026-09-24
---

# Feature Spec: 86M Enablement — Vendor, Label Pin, Allowlist, Benchmark, Release

> Spec 0 of 6 in `epic-forage-injection-corpus` (wrapper: `epic-forage-injection-corpus.md`,
> owner decisions 17–18, ruling 6a). Added 2026-09-24 at validation round 4; revised the same day
> after its first review (round 5). **This is the only spec in the epic that changes runtime files**
> (ruling 6a names them). All four stories are **owner gates** run on the owner's lab host (the host
> Poppy runs on), not the development Mac — the work is CPU-bound and needs the gated weights,
> credentials and Docker. Anchors were read at `main` = `403e9c5` (post-hardening `v1.2.1`); cite
> symbols, re-grep lines.

## Overview

`epic-forage-hardening` built the seams for a second Prompt Guard model and shipped none of the
payload. Its spec 7 closed with US-005 (vendor the 86M) and US-004 (benchmark it) recorded
`gate not run, 2026-09-22` (`specs/archive/feature-hardening-promptguard-86m.md`, Implementation
Notes), which that spec names as the sanctioned "gates-unrun end state". On the shipped tree, four
things stand between `FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M` and a running service:

- `model_fetcher.ALLOWED_MODEL_IDS` is `frozenset({DEFAULT_MODEL_ID})`; `resolve_model_id()` falls
  back and the lifespan refuses boot (`model_id_not_allowed`).
- `weights_manifest.json` carries only the 22M entry.
- `promptguard/classifier.py`'s `_PINNED_GENERIC_LABEL_INDICES` has exactly one entry, the 22M
  `(model_id, revision)` pair. `PromptGuardClassifier.load()` refuses a config carrying only generic
  `LABEL_0`/`LABEL_1` names unless its exact pair is pinned (`model_labels_unexpected`). This is the
  v1.2.1 repair for the defect that withdrew `v1.2.0` (`docs/releases.md`; `kit_tools/docs/GOTCHAS.md`
  "Real model configs can omit human-readable labels": *new pins require new semantic evidence, not a
  blanket acceptance of arbitrary binary labels*).
- `pipeline/extraction_limits.py`'s `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` is
  `{DEFAULT_MODEL_ID: 0}`, read with a bare subscript by `_warn_if_envelope_memory_rule_unmet`
  (`retrieval_app.py`, ~:1418-1441 at 403e9c5), which the lifespan calls unconditionally at boot with
  the resolved model id. Selecting an allowlisted 86M today would **crash the lifespan with a
  `KeyError`** — not a degraded boot.

This spec finishes hardening spec 7's two gates, adds the label-semantics step and the resident-delta
entry hardening did not foresee, and releases the result as a **PATCH, `v1.2.2`** (owner decision
18), so a deployment can choose either model — **one model per process, selected by
`FORAGE_MODEL_ID`**, 22M staying the default. It does not change the default, the contract, or any
stage-3 rule.

## Goals

- `FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M`, at the memory limit the docs recommend for
  it, boots a service that downloads a digest-verified snapshot, loads it and reports
  `promptguard_loaded: true` with `promptguard_model` naming the 86M; unset, it behaves
  byte-identically to `v1.2.1`.
- The 86M's label semantics are established from evidence measured **outside** the classifier's
  refusal path, against the 22M as a control, and pinned by exact `(model_id, revision)` — never by a
  wider rule.
- 22M and 86M are measured on the reference container at `FORAGE_CPUS=1` and `4`; the sizing table
  and the resident-delta map carry measured values.
- `v1.2.2` is published with the 86M option; `derive_sanitizer_revision({})` at the default model is
  unchanged; the contract is unchanged.
- If any gate cannot run (licence mismatch, HF access pending, label evidence inconclusive or a label
  vocabulary outside scope), the spec stops in a named, recorded state and specs 1–5 proceed 22M-only
  (ruling 6a, "86M not enabled").

## User Stories

### US-001: Vendor the 86M weights — licence, manifest entry, mirror (owner gate, lab host)

**Priority:** P1

**Description:** As the owner, I want the 86M weights licence-checked, downloaded, hashed, entered
in `weights_manifest.json` and mirrored exactly as the 22M was, so a later story can pin and
allowlist a snapshot whose bytes are verified. **Execution halts here for the owner.**

This is hardening spec 7 US-005 as written (`specs/archive/feature-hardening-promptguard-86m.md`,
"US-005: Vendor the 86M weights"), with **one change**: `ALLOWED_MODEL_IDS` does **not** gain the
86M here. Hardening's rule was that the allowlist never names a model the service cannot serve; after
v1.2.1 the service cannot serve the 86M until US-002 settles its labels and resident delta, so the
allowlist edit moves to US-002's commit.

**Independent Test:** *Gate run* → `weights_manifest.json` carries a
`meta-llama/Llama-Prompt-Guard-2-86M` entry with a 40-hex `revision` and a non-empty `files[]`; the
scoped `manifest_diff` reports the 22M entry untouched; the mirror holds
`ghcr.io/washingbearlabs/forage-weights:<86M revision>`; `NOTICE` names both ids; `grep -c
'Llama-Prompt-Guard-2-86M' model_fetcher.py` is still `0`. *Gate not run* → Implementation Notes
carry `### US-001 — gate not run, <date>` naming the missing prerequisite, and nothing else changes.

**Acceptance Criteria:**
- [ ] The licence check is recorded with both identifier strings (the 22M's from `NOTICE`, the
      86M's from its model card) and the outcome; on mismatch nothing is vendored, this story records
      `### US-001 — gate not run — licence, <date>`, and US-002–US-004 record their `upstream` /
      not-enabled states naming it.
- [ ] `weights_manifest.json` carries the 86M entry, generated by `scripts/vendor_weights.py` (never
      hand-edited); `ALLOWED_SUFFIXES` unchanged; `DEFAULT_MODEL_REVISION` unchanged.
- [ ] The mirror tag exists and a fresh pull verifies against the committed entry.
- [ ] `docs/weights.md` states both pins; both of its credential blocks that type a value after
      `export` are rewritten to the one-file recipe (carried over from hardening spec 7 US-005); and
      "Bring your own token" names **both** gated repositories and states that Meta's approval is
      granted per repository, so 22M access does not imply 86M access.
- [ ] The credential file was mode 0600 and is recorded as deleted; the record holds no credential
      value (token-shape grep over the produced artifacts only, hardening ruling R43).
- [ ] The lab-host isolation posture is recorded before the first credentialed step (see Technical
      Considerations).
- [ ] `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` green.

**Implementation Hints:**
- **Procedure**: hardening spec 7 US-005's hints, verbatim — licence first, the one-file credential
  recipe (`umask 077`, `mktemp`, `trap`; never on a command line, never `export NAME=<value>`),
  `docs/weights.md` § "Vendoring: the procedure" with `--model-id meta-llama/Llama-Prompt-Guard-2-86M
  --revision <sha from the model card>`, allow-patterns only, the scoped `manifest_diff`, the
  revision-keyed mirror push, and its failure paths (403 → `access pending`; 429 / partial → clear
  and re-run, recorded).
- **Where**: the lab host. Record OS/arch, Python and `uv` versions; the vendoring does not need the
  service running. Expect the 86M `model.safetensors` near 1.1 GB (mDeBERTa-base with a multilingual
  embedding table) against the 22M's 283 MB — check the working cache's free space first.
- **Not in this commit**: `ALLOWED_MODEL_IDS`, `_PINNED_GENERIC_LABEL_INDICES`,
  `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL`, the "Pending vendoring" rows in `docs/configuration.md`
  / `kit_tools/docs/ENV_REFERENCE.md` — all US-002.

### US-002: Establish the 86M label semantics; pin, size and allowlist it (owner gate, lab host)

**Priority:** P1

**Description:** As the owner, I want evidence that the 86M's injection class is the index I pin,
then the pin, a resident-delta entry and the allowlist entry in one commit, so selecting the 86M
loads a model whose "injection" score means what stage 3 assumes and boots without crashing — the
failure `v1.2.0` shipped with, not repeated for a second model. **Execution halts here for the
owner.**

**Independent Test:** *Gate run* → `ALLOWED_MODEL_IDS` has exactly two members;
`CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` has exactly two keys; `_PINNED_GENERIC_LABEL_INDICES` has
two entries (22M unchanged, 86M added at its manifest revision) **or** one entry plus a recorded
named-labels finding; a genuine-config fixture test under `tests/fixtures/promptguard_86m_config/`
passes; a real-weights boot on the lab host at the recommended memory limit reaches
`promptguard_loaded: true` with `promptguard_model` = the 86M; the evidence table is in
Implementation Notes. *Gate not run* → `### US-002 — gate not run — <reason>, <date>` with reason
one of `upstream` (US-001 not run), `evidence` (direction test failed), `label vocabulary` (see
below) or `smoke` (the real-weights smoke failed), and nothing else changes.

**Acceptance Criteria:**
- [ ] **Config read first**: the 86M `config.json` from the verified snapshot is inspected and its
      `id2label` / `label2id` recorded. Three branches, and only three:
      (a) generic `LABEL_0`/`LABEL_1` → the evidence criterion below, then a pin;
      (b) human-readable names `load()` already accepts → no pin; record it and go to the
          resident-delta criterion;
      (c) any other vocabulary (e.g. names `load()` does not accept) → stop,
          `gate not run — label vocabulary`; enabling it needs an owner ruling outside ruling 6a.
- [ ] **Evidence is gathered without the pin.** The probe runs in an uncommitted host-side script
      that loads the digest-verified snapshot directly — `AutoTokenizer` /
      `AutoModelForSequenceClassification.from_pretrained(<snapshot dir>, local_files_only=True,
      use_safetensors=True)` — and prints **both** softmax columns per probe. It never edits
      `_PINNED_GENERIC_LABEL_INDICES` to get a loadable classifier (that would assume the answer),
      and never goes through `classify_windows`, which returns only the pinned column.
- [ ] **Direction test with a control.** The same script scores the same probes with the 22M, whose
      index 1 is established by the v1.2.1 repair. The probe set is every probe the 22M scores
      confidently (index-1 probability > 0.9 → injection probe, < 0.1 → benign probe), drawn from at
      least two existing test modules, with at least 10 of each class. Injection text in the tests is
      mostly inline literals, not module constants, so each probe is cited by
      `module::test_function` plus its line at the spec 0 start commit **and** the sha256 of the probe
      text — never quoted (ruling 8). If the tests cannot supply 10 confident probes per class, an
      uncommitted host-side probe list may be used, and the committed record is per-probe sha256 +
      source category + both models' columns. The 86M's candidate index must agree in direction
      with the 22M on **every** probe; one disagreement per class may be accepted if recorded with its
      scores and the owner signs it off; more → `gate not run — evidence`. The model card's statement
      of class order is quoted alongside. This separates "which index is injection" (what the pin
      asserts) from "does the 86M separate hard probes perfectly" (spec 5's question, not this one's).
- [ ] `_PINNED_GENERIC_LABEL_INDICES` gains exactly the 86M `(model_id, revision)` entry (branch a
      only); the 22M entry is byte-unchanged; no pattern, prefix or wildcard key is introduced.
- [ ] `tests/fixtures/promptguard_86m_config/config.json` is the genuine 86M config, and a test
      mirrors the 22M one (`tests/test_stage3_promptguard.py`, the `promptguard_22m_config` fixture
      test): the fixture's sha256 equals the manifest's `config.json` hash, and `load()` with it
      derives the pinned index (branch a) or the named index (branch b). A negative test: the same
      config under a different revision is refused with `model_labels_unexpected` (branch a).
- [ ] The new fixture is added as an exact file entry to `tests/test_brave_provider.py`'s
      `_TOKEN_WALK_ALLOWLIST` **and** to `test_token_walk_exceptions_are_pinned`'s exact-set
      assertion (a genuine DeBERTa config carries 24+-character identifiers that trip the token walk),
      and to `tests/fixtures/README.md`'s exemption sentence, with a provenance section parallel to
      the existing `promptguard_22m_config/config.json` one.
- [ ] `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` gains the 86M with a **provisional** value — the
      difference of the two manifests' `model.safetensors` sizes, rounded up to the next MiB, with a
      `# Provisional` comment naming US-003 as the story that replaces it — and a test boots the
      lifespan with `FORAGE_MODEL_ID=<86M>` (weights stubbed) without a `KeyError`.
      `tests/test_app.py::test_provisional_memory_rule_constants_and_default_margins`'s exact-map
      assertion (`== {DEFAULT_MODEL_ID: 0}`) becomes an exact two-key equality — updated, never
      loosened to "contains". A new invariant test asserts every `ALLOWED_MODEL_IDS` member is a key
      of the map, so a third model cannot reintroduce the crash.
- [ ] `ALLOWED_MODEL_IDS` gains the 86M **in the same commit** as this story's label determination
      (the pin in branch a, the recorded finding in branch b) and the resident-delta entry — never
      later; a new exact-set pin `ALLOWED_MODEL_IDS == frozenset({22M, 86M})` is added (the allowlist
      has no exact-set guard today — `test_every_allowlisted_model_has_a_manifest_entry` iterates it);
      the tests that use the 86M id as the canonical *disallowed* example
      (`tests/test_app.py::test_lifespan_refuses_disallowed_model_without_echoing_the_value`,
      `tests/test_model_fetcher.py::test_disallowed_model_id_is_a_total_closed_warning`) switch to a
      plausible-but-unlisted id; `docs/configuration.md` and `kit_tools/docs/ENV_REFERENCE.md` lose "Pending
      vendoring" and name both ids.
- [ ] A **real-weights candidate smoke** on the lab host (GOTCHAS: the weights-free CI smoke cannot
      establish readiness), on an image built from the candidate commit **before it is pushed or
      merged**; a smoke failure reverts landing steps (2)–(4) and records `gate not run — smoke`, so no
      branch tip ever allowlists a model that cannot boot. `docker build` from that commit, run with `FORAGE_MODEL_ID=<86M>` via the
      one-file `--env-file` and `--memory` set to the limit the docs will recommend for the 86M (US-003
      confirms it; start at `2048m`); `/health` shows `promptguard_loaded: true`, `promptguard_model`
      = the 86M, `degraded_reasons` empty; one `/extract` of a benign fixture returns
      `promptguard_state: scanned`. The same smoke with the variable unset, at `1024m`, shows the 22M
      unchanged.
- [ ] `derive_sanitizer_revision({})` (model variables unset) prints the same value before and after
      this story; recorded.
- [ ] **Scope asserted**: `git diff --stat <spec 0 start commit> -- pipeline/ promptguard/ models.py
      retrieval_app.py cache.py url_validator.py contract/ config.yaml Dockerfile` lists only
      `pipeline/extraction_limits.py` and `promptguard/classifier.py`, and `git diff <start> --
      model_fetcher.py` touches only `ALLOWED_MODEL_IDS` (ruling 6a).
- [ ] `uv run pytest`, `ruff check`, `ruff format --check`, `pyright` green; `tests/test_dockerfile.py`
      green (no build ARG).

**Implementation Hints:**
- **Landing order**, a green suite at each step: (1) config read + probe script run + evidence table
  recorded — no code change; (2) pin (branch a) + genuine-config fixture + positive/negative tests +
  token-walk allowlist; (3) resident-delta entry + its boot test; (4) allowlist + docs rows, same
  commit as (2)–(3); (5) real-weights smoke at both limits; (6) revision and scope checks.
- `PromptGuardClassifier.load()` in `promptguard/classifier.py` consults
  `_PINNED_GENERIC_LABEL_INDICES` (search `model_labels_unexpected`); read the whole label-derivation
  block before editing — it also accepts named labels, and that path must not widen.
- `resolve_model_id()` and `ALLOWED_MODEL_IDS` are in `model_fetcher.py`;
  `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` and its `PARENT_RESERVATION_BYTES` sibling are in
  `pipeline/extraction_limits.py`, consumed by `_warn_if_envelope_memory_rule_unmet` in
  `retrieval_app.py`.
- The probe script and its output are not committed; the table (probe module + constant name, both
  models' two columns, the direction verdict) is.

### US-003: Benchmark both models; replace the provisional delta (owner gate, lab host)

**Priority:** P2

**Description:** As the owner, I want 22M and 86M latency and memory measured on the running
container at `FORAGE_CPUS=1` and `4`, recorded here and in the sizing table, and the 86M's measured
resident delta written into `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL`, so operators can choose
between the models and the envelope warning is honest. **Execution halts here for the owner.**

This is hardening spec 7 US-004 as written ("US-004: Run the benchmark on the reference container
and record it"), with `scripts/bench_promptguard.py` (shipped by hardening spec 7 US-003), run on the
lab host — plus a planned second memory limit for the 86M, because it is **expected not to fit** the
1024m reference envelope.

**Independent Test:** *Gate run* → Implementation Notes carry `### US-003 — benchmark, <date>` with
the four required rows ({22M, 86M} × {1, 4} CPUs) at the default limit, plus the 86M rows re-run at
the recommended raised limit if the default OOMs or never goes healthy; each row's
`promptguard_model` and `sanitizer_revision` read from `/health`, the memory and `memory.peak`
columns, the OOM/exit line per run, and the lab host's CPU model / core count / RAM;
`docs/configuration.md`'s sizing table has the classifier column filled for 1 and 4 vCPU with both
models, and `grep -c 'measured in spec 7' docs/configuration.md` is `0`. *US-002 recorded gate not
run* → `### US-003 — 86M not benchmarked (86M not enabled), <date>`; the 22M rows may still be
measured and recorded. *Gate not run for this story's own prerequisites* → `### US-003 — gate not
run, <date>`.

**Acceptance Criteria:**
- [ ] Hardening spec 7 US-004's acceptance criteria, applied here (the table, the per-run records,
      `--env-file` only, loopback-bound port, `docker rm -f`, no `docker inspect` / `docker ps
      --no-trunc` / `docker compose config` output in the record).
- [ ] The 86M at the default `1024m` limit is attempted and its outcome recorded (OOM line,
      `memory.peak`); if it does not go healthy, the 86M rows are re-run at the raised limit US-002
      started from (or the smallest that holds), and the docs name that limit in the 86M opt-in
      recipe. No default changes.
- [ ] The five "measured in spec 7 (`feature-hardening-promptguard-86m` US-004)" placeholder cells in
      `docs/configuration.md` (the sizing table and the memory-rule table's 86M row) are replaced with
      measured values or `not measured — <reason>`; the grep above is `0`.
- [ ] `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL`'s 86M entry is replaced with the measured delta and
      its `# Provisional` comment removed. The reading is the server process's **`VmRSS`** from
      `/proc/1/status` inside the container (PID 1 is uvicorn — the entrypoint `exec`s it), not
      `memory.current`: `memory.current` also charges unmapped page cache from reading
      `model.safetensors` (~1.1 GB for the 86M), while anonymous-only readings (`anon`, `RssAnon`)
      miss weights that stay mmap-backed under safetensors. `VmRSS` counts both anonymous and mapped
      resident pages; `RssAnon` / `RssFile` are recorded beside it. Read after `/health` first shows
      `promptguard_loaded: true`
      and **before the first request**, per model, at `FORAGE_CPUS=1` (and at 4 if taken — the larger
      delta is used), rounded up to the next MiB — the resident, no-classification figure
      `PARENT_RESERVATION_BYTES` is defined against. `memory.current` is kept in the record for
      context. US-002's provisional safetensors-size difference is a **two-sided** sanity bound: a
      delta far above it points to a cache artefact, one far below it to unmapped weights; either is
      re-read and explained before it is committed.
- [ ] The per-classification working set is **not** measured per model here:
      `PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES` is one model-independent constant outside ruling 6a.
      The `docs/configuration.md` 86M working-set cell reads `not measured — model-independent
      provisional constant`, the "Spec 7 US-004 replaces it" sentence is reworded to name a follow-up
      (BACKLOG entry added), and Known risks records that the envelope warning uses a 22M-derived
      working set for both models.
- [ ] The record names the host and states that `--cpus` pins the envelope but absolute latencies
      are host-specific; before the first run, port and volume availability on the host is recorded
      (see Technical Considerations).
- [ ] `docs/configuration.md` documents the 86M opt-in recipe and its acquisition caveat once each
      (`grep -c 'FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M' docs/configuration.md` ≥ 1).

**Implementation Hints:**
- `docs/weights.md`'s two-fresh-service procedure and the hardening US-004 "gate not run" record
  (archived spec, Implementation Notes) give the exact order: process-cold samples first, window-count
  confirmation and optional probes after, `VALKEY_URL` unset, 900 s readiness bound.

### US-004: Release the 86M option as `v1.2.2` (owner gate, lab host)

**Priority:** P1

**Description:** As the owner, I want a published PATCH release carrying US-001–US-003, so a
deployment (and Poppy) can select the 86M from a public image, and so spec 4's 86M cassette records
against a released model identity.

**Independent Test:** `docs/releases.md` records `v1.2.2`, its tag commit and index digest; the
published image boots with `FORAGE_MODEL_ID=<86M>` at the recommended limit to
`promptguard_loaded: true` (lab host); the contract document is unchanged (`uv run python -m
scripts.export_contract --check` green, no `contract/openapi.yaml` diff since `v1.2.1`).

**Acceptance Criteria:**
- [ ] The version is **`v1.2.2` (PATCH)** — owner decision 18. The release notes say why: the 86M is
      an opt-in addition behind an existing configuration key, the default is unchanged, and a PATCH
      does not trigger the two "next MINOR" compatibility windows `docs/releases.md` and
      `contract/GOVERNANCE.md` publish (the `retrieve.max_promptguard_chunks` default flip and the
      request-validation 422 field drop) — **both windows stay open**, and the notes say so. They
      also state "contract `1.3.0`, unchanged from `v1.2.1`".
- [ ] The release follows `docs/releases.md` (there is no `kit_tools/docs/BUMP_VERSION.md`); all six
      gates plus the layer-identity check pass; anonymous pull verified.
- [ ] `compose/minimal.yml` and `compose/full.yml` and the docs that pin `v1.2.1` move to `v1.2.2`
      (deployment docs, within ruling 6a's "docs"); the compose `FORAGE_MEM_LIMIT` default stays
      `1024m`, and the 86M recipe overrides it.
- [ ] **Before the tag is pushed**, a real-weights candidate smoke runs on an image built on the lab
      host from the **exact commit to be tagged** (commit recorded), once per model (22M default at
      `1024m`, 86M selected at its recommended limit). CI's own build is covered by the layer-identity
      check; the post-publish boot above confirms the released bytes.
- [ ] Release notes state: 86M selectable, 22M remains the default, contiguity remains off, no
      contract change, the 86M's recommended memory limit and its separate gated-access grant; the
      Poppy coexistence note records the new pin option.
- [ ] If US-002 recorded `gate not run`, this story records `### US-004 — not released (86M not
      enabled)` and nothing is tagged. If only US-003 is `gate not run` or partial, the release may
      proceed; the notes then say "86M sizing not yet measured" and the opt-in recipe carries the
      conservative limit US-002 used.

**Implementation Hints:**
- `docs/releases.md` is the tag-scheme reference; the v1.2.0 withdrawal section there is why the
  candidate smoke is an acceptance criterion, not a hint.

## Edge Cases

- **HF gated access pending** (403): US-001 records `access pending`; US-002–US-004 record their
  `upstream` / not-released states; the epic proceeds 22M-only (ruling 6a).
- **The 86M config carries accepted named labels** (branch b): no pin; the allowlist and resident
  delta land in one candidate commit exactly as in branch a, and the real-weights smoke confirms that
  commit before it is pushed (the lifespan refuses a non-allowlisted id, so the smoke cannot precede
  the allowlist). Branch b carries no direction test; the smoke's one `/extract` plus the recorded
  label maps are its evidence, and spec 5's numbers are the corroboration.
- **The 86M config carries some other vocabulary** (branch c): stop, no edit; an owner ruling outside
  ruling 6a is required.
- **The direction test fails**: no pin; record the table; US-002 stops. The disagreements are
  themselves input for spec 5's decision table.
- **The 86M does not fit** `1024m`: the expected path, not an exception — US-003 records it,
  benchmarks at the raised limit and documents it; nothing about defaults changes.
- **Port 8020 or the `forage-model-cache` volume name is taken on the lab host** (Poppy's retrieval
  service is Forage's ancestor): use a distinct loopback port and volume name; the harness takes a
  base URL.

## Out of Scope

- Changing the default model, `promptguard_threshold`, or contiguity defaults (spec 5 US-004 produces
  the decision table; flipping a default is a later ruling).
- The `retrieve.max_promptguard_chunks` default flip and the 422 field drop promised for the next
  MINOR (owner decision 18 keeps this release a PATCH so both stay open).
- Loading both models in one process, or per-request model selection.
- Any contract change.

## Assumptions

- The owner has, or can obtain, gated access to `meta-llama/Llama-Prompt-Guard-2-86M` on Hugging Face.
- The lab host has Docker, `uv`, `oras`, egress to Hugging Face / GHCR / GitHub, and RAM for the 86M
  at a raised limit (~2 GB per container).
- `classifier.py`, `model_fetcher.py` and `extraction_limits.py` are not `_REVISION_SOURCES` members
  (`pipeline/sanitizer_revision.py`), so this spec's edits do not rotate `sanitizer_revision` at the
  default model; selecting the 86M changes the hashed `MODEL_ID@revision` input, by design.

## Technical Considerations

- **Scope (ruling 6a)**: runtime edits are limited to `model_fetcher.py` (`ALLOWED_MODEL_IDS` only),
  `promptguard/classifier.py` (`_PINNED_GENERIC_LABEL_INDICES` only), `pipeline/extraction_limits.py`
  (`CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` only), `weights_manifest.json`, `NOTICE`, docs and
  tests; US-002's scope criterion asserts it. Specs 1–5's ruling-6 assertion diffs against this
  spec's completion tag (`forage-injection-corpus/corpus-86m-enablement-complete`), not `main`.
- **Lab-host isolation posture** (recorded in US-001 before the first credentialed step): the runs
  use a dedicated OS user or a separate Docker context, write-scoped credentials live only in the
  one-file recipe for the duration of US-001, and the owner records either that isolation or an
  explicit acceptance that the host is single-owner and not exposed. Benchmark containers are
  loopback-bound and removed after each run.

## Related Documentation

- `kit_tools/specs/archive/feature-hardening-promptguard-86m.md` — US-003/US-004/US-005 and their
  gate-not-run records
- `docs/weights.md`, `docs/configuration.md`, `docs/releases.md`, `contract/GOVERNANCE.md`,
  `kit_tools/docs/GOTCHAS.md`

## Implementation Notes

## Refinement Notes

### Decisions Made

- 2026-09-24 (owner decision 17): the 86M is a prerequisite inside this epic, not a follow-up and not
  a separate epic; the gates run on the lab host.
- 2026-09-24 (owner decision 18): release as PATCH `v1.2.2`, so the two published "next MINOR"
  compatibility windows are neither triggered nor reinterpreted.
- The label pin is new semantic-evidence work that hardening spec 7 did not cover, because the v1.2.1
  repair postdates it; the resident-delta entry is new because hardening spec 7's US-004 never ran to
  replace the provisional single-key map.

### Validation round 5 — 2026-09-24 (first review of this spec)

Six reviewers. Fixed: probe-before-pin circularity (evidence via direct transformers load, 22M
control, direction test) (6, 5); lifespan `KeyError` on the one-key resident-delta map → provisional
entry in US-002, measured in US-003, ruling 6a widened (4); version conflict with the "next MINOR"
windows → PATCH `v1.2.2` by owner decision 18 (6, 3); 86M expected not to fit 1024m → planned raised
limit and smokes run under it (6); third label-vocabulary branch (6); allowlist-same-commit wording
for the named-labels branch (3); US-003 state when US-002 stops (1, 3); literal scope diff criterion
(3); token-walk allowlist as a certainty (6, 3); stale "measured in spec 7" cells (4); lab-host
isolation posture (5); per-repo gated access in "Bring your own token" (5); candidate-smoke image
provenance (6); port/volume collision (6); release may proceed without sizing (6); landing order
(2); US-004 heading (1). Second pass (round 6): probe citation by test function + sha256 (6);
branch-b ordering and a `smoke` stop reason (3, 6); the real exact-equality tests that go red, an
exact-set allowlist pin and an allowlist⊆delta-map invariant (4, 6, 5); named delta reading and the
working-set cell ruled out of scope (6); compose pins and memory default (6); licence stop reason (1).
Third pass (rounds 7–8): the resident delta reads process `VmRSS` (anonymous + mapped), not
`memory.current` or anonymous-only, with the provisional value as a two-sided bound (6). Carried: PRODUCT_VISION's T2.3 spec list gains this spec at epic close (1),
already covered by the wrapper's completion criteria.

## Open Questions

- Which lab host, and whether it can hold a `forage:bench` build alongside Poppy's services without
  contention skewing the benchmark (US-003 records the host; the owner decides whether to quiesce).

## Known risks (planning)

- The 86M may ship generic labels whose order differs from the 22M's; the direction test against the
  22M control is what catches that, which is why it is an acceptance criterion rather than a hint.
- Meta's gated-repo approval is not instant, and is per repository; the named "access pending" end
  state keeps the epic moving 22M-only.
- The envelope warning uses the 22M-derived `PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES` for both
  models until a follow-up measures a per-model working set (US-003 records this).
- The provisional resident delta may under-reserve until US-003 measures it; the envelope check only
  warns, so the risk is a misleading WARNING for the interval between US-002 and US-003, not a crash.
