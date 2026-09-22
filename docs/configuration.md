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

<!-- boundary-text:start -->

`/search` finds and returns provider-extracted content for a query across sources —
snippets or chunks, per result `content_kind` — from the configured provider chain, every
result sanitized, never cached; `/retrieve` fetches and sanitizes one caller-named URL
through the full pipeline, cached by `sanitizer_revision`. Only `/retrieve` honours
`cache_ttl_hours`, `extract_mode`, `trusted_domains` and `verified_domains`; only
`/search` honours `allow_paid_fallback`, `num_results` and `providers` and scans every
result at trust tier `standard`. Shared by both routes: `blocked_domains`,
`promptguard_threshold` and `promptguard_fail_closed`. On both routes an omitted or
null threshold uses the validated `config.yaml` default (shipped as 0.85), then
`promptguard_threshold_ceiling` bounds the requested or default value.

<!-- boundary-text:end -->

**And so are the three documentation endpoints FastAPI serves alongside them** — easy to
forget, because nothing in this repo declares them:

| Endpoint | Auth | What it exposes |
|----------|------|-----------------|
| `GET /openapi.json` | none | The generated OpenAPI document: every route, every response shape, every error code, and the service's `contract_version` as `info.version`. |
| `GET /docs` | none | Swagger UI over that document — **interactive**, so a reader can issue real `/retrieve`, `/search` and `/extract` calls from the browser. |
| `GET /redoc` | none | ReDoc over the same document; read-only rendering. |

Swagger UI also registers `GET /docs/oauth2-redirect`, an inert OAuth callback page —
Forage configures no OAuth flow, so it has nothing to redirect.

None of the three leaks a secret (the document is generated from the same models this
repo publishes as `contract/openapi.yaml`, and Forage holds no user data), but `/docs` is
a working client for an unauthenticated SSRF-capable service, and the document is a map
of the attack surface. Treat all three as part of what network placement protects. If you
want them gone on a particular deployment, FastAPI takes `docs_url=None`,
`redoc_url=None` and `openapi_url=None` — Forage does not expose that as configuration,
because turning the contract off is not a substitute for putting the service on a private
network.

There is no API key, no bearer token, no allowlist, and no rate limit. **Anyone who can
reach port 8020 can make Forage fetch arbitrary URLs on your behalf, can read every
counter `/metrics` exposes, and can read the full API contract.**

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
- **A configured paid key raises the stakes.** With `FORAGE_BRAVE_API_KEY` set and
  `brave` in `FORAGE_SEARCH_PROVIDERS`, anyone who can reach port 8020 can spend the
  operator's money on Brave queries — Forage enforces no budget cap. Network placement and a front-side proxy or rate limit are your
  controls; `/health` discloses key presence (`capabilities.brave_api_key`) to anyone who
  can reach it. `/metrics` `search.paid_calls` and `search.fallback_fired` are how spend
  is seen.

The bundled SearXNG configuration (`searxng/config/`) makes the same assumption: its
rate limiter is off and its `secret_key` is a non-secret placeholder, because that
instance is private-network-only and Forage is its only client.

---

## Environment variables

### Runtime

| Variable | Default | Purpose |
|----------|---------|---------|
| `VALKEY_URL` | **unset** — the content cache runs in memory | Connection string for the Valkey/Redis content cache. Standard `redis://` URL, including the database index. Setting it selects the Valkey backend; leaving it unset selects the bounded in-memory one. **May carry a password — see credential handling below.** |
| `SEARXNG_URL` | `http://searxng:8080` | Base URL of the SearXNG instance backing `POST /search`. |
| `FORAGE_SEARCH_PROVIDERS` | `searxng` | Ordered, comma-separated chain of search backends `POST /search` resolves at container start. Known names are `searxng` and `brave`; **any entry other than `searxng` sends the caller's query to that provider**, so add one only if you mean to. An unknown name refuses the boot (the resolved names are in the startup log); a set-but-blank value logs a WARNING and resolves to the default. Read once at start — restart to apply. |
| `FORAGE_BRAVE_API_KEY` | unset | API key for Brave's paid LLM-Context search endpoint. **Carries a credential** — supply it the same way as `VALKEY_URL`, with `--env-file` or an explicit `environment:` entry until spec 5 US-004 adds the compose passthrough. With it set, a `brave` entry in `FORAGE_SEARCH_PROVIDERS` sends the caller's query text — whatever the calling agent put in it, truncated to `search_brave_query_max_chars` — to Brave's API under the operator's account and terms; the call needs direct HTTPS egress and ignores proxy variables by design. A key-less `brave` entry is skipped (WARNING `brave_skipped_missing_key`) rather than refusing the boot, and a chain where every entry was skipped this way falls back to SearXNG alone (a second WARNING, `search_chain_defaulted_to_searxng`, marks the substitution): **no key means SearXNG-only, fully supported.** Read once at start — restart to apply. |
| `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` | unset | **Break-glass only** — see below. |
| `POPPY_RETRIEVAL_LEGACY_CAPABILITY` | unset | Deprecated alias of `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION`, kept so a pre-extraction deployment keeps working. Identical semantics. |
| `HF_HOME` | `/app/model-cache` (set by the image) | Hugging Face cache directory the PromptGuard weights are fetched into and read from. Override only if you mount the weights elsewhere. Mount a volume here or the weights are re-fetched on every container recreate. |
| `HF_TOKEN` | unset | Hugging Face access token for the **gated** `meta-llama/Llama-Prompt-Guard-2-22M` repository. Optional — see "Weights acquisition" below. **Carries a credential**; supply it the same way as `VALKEY_URL`. |
| `FORAGE_MODEL_REVISION` | the committed pin (a 40-character commit sha) | Which upstream revision of the weights to fetch, verify and load. Only a full commit sha is accepted — a branch name is refused with an error and the committed pin is used instead. |
| `FORAGE_WEIGHTS_MIRROR` | `ghcr.io/washingbearlabs/forage-weights` | The OCI **repository** holding the vendored weights, used when Hugging Face cannot supply them. A repository, never a tag: the tag is always `FORAGE_MODEL_REVISION`, so redirecting the mirror cannot also redirect which revision it serves. Validated to a lower-case `<registry>/<owner>/<name>`, optionally prefixed `https://` — anything else (an `http://` scheme, embedded credentials, a tag or digest) is refused with an error and the mirror is treated as unconfigured. |
| `TMPDIR` | the platform default (`/tmp` in the image) | Parent of the process-private spool directory `forage-spool-<uid>` that `/extract` uploads and `/retrieve`'s fetched PDFs are written to for the PDF worker. **Must be sticky or not writable by other users**; tmpfs recommended. See "The spool directory" below. |
| `FORAGE_MIRROR_TOKEN` | unset | Registry credential for `FORAGE_WEIGHTS_MIRROR`. Optional — without it the mirror is skipped exactly as a missing `HF_TOKEN` skips Hugging Face. **Carries a credential**; supply it the same way as `VALKEY_URL`. A **read-only** token, scoped as narrowly as your registry allows — see `docs/weights.md` § "The mirror read token". |

