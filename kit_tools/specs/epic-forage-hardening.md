<!-- Template Version: 2.1.0 -->
---
epic: forage-hardening
status: active
vision_ref: "T2.2 — Forage hardening"
created: 2026-09-14
updated: 2026-09-19
---

# Epic: Forage Hardening — Retrieve Parity, Search-Text and URL Audit, Cache Integrity, Provider Bounds, Configurable Envelope, PromptGuard 86M

> **Web Access family Epic 4, Forage half.** Re-planned 2026-09-19 with `/kit-tools:plan-epic`
> against the `v1.1.0` tree (`main` `5bbc30b` plus the post-epic housekeeping PR). The 2026-09-14
> stub carried the 2026-08-28 holistic review's punch lists (WA-B cache items, WA-D, WA-E), the
> 2026-08-30 PromptGuard research and the Epic-2 resource-envelope finding; this plan adds the 20
> hardening findings the `epic-search-providers` validation runs surfaced (audit ids cited per story)
> and drops everything the extraction and search epics already closed (chunk budget and semaphore
> on `/extract`, cache keys carrying `sanitizer_revision`, the honest `/health`, the 64-token chunk
> overlap, the engine-list parity test); validation round 1 (48 reviews, 51 criticals) revised
> rulings 8, 10, 13–19 and added 21–35; round 2 (48 reviews, 32 criticals) corrected 8, 10, 13, 16,
> 19, 21–23, 26, 29, 30, 32, 33 and added 36–40; round 3 (48 reviews, 28 criticals) corrected 8, 13,
> 16, 21, 22, 23, 26, 27, 29, 30, 33, 37 and added 41–42. Runs **in Forage**; the Poppy
> trust-boundary consolidation is family Epic 5 and executes in Poppy.

## Goal

Make the extraction service itself sturdier now that it is a standalone, third-party-reusable image:
the `/retrieve` path gets the same budgets, isolation and off-loop execution `/extract` already has;
search text and search-result URLs are scanned in the forms attackers actually control; cached
sanitization is signed so a poisoned Valkey cannot re-enter the model unscanned; provider bodies,
timeouts and queries are bounded before decode; the service is sizable to the operator's host rather
than baked to one deployment; and the injection classifier can be upgraded to Prompt Guard 2 86M with
contiguity gating against chunk-boundary evasion — all measured, all recorded, and shipped as
`v1.2.0` at contract **1.3.0** so Poppy's consuming epic can pin it.

## Decomposition

| Seq | Feature Spec | Status | Stories | Human gate | Dependencies |
|-----|-------------|--------|---------|------------|--------------|
| 1 | [feature-hardening-search-sanitization](feature-hardening-search-sanitization.md) | Planned | 4 | — | — |
| 2 | [feature-hardening-retrieve-parity](feature-hardening-retrieve-parity.md) | Planned | 6 | — | hardening-search-sanitization |
| 3 | [feature-hardening-hostname-and-config](feature-hardening-hostname-and-config.md) | Planned | 7 | — | hardening-retrieve-parity |
| 4 | [feature-hardening-cache-integrity](feature-hardening-cache-integrity.md) | Planned | 4 | — | hardening-hostname-and-config |
| 5 | [feature-hardening-provider-bounds](feature-hardening-provider-bounds.md) | Planned | 5 | — | hardening-cache-integrity |
| 6 | [feature-hardening-resource-envelope](feature-hardening-resource-envelope.md) | Planned | 4 | — | hardening-provider-bounds |
| 7 | [feature-hardening-promptguard-86m](feature-hardening-promptguard-86m.md) | Planned | 7 | **US-005** (owner vendors the 86M weights with the HF token) and **US-004** (owner runs the benchmark) | hardening-resource-envelope |
| 8 | [feature-hardening-release](feature-hardening-release.md) | Planned | 5 | **US-003** (owner-gated `v1.2.0` cut) and **US-005** (owner-run post-release verification + handoff) | hardening-promptguard-86m |

Spec 6 precedes spec 7 so the benchmark can run at `FORAGE_CPUS=1` and `4` through the envelope
knobs. Spec 1 opens the contract window (ruling 5) so every later wire addition lands inside it.

## Owner decisions (planning session 2026-09-19)

1. **All eight specs** ship in this epic, ending in a `v1.2.0` cut. The security-only trim (drop 5, 6,
   7) and the release-less variant were offered and declined.
2. **Cache integrity is an optional key, loud when absent.** `FORAGE_CACHE_HMAC_KEY` signs every value
   the Valkey backend writes (HMAC-SHA256); an unsigned, mis-signed or unparseable value is a miss,
   deleted and counted. When the variable is unset **and** `VALKEY_URL` is set, `/health` lists
   `cache_unauthenticated` in `degraded_reasons`. The in-memory backend needs no key and reports
   nothing. Spec 4.
3. **Operator fail-closed floor, off by default.** `config.yaml` `promptguard_fail_closed_floor`
   (default `false`) overrides a caller's `promptguard_fail_closed: false` on `/retrieve` and `/search`
   when `true`; both responses gain `effective_promptguard_fail_closed: bool`. Today's contract holds
   unchanged for every operator who does not set it. Spec 2.
