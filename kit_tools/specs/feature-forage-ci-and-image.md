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
- [ ] `test` job green on PR + main; sanitizer-revision step present; hermeticity canary
      committed and passing.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

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
- [ ] Dockerfile: no HF build-arg/bake, `uv.lock`-driven install, digest-pinned base;
      Dockerfile-text guard test committed.
- [ ] `build-amd64` + `secret-grep` jobs green with the save/upload/load/digest-equality
      handoff (asserted, not assumed); grep pattern set defined in-workflow; metadata-scope
      caveat documented in the job comment.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

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
- [ ] `smoke` job green via the artifact handoff (download + load + digest-equality
      asserted against `build-amd64`'s recorded ID), 120 s budget, log dump on failure,
      field source shared with the golden-schema machinery.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes

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
