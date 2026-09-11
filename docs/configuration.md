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
| `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` | unset | **Break-glass only** — see below. |
| `POPPY_RETRIEVAL_LEGACY_CAPABILITY` | unset | Deprecated alias of `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION`, kept so a pre-extraction deployment keeps working. Identical semantics. |
| `HF_HOME` | `/app/model-cache` (set by the image) | Hugging Face cache directory the PromptGuard weights are fetched into and read from. Override only if you mount the weights elsewhere. Mount a volume here or the weights are re-fetched on every container recreate. |
| `HF_TOKEN` | unset | Hugging Face access token for the **gated** `meta-llama/Llama-Prompt-Guard-2-22M` repository. Optional — see "Weights acquisition" below. **Carries a credential**; supply it the same way as `VALKEY_URL`. |
| `FORAGE_MODEL_REVISION` | the committed pin (a 40-character commit sha) | Which upstream revision of the weights to fetch, verify and load. Only a full commit sha is accepted — a branch name is refused with an error and the committed pin is used instead. |
| `FORAGE_WEIGHTS_MIRROR` | `ghcr.io/washingbearlabs/forage-weights` | The OCI **repository** holding the vendored weights, used when Hugging Face cannot supply them. A repository, never a tag: the tag is always `FORAGE_MODEL_REVISION`, so redirecting the mirror cannot also redirect which revision it serves. Validated to a lower-case `<registry>/<owner>/<name>`, optionally prefixed `https://` — anything else (an `http://` scheme, embedded credentials, a tag or digest) is refused with an error and the mirror is treated as unconfigured. |
| `FORAGE_MIRROR_TOKEN` | unset | Registry credential for `FORAGE_WEIGHTS_MIRROR`. Optional — without it the mirror is skipped exactly as a missing `HF_TOKEN` skips Hugging Face. **Carries a credential**; supply it the same way as `VALKEY_URL`. A **read-only** token, scoped as narrowly as your registry allows — see `docs/weights.md` § "The mirror read token". |

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

### Break-glass: the sanitization-advertisement override

`FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` (or its deprecated alias
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

**There are none, and that is deliberate.** `Dockerfile` declares no `ARG`, so
`docker build .` takes nothing but the source tree; every setting Forage has arrives as a
runtime environment variable, from the tables above.

This replaced an `HF_TOKEN` build argument that pre-downloaded the gated
`meta-llama/Llama-Prompt-Guard-2-22M` weights into the image. A build argument is
recorded in the finished image's layer history, where `docker history --no-trunc` reads it
straight back out of any registry the image reaches — so an image built with a token was a
published token, and no amount of cleaning the filesystem changed that. The path was
removed in full (`forage-ci-and-image` US-003); `tests/test_dockerfile.py` and CI's
`secret-grep` job both fail if it comes back.

> **Consequence: every image ships without PromptGuard weights.** They are acquired at
> *run time* instead, into the `HF_HOME` volume — see the next section.

### Weights acquisition

`feature-forage-model-bootstrap` US-001/US-004. At start Forage checks the model cache
and, if it does not already hold the pinned weight set, fetches it — from Hugging Face
first, then from the OCI mirror. Three things about that are worth knowing before you
configure it:

- **The fetch never blocks the service.** Startup yields immediately and the download runs
  behind it, so `/health` answers throughout and a container healthcheck never sees a
  hung boot. While it runs, `/metrics`' `model.fetch_in_progress` is `true` — that is what
  distinguishes "downloading ~270 MiB" from "wedged", since `/health` reports `degraded`
  for both. `promptguard_loaded` flips to `true` in place when the load completes; no
  restart is involved.
- **Nothing unverified is ever loaded.** The download is checked against the committed
  `weights_manifest.json` — an exact file set with per-file sha256, safetensors only —
  before `from_pretrained` is allowed to open it. A set that fails is quarantined, not
  loaded.
- **Every credential is optional, and their absence is a supported mode.** The Hugging
  Face repository is gated and the mirror is private, so a source without its credential
  is *skipped*, not failed. Nothing else about the service changes: extraction, the
  structural scan and the URL audit all still work. Getting a token is
  `docs/weights.md`'s subject.

#### Two sources, in order

Hugging Face is tried first, then `FORAGE_WEIGHTS_MIRROR`. The mirror is the fallback and
not an alternative: it is a copy we refresh by hand at vendor cadence, so reaching for it
while the upstream answers would let a stale artifact quietly become the source of truth.
It is what keeps the service buildable when the gated repository is unavailable — an
outage, a revoked token, or a vendor decision.

Both sources land in the same place and pass the same gate. The mirrored artifact is
pulled with `oras` (shipped in the image), extracted into a staging directory under
`HF_HOME`, checked against `weights_manifest.json` **there**, and only then moved into the
cache the loader reads. Unverified bytes never enter it. The staging directory is removed
on every path, successful or not, along with `huggingface_hub`'s own `$HF_HOME/xet/`
chunk cache — on a 1 GB container those are the space the next fetch needs.

#### When neither source answers

Forage stays `degraded` with `promptguard_unavailable` and logs a single *terminal* ERROR naming both
sources and what each did — `weights_unavailable — … Attempts: huggingface=…, mirror=…` —
(a source that was genuinely reached and failed also logs its own leg-level diagnostic
first, so a real outage produces the terminal record plus one per failed attempt) —
and `/metrics`' `model.fetch_failures` moves. That is true of a container with no
credentials at all, which is the stock image's honest state: it is a supported mode, but
not a quiet one. The outcome codes are a closed set; `skipped_no_token` means no
credential was configured for that source, `misconfigured` means `FORAGE_WEIGHTS_MIRROR`
could not be used, and anything else means the source was reached and did not deliver.

#### Warm starts, and what makes the second boot fast

Once the volume holds a verified set at the pinned revision, start-up **verifies it and
loads it, and that is all** — no download, no `oras`, and no request to Hugging Face at
any point, so a warm start works on a container with no egress whatsoever. Measured on
the reference envelope (1 vCPU / 1 GB): **19 s cold, 9 s warm** — the warm figure taken
with `--network none`.

The three environment variables above are the whole surface, and two of them only matter
on a cold boot: `FORAGE_MODEL_REVISION` decides which set counts as "the" set (change it
and the next start is cold again), while `HF_TOKEN` and `FORAGE_MIRROR_TOKEN` are simply
never read for their purpose when the cache already satisfies the pin.

> **If you do not mount a volume at `HF_HOME`, the cache lives in the container's writable
> layer.** That works and is not an error — it just means every `docker run` is a cold
> boot and re-fetches ~270 MiB. The volume is named `forage-model-cache` by convention;
> `docs/weights.md` § "The model cache volume" is the canonical spelling and describes
> what is inside it.

#### When a source is only temporarily unavailable

A failed acquisition is not final. The service retries in the background — **30 s,
doubling to a 10-minute ceiling, with ±20% jitter** — until the classifier loads, so a
Hugging Face outage, a registry hiccup, or a gated-repo approval that arrives an hour
after the container started all converge **without a restart**. The schedule is fixed in
code rather than configurable: it bounds the load a fleet of sidecars puts on someone
else's registry, and that bound is worth more than the flexibility.

Two things follow for an operator:

- **`/metrics`' `model.retries_scheduled`** counts the retries armed so far. Combined with
  `fetch_in_progress` it separates the three states `/health` reports identically:
  `fetch_in_progress: true` is downloading, `retries_scheduled > 0` with
  `fetch_in_progress: false` is waiting for the next attempt, and both at zero on a
  degraded container means the first attempt has not finished yet. Each armed retry also
  logs a WARNING naming the delay.
- **A credential-less container keeps saying so.** It retries at the ceiling for as long
  as it runs, logging its terminal ERROR each time. That is deliberate — a service that
  has been missing a capability for six hours should still be saying so — and it is the
  same choice the Valkey reconnect makes.

#### Credentials in logs

Neither token is ever logged. Failures are reported through closed reason vocabularies —
`http_401`, `timeout`, `io_failed`, `fetch_failed` for the Hugging Face leg; an exit
status plus `pull_failed` / `timeout` / `oras_missing` and friends for the mirror — rather
than by interpolating an exception or a captured subprocess stream, for the same reason
`cache.py` never prints `VALKEY_URL`: `huggingface_hub`'s errors carry request context and
a registry can put anything it likes in an error body. `FORAGE_MIRROR_TOKEN` reaches
`oras` on stdin, never as an argument, because `ps` is world-readable. Supply both through
an env file or a secret store, exactly as for `VALKEY_URL` above.

Check it with `curl -s localhost:8020/health | jq .promptguard_loaded`, and treat
standard-tier content as unscanned while it reads `false`.

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
| `cache` | mapping | `{}` (all defaults) | both keys at their defaults | Bounds for the bounded in-memory content-cache storage — see below. |
| `extraction` | mapping | `{}` (all defaults) | all keys set to their maxima | Resource limits for untrusted document extraction — see below. |

### The `cache:` block

Bounds for `InMemoryStorage`, the bounded in-process content-cache storage that sits
under the cache's policy layer. They are validated at startup regardless of which storage
is active, so a typo fails the boot loudly rather than silently widening a memory bound.

The budget is the container's real headroom: `mem_limit: 1024m` already reserves 512 MiB
for the parent FastAPI + torch + PromptGuard process and 384 MiB for the spawned
extraction worker, leaving roughly 128 MiB. The 32 MiB default spends a quarter of it.

| Key | Default | Allowed range | Purpose |
|-----|---------|---------------|---------|
| `max_entries` | `256` | 1 – 4096 | Maximum cached responses held in memory. Beyond it, entries are evicted — already-expired ones first, then least-recently-used. |
| `max_bytes` | `33554432` (32 MiB) | 1 MiB – 128 MiB | Maximum total serialised bytes held in memory, accounted exactly (values are stored as the same JSON bytes Valkey would hold). A single response larger than this bound is never cached: it is served uncached and counted in `/metrics` as `cache.storage_oversize_skips`. |

The cache serves `POST /retrieve` only — `/search` has never been cached — and it is
per-process by design (one uvicorn worker, nothing shared, nothing persisted across a
restart).

Two layers of counter appear in `/metrics` and are not duplicates of each other:
`retrieve.cache_hits` / `cache_misses` count **request** outcomes, while
`cache.storage_hits` / `storage_misses` / `storage_evictions` /
`storage_oversize_skips` count **storage operations** underneath the policy layer. Only
the in-memory storage can move the last two.

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
itself. **You no longer mount it**: since `forage-ci-and-image` US-004 it is baked into
`ghcr.io/washingbearlabs/forage-searxng`, over a digest-pinned upstream base.
[`docs/searxng.md`](searxng.md) is the full reference — running it, the pin-bump cadence,
and what CI proves. The short version:

| File | Purpose |
|------|---------|
| `settings.yml` | Instance name, enabled engines and their timeouts, `search.formats` (**`json` is required** — Forage calls the JSON API), bind address and port. **No `secret_key`**: `SEARXNG_SECRET` is required at runtime and an unset one is a hard start failure. |
| `limiter.toml` | Bot-detection settings. **Hardened, not relaxed** — no wildcard pass list, no `trusted_proxies` additions, `link_token` off. |

| Variable | Required | What it does |
|---|---|---|
| `SEARXNG_SECRET` | **yes** | Signs the SearXNG HTML UI's session cookies. No default, no baked literal. |
| `SEARXNG_VALKEY_URL` | no | `valkey://host:6379/0`. The verified current name; `SEARXNG_REDIS_URL` still works but is deprecated upstream. |
| `SEARXNG_LIMITER` | no | `true` turns the rate limiter on. See the warning below. |

Three coupling points to keep in mind:

- Forage pins an explicit engine list on every query. It must stay in sync with the
  engines enabled in `settings.yml` — otherwise a query names an engine SearXNG does not
  have. `tests/test_searxng_docker.py` asserts the two sets are equal.
- The upstream base **is** digest-pinned now, so a `:latest` bump can no longer enable
  engines this configuration never vetted. The trade is that engine fixes no longer
  arrive on their own: see the pin-bump cadence in `docs/searxng.md`.
- **The limiter is off, deliberately.** Turning it on refuses Forage's own client: an
  httpx request is 429'd on the first call (no `Accept-Language` header) and even a
  browser-shaped client gets four `format!=html` requests per hour. `docs/searxng.md`
  carries the measurements and the opt-in for a deployment that needs one anyway.

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
