<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: architecture, dependencies
  required_sections:
    - "Endpoints" or "Commands" or "Interface"
  skip_if: no-api
-->
# API_GUIDE.md

> **TEMPLATE_INTENT:** Document API endpoints, CLI commands, or library interface. The external contract.

> Last updated: 2026-09-13
> Updated by: Claude (seed-project)

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
currently **1.2.0** (`pipeline/contract.py`). It appears on `/health`, on `/metrics`, and
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
| POST | `/search` | Web search via SearXNG, every result sanitised | `SearchRequest` (JSON) | `SearchResponse` | `num_results` 1..20; 10 s SearXNG timeout | SearXNG must be reachable |
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
| `POST /search` | 200, 422 | 422 is a SearXNG failure or a schema-validation body |
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
| `capabilities` | dict of str to int | `{"search_sanitization": 1}` when the model is loaded (or break-glass advertising is armed), otherwise `{}` |
| `contract_version` | str | `1.2.0`; the compatibility signal |
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
| `trusted_domains` | list of str | `[]` | | Exact-host match. Trusted content skips Prompt Guard entirely (`skipped_trusted`) |
| `verified_domains` | list of str | `[]` | | Higher base score; fail-open if the model is absent |
| `blocked_domains` | list of str | `[]` | | Merged with `config.yaml` `seed_blocklist`; refused with `blocked_domain` |
| `promptguard_threshold` | float | 0.85 | 0.0..1.0 | Classifier score at or above which content is quarantined |
| `promptguard_fail_closed` | bool | `true` | | When the model is absent: `true` quarantines standard and untrusted content, `false` allows it with a trust penalty |

```bash
curl -s -X POST http://127.0.0.1:8020/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/article", "extract_mode": "summary"}'
```

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
`blocked_domain`, `fetch_timeout`, `fetch_error`, `content_too_large`. `reason` echoes the
requested URL, and `private_ip` echoes the resolved address (`contract/GOVERNANCE.md`
ruling (d)). Quarantined results are never cached; neither are `untrusted` or `blocked`
tiers.

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

Request fields (`SearchRequest`):

| Field | Type | Default | Bounds | Notes |
|---|---|---|---|---|
| `query` | str | required | non-empty | |
| `num_results` | int | 5 | 1..20 | Forage asks SearXNG for up to `min(2 * num_results, 20)` candidates and scans at most 20 |
| `promptguard_fail_closed` | bool | `true` | | When the model is absent: `true` withholds results (`omitted_by_reason.promptguard_unavailable`), `false` returns them marked `suspicious` and counts them in `unscanned_results` |

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
| `results` | list of `{title, url, snippet, engine, content_kind, date, suspicious}` | `suspicious: true` means the structural scan flagged it, the classifier scored above 0.5, or it was returned unscanned under fail-open. Title is at most 512 characters, URL 2048, snippet 2000. `content_kind` (added in `1.2.0`) is `snippet` or `chunk` and nothing else. `date` (added in `1.2.0`) is a strict `YYYY-MM-DD` calendar date or `null` — anything a provider sends that is not one becomes `null`, so it never needs parsing defensively |
| `omitted_results`, `omitted_by_reason` | int, dict of str to int | How many candidates were withheld and why; keys are only ever `invalid_url`, `structural_blocked`, `injection_detected`, `promptguard_unavailable`, and only non-zero counts appear |
| `unscanned_results`, `promptguard_unavailable` | int, bool | Non-zero or `true` means results came back without the ML scan; treat the whole response as unscanned evidence |
| `unresponsive_engines` | list of str | Passed through from SearXNG; a partial answer, not an error |
| `request_id`, `query` | str, str | Correlation and echo |

Failures are 422 with `{"error", "reason", "request_id"}`. Which code you get depends on
the **configured chain**, not on which backend failed. A chain of exactly one provider
named `searxng` — the default — keeps the legacy pair: `searxng_error` (SearXNG answered
non-2xx; `reason` carries the status as `http_<code>`) or `searxng_unavailable`
(connection refused, DNS, timeout, an oversized body, bad JSON; `reason` carries the
scheme, host and port of the configured URL — userinfo stripped — and a closed `detail`
token, never exception text). Any other chain refuses with `search_unavailable` (added in
`1.2.0`), whose `reason` is the closed pair `<provider_name>: <failure_class>` and carries
no endpoint at all. One request, 10 s timeout, no retries.

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
| `content_too_large` | 422 (and the unreachable 413) | `/retrieve`, `/extract` | Response body over 10 MiB (`/retrieve`); upload over 50 MiB (`/extract`) |
| `content_too_large_to_classify` | 422 | `/extract` | Extracted text exceeds the Prompt Guard chunk budget |
| `extraction_failed` | 422 | `/extract` | Parser failure |
| `fetch_error` | 422 | `/retrieve` | Any other fetch failure, including more than 5 redirects |
| `fetch_timeout` | 422 | `/retrieve` | 30 s fetch timeout |
| `invalid_filename` | 422 | `/extract` | Over 255 characters or no basename |
| `invalid_mime_hint` | 422 | `/extract` | Over 255 characters |
| `invalid_request_id` | 422 | `/extract` | Over 128 characters or disallowed characters |
| `invalid_url` | 422 | `/retrieve` | Bad scheme, no hostname, or DNS failure |
| `pdf_encrypted` | 422 | `/extract` | Encrypted PDF |
| `pdf_no_text` | 422 | `/extract` | No extractable text; OCR is not supported |
| `private_ip` | 422 | `/retrieve` | Resolves to a private or reserved address, `localhost` or a `.local` name |
| `search_unavailable` | 422 | `/search` | The configured provider chain failed and is not a lone `searxng`; `reason` is `<provider_name>: <failure_class>`. Added in `1.2.0` |
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
added `SearchResult.content_kind` and `SearchResult.date` and the `search_unavailable`
error code. Do not
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
`7d297dfea6b329c361c34d5a6633fbac0884e1df59294cf70ba4c9e86fb226d1`).

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
