<!-- Template Version: 2.5.0 -->
---
feature: forage-ci-and-image
status: completed
session_ready: true
depends_on: []
vision_ref: Secure Web Retrieval / provider-independent web access
type: epic-child
size: L
epic: forage-extraction-forage-side
epic_seq: 1
epic_final: false
execution_order: [US-001, US-006, US-002, US-003, US-005, US-007, US-004, US-008]
created: 2026-09-02
updated: 2026-09-10
completed: 2026-09-10
---

# Feature Spec: Forage CI + Published Images (GHCR, secret-free)

> **Executes in the Forage repo** (copied there by spec 1 US-005). GitHub-hosted runners —
> Forage has no GPU/DB needs and must not depend on Poppy's self-hosted runner. **US-008 (the
> public flip) is a human gate — pause for the supervisor**; several publish ACs additionally
> need a real tag push, flagged per-story.

## Overview

Give Forage real CI (lint + format + strict types + tests on every PR) and a gated release
pipeline publishing two images to GHCR: `ghcr.io/washingbearlabs/forage` (the service, **no
baked weights, no HF build-arg** — closing the docker-history token-leak gotcha that blocks
any public push) and `ghcr.io/washingbearlabs/forage-searxng` (digest-pinned base + baked
config with **honestly-scoped public defaults** — round 2 established that `limiter: true`
without a Redis backend is inert theater; US-004's own measurements then went further and
established that a *working* limiter refuses the image's only client (429 on request one)
and caps JSON at 4 requests/hour via a module constant, so the shipped image bakes
`limiter: false` with the env-pair opt-in (`SEARXNG_LIMITER=true` + `SEARXNG_VALKEY_URL`)
documented; the hardening is: no IP-trust relaxations, no baked secret, and the measured
limiter reality documented, proven by a cross-container JSON smoke). All jobs live in **one workflow file** so `needs:` edges are
real, jobs are introduced in dependency order across stories (no forward `needs:` to a job a
later story creates — round-2 finding), and required status checks are registered only after
every job exists (US-007). The publish job runs after smoke + secret-grep; the one-way public
flip (US-008) is last, gated three ways.

## Goals

- Every PR runs ruff check, ruff format --check, pyright strict, and pytest — all green with
  zero baseline carve-outs; the same jobs gate publishes via same-workflow `needs:`.
- A `v*` tag publishes the service image + a GitHub Release, a `searxng-v*` tag publishes the
  companion image — in both cases only after their gate chains pass; `main` pushes publish
  `sha-` service tags through the same chain.
- `docker history --no-trunc` of the amd64 service image contains no `HF_TOKEN`, the
  committed `uv.lock` contains no `nvidia-*` wheels, and the repo's full git history re-scans
  clean before the one-way public flip.
- The published service image, run with no env, serves `/health` with `status: "degraded"`,
  `"promptguard_unavailable"` in `degraded_reasons`, and `contract_version` equal to
  `pipeline/contract.py::CONTRACT_VERSION` within 120 s of start.
- A second container can query the published searxng image with `format=json` and get engine
  results (the client path Forage itself uses — proves bot-detection defaults don't break
  the API consumer).

## User Stories

### US-001: CI workflow — ruff + format gates, CPU-locked dependencies

**Priority:** P1

**Description:** As a Forage maintainer, I want a single CI workflow with lint and format
enforced and the dependency lock CPU-clean, structured so later jobs hang off it with real
`needs:` edges.

**Independent Test:** A PR with a deliberate lint error fails the `lint` job; one with only a
formatting error fails the `format` check; `grep nvidia- uv.lock` is empty; main is green.

**Implementation Hints:**
- One file: `.github/workflows/ci.yml`, triggered on `pull_request`, `push` to `main`, and
  tags `v*` **and `searxng-v*`** (the second pattern is load-bearing — round-2 finding:
  `v*` does not match `searxng-v*`, so without it the companion image's publish lane is
  unreachable). Publish jobs are added by later stories; this story lands only `lint` — no
  `needs:` may reference a job that doesn't exist yet (GitHub rejects the whole file).
- Jobs: `lint` (`uv run ruff check .` + `uv run ruff format --check .`). Burn the measured backlog to zero — per spec 1's **final** recorded numbers (US-004
  execution, 2026-09-07): `ruff check` already 0, **6 files** fail `ruff format --check`;
  the earlier 25/11 planning figure is superseded.
