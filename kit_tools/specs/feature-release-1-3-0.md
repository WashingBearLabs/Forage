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

The 1.3.0 contract shipped two published compatibility windows. Both close at the "next MINOR":

1. **Ruling (g), worked example 6.** `retrieve.max_promptguard_chunks` defaults to `0` (no budget)
   and logs a `retrieve_budget_unset coming_default=256` boot WARNING. The announced next step was
   a default of 256, with `0` staying a legal opt-out.
   - **Owner ruling (2026-10-07):** ship **64**, not 256.
   - Measured with the 86M: one 512-token window costs ~302 ms on 1 thread and ~201 ms on 4.
   - At 256 chunks a single page would hold the classifier ~77 s (1 CPU) or ~51 s (4 CPUs). That is
     past the 30 s `promptguard_wait_seconds`, so concurrent requests would time out into the
     classifier-unavailable outcome.
   - 64 chunks holds ~19 s / ~13 s, inside the wait **for one holder queued ahead**. With two
     holders ahead at 1 CPU (shared `classification_concurrency: 1`) the wait is ~38.6 s, past
     30 s. The character ceiling is
     64 × 1,792 = **114,688**, the same figure the extraction path already uses at 64.
   - `0` remains the opt-out.
2. **Ruling (l).** Request-validation 422s carry `input`, `ctx` and `url` as the fixed placeholder
   `"[redacted]"`. The next MINOR drops those keys. This uses the ruling's
   description-admitted-key carve-out, so it is a **MINOR**.

Spec 1 US-007 has already bumped `CONTRACT_VERSION` to **1.4.0**, created a held
`tests/golden/contract_1_4_0.json`, and written an in-progress 1.4.0 entry. This spec:
- closes both windows;
- adds the missing operator signal for budget refusals;
- finalises the entry;
- prepares the release tree in the shape of the v1.2.2 `release: prepare` commit (`c933673`).

The image release is **v1.3.0**. The tag is the version. `pyproject.toml`'s `version` is inert
and is not edited. No BUMP_VERSION runbook exists; `docs/releases.md` is the procedure.

