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
| `GET /health` | Always 200. Body carries `status` (`healthy`/`degraded`), `degraded_reasons`, `promptguard_loaded`, `cache_connected`, `cache_backend`, `sanitizer_revision`, `contract_version`. **Check the body, not the status code.** |
| `GET /metrics` | Extraction, search, retrieve, and cache counters. |
| `POST /search` | Search via SearXNG, with every result run through the pipeline. |
| `POST /retrieve` | Fetch and sanitize a single URL. |
| `POST /extract` | Extract from an uploaded document (gated behind `extract_route_enabled` in `config.yaml`). |

The response contract is versioned (`contract_version`, currently **1.1.0**). Consumers
should refuse to activate on a mismatch rather than guess.

## Quickstart

Two containers, one token. [`compose/minimal.yml`](compose/minimal.yml) is the whole
deployment:

```bash
git clone https://github.com/WashingBearLabs/Forage && cd Forage/compose

# HF_TOKEN is optional (without it Forage runs degraded, honestly — see below).
# SEARXNG_SECRET is required: upstream SearXNG exits 1 without one.
{
  echo "HF_TOKEN=hf_your_token_here"
  echo "SEARXNG_SECRET=$(head -c 32 /dev/urandom | base64)"
} > .env

docker compose -f minimal.yml up -d

# Health — read the body, not just the code
curl -s localhost:8020/health | jq
```

[`compose/full.yml`](compose/full.yml) is the same thing with a Valkey under the content
cache. Both publish Forage's port to `127.0.0.1` only and publish nothing else at all —
**Forage ships no authentication**, so that binding is your first control, not Forage's
own SSRF defenses. Read the posture note above before widening it.

Or run the image directly (private network only):

```bash
docker build -t forage .
docker run --rm -p 127.0.0.1:8020:8020 forage
```

`SEARXNG_URL` defaults to `http://searxng:8080` and `VALKEY_URL` is unset by default, so a
compose network with a service literally called `searxng` needs neither.

### The two cache modes

Forage wants one companion and can use a second. **SearXNG** backs `/search`: without it
Forage starts and reports itself `degraded`. **Valkey/Redis** backs the content cache and
is genuinely optional — which mode you are in is `cache_backend` in `/health`:

| | Memory mode | Valkey mode |
|---|---|---|
| How you select it | leave `VALKEY_URL` **fully unset** | set `VALKEY_URL` |
| `/health` `cache_backend` | `"memory"` | `"valkey"` |
| `/health` when the cache is fine | `healthy` | `healthy` |
| `/health` when it is not | n/a — nothing to lose | `degraded`, `cache_unavailable` |
| Survives a container restart | **no** | yes |
| Shared between replicas | **no** — one uvicorn worker's process, per-container | yes |
| Bounded by | `cache.max_entries` / `cache.max_bytes` (256 entries / 32 MiB) | your Valkey |
| Example | [`compose/minimal.yml`](compose/minimal.yml) | [`compose/full.yml`](compose/full.yml) |

Two things are easy to get wrong. **Only a *fully unset* `VALKEY_URL` means memory
mode** — an empty string, or a `VALKEY_URL=${VALKEY_URL}` that rendered nothing, is a
Valkey you asked for and did not get, and Forage reports `degraded: cache_unavailable`
rather than silently substituting a per-process cache. And the cache serves `POST
/retrieve` only; `/search` has never been cached, in either mode.

A multi-replica or restart-sensitive deployment should set `VALKEY_URL`.
[`docs/configuration.md`](docs/configuration.md) § "Cache backend selection" has the full
five-case table.

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

- `VALKEY_URL` (**unset by default** — the content cache runs in memory) — content-cache
  connection string. Only a fully unset value selects memory mode; an empty or broken one
  is a configured Valkey that reports `cache_unavailable`. Supply it through an env file
  or your secret store, not an inline `-e` flag (shell history).
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
with the image. The weights are fetched at **run time** instead, into the `HF_HOME`
volume, from a pinned revision and verified against a committed sha256 manifest before
anything is loaded. Set `HF_TOKEN` in the container's environment to enable it; leave it
unset and Forage runs degraded, indefinitely and honestly. Either way the service starts
and serves immediately — the download runs behind a live `/health`, and
`promptguard_loaded` flips to `true` in place when it completes.

The step-by-step — Hugging Face account, Meta's access approval, the token scope to
pick, what `/health` and `/metrics` show while the download converges, and the license
obligations that travel with the weights — is
[`docs/weights.md` § "Bring your own token"](docs/weights.md#bring-your-own-token).

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
went public at the 2026-09-10 US-008 flip**; anonymous pulls verified at the gate.

The optional in-memory cache shipped with contract `1.1.0`; still to come is the frozen
OpenAPI contract.
