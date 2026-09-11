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

### 2. No secret may enter the image build. Ever.

`Dockerfile` takes **no build arguments at all**, and it must stay that way. A build ARG is
not a secret: Docker records it in the image's layer history, where `docker history
--no-trunc` reads it straight back out of any registry the image reaches. Deleting the
downloaded file afterwards does nothing about it.

The old `ARG HF_TOKEN` + `from_pretrained` bake block was removed in
`forage-ci-and-image` US-003, and two mechanical guards keep it removed —
`tests/test_dockerfile.py` on the file's text (every `uv run pytest`) and CI's
`secret-grep` job on the built image's layer history. Adding a credential back to the
build means defeating both on purpose. Pass secrets at **runtime**, through the container
environment (`docs/configuration.md`); Forage reads no secret store at boot either.

`test_the_build_takes_no_arguments_at_all` asserts the *absolute*, not just the
secret-shaped names, because the absolute is the part that survives review: once "the
Dockerfile declares no ARG" stops being true, the next argument only has to clear "is it
as harmless as that one?". `forage-model-bootstrap` US-004 is the worked example — it
needed a per-architecture download, declined the conventional `ARG TARGETARCH`, and used
`dpkg --print-architecture` inside the `RUN` instead.

Two things that closure does not license:

- **Publishing.** The image is secret-free AND public (US-008's flip, 2026-09-10). The
  `publish` lane is live (US-007, 2026-09-08): a push reaches GHCR only behind all six
  gates plus a layer-identity check; anonymous pulls were verified at the flip gate.
  `docs/releases.md` is the reference for the tag scheme and what a green publish proves.
- **Poppy's copy.** `services/retrieval/Dockerfile` in the monorepo still carries
  `ARG HF_TOKEN` and will until spec 6 pins Poppy to a published Forage image. Never push
  an image built from *that* file.

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
**1.1.0**), and consumers are expected to refuse activation on a major mismatch rather
than guess. Changing any response shape means: bump the version, update or add the golden
fixture under `tests/golden/`, and note the change for the consuming repo.
`feature-forage-contract` freezes the OpenAPI surface and formalises the bump policy —
read it before touching `pipeline/contract.py` or the response models in `models.py`.

Since US-002 the frozen surface is a file, `contract/openapi.yaml`, with a committed
`openapi.yaml.sha256` anchor beside it — the trust root every other copy of the contract
is verified against, because Release assets and registry tags are mutable and a checksum
in the git history is not. Both are **generated**; hand-editing either is always wrong.
Anything that moves the document — a response model, a `responses=` declaration, a field
description, a FastAPI bump — is followed by `uv run python -m scripts.export_contract`,
and `tests/test_contract_export.py` is red until it is.

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
uv run pytest           # full suite green, hermetic — blocking CI gate (count: TESTING_GUIDE.md; 1427 at contract US-001)
uv run ruff check .     # must stay clean — blocking CI gate
uv run ruff format .    # must stay clean — blocking CI gate
uv run pyright          # strict, ZERO errors — blocking CI gate
```

Always go through `uv run` — the lock pins the toolchain, and a system-installed ruff or
pyright will report numbers that don't reproduce.

All three are zero, with no baseline and no excludes. The single type-checking carve-out
in the repo is `reportPrivateUsage` for `tests/`; type-ignore comments are switched off
outright, so a suppression cannot quietly restore the green. The policy lives in
`pyproject.toml`'s `[tool.pyright]` comment and is asserted by
`tests/test_pyright_policy.py`. Third-party gaps go in `typings/` — minimal stubs
declaring only the symbols this repo calls; read `typings/README.md` before adding one.

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
`e6b2b56d…` to `2b8d7e9a…` here when the vault-free hostname defaults landed, again to
`cd00a8b4…` when the `ruff format` CI gate reformatted `pipeline/stage2_structural.py`,
once more to `0537316d…` when the pyright-strict burn-down retyped
`pipeline/stage1_extraction.py` and `pipeline/stage2_structural.py`, and now to
`5927038d…` — the one rotation that changed an *input* rather than a source
byte: the hashed model identity is `MODEL_ID@revision` since weights became a runtime,
per-deployment input (`forage-model-bootstrap` US-001) — and a fifth time to
`fa4691c5…` with the contract bump to `1.1.0`, the first rotation whose *point* is the
invalidation (`forage-cache-fallback` US-003 made the revision an input to the content
cache's key), and a sixth to `8b1b7f78…` when `contract.py` gained the seventeen-code
error vocabulary (`forage-contract` US-001 — documentation only, contract still `1.1.0`,
and the one rotation that whole spec gets). `docs/bootstrap-notes.md` carries
the before/after and the reasoning for each.
**Do not assume Poppy↔Forage revision parity** — compare contracts, not revisions.
