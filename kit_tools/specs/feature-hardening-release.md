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
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Validation-422 Tightening + Contract 1.3.0 Close-Out + v1.2.0 Release Cut

> **Spec 8 (final) of `epic-forage-hardening`.** Tighten the one wire shape this epic still owes
> (the request-validation 422 body loses its `input`/`ctx`/`url` echoes — ruling 19), **close the
> 1.3.0 contract window** that spec 1 US-004 opened (ruling 5: complete the docstring record, freeze
> the golden, sweep every document that states the version or the anchor, move the compose pins),
> then **cut and publish `v1.2.0`** — **US-003 is the owner gate** (ruling 20) — and write the
> handoff record Poppy pins. Binding: rulings 5, 6, 19, 20; `contract/GOVERNANCE.md`'s bump procedure
> (`:296-325`); the `v1.1.0` runbook as executed (`kit_tools/specs/archive/feature-search-release.md`
> US-002/US-003 Implementation Notes).

## Overview

Specs 1–7 changed the wire only additively — `blocked_url`, `effective_promptguard_fail_closed`,
`SearchRequest.blocked_domains`, an honoured `promptguard_threshold`, `cache_unauthenticated`, the
`search.promptguard_latency_target_exceeded` counter, `promptguard_model`, and `CacheMetrics` fields —
each appending a line to the held `1.3.0` docstring entry and re-creating
`tests/golden/contract_1_3_0.json` in place. Nothing is published until this spec cuts `v1.2.0`. Three
stories, in document order:

- **US-001 (code, autonomous)** — an app-level `RequestValidationError` handler that re-emits the
  documented `{loc, msg, type}` trio and nothing else. Today there is no such handler
  (`retrieval_app.py:1410` registers only `PipelineError`), so FastAPI's stock body reaches the wire
  with pydantic's `input` (and sometimes `ctx`/`url`) — an unbounded reflector
  (`kit_tools/arch/SECURITY.md:174`). The documented schema (`ValidationErrorDetail`,
  `retrieval_app.py:805-818`; `contract/openapi.yaml:1271-1301`) never listed those keys, so this is a
  PATCH-class tightening announced inside the open MINOR window — recorded as a GOVERNANCE ruling.
- **US-002 (close-out, autonomous)** — the `1.3.0` record completed, the golden frozen, the fan-out
  swept, compose pins and `_FORAGE_RELEASE_TAG` at `1.2.0`, suite counts, the `docs/releases.md`
  entry drafted.
- **US-003 (the cut, owner gate)** — pre-flight, tag push, publish watched, four-way sha256, both
  smoke modes from the `v1.2.0` checkout, credential-free pull, handoff table. Execution halts here
  if the gate has not run.

The image tag moves `1.1.0 → 1.2.0` (MINOR feature) while the contract it advertises is `1.3.0` —
the two-semver rule (`contract/GOVERNANCE.md` § "Two semvers, independent").

## Goals

- A request that fails schema validation on any route returns a 422 whose every `detail[]` item has
  exactly the keys `loc`, `msg`, `type` — proven by a test that posts a wrong-typed body to `/search`,
  `/retrieve` and `/extract` and asserts `set(item) == {"loc", "msg", "type"}`.
- `CONTRACT_VERSION == "1.3.0"`, the docstring entry names every addition specs 1–7 made (one clause
  each), `uv run python -m scripts.export_contract --check` is clean, and
  `grep -rn '1\.2\.0' README.md CLAUDE.md contract/GOVERNANCE.md kit_tools/arch kit_tools/docs` finds
  the version only in history rows.
- A published multi-arch `v1.2.0` image advertising contract 1.3.0 through the gated lane, `latest`,
  `1.2` and `1.2.0` resolving to one index digest, verified from the `v1.2.0` checkout in both smoke
  modes.
- The handoff record (tag, index digest, contract, anchor, tagged commit, run URL, cache-integrity
  posture, model posture) in this file's Implementation Notes and `docs/releases.md`.

## User Stories

### US-001: Validation-422 body carries `loc`, `msg`, `type` only (ruling 19)

**Priority:** P1

