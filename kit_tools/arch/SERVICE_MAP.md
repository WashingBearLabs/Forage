<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: architecture, dependencies
  required_sections:
    - "Service Topology"
  skip_if: never
-->
# SERVICE_MAP.md

> **TEMPLATE_INTENT:** Document dependencies and integrations. Shows what talks to what and failure impacts.

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-resource-envelope US-003)

---

## System Overview

Forage is one Python 3.12 FastAPI process in one container, with exactly four outbound
dependencies and one consumer. There is no database, no message queue, no cloud provider,
and no authentication on any route: the `127.0.0.1:8020` port binding in `compose/*.yml`
is the only access control. Two of the four dependencies (Valkey, the PromptGuard weights)
are optional and their absence is *honest degradation*; the other two (SearXNG, the target
web site) are needed per request and fail per request. Module-level detail lives in
`kit_tools/arch/CODE_ARCH.md`; this document only covers the edges.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Docker host — private network only; no auth exists on any Forage route     │
│                                                                            │
│   Poppy (consumer; compares contract_version, never sanitizer_revision)    │
│      │  HTTP to the host loopback                                          │
│      ▼                                                                     │
│   127.0.0.1:8020 ──▶ ┌─────────────────────────────────────┐               │
│   (compose port map) │ forage — uvicorn 0.0.0.0:8020       │               │
│                      │ ghcr.io/washingbearlabs/forage      │               │
│                      │ /health /metrics /retrieve /search  │               │
│                      │ /extract (404 unless enabled)       │               │
│                      └────┬──────────┬───────────┬─────────┘               │
│   compose network         │          │           │  egress to the internet │
│   (no published ports)    │          │           │                         │
│          ┌────────────────┘          │           └───────────┬──────────┐  │
│          ▼                           ▼                       ▼          ▼  │
│  ┌────────────────┐  ┌────────────────────┐  ┌──────────────────┐ ┌──────┐ │
│  │ searxng :8080  │  │ valkey :6379 DB 4  │  │ huggingface.co   │ │ open │ │
│  │ /search only   │  │ /retrieve cache    │  │ (HF_TOKEN), then │ │ web  │ │
│  │ forage-searxng │  │ full.yml only;     │  │ ghcr.io weights  │ │ GET, │ │
│  │ image          │  │ memory when unset  │  │ mirror via oras  │ │ st.5 │ │
│  └────────────────┘  └────────────────────┘  └──────────────────┘ └──────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

Which edges are health-probed: Valkey (live `ping_if_due` behind `/health.cache_connected`)
and the weights (`/health.promptguard_loaded`). SearXNG and the open web are **never**
probed; their failures surface only as per-request 422s.

---

## Internal Services

Forage is the only internal service. Its pipeline stages (`pipeline/stage1_*` through
`stage5_url_audit.py`) are in-process modules, not services; see
`kit_tools/arch/CODE_ARCH.md` for the module map.

### Forage

| Attribute | Value |
|-----------|-------|
| **Purpose** | Extraction and telemetry service for safe web access by LLM agents: fetch a URL (`/retrieve`), search the web (`/search`), or classify an uploaded document (`/extract`), running every result through structural and ML prompt-injection scans and reporting trust signals rather than deciding for the caller. |
| **Repository** | This repo — `https://github.com/WashingBearLabs/Forage` (public since 2026-09-10). Extracted from the Poppy monorepo on 2026-09-07; never imports `poppy` (`CLAUDE.md` invariant 1). |
| **Image** | `ghcr.io/washingbearlabs/forage` (`linux/amd64`, `linux/arm64`; arm64 is built but never executed in CI). Base `python:3.12-slim`, digest-pinned in `Dockerfile`; no build `ARG` at all (`CLAUDE.md` invariant 2). |
| **Runtime** | Python 3.12, FastAPI + uvicorn, one worker (`CMD` has no `--workers`); uv-managed lockfile; CPU-only torch/transformers loaded lazily by `promptguard/classifier.py`. |
| **Port** | `8020` in-container (`EXPOSE 8020`, uvicorn `--host 0.0.0.0`); published as `127.0.0.1:8020` by both compose fragments. |
| **Health Check** | `GET /health` — **always HTTP 200; the truth is in the body** (`status`, `degraded_reasons`, `promptguard_loaded`, `cache_connected`, `cache_backend`, `capabilities`, `search_providers`, `sanitizer_revision`, `contract_version`). No image-level `HEALTHCHECK`; both compose fragments declare `curl -fsS -o /dev/null` liveness (30 s interval, 5 s timeout, three retries, 30 s start period). It must not gate traffic, `depends_on: service_healthy` or activation. |
| **Contract** | `contract_version` **1.3.0** (`pipeline/contract.py`), frozen as `contract/openapi.yaml` with a committed `openapi.yaml.sha256` anchor. Image tag and contract version are independent semvers — `v1.1.0` serves `1.2.0`; no published image serves `1.3.0` yet. |
| **Auth** | None on any route, including `/docs`, `/redoc`, `/openapi.json`. Network placement is the control (`docs/configuration.md`, `SECURITY.md`). |

**Depends on:**
- SearXNG (`/search` only; required per request)
- Valkey / Redis (optional content cache for `/retrieve`; bounded in-memory backend when `VALKEY_URL` is fully unset)
- Hugging Face Hub, then the private GHCR weights mirror (optional at runtime; absence is degradation, and stage 3 fails closed)
- The open web (stage 5 fetch for `/retrieve`; the target of every request)
- `/app/config.yaml` (mounted; bad `cache:` or `extraction:` values abort boot)

