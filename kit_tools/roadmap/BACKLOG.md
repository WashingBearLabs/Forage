<!-- Template Version: 2.0.0 -->
# BACKLOG.md

> Last updated: 2026-09-19
> Updated by: Claude (plan-epic forage-hardening)

Work that is real but not yet scheduled into a feature spec. Items with an owning spec
live in `MILESTONES.md` instead.

## Prioritized Items

| Priority | Item | Type | Feature Spec | Status |
|----------|------|------|--------------|--------|
| P0 | Re-apply the three deferred GitHub settings (secret scanning, push protection, branch protection) at the public flip | Tech Debt | `feature-forage-ci-and-image` (US-008) | Done |
| P1 | `ruff format --check` backlog — 6 files | Tech Debt | `feature-forage-ci-and-image` | Done |
| P1 | `pyright` strict backlog — 214 errors (22 service / 192 tests, incl. 35 `reportPrivateUsage`) | Tech Debt | `feature-forage-ci-and-image` | Done |
| P1 | Committed hermeticity canary test (deliberately omitted at bootstrap to keep the exact-count gate) | Tech Debt | `feature-forage-ci-and-image` (US-002) | Done |
| P2 | Rotate the SearXNG placeholder `secret_key` before the repo goes public | Security | — | Planned |
| P2 | Automated container smoke (`docker build` + `docker run` + `/health`) — manual today | Tech Debt | `feature-forage-ci-and-image` | Done |

---

## Forage Hardening (Epic)
- [Epic Overview](../specs/epic-forage-hardening.md) — Web Access family Epic 4, Forage half; planned 2026-09-19; ships `v1.2.0` at contract 1.3.0
- [Search sanitization](../specs/feature-hardening-search-sanitization.md) — newline-preserving structural scan, URL wire-form scan, search-result URL audit, contract window opens
- [Retrieve parity](../specs/feature-hardening-retrieve-parity.md) — chunk budget + semaphore, off-loop extraction, corrupt cache entry = miss, operator fail-closed floor (depends on: search-sanitization)
- [Hostname and config](../specs/feature-hardening-hostname-and-config.md) — dot-boundary hostname matching, `/search` `blocked_domains` + honoured threshold, `config.yaml` key registry (depends on: retrieve-parity)
- [Cache integrity](../specs/feature-hardening-cache-integrity.md) — optional `FORAGE_CACHE_HMAC_KEY`, loud `cache_unauthenticated` when absent (depends on: hostname-and-config)
- [Provider bounds](../specs/feature-hardening-provider-bounds.md) — streamed body caps, wall-clock timeouts, query cap, policy monotonicity, the orchestrator cleanup rotation (depends on: cache-integrity)
- [Resource envelope](../specs/feature-hardening-resource-envelope.md) — `FORAGE_CPUS` / `FORAGE_MEM_LIMIT`, threads, latency target on `/metrics`, sizing table (depends on: provider-bounds)
- [PromptGuard 86M](../specs/feature-hardening-promptguard-86m.md) — `FORAGE_MODEL_ID`, contiguity gating, benchmark harness, owner-run benchmark (depends on: resource-envelope)
- [Release](../specs/feature-hardening-release.md) — validation-422 trim, contract 1.3.0 frozen, owner-gated `v1.2.0` cut (depends on: promptguard-86m)

---

## Future Work (no spec yet)

### Rename modules into a `forage/` package
**Priority:** Low · **Effort:** Medium
Deliberately deferred at extraction — the flat layout keeps the Dockerfile,
`sanitizer_revision`'s hashed source paths, and the whole suite working unchanged.
Cosmetic only, and it touches `sanitizer_revision`, so it needs its own change.

### Injection regression corpus in CI (T2.3) — planned
**Priority:** Medium · **Effort:** Large
`epic-forage-injection-corpus` was planned on 2026-09-19 (`/kit-tools:plan-epic`; five specs, 21
stories — `feature-corpus-{harness,attacks,benign,recording,gates}.md`): a licence-clean attack
corpus and benign counter-corpus driven hermetically through `POST /search`, `/retrieve` and
`/extract`, with the real classifier measured once per model revision on a host and replayed in CI
from committed per-window score cassettes; the gate is a generated baseline (exact match) plus
measured floors. Ships no runtime change. Sequenced **after** `epic-forage-hardening` (spec 1
`depends_on: [hardening-release]`); next step `/kit-tools:validate-epic forage-injection-corpus`.
Shipped and moved out of this list: the search-provider abstraction (`v1.1.0`, 2026-09-18) and
`/retrieve` hardening + the 86M model (now the planned `epic-forage-hardening`, below).

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
