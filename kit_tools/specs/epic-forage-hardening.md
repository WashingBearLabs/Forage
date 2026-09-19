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
> and drops everything the extraction and search epics already closed (chunk budget and semaphore on
> `/extract`, cache keys carrying `sanitizer_revision`, the honest `/health`, the 64-token chunk
> overlap, the engine-list parity test). Runs **in Forage**; the Poppy trust-boundary consolidation
> is family Epic 5 and executes in Poppy.

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
| 2 | [feature-hardening-retrieve-parity](feature-hardening-retrieve-parity.md) | Planned | 4 | — | hardening-search-sanitization |
| 3 | [feature-hardening-hostname-and-config](feature-hardening-hostname-and-config.md) | Planned | 4 | — | hardening-retrieve-parity |
| 4 | [feature-hardening-cache-integrity](feature-hardening-cache-integrity.md) | Planned | 3 | — | hardening-hostname-and-config |
| 5 | [feature-hardening-provider-bounds](feature-hardening-provider-bounds.md) | Planned | 3 | — | hardening-cache-integrity |
| 6 | [feature-hardening-resource-envelope](feature-hardening-resource-envelope.md) | Planned | 3 | — | hardening-provider-bounds |
| 7 | [feature-hardening-promptguard-86m](feature-hardening-promptguard-86m.md) | Planned | 4 | **US-004** (owner runs the benchmark with the gated weights) | hardening-resource-envelope |
| 8 | [feature-hardening-release](feature-hardening-release.md) | Planned | 3 | **US-003** (owner-gated `v1.2.0` cut) | hardening-promptguard-86m |

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
   revert-and-reproduce and records before/after in `docs/bootstrap-notes.md`, `CLAUDE.md` and
   `kit_tools/arch/DECISIONS.md`. `cache.py`, `url_validator.py`, `retrieval_app.py`, `models.py`,
   `promptguard/*`, `pipeline/search_providers/*` and `pipeline/extraction_limits.py` are not hashed.
7. **No DNS at search time.** The search-result URL audit checks literal IP hosts with the synchronous
   `_is_private_ip` helpers and hostnames against the blocklist and suffix rules only; DNS-pinned
   validation stays a fetch-time (`validate_url`) concern. A search result is never fetched to audit it.
8. **Hostname matching is dot-boundary suffix matching everywhere.** One helper in `url_validator.py`:
   an entry matches a host when equal, or when the host ends with `"." + entry`. `evil.com` matches
   `www.evil.com`, never `notevil.com`. Applied to `blocked_domains`, `trusted_domains`,
   `verified_domains` (trust tier) and `news_domains` (TTL). Bare hosts still match themselves.
9. **The omission vocabulary grows by one token, `blocked_url`** (private-IP literal, blocklisted host,
   forbidden host code point, IPv6 zone id, or a request `blocked_domains` match). `invalid_url` keeps
   its meaning (unparseable, wrong scheme, userinfo, forbidden characters). Closed set in
   `pipeline/contract.py`.
