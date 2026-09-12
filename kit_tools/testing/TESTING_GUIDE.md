<!-- Template Version: 2.1.0 -->
# TESTING_GUIDE.md

> Last updated: 2026-09-11
> Updated by: Claude (forage-contract US-004)

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
own red line instead of as nine failures inside a full-suite log.

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

**29 `test_*.py` modules** under `tests/`, flat, one per subject — 32 Python files in all
once `conftest.py`, `fakes.py` and `__init__.py` are counted — plus `golden/` and
`fixtures/`. (Both numbers measured 2026-09-11; state the convention with the count, or
the next person reconciles two different ones by increment.) **1608 tests, all green** as
of 2026-09-11 (`feature-forage-contract` US-001 added `test_contract_errors.py`'s 25,
US-005 `test_contract_metrics.py`'s 20, US-002 `test_contract_export.py`'s 18, US-003
`test_governance_docs.py`'s 41 plus 26 in `test_ci_workflow.py`, and US-004 another 76
spread across four existing modules — 34 workflow-shape, 29 smoke, 11 Dockerfile, 2
governance; the per-module counts in the table below have not all been re-measured since
2026-09-10).

| Module | Tests | Covers |
|--------|------:|--------|
| `tests/test_model_fetcher.py` | 212 | `model_fetcher.py`: fail-closed manifest verification, exact-set + safetensors-only allowlist, symlink-resolving hashing, one-generation quarantine, the loadable safetensors fixture, the acquisition pipeline (revision pin, `$HF_HOME/hub` resolution, the mocked HF fetch, the `oras` mirror leg, token redaction), and US-005's warm start + retry loop — the counted-attempt proof that a warm load reaches no network, the normative 30 s→10 min jittered schedule, quarantine→re-fetch→loaded recovery on the real loader, single-flight, and clean cancellation |
| `tests/test_vendor_weights.py` | 102 | `scripts/vendor_weights.py`: the symlink-dereferenced tarball (built, extracted, bytes compared), tar determinism, generation-time allowlist refusal, the manifest round-trip through the real verifier, credential hygiene on the `oras` path, and the private-package visibility check — all fixture-driven, no registry and no token |
| `tests/test_stage2_structural.py` | 78 | Deterministic regex injection scan |
| `tests/test_orchestrator.py` | 63 | End-to-end pipeline drive, search + retrieve paths |
| `tests/test_url_validator.py` | 59 | SSRF defense: RFC1918, DNS rebinding, schemes |
| `tests/test_cache.py` | 116 | Valkey cache incl. the never-log-the-URL invariant |
| `tests/test_smart_extraction.py` | 45 | Summary mode / high-signal preservation |
| `tests/test_stage1_extraction.py` | 41 | HTML extraction, `raw_text` vs `main_content` |
| `tests/test_stage4_structuring.py` | 39 | Response assembly + composite trust score |
| `tests/test_models.py` | 31 | Pydantic request/response models |
| `tests/test_app.py` | 60 | FastAPI endpoints, `/health` body, capability break-glass, the `/metrics` `model` counters, and the lifespan harness: startup yields immediately, `/health` latency during a fetch, the `promptguard_loaded` flip, and the retry task's cancellation at shutdown |
| `tests/test_stage3_promptguard.py` | 30 | ML scan; transformers/torch mocked |
| `tests/test_ci_workflow.py` | 269 | `ci.yml` shape: SHA pins, permissions, triggers, fork posture, job graph, test lane, image build + secret-grep gate, smoke job + artifact handoff, both publish lanes (tag policies evaluated, not matched), the cross-fire guards between them, and — since US-003 — the image↔contract mapping: the version is read from the tagged tree, the Release body is written from it, the published body is read back and asserted, and no version literal may appear in the job's shell. US-004 adds the Release assets (attached by the create call, downloaded back and verified against the committed anchor) and the reproducible-export guards: one identical SOURCE_DATE_EPOCH script in all four building jobs, `rewrite-timestamp=true` on every exporter, and the longhand `type=docker` / `type=image,push=true` forms that can carry it |
| `tests/test_compose_fragments.py` | 56 | Compose fragments, parse-only shape guards (audit: row was missing) |
| `tests/test_contract_smoke.py` | 76 | `contract_smoke.py`: every `/health` clause, polling, the single-source ties to the golden schema, and — since US-004 — the in-image contract checks: the `docker run --rm --entrypoint cat` argv it builds, the anchor comparisons against the committed trust root, and the `info.version` ↔ live `contract_version` claim, all driven through an injected runner so the suite never starts a container |
| `tests/test_stage5_url_audit.py` | 28 | Outbound fetch + redirect-chain audit |
| `tests/test_stage1_pdf.py` | 23 | PDF branch, subprocess isolation |
| `tests/test_dockerfile.py` | 50 | `Dockerfile` text: no secret may enter the build, digest-pinned base, lock-driven install, and — since US-004 — that the frozen contract is COPYed to `/app/contract/` (with `.dockerignore` checked for a pattern that would silently empty it) and that the two reproducibility normalizations stay: no timestamped apt artefacts, no bytecode from the import check |
| `tests/test_pyright_policy.py` | 12 | Type-checking policy: strict, one carve-out, no suppressions |
| `tests/test_searxng_smoke.py` | 61 | `searxng_smoke.py`: every evaluator branch, the Docker argv it builds, and the `--internal` wiring |
| `tests/test_searxng_docker.py` | 28 | `searxng/Dockerfile` + baked config: the negatives (no wildcard pass list, no baked secret, no header trust) and engine parity with `_SEARXNG_ENGINES` |
| `tests/test_hermeticity.py` | 10 | Executing canary for the autouse socket guard |
| `tests/test_sanitizer_revision.py` | 9 | Revision hashing over `_REVISION_SOURCES` and the `MODEL_ID@revision` model identity |
| `tests/test_dependency_lock.py` | 3 | `uv.lock` stays CPU-only (no `nvidia-*` wheels) |
| `tests/test_contract_errors.py` | 25 | The documented error surface: the seventeen-code vocabulary swept from every raise site in the repo, pinned against Poppy's inlined allowlist; per-emission-site parity (each mirror model reproduces the live body byte-for-byte, driven through the real routes); the `responses=` declaration map; and the FastAPI 422-suppression behaviour the union declarations rest on |
| `tests/test_contract_metrics.py` | 20 | The typed `/metrics` body and the served app metadata: parity between the handler's dict and the bytes the typed route sends (compared *outside* the model, so a reorder at any depth is caught), the flat cgroup keys in both wire and schema, the `extra="forbid"` failure mode and the permissive-model counterfactual it avoids, the dataclass-counter ↔ model field ties, `info.version == CONTRACT_VERSION`, and the mechanical check that every path FastAPI serves — `/docs`, `/redoc` and `/openapi.json` included — is acknowledged in `docs/configuration.md`'s posture section |
| `tests/test_contract_export.py` | 18 | The frozen `contract/openapi.yaml`: that the committed bytes are what the app generates, that the committed `.sha256` anchor is the sha256 of those bytes in `sha256sum -c` form, that the render is byte-stable across processes and `PYTHONHASHSEED` values (measured in subprocesses, not asserted), that the canonical form round-trips and carries no YAML anchors, and that `/extract` is documented while `extract_route_enabled` is `false`. The drift check's own failure case is committed as `tests/fixtures/contract/unregenerated_openapi.yaml` and fed to the same checker |
| `tests/test_governance_docs.py` | 43 | The three governance documents US-003 adds — `contract/GOVERNANCE.md`, `SECURITY.md`, `.github/pull_request_template.md` — held to the code they describe: the stated contract version against `CONTRACT_VERSION`, the regeneration command against `scripts.export_contract.REGEN_COMMAND`, the hashed-source count against `_REVISION_SOURCES`, the PR template's required-checks sentence against the jobs `publish` hangs off, the six worked examples parsed out of the table and checked for exactly one classification each (two are deliberately two-valued), the five recorded rulings present with a citation that resolves, and every relative link in all three files |
| `tests/test_contract_schema.py` | 1 | Golden contract fixture vs `pipeline/contract.py` |

