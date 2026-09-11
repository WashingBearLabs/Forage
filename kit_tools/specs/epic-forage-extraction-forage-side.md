<!-- Template Version: 2.1.0 -->
---
epic: forage-extraction-forage-side
status: active
vision_ref: Secure Web Retrieval / provider-independent web access
created: 2026-09-07
updated: 2026-09-07
---

# Epic: Forage Extraction — Forage-Side Half

> **This is the Forage-repo half of a seven-spec epic planned in Poppy.** The four child
> specs below were authored and validated in Poppy and copied here at bootstrap
> (`forage-repo-bootstrap` US-005, 2026-09-07). **They execute here, in this repo.** The
> Poppy copies are `status: on-hold` and Poppy's wrapper marks them "Moved to Forage — do
> not execute here". See **Notes → Sync rule** before editing any of them.

## Goal

Take the freshly extracted Forage repository from "green suite on a laptop" to **a
published, secret-free, contract-frozen `v1.0.0` image a third party can run**: its own CI
(lint + format + pyright-strict + the full suite), multi-arch
`ghcr.io/washingbearlabs/forage` and `ghcr.io/washingbearlabs/forage-searxng` images built
without ever embedding a Hugging Face token, PromptGuard weights fetched at start rather
than baked at build, an optional in-memory cache so Valkey stops being mandatory, and a
frozen, versioned OpenAPI contract with a documented bump policy. Completing this half is
what unblocks the Poppy-side half (consume the digest-pinned images, then delete the
in-tree copy), which executes in Poppy.

## Decomposition

| Seq | Feature Spec | Status | Stories | Human gates | Dependencies |
|-----|-------------|--------|---------|-------------|--------------|
| 1 | [feature-forage-ci-and-image](archive/feature-forage-ci-and-image.md) | **✅ Completed 2026-09-10** (8/8, 1 attempt each; repo+images PUBLIC, branch protection live — main is PR-only now) | 8 | US-008 executed 2026-09-10 | — |
| 2 | [feature-forage-model-bootstrap](archive/feature-forage-model-bootstrap.md) | **✅ Completed 2026-09-12** (6/6, 1 attempt each; mirror vendored + private; cold 19s / warm 9s network-none; revision rotated to 5927038d…) | 6 | forage-ci-and-image |
| 3 | [feature-forage-cache-fallback](feature-forage-cache-fallback.md) | Planned | 4 | **US-004 manual-smoke half = human gate** | forage-model-bootstrap |
| 4 | [feature-forage-contract](feature-forage-contract.md) | Planned | 5 | **US-004 cuts `v1.0.0`** — supervised tag push | forage-ci-and-image, forage-cache-fallback |

**Execution order:** 1 → 2 → 3 → 4 (**23 stories, fully sequential**). The {2, 3}
parallelism was removed during Poppy's validation round 1: both touch `/metrics` and the
golden contract fixtures.

`forage-ci-and-image` carries `depends_on: []` here — its Poppy-side dependency
(`forage-repo-bootstrap`) is already satisfied: that spec **created this repository**, and
its archived record lives in Poppy. Leaving the entry would dangle forever and block
execution.

## Completion Criteria

- [ ] CI green on every lane — `ruff check`, `ruff format --check`, `pyright` strict, the
      full pytest suite — on GitHub-hosted runners.
- [ ] The published image contains **no HF token and no baked weights**;
      `docker history --no-trunc` on it shows no secret. This closes the standing
      never-push-this-image invariant in `CLAUDE.md` and `kit_tools/docs/GOTCHAS.md`.
- [ ] Multi-arch (amd64 + arm64) `ghcr.io/washingbearlabs/forage` and
      `ghcr.io/washingbearlabs/forage-searxng` published behind the gated publish chain.
- [ ] Repository is **public**, with secret scanning, push protection, and branch
      protection applied — the three settings deferred at bootstrap under the Free-plan
      private-repo combination (re-application calls and the two deadlock warnings are
      recorded in `../../docs/bootstrap-notes.md`).
- [ ] Weights fetch at start (HF first, vendored GHCR mirror as fallback); a missing token
      shows as `degraded` in `/health`, never silently.
- [ ] `VALKEY_URL` unset = healthy bounded in-memory cache; configured-but-unreachable
      stays `degraded: cache_unavailable`.
- [ ] Contract frozen with a drift check in CI and a documented semver bump policy;
      `v1.0.0` tagged and released.
- [ ] A third party can `docker compose up` from the example fragment with only `HF_TOKEN`
      (+ `SEARXNG_SECRET`) set and get a working `/search` + `/retrieve` — the "genuinely
      reusable" proof.

## Non-goals

- **Anything inside Poppy.** The consuming-side pin and the in-tree deletion are two
  separate Poppy specs and execute there. Nothing here may edit Poppy.
- Renaming modules into a `forage/` package — deferred by decision (see `CLAUDE.md` §3).
- Search-provider work and `/retrieve` hardening — later Web Access family epics, planned
  in Poppy, re-homed here when scheduled.
- A PyPI package or a UI. The deliverable is the image; Forage is headless.

## Notes

### Sync rule — Poppy is canonical, one-way, at handoff only

The four child specs were authored and validated in **Poppy**, which holds the family
context, and copied here once at bootstrap. **Poppy's copies remain the canonical planning
record.** Edits flow **Poppy → Forage, one way, at handoff only** — there is no reverse
sync and no ongoing sync.

