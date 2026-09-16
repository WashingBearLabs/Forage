<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: security, architecture
  required_sections:
    - "Security Overview"
  skip_if: no-auth
-->
# SECURITY.md

> **TEMPLATE_INTENT:** Document authentication, authorization, and secrets management. Security architecture reference.

> Last updated: 2026-09-13
> Updated by: Claude (seed-project)

---

## Security Overview

Forage is an extraction and telemetry service that fetches, parses, and scans web content on behalf of LLM agents. It is **not a trust boundary**: it reports what it found and how confident it is (`trust_score`, `injection_detected`, `promptguard_state`, and the rest of the wire signals), and the calling agent's own security layer owns every trust decision. Treat every Forage response as evidence, never as an all-clear (`README.md` "not a trust boundary"; `docs/configuration.md`: "A `healthy` Forage is not a promise that the content it returned is safe.").

Two facts shape the whole threat model:

1. **Outbound fetching is the job**, so server-side request forgery is the standing threat. Anyone who can reach port 8020 can make Forage fetch a URL of their choosing; the defences in `url_validator.py` and `pipeline/stage5_url_audit.py` decide which URLs are refused.
2. **There is no authentication on any route.** Eight paths are reachable, all unauthenticated: `GET /health`, `GET /metrics`, `POST /search`, `POST /retrieve`, `POST /extract` (404 until `extract_route_enabled: true`), plus FastAPI's `/openapi.json`, `/docs` (an interactive client for an SSRF-capable service), and `/redoc` (`/docs/oauth2-redirect` also answers but is inert). Network placement is the operator's first control; everything below is defence in depth behind it.

**Auth provider:** none, by design (see "Authentication and Authorization").
**Secrets management:** runtime environment variables only; no secret store, no build-time secrets.
**Encryption:** outbound fetches use TLS with `ssl.create_default_context()` verification, preserved even when the connection is IP-pinned; inbound HTTP on 8020 is plaintext and must stay on a private network. Whether the Valkey and SearXNG links are TLS-protected is not stated anywhere in the repo (see "Known Limitations and Open Questions").

The security architecture at a glance:

| Control | Where it lives | Pinned by |
|---|---|---|
| Network placement (loopback-only publish) | `compose/minimal.yml`, `compose/full.yml`, `README.md` | `tests/test_compose_fragments.py` |
| SSRF defence | `url_validator.py`, `pipeline/stage5_url_audit.py` | `tests/test_url_validator.py`, `tests/test_stage5_url_audit.py` |
| Prompt-injection signalling and quarantine | `pipeline/stage1_extraction.py`, `pipeline/stage1_pdf.py`, `pipeline/stage2_structural.py`, `pipeline/stage3_promptguard.py`, `pipeline/stage4_structuring.py` | `tests/test_stage1_extraction.py`, `tests/test_stage1_pdf.py`, `tests/test_stage2_structural.py`, `tests/test_stage3_promptguard.py`, `tests/test_orchestrator.py` |
| Input bounds and process isolation | `models.py`, `pipeline/extraction_limits.py`, `pipeline/stage1_upload.py`, `pipeline/pdf_subprocess.py`, `retrieval_app.py` | `tests/test_app.py`, `tests/test_contract_errors.py`, `tests/test_models.py` |
| Secrets hygiene | `Dockerfile` (zero `ARG`), `cache.py`, `model_fetcher.py`, `docker-entrypoint.sh` | `tests/test_dockerfile.py`, `tests/test_cache.py`, CI `secret-grep` |
| Supply-chain integrity | `uv.lock`, `Dockerfile`, `weights_manifest.json`, `contract/openapi.yaml.sha256`, `.github/workflows/ci.yml` | `tests/test_dependency_lock.py`, `tests/test_model_fetcher.py`, `tests/test_contract_export.py`, `tests/test_ci_workflow.py` |
| Honest degradation | `retrieval_app.py` `/health` | `tests/test_app.py`, CI `smoke` (`contract_smoke.py`) |

The vulnerability-disclosure policy, supported versions, and the in-scope list live in the repo-root `SECURITY.md`; this document does not restate them. Service topology is in `kit_tools/arch/SERVICE_MAP.md`, the build and publish pipeline in `kit_tools/arch/INFRA_ARCH.md`, and the pipeline stages in `kit_tools/arch/CODE_ARCH.md`.

---

## Authentication and Authorization

### There is none, by design

`retrieval_app.py` contains no auth dependency, API-key scheme, bearer check, CORS middleware, or trusted-host filter (a grep for `Depends|APIKey|CORSMiddleware|Authorization|HTTPBearer|OAuth2|allow_origins|TrustedHost` returns zero hits). The only two middlewares, `DocumentSizeLimitMiddleware` and `ExtractionAdmissionMiddleware`, gate on `scope["path"] == "/extract"` and enforce resource bounds, not identity. The FastAPI app is constructed with default `docs_url`, `redoc_url`, and `openapi_url`, so the interactive documentation is exactly as reachable as the routes it documents; there is no configuration switch to turn it off because "turning the contract off is not a substitute for a private network" (`docs/configuration.md`). No security headers (HSTS, CSP, X-Frame-Options) and no CORS policy are configured, and none are expected for a JSON API on a private network.

`kit_tools/AGENT_README.md` makes this a standing rule: do not add half-measures that read as auth without being it. `docs/configuration.md` gives the reasoning: a partial auth layer would invite exactly the "it's protected" assumption that a private network makes unnecessary.

### The control that replaces it: network placement

Forage must run where only its consumer can reach it: a Docker bridge network, a loopback-bound host port, or a VPN. Port 8020 is never published to the internet. Concretely:

- Both compose fragments publish exactly one port, `"127.0.0.1:8020:8020"`, and nothing else; SearXNG has no `ports:` block at all. `tests/test_compose_fragments.py::test_every_published_port_binds_to_loopback` and `::test_only_forage_publishes_anything` keep it that way.
- The documented `docker run` form is `-p 127.0.0.1:8020:8020` (`README.md`, `CLAUDE.md`, `kit_tools/docs/LOCAL_DEV.md`).
- Inside the container uvicorn binds `0.0.0.0:8020`; the loopback restriction is the host-side publish, not the process. Do not mistake the `EXPOSE 8020` line for a firewall.
- The container runs as the non-root user `poppy` (`Dockerfile`; `tests/test_dockerfile.py::TestRuntimeShapePreserved::test_runs_as_a_non_root_user`).

