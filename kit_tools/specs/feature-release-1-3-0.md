<!-- Template Version: 2.5.0 -->
---
feature: release-1-3-0
status: active
session_ready: true
depends_on: [release-resource-bounds, release-padding-gate]
vision_ref: "T2 hardening follow-through — bound the remaining CPU costs and cut v1.3.0"
type: epic-child
size: L
epic: forage-v1-3-0-release
epic_seq: 3
epic_final: true
created: 2026-10-07
updated: 2026-10-07
---

# Feature Spec: Release 1.3.0 — Close the Two Windows, Finalise Contract 1.4.0, Prepare the Tag

## Overview

The 1.3.0 contract shipped two published compatibility windows, both closing at the "next
MINOR":

1. **Ruling (g), worked example 6.** `retrieve.max_promptguard_chunks` defaults to `0` (no budget),
   with a `retrieve_budget_unset coming_default=256` boot WARNING. The next MINOR flips the default
   to **256**; `0` stays a legal, documented opt-out.
2. **Ruling (l).** Request-validation 422s carry `input`/`ctx`/`url` as the fixed placeholder
   `"[redacted]"`. The next MINOR **drops those keys**. Only `loc`/`msg`/`type` were declared
   properties, so this is the ruling's description-admitted-key carve-out: a **MINOR**, not a
   MAJOR.

Spec 1 US-004 has already bumped `CONTRACT_VERSION` to **1.4.0**. It created a held
`tests/golden/contract_1_4_0.json` and wrote an in-progress 1.4.0 entry. This spec closes both
windows, finalises that entry, and prepares the release tree in the shape of the v1.2.2
`release: prepare` commit (`c933673`).

Version numbers:
- **The image release is v1.3.0.** The tag is the version.
- `pyproject.toml`'s `version` is inert and is not edited.
- No BUMP_VERSION runbook exists; `docs/releases.md` is the procedure.

**Owner gates (not stories):** the real-weights candidate smoke, merge, tag push, watching
`publish`, the anonymous pull, and the healthy-model check on the published image.

## Goals

- **Chunk budget.**
  - An empty `retrieve:` config yields 256 chunks (458,752-character ceiling).
  - No `retrieve_budget_unset` record is logged at boot.
  - `max_promptguard_chunks: 0` disables the pre-check.
- **Sizing docs.** They state a measured per-window classification cost for the 86M at 1 and 4
  CPUs, and the resulting 256-chunk worst-case hold, with the measurement method named.
- **Validation 422s.**
  - Every request-validation 422 item has exactly `loc`, `msg`, `type`.
  - No item's `msg` echoes a per-route request sentinel (tested).
  - The placeholder constants and helper are gone.
- **Contract 1.4.0.**
  - One final 1.4.0 entry, reconciled against the diff of `contract.py` and `openapi.yaml` since
    the pre-epic commit.
  - The held golden and the OpenAPI file and anchor are regenerated.
  - `contract/GOVERNANCE.md` records both windows closed.
- **Release record.**
  - `docs/releases.md` holds a `### v1.3.0` entry marked NOT YET PUBLISHED, with the v1.2.2-style
    placeholders.
  - Every current image pin names `1.3.0`.
  - The owner sequence, including the real-weights smoke, is written down.

## User Stories

### US-001: Flip the `/retrieve` chunk budget default to 256

**Priority:** P1

**Description:** As an operator, I want `/retrieve`'s classifier hold bounded by default at 256
chunks, as ruling (g) announced, with `0` kept as an explicit opt-out. Then one large fetched page
can no longer hold the classification permit for an unbounded time.

**Independent Test:**
- Settings from an empty `retrieve:` block give `max_promptguard_chunks == 256`.
- Booting the app logs no `retrieve_budget_unset`.
- An over-budget page gets `422 content_too_large` / `promptguard_budget`.
- `0` runs no pre-check.

**Implementation Hints:**
- **`pipeline/retrieve_limits.py:25-31`** (unhashed):
  - `RETRIEVE_MAX_PROMPTGUARD_CHUNKS = 256`.
  - Delete `COMING_MAX_PROMPTGUARD_CHUNKS` and rewrite the comment.
  - Lines 95-100: `0` still means `None`.
- **`retrieval_app.py`:**
  - Remove the import (:85) and the warning block (:1717-1725).
  - Update the "Deliberately silent" fallback comment (:1925).
