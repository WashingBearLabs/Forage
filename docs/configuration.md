# Forage configuration reference

Forage is 12-factor. Everything it needs at run time arrives as an **environment
variable** at container start, plus a mounted **`config.yaml`**. There is no OpenBao
client, no secret-bearing runtime API, and no configuration database — the entrypoint is
a bare `exec "$@"` shim, so nothing is fetched from a secret store at boot.

This file is the complete reference: every environment variable, every `config.yaml`
key, their defaults, and the deployment posture you must respect for any of it to be
safe.

---

## Deployment posture

Read this before you configure anything else.

**Forage ships no authentication.** All five HTTP endpoints are unauthenticated by
design:

| Endpoint | Auth |
|----------|------|
| `GET /health` | none |
| `GET /metrics` | none |
| `POST /search` | none |
| `POST /retrieve` | none |
| `POST /extract` | none (and gated off by default — see `extract_route_enabled`) |

There is no API key, no bearer token, no allowlist, and no rate limit. **Anyone who can
reach port 8020 can make Forage fetch arbitrary URLs on your behalf, and can read every
counter `/metrics` exposes.**

Consequences you must design around:

- **Run Forage only on a private network.** A Docker bridge network shared with its
  consumer, a loopback-bound host port (`-p 127.0.0.1:8020:8020`), or a VPN segment.
  **Never publish port 8020 to the internet or to any untrusted network segment.**
- **Network placement is the access control.** Forage is a natural server-side-request-
  forgery target — making outbound requests *is its job*. It defends itself (RFC1918
  rejection, DNS-rebinding checks via manual redirect following, domain blocklists), but
  those are defence in depth behind your network boundary, not a substitute for it.
- **Forage is not a trust boundary.** It emits injection *signals* and honest health.
  Your agent's own security layer owns every trust decision. A `healthy` Forage is not a
  promise that the content it returned is safe.
- If you need authentication, terminate it in front of Forage (reverse proxy, service
  mesh, or the consuming service itself). Forage will not grow it: adding a half-auth
  layer would invite exactly the "it's protected" assumption this section exists to
  prevent.

The bundled SearXNG configuration (`searxng/config/`) makes the same assumption: its
rate limiter is off and its `secret_key` is a non-secret placeholder, because that
instance is private-network-only and Forage is its only client.

---

## Environment variables

### Runtime

| Variable | Default | Purpose |
|----------|---------|---------|
| `VALKEY_URL` | `redis://valkey:6379/4` | Connection string for the Valkey/Redis content cache. Standard `redis://` URL, including the database index. **May carry a password — see credential handling below.** |
| `SEARXNG_URL` | `http://searxng:8080` | Base URL of the SearXNG instance backing `POST /search`. |
| `FORAGE_LEGACY_CAPABILITY` | unset | **Break-glass only** — see below. |
| `POPPY_RETRIEVAL_LEGACY_CAPABILITY` | unset | Deprecated alias of `FORAGE_LEGACY_CAPABILITY`, kept so a pre-extraction deployment keeps working. Identical semantics. |
| `HF_HOME` | `/app/model-cache` (set by the image) | Hugging Face cache directory the PromptGuard weights are read from. Override only if you mount the weights elsewhere. |

The defaults for `VALKEY_URL` and `SEARXNG_URL` are deliberately neutral service names —
they assume a compose network with services literally called `valkey` and `searxng`, and
nothing more. If neither companion is reachable, Forage still starts and reports itself
`degraded` (`cache_unavailable`, and `promptguard_unavailable` on any token-less build)
rather than refusing to boot.

> **Forward note.** A later change makes an unset `VALKEY_URL` mean "run the cache
> in-memory" rather than "connect to the default host". Until then, unset means *the
> default above*, and an unreachable default means `cache_unavailable`.

### Credential handling for `VALKEY_URL`

`VALKEY_URL` is the one variable that routinely carries a secret
(`redis://:PASSWORD@host:6379/4`). Forage never logs it: `cache.py` maps every failure
to a closed reason vocabulary (`connect_failed` / `operation_failed` / `timeout`) and
never emits `str(exc)` or the URL itself, and the entrypoint prints nothing at all. A
test asserts this for both the cache-connect and service-startup paths.

