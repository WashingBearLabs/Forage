<!-- Template Version: 2.5.0 -->
---
feature: hardening-release
status: active
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
updated: 2026-09-19
---

# Feature Spec: Validation-422 Tightening + Contract 1.3.0 Close-Out + v1.2.0 Release Cut

> **Spec 8 (final) of `epic-forage-hardening`.** Tighten the one wire shape this epic still owes
> (the request-validation 422 body loses its `input`/`ctx`/`url` echoes — ruling 19 as corrected in
> round 2: a **MINOR** announced with a compatibility note, because the shipped contract documented
> those keys; the story owns its own anchor refresh), **close the 1.3.0 contract window** that spec 1
> US-004 opened (rulings 5, R30, R36: the docstring record, the mechanical coverage sweep, the frozen
> golden), do the **pre-release bookkeeping** (US-004: version and release fan-out swept **by value**
> for both moving strings, compose pins, suite counts, release-notes draft), then **cut and publish
> `v1.2.0`** (US-003, the owner gate — ruling 20) and **verify it from the tagged checkout and write the
> handoff record** (US-005, owner-executed — ruling R37), with a third smoke against `compose/full.yml`
> so the cache-integrity posture Poppy pins is witnessed on the released image (R30). Binding: rulings
> 5, 6, 19/R19, 20, R30, R33, R36, R37, R39; `contract/GOVERNANCE.md`'s bump procedure (`:297-330`);
> the `v1.1.0` runbook as executed (`kit_tools/specs/archive/feature-search-release.md` US-002/US-003
> Implementation Notes). Validation rounds 1 and 2 (2026-09-19) applied — see Clarifications.
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
  documented `{loc, msg, type}` trio and nothing else, capped at `_MAX_VALIDATION_ERRORS` entries.
  Today there is no such handler (`retrieval_app.py:1410` registers only `PipelineError`), so FastAPI's
  stock body reaches the wire with pydantic's `input` (and sometimes `ctx`/`url`) — an unbounded
  reflector (`kit_tools/arch/SECURITY.md:174`). The shipped `ValidationErrorDetail` description
  (`contract/openapi.yaml:1271-1278`, in the `v1.1.0` Release and image) **says so explicitly** —
  "pydantic adds `input` and sometimes `ctx`/`url`" — so removing them is a documented-behaviour change:
  a **MINOR** inside the open window with a compatibility note, recorded as GOVERNANCE ruling (e). Its
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
  exactly the keys `loc`, `msg`, `type`, at most `_MAX_VALIDATION_ERRORS` (100) items, whose `msg` is
  pydantic's stock text for the error type and whose `loc` carries no caller-chosen segment — proven
  by tests, including a marker fuzz that **provokes a validator failure** on every request-model field
  and asserts the marker appears 0 times in the body, and a guard-of-the-guard that turns the fuzz red
  against an interpolating validator.
- `CONTRACT_VERSION == "1.3.0"`; the docstring entry is one well-formed bullet naming exactly the
  window's contents (the list in Technical Considerations § "The 1.3.0 window", reconciled against
  what specs 1–7 actually appended); `uv run python -m scripts.export_contract --check` is clean;
  `tests/test_contract_schema.py`'s 1.2.0 → 1.3.0 coverage sweep equals the golden-visible subset of
  that list; no docstring entry carries a publication-state clause.
- Every current-value statement of the contract version reads `1.3.0` and every current-release
  statement reads `v1.2.0` / `forage:1.2.0` (both by-value greps classified with zero unclassified hits;
  history rows, rotation records, contract-provenance phrases and archived specs are expected hits).
- A published multi-arch `v1.2.0` image advertising contract 1.3.0 through the gated lane, `latest`,
  `1.2` and `1.2.0` resolving to one index digest, verified from the `v1.2.0` checkout in both smoke
  modes and brought up from `compose/full.yml` at `1.2.0` keyed and key-less.
- The handoff record (tag, index digest, contract, anchor, tagged commit, run URL, cache-integrity
  posture witnessed, model posture, envelope defaults) in this file's Implementation Notes and
  `docs/releases.md` — or, for the two owner-executed stories, the sanctioned `gate not run` record
  naming the prerequisites.

## User Stories

### US-001: Validation-422 body carries `loc`, `msg`, `type` only (ruling 19 / R19)

**Priority:** P1

**Description:** As an operator exposing port 8020 on a private network, I want a malformed request to
be refused without the service echoing the caller's bytes back, so the request-validation 422 cannot
be used as a byte reflector — with the change classified honestly against what the shipped contract
documented.

