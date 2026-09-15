<!-- Template Version: 2.1.0 -->
---
epic: forage-hardening
status: on-hold
vision_ref: "T2.2 — Forage hardening"
created: 2026-09-14
updated: 2026-09-14
---

# Epic: Forage Hardening — Retrieve Parity, Cache Integrity, PromptGuard 86M + Configurable Envelope

> **STUB — MUST BE RE-PLANNED before execution (`/kit-tools:plan-epic` then `/kit-tools:validate-epic`).**
> This is the **Forage half of Web Access family Epic 4**. It exists so the 2026-08-30 review context
> and the findings surfaced during Epics 2–3 aren't lost — **not because it's ready**. Sources: the
> `HOLISTIC_REVIEW_WEB_ACCESS_2026-08-28` punch lists (WA-D, WA-E, WA-B cache items), the 2026-08-30
> PromptGuard-alternatives research (summarized in Poppy's `WEB_ACCESS_FAMILY.md`), and the
> configurable-resource-envelope finding raised at the Epic-2 US-007 cutover. Runs **in Forage**;
> the Poppy trust-boundary consolidation is a separate Poppy epic (family Epic 5).

## Goal (provisional)

Deepen Forage's own hardening now that it's a standalone, third-party-reusable service: bring `/retrieve`
to full sanitization parity with `/search`, add cache integrity, tighten hostname/URL handling, upgrade
the injection classifier, and make the service **sizable to the operator's hardware** rather than baked
to any one host. This is the "make the extraction service itself sturdier" epic — distinct from the
Poppy-side trust-boundary consolidation (family Epic 5).

## Candidate scope (to decompose at plan time — NOT final)

- **`/retrieve` sanitization parity** — ensure the single-URL fetch path applies the same stage-2/stage-3
  guarantees the `/search` path does (WA-D).
- **Cache integrity** — HMAC (or equivalent) over cached content so a poisoned/expired cache entry can't
  silently serve unsanitized content (WA-B cache items). Respect provider ToS: no persisting raw
  paid-provider result bodies.
- **Hostname matching / URL audit** — tighten host validation and audit search-result URLs (WA-E) as a
  complement to the IP-level SSRF/rebinding defense.
- **PromptGuard 2 22M → 86M** — multilingual pretraining (AUC .942→.995 non-English); ~350–560 MB fp32,
  fits under 1 GB; **needs an RSS + CPU-latency benchmark story on the target container first** (no
  published CPU numbers). ONNX/int8 as a fallback if needed.
- **Contiguity gating** — mitigate the "Prompt Overflow" / chunk-boundary evasion of PG2 (interleaving
  fragments across 512-token windows defeats max-pooling): add an N-consecutive-windows-over-a-lower-
  threshold (~0.5) rule alongside the existing 0.85 max rule; keep the 64-token overlap. **Relevant to
  Epic-3's chunk results** — chunk classification is exactly where this matters.
- **Config single-sourcing** — one authoritative place for the engine list ↔ SearXNG settings, thresholds,
  and provider config (removes the drift class the extraction epic flagged).
- **Configurable resource envelope (open-source readiness)** — CPU/mem sizing + the PromptGuard latency
  target as **documented operator config** (compose fragments + a `docs/configuration.md` sizing table),
  sized to the host, not to any one deployment. Surfaced 2026-09-12 at the Epic-2 cutover: the hard 1-CPU
  cap makes PromptGuard CPU-bound and serializes parallel search + fetch → client timeouts
  (`search_promptguard_local_latency_target_exceeded`). Owner (28-core host) explicitly declined a
  self-specced bump — make it configurable instead.

## Dependencies / sequencing

- Follows `epic-search-providers` (this repo) — contiguity gating operates on the chunk results that epic
  introduces; the envelope work should account for the added provider/fallback CPU cost.
- Coordinates with the Poppy family Epic 5 (trust-boundary) and Epic 6 (injection corpus — the corpus is
  how the classifier upgrades here get *measured*).

## Non-goals (provisional)

- Poppy-side trust-boundary work (activation, one-turn-wait re-arm, datamarking policy, egress network) —
  family Epic 5, executes in Poppy.
- New search providers — Epic-3 / Tier-3 future.

> Re-plan this stub with a full `/kit-tools:plan-epic` pass against the then-current Forage layout before
> writing any stories.