**Description:** As an operator exposing port 8020 on a private network, I want a malformed request to
be refused without the service echoing the caller's bytes back, so the validation 422 cannot be used
as a reflector and the wire matches what the contract already documents.

**Independent Test:** `POST /search` with `{"query": 5, "providers": "x"}` returns 422; every entry
in `detail` has exactly the keys `loc`, `msg`, `type`; the literal `5` and `"x"` appear nowhere in the
body; the OpenAPI document regenerates without a schema change (`export_contract --check` clean after
the description edit and regenerate).

**Implementation Hints:**
- **Register the handler** beside `@app.exception_handler(PipelineError)` (`retrieval_app.py:1410`):
  `@app.exception_handler(RequestValidationError)` returning `JSONResponse(status_code=422,
  content={"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]})`.
  `loc` items are `str | int` — keep them as emitted (`ValidationErrorDetail.loc: list[str | int]`,
  `:813`). Do not touch `ValidationErrorDetail`'s `extra` setting; update its docstring (`:806-811`)
  to say the trio is now the whole body, not merely the documented subset.
- **Both middlewares stay first.** `DocumentSizeLimitMiddleware` and `ExtractionAdmissionMiddleware`
  refuse before routing; the handler only sees requests that reached a route. A test for each route's
  existing 4xx paths (`tests/test_app.py::test_post_retrieve_error_response` `:1734` region) proves
  nothing else moved.
- **Governance record.** Add the next lettered ruling to `contract/GOVERNANCE.md` § "Recorded rulings"
  (`:172+`, `### (x) Title` / **Ruling:** / **Source:** shape): "Dropping pydantic's undocumented
  `input`/`ctx`/`url` from the validation 422 is a PATCH-class tightening — the documented schema
  never carried them — announced inside the open 1.3.0 MINOR window." Source: this story and
  `kit_tools/arch/SECURITY.md:174`. `tests/test_governance_docs.py` has no count on rulings; check
  `test_it_names_the_regeneration_command_exactly` (`:205`) still passes.
- **Security docs.** Rewrite `kit_tools/arch/SECURITY.md:174`'s sentence ("A validation 422 echoes
  the offending value verbatim…") to the new fact and move the non-vulnerabilities row the
  housekeeping PR added for the 422 echo (spec 0, 2026-09-19) from "accepted" to "closed by
  `hardening-release` US-001".
- **Window line (ruling 5):** append one clause to the `1.3.0` docstring entry
  (`pipeline/contract.py:22-68`) — "the request-validation 422 body now carries exactly `loc`, `msg`,
  `type` per entry (a tightening of an undocumented echo, GOVERNANCE ruling (x))"; regenerate; re-create
  `tests/golden/contract_1_3_0.json`. `contract.py` is hashed → measure and record the rotation
  (ruling 6).

**Acceptance Criteria:**
- [ ] A `RequestValidationError` handler is registered; a wrong-typed body on `/search`, `/retrieve`
      and `/extract` (route enabled in the test) yields 422 with `set(item) == {"loc","msg","type"}`
      for every `detail` item, and the offending literals appear 0 times in the body (test).
- [ ] Existing 4xx/5xx paths on all routes are unchanged (the pre-existing error-response tests pass
      untouched).
- [ ] `contract/GOVERNANCE.md` § "Recorded rulings" gains the ruling with **Ruling** and **Source**;
      `kit_tools/arch/SECURITY.md` no longer says a 422 echoes the value verbatim (`grep -c 'echoes
      the offending value verbatim' kit_tools/arch/SECURITY.md` is 0).
- [ ] The `1.3.0` docstring entry names the tightening; `uv run python -m scripts.export_contract
      --check` clean; golden re-created; rotation recorded in `docs/bootstrap-notes.md`, `CLAUDE.md`,
      `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-002: Close the 1.3.0 window — record, golden, fan-out, pins, counts

**Priority:** P1

**Description:** As the consumer's re-vendoring session, I want one complete, mechanical statement of
what contract 1.3.0 changed and one frozen golden, with every document that restates the version or
the anchor already correct, so the `v1.2.0` Release body announces the whole epic and nothing in the
tree contradicts it.

**Independent Test:** `awk -v v=1.3.0 '<the extractor program from ci.yml's "Read the contract
version from the tagged tree" step>' pipeline/contract.py` prints one entry beginning
`* ``1.3.0`` ` that names `blocked_url`, `effective_promptguard_fail_closed`, `blocked_domains`,
`cache_unauthenticated`, `promptguard_latency_target_exceeded`, `promptguard_model` and the 422
tightening, with no line of the `1.2.0` entry; `uv run pytest tests/test_ci_workflow.py -k 'extractor
or docstring_entry'` passes; `uv run pytest tests/test_compose_fragments.py` passes with
`_FORAGE_RELEASE_TAG = "1.2.0"`.

