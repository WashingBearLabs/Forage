<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: none
  required_sections: []
  skip_if: always
  note: Created interactively by create-vision skill, not auto-seeded
-->
# PRODUCT_VISION.md

> **TEMPLATE_INTENT:** Singular strategic document capturing the product's why, who, and what. Guides feature planning and prioritization. One per project.

> Last updated: 2026-09-17
> Updated by: Claude (drafted from the Poppy Web Access family context — landing `WEB_ACCESS_FAMILY.md`, the 2026-08-28 provider-independence ADR, and the 2026-09-12 §10 "extraction + telemetry service, not a trust boundary" ADR)

---

## Product Vision Statement

Forage is **safe web access for LLM agents, self-hosted**. It goes out, fetches search results and
web pages, runs everything through a multi-stage sanitization pipeline plus an injection-detection
classifier, and returns only what's safe to feed a model — reducing (never eliminating) the indirect
prompt-injection and untrusted-content risk that comes with putting the open web in front of an
autonomous agent. It ships as a small, headless, 12-factor HTTP service that any agent stack can run
next to its models: provider-independent, local-first, and honest about its own health. The name is
deliberate and deliberately un-boastful — a forager goes out, finds food, and washes it before eating
(the washing-bear story behind the sanitization pipeline); it does **not** claim to make the web safe,
only to bring back what's safe to eat.

---

## Target Users & Personas

| Persona | Role / Context | Primary Need | Pain Point |
|---------|---------------|--------------|------------|
| Agent builder | Indie/OSS developer wiring web access into an LLM agent | Search + page-read that doesn't pipe attacker-controlled text straight into the model | Raw search APIs return unsanitized snippets/pages; hand-rolling SSRF + injection defense is hard and easy to get wrong |
| Self-hosting operator | Privacy-conscious operator running their own AI stack (e.g. Poppy) | Provider-independent, local-first web access with no vendor lock-in | SaaS search/answer engines leak queries, lock you to their model, and can't be self-hosted |
| Platform / security engineer | Team giving agents a defensible web-ingress boundary | An auditable, measured web-access boundary with honest failure signals | Injection defense is usually *asserted*, not measured; failures are silent |

---

## Value Proposition

**For** developers and operators putting the open web in front of LLM agents
**Who** need search and page-reading that won't feed attacker-controlled or vendor-locked content into the model
**This product** is a self-hosted HTTP service that sanitizes every search result and fetched page through a multi-stage pipeline + injection classifier, behind a versioned contract, with honest fail-loud health
**Unlike** raw search APIs (Brave/Tavily/Serper) that return unsanitized text, or answer engines (Perplexity) that also do the reasoning and lock you to their model
**Our approach** is local-first + provider-independent + defense-in-depth — structural sanitization, a PromptGuard injection tripwire, and IP-level SSRF/DNS-rebinding defense — treating retrieved content as untrusted *data, never commands*, and reporting every degraded state instead of failing silently.

> **Honest posture (do not overclaim).** Forage is an **extraction + telemetry service, not a trust
> boundary** (§10 ADR). Its classifier and pipeline *reduce* injection risk as one layer of
> defense-in-depth; they are known-evadable and not a guarantee. The canonical trust decisions
> (activation, provenance, egress, write-firewall) live in the *consumer* (in Poppy's case,
> `poppy/core/retrieval/`), which owns every trust decision and talks to Forage over a versioned
> contract it refuses to activate against on mismatch.

---

## Success Criteria

| Criterion | Metric | Target |
|-----------|--------|--------|
| Genuinely reusable | A third party runs Forage from the example compose fragment with only `HF_TOKEN` (+ `SEARXNG_SECRET`) and gets working `/search` + `/retrieve` | Achieved (Epic: extraction, v1.0.0); holds for every release |
| Key-less floor | A deployment with **no** paid search key still returns results on free engines | No crash, no new required secret, no degradation of the default path |
| Nothing unsanitized reaches the model | Every returned result (snippet, chunk, page) passes the sanitization pipeline; injection signals emitted, never silently dropped | 100% of results classified; discards counted in telemetry |
| Fails loud, never silent | `/health` reports every degraded state (weights absent, cache unreachable) and provider status (resolved chain, key presence); a missing paid key is a supported mode, never a degraded state | Each degraded condition has a distinct, documented `/health` signal; provider status is reported without ever exposing a key |
| Safe to depend on | Contract is versioned + drift-gated; the published image carries no secret and is byte-reproducible | Consumers refuse on major mismatch; `docker history` shows no secret; identical diff_ids on rebuild |
| Operator-sizable | CPU/memory + classifier latency targets are documented operator config, not baked to one host | Sizing table in `docs/configuration.md`; defaults sane, overridable by env |

