<!-- Template Version: 2.0.0 -->
# SESSION_LOG.md

> Running history of development sessions. Enables continuity across sessions.

---

## Log Format

```
## YYYY-MM-DD — [Brief Title]

**Duration:** ~X hours
**Focus:** [Main topic/feature]

### Accomplished
- [What was done]

### Documentation Updated
- [x] [File updated]

### Decisions
- [Non-obvious choice and why]

### Open / Next
- [What the next session should pick up]
```

---

## 2026-09-07 — Repo bootstrap (Poppy spec `forage-repo-bootstrap`)

**Duration:** one supervised session
**Focus:** Creating this repository out of Poppy and making it self-sufficient.

### Accomplished

- **US-001** — history-preserving split via `git filter-repo` over three Poppy paths
  (`services/retrieval/`, `config/searxng/`, `tests/retrieval/`) into a flat layout.
  47 commits, census 25/2/18, all 45 blobs sha-identical to Poppy `f73e2091`. Full-history
  secret scan clean (one triaged non-credential synthetic fixture). Token-less
  `docker build` green. Pre-bootstrap tip `93acb6a`.
- **US-003** — identity: Apache-2.0 `LICENSE` + Llama `NOTICE`, `pyproject.toml` renamed to
  `forage` with a working hatchling wheel target, `requirements.txt` deleted,
  `.dockerignore` added (context 33 files / ~210 KB), README written.
- **US-004** — test suite green from a fresh clone on macOS **and** linux/amd64: 531
  passed. Cross-repo `poppy.*` import and taxonomy assertion deleted (re-homed to Poppy);
  `tests.retrieval.` references swept; `tests/conftest.py` replaces the per-module
  `sys.path` dance and adds the `pytest-socket` hermeticity guard (which immediately
  caught three real DNS calls); CPU-pinned `uv.lock` committed.
- **US-002** — vault-free runtime configuration: 17-line `exec "$@"` entrypoint replacing
  the 120-line AppRole/vault shell, Forage-neutral hostname defaults,
  `FORAGE_LEGACY_CAPABILITY` alias with exact `== "1"` semantics on both names,
  `docs/configuration.md` written, stale Poppy-network posture comments corrected.
  Suite 534 (531 + 3 alias parametrizations).
- **US-005** — this scaffold: `kit_tools/` with `worktree.yaml`, `model_preferences.json`,
  the four Forage-side feature specs + a Forage-local epic wrapper, and `CLAUDE.md`.

### Documentation Updated

- [x] `README.md`, `NOTICE`, `LICENSE`
- [x] `docs/configuration.md`, `docs/bootstrap-notes.md`, `docs/bootstrap-scan.txt`
- [x] `CLAUDE.md`
- [x] `kit_tools/` — AGENT_README, SYNOPSIS, this log, arch/CODE_ARCH, docs/CONVENTIONS,
      docs/GOTCHAS, testing/TESTING_GUIDE, roadmap stubs

### Decisions

- **Flat module layout preserved.** Keeps the Dockerfile, `sanitizer_revision`'s hashed
  source paths, and the moved suite working unchanged. A `forage/` package rename is
  deferred.
- **`sanitizer_revision` deliberately diverged** from Poppy (`e6b2b56d…` → `2b8d7e9a…`)
  when the neutral hostname defaults landed. Nothing downstream may assume parity.
- **Repo stays private** until the CI spec's public flip; the Dockerfile still bakes an
  HF token recoverable via `docker history`.
- **Secret scanning, push protection, and branch protection are deferred** — unavailable
  on a Free-plan private repo (422/403 recorded). Stories landed via supervised direct
  push. Re-application calls are in `docs/bootstrap-notes.md`.

### Open / Next

- Execute `feature-forage-ci-and-image` **here**, autonomously — it owns CI, the
  format/pyright backlog (6 files / 214 errors), image publishing, and the public flip
  (US-008 is a human gate).
- Poppy's in-tree copy remains the deployed source of truth until it pins a Forage image.
  Replay any hotfix to the extracted paths across both repos until then.