Support files:

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Puts the repo root on `sys.path`; installs the autouse socket guard |
| `tests/fakes.py` | Shared fakes and builders: the fake cache, `assert_frozen`, the Hugging Face cache-layout helpers (`materialize_hub_snapshot`, `hub_download_double`, `weights_manifest_document`) that `test_model_fetcher.py` and `test_app.py` both build fixtures from, and `record_network_attempts` — which *counts* outbound attempts rather than only refusing them, because a library that swallows the guard's error makes "blocked" and "never tried" look identical |
| `tests/fixtures/tiny_model/` | A real, loadable 2-layer DeBERTa-v2 classifier (~96 KB, safetensors only) — the fixture that lets the *actual* loader be exercised rather than mocked |
| `tests/fixtures/contract/unregenerated_openapi.yaml` | The contract drift check's committed failure case: `contract/openapi.yaml` with `Extract422ErrorResponse.sanitizer_revision` removed — what the file would look like if a response model had changed and nobody regenerated. Written by `scripts/export_contract.py` alongside the contract, so one command keeps both in step |
| `tests/golden/contract_1_0_0.json` | Frozen contract fixture for `test_contract_schema.py`; `contract_1_1_0.json` is the current one. The fixture pins `model_json_schema()`, which moves for description and enum-rendering changes as well as wire ones — regenerate it for a documentation-only change, bump `CONTRACT_VERSION` (new file alongside the old) for a real one |

