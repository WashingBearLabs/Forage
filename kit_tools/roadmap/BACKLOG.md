<!-- Template Version: 2.0.0 -->
# BACKLOG.md

> Last updated: 2026-10-07
> Updated by: Claude (structural-hardening epic completed; PR #42)

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

## Forage Structural Hardening (Completed)
- [Epic Overview](../specs/epic-forage-structural-hardening.md) — closes the corpus's 30 structural findings with derived stage-2 scan forms; stage-3 input byte-identical (no re-record); ratchet both ways
- [Scan forms](../specs/archive/feature-structural-scan-forms.md) — case-insensitive patterns; linear patterns; shared decoded form; generated confusable tables and fold forms (5 stories)
- [Markup surface](../specs/archive/feature-structural-markup-surface.md) — inline-joined form (linear walk); first-match raw-source markup scan (2 stories; depends on: scan-forms)
- [Wire closure](../specs/archive/feature-structural-wire-closure.md) — quarantined titles null (ruling (m)); body-only visibility pass (2 stories; depends on: markup-surface)
- [Close-out](../specs/archive/feature-structural-closeout.md) — floors ratchet script, findings re-file (owner step), docs, records preflight (4 stories; depends on: wire-closure)

---

## Injection Regression Corpus (Completed)
- [Epic Overview](../specs/epic-forage-injection-corpus.md) — T2.3; six specs, 25 stories (`86m-enablement` 4, `harness` 3, `attacks` 5, `benign` 4, `recording` 4, `gates` 5); the 86M became selectable as `v1.2.2`, the epic's only release. Guide: `../../docs/corpus.md`. Findings (leaks, over-defence, blocked-but-leaked) are filed in `../AUDIT_FINDINGS.md`, none fixed

---

## Forage Hardening (Completed)
- [Epic Overview](../specs/epic-forage-hardening.md) — Web Access family Epic 4, Forage half; shipped and verified 2026-09-23 as `v1.2.1` at contract 1.3.0; v1.2.0 withdrawn
- [Search sanitization](../specs/archive/feature-hardening-search-sanitization.md) — newline-preserving structural scan, URL wire-form scan, search-result URL audit, contract window opens
- [Retrieve parity](../specs/archive/feature-hardening-retrieve-parity.md) — chunk budget + semaphore, off-loop extraction, corrupt cache entry = miss, operator fail-closed floor (depends on: search-sanitization)
- [Hostname and config](../specs/archive/feature-hardening-hostname-and-config.md) — dot-boundary hostname matching, `/search` `blocked_domains` + honoured threshold, `config.yaml` key registry (depends on: retrieve-parity)
- [Cache integrity](../specs/archive/feature-hardening-cache-integrity.md) — optional `FORAGE_CACHE_HMAC_KEY`, loud `cache_unauthenticated` when absent (depends on: hostname-and-config)
- [Provider bounds](../specs/archive/feature-hardening-provider-bounds.md) — streamed body caps, wall-clock timeouts, query cap, policy monotonicity, the orchestrator cleanup rotation (depends on: cache-integrity)
- [Resource envelope](../specs/archive/feature-hardening-resource-envelope.md) — `FORAGE_CPUS` / `FORAGE_MEM_LIMIT`, threads, latency target on `/metrics`, sizing table (depends on: provider-bounds)
- [PromptGuard selection](../specs/archive/feature-hardening-promptguard-86m.md) — model selection, contiguity gating and benchmark harness shipped; 86M vendoring/benchmark owner gates not run, only 22M allowlisted
- [Release](../specs/archive/feature-hardening-release.md) — validation-422 trim, contract 1.3.0 frozen, verified `v1.2.1` cut and completed recovery

---

## Future Work (no spec yet)

### Rename modules into a `forage/` package
**Priority:** Low · **Effort:** Medium
Deliberately deferred at extraction — the flat layout keeps the Dockerfile,
`sanitizer_revision`'s hashed source paths, and the whole suite working unchanged.
Cosmetic only, and it touches `sanitizer_revision`, so it needs its own change.

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

- **Per-model classifier working set** (from `corpus-86m-enablement` US-003, 2026-10-02).
  `PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES` (64 MiB) is one model-independent constant with no
  measurement behind it, so the memory rule applies a 22M-derived working set to the 86M too.
  Measure the RSS delta between `classification_concurrency` 1 and 2 per model and make it a
  per-model map, as `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` now is.
- **`/extract` classify latency is high on the reference host** (same benchmark): one window
  warm p50 13.1 s (22M) / 26.2 s (86M) at 1 vCPU, 3.1 s / 6.4 s at 4 vCPU, scaling with
  `FORAGE_CPUS`, with the host otherwise idle. That is far above a bare forward pass at these
  model sizes; profile where an `/extract` request spends its time (worker spawn, tokenizer,
  forward pass) before treating these numbers as the classifier's cost.