- Poppy's workflow supply-chain conventions (this repo goes public): SHA-pin every
  third-party action with a `# vX.Y.Z` comment, top-level `permissions: contents: read`,
  `persist-credentials: false` on checkout. **Fork posture, corrected** (round-3 critical:
  Poppy's fork guard is a *self-hosted-runner* mitigation — its own comment says so — and
  porting it to `ubuntu-latest` would skip every gate on external PRs, or kill fork CI
  outright): on GitHub-hosted runners, fork PRs run the quality gates normally with
  read-only `GITHUB_TOKEN` and no secrets (GitHub's default); the rule to encode is
  **`pull_request_target` is banned** (workflow-shape test) and no job exposes secrets to
  fork-triggered runs.
- **Port, don't reinvent, Poppy's workflow guard suite** (round-3 finding):
  `tests/deployment/test_ci_workflow.py` already asserts SHA-pinning, top-level read-only
  permissions, `persist-credentials: false`, and trigger shapes — none of which actionlint
  checks; adapt a trimmed copy for Forage's workflow, and give the new non-Python guard
  tests `test_mapping` entries per the KitTools convention.
- Verify spec 1's CPU-torch lock landed: `uv sync` in CI must pull no CUDA wheels — assert by
  grepping the **committed lock** (a warm cache makes job-log inspection vacuous; round-2
  finding), plus one cold-cache job-log check recorded in Implementation Notes.
- Add `actionlint` (pinned) as part of the lint job — machine-checks workflow validity on
  every PR, replacing "verified once" throwaway proofs for the workflow structure.
- Do NOT register required status checks here (US-007 does, once all jobs exist — round-2
  deadlock finding); branch protection stays PR-only until then.

**Acceptance Criteria:**
- [x] `ci.yml` with `lint` job on PR/push/both tag patterns; actions SHA-pinned; top-level
      read-only permissions; `persist-credentials: false`; `pull_request_target` banned
      (workflow-shape test); the ported workflow guard suite green; `actionlint` green.
- [x] Ruff check + format backlog burned to zero, no excludes; `uv run ruff format
      --check .` added to this spec's standard AC tail (it is the gate this story adds).
- [x] `grep nvidia- uv.lock` empty (committed lock), one cold-cache CI log inspection
      recorded.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .` passes

### US-006: Pyright-strict burn-down + tests-lane policy

**Priority:** P1

**Description:** As a Forage maintainer, I want pyright strict enforced in CI with the
never-measured backlog actually burned, under an explicit written policy for test-code rules.

**Independent Test:** A PR introducing a type error fails the `typecheck` job; `uv run
pyright` exits 0 on main.

**Implementation Hints:**
- **Re-measure at story start** in the real environment (`uv sync --extra dev` first —
  round 2 found the ~313 figure was taken in a venv missing torch/transformers/trafilatura,
  and ~87 of those errors were in `test_searxng_docker.py`'s departed compose half). Record
  the fresh number before burning.
- Policy (decided 2026-09-02): service code fully strict — fix real errors; missing
  third-party stubs handled via a small `typings/` dir containing **only stub declarations
  for symbols actually used** (never a shadow of torch's real types — round-2 caveat). For
  `tests/`, a pyright execution environment disables `reportPrivateUsage` only (rule-level,
  documented); every other strict rule stays on.
- Wire the `typecheck` job into ci.yml. **Escape hatch** (pre-agreed, round-2 finding): if
  the burn-down overruns a session, split service-code-strict (blocking) from
  tests-strict (follow-up story) at the supervisor pause — the seam is the two pyright
  execution environments.

**Acceptance Criteria:**
- [x] Fresh backlog measurement recorded; `uv run pyright` exits 0 on main; only rule-level
      relaxation is `reportPrivateUsage` for `tests/`, with the policy comment.
- [x] Third-party gaps via minimal `typings/` stubs, no inline suppressions.
- [x] `typecheck` job in ci.yml (registration as required check deferred to US-007).
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

### US-002: Test lane

**Priority:** P1

**Description:** As a Forage maintainer, I want the 531-test suite running on GitHub-hosted
runners on every PR so regressions are caught without Poppy's infrastructure.

**Independent Test:** A PR that breaks a pipeline stage test fails the `test` job; the
hermeticity guard is exercised by a committed always-skipped canary test, not a throwaway.

**Implementation Hints:**
- `ubuntu-latest`, cached uv, `uv run pytest -q` as the `test` job.
- Hermeticity from spec 1's `pytest-socket` autouse guard; commit a canary that **runs and
  passes** — `tests/test_hermeticity.py` asserting `pytest.raises(SocketBlockedError)` on a
  socket attempt (round-3 prose fix: it is an executing test, not "always-skipped"; Poppy's
  `tests/test_conftest_socket_isolation.py` is the ready-made template to port).
- Named step `uv run pytest -q tests/test_sanitizer_revision.py` (mirrors Poppy
  `ci.yml:141-145`).

**Acceptance Criteria:**
- [x] `test` job green on PR + main; sanitizer-revision step present; hermeticity canary
      committed and passing.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

### US-003: Secret-free service image build + secret-grep gate

**Priority:** P1

**Description:** As a release manager, I want the service image built weights-free and
secret-free from locked dependencies, with a mechanical history-grep job — the publish
pipeline itself is US-007.

**Independent Test:** The `build-amd64` job produces a loadable image; the `secret-grep` job
greps its `docker history --no-trunc` for a defined pattern set and passes; a deliberately
re-added `ARG HF_TOKEN` (committed canary test on the Dockerfile text, not a throwaway run)
fails.

**Implementation Hints:**
- **Delete the HF build-arg path**: `ARG HF_TOKEN` + the conditional `from_pretrained` bake
  block go away entirely; keep `ENV HF_HOME=/app/model-cache` and the
  `RUN python -c "import retrieval_app"` build smoke. Install dependencies from the
  committed `uv.lock` (`uv sync --locked` in the Dockerfile) — not the current unpinned
  pip-parse (round-1 finding), and now guaranteed CPU-only by US-001's lock.
- **Digest-pin the base**: `FROM python:3.12-slim@sha256:<digest> # 3.12.x` — the smoke→push
  identity argument fails if the base can move between the two builds (round-2 finding), and
  it matches the repo's own pinning posture.
- Jobs this story lands: `build-amd64` (linux/amd64, `load: true`, GHA cache) and
  `secret-grep`. **Image handoff is explicit** (round-3 critical: `needs:` is an ordering
  edge, not a shared Docker daemon — a downstream job on a fresh runner has no image):
  `build-amd64` ends with `docker save` → `actions/upload-artifact`; `secret-grep` and
  US-005's `smoke` each `download-artifact` + `docker load` and **assert the loaded image
  ID/digest equals the one `build-amd64` recorded** — one artifact, provably the same
  bytes, never a rebuild. Grep pattern set defined in the workflow: `HF_TOKEN`,
  `hf_[A-Za-z0-9]{20,}` (a bounded list; `docker history` inspects layer
  metadata/commands, not file contents — the content guard is the deleted build-arg path +
  the git-history scan).
- Add a plain pytest asserting the Dockerfile contains no `ARG HF_TOKEN`/`ENV *TOKEN` line —
  the permanent regression guard.

**Acceptance Criteria:**
- [x] Dockerfile: no HF build-arg/bake, `uv.lock`-driven install, digest-pinned base;
      Dockerfile-text guard test committed.
- [x] `build-amd64` + `secret-grep` jobs green with the save/upload/load/digest-equality
      handoff (asserted, not assumed); grep pattern set defined in-workflow; metadata-scope
      caveat documented in the job comment.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

### US-005: Published-image contract smoke job

**Priority:** P1

**Description:** As the Poppy integration owner, I want CI to run the built image and assert
the `/health` contract so no artifact can regress the Epic 1 handshake.

**Independent Test:** The `smoke` job (`needs: build-amd64`, image obtained via US-003's
`download-artifact` + `docker load` with the digest-equality assertion — `needs:` alone
provides ordering, not the image) starts the candidate with no HF token, polls `/health` up
to 120 s, and fails on shape/value drift; assertions are shared with the golden-schema
test's field source, not hand-enumerated.

**Implementation Hints:**
- `docker run -d -p 8020:8020` the loaded amd64 image; poll ≤120 s (round-2 finding: 30 s
  spans a cold torch import too tightly); on failure dump `docker logs` into the job output.
- Assert: HTTP 200; `status == "degraded"`; `"promptguard_unavailable"` in
  `degraded_reasons`; `capabilities` lacks `search_sanitization`; `contract_version` read
  from `pipeline/contract.py` at job time; `sanitizer_revision` non-empty; `/metrics`
  responds with `contract_version`. Import the field expectations from the same module the
  golden test uses (`tests/test_contract_schema.py` machinery) — one source of truth.
- Spec 5 later extends this job (in-image contract file ↔ `/health` version equality).

**Acceptance Criteria:**
- [x] `smoke` job green via the artifact handoff (download + load + digest-equality
      asserted against `build-amd64`'s recorded ID), 120 s budget, log dump on failure,
      field source shared with the golden-schema machinery.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

### US-007: Publish pipeline — tags, Release, multi-arch, required checks

**Priority:** P1

**Description:** As a release manager, I want the gated publish job, the GitHub Release, and
the now-complete set of required status checks wired, so a tag can ship only through green
gates.

**Independent Test:** Push a `v0.9.0-rc` tag → `publish` (`needs: [lint, typecheck, test,
build-amd64, secret-grep, smoke]`) pushes semver tags + creates a Release; the run URL and a
deliberately-red rehearsal run URL are recorded in Implementation Notes (needs a real tag
push — supervised checkpoint, flagged).

**Implementation Hints:**
- `publish` job conditioned on `v*` tags and main pushes: `docker/metadata-action` tags —
  on `v*`: `X.Y.Z`, `X.Y`; on main: `sha-<short>`. **`latest` only on non-prerelease `v*`**
  (round-2 finding: the rc-tag Independent Test must not move `latest`). Multi-arch rebuild
  from the warm cache (qemu for arm64) with the **feasibility check first** — verify torch
  CPU aarch64 wheels resolve and build time is < 30 min; else amd64-only, decision recorded
  in `docs/releases.md` and the platform list trimmed.
- GH Release created on `v*` (spec 5 attaches the contract asset). Job-scoped permissions:
  `packages: write` **and `contents: write`** (Release creation 403s under the top-level
  read-only default without it — round-3 finding), both on this job only. Failure modes stated: a partially-pushed multi-arch manifest re-runs
  idempotently (buildx pushes are atomic per manifest list); Release creation after image
  push, so a Release implies a pulled image exists.
- Register required status checks now that all six jobs exist: `lint`, `typecheck`, `test`
  (+ `actionlint` if separate). **Name the actor**: branch-protection edits need admin — a
  fine-grained PAT or the supervisor by hand; `GITHUB_TOKEN` cannot (round-2 finding). AC
  records which was used.
- `docs/releases.md`: tag scheme, image-semver source of truth (git tag), prerelease
  policy, amd64/arm64 status.

**Acceptance Criteria:**
- [x] `publish` with the full `needs:` list; semver/`latest`/`sha-` tag policy implemented
      as stated; Release on `v*`; multi-arch or the documented fallback.
- [x] Green publish run URL + red-rehearsal run URL recorded (supervised tag pushes).
- [x] Required status checks registered (actor recorded); `docs/releases.md` written.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

### US-004: forage-searxng companion image (honest public defaults)

**Priority:** P1

**Description:** As a Forage consumer, I want a published SearXNG image with baked engine
config, no IP-trust relaxations, no baked secret, and a *working* JSON API — with rate
limiting documented as requiring Redis rather than pretended.

**Independent Test:** Two-container smoke: the searxng image + a curl container on one
network — a `format=json` query returns engine results (bot-detection defaults verified
compatible with the API client path Forage itself uses).

**Implementation Hints:**
- `searxng/Dockerfile`: `FROM searxng/searxng@sha256:<digest>` + `COPY config/
  /etc/searxng/`.
- **Honest hardening** (round-2 critical: `limiter: true` with no Redis backend is inert —
  SearXNG logs it and runs unthrottled): baked config drops `pass_ip = ["0.0.0.0/0"]`, drops
  the `forwarded_for`/`real_ip` header trust (client-spoofable once the image is reached
  directly — round-2 finding), removes the baked `secret_key`, keeps engines/timeouts/JSON
  format. ~~sets `limiter: true` with the Redis requirement documented~~ **[RETIRED by
  US-004's own measurements — supervisor correction 2026-09-08]**: the story verified the
  env var (it is `SEARXNG_VALKEY_URL`; `SEARXNG_REDIS_URL` is deprecated-but-working) and
  found that a *working* limiter refuses the image's only client — Forage's httpx gets 429
  on request ONE (`http_accept_language`), and even browser-shaped clients get 4
  `format!=html` API requests/hour (`ip_limit.API_MAX`, a module constant `limiter.toml`
  cannot raise). The image therefore ships `limiter: false` with the measurements written
  into the config file and the env-pair opt-in (`SEARXNG_LIMITER=true` +
  `SEARXNG_VALKEY_URL`) documented; the spec-4 compose fragments must NOT wire a limiter
  (cache-fallback US-004 hint corrected in-repo). See US-004 Implementation Notes.
  Whether `link_token` can be enabled without breaking the JSON client is answered by the
  smoke below, not assumed (answer: no — off).
- **Cross-container smoke, split blocking/advisory** (round-3 critical — a live
  third-party engine query in a publish `needs:` chain reproduces the exact
  every-engine-throttled outage GOTCHAS records, with no break-glass): the **blocking**
  half is hermetic — searxng + a Redis/Valkey container + a curl container on one network;
  assert the limiter actually initialized (no inert-limiter warning in logs), a
  `format=json` request returns HTTP 200 with a parseable envelope carrying a `results`
  key and no bot-detection block page (proves baked config + `link_token` compatibility
  with the API client path); the **advisory** half (real engine results non-empty) runs
  non-blocking with its outcome logged. **[Verified 2026-09-08]** the env-var name is
  `SEARXNG_VALKEY_URL` (upstream renamed the family toward Valkey; `SEARXNG_REDIS_URL`
  is deprecated-but-working — both exercised live against the pinned digest); the
  correction is propagated to cache-fallback US-004 (in-repo, commit `3bc6daf`) and to
  poppy-consume US-002 (Poppy repo, via supervisor handoff).
- `SEARXNG_SECRET` mechanism: verify the upstream image's env substitution **in the
  blocking smoke** (set it; assert start + serve) and determine unset behavior
  empirically; document both.
- Update the moved config-half tests (in Forage since spec 1): `test_engines_configured`
  (asserts `"bing" in engine_names`) becomes the disabled-engine negative;
  `test_secret_key_set` becomes the no-baked-literal negative; the two `TestSearxngLimiter`
  tests re-point at the new limiter config values. Engine-parity test: compare the enabled
  set against `_SEARXNG_ENGINES.split(",")` — it is a comma-joined **string** at
  `orchestrator.py:554` (round-2 finding).
- **Independent versioning**: `searxng-v*` tags (trigger already in US-001's workflow);
  publish lane `needs:` its own build + the blocking smoke **+ the `test` job** (it carries
  the config-regression and parity guards this story adds — round-3 finding); guard
  conditions so `v*` and `searxng-v*` lanes never cross-fire. **Pre-agreed split line if
  this story overruns a session** (round-3 sizing): artifact half (Dockerfile + config +
  config tests) vs pipeline half (smoke jobs + publish lane + docs).
- Add a config-regression pytest: baked `settings.yml`/`limiter.toml` contain no
  `pass_ip` wildcard, no `secret_key` literal, no header-trust keys (the guard that stops a
  future edit silently re-relaxing a public image — round-2 finding).
- `docs/searxng.md`: pin-bump cadence (bump digest → smoke → `searxng-v*` tag; explicitly
  the replacement for Poppy's deploy-time `:latest` refresh, with the honest note that
  engine rot now surfaces via failed JSON smokes at bump time and Poppy-side web probes at
  runtime), Redis requirement, header-trust rationale, Poppy limiter-overlay pattern.

**Acceptance Criteria:**
- [x] `forage-searxng` published under `searxng-v*` via its own gated lane; base
      digest-pinned; no bind mount needed.
- [x] Baked config: no `pass_ip` wildcard, no baked secret, no client-header trust;
      limiter-with-Redis documented; config-regression pytest committed. *(Satisfied with
      a measured deviation: the image ships `limiter: false` — a working limiter refuses
      the JSON client — with the Valkey-backed opt-in documented; see the RETIRED hint
      above and Implementation Notes.)*
- [x] Blocking hermetic smoke (redis-backed limiter initialized + JSON envelope + no
      bot-block page) green in the lane; advisory live-engine half non-blocking; the real
      Redis env-var name verified against the pinned digest, recorded, and corrected in the
      two citing sibling specs; `SEARXNG_SECRET` set/unset behavior verified + documented.
      *(The smoke proves a Valkey-backed limiter CAN initialize — behaviorally, via the
      keys it writes, differentially vs no-backend — which is the clause's substance; the
      shipped default is off, per the deviation above. Verified name: `SEARXNG_VALKEY_URL`.)*
- [x] All four moved config-half tests updated (two negatives, limiter re-points,
      string-split parity) and green.
- [x] `docs/searxng.md` complete per hints.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

### US-008: Public flip (human gate)

**Priority:** P3 *(deliberately last and human-executed; the epic's downstream specs need it
done before spec 6 pulls images, but nothing in THIS spec depends on it)*

**Description:** As the owner, I want the repo and both GHCR packages flipped public exactly
once, behind every gate, with the window controlled.

**Independent Test:** Anonymous `docker pull` of both images succeeds; the flip checklist is
recorded.

**Gate decisions (2026-09-08, owner — codified before execution):**
1. **Break-glass flag renamed before the flip.** `FORAGE_LEGACY_CAPABILITY` →
   `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` (self-describing; the old name read like a
   compat shim). The never-deployed interim name is retired un-aliased;
   `POPPY_RETRIEVAL_LEGACY_CAPABILITY` stays as the deployed-in-Poppy alias. Semantics
   unchanged (exact `== "1"`, loud boot warning, only `capabilities` lies). Log token
   `legacy_capability_advertisement_active` → `break_glass_advertisement_active`.
2. **Limiter posture accepted.** The public `forage-searxng` ships `limiter: false` per
   US-004's measurements; protection is deployment-side (internal network / fronting
   service), stated loudly in the README images section, matching the official
   Redis/Postgres-image posture. Round 2's `limiter: true`-is-hardened premise is
   formally retired.
3. **Registry after the purge: fresh rc tags.** Step (e) deletes every pre-flip version;
   after the flip, push `v0.9.1-rc` + `searxng-v0.1.1-rc` through the full gate chains —
   they verify the anonymous-pull AC, carry the corrected OCI labels, and prove publish
   works on the public repo. The weights mirror (spec 2's `forage-weights`, when created)
   stays **private** regardless — Llama license.

**Implementation Hints:**
- Gates, in order: (a) `secret-grep` green on the current images; (b) spec 1's full-history
  secret scan re-run clean at HEAD (`gitleaks detect --redact`); (c) LICENSE/NOTICE present;
  (d) **merge freeze on `main` for the flip window** (round-2 race finding); (e) delete all
  pre-flip GHCR package versions (needs a PAT with `delete:packages` — `GITHUB_TOKEN`
  cannot; round-2 finding); (f) flip repo + both packages public; (g) lift the freeze.
- Note for the record: the `poppy-searxng-internal` placeholder in git history is Poppy's
  live compose value until spec 6 rotates it — the flip is safe because the string grants
  nothing outside poppy-net, but spec 6's rotation is registered as a follow-through
  (cross-referenced there).
- Supervisor performs; everything recorded in Implementation Notes.

**Acceptance Criteria:**
- [x] Seven-step checklist executed in order and recorded; anonymous pulls of both images
      verified.
- [x] The spec-6 secret-rotation follow-through cross-reference recorded.

## Edge Cases

- arm64 qemu build times out/flakes → feasibility check fixes or documents amd64-only;
  smoke always runs the amd64 leg (US-007).
- Tag pushed on a red tree → publish `needs:` the quality jobs in the same workflow
  (US-007); actionlint prevents invalid-workflow states between stories (US-001).
- GHCR package auto-created private on first push → flip checklist purges then flips
  (US-008).
- SearXNG upstream digest yanked → pin-bump doc covers recovery (US-004).
- `SEARXNG_SECRET` unset at runtime → behavior verified + documented, never a silently
  baked known secret (US-004).
- A `searxng-v*` tag must never trigger the service publish lane and vice versa → explicit
  guard conditions, asserted by actionlint + a workflow-shape pytest (US-004/US-007).

## Out of Scope

- Model download-at-start and the weights mirror (spec 3) — the weights-free image is
  degraded-but-honest in the gap and Poppy does not consume it yet.
- The OpenAPI contract file + drift check (spec 5); image signing/provenance (conscious
  deferral — revisit with real third-party consumers).
- Renovate/dependabot automation for the SearXNG pin.
- Making the baked searxng config carry Poppy's relaxations (spec 6's overlay owns those).

## Assumptions

- GitHub-hosted runners suffice (CPU torch, mocked model in tests).
- `WashingBearLabs` allows GHCR public packages; a PAT with branch-protection admin +
  `delete:packages` is available for US-007/US-008's human steps.
- First service `v1.0.0` waits for spec 5's freeze; pre-freeze tags are `0.x`/`-rc` and
  never move `latest`… `latest` starts existing at `v1.0.0`.

## Technical Considerations

- Story order (frontmatter `execution_order`) is job-dependency order: lint → typecheck →
  test → build/grep → smoke → publish+checks → searxng lane → flip. No story writes a
  `needs:` referencing a job a later story creates.
- The repo/packages stay private until US-008.

## Related Documentation

- Epic (this repo): [epic-forage-extraction-forage-side.md](epic-forage-extraction-forage-side.md)
- Planned in Poppy (canonical planning record, one-way sync — see the wrapper's Notes):
  [`epic-forage-extraction.md`](https://github.com/WashingBearLabs/Poppy/blob/main/kit_tools/specs/epic-forage-extraction.md) ·
  [`WEB_ACCESS_FAMILY.md`](https://github.com/WashingBearLabs/Poppy/blob/main/kit_tools/specs/WEB_ACCESS_FAMILY.md)

## Implementation Notes

- **US-006 PASS 2026-09-07** (verifier: pass-with-warnings, 6/6; all figures re-derived,
  suppression-scoping proven with planted errors). f673fab+9376e33: pyright strict 0/0/0
  with enableTypeIgnoreComments=false (real backlog 269, incl. 55 hidden behind 39
  inherited suppressions — census corrected from 30 by verifier); minimal transformers
  stub, no torch stub; tests-lane reportPrivateUsage only; typecheck lane green on GitHub;
  suite 586 delta-lossless. Revision rotated cd00a8b4…→0537316d… (2 sources; the
  stage1_extraction change verified behavior-identical over 630 docs — recorded as
  defensive hardening, NOT a bug fix, per verifier; branch accepted untested). Honesty
  corrections applied by supervisor pre-commit.

- **US-001 PASS 2026-09-07** (verifier: pass-with-warnings, 8/8 re-measured incl. own
  mutations + SHA-pin resolution + cold-cache log fetch). Commits 8c652ad+5c8d8f2: ci.yml
  lint lane (25s cold/18s warm, both runs success), format backlog 6→0, 33 guard tests
  (567 total), CPU-lock asserted. sanitizer_revision rotated format-only (stage2_structural
  reformat): 2b8d7e9a… → cd00a8b4…, ast-identical, propagated. Rider to US-006: refresh the
  Forage wrapper's stale snapshot lines (old revision value, '6 format files', '534').

### US-001 — CI workflow, ruff + format gates, CPU-locked dependencies (2026-09-07)

**Shipped:** `.github/workflows/ci.yml` (one file, `lint` job only — no `needs:` anywhere,
so nothing forward-references a job US-002/003/005/006/007 will add),
`tests/test_ci_workflow.py` (30 tests), `tests/test_dependency_lock.py` (3 tests).
Commit `8c652ad`.

**First CI run — green, cold cache:**
<https://github.com/WashingBearLabs/Forage/actions/runs/34169334923> · conclusion
`success` · `lint` in **25 s** · run triggered by the push of `8c652ad` to `main`.

**Cold-cache CI log inspection (AC 3).** The run above is the cold one — `Install uv`
logged `No GitHub Actions cache found for key: setup-uv-2-x86_64-unknown-linux-gnu-
ubuntu-24.04-3.12.3-daaa55d7…`, so every wheel was fetched from the network, and the
`Post Install uv` step saved the cache afterwards. Evidence from that job's log:

| Signal | Value |
|---|---|
| `Downloading nvidia*` lines | **0** (the only `nvidia` strings in the whole log are the guard scripts' own `grep` commands) |
| torch download | `Downloading torch (187.2MiB)` — one wheel, no CUDA runtime packages |
| Package count | `Prepared 75 packages` / `Installed 75 packages` (the CUDA resolution adds 15 more) |
| Installed torch build | `torch 2.14.0+cpu` |
| Guard steps | `uv.lock is CPU-only: no nvidia-* wheels.` / `No nvidia-* packages in the synced environment.` |

The grep of the **committed lock** is the durable assertion (a warm cache would make
log-reading vacuous); the environment check is the second, complementary one — it proves
the lock is what CI actually installs, and it is the step that produces the log evidence
above. Both are also asserted locally by `tests/test_dependency_lock.py`.

**`ruff format --check` backlog burned to zero.** Six files, matching spec 1's final
recorded figure: `pipeline/stage2_structural.py`, `pipeline/stage5_url_audit.py`,
`url_validator.py`, `tests/test_stage2_structural.py`, `tests/test_stage5_url_audit.py`,
`tests/test_url_validator.py`. `ruff check` was already clean and stayed clean. No
excludes, no `# fmt: off`.

**⚠️ `sanitizer_revision` rotated — format-only, deliberate, once.**
`pipeline/stage2_structural.py` is one of the eight `_REVISION_SOURCES`, so reformatting
it moved the hash:

```
before: 2b8d7e9afd28e6a90dc8eb9c7408bf84294f6c74c26b972a7d25e2a9f65f1d6e
after:  cd00a8b456990c01529fbaf230d9c1c1a14b0a8aadbe2f9839b6f8f468c96b9a
```

(`derive_sanitizer_revision({"promptguard_threshold": 0.85})`; `config.yaml` yields the
same value.) `pipeline/stage5_url_audit.py`, the other reformatted pipeline file, is *not*
a revision source. **No sanitization behaviour changed** — the hash is over bytes. Taking
the rotation here, at gate installation, is the cheap moment: it happens once, under a
recorded before/after, instead of surfacing later inside a behavioural change where it
would be indistinguishable from a real sanitizer edit. Propagated to `CLAUDE.md`,
`kit_tools/arch/CODE_ARCH.md`, `kit_tools/docs/GOTCHAS.md` and
`docs/bootstrap-notes.md` (which now carries all three values in one table). Nothing
asserts the literal in code — only prose referenced it.

**Guard suite — ported, not reinvented.** A trimmed adaptation of Poppy's
`tests/deployment/test_ci_workflow.py`, keeping the assertions actionlint cannot make:
SHA-pinning (plus a `# vX.Y.Z` comment on every pin), top-level read-only permissions,
`persist-credentials: false` on every checkout, and trigger shape (both `v*` **and**
`searxng-v*`). Dropped as Poppy-specific: self-hosted `runs-on`, the draft-skip condition,
sibling-checkout layout, the lint ratchet, and the change-detection job. Added for Forage:
a `pull_request_target` ban across every workflow file, a repository-secret allowlist
(`GITHUB_TOKEN` only), and a **job-graph integrity test** — no `needs:` may name an
undefined job — which is the mechanical form of this spec's no-forward-`needs:` rule and
guards every remaining story. Each guard was verified to *bite* by mutating `ci.yml` five
ways (dropped `searxng-v*`, unpinned action, added `pull_request_target` + a repo secret +
top-level write, forward `needs: [typecheck]`, dropped `persist-credentials` and the
format gate) and confirming exactly the expected failures before restoring.

**Fork posture, as corrected in round 3.** No fork guard was ported. Fork PRs run the
gates as ordinary `pull_request` events on GitHub-hosted runners with a read-only
`GITHUB_TOKEN` and no secrets; the encoded rules are the `pull_request_target` ban and the
secret allowlist. Poppy's `head.repo.full_name == github.repository` guard is a
*self-hosted-runner* mitigation and is inapplicable here.

**actionlint** is pinned to **1.7.12** with a `sha256sum -c` check on the release tarball
(digest `8aca8db9…a3d8` in the workflow's `env:`) — a version pin over an unverified
download is not a pin. Verified locally at the same version before pushing (`actionlint`
exit 0), and green in CI. A guard test asserts the version is exact and the digest is a
real sha256.

**Deliberately NOT done:** required status checks are not registered — US-007 does that
once all jobs exist, because a required check with no reporting job deadlocks every PR.

**Notes for the following stories:**
- `concurrency.cancel-in-progress` is `${{ github.event_name == 'pull_request' }}` — PR
  pushes collapse, main and **tag** runs never cancel. Do not simplify it to `true`: a
  cancelled tag run is a cancelled publish gate chain.
- `uv sync --extra dev --locked` is the environment step; `--locked` makes a
  `pyproject.toml` edit that skipped re-locking fail CI. Reuse it verbatim in the
  `typecheck` and `test` jobs.
- Cold `lint` is 25 s wall clock, ~185 MB of downloads dominated by the torch wheel. With
  the uv cache warm it will be far less. Budget accordingly on the free tier.
- Suite went 534 → **567** tests, all green. `kit_tools/testing/TESTING_GUIDE.md` carries
  the new counts and the three non-Python `test_mapping` entries
  (`.github/workflows/ci.yml`, `uv.lock`, `pyproject.toml`).

### US-006 — Pyright-strict burn-down + tests-lane policy (2026-09-07)

**Shipped:** `pyproject.toml` (`[tool.pyright]` policy + two execution environments),
`typings/` (`README.md` + `transformers/__init__.pyi`), `.github/workflows/ci.yml`
(`typecheck` job), `tests/test_pyright_policy.py` (12 tests), 7 new guards in
`tests/test_ci_workflow.py`, and real type fixes across 4 service modules and 11 test
modules. Commit `f673fab`.

**CI run — green:** <https://github.com/WashingBearLabs/Forage/actions/runs/34171246831>
· conclusion `success` · `typecheck` **29 s**, `lint` **22 s**, started in parallel · the
`pyright (strict)` step logged `0 errors, 0 warnings, 0 informations` on `ubuntu-latest`,
which is a real cross-check: the CPU-torch index is `sys_platform == 'linux'`-gated, so a
macOS-only zero would not have proved the Linux resolution.

**Fresh backlog measurement (AC 1).** Taken after `uv sync --extra dev --locked` in the
real environment, on the locked pyright 1.1.411 — and taken *twice*, because the first
number is not the honest one:

| Measurement | Total | Service | Tests |
|---|---:|---:|---:|
| Default config (pyright honours type-ignore comments) | **214** | 22 (4 files) | 192 (incl. 35 `reportPrivateUsage`) |
| With `enableTypeIgnoreComments = false` | **269** | 57 | 212 |

214 reproduces spec 1's hand-over figure exactly, so nothing had moved under US-001's
format pass. But the repo carried **39 inherited `# type: ignore` comments** (census corrected by the US-006 verifier), and pyright
honours them by default with no rule code required — one comment silences every diagnostic
on its line. 55 errors were hiding behind them. Reporting 214 as "the backlog" and then
declaring zero would have been a fiction, so the switch was turned off and the real 269
paid down. Every one of the 30 comments is gone; four turned out to be stale (they
suppressed nothing).

**Service code: strict, fixed, not silenced.**

- `promptguard/classifier.py` (24 errors) — `transformers`' auto-class factories are
  annotated as returning `Unknown`, which propagated through tokenising, the forward pass
  and `outputs.logits`. Fixed with `typings/transformers/__init__.pyi`: four symbols,
  narrowest true signatures, `TYPE_CHECKING`-only imports so the runtime stays lazy.
  **torch gets no stub** — it ships complete types and the classifier now consumes them
  (`torch.no_grad`, `torch.softmax`, `Tensor.item()` wrapped in `float()`), exactly as
  round 2 asked.
- `cache.py` (5) — `redis.asyncio.Redis` declares its commands through `**kwargs: Any`. A
  `_ValkeyClient` Protocol names the five operations Forage issues, the connection is cast
  to it once where it is created, and `_ensure_client()` now returns the client instead of
  a bool so each call site is narrowed rather than a possible `None` access.
- `pipeline/stage1_extraction.py` (26) — the optional-import dance became
  `try: import trafilatura / except ImportError: trafilatura = None`, which pyright
  narrows at the call site (a separate `_HAS_*` bool carries no such correlation). The
  `<meta>` and JSON-LD strategies moved behind `_attr_text()` and `_json_ld_documents()`.
- `pipeline/stage2_structural.py`, `pipeline/stage5_url_audit.py` (1 each) —
  `field(default_factory=list)` → `list[FlaggedSpan]` / `list[str]`.

**Defensive hardening the strict pass surfaced** (verifier downgraded from "real bug": not reproducible — no call site reads a multi-valued attribute; 630-doc differential test showed zero behavior change; branch accepted untested as defensive). `tag[name].strip()` assumed every HTML
attribute is a `str`; bs4 returns an `AttributeValueList` for multi-valued attributes
(`class`, `rel`), which has no `.strip()` and would raise `AttributeError` out of author
and date extraction. `_attr_text()` now falls through to the next strategy, which is what
the priority-ordered list already meant. Everything else in that refactor is
behaviour-preserving — verified by the suite, and by re-confirming that bs4 4.15 gives
empty `Tag`s a truthy `__bool__` (on older bs4, `if meta and …` would have been dead code
via `__len__`, and the rewrite would have been a behaviour *change*).

**Tests lane: one rule, and only one.** `[[tool.pyright.executionEnvironments]]` with
`root = "tests"` disables `reportPrivateUsage` and nothing else; a second entry with
`root = "."` covers everything else with no overrides at all. Two notes for later stories:

- The tests environment needs `extraPaths = ["."]`. An execution environment *replaces*
  the default import root, and Forage's modules are flat at the repo root — without it,
  every first-party import in the suite resolves as a stub-less third-party library and
  the error count goes *up* by 62 `reportMissingTypeStubs`.
- Six tests deliberately do what the type system forbids (assign to a frozen dataclass,
  pass a value outside a `Literal`). Those route through `tests.fakes.assert_frozen` and a
  `dict[str, Any]` splat rather than a suppression, so the runtime assertion still runs.
  `kit_tools/docs/CONVENTIONS.md` now carries this as a table: which situation gets which
  escape, and that "anything else" gets none.

**Guards, mutation-verified.** `tests/test_pyright_policy.py` pins the whole policy —
strict mode, no top-level rule overrides, type-ignore comments disabled, no inline
suppression anywhere in the repo's `.py`/`.pyi`, exactly one relaxation scoped to `tests/`,
`stubPath`, and no torch stub. Seven new `test_ci_workflow.py` guards pin the `typecheck`
job, including one that fails if `uv run pyright` ever gains a path argument (which would
silently narrow the checked surface). Six mutations were applied and confirmed to fail
before restoring: dropped `typecheck` job (7 failures), narrowed pyright scope, a second
tests relaxation, `enableTypeIgnoreComments = true`, a top-level rule override, and a
re-introduced inline suppression (1 failure each).

**⚠️ `sanitizer_revision` rotated — second rotation of this spec, behaviour-preserving.**
Two `_REVISION_SOURCES` members were touched (`stage1_extraction.py`,
`stage2_structural.py`):

```
before: cd00a8b456990c01529fbaf230d9c1c1a14b0a8aadbe2f9839b6f8f468c96b9a
after:  0537316d83510dab3cfafb6ebd61dafdffa5d51be2dd2e01fb9777bfd0e3e253
```

(`derive_sanitizer_revision({"promptguard_threshold": 0.85})`; `config.yaml` yields the
same value.) Unlike US-001's, this one is **not** ast-identical — `stage1_extraction.py`
took the helper refactor above. It is still not a sanitizer change: the extraction
strategies, their priority order and their outputs are unchanged bar the
`AttributeValueList` fix. Taken here for the same reason as last time — at a gate
boundary, once, under a recorded before/after. Propagated to `CLAUDE.md`,
`kit_tools/arch/CODE_ARCH.md`, `kit_tools/docs/GOTCHAS.md` (now a four-row table) and
`docs/bootstrap-notes.md`. Five of the eight sources remain byte-identical to Poppy's.

**Job shape.** `typecheck` is a **sibling** of `lint`, not a successor: neither reads the
other's output, so a `needs:` edge would only serialise two ~25 s jobs and delay the second
failure report. The `needs:` edges that matter are US-007's, hanging the publish off both.

**Notes for the following stories:**
- The suite is **not gated by CI yet** — there is no `test` job until US-002, so
  `test_pyright_policy.py` and `test_ci_workflow.py` currently bite only locally and at
  review. US-002 closes that, and it is the story that makes every guard in this spec real.
- Reuse `uv sync --extra dev --locked` verbatim (US-001's note still stands). `uv run
  pyright` also pulls `nodeenv` and a Node runtime on first run inside the job; that is
  included in the 29 s and is cached with the uv cache.
- Suite went 567 → **586** tests, all green. `kit_tools/testing/TESTING_GUIDE.md` carries
  the new counts, the `tests/test_pyright_policy.py` row, and two new `test_mapping`
  entries (`pyproject.toml` now maps to two modules; `typings/*` is new).
- **Rider from US-001, discharged:** `kit_tools/specs/epic-forage-extraction-forage-side.md`
  now carries the current revision (`0537316d…`), a zero backlog with the 214/269
  correction, and 586 collected — with the "compare contracts, not revisions" instruction
  kept intact.

### US-002 — Test lane (2026-09-07)

**Shipped:** `.github/workflows/ci.yml` (`test` job), `tests/test_hermeticity.py` (8
tests, new), 13 new guards in `tests/test_ci_workflow.py` (37 → 50), and the doc
propagation. Commit `5bea1b8`.

**CI run — green:** <https://github.com/WashingBearLabs/Forage/actions/runs/34172572962>
· conclusion `success` · `test` **31 s** (`607 passed, 10 warnings in 10.33s`), `lint`
**17 s**, `typecheck` **29 s**, all three started within a second of each other. The named
`Verify sanitizer revision derivation` step reported its own `6 passed in 0.03s` above the
full run.

**This is the story that makes every guard in the repo CI-enforced.** Until this job
existed, nothing in CI ran pytest at all: `test_ci_workflow.py`'s supply-chain assertions,
`test_pyright_policy.py`'s policy pins, `test_dependency_lock.py`'s CPU-only lock check and
the hermeticity guard all bit on a developer's machine and at review, and nowhere else. A
PR could have deleted the socket guard, unpinned an action or relaxed a pyright rule and
still shown two green checks. US-006's "not gated by CI yet" note is discharged here; the
only remaining gap is that these checks are not yet *required* for merge, which US-007
registers once all six jobs exist.

**Job shape.** `test` is a third **sibling** of `lint` and `typecheck` — no `needs:`, for
the same reason US-006 gave: none reads another's output, so an edge would only serialise
three sub-30 s jobs and delay the second and third failure reports. `timeout-minutes: 20`
is a runaway backstop, not a budget (the suite is 3 s locally, 10 s on the runner; the cold
cost is the ~185 MB torch wheel, not pytest).

**Step order is deliberate.** The named sanitizer-revision step runs *before* the full
suite, not after Poppy's placement in a separate job. Poppy can afford a separate job; here
the same job runs both, and after the full suite an unrelated failure anywhere in 607 tests
would stop the contract guard from reporting at all. Running it first costs 0.03 s and buys
a distinct red line for the file-recall cache contract. A guard test pins the ordering.

**The canary runs and passes — round 3's prose fix, honoured.** `tests/test_hermeticity.py`
is an executing test module, never a skipped one. Ported from Poppy's
`tests/test_conftest_socket_isolation.py` and trimmed to what is true here: Poppy's
`@pytest.mark.integration` re-enable lane and its `allow_hosts(['127.0.0.1'])` postgres
exemption **do not exist in Forage**, so copying those two tests would have asserted
fixtures this repo does not have. What replaced them are holes the original left open on a
guard that also patches DNS:

| Assertion | Hole it closes |
|---|---|
| TCP / UDP / IPv6 construction blocked | the exemption is family-scoped (`AF_UNIX`), not protocol-scoped |
| `getaddrinfo` and `gethostbyname` blocked | name resolution is what the three real cache-test leaks actually did; a socket-only canary would pass while DNS escaped |
| `create_connection` to loopback blocked | the API client code actually calls, and proof this suite has no local-service exemption to grow into one silently |
| `AF_UNIX` socketpair works; an async test runs | a guard that took the exemption away would take the whole async half of the suite with it |

Verified on Linux as well as macOS by the CI run above — the event-loop self-pipe assertion
is platform-sensitive and a macOS-only green would not have proved it.

**A canary cannot notice its own module being skipped**, so
`TestHermeticityCanaryIsEnforced` lives in `tests/test_ci_workflow.py` instead: the canary
exists, asserts `SocketBlockedError`, carries no skip/xfail markers, and `conftest.py` still
declares `autouse=True` + `disable_socket(allow_unix_socket=True)`. Cross-module on purpose
— a `pytestmark = pytest.mark.skip` on the canary still turns the suite red.

**Guards, mutation-verified.** Eleven mutations applied, each confirmed to fail exactly the
expected tests before restoring (script kept out of the tree):

| Mutation | Failures |
|---|---|
| `test` job deleted | 9 (all of `TestTestJob`) |
| full suite narrowed to `pytest -q tests/` | 2 |
| `pytest -q -x --maxfail=1` | 3 |
| sanitizer-revision step removed | 2 |
| sanitizer step renamed to something meaningless | 1 |
| sanitizer step moved after the full suite | 1 |
| `--locked` dropped from the test job's sync | 1 |
| `enable-cache: false` in the test job | 1 |
| canary module `pytestmark`-skipped | 1 (and 8 skipped, which is the tell) |
| canary module deleted | 3 |
| `autouse` removed from the socket fixture | 8 — 1 structural + **all 7 canary behaviour tests**, which is the canary earning its place |

The narrowing guard rejects `-k`, `-m`, `-x`, `--exitfirst`, `--maxfail`, `--ignore`,
`--ignore-glob`, `--deselect`, `--lf`, `--last-failed`, `--sw`, `--stepwise` on any pytest
invocation in the job, and separately requires a bare `uv run pytest -q` line. A green
`test` job is the evidence every other guard rests on; the one thing it must never do is
quietly check a subset.

**`sanitizer_revision` did NOT rotate.** `0537316d83510dab…e3e253` before and after — the
first story in this spec to leave it alone, because nothing here touches a
`_REVISION_SOURCES` file. Recorded because the previous two stories each moved it and
silence would be ambiguous.

**Warning noise is pre-existing and intentional.** The canary adds 7 `UserWarning: A test
tried to use socket.*` lines to the summary (10 total). `pytest-socket` emits that warning
alongside the exception so a swallowed error still surfaces; `tests/test_cache.py` already
produced 3 of them. Filtering them in the canary only would have invented a new convention
for a cosmetic gain, so they stand.

**Notes for the following stories:**
- The `test` job is a required-check candidate for US-007 alongside `lint` and `typecheck`
  — it is the one whose absence from that list would matter most.
- `TestJobGraph::test_no_needs_references_an_undefined_job` still passes with three jobs and
  no edges; US-003's `secret-grep` is the first real `needs:` and the first exercise of it.
- Reuse `uv sync --extra dev --locked` verbatim (US-001/US-006's note stands, now three for
  three).
- Suite went 586 → **607** tests (+8 canary, +13 workflow guards), all green.
  `kit_tools/testing/TESTING_GUIDE.md` carries the new counts, the
  `tests/test_hermeticity.py` row, and a new `test_mapping` entry
  (`tests/conftest.py` → `tests/test_hermeticity.py`).
- `kit_tools/docs/GOTCHAS.md`'s hermeticity entry said removing the guard "would not show
  up as a failure". That is now false and was corrected in the same commit rather than left
  to rot — the claim was true only until this story landed.

### US-003 — Secret-free service image build + secret-grep gate (2026-09-07)

**Shipped:** `Dockerfile` (reworked), `.github/workflows/ci.yml` (`build-amd64` +
`secret-grep`), `tests/test_dockerfile.py` (20 tests, new), 29 new guards in
`tests/test_ci_workflow.py` (50 → 79). Commit `32df2d0`.

**CI run — green, all five jobs:**
<https://github.com/WashingBearLabs/Forage/actions/runs/34174140579> · conclusion
`success` · `lint` 21 s, `typecheck` 31 s, `test` 27 s, **`build-amd64` 3 m 00 s**
(cold cache — 2 m 06 s of it the build itself), **`secret-grep` 37 s**. The docs commit's
run (<https://github.com/WashingBearLabs/Forage/actions/runs/34174487496>) is the warm-cache
confirmation: same five jobs green, `build-amd64` down to 1 m 15 s on 12 cached layers.

**The handoff worked end to end, and the log says so rather than implying it:**

```
build-amd64  Built  forage:ci = sha256:9208d33abf5ea227cb6f163449752a211def142e8a934a232c028636dc774751
build-amd64  Builder and daemon agree on the image ID.
secret-grep  Loaded forage:ci = sha256:9208d33a…4751 (identical to build-amd64's)
secret-grep  No forbidden pattern in the layer history of forage:ci.
```

**What was deleted.** `ARG HF_TOKEN` and the conditional `from_pretrained` bake block are
gone entirely — not guarded, not defaulted-to-empty, gone. A build argument is not a
secret: BuildKit records the instruction verbatim in the finished image's layer history,
`docker history --no-trunc` reads it back, and the image carries it to every registry it
reaches. Deleting the downloaded file afterwards does nothing. That was *the* blocker on
publishing a Forage image and the reason this repo is private.

**What replaced the install.** `uv sync --locked --no-dev --no-install-project`, from the
committed lock, with uv itself digest-pinned (`ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65…`)
at the same version `ci.yml`'s `UV_VERSION` installs. The previous form parsed
`pyproject.toml` with a `tomllib` shell one-liner and pip-installed the unpinned ranges
plus a second `pip install torch --index-url …/cpu` — a different dependency set from the
one CI lints, type-checks and tests. Three flags carry weight and are commented in place:
`--no-install-project` (Forage's modules are flat at `/app` and imported from the working
directory, exactly as before — installing the project as a wheel too would ship a second
copy of every module), and `UV_PYTHON_DOWNLOADS=never` + `UV_PYTHON_PREFERENCE=only-system`
(uv's default preference is a *managed* interpreter, so without these the image would
download a second 3.12 and run one Python while CI type-checked another). The `UV_*` vars
are set on the `RUN` rather than as `ENV` so nothing leaks into the runtime environment.

**Base pin, and a syntax finding.** `FROM python:3.12-slim@sha256:78387bc3…184ea`,
resolved 2026-09-07 to **3.12.14-slim-trixie**; the index digest covers `linux/amd64` and
`linux/arm64/v8`, so US-007's multi-arch ambition is not foreclosed. The spec's hint spells
the pin `FROM …@sha256:<digest> # 3.12.x`, and **that form does not parse** — Dockerfile
has no inline comments, a `#` after an instruction's arguments is another argument, and
`FROM` rejects it (`FROM requires either one or three arguments`, verified by building
it). The version therefore lives on the comment line directly above, and
`test_base_version_recorded_in_an_adjacent_comment` keeps it there — a bare 64-hex digest
with no record of what it is cannot be maintained.

**Single-stage, deliberately.** A multi-stage build would have kept uv out of the shipped
image, but `docker history` reports only the *final* stage's layers, so it would silently
narrow what `secret-grep` can see to the last stage alone. Trading ~30 MB on a 348 MB
image for a weaker gate is a bad trade when the gate is the point of the story.
`test_single_from_instruction` pins it, with that reasoning in the failure message.

**Image handoff: one artifact, asserted identity, never a rebuild.** Round 3's critical was
right — `needs:` is an ordering edge, not a shared Docker daemon. `build-amd64` ends with
`docker save | gzip -1` plus the image ID in `image-id.txt`; `secret-grep` downloads both,
loads, and compares. `docker save`/`docker load` preserve the image ID exactly (it is the
digest of the image config), which is what makes the comparison meaningful.

One honesty note on the cross-check: `build-push-action`'s own `imageid` output is logged
beside the daemon's `.Id` but a **disagreement is a notice, not a failure**. The two
legitimately differ by storage backend — the action reports the image *config* digest while
a daemon on the containerd image store reports the manifest-list digest for the same image
(reproduced locally on Docker Desktop: `71ada4f2…` vs `f4344483…`). Failing on that would
be failing on a storage detail. On the runner they agreed. The check that is hard is the
daemon-to-daemon one in `secret-grep`: same field, same command, both sides.

**Grep scope, stated in the job rather than assumed.** `docker history --no-trunc` reports
layer metadata — the instruction that created each layer — and does not read file
contents. It therefore catches a `--build-arg`, an `ENV`, or a `RUN`-line assignment, and
does not catch a secret written into a file. The job comment says so at length, because a
green tick that reads as "filesystem scanned" is worse than no tick. The content-side
guards are named there: the deleted path, `tests/test_dockerfile.py`, and spec 1's
full-history `gitleaks` scan re-run before US-008. Pattern set, defined in the workflow and
not in a checked-out script: `HF_TOKEN` and `hf_[A-Za-z0-9]{20,}`.

**The gate was proven to bite, not just to pass.** A throwaway canary image built with
`--build-arg HF_TOKEN="hf_FAKE…"` (synthetic; no real token exists anywhere in this work)
matched **both** patterns out of its layer history, while the real image matched neither.
A gate only ever verified green is a gate nobody has tested.

**Guards, mutation-verified — 25 mutations, each confirmed to fail exactly the expected
tests before restoring.**

| Mutation | Failures |
|---|---|
| Dockerfile: re-add `ARG HF_TOKEN` | 3 |
| Dockerfile: re-add a `from_pretrained` bake step | 1 |
| Dockerfile: un-pin the base image | 1 |
| Dockerfile: drop the base-version comment | 1 |
| Dockerfile: drop `--locked` from `uv sync` | 1 |
| Dockerfile: un-pin the `COPY --from` uv image | 1 |
| Dockerfile: drop `ENV HF_HOME` | 1 |
| Dockerfile: drop the build-time import smoke | 1 |
| Dockerfile: bake a differently-*named* secret ENV (`HF_API_TOKEN`) | 2 |
| ci.yml: `build-amd64` pushes | 1 |
| ci.yml: `build-amd64` does not load the image | 1 |
| ci.yml: build targets the runner's default platform | 1 |
| ci.yml: `build-amd64` grants itself `packages: write` | 1 |
| ci.yml: drop the GHA build cache | 1 |
| ci.yml: upload warns instead of failing on an empty save | 1 |
| ci.yml: download names a different artifact | 1 |
| ci.yml: drop the token-shape grep pattern | 1 |
| ci.yml: remove the metadata-scope caveat | 1 |
| ci.yml: identity mismatch warns instead of failing | 1 |
| ci.yml: delete the identity comparison entirely | 1 |
| ci.yml: `secret-grep` rebuilds instead of loading | 1 |
| ci.yml: `secret-grep` checks out the repo | 1 |
| ci.yml: forward `needs:` to a job US-007 has not added | 1 (`TestJobGraph`) |
| ci.yml: delete the `secret-grep` job | 15 |
| ci.yml: delete the `build-amd64` job | 16 |

**One mutation escaped on the first pass, and that is the useful finding.** The
identity-assert guard originally checked `"exit 1" in run_text`. Downgrading the mismatch
branch from `::error` + `exit 1` to a bare `::warning` left the job's *other* `exit 1`
(the empty-ID-file guard) in place, so the coarse check stayed green while the assertion
the whole handoff rests on had become decorative — **98 passed**. The guard now locates
the specific conditional (`_if_block_body`) and asserts `exit 1` inside *that* branch.
The lesson generalises: a substring check over a whole script tests the script's
vocabulary, not its control flow. It is recorded in the test's own comment so the next
person does not re-loosen it.

**`sanitizer_revision` did NOT rotate.** `0537316d83510dab…e3e253` before and after,
measured both times — the second story running in a row to leave it alone. None of the
eight `_REVISION_SOURCES` files (all under `pipeline/`) is touched by a Dockerfile or
workflow change. Recorded because two of this spec's stories did move it and silence
would be ambiguous; `docs/bootstrap-notes.md` now says "no fourth rotation" explicitly
rather than just not mentioning one.

**Measurements worth carrying forward.**

| Thing | Value |
|---|---|
| Image size (amd64) | 348 MB — CPU torch, no weights, no CUDA |
| `docker save \| gzip -1` on the runner | 375 MiB; artifact **392,867,329 bytes** |
| Cold `build-amd64` | 3 m 00 s total, 2 m 06 s of build |
| Warm `build-amd64` (2nd run, 12 `CACHED` layers) | **1 m 15 s** |
| `secret-grep` | 37 s cold / 34 s warm (≈6 s download, ≈28 s `docker load`) |
| Artifact retention | 1 day (the minimum) |

The warm number is the evidence that `cache-from`/`cache-to: type=gha,mode=max` actually
works rather than merely being configured: the second run logged 12 `CACHED` layers and
came in at 42 % of the cold time. The residual minute is `docker save` + upload, which no
cache helps.

**Free-tier storage: a cost to watch, not the hard failure I first wrote down.** GitHub
Free nominally gives the org **500 MB** of shared Actions/Packages storage and one run's
image artifact is **375 MiB** of it, so I expected the second same-day run to be refused.
It was not: after two runs the repository held **785,840,829 bytes** of live artifacts
(2 × `forage-amd64-image` + 2 small `*.dockerbuild` records) and both uploads succeeded.
So the quota is not enforced as an upload block here — it is billed/soft, or the
accounting lags. The measured facts, not the prediction: 375 MiB per run, 1-day retention,
two concurrent copies fine. It is still worth US-005/US-007 adding a final job that deletes
`forage-amd64-image` once `smoke` and `publish` have consumed it, on cost grounds rather
than on a predicted red build. Note also that `build-push-action` uploads its own
`*.dockerbuild` build record (42–64 KB, **90-day** retention) on every run; small and
useful for debugging failed builds, so it was left on, but it is not free either.

**Notes for the following stories:**
- **US-005's `smoke` reuses this exact artifact** — `download-artifact` with
  `name: ${{ env.IMAGE_ARTIFACT }}`, `gunzip -c "${IMAGE_TARBALL}" | docker load`, then the
  same `.Id`-vs-`image-id.txt` comparison. Copy the assert; do not re-derive it, and do not
  add a second artifact.
- `provenance: false` on the build step is load-bearing, not tidiness: the docker exporter
  `load: true` uses cannot carry attestations.
- The four action pins added here: `docker/setup-buildx-action` v4.3.0,
  `docker/build-push-action` v7.3.0, `actions/upload-artifact` v7.0.1,
  `actions/download-artifact` v8.0.1. The upload/download major numbers genuinely differ —
  they are separately versioned repositories, both on the v4+ artifact backend.
- `secret-grep` deliberately has **no checkout**. The pattern set lives in the workflow so
  the gate cannot be weakened by editing a script the job fetches, and a guard test pins
  the absence.
- `build-amd64` is a *sibling* of `lint`/`typecheck`/`test`, not a successor: a broken
  image and a broken lint are independent failures and one run should report both.
  US-007's `publish` is what requires all of them green at once.
- **Harness lesson (self-inflicted, cost ~20 minutes):** the mutation script restored files
  with `git checkout --`, which reverted *uncommitted* work — the whole Dockerfile and both
  new jobs — because the implementation had not been committed yet. Commit first, mutate
  second. The untracked test module survived, which made the loss quieter than it should
  have been.
- Suite went 607 → **656** tests (+20 `test_dockerfile.py`, +29 workflow guards), all
  green. `kit_tools/testing/TESTING_GUIDE.md` carries the new counts and a new
  `test_mapping` entry (`Dockerfile` → `tests/test_dockerfile.py`).
- `kit_tools/docs/GOTCHAS.md`'s build-arg entry moved to **Historical / Closed** — with
  both carve-outs written into the entry rather than left implied: closure is not
  permission to push (US-007/US-008 still gate that), and Poppy's in-tree
  `services/retrieval/Dockerfile` still carries `ARG HF_TOKEN` until spec 6. The
  PromptGuard-degraded entry was corrected in the same pass: "a token-less build produces
  an image with no weights" is now "*every* image is weights-free until spec 3".

### US-005 — Published-image contract smoke job (2026-09-07)

**Shipped:** `.github/workflows/ci.yml` (`smoke` job), `contract_smoke.py` (367 lines,
new, repo root), `tests/test_contract_smoke.py` (47 tests, new), 28 new guards in
`tests/test_ci_workflow.py` (79 → 107), and a two-line change to `retrieval_app.py`.
Commit `d88ba70`.

**CI run — green, all six jobs:**
<https://github.com/WashingBearLabs/Forage/actions/runs/34176322730> · conclusion
`success` · `lint` 21 s, `typecheck` 30 s, `test` 27 s, `build-amd64` 1 m 43 s (warm),
`secret-grep` 47 s, **`smoke` 1 m 10 s**. This is the first run in which CI *executes*
the image it builds rather than inspecting it.

**What the smoke job's log says, rather than implies:**

```
smoke  Loaded forage:ci = sha256:ad349b75…0996 (identical to build-amd64's)
smoke  forage-smoke Up Less than a second
smoke  Expecting contract_version 1.0.0 (pipeline/contract.py)
smoke  /health answered 200 after 4 attempt(s)
smoke  {"status":"degraded","promptguard_loaded":false,"cache_connected":false,
        "capabilities":{},"sanitizer_revision":"0537316d…e3e253",
        "contract_version":"1.0.0","degraded_reasons":["promptguard_unavailable",
        "cache_unavailable"]}
smoke  Contract smoke PASSED: degraded, honest, and on-contract.
```

**The field expectations are not in bash, and that is the story.** The AC asks for the
smoke's expectations to share a source with the golden-schema machinery. What that rules
out is the obvious implementation — `curl | jq -e '.status == "degraded"'` and a list of
field names in the workflow — because that list is a *second* copy of the contract, free
to keep passing after the first one moves. `contract_smoke.py` instead:

* validates the live body with `HealthResponse.model_validate` and compares the payload's
  key set against `HealthResponse.model_fields` — the same model object
  `tests/test_contract_schema.py` pins against `tests/golden/contract_1_0_0.json`, so a
  wire-shape change that skipped a `CONTRACT_VERSION` bump fails the golden test and the
  smoke together, and an *added* field fails the smoke even before the golden file is
  regenerated;
* imports `CONTRACT_VERSION` and `DEGRADED_PROMPTGUARD_UNAVAILABLE` from
  `pipeline/contract.py` at job time (the job checks the repo out precisely so it can);
* imports the capability key from `retrieval_app`.

`TestSingleSourceOfTruth` makes all three checkable rather than conventional: the model
must be the *same object* (`is`) as the golden test's, the version must be the same object
as `pipeline.contract`'s, and an AST pass asserts that **no wire value appears as a string
literal in the smoke's code** — docstrings exempt, because prose naming
`promptguard_unavailable` is documentation while a literal is a contract. A parallel guard
(`test_smoke_does_not_enumerate_the_contract_in_bash`) applies the same rule to the
workflow's shell.

**One two-line service change, and why it was the right seam.** `capabilities` is typed
`dict[str, int]`, so the JSON schema — and therefore the golden fixture — cannot pin the
key `search_sanitization`; it existed only as a literal inside `health()`.
`retrieval_app.py` now names it once (`CAPABILITY_SEARCH_SANITIZATION`) and the smoke
imports it. The alternative was putting the string in `pipeline/contract.py`, which is a
`_REVISION_SOURCES` file — that would have rotated `sanitizer_revision` for a naming
change, which is exactly the drive-by rotation `GOTCHAS.md` warns against.
`tests/test_app.py` still spells the literal out in five places on purpose: the constant
single-sources the *symbol*, those tests pin the *value*, and a rename of either is caught.
The tie is also asserted behaviourally — `test_capability_key_is_the_one_health_advertises`
stands the real app up with a loaded classifier and reads back what it advertises, because
a constant that merely *looks* like the wire string is not evidence.

**Why the script sits at the repo root.** `CONVENTIONS.md` has two rules that between them
decide this: modules are flat at the root, and never a per-module `sys.path.insert`. A
`scripts/contract_smoke.py` would import nothing (`sys.path[0]` is the script's directory,
not the cwd) without either a path hack or a `-m` invocation. At the root it runs as
`uv run python contract_smoke.py` from any developer's checkout, which matters because
reproducing a red smoke by hand is the first thing anyone will want to do. It is a CI
utility, not part of the image: the Dockerfile's `COPY` list is explicit and does not
include it, and `.dockerignore` is irrelevant to that.

**It is deliberately not a pytest module.** The suite runs under an autouse
`pytest-socket` guard that blocks exactly the loopback request the smoke has to make.
Splitting it into an injectable fetcher plus pure evaluators is what let 47 tests cover
every clause without touching a socket — and it is also what let the *timeout* path be
tested (a zero budget still makes one attempt, and the last response goes to the evaluator,
so "never came up" reports as a contract violation with the body attached rather than as a
second error path).

**The gate was proven to bite against a live container, not only in unit tests.** Two
negative rehearsals against the real amd64 image on the local daemon:

| Rehearsal | Result |
|---|---|
| stock image, no env | PASSED — `degraded`, `promptguard_unavailable`, `capabilities: {}`, revision `0537316d…` |
| same image, `FORAGE_LEGACY_CAPABILITY=1` armed | **FAILED**, exit 1, exactly one violation: `/health advertises 'search_sanitization' … while PromptGuard is unavailable` |
| nothing listening on the port, 3 s budget | **FAILED**, exit 1, two violations, no crash and no traceback |

The middle row is the useful one: the break-glass override makes a *degraded* service
advertise the capability anyway, which is the nearest thing this repo can produce to a
dishonest image, and the smoke isolates it to one line while every other clause still
passes.

**PromptGuard's refusal is fast, which is why 120 s is comfortable.** The weights-free
image reaches Hugging Face, gets `GatedRepoError` / HTTP 401 for
`meta-llama/Llama-Prompt-Guard-2-22M`, logs it and carries on. On the runner `/health`
answered on the **4th** poll (~8 s after `docker run`); locally, under qemu emulation on
arm64, on the 4th–5th. The budget is not close to binding, but it stays at 120 s: the
number that matters is a cold `torch` import on a loaded runner, not the warm case.

**Guards, mutation-verified — 28 mutations, each confirmed to fail exactly the expected
tests before restoring.** The implementation was committed *first* (US-003's harness
lesson: its mutation script's `git checkout --` restore ate 20 minutes of uncommitted
work).

| Mutation | Failures |
|---|---|
| ci.yml: the `smoke` job is renamed out of the graph | 26 |
| ci.yml: identity mismatch warns instead of failing | 1 |
| ci.yml: delete the identity comparison entirely | 1 |
| ci.yml: enumerate the contract in bash (`grep '"contract_version":"1.0.0"'`) | 1 |
| ci.yml: pass `-e HF_TOKEN=fake` into the container | 1 |
| ci.yml: drop `-p 8020:8020` | 1 |
| ci.yml: budget cut from 120 s to 30 s | 1 |
| ci.yml: log dump conditioned on `success()` | 1 |
| ci.yml: log-dump step deleted outright | 1 |
| ci.yml: cleanup no longer `always()` | 1 |
| ci.yml: smoke stops running the committed script | 1 |
| ci.yml: smoke rebuilds instead of loading the artifact | 3 |
| ci.yml: smoke downloads into the checked-out working tree | 1 |
| ci.yml: smoke downloads a different artifact name | 1 |
| ci.yml: smoke grants itself `packages: write` | 1 |
| ci.yml: smoke drops `--locked` | 1 |
| ci.yml: smoke uploads its own artifact | 2 |
| ci.yml: smoke forward-`needs:` a job US-007 has not added | 1 |
| contract_smoke: tolerate `status: "healthy"` | 3 |
| contract_smoke: hardcode the contract version | 1 |
| contract_smoke: drop the `promptguard_unavailable` check | 3 |
| contract_smoke: drop the capability check | 2 |
| contract_smoke: accept the `"unknown"` revision | 1 |
| contract_smoke: drop the unexpected-field check | 1 |
| contract_smoke: validate against a private subclass of the model | 2 |
| contract_smoke: drop the `/metrics` version check | 2 |
| contract_smoke: lower the default budget to 30 s | 2 |
| retrieval_app: rename the advertised capability key | 3 |

**One probe was bad and it is worth recording as a probe, not as an escape.** The first
attempt at "remove the on-failure log dump" inserted a second `if:` key into the same
step; PyYAML takes the last key, so `if: failure()` survived and the mutation changed
nothing — it reported a green suite and looked exactly like an escaped guard. Re-run
properly (replace the condition; then delete the whole step) it fails as designed, twice.
A mutation harness needs its mutations verified as much as the guards do.

**`sanitizer_revision` did NOT rotate.** `0537316d83510dab…e3e253` before and after,
measured both times — the third story in a row to leave it alone, and the first that can
*observe* it from outside: the smoke reads the value off a running container and fails on
an empty one or the `"unknown"` fallback. Nothing under `pipeline/` was touched;
`retrieval_app.py` is not a `_REVISION_SOURCES` member.

**Artifact pressure: measured, and deliberately not "fixed" here.** The carry-forward from
US-003 asked this story to reduce artifact pressure where cheap without foreclosing
US-007's consumption of the same artifact. What is actually available:

* `retention-days` is already **1**, which is upload-artifact's floor. That lever is spent.
* The smoke adds **zero** artifact bytes — it consumes the tarball and uploads nothing.
  `test_smoke_uploads_no_artifacts_of_its_own` and `test_there_is_exactly_one_image_artifact`
  keep it that way; the second is the one that stops a future consumer solving its problem
  with a second 375 MiB copy.
* A **delete-the-artifact job was considered and rejected.** It needs job-scoped
  `actions: write` — a new write-permission surface on a repository about to go public,
  which also grants cancelling runs and deleting caches — to reclaim storage against a
  quota US-003 measured as *not* enforced (two concurrent runs held 785 MB against a
  nominal 500 MB and both uploads succeeded). And the sequencing is a trap: a cleanup job
  gated on `[secret-grep, smoke]` today would delete the artifact before US-007's
  `publish` can read it, and nothing would fail until a release did.
* This bullet used to nominate US-007 as the right place for that cleanup job, on the
  grounds that `publish` is the last consumer and its `needs:` list is complete by
  construction. **US-007 landed and that turned out to be wrong**, so the claim is
  retired rather than annotated: `publish` is *conditional* — it is skipped on every
  pull_request event and on `searxng-v*` tags — so a cleanup job hung off it would never
  run for the majority of workflow runs, which are exactly the ones parking a 375 MiB
  tarball. The rejection stands on the reason above (a new `actions: write` surface for a
  quota that is not enforced), and the retention floor of 1 day stands as the whole of the
  mitigation. `_IMAGE_CONSUMERS` in `tests/test_ci_workflow.py` did gain `"publish"`, and
  every consumer is held to the same download-and-assert contract by parametrization.

**Where the smoke's 70 seconds go** (from the run above — worth knowing before anyone
tries to speed it up):

| Step | Time |
|---|---|
| Checkout | 1 s |
| Install uv | 7 s |
| `uv sync --extra dev --locked` | **2 s** (cache warm — shared `uv.lock` key with the other four lanes) |
| Download the image artifact | 4.5 s |
| `docker load` + identity assert | **41 s** |
| `docker run -d` | 0.3 s |
| `/health` contract (4 polls) | **10 s** |

`docker load` is the job, and no cache helps it — it is the same ~28-41 s `secret-grep`
pays. The sync is cheap only because four other jobs warmed the same cache; on a cold one
it is the ~185 MB torch wheel. That cost is inherent rather than accidental: the smoke's
expectations *are* Python objects from this tree, and the only way to avoid importing them
is to restate them, which is the thing the AC forbids.

**Job shape.** `smoke` is a sibling of `secret-grep`, both `needs: build-amd64`: neither
reads the other's output and a broken contract and a leaked token are independent
failures worth reporting in one run. `smoke` *does* check the repository out, which is the
exact opposite of `secret-grep`'s deliberate no-checkout — and for the opposite reason.
`secret-grep` keeps its working directory empty so its pattern set cannot be weakened by
editing a fetched script; `smoke`'s whole point is to compare the running image against
*this commit's* contract, so reading `pipeline/contract.py` at job time is the feature.
Both reasons are written beside their jobs.

**Notes for the following stories:**
- **Spec 5 extends this job** (in-image contract file ↔ `/health` version equality). The
  seam is `contract_smoke.py`: add an evaluator returning a failure list, call it from
  `run_smoke`, and cover it in `tests/test_contract_smoke.py`. Do not add assertions to
  the workflow's shell — the guard that forbids it is
  `test_smoke_does_not_enumerate_the_contract_in_bash`, and it is deliberate.
- A close relative is available and was left out as beyond the AC: the container's
  `sanitizer_revision` could be compared to `derive_sanitizer_revision(config)` computed
  from this tree, since the image is built from this commit in the same run. That is a
  strictly stronger check than "non-empty" and a natural companion to spec 5's version
  equality.
- **US-007's `publish` downloads the same artifact and asserts the same identity — and
  that is not, on its own, a statement about what gets published.** This bullet originally
  stopped at the first clause, and its verifier was right to flag it against US-007's
  multi-arch hint: the two cannot both be read as written, because a buildx multi-arch
  push *cannot ship a `docker load`ed image*. A manifest list is built across platforms by
  buildx and pushed from its own builder; the tarball lives in the runner's daemon, which
  buildx does not push from. The corrected statement, as implemented:

  * `publish` **is** a consumer — it downloads the one artifact and asserts the loaded
    image ID equals `build-amd64`'s, byte-for-byte the assertion `secret-grep` and `smoke`
    make. `"publish"` is in `_IMAGE_CONSUMERS` and the two parametrized handoff tests
    cover it for free, exactly as this bullet promised.
  * What that assertion establishes is that *the tarball* is the gated image. It
    establishes nothing about the registry, because the amd64 leg is **rebuilt** at
    publish time from `type=gha` — the cache `build-amd64` wrote with `mode=max` earlier
    in the same run, at the same commit, off the same digest-pinned base and lock. Every
    amd64 layer is served from cache rather than re-executed.
  * "Served from cache" is a claim, so US-007 checks it instead of asserting it: after the
    push it reads the published amd64 manifest's `rootfs.diff_ids` back out of the
    registry and requires them equal, in order, to the tarball's `RootFS.Layers`. Diff IDs
    are the sha256 of each layer's *uncompressed* tar, so they survive re-compression by a
    registry and by a different exporter — which is what makes them the right thing to
    compare and image IDs the wrong thing (those differ legitimately, since the config
    records exporter metadata).

  So: `needs:` is ordering, the artifact download gives the gated image an identity, and
  the post-push diff-ID comparison is what ties that identity to the registry. Three
  separate mechanisms; none of them substitutes for another.
- The two action pins this story reuses (`actions/download-artifact` v8.0.1,
  `actions/checkout` v7.0.1) are US-001/US-003's; no new third-party action was added.
- Suite went 656 → **731** tests (+47 `test_contract_smoke.py`, +28 workflow guards), all
  green. `kit_tools/testing/TESTING_GUIDE.md` carries the new counts, the six-job list, a
  copy-pasteable local smoke recipe, and two `test_mapping` changes (`contract_smoke.py`
  is new; `retrieval_app.py` now maps to two modules).
- `kit_tools/docs/GOTCHAS.md`'s PromptGuard entry gained the paragraph that makes it
  honest: the "never report healthy without weights" rule is no longer something a
  reviewer has to remember — it is a red workflow.

### US-007 — Publish pipeline: tags, Release, multi-arch, required checks (2026-09-08)

**Shipped:** `.github/workflows/ci.yml` (`publish` job), `docs/releases.md` (new), 40 new
guards in `tests/test_ci_workflow.py` (107 → 147) including a small GitHub-expression
evaluator, plus the doc propagation. Commits `cf0517b` + `e45f70f` + `7f99371` (the
scoped-permissions-keeps-contents guard, described below but omitted from this list as
first written — supervisor correction).

**The green publish — tag `v0.9.0-rc`:**
<https://github.com/WashingBearLabs/Forage/actions/runs/34179620038> · conclusion
`success` · `lint` 17 s, `typecheck` 28 s, `test` 38 s, `build-amd64` 1 m 39 s,
`secret-grep` 47 s, `smoke` 1 m 11 s, **`publish` 1 m 02 s**. It pushed exactly one tag —
`ghcr.io/washingbearlabs/forage:0.9.0-rc` — and created a Release flagged
**pre-release**. `latest` was not referenced.

**The red rehearsal — tag `v0.9.1-redrehearsal`:**
<https://github.com/WashingBearLabs/Forage/actions/runs/34179924855> · conclusion
`failure` · `test` **failed**, `lint`/`typecheck`/`build-amd64`/`secret-grep`/`smoke`
succeeded, **`publish` skipped with zero steps executed**. Nothing was pushed and no
Release was created. Detail below.

Two earlier runs in the same story, both on throwaway branches now deleted: the
**PR-event proof** (<https://github.com/WashingBearLabs/Forage/actions/runs/34178166673>,
all six jobs green on a real `pull_request` event) and the **arm64 feasibility
measurement** (<https://github.com/WashingBearLabs/Forage/actions/runs/34178289804>).

---

#### The contradiction US-005's verifier flagged, resolved

US-005's hand-over said `publish` "must download the same artifact and assert the same
identity"; this story's hint described a multi-arch buildx rebuild from a warm cache. The
verifier was right that both cannot hold as written, and the reason is concrete: **a
buildx multi-arch push cannot ship a `docker load`ed image.** A manifest list is built
across platforms by buildx and pushed from its own builder; the tarball lives in the
runner's Docker daemon, which buildx does not push from. There is no flag that bridges
that.

The resolution is not to pick one horn. It is that "the published image is the gated one"
was being asked of a single mechanism when it needs three, and each does a different job:

1. **`needs:`** — ordering, and only ordering. Six edges, so a tag on a red tree never
   reaches this job.
2. **The artifact download + image-ID assert** — copied verbatim from `smoke`. This
   establishes that *the tarball this job holds* is the image `build-amd64` built,
   `secret-grep` cleared and `smoke` executed. It establishes nothing about the registry.
3. **The post-push diff-ID comparison** — the new part, and the one that closes the gap.

The amd64 leg *is* rebuilt at publish time, from `type=gha` — the cache `build-amd64`
wrote with `mode=max` earlier in the same run, at the same commit, off the same
digest-pinned base and the same committed `uv.lock`. "Served from cache rather than
re-executed" is a claim about a build system's behaviour, so the job checks it instead of
asserting it: it records the gated tarball's `RootFS.Layers` before the push, and after
the push reads the published amd64 manifest's `rootfs.diff_ids` back out of the registry
with `docker buildx imagetools inspect`. They must be equal, in order.

Diff IDs are the sha256 of each layer's **uncompressed** tar, so they are unaffected by a
registry re-compressing blobs or by a different exporter writing the config — which is
exactly what makes them the right thing to compare, and image IDs the wrong thing. Image
IDs *do* differ here, legitimately, because the config records the exporter's own
metadata. Asserting on those would be the same mistake `build-amd64`'s
builder-vs-daemon note already declines to make.

**It holds, measured, on both real runs:**

```
publish  Gated amd64 filesystem: 16 layers
publish  Published amd64 filesystem is identical to the gated one (16 layers).
```

with 12 `CACHED` amd64 layers in the build log. So the linkage argument is not "cache-keyed,
therefore probably the same" — it is "cache-keyed, and then verified".

US-005's Implementation Notes have been **rewritten** where they carried the contradicted
half, not annotated underneath it — this project has a recorded failure mode about
corrections that live below the wrong text. Two passages changed: the `_IMAGE_CONSUMERS`
hand-over bullet, and the artifact-pressure bullet (see the retention note below).
`tests/test_ci_workflow.py`'s module docstring and `_IMAGE_CONSUMERS`' own comment now
state the twist where a reader meets it: `publish` is a consumer that cannot push what it
consumes.

**Scope of the claim, stated in the job and in `docs/releases.md` rather than implied:
amd64 only.** There is no arm64 artifact because there is no arm64 gate — nothing in this
workflow has ever *executed* the emulated leg. It is built from the same commit, the same
lock and the same digest-pinned multi-arch base, and that is the whole of the claim. A
consumer on arm64 is the first thing to run that image, and `docs/releases.md` says so.

#### Multi-arch feasibility: measured before the platform list was decided

Both halves of the hint's check, answered with numbers rather than judgement.

*Do the CPU torch aarch64 wheels resolve?* Yes, and from the committed lock:
`torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_aarch64.whl` is in `uv.lock` under the
`download.pytorch.org/whl/cpu` index. `manylinux_2_28` needs glibc ≥ 2.28; the pinned
base is trixie (2.41). The base digest is an *index* covering `linux/amd64` and
`linux/arm64/v8`, as US-003 already recorded.

*Does a cold qemu build fit inside 30 minutes?* **2 m 22 s** — 8 % of the budget. Measured
on a throwaway PR job with an empty cache, every layer built:

| Step | Time |
|---|---|
| `apt-get install curl` | 44.1 s |
| `uv sync --locked --no-dev` (torch 151.9 MiB aarch64 wheel; 66 packages) | 39.2 s |
| `RUN python -c "import retrieval_app"` — the genuinely emulated CPU work | 54.1 s |
| base pull, context, the rest | ~5 s |

It is cheap because **nothing in this image is compiled**: it is a wheel install, and qemu
only slows the interpreter work. The one emulated CPU cost is the import smoke, 54 s
against ~5 s native. So multi-arch went ahead and **no amd64-only fallback was taken**.

The fallback is still one line if that ever changes — `PUBLISH_PLATFORMS` in the
workflow's `env:` block — and it is wired so a trim is self-consistent: the QEMU setup
step is conditioned on the platform list and drops out on its own, and the verification
step handles a single-platform manifest as well as an index. `docs/releases.md` carries
the table and the trim rule.

**Cache shape matters and is not tidiness.** The arm64 layers live in their own GHA scope
(`cache-to: type=gha,mode=max,scope=publish`, with `cache-from` reading both that and the
default scope). They cannot share `build-amd64`'s: that job writes an amd64-only manifest
on every run, so a shared scope would evict the emulated layers each time and pay the cold
qemu cost forever. The evidence it works: the first publish (main push, arm64 cold) took
**4 m 37 s**; the tag run's publish, reading the scope the main run filled, took
**1 m 02 s**.

#### The tag policy is evaluated, not pattern-matched

Four rules, each carrying its own `enable=`:

| Ref | Tags published | Release |
|---|---|---|
| `refs/heads/main` | `sha-<short>` | none |
| `refs/tags/v0.9.0-rc` (any tag with a `-`) | `0.9.0-rc` | yes, `--prerelease` |
| `refs/tags/v1.0.0` | `1.0.0`, `1.0`, `latest` | yes |
| `refs/tags/searxng-v0.1.0` | none — the job does not run | none |

`flavor: latest=false` turns off `metadata-action`'s own `latest=auto` first. `latest=auto`
does roughly the right thing, and "roughly" is not a standard to hold the one tag whose
accidental movement is a production incident to. The rule is written out instead:
`startsWith(github.ref, 'refs/tags/v') && !contains(github.ref, '-')`, which is semver's
own definition of a pre-release reduced to one character.

The guards for this do not grep the policy — they **evaluate** it.
`tests/test_ci_workflow.py` grew a ~120-line recursive-descent interpreter for the subset
of GitHub expressions this file uses (`&&`, `||`, `!`, `==`, `!=`, parentheses,
single-quoted strings, `github.*`, `startsWith`/`endsWith`/`contains`), so a test can ask
"for `refs/tags/v0.9.0-rc`, which tags does this publish, and does this job even run?" and
get the *workflow's* answer. An expression it cannot parse **raises** rather than reading
as `False` — a rewritten condition that silently evaluated false would report a green
"`latest` does not move" for a policy nobody checked.

That was worth the code the first time it ran. Written loosely, the `{{major}}.{{minor}}`
rule was gated only on `!contains(github.ref, '-')`, which is *true* for
`refs/heads/main`: the rule was live on every main push and merely produced no output,
because `metadata-action` emits nothing for a branch ref. The test caught it, and the
policy now states each condition in full rather than leaning on an action's internals.
US-003's lesson generalises past shell: a substring check over a condition tests its
vocabulary, not its meaning.

The same evaluator is what makes the **cross-fire guard** real rather than rhetorical.
`refs/tags/searxng-v0.1.0` does not start with `refs/tags/v`, and the test demonstrates
that by evaluating the job's own `if:` against that ref rather than by asserting a
substring is present.

#### The supervised tag push, and what the registry actually holds

Verified from inside Actions on a throwaway PR
(<https://github.com/WashingBearLabs/Forage/actions/runs/34180408923>), because the
session's `gh` token carries no `read:packages` scope and **no PAT was minted to get
one**. That turned out to be the better instrument anyway: it pulls the way a consumer
does, from the registry, with a token that only has `packages: read`.

```
--- ghcr.io/v2/washingbearlabs/forage/tags/list ---
{ "name": "washingbearlabs/forage", "tags": [ "sha-e45f70f", "0.9.0-rc" ] }
--- end tag list ---
latest does not resolve. Correct: no non-pre-release v* tag has been published.

Name:      ghcr.io/washingbearlabs/forage:0.9.0-rc
MediaType: application/vnd.oci.image.index.v1+json
Digest:    sha256:be9c1996b04b5f59677b973baa2c534f2f52027fde9c0c4478615c6816cb2fd0
  Platform:  linux/amd64   (sha256:848a5244…23e5)
  Platform:  linux/arm64   (sha256:cc4bb375…ed5f)

/health answered 200 after 5 attempt(s)
{"status":"degraded", … ,"sanitizer_revision":"0537316d…e3e253","contract_version":"1.0.0",
 "degraded_reasons":["promptguard_unavailable","cache_unavailable"]}
Contract smoke PASSED: degraded, honest, and on-contract.
```

So, at that moment: two tags and no others; **`latest` does not exist** (asserted by
`docker manifest inspect` *failing*, not by absence from a list); the rc is a real
two-platform OCI index; and the *published* image — not the artifact tarball — passes the
committed contract smoke. `docker pull` of the ghcr ref succeeded as part of that.

The tag *count* is a snapshot, not an invariant: every push to `main` adds one more
`sha-<short>` by design, and this story's own doc commit added `sha-893ccb6` after the
listing above was taken (run
<https://github.com/WashingBearLabs/Forage/actions/runs/34180822114>, seven jobs green,
`publish` pushing that one tag and again reporting 16 identical layers). The invariant the
evidence establishes is the shape of the set: **only** `sha-` tags and pre-release
versions, no `X.Y` alias, and no `latest` — which stays true until the first
non-pre-release `v*` tag, and is asserted on every run by the guards rather than by this
paragraph.

The Release body carries the digest, the platform list, and a link to the run that built
it.

#### The red rehearsal, and the boundary it exposed

The rehearsal commit is a realistic defect rather than sabotage of the pipeline:
`CONTRACT_VERSION` bumped to `9.9.9` without regenerating the golden fixture — the exact
mistake US-005 built the smoke to worry about. It was built in a **separate git worktree**
so `main` and the working tree were never touched, tagged `v0.9.1-redrehearsal`, and only
the *tag* was pushed (the workflow's `push` trigger covers `main` and tags, so a branch
push would have run nothing).

Result: `test` **failed** — `4 failed, 766 passed`, headed by
`test_contract_schema_matches_golden — FileNotFoundError: tests/golden/contract_9_9_9.json`
— and `publish` was **skipped with an empty step list**: it never started, so nothing could
have been pushed even partially. No Release exists for that tag; the registry tag list
above, taken afterwards, holds nothing from it. The tag was then deleted from the remote
and locally, and the worktree removed.

**The useful surprise: `smoke` passed.** That is not a hole, it is the boundary between two
guards, and it is worth knowing before someone reads a green smoke as "the contract is
right". `smoke` reads `CONTRACT_VERSION` from `pipeline/contract.py` at job time and
compares it to what the running image reports — and the image was built from the *same*
commit, so both said `9.9.9` and they agreed. The smoke tests **image↔tree agreement**;
the golden fixture tests **tree↔frozen-contract agreement**. Neither subsumes the other,
and it took a deliberately-red run to make that visible. The rehearsal was aimed at the
`needs:` chain and it demonstrated something else as well.

#### Required status checks: attempted, refused, deferred — with the actor named

| Attempt | Actor | Result |
|---|---|---|
| `PUT /repos/WashingBearLabs/Forage/branches/main/protection`, six contexts | `wblabs001` — repo **ADMIN**, token scoped `admin:org, repo, workflow, gist` | `403 Upgrade to GitHub Pro or make this repository public` |
| `POST /repos/WashingBearLabs/Forage/rulesets` | same | same 403 |
| `GET /orgs/WashingBearLabs/rulesets` (org-level fallback) | same | `403 Upgrade to GitHub Team` |

**A correction to the hint, which said the blocker was the actor.** It read
"branch-protection edits need admin — a fine-grained PAT or the supervisor by hand;
`GITHUB_TOKEN` cannot". An admin token was used and got the same 403 from all three APIs.
The blocker is the **plan**: private repositories on GitHub Free have no branch protection
at all. A PAT would have bought nothing, and none was created. This is the same wall the
bootstrap spec hit, so the deferral is recorded the same way — in
`docs/bootstrap-notes.md`'s deferred-security list, with the exact six contexts to
register at the public flip and the note that `actionlint` is a *step* inside `lint`, not
a job, and must not be listed.

The AC therefore reads as satisfied-by-recorded-deferral, matching the bootstrap
precedent. Worth saying plainly what is and is not lost: required checks stop a human
merging over red. The `needs:` chain stops anything *publishing* over red — which is the
property that protects a consumer, and it is live now.

#### Guards, mutation-verified — 28 mutations, one escape, caught and closed

Implementation committed **first** (US-003's harness lesson, honoured — the restore is
`git checkout --`).

| Mutation | Failures |
|---|---|
| ci.yml: delete the `publish` job outright | 39 |
| ci.yml: drop `smoke` from the `needs:` list | 1 |
| ci.yml: publish grants itself a third write scope (`actions: write`) | 2 |
| ci.yml: `build-amd64` grants itself `packages: write` | 2 |
| ci.yml: `latest` becomes unconditional | 3 |
| ci.yml: `latest` drops only the pre-release half of its condition | 2 |
| ci.yml: metadata-action's own `latest=auto` re-enabled | 1 |
| ci.yml: the `X.Y` alias loses its pre-release gate | 1 |
| ci.yml: the `sha-` tag is published from every ref | 2 |
| ci.yml: the publish `if` widens to every tag (searxng cross-fire) | 1 |
| ci.yml: the publish `if` stops excluding pull requests | 1 |
| ci.yml: the Release is created before the image is pushed | 1 |
| ci.yml: the Release step loses its `v*`-tag condition | 1 |
| ci.yml: the Release stops flagging pre-releases | 1 |
| ci.yml: a layer mismatch warns instead of failing | 1 |
| ci.yml: the layer verification is deleted entirely | 5 |
| ci.yml: an image-identity mismatch warns instead of failing | 1 |
| ci.yml: publish stops downloading the gated artifact | 6 |
| ci.yml: publish does not push | 1 |
| ci.yml: the platform list is hardcoded instead of read from env | **0 → 1** |
| ci.yml: `PUBLISH_PLATFORMS` trimmed to amd64 but the QEMU step stays | 1 |
| ci.yml: attestations turned back on | 1 |
| ci.yml: the emulated layers share `build-amd64`'s cache scope | 1 |
| ci.yml: publish uploads its own copy of the image | 2 |
| ci.yml: the registry login uses a stored PAT | 2 |
| ci.yml: the failure-mode prose removed from the job | 1 |
| ci.yml: the arm64-is-ungated caveat removed | 1 |
| ci.yml: publish forward-`needs:` a job US-004 has not added | 2 |

**The escape, and why it is the useful row.** Hardcoding
`platforms: linux/amd64,linux/arm64` in place of `${{ env.PUBLISH_PLATFORMS }}` failed
nothing. The guard read `_resolve_env(workflow, platforms)` and compared it to the env
block — but `_resolve_env` returns a non-expression unchanged, so a hardcoded copy of the
same string resolves to itself and matches. The test verified equality of *values* and
believed it had verified *single-sourcing*. That matters beyond tidiness: `docs/releases.md`
tells the next maintainer that `PUBLISH_PLATFORMS` is the one lever for the amd64-only
fallback, and a second copy of the string makes that documentation false while everything
stays green. The guard now asserts the expression itself, and the reasoning is in the
test so nobody re-loosens it. Generalised: `_resolve_env` is for comparing two
single-sourced references to *each other*; put a literal on one side and it stops testing
what you think.

#### A second finding, from the evidence-gathering rather than the implementation

A job-level `permissions:` block **replaces** the top-level grant — it does not merge with
it. The throwaway pull-smoke job declared `permissions: {packages: read}`, silently lost
the `contents: read` it had been inheriting, and `actions/checkout` failed with
`fatal: repository 'https://github.com/WashingBearLabs/Forage/' not found` — a private-repo
404 that reads like a typo in the repo name rather than like a permissions bug. `publish`
is unaffected only because `contents: write` implies read.

Since US-004 writes the next permissions block, this became a guard rather than a memory:
`test_a_job_that_scopes_permissions_and_checks_out_keeps_contents` fails if any job scopes
its own permissions, checks the repository out, and omits `contents`. The reason is also
written beside `publish`'s block.

#### The no-job-permissions rule, restated honestly

`build-amd64` and `smoke` each asserted "this job raises no write permissions", and the
file's shape was simply that no job declared permissions at all. That is no longer true —
`publish` must raise two scopes — so the invariant is now stated as what it actually is:
`test_publish_is_the_only_job_that_raises_write_permissions` collects every job's write
grants and requires the result to be exactly `{"publish": ["contents", "packages"]}`. A
third scope fails it, and so does a second job, including one that did not exist when it
was written. The two per-job guards stay: they name their own reason and fail with a
message about *that* job.

#### `sanitizer_revision` did NOT rotate

`0537316d83510dab…e3e253` before and after, measured both times — the fourth story in a
row to leave it alone. Nothing here touches a `_REVISION_SOURCES` file; the only edit that
went near one was the red rehearsal's `CONTRACT_VERSION` bump, which lived in a throwaway
worktree on a tag that has been deleted and was never an ancestor of `main`.
`docs/bootstrap-notes.md`'s "no fourth rotation" paragraph still holds.

#### Artifact retention: the US-005 carry-forward, closed with a correction

`publish` **does** consume the amd64 artifact, so the retention floor of **1 day** stands
as the whole of the mitigation and no cleanup job was added.

US-005 nominated this story as the right place for one, reasoning that `publish` is the
last consumer and its `needs:` list is complete by construction. That reasoning does not
survive contact: **`publish` is conditional.** It is skipped on every `pull_request` event
and on `searxng-v*` tags, so a cleanup job hung off it would never run for the majority of
workflow runs — which are exactly the ones parking a 375 MiB tarball. US-005's own
rejection reasons still stand unchanged (a new `actions: write` surface on a repository
about to go public, against a quota US-003 measured as not enforced). That bullet in
US-005's notes has been rewritten rather than footnoted.

#### Notes for the following stories

- **US-004's searxng lane is a sibling of this one, not an extension.** Guard the two
  lanes apart with `startsWith(github.ref, 'refs/tags/searxng-v')`; `refs/tags/v` and
  `refs/tags/searxng-v` are already mutually exclusive as prefixes, and
  `TestPublishJob::test_which_refs_reach_the_publish_lane` is the pattern to copy — add
  `_SEARXNG_REF` cases to both lanes' tables and the cross-fire is machine-checked in both
  directions.
- `_evaluate` in `tests/test_ci_workflow.py` is reusable for any `if:` or `enable=` in
  this file. Extend `_EXPR_FUNCTIONS` if a new predicate is needed; do **not** make it
  tolerant of what it cannot parse.
- **Read `docs/releases.md` before touching the tag policy.** It is written for a consumer
  and is the only place the amd64/arm64 gating asymmetry is stated in full.
- The four action pins added here: `docker/setup-qemu-action` v4.3.0,
  `docker/metadata-action` v6.2.0, `docker/login-action` v4.6.0 — and *not* a
  release action: `gh release create` uses the preinstalled CLI, which is one fewer pinned
  dependency for a two-line API call on a repository about to go public.
- **spec 5 attaches the contract asset to the Release.** The seam is the
  `Create the GitHub Release` step; `gh release create` takes files as trailing arguments,
  and `test_publish_verifies_before_it_releases` already pins the ordering that makes an
  attached asset meaningful.
- `publish` on a `main` push costs ~1–4½ minutes depending on whether the arm64 scope is
  warm, on top of the six gates. Every merge to main now publishes a `sha-` image; that is
  deliberate (a pullable build per commit) but it is not free on the Actions tier.
- Suite went 731 → **771** tests (+40 workflow guards), all green.
  `kit_tools/testing/TESTING_GUIDE.md` carries the new counts and the seventh job.
- Three throwaway branches were used and all three are deleted, with their runs recorded
  above: the PR-event proof, the arm64 feasibility measurement, and the published-image
  pull smoke. They are measurements and one-time proofs, not gates — the durable artifacts
  are the numbers, the platform list, and the committed guards.

### US-004 — forage-searxng companion image, honest public defaults (2026-09-08)

**Shipped:** `searxng/Dockerfile` (new), `searxng/config/settings.yml` +
`searxng/config/limiter.toml` (reworked), `searxng_smoke.py` (779 lines, new, repo root),
`tests/test_searxng_smoke.py` (61 tests, new), `tests/test_searxng_docker.py`
(9 → 28 tests, rewritten), `.github/workflows/ci.yml` (`searxng-build`, `searxng-smoke`,
`searxng-publish`, plus cross-fire `if:` on `build-amd64`/`secret-grep`/`smoke`),
52 new guards in `tests/test_ci_workflow.py` (149 → 201), `docs/searxng.md` (new), and
the doc propagation. Commits `090cb38` + `474333c` + `e10ff5f` + `1700b29` + `fefe0ae`
+ `3bc6daf`.

**The main-push run — green, nine jobs, one correctly skipped:**
<https://github.com/WashingBearLabs/Forage/actions/runs/34184930763> · conclusion
`success` · `lint` 21 s, `typecheck` 27 s, `test` 28 s, `build-amd64` 1 m 32 s,
`secret-grep` 46 s, `smoke` 1 m 11 s, `publish` 1 m 30 s, **`searxng-build` 1 m 01 s**,
**`searxng-smoke` 58 s**; `searxng-publish` skipped (no `main`-push publish for the
companion image, by design).

**The companion publish — tag `searxng-v0.1.0-rc`:**
<https://github.com/WashingBearLabs/Forage/actions/runs/34185197984> · conclusion
`success` · `lint` 16 s, `typecheck` 23 s, `test` 31 s, `searxng-build` 33 s,
`searxng-smoke` 46 s, **`searxng-publish` 1 m 08 s** — and `build-amd64`, `secret-grep`,
`smoke` and `publish` all **skipped in 0 s**. That skip list is the cross-fire guard
working in production rather than in a test.

**The published-image proof:**
<https://github.com/WashingBearLabs/Forage/actions/runs/34185387103> (throwaway branch,
since deleted). Run from inside Actions with `packages: read`, because the session's `gh`
token carries no `read:packages` scope and no PAT was minted — the same instrument
US-007 used, and the better one anyway: it pulls the way a consumer does.

---

#### The Redis env-var name, and the much larger thing behind it

The story existed to defuse one landmine — `SEARXNG_REDIS_URL` was an unverified name —
and defusing it uncovered a bigger one under the same floorboard.

**The name, verified against the pinned digest.** `searx/settings_defaults.py` carries
*both*:

```python
# redis is deprecated ..
'redis':  {'url': SettingsValue((None, False, str), False, 'SEARXNG_REDIS_URL')},
'valkey': {'url': SettingsValue((None, False, str), False, 'SEARXNG_VALKEY_URL')},
```

`searx/valkeydb.py` prefers `valkey.url`, falls back to `redis.url`, and warns
(`DeprecationWarning: setting redis.url is deprecated, use valkey.url`). Both were
exercised live and both initialise the limiter. **The name to document is
`SEARXNG_VALKEY_URL`**; the old one works and is superseded.

**The bigger finding: a working limiter refuses this image's only client.** The spec's
plan was `limiter: true` with the backend requirement documented, on round 2's correct
observation that `limiter: true` without a backend is inert theatre. The first half
reproduces exactly. The second half does not survive contact with the consumer.
Measured, limiter installed, real Valkey attached:

| Client | Result | Blocked by |
|---|---|---|
| Forage's own httpx client | **429 on request 1** | `http_accept_language` — httpx sends no `Accept-Language` |
| `curl` | **429 on request 1** | `http_accept_encoding`, and again `http_user_agent` |
| a browser-shaped client | 200, 200, 200, **429** | `ip_limit.API_MAX = 4` per `API_WINDOW = 3600 s`, per client network, for `format != html` |

Neither constant is settable from `limiter.toml`; both are module-level in
`searx/botdetection/ip_limit.py`. botdetection is a *browser* detector, and an API
consumer is not a browser.

So the AC's own smoke, written literally, would have passed and been useless: one
`format=json` request inside a fresh window returns 200 whether or not a limiter is
installed. The gate would have been green while the shipped image throttled Forage's
search stage to four queries an hour — an *intermittent* production failure, surfacing in
Forage's logs only as `searxng_error`.

**What shipped instead: `limiter: false`, stated at length in the file.** This is a
deliberate deviation from the AC's wording and it is aimed squarely at the AC's intent —
the story's own Description asks for "a *working* JSON API", and `limiter: true` is not
compatible with one. The honest position is that this image does not rate limit and says
so; what protects it is not being exposed. The opt-in for a deployment that must expose
it is `SEARXNG_LIMITER=true` + `SEARXNG_VALKEY_URL` (no rebuild, no overlay — both are
schema-mapped env vars), and then a `pass_ip` entry for the consumer's network: a scoped
IP-trust relaxation made by the operator who needs it, rather than baked in for everyone.

The AC's clause is still satisfied in substance, and by measurement rather than
assertion: the blocking smoke *does* stand up a Valkey-backed limiter and prove it
initialised. It just does not ship it.

**`link_token`, answered empirically rather than assumed.** With the limiter installed
and a browser-shaped JSON client: `200 200 200 200 429` with `link_token = false`,
`200 200 429 302` with it true (`BURST_MAX_SUSPICIOUS = 2`, then `SUSPICIOUS_IP_MAX`
redirects). It strictly narrows an already-narrow budget, and never gets the chance to
matter for Forage's real client, which the header methods refuse two steps earlier. Off.

**`SEARXNG_SECRET`, both directions.** Set → starts and serves. Unset → the container
**exits 1** with `ERROR:searx.webapp: server.secret_key is not changed. Please use
something else instead of ultrasecretkey.` The mechanism is not the obvious one: the
image's entrypoint substitutes a random secret only into a *generated* settings file, and
a baked file means that path never runs — so with `use_default_settings: true` the value
falls through to upstream's placeholder and `webapp.py` refuses it. Fail-loud, which is
what a required variable should be.

#### What the baked config actually changed

Every interesting property is a removal, which is why every guard is a negative.

| Was | Is | Why |
|---|---|---|
| `secret_key: "poppy-searxng-internal"` | absent; `SEARXNG_SECRET` required | a known secret in a published image signs sessions for every puller |
| `limiter: false` + relaxed limiter.toml | `limiter: false`, hardened limiter.toml | same value, opposite meaning: it is now a stated position with the measurements behind it, not an oversight |
| `pass_ip = ["0.0.0.0/0"]` | `pass_ip = []`, `block_ip = []` | `ip_lists` has priority over every other method — that one line disabled bot detection entirely |
| `forwarded_for_header` / `real_ip_header` | absent | client-spoofable once reachable directly; **and not schema keys any more** — header trust moved to `botdetection.trusted_proxies`, whose loopback-only default is correct here and is deliberately not extended |
| (upstream default) `pass_searxng_org = true` | `false` | an unasked-for grant of unrestricted access to a hardcoded IP set, in a sidecar nobody monitors |
| `instance_name: "Poppy Search"` | `"Forage Search"` | this image is Forage's to publish |
| `torch`, `karmasearch` listed disabled | removed | **neither module exists upstream any more, and naming a missing engine is not a no-op**: SearXNG logs `Cannot load engine` with a traceback at startup for each, disabled or not. Found by reading the container's log rather than by reasoning. |

`bing` and `ahmia` stay *listed and disabled* — with `use_default_settings: true`, an
entry is what stops an upstream release re-enabling them.

#### The base pin

`searxng/searxng@sha256:1dab138e…9759` = **2026.9.7-3e454637f**, published 2026-09-07,
upstream revision `3e454637fb9829756c805dd9c02100f0bc9520fd`. A multi-arch index covering
`linux/amd64`, `linux/arm64` and `linux/arm/v7`, so the two-platform publish is not
foreclosed. The release and date live on the comment lines *above* the `FROM`, because
Dockerfile has no inline comments (US-003's finding, reused rather than rediscovered),
and a guard test keeps them there.

Two helper images are pinned the same way, in `searxng_smoke.py` rather than in the
workflow: `valkey/valkey@sha256:d2e18f34…` (8.1-alpine, Valkey 8.1.10) and
`curlimages/curl@sha256:58adaa4e…` (curl 8.22.0). A gate whose supporting cast can move
is not reproducible.

#### The smoke is genuinely hermetic, and that took a specific mechanism

`docker network create --internal`. Containers on it reach each other and nothing else:
every engine query fails DNS resolution and the JSON envelope comes back with
`results: []`. That is the property that lets this job sit in a publish `needs:` chain —
round 3's critical was that a live engine query there reproduces the
every-engine-throttled outage `GOTCHAS.md` records, with a release as the victim.

**Docker silently ignores `-p` on an `--internal` network.** Measured: the container
reported `8080/tcp` rather than a published port and the host got `000` from curl. So the
client has to be a third container, which is what the spec's hint said anyway — but for a
reason worth writing down rather than inherited.

Four blocking phases, and the third and fourth are a differential rather than a log grep:

1. `SEARXNG_SECRET` unset ⇒ the container exits non-zero, naming the secret.
2. Shipped defaults (with a Valkey URL supplied, to prove a *present* backend does not
   switch a limiter on) ⇒ `format=json` answers 200 with a parseable envelope carrying
   `results`, and no block page.
3. Six consecutive JSON requests, all 200 — past `API_MAX`. This is the guard that
   notices if someone turns the limiter back on, and it is why the probe count is
   `API_MAX + 2` rather than a round number.
4. `SEARXNG_LIMITER=true` **with** Valkey ⇒ the limiter installs for real; **without** ⇒
   the inert-limiter error is logged and everything is served.

Phase 4's positive assertion is the one worth explaining. "The limiter initialised" has
no log line at the default level (`connecting to Valkey` is INFO, and INFO is filtered
unless `SEARXNG_DEBUG` is set — which changes behaviour and has no business in a gate).
So the smoke asserts it *behaviourally and directly*: a browser-shaped request reaches
`ip_limit`, `ip_limit` writes its sliding windows to Valkey, and the smoke reads that
DB's key count back. `Valkey keys after it: 3` is the limiter's own fingerprint. An
API-shaped request then gets 429, and the unbacked phase gets 200 with the error logged
and **zero** keys written. Three states, three distinguishable signatures.

The runner's log, unedited:

```
[1/4] SEARXNG_SECRET unset — the image must refuse to serve
      exited=True code=1
[2/4] shipped defaults — JSON envelope and 6 consecutive requests
      format=json -> HTTP 200
      6 consecutive JSON requests: [200, 200, 200, 200, 200, 200]
[3/4] SEARXNG_LIMITER=true with SEARXNG_VALKEY_URL — a real limiter
      browser-shaped -> HTTP 200; Valkey keys after it: 3; API-shaped -> HTTP 429
[4/4] SEARXNG_LIMITER=true with no backend — inert, and loud
      API-shaped -> HTTP 200; Valkey keys: 0
searxng hermetic smoke PASSED.
```

**The advisory half passed too**, on the first run — the four enabled engines returned
results from a GitHub-hosted runner IP (`Live-engine probe outcome: success`, also
written to the job summary). Recorded because a passing advisory probe today is the
baseline against which a future red one means something.

**It is a committed script, not a heredoc**, for `contract_smoke.py`'s reasons: Docker
goes through an injected runner, every judgement is a pure function over captured output,
and `tests/test_searxng_smoke.py` drives all 61 of those branches without a daemon. It
also means a maintainer bumping the pin reproduces CI with one command instead of
reconstructing a job — which is the thing anyone will want first.

#### Two lanes, guarded apart in both directions

`refs/tags/v` and `refs/tags/searxng-v` are mutually exclusive prefixes, and every job in
both lanes now carries its own `if:` rather than leaning on skip propagation through
`needs:` — propagation is a property of the graph that a later edit could remove without
noticing, and an explicit condition is what a test can evaluate per job.

| Ref | Runs | Skips |
|---|---|---|
| `refs/tags/searxng-v0.1.0-rc` | lint, typecheck, test, searxng-build, searxng-smoke, searxng-publish | build-amd64, secret-grep, smoke, publish |
| `refs/tags/v1.0.0` | lint, typecheck, test, build-amd64, secret-grep, smoke, publish | all three companion jobs |
| PR / `main` | everything except the two publishes (and `searxng-publish` never runs on `main`) | — |

`test` is the one edge the lanes share, and it is load-bearing: the guards that stop a
relaxation reaching a published companion image are pytest tests, not workflow steps.
Publishing over a red suite would publish exactly the config they exist to refuse.

**A trap the service lane's policy would have walked into.** `publish` tests for a
pre-release with `!contains(github.ref, '-')`. Copied to this lane that is *always false*
— every ref here contains a hyphen, in the `searxng-v` prefix itself — so `latest` would
never have moved for any release, and the failure would have been silent for as long as
nobody cut a stable version. The companion policy is written against the version after
the prefix is stripped, and `_evaluate` gained a `steps.version.outputs.version` context
key so a test can ask the workflow its own answer for both a release and a pre-release.

`searxng-v0.1.0` is also not a semver string, so `metadata-action` cannot parse the ref:
a step strips the prefix and hands the version to every rule as an explicit `value=`,
failing the job if the ref did not carry the prefix.

#### What the registry holds, verified from a consumer's position

```
--- ghcr.io/v2/washingbearlabs/forage-searxng/tags/list ---
{"name":"washingbearlabs/forage-searxng","tags":["0.1.0-rc"]}
--- end tag list ---
latest does not resolve. Correct: no non-pre-release searxng-v* tag has been published.

Name:      ghcr.io/washingbearlabs/forage-searxng:0.1.0-rc
MediaType: application/vnd.oci.image.index.v1+json
Digest:    sha256:e6c7aec517386392573ab966b8a5e3fb6e9fc39227c5ad5987b5094595f6dfec
  Platform:  linux/amd64   (sha256:8d2e50ae…4ef8)
  Platform:  linux/arm64   (sha256:dde08293…977f)

searxng hermetic smoke PASSED.        <- against the PUBLISHED image, not a local build
```

One tag and no others; `latest` asserted absent by `docker manifest inspect` *failing*
with a token that can read the repository, not by absence from a list; a real two-platform
OCI index; and the published image passes the same four blocking phases the gate ran.

`searxng-publish` also verified itself: `Gated companion filesystem: 6 layers` →
`Published companion filesystem is identical to the gated one (6 layers)`. Same
mechanism and same reasoning as US-007's — the amd64 leg is rebuilt from the cache
`searxng-build` filled in the same run (a buildx multi-arch push cannot ship a
`docker load`ed image), and diff IDs are what tie the rebuild back to the smoked image.

**No GitHub Release for this image, and therefore no `contents: write`.** Its changelog
is upstream's; a Release here would be an empty page asserting authorship of someone
else's work. `searxng-publish` holds `packages: write` and `contents: read` — the latter
written out rather than inherited, because a job-level block replaces the top-level grant
(US-007's expensive lesson, now a guard test that this lane is the first to exercise).

#### Guards, mutation-verified — 51 mutations, one escape, caught and closed

Implementation committed **first**, then mutated (US-003's harness lesson).

| Mutation | Failures |
|---|---|
| ci.yml: searxng-publish drops the `test` gate | 1 |
| ci.yml: searxng-publish drops the blocking smoke | 1 |
| ci.yml: the live probe becomes blocking | 1 |
| ci.yml: the blocking smoke becomes advisory | 1 |
| ci.yml: the advisory outcome is never recorded | 1 |
| ci.yml: searxng-publish fires on every tag (cross-fire) | 1 |
| ci.yml: build-amd64 loses its `searxng-v` exclusion | 2 |
| ci.yml: searxng-build loses its `v*` exclusion | 2 |
| ci.yml: the companion pre-release test copies the service lane's | 1 |
| ci.yml: `latest` becomes unconditional on the companion lane | 1 |
| ci.yml: searxng-publish grants itself `contents: write` | 2 |
| ci.yml: searxng-publish scopes permissions without `contents` | 2 |
| ci.yml: searxng-build pushes | 1 |
| ci.yml: the companion build shares build-amd64's cache scope | 1 |
| ci.yml: the companion build context widens to the repo root | 1 |
| ci.yml: the companion layer verification warns instead of failing | 1 |
| ci.yml: the companion identity assert warns instead of failing | 1 |
| ci.yml: searxng-smoke rebuilds instead of loading the artifact | 2 |
| ci.yml: the smoke job restates an assertion in bash | 1 |
| ci.yml: the smoke job skips the `--locked` sync | 1 |
| ci.yml: the companion platform list is hardcoded | 1 |
| ci.yml: the QEMU step stops following the platform list | 1 |
| ci.yml: the companion lane creates a GitHub Release | 1 |
| ci.yml: the vacuous-comparison floor is removed | 1 |
| ci.yml: the version-prefix strip loses its failure branch | 1 |
| ci.yml: the companion lane uploads a second copy of the image | 1 |
| ci.yml: the companion lane logs in with a stored PAT | 2 |
| settings.yml: the limiter is switched back on | 1 |
| settings.yml: a `secret_key` is baked back in | 2 |
| settings.yml: header trust returns | 1 |
| settings.yml: an engine is enabled the orchestrator never asks for | 4 |
| settings.yml: bing disappears from the list entirely | 2 |
| settings.yml: `use_default_settings` is dropped | 1 |
| limiter.toml: the wildcard pass list returns | 3 |
| limiter.toml: `trusted_proxies` is baked in | 1 |
| limiter.toml: `link_token` is enabled | 1 |
| limiter.toml: the searxng.org passlist is re-enabled | 1 |
| searxng/Dockerfile: the base is un-pinned | 1 |
| searxng/Dockerfile: the release comment is dropped | 1 |
| searxng/Dockerfile: the config stops being baked | 2 |
| searxng/Dockerfile: an entrypoint wrapper is added | 1 |
| smoke: the blocking network stops being internal | **0 → 1** |
| smoke: the budget probe stops reaching past `API_MAX` | 1 |
| smoke: a phase is dropped from the blocking tuple | 1 |
| smoke: the deprecated env-var name becomes the documented one | 2 |
| smoke: an empty Valkey DB stops failing the backed phase | 2 |
| smoke: the envelope check tolerates a missing `results` key | 1 |
| smoke: the inert phase stops requiring the warning | 1 |
| smoke: a bot-block 429 is accepted as an envelope | 2 |
| smoke: the helper images are un-pinned | 1 |
| smoke: a secret-less container that keeps running is accepted | 1 |

**The escape, and it is the most important row in the table.** Flipping
`create_network(internal=True)` to `False` at its one real call site inside
`run_blocking_smoke` failed **nothing**. The guard had been written the obvious way — it
called `harness.create_network(internal=True)` itself and asserted the flag came
through — so it tested the *method* and not the *call site*. Hermeticity is the entire
justification for putting this smoke inside a publish gate chain; without `--internal`
every phase still goes green while a throttled DuckDuckGo can fail a release. The guard
now drives `run_blocking_smoke` with the phases monkeypatched empty and asserts on the
`docker network create` argv it actually issues. This is the same family as US-007's
`_resolve_env` escape: **asserting that a helper behaves correctly is not asserting that
anything calls it correctly.**

**Three mutations were bad probes, not escapes, and re-running them properly is the
other lesson.** `use_default_settings: true`, `link_token = false` and the `--- end tags
---` anchor each appear more than once in their file, and a first-occurrence replace hit
a *comment* (or, for the release anchor, the service lane's `publish` job instead of
`searxng-publish`). Each reported zero failures and looked exactly like an escaped guard.
Re-applied against a unique anchor they fail 1 test each. US-005 recorded this as "a
mutation harness needs its mutations verified as much as the guards do"; the corollary
this time is concrete — **check the occurrence count before trusting a zero.**

**One more finding from the guards themselves.** The first version of
`test_the_smoke_does_not_enumerate_its_assertions_in_bash` grepped the job's shell for
`"results"`, `"429"` and `"limiter"` — and failed on the advisory step's own prose ("the
engines the baked config enables returned results"). That is US-003's
vocabulary-versus-behaviour mistake committed *by the guard* rather than by the thing
guarded. It now forbids the three tools you would reach for to restate an assertion —
`grep`, `jq`, `curl` — which is a statement about behaviour and cannot false-positive on
English.

#### `sanitizer_revision` did NOT rotate

`0537316d83510dab…e3e253` before and after, measured both times — the fifth story in a
row to leave it alone. Nothing here touches a `_REVISION_SOURCES` file;
`pipeline/orchestrator.py` *is* one, which is exactly why the engine-parity assertion
imports `_SEARXNG_ENGINES` from a test rather than adding a public alias to it. A naming
convenience is not worth a rotation.

#### Measurements worth carrying forward

| Thing | Value |
|---|---|
| Companion image size (amd64) | **91 MB** (upstream's base + one COPY layer) |
| `docker save \| gzip -1` | **90 MB** |
| `searxng-build` (cold) | 1 m 01 s; 33 s warm |
| `searxng-smoke` | 46–58 s for four container lifecycles + the advisory probe |
| `searxng-publish` | 1 m 08 s including the emulated arm64 leg |
| Artifact retention | 1 day (the floor), as with the service image |

The arm64 leg is cheap here in a way it is not for the service image: this build runs no
`RUN` step, so emulation has no CPU work to slow down. It is a base layer plus a COPY.

#### Notes for the following stories

- **Spec 4's compose fragments** (`feature-forage-cache-fallback` US-004) have been
  corrected in place: the backend variable is `SEARXNG_VALKEY_URL`, `SEARXNG_SECRET` is
  **required** (an unset one exits 1, so a fragment without it dies on first `up`), and
  the fragments must **not** wire a limiter at all — turning it on would fail their own
  `/search` round-trip.
- **`epic-forage-extraction` spec 3 (`poppy-consume`) US-002 lives in the Poppy
  repository and was NOT touched.** Its citation of `SEARXNG_REDIS_URL` needs the same
  correction, plus the limiter finding: Poppy's overlay must not enable the limiter
  without also passlisting Forage's network. Flagged for the supervisor.
- **Spec 6 owns the Poppy overlay.** `/etc/searxng/settings.yml` and
  `/etc/searxng/limiter.toml` are ordinary files in the image, so a bind mount over
  either *replaces* it wholesale — an overlay must repeat whatever it still wants from
  the baked file. The `use_default_settings: true` relationship is with *upstream's*
  settings, not with this image's. It also rotates the `poppy-searxng-internal` secret,
  which US-008's flip note already tracks.
- **Bumping the pin is a documented, testable ritual** (`docs/searxng.md`): bump the
  digest and the comment, `uv run python searxng_smoke.py --image forage-searxng:ci`,
  then `--live`, then `pytest tests/test_searxng_docker.py`, then a `searxng-v*` tag.
  `GOTCHAS.md`'s `:latest`-rot entry was rewritten rather than annotated: pinning removes
  the *silent* rot, it does not make upstream's engine fixes arrive on their own, and a
  bump nobody performs re-arms the gotcha.
- The advisory probe is the early-warning signal for engine rot and it is deliberately
  non-blocking. Read the job summary at every bump; a red line there is the cue to look,
  not a reason to hold a release.
- **arm64 is published but ungated**, exactly as for the service image. Nothing in this
  workflow has ever executed the emulated leg; it is built from the same commit and the
  same digest-pinned multi-arch base, and `docs/searxng.md` says so to consumers.
- Two new action pins were needed: none. The lane reuses `actions/checkout` v7.0.1,
  `astral-sh/setup-uv` v10.0.1, `docker/setup-buildx-action` v4.3.0,
  `docker/build-push-action` v7.3.0, `actions/upload-artifact` v7.0.1,
  `actions/download-artifact` v8.0.1, `docker/setup-qemu-action` v4.3.0,
  `docker/metadata-action` v6.2.0 and `docker/login-action` v4.6.0.
- Suite went 771 → **905** tests (+61 `test_searxng_smoke.py`, +19 net in
  `test_searxng_docker.py`, +52 workflow guards), all green.
  `kit_tools/testing/TESTING_GUIDE.md` carries the counts, the ten-job two-lane
  description, and three `test_mapping` entries (`searxng/Dockerfile`,
  `searxng_smoke.py`, and `searxng/config/*` unchanged).
- One throwaway branch was used and is deleted, with its run recorded above.
- `US-008's flip checklist now covers two packages that both exist`: `forage` and
  `forage-searxng`. The latter currently holds exactly one pre-release tag.

### US-008 — Public flip (2026-09-10, supervisor-executed human gate)

**The seven steps, in order, all recorded:**

| Step | Result |
|---|---|
| (a) secret-grep green on current images | run 34188019788 (HEAD `ca8c9f5`→rerun green) — `secret-grep: success` |
| (b) full-history gitleaks at HEAD | 88 commits scanned; **1 finding, triaged false positive** — the synthetic `Token: abc123def456` literal in the stage-2 structural test; recorded by fingerprint in the committed `.gitleaksignore` (`c50dab5`), re-scan **no leaks found** |
| (c) LICENSE/NOTICE | Apache-2.0 LICENSE + NOTICE present |
| (d) merge freeze | held from `c50dab5` (2026-09-08) through the flip (2026-09-10); zero pushes to main in the window |
| (e) purge pre-flip GHCR versions | both packages **deleted wholesale** (30 forage + 3 forage-searxng versions); `delete:packages` obtained via `gh auth refresh` on the owner's existing login — **no standalone PAT minted**; org listing confirmed empty |
| (f) flip public | repo: `gh repo edit --visibility public` ✓; packages: recreated private by the post-flip publishes, flipped in the UI (visibility change is UI-only) — **blocked first by the org's package-creation policy** (public disabled for members); fixed at org Settings → Packages → allow Public, then both flips succeeded |
| (g) lift freeze | lifted; `v0.9.1-rc` + `searxng-v0.1.1-rc` pushed at `c50dab5`, both lanes green (runs 34528502559 / 34528505149) |

**Anonymous-pull verification (the AC's own test):** with a scratch `DOCKER_CONFIG`
(no stored credentials), `docker pull` of `forage:0.9.1-rc` and
`forage-searxng:0.1.1-rc` both succeeded. On the pulled artifacts: OCI titles correct
(`forage-searxng` carries the US-004 supervisor-fix label), `docker history` secret grep
0 matches on both, and `contract_smoke.py` against the anonymously-pulled service image:
**PASSED — degraded, honest, and on-contract.**

**Deferred security re-applied in full at the flip** (per `docs/bootstrap-notes.md`):
secret scanning + push protection enabled; branch protection on `main` — the six required
checks `lint`/`typecheck`/`test`/`build-amd64`/`secret-grep`/`smoke` (`strict: false`),
PRs required, `required_approving_review_count: 0`, `enforce_admins: false`. **Direct
push to main is closed from here on** — this closeout is the first self-merged PR under
the new protection. `actionlint` correctly not listed (a step inside `lint`, not a job).

**Gate decisions executed as codified** (see the Gate decisions block above): break-glass
flag renamed pre-flip (`FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION`, alias kept, interim
name retired un-aliased); limiter posture shipped with the loud README warning; registry
purged then re-seeded with fresh gate-chain rc tags.

**Spec-6 secret-rotation follow-through (AC 2):** the `poppy-searxng-internal` placeholder
string in this repo's git history is Poppy's live compose value until spec 6 US-002/US-003
rotate it. The flip is safe — the string grants nothing outside poppy-net (it is a SearXNG
`secret_key` HMAC seed, not a credential to any reachable service) — but rotation at
spec 6 is registered there and cross-referenced here. The one remaining scope note:
`delete:packages` is to be dropped from the owner's gh login now that step (e) is done.

## Refinement Notes

### Research Findings

**Decision:** One workflow file, jobs introduced in dependency order, required checks
registered last
**Rationale:** `needs:` cannot span workflow files; an undefined `needs:` invalidates the
whole file; a required check with no reporting job deadlocks every PR (round-2 criticals).

**Decision:** Honest searxng hardening — drop IP-trust relaxations + secret, document the
Redis requirement, prove the JSON client path with a cross-container smoke
**Rationale:** round 2 established `limiter: true` alone is inert without a Redis backend
(config-text hardening only), the header-trust keys are client-spoofable, and dropping
`pass_ip`/`link_token` blindly risks breaking Forage's own `format=json` calls — so the
smoke, not an assertion, is the arbiter.

**Decision:** `searxng-v*` independent tag lane with explicit trigger + cross-fire guards
**Rationale:** `v*` doesn't match `searxng-v*`; without its own trigger the companion
image's publish path is unreachable (round-2 critical).

**Decision:** Committed guard tests replace one-off "verified once" proofs
**Rationale:** round-2 proof-by-throwaway pattern — Dockerfile-text guard, hermeticity
canary, config-regression pytest, workflow-shape checks leave artifacts, not anecdotes.

### Scope Adjustments

- Round 2 (2026-09-02): US-003 split three ways (image+grep / US-007 publish+checks /
  US-008 human flip); `execution_order` declared; required-check registration moved to
  US-007; CPU-lock regeneration moved to spec 1 with a grep AC here; base image
  digest-pinned; searxng hardening made honest (Redis-documented limiter, header-trust
  dropped, JSON smoke); moved-test fossils enumerated; smoke budget 30→120 s with log dump;
  `latest` scoped to non-prerelease; US-006 gains re-measure + escape hatch.

### Decisions Made

## Clarifications

### Session 2026-09-02
- Q: SearXNG config delivery to consumers? → A: Forage publishes `forage-searxng` (pinned
  base + baked config); round 1: public-safe defaults + Poppy overlay; round 2: "safe"
  redefined honestly (no IP-trust relaxations/secret; limiter requires Redis, documented and
  compose-wired; JSON smoke proves the client path).
- Q: Multi-arch from day one? → A: Yes, gated on an in-story feasibility check with a
  documented amd64-only fallback.