### Testing the lifespan

`tests/test_app.py`'s `client` fixture drives the app through `httpx.ASGITransport`,
which **never fires lifespan events**. That is fine for endpoint tests and useless for
startup ones: every assertion about what the lifespan does would pass vacuously. Since
`forage-model-bootstrap` US-001 the module carries a second harness, `_running_app()`,
which enters the real `lifespan(app)` context manager with only `ContentCache` replaced.
Anything asserting startup ordering, `app.state` population, background tasks or shutdown
belongs there; anything else should stay on the cheaper fixture.

The weight-acquisition tests mock exactly one thing — `huggingface_hub.snapshot_download`
— and let the real verifier, the real cache layout and the real
`PromptGuardClassifier.load()` run against the committed tiny-model fixture. The autouse
socket guard is what makes that a proof rather than a hope: a load that reached the
network would fail loudly instead of passing slowly.

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
`forage-cache-fallback` US-002 added two more for `conftest.py`'s *other* autouse
fixture, the one that clears inherited environment settings (`HF_TOKEN`, `HF_HOME`, the
three `FORAGE_*` weight variables, and `VALKEY_URL` — which since that story selects the
cache backend): an exact-set gate on the list and a canary that none of them survives
into a test.
`TestHermeticityCanaryIsEnforced` in `tests/test_ci_workflow.py` covers the one thing the
canary cannot check about itself: that it is committed and not skipped.

**Async needs no decorator.** `asyncio_mode = "auto"` is set in `pyproject.toml`.

**The exact count is a gate, not a floor.** The extraction verified *exactly* 531 tests
moved (534 collected after alias parametrization); the suite has since grown (1427 at contract US-001 — the headline above is current) as CI
guards landed (US-001 +33, US-006 +19, US-002 +21, US-003 +49, US-005 +75, US-007 +40; then
`forage-model-bootstrap` US-002 +87, US-001 +56, US-003 +102, US-004 +76 and US-005 +24;
then `forage-cache-fallback` US-001 +63 and US-002 +13; then `forage-contract` US-001 +25,
US-005 +20, US-002 +18 and US-003 +67). A silently
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

**And the frozen contract regenerated.** Since US-002 anything that moves the OpenAPI
document — a response model, a `responses=` declaration, a field description, a FastAPI
bump — must be followed by:

```bash
uv run python -m scripts.export_contract   # rewrites all three artifacts
```

`tests/test_contract_export.py` fails until you do, naming that command. Commit
`contract/openapi.yaml`, `contract/openapi.yaml.sha256` and
`tests/fixtures/contract/unregenerated_openapi.yaml` together — one command writes all
three and they are only consistent as a set. Never hand-edit any of them.

---

## Test Mapping

Used by the KitTools orchestrator to pick the right tests for a changed file.

