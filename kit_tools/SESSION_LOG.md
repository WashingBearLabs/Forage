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