**Independent Test:** `POST /search` with `{"query": 5, "providers": "x"}` returns 422; every entry in
`detail` has exactly the keys `loc`, `msg`, `type`; the literals `5` and `"x"` and a distinctive marker
appear 0 times in the body; the regenerated `contract/openapi.yaml` differs only in **description text
on the validation-422 surface** — `ValidationErrorDetail` (`retrieval_app.py:806-811`),
`HTTPValidationError` (`:821-830`) and `_PIPELINE_422_DESCRIPTION` (`:1438`; published at
`contract/openapi.yaml:392-415` and `:1395`, `:1505`, `:1561`) — with no property added, removed or
retyped anywhere; the anchor moves and the four anchor-quoting pages are refreshed; `uv run pytest` is
green in this story's own scope.

**Implementation Hints:**
- **Register the handler** beside `@app.exception_handler(PipelineError)` (`retrieval_app.py:1410`):
  `@app.exception_handler(RequestValidationError)` returning `JSONResponse(status_code=422,
  content={"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in
  exc.errors()[:_MAX_VALIDATION_ERRORS]]})`, with `_MAX_VALIDATION_ERRORS = 100` declared beside the
  other `_MAX_*` constants (`retrieval_app.py:836-839`; the `_MAX_POLICY_ENTRIES` precedent at
  `pipeline/search_providers/policy.py:17`) and quoted **by name** in the field description, ruling
  (e) and SECURITY.md. `loc` items are `str | int` — kept as emitted (`ValidationErrorDetail.loc:
  list[str | int]`, `:813`). **The cap bounds the response body only** — `DocumentSizeLimitMiddleware`
  gates on `/extract` (`:1041`), so a 50,000-item body on `/search` is still fully parsed before the
  slice; that cost falls under the existing "resource exhaustion by an admitted caller" row, which the
  closure prose cross-references as unchanged (security, round 1). When the cap bites, the handler
  logs one WARNING `validation_422_truncated — count=<n> route=<path>` (closed vocabulary, no values —
  WARNING renders without logging configuration, INFO does not; GOTCHAS "Nothing configures logging")
  so the truncation is loud without a wire change (Decisions Made); the handler never logs a record
  that carries an offending value (a `caplog` test asserts no record contains the marker).
