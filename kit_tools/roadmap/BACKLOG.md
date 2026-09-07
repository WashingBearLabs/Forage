<!-- Template Version: 2.0.0 -->
# BACKLOG.md

> Last updated: 2026-09-07
> Updated by: Claude (forage-repo-bootstrap US-005)

Work that is real but not yet scheduled into a feature spec. Items with an owning spec
live in `MILESTONES.md` instead.

## Prioritized Items

| Priority | Item | Type | Feature Spec | Status |
|----------|------|------|--------------|--------|
| P0 | Re-apply the three deferred GitHub settings (secret scanning, push protection, branch protection) at the public flip | Tech Debt | `feature-forage-ci-and-image` (US-008) | Planned |
| P1 | `ruff format --check` backlog — 6 files | Tech Debt | `feature-forage-ci-and-image` | Planned |
| P1 | `pyright` strict backlog — 214 errors (22 service / 192 tests, incl. 35 `reportPrivateUsage`) | Tech Debt | `feature-forage-ci-and-image` | Planned |
| P1 | Committed hermeticity canary test (deliberately omitted at bootstrap to keep the exact-count gate) | Tech Debt | `feature-forage-ci-and-image` (US-002) | Planned |
| P2 | Rotate the SearXNG placeholder `secret_key` before the repo goes public | Security | — | Planned |
| P2 | Automated container smoke (`docker build` + `docker run` + `/health`) — manual today | Tech Debt | `feature-forage-ci-and-image` | Planned |

---

## Future Work (no spec yet)

### Rename modules into a `forage/` package
**Priority:** Low · **Effort:** Medium
Deliberately deferred at extraction — the flat layout keeps the Dockerfile,
`sanitizer_revision`'s hashed source paths, and the whole suite working unchanged.
Cosmetic only, and it touches `sanitizer_revision`, so it needs its own change.

### Search-provider abstraction (SearXNG is not the only option)
**Priority:** Medium · **Effort:** Large
Planned in Poppy's Web Access family (`epic-search-reliability`) against the pre-extraction
paths; needs re-planning against this layout once the extraction completes.

### `/retrieve` hardening and the 86M PromptGuard model
**Priority:** Medium · **Effort:** Large
Planned in Poppy's Web Access family as a later epic; lands here after the extraction.

### Structured request logging / tracing
**Priority:** Low · **Effort:** Small
`/metrics` carries counters only. Any per-request logging must respect the closed log
vocabulary — no credential-bearing values, ever.

---

## Explicit Non-Goals

- **Authentication.** Forage is private-network-only by design; the README says so on its
  first screen. Adding a half-measure that reads as auth without being it is worse than
  none.
- **A UI.** Forage is headless. The consuming agent owns the UI.
- **A PyPI package.** The deliverable is the image.
- **Distributing the PromptGuard weights.** Gated, Llama-licensed, downloaded by the
  operator.
