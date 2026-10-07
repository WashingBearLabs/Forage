<!-- Template Version: 2.1.0 -->
---
epic: forage-v1-3-0-release
status: active
vision_ref: "T2 hardening follow-through — bound the remaining CPU costs and cut v1.3.0"
created: 2026-10-07
updated: 2026-10-07
---

# Epic: Forage v1.3.0 — Bound the Parse, Close the Padding Gate, Cut the Release

> Planned 2026-10-07 with `/kit-tools:plan-epic`, after `epic-forage-structural-hardening`
> merged (PR #42, `7fe91c0`). The owner scoped it that day: both CPU-cost fixes and the padding
> fix ship in v1.3.0, together with the two promised "next MINOR" windows. **Validate with
> `/kit-tools:validate-epic` before executing.** Tag push and publish remain **owner gates**;
> no story pushes a tag.

## Goal

v1.3.0 closes the three resource and evasion findings the structural epic left open, then cuts
the release that the 1.3.0 contract's compatibility windows promised:

1. **Parse cost (finding 2026-10-06-001).** An element-dense 10 MB page costs about 150 s of
   stage-1 parse on `/retrieve`. The parse holds the admission slot and the GIL the whole time.
   - Pages above a size threshold calibrated on four hostile shapes move into the CPU- and
     memory-rlimited worker that fetched PDFs already use, with a coded 422 on overrun. Both
     workers start with a credential-free environment, and stale spool files are swept at
     startup.
   - Realistic pages above about 2 MB are refused by design (owner, 2026-10-07). The 256-chunk
     budget already refuses about 1 MB of realistic text.
   - `/search`'s per-field parse moves off the event loop.
2. **Look-alike padding gate (residual 2026-10-04-060) and fold cost (finding 2026-10-07-002).**
   - Today a refused confusable fold only *flags* the page. On the TRUSTED tier, where stage 3
     is skipped, that leaves no control at all. A refusal will now **BLOCK**.
   - The refusal limit drops from 4× to `max(2n, n + 256)`. The constant slack keeps short
     fields with everyday ligatures from being blocked. The worst accepted fold cost falls to
     about 56% of its 4× figure (measured).
3. **The two next-MINOR windows.**
   - The `/retrieve` `max_promptguard_chunks` default flips 0 → 256 (ruling (g)). `0` stays the
     opt-out.
   - Request-validation 422s drop the `"[redacted]"` `input`/`ctx`/`url` placeholder keys
     (ruling (l)).
   - Together these are contract **1.3.0 → 1.4.0**.
4. **The release.** Release notes, image-pin fan-out, and a ready-to-tag tree. The image tag
   is `v1.3.0`; the contract is `1.4.0`. They are two different version numbers.

## Decomposition

| Seq | Feature Spec | Status | Dependencies |
|-----|-------------|--------|--------------|
| 1 | [feature-release-resource-bounds.md](feature-release-resource-bounds.md) — 5 stories: worker plumbing and environment scrub, HTML worker, threshold routing and telemetry, held contract 1.4.0, `/search` off the loop | Planned | None |
| 2 | [feature-release-padding-gate.md](feature-release-padding-gate.md) — 1 story: fold limit `max(2n, n + 256)`, refusal BLOCKs on every route | Planned | None (runs after 1 for ordering only) |
| 3 | [feature-release-1-3-0.md](feature-release-1-3-0.md) — 5 stories: budget default 256, measured sizing docs, 422 key drop and final 1.4.0 entry, GOVERNANCE closure, release prep | Planned | 1, 2 |

## Completion Criteria

- [ ] All three feature specs completed and archived.
- [ ] Hostile inputs are bounded, measured on the CI runner and recorded in
      `docs/bootstrap-notes.md`.
  - An element-dense `/retrieve` page above the threshold is refused with a coded 422 within
    the worker's CPU limit.
  - No `/retrieve` HTML parse runs in-process above the threshold.
  - `/search` never runs `extract_html` on the event loop thread.
- [ ] A refused fold is a BLOCK on `/retrieve`, `/extract` and `/search`. The exact benign false
      positive rate is unchanged against the pre-epic baseline, checked by
      `scripts.corpus.floors_diff --baseline-fpr`.
- [ ] `CONTRACT_VERSION == "1.4.0"`, with `tests/golden/contract_1_4_0.json` added and the
      OpenAPI file and its anchor regenerated.
- [ ] Every `sanitizer_revision` rotation is recorded in `CLAUDE.md`,
      `docs/bootstrap-notes.md` and the GOTCHAS table, each with read-only reversal controls.
- [ ] `docs/releases.md` carries a complete v1.3.0 entry (the tagged commit is filled at tag
      time), and every image pin names `1.3.0`.
- [ ] `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and
      `uv run pyright` are green, and CI is green on the epic PR.
- [ ] **Owner gates (not stories):** merge the PR, push the `v1.3.0` tag, watch `publish`
      go green, and verify an anonymous pull.

## Notes

- **Pre-epic reference commit:** the commit that adds these specs. Every "pre-epic" comparison
  in the three specs means `git show <that commit>:<path>`; the first story records the hash in
  its Implementation Notes.
- **Execution:** guarded mode. Every spec is **size L**, because stories regenerate the corpus
  baseline and run the complexity sweep (structural-hardening lesson: M = 900 s timed out on
  exactly these stories).
- **Ordering:** spec 2 is independent of spec 1. Spec 3 must run last, because its contract
  entry describes the other two specs' wire-visible changes (the new HTML refusal reason).
- **No BUMP_VERSION runbook exists** (it was skipped at seeding). `docs/releases.md` is the
  release procedure, and the pushed tag *is* the version. `pyproject.toml`'s `version` is inert
  metadata and stays untouched (`docs/releases.md` "What the tag means").
- **Out of epic:** a compiled HTML parser (`selectolax`/lexbor, or Rust) to cut normal-page
  parse cost. It changes stage-1 output, so it forces a cassette re-record and a corpus
  re-baseline. Filed as a backlog item, not a story. Also out: scrubbing the 73 frozen doc
  payload quotes (finding 2026-10-07-001).