```yaml
test_mapping:
  "retrieval_app.py": ["tests/test_app.py", "tests/test_contract_smoke.py", "tests/test_contract_errors.py", "tests/test_contract_metrics.py", "tests/test_contract_export.py"]
  "models.py": ["tests/test_models.py", "tests/test_contract_errors.py", "tests/test_contract_export.py"]
  "cache.py": "tests/test_cache.py"
  "url_validator.py": "tests/test_url_validator.py"
  "pipeline/orchestrator.py": "tests/test_orchestrator.py"
  "pipeline/contract.py": ["tests/test_contract_schema.py", "tests/test_contract_errors.py", "tests/test_contract_export.py"]
  "pipeline/sanitizer_revision.py": ["tests/test_sanitizer_revision.py", "tests/test_governance_docs.py"]
  "model_fetcher.py": ["tests/test_model_fetcher.py", "tests/test_app.py"]
  "weights_manifest.json": "tests/test_model_fetcher.py"
  "tests/fakes.py": ["tests/test_model_fetcher.py", "tests/test_app.py"]
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
  "promptguard/classifier.py": ["tests/test_stage3_promptguard.py", "tests/test_model_fetcher.py"]
  "searxng/config/*": "tests/test_searxng_docker.py"
  "searxng/Dockerfile": "tests/test_searxng_docker.py"
  "searxng_smoke.py": "tests/test_searxng_smoke.py"
  "tests/conftest.py": "tests/test_hermeticity.py"
  ".github/workflows/ci.yml": "tests/test_ci_workflow.py"
  "Dockerfile": ["tests/test_dockerfile.py", "tests/test_contract_smoke.py"]
  "contract_smoke.py": "tests/test_contract_smoke.py"
  "uv.lock": "tests/test_dependency_lock.py"
  "pyproject.toml": ["tests/test_dependency_lock.py", "tests/test_pyright_policy.py"]
  "typings/*": "tests/test_pyright_policy.py"
  "docs/configuration.md": "tests/test_contract_metrics.py"
  "contract/openapi.yaml": ["tests/test_contract_export.py", "tests/test_contract_smoke.py"]
  "contract/openapi.yaml.sha256": ["tests/test_contract_export.py", "tests/test_contract_smoke.py"]
  "scripts/export_contract.py": ["tests/test_contract_export.py", "tests/test_governance_docs.py"]
  "contract/GOVERNANCE.md": "tests/test_governance_docs.py"
  "SECURITY.md": "tests/test_governance_docs.py"
  ".github/pull_request_template.md": "tests/test_governance_docs.py"
```

`tests/conftest.py` maps to the canary because the fixture it installs is the thing under
test there — an edit to the socket guard must run `tests/test_hermeticity.py`.

The non-Python entries at the end — the workflow, the Dockerfile, the lock,
`pyproject.toml`, `typings/` (stub files no test imports) and `docs/configuration.md`
(whose deployment-posture section `tests/test_contract_metrics.py` checks against the
paths FastAPI actually serves) — are the ones most easily left unmapped.
They still need mappings: without one the orchestrator falls back to a heuristic glob over
the whole suite, and a workflow, lock, Dockerfile or config edit either runs everything or
nothing. `pyproject.toml` maps to two modules because it carries two independently-guarded
concerns: the CPU-only dependency lock and the type-checking policy.

`retrieval_app.py`, `models.py` and `pipeline/contract.py` all gained
`tests/test_contract_export.py` in `feature-forage-contract` US-002. That is the whole
point of the drift check: the three files that can move the OpenAPI document must run the
test that notices, or a response-model edit reaches a PR with `contract/openapi.yaml` still
describing the old shape.

US-004 widened three entries rather than adding a module. `Dockerfile` now also runs
`tests/test_contract_smoke.py`, because the smoke's in-image paths are tied to the COPY
destination `tests/test_dockerfile.py` asserts — move the destination and the tie is what
notices. `contract/openapi.yaml` and its anchor gained it too: the smoke's in-image checks
are driven against the *committed* pair, so a regeneration that left one of them behind
fails there as well as in the export test.

US-003 added three **Markdown** entries — `contract/GOVERNANCE.md`, `SECURITY.md` and
`.github/pull_request_template.md` — for the same reason `docs/configuration.md` has one:
each makes mechanical claims (a contract version, a regeneration command, a file count, the
list of required checks) that `tests/test_governance_docs.py` holds to the code. Two
*source* files gained it in the other direction: `pipeline/sanitizer_revision.py`, because
the PR template states how many files rotate the revision, and
`scripts/export_contract.py`, because two documents name its regeneration command and the
module owns the string they are compared against.

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
  degraded path needs its own manual verification after a rebuild.
- **A real Hugging Face download.** CI has no token and the socket guard is autouse, so
  every fetch test drives a `snapshot_download` double. What that *does* prove is
  everything downstream of the transport: the arguments the fetcher passes, the cache
  tree the bytes land in, the verification, and a real `from_pretrained` opening the
  result. What it does **not** prove is that the gated repo answers, that the pinned
  revision exists upstream, or how long ~270 MiB takes on the reference container.
  `feature-forage-model-bootstrap` US-003 is the supervised session that holds a real
  token and records that measurement.
