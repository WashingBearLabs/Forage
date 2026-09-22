<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: architecture, dependencies
  required_sections:
    - "Endpoints" or "Commands" or "Interface"
  skip_if: no-api
-->
# API_GUIDE.md

> **TEMPLATE_INTENT:** Document API endpoints, CLI commands, or library interface. The external contract.

> Last updated: 2026-09-22
> Updated by: Copilot (hardening-hostname-and-config US-002)

---

## Overview

Forage is a single FastAPI service with five HTTP routes. The frozen, generated schema is
`contract/openapi.yaml` (anchored by `contract/openapi.yaml.sha256`, governed by
`contract/GOVERNANCE.md`), and the running container serves the same document live at
`/openapi.json`, `/docs` (Swagger UI) and `/redoc`. **This guide is not a second copy of
that schema.** It is the usage guide: how to call each route correctly, what to check
before trusting a response, how errors are shaped, and how to vendor and verify the
contract. Field lists below are the fields that matter for correct use; the contract has
the full shape.

**Base URL.** The container listens on `0.0.0.0:8020`; both compose fragments
(`compose/minimal.yml`, `compose/full.yml`) publish it as `127.0.0.1:8020` only. Examples
below use `http://127.0.0.1:8020`. There is no path prefix and no versioned path.

**No authentication, by design.** Every route, including `/docs`, `/redoc` and
`/openapi.json`, is unauthenticated: no API key, no bearer token, no allowlist, no CORS
configuration. Network placement is the access control. Anyone who can reach the port can
make Forage fetch arbitrary URLs and read every `/metrics` counter, so run it on a private
network only. `kit_tools/arch/SECURITY.md` "Authentication and Authorization" explains the
reasoning; `docs/configuration.md` "Deployment posture" is the operator statement.

**Formats.** JSON request and response bodies everywhere, with one exception:
`POST /extract` takes `multipart/form-data`. Datetimes are ISO 8601 in UTC.

**Versioning signal.** The response contract has a hand-bumped semver, `contract_version`,
currently **1.3.0** (`pipeline/contract.py`). It appears on `/health`, on `/metrics`, and
as `info.version` in `/openapi.json`. Consumers compare the MAJOR component and refuse to
activate on a mismatch; a MINOR difference is additive and safe. `/health` also carries
`sanitizer_revision`, a hash of pipeline *behaviour*; never compare it for compatibility
(see "Versioning and contract vendoring").

---

## Endpoint summary

| Method | Path | Purpose | Request | Response | Notable limits | Gate |
|---|---|---|---|---|---|---|
| GET | `/health` | Liveness plus honest degradation state | none | `HealthResponse` | always 200 | none |
| GET | `/metrics` | In-process counters as JSON | none | `MetricsResponse` | reset on restart | none |
| POST | `/retrieve` | Fetch, extract and sanitise one URL | `RetrieveRequest` (JSON) | `RetrievedContent` | 30 s fetch timeout, 5 redirects, 10 MiB response cap | none |
| POST | `/search` | Finds and returns provider-extracted content for a query across sources, sanitised, never cached | `SearchRequest` (JSON) | `SearchResponse` | `num_results` 1..20; 10 s per provider call | at least one provider of the configured chain (`/health` `search_providers`) reachable |
| POST | `/extract` | Sanitise an uploaded PDF or UTF-8 text document | multipart form | `ExtractedContent` | 50 MiB upload, 500 PDF pages, admission queue depth 1 | `config.yaml` `extract_route_enabled` (shipped `false`; route answers 404) |

Also present and unauthenticated: `GET /openapi.json`, `GET /docs`, `GET /redoc`. They
cannot be disabled by configuration.

---

## Response codes by route

Exactly the status codes `contract/openapi.yaml` declares. `/health` and `/metrics`
declare only 200.

| Route | Codes | Notes |
|---|---|---|
| `GET /health` | 200 | Always 200; degradation is in the body |
| `GET /metrics` | 200 | An unmodelled counter would be a loud 500 by design, never a silently dropped key |
| `POST /retrieve` | 200, 422 | 422 is a coded refusal or FastAPI's schema-validation body |
| `POST /search` | 200, 422 | 422 is a provider failure (`searxng_error`, `searxng_unavailable`, or `search_unavailable` with a `<provider_name>: <failure_class>` reason), a policy refusal (`search_unavailable` with reason `policy_excluded_all_providers`), or a schema-validation body |
| `POST /extract` | 200, 400, 404, 413, 422, 429, 503 | 404 = route disabled; 429 = admission refused; 413 is documented but unreachable (an oversized upload receives 400); 503 = application state not wired |

Two things that are **not** errors: prompt-injection quarantine (a 200 with a content-free
body) and a missing Prompt Guard model (a 200 whose `promptguard_state` is an
`unavailable_*` value, with `/health` reporting `degraded`).

---

## Endpoints

### GET /health

```
GET /health
```

Always returns 200. The truth is in the body; a bare `curl -f` proves only that the
process is up.

```bash
curl -s http://127.0.0.1:8020/health
```

Fields to read (all present; `degraded_reasons` defaults to `[]`):

