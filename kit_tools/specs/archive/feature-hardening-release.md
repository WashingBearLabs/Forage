<!-- Template Version: 2.5.0 -->
---
feature: hardening-release
status: completed
session_ready: true
depends_on: [hardening-promptguard-86m]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 8
epic_final: true
execution_order: [US-001, US-002, US-004, US-003, US-005]
created: 2026-09-19
updated: 2026-09-23
completed: 2026-09-23
---

# Feature Spec: Validation-422 Tightening + Contract 1.3.0 Close-Out + v1.2.0 Release Cut

> **Spec 8 (final) of `epic-forage-hardening`.** Tighten the one wire shape this epic still owes
> (the request-validation 422 body stops echoing the caller's bytes — ruling 19 as corrected in
> round 5: an expedited **MINOR** under GOVERNANCE Example 6 step 1, the `input`/`ctx`/`url` keys
> kept for one minor release with the fixed value `"[redacted]"` and dropped at the next MINOR,
> because the shipped contract documented those keys; the story owns its own anchor refresh), **close the 1.3.0 contract window** that spec 1
> US-004 opened (rulings 5, R30, R36: the docstring record, the mechanical coverage sweep, the frozen
> golden), do the **pre-release bookkeeping** (US-004: version and release fan-out swept **by value**
> for both moving strings, compose pins, suite counts, release-notes draft), then **cut and publish
> `v1.2.0`** (US-003, the owner gate — ruling 20) and **verify it from the tagged checkout and write the
> handoff record** (US-005, owner-executed — ruling R37), with a third smoke against `compose/full.yml`
> so the cache-integrity posture Poppy pins is witnessed on the released image (R30). Binding: rulings
> 5, 6, 19/R19, 20, R30, R33, R36, R37, R39; `contract/GOVERNANCE.md`'s bump procedure (`:297-330`);
> the `v1.1.0` runbook as executed (`kit_tools/specs/archive/feature-search-release.md` US-002/US-003
> Implementation Notes); rulings R41, R42, R43. Validation rounds 1–5 (2026-09-19) applied — see
> Clarifications (round 5 is the final, un-reviewed close-out; its unapplied warnings are listed in
> § Known risks).
>
> **Line numbers** in the hints were measured before specs 1–7 executed; those specs add rulings to
> GOVERNANCE.md, fields to `models.py` and sentences to SECURITY.md, so an implementer anchors by the
> quoted text or symbol, never by the offset (a mismatch is not a spec defect).

## Overview

Specs 1–7 changed the wire only additively, each appending a line to the held `1.3.0` docstring entry,
appending its golden-visible field to `_EXPECTED_ONE_THREE_ZERO_DIFF` and re-creating
`tests/golden/contract_1_3_0.json` in place (ruling R36). Nothing is published until this spec cuts
`v1.2.0`. Five stories, in execution order `US-001 → US-002 → US-004 → US-003 → US-005`:

- **US-001 (code, autonomous)** — an app-level `RequestValidationError` handler that re-emits the
  documented `{loc, msg, type}` trio plus, for the 1.3.0 window only, `input`/`ctx`/`url` with the
  fixed value `"[redacted]"` (Example 6 step 1: the keys survive one release, the reflector does
  not), capped at `_MAX_VALIDATION_ERRORS` entries.
  Today there is no such handler (`retrieval_app.py:1410` registers only `PipelineError`), so FastAPI's
  stock body reaches the wire with pydantic's `input` (and sometimes `ctx`/`url`) — an unbounded
  reflector (`kit_tools/arch/SECURITY.md:174`). The shipped `ValidationErrorDetail` description
  (`contract/openapi.yaml:1271-1278`, in the `v1.1.0` Release and image) **says so explicitly** —
  "pydantic adds `input` and sometimes `ctx`/`url`" — so dropping them outright would be a
  documented-behaviour change shipped with no surviving behaviour, Example 6 step 4's exact
  prohibition (salty, round 4). The story therefore takes **step 1**: an **expedited MINOR with a
  compatibility window** — the category GOVERNANCE's own MINOR row names (`contract/GOVERNANCE.md:103`,
  "An expedited security tightening shipped with a compatibility window"; table row 6 at `:148`) —
  in which the three keys stay present with a placeholder value for one minor release, the docstring
  line and `docs/releases.md` state the window and the release that drops them, and the drop is a
  **second MINOR** (no declared property moves: `loc`/`msg`/`type` are the only declared ones, the
  three were admitted by a description and never declared), recorded as the next-lettered GOVERNANCE
  ruling as exactly this two-step **and answered against § "Example 6 in full: expedited security
  changes" step by step** (rounds 3–5: one framing, not "plain MINOR under (b)" beside it). Its
  description edits **move the document and the anchor**, so the story runs the full window block.
- **US-002 (contract close-out, autonomous)** — the `1.3.0` record in its final form, the 1.3.0
  coverage sweep beside the 1.2.0 pair spec 1 already pinned, the golden frozen, the stale
  publication-state sentence removed from the `1.2.0` entry (publication state is not the docstring's
  job — Decisions Made), the rotation recorded.
- **US-004 (pre-release bookkeeping, autonomous, P1)** — **both** moving strings swept by value
  (`1.2.0` the retiring contract version, `1.1.0` the retiring image tag) over the whole tree minus
  `kit_tools/specs/`, with named site lists; the four anchors confirmed; compose pins and
  `_FORAGE_RELEASE_TAG` at `1.2.0` (with the unpublished-tag window bounded); suite counts including the
  per-module rows; the `docs/releases.md` draft naming the digest as the pinnable form.
- **US-003 (the cut, owner gate)** — pre-flight, tag push, `publish` watched, four-way sha256, Release
  body read-back, three-tag digest equality, the two config greps. Execution halts here if the gate
  has not run.
- **US-005 (post-release verification + handoff, owner-executed)** — three smoke runs from the
  `v1.2.0` checkout (degraded, healthy, and `compose/full.yml` key-less and keyed), the credential-free
  pull after `docker image rm`, the whole-tree leak grep, the handoff table, the `docs/releases.md`
  placeholders, the roadmap status flips.

The image tag moves `1.1.0 → 1.2.0` (MINOR feature) while the contract it advertises is `1.3.0` —
the two-semver rule (`contract/GOVERNANCE.md` § "Two semvers, independent"). The string `1.2.0` is
therefore **both** the retiring contract version and the new image tag, and `1.1.0` is the retiring
image tag; every sweep in this spec runs both greps and classifies each hit by context, never by a
bare count.

## Goals

- A request that fails schema validation on `/search`, `/retrieve` or `/extract` (route enabled;
  well-formed multipart with an invalid `extract_mode`) returns a 422 whose every `detail[]` item has
  exactly the keys `loc`, `msg`, `type`, `input`, `ctx`, `url` — the last three each equal to the
  fixed string `"[redacted]"` for the 1.3.0 window (ruling R19 as corrected in round 5) — at most
  `_MAX_VALIDATION_ERRORS` (100) items, whose `msg` is
  pydantic's stock text for the error type and whose `loc` carries no caller-chosen segment — proven
  by tests, including a marker fuzz that **provokes a validator failure** on every request-model field
  (each field's own case produces at least one `detail` entry) and asserts the marker appears 0 times
  in `response.text` — the whole serialized body, so every `msg`, `loc` and `type` (ruling R30/R19
  as corrected in round 4) — and a guard-of-the-guard that turns the fuzz red against an interpolating validator;
  the handler is total (a malformed, non-mapping or non-serialisable error entry still yields a 422,
  never a 500) and no value from the request reaches any logger, including uvicorn's, from the
  request-validation path.
- `CONTRACT_VERSION == "1.3.0"`; the docstring entry is one well-formed bullet naming exactly the
  window's contents (the list in Technical Considerations § "The 1.3.0 window", reconciled against
  what specs 1–7 actually appended); `uv run python -m scripts.export_contract --check` is clean;
  `tests/test_contract_schema.py`'s 1.2.0 → 1.3.0 coverage sweep equals the golden-visible subset of
  that list; no docstring entry carries a publication-state clause.
- Every current-value statement of the contract version reads `1.3.0` and every current-release
  statement reads `v1.2.0` / `forage:1.2.0` (both by-value greps classified with zero unclassified hits;
  history rows, rotation records, contract-provenance phrases and archived specs are expected hits);
  every count word this epic moves (rotations, recorded rulings, secret-grep patterns) is derived from
  its artifact at execution time, never from a literal written into this spec.
- A published multi-arch `v1.2.0` image advertising contract 1.3.0 through the gated lane, `latest`,
  `1.2` and `1.2.0` resolving to one index digest, verified from the `v1.2.0` checkout in both smoke
  modes and brought up from `compose/full.yml` at `1.2.0` keyed and key-less.
- The handoff record (tag, index digest, contract, anchor, tagged commit, run URL, cache-integrity
  posture witnessed, model posture, envelope defaults) in this file's Implementation Notes and
  `docs/releases.md` — or, for the two owner-executed stories, the sanctioned `gate not run` record
  naming the prerequisites.

## User Stories

### US-001: Validation-422 body stops echoing the caller — the trio plus `"[redacted]"` placeholders for one release (ruling 19 / R19)

**Priority:** P1

**Description:** As an operator exposing port 8020 on a private network, I want a malformed request to
be refused without the service echoing the caller's bytes back, so the request-validation 422 cannot
be used as a byte reflector — with the change classified honestly against what the shipped contract
documented.