**Implementation Hints:**
- **The record** (`pipeline/contract.py:22-68`): rewrite the `1.3.0` bullet into its final form —
  one bullet at column 0 opening `* ``1.3.0`` —`, continuation lines indented two spaces, **no blank
  line inside it** (a blank line ends the entry — GOVERNANCE step 7), every addition from specs 1–7
  (field, enum member, counter, honoured request field, the 422 tightening), the sentence "Every
  addition above is additive … a consumer comparing MAJOR keeps working untouched", and the "held
  until the `v1.2.0` image publishes it" closing clause replaced by "published by `v1.2.0`". Then
  `uv run python -m scripts.export_contract` one last time and treat `tests/golden/contract_1_3_0.json`
  as frozen from this commit (older goldens never edited — GOVERNANCE ruling (c)).
- **Rotation (ruling 6):** the docstring edit changes `contract.py`'s bytes → measure and record.
- **Fan-out sweep, by grep, each site named**: `README.md:62` ("currently **1.2.0**") and `:258`;
  `CLAUDE.md:82` (invariant 4), `:204,229` (Coexistence rotation history — append, do not rewrite);
  `contract/GOVERNANCE.md:29` ("The current contract version is **1.3.0**" — pinned by
  `tests/test_governance_docs.py::test_it_states_the_current_contract_version` `:193`) and § "Two
  semvers" (`:46`, the mapping row `v1.2.0 → 1.3.0`); `kit_tools/arch/CODE_ARCH.md` bold version;
  `kit_tools/docs/API_GUIDE.md` version + anchor; `kit_tools/arch/SERVICE_MAP.md` version rows and
  the anchor paragraph (~`:202`); `kit_tools/docs/CI_CD.md` and `kit_tools/docs/DEPLOYMENT.md` anchor
  literals. The new anchor is whatever `contract/openapi.yaml.sha256` holds after the final regenerate
  — copy it, never retype it: `anchor=$(cut -d' ' -f1 contract/openapi.yaml.sha256)`.
- **Compose pins**: `compose/minimal.yml` and `compose/full.yml` `image:` lines to `forage:1.2.0` and
  `tests/test_compose_fragments.py:77` `_FORAGE_RELEASE_TAG = "1.2.0"` — "move both fragments and the
  constant together" (`test_the_forage_image_is_the_current_release` `:505`). The compose envelope
  variables (spec 6) and `FORAGE_CACHE_HMAC_KEY` / `FORAGE_MODEL_ID` passthroughs (specs 4, 7) are
  already in the fragments; confirm by `grep -c` rather than re-adding.
- **Suite-count bookkeeping**: `uv run pytest` count into `kit_tools/testing/TESTING_GUIDE.md`,
  `kit_tools/SYNOPSIS.md`, `kit_tools/AGENT_README.md` and `CLAUDE.md`'s Development parenthetical
  (all four say the same number).
- **`docs/releases.md`**: draft the `### v1.2.0 — <date>` block under "Released versions" (`:13-20`)
  with `contract: 1.3.0`, the anchor, `index digest: (filled at the cut)`, `tagged commit: (filled at
  the cut)` and a "What shipped" list mirroring the docstring entry in prose; US-003 fills the two
  placeholders. Also the tag inventory sentences in `kit_tools/docs/DEPLOYMENT.md`, `CI_CD.md` and
  `kit_tools/arch/INFRA_ARCH.md` that name the current release.

**Acceptance Criteria:**
- [ ] The `1.3.0` docstring entry is a single well-formed bullet naming every addition of specs 1–7
      and US-001; the by-hand extractor run prints it verbatim (recorded in Implementation Notes) with
      no `1.2.0` line.