`SEARXNG_URL`'s default is a deliberately neutral service name — it assumes a compose
network with a service literally called `searxng`, and nothing more. On the default
chain an unreachable SearXNG surfaces per request as a `/search` 422
(`searxng_unavailable`), never as a `degraded_reasons` value — `/health` never probes
SearXNG. Provider *status* is instead
the `search_providers` field on `/health`: the resolved chain's names, a configuration
echo rather than a liveness probe. A missing PromptGuard (`promptguard_unavailable`) or a
configured-but-unreachable Valkey (`cache_unavailable`, see the table below) is what
degrades the service.

### The spool directory (`TMPDIR`)

The PDF worker is a spawned child that re-opens its input **by path**, so both PDF routes
write the document to a spool file first: `/extract` its upload (`poppy-extract-*`) and,
since `hardening-retrieve-parity` US-003, `/retrieve` every fetched PDF
(`forage-retrieve-*`). Both land in one directory,
`<TMPDIR>/forage-spool-<uid>`, which Forage creates for you on first use with mode `0700`
— never wider at any instant, because it is created with that mode rather than chmod-ed
after. The boot checks it once and each spool checks it again: if the path already
exists as a symlink, as a non-directory, owned by another user, or with any group or other
permission bit, the boot is **refused** (`RetrieveConfigurationError` with a closed token
such as `spool_dir_mode`) and a `/retrieve` fetched PDF is refused 422 `extraction_failed`
/ `pdf_spool_error`. Forage never repairs such a directory: one already present with the
wrong owner or mode is evidence that something else put it there. Remove it and restart.

**The parent requirement.** `TMPDIR` itself must be **sticky** (like `/tmp`, mode `1777`)
**or not writable by other users**. The per-call check re-establishes owner and mode before
every spool, but inside a world-writable, non-sticky parent another local user could
delete and re-create the directory between two requests; the sticky bit, or a parent
nobody else can write, is what stops that.

**Why it matters — confidentiality, not only integrity.** A spool file holds fetched
third-party content, possibly from an internal or authenticated URL the agent was asked to
read. It is `0600` inside a `0700` directory and unlinked on every normal exit path —
success, every worker failure, a failed spool write and a cancelled request. A **tmpfs**
`TMPDIR` keeps that content off durable storage altogether. On a non-tmpfs `TMPDIR`, a
process killed with SIGKILL (an OOM kill, `docker kill`) mid-parse leaves its
`forage-retrieve-*` or `poppy-extract-*` file behind, and those orphans are
**content-bearing**: they survive until the next manual clear of the spool directory. No
sweep runs at boot (an open question in the resource-envelope spec).

**Cancellation ownership.** Cancelling a `/retrieve` task cannot stop its Python
worker thread. The request therefore keeps its admission slot and waits for the existing
bounded PDF worker to finish (or hit its wall-clock limit and be killed/reaped), then
unlinks the spool before propagating cancellation. Repeated cancellation does not detach
that work or admit a replacement early. Shutdown must allow this cleanup time; SIGKILL
still bypasses it. This does not change `/extract`'s cancellation behavior.

**Disk footprint.** The combined worst case is the two routes' reservations added
together: `/extract`'s existing `extraction_concurrency × max_input_bytes` +
`admission_queue_depth × max_input_bytes` (50 MiB + 50 MiB ≈ 100 MiB at the defaults)
plus `/retrieve`'s `fetch_concurrency × 10 MiB` active + `max_queued_fetch_bytes` queued
(10 MiB + 30 MiB = 40 MiB) — about **140 MiB** at the defaults. It is an upper bound: a
queued request has not spooled yet. On a tmpfs `TMPDIR` that figure is **memory**, and it
sits beside the container's memory ceiling rather than inside the worker's 384 MiB rlimit
— size the tmpfs and the container limit together.