**Independent Test:** `POST /search` with `{"query": 5, "providers": "x"}` returns 422; every entry in
`detail` has exactly the keys `loc`, `msg`, `type`, `input`, `ctx`, `url`, with `input == ctx == url
== "[redacted]"`; the literals `5` and `"x"` and a distinctive marker appear 0 times in the body;
`assert_mirrors(HTTPValidationError, response)` holds on all three routes **after the three
placeholder keys are stripped** from each item (the module's `_strip_window_keys` helper, which also
asserts each stripped value was `"[redacted]"`, and is deleted with the keys at the next MINOR —
`assert_mirrors` compares `model_dump_json()` to `response.text`, `:139-157`, so it cannot hold on the
raw body while the description-admitted keys are present); the regenerated `contract/openapi.yaml`
differs only in **description text on the validation-422 surface** — `ValidationErrorDetail`
(`retrieval_app.py:806-811`; the required edit: its "pydantic adds `input` and sometimes `ctx`/`url`"
sentence is replaced by the trio-and-cap statement plus the window sentence "for contract 1.3.0
`input`, `ctx` and `url` are present with the fixed value `"[redacted]"`; they are dropped at the
next MINOR"),
`HTTPValidationError` (`:821-830`; drops its per-error-keys sentence) **and its `detail` field
description** (`:831-833`, "One entry per failed field." → "One entry per failed field, at most
`_MAX_VALIDATION_ERRORS` (100) entries."; salty, round 3), `_PIPELINE_422_DESCRIPTION`
(`:1438`, used at `:1569` and `:1773`; re-grounds the validation arm of the 422 union, the pipeline
arm's `reason` wording byte-identical) and the **`/extract` route's inline 422 literal**
(`retrieval_app.py:1643-1647`, a separate string, not the constant) — **five** description edits
published at **six** `openapi.yaml` sites (`:392-403`, `:1272-1278`, `:1394-1396`, `:1504-1506`,
`:1560-1562`, and the `detail` property description under the `HTTPValidationError` schema,
anchored by its text, never by a line) — with no property added, removed or retyped anywhere; the anchor moves and the four anchor-quoting pages are
refreshed; `uv run pytest` is green in this story's own scope.

**Implementation Hints:**
- **Register the handler — total, and it never re-raises** beside `@app.exception_handler(PipelineError)`
  (`retrieval_app.py:1410`): `@app.exception_handler(RequestValidationError)` returning
  `JSONResponse(status_code=422, content={"detail": items})` where each item is `{"loc": …, "msg": …,
  "type": …, "input": _VALIDATION_PLACEHOLDER, "ctx": _VALIDATION_PLACEHOLDER, "url":
  _VALIDATION_PLACEHOLDER}` — `_VALIDATION_PLACEHOLDER = "[redacted]"` and `_VALIDATION_WINDOW_KEYS
  = ("input", "ctx", "url")` declared beside `_MAX_VALIDATION_ERRORS`, the three keys emitted on
  **every** item for the 1.3.0 window and removed, constants and all, at the next MINOR (ruling R19
  as corrected in round 5: GOVERNANCE Example 6 step 1 — the stricter behaviour ships alongside the
  shape consumers read; the value is fixed, so the reflector is closed the moment this lands; step
  2's window is one minor release, stated in the docstring line and `docs/releases.md`) — and where
  `items` is built by **coerce-and-fail-closed**, not merely defensive key access (salty, round 4:
  `ValidationException.errors()` is typed `Sequence[Any]`, `fastapi/exceptions.py:189-190`, so an
  entry is not guaranteed to be a mapping and `.get()` on a non-mapping is an `AttributeError`;
  `JSONResponse(...)` renders its content in `__init__`, inside the handler's frame, so a
  non-serialisable `msg` raises there too): an entry is used only after `isinstance(entry, Mapping)`
  (others dropped), `msg` and `type` are `str(...)`-coerced, a `loc` segment is kept only when
  `isinstance(seg, int)` or `isinstance(seg, str)` (anything else dropped and counted by
  `validation_422_loc_dropped`), over `exc.errors()[:_MAX_VALIDATION_ERRORS]`, and the whole body
  construction sits under one `except Exception` that returns the fixed `{"detail": []}` 422 — so
  nothing can raise inside the handler (security, round 2: Starlette invokes handlers inside its `except`, so a raise chains
  `__context__` and uvicorn logs `str(exc)`, and `ValidationException.__str__` renders every error
  dict **including `input`**; `exc.body` holds the raw parsed body — neither ever reaches a logger,
  the exception object is never passed to a logger and never re-raised). `_MAX_VALIDATION_ERRORS = 100`
  is declared beside the other `_MAX_*` constants (`retrieval_app.py:836-839`; the
  `_MAX_POLICY_ENTRIES` precedent at `pipeline/search_providers/policy.py:17`) and quoted **by name** in
  the field description, the ruling and SECURITY.md. `loc` items are `str | int`; **the handler
  enforces the `loc` invariant at runtime too, from a stated source of truth, failing closed** (second
  opinion, round 2; ruling R30/R19 as corrected in round 4; salty, codebase-fit and security, round
  3): a module-level map built once at import from the models this repo owns —
  `_ROUTE_LOC_ALLOWLIST: dict[str, frozenset[str]] = {"/search": frozenset(SearchRequest.model_fields),
  "/retrieve": frozenset(RetrieveRequest.model_fields), "/extract": frozenset({"file", "filename",
  "mime_hint", "extract_mode", "request_id", "timeout_s"})}` (the `/extract` `Form`/`File` parameter
  names, `retrieval_app.py:1664-1667` — a literal, so it is **pinned to the route by a test**:
  `frozenset(inspect.signature(retrieval_app.extract).parameters) - {"request"}` equals the entry,
  and the fuzz's `/extract` case set is built from that same signature, so a seventh `Form()`
  parameter turns a test red instead of quietly degrading the error message; salty, round 4) —
  keyed by the closed route token, plus the fixed framework segments `{"body", "query", "path",
  "header"}`. The handler reads the matched route from `request.scope.get("route")` and keys the
  map by `route.path` — **the single mechanism** (the locked FastAPI sets `child_scope["route"] =
  self` in `APIRoute.matches`, `fastapi/routing.py:836`, and Starlette's `scope.update(child_scope)`
  runs before the handler, so `scope["route"]` is present when the handler's `Request` is built —
  verified empirically in round 4); `exc.endpoint_path` is **not** an alternative — it is the
  method-prefixed message string `"POST /search"` (`fastapi/routing.py:423`, measured against the
  locked FastAPI 0.141.1; codebase-fit, round 4), which would miss every key, take the fail-closed
  branch on every request and drop every caller's `loc` while every criterion stayed green — and
  never a FastAPI internal such as `route.body_field`. A criterion pins that the resolved token for
  a normal `/search` 422 is `"/search"`, not `other`, so the fail-closed branch cannot become the
  always-branch. The rule: an `int` segment passes; a `str` segment
  passes only if it is a framework segment or in the matched route's set; **any other `str` segment
  is dropped** — not replaced (the round-3 `"?"` token is withdrawn) — and **no route in scope, or a
  route outside the map, drops every non-framework `str` segment** (fail closed, never pass-through).
  Drops are counted: one WARNING `validation_422_loc_dropped — dropped=<n> route=<token>` per request
  that dropped anything (closed vocabulary; it joins `validation_422_truncated` at every
  documentation home below). The structural test stays as the canary, the handler as the guarantee.
  **The cap bounds the response body only** — `DocumentSizeLimitMiddleware` gates on `/extract`
  (`:1041`), so a 50,000-item body on `/search` is still fully parsed before the slice; that cost falls
  under the existing "resource exhaustion by an admitted caller" row, which the closure prose
  cross-references as unchanged (security, round 1). When the cap bites, the handler logs one WARNING
  `validation_422_truncated — count=<n> route=<route>` where `<route>` is **the matched route template**
  (`request.scope["route"].path`) checked against the closed three-member set `{"/search",
  "/retrieve", "/extract"}` with the fixed fallback `other` — never `request.url.path` (invariant 6 is
  closed by construction, not by today's route table; salty and security, round 2). WARNING renders
  without logging configuration, INFO does not (GOTCHAS "Nothing configures logging"), so the
  truncation is loud without a wire change (Decisions Made); it is emitted **at most once per
  request** and its volume falls under the same accepted exhaustion row (network placement is the
  control) — the closure prose says so (security, round 3). Tests: a `caplog` test on the
  `retrieval_app` logger asserts the record's route token is a member of the closed set and no record
  contains the marker; a **root-level** capture (`with caplog.at_level(logging.DEBUG):` with no
  `logger=`, every propagating logger — the house form, `tests/test_cache.py:796`, whose canary
  assertion that the capture really caught records is adopted so an empty capture cannot pass as a
  clean one; the marker is chosen and the records filtered the way `tests/test_brave_provider.py:1184`
  `_EXC_TEXT_MARKER` and `:1319-1329` do, this test widening deliberately from `caplog.text` to
  `getMessage()` and `args`; codebase-fit, round 4) asserts the marker appears in no captured record
  on **any** logger (security, round 3); a third feeds the handler an error entry with `loc` missing
  and asserts a 422 with `loc == []`, not a 500, and its siblings feed a non-mapping entry, a `loc`
  segment that is neither `int` nor `str`, and a non-serialisable `msg` — each a 422, never a 500
  (salty, round 4) — **those never-raises tests are the uvicorn guarantee**: under
  `httpx.ASGITransport` no uvicorn runs, so the round-3 "uvicorn error logger emits nothing"
  assertion was a tautology and is withdrawn (salty, round 3); the only way a value reaches uvicorn's
  `str(exc)` rendering is a raise inside the handler, and the runtime witness is US-005's `docker
  logs` check on the released container.
- **The `msg` invariant, tested by provoking validators** (salty, round 1). Pydantic renders a custom
  validator's `ValueError` as `msg = "Value error, <message>"`, so the invariant is: no request-model
  validator (`RetrieveRequest`, `SearchRequest`, the `/extract` `Form` params) may interpolate the
  caller's value into its message, and `detail[].msg` is pydantic's stock text for the error type.
  Today `models.py`'s only `@field_validator`s (`:134`, `:206`, `:371`) are on response models, but
  spec 3 adds `blocked_domains` and a leading-dot form to `SearchRequest` — exactly the shape somebody
  implements as `ValueError(f"invalid entry: {entry}")`. The fuzz therefore posts the marker **one
  field at a time**, and the value shape is chosen per field (security and salty, round 2): for a
  field that carries an after-validator at execution time, the marker is a **type-valid but
  semantically invalid** value (a marker string as a `blocked_domains` entry, not as `num_results`) so
  the validator is actually entered — pydantic v2 runs after-validators only once coercion succeeds;
  for a field whose only bound is `min_length` (`query`, `url` — the two long attacker-chosen fields)
  the marker rides inside a **wrong-typed container** (`{"query": {"marker": 1}}`) so a `string_type`
  error is provoked while the marker still reaches `exc.errors()[].input`. **Those two clauses are
  worked examples, not the closed set** (security, round 3): for each field, choose whatever value
  shape provokes at least one `detail` entry for that field while the marker still reaches
  `exc.errors()[].input` (a wrong-typed container works for the domain lists and the booleans; a field
  with an after-validator takes the type-valid marker). **Liveness is per field**:
  every field of both request models and the `/extract` `Form` params must produce at least one
  `detail` entry in its own case (a field that produced none fails the test), and every field that
  carries a validator must produce `value_error` **or** `assertion_error` (a bare `assert` renders as
  "Assertion failed, <message>" and can carry a value too) in its own case — never an aggregate
  "somewhere in the run". The marker is absent from `response.text` — every `msg`, `loc` and `type`
  (ruling R30/R19 as corrected in round 4; `type` is pydantic's fixed identifier set, and no
  request-model validator may raise `PydanticCustomError` with an interpolated code). **Guard of the
  guard:** a temporary
  in-test model (or a monkeypatched validator on the real request model) that raises with the value
  interpolated must turn the fuzz red — a guard that has never failed is not a guard. The covered
  field set is enumerated in Implementation Notes so a field added later without a fuzz case is
  visible. The bracketed error-type list from round 1 (`missing`, `string_type`, `int_parsing`,
  `literal_error`, `json_invalid`, `less_than_equal`, `greater_than`) is **illustrative**: derive the
  set from the live models at execution time (spec 3's `ge=0.0` yields `greater_than_equal`, for one).
- **The `loc` invariant** (security, round 1): `loc` is caller-controlled the moment a request model
  sets `model_config = ConfigDict(extra="forbid")` (`extra_forbidden` puts the attacker-chosen field
  name into `loc`) or carries a mapping-typed field. Neither exists today (`models.py:221`, `:273`
  declare no `model_config` and no dict-typed field), but `extra="forbid"` is house style elsewhere
  (`retrieval_app.py:436,491,541,580,618,660,697,721` — the metrics and response models). Pin it
  structurally **where those models' bounds are already pinned** (codebase-fit, round 3):
  `tests/test_models.py::TestRetrieveRequest` (`:185`) and `::TestSearchRequest` (`:260`) each gain
  one assertion that `model_config` sets no `extra="forbid"` and no field annotation is a mapping
  type; the handler tests stay in `tests/test_contract_errors.py`: a synthetic error entry carrying a
  caller-shaped `loc` segment comes back with that segment **dropped**, and a handler invocation with
  no `route` in `request.scope` (or a route outside the map) drops every non-framework `str` segment
  and emits the `validation_422_loc_dropped` WARNING (the deny-by-default branch, tested; security,
  round 3); `kit_tools/arch/SECURITY.md:174`'s "Pinned by" sentence names both modules; state the
  invariant beside the `msg` one in the ruling and SECURITY.md.
- **Both middlewares stay first.** `DocumentSizeLimitMiddleware` and `ExtractionAdmissionMiddleware`
  refuse before routing; the handler only sees requests that reached a route. The `/extract` case is
  a **well-formed multipart** request with `extract_mode=bogus` (a wrong-typed body on the multipart
  route is FastAPI's documented **400** body-parse guard and never reaches the handler — Edge Cases).
  Regression witnesses: `tests/test_orchestrator.py:1734` `test_post_retrieve_error_response`, the 413
  middleware test and the 404 gated-route test.
- **Where the tests live.** `tests/test_contract_errors.py:499-521`
  `test_validation_arm_of_the_422_union_is_real` is parametrised over `('route', 'body')` and posts
  `json=body` — the key-set property is asserted with the module's house parity helper
  `assert_mirrors(HTTPValidationError, response)` (`:139-157`: `model_validate`, dict-and-key-order
  equality, `model_dump_json() == response.text`), which becomes true for the validation 422 for the
  first time **on the body with the three window keys stripped** (`_strip_window_keys(payload)` — a
  module helper removing exactly `_VALIDATION_WINDOW_KEYS` from each item and asserting each was
  `"[redacted]"`; deleted with the keys at the next MINOR) and is strictly stronger than a bare
  key-set check (codebase-fit, round 2; the raw
  `set(item)` assertion stays only where the cap and fuzz tests need per-item introspection). Add the
  `/extract` case as a **sibling test** copying `test_extract_422_metadata_refusal_is_mirrored`'s
  multipart shape (`:318-331`: `files={"file": ("document.txt", b"safe", "text/plain")}`,
  `data={"filename": "document.txt", ...}` — both `file` and the required `filename` form field must
  be valid so the request reaches `extract_mode` validation, `retrieval_app.py:1664-1667`) with
  `extract_mode=bogus` (a real `literal_error`; the module's client fixture already sets
  `extract_route_enabled: True`, `:125-129`); the original's docstring (`:511-514`, "deliberately
  tolerates the extra per-error keys pydantic adds") is rewritten to the new fact. In the same module,
  `test_our_validation_mirror_matches_fastapis_own_definition` (`:716-734`) still passes but its
  premise changes: re-ground its docstring ("the handler emits exactly this trio, so the mirror pins
  our own body; the FastAPI comparison is now a compatibility check, not the source of truth"). The
  marker fuzz, the guard-of-the-guard, the `loc` structural test, the cap test and the `caplog` test
  join the same module.
- **Governance record — derived, not hard-coded, and answering Example 6** (salty, round 1;
  codebase-fit, round 2). Add a new lettered section to `contract/GOVERNANCE.md` § "Recorded rulings"
  (`:172+`; **Ruling:** / **Source:** shape, Source citing backticked in-repo paths such as
  `retrieval_app.py` and `kit_tools/arch/SECURITY.md`) whose letter is **the next unused one after the
  last `### (` heading in the file at execution time** (today `(e)` — the rulings are `(a)`, `(a2)`,
  `(b)`, `(c)`, `(d)` — but specs 1 and 3 write into this section first). **The derived letter is
  substituted at every site it appears** — the docstring clause, `_MAX_VALIDATION_ERRORS`'s field
  description, the SECURITY.md closure prose, `_RULING_MARKERS` and this spec's Implementation Notes —
  and a closing check greps that letter across `pipeline/contract.py`, `retrieval_app.py`,
  `contract/GOVERNANCE.md` and `kit_tools/arch/SECURITY.md` with no stale `(e)` left behind when the
  derived letter is not `(e)`; US-003's extractor rehearsal is the last place a mismatch is caught
  before the Release body freezes it. Add the marker to `tests/test_governance_docs.py:93`
  `_RULING_MARKERS` (and its introducing comment `:90-92`) in the same commit (it drives
  `test_the_ruling_has_a_section` `:358` and `test_the_ruling_cites_a_source_file_that_exists` `:367`;
  a criterion checks it has no duplicate). **The derived letter is written once** (salty, round 3):
  the first act of the story records `letter: (<x>)` under `### US-001` in Implementation Notes, and
  every site copies that recorded value — never re-derived per site. **The classification is one
  framing** (salty, round 3 — the round-3 text said "MINOR under ruling (b)" and "reconciled with
  Example 6" side by side): the trim is an **expedited MINOR with a compatibility window**, the
  category GOVERNANCE's MINOR row already names (`:103`; table row 6, `:148`), and the ruling answers
  § "Example 6 in full: expedited security changes" (`:150-167`) **by name**: its trigger is "a value
  that used to be accepted must stop being accepted", and this trim is the response-side mirror —
  nothing the caller *sends* stops being accepted, but something the caller *received* changes — so
  steps 1–4 are applied to what consumers read, **and step 1 is actually taken** (ruling R19 as
  corrected in round 5; salty, round 4 — the round-4 text stopped emitting the keys immediately with
  a window containing no surviving behaviour, which is step 4's "expedited MINOR wearing a disguise",
  not step 1): **step 1** — the keys `input`, `ctx` and `url` stay present on every item with the
  fixed value `"[redacted]"`, so a consumer reading the key still finds it while the reflector is
  closed at once (an opt-in flag that kept the real values was rejected because it keeps the
  reflector for exactly the consumers who never read the note); **step 2** — the window is one minor
  release, stated in the docstring line and `docs/releases.md` ("`input`/`ctx`/`url` carry
  `"[redacted]"` in contract 1.3.0 and are dropped at the next MINOR; consumers reading
  `detail[].input` must stop"); **step 3, answered** — the drop is a **second MINOR, not a MAJOR**,
  because the three keys were admitted by a description and never declared (`loc`, `msg`, `type`
  are the only declared properties), so removing them moves no declared property and the
  classification table's MAJOR row ("a response field is removed") does not reach them — the ruling
  records this carve-out explicitly so it does not sit beside a table row that reads as
  contradicting it; **step 4** is thereby not entered. Ruling (b) is **not** the basis (it governs
  enum members). The ruling states why a MAJOR was not chosen at either step (as above), the `msg`
  and `loc` invariants (stated as "no value from the request reaches a log
  or a traceback from the request-validation path", naming `exc.body` and `str(exc)`/`repr(exc)` as
  the carriers), the cap by constant name and that it bounds the response only, and the closed route
  token. **The count word moves by artifact, not by literal** (ruling R39; salty and codebase-fit,
  round 2): spell `_NUMBER_WORDS[len(_RULING_MARKERS)]` as it stands **before** this story's addition,
  grep for that word **case-insensitively over an explicit path set** (R43 — `--exclude-dir` matches
  basenames, so the round-3 `--exclude-dir=kit_tools/specs` excluded nothing): `grep -rniE
  '\b<word> (recorded )?rulings' --include='*.md' --include='*.py' contract tests kit_tools/testing
  kit_tools/arch kit_tools/docs kit_tools/AGENT_README.md kit_tools/SYNOPSIS.md CLAUDE.md README.md
  docs` — executed 2026-09-19 for `five`: **9 hits** — `contract/GOVERNANCE.md:174` ("**F**ive
  rulings"), `tests/test_governance_docs.py:90` and `:355`, `kit_tools/testing/TESTING_GUIDE.md:145`,
  `CLAUDE.md:89`, `kit_tools/AGENT_README.md:53`, `kit_tools/SYNOPSIS.md:102`,
  `kit_tools/arch/CODE_ARCH.md:190` and `kit_tools/arch/DECISIONS.md:778` — and every hit **except
  `kit_tools/arch/DECISIONS.md`'s dated ADR heading** becomes the new count; `GOVERNANCE.md:174`'s
  scope clause "this epic" is rewritten to name both epics ("six rulings — five from
  `feature-forage-contract`, one from `epic-forage-hardening`", or whatever the derived count is;
  salty, round 3); zero hits means the word already moved and the sweep is re-derived, never ticked. Pin it: extend
  `tests/test_governance_docs.py::_NUMBER_WORDS` (`:99-107`, already carrying `6: "six"`) with one
  assertion that § "Recorded rulings" states `_NUMBER_WORDS[len(_RULING_MARKERS)]`, the way
  `test_the_hashed_source_count_matches_the_code` (`:583-595`) pins `_REVISION_SOURCES`;
  `_NUMBER_WORDS` (`:99-107`) maps **4–10 only** today — extend it through `20: "twenty"` here, the
  once-only edit the rotation-count rule below also needs (codebase-fit, round 3).
- **Security docs.** `kit_tools/arch/SECURITY.md:174` argues that `SearchRequest.providers` carries no
  pydantic bound *because* the 422 echoes `detail[].input`; the rewritten paragraph must still
  establish that `providers` remains unbounded at the schema, that the bound lives in
  `apply_request_policy`, and that the echo risk that motivated the split is now **closed by the new
  ruling (by its derived letter)** rather than merely unmentioned — scoped to the **request-validation** 422 on the three routes, with a
  cross-reference that the pipeline 422's `reason` is unchanged (ruling (d): `/retrieve` still echoes
  the resolved private IP by design); the rewritten `providers` paragraph also states the
  **surviving** rationale for leaving the field unbounded at the schema (a request-side bound is a
  tightening under § "Example 6 in full"; the effect is bounded in `apply_request_policy`; the parse
  cost stays under the accepted exhaustion row) so the decision is re-derived, not orphaned
  (security, round 3). The non-vulnerabilities table (`:336-356`, two columns `| Item |
  Source |`, no status column) loses the 422-echo row at `:353`; the closure is recorded in prose the
  way `:373` records the `searxng_unavailable` one, citing the ruling by its derived letter, this
  story, the response-only scope of the cap and the unchanged exhaustion row. **The marker's homes**
  (codebase-fit and completionist, round 2): `validation_422_truncated` joins
  `kit_tools/arch/patterns/LOGGING.md`'s `retrieval_app` Logger Inventory row (`:44`) and its
  § "Closed Vocabularies" (`:74`, with the `caplog` test named as its enforcement),
  `kit_tools/docs/MONITORING.md`'s log-lines table (`:233-266`) and § "Closed vocabularies" (`:240`)
  with the operator meaning (a caller sent more than `_MAX_VALIDATION_ERRORS` validation failures; the
  response was truncated; the request was still fully parsed), and
  `kit_tools/docs/TROUBLESHOOTING.md:100`'s grep alternation — **both** tokens,
  `validation_422_truncated` and `validation_422_loc_dropped`, at every home — proven by value over
  an explicit path set (R43): `grep -rn 'validation_422_' --include='*.md' --include='*.py'
  retrieval_app.py tests kit_tools/arch kit_tools/docs` hits the source, the tests and each
  documentation site for each token (0 hits today). Root `SECURITY.md` needs no change here (its "Not
  vulnerabilities here" list is pinned by `tests/test_governance_docs.py:527`). The DNS-oracle rows in
  both files survive untouched (criterion).
- **Window block (rulings 5, R34, R36), copied verbatim into the criteria:** append one `* ``1.3.0``` —
  format clause to the docstring entry (`pipeline/contract.py:22-68`): "the request-validation 422
  body no longer echoes the request: `loc`, `msg`, `type` per entry, at most `_MAX_VALIDATION_ERRORS`
  entries, and for this contract version `input`, `ctx` and `url` present with the fixed value
  `"[redacted]"` — an expedited MINOR under Example 6 step 1: the shipped description documented
  pydantic's extra keys; consumers reading `detail[].input` must stop — the three keys are dropped
  at the next MINOR (GOVERNANCE ruling (<derived letter>))"; run `uv run python -m
  scripts.export_contract`;
  re-create `tests/golden/contract_1_3_0.json` from `tests/test_contract_schema.py::_SCHEMA_MODELS`
  (`:28`; neither validation model is in the set, so "golden unchanged, expected" is the recorded
  outcome); append nothing to `_EXPECTED_ONE_THREE_ZERO_DIFF` (no golden-visible field moves — say
  so); refresh the four pages in `tests/test_governance_docs.py:60-66` `_ANCHOR_QUOTING_PAGES`
  (`kit_tools/docs/API_GUIDE.md`, `CI_CD.md`, `DEPLOYMENT.md`, `kit_tools/arch/SERVICE_MAP.md`) with
  `anchor=$(cut -d' ' -f1 contract/openapi.yaml.sha256)` — copied, never retyped; run `--check`.
  `contract.py` is hashed → measure and record the rotation (ruling 6) at the five sites. **The
  rotation-count sentences move by artifact, and the artifact is named exactly** (salty and
  codebase-fit, rounds 2 and 3; the round-4 Addendum): the pre-story count is **the ordinal of the
  last `### The <ordinal> rotation:` heading in `docs/bootstrap-notes.md`** (today `fourteenth`,
  `:495`) — **not** the number of those headings: they start at the fourth (`:111`; rotations 1–3
  share the split section at `:50`), so there are 11 headings for 14 rotations — cross-checked
  against `kit_tools/docs/GOTCHAS.md`'s rotation table **rows minus the `At split` origin row**
  (`:415`; 15 − 1 = 14 today); the two must agree or the story stops. Spell it with the extended
  `_NUMBER_WORDS` and grep **digit-or-word, case-insensitively, over an explicit path set** (R43):
  `grep -rniE '\b(<word>|<digits>) (times|rotations)\b' --include='*.md' docs kit_tools/docs
  kit_tools/arch kit_tools/testing kit_tools/AGENT_README.md kit_tools/SYNOPSIS.md CLAUDE.md README.md`
  — executed 2026-09-19 for `(fourteen|14)`: **6 hits** — `kit_tools/docs/GOTCHAS.md:410,434`,
  `kit_tools/arch/SERVICE_MAP.md:204`, `kit_tools/docs/TROUBLESHOOTING.md:399` ("**F**ourteen") and
  `:617`, `kit_tools/docs/DEPLOYMENT.md:121` — every hit outside the dated ordinal lines in
  `CLAUDE.md`, `kit_tools/arch/CODE_ARCH.md` and `docs/bootstrap-notes.md` headings rewritten to the
  new count; the "previous revision" literal at `kit_tools/arch/SERVICE_MAP.md:204` is whatever spec 7
  last recorded, read from the table, not `41ac98ca…`; zero hits is a failure, not a pass.

**Acceptance Criteria:**
- [x] A `RequestValidationError` handler is registered with `_MAX_VALIDATION_ERRORS = 100`,
      `_VALIDATION_PLACEHOLDER = "[redacted]"` and `_VALIDATION_WINDOW_KEYS` as named constants,
      coerces and fails closed (non-mapping entries dropped, `msg`/`type` `str()`-coerced,
      non-`int`/`str` `loc` segments dropped and counted, one blanket `except Exception` returning
      `{"detail": []}`), never re-raises, passes no exception object to a logger, and emits
      `input`/`ctx`/`url` as `"[redacted]"` on every item; a wrong-typed JSON body on `/search` and
      `/retrieve` and a well-formed multipart with `extract_mode=bogus` on `/extract` (sibling test
      copying `:318-331`'s shape) yield 422 with every item carrying exactly the six keys, the three
      placeholders equal to `"[redacted]"`, and `assert_mirrors(HTTPValidationError, response)`
      holding on all three routes after `_strip_window_keys` (the validation arm joins the module's
      parity convention on the trio).
- [x] The marker fuzz posts the marker one field at a time (type-valid-but-invalid for validated
      fields; inside a wrong-typed container for `min_length`-only fields), asserts every field of both
      request models and the `/extract` `Form` params produced at least one `detail` entry in its own
      case, asserts every validated field produced `value_error` or `assertion_error` in its own case,
      asserts the marker appears 0 times in `response.text` (every `msg`, `loc` and `type`), and
      enumerates the covered fields in Implementation Notes.
- [x] A guard-of-the-guard (an interpolating validator monkeypatched onto a real request model) turns
      the fuzz red.
- [x] `_ROUTE_LOC_ALLOWLIST` is a module-level map built from `SearchRequest.model_fields`,
      `RetrieveRequest.model_fields` and the six `/extract` parameter names, keyed by the closed route
      token, read through `request.scope["route"].path` only (`grep -c endpoint_path
      retrieval_app.py` is 0) and no FastAPI internal; a test asserts the `/extract` entry equals
      `inspect.signature(extract).parameters` minus `request` and the fuzz's `/extract` cases derive
      from the same signature; a test asserts a normal `/search` 422 resolves the token `"/search"`,
      not `other`; the structural assertions in `tests/test_models.py::TestRetrieveRequest` and
      `::TestSearchRequest` show neither request model sets `extra="forbid"` or carries a
      mapping-typed field; handler tests in `tests/test_contract_errors.py` show a caller-shaped `loc`
      segment is dropped, and that with no `route` in scope every non-framework `str` segment is
      dropped with one `validation_422_loc_dropped` WARNING (deny by default).
- [x] A 50,000-item list yields at most 100 entries and one `validation_422_truncated` WARNING whose
      route token is a member of `{"/search", "/retrieve", "/extract", "other"}` (`caplog`); no
      `retrieval_app` record contains the marker; a root-level `caplog` capture shows the marker in no
      record on any logger (root-level `caplog.at_level(logging.DEBUG)` with the canary, the
      `tests/test_cache.py:796` form); a malformed error entry (missing `loc`), a non-mapping entry,
      a `loc` segment that is neither `int` nor `str`, and a non-serialisable `msg` each yield 422,
      not 500 (the never-raises guarantee; no uvicorn-logger assertion — it is vacuous under
      `ASGITransport`).
- [x] The middleware-raised 4xx paths still bypass the handler
      (`tests/test_orchestrator.py::test_post_retrieve_error_response`, the 413 and 404 tests pass untouched);
      `test_our_validation_mirror_matches_fastapis_own_definition` passes with its docstring re-grounded.
- [x] `contract/GOVERNANCE.md` gains the next-lettered ruling with **Ruling** and **Source** (an
      **expedited MINOR with a compatibility window** — the MINOR row's own category — argued from
      `openapi.yaml:1271-1278` **and answering § "Example 6 in full" step by step**: step 1 the
      `"[redacted]"` placeholders, step 2 the one-release window, step 3 the drop as a second MINOR
      with the description-admitted carve-out stated, never "under ruling (b)"; why not a MAJOR at
      either step; the rejected opt-in flag; both invariants naming `exc.body` and
      `str(exc)` as the carriers; the cap by name, its response-only scope and its once-per-request
      WARNING under the exhaustion row; the closed route token; the fail-closed `loc` allowlist);
      `_RULING_MARKERS` includes it with no duplicate; the letter is recorded once in Implementation
      Notes and copied to the docstring clause, the field description and the SECURITY.md closure
      (the closing grep finds no stale `(e)` unless `(e)` is the derived letter); the pre-story
      ruling-count word (from `_NUMBER_WORDS[len(_RULING_MARKERS)]`) is grepped case-insensitively
      over the explicit path set (9 hits today) and every hit except `kit_tools/arch/DECISIONS.md`'s
      dated heading reads the new count, `contract/GOVERNANCE.md:174` included with its scope clause
      naming both epics; `_NUMBER_WORDS` extends through twenty and pins the ruling count;
      `tests/test_governance_docs.py` green.
- [x] `kit_tools/arch/SECURITY.md`: `grep -c 'echoes the offending value verbatim' kit_tools/arch/SECURITY.md`
      is 0; the rewritten `providers` paragraph names `apply_request_policy` as the bound, the new
      ruling (derived letter) as the closure and the surviving rationale for no schema bound, and its
      "Pinned by" sentence names `tests/test_models.py` and `tests/test_contract_errors.py`; the
      closure prose cross-references the unchanged exhaustion row and states the truncation WARNING
      is once per request; the DNS-oracle rows in `kit_tools/arch/SECURITY.md` and root
      `SECURITY.md` are byte-unchanged; root `SECURITY.md` unchanged by this story;
      `validation_422_truncated` and `validation_422_loc_dropped` are each documented at the
      LOGGING.md, MONITORING.md and TROUBLESHOOTING.md sites (`grep -rn 'validation_422_'` over the
      explicit path set hits source, tests and each site for both tokens).
- [x] Contract regenerated: the docstring clause appended with the derived letter; `uv run python -m
      scripts.export_contract` run; the document differs only in the five validation-422 descriptions
      (`ValidationErrorDetail`, `HTTPValidationError`, its `detail` field, `_PIPELINE_422_DESCRIPTION`,
      the `/extract` inline literal) at the six `openapi.yaml` sites (anchored by text), with no
      property added, removed or retyped;
      golden unchanged (expected); `_EXPECTED_ONE_THREE_ZERO_DIFF` unchanged (expected); the four
      anchor-quoting pages equal `contract/openapi.yaml.sha256`; `--check` clean.
- [x] Rotation recorded: the measured before/after in `docs/bootstrap-notes.md`, `CLAUDE.md`,
      `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md`'s rotation table and
      `kit_tools/arch/CODE_ARCH.md`; the pre-story rotation count (the ordinal of the last
      bootstrap-notes rotation heading, cross-checked against GOTCHAS rows minus the origin row —
      14 today, both sources recorded) grepped digit-or-word, case-insensitively over
      `(times|rotations)` on the explicit path set (6 hits today) with every hit outside the dated
      ordinal lines rewritten (`kit_tools/docs/TROUBLESHOOTING.md:399` included); zero hits is a
      failure.
- [x] The marker fuzz, the guard-of-the-guard, the two `loc` handler tests (dropped segment; no
      route in scope), the cap test, the two log-capture tests, the four never-raises tests, the
      `/extract`-signature test, the `/search`-token test and the placeholder-keys test are new
      tests in `tests/test_contract_errors.py`, and the two `loc` structural assertions are new in
      `tests/test_models.py`; no existing test in either module was deleted.
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-002: Close the 1.3.0 contract record — final entry, coverage sweep, frozen golden

**Priority:** P1

**Description:** As the consumer's re-vendoring session, I want one complete, mechanical statement of
what contract 1.3.0 changed — a docstring entry the Release body will carry and a test that fails if
the golden carries an addition the entry does not name — and one frozen golden, so the `v1.2.0`
Release announces the whole epic and nothing rides through silently.

**Independent Test:** `uv run pytest tests/test_ci_workflow.py -k 'extractor or docstring_entry or
tense'` passes and `tests/test_ci_workflow.py::_run_entry_extractor` (`:2253`) applied to
`pipeline/contract.py` for `1.3.0` prints one entry beginning `* ``1.3.0`` ` that names every item in
the reconciled window list and no line of the `1.2.0` entry; `uv run pytest tests/test_contract_schema.py`
passes with `_EXPECTED_ONE_THREE_ZERO_DIFF` equal to the golden-visible subset of that list and the
1.2.0 pair untouched; no entry below `CONTRACT_VERSION` contains "held", "until … publishes" or
"published by".

**Implementation Hints:**
- **The record** (`pipeline/contract.py:22-68`): rewrite the `1.3.0` bullet into its final form — one
  bullet at column 0 opening `* ``1.3.0`` —`, two-space continuation lines, **no blank line inside it**
  (a blank line ends the entry — GOVERNANCE step 7; the extractor is pinned by
  `tests/test_ci_workflow.py:2504-2580`), naming exactly the window list. **Treat Technical
  Considerations' list as a draft to reconcile**, not a checklist to satisfy verbatim (second
  opinion, round 1): `git log -p -- pipeline/contract.py` shows every line specs 1–7 appended; nothing a
  story added is dropped, nothing that did not ship is claimed. Close with the sentence "Every addition
  above is additive except the request-validation 422 trim (ruling (<derived letter>)); a consumer comparing MAJOR
  keeps working untouched". **No publication-state clause** — the round-1 draft would have written
  "published by `v1.2.0`" three stories before that tag exists, the same forward-dated lie the `1.2.0`
  entry's stale "held … until the `v1.1.0` image publishes it" (`:65-68`) is in the other direction.
  Publication state lives in `docs/releases.md` and GOVERNANCE § "Two semvers", which US-005 flips
  after the cut without touching a hashed file; the `1.2.0` entry's "held" sentence is **removed**,
  not re-dated — and so is its second publication-state clause, `:63`'s "there is no vendored 1.2.0
  copy yet to re-vendor" (false since `v1.1.0` shipped it; salty, round 3), rewritten to the timeless
  fact ("the description edits landed inside the same unreleased window and are subsumed by this
  MINOR"); `grep -c 'no vendored' pipeline/contract.py` is 0 afterwards. **The `.py` sources are
  swept too** (salty, round 3 — US-004's sweep covers docs only): `grep -rn '1\.2\.0' retrieval_app.py
  models.py model_fetcher.py cache.py url_validator.py pipeline promptguard scripts contract_smoke.py`
  — executed 2026-09-19: **20 hits** (ruling R30 as corrected in round 5; the round-4 count of 19
  missed `models.py:232` and `:282`, the two request-model docstrings' "(contract 1.2.0)" clauses —
  contract provenance, they stay) — classified here: contract-provenance phrases ("added in
  contract 1.2.0", `models.py:232,282,313-363`, `pipeline/contract.py:131,142,272`, `brave.py:5`,
  `policy.py:1`, `retrieval_app.py:409,1586,1788`) stay; `pipeline/contract.py:22,33,63` are this story's;
  **current-value statements in field descriptions** — `retrieval_app.py:378-379` ("Two keys are
  defined in contract 1.2.0", the `HealthResponse.capabilities` description, three keys with spec 4's
  `cache_hmac_key`) and `:383` — become `1.3.0` inside **this** story's regenerate (a description-only
  move: ruling R36 as corrected in round 4 — nothing is appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`;
  the gate is `test_contract_schema_matches_golden` after the golden is re-created) unless spec 4
  already moved them (confirm by value); the pre-flight records the regenerated `openapi.yaml` diff.
- **The tense guard reuses the existing parser and inspects whole bullets** (codebase-fit, round 1;
  salty and codebase-fit, round 3; the round-4 Addendum): add the assertion to
  `tests/test_ci_workflow.py::TestReleaseContractMapping` (`:2288`), walking every `* ``X.Y.Z`` `
  bullet with `_slice_entry` (`:2264` — it returns the bullet from its `* ``X.Y.Z`` ` prefix to the
  first non-blank line not indented two spaces, i.e. the next bullet or the docstring end) — the only
  place in the suite that already parses those entries; the story's own `-k` selector collects it.
  **The round-3 patterns never matched**: their `[^.]*` sentence bound is broken by the periods inside
  `contract_1_2_0.json` and `v1.1.0`, which sit between "held" and "until" in the real entry. The
  three checks run over the **whole sliced bullet** with no sentence bound (`re.S`):
  `re.search(r"\bheld\b.*\b(until|pending)\b", entry, re.S)`, `re.search(r"\buntil\b.*\bpublish(es|ed)\b",
  entry, re.S)` and `"published by" in entry`; the failure message names publication state as the
  banned thing ("docstring entries carry no publication state — it lives in docs/releases.md and
  GOVERNANCE § Two semvers; rephrase, do not delete the guard") so an innocent "held in memory" is
  rephrased, not the guard removed. **Guard of the guard, on the real stale entry** (as US-001's
  fuzz): the test is written and run **first**, against the unmodified `pipeline/contract.py:65-68`
  ("This version is **held**: … until the ``v1.1.0`` image publishes it"), and its red run is
  recorded verbatim in Implementation Notes; only then is the sentence removed and the test goes
  green — a guard that has never failed is not a guard.
- **The coverage sweep**: spec 1 US-004 pinned the 1.2.0 pair to the literal `contract_1_2_0.json`
  (`feature-hardening-search-sanitization.md`, its window criterion), so that pair is **green and
  stays byte-alone** — there is no "retire" branch. The **completeness half already exists from spec 1
  US-004** — its hint titled **"The golden gate"** (cited by title and symbol, never by line: two
  line-number repoints landed on spec 1's `idna` material, codebase-fit rounds 3 and 4) introduces
  `_EXPECTED_ONE_THREE_ZERO_DIFF`, `_ONE_THREE_ZERO_DIFFED_SCHEMAS`, `_diff_against_1_2_0` and
  `test_the_1_2_0_to_1_3_0_diff_has_no_unlisted_additions` and re-points the 1.2.0 pair to the literal
  `contract_1_2_0.json` — confirm, reconcile the set against the docstring, freeze; this story **adds** the presence half (`test_the_N_1_3_0_additions_are_all_golden_pinned`)
  and `_ONE_THREE_ZERO_DIFFED_SCHEMAS` / `_diff_against_1_2_0(current)` if spec 1's sweep did not
  already introduce them (codebase-fit, round 2). **The counters get a symmetric one-liner**
  (completionist, round 2): in `tests/test_contract_metrics.py` (which already owns `_SECTION_MODELS`
  and the counter pins), one assertion that every counter and `CacheMetrics` field name that moved
  between the 1.2.0 and 1.3.0 section models appears literally in the `1.3.0` docstring entry as
  sliced by `_slice_entry` — so the non-golden half of the window is mechanical too. **The
  description-only third** (salty, round 4): three window items — spec 3's leading-dot form in
  three field descriptions, spec 6's two rendered healthcheck descriptions and US-001's own
  validation-422 descriptions — are golden-invisible and counter-free, held by nothing mechanical;
  so this story runs `git diff <the commit that opened the 1.3.0 window, spec 1 US-004>..HEAD --
  contract/openapi.yaml` and classifies **every description hunk** in Implementation Notes as
  named-in-the-docstring-entry or deliberately-not, with zero unclassified hunks (criterion).
  The two structural pins at `tests/test_contract_schema.py:229-231` carry over: `set(_ONE_THREE_ZERO_
  DIFFED_SCHEMAS) == set(_SCHEMA_MODELS)` (all six keys) and no `| {…}` top-level-model clause, because
  1.3.0 adds no top-level model. The expectation set mirrors the docstring's golden-visible items
  one-for-one; the `/metrics` counters and `CacheMetrics` fields are pinned by
  `tests/test_contract_metrics.py`, not the golden (`:79-83`) — the docstring names them, the set does not.
- **The golden** is written by hand from `_current_schemas()` over `_SCHEMA_MODELS`
  (`tests/test_contract_schema.py:28-40`; `indent=2, sort_keys=True`, trailing newline —
  `kit_tools/EXECUTION_LOG.md:264` records the procedure); `uv run python -m scripts.export_contract`
  writes only `contract/openapi.yaml`, its `.sha256` and the drift twin (`scripts/export_contract.py:267-270`).
  From this commit `tests/golden/contract_1_3_0.json` is frozen (older goldens never edited —
  GOVERNANCE ruling (c)).
- **GOVERNANCE § "Two semvers"** (`contract/GOVERNANCE.md:46-70`): leave the table's `v1.0.0` example
  alone — `tests/test_governance_docs.py::test_the_independence_is_stated_with_its_worked_example`
  (`:443-450` and beyond) pins **three** things in the section: the literal `v1.0.0`, the current
  `CONTRACT_VERSION`, **and the `US-004` reference** ("the section must say which story cuts that
  tag"); keep all three (salty, round 2) — and rewrite the "currently **pending**" paragraph (`:61-67`) to
  record `v1.1.0` shipped serving `1.2.0` (2026-09-18) and `v1.2.0` as the pending image for `1.3.0`
  (US-005 flips "pending" to the date after the cut). The current-version sentence (`:29`, pinned by
  `test_it_states_the_current_contract_version` `:193`) reads `1.3.0`.
- **Rotation (ruling 6):** the docstring edit changes `contract.py`'s bytes → measure and record at the
  five sites with the count sentences updated by value (as US-001).
- **Anchor**: regenerate, then confirm that the four anchor-quoting pages equal
  `contract/openapi.yaml.sha256`; refresh them if this final regenerate moved the document.

**Acceptance Criteria:**
- [x] The `1.3.0` docstring entry is a single well-formed bullet naming exactly the reconciled window
      list, with the additive sentence and **no** publication-state clause; `_run_entry_extractor`
      prints it verbatim (recorded in Implementation Notes) with no `1.2.0` line; `uv run pytest
      tests/test_ci_workflow.py -k 'extractor or docstring_entry or tense'` green.
- [x] The `1.2.0` entry's "held … until" sentence and its "no vendored 1.2.0 copy yet" clause are
      removed (`grep -c 'no vendored' pipeline/contract.py` is 0); a `TestReleaseContractMapping` test
      walking every entry with `_slice_entry` applies the three whole-bullet patterns with the named
      failure message, and its recorded red run against the pre-story `1.2.0` entry is in
      Implementation Notes; the `.py` `1\.2\.0` sweep (20 hits today) is classified there and the
      description-only current-value hits moved inside this story's regenerate with nothing appended
      to `_EXPECTED_ONE_THREE_ZERO_DIFF`; GOVERNANCE § "Two semvers" records `v1.1.0` shipped and `v1.2.0` pending, keeps
      `v1.0.0` in the table, the `US-004` reference and `1.3.0`
      (`test_the_independence_is_stated_with_its_worked_example` green).
- [x] `tests/test_contract_schema.py` carries the 1.2.0 → 1.3.0 pair (spec 1's completeness half
      confirmed, the presence half added) with `_EXPECTED_ONE_THREE_ZERO_DIFF` equal to the
      golden-visible subset of the window list and the two structural pins; the 1.2.0 pair is
      byte-unchanged and green; `tests/test_contract_metrics.py` asserts every moved counter name is in
      the sliced `1.3.0` entry; the `contract/openapi.yaml` diff since the window opened has every
      description hunk classified in Implementation Notes with zero unclassified.
- [x] `tests/golden/contract_1_3_0.json` re-created by hand from `_SCHEMA_MODELS` for the last time;
      `uv run python -m scripts.export_contract --check` clean; the four anchors equal the committed
      sha256; the rotation recorded at the five sites with the count sentences moved by artifact (as
      US-001).
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-004: Pre-release bookkeeping — both fan-outs by value, pins, counts, release-notes draft

**Priority:** P1

**Description:** As the owner about to cut, I want every document that states the current contract
version, the current release or a suite count already correct, and the compose examples pinned to the
tag I am about to publish, so the pre-flight finds nothing and the quickstart is right the moment the
tag exists. This story changes no runtime behaviour, but US-003's pre-flight depends on it, so it is
**not** trimmable.

**Independent Test:** Two by-value greps over the **explicit human-written path set** `README.md
CLAUDE.md contract/ docs/ compose/ contract_smoke.py kit_tools/docs kit_tools/arch kit_tools/testing
kit_tools/roadmap kit_tools/SYNOPSIS.md kit_tools/AGENT_README.md kit_tools/PRODUCT_VISION.md` —
`grep -rn '1\.2\.0'` and `grep -rn '1\.1\.0'` — return only hits classified in Implementation Notes
as (a) history rows and rotation records, (b) contract-provenance phrases ("added in contract
1.1.0"), (c) sentences naming the **image** `v1.2.0` / `forage:1.2.0`, or (d) compose comment blocks
naming the pinned tag (rewritten with the pin); zero unclassified hits; `grep -c 'forage:1.2.0' compose/minimal.yml
compose/full.yml` reports 1 each and `tests/test_compose_fragments.py` passes with
`_FORAGE_RELEASE_TAG = "1.2.0"`; the four total suite counts and every touched per-module row equal
`uv run pytest --collect-only -q`.

**Implementation Hints:**
- **Two sweeps, by value, not by bold** (ruling R39; completionist, round 1). This release moves two
  strings: the contract version (`1.2.0` → `1.3.0`) and the image tag (`1.1.0` → `1.2.0`). The named
  site list is a starting point; the criterion is the grep. **The scope is the human-written tree**
  (R43; codebase-fit, round 3 — a bare `kit_tools/` pulled 326 hits from machine-written trees):
  the path set in the Independent Test; **excluded and named**: `kit_tools/specs/` (this epic's own
  specs and the archive, ruling R28), `kit_tools/.seed_cache/`, `kit_tools/EXECUTION_LOG.md` and
  `kit_tools/SESSION_SCRATCH.md` (append-only history), `kit_tools/.validate_epic_*` result files,
  `tests/golden/`, and **`contract/openapi.yaml` with its `.sha256`** (generated — CLAUDE.md invariant
  4, "hand-editing either is always wrong"; they move only through `uv run python -m
  scripts.export_contract`, already carried by US-002's regenerate, and the pre-flight confirms them
  by `--check`, never by grep; measured 2026-09-19: 13 of the 93 `1.2.0` hits and 3 of the 104
  `1.1.0` hits over the path set, `info.version` at `:1319` among them; codebase-fit, round 4).
  Contract statements become `1.3.0`; release statements become `v1.2.0` /
  `forage:1.2.0`; `docs/releases.md` released-version entries and archived specs are never edited
  (ruling R28). A fourth bucket (salty, round 3): compose fragments carry comment blocks that name the
  image tag beside the `image:` line — rewrite sites, classified (d), not history.
  - Contract sites: `README.md:62` ("currently **1.2.0**") and `:257-258`; `CLAUDE.md:82` (invariant 4)
    and the Coexistence rotation history (`:204,229` — **append**, never rewrite);
    `contract/GOVERNANCE.md:29,:61-67` (US-002 did these — confirm); `kit_tools/arch/CODE_ARCH.md:108`;
    `kit_tools/docs/API_GUIDE.md:43,:111` (the `| contract_version | str | 1.2.0 |` row is a current
    value) and `:449` (the version-history list **gains a `1.3.0` line**); `kit_tools/arch/SERVICE_MAP.md:75,
    :189,:202-211`; `kit_tools/docs/TROUBLESHOOTING.md:61`; `kit_tools/docs/MONITORING.md:63,65,67`
    ("two keys as of contract 1.2.0" — now three with `cache_hmac_key`, spec 4);
    `kit_tools/docs/CI_CD.md:314,:343,:510-511,:531`; `kit_tools/docs/DEPLOYMENT.md:87-232`;
    `kit_tools/arch/INFRA_ARCH.md:146,159-160,188-195`; `kit_tools/testing/TESTING_GUIDE.md:156` ("`1.2.0`
    being the current one … regenerated in place" → `contract_1_3_0.json`, frozen).
  - **The secret-grep pattern count, by value** (codebase-fit, round 2): spec 4 changes the two grep
    alternations only and hands the prose off; `.github/workflows/ci.yml:539` ("iterates the same three
    patterns") is the one site outside spec 4's sweep scope (`docs kit_tools README.md`), and **spec 4
    US-004 owns that line** (codebase-fit, round 3) — so this story **confirms**, editing only what
    spec 4 left behind: `grep -rniE 'three patterns' .github kit_tools/docs kit_tools/arch
    kit_tools/testing docs README.md SECURITY.md` (R43; executed 2026-09-19: **6 hits** — `ci.yml:539`,
    `kit_tools/docs/TROUBLESHOOTING.md:121`, `kit_tools/docs/CI_CD.md:217,310`,
    `kit_tools/docs/DEPLOYMENT.md:100` and `kit_tools/arch/SECURITY.md:233`, the site the round-3
    list omitted) and every hit reads the count `tests/test_ci_workflow.py::_REQUIRED_GREP_PATTERNS`
    (`:1072`) has at execution time (four after spec 4).
  - Release sites (the `1.1.0` grep): `kit_tools/docs/LOCAL_DEV.md:229-230` (both pins resolve);
    `kit_tools/docs/TROUBLESHOOTING.md:686-692` (the compose-pin paragraph, "pin a full semver");
    `kit_tools/SYNOPSIS.md:30` (Maturity row) and `:36` (Published image row — its digest and
    `latest`/`1.1` claim are filled by US-005 after the cut); (`kit_tools/docs/MONITORING.md` carries
    no `1.1.0` — the round-4 `:380` entry was un-executed, codebase-fit; dropped);
    `contract_smoke.py:64,97` (the worked anchor example `git show v1.1.0:…`);
    `tests/test_compose_fragments.py`'s TESTING_GUIDE row text ("the `forage:1.1.0` pin");
    DEPLOYMENT/CI_CD/INFRA_ARCH current-release sentences.
- **Root `SECURITY.md` is version-agnostic** (codebase-fit, round 1): its § "Supported versions" table
  (`:29-35`) names no version by design and is pinned by `tests/test_governance_docs.py:498-503` — it is
  **not** edited at a cut. The one stale sentence is `:36` "Pre-1.0 (where the project is today)",
  false since `v1.0.0`: reword to the post-1.0 wording the same paragraph already describes, keeping
  the "latest release only" phrase the test asserts. The file lists no `FORAGE_*` credential (only
  `VALKEY_URL` at `:74`).
- **Compose pins**: `compose/minimal.yml` and `compose/full.yml` `image:` lines to `forage:1.2.0` and
  `tests/test_compose_fragments.py:77` `_FORAGE_RELEASE_TAG = "1.2.0"` together
  (`test_the_forage_image_is_the_current_release` `:505`). The envelope variables (spec 6),
  `FORAGE_CACHE_HMAC_KEY` (spec 4) and `FORAGE_MODEL_ID` / `FORAGE_MODEL_REVISION` (spec 7)
  passthroughs are already in the fragments — confirm by `grep -c`. **The unpublished-tag window is
  bounded** (salty, round 1): from the epic branch's merge to `main` until US-003 pushes the tag,
  `main`'s quickstart pulls a tag that does not exist. The rule: the owner cuts from that merge commit
  in the same sitting the completion PR merges (as at `v1.1.0`); if the gate is not run in that
  sitting, the `gate not run` record and the PR description both name the open window as an
  **outstanding item** with the revert instruction (`git revert` of this story's pin commit), never as
  a closed decision — **a criterion on this story and on US-003's gate-not-run branch, not prose**
  (salty, round 2). `docs/releases.md`'s draft block says "pinned ahead of the cut; the tag lands
  with `v1.2.0`".
- **`compose/full.yml` and the key — name the consequence** (security, round 1): the fragment's
  `FORAGE_CACHE_HMAC_KEY` passthrough is an unset bare name (spec 4), so a stock `full.yml` at `1.2.0`
  with an external Valkey serves cached content **unsigned** and reports `cache_unauthenticated` on
  `/health` until the operator sets it; `compose/full.yml`'s comment and `README.md`'s quickstart say
  exactly that in one sentence each (the remedy *and* the consequence; `docs/configuration.md`'s
  wording is spec 4's). US-005 witnesses both states.
- **Suite-count bookkeeping — totals and per-module rows**: `uv run pytest --collect-only -q` into
  `kit_tools/testing/TESTING_GUIDE.md:100`, `kit_tools/SYNOPSIS.md:32`, `kit_tools/AGENT_README.md:72`
  and `CLAUDE.md:131`; and every per-module row in TESTING_GUIDE's table for a module this epic touched
  (`tests/test_contract_errors.py` `:142`, `tests/test_governance_docs.py` `:145`,
  `tests/test_contract_schema.py` `:146` and the compose-fragments row text) reconciled against
  `uv run pytest --collect-only -q <module>` — several of these rows are already stale on today's tree
  (measured: `:146` says 1, the module collects 13), so measure, never assume the delta is only this
  epic's new tests (salty and codebase-fit, round 2) — with the row descriptions naming the new 422
  tests, the 1.3.0 sweep and the tense guard.
- **`docs/releases.md`**: draft the `### v1.2.0 — <date>` block under "Released versions" (`:13-20`)
  **with `NOT YET PUBLISHED — filled by US-005` as its first line** (salty, round 4: the section's
  own header promises "every non-pre-release tag", and the sanctioned autonomous end state leaves
  this block unfilled — the marker keeps the draft from being the forward-dated claim US-002 bans;
  US-005 deletes the line when it fills the heading date, the digest and the tagged commit), with
  `contract: 1.3.0`, the anchor, `index digest: (filled at the cut)`, `tagged commit: (filled at
  the cut)`, the pinned-ahead sentence, a "What shipped" list mirroring the docstring entry in prose,
  and one sentence that the recorded index digest is the **pinnable form**
  (`ghcr.io/washingbearlabs/forage@sha256:…`) for deployments that need immutability, with the tag pin
  as the quickstart default (security, round 1; the digest-vs-tag policy itself stays deferred); the
  "What shipped" block also carries **one upgrade action** (security, round 2): any deployment with an
  external Valkey sets `FORAGE_CACHE_HMAC_KEY` — without it cached content is served unsigned and
  `/health` reports `cache_unauthenticated` — so the operator-facing action appears in the artifact
  read at the cut, not only in the fragment's comment — the action points at spec 4's
  `docs/configuration.md` section for how the key is generated and rotated and says in one clause that
  it must be a high-entropy, per-deployment value (the verification placeholder is not one; security,
  round 3); and the compatibility-window sentence for the validation-422 change (US-001) states that
  `input`/`ctx`/`url` carry `"[redacted]"` in contract 1.3.0 and names the next MINOR as the release
  that drops them.
- **GOTCHAS rotation table** (`kit_tools/docs/GOTCHAS.md:410-434`): the count sentence and rows are
  complete for every rotation this epic recorded (confirm against `docs/bootstrap-notes.md`).

**Acceptance Criteria:**
- [x] Both by-value greps' hits are classified in Implementation Notes (history / rotation record /
      contract provenance / image tag / compose comment block), with zero unclassified hits over the
      explicit path set in the Independent Test and the named exclusions recorded; every named contract and
      release site above is updated; `kit_tools/docs/API_GUIDE.md:449` carries a `1.3.0` line;
      `MONITORING.md` says three `capabilities` keys; `kit_tools/SYNOPSIS.md:30` reads `v1.2.0` /
      `1.3.0`; root `SECURITY.md`'s supported-versions table is unchanged and `:36` no longer says
      "Pre-1.0 (where the project is today)".
- [x] `grep -c 'forage:1.2.0' compose/minimal.yml compose/full.yml` reports 1 each; `_FORAGE_RELEASE_TAG`
      is `"1.2.0"`; `tests/test_compose_fragments.py` green; the pinned-ahead sentence, the
      pinnable-digest sentence, the `FORAGE_CACHE_HMAC_KEY` upgrade action and the 422
      compatibility-window sentence are in `docs/releases.md`'s draft; the consequence-and-remedy
      sentence for `FORAGE_CACHE_HMAC_KEY` is in `compose/full.yml` and `README.md`, and the upgrade
      action points at spec 4's configuration section and says high-entropy, per-deployment; every
      `three patterns` hit over the explicit path set (case-insensitive, `.github` and
      `kit_tools/arch/SECURITY.md:233` included; 6 today) reads the `_REQUIRED_GREP_PATTERNS` count
      (confirmation of spec 4's work, edits only for leftovers).
- [x] If the cut will not happen in this sitting, the completion PR's description names the
      unpublished-tag window on `main` as an outstanding item with the `git revert <pin commit>`
      instruction (recorded in Implementation Notes with the commit sha).
- [x] The four total-count sites and every touched per-module row in TESTING_GUIDE equal
      `uv run pytest --collect-only -q` (total and per module); the compose-fragments row reads
      `forage:1.2.0`.
- [x] `docs/releases.md` carries the `v1.2.0` block with the `NOT YET PUBLISHED` first line,
      `contract: 1.3.0`, the anchor and the three placeholders (heading date, index digest, tagged
      commit); DEPLOYMENT/CI_CD/INFRA_ARCH name `v1.2.0` as the current release; the GOTCHAS
      rotation table matches `docs/bootstrap-notes.md`.
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-003: Cut + publish the v1.2.0 image (owner gate)

**Priority:** P1

**Description:** As the owner, I want a `v1.2.0` image published through the gated lane from a
pre-flighted commit, with the run, the digest and the Release body verified, so the hardening epic's
value reaches the consumer as one digest. **Execution halts here for the owner** (ruling 20): the tag
push is the human gate, and every step needs a Docker daemon, network egress and GHCR access the
autonomous environment does not have.

**Independent Test:** *Gate run* → the `v1.2.0` tag triggers `publish` (which `needs:` the six gates)
and it goes green; `gh release view v1.2.0 --json body --jq '.body' | tr -d '\r'` matches
`^contract: 1\.3\.0$` and contains every line of the rehearsal extraction; `latest`, `1.2` and `1.2.0`
resolve to one index digest; the four-way sha256 table agrees. *Gate not run* → Implementation Notes
carry `### US-003 — gate not run, <date>` naming the missing prerequisites and nothing else changes;
the story stops and reports, and nothing is asserted.

**Implementation Hints:**
- **The runbook is the `v1.1.0` cut**, executed and recorded at
  `kit_tools/specs/archive/feature-search-release.md` § "US-002 — v1.1.0 cut and publish";
  `docs/releases.md` § "Cutting a release" and § "When something goes wrong" (`:342-390`) are the short
  form. Follow them with `v1.2.0` / `1.3.0` / `1.2` substituted; only the differences are listed below.
- **Pre-flight, on `main` at the commit to be tagged:** the epic branch merged with a merge commit;
  the six required checks green on that run; `git switch main && git pull` and `git log -1` recorded;
  `uv run python -m scripts.export_contract --check` clean; `grep CONTRACT_VERSION pipeline/contract.py`
  prints `1.3.0`; US-004's two sweeps re-run and classified; `grep -c 'forage:1.2.0'` 1 per fragment;
  the four anchors equal the committed sha256; the suite counts equal `uv run pytest --collect-only -q`
  on this commit; the extractor rehearsed — `_run_entry_extractor`'s awk program from
  `.github/workflows/ci.yml` — and its output recorded verbatim, first line `* ``1.3.0`` `, no `1.2.0`
  line. Cut clear of the ten minutes around 00:00 UTC (`docs/releases.md` § "Reproducible builds").
- **The cut:** `git tag v1.2.0 && git push origin v1.2.0`, then `gh run watch`. No `searxng-v*` tag —
  the companion image is unchanged this epic.
- **Watch these `publish` steps live**, in order: "Verify the published amd64 image is the gated
  filesystem"; "Read the contract version from the tagged tree"; the published-config secret grep —
  **read the pattern set out of the tagged tree, not the log — and find it by value, never by line**
  (codebase-fit, rounds 1 and 3; the round-4 Addendum): `git show v1.2.0:.github/workflows/ci.yml |
  grep -n "grep -Eiq '"` returns exactly one line (today `:998`; the layer-history loop at `:549`
  greps `-- "${pattern}"` and does not match the quoted form) and its alternation equals, in order,
  `_REQUIRED_GREP_PATTERNS` read from the **same tagged tree** (`git show
  v1.2.0:tests/test_ci_workflow.py | grep -n '^_REQUIRED_GREP_PATTERNS'`; four after spec 4, with
  `FORAGE_CACHE_HMAC_KEY`) — the prose count at `ci.yml:539` is spec 4's and US-004's by-value sweep,
  not a gate assertion (codebase-fit, rounds 2 and 3); in the run log only the success line (found by
  value: `grep -n 'No forbidden pattern in the published image config'` on the tagged `ci.yml`)
  appears; "Create the GitHub Release"; both Release assertions. Record cold/warm (count `CACHED`
  lines).
- **After the run:** the four-way sha256 table (committed anchor `git show v1.2.0:contract/
  openapi.yaml.sha256`; repository copy `git show v1.2.0:contract/openapi.yaml | shasum -a 256`;
  Release asset via `gh release download v1.2.0 --pattern 'openapi.yaml*'` then `shasum -a 256 -c
  openapi.yaml.sha256` → `openapi.yaml: OK`; in-image **by digest, not by tag** — `docker run --rm
  --entrypoint cat ghcr.io/washingbearlabs/forage@sha256:<index digest> /app/contract/openapi.yaml |
  shasum -a 256`, ordered **after** the `imagetools inspect` step below establishes the digest, the
  ref recorded beside the four values so the table names what it measured (security, round 3));
  the Release body check; `docker buildx imagetools inspect ghcr.io/washingbearlabs/forage:<tag>` for
  `latest`, `1.2`, `1.2.0` — read the `Digest:` line (the `--format '{{.Manifest.Digest}}'` form printed whole blocks at
  `v1.1.0`); `latest` moves off the `1.1.0` image and `1.2` is minted — a pointer landing anywhere
  else is the one outcome to stop for.
- **Recovery after a pushed tag** (completionist, round 1): a red `publish` on a pushed tag is fixed
  by a **new** tag, never a moved one (`docs/releases.md` § "When something goes wrong"); a
  published-then-bad image goes to § "Withdrawn tags" (`:64`), and the handoff record (US-005) names
  the withdrawn tag. **A replacement tag re-runs the fan-out** (salty, round 4): at that moment both
  compose fragments, `_FORAGE_RELEASE_TAG`, the `docs/releases.md` block heading and pinned-ahead
  sentence, `SYNOPSIS.md:30/:36` and the DEPLOYMENT / CI_CD / INFRA_ARCH / LOCAL_DEV /
  TROUBLESHOOTING / `contract_smoke.py` current-release sentences all name a tag that will never
  exist — with the suite green — so US-004's two by-value sweeps are re-run for the new value
  **before** US-005 runs, and the withdrawn tag is moved from § "Released versions" to § "Withdrawn
  tags" rather than left as a released entry.
- **Record** in `### US-003 — v1.2.0 cut and publish, <date>` under Implementation Notes: run URL,
  tagged commit sha, index digest, the four sha256 values, the `contract:` line and the entry as
  published, three-tag digest equality, cold/warm, the rehearsal extraction, the two config-grep
  facts (pattern set from the tagged tree, the success line from the log).

**Acceptance Criteria:**
- [x] If the gate has not run: Implementation Notes carry `### US-003 — gate not run, <date>` naming
      the missing prerequisites **and the open unpublished-tag window on `main`** (US-004's pin
      commit sha and the `git revert` instruction); nothing else changes.
- [x] Pre-flight recorded: six checks green on the tagged commit; `export_contract --check` clean;
      `CONTRACT_VERSION` is `1.3.0`; US-004's two sweeps re-run and classified; the four anchors and
      the suite counts verified; the `awk` extraction recorded verbatim (first line `* ``1.3.0`` `, no
      `1.2.0` line); the cut time is outside 23:55–00:05 UTC.
- [x] `v1.2.0` is cut by the owner and no `searxng-v*` tag is pushed; `publish` is green; the
      multi-arch `1.2.0` image is on GHCR; run URL, tagged commit sha and OCI index digest recorded.
- [x] Four-way sha256 equality at `v1.2.0` verified and the four values recorded, the in-image leg
      read through the `@sha256:<index digest>` ref (the ref recorded).
- [x] `gh release view v1.2.0 --json body --jq '.body' | tr -d '\r'` matches `^contract: 1\.3\.0$` and
      contains every line of the rehearsal extraction (`grep -F` per line); both Release assertions
      green; assets `openapi.yaml` and `openapi.yaml.sha256` present.
- [x] `docker buildx imagetools inspect` prints one identical index digest for `latest`, `1.2` and
      `1.2.0`; recorded.
- [x] `secret-grep` and the publish config grep are green; the tagged `ci.yml`'s one `grep -Eiq '`
      line (found by value) carries exactly the tagged tree's `_REQUIRED_GREP_PATTERNS` alternatives;
      both success lines recorded; the rehearsal extraction's ruling letter equals the `### (`
      heading US-001 added to `contract/GOVERNANCE.md`.
- [x] If a replacement tag was cut, US-004's two by-value sweeps were re-run for the new value
      before US-005 and the withdrawn tag is recorded under § "Withdrawn tags", not § "Released
      versions" (both recorded).

### US-005: Post-release verification and the v1.2.0 handoff record (owner-executed)

**Priority:** P1

**Description:** As the owner, I want the published image verified from the tagged checkout in three
smoke runs and from a credential-free pull, the whole-tree leak check run, and the record Poppy pins
written here with the roadmap flipped — so the epic's own cache-integrity control is witnessed on the
released artifact and the consumer's re-vendoring session has one table to read. Owner-executed in the
same sitting as US-003 or a later one; if US-003 has not run, this story records that and stops.

**Independent Test:** *Gate run* → from the `v1.2.0` checkout `contract_smoke.py --image <digest ref>
--anchor contract/openapi.yaml.sha256` exits 0 with `--expect-status degraded` against a no-env
container and with `--expect-status healthy --timeout-seconds 540` against a weights-loaded one started
with `--env-file`; `compose/full.yml` at `1.2.0` reports `cache_unauthenticated` key-less and
`healthy` with `capabilities.cache_hmac_key: 1` keyed, with one cached `/retrieve` round-trip; the
credential-free pull after `docker image rm` exits 0; the whole-tree leak grep returns nothing; the
handoff table, the `docs/releases.md` placeholders and the roadmap flips are done. *Gate not run* →
Implementation Notes carry `### US-005 — gate not run, <date>` naming the missing prerequisite
(US-003's cut) and nothing else changes.

**Implementation Hints:**
- **Credential handling** (ruling R33 as corrected): `umask 077; f="$(mktemp)"; trap 'rm -f "$f"'
  EXIT`, the HF token via `read -rs` into `$f` (one line, `HF_TOKEN=…`), only ever `--env-file "$f"`;
  a **second** file `$g` (same recipe) for the `full.yml` runs, **written fresh from placeholders,
  never copied from a working `compose/.env`** (`.gitignore:35` ignores `compose/.env`; a working copy
  carries a live HF token and SearXNG secret): `HF_TOKEN=<read -rs>`, `SEARXNG_SECRET=placeholder-not-a-secret`
  (`compose/full.yml:91`'s `${SEARXNG_SECRET:?…}` interpolation must be satisfied even though SearXNG
  is not started), `FORAGE_CACHE_HMAC_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (32 `x`s) for the keyed
  run and the same file without that line for the key-less run; `docker compose --env-file "$g"`
  satisfies R33 (no `compose/.env` on disk). The output of `gh auth token`, the contents of `$f`/`$g`,
  `docker inspect` of any keyed container, `docker ps --no-trunc` and **`docker compose config`** are
  **never** pasted into the record.
- **Smoke, three ways, from the tagged checkout** (`git switch --detach v1.2.0 && uv sync --extra dev`),
  `<ref>` = `ghcr.io/washingbearlabs/forage@sha256:<index digest>`, `--anchor contract/openapi.yaml.sha256`
  of that checkout each time (never the downloaded copy, never the in-image file — GOVERNANCE
  "Consumers"): (1) `docker run --rm -d -p 127.0.0.1:8020:8020 --name forage-rel <ref>` with no env
  file and no volume, then `uv run python contract_smoke.py --image <ref> --expect-status degraded
  --anchor contract/openapi.yaml.sha256`; (2) the same image with `--env-file "$f"` and `-v
  forage-model-cache:/app/model-cache`, then `--expect-status healthy --timeout-seconds 540` — no
  `VALKEY_URL` in `$f` (an unreachable Valkey pins `cache_unavailable`); (3) **`compose/full.yml` at
  `1.2.0`** (R30), as its own command block: `docker compose --env-file "$g" -f compose/full.yml up -d
  --no-deps valkey forage` (SearXNG not started; the placeholder secret satisfies the interpolation).
  `compose/full.yml`'s volumes block declares `forage-model-cache:` with an explicit `name:
  forage-model-cache` — a **fixed name shared with `minimal.yml` on purpose** (its comment: "it lets
  the two fragments share one verified weight set instead of downloading ~270 MiB twice"), so project
  prefixing is bypassed and run (3) is **warm** whenever run (2) populated the volume, cold only on a
  clean host (salty, round 2 — the round-2 "materialises its own volume" claim was false). Keep the
  540 s budget for the clean-host case, polling `/health` with `contract_smoke.wait_for_health`
  semantics. Key-less
  → `status: degraded`, `cache_unauthenticated` in `degraded_reasons`; then `down` and `up` with the
  keyed `$g` → `status: healthy`, `capabilities.cache_hmac_key: 1`, one `/retrieve` round-trip against
  `https://example.com/` (fallback `https://www.iana.org/`) served from the cache on the second call
  (`cache_hit: true`) — a fetch failure on both targets is a verification-environment problem, not a
  release failure: retry against the alternate before stopping. Both transcripts recorded with the key
  and secret values absent; **`docker compose -f compose/full.yml down` afterwards — never `down -v`**:
  `-v` would delete the shared fixed-name `forage-model-cache` volume every local bring-up depends on;
  the project-scoped `forage-valkey-data` **is** removed explicitly (`docker volume rm
  <project>_forage-valkey-data`, name read from `docker volume ls`) as a teardown step, because it
  holds entries signed with the placeholder key (security, round 3).
- **Third-party view** (the `v1.1.0` US-003 procedure): `docker logout ghcr.io`, then **`docker image
  rm <ref>` before the pull** so the anonymous pull proves more than manifest resolution (audit -014),
  `docker pull <ref>`, then the login state decided and recorded (security, round 2): `gh auth token |
  docker login ghcr.io -u <user> --password-stdin` writes the owner's broad-scope PAT base64-encoded
  into `~/.docker/config.json` and leaves it there — restore only if the session needs the login
  afterwards, preferring a `read:packages`-scoped token, otherwise end with `docker logout ghcr.io`;
  the story records which (output never recorded); key-less `/health` from
  `compose/minimal.yml` at `1.2.0` showing `contract_version: "1.3.0"`, `promptguard_model`, no
  `brave_api_key`, no `cache_unauthenticated` (memory backend); one `/search` round-trip; the
  placeholder-key packaging run at zero spend with the leak check over `/health`, `/metrics`, `docker
  logs` for **both** placeholders (the Brave placeholder and the HMAC placeholder) — four zeros; and
  **the validation-422 runtime witness** (salty, round 3 — the only place uvicorn actually runs):
  one `POST /search` with `{"query": "<marker>", "providers": "<marker>"}` against the released
  container, then `docker logs forage-rel 2>&1 | grep -c '<marker>'` is 0 and the 422 body carries
  `loc`/`msg`/`type` plus the three `"[redacted]"` placeholders and nothing else — a fifth zero in
  the leak table.
- **Whole-tree leak check** before pushing the record: `git diff <pre-record commit>..HEAD | grep -nE
  'HF_TOKEN=|hf_[A-Za-z0-9]{20,}|FORAGE_BRAVE_API_KEY=|FORAGE_CACHE_HMAC_KEY=|SEARXNG_SECRET=|VALKEY_URL=[^[:space:]]*:[^[:space:]]*@|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_'`
  returns nothing (bare variable *names* in prose are fine; `NAME=` followed by a value is not — the
  placeholders themselves must not be pasted either); a hit found after the tag is pushed is a history
  rewrite, not an edit.
- **Record** in `### US-005 — v1.2.0 verification and handoff, <date>` under Implementation Notes: all
  three smoke transcripts with exit codes, the credential-free pull transcript, the key-less `/health`,
  the four-zero leak table, and the handoff table with the `v1.1.0` columns plus three new rows:
  **Cache-integrity posture** (witnessed: keyed `full.yml` healthy with `cache_hmac_key: 1`; key-less
  external Valkey reports `cache_unauthenticated` and serves unsigned; memory mode needs no key),
  **Model posture** (`promptguard_model` on `/health` — the configured id; 22M default; whether the
  86M is allowlisted and vendored, and the benchmark figures' location), **Envelope defaults**
  (`FORAGE_CPUS` unset = no limit, `FORAGE_MEM_LIMIT` `1024m`), and **Supply-chain posture** (security,
  round 2: no image signature, no provenance attestation, no SBOM — the `kit_tools/arch/SECURITY.md`
  non-vulnerabilities row; integrity is the four-way sha256 on the contract plus the recorded index
  digest; pin by digest for immutability). Then fill the three `docs/releases.md` placeholders (the
  heading date, the index digest, the tagged commit), delete the block's `NOT YET PUBLISHED` first
  line, and fill `kit_tools/SYNOPSIS.md:36`'s digest and tag claim; note any withdrawn tag.
- **Status flip — by value, not by line** (completionist, rounds 1 and 2; ruling R39): the criterion is
  `grep -rniE 'epic-forage-hardening|T2\.2|v1\.2\.0|forage-hardening' kit_tools/roadmap
  kit_tools/PRODUCT_VISION.md kit_tools/SYNOPSIS.md README.md contract/GOVERNANCE.md`, every hit
  classified as (a) a shipped statement (the `v1.2.0` date and a link to the handoff record) or (b) a
  history row, with **zero** remaining "Planned" / "planned 2026-09-19" / "pending" / "`validate-epic`
  next" hits for this epic. The named sites are the starting point: `kit_tools/roadmap/MILESTONES.md`'s
  hardening row (whose "two human gates" sentence is corrected to the **four** owner-executed stories
  R37 created), `:7` ("Current target"), `:11` (the status line) and `:86`; `kit_tools/roadmap/BACKLOG.md`
  (`:24`, `:32`, `:47-49` — the injection-corpus item records the epic as shipped and the corpus as
  plannable, which is the epic wrapper's "`epic-forage-injection-corpus` unblocked" completion
  criterion); `kit_tools/PRODUCT_VISION.md` § T2.2 → Shipped; GOVERNANCE § "Two semvers" "pending" →
  the date. The unpublished-tag window that existed between merge and cut is recorded as an accepted
  risk in the handoff (the quickstart pointed at an unpublished tag for that interval).

**Acceptance Criteria:**
- [x] If US-003 has not run: Implementation Notes carry `### US-005 — gate not run, <date>`; nothing
      else changes.
- [x] Smoke runs (1) and (2) from the `v1.2.0` checkout exit 0 with `--anchor contract/openapi.yaml.sha256`
      of that checkout; run (3) is recorded as its own command block with `--env-file "$g"` written
      from placeholders, `--no-deps valkey forage`, the shared fixed-name volume (warm or cold, stated),
      `cache_unauthenticated` key-less and `healthy` + `capabilities.cache_hmac_key: 1` + a cache hit
      keyed, ending with `docker compose down` **without `-v`** and the explicit `docker volume rm` of
      the project-scoped Valkey volume; commands and exit codes recorded; every
      credential reached a container only through `--env-file`; no `docker compose config` /
      `docker inspect` / `docker ps --no-trunc` output in the record.
- [x] Credential-free pull after `docker image rm` recorded (both exit 0); the GHCR login state at the
      end of the story is recorded (restored with a scoped token, or logged out); key-less `/health`
      shows `contract_version: "1.3.0"` and `promptguard_model`; the placeholder leak check is four
      zeros and the validation-422 marker witness (`docker logs` count 0, trio plus placeholders
      only) is the fifth.
- [x] The whole-tree leak grep (with the `SEARXNG_SECRET=` and `VALKEY_URL` credential patterns) over
      the record's diff returns nothing; both token files were mode 0600 and are recorded as deleted.
- [x] The handoff table with the four new posture rows is in the Implementation Notes;
      `docs/releases.md`'s `v1.2.0` block has its heading date, digest and tagged commit filled and
      its `NOT YET PUBLISHED` line removed, and `kit_tools/SYNOPSIS.md:36` its digest and tag claim; the by-value status grep over the roadmap, vision, synopsis, README and GOVERNANCE
      has zero unflipped hits for this epic (classified in Implementation Notes), MILESTONES' hardening
      row names the four owner-executed stories, and BACKLOG's injection-corpus item records the epic
      shipped and the corpus plannable.

## Edge Cases

- A route-level 4xx raised by a middleware (413 size, 404 gated `/extract`) — never reaches the
  validation handler; unchanged (US-001).
- A wrong-typed body on the multipart `/extract` route — FastAPI's documented 400 body-parse guard,
  not the handler; the handler case is a well-formed multipart with an invalid `extract_mode` (US-001).
- A validation error whose `loc` contains an integer index (`providers[3]`) — kept as `int` (US-001).
- A body producing thousands of validation errors — `detail` capped at `_MAX_VALIDATION_ERRORS`, one
  WARNING, the request still fully parsed (the exhaustion row, unchanged) (US-001).
- A future request model with `extra="forbid"` — the `loc` structural test in `tests/test_models.py`
  goes red (US-001).
- The handler runs with no `route` in `request.scope`, or for a route outside `_ROUTE_LOC_ALLOWLIST`
  — every non-framework `str` segment is dropped and one `validation_422_loc_dropped` WARNING is
  emitted; never a pass-through (US-001).
- A future validator raises `PydanticCustomError` with an interpolated code — the fuzz over
  `response.text` catches it in `type` (US-001).
- The `1.3.0` docstring entry accidentally contains a blank line — the extractor stops early and the
  read-back fails on the tag; `tests/test_ci_workflow.py` and US-003's rehearsal catch it before the
  push (US-002/US-003).
- A golden-visible addition nobody wrote into the docstring — the 1.3.0 coverage sweep fails (US-002).
- Specs 1 or 3 added a lettered GOVERNANCE section — this story takes the next letter; the
  `_RULING_MARKERS` duplicate check would catch a collision (US-001).
- Between the completion PR's merge and the tag push, `main`'s compose examples pull an unpublished
  tag — bounded to the same sitting; otherwise recorded as outstanding with the revert instruction
  (US-004/US-003).
- The publish rebuild is cold — expected to pass since the reproducibility fix; record it as the first
  live cold proof if so (US-003).
- `latest` or `1.2` lands on a different digest than `1.2.0` — stop; treat the tags as untrusted, do
  not re-run blindly (`docs/releases.md` § "When something goes wrong") (US-003).
- `publish` goes red on the pushed tag — a new tag, never a moved one; a published-then-bad image is
  withdrawn per § "Withdrawn tags" and named in the handoff — and the new tag re-runs US-004's
  by-value sweeps before US-005 (US-003/US-004/US-005).
- A consumer indexes `detail[].ctx` as a mapping — it receives the string `"[redacted]"` for the
  window; no such consumer is known (Assumptions) and the note names the drop (US-001).
- A healthy smoke against a container whose env file carried `VALKEY_URL` without the HMAC key —
  `cache_unauthenticated` pins `degraded`; a red under the wrong flag is a misconfigured verification,
  not a release failure (US-005).
- `compose/full.yml` refuses to start — `SEARXNG_SECRET` missing from `$g`; the placeholder line is
  required even with `--no-deps` (US-005).
- `docker compose down -v` after run (3) — would delete the shared fixed-name `forage-model-cache`
  volume; the story ends with `down` and says why (US-005).
- The derived GOVERNANCE letter is not `(e)` — every site carries the derived letter; the closing grep
  and US-003's rehearsal catch a stale `(e)` (US-001, US-003).
- The `/retrieve` round-trip target is down — try the alternate; both down is an environment problem,
  not a release failure (US-005).
- The credential-free pull says "Image is up to date" — the `docker image rm` step was skipped; redo
  it (US-005).

## Out of Scope

- Anything inside Poppy: the re-vendor, the pin bump, mirroring the new fields (Poppy's
  `epic-search-policy` successor reads the handoff table; nothing here pushes).
- A `searxng-v*` companion release.
- Tag-vs-digest policy for the compose examples (audit -010) — deferred to the next cut regardless of
  the owner's view at this one; the `1.2.0` tag pin lands in US-004 and stands, and the pinnable digest
  is offered in `docs/releases.md`. Image signing / provenance attestation — a later runbook item.
- A `/metrics` counter for the validation cap — the WARNING is the signal (Decisions Made).
- Bounding `detail[].msg` beyond pydantic's stock text, or a MAJOR bump of any kind.
- A script wrapping the verification half of the runbook (second-opinion) — a follow-up outside this
  epic; the procedure has now run three times by hand and the next cut is the moment to script it.

## Assumptions

- Poppy is the only consumer and validates 1.3.0 with every new field defaulted; a Poppy reader of
  `detail[].input` (none known) is the compatibility note's audience, and the placeholder keeps the
  key present for such a reader for one release.
- The six required checks and the `publish` lane are unchanged from `v1.1.0` except for the
  four-pattern config grep (spec 4).
- `contract_smoke.py` asserts `/health` by **membership** (`:392-401` `in degraded_reasons`,
  `:403-415` `in capabilities` — measured, not assumed), so `cache_unauthenticated` and
  `cache_hmac_key` cannot affect either flag; the `full.yml` witness is a separate `/health` read.
- The owner has `packages:write` on GHCR and an HF token for the healthy smoke, as at `v1.1.0`; the
  Valkey for the `full.yml` run is the fragment's own service.
- `tests/test_contract_schema.py::_SCHEMA_MODELS` remains the only producer of the golden.
- Specs 1 and 3 add prose lines under existing GOVERNANCE rulings rather than new lettered sections;
  if not, the derived-letter rule absorbs it.

## Technical Considerations

- **The 1.3.0 window — a draft to reconcile at execution** (ruling R36): `blocked_url` omission reason
  and the bounded `SearchResult.engine` (spec 1); `effective_promptguard_fail_closed` and
  `effective_promptguard_threshold` on `RetrievedContent`, `RetrieveErrorCode.extraction_failed`,
  `CacheMetrics.corrupt_entries` (spec 2); `SearchRequest.blocked_domains`,
  `SearchRequest.promptguard_threshold: float | None`, `RetrieveRequest.promptguard_threshold: float |
  None`, `effective_promptguard_threshold` on `SearchResponse`, the leading-dot allowlist form in three
  field descriptions, `policy_invalid_domain_entry` counters (spec 3); `cache_unauthenticated`
  `DegradedReason`, `capabilities.cache_hmac_key`, `CacheMetrics.integrity_rejects` (spec 4);
  `search.provider_compressed_body` (spec 5); `search.promptguard_latency_target_exceeded`,
  `promptguard_latency_max_ms` and the two rendered healthcheck descriptions (spec 6); `/health`
  `promptguard_model` and the three `promptguard_contiguity_detections` counters (spec 7); the
  request-validation 422 trio with the `_MAX_VALIDATION_ERRORS` cap, the one-release `"[redacted]"`
  placeholders and the five validation-422 descriptions (spec 8 US-001). Golden-visible items are the request/response model fields and enum
  members; counters and `CacheMetrics` fields are pinned by `tests/test_contract_metrics.py`.
- **Rotations (ruling 6):** US-001 and US-002 each edit `pipeline/contract.py` — two measured,
  recorded rotations at the five sites, with the count sentences moved by artifact (the ordinal of
  the last `docs/bootstrap-notes.md` rotation heading, cross-checked against the GOTCHAS table's rows
  minus its origin row, spelled through the extended `_NUMBER_WORDS`, grepped digit-or-word and
  case-insensitively), never by a word this spec was written with. Two rotations rather than one
  (salty, round 3): the R36 window block makes each window story's verifier see its own docstring
  line and recorded rotation; US-001 appends its clause, US-002 rewrites the entry into final form —
  two small, attributable invalidations over one unattributable one (the same reasoning spec 7
  records for US-006/US-007); a per-bucket summary in Implementation Notes is welcome. US-004, US-003 and US-005 rotate nothing (no hashed file; the tense flip lives in
  unhashed docs).
- **Anchor ownership (R19, R36):** every story in this epic that regenerates the document refreshes the
  four anchor-quoting pages in its own commit; US-002 confirms, US-003's pre-flight re-verifies.
- **Contract window (ruling 5):** US-002 is the only story allowed to declare the `1.3.0` golden
  frozen; any later shape change in this epic is a bug.
- **Announcement (GOVERNANCE step 7, search-epic ruling 31):** the extractor is bash + awk in
  `ci.yml`; the entry's formatting rules (column-0 bullet, two-space continuation, no blank line) are
  load-bearing and pinned by `tests/test_ci_workflow.py`.
- **Priorities:** all five are P1: US-001 is standalone user-visible value; US-002, US-003 and US-005
  are release-blocking; US-004 changes no behaviour but is a hard prerequisite of US-003's pre-flight
  (trimming it defers the whole cut), so it is not the trimmable one.
- **Reproducible builds:** cut outside the passwd-layer window (`docs/releases.md`).
- **Autonomous terminal state:** an autonomous run of this spec ends with US-003 and US-005 recorded
  as gates not run, the `docs/releases.md` placeholders unfilled and the roadmap reading Planned —
  the expected state; the epic cannot close without the owner gate (the wrapper's completion criteria
  say so).

## Related Documentation

- Governance: [`contract/GOVERNANCE.md`](../../contract/GOVERNANCE.md) (§ "Bumping the contract",
  § "Recorded rulings", § "Two semvers")
- Releases: [`docs/releases.md`](../../docs/releases.md)
- The `v1.1.0` record: [`archive/feature-search-release.md`](archive/feature-search-release.md)
- Security: [SECURITY.md](../arch/SECURITY.md) (request models, non-vulnerabilities table) and root
  `SECURITY.md`
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("A cold-cache publish fails its own parity gate";
  the rotation table)

## Implementation Notes

### Published-model failure and authorized replacement — 2026-09-23

PR #30 merged as `d747a2bd41da993914229c7f31622ab20148dc32`.
Actual-main CI `35891633312` and v1.2.0 tag CI `35892927967` passed;
publication completed at 17:08:19Z, index
`sha256:f96827955cd8b43c6b5a2c46d53637eded8b2030fda7f864f7f2f6ae62ea4858`.
All three aliases agreed; the four contract hashes matched
`74b9db01ab0b536e92cc54efe20c58ba4ed18ec531fe42a8ed4872f01115fa72`;
the Release carried all 112 announcement lines; anonymous pull after removal
succeeded using an isolated empty auth config without changing global login.

US-005 did **not** pass: no-env degraded smoke succeeded, but the healthy
warm-cache smoke timed out after 540 seconds. Real verified weights loaded,
then `model_labels_unexpected` refused them. The pinned config omits
`id2label`/`label2id`; transformers resolves `LABEL_0`/`LABEL_1`.
Spec 5's named-label-only assumption was false. No weights or manifest were
modified to bypass it. Diagnostic containers and temporary env files were
cleaned; the shared model volume and user's main checkout were preserved.

The owner selected: "Warn now; fix and publish v1.2.1, then withdraw v1.2.0
(delete its git tag and GHCR version)." The public warning is applied.
The replacement is on `fix/promptguard-labels-v1.2.1`, isolated from the
planning checkout. The published tag is never moved. Withdrawal remains
pending replacement verification, not falsely recorded as complete.

The repair accepts generic labels only for the exact verified 22M pin.
Explicit named-label handling is unchanged; unknown/reversed/non-binary
generic mappings still refuse. A genuine config fixture is hash-anchored to
the manifest and passed through real offline AutoConfig. Its two scoring
regressions failed before the fix and pass afterwards. A read-only,
network-disabled probe of the actual cached model loads successfully and
scores a benign input 0.001107 and an injection probe 0.997931.
This is not an 86M benchmark or a claim that those owner gates ran.

No response shape, contract version, model identity or hashed source changes.
The config fixture's exact-file token-scan exception is needed for long public
architecture/key names and guarded by its manifest hash; no payload directory
is exempted. The replacement pin fan-out follows US-003's recovery rule.
There is no `kit_tools/BUMP_VERSION.md`; the generic bump workflow cannot run.
The existing `docs/releases.md` / US-003 tag procedure is authoritative:
`pyproject.toml`'s version is inert packaging metadata and remains untouched.
The old runner remains stopped and epic completion remains blocked on
replacement publication, actual-image runtime verification and withdrawal.

Prepublication rehearsal completed against a newly built, unmodified local
candidate image `sha256:171c10d3cb42f4c9f14de836833bc935eee7e7a4f0e198c3bce212de69611038`
at 17:42:02Z: no-env degraded, warm-cache healthy, full Compose keyless
`cache_unauthenticated`, keyed healthy with signing enabled, two retrieves
with the second a cache hit, and minimal Compose search (200, three results).
All five marker counts were zero and the validation 422 had only the six
specified keys. Temporary files, project resources and the disposable
Valkey volume were removed; shared weights and global auth were preserved.
This is candidate evidence, not yet the published v1.2.1 witness.

### US-001

letter: (l)

**Retry preflight, 2026-09-23.** Retained the previous attempt's reservation,
derived from the last GOVERNANCE heading `(k)` at baseline
`7a4819b781a3f61e773ff56bbb12a6ed7257f929` (also this retry's clean starting
commit). No intervening `(l)` ruling exists. The prior attempt stopped at
35 exact-heading rotations versus 39 table-derived rotations and implemented
nothing. As requested by its verifier, reconciled only the heading forms for
the existing rotation-36 through rotation-39 records; their historical
measurements and body text are unchanged. The mandatory derivation still
uses the last exact `### The <ordinal> rotation:` heading, not heading count.
No owner gate or release action is part of this story.

**Implementation and measurements, 2026-09-23.** The preflight now agrees:
last exact bootstrap heading `thirty-ninth`, GOTCHAS 40 data rows minus
`At split` = 39. The required count-word sweep initially returned zero
because six prose sites still said 14, 23 or 38. Those sites were explicitly
reconciled to the artifact-derived 39, and the repeated
`\b(thirty-nine|39) (times|rotations)\b` sweep returned six hits (GOTCHAS
twice, TROUBLESHOOTING twice, DEPLOYMENT and SERVICE_MAP). All six now say
forty; SERVICE_MAP's revision moved from the table's pre-story `b641e6a5…`
to `bffeb7ba…`. This is a repair of stale prose, not a substitute derivation.

Ruling (l) is present once, after (k), and answers Example 6 steps 1–4.
The pre-story marker tuple had 12 entries (`_NUMBER_WORDS[12] == "twelve"`):
the explicit-path sweep found four hits, all now thirteen. Four additional
live prose sites still said five; those and CODE_ARCH's wrapped tree label
now say thirteen too. DECISIONS' dated five-rulings ADR heading is retained.
The introduction names five original contract rulings plus eight hardening
rulings, and tests pin the count and unique marker/headings correspondence.
`_NUMBER_WORDS` extends through forty because this retry's actual rotation
artifact has outgrown the spec's twenty-entry minimum.

The registered handler slices at `_MAX_VALIDATION_ERRORS = 100`, ignores
non-mappings, string-coerces messages/types, guards `loc` against owned route
fields plus framework strings and integer indexes, and catches construction
and JSON rendering into the fixed empty-detail 422. Neither the exception nor
its body/string/repr reaches a logger or is re-raised. The two WARNINGs carry
only counts and a matched template closed to the three routes or `other`.
The fallback does not turn failure into success. The three window keys are
always fixed placeholders; the constants/helper are explicitly retired with
the keys at the next MINOR in the release note. Pipeline refusals and both
middleware paths are unchanged.

**Per-field fuzz coverage (21 fields):**
- `SearchRequest`: `query`, `num_results`, `promptguard_threshold`,
  `promptguard_fail_closed`, `providers`, `blocked_domains`, `allow_paid_fallback`.
- `RetrieveRequest`: `url`, `extract_mode`, `cache_ttl_hours`, `trusted_domains`,
  `verified_domains`, `blocked_domains`, `promptguard_threshold`,
  `promptguard_fail_closed`.
- `/extract`: `file`, `filename`, `mime_hint`, `extract_mode`, `request_id`,
  `timeout_s`, derived from the live signature minus `request`.

Every case must place the marker in its own `exc.errors()[].input` and
produce its own field error. All current request-model fields lack custom
validators; the fuzz introspects decorator/Annotated metadata and requires a
semantic `value_error` or `assertion_error` for any that appear. The
counterexample temporarily patches the real `SearchRequest`'s compiled schema,
validator and decorator metadata with an interpolating after-validator,
rebuilds the real endpoint's route in a probe app (existing routes cache their
compiled schema), and proves the same fuzz assertion fails. JSON fields use
wrong-typed marker containers; string Form parameters receive an UploadFile
whose filename carries the marker (content bytes alone are not in its repr),
while the file, literal and numeric parameters receive marker form text.
The structural model canaries disallow mapping annotations/extra-forbid.

Named new guards include `test_validation_422_cap_and_closed_route` (50,000
errors, exactly 100 items, one WARNING, `/search`), the explicit normal-search
scope witness, signature/allowlist parity, fixed window placeholders,
caller-location dropping and absent/unknown-route fail-closed tests, root
DEBUG log capture with live httpx/WARNING canaries, four malformed-entry cases,
and failures in error access, `str()` and actual JSON UTF-8 rendering. Both
messages and log arguments are checked. Existing test functions were retained;
the pipeline 422, ASGI-only 413, actual multipart 400 and disabled-route 404
regressions pass unchanged.

`contract.py` alone changed among all nine hashed sources. Before/reverted:
`b641e6a51ef7cb45a5209a42321a5fff135f432264d256dd8eec9fe9e62698f5`;
after: `bffeb7bac1b319c566253ff7512ca61fad12df75ecbdb4d8284c9aeeb0d47fe1`.
Read-only whole-file substitution against the clean starting commit
reproduces the former under default and shipped config; all other hashed
sources/hash definition are unchanged. This is the fortieth rotation, not
a text-sanitization change; recorded at all five required sites.

Exporter regenerated the document, anchor and drift twin. Exactly six YAML
description paths changed (five source descriptions); no property changes.
Anchor `74b9db01ab0b536e92cc54efe20c58ba4ed18ec531fe42a8ed4872f01115fa72`
was copied into all four quoting pages, then `--check` passed. Re-created
`contract_1_3_0.json` through `_SCHEMA_MODELS`: **golden unchanged, expected**
(sha256 `2cfd8808eec3e533237fc45448be812e85c476c4153a7648ae140a58fffc2fe2`).
Neither validation model is in that producer set; no golden-visible field
moved and `_EXPECTED_ONE_THREE_ZERO_DIFF` is unchanged. Historical goldens
and root SECURITY.md are byte-identical. Both DNS-oracle rows survive unchanged.
The closing marker-home sweep finds both WARNING tokens in source, tests,
LOGGING, MONITORING and TROUBLESHOOTING. All new ruling references use (l);
the legitimate pre-existing engine ruling (e) references remain unchanged.

**Verification:** the final combined story-scoped run passed **1,615 tests**
across `test_contract_errors`, `test_models`, `test_governance_docs`,
`test_contract_export`, `test_contract_schema`, `test_contract_metrics`,
`test_app`, `test_orchestrator`, `test_ci_workflow` and
`test_sanitizer_revision` (13.54 s; three existing dependency/socket-guard
warnings). Repository-wide `uv run ruff check .`, `uv run ruff format --check .`
and `uv run pyright` pass (zero errors); export `--check` and `git diff --check`
pass. Only changed Python files received safe fixes/formatting. The full
`uv run pytest` suite was **not run**, honoring the story implementer's explicit
targeted-only instruction; its full-suite acceptance gate remains for the
authorized verifier, not claimed green from these scoped results. No story
definition or acceptance checkbox was modified. No image/model/benchmark,
push, tag, release, publication or owner-gate action was performed.

### Spec 2 US-005 consumer-note handoff (2026-09-22)

The existing release deliverable `docs/releases.md` now carries the pending-hardening
consumer note: callers sending `promptguard_fail_closed: false` may receive
unscanned-but-marked content after contention exceeds `promptguard_wait_seconds`;
`promptguard_state` / `suspicious` / `promptguard_unavailable` / `unscanned_results`
are the signals, and `promptguard_fail_closed_floor: true` is the operator control
(config.yaml-only until spec 6's bind-mount procedure). It preserves the trusted-tier
skip and VERIFIED exemption. Carry that sentence into US-004's release draft; no release
or owner gate was run here.

### US-002 — final contract record and golden freeze (2026-09-23 UTC)

**Baseline and reconciliation.** Started clean at
`3ea0b327177b4b9f3cf8bb18f0d01bccc9acb5b0`. Read the known risks and
`git log -p -- pipeline/contract.py`; the window opened at
`d920820f7931737933e6f6ee1cf71c49c1c94db5`. Reconciled the draft against
every continuation, not just its original checklist:

- Spec 1: `blocked_url`, bounded/normalised `engine`, post-extraction
  title/snippet truncation and both scan forms, canonical ASCII `domain`,
  embedded-private IPv4 and `.localhost` fetch refusals (rulings (e)/(f)).
- Spec 2: chunk-budget reason/window, classification waits and mixed
  scanned/unscanned search results, admission `busy`, worker
  `extraction_failed` and four PDF reasons, cache corruption misses, effective
  fail-closed/threshold fields. Retained `SearchResponse`'s fail-closed field,
  the two admission counters and both wait counters missing from the draft.
- Spec 3: directional domain matching and private-name precedence, byte-cap
  policy refusals, both policy counters on both routes, search denylist and
  threshold, null/config default and ceiling on both routes. The earlier
  retrieve-only ceiling clause is superseded, not retained as a false claim.
- Spec 4: integrity rejects, widened oversize-skip producers, unsigned-cache
  degradation and the boot signing capability.
- Spec 5: both compressed-body and timeout counters, SearXNG-only
  `unsupported_encoding`, and the production-unreachable all-paid prefix
  refusal. Retained the latter two items omitted by the draft.
- Spec 6: configurable loop target and high-water mark, both rendered
  liveness descriptions. The real field is `sanitization_latency_max_ms`,
  **not** the draft's `promptguard_latency_max_ms`.
- Spec 7: configured model identity, three contiguity counters and max-rule-only
  threshold descriptions. No model acquisition or benchmark is claimed here.
- US-001: the redacted validation trio, 100-entry cap, fixed placeholders for
  this minor release and their next-MINOR removal, under the existing (l).

The result is one 112-line `1.3.0` bullet with the required final additive
sentence. Publication state is removed, including the live `1.2.0` "no vendored"
and "now frozen" clauses. GOV's mapping retains the `v1.0.0` / `US-004`
worked example, states `v1.1.0` shipped 2026-09-18 serving 1.2.0, and names
`v1.2.0` / 1.3.0 as pending. Ruling (c) freezes the golden now.

**Guard-first evidence and baseline deviation.** Before any contract edit,
the new whole-bullet tense test failed on the live 1.3.0 "held ... until" clause
(`1 failed, 282 deselected in 0.67s`). The stale 1.2.0 "held ... publishes"
sentence was **already removed by d920820**, so it cannot honestly be reported
as present in this story's starting tree. The second red run read the original
unmodified `6b9ed32:pipeline/contract.py` bytes via a temporary read-only
`Path.read_text` substitution for that one path and ran
`pytest.main(["-q", "tests/test_ci_workflow.py", "-k", "docstring_entry_tense"])`.
No checkout/source file was restored or mutated. The real historical red output
is recorded verbatim below, with trailing whitespace stripped for the diff
check. Only afterwards was the live contract rewritten.
The test walks every bullet occurrence (including repeated versions), calls
the existing `_slice_entry`, and applies all three specified whole-entry
checks. A separate test now requires one complete bullet per version and
compares every version with the actual workflow awk. Five permanent
counterexamples retain the real period-containing stale clause and exercise
each publication pattern. All are selected by the story's `-k` expression.

**Golden and metrics scope.** Spec 1's "The golden gate" already provided
`_EXPECTED_ONE_THREE_ZERO_DIFF`, `_ONE_THREE_ZERO_DIFFED_SCHEMAS`,
`_diff_against_1_2_0` and the completeness test. Spec 4 had subsequently
added a seventh top-level `CacheMetricsResponse` entry and an exception to the
top-level pin, contrary to this close-out's explicit six-model rule. Restored
the six-model producer and removed that exception; moved the cache baseline
into the metrics module's five-section baseline, verified directly against
`v1.1.0:contract/openapi.yaml`. Nothing loses coverage: metrics model/handler/
document parity and dataclass parity remain, the original eight cache fields
must remain, and every one of the seventeen new metric fields must occur as
a literal section-qualified token in the sliced announcement. The unchanged
`cache.storage_oversize_skips` field's widened producers are named separately.

The schema expectation has exactly ten golden-visible additions: two health
paths (`promptguard_model`, `cache_unauthenticated`), two search request fields,
four effective-policy response fields, and the two shared 422 enum members.
The presence half pins each in the literal `contract_1_3_0.json` and in the
announcement; the completeness half requires the exact delta and both
six-model structural pins. The two original 1.2.0 test functions were compared
as source slices and are **byte-identical**; all older goldens are byte-identical.
The final hand-invoked producer was:

```python
path.write_text(json.dumps(_current_schemas(), indent=2, sort_keys=True) + "\n")
```

The frozen golden sha256 is
`79dd2564be91a3c2f2bd5fd91aa28c2387ab315e49a4c8f82d0e77ec42e4c7ef`;
the only golden diff is removal of the relocated cache schema.
`uv run python -m scripts.export_contract` regenerated the document, anchor
and drift twin with **zero byte diff**. Spec 4 already changed the capability
description to "Three keys ... contract 1.3.0", so no current-value source edit
or extra expectation path was necessary. All four quoting pages (API_GUIDE,
CI_CD, DEPLOYMENT, SERVICE_MAP) still equal the committed
`74b9db01ab0b536e92cc54efe20c58ba4ed18ec531fe42a8ed4872f01115fa72`.

**Python 1.2.0 sweep, classified by value.** The exact explicit source-path
sweep with `--include='*.py'` found **16 pre-story hits**, not the planning
snapshot's 20. Classification below covers all 16; the final tree has 13:

| Baseline site | Classification and action |
|---|---|
| `retrieval_app.py:554` / `HealthResponse.capabilities` | Brave capability provenance, retained; the current-version sentence is already 1.3.0. |
| `retrieval_app.py:584` / `HealthResponse.search_providers` | Added-in provenance, retained. |
| `models.py:393,420` / `SearchRequest.providers,allow_paid_fallback` | Honoured-from provenance, both retained. |
| `models.py:444,466,474` / `SearchResult.domain,content_kind,date` | Added-in provenance, all retained. |
| `pipeline/search_providers/brave.py:5`, `policy.py:1` | Contract provenance, both retained. |
| `pipeline/contract.py:33` | Historical 1.2.0 bullet, retained with timeless description history. |
| `pipeline/contract.py:63,65` | Stale "no vendored" and publication-state clauses, removed. |
| `pipeline/contract.py:72` | Provisional 1.2.0 `chunk` analogy, replaced by the final `blocked_url` announcement; no shipped addition dropped. |
| `pipeline/contract.py:304,315,474` | `ContentKind`, pre-producer `chunk`, `search_unavailable` provenance, all retained. |

No hits in `model_fetcher.py`, `cache.py`, `url_validator.py`, `promptguard/`,
`scripts/` or `contract_smoke.py`. The old request/route docstring divergence
clauses were already replaced by the shared policy descriptions; they were
not silently re-versioned here. `grep -c 'no vendored' pipeline/contract.py`
now prints 0. **Zero unclassified source hits.**

**Description sweep.** Executed
`git diff d920820..HEAD -- contract/openapi.yaml` before the final regenerate
and compared it again afterwards (the file is unchanged). Its 34 diff hunks
contain **49 changed description paths**, including descriptions on newly
added fields. Every path is classified below as **named** in the final entry;
none is deliberately omitted and none is unclassified. `S/` abbreviates
`components/schemas/`; `P/` abbreviates `paths/`; every row ends in
`/description`. This path-level accounting covers reflowed hunks without
mistaking YAML line wrapping for another contract change.

| # | Description path (suffix `/description`) | Named window item |
|---|---|---|
| 1 | `S/CacheMetricsResponse` | Wider oversize-skip producers |
| 2 | `S/CacheMetricsResponse/properties/corrupt_entries` | Corrupt cache misses |
| 3 | `S/CacheMetricsResponse/properties/integrity_rejects` | Pre-parse integrity/type/byte rejects |
| 4 | `S/CacheMetricsResponse/properties/storage_oversize_skips` | Both-backend write bounds |
| 5 | `S/ExtractionMetricsResponse/properties/promptguard_contiguity_detections` | Upload contiguity counter |
| 6 | `S/HTTPValidationError` | Redacted validation response |
| 7 | `S/HTTPValidationError/properties/detail` | Named 100-entry cap |
| 8 | `S/HealthResponse` | Status-only liveness description |
| 9 | `S/HealthResponse/properties/capabilities` | Boot Valkey signing capability; three keys |
| 10 | `S/HealthResponse/properties/promptguard_model` | Configured model identity, not load state |
| 11 | `S/Pipeline422ErrorResponse/properties/error` | Retrieve-only busy and PDF refusal codes/reasons |
| 12 | `S/RetrieveMetricsResponse/properties/busy_rejections` | Admission refusal counter |
| 13 | `S/RetrieveMetricsResponse/properties/classification_wait_timeouts` | Retrieve wait timeout counter |
| 14 | `S/RetrieveMetricsResponse/properties/policy_invalid_domain_entry` | Domain-entry drops |
| 15 | `S/RetrieveMetricsResponse/properties/policy_suffix_trusted_skip` | Wildcard trusted/verified resolutions |
| 16 | `S/RetrieveMetricsResponse/properties/promptguard_contiguity_detections` | Retrieve contiguity counter |
| 17 | `S/RetrieveMetricsResponse/properties/semaphore_saturation` | Admission saturation counter |
| 18 | `S/RetrieveRequest` | Shared route policy boundary |
| 19 | `S/RetrieveRequest/properties/blocked_domains` | Multi-label suffix denylist and apex caution |
| 20 | `S/RetrieveRequest/properties/promptguard_fail_closed` | Operator floor and trust-tier exemptions |
| 21 | `S/RetrieveRequest/properties/promptguard_threshold` | Null/config default, ceiling, max-rule-only scope |
| 22 | `S/RetrieveRequest/properties/trusted_domains` | Leading-dot skip and multi-tenant caution |
| 23 | `S/RetrieveRequest/properties/verified_domains` | Leading-dot unavailable exemption and caution |
| 24 | `S/RetrievedContent/properties/effective_promptguard_fail_closed` | Effective policy, not evidence of scanning |
| 25 | `S/RetrievedContent/properties/effective_promptguard_threshold` | Effective threshold and trust-tier exemptions |
| 26 | `S/SearchMetricsResponse/properties/classification_wait_timeouts` | Per-request wait budget |
| 27 | `S/SearchMetricsResponse/properties/policy_invalid_domain_entry` | Invalid denylist entries |
| 28 | `S/SearchMetricsResponse/properties/policy_suffix_trusted_skip` | Reserved zero on standard-tier search |
| 29 | `S/SearchMetricsResponse/properties/promptguard_contiguity_detections` | Search contiguity counter |
| 30 | `S/SearchMetricsResponse/properties/promptguard_latency_target_exceeded` | Once-per-request target overrun |
| 31 | `S/SearchMetricsResponse/properties/provider_compressed_body` | Non-identity upstream responses |
| 32 | `S/SearchMetricsResponse/properties/provider_timeouts` | Bounded interaction timeouts |
| 33 | `S/SearchMetricsResponse/properties/sanitization_latency_max_ms` | Whole-loop process-lifetime high-water mark |
| 34 | `S/SearchRequest` | Shared route policy boundary |
| 35 | `S/SearchRequest/properties/blocked_domains` | Operator-first denylist, byte-cap policy refusal |
| 36 | `S/SearchRequest/properties/promptguard_fail_closed` | Operator floor |
| 37 | `S/SearchRequest/properties/promptguard_threshold` | Optional/null threshold and independent contiguity |
| 38 | `S/SearchRequest/properties/providers` | Paid prefix and unreachable all-paid refusal |
| 39 | `S/SearchResponse/properties/effective_promptguard_fail_closed` | Effective fail-closed policy |
| 40 | `S/SearchResponse/properties/effective_promptguard_threshold` | Effective threshold |
| 41 | `S/SearchResult/properties/domain` | Canonical ASCII host and unchanged URL spelling |
| 42 | `S/SearchResult/properties/suspicious` | Unscanned-result caution and mixed responses |
| 43 | `S/ValidationErrorDetail` | Trio, cap, placeholders and next-MINOR drop |
| 44 | `P//extract/post/responses/422` | Validation arm/window, unchanged document refusal |
| 45 | `P//health/get` | Liveness, not readiness; no Compose restart |
| 46 | `P//retrieve/post` | Shared route policy boundary |
| 47 | `P//retrieve/post/responses/422` | Validation arm/window, unchanged pipeline refusal |
| 48 | `P//search/post` | Shared route policy boundary |
| 49 | `P//search/post/responses/422` | Validation arm/window, unchanged pipeline refusal |

The opening commit itself already included the engine bound and
`omitted_by_reason` description, so they are named in the record and pinned
by golden equality even though `d920820..HEAD` does not show them as new hunks.

**Forty-first rotation.** Before editing, the last exact bootstrap heading was
fortieth; GOTCHAS had 41 data rows minus `At split` = 40. The required
case-insensitive digit/word count sweep found six live prose sites (GOTCHAS
two, TROUBLESHOOTING two, DEPLOYMENT, SERVICE_MAP) plus CLAUDE's summary.
All were moved to forty-one, with the no-sanitization subtotal 32 → 33.
`contract.py` is the only changed hashed source. Default and shipped config
both measure `bffeb7bac1b319c566253ff7512ca61fad12df75ecbdb4d8284c9aeeb0d47fe1`
before and `6884dc29b3dc3d7a0a1f2c1da638f767fb301b2baac68446541f6de2638bd7ec`
after; read-only whole-file reversal reproduces the former exactly. The other
eight sources and hash definition are byte-identical. Recorded at bootstrap,
CLAUDE, DECISIONS, GOTCHAS and CODE_ARCH; SERVICE_MAP's current value follows
the measurement. No runtime behavior changes, but old cache keys invalidate.

**Verification.** The combined six-module scoped run passed **648 tests**:
`test_contract_schema`, `test_contract_metrics`, `test_ci_workflow`,
`test_governance_docs`, `test_contract_export`, `test_sanitizer_revision`.
The final repeat passed all 648 in 3.60 s; the exact
`-k 'extractor or docstring_entry or tense'` selector passed 11 tests.
Repository-wide Ruff lint/format, strict Pyright, export `--check` and
`git diff --check` pass. A read-only audit confirmed all 49 description paths
appear exactly once in the classification table, the recorded extractor is
byte-exact, the five section baselines and two cache additions retain coverage, and the
contract's executable AST is unchanged.
Only changed Python files received safe fixes/formatting. The full suite is
**not run**, as explicitly prohibited by the implementer instructions;
full-suite acceptance remains for the authorized verifier. No story
definitions or acceptance checkboxes changed. No image build, model
acquisition, benchmark, owner gate, push, tag, release or publication ran.

#### US-002 historical tense guard — verbatim red run

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_ TestReleaseContractMapping.test_docstring_entry_tense_has_no_publication_state _

self = <tests.test_ci_workflow.TestReleaseContractMapping object at 0x10e63d880>

    def test_docstring_entry_tense_has_no_publication_state(self) -> None:
        source = (_REPO_ROOT / _CONTRACT_SOURCE_FILE).read_text(encoding="utf-8")
        bullets = list(re.finditer(r"^\* ``(\d+\.\d+\.\d+)`` ", source, re.M))
        assert bullets
        for bullet in bullets:
            entry = _slice_entry(source[bullet.start() :], bullet.group(1))
>           assert not (
                re.search(r"\bheld\b.*\b(until|pending)\b", entry, re.S)
                or re.search(r"\buntil\b.*\bpublish(es|ed)\b", entry, re.S)
                or "published by" in entry
            ), (
                "docstring entries carry no publication state — it lives in "
                "docs/releases.md and GOVERNANCE § Two semvers; rephrase, "
                f"do not delete the guard:\n{entry}"
            )
E           AssertionError: docstring entries carry no publication state — it lives in docs/releases.md and GOVERNANCE § Two semvers; rephrase, do not delete the guard:
E             * ``1.2.0`` — ``/search``'s ``SearchResult`` gained ``content_kind``
E               (``"snippet"`` | ``"chunk"``, defaulted), ``date`` (a strict
E               ``YYYY-MM-DD`` calendar date or ``None``, defaulted) and ``domain`` (the
E               lower-cased hostname of ``url``, required); ``SearchResponse`` gained
E               ``provider_used`` (required — the serving provider's name),
E               ``fallback_fired`` (defaulted) and ``provider_errors`` (defaulted);
E               ``SearchRequest`` gained ``providers`` and ``allow_paid_fallback`` (both
E               defaulted — a restrict-only per-request policy over the configured
E               chain); ``HealthResponse`` gained ``search_providers`` (the resolved
E               chain's names, in traversal order) and its ``capabilities`` description
E               now names ``brave_api_key`` alongside ``search_sanitization``; and
E               ``search_unavailable`` joined the ``/search`` 422 vocabulary, naming an
E               exhausted provider chain — a new enum *member*, MINOR under
E               ``contract/GOVERNANCE.md`` ruling (b) and carrying that ruling's
E               announcement obligation. ``/metrics``'s ``search`` section gained three
E               counters — ``fallback_fired``, ``paid_calls`` and
E               ``policy_unknown_provider`` — pinned against the handler by
E               ``tests/test_contract_metrics.py`` rather than by the golden fixture.
E               Two ``/search`` refusal ``reason`` *texts* also narrowed in
E               ``search-provider-abstraction`` US-002 and ride this bump:
E               ``searxng_error`` now reads ``SearXNG returned HTTP error (http_<status>)``
E               and ``searxng_unavailable`` now reads ``SearXNG not reachable at
E               <scheme://host:port>: <detail>`` — no exception text, no userinfo — neither
E               changing a code, a status, or the body shape. Every addition above is
E               additive — a new field, a new enum member, or a new counter — so a
E               consumer comparing MAJOR keeps working untouched; nothing was removed and
E               no field changed meaning. The ``/search``/``/retrieve`` boundary text
E               written into both routes' descriptions and the
E               ``SearchRequest``/``RetrieveRequest`` model docstrings
E               (``search-policy-and-health`` US-003) landed inside this same unpublished
E               window and is not a separate PATCH: there is no vendored 1.2.0 copy yet to
E               re-vendor, so the description edits are subsumed by this unreleased
E               MINOR. This version is **held**: ``tests/golden/contract_1_2_0.json`` is
E               regenerated in place across ``search-provider-abstraction`` specs 2-4 and
E               every ``search-fallback``/``search-policy-and-health`` story that moved
E               this shape, until the ``v1.1.0`` image publishes it.
E
E           assert not (<re.Match object; span=(2223, 2440), match='held**: ``tests/golden/contract_1_2_0.json`` is\n>)
E            +  where <re.Match object; span=(2223, 2440), match='held**: ``tests/golden/contract_1_2_0.json`` is\n> = <function search at 0x104f05940>('\\bheld\\b.*\\b(until|pending)\\b', '* ``1.2.0`` — ``/search``\'s ``SearchResult`` gained ``content_kind``\n  (``"snippet"`` | ``"chunk"``, defaulted), ``...rch-fallback``/``search-policy-and-health`` story that moved\n  this shape, until the ``v1.1.0`` image publishes it.\n', re.DOTALL)
E            +    where <function search at 0x104f05940> = re.search
E            +    and   re.DOTALL = re.S

tests/test_ci_workflow.py:2357: AssertionError
=========================== short test summary info ============================
FAILED tests/test_ci_workflow.py::TestReleaseContractMapping::test_docstring_entry_tense_has_no_publication_state
1 failed, 282 deselected in 0.59s
```

#### US-002 release extractor — verbatim final output

The workflow's own awk, through `_run_entry_extractor`, emits the following
112 lines (sha256 `7bdf3582aced1b2ce15c568defea6b38ff320592368f041474cd6f78dd41e928`).
No 1.2.0 line is included.

```text
* ``1.3.0`` — ``SearchResponse.omitted_by_reason`` gains ``blocked_url``
  (``OMIT_BLOCKED_URL``) for the search-time URL audit and domain policy.
  ``SearchResult.engine`` is NFC-normalised, stripped of C0/C1 controls,
  whitespace-collapsed and truncated to 64 characters; non-string or empty
  values become ``None``. It remains outside structural and PromptGuard
  scanning (GOVERNANCE ruling (e)). ``title`` and ``snippet`` are truncated
  after Stage 1 extraction, not before: padded and markup-dense inputs can
  serve different byte counts, and payload-shaped escaped markup is blocked
  as ``structural_blocked`` rather than served stripped. Both newline-preserving
  and whitespace-collapsed text forms are scanned. ``SearchResult.domain`` is
  the canonicalised ASCII host (UTS-46 punycode for internationalised names);
  ``url`` retains the provider's spelling. On ``/retrieve`` and ``/extract``,
  IPv6 literals embedding private IPv4 (6to4, Teredo, NAT64 and IPv4-compatible)
  and names under ``.localhost`` are refused ``private_ip`` rather than
  fetched (expedited MINOR without a compatibility window, ruling (f)).
  ``Pipeline422ErrorResponse.error`` gains ``busy`` and ``extraction_failed``
  (ruling (b)): both arrive only on ``/retrieve``, though the shared model
  also widens ``/search``'s enum. Admission refusal is 422 ``busy`` /
  ``admission_queue_full``, not ``/extract``'s 429; queue depth and reserved
  bytes are bounded by ``retrieve.admission_queue_depth`` and
  ``retrieve.max_queued_fetch_bytes``, with ``retrieve.fetch_concurrency``
  fixed at one. Fetched PDFs run in ``/extract``'s rlimited worker; failures
  formerly answered 500 now use ``extraction_failed`` with ``pdf_encrypted``,
  ``pdf_no_text``, ``pdf_extraction_error`` or ``pdf_spool_error`` reasons.
  A PDF over ``extraction.max_promptguard_chunks`` is ``content_too_large`` /
  ``promptguard_budget``. That reason also refuses pages over the opt-in
  ``retrieve.max_promptguard_chunks`` budget: ``0`` preserves the unbounded
  default for this minor release, ``retrieve_budget_unset`` warns of the next
  MINOR's default 256, and ``0`` remains an opt-out afterwards (ruling (g)).
  ``RetrievedContent.effective_promptguard_fail_closed``,
  ``RetrievedContent.effective_promptguard_threshold``,
  ``SearchResponse.effective_promptguard_fail_closed`` and
  ``SearchResponse.effective_promptguard_threshold`` are defaulted fields
  stamped on every 200, including cache hits. They report policy, not scanning;
  the operator floor bounds fail-closed on both fetch routes and the ceiling
  bounds both thresholds, without overriding trusted-tier classification skip
  or VERIFIED unavailable fail-open. ``/extract`` remains fail-closed and
  carries neither field. ``SearchRequest.promptguard_threshold`` is optional;
  both it and ``RetrieveRequest.promptguard_threshold`` accept null or omission
  for the validated configured default (shipped 0.85), before the operator
  ceiling (ruling (i)). Route/model boundary descriptions name the shared
  threshold, fail-closed and blocked-domain policy. Threshold descriptions
  apply to the max-score rule only: the opt-in server-side contiguity rule
  can block independently and ships disabled.
  The three ``RetrieveRequest`` domain-list descriptions specify directional
  matching: multi-label denylists cover subdomains, while bare allowlist
  entries match exactly and a leading dot opts into apex and subdomains.
  IP literals and single-label denylists match exactly. Leading-dot
  ``trusted_domains`` skips classification across the suffix;
  ``verified_domains`` degrades open when unavailable, even under the floor
  or a classification wait timeout; neither should name a multi-tenant apex.
  Canonical private-name rejection precedes caller denylists: a host matching
  both becomes ``private_ip`` rather than ``blocked_domain`` (ruling (h)).
  Optional ``SearchRequest.blocked_domains`` merges after the operator's
  ``seed_blocklist``; either omits matching results as ``blocked_url`` before
  content scanning without paid fallback. An over-budget denylist is refused
  whole with ``policy_domain_list_too_large``: ``content_too_large`` on
  ``/retrieve``, ``search_unavailable`` on ``/search``, non-retryable policy
  refusals. Allowlists instead drop their over-budget remainder.
  ``SearchResult.suspicious``'s corrected description includes unscanned
  results: on ``promptguard_unavailable: true``, consumers treat suspicious
  results as unscanned, not scanned-and-flagged. A single response can mix
  scanned and unscanned results because classification wait is one budget
  per request. ``SearchRequest.providers`` documents the paid-prefix rule
  and ``provider_used`` diagnosis: later-paid-only selection on an all-paid
  chain yields ``search_unavailable`` / ``policy_excluded_all_providers``.
  With one registered paid backend and duplicate collapse this changed
  outcome is not production-reachable (ruling (k), on (a2)'s basis);
  ``search.policy_unknown_provider`` counting is unchanged.
  ``HealthResponse.degraded_reasons`` gains ``cache_unauthenticated`` for
  unsigned Valkey; ``capabilities`` gains ``cache_hmac_key`` when Valkey
  signing is enabled at boot, independent of connectivity and absent in
  memory mode. ``HealthResponse.promptguard_model`` reports the configured
  model id whether loaded or not; ``promptguard_loaded`` still reports serving
  state. Both healthcheck descriptions now call the shipped Compose
  ``curl -fsS -o /dev/null`` probe status-only liveness, not body health;
  Docker-healthy does not imply loaded weights and Compose does not restart
  on an unhealthy probe.
  ``/metrics`` adds ``retrieve.classification_wait_timeouts``,
  ``search.classification_wait_timeouts``, ``retrieve.semaphore_saturation``,
  ``retrieve.busy_rejections``, ``retrieve.policy_invalid_domain_entry``,
  ``retrieve.policy_suffix_trusted_skip``, ``search.policy_invalid_domain_entry``
  and ``search.policy_suffix_trusted_skip`` (the last stays zero on standard-tier
  search). Domain counters report invalid/over-budget allowlist drops and
  wildcard trusted/verified resolutions. ``cache.corrupt_entries`` counts
  stored JSON/schema failures treated as misses rather than 500s; parse
  success is not authenticity. ``cache.integrity_rejects`` counts rejected
  signatures, envelopes, byte bounds and Valkey types before parsing.
  ``cache.storage_oversize_skips`` now counts Forage's write-side byte-bound
  refusals on both backends, not just memory (same meaning, wider producers,
  ruling (j)). ``search.provider_compressed_body`` and
  ``search.provider_timeouts`` count bounded upstream interactions; on a
  configured ``[searxng]``-only chain, ``searxng_unavailable`` reasons may
  end in ``unsupported_encoding``. Brave details stay internal, with only
  failure class wire-visible. ``search.promptguard_latency_target_exceeded``
  counts requests over the configurable target once per request;
  ``search.sanitization_latency_max_ms`` is the process-lifetime high-water
  mark of the whole result loop (structural scan, PromptGuard and semaphore
  wait), not a single wait. ``retrieve.promptguard_contiguity_detections``,
  ``search.promptguard_contiguity_detections`` and
  ``extraction.promptguard_contiguity_detections`` count contiguity blocks,
  including both-rule verdicts. These metrics are pinned by
  ``tests/test_contract_metrics.py``, not the schema golden.
  The request-validation 422 body no longer echoes the request:
  ``loc``, ``msg``, ``type`` per entry, at most ``_MAX_VALIDATION_ERRORS``
  (100) entries, and for this contract version ``input``, ``ctx`` and ``url``
  present with the fixed value ``"[redacted]"`` — an expedited MINOR under
  Example 6 step 1: the shipped description documented pydantic's extra keys;
  consumers reading ``detail[].input`` must stop — the three keys are dropped
  at the next MINOR (GOVERNANCE ruling (l)).
  Every addition above is additive except the request-validation 422 trim
  (ruling (l)); a consumer comparing MAJOR keeps working untouched.
```

### US-004 — pre-release bookkeeping (2026-09-23 UTC)

Implemented against clean `2cd2e1b` without changing runtime behavior or running
an owner gate. The current image **target** is `v1.2.0`, serving frozen
contract `1.3.0`; no wording promotes that target to a published release.
The draft's heading date, index digest and tagged commit remain explicit
placeholders beneath `NOT YET PUBLISHED — filled by US-005`.

#### Both by-value fan-outs — exhaustive final classification

Ran the two literal-value greps independently over exactly:

```text
README.md CLAUDE.md contract/ docs/ compose/ contract_smoke.py
kit_tools/docs kit_tools/arch kit_tools/testing kit_tools/roadmap
kit_tools/SYNOPSIS.md kit_tools/AGENT_README.md kit_tools/PRODUCT_VISION.md
```

Commands: `grep -rn --exclude=openapi.yaml --exclude=openapi.yaml.sha256
'1\.2\.0' <paths>` and the same command with `'1\.1\.0'`.
Excluded by name: `kit_tools/specs/` (including archives and this record),
`kit_tools/.seed_cache/`, `kit_tools/EXECUTION_LOG.md`,
`kit_tools/SESSION_SCRATCH.md`, `kit_tools/.validate_epic_*`,
`tests/golden/`, `contract/openapi.yaml` and
`contract/openapi.yaml.sha256`. The generated pair is checked by
`uv run python -m scripts.export_contract --check`, never hand-edited or
classified by grep. The explicit path set also excludes machine-written
execution/result trees. Root `SECURITY.md` and `.github/` were separately
checked for the support-policy and pattern-count criteria.

The tables enumerate **matching lines**, just as `grep -rn` does (a line
containing several instances is one hit). Line numbers identify the final
US-004 tree outside this excluded spec. Keys: **H** = (a) historical release
or completed-feature record; **R** = (a) rotation record; **P** = (b) contract
provenance, retained baseline or worked compatibility example; **I** = (c)
image tag/target/example; **C** = (d) rewritten Compose pin comment.
No current-contract statement remains at either retiring value.

**`1.2.0`: 115 matching lines, 115 classified, zero unclassified.**

| Path | Classification and final line numbers |
|---|---|
| `README.md` | I: 74, 247 |
| `CLAUDE.md` | R: 204, 229, 539 |
| `contract/GOVERNANCE.md` | H: 62, 262; I: 63; P: 488, 562 |
| `docs/releases.md` | I: 20, 29; H: 148 |
| `docs/bootstrap-notes.md` | R: 70, 75, 340, 364, 382, 517, 531, 538, 724, 732, 1501, 1958, 1979; I: 1577, 1995; H: 1994 |
| `docs/configuration.md` | P: 1138 |
| `compose/minimal.yml` | C: 52, 53, 65; I: 68 |
| `compose/full.yml` | C: 41, 42; I: 46 |
| `contract_smoke.py` | I: 66, 97, 99 |
| `kit_tools/docs/GOTCHAS.md` | R: 587, 592, 619, 672, 673 |
| `kit_tools/docs/TROUBLESHOOTING.md` | P: 207, 759; I: 872, 873, 879 |
| `kit_tools/docs/LOCAL_DEV.md` | I: 232, 234 |
| `kit_tools/docs/DEPLOYMENT.md` | I: 87, 89, 94, 105, 118, 145, 147, 203, 222, 246, 271 |
| `kit_tools/docs/CI_CD.md` | I: 350, 517, 522, 523, 537 |
| `kit_tools/docs/API_GUIDE.md` | P: 111, 286, 287, 290, 291, 309, 473, 493, 495 |
| `kit_tools/docs/MONITORING.md` | P: 68, 150; I: 528 |
| `kit_tools/arch/patterns/ERROR_HANDLING.md` | P: 170 |
| `kit_tools/arch/INFRA_ARCH.md` | I: 146, 160, 161, 200 |
| `kit_tools/arch/CODE_ARCH.md` | R: 183, 193, 430 |
| `kit_tools/arch/SERVICE_MAP.md` | I: 75, 195, 395, 397, 399 |
| `kit_tools/arch/SECURITY.md` | P: 615, 616, 624 |
| `kit_tools/arch/DECISIONS.md` | P: 228; I: 243; H: 244; R: 632, 637, 663 |
| `kit_tools/testing/TESTING_GUIDE.md` | I: 148; P: 164 |
| `kit_tools/roadmap/BACKLOG.md` | I: 24, 32 |
| `kit_tools/roadmap/MILESTONES.md` | I: 7, 52, 57, 86; H: 29; P: 56 |
| `kit_tools/SYNOPSIS.md` | I: 30, 36 |
| `kit_tools/PRODUCT_VISION.md` | H: 94 |

**`1.1.0`: 57 matching lines, 57 classified, zero unclassified.**

| Path | Classification and final line numbers |
|---|---|
| `README.md` | P: 284 |
| `CLAUDE.md` | R: 192, 195 |
| `contract/GOVERNANCE.md` | P: 53, 70, 128, 182, 249, 253, 433; H: 56, 58, 62, 265 |
| `docs/releases.md` | H: 146, 173, 236 |
| `docs/bootstrap-notes.md` | R: 66, 67, 175, 194, 259, 364, 1753; H: 1994 |
| `docs/configuration.md` | P: 1137 |
| `kit_tools/docs/GOTCHAS.md` | R: 583, 584, 653, 665 |
| `kit_tools/docs/TROUBLESHOOTING.md` | P: 759 |
| `kit_tools/docs/DEPLOYMENT.md` | P: 267 |
| `kit_tools/docs/CI_CD.md` | H: 347, 348 |
| `kit_tools/docs/API_GUIDE.md` | P: 109, 493 |
| `kit_tools/arch/INFRA_ARCH.md` | H: 193, 195 |
| `kit_tools/arch/CODE_ARCH.md` | R: 174, 178 |
| `kit_tools/arch/SERVICE_MAP.md` | H: 124 |
| `kit_tools/arch/DECISIONS.md` | P: 228, 768; H: 244, 872; R: 628, 629 |
| `kit_tools/roadmap/BACKLOG.md` | H: 57 |
| `kit_tools/roadmap/MILESTONES.md` | H: 13, 25, 26, 76, 77, 86 |
| `kit_tools/PRODUCT_VISION.md` | H: 94, 132, 142 |

The GOV first-release worked example is historical, not a current-version
claim. DECISIONS' latest-*published* mapping is also intentionally retained:
the owner has not cut the new image. Roadmap/vision completion statuses are
not advanced; that is US-005's handoff.

README, CLAUDE invariant 4, GOVERNANCE's current-version/mapping paragraphs,
CODE_ARCH, API_GUIDE's current-value row, SERVICE_MAP, TROUBLESHOOTING and
MONITORING already named current contract `1.3.0` from prior stories.
Confirmed them rather than manufacturing a new change. API_GUIDE's existing
1.3.0 history now summarizes the complete frozen record; MONITORING already
says **three** capability keys, including `cache_hmac_key`. Every active
release example now targets `v1.2.0` with the publication caveat; the
additional stale `forage:1.0.0` plain-Docker example was advanced too.
Both previously published `docs/releases.md` blocks and root SECURITY's
supported-versions table are byte-identical to the baseline. Only the
support paragraph and README's false pre-1.0 claim change era.

#### Pins, signing posture, counts and unchanged artifacts

`grep -c 'forage:1.2.0' compose/minimal.yml compose/full.yml` returns **1, 1**;
`_FORAGE_RELEASE_TAG` is `1.2.0`. Anchored line greps confirm both
`FORAGE_CPUS`/`FORAGE_MEM_LIMIT` interpolations (**2, 2**) and both bare model
variables (**2, 2**). The bare HMAC key is intentionally **0, 1**:
minimal is Valkey-free; full alone needs it. No envelope/default/model/key
wiring was altered. The Compose signing prose retains the existing
health-necessity statement and adds the consequence/remedy sentence.
Its existing test now also checks both that comment and README's quickstart
for unsigned cached content, `cache_unauthenticated` and setting the key.

The draft mirrors the final contract announcement, distinguishes the
unrun 86M owner gates from shipped selection/disabled contiguity support,
states the validation-422 next-MINOR removal and retrieve-budget window,
and preserves the prior whole-interaction timeout/compression upgrade note.
Its external-Valkey upgrade action requires a high-entropy, per-deployment
key and links the canonical generation/stop-all-replicas rotation procedure.
The recorded index digest is explicitly the immutable pinnable form;
the tag remains the quickstart default.

`_REQUIRED_GREP_PATTERNS` has **4** entries (measured from the test's AST).
The case-insensitive `three patterns` grep over `.github kit_tools/docs
kit_tools/arch kit_tools/testing docs README.md SECURITY.md` returns **zero**.
All six originally named sites already say four, including workflow `:552`
and architecture SECURITY `:435`; spec 4 left no prose to repair.

`uv run pytest --collect-only -q` collects **4126**. Independently,
`uv run pytest --collect-only -qq tests/test_*.py` selects every module and
reports each module's count; all **38** TESTING_GUIDE rows match, sum to
4126, and the four total-count sites agree. There are **41** top-level
test Python files including the three support modules. Newly restored
table rows are retrieve admission and search policy. The release-specific
rows are errors **53**, governance **69**, schema **17**, Compose **79**,
workflow **289**, export **177**, smoke **94** and metrics **69**.
Descriptions name the 422 non-reflection tests, exact 1.3.0 additions sweep,
and whole-entry tense guard. Collection is not advertised as a suite pass.

The four current anchor homes (API_GUIDE, CI_CD, DEPLOYMENT, SERVICE_MAP)
and the draft quote
`74b9db01ab0b536e92cc54efe20c58ba4ed18ec531fe42a8ed4872f01115fa72`.
All generated artifacts and historical goldens, including frozen 1.3.0,
are byte-identical to `2cd2e1b`. All nine hashed sources, hash definition,
config, lock and weights manifest are unchanged. Default and shipped
config still derive
`6884dc29b3dc3d7a0a1f2c1da638f767fb301b2baac68446541f6de2638bd7ec`.
There is **no rotation** to append to CLAUDE/bootstrap. GOTCHAS already has
the split value plus **41** rotations, all present in bootstrap, ending at
that same value; its count/table needed no edit.

#### Verification and outstanding release handoff

The final six complete affected modules pass **725 tests**: Compose,
contract smoke, governance, workflow, schema and export. The first scoped
run caught removal of the existing literal health-prose guard; restored
that claim and strengthened the same test with the signing consequence,
then reran all six modules green. Safe Ruff fixes/formatting were limited
to the two changed Python files. Repository-wide Ruff lint and format
checks, strict Pyright (**0 errors**), export `--check` and `git diff --check`
pass. No full-suite run was performed: the story-implementer instruction
explicitly prohibits it. The full-suite acceptance gate remains
**unverified**, not satisfied by these 725 passes.

**Gate not run — outstanding, not a closed decision.** No owner
authorization/evidence for US-003 or US-005 is available in this task.
Merging these pins into `main` starts an unpublished-tag window: the
quickstart fails until `v1.2.0` publishes. The owner must cut from the
completion PR's merge commit in that same sitting; otherwise revert the
US-004 pin commit, **`e12182f02ae977336b44e19040305a38d3e519c9`**:
`git revert e12182f02ae977336b44e19040305a38d3e519c9`.
That commit contains both pins, their test and the coordinated documentation;
this follow-up only records its now-known identity, without amending it.

**PR-description criterion blocked.** `gh pr view` found no PR for
`epic/forage-hardening-US-004-attempt-1`; the open-PR list contains only
the unrelated injection-corpus plan. A targeted all-state hardening lookup
finds only the already merged/closed planning PRs, no completion PR.
No unrelated PR was edited and no branch was pushed to create one.
Before merge, copy this **outstanding item** into the completion PR description:

> **Outstanding: unpublished-tag window on main.** Compose pins the not-yet-
> published `v1.2.0` image. Run owner-gated US-003 from this PR's merge commit
> in the same sitting; if the cut is not run,
> `git revert e12182f02ae977336b44e19040305a38d3e519c9`.
> The release and US-005 post-release verification are not complete.

No credentials were inspected; no model acquisition, image build,
benchmark, owner gate, push, tag, release or publication was performed.
Story definitions and acceptance checkboxes are unchanged. Result remains
**partial / needs-work** solely for the unavailable completion-PR
description and the explicitly deferred full-suite gate.

#### US-004 retry — completion PR notice persisted (2026-09-23)

Restored the verified implementation by fast-forwarding this retry branch
from `2cd2e1b` through `e12182f02ae977336b44e19040305a38d3e519c9`
and `8e934c49723f1553c77668ad2de40b66c4f33f67`. Neither commit was
rewritten, so the pin rollback instruction above still names the original
ancestor commit. No runtime, generated artifact or revision input changed.

**The missing PR-description criterion is now satisfied.** Created the actual
draft completion PR [#30](https://github.com/WashingBearLabs/Forage/pull/30),
`epic/forage-hardening-US-004-attempt-2` into `main`. Its description explicitly
names the **outstanding unpublished-tag window on main**, requires the owner
to cut from this PR's merge commit in the same sitting that it merges, and
gives the exact fallback command:

```bash
git revert e12182f02ae977336b44e19040305a38d3e519c9
```

Read the persisted description back with `gh pr view 30 --json ...`;
verified its complete body against the submitted text, the outstanding notice,
same-sitting merge-commit requirement, exact revert command, open/draft state
and `main` base. This is remote PR evidence, not merely a repository-only
notice. The attempt-1 blocked finding above is historical and superseded.
The PR remains unmerged; creating it does not authorize a cut or close the
window. Its description keeps US-003/US-005 and both 86M owner gates pending.

Fresh retry evidence: all six complete affected modules pass **725 tests**,
including all **79** Compose tests. Whole-tree `--collect-only -q` and separate
per-module `--collect-only -qq tests/test_*.py` both report **4126 tests** in
**38 modules**. Compared their actual outputs against every TESTING_GUIDE
row and all four total-count sites; all agree, with **41** top-level test
Python files. Rechecked both exact-scope classification tables: **115** and
**57** matching lines, zero missing, extra or duplicate classifications.
Both image pins, envelope/model passthroughs, full-only HMAC wiring, four
secret-grep patterns, five anchor quotations, retained release entries and
supported-versions table agree. GOTCHAS' split value plus all **41** rotation
rows appear in bootstrap. An initial ad-hoc history comparison extended into
intentionally changed tag-scheme prose; bounding it at `Withdrawn tags`
confirmed the actual published entries are unchanged.

Safe Ruff fix/format passes on the two restored Python files changed nothing.
Repository Ruff lint/format, strict Pyright (**0 errors**), contract export
`--check` and whitespace checks pass. Scoped and collection logs are retained
in this retry session's `files/us004-retry-*.log`; the read-back PR evidence is
`files/us004-completion-pr.json`.

**Still unverified: the full-suite acceptance gate.** This invocation expressly
prohibits a full-suite run, so neither the 725 scoped passes nor fresh collection
is represented as `uv run pytest` success. The authorized regression/end-of-epic
gate must independently run Compose, reconcile collection and execute the full
suite on its final tree. PR CI and the owner release pre-flight are not asserted
green here. The result remains **partial / needs-work** for that deferred gate,
not for the now-resolved completion-PR description.

Only the named non-release branch was pushed, with `--no-follow-tags`; the
workflow's publish jobs exclude pull requests. No merge, credential inspection,
local image build, model acquisition, benchmark, owner gate, tag push, release
or publication was performed. Story definitions and checkboxes remain unchanged.

#### US-004 attempt 3 — portable IPv6 policy assertion (2026-09-23)

Fast-forwarded this attempt from `2cd2e1b` to preserved `b08c1d2`; the exact
pin commit and draft completion PR #30 remain intact. Read back the PR's
outstanding-window notice and its same-sitting cut or
`git revert e12182f02ae977336b44e19040305a38d3e519c9` requirement.

The previous [CI run](https://github.com/WashingBearLabs/Forage/actions/runs/35830193440)
failed only `test_the_ipv6_list_is_unchanged_at_six_entries`: CI formatted the
mapped prefix as `::ffff:0:0/96`, while the assertion required
`::ffff:0.0.0.0/96` (also the local Python 3.12.12 spelling). Compare all six
ordered `IPv6Network` values instead of their version-dependent strings.
No range, prefix length, ordering or SSRF implementation changes; no runtime
or sanitizer-revision input changes. The existing test remains one test.

Safe Ruff fixes/formatting applied only to the changed test. The seven related
modules (the previous six plus URL validation) pass **982 tests**, including
**257** URL-validation and **79** Compose tests. Fresh whole-tree collection
and separately selected explicit-module collection still report **4126** tests
in **38** modules. This is not full-suite execution. The full-suite gate will
be obtained from ordinary PR CI, whose two publish jobs exclude pull requests;
no local full-suite command or owner release gate is authorized here.

#### US-004 attempt 3 — regression gate obtained (2026-09-23)

The [ordinary PR CI run 35830893486](https://github.com/WashingBearLabs/Forage/actions/runs/35830893486)
at **`be4c94f0afccfbba00c81c3f23b1b9b39a0a5849`** is green.
Its full, unfiltered `uv run pytest -q` step reports **4120 passed,
6 xfailed, 13 warnings in 83.32 seconds**. This is actual full-suite
execution, not collection or scoped-test evidence. CI uses Python 3.12.3;
the local scoped run uses 3.12.12. All six service gates and both companion
build/smoke jobs succeeded; both publishing jobs were **skipped**.
The CI merge commit `8dd63f69bdd13dc48494cc91080a315fe5a0d993` has tree
`8273958f6a969f9aebaefa68ac229042df0377c4`, exactly the tested branch tree.
The previous deferred/full-suite-failed findings are superseded by this run.
The six expected failures and warnings are inherited; no test was skipped,
xfail-marked or suppressed to obtain green. Full-suite execution was kept
in PR CI as requested, not run locally.

Independently collected the explicit `tests/test_*.py` modules with
`uv run pytest --collect-only -qq`, and the whole tree with
`uv run pytest --collect-only -q`. Compared the first output's module counts
with a node-ID count of the second and **every one of the 38 documented
module rows**: all match, sum to **4126**, and agree with all four total sites.
The portable assertion retains the URL module's **257** tests; Compose is
**79** and the seven-module scoped run is **982**. Changed total-site prose
now distinguishes the actual full-suite result from the collected total.
The existing six xfails account for the difference, not a count discrepancy.

A direct guard probe makes `IPv6Network.__str__` raise and the revised test
still passes. Removing a range, adding a transition prefix, changing a prefix
length or reversing the list each still fails. Thus portability does not
weaken the six-entry policy pin. Runtime `url_validator.py` remains byte-identical.

Re-ran the exact-scope fan-outs against their classification tables:
**115** and **57** matching lines, each classified once, no gaps or duplicates.
Line counts in the four total-site edits are preserved, so the tables above
still identify the final files. Mechanically rechecked both pins and all
passthroughs, the four secret-grep patterns and absence of stale count prose,
all five anchor quotations, the split plus all **41** rotation rows,
historical releases, root supported-versions table and unchanged runtime /
generated / golden / revision inputs. Default and shipped revision remain
`6884dc29b3dc3d7a0a1f2c1da638f767fb301b2baac68446541f6de2638bd7ec`.
Repository Ruff lint/format, strict Pyright (zero errors), export `--check`
and whitespace checks pass. Story definitions and checkboxes are untouched.

This evidence-only follow-up does not alter the tested assertion or any
runtime source. Final-head CI read-back is recorded on
[draft completion PR #30](https://github.com/WashingBearLabs/Forage/pull/30)
and in this attempt's result/evidence artifacts. The PR remains draft,
unmerged, with its original outstanding-window and exact-revert requirement.
No local image build, credentials inspection, model acquisition, benchmark,
owner gate, tag push, release or publication was performed. The only remote
write to git is the named PR branch, using `--no-follow-tags`. Owner release
US-003, post-release US-005 and both 86M owner gates remain pending; green
bookkeeping CI is not authorization to run them.

### Owner-authorized whole-epic release gate (2026-09-23)

The owner authorized final validation/fixes, merge of existing PR #30,
publication of v1.2.0 and post-release verification, conditional on passing
the release gate. The paused orchestrator and supervisor were stopped after
backing up the execution state; the main planning checkout remains untouched.
The parent session is the sole writer. This does not authorize the separate
86M licence, vendoring or benchmark gates, which remain explicitly unrun.

The first whole-epic reviews reproduced admission warmup and equivalent-IPv6
policy bypasses, the exact provider read-bound failure, Unicode threshold
revisioning failures and documentation drift. The owner requested all fixes
and explicitly declined weakening the raw ceiling to allow chunk overshoot.
Repairs now have a full local result of **4244 passed, no xfails**, with
Ruff lint/format, strict Pyright, contract-export and whitespace checks green.
The six old xfails now pass through the real HTTPX/httpcore transport stack.
The forty-second revision and all read-only reversal controls are recorded
in `docs/bootstrap-notes.md` and the other four required sites; contract 1.3.0,
its frozen golden and generated artifacts are unchanged.

Round 2 independently closed all original findings. Quality and compliance
then identified two new transport compatibility issues: h11's smaller default
header allowance and eager malformed-body parsing ahead of status/encoding
decisions. Both are repaired with 26 new regressions (12 reproduced red before
the fix); the exact raw bound remains unchanged. Final quality, security and
eight-spec compliance revalidation all returned **ready with no findings**.
There were three review rounds and two fix cycles; the accepted Stage-5
decoder residual remains open, and neither 86M gate is claimed as run.

This is prepublication evidence, not publication evidence. Fresh CI on
PR #30's updated head, the merge-commit cut and US-005 remain
outstanding. The original same-sitting cut-or-revert requirement still holds.

<!-- Populated during execution. US-001/US-002 record their rotations and the rehearsal extraction;
US-004 records both classified sweeps; US-003 records the cut, the four-way sha256 table and the
config-grep facts; US-005 records the three smoke runs, the credential-free pull, the leak check and
the handoff table. -->

## Refinement Notes

### Research Findings

**Decision:** The validation 422 tightening is an **expedited MINOR with a compatibility window**
(the category GOVERNANCE's MINOR row names, `:103`, row 6) inside the 1.3.0 window, answering
§ "Example 6 in full" step by step (its trigger is request-side acceptance; this trim is the
response-side mirror with no schema property removed, so its steps are applied to what consumers
read: step 1 keeps `input`/`ctx`/`url` present with the fixed value `"[redacted]"` for one minor
release, step 2 states that window, step 3's drop is a second MINOR because the keys were
description-admitted and never declared), with a named cap, a total coerce-and-fail-closed handler,
and stated `msg` and `loc` invariants enforced both by test and at runtime — the `loc` allowlist
from a module-level per-route map keyed by `request.scope["route"].path`, failing closed.
**Rationale:** `contract/openapi.yaml:1271-1278` — the description shipped in the `v1.1.0` Release and
image — tells consumers "pydantic adds `input` and sometimes `ctx`/`url`", so the trim removes
documented behaviour and the round-1 "never documented, hence PATCH" claim was false (validation round
1, salty; ruling R19 corrected). No property is removed from the schema, so it is not a MAJOR; the
compatibility note names the one consumer behaviour that changes. Replacing `input`'s value with a
fixed placeholder closes the byte echo on day one while the key survives the window; the cap closes response-volume amplification (and only that); the `msg` and `loc` invariants
keep the echo closed once spec 3 adds request-side validation.
**Alternatives considered:** Leaving it (an unbounded reflector); `extra="forbid"` without a handler
(would 500 on FastAPI's own body); PATCH classification (contradicted by the shipped description);
an opt-in flag that keeps emitting `input` for a window (rejected: it keeps the reflector for exactly
the consumers who never read the note); a plain MINOR under ruling (b) (rejected in round 4: (b)
governs enum members, and the two framings side by side read as a contradiction); dropping the keys
immediately with a note-only window (rejected in round 5: a window with no surviving behaviour is
Example 6 step 4's disguised break — salty, round 4); cutting a MAJOR (rejected: no declared
property moves at either step).
**Source:** `retrieval_app.py:805-834,1410,1438,1569,1643-1647,1773`;
`contract/openapi.yaml:392-403,1272-1278,1394-1396,1504-1506,1560-1562`;
`contract/GOVERNANCE.md:143-148,150-167`; `models.py:134,206,221,273,371`;
`.venv/lib/python3.12/site-packages/fastapi/exceptions.py` (`errors()` is materialised; `__str__`
renders `input`).

**Decision:** The golden is produced by `tests/test_contract_schema.py::_SCHEMA_MODELS`; the 1.3.0
close-out adds a 1.3.0 pair beside the 1.2.0 pair spec 1 pinned.
**Rationale:** `_SCHEMA_MODELS` (`:28-40`) is the only producer of `tests/golden/`;
`scripts/export_contract.py:267-270` writes the document, the anchor and the drift twin only; spec 1
US-004 pins the 1.2.0 pair to the literal older golden so it stays green (validation rounds 1 and 2,
codebase-fit).
**Alternatives considered:** A hand-written docstring only — not mechanical; retiring the 1.2.0 pair —
unnecessary once pinned.
**Source:** `tests/test_contract_schema.py:20,28-40,74-110,180-231`; `kit_tools/EXECUTION_LOG.md:264`.

**Decision:** Docstring entries carry no publication-state clause; publication state lives in
`docs/releases.md` and GOVERNANCE § "Two semvers".
**Rationale:** The `1.2.0` entry's "held … until the `v1.1.0` image publishes it" survived the cut, and
writing "published by `v1.2.0`" three stories before the tag exists is the same defect the other way;
a tense guard over every entry (via `_slice_entry`) is only honest if the docstring makes no such
claim (validation round 1, salty and codebase-fit).
**Source:** `pipeline/contract.py:22-68`; `tests/test_ci_workflow.py:2253,2264,2288,2504`.

**Decision:** The release witnesses the cache-integrity control on the released image from a fresh
placeholder env file, SearXNG not started, cold volume budgeted.
**Rationale:** Both smokes exclude `VALKEY_URL` by design and `compose/minimal.yml` is memory-backed;
`compose/full.yml:91` hard-fails without `SEARXNG_SECRET`; the weights volume is the **fixed-name
`forage-model-cache` shared with `minimal.yml`** (`compose/full.yml`'s volumes block, `name:` set
explicitly to defeat project prefixing), so run (3) is warm after run (2) and `down -v` must never be
used; a copied `compose/.env` would carry live secrets (validation rounds 1–3, salty and security).
**Source:** `compose/full.yml:36,45,79,91` and its `volumes:` block; `.gitignore:35`; spec 4 US-004 (the
compose on-switch).

**Decision:** `docker image rm` precedes the anonymous pull.
**Rationale:** The `v1.1.0` credential-free pull returned "Image is up to date", proving only
manifest resolution (audit finding 2026-09-17-014).
**Source:** `kit_tools/specs/archive/feature-search-release.md` § US-003 Implementation Notes.

### Scope Adjustments

- Validation round 1 (2026-09-19): US-002 split into US-002 (contract record, coverage sweep, golden)
  and US-004 (fan-out by value, pins, counts, release draft); US-001 owns its anchor refresh; a third
  smoke run added.
- Validation round 2 (2026-09-19): US-003 split into US-003 (cut + publish) and US-005 (post-release
  verification + handoff + roadmap flips), as the `v1.1.0` runbook was — ruling R37; US-004 raised to
  P1; the classification corrected to MINOR (ruling R19); the tense guard reworked to "no
  publication-state clause"; both moving strings swept by value; the per-module suite rows added; the
  credential recipe and leak grep widened; run (3) spelled out.
- **Correction (round 1):** the earlier Scope Adjustment claimed `_SCHEMA_MODELS` does not exist. It
  does — `tests/test_contract_schema.py:28` — and it is the golden's only producer.
- Validation round 3 (2026-09-19): the handler made total with a runtime `loc` allowlist and no
  exception object ever logged; the fuzz made per-field with type-valid markers; the ruling
  reconciled with GOVERNANCE Example 6 and its letter derived at every site; every count word moved by
  artifact; the `/extract` inline 422 literal and the five `openapi.yaml` sites named; `assert_mirrors`
  adopted; the marker's documentation homes added; `ci.yml:539` moved to US-004's by-value sweep;
  the false "own volume" claim corrected and `down -v` forbidden; status flips by value; the
  supply-chain posture row and the login-state record added.
- **Correction (round 2):** the round-2 text claimed `compose/full.yml`'s project name materialises its
  own weights volume. It does not — the volume carries an explicit fixed `name: forage-model-cache`
  shared with `minimal.yml`.
- Validation round 5 (2026-09-19, final, not re-reviewed): the 422 trim reshaped to Example 6 step 1
  (`"[redacted]"` placeholders for one release, the drop a second MINOR); `exc.endpoint_path`
  withdrawn; the handler made coerce-and-fail-closed with three more never-raises cases; the
  `/extract` allowlist entry pinned to the route signature; the `.py` sweep count corrected to 20;
  the `openapi.yaml` description-hunk classification added to US-002; `contract/openapi.yaml`
  excluded from US-004's sweep by name; the releases draft marked `NOT YET PUBLISHED`; a
  replacement tag re-runs the fan-out; the root-level capture switched to the house `at_level` form.

### Decisions Made

- Rulings 5, 19/R19 (corrected), 20, R30 (corrected), R33 (corrected), R36, R37, R39 as stated in the
  epic wrapper and its round-1 and round-2 addenda.
- The validation cap is observable through a WARNING (`validation_422_truncated`, closed vocabulary),
  not a `/metrics` counter: there is no route-scoped section a pre-routing 422 belongs to, and a new
  top-level section is a larger wire change than the signal warrants (salty's counter suggestion
  overruled with this reason; the cap bounds the response only, and the exhaustion row stays).
- `/health.promptguard_model` is the **configured** id (spec 7's decision); the handoff's model-posture
  row says so.
- Overruled: "add a `logger.info` with the untrimmed `exc.errors()`" (second-opinion) — the handler
  logs types and a count only; the value must never reach a log (invariant 6's spirit).
- Overruled: "make the digest-vs-tag question decided at this cut" — deferred regardless (Out of
  Scope); the pinnable digest is offered in `docs/releases.md` without changing the pin policy.
- Overruled: "move the compose pin into the cut commit" (salty) — ruling 20 keeps the pin before the
  tag; the window is bounded to the same sitting and recorded as outstanding otherwise.
- Overruled: "script the verification half now" (second-opinion, rounds 2 and 3) — a follow-up at the
  next cut; the runbook's manual steps are now criteria with recorded outcomes, which is the guard this
  epic can afford.
- Overruled: "split US-004 by verification command" (story quality, round 2) — ruling R37 as corrected
  in round 3: no further splits; the four bookkeeping concerns share one pre-flight deadline and one
  verifier, and none is trimmable.
- Overruled: "generate the current-version sentences at build time" (second opinion, round 2) — out of
  this epic's scope; recorded as a candidate for the next release-runbook change beside the
  verification script.
- Accepted: a runtime `loc` allowlist in the handler beside the structural test (second opinion, round
  2); the counters-in-docstring one-liner in `tests/test_contract_metrics.py` (completionist, round 2).
- Kept the boilerplate test criterion on US-001 only, made concrete (the five named new tests).
- Overruled (round 4): "split US-001" (salty, round 3, oversized) — ruling R37 as corrected: no
  further splits; the handler, its tests, the ruling and the window block cannot land green apart,
  and the size is stated as the cost; the derived letter is now written once and copied.
- Overruled (round 4): "`extra="forbid"` on `ValidationErrorDetail`" (salty, round 3, INFO) — it
  adds `additionalProperties: false` to the published schema, which is outside the description-only
  diff US-001 is allowed; `assert_mirrors` already rejects a stray key by dict equality.
- Overruled (round 4): "the `"?"` replacement token" and "the uvicorn error-logger capture" (this
  spec's own round-3 text) — ruling R30/R19 as corrected: a foreign `loc` segment is dropped and
  counted, and the uvicorn assertion is vacuous under `ASGITransport`; the never-raises test and
  US-005's `docker logs` witness replace it.
- Overruled (round 4): "one closed allowlist as the union of every model's fields" (security, round
  3) — adopted as a **per-route** map keyed by the closed token, which is strictly narrower and is
  what the ruling's "derived from the matched route's request model" means; the fail-closed default
  is the ruling's.
- Corrected (round 4): the Addendum's "count the `docs/bootstrap-notes.md` rotation headings" — the
  file has 11 `### The <ordinal> rotation:` headings for 14 rotations (the first three share the
  split section), so the count is the **ordinal of the last heading**, cross-checked against GOTCHAS
  rows minus the origin row; the Addendum's intent (an artifact, not a literal; digit-or-word) is kept.
- Overruled (round 4): "write the 1.3.0 clause once, in US-002" (salty, round 3) — the R36 uniform
  window block; reasoning in Technical Considerations § Rotations.
- Overruled (round 4): "decide the tag-vs-digest question at this cut" (security, round 3, via the
  in-image leg) — the leg now reads by digest, which is a measurement choice; the pin policy stays
  deferred (Out of Scope).
- Corrected (round 5): ruling R19 — the trim follows Example 6 step 1: `input`/`ctx`/`url` stay
  present with the fixed value `"[redacted]"` for one minor release and are dropped at the next
  MINOR as a second MINOR under the description-admitted carve-out (salty, round 4: the round-4
  "drop immediately, note-only window" was step 4's disguised break); `exc.endpoint_path` withdrawn
  as an alternative — it is `"POST /search"`, not `/search` (codebase-fit, round 4).
- Corrected (round 5): ruling R30 — the `.py` `1.2.0` sweep is 20 hits, the two request-model
  docstrings included.
- Accepted (round 5): the `/extract` allowlist entry stays a literal but is pinned to
  `inspect.signature(extract)` by a test (salty, round 4) — deriving it at import from the signature
  was the alternative; the literal reads better and the test closes the drift.

## Clarifications

### Session 2026-09-19
- Q: Does the epic end in a release? → A: Yes — all eight specs, ending in a `v1.2.0` cut (owner
  decision 1).
- Q: Is dropping `input`/`ctx` from the validation 422 a MAJOR? → A: No — no schema property is
  removed; it is a MINOR with a compatibility note because the shipped description documented the
  extra keys (ruling 19, corrected in round 2).

### Session 2026-09-19 (validation round 1)
- Rulings applied: R19 (US-001 owns the anchor refresh; `msg` invariant recorded), R30 (coverage sweep,
  `_SCHEMA_MODELS` corrected, one window list, third smoke run on `full.yml`, whole-tree leak grep),
  R31 (US-002/US-004 split), R33.
- Q: How is `/extract` exercised for the 422? → A: A well-formed multipart with an invalid
  `extract_mode`; a wrong-typed body is the documented 400 guard.
- Q: What does the by-value sweep do with `v1.2.0` image-tag hits? → A: Classifies them as expected;
  only unclassified hits fail.

### Session 2026-09-19 (validation round 3)
- Rulings applied: R19 / R30 / R33 (corrected — total handler, no exception object in any log, the
  uvicorn-logger capture, the closed route token, per-field fuzz liveness with type-valid markers, the
  runtime `loc` allowlist, GOVERNANCE Example 6 answered by name with a stated window, the derived
  letter at every site, count words by artifact, the fixed-name shared volume and no `down -v`, status
  flips by value, the supply-chain posture row, the login state recorded), the Addendum (`ci.yml:539`
  to US-004's sweep), R37 (corrected — no further splits), R41, R42.
- Q: Is the 422 trim governed by GOVERNANCE Example 6? → A: Its steps govern request-side acceptance;
  the trim is response-side with no property removed, so it is a MINOR under ruling (b) — but it ships
  with a stated compatibility window and the ruling says why a MAJOR was not chosen.
- Q: Is run (3) cold? → A: No — `full.yml` shares the fixed-name `forage-model-cache` volume; warm after
  run (2), cold only on a clean host; never `down -v`.

### Session 2026-09-19 (validation round 4)
- Rulings applied: R30/R19 (corrected — the marker fuzz asserts over `response.text`, so every
  `msg`, `loc` and `type`; the runtime `loc` allowlist is `_ROUTE_LOC_ALLOWLIST`, a module-level
  per-route map from the request models' `model_fields` and the `/extract` parameter names, looked
  up through `request.scope["route"]`, dropping any foreign segment and every non-framework segment
  when no route matches — fail closed, counted by `validation_422_loc_dropped`), the Addendum (the
  tense guard anchored on the `* ``X.Y.Z`` ` bullet and inspecting the whole sliced bullet, proved
  red on the real stale `1.2.0` entry before the fix; the rotation count from the last
  bootstrap-notes rotation heading's ordinal cross-checked against GOTCHAS rows minus the origin
  row, matched digit-or-word, `_NUMBER_WORDS` extended through twenty; by-value sweeps over an
  explicit human-written path set with the machine-written `kit_tools/` trees named as excluded;
  US-003's config-grep anchor by value, never `sed -n '998p'`), R43 (every grep names its path set —
  `--exclude-dir=kit_tools/specs` never excluded anything — and was executed on 2026-09-19 with its
  hit count recorded: 9 for `five rulings`, 6 for `fourteen`, 6 for `three patterns`, 19 for the
  `.py` `1.2.0` sweep — corrected to 20 in round 5), R41, R37 (no splits). Round-3 findings applied: the expedited-MINOR framing as
  one framing; the `HTTPValidationError.detail` description in the permitted diff (five
  descriptions, six sites); the `.py` version sweep in US-002; the `:63` "no vendored" clause; the
  structural `loc` test in `tests/test_models.py`; the citation repoints (`:802-808`, `:580`);
  `kit_tools/arch/SECURITY.md:233` in the `three patterns` sweep; the GOVERNANCE:174 scope clause;
  the letter written once; the in-image sha256 leg by digest; the `docs/releases.md` upgrade action's
  pointer; the placeholder-keyed Valkey volume removed at teardown; the `docker logs` marker witness.
- Q: Is the 422 trim "MINOR under ruling (b)" or an expedited MINOR? → A: An expedited MINOR with a
  compatibility window — the MINOR row's own category, answering Example 6 by name; (b) governs enum
  members and is not the basis.
- Q: What happens to a `loc` segment the allowlist does not know? → A: Dropped and counted; with no
  matched route every non-framework string segment is dropped. Never `"?"`, never passed through.
- Q: Why did the round-3 tense-guard patterns never match? → A: `[^.]*` stops at the period inside
  `contract_1_2_0.json`; the guard now inspects the whole bullet and was proved red on the real entry.
- Q: What is the rotation count's artifact? → A: The ordinal of the last bootstrap-notes rotation
  heading (14 today), cross-checked against GOTCHAS rows minus the origin row — not the heading
  count, which is 11.

### Session 2026-09-19 (validation round 5, final)
- Rulings applied: R19 (corrected — GOVERNANCE Example 6 step 1: for one minor release
  `input`/`ctx`/`url` stay present with the fixed value `"[redacted]"`, the docstring line states
  the window and the release that drops them as a second MINOR, GOVERNANCE ruling records the
  two-step; `_ROUTE_LOC_ALLOWLIST` keyed by `request.scope["route"].path` only, the
  `exc.endpoint_path` alternative deleted), R30 (corrected — the `.py` `1.2.0` sweep count is 20).
  Round-4 warnings applied: coerce-and-fail-closed handler with three more never-raises cases; the
  `/extract` entry pinned to the route signature; the `openapi.yaml` description-hunk classification
  in US-002; a replacement tag re-runs US-004's sweeps; `contract/openapi.yaml` excluded from the
  sweep by name; the `NOT YET PUBLISHED` marker and the third placeholder; the house
  `caplog.at_level` form with the canary; the spec 1 citation by hint title; MONITORING.md dropped
  from the `1.1.0` site list. This round was not re-reviewed; the unapplied warnings are in § Known
  risks.
- Q: Why keep `input`/`ctx`/`url` at all if their values are fixed? → A: Example 6 step 1 — the
  shape a consumer reads survives one minor release while the reflector closes immediately; a
  note-only window contains no surviving behaviour and is step 4's disguised break.
- Q: Why is the drop a MINOR and not the MAJOR step 3 names? → A: The three keys were admitted by a
  description and never declared; removing them moves no declared property, and the ruling records
  that carve-out explicitly.

### Session 2026-09-19 (validation round 2)
- Rulings applied: R19 (corrected — MINOR; the `msg` fuzz provokes validators and includes
  `value_error`; the `loc` invariant and structural test; the three other 422 descriptions move), R30
  (corrected — both strings swept by value, per-module rows, root `SECURITY.md` untouched except `:36`,
  `_slice_entry` reuse, the 1.2.0 pair stays, the tagged-tree pattern check, `contract_smoke.py`
  membership as fact, the pinnable digest), R33 (corrected — fresh placeholder env file, widened leak
  grep, four zeros, no `docker compose config`), R36 (uniform window block), R37 (US-003/US-005 split;
  execution order), R39 (grep-defined fan-out).
- Q: Where does publication state live? → A: `docs/releases.md` and GOVERNANCE § "Two semvers"; the
  docstring entries carry none, and a test says so.
- Q: Which letter does the new GOVERNANCE ruling take? → A: The next unused one at execution time;
  the count sites move by value.

## Open Questions

- [ ] Whether the compose examples should pin by digest rather than tag from the *next* cut on (audit
      -010) — advisory at this cut; the `1.2.0` tag pin lands regardless and the digest is offered as
      the pinnable form. Non-blocking.
- [ ] Whether `detail[].msg` should be normalised to a closed vocabulary in a later PATCH (security
      warning) — the invariant tests hold the line meanwhile. Non-blocking.

## Known risks (validation close-out)

Round 5 was a final fix pass without a review round. Warnings and notes from round 4 that were not
applied, or were applied in a form the reviewer did not see, are recorded here so the implementer
reads them as open rather than closed.

- **`ctx` changes type for the window (round 5, self-reported).** Pydantic emitted `ctx` as a
  mapping; the placeholder is the string `"[redacted]"`, so a consumer that indexes `ctx` breaks for
  one release. Accepted under ruling R19 (corrected): no consumer is known to read it (Assumptions),
  the key's *presence* is what step 1 preserves, and the alternative — a real `ctx` — is the
  reflector. The Edge Case names it.
- **The second-MINOR drop rests on a recorded carve-out (round 5, self-reported).** Example 6 step 3
  says the removal is the MAJOR; the ruling argues the three keys were never declared surface, so
  their removal is outside the classification table's MAJOR row. A stricter reading at the next cut
  would call the drop a MAJOR — the ruling records the reasoning so that decision is re-made with it
  on the record rather than by default.
- **`_strip_window_keys` and the constants must be deleted with the keys** at the next MINOR — a
  manual follow-up with no guard; `docs/releases.md`'s window sentence is the reminder.
- **salty (round 4, info) — US-001 remains oversized** (handler, its tests, the ruling, the
  docstring clause, the regenerate, the count sweeps) under one suite gate with no diagnosable
  midpoint. Ruling R37 (corrected) forbids the split; the derived letter written once and the named
  new tests are the only midpoints.
- **salty (round 4) — the releases draft still sits under § "Released versions"** with a marker
  line rather than in a separate staging section. Applied in the lighter form: the section header's
  promise is bent, not kept, until US-005 fills the block; a `## Pending` section was not added
  because the `v1.1.0` runbook and `tests/test_governance_docs.py` read the section by name.
- **codebase-fit (round 4, info) — line anchors drift** (`tests/test_contract_schema.py:229-231`,
  `tests/test_ci_workflow.py:2264`, others); the preamble disclaims offsets, and the implementer
  anchors by quoted text or symbol. Not re-verified in round 5.
- **codebase-fit (round 4, info) — `assert_mirrors` adoption is confirmed feasible** by the
  reviewer's probe; positive, recorded because the criterion's feasibility was not obvious from
  reading. The round-5 stripped-body form was not probed.
