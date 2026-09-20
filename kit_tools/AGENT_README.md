<!-- Template Version: 2.0.1 -->
# AGENT_README.md

> Last updated: 2026-09-19
> Updated by: Claude (close-session — in-flight epics, decisions count)

Navigation guide for AI assistants working in Forage. Forage is a ~5,100-line single
service, so this documentation set is deliberately smaller than a monorepo's — every file
listed below exists, and nothing is listed that doesn't.

---

## Quick Start — Read Order

**For general orientation:**
1. **This file** — how to navigate and what not to touch
2. **`../CLAUDE.md`** — the hard invariants. Read before writing any code.
3. **`SYNOPSIS.md`** — what Forage is, current state, tech stack, how to run it
4. **`arch/CODE_ARCH.md`** — module map, key patterns, deliberate non-structure
5. **`arch/SERVICE_MAP.md`** — the four outbound dependencies, the one consumer, and
   what breaks when each is down (the failure impact matrix)
6. **`docs/GOTCHAS.md`** — landmines. Short file, all of it load-bearing.
7. **`arch/DECISIONS.md`** — the *why* behind the invariants, reconstructed with sources.
   Read the entry before re-litigating a choice.

**Before changing code:**
8. **`docs/CONVENTIONS.md`** — style, imports, logging, config, commit format
9. **`testing/TESTING_GUIDE.md`** — commands, structure, the hermeticity rule
10. **`arch/patterns/ERROR_HANDLING.md`** — the closed 18-code vocabulary, the exception
    hierarchy, loud degradation. Read before adding any error path.
11. **`arch/patterns/LOGGING.md`** — closed log vocabularies, why INFO is invisible in a
    container, what may never be logged
12. **`arch/SECURITY.md`** — SSRF defences, injection signalling, secrets hygiene, supply
    chain. Required reading before touching `url_validator.py`, `pipeline/stage5_url_audit.py`,
    `cache.py`, `model_fetcher.py`, or the Dockerfile.

**For running and operating it:**
13. **`docs/LOCAL_DEV.md`** — clone to green suite to running container
14. **`docs/ENV_REFERENCE.md`** — every env var and `config.yaml` key at a glance, with read
    sites and traps (`../docs/configuration.md` stays canonical for semantics)
15. **`docs/API_GUIDE.md`** — how to call the five endpoints and read a response safely
    (`../contract/openapi.yaml` stays canonical for the schema)
16. **`docs/MONITORING.md`** — `/health` and `/metrics` field semantics, what reaches
    `docker logs`, suggested watch points
17. **`docs/TROUBLESHOOTING.md`** — symptom-first runbook with the full error-code reference
18. **`docs/DEPLOYMENT.md`** — pull, verify, pin, roll back; the Poppy coexistence rule
19. **`docs/CI_CD.md`** — the six gates, the publish job, the failure playbook
20. **`arch/INFRA_ARCH.md`** — the image, the compose fragments, the registry, the volume

**For the outside view (what a third party sees):**
21. **`../README.md`** — framing, deployment posture, licensing
22. **`../docs/configuration.md`** — the complete env + `config.yaml` reference
23. **`../contract/GOVERNANCE.md`** — the contract bump policy and the five rulings

**For planned work:**
24. **`specs/epic-forage-extraction-forage-side.md`** — the (completed) Forage-side epic;
    the four feature specs it wrapped are in `specs/archive/`
25. **`roadmap/MILESTONES.md`**, **`roadmap/BACKLOG.md`**
26. **`PRODUCT_VISION.md`** — currently the unfilled template; run `/kit-tools:create-vision`
    before planning the next epic

---

## Session Start Checklist

- [ ] Read `SYNOPSIS.md` for current state
- [ ] Read `../CLAUDE.md` for the invariants
- [ ] Check `specs/` for anything in flight: `epic-search-providers` shipped as `v1.1.0`
      (2026-09-18, archived); `epic-forage-hardening` (eight specs, validated to needs-work
      2026-09-19) is next to execute, then `epic-forage-injection-corpus` (five specs, planned
      2026-09-19, `validate-epic` pending). New specs land here via `/kit-tools:plan-epic`.
