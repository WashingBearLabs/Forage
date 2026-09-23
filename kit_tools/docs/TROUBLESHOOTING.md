<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: operations, tech-stack
  required_sections:
    - "Debugging Tools"
  skip_if: never
-->
# TROUBLESHOOTING.md

> **TEMPLATE_INTENT:** Document debugging procedures and common fixes. How to diagnose problems.

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-resource-envelope US-003)

---

## Scope

This is the symptom-first runbook for a **running** Forage: startup, request, consumer,
and operational problems. It does not repeat what its neighbours own:

- **Setting up a checkout** (uv, pyright, ruff, torch wheels, port clashes, compose `.env`):
  [`LOCAL_DEV.md`](LOCAL_DEV.md) § "Troubleshooting Local Setup".
- **CI failures** (gate chain, publish parity, cold-cache recovery): [`CI_CD.md`](CI_CD.md)
  § "Failure Playbook" and § "Troubleshooting CI".
- **Field semantics** of `/health` and `/metrics`, and the log-search command table:
  [`MONITORING.md`](MONITORING.md).
- **Which dependency breaks which endpoint**: the failure impact matrix in
  [`../arch/SERVICE_MAP.md`](../arch/SERVICE_MAP.md).
- **The deeper why** behind each landmine, with measurements: [`GOTCHAS.md`](GOTCHAS.md).
- **Error shapes and logging conventions** as patterns:
  [`../arch/patterns/ERROR_HANDLING.md`](../arch/patterns/ERROR_HANDLING.md) and
  [`../arch/patterns/LOGGING.md`](../arch/patterns/LOGGING.md). Deploy and rollback:
  [`DEPLOYMENT.md`](DEPLOYMENT.md).

Canonical repo-root references used throughout: `docs/configuration.md`, `docs/weights.md`,
`docs/searxng.md`, `docs/releases.md`, `contract/GOVERNANCE.md`, `SECURITY.md`.

---

## Quick Diagnostics: the first five minutes

### policy_invalid_domain_entry

`retrieve.policy_invalid_domain_entry` rises once **per dropped entry**, not per
request. Two causes share it deliberately: malformed domain entries, and entries
past an allowlist's raw byte-budget boundary. Both kinds of allowlist drops only
narrow privilege, so one counter is enough; the denylist is never truncated.
Invalid entries include single-label allowlist names (`com`, `.com`), empty
labels, overlong names and names that cannot be UTS-46 encoded. Whitespace, case
and one trailing dot are normalised rather than treated as invalid.

For `/retrieve`, check all three lists and the raw UTF-8 size of each, including
the newline separators, against `policy_domain_entries_max_bytes` (default 65536).
`trusted_domains` and `verified_domains` keep only the prefix within that separate
per-list budget. `/search` counts **invalid denylist entries only**, once US-002
lands its domain policy; its counter is present but remains zero until then.
An over-budget denylist is a **422 with reason `policy_domain_list_too_large` on
either route**, never a count or partial enforcement: `/retrieve` uses
`content_too_large`, and `/search` declares `search_unavailable` for its coming
raiser. Retrying the same list without changing the budget cannot fix it.

The offending entry is neither stored nor logged: it is caller-controlled content
and could be a misplaced credential or URL. Inspect the consumer's configuration
at its source; do not turn on payload logging. Check spelling, list assembly,
unnecessary padding and the order of allowlist entries; there is no entry-count
cap. The byte limit bounds canonicalisation work **after JSON parsing**, not
request-body size. `retrieve.policy_suffix_trusted_skip` separately counts
wildcard-caused trusted **and verified** resolutions; review those suffixes if it
rises unexpectedly.

### Immediate checks

Forage is one FastAPI container with four dependencies (SearXNG, Valkey, the Hugging Face
Hub or the private weights mirror, and the open web). Every failure surfaces in one of
three places, and the order below is the order to read them.

**1. Read the `/health` body. Never the status code: it is always 200.**

```bash
curl -s localhost:8020/health | jq
```

| Field | Healthy reading | What a bad reading means |
|---|---|---|
| `status` | `"healthy"` | `"degraded"` iff `degraded_reasons` is non-empty |
| `degraded_reasons` | `[]` | Three values: `promptguard_unavailable` (classifier not loaded), `cache_unavailable` (configured Valkey unavailable), `cache_unauthenticated` (Valkey without signing; cached content lacks proof of origin and is served without re-sanitization). Both cache reasons can coexist; neither appears in memory mode |
| `promptguard_loaded` | `true` | `false`: no weights yet. Always honest, even with break-glass armed |
| `cache_connected` | `true` | `false` only with `cache_backend: "valkey"`; memory mode is always `true` |
| `cache_backend` | what you configured | `"memory"` in production means the env file was not mounted; `"valkey"` when you meant memory means `VALKEY_URL` is set (even to `""`) |
| `capabilities` | Presence map with up to three keys | `search_sanitization` when weights load (the only key break-glass can force), `brave_api_key` when a usable paid key was resolved, `cache_hmac_key` when a usable signing key was resolved on Valkey, even if disconnected. Missing keys are omitted, never zero; memory omits `cache_hmac_key` |
| `contract_version` | `"1.3.0"` | Consumers compare MAJOR (see Consumer-Side Problems) |
| `sanitizer_revision` | 64-hex sha256 | `"unknown"` only when no lifespan ran (test transports) |

**2. Read `/metrics`.** It is JSON, in-process, and resets on restart. The `model` section
is the only way to tell "downloading" from "waiting" from "wedged"; `/health` reports all
three identically.

```bash
curl -s localhost:8020/metrics | jq .model        # fetch_in_progress, retries_scheduled, fetch_failures, verify_failures, quarantines
curl -s localhost:8020/metrics | jq .cache        # reconnect_attempts/successes/failures, operation_failures
curl -s localhost:8020/metrics | jq .search       # errors.searxng_error / errors.searxng_unavailable / errors.search_unavailable, omitted_by_reason, unscanned_results
curl -s localhost:8020/metrics | jq .retrieve     # errors.<code>, promptguard_state.*, blocked_by_reason.*
curl -s localhost:8020/metrics | jq .extraction   # busy_rejections, active, queued, verdicts, oom_proximity_ratio
```

| `promptguard_loaded: false` and ... | State |
|---|---|
| `model.fetch_in_progress: true` | downloading or verifying right now |
| `model.retries_scheduled > 0`, `fetch_in_progress: false` | a previous attempt failed; waiting out the backoff (30 s doubling to 600 s, plus or minus 20 percent, forever) |
| both zero/false, still degraded | the first attempt has not finished (or the container just started) |

**3. Grep the logs for the fixed handles.** Nothing configures logging, so only
WARNING/ERROR lines from first-party code reach `docker logs`; the INFO success narrative
is dropped. The handles below are the only strings you need.

```bash
# Weights acquisition, per attempt (the ONE terminal line is weights_unavailable)
docker logs <container> 2>&1 | grep -E 'weights_unavailable|weights_fetch_failed|weights_fetch_skipped|weights_mirror_skipped|weights_retry_scheduled|manifest_model_unknown|weights_revision_unpinned'

# Verifier refusals, quarantines, unusable pin, load failure, crash
docker logs <container> 2>&1 | grep -E 'weights_verification_failed|weights_quarantined|weights_pin_unusable|weights_load_failed|weights_acquisition_crashed'

# Cache: closed failure tokens (the URL, value and password never appear)
docker logs <container> 2>&1 | grep -E 'Valkey connection failed for content cache|Content cache operation failed|Content cache not available at startup|cache_entry_corrupt|cache_integrity_reject|cache_hmac_key_|cache_bounds_inverted|valkey_url_option_forbidden'

# Per-request noise on a degraded container, and quarantines
docker logs <container> 2>&1 | grep -E 'PromptGuard unavailable|Content quarantined'

# Configuration warnings
docker logs <container> 2>&1 | grep -E 'break_glass_advertisement_active|config.yaml not found|model_revision_invalid|weights_mirror_invalid|manifest_model_unknown|weights_revision_unpinned'
```