---

## 2026-09-13 — kit_tools framework retrofit and documentation seeding

**Duration:** ~2.5 hours wall clock (including two laptop-sleep stalls that each cost one seeder relaunch or wait)
**Focus:** `/kit-tools:init-project` (merge mode) followed by `/kit-tools:seed-project`

### Accomplished

- **init-project (merge, API/Backend set, ERROR_HANDLING + LOGGING patterns):** 17 templates
  added without touching the 14 existing docs; `kit_tools/hooks/` installed (7 automation
  scripts plus the `_placeholders.py` helper two of them import);
  `.claude/settings.local.json` created with the SessionStart / PostToolUse / PreCompact /
  Stop registrations; Session Scratchpad section appended to `CLAUDE.md`; `.gitignore`
  KitTools block and `worktree.yaml` were already correct; lint commands already recorded;
  plugin doctor HEALTHY (0 errors, 0 warnings).
- **seed-project:** six focused explorations run in parallel and cached under
  `.seed_cache/` (tech-stack 206, infrastructure 208, dependencies 218, security 395,
  operations 469, architecture 311 lines; all high confidence). Fourteen templates seeded
  sequentially by tier, each written by a `generic-seeder` agent that verified names,
  constants, and paths against source before writing.
- **Validation:** all 21 seeded docs clean on the placeholder scan (the only two hits are
  the intentional date-format examples in `AGENT_README.md` and this file's Log
  Format block); ~1,085 backticked path references across the 14 new docs checked, every
  one resolves or is a deliberate mention of an absent file (`arch/DATA_MODEL.md`,
  `app.py` as the historical name, CI artifact names).

### Documentation Updated

- [x] New (seeded): `docs/LOCAL_DEV.md`, `arch/SERVICE_MAP.md`, `arch/INFRA_ARCH.md`,
      `arch/SECURITY.md`, `docs/MONITORING.md`, `docs/CI_CD.md`, `docs/TROUBLESHOOTING.md`,
      `docs/API_GUIDE.md`, `docs/ENV_REFERENCE.md`, `docs/DEPLOYMENT.md`,
      `arch/patterns/ERROR_HANDLING.md`, `arch/patterns/LOGGING.md`, `arch/DECISIONS.md`
      (19 entries, dates from source text or first-introducing commits)
- [x] Rewritten: `AGENT_README.md` (read order for 26 docs, session-end checklist, stale
      off-limits entries replaced, documentation tree, canonical-source rule)
- [x] `CLAUDE.md` (scratchpad section only), `SEED_MANIFEST.json` (created)
- [x] Deleted: `arch/DATA_MODEL.md` (no database; the template says to delete it)
- [ ] Not touched: `SYNOPSIS.md`, `arch/CODE_ARCH.md`, `docs/CONVENTIONS.md`,
      `docs/GOTCHAS.md`, `testing/TESTING_GUIDE.md`, `roadmap/*`, `PRODUCT_VISION.md`
      (still the unfilled template; `/kit-tools:create-vision` owns it)

### Decisions

- **Pre-existing seeded docs were not re-seeded.** seed-project's default scope is "all
  templates", but the seven docs seeded during the extraction epic are curated and passed
  validation; overwriting them with agent output would have been destructive. They are
  marked `seeded` in the manifest with a note.
- **`arch/SECURITY.md` was seeded although `skip_if: no-auth` is literally met.** Forage's
  security architecture is substantial and non-auth-centric; the auth sections were reframed
  as "none by design, network placement is the control".
- **`docs/API_GUIDE.md` and `docs/ENV_REFERENCE.md` are guides, not copies.**
  `contract/openapi.yaml` and `docs/configuration.md` remain canonical; the guides add usage,
  read sites, and traps, and must change in the same commit as their source.
  `AGENT_README.md` records the rule (it previously listed both as deliberately absent).
- **`arch/DECISIONS.md` is a retrospective log, labelled as such.** Where a source records
  only the chosen path, "Options Considered" says "not recorded" rather than inventing
  alternatives.
