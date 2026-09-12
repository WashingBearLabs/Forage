<!-- Template Version: 2.5.0 -->
---
feature: forage-contract
status: completed
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
updated: 2026-09-12
completed: 2026-09-12
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
- ~~No `sanitizer_revision` sources change~~ **[RETIRED — audit 2026-09-11: FALSE as
  shipped.** `pipeline/contract.py` IS a hashed source, the frozensets landed there, and
  the revision rotated (`fa4691c5…` → `8b1b7f78…`) — the spec's one acknowledged
  rotation. See Implementation Notes.]**

**Acceptance Criteria:**
- [x] Per-status models + `responses=` declarations exactly matching the emission map above
      (`/health`+`/metrics` clean); no emission site's output bytes change (parity pytest
      per site).
- [x] `ErrorCode` covers all 17 codes via frozenset-derived Literals; `degraded_reasons`
      enum-rendered the same way; `sanitizer_revision` present-and-documented on the
      `/extract` 422 component.
- [x] `capabilities`/`omitted_by_reason` vocabularies documented in field descriptions
      (kept `dict[str,int]` — additive-safe per governance).
- [x] **Golden fixtures regenerated in the same commit** (`golden/contract_1_1_0.json` —
      the enum/description tightening changes `model_json_schema()` and the existing
      golden test fails otherwise; its docstring's "bump CONTRACT_VERSION" instruction is
      updated to reference the no-bump ruling; round-3 critical restoring a dropped
      round-2 AC).
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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
- **Must NOT rotate `sanitizer_revision`** — US-001 spent the spec's one acknowledged rotation; stay out of `_REVISION_SOURCES` files or be content-neutral in them.
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
- [x] `/metrics` fully typed (`extra="forbid"`), cgroup keys flat as today, parity test
      green, `test_app.py` flat-key assertion untouched and green.
- [x] Live `info.version == CONTRACT_VERSION`; posture description served; docs updated.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

### US-002: Generated contract file + pytest drift check

**Priority:** P1

**Description:** As a contract consumer, I want the OpenAPI document generated from the app
and drift-checked in the normal test suite so the file is always the truth.

**Independent Test:** Changing a response model without regenerating fails
`tests/test_contract_export.py`; `scripts/export_contract.py` output is byte-stable across
repeat runs; `/extract` appears while `extract_route_enabled: false`.

**Implementation Hints:**
- **Must NOT rotate `sanitizer_revision`** — US-001 spent the spec's one acknowledged rotation; stay out of `_REVISION_SOURCES` files or be content-neutral in them.
- `scripts/export_contract.py`: dump `app.openapi()` deterministically (sorted keys,
  canonical YAML); also write `contract/openapi.yaml.sha256` (the **committed anchor** the
  distribution story and Poppy's vendoring verify against — round-2: Release assets are
  mutable, the git-tagged checksum is the trust anchor).
- Drift check as a pytest (regenerate in-memory, compare, fail with the regen message) —
  runs in spec 2's `test` job, locally, and under the orchestrator's regression gate.
- Dependency-bump rendering changes show as reviewable regen diffs (docstring note).

**Acceptance Criteria:**
- [x] `contract/openapi.yaml` + `contract/openapi.yaml.sha256` committed, generated,
      deterministic; `/extract` present.
