<!-- Template Version: 2.0.0 -->
# CODE_ARCH.md

> Last updated: 2026-09-11
> Updated by: Claude (forage-contract US-005)

---

## Overview

Forage is a **single-process FastAPI service, 7,218 lines** (plus 1,146 lines of CI-only
smoke drivers and 1,124 lines of operator scripts under `scripts/`, none of which ship in
any image; all figures measured outside `tests/`), with a deliberately flat
module layout: the top-level modules sit at the repo root rather than inside a `forage/`
package. That is a decision, not an accident — it keeps the Dockerfile's `COPY` lines,
`pipeline/sanitizer_revision.py`'s hashed source paths, and the whole moved test suite
working unchanged after the extraction from Poppy. A cosmetic rename into a `forage/`
package is deferred to a later epic; **do not do it opportunistically.**

Design principles:

1. **Report, don't decide.** Every stage emits signals and confidence. Trust decisions
   belong to the calling agent. Nothing here silently drops content on a policy judgment
   the caller cannot see.
2. **Fail loud, never silently degrade.** A missing model, an unreachable cache, or a
   refused URL surfaces in `/health`'s body and in the response envelope. The service
   returns 200 with an honest body far more often than it returns an error code.
3. **Deterministic before probabilistic.** The cheap regex scan (stage 2) runs before ML
   inference (stage 3) so obvious attacks never reach the model.
4. **12-factor config.** Environment variables at container start plus a mounted
   `config.yaml`. No vault client, no config database, no secret-bearing runtime API.

---

## Directory Structure

```
.
├── retrieval_app.py         # FastAPI app: the 5 endpoints, startup, health/metrics,
│                            # and every response model the contract documents
├── models.py                # Pydantic request/response models
├── cache.py                 # Valkey/Redis content cache (closed log vocabulary)
├── url_validator.py         # SSRF defense: RFC1918 rejection, DNS-rebinding checks
├── model_fetcher.py         # Weight acquisition: the pinned revision, the HF fetch,
│                            # the GHCR mirror fallback (oras), exact-set manifest
│                            # verification, safetensors-only allowlist,
│                            # one-generation quarantine, and the single-flight
│                            # backoff retry that converges a degraded sidecar
├── weights_manifest.json    # The committed weight pin — the real five-entry
│                            # manifest since US-003's supervised vendoring run
├── contract_smoke.py        # CI-only: asserts a running image's /health contract.
├── searxng_smoke.py         # CI-only: stands the companion image up beside a
│                            # Valkey on an --internal network and probes it.
│                            # Not in the image — the Dockerfile COPY list is explicit
├── config.yaml              # UA pool, trusted domains, blocklist, thresholds, limits
├── Dockerfile               # CPU-torch image; digest-pinned base, uv.lock install,
│                            # secret-free (no build ARG, no baked weights)
├── docker-entrypoint.sh     # 17 lines: `exec "$@"`. Vault-free by design.
├── pyproject.toml           # uv/hatchling/ruff/pyright/pytest config
├── uv.lock                  # CPU-pinned torch on Linux; `grep nvidia-` must stay empty
├── pipeline/                # the five sanitization stages + orchestrator + contract
├── promptguard/             # Llama Prompt Guard 2 classifier wrapper
├── searxng/                 # the forage-searxng companion image: Dockerfile
│                            # (digest-pinned base) + config/ (settings.yml, limiter.toml)
├── scripts/                 # operator-only, run by hand: vendor_weights.py vendors the
│                            # pinned weights to the private GHCR mirror. Ships in no
│                            # image — the Dockerfile COPY list names nothing here
├── tests/                   # 27 files; flat, one module per subject
├── docs/                    # configuration.md, weights.md, releases.md, searxng.md,
│                            # bootstrap-notes.md, bootstrap-scan.txt
└── kit_tools/               # this documentation framework + feature specs
```

---

## Key Modules