4. **Configurable model, 22M stays default.** `FORAGE_MODEL_ID` from a closed allowlist (22M, 86M —
   any other value refuses boot), `/health` reports `promptguard_model`, the configured id enters the
   `sanitizer_revision` hash, and 86M is documented as opt-in until the corpus epic (T2.3) measures it.
   Spec 7.

## Planning rulings (senior dev, 2026-09-19)

5. **One contract window, opened by spec 1 US-004, closed by spec 8 US-002.** `CONTRACT_VERSION`
   moves `1.2.0` → `1.3.0` in the first story that moves the document, as the search epic's ruling 14
   did. Every later wire addition (`effective_promptguard_fail_closed`, `SearchRequest.blocked_domains`,
   an honoured `promptguard_threshold` on `/search`, `cache_unauthenticated`, `CacheMetrics` counters,
   `search.promptguard_latency_target_exceeded`, `promptguard_model`, the trimmed validation 422 body)
   lands inside the unreleased window: the story appends its line to the `CONTRACT_VERSION` docstring
   entry, runs `uv run python -m scripts.export_contract`, and re-creates
   `tests/golden/contract_1_3_0.json`. That golden is mutable until spec 8 freezes it; `1_2_0` and
   older are never edited. Everything is additive → MINOR under GOVERNANCE ruling (b); the Release body
   announces every line mechanically (search epic ruling 20a).
6. **Every rotation is recorded, none is avoided.** A story editing a `_REVISION_SOURCES` file
   (`contract.py`, `stage1_*.py`, `stage2_structural.py`, `stage3_promptguard.py`,
   `stage4_structuring.py`, `orchestrator.py`) measures the new `derive_sanitizer_revision({})` by
   revert-and-reproduce and records before/after at the five sites the repo's rotation protocol
   names — `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`,
   `kit_tools/docs/GOTCHAS.md` (the divergence table) and `kit_tools/arch/CODE_ARCH.md`. `cache.py`, `url_validator.py`, `retrieval_app.py`, `models.py`,
   `promptguard/*`, `pipeline/search_providers/*` and `pipeline/extraction_limits.py` are not hashed.
7. **No DNS at search time.** The search-result URL audit checks literal IP hosts with the synchronous
   `_is_private_ip` helpers and hostnames against the blocklist and suffix rules only; DNS-pinned
   validation stays a fetch-time (`validate_url`) concern. A search result is never fetched to audit it.
8. **Hostname matching is asymmetric by direction** (revised in rounds 1–4). Denylists —
   the `blocked_domains` request field, `config.yaml` `seed_blocklist` — match by dot-boundary
   suffix for multi-label entries (single-label entries such as `intranet` stay exact-only, so no
   existing entry widens); operator entries merge first and can never be evicted by a caller's
   list. Allowlists (`trusted_domains`, `verified_domains`, `news_domains`) keep exact matching
   for bare entries; a leading-dot entry (`.example.com`) matches the apex and every subdomain —
   a form today's matcher can never match; a leading-dot `verified_domains` entry is documented
   with its consequence (VERIFIED degrades open on classifier unavailability) and counted like a
   trusted wildcard skip. Caller lists are bounded by a byte budget, never an entry count
   (ruling 44). Every entry and host is normalised through spec 1's canonicaliser (strip, lower,
   trailing dot, IDNA via `idna` ≥ 3.7) before comparison, and canonical strings cross every
   boundary; config-sourced lists are normalised once in the lifespan; an entry needs at least
   two labels; invalid entries are ignored and counted, never a 422; a host that fails IDNA is
   `invalid_url` at search time and refused at fetch time. The built-in private-name list
   (`localhost`, `*.localhost`, `.local`) keeps its own check ahead of the matcher. The trust
   resolver keeps its signature; wildcard-skip counting uses a separate matched-entry helper.
   `seed_blocklist` applies to `/search` results too.
9. **The omission vocabulary grows by one token, `blocked_url`** — policy omissions only: a
   private/loopback/link-local literal, a blocklisted host, a `blocked_domains` or `seed_blocklist`
   match. `invalid_url` keeps its meaning and covers every malformed URL (unparseable, wrong scheme,
   userinfo, forbidden characters, forbidden host code points, IPv6 zone ids — ruling 25). Closed set in
   `pipeline/contract.py`.
10. **Threshold single-sourcing keeps `/extract`'s path** (revised in rounds 1 and 2).
    `SearchRequest` gains `promptguard_threshold: float | None = None` (spec 3 US-005, window);
    on both routes `None` means "use `config.yaml` `promptguard_threshold`" (a malformed config
    value logs `config_invalid_value` and falls back to the default — never a boot refusal);
    `/extract` keeps its existing read. The operator ceiling `promptguard_threshold_ceiling`
    (default `1.0`) bounds what a caller may request; `effective_promptguard_threshold` joins
    `effective_promptguard_fail_closed` on `RetrievedContent` (spec 2 US-005) and on
    `SearchResponse` (spec 3 US-005).
11. **The engine list is already single-sourced by test** —
    `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator` pins
    `searxng/config/settings.yml` to `SEARXNG_ENGINES`. WA-E's duplication item is closed; spec 3 only
    documents the mechanism. "Config single-sourcing" in this epic means the key registry (ruling 12).
