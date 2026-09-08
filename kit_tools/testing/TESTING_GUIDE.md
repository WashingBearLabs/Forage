<!-- Template Version: 2.1.0 -->
# TESTING_GUIDE.md

> Last updated: 2026-09-07
> Updated by: Claude (forage-repo-bootstrap US-005)

## Quick Start

```bash
uv run pytest
uv run ruff check .
```

Full set of commands:

```bash
# Environment (once per checkout / worktree)
uv sync --extra dev

# Everything
uv run pytest

# One module
uv run pytest tests/test_cache.py -q

# One test
uv run pytest tests/test_orchestrator.py -q -k test_search_returns_sanitized_results

# Static analysis
uv run ruff check .          # CI gate: must be clean
uv run ruff format --check . # CI gate since US-001: must be clean
uv run pyright               # CI gate since US-006: strict, zero errors
```

All four are blocking jobs in `.github/workflows/ci.yml` (`lint`, `typecheck` and, since
US-002, `test`). Run them before you push — a red gate costs a round trip on the free
Actions tier.

**Ten jobs, in two lanes.** The *service* lane runs `lint`, `typecheck`, `test`,
`build-amd64` and `secret-grep` (US-003) and `smoke` (US-005) on every push and PR, plus
`publish` (US-007) on a push to `main` or a `v*` tag, which `needs:` all six — see
[`docs/releases.md`](../../docs/releases.md) for the tag scheme and what a green publish
does and does not prove. The *companion* lane (US-004) is `searxng-build`,
`searxng-smoke` and `searxng-publish`, keyed off `searxng-v*` tags and documented in
[`docs/searxng.md`](../../docs/searxng.md); `searxng-publish` `needs:` its own two plus
`test`. The two lanes are mutually exclusive by ref prefix: a `searxng-v*` tag skips
every service-image job and a `v*` tag skips all three companion jobs, each asserted by
*evaluating* the job's own condition against both tag shapes.

`searxng-smoke` is worth knowing about separately: its blocking half stands the companion
image up beside a Valkey on a `--internal` Docker network (no egress at all) and runs four
phases through `searxng_smoke.py`, while a `--live` probe that reaches real engines runs
`continue-on-error: true` afterwards. Reproduce either with
`uv run python searxng_smoke.py --image forage-searxng:ci [--live]`.

`smoke` is the only service-lane job that *runs*
the image rather than inspecting it — it starts the built container with no Hugging Face
token and asserts the degraded `/health` contract through `contract_smoke.py`. You can
run exactly what it runs:

```bash
docker build -t forage:ci .
docker run -d --name forage-smoke -p 8020:8020 forage:ci
uv run python contract_smoke.py --base-url http://127.0.0.1:8020
docker rm -f forage-smoke
```

Expect `status: "degraded"` with `promptguard_unavailable`. A weights-free image
reporting `healthy` is the nine-day production failure this repo exists not to repeat,
and the smoke is the mechanical guard against it.

**`test` is the job that makes the rest real.** Until it landed, nothing in CI ran pytest,
so `test_ci_workflow.py`, `test_pyright_policy.py`, `test_dependency_lock.py` and the
hermeticity canary all bit only on a developer's machine. The job runs `uv run pytest -q`
with no selection filters, preceded by a named `uv run pytest -q
tests/test_sanitizer_revision.py` step so the file-recall cache contract reports as its
own red line instead of as six failures inside a 607-test log.

`pyright` runs with **no baseline and one carve-out**: `reportPrivateUsage` is off for
`tests/` and nothing else is relaxed anywhere. Type-ignore comments are disabled
(`enableTypeIgnoreComments = false`), so the only way to make pyright quiet is to make the
types right — or, for a genuine third-party gap, to add a minimal stub under `typings/`.
`tests/test_pyright_policy.py` fails if any of that drifts.

**Always go through `uv run`.** The lock pins the toolchain (ruff 0.16.6, pyright 1.1.411);
a system-installed tool reports different numbers and the backlog counts will not
reproduce. This is also why `kit_tools/worktree.yaml` sets `run_prefix: uv run` — without
it the detached orchestrator inherits the ambient PATH, pytest collection crashes, and the
crash reads as a false regression.

---

## Test Structure

25 files under `tests/`, flat, one module per subject. **905 tests, all green** as of
2026-09-08.