**Depended on by:**
- Poppy — the only known consumer (see [Consumers](#consumers))

**Failure impact:**
If the Forage container is down, the consumer loses `/retrieve`, `/search` and `/extract`
outright. Nothing durable is lost: the in-memory cache and the in-process `/metrics`
counters reset on every restart by design, Valkey data survives in `forage-valkey-data`
(`--save 60 1`), and verified weights survive in the `forage-model-cache` volume so the
next start is a warm start (zero network, about 9 s on the reference envelope (1 vCPU / 1 GB),
configurable via `FORAGE_CPUS` / `FORAGE_MEM_LIMIT` — see `docs/configuration.md` § Sizing the container).

---

## External Integrations

### SearXNG

| Attribute | Value |
|-----------|-------|
| **Purpose** | The free search backend for `POST /search`, tried at its configured position in the chain. Terminal for `/search` only when the configured chain is exactly `[searxng]` (`search-fallback` epic) — any other chain advances to the next provider on failure instead. Every result's title, URL and snippet is run through pipeline stages 1-3 before it is returned. |
| **Client / protocol** | `httpx.AsyncClient(timeout=<search_searxng_timeout_seconds>, trust_env=False, follow_redirects=False)` streams GET `{SEARXNG_URL}/search` with `q`, `format=json`, `pageno=1`, `engines=duckduckgo,brave,startpage,mojeek` (`SEARXNG_ENGINES` in `pipeline/search_providers/searxng.py`; the orchestrator keeps an assigned alias). `Accept-Encoding: identity` is requested; gzip/deflate replies are self-decoded with 1 MiB decoded and 4 MiB raw bounds before JSON parsing. At most `min(num_results*2, 20)` candidates are scanned; `unresponsive_engines` is capped at sixteen entries of 64 clean characters orchestrator-side. |
| **Configuration** | `SEARXNG_URL` — default `http://searxng:8080`, read **once at import** in `retrieval_app.py` (changing it needs a restart). Forage reads no other SearXNG variable. The companion container reads `SEARXNG_SECRET` (**secret, required**; unset means the container exits 1), `SEARXNG_VALKEY_URL` (optional) and `SEARXNG_LIMITER` (optional, off by design — a working limiter 429s Forage's client and caps API formats at 4 requests/hour). Canonical reference: `docs/configuration.md`, `docs/searxng.md`. |
| **Companion image** | `ghcr.io/washingbearlabs/forage-searxng` — `searxng/Dockerfile` (digest-pinned upstream) plus `searxng/config/settings.yml` (`formats: [html, json]`, port 8080, `limiter: false`, four engines with 5 s timeout each) and `searxng/config/limiter.toml`. Engine parity with `SEARXNG_ENGINES` is asserted by `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator` (which reads it through `pipeline/orchestrator.py`'s `_SEARXNG_ENGINES` alias). Own tag lane `searxng-v*`. |
| **Timeouts / retries** | `search_searxng_timeout_seconds` (default 10.0) bounds connect, headers and body together, with the same inner httpx per-operation guard; parse/sanitization/classification are outside it. Raise the budget for a slow instance; the default's whole-interaction semantics are tighter than before. **Zero retries.** SearXNG-side `outgoing.request_timeout: 10`, per-engine 5 s. |
| **Health signal** | **None.** `/health` never probes SearXNG; `DegradedReason` in `pipeline/contract.py` contains `promptguard_unavailable`, `cache_unavailable` and `cache_unauthenticated`. SearXNG absence surfaces only per request. |
| **Failure impact** | `/search` returns **422** `{error, reason, request_id}` with `searxng_error` (non-2xx: `"SearXNG returned HTTP error (http_<code>)"`; a 429 here means the limiter was turned on) or `searxng_unavailable` (connect refused, DNS, timeout, oversized or unparseable body: `"SearXNG not reachable at <scheme>://<host>:<port>: <detail>"` — the reason echoes scheme, host and port, never userinfo and never exception text; `detail` is a closed token). `/retrieve` and `/extract` are unaffected. `/metrics.search.errors` is keyed by code. |
| **Rate limits** | None on the shipped image; not built for direct exposure (no published ports in compose). |

`README.md` and `docs/configuration.md` were corrected to match this real behaviour
(per-request 422, never a `degraded_reasons` value) by `search-release` US-001.

### Brave Search API

| Attribute | Value |
|-----------|-------|
| **Purpose** | The optional paid search backend for `POST /search`, used only as `chain[0]` (fallback chain traversal is spec 3). Registered only when `FORAGE_BRAVE_API_KEY` is present (`pipeline/search_providers/brave.py`). Returns extracted content chunks (`content_kind="chunk"`), not one-line SERP snippets, but every chunk runs through the same stage 1-3 sanitization loop as a SearXNG result. |
| **Client / protocol** | `httpx.AsyncClient(timeout=<search_brave_timeout_seconds>, follow_redirects=False, trust_env=False, verify=ssl.create_default_context())` GET the fixed constant `https://api.search.brave.com/res/v1/llm/context` (`_BRAVE_LLM_CONTEXT_URL`, no operator override — no `SEARXNG_URL`-style env var exists for it) with `q` (truncated to `search_brave_query_max_chars` before egress) and `count=<max_results>`; auth via the `X-Subscription-Token` header only, never the URL or query string. The response body is bounded at 1 MiB (`_BRAVE_MAX_RESPONSE_BYTES`) before it is parsed, and each mapped chunk is capped to `search_brave_chunk_max_chars` before it reaches the orchestrator. One request per `/search` on a Brave-served chain; zero retries (ruling 18). |
| **Configuration** | `FORAGE_BRAVE_API_KEY` — **secret**, optional, read exactly once in the lifespan; unset (or not present per `brave_key_present()`) means the `brave` chain entry is skipped with one WARNING (`brave_skipped_missing_key`) and the deployment falls back to SearXNG. `config.yaml` top-level scalars, read **unconditionally** at boot so an out-of-range value refuses boot whether or not `brave` is in the chain: `search_brave_timeout_seconds` (1.0-60.0, default 15.0), `search_brave_chunk_max_chars` (200-2000, default 2000), `search_brave_query_max_chars` (50-400, default 400). Canonical reference: `docs/configuration.md`, `kit_tools/docs/ENV_REFERENCE.md`. |
| **Timeouts / retries** | `search_brave_timeout_seconds` (default 15.0) is a whole-interaction HTTP budget with an inner per-operation httpx guard; **zero retries** (ruling 18). Identity is requested; gzip/deflate replies are self-decoded under 1 MiB decoded / 4 MiB raw caps. The chain can spend the sum of its budgets plus parse/sanitization/classification time. `/search` on a Brave-only chain makes at most one paid call. |
| **Health signal** | `/health` reports `search_providers` (the resolved chain) and `capabilities.brave_api_key: 1` when a usable key is set, absent otherwise (`search-policy-and-health` US-002, shipped in `v1.1.0`); a failing key still surfaces only per request, as `brave: auth` in `provider_errors`. |
| **Failure impact** | `/search` returns **422** `{error, reason, request_id}` with `search_unavailable`, reason `brave: <failure_class>` — `auth` (rejected key), `rate_limited` (`429`), `timeout`, or `hard_error` (everything else, diagnosed by the `brave_search_failed` log line's `detail` token) — never the key, the endpoint, or exception text. `/retrieve` and `/extract` are unaffected. `/metrics.search.errors.search_unavailable` counts it. See `kit_tools/docs/TROUBLESHOOTING.md`. |
| **Rate limits** | None enforced by Forage — no spend ceiling and no in-request retry (ruling 12, ruling 18). Brave is billed per query (`$5`/1,000, no free tier); the operator's own account/plan is the only ceiling. |

### Valkey / Redis (content cache)

| Attribute | Value |
|-----------|-------|
| **Purpose** | Caches `RetrievedContent` for `POST /retrieve` only. `/search` and `/extract` are never cached. |
| **Client / protocol** | `redis.asyncio` (`redis>=5.0.0`) via `from_url(url, decode_responses=False, socket_connect_timeout=2.0, socket_timeout=2.0)`; only `ping`, `getrange`, `set(ex=)`, `delete`, `aclose` are used (`cache.py::ValkeyStorage`). The single positional `getrange(key, 0, max_value_bytes)` atomically reads at most bound + 1 bytes. URL query keys `decode_responses`, `encoding`, `encoding_errors` and `protocol` refuse boot because they override client keywords; socket timeouts remain tunable. |
| **Configuration** | `VALKEY_URL` — **secret** (may carry a password; use an env file, never inline `-e`); unset by default; standard `redis://[:password@]host:port/db`. `FORAGE_CACHE_HMAC_KEY` is the optional, CSPRNG-generated runtime signing secret; use one key across all replicas, stop all before rotation. `config.yaml` `cache.max_entries` 256 (1-4096) and `cache.max_bytes` 33554432 = 32 MiB (1 MiB-128 MiB) bound memory storage; `cache.max_value_bytes` 4194304 = 4 MiB (512 KiB-8 MiB) bounds each value on both backends. All are validated at boot **regardless of backend**; invalid values raise `CacheConfigurationError`, unknown names warn and are ignored. |
| **Timeouts / retries** | Connect deadline 2 s. On failure: reconnect backoff 1 s doubling to a 30 s cap (`_RECONNECT_*` in `cache.py`), single-flight lock, callers during backoff get an immediate miss. `get` returns `None`, `set` returns `False`; a cache outage **never raises** and never fails a `/retrieve`. |
| **Health signal** | `/health.cache_connected` (`ping_if_due()` in Valkey mode; always `true` in memory mode), `/health.cache_backend` `valkey` or `memory`, and `cache_unavailable` / `cache_unauthenticated` in `degraded_reasons`. Both reasons may coexist; memory reports neither. `capabilities.cache_hmac_key: 1` means a usable key was resolved for Valkey at boot, independently of connectivity. `/metrics.cache`: `reconnect_attempts`, `reconnect_successes`, `reconnect_failures`, `operation_failures`, `storage_hits`, `storage_misses`, `storage_evictions`, `storage_oversize_skips`, `corrupt_entries`, `integrity_rejects`. |
| **Failure impact** | During an outage `/retrieve` works uncached; `/health` goes `degraded` with `cache_unavailable`. Without signing, cached content lacks proof of origin and is served without re-sanitization: an open poisoning path on shared Valkey, reported as `cache_unauthenticated`. Logs use closed connection/operation, parse and six-reason integrity vocabularies, never the URL or key value (see `patterns/LOGGING.md`). |
| **Compose wiring** | `compose/full.yml` only: service `valkey` = `valkey/valkey:8@sha256:3fbd2e3e…` (8.1.10), `valkey-server --save 60 1 --appendonly no`, volume `forage-valkey-data:/data`, no ports, no password; Forage gets the literal `VALKEY_URL=redis://valkey:6379/4` and the bare `FORAGE_CACHE_HMAC_KEY` runtime passthrough from the private env file. |

Backend selection happens once per start (`_select_cache_storage` in `retrieval_app.py`)
and **never falls back** from a configured Valkey to memory — a typo must not silently
become an unshared cache:

| `VALKEY_URL` | Storage | `/health.cache_backend` | `/health` |
|---|---|---|---|
| fully unset | `InMemoryStorage` (bounded LRU, per-process, non-persistent) | `memory` | `healthy`, `cache_connected: true`, no connection attempted |
| reachable, usable signing key | `ValkeyStorage` | `valkey` | `healthy` |
| reachable, no signing key | `ValkeyStorage` | `valkey` | `degraded`, `cache_unauthenticated` |
| set and unreachable | `ValkeyStorage` | `valkey` | `degraded`, `cache_unavailable` |
| set but unparseable | `ValkeyStorage` | `valkey` | `degraded`, `cache_unavailable` (never a failed boot) |
| set to `""` | `ValkeyStorage` | `valkey` | `degraded`, `cache_unavailable` — empty string is **not** memory mode |

The table assumes PromptGuard is loaded; otherwise add `promptguard_unavailable`.
The last three rows also add `cache_unauthenticated` if no signing key is configured.
Reachability alone does not authenticate cached content.

### Hugging Face Hub and the GHCR weights mirror (PromptGuard, stage 3)

| Attribute | Value |
|-----------|-------|
| **Purpose** | Runtime acquisition of `meta-llama/Llama-Prompt-Guard-2-22M` (gated repo, Llama 4 Community License) at the pinned revision `11614a155199674a0a95e6602d6ab0417b790ed0` (`DEFAULT_MODEL_REVISION` in `model_fetcher.py`). `weights_manifest.json` pins five files with sha256 and size (`config.json`, `model.safetensors` 283,347,432 B, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json`; about 270 MiB). No weights are ever in the image. |
| **Client / protocol** | Primary leg: `huggingface_hub.snapshot_download(MODEL_ID, revision, cache_dir=$HF_HOME/hub, allow_patterns=[*.json, *.model, *.safetensors, *.txt], token=HF_TOKEN)`. Fallback leg (tried only after the HF leg fails): `oras pull <FORAGE_WEIGHTS_MIRROR>:<revision>` (oras 1.3.4, sha256-pinned per arch in `Dockerfile`; `ORAS_TIMEOUT_S` 1800; token on stdin; artifact type `application/vnd.washingbearlabs.forage-weights.v1+tar`; staged under `$HF_HOME/staging/`, capped at 2x manifest bytes + 10%). Both legs end in exact-set sha256 verification; a refused set is moved to `$HF_HOME/quarantine/` (one generation kept) and never loaded. Loader uses `use_safetensors=True`, `local_files_only=True`. |
| **Configuration** | `HF_TOKEN` (**secret**, optional; read token, only ever passed to `snapshot_download`). `HF_HOME` (image sets `/app/model-cache`; mount the `forage-model-cache` volume here or every recreate re-downloads). `FORAGE_MODEL_REVISION` uses the selected model's committed pin; a malformed value falls back to the pin with `model_revision_invalid`; a well-formed value that is not that pin refuses to verify (`weights_revision_unpinned`). Pins live per model under `weights_manifest.json.models`. `FORAGE_WEIGHTS_MIRROR` (default `ghcr.io/washingbearlabs/forage-weights`, **private** — useless to third parties; `http://`, userinfo, `:tag` or `@digest` are refused). `FORAGE_MIRROR_TOKEN` (**secret**, optional read-only registry token). Walk-through: `docs/weights.md`. |
| **Timeouts / retries** | Acquisition runs in an `asyncio` task started by the lifespan and **never blocks startup**. `WeightAcquisition.run()` retries **forever** until loaded: 30 s initial backoff doubling to 600 s, plus or minus 20% jitter (`RETRY_*` constants in `model_fetcher.py`, not env-configurable), single-flight (a second caller is refused, not queued). Each armed retry logs WARNING `weights_retry_scheduled`; each failed round logs ERROR `weights_unavailable — … Attempts: huggingface=<code>, mirror=<code>`. Warm start with a verified set does zero network (measured 9 s warm / 19 s cold on the reference envelope (1 vCPU / 1 GB), configurable via `FORAGE_CPUS` / `FORAGE_MEM_LIMIT` — see `docs/configuration.md` § Sizing the container). |
| **Health signal** | `/health.promptguard_loaded` (always honest), `degraded_reasons: ["promptguard_unavailable"]`; `capabilities` (a presence map that may also carry `brave_api_key`, independently) omits `search_sanitization` here, carrying it (`{"search_sanitization": 1, ...}`) once loaded. `/metrics.model`: `fetch_failures`, `verify_failures`, `quarantines`, `fetch_in_progress`, `retries_scheduled` — the only way to tell "downloading" (`fetch_in_progress: true`) from "waiting out backoff" (`retries_scheduled > 0`) from "first attempt unfinished" (both zero). |
| **Failure impact** | Stage 3 fails closed (`pipeline/stage3_promptguard.py`). `trusted` tier: skipped anyway (`promptguard_state: skipped_trusted`). `standard`/`untrusted` with `promptguard_fail_closed=true` (the default): verdict `injection_detected`, penalty -0.5, `promptguard_state: unavailable_blocked`, body replaced by the content-free quarantine text — still **HTTP 200**. `verified` tier or `fail_closed=false`: `unavailable_allowed`, penalty -0.1, content returned. `/search` (always `standard`): results withheld with `omitted_by_reason.promptguard_unavailable` when fail-closed, or returned with `unscanned_results > 0` and `promptguard_unavailable: true` when fail-open. `/extract` uploads are always `untrusted` and fail-closed, so every upload is quarantined while the model is absent. |
| **Break glass** | `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION=1` (legacy alias `POPPY_RETRIEVAL_LEGACY_CAPABILITY=1`; exactly `1`) forces `capabilities.search_sanitization` on with a per-boot WARNING; `status` and `promptguard_loaded` stay honest. Never the fix for a missing token. |

Operator-side vendoring of a new revision to the mirror is `scripts/vendor_weights.py`
(not in the image; needs `HF_TOKEN`, `GHCR_USER`, `GHCR_TOKEN` with `write:packages`, and
`GITHUB_TOKEN`). Revision, manifest and mirror tag move in one commit, and because
`pipeline/sanitizer_revision.py` hashes `MODEL_ID@revision`, a revision bump rotates
`sanitizer_revision` and flushes the content cache.

### The open web (stage 5 fetch, `/retrieve`)

| Attribute | Value |
|-----------|-------|
| **Purpose** | Fetch the caller's URL for `POST /retrieve`. Forage is SSRF-capable by nature, so every hop is validated. |
| **Client / protocol** | `httpx.AsyncClient(timeout=30.0, follow_redirects=False, verify=ssl.create_default_context())` in `pipeline/stage5_url_audit.py`; streaming GET; one client per `fetch_url` call. Redirects are followed by a manual loop, each hop re-validated and recorded in `redirect_chain`; a netloc change sets `domain_changed_on_redirect` (-0.1 trust). DNS pinning: `url_validator.validate_url` resolves off-loop, rejects if **any** address is private, then the request is sent to the resolved IP with `Host: <hostname>` and `sni_hostname` so TLS verification stays on. One user agent per top-level fetch, chosen from `config.yaml user_agents` (five shipped) or the built-in list. |
| **Configuration** | No env vars. `config.yaml`: `user_agents`, `seed_blocklist` (shipped `[]`; merged with `/retrieve`'s `blocked_domains`, canonical apex plus dot-boundary subdomains; also applied to `/search` result URLs, merged before caller entries), `news_domains` (cache TTL only, suffixes opt in with a leading dot). The fetch tunables are code constants, not config keys. |
| **Timeouts / retries** | `DEFAULT_TIMEOUT` 30 s per request, `DEFAULT_MAX_REDIRECTS` 5, `DEFAULT_MAX_CONTENT_BYTES` 10 MiB (Content-Length fast reject, then a streamed cap). **No retries.** |
| **SSRF defence** | Schemes `http`/`https` only; hostname `localhost` and suffixes `.local` / `.localhost` rejected before DNS; private and reserved IPv4 and IPv6 ranges (link-local, CGN, multicast, documentation ranges, `0.0.0.0` and `::`); five classes of **embedded IPv4** unwrapped and checked against the IPv4 list — IPv4-mapped, 6to4, Teredo (the client field), NAT64 (**only inside `64:ff9b::/96`**) and IPv4-compatible (**only inside `::/96`**), the prefix guards being what keeps a public embedding and ordinary public IPv6 fetchable; unparseable addresses treated as unsafe. Full range list in `url_validator.py`. |
| **Health signal** | **None** — per request only. `/metrics.retrieve.errors` keyed by code; `/metrics.retrieve.requests`. |
| **Failure impact** | All **422** on `/retrieve` with `{error, reason, request_id}`: `private_ip` (reason echoes the resolved IP — `contract/GOVERNANCE.md` ruling (d), a DNS oracle if the private-network posture is broken), `blocked_domain`, `invalid_url` (bad scheme, no hostname, DNS failure), `fetch_timeout` (httpx timeout), `content_too_large` (over 10 MiB), `fetch_error` (everything else, including more than 5 redirects and TLS errors; `"Failed to fetch <url>: <exc>"`). Reasons are **not** content-free: they echo the URL. |

### GitHub Container Registry (publish target)

| Attribute | Value |
|-----------|-------|
| **Purpose** | Hosts the three images: `ghcr.io/washingbearlabs/forage` (service), `ghcr.io/washingbearlabs/forage-searxng` (companion, own `searxng-v*` lane) and `ghcr.io/washingbearlabs/forage-weights:<revision>` (**private** OCI artifact of vendored weights). Public since 2026-09-10; anonymous pulls were verified at the flip. |
| **Runtime use** | Only the weights-mirror fallback (`oras pull`), and only when `FORAGE_MIRROR_TOKEN` is set. A GHCR outage does not affect a running container beyond that leg (`pull_failed` / `timeout` outcomes). |
| **Publish gates** | `.github/workflows/ci.yml` `publish` job needs `lint`, `typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke`; the pushed amd64 image is verified layer-for-layer against the smoke-tested artifact; on `v*` tags a Release is created carrying `contract/openapi.yaml` and `openapi.yaml.sha256`, read back and checked against the committed anchor. |
| **Tag scheme** | `v1.2.3` publishes `1.2.3`, `1.2`, `latest`; any tag containing `-` (e.g. `v0.9.3-rc`) publishes the exact tag only; push to `main` publishes `sha-<short>`. The git tag is the version; `pyproject.toml`'s `version` is inert. Reference: `docs/releases.md`. |
| **Current state** | Two non-pre-release tags published, both verified against GHCR: `v1.0.0` (2026-09-12 UTC, commit `f4c2b16`) minted `latest`, `1.0` and `1.0.0` at index digest `sha256:d83639cc…`; `v1.1.0` (2026-09-18 UTC, commit `06b01b14`) moved `latest` and minted `1.1` and `1.1.0` at `sha256:e1b875cc…`, serving contract `1.2.0` (`docs/releases.md` § "Released versions" has the full digests). `compose/*.yml` pin `forage:1.1.0` and `forage-searxng:0.1.1-rc`, and both pins resolve. `v0.9.2-rc` was withdrawn after failing the parity gate (package-version deletion recorded as pending). |

---

## Consumers

### Poppy

Poppy is the monorepo Forage was extracted from and the only known consumer. Its
behaviour is described here from Forage's own docs and tests
(`tests/test_contract_errors.py`, `docs/bootstrap-notes.md`); the Poppy repo was not read.

**What Poppy must do:**
- Compare `/health.contract_version` (**1.3.0**) on its **MAJOR** and refuse to activate on
  a mismatch (`CLAUDE.md` invariant 4). **Never** compare `sanitizer_revision`: the two
  repos' revisions diverged deliberately twenty-three times (Forage `464b6ad5…`, Poppy still
  `e6b2b56d…`; `docs/bootstrap-notes.md` is the running record, not this count).
- Vendor the contract by the procedure in `contract/GOVERNANCE.md`: pick a tag (never
  `latest`); fetch `openapi.yaml` and `openapi.yaml.sha256` from the **same** tag (git
  tag, `gh release download v<ver> --pattern 'openapi.yaml*'`, or
  `docker run --rm --entrypoint cat <image> /app/contract/openapi.yaml`); run
  `sha256sum -c openapi.yaml.sha256`; commit both; record the tag. The anchor is
  currently `c9cd19bad84decd7415ba912ae81c826447f2a19edc41b57c857d4a7b4d42ab2`.
- Its client caches `sanitizer_revision` from `/health`, pins the ten `/extract` error
  codes, rejects an `/extract` 422 lacking `sanitizer_revision`, gates web search on
  `capabilities.search_sanitization`, and buckets unknown `omitted_by_reason` /
  `degraded_reasons` members — which is why adding an enum member is MINOR with an
  announcement obligation (`contract/GOVERNANCE.md` ruling (b)).

**Coexistence rule (temporary, from `CLAUDE.md`):** until Poppy's "spec 6" pins a
published Forage image, Poppy's in-tree copy (`services/retrieval/` in the monorepo) is
the **deployed source of truth**. Any fix to the extracted paths on either side is
replayed by hand onto the other, and the Poppy source commit is recorded in
`docs/bootstrap-notes.md`'s pin record (currently `f73e2091…`). Before Poppy points at this
image its Forage env file must become `required: true` (else production silently drops
to memory mode), and no image may ever be pushed from Poppy's Dockerfile, which still
carries `ARG HF_TOKEN`.

No other consumers are known; open-source users start from the `compose/*.yml` fragments.

---

## Data Stores

There is **no database** — no relational or document store, no ORM, no migrations
(`kit_tools/arch/CODE_ARCH.md`: "State is the content cache; it is allowed to be absent").
`kit_tools/arch/DATA_MODEL.md` was intentionally **not created** for that reason; the
content-cache scheme below is the only data-shape knowledge the repo needs.

### Content cache (`cache.py`)

| Attribute | Value |
|-----------|-------|
| **Type** | Valkey / Redis (`ValkeyStorage`, `redis.asyncio`) **or** bounded in-process LRU (`InMemoryStorage`), selected once at start; one `ContentCache` policy layer over a `CacheStorage` protocol so the two cannot drift. |
| **Purpose** | Serialised `RetrievedContent` responses for `POST /retrieve` (body text, trust fields, provenance, tz-aware `retrieved_at`). |
| **Location** | `compose/full.yml` `valkey` service (self-hosted, DB 4, no password, no published port) or the Forage process's own memory (`compose/minimal.yml`). |
| **Connection** | `VALKEY_URL` (**secret**); fully unset selects memory mode. |

**Key scheme:** `ret:` + `sha256hex(f"{normalize_url(url)}:{extract_mode}:{policy_fingerprint}")`.
`normalize_url` lowercases scheme and host, strips `utm_*`, `fbclid`, `gclid`, `ref` and
`source` parameters, strips the fragment and trailing slash, and sorts the query.
`policy_fingerprint` is the first 16 hex chars of the sha256 of canonical JSON over
`blocked_domains` (sorted, lower-cased), `classifier_loaded`, `promptguard_fail_closed`,
`promptguard_threshold`, `sanitizer_revision`, `trusted_domains`, `verified_domains` —
so a revision rotation, a threshold change, or the model finishing its load all
invalidate existing entries without any flush command (`cache_policy_fingerprint` in
`cache.py`).

**Values:** `RetrievedContent.model_dump_json()` as UTF-8 bytes, wrapped as
`v1.<hex-mac>.<json>` when signing is enabled on Valkey. The HMAC covers the version,
cache key and exact payload, so a relocated envelope fails verification before parsing.
Memory and keyless Valkey store bare JSON; the latter lacks authenticity. A hit is returned as
`model_copy(cache_hit=True, cached_at=retrieved_at)` with a fresh `request_id`.

**TTL rules:** Valkey `EX = effective_ttl_hours * 3600`, where effective TTL is the
caller's `cache_ttl_hours` (default 24, max 8760) capped to 1 h for canonical matches in
`config.yaml news_domains`: bare entries match only themselves; leading-dot entries
cover apex and subdomains. The shipped six entries opt in. Read-time revalidation deletes an entry older than the
caller's current TTL or one with a tz-naive `retrieved_at`. `cache_ttl_hours=0` deletes
the variant and skips both read and write ("zero-TTL purge").

The shared rule is directional: denylist `evil.com` covers `www.evil.com`, never
`notevil.com`; allowlist `example.com` matches only itself, `.example.com` adds every
subdomain. IP literals and single-label denylist entries are equality-only;
single-label allowlists are invalid.

**Refusals:** never stores `untrusted` or `blocked` tiers (`_NO_CACHE_TIERS`) or any
`injection_detected` result. Both backends skip writes over `cache.max_value_bytes`
(4 MiB, including the signed prefix) after deleting any superseded entry;
`storage_oversize_skips` also counts memory's own `cache.max_bytes` refusal.
Memory bounds: `cache.max_entries` 256 and `cache.max_bytes` 32 MiB, evicting
expired entries first then LRU; sized for the 1 GiB `mem_limit` in compose.
Keep `cache.max_value_bytes` equal across replicas; lowering it can reject old,
larger Forage-authored values as `oversize`, not proof of tampering.

**Two counter layers:** `/metrics.retrieve.cache_hits` / `cache_misses` count request
outcomes; `/metrics.cache.storage_*` count storage operations.

**Backup:** none. The cache is a rebuildable derivative; Valkey persists with
`--save 60 1 --appendonly no` only so a restart is warm.

### Other state (not a database)

- **`forage-model-cache` volume** at `/app/model-cache` (= `HF_HOME`): `hub/` (verified
  snapshot), `quarantine/` (one generation of refused weights), `staging/` and `xet/`
  (purged on every acquisition path). Both compose fragments name it explicitly so it is
  shared between them. Without it every recreate is a cold 270 MiB download — not an
  error, just slow. See `docs/weights.md`.
- **Upload spool** for `/extract`: `0600` tempfiles, unlinked in `finally`.
- **In-process metrics** (`/metrics`): reset on restart, per container; there is no exporter.

---

## Background Jobs / Scheduled Tasks

No cron, no worker processes. The one long-running task is in-process:

| Job Name | Schedule | Purpose | Runs On |
|----------|----------|---------|---------|
| `WeightAcquisition.run()` | Started once by the lifespan; retries 30 s doubling to 600 s (plus or minus 20% jitter) until the model is loaded | Verify the cached PromptGuard snapshot or fetch it (Hugging Face, then GHCR mirror), verify, quarantine on refusal, load the classifier | An `asyncio` task in the Forage process (`model_fetcher.acquire_and_load` inside `asyncio.to_thread`); handle on `app.state.model_task`, cancelled at shutdown |

**Trigger:** lifespan startup; not awaited, so uvicorn starts serving immediately and
`/health` answers `degraded` until the load lands, then `promptguard_loaded` flips to
`true` in place with no restart.

**Duration:** about 9 s warm (verified set on the volume, zero network) or 19 s cold on
the reference envelope (1 vCPU / 1 GB), configurable via `FORAGE_CPUS` / `FORAGE_MEM_LIMIT` —
see `docs/configuration.md` § Sizing the container; the CI smoke budget for `/health` to answer is 120 s.

**Failure handling:** never raises (`weights_acquisition_crashed` is logged and the loop
continues); single-flight, so a second caller is refused rather than queued; a token-less
container logs ERROR `weights_unavailable` roughly every 10 minutes forever, by design.
Progress is legible only through `/metrics.model` (`fetch_in_progress`,
`retries_scheduled`), because nothing configures Python logging and INFO lines are dropped
(`kit_tools/docs/GOTCHAS.md`).

**Manual trigger:** none. Supplying `HF_TOKEN` (or `FORAGE_MIRROR_TOKEN`) converges in
place at the next retry; restarting the container runs the first attempt immediately.

---

## Failure Impact Matrix

Every row is HTTP-level truth from `pipeline/contract.py`'s 18-code vocabulary and the
`/health` / `/metrics` models in `retrieval_app.py`. "Unaffected" means the endpoint's
behaviour and status code do not change. Symptom-first remedies are in
`kit_tools/docs/TROUBLESHOOTING.md`; counter semantics in `kit_tools/docs/MONITORING.md`.

| Dependency down | User-visible effect per endpoint | `/health` change | `/metrics` counters that move | Recovery behaviour |
|---|---|---|---|---|
| **SearXNG** unreachable or erroring, including the 200-empty-plus-`unresponsive_engines` shape (`search-fallback` US-002) | Terminal for `/search` only when the configured chain is exactly `[searxng]`: `/search`: **422** `searxng_unavailable` (refused, DNS, 10 s timeout, bad JSON) or `searxng_error` (non-2xx; a 429 means the limiter is on) on that default lone-`searxng` chain; any other configured chain advances to the next provider and refuses with `search_unavailable` only once exhausted, reason `<provider_name>: <failure_class>`. `/retrieve`, `/extract`: unaffected. | **None.** Still `healthy` — SearXNG is not probed (see the documented discrepancy above). | `search.requests`, `search.errors.searxng_unavailable` / `search.errors.searxng_error` / `search.errors.search_unavailable` | Stateless: the next `/search` succeeds as soon as SearXNG answers. No reconnect logic. A changed `SEARXNG_URL` needs a Forage restart (read at import). |
| **A configured non-SearXNG provider** failing (any chain that is not exactly one `searxng`) | `/search`: **422** `search_unavailable`, reason `<provider_name>: <failure_class>` — the closed pair, never an endpoint or upstream text. `/retrieve`, `/extract`: unaffected. | **None.** Provider status is not a `/health` field in this spec. | `search.requests`, `search.errors.search_unavailable` | Stateless, per request; no in-request retries (ruling 18). Which provider failed is in the 422 `reason`, not the counter key. |
| **Brave** rejecting, rate-limiting, timing out, or otherwise failing (configured as `chain[0]`) | `/search`: **422** `search_unavailable`, reason `brave: auth` / `brave: rate_limited` / `brave: timeout` / `brave: hard_error` — never the key, the endpoint, or upstream text. `/retrieve`, `/extract`: unaffected. | **None.** Not a `/health` field until spec 4. | `search.requests`, `search.errors.search_unavailable` | Stateless, per request; no in-request retries (ruling 18) and no spend ceiling (ruling 12). `FORAGE_BRAVE_API_KEY` is read once at process start, so a rotated or revoked key needs a container restart. |
| **Valkey** unreachable at boot or dropped mid-run (`VALKEY_URL` set) | `/retrieve`: served uncached (`cache_hit: false`), no cache-outage error. `/search`, `/extract`: unaffected. | `status: degraded`, `cache_unavailable` in `degraded_reasons`, `cache_connected: false`, `cache_backend: valkey`; unsigned Valkey also adds `cache_unauthenticated` | `cache.reconnect_attempts`, `cache.reconnect_failures`, `cache.operation_failures` (mid-run), `retrieve.cache_misses`; closed connection/operation WARNING | Automatic reconnect with 1 s doubling to 30 s backoff, driven by traffic and `/health` polls (`ping_if_due`); `cache.reconnect_successes` increments and `cache_unavailable` clears. `healthy` requires no other degraded reason, including missing signing. Changing `VALKEY_URL` needs a restart; `""` is not memory mode — unset it fully. |
| **Valkey signing key absent** | Cached `/retrieve` content is served without proof of origin or re-sanitization: an open poisoning path on shared Valkey. | `cache_unauthenticated`, no `capabilities.cache_hmac_key`, even when connected | `cache_hmac_key_missing` once at startup; a flat `integrity_rejects` cannot establish authenticity | Stop every replica, set the same CSPRNG-generated `FORAGE_CACHE_HMAC_KEY` everywhere, start. See the credential recipe; never mix keys or keyed/keyless replicas. |
| **`VALKEY_URL` unset** (memory mode, `compose/minimal.yml`) | Not a failure: `/retrieve` cached per process, entries lost on restart and never shared across containers. | `cache_backend: memory`, `cache_connected: true` always; this mode **cannot** report `cache_unavailable`. | `cache.storage_evictions`, `cache.storage_oversize_skips` under pressure | Not applicable. Set `VALKEY_URL` and restart to switch backends. |
| **PromptGuard weights unavailable** (no `HF_TOKEN`, token lacks gated-repo access, HF Hub down, mirror down, verification refused, load failed) | `/retrieve`: **200** but `standard`/`untrusted` content with the default `promptguard_fail_closed=true` is quarantined — content-free body, `injection_detected: true`, `injection_spans: ["promptguard_unavailable"]`, `promptguard_state: unavailable_blocked`, penalty -0.5; `trusted` domains unaffected (`skipped_trusted`); `verified` or `fail_closed=false` returns content with `unavailable_allowed`, -0.1. `/search`: results withheld via `omitted_by_reason.promptguard_unavailable` (fail-closed) or returned with `unscanned_results > 0`, `suspicious: true`, `promptguard_unavailable: true` (fail-open). `/extract`: **every** upload quarantined (`unavailable_blocked`) — uploads are always untrusted and fail-closed. | `status: degraded`, `degraded_reasons: ["promptguard_unavailable"]`, `promptguard_loaded: false`, `capabilities` (a presence map that may also carry `brave_api_key`, independently) omitting `search_sanitization` — unless break-glass is armed, in which case only `capabilities.search_sanitization` lies | `model.fetch_failures` (per reached-and-failed leg, or once when no leg was attempted), `model.verify_failures` + `model.quarantines` (refused set), `model.fetch_in_progress` (downloading now), `model.retries_scheduled` (waiting out backoff); `retrieve.promptguard_state.unavailable_blocked` / `unavailable_allowed`, `retrieve.blocked_by_reason.promptguard_unavailable`, `search.omitted_by_reason.promptguard_unavailable`, `search.unscanned_results` | The retry loop runs forever (30 s to 10 min, jittered); once a leg succeeds, `promptguard_loaded` flips in place, `capabilities` gains `search_sanitization`, and every existing cache entry is invalidated because `classifier_loaded` is in the key fingerprint. Remedy for the token-less case is a Hugging Face read token with the Meta license accepted, via env file — never the break-glass variable. |
| **Target web site** slow, down, oversize, or resolving privately | `/retrieve`: **422** `fetch_timeout` (30 s), `fetch_error` (transport, TLS, more than 5 redirects), `content_too_large` (over 10 MiB), `private_ip`, `invalid_url` (DNS failure, bad scheme), `blocked_domain`. `/search`, `/extract`: unaffected (`/search` result URLs are canonicalised and audited — literal private, embedded-private and blocklisted hosts are dropped under `blocked_url` — but never fetched, and never resolved). | **None.** | `retrieve.requests`, `retrieve.errors.<code>` | Stateless, per request; no retries, nothing to recover. |
| **GHCR** down | Running containers: no effect unless the weights-mirror fallback is in use (`weights_fetch_failed source=mirror reason=pull_failed|timeout`, then retried). New image pulls and the `publish` job fail. | Only via the weights row above, and only when `FORAGE_MIRROR_TOKEN` is set. | `model.fetch_failures`, `model.retries_scheduled` (mirror leg only) | Weights: the retry loop. Publishing: re-run the workflow; an interrupted push leaves orphan blobs and is safe to re-run (`docs/releases.md`). |
| **`/app/config.yaml`** missing or invalid | Missing file: WARNING and code defaults, service runs. Invalid `cache:` or `extraction:` value: **the container exits at start** (`CacheConfigurationError` / `ExtractionConfigurationError`). | Invalid bounds: boot never completes; no `/health`. | None — the process is not up. | Fix the mounted file and restart. Invalid signing keys or forbidden Valkey URL query options also refuse boot; see the startup diagnostics in Troubleshooting. |

---

## Startup Order

There is **no required order**. Neither compose fragment declares `depends_on`, and
Forage tolerates both companions being absent: it starts, answers `/health` immediately
(as `degraded` until weights load), reports `/search` failures honestly per request, and
reconnects to Valkey in place. To avoid a visible degraded window, start them in this
order anyway:

1. `valkey` (`compose/full.yml` only) — so the first `/health` shows `cache_connected: true`
2. `searxng` — companion; needs `SEARXNG_SECRET` in `compose/.env` or it exits 1
3. `forage` — the service itself

Inside the Forage process the lifespan (`retrieval_app.lifespan`) runs: load
`/app/config.yaml` → validate `extraction:` (bad value aborts boot) → derive
`sanitizer_revision` → validate `cache:` (bad value aborts boot, whichever backend) →
select and connect the cache backend (2 s deadline, no fallback) → create the classifier
and start the weights task → yield. Only config validation can stop the boot; every other
failure is reported through `/health` and `/metrics`.

---

## Network Boundaries

Private network only. Forage has no auth, so anything that can reach `:8020` can drive
SSRF-capable fetches; the compose fragments therefore publish the port on the host
loopback and publish nothing for the companions. Invariants across both files (loopback
binding, service names, volume literal, absent limiter, required secret) are asserted by
`tests/test_compose_fragments.py`.

| Boundary | `compose/minimal.yml` (project `forage-minimal`) | `compose/full.yml` (project `forage-full`) |
|---|---|---|
| **Starts** | `forage` + `searxng` | `forage` + `searxng` + `valkey` |
| **Published ports** | `forage`: `127.0.0.1:8020:8020` only. `searxng`: none. | Same, plus `valkey`: none. |
| **Forage env** | `HF_TOKEN`, `FORAGE_SEARCH_PROVIDERS` and `FORAGE_BRAVE_API_KEY` bare pass-through (genuinely unset if absent — unset is the default `searxng` chain). `VALKEY_URL` deliberately absent (memory mode). `SEARXNG_URL` absent (default `http://searxng:8080` — the compose service name `searxng` is load-bearing). | Same, plus the literal `VALKEY_URL=redis://valkey:6379/4`. `SEARXNG_VALKEY_URL` is intentionally **not** wired (limiter stays off). |
| **Volumes** | `forage-model-cache:/app/model-cache` (explicit `name:`, shared across fragments) | Same, plus `forage-valkey-data:/data` (project-scoped) |
| **Secrets** | `compose/.env` (gitignored): `HF_TOKEN` (optional), `FORAGE_BRAVE_API_KEY` (optional), `SEARXNG_SECRET` (required-or-fail via `${SEARXNG_SECRET:?…}`) | Same |
| **Limits** | `forage` `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` (default `1024m`), `cpus: ${FORAGE_CPUS:-0}` (unset/0 omits the cap), `restart: unless-stopped` | Same |
| **Image pins** | `forage:1.1.0`, `forage-searxng:0.1.1-rc` | Same |

**Pin sequencing:** both fragments pin `ghcr.io/washingbearlabs/forage:1.1.0` and
`ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc`. The service pin resolves: `v1.1.0`
published on 2026-09-18 (`search-release` US-002), so the `manifest unknown` a
`docker compose up` returned before that date was sequencing, not breakage — the failure any
pin to a not-yet-published tag produces. The searxng pin stays a pre-release because
no non-pre-release `searxng-v*` tag exists.

**Egress from the `forage` container:** `searxng:8080` and `valkey:6379` on the compose
network; `huggingface.co` (HF leg, only with `HF_TOKEN`); `ghcr.io` (mirror leg, only
with `FORAGE_MIRROR_TOKEN`); and arbitrary public hosts on 80/443 for stage 5 fetches,
with private and reserved ranges refused by `url_validator.py`. A warm-start container
with weights on the volume and `VALKEY_URL` unset makes no egress at all until its first
`/retrieve` or `/search`.

**Inbound to `forage`:** any host-local client on `127.0.0.1:8020` — in practice Poppy.
FastAPI's `/docs`, `/redoc` and `/openapi.json` are served on the same port, unauthenticated.

Related: `kit_tools/arch/INFRA_ARCH.md` (container and CI shape),
`kit_tools/docs/DEPLOYMENT.md`, `kit_tools/docs/LOCAL_DEV.md` (running the companions
locally), `kit_tools/docs/ENV_REFERENCE.md` and `docs/configuration.md` (every variable
named above).
