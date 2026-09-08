# Forage

**Forage is an extraction and telemetry service for safe web access by LLM agents.**
It fetches, searches, extracts, and sanitizes web content, and it emits prompt-injection
*signals* plus honest health.

**Forage is not a trust boundary.** It does not decide what your agent is allowed to
read, remember, or act on. It reports what it found and how confident it is; your
agent's own security layer owns every trust decision. Treat Forage's output as
*evidence*, never as an all-clear.

> ### Deployment posture — read before you run it
>
> **Forage ships no authentication.** All five HTTP endpoints (`/health`, `/metrics`,
> `/search`, `/retrieve`, `/extract`) are unauthenticated by design. Run Forage **only on
> a private network** — a Docker bridge network, a host-local port, or a VPN. Never
> publish port 8020 to the internet or to any untrusted network segment. Anyone who can
> reach the port can make Forage fetch arbitrary URLs on your behalf.
>
> Forage is a good server-side-request-forgery target by nature: making outbound requests
> is its job. It defends itself (RFC1918 rejection, DNS-rebinding checks, redirect
> auditing), but network placement is your first control, not its.

## Why "Forage"?

Raccoons — the WashingBearLabs mascot, and literally *Waschbär*, "wash bear" — go out,
find things, and wash them before eating. That is exactly the job: go out to the web,
bring back only what is safe to eat, and be honest about what could not be washed. The
name deliberately does not overclaim security.

## What it does — the pipeline

Every fetched or searched document runs the same five stages:

| Stage | Does |
|-------|------|
| **1 — Extraction** | HTML/PDF/upload → two outputs: full flattened `raw_text` for scanning, and boilerplate-free `main_content` for the agent. Invisible-Unicode and control characters normalized out. |
| **2 — Structural scan** | Fast, deterministic regex pass for prompt-injection patterns. Runs before any ML inference so obvious attacks never reach the model. |
| **3 — PromptGuard** | Llama Prompt Guard 2 classifies `raw_text` for injection. Skipped for trusted domains; when the model is absent the service reports itself **degraded** rather than pretending the content was scanned. |
| **4 — Structuring** | Assembles the response object and computes a composite trust score from the stage outputs plus source metadata. Summary mode preserves high-signal content (statistics, quotes, references). |
| **5 — URL audit + fetch** | Outbound fetching with private-IP rejection, DNS-rebinding protection via manual redirect following, domain blocklists, User-Agent rotation, and redirect-chain tracking. |

Stage 5 runs first chronologically for `/retrieve` and `/search` (it *is* the fetch);
the numbering follows the sanitization order the contract reports.

### HTTP surface

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Always 200. Body carries `status` (`healthy`/`degraded`), `degraded_reasons`, `promptguard_loaded`, `cache_connected`, `sanitizer_revision`, `contract_version`. **Check the body, not the status code.** |
| `GET /metrics` | Extraction, search, retrieve, and cache counters. |
| `POST /search` | Search via SearXNG, with every result run through the pipeline. |
| `POST /retrieve` | Fetch and sanitize a single URL. |
| `POST /extract` | Extract from an uploaded document (gated behind `extract_route_enabled` in `config.yaml`). |

The response contract is versioned (`contract_version`, currently **1.0.0**). Consumers
should refuse to activate on a mismatch rather than guess.

## Quickstart

```bash
# Build
docker build -t forage .

# Run (private network only — see the posture note above)
docker run --rm -p 127.0.0.1:8020:8020 \
  -e VALKEY_URL=redis://valkey:6379/4 \
  -e SEARXNG_URL=http://searxng:8080 \
  forage

# Health — read the body, not just the code
curl -s localhost:8020/health | jq
```

Forage wants two companions: a Valkey/Redis instance for the content cache and a
SearXNG instance for `/search`. Both are optional in the sense that Forage starts
without them and reports itself `degraded`; neither is optional for full function. A
worked `docker-compose.yml` bringing up all three lands with the cache-fallback work
(`examples/` — not yet in this repo).

Local development:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## Configuration

Forage is 12-factor: everything comes from environment variables at container start
plus the mounted `config.yaml`. It has **no** OpenBao/Vault client, no secret-bearing
runtime API, and no config database.

- `VALKEY_URL` (default `redis://valkey:6379/4`) — content-cache connection string.
  Supply it through an env file or your secret store, not an inline `-e` flag (shell
  history).