12. **`config.yaml` key registry.** `_load_config` stays `yaml.safe_load`; a central registry of known
    keys (top-level and nested) is asserted at boot: an unknown key logs one WARNING
    `config_unknown_key — key=<name>` and never refuses boot (an operator typo must not take the service
    down); a test asserts every registered key has a row in `docs/configuration.md`.
13. **Provider bodies are read raw, decoded by Forage, and capped before parse** (corrected in
    rounds 2–4). Both providers set `Accept-Encoding: identity` explicitly and read with
    `aiter_raw()`; `gzip`/`deflate` bodies are decoded by a `zlib` decompressor fed per chunk with
    `max_length`, draining `unconsumed_tail`, never calling `flush()`; decoded bytes never exceed
    the 1 MiB cap and raw bytes are capped at four times it; any other encoding is
    `hard_error`/`unsupported_encoding`, counted — and because the provider detail reaches the
    `searxng_unavailable` 422 reason on a `[searxng]`-only chain, that token is announced by a
    docstring line in the window. `search.provider_compressed_body` counts the header for every
    outcome. Each request is wrapped in `asyncio.timeout(...)`. SearXNG gains a `SearxngSettings`
    dataclass read through `pipeline/config_bounds` (spec 2 US-001's shared helper), wired
    through `build_provider_chain`; the streaming migration owns its test seam (a stream-backed
    `make_response` double with a per-chunk delay in `tests/fakes.py`; no `httpx.MockTransport`).
    The stage-5 fetch decoder's identical residual is filed in BACKLOG with a SECURITY.md row.
14. **Cost-monotonicity holds by construction** (revised). `apply_request_policy` keeps only a
    *prefix* of the configured paid providers; a property test with two paid fakes asserts no
    request can produce a paid call the configured chain would not make first.
    `policy_unknown_provider` keeps its per-entry unit (no contract or MONITORING change); the
    amplification is recorded as an accepted risk in `kit_tools/arch/SECURITY.md`.
15. **The `orchestrator.py` cleanup is one rotation, one story** (spec 5 US-005): extract the
    provider loop into `_query_provider_chain`, one failure-log helper, `_legacy_searxng_codes`
    computed once, the duplicate `ValueError` sites merged, the `searxng_url=` legacy parameter of
    `run_search_pipeline` dropped with its 19 test call sites migrated to `providers=` (the
    `build_provider_chain(*, searxng_url=)` keyword stays), omission INFO lines logging the closed
    reason and validated `domain` (never the result URL), and `provider_errors` composition
    enforcing the closed vocabulary. Behaviour preserved: `SearchResponse.model_dump()` with
    `request_id` excluded is identical across at least three existing fixtures.
16. **The envelope changes nothing at the defaults** (corrected in rounds 2–4). `cpus:
    ${FORAGE_CPUS:-0}` renders as no `cpus` key and `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` keeps
    today's value; a liveness `healthcheck` joins both fragments (its `/health` polling effect on
    the reconnect counters is documented) and the two rendered docstrings it falsifies move
    inside the window. `promptguard_threads` defaults to `0`; a positive value sets
    `torch.set_num_threads` and `TOKENIZERS_PARALLELISM=false` before the classifier loads.
    `classification_concurrency` widens to 1–8 under a memory rule whose terms are read from
    config and conditional — 512 MiB parent + concurrency × the classifier working set spec 7
    measures + `extraction_concurrency` × `extraction.child_address_space_bytes` + `cache.max_bytes`
    only when the in-memory backend is selected — with an advisory boot WARNING (after the cache
    settings are read) only when the cgroup limit is strictly below it; the "only tighten"
    sentence names every raisable key honestly. The two orchestrator latency constants become
    `config.yaml` keys; overruns are counted on `/metrics`; the high-water mark
    `search.sanitization_latency_max_ms` measures the whole per-result loop, and the operator
    signal for fail-open-under-load is spec 2's `classification_wait_timeouts`. The `lint` job
    renders both fragments on the default branch and again with the knobs set; no criterion
    records rendered compose config (ruling 33).
17. **Contiguity gating sits beside the max rule and ships off** (revised). The classifier
    exposes per-window scores; stage 3's verdict is INJECTION when `max_score > threshold` **or**
    at least `promptguard_contiguity_windows` consecutive windows score
    `>= promptguard_contiguity_threshold`. `promptguard_contiguity_windows` defaults to `0`
    (disabled); the enabling recipe (`2` at `0.5`) is documented and tested; the corpus epic
    (T2.3) flips the default after measuring the false-positive rate. `flagged_chunks` carries the
    contiguous run.
18. **The benchmark is an owner gate that measures the service** (revised). The harness runs on
    the host against the running container (`.dockerignore` excludes `scripts/`): it drives
    `POST /extract` with a mounted benchmark config (the URL validator refuses loopback fixtures on
    `/retrieve`), reports p50/p95 wall-clock for a 512-token page and a max-budget page, cold and
    warm, and reads container RSS from `docker stats`. The
    *run* needs the gated weights and an HF token supplied only through an env file referenced by
    path, so spec 7 US-004 is a declared human gate, preceded by the vendoring gate US-005
    (ruling 29). Numbers recorded: a 2×2×2 matrix (22M/86M × `FORAGE_CPUS=1`/`4` × two inputs)
    plus RSS rows.