**Latency.** The worker spawns a fresh interpreter per call
(`multiprocessing.get_context("spawn")`), which costs on the order of **hundreds of
milliseconds** on a cold spawn. A fetched PDF now pays that on its first fetch; repeat
fetches of the same URL are served by the content cache and spawn nothing. Set the
consumer's `/retrieve` request timeout accordingly.

### Cache backend selection

`VALKEY_URL` decides which storage the content cache runs over, once per container
start. There are two backends and one rule:

| `VALKEY_URL` | Backend | `/health` `cache_backend` | `/health` `status` |
|---|---|---|---|
| **Fully unset** | Bounded in-memory (see the `cache:` block below) | `memory` | `healthy` — nothing is missing, this is a supported deployment |
| Set and reachable | Valkey | `valkey` | `healthy` |
| Set but unreachable | Valkey | `valkey` | `degraded`, `cache_unavailable` |
| Set to an unparseable URL | Valkey | `valkey` | `degraded`, `cache_unavailable` — never a failed boot |
| **Set to the empty string** | Valkey | `valkey` | `degraded`, `cache_unavailable` |

`cache_backend` reports the choice this container made, not the one its environment would
make now: it is decided once, at start, and fixed for the life of the process. Changing
`VALKEY_URL` takes a restart, and until then `/health` keeps telling you what is actually
running.

The rule is that **only a fully unset variable means "no Valkey"**. Anything else is a
Valkey you asked for, and a Valkey you asked for and did not get is reported, never
silently replaced with an in-memory cache: the memory backend is per-process and
non-persistent, so quietly substituting it would hand a broken deployment a cache
nothing else in it shares.

The empty string is deliberately on the "configured" side of that line, which is worth
stating plainly because it is the case most likely to surprise. `VALKEY_URL=${VALKEY_URL}`
in a compose file, an `env_file` line with nothing after the `=`, or a templating step
that rendered nothing all produce an empty value, and every one of them is a mistake
someone should hear about rather than a request for memory mode.

There is **no connection attempt at all** when the variable is unset — not to a default
host, not to `localhost`. Forage has no baked-in Valkey address.

Consequences of memory mode, in one place:

- The cache lives in the one uvicorn worker's process. Nothing is shared with another
  container and nothing survives a restart.
- It is bounded — `cache.max_entries` and `cache.max_bytes` below — and evicts rather
  than grows.
- `cache_connected` in `/health` means "the selected backend is operational", so it is
  always `true` in memory mode. It is not a statement that Valkey is present —
  `cache_backend` is the field that answers that one.
- The cache serves `POST /retrieve` only. `/search` has never been cached, in either
  backend.

> **Production deployments should set it.** Poppy — the consumer this service was
> extracted from — runs Valkey and keeps doing so: its Forage environment file **will
> be** declared `required: true` on the Poppy side when it adopts this image (the
> extraction epic's spec 6 — as of this story Poppy still builds the vendored tree and
> its env_file is `required: false`; do NOT point Poppy at this image before that flag
> flips, or a missing env file silently drops prod to memory mode), so a missing or
> unmounted env file fails that deployment loudly instead of quietly dropping it into
> memory mode, and the
> epic's live checklist asserts `cache_backend == "valkey"` on the running container
> rather than trusting the config. If you run more than one Forage replica, or want the
> cache to survive a restart, set `VALKEY_URL`.

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
- Rotating the password means restarting the container — the URL is read once per start,
  when the cache backend is selected.

### Credential handling for `FORAGE_BRAVE_API_KEY`

`FORAGE_BRAVE_API_KEY` is another variable that carries a secret, and the generic
mechanics match `VALKEY_URL` above — that subsection is the fuller treatment; this one
spends its length on what is Brave-specific.

- **Runtime container environment only.** The key reaches Forage through the running
  container's environment and nowhere else; Forage reads no secret store at boot.
- **Never a build argument.** `Dockerfile` takes no build arguments at all (`CLAUDE.md`
  invariant 2), so a `--build-arg FORAGE_BRAVE_API_KEY=…` attempt is the exact leak shape
  the repo went private over: a build argument is recorded in the image's layer history,
  where `docker history --no-trunc` reads it straight back out of any registry the image
  reaches.
- **Supply it through `compose/.env` (git-ignored) or a secret store, never an inline `-e`
  flag** — the same shell-history and `ps` exposure as for `VALKEY_URL`, and `docker
  inspect` shows it to anyone who can reach the Docker socket either way.
- **Forage never logs the value, and `/health` shows presence only** — the
  `capabilities.brave_api_key` entry (ruling 15), never the key and never its validity.
- **Rotating the key is a restart.** It is read once, in the lifespan, so a new value
  takes effect only when the container starts again.