| Module | Lines | Responsibility |
|--------|------:|----------------|
| `pipeline/orchestrator.py` | 853 | Drives the five stages end to end; owns the SearXNG engine list and `_DEFAULT_SEARXNG_URL`. The busiest file in the repo. |
| `retrieval_app.py` | 1563 | FastAPI app + the five endpoints, startup wiring, `/health` body assembly, the legacy-capability break-glass warning. Since `forage-contract` it also carries the documentation surface: the five per-shape error **mirrors** (US-001) and the six `/metrics` response models (US-005), all `extra="forbid"`, none of which any emission site routes through — the emission sites are unchanged and parity tests hold the models to them. The `FastAPI(...)` call serves `title="Forage"`, `version=CONTRACT_VERSION` and the no-auth/private-network posture, so `/openapi.json` cannot disagree with `/health` about which contract this process implements. |
| `cache.py` | 860 | Valkey content cache. **Never logs the connection URL** — it may carry a password; enforced by a closed log vocabulary and a dedicated regression test. |
| `models.py` | 313 | Pydantic models for every request and response shape. |
| `pipeline/stage4_structuring.py` | 308 | Assembles the response object and the composite trust score. |
| `pipeline/stage1_extraction.py` | 337 | HTML extraction → `raw_text` (for scanning) + `main_content` (for the agent). |
| `pipeline/stage5_url_audit.py` | 237 | Outbound fetch with manual redirect following and redirect-chain auditing. |
| `pipeline/pdf_subprocess.py` | 219 | PDF parsing isolated in a subprocess (pypdf is not trusted with hostile input in-process). |
| `pipeline/stage2_structural.py` | 304 | Deterministic regex injection scan. |
| `pipeline/smart_extraction.py` | 207 | Summary mode that preserves high-signal content (stats, quotes, references). |
| `url_validator.py` | 188 | Private-IP rejection and DNS-rebinding protection. |
| `pipeline/extraction_limits.py` | 181 | Resource limits from `config.yaml`'s `extraction:` block. |
| `pipeline/stage1_upload.py` | 172 | Upload path for `/extract` (gated by `extract_route_enabled`). |
| `promptguard/classifier.py` | 211 | Loads and runs Llama Prompt Guard 2 (`use_safetensors=True` — the loader can never fall back to a pickle); absent weights → degraded, never silent. |
| `model_fetcher.py` | 1872 | Weight acquisition end to end. The one gate every source passes — fail-closed manifest verification, exact-set + safetensors-only allowlist, symlink-resolving hashing over `snapshots/<revision>/`, one-generation quarantine, the `ModelMetrics` counters `/metrics` exports — plus `acquire_and_load()`, the boot pipeline the lifespan runs in a worker thread: verify the cache, then **Hugging Face, then the GHCR mirror**, then one ERROR naming both. The mirror leg shells out to the image's pinned `oras`, extracts with `filter="data"` into a bounded staging area, verifies *there*, and installs by rename. Owns the revision pin, the `$HF_HOME/hub` resolution both the download and the loader are handed, and the five environment variables the acquisition path reads. `WeightAcquisition` wraps that pipeline in the service's only background loop: single-flight, 30 s→10 min jittered backoff, cancellable, and a hub-offline pin scoped to the load so a warm start makes zero network attempts. |
| `pipeline/stage1_pdf.py` | 156 | PDF branch of stage 1. |
| `pipeline/stage3_promptguard.py` | 151 | ML injection scan; skipped for trusted domains. |
| `pipeline/contract.py` | 108 | The versioned response contract (`contract_version`, currently **1.1.0**). |
| `contract_smoke.py` | 367 | CI's published-image smoke: polls a running container's `/health`, validates it against the same `HealthResponse` model the golden test pins, and reads every wire value from `pipeline/contract.py` at run time. Ships in no image. |
| `searxng_smoke.py` | 779 | CI's companion-image smoke: creates an egress-free Docker network, runs SearXNG beside a Valkey and probes it from a third container. Docker goes through an injected runner and every judgement is a pure function, so `tests/test_searxng_smoke.py` covers the failure branches without a daemon. Ships in no image. |
| `scripts/vendor_weights.py` | 1112 | Operator-only, supervised: downloads the pinned revision, generates `weights_manifest.json` with the safetensors allowlist enforced **at generation time**, builds a deterministic symlink-dereferenced tarball, self-checks it through the real verifier, `oras push`es it tagged by revision sha, and confirms the GHCR package is private. Every constant comes from `model_fetcher`; no credential ever reaches an argv. Ships in no image; `docs/weights.md` is the procedure. |
| `pipeline/sanitizer_revision.py` | 42 | Hashes eight source files into a `sanitizer_revision` string. See the gotcha below. |

---

## Patterns That Matter

