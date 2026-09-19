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
execution_order: [US-001, US-002, US-004, US-003]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Validation-422 Tightening + Contract 1.3.0 Close-Out + v1.2.0 Release Cut

> **Spec 8 (final) of `epic-forage-hardening`.** Tighten the one wire shape this epic still owes
> (the request-validation 422 body loses its `input`/`ctx`/`url` echoes — ruling 19, revised R19: the
> story owns its own anchor refresh), **close the 1.3.0 contract window** that spec 1 US-004 opened
> (ruling 5 / R30: the docstring record, the mechanical coverage sweep in
> `tests/test_contract_schema.py`, the frozen golden, the tense flip), do the **pre-release
> bookkeeping** (US-004: version and anchor fan-out by value, compose pins, suite counts, release-notes
> draft — ruling R31), then **cut and publish `v1.2.0`** — **US-003 is the owner gate** (ruling 20) —
> with a third smoke against `compose/full.yml` so the cache-integrity posture Poppy pins is witnessed
> on the released image (R30). Binding: rulings 5, 6, 19/R19, 20, R30, R31, R33; `contract/GOVERNANCE.md`'s
> bump procedure (`:296-325`); the `v1.1.0` runbook as executed (`kit_tools/specs/archive/
> feature-search-release.md` US-002/US-003 Implementation Notes). Validation round 1 (2026-09-19)
> applied — see Clarifications.

## Overview

Specs 1–7 changed the wire only additively, each appending a line to the held `1.3.0` docstring entry
and re-creating `tests/golden/contract_1_3_0.json` in place. Nothing is published until this spec cuts
`v1.2.0`. Four stories, in execution order `US-001 → US-002 → US-004 → US-003`:

- **US-001 (code, autonomous)** — an app-level `RequestValidationError` handler that re-emits the
  documented `{loc, msg, type}` trio and nothing else, capped at 100 entries. Today there is no such
  handler (`retrieval_app.py:1410` registers only `PipelineError`), so FastAPI's stock body reaches the
  wire with pydantic's `input` (and sometimes `ctx`/`url`) — an unbounded reflector
  (`kit_tools/arch/SECURITY.md:174`). The documented schema (`ValidationErrorDetail`,
  `retrieval_app.py:805-818`; `contract/openapi.yaml:1271-1301`) never listed those keys, so this is a
  PATCH-class tightening announced inside the open MINOR window, recorded as GOVERNANCE ruling (e).
  Its docstring edit **moves the document and the anchor**, so the story refreshes the four
  anchor-quoting pages itself.
- **US-002 (contract close-out, autonomous)** — the `1.3.0` record in its final form, the 1.3.0
  coverage sweep in `tests/test_contract_schema.py` (the previous epic's close-out mechanism, red
  from the moment spec 1 bumped unless re-based), the golden frozen, the `1.2.0` entry's and
  GOVERNANCE's stale "held / pending" tense fixed, the rotation recorded.
- **US-004 (pre-release bookkeeping, autonomous)** — version fan-out swept **by value** with a named
  site list, the four anchors confirmed, compose pins and `_FORAGE_RELEASE_TAG` at `1.2.0` (with the
  unpublished-tag window stated), suite counts, both `SECURITY.md` files, the `docs/releases.md` draft.
- **US-003 (the cut, owner gate)** — pre-flight, tag push, publish watched, four-way sha256, three
  smoke runs from the `v1.2.0` checkout (degraded, healthy, and `compose/full.yml` keyed and key-less),
  credential-free pull after `docker image rm`, whole-tree leak grep, handoff table. Execution halts
  here if the gate has not run.

The image tag moves `1.1.0 → 1.2.0` (MINOR feature) while the contract it advertises is `1.3.0` —
the two-semver rule (`contract/GOVERNANCE.md` § "Two semvers, independent"). The string `1.2.0` is
therefore **both** the retiring contract version and the new image tag; every sweep in this spec
distinguishes the two by context, never by a bare grep.

## Goals

- A request that fails schema validation on `/search`, `/retrieve` or `/extract` (route enabled;
  well-formed multipart with an invalid `extract_mode`) returns a 422 whose every `detail[]` item has
  exactly the keys `loc`, `msg`, `type`, at most 100 items, and whose `msg` is pydantic's stock text
  for the error type — proven by tests, including a marker-token fuzz that asserts the marker appears
  0 times in the body.
