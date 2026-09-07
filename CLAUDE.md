# CLAUDE.md

**Forage** — extraction and telemetry service for safe web access by LLM agents.
By WashingBearLabs. Apache-2.0.

Read `README.md` for what Forage is and `kit_tools/AGENT_README.md` for how to navigate
this repo. This file carries only the invariants — the things that are expensive to
rediscover.

---

## Hard Invariants

### 1. Never import from `poppy`. Never depend on Poppy at all.

Forage was extracted from the Poppy monorepo on 2026-09-07 and is standalone. There is no
`poppy` package here, no path to one, and no circumstance in which importing one is
correct.

**Origin lesson (US-004).** The 24 service source files came out of the split clean, but
*one test* — `tests/test_orchestrator.py` — did `from poppy.core.abilities.builtin.canvas
import FILE_RECALL_FAILURE_MESSAGES` to assert that Forage's document-failure codes were a
subset of Poppy's recall-message vocabulary. A single line, easy to miss in review, and it
made the suite unrunnable outside the monorepo. The fix was not to vendor Poppy's constant
— it was to recognise the assertion as a *Poppy-side* property (Poppy owns both
vocabularies) and re-home it there. The import and the assertion were deleted here.

The generalisation: **if an assertion needs to see both sides of the boundary, it belongs
on the consumer's side, not here.** Forage may define and publish its own vocabulary; it
may never reach across to check what someone does with it.

Legacy `POPPY_*` environment-variable names survive only as back-compat aliases with a
`FORAGE_*` primary. No new name contains "poppy".

### 2. Never push an image built from the current Dockerfile.

`Dockerfile` still takes `ARG HF_TOKEN` to bake the PromptGuard weights at build time. A
build ARG is recoverable from the image's layer history via `docker history --no-trunc` —
and from any registry the image reaches. An image built with a token is a leaked token the
moment it is pushed, public registry or private.

Local builds and local runs are fine. **Pushing is not**, anywhere, until
`feature-forage-model-bootstrap` moves to download-at-start and
`feature-forage-ci-and-image` removes the build-arg path. This is also why the repository
is private; the public flip is a human gate.

### 3. The module layout is flat, and that is a decision.

`retrieval_app.py`, `models.py`, `cache.py`, `url_validator.py` sit at the repo root;
`pipeline/` and `promptguard/` are the only package directories. Do not reorganise them
into a `forage/` package. Three things depend on the current paths: the Dockerfile's
`COPY` lines, `pipeline/sanitizer_revision.py`'s `_REVISION_SOURCES` (which hashes files
by relative path), and the entire test suite. The rename is deferred to a scheduled epic
that will handle all three together.

Corollary: `retrieval_app.py` keeps its name. It was renamed from `app.py` to avoid a
`sys.modules['app']` collision, and while that collision cannot happen here, the filename
is now load-bearing for the two consumers above.

### 4. A change to a response shape is a contract change.

The response contract is versioned (`pipeline/contract.py`, `contract_version` currently
**1.0.0**), and consumers are expected to refuse activation on a major mismatch rather
than guess. Changing any response shape means: bump the version, update or add the golden
fixture under `tests/golden/`, and note the change for the consuming repo.
`feature-forage-contract` freezes the OpenAPI surface and formalises the bump policy —
read it before touching `pipeline/contract.py` or the response models in `models.py`.

### 5. Degradation is loud, never silent.

`/health` always returns 200; the truth is in the body (`status`, `degraded_reasons`,
`promptguard_loaded`, `cache_connected`). Never make a missing model, an unreachable
cache, or a refused fetch look healthy. A silent version of exactly this failure ran
unnoticed in production for nine days, and the honest-health contract is the fix. Do not
regress it in the name of a cleaner status code.

### 6. Never log a credential-bearing value.

`VALKEY_URL` may carry a password. `cache.py` keeps a *closed log vocabulary* — failures
map to fixed reason strings rather than interpolating the URL — and
`tests/test_cache.py` asserts it. The entrypoint deliberately prints nothing for the same
reason. Any new startup or cache code must preserve this.

---

## Development

```bash
uv sync --extra dev     # environment (creates .venv)
uv run pytest           # 534 tests, all green, hermetic
uv run ruff check .     # must stay clean
uv run ruff format .    # 6-file backlog exists
uv run pyright          # strict; 214-error backlog exists
```

Always go through `uv run` — the lock pins the toolchain, and a system-installed ruff or
pyright will report numbers that don't reproduce.

The suite is hermetic: an autouse `pytest-socket` guard in `tests/conftest.py` fails any
test that touches the real network. Mock at the seam; never relax the guard.

Container:

```bash
docker build -t forage .                      # works with no HF token (degraded runtime)
docker run --rm -p 127.0.0.1:8020:8020 forage # private network only — no auth exists
```

---

## Spec Execution

Feature specs live in `kit_tools/specs/` and execute **here** via
`/kit-tools:execute-epic`. The Forage-side epic wrapper is
`kit_tools/specs/epic-forage-extraction-forage-side.md`.

The four feature specs were authored in Poppy and copied here at bootstrap. **Poppy's
copies are canonical**; edits flow Poppy → Forage, one way, at handoff only. Poppy's
copies are `status: on-hold` and its epic wrapper marks them "Moved to Forage — do not
execute here". Record work done here in this repo's Implementation Notes, not in Poppy's
originals.

`kit_tools/worktree.yaml` is the environment contract the orchestrator reads
(`env_bootstrap: uv sync --extra dev`, `run_prefix: uv run`). Keep it accurate — a missing
`run_prefix` makes the orchestrator run system Python and report false regressions.

---

## Coexistence with Poppy (temporary)

Until Poppy pins a published Forage image, **Poppy's in-tree copy remains the deployed
source of truth** and the two copies coexist. Any fix to the extracted paths on either
side must be replayed onto the other, with the Poppy source commit recorded in
`docs/bootstrap-notes.md`'s pin record.

One thing has already diverged deliberately: `derive_sanitizer_revision()` moved from
`e6b2b56d…` to `2b8d7e9a…` here when the vault-free hostname defaults landed, and again to
`cd00a8b4…` when the `ruff format` CI gate reformatted `pipeline/stage2_structural.py`.
**Do not assume Poppy↔Forage revision parity** — compare contracts, not revisions.