Do your half:

- **Prefer an env file or a secret store.** `docker run --env-file ./forage.env …`,
  compose `env_file:`, a Kubernetes `Secret` projected as an env var, or your
  orchestrator's equivalent.
- **Avoid an inline `-e VALKEY_URL=redis://:hunter2@…` flag.** It lands in your shell
  history, in `ps` output, and in any process listing on the host.
- Remember that `docker inspect` shows a container's full environment to anyone who can
  reach the Docker socket, whichever method you used. Restrict socket access
  accordingly.
- Rotating the password means restarting the container — the URL is read once at import
  time.

### Break-glass: the legacy capability override

`FORAGE_LEGACY_CAPABILITY` (or its deprecated alias
`POPPY_RETRIEVAL_LEGACY_CAPABILITY`) set to **exactly `1`** forces `GET /health` to
advertise `capabilities: {"search_sanitization": 1}` even when the PromptGuard
classifier is not loaded.

- **Exact-match semantics on both names.** Only the literal string `1` arms it. `true`,
  `TRUE`, `yes`, `on`, `0`, `11`, `" 1"`, and the empty string all leave it disarmed.
  There is deliberately no truthiness parsing — a typo must fail safe.
- **It fires a loud warning on every boot**, naming whichever variable actually armed
  it, so an operator reading the log knows which one to unset.
- **Only `capabilities` lies.** `status`, `degraded_reasons`, and `promptguard_loaded`
  stay honest — a Forage running with the override still reports itself `degraded` with
  `promptguard_unavailable`.

> **Caveat — this is a break-glass switch, not a configuration option.**
>
> Its only legitimate use is a transition window: your consuming agent gates its
> web-search feature on Forage advertising `search_sanitization`, and you need that
> feature to keep working for a short period while the consumer-side gate catches up.
> While it is set, **your agent is being told a capability is available when the ML
> injection scan is not actually running**. That is precisely the silent-degradation
> failure the honest-health design exists to prevent (an earlier silent version of this
> failure went unnoticed for nine days in production).
>
> Set it knowingly, for a bounded window, with the boot warning visible in your logs,
> and unset it as soon as the consumer's own gate is deployed. Never bake it into a
> long-lived deployment.

### Build-time arguments

These are `docker build` arguments, not runtime variables.

| Argument | Default | Purpose |
|----------|---------|---------|
| `HF_TOKEN` | `""` (empty) | Hugging Face token used **at build time only** to pre-download `meta-llama/Llama-Prompt-Guard-2-22M`, which lives in a gated repository. With an empty token the build succeeds and simply skips the download. |

> **Never push an image built with `HF_TOKEN` to any registry you do not fully control.**
> The build argument is recoverable from the image's layer history (`docker history`).
> An image built *without* the token has no PromptGuard weights and therefore runs
> permanently `degraded` with `promptguard_unavailable` — verify with
> `curl -s localhost:8020/health | jq .promptguard_loaded` after every deploy.

---

## `config.yaml`

Loaded from the directory containing `retrieval_app.py` — inside the image that is
`/app/config.yaml`. Mount your own over it:

```bash
docker run --rm -p 127.0.0.1:8020:8020 \
  --env-file ./forage.env \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  forage
```

If the file is missing, Forage logs a warning and every key below falls back to its code
default. The repository ships a working `config.yaml`; the "shipped" column records what
that file sets, which is not always the code default.

### Top-level keys

| Key | Type | Code default | Shipped | Purpose |
|-----|------|--------------|---------|---------|
| `user_agents` | list of strings | `[]` | 5 desktop browser UAs | Pool rotated across outbound fetches. Empty means the fetcher's own built-in default is used. |
| `news_domains` | list of strings | `[]` | 6 wire/major outlets | Domains whose cached entries expire after **at most 1 hour**, regardless of the caller's requested TTL (news goes stale fast). Matched case-insensitively on the exact host. |
| `seed_blocklist` | list of strings | `[]` | `[]` | Domains merged into every request's `blocked_domains` before URL validation — a permanent, deployment-wide deny list. |
| `promptguard_threshold` | float | `0.85` | `0.85` | Injection score at or above which stage 3 marks content as injected. Also feeds the `sanitizer_revision` hash, so changing it changes that value by design. |
| `extract_route_enabled` | boolean | `false` | `false` | Release gate for `POST /extract`. While `false` the route returns **404** — it is invisible, not merely refused. Requires a restart to take effect. Remember there is no authentication in front of it. |
| `extraction` | mapping | `{}` (all defaults) | all keys set to their maxima | Resource limits for untrusted document extraction — see below. |

