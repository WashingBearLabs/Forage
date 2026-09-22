<!-- Template Version: 2.0.0 -->
# CODE_ARCH.md

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-hostname-and-config US-001)

---

## Overview

Forage is a **single-process FastAPI service, 10,337 lines** (plus 1,538 lines of CI-only
smoke drivers and 1,439 lines of operator scripts under `scripts/` (measured `wc -l`, sum of the table below), none of which ship in
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
│                            # secret-free (no build ARG, no baked weights), ships
│                            # contract/ at /app/contract/, and normalized for
│                            # reproducibility (no apt logs, no import-time .pyc)
├── docker-entrypoint.sh     # 17 lines: `exec "$@"`. Vault-free by design.
├── pyproject.toml           # uv/hatchling/ruff/pyright/pytest config
├── uv.lock                  # CPU-pinned torch on Linux; `grep nvidia-` must stay empty
├── pipeline/                # the five sanitization stages + orchestrator + contract
├── promptguard/             # Llama Prompt Guard 2 classifier wrapper
├── searxng/                 # the forage-searxng companion image: Dockerfile
│                            # (digest-pinned base) + config/ (settings.yml, limiter.toml)
├── contract/                # the frozen wire contract: openapi.yaml (generated) and
│                            # openapi.yaml.sha256, the committed anchor every other
│                            # copy is verified against (generated, never hand-edited),
│                            # plus GOVERNANCE.md — the semver rules, the five recorded
│                            # rulings, and the consumer vendoring procedure. Copied
│                            # whole into the image and published as Release assets
├── SECURITY.md              # reporting channel, supported versions, in/out of scope;
│                            # the posture itself stays in README.md
├── scripts/                 # operator-only, run by hand: vendor_weights.py vendors the
│                            # pinned weights to the private GHCR mirror;
│                            # export_contract.py regenerates contract/. Ships in no
│                            # image — the Dockerfile COPY list names nothing here
├── tests/                   # 32 test_*.py modules (+ conftest.py, fakes.py, __init__.py); flat, one module per subject
├── docs/                    # configuration.md, weights.md, releases.md, searxng.md,
│                            # bootstrap-notes.md, bootstrap-scan.txt
└── kit_tools/               # this documentation framework + feature specs
```

---

## Key Modules

| Module | Lines | Responsibility |
|--------|------:|----------------|
| `pipeline/orchestrator.py` | 1128 | Drives the five stages end to end. Since `search-provider-abstraction` US-002 it reaches search backends through the `SearchProvider` seam rather than calling SearXNG itself: it sets the candidate budget, re-applies its own slice, bounds `unresponsive_engines`, and maps a `ProviderFailure`'s closed `detail` onto `searxng_error` / `searxng_unavailable`. `_DEFAULT_SEARXNG_URL` and `_SEARXNG_ENGINES` survive here as **assigned aliases** of `pipeline/search_providers/searxng.py`'s public constants (three test modules import the private names from here); the definitions live in the provider. Since `hardening-search-sanitization` US-001 the per-result sanitization loop keeps **two forms** of `title` and `snippet`: `_scan_forms_for_search_text` returns `(wire_form, scan_form)`, where the scan form is what Stages 2 and 3 see and the wire form is its whitespace collapse (`wire_form == " ".join(scan_form.split())`, so nothing reaches the wire unscanned). The scan form keeps line breaks, which is what lets Stage 2's line-anchored patterns fire anywhere in the field. Order: NFC → a **first** control strip on the raw provider value (the parser maps a raw NUL to U+FFFD, outside the strip's class) → a parser-input bound of `_SEARCH_PARSER_INPUT_MULTIPLIER * max_length` (4×, measured — truncation now follows extraction, so without it the parser would see the whole provider body per field) → `extract_html` on the `<div>`-wrapped text (one entity level) → `html.unescape` (the second level) → a **second** control strip for what those decodes produced → `normalize_text` → truncate once at the field's cap. `_sanitize_search_text` survives for the URL call site only. Since `hardening-search-sanitization`
US-004, `SearchResult.engine` is also routed through `_normalize_search_text`
(`_MAX_SEARCH_ENGINE_LENGTH = 64`) rather than passed through unexamined; it is not routed
through the structural scan or PromptGuard, unlike `title`, `url` and `snippet`. The busiest
file in the repo. |
| `retrieval_app.py` | 1824 | FastAPI app + the five endpoints, startup wiring, `/health` body assembly, the legacy-capability break-glass warning. Since `forage-contract` it also carries the documentation surface: the five per-shape error **mirrors** (US-001) and the six `/metrics` response models (US-005), all `extra="forbid"`, none of which any emission site routes through — the emission sites are unchanged and parity tests hold the models to them. The `FastAPI(...)` call serves `title="Forage"`, `version=CONTRACT_VERSION` and the no-auth/private-network posture, so `/openapi.json` cannot disagree with `/health` about which contract this process implements. |
| `cache.py` | 860 | Valkey content cache. **Never logs the connection URL** — it may carry a password; enforced by a closed log vocabulary and a dedicated regression test. |
| `models.py` | 470 | Pydantic models for every request and response shape. |
| `pipeline/stage4_structuring.py` | 308 | Assembles the response object and the composite trust score. |
| `pipeline/stage1_extraction.py` | 337 | HTML extraction → `raw_text` (for scanning) + `main_content` (for the agent). |
| `pipeline/stage5_url_audit.py` | 237 | Outbound fetch with manual redirect following and redirect-chain auditing. |
| `pipeline/pdf_subprocess.py` | 297 | PDF parsing isolated in a subprocess (pypdf is not trusted with hostile input in-process), for both routes; also `spool_dir()`, the process-private `0700` spool directory, and `extract_pdf_bytes_in_subprocess`, `/retrieve`'s bytes entry point. |
| `pipeline/stage2_structural.py` | 304 | Deterministic regex injection scan. |
| `pipeline/smart_extraction.py` | 207 | Summary mode that preserves high-signal content (stats, quotes, references). |
| `url_validator.py` | 336 | Private-IP rejection and DNS-rebinding protection, plus the service's one host canonicaliser. `canonicalize_host` / `canonical_host` (literals first: an IPv6 literal is recognised by its colons and never reaches the encode; every other host loses exactly one trailing dot, is lower-cased, is UTS-46-encoded via **`idna`** — a direct dependency, floor `>=3.7` for CVE-2024-3651 — and only then classified as numeric or named) and `private_address_class`, which reports *how* an address was reached (`private_literal` / `embedded_private`) and unwraps IPv4-mapped, 6to4, Teredo, prefix-guarded NAT64 and prefix-guarded IPv4-compatible embeddings. In `_ROOT_REVISION_SOURCES` since `hardening-search-sanitization` US-003. |
| `pipeline/extraction_limits.py` | 181 | Resource limits from `config.yaml`'s `extraction:` block. |
| `pipeline/stage1_upload.py` | 172 | Upload path for `/extract` (gated by `extract_route_enabled`). |
| `promptguard/classifier.py` | 211 | Loads and runs Llama Prompt Guard 2 (`use_safetensors=True` — the loader can never fall back to a pickle); absent weights → degraded, never silent. |
| `model_fetcher.py` | 1872 | Weight acquisition end to end. The one gate every source passes — fail-closed manifest verification, exact-set + safetensors-only allowlist, symlink-resolving hashing over `snapshots/<revision>/`, one-generation quarantine, the `ModelMetrics` counters `/metrics` exports — plus `acquire_and_load()`, the boot pipeline the lifespan runs in a worker thread: verify the cache, then **Hugging Face, then the GHCR mirror**, then one ERROR naming both. The mirror leg shells out to the image's pinned `oras`, extracts with `filter="data"` into a bounded staging area, verifies *there*, and installs by rename. Owns the revision pin, the `$HF_HOME/hub` resolution both the download and the loader are handed, and the five environment variables the acquisition path reads. `WeightAcquisition` wraps that pipeline in the service's only background loop: single-flight, 30 s→10 min jittered backoff, cancellable, and a hub-offline pin scoped to the load so a warm start makes zero network attempts. |
| `pipeline/stage1_pdf.py` | 156 | PDF branch of stage 1. |
| `pipeline/stage3_promptguard.py` | 151 | ML injection scan; skipped for trusted domains. |
| `pipeline/contract.py` | 358 | The versioned response contract (`contract_version`, currently **1.3.0**), the 18-code error vocabulary, and the `ContentKind` Literal. |
| `contract_smoke.py` | 759 | CI's published-image smoke: polls a running container's `/health`, validates it against the same `HealthResponse` model the golden test pins, and reads every wire value from `pipeline/contract.py` at run time. Two modes via `--expect-status`: `degraded` (the default, CI's weights-free image) and `healthy` (a container started with weights — the three PromptGuard-coupled checks invert, every other check is identical); the wait polls until `/health` answers 200 with the expected `status`, not merely the first 200. With `--image` (US-004) it also `cat`s `/app/contract/openapi.yaml` out of the candidate image, hashes it against the `--anchor` file (default the committed anchor) and compares its `info.version` with the version the container serves. Ships in no image. |
| `searxng_smoke.py` | 779 | CI's companion-image smoke: creates an egress-free Docker network, runs SearXNG beside a Valkey and probes it from a third container. Docker goes through an injected runner and every judgement is a pure function, so `tests/test_searxng_smoke.py` covers the failure branches without a daemon. Ships in no image. |
| `scripts/vendor_weights.py` | 1112 | Operator-only, supervised: downloads the pinned revision, generates `weights_manifest.json` with the safetensors allowlist enforced **at generation time**, builds a deterministic symlink-dereferenced tarball, self-checks it through the real verifier, `oras push`es it tagged by revision sha, and confirms the GHCR package is private. Every constant comes from `model_fetcher`; no credential ever reaches an argv. Ships in no image; `docs/weights.md` is the procedure. |
| `scripts/export_contract.py` | 327 | Operator-only: renders `app.openapi()` into `contract/openapi.yaml` in a canonical form pinned here (JSON round-trip, no anchors, sorted keys, `width=88`), writes the sha256 anchor, and writes the drift check's own committed failure case. Byte-stable across processes and hash seeds — `tests/test_contract_export.py` calls `drift_report()` directly, so the gate runs on every `uv run pytest` rather than in a lane someone has to remember. |
| `pipeline/sanitizer_revision.py` | 62 | Hashes nine source files — the eight `pipeline/` sources (`_REVISION_SOURCES`) plus repo-root `url_validator.py` (`_ROOT_REVISION_SOURCES`, resolved against `pipeline_dir.parent`) — the model identity, `idna@<version>` and the threshold into a `sanitizer_revision` string. See the gotcha below. |
| `pipeline/search_providers/searxng.py` | 262 | `SearxngProvider` — the key-less free floor behind the protocol, and the home of `DEFAULT_SEARXNG_URL`, `SEARXNG_ENGINES`, `HTTP_STATUS_DETAIL_PREFIX` and the closed `_SEARXNG_FAILURE_DETAILS` vocabulary. A behavior-preserving extraction of the `httpx` block that used to sit inline in `run_search_pipeline`, with two recorded deviations: `trust_env=False` on the client and a `reason` text that no longer carries `str(exc)` or userinfo. Not in `_REVISION_SOURCES`, for the same reason as `base.py`. |
| `pipeline/search_providers/base.py` | 146 | The `SearchProvider` protocol (`name`, `paid`, `origin`, `search()`) plus the internal `ProviderSearchResult` / `ProviderFailure` types and the closed `FailureClass` vocabulary every backend implements. First nested package under `pipeline/` (its `__init__.py` is the registry, below, not a bare marker); not in `_REVISION_SOURCES` — provider code changes what is fetched, not how it is sanitized. |
| `pipeline/search_providers/brave.py` | 504 | `BraveApiProvider` (`feature-brave-provider`) — the paid Brave LLM-Context backend, `paid = True`, returning content chunks (`content_kind="chunk"`, `engine="brave-api"`, deliberately distinct from SearXNG's own `brave` sub-engine) parsed against one owner-captured pinned sample (`tests/fixtures/brave/llm_context_sample.json`). A hardened per-call `httpx.AsyncClient` (`trust_env=False`, `follow_redirects=False`, TLS verified) against a fixed constant endpoint, a response body bounded before any `json.loads`, and `config.yaml`-tunable timeout/chunk/query caps read unconditionally in the lifespan. Also home of `brave_key_present()` — strip, non-empty; the one key-presence helper the registry and `/health` share (ruling 28) — and of the closed `_BRAVE_FAILURE_DETAILS` vocabulary every failure's `detail` token is drawn from (`http_401`/`http_403` → `auth`, `http_429` → `rate_limited`, everything else → `hard_error`). Not in `_REVISION_SOURCES`, for the same reason as the other two provider modules. |
| `pipeline/search_providers/__init__.py` | 154 | The provider **registry and chain builder**: `build_provider_chain(...)` turns the `FORAGE_SEARCH_PROVIDERS` names into ordered `SearchProvider` instances, refuses boot on an unknown name, and skips a configured `brave` that has no usable key with the WARNING `brave_skipped_missing_key` (the key-less floor); `_KNOWN_PROVIDER_NAMES` is the closed set of chain tokens (ruling 23). Not in `_REVISION_SOURCES`. |
| `pipeline/search_providers/policy.py` | 77 | `apply_request_policy(...)` (`search-policy-and-health` US-010): normalises a request's `providers` list (strip, lower-case, first eight), keeps the configured order, never adds, reorders or promotes a provider, honours `allow_paid_fallback`, and counts every ignored entry on `search.policy_unknown_provider`. Restrict-only by construction (rulings 16, 29). Not in `_REVISION_SOURCES`. |

---

## Patterns That Matter

**Stages 1, 2 and 4 run off the event loop, on both routes.** Since
`hardening-retrieve-parity` US-002, `extract_html` (a fetched page's HTML), `scan_structural`
and `structure_sanitization_result` are called through `asyncio.to_thread`, as stage 3's
inference already was — the latter two inside `sanitize_and_structure`, so `/retrieve` and
`/extract` share them, with byte-identical output. A pathological page therefore cannot stall
`/health`. `/retrieve`'s fetch and stage 1 run under a second `ExtractionAdmissionController`
(`app.state.retrieve_admission`, built by `from_retrieve_settings`), acquired after the cache
read and released in `finally` after stage 1, and the fetched body is deleted with the slot
so a request waiting on the classification permit holds only its extracted text. `/search`'s
per-result `scan_structural` over bounded fields stays on the loop.

**Both routes parse PDFs in the worker.** Since `hardening-retrieve-parity` US-003 a fetched
PDF on `/retrieve` goes through `extract_pdf_bytes_in_subprocess`, which spools the body to a
`0600` `forage-retrieve-*` file in `spool_dir()` (`<TMPDIR>/forage-spool-<uid>`, `0700`,
created on first use and verified with `lstat` on every call, never repaired) and calls the
same `extract_pdf_in_subprocess` `/extract`'s uploads use — spawned, under `/extract`'s
rlimits and `extraction.max_promptguard_chunks` — inside `asyncio.to_thread` and the
admission slot, unlinking in `finally`. The in-process `stage1_pdf.extract_pdf` is now
reached only by `run_extract_pipeline`, the bytes-in `/extract` path. The mapping around the
call is most-specific first — `PDFClassifiableTextLimitError` (→ `content_too_large` /
`promptguard_budget`), `PDFEncryptedError`, `PDFNoTextError`, `PDFExtractionError`, `OSError`
(→ `extraction_failed` with the four `contract.RETRIEVE_PDF_*` reasons) — because every PDF
failure subclasses `PDFExtractionError`. `_spool_upload` writes into the same directory, and
the lifespan checks it once so a planted directory refuses boot.
The fetched-PDF task remains owned until the bounded worker is reaped and the spool is
unlinked, even if the request is cancelled repeatedly. `asyncio.wait` does not cancel the
worker task; the pipeline retrieves its outcome and propagates pending cancellation only
after cleanup, before leaving the admission slot. Merely cancelling a `to_thread` await
would detach the still-running thread and release admission early.

**The sanitizer revision is a content hash of source files *and of the model pin*.**
`sanitizer_revision.py` resolves `_REVISION_SOURCES` relative to its own file and
`_ROOT_REVISION_SOURCES` against its parent, and hashes the nine files in that order, then
the model identity (`MODEL_ID@revision`), then `idna@<version>`, then the active threshold;
the value ships in every `/health` body and response envelope so a consumer can tell which
sanitizer version produced a result. Editing any of those nine files — or bumping `idna`,
whose UTS-46 tables decide which hosts the search audit drops and `validate_url` refuses —
changes it. That
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
docstrings only, zero wire bytes changed, contract still `1.1.0` then). The three most
recent are `search-provider-abstraction`'s: a seventh when the inline SearXNG call left
`orchestrator.py` for `SearxngProvider` (`8b1b7f78…` → `ee4450d9…`, US-002 — the provider
module is not hashed, so `orchestrator.py` alone moved), an eighth for the `providers=`
chain seam (`ee4450d9…` → `e7038672…`, US-003), and a ninth with the contract bump to
`1.2.0` (`e7038672…` → `b7871b20…`, US-004 — `contract.py` + `orchestrator.py`, the epic's
only two-file rotation, each file's contribution measured by reverting it in turn). Four
more followed from `search-fallback` and `search-policy-and-health`: a tenth for free-first
chain traversal (`b7871b20…` → `55e2af1b…`, US-001 — `orchestrator.py` alone), an eleventh
for fallback telemetry and per-result provenance (`55e2af1b…` → `5249def6…`, US-003 —
`orchestrator.py` alone; `models.py`'s matching wire fields are not hashed), a twelfth for
failure-class discrimination (`5249def6…` → `f0b93318…`, US-002 — `orchestrator.py` alone,
a lone-`searxng` chain carved out and unaffected), a thirteenth for the per-request
policy literal (`f0b93318…` → `dc3ff92a…`, `search-policy-and-health` US-010 —
`contract.py` alone; `retrieval_app.py`, where the new 422 is raised, is not hashed), and a
fourteenth when `contract.py`'s `CONTRACT_VERSION` docstring gained the completed 1.2.0
change record (`dc3ff92a…` → `41ac98ca…`, `search-policy-and-health` US-003 —
`contract.py` alone; `retrieval_app.py` and `models.py`, where the new
`/search`/`/retrieve` boundary text lives, are not hashed), and a fifteenth — **the first
rotation that changes sanitization behaviour** — when `orchestrator.py` gained
`_scan_forms_for_search_text` (`41ac98ca…` → `b0ca8d9a…`,
`hardening-search-sanitization` US-001 — `orchestrator.py` alone, measured from a clean
tree), and a sixteenth — **the second** — when `_canonicalize_search_url` became the
`_SEARCH_URL_RULES` registry (`b0ca8d9a…` → `42485686…`,
`hardening-search-sanitization` US-002 — `orchestrator.py` alone, measured from a clean
tree), and a seventeenth with the contract bump to `1.3.0` (`42485686…` → `05dbbb5c…`,
`hardening-search-sanitization` US-004 — `contract.py` + `orchestrator.py`, the epic's
second two-file rotation, each file's contribution measured by reverting it in turn:
`contract.py` gained `OMIT_BLOCKED_URL` and the version bump, `orchestrator.py` gained
`_MAX_SEARCH_ENGINE_LENGTH = 64` and routed `SearchResult.engine` through
`_normalize_search_text`. Bounds and normalizes a field rather than scanning one, so this
does **not** join the fifteenth and sixteenth as a third behaviour-changing rotation), and
an eighteenth — **the third behaviour-changing rotation, and the first that adds *inputs***
— with the search-time URL audit (`05dbbb5c…` → `840c78fa…`,
`hardening-search-sanitization` US-003 — `orchestrator.py` + `contract.py`, each measured by
reverting it in turn, **plus** two new inputs each measured absent/present: repo-root
`url_validator.py` as `_ROOT_REVISION_SOURCES` and `idna@<version>`, with a control that
reverts both files and removes both inputs landing exactly on `05dbbb5c…`), and a nineteenth
— **the fourth behaviour-changing rotation** — when the `/search` scan loop began scanning
**both** forms of each text field (`840c78fa…` → `6f0fa2de…`, the
`hardening-search-sanitization` validation fix; `orchestrator.py` alone, with the revert
reproducing `840c78fa…` exactly). A twentieth rotation — **not** behaviour-changing —
came with `run_retrieve_pipeline`'s five new keyword-only dependencies and the pre-checked
chunk budget (`6f0fa2de…` → `e55b5f06…`, `hardening-retrieve-parity` US-001 —
`orchestrator.py` + `contract.py`, each reverted in turn, both-reverted control landing on
`6f0fa2de…`; the shipped default `retrieve.max_promptguard_chunks: 0` runs no pre-check, and
the new `pipeline/retrieve_limits.py` and `pipeline/config_bounds.py` are not hashed).
A twenty-first — also **not** behaviour-changing — came with the classification semaphore on
`/retrieve` and `/search` (`e55b5f06…` → `d0433876…`, `hardening-retrieve-parity` US-006 —
the first rotation of this epic with **three** hashed files: `orchestrator.py` for
`_bounded_permit` and the two routes' acquisitions, `stage3_promptguard.py` for the pure
`unavailable_result` seam, `contract.py` for the `1.3.0` continuation line; each reverted in
turn, all-reverted control landing on `e55b5f06…`). A twenty-second — also **not**
behaviour-changing — came with stages 1, 2 and 4 moving off the event loop and the `/retrieve`
admission gate (`d0433876…` → `f654be77…`, `hardening-retrieve-parity` US-002 —
`orchestrator.py` + `contract.py`, each reverted in turn, both-reverted control landing on
`d0433876…`). A twenty-third — also **not** a change to how text is sanitized, though it
refuses fetched PDFs over the worker's bounds at the shipped defaults — came with fetched
PDFs moving into the rlimited worker (`f654be77…` → `464b6ad5…`, `hardening-retrieve-parity`
US-003 — `orchestrator.py` + `contract.py`, each reverted in turn, both-reverted control
landing on `f654be77…`; includes the cancellation-ownership correction to the unaccepted
`6fd320da…` candidate). A twenty-fourth — **not** sanitization-behaviour-changing —
came with corrupt cache entries becoming misses (`464b6ad5…` → `664ee603…`,
`hardening-retrieve-parity` US-004): only `contract.py`'s 1.3.0 continuation line
moved a hashed input, and its read-only revert reproduces `464b6ad5…` exactly.
The parse guard in `cache.py` and metrics mirror/emission in `retrieval_app.py`
are not hashed. A twenty-fifth — **not** sanitization-behaviour-changing at shipped
defaults — announces the effective-policy fields (`664ee603…` → `d98f7dbe…`,
`hardening-retrieve-parity` US-005): `contract.py` alone, whose read-only whole-file
revert reproduces `664ee603…` under both default and shipped configuration.
`retrieval_app.py`'s policy helper and `models.py`'s fields are not hashed.
A twenty-sixth (`d98f7dbe…` → `5a470872…`) came from the preceding retrieve-parity
validation commit `fe211e3`: `orchestrator.py` and `stage3_promptguard.py` changed
cancellation ownership, the absolute fetch deadline and timeout accounting, not the
text sanitization algorithm. Read-only substitution of its parent reproduces the
before value. The twenty-seventh (`5a470872…` → `328d386c…`) is the **fifth
sanitization-behaviour change**: hostname policy now has unconditional multi-label
denylist suffixes and leading-dot opt-in allowlist suffixes, which can skip PromptGuard
on trusted subdomains. `orchestrator.py`, `contract.py` and `url_validator.py` all
move; the latter was already in `_ROOT_REVISION_SOURCES`. Each reversal is measured
in `docs/bootstrap-notes.md`, with the all-reverted control reproducing `5a470872…`
under default and shipped config.
Nothing downstream may assume Poppy↔Forage revision parity.

**Domain lists cross boundaries as canonical strings.** `url_validator.py` owns the
normaliser, byte measure and matcher; there is still one IDNA implementation in
`canonicalize_host`. A leading dot remains in allowlist strings (and fingerprints)
but is removed from denylist strings. IP literals are equality-only, single-label
denylists are exact-only, and single-label allowlists are invalid. Private-name
rejection remains its own unconditional check before caller lists.
The lifespan publishes a normalised config copy and warns once per invalid list;
the raw loaded config still feeds revision derivation. Until US-007 owns caller
normalisation, each comparison normalises its inputs once per call without a budget.

**Operator policy is resolved in the handler, once.** `_apply_promptguard_policy`
replaces the typed request with `model_copy` after asserting update keys against
`model_fields`: both routes apply `request.promptguard_fail_closed or floor`,
and `/retrieve` alone applies `min(request.promptguard_threshold, ceiling)`.
The pipeline and `cache_policy_fingerprint` therefore read the same effective values;
no parallel pipeline argument can bypass the cache key. Each handler stamps its
response after the pipeline, including hits with missing or stale stored policy
fields. The fields report policy, not scanning; trusted-tier skip and VERIFIED
fail-open remain exemptions, `/search` keeps fixed `0.85`, and `/extract` is untouched.

**Cache parse failure is a miss, not an authenticity check.** `ContentCache._parse_entry`
catches `ValueError` from `RetrievedContent.model_validate_json`, increments
`corrupt_entries`, logs one WARNING with `cache_entry_corrupt` and the key digest,
and attempts deletion before returning `None`. Storage owns operation failures as
before; even a failed deletion leaves this request on the miss path. Values that
parse still pass through the existing freshness checks and are not authenticated.

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
add a new golden under `tests/golden/` (older ones are retained, never edited), regenerate
`contract/` with `uv run python -m scripts.export_contract`, and note it for the consuming
repo. Since `feature-forage-contract` US-003 the rules are written down rather than
remembered: **`contract/GOVERNANCE.md`** classifies any change (MAJOR / MINOR / PATCH / no
bump), carries the five rulings this epic recorded — the documentation pass taking no bump,
the unreachable `/extract` 413 and what fixing it would cost, enum additions as MINOR with
an announcement obligation, fixture retention, and the private-IP echo caveat — and states
the image↔contract mapping that the `publish` job now emits into the Release body and
asserts back out of it.

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
