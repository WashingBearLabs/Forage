<!-- Template Version: 2.5.0 -->
---
feature: config-overlay
status: active
session_ready: true
depends_on: []
vision_ref: "T2.2 — Forage hardening (operator configuration, single source)"
type: epic-child
size: M
epic: forage-config-overlay
epic_seq: 1
epic_final: true
created: 2026-11-11
updated: 2026-11-11
---

# Feature Spec: Config Overlay — `FORAGE_CONFIG_OVERLAY` Merged Over the Baked Defaults

## Overview

`retrieval_app._load_config()` (`retrieval_app.py:514`) reads `/app/config.yaml` and returns it
whole. The only way to change one key is to bind-mount a complete replacement file.
`docs/configuration.md` § "Replace, never merge" warns that a short file silently resets every
omitted key to its code default, security bounds included. That makes every upgrade a manual
re-sync of about 90 tuned lines. A consumer (Poppy) vendors the whole file under a sha256 pin and
a drift test to stay safe.

This spec adds **`FORAGE_CONFIG_OVERLAY`**, a path to a YAML file merged over whichever base file
is at `/app/config.yaml` (baked or fully replaced).

**Merge rules:**
- Mapping blocks (`cache:`, `extraction:`, `retrieve:`) merge key by key.
- Every other value, **lists included**, replaces the base value outright.

**Refusals:** the overlay file is validated strictly, and any of these refuses the boot with a
closed reason token, never echoing a value or the file's contents:
- a missing or unreadable file;
- a non-mapping document;
- an unknown or misspelled key;
- a `null`;
- a scalar where the base holds a block, or a block where the base holds a scalar.

**Boot log:** one WARNING names every dotted key the overlay set, so a change in posture is visible
in `docker logs`.