- **A leaked key is metered spend with no cap in Forage.** Brave bills per query with no
  free tier ($5/1,000), and Forage enforces no spend ceiling (see "Consequences you must
  design around" above) — whoever holds a leaked key spends on your account until you
  revoke it with Brave.

**Enablement is two variables.** `FORAGE_BRAVE_API_KEY` alone changes nothing about
`/search`: the default chain is `searxng`, so the key takes effect only once `brave` is
also named in `FORAGE_SEARCH_PROVIDERS` — `FORAGE_SEARCH_PROVIDERS=searxng,brave` plus
`FORAGE_BRAVE_API_KEY=example-not-a-real-key` in the same env file. No key configured
means **SearXNG-only**: fully supported, no error, no new required secret.

**Data flow.** Forage sends the caller's verbatim query text (truncated to
`search_brave_query_max_chars`), under the operator's account, to exactly one outbound
host, `api.search.brave.com` — a fixed constant endpoint with no operator override, so an
egress allowlist needs that host and no other for Brave traffic. What comes back is
chunks (`content_kind: "chunk"`) that enter the unchanged sanitization pipeline; nothing
paid is retained, and `/search` has never been cached (see "Consequences of memory mode"
above). The key-less floor adds no outbound destination beyond the self-hosted SearXNG
and its configured engines.

**Per-provider ToS — a constraint on consumers, not a Forage cache.** Forage persists no
search result from any provider; these terms govern what a downstream consumer of
`/search` may keep. SearXNG-served results carry no persistence restriction. Brave
forbids persisting or redistributing result payloads, so Forage's own telemetry stores
metadata only — never a result body (decision 6) — and a consumer that stores Brave-served
results is bound by Brave's terms, not Forage's.

**Presence, not validity.** `capabilities.brave_api_key` reports presence, not validity: a
rejected, expired or unentitled key never changes `/health` and surfaces only per request,
as a `brave: <failure_class>` entry drawn from the closed failure-class vocabulary
(`rate_limited`, `timeout`, `hard_error`, `auth`, `quota`). When every provider in the
chain failed, the entries appear in the 422 `search_unavailable` error's `reason`
(e.g. `searxng: rate_limited; brave: auth`); when a later provider served, they appear in
the successful response's `provider_errors`. Brave maps a `401`/`403` to `auth`, and maps
plan exhaustion to `rate_limited`, because Brave answers it with the same `429` as a
per-second limit; `quota` is a class in the vocabulary that Brave does not emit today. The
per-class diagnosis is in
[`kit_tools/docs/TROUBLESHOOTING.md`](../kit_tools/docs/TROUBLESHOOTING.md).

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
- **Only `capabilities.search_sanitization` lies.** `status`, `degraded_reasons`,
  `promptguard_loaded`, `search_providers`, and `capabilities.brave_api_key` all stay
  honest — a Forage running with the override still reports itself `degraded` with
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

Unknown keys are ignored, with one boot WARNING per key:
`config_unknown_key — key=<dotted.name>`. The message names only the key, never
its value. An unknown top-level block gets one warning, not one per child;
known blocks are checked one level deep. Correct the spelling and restart.
This is not value validation: an invalid known safety setting still refuses boot
through its reader's typed error (`ExtractionConfigurationError`,
`CacheConfigurationError`, or the corresponding retrieve/Brave error).
The warn-and-fall-back exceptions are `promptguard_threshold` (fetch routes use
0.85; `/extract` retains its raw-value guard), `policy_domain_entries_max_bytes`
(65536), and invalid entries in `seed_blocklist` / `news_domains` (dropped at boot).
Those emit `config_invalid_value`, not `config_unknown_key`.

### Top-level keys