19. **The validation 422 body closes its reflector in two steps** (corrected in rounds 2–5).
    An app-level `RequestValidationError` handler re-emits `{loc, msg, type}`; following
    GOVERNANCE "Example 6 in full" step 1, for one minor release the documented-by-description
    extra keys `input`, `ctx` and `url` stay present with the fixed value `"[redacted]"`
    (shape-compatible, reflector closed immediately), and the docstring line names the release
    in which they are dropped as a second MINOR; GOVERNANCE ruling (e) records the two-step.
    Spec 8 US-001 owns its own regenerate, golden and four-page anchor refresh; `msg`, `loc`
    and `type` carry a no-caller-bytes invariant pinned by a per-field marker fuzz; the `loc`
    allowlist is keyed by the matched route's path with a fail-closed default; the pipeline 422
    `reason` stays what the search epic's ruling (d) made it.
20. **Release plumbing repeats the `v1.1.0` runbook verbatim**: compose pins and `_FORAGE_RELEASE_TAG`
    move to `1.2.0` before the tag, both smoke modes run with `--anchor`, the credential-free pull
    follows `docker image rm` (audit -014), and the handoff table copies the shape of the archived
    `feature-search-release.md` record.

### Rulings from `/kit-tools:validate-epic` round 1 (2026-09-19; 48 reviews, 51 criticals resolved here)

21. **The cache MAC binds the key, and both sides are bounded** (corrected in rounds 2–5).
    `HMAC-SHA256(key, "v1" || cache_key || payload)`, envelope `v1.<mac>.<payload>`, recomputed
    under the key being read; the secret is at least 32 bytes after strip. Read: one atomic
    `GETRANGE key 0 max` — an empty result is a miss, a `WRONGTYPE` reply is an integrity
    reject, a value longer than `cache.max_value_bytes` (default 4 MiB, floor 512 KiB; larger
    than `max_bytes` is a WARNING) is deleted and counted `oversize`; because `from_url` applies
    URL query options over explicit kwargs, a `VALKEY_URL` carrying `decode_responses`,
    `protocol` or `encoding` options refuses boot (`valkey_url_option_forbidden`) so the byte
    bound stays a byte bound. Write: `put` deletes the superseded entry and refuses to store a
    value it would reject, counting `storage_oversize_skips` (documentation-only semantics
    change recorded in GOVERNANCE). Envelope parsing is bytes end-to-end; the five rejection
    shapes each have a literal in `CACHE_INTEGRITY_REASONS`, nothing raises. Because
    `contract.py` rotates in this spec, every pre-epic entry is orphaned at upgrade: the runbook
    says there is no first-enable burst, that `unsigned`/`bad_mac`/`wrong_type` mean a foreign
    writer, and that `oversize` means a cap mismatch across replicas. A configured key on the
    memory backend logs `cache_hmac_key_unused`.
22. **The `/retrieve` chunk budget is a pre-checked refusal**, not a clamp: its own key
    `retrieve.max_promptguard_chunks` (default 256), a characters-only pre-check before
    classification (`len(raw_text) > max_extracted_characters(n)`; no byte limb — it cannot bind
    below 293 chunks and the character bound already bounds bytes ×4), over budget → 422
    `content_too_large` with the closed reason `promptguard_budget`; the classifier's
    `PromptGuardBudgetExceededError` is caught on the route and mapped to the same 422. Every
    semaphore acquisition is `async with`, and a criterion asserts the permit count is unchanged
    after an over-budget page, a timed-out wait and a raised classifier; a timed-out wait logs its
    own `classification_wait_timeout` token, never the "PromptGuard unavailable" line (corrected in
    rounds 2 and 3).