| Module | Tests | Covers |
|--------|------:|--------|
| `tests/test_stage2_structural.py` | 78 | Deterministic regex injection scan |
| `tests/test_orchestrator.py` | 61 | End-to-end pipeline drive, search + retrieve paths |
| `tests/test_url_validator.py` | 59 | SSRF defense: RFC1918, DNS rebinding, schemes |
| `tests/test_cache.py` | 52 | Valkey cache incl. the never-log-the-URL invariant |
| `tests/test_smart_extraction.py` | 45 | Summary mode / high-signal preservation |
| `tests/test_stage1_extraction.py` | 41 | HTML extraction, `raw_text` vs `main_content` |
| `tests/test_stage4_structuring.py` | 39 | Response assembly + composite trust score |
| `tests/test_models.py` | 31 | Pydantic request/response models |
| `tests/test_app.py` | 31 | FastAPI endpoints, `/health` body, capability break-glass |
| `tests/test_stage3_promptguard.py` | 30 | ML scan; transformers/torch mocked |
| `tests/test_ci_workflow.py` | 201 | `ci.yml` shape: SHA pins, permissions, triggers, fork posture, job graph, test lane, image build + secret-grep gate, smoke job + artifact handoff, both publish lanes (tag policies evaluated, not matched) and the cross-fire guards between them |
| `tests/test_contract_smoke.py` | 47 | `contract_smoke.py`: every `/health` clause, polling, and the single-source ties to the golden schema |
| `tests/test_stage5_url_audit.py` | 28 | Outbound fetch + redirect-chain audit |
| `tests/test_stage1_pdf.py` | 23 | PDF branch, subprocess isolation |
| `tests/test_dockerfile.py` | 20 | `Dockerfile` text: no secret may enter the build, digest-pinned base, lock-driven install |
| `tests/test_pyright_policy.py` | 12 | Type-checking policy: strict, one carve-out, no suppressions |
| `tests/test_searxng_smoke.py` | 61 | `searxng_smoke.py`: every evaluator branch, the Docker argv it builds, and the `--internal` wiring |
| `tests/test_searxng_docker.py` | 28 | `searxng/Dockerfile` + baked config: the negatives (no wildcard pass list, no baked secret, no header trust) and engine parity with `_SEARXNG_ENGINES` |
| `tests/test_hermeticity.py` | 8 | Executing canary for the autouse socket guard |
| `tests/test_sanitizer_revision.py` | 6 | Revision hashing over `_REVISION_SOURCES` |
| `tests/test_dependency_lock.py` | 3 | `uv.lock` stays CPU-only (no `nvidia-*` wheels) |
| `tests/test_contract_schema.py` | 1 | Golden contract fixture vs `pipeline/contract.py` |

Support files:

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Puts the repo root on `sys.path`; installs the autouse socket guard |
| `tests/fakes.py` | Shared fakes (fake cache, fake HTTP responses) |
| `tests/golden/contract_1_0_0.json` | Frozen contract fixture for `test_contract_schema.py` |

---

## Rules

**The suite is hermetic and must stay that way.** `tests/conftest.py` installs an autouse
`pytest-socket` fixture (`disable_socket(allow_unix_socket=True)`). A test that touches the
real network fails loudly with `SocketBlockedError`. Mock at the seam — httpx,
`socket.getaddrinfo`, transformers/torch — never relax the guard. It caught three real DNS
calls in the cache tests the day it was added.

Since US-002 the guard has an executing canary, `tests/test_hermeticity.py`: eight tests
that assert TCP/UDP/IPv6 construction, both DNS entry points and `create_connection` are
blocked while `AF_UNIX` socketpairs and async tests still work. Delete or weaken the
fixture and those tests go red — before the canary, the loss produced no failure at all.
`TestHermeticityCanaryIsEnforced` in `tests/test_ci_workflow.py` covers the one thing the
canary cannot check about itself: that it is committed and not skipped.

**Async needs no decorator.** `asyncio_mode = "auto"` is set in `pyproject.toml`.

**The exact count is a gate, not a floor.** The extraction verified *exactly* 531 tests
moved (534 collected after alias parametrization); the suite has since grown to 905 as CI
guards landed (US-001 +33, US-006 +19, US-002 +21, US-003 +49, US-005 +75, US-007 +40). A silently
dropped module cannot hide under a "≥ N passed" assertion. When you add tests, update the
counts here.