| Key | Type | Code default | Shipped | Purpose |
|-----|------|--------------|---------|---------|
| `user_agents` | list of strings | `[]` | 5 desktop browser UAs | Pool rotated across outbound fetches. Empty means the fetcher's own built-in default is used. |
| `news_domains` | list of strings | `[]` | 6 leading-dot wire/major outlets | Domains whose cached entries expire after **at most 1 hour**. Bare entries match only the apex; a leading dot covers the apex and every subdomain. **Upgrade note:** your bare entries stay exact; add the dot for subdomains. The six shipped entries now have it (`.bbc.co.uk` covers `www.bbc.co.uk`). |
| `seed_blocklist` | list of strings | `[]` | `[]` | Deployment-wide denylist merged into both routes, `/retrieve` and `/search`, before caller `blocked_domains`; caller entries cannot evict it. **Upgrade note:** existing multi-label entries now cover subdomains; review apex entries before upgrading, because a multi-tenant apex removes every tenant. Single-label entries keep matching exactly as before. This list is policy, not a secret: observable through `/retrieve`'s refusal message and `/search`'s `blocked_url` counts. |
| `promptguard_threshold` | float | `0.85` | `0.85` | Injection score above which stage 3 marks content as injected. `/retrieve` and `/search` use this boot-validated default when the request omits the field or sends `null`, then apply `min(value, promptguard_threshold_ceiling)`; an explicit request value is capped too. Numeric strings remain accepted. Invalid values (including YAML booleans, non-finite or out-of-range numbers) warn once with `config_invalid_value` and fall back to `0.85`, never refusing boot for this validation; `promptguard_threshold_resolved` logs the validated default once at INFO. `/extract` instead retains its own per-request `float(raw_value)` conversion and range guard, outside the resolver and ceiling: invalid numeric strings/ranges still give its existing unsupported-format refusal, but YAML `true` becomes `1.0`, disabling blocking on `/extract` only while the fetch routes warn and default to `0.85`. The WARNING explicitly says `/extract reads the raw value through its own guard`; closing that divergence is an open question. The raw configured value still feeds `sanitizer_revision`; the resolved active threshold feeds `cache_policy_fingerprint`. **Upgrade note (1.3.0):** raising this key above `0.85` to quiet `/extract` false positives now **loosens** injection blocking on `/retrieve` and `/search` unless `promptguard_threshold_ceiling` bounds it; a value below `0.85` **tightens** both. The old per-route config knob is gone (caller overrides remain), and the content cache re-keys. |
| `policy_domain_entries_max_bytes` | integer | `65536` (64 KiB) | `65536` | Raw UTF-8 bytes per caller domain list, including newline separators; range **4096–1048576** (4 KiB–1 MiB). Each `/retrieve` list and `/search`'s denylist has its own budget. An over-budget denylist is refused whole with 422 `content_too_large` on `/retrieve` or `search_unavailable` on `/search`, reason `policy_domain_list_too_large`; allowlists retain the in-budget prefix and count all remaining entries as drops. Invalid configuration logs `config_invalid_value — key=policy_domain_entries_max_bytes` and falls back to 65536, never refuses boot. Read once at startup; restart after changing it. |
| `extract_route_enabled` | boolean | `false` | `false` | Release gate for `POST /extract`. While `false` the route returns **404** — it is invisible, not merely refused. Requires a restart to take effect. Remember there is no authentication in front of it. |
| `search_brave_timeout_seconds` | float | `15.0` | `15.0` | Per-request timeout for the Brave LLM-Context HTTP call. This is `/search`'s worst-case latency on a Brave-only chain until spec 3's fallback exists. Out of range (1.0 to 60.0) or wrong-typed refuses boot. A caller's `/search` timeout must exceed the sum of the configured chain's per-provider timeouts — 10 s + this value for `searxng,brave` — so lower this value rather than raising the caller's. |
| `search_brave_chunk_max_chars` | integer | `2000` | `2000` | Cap on each Brave result's extracted-chunk text before it reaches sanitization. Out of range (200 to 2000) or wrong-typed refuses boot. |
| `search_brave_query_max_chars` | integer | `400` | `400` | Cap on the outbound query text sent to Brave. Out of range (50 to 400) or wrong-typed refuses boot. |
| `cache` | mapping | `{}` (all defaults) | both keys at their defaults | Bounds for the bounded in-memory content-cache storage — see below. |
| `extraction` | mapping | `{}` (all defaults) | all keys set to their maxima | Resource limits for untrusted document extraction — see below. |
| `retrieve` | mapping | `{}` (all defaults) | all four keys at their defaults | Fetch-route admission and classification limits — see the `retrieve:` block below. |
| `promptguard_fail_closed_floor` | boolean | `false` | `false` | Operator fail-closed floor on both fetch routes; see "Top-level PromptGuard policy keys" below. |
| `promptguard_threshold_ceiling` | float | `1.0` | `1.0` | Operator threshold ceiling on both fetch routes; see "Top-level PromptGuard policy keys" below. |
| `promptguard_wait_seconds` | float | `30.0` | `30.0` | Classification-permit wait budget on both fetch routes; see "Top-level PromptGuard policy keys" below. |

Domain matching is directional: denylist `evil.com` blocks `evil.com` and
`www.evil.com`, never `notevil.com` or `evil.com.attacker.net`. Allowlist
`example.com` matches only itself; `.example.com` includes every subdomain.
IP literals match only themselves; single-label denylists are exact-only and
single-label allowlists are rejected. All entries use the same UTS-46 host
canonicaliser (case and one trailing dot normalised). Config lists are unbudgeted,
normalised at boot, and invalid entries produce one `config_invalid_value` WARNING
per list naming the dropped entries; misplaced credential/URL-shaped entries are redacted.

Request lists are normalised once in the `/retrieve` and `/search` handlers before pipeline entry.
`blocked_domains` is measured before **any** caller entry is canonicalised, then
normalised in full if in budget. `trusted_domains` and `verified_domains` consume
raw bytes in order before normalising each retained entry; the first entry that
exceeds the budget and every following entry are dropped. There is no entry-count
cap. The operator's canonical `seed_blocklist` is merged first and has no caller
budget, so caller entries can never evict it. Invalid entries and over-budget
allowlist drops increment `retrieve.policy_invalid_domain_entry` once per entry,
without logging or storing the offending value. `/search` counts invalid denylist
entries on `search.policy_invalid_domain_entry`; an oversized denylist on either
route is refused before normalisation, not partially enforced or counted as drops.

**This is an encode-work bound, not request-body admission.** FastAPI has already
parsed the entire JSON body into `list[str]` before the handler runs; neither
`/retrieve` nor `/search` has a request-body size limit here. Retain private-network
placement and enforce body-size limits at the caller-facing proxy as appropriate.

| Per-request allowlist | Consequence and caution |
|---|---|
| `trusted_domains` | A leading-dot entry skips injection classification for every host under the suffix. Never name a multi-tenant or registry-level apex (`.co.uk`, `.github.io`, `.s3.amazonaws.com`). `retrieve.policy_suffix_trusted_skip` counts wildcard-caused resolutions on uncached retrievals. |
| `verified_domains` | A leading-dot entry makes every host under the suffix degrade open when the classifier is unavailable, including under `promptguard_fail_closed_floor` and a load-triggered classification wait timeout. Never name a multi-tenant or registry-level apex (`.co.uk`, `.github.io`, `.s3.amazonaws.com`). The same `retrieve.policy_suffix_trusted_skip` counts these resolutions, even when classification is available. |

### The `cache:` block

