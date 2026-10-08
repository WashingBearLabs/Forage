<!-- Template Version: 2.0.0 -->
# Milestones

> Last updated: 2026-10-07
> Updated by: Claude (v1.3.0 release epic planned 2026-10-07)

**Previous release:** Forage `v1.2.1` — the hardened image (`epic-forage-hardening`, T2.2):
`/retrieve` parity, search-text and URL audit in attacker-controlled forms, signed cache
entries, bounded providers, an operator-sizable envelope, and model-selection tooling
at contract **1.3.0**. At v1.2.1 only the 22M was allowlisted.
**Status:** Shipped and verified (2026-09-23). v1.2.0 was withdrawn after its
default-model failure. [Handoff](../specs/archive/feature-hardening-release.md).
**Current release:** MINOR `v1.3.0` / contract `1.4.0` is prepared and **not yet published** (`epic-forage-v1-3-0-release`; pins moved ahead of the cut, owner gates pending). Latest published: PATCH `v1.2.2` (`epic-forage-injection-corpus` spec 0) makes the 86M
selectable via `FORAGE_MODEL_ID` with the 22M default, contract 1.3.0 unchanged;
published 2026-10-04 and verified (index `sha256:5cb60943b99da45829613cde1f8286bdb4b72866210aa2146ca0cc5233569365`).

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
- [x] **T2.3 Injection regression corpus** (`epic-forage-injection-corpus`, planned 2026-09-19,
      six specs / 25 stories since spec 0 was added 2026-09-24; P1, `epic-forage-hardening` shipped): attack + benign corpus,
      hermetic route drivers, recorded-score cassettes (owner gates: 22M, 86M), generated baseline +
      floors gate in the `test` job, CI step summary, contiguity / 86M decision table. Spec 0 released
      `v1.2.2`; specs 1–5 changed no runtime behaviour. Guide `../../docs/corpus.md`; wrapper
      `../specs/epic-forage-injection-corpus.md`. Follow-ups resolved 2026-10-06 (unreleased): the 86M
      becomes the default and contiguity stays off (`../arch/DECISIONS.md`).

- [x] **Structural hardening** (`epic-forage-structural-hardening`, planned 2026-10-06, completed 2026-10-07 on PR #42, four specs /
      thirteen stories; P1, follows T2.3): closes the corpus's 30 structural findings (case, entity,
      confusable, split-tag, newline-split and markup-consumed variants; blocked-page titles;
      inline-hidden body content) with stage-2 scan forms, keeping stage-3 input byte-identical
      so no cassette is re-recorded. Wrapper `../specs/epic-forage-structural-hardening.md`.

- [ ] **v1.3.0 release** (`epic-forage-v1-3-0-release`, planned 2026-10-07, three specs / fourteen
      stories; P1, follows structural hardening): `/retrieve` HTML above a measured size parses in
      the rlimited worker (coded 422), `/search` parse off the event loop, a refused look-alike
      fold BLOCKs at `max(2n, n + 256)`, budget default 64, validation-422 placeholders dropped → contract
      `1.4.0`, release notes and pins. Tag/publish are owner gates. Wrapper
      `../specs/epic-forage-v1-3-0-release.md`.

- [ ] **Inference backends / v1.4.0** (`epic-forage-inference-backends`, planned 2026-10-08, four specs /
      eleven stories; P1, follows v1.3.0): one `forage` image serves CPU and GPU hosts (cu130 torch on
      amd64, CPU torch on arm64), `FORAGE_DEVICE` + `FORAGE_DEVICE_FALLBACK`, GPU batching, OOM failover,
      `/health` device → contract `1.5.0`, corpus parity on thelab's RTX 4070 Ti. Wrapper
      `../specs/epic-forage-inference-backends.md`.

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
2026-09-23), then `epic-forage-injection-corpus` (T2.3, planned 2026-09-19, revalidated against the
shipped tree 2026-09-24 — six specs, 25 stories; spec 0 makes the 86M selectable on the owner's lab
host and releases PATCH `v1.2.2`, specs 1–5 build the corpus with no runtime change; owner gates in
spec 0 and for the two cassette recordings; shipped 2026-10-04), then the T3 items (additional providers, provenance hooks) and the
deferred `forage/` package rename.
