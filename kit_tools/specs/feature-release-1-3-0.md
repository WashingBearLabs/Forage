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

# Feature Spec: Release 1.3.0 — Close the Two Windows, Contract 1.4.0, Ready to Tag

## Overview

The 1.3.0 contract shipped with two published compatibility windows. Both promised to close at
the "next MINOR":

1. **Ruling (g), worked example 6.** `retrieve.max_promptguard_chunks` defaults to `0` (no budget)
   for one release, with a `retrieve_budget_unset coming_default=256` boot WARNING. The next MINOR
   flips the default to **256**, and `0` stays a legal, documented opt-out.
2. **Ruling (l).** Request-validation 422s carry `input`, `ctx` and `url` as the fixed placeholder
   `"[redacted]"` for one release. The next MINOR **drops those keys**. Because only
   `loc`/`msg`/`type` were declared properties, this is the ruling's description-admitted-key
   carve-out: a **MINOR**, not a MAJOR.

Closing them, together with the new `/retrieve` HTML refusal reason from spec 1, makes contract
**1.4.0**. The image release is **v1.3.0**. The tag is the version: `pyproject.toml`'s `version`
is inert metadata and is not edited, and no BUMP_VERSION runbook exists (`docs/releases.md` is the
procedure).

**Owner gates (not stories):** merging the epic PR, pushing `v1.3.0`, watching `publish` go
green, and verifying an anonymous pull.

## Goals

- **Budget default.** An empty `retrieve:` config yields a 256-chunk budget (458,752-character
  ceiling). The boot log carries no `retrieve_budget_unset` record. `max_promptguard_chunks: 0`
  still disables the pre-check.
- **422 placeholders.** A request-validation 422 item has exactly the keys `loc`, `msg`, `type`,
  across every route (tested). The `"[redacted]"` placeholder and the window constants are gone
  from code and tests.
- **Contract.** `CONTRACT_VERSION == "1.4.0"`, with one final docstring entry listing every 1.4.0
  change. `tests/golden/contract_1_4_0.json` exists; the 1.3.0 golden is byte-unchanged. The
  OpenAPI file and its anchor are regenerated, and `contract/GOVERNANCE.md` records both windows
  as closed.
- **Release notes.** `docs/releases.md` holds a complete `### v1.3.0` entry. Every image pin
  (compose fragments, the compose test, contract smoke, README) names `1.3.0`. The full gate set
  is green on the PR.

## User Stories

### US-001: Flip the `/retrieve` PromptGuard chunk budget default to 256

**Priority:** P1

**Description:** As an operator, I want `/retrieve`'s classifier hold bounded by default at 256
chunks, as ruling (g) announced, with `0` kept as an explicit opt-out, so that one large fetched
page can no longer hold the classification permit for an unbounded time.

**Independent Test:** Load settings from an empty `retrieve:` block and get
`max_promptguard_chunks == 256`. Boot the app and assert no `retrieve_budget_unset` record.
Request an over-budget page and get `422 content_too_large` / `promptguard_budget`. Set the key to
`0` and see no pre-check.

**Implementation Hints:**
- **`pipeline/retrieve_limits.py:25-31`** (unhashed): `RETRIEVE_MAX_PROMPTGUARD_CHUNKS = 256`.
  - Delete `COMING_MAX_PROMPTGUARD_CHUNKS` and rewrite the comment: the default is 256, and `0`
    is the documented opt-out.
  - Lines 95-100: `0` still means `None`.
  - `_MAX_RETRIEVE_PROMPTGUARD_CHUNKS = 1024` is unchanged.
- **`retrieval_app.py`:** remove the import (:85) and the warning block (:1717-1725). Update the
  "Deliberately silent" fallback comment (:1925) and the module-level fallback (tests at
  `test_app.py:3578-3582`).
- **`config.yaml:80-87` and `bench/config.yaml:83-90`:** `max_promptguard_chunks: 256`, with the
  comment rewritten. The `extraction` block's 64 is a different key; leave it.
- **Tests that flip:**
  - `tests/test_app.py:3313-3315`: empty config → 256 and the derived ceiling.
  - `test_app.py:3544-3582`: delete the warning parametrization; assert that **no**
    `retrieve_budget_unset` record appears for empty or explicit config.
  - `tests/test_orchestrator.py:6090` and :6128-6161: the `{}` cases move to "explicit 0", and
    the "absent" case now expects refusal.