**The sanitizer revision is a content hash of source files *and of the model pin*.**
`sanitizer_revision.py` resolves `_REVISION_SOURCES` relative to its own file and hashes
them, then the model identity (`MODEL_ID@revision`) and the active threshold; the value
ships in every `/health` body and response envelope so a consumer can tell which
sanitizer version produced a result. Editing any of those eight files changes it — that
is the intent, but it means Forage's revision has **deliberately diverged** from Poppy's
since the vault-free config work (`e6b2b56d…` → `2b8d7e9a…`), moved again when the
`ruff format` CI gate reformatted `stage2_structural.py` (`2b8d7e9a…` → `cd00a8b4…`) — a
format-only rotation, taken deliberately at gate installation — a third time when the
pyright-strict burn-down retyped `stage1_extraction.py` and `stage2_structural.py`
(`cd00a8b4…` → `0537316d…`), a fourth when the weights became a runtime input and the
pinned revision joined the identity (`0537316d…` → `5927038d…`, no source byte moved), and
a fifth with the contract bump to `1.1.0` (`5927038d…` → `fa4691c5…`, `contract.py` +
`orchestrator.py`) — the first rotation taken *for* the invalidation rather than despite
it, now that the revision keys the content cache — and a sixth when the error vocabulary
joined `contract.py` (`fa4691c5…` → `8b1b7f78…`, `forage-contract` US-001: typing and
docstrings only, zero wire bytes changed, contract still `1.1.0`).
Nothing downstream may assume Poppy↔Forage revision parity.

**Startup is non-blocking, and one background task is the reason.** The lifespan does its
synchronous wiring, starts weight acquisition as
`asyncio.create_task(model_fetcher.WeightAcquisition(...).run())`, and yields — it never
awaits the fetch. uvicorn serves nothing until lifespan startup returns, so an `await`
there would hold the port closed for the length of a ~270 MiB download and a compose
healthcheck would restart-loop the container. The handle lives on `app.state.model_task`
and is cancelled at shutdown. `/health` reads `classifier.loaded` per request, so
`promptguard_loaded` flips in place when the load lands; `/metrics`'
`model.fetch_in_progress` is what tells "downloading" from "wedged" while it has not.

**That task is a retry loop, and it has three properties worth knowing** (US-005).
`WeightAcquisition.run()` retries acquisition on a **30 s backoff doubling to a 10-minute
cap, jittered ±20%** — normative constants, deliberately not environment variables —
until the classifier loads, so an outage or a late gated-repo approval converges without
a restart; `model.retries_scheduled` and a per-retry WARNING are what distinguish
"waiting" from "wedged". Exactly **one acquisition is in flight at a time**: the lock is
held for the whole of `attempt_once()`, and a second caller is turned away rather than
queued, because queuing means a second ~270 MiB download the moment the first ends
(`app.state.model_acquisition` is the object any future caller must go through). And the
load itself runs under a **scoped hub-offline pin** — `local_files_only=True` does not
stop `huggingface_hub` from fetching its agent-harness registry while building headers,
and a warm start has to mean zero attempts, not zero successes.

**The response contract is versioned and consumers refuse on a mismatch.** Any change to
a response shape is a contract change: bump `contract_version` in `pipeline/contract.py`,
update `tests/golden/contract_1_0_0.json` (or add a new golden), and note it for the
consuming repo. `feature-forage-contract` freezes the OpenAPI surface and formalizes this
governance.

**Health is a body, not a status code.** `/health` always returns 200. `status` is
`healthy` or `degraded`, with machine-readable `degraded_reasons`
(`promptguard_unavailable`, `cache_unavailable`). Never "fix" a degraded report by
loosening the check — a silent version of this failure once ran unnoticed for nine days
in production.

**Config has exactly two sources.** Environment variables and the mounted `config.yaml`.
Every variable and key is documented in `docs/configuration.md`; adding one means adding
it there in the same change.

---

## Deliberate Non-Structure

- **No `forage/` package directory.** Flat by decision (see Overview).
- **No auth layer.** Deployment posture is "private network only", stated in the README's
  first screen. Do not add half-measures that read as authentication without being it.
- **No vault/OpenBao client.** Removed at extraction; the entrypoint is `exec "$@"`. A
  `VAULT_` grep should hit only `docs/bootstrap-scan.txt` and history.
- **No database.** State is the content cache; it is allowed to be absent.
