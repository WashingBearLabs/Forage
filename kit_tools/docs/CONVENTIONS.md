<!-- Template Version: 2.0.0 -->
# CONVENTIONS.md

> Last updated: 2026-09-07
> Updated by: Claude (forage-repo-bootstrap US-005)

## Code Style

### Python (the whole repo)

| Aspect | Setting | Where |
|--------|---------|-------|
| Version | 3.12+ | `pyproject.toml` `requires-python` |
| Formatter | `ruff format` | `pyproject.toml` `[tool.ruff]` |
| Linter | `ruff check` — rules `E, F, I, N, UP, B, SIM, RUF` | `[tool.ruff.lint]` |
| Line length | 88 | `[tool.ruff]` |
| Type checker | `pyright`, **strict** mode | `[tool.pyright]` |

Run every tool through `uv run` — never a system-installed binary. The lock pins ruff
0.16.6 and pyright 1.1.411; a system tool will report different numbers and the backlog
counts below will not reproduce.

```bash
uv run ruff check .
uv run ruff format .
uv run pyright
```

**No backlog.** `ruff check`, `ruff format --check` and `pyright` are all clean, and all
three are **blocking CI gates** (US-001 and US-006, 2026-09-07). None of them was
baselined: the pyright backlog — 269 errors once the 39 inherited type-ignore comments
were counted — was burned to zero with real fixes.

### Type-checking policy

Read this before reaching for a suppression; there is a right place for every case.

| Situation | What to do |
|---|---|
| Your own code does not type-check | Fix the types. This is the answer almost every time. |
| A dependency's shipped types leave a symbol you call as `Unknown` | Add a minimal stub under `typings/` declaring **only** the symbols this repo calls — read `typings/README.md` first, a stub shadows the real package. |
| A test needs a module's private surface | Already allowed: `reportPrivateUsage` is off for `tests/`, and only for `tests/`. |
| A test needs to do something the type system forbids on purpose (assign to a frozen dataclass, pass a value outside a `Literal`) | Route it through a helper that hides the constant from the checker — `tests.fakes.assert_frozen` is the worked example — so the runtime assertion still runs. |
| Anything else | Nothing. Type-ignore comments are switched off repo-wide (`enableTypeIgnoreComments = false`); adding a rule relaxation is a policy change that fails `tests/test_pyright_policy.py` until the policy comment in `pyproject.toml` is updated too. |

---

## Naming Conventions

| Thing | Convention | Example |
|-------|-----------|---------|
| Modules | `snake_case.py`, flat at repo root or under `pipeline/` / `promptguard/` | `url_validator.py` |
| Pipeline stages | `stageN_<subject>.py` | `pipeline/stage2_structural.py` |
| Test modules | `tests/test_<module>.py`, mirroring the module under test | `tests/test_cache.py` |
| Private helpers | leading underscore | `_closed_vocabulary_reason` |
| Module constants | `SCREAMING_SNAKE`, leading `_` when private | `_DEFAULT_SEARXNG_URL` |
| Env vars | `SCREAMING_SNAKE`, product-neutral | `VALKEY_URL`, `FORAGE_LEGACY_CAPABILITY` |

**No `poppy` in a new name.** The extraction is deliberate; legacy `POPPY_*` env-var names
survive only as back-compat aliases with a `FORAGE_*` primary.

---

## Imports

- **Never import from `poppy`.** Forage cannot see Poppy's code and must never try. This
  is a hard invariant — see `CLAUDE.md` for the origin lesson.
- Modules are flat at the repo root: `from pipeline.orchestrator import ...`,
  `from cache import ...`. `tests/conftest.py` puts the repo root on `sys.path` once;
  never re-add a per-module `sys.path.insert`.
- Tests import fakes as `from tests.fakes import ...` — not `tests.retrieval.fakes`
  (the pre-extraction path).
- Import sorting is enforced by ruff's `I` rules; `uv run ruff check --fix .` handles it.

---

## Logging

- **Never log a credential-bearing value.** `VALKEY_URL` may carry a password.
  `cache.py` keeps a *closed log vocabulary*: failures map to a fixed set of reason
  strings rather than interpolating the URL. Any new code on the startup or cache path
  must preserve this; `tests/test_cache.py` asserts it.
- Prefer machine-readable reason codes over prose in anything a consumer parses
  (`degraded_reasons` is the model).

---

## Configuration

Two sources, both documented in `docs/configuration.md`:

1. **Environment variables**, read at process start.
2. **`config.yaml`**, mounted into the container.

Adding either means adding it to `docs/configuration.md` in the same change — the file is
the reference a third-party operator reads, and it is checked against the code.

---

## Tests

- One test module per subject; see `kit_tools/testing/TESTING_GUIDE.md` for the mapping.
- `asyncio_mode = "auto"` — async tests need no decorator.
- The suite is **hermetic**: `tests/conftest.py` installs an autouse `pytest-socket`
  guard. Any test that reaches the real network fails loudly. Mock at the seam
  (httpx, DNS, transformers/torch) rather than relaxing the guard.
- Tests are expected alongside the code they cover, in the same commit.

---

## Commits

Conventional commits, scoped to the feature spec being executed:

```
feat(forage-ci-and-image): US-003 - Publish the multi-arch image

Co-Authored-By: KitTools + Claude
```

Types in use: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`.

---

## Documentation

- **`README.md`** is the third-party front door: framing, deployment posture, quickstart,
  licensing. Keep the no-auth/private-network posture on the first screen.
- **`docs/configuration.md`** is the complete config reference.
- **`kit_tools/`** is the working documentation set for agents and maintainers:
  `SYNOPSIS.md` (orientation), `arch/CODE_ARCH.md` (module map), this file,
  `docs/GOTCHAS.md` (landmines), `testing/TESTING_GUIDE.md`.
- Record a landmine in `docs/GOTCHAS.md` the moment you hit one. A gotcha discovered and
  not written down gets rediscovered at the worst possible time.