Practically:

- **Execute here.** Poppy's copies are `status: on-hold` with a "moved to Forage" banner,
  and Poppy's wrapper Decomposition marks rows 2–5 "Moved to Forage — do not execute here"
  (that table, not child frontmatter, is what `execute-epic` builds its run list from).
- **Record results here.** Implementation Notes, ticked criteria, and learnings from
  execution belong in *this repo's* copies. Do not edit Poppy's originals to reflect work
  done here.
- **If a spec needs re-planning** rather than execution, that happens in Poppy and the
  result is re-copied here — the same one-way handoff.

### Renumbering, and what "spec N" means in the child specs

These four are specs **2–5** of Poppy's seven-spec `forage-extraction` epic. They were
renumbered to `epic_seq` 1–4 here so this wrapper is schema-valid (KitTools requires a
contiguous sequence starting at 1 with exactly one `epic_final: true` terminal — which is
`feature-forage-contract`).

The child specs' prose still uses **Poppy's numbering**. Read it with this map:

| "spec N" in the prose | Which spec | Where it lives |
|---|---|---|
| spec 1 | `forage-repo-bootstrap` | Poppy — **already executed** (created this repo, 2026-09-07) |
| spec 2 | `forage-ci-and-image` | here, `epic_seq: 1` |
| spec 3 | `forage-model-bootstrap` | here, `epic_seq: 2` |
| spec 4 | `forage-cache-fallback` | here, `epic_seq: 3` |
| spec 5 | `forage-contract` | here, `epic_seq: 4` |
| spec 6 | `poppy-consume-forage-image` | **Poppy** — executes there, after this epic |
| spec 7 | `forage-teardown-in-poppy` | **Poppy** — executes there, last |

A "spec 6 owns this" note in a child spec therefore means *the Poppy side owns it* — not
that something is missing here.

### Standing context for every story in this epic

- **The Dockerfile no longer bakes a secret** (spec 1 `ci-and-image` US-003, 2026-09-07).
  `ARG HF_TOKEN` and the `from_pretrained` bake block are gone, the base is digest-pinned
  and dependencies come from the committed `uv.lock`; `tests/test_dockerfile.py` guards
  the source and CI's `secret-grep` job greps the built image's layer history. **Pushing
  is still not on**, for a different reason: publishing runs through US-007's gated lane
  and the repo plus both GHCR packages stay private until US-008's human flip. Note also
  that Poppy's in-tree `services/retrieval/Dockerfile` *still* carries `ARG HF_TOKEN` —
  the old rule applies there verbatim until spec 6.
- **Every built image is weights-free** until spec 2 (`model-bootstrap`) adds the runtime
  fetch. That is the honest degraded state (`promptguard_unavailable`), not a broken
  build, and US-005's `smoke` job asserts exactly that contract.
- **Static-analysis backlog: none left** (measure with `uv run`, not system tools — the
  lock pins ruff 0.16.6 / pyright 1.1.411). All three lanes are clean and all three are
  blocking CI gates: `ruff check` and `ruff format --check` since `ci-and-image` US-001,
  `pyright` strict since US-006. The bootstrap hand-over figures — `ruff format` 6 files,
  `pyright` 214 errors — are **historical**, kept in `../../docs/bootstrap-notes.md`; the
  pyright one was itself understated, since 55 more errors sat behind 30 inherited
  type-ignore comments (269 real). Pyright's only carve-out is `reportPrivateUsage` for
  `tests/`, pinned by `tests/test_pyright_policy.py`.
- **`sanitizer_revision` has deliberately diverged** from Poppy, and has now rotated four
  times (`e6b2b56d…` → `2b8d7e9a…` → `cd00a8b4…` → `0537316d…` → **`5927038d…`**,
  current). The fourth is the odd one out: no source byte moved, the hashed model identity
  became `MODEL_ID@revision` when weights became a runtime input (spec-2 US-001). The
  Poppy-side spec must not assume revision parity — compare contracts.
- **The suite is hermetic and exact**: **1048 collected**, all green (534 at bootstrap;
  spec-1 additions: +33 US-001 workflow guards, +19 US-006 typecheck/policy, +21 US-002
  canary/test-lane, +49 US-003 Dockerfile/handoff, +75 US-005 smoke, +40 US-007 publish,
  +2 supervisor parity guards, +132 US-004 searxng (its supervisor fix renamed a test,
  net zero); spec-2: +87 US-002 manifest/quarantine, +56 US-001 acquisition/lifespan — the per-story counts live in
  `kit_tools/testing/TESTING_GUIDE.md`, which is the canonical tally). The count is a
  gate, not a floor. The hermeticity
  canary is committed and executing (`tests/test_hermeticity.py`), and since US-002 the
  whole suite runs in CI — every guard in this repo is finally CI-enforced rather than
  local-only.
- **Coexistence:** Poppy's in-tree copy stays the deployed source of truth until it pins a
  Forage image. Replay any hotfix to the extracted paths both ways and update the pin
  record in `../../docs/bootstrap-notes.md`.
- **The SearXNG placeholder `secret_key`** in `searxng/config/settings.yml` is also
  Poppy's live compose value until the Poppy side rotates it. The public flip re-checks
  history at flip time; the rotation is a registered follow-through, not this repo's story.