---

## Feature Areas

<!--
Major capability areas organized by tier. Tier-prefixed IDs (T1.1, T1.2, T2.1) avoid renumber pain.
Link to feature specs / epics as they are created.
-->

### Tier 1 — Core (shipped)

#### T1.1 — Extraction & sanitization pipeline
- **Description:** `/retrieve` (fetch + sanitize a URL), `/extract` (internal-upload extraction), `/search` (SearXNG metasearch + snippet sanitization); the 5-stage structural pipeline; the PromptGuard injection tripwire; SSRF / DNS-rebinding defense; the optional content cache.
- **Feature Spec(s):** `epic-forage-extraction-forage-side` (shipped 2026-09-12)
- **Status:** Shipped

#### T1.2 — Packaging, contract & operability
- **Description:** Reproducible, secret-free multi-arch GHCR image (`forage` + `forage-searxng`); weights fetched at start (HF → GHCR mirror), never baked; optional Valkey / in-memory cache; the frozen, versioned OpenAPI contract + drift gate + bump governance; honest fail-loud `/health`.
- **Feature Spec(s):** `epic-forage-extraction-forage-side` (shipped 2026-09-12)
- **Status:** Shipped

### Tier 2 — Extended

#### T2.1 — Search-provider abstraction & reliable search
- **Description:** A pluggable `SearchProvider` seam over the (currently hardwired) SearXNG client; an optional paid backend (Brave, via its LLM-Context endpoint) behind an env-var key; free-first → paid-on-failure fallback with failure-class discrimination; provider/fallback telemetry (metadata only, per ToS) + per-request policy params + `/health` provider status; a documented third-party env contract (`FORAGE_SEARCH_PROVIDERS`, `FORAGE_BRAVE_API_KEY`). SearXNG stays the key-less free floor.
- **Feature Spec(s):** `epic-search-providers` (Web Access family Epic 3, Forage half; shipped 2026-09-18 at `v1.1.0`, contract `1.2.0`)
- **Status:** Shipped

#### T2.2 — Forage hardening
- **Description:** `/retrieve` sanitization parity with `/search`; cache integrity (HMAC); hostname matching; search-result URL audit; PromptGuard 2 **86M** upgrade + **contiguity gating** against chunk-boundary evasion; config single-sourcing; and a **configurable resource envelope** (CPU/mem sizing + classifier latency target as documented operator config, not baked to any one host).
- **Feature Spec(s):** `epic-forage-hardening` (stub — Web Access family Epic 4, Forage half)
- **Status:** Planned (stub)

#### T2.3 — Injection regression corpus (CI)
- **Description:** A curated indirect-injection attack corpus + classifier/pipeline gates wired into Forage CI, so injection-defense efficacy is *measured on every change* rather than asserted.
- **Feature Spec(s):** `epic-forage-injection-corpus` (stub — Web Access family Epic 6, Forage half)
- **Status:** Planned (stub)

### Tier 3 — Future