Bounds for `InMemoryStorage`, the bounded in-process content-cache storage that sits
under the cache's policy layer — the backend an unset `VALKEY_URL` selects (see "Cache
backend selection" above). They are validated at startup regardless of which storage
is active, so an invalid known value refuses boot rather than silently widening a
memory bound. A misspelled key instead warns and is ignored.

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
safety limits. A misspelled key instead warns and is ignored.

Note the pattern: for most keys the shipped value **is** the maximum, so these knobs
exist to make the service *more* conservative, not less.

| Key | Default | Allowed range | Purpose |
|-----|---------|---------------|---------|
| `max_input_bytes` | `52428800` (50 MiB) | 1 MiB – 50 MiB | Hard ceiling on an uploaded document. Enforced by streaming byte count, not by `Content-Length`. |
| `max_pages` | `500` | 1 – 500 | Maximum PDF pages parsed before the extraction is abandoned. |
| `child_cpu_seconds` | `20` | 1 – 20 | CPU-time rlimit on the spawned pypdf worker process. |
| `child_address_space_bytes` | `402653184` (384 MiB) | 128 MiB – 512 MiB | Address-space rlimit on that worker. The 1 GiB container reserves ≥512 MiB for the parent FastAPI + torch + PromptGuard process, so parser working memory cannot eat the parent's reservation. |
| `wall_clock_seconds` | `90` | 1 – 90 | Total wall-clock budget for one extraction, worker included. |
| `max_promptguard_chunks` | `64` | 1 – 64 | PromptGuard chunk budget for one document. Derives the classifiable character ceiling: `(512 − 64) × chunks × 4` = 114,688 characters at the default. Fetched PDFs run under `extraction.max_promptguard_chunks`; fetched HTML under `retrieve.max_promptguard_chunks` — a fetched PDF over this ceiling is refused 422 `content_too_large` / `promptguard_budget` (`hardening-retrieve-parity` US-003). |
| `extraction_concurrency` | `1` | 1 – 1 | Concurrent extractions. Pinned at 1 — the memory reservation above assumes exactly one worker. |
| `classification_concurrency` | `1` | 1 – 1 | Concurrent PromptGuard classifications. Pinned at 1 for the same reason. Since `hardening-retrieve-parity` US-006 it sizes **all three** classifying routes, not just `/extract`: `/retrieve` and `/search` take the same permit around their own stage 3. See the sizing rule below. |
| `admission_queue_depth` | `1` | 0 – 4 | Requests allowed to wait for the extraction slot. `0` means reject immediately with `busy` (HTTP 429) whenever the slot is taken. |
| `max_queued_upload_bytes` | `52428800` (50 MiB) | 0 – 50 MiB | Total bytes of queued uploads held in flight. `0` disables queuing of upload bodies. |

<a id="retrieve--fetch-route-limits"></a>

### The `retrieve:` block

The `/retrieve` counterpart to `extraction:`, read by
`pipeline/retrieve_limits.py` at boot: an out-of-range value refuses startup rather than
surfacing as a strange refusal on the first request.

Unlike `extraction:`, which is bounded *at* its defaults on every key but two because its
memory reservation assumes exactly one worker, three of these four keys are **raisable**.
They bound queued and classified text, which the 10 MB fetch cap already bounds per body.
`fetch_concurrency` is the exception and is pinned at `1` for the same worker reason
`extraction.extraction_concurrency` is: a fetched PDF spawns the same bounded child under
the same `child_address_space_bytes` rlimit, so N fetch slots would put N × 384 MiB of
worker address space in a 1 GiB container. The constraint is worker address space, not
fetched-body size — which means an HTML-only deployment, whose fetch path spawns no worker
at all, is throttled to single flight by a bound sized for PDFs. That is accepted; sizing
the envelope belongs to the resource-envelope spec.

**Admission.** Since `hardening-retrieve-parity` US-002 the fetch and stage 1 run under a
second admission controller — the same class `/extract` uses, with its own counters. A
request takes a slot after the cache read (a cache hit never waits) and before the fetch, and
gives it back once stage 1 is done. When no slot is free it queues, holding **nothing** — it
has not fetched — behind a queue bounded in depth (`admission_queue_depth`) and in reserved
bytes (`max_queued_fetch_bytes`, one 10 MB fetch-cap reservation per queued request). Beyond
either bound it is refused at once: **422 `busy`, reason `admission_queue_full`**, counted
under `retrieve.busy_rejections` (every request that found no free slot, queued or refused,
counts under `retrieve.semaphore_saturation`). There is no timer on the queue wait; it is
bounded by construction. At the shipped defaults that means single flight, at most four
queued by depth and three by bytes — the byte bound binds first — so the fifth concurrent
`/retrieve` (one fetching, three queued) is refused.

**Worst-case queue latency** is a derived number, not a knob:
`admission_queue_depth / fetch_concurrency × max(fetch timeout 30 s, PDF worker wall clock)`
— at the defaults, 4 × 30 s = **120 s** before a queued request reaches the fetch, plus the
stage-1 time of the requests ahead of it.

