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