#### T3.1 — Additional providers & adapters
- **Description:** More `SearchProvider` impls as operators want them (Tavily — caching-friendly, LLM-native; Exa — semantic), and a `LiteLLMProvider` adapter delegating to a LiteLLM `/search` gateway (our result schema already cribs LiteLLM's `{title,url,snippet,date}` shape to make this a drop-in). All optional, operator-supplied, cloud-only — never the free floor.
- **Feature Spec(s):** —
- **Status:** Future

#### T3.2 — Provenance & datamarking hooks
- **Description:** Per-result provenance metadata (engine/domain) so a consumer can do source-trust weighting; clear per-snippet/chunk boundaries so a consumer can datamark/spotlight untrusted text into its model prompt. (The weighting/datamarking *policy* lives in the consumer; Forage exposes the signals.)
- **Feature Spec(s):** —
- **Status:** Future

#### T3.3 — Web image/vision ingestion
- **Description:** Ingesting images from the web for multimodal agents. Explicitly gated on its own trust review — PromptGuard doesn't see pixels.
- **Feature Spec(s):** —
- **Status:** Future (needs trust review first)

---

## Build Order

### Dependency Graph

| Feature | Depends On | Notes |
|---------|-----------|-------|
| T1.1 / T1.2 | — | Shipped foundation (extraction epic, v1.0.0 @ contract 1.1.0) |
| T2.1 | T1.1, T1.2 | Search providers build on the shipped pipeline + versioned contract |
| T2.2 | T1.1, T1.2 | Hardening deepens the shipped pipeline; PG-86M coordinates with the resource envelope |
| T2.3 | T2.2 (partly) | The corpus gates the classifier improvements T2.2 makes |
| T3.1 | T2.1 | New providers slot into the T2.1 abstraction |
| T3.2 | T2.1 | Provenance rides the provider result shape |

### Suggested Build Sequence

1. **Phase 1 (done):** T1.1 + T1.2 — extraction, packaging, contract, v1.0.0.
2. **Phase 2 (done):** T2.1 — search-provider abstraction + reliable search, v1.1.0.
3. **Phase 3:** T2.2 — hardening (incl. PG-86M + configurable resource envelope).
4. **Phase 4:** T2.3 — injection regression corpus in CI.
5. **Later:** T3.x — more providers/adapters, provenance/datamarking hooks, image ingestion (post trust-review).

---

## Walking Skeleton

**Slice:** A caller `POST`s a query to `/search`, Forage queries a backend, sanitizes each result through the pipeline, and returns structured results with honest telemetry — reachable from a bare `docker compose up` with only `HF_TOKEN` (+ `SEARXNG_SECRET`) set.

**Layers touched:**
- **API:** the FastAPI `/search` (+ `/retrieve`, `/health`) endpoints
- **Logic:** `run_search_pipeline` → provider → 5-stage sanitization → PromptGuard classifier
- **Data/Storage:** optional Valkey / in-memory content cache (transient only, per provider ToS)
- **Integration:** SearXNG companion image (free floor) + optional operator-supplied paid API

**Proves:** the full extraction + sanitization path works end-to-end from a reusable compose fragment — already validated at v1.0.0; every subsequent feature preserves this slice.

---

## Constraints & Assumptions

### Constraints
- **Provider-independent and local-first is a hard architectural constraint** (2026-08-28 ADR), not a preference: provider-native web/answer tools (Anthropic/OpenAI/Perplexity Sonar server-side search) are excluded; only raw-result search APIs qualify as backends; a self-hosted free path (SearXNG) is always the default.
- **12-factor, no secrets manager:** all config + keys arrive as environment variables at container start. Forage knows nothing about OpenBao/Vault; the consumer owns getting secrets into Forage's env.
- **Small, headless service:** no UI; default sizing (~1 CPU / 1 GB) must be *operator-configurable*, sized to the host, not baked to any one deployment.
- **Licensing:** service code Apache-2.0; PromptGuard weights under the Llama Community License (ship license + NOTICE + "Built with Llama"); provider result payloads are subject to each provider's ToS — some (Brave, Exa) forbid persisting/redistributing, so telemetry stores metadata, never raw paid-provider bodies.
- **Release discipline:** `main` is PR-only behind required checks; images publish through the gated lane; multi-arch (amd64 + arm64); the contract is frozen + drift-gated + governed.

### Assumptions
- The operator brings their own keys (HF token; optional paid search key); a key-less deployment is a first-class supported mode.
- The **consumer** (e.g. Poppy) is the canonical trust/policy layer — Forage emits signals and honest health; it does not make trust decisions.
- The flat module layout (`retrieval_app.py`, `pipeline/`, `promptguard/`, …) is preserved; a `forage/` package rename is deferred.

---

## Open Questions

- [ ] None blocking. Provider *choice* beyond Brave (Tavily/Exa/LiteLLM adapter) is intentionally deferred to T3.1 and driven by operator demand.