- **Config:** `config.yaml:80-87` and `bench/config.yaml:83-90` set 256, with the comments
  rewritten. The `extraction` block's 64 is a different key; leave it.
- **Tests:**
  - `tests/test_app.py:3313-3315`: empty config gives 256 and a ceiling of 458,752.
  - `test_app.py:3544-3582`: delete the warning parametrization, and assert no
    `retrieve_budget_unset` record for empty, 256 or 0.
  - `tests/test_orchestrator.py:6090` and :6128-6161: the `{}` cases move to explicit `0`, and
    the "absent" case now expects refusal.
- **Operator signal:** find the existing `/metrics` counter and log token that a
  `promptguard_budget` refusal increments (grep `PROMPTGUARD_BUDGET` in `retrieval_app.py` and
  `orchestrator.py`). Name them in `docs/configuration.md` as "how to see the new refusals". If
  none exists, say so in Implementation Notes; do not add one here.
- **Corpus:**
  - Update the comment at `scripts/corpus/outcomes.py:95-96`.
  - Regenerate the baseline. Corpus pages are far under 458,752 characters.
  - If any record moves, record its id and numbers and leave the story failing; guarded mode
    pauses for the owner.
- **Doc sweep:**
  - Grep `coming_default`, `retrieve_budget_unset`, `max_promptguard_chunks: 0`, and phrases
    such as "defaults to 0" or "no budget by default" across `docs/`, `kit_tools/`, `README.md`,
    `CLAUDE.md` and `contract/`.
  - Every *current* statement is rewritten, including `contract/GOVERNANCE.md:499` (ruling (g)
    worked example 6, which gets a dated "closed in 1.4.0" continuation) and the hinted sites
    (`CLAUDE.md:288`, `SECURITY.md:176`, `CODE_ARCH.md:228`, `TROUBLESHOOTING.md:194`,
    `MONITORING.md:212`, `docs/releases.md` :175-176/:232-234/:456-461).
  - Historical rows stay: `GOTCHAS.md:745`, `DECISIONS.md:644`, `SESSION_LOG.md`,
    `docs/bootstrap-notes.md` history, and `contract.py:90-92` (the frozen 1.3.0 entry).
- **No rotation:** only unhashed files change. Record the before and after
  `derive_sanitizer_revision()` values (they should be equal).

**Acceptance Criteria:**
- [ ] `RETRIEVE_MAX_PROMPTGUARD_CHUNKS == 256`, and `COMING_MAX_PROMPTGUARD_CHUNKS` appears
      nowhere in the repo (grep).
- [ ] An empty config gives 256 and a ceiling of 458,752. An explicit `0` gives no pre-check and
      passes no `max_chunks` (tests).
- [ ] No `retrieve_budget_unset` record is logged at boot under empty, 256 or 0 config (test).
- [ ] An over-budget fetched page under the default config is refused `422 content_too_large` /
      `promptguard_budget` (test). The operator-visible counter or log token is named in
      `docs/configuration.md`.
- [ ] The corpus baseline regenerates with zero outcome changes, and `sanitizer_revision` is
      unchanged (values recorded).
- [ ] The doc-sweep greps return no current statement that `0` is the default, outside the listed
      historical sites. `GOVERNANCE.md:499` carries the closure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Measure the 86M per-window cost and rewrite the budget sizing guidance

**Priority:** P2

**Description:** As an operator, I want the budget sizing section to state a measured per-window
classification cost and the resulting 256-chunk hold. Then I can choose a budget that fits my
host's wait limit.

**Independent Test:** The measurement script's output is recorded. The `docs/configuration.md`
sizing section cites it, states the hold at 256 for 1 and 4 CPUs, and names the method and its
caveats.

**Implementation Hints:**
- **Why the docs need replacing:**
  - The sizing section (`docs/configuration.md` :1032 row, :1080-1106) uses a provisional 100 ms
    per window.
  - The bench table (:562-574: "86M 26.2 s / 1,316 s over 55 windows" at 1 vCPU) measures
    end-to-end `/extract` latency in a fresh container, not per-window cost. Read naively it
    implies ~24 s per window, which is implausible. Do **not** derive per-window cost from it.