**Precedence for the one key with its own variable:** `FORAGE_EXTRACT_ROUTE_ENABLED` (PR #52), then
the overlay, then the base file, then the code default.

No response shape changes and no `_REVISION_SOURCES` file moves, so there is no contract bump and
no `sanitizer_revision` rotation. The revision still hashes the *effective* threshold and
contiguity values, which now come from the merged mapping.

## Goals

- An operator changes any single known key by supplying a file that contains only that key. The
  served effective configuration equals the base file with exactly that key changed. Pinned by a
  test that compares the merged mapping key-for-key against the baked `config.yaml`.
- Zero silent outcomes. Each of the six overlay failure classes (missing, unreadable, invalid YAML
  or non-mapping, unknown key, `null`, shape mismatch) refuses the boot, and the operator can tell
  them apart from the error token alone.
- One WARNING line per boot when an overlay applied, naming 100% of the keys it set. No overlay
  value appears in any log line.
- Deployments without `FORAGE_CONFIG_OVERLAY` behave byte-identically: same effective config,
  same `sanitizer_revision`, and the full suite unchanged apart from the new tests.

## User Stories

### US-001: Pure overlay merge and validation

**Priority:** P1

**Description:** As an operator, I want a small overlay merged over the shipped config by fixed,
documented rules, so that I state only the keys I change and keep every shipped default I didn't
touch.

**Independent Test:** Unit-test the pure `merge_config_overlay(base, overlay, known_keys)` against
the baked `config.yaml` with hand-built overlays. This delivers the complete merge contract with no
lifespan, filesystem or app involved.

**Implementation Hints:**
- New module `pipeline/config_overlay.py`, beside `pipeline/config_bounds.py`, which is the
  precedent for small config helpers in `pipeline/`. It is **not** a `_REVISION_SOURCES` member
  (`pipeline/sanitizer_revision.py:16`). Do not add it there: the revision hashes effective
  values, not the merge code.
- Invariant 3: no `forage/` package, flat layout. A new file under the existing `pipeline/`
  package is fine.
- Take `known_keys: frozenset[str]` as a parameter rather than importing `KNOWN_CONFIG_KEYS` from
  `retrieval_app` (`retrieval_app.py:447`). A `pipeline/` module importing `retrieval_app` would
  invert the dependency direction. The dotted-key convention ("block.leaf" for block keys, the
  bare block name also listed) is the one `_warn_unknown_config_keys` (`retrieval_app.py:497`)
  already uses; reuse its top-level/blocks derivation logic, extracting a shared helper if that
  is cleaner.
- Define `class ConfigOverlayError(ValueError)` carrying a closed token: `overlay_not_mapping`,
  `overlay_unknown_key`, `overlay_null_value`, `overlay_shape_mismatch`. The message names the
  token and the **dotted key name** only (key names are operator-chosen schema words, not
  secrets), never the value. Follow `ExtractionConfigurationError`'s message style
  (`pipeline/extraction_limits.py`).
- Return the merged mapping **and** the sorted tuple of dotted keys the overlay set: a block leaf
  is `extraction.classification_concurrency`, a top-level scalar or list is its bare name. US-002
  logs this tuple.
- Never mutate `base`: build a new dict, and copy the block mappings you merge into, because
  `retrieval_app` later derives `published_config` from the result.
- Value *types and ranges* are **not** checked here. The existing typed readers
  (`extraction_settings_from_config`, `retrieve_settings_from_config`,
  `cache_settings_from_config`, `promptguard_threshold_from_config`, …) already refuse bad values
  at boot, and must keep being the single place that does. This function checks only names,
  `null` and shape.

**Acceptance Criteria:**
- [ ] `merge_config_overlay(base, overlay, known_keys)` exists in `pipeline/config_overlay.py` and
      returns `(merged, set_keys)`; `base` and `overlay` are left unmodified (asserted by deep
      equality with copies taken before the call)
- [ ] Block keys (`cache`, `extraction`, `retrieve`) merge leaf by leaf: an overlay
      `{"extraction": {"classification_concurrency": 2}}` over the baked file yields the baked
      `extraction` block with only that leaf changed
- [ ] Every non-block value replaces outright, **lists included**: an overlay
      `seed_blocklist: [".example"]` yields exactly `[".example"]`, and `news_domains: []` yields
      `[]`, not the shipped list
- [ ] An empty overlay mapping `{}` returns a merged mapping equal to `base` and an empty
      `set_keys`
- [ ] An overlay document that is not a mapping (list, scalar, string) raises
      `ConfigOverlayError` with token `overlay_not_mapping`
- [ ] An unknown top-level key, or an unknown leaf under a known block, raises with token
      `overlay_unknown_key`, naming the dotted key; it is never only warned about
- [ ] A `null` anywhere in the overlay (top-level, block, or block leaf) raises with token
      `overlay_null_value`
- [ ] A non-mapping where the base or `known_keys` treat the key as a block (e.g.
      `extraction: 5`), or a mapping where a scalar or list is expected (e.g.
      `promptguard_threshold: {a: 1}`), raises with token `overlay_shape_mismatch`
- [ ] No `ConfigOverlayError` message contains any overlay value (parametrized test with sentinel
      values across every refusal class)
- [ ] `set_keys` is the sorted tuple of dotted keys the overlay set, exactly
- [ ] Tests written/updated for new functionality (new `tests/test_config_overlay.py`, added to
      `kit_tools/testing/TESTING_GUIDE.md`'s `test_mapping`)
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass with zero
      findings

### US-002: Load the overlay at boot, refuse loudly, log the keys

**Priority:** P1

**Description:** As an operator, I want `FORAGE_CONFIG_OVERLAY=/path/overlay.yaml` to apply my
overlay at container start, refusing to boot on any overlay problem and logging which keys it
changed, so that a deployment can never run with a half-applied or silently ignored override.

**Independent Test:** Lifespan tests with a temporary overlay file show:
- the merged values reach `app.state` (extraction settings, cache settings, threshold default,
  `sanitizer_revision`);
- each failure class refuses the boot;
- exactly one WARNING names the set keys.

This delivers the feature end to end without any doc or compose change.

**Implementation Hints:**
- Extend `_load_config()` (`retrieval_app.py:514`), or add a sibling it calls, so that the
  lifespan's `config = _load_config()` (`retrieval_app.py:1808`) receives the **merged**
  mapping. Everything downstream must see only that mapping:
  `_warn_unknown_config_keys`, `published_config`, every `*_from_config` reader, the
  `extract_route` resolution and `derive_sanitizer_revision`. Many tests monkeypatch
  `retrieval_app._load_config` to return a dict, so keep that seam returning the final mapping.
- The env var is read once at boot. Strip whitespace; unset or blank means "no overlay", which
  is today's path unchanged. Name the constant `CONFIG_OVERLAY_ENV_VAR`.
- File refusals add two tokens to US-001's set: `overlay_missing` (path does not exist or is not
  a regular file) and `overlay_unreadable` (an `OSError` on open/read, `yaml.YAMLError`, or a
  file over a fixed byte bound). Use `yaml.safe_load` only, as `_load_config` already does.
  Bound the read at 1 MiB, refused rather than truncated.
- **Never log or echo the overlay path or contents** in a refusal: invariant 6 and the closed
  log vocabulary (`kit_tools/arch/patterns/LOGGING.md`). The env var's *name* is fine. Raise
  `ConfigOverlayError` out of the lifespan so the boot refuses, as `ExtractionConfigurationError`
  does (see `test_lifespan_refuses_invalid_classification_concurrency` in `tests/test_app.py`
  for the test pattern).
- The success line, once per boot and only when an overlay applied, is WARNING:
  `config_overlay_applied — keys=<comma-joined dotted keys> count=<n>`. An empty overlay logs
  `keys= count=0`. Effective-value logging stays declined (`kit_tools/arch/DECISIONS.md`,
  resource-envelope Consequences); names only.
- `FORAGE_EXTRACT_ROUTE_ENABLED` must keep winning over an overlay's `extract_route_enabled`.
  `resolve_extract_route(config, os.environ)` already reads the config after `_load_config`, so
  only a test is needed to pin the order. Its `source` token stays `config` when the value came
  from the overlay: source is env/config/default, not file-level provenance.
- Hermeticity: add `FORAGE_CONFIG_OVERLAY` to `tests/conftest.py::_CLEARED_ENV_VARS` and to
  the exact-set pin in `tests/test_hermeticity.py`, as PR #52 did for its variable.
- No `/health`, `/metrics` or response-shape change: no contract bump. `retrieval_app.py` is not
  a hashed source: no rotation. Confirm the default-config revision is unchanged with the
  overlay unset (expected: the then-current `HEAD` value).

**Acceptance Criteria:**
- [ ] With `FORAGE_CONFIG_OVERLAY` unset or blank, the effective config and `sanitizer_revision`
      are identical to the base file's (test compares against `yaml.safe_load` of the baked
      `config.yaml` and against `derive_sanitizer_revision` of it)