- [ ] Scan `docs/GOTCHAS.md`
- [ ] Confirm the environment: `uv sync --extra dev && uv run pytest` (expect ALL green, zero
      failures — the current count lives in `testing/TESTING_GUIDE.md`; 2105 as of
      `search-release` US-003)

**Flag anything that looks like:**
- A new coupling back to Poppy → stop, it is forbidden
- Code that would make a degradation silent → stop, that is the failure this repo exists to avoid
- Documentation that contradicts the code → fix the doc in the same change

---

## Session End Checklist

| File | Update if... |
|------|--------------|
| `SESSION_LOG.md` | Always — log what was done |
| `specs/*.md` | Working a feature — tick criteria, append Implementation Notes |
| `SYNOPSIS.md` | Status, stack, or test/lint counts changed |
| `arch/CODE_ARCH.md` | Modules added/removed, a pattern established |
| `arch/DECISIONS.md` | A choice was made that someone could later re-litigate — record it with its source |
| `arch/SERVICE_MAP.md` | A dependency, timeout, retry, or failure behaviour changed |
| `arch/SECURITY.md` | Any SSRF, injection-signalling, secrets, or supply-chain control changed |
| `arch/patterns/ERROR_HANDLING.md` | An error code, exception class, or degradation path changed (contract bump likely) |
| `arch/patterns/LOGGING.md` | A log vocabulary, logger, or level convention changed |
| `docs/CONVENTIONS.md` | A style or tooling rule changed |
| `docs/GOTCHAS.md` | You hit a landmine — write it down while it still hurts |
| `docs/ENV_REFERENCE.md` + `../docs/configuration.md` | Any env var or `config.yaml` key added/changed/removed — both, in the same change |
| `docs/API_GUIDE.md` + `uv run python -m scripts.export_contract` | Any endpoint, field, or error code changed — the guide embeds the contract anchor |
| `docs/MONITORING.md` | A `/health` or `/metrics` field, a `degraded_reasons` value, or a log marker changed |
| `docs/TROUBLESHOOTING.md` | A new failure mode, error code, or remedy |
| `docs/DEPLOYMENT.md`, `docs/CI_CD.md`, `arch/INFRA_ARCH.md` | Dockerfile, compose fragments, workflow jobs, tag scheme, or release procedure changed |
| `testing/TESTING_GUIDE.md` | Test counts, structure, commands, or `test_mapping` changed |
| `../README.md` | Framing, posture, quickstart, or licensing changed |
| `roadmap/MILESTONES.md` | Milestone progress changed |

---

## Patterns to Follow

### Code organization
- Modules are **flat at the repo root** (`retrieval_app.py`, `models.py`, `cache.py`,
  `url_validator.py`, `model_fetcher.py`). This is a decision — do not "tidy" them into a
  `forage/` package. `arch/DECISIONS.md` records why and what the rename would have to carry.
- Pipeline stages live in `pipeline/` as `stageN_<subject>.py`; the driver is
  `pipeline/orchestrator.py`; the error vocabulary is `pipeline/contract.py`.
- The ML classifier wrapper lives in `promptguard/`.
- Tests are flat under `tests/`, one module per subject.

### Imports
- **Never import from `poppy`.** Hard invariant; see `../CLAUDE.md`.
- `tests/conftest.py` puts the repo root on `sys.path` once — never add a per-module
  `sys.path.insert`.

### Security patterns
- **Never log a credential-bearing value.** `VALKEY_URL` may carry a password; `cache.py`
  and `model_fetcher.py` keep closed log vocabularies and tests assert them.
  `arch/patterns/LOGGING.md` has the exact strings.
- **SSRF is the standing threat.** Making outbound requests is Forage's job. Changes to
  `url_validator.py` or `pipeline/stage5_url_audit.py` (RFC1918 rejection, DNS-rebinding
  checks, manual redirect following with per-hop revalidation and IP pinning) are security
  changes — treat them as such. `arch/SECURITY.md` maps each control to its tests.
- **No authentication exists, by design.** Do not add half-measures that read as auth
  without being it. Network placement is the control; the README says so on its first
  screen.