- **The `msg` invariant, tested by provoking validators** (salty, round 1). Pydantic renders a custom
  validator's `ValueError` as `msg = "Value error, <message>"`, so the invariant is: no request-model
  validator (`RetrieveRequest`, `SearchRequest`, the `/extract` `Form` params) may interpolate the
  caller's value into its message, and `detail[].msg` is pydantic's stock text for the error type.
  Today `models.py`'s only `@field_validator`s (`:134`, `:206`, `:371`) are on response models, but
  spec 3 adds `blocked_domains` and a leading-dot form to `SearchRequest` — exactly the shape somebody
  implements as `ValueError(f"invalid entry: {entry}")`. The fuzz therefore posts the marker **one
  field at a time** as an **invalid** value for that field (so each reachable validator is exercised
  rather than masked by a co-occurring error), asserts `value_error` is among the observed `type`
  values wherever a request-model validator exists at execution time, and asserts the marker is absent
  from every `msg`. **Guard of the guard:** a temporary in-test model (or a monkeypatched validator)
  that raises with the value interpolated must turn the fuzz red — a guard that has never failed is not
  a guard. The bracketed error-type list from round 1 (`missing`, `string_type`, `int_parsing`,
  `literal_error`, `json_invalid`, `less_than_equal`, `greater_than`) is **illustrative**: derive the
  set from the live models at execution time (spec 3's `ge=0.0` yields `greater_than_equal`, for one).
- **The `loc` invariant** (security, round 1): `loc` is caller-controlled the moment a request model
  sets `model_config = ConfigDict(extra="forbid")` (`extra_forbidden` puts the attacker-chosen field
  name into `loc`) or carries a mapping-typed field. Neither exists today (`models.py:221`, `:273`
  declare no `model_config` and no dict-typed field), but `extra="forbid"` is house style elsewhere
  (`retrieval_app.py:798`, `:568`). Pin it structurally in the same module: one assertion over
  `RetrieveRequest` and `SearchRequest` that `model_config` sets no `extra="forbid"` and no field
  annotation is a mapping type; state the invariant beside the `msg` one in ruling (e) and SECURITY.md.
- **Both middlewares stay first.** `DocumentSizeLimitMiddleware` and `ExtractionAdmissionMiddleware`
  refuse before routing; the handler only sees requests that reached a route. The `/extract` case is
  a **well-formed multipart** request with `extract_mode=bogus` (a wrong-typed body on the multipart
  route is FastAPI's documented **400** body-parse guard and never reaches the handler — Edge Cases).
  Regression witnesses: `tests/test_orchestrator.py:1734` `test_post_retrieve_error_response`, the 413
  middleware test and the 404 gated-route test.
- **Where the tests live.** `tests/test_contract_errors.py:499-521`
  `test_validation_arm_of_the_422_union_is_real` is parametrised over `('route', 'body')` and posts
  `json=body` — add the key-set assertion there, and add the `/extract` case as a **sibling test**
  posting `files=`/`data=` with `extract_mode=bogus` (the module's client fixture already sets
  `extract_route_enabled: True`, `:125-129`); its docstring (`:511-514`, "deliberately tolerates the
  extra per-error keys pydantic adds") is rewritten to the new fact. In the same module,
  `test_our_validation_mirror_matches_fastapis_own_definition` (`:716-734`) still passes but its
  premise changes: re-ground its docstring ("the handler emits exactly this trio, so the mirror pins
  our own body; the FastAPI comparison is now a compatibility check, not the source of truth"). The
  marker fuzz, the guard-of-the-guard, the `loc` structural test, the cap test and the `caplog` test
  join the same module.
- **Governance record — derived, not hard-coded** (salty, round 1). Add a new lettered section to
  `contract/GOVERNANCE.md` § "Recorded rulings" (`:172+`; **Ruling:** / **Source:** shape, Source citing
  backticked in-repo paths such as `retrieval_app.py` and `kit_tools/arch/SECURITY.md`) whose letter is
  **the next unused one after the last `### (` heading in the file at execution time** (today `(e)`
  — the rulings are `(a)`, `(a2)`, `(b)`, `(c)`, `(d)` — but specs 1 and 3 write into this section
  first; this spec assumes they add prose lines under existing rulings, not lettered sections, and a
  criterion checks `_RULING_MARKERS` has no duplicate). Add the marker to `tests/test_governance_docs.py:93`
  `_RULING_MARKERS` (and its introducing comment `:90-92`) in the same commit (it drives
  `test_the_ruling_has_a_section` `:358` and `test_the_ruling_cites_a_source_file_that_exists` `:367`).
  The ruling states: the shipped description documented the extra keys, so the trim is a MINOR with a
  compatibility note ("a consumer that read `detail[].input` sees it gone; acceptable inside the
  unpublished 1.3.0 window, announced by the docstring line"); the `msg` and `loc` invariants; the cap
  by constant name and that it bounds the response only. **The count word moves by value** (ruling
  R39): `grep -rn -E 'five (recorded )?rulings' --include='*.md' --include='*.py' . --exclude-dir=kit_tools/specs`
  finds `contract/GOVERNANCE.md:174`, `tests/test_governance_docs.py:90` (comment) and `:355` (class
  docstring), `kit_tools/testing/TESTING_GUIDE.md:145`, `CLAUDE.md:89`, `kit_tools/AGENT_README.md:53`,
  `kit_tools/SYNOPSIS.md:102`, `kit_tools/arch/CODE_ARCH.md:190` — every hit becomes the new count
  (equal to `len(_RULING_MARKERS)`); `kit_tools/arch/DECISIONS.md:778` is a dated ADR heading and is
  not edited.
- **Security docs.** `kit_tools/arch/SECURITY.md:174` argues that `SearchRequest.providers` carries no
  pydantic bound *because* the 422 echoes `detail[].input`; the rewritten paragraph must still
  establish that `providers` remains unbounded at the schema, that the bound lives in
  `apply_request_policy`, and that the echo risk that motivated the split is now **closed by ruling (e)**
  rather than merely unmentioned — scoped to the **request-validation** 422 on the three routes, with a
  cross-reference that the pipeline 422's `reason` is unchanged (ruling (d): `/retrieve` still echoes
  the resolved private IP by design). The non-vulnerabilities table (`:336-356`, two columns `| Item |
  Source |`, no status column) loses the 422-echo row at `:353`; the closure is recorded in prose the
  way `:373` records the `searxng_unavailable` one, citing ruling (e), this story, the response-only
  scope of the cap and the unchanged exhaustion row. Root `SECURITY.md` needs no change here (its "Not
  vulnerabilities here" list is pinned by `tests/test_governance_docs.py:527`). The DNS-oracle rows in
  both files survive untouched (criterion).
- **Window block (rulings 5, R34, R36), copied verbatim into the criteria:** append one `* ``1.3.0``` —
  format clause to the docstring entry (`pipeline/contract.py:22-68`): "the request-validation 422
  body now carries exactly `loc`, `msg`, `type` per entry, at most `_MAX_VALIDATION_ERRORS` entries —
  a MINOR: the shipped description documented pydantic's extra keys; consumers reading
  `detail[].input` must stop (GOVERNANCE ruling (e))"; run `uv run python -m scripts.export_contract`;
  re-create `tests/golden/contract_1_3_0.json` from `tests/test_contract_schema.py::_SCHEMA_MODELS`
  (`:28`; neither validation model is in the set, so "golden unchanged, expected" is the recorded
  outcome); append nothing to `_EXPECTED_ONE_THREE_ZERO_DIFF` (no golden-visible field moves — say
  so); refresh the four pages in `tests/test_governance_docs.py:60-66` `_ANCHOR_QUOTING_PAGES`
  (`kit_tools/docs/API_GUIDE.md`, `CI_CD.md`, `DEPLOYMENT.md`, `kit_tools/arch/SERVICE_MAP.md`) with
  `anchor=$(cut -d' ' -f1 contract/openapi.yaml.sha256)` — copied, never retyped; run `--check`.
  `contract.py` is hashed → measure and record the rotation (ruling 6) at the five sites.

**Acceptance Criteria:**
- [ ] A `RequestValidationError` handler is registered with `_MAX_VALIDATION_ERRORS = 100` as a named
      constant; a wrong-typed JSON body on `/search` and `/retrieve` and a well-formed multipart with
      `extract_mode=bogus` on `/extract` (sibling test, `files=`/`data=`) yield 422 with
      `set(item) == {"loc", "msg", "type"}` for every `detail` item.
- [ ] The marker fuzz posts the marker one field at a time as an invalid value for every field of both
      request models, asserts `value_error` appears among the observed types wherever a request-model
      validator exists, asserts the marker appears 0 times in every `msg`, and a guard-of-the-guard
      (an interpolating validator on a temporary model) turns it red; the `loc` structural test asserts
      neither request model sets `extra="forbid"` or carries a mapping-typed field; a 50,000-item list
      yields at most 100 entries and one `validation_422_truncated` WARNING carrying no value; the
      handler emits no log record containing the marker (`caplog`).
- [ ] The middleware-raised 4xx paths still bypass the handler
      (`tests/test_orchestrator.py::test_post_retrieve_error_response`, the 413 and 404 tests pass untouched);
      `test_our_validation_mirror_matches_fastapis_own_definition` passes with its docstring re-grounded.
- [ ] `contract/GOVERNANCE.md` gains the next-lettered ruling with **Ruling** and **Source** (MINOR
      classification argued from `openapi.yaml:1271-1278`, the compatibility note, both invariants, the
      cap by name and its response-only scope); `_RULING_MARKERS` includes it with no duplicate; every
      hit of `grep -rn -E 'five (recorded )?rulings'` outside `kit_tools/specs/` reads the new count;
      `tests/test_governance_docs.py` green.
- [ ] `kit_tools/arch/SECURITY.md`: `grep -c 'echoes the offending value verbatim' kit_tools/arch/SECURITY.md`
      is 0; the rewritten `providers` paragraph names `apply_request_policy` as the bound and ruling (e)
      as the closure; the closure prose cross-references the unchanged exhaustion row; the DNS-oracle
      rows in `kit_tools/arch/SECURITY.md` and root `SECURITY.md` are byte-unchanged; root `SECURITY.md`
      unchanged by this story.
- [ ] Window block done: the docstring clause appended; regenerated; the document differs only in the
      three validation-422 descriptions (`ValidationErrorDetail`, `HTTPValidationError`,
      `_PIPELINE_422_DESCRIPTION` at the six `openapi.yaml` sites); golden unchanged (expected);
      `_EXPECTED_ONE_THREE_ZERO_DIFF` unchanged (expected); the four anchor-quoting pages equal
      `contract/openapi.yaml.sha256`; `--check` clean; rotation recorded in `docs/bootstrap-notes.md`,
      `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md`'s rotation table and
      `kit_tools/arch/CODE_ARCH.md`, with every "fourteen times / fourteen rotations" count sentence
      updated by value (`kit_tools/docs/GOTCHAS.md:410,434`, `kit_tools/arch/SERVICE_MAP.md:204`,
      `kit_tools/docs/TROUBLESHOOTING.md:617`, `kit_tools/docs/DEPLOYMENT.md:121`).
- [ ] The marker fuzz, the guard-of-the-guard, the `loc` structural test, the cap test and the `caplog`
      test are new tests in `tests/test_contract_errors.py`; no existing test in that module was deleted.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

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
  above is additive except the request-validation 422 trim (ruling (e)); a consumer comparing MAJOR
  keeps working untouched". **No publication-state clause** — the round-1 draft would have written
  "published by `v1.2.0`" three stories before that tag exists, the same forward-dated lie the `1.2.0`
  entry's stale "held … until the `v1.1.0` image publishes it" (`:65-68`) is in the other direction.
  Publication state lives in `docs/releases.md` and GOVERNANCE § "Two semvers", which US-005 flips
  after the cut without touching a hashed file; the `1.2.0` entry's "held" sentence is **removed**,
  not re-dated.
- **The tense guard reuses the existing parser** (codebase-fit, round 1): add the assertion to
  `tests/test_ci_workflow.py::TestReleaseContractMapping` (`:2288`), walking every `* ``X.Y.Z`` `
  bullet with `_slice_entry` (`:2264`) — the only place in the suite that already parses those
  entries; the story's own `-k` selector collects it. It asserts no entry contains "held", "until …
  publishes" or "published by".
- **The coverage sweep**: spec 1 US-004 pinned the 1.2.0 pair to the literal `contract_1_2_0.json`
  (`feature-hardening-search-sanitization.md`, its window criterion), so that pair is **green and
  stays byte-alone** — there is no "retire" branch. Add the 1.3.0 pair beside it
  (`_ONE_THREE_ZERO_DIFFED_SCHEMAS`, `_EXPECTED_ONE_THREE_ZERO_DIFF` as accumulated by specs 1–7 under
  ruling R36, `_diff_against_1_2_0(current)` diffing `contract_1_3_0.json` against `contract_1_2_0.json`,
  `test_the_N_1_3_0_additions_are_all_golden_pinned`, `test_the_1_2_0_to_1_3_0_diff_has_no_unlisted_additions`).
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
  alone (`tests/test_governance_docs.py:443-450` pins the literal `v1.0.0` **and** the current
  `CONTRACT_VERSION` in the section) and rewrite the "currently **pending**" paragraph (`:61-67`) to
  record `v1.1.0` shipped serving `1.2.0` (2026-09-18) and `v1.2.0` as the pending image for `1.3.0`
  (US-005 flips "pending" to the date after the cut). The current-version sentence (`:29`, pinned by
  `test_it_states_the_current_contract_version` `:193`) reads `1.3.0`.
- **Rotation (ruling 6):** the docstring edit changes `contract.py`'s bytes → measure and record at the
  five sites with the count sentences updated by value (as US-001).
- **Anchor**: regenerate, then confirm that the four anchor-quoting pages equal
  `contract/openapi.yaml.sha256`; refresh them if this final regenerate moved the document.

**Acceptance Criteria:**
- [ ] The `1.3.0` docstring entry is a single well-formed bullet naming exactly the reconciled window
      list, with the additive sentence and **no** publication-state clause; `_run_entry_extractor`
      prints it verbatim (recorded in Implementation Notes) with no `1.2.0` line; `uv run pytest
      tests/test_ci_workflow.py -k 'extractor or docstring_entry or tense'` green.
- [ ] The `1.2.0` entry's "held … until" sentence is removed; a `TestReleaseContractMapping` test
      walking every entry with `_slice_entry` asserts no entry contains "held", "until … publishes" or
      "published by"; GOVERNANCE § "Two semvers" records `v1.1.0` shipped and `v1.2.0` pending, keeps
      `v1.0.0` in the table, and contains `1.3.0`.
- [ ] `tests/test_contract_schema.py` carries the 1.2.0 → 1.3.0 pair with `_EXPECTED_ONE_THREE_ZERO_DIFF`
      equal to the golden-visible subset of the window list and the two structural pins; the 1.2.0 pair
      is byte-unchanged and green.
- [ ] `tests/golden/contract_1_3_0.json` re-created by hand from `_SCHEMA_MODELS` for the last time;
      `uv run python -m scripts.export_contract --check` clean; the four anchors equal the committed
      sha256; the rotation recorded at the five sites with the count sentences updated.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-004: Pre-release bookkeeping — both fan-outs by value, pins, counts, release-notes draft

**Priority:** P1

**Description:** As the owner about to cut, I want every document that states the current contract
version, the current release or a suite count already correct, and the compose examples pinned to the
tag I am about to publish, so the pre-flight finds nothing and the quickstart is right the moment the
tag exists. This story changes no runtime behaviour, but US-003's pre-flight depends on it, so it is
**not** trimmable.

**Independent Test:** Two by-value greps over `README.md CLAUDE.md contract/ kit_tools/ docs/ compose/
contract_smoke.py` excluding `kit_tools/specs/` — `grep -rn '1\.2\.0'` and `grep -rn '1\.1\.0'` —
return only hits classified in Implementation Notes as (a) history rows and rotation records, (b)
contract-provenance phrases ("added in contract 1.1.0"), or (c) sentences naming the **image**
`v1.2.0` / `forage:1.2.0`; zero unclassified hits; `grep -c 'forage:1.2.0' compose/minimal.yml
compose/full.yml` reports 1 each and `tests/test_compose_fragments.py` passes with
`_FORAGE_RELEASE_TAG = "1.2.0"`; the four total suite counts and every touched per-module row equal
`uv run pytest --collect-only -q`.

**Implementation Hints:**
- **Two sweeps, by value, not by bold** (ruling R39; completionist, round 1). This release moves two
  strings: the contract version (`1.2.0` → `1.3.0`) and the image tag (`1.1.0` → `1.2.0`). The named
  site list is a starting point; the criterion is the grep. Contract statements become `1.3.0`;
  release statements become `v1.2.0` / `forage:1.2.0`; `docs/releases.md` released-version entries and
  archived specs are never edited (ruling R28).
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
  - Release sites (the `1.1.0` grep): `kit_tools/docs/LOCAL_DEV.md:229-230` (both pins resolve);
    `kit_tools/docs/TROUBLESHOOTING.md:686-692` (the compose-pin paragraph, "pin a full semver");
    `kit_tools/SYNOPSIS.md:30` (Maturity row) and `:36` (Published image row — its digest and
    `latest`/`1.1` claim are filled by US-005 after the cut); `kit_tools/docs/MONITORING.md:380`;
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
  a closed decision. `docs/releases.md`'s draft block says "pinned ahead of the cut; the tag lands
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
  `tests/test_contract_schema.py` `:146` — already stale at 1 vs 7 — and the compose-fragments row
  text) reconciled against `uv run pytest --collect-only -q <module>`, with the row descriptions
  naming the new 422 tests, the 1.3.0 sweep and the tense guard.
- **`docs/releases.md`**: draft the `### v1.2.0 — <date>` block under "Released versions" (`:13-20`)
  with `contract: 1.3.0`, the anchor, `index digest: (filled at the cut)`, `tagged commit: (filled at
  the cut)`, the pinned-ahead sentence, a "What shipped" list mirroring the docstring entry in prose,
  and one sentence that the recorded index digest is the **pinnable form**
  (`ghcr.io/washingbearlabs/forage@sha256:…`) for deployments that need immutability, with the tag pin
  as the quickstart default (security, round 1; the digest-vs-tag policy itself stays deferred).
- **GOTCHAS rotation table** (`kit_tools/docs/GOTCHAS.md:410-434`): the count sentence and rows are
  complete for every rotation this epic recorded (confirm against `docs/bootstrap-notes.md`).

**Acceptance Criteria:**
- [ ] Both by-value greps' hits are classified in Implementation Notes (history / rotation record /
      contract provenance / image tag), with zero unclassified hits over `README.md CLAUDE.md contract/
      kit_tools/ docs/ compose/ contract_smoke.py` minus `kit_tools/specs/`; every named contract and
      release site above is updated; `kit_tools/docs/API_GUIDE.md:449` carries a `1.3.0` line;
      `MONITORING.md` says three `capabilities` keys; `kit_tools/SYNOPSIS.md:30` reads `v1.2.0` /
      `1.3.0`; root `SECURITY.md`'s supported-versions table is unchanged and `:36` no longer says
      "Pre-1.0 (where the project is today)".
- [ ] `grep -c 'forage:1.2.0' compose/minimal.yml compose/full.yml` reports 1 each; `_FORAGE_RELEASE_TAG`
      is `"1.2.0"`; `tests/test_compose_fragments.py` green; the pinned-ahead sentence and the
      pinnable-digest sentence are in `docs/releases.md`'s draft; the consequence-and-remedy sentence
      for `FORAGE_CACHE_HMAC_KEY` is in `compose/full.yml` and `README.md`.
- [ ] The four total-count sites and every touched per-module row in TESTING_GUIDE equal
      `uv run pytest --collect-only -q` (total and per module); the compose-fragments row reads
      `forage:1.2.0`.
- [ ] `docs/releases.md` carries the `v1.2.0` block with `contract: 1.3.0`, the anchor and the two
      placeholders; DEPLOYMENT/CI_CD/INFRA_ARCH name `v1.2.0` as the current release; the GOTCHAS
      rotation table matches `docs/bootstrap-notes.md`.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

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
  **read the pattern set out of the tagged tree, not the log** (codebase-fit, round 1): `git show
  v1.2.0:.github/workflows/ci.yml | sed -n '998p'` must carry four alternatives (`HF_TOKEN`,
  `hf_[A-Za-z0-9]{20,}`, `FORAGE_BRAVE_API_KEY`, `FORAGE_CACHE_HMAC_KEY`) and the comment at `ci.yml:539`
  must say four, not three (spec 4 changed both); in the run log only the success line (`ci.yml:1002`,
  "No forbidden pattern in the published image config.") appears; "Create the GitHub Release"; both
  Release assertions. Record cold/warm (count `CACHED` lines).
- **After the run:** the four-way sha256 table (committed anchor `git show v1.2.0:contract/
  openapi.yaml.sha256`; repository copy `git show v1.2.0:contract/openapi.yaml | shasum -a 256`;
  Release asset via `gh release download v1.2.0 --pattern 'openapi.yaml*'` then `shasum -a 256 -c
  openapi.yaml.sha256` → `openapi.yaml: OK`; in-image `docker run --rm --entrypoint cat
  ghcr.io/washingbearlabs/forage:1.2.0 /app/contract/openapi.yaml | shasum -a 256`); the Release body
  check; `docker buildx imagetools inspect ghcr.io/washingbearlabs/forage:<tag>` for `latest`, `1.2`,
  `1.2.0` — read the `Digest:` line (the `--format '{{.Manifest.Digest}}'` form printed whole blocks at
  `v1.1.0`); `latest` moves off the `1.1.0` image and `1.2` is minted — a pointer landing anywhere
  else is the one outcome to stop for.
- **Recovery after a pushed tag** (completionist, round 1): a red `publish` on a pushed tag is fixed
  by a **new** tag, never a moved one (`docs/releases.md` § "When something goes wrong"); a
  published-then-bad image goes to § "Withdrawn tags" (`:64`), and the handoff record (US-005) names
  the withdrawn tag.
- **Record** in `### US-003 — v1.2.0 cut and publish, <date>` under Implementation Notes: run URL,
  tagged commit sha, index digest, the four sha256 values, the `contract:` line and the entry as
  published, three-tag digest equality, cold/warm, the rehearsal extraction, the two config-grep
  facts (pattern set from the tagged tree, the success line from the log).

**Acceptance Criteria:**
- [ ] If the gate has not run: Implementation Notes carry `### US-003 — gate not run, <date>` naming
      the missing prerequisites; nothing else changes.
- [ ] Pre-flight recorded: six checks green on the tagged commit; `export_contract --check` clean;
      `CONTRACT_VERSION` is `1.3.0`; US-004's two sweeps re-run and classified; the four anchors and
      the suite counts verified; the `awk` extraction recorded verbatim (first line `* ``1.3.0`` `, no
      `1.2.0` line); the cut time is outside 23:55–00:05 UTC.
- [ ] `v1.2.0` is cut by the owner and no `searxng-v*` tag is pushed; `publish` is green; the
      multi-arch `1.2.0` image is on GHCR; run URL, tagged commit sha and OCI index digest recorded.
- [ ] Four-way sha256 equality at `v1.2.0` verified and the four values recorded.
- [ ] `gh release view v1.2.0 --json body --jq '.body' | tr -d '\r'` matches `^contract: 1\.3\.0$` and
      contains every line of the rehearsal extraction (`grep -F` per line); both Release assertions
      green; assets `openapi.yaml` and `openapi.yaml.sha256` present.
- [ ] `docker buildx imagetools inspect` prints one identical index digest for `latest`, `1.2` and
      `1.2.0`; recorded.
- [ ] `secret-grep` and the publish config grep are green; the tagged `ci.yml:998` carries four
      alternatives and `:539` says four; both success lines recorded.

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
  The compose project (`name: forage-full`, `compose/full.yml:36`) materialises **its own** volume
  `forage-full_forage-model-cache`, so this is a **cold** weights download, not warm — allow the same
  540 s budget as run (2), polling `/health` with `contract_smoke.wait_for_health` semantics. Key-less
  → `status: degraded`, `cache_unauthenticated` in `degraded_reasons`; then `down` and `up` with the
  keyed `$g` → `status: healthy`, `capabilities.cache_hmac_key: 1`, one `/retrieve` round-trip against
  `https://example.com/` (fallback `https://www.iana.org/`) served from the cache on the second call
  (`cache_hit: true`) — a fetch failure on both targets is a verification-environment problem, not a
  release failure: retry against the alternate before stopping. Both transcripts recorded with the key
  and secret values absent; `docker compose down -v` afterwards.
- **Third-party view** (the `v1.1.0` US-003 procedure): `docker logout ghcr.io`, then **`docker image
  rm <ref>` before the pull** so the anonymous pull proves more than manifest resolution (audit -014),
  `docker pull <ref>`, `docker login ghcr.io` restored afterwards via `gh auth token | docker login
  ghcr.io -u <user> --password-stdin` (output never recorded); key-less `/health` from
  `compose/minimal.yml` at `1.2.0` showing `contract_version: "1.3.0"`, `promptguard_model`, no
  `brave_api_key`, no `cache_unauthenticated` (memory backend); one `/search` round-trip; the
  placeholder-key packaging run at zero spend with the leak check over `/health`, `/metrics`, `docker
  logs` for **both** placeholders (the Brave placeholder and the HMAC placeholder) — four zeros.
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
  86M is allowlisted and vendored, and the benchmark figures' location), and **Envelope defaults**
  (`FORAGE_CPUS` unset = no limit, `FORAGE_MEM_LIMIT` `1024m`). Then fill the two `docs/releases.md`
  placeholders and `kit_tools/SYNOPSIS.md:36`'s digest and tag claim; note any withdrawn tag.
- **Status flip — every current-state site** (completionist, round 1): `kit_tools/roadmap/MILESTONES.md`'s
  hardening row **and** `:7` ("Current target") **and** `:86`; `kit_tools/roadmap/BACKLOG.md:24,:32,:49`;
  `kit_tools/PRODUCT_VISION.md` § T2.2 → Shipped with the `v1.2.0` date and a link to the handoff
  record; GOVERNANCE § "Two semvers" "pending" → the date.

**Acceptance Criteria:**
- [ ] If US-003 has not run: Implementation Notes carry `### US-005 — gate not run, <date>`; nothing
      else changes.
- [ ] Smoke runs (1) and (2) from the `v1.2.0` checkout exit 0 with `--anchor contract/openapi.yaml.sha256`
      of that checkout; run (3) is recorded as its own command block with `--env-file "$g"` written
      from placeholders, `--no-deps valkey forage`, the cold-volume wait, `cache_unauthenticated`
      key-less and `healthy` + `capabilities.cache_hmac_key: 1` + a cache hit keyed; commands and exit
      codes recorded; every credential reached a container only through `--env-file`; no
      `docker compose config` / `docker inspect` / `docker ps --no-trunc` output in the record.
- [ ] Credential-free pull after `docker image rm` recorded (both exit 0); Docker login restored;
      key-less `/health` shows `contract_version: "1.3.0"` and `promptguard_model`; the placeholder
      leak check is four zeros.
- [ ] The whole-tree leak grep (with the `SEARXNG_SECRET=` and `VALKEY_URL` credential patterns) over
      the record's diff returns nothing; both token files were mode 0600 and are recorded as deleted.
- [ ] The handoff table with the three new posture rows is in the Implementation Notes;
      `docs/releases.md`'s `v1.2.0` block and `kit_tools/SYNOPSIS.md:36` have their digest and tagged
      commit filled; MILESTONES (row, `:7`, `:86`), BACKLOG (`:24,:32,:49`), PRODUCT_VISION (T2.2) and
      GOVERNANCE § "Two semvers" read Shipped / the date.

## Edge Cases

- A route-level 4xx raised by a middleware (413 size, 404 gated `/extract`) — never reaches the
  validation handler; unchanged (US-001).
- A wrong-typed body on the multipart `/extract` route — FastAPI's documented 400 body-parse guard,
  not the handler; the handler case is a well-formed multipart with an invalid `extract_mode` (US-001).
- A validation error whose `loc` contains an integer index (`providers[3]`) — kept as `int` (US-001).
- A body producing thousands of validation errors — `detail` capped at `_MAX_VALIDATION_ERRORS`, one
  WARNING, the request still fully parsed (the exhaustion row, unchanged) (US-001).
- A future request model with `extra="forbid"` — the `loc` structural test goes red (US-001).
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
  withdrawn per § "Withdrawn tags" and named in the handoff (US-003/US-005).
- A healthy smoke against a container whose env file carried `VALKEY_URL` without the HMAC key —
  `cache_unauthenticated` pins `degraded`; a red under the wrong flag is a misconfigured verification,
  not a release failure (US-005).
- `compose/full.yml` refuses to start — `SEARXNG_SECRET` missing from `$g`; the placeholder line is
  required even with `--no-deps` (US-005).
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
  `detail[].input` (none known) is the compatibility note's audience.
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
  request-validation 422 trio with the `_MAX_VALIDATION_ERRORS` cap and the three validation-422
  descriptions (spec 8 US-001). Golden-visible items are the request/response model fields and enum
  members; counters and `CacheMetrics` fields are pinned by `tests/test_contract_metrics.py`.
- **Rotations (ruling 6):** US-001 and US-002 each edit `pipeline/contract.py` — two measured,
  recorded rotations at the five sites with the count sentences updated by value. US-004, US-003 and
  US-005 rotate nothing (no hashed file; the tense flip lives in unhashed docs).
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

<!-- Populated during execution. US-001/US-002 record their rotations and the rehearsal extraction;
US-004 records both classified sweeps; US-003 records the cut, the four-way sha256 table and the
config-grep facts; US-005 records the three smoke runs, the credential-free pull, the leak check and
the handoff table. -->

## Refinement Notes

### Research Findings

**Decision:** The validation 422 tightening is a **MINOR** announced with a compatibility note inside
the 1.3.0 window, with a named cap and stated `msg` and `loc` invariants.
**Rationale:** `contract/openapi.yaml:1271-1278` — the description shipped in the `v1.1.0` Release and
image — tells consumers "pydantic adds `input` and sometimes `ctx`/`url`", so the trim removes
documented behaviour and the round-1 "never documented, hence PATCH" claim was false (validation round
1, salty; ruling R19 corrected). No property is removed from the schema, so it is not a MAJOR; the
compatibility note names the one consumer behaviour that changes. Dropping `input` closes the byte
echo; the cap closes response-volume amplification (and only that); the `msg` and `loc` invariants
keep the echo closed once spec 3 adds request-side validation.
**Alternatives considered:** Leaving it (an unbounded reflector); `extra="forbid"` without a handler
(would 500 on FastAPI's own body); PATCH classification (contradicted by the shipped description).
**Source:** `retrieval_app.py:805-834,1410,1438`; `contract/openapi.yaml:392-415,1271-1301,1395,1505,1561`;
`contract/GOVERNANCE.md:143-148`; `models.py:134,206,221,273,371`.

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
`compose/full.yml:91` hard-fails without `SEARXNG_SECRET`, `name: forage-full` (`:36`) materialises its
own volume, and a copied `compose/.env` would carry live secrets (validation round 1, salty and security).
**Source:** `compose/full.yml:36,45,79,91`; `.gitignore:35`; spec 4 US-004 (the compose on-switch).

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
- Overruled: "script the verification half now" (second-opinion) — a follow-up at the next cut.
- Kept the boilerplate test criterion on US-001 only, made concrete (the five named new tests).

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