- [ ] With an overlay setting `extraction.classification_concurrency: 2` and
      `promptguard_threshold: 0.9`, `app.state.extraction_settings.classification_concurrency`
      is 2, `app.state.promptguard_threshold_default` is 0.9, every other extraction value
      equals the baked file's, and `sanitizer_revision` equals the revision of an equivalent
      full file
- [ ] A missing path refuses boot with `overlay_missing`; an unreadable file, invalid YAML, or a
      file over 1 MiB refuses with `overlay_unreadable`; US-001's four tokens refuse at boot too
      (one parametrized lifespan test per class)
- [ ] No refusal's exception text or log record contains the overlay path or any overlay value
      (asserted with sentinel path and value strings and `caplog`)
- [ ] An applied overlay logs exactly one WARNING
      `config_overlay_applied — keys=<sorted dotted keys> count=<n>`; with no overlay no such
      line is logged
- [ ] An overlay value that a typed reader rejects (e.g. `extraction.classification_concurrency:
      9`) still refuses boot through that reader's existing error type, not through
      `ConfigOverlayError`
- [ ] `FORAGE_EXTRACT_ROUTE_ENABLED=0` beats an overlay `extract_route_enabled: true` (route
      closed, `source=env`); with the env var unset, the overlay's `true` opens it with
      `source=config`
- [ ] `FORAGE_CONFIG_OVERLAY` is in `tests/conftest.py::_CLEARED_ENV_VARS` and the
      `tests/test_hermeticity.py` exact-set pin
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass with zero
      findings