- **No secret enters the image build.** The Dockerfile declares zero `ARG`s and
  `tests/test_dockerfile.py` asserts the absolute. Secrets arrive at runtime only.

### Error handling
- `/health` is always 200; honesty lives in the **body** (`status`, `degraded_reasons`).
- Errors are one of the 18 codes in `pipeline/contract.py`; adding one is a contract change
  (`../contract/GOVERNANCE.md`). Prefer machine-readable reason codes over prose in anything
  a consumer parses.
- A degradation must never be silent. That failure mode ran for nine days in production
  and is the reason this service reports the way it does. A configured-but-unreachable
  cache is `cache_unavailable`, never a quiet in-memory fallback.

---

## Off-Limits / Requires Human Review

Draft these, but do not apply without the owner's approval:

- [ ] **Pushing any image by hand, or any image built from Poppy's in-tree
      `services/retrieval/Dockerfile`** — that file still carries `ARG HF_TOKEN`. Forage's
      own image is secret-free and public, and it reaches GHCR only through the CI `publish`
      job behind the six gates (`docs/CI_CD.md`). Never `docker push` from a workstation.
- [ ] **Adding any build argument to the Dockerfile** — `CLAUDE.md` invariant 2 asserts the
      absolute, not just the secret-shaped names. Use a `RUN`-time mechanism instead
      (`dpkg --print-architecture` is the worked example).
- [ ] **Anything that weakens `url_validator.py` or the stage-5 redirect audit.**
- [ ] **Changing the response contract** (`pipeline/contract.py`, `models.py` response
      shapes, a `responses=` declaration, a `degraded_reasons` value) — versioned, with a
      consumer that refuses on a mismatch.
- [ ] **Configuring logging** (adding `basicConfig`, raising the root level, uvicorn log
      flags) — it changes what operators see in `docker logs`. Recorded, not fixed;
      `arch/patterns/LOGGING.md`.
- [ ] **Renaming modules into a `forage/` package** — deferred by decision.
- [ ] **Adding a dependency**, or re-locking without the CPU-torch index config.
- [ ] **Secrets or API keys** — never commit; there is no secret store in this repo.
- [ ] **Editing `../contract/openapi.yaml` or its `.sha256` by hand** — both are generated;
      run `uv run python -m scripts.export_contract`.

When in doubt, ask before applying.

---

## Documentation Structure

```
kit_tools/
├── AGENT_README.md          # This file
├── SYNOPSIS.md              # What Forage is, current state, how to run it
├── SESSION_LOG.md           # Development session history
├── PRODUCT_VISION.md        # Unfilled template until /kit-tools:create-vision runs
├── SEED_MANIFEST.json       # Seeding progress (committed); .seed_cache/ is gitignored
├── model_preferences.json   # KitTools model roles
├── worktree.yaml            # Worktree & environment contract (committed)
├── hooks/                   # KitTools automation hooks (registered in .claude/settings.local.json)
│
├── arch/
│   ├── CODE_ARCH.md         # Module map, patterns, deliberate non-structure
│   ├── DECISIONS.md         # Decision log with sources (20 entries; reconstructed 2026-09-13, appended since)
│   ├── INFRA_ARCH.md        # Image, compose fragments, registry, volume, resource envelope
│   ├── SECURITY.md          # Posture, SSRF, injection signalling, secrets, supply chain
│   ├── SERVICE_MAP.md       # Dependencies, consumer, failure impact matrix, cache key scheme
│   └── patterns/
│       ├── ERROR_HANDLING.md  # The 18-code vocabulary, exception hierarchy, degradation
│       └── LOGGING.md         # Closed vocabularies, levels, what never to log
│
├── docs/
│   ├── API_GUIDE.md         # Usage guide for the five endpoints (schema: ../contract/openapi.yaml)
│   ├── CI_CD.md             # Six gates, publish job, failure playbook
│   ├── CONVENTIONS.md       # Style, imports, logging, config, commits
│   ├── DEPLOYMENT.md        # Pull, verify, pin, roll back; Poppy coexistence
│   ├── ENV_REFERENCE.md     # Env vars and config.yaml at a glance (semantics: ../docs/configuration.md)
│   ├── GOTCHAS.md           # Known landmines
│   ├── LOCAL_DEV.md         # Clone to green suite to running container
│   ├── MONITORING.md        # /health, /metrics, docker logs, watch points
│   ├── TROUBLESHOOTING.md   # Symptom-first runbook, full error-code reference
│   └── feature_guides/
│       └── FEATURE_TEMPLATE.md   # Copy for per-feature guides
│
├── specs/                   # The epic wrapper + templates; completed specs in archive/
│   ├── epic-forage-extraction-forage-side.md
│   ├── EPIC.md / FEATURE_SPEC.md / SCHEMA.md   # templates + frontmatter reference
│   └── archive/             # feature-forage-{ci-and-image,model-bootstrap,cache-fallback,contract}.md
│
├── testing/
│   └── TESTING_GUIDE.md     # Commands, structure, test_mapping
│
└── roadmap/
    ├── MILESTONES.md
    └── BACKLOG.md
```

