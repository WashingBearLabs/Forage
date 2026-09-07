<!-- Template Version: 2.0.0 -->
# Milestones

> Last updated: 2026-09-07
> Updated by: Claude (forage-repo-bootstrap US-005)

**Current target:** Forage `v1.0.0` — a published, secret-free, contract-frozen image a
third party can run.
**Status:** In Progress

The four specs below are the Forage half of the Web Access family's extraction epic; the
Poppy half (consuming the image, then deleting the in-tree copy) executes in Poppy. See
`../specs/epic-forage-extraction-forage-side.md`.

---

## Done

- [x] **Repo bootstrap** (`forage-repo-bootstrap`, executed in Poppy, 2026-09-07) —
      history-preserving split, identity, vault-free config, green suite, this scaffold.

---

## Must Have (P0)

- [ ] **CI + published images** (`feature-forage-ci-and-image`) — GitHub-hosted CI
      (lint + format + pyright-strict + the full suite), the format/pyright backlog burnt
      down, multi-arch `ghcr.io/washingbearlabs/forage` + `forage-searxng` published
      behind a gated chain, the `ARG HF_TOKEN` build path removed, and the **public flip**
      (US-008 — human gate).
- [ ] **Model bootstrap** (`feature-forage-model-bootstrap`) — download-at-start weights
      with a vendored GHCR mirror fallback, replacing bake-at-build. US-003 (vendoring)
      is **supervised** — it needs the owner's HF token and GHCR credentials.
- [ ] **Frozen contract** (`feature-forage-contract`) — documented error surface, frozen
      OpenAPI, drift check in CI, governance for version bumps. Its final story cuts
      **`v1.0.0`** (supervised tag push).

---

## Should Have (P1)

- [ ] **Optional cache** (`feature-forage-cache-fallback`) — bounded in-memory backend
      when `VALKEY_URL` is unset (healthy), while configured-but-unreachable stays
      `degraded: cache_unavailable`. Carries a manual-smoke half (human gate) and the
      example `docker-compose.yml` that makes the quickstart real.

---

## Exit Criteria for `v1.0.0`

- [ ] Published image contains **no HF token and no baked weights** — `docker history`
      shows no secret.
- [ ] Repository is public, with secret scanning + push protection + branch protection
      applied (the three settings deferred at bootstrap — see `../../docs/bootstrap-notes.md`).
- [ ] CI green on every lane: `ruff check`, `ruff format --check`, `pyright` strict, full
      suite.
- [ ] A third party can `docker compose up` from the example fragment with only
      `HF_TOKEN` (+ `SEARXNG_SECRET`) set and get a working `/search` + `/retrieve`.
- [ ] Contract drift check passes; `contract_version` frozen at `1.0.0` with a documented
      bump policy.

---

## After `v1.0.0`

Poppy pins the published images and deletes its in-tree copy (both specs execute in
Poppy). Once that lands, the coexistence rule ends and this repo becomes the sole source
of truth. Later Forage-side work — search-provider abstraction, `/retrieve` hardening, the
86M model, a `forage/` package rename — is planned in Poppy's Web Access family and
re-homed here as it is scheduled.
