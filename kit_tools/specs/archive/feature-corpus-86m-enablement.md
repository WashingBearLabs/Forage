<!-- Template Version: 2.5.0 -->
---
feature: corpus-86m-enablement
status: completed
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
updated: 2026-10-04
completed: 2026-10-04
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
- [x] The licence check is recorded with both identifier strings (the 22M's from `NOTICE`, the
      86M's from its model card) and the outcome; on mismatch nothing is vendored, this story records
      `### US-001 — gate not run — licence, <date>`, and US-002–US-004 record their `upstream` /
      not-enabled states naming it.
- [x] `weights_manifest.json` carries the 86M entry, generated by `scripts/vendor_weights.py` (never
      hand-edited); `ALLOWED_SUFFIXES` unchanged; `DEFAULT_MODEL_REVISION` unchanged.
- [x] The mirror tag exists and a fresh pull verifies against the committed entry.
- [x] `docs/weights.md` states both pins; both of its credential blocks that type a value after
      `export` are rewritten to the one-file recipe (carried over from hardening spec 7 US-005); and
      "Bring your own token" names **both** gated repositories and states that Meta's approval is
      granted per repository, so 22M access does not imply 86M access.
- [x] The credential file was mode 0600 and is recorded as deleted; the record holds no credential
      value (token-shape grep over the produced artifacts only, hardening ruling R43).
- [x] The lab-host isolation posture is recorded before the first credentialed step (see Technical
      Considerations).
- [x] `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` green.

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
- [x] **Config read first**: the 86M `config.json` from the verified snapshot is inspected and its
      `id2label` / `label2id` recorded. Three branches, and only three:
      (a) generic `LABEL_0`/`LABEL_1` → the evidence criterion below, then a pin;
      (b) human-readable names `load()` already accepts → no pin; record it and go to the
          resident-delta criterion;
      (c) any other vocabulary (e.g. names `load()` does not accept) → stop,
          `gate not run — label vocabulary`; enabling it needs an owner ruling outside ruling 6a.
- [x] **Evidence is gathered without the pin.** The probe runs in an uncommitted host-side script
      that loads the digest-verified snapshot directly — `AutoTokenizer` /
      `AutoModelForSequenceClassification.from_pretrained(<snapshot dir>, local_files_only=True,
      use_safetensors=True)` — and prints **both** softmax columns per probe. It never edits
      `_PINNED_GENERIC_LABEL_INDICES` to get a loadable classifier (that would assume the answer),
      and never goes through `classify_windows`, which returns only the pinned column.
- [x] **Direction test with a control** (*amended 2026-10-01 by owner decision 19*). The same script
      scores the same probes with the 22M, whose index 1 is established by the v1.2.1 repair; the
      probe set is every test-module string the 22M scores confidently (index-1 probability > 0.9 →
      injection probe, < 0.1 → benign probe), cited by `module::function`, line and sha256 — never
      quoted (ruling 8). Because the 22M control is itself noisy on code and config text, the
      verdict is taken on the **stage-2-corroborated control set** (probes where stage 2's
      structural verdict agrees with the 22M's class): the candidate index must agree with the 22M
      on **≥ 95%** of each class there, and the opposite index on **≤ 5%** overall. The full table,
      the stage-2 cross-check and every disagreement are recorded; below the thresholds →
      `gate not run — evidence`. The model card's class names are quoted alongside. This separates
      "which index is injection" (what the pin asserts) from "does the 86M separate hard probes
      perfectly" (spec 5's question, not this one's).
- [x] `_PINNED_GENERIC_LABEL_INDICES` gains exactly the 86M `(model_id, revision)` entry (branch a
      only); the 22M entry is byte-unchanged; no pattern, prefix or wildcard key is introduced.
- [x] `tests/fixtures/promptguard_86m_config/config.json` is the genuine 86M config, and a test
      mirrors the 22M one (`tests/test_stage3_promptguard.py`, the `promptguard_22m_config` fixture
      test): the fixture's sha256 equals the manifest's `config.json` hash, and `load()` with it
      derives the pinned index (branch a) or the named index (branch b). A negative test: the same
      config under a different revision is refused with `model_labels_unexpected` (branch a).
- [x] The new fixture is added as an exact file entry to `tests/test_brave_provider.py`'s
      `_TOKEN_WALK_ALLOWLIST` **and** to `test_token_walk_exceptions_are_pinned`'s exact-set
      assertion (a genuine DeBERTa config carries 24+-character identifiers that trip the token walk),
      and to `tests/fixtures/README.md`'s exemption sentence, with a provenance section parallel to
      the existing `promptguard_22m_config/config.json` one.
- [x] `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` gains the 86M with a **provisional** value — the
      difference of the two manifests' `model.safetensors` sizes, rounded up to the next MiB, with a
      `# Provisional` comment naming US-003 as the story that replaces it — and a test boots the
      lifespan with `FORAGE_MODEL_ID=<86M>` (weights stubbed) without a `KeyError`.
      `tests/test_app.py::test_provisional_memory_rule_constants_and_default_margins`'s exact-map
      assertion (`== {DEFAULT_MODEL_ID: 0}`) becomes an exact two-key equality — updated, never
      loosened to "contains". A new invariant test asserts every `ALLOWED_MODEL_IDS` member is a key
      of the map, so a third model cannot reintroduce the crash.
- [x] `ALLOWED_MODEL_IDS` gains the 86M **in the same commit** as this story's label determination
      (the pin in branch a, the recorded finding in branch b) and the resident-delta entry — never
      later; a new exact-set pin `ALLOWED_MODEL_IDS == frozenset({22M, 86M})` is added (the allowlist
      has no exact-set guard today — `test_every_allowlisted_model_has_a_manifest_entry` iterates it);
      the tests that use the 86M id as the canonical *disallowed* example
      (`tests/test_app.py::test_lifespan_refuses_disallowed_model_without_echoing_the_value`,
      `tests/test_model_fetcher.py::test_disallowed_model_id_is_a_total_closed_warning`) switch to a
      plausible-but-unlisted id; `docs/configuration.md` and `kit_tools/docs/ENV_REFERENCE.md` lose "Pending
      vendoring" and name both ids.
- [x] A **real-weights candidate smoke** on the lab host (GOTCHAS: the weights-free CI smoke cannot
      establish readiness), on an image built from the candidate commit **before it is pushed or
      merged**; a smoke failure reverts landing steps (2)–(4) and records `gate not run — smoke`, so no
      branch tip ever allowlists a model that cannot boot. `docker build` from that commit, run with `FORAGE_MODEL_ID=<86M>` via the
      one-file `--env-file` and `--memory` set to the limit the docs will recommend for the 86M (US-003
      confirms it; start at `2048m`); `/health` shows `promptguard_loaded: true`, `promptguard_model`
      = the 86M, `degraded_reasons` empty; one `/extract` of a benign fixture returns
      `promptguard_state: scanned`. The same smoke with the variable unset, at `1024m`, shows the 22M
      unchanged.
- [x] `derive_sanitizer_revision({})` (model variables unset) prints the same value before and after
      this story; recorded.
- [x] **Scope asserted**: `git diff --stat <spec 0 start commit> -- pipeline/ promptguard/ models.py
      retrieval_app.py cache.py url_validator.py contract/ config.yaml Dockerfile` lists only
      `pipeline/extraction_limits.py` and `promptguard/classifier.py`, and `git diff <start> --
      model_fetcher.py` touches only `ALLOWED_MODEL_IDS` (ruling 6a).
- [x] `uv run pytest`, `ruff check`, `ruff format --check`, `pyright` green; `tests/test_dockerfile.py`
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
- [x] Hardening spec 7 US-004's acceptance criteria, applied here (the table, the per-run records,
      `--env-file` only, loopback-bound port, `docker rm -f`, no `docker inspect` / `docker ps
      --no-trunc` / `docker compose config` output in the record).
- [x] The 86M at the default `1024m` limit is attempted and its outcome recorded (OOM line,
      `memory.peak`); if it does not go healthy, the 86M rows are re-run at the raised limit US-002
      started from (or the smallest that holds), and the docs name that limit in the 86M opt-in
      recipe. No default changes.
- [x] The five "measured in spec 7 (`feature-hardening-promptguard-86m` US-004)" placeholder cells in
      `docs/configuration.md` (the sizing table and the memory-rule table's 86M row) are replaced with
      measured values or `not measured — <reason>`; the grep above is `0`.
- [x] `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL`'s 86M entry is replaced with the measured delta and
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
- [x] The per-classification working set is **not** measured per model here:
      `PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES` is one model-independent constant outside ruling 6a.
      The `docs/configuration.md` 86M working-set cell reads `not measured — model-independent
      provisional constant`, the "Spec 7 US-004 replaces it" sentence is reworded to name a follow-up
      (BACKLOG entry added), and Known risks records that the envelope warning uses a 22M-derived
      working set for both models.
- [x] The record names the host and states that `--cpus` pins the envelope but absolute latencies
      are host-specific; before the first run, port and volume availability on the host is recorded
      (see Technical Considerations).
- [x] `docs/configuration.md` documents the 86M opt-in recipe and its acquisition caveat once each
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
- [x] The version is **`v1.2.2` (PATCH)** — owner decision 18. The release notes say why: the 86M is
      an opt-in addition behind an existing configuration key, the default is unchanged, and a PATCH
      does not trigger the two "next MINOR" compatibility windows `docs/releases.md` and
      `contract/GOVERNANCE.md` publish (the `retrieve.max_promptguard_chunks` default flip and the
      request-validation 422 field drop) — **both windows stay open**, and the notes say so. They
      also state "contract `1.3.0`, unchanged from `v1.2.1`".
- [x] The release follows `docs/releases.md` (there is no `kit_tools/docs/BUMP_VERSION.md`); all six
      gates plus the layer-identity check pass; anonymous pull verified.
- [x] `compose/minimal.yml` and `compose/full.yml` and the docs that pin `v1.2.1` move to `v1.2.2`
      (deployment docs, within ruling 6a's "docs"); the compose `FORAGE_MEM_LIMIT` default stays
      `1024m`, and the 86M recipe overrides it.
- [x] **Before the tag is pushed**, a real-weights candidate smoke runs on an image built on the lab
      host from the **exact commit to be tagged** (commit recorded), once per model (22M default at
      `1024m`, 86M selected at its recommended limit). CI's own build is covered by the layer-identity
      check; the post-publish boot above confirms the released bytes.
- [x] Release notes state: 86M selectable, 22M remains the default, contiguity remains off, no
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
  tests; US-002's scope criterion asserts it. This spec runs on its own branch from `main`, in
  parallel with specs 1–3, and reaches `main` through its PR and the `v1.2.2` release; specs 1–5's
  ruling-6 assertion diffs against the epic branch's merge base with `main`, which excludes it.
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

### US-004 — v1.2.2 published and verified, 2026-10-04

**Current completion record.** The owner cut `v1.2.2` (PATCH, owner decision 18) from the PR #36
merge commit; the prep note below is historical evidence. Contract `1.3.0`, byte-identical to
`v1.2.1` (`git diff v1.2.1 v1.2.2 -- contract/openapi.yaml contract/openapi.yaml.sha256` is empty;
anchor `74b9db01…`). Both "next MINOR" windows remain open.

- **Tag:** `v1.2.2` = `c213bbfbe31c42dcf3a84dcaba14145e89805182`, the merge of PR #36 (spec 0
  US-001–US-004 prep). The compose/doc pin commit was `c933673` (release prep), merged via
  PR #36 at `c213bbf` (2026-10-03T23:18:28Z).
- **Pre-tag real-weights candidate smoke on the exact tagged commit** (lab host `thelab-claude`,
  image `forage:cand-c213bbf` = `sha256:46330782f2c9…`, built from `c213bbf` before the tag was
  pushed):

| Model | Limit | Healthy after | `promptguard_loaded` | `sanitizer_revision` | `/extract` | `memory.peak` (bytes) | OOM |
|---|---|---|---|---|---|---|---|
| 86M (`FORAGE_MODEL_ID`) | `1536m` | 21 s | true (`promptguard_model` 86M) | `b5e91fd64727…` | 200, `scanned` | 658,604,032 | none |
| 22M (default) | `1024m` | 15 s | true | `021378efee6a…` (unchanged) | 200, `scanned` | 527,351,808 | none |

- **Publish:** [run 37163854549](https://github.com/WashingBearLabs/Forage/actions/runs/37163854549)
  — lint, typecheck, test, build-amd64, secret-grep, smoke and publish (which carries the
  layer-identity check) all success; the `searxng-*` jobs skipped on a service tag.
- **Release:** [`v1.2.2`](https://github.com/WashingBearLabs/Forage/releases/tag/v1.2.2),
  publishedAt 2026-10-04T00:09:48Z, not draft, not prerelease, assets `openapi.yaml` and
  `openapi.yaml.sha256`. Its body carries `contract: 1.3.0`, "Contract 1.3.0, unchanged from
  v1.2.1", the why-a-PATCH paragraph, both open windows, 22M default, contiguity off, the
  `1536m` recipe, the separate gated-access grant and the Poppy coexistence pin option.
- **Index digest:** `sha256:5cb60943b99da45829613cde1f8286bdb4b72866210aa2146ca0cc5233569365`;
  `1.2.2`, `1.2` and `latest` all resolve to it (`docker buildx imagetools inspect` from an empty
  `DOCKER_CONFIG`, i.e. anonymously). Anonymous `docker pull ghcr.io/washingbearlabs/forage:1.2.2`
  succeeded on the lab host.
- **Post-publish boot of the published image** `ghcr.io/washingbearlabs/forage:1.2.2` with
  `FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M` at `1536m`: `status` healthy,
  `degraded_reasons` `[]`, `promptguard_loaded` true, `promptguard_model` 86M,
  `sanitizer_revision` `b5e91fd64727…`, `contract_version` 1.3.0; `/extract` 200,
  `promptguard_state` `scanned`.
- **Credentials:** the lab host's HF token file was shredded after verification; no registry
  login is stored. No credential value appears in this record.
- **Window:** the merge-to-publication window (PR #36 merge 2026-10-03T23:18:28Z to publication
  2026-10-04T00:09:48Z) is closed; the `<v1.2.2 pin commit>` revert placeholders in the docs are
  replaced by this historical framing.
- **AC status:** the five substantive criteria are ticked. The last criterion is a conditional
  branch (US-002 `gate not run`, or US-003 partial) that did not occur — US-002 and US-003 both
  completed — so it is inapplicable and left unticked, not a claim that anything was skipped.

### US-004 — release prep, 2026-10-03 (historical; superseded by the record above)

Release-prep changes only, on `feat/corpus-86m-enablement`; nothing committed, tagged or pushed by
this step. Version is `v1.2.2` (PATCH, owner decision 18); contract `1.3.0`, unchanged from
`v1.2.1` (`contract/openapi.yaml` and its anchor `74b9db01…` byte-identical to the `v1.2.1` tag).
Both "next MINOR" windows stay open. Both compose fragments and `tests/test_compose_fragments.py`'s
`_FORAGE_RELEASE_TAG` pin `1.2.2` ahead of the cut (`FORAGE_MEM_LIMIT` default stays `1024m`);
README, `contract/GOVERNANCE.md`, `contract_smoke.py`'s post-cut examples and the kit_tools
current-release rows moved with them, mirroring `def26ad`. `docs/releases.md` carries a
`v1.2.2` entry marked NOT YET PUBLISHED with placeholders `<publication date>`,
`<index digest>`, `<tag commit>`, `<publish run>`, `<candidate smoke record>` and
`<v1.2.2 pin commit>`, filled at the cut. Local gate at prep: 4272 passed, no xfails.
**Still open (AC unchecked):** the per-model real-weights candidate smoke on the exact commit to
be tagged, the cut, the six gates plus layer identity, anonymous pull, and the post-publish 86M
boot. Until the cut, merging puts `main`'s quickstart on an unpublished tag.

### US-003 — benchmark, 2026-10-02

Lab host `thelab-claude` (AMD Ryzen Threadripper 2970WX, 48 threads, 31 GiB; Docker 29.5.2,
cgroup v2; load average below 3.5 throughout, Poppy's containers under 20% of one core each).
Image `forage:cand-4327991` (`sha256:ad36af40c236…`), commit `43279910`, manifest revisions 22M
`11614a15…` / 86M `a8ded8e6…`. `scripts/bench_promptguard.py`, `bench/config.yaml` mounted
read-only, one fresh container per input, loopback port 8021 (8020 and the volume names were
checked free; per-model volumes `forage-smoke-<m>-cache`), `--env-file` holding `HF_TOKEN` only,
`VALKEY_URL` unset, `docker rm -f` after each. Tokenizers copied (dereferenced) from each
verified snapshot to `bench/tokenizer-<m>/` on the host (not committed). Matrix wall-clock
5,234 s, plus an 8,780 s re-run of the three `budget` cells that hit the harness's 300 s default
request timeout (re-run with `--timeout-seconds 1800 --runs 3`; `p95` needs more samples and is
reported only for the 20-run cells).

| Model | CPUs | Mem | 1w cold / p50 / p95 (ms) | budget cold / p50 / p95 (ms) | windows | mem after warm-up | memory.peak | OOM |
|---|---|---|---|---|---|---|---|---|
| 22M | 1 | 1024m | 14,801 / 13,097 / 14,104 | 500,479 / 501,211 / — | 40 | 522–532 MiB | 611,639,296 | no |
| 22M | 4 | 1024m | 3,471 / 3,093 / 3,318 | 105,478 / 104,301 / 107,695 | 40 | 524–525 MiB | 600,821,760 | no |
| 86M | 1 | 1024m | 28,216 / 26,213 / 26,802 | 1,296,133 / 1,316,102 / — | 55 | 653–691 MiB | 855,318,528 | no |
| 86M | 4 | 1024m | 6,586 / 6,407 / 6,690 | 368,975 / 368,490 / — | 55 | 653–656 MiB | 807,354,368 | no |

Every row's `/health` read `promptguard_loaded: true`, `promptguard_model` = the row's model,
`sanitizer_revision` `021378ef…` (22M) / `b5e91fd6…` (86M), `contract_version` `1.3.0`. The 2 vCPU
rows were not run. The first-pass `budget` timeouts were the harness's request timeout, not the
service: no container stopped, none was OOM-killed, and the 86M cells retried at `2048m` timed out
identically, so memory was ruled out. Absolute latencies are host-specific; that a single window
costs 3–26 s here is filed as a follow-up investigation (`roadmap/BACKLOG.md`).

**Resident delta (replaces the provisional 794 MiB):** process `VmRSS` from `/proc/1/status`,
86M minus 22M at equal settings. Idle (after `promptguard_loaded`, before any request, 1 and 4
CPUs): 708,448 − 590,352 kB = **115 MiB**; after the benchmark: 375.6 (1w), 376.8 (budget, 4 CPU)
and **404.3 MiB (budget, 1 CPU)**. `RssFile` is ~113 MB for both models idle but ~458 vs ~207 MB
under load: the weights are memory-mapped and page in on use, and only the embedding rows actually
touched become resident, which is also why every measured delta sits well below the provisional
size-difference bound. Committed value: the largest, rounded up — `405 * MEBIBYTE`.

**Memory limit:** the 86M ran every cell at the default `1024m` without OOM (peak 855 MB,
single in flight), but the memory rule (`512 + 405 + 64 + 384 + 32 MiB` at shipped settings) adds
up to ~1,397 MiB, so the docs recommend `FORAGE_MEM_LIMIT=1536m` for the 86M; the default is
unchanged. The per-classification working set stays the model-independent provisional constant
(out of ruling 6a); follow-up recorded in `roadmap/BACKLOG.md`.

### US-002 — label semantics established, 2026-10-01 (evidence; code lands in the same story)

- **Config read:** the 86M `config.json` carries no `id2label` / `label2id` (`num_labels` unset;
  `DebertaV2ForSequenceClassification`, hidden 768, vocab 251,000) — transformers names the labels
  generically, `LABEL_0` / `LABEL_1`, exactly as for the 22M: **branch (a)**.
- **Probe:** an uncommitted host-side script on the lab host loaded both verified snapshots
  directly (`AutoModelForSequenceClassification.from_pretrained(<snapshot>, local_files_only=True,
  use_safetensors=True)`, offline) and printed both softmax columns; it never touched
  `_PINNED_GENERIC_LABEL_INDICES` or `classify_windows`. Candidates: 935 distinct string literals
  (24–2,000 chars, ≥ 4 words, docstrings excluded) from every `tests/test_*.py` module at `fe06cd8`.
  The 22M selected 20 confident injection probes (10 modules) and 891 confident benign probes
  (32 modules).
- **Result:** index 1 agrees with the 22M on 16/20 injection and 870/891 benign probes (886/911);
  index 0 on 4/20 and 21/891 (25/911). On the **stage-2-corroborated control set** index 1 agrees on
  **11/11** injection and **857/869 (98.6%)** benign; index 0 agrees on 2.7% overall. All 4
  injection-class disagreements are stage-2-clean (22M false positives on config/test text); 9 of
  the 21 benign-class disagreements are stage-2-**blocked** attack fixtures the 22M under-scored and
  the 86M caught; 12 are clean to both and scored high by the 86M (possible 86M false positives on
  test-suite text — spec 5's question, not the pin's).
- **Model card:** names the classes "benign" and "malicious" but states no index order.
- **Verdict:** injection index **1** (owner decision 19). Full table (911 rows: module, function,
  line, sha256, both models' columns, stage-2 verdict; no text) committed at
  `kit_tools/specs/evidence/corpus-86m-label-probe-2026-10-01.json`.
- **Fixture:** `tests/fixtures/promptguard_86m_config/config.json` copied from the verified snapshot;
  sha256 `cd54ac39a1f2…` equals the manifest's `config.json` entry.
- **Code:** candidate commit `4327991` — the 86M pin `(id, a8ded8e6…): 1`, a provisional resident
  delta of `794 * MEBIBYTE` (safetensors size difference, rounded up), and the allowlist entry, in one
  commit; tests and docs as the criteria list. Gates: 4,272 passed, ruff clean, pyright 0 errors.
  `derive_sanitizer_revision({})` (model variables unset) is `021378efee6a…` before and after.
  Scope: `git diff --stat main` over the runtime paths lists only `pipeline/extraction_limits.py` and
  `promptguard/classifier.py`; `model_fetcher.py`'s diff is the `ALLOWED_MODEL_IDS` hunk alone.
- **Real-weights candidate smoke** (lab host, image `forage:cand-4327991` = `sha256:ad36af40c236…`,
  built from the candidate commit **before it was pushed**, transported as a git bundle; `/extract`
  enabled through a read-only mounted copy of `config.yaml`; loopback port 8021; per-model volumes;
  `--env-file` holding `HF_TOKEN` only; containers removed after):
  - **86M**, `FORAGE_MODEL_ID` set, `--memory 2048m`: cold boot healthy in 82 s (download + verify
    through the service's own acquisition), `memory.peak` 1,859,235,840; warm boot healthy in 21 s,
    `memory.peak` 682,188,800. `/health`: `status: healthy`, `degraded_reasons: []`,
    `promptguard_loaded: true`, `promptguard_model: meta-llama/Llama-Prompt-Guard-2-86M`,
    `sanitizer_revision: b5e91fd64727…` (differs by design — the selected model is a hash input).
    `/extract` of a benign upload: 200, `promptguard_state: scanned`. No OOM.
  - **22M**, variable unset, `--memory 1024m`: healthy in 31 s, `promptguard_model` 22M,
    `sanitizer_revision: 021378efee6a…` (unchanged), `/extract` 200 / `scanned`,
    `memory.peak` 850,411,520. No OOM.
  - A first 86M run's `/extract` returned 422 because the smoke script omitted the required
    `filename` form field — a harness error, fixed and re-run; not a service finding.

### US-001 — 86M vendored, 2026-10-01

Run on the lab host `thelab-claude` (Ubuntu 24.04.4, AMD Ryzen Threadripper 2970WX, 48 threads,
31 GiB RAM; Python 3.12.3, uv 0.10.12, Docker 29.5.2 / cgroup v2; `oras` 1.3.4 installed to
`~/.local/bin` against its release sha256). Repo clone at `fe06cd8`, branch
`feat/corpus-86m-enablement`.

- **Isolation posture:** a dedicated non-owner OS user (`claude`, uid 1001). It is in the
  `docker` and `sudo` groups, so it is not privilege-isolated from the host; the owner accepts
  that the host is single-owner and not exposed. Write-scoped credentials existed only for the
  duration of this story.
- **Licence:** both model cards declare `license: other` / `license_name: llama4` and name the
  "Llama 4 Community License"; `NOTICE` names "Llama 4 Community License Agreement". The 86M
  card's `LICENSE` and `USE_POLICY.md` are byte-identical to the 22M's (sha256 `73755cee8866…` and
  `5ae40fe842b8…`); the only restriction-type clause in either card is the same compliance clause.
  Outcome: **match**.
- **Revision:** `a8ded8e697ce7c355e395a0df51f94adb4a2fd27` (the 86M repository head; the 22M
  pin `11614a15…` was confirmed to still be its repository head).
- **Download:** 5 files, 1,131,640,384 bytes (`model.safetensors` 1,115,268,200; `tokenizer.json`
  16,351,353; `tokenizer_config.json` 19,674; `config.json` 871; `special_tokens_map.json` 286).
  `ALLOWED_SUFFIXES` unchanged; no file refused.
- **Manifest diff (scoped):** `meta-llama/Llama-Prompt-Guard-2-86M (added)`, revision
  `None -> a8ded8e6…`, five files; `meta-llama/Llama-Prompt-Guard-2-22M: untouched`.
- **Tarball / selfcheck:** 978,683,163 bytes, 5 members; the extracted tarball verifies against
  the committed manifest.
- **Mirror:** pushed `ghcr.io/washingbearlabs/forage-weights:a8ded8e697ce7c355e395a0df51f94adb4a2fd27`
  (owner-authorized, 2026-10-01); `visibility` reports the package **private**. A first push with a
  fine-grained PAT was refused (`403`, nothing written); a classic PAT with `write:packages` from
  the org-admin account succeeded. **Fresh pull:** byte-identical to the built tarball (sha256
  `70cab69810b1…`) and verifies against the committed manifest.
- **Credentials:** `HF_TOKEN`, `GHCR_USER`, `GHCR_TOKEN`, `GITHUB_TOKEN` lived only in a mode-0600
  file typed at a hidden prompt by the owner and sourced inside single commands; never on a
  command line, never printed. The file was shredded at the end of the story (recorded:
  `before: mode=600`, then no `.creds*` files remain); no GHCR login is stored in the docker or
  oras config on the host. The owner may now revoke the classic PAT.
- **Docs:** `NOTICE` names both ids; `docs/weights.md` states both pins, both `export TOKEN=…`
  blocks are now the one-file recipe, and "Bring your own token" names both gated repositories
  and that access is granted per repository. `ALLOWED_MODEL_IDS` is unchanged (US-002).

## Refinement Notes

### Decisions Made

- 2026-09-24 (owner decision 17): the 86M is a prerequisite inside this epic, not a follow-up and not
  a separate epic; the gates run on the lab host.
- 2026-10-01 (owner decision 19): the literal direction rule ("every probe, at most one
  disagreement per class against the 22M") treated a noisy control as ground truth; it is amended
  to agreement rates on the stage-2-corroborated control set (US-002), and index 1 is accepted on
  that evidence.
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