23. **Fetched PDFs go through the worker from a spooled file** under a second
    `ExtractionAdmissionController` built from a `retrieve.*` config block through the
    controller's existing constructor shape and handed to the pipeline as a pipeline-side
    Protocol (`admission: AdmissionSlot | None = None` in spec 2 US-001 so that story is green
    alone; required from US-002); the acquisition is never wrapped in `asyncio.timeout`, the
    controller's bounded queue and queued-bytes limits are the backpressure, and a full queue
    refuses with the existing `busy` code — 422 on `/retrieve` (reason `admission_queue_full`)
    while `/extract` keeps its 429 — through a route-aware handler declared per route (ruling
    42). At most one fetched body is held, from fetch through stage 1 (`fetch_concurrency`
    1–1), none while waiting on classification; a fail-open wait timeout never writes the
    cache. The spool directory is created 0700 lazily with an `lstat` check and no repair. The
    worker's four reachable typed errors map to one new `RetrieveErrorCode` member
    `extraction_failed` with closed reasons, so `RetrieveErrorCode` grows by two. The chunk
    budget ships at `0` (unbounded, today's behaviour) with a `retrieve_budget_unset` WARNING for
    one minor release before the default flips to 256 — GOVERNANCE Example 6 step 1, the knob
    is the compatibility mechanism and the window is stated in `docs/releases.md`. HTML
    extraction and `scan_structural` run under `asyncio.to_thread` on both routes.
24. **`effective_*` fields are computed in the handler and cached faithfully.** Both are inputs to
    `cache_policy_fingerprint`, so a cache hit always reports the request's current effective
    values; `effective_promptguard_fail_closed` means "the fail-closed policy applied", never
    "content was scanned" (trusted-tier content still skips the classifier by design).
25. **The URL scan is a direct structural scan** on the raw provider value (after `html.unescape`)
    and on its `unquote`, never through `extract_html`; character rules run on the raw value;
    malformed URLs (control characters, whitespace, RFC-3986-forbidden characters, forbidden host
    code points, IPv6 zone ids) are `invalid_url`; `blocked_url` is reserved for policy —
    private/loopback/link-local literals, blocklisted hosts, `blocked_domains` / `seed_blocklist`
    matches (ruling 9 clarified).
26. **One normalisation, wire ⊆ scan by construction** (order fixed in rounds 2–5): NFC → raw
    control strip → Stage 1 prose extraction (the parser decodes one entity level, so benign
    escaped markup survives as visible text) → `html.unescape` on the extracted text (the
    second level, so double-encoded markers reach the scanner) → control strip again →
    newline-preserving whitespace collapse → truncate at the cap = the scan form; the wire form
    is `" ".join(scan.split())`. The measured fixtures (660 padded paragraphs blocked, 700
    served; a double-encoded line-anchored marker decoded; a raw NUL stripped before parsing; a
    benign `&lt;div&gt;` kept as text) are executed before they are written (ruling 41). Wire
    identity holds for every existing fixture except the ones the story names and rewrites.
    `SearchResult.engine` is an explicit documented exemption from Stage 2/3.
27. **Hosts are canonicalised before audit, IPv6 literals first** (corrected in rounds 3–5): a
    colon-bearing hostname is an IPv6 literal and is classified by `ipaddress` before any IDNA
    step; colon-free hosts strip one trailing dot, IDNA via `idna.encode(..., uts46=True)`
    (failure → `invalid_url`/`idna`), then numeric hosts (decimal, octal, hex or short forms)
    must be a dotted quad `ipaddress` parses or are `invalid_url`/`numeric_host`; `urlsplit`
    failures and a `parsed.port` that raises are `invalid_url` (`unparseable` / `invalid_port`).
    The canonicaliser returns a result carrying the closed rejection token. IPv4-mapped,
    IPv4-compatible, 6to4 and Teredo embedded addresses are unwrapped per their layouts and NAT64
    only under `64:ff9b::/96`; `2001:db8::/32` stays private, so served IPv6 controls are
    global-unicast literals. The raw URL is rejected `invalid_url`/`too_long` past
    `_MAX_SEARCH_URL_LENGTH` before any scan.
28. **Anchor sweeps never touch released records**: only the four anchor-quoting pages the
    governance test names; `docs/releases.md` entries and archived specs keep the anchor of the
    release they record.
29. **The model id reaches every consumer, and 86M is vendored before it is benchmarked**
    (corrected in rounds 2–5). One resolver feeds `model_fetcher` — through
    `WeightAcquisition`, which gains `model_id` — `sanitizer_revision`, `vendor_weights` and the
    classifier's two `from_pretrained` calls; spec 7 US-001 owns the plumbing, US-006 the
    `FORAGE_MODEL_ID` allowlist, refuse-boot, `id2label` name-and-cardinality assertion and
    `/health` `promptguard_model`. `weights_manifest.json` is keyed by model id with one exact-set
    file allowlist and one revision per model; `resolve_revision(model_id)` is the validated env
    override (40-hex shape checked before any path or fetch) → manifest entry →
    `DEFAULT_MODEL_REVISION` for the default model → `unpinned`; `_load_verified` derives the
    manifest-derived and resolved snapshot paths itself and asserts they are equal; SECURITY.md
    records that the classifier trusts the path the verifier handed it. Verification is never
    skipped; the allowlist ships with the 22M only, a test proves every allowlisted id has a
    manifest entry, and spec 7 US-005 (owner gate) adds the 86M before US-004 runs. The benchmark
    refuses to measure a container whose classifier is not loaded, records `promptguard_model`
    and `sanitizer_revision` per row, sizes its documents by tokenising with the model's own
    tokenizer, drives `POST /extract` under a committed `bench/config.yaml`, and publishes
    single-in-flight numbers with that caveat.
30. **The release spec closes the window mechanically** (corrected in rounds 2 and 3): each
    window story appends its field to `_EXPECTED_ONE_THREE_ZERO_DIFF` and spec 8 US-002 freezes
    the list, the golden and the docstring record (entries carry no publication-state clause);
    bookkeeping sweeps by value with pattern greps, never pinned count words; the `v1.2.0` cut
    (US-003) and the post-release verification + handoff record (US-005) are two owner-executed
    stories; the Valkey-backed smoke run uses `compose/full.yml` (whose `name:` shares the warm
    `forage-model-cache` volume, so it ends with `docker compose down`, never `-v`) with a
    throwaway env file built from placeholders for every passthrough variable, SearXNG not
    started; the leak check greps the whole tree for the HF, SearXNG, Brave, HMAC and
    Valkey-credential shapes. The 422 trim follows GOVERNANCE "Example 6 in full": an expedited
    MINOR with a stated compatibility window, and the story says why not a MAJOR.
31. **Story splits from round 1**: spec 3 gains US-005 (threshold honoured + config default) and
    US-006 (engine-sync and docs sentences); spec 5's US-002 splits into US-002 (egress bound) and US-004 (paid-prefix
    policy) with `SearxngSettings` born in US-001, the cleanup is US-005 (no US-003); spec 6 gains US-004
    (latency targets + counter); spec 7 gains US-006 (`FORAGE_MODEL_ID` resolver, refuse-boot,
    `/health` field, revision hash) and US-005 (vendoring gate); spec 8 gains US-004 (pins, counts,
    release-notes draft). Each spec's
    `execution_order` records the resulting walk.