- [ ] `tests/golden/contract_1_3_0.json` regenerated for the last time; `export_contract --check`
      clean; the rotation recorded.
- [ ] `grep -rn '\*\*1\.2\.0\*\*' README.md CLAUDE.md contract/GOVERNANCE.md kit_tools/docs/API_GUIDE.md
      kit_tools/arch/CODE_ARCH.md kit_tools/arch/SERVICE_MAP.md` returns nothing; the four embedded
      anchors (API_GUIDE, CI_CD, DEPLOYMENT, SERVICE_MAP) equal `contract/openapi.yaml.sha256`
      (`grep -c "$anchor"` is 1 for each).
- [ ] `grep -c 'forage:1.2.0' compose/minimal.yml compose/full.yml` reports 1 each;
      `_FORAGE_RELEASE_TAG` is `"1.2.0"`; `tests/test_compose_fragments.py` green.
- [ ] TESTING_GUIDE, SYNOPSIS, AGENT_README and CLAUDE.md state the same suite count as
      `uv run pytest` collects.
- [ ] `docs/releases.md` carries the `v1.2.0` block with `contract: 1.3.0`, the anchor and the two
      placeholders; DEPLOYMENT/CI_CD/INFRA_ARCH name `v1.2.0` as the current release.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass

### US-003: Cut + publish the v1.2.0 image and write the handoff record (owner gate)

**Priority:** P1

**Description:** As the owner, I want a `v1.2.0` image published through the gated lane, verified
from the tagged checkout in both smoke modes and from a credential-free pull, with the record Poppy
pins written here — so the hardening epic's value reaches the consumer as one digest. **Execution
halts here for the owner** (ruling 20): the tag push is the human gate, and every step needs a Docker
daemon, network egress and GHCR access the autonomous environment does not have. The implementer
records that the gate has not run and stops; the verifier expects the evidence listed under
"Record" in the Implementation Notes.

**Independent Test:** The `v1.2.0` tag triggers `publish` (which `needs:` the six gates) and it goes
green; `gh release view v1.2.0 --json body --jq '.body' | tr -d '\r'` matches `^contract: 1\.3\.0$`
and contains every line of the `1.3.0` docstring entry; `latest`, `1.2` and `1.2.0` resolve to one
index digest; from the `v1.2.0` checkout `contract_smoke.py --image <digest ref> --anchor
contract/openapi.yaml.sha256` exits 0 with `--expect-status degraded` against a no-env container and
with `--expect-status healthy` against a weights-loaded one started with `--env-file`.

**Implementation Hints:**
- **The runbook is the `v1.1.0` cut**, executed and recorded at
  `kit_tools/specs/archive/feature-search-release.md` § "US-002 — v1.1.0 cut and publish" and
  § "US-003 — Post-release verification"; `docs/releases.md` § "Cutting a release" and § "When
  something goes wrong" (`:342-390`) are the short form. Follow them with `v1.2.0` / `1.3.0` / `1.2`
  substituted; only the differences are listed below.