**Pre-epic commit:** `7fe91c0` (PR #42 merge). Every "since the pre-epic commit" diff in this spec
is `git diff 7fe91c0..HEAD`.

**Owner gates, not stories:**
- the real-weights candidate smoke;
- merge;
- tag push;
- watching `publish`;
- the anonymous pull;
- the healthy-model check on the published image.

## Goals

- **Budget default.** An empty `retrieve:` config yields 64 chunks (114,688-character ceiling).
  - No `retrieve_budget_unset` record is logged.
  - `max_promptguard_chunks: 0` disables the pre-check.
  - A budget refusal increments a `/metrics` counter.
- **Sizing docs.** They state the measured per-window cost (method named, host-thread figures
  labelled as lower bounds unless taken under `docker --cpus`) and the hold at 64 and 256 against
  the 30 s wait. They also state what a waiting request gets when its wait expires.
- **Validation 422s.**
  - Every request-validation 422 item has exactly `loc`, `msg` and `type`.
  - No item's `msg` echoes a per-route request sentinel.
  - The placeholder constants and helper are gone.
- **Contract 1.4.0.**
  - One final 1.4.0 entry, reconciled against `git diff 7fe91c0..HEAD -- pipeline/contract.py
    contract/openapi.yaml`.
  - The held golden, the OpenAPI file and the anchor are regenerated.
  - `contract/GOVERNANCE.md` records both windows as closed, including the 64 ruling.
- **Release record.**
  - `docs/releases.md` holds a `### v1.3.0` NOT YET PUBLISHED entry. Its anchor and rotation value
    equal `contract/openapi.yaml.sha256` and `derive_sanitizer_revision()` at the final commit.
  - Every current image pin names `1.3.0`.

## User Stories

### US-001: Flip the `/retrieve` chunk budget default to 64

**Priority:** P1

**Description:** As an operator, I want `/retrieve`'s classifier hold bounded by default at 64
chunks, with `0` kept as an explicit opt-out. Then one large fetched page can no longer hold the
classification permit past the wait other requests are given.

**Independent Test:**
- Settings from an empty `retrieve:` block give `max_promptguard_chunks == 64`.
- Boot logs no `retrieve_budget_unset`.
- An over-budget page gets `422 content_too_large` / `promptguard_budget`.
- `0` runs no pre-check.

**Implementation Hints:**
- **`pipeline/retrieve_limits.py:25-31`** (unhashed):
  - Set `RETRIEVE_MAX_PROMPTGUARD_CHUNKS = 64`.
  - Delete `COMING_MAX_PROMPTGUARD_CHUNKS`.
  - Rewrite the comment: default 64, owner ruling 2026-10-07, measured; `0` is the opt-out.
  - Lines 95-100: `0` still means `None`.
- **`retrieval_app.py`:**
  - Remove the import (:85) and the warning block (:1717-1725).
  - Update the "Deliberately silent" fallback comment (:1925).
- **Config files:** `config.yaml:80-87` and `bench/config.yaml:83-90` set `64`, with the comment
  rewritten.
  - The `extraction` block's `64` is a different key; leave it.
  - Keep the shipped-defaults test (`tests/test_contract_metrics.py` ~:800-850) green.
- **Tests:**
  - `tests/test_app.py:3313-3315`: an empty config gives 64 and a ceiling of 114,688.
  - `test_app.py:3544-3582`: delete the warning parametrization. Assert no
    `retrieve_budget_unset` record for empty, 64 or 0.
  - `tests/test_orchestrator.py:6090` and :6128-6161: the `{}` cases move to "explicit 0". The
    "absent" case now expects refusal.
- **Do not touch hashed files here.**
  - The stale comment at `pipeline/orchestrator.py` ~:663 ("at the coming default of 256 chunks")
    is in a hashed file. It is fixed in US-003, which rotates anyway. Editing it here would break
    this story's "revision unchanged" criterion.
- **Corpus:**
  - Update the comment at `scripts/corpus/outcomes.py:95-96`.
  - Regenerate the baseline.
  - If any record moves (a corpus page over 114,688 characters), record the id and numbers. Leave
    the story failing, because the owner decides.
- **Doc sweep:** grep `coming_default`, `coming default`, `retrieve_budget_unset`,
  `max_promptguard_chunks: 0`, `defaults to 0`, `no budget by default` and `256` (near
  "chunk") across `docs/`, `kit_tools/`, `README.md`, `CLAUDE.md` and `contract/`.
  - Rewrite every current statement, including `contract/GOVERNANCE.md:499`. Ruling (g) gets a
    dated continuation: "closed in 1.4.0 at 64, owner ruling 2026-10-07, measured; 256 was
    announced".
  - Also rewrite the hinted sites: `CLAUDE.md:288`, `SECURITY.md:176`, `CODE_ARCH.md:228`,
    `TROUBLESHOOTING.md:194`, `MONITORING.md:212`, and `docs/releases.md`
    :175-176/:232-234/:456-461.
  - Allowed historical hits: `GOTCHAS.md:745`, `DECISIONS.md:644`, `SESSION_LOG.md`,
    `docs/bootstrap-notes.md` history, `contract.py:90-92` (the frozen 1.3.0 entry), and archived
    specs.
  - Record the grep commands and remaining hits in Implementation Notes.
- **No rotation:** record `derive_sanitizer_revision()` before and after (equal).

**Acceptance Criteria:**
- [ ] `RETRIEVE_MAX_PROMPTGUARD_CHUNKS == 64`, and `COMING_MAX_PROMPTGUARD_CHUNKS` appears nowhere
      (grep).
- [ ] An empty config gives 64 and a ceiling of 114,688. An explicit `0` gives no pre-check and
      no `max_chunks` (tests).
- [ ] No `retrieve_budget_unset` record under empty, 64 or 0 config (test).
- [ ] An over-budget fetched page under the default config is refused `422 content_too_large` /
      `promptguard_budget` (test).
- [ ] The corpus baseline regenerates with zero outcome changes, and `sanitizer_revision` is
      unchanged (values recorded).
- [ ] The doc-sweep greps find no current statement of a `0` or `256` default outside the allowed
      historical list. The commands and hits are recorded. `GOVERNANCE.md:499` carries the
      dated 64 closure.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Measure the 86M per-window cost and rewrite the budget sizing guidance

**Priority:** P2

**Description:** As an operator, I want the budget sizing section to state a measured
per-window classification cost, the resulting holds, and what happens to a waiting request when
its wait expires, so that I can choose a budget and a fail-closed policy that fit my host.

**Independent Test:** The measurement output is recorded. The `docs/configuration.md` sizing
section cites it, states the holds at 64 and 256 against the 30 s wait, and names the waiter
outcome.

**Implementation Hints:**
- **Why the current figures go.**
  - The bench table (`docs/configuration.md` :562-574, "86M 26.2 s / 1,316 s over 55 windows")
    is end-to-end `/extract` latency in a fresh container, not per-window cost. Do not derive
    per-window cost from it; caption it as end-to-end.
  - The provisional "100 ms per window" (:1080-1106) goes.
- **Weights-free measurement** (validation prototyped it; ~302 ms at 1 thread and ~201 ms at
  4 threads on an M-series Mac, torch 2.14 / transformers 5.16, offline):
  - Use `transformers.DebertaV2ForSequenceClassification(DebertaV2Config(...))` with random init.
  - **Config source of truth:** a local snapshot `config.json` for
    `meta-llama/Llama-Prompt-Guard-2-86M`, if one exists under the HF cache or the weights
    manifest path. Otherwise use the mDeBERTa-v3-base hyperparameters, and record each in
    Implementation Notes:
    - `hidden_size=768`, `num_hidden_layers=12`, `num_attention_heads=12`,
      `intermediate_size=3072`, `vocab_size=251000`;
    - `relative_attention=True`, `position_buckets=256`, `max_relative_positions=-1`,
      `pos_att_type=["p2c","c2p"]`, `norm_rel_ebd="layer_norm"`, `share_att_key=True`;
    - `position_biased_input=False`, `num_labels=2`.
  - **Input:** `MAX_SEQ_LEN` (512, `promptguard/classifier.py:42`) random token ids. Use the batch
    size the classifier actually uses (read `promptguard/classifier.py` around :303) and record
    it.
  - **Timing:** warm, median of 20, with `torch.set_num_threads(1)` and `(4)`.
  - **1-CPU figure:** take it under `docker run --cpus 1` on the repo image when Docker is
    available. Otherwise label host-thread figures as **lower bounds** on hold time.
  - The script lives in the scratchpad, or `scripts/` if worth keeping. **No network, no gated
    download.**
- **The rewrite states:**
  - the per-window figures, the method and its caveats;
  - the hold at 64 and at 256, at 1 and 4 CPUs, against `promptguard_wait_seconds` (30.0), for
    **k = 1, 2, 3 holders queued ahead**. The permit is shared by `/extract`, `/retrieve` and
    `/search` at `classification_concurrency: 1`. Say plainly that the 30 s fit is per single
    holder.
  - the budget that fits 30 s at each CPU count;
  - **what a waiter gets when its wait expires** (`unavailable_allowed`, served unclassified, unless
    `promptguard_fail_closed` refuses it; check the exact behaviour in
    `stage3_promptguard.unavailable_result`), and the counters that show it
    (`retrieve.classification_wait_timeouts`, `search.classification_wait_timeouts`);
  - the "Known risk" paragraph rewritten. At 64 the shipped pair fits the wait on the reference
    envelope. `0` (opt-out) and values over the fitting budget reopen the risk.
- Docs only, so no rotation.

**Acceptance Criteria:**
- [ ] The per-window median at 1 and 4 threads (or CPUs) is recorded in Implementation Notes,
      with the config source, every hyperparameter used, batch size, window length, machine and
      command.
- [ ] The `docs/configuration.md` sizing section gives the measured figures, labelled as lower
      bounds where they are host-thread figures. It states the hold at 64 and 256 for 1 and 4
      CPUs against 30 s, the fitting budget per CPU count, and the waiter outcome and its
      counters. The bench table is captioned as end-to-end latency.
- [ ] No provisional "100 ms" figure remains (grep). The default is unchanged by this story.

### US-003: Drop the validation-422 placeholder keys, add the budget-refusal counter, finalise contract 1.4.0

**Priority:** P1

**Description:** As a consumer, I want request-validation 422 items to carry exactly `loc`, `msg`
and `type`, a counter for budget refusals, and a final 1.4.0 entry, so that the frozen surface I
vendor matches the closed windows and the new refusals are visible.

**Independent Test:**
- A malformed body on each route yields items with exactly `{loc, msg, type}`, and no `msg`
  contains the request sentinel.
- A budget refusal increments `retrieve.promptguard_budget_refusals`.
- The held 1.4.0 golden matches, and `openapi.yaml` matches its anchor.

**Implementation Hints:**
- **Precondition:** confirm from `feature-release-resource-bounds.md` US-007's Implementation
  Notes that `CONTRACT_VERSION == "1.4.0"` and the held golden exists. This story regenerates;
  it never bumps.
- **422 key drop** (`retrieval_app.py`):
  - Delete `_VALIDATION_PLACEHOLDER` and `_VALIDATION_WINDOW_KEYS` (:1160-1161), and the
    `**dict.fromkeys(...)` emission (:2010).
  - Rewrite the `ValidationErrorDetail` docstring (:1120-1126), `_PIPELINE_422_DESCRIPTION`
    (:2064-2069) and `/extract`'s 422 description (:2396-2401).
  - **Do not touch** the unrelated `"[redacted]"` at :1673 (tests at `test_app.py:2699, :2827`).
- **Ruling (l)'s invariants stay green:** no request value in logs, the closed `loc` allowlist,
  the cap of 100, and the two WARNING tokens.
- **`msg` check.** `tests/test_contract_errors.py` already has `_VALIDATION_MARKER` (:587). It is
  injected into every request field per route (:660-697) and checked against the response and the
  WARNING logs (:640, :831-866).
  - **Extend it rather than adding a parallel suite.** Assert per item that the marker is not in
    `msg`, and add a caplog check at all levels.
  - Add a comment that a validator interpolating its input into `msg` would fail this. That is
    the guard.
- **Test edits** (`tests/test_contract_errors.py`):
  - Delete `_strip_window_keys` (:174-178, used at :185).
  - `_VALIDATION_ITEM_KEYS` (:588) becomes `{loc, msg, type}`.
  - Delete the placeholder loop (:643-644) and `test_validation_window_keys_are_fixed_placeholders`
    (:790-792).
  - Add an exact-key-set test per route.
  - `tests/test_governance_docs.py:479` (`'"[redacted]"'` in ruling (l)): keep the history text,
    or update it to the closure wording.
- **Budget-refusal counter.** No dedicated signal exists today; the three refusal sites
  (`orchestrator.py` ~:604, ~:669, ~:733) return only the 422 body. Add
  `retrieve.promptguard_budget_refusals`, counted **at the handler**:
  `retrieval_app.py` ~:2349-2351 already catches every retrieve `PipelineError` to call
  `record_error(exc.error)`. Add `if exc.reason == contract.PROMPTGUARD_BUDGET` there.
  - Add the counter to `retrieval_app.RetrieveMetrics` (:1243) and `RetrieveMetricsResponse` (:784).
  - Name it in the 1.4.0 entry (enforced by spec 1 US-007's metric-addition test) and in
    `docs/configuration.md`.
  - The orchestrator rotation still happens, because of the stale-comment fix below.
- **The three new counters** (the list US-004 and US-005 refer to):
  - `retrieve.html_worker_spawns` (spec 1)
  - `retrieve.html_worker_refusals` (spec 1)
  - `retrieve.promptguard_budget_refusals` (this story)
- **Stale comment.** Fix the `orchestrator.py` ~:663 "coming default of 256" comment.
- **Finalise the 1.4.0 entry** (`pipeline/contract.py`): one final `* ``1.4.0`` —` bullet,
  replacing the in-progress text.
  - **Reconcile against the diff:** `git diff 7fe91c0..HEAD -- pipeline/contract.py
    contract/openapi.yaml`. Every wire-visible change appears, at minimum:
    - the dropped placeholder keys;
    - the budget default 64, with `0` as the opt-out and 256 as announced;
    - `html_extraction_error` and the large-page refusal;
    - the three `retrieve.*` counters.
  - Add one sentence saying sanitizer outcomes (the fold BLOCK, ruling (m)) are not contract
    changes.
  - It must pass the tense guard (`tests/test_ci_workflow.py:2410-2430`).
- **Golden and export.**
  - Regenerate the held `tests/golden/contract_1_4_0.json`.
  - Run `uv run python -m scripts.export_contract`.
  - Update the doc anchor quotes that `tests/test_contract_export.py` checks.
  - Goldens 1.0.0–1.3.0 stay byte-unchanged.
- **Rotation:** `contract.py` and `orchestrator.py` move. Follow the **Rotation record
  procedure** in `feature-release-resource-bounds.md` → Technical Considerations: each file
  reverted alone, plus a both-reverted control.

**Acceptance Criteria:**
- [ ] On `/search`, `/retrieve` and `/extract`, every request-validation 422 item has exactly
      `loc`, `msg`, `type` (one test per route). `_VALIDATION_PLACEHOLDER`,
      `_VALIDATION_WINDOW_KEYS` and `_strip_window_keys` no longer exist (grep).
- [ ] The existing `_VALIDATION_MARKER` parametrization asserts the marker is in no 422 item's
      `msg` and in no log record at any level (tests).
- [ ] Ruling (l)'s other invariants pass unchanged.
- [ ] The `:1673` value is untouched.
- [ ] `retrieve.promptguard_budget_refusals` increments on a budget refusal from each of the
      three refusal sites (tests through the handler), appears on `/metrics`, and is named in
      `docs/configuration.md`.
- [ ] The `orchestrator.py` "coming default" comment is gone.
- [ ] The 1.4.0 bullet is final.
  - It covers every change in `git diff 7fe91c0..HEAD -- pipeline/contract.py
    contract/openapi.yaml`, with the reconciliation listed in Implementation Notes.
  - The tense guard and the 1.4.0 metric-addition test pass.
- [ ] The held 1.4.0 golden, `openapi.yaml`, its anchor and the fixture twin are regenerated and
      green.
- [ ] The anchor quotes are updated.
- [ ] Goldens 1.0.0–1.3.0 are byte-unchanged.
- [ ] The rotation is recorded per the procedure (two files).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-004: Close the windows in GOVERNANCE and write the consumer note

**Priority:** P2

**Description:** As a consumer, I want `contract/GOVERNANCE.md` and the consumer handoff to record
both windows as closed in 1.4.0, so that I know none is open and what to change on my side.

**Independent Test:** `tests/test_governance_docs.py` passes. GOVERNANCE.md's window paragraph
and rulings (g) and (l) record the closure. `docs/bootstrap-notes.md` carries the consumer note.

**Implementation Hints:**
- **`contract/GOVERNANCE.md`:**
  - Rewrite :72-75 ("both 'next MINOR' windows stay open") to say the windows are closed in 1.4.0
    and none is open.
  - Rewrite :71, the current image-to-contract mapping sentence ("image v1.2.2 … published"), to
    say v1.3.0 is prepared and maps to 1.4.0.
  - Append the ruling (l) closure (~:631-703, step 3 executed). Ruling (g)'s closure line comes
    from US-001.
  - No new ruling unless something new was decided. The 64 default is recorded as a dated
    continuation of (g), not a new ruling. If a ruling is added anyway, bump the "Fourteen
    rulings" count (:182; `tests/test_governance_docs.py:97, :443`).
- **Current-version prose:** grep `1\.3\.0` across `docs/`, `kit_tools/`, `README.md`,
  `CLAUDE.md` and `contract/`.
  - Rewrite only statements of the *current* contract version: `CLAUDE.md` invariant 4,
    `README.md:70` and `:293`, and the `docs/releases.md` Unreleased "Contract stays `1.3.0`".
    History stays.
  - Record the files checked.
- **Consumer note** (`docs/bootstrap-notes.md`, for Poppy):
  - the 422 key drop;
  - 1.4.0 MINOR activation (activates, no refusal);
  - `html_extraction_error`;
  - the budget default 64 and what an over-budget page returns;
  - the three new counters, as listed in US-003.
  - No push to Poppy.
- Docs only, so no rotation.

**Acceptance Criteria:**
- [ ] `contract/GOVERNANCE.md` states that no compatibility window is open.
- [ ] GOVERNANCE.md :71 names v1.3.0 and 1.4.0.
- [ ] Rulings (g) and (l) carry dated closure lines.
- [ ] `tests/test_governance_docs.py` passes.
- [ ] A `1\.3\.0` grep shows no remaining current-version statement outside history, with the
      files checked listed.
- [ ] The consumer note covers the five items.
- [ ] Full test suite passes (`uv run pytest`)

### US-005: Prepare the v1.3.0 release tree

**Priority:** P1

**Description:** As the owner, I want the release entry, image pins and tracking docs prepared in
the shape of the v1.2.2 `release: prepare` commit, with the owner sequence written out. Then
merging and the owner gates are all that remain.

**Independent Test:**
- `tests/test_compose_fragments.py` passes with `_FORAGE_RELEASE_TAG = "1.3.0"`.
- The `docs/releases.md` v1.3.0 entry's anchor equals `contract/openapi.yaml.sha256`, and its
  final revision equals `derive_sanitizer_revision()`.
- A `1\.2\.2` grep hits only the allowed sites.

**Implementation Hints:**
- **Runs last.** It reads the final anchor and revision after every other story has landed.
- **Precedent:** `git show c933673`. Pins, the compose-fragment test and the "current release"
  rows moved before the tag, and the quickstart pull fails with `manifest unknown` until publish.
  The entry was marked NOT YET PUBLISHED with placeholders; copy that wording exactly.
- **`docs/releases.md`:**
  - Unreleased (:13-72) becomes `### v1.3.0 — <date> (NOT YET PUBLISHED)`, with:
    - `contract: 1.4.0`;
    - `anchor:` set to the content of `contract/openapi.yaml.sha256`;
    - placeholder `index digest:` and `tagged commit:` lines.
  - Leave an empty Unreleased heading above it.
  - One or two sentences each, with links:
    1. The 86M default, the HF 86M grant and `FORAGE_MEM_LIMIT` 1536m.
    2. The bounded decoder.
    3. Structural scan forms and `title: null` quarantine.
    4. Hidden-text removal and the two re-pinned probes.
    5. The linear stage 1.
    6. The worker threshold, `html_extraction_error` and the large-page envelope.
    7. Worker isolation (allowlisted env, non-dumpable parent) and the spool sweep.
    8. `/search` parse off the loop.
    9. The fold BLOCK at `max(2n, n+256)`.
    10. Budget default 64 (256 was announced; why it changed).
    11. The 422 key drop.
    12. The three counters, named as listed in US-003.
    13. The rotation range from `021378ef…` to the final value, with its count.
    14. "Compatibility windows: none open".
- **Pin fan-out** (`1.2.2` → `1.3.0`), driven by a full classification rather than a hand list:
  1. Run `git show --stat c933673` to list every file the v1.2.2 prepare commit touched. Each is a
     candidate.
  2. Run `git grep -n '1\.2\.2'` and classify **every** hit as *rewrite* (a current-release
     statement) or *history*. Record the table in Implementation Notes.
  3. Known rewrites, not exhaustive:
     - `compose/minimal.yml:52, :64, :66` and `compose/full.yml:41, :45`
     - `tests/test_compose_fragments.py:78`
     - `contract_smoke.py:66` and the docstring example (:97-99)
     - `README.md:78, :253`
     - `kit_tools/roadmap/MILESTONES.md:13` ("Current release")
     - `kit_tools/SYNOPSIS.md:30, :36`
     - `kit_tools/AGENT_README.md:74, :79`
     - `kit_tools/arch/INFRA_ARCH.md` (~:146, :160-161, :201)
     - `kit_tools/arch/SERVICE_MAP.md` (~:195, :395-398)
     - `kit_tools/docs/CI_CD.md` (~:352-354, :523-525, :546)
     - `kit_tools/docs/DEPLOYMENT.md` (~:89-272, including the `TAG=1.2.2` examples)
     - `kit_tools/docs/LOCAL_DEV.md` (~:233-234)
     - `kit_tools/docs/TROUBLESHOOTING.md` (~:880-889)
  4. Known history, to keep:
     - `docs/releases.md` entries and `docs/bootstrap-notes.md`
     - archived specs and `kit_tools/SESSION_LOG.md`
     - `DECISIONS.md` history and the GOTCHAS table and header (:5)
     - `kit_tools/PRODUCT_VISION.md:105` and `kit_tools/roadmap/BACKLOG.md:39`
     - MILESTONES history rows (:75, :115) and GOVERNANCE history lines
     - `docs/weights.md:4` if historical
     - the unarchived injection-corpus epic wrapper
  - `contract/GOVERNANCE.md:71` is rewritten by US-004.
- **Tracking:**
  - Update SYNOPSIS and the MILESTONES epic item (unchecked until publish).
  - **Counts last:** run `uv run pytest --collect-only -q | tail -1` and update
    `kit_tools/testing/TESTING_GUIDE.md` and `CLAUDE.md` to match.
- **Findings:** do not edit `kit_tools/AUDIT_FINDINGS.md`. List these as ready to resolve:
  2026-10-06-001, 2026-10-04-060 (dismissed-as-residual, now fixed: re-status it), and
  2026-10-07-002.
- **Owner sequence.** End the Implementation Notes with these steps, mirroring the v1.2.2 record
  (`docs/releases.md` ~:99-110). **No story runs any of it.**
  1. Real-weights candidate smoke on an image built from the merge commit: the 86M at `1536m`,
     `/health` healthy, `promptguard_model` 86M, `/extract` 200 `scanned`, `/retrieve` of a
     large page returning `html_extraction_error`. Also check:
     - an over-budget page (more than 114,688 extracted characters, under the worker threshold)
       returns `promptguard_budget` and increments `retrieve.promptguard_budget_refusals`;
     - an at-budget page classifies within the wait, with its latency recorded;
     - peak memory is recorded, with no OOM.
  2. Merge.
  3. `git switch main && git pull`.
  4. Confirm the six gates are green.
  5. `git tag v1.3.0 && git push origin v1.3.0`.
  6. Watch `publish`.
  7. Anonymous pull of `1.3.0`.
  8. Boot the published image with the 86M and confirm it is healthy.
  9. Fill `index digest`, `tagged commit` and the smoke record, and remove NOT YET PUBLISHED.

**Acceptance Criteria:**
- [ ] `docs/releases.md` has a `### v1.3.0` NOT YET PUBLISHED entry with an empty Unreleased
      heading above it, using `c933673`'s placeholder wording.
- [ ] The entry's `contract:` is 1.4.0. Its anchor equals `contract/openapi.yaml.sha256` and its
      final revision equals `derive_sanitizer_revision()` at the final commit (checked by a
      command recorded in Implementation Notes).
- [ ] The entry covers each of the 14 listed items, each with a link where one exists.
- [ ] Every `git grep -n '1\.2\.2'` hit is classified in a recorded table as rewritten or history,
      and every rewrite now names `1.3.0`. Every file in `git show --stat c933673` is accounted
      for. `tests/test_compose_fragments.py` passes.
- [ ] SYNOPSIS and MILESTONES are updated. TESTING_GUIDE and `CLAUDE.md` counts equal the
      `--collect-only` total.
- [ ] Implementation Notes list the findings ready to resolve and the exact nine-step owner
      sequence. No tag is created or pushed.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass

## Edge Cases

- **Explicit `max_promptguard_chunks: 0`:** no warning and no pre-check. This is the documented
  opt-out. (US-001)
- **Operators relying on the old silent default:** they now see refusals above 114,688
  characters. The release notes and the new counter call this out. (US-001, US-003, US-005)
- **A corpus record moves with the flip:** the story fails with the ids recorded, and the owner
  decides. (US-001)
- **A waiter's permit wait expires:** the outcome is documented (fail-open by default), with its
  counters. (US-002)
- **A pydantic `msg` that could echo request text:** guarded by the sentinel test. (US-003)
- **A consumer pinned to 1.3.0 activates against 1.4.0:** MINOR, so it activates by design.
  (US-004)
- **The quickstart pull fails until publish:** the documented pre-tag state, as for v1.2.2.
  (US-005)

## Out of Scope

- Owner gates: the smoke, merge, tag push, publish, the anonymous pull and verification.
- `pyproject.toml` `version`.
- Changing `promptguard_wait_seconds` or the fail-closed default.
- Poppy-side changes.

## Assumptions

- Ruling (l)'s carve-out makes the key drop a MINOR (GOVERNANCE.md:657-662).
- Shipping 64 instead of the announced 256 tightens a default. It is not a wire-shape change, so
  it rides the same MINOR under ruling (g), with a dated continuation recording the owner's
  measured reason.
- There are no known third-party consumers (owner, 2026-10-06). The announcement discipline is
  followed anyway.

## Technical Considerations

- `contract.py` and `orchestrator.py` are hashed, so US-003 rotates once (two files).
  `retrieval_app.py`, `retrieve_limits.py` and the docs are unhashed. US-001, US-002, US-004 and
  US-005 do not rotate.
- The publish job extracts the `CONTRACT_VERSION` docstring entry into the Release body
  (`ci.yml:1054-1123`; `tests/test_ci_workflow.py::TestReleaseContractMapping`).
- Smoke checks `info.version` against `/health.contract_version` and the anchor
  (`ci.yml:623-627, :705-707`).

## Related Documentation

- [contract/GOVERNANCE.md](../../contract/GOVERNANCE.md): rulings (g), (l) and (m), and the bump
  procedure at :347-379
- [docs/releases.md](../../docs/releases.md), [docs/configuration.md](../../docs/configuration.md)
- `git show c933673` (the v1.2.2 prepare precedent)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Default 64, not the announced 256.
**Rationale:** The measured per-window cost (~302 ms at 1 thread, ~201 ms at 4) puts 256 chunks at
~77 s / ~51 s, past the 30 s wait, which turns concurrent requests fail-open. 64 fits everywhere.
**Source:** Round-2 second-opinion prototype; owner ruling, 2026-10-07.

**Decision:** Add `retrieve.promptguard_budget_refusals`.
**Rationale:** No signal exists today; the refusal sites return only the 422 body. Flipping a
default on without an operator signal contradicts the loud-degradation rule.
**Source:** Round-2 codebase-fit verification (`orchestrator.py` ~:604/:669/:733).

**Decision:** Spec 1 owns the 1.4.0 bump; this spec finalises it.
**Rationale:** The `/retrieve` 422 description is pinned in the frozen 1.3.0 golden.

**Decision:** Pins move before the tag, and the entry is marked NOT YET PUBLISHED (`c933673`).

**Decision:** The owner sequence includes a real-weights candidate smoke.
**Rationale:** CI has no weights. v1.2.0 was withdrawn after a green publish. This is the first
release with the 86M as default.

### Scope Adjustments

- Round 3 made these changes:
  - the "64 fits" claim is scoped to a single holder, with a k-holder table;
  - the counter moved to the handler;
  - the existing validation marker is reused;
  - the three counters are named;
  - the `1.2.2` sweep is driven by a full grep classification;
  - the over-budget smoke check was added.
- Round 1 split the spec into five stories.
- Round 2 made these changes:
  - the default changed to 64;
  - the budget-refusal counter and the hashed-comment fix moved into US-003;
  - the measurement config is pinned, and the waiter outcome is documented;
  - the `1\.2\.2` allowlist was extended, and the current-release rows are named;
  - a final-anchor and final-revision equality check was added;
  - the pre-epic commit is named (`7fe91c0`).

### Decisions Made

- `pyproject.toml` `version` stays `0.1.0` (inert).

## Clarifications

### Session 2026-10-07
- **Q:** Bump the version to 1.3.0 with this release? **A:** Yes. v1.3.0 is the image; the
  contract is 1.4.0.
- **Q:** Which default ships, first round? **A:** 256 as promised.
- **Q:** Which default ships after measurement showed 256 holds ~51–77 s against a 30 s wait?
  **A:** 64 chunks.
- **Q:** Plan the release as a small spec, then validate and execute? **A:** Yes. Tag and publish
  stay owner steps.