- [x] Drift pytest in the suite; a deliberate un-regenerated change fails it (committed as
      a self-test fixture case, not a throwaway run).
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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
- **Must NOT rotate `sanitizer_revision`** — US-001 spent the spec's one acknowledged rotation; stay out of `_REVISION_SOURCES` files or be content-neutral in them.
- `contract/GOVERNANCE.md`: the classification rules; the recorded rulings — (a) US-001/
  US-005's documentation pass = no bump (parity-tested zero wire change); (a2, added at
  US-001 verification 2026-09-11) **the documented-but-unreachable `/extract` 413**: the
  contract declares a status no HTTP client can observe (FastAPI wraps the middleware's
  raise into a 400 — GOTCHAS carries the measurements); GOVERNANCE must classify BOTH the
  current state (documenting intent alongside reality's 400, no bump) and the future fix
  (correcting 400→413 is a WIRE CHANGE requiring a bump) — codegen consumers read
  statuses, not descriptions, so this ruling is load-bearing for them; (b) new
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
- [x] GOVERNANCE.md with rules + the five recorded rulings (a2 added at US-001 verification) + the six classification
      examples answerable from the doc alone; PR template created; SECURITY.md present.
- [x] Release-body regex step added to the publish workflow (asserted by the workflow-shape
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
- **Must NOT rotate `sanitizer_revision`** — US-001 spent the spec's one acknowledged rotation; stay out of `_REVISION_SOURCES` files or be content-neutral in them.
- Dockerfile: `COPY contract/ /app/contract/`.
- Release workflow: upload `contract/openapi.yaml` + its `.sha256` as assets (mutability
  caveat documented; the committed anchor governs).
- Extend spec 2's smoke job: in-image contract `info.version` equals the running
  container's `/health.contract_version`.
- Consumer vendoring procedure (verify against the anchor) in GOVERNANCE's Consumers
  section.

**Acceptance Criteria:**
- [x] Contract + sha256 in-image (COPY landed, smoke extension green); the drift pytest
      also asserts the committed anchor matches the committed file (the anchor↔file tie —
      round-3).
- [x] **Image `v1.0.0` cut as this story's final step** (supervised tag push after
      COPY + asset workflow land; run URL recorded) — the artifact spec 6 pins.
- [x] Release assets present on the `v1.0.0` release; three-way sha256 equality against
      the committed anchor verified and recorded; vendoring procedure documented.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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

### US-001 — Document the error surface as it exists (2026-09-11)

**Shipped:** branch `story/ct-us-001-error-surface`. Suite 1402 → **1427** green
(+25, all in the new `tests/test_contract_errors.py`); `ruff check`, `ruff format
--check` and `uv run pyright` all zero. `CONTRACT_VERSION` unchanged at `1.1.0`.

**The mirror models, and where they live.** Five per-shape models plus the validation
mirror, all in `retrieval_app.py` beside `HealthResponse`:
`Extract422ErrorResponse` (4 fields), `Pipeline422ErrorResponse` (3),
`Admission413Response` (2), `RateLimit429Response` (4), `DetailResponse` (1), and
`HTTPValidationError`/`ValidationErrorDetail`. Every coded one carries
`extra="forbid"` — that, plus the parity tests' serialized-byte comparison, is what
makes them mirrors rather than descriptions. **No emission site was rewritten to emit
through a model**, per the round-2 ruling.

**The vocabulary went into `contract.py` as nested Literals.** `Extract422ErrorCode`
(9) / `Admission413ErrorCode` / `RateLimit429ErrorCode` compose into `ExtractErrorCode`
(10); `RetrieveErrorCode` (6) and `SearchErrorCode` (2) compose into
`Pipeline422ErrorCode` (8); both compose into `ErrorCode` (**17**, deduplicated —
`content_too_large` is the one code two surfaces share). PEP 586 flattening of
`Literal[SomeLiteralAlias, ...]` was verified to work both at runtime and under
pyright strict, so the composites cannot drift from their parts. Each frozenset is
`frozenset(get_args(...))` off its own alias.

**`sanitizer_revision` rotated — once, here, as the spec acknowledged.**

```
before: fa4691c57449c52fe367208bdeeb650cbe474d93485c3b90cc8989b5e593547c
after:  8b1b7f78e85f733ef3b8ace5194632a8cf92410131b2c1af995c456f20196d7c
```

Attribution measured by re-deriving with `pipeline/contract.py` swapped back to
`main`'s bytes: all-eight-at-`main` (control) and only-`contract.py`-reverted both
return `fa4691c5…93547c`, and `contract.py` is the only `_REVISION_SOURCES` file this
story touched — one measurement, one conclusion, no intermediate value to transpose.
Recorded in `docs/bootstrap-notes.md` ("The sixth rotation"), `kit_tools/docs/GOTCHAS.md`,
`kit_tools/arch/CODE_ARCH.md`, `CLAUDE.md` and the epic wrapper. The cache invalidation
it causes is **correct behaviour**: `cache_policy_fingerprint()` takes the revision since
spec 3's US-003, so a rotation is the flush lever working as designed.
**US-005, US-002, US-003 and US-004 must not rotate again** — keep their edits out of
`pipeline/`'s eight hashed files, or content-neutral within them.

**FastAPI behaviour, re-verified on the locked 0.141.1 and pinned by test.** Declaring
a 422 does stop the automatic `HTTPValidationError` (`test_declaring_422_suppresses_
fastapis_automatic_one` asserts both halves on a throwaway app). One consequence worth
knowing: because all three body-taking routes now declare their 422, FastAPI injects
neither `HTTPValidationError` nor `ValidationError` into components, and *our* mirror is
the component the document carries —
`test_our_validation_mirror_matches_fastapis_own_definition` ties its required fields to
`fastapi.openapi.utils.validation_error_definition` so a FastAPI change to the validation
body shows up here.

**⚠ Finding: the spec's emission map is one shape short, and its 413 is unreachable.**
`DocumentSizeLimitMiddleware` emits the documented
`413 {"error": "content_too_large", "reason": ...}` to any downstream honouring the raw
ASGI contract — but **not on `/extract`**. It refuses by raising through the `receive`
callable, `fastapi/routing.py` wraps *any* exception out of `await request.form()` in
`HTTPException(400, "There was an error parsing the body")`, and that response is
produced inside the middleware's own `await self._app(...)` — so its
`except _RequestBodyTooLargeError` never runs. Measured on the running app: a multipart
or urlencoded body over the limit gets **400**; a JSON one gets 422 (starlette never
streams a non-form body, so the counter never runs). The byte cap itself is unaffected —
nothing oversized is spooled or extracted.

Handled **without changing a wire byte**, and flagged rather than improvised on:
`400 → DetailResponse` is declared on `/extract` alongside the 413, whose description
now says it is shadowed; the 400 is parity-tested through the real route and the 413 at
the ASGI seam (the only place it can be produced). A new GOTCHAS entry carries the
measurements and the rule that *fixing* it would be a wire change belonging to a
contract bump, not to this spec. **US-002 will therefore generate a contract carrying
both statuses on `/extract`, and US-003's governance doc should classify the
400→413 correction when someone wants it.**

**Golden fixture regenerated in the same commit** (`tests/golden/contract_1_1_0.json`).
The diff is +7/−1 lines across four annotation changes (audit correction: first recorded
as "three lines"/three items — `degraded_reasons` also gained a description) — `capabilities` and `omitted_by_reason`
descriptions, and `degraded_reasons` items gaining their enum — with no field added,
removed, renamed or retyped on the wire. `test_contract_schema.py`'s docstring no longer
says "bump CONTRACT_VERSION"; it now explains that the fixture pins `model_json_schema()`
(strictly more than the wire) and points at the documentation-only ruling.

**Mutation-verified** (each mutation applied to a committed tree, then reverted): the
handler dropping `sanitizer_revision` → 2 failures; the 429 body growing a field → 1;
a raise site using an undocumented code → 1; the 413 declaration moved off its status →
2; a `degraded_reasons` member outside the Literal → 2. Clean tree back to 25 passed.

**Left for siblings:** `/metrics` is deliberately untyped and undeclared here (US-005);
`FastAPI(title=..., version=CONTRACT_VERSION, description=...)` still reads
`version="0.1.0"` (US-005); no `contract/` directory yet (US-002).

### US-005 — Typed `/metrics` + app metadata (2026-09-11)

**Shipped:** branch `story/ct-us-005-typed-metrics`. Suite 1427 → **1447** green
(+20, all in the new `tests/test_contract_metrics.py`); `ruff check`, `ruff format
--check`, `uv run pyright` and `actionlint` all zero. `CONTRACT_VERSION` unchanged at
`1.1.0`.

**`sanitizer_revision` did NOT rotate** —
`8b1b7f78e85f733ef3b8ace5194632a8cf92410131b2c1af995c456f20196d7c` before and after,
re-derived at both ends. Nothing this story touched is in `_REVISION_SOURCES`:
`retrieval_app.py` is not one of the eight hashed `pipeline/` files, and neither is
`models.py`, which was not opened. US-001 keeps the spec's one acknowledged rotation.

**Zero wire bytes moved, and that is the assertion rather than the claim.** `/metrics`'
full body was captured before the change and compared after: byte-identical, including
key order at every depth. The committed guard is stronger than a captured string, though,
because a fixture goes stale —
`test_served_metrics_are_the_handlers_dict_serialized` calls the handler **directly**
for the dict it builds and compares the served bytes against
`json.dumps(expected, ensure_ascii=False, allow_nan=False, separators=(",", ":"))`,
which is exactly `JSONResponse.render`'s call. That comparison never passes through the
model, so it catches a rename, a retype, a dropped counter *or a reorder at any depth* —
and the reorder case is not hypothetical: mutation 3 below is caught by this assertion
and by nothing else in the suite.

**Six models, in `retrieval_app.py` beside `HealthResponse`.** `MetricsResponse` plus
`ExtractionMetricsResponse` / `SearchMetricsResponse` / `RetrieveMetricsResponse` /
`CacheMetricsResponse` / `ModelMetricsResponse` — the `*Response` suffix is load-bearing,
since `ExtractionMetrics`, `SearchMetrics`, `RetrieveMetrics`, `CacheMetrics` and
`ModelMetrics` are all live counter classes. All six carry `extra="forbid"`. The handler
still **builds a dict** and lets FastAPI validate it on the way out; that ordering is the
design, not an accident of minimal diffing. A permissive response model silently
*filters* an unmodeled key and answers 200 — measured on the locked FastAPI in
`test_a_permissive_response_model_would_have_dropped_it_instead`, which is the
counterfactual that makes the forbid worth its cost. Ours raises instead
(`test_an_unmodeled_counter_fails_loudly`), and that new failure mode is written up in
`kit_tools/docs/GOTCHAS.md` so the next person to add a counter meets it in a doc rather
than in a 500.

**The cgroup keys are flat, in the wire and now in the schema.** `**_cgroup_memory_
snapshot()` still splats `cgroup_memory_current_bytes` / `cgroup_memory_max_bytes` /
`oom_proximity_ratio` into `extraction`, and `ExtractionMetricsResponse` declares them as
three flat scalar-or-null fields. `tests/test_app.py`'s
`test_metrics_expose_saturation_and_oom_proximity` was **not touched** — it is the fossil
guard and stays exactly as it was; the new module adds the schema half (`$ref`-free
properties, and `verdicts` as the section's only object-valued member, so no `memory: {}`
can appear).

**App metadata.** `FastAPI(title="Forage", version=CONTRACT_VERSION, description=…)`
replaces `title="Poppy Retrieval Sidecar", version="0.1.0"`. The description states the
no-auth/private-network posture and names `/docs`, `/redoc` and `/openapi.json` as part
of the unauthenticated surface, so a reader meets the precondition before the first route.
`pyproject.toml`'s `version = "0.1.0"` is untouched — packaging metadata, independent by
`docs/releases.md`'s rule.

**What the metadata swap does and does not disturb, reconciled honestly.** US-001's
declaration-map tests (`test_declared_error_statuses_match_the_emission_map`,
`test_each_declaration_points_at_its_mirror_model`, the enum and component assertions)
all read `app.openapi()["paths"]` and `["components"]`; **none** of them reads `info`, so
none needed a change and none was changed. The golden fixture is likewise untouched:
`tests/test_contract_schema.py` pins `model_json_schema()` for four models
(`HealthResponse`, `SearchResponse`, `RetrievedContent`, `ExtractedContent`) and this
story changed none of them — the new metrics models were deliberately **not** added to
`_SCHEMA_MODELS`, because US-002 is about to freeze the whole document and a second
partial pin would be two sources for one fact. What did change in `/openapi.json` is the
`info` block (title, version, a description where there was none) and the `/metrics` 200
schema, which was `{"additionalProperties": true, "type": "object", "title": "Response
Metrics Metrics Get"}` and is now a `$ref`. Documentation growth, no response body moved.

**For US-002's diff review — what `/metrics` contributes to the document.** Six new
components: `MetricsResponse` (`additionalProperties: false`; six required properties in
order `contract_version`, then `$ref`s to `extraction`, `search`, `retrieve`, `cache`,
`model`) and the five section components, each `additionalProperties: false` with every
property required and described. `paths./metrics.get.responses.200` becomes
`{"$ref": "#/components/schemas/MetricsResponse"}`. `info` becomes
`{"title": "Forage", "description": "<posture paragraph>", "version": "1.1.0"}`. The
component count goes 19 → 25; the generated document is ~38 KB (38,414 bytes as
`json.dumps(..., sort_keys=True)`). Nothing else in `paths` moves except `paths./metrics.get.description` (the handler docstring grew and FastAPI publishes it as the operation description — verifier precision 2026-09-11; every other path item is byte-identical).

**Docs.** `docs/configuration.md`'s "Deployment posture" gains a second table for the
three documentation endpoints (each `none`), a note that Swagger also registers the inert
`/docs/oauth2-redirect`, and the reason they matter (`/docs` is a working client for an
unauthenticated SSRF-capable service). "Verifying a running instance" gains the
`/openapi.json | jq -r .info.version` check. That doc is now **mechanically** tied to
reality: `test_every_served_path_is_acknowledged_in_the_posture_doc` walks `app.routes`
and fails if a served path is missing from the section — which is the guard the
documentation endpoints needed, since nothing in this repo declares them.

**Mutation-verified** (each applied to a committed tree, then reverted; clean tree back to
1447):

| Mutation | Result |
|---|---|
| cgroup keys nested under `"memory"` instead of splatted | **20 failures**, incl. `test_app.py`'s fossil guard |
| `retries_scheduled` dropped from `ModelMetricsResponse` | **21 failures**, incl. the dataclass↔model field tie |
| two `CacheMetricsResponse` fields swapped | **1 failure** — the handler-dict serialization assertion, and only it |
| `title`/`version` reverted to the placeholders | **2 failures** |
| the `/redoc` row deleted from `docs/configuration.md` | **2 failures** |
| a sixth counter added to the handler only | **20 failures** (the endpoint 500s) |

**Left for siblings:** no `contract/` directory yet, and `/openapi.json` is generated
per-request rather than exported (US-002); the 400→413 correction and the two-semver
rulings are still spec text, not `GOVERNANCE.md` (US-003).

### US-002 — Generated contract file + pytest drift check (2026-09-11)

**Shipped:** branch `story/ct-us-002-contract-export`. Suite 1447 → **1465** green
(+18, all in the new `tests/test_contract_export.py`); `ruff check`, `ruff format
--check`, `uv run pyright` and `actionlint` all zero. `CONTRACT_VERSION` unchanged at
`1.1.0`. Zero production code changed — `retrieval_app.py`, `models.py` and `pipeline/`
are byte-identical to US-005's tree; this story only *reads* the app.

**`sanitizer_revision` did NOT rotate** —
`8b1b7f78e85f733ef3b8ace5194632a8cf92410131b2c1af995c456f20196d7c` before and after,
re-derived at both ends. Nothing this story touched is in `_REVISION_SOURCES`, and
`git diff --stat main -- pipeline/ models.py` is empty. US-001 keeps the spec's one
acknowledged rotation.

**Three artifacts, one command.** `uv run python -m scripts.export_contract` writes
`contract/openapi.yaml` (45,089 bytes, 1,335 lines), `contract/openapi.yaml.sha256`, and
`tests/fixtures/contract/unregenerated_openapi.yaml` (the self-test twin, below). They are
only consistent as a set, so the script writes all three or none, and `--check` verifies
all three without writing. The drift gate itself is **not** the CLI: the tests import
`drift_report()` and call it, so it runs in spec 2's `test` job, under the orchestrator's
regression gate, and on every local `uv run pytest` with no lane for anyone to forget.

**The document matches US-005's preview exactly** — 5 paths, 25 components, 38,414 bytes
as `json.dumps(..., sort_keys=True)`, `info.version` `1.1.0`, `/metrics.get` carrying the
`description` the grown handler docstring produced. The 17-code vocabulary is visible in
the artifact as well as in the models: sweeping every `error` property in the committed
YAML for its `const`/`enum` members returns exactly **17** distinct codes.

**The canonical form, and an honest account of which parts are load-bearing.** Four
settings are pinned in `scripts/export_contract.py` (JSON round-trip → no-alias dumper →
`sort_keys=True` → `width=88, indent=2, allow_unicode=True`), and the docstring explains
each. Mutation testing then made a more precise claim possible than "all four are
required", which would have been false:

- **Sorting is produced twice.** `json.dumps(sort_keys=True)` and the dumper's
  `sort_keys=True` each fully sort the document; removing either alone moves no byte.
  Removing **both** does, and fails the drift test plus
  `test_every_mapping_is_key_sorted_at_every_depth` — which reads the *render*, not the
  committed file, precisely so it pins the renderer rather than re-checking a file the
  drift test already covers.
- **The anti-alias machinery is precautionary today.** With *both* defences removed the
  document still renders anchor-free: FastAPI + pydantic 0.141.1/2.x happen not to share
  a non-scalar sub-object. It is kept because the failure it guards against — `&id001`
  appearing in a published contract after a dependency bump — is the silent kind, and the
  cost is two lines. Recorded rather than dressed up as a fix for something observed.
- **`width=88` is a deliberate trade.** The artifact reads like the rest of the tree and
  keeps its em dashes, at the price that editing one word of a long description reflows
  its block, and that a PyYAML folding change will move bytes no API change moved. Per the
  spec's edge case, that *is* the intent: a dependency bump surfaces as a reviewable regen
  diff rather than as silence.

**Determinism is measured, not asserted.** `test_the_render_survives_a_different_hash_seed`
renders the contract in two subprocesses with `PYTHONHASHSEED=0` and `=1` and compares
both to the in-process render. The seed can only be set before interpreter start, so this
costs two ~1.2 s subprocesses; it is the only test that could catch a set iterated into a
list, which is the one nondeterminism class that would make the drift check a coin flip
between two CI runs. (The children also get `HF_HUB_OFFLINE=1` — the suite's socket guard
does not reach a subprocess, and `kit_tools/docs/GOTCHAS.md` documents the hub call that
would otherwise be possible.)

**The anchor is `sha256sum`-format with the basename, not the repo path.**
`<64 hex>  openapi.yaml\n`, so `sha256sum -c openapi.yaml.sha256` verifies unchanged from
the repo's `contract/`, from a directory of downloaded Release assets, and from
`/app/contract/` in the image — the three contexts US-004's three-way check runs in, where
the paths differ and the bytes must not. It is checked against the **committed file's own
bytes**, not against a fresh render: a rendered-vs-rendered check would pass on a tree
where both files were stale together.

**The self-test twin, and why it is a full copy.** The AC asks for the drift check's
failure case to be *committed*, not demonstrated once by hand. The twin is
`contract/openapi.yaml` with `Extract422ErrorResponse.sanitizer_revision` removed — the
field Poppy hard-rejects a 422 without — which is exactly the file you would have on disk
after adding that field to the model and forgetting to regenerate. Three properties make
it a real test rather than a ritual:

1. it is **generated** by the same command as the contract, so it cannot fall behind;
2. `test_the_twin_differs_from_the_contract_in_one_documented_way` computes a structural
   diff and requires it to be exactly `…Extract422ErrorResponse.properties.sanitizer_revision`
   and `…required` — without it, a twin that rotted into an unrelated file would keep the
   "is it caught?" test green for the wrong reason;
3. `unregenerated_twin()` **raises** if its target property is gone, because a twin that
   silently came back identical to the contract would hollow the whole check out. Mutation
   4 below is that failure mode, and three tests catch it.

The cost is a 1,334-line duplicate in `tests/fixtures/` and a doubled diff on every future
regen. Paid deliberately: a committed failure case that is a whole real document is the
only version that proves the checker rejects what it must.

**`/extract` while `extract_route_enabled: false` — verified, and then strengthened.**
The route is registered at import and only the handler gates (404), so the document is
config-independent. Rather than assume it, two tests measure it: `/extract` is in `paths`
with the gate off, and toggling the gate moves **no byte** of the render. A contract that
appeared and disappeared with a config file would be worthless to pin.

**Mutation-verified** (each applied to a committed tree, then reverted; clean tree back to
1465):

| Mutation | Result |
|---|---|
| a response model's field description reworded, nothing regenerated | **3 failures** — contract drift, twin drift, and the one-documented-way check |
| `contract/openapi.yaml` hand-edited | **3 failures** — drift plus both anchor assertions |
| the twin edited somewhere other than its documented difference | **2 failures** |
| the twin replaced by a copy of the contract (a hollow self-test) | **3 failures**, incl. "the checker catches an un-regenerated change" |
| `CONTRACT_VERSION` bumped to `1.2.0`, nothing regenerated | **5 failures** — drift ×2, the file↔`CONTRACT_VERSION` tie, the one-documented-way check, and `test_contract_schema.py`'s missing golden |
| `sort_keys=False` in the dumper *only* | 18 passed — the JSON round-trip already sorted it |
| the no-alias dumper removed, or the round-trip removed, or both | 18 passed, and the render is still anchor-free |
| both sorting mechanisms removed | **3 failures** |

**Handoffs, and one AC landed early.**

- The **Dockerfile `COPY` was deliberately not added** — US-004 owns in-image shipping, and
  the COPY list is filename-enumerated by `CLAUDE.md` invariant 3. Worth knowing before
  that story starts: `.dockerignore` excludes `tests/`, `kit_tools/` and `scripts/` but
  **not** `contract/`, so the directory is already in the build context and US-004's change
  is one `COPY contract/ /app/contract/` line plus its `tests/test_dockerfile.py` guard.
- US-004's AC-1 half "the drift pytest also asserts the committed anchor matches the
  committed file" is **already landed here**
  (`test_the_committed_anchor_is_the_sha256_of_the_committed_contract`, plus
  `test_the_anchor_is_sha256sum_verifiable_as_committed`). Committing an anchor in US-002
  that nothing checked until US-004 would have been a two-story gap in the trust root. Read
  that AC as verify-not-implement.
- US-003 inherits the bump checklist's target: the regen command is
  `uv run python -m scripts.export_contract` and it writes three files that must be
  committed together. `kit_tools/testing/TESTING_GUIDE.md`'s Rules section already states
  it; GOVERNANCE.md should carry the consumer-facing half (verify the copy you fetched
  against `contract/openapi.yaml.sha256`, never against another copy).

**Docs touched.** `kit_tools/testing/TESTING_GUIDE.md` (counts 1447 → 1465, 28 → 29 files,
the new module's row, the twin fixture's row, the regen rule, and the mapping — where
`retrieval_app.py`, `models.py` and `pipeline/contract.py` all gained
`tests/test_contract_export.py`, which is the point: the three files that can move the
document must run the test that notices). `kit_tools/arch/CODE_ARCH.md` (the `contract/`
directory, the script's Key Modules row, the `scripts/` line count). `kit_tools/SYNOPSIS.md`
(test count, `contract/` and `scripts/` rows). `tests/fixtures/README.md` (what the twin is
and why it is generated). `CLAUDE.md` invariant 4 gained the mechanical consequence: the
frozen surface is a file now, both artifacts are generated, and the regen command is named.

### US-003 — Semver governance, PR template, SECURITY.md (2026-09-11)

**Shipped:** branch `story/ct-us-003-governance`. Suite 1465 → **1532** green (+67: 41 in
the new `tests/test_governance_docs.py`, 26 added to `tests/test_ci_workflow.py`);
`ruff check`, `ruff format --check`, `uv run pyright` and `actionlint` all zero.
`CONTRACT_VERSION` unchanged at `1.1.0`.

**`sanitizer_revision` did NOT rotate** —
`8b1b7f78e85f733ef3b8ace5194632a8cf92410131b2c1af995c456f20196d7c` before and after,
re-derived at both ends. Nothing this story touched is in `_REVISION_SOURCES`: no file
under `pipeline/` was opened, and `git diff --stat main -- pipeline/ models.py
retrieval_app.py` is empty. US-001 keeps the spec's one acknowledged rotation, and this
story is the third in a row to leave it alone.

**Three documents, and where each one lives is part of the design.**
`contract/GOVERNANCE.md` (336 lines) sits beside the artifact it governs, so a consumer who
vendors `openapi.yaml` from the git tag gets the rules in the same directory.
`SECURITY.md` (116) is at the repo root because GitHub renders it as the repository's
security policy only from there. `.github/pull_request_template.md` (65) is the short form
of the same checklist, and it is the only one of the three a contributor is *made* to read.

**The release-body mechanization, reconciled against what the body actually said.** The
existing `publish` job's Release step wrote a five-line heredoc — image, digest, platforms,
and the gate-chain sentence — and carried **no contract line at all** (read off the live
`v0.9.0-rc` / `v0.9.3-rc` bodies with `gh release view`, not assumed from the workflow
source). So "assert the body contains `contract: X.Y.Z`" could not be a pure assertion:
there was nothing to assert on. It became three steps, on `v*` tags only:

| Step | Does |
|---|---|
| `Read the contract version from the tagged tree` (`id: contract`) | parses `CONTRACT_VERSION` out of the tagged commit's `pipeline/contract.py`, validates it as a semver, publishes it as a step output |
| `Create the GitHub Release` | writes `contract: ${CONTRACT_VERSION}` into the notes from that output |
| `Assert the Release body advertises the tagged tree's contract version` | `gh release view --json body`, strip CRs, anchored `grep -qE "^contract: ${escaped}$"`, `exit 1` on a miss |

Emitting and asserting off **one** value in **one** job is the whole of the mechanization
the round-2 finding asked for: a human release-note line was the last unmechanized
integrity claim, and two independent copies would just have been two of them. The
assertion reads the *published* body rather than the local `notes` variable — checking the
variable would restate the heredoc, not verify what a consumer sees. Three details are
load-bearing and each is commented in place: `tr -d '\r'` (the API returns CRLF bodies and
`$` will not match past a carriage return), the dot-escaping (`1.1.0` unescaped also
matches `1a1a0`), and `|| true` on the parse (under `set -euo pipefail` a `grep` miss or a
`head` SIGPIPE would kill the step before its own error message ran).

**No new permission was granted.** The job already carries `contents: write` for
`gh release create`, and `gh release view` needs nothing more — the AC's "named" half is a
*verification*, and `test_the_job_can_create_and_read_the_release_it_asserts_on` records it
as one. Cross-fire: all three steps carry the same `if: startsWith(github.ref,
'refs/tags/v')` as the Release itself, inside a job whose own `if:` already excludes
`searxng-v*`; the guard test *evaluates* each step's condition against `v1.0.0`,
`v0.9.0-rc`, `main` and `searxng-v0.1.0` rather than reading it, and a separate test asserts
the companion lane grew no contract line of its own.

**The judgement calls the sources left open, and how they were closed.**

- **(a2) is classified MAJOR.** GOTCHAS and the US-001 notes both say correcting 400 → 413
  is "a wire change requiring a bump" without naming the class. Under GOVERNANCE's own
  table — "a status code a client observes changes" — that is the MAJOR row, and saying so
  is the difference between a document that answers the question and one that defers it.
  The current state (both statuses declared, the 413's description saying it is shadowed)
  is recorded as **no bump**, which is what US-001 shipped under.
- **"Pre-freeze / post-freeze" needed a boundary.** Two of the six required examples answer
  differently on either side of it, so the document defines it once: the freeze is the first
  release that publishes `contract/openapi.yaml` as an artifact (image `v1.0.0` serving
  contract `1.1.0`, US-004). Before it, no consumer held a copy to diff, so a doc-only change
  moved nothing — ruling (a). After it, the document is an artifact with an anchor, and a
  doc-only change is a PATCH.

**Two audit findings, both measured rather than inferred.**

1. **Private vulnerability reporting was OFF.** `gh api repos/WashingBearLabs/Forage/
   private-vulnerability-reporting` returned `{"enabled": false}` — and this repository
   publishes no security email and has no PGP key, so SECURITY.md would have advertised a
   channel that 403s the first person to try it. Enabled it (`PUT` on the same endpoint,
   re-read as `{"enabled": true}`) and dated the claim in the file. A policy naming a dead
   channel is worse than no policy: it converts a would-be private report into a public
   issue.
2. **`docs/releases.md` said required status checks were "Not registered".** They are:
   `gh api repos/WashingBearLabs/Forage/branches/main/protection` lists exactly `lint`,
   `typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke`, with PRs required
   (`required_approving_review_count: 0`) and force-pushes and deletions blocked. The
   section described the pre-flip deferral and was never updated at US-008. Corrected with
   the measured table — and the PR template's "six checks required on `main`" sentence is
   now tied to the workflow's own `publish` `needs:` by test, so the document cannot fall
   behind a seventh gate.

**Doc-count discipline, stated with its convention.** Measured, not incremented:
**29 `test_*.py` modules** (32 Python files under `tests/` once `conftest.py`, `fakes.py`
and `__init__.py` are counted), **1532 tests**, `test_ci_workflow.py` 209 → **235**.
`SYNOPSIS.md` also carried three stale rows from earlier stories — "Repo visibility:
**Private**", a CI row listing three jobs of ten, "Published image: **None yet**" — and its
`docs/` row was missing `releases.md`, `weights.md` and `searxng.md`. Fixed against measured
reality (`gh api`, `gh release list`, `ls`) and marked as audit corrections in place, since
silently re-stating them would leave the next reader unable to tell a fix from a claim.

**Mutation-verified** (each applied to the committed tree, then reverted; clean tree back to
276 in the two modules):

| Mutation | Result |
|---|---|
| the assertion step deleted entirely | **10 failures** |
| the regex unanchored (`contract: ${escaped}`) | **2 failures** |
| the version's dots left unescaped | **1 failure** |
| the body emits `Contract:` while the grep still reads `^contract: ` | **2 failures** — including the one-token tie |
| the version hardcoded as a literal in the notes | **3 failures** |
| the assertion step's `if:` removed (fires on `main` and `searxng-v*` too) | **4 failures** |
| the failure branch degraded from `exit 1` to a warning | **1 failure** |
| GOVERNANCE's stated contract version bumped, code untouched | **1 failure** |
| a required check dropped from the PR template's sentence | **1 failure** — *after* the guard was tightened; see below |
| a worked-example row deleted | **2 failures** |
| the PR template invents a seventh required check | **1 failure** |
| the `contract:` line dropped from the body | **2 failures** |
| SECURITY.md's reporting URL replaced with an email | **1 failure** |

**One guard escaped its own mutation and was rewritten.** The first version of
`test_it_lists_exactly_the_checks_the_workflow_requires` asked whether each gate's name
appeared *anywhere* in the PR template — and stayed green with `secret-grep` deleted from
the required-checks sentence, because the invariants section also names it. It now parses
the sentence itself and compares the backticked names (and the number word) against the
jobs `publish` hangs off. Same lesson this file already records for bash: checking a
document's vocabulary is not checking its claim. Committed as its own
`test(...)` commit so the escape is visible in the history.

**Handoffs to US-004.**

- `contract/GOVERNANCE.md` sits in the directory US-004 will `COPY contract/ /app/contract/`
  into the image. That is deliberate — the governance travels with the artifact — and it
  changes nothing about the three-way sha256 check, which is over `openapi.yaml` alone.
- GOVERNANCE's **Consumers** table already lists the Release-asset and in-image routes,
  marked *"with US-004"*. Flip those two rows when the assets land, and add the vendoring
  specifics the spec assigns to that story.
- **The release-body assertion has never run.** It fires only on a `v*` tag, and the next
  one is US-004's `v1.0.0` cut. The step's shell was exercised locally against a rendered
  copy of the real heredoc (match, plus rejection of `1a1a0` and `1.1.0-draft`), and
  `actionlint` is clean — but the first live proof is that tag push, and it is worth
  watching the step rather than only the tags.
- If that assertion ever fails, the recovery is in `docs/releases.md`: the Release exists
  and is wrong, `gh release create` refuses a second one on the same tag, so fix it with
  `gh release edit` or delete the Release and re-run.

### US-004 — Contract distribution, build half (2026-09-11)

**Shipped:** branch `story/ct-us-004-distribution`. Suite 1532 → **1610** green (+78,
all added to four existing modules: 36 in `tests/test_ci_workflow.py`, 29 in
`tests/test_contract_smoke.py`, 11 in `tests/test_dockerfile.py`, 2 in
`tests/test_governance_docs.py`); `ruff check`, `ruff format --check`, `uv run pyright`
and `actionlint` all zero. `CONTRACT_VERSION` unchanged at `1.1.0`. **The `v1.0.0` tag is
NOT cut** — it is the owner gate below.

**`sanitizer_revision` did NOT rotate** —
`8b1b7f78e85f733ef3b8ace5194632a8cf92410131b2c1af995c456f20196d7c` before and after,
re-derived at both ends. `git diff --stat main -- pipeline/ models.py retrieval_app.py`
is empty: no service source was opened. US-001 keeps the spec's one acknowledged
rotation, and all four siblings have now left it alone.

**AC-1's second clause was landed by US-002 and is verified, not re-implemented.** The
anchor↔file tie is `test_the_committed_anchor_is_the_sha256_of_the_committed_contract`
plus `test_the_anchor_is_sha256sum_verifiable_as_committed`, both comparing against the
*committed bytes* rather than a fresh render. Read, re-run, left alone.

**What ships, and what checks it.** The Dockerfile gains one line —
`COPY contract/ /app/contract/`, the directory whole, so `GOVERNANCE.md` travels with the
artifact it governs (the anchor still covers `openapi.yaml` alone). The publish job
attaches `openapi.yaml` and `openapi.yaml.sha256` to every `v*` Release, from
`gh release create` itself rather than a later upload, so a Release without them is a
failed step instead of a quietly incomplete Release. Then both copies are *checked*:

| Copy | Checked by | What it asserts |
|---|---|---|
| in-image `/app/contract/` | `smoke`, every push and PR | the pair is readable; the in-image anchor **is** this tree's committed anchor; the document hashes to it; `info.version` equals the running container's `/health.contract_version` |
| Release assets | `publish`, on `v*` tags | the downloaded anchor is the anchor committed at the tag, then `sha256sum -c` on the downloaded pair |
| git tag | the drift pytest, every run | US-002's anchor↔file tie |

That is the three-way equality, mechanized on two of three legs rather than recorded by
hand once. The comparison against the *committed* anchor is the load-bearing half of the
Release check: a tampered document and anchor replaced together verify perfectly against
each other.

**The smoke extension went into the script, not into bash** (the `searxng_smoke.py`
precedent). `contract_smoke.py --image <ref>` runs
`docker run --rm --entrypoint cat <ref> /app/contract/openapi.yaml` — the exact command
GOVERNANCE hands consumers — through an injected runner, so
`tests/test_contract_smoke.py` drives all of it with no container and no daemon. The
workflow step gained four words; no path and no version is restated in YAML, and
`test_the_in_image_contract_path_is_not_restated_in_bash` keeps it that way. Two smaller
choices worth knowing: `--image` is **opt-in**, because `docs/releases.md` tells arm64
consumers to run this script against their own container and they may not have the image
locally — which is why there is a separate guard that CI actually passes it (a green
smoke that silently skipped the image would be the worst outcome available); and a
`/health` body that yields no `contract_version` produces a failure rather than a skipped
comparison.

**Live-verified against a real container**, not only in tests. A container from a locally
built image: `/health` 200, degraded, `sanitizer_revision 8b1b7f78…`, and the smoke
PASSED with the in-image anchor reading
`00b1dbaa5971895e7e7f1532f52ab46026df5e789ee5572380fd822f6bb295c0  openapi.yaml`,
equal to the committed one. The negative direction too: pointed at an image built from
`main` (before the COPY), it fails with two violations naming `COPY contract/` and
`.dockerignore`.

#### The cold-cache blocker is CLOSED — measured, not asserted

GOTCHAS' "A cold-cache publish fails its own parity gate" was a spec-4 blocker on
`v1.0.0`, and the brief was to attempt the durable fix and report honestly either way.
It works, but **not** in the shape the brief guessed: `SOURCE_DATE_EPOCH` +
`rewrite-timestamp=true` is necessary and **not sufficient**.

Method: two `--no-cache` builds of the real image from two exports of the same tree with
different file mtimes — which is what two CI checkouts of one commit are — comparing
`RootFS.Layers` and then, for any layer that differed, every entry inside both layer
tars.

| Configuration | Layers identical |
|---|---|
| neither half | 7 of 19 |
| `SOURCE_DATE_EPOCH` + `rewrite-timestamp=true` alone | 16 of 18 (pre-COPY tree) |
| both halves | **19 of 19** |

The two survivors of the middle row are the finding. `rewrite-timestamp` rewrites the
timestamps in a layer tar's *headers*; a timestamp written *inside* a file is out of its
reach, and this image wrote two kinds:

- the apt layer: exactly four files — `/var/log/apt/history.log`, `/var/log/apt/term.log`,
  `/var/log/dpkg.log`, `/var/cache/ldconfig/aux-cache`;
- the build-time import check: **579** `.pyc`, each embedding its source's mtime.

So the fix is one change in two files: the workflow half (all four building jobs compute
one `SOURCE_DATE_EPOCH` from the commit's committer date with a byte-identical script,
and every exporter is written longhand — `type=docker` / `type=image,push=true` — so it
can carry `rewrite-timestamp=true`) and the Dockerfile half (the four files removed in
the same `RUN` as the install; `PYTHONDONTWRITEBYTECODE=1` on the import check).

**Two honest costs, both measured.** The image no longer ships a bytecode cache: the app
imports in ~2.3 s instead of ~0.9 s, once per container start, against a 120 s health
budget. That is a cost of `rewrite-timestamp`, not of dropping the `.pyc` — measured in a
built image, the shipped `.pyc` record the build clock while their sources read
`SOURCE_DATE_EPOCH`, so the cache was already void and the choice was between dead files
that break the parity gate and no dead files. The future optimisation, if it ever
matters, is hash-based `.pyc` (PEP 552) over the modules the app actually imports. And
reproducibility is bounded by **one UTC day**: `useradd` stamps a day count into
`/etc/shadow` (`poppy:!:20708::::::`), so two builds either side of midnight differ in
that layer. The parity gate compares two builds of one run, so it is unaffected — but a
"same digest next year" claim would be, and is not made. (Reasoned from the field, not
measured across a midnight.)

**What is still unproven:** a live cold publish. Everything above is local plus the
workflow-shape tests, and `publish` never runs on a PR — so the exporter longhand, the
epoch step and the asset assertion all execute for the first time at the `v1.0.0` push.
`actionlint` is clean and the shapes are asserted, but watch the run.

**Mutation-verified** (each applied to the committed tree, then reverted; clean tree back
to 1610):

| Mutation | Result |
|---|---|
| the `COPY contract/` line deleted | **2 failures** |
| the COPY destination moved to `/app/spec/` | **1 failure** |
| `contract/` added to `.dockerignore` | **3 failures** |
| `PYTHONDONTWRITEBYTECODE=1` removed | **1 failure** |
| the apt log scrub removed | **3 failures** |
| `rewrite-timestamp=true` dropped from publish's exporter | **1 failure** |
| publish reverted to the `push: true` shorthand | **2 failures** |
| the epoch taken from `date +%s` instead of the commit | **2 failures** — including the four-jobs-agree tie |
| build-amd64's build step loses its `SOURCE_DATE_EPOCH` env | **1 failure** |
| the two assets dropped from `gh release create` | **2 failures** |
| the Release-asset anchor comparison disabled | **2 failures** |
| the smoke stops passing `--image` | **1 failure** |
| the smoke stops comparing the in-image anchor | **2 failures** |
| the smoke stops comparing versions | **3 failures** |
| GOVERNANCE stops naming the in-image path | **1 failure** |

**One guard was missing and is committed separately.** The first pass tested the script's
in-image checks thoroughly and never asserted that the *workflow* passes `--image`. Since
the flag is opt-in, a workflow edit could have left the smoke green while checking
nothing. `test_smoke_reads_the_contract_out_of_the_candidate_image` closes it, in its own
`test(...)` commit so the gap is visible in the history.

**One shared helper was fixed.** `tests/test_governance_docs.py::_section` treated a
shell comment at column 0 inside a ``` fence as a Markdown heading, silently truncating
the Consumers section at its first code example — so a test of anything after that
example failed for a reason unrelated to the text. It now tracks fences.

**Docs touched.** `contract/GOVERNANCE.md` (the Consumers table's three routes with the
command for each, what CI checks on two of them, and the six-step vendoring procedure —
pin a tag, fetch, verify *before reading*, commit document and anchor together, re-run
the verification in your own suite, record the tag). `docs/releases.md` (the assets, the
mutability caveat, and a new "Reproducible builds" section with the measurements and the
two costs). `kit_tools/docs/GOTCHAS.md` (the cold-cache entry rewritten: the incident and
its cache-scope recovery kept, the fix and its measurements recorded, the bytecode and
UTC-day caveats stated, and the live-cold-publish gap flagged). `CLAUDE.md` invariant 4
(the three routes and which two are checked). `kit_tools/SYNOPSIS.md`,
`kit_tools/arch/CODE_ARCH.md`, `kit_tools/testing/TESTING_GUIDE.md` (counts, the widened
test mapping — `Dockerfile`, `contract/openapi.yaml` and its anchor now also run
`tests/test_contract_smoke.py`).

#### v1.0.0 cut — EXECUTED 2026-09-12 (owner + supervisor)

**Recorded (run 34667821482, tag at `f4c2b16`):**
- Pre-flight: main green on the tagged commit (its sha-publish had already survived the
  new epoch/reproducibility steps live), `export_contract --check` clean,
  `CONTRACT_VERSION` 1.1.0 confirmed, 02:22 UTC — clear of the midnight window.
- The run: **all jobs green, COLD** — no warm-cache ritual — including the three
  never-before-run steps (epoch derivation, release-body `contract:` assertion,
  Release-asset download-back + `sha256sum -c`). The first cold cut was itself the first
  live proof of the reproducibility fix, as the runbook intended.
- **Four-way sha256 equality**: committed anchor = repo file = Release asset
  (self-verifying via `sha256sum -c`) = `docker run --entrypoint cat` of the anonymously
  pulled image — all `00b1dbaa5971895e7e7f1532f52ab46026df5e789ee5572380fd822f6bb295c0`.
- Release `v1.0.0`: `prerelease: false`, body carries `contract: 1.1.0` (the two-semver
  mechanization's first live pass).
- Registry: `latest` exists **for the first time**, beside `1.0` and `1.0.0`.
- Live container: `/health` serves `contract_version 1.1.0`, `cache_backend memory`,
  revision `8b1b7f78…196d7c`.
- This is the artifact `feature-poppy-consume-forage-image` pins.

The last acceptance criterion of this story, and the last of the epic. It is a **tag push
by the owner**, not something an implementer does: it moves `latest` for the first time in
this repository's history, and a `v1.0.0` that goes wrong cannot be re-cut (a registry tag
re-pushed at a different commit leaves consumers holding a digest that no longer matches
— `docs/releases.md`).

**Before tagging**

1. This branch is merged and `main` is green — all six required checks on the merge
   commit. The tagged tree must carry the `COPY contract/`, the Release-asset steps and
   the reproducibility changes, or the tag ships a contract-less v1.0.0.
2. `git switch main && git pull` and confirm `git log -1` is the commit you intend.
3. `uv run python -m scripts.export_contract --check` — clean, i.e. the tagged tree's
   contract, anchor and twin are current.
4. Confirm the contract version you are about to advertise:
   `grep CONTRACT_VERSION pipeline/contract.py` → `1.1.0`. Image `v1.0.0` serving contract
   `1.1.0` is the intended, recorded mapping (GOVERNANCE, "Two semvers").

**The warm-cache ritual is no longer required.** It was: cut the tag immediately after a
green `main` build so `publish`'s rebuild hits a warm cache. The builds are reproducible
now, so a tag cut at any time should pass — and the first cold cut is also the first live
proof of that, which is a reason to watch the run rather than to wait for a warm one. If
`publish`'s "Verify the published amd64 image is the gated filesystem" step fails anyway,
**do not re-run blindly**: that failure is now a finding — with ONE exception: a run
straddling UTC midnight can trip the gate on the `/etc/shadow` day-count layer
(releases.md § the known bound); if the failure lands within ~10 minutes of 00:00 UTC,
check the passwd-layer timestamps first and re-run — only a non-midnight failure is a
finding. Read the recovery in
`kit_tools/docs/GOTCHAS.md` (delete the `index-publish-*` GHA cache entries so publish
falls through to the gated scope) and treat the tag as withdrawn per `docs/releases.md`.

**The cut**

```bash
git tag v1.0.0
git push origin v1.0.0
gh run watch                     # or: gh run list --limit 3
```

**Watch four steps in `publish`, in this order** — three of them have never run:

| Step | First run? | What a failure means |
|---|---|---|
| Verify the published amd64 image is the gated filesystem | no (has passed warm) | the reproducibility fix did not hold cold — a finding, not a flake |
| Read the contract version from the tagged tree | **yes** | `CONTRACT_VERSION` unparseable at the tag |
| Assert the Release body advertises the tagged tree's contract version | **yes** (US-003 wrote it; no tag since) | the body and the tree disagree — `gh release edit`, or delete the Release and re-run |
| Assert the Release assets verify against the committed anchor | **yes** | the assets are not this tag's contract — `gh release upload --clobber`, or delete and re-run |

**After the run: the three-way sha256 verification.** Run all three from the same tag and
compare to `contract/openapi.yaml.sha256`, which is the answer, not one of the three:

```bash
# 0. the answer — the committed anchor at the tag
git show v1.0.0:contract/openapi.yaml.sha256

# 1. the repository copy
git show v1.0.0:contract/openapi.yaml | shasum -a 256

# 2. the Release assets (verify exactly as a consumer would)
mkdir -p /tmp/v100 && gh release download v1.0.0 --repo WashingBearLabs/Forage \
  --pattern 'openapi.yaml*' --dir /tmp/v100
(cd /tmp/v100 && shasum -a 256 -c openapi.yaml.sha256)

# 3. the image
docker pull ghcr.io/washingbearlabs/forage:1.0.0
docker run --rm --entrypoint cat ghcr.io/washingbearlabs/forage:1.0.0 \
  /app/contract/openapi.yaml | shasum -a 256
```

All four values equal. (`shasum -a 256` on macOS; `sha256sum` on Linux — the anchor's
`sha256sum -c` form works with both, and the basename in it is why the check runs
unchanged in all three directories.)

**Then the release-body line and `latest`:**

```bash
gh release view v1.0.0 --repo WashingBearLabs/Forage --json body --jq '.body' \
  | grep -E '^contract: 1\.1\.0$'          # the mechanized mapping, seen by a human once
gh release view v1.0.0 --json assets --jq '.assets[].name'   # openapi.yaml + .sha256
docker buildx imagetools inspect ghcr.io/washingbearlabs/forage:latest --format '{{.Manifest.Digest}}'
docker buildx imagetools inspect ghcr.io/washingbearlabs/forage:1.0.0  --format '{{.Manifest.Digest}}'
```

`latest` **exists for the first time** at this tag (every previous release was a `-rc`,
which moves neither `latest` nor the `X.Y` alias) and must resolve to the same digest as
`1.0.0`; `1.0` should too. A `latest` that appeared pointing anywhere else is the one
outcome worth stopping for.

**What to record** (in this section, replacing this checklist):

- the tag, the run URL, and the published digest;
- the four sha256 values, shown equal;
- that the Release carries both assets and the body's `contract: 1.1.0` line;
- that `latest`, `1.0`, and `1.0.0` resolve to one digest;
- **whether the publish rebuild was cold or warm** — the first cold pass is the evidence
  the reproducibility fix works in CI, and the first warm one leaves that still unproven;
- anything the three never-run steps did that the workflow-shape tests could not predict.

Then tick this story's remaining ACs and close the spec: `epic_final: true`.

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