- **Corpus:** `scripts/corpus/outcomes.py:95-96` comment. Regenerate the baseline and confirm zero
  outcome changes. Corpus pages are far under 458,752 characters; if any record moves, stop and
  report.
- **Docs: the sizing rewrite** (`docs/configuration.md` :1032 row, :1080-1106).
  - The "provisional 100 ms per window" pairing predates the 86M default. Replace it with the
    measured reference-envelope figures already in the same file (:562-574: 86M 26.2 s / 1,316 s
    over 55 windows at 1 vCPU; 6.4 s / 368 s at 4 vCPU). State plainly that at 256 chunks on the
    1 vCPU reference envelope the worst-case hold exceeds the shipped 30 s wait.
  - Recommend operators on small hosts lower the budget (the documented lever) rather than raise
    the wait.
  - Rewrite the "Known risk — the shipped default pair" paragraph: the unbounded case is now
    opt-in (`0`).
- **Other docs:** `docs/releases.md` :175-176, :232-234, :456-461 (the windows paragraph), the
  Unreleased bullet, `CLAUDE.md:288` if it names the default, `kit_tools/arch/SECURITY.md:176`,
  `CODE_ARCH.md:228`, `TROUBLESHOOTING.md:194`, `MONITORING.md:212`, `docs/bootstrap-notes.md`
  note. Historical rows (`GOTCHAS.md:745`, `DECISIONS.md:644`, `SESSION_LOG.md`) are history;
  leave them.
- **No rotation:** every file here is unhashed. Confirm by computing `derive_sanitizer_revision()`
  before and after and recording both values (expected equal).

**Acceptance Criteria:**
- [ ] `RETRIEVE_MAX_PROMPTGUARD_CHUNKS == 256`. `COMING_MAX_PROMPTGUARD_CHUNKS` no longer exists
      anywhere in the repo (grep).
- [ ] An empty config yields 256, and the derived character ceiling is 458,752. An explicit `0`
      yields no pre-check and no `max_chunks` to the classifier (tests for both).
- [ ] App startup emits no `retrieve_budget_unset` record under empty, explicit-256 or explicit-0
      config.
- [ ] An over-budget fetched page under the default config is refused `422 content_too_large` /
      `promptguard_budget` (test).
- [ ] The corpus baseline regenerates with zero outcome changes, and `sanitizer_revision` is
      unchanged (before and after values recorded).
- [ ] The `docs/configuration.md` sizing section uses the measured 86M figures, states the
      1 vCPU over-wait consequence, and calls `0` the opt-out. The listed docs are updated.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-002: Drop the validation-422 placeholder keys and cut contract 1.4.0

**Priority:** P1

**Description:** As a consumer, I want request-validation 422 items to carry exactly `loc`, `msg`
and `type`, and the contract to read 1.4.0 with one complete change record, so that the closed
windows are reflected in the frozen surface I vendor.

**Independent Test:** Send a malformed body to each route and assert each `detail` item's key set
is exactly `{loc, msg, type}`. Assert that `CONTRACT_VERSION == "1.4.0"`, that the 1.4.0 golden
matches the live schema, and that `contract/openapi.yaml` matches its anchor.

**Implementation Hints:**
- **`retrieval_app.py`:**
  - Delete `_VALIDATION_PLACEHOLDER` and `_VALIDATION_WINDOW_KEYS` (:1160-1161) and the
    `**dict.fromkeys(...)` emission (:2010).
  - Rewrite the `ValidationErrorDetail` docstring (:1120-1126), `_PIPELINE_422_DESCRIPTION`
    (:2064-2069) and `/extract`'s 422 description (:2396-2401). These are all in the OpenAPI
    document.
  - **Do not touch** the unrelated `"[redacted]"` domain-entry log value at :1673 (tested at
    `test_app.py:2699, :2827`).
- **Ruling (l)'s invariants stay:** no request value in logs, the closed `loc` allowlist, the cap
  of 100, and the two WARNING tokens. Their tests must stay green unchanged.