**4. Match the request's error code against the vocabulary table** below. Every coded
body is `{"error", "reason", "request_id"}` (plus `sanitizer_revision` on `/extract`), and
each code has one fixed HTTP status. A 200 with `injection_detected: true` is not an
error; it is a quarantine (see Request Problems).

---

## Debugging Tools

| Tool | Command | What it tells you |
|---|---|---|
| Health body | `curl -s localhost:8020/health \| jq` | Degradation, backend selected, contract and revision |
| Metrics | `curl -s localhost:8020/metrics \| jq` | Counters per section; the fetch state machine |
| Served contract version | `curl -s localhost:8020/openapi.json \| jq -r .info.version` | Must equal `/health.contract_version` |
| Container logs | `docker logs <container> 2>&1 \| grep -E '<handle>'` | WARNING/ERROR lines only; see the grep table above |
| Container state | `docker inspect --format '{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}' <container>` | Crash loops, OOM kills, refused boots |
| Image identity | `docker inspect --format '{{.Config.Image}}' <container>` | The tag or digest actually running |
| In-image contract | `docker run --rm --entrypoint cat <image> /app/contract/openapi.yaml.sha256` | Compare with `git show <tag>:contract/openapi.yaml.sha256` |
| Secret-free check | `docker history --no-trunc <image> \| grep -Ei 'HF_TOKEN\|hf_[A-Za-z0-9]{20,}\|FORAGE_BRAVE_API_KEY\|FORAGE_CACHE_HMAC_KEY'` | Must print nothing (CLAUDE.md invariant 2); the same four patterns CI's `secret-grep` job greps, including the name-only `FORAGE_CACHE_HMAC_KEY` pattern |
| Contract smoke | `uv run python contract_smoke.py --base-url http://127.0.0.1:8020 [--expect-status {healthy,degraded}] [--image <ref>]` | Asserts the `/health` contract for the container you started: `--expect-status degraded` (the default, CI's) for a token-less, weights-free container; `--expect-status healthy` requires weights and an operational cache, plus a usable `FORAGE_CACHE_HMAC_KEY` in the runtime env file for Valkey at contract 1.3.0. The wait is status-aware, so raise `--timeout-seconds` for a cold weights fetch |
| SearXNG companion smoke | `uv run python searxng_smoke.py --image forage-searxng:ci [--live] [--keep]` | Secret, JSON envelope, budget, limiter phases |
| Contract drift | `uv run python -m scripts.export_contract --check` | Exit 1 if `contract/openapi.yaml` no longer matches the app |
| Reach a target from the container's network | `docker exec <container> curl -sI https://example.com/` | `curl` ships in the image; separates "Forage refused" from "network cannot reach" |

There is no request-ID search: `request_id` is minted per request and returned in every
body, but it is never written to a visible log line. Correlate by the uvicorn access line
timestamp instead (those `INFO: ... "POST /retrieve HTTP/1.1" 422` lines are uvicorn's
own logger and do appear; inferred from uvicorn's default config, not measured here).

---

## Where the Logs Are

| Where Forage runs | How to read | Notes |
|---|---|---|
| Any container | `docker logs <container> 2>&1` | stdout and stderr of PID 1; the entrypoint prints nothing by design |
| Compose | `cd compose && docker compose -f minimal.yml logs forage` (or `full.yml`) | `searxng` and `valkey` have their own service names |
| CI `smoke` job | Job output | On failure the job dumps `docker logs` and `docker inspect .State` |
| Bare host (untested path) | stderr of the `uvicorn` process | No documented bare-host run exists; see Startup Problems for `HF_HOME` |

Expect first-party lines as bare message text without timestamp or level prefix
(stdlib `lastResort` handler; inferred, medium confidence). No log driver, rotation, or
retention is configured anywhere in the repo; the Docker daemon defaults apply.

---

## Error-Code Reference

The complete wire vocabulary is the eighteen codes in `pipeline/contract.py`
(`ErrorCode`), verified against the source at seeding time. `content_too_large` is one
code emitted from three sites, `busy` is shared by `/extract` (429) and `/retrieve`
(422), and `extraction_failed` by `/extract` and `/retrieve` (both 422), which is why ten
`/extract` codes plus eight `/retrieve` codes plus three `/search` codes deduplicate to
eighteen.

| Code | HTTP | Route | What it means | First thing to check |
|---|---|---|---|---|
| `blocked_domain` | 422 | `/retrieve` | Canonical host matched the request's `blocked_domains` or `seed_blocklist`; multi-label entries include every subdomain | The two lists. This is an intended refusal; private names take precedence as `private_ip` |
| `busy` | 429 | `/extract` | Admission queue full: `admission_queue_depth` (shipped 1) or `max_queued_upload_bytes` exceeded. Body carries `sanitizer_revision` | `/metrics.extraction.busy_rejections`, `active`, `queued`. Extraction concurrency is pinned to 1; retry client-side |
| `busy` | 422 | `/retrieve` | Reason `admission_queue_full`: the `/retrieve` admission queue is full — `retrieve.admission_queue_depth` (shipped 4) or `retrieve.max_queued_fetch_bytes` (shipped 30 MiB, three queued requests) exceeded while the single fetch slot was held. No `sanitizer_revision` in the body | `/metrics` `retrieve.busy_rejections` and `retrieve.semaphore_saturation`; what `/retrieve` is being pointed at and how often. Raise the two queue knobs, not `fetch_concurrency` (pinned at 1) |
| `content_too_large` | 422 (`/retrieve`, `/extract`); 413 documented on `/extract` but **unreachable** | `/retrieve`: `Content-Length` or streamed body over 10 MiB, **or** — reason `promptguard_budget`, the fixed literal rather than prose — extracted text over the ceiling derived from `retrieve.max_promptguard_chunks` (no pre-check at the shipped default `0`; 458,752 characters once the coming default 256 lands, `contract/GOVERNANCE.md` ruling (g)). `/extract`: upload over `max_input_bytes` (50 MiB); the streaming refusal actually arrives as **400** `{"detail": "There was an error parsing the body"}`, the post-spool re-check as 422 | The target or upload size. The 400 is expected: `contract/GOVERNANCE.md` ruling (a2); fixing it is a MAJOR |
| `content_too_large_to_classify` | 422 | `/extract` | Extracted text exceeds `max_promptguard_chunks` (64 chunks, 114,688 classifiable characters) | Document length; the shipped value is the maximum, `config.yaml` can only tighten it |
| `extraction_failed` | 422 | `/extract` | The pypdf child failed or hit a bound: 20 s CPU, 384 MiB address space, 90 s wall clock, 500 pages | `/metrics.extraction.verdicts.extraction_failed`; page count; container memory headroom |
| `extraction_failed` | 422 | `/retrieve` | A fetched PDF, parsed in the same worker under the same bounds (`hardening-retrieve-parity` US-003). Reason `pdf_encrypted` or `pdf_no_text` (the document), `pdf_extraction_error` (corrupt parse, page limit, or the child killed by an rlimit or the wall clock), or `pdf_spool_error` — a **host** fault: the spool file under `<TMPDIR>/forage-spool-<uid>` could not be created or written, or that directory was refused. Fetched PDFs run under `extraction.max_promptguard_chunks`; fetched HTML under `retrieve.max_promptguard_chunks` — over it is `content_too_large` / `promptguard_budget`, not this code | For `pdf_spool_error`: the `retrieve_spool_error` WARNING, free space and permissions on `TMPDIR`, and whether the spool directory is a symlink, foreign-owned or not `0700` (remove it and restart; Forage never repairs it). Otherwise `/metrics` `retrieve.errors.extraction_failed` and the document itself |
| `fetch_error` | 422 | `/retrieve` | Anything the other codes do not name: transport or TLS failure, and **more than 5 redirects** (`TooManyRedirectsError` has no dedicated code). Reason is `Failed to fetch <url>: <exc>` | Read the reason: `Exceeded 5 redirects` versus a TLS/transport message. Reach the target from the container's network with `curl` |
| `fetch_timeout` | 422 | `/retrieve` | httpx timeout on a hop; 30 s per request (`DEFAULT_TIMEOUT` in `pipeline/stage5_url_audit.py`), no retry | Target latency from the container's network; retry is the client's job |
| `invalid_filename` | 422 | `/extract` | `filename` longer than 255 characters, or no basename left after path separators are stripped (empty, `.`, `..`) (`_sanitize_upload_metadata`) | The client's form field |
| `invalid_mime_hint` | 422 | `/extract` | `mime_hint` longer than 255 characters; the hint is advisory only | The client's form field |
| `invalid_request_id` | 422 | `/extract` | `request_id` longer than 128 characters or not matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` | The client's form field |
| `invalid_url` | 422 | `/retrieve` | Scheme not http/https, no hostname, or DNS failure (`socket.gaierror`); reason echoes the URL or hostname | DNS from inside the container's network; the scheme |
| `pdf_encrypted` | 422 | `/extract` | The PDF is encrypted | Nothing server-side; the fixed reason says so |
| `pdf_no_text` | 422 | `/extract` | No extractable text; OCR is not supported | A scanned document; nothing server-side |
| `private_ip` | 422 | `/retrieve` | Hostname is `localhost` or ends in `.local` or `.localhost`, or **any** resolved address is private, loopback, link-local, CGN, multicast or reserved, or is an IPv6 literal embedding a private IPv4 — IPv4-mapped, 6to4 (`2002::/16`), Teredo (the client field), NAT64 (only inside `64:ff9b::/96`) or IPv4-compatible (only inside `::/96`) (`_PRIVATE_NETWORKS_*` and `private_address_class` in `url_validator.py`). The `.localhost` suffix and the four embedded classes are **newly refused** since `hardening-search-sanitization` US-003 (`contract/GOVERNANCE.md` ruling (f)); a public embedding such as `2002:808:808::` and ordinary public IPv6 such as `2a00:1450:4001:80e::200e` stay fetchable, which is what the prefix guards are for. Re-checked on every redirect hop. Reason echoes the resolved IP (GOVERNANCE ruling d) | Intended. A public name resolving privately from inside the container is split-horizon DNS or a rebinding attempt |
| `search_unavailable` | 422 | `/search` | Two reason forms. **Provider failure:** every provider in the configured chain failed and the chain is **not** a lone `searxng` — the chain is tried in order (free-first), and the reason is every provider's `<provider_name>: <failure_class>` entry, in chain order, joined by `"; "` — e.g. `searxng: rate_limited; brave: timeout` — never a URL and never exception text. A single non-`searxng` provider's chain has exactly one entry (e.g. `brave: auth`). Added in contract `1.2.0`, chain traversal in `search-fallback` US-001. **Policy-excluded:** the fixed literal reason `policy_excluded_all_providers` — the request's `providers` and/or `allow_paid_fallback` narrowed the effective chain to empty before any provider was called; no provider was tried, so there is no per-provider entry (`search-policy-and-health` US-010) | Which providers `FORAGE_SEARCH_PROVIDERS` names, and in what order — the first entry is the one that was tried first. Each `failure_class` is the diagnosis for that provider: `auth` is the provider account, `rate_limited`/`timeout` is the provider, `hard_error` is Forage's log. For `policy_excluded_all_providers`, nothing failed: the configured chain has no free provider (`FORAGE_SEARCH_PROVIDERS` names only paid ones), and the request either sent `allow_paid_fallback: false` or a non-empty `providers` list naming none of them — compare it against `/health` `search_providers`. Add a free provider to the chain, or fix the consumer's policy |
| `brave: auth` | 422 | `/search` | Brave rejected the key on the LLM-Context call — wrong, revoked, or not entitled (`401`/`403`) | Replace `FORAGE_BRAVE_API_KEY` and **restart the container** — `BraveApiProvider` reads the key once, at process start |
| `brave: rate_limited` | 422 | `/search` | Brave answered `429` — a per-second limit and plan exhaustion share the same status | Retry after a pause; if it persists, check the Brave account's plan, since a transient limit and an exhausted quota look identical here |
| `brave: timeout` | 422 | `/search` | The LLM-Context call exceeded `search_brave_timeout_seconds` (default 15 s) | Raise `search_brave_timeout_seconds` in `config.yaml`, or check egress latency to `api.search.brave.com` |
| `brave: hard_error` | 422 | `/search` | Everything else — a non-2xx status, a redirect, an oversized or unparseable body, or an unexpected exception — collapsed to one class; the diagnosis is in the `brave_search_failed` log line's `detail` token | Grep the `brave_search_failed` line for `detail`: `transport_error` → egress/DNS/proxy, `redirect_refused` → the endpoint moved, `bad_json`/`malformed_body` → capture a fresh sample, `unsupported_encoding` → this build cannot decode the proxy's encoding (gzip and deflate are supported), `body_too_large` → the response bound, `http_4xx` → a 4xx other than 401/403/429, so the request shape or endpoint changed (capture a fresh sample), `http_5xx` → Brave-side, `unexpected` → file a bug report |
| `searxng_error` | 422 | `/search` | SearXNG answered non-2xx; reason `SearXNG returned HTTP error (http_<n>)`. A **429** here means the SearXNG limiter is on | `SEARXNG_LIMITER` must stay unset. Persistent 4xx/5xx with the limiter off is engine rot: bump the digest pin (`docs/searxng.md`) |
| `searxng_unavailable` | 422 | `/search` | Connection refused, DNS failure, configured wall-clock timeout, an oversized body, or an unparseable envelope; reason `SearXNG not reachable at <scheme>://<host>:<port>: <detail>`, where `detail` is one of the closed tokens `timeout`, `connect_error`, `body_too_large`, `bad_json`, `malformed_body`, `unsupported_encoding`, `unexpected` — no exception text, and no userinfo from `SEARXNG_URL` | Is the `searxng` container up? It exits 1 without `SEARXNG_SECRET`. Raise `search_searxng_timeout_seconds` for a previously working slow instance and read `search.provider_timeouts`; `unsupported_encoding` means this build cannot decode the reply. `SEARXNG_URL` is read at import time: restart Forage after changing it |
| `unsupported_format` | 422 | `/extract` | Upload is neither `%PDF-` nor valid UTF-8 text: empty, NUL bytes, invalid UTF-8, or no visible text (`pipeline/stage1_upload.py`) | The bytes, not the `mime_hint`; magic bytes decide |

Bodies without an error code, all on `/extract` unless noted:

| HTTP | Body | Cause |
|---|---|---|
| 400 | `{"detail": "There was an error parsing the body"}` | FastAPI's multipart parser; also what an oversized upload actually receives (see `content_too_large`) |
| 404 | `{"detail": "Not Found"}` | `extract_route_enabled: false` (the shipped default) |
| 503 | `{"detail": "Unavailable"}` | Admission state missing because the lifespan did not run (test transports, not a deployed container) |
| 422 | `{"detail": [ ... ]}` (any JSON route) | FastAPI schema validation of the request body, not a pipeline refusal |

`/health` and `/metrics` never emit error bodies. A `/metrics` **500** means a counter was
added to a handler dict without being added to its `extra="forbid"` model.

---

## Startup Problems

The lifespan order matters for reading these: load `config.yaml` → validate `extraction:`
(bad value refuses boot) → derive `sanitizer_revision` → validate `cache:` (bad value
refuses boot, whichever backend) → select and connect the cache (2 s deadline, no
fallback) → start the weights task → serve. Only configuration validation can stop the
boot; everything else is reported through `/health` and `/metrics`.

### The container exits at start

**Symptom:** `docker inspect` shows `exited`, no `/health` at all, a Python traceback in
`docker logs` ending in `ExtractionConfigurationError` or `CacheConfigurationError`.

**Cause:** a value in the mounted `config.yaml`'s `extraction:` or `cache:` block is
outside its range (ranges in `docs/configuration.md`). A *missing* file is not this: it
logs `config.yaml not found at /app/config.yaml` and runs on code defaults.

**Fix:** correct the mounted file and restart. This is the only dependency whose failure is
a refused boot rather than a degraded service.

---

### config_unknown_key

**Symptom:** boot logs `config_unknown_key — key=<dotted.name>` at WARNING.
The service starts normally, but the misspelled setting has no effect.

**Cause:** the key is not registered. Known `cache:`, `extraction:` and `retrieve:`
blocks are checked one level deep; an unknown block is named once without
walking its children. No value appears in this warning.

**Fix:** compare the key and indentation against `docs/configuration.md`, correct
the mounted file and restart. Unknown keys are ignored, not translated to similar
names. This warning does not replace value validation: invalid known bounds still
refuse boot with their reader's typed error. `config_invalid_value` instead names
the warn-and-fall-back cases: the fetch-route threshold, domain-list byte budget,
or invalid operator domain entries.

---

### Weights never arrive: no token

**Symptom:** `status: "degraded"`, `degraded_reasons: ["promptguard_unavailable"]`,
`promptguard_loaded: false`, `capabilities: {}`. `/metrics.model.fetch_failures` is 1 and
`retries_scheduled` climbs. Log, in order: `weights_fetch_skipped — no HF_TOKEN in the
environment`, `weights_mirror_skipped`, then the terminal
`weights_unavailable — ... Attempts: huggingface=skipped_no_token, mirror=skipped_no_token`,
then `weights_retry_scheduled` every attempt.

**Cause:** the image is weights-free by design and the gated repository needs a token.
This is a supported mode, not a broken deploy, but content is unscanned (see Request
Problems, "Content unscanned").

**Fix:** a Hugging Face account with the Meta license accepted on
`meta-llama/Llama-Prompt-Guard-2-22M` and a fine-grained read token, supplied as `HF_TOKEN`
through the env file (`docker run --env-file`, or `compose/.env`). The retry loop converges
**in place**, no restart; expect about 270 MiB and 19 s cold on the reference envelope (1 vCPU / 1 GB),
configurable via `FORAGE_CPUS` / `FORAGE_MEM_LIMIT` — see `docs/configuration.md` § Sizing the container. The
token-less container keeps logging the ERROR at the 10-minute ceiling forever; that is
deliberate. Never "fix" this with `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION`: it makes
only `capabilities` lie and logs `break_glass_advertisement_active` every boot.

---

### Weights never arrive: token present but refused, or revision gone

**Symptom:** `weights_fetch_failed — source=huggingface revision=<sha> reason=http_401`
(or `http_403`, `http_404`), then `weights_unavailable`.

**Cause:** `401`/`403`: the token lacks gated-repo access or was revoked. `404`: the pinned
revision in the selected model's committed manifest entry no longer exists upstream.
`timeout` / `io_failed` / `fetch_failed` are the Hub or the disk, not the token.

**Fix:** confirm the Meta license approval and rotate the token (no restart needed; the
next retry picks it up). For `404`, check the selected model's
`weights_manifest.json` → `models[model_id].revision`; a moved upstream means the
re-vendoring procedure in `docs/weights.md`. `FORAGE_MODEL_REVISION` uses the selected
model's committed pin; a malformed value falls back to the pin with
`model_revision_invalid`; a well-formed value that is not that pin refuses to verify
(`weights_revision_unpinned`). That last refusal precedes all sources, so it cannot
cause a Hub 404. The override value is deliberately not echoed.

---

### Weights arrive but are refused by the verifier

**Symptom:** `/metrics.model.verify_failures` and `quarantines` each +1
(`fetch_failures` unchanged). Log: `weights_verification_failed — refusing to load;
reasons: file_extra` (or `hash_mismatch`, `size_mismatch`, `file_missing`,
`disallowed_format`, `snapshot_missing`, `symlink_escape`, `disallowed_entry`, ...) then
`weights_quarantined — refused weight set moved to <HF_HOME>/quarantine/...`.

**Cause:** the downloaded set does not match its per-model entry in
`weights_manifest.json` exactly (22M still has five files, sha256 and size each,
safetensors only). `file_extra` typically means a plain
`snapshot_download` without `ALLOW_PATTERNS` pulled README or `.gitattributes`.

**Fix:** check `FORAGE_MODEL_REVISION` against `models["<selected model id>"].revision`
in `weights_manifest.json`. `weights_revision_unpinned` refuses an override before
any snapshot lookup, even with a warm cache; it neither fetches nor quarantines.
`manifest_model_unknown` means that model has no entry; another entry is never used.
Do not
widen `ALLOWED_SUFFIXES`. If upstream mutated the pinned revision, re-vendor
(`docs/weights.md` § "Re-vendoring", `uv run python -m scripts.vendor_weights`). Reasons
starting `manifest_` (`manifest_empty`, `manifest_invalid`, ...) or the line
`weights_pin_unusable` mean the *image's* manifest is wrong: no download can fix that,
nothing is fetched or quarantined, rebuild from the corrected manifest and restart.
Manifest entries and failures are memoised for the service process lifetime.

---

### Weights verified but `promptguard_loaded` stays `false`

**Symptom:** WARNING `PromptGuard model not available — ML injection detection disabled`
with a traceback, then ERROR `weights_load_failed — the verified set at revision=<sha> did
not load`. Per-request WARNINGs `PromptGuard unavailable — fail-closed for standard tier`
follow.

**Cause:** torch or transformers failed to load a set that hashed correctly: an image
problem or memory pressure during load (torch imports inside the acquisition thread).

**Fix:** rebuild the image from `main`; check `mem_limit` (the compose fragments give
1024m) and `docker inspect` for `OOMKilled`. `weights_acquisition_crashed` with a traceback
is the catch-all for anything else in the acquisition thread; the retry loop re-arms after
it too.

---

### Mirror fallback problems (WashingBearLabs deployments only)

**Symptom:** `weights_fetch_failed — source=mirror ... reason=oras_missing` (or
`insufficient_space`, `pull_failed exit=N`, `timeout`, `no_artifact`, `artifact_oversized`,
`extract_failed`, `install_failed`), or `weights_mirror_invalid` with the reference
redacted to `***@host/...`.

**Cause:** `FORAGE_WEIGHTS_MIRROR` must be a lower-case `<registry>/<owner>/<name>` with no
scheme other than `https://`, no credentials, no tag (the tag is always the revision).
`insufficient_space` needs roughly 2.2x the manifest bytes free on the `HF_HOME` volume;
`oras` pull has an 1800 s timeout.

**Fix:** free space on the volume; check `FORAGE_MIRROR_TOKEN` has read scope. The mirror
`ghcr.io/washingbearlabs/forage-weights` is private, so third parties should leave both
mirror variables unset; the Hub leg is theirs.

---

### Every start is a cold download

**Symptom:** each `docker run` fetches about 270 MiB again; `fetch_in_progress: true` for
the first minutes after every recreate.

**Cause:** no volume is mounted at `HF_HOME` (`/app/model-cache`), so the cache lives in the
container's writable layer. Not an error.

**Fix:** `-v forage-model-cache:/app/model-cache`, as both compose fragments do. Note
`docker compose down -v` deletes the weights. A warm start loads with zero network and does
not even read the token.

---

### Bare-host run: `HF_HOME` unwritable

**Symptom:** on a dev box, `promptguard_loaded` never flips and the log shows either
`weights_fetch_failed — source=huggingface ... reason=io_failed` or
`weights_acquisition_crashed` with an `OSError` traceback naming `/app/model-cache`.

**Cause:** the code default `DEFAULT_CACHE_ROOT` is `/app/model-cache`, which does not exist
outside the image. The exploration did not trace this path end to end; the two lines above
are inferred from the `OSError` mapping in the Hub leg and the catch-all in
`acquire_and_load`, so treat them as the likely shapes rather than measured ones.

**Fix:** export a writable `HF_HOME` before starting uvicorn. A bare-host run is itself
undocumented (every doc runs the container); prefer `docker build -t forage . && docker run`.

---

### `cache_entry_corrupt`: invalid cached content was dropped

**Symptom:** WARNING `Content cache entry rejected (cache_entry_corrupt) key=ret:<sha256>`
and a rising `/metrics.cache.corrupt_entries`. `/retrieve` treats invalid JSON, schema
mismatches and invalid timestamps as misses, deletes the key, and fetches again instead
of answering 500. Only the fixed token and one-way key digest are logged.

**Cause:** schema drift or an external writer storing invalid values. Repeated
`cache_entry_corrupt` WARNINGs for one key digest after successful deletion mean an
external writer is repopulating it. If `operation_failures` also rises, check the
closed operation-failure WARNING first: deletion itself may be failing.

**Fix:** identify and stop the external writer or correct its schema; restrict access
to the cache. A following successful fetch repopulates the key. This counter measures
parse failures, not tampering: parse success is not authenticity. Signing now verifies
before parsing; `/metrics.cache.integrity_rejects` and the `cache_integrity_reject`
reason/digest are the stronger signal with a key set. A compromised key can still
forge values with no counter movement; see [Monitoring](MONITORING.md#reading-cacheintegrity_rejects).

---

### `cache_unavailable`: Valkey configured but unreachable

**Symptom:** `status: "degraded"`, `cache_unavailable` in `degraded_reasons`,
`cache_backend: "valkey"`, `cache_connected: false`. `/metrics.cache.reconnect_attempts`
and `reconnect_failures` climb. Log: `Valkey connection failed for content cache
(connect_failed)` (or `(timeout)`) and, at boot, `Content cache not available at startup`.

**Cause:** `VALKEY_URL` names a host that is down, unreachable from the container network,
or mis-spelled. The connect deadline is 2 s; there is **no fallback to memory**.

**Fix:** fix the URL or the network. Recovery is automatic and in place: reconnects follow a
1 s doubling to 30 s backoff, driven by traffic and by `/health` polls (`ping_if_due`), so
even a zero-traffic container notices when Valkey returns; `reconnect_successes` +1 and
that reason clears (`healthy` only if no other reason remains). Changing `VALKEY_URL`
itself needs a restart. `/retrieve` keeps working
throughout, served uncached. The URL and its password never appear in any log line;
`tests/test_cache.py` asserts it.

---

### `cache_unauthenticated`: Valkey configured without a signing key

**Symptom:** `/health` remains HTTP 200 with `status: "degraded"`,
`cache_backend: "valkey"` and `cache_unauthenticated` in `degraded_reasons`, even
with `cache_connected: true`. `capabilities.cache_hmac_key` is absent and startup
logs `cache_hmac_key_missing`. An unreachable cache adds `cache_unavailable`;
loading PromptGuard or reconnecting Valkey does not clear the signing reason.

**Cause:** no usable `FORAGE_CACHE_HMAC_KEY` was configured at boot. Cached
`/retrieve` content cannot be proven to be Forage's own and is served without
re-sanitization. On shared Valkey this is an **open cache-poisoning path**, not a
harmless performance signal.

**Fix:** follow the [CSPRNG recipe](../../docs/configuration.md#credential-handling-for-forage_cache_hmac_key)
into `compose/.env`; `compose/full.yml` passes the key to Forage. Stop every
replica, set the same key everywhere, then start. Do not roll through a mixed
keyed/keyless fleet: the replicas delete each other's entries. Memory mode needs
no key; `cache_hmac_key_unused` there is a warning about an unnecessary secret,
not a degraded reason. Read [the integrity runbook](MONITORING.md#reading-cacheintegrity_rejects)
for a rising counter: an image upgrade produces a cold cache, not an unsigned
migration burst; a key-only enable on the same code can produce a bounded burst.

---

### The container exits at start: `FORAGE_CACHE_HMAC_KEY` refused

**Symptom:** uvicorn exits during startup with `CacheConfigurationError`; no
`/health` endpoint becomes available. Check stderr or the stopped container's log:

```bash
docker logs <container> 2>&1 | grep -E 'cache_hmac_key_too_short|cache_hmac_key_invalid'
```

**Cause:** `cache_hmac_key_too_short` means fewer than **32 UTF-8 bytes** after
stripping leading/trailing space, tab and LF. `cache_hmac_key_invalid` means a
remaining character is not printable ASCII without whitespace or controls.
Interior spaces/tabs/LF, CR (even trailing), non-ASCII and other controls refuse
boot. Blank after the permitted strip means absent, not a refusal; on Valkey it
takes the degraded path above. Validation also runs in memory mode.

**Fix:** replace the assignment with a fresh CSPRNG-generated value using the
[private env-file recipe](../../docs/configuration.md#credential-handling-for-forage_cache_hmac_key),
with all replicas stopped, then start them with the same key. A passphrase of
the right length may pass validation but is not acceptable. The printable
base64 text is used as UTF-8 bytes, **never decoded** by Forage. The diagnostic
names the variable, never its value; do not print the key or paste it into a
command line to debug it.

---

### `cache_unavailable` when you meant memory mode

**Symptom:** as above, but no Valkey exists anywhere in the deployment.

**Cause:** `VALKEY_URL` is set to the **empty string**: an unfilled `${VALKEY_URL}` in a
compose file or a blank `VALKEY_URL=` line in an env file. Empty is "configured and broken",
never memory mode; the backend is chosen by the variable's presence, not its reachability.

**Fix:** remove the variable entirely and recreate the container. `compose/minimal.yml`
deliberately does not mention it. The inverse mistake, `cache_backend: "memory"` in a
deployment that runs Valkey, means the env file was not mounted; look at `cache_backend`,
not `cache_connected`.

---

### Valkey drops mid-run

**Symptom:** `/retrieve` still answers 200 with `cache_hit: false`; log `Content cache
operation failed (operation_failed)` (or `(timeout)`); `/metrics.cache.operation_failures`
+1; `/health` flips to `cache_unavailable` until the reconnect succeeds.

**Cause:** a `get`/`set`/`delete` raised; the client is cleared and the backoff starts. A
cache outage never fails a request.

**Fix:** nothing on Forage's side; watch `reconnect_successes`.

---

### Cache misses everywhere after an upgrade

**Symptom:** `retrieve.cache_misses` jumps, `cache_hits` stays flat, `/health` shows a
different `sanitizer_revision` than before the deploy.

**Cause:** expected, not a bug. `sanitizer_revision` hashes eight `pipeline/*.py` files, the
model identity and `promptguard_threshold`, and it is part of the content-cache key
fingerprint, so a rotation invalidates every existing entry on purpose. Fourteen rotations
are recorded in `docs/bootstrap-notes.md` (`e6b2b56d` → ... → `41ac98ca`); that file, not
this count, is the record. Any consumer cache keyed on the revision must flush too.

**Fix:** none needed. If the rotation surprised you, the bump was made as a drive-by inside
a behavioural change instead of at a boundary; record it in `docs/bootstrap-notes.md`.

---

## Request Problems

### `/retrieve` refuses the URL

**Symptom:** 422 with `invalid_url`, `private_ip`, or `blocked_domain`.

**Cause:** `url_validator.validate_url` runs before any byte is fetched and again on
**every redirect hop**. `invalid_url` is scheme, hostname, or DNS; `private_ip` is
`localhost`, `.local`, or any resolved address in a private range; `blocked_domain` is an
apex or dot-boundary subdomain match against the request's `blocked_domains` merged
with `seed_blocklist`. Denylist `evil.com` covers `www.evil.com`, never `notevil.com`;
allowlist `example.com` matches only itself and `.example.com` adds all subdomains.
IP literals and single-label denylist entries match only themselves, while single-label
allowlists are invalid. Canonical private names are rejected before caller lists.

**Fix:** these are the service working as designed. If a public site trips `private_ip`,
resolve it from inside the container (`docker exec <container> curl -sI <url>`): a split
DNS horizon returns private addresses, and so does a rebinding attempt. The reason string
echoes the resolved IP, which is a documented caveat (GOVERNANCE ruling d) and a reason to
keep Forage on a private network.

---

### DNS rebinding is being caught, or a redirect chain dies

**Symptom:** a request that works from a browser gets `private_ip` part-way through a
redirect chain, or `fetch_error` with reason `Failed to fetch <url>: Exceeded 5 redirects
for URL: ...`.

**Cause:** stage 5 follows redirects manually (`follow_redirects=False`), re-validates each
hop, and connects to the **validated IP** with `Host: <hostname>` and SNI set to the
hostname, so a second resolution cannot swap in a private address. More than 5 hops
(`DEFAULT_MAX_REDIRECTS`) raises `TooManyRedirectsError`, which collapses into the generic
`fetch_error` code; there is no dedicated redirect code.

**Fix:** none on Forage's side. Ask for the final URL directly if the chain is long;
`redirect_chain` and `domain_changed_on_redirect` (a -0.1 trust penalty) in a successful
response show what was followed.

---

### `/retrieve` times out or the response is too large

**Symptom:** 422 `fetch_timeout` after about 30 s, or `content_too_large` with a reason
naming the `Content-Length` or the streamed body.

**Cause:** per-request timeout 30 s; body cap 10 MiB (`DEFAULT_MAX_CONTENT_BYTES`), enforced
by `Content-Length` fast-reject and again while streaming so a lying server cannot OOM the
service. Neither is configurable in `config.yaml`, and there are no retries.

**Fix:** retry client-side; for large documents, download them yourself and use `/extract`.

---

### `/search` fails: `searxng_error` versus `searxng_unavailable`

**Symptom:** 422 with one of the two codes. `/health` stays `healthy`: SearXNG is **not
probed**. Read `search.errors.searxng_error` / `search.errors.searxng_unavailable`,
`search.provider_timeouts` and `search.provider_compressed_body` in `/metrics`,
the response itself and the `search_provider_failed` WARNING.

**Cause:** `searxng_unavailable` is transport: refused, DNS, the configured
`search_searxng_timeout_seconds` wall-clock budget, or an oversized, undecodable or
unparseable envelope. A previously working slow instance may need a larger budget
after upgrading: connect, headers and streaming now share it.
`searxng_error` is an HTTP status from SearXNG; **429 means the
SearXNG limiter is on**, which blocks Forage's own httpx client on the first request (no
`Accept-Language`) and caps JSON formats at 4 requests per hour regardless.

**Fix:** for `searxng_unavailable`, confirm the companion is running (it exits 1 without
`SEARXNG_SECRET`) and that `SEARXNG_URL` (default `http://searxng:8080`) is right. The
variable is read at **import time** in `retrieval_app.py`, so a change needs a Forage
restart. For `searxng_error`, keep `SEARXNG_LIMITER` unset; if 4xx/5xx persist with the
limiter off, the engine fingerprints have rotted and the digest pin needs bumping
(`docs/searxng.md` § "Bumping the pin"). No result cache exists; the next `/search` works
as soon as SearXNG does.

---

### `/extract` returns 404, 503, or 429

**Symptom:** `{"detail": "Not Found"}`, `{"detail": "Unavailable"}`, or
`{"error": "busy", ...}` with `sanitizer_revision`.

**Cause:** 404: `extract_route_enabled` is `false` (shipped default), enforced by
`ExtractionAdmissionMiddleware`. 503: the admission controller was never wired because the
lifespan did not run; you will see this from a test transport, not a deployed container.
429: the admission queue is full (`admission_queue_depth` 1, `max_queued_upload_bytes`
50 MiB), counted in `extraction.busy_rejections`; `semaphore_saturation` counts every time
all slots were busy, queued or rejected, so it is always the larger number.

**Fix:** 404: set `extract_route_enabled: true` in the mounted `config.yaml` and restart.
429: extraction is one-at-a-time by design; queue client-side.

---

### An oversized upload gets 400, not 413

**Symptom:** a multipart body over 50 MiB receives 400 `{"detail": "There was an error
parsing the body"}` although the OpenAPI document lists a 413 `content_too_large`.

**Cause:** `DocumentSizeLimitMiddleware` refuses by raising out of the ASGI `receive`
callable, and FastAPI's form parser wraps that into its own 400. A JSON body over the limit
gets 422. Documenting the shadowed 413 carried no bump; correcting it moves a status code a
client observes and is a **MAJOR** (`contract/GOVERNANCE.md` ruling a2).

**Fix:** treat 400 on `/extract` as "too large or unparseable" until the next major
contract. Do not patch it in a minor.

---

### `/extract` returns a document-failure code

**Symptom:** 422 with `extraction_failed`, `content_too_large_to_classify`, `pdf_encrypted`,
`pdf_no_text`, or `unsupported_format`; reasons are fixed content-free strings from
`DOCUMENT_FAILURE_REASONS` in `pipeline/orchestrator.py`.

**Cause:** the pypdf child runs under 20 s CPU, 384 MiB address space (Linux only; macOS
relies on parent supervision), 90 s wall clock and 500 pages, and any breach is
`extraction_failed`. Text over 114,688 classifiable characters is
`content_too_large_to_classify`. Format is decided by magic bytes (`%PDF-`) and strict
UTF-8, never by `mime_hint`.

**Fix:** check `/metrics.extraction.verdicts` for the distribution. The shipped
`extraction.*` limits are already the maxima except three raisable keys:
`classification_concurrency` (1–8 under the memory rule and boot WARNING),
`child_address_space_bytes` (128–512 MiB, widens the untrusted-PDF sandbox), and
`admission_queue_depth` (0–4). See `docs/configuration.md` § Sizing the container
before raising a bound.

---

### Content came back quarantined

**Symptom:** HTTP 200, `injection_detected: true`, `body` replaced by the stable string
`Content quarantined due to potential prompt injection.`, `injection_spans` holding exactly
one diagnostic (`structural_injection_detected`, `promptguard_injection_detected`, or
`promptguard_unavailable`), `promptguard_state` one of `structural_blocked`,
`scanned`, or `unavailable_blocked`, and `trust_score` reduced (a stage-3 detection
subtracts 0.5; a stage-2 block zeroes it). Log:
WARNING `Content quarantined for <url> — returning content-free response`.

**Cause:** stage 2 (structural rules) returned BLOCKED or stage 3 (PromptGuard) returned
INJECTION_DETECTED. This is not an error; Forage reports signals and never ships hostile
text. Quarantined results are never cached.

**Fix:** if the diagnostic is `promptguard_unavailable`, read the next entry. Otherwise
`structural_flags` names the rule category; the threshold is `promptguard_threshold`
(`config.yaml`, shipped 0.85, part of `sanitizer_revision`). Tally by
`retrieve.blocked_by_reason.*` in `/metrics`.

---

### Content unscanned because the model is absent

**Symptom:** with `promptguard_loaded: false`, `/retrieve` on `standard`/`untrusted`
domains returns the quarantine shape with `injection_spans: ["promptguard_unavailable"]`
and `promptguard_state: "unavailable_blocked"`; `/search` withholds results under
`omitted_by_reason.promptguard_unavailable`; `/extract` quarantines **every** upload.
Log, one WARNING per request: `PromptGuard unavailable — fail-closed for standard tier`.

**Cause:** `promptguard_fail_closed` defaults to `true`, so an absent classifier blocks
rather than passes. `trusted` domains skip the scan (`skipped_trusted`); `verified`
domains, or a request with `promptguard_fail_closed: false`, get the content back as
`unavailable_allowed` with a -0.1 penalty; on `/search` that shape is `unscanned_results >
0` with `promptguard_unavailable: true`.

**Fix:** supply the weights (Startup Problems above). To the consumer this looks like "no
matches", which is exactly the nine-day silent failure the honest-health contract exists to
prevent; `retrieve.promptguard_state.unavailable_blocked` rising is the signal to alarm
on. Never resolve it by turning fail-closed off.

**Not the only cause, since `hardening-retrieve-parity` US-006.** The two `unavailable_*`
states now have a second, entirely different cause: the classifier is loaded and *busy*,
and the request's wait for the single classification permit expired. Tell them apart in
two places rather than guessing:

| | Model absent | Permit contention |
|---|---|---|
| `/health` `promptguard_loaded` | `false` | `true` |
| `/metrics` `retrieve.classification_wait_timeouts` / `search.classification_wait_timeouts` | stays `0` | rises, once per request |
| Log line | `PromptGuard unavailable — fail-closed for <tier> tier` | `classification_wait_timeout route=<retrieve\|search>` |

The two log lines are deliberately disjoint: the timeout never emits the "PromptGuard
unavailable" text, because that text would send you to the model loader for a problem the
model loader cannot fix. For contention the fix is the sizing rule in
`docs/configuration.md` — lower `retrieve.max_promptguard_chunks` to bound the worst-case
permit hold, or raise `promptguard_wait_seconds` — and note that a `/search` request spends
one budget for the whole request, so a single `classification_wait_timeouts` increment can
account for many unscanned results. `/extract` shares the same permit but waits without a
deadline and increments nothing, so a hanging `/extract` under `/retrieve` load shows up
only as latency.

---

## Consumer-Side Problems

### `contract_version` mismatch

**Symptom:** the consumer (Poppy) refuses to activate, or hard-rejects an `/extract` 422
body.

**Cause:** the wire contract is semver-versioned independently of the image tag
(`CONTRACT_VERSION` in `pipeline/contract.py`, served as `/health.contract_version` and
`/openapi.json` `info.version`). Consumers compare **MAJOR** and are expected to refuse on
a mismatch rather than guess; MINOR is additive (`1.1.0` added `cache_backend`; `1.2.0`
added `SearchResult.content_kind` and `SearchResult.date`, plus the `search_unavailable`
code; `1.3.0` declared `blocked_url` and bounded `SearchResult.engine` to 64 characters).
Poppy also
rejects an `/extract` 422 lacking `sanitizer_revision`, and pins the ten `/extract` codes as
`_SIDECAR_EXTRACT_FAILURE_CODES`.

**Fix:** read `contract/GOVERNANCE.md` for what the bump classified; the Release body
carries a `contract: X.Y.Z` line. A MAJOR is the consumer's cue to re-vendor and adapt, not
to relax its check.

---

### Verifying a vendored `openapi.yaml`

**Symptom:** you are not sure the copy of the contract you hold is the one the running
image serves.

**Cause:** Release assets and registry tags are mutable; the committed
`contract/openapi.yaml.sha256` anchor in git history is not. Both files are generated;
hand-editing either is always wrong.

**Fix:** always verify against the anchor **from the same tag**, never against another copy.

```bash
docker run --rm --entrypoint cat ghcr.io/washingbearlabs/forage:1.0.0 /app/contract/openapi.yaml > openapi.yaml
docker run --rm --entrypoint cat ghcr.io/washingbearlabs/forage:1.0.0 /app/contract/openapi.yaml.sha256 > openapi.yaml.sha256
git show v1.0.0:contract/openapi.yaml.sha256 | diff - openapi.yaml.sha256 && sha256sum -c openapi.yaml.sha256
# or: gh release download v1.0.0 --pattern 'openapi.yaml*'
```

---

### Comparing `sanitizer_revision` across Poppy and Forage

**Symptom:** someone concludes the two deployments are "out of sync" because the revisions
differ.

**Cause:** wrong measure. Forage's revision has deliberately diverged from Poppy's fourteen times
(recorded in `docs/bootstrap-notes.md`); it hashes source bytes, model identity and the
threshold, not the wire shape.

**Fix:** compare contracts, not revisions. Until Poppy pins a published Forage image, its
in-tree copy is the deployed source of truth and hotfixes are replayed by hand both ways
with the Poppy SHA recorded in the pin record. Poppy must make its Forage env file
`required: true` before pointing at this image, or production silently drops to memory
mode.

---

## Operational Problems

### The container keeps restarting

**Symptom:** `docker inspect` shows a rising `RestartCount`; both compose fragments set
`restart: unless-stopped`, so a refused boot becomes a loop.

**Cause:** almost always configuration validation (`ExtractionConfigurationError` /
`CacheConfigurationError`) or an OOM kill; nothing else refuses boot.

**Fix:**
```bash
docker inspect --format '{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}' <container>
docker logs <container> 2>&1 | tail -50
```
Fix the mounted `config.yaml` or raise memory, then recreate.

---

### Memory pressure

**Symptom:** `OOMKilled: true`, `weights_load_failed` during a cold start, or
`extraction_failed` clustering on large PDFs. `/metrics.extraction.oom_proximity_ratio`
approaches 1.

**Cause:** the reference budget behind `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` (default `1024m`) is 512 MiB for the
parent (torch and the 22M-parameter model), 384 MiB for the pypdf child's address space,
and about 128 MiB headroom: 32 MiB in-memory cache, 64 MiB provisional classifier
working set and 32 MiB margin. See `docs/configuration.md` § Sizing the container
for the model-dependent rule and Valkey branch. `oom_proximity_ratio` is
`cgroup memory.current / memory.max` and is `null` off Linux or when the limit is `max`;
the explorer's suggested alert line is above 0.9.

**Fix:** size `FORAGE_MEM_LIMIT` to the configured rule, then verify
`extraction.cgroup_memory_max_bytes`. Boot warns `envelope_memory_rule_unmet` when
the readable limit is too small; it does not refuse to run. Tightening
`extraction.child_address_space_bytes` in `config.yaml` trades extraction capacity
for headroom; increasing concurrency, model size or cache requires more memory.

---

### No INFO lines in the logs

**Symptom:** `docker logs` shows only WARNING/ERROR lines, or nothing at all on a healthy
container.

**Cause:** expected. Nothing calls `logging.basicConfig()`, the root logger sits at WARNING,
the entrypoint prints nothing, and `uvicorn --log-level info` does not raise first-party
loggers. The whole success narrative (`weights_fetched`, `weights_loaded`, `Content cache
connected`, cache hits) is INFO and therefore invisible.

**Fix:** use `/metrics`. This is a recorded decision, not a gap to fix in a deploy.

---

### Compose pins lag the release tag, or `manifest unknown`

**Symptom:** `docker compose pull` fails with `manifest unknown`, or a fresh deployment runs
an older image than expected.

**Cause:** `compose/minimal.yml` and `compose/full.yml` pin
`ghcr.io/washingbearlabs/forage:1.1.0` and `forage-searxng:0.1.1-rc`; both resolve today
(`v1.1.0` published 2026-09-18, `search-release` US-002). A pin that runs ahead of the newest
published tag fails with `manifest unknown` until that tag publishes — sequencing, not
breakage — and a pre-release tag that was withdrawn (`v0.9.2-rc` was) or never published
pulls nothing.

**Fix:** pin a full semver (`1.1.0`) or the `@sha256` digest from the Release body, never
`latest`; then `docker compose -f <file> up -d`. Rollback is the same command with the
previous tag; a rollback across a `sanitizer_revision` rotation flushes the content cache,
which is expected.

---

### The shipped healthcheck and what it means

**Symptom:** Docker says healthy while Forage reports degraded or serves marked,
unscanned results. The image has no `HEALTHCHECK`; both compose fragments declare a liveness healthcheck.
The status-only `curl -fsS -o /dev/null` probe has a 30 s interval, 5 s timeout,
three retries and a 30 s start period.

**Fix:** treat this as liveness only. Never gate traffic, `depends_on:
service_healthy`, or consumer activation on Docker's health state. Read `/health`
`promptguard_loaded` / `degraded_reasons` and `/metrics` `search.unscanned_results`.
Plain Compose does not restart unhealthy containers. The probe also drives cache
recovery polls; with Valkey down, reconnect failures and WARNINGs have a baseline
of one per probe on refusal or about one per two on timeout. See `MONITORING.md`.

---

## Test and CI Problems

Setup and toolchain failures are in [`LOCAL_DEV.md`](LOCAL_DEV.md) § "Troubleshooting
Local Setup" (always `uv run`; phantom pyright errors; CUDA wheels in the lock; ruff
version drift). CI gate and publish failures are in [`CI_CD.md`](CI_CD.md) § "Failure
Playbook". The three that touch runtime behaviour most often:

- **`pytest_socket.SocketBlockedError`**: a test reached the real network. The autouse
  guard in `tests/conftest.py` is doing its job (`tests/test_hermeticity.py` is the canary);
  mock at the seam, never relax it. The same file clears `HF_TOKEN`, `HF_HOME`,
  `FORAGE_MODEL_REVISION`, `FORAGE_WEIGHTS_MIRROR`, `FORAGE_MIRROR_TOKEN` and `VALKEY_URL`
  before every test, which is why shell exports never reach the suite.
- **`tests/test_contract_schema.py::test_contract_schema_matches_golden` fails after a
  response change**: the wire shape moved without the governance steps. Bump
  `CONTRACT_VERSION`, add a new fixture under `tests/golden/` (existing ones are retained
  and never edited), and classify the change with `contract/GOVERNANCE.md`.
- **`tests/test_contract_export.py` is red**: anything that moves the OpenAPI document (a
  model, a `responses=` declaration, a description, a FastAPI bump) must be followed by
  `uv run python -m scripts.export_contract`; commit `contract/openapi.yaml`, its `.sha256`,
  and the fixture twin together.

---

## External Dependencies at a Glance

The full matrix (per-endpoint effect, counters, recovery) is in
[`../arch/SERVICE_MAP.md`](../arch/SERVICE_MAP.md) § "Failure Impact Matrix".

| Dependency | Symptom when down | Fallback | Health signal |
|---|---|---|---|
| SearXNG | `/search` 422 `searxng_unavailable` / `searxng_error` | None; stateless per request | **None** (not probed) |
| Valkey | `/retrieve` served uncached | Automatic reconnect, 1 s to 30 s | `cache_unavailable`, `cache_connected: false` |
| Hugging Face Hub / weights mirror | Content unscanned or quarantined | Retry loop, 30 s to 10 min, forever | `promptguard_unavailable`, `promptguard_loaded: false` |
| Target web site | `/retrieve` 422 per the code table | None; no retries | None |
| GHCR | New pulls and `publish` fail; running containers unaffected unless the mirror leg is in use | Retry loop (mirror leg only) | Via the weights row |

---

## Escalation

Capture these **before** asking for help; together they answer most questions without a
second round trip:

1. The `/health` body and the full `/metrics` document (`curl -s localhost:8020/health | jq`
   and `curl -s localhost:8020/metrics | jq`).
2. The relevant log lines from the grep table above, plus `docker inspect --format
   '{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' <container>`.
3. The image actually running (`docker inspect --format '{{.Config.Image}}' <container>`)
   and the `contract_version` and `sanitizer_revision` from `/health`.
4. The failing request: route, the request body with any document content removed, and the
   response's `error`, `reason`, and `request_id`.
5. The mounted `config.yaml`, and the **names** of the environment variables set. Never
   paste the env file: `HF_TOKEN`, `FORAGE_MIRROR_TOKEN`, `SEARXNG_SECRET`, and a
   `VALKEY_URL` with a password are all secrets, and Forage itself never logs them.

Anything security-relevant (a credential in a log line, a `private_ip` bypass, a
quarantine that let hostile text through with `promptguard_loaded: true`) goes through the
root `SECURITY.md` process: GitHub private vulnerability reporting, not a public issue.
Response is best-effort with no SLA; expect an acknowledgement within about a week.

---

## Post-Incident

After resolving an issue:

- [ ] If it was a new recurring shape, add an entry to this file (symptom, cause, fix).
- [ ] If it revealed a landmine with a measurable "why", add it to [`GOTCHAS.md`](GOTCHAS.md).
- [ ] If the fix rotated `sanitizer_revision`, record before/after in `docs/bootstrap-notes.md`.
- [ ] If the fix touched a response shape, follow `contract/GOVERNANCE.md` and the PR template.
- [ ] If the fix touched `services/retrieval/` paths that Poppy still carries, replay it
      there and update the pin record.