32. **The rotation ledger is honest**: any story that appends a `CONTRACT_VERSION` docstring line
    or edits `orchestrator.py` rotates (spec 5's counter sink lives in `orchestrator.py`, so that
    story moves two hashed files); every "rotates nothing" claim was corrected and each ledger
    lists which hashed files move per story, recorded at the five rotation-record sites.
33. **No secret-bearing capture, ever**: no criterion records rendered compose config,
    `docker inspect` output, environment dumps or any env value in a tracked file; renders use
    `--env-file /dev/null`, a complete placeholder set, a scratch project directory and a
    grep-filtered excerpt; the healthcheck uses `curl -o /dev/null`; criteria reference
    `--env-file`, never a value; throwaway env files are created from placeholders, never copied;
    the validation-422 handler is total and never lets the raw body or `str(exc)` reach a log.
34. **Docstring entry format**: new `CONTRACT_VERSION` lines follow the `* ``1.3.0`` — …` bullet
    format the CI awk extractor and `tests/test_ci_workflow.py` pin.
35. **Spec 1 definitions**: "wire form" is the raw provider value before any normalisation;
    "decoded form" is its `unquote`; the `engine` bound normalises then truncates to 64 characters
    like `title`; rule (3)'s host character set is the WHATWG forbidden host code points; US-004
    is P2 but still runs before US-003 by `execution_order`.

### Rulings from validate-epic round 2 (2026-09-19; 48 reviews, 32 criticals resolved here)

36. **Window mechanics are uniform** (corrected in round 4). Every story that moves the document
    appends its docstring line, regenerates, re-creates `tests/golden/contract_1_3_0.json` via
    `_SCHEMA_MODELS`, refreshes the four anchor-quoting pages and runs `--check`. A story that adds
    a field or enum member also appends that path to `_EXPECTED_ONE_THREE_ZERO_DIFF`; a story whose
    move is a description or a bound appends nothing (the diff helper sees only additions) and is
    gated by `test_contract_schema_matches_golden`. Spec 8 US-002 freezes all of it.
37. **Story splits, round 2 — and no further splits.** Spec 2 US-001 → US-001 + US-006; spec 4
    US-002 → US-002 + US-004; spec 5 US-001 → US-001 + US-003; spec 7 US-002 → US-002 + US-007;
    spec 8 US-003 → US-003 + US-005. Owner-gate stories state the "gate not run" outcome in their
    Independent Test. The round-2 split proposals for spec 1 US-003/US-004, spec 5 US-003, spec 6
    US-001 and spec 7 US-001 are overruled: single concern each, session-fit at size L.
38. **`/search` classification is bounded**: `run_search_pipeline`'s per-result classification runs
    under the classification semaphore (spec 2 US-006); the head-of-line risk against a 256-chunk
    `/retrieve` is recorded in Security Considerations with the wait counter as the signal.