- **Tests:**
  - `tests/test_contract_errors.py`: delete `_strip_window_keys` (:174-178, used :185).
    `_VALIDATION_ITEM_KEYS` (:588) becomes `{loc, msg, type}`. Delete the placeholder loop
    (:643-644) and `test_validation_window_keys_are_fixed_placeholders` (:790-792), replacing it
    with a test asserting the exact key set on every route.
  - `tests/test_governance_docs.py:479` requires `'"[redacted]"'` in ruling (l)'s section. Keep
    the ruling's history text, or update the test to the closed-window wording.
- **The bump** (GOVERNANCE.md:347-379):
  - If spec 1 already bumped `CONTRACT_VERSION` (see its Implementation Notes), finalise rather
    than bump.
  - `CONTRACT_VERSION = "1.4.0"` (`pipeline/contract.py:22`). **One** final
    `* ``1.4.0`` — …` bullet (column 0, continuation lines indented two spaces, no blank lines),
    replacing any provisional lines from spec 1. It lists:
    1. the dropped placeholder keys (ruling (l) step 3);
    2. the budget default 256 with `0` as opt-out (ruling (g));
    3. `html_extraction_error` under `extraction_failed` on `/retrieve`.
  - Sanitizer outcomes (fold BLOCK, quarantine) are not contract changes (ruling (m)); one
    sentence may say so.
  - Tense guard: `tests/test_ci_workflow.py:2410-2430`.
- **Golden:** create `tests/golden/contract_1_4_0.json` with the same six models (pattern:
  `tests/test_contract_schema.py:22-30`). The 1.0.0–1.3.0 goldens are frozen and byte-unchanged.
- **Export:** `uv run python -m scripts.export_contract` writes `contract/openapi.yaml`, its
  `.sha256` and `tests/fixtures/contract/unregenerated_openapi.yaml` together. Never hand-edit
  them.
- **Version pins:**
  - `tests/test_bench_promptguard.py:83, :307` (`== "1.3.0"`).
  - Prose: `contract/GOVERNANCE.md:29` (checked by `test_governance_docs.py:281-296`),
    `CLAUDE.md` invariant 4 ("currently **1.3.0**"), `README.md:70`, and the anchor quotes in
    `README.md`/docs that `tests/test_contract_export.py` checks.
  - Grep `1.3.0` across `docs/`, `kit_tools/` and the root. Only *current-version* statements
    change; history stays.
- **GOVERNANCE:**
  - Rewrite `contract/GOVERNANCE.md:72-75` ("both 'next MINOR' windows stay open") to record both
    closed in 1.4.0.
  - Append the closure to rulings (g) and (l) as dated continuation lines.
  - Do not add a new ruling unless something new was decided. If one is added, bump the "Fourteen
    rulings" count (:182; `tests/test_governance_docs.py:97, :443`).
- **Rotation:** `contract.py` is hashed.
  - Measure the reversal of `contract.py` alone against the pre-story commit, under default and
    shipped config.
  - Record it in `CLAUDE.md`, `docs/bootstrap-notes.md` and the GOTCHAS table and tally, plus all
    count sites. Not a sanitization change.
- **Consumers:** the GOVERNANCE vendoring procedure applies. Add a consumer note to
  `docs/bootstrap-notes.md` for Poppy: the 422 key drop and the 1.4.0 activation check (minor
  bump, no refusal).

**Acceptance Criteria:**
- [ ] Every request-validation 422 `detail` item on `/search`, `/retrieve` and `/extract` has
      exactly the keys `loc`, `msg`, `type` (one test per route). `_VALIDATION_PLACEHOLDER`,
      `_VALIDATION_WINDOW_KEYS` and `_strip_window_keys` no longer exist (grep).
- [ ] Ruling (l)'s retained invariants still pass unchanged: no request value in logs, the closed
      `loc` allowlist, the cap of 100, and the two WARNING tokens. The `:1673` log value is
      untouched.
- [ ] `CONTRACT_VERSION == "1.4.0"`, with one final docstring bullet listing all three 1.4.0
      changes and no provisional lines left. The tense guard passes.
- [ ] `tests/golden/contract_1_4_0.json` exists and matches. The 1.0.0–1.3.0 goldens are
      byte-unchanged (`git diff --stat` shows none).
- [ ] `contract/openapi.yaml`, its `.sha256` and the fixture twin are regenerated by
      `scripts.export_contract`. `tests/test_contract_export.py` and the doc anchor quotes are
      green.
