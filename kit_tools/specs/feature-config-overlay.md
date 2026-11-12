<!-- Template Version: 2.5.0 -->
---
feature: config-overlay
status: active
session_ready: true
depends_on: []
vision_ref: "T2.2 — Forage hardening (operator configuration, single source)"
type: epic-child
size: L
epic: forage-config-overlay
epic_seq: 1
epic_final: true
created: 2026-11-11
updated: 2026-11-11
---

# Feature Spec: Config Overlay — `FORAGE_CONFIG_OVERLAY` Merged Over the Base Config

> **Execution precondition:** PR #52 (`feat/extract-route-env`: `FORAGE_EXTRACT_ROUTE_ENABLED`,
> contract 1.6.0) must be merged to `main` before this spec executes. The line numbers below and
> US-002's precedence criterion refer to that tree. `depends_on` is empty only because #52 is a
> PR, not a kit_tools feature spec. Check that `retrieval_app.py` contains `resolve_extract_route`
> before starting; if it does not, stop.

## Overview

`retrieval_app._load_config()` (`retrieval_app.py:514`) reads `/app/config.yaml` and returns it
whole. The only way to change one key is to bind-mount a complete replacement file.
`docs/configuration.md` § "Replace, never merge" warns that a short file silently resets every
omitted key to its code default, security bounds included. So every upgrade is a manual re-sync
of about 90 tuned lines, and a consumer (Poppy) vendors the whole file under a sha256 pin and a
drift test.

This spec adds **`FORAGE_CONFIG_OVERLAY`**, the path of a YAML file merged over the base config
(the baked `/app/config.yaml`, or a full replacement mounted there). The merge rules are short:

- **Blocks merge.** `cache:`, `extraction:` and `retrieve:` merge leaf by leaf.
- **Lists replace.** A list in the overlay replaces the base list outright.
- **Other values replace.** Every other value replaces the base value outright.

The overlay is validated strictly, and any problem refuses the boot with one of six closed tokens:
`overlay_missing`, `overlay_unreadable`, `overlay_not_mapping`, `overlay_unknown_key`,
`overlay_null_value`, `overlay_shape_mismatch`. A refusal never shows the overlay path, any
overlay value or any operator-written text, not even in a chained traceback.

**One WARNING per boot**, `config_overlay_applied`, names the dotted keys the overlay set.

