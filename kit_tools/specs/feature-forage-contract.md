<!-- Template Version: 2.5.0 -->
---
feature: forage-contract
status: active
session_ready: true
depends_on: [forage-ci-and-image, forage-cache-fallback]
vision_ref: Secure Web Retrieval / provider-independent web access
type: epic-child
size: M
epic: forage-extraction-forage-side
epic_seq: 4
epic_final: true
execution_order: [US-001, US-005, US-002, US-003, US-004]
created: 2026-09-02
updated: 2026-09-07
---

# Feature Spec: Forage Contract — Documented Error Surface, Frozen OpenAPI + Governance

> **Executes in the Forage repo** (last Forage-side spec; `epic_final: true` in the
> Forage-local wrapper). The Poppy half (vendored copy + Poppy-side contract test +
> `ACCEPT_MISSING_CONTRACT_VERSION` flip) lives in `feature-poppy-consume-forage-image`.

## Overview

Freeze the Poppy↔Forage wire contract as an artifact that tells the truth — **without
changing a single wire byte**. Round 2 killed the round-1 "emit through one envelope model"
design three ways (it dropped `sanitizer_revision`, which Poppy hard-rejects 422s without;
it omitted the five `/retrieve` refusal codes, turning the SSRF-drill path into a 500; and
homogenizing the middleware bodies is a shape change the no-bump ruling forbids). The
corrected design is **document-as-is**: per-status Pydantic models that mirror each existing
emission shape byte-for-byte, wired via `responses=` only on the routes/statuses that
actually produce them, with runtime parity tests asserting model ↔ emission equality so the
documentation can never drift from the code. Then: `contract/openapi.yaml` generated
deterministically, drift-checked as a pytest, versioned by `CONTRACT_VERSION` (1.1.0 after
spec 4), governed by written semver rules with a mechanized image↔contract mapping, and
shipped in-image + as a sha256-anchored Release asset.

## Goals

- The generated contract documents: all five routes; the **complete 17-code error
  vocabulary** (the ten `/extract` document-failure codes incl. middleware-emitted `busy`/
  `content_too_large`, the five `/retrieve` refusal codes `private_ip`/`blocked_domain`/
  `invalid_url`/`fetch_timeout`/`fetch_error`, and `searxng_error`/`searxng_unavailable`);
  every distinct error body shape as it exists today (incl. the `{"detail": ...}` 404/503s
  and the `request_id`-less 413); enum-rendered `degraded_reasons`; a fully-typed
  `/metrics`; and a top-level description stating the no-auth/private-network precondition.
- Zero wire-byte changes: every error emission site's output is byte-identical before and
  after this spec (parity-tested), so `CONTRACT_VERSION` stays 1.1.0 under the recorded
  documentation-only ruling.
- `contract/openapi.yaml`, `pipeline/contract.py::CONTRACT_VERSION`, and the live
  `/openapi.json` `info.version` can never disagree (CI-enforced; the hardcoded
  `version="0.1.0"` replaced).
- Drift fails within one PR (pytest drift check); a consumer can obtain the contract from
  the git tag, the Release asset, or the image — all sha256-identical against the committed
  anchor file.
- The governance doc classifies any wire change without asking a human, including the
  image↔contract mapping (regex-checked in the release job, not a human-authored line).

## User Stories

### US-001: Document the error surface as it exists (no wire changes)

**Priority:** P1

**Description:** As a contract consumer, I want every error shape and code the service
actually emits captured in the schema — mirrored from the code, never imposed on it.