Repo-root docs that are **not** under `kit_tools/` but matter just as much:
`../CLAUDE.md`, `../README.md`, `../SECURITY.md`, `../NOTICE`,
`../contract/GOVERNANCE.md`, `../.github/pull_request_template.md`,
`../docs/configuration.md`, `../docs/releases.md`, `../docs/weights.md`,
`../docs/searxng.md`, `../docs/bootstrap-notes.md`, `../docs/bootstrap-scan.txt`.

`SECURITY.md` and `contract/GOVERNANCE.md` arrived with `feature-forage-contract` US-003
and live at the **repo root / beside the artifact they govern**, not under `kit_tools/` —
they are read by contributors and consumers, not only by agents. `tests/test_governance_docs.py`
holds their mechanical claims to the code. The kit_tools `arch/SECURITY.md` is the
architecture view; the root `SECURITY.md` is the disclosure policy. Do not merge them.

**Two docs have a canonical source elsewhere and are guides, not copies.** `docs/API_GUIDE.md`
explains how to call the API; `../contract/openapi.yaml` (sha256-anchored) is the schema.
`docs/ENV_REFERENCE.md` is the operator's table with read sites and traps;
`../docs/configuration.md` carries the semantics. When either canonical source changes, the
guide changes in the same commit.

Templates this project deliberately does not carry: `arch/DATA_MODEL.md` (no database; the
Valkey/in-memory cache key scheme is in `arch/SERVICE_MAP.md`), `docs/UI_STYLE_GUIDE.md`
(headless), `arch/patterns/AUTH.md` (no authentication, by design). Add one only when there
is real content for it.

---

## File Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Feature specs | `feature-<kebab-name>.md` | `feature-forage-ci-and-image.md` |
| Epic wrappers | `epic-<kebab-name>.md` | `epic-forage-extraction-forage-side.md` |
| Source modules | `snake_case.py` | `url_validator.py` |
| Pipeline stages | `stageN_<subject>.py` | `stage2_structural.py` |
| Test modules | `test_<subject>.py` | `test_cache.py` |
| Decision entries | `### <ISO date>: <short title>` in `arch/DECISIONS.md` | `### 2026-09-07: Forage is standalone` |

---

## Documentation Standards

Every `kit_tools/` doc carries, right under its title:

```markdown
> Last updated: 2026-09-17
> Updated by: [Human/Claude]
```

The canonical field is exactly `Last updated: YYYY-MM-DD`. The `update_doc_timestamps.py`
hook in `hooks/` refreshes it on edit.

---

## Spec Sync Rule (while the extraction finishes)

The four Forage-side feature specs were **authored in Poppy** and copied here at bootstrap.
**Poppy's copies are canonical**; edits flow Poppy → Forage, one way, at handoff only.
Poppy's copies are now `status: on-hold` and its epic wrapper marks them "Moved to Forage —
do not execute here", so execution happens **only here**. Do not edit the Poppy originals
to reflect work done here — record that work in this repo's Implementation Notes. All four
are complete and archived under `specs/archive/`; the rule still governs any follow-up
edits to them.