**Precedence** for `extract_route_enabled`, the one key that also has its own variable:
`FORAGE_EXTRACT_ROUTE_ENABLED` (PR #52), then the overlay, then the base file, then the code default.

**Seam:** `_load_config()` stays the base-file loader, unchanged. The lifespan applies the overlay
**immediately after** it. About 45 existing tests monkeypatch `_load_config` to supply a base
mapping; they keep working, and overlay tests use the same seam to supply a hand-built base.

**No contract bump, no rotation:** no response shape changes and no `_REVISION_SOURCES` file moves.
The revision keeps hashing the *configured* threshold and contiguity values, which now come from
the merged mapping, exactly as they would from an equivalent full file.

## Goals

- An operator changes any single known key by supplying a file that contains only that key. The
  effective configuration equals the base with exactly that key changed. Pinned by a test that
  compares the merged mapping key-for-key against the baked `config.yaml`.
- Zero silent outcomes from the overlay itself: every overlay defect refuses the boot. Each of the
  six refusal classes is distinguishable from its token alone:
  - `overlay_missing` — no such file, or not a regular file;
  - `overlay_unreadable` — I/O or permission error, over 1 MiB, not UTF-8, invalid YAML, a
    duplicate key or a YAML alias;
  - `overlay_not_mapping`;
  - `overlay_unknown_key`;
  - `overlay_null_value`;
  - `overlay_shape_mismatch`.
- The lines this feature adds never contain the overlay path, an overlay value or unvalidated
  operator text. That covers the `config_overlay_applied` WARNING, every `ConfigOverlayError`
  message, and the full formatted traceback of a refused boot. Existing boot lines that already
  print effective values are out of this goal and unchanged: `promptguard_threshold_resolved
  value=`, `extract_route_resolved enabled=`, and `config_invalid_value entries=`.
- Deployments without `FORAGE_CONFIG_OVERLAY` behave byte-identically. The effective config, both
  revision readers and the full suite are unchanged, apart from the new tests.

## User Stories

### US-001: Overlay reading, validation and merge (pure module)

**Priority:** P1

**Description:** As an operator, I want my overlay file read safely and merged over the base by
fixed, documented rules, so that I state only the keys I change, keep every default I didn't
touch, and get a precise refusal for any mistake.

**Independent Test:** Unit tests over `read_overlay_file` (with `tmp_path`) and
`merge_config_overlay` (with hand-built mappings and the baked `config.yaml`). This delivers the
complete read, validation and merge contract with no lifespan or app involved.

**Implementation Hints:**
- New module `pipeline/config_overlay.py`, beside `pipeline/config_bounds.py`, the precedent for
  small config helpers. It is **not** a `_REVISION_SOURCES` member
  (`pipeline/sanitizer_revision.py:16`); do not add it. Invariant 3: flat layout, existing
  package, so a new file is fine.
- **This story owns all six tokens.** Define `ConfigOverlayToken = Literal[...]` (the six) and
  `class ConfigOverlayError(ValueError)` with a `token: ConfigOverlayToken` attribute. Its message
  is built only from the token, a fixed phrase and at most one **validated** key name.
  `ExtractionConfigurationError` (`pipeline/extraction_limits.py`) is a bare `ValueError`; this
  class is deliberately richer, because the operator needs the token.
- **`read_overlay_file(path: str) -> dict[str, Any]`.** Each step's failure maps to a token:
  1. `os.stat(path)`: `FileNotFoundError` or `NotADirectoryError` → `overlay_missing`;
     `not stat.S_ISREG` → `overlay_missing`; any other `OSError` (including `PermissionError`) →
     `overlay_unreadable`.
  2. Open in **binary** mode and `read(MAX_OVERLAY_BYTES + 1)`. More than
     `MAX_OVERLAY_BYTES = 1_048_576` bytes → `overlay_unreadable` (refused, never truncated). One
     open and one bounded read: no check-then-open size test.
  3. `bytes.decode("utf-8")`. `UnicodeDecodeError` → `overlay_unreadable`.
  4. Parse the **decoded string** (not the file object, so no YAML mark carries the path) with a
     `yaml.SafeLoader` subclass that refuses three things:
     - **any alias**: override `compose_node` to raise on an `AliasEvent`. A round-2 prototype
       caught aliases as mapping values and keys, in flow sequences and maps, at top level and in
       `<<: *x`;
     - **duplicate mapping keys**: check the composed `node.value` keys, so `1`, `true` and `1.0`
       collide;
     - **merge keys** (`<<`, tag `tag:yaml.org,2002:merge`), explicitly, whether or not an alias
       follows. Do **not** override `construct_mapping` to build keys yourself: the round-2
       prototype showed that breaks `<<` with a misleading "no constructor" error, refusing it
       only by accident.

     `yaml.YAMLError` or `RecursionError` → `overlay_unreadable`. `safe_load` raises a plain
     `RecursionError` at nesting depth ≥ 1000 (prototyped). Refusing aliases keeps
     self-referential and shared structures out of the typed readers. Memory is not the reason:
     PyYAML aliases share one object rather than copying it.
  - **Pyright strict and yaml:** PyYAML ships no `py.typed`. A `SafeLoader` subclass calling
    `self.check_event(...)` and similar reports `reportUnknownMemberType` (prototyped), and
    suppressions are switched off. In order of preference:
    1. add `types-PyYAML` to the `dev` extra (`uv add --optional dev types-PyYAML`), then
       confirm `uv run pyright` stays at zero repo-wide, because the stubs also type the existing
       `yaml.safe_load` callers;
    2. if that surfaces unrelated errors, add a minimal `typings/yaml/` stub declaring only the
       symbols the repo calls, per `typings/README.md`, and record it in that README's table.
  5. Document `is None` (an empty or comments-only file) → `{}`. Any other non-`dict` → 
     `overlay_not_mapping`, including `[]`, `0`, `False` and `''`. Use `is None`, **never**
     `or {}`, which turns falsy documents into empty overlays.
- **Every `ConfigOverlayError` is raised `from None`.** Without it, the lifespan traceback uvicorn
  prints includes the chained `OSError` or `YAMLError` text, which contains the path and a YAML
  snippet. This was prototyped: `yaml.safe_load(open(p))` puts `in "<path>", line 1` in the
  mark. A `caplog` assertion cannot see a traceback, so the test must format it.
- **`merge_config_overlay(base, overlay, known_keys) -> tuple[dict[str, Any], tuple[str, ...]]`.**
  `known_keys` is passed by the caller **at call time** (US-002 passes the live module global
  `retrieval_app.KNOWN_CONFIG_KEYS`; tests patch that global, e.g. `tests/test_app.py:2514`).
  Derive inside the function, as `_warn_unknown_config_keys` does (`retrieval_app.py:494-500`):
  - `top_level` = known keys without a dot;
  - `blocks` = the prefixes of dotted keys;
  - `leaves` = dotted keys.
- **Validation order is deterministic.** Walk the overlay's keys in **sorted** order. For each key,
  check name, then null, then shape; the first failure raises. This defines the precedence when
  several defects coexist. Non-string keys can't be sorted alongside strings (`sorted()` raises
  `TypeError`, prototyped), so check every key is a `str` *before* sorting, **at both levels**:
  the top-level keys and each block's leaf keys (`cache: {1: x}`). Any non-string key →
  `overlay_unknown_key`.
