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
uv run pyright               # strict; 214-error backlog, owned by US-006
```

Both ruff commands are blocking jobs in `.github/workflows/ci.yml`. Run them before you
push — a red `lint` job costs a round trip on the free Actions tier.

**Always go through `uv run`.** The lock pins the toolchain (ruff 0.16.6, pyright 1.1.411);
a system-installed tool reports different numbers and the backlog counts will not
reproduce. This is also why `kit_tools/worktree.yaml` sets `run_prefix: uv run` — without
it the detached orchestrator inherits the ambient PATH, pytest collection crashes, and the
crash reads as a false regression.

---

## Test Structure

20 files under `tests/`, flat, one module per subject. **567 tests, all green** as of
2026-09-07.

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
| `tests/test_ci_workflow.py` | 30 | `ci.yml` shape: SHA pins, permissions, triggers, fork posture |
| `tests/test_stage5_url_audit.py` | 28 | Outbound fetch + redirect-chain audit |
| `tests/test_stage1_pdf.py` | 23 | PDF branch, subprocess isolation |
| `tests/test_searxng_docker.py` | 9 | `searxng/config/` sanity (config half only) |
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

**Async needs no decorator.** `asyncio_mode = "auto"` is set in `pyproject.toml`.

**The exact count is a gate, not a floor.** The extraction verified *exactly* 531 tests
moved (534 collected after alias parametrization), so a silently dropped module cannot
hide under a "≥ N passed" assertion. When you add tests, update the counts here.

**Tests ship with the code they cover, in the same commit.**

**Contract changes need the golden updated.** Any change to a response shape means bumping
`contract_version` in `pipeline/contract.py` and updating (or adding) the fixture under
`tests/golden/`.

---

## Test Mapping

Used by the KitTools orchestrator to pick the right tests for a changed file.

```yaml
test_mapping:
  "retrieval_app.py": "tests/test_app.py"
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
  ".github/workflows/ci.yml": "tests/test_ci_workflow.py"
  "uv.lock": "tests/test_dependency_lock.py"
  "pyproject.toml": "tests/test_dependency_lock.py"
```

The last three are non-Python sources. They still need mappings: without one the
orchestrator falls back to a heuristic glob over the whole suite, and a workflow or lock
edit either runs everything or nothing.

---

## What Is Not Covered by `pytest`

- **The container.** `docker build .` (token-less) and `docker run` + `curl /health` are
  manual checks today. `feature-forage-ci-and-image` automates them.
- **Live SearXNG / live Valkey.** Every test mocks them. A real end-to-end smoke against
  running companions is a manual step, and `feature-forage-cache-fallback` carries an
  explicit manual-smoke half.
- **Real PromptGuard weights.** `tests/test_stage3_promptguard.py` mocks transformers and
  torch, so the suite passes with no model present — which is correct, and also why the
  degraded path needs its own manual verification after a rebuild.