39. **Every enumerated site list is generated by value**: the criterion is the grep (pattern +
    expected count at the story's start, zero at its end); hand-typed lists are hints only.
40. **Every code story names the tests it breaks**: a story that changes a mocked seam lists the
    test files and helper names it migrates with a `grep -c` starting count, as a criterion.

41. **Every fixture a spec names is executed once before it is written**: each carries its expected
    reason token and, where a count matters, the arithmetic (`fetch_limit`, window count).
42. **Status codes are declared per route**: a story that makes an existing error code answer a
    different status on a different route declares it in the route's `responses=`, the docstring
    line and `tests/test_contract_errors.py`, and the handler is route-aware.

43. **Grep criteria are scoped and executed**: every grep criterion names its exact path set,
    excludes `kit_tools/specs/`, `kit_tools/.seed_cache/`, `tests/golden/` and result artifacts
    unless the story edits them, and its expected count was produced by running it.
44. **No new caps on caller lists by count**: bounds on caller-supplied lists are byte budgets
    (`policy_domain_entries_max_bytes`) with a declared truncation rule for allowlists and a
    declared per-route refusal (`policy_domain_list_too_large`) for denylists.

### Resolution map (which story owns which finding)

| Finding | Owner |
|---|---|
| Audit 2026-09-16-016 newline collapse before the structural scan | Spec 1 US-001 |
| Audit -032 URL scanned only decoded, -033 forbidden host code points | Spec 1 US-002 |
| WA-E "search result URLs never checked against private-IP or blocklist rules" | Spec 1 US-003 |
| Audit -014 unbounded `engine` pass-through; contract window opens | Spec 1 US-004 |
| WA-D `/retrieve` chunk budget and semaphore | Spec 2 US-001 |
| WA-D sync PDF/HTML/structural scan on the event loop, un-sandboxed PDF | Spec 2 US-002, US-003 |
| WA-D corrupted cache entry → 500 | Spec 2 US-004 |
| WA-D fail-closed policy caller-controlled (owner decision 3); threshold ceiling (ruling 10) | Spec 2 US-005 |
| WA-E exact-hostname matching (blocklist, trust tiers, news TTL) | Spec 3 US-001 |
| WA-E `blocked_domains` never applies to search; `promptguard_threshold` dead on `/search` | Spec 3 US-002 |
| WA-E config sprawl (key registry) | Spec 3 US-003 |
| Audit -058 boundary-text knob parity; engine-list sync documented | Spec 3 US-004 |
| WA-B poisonable content cache (owner decision 2) | Spec 4 US-001, US-002 |
| Audit -012 SearXNG post-buffer bound, -020 Brave post-decompression cap, -021 per-socket timeout | Spec 5 US-001 |
| Audit -044 unbounded SearXNG query, -052/-060 policy monotonicity, -054 counter amplification | Spec 5 US-002 |
| Audit -035/-036/-038/-039/-043/-045/-011 orchestrator cleanup | Spec 5 US-003 |
| Epic-2 cutover finding: 1-CPU cap serialises search + fetch; latency WARNING never renders | Spec 6 US-001, US-002 |
| Research 2026-08-30: 22M → 86M (owner decision 4) | Spec 7 US-001, US-004 |
| Research 2026-08-30 / scorecard #11: Prompt Overflow chunk-boundary evasion | Spec 7 US-002 |
| Audit -055 validation 422 echoes `detail[].input` | Spec 8 US-001 |
| Audit -056/-057 wire description and docstring wording (PATCH-class, deferred at housekeeping) | Spec 8 US-002 |
| Audit -010 mutable tag in compose examples, -014 pull proof | Spec 8 US-004, US-003 |
| Round 1: key-bound MAC, bounded reads, mirrored counters (21) | Spec 4 US-001, US-002 |
| Round 1: `/retrieve` budget refusal (22), spooled PDF worker + error mapping (23), effective fields (24), threshold ceiling (10) | Spec 2 US-001, US-002, US-003, US-005 |
| Round 1: direct URL scan (25), wire ⊆ scan (26), host canonicalisation (27), anchor scope (28), definitions (35) | Spec 1 US-001–US-004 |
| Round 1: asymmetric hostname matching, IDNA, `seed_blocklist` on search (8); honoured threshold (10); seven-copy guard (31) | Spec 3 US-001, US-002, US-004, US-005 |
| Round 1: compressed bodies never refused, `SearxngSettings`, test seam (13); per-entry unit kept (14); implementable proofs (15) | Spec 5 US-001, US-002, US-004, US-005 |
| Round 1: defaults unchanged, memory rule, search semaphore parity (16); placeholder captures (33) | Spec 6 US-001, US-002, US-004; Spec 2 US-001 |
| Round 1: contiguity off (17), service-level benchmark (18), model-id plumbing + vendoring gate (29) | Spec 7 US-001, US-002, US-003, US-005, US-006 |
| Round 1: anchor ownership (19), mechanical close-out + Valkey-backed smoke (30) | Spec 8 US-001, US-002, US-004, US-003 |

## Contract versioning (governance approach for this epic)

One MINOR bump, `1.2.0` → `1.3.0`, opened early (ruling 5) and closed by spec 8 US-002. Additions:
`blocked_url` omission reason (spec 1), bounded `engine` (spec 1, tightening of what Forage emits),
`effective_promptguard_fail_closed` and `effective_promptguard_threshold` on `RetrievedContent` and `effective_promptguard_fail_closed` on `SearchResponse` (spec 2; `SearchResponse.effective_promptguard_threshold` lands with spec 3 US-005), the `promptguard_budget` reason literal and two `RetrieveErrorCode` members, `extraction_failed` with closed reasons and `busy` with `admission_queue_full` / `admission_wait_timeout` (spec 2), `SearchResult.domain` as the UTS-46 host for IDN results (spec 1), `policy_invalid_domain_entry` counters and the leading-dot allowlist form in three field descriptions (spec 3),
`CacheMetrics.corrupt_entries` / `integrity_rejects` and the third `capabilities` key (specs 2, 4), `SearchRequest.blocked_domains` and
`promptguard_threshold: float | None` on both request models (spec 3), `cache_unauthenticated`
`DegradedReason` (spec 4), `search.provider_compressed_body` (spec 5),
`search.promptguard_latency_target_exceeded` and `search.sanitization_latency_max_ms` (spec 6), the healthcheck sentences in two `/health` descriptions (spec 6), `/health` `promptguard_model` and `promptguard_contiguity_detections` in the `retrieve`, `search` and `extraction` sections (spec 7), and the
`{loc, msg, type}`-only validation 422 (spec 8, a MINOR with a compatibility note — the shipped
contract documented the extra keys). Every line is in the
`CONTRACT_VERSION` docstring entry and therefore in the `v1.2.0` Release body. Poppy's consuming epic
mirrors the defaulted fields; nothing here removes or redefines a member.

## Success Criteria

- The audit -016 parity case — a `System:` marker after a paragraph break inside the second chunk of a
  search result — is `structural_blocked` on `/search` and blocked on `/retrieve`, with one shared test.
- A search result with a literal private-IP host, a blocklisted host, `evil.com\.good.com`, or an IPv6
  zone id never reaches the wire: omitted under `blocked_url` or `invalid_url`, zero DNS lookups.
- A fetched page whose extracted text exceeds `max_extracted_characters(retrieve.max_promptguard_chunks)`
  (default 256) is refused 422 `content_too_large` / `promptguard_budget` before any inference; two
  concurrent `/retrieve` classifications serialise; a concurrent `/health` completes while a slow
  extraction runs.
- With `FORAGE_CACHE_HMAC_KEY` set, a hand-written Valkey value and a signed envelope copied to
  another key are each served zero times; without the key and with `VALKEY_URL` set, `/health` lists
  `cache_unauthenticated`.
- A 2 MiB SearXNG body, a gzip-bombed Brave body and a trickling Brave response each produce a
  classified `ProviderFailure` within the configured wall-clock timeout, never an unbounded read.
- `FORAGE_CPUS=4 FORAGE_MEM_LIMIT=4g docker compose up` runs the same image with the documented knob
  values; the sizing table carries measured classifier numbers for 22M and 86M at 1 and 4 CPUs.
- `FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M` boots after the vendoring gate and reports
  `promptguard_model`; with `promptguard_contiguity_windows: 2` a `0.6, 0.6` window sequence is
  INJECTION while `0.6, 0.2, 0.6` is not, and at the default (`0`) neither fires.
- `v1.2.0` is published at contract `1.3.0` with the handoff table, both smoke modes green.

## Completion Criteria

- [ ] All eight feature specs completed and archived; every human gate recorded in its spec.
- [ ] Contract `1.3.0` frozen: docstring record complete, `tests/golden/contract_1_3_0.json` final,
      anchor committed, GOVERNANCE current-version sentence updated.
- [ ] Every hashed-file story's rotation recorded (`docs/bootstrap-notes.md` rotation count grew by the
      number of such stories).