- **Name rules:**
  - A top-level key must be in `top_level`. **A top-level key containing a dot is refused**
    (`cache.max_entries: 1` → `overlay_unknown_key`), because `KNOWN_CONFIG_KEYS` holds dotted
    entries and a naive `key in known_keys` would accept it.
  - Under a block, each leaf (a `str`) must form a dotted key in `leaves`.
- **Echo rule for unknown keys:** include the key in the message only if
  `re.fullmatch(r"[a-z][a-z0-9_]{0,63}(\.[a-z][a-z0-9_]{0,63})?", key)` succeeds; otherwise
  write `<unprintable key>`. Use **`fullmatch`, never `match` with `^…$`**: Python's `$` also
  matches just before a trailing newline, so `'foo\n'` would be echoed raw. This was prototyped in
  round 2, and it is the very log-forging input the rule exists to stop. An
  unknown key is operator text: it may contain newlines (log forging) or a mis-pasted secret.
  Known keys (in `set_keys` and in null or shape messages) come from the closed set, so they are
  safe.
- **Null rule:** `None` as any value — top-level, block leaf, or any element of a list — →
  `overlay_null_value`.
- **Shape rules (block-ness comes from `known_keys`, not from the base):**
  - a key in `blocks` must map to a `dict`, else `overlay_shape_mismatch`;
  - a key not in `blocks` must not map to a `dict`;
  - where the **base** value under that key is a `list`, the overlay value must be a `list`;
  - where the base value is a plain scalar (`str`, `int`, `float`, `bool`), the overlay value
    must not be a list. If the base value is **`None` or any other type**, skip both list rules
    and let the typed readers decide. A full replacement left with `seed_blocklist:` (YAML null)
    must not make a correct overlay list refuse the boot, because the overlay exists to repair
    exactly that file (round-2 second opinion).

  So a plain string for `user_agents` is refused: today the user-agent pick would silently choose
  one character. With the base key absent, only the block rule applies, and the typed readers
  handle the rest.
- **Merge:** build a new dict and never mutate `base` or `overlay` (copy each block you merge
  into, because `retrieval_app` later derives `published_config` from the result). `set_keys` is
  the sorted tuple of dotted keys actually set: `cache: {}` sets nothing.
- **No value types or ranges here.** The typed readers own that, as today. Note that some
  readers **fall back with a WARNING** rather than refusing: `promptguard_threshold`,
  `policy_domain_entries_max_bytes`, the domain-list entries. `user_agents` is unchecked beyond
  the list-shape rule above. That is existing behaviour for a full file too; US-002 pins it and
  US-003 documents it. Do not add a second value-validation path here.

**Acceptance Criteria:**
- [x] `pipeline/config_overlay.py` defines `ConfigOverlayToken` (exactly the six tokens),
      `ConfigOverlayError(ValueError)` with `.token`, `MAX_OVERLAY_BYTES = 1_048_576`,
      `read_overlay_file` and `merge_config_overlay`. Nothing in the module imports
      `retrieval_app`.
- [x] `read_overlay_file` maps each failure to its token:
  - missing path, directory or dangling symlink → `overlay_missing`;
  - permission denied (on stat or open), a 1 MiB + 1 byte file, non-UTF-8 bytes, invalid YAML,
    a duplicate key, a YAML alias, a `<<` merge key (with and without an alias), an unsafe tag
    (`!!python/object:os.system`), or a nesting depth of 5000 → `overlay_unreadable`;
  - a document that is `[]`, `0`, `false` or `''` → `overlay_not_mapping`.

  An empty or comments-only file returns `{}`.
- [x] For every refusal class in both functions, the **full formatted traceback**
      (`"".join(traceback.format_exception(exc))`) contains neither a sentinel path nor a
      sentinel value, and `exc.__cause__ is None` and `exc.__suppress_context__ is True`.
      Do not assert `__context__ is None`; Python keeps it set under `from None`. The
      invalid-YAML case puts the sentinel value **inside** the malformed text (e.g. an
      unterminated `'SENTINELVAL`), because the raw `YAMLError` message quotes operator content.
- [x] Block keys merge leaf by leaf: `{"extraction": {"classification_concurrency": 2}}` over the
      baked file yields the baked `extraction` block with only that leaf changed; `cache: {}`
      changes nothing and sets no key
- [x] Non-block values replace outright, lists included: `seed_blocklist: [".example"]` yields
      exactly that list, and `news_domains: []` yields `[]`
- [x] An unknown top-level key, an unknown leaf under a known block, a **dotted top-level key**
      (`cache.max_entries`, `extraction.max_pages`), a non-string top-level key and a non-string
      block leaf key (`cache: {1: x}`) each raise `overlay_unknown_key`, never a raw `TypeError`