**Contract expectations have one source.** `contract_smoke.py` validates a live
`/health` body against the same `HealthResponse` model `tests/test_contract_schema.py`
pins against the golden fixture, and imports every wire value it compares
(`CONTRACT_VERSION`, the degraded reason, the capability key) rather than restating it.
`tests/test_contract_smoke.py::TestSingleSourceOfTruth` asserts that mechanically — same
objects, and no wire literal anywhere in the smoke's code. If you find yourself typing a
field name or a version into a workflow's shell, that is the thing this arrangement
exists to prevent.

**Tests ship with the code they cover, in the same commit.**

**Contract changes need the golden updated.** Any change to a response shape means bumping
`contract_version` in `pipeline/contract.py` and updating (or adding) the fixture under
`tests/golden/`.

---

## Test Mapping

Used by the KitTools orchestrator to pick the right tests for a changed file.

```yaml
test_mapping:
  "retrieval_app.py": ["tests/test_app.py", "tests/test_contract_smoke.py"]
  "models.py": "tests/test_models.py"
  "cache.py": "tests/test_cache.py"
  "url_validator.py": "tests/test_url_validator.py"
  "pipeline/orchestrator.py": "tests/test_orchestrator.py"
  "pipeline/contract.py": "tests/test_contract_schema.py"
  "pipeline/sanitizer_revision.py": "tests/test_sanitizer_revision.py"
  "pipeline/stage1_extraction.py": "tests/test_stage1_extraction.py"
  "pipeline/stage1_pdf.py": "tests/test_stage1_pdf.py"
  "pipeline/pdf_subprocess.py": "tests/test_stage1_pdf.py"
  "pipeline/stage1_upload.py": "tests/test_app.py"
  "pipeline/stage2_structural.py": "tests/test_stage2_structural.py"
  "pipeline/stage3_promptguard.py": "tests/test_stage3_promptguard.py"
  "pipeline/stage4_structuring.py": "tests/test_stage4_structuring.py"
  "pipeline/stage5_url_audit.py": "tests/test_stage5_url_audit.py"
  "pipeline/smart_extraction.py": "tests/test_smart_extraction.py"
  "pipeline/extraction_limits.py": "tests/test_stage1_extraction.py"
  "promptguard/classifier.py": "tests/test_stage3_promptguard.py"
  "searxng/config/*": "tests/test_searxng_docker.py"
  "searxng/Dockerfile": "tests/test_searxng_docker.py"
  "searxng_smoke.py": "tests/test_searxng_smoke.py"
  "tests/conftest.py": "tests/test_hermeticity.py"
  ".github/workflows/ci.yml": "tests/test_ci_workflow.py"
  "Dockerfile": "tests/test_dockerfile.py"
  "contract_smoke.py": "tests/test_contract_smoke.py"
  "uv.lock": "tests/test_dependency_lock.py"
  "pyproject.toml": ["tests/test_dependency_lock.py", "tests/test_pyright_policy.py"]
  "typings/*": "tests/test_pyright_policy.py"
```

`tests/conftest.py` maps to the canary because the fixture it installs is the thing under
test there — an edit to the socket guard must run `tests/test_hermeticity.py`.

The last five are non-Python sources (or, for `typings/`, stub files no test imports).
They still need mappings: without one the orchestrator falls back to a heuristic glob over
the whole suite, and a workflow, lock, Dockerfile or config edit either runs everything or
nothing. `pyproject.toml` maps to two modules because it carries two independently-guarded
concerns: the CPU-only dependency lock and the type-checking policy.

---

## What Is Not Covered by `pytest`

- **Building and running the container.** `tests/test_dockerfile.py` guards the
  Dockerfile's *text*, not a build — pytest never invokes Docker, and the suite stays
  hermetic. The build itself is CI's `build-amd64` job (US-003) and the runtime `/health`
  contract is the `smoke` job (US-005); `docker run` + `curl /health` remains a manual
  check locally.
- **Live SearXNG / live Valkey.** Every test mocks them. A real end-to-end smoke against
  running companions is a manual step, and `feature-forage-cache-fallback` carries an
  explicit manual-smoke half.
- **Real PromptGuard weights.** `tests/test_stage3_promptguard.py` mocks transformers and
  torch, so the suite passes with no model present — which is correct, and also why the
  degraded path needs its own manual verification after a rebuild. Since US-003 removed
  the build-time bake, *every* built image is in that state until
  `feature-forage-model-bootstrap` lands.