- [ ] `v1.2.0` published: index digest equal for `latest` / `1.2` / `1.2.0`, Release body carries
      `contract: 1.3.0` and every docstring line, both `contract_smoke.py` modes exit 0 with `--anchor`.
- [ ] Handoff table (tag, digest, contract, anchor, tagged sha, publish run, key-less and keyed
      `/health`, spend posture, envelope defaults, benchmark pointer) in `feature-hardening-release.md`
      Implementation Notes, and Poppy's split doc updated at the next handoff.
- [ ] MILESTONES and PRODUCT_VISION T2.2 marked shipped; `epic-forage-injection-corpus` unblocked.

## Non-goals

- **Anything inside Poppy** — trust-boundary consolidation, datamarking policy, activation and re-arm,
  egress network, the paid-fallback circuit breaker (family Epic 5 and Poppy's `epic-search-policy`).
- **The injection regression corpus** — `epic-forage-injection-corpus` (T2.3) measures what this epic
  builds; this epic ships the fixed parity cases only.
- **New search providers, a LiteLLM adapter** — T3.1.
- **Training or fine-tuning a guard model; ensembling classifiers** — research verdict 2026-08-30.
- **int8 / ONNX for 86M** — only if the benchmark forces it (non-blocking open question in spec 7).
- **A `forage/` package rename** — deferred, separate change.
- **A spend ceiling** — search epic ruling 12 stands; Poppy's breaker is the cap.

## Notes

### Cross-repo sync rule — Forage is canonical for this epic, one-way, at handoff only

This epic was planned and is executed in Forage. Poppy's `EPIC3_SEARCH_RELIABILITY_SPLIT.md` and
`WEB_ACCESS_FAMILY.md` receive the handoff record (rulings, contract window contents, `v1.2.0` table)
at spec 8, the way the search epic's rulings 8–34 were replayed on 2026-09-19. Nothing here edits Poppy.

### Standing context for every story

- Read `CLAUDE.md`'s Hard Invariants first; invariant 2 (no secret in the build) and 6 (no credential
  in a log) bind every story that touches an env var; invariant 5 binds every `/health` change.
- The four gates are zero-tolerance: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyright` (strict, no type-ignore comments), `uv run pytest` (hermetic; mock at the seam).
- Any contract movement: `uv run python -m scripts.export_contract`, then `--check`; the 1.3.0 golden is
  re-created, never a new file, until spec 8.
- Any hashed-file edit: ruling 6. Any new env var: `tests/conftest.py::_CLEARED_ENV_VARS`'s exact-set
  test, `docs/configuration.md`, `kit_tools/docs/ENV_REFERENCE.md`, compose passthrough where relevant.
- Owner-gate stories stop and report if the gate has not run; they never fabricate a measurement.
- The hardening knobs specs 1–5 add (`retrieve:` block, the policy floors, `FORAGE_CACHE_HMAC_KEY`'s
  companions) are `config.yaml`-only until spec 6 lands the bind-mount and env procedure — an
  interim state, not a design; the shipped `extraction:` block has the same shape today.
- Findings from the search epic's validation runs are quoted by id; the full text is in the (gitignored)
  `kit_tools/AUDIT_FINDINGS.md` of the main checkout and in the triage record of 2026-09-19.
