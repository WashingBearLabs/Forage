<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: tech-stack, infrastructure
  required_sections:
    - "Environment Variables"
  skip_if: never
-->
# ENV_REFERENCE.md

> **TEMPLATE_INTENT:** Document environment variables and secrets. What config exists and where to find it.

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-cache-integrity US-003)

## Overview

Forage has exactly two configuration sources, and they do not overlap:

| Source | What it carries | When it is read |
|---|---|---|
| Environment variables | Backend selection, service URLs, the model pin, and every credential | Once per process start (`SEARXNG_URL` earlier, at module import; the weights variables inside the background acquisition task the lifespan launches) |
| `config.yaml` (tracked, beside `retrieval_app.py`; `/app/config.yaml` in the image) | Tuning knobs and safety limits: UA pool, blocklist, PromptGuard threshold, the `/extract` gate, cache and extraction bounds | Once, in the FastAPI lifespan (`retrieval_app._load_config()`) |

No key exists in both, so there is no precedence rule to learn; a change to either needs a restart. There is no `.env.example`, no secret-store client, and no per-environment file set: the same image takes the same variables everywhere, from an env file.

Naming rule (`CLAUDE.md` invariant 1): the primary name of every Forage-specific variable is `FORAGE_*`. Legacy `POPPY_*` names survive only as back-compat aliases of a `FORAGE_*` primary, and no new name may contain "poppy".

**`docs/configuration.md` is the canonical, full-semantics reference** (the six-case cache-selection table, the weights acquisition sequence, break-glass semantics, every allowed range). This page is the operator's map: one row per variable with the facts that reference does not line up in one place, namely who reads it, when, where to set it, and what happens when it is unset.

---

## Environment Variables

"Read by" names the function and the phase. *Import* means `import retrieval_app`; *start* means the lifespan, once per process; *acquisition* means the background weights task the lifespan launches, which runs while the server is already serving.

### Runtime service