Anyone who can reach the port is, by design, able to use Forage as a request proxy; the root `SECURITY.md` lists this under "Not vulnerabilities here".

### If Forage must ever be exposed

Terminate authentication *in front of* Forage, in a reverse proxy, a service mesh, or the consumer itself, and keep Forage's own port private (`docs/configuration.md` "Deployment posture"). Do not add it inside the service.

### Reporting a vulnerability

Use GitHub private vulnerability reporting (verified enabled 2026-09-11). There is no security email and no PGP key. The root `SECURITY.md` documents the acknowledgement window, credit policy, supported versions, and the fix path: a PR behind the six CI gates, a new tag, and an advisory alongside; wire-moving fixes follow `contract/GOVERNANCE.md` Example 6.

---

## SSRF Defences

There are two entry points: `validate_url(url, blocked_domains)` in `url_validator.py`, and `fetch_url(...)` in `pipeline/stage5_url_audit.py`. `/retrieve` validates once before the cache lookup (`pipeline/orchestrator.py`) and again inside every fetch hop; `/search` validates every SearXNG result URL and counts failures under `omitted_by_reason["invalid_url"]` rather than fetching them.

### `validate_url`

- **Scheme allowlist.** Only `http` and `https`; anything else raises `ValueError`, surfaced as `invalid_url`.
- **Hostname rejection.** `_BLOCKED_HOSTNAMES = {"localhost"}` and `_BLOCKED_SUFFIXES = {".local"}`, matched case-insensitively.
- **Reserved-range rejection** (`_is_private_ip`). Fifteen IPv4 networks: `0.0.0.0/8`, `10.0.0.0/8`, `100.64.0.0/10` (carrier-grade NAT), `127.0.0.0/8`, `169.254.0.0/16` (link-local, which covers the `169.254.169.254` cloud-metadata endpoint), `172.16.0.0/12`, `192.0.0.0/24`, `192.0.2.0/24`, `192.168.0.0/16`, `198.18.0.0/15`, `198.51.100.0/24`, `203.0.113.0/24`, `224.0.0.0/4`, `240.0.0.0/4`, `255.255.255.255/32`. Six IPv6 networks: `::1/128`, `fe80::/10`, `fc00::/7`, `::ffff:0:0/96`, `2001:db8::/32`, `ff00::/8`. IPv4-mapped IPv6 addresses are unwrapped and checked against the IPv4 list; `0.0.0.0` and `::` are rejected explicitly.
- **Unparseable is private.** An address that `ipaddress` cannot parse is treated as private, so the check fails closed.
- **Every resolved address is checked.** `socket.getaddrinfo` runs off the event loop; if *any* returned address is private the whole URL is rejected. DNS failure or an empty result is `invalid_url`.
- **Exact-host blocklist.** The request's `blocked_domains` are merged with `config.yaml` `seed_blocklist` (currently `[]`) and matched case-insensitively as exact hosts; a hit raises `BlockedDomainError`, surfaced as `blocked_domain` (422), before any HTTP request is made.

### `fetch_url`

- **No automatic redirects.** The `httpx.AsyncClient` is created with `follow_redirects=False` and `verify=ssl.create_default_context()`.
- **Per-hop revalidation with IP pinning.** Each hop re-runs `validate_url`, then rewrites the netloc to the validated IP, sends `Host: <hostname>`, and passes `extensions={"sni_hostname": hostname}` so certificate verification still applies. A second DNS resolution can never happen between check and connect, which closes the rebinding race.
- **Redirect budget.** `DEFAULT_MAX_REDIRECTS = 5`, enforced by `TooManyRedirectsError`; relative `Location` headers are resolved with `urljoin`; every hop is appended to `redirect_chain`, and `domain_changed_on_redirect` is flagged and costs `-0.1` trust (`_REDIRECT_DOMAIN_CHANGE_PENALTY` in `pipeline/stage4_structuring.py`).
- **Time and size.** `DEFAULT_TIMEOUT = 30.0` seconds per request; `DEFAULT_MAX_CONTENT_BYTES` of 10 MiB enforced both by a `Content-Length` fast-reject and an incremental cap on the streamed body (`ContentTooLargeError`). Redirect-hop bodies are never buffered.
- **User-Agent rotation.** One UA per top-level fetch, chosen with `random.choice` from `config.yaml` `user_agents` (five browser strings) or `DEFAULT_USER_AGENTS`.

### The private-IP echo (documented caveat)

A `/retrieve` refusal `reason` echoes `URL '<url>' resolves to private IP <ip>` verbatim (`url_validator.py` line 174, passed through by `pipeline/orchestrator.py`). Forage is therefore a DNS oracle for its own network to anyone who can reach it. This was ruled a documented caveat, not a defect, because redacting it is a wire change (`contract/GOVERNANCE.md` ruling (d); root `SECURITY.md` "Not vulnerabilities here").

### Tests that pin each behaviour

| Behaviour | Test |
|---|---|
| Reserved IPv4/IPv6 ranges, unparseable-is-private, `::` | `tests/test_url_validator.py::TestIsPrivateIP` (`test_private_ipv4`, `test_private_ipv6`, `test_unparseable_ip_is_private`, `test_zero_ipv6`) |
| Any private address among several rejects the URL | `TestRFC1918Rejection::test_mixed_ips_rejected_if_any_private` |
| `localhost` and `.local` regardless of case or nesting | `TestHostnameRejection::test_localhost_uppercase_rejected`, `::test_nested_local_domain_rejected` |
| Exact-host blocklist | `TestBlockedDomains` |
| Scheme allowlist, DNS failure | `TestEdgeCases::test_unsupported_scheme_raises_valueerror`, `::test_dns_failure_raises_valueerror` |
| Private IP reached mid-fetch or via redirect | `tests/test_stage5_url_audit.py::TestRFC1918DuringFetch`, `TestRedirectTracking::test_redirect_to_private_ip_rejected` |
| Redirect budget and relative `Location` | `TestRedirectTracking::test_too_many_redirects`, `::test_relative_redirect_resolved` |
| Domain-change flag, UA rotation, 30 s default | `TestDomainChangeDetection`, `TestUserAgentRotation`, `TestTimeoutEnforcement::test_default_timeout_is_30` |
| Byte cap, unbuffered redirect bodies | `TestStreamingByteCap::test_content_length_fast_reject`, `::test_redirect_hop_large_body_not_buffered` |
| End-to-end refusal through the orchestrator | `tests/test_orchestrator.py::test_retrieve_private_ip_raises_pipeline_error`, `::test_retrieve_blocked_domain_raises_pipeline_error` |

