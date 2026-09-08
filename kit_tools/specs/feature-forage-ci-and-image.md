<!-- Template Version: 2.5.0 -->
---
feature: forage-ci-and-image
status: active
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
updated: 2026-09-07
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
without a Redis backend is inert theater, so the hardening is: no IP-trust relaxations, no
baked secret, and a documented Redis requirement for real rate limiting, proven by a
cross-container JSON smoke). All jobs live in **one workflow file** so `needs:` edges are
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
- [ ] `publish` with the full `needs:` list; semver/`latest`/`sha-` tag policy implemented
      as stated; Release on `v*`; multi-arch or the documented fallback.
- [ ] Green publish run URL + red-rehearsal run URL recorded (supervised tag pushes).
- [ ] Required status checks registered (actor recorded); `docs/releases.md` written.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

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
  format, and sets `limiter: true` **with the Redis requirement documented**: real limiting
  requires a Redis/Valkey backend configured via the env var this story verifies against
  the pinned digest (working name `SEARXNG_REDIS_URL` — **pending verification**, see the
  smoke bullet) — the
  spec-4 compose fragments wire it; without it the image logs the inert-limiter warning.
  Whether `link_token` can be enabled without breaking the JSON client is answered by the
  smoke below, not assumed.
- **Cross-container smoke, split blocking/advisory** (round-3 critical — a live
  third-party engine query in a publish `needs:` chain reproduces the exact
  every-engine-throttled outage GOTCHAS records, with no break-glass): the **blocking**
  half is hermetic — searxng + a Redis/Valkey container + a curl container on one network;
  assert the limiter actually initialized (no inert-limiter warning in logs), a
  `format=json` request returns HTTP 200 with a parseable envelope carrying a `results`
  key and no bot-detection block page (proves baked config + `link_token` compatibility
  with the API client path); the **advisory** half (real engine results non-empty) runs
  non-blocking with its outcome logged. Real limiting requires a Redis/Valkey backend
  configured via the env var **whose name this story verifies** (see next bullet — do not
  treat any name as upstream-supported until verified). **Verify the actual Redis env-var name against the
  pinned digest in-story** — `SEARXNG_REDIS_URL` is an unverified name that exists nowhere
  in this repo, and upstream renamed this setting family toward Valkey during 2025
  (round-3 critical); record the verified name, and propagate the correction to the two
  sibling specs that cite it (cache-fallback US-004, poppy-consume US-002).
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
- [ ] `forage-searxng` published under `searxng-v*` via its own gated lane; base
      digest-pinned; no bind mount needed.
- [ ] Baked config: no `pass_ip` wildcard, no baked secret, no client-header trust;
      limiter-with-Redis documented; config-regression pytest committed.
- [ ] Blocking hermetic smoke (redis-backed limiter initialized + JSON envelope + no
      bot-block page) green in the lane; advisory live-engine half non-blocking; the real
      Redis env-var name verified against the pinned digest, recorded, and corrected in the
      two citing sibling specs; `SEARXNG_SECRET` set/unset behavior verified + documented.
- [ ] All four moved config-half tests updated (two negatives, limiter re-points,
      string-split parity) and green.
- [ ] `docs/searxng.md` complete per hints.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

### US-008: Public flip (human gate)

**Priority:** P3 *(deliberately last and human-executed; the epic's downstream specs need it
done before spec 6 pulls images, but nothing in THIS spec depends on it)*

**Description:** As the owner, I want the repo and both GHCR packages flipped public exactly
once, behind every gate, with the window controlled.

**Independent Test:** Anonymous `docker pull` of both images succeeds; the flip checklist is
recorded.

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
- [ ] Seven-step checklist executed in order and recorded; anonymous pulls of both images
      verified.
- [ ] The spec-6 secret-rotation follow-through cross-reference recorded.

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
* US-007 is the right place, because `publish` is the last consumer and the `needs:` list
  is complete by construction there. `_IMAGE_CONSUMERS` in `tests/test_ci_workflow.py` is
  the list to extend when it lands — every consumer is then held to the same
  download-and-assert contract by parametrization.

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
- **US-007's `publish` must download the same artifact and assert the same identity.** Add
  `"publish"` to `_IMAGE_CONSUMERS` and the two parametrized handoff tests cover it for
  free. `needs:` is still only ordering.
- The two action pins this story reuses (`actions/download-artifact` v8.0.1,
  `actions/checkout` v7.0.1) are US-001/US-003's; no new third-party action was added.
- Suite went 656 → **731** tests (+47 `test_contract_smoke.py`, +28 workflow guards), all
  green. `kit_tools/testing/TESTING_GUIDE.md` carries the new counts, the six-job list, a
  copy-pasteable local smoke recipe, and two `test_mapping` changes (`contract_smoke.py`
  is new; `retrieval_app.py` now maps to two modules).
- `kit_tools/docs/GOTCHAS.md`'s PromptGuard entry gained the paragraph that makes it
  honest: the "never report healthy without weights" rule is no longer something a
  reviewer has to remember — it is a red workflow.

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