**Independent Test:** `app.openapi()` contains the per-status error components on exactly
the route/status pairs that emit them; the parity pytest (`model.model_dump()` equals each
live emission site's dict, byte-for-byte on serialization) passes; all 17 error codes render
as an enum.

**Implementation Hints:**
- **Ground truth to mirror** (verified round 2): the `PipelineError` handler
  (`retrieval_app.py:571-587`) emits `{error, reason, request_id}` + `sanitizer_revision`
  **added on `/extract` paths** (`:579-583`); the 413 admission middleware emits
  `{error, reason}` with **no** `request_id` (`:355-362`); the 429 emits four fields
  (`:396-400`); the gated-`/extract` 404 and the two admission 503s emit
  `{"detail": ...}` (`:379-381,383-393,721`). Both middlewares gate on `path == "/extract"`
  (`:326-328,373-375`) — so `responses=` declarations go on `/extract` for
  404/413/429/503, on `/retrieve`+`/search`+`/extract` for the handler-emitted 422, and
  `/health`+`/metrics` get **no** error declarations (they only ever 200).
- Models — **one per byte-shape** (round-3: one model with optional fields conflates four
  distinct shapes and defeats its own parity test): `Extract422ErrorResponse`
  (`error/reason/request_id/sanitizer_revision`, the `/extract` handler shape — Poppy's
  hard requirement at `client.py:594-600` visible in-schema), `Pipeline422ErrorResponse`
  (`error/reason/request_id`, the `/retrieve`+`/search` handler shape),
  `Admission413Response` (`{error, reason}` — no request_id), `RateLimit429Response` (the
  four-field shape), `DetailResponse` (`{"detail": str}` for 404/503). **422 declarations
  are unions**: `responses={422: ...anyOf [the route's pipeline shape,
  HTTPValidationError]}` — FastAPI stops auto-adding `HTTPValidationError` once 422 is
  declared (round-3, reproduced on the pinned FastAPI), and the live service genuinely
  returns `{detail:[...]}` for malformed request bodies; the parity tests cover the
  pipeline arm, the validation arm is documented as FastAPI-default.
  **The emission sites are NOT rewritten to emit through the models** — the models mirror;
  the parity tests enforce the mirror (round-2 reversal of the round-1 design).
- `ErrorCode` Literal: **create the frozensets in `contract.py`** (they don't exist yet —
  this story adds them) and derive Literals via the `frozenset(get_args(...))` pattern
  (`retrieval_app.py:160`) so Literal and set can't diverge. Enumerate all 17 by sweeping
  ALL raise sites: `pipeline/orchestrator.py:248-319` (the five `/retrieve` refusal codes —
  and note `content_too_large` is *also* raised on `/retrieve` at `:312-317`, not only by
  the `/extract` middleware), `:680,686` (searxng), **and `retrieval_app.py:449-487`**
  (round-3: an in-repo raise site the sweep list missed); Poppy's pinned allowlist
  (`client.py:218-230`) is inlined verbatim into the test, since that file lives in the
  other repo.
- `degraded_reasons: list[DegradedReason]` typed the same derived-Literal way — and because
  response validation would 500 on an unlisted member, the derivation-from-frozenset is the
  guard (a future new reason updates both atomically; noted in the field docstring).
- No `sanitizer_revision` sources change (this story touches typing/declarations, not the
  hashed pipeline files' behavior — `contract.py` edits rotate the revision, acknowledged
  once for the spec).

**Acceptance Criteria:**
- [ ] Per-status models + `responses=` declarations exactly matching the emission map above
      (`/health`+`/metrics` clean); no emission site's output bytes change (parity pytest
      per site).
- [ ] `ErrorCode` covers all 17 codes via frozenset-derived Literals; `degraded_reasons`
      enum-rendered the same way; `sanitizer_revision` present-and-documented on the
      `/extract` 422 component.
- [ ] `capabilities`/`omitted_by_reason` vocabularies documented in field descriptions
      (kept `dict[str,int]` — additive-safe per governance).
- [ ] **Golden fixtures regenerated in the same commit** (`golden/contract_1_1_0.json` —
      the enum/description tightening changes `model_json_schema()` and the existing
      golden test fails otherwise; its docstring's "bump CONTRACT_VERSION" instruction is
      updated to reference the no-bump ruling; round-3 critical restoring a dropped
      round-2 AC).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-005: Typed `/metrics` + app metadata

**Priority:** P1

**Description:** As an operator and contract consumer, I want `/metrics` typed without
silently dropping fields, and the app's served metadata truthful.

**Independent Test:** `/metrics` renders a full schema (no free-form top-level object); a
runtime parity test asserts the typed response equals the handler's current dict — including
the cgroup keys **flattened into `extraction` exactly as today** (`**_cgroup_memory_snapshot()`,
`retrieval_app.py:653` — nesting them is a wire change; round-2 finding); `/openapi.json`
serves `info.version == CONTRACT_VERSION` and the posture description.