- **Explorations parallel, seeding sequential.** No `--parallel` flag was given, so each tier
  seeded one template at a time and later docs could cross-reference earlier ones.

### Open / Next

- **Owner rulings surfaced by the seeding** (recorded in the docs as observations, nothing
  was changed in code or root docs):
  1. `README.md` and `docs/configuration.md` say `/health` reports degraded without SearXNG;
     the code has no probe and `DegradedReason` has only two members. Fix the docs, or add a
     reason (a MINOR contract change).
     *(Resolved on 2026-09-16: `search-release` US-001 corrected both sentences — an
     unreachable SearXNG surfaces per request as a `/search` 422 (`searxng_unavailable`),
     never a `degraded_reasons` value — and closed out the same discrepancy recorded in
     `kit_tools/docs/MONITORING.md`, `kit_tools/arch/SERVICE_MAP.md` and
     `kit_tools/AGENT_README.md`.)*
  2. `compose/minimal.yml` and `compose/full.yml` still pin `forage:0.9.3-rc` and
     `forage-searxng:0.1.1-rc` although `v1.0.0` is tagged.
     *(Resolved on 2026-09-17: `search-release` US-004 moved both fragments' `forage` pin to
     `ghcr.io/washingbearlabs/forage:1.1.0` — it resolves once `v1.1.0` publishes (US-002), and
     a `docker compose up` before that fails with `manifest unknown`, which is sequencing, not
     breakage — and swept the old literal out of `kit_tools/docs` and `kit_tools/arch`. The
     `forage-searxng:0.1.1-rc` pin stays: no non-pre-release `searxng-v*` tag exists.)*
  3. `contract_smoke.py` hard-codes `EXPECTED_STATUS = "degraded"`; its fitness as a
     weights-loaded production probe needs a decision.
     *(Resolved on 2026-09-17: `search-release` US-004 added `--expect-status {healthy,degraded}`
     (default `degraded`, so CI's invocation is unchanged) — under `healthy` the three
     PromptGuard-coupled checks invert — and made the wait status-aware, polling until
     `/health`'s `status` matches rather than returning on the first 200. `--anchor` lets a
     release image be verified from any checkout against the committed anchor at the tag.
     `degraded` matches a container started with no weights, `healthy` one started with them.)*
  4. `/search` scans at the hard default threshold 0.85 and ignores `config.yaml`
     `promptguard_threshold`; undocumented whether intentional.
  5. `fetch_error` and `searxng_unavailable` wire bodies interpolate `str(exc)`;
     `TooManyRedirectsError` collapses into generic `fetch_error`.
     *(Resolved for search on 2026-09-15: `search-provider-abstraction` US-002 replaced
     `searxng_unavailable`'s `str(exc)` with a closed provider `detail` token and its raw
     `SEARXNG_URL` echo with a userinfo-stripped scheme/host/port. `fetch_error` and the
     `TooManyRedirectsError` collapse are unchanged.)*
  6. Logging is unconfigured (INFO invisible in containers): recorded, not fixed.
  7. No container hardening in compose (`read_only`, `cap_drop`, `no-new-privileges`,
     `pids_limit`), no dependency-vulnerability scanning, no image signing/SBOM.
  8. Drift in the pre-existing `docs/GOTCHAS.md` (not re-seeded this run): the "uv.lock
     must stay CPU-pinned" entry still says the image pip-installs from the CPU index and
     never reads the lock, stale since `ci-and-image` US-003 (the Dockerfile runs
     `uv sync --locked`); and it dates the `app.py` → `retrieval_app.py` rename to
     2026-08-04 while the rewritten history shows commit `1baa58b` on 2026-06-12.
     `arch/DECISIONS.md` follows the Dockerfile and git; `/kit-tools:sync-project` should
     reconcile GOTCHAS.
- Run `/kit-tools:create-vision` (PRODUCT_VISION.md is unfilled), then `/kit-tools:plan-epic`.
- Commit the new `kit_tools/` files; `git status` shows them untracked. `.seed_cache/` is
  gitignored by the existing `kit_tools/.*` rule; `SEED_MANIFEST.json` is meant to be committed.
- `docs/API_GUIDE.md` embeds the contract anchor sha256 verbatim; update it whenever
  `scripts.export_contract` regenerates the contract.

---

## 2026-09-15 — Vision + epic intake and `validate-epic search-providers` (five rounds)

**Duration:** ~6 hours wall clock across two calendar days (one usage-limit interruption)
**Focus:** Verify the injected vision and three epics, then validate `epic-search-providers` to
execution readiness.

### Accomplished

- Verified the vision and the three epics / five feature specs another session injected: correct
  locations, readable, schema-valid. Fixed every `vision_ref` (all pointed at a heading that did
  not exist) to the vision's `T1.x`/`T2.x` headings, including the completed extraction epic.
- Pre-validation alignment pass surfaced six design questions; owner rulings recorded as
  epic rulings 8–11 (422 stays 422; env-var chain; `FORAGE_*` names; revision rotation).
- `/kit-tools:validate-epic search-providers`, full six-reviewer panel, five rounds:
  criticals 42 → 15 → 8 → 1 → 0. Rulings 12–34 recorded in the epic with a resolution map.
  Owner decisions along the way: **no spend ceiling in this epic** (v2), failure taxonomy in
  spec 1's seam, `search_unavailable` lands with the 1.2.0 bump, `capabilities` presence map for
  key presence, restrict-only per-request policy, `unresponsive_engines` counts as a free-path
  failure, no in-request retries, strict `date` validation, hardened release plumbing in a new
  spec 5 US-004, `/metrics` counters as the observability floor, Brave sample capture declared
  as a human gate, provider identity `brave` (name) vs `brave-api` (engine).
- Final matrix: every cell ⚠️ or ✅, no 🔴; worst readiness 6 (brave-provider/security,
  search-fallback/security, policy/completionist, release/codebase-fit). Overall: needs-work.
  `kit_tools/.validate_epic_summary.json` written; 30 `spec.validate.scored` events emitted.

### Documentation Updated

- [x] `kit_tools/PRODUCT_VISION.md` (wording fixes only), `kit_tools/specs/epic-search-providers.md`
      (rulings 8–34, resolution map, decomposition table), all five `feature-search-*.md` /
      `feature-brave-provider.md` (rewritten in round 1, revised in rounds 2–5),
      `kit_tools/specs/epic-forage-extraction-forage-side.md` (`vision_ref` only)
- [x] `kit_tools/SEED_MANIFEST.json`, this log

### Decisions

- Pre-existing seeded docs were never re-seeded; the specs assign their updates to stories.
- The remaining 121 warnings are accepted as execution-time detail; the reviewers are now
  finding line-level items, and each round's criticals were verified resolved by the reviewer
  that raised them.

### Open / Next

- Commit the whole `kit_tools/` tree (vision, epics, specs, seeded docs, manifest, hooks) — all
  still untracked or modified.
- Replay rulings 8–34 into Poppy's `EPIC3_SEARCH_RELIABILITY_SPLIT.md` at the next handoff
  (one-way sync; Poppy's copy is stale on every ruling).
- Spec 2 US-001's pre-flight human gate: the owner must capture the Brave sample before
  execution reaches it.
- Run `/kit-tools:execute-epic search-providers`.
- `epic-forage-hardening` and `epic-forage-injection-corpus` remain on-hold stubs needing
  `/kit-tools:plan-epic`.

---

## 2026-09-18 — `search-release` US-003: post-release verification + Poppy handoff

**Duration:** ~1 hour
**Focus:** Verify the published `v1.1.0` image the way a third party reaches it, and record the
handoff table `epic-search-providers`' Poppy counterpart pins from.

### Accomplished

- `docker logout ghcr.io` + anonymous `docker pull` of the `v1.1.0` digest, both recorded.
- Brought the pulled image up from `compose/minimal.yml` on the key-less floor: `/health` reports
  `contract_version: "1.2.0"`, `search_providers: ["searxng"]`, no `brave_api_key` in
  `capabilities`; one `/search` round-trip returned `provider_used: "searxng"`,
  `fallback_fired: false` on the first try.
- Re-ran the same image with a placeholder `FORAGE_BRAVE_API_KEY` via a throwaway `--env-file`:
  `capabilities.brave_api_key: 1`, and the placeholder appears zero times across `/health`,
  `/metrics` and `docker logs`. No `/search` issued in that run — zero spend.
- Completed `kit_tools/specs/feature-search-release.md`'s Implementation Notes with the handoff
  table (tag, index digest, contract, anchor, spend posture, paid-path evidence) and ticked every
  Completion Criterion in `kit_tools/specs/epic-search-providers.md` — the epic's last piece.
- `docs/releases.md` gained a "Released versions" section (`v1.0.0`, `v1.1.0`) and lost the stale
  "`latest` therefore does not exist yet" claim.
- Suite-count bookkeeping: `uv run pytest` now reports 2089 (was 1610/1713 in stale docs);
  updated `testing/TESTING_GUIDE.md`, `SYNOPSIS.md`, `AGENT_README.md` and `../CLAUDE.md`.

### Documentation Updated

- [x] `kit_tools/specs/feature-search-release.md` (US-003 Implementation Notes),
      `kit_tools/specs/epic-search-providers.md` (Completion Criteria, all six)
- [x] `docs/releases.md`, `kit_tools/SYNOPSIS.md`, `kit_tools/docs/DEPLOYMENT.md`,
      `kit_tools/docs/CI_CD.md`, `kit_tools/arch/INFRA_ARCH.md` (v1.1.0 as shipped fact)
- [x] `kit_tools/testing/TESTING_GUIDE.md`, `kit_tools/AGENT_README.md`, `../CLAUDE.md` (test count)
- [x] `kit_tools/PRODUCT_VISION.md` (T2.1 → Shipped), `kit_tools/roadmap/MILESTONES.md` (Done entries)

### Decisions

- Docker was logged back into `ghcr.io` after the anonymous-pull proof (via the owner's `gh`
  token) rather than left logged out, so the workstation's push access for future releases was
  not disturbed by this story's verification step.
- `skopeo` is not installed on this workstation; the anonymous `docker pull` is the sole
  credential-free witness recorded (the hint's second witness is optional and skipped).

### Open / Next

- `epic-search-providers` is complete; Poppy's `epic-search-policy` session reads the handoff
  table in `feature-search-release.md` to pin the `v1.1.0` digest — nothing here pushes to Poppy.
- `epic-forage-hardening` and `epic-forage-injection-corpus` remain on-hold stubs needing
  `/kit-tools:plan-epic`.

## 2026-09-16 → 2026-09-19 — `epic-search-providers` supervised to `v1.1.0`; loose ends; `forage-hardening` planned and validated; `forage-injection-corpus` planned

**Duration:** four days of supervised execution and planning (2026-09-16 → 2026-09-19), across
several context windows
**Focus:** Supervise `epic-search-providers` to `v1.1.0`, clear the loose ends, plan and validate
`epic-forage-hardening`, plan `epic-forage-injection-corpus`.
**Feature specs:** `epic-search-providers` (all five specs, archived — executed by the guarded
orchestrator under supervision); `epic-forage-hardening` + `feature-hardening-*.md` (eight,
planned and validated); `epic-forage-injection-corpus` + `feature-corpus-*.md` (five, planned).

### Accomplished

- **Supervision of the guarded run (09-16 → 09-18).** Three stories that kept timing out at the
  M-size 900 s cap were split by the supervisor (spec 2 US-001 → US-010/US-011, spec 2 US-003 →
  US-012/US-013, spec 4 US-001 → US-010/US-011); spec 4 US-010's exhausted attempt was salvaged
  from the reflog (`8f7dd0f`) and finished as a follow-up story; the orchestrator's 24 h safety net
  fired and the run was relaunched with `size: L` on specs 4–5. The Brave owner gate was met with
  an envelope-only synthetic fixture (`9794cba`); PR #23 merged as `06b01b1`; `v1.1.0` was cut
  and verified (four-way sha256 `11435a17…`); the orchestrator closed 25/25 stories in 36 attempts
  / 93 sessions and opened PR #24 (merged `5bbc30b`); worktree, branch and registry torn down.
- **Loose ends (09-19).** Rulings 8–34 and the `v1.1.0` handoff replayed into Poppy's
  `EPIC3_SEARCH_RELIABILITY_SPLIT.md` (Poppy `b56a47fc`, **not pushed**). The 83 advisory audit
  findings triaged: 42 fix-now landed as PR #25 (`c311464`; code+tests `effe060`, docs `a2b1614`;
  2105 tests green), 20 folded into the hardening epic, 1 into the corpus epic, the rest deferred,
  fixed or dismissed with statuses in `AUDIT_FINDINGS.md`.
- **`epic-forage-hardening` planned (PR #27, `642c700`) and validated (PR #28, `20ddb2a`).**
  Eight specs, 42 stories, one contract window 1.2.0 → 1.3.0; five validation rounds drove
  criticals 51 → 32 → 28 → 21 → 15 (round 5 applied without re-review); 44 wrapper rulings; 46
  known-risk bullets; summary written as `needs-work`, 48 trace events emitted.
- **`epic-forage-injection-corpus` planned (PR #29, open).** Stub replaced by a wrapper (owner
  decisions 1–4, rulings 5–16) and five specs / 21 stories: harness, attacks, benign, recording
  (two owner gates), gates. Landscape research (16 sourced findings) folded in: licences read at
  source (AgentDojo / LLMail-Inject / CyberSecEval in; BIPIA / WASP out), Prompt Guard 2's dropped
  injection label → stage-2 / stage-3 catch reported separately, Prompt Overflow density and Zenity
  repetition as sweep categories, cassette miss as a hard error.

### Documentation Updated

- [x] `kit_tools/specs/` — hardening wrapper + eight specs (validation close-out), corpus wrapper +
      five specs; `kit_tools/.validate_epic_summary.json`
- [x] `kit_tools/roadmap/BACKLOG.md`, `kit_tools/roadmap/MILESTONES.md`, `kit_tools/PRODUCT_VISION.md`
      (T2.2 validated, T2.3 planned)
- [x] `kit_tools/arch/DECISIONS.md` (recorded-score cassettes; weights never in CI)
- [x] `kit_tools/AGENT_README.md` (in-flight epics), `kit_tools/SYNOPSIS.md` (status)
- [x] `kit_tools/AUDIT_FINDINGS.md` (triage statuses, gitignored)
- [x] Poppy: `kit_tools/specs/EPIC3_SEARCH_RELIABILITY_SPLIT.md` (rulings replay, unpushed)

### Decisions

- Recorded-score cassettes over weights in CI (see `arch/DECISIONS.md` 2026-09-19).
- Hardening: one contract window per epic, opened in spec 1 US-004 and frozen in spec 8 US-002;
  cache HMAC with an optional key that is loud when absent; fail-closed is an operator floor, off
  by default; the model is configurable with 22M staying the default; benchmark and release cut are
  owner gates.
- Validation was closed at `needs-work` rather than chased to zero: the two precision reviewers
  surfaced new spec-precision items every round; remaining warnings live in each spec's Known-risks
  section.
- Supervisor practice: split a story on repeated size-cap timeouts when the last attempt was
  substantively right; check `git reflog` before assuming a failed attempt's work is gone; a
  queued `pause` control is not self-clearing.
- The corpus epic flips no security default (contiguity, 86M); it produces the decision table.

### Open / Next

- Merge PR #29 (owner), then `/kit-tools:validate-epic forage-injection-corpus`.
- Execute `epic-forage-hardening` (`/kit-tools:execute-epic`, guarded; owner gates in spec 7
  US-005/US-004 and spec 8 US-003/US-005), then the corpus epic (`depends_on: hardening-release`).
- Push Poppy `b56a47fc` when the owner asks (not authorised from here).
- Standing: never put a token on a command line; corpus payloads are data, never quoted.