- `CONTRACT_VERSION == "1.3.0"`; the docstring entry is one well-formed bullet naming exactly the
  window's contents (the list in Technical Considerations § "The 1.3.0 window"); `uv run python -m
  scripts.export_contract --check` is clean; `tests/test_contract_schema.py`'s 1.2.0 → 1.3.0 coverage
  sweep equals the golden-visible subset of that list; the `1.2.0` entry reads "published by `v1.1.0`".
- Every current-value statement of the contract version in the tree reads `1.3.0` (the named site
  list in US-004, swept by value — image-tag sentences naming `v1.2.0` are expected hits, not failures).
- A published multi-arch `v1.2.0` image advertising contract 1.3.0 through the gated lane, `latest`,
  `1.2` and `1.2.0` resolving to one index digest, verified from the `v1.2.0` checkout in both smoke
  modes and brought up from `compose/full.yml` at `1.2.0` keyed and key-less.
- The handoff record (tag, index digest, contract, anchor, tagged commit, run URL, cache-integrity
  posture witnessed, model posture) in this file's Implementation Notes and `docs/releases.md`.

## User Stories

### US-001: Validation-422 body carries `loc`, `msg`, `type` only (ruling 19 / R19)

**Priority:** P1

**Description:** As an operator exposing port 8020 on a private network, I want a malformed request to
be refused without the service echoing the caller's bytes back, so the request-validation 422 cannot
be used as a byte reflector and the wire matches what the contract already documents.

**Independent Test:** `POST /search` with `{"query": 5, "providers": "x"}` returns 422; every entry in
`detail` has exactly the keys `loc`, `msg`, `type`; the literals `5` and `"x"` and a distinctive
marker posted in every string field appear 0 times in the body; the regenerated `contract/openapi.yaml`
differs only in `ValidationErrorDetail`'s `description` (no property added, removed or retyped), the
anchor moves and the four anchor-quoting pages are refreshed, and `uv run pytest` is green in this
story's own scope.

**Implementation Hints:**
- **Register the handler** beside `@app.exception_handler(PipelineError)` (`retrieval_app.py:1410`):
  `@app.exception_handler(RequestValidationError)` returning `JSONResponse(status_code=422,
  content={"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in
  exc.errors()[:100]]})`. `loc` items are `str | int` — keep them as emitted
  (`ValidationErrorDetail.loc: list[str | int]`, `:813`). The cap bounds response-volume amplification
  (a body of 50,000 wrong-typed list items would otherwise produce 50,000 entries); it is documented
  in the field description and the GOVERNANCE ruling. The handler logs **nothing** that carries the
  offending value (a `caplog` test asserts no record contains the marker); a `logger.debug` with the
  error *types* and count is fine.
- **The `msg` invariant.** Pydantic renders a custom validator's `ValueError` as `msg = "Value error,
  <message>"`, so the invariant is: no request-model validator (`RetrieveRequest`, `SearchRequest`,
  the `/extract` `Form` params) may interpolate the caller's value into its message, and `detail[].msg`
  is pydantic's stock text for the error type. Today `models.py`'s only `@field_validator`s (`:134`,
  `:206`, `:371`) are on response models. Pin it structurally: a test posts a marker token into every
  string field and list item of both request models and asserts the marker appears 0 times in the 422
  body across the reachable error types (`missing`, `string_type`, `int_parsing`, `literal_error`,
  `json_invalid`, `less_than_equal`, `greater_than`); write the invariant into the GOVERNANCE ruling
  and the SECURITY.md sentence.