**Implementation Hints:**
- Typed model with `extra="forbid"` + the runtime parity test (round-2 second-opinion: the
  forbid + parity pair makes a future unmodeled metric fail loudly instead of being
  silently filtered by FastAPI's response validation).
- Keep `test_app.py:395`'s existing flat-key assertion green — it is the fossil guard for
  the flatten decision.
- `FastAPI(title="Forage", version=CONTRACT_VERSION, description=<no-auth/private-network
  posture paragraph>)` replacing `retrieval_app.py:546-550`; `/docs`,`/redoc`,
  `/openapi.json` acknowledged in `docs/configuration.md` as exposed unauthenticated
  surface.

**Acceptance Criteria:**
- [ ] `/metrics` fully typed (`extra="forbid"`), cgroup keys flat as today, parity test
      green, `test_app.py` flat-key assertion untouched and green.
- [ ] Live `info.version == CONTRACT_VERSION`; posture description served; docs updated.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-002: Generated contract file + pytest drift check

**Priority:** P1

**Description:** As a contract consumer, I want the OpenAPI document generated from the app
and drift-checked in the normal test suite so the file is always the truth.

**Independent Test:** Changing a response model without regenerating fails
`tests/test_contract_export.py`; `scripts/export_contract.py` output is byte-stable across
repeat runs; `/extract` appears while `extract_route_enabled: false`.

**Implementation Hints:**
- `scripts/export_contract.py`: dump `app.openapi()` deterministically (sorted keys,
  canonical YAML); also write `contract/openapi.yaml.sha256` (the **committed anchor** the
  distribution story and Poppy's vendoring verify against — round-2: Release assets are
  mutable, the git-tagged checksum is the trust anchor).
- Drift check as a pytest (regenerate in-memory, compare, fail with the regen message) —
  runs in spec 2's `test` job, locally, and under the orchestrator's regression gate.
- Dependency-bump rendering changes show as reviewable regen diffs (docstring note).

**Acceptance Criteria:**
- [ ] `contract/openapi.yaml` + `contract/openapi.yaml.sha256` committed, generated,
      deterministic; `/extract` present.
- [ ] Drift pytest in the suite; a deliberate un-regenerated change fails it (committed as
      a self-test fixture case, not a throwaway run).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-003: Semver governance, PR template, SECURITY.md

**Priority:** P2

**Description:** As a Forage contributor, I want written rules for contract versioning, a PR
checklist, and a security policy for the soon-public repo.

**Independent Test:** The doc classifies unambiguously: removing a response field (MAJOR),
adding an optional request param (MINOR), fixing a description (PATCH), changing an enum
member's meaning (MAJOR), a schema-doc tightening with no wire-byte change (no bump
pre-freeze / PATCH post-freeze), an urgent security tightening (expedited MINOR **with a
compatibility window** — never a silent break).

**Implementation Hints:**
- `contract/GOVERNANCE.md`: the classification rules; the recorded rulings — (a) US-001/
  US-005's documentation pass = no bump (parity-tested zero wire change); (b) new
  omission/degraded enum members = MINOR (Poppy buckets unknowns) with the release-note
  announcement obligation for security-block reasons; (c) golden-fixture retention (all
  historical `contract_X_Y_Z.json` fixtures retained); (d) the `/retrieve` error `reason`
  echoing resolved private IPs is a **documented caveat** for consumers exposing Forage
  beyond a private network (wire unchanged in this epic; Epic 5 owns any change) — round-2
  security note.