- [ ] `contract/GOVERNANCE.md` records both windows closed in 1.4.0. Current-version prose names
      1.4.0, and `test_bench_promptguard.py`'s pins are updated.
- [ ] The rotation is measured (`contract.py` reverted alone reproduces the previous value under
      default and shipped config) and recorded in `CLAUDE.md`, `docs/bootstrap-notes.md` and the
      GOTCHAS table. All count sites are updated.
- [ ] A consumer note for the 1.4.0 changes is in `docs/bootstrap-notes.md`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

### US-003: Release notes, image pins and the ready-to-tag record

**Priority:** P1

**Description:** As the owner, I want `docs/releases.md` to carry a complete v1.3.0 entry and
every image pin to name 1.3.0, so that merging the PR and pushing `v1.3.0` is the only thing left.

**Independent Test:** `tests/test_compose_fragments.py` passes with `_FORAGE_RELEASE_TAG = "1.3.0"`.
The `docs/releases.md` v1.3.0 entry exists with `contract: 1.4.0` and the anchor that
`tests/test_contract_export.py` checks. The full suite passes.

**Implementation Hints:**
- **`docs/releases.md`:**
  - Turn the Unreleased section (:13-72, currently "version number is decided at the release
    gate. Contract stays `1.3.0`") into `### v1.3.0 — <date>`, in the v1.2.2 entry format
    (:81-180): `contract:`, `anchor:`, `index digest:` and `tagged commit:` lines.
  - `index digest` and `tagged commit` are filled **after** publish by the owner; leave a
    clearly marked placeholder. Check whether any test parses these lines and reads placeholders,
    and follow the convention the v1.2.2 entry used before its tag.
  - Leave an empty Unreleased heading above it.
- **What the entry must cover** (each item one or two sentences, linking to config/docs):
  - the 86M default (the HF token needs the 86M grant) and the `FORAGE_MEM_LIMIT` 1536m default;
  - the bounded decoder;
  - structural scan forms and `title: null` quarantine;
  - hidden-text removal and the two re-pinned over-defence probes;
  - spec 1's worker threshold and the `html_extraction_error` 422;
  - `/search` parse moved off the event loop;
  - spec 2's 2× fold BLOCK;
  - budget default 256;
  - the 422 key drop;
  - the rotation range from `021378ef…` to the final value, with its count;
  - "Compatibility windows: none open".
- **Image pin fan-out** (`1.2.2` → `1.3.0`): `compose/minimal.yml:52, :64, :66`,
  `compose/full.yml:41, :45`, `tests/test_compose_fragments.py:78`,
  `contract_smoke.py:66, :97-99`, `README.md:78, :253`. Grep `1\.2\.2` for any other *current*
  pin; historical release entries stay.
  - Follow the v1.2.2 precedent: check how the previous release ordered pinning compose to an
    unpublished tag against publishing it (`docs/releases.md` "Cutting a release" :758-768 and the
    archived `feature-hardening-release.md` US-003/US-004). Write the order into Implementation
    Notes.
- **Tracking docs:**
  - `kit_tools/SYNOPSIS.md` current state.
  - `kit_tools/roadmap/BACKLOG.md`: add "Evaluate a compiled HTML parser (selectolax/lexbor or
    Rust), measured against the corpus" with its cassette-re-record caveat, and the open finding
    2026-10-07-001.
  - `kit_tools/roadmap/MILESTONES.md`.
  - `kit_tools/testing/TESTING_GUIDE.md` module and test counts, and `CLAUDE.md`'s collected
    count.
- **Findings:** do not edit `kit_tools/AUDIT_FINDINGS.md` in the worktree. List in Implementation
  Notes the findings that are ready to resolve (2026-10-06-001, 2026-10-04-060, 2026-10-07-002)
  for the owner.
- **Owner gate text:** end the Implementation Notes with the exact owner sequence: merge →
  `git switch main && git pull` → confirm the six gates are green → `git tag v1.3.0` →
  `git push origin v1.3.0` → watch `publish` → anonymous pull → fill `index digest` and
  `tagged commit`. **No story runs these.**

**Acceptance Criteria:**
- [ ] `docs/releases.md` has a `### v1.3.0` entry with `contract: 1.4.0` and the current anchor,
      covering every listed item. An empty Unreleased section sits above it, and the
      `index digest`/`tagged commit` placeholders follow the established pre-tag convention.