### The `extraction:` block

Every value is validated at startup. A non-integer, a boolean, or an out-of-range value
raises `ExtractionConfigurationError` and the service refuses to start — these are
safety limits, so a typo fails loudly rather than silently widening a bound.

Note the pattern: for most keys the shipped value **is** the maximum, so these knobs
exist to make the service *more* conservative, not less.

| Key | Default | Allowed range | Purpose |
|-----|---------|---------------|---------|
| `max_input_bytes` | `52428800` (50 MiB) | 1 MiB – 50 MiB | Hard ceiling on an uploaded document. Enforced by streaming byte count, not by `Content-Length`. |
| `max_pages` | `500` | 1 – 500 | Maximum PDF pages parsed before the extraction is abandoned. |
| `child_cpu_seconds` | `20` | 1 – 20 | CPU-time rlimit on the spawned pypdf worker process. |
| `child_address_space_bytes` | `402653184` (384 MiB) | 128 MiB – 512 MiB | Address-space rlimit on that worker. The 1 GiB container reserves ≥512 MiB for the parent FastAPI + torch + PromptGuard process, so parser working memory cannot eat the parent's reservation. |
| `wall_clock_seconds` | `90` | 1 – 90 | Total wall-clock budget for one extraction, worker included. |
| `max_promptguard_chunks` | `64` | 1 – 64 | PromptGuard chunk budget for one document. Derives the classifiable character ceiling: `(512 − 64) × chunks × 4` = 114,688 characters at the default. |
| `extraction_concurrency` | `1` | 1 – 1 | Concurrent extractions. Pinned at 1 — the memory reservation above assumes exactly one worker. |
| `classification_concurrency` | `1` | 1 – 1 | Concurrent PromptGuard classifications. Pinned at 1 for the same reason. |
| `admission_queue_depth` | `1` | 0 – 4 | Requests allowed to wait for the extraction slot. `0` means reject immediately with `busy` (HTTP 429) whenever the slot is taken. |
| `max_queued_upload_bytes` | `52428800` (50 MiB) | 0 – 50 MiB | Total bytes of queued uploads held in flight. `0` disables queuing of upload bodies. |

---

## SearXNG configuration

`searxng/config/` holds the settings for the companion SearXNG instance, not for Forage
itself. Mount it into your SearXNG container:

| File | Purpose |
|------|---------|
| `settings.yml` | Instance name, enabled engines and their timeouts, `search.formats` (**`json` is required** — Forage calls the JSON API), bind address and port, and the non-secret `secret_key` placeholder for SearXNG's own HTML UI CSRF token. |
| `limiter.toml` | Bot-detection and rate-limit settings. Relaxed, on the private-network assumption above. |

Two coupling points to keep in mind:

- Forage pins an explicit engine list on every query. It must stay in sync with the
  engines enabled in `settings.yml` — otherwise a query names an engine SearXNG does not
  have.
- The SearXNG image is not pinned to a digest upstream; a `:latest` bump can enable
  engines this configuration never vetted. That is why the engine list is explicit on
  both sides.

---

## Verifying a running instance

```bash
# Read the BODY, not the status code — /health is always 200.
curl -s localhost:8020/health | jq
```

| Field | What it tells you |
|-------|-------------------|
| `status` | `healthy` or `degraded`. |
| `degraded_reasons` | `promptguard_unavailable` (no weights — the ML scan is not running), `cache_unavailable` (Valkey unreachable). |
| `promptguard_loaded` | Always honest, even with the break-glass override set. |
| `cache_connected` | Live ping, subject to reconnect backoff. |
| `sanitizer_revision` | Opaque hash of the sanitization sources, the model identity, and `promptguard_threshold`. Changes when sanitization behaviour changes. |
| `contract_version` | Response-contract version. Consumers should refuse to activate on a mismatch rather than guess. |
