<!-- Template Version: 2.0.0 -->
# SYNOPSIS.md

> Last updated: 2026-09-07
> Updated by: Claude (forage-model-bootstrap US-002)

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
| Maturity | Pre-1.0, freshly extracted (2026-09-07) |
| Repo visibility | **Private** — flips public in `feature-forage-ci-and-image` after its gates pass |
| Tests | 1151 collected, all green (`uv run pytest`), hermetic via `pytest-socket` — **enforced in CI** since US-002, with a committed hermeticity canary |
| Lint | `uv run ruff check .` and `ruff format --check .` both clean — **enforced in CI** |
| Types | `uv run pyright` (strict) is **clean — 0 errors**, no baseline; **enforced in CI** |
| CI | `.github/workflows/ci.yml` — `lint`, `typecheck` and `test` jobs live; build/publish land across the rest of `feature-forage-ci-and-image` |
| Published image | **None yet** — same spec |
| Deployment | Poppy's in-tree copy is still the deployed source of truth (coexistence rule) |

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
| ML | transformers + torch (CPU) running Llama Prompt Guard 2 22M |
| Cache | Valkey/Redis via `redis` (optional-in-memory fallback lands in `feature-forage-cache-fallback`) |
| Search | SearXNG (companion service; config in `searxng/config/`) |
| Tests | pytest + pytest-asyncio (`asyncio_mode = auto`) + pytest-socket |
| Lint / types | ruff (E,F,I,N,UP,B,SIM,RUF; line-length 88) + pyright **strict** |
| Container | Dockerfile at repo root; entrypoint is a 17-line `exec "$@"` — **no vault client** |
| License | Apache-2.0 (code) / Llama 4 Community License (weights, never shipped) |

---

## How to Run Locally

```bash
# Environment (creates .venv, installs runtime + dev extras)
uv sync --extra dev

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
| repo root | `retrieval_app.py`, `models.py`, `cache.py`, `url_validator.py`, `model_fetcher.py`, `weights_manifest.json`, `config.yaml`, `Dockerfile` |
| `pipeline/` | The five sanitization stages, the orchestrator, and the response contract |
| `promptguard/` | The Llama Prompt Guard 2 classifier wrapper |
| `searxng/config/` | SearXNG `settings.yml` + `limiter.toml` |
| `tests/` | 26 files, one module per subject, plus `fakes.py`, `golden/` and `fixtures/` |
| `docs/` | `configuration.md` (full env/config reference), `bootstrap-notes.md`, `bootstrap-scan.txt` |
| `kit_tools/` | This documentation framework + the feature specs |

See `kit_tools/arch/CODE_ARCH.md` for the module map.