- **Two-semver rule, mechanized** (round-2: a human release-notes line was the only
  integrity claim left unmechanized): image tags and `CONTRACT_VERSION` are independent;
  the release job (spec 2's US-007 workflow) gains a step asserting the Release body
  contains `contract: X.Y.Z` matching the tagged tree's `CONTRACT_VERSION` (regex check).
  First release after this spec: image `v1.0.0` serving contract `1.1.0` — **cut by US-004
  as its final AC, not here** (round-3 critical: US-004 adds the in-image COPY and Release
  assets; tagging before it ships a contract-less v1.0.0, unrecoverable without a re-tag —
  and spec 6's entry gate pins exactly that tag).
- Bump checklist → `.github/pull_request_template.md` (created here).
- `SECURITY.md` at repo root: reporting contact, supported-versions note, the deployment
  posture summary (round-2: public repo, no security policy).

**Acceptance Criteria:**
- [ ] GOVERNANCE.md with rules + the four recorded rulings + the six classification
      examples answerable from the doc alone; PR template created; SECURITY.md present.
- [ ] Release-body regex step added to the publish workflow (asserted by the workflow-shape
      tests); the step's job carries `contents: write` (named — round-3).

### US-004: Contract distribution — image + release asset, sha256-anchored

**Priority:** P1

**Description:** As Poppy (or any consumer), I want the exact contract retrievable from the
artifacts I already consume, verified against the committed anchor.

**Independent Test:** For the `v1.0.0` release: `sha256sum` of the repo file, the Release
asset, and `docker run --rm --entrypoint cat <image> /app/contract/openapi.yaml` all equal
the committed `contract/openapi.yaml.sha256` (needs the pushed tag — supervised checkpoint,
run URL recorded).

**Implementation Hints:**
- Dockerfile: `COPY contract/ /app/contract/`.
- Release workflow: upload `contract/openapi.yaml` + its `.sha256` as assets (mutability
  caveat documented; the committed anchor governs).
- Extend spec 2's smoke job: in-image contract `info.version` equals the running
  container's `/health.contract_version`.
- Consumer vendoring procedure (verify against the anchor) in GOVERNANCE's Consumers
  section.

**Acceptance Criteria:**
- [ ] Contract + sha256 in-image (COPY landed, smoke extension green); the drift pytest
      also asserts the committed anchor matches the committed file (the anchor↔file tie —
      round-3).
- [ ] **Image `v1.0.0` cut as this story's final step** (supervised tag push after
      COPY + asset workflow land; run URL recorded) — the artifact spec 6 pins.
- [ ] Release assets present on the `v1.0.0` release; three-way sha256 equality against
      the committed anchor verified and recorded; vendoring procedure documented.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

## Edge Cases

- Dependency bump changes OpenAPI rendering → reviewable regen diff (US-002).
- `CONTRACT_VERSION` bumped with no wire change → checklist forces regen of openapi +
  sha256 + golden so all copies stay equal (US-003).
- An emission site's shape edited without updating its mirror model → the per-site parity
  pytest fails (US-001/US-005).
- A future degraded/omission reason added → frozenset-derived Literals force the atomic
  double-update; governance classifies it MINOR with the announcement obligation (US-001/
  US-003).
- Release asset replaced after publish → consumers verify against the committed anchor
  (US-004).

## Out of Scope

- Poppy-side vendoring, the Poppy contract test, the `ACCEPT_MISSING_CONTRACT_VERSION`
  flip — spec 6.
- ANY wire-byte change: no envelope homogenization, no auth, no `reason`-field redaction
  (Epic 5's conversation), no caller-policy-field changes (`models.py:225-256` frozen as-is,
  recorded).
- Contract-testing frameworks (pact/schemathesis).

## Assumptions

- Spec 4 landed (contract at 1.1.0 incl. `cache_backend`) — `depends_on`.
- YAML artifact + JSON goldens as machine twins.

## Technical Considerations

- Order (frontmatter): US-001 → US-005 → US-002 → US-003 → US-004 — models before export,
  export before governance's checklist references it, and the `v1.0.0` cut is **US-004's
  final step** so the tagged tree already carries the in-image COPY + release-asset
  workflow it verifies (micro-verify reconciliation — three passages previously disagreed
  on the owner). The rulings referenced by US-001 live in this spec's text until US-003
  copies them into GOVERNANCE.md (no forward file dependency).
- This spec + Epic 1's handshake make Epic 3 (search providers) safely addable: provider
  additions become MINOR bumps with a typed home for their failure modes.

## Related Documentation

- Epic (this repo): [epic-forage-extraction-forage-side.md](epic-forage-extraction-forage-side.md)
- Planned in Poppy (canonical planning record, one-way sync — see the wrapper's Notes):
  [`epic-forage-extraction.md`](https://github.com/WashingBearLabs/Poppy/blob/main/kit_tools/specs/epic-forage-extraction.md) ·
  [`WEB_ACCESS_FAMILY.md`](https://github.com/WashingBearLabs/Poppy/blob/main/kit_tools/specs/WEB_ACCESS_FAMILY.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision (round 2, reversing round 1):** Mirror-and-parity-test the existing error
shapes; never rewrite emission sites to emit through a model
**Rationale:** the round-1 single-envelope design was triply broken in ways five reviewers
independently verified: dropping `sanitizer_revision` breaks Poppy's hard 422 validation
(`client.py:594-600`), omitting the five `/retrieve` codes turns the RFC1918 drill into a
handler-internal ValidationError → 500, and homogenizing 404/413/503 bodies is a MAJOR-class
wire change smuggled under a no-bump ruling. Documentation must follow the wire, with parity
tests as the leash.

**Decision:** Frozenset-derived Literals (`frozenset(get_args(...))` pattern,
`retrieval_app.py:160`)
**Rationale:** enum-typing `degraded_reasons` on a response model makes an unlisted member a
500 on the healthcheck's endpoint; deriving Literal and set from one source removes the
divergence class.

**Decision:** Committed `.sha256` anchor + mechanized release-body mapping check; `v1.0.0`
cut owned by **US-004 (final AC)** — round-3 correction after round 2 briefly parked it in
US-003, before the COPY/asset work existed
**Rationale:** Release assets are mutable; a human release-notes line was the last
unmechanized integrity claim; and no story owned cutting the release spec 6 gates on
(round-2 findings).

### Scope Adjustments

- Round 2 (2026-09-02): US-001 split (error surface / US-005 metrics+metadata);
  emit-through-model design replaced by mirror+parity; 17-code vocabulary enumerated with
  in-repo derivation; cgroup keys stay flat; SECURITY.md + expedited-path compat window +
  IP-echo caveat added to governance; sha256 anchor committed; `v1.0.0` cut given an owner.

### Decisions Made

## Clarifications

### Session 2026-09-02
- Q: Where does the Poppy-side contract test live? → A: In spec 6 — this spec is
  Forage-only.
- Q (round 1): does the typing/envelope pass bump the contract? → A: No — and round 2
  tightened the ruling's premise: it holds only because the design now provably changes
  zero wire bytes (parity tests).
