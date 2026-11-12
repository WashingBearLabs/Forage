<!-- Template Version: 2.0.0 -->
# SYNOPSIS.md

> Last updated: 2026-11-12
> Updated by: Claude (close-session: extract-route-env PR #52, config-overlay epic PR #53)

---

## What Is This?

Forage is a headless HTTP **extraction and telemetry service for safe web access by LLM
agents**. It fetches, searches, extracts, and sanitizes web content, emits
prompt-injection *signals* and a honest health report, and returns a versioned response
contract. It is **not a trust boundary** — the calling agent's own security layer owns
every trust decision; Forage's output is evidence, not an all-clear.

Forage ships **no authentication** on any of its five endpoints and must run on a private
network only. See `README.md` for the full framing and posture note.

Forage was split out of the [Poppy](https://github.com/WashingBearLabs) monorepo on
2026-09-07 (`services/retrieval/` + `config/searxng/` + `tests/retrieval/`, history
preserved). See `docs/bootstrap-notes.md` for the pin record.

---

## Current State

| Aspect | Status |
|--------|--------|
| Maturity | `v1.4.0` / contract `1.5.0` (MINOR: CPU and GPU from one image, plus the never-published v1.3.0 work: 86M default, bounded workers, fold BLOCK, budget default 64) is **published (2026-10-11) and verified** (`gpu-validation` US-005; candidate and published-image smokes on CPU and GPU). Previously published: `v1.2.2` / contract `1.3.0` (PATCH: 86M selectable via `FORAGE_MODEL_ID`, 22M default; contract unchanged from v1.2.1) published 2026-10-04 and verified (`corpus-86m-enablement` US-004). `v1.2.1` / contract `1.3.0` published and verified 2026-09-23; hardening epic complete. Defective v1.2.0 is withdrawn. The schema golden is frozen |
| Repo visibility | **Public** since the US-008 flip (2026-09-10), repository and both packages; `main` is PR-only, with six required status checks (audit-measured 2026-09-11 — this row said "Private" for a month after the flip) |
| Tests | 5879 collected (2026-11-12, after PR #52 `extract-route-env` 5766 → 5800 and PR #53 `config-overlay` 5800 → 5879; CI green on both). Hermetic, no xfails; ruff, format and pyright strict all zero. CI pins `.python-version` 3.12, and the stage-2 linearity bound is machine-relative (GOTCHAS) |
| Lint | `uv run ruff check .` and `ruff format --check .` both clean — **enforced in CI** |
| Types | `uv run pyright` (strict) is **clean — 0 errors**, no baseline; **enforced in CI** |
| CI | `.github/workflows/ci.yml` — ten jobs in two lanes: `lint`, `typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke`, `publish` for the service image, and `searxng-build`/`-smoke`/`-publish` for the companion |
| Published image | `ghcr.io/washingbearlabs/forage` — `1.4.0` published 2026-10-11 and verified, index `sha256:d305bbbe223e13479f8a119540714d2277fe3e13c38dcf426729cd8c10019036` (amd64 CUDA torch, arm64 CPU torch); `latest` / `1.4` / `1.4.0` equality verified anonymously. Tag commit `49de6cc812052e925c6897b1812f55291dab0473`; [publish run](https://github.com/WashingBearLabs/Forage/actions/runs/38104861366); [record](specs/archive/feature-gpu-validation.md). `1.3.0` was never published (folded into `1.4.0`). Previous: `1.2.2` published 2026-10-04 and verified, index `sha256:5cb60943b99da45829613cde1f8286bdb4b72866210aa2146ca0cc5233569365`; `latest` / `1.2` / `1.2.2` equality verified anonymously. Tag commit `c213bbfbe31c42dcf3a84dcaba14145e89805182`; [publish run](https://github.com/WashingBearLabs/Forage/actions/runs/37163854549); [record](specs/feature-corpus-86m-enablement.md). Previous: `1.2.1`, index `sha256:a29329af38ee563dcc890c9b68749e4d7bc32e20c422640b2f5ffecaa8c89e7b`, tag commit `e8cf83c51e8786abf30d79ae0a3d6608c5f8df2c`; [handoff](specs/archive/feature-hardening-release.md). `docs/releases.md` records v1.2.0's withdrawal |
| Deployment | Poppy pinned the published `v1.4.0` (contract 1.5.0) on 2026-10-13, as reported by the owner (SESSION_LOG addendum). The coexistence wording in `CLAUDE.md` has not been retired yet; that is the owner's call. `main` is ahead of the published image (unreleased contract 1.6.0 plus the config overlay) |
| Planned next | **Unreleased on `main`:** contract **1.6.0** (PR #52: `FORAGE_EXTRACT_ROUTE_ENABLED`, `/extract` 404 `route_disabled`, `/health` `capabilities.extract_route`, two-worker memory rule) and `FORAGE_CONFIG_OVERLAY` (`epic-forage-config-overlay`, PR #53, no contract change). Next: an owner decision on cutting `v1.5.0` so Poppy can drop its vendored config (consumer notes in `docs/bootstrap-notes.md`); the owner's lab security testing still shapes the next epic. Info-level follow-ups (2026-11-11-001..009) are in the local `AUDIT_FINDINGS.md` |

**Coexistence rule:** until Poppy pins a published Forage image, any fix to the extracted
paths on either side must be replayed onto the other, and `docs/bootstrap-notes.md`'s pin
record updated.

---

## Tech Stack Summary

| Layer | Choice |
|-------|--------|
| Language | Python 3.12+ |
| Web framework | FastAPI + uvicorn (single process, port 8020) |
| Package/env manager | **uv** (`uv.lock` committed; torch pinned to the CPU index on Linux) |
| Build backend | hatchling, explicit wheel target (flat module layout — no `forage/` package) |
| Extraction | trafilatura, BeautifulSoup4 + lxml, pypdf |
| ML | transformers + torch (CPU) running Llama Prompt Guard 2 — 86M by default since v1.4.0 (22M opt-out via `FORAGE_MODEL_ID`) |
| Cache | Valkey/Redis via `redis` (optional-in-memory fallback lands in `feature-forage-cache-fallback`) |
| Search | SearXNG (companion service; config in `searxng/config/`) |
| Host canonicalisation | **`idna`** (`>=3.7`, direct since `hardening-search-sanitization` US-003; `url_validator.canonicalize_host` is the one UTS-46 call site, and `idna@<version>` is a `sanitizer_revision` input) |
| Tests | pytest + pytest-asyncio (`asyncio_mode = auto`) + pytest-socket |
| Lint / types | ruff (E,F,I,N,UP,B,SIM,RUF; line-length 88) + pyright **strict** |
| Container | Dockerfile at repo root; entrypoint is a 17-line `exec "$@"` — **no vault client** |
| License | Apache-2.0 (code) / Llama 4 Community License (weights, never shipped) |

---

## How to Run Locally

```bash
# Environment (creates .venv, installs runtime + dev extras)
uv sync --extra dev --extra cpu

# Tests
uv run pytest

# Lint
uv run ruff check .

# Types (backlog — see Current State)
uv run pyright

# Build + run the container (private network only)
docker build -t forage .
docker run --rm -p 127.0.0.1:8020:8020 \
  -e VALKEY_URL=redis://valkey:6379/4 \
  -e SEARXNG_URL=http://searxng:8080 \
  forage

curl -s localhost:8020/health | jq   # read the BODY, not the status code
```

`docker build` needs no Hugging Face token — the token-less branch is a supported guard
path and yields a `promptguard_unavailable` degraded runtime.

---

## Where Things Live

| Path | Contents |
|------|----------|
| repo root | `retrieval_app.py`, `models.py`, `cache.py`, `url_validator.py`, `model_fetcher.py`, `weights_manifest.json`, `config.yaml`, `Dockerfile`, `SECURITY.md` |
| `pipeline/` | The five sanitization stages, the orchestrator, and the response contract |
| `promptguard/` | The Llama Prompt Guard 2 classifier wrapper |
| `searxng/config/` | SearXNG `settings.yml` + `limiter.toml` |
| `contract/` | The frozen wire contract: generated `openapi.yaml` + its committed `.sha256` anchor, and `GOVERNANCE.md` — the semver rules, the fourteen recorded rulings and the consumer vendoring procedure. Regenerate the two generated files with `uv run python -m scripts.export_contract`; never hand-edit. The whole directory ships in the image at `/app/contract/` and the two generated files ship as `v*` Release assets (US-004) |
| `scripts/` | Operator-only, run by hand from a checkout; in no image |
| `tests/` | 38 `test_*.py` modules, one per subject, plus `conftest.py`, `fakes.py`, `__init__.py`, `golden/` and `fixtures/` |
| `docs/` | `configuration.md` (full env/config reference), `releases.md`, `weights.md`, `searxng.md`, `bootstrap-notes.md`, `bootstrap-scan.txt` (audit 2026-09-11: three were missing from this row) |
| `.github/` | `workflows/ci.yml` and `pull_request_template.md` (the bump checklist + standing invariants) |
| `kit_tools/` | This documentation framework + the feature specs |

See `kit_tools/arch/CODE_ARCH.md` for the module map.