### US-003: Operator docs, compose recipe and the decision record

**Priority:** P2

**Description:** As an operator, and as the Poppy maintainer, I want the docs to make the overlay
the recommended way to change configuration, so that nobody copies the whole file by default
again, and so that Poppy can drop its vendored copy.

**Independent Test:** A reader following only `docs/configuration.md` can write a one-key overlay,
mount it with either compose fragment's commented recipe, and confirm it from the
`config_overlay_applied` line. The docs tests (`tests/test_governance_docs.py`,
`tests/test_compose_fragments.py`) stay green.

**Implementation Hints:**
- `docs/configuration.md`:
  - add a `FORAGE_CONFIG_OVERLAY` row to the Runtime env table (beside
    `FORAGE_EXTRACT_ROUTE_ENABLED`);
  - rewrite § "Delivering `config.yaml` through the fragments" to lead with the overlay;
  - reframe "**Replace, never merge.**" as "a full replacement replaces; an overlay merges",
    keeping the silent-reset warning for the full-mount path;
  - state the merge rules (blocks merge, lists replace, `null` refused) and the precedence chain.
- `kit_tools/docs/ENV_REFERENCE.md`: new variable row and the conftest cleared-list sentence.
- `kit_tools/arch/patterns/LOGGING.md` and `kit_tools/docs/MONITORING.md`: the
  `config_overlay_applied` WARNING.
- `kit_tools/docs/TROUBLESHOOTING.md`: the six refusal tokens, each with its fix.
- `kit_tools/arch/DECISIONS.md`: a new entry, "Config overlay: blocks merge, lists replace,
  strict names", recording the four clarifications below and why loosening is allowed but
  logged. Update the resource-envelope entry's "replaces rather than merges" consequence to
  point to it.
- `compose/minimal.yml` and `compose/full.yml`: a **commented** recipe (volume line plus
  environment line) near the existing passthrough block. Do not add a live bare passthrough: an
  env var naming a container path without its mount refuses boot. Check
  `tests/test_compose_fragments.py` still passes; it parses the fragments.
- `kit_tools/docs/LOCAL_DEV.md` (`docker run -v … -e FORAGE_CONFIG_OVERLAY=…` example),
  `kit_tools/docs/DEPLOYMENT.md` (read once at start), `README.md` if its quickstart mentions
  mounting config.
- `docs/releases.md` Unreleased: one bullet. `docs/bootstrap-notes.md`: a short Poppy consumer
  note covering:
  - replace the vendored `config/forage/config.yaml` with an overlay of only Poppy's deltas;
  - drop the sha256 pin and the drift test;
  - every upgrade now inherits Forage's tuned defaults.
- `kit_tools/docs/GOTCHAS.md`: "Lists in an overlay replace, they never append". An operator
  adding one `seed_blocklist` entry must restate the full list. The shipped list is empty today,
  but `news_domains` and `user_agents` are not.

**Acceptance Criteria:**
- [ ] `docs/configuration.md` documents `FORAGE_CONFIG_OVERLAY`:
  - the merge rules;
  - the precedence chain (`FORAGE_EXTRACT_ROUTE_ENABLED`, then overlay, then base file, then
    code default);
  - the 1 MiB bound;
  - every refusal token.

  It presents the overlay before the full-replacement mount.
- [ ] `kit_tools/docs/ENV_REFERENCE.md`, `LOGGING.md`, `MONITORING.md` and `TROUBLESHOOTING.md`
      carry the variable, the WARNING line and the six refusal tokens respectively
- [ ] `kit_tools/arch/DECISIONS.md` has the new overlay entry, and the resource-envelope
      consequence points to it
- [ ] Both compose fragments carry a commented overlay recipe, and `tests/test_compose_fragments.py`
      passes unchanged or with a test pinning that the variable is **not** a live passthrough
- [ ] `docs/bootstrap-notes.md` carries the Poppy consumer note; `docs/releases.md` Unreleased
      carries one bullet
- [ ] `kit_tools/docs/GOTCHAS.md` carries the list-replacement entry
- [ ] Full test suite passes (`uv run pytest`): the docs tests guard the anchors and counts