- **Pre-flight, on `main` at the commit to be tagged:** the epic branch merged with a merge commit;
  the six required checks green on the merge commit's run; `git switch main && git pull` and `git
  log -1` recorded; `uv run python -m scripts.export_contract --check` clean; `grep CONTRACT_VERSION
  pipeline/contract.py` prints `1.3.0`; US-002's greps re-run on this commit (the `**1.2.0**` sweep
  empty, the four anchors equal, `forage:1.2.0` pinned once per fragment, suite counts equal); the
  extractor rehearsed by hand — `awk -v v=1.3.0 '<program>' pipeline/contract.py` — and its output
  recorded verbatim, first line `* ``1.3.0`` `, no `1.2.0` line. Cut clear of the ten minutes around
  00:00 UTC (`docs/releases.md` § "Reproducible builds").
- **The cut:** `git tag v1.2.0 && git push origin v1.2.0`, then `gh run watch`. No `searxng-v*` tag —
  the companion image is unchanged this epic.
- **Watch these `publish` steps live**, in order: "Verify the published amd64 image is the gated
  filesystem"; "Read the contract version from the tagged tree"; the published-config secret grep
  (now four patterns after spec 4 added `FORAGE_CACHE_HMAC_KEY` — confirm the count in the log);
  "Create the GitHub Release"; both Release assertions. Record cold/warm (count `CACHED` lines).
- **After the run:** the four-way sha256 table (committed anchor `git show v1.2.0:contract/
  openapi.yaml.sha256`; repository copy `git show v1.2.0:contract/openapi.yaml | shasum -a 256`;
  Release asset via `gh release download v1.2.0 --pattern 'openapi.yaml*'` then `shasum -a 256 -c
  openapi.yaml.sha256` → `openapi.yaml: OK`; in-image `docker run --rm --entrypoint cat
  ghcr.io/washingbearlabs/forage:1.2.0 /app/contract/openapi.yaml | shasum -a 256`); the Release body
  check; `docker buildx imagetools inspect ghcr.io/washingbearlabs/forage:<tag>` for `latest`, `1.2`,
  `1.2.0` — read the `Digest:` line (the `--format '{{.Manifest.Digest}}'` form printed whole blocks
  at `v1.1.0`); `latest` moves off the `1.1.0` image and `1.2` is minted — a pointer landing anywhere
  else is the one outcome to stop for.
- **Smoke, twice, from the tagged checkout** (`git switch --detach v1.2.0 && uv sync --extra dev`),
  `<ref>` = `ghcr.io/washingbearlabs/forage@sha256:<index digest>`, `--anchor
  contract/openapi.yaml.sha256` of that checkout both times (never the downloaded copy, never the
  in-image file — GOVERNANCE "Consumers"): (1) `docker run --rm -d -p 127.0.0.1:8020:8020 --name
  forage-rel <ref>` with no env file and no volume, then `uv run python contract_smoke.py --image
  <ref> --expect-status degraded --anchor contract/openapi.yaml.sha256`; (2) the same image with
  `--env-file "$TMPDIR/hf.env"` (one line, `HF_TOKEN=…`, written with `read -rs`, never printed,
  deleted afterwards) and `-v forage-model-cache:/app/model-cache`, then `--expect-status healthy
  --timeout-seconds 540`. Never `-e HF_TOKEN=…` — this file is public. No `VALKEY_URL` in the env
  file (an unreachable Valkey pins `cache_unavailable`; and with spec 4, a reachable Valkey without
  `FORAGE_CACHE_HMAC_KEY` pins `cache_unauthenticated` — either turns the healthy wait into a hang).
- **Third-party view** (the `v1.1.0` US-003 procedure): `docker logout ghcr.io`, then **`docker
  image rm <ref>` before the pull** so the anonymous pull proves more than manifest resolution (the
  audit's -014 note), `docker pull <ref>`, `docker login ghcr.io` restored afterwards via
  `gh auth token | docker login ghcr.io -u <user> --password-stdin`; key-less `/health` from
  `compose/minimal.yml` at `1.2.0` showing `contract_version: "1.3.0"`, `promptguard_model`, no
  `brave_api_key`, no `cache_unauthenticated` (memory backend); one `/search` round-trip; the
  placeholder-key packaging run at zero spend with the leak check over `/health`, `/metrics`, `docker
  logs` (three zeros).
- **Record** in `### US-003 — v1.2.0 cut, publish and handoff, <date>` under Implementation Notes: run
  URL, tagged commit sha, index digest, the four sha256 values, the `contract:` line and the entry as
  published, three-tag digest equality, cold/warm, the rehearsal extraction, both smoke commands with
  exit codes, the credential-free pull transcript, the key-less `/health`, the leak-check table, and
  the handoff table with the `v1.1.0` columns plus two new rows: **Cache-integrity posture** (a keyed
  Valkey verifies every value; a key-less external Valkey is reported `cache_unauthenticated`; memory
  mode needs no key) and **Model posture** (`promptguard_model` on `/health`; 22M default; 86M opt-in
  with the benchmark figures' location). Then fill the two `docs/releases.md` placeholders.

**Acceptance Criteria:**
- [ ] Pre-flight recorded: six checks green on the tagged commit; `export_contract --check` clean;
      `CONTRACT_VERSION` is `1.3.0`; US-002's greps re-run and pass; the by-hand `awk` extraction on
      that commit recorded verbatim (first line `* ``1.3.0`` `, no `1.2.0` line); the cut time is
      outside 23:55–00:05 UTC.
- [ ] `v1.2.0` is cut by the owner and no `searxng-v*` tag is pushed; `publish` is green; the
      multi-arch `1.2.0` image is on GHCR; run URL, tagged commit sha and OCI index digest recorded.
- [ ] Four-way sha256 equality at `v1.2.0` verified and the four values recorded.
- [ ] `gh release view v1.2.0 --json body --jq '.body' | tr -d '\r'` matches `^contract: 1\.3\.0$` and
      contains every line of the rehearsal extraction (`grep -F` per line); both Release assertions
      green; assets `openapi.yaml` and `openapi.yaml.sha256` present.
- [ ] `docker buildx imagetools inspect` prints one identical index digest for `latest`, `1.2` and
      `1.2.0`; recorded.
- [ ] Both smoke runs from the `v1.2.0` checkout exit 0 (`degraded` with no env file and no volume;
      `healthy` with `--env-file "$TMPDIR/hf.env"` and the warm volume); both commands and exit codes
      recorded; every recorded `--anchor` is `contract/openapi.yaml.sha256` of the `v1.2.0` checkout;
      `grep -nE 'hf_[A-Za-z0-9]{20,}' kit_tools/specs/feature-hardening-release.md` returns nothing.
- [ ] `secret-grep` and the publish config grep are green with the four-pattern set; both "No
      forbidden pattern" lines recorded.
- [ ] Credential-free pull after `docker image rm` recorded (both exit 0); Docker login restored;
      key-less `/health` shows `contract_version: "1.3.0"` and `promptguard_model`; the placeholder-key
      leak check is three zeros.
- [ ] The handoff table with the two new posture rows is in the Implementation Notes;
      `docs/releases.md`'s `v1.2.0` block has its digest and tagged commit filled;
      `kit_tools/roadmap/MILESTONES.md` and `kit_tools/PRODUCT_VISION.md` (T2.2) read Shipped.

## Edge Cases

- A route-level 4xx raised by a middleware (413 size, 404 gated `/extract`) — never reaches the
  validation handler; unchanged (US-001).
- A validation error whose `loc` contains an integer index (`providers[3]`) — kept as `int` (US-001).
- The `1.3.0` docstring entry accidentally contains a blank line — the extractor stops early and the
  read-back fails on the tag; the by-hand rehearsal in US-003's pre-flight is what catches it before
  the push (US-002/US-003).
- The publish rebuild is cold — expected to pass since the reproducibility fix; record it as the first
  live cold proof if so (US-003).
- `latest` or `1.2` lands on a different digest than `1.2.0` — stop; treat the tags as untrusted, do
  not re-run blindly (`docs/releases.md` § "When something goes wrong") (US-003).
- A healthy smoke against a container whose env file carried `VALKEY_URL` — `cache_unavailable` or
  `cache_unauthenticated` pins `degraded`; a red under the wrong flag is a misconfigured verification,
  not a release failure (US-003).
- The credential-free pull says "Image is up to date" — the `docker image rm` step was skipped; redo
  it (US-003).

## Out of Scope

- Anything inside Poppy: the re-vendor, the pin bump, mirroring the new fields (Poppy's
  `epic-search-policy` successor reads the handoff table; nothing here pushes).
- A `searxng-v*` companion release.
- Tag-vs-digest policy for the compose examples (audit -010) — a release-runbook decision deferred to
  the next cut after this one.
- A MAJOR bump of any kind; every change in this epic is additive or a documented-shape tightening.

## Assumptions

- Poppy is the only consumer and validates 1.3.0 with every new field defaulted.
- The six required checks and the `publish` lane are unchanged from `v1.1.0` except for the
  four-pattern secret grep (spec 4).
- `contract_smoke.py` does not assert `promptguard_model` or `cache_unauthenticated` (so it still
  passes against older images the way the runbook expects).
- The owner has `packages:write` on GHCR and an HF token for the healthy smoke, as at `v1.1.0`.

## Technical Considerations

- **Rotations (ruling 6):** US-001 and US-002 each edit `pipeline/contract.py` — two measured,
  recorded rotations (the docstring is hashed bytes).
- **Contract window (ruling 5):** US-002 is the only story allowed to declare the `1.3.0` golden
  frozen; any later shape change in this epic is a bug.
- **Announcement (GOVERNANCE step 7, search-epic ruling 31):** the extractor is bash + awk in
  `ci.yml`; the entry's formatting rules (column-0 bullet, two-space continuation, no blank line) are
  load-bearing.
- **Reproducible builds:** cut outside the passwd-layer window (`docs/releases.md`).

## Related Documentation

- Governance: [`contract/GOVERNANCE.md`](../../contract/GOVERNANCE.md) (§ "Bumping the contract",
  § "Recorded rulings", § "Two semvers")
- Releases: [`docs/releases.md`](../../docs/releases.md)
- The `v1.1.0` record: [`archive/feature-search-release.md`](archive/feature-search-release.md)
- Security: [SECURITY.md](../arch/SECURITY.md) (request models, non-vulnerabilities table)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("A cold-cache publish fails its own parity gate")

## Implementation Notes

<!-- Populated during execution. US-001/US-002 record their rotations and the rehearsal extraction;
US-003 records the cut, the four-way sha256 table, both smoke runs, the credential-free pull and the
handoff table. -->

## Refinement Notes

### Research Findings

**Decision:** The validation 422 tightening is a PATCH-class change announced inside the 1.3.0 window.
**Rationale:** `ValidationErrorDetail` (`retrieval_app.py:805-818`) and `contract/openapi.yaml:1271-
1301` document only `loc`/`msg`/`type`; the extra keys are pydantic's runtime additions that the model
deliberately did not close over. Removing an undocumented key is not a documented-field removal.
**Alternatives considered:** Leaving it (an unbounded reflector, `SECURITY.md:174`); making the model
`extra="forbid"` without a handler (would 500 on FastAPI's own body).
**Source:** `retrieval_app.py:805-834,1410`; `contract/GOVERNANCE.md:143-148` (six-example table).

**Decision:** One frozen golden per MINOR, re-created in place until the release story closes it.
**Rationale:** The `1.2.0` entry records exactly this practice ("This version is **held** … regenerated
in place … until the `v1.1.0` image publishes it"); the golden is a wire snapshot, not history.
**Alternatives considered:** A golden per story — noise, and older goldens are never edited (ruling (c)).
**Source:** `pipeline/contract.py:26-68`; `contract/GOVERNANCE.md:296-325`.

**Decision:** `docker image rm` precedes the anonymous pull.
**Rationale:** The `v1.1.0` credential-free pull returned "Image is up to date", proving only
manifest resolution (audit finding 2026-09-17-014).
**Source:** `kit_tools/specs/archive/feature-search-release.md` § US-003 Implementation Notes.

### Scope Adjustments

- The outline named a `_SCHEMA_MODELS` constant to extend; no such constant exists — the export calls
  `app.openapi()` (`scripts/export_contract.py:150-156`). The stories reference the regenerate command
  only.
- The outline listed the `/metrics` `CacheMetrics` fields (specs 2 and 4) as window members; they are
  pinned by `tests/test_contract_metrics.py` against the handler rather than by the golden, so the
  docstring entry names them but the golden does not move for them.

### Decisions Made

- Rulings 5, 19 and 20 as stated in the epic wrapper; the third-party view is repeated because it is
  the only credential-free witness Poppy gets.

## Clarifications

### Session 2026-09-19
- Q: Does the epic end in a release? → A: Yes — all eight specs, ending in a `v1.2.0` cut (owner
  decision 1).
- Q: Is dropping `input`/`ctx` from the validation 422 a MAJOR? → A: No — the keys were never in the
  documented schema; PATCH-class tightening recorded as a GOVERNANCE ruling, announced in the 1.3.0
  window (ruling 19).

## Open Questions

- [ ] Whether the compose examples should pin by digest rather than tag from `v1.2.0` on (audit
      -010) — decided at this cut by the owner; non-blocking (the pin test follows either choice).