| Variable | Legacy alias | Default | Required | Secret | Read by | When unset |
|---|---|---|---|---|---|---|
| `VALKEY_URL` | none | unset | no | **yes** (may embed a password: `redis://:PASSWORD@host:6379/4`) | `retrieval_app._configured_valkey_url()`, start | Bounded in-memory content cache; `/health.cache_backend` reads `"memory"`. **Only a fully unset variable means this.** When set, remove the forbidden query options `decode_responses`, `encoding`, `encoding_errors` and `protocol`: any one refuses boot. |
| `FORAGE_CACHE_HMAC_KEY` | none | unset | no | **yes** | `retrieval_app._resolve_cache_hmac_key()`, start | Valkey reports `cache_unauthenticated` and logs `cache_hmac_key_missing`: cached content is served without proof of origin or re-sanitization. Memory needs no key; one configured there logs `cache_hmac_key_unused` without signing. Strip leading/trailing space/tab/LF; blank means absent, otherwise require printable ASCII without whitespace/controls and at least 32 UTF-8 bytes, never base64-decoded. Invalid values refuse boot with a value-free WARNING. See [credential handling](../../docs/configuration.md#credential-handling-for-forage_cache_hmac_key) for the CSPRNG recipe (not a passphrase) and stop-all-replicas rotation. |
| `SEARXNG_URL` | none | `http://searxng:8080` | no | no | `retrieval_app.py` line 82, **import** | Default used; assumes a compose service literally named `searxng`. An unreachable SearXNG surfaces as `searxng_unavailable` on `/search`, never as a boot failure. The client is built with `trust_env=False` (`search-provider-abstraction` US-002), so an ambient `HTTP_PROXY` / `HTTPS_PROXY` / `.netrc` / `SSL_CERT_FILE` no longer affects the SearXNG call — the one deliberate behaviour change of that extraction. Only scheme, host and port are echoed on the wire. |
| `FORAGE_SEARCH_PROVIDERS` | none | `searxng` | no | no | `retrieval_app._configured_provider_names()`, start | Default one-element chain `["searxng"]`. Ordered and comma-separated; parsed by `pipeline.search_providers.parse_provider_names` (strip, lower-case, drop empty tokens, collapse duplicates keeping the first) and resolved by `build_provider_chain` through a static dict literal — no dynamic import. Known names: `searxng` and `brave`. An unknown name raises `SearchProviderConfigurationError` out of the lifespan and **refuses the boot**; the message names the entry's 1-based position and the known names, never the token or the raw value. Set but blank: WARNING `search_providers_blank`, default applies. **Any entry other than `searxng` sends the caller's query to that provider.** The resolved names (and nothing else) are logged at start; `app.state.search_providers` holds the chain objects, never names. |
| `FORAGE_BRAVE_API_KEY` | none | unset | no | **yes** | `retrieval_app._resolve_brave_key()`, start | A `brave` entry in `FORAGE_SEARCH_PROVIDERS` is skipped (WARNING `brave_skipped_missing_key`) rather than refusing the boot; a chain left entirely empty by such skips falls back to SearXNG (WARNING `search_chain_defaulted_to_searxng`). Presence is `pipeline.search_providers.brave.brave_key_present()` — non-empty after stripping spaces/tabs/LF, ASCII, printable, no interior whitespace or control character (a lone CR included) — the same predicate `/health`'s `capabilities["brave_api_key"]` (spec 4 US-002) reads, so the two can never disagree. A present-but-unusable value logs `brave_key_invalid` and is treated as absent; the value itself is never logged. |
| `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` | `POPPY_RETRIEVAL_LEGACY_CAPABILITY` (deprecated, identical semantics) | unset | no | no | `retrieval_app._break_glass_arming_env_var()`, start (boot WARNING) and every `/health` | Override off. Armed only by the exact string `1`; then `/health.capabilities` advertises `search_sanitization` regardless of classifier state while `status`, `degraded_reasons`, and `promptguard_loaded` stay honest. |

### Model weights (`model_fetcher.py`)

| Variable | Legacy alias | Default | Required | Secret | Read by | When unset |
|---|---|---|---|---|---|---|
| `HF_HOME` | none | `/app/model-cache` (Dockerfile `ENV`, line 156; code `DEFAULT_CACHE_ROOT` restates it) | no | no | `resolve_cache_root()`, acquisition | Code default `/app/model-cache`; weights land in `$HF_HOME/hub/`, refused sets in `$HF_HOME/quarantine/`. Always set in the image; on a bare host the default path does not exist. |
| `HF_TOKEN` | none | unset | no | **yes** | `_resolve_token()`, acquisition, cold fetch only | Hugging Face leg skipped (`weights_fetch_skipped`, outcome `skipped_no_token`). With no mirror token either and no warm volume: `degraded` with `promptguard_unavailable`, indefinitely and honestly. |
| `FORAGE_MODEL_REVISION` | none | `11614a155199674a0a95e6602d6ab0417b790ed0` (`DEFAULT_MODEL_REVISION`, equal to `weights_manifest.json`) | no | no | `resolve_revision()`, acquisition | Committed pin used. Anything but a 40-hex sha logs `model_revision_invalid` and the pin is used; the value is never echoed. |
| `FORAGE_WEIGHTS_MIRROR` | none | `ghcr.io/washingbearlabs/forage-weights` (`DEFAULT_WEIGHTS_MIRROR`) | no | no | `resolve_mirror_repository()`, acquisition | Default private mirror. Must be lower-case `<registry>/<owner>/<name>`, optional `https://`; anything else logs `weights_mirror_invalid` and the mirror is treated as unconfigured. The tag is always the revision. |
| `FORAGE_MIRROR_TOKEN` | none | unset | no | **yes** | `_resolve_mirror_token()`, acquisition; fed to `oras login --password-stdin` | Mirror leg skipped, the same shape as a missing `HF_TOKEN`. Third parties cannot read the mirror, so leave both unset. |

### Companion SearXNG container (never read by Forage)

| Variable | Default | Required | Secret | Effect |
|---|---|---|---|---|
| `SEARXNG_SECRET` | none | **yes** | **yes** | Signs SearXNG session cookies. Unset: the container exits 1. Both compose fragments use `${SEARXNG_SECRET:?...}` so Compose names the missing variable before anything starts. |
| `SEARXNG_VALKEY_URL` | unset | no | no | Limiter backend (`valkey://host:6379/0`); `SEARXNG_REDIS_URL` still works but is deprecated upstream. Only meaningful with the limiter on. |
| `SEARXNG_LIMITER` | off | no | no | `true` enables the bot limiter, which 429s Forage's own httpx client on its first request. Leave it off (`docs/searxng.md`). |

### Operator tooling and CI (never read by the service)

| Variable | Used by | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | CI `publish` and `searxng-publish` (`secrets.GITHUB_TOKEN`); `scripts/vendor_weights.py --step visibility` (`read:packages`) | The **only** credential CI holds. No repository secrets exist; fork PRs run with a read-only token. |
| `HF_TOKEN`, `GHCR_USER`, `GHCR_TOKEN` (`write:packages`) | `scripts/vendor_weights.py`, human-run only | Re-vendoring the pinned weights to the private mirror (`docs/weights.md`). |

### Tests

No test-only variables exist. `tests/conftest.py` autouse-clears `HF_TOKEN`, `HF_HOME`, `FORAGE_MODEL_REVISION`, `FORAGE_WEIGHTS_MIRROR`, `FORAGE_MIRROR_TOKEN`, `VALKEY_URL`, `FORAGE_SEARCH_PROVIDERS`, `FORAGE_BRAVE_API_KEY`, and `FORAGE_CACHE_HMAC_KEY` before every test and blocks the network. CI's `smoke` job runs the built image with **no environment at all** and asserts the degraded `/health` contract, so a token-less start is a tested mode, not an accident.

---

## `config.yaml` Keys

Loaded by `retrieval_app._load_config()` in the lifespan; a missing file logs a WARNING (`config.yaml not found at ...`) and every key falls to its code default. Override in a container with `-v "$PWD/config.yaml:/app/config.yaml:ro"`. The shipped file is not always the code default, so both columns are shown.

Unknown keys are ignored with one boot WARNING `config_unknown_key — key=<dotted.name>`,
never the value. The `KNOWN_CONFIG_KEYS` registry checks top-level names and one level
inside registered blocks; an unknown block is named once, without walking its children.
Invalid known bounds still refuse boot through their typed readers. The separate
`config_invalid_value` exceptions are the fetch-route threshold, domain-list byte budget,
and invalid operator domain entries; see the canonical reference for their fallbacks.

### Top-level keys

| Key | Code default | Shipped | Type / range | Controls | Read site |
|---|---|---|---|---|---|
| `user_agents` | empty list | 5 desktop browser UAs | list of strings | Outbound User-Agent pool for stage-5 fetches; empty falls to `DEFAULT_USER_AGENTS` in `pipeline/stage5_url_audit.py` | `pipeline/orchestrator.py` line 294, per `/retrieve` |
| `news_domains` | empty list | .reuters.com, .apnews.com, .bbc.co.uk, .nytimes.com, .theguardian.com, .cnn.com | list of strings | Cache TTL capped at 1 h; bare entries match only themselves, leading-dot entries cover apex and subdomains. Upgrade: operator bare entries stay exact; shipped entries now opt in with a dot | `pipeline/orchestrator.py`, then `cache.py`, per `/retrieve`; normalised at boot |
| `seed_blocklist` | empty list | empty list | list of strings | Merged operator-first with every request's `blocked_domains` on both `/retrieve` and `/search`; callers cannot evict entries. Fetch matches are refused; search matches are omitted as `blocked_url` before content scanning. The trust-tier lists themselves (`trusted_domains`, `verified_domains`) remain per-request `/retrieve` fields | `pipeline/orchestrator.py`, `run_retrieve_pipeline` and `run_search_pipeline` |
| `promptguard_threshold` | `0.85` | `0.85` | float, 0.0 to 1.0; numeric strings accepted | Default on `/retrieve` and `/search` for null/omitted request values, then operator-capped; invalid (including bool) warns and falls back to 0.85. `/extract` keeps its raw `float()` and range guard, so YAML true still becomes 1.0 there only. Changing the raw value rotates `sanitizer_revision`; the resolved value also keys the content cache | `retrieval_app.promptguard_threshold_from_config` (boot), `_promptguard_policy_updates` (fetch handlers), `/extract` (per request); `pipeline/sanitizer_revision.py` (boot hash) |
| `policy_domain_entries_max_bytes` | `65536` | `65536` | integer, 4096 to 1048576; invalid warns and falls back | Raw UTF-8 byte budget per caller domain list, including separators. `/retrieve` truncates allowlists and counts drops; an over-budget denylist is refused whole, 422 `content_too_large` / `policy_domain_list_too_large`. Bounds encode work, not JSON body admission | `retrieval_app.py` lifespan via `bounded_int`, published as `app.state.policy_domain_entries_max_bytes`; consumed by the handler |
| `extract_route_enabled` | `false` | `false` | boolean (a non-boolean refuses boot) | Release gate: while `false`, `POST /extract` returns **404** from `ExtractionAdmissionMiddleware`. No authentication exists behind it | `pipeline/extraction_limits.py` line 100, start |
| `promptguard_fail_closed_floor` | `false` | `false` | boolean; wrong type refuses boot | `/retrieve` and `/search` apply `request.promptguard_fail_closed or floor` and report the result on every 200; trusted-tier skip and VERIFIED fail-open remain exempt | `pipeline/retrieve_limits.py` `retrieve_settings_from_config()`, start; `retrieval_app.py` `_promptguard_policy_updates()`, per request |
| `promptguard_threshold_ceiling` | `1.0` | `1.0` | finite number, 0.0 to 1.0; invalid refuses boot | Both fetch routes apply `min(resolved threshold, ceiling)` after selecting the explicit request value or configured default, and report it on every 200; `/extract` is unchanged | Same reader and handler helper as the floor; config.yaml-only, see [delivery and scope](../../docs/configuration.md#top-level-promptguard-policy-keys) |
| `search_brave_timeout_seconds` | `15.0` | `15.0` | float, 1.0 to 60.0 (wrong-typed or out-of-range refuses boot) | Wall-clock budget for connect, headers and body together, not parse or sanitization. An N-provider chain can spend the sum of its budgets. Raise it for a slow Brave instance; raise `search_searxng_timeout_seconds` for a slow SearXNG. Defaults are unchanged but now bound the whole interaction, not each socket operation. | `pipeline/search_providers/brave.py`'s `brave_settings_from_config()`, start |
| `search_brave_chunk_max_chars` | `2000` | `2000` | integer, 200 to 2000 (wrong-typed or out-of-range refuses boot) | Cap on each Brave result's extracted-chunk text before it reaches sanitization | `pipeline/search_providers/brave.py`'s `brave_settings_from_config()`, start |
| `search_searxng_timeout_seconds` | `10.0` | `10.0` | float, 1.0 to 60.0 (wrong-typed or out-of-range refuses boot with `SearxngConfigurationError`) | Wall-clock budget for connect, headers and body together; the chain may spend the sum of its budgets. Raise `search_searxng_timeout_seconds` for a slow instance: the unchanged default is tighter than per-socket-operation timing, and a formerly working four-engine fan-out can now time out and buy a paid call. No fan-out latency distribution has been measured; watch `search.provider_timeouts`. | `pipeline/search_providers/searxng.py`'s `searxng_settings_from_config()`, start, even without SearXNG in the chain |
| `search_brave_query_max_chars` | `400` | `400` | integer, 50 to 400 (wrong-typed or out-of-range refuses boot) | Cap on the outbound query text sent to Brave | `pipeline/search_providers/brave.py`'s `brave_settings_from_config()`, start |

Domain lists use canonical UTS-46 names: denylist `evil.com` covers `www.evil.com`,
never `notevil.com`; allowlist `example.com` matches only itself and `.example.com`
adds every subdomain. IP literals and single-label denylist entries match only themselves;
single-label allowlists are invalid. Config lists are normalised at boot without a budget,
with one `config_invalid_value` WARNING per list naming invalid entries.

Upgrade note: a configured threshold above 0.85 now loosens both fetch routes unless
the ceiling bounds it; below 0.85 tightens both. See the canonical configuration row
for the raw `/extract` boolean divergence and the cache re-key.

### `cache:` block

Validated at start by `cache.cache_settings_from_config()` **regardless of backend**; an out-of-range value raises `CacheConfigurationError` and the boot fails.

| Key | Code default | Shipped | Allowed range | Controls |
|---|---|---|---|---|
| `cache.max_entries` | `256` | `256` | 1 to 4096 | Entries held by `InMemoryStorage` (expired-first, then LRU eviction) |
| `cache.max_bytes` | `33554432` (32 MiB) | `33554432` | 1 MiB to 128 MiB | Serialised bytes held in memory; a single larger response is served uncached and counted in `cache.storage_oversize_skips` |
| `cache.max_value_bytes` | `4194304` (4 MiB) | `4194304` | 512 KiB to 8 MiB | Per-value UTF-8 bytes, including envelope, on both backends; one atomic bounded Valkey read. Above `cache.max_bytes` warns but boots. Keep equal across replicas; lowering it can reject old writes. See the canonical configuration reference for aggregate memory sizing. |

`VALKEY_URL` query keys `decode_responses`, `encoding`, `encoding_errors` and
`protocol` are forbidden at construction, with one key-only WARNING before boot
refusal. Remove them before upgrading; socket timeout options remain tunable.

### `extraction:` block

Validated at start by `pipeline.extraction_limits.extraction_settings_from_config()`; a non-integer, boolean, or out-of-range value raises `ExtractionConfigurationError` and the boot is refused. Shipped values equal the maxima, so these knobs can only tighten.

| Key | Code default = shipped | Allowed range | Controls |
|---|---|---|---|
| `max_input_bytes` | `52428800` (50 MiB) | 1 MiB to 50 MiB | Upload ceiling, enforced by streamed byte count |
| `max_pages` | `500` | 1 to 500 | PDF pages parsed before abandoning |
| `child_cpu_seconds` | `20` | 1 to 20 | CPU rlimit on the spawned pypdf worker |
| `child_address_space_bytes` | `402653184` (384 MiB) | 128 MiB to 512 MiB | Address-space rlimit on that worker (Linux only; skipped on macOS) |
| `wall_clock_seconds` | `90` | 1 to 90 | Whole-extraction wall budget |
| `max_promptguard_chunks` | `64` | 1 to 64 | Stage-3 chunk budget; derives the 114,688 classifiable-character ceiling |
| `extraction_concurrency` | `1` | 1 to 1 | Pinned; the memory envelope assumes one worker |
| `classification_concurrency` | `1` | 1 to 1 | Pinned, same reason |
| `admission_queue_depth` | `1` | 0 to 4 | Requests that may wait for the slot; `0` means immediate `busy` (429) |
| `max_queued_upload_bytes` | `52428800` (50 MiB) | 0 to 50 MiB | Bytes of queued uploads held in flight; `0` disables queuing |

---

## Where to Set Values

| Context | How | Notes |
|---|---|---|
| Local shell, `uv run` | `export` before `uv run pytest`, or before a bare `uv run uvicorn retrieval_app:app --host 127.0.0.1 --port 8020` | The bare-uvicorn run is inferred from the Dockerfile `CMD`, not a documented path; set a writable `HF_HOME` first, and `config.yaml` is read from the repo root. Under pytest, `conftest.py` clears the six variables listed above. |
| `docker run` | `--env-file ./forage.env`, plus `-v forage-model-cache:/app/model-cache` and optionally `-v "$PWD/config.yaml:/app/config.yaml:ro"` | Never an inline `-e` for a credential: shell history and `ps`. `docker inspect` shows the environment to anyone with socket access. `kit_tools/docs/LOCAL_DEV.md` has the full command. |
| `compose/minimal.yml`, `compose/full.yml` | `compose/.env` (gitignored), read automatically when you run from `compose/` | `.env` holds `HF_TOKEN` (optional) and `SEARXNG_SECRET` (required). Both fragments pass `HF_TOKEN` as a bare name so an absent host variable stays genuinely unset. `full.yml` alone sets the literal `VALKEY_URL=redis://valkey:6379/4`. `SEARXNG_URL` and `SEARXNG_LIMITER` are deliberately absent; `kit_tools/arch/INFRA_ARCH.md` covers the fragments. |
| CI (`.github/workflows/ci.yml`) | Nothing to set. `GITHUB_TOKEN` is the only credential; `smoke` runs the image with no env; `lint` runs `docker compose config` with a placeholder `SEARXNG_SECRET` | No runtime secret exists in CI, by design (`kit_tools/docs/CI_CD.md`). |
| Poppy's compose | Out of scope for this repo. Poppy's in-tree copy remains the deployed source of truth until it pins a published image; `POPPY_RETRIEVAL_LEGACY_CAPABILITY` survives for it | `CLAUDE.md`, "Coexistence with Poppy". |

### There are no build arguments

`Dockerfile` declares **zero** `ARG` instructions, and the only runtime `ENV` lines it sets are `PATH` and `HF_HOME`. A build argument is recorded in the image's layer history, where `docker history --no-trunc` reads it back from any registry the image reaches, so a token passed at build time is a published token. `tests/test_dockerfile.py::test_the_build_takes_no_arguments_at_all` asserts the absolute on every `uv run pytest`, and CI's `secret-grep` job greps the built image's layer history for `HF_TOKEN` and any `hf_`-prefixed token-shaped literal. Every credential enters at runtime through the container environment (`CLAUDE.md` invariant 2; `kit_tools/arch/SECURITY.md`, "No secret enters the image build").

---

## Secrets Handling

| Variable | Consumer | Kept out of logs by |
|---|---|---|
| `VALKEY_URL` | `cache.py` | Closed vocabulary `connect_failed` / `operation_failed` / `timeout` (`cache._closed_vocabulary_reason`); `tests/test_cache.py` asserts a password never appears. `docker-entrypoint.sh` prints nothing for the same reason. |
| `HF_TOKEN` | `model_fetcher.py` | Passed only as `snapshot_download(token=)`; failures reported as `http_401` / `timeout` / `io_failed` / `fetch_failed`, never the exception text. |
| `FORAGE_MIRROR_TOKEN` | `model_fetcher.py` via `oras` | Stdin only, never argv, disk, or log; outcomes `pull_failed` / `timeout` / `oras_missing`. |
| `SEARXNG_SECRET` | SearXNG container | Never read by Forage. |

Rules: `.env` and `compose/.env` are gitignored and must stay uncommitted (GitHub secret scanning with push protection is enabled on the repo). Rotation is "change the variable, restart the container": every secret is read once per start or per cold acquisition. Report a leak or a vulnerability through the repo-root `SECURITY.md` procedure (GitHub private vulnerability reporting; there is no email channel). Broader posture: `kit_tools/arch/SECURITY.md`, "Secrets Management".

---

## Traps and Gotchas

- **Empty `VALKEY_URL` is a configured Valkey, not memory mode.** `VALKEY_URL=` or a `${VALKEY_URL}` interpolation rendered against nothing selects the Valkey backend, which then fails loudly as `degraded: cache_unavailable`. Fully unset the variable to get memory mode. Check `/health.cache_backend`, not `cache_connected`.
- **`SEARXNG_URL` is read at import time** (`retrieval_app.py` line 82), not in the lifespan: a change needs a process restart, and a monkeypatch after import does nothing. Its **scheme, host and port** are echoed into the `searxng_unavailable` reason on the wire — userinfo is stripped since `search-provider-abstraction` US-002, and a value that will not parse at all echoes the literal token `unparseable-endpoint` rather than the raw string. Putting a credential in it is still a bad idea, but it no longer reaches a 422 body.
- **`SEARXNG_URL` is reached with `trust_env=False`.** The provider client ignores the ambient `HTTP_PROXY`, `HTTPS_PROXY`, `.netrc` and `SSL_CERT_FILE` (contract point 2 of the `SearchProvider` seam). A deployment that reached SearXNG *through* a proxy set in the container environment must point `SEARXNG_URL` at the reachable address instead.
- **`HF_HOME` defaults to `/app/model-cache`** from the Dockerfile `ENV` and from `model_fetcher.DEFAULT_CACHE_ROOT`. On a bare host that path does not exist; export a writable directory or the acquisition fails as `io_failed` (`kit_tools/docs/TROUBLESHOOTING.md`, "Bare-host run").
- **Secrets never enter the image build.** No `ARG`, ever, and no `ENV` with a secret-shaped name; both are tested. Poppy's `services/retrieval/Dockerfile` still carries `ARG HF_TOKEN` and must never be pushed.
- **`HF_HUB_OFFLINE=1` in the environment does nothing at runtime.** `huggingface_hub` samples it once at import; `model_fetcher._offline_hub()` flips the constant in-process for the load path only.
- **Break-glass arms on the exact string `1`.** `true`, `yes`, and `0` do not arm it. Both names are checked; the WARNING names whichever armed it.
- **`FORAGE_MODEL_REVISION` refuses branch names.** A movable `main` would turn the next upstream commit into "corruption"; only a 40-hex sha is accepted, and a bad value falls back to the pin with an ERROR that never echoes it.
- **`FORAGE_WEIGHTS_MIRROR` points at a private mirror.** Without `FORAGE_MIRROR_TOKEN` the leg is skipped; with a token that cannot read it, `pull_failed`. Third parties: leave both unset and use `HF_TOKEN`.
- **`config.yaml` validation refuses boot; a missing file does not.** A bad `cache.*` or `extraction.*` value fails the start with a `ValueError` subclass; an absent file logs one WARNING and runs on code defaults, which differ from the shipped file for `user_agents` and `news_domains`.
- **`/extract` reads the raw threshold.** A YAML boolean `true` becomes 1.0 there only; the fetch routes reject it at boot with a warning and use 0.85.
- **INFO logs are invisible.** Nothing calls `logging.basicConfig()`, so the weights success narrative never reaches `docker logs`; watch `/metrics` (`model.fetch_in_progress`, `model.retries_scheduled`) instead.
- **`SEARXNG_LIMITER=true` breaks `/search`.** The limiter 429s Forage's own client on the first request. `SEARXNG_VALKEY_URL` is not the content cache's Valkey; do not wire it to `full.yml`'s `valkey` service.
- **`docker inspect` prints the whole environment.** An env file keeps a password out of shell history and `ps`, not out of the Docker socket; restrict socket access accordingly.

---

## Adding a New Variable

1. Name it `FORAGE_*`. Never a name containing "poppy"; add a `POPPY_*` alias only if a deployed Poppy environment already carries one, and mark it deprecated.
2. Read it in exactly one place, through a named constant or a small resolver in the module that owns the behaviour (`model_fetcher.py` keeps its five names as `*_ENV_VAR` constants so `tests/test_model_fetcher.py` can assert the whole set). Prefer reading in the lifespan; read at import only if a restart-to-apply rule is acceptable.
3. Decide the unset behaviour explicitly and make it honest: a missing dependency degrades in the `/health` body (`CLAUDE.md` invariant 5); a bad safety limit refuses boot.
4. If it can carry a credential: never log it, never put it in an exception message or argv, extend the closed log vocabulary instead, and add an assertion beside `tests/test_cache.py`'s. Never make it a build `ARG` or an image `ENV`.
5. If tests must not see the host's value, add it to `_CLEARED_ENV_VARS` in `tests/conftest.py`.
6. Document it in `docs/configuration.md` (the canonical reference), then add its row here, and to `compose/*.yml` only if a deployment needs it (the fragments are shape-tested by `tests/test_compose_fragments.py`).
7. If it changes a response shape or a `/health` field, it is a contract change: read `contract/GOVERNANCE.md` before touching `pipeline/contract.py`.
