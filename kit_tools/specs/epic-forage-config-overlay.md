<!-- Template Version: 2.1.0 -->
---
epic: forage-config-overlay
status: active
vision_ref: "T2.2 — Forage hardening (operator configuration, single source)"
created: 2026-11-11
updated: 2026-11-11
---

# Epic: Forage Config Overlay — Change One Key Without Copying the File

> Planned 2026-11-11 with `/kit-tools:plan-epic`, as the follow-up to PR #52
> (`FORAGE_EXTRACT_ROUTE_ENABLED`, contract 1.6.0). Build on a tree where #52 is merged:
> the overlay sits between that env override and the baked file. Validate with
> `/kit-tools:validate-epic` before executing.

## Goal

Today `_load_config()` reads the baked `/app/config.yaml` and nothing else. The only supported
way to change a key is to mount a **complete** replacement file ("replace, never merge"). Two
consequences follow:

- Every Forage upgrade means re-syncing about 90 lines of tuned security and sizing bounds by
  hand.
- A shorter file silently resets every omitted key to its code default.

Poppy vendors the whole file under a sha256 pin and a drift test just to stay safe.

This epic adds `FORAGE_CONFIG_OVERLAY`, the path of a small YAML file **merged over** the baked
defaults. An operator states only the keys they change. An upgrade then picks up the new image's
tuned defaults, and the overlay re-applies the operator's deltas on top.

The overlay is strict:
- a malformed, unknown or `null` entry refuses the boot;
- a missing or unreadable overlay file refuses the boot too;
- one boot WARNING names every key the overlay set (names only).

It changes no wire byte and no hashed source: there is **no contract bump and no
`sanitizer_revision` rotation**.

## Decomposition

| Seq | Feature Spec | Status | Dependencies |
|-----|-------------|--------|--------------|
| 1 | [feature-config-overlay.md](feature-config-overlay.md) | Planned | PR #52 merged |

One spec with three stories:
1. the pure merge and validation function;
2. the lifespan wiring (env var, file read, refusals, boot log);
3. operator docs, compose recipe and the decision record.

The work has no second independently meaningful concern, so it is not split further.

## Completion Criteria

- [ ] `feature-config-overlay` completed and archived
- [ ] A container started with only `FORAGE_CONFIG_OVERLAY` pointing at a one-key file serves
      exactly the baked configuration with that one key changed. Every other key, including
      every security-relevant key, keeps its shipped value.
- [ ] `docs/configuration.md` recommends the overlay as the default way to change a key, and keeps
      the full-replacement mount as a supported alternative
- [ ] Poppy consumer note written, so Poppy can replace its vendored full copy with an overlay
      (Poppy executes its own half)

## Notes

- "Replace, never merge" was never a standalone decision. `kit_tools/arch/DECISIONS.md` (resource
  envelope, Consequences) records it as a *consequence* and names the silent-reset risk this epic
  removes. A new DECISIONS entry records the overlay's merge semantics.
- Effective-value logging stays declined (the same entry). The overlay logs key **names** once,
  at WARNING, because an overlay is by definition a deliberate deviation from shipped defaults.
