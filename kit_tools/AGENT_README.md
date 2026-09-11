<!-- Template Version: 2.0.1 -->
# AGENT_README.md

> Last updated: 2026-09-07
> Updated by: Claude (forage-repo-bootstrap US-005)

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
5. **`docs/GOTCHAS.md`** — landmines. Short file, all of it load-bearing.

**Before changing code:**
6. **`docs/CONVENTIONS.md`** — style, imports, logging, config, commit format
7. **`testing/TESTING_GUIDE.md`** — commands, structure, the hermeticity rule

**For the outside view (what a third party sees):**
8. **`../README.md`** — framing, deployment posture, licensing
9. **`../docs/configuration.md`** — the complete env + `config.yaml` reference

**For planned work:**
10. **`specs/*.md`** — active feature specs (see the epic wrapper first)
11. **`roadmap/MILESTONES.md`**, **`roadmap/BACKLOG.md`**

---

## Session Start Checklist

- [ ] Read `SYNOPSIS.md` for current state
- [ ] Read `../CLAUDE.md` for the invariants
- [ ] Check `specs/epic-forage-extraction-forage-side.md` for what's in flight
- [ ] Scan `docs/GOTCHAS.md`
- [ ] Confirm the environment: `uv sync --extra dev && uv run pytest` (expect ALL green, zero failures — the current count lives in `kit_tools/testing/TESTING_GUIDE.md`; 1250 as of spec-2 US-005)

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
| `docs/CONVENTIONS.md` | A style or tooling rule changed |
| `docs/GOTCHAS.md` | You hit a landmine — write it down while it still hurts |
| `testing/TESTING_GUIDE.md` | Test counts, structure, commands, or `test_mapping` changed |
| `../docs/configuration.md` | Any env var or `config.yaml` key added/changed/removed |
| `../README.md` | Framing, posture, quickstart, or licensing changed |
| `roadmap/MILESTONES.md` | Milestone progress changed |

---

## Patterns to Follow

### Code organization
- Modules are **flat at the repo root** (`retrieval_app.py`, `models.py`, `cache.py`,
  `url_validator.py`). This is a decision — do not "tidy" them into a `forage/` package.
- Pipeline stages live in `pipeline/` as `stageN_<subject>.py`; the driver is
  `pipeline/orchestrator.py`.
- The ML classifier wrapper lives in `promptguard/`.
- Tests are flat under `tests/`, one module per subject.

### Imports
- **Never import from `poppy`.** Hard invariant; see `../CLAUDE.md`.
- `tests/conftest.py` puts the repo root on `sys.path` once — never add a per-module
  `sys.path.insert`.

### Security patterns
- **Never log a credential-bearing value.** `VALKEY_URL` may carry a password; `cache.py`
  keeps a closed log vocabulary and a test asserts it.
- **SSRF is the standing threat.** Making outbound requests is Forage's job. Changes to
  `url_validator.py` or `pipeline/stage5_url_audit.py` (RFC1918 rejection, DNS-rebinding
  checks, manual redirect following) are security changes — treat them as such.
- **No authentication exists, by design.** Do not add half-measures that read as auth
  without being it. Network placement is the control; the README says so on its first
  screen.

### Error handling
- `/health` is always 200; honesty lives in the **body** (`status`, `degraded_reasons`).
- Prefer machine-readable reason codes over prose in anything a consumer parses.
- A degradation must never be silent. That failure mode ran for nine days in production
  and is the reason this service reports the way it does.

---

## Off-Limits / Requires Human Review

Draft these, but do not apply without the owner's approval:

- [ ] **Pushing any image built from the current Dockerfile** — it bakes an HF token
      recoverable via `docker history`. See `docs/GOTCHAS.md`. Never push, anywhere.
- [ ] **Making this repository public** — gated by `feature-forage-ci-and-image` US-008.
- [ ] **Anything that weakens `url_validator.py` or the stage-5 redirect audit.**
- [ ] **Changing the response contract** (`pipeline/contract.py`, `models.py` response
      shapes) — versioned, with a consumer that refuses on a mismatch.
- [ ] **Renaming modules into a `forage/` package** — deferred by decision.
- [ ] **Adding a dependency**, or re-locking without the CPU-torch index config.
- [ ] **Secrets or API keys** — never commit; there is no secret store in this repo.

When in doubt, ask before applying.

---

## Documentation Structure

```
kit_tools/
├── AGENT_README.md          # This file
├── SYNOPSIS.md              # What Forage is, current state, how to run it
├── SESSION_LOG.md           # Development session history
├── model_preferences.json   # KitTools model roles (copied from Poppy)
├── worktree.yaml            # Worktree & environment contract (committed)
│
├── arch/
│   └── CODE_ARCH.md         # Module map, patterns, deliberate non-structure
│
├── docs/
│   ├── CONVENTIONS.md       # Style, imports, logging, config, commits
│   └── GOTCHAS.md           # Known landmines
│
├── specs/                   # Feature specs + the Forage-local epic wrapper
│   ├── epic-forage-extraction-forage-side.md
│   ├── feature-forage-*.md
│   ├── EPIC.md / FEATURE_SPEC.md / SCHEMA.md   # templates + frontmatter reference
│   └── archive/             # completed specs land here
│
├── testing/
│   └── TESTING_GUIDE.md     # Commands, structure, test_mapping
│
└── roadmap/
    ├── MILESTONES.md
    └── BACKLOG.md
```

Repo-root docs that are **not** under `kit_tools/` but matter just as much:
`../CLAUDE.md`, `../README.md`, `../NOTICE`, `../docs/configuration.md`,
`../docs/bootstrap-notes.md`, `../docs/bootstrap-scan.txt`.

Templates this project does not carry (and does not need): `DATA_MODEL.md` (no database),
`SECURITY.md` (posture lives in the README and `CODE_ARCH.md`), `INFRA_ARCH.md`,
`API_GUIDE.md` (the OpenAPI contract is the API doc; `feature-forage-contract` freezes it),
`ENV_REFERENCE.md` (`../docs/configuration.md` is it), `UI_STYLE_GUIDE.md` (headless).
Add one only when there is real content for it.

---

## File Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Feature specs | `feature-<kebab-name>.md` | `feature-forage-ci-and-image.md` |
| Epic wrappers | `epic-<kebab-name>.md` | `epic-forage-extraction-forage-side.md` |
| Source modules | `snake_case.py` | `url_validator.py` |
| Pipeline stages | `stageN_<subject>.py` | `stage2_structural.py` |
| Test modules | `test_<subject>.py` | `test_cache.py` |

---

## Documentation Standards

Every `kit_tools/` doc carries, right under its title:

```markdown
> Last updated: 2026-09-07
> Updated by: [Human/Claude]
```

The canonical field is exactly `Last updated: YYYY-MM-DD`.

---

## Spec Sync Rule (while the extraction finishes)

The four Forage-side feature specs were **authored in Poppy** and copied here at bootstrap.
**Poppy's copies are canonical**; edits flow Poppy → Forage, one way, at handoff only.
Poppy's copies are now `status: on-hold` and its epic wrapper marks them "Moved to Forage —
do not execute here", so execution happens **only here**. Do not edit the Poppy originals
to reflect work done here — record that work in this repo's Implementation Notes.