- **Weights-free measurement:**
  - Inference cost depends on shape, not weights. Instantiate the 86M's architecture with random
    weights.
  - Use the config from the vendored manifest/snapshot if present locally; otherwise the
    documented hyperparameters of `meta-llama/Llama-Prompt-Guard-2-86M` (mDeBERTa-v3-base: 12
    layers, hidden 768). Check `promptguard/classifier.py` for the window length (`MAX_SEQ_LEN`)
    and the batch shape the service uses.
  - Time the forward pass on full-length windows with `torch.set_num_threads(1)` and `(4)`,
    warm, median of 20.
  - Prefer `docker run --cpus 1` / `--cpus 4` on the repo image when available; otherwise host
    threads, and say so.
  - The script lives in the scratchpad, or `scripts/` if worth keeping. **No network** and **no
    gated download**.
- **What the rewrite covers:**
  - The per-window figures and the method.
  - The 256-chunk worst-case hold at 1 and 4 CPUs, compared with the shipped
    `promptguard_wait_seconds` (30.0).
  - The recommendation to lower the budget rather than raise the wait, with worked numbers
    (e.g. 64 and 128).
  - The "Known risk — the shipped default pair" paragraph: the unbounded case is now opt-in.
- If the measured hold at 256 on 1 CPU is far above 30 s, say so plainly and recommend a budget
  for the reference envelope. **Do not change the default.** The owner chose 256 as announced
  (2026-10-07).
- Unhashed docs only, so no rotation.

**Acceptance Criteria:**
- [ ] The per-window forward-pass median at 1 and 4 threads (or CPUs) is recorded in
      Implementation Notes, with the method, machine, window length and command.
- [ ] `docs/configuration.md`'s sizing section uses those figures, states the 256-chunk hold at 1
      and 4 CPUs against the 30 s wait, and gives worked lower budgets. The bench table is
      captioned as end-to-end latency, not per-window cost.
- [ ] No provisional 100 ms figure remains (grep).
- [ ] The default is unchanged.

### US-003: Drop the validation-422 placeholder keys and finalise contract 1.4.0

**Priority:** P1

**Description:** As a consumer, I want request-validation 422 items to carry exactly `loc`, `msg`
and `type`, and the 1.4.0 entry to be final. Then the frozen surface I vendor matches the closed
windows.

**Independent Test:**
- A malformed body on each route yields items with exactly `{loc, msg, type}`, and no `msg`
  contains the request sentinel.
- The held 1.4.0 golden matches the live schema.
- `openapi.yaml` matches its anchor.

**Implementation Hints:**
- **Precondition:** read `feature-release-resource-bounds.md` US-004's Implementation Notes and
  confirm `CONTRACT_VERSION == "1.4.0"` and that the held golden exists. This story regenerates;
  it never bumps again.
- **`retrieval_app.py`:**
  - Delete `_VALIDATION_PLACEHOLDER` and `_VALIDATION_WINDOW_KEYS` (:1160-1161) and the
    `**dict.fromkeys(...)` emission (:2010).
  - Rewrite the `ValidationErrorDetail` docstring (:1120-1126), `_PIPELINE_422_DESCRIPTION`
    (:2064-2069) and `/extract`'s 422 description (:2396-2401). All of these are in the OpenAPI
    document.
  - **Do not touch** the unrelated `"[redacted]"` domain-entry log value at :1673 (tests at
    `test_app.py:2699, :2827`).
- **Ruling (l)'s invariants stay green, unchanged:** no request value in logs, the closed `loc`
  allowlist, the cap of 100, and the two WARNING tokens.
- **`msg` check:** for each route, send a malformed body carrying a distinctive sentinel string
  in every field. Assert that the sentinel appears in no 422 item's `msg`. Also assert it appears
  in no log record (that invariant is already there; extend it to the sentinel).
- **Tests** (`tests/test_contract_errors.py`):
  - Delete `_strip_window_keys` (:174-178, used at :185).
  - `_VALIDATION_ITEM_KEYS` (:588) becomes `{loc, msg, type}`.
  - Delete the placeholder loop (:643-644) and
    `test_validation_window_keys_are_fixed_placeholders` (:790-792).
  - Add an exact-key-set test per route.
  - `tests/test_governance_docs.py:479` requires `'"[redacted]"'` in ruling (l)'s section. Keep
    the history text, or update the test to the closure wording.
- **Finalise the 1.4.0 entry** (`pipeline/contract.py`):
  - Replace the in-progress bullet with one final `* ``1.4.0`` —` bullet.
  - **Reconcile it against the diff, not a fixed list:**
    `git diff <pre-epic>..HEAD -- pipeline/contract.py contract/openapi.yaml`. Every
    wire-visible change appears. At minimum:
    - the dropped placeholder keys;
    - the budget default 256 with `0` as the opt-out;
    - `html_extraction_error` and the large-page refusal;
    - the two `retrieve.html_worker_*` counters.
  - Add one sentence that sanitizer outcomes (the fold BLOCK, ruling (m)) are not contract
    changes.
  - Pass the tense guard (`tests/test_ci_workflow.py:2410-2430`).
