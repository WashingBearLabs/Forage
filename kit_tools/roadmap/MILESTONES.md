<!-- Template Version: 2.0.0 -->
# Milestones

> Last updated: 2026-09-23
> Updated by: Copilot (authorized replacement release)

**Current release:** Forage `v1.2.1` — the hardened image (`epic-forage-hardening`, T2.2):
`/retrieve` parity, search-text and URL audit in attacker-controlled forms, signed cache
entries, bounded providers, an operator-sizable envelope, and model-selection tooling
at contract **1.3.0**. The 86M owner gates remain unrun; only 22M is allowlisted.
**Status:** Shipped and verified (2026-09-23). v1.2.0 was withdrawn after its
default-model failure. [Handoff](../specs/archive/feature-hardening-release.md).
Next plannable work: `epic-forage-injection-corpus` (T2.3).

`v1.0.0` (extraction epic) and `v1.1.0` (search providers) are done below. The Poppy halves
of both families (consuming the pinned image, the policy UI, the trust boundary) execute in
Poppy.

---

## Done

- [x] **Repo bootstrap** (`forage-repo-bootstrap`, executed in Poppy, 2026-09-07) —
      history-preserving split, identity, vault-free config, green suite, this scaffold.
- [x] **v1.0.0** (`feature-forage-contract`, cut 2026-09-12) — the extraction epic's Exit
      Criteria below, all met: secret-free image, public repo, green CI, `docker compose up`
      with only `HF_TOKEN` + `SEARXNG_SECRET`, contract frozen at `1.1.0`.
- [x] **v1.1.0 / T2.1 search-provider abstraction** (`epic-search-providers`, cut 2026-09-18)
      — the `SearchProvider` seam, `SearxngProvider`, the optional Brave LLM-Context paid
      backend behind `FORAGE_BRAVE_API_KEY`, free-first/paid-on-failure fallback, per-request
      policy and `/health` provider status, contract bumped to `1.2.0`. See
      `../specs/epic-search-providers.md` and `../../docs/releases.md` § "Released versions".

---

## Must Have (P0)

- [x] **CI + published images** (`feature-forage-ci-and-image`) — GitHub-hosted CI
      (lint + format + pyright-strict + the full suite), the format/pyright backlog burnt
      down, multi-arch `ghcr.io/washingbearlabs/forage` + `forage-searxng` published
      behind a gated chain, the `ARG HF_TOKEN` build path removed, and the **public flip**
      (US-008 — human gate).
- [x] **Model bootstrap** (`feature-forage-model-bootstrap`) — download-at-start weights
      with a vendored GHCR mirror fallback, replacing bake-at-build. US-003 (vendoring)
      is **supervised** — it needs the owner's HF token and GHCR credentials.
- [x] **Frozen contract** (`feature-forage-contract`) — documented error surface, frozen
      OpenAPI, drift check in CI, governance for version bumps. Its final story cuts
      **`v1.0.0`** (supervised tag push).

---

## Should Have (P1)

- [x] **v1.2.1 / T2.2 Forage hardening** (`epic-forage-hardening`, shipped 2026-09-23, eight
      specs) — search-text and URL scanning in wire form, `/retrieve` budgets and isolation,
      dot-boundary hostname matching, `FORAGE_CACHE_HMAC_KEY` cache integrity, provider body /
      timeout / query bounds, `FORAGE_CPUS` / `FORAGE_MEM_LIMIT` envelope, `FORAGE_MODEL_ID`
      with contiguity gating, contract `1.2.0` → `1.3.0`. Four owner-executed stories:
      86M vendoring (spec 7 US-005), benchmark (spec 7 US-004), the replacement
      cut (spec 8 US-003), and verification/handoff (spec 8 US-005). The first
      two have explicit gate-not-run records; release and verification completed. See
      `../specs/epic-forage-hardening.md`.
- [x] **Optional cache** (`feature-forage-cache-fallback`) — bounded in-memory backend
      when `VALKEY_URL` is unset (healthy), while configured-but-unreachable stays
      `degraded: cache_unavailable`. Carries a manual-smoke half (human gate) and the
      example `docker-compose.yml` that makes the quickstart real.

---

## Exit Criteria for `v1.0.0`

- [x] Published image contains **no HF token and no baked weights** — `docker history`
      shows no secret.
- [x] Repository is public, with secret scanning + push protection + branch protection
      applied (the three settings deferred at bootstrap — see `../../docs/bootstrap-notes.md`).
- [x] CI green on every lane: `ruff check`, `ruff format --check`, `pyright` strict, full
      suite.
- [x] A third party can `docker compose up` from the example fragment with only
      `HF_TOKEN` (+ `SEARXNG_SECRET`) set and get a working `/search` + `/retrieve`.
- [x] Contract drift check passes; `contract_version` frozen at `1.1.0` with a documented
      bump policy (`1.0.0` → `1.1.0` in `forage-cache-fallback` US-003, the additive
      `/health` field `cache_backend`, before the freeze).

---

## After `v1.0.0`

Poppy pins the published images and deletes its in-tree copy (both specs execute in
Poppy). Once that lands, the coexistence rule ends and this repo becomes the sole source
of truth. Forage-side sequence after `v1.1.0`: `epic-forage-hardening` (`v1.2.1`, shipped
2026-09-23), then `epic-forage-injection-corpus` (T2.3, plannable — it measures what the
hardening epic builds), then the T3 items (additional providers, provenance hooks) and the
deferred `forage/` package rename.