- [x] An unknown key matching the safe pattern appears in the message. Each of these appears only
      as `<unprintable key>`:
  - a **trailing** newline (`'foo\n'`);
  - an embedded newline (`'foo\n.bar'`);
  - uppercase;
  - punctuation;
  - more than 64 characters in a segment.
- [x] `None` at top level, as a block leaf, or as a list element raises `overlay_null_value`
- [x] `extraction: 5`, `promptguard_threshold: {a: 1}`, `user_agents: "x"` (base holds a list)
      and `promptguard_threshold: [1]` (base holds a scalar) each raise `overlay_shape_mismatch`.
      Over an empty base, `extraction: 5` still raises (block-ness from `known_keys`).
- [x] With several defects present, the first failing key in sorted order decides the token (one
      test with an unknown key sorting before a null)
- [x] `base` and `overlay` are unmodified after the call (deep equality with copies taken
      before); `set_keys` is exactly the sorted dotted keys set
- [x] Tests written/updated for new functionality (new `tests/test_config_overlay.py`, added to
      `kit_tools/testing/TESTING_GUIDE.md`'s `test_mapping` under `pipeline/config_overlay.py`)
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass with zero
      findings

### US-002: Apply the overlay at boot and log the keys

**Priority:** P1

**Description:** As an operator, I want `FORAGE_CONFIG_OVERLAY=/path/overlay.yaml` applied at
container start, so that my changes take effect over the shipped defaults. A broken overlay should
refuse the boot rather than half-apply, and one log line should tell me which keys changed.

**Independent Test:** Lifespan tests with `monkeypatch.setattr(retrieval_app, "_load_config",
lambda: base)` and a `tmp_path` overlay file. They show that merged values reach `app.state`,
that each refusal stops the boot, and that exactly one WARNING names the keys. Delivers the
feature end to end without docs.

**Implementation Hints:**
- Leave `_load_config()` (`retrieval_app.py:514`) as the base loader. Add
  `_apply_config_overlay(base: dict[str, Any]) -> dict[str, Any]` in `retrieval_app.py`. It:
  - reads `CONFIG_OVERLAY_ENV_VAR = "FORAGE_CONFIG_OVERLAY"` once, stripped;
  - returns `base` unchanged when the value is unset or blank;
  - otherwise calls `read_overlay_file` and then `merge_config_overlay(base, overlay,
    KNOWN_CONFIG_KEYS)`, reading the live module global at call time;
  - logs the WARNING and returns the merged mapping.
- In the lifespan, change `config = _load_config()` (`retrieval_app.py:1808`) to
  `config = _apply_config_overlay(_load_config())`. Everything after that line already reads only
  `config`, so every downstream consumer sees the merged mapping: `_warn_unknown_config_keys`,
  `published_config`, the `*_from_config` readers, `resolve_extract_route` and
  `derive_sanitizer_revision`.
- Success line, WARNING, once per boot, only when an overlay applied:
  `config_overlay_applied — keys=<comma-joined set_keys> count=<n>`. An empty overlay logs
  `keys= count=0`. Effective-value logging stays declined (`kit_tools/arch/DECISIONS.md`,
  resource-envelope Consequences).
- Let `ConfigOverlayError` propagate out of the lifespan, so the boot refuses. Test it with
  `async with lifespan(FastAPI())` under `pytest.raises`, like
  `test_lifespan_refuses_invalid_classification_concurrency` in `tests/test_app.py`. Assert on
  `traceback.format_exception` of the caught exception, not only on `caplog`.
- Boot stores `app.state.sanitizer_revision = derive_sanitizer_revision(config)`, and `/health`
  returns that stored value. The request paths re-derive the revision from `app.state.config`
  (`retrieval_app.py:2214`, `:2694` on the PR #52 tree). Check that the boot value and a
  re-derivation from `app.state.config` both equal the revision of the equivalent full mapping.
- Existing readers that **fall back** rather than refuse keep doing so for overlay values. Pin one
  such case. For example, overlay `promptguard_threshold: 1.5` boots, logs `config_invalid_value
  — key=promptguard_threshold`, and serves 0.85; the revision then hashes the raw configured 1.5,
  identical to an equivalent full file. The test documents that the overlay does not change this
  behaviour; it does not endorse it.
- `FORAGE_EXTRACT_ROUTE_ENABLED` already wins, because `resolve_extract_route(config, os.environ)`
  runs after the merge. Only a test is needed. `source` stays `config` for an overlay-supplied
  value: the source is env, config or default, not file-level provenance.
- Hermeticity: add `FORAGE_CONFIG_OVERLAY` to `tests/conftest.py::_CLEARED_ENV_VARS` and to the
  exact-set pin in `tests/test_hermeticity.py`.
- No `/health`, `/metrics` or response change, so no contract bump. `retrieval_app.py` and
  `pipeline/config_overlay.py` are unhashed, so no rotation. With the overlay unset, confirm the
  default-config revision equals the current `HEAD` value.

**Acceptance Criteria:**
- [ ] With `FORAGE_CONFIG_OVERLAY` unset or blank, `_apply_config_overlay` returns its input
      object unchanged. A lifespan over the real `_load_config` yields `app.state.config` and both
      revision readers identical to the baked file's.
- [ ] Over a monkeypatched base equal to the baked file, an overlay setting
      `extraction.classification_concurrency: 2` and `promptguard_threshold: 0.9` gives:
  - `app.state.extraction_settings.classification_concurrency == 2`;
  - `app.state.promptguard_threshold_default == 0.9`;
  - every other extraction value equal to the baked file's;
  - both revision readers equal to the revision of the equivalent full mapping.
- [ ] Each of the six tokens refuses the boot through the lifespan: one parametrized test, with
      the file-level cases built on disk. The formatted traceback contains neither the sentinel
      path nor a sentinel value, and no `caplog` record does either.
- [ ] An applied overlay logs exactly one WARNING `config_overlay_applied — keys=<sorted dotted
      keys> count=<n>`. A comments-only overlay boots and logs `keys= count=0`. With no overlay,
      no such line is logged.
- [ ] With a monkeypatched **empty** base (the base-file-missing path), an overlay containing
      `extraction: {classification_concurrency: 2}` boots with
      `app.state.extraction_settings.classification_concurrency == 2`, every other extraction
      value at its code default, and the WARNING `config_overlay_applied —
      keys=extraction.classification_concurrency count=1`
- [ ] An overlay value that a typed reader refuses (`extraction.classification_concurrency: 9`)
      refuses the boot through that reader's error type (`ExtractionConfigurationError`), not
      `ConfigOverlayError`. An overlay `promptguard_threshold: 1.5` boots, logs
      `config_invalid_value`, serves `0.85`, and has the same revision as an equivalent full
      mapping.
- [ ] `FORAGE_EXTRACT_ROUTE_ENABLED=0` beats an overlay `extract_route_enabled: true`: the route
      is closed and the log says `source=env`. With the variable unset, the overlay's `true` opens
      the route with `source=config`.
- [ ] `FORAGE_CONFIG_OVERLAY` is in `tests/conftest.py::_CLEARED_ENV_VARS` and the
      `tests/test_hermeticity.py` exact-set pin
- [ ] Tests written/updated for new functionality (lifespan cases in `tests/test_app.py`, beside
      the `FORAGE_EXTRACT_ROUTE_ENABLED` gate tests)
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass with zero
      findings

### US-003: Operator docs, compose recipe and the decision record

**Priority:** P2

**Description:** As an operator, and as the Poppy maintainer, I want the docs to make the overlay
the recommended way to change configuration, so that nobody copies the whole file by default
again and Poppy can drop its vendored copy.

**Independent Test:** A reader using only `docs/configuration.md` can write a one-key overlay,
mount it read-only with a compose fragment's commented recipe, and confirm it from the
`config_overlay_applied` line. `tests/test_contract_metrics.py`,
`tests/test_governance_docs.py` and `tests/test_compose_fragments.py` stay green.

**Implementation Hints:**
- **Guarded sections: keep them, extend them, don't restructure them.**
  `tests/test_contract_metrics.py::test_unknown_config_key_warning_is_documented` asserts phrases
  in four places:
  - `docs/configuration.md`'s `## \`config.yaml\`` section must keep "Unknown keys are ignored",
    `config_unknown_key`, `config_invalid_value`, `ExtractionConfigurationError`,
    `CacheConfigurationError`, `promptguard_threshold`, `policy_domain_entries_max_bytes`,
    `seed_blocklist` and `news_domains`;
  - `kit_tools/docs/MONITORING.md` `### Startup lines you may see` needs a WARNING row for
    `config_unknown_key`;
  - `kit_tools/arch/patterns/LOGGING.md` `## Logger Inventory`;
  - `kit_tools/docs/TROUBLESHOOTING.md` `### config_unknown_key`, which must mention "restart".

  "Unknown keys are ignored" stays true for the **base file**. Add the overlay's stricter rule
  beside it; don't replace it.
- **`docs/configuration.md`:**
  - add a `FORAGE_CONFIG_OVERLAY` row to the Runtime env table;
  - rewrite § "Delivering `config.yaml` through the fragments" to lead with a one-key overlay
    mounted **read-only** (`:ro`);
  - reframe "**Replace, never merge.**" as "a full replacement replaces; an overlay merges",
    keeping the silent-reset warning for full mounts;
  - state the merge, null, shape and dotted-key rules, the precedence chain, the 1 MiB bound and
    the six tokens;
  - state that readers which fall back with `config_invalid_value` (threshold, domain-list
    budget, list entries) do so for overlay values too, so the operator should check the boot
    log after adding one.
- **Where the other docs need changing:**
  - `kit_tools/docs/ENV_REFERENCE.md`: the variable row, and the conftest cleared-list sentence.
  - `LOGGING.md` (Logger Inventory) and `MONITORING.md` (Startup lines table): the
    `config_overlay_applied` WARNING row.
  - `TROUBLESHOOTING.md`: one `### overlay_<token>` section per token, each with its fix.
  - `kit_tools/arch/patterns/ERROR_HANDLING.md`: add `ConfigOverlayError` to the boot-time
    refusers (lines 59 and 139 on the PR #52 tree).
- **`kit_tools/arch/DECISIONS.md`:** a new entry, "Config overlay: blocks merge, lists replace,
  strict names". Record the four clarifications, the validation-round decisions (aliases and
  duplicates refused, `from None`, unknown keys echoed only when safe) and why loosening is
  allowed but logged. Repoint the resource-envelope entry's "replaces rather than merges"
  consequence to it.
- **Compose:** add a **commented** recipe (a `volumes:` line with `:ro` plus an `environment:`
  line) to `compose/minimal.yml` and `compose/full.yml`. Do not add a live bare passthrough: a
  path without its mount refuses the boot. `compose/gpu.yml` is itself an overlay merged onto
  those two fragments, so it gets **no** recipe. Say so in one comment line there, or in the
  docs.
- **Other docs:**
  - `kit_tools/docs/LOCAL_DEV.md`: a `docker run -v "$PWD/overlay.yaml:/app/overlay.yaml:ro"
    -e FORAGE_CONFIG_OVERLAY=/app/overlay.yaml` example.
  - `kit_tools/docs/DEPLOYMENT.md`: the variable is read once at start.
  - `kit_tools/arch/CODE_ARCH.md`: a row for the new module.
- **`docs/releases.md`:** one bullet under Unreleased.
- **`docs/bootstrap-notes.md`:** a Poppy consumer note covering:
  - replace the vendored `config/forage/config.yaml` with an overlay of Poppy's deltas only;
  - drop the sha256 pin and the drift test, but **keep a test pinning Poppy's expected overlay
    keys**, so an accidental deletion is caught;
  - mount it `:ro`;
  - every upgrade now inherits Forage's tuned defaults.
- **`kit_tools/docs/GOTCHAS.md`:**
  - "Lists in an overlay replace, they never append" — `news_domains` and `user_agents` are
    non-empty in the shipped file;
  - "An overlay value a reader falls back on is still ignored behind a WARNING" — the threshold
    case.

**Acceptance Criteria:**
- [ ] `docs/configuration.md` documents `FORAGE_CONFIG_OVERLAY`: the merge, null, shape and
      dotted-key rules, the precedence chain, the 1 MiB bound, all six tokens, read-only mounting
      and the reader-fallback caveat. It presents the overlay before the full-replacement mount.
      Every phrase `test_unknown_config_key_warning_is_documented` requires is still present.
- [ ] `ENV_REFERENCE.md`, `LOGGING.md` (Logger Inventory), `MONITORING.md` (Startup lines table),
      `TROUBLESHOOTING.md` (a section per token) and `ERROR_HANDLING.md` (boot-time refusers)
      cover the variable, the WARNING, the tokens and `ConfigOverlayError` respectively
- [ ] `kit_tools/arch/DECISIONS.md` has the new overlay entry, and the resource-envelope
      consequence points to it
- [ ] `compose/minimal.yml` and `compose/full.yml` each carry a commented `:ro` overlay recipe.
      `tests/test_compose_fragments.py` passes and pins that `FORAGE_CONFIG_OVERLAY` is **not**
      a live environment entry on any service.
- [ ] `docs/bootstrap-notes.md` carries the Poppy consumer note, including the keep-a-key-pin
      advice. `docs/releases.md` Unreleased carries one bullet.
- [ ] `kit_tools/docs/GOTCHAS.md` carries both entries, `CODE_ARCH.md` the module row, and
      `LOCAL_DEV.md` and `DEPLOYMENT.md` the run example and the read-once note
- [ ] Full test suite passes (`uv run pytest`)

## Edge Cases

- Empty or comments-only overlay file: `{}`, a no-op that logs `count=0`. (US-001 read; US-002 log)
- Falsy non-mapping documents (`[]`, `0`, `false`, `''`): `overlay_not_mapping`, never an empty
  overlay. (US-001)
- `cache: {}`: merges nothing and sets no key. (US-001)
- An overlay key the base omits (a known key a full replacement dropped): it is added and the
  readers validate it; list and scalar shape checks need a base value, so only the block rule
  applies. (US-001)
- An overlay key set to the base's own value: still in `set_keys`; the log names intent, not
  difference. (US-001)
- The base `/app/config.yaml` is itself a full replacement: the overlay merges over it. (US-002,
  documented in US-003)
- The base file is missing (`config.yaml not found` WARNING, base `{}`): the overlay merges over
  `{}`. (US-002 criterion)
- `FORAGE_CONFIG_OVERLAY` set but blank or whitespace: no overlay. (US-002)
- The path is a directory or a dangling symlink: `overlay_missing`. A permission-denied parent
  or file: `overlay_unreadable`. (US-001)
- A file over 1 MiB, non-UTF-8 bytes, a duplicate key, a YAML alias, a `<<` merge key, an unsafe
  tag or nesting depth ≥ 1000: `overlay_unreadable`. (US-001)
- A dotted top-level key or a non-string key: `overlay_unknown_key`. (US-001)
- An unknown key containing a newline or other unsafe text: refused, and echoed as
  `<unprintable key>`. (US-001)
- `null` inside a list (`seed_blocklist: [~]`): `overlay_null_value`. (US-001)
- Several defects at once: the first key in sorted order decides; per key the order is name, then
  null, then shape. (US-001)
- An overlay value a reader falls back on (threshold 1.5): boots with `config_invalid_value` and
  serves the default. This is existing behaviour, pinned and documented, not changed. (US-002,
  US-003)

## Out of Scope

- **Per-key loosening rules.** Rejected in clarification; every overlaid key is named in the
  WARNING instead.
- **Turning existing reader fallbacks into refusals** (e.g. a threshold of 1.5 refusing boot). That
  would change full-file behaviour too; it needs its own decision.
- **Environment variables for other individual keys.** `FORAGE_EXTRACT_ROUTE_ENABLED` remains the
  only one.
- **Multiple overlays, `config.d/` directories, or inline YAML in an env var.**
- **Overlay state on `/health` or `/metrics`.** That would be a contract change.
- **List append syntax. Hot reload. Changing baked values. Removing full-replacement mounts.**
- **Poppy's adoption.** It executes in Poppy, from the consumer note.

## Assumptions

- PR #52 is merged first (see the execution precondition at the top).
- Exactly three blocks (`cache`, `extraction`, `retrieve`), nested one level, as `KNOWN_CONFIG_KEYS`
  defines today. A future block follows the same "block.leaf" convention and needs no code
  change here.
- `null` is refused rather than read as "reset to default". It is ambiguous, and stating the
  default value explicitly achieves a reset.
- Known key names are closed-set schema words and safe to log. Unknown keys are not, so they are
  echoed only when they match the safe pattern.
- 1 MiB is ample: the full shipped file is about 6 KB.
- Refusing YAML aliases costs nothing real: an overlay is a few keys, and anchors and aliases add
  no expressiveness the operator needs.
- `bench/config.yaml` and the corpus tooling load their own files and are unaffected.

## Technical Considerations

- **No contract bump, no rotation.** Covered in the Overview; pinned by US-002's unset-overlay
  criterion.
- **Single value-validation path.** Name, null and shape checks live in the overlay; value types
  and ranges stay in the existing readers, including their existing fall-back-with-WARNING
  behaviour.
- **Strict overlay, lenient base.** The base file keeps warn-only unknown keys, because a full
  replacement may lag a release. The overlay is strict, because it exists only to change named
  keys.
- **Invariant 6 / closed log vocabulary:** tokens and validated key names only, and `from None`
  on every refusal so no chained exception leaks the path or contents.
- **Invariant 5:** every overlay defect refuses the boot. Reader fallbacks remain loud through
  `config_invalid_value`.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Decisions: [DECISIONS.md](../arch/DECISIONS.md) (resource envelope, Consequences)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)
- Logging: [LOGGING.md](../arch/patterns/LOGGING.md) · Errors: [ERROR_HANDLING.md](../arch/patterns/ERROR_HANDLING.md)
- Canonical config reference: [`docs/configuration.md`](../../docs/configuration.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Apply the overlay in the lifespan right after `_load_config()`, not inside it.
**Rationale:** About 45 tests monkeypatch `_load_config` to supply a base mapping
(`tests/test_app.py:470, 2158, 2169, 2186` and others). Merging inside it would bypass the overlay
in exactly the tests that need it. Applying it after keeps one seam for the base and one for the
overlay, and everything downstream still reads the single `config` local.
**Alternatives considered:** Merging inside `_load_config` (rejected in validation round 1:
untestable over a hand-built base); merging in each reader (duplicated).
**Source:** `retrieval_app.py:514`, `:1808`; codebase-fit and second-opinion findings, round 1.

**Decision:** Read bytes, decode, parse a string with a strict loader, and raise `from None`.
**Rationale:** Prototyped in round 1 by the salty-engineer reviewer. `yaml.safe_load(open(p))`
embeds the path in the parser mark. A chained `OSError` or `YAMLError` reaches uvicorn's traceback
even when the outer message is clean. Text-mode reads raise `UnicodeDecodeError`, outside
`OSError` and `YAMLError`. `safe_load` silently keeps the last duplicate key, and expands aliases
past any byte cap.
**Source:** salty-engineer critical 1, security warning 1, second-opinion info, round 1.

**Decision:** Refuse dotted top-level keys; derive top-level, block and leaf sets separately.
**Rationale:** `KNOWN_CONFIG_KEYS` (`retrieval_app.py:447`) is flat and contains dotted entries, so
a naive membership test accepts `cache.max_entries: 1` at the top level. That would be a silent
no-op logged as applied.
**Source:** salty-engineer critical 2, round 1.

**Decision:** `pipeline/config_overlay.py` takes `known_keys` at call time.
**Rationale:** A `pipeline/` module must not import `retrieval_app`. Tests patch the module global
(`tests/test_app.py:2514`), so the caller passes the live value.
**Source:** `retrieval_app.py:445-500`, `pipeline/config_bounds.py`; codebase-fit warning, round 1.

**Decision:** Reader fallbacks are documented and pinned, not changed.
**Rationale:** `promptguard_threshold`, `policy_domain_entries_max_bytes` and domain-list entries
fall back with `config_invalid_value` rather than refusing, so an overlay value can be ignored
behind a WARNING. Changing that would alter full-file behaviour and add a second validation path.
**Source:** second-opinion warning 2, round 1; `retrieval_app.py` lifespan (threshold and domain
lists).

**Decision:** No live compose passthrough; commented `:ro` recipe only; none in `gpu.yml`.
**Rationale:** The variable names a container path, so a passthrough without its mount refuses the
boot. `gpu.yml` is itself an overlay onto the two fragments.
**Source:** `compose/minimal.yml`, `compose/gpu.yml` header, `tests/test_compose_fragments.py`.

### Scope Adjustments

- Round 1: file reading moved from US-002 into US-001, so one story owns all six tokens and the
  whole read-and-validate contract. US-002 is now wiring only.
- Round 1: the log-hygiene goal was narrowed to the lines this feature adds. Existing value-bearing
  boot lines are explicitly out of that goal.

### Decisions Made

- The four clarification answers below.
- `null` is refused; a `None` document is an empty overlay.
- Validation-round additions:
  - aliases and duplicate keys are refused as `overlay_unreadable`;
  - an unknown key is echoed only when it matches the safe pattern;
  - the list and scalar shape rule is checked against the base value;
  - block-ness comes from `known_keys`;
  - the check order is sorted keys, then name, null, shape per key.

## Clarifications

### Session 2026-11-11
- Q: How should an operator point Forage at an overlay? → A: an env var naming a file,
  `FORAGE_CONFIG_OVERLAY=/path`. Unset means today's behaviour; set but missing or unreadable
  refuses the boot.
- Q: When the overlay sets a list, what happens to the shipped list? → A: replace it wholesale.
  Blocks merge per key; a list is exactly what is served.
- Q: What if the overlay has an unknown or misspelled key? → A: refuse the boot, never echoing the
  value. The base file keeps warn-only.
- Q: Can an overlay loosen security-relevant keys? → A: allowed, with one boot WARNING naming every
  key set (names only).

### Validation round 1 (2026-11-11)
- 2 critical and 21 warnings across six reviewers (worst readiness 6, salty engineer). All were
  folded in above: the chained-traceback path leak, the dotted top-level key, the placement of the
  seam, the falsy documents, duplicate keys and aliases, the narrowed log goal, reader fallbacks,
  the token ownership, the precedence order, the guarded doc sections, `ERROR_HANDLING.md` and
  `gpu.yml`, and the explicit PR #52 precondition.

### Validation round 2 (2026-11-11)
- Five reviewers re-ran (completionist carried forward at ready 8). Scores: story quality 7,
  salty 7, codebase fit 9, security 8, second opinion 8. Every round-1 finding was confirmed
  resolved.
- The one critical, from the salty engineer and confirmed by the security reviewer: the echo regex
  used `^…$`, which accepts `'foo\n'`. It is now `re.fullmatch`, and the planner checked directly
  that `fullmatch` rejects `'foo\n'` while `match` with `^…$` accepts it.
- Also folded in:
  - the pyright typing route for the `SafeLoader` subclass;
  - explicit refusal of `<<` merge keys (no `construct_mapping` override);
  - a sentinel value inside the malformed YAML;
  - a nesting test at depth 5000;
  - non-string block-leaf keys;
  - a concrete outcome for the empty-base criterion;
  - an unsafe-tag criterion;
  - the list rule skipped when the base value is `None` or a non-scalar;
  - the corrected alias rationale and revision-reader hint;
  - the `ERROR_HANDLING.md` line numbers and the US-002 test file.