## Edge Cases

- Overlay `{}` or a YAML file containing only comments (which `safe_load` reads as `None`): an
  empty overlay is a no-op that still logs `count=0`; a `None` document is treated as `{}`.
  (US-001 for the mapping; US-002 for the `None` document)
- Overlay sets a block to `{}` (`cache: {}`): merges nothing, so the base block is unchanged.
  `cache` is not in `set_keys` unless a leaf was set. (US-001)
- Overlay sets a key whose base value is absent (a known key the base file omits, e.g. a full
  replacement that dropped it): it is added, and the readers validate it. (US-001)
- Overlay sets a key to the same value the base holds: still listed in `set_keys`; the log names
  intent, not difference. (US-001)
- Base `/app/config.yaml` is itself a full replacement mount: the overlay merges over that, not
  over the image's baked file. (US-002; documented in US-003)
- Base file missing (today's `config.yaml not found` WARNING path, base `{}`): the overlay merges
  over `{}` and its keys are validated against `known_keys` and shape as usual; a block in the
  overlay is accepted as a block. (US-001/US-002)
- `FORAGE_CONFIG_OVERLAY` is set but blank or whitespace: "no overlay", today's path. (US-002)
- Path is a directory, or a symlink to nothing: `overlay_missing`. (US-002)
- Overlay file over 1 MiB: `overlay_unreadable`, refused, not truncated. (US-002)
- YAML tags or anchors: `safe_load` refuses unsafe tags (→ `overlay_unreadable`); plain anchors
  and aliases resolve normally. (US-002)
- Overlay contains an unknown key *and* a bad value: the unknown-key refusal fires first, because
  name validation runs before the typed readers. (US-001/US-002)

## Out of Scope

- **Per-key loosening rules** (refusing an overlay that weakens a security bound). Rejected in
  clarification: it is a policy engine, not an overlay. Every overlaid key is named in the WARNING
  instead.
- **Environment variables for any other individual key.** `FORAGE_EXTRACT_ROUTE_ENABLED` stays the
  only one; the overlay is the general mechanism.
- **Multiple overlay files, directories (`config.d/`) or inline YAML in an env var.**
- **Exposing overlay state on `/health` or `/metrics`.** That would be a contract change. The boot
  WARNING is the signal; revisit only if a consumer needs it on the wire.
- **List append or merge syntax** (e.g. `seed_blocklist+:`).
- **Hot reload.** Read once at start, like every other setting.
- **Changing the baked `config.yaml`'s values**, or removing full-replacement mounts.
- **Poppy's adoption.** It executes in Poppy, from the consumer note.

## Assumptions

- PR #52 (`FORAGE_EXTRACT_ROUTE_ENABLED`, contract 1.6.0) is merged before execution, and its
  precedence (env beats config) is the one this spec extends.
- Only three keys are mapping blocks (`cache`, `extraction`, `retrieve`), and blocks nest exactly
  one level, as in `KNOWN_CONFIG_KEYS` today. A future block keeps the same "block.leaf"
  convention.
- `null` is refused rather than read as "reset to code default". It is ambiguous, and "reset" is
  achievable by stating the default value explicitly.
- A `None` YAML document (empty or comments-only file) is an empty overlay, not an error. The
  operator pointed at a real file; the log line's `count=0` makes it visible.
- Key *names* in refusal messages and in the WARNING are not sensitive (schema words). Values and
  the path are never logged.
- 1 MiB is ample: the full shipped file is about 6 KB, and the largest legitimate overlay is a
  long domain list, which `policy_domain_entries_max_bytes` caps at 64 KiB per request list
  anyway.
- `bench/config.yaml` and the corpus tooling load their own config files directly and are
  unaffected.

## Technical Considerations

- **No contract bump, no rotation.** Responses are unchanged. `retrieval_app.py` and the new
  `pipeline/config_overlay.py` are outside `_REVISION_SOURCES`. The revision's config inputs
  (`promptguard_threshold`, contiguity) are read from the merged mapping, so an overlay that
  changes them changes the revision exactly as an equivalent full file would. That is
  intended, and it is pinned by US-002.
- **Single validation path.** Name, null and shape checks live in the overlay merge (US-001).
  Value validation stays in the existing typed readers, so a value is never validated twice
  under two different rules.
- **Strict overlay, lenient base.** The base file keeps today's warn-only unknown-key rule
  (`config_unknown_key`), because a full replacement may lag a release. The overlay is strict,
  because its only purpose is to change named keys. Both rules are documented.
- **Invariant 6 / closed log vocabulary:** refusals carry tokens and key names only.
- **Invariant 5 (loud degradation):** every overlay failure refuses the boot; nothing degrades
  silently.
- Architecture: `kit_tools/arch/CODE_ARCH.md` gains one row for `pipeline/config_overlay.py`.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Decisions: [DECISIONS.md](../arch/DECISIONS.md) (resource envelope, Consequences)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)
- Conventions: [CONVENTIONS.md](../docs/CONVENTIONS.md)
- Logging: [LOGGING.md](../arch/patterns/LOGGING.md)
- Canonical config reference: [`docs/configuration.md`](../../docs/configuration.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Merge inside the `_load_config()` seam, so the lifespan receives one final mapping.
**Rationale:** Every consumer (`_warn_unknown_config_keys`, `published_config`, the typed readers,
`resolve_extract_route`, `derive_sanitizer_revision`) reads the single `config` local in the
lifespan, and dozens of tests monkeypatch `_load_config`. Merging there touches one seam.
**Alternatives considered:** Merging in each reader (duplicated, easy to miss one); merging after
`_warn_unknown_config_keys` (the base warning would then not see overlay keys, splitting the
validation story).
**Source:** `retrieval_app.py:514` (`_load_config`), `retrieval_app.py:1808` (call site),
`tests/test_app.py` (`monkeypatch.setattr(retrieval_app, "_load_config", ...)`).

**Decision:** `pipeline/config_overlay.py` takes `known_keys` as a parameter.
**Rationale:** `KNOWN_CONFIG_KEYS` lives in `retrieval_app.py:447`. A `pipeline/` module importing
the app module would invert the dependency direction the repo keeps
(`pipeline/config_bounds.py` imports nothing from the app).
**Alternatives considered:** Moving `KNOWN_CONFIG_KEYS` into `pipeline/`. That is acceptable too,
but it widens the diff and isn't needed.
**Source:** `retrieval_app.py:445-495`, `pipeline/config_bounds.py`.

**Decision:** "Replace, never merge" is extended, not re-litigated.
**Rationale:** `kit_tools/arch/DECISIONS.md` records it only as a *consequence* of the envelope
work, explicitly naming the silent-reset risk. No ruling chose replacement over merging.
**Source:** `kit_tools/arch/DECISIONS.md` (resource-envelope entry, Consequences, about line 956).

**Decision:** No live compose passthrough; commented recipe only.
**Rationale:** Unlike PR #52's flag, this variable names a path inside the container. Passing it
through without the matching volume refuses the boot, which is a worse default than not offering
it.
**Source:** `compose/minimal.yml` environment block; `tests/test_compose_fragments.py`.

### Scope Adjustments

- The boot log and the `FORAGE_EXTRACT_ROUTE_ENABLED` precedence pin were folded into US-002
  rather than given their own stories: each is a criterion of the wiring concern, not a separate
  capability.

### Decisions Made

- The four clarification answers below. `null` is refused and a `None` document is an empty
  overlay (Assumptions).

## Clarifications

### Session 2026-11-11
- Q: How should an operator point Forage at an overlay? → A: an env var naming a file,
  `FORAGE_CONFIG_OVERLAY=/path`. Unset means today's behaviour; set but missing or unreadable
  refuses boot.
- Q: When the overlay sets a list, what happens to the shipped list? → A: replace wholesale.
  Mapping blocks merge per key; a list is exactly what is served.
- Q: What if the overlay has an unknown or misspelled key? → A: refuse boot, naming the key and
  never the value. The base file keeps its warn-only rule.
- Q: Can an overlay loosen security-relevant keys? → A: allowed, with one boot WARNING naming every
  key the overlay set (names only, never values).
