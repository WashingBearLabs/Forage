<!-- Template Version: 2.1.0 -->
---
epic: forage-structural-hardening
status: active
vision_ref: "T2.3 follow-up — close the injection corpus's structural findings"
created: 2026-10-06
updated: 2026-10-06
---

# Epic: Forage Structural Hardening — Close the Stage-2 Leaks the Corpus Found

> Planned 2026-10-06 with `/kit-tools:plan-epic`. Runs **in Forage**, after
> `epic-forage-injection-corpus` (completed 2026-10-05) and the 86M-default ruling
> (`feat/86m-default`, 2026-10-06). The corpus measured the defences; this epic fixes the
> structural (stage 1 / 2 / 4) part of what it found. **Validate with
> `/kit-tools:validate-epic` before executing.**

## Goal

The injection corpus (`docs/corpus.md`, baseline `tests/corpus/baseline.json`) filed 52
findings in `kit_tools/AUDIT_FINDINGS.md` ("Corpus findings 2026-10-04"). This epic closes the
**30 structural ones**:
- **22 leaked structural (category, route) cells.** These are 38 attack records that evade the
  24 stage-2 patterns through letter case, HTML entities, Unicode confusables, inline tags
  splitting a keyword, a newline inside a non-DOTALL trigger, or markup the parser consumes.
- **3 blocked-but-leaked records.** The page is blocked, but its title still reaches the wire.
- **The `css_offscreen` and `hidden_div` hidden-markup carriers**, plus the four `hidden_markup`
  / carrier aggregate rows they drive.

Every root cause was traced and a fix verified during planning
(`feature-structural-scan-forms.md` → Research Findings).

**The constraint that shapes the whole epic:** stage 3's input text (`ExtractionResult.raw_text`
and `/search`'s joined wire forms) stays **byte-identical**. The CI gate replays owner-recorded
classifier scores keyed by the sha256 of that text, so changing it would force an owner
re-recording of both cassettes. Stage 2 therefore gains extra, scan-only **derived forms** of
the same text, as `/search` already does. The text is never rewritten.

## Decomposition

| Seq | Feature Spec | Status | Dependencies |
|-----|-------------|--------|--------------|
| 1 | [feature-structural-scan-forms.md](feature-structural-scan-forms.md) — case-insensitive patterns; shared scan-form builder (entity decode, control strip, collapse) on all routes; generated confusable fold | Planned | None |
| 2 | [feature-structural-markup-surface.md](feature-structural-markup-surface.md) — inline-tag-unwrapped scan form; bounded raw-markup scan for tag-consumed triggers | Planned | structural-scan-forms |
| 3 | [feature-structural-wire-closure.md](feature-structural-wire-closure.md) — quarantine a blocked page's title; Forage-owned visibility pass on the served body | Planned | structural-markup-surface |
| 4 | [feature-structural-closeout.md](feature-structural-closeout.md) — floors ratchet, findings re-filed from the new baseline, docs and records | Planned | structural-wire-closure |

## Completion Criteria

- [ ] All four feature specs completed and archived.
- [ ] Every one of the 22 structural leaked-cell findings (2026-10-04-004 … -025) and the three
      blocked-but-leaked findings (-043 … -045) is `resolved` against the regenerated baseline.
      Any record still leaking has its own finding with a stated reason.
- [ ] The `css_offscreen` and `hidden_div` carrier findings (-040, -041) are resolved.
      `title_stuffing` (-042) is re-filed as an accepted residual: unmarked text in a title is
      stage 3's job.
- [ ] Both cassettes replay every record with **no re-recording**: the cassette files are
      byte-unchanged at the end of the epic.
- [ ] No core-genre benign false-positive rate rises on any route
      (`news`/`docs`/`forum`/`ecommerce`/`code`). Every pinned benign record's pin holds.
- [ ] Each catch floor that improved is raised in `tests/corpus/floors.json` (ratchet); none is
      lowered.
- [ ] Contract stays `1.3.0`: `contract/openapi.yaml.sha256` unchanged, unless spec 3 US-001's
      governance check rules otherwise, in which case the ruling is recorded.
- [ ] Every `sanitizer_revision` rotation is recorded (CLAUDE.md, `docs/bootstrap-notes.md`,
      the GOTCHAS table) with read-only reversal controls, per house practice.

## Notes

- **Ratchet both ways (owner, 2026-10-06).** Catch may only rise, and core-genre false-positive
  rates may not rise. Over-defence (`security_prose`, `over_defence_probe`) may not get worse,
  but reducing it is **out of scope**. It is all stage 2, and un-blocking those records would
  send new text to stage 3, which forces a re-record.
- **Out of scope:**
  - The 14 classifier-only categories (`natural_language`, `authority_seo`, …).
  - Feeding normalised text to stage 3. That is a later epic with an owner recording gate;
    landscape research shows Prompt Guard is evaded by character tricks too.
  - Stylesheet- or class-based hiding.
  - A release cut. Which version number to use, given the two published "next MINOR"
    windows, is a separate decision.
- **Acceptance criteria are written per technique class**, not per leaked row: adaptive attackers
  defeat row-shaped fixes (landscape research, arXiv 2510.09023).
- Branch base: this epic stacks on `feat/86m-default` until that merges.