---

## Prompt-Injection Signalling

Forage never decides whether content is safe; it produces signals and, for the clear cases, quarantines the body. Three pipeline stages contribute. Stage numbering and data flow are in `kit_tools/arch/CODE_ARCH.md`; this section covers only the security-relevant behaviour.

### Stage 1: normalisation removes the cheap tricks

`pipeline/stage1_extraction.py` strips `_DANGEROUS_TAGS` (`script`, `style`, `iframe`, `meta`, `link`, `object`, `embed`, `form`, `svg`) and HTML comments before extracting text, then `_normalize_text` applies NFC, deletes `_INVISIBLE_CHARS` (zero-width space, non-joiner, joiner, and word joiner; the right-to-left override; the byte-order mark; the soft hyphen), and collapses whitespace. It produces two texts: `raw_text` (everything, for scanning) and `main_content` (trafilatura's extraction, for the agent), so an injection hidden outside the main article is still scanned. For PDFs, `pipeline/stage1_pdf.py` strips document metadata, never evaluates `/JS` or `/AA` actions, and refuses encrypted files.

### Stage 2: deterministic regex, before any ML

`pipeline/stage2_structural.py` runs seven pattern categories and always runs, model or no model.

| Category | Verdict | What it catches |
|---|---|---|
| `instruction_override` | `blocked` | "ignore previous/prior/above", "disregard ... instructions", "new directive/instruction/task/objective", a bracketed `SYSTEM` tag, `<system>`, `---INSTRUCTIONS---` |
| `authority_impersonation` | `blocked` | bracketed `admin` or `poppy` tags, line-start `assistant:`, `POPPY:`, or `System:`, "user with elevated" |
| `prompt_boundary` | `blocked` | fenced `system` or `instructions` code blocks, the `im_start` and `endoftext` chat-template delimiters |
| `encoded_payload` | `suspicious` | a base64 island of 40 or more characters, `rot13`, four or more `\xNN` escapes |
| `suspicious_url` | `suspicious` | `data:` URIs, `javascript:`, `href` or `src` pointing at an RFC1918 literal |
| `exfil_beacon` | `suspicious` | a markdown image whose URL carries double-brace, `${`, or `%7B` templating |
| `envelope_breakout` | `suspicious` | any `<`, `&lt;`, `&#60;`, or `&#x3c;` spelling of the `retrieved_content`, `retrieval_note`, `retrieval_warning`, or `retrieval_cache_note` tags |

A blocking hit yields verdict `blocked` and quarantine. Each suspicious hit costs `-0.15` trust, capped at `-0.45` (`penalty = max(-0.45, -0.15 * suspicious_count)`); blocking always overrides suspicious. `tests/test_stage2_structural.py` (78 tests) has a class per category plus `TestMixedContent::test_blocking_overrides_suspicious` and `::test_penalty_cap`.

### Stage 3: Llama Prompt Guard 2

`promptguard/classifier.py` loads `meta-llama/Llama-Prompt-Guard-2-22M` (a DeBERTa-v3 sequence classifier) on CPU with `use_safetensors=True`. Text is chunked at `MAX_SEQ_LEN = 512` tokens with `CHUNK_OVERLAP = 64`, up to `MAX_PROMPTGUARD_CHUNKS = 64`; over budget raises `PromptGuardBudgetExceededError` rather than silently classifying a prefix. `pipeline/stage3_promptguard.py` applies `DEFAULT_THRESHOLD = 0.85` (`config.yaml` `promptguard_threshold`; overridable per `/retrieve` request within 0.0 to 1.0): a score above threshold is `injection_detected` with `INJECTION_PENALTY = -0.5`.

Two policy branches matter for security:

- **Trusted-tier skip.** `trust_tier == "trusted"` skips inference entirely (`promptguard_state: skipped_trusted`). Marking a domain trusted means opting it out of the ML scan.
- **Model absent, fail closed.** With `promptguard_fail_closed=True` (the default), `standard` and `untrusted` content is blocked (`unavailable_blocked`, penalty `-0.5`); with `fail_closed=False`, or for the `verified` tier, it is allowed with `-0.1` (`unavailable_allowed`). `/extract` ignores request policy and always runs as `TrustTier.UNTRUSTED` with `promptguard_fail_closed=True`.

### Quarantine

When stage 2 says `blocked` or stage 3 says `injection_detected`, `finalize_quarantine` in `pipeline/stage4_structuring.py` replaces `body` with the fixed string "Content quarantined due to potential prompt injection." and `injection_spans` with a single stable diagnostic (`structural_injection_detected`, `promptguard_injection_detected`, or `promptguard_unavailable`). Hostile text never rides the response, and quarantined results are never cached. `tests/test_orchestrator.py::test_post_extract_structural_block_is_content_free` and `::test_post_extract_promptguard_block_is_content_free` pin this.

### Trust score

`pipeline/stage4_structuring.py` composes `trust_score` from a per-tier base in `_BASE_SCORES` (`trusted` 0.95, `verified` 0.85, `standard` 0.70, `untrusted` 0.40, `blocked` 0.0) plus the stage-2 penalty, the stage-3 penalty, and `-0.1` for a redirect that changed domain, clamped to 0 to 1. The tier is resolved per request by `orchestrator._resolve_request_trust_tier` in the order `blocked_domains`, then `trusted_domains`, then `verified_domains`, else `standard`; `config.yaml` `news_domains` affects cache TTL only. The wire fields a consumer should read are `injection_detected`, `injection_spans`, `structural_flags`, `stage2_verdict`, `stage3_verdict`, `promptguard_state` (one of `scanned`, `skipped_trusted`, `structural_blocked`, `unavailable_blocked`, `unavailable_allowed`), `trust_score`, `trust_tier`, `redirect_chain`, and `domain_changed_on_redirect`; `/search` adds `omitted_by_reason`, `unscanned_results`, and `promptguard_unavailable`. The vocabulary is fixed in `pipeline/contract.py`.

### Observation: `/search` scans at the hard default

The `/search` handler makes one stage-3 pass over the title, URL, and snippet of each result at `standard` tier and does not pass `config.yaml`'s `promptguard_threshold`, so it always scans at the hard default 0.85 even when the operator has changed the threshold that `/retrieve` and `/extract` honour. The architecture exploration recorded this as an observation; nothing in the repo documents it as intentional, and no decision has been made.

### Tests

`tests/test_stage1_extraction.py::TestDangerousElementStripping`, `::TestUnicodeHandling`; `tests/test_stage1_pdf.py::TestMetadataStripping`, `::TestEncryptedPDF`, `::TestJavaScriptDisabled`; `tests/test_stage2_structural.py` (all classes); `tests/test_stage3_promptguard.py::TestTrustedDomainSkip`, `::TestModelNotLoaded`, `::TestChunking`; `tests/test_orchestrator.py::test_retrieve_classifier_absent_fail_closed_reports_unavailable_blocked`, `::test_search_classifier_unavailable_fails_closed`, `::test_post_extract_uses_fixed_untrusted_policy`.

---

## Input Validation and Resource Bounds

### Request models

`models.py` (Pydantic v2) bounds every request field: `RetrieveRequest.url` has `min_length=1`, `extract_mode` is a `Literal`, `cache_ttl_hours` is 0 to 8760, `promptguard_threshold` is 0.0 to 1.0; `SearchRequest.query` has `min_length=1` and `num_results` is 1 to 20. Response `content_type` validators restrict to `html`, `pdf`, or `text`. Pinned by `tests/test_models.py::TestRetrieveRequest` and `::TestSearchRequest`.

### The `/extract` release gate

`config.yaml` ships `extract_route_enabled: false`. While it is false both `ExtractionAdmissionMiddleware` and the route itself answer `404 {"detail": "Not Found"}`; opening the route requires a restart. `tests/test_app.py::test_extract_release_gate_returns_404_when_disabled`.

### Upload ceilings

`pipeline/extraction_limits.py` defines hard ceilings that `config.yaml` may lower but never raise: `MAX_INPUT_BYTES` 50 MiB, `MAX_PDF_PAGES` 500, `MAX_CHILD_CPU_SECONDS` 20, `MAX_CHILD_ADDRESS_SPACE_BYTES` 384 MiB (Linux `RLIMIT_AS`), `MAX_EXTRACTION_WALL_SECONDS` 90, `MAX_EXTRACTED_OUTPUT_BYTES` 2 MiB, and a character ceiling of `(512 - 64) * 64 * 4 = 114688` derived from the PromptGuard chunk budget. `extraction_settings_from_config` rejects booleans-as-integers and out-of-range values with `ExtractionConfigurationError`.

### The upload path

In `retrieval_app.py`, `DocumentSizeLimitMiddleware` counts ASGI body bytes as they arrive and does **not** trust `Content-Length`; `_spool_upload` spools to a `0600` temp file under a second cap and always unlinks it; `_sanitize_upload_metadata` bounds `filename` and `mime_hint` to 255 characters, constrains `request_id` to `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`, strips control characters, reduces `filename` to a basename, and rejects `""`, `.`, and `..`. `UploadProvenance` in `models.py` documents that `filename` and `mime_hint` are display-only and never used as filesystem paths.

### Content-type detection and process isolation

`pipeline/stage1_upload.py` decides the format from bytes, not from the caller: `%PDF-` magic wins; otherwise the body must decode as strict UTF-8, contain no NUL byte, and have at most 5 % non-whitespace control characters (`_MAX_CONTROL_CHARACTER_RATIO = 0.05`); `mime_hint` is explicitly discarded (`del mime_hint`). PDF parsing runs in a spawned, killable subprocess (`pipeline/pdf_subprocess.py`) under `RLIMIT_CPU`, `RLIMIT_AS`, and a `setitimer` wall clock; the result comes back over a length-framed JSON pipe capped at `max_ipc_result_bytes`, and parser exceptions map to fixed status tokens so document-derived text never crosses the pipe.

### Admission control (the only rate limiting)

| Route | Limit | Over capacity |
|---|---|---|
| `POST /extract` | `extraction_concurrency=1`, `admission_queue_depth=1` (configurable 0 to 4), `max_queued_upload_bytes` 50 MiB (`ExtractionAdmissionController`) | `429 {"error": "busy"}` |
| PromptGuard inference, all routes | `classification_semaphore = asyncio.Semaphore(classification_concurrency=1)` | queued |
| `POST /retrieve`, `POST /search`, `GET /health`, `GET /metrics` | none | not applicable |

Resource exhaustion by a caller who is allowed to call is a documented non-vulnerability (root `SECURITY.md`). Note also that the companion `forage-searxng` ships `limiter: false`, because a working SearXNG limiter refuses the JSON API Forage depends on (`searxng/config/settings.yml`; `kit_tools/docs/GOTCHAS.md`).

### The documented-but-unreachable 413

`DocumentSizeLimitMiddleware` emits `413 {"error": "content_too_large"}`, but FastAPI's form parser converts it into `400 {"detail": "There was an error parsing the body"}` on `/extract`. The byte cap is intact; only the status differs. The 413 is documented in the contract, that documentation carried no version bump, and *correcting* it is a MAJOR change (`contract/GOVERNANCE.md` ruling (a2); `kit_tools/docs/GOTCHAS.md`). Pinned by `tests/test_contract_errors.py::test_extract_oversized_upload_actually_receives_400`, `::test_extract_413_body_is_mirrored_and_carries_no_request_id`, and `tests/test_app.py::test_extract_asgi_size_limit_ignores_content_length`.

`/extract` error reasons are fixed, content-free strings (`DOCUMENT_FAILURE_REASONS`). `/retrieve` `fetch_error` is the one reason that still interpolates the caught exception; see "Known Limitations and Open Questions". `/search`'s `searxng_unavailable` no longer does — since `search-provider-abstraction` US-002 it carries a closed provider `detail` token plus the userinfo-stripped scheme, host and port of `SEARXNG_URL`. The full error vocabulary is in `kit_tools/arch/patterns/ERROR_HANDLING.md`.

---

## Secrets Management

### Model

Twelve-factor, runtime environment only. There is no Vault or OpenBao client, no secret store, and no configuration database (`README.md`; the comment in `retrieval_app.py` where `VALKEY_URL` is read). `docker-entrypoint.sh` is `set -euo pipefail` followed by `exec "$@"` and prints nothing, deliberately, so a `VALKEY_URL` password can never reach container logs at start. Rotation is "change the variable and restart". `docs/configuration.md` "Credential handling" is the operator reference: use an env file or a secret store, never inline `-e` (shell history, `ps`); remember that `docker inspect` exposes the environment to anyone with socket access. Variable-by-variable detail is in `kit_tools/docs/ENV_REFERENCE.md`.

### Secrets inventory

| Variable | Purpose | Required | Handling |
|---|---|---|---|
| `HF_TOKEN` | Gated Hugging Face download of the PromptGuard weights | No; absent is the supported degraded mode | Read at runtime by `model_fetcher.py`; never a build argument |
| `FORAGE_MIRROR_TOKEN` | Read-only credential for the private OCI weights mirror | No | Passed to `oras` on stdin, never argv, log, or disk |
| `VALKEY_URL` | Content-cache connection string; may embed a password (`redis://:PASSWORD@host:6379/4`) | No; fully unset means the in-memory cache | Read once at start; never logged (closed vocabulary below) |
| `SEARXNG_SECRET` | The companion SearXNG's own secret | Yes for the companion; `${SEARXNG_SECRET:?...}` in compose, no baked default | Never read by Forage itself |

Related but not a secret: `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` (alias `POPPY_RETRIEVAL_LEGACY_CAPABILITY`). Exactly `"1"` forces `/health.capabilities` to advertise `search_sanitization` during a consumer transition; `status`, `degraded_reasons`, and `promptguard_loaded` stay honest, and a WARNING is logged every boot (`docs/configuration.md` "Break-glass").

### No secret enters the image build (CLAUDE.md invariant 2)

`Dockerfile` declares **zero** `ARG` instructions. A build ARG is not a secret: `docker history --no-trunc` reads it back out of any registry the image reaches. Two mechanical guards keep it that way:

1. `tests/test_dockerfile.py::TestNoSecretEntersTheBuild`: `test_the_build_takes_no_arguments_at_all` (the absolute), `test_no_arg_declares_a_secret_name`, `test_no_env_declares_a_secret_name`, `test_no_instruction_assigns_a_secret_valued_variable`, `test_no_hf_token_in_any_instruction`, `test_no_model_bake_step`, `test_no_token_shaped_literal_anywhere`. The absolute is asserted because it is the part that survives review: once "no ARG" stops being true, the next one only has to look as harmless as the last.
2. CI's `secret-grep` job runs `docker history --no-trunc` on the exact built artifact and greps for `HF_TOKEN` and `hf_[A-Za-z0-9]{20,}` (scope: layer metadata, not file contents); the `publish` job re-greps the *published* image config for the same patterns after push. `tests/test_ci_workflow.py::TestSecretGrepJob` and `::TestPublishJob::test_publish_greps_the_published_config_for_secrets` pin both.

The image is deliberately single-stage so that `docker history` covers everything (`tests/test_dockerfile.py::TestBaseImagePin::test_single_from_instruction`). Poppy's in-tree `services/retrieval/Dockerfile` still carries `ARG HF_TOKEN`; never push an image built from that file.

### Closed log vocabularies (CLAUDE.md invariant 6)

`cache._closed_vocabulary_reason` maps every Valkey failure to one of `connect_failed`, `operation_failed`, or `timeout` and never emits `str(exc)` or the URL. `tests/test_cache.py::TestReconnect::test_connect_failure_never_logs_url_or_secret` drives both the cache path and a real lifespan with `redis://:hunter2-startup-password@...` and asserts the password appears nowhere; `tests/test_app.py::test_no_selection_path_logs_the_valkey_url` covers five start modes. `model_fetcher.py` uses the same pattern (`http_401`, `timeout`, `io_failed`, `fetch_failed`, `pull_failed`, `oras_missing`). Any new startup or cache code must preserve this; `kit_tools/arch/patterns/LOGGING.md` carries the general convention.

### Repository hygiene

- `.gitignore` excludes `.env` and `compose/.env` because they carry live tokens; the compose fragments use bare `- HF_TOKEN` pass-through so an unset variable stays unset.
- `.gitleaksignore` holds exactly one entry, `ba73b078...:tests/test_stage2_structural.py:generic-api-key:170`: the synthetic `Token: abc123def456` literal in `test_short_base64_no_match`, which exists to prove that short base64-like strings do *not* trigger stage 2. Triaged 2026-09-08 at the public flip; the full-history scan record (gitleaks 8.30.1, 88 commits, one false positive) is `docs/bootstrap-scan.txt`.
- GitHub's own secret scanning and push protection were enabled on the repository at the public flip.
- `tests/conftest.py` clears `HF_TOKEN`, `HF_HOME`, `FORAGE_MODEL_REVISION`, `FORAGE_WEIGHTS_MIRROR`, `FORAGE_MIRROR_TOKEN`, and `VALKEY_URL` before every test, so a developer's shell credentials cannot change which branch runs.

### Adding a new secret

1. Read it from the environment at runtime, once, in the place that needs it; never through a build `ARG`, an `ENV` default, or a config file.
2. Give every failure path a fixed reason string; add a test in the style of `test_connect_failure_never_logs_url_or_secret` that asserts the value never reaches a log record.
3. Add the variable to `tests/conftest.py`'s cleared list, to `docs/configuration.md`, and to `kit_tools/docs/ENV_REFERENCE.md`.
4. If it must reach a subprocess, pass it on stdin as `FORAGE_MIRROR_TOKEN` is, never on argv.

---

## Supply Chain Integrity

### Dependencies

`uv.lock` is committed and the image installs with `uv sync --locked --no-dev --no-install-project`. Torch resolves from the CPU index (`https://download.pytorch.org/whl/cpu`, pinned in `pyproject.toml` under `[[tool.uv.index]]`; `torch==2.14.0+cpu`); the CI `lint` job greps both `uv.lock` and the synced environment for `nvidia-` so a re-lock cannot silently drag in CUDA wheels. `tests/test_dependency_lock.py::test_lock_file_is_committed`, `::test_lock_contains_no_cuda_wheels`, `::test_pyproject_pins_the_cpu_torch_index`.

### Image pins

The base is `python:3.12-slim@sha256:78387bc3...` (digest, with the resolved tag recorded in the adjacent comment); `uv` is `0.9.28@sha256:59240a65...`; `oras` 1.3.4 is downloaded over HTTPS with a per-architecture sha256 verified by `sha256sum -c`, the architecture coming from `dpkg --print-architecture` inside the `RUN` rather than an `ARG TARGETARCH` (which would break the zero-ARG invariant); `actionlint` 1.7.12 is sha256-pinned in CI, and every GitHub Action is pinned to a full commit SHA with the version in a trailing comment. `tests/test_dockerfile.py::TestBaseImagePin`, `::TestOrasIsPinnedPerArchitecture`, `::TestLockDrivenInstall`.

### Model weights

Weights are a runtime input, never baked. `model_fetcher.py` pins `DEFAULT_MODEL_REVISION = "11614a155199674a0a95e6602d6ab0417b790ed0"` (override via `FORAGE_MODEL_REVISION`) and verifies downloads against `weights_manifest.json`, an **exact-set** allowlist of five files with sha256 and size. `ALLOWED_SUFFIXES` is `.safetensors`, `.json`, `.txt`, `.model`, so a pickle `.bin` can never be blessed or loaded, belt-and-braces with the loader's `use_safetensors=True`. Symlinks are resolved and containment-checked; a failed set is quarantined for one generation; a mirror tarball is extracted with `tarfile` `filter="data"` into a throwaway root and verified before install; `FORAGE_WEIGHTS_MIRROR` must be a bare lowercase `<registry>/<owner>/<name>` and TLS is non-negotiable. `tests/test_model_fetcher.py` (`TestManifestFailsClosed`, `TestExactSetVerification`, `TestFormatAllowlist`, `TestSymlinkResolution`, `TestQuarantine`, `TestMirrorExtractionIsSafe`, `TestWarmStartTouchesNoNetwork`) and `tests/test_vendor_weights.py`.

### Contract anchor

`contract/openapi.yaml` and `contract/openapi.yaml.sha256` are both generated by `uv run python -m scripts.export_contract`; the committed checksum is the trust root because Release assets and registry tags are mutable and git history is not. The document ships in three places, and two of them are checked rather than promised: the `smoke` job reads `/app/contract/openapi.yaml` back out of the candidate image and verifies it against the anchor and against `/health.contract_version`; the `publish` job downloads the Release assets back from the API and verifies them the same way. Consumers vendor per `contract/GOVERNANCE.md` "Consumers": verify against the anchor from the same tag, never against another copy. `tests/test_contract_export.py`, `tests/test_dockerfile.py::TestTheContractShipsInTheImage`, `tests/test_ci_workflow.py::TestReleaseContractAssets`.

### Publish gates

`.github/workflows/ci.yml` sets top-level `permissions: contents: read`; only the `publish` job holds `packages: write`, logs in with `GITHUB_TOKEN` only, and runs behind `needs: [lint, typecheck, test, build-amd64, secret-grep, smoke]`. The image travels between jobs as an artifact, never a rebuild, and after push the job inspects the published amd64 manifest and asserts its `rootfs.diff_ids` equal the gated tarball's layer for layer (a non-vacuous check), then greps the published config for secret patterns. Builds are reproducible (`SOURCE_DATE_EPOCH` from the commit date plus `rewrite-timestamp=true`; 19 of 19 layers measured identical across two `--no-cache` builds). A green publish therefore proves the published bytes are the bytes that `smoke` executed and `secret-grep` cleared. Failure handling, tag rules, and the withdraw-tag-and-delete-package recovery are in `docs/releases.md` and `kit_tools/docs/CI_CD.md`. `tests/test_ci_workflow.py::TestPermissions`, `::TestPublishJob`.

### Not present

- **Image signing, provenance attestation, and SBOM.** `publish` sets `provenance: false` and `sbom: false`; the workflow comment declares them out of scope "to be revisited with real third-party consumers", and `TestPublishJob::test_publish_ships_no_attestations` pins the current state. No timeline is stated.
- **arm64 execution.** The arm64 leg is built and pushed but never executed by CI (`docs/releases.md` "Architectures"); run `contract_smoke.py` against your own arm64 container before trusting it.
- **Dependency-vulnerability scanning.** No Dependabot, `pip-audit`, or Trivy step was found; the lock is pinned but not audited. This is an observed absence, not a recorded decision.

The `sanitizer_revision` (`pipeline/sanitizer_revision.py`, a sha256 over eight pipeline sources plus `MODEL_ID@revision` plus the threshold) is a drift and cache-invalidation signal, not a supply-chain attestation: the service computes it itself, so it is not tamper-proof.

---

## Honest Degradation (CLAUDE.md invariant 5)

`/health` always returns HTTP 200; the truth is in the body. `status` is `healthy` or `degraded`; `degraded_reasons` is a typed `Literal` list drawn from `promptguard_unavailable` and `cache_unavailable` (an unlisted reason fails response validation); `promptguard_loaded`, `cache_connected`, `cache_backend` (`memory` or `valkey`), `capabilities`, `sanitizer_revision`, and `contract_version` complete the picture. `promptguard_loaded: false` means the ML scan **did not run**, and a consumer must treat standard-tier content as unscanned (`kit_tools/docs/GOTCHAS.md` "PromptGuard model absent"). An empty or unreachable `VALKEY_URL` is configured-and-missing, reported as `degraded`, never a silent fallback to memory.

This is a security property because of the incident it answers: an earlier version reported `healthy` regardless of the model, a fail-closed consumer silently dropped every standard-tier search result (reading it as "no matches"), and nobody noticed for nine days in production. Do not regress it in the name of a cleaner status code. CI enforces it mechanically: the `smoke` job runs the image with no token and `contract_smoke.py` asserts `status: degraded`, `promptguard_unavailable` in `degraded_reasons`, and no `search_sanitization` capability. `tests/test_app.py::test_health_degraded_reports_promptguard_unavailable`, `::test_a_broken_valkey_url_degrades_and_never_falls_back_to_memory`, `::test_health_break_glass_override_unset_withholds_advertisement`, `tests/test_ci_workflow.py::TestSmokeJob`. Operational guidance for reading `/health` and `/metrics` is in `kit_tools/docs/MONITORING.md`.

---

## Security-Relevant Logging and Metrics

Forage has no audit log in the authentication sense; there is no identity to record. What it does log is constrained: the root logger runs at WARNING, so `logger.info` output is invisible in the container (`kit_tools/docs/GOTCHAS.md`), and the failure paths that could touch a credential use the closed vocabularies above. For observing security behaviour prefer `GET /metrics`, which exposes `retrieve.blocked_by_reason`, `retrieve.promptguard_state`, `search.omitted_by_reason`, `search.unscanned_results`, `extraction.busy_rejections`, `extraction.verdicts`, and `model.fetch_failures`, `model.verify_failures`, and `model.quarantines` as typed counters (`kit_tools/arch/CODE_ARCH.md`; `kit_tools/docs/MONITORING.md`). Logging conventions are in `kit_tools/arch/patterns/LOGGING.md`; which error reasons are content-free is in `kit_tools/arch/patterns/ERROR_HANDLING.md`.

---

## Security Testing

### The hermetic guard

`tests/conftest.py` installs an autouse `pytest_socket.disable_socket(allow_unix_socket=True)`, so any test that touches the real network fails. `tests/test_hermeticity.py` is the canary (TCP, UDP, IPv6, `getaddrinfo`, `gethostbyname`, and `create_connection` are all confirmed blocked) and `tests/test_ci_workflow.py::TestHermeticityCanaryIsEnforced` ensures CI runs it. Mock at the seam; never relax the guard. The suite stood at 1610 tests at contract US-004 (`CLAUDE.md`; not re-measured for this document).

### Coverage by control

| Control | Test files |
|---|---|
| SSRF | `tests/test_url_validator.py`, `tests/test_stage5_url_audit.py`, `tests/test_orchestrator.py` (refusal paths) |
| Injection signalling and quarantine | `tests/test_stage1_extraction.py`, `tests/test_stage1_pdf.py`, `tests/test_stage2_structural.py`, `tests/test_stage3_promptguard.py`, `tests/test_orchestrator.py` |
| Upload bounds and isolation | `tests/test_app.py`, `tests/test_contract_errors.py`, `tests/test_smart_extraction.py`, `tests/test_models.py` |
| Secrets | `tests/test_dockerfile.py::TestNoSecretEntersTheBuild`, `tests/test_cache.py::TestReconnect`, `tests/test_app.py::test_no_selection_path_logs_the_valkey_url`, `tests/test_ci_workflow.py::TestSecretGrepJob` |
| Supply chain | `tests/test_model_fetcher.py`, `tests/test_vendor_weights.py`, `tests/test_dependency_lock.py`, `tests/test_contract_export.py`, `tests/test_dockerfile.py` (pins, lock-driven install, contract in image), `tests/test_ci_workflow.py` (permissions, publish, release assets) |
| Network placement | `tests/test_compose_fragments.py` |
| Honest degradation | `tests/test_app.py` (the health tests above), `tests/test_ci_workflow.py::TestSmokeJob`, `contract_smoke.py` |
| Posture documents | `tests/test_governance_docs.py::TestSecurityPolicy` (pins prose in the root `SECURITY.md` and `contract/GOVERNANCE.md`) |
| Type-safety policy | `tests/test_pyright_policy.py` (strict, `enableTypeIgnoreComments = false`, so a suppression cannot quietly restore green) |

### Fixtures

`tests/golden/contract_1_0_0.json` and `tests/golden/contract_1_1_0.json` are schema goldens retained forever (a contract change adds one, never edits one). `tests/fixtures/tiny_model/` is a real, loadable safetensors DeBERTa for loader tests, and `tests/fixtures/contract/unregenerated_openapi.yaml` is the drift-check failure case. `tests/fixtures/brave/llm_context_sample.json` is the Brave LLM-Context response **envelope** captured by the owner on 2026-09-16 with every chunk body, title, URL, hostname and date replaced by synthetic values — no Brave-authored text, no request headers, no key (provenance and the observed shape are in `tests/fixtures/README.md`); `tests/test_brave_provider.py` asserts nothing under `tests/fixtures/` carries an auth header name and nothing under `tests/fixtures/brave/` carries a token-shaped literal.

### Observations

- Injection coverage is example-based unit testing. No fuzz harness or adversarial corpus exists for the stage-2 regexes or the HTML and PDF parsers.
- No dependency-vulnerability scanning runs in CI or is described in the docs.

---

## Known Limitations and Open Questions

### Documented non-vulnerabilities

These are recorded in the repo with a source and a reason; they are decisions, not gaps.

| Item | Source |
|---|---|
| No authentication on any endpoint, including `/docs`, `/redoc`, `/openapi.json` | root `SECURITY.md`; `docs/configuration.md` |
| Forage is a request proxy for anyone who can reach the port | root `SECURITY.md` |
| Private-IP echo in the `/retrieve` refusal `reason` (DNS oracle) | root `SECURITY.md`; `contract/GOVERNANCE.md` ruling (d) |
| Over-sized `/extract` upload answers 400, not the documented 413 | `contract/GOVERNANCE.md` ruling (a2); `kit_tools/docs/GOTCHAS.md` |
| No rate limiting on `/retrieve` or `/search`; exhaustion by an admitted caller is out of scope | root `SECURITY.md` |
| Companion SearXNG runs `limiter: false` | `searxng/config/settings.yml`; `kit_tools/docs/GOTCHAS.md` |
| Weights are not shipped; a token-less container is degraded indefinitely | root `SECURITY.md`; `README.md` |
| The break-glass switch makes `capabilities` lie for a transition window | `docs/configuration.md` "Break-glass" |
| arm64 image built but never executed by CI | `docs/releases.md` |
| No image signing, provenance, or SBOM | `.github/workflows/ci.yml` publish job comment |
| `SearchResult.engine` is an unbounded `isinstance(engine, str)` pass-through — no length cap, no normalisation, no structural scan — and from contract `1.2.0` its provenance widens from the operator's own SearXNG to any provider in the chain, including spec 2's third-party API | `pipeline/orchestrator.py` (the sanitization loop); `search-provider-abstraction` US-004 |

The `engine` row is the one that *changed shape* rather than merely being restated.
`engine` is provenance, not identity (`SearchProvider.name` is identity), and it has always
been passed through unbounded — but until contract `1.2.0` the only thing that could
populate it was the operator's own SearXNG deployment. It is now whatever a chained
provider puts in the field, which from spec 2 includes a third-party API's response. The
risk is carried forward deliberately rather than fixed here: bounding `engine` is a wire
change and belongs with the provider that first widens it. The two fields `1.2.0` *adds*
are closed by construction — `content_kind` is a `Literal` validated on the way out, and
`date` is filtered to a strict `YYYY-MM-DD` calendar date or `None`, so neither can carry
free text (GOVERNANCE ruling 19 is why they need no scan).

### Observed absences (for the owner to rule on)

The exploration found no source that either accepts or rejects these. They are listed so that a decision can be recorded, not because one has been made.

- **Exception text on the wire.** `/retrieve` `fetch_error` is now the **only** reason that interpolates `str(exc)` into the response body (`pipeline/orchestrator.py`, the fetch catch-all). Whether upstream error text can carry anything sensitive is neither documented nor tested there. Redaction would be a wire change under `contract/GOVERNANCE.md`. `/search`'s `searxng_unavailable` was the other case and is closed: `search-provider-abstraction` US-002 replaced `str(exc)` with the provider's closed `detail` token and replaced the raw `SEARXNG_URL` echo with `SearxngProvider.origin` — scheme, host and port, userinfo stripped — so a credential in `SEARXNG_URL` can no longer reach a 422 body on an unauthenticated route. The host:port echo stays deliberately (GOVERNANCE ruling (d) treats it as a documented caveat; it is what makes a misconfigured deployment diagnosable from the response alone).
- **Container hardening beyond non-root.** The compose fragments set `mem_limit: 1024m` and nothing else: no `read_only` rootfs, no `cap_drop`, no `no-new-privileges`, no seccomp profile, no `pids_limit`, no CPU quota. Unknown whether that is a deliberate omission.
- **TLS to companions.** Whether the Valkey and SearXNG links must be TLS-protected on the private network is not stated; `VALKEY_URL` examples are plain `redis://`.
- **Dependency vulnerability scanning.** None found (see "Supply Chain Integrity").
- **Fuzzing.** None found (see "Security Testing").
- **`/search` threshold.** Scans at the hard default 0.85 regardless of `config.yaml` (see "Prompt-Injection Signalling").

---

## Incident Response

There is no named security contact or on-call rotation; the project is best-effort and reports arrive through GitHub private vulnerability reporting (root `SECURITY.md`). What the repo does document is the mechanics:

1. **A credential reached a log, a response body, or an image layer.** Rotate it by changing the environment variable and restarting (`docs/configuration.md`). If it is in a published image, withdraw the git tag *and* delete the GHCR package version, as was done for `v0.9.2-rc` (`docs/releases.md`; `kit_tools/docs/CI_CD.md`).
2. **A validator bypass or sanitization bypass.** Fix behind the six gates, publish a new tag, and open an advisory alongside. If the fix moves the wire, follow `contract/GOVERNANCE.md` Example 6: an expedited MINOR with a compatibility window, or an immediate MAJOR, never a silent break. Any contract change adds a golden fixture under `tests/golden/`.
3. **A published image fails layer verification after push.** Treat the tags as untrusted and withdraw both the tag and the package version. A gate-red run pushes nothing, and an interrupted push leaves only orphan blobs, so re-running is safe.

---

## Security Review Checklist

For PRs that touch fetching, parsing, the contract, startup, or the image. `.github/pull_request_template.md` "Standing invariants" is the short form.

- [ ] No new `ARG` in `Dockerfile`; `tests/test_dockerfile.py::TestNoSecretEntersTheBuild` still passes (CLAUDE.md invariant 2).
- [ ] Every new outbound request goes through `validate_url` and, if it follows redirects, through `fetch_url`'s manual, per-hop revalidated path rather than `follow_redirects=True`.
- [ ] Any change to a response shape or a `responses=` declaration follows `contract/GOVERNANCE.md`, bumps `contract_version` where required, adds a golden fixture, and regenerates `contract/openapi.yaml` with `uv run python -m scripts.export_contract`.
- [ ] New failure paths in startup, cache, or fetch code log a fixed reason string, never `str(exc)` or a URL (CLAUDE.md invariant 6), with a test that asserts it.
- [ ] `/health` still reports `degraded` with the right `degraded_reasons` for every new failure mode; nothing new makes a missing model or cache look healthy (CLAUDE.md invariant 5).
- [ ] New tests mock at the seam and pass under the `pytest-socket` guard; no network in the suite.
- [ ] No new dependency without re-locking through the CPU torch index; `uv.lock` committed and free of `nvidia-` wheels.
- [ ] Nothing that reads as authentication was added (`kit_tools/AGENT_README.md`).