10. **Threshold single-sourcing.** `promptguard_threshold` is honoured on `/search` (the handler never
    passes it today, so the pipeline's 0.85 default always wins); on both routes the request field
    becomes `float | None = None`, meaning "use `config.yaml` `promptguard_threshold`"; `/extract`
    already reads config. A consumer that sent an explicit value sees no change.
11. **The engine list is already single-sourced by test** —
    `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator` pins
    `searxng/config/settings.yml` to `SEARXNG_ENGINES`. WA-E's duplication item is closed; spec 3 only
    documents the mechanism. "Config single-sourcing" in this epic means the key registry (ruling 12).
12. **`config.yaml` key registry.** `_load_config` stays `yaml.safe_load`; a central registry of known
    keys (top-level and nested) is asserted at boot: an unknown key logs one WARNING
    `config_unknown_key — key=<name>` and never refuses boot (an operator typo must not take the service
    down); a test asserts every registered key has a row in `docs/configuration.md`.
13. **Provider bodies are streamed and capped before decode.** SearXNG mirrors Brave's incremental
    counter; both providers send `Accept-Encoding: identity` and treat a non-identity
    `Content-Encoding` as a failure; both wrap the whole request in `asyncio.timeout(...)` mapped to
    `failure_class="timeout"` so the configured timeout is a wall-clock budget, not a per-socket one.
    SearXNG's outbound `q` is truncated to `search_searxng_query_max_chars` (default 400). No wire
    change.
14. **Cost-monotonicity holds by construction.** `apply_request_policy` keeps only a *prefix* of the
    configured paid providers; a property test with two paid fakes asserts no request can produce a
    paid call the configured chain would not make first. `policy_unknown_provider` counts one increment
    per request with any ignored name (documented unit change, inside the window).
15. **The `orchestrator.py` cleanup is one rotation, one story** (spec 5 US-003): extract the provider
    loop, one failure-log helper, `_legacy_searxng_codes` computed once, the duplicate `ValueError`
    sites merged, the `searxng_url=` legacy parameter dropped with its 19 test call sites migrated to
    `providers=`, omission INFO lines logging the closed reason and validated `domain` (never the
    result URL), and `provider_errors` composition enforcing the closed `failure_class` / detail
    vocabulary. Wire output byte-identical on the existing fixtures.
16. **Envelope knobs are env-substituted compose values with today's numbers as defaults.**
    `cpus: ${FORAGE_CPUS:-1}`, `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` and a `healthcheck` in both
    fragments; `classification_concurrency` range 1–8; `promptguard_threads` (default 1) applied via
    `torch.set_num_threads` at load; the two orchestrator latency constants become `config.yaml` keys;
    overruns counted on `/metrics` because the WARNING never renders (GOTCHAS: nothing configures
    logging); a sizing table in `docs/configuration.md` whose classifier column spec 7 fills.
17. **Contiguity gating sits beside the max rule, never replaces it.** The classifier exposes
    per-window scores; stage 3's verdict is INJECTION when `max_score > threshold` **or** at least
    `promptguard_contiguity_windows` (default 2) consecutive windows score
    `>= promptguard_contiguity_threshold` (default 0.5); `0` windows disables the rule;
    `flagged_chunks` carries the contiguous run.
18. **The benchmark is an owner gate.** The harness is autonomous and hermetic; the *run* needs the
    gated weights, an HF token supplied only through an env file referenced by path, and the reference
    container, so spec 7 US-004 is a declared human gate like the Brave capture and the `v1.1.0` cut.
    Numbers recorded: RSS after load, cold and warm p50/p95 classify latency for a 512-token and a
    64-chunk input, 22M and 86M, at `FORAGE_CPUS=1` and `4`.
19. **The validation 422 body loses `input`/`ctx`/`url`** via an app-level `RequestValidationError`
    handler re-emitting `{loc, msg, type}` — the documented `ValidationErrorDetail` shape. The dropped
    keys were never in the documented schema, so this is a PATCH-class tightening announced inside the
    1.3.0 window; the ruling is recorded in GOVERNANCE.md and the SECURITY.md reflector note moves to
    "closed".
20. **Release plumbing repeats the `v1.1.0` runbook verbatim**: compose pins and `_FORAGE_RELEASE_TAG`
    move to `1.2.0` before the tag, both smoke modes run with `--anchor`, the credential-free pull
    follows `docker image rm` (audit -014), and the handoff table copies the shape of the archived
    `feature-search-release.md` record.

### Resolution map (which story owns which finding)

| Finding | Owner |
|---|---|
| Audit 2026-09-16-016 newline collapse before the structural scan | Spec 1 US-001 |
| Audit -032 URL scanned only decoded, -033 forbidden host code points | Spec 1 US-002 |
| WA-E "search result URLs never checked against private-IP or blocklist rules" | Spec 1 US-003 |
| Audit -014 unbounded `engine` pass-through; contract window opens | Spec 1 US-004 |
| WA-D `/retrieve` chunk budget and semaphore | Spec 2 US-001 |
| WA-D sync PDF/HTML/structural scan on the event loop, un-sandboxed PDF | Spec 2 US-002 |
| WA-D corrupted cache entry → 500 | Spec 2 US-003 |
| WA-D fail-closed policy caller-controlled (owner decision 3) | Spec 2 US-004 |
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
| Audit -010 mutable tag in compose examples, -014 pull proof | Spec 8 US-002, US-003 |

## Contract versioning (governance approach for this epic)

One MINOR bump, `1.2.0` → `1.3.0`, opened early (ruling 5) and closed by spec 8 US-002. Additions:
`blocked_url` omission reason (spec 1), bounded `engine` (spec 1, tightening of what Forage emits),
`effective_promptguard_fail_closed` on `RetrievedContent` and `SearchResponse` (spec 2),
`CacheMetrics.corrupt_entries` / `integrity_rejects` (specs 2, 4), `SearchRequest.blocked_domains` and
`promptguard_threshold: float | None` on both request models (spec 3), `cache_unauthenticated`
`DegradedReason` (spec 4), `policy_unknown_provider` per-request unit (spec 5),
`search.promptguard_latency_target_exceeded` (spec 6), `/health` `promptguard_model` (spec 7), and the
`{loc, msg, type}`-only validation 422 (spec 8, PATCH-class inside the same window). Every line is in the
`CONTRACT_VERSION` docstring entry and therefore in the `v1.2.0` Release body. Poppy's consuming epic
mirrors the defaulted fields; nothing here removes or redefines a member.

## Success Criteria

- The audit -016 parity case — a `System:` marker after a paragraph break inside the second chunk of a
  search result — is `structural_blocked` on `/search` and blocked on `/retrieve`, with one shared test.
- A search result with a literal private-IP host, a blocklisted host, `evil.com\.good.com`, or an IPv6
  zone id never reaches the wire: omitted under `blocked_url` or `invalid_url`, zero DNS lookups.
- A 65-chunk page on `/retrieve` classifies at most 64 chunks; two concurrent `/retrieve` classifications
  serialise; a concurrent `/health` completes while a slow extraction runs.
- With `FORAGE_CACHE_HMAC_KEY` set, a hand-written Valkey value is served zero times; without it and
  with `VALKEY_URL` set, `/health` lists `cache_unauthenticated`.
- A 2 MiB SearXNG body, a gzip-bombed Brave body and a trickling Brave response each produce a
  classified `ProviderFailure` within the configured wall-clock timeout, never an unbounded read.
- `FORAGE_CPUS=4 FORAGE_MEM_LIMIT=4g docker compose up` runs the same image with the documented knob
  values; the sizing table carries measured classifier numbers for 22M and 86M at 1 and 4 CPUs.
- `FORAGE_MODEL_ID=meta-llama/Llama-Prompt-Guard-2-86M` boots, reports `promptguard_model`, and a
  two-window `0.6, 0.6` sequence is INJECTION while `0.6, 0.2, 0.6` is not.
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
- Findings from the search epic's validation runs are quoted by id; the full text is in the (gitignored)
  `kit_tools/AUDIT_FINDINGS.md` of the main checkout and in the triage record of 2026-09-19.