- **Both middlewares stay first.** `DocumentSizeLimitMiddleware` and `ExtractionAdmissionMiddleware`
  refuse before routing; the handler only sees requests that reached a route. The `/extract` case is
  a **well-formed multipart** request with `extract_mode=bogus` (a wrong-typed body on the multipart
  route is FastAPI's documented **400** body-parse guard and never reaches the handler — Edge Cases).
  Regression witnesses: `tests/test_orchestrator.py:1734` `test_post_retrieve_error_response` (not
  `tests/test_app.py`), the 413 middleware test and the 404 gated-route test.
- **Where the tests live.** `tests/test_contract_errors.py:499-521`
  `test_validation_arm_of_the_422_union_is_real` already posts a malformed body to `/retrieve` and
  `/search` through the real client fixture — add the key-set assertion there and the `/extract`
  parametrisation; its docstring (`:511-514`, "deliberately tolerates the extra per-error keys pydantic
  adds") is rewritten to the new fact. The marker fuzz and the cap test join the same module.
- **Governance record.** Add `### (e) The request-validation 422 carries the documented trio only` to
  `contract/GOVERNANCE.md` § "Recorded rulings" (`:172+`; **Ruling:** / **Source:** shape, Source citing
  a backticked in-repo path such as `retrieval_app.py` and `kit_tools/arch/SECURITY.md`); add
  `"### (e) "` to `tests/test_governance_docs.py:92` `_RULING_MARKERS` in the same commit (it drives
  `test_the_ruling_has_a_section` `:358` and `test_the_ruling_cites_a_source_file_that_exists` `:367`);
  update the section's opening sentence "Five rulings this epic already made" (`:174`), the class
  docstring at `:355` and `kit_tools/testing/TESTING_GUIDE.md:145` ("the five recorded rulings") to six.
- **Security docs.** `kit_tools/arch/SECURITY.md:174`: rewrite the sentence to the new fact, scoped to
  the **request-validation** 422 on the three routes, with a cross-reference that the pipeline 422's
  `reason` is unchanged (ruling (d): `/retrieve` still echoes the resolved private IP by design). The
  non-vulnerabilities table (`:336-356`, two columns `| Item | Source |`, no status column) loses the
  422-echo row at `:353`; the closure is recorded in prose the way `:373` records the
  `searxng_unavailable` one, citing ruling (e) and this story. Root `SECURITY.md` needs no change (its
  "Not vulnerabilities here" list is pinned by `tests/test_governance_docs.py:527`) — say so in the
  commit. The DNS-oracle rows in both files survive untouched (criterion).
- **Window line (ruling 5, R34):** append one `* ``1.3.0``` — format clause to the docstring entry
  (`pipeline/contract.py:22-68`): "the request-validation 422 body now carries exactly `loc`, `msg`,
  `type` per entry, at most 100 entries (a tightening of an undocumented echo, GOVERNANCE ruling (e))";
  regenerate; `contract.py` is hashed → measure and record the rotation (ruling 6). The golden does
  **not** move (neither validation model is in `tests/test_contract_schema.py::_SCHEMA_MODELS` `:28`) —
  state "golden unchanged, expected".
- **Anchor refresh (R19).** `ValidationErrorDetail`'s docstring is the schema `description`
  (`contract/openapi.yaml:1272-1278`), so the regenerate moves `contract/openapi.yaml.sha256`; refresh
  the four pages in `tests/test_governance_docs.py:60-66` `_ANCHOR_QUOTING_PAGES` (`kit_tools/docs/API_GUIDE.md`,
  `CI_CD.md`, `DEPLOYMENT.md`, `kit_tools/arch/SERVICE_MAP.md`) with
  `anchor=$(cut -d' ' -f1 contract/openapi.yaml.sha256)` — copied, never retyped. Every regenerating
  story in this epic owns the same refresh in its own commit (Technical Considerations).

**Acceptance Criteria:**
- [ ] A `RequestValidationError` handler is registered; a wrong-typed JSON body on `/search` and
      `/retrieve` and a well-formed multipart with `extract_mode=bogus` on `/extract` (route enabled
      in the test) yield 422 with `set(item) == {"loc", "msg", "type"}` for every `detail` item
      (`tests/test_contract_errors.py::test_validation_arm_of_the_422_union_is_real` extended).
- [ ] The marker fuzz asserts a distinctive token posted in every string field and list item of both
      request models appears 0 times in the 422 body across the listed error types; a 50,000-item
      list yields at most 100 entries; the handler emits no log record containing the marker (`caplog`).
- [ ] The middleware-raised 4xx paths still bypass the handler
      (`tests/test_orchestrator.py::test_post_retrieve_error_response`, the 413 and 404 tests pass untouched).
- [ ] `contract/GOVERNANCE.md` gains ruling (e) with **Ruling** and **Source**; `_RULING_MARKERS`
      includes `"### (e) "`; the "Five rulings" sentence, the test class docstring and TESTING_GUIDE
      say six; `tests/test_governance_docs.py` green.
- [ ] `kit_tools/arch/SECURITY.md`: `grep -c 'echoes the offending value verbatim' kit_tools/arch/SECURITY.md`
      is 0 (both `:174` and the table row); the closure prose cites ruling (e); the DNS-oracle rows in
      `kit_tools/arch/SECURITY.md` and root `SECURITY.md` are byte-unchanged; root `SECURITY.md` unchanged.
- [ ] The `1.3.0` docstring clause is appended; `uv run python -m scripts.export_contract --check`
      clean; the four anchor-quoting pages equal `contract/openapi.yaml.sha256`; golden unchanged
      (expected); rotation recorded in `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`
      and the GOTCHAS rotation table.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-002: Close the 1.3.0 contract record — final entry, coverage sweep, frozen golden

**Priority:** P1

**Description:** As the consumer's re-vendoring session, I want one complete, mechanical statement of
what contract 1.3.0 changed — a docstring entry the Release body will carry and a test that fails if
the golden carries an addition the entry does not name — and one frozen golden, so the `v1.2.0`
Release announces the whole epic and nothing rides through silently.

**Independent Test:** `uv run pytest tests/test_ci_workflow.py -k 'extractor or docstring_entry'`
passes and `tests/test_ci_workflow.py::_run_entry_extractor` (`:2253`) applied to `pipeline/contract.py`
for `1.3.0` prints one entry beginning `* ``1.3.0`` ` that names every item in the window list
(Technical Considerations) and no line of the `1.2.0` entry; `uv run pytest tests/test_contract_schema.py`
passes with `_EXPECTED_ONE_THREE_ZERO_DIFF` equal to the golden-visible subset of that list; the
`1.2.0` entry and GOVERNANCE § "Two semvers" read "published by `v1.1.0`".

**Implementation Hints:**
- **The record** (`pipeline/contract.py:22-68`): rewrite the `1.3.0` bullet into its final form — one
  bullet at column 0 opening `* ``1.3.0`` —`, two-space continuation lines, **no blank line inside it**
  (a blank line ends the entry — GOVERNANCE step 7; the extractor is pinned by
  `tests/test_ci_workflow.py:2504-2580`), naming exactly the window list — reconcile it first against
  the lines earlier stories appended (`git log -p -- pipeline/contract.py` shows each) so nothing a
  story added is dropped — the sentence "Every addition above is additive … a consumer comparing MAJOR
  keeps working untouched", and the closing clause "published by `v1.2.0`" (no "held … until"). Then
  the **tense flip the last window missed**: the `1.2.0` entry (`:65-68`) still reads "This version is
  **held** … until the `v1.1.0` image publishes it" although `v1.1.0` shipped 2026-09-18 — rewrite to
  "published by `v1.1.0` (2026-09-18)". Add one assertion to `tests/test_contract_export.py` or
  `tests/test_governance_docs.py`: every docstring entry below `CONTRACT_VERSION` reads "published by",
  never "held"/"until … publishes".
- **The coverage sweep** (`tests/test_contract_schema.py:74-110,180-227`): re-base the 1.2.0 sweep into
  a 1.3.0 one — `_ONE_THREE_ZERO_DIFFED_SCHEMAS`, `_EXPECTED_ONE_THREE_ZERO_DIFF`,
  `_diff_against_1_2_0(current)` diffing `contract_1_3_0.json` against `contract_1_2_0.json`, and the
  two tests (`test_the_N_1_3_0_additions_are_all_golden_pinned`,
  `test_the_1_2_0_to_1_3_0_diff_has_no_unlisted_additions`); keep the 1.2.0 tests as a frozen
  historical pair only if they still pass against the two older goldens, else retire them with a
  comment. The expectation set mirrors the docstring's golden-visible items one-for-one; the `/metrics`
  counters and `CacheMetrics` fields are pinned by `tests/test_contract_metrics.py`, not the golden
  (as today's `:79-83` comment says) — the docstring names them, the set does not.
- **The golden** is written by hand from `_current_schemas()` over `_SCHEMA_MODELS`
  (`tests/test_contract_schema.py:28-40`; `indent=2, sort_keys=True`, trailing newline —
  `kit_tools/EXECUTION_LOG.md:264` records the procedure); `uv run python -m scripts.export_contract`
  writes only `contract/openapi.yaml`, its `.sha256` and the drift twin (`scripts/export_contract.py:267-270`).
  From this commit `tests/golden/contract_1_3_0.json` is frozen (older goldens never edited —
  GOVERNANCE ruling (c)).
- **GOVERNANCE § "Two semvers"** (`contract/GOVERNANCE.md:46-70`): leave the table's `v1.0.0` example
  alone (`tests/test_governance_docs.py:443-450` pins the literal `v1.0.0` **and** the current
  `CONTRACT_VERSION` in the section) and rewrite the "currently **pending**" paragraph (`:61-67`) to
  record `v1.1.0` shipped serving `1.2.0` (2026-09-18) and `v1.2.0` as the pending image for `1.3.0`.
  The current-version sentence (`:29`, pinned by `test_it_states_the_current_contract_version` `:193`)
  reads `1.3.0`.
- **Rotation (ruling 6):** the docstring edit changes `contract.py`'s bytes → measure and record
  (`docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`, the GOTCHAS rotation table).
- **Anchor**: regenerate, then confirm (not sweep — the refresh is each regenerating story's own job)
  that the four anchor-quoting pages equal `contract/openapi.yaml.sha256`; refresh them if this final
  regenerate moved the document.

**Acceptance Criteria:**
- [ ] The `1.3.0` docstring entry is a single well-formed bullet naming exactly the window list, closing
      with "published by `v1.2.0`"; `_run_entry_extractor` prints it verbatim (recorded in
      Implementation Notes) with no `1.2.0` line; `uv run pytest tests/test_ci_workflow.py -k 'extractor
      or docstring_entry'` green.
- [ ] The `1.2.0` entry reads "published by `v1.1.0` (2026-09-18)"; GOVERNANCE § "Two semvers" records
      `v1.1.0` shipped and `v1.2.0` pending, keeps `v1.0.0` in the table, and contains `1.3.0`; a new
      test asserts no entry below `CONTRACT_VERSION` reads "held" / "until … publishes".
- [ ] `tests/test_contract_schema.py` carries the 1.2.0 → 1.3.0 sweep with `_EXPECTED_ONE_THREE_ZERO_DIFF`
      equal to the golden-visible subset of the window list; both sweep tests green; the historical
      1.2.0 pair kept or retired with a comment.
- [ ] `tests/golden/contract_1_3_0.json` re-created by hand from `_SCHEMA_MODELS` for the last time;
      `uv run python -m scripts.export_contract --check` clean; the four anchors equal the committed
      sha256; the rotation recorded.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-004: Pre-release bookkeeping — version fan-out by value, pins, counts, release-notes draft

**Priority:** P2

**Description:** As the owner about to cut, I want every document that states the current contract
version, the current release or the suite count already correct, and the compose examples pinned to
the tag I am about to publish, so the pre-flight finds nothing and the quickstart is right the moment
the tag exists.

**Independent Test:** `grep -rn '1\.2\.0' -- README.md CLAUDE.md contract/GOVERNANCE.md kit_tools/arch
kit_tools/docs docs/configuration.md` returns only (a) history rows and rotation records and (b)
sentences naming the **image** `v1.2.0` / `forage:1.2.0` — each hit is classified in Implementation
Notes; `grep -c 'forage:1.2.0' compose/minimal.yml compose/full.yml` reports 1 each and
`tests/test_compose_fragments.py` passes with `_FORAGE_RELEASE_TAG = "1.2.0"`; the four suite-count
sites equal `uv run pytest`'s collection.

**Implementation Hints:**
- **Sweep by value, not by bold.** The named site list (grep each distinctive phrase rather than
  trusting the offset): `README.md:62` ("currently **1.2.0**") and `:257-258` ("the current contract is
  `1.2.0`"); `CLAUDE.md:82` (invariant 4) and the Coexistence rotation history (`:204,229` — **append**,
  never rewrite); `contract/GOVERNANCE.md:29,:61-67` (US-002 did these — confirm); `kit_tools/arch/CODE_ARCH.md`
  bold version; `kit_tools/docs/API_GUIDE.md:43,:111` (the `| contract_version | str | 1.2.0 |` row is a
  current value, not history); `kit_tools/arch/SERVICE_MAP.md:75,:189` ("Current state" release row),
  `:202-211` (anchor paragraph); `kit_tools/docs/TROUBLESHOOTING.md:61` (`| contract_version | "1.2.0" |`);
  `kit_tools/docs/MONITORING.md:63,65,67` (`contract_version` row, "two keys as of contract 1.2.0" — now
  three keys with `cache_hmac_key`, spec 4 — `search_providers` "contract 1.2.0");
  `kit_tools/docs/CI_CD.md:314,:343,:510-511,:531`; `kit_tools/docs/DEPLOYMENT.md:87-232` (the `TAG=`
  snippets and the image↔contract pairs at `:117`, `:192`; the tag-inventory sentence `:90`);
  `kit_tools/arch/INFRA_ARCH.md:146,159-160,188-195` (compose pin rows, current release). Contract
  statements become `1.3.0`; release statements become `v1.2.0` / `forage:1.2.0`. `docs/releases.md`
  released-version entries and archived specs are never edited (ruling R28).
- **Both `SECURITY.md` files** (security info finding): root `SECURITY.md` (`:29` "Supported
  versions" names the current release; its credential bullets name `FORAGE_CACHE_HMAC_KEY` and
  `FORAGE_MODEL_ID` if the file lists credentials — verify) and `kit_tools/arch/SECURITY.md`'s cache and
  request-model sections as left by specs 4 and 8 US-001.
- **Compose pins**: `compose/minimal.yml` and `compose/full.yml` `image:` lines to `forage:1.2.0` and
  `tests/test_compose_fragments.py:77` `_FORAGE_RELEASE_TAG = "1.2.0"` together
  (`test_the_forage_image_is_the_current_release` `:505`). The envelope variables (spec 6),
  `FORAGE_CACHE_HMAC_KEY` (spec 4) and `FORAGE_MODEL_ID` / `FORAGE_MODEL_REVISION` (spec 7)
  passthroughs are already in the fragments — confirm by `grep -c`. **The unpublished-tag window**: from
  this commit until US-003 pushes the tag, `main`'s quickstart pulls a tag that does not exist. That
  is accepted and stated in `docs/releases.md`'s draft block ("pinned ahead of the cut; the tag lands
  with `v1.2.0`") and in the Edge Cases; the orchestrator's PR merges before the owner cuts, and the
  owner cuts from that merge commit.
- **`compose/full.yml` and the key**: the fragment's `FORAGE_CACHE_HMAC_KEY` passthrough is an unset
  bare name (spec 4), so a stock `full.yml` at `1.2.0` comes up `degraded` / `cache_unauthenticated`
  until the operator sets it; `compose/full.yml`'s comment, `README.md`'s quickstart and
  `docs/configuration.md` say so in one sentence each ("set `FORAGE_CACHE_HMAC_KEY` before upgrading to
  1.2.0 with an external Valkey"). US-003 witnesses both states.
- **Suite-count bookkeeping**: `uv run pytest` count into `kit_tools/testing/TESTING_GUIDE.md`,
  `kit_tools/SYNOPSIS.md`, `kit_tools/AGENT_README.md` and `CLAUDE.md`'s Development parenthetical.
- **`docs/releases.md`**: draft the `### v1.2.0 — <date>` block under "Released versions" (`:13-20`)
  with `contract: 1.3.0`, the anchor, `index digest: (filled at the cut)`, `tagged commit: (filled at
  the cut)`, the pinned-ahead sentence, and a "What shipped" list mirroring the docstring entry in
  prose; US-003 fills the placeholders.
- **GOTCHAS rotation table** (`kit_tools/docs/GOTCHAS.md:410-434`): the count sentence and rows are
  complete for every rotation this epic recorded (confirm against `docs/bootstrap-notes.md`).

**Acceptance Criteria:**
- [ ] Every site in the named list is updated; the by-value grep's hits are classified in
      Implementation Notes (history / rotation record / image tag), with zero unclassified hits;
      `kit_tools/docs/API_GUIDE.md`'s `contract_version` row and `TROUBLESHOOTING.md:61` read `1.3.0`;
      `MONITORING.md` says three `capabilities` keys.
- [ ] `grep -c 'forage:1.2.0' compose/minimal.yml compose/full.yml` reports 1 each; `_FORAGE_RELEASE_TAG`
      is `"1.2.0"`; `tests/test_compose_fragments.py` green; the pinned-ahead sentence is in
      `docs/releases.md`'s draft; the `FORAGE_CACHE_HMAC_KEY` upgrade sentence is in `compose/full.yml`,
      `README.md` and `docs/configuration.md`.
- [ ] TESTING_GUIDE, SYNOPSIS, AGENT_README and CLAUDE.md state the same suite count as `uv run pytest`
      collects.
- [ ] `docs/releases.md` carries the `v1.2.0` block with `contract: 1.3.0`, the anchor and the two
      placeholders; DEPLOYMENT/CI_CD/INFRA_ARCH/root `SECURITY.md` name `v1.2.0` as the current release;
      the GOTCHAS rotation table matches `docs/bootstrap-notes.md`.
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-003: Cut + publish the v1.2.0 image and write the handoff record (owner gate)

**Priority:** P1

**Description:** As the owner, I want a `v1.2.0` image published through the gated lane, verified from
the tagged checkout in three smoke runs and from a credential-free pull, with the record Poppy pins
written here — so the hardening epic's value reaches the consumer as one digest, and the epic's own
cache-integrity control is witnessed on the released artifact. **Execution halts here for the owner**
(ruling 20): the tag push is the human gate, and every step needs a Docker daemon, network egress and
GHCR access the autonomous environment does not have. The implementer records that the gate has not
run (`### US-003 — gate not run, <date>`) and stops; the verifier expects the evidence listed under
"Record".

**Independent Test:** The `v1.2.0` tag triggers `publish` (which `needs:` the six gates) and it goes
green; `gh release view v1.2.0 --json body --jq '.body' | tr -d '\r'` matches `^contract: 1\.3\.0$` and
contains every line of the `1.3.0` docstring entry; `latest`, `1.2` and `1.2.0` resolve to one index
digest; from the `v1.2.0` checkout `contract_smoke.py --image <digest ref> --anchor
contract/openapi.yaml.sha256` exits 0 with `--expect-status degraded` against a no-env container and
with `--expect-status healthy` against a weights-loaded one started with `--env-file`; `compose/full.yml`
at `1.2.0` reports `cache_unauthenticated` key-less and `healthy` with `capabilities.cache_hmac_key: 1`
keyed.

**Implementation Hints:**
- **The runbook is the `v1.1.0` cut**, executed and recorded at
  `kit_tools/specs/archive/feature-search-release.md` § "US-002 — v1.1.0 cut and publish" and
  § "US-003 — Post-release verification"; `docs/releases.md` § "Cutting a release" and § "When
  something goes wrong" (`:342-390`) are the short form. Follow them with `v1.2.0` / `1.3.0` / `1.2`
  substituted; only the differences are listed below.
- **Credential handling** (ruling R33): `umask 077; f="$(mktemp)"; trap 'rm -f "$f"' EXIT`, the HF
  token via `read -rs` into `$f` (one line, `HF_TOKEN=…`), only ever `--env-file "$f"`; the placeholder
  HMAC key for the `full.yml` run is a literal 32-character `x` string in a throwaway `compose/.env`
  copy that is deleted after; the output of `gh auth token`, the contents of `$f`, and `docker inspect`
  of any keyed container are **never** pasted into the record.
- **Pre-flight, on `main` at the commit to be tagged:** the epic branch merged with a merge commit;
  the six required checks green on that run; `git switch main && git pull` and `git log -1` recorded;
  `uv run python -m scripts.export_contract --check` clean; `grep CONTRACT_VERSION pipeline/contract.py`
  prints `1.3.0`; US-004's by-value sweep re-run and classified; `grep -c 'forage:1.2.0'` 1 per
  fragment; the four anchors equal the committed sha256; the four suite counts equal `uv run pytest`'s
  collection on this commit; the extractor rehearsed — `_run_entry_extractor`'s awk program from
  `.github/workflows/ci.yml` — and its output recorded verbatim, first line `* ``1.3.0`` `, no `1.2.0`
  line. Cut clear of the ten minutes around 00:00 UTC (`docs/releases.md` § "Reproducible builds").
- **The cut:** `git tag v1.2.0 && git push origin v1.2.0`, then `gh run watch`. No `searxng-v*` tag —
  the companion image is unchanged this epic.
- **Watch these `publish` steps live**, in order: "Verify the published amd64 image is the gated
  filesystem"; "Read the contract version from the tagged tree"; the published-config secret grep
  (`ci.yml:998` — confirm its pattern count in the log, four after spec 4); "Create the GitHub Release";
  both Release assertions. Record cold/warm (count `CACHED` lines).
- **After the run:** the four-way sha256 table (committed anchor `git show v1.2.0:contract/
  openapi.yaml.sha256`; repository copy `git show v1.2.0:contract/openapi.yaml | shasum -a 256`;
  Release asset via `gh release download v1.2.0 --pattern 'openapi.yaml*'` then `shasum -a 256 -c
  openapi.yaml.sha256` → `openapi.yaml: OK`; in-image `docker run --rm --entrypoint cat
  ghcr.io/washingbearlabs/forage:1.2.0 /app/contract/openapi.yaml | shasum -a 256`); the Release body
  check; `docker buildx imagetools inspect ghcr.io/washingbearlabs/forage:<tag>` for `latest`, `1.2`,
  `1.2.0` — read the `Digest:` line (the `--format '{{.Manifest.Digest}}'` form printed whole blocks at
  `v1.1.0`); `latest` moves off the `1.1.0` image and `1.2` is minted — a pointer landing anywhere
  else is the one outcome to stop for.
- **Smoke, three ways, from the tagged checkout** (`git switch --detach v1.2.0 && uv sync --extra dev`),
  `<ref>` = `ghcr.io/washingbearlabs/forage@sha256:<index digest>`, `--anchor contract/openapi.yaml.sha256`
  of that checkout each time (never the downloaded copy, never the in-image file — GOVERNANCE
  "Consumers"): (1) `docker run --rm -d -p 127.0.0.1:8020:8020 --name forage-rel <ref>` with no env
  file and no volume, then `uv run python contract_smoke.py --image <ref> --expect-status degraded
  --anchor contract/openapi.yaml.sha256`; (2) the same image with `--env-file "$f"` and `-v
  forage-model-cache:/app/model-cache`, then `--expect-status healthy --timeout-seconds 540` — no
  `VALKEY_URL` in `$f` (an unreachable Valkey pins `cache_unavailable`); (3) **`compose/full.yml` at
  `1.2.0`** (R30): key-less → `/health` `status: degraded`, `cache_unauthenticated` in
  `degraded_reasons`; then with the placeholder `FORAGE_CACHE_HMAC_KEY` → `status: healthy` (weights
  warm), `capabilities.cache_hmac_key: 1`, one `/retrieve` round-trip served from the cache on the
  second call (`cache_hit: true`); both transcripts recorded with the key value absent.
- **Third-party view** (the `v1.1.0` US-003 procedure): `docker logout ghcr.io`, then **`docker image
  rm <ref>` before the pull** so the anonymous pull proves more than manifest resolution (audit -014),
  `docker pull <ref>`, `docker login ghcr.io` restored afterwards via `gh auth token | docker login
  ghcr.io -u <user> --password-stdin` (output never recorded); key-less `/health` from
  `compose/minimal.yml` at `1.2.0` showing `contract_version: "1.3.0"`, `promptguard_model`, no
  `brave_api_key`, no `cache_unauthenticated` (memory backend); one `/search` round-trip; the
  placeholder-key packaging run at zero spend with the leak check over `/health`, `/metrics`, `docker
  logs` (three zeros).
- **Whole-tree leak check** before pushing the record: `git diff <pre-record commit>..HEAD | grep -nE
  'HF_TOKEN=|hf_[A-Za-z0-9]{20,}|FORAGE_BRAVE_API_KEY=|FORAGE_CACHE_HMAC_KEY=|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_'`
  returns nothing (bare variable *names* in prose are fine; `NAME=` followed by a value is not); a hit
  found after the tag is pushed is a history rewrite, not an edit.
- **Record** in `### US-003 — v1.2.0 cut, publish and handoff, <date>` under Implementation Notes: run
  URL, tagged commit sha, index digest, the four sha256 values, the `contract:` line and the entry as
  published, three-tag digest equality, cold/warm, the rehearsal extraction, all three smoke
  transcripts with exit codes, the credential-free pull transcript, the key-less `/health`, the
  leak-check table, and the handoff table with the `v1.1.0` columns plus three new rows:
  **Cache-integrity posture** (witnessed: keyed `full.yml` healthy with `cache_hmac_key: 1`; key-less
  external Valkey reports `cache_unauthenticated`; memory mode needs no key), **Model posture**
  (`promptguard_model` on `/health` — the loaded id; 22M default; 86M opt-in with the benchmark
  figures' location and the vendoring state), and **Envelope defaults** (`FORAGE_CPUS` unset = no
  limit, `FORAGE_MEM_LIMIT` `1024m`). Then fill the two `docs/releases.md` placeholders.
- **Status flip**: `kit_tools/roadmap/MILESTONES.md`'s hardening row and `kit_tools/PRODUCT_VISION.md`
  § T2.2 move to Shipped with the `v1.2.0` date and a link to the handoff record.

**Acceptance Criteria:**
- [ ] If the gate has not run: Implementation Notes carry `### US-003 — gate not run, <date>` naming
      the missing prerequisites; nothing else changes.
- [ ] Pre-flight recorded: six checks green on the tagged commit; `export_contract --check` clean;
      `CONTRACT_VERSION` is `1.3.0`; US-004's sweep re-run and classified; the four anchors and the
      four suite counts verified; the `awk` extraction recorded verbatim (first line `* ``1.3.0`` `, no
      `1.2.0` line); the cut time is outside 23:55–00:05 UTC.
- [ ] `v1.2.0` is cut by the owner and no `searxng-v*` tag is pushed; `publish` is green; the
      multi-arch `1.2.0` image is on GHCR; run URL, tagged commit sha and OCI index digest recorded.
- [ ] Four-way sha256 equality at `v1.2.0` verified and the four values recorded.
- [ ] `gh release view v1.2.0 --json body --jq '.body' | tr -d '\r'` matches `^contract: 1\.3\.0$` and
      contains every line of the rehearsal extraction (`grep -F` per line); both Release assertions
      green; assets `openapi.yaml` and `openapi.yaml.sha256` present.
- [ ] `docker buildx imagetools inspect` prints one identical index digest for `latest`, `1.2` and
      `1.2.0`; recorded.
- [ ] Smoke runs (1) and (2) from the `v1.2.0` checkout exit 0 with `--anchor contract/openapi.yaml.sha256`
      of that checkout; run (3) records `cache_unauthenticated` key-less and `healthy` +
      `capabilities.cache_hmac_key: 1` + a cache hit keyed; commands and exit codes recorded; every
      credential reached a container only through `--env-file` or a deleted throwaway `.env`.
- [ ] `secret-grep` and the publish config grep are green; both "No forbidden pattern" lines recorded.
- [ ] Credential-free pull after `docker image rm` recorded (both exit 0); Docker login restored;
      key-less `/health` shows `contract_version: "1.3.0"` and `promptguard_model`; the placeholder-key
      leak check is three zeros.
- [ ] The whole-tree leak grep over the record's diff returns nothing; the token file was mode 0600 and
      is recorded as deleted.
- [ ] The handoff table with the three new posture rows is in the Implementation Notes;
      `docs/releases.md`'s `v1.2.0` block has its digest and tagged commit filled; MILESTONES and
      PRODUCT_VISION (T2.2) read Shipped.

## Edge Cases

- A route-level 4xx raised by a middleware (413 size, 404 gated `/extract`) — never reaches the
  validation handler; unchanged (US-001).
- A wrong-typed body on the multipart `/extract` route — FastAPI's documented 400 body-parse guard,
  not the handler; the handler case is a well-formed multipart with an invalid `extract_mode` (US-001).
- A validation error whose `loc` contains an integer index (`providers[3]`) — kept as `int` (US-001).
- A body producing thousands of validation errors — `detail` capped at 100 entries (US-001).
- The `1.3.0` docstring entry accidentally contains a blank line — the extractor stops early and the
  read-back fails on the tag; `tests/test_ci_workflow.py` and US-003's rehearsal catch it before the
  push (US-002/US-003).
- A golden-visible addition nobody wrote into the docstring — the 1.3.0 coverage sweep fails (US-002).
- Between US-004's merge and the tag push, `main`'s compose examples pull an unpublished tag — accepted
  and stated; the owner cuts from that merge commit (US-004/US-003).
- The publish rebuild is cold — expected to pass since the reproducibility fix; record it as the first
  live cold proof if so (US-003).
- `latest` or `1.2` lands on a different digest than `1.2.0` — stop; treat the tags as untrusted, do
  not re-run blindly (`docs/releases.md` § "When something goes wrong") (US-003).
- A healthy smoke against a container whose env file carried `VALKEY_URL` without the HMAC key —
  `cache_unauthenticated` pins `degraded`; a red under the wrong flag is a misconfigured verification,
  not a release failure (US-003).
- The credential-free pull says "Image is up to date" — the `docker image rm` step was skipped; redo
  it (US-003).

## Out of Scope

- Anything inside Poppy: the re-vendor, the pin bump, mirroring the new fields (Poppy's
  `epic-search-policy` successor reads the handoff table; nothing here pushes).
- A `searxng-v*` companion release.
- Tag-vs-digest policy for the compose examples (audit -010) — **deferred to the next cut regardless of
  the owner's view at this one**; the `1.2.0` tag pin lands in US-004 and stands (the Open Question is
  advisory only). Image signing / provenance attestation — a later release-runbook item.
- Bounding `detail[].msg` beyond pydantic's stock text, or a MAJOR bump of any kind; every change in
  this epic is additive or a documented-shape tightening.
- A script wrapping the verification half of the runbook (second-opinion info) — a follow-up outside
  this epic.

## Assumptions

- Poppy is the only consumer and validates 1.3.0 with every new field defaulted.
- The six required checks and the `publish` lane are unchanged from `v1.1.0` except for the
  four-pattern secret grep (spec 4).
- `contract_smoke.py` does not assert `promptguard_model` or `cache_unauthenticated` (so it still
  passes against older images the way the runbook expects); the `full.yml` witness is a separate
  `/health` read, not a smoke flag.
- The owner has `packages:write` on GHCR and an HF token for the healthy smoke, as at `v1.1.0`; the
  Valkey for the `full.yml` run is the fragment's own service.
- `tests/test_contract_schema.py::_SCHEMA_MODELS` remains the only producer of the golden.

## Technical Considerations

- **The 1.3.0 window** — the single enumerated list the docstring entry, US-002's criterion and its
  Independent Test all use (reconciled against the appended lines at execution time): `blocked_url`
  omission reason and the bounded `SearchResult.engine` (spec 1); `effective_promptguard_fail_closed`
  and `effective_promptguard_threshold` on `RetrievedContent` and `SearchResponse`,
  `CacheMetrics.corrupt_entries`, and — only if spec 2 added it — `RetrieveErrorCode.extraction_failed`
  (spec 2); `SearchRequest.blocked_domains`, `promptguard_threshold: float | None` on both request
  models, the leading-dot allowlist form in three field descriptions, `policy_invalid_domain_entry`
  counters (spec 3); `cache_unauthenticated` `DegradedReason`, `capabilities.cache_hmac_key`,
  `CacheMetrics.integrity_rejects` (spec 4); `search.provider_compressed_body` (spec 5);
  `search.promptguard_latency_target_exceeded` (spec 6); `/health` `promptguard_model` and the three
  `promptguard_contiguity_detections` counters (spec 7); the request-validation 422 trio with the
  100-entry cap (spec 8 US-001). Golden-visible items are the request/response model fields and enum
  members; counters and `CacheMetrics` fields are pinned by `tests/test_contract_metrics.py`.
- **Rotations (ruling 6):** US-001 and US-002 each edit `pipeline/contract.py` — two measured,
  recorded rotations (the docstring is hashed bytes). US-004 and US-003 rotate nothing.
- **Anchor ownership (R19):** every story in this epic that regenerates the document refreshes the four
  anchor-quoting pages in its own commit; US-002 confirms, US-003's pre-flight re-verifies.
- **Contract window (ruling 5):** US-002 is the only story allowed to declare the `1.3.0` golden
  frozen; any later shape change in this epic is a bug.
- **Announcement (GOVERNANCE step 7, search-epic ruling 31):** the extractor is bash + awk in
  `ci.yml`; the entry's formatting rules (column-0 bullet, two-space continuation, no blank line) are
  load-bearing and pinned by `tests/test_ci_workflow.py`.
- **Priorities:** US-001 is standalone user-visible value; US-002 and US-003 are release-blocking
  (the epic's deliverable is a published digest); US-004 is bookkeeping that blocks the cut but
  changes no behaviour — P2 so the orchestrator knows it is the trimmable one.
- **Reproducible builds:** cut outside the passwd-layer window (`docs/releases.md`).

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
US-004 records the classified sweep; US-003 records the cut, the four-way sha256 table, the three
smoke runs, the credential-free pull, the leak check and the handoff table. -->

## Refinement Notes

### Research Findings

**Decision:** The validation 422 tightening is a PATCH-class change announced inside the 1.3.0 window,
with a 100-entry cap and a stated `msg` invariant.
**Rationale:** `ValidationErrorDetail` (`retrieval_app.py:805-818`) and `contract/openapi.yaml:1271-
1301` document only `loc`/`msg`/`type`; the extra keys are pydantic's runtime additions. Dropping
`input` closes the byte echo; the cap closes response-volume amplification; the `msg` invariant is what
keeps the echo closed once spec 3 adds request-side validation (validation round 1, salty and security).
**Alternatives considered:** Leaving it (an unbounded reflector); `extra="forbid"` without a handler
(would 500 on FastAPI's own body).
**Source:** `retrieval_app.py:805-834,1410`; `contract/GOVERNANCE.md:143-148`; `models.py:134,206,371`.

**Decision:** The golden is produced by `tests/test_contract_schema.py::_SCHEMA_MODELS`, and the
1.3.0 close-out re-bases that module's coverage sweep.
**Rationale:** `_SCHEMA_MODELS` (`:28-40`) is the only producer of `tests/golden/`;
`scripts/export_contract.py:267-270` writes the document, the anchor and the drift twin only; the 1.2.0
sweep (`:74-110,180-227`) is the previous epic's close-out mechanism and goes red once the golden path
moves to 1.3.0 (validation round 1, codebase-fit).
**Alternatives considered:** A hand-written docstring only — not mechanical; the last window's tense
flip was silently missed (`contract.py:65-68`).
**Source:** `tests/test_contract_schema.py:20,28-40,74-110,180-227`; `kit_tools/EXECUTION_LOG.md:264`.

**Decision:** The release witnesses the cache-integrity control on the released image.
**Rationale:** Both smokes exclude `VALKEY_URL` by design and `compose/minimal.yml` is memory-backed, so
without a `full.yml` bring-up the handoff's posture row would be sourced from unit evidence only
(validation round 1, security).
**Source:** `compose/full.yml:50-60`; spec 4 US-002.

**Decision:** `docker image rm` precedes the anonymous pull.
**Rationale:** The `v1.1.0` credential-free pull returned "Image is up to date", proving only
manifest resolution (audit finding 2026-09-17-014).
**Source:** `kit_tools/specs/archive/feature-search-release.md` § US-003 Implementation Notes.

### Scope Adjustments

- Validation round 1 (2026-09-19): US-002 split into US-002 (contract record, coverage sweep, golden,
  tense flip) and US-004 (fan-out by value, pins, counts, release draft); execution order
  `[US-001, US-002, US-004, US-003]`; US-001 owns its anchor refresh; a third smoke run added.
- **Correction:** the earlier Scope Adjustment claimed `_SCHEMA_MODELS` does not exist. It does —
  `tests/test_contract_schema.py:28` — and it is the golden's only producer; `scripts/export_contract.py`
  is what has no such constant. The `/metrics` counters and `CacheMetrics` fields are pinned by
  `tests/test_contract_metrics.py`, so the docstring names them and the golden does not move for them.

### Decisions Made

- Rulings 5, 19/R19, 20, R30, R31, R33 as stated in the epic wrapper and its validation-round-1 addendum.
- Overruled: "add a `logger.info` with the untrimmed `exc.errors()`" (second-opinion) — the handler
  logs types and a count only; the value must never reach a log (invariant 6's spirit).
- Overruled: "make the digest-vs-tag question decided at this cut" — deferred regardless (Out of
  Scope), so US-004's pin criterion stands.
- Overruled: "delete the boilerplate 'Tests written/updated' criterion" — kept on US-001 (it adds
  tests); dropped on US-002 and US-004, which add no functionality.

## Clarifications

### Session 2026-09-19
- Q: Does the epic end in a release? → A: Yes — all eight specs, ending in a `v1.2.0` cut (owner
  decision 1).
- Q: Is dropping `input`/`ctx` from the validation 422 a MAJOR? → A: No — the keys were never in the
  documented schema; PATCH-class tightening recorded as a GOVERNANCE ruling, announced in the 1.3.0
  window (ruling 19).

### Session 2026-09-19 (validation round 1)
- Rulings applied: R19 (US-001 owns the anchor refresh; `msg` invariant recorded), R30 (coverage sweep
  re-based, `_SCHEMA_MODELS` corrected, tense flip, one window list, third smoke run on `full.yml`,
  whole-tree leak grep), R31 (US-002/US-004 split and execution order), R33 (`umask 077` + `mktemp` +
  `trap`; no `gh auth token` output or `docker inspect` of keyed containers in the record).
- Q: How is `/extract` exercised for the 422? → A: A well-formed multipart with an invalid
  `extract_mode`; a wrong-typed body is the documented 400 guard.
- Q: What does the by-value sweep do with `v1.2.0` image-tag hits? → A: Classifies them as expected;
  only unclassified hits fail.

## Open Questions

- [ ] Whether the compose examples should pin by digest rather than tag from the *next* cut on (audit
      -010) — advisory at this cut; the `1.2.0` tag pin lands regardless. Non-blocking.
- [ ] Whether `detail[].msg` should be normalised to a closed vocabulary in a later PATCH (security
      warning) — the invariant test holds the line meanwhile. Non-blocking.
