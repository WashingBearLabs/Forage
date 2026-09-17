<!-- Template Version: 2.1.0 -->
---
epic: forage-injection-corpus
status: on-hold
vision_ref: "T2.3 — Injection regression corpus (CI)"
created: 2026-09-14
updated: 2026-09-14
---

# Epic: Forage Injection Regression Corpus — Measure Injection Defense in CI

> **STUB — MUST BE RE-PLANNED before execution (`/kit-tools:plan-epic` then `/kit-tools:validate-epic`).**
> This is the **Forage half of Web Access family Epic 6** (the Poppy half — an end-to-end
> firewall/quarantine suite + tier-1 canary — executes in Poppy). It exists to preserve the 2026-08-30
> review context (scorecard #10, §9 Q4) — **not because it's ready**. Runs **in Forage**.

## Goal (provisional)

Turn "injection defense works" from an assertion into a **measured, regression-gated property**: a
curated corpus of indirect-injection attacks (in search snippets/chunks and fetched page content) run
against Forage's sanitization pipeline + PromptGuard classifier in CI, so every change reports its
attack-catch rate and benign false-positive rate instead of hoping. This is the empirical backstop for
the classifier upgrades in `epic-forage-hardening` (a classifier change with no corpus is a change with
no evidence).

## Candidate scope (to decompose at plan time — NOT final)

- **Attack corpus** — a versioned set of indirect-injection payloads spanning the documented in-the-wild
  vectors: poisoned JSON-LD / Open Graph meta-descriptions surfaced as high-signal snippets (Zscaler,
  Jul 2026); chunk-boundary "Prompt Overflow" evasion (interleaved fragments across token windows);
  answer-engine-poisoning style planted text; classic "ignore previous instructions" in page bodies.
- **Benign counter-corpus** — legitimate content that must NOT trip the classifier (guards the
  false-positive rate; a false positive silently discards a real result).
- **CI gates** — attack-catch rate and benign-FPR thresholds wired as blocking (or tracked-with-alarm)
  jobs, so a regression in either surfaces on the PR. Deterministic + hermetic (no live network).
- **Reporting** — per-run metrics (catch rate, FPR, per-vector breakdown) so classifier/pipeline changes
  are comparable across commits.

## Dependencies / sequencing

- Best landed **after** (or alongside) `epic-forage-hardening`, since the corpus is how the PG2-86M +
  contiguity-gating changes there are validated. Can begin any time after the search epic (there is a
  pipeline + classifier to test).
- The Poppy half (end-to-end firewall/quarantine e2e suite + tier-1 canary) is a separate Poppy epic and
  consumes nothing from this beyond confidence.

## Non-goals (provisional)

- The Poppy-side end-to-end suite / tier-1 canary — Poppy's Epic-6 half.
- Training or fine-tuning a guard model — explicitly out (family non-goal); this measures the guard we
  ship, it doesn't build one.

> Re-plan this stub with a full `/kit-tools:plan-epic` pass before writing any stories; the corpus design
> (sources, licensing of attack samples, threshold-setting method) is itself a planning conversation.