- [ ] Every current image pin names `1.3.0`: compose fragments, `test_compose_fragments.py`,
      `contract_smoke.py` and README. A grep for `1\.2\.2` finds only historical entries.
- [ ] BACKLOG gains the compiled-parser item and 2026-10-07-001. SYNOPSIS, MILESTONES,
      TESTING_GUIDE and `CLAUDE.md` counts match the tree.
- [ ] Implementation Notes list the findings ready to resolve and the exact owner tag/publish
      sequence. No tag is created or pushed by the story.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes with zero errors

## Edge Cases

- An operator config that sets `max_promptguard_chunks: 0` explicitly: no warning, no pre-check.
  This is the documented opt-out. (US-001)
- An operator relying on the old silent default: they now get refusals on pages over 458,752
  characters. The release notes call this out. (US-001, US-003)
- A validation error whose pydantic item carried `ctx` with an exception object: the key is now
  simply absent, with no serialization path left. (US-002)
- Spec 1 already bumped `CONTRACT_VERSION`: US-002 finalises the entry and regenerates the held
  1.4.0 golden, and does not bump twice. (US-002)
- A consumer pinned to 1.3.0 activates against 1.4.0. Minor bump, so it activates by design; the
  consumer note says so. (US-002)

## Out of Scope

- Pushing the tag, publishing, the anonymous-pull check, and merging. These are owner gates.
- Editing `pyproject.toml` `version` (inert).
- Changing any other default, or opening new compatibility windows.
- Poppy-side pin changes. Pushes to Poppy need the owner.

## Assumptions

- Ruling (l)'s carve-out makes the key drop a MINOR (GOVERNANCE.md:657-662). There is no MAJOR.
- The flip to 256 rides the same MINOR under ruling (g) and moves no wire shape.
- No third-party consumers exist (owner, 2026-10-06), but the announcement discipline is followed
  anyway, because it is cheap.

## Technical Considerations

- `contract.py` is hashed, so US-002 rotates. `retrieval_app.py` and `retrieve_limits.py` are not,
  so US-001 does not.
- The publish job extracts the `CONTRACT_VERSION` docstring entry into the Release body and fails
  without one (`ci.yml:1054-1123`; `tests/test_ci_workflow.py::TestReleaseContractMapping`).
- Smoke checks `info.version` against `/health.contract_version` and the anchor
  (`ci.yml:623-627, :705-707`).

## Related Documentation

- [contract/GOVERNANCE.md](../../contract/GOVERNANCE.md): rulings (g), (l) and (m), and the bump
  procedure at :347-379
- [docs/releases.md](../../docs/releases.md), [docs/configuration.md](../../docs/configuration.md)
- Archived `feature-hardening-release.md` (the v1.2.x release precedent)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** The validation-key drop and the contract bump are one story.
**Rationale:** The frozen 1.3.0 golden pins six models. A schema change without the bump turns
`test_contract_schema_matches_golden` red, so the two cannot land in separate commits.
**Source:** `tests/test_contract_schema.py:21-30`; GOVERNANCE.md:347-379.

**Decision:** No BUMP_VERSION runbook; follow `docs/releases.md`.
**Rationale:** It was skipped at seeding (`kit_tools/SEED_MANIFEST.json:117-125`). The tag is the
version, and `pyproject.toml` is inert (`docs/releases.md:412-418`).

**Decision:** Ship 256 as announced and rewrite the sizing docs honestly.
**Rationale:** The announcement is a promise. Any bound beats none, and the measured 86M
latencies make the small-host trade-off explicit for operators to tune.
**Source:** `docs/configuration.md:562-574, 1080-1106`.

### Scope Adjustments

- The original "version bump runbook" item is replaced by `docs/releases.md` plus the pin fan-out
  (US-003).

### Decisions Made

- `pyproject.toml` `version` stays `0.1.0` (inert, documented).

## Clarifications

### Session 2026-10-07
- Q: Bump the version to 1.3.0 with this release? → A: Yes. v1.3.0 is the image; the contract is
  1.4.0.
- Q: Which `max_promptguard_chunks` default ships? → A: 256 as promised; the sizing docs are
  rewritten with the 86M measurements, and `0` stays the opt-out.
- Q: Plan the release as a small spec, then validate and execute? → A: Yes. Tag and publish stay
  owner approval steps.