| Field | Type | What it tells you |
|---|---|---|
| `status` | `"healthy"` or `"degraded"` | `degraded` whenever any reason below is present |
| `degraded_reasons` | list of `promptguard_unavailable`, `cache_unavailable` | The closed reason vocabulary; an unlisted member cannot appear (response validation would 500) |
| `promptguard_loaded` | bool | Whether the Prompt Guard model is loaded. Flips to `true` in place when weights land; no restart needed |
| `cache_connected` | bool | Live ping in `valkey` mode; always `true` in `memory` mode |
| `cache_backend` | `"valkey"` or `"memory"` | Which storage was selected at start (added in 1.1.0). `memory` means `VALKEY_URL` was fully unset |
| `capabilities` | dict of str to int | Presence map, two keys as of 1.2.0: `search_sanitization` present when the model is loaded (or break-glass advertising is armed), `brave_api_key` present when this start resolved a usable `FORAGE_BRAVE_API_KEY` — independently of the sanitization key and untouched by break-glass |
| `search_providers` | list of str | The resolved provider chain's names, in traversal order, after key-gated skips (added in 1.2.0). Configuration echo, not a liveness probe |
| `contract_version` | str | `1.3.0`; the compatibility signal |
| `sanitizer_revision` | str | Hash of pipeline behaviour; a cache-key input, not a compatibility signal |

`/health` never probes SearXNG: a missing search backend surfaces per request on
`/search` as a 422, not here. Field semantics, the startup budget and what `/health`
cannot tell you are in `kit_tools/docs/MONITORING.md` "Health Checks".

### GET /metrics

```
GET /metrics
```

JSON, not Prometheus text. Counters live in-process and reset on restart.

```bash
curl -s http://127.0.0.1:8020/metrics
```

The body is `contract_version` plus five sections: `extraction`, `search`, `retrieve`,
`cache`, `model`. Keys outside the contract vocabularies (an unexpected error code,
omission reason or state) fold into an `"other"` bucket rather than adding a new key, and
the section models forbid extra fields, so the key set you see is the contract.
Per-section meaning is in `kit_tools/docs/MONITORING.md` "Metrics". The `model` section
(`fetch_in_progress`, `retries_scheduled`, `fetch_failures`, `verify_failures`,
`quarantines`) is the way to see what weight acquisition is doing, because application
INFO logging is not configured in the container.

### POST /retrieve

```
POST /retrieve
Content-Type: application/json
```

Fetches one URL (HTML or PDF), extracts it, runs the structural and Prompt Guard scans,
and returns the sanitised text with its trust evidence. Results are cached per URL,
extraction mode and trust policy (`cache.py`), so repeated calls with identical policy
fields hit the cache.

Request fields (`RetrieveRequest` in `models.py`):

| Field | Type | Default | Bounds | Notes |
|---|---|---|---|---|
| `url` | str | required | non-empty | `http` or `https` only; hosts resolving to private or reserved addresses, `localhost` and `.local` names are refused |
| `extract_mode` | `"summary"` or `"full"` | `"summary"` | | Summary keeps the leading and trailing paragraphs, statistics, quotes, list items and table rows, and sets `truncation_notice` |
| `cache_ttl_hours` | int | 24 | 0..8760 | Maximum acceptable age of a cached copy; `0` disables the cache for this call and purges the entry |
| `trusted_domains` | list of str | `[]` | | Bare entries match only themselves; a leading dot covers the apex and every subdomain, skipping injection classification for all of them (`skipped_trusted`). Never use a multi-tenant or registry-level apex (`.co.uk`, `.github.io`, `.s3.amazonaws.com`). US-007's `policy_suffix_trusted_skip` counts wildcard-caused resolutions to either tier. |
| `verified_domains` | list of str | `[]` | | Bare entries match only themselves; a leading dot covers the apex and every subdomain. All covered hosts degrade open when the classifier is unavailable, even under `promptguard_fail_closed_floor` or a load-triggered wait timeout. Never use a multi-tenant or registry-level apex (`.co.uk`, `.github.io`, `.s3.amazonaws.com`). Same planned `policy_suffix_trusted_skip` counter. |
| `blocked_domains` | list of str | `[]` | | Merged with `config.yaml` `seed_blocklist`; refused with `blocked_domain` (private names take precedence as `private_ip`). **Upgrade note:** existing multi-label entries now cover subdomains; review apex entries before upgrading, because a multi-tenant apex removes every tenant. Single-label entries keep matching exactly as before. |
| `promptguard_threshold` | float | 0.85 | 0.0..1.0 | Classifier scores above this value quarantine content; bounded by the operator's `promptguard_threshold_ceiling` via `min(request, ceiling)` |
| `promptguard_fail_closed` | bool | `true` | | When the classifier is absent or the permit wait expires: `true` quarantines standard and untrusted content, `false` allows it with a trust penalty; bounded by the operator's `promptguard_fail_closed_floor` via `request or floor`. Neither overrides the trusted_tier skip or VERIFIED fail-open exemption |

```bash
curl -s -X POST http://127.0.0.1:8020/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/article", "extract_mode": "summary"}'
```