- **Golden and export:**
  - Regenerate the held `tests/golden/contract_1_4_0.json`.
  - Run `uv run python -m scripts.export_contract`. It writes `openapi.yaml`, `.sha256` and the
    fixture twin together.
  - Update the doc anchor quotes that `tests/test_contract_export.py` checks.
  - Goldens 1.0.0 through 1.3.0 stay byte-unchanged.
- **Rotation:** `contract.py` moves. Follow the **Rotation record procedure** in
  `feature-release-resource-bounds.md` → Technical Considerations.

**Acceptance Criteria:**
- [ ] On `/search`, `/retrieve` and `/extract`, every request-validation 422 item has exactly
      `loc`, `msg`, `type` (one test per route). `_VALIDATION_PLACEHOLDER`,
      `_VALIDATION_WINDOW_KEYS` and `_strip_window_keys` no longer exist (grep).
- [ ] A per-route sentinel appears in no 422 `msg` and in no log record (tests). Ruling (l)'s
      other invariants pass unchanged, and the `:1673` log value is untouched.
- [ ] The 1.4.0 docstring bullet is final, with no in-progress wording. It covers every change
      in the `contract.py` / `openapi.yaml` diff since the pre-epic commit, and the reconciliation
      is listed in Implementation Notes. The tense guard passes.
- [ ] The held 1.4.0 golden, `openapi.yaml`, its anchor and the fixture twin are regenerated and
      green. The doc anchor quotes are updated, and goldens 1.0.0 through 1.3.0 are
      byte-unchanged.
- [ ] The rotation is recorded per the procedure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-004: Close the windows in GOVERNANCE and write the consumer note

**Priority:** P2

**Description:** As a consumer, I want `contract/GOVERNANCE.md` and the consumer handoff to record
both windows as closed in 1.4.0. Then I know no compatibility window is open and what to change
on my side.

**Independent Test:** `tests/test_governance_docs.py` passes. GOVERNANCE.md's window paragraph
and rulings (g) and (l) record the 1.4.0 closure. `docs/bootstrap-notes.md` carries the consumer
note.

**Implementation Hints:**
- **`contract/GOVERNANCE.md`:**
  - Rewrite :72-75 ("both 'next MINOR' windows stay open") to "closed in 1.4.0, none open".
  - Append dated closure continuations to ruling (g) (if US-001 did not already) and to ruling (l)
    (~:631-703; step 3 executed).
  - Add no new ruling unless something new was decided. If one is added, bump the "Fourteen
    rulings" count (:182; `tests/test_governance_docs.py:97, :443`).