- `SEARXNG_URL` (default `http://searxng:8080`) — SearXNG base URL for `/search`.
- `config.yaml` — user-agent pool, news-domain trust list, seed blocklist, PromptGuard
  threshold, the `extract_route_enabled` gate, and the `extraction:` resource limits.
- SearXNG's own settings are baked into the companion image built from `searxng/`
  (`ghcr.io/washingbearlabs/forage-searxng`) — see [`docs/searxng.md`](docs/searxng.md).
  `SEARXNG_SECRET` is required when you run it.

The complete reference — every variable, every `config.yaml` key, defaults, the
break-glass caveat, and credential-handling guidance — lives in
[`docs/configuration.md`](docs/configuration.md).

## PromptGuard weights and the Hugging Face gated repo

Stage 3 runs `meta-llama/Llama-Prompt-Guard-2-22M`, which lives in a **gated** Hugging
Face repository. You must accept Meta's license on Hugging Face and supply your own
token to obtain the weights; Forage cannot and does not distribute them for you.

**Without the weights, Forage runs degraded**: `/health` reports
`promptguard_unavailable`, and the ML injection scan does not happen. That is a loud,
machine-readable state on purpose — an earlier silent version of this failure went
unnoticed for nine days in production. Do not treat a degraded Forage as a scanned one.

**The image ships no weights, and takes no build argument.** `docker build .` needs
nothing but the source: no credential enters the build, because a Docker build argument is
recoverable from the finished image's layer history and would therefore be published along
with the image. A runtime fetch into the `HF_HOME` volume replaces it; until that lands,
a freshly built image starts degraded, which is the honest answer rather than a broken
one.

## Licensing

Forage's licensing splits in two, and the split matters:

| What | License |
|------|---------|
| **This repository's source code** | Apache License 2.0 — see [`LICENSE`](LICENSE) |
| **Llama Prompt Guard 2 model weights** (downloaded by you at run time, never shipped here) | Llama 4 Community License Agreement + Llama 4 Acceptable Use Policy |

Built with Llama.

See [`NOTICE`](NOTICE) for the full attribution and the obligations that travel with the
weights — in particular, if you redistribute an image or artifact that *contains* the
weights, the Llama terms come with it.

## Status

Forage is **pre-1.0 and freshly extracted**. The code and its full history were split
out of the [Poppy](https://github.com/WashingBearLabs) monorepo (`services/retrieval/`,
`config/searxng/`, `tests/retrieval/`) on 2026-09-07; see
[`docs/bootstrap-notes.md`](docs/bootstrap-notes.md) for the pin record and the split
command.

**Coexistence rule while the extraction finishes:** Poppy's in-tree copy remains the
**deployed source of truth** until Poppy pins a Forage image. Until that pin lands, the
two copies coexist, and any fix to the three extracted paths on either side must be
replayed onto the other — with the Poppy source commit recorded in the pin record.

**CI and published images are live.** Every push and PR runs lint, strict types, the full
suite, an image build, a baked-secret scan and a contract smoke against the built
container; a `v*` tag or a push to `main` publishes a multi-arch image to
`ghcr.io/washingbearlabs/forage` behind all six gates. The tag scheme, the pre-release
policy and what a green publish does and does not prove are in
[`docs/releases.md`](docs/releases.md).

A second, independently versioned lane publishes the **companion SearXNG image**,
`ghcr.io/washingbearlabs/forage-searxng` — upstream's SearXNG at a digest-pinned base
plus a baked config with honest public defaults (no wildcard pass list, no baked secret,
no client-header trust) — behind its own hermetic cross-container smoke.

> ⚠️ **`forage-searxng` is not built for direct internet exposure.** It ships with rate
> limiting **off** — measured on the pinned digest, a working SearXNG limiter refuses the
> JSON API this image exists to serve (HTTP 429 on the first request from a header-bare
> client, and a hard 4-requests/hour cap on API formats via an upstream module constant no
> config can raise). Run it on an internal network behind your own service, the way the
> official Redis and Postgres images assume. If a deployment must expose it, the opt-in
> (`SEARXNG_LIMITER=true` + `SEARXNG_VALKEY_URL` + a `pass_ip` passlist for your own
> client's network) and its measured consequences are documented in
> [`docs/searxng.md`](docs/searxng.md).

[`docs/searxng.md`](docs/searxng.md) has the runbook. **The repository and both packages
are private until the one-way public flip**, so those pulls are not anonymous yet.

Still to come: download-at-start model bootstrap,
an optional in-memory cache when no Valkey is configured, and the frozen OpenAPI
contract.