Matching uses canonical UTS-46 names: denylist `evil.com` covers `www.evil.com`
but never `notevil.com`; allowlist `example.com` matches only itself whereas
`.example.com` covers the apex and every subdomain. IP literals are equality-only.
Single-label entries are accepted only on denylists, with exact-only matching.

With a trust policy:

```bash
curl -s -X POST http://127.0.0.1:8020/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://docs.python.org/3/", "extract_mode": "full",
       "trusted_domains": ["docs.python.org"], "cache_ttl_hours": 168}'
```

Response fields a consumer must read (`RetrievedContent`; full shape in the contract):

| Field | Type | Read it because |
|---|---|---|
| `body` | str | The sanitised text. On quarantine it is the fixed sentence `Content quarantined due to potential prompt injection.` and nothing else |
| `injection_detected` | bool | `true` means `body` is the quarantine placeholder, not the page |
| `injection_spans` | list of str | On quarantine, one diagnostic label: `structural_injection_detected`, `promptguard_injection_detected` or `promptguard_unavailable`. Never hostile text |
| `promptguard_state` | `scanned`, `skipped_trusted`, `structural_blocked`, `unavailable_blocked`, `unavailable_allowed` | Whether the ML scan actually ran on this content |
| `effective_promptguard_fail_closed` | bool (default `true`) | Applied flag after the operator floor, on every 200 including cache hits; decides behaviour only when the classifier is absent or its wait times out. Reports policy, **not whether content was scanned** (`promptguard_state` does). Neither effective field overrides caller trust lists: `trusted_domains` skips classification (`trusted_tier`); `verified_domains` (VERIFIED) degrades open when unavailable |
| `effective_promptguard_threshold` | float (default `0.85`) | Applied block threshold after the operator ceiling, on every 200 including cache hits. Policy, **not proof of scanning**: `trusted_domains` still skips (`trusted_tier`) and `verified_domains` (VERIFIED) still degrades open when unavailable. Read `promptguard_state` for the outcome |
| `trust_score` | float 0.0..1.0 | Composite evidence score; see "Reading a response safely" |
| `trust_tier` | `trusted`, `verified`, `standard`, `untrusted`, `blocked` | Tier resolved from your domain lists against the domain of `final_url` |
| `stage2_verdict`, `stage3_verdict` | `clean`, `suspicious`, `blocked`; `safe`, `injection_detected` | The two scan verdicts separately |
| `structural_flags` | list of str | Category names from the regex scan (for example `instruction_override`); never the matched text |
| `source_url`, `final_url`, `redirect_chain`, `domain_changed_on_redirect`, `domain` | str, str, list of str, bool, str | Where the content actually came from. A domain change on redirect costs 0.1 of trust |
| `cache_hit`, `cached_at`, `retrieved_at` | bool, datetime or null, datetime | Whether you got a cached copy and how old it is |
| `content_type` | `"html"` or `"pdf"` | Source format |
| `title`, `word_count`, `truncation_notice` | str or null, int, str or null | `truncation_notice` is set only in summary mode |
| `request_id` | str | Server-minted per call (fresh even on a cache hit); quote it in bug reports |

Refusals are 422 with `{"error", "reason", "request_id"}`: `invalid_url`, `private_ip`,
`blocked_domain`, `fetch_timeout`, `fetch_error`, `content_too_large`, and `busy` (reason
`admission_queue_full`: the `/retrieve` admission queue is full — retry later; the same
literal is `/extract`'s 429, but here it is always 422), and `extraction_failed` (a fetched
PDF the worker could not parse or spool, with one of four fixed reasons: `pdf_encrypted`,
`pdf_no_text`, `pdf_extraction_error`, or `pdf_spool_error` for a host-side spool failure
— reasons, not codes, even where the literal matches an `/extract` code; `1.3.0`,
`hardening-retrieve-parity` US-003). `reason` echoes the
requested URL, and `private_ip` echoes the resolved address (`contract/GOVERNANCE.md`
ruling (d)). `content_too_large` is the one code with **two reason shapes**: the fetch-cap
prose that echoes the URL, or the fixed literal `promptguard_budget` when the fetched
page's extracted text exceeds its ceiling. Fetched PDFs run under
`extraction.max_promptguard_chunks`; fetched HTML under `retrieve.max_promptguard_chunks`.
Branch on the literal, not on the prose. A fetched PDF is parsed in the same spawned,
rlimited worker `/extract` uses, so its first fetch pays a process-spawn cost (hundreds of
milliseconds); repeat fetches are cache hits. Quarantined results are never cached; neither are `untrusted` or `blocked`
tiers.

The effective fields are stamped after the pipeline using the same resolved values that
key the cache, not trusted from a stored entry. Refusal 422 bodies carry neither field.
`/extract` is permanently fail-closed and carries neither field.

### POST /search

```
POST /search
Content-Type: application/json
```

Runs one query through the configured provider chain (`FORAGE_SEARCH_PROVIDERS`, default
`searxng` — the SearXNG companion at `SEARXNG_URL`, default `http://searxng:8080`), then
passes every result's title, URL and snippet through the same structural and Prompt Guard
scans. Results that fail are omitted and counted, not
returned. Never cached.