**Memory, honestly.** At most `fetch_concurrency` bodies are alive during fetch and stage 1;
a queued request holds nothing; the queue is bounded in depth and reserved bytes; a request
waiting on the classification permit holds only its extracted text, because the body and its
decoded copy are released with the slot. The stage-1 peak is
`fetch_concurrency × (10 MB body + its decoded str + the ExtractionResult's raw_text and
main_content)` — the body and its decoded copy are alive together while HTML extraction
runs, so an in-flight page costs three to five times the 10 MB body term, and the 10 MB cap
bounds the body term only. What is **not** bounded is the population of classification
waiters: `uvicorn` runs with no `--limit-concurrency` and both middlewares gate on `/extract`
alone, so admission bounds the *rate* through stage 1, not the number of requests past it.
Each such waiter costs at most `max_extracted_characters(256)` ≈ 459 KB of text for at most
`promptguard_wait_seconds`, so the waiter term is
`arrival rate × promptguard_wait_seconds × ≤ 0.5 MB`. `--limit-concurrency` is the envelope
knob that bounds it, and the resource-envelope spec owns it.

**Disk.** The HTML path writes nothing to disk: the fetched body lives in memory, inside the
slot, and nowhere else. The PDF path (`hardening-retrieve-parity` US-003) spools the fetched
body to a `0600` `forage-retrieve-*` file in the process-private spool directory and parses
it in `/extract`'s spawned, rlimited worker, under `extraction.max_promptguard_chunks`
rather than this block's budget; the file is unlinked as soon as the worker returns, on
every outcome. See "The spool directory (`TMPDIR`)" above for the requirement, the footprint and
the spawn latency. Every fetched-PDF failure is a 422 — `extraction_failed` with reason
`pdf_encrypted`, `pdf_no_text`, `pdf_extraction_error` or `pdf_spool_error`, or
`content_too_large` / `promptguard_budget` over the ceiling — never a 500. Stage 1 runs on the default thread pool (`asyncio.to_thread`, shared
with stage 3's inference), which is uncancellable — the slot is what keeps hostile pages from
starving the classifier of threads. No wall clock is put on HTML extraction; the 30 s fetch
timeout and the 10 MB cap bound its input.

There is **no environment-variable override for any key below**. `config.yaml` is copied
into the image, so changing one in a deployed container means bind-mounting a replacement
file — the procedure the resource-envelope spec documents.

| Key | Default | Allowed range | Purpose |
|-----|---------|---------------|---------|
| `max_promptguard_chunks` | `0` | 0 – 1024 | PromptGuard chunk budget for one **fetched page**. `0` means **no pre-check** and no `max_chunks` handed to the classifier — today's behaviour — and boot logs one WARNING `retrieve_budget_unset coming_default=256`. Non-zero derives the classifiable character ceiling `(512 − 64) × chunks × 4` (458,752 at the coming default of 256); a page over it is refused 422 `content_too_large` with reason `promptguard_budget`. Ships at `0` for one minor release; the next MINOR flips the default to `256`, and `0` stays a legal opt-out (`contract/GOVERNANCE.md` ruling (g)). |
| `fetch_concurrency` | `1` | 1 – 1 | Admission slots: concurrent `/retrieve` fetch-and-stage-1 work. Pinned at 1 — see above; not an operator knob until the resource-envelope spec. |
| `admission_queue_depth` | `4` | 0 – 16 | Requests allowed to wait for the fetch slot, holding no body while they wait. Beyond it a request is refused 422 `busy` / `admission_queue_full` (`retrieve.busy_rejections`). `0` means refuse immediately whenever the slot is taken. Worst-case queue latency is `admission_queue_depth / fetch_concurrency × 30 s` — 120 s at the defaults. |
| `max_queued_fetch_bytes` | `31457280` (30 MiB) | 10 MiB – 160 MiB | Bytes reserved for queued requests, one 10 MB fetch-cap reservation each — three at the default. A request whose reservation would exceed it is refused 422 `busy` / `admission_queue_full` (`retrieve.busy_rejections`). Deliberately **not** `admission_queue_depth × 10 MB`, so at the shipped defaults the byte bound binds before the depth bound and both are exercisable. |

### Top-level PromptGuard policy keys

Read at boot by `retrieve_settings_from_config`, which owns the fetch-route policy.
A non-boolean floor or a non-numeric, non-finite or out-of-range ceiling refuses boot
with a closed-vocabulary `RetrieveConfigurationError`; values are never echoed.
Numeric bounds are checked before conversion to float, so even arbitrarily large
YAML integers receive the same range refusal; integer endpoints `0` and `1` are valid.
These are **config.yaml-only**, with no environment override. For a deployed container,
bind-mount a complete replacement over `/app/config.yaml` read-only and restart; start
from the shipped file, because replacement **never merges** and omitted security keys
reset to code defaults. The full Compose delivery procedure is tracked by
[resource-envelope spec 6 US-003](../kit_tools/specs/feature-hardening-resource-envelope.md#us-003-operator-documentation--the-sizing-section-and-the-by-value-sweeps);
it has not landed yet.

| Key | Default | Allowed range | Route | Purpose |
|-----|---------|---------------|-------|---------|
| `promptguard_fail_closed_floor` | `false` | `true` / `false` | `/retrieve` and `/search` | Effective flag is `request.promptguard_fail_closed or floor`: `true` blocks STANDARD/UNTRUSTED content when the classifier is absent or the classification wait expires, even if the caller requests fail-open. `false` imposes no floor. Every 200 reports `effective_promptguard_fail_closed`; trust-tier exemptions remain. Set through the deployed-container bind mount described above. |
| `promptguard_threshold_ceiling` | `1.0` | 0.0 – 1.0 | `/retrieve` and `/search` | Effective threshold is `min(requested value or validated config default, ceiling)` where only null/omitted selects the default (zero remains zero). The default is resolved **before** capping. A lower ceiling blocks at a lower classifier score; `1.0` imposes no ceiling. Every 200 reports `effective_promptguard_threshold`, including retrieve cache hits. Set through the deployed-container bind mount described above. |
| `promptguard_wait_seconds` | `30.0` | 0.05 – 300.0 | **both fetch routes** (`/retrieve` and `/search`) | How long a request waits for a classification slot before giving up. A float, so sub-second values are expressible. On `/search` it is one budget for the whole request, not per result. `/extract` takes the same permit but waits without a deadline. |

The threshold ceiling applies to both fetch routes, including their configured default.
`/extract` stays permanently fail-closed with its own raw-value threshold guard;
neither bound reaches it and it carries neither effective field. These fields report
**policy, not proof of scanning**. Neither bound overrides caller-supplied trust tiers:
`trusted_domains` skips classification (`trusted_tier`), and `verified_domains` (VERIFIED)
degrades open when the classifier is unavailable, even under a true floor. Read
`promptguard_state` on `/retrieve` and omissions / `suspicious` /
`promptguard_unavailable` / `unscanned_results` on `/search`. Refusal 422 bodies carry
no policy fields. The handler resolves policy before caching and stamps after the
pipeline, so fingerprint inputs and reported values agree on hits and misses.

#### Sizing `promptguard_wait_seconds` against `retrieve.max_promptguard_chunks`

`extraction.classification_concurrency` is one permit, and since
`hardening-retrieve-parity` US-006 all three classifying routes take it: `/retrieve` and
`/search` around their own stage 3, `/extract` around its own. So the wait one request
faces is the time the *other* routes hold the permit, and the rule is a single sentence:

> `promptguard_wait_seconds` must exceed the worst-case single permit hold, which is
> `retrieve.max_promptguard_chunks` × the per-window classify latency on the operator's
> CPU.

A wait timeout under ordinary mixed traffic therefore means the permit holder exceeded the
wait — not that the model is missing. The fix is to raise `promptguard_wait_seconds` or to
lower `retrieve.max_promptguard_chunks`, and lowering the budget is the better of the two:
it bounds the hold rather than waiting longer for an unbounded one.

Until the resource-envelope spec measures the per-window number on the reference envelope,
a **provisional** pairing: at an assumed 100 ms per window on a 2-vCPU container, the
coming default of 256 chunks is a ~25.6 s hold, which the shipped `30.0` clears with
little margin. An operator who cannot meet that on their hardware lowers
`retrieve.max_promptguard_chunks` — to 128 for a ~12.8 s hold, to 64 for ~6.4 s — rather
than raising the wait, because a longer wait parks more requests behind the same permit
without making any of them finish sooner.

One honest qualification: **while `retrieve.max_promptguard_chunks` is `0` the rule does
not hold**, because there is no chunk budget to multiply — the worst-case hold is bounded
only by the 10 MB fetch cap, which is far more windows than any wait in range covers. An
operator who wants the sizing rule to apply sets the key explicitly; the
`retrieve_budget_unset` boot WARNING says so. The shipped default pair (`0` and `30.0`) is
recorded under Known risks for exactly that reason: on a CPU-bound classifier it makes
wait timeouts likely under even modest concurrency.

**Known risk — the shipped default pair.** `retrieve.max_promptguard_chunks: 0` with
`promptguard_wait_seconds: 30.0` leaves the permit hold unbounded by anything but the fetch
cap, so a single large fetched page can time out every other request's wait. The signal is
`retrieve.classification_wait_timeouts` and `search.classification_wait_timeouts` on
`/metrics` rising while `/health` still reports `promptguard_loaded: true` — contention,
not a missing model. Watch both counters after enabling `/retrieve` at volume, and set
`retrieve.max_promptguard_chunks` to bound the hold.

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
| `degraded_reasons` | `promptguard_unavailable` (no weights — the ML scan is not running), `cache_unavailable` (a *configured* Valkey is unreachable or its URL is unusable — see "Cache backend selection"; memory mode never reports it). |
| `promptguard_loaded` | Always honest, even with the break-glass override set. |
| `cache_connected` | "The selected backend is operational." A live ping in Valkey mode, subject to reconnect backoff; always `true` in memory mode, where there is no connection to lose. It is **not** a statement that Valkey is present — read `cache_backend` for that. |
| `cache_backend` | `valkey` or `memory` — which storage the content cache selected at start, decided once from `VALKEY_URL` and fixed for the life of the process. Added in contract `1.1.0`. This is the field that separates "healthily in memory mode" from "silently lost its Valkey"; `cache_connected` alone reports `true` for both. |
| `search_providers` | The resolved search-provider chain's names, in traversal order, after key-gated skips — e.g. `["searxng"]` or `["searxng", "brave"]`. Configuration echo fixed for the life of the process, not a liveness probe. Added in contract `1.2.0`. |
| `sanitizer_revision` | Opaque hash of the sanitization sources, the model identity, and `promptguard_threshold`. Changes when sanitization behaviour changes. |
| `contract_version` | Response-contract version. Consumers should refuse to activate on a mismatch rather than guess. |

```bash
# The served contract's version, which is the same number by construction.
curl -s localhost:8020/openapi.json | jq -r .info.version
```

Both read `pipeline/contract.py`'s `CONTRACT_VERSION` — one constant, so the two can only
disagree if something in front of Forage is rewriting responses. The suite pins the tie
(`tests/test_contract_metrics.py`); there is no deployment-time setting that moves either
value.