- **Current-version prose:**
  - Grep `1\.3\.0` across `docs/`, `kit_tools/`, `README.md`, `CLAUDE.md` and `contract/`.
  - Rewrite only statements of the *current* contract version (e.g. `README.md:70`, "Contract
    stays `1.3.0`" in `docs/releases.md` Unreleased). History stays.
- **Consumer note** (`docs/bootstrap-notes.md`, for Poppy):
  - The 422 key drop.
  - The 1.4.0 MINOR activation (it activates; no refusal).
  - The new `html_extraction_error` reason under `extraction_failed`.
  - The budget default 256, and what a consumer sees for an over-budget page.
  - **No push to Poppy.** That is an owner action.
- Docs only, so there is no rotation.

**Acceptance Criteria:**
- [ ] `contract/GOVERNANCE.md` states that no compatibility window is open. Rulings (g) and (l)
      carry dated 1.4.0 closure lines, and `tests/test_governance_docs.py` passes.
- [ ] A `1\.3\.0` grep shows no remaining statement that the *current* contract is 1.3.0 outside
      history. The files checked are listed in Implementation Notes.
- [ ] The `docs/bootstrap-notes.md` consumer note covers the four items.
- [ ] Full test suite passes (`uv run pytest`)

### US-005: Prepare the v1.3.0 release tree

**Priority:** P1

**Description:** As the owner, I want the release entry, image pins and tracking docs prepared in
the shape of the v1.2.2 `release: prepare` commit, with the owner sequence written out. Then
merging and the owner gates are all that remain.

**Independent Test:**
- `tests/test_compose_fragments.py` passes with `_FORAGE_RELEASE_TAG = "1.3.0"`.
- `docs/releases.md` has a NOT YET PUBLISHED `### v1.3.0` entry with `contract: 1.4.0` and the
  current anchor.
- A `1\.2\.2` grep finds only allowed historical sites.

**Implementation Hints:**
- **Precedent:** `git show c933673` ("release: prepare v1.2.2"). Pins, the compose-fragment test
  and the "current release" doc rows moved **before** the tag. The quickstart pull fails with
  `manifest unknown` until publish, as it did for v1.2.1 and v1.2.2. The release entry was
  marked NOT YET PUBLISHED, with placeholders for the tag commit, index digest and smoke record.
  Copy that commit's placeholder wording exactly.
- **`docs/releases.md`:**
  - Turn Unreleased (:13-72) into `### v1.3.0 — <date> (NOT YET PUBLISHED)`, with `contract:
    1.4.0`, the current `anchor:`, and placeholder `index digest:` / `tagged commit:` lines.
  - Leave an empty Unreleased heading above it.
  - Cover each item in one or two sentences, with links:
    - the 86M default, the HF 86M grant, and `FORAGE_MEM_LIMIT` 1536m;
    - the bounded decoder;
    - structural scan forms, and `title: null` quarantine;
    - hidden-text removal, and the two re-pinned probes;
    - the worker threshold, `html_extraction_error`, the large-page refusal envelope, the worker
      environment scrub and the spool sweep;
    - `/search` parse off the loop;
    - the 2× + 256 fold BLOCK;
    - budget 256;
    - the 422 key drop;
    - the rotation range from `021378ef…` to the final value, with its count;
    - "Compatibility windows: none open".
- **Pin fan-out** (`1.2.2` → `1.3.0`):
  - `compose/minimal.yml:52, :64, :66`
  - `compose/full.yml:41, :45`
  - `tests/test_compose_fragments.py:78`
  - `README.md:78, :253`
  - the `contract_smoke.py` docstring example (:97-99), changed exactly as `c933673` changed it
    for 1.2.2
  - The `1\.2\.2` grep may then hit only: `docs/releases.md` history entries,
    `docs/bootstrap-notes.md`, `kit_tools/specs/archive/`, `kit_tools/SESSION_LOG.md`,
    `kit_tools/arch/DECISIONS.md` history, and the GOTCHAS rotation table.
- **Tracking:**
  - `kit_tools/SYNOPSIS.md` current state.
  - `kit_tools/roadmap/MILESTONES.md` epic item (stays unchecked until publish).
  - `kit_tools/AGENT_README.md` if it names the current release (`c933673` touched it).
  - **Counts last:** run `uv run pytest --collect-only -q | tail -1` and update
    `kit_tools/testing/TESTING_GUIDE.md` and `CLAUDE.md`'s collected count to match.
- **Findings:** do not edit `kit_tools/AUDIT_FINDINGS.md`. List as ready to resolve:
  2026-10-06-001, 2026-10-04-060, 2026-10-07-002.
- **Owner sequence:** end the Implementation Notes with this sequence, mirroring the v1.2.2
  record (`docs/releases.md` ~:99-110). **No story runs any of it.**
  1. Real-weights candidate smoke on an image built from the merge commit: the 86M at `1536m`,
     `/health` healthy, `promptguard_model` 86M, `/extract` 200 `scanned`, peak memory recorded,
     no OOM.
  2. Merge.
  3. `git switch main && git pull`.
  4. Confirm the six gates are green.
  5. `git tag v1.3.0 && git push origin v1.3.0`.
  6. Watch `publish`.
  7. Anonymous pull of `1.3.0`.
  8. Boot the published image with the 86M and confirm healthy.
  9. Fill `index digest`, `tagged commit` and the smoke record, and remove NOT YET PUBLISHED.

**Acceptance Criteria:**
- [ ] `docs/releases.md` has a `### v1.3.0` NOT YET PUBLISHED entry with `contract: 1.4.0` and the
      current anchor. It covers every listed item and uses `c933673`'s placeholder wording, with
      an empty Unreleased heading above it.
- [ ] Every listed pin names `1.3.0`, and `tests/test_compose_fragments.py` passes. A `1\.2\.2`
      grep hits only the allowed historical sites.
- [ ] SYNOPSIS, MILESTONES and AGENT_README (if applicable) are updated. TESTING_GUIDE and
      `CLAUDE.md` counts equal the `--collect-only` total.
- [ ] Implementation Notes list the findings ready to resolve and the exact nine-step owner
      sequence, including the real-weights smoke. No tag is created or pushed.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

## Edge Cases

- **Explicit `max_promptguard_chunks: 0`:** no warning and no pre-check; it is the documented
  opt-out. (US-001)
- **Operators relying on the old silent default:** they now see refusals above 458,752
  characters. The release notes and the named counter call this out. (US-001, US-005)
- **A corpus record moves with the budget flip:** the story fails with ids recorded, and the
  owner decides. (US-001)
- **The measured 86M hold at 256 is far above 30 s:** the docs say so and recommend a lower
  budget for small hosts. The default stays. (US-002)
- **A pydantic item carrying `ctx` with an exception object:** the key is simply absent now.
  (US-003)
- **A pydantic `msg` that could echo request text:** guarded by the sentinel test. (US-003)
- **A consumer pinned to 1.3.0 activates against 1.4.0:** this is a MINOR, so it activates by
  design. (US-004)
- **The quickstart pull fails until publish:** this is the documented pre-tag state, as for
  v1.2.2. (US-005)

## Out of Scope

- Owner gates: the smoke, merge, tag push, publish, the anonymous pull and post-publish
  verification.
- Editing `pyproject.toml`'s `version`.
- Changing the 256 default after measuring, or opening new windows.
- Poppy-side changes.

## Assumptions

- Ruling (l)'s carve-out makes the key drop a MINOR (GOVERNANCE.md:657-662).
- The 256 flip rides the same MINOR under ruling (g).
- There are no known third-party consumers (owner, 2026-10-06), but the announcement discipline
  is followed anyway.

## Technical Considerations

- `contract.py` is hashed, so US-003 rotates.
- `retrieval_app.py`, `retrieve_limits.py` and the docs are unhashed, so US-001, US-002, US-004
  and US-005 do not rotate.
- The publish job extracts the `CONTRACT_VERSION` docstring entry into the Release body and fails
  if there is none (`ci.yml:1054-1123`;
  `tests/test_ci_workflow.py::TestReleaseContractMapping`).
- Smoke checks `info.version` against `/health.contract_version` and the anchor
  (`ci.yml:623-627, :705-707`).

## Related Documentation

- [contract/GOVERNANCE.md](../../contract/GOVERNANCE.md): rulings (g), (l) and (m); the bump
  procedure at :347-379
- [docs/releases.md](../../docs/releases.md), [docs/configuration.md](../../docs/configuration.md)
- `git show c933673` (the v1.2.2 prepare precedent)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Spec 1 owns the 1.4.0 bump; this spec finalises it.
**Rationale:** The `/retrieve` 422 description is pinned in the frozen 1.3.0 golden, so spec 1's
new reason forces the bump there.
**Source:** `tests/golden/contract_1_3_0.json` ~:292; `retrieval_app.py:1039-1042`.

**Decision:** Pins move before the tag, and the entry is marked NOT YET PUBLISHED.
**Rationale:** This is the v1.2.2 precedent (`c933673`, merged via PR #36).

**Decision:** Per-window cost is measured weights-free.
**Rationale:** The bench table is end-to-end `/extract` latency. Inference cost depends on shape,
not weights, so no gated download is needed.
**Source:** Second-opinion review, 2026-10-07.

**Decision:** The owner sequence includes a real-weights candidate smoke.
**Rationale:** CI has no weights. v1.2.0 published green and was withdrawn
(GOVERNANCE.md:62-64), and this is the first release with the 86M as default.

### Scope Adjustments

- Validation round 1 split the original three stories into five: the budget flip, sizing docs,
  the 422 drop and final 1.4.0, the GOVERNANCE closure and consumer note, and release prep.
- Added: the `msg` sentinel test, entry reconciliation against the diff, and the real-weights
  smoke in the owner sequence.

### Decisions Made

- `pyproject.toml` `version` stays `0.1.0` (inert, documented).

## Clarifications

### Session 2026-10-07
- **Q:** Bump the version to 1.3.0 with this release? **A:** Yes. v1.3.0 is the image; the
  contract is 1.4.0.
- **Q:** Which `max_promptguard_chunks` default ships? **A:** 256 as promised. The docs are
  rewritten with measured figures, and `0` stays the opt-out.
- **Q:** Plan the release as a small spec, then validate and execute? **A:** Yes. Tag and publish
  stay owner steps.