`/search` finds and returns provider-extracted content for a query across sources —
snippets or chunks, per result `content_kind` — from the configured provider chain, every
result sanitized, never cached; `/retrieve` fetches and sanitizes one caller-named URL
through the full pipeline, cached by `sanitizer_revision`. `promptguard_fail_closed` and `blocked_domains` are
honoured on both routes; this route additionally honours `providers` and
`allow_paid_fallback` (contract 1.2.0) and scans every result at the fixed 0.85 default at
trust tier `standard` (`config.yaml`'s `promptguard_threshold` is not applied here), while
`/retrieve` additionally honours `promptguard_threshold`, `trusted_domains`,
`verified_domains` and `cache_ttl_hours`. This documents today's
divergence; changing it belongs to `epic-forage-hardening`.

Request fields (`SearchRequest`):

| Field | Type | Default | Bounds | Notes |
|---|---|---|---|---|
| `query` | str | required | non-empty | |
| `num_results` | int | 5 | 1..20 | Forage asks SearXNG for up to `min(2 * num_results, 20)` candidates and scans at most 20 |
| `promptguard_fail_closed` | bool | `true` | | When the classifier is absent or the permit wait expires: `true` withholds results (`omitted_by_reason.promptguard_unavailable`), `false` returns them marked `suspicious` and counts them in `unscanned_results`; bounded by the operator's `promptguard_fail_closed_floor` via `request or floor` |
| `providers` | list of str | `[]` | at most 8 honoured, rest ignored | Restrict-only filter of the configured chain, in configured order: can exclude paid providers only, never add, reorder, or key one — free providers always run, and a non-empty list removes every paid provider it does not name. Matched after `strip()` and lower-casing; entries beyond the first eight, and entries matching no configured provider, are ignored and counted on `/metrics` `search.policy_unknown_provider` rather than rejected. Empty (the default) runs the configured chain unrestricted |
| `blocked_domains` | list of str | `[]` | raw UTF-8 list bytes, not entry count | Merged after the operator's `seed_blocklist`, which cannot be overridden. Multi-label names omit apex and dot-boundary subdomains; single-label names and IP literals match exactly. Entries are stripped and UTS-46-canonicalised once; malformed entries are ignored and counted on `search.policy_invalid_domain_entry`, never echoed. A list exceeding `policy_domain_entries_max_bytes` (including newline separators) is refused whole before encoding, 422 `search_unavailable` / `policy_domain_list_too_large`. Matches are omitted as `blocked_url` after the URL audit and before content scanning, without triggering paid fallback. Added in `1.3.0` |
| `allow_paid_fallback` | bool | `true` | | When `false`, excludes every paid provider from this request's effective chain regardless of `providers` — free providers always run. Applied after `providers`' own filtering, one-way: can only narrow the configured chain, never widen, reorder, or key it |

A consumer sources the names it may put in `providers` from `/health`'s `search_providers`
and reconciles against that field, not against `FORAGE_SEARCH_PROVIDERS` or any other
local copy of the chain.

There is no per-request threshold on `/search`: every result is scanned at the hard
default 0.85 at trust tier `standard` (observation from `pipeline/orchestrator.py`;
`config.yaml` `promptguard_threshold` is not applied on this route).

```bash
curl -s -X POST http://127.0.0.1:8020/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "prompt injection detection", "num_results": 5}'
```

Response fields to read (`SearchResponse`):

| Field | Type | Read it because |
|---|---|---|
| `results` | list of `{title, url, domain, snippet, engine, content_kind, date, suspicious}` | `suspicious: true` means the structural scan flagged it, the classifier scored above 0.5, or **PromptGuard never scanned it at all** — either the classifier was absent, or (since `1.3.0`) the request's classification wait expired while the classifier was busy. The consumer rule is therefore: **on `promptguard_unavailable: true`, treat every `suspicious` result in the response as unscanned rather than as scanned-and-flagged**, with `unscanned_results` saying how many. Since `1.3.0` a single response may *mix* the two — the classification wait is one budget per request, so earlier results can be scanned and later ones not — so the flag alone no longer tells you which. Title is at most 512 characters, URL 2048 (over-length URLs are omitted under `invalid_url`, never truncated), snippet 2000. `domain` (added in `1.2.0`) is the **canonicalised ASCII host** of `url`, never eTLD+1 — a provenance signal, not a trust decision. A name is UTS-46-encoded, so an internationalised host appears in punycode (`xn--strae-oqa.de`) while `url` keeps the provider's spelling (`http://straße.de/`); an address literal is the raw lower-cased literal as written, so for an IPv6 literal `domain` is unbracketed (`2606:4700::1111`) while `url` carries brackets (`[2606:4700::1111]`), the one case where `domain` is not a substring of `url`. `engine` is whichever SearXNG sub-engine answered (e.g. `duckduckgo`, or SearXNG's own `brave` sub-engine) or, for a Brave-served result, `brave-api` — the two are deliberately never normalized into each other; bounded to 64 characters and NFC-normalised (added in `1.3.0`), or `null` for a non-string or an empty-after-normalisation value — still neither structurally scanned nor part of the PromptGuard input. `content_kind` (added in `1.2.0`) is `snippet` or `chunk` and nothing else. `date` (added in `1.2.0`) is a strict `YYYY-MM-DD` calendar date or `null` — anything a provider sends that is not one becomes `null`, so it never needs parsing defensively |
| `provider_used` | str | Added in `1.2.0`. The serving provider's `name` — `searxng`, `brave`, or a future third token (open string, not an enum, so a new provider is additive) |
| `effective_promptguard_fail_closed` | bool (default `true`) | Applied unavailable-classifier policy after the floor on every 200, **not whether results were scanned**; read omissions / `suspicious` / `promptguard_unavailable` / `unscanned_results`. Search uses STANDARD tier; on `/retrieve` the floor does not override `trusted_domains`' classification skip (`trusted_tier`) or `verified_domains`' VERIFIED fail-open exemption. No effective threshold is reported on `/search`: it still scans at `0.85` |
| `fallback_fired` | bool | Added in `1.2.0`. `true` iff the provider chain advanced past the first provider before this response was served — the per-response face of the `search.fallback_fired` `/metrics` counter. It says nothing about which provider served: for a chain that tries a paid provider first, this is `true` when the free provider ends up serving |
| `provider_errors` | list of str | Added in `1.2.0`. Chain-order `"<provider_name>: <failure_class>"` entries for every provider tried before the one that served (closed vocabulary, never exception text or a URL) — the only place provider-level failures appear; they never affect `omitted_results` / `omitted_by_reason` or `unresponsive_engines` |
| `omitted_results`, `omitted_by_reason` | int, dict of str to int | How many candidates were withheld and why; keys are only ever `invalid_url`, `structural_blocked`, `injection_detected`, `promptguard_unavailable`, and — added in `1.3.0` — `blocked_url`, and only non-zero counts appear. `invalid_url` covers three families of URL rule, checked on the provider's raw value in this order, first rejection wins: **presence and length** (missing, empty, non-string, or longer than 2 048 characters after trimming — over-length is rejected, never truncated), **raw characters** (any control character, any whitespace, or any RFC 3986 excluded character — `<`, `>`, `"`, `{`, `}`, `\|`, `\\`, `^`, backtick — rejected rather than stripped), and **host code points** (any WHATWG forbidden domain code point surviving into the hostname, plus a non-`http(s)` scheme, userinfo, an unparseable or out-of-range port, and an IPv6 zone id). A URL that clears all three is scanned structurally in both its entity-decoded and its once-percent-decoded form; a block there counts under `structural_blocked`, never twice. `blocked_url` is policy rather than malformation: a URL that parsed and canonicalised cleanly but names a literal private, loopback, link-local, documentation-range or blocklisted host — including an IPv6 literal that *embeds* a private IPv4 (IPv4-mapped, 6to4, Teredo, prefix-guarded NAT64 and IPv4-compatible forms) and any name under `.local` or `.localhost`. The audit is purely lexical: no DNS is resolved for a URL nobody asked to fetch. Two further `invalid_url` tokens come from the same canonicalisation: a host whose every label is a digit run but which is not a canonical dotted quad (`2130706433`, `0177.0.0.1`, `0x7f000001`, `127.1`) is `numeric_host`, and a host the UTS-46 encode refuses (an underscore label, an over-long label, an empty label) is `idna` |
| `unscanned_results`, `promptguard_unavailable` | int, bool | Non-zero or `true` means results came back without the ML scan; treat the whole response as unscanned evidence. `promptguard_unavailable` says *whether any* result was unscanned, `unscanned_results` says *how many* — and since `1.3.0` that count can be a strict subset of the response, because the classification wait is per request rather than a state of the process. The unscanned ones are exactly the `suspicious` ones; there is no per-result marker distinguishing "flagged" from "never scanned" |
| `unresponsive_engines` | list of str | The serving provider's SearXNG engines that failed to respond; empty on a Brave-served response. With zero results this is the free-path failure signal that advances a multi-provider chain (`search-fallback` US-002); with results present it is a partial answer, not an error, and no fallback fires. Entries are unsanitized, provider-asserted text — no stage scans them — and must never be rendered into a model prompt |
| `request_id`, `query` | str, str | Correlation and echo |

`blocked_url` also counts matches against caller `blocked_domains` and operator
`seed_blocklist`, after the lexical URL audit and before content scanning. Even
if every raw result is omitted, this does not trigger paid fallback: provider
sufficiency is decided before sanitization and policy omissions.

Failures are 422 with `{"error", "reason", "request_id"}`. Which code you get depends on
the **configured chain**, not on which backend failed. A chain of exactly one provider
named `searxng` — the default — keeps the legacy pair: `searxng_error` (SearXNG answered
non-2xx; `reason` carries the status as `http_<code>`) or `searxng_unavailable`
(connection refused, DNS, timeout, an oversized body, bad JSON; `reason` carries the
scheme, host and port of the configured URL — userinfo stripped — and a closed `detail`
token, never exception text). Any other chain refuses with `search_unavailable` (added in
`1.2.0`), whose `reason` is the closed composite of chain-order `<provider_name>:
<failure_class>` entries joined by `"; "`. Traversal makes one call per provider in
configured order, no retries, bounded by the sum of the per-provider timeouts.

The second `search_unavailable` form is a policy refusal, not a provider failure: when a
request's `providers` / `allow_paid_fallback` leaves its effective chain empty — possible
only on a configured chain with no free provider — the handler refuses before any provider
is called, with the fixed literal `reason` `policy_excluded_all_providers`
(`search-policy-and-health` US-010). The third form is an oversized caller
`blocked_domains` list: `reason=policy_domain_list_too_large`, refused whole before
encoding or any provider call (`hardening-hostname-and-config` US-002).
Both policy reasons are permanent client errors, **not retryable** without
changing the request or the configured policy/budget, even on a lone SearXNG chain.

### POST /extract

```
POST /extract
Content-Type: multipart/form-data
```

Sanitises an uploaded document: a PDF (parsed in a resource-limited child process) or
strict UTF-8 text. Uploads are always treated as tier `untrusted` with fail-closed
scanning; there is no caller-controlled trust policy on this route. Never cached.

**Gate.** The route ships disabled: `config.yaml` `extract_route_enabled: false` makes it
answer 404 `{"detail": "Not Found"}` before any parsing. Set it to `true` to enable
(`docs/configuration.md`).

Form fields (from the handler signature in `retrieval_app.py`):

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file part | yes | The document bytes |
| `filename` | str | yes | Display only; at most 255 characters and must contain a basename. Format is detected from content, not from the name |
| `mime_hint` | str | no | Advisory and display only; at most 255 characters |
| `extract_mode` | `"summary"` or `"full"` | no; default **`"full"`** | The default differs from `/retrieve` |
| `request_id` | str | no | Caller-supplied correlation ID; at most 128 characters matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`. Omit it and Forage mints one |
| `timeout_s` | float greater than 0 | no | Accepted for compatibility and ignored |

```bash
curl -s -X POST http://127.0.0.1:8020/extract \
  -F 'file=@report.pdf' \
  -F 'filename=report.pdf' \
  -F 'mime_hint=application/pdf' \
  -F 'extract_mode=full'
```

Limits (`config.yaml` `extraction:`, shipped at their maxima): 50 MiB upload
(`max_input_bytes`), 500 PDF pages, 20 s CPU and 90 s wall clock for the parser child,
384 MiB child address space, 64 Prompt Guard chunks (about 114,688 characters of text),
one extraction and one classification at a time, admission queue depth 1.

The response (`ExtractedContent`) carries the same trust fields as `/retrieve` (`body`,
`injection_detected`, `injection_spans`, `promptguard_state`, `trust_score`,
`trust_tier`, `stage2_verdict`, `stage3_verdict`, `structural_flags`, `title`,
`word_count`, `truncation_notice`) plus:

| Field | Type | Read it because |
|---|---|---|
| `content_type` | `"pdf"` or `"text"` | What the content sniff decided, regardless of `mime_hint` |
| `provenance` | `{source_type: "upload", filename, mime_hint}` | The sanitised display metadata you sent |
| `sanitizer_revision` | str | Same value as `/health`. Consumers that cache by it need it on every extract response, so it is on the 422 and 429 bodies too |
| `trust_tier` | always `untrusted` | Base score 0.40 before penalties |

Status codes on this route, in the order a request meets them:

- **404** `{"detail": "Not Found"}`: route disabled.
- **503** `{"detail": "Unavailable"}`: admission controller not wired on application
  state; not expected under a normal uvicorn start.
- **429** `{"error": "busy", "reason", "request_id", "sanitizer_revision"}`: admission
  refused. One extraction runs at a time, one may queue, and each queued request reserves
  50 MiB of upload budget. Retry later; there is no `Retry-After` header.
- **400** `{"detail": "There was an error parsing the body"}`: the multipart body could
  not be parsed, **and** what an upload over 50 MiB actually receives. The byte-counting
  middleware raises the 413 below, but FastAPI's form parser catches it first. Documented,
  not fixed; correcting it is a MAJOR bump (`contract/GOVERNANCE.md` ruling (a2)).
- **413** `{"error": "content_too_large", "reason"}`: declared in the contract because the
  middleware emits exactly this shape, but unreachable on this route today. No
  `request_id`, because the refusal happens before any handler runs.
- **422** `{"error", "reason", "request_id", "sanitizer_revision"}`: a document failure
  with a fixed, content-free reason: `content_too_large` (post-parse re-check),
  `content_too_large_to_classify`, `extraction_failed`, `invalid_filename`,
  `invalid_mime_hint`, `invalid_request_id`, `pdf_encrypted`, `pdf_no_text`,
  `unsupported_format`. Also FastAPI's schema-validation body when a required form field
  is missing.
- **200**: extracted, possibly quarantined (read `injection_detected`).

---

## Reading a response safely

Forage reports evidence; the consumer owns every trust decision. A `healthy` Forage and a
200 response are not a promise that the content is safe, and a 200 with a quarantined body
is not an error. Before using `body` from `/retrieve` or `/extract`:

1. **Check the scan actually ran.** `promptguard_state` is `scanned` only when the model
   examined the content. `skipped_trusted` means you asked for that via `trusted_domains`.
   `structural_blocked` means the regex stage quarantined it before the model ran.
   `unavailable_blocked` and `unavailable_allowed` mean the model was absent: the first
   quarantined (fail-closed), the second let the content through with a 0.1 penalty
   (fail-open, or a `verified` domain). At deployment level, `/health.promptguard_loaded`
   and `capabilities.search_sanitization` answer the same question for `/search`, whose
   responses carry `promptguard_unavailable` and `unscanned_results` instead.
2. **Recognise quarantine.** `injection_detected: true`, `body` equal to the fixed
   placeholder sentence, and one diagnostic label in `injection_spans`.
   `structural_flags` keeps the category names. Nothing of the hostile text is returned,
   and the result is never cached.
3. **Use `trust_score` as a summary, not as a gate you did not choose.** It is a base
   score by tier (trusted 0.95, verified 0.85, standard 0.70, untrusted 0.40, blocked
   0.0), minus a structural penalty (0.15 per suspicious flag, capped at 0.45), minus a
   classifier penalty (0.5 on `injection_detected`, including the fail-closed-unavailable
   case; 0.1 for fail-open-unavailable), minus 0.1 when the domain changed on redirect,
   clamped to 0..1 (`pipeline/stage4_structuring.py`). Read `stage2_verdict` and
   `stage3_verdict` when you need the components.
4. **Check where it came from.** `final_url`, `redirect_chain` and
   `domain_changed_on_redirect` on `/retrieve`; `provenance` on `/extract`.
5. **Check freshness.** `cache_hit` and `cached_at` on `/retrieve`; lower
   `cache_ttl_hours` (or send `0`) when staleness matters.

The reasoning behind each stage and the tests that pin it are in
`kit_tools/arch/SECURITY.md` "Prompt-Injection Signalling".

---

## Error responses

### Body shapes

Four coded shapes and one bare shape; the shape is determined by route and status, never
by the code.

| Shape | Routes and statuses | Body |
|---|---|---|
| Pipeline 422 | `/retrieve` 422, `/search` 422 | `{"error", "reason", "request_id"}`. `reason` is not content-free: it echoes the URL (and for `private_ip`, the resolved address) |
| Extract 422 | `/extract` 422 | `{"error", "reason", "request_id", "sanitizer_revision"}`. `reason` is a fixed string from a closed vocabulary |
| Admission 429 | `/extract` 429 | `{"error": "busy", "reason", "request_id", "sanitizer_revision"}`, minted by the middleware |
| Admission 413 | `/extract` 413 (unreachable today) | `{"error": "content_too_large", "reason"}`, no `request_id` |
| Bare detail | `/extract` 400, 404, 503 | `{"detail": "..."}` with no error code. Schema-validation failures on any POST are FastAPI's default `detail` list of `loc`, `msg`, `type` entries |

Every coded body's `error` is one of the eighteen codes below and nothing else
(`pipeline/contract.py` `ERROR_CODES`; `tests/test_contract_errors.py` drives each
emission site through the real routes and asserts parity).

### Codes

| Code | Status | Route | Meaning |
|---|---|---|---|
| `blocked_domain` | 422 | `/retrieve` | Host is in `blocked_domains` or the configured `seed_blocklist` |
| `busy` | 429 | `/extract` | Admission queue full |
| `busy` | 422 | `/retrieve` | Reason `admission_queue_full`: the fetch admission queue is at `retrieve.admission_queue_depth` or `retrieve.max_queued_fetch_bytes`. Added in `1.3.0` (`hardening-retrieve-parity` US-002) |
| `content_too_large` | 422 (and the unreachable 413) | `/retrieve`, `/extract` | Response body over 10 MiB (`/retrieve`); upload over 50 MiB (`/extract`) |
| `content_too_large_to_classify` | 422 | `/extract` | Extracted text exceeds the Prompt Guard chunk budget |
| `extraction_failed` | 422 | `/extract` | Parser failure |
| `extraction_failed` | 422 | `/retrieve` | A fetched PDF failed in the worker or its spool. Reason `pdf_encrypted`, `pdf_no_text`, `pdf_extraction_error` (corrupt parse, page limit, or an rlimit / wall-clock kill) or `pdf_spool_error` (host fault: the spool file could not be written). A fetched PDF over `extraction.max_promptguard_chunks` is `content_too_large` / `promptguard_budget` instead — fetched PDFs run under `extraction.max_promptguard_chunks`; fetched HTML under `retrieve.max_promptguard_chunks`. Added in `1.3.0` (`hardening-retrieve-parity` US-003) |
| `fetch_error` | 422 | `/retrieve` | Any other fetch failure, including more than 5 redirects |
| `fetch_timeout` | 422 | `/retrieve` | 30 s fetch timeout |
| `invalid_filename` | 422 | `/extract` | Over 255 characters or no basename |
| `invalid_mime_hint` | 422 | `/extract` | Over 255 characters |
| `invalid_request_id` | 422 | `/extract` | Over 128 characters or disallowed characters |
| `invalid_url` | 422 | `/retrieve` | Bad scheme, no hostname, or DNS failure |
| `pdf_encrypted` | 422 | `/extract` | Encrypted PDF |
| `pdf_no_text` | 422 | `/extract` | No extractable text; OCR is not supported |
| `private_ip` | 422 | `/retrieve` | Resolves to a private or reserved address, `localhost` or a `.local` name |
| `search_unavailable` | 422 | `/search` | Three reason forms: `<provider_name>: <failure_class>` when the configured provider chain failed and is not a lone `searxng`; `policy_excluded_all_providers` when `providers` / `allow_paid_fallback` narrowed the chain to empty; or `policy_domain_list_too_large` when caller `blocked_domains` exceeded the byte budget. Both policy refusals occur before any provider call and are permanent client errors, **not retryable** without changing the request or configured policy/budget; distinguish them by `reason`, not the code alone. Code added in `1.2.0`, domain-policy refusal in `1.3.0` |
| `searxng_error` | 422 | `/search` | SearXNG returned a non-2xx status (a lone `searxng` chain only) |
| `searxng_unavailable` | 422 | `/search` | SearXNG unreachable, timed out, or returned bad JSON (a lone `searxng` chain only) |
| `unsupported_format` | 422 | `/extract` | Neither a PDF nor valid UTF-8 text |

First-thing-to-check guidance per code is in `kit_tools/docs/TROUBLESHOOTING.md`
"Error-Code Reference". Two observations for consumers that log or display `reason`:
`fetch_error` interpolates exception text, and `searxng_unavailable` echoes the scheme,
host and port of the configured `SEARXNG_URL` (userinfo stripped, no exception text); the
`/extract` reasons never carry anything variable.

---

## Versioning and contract vendoring

**`contract_version` semantics** (`pipeline/contract.py`; policy in
`contract/GOVERNANCE.md`): MAJOR bumps when a field is removed or renamed or its meaning
changes; MINOR when fields or enum members are only added. Compare MAJOR and refuse to
activate on a mismatch. New members of `degraded_reasons`, `omitted_by_reason` or
`promptguard_state` are MINOR, so bucket unknown members rather than failing on them.
`1.0.0` is the frozen original surface; `1.1.0` added `/health.cache_backend`; `1.2.0`
added `SearchResult.content_kind` and `SearchResult.date`, the `search_unavailable`
error code, and — additively, still `1.2.0` — `SearchResult.domain` and
`SearchResponse.provider_used` / `fallback_fired` / `provider_errors`; `1.3.0` declared
`blocked_url` in `omitted_by_reason` and bounded `SearchResult.engine` to 64 characters,
NFC-normalised. Do not
compare `sanitizer_revision`: it has deliberately diverged between Forage and Poppy's
in-tree copy and says nothing about wire compatibility. The image tag (for example
`v1.0.0`) is a third, independent version.

**Where the contract ships.** Every release carries the same byte-identical
`openapi.yaml` in three places:

| Route | How to get it |
|---|---|
| Git tag | `contract/openapi.yaml` and `contract/openapi.yaml.sha256` at `v<version>` |
| Release assets | `gh release download v<version> --pattern 'openapi.yaml*'` |
| Inside the image | `docker run --rm --entrypoint cat <image> /app/contract/openapi.yaml` |

CI verifies two of the three on every release: the `smoke` job reads the in-image copy
back out of the candidate image, and the `publish` job downloads the Release assets back
from the API; both are checked against the anchor committed at the tag (currently
`9c27428a293dfc033439074084776564ff22a26ec3220ac92602fc49d38593b9`).

**Vendoring procedure** (`contract/GOVERNANCE.md` "Consumers"):

1. Pick a tag, never `latest`.
2. Fetch `openapi.yaml` and `openapi.yaml.sha256` from that same tag, by any route. The
   anchor from the git tag is the strongest choice because registry tags and Release
   assets are mutable and a commit is not.
3. Verify before reading the content: `sha256sum -c openapi.yaml.sha256` from the
   directory holding both files.
4. Commit the document and the anchor together.
5. Re-run the verification in your own test suite.
6. Record the tag you vendored from beside the files.

**For maintainers.** `contract/openapi.yaml` and its `.sha256` are generated, never
hand-edited. Anything that moves the document (a response model, a `responses=`
declaration, a field description, a FastAPI upgrade) is followed by
`uv run python -m scripts.export_contract`; `tests/test_contract_export.py` is red until
it is. A shape change also means bumping `CONTRACT_VERSION`, adding a golden fixture under
`tests/golden/` (older ones are retained, never edited) and noting the change for
consumers. `.github/pull_request_template.md` is the short form of that checklist.

---

## Rate limits

None, beyond `/extract` admission control (one extraction in flight, queue depth 1, a
50 MiB queued-upload budget; refusal is 429 `busy` with no `Retry-After`). `/retrieve` and
`/search` have no throttle of any kind, and the SearXNG companion ships with its own
limiter off because it would refuse Forage's client. Concurrency is bounded only by the
container's resources, which is one more reason the port must stay on a private network.
