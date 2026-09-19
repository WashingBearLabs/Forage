<!-- Template Version: 2.5.0 -->
---
feature: hardening-hostname-and-config
status: active
session_ready: true
depends_on: [hardening-retrieve-parity, hardening-search-sanitization]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 3
epic_final: false
execution_order: [US-001, US-002, US-005, US-003, US-004, US-006]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Hostname Semantics + Config Single-Sourcing

> **Spec 3 of `epic-forage-hardening`.** Make every hostname-against-a-list comparison in the
> service mean one written-down thing — suffix matching for denylists, opt-in suffix matching for
> allowlists (ruling R8, revised in validation round 1) — give `/search` the domain-policy and
> threshold knobs `/retrieve` already has (rulings 9, R10), and put the `config.yaml` surface
> behind one key registry so an operator typo is a WARNING and every key has a documented row
> (ruling 12). Ruling 11 records that the SearXNG engine list is *already* single-sourced by test.
> Context: WA-E punch list ("Exact-hostname matching breaks blocklists, trust tiers, and news
> TTLs", "`blocked_domains` applies to fetch but never search", "`promptguard_threshold` is dead
> for 2 of 3 endpoints", "Nine distinct config surfaces"), audit finding 2026-09-16-058, and the
> epic's rulings 5, 6, 9, 11, 12, R8, R10, R31, R32 are binding here.

## Overview

Three exact-match sites decide security-relevant things today, and all three read
`host in {lower-cased entries}`: `validate_url`'s `blocked_domains` check
(`url_validator.py:145-146`, `lower_host in lower_blocked`), `_resolve_request_trust_tier`
(`pipeline/orchestrator.py:1114`) and `_effective_ttl_hours`'s `news_domains` check
(`cache.py:167`). Blocking `evil.com` therefore does not block `www.evil.com`, and the one-hour news
TTL never applies to a `www.`-prefixed host.

The load-bearing decision is **ruling R8, and it is asymmetric by direction.** Suffix matching
*narrows* what a denylist lets through, so `blocked_domains`, the operator's `seed_blocklist` and
the private-suffix list match by dot-boundary suffix unconditionally. Suffix matching *widens* what
an allowlist privileges — and `trusted` is not a label, it is a bypass: `run_promptguard` returns
SAFE with `skip_reason="trusted_tier"` without classifying (`pipeline/stage3_promptguard.py:77-88`)
and stage 4 assigns a 0.95 base score. A uniformly-applied suffix rule would let one caller-supplied
entry such as `trusted_domains: ["com"]` switch the classifier off for most of the web. So bare
allowlist entries (`trusted_domains`, `verified_domains`, `news_domains`) keep **exact** matching;
an entry written with a leading dot (`.example.com`) opts into the apex plus every subdomain. The
leading-dot form is one today's exact matcher can never match, so no existing entry changes
meaning. Every entry in every list must have at least two labels after any leading dot is removed;
single-label, empty-label, over-long and non-IDNA entries are ignored and counted, never a 422.

The second decision is policy parity between the two routes. `SearchRequest` (`models.py:273`)
carries no domain list, and `run_search_pipeline` never sees `config['seed_blocklist']` — only
`run_retrieve_pipeline` merges it (`pipeline/orchestrator.py:247-252`) — so a blocked domain's
title, URL and snippet still reach the model through search results. And the `/search` handler
never passes a threshold into `run_search_pipeline` (`retrieval_app.py:1811-1815`), so the
orchestrator's `promptguard_threshold: float = 0.85` default (`pipeline/orchestrator.py:783`) wins
regardless of `config.yaml`. Ruling 9 adds `blocked_domains` (and the `seed_blocklist` merge) to
`/search`; ruling R10 makes the request field `float | None` on both routes, meaning "the server's
configured threshold", with `/extract`'s own guarded read left exactly as it is.

The third is the config surface. `_load_config` is a bare `yaml.safe_load` (`retrieval_app.py:329`)
and each subsystem pulls its own keys with `.get(key, default)`, so a misspelled key is silently the
default. Ruling 12 adds a registry and a boot WARNING — never a refusal — and a test that ties the
registry to `docs/configuration.md`. The engine list is out: `tests/test_searxng_docker.py::
test_enabled_engines_match_the_orchestrator` already pins `searxng/config/settings.yml`'s enabled
engines to `SEARXNG_ENGINES`; this spec only writes that fact down (ruling 11).

## Goals

- One hostname predicate and one entry normaliser: `hostname_matches` is the only comparison used
  by the three sites; a parametrised test proves `evil.com` (denylist) matches `evil.com` and
  `www.evil.com` and never `notevil.com` or `evil.com.attacker.net`; that a bare `example.com`
  allowlist entry matches only `example.com`; that `.example.com` matches `example.com` and
  `a.b.example.com`; and that `com`, `.com`, `""`, `bad..entry` and a non-IDNA entry are ignored
  and counted at every site.
- `/search` honours `blocked_domains` and the operator's `seed_blocklist` (results omitted as
  `blocked_url`) and `promptguard_threshold` (per-request value, else `config.yaml`); a request
  that sends neither field yields a response equal to a committed pre-story baseline fixture.
- Zero silent config keys: every key `config.yaml` can carry is in `KNOWN_CONFIG_KEYS`, every
  registry key has a row in `docs/configuration.md`, an unknown key produces exactly one
  `config_unknown_key` WARNING naming the key (never its value), and a malformed top-level
  document never raises.
- The `/search`↔`/retrieve` boundary text cannot drift again: a test derives each route's knob set
  from `model_fields` and fails when a knob is missing from any of the seven authored copies.
- The SearXNG engine list's sync mechanism is named in the two docs that describe the list.

## User Stories

### US-001: Hostname matching — suffix for denylists, opt-in suffix for allowlists

**Priority:** P1

**Description:** As an operator, I want `blocked_domains` and `seed_blocklist` entries to cover
their subdomains, and `trusted_domains` / `verified_domains` / `news_domains` entries to cover
subdomains only when I write them with a leading dot, so that blocking `evil.com` blocks
`www.evil.com` while trusting `example.com` never silently trusts a subdomain I do not control.

**Independent Test:** A parametrised test over `hostname_matches`, `normalize_domain_entries` and
each of the three call sites — `validate_url(..., blocked_domains=["evil.com"])` with DNS patched
as `tests/test_url_validator.py` does, `_resolve_request_trust_tier("www.example.com",
[".example.com"], [], [])` versus `(["example.com"])`, and `_effective_ttl_hours(24,
domain="www.bbc.co.uk", news_domains=[".bbc.co.uk"])` — proves the rules above; every pre-existing
exact-match test in the four affected test modules still passes.

**Implementation Hints:**
- Add to `url_validator.py` (not a `_REVISION_SOURCES` member) beside `_check_hostname_blocklist`
  (`:95`): `normalize_domain_entries(entries, *, limit=64) -> tuple[list[str], int]` and
  `hostname_matches(host, entry, *, allow_suffix: bool) -> bool`. Normalisation of an entry: strip
  → lower → strip one trailing dot → remember and strip one leading dot (the opt-in marker) →
  IDNA-encode (`label.encode("idna")` per label; `UnicodeError` → invalid) → require ≥ 2 labels,
  no empty label, ≤ 253 chars. Invalid entries and entries past `limit` are dropped; the second
  return value is the dropped count. A normalised entry keeps its leading-dot marker (store as
  `(".example.com")` or a small dataclass) so the call site knows it opted in.
- Matching: normalise `host` the same way (strip trailing dot, lower, IDNA; an un-encodable host
  never matches anything — on `/search` spec 1 US-002 already rejects it as `invalid_url`).
  Denylist sites call `hostname_matches(host, entry, allow_suffix=True)`: equal, or `host` ends
  with `"." + entry`. Allowlist sites call it with `allow_suffix=<entry had the leading dot>`.
  `_BLOCKED_SUFFIXES` (`localhost`, `.local`) stays as it is.
- Replace the three comparisons: `url_validator.py:145-146` (keep raising `BlockedDomainError`
  with the same message shape; `validate_url` runs on every redirect hop —
  `pipeline/stage5_url_audit.py:144` — so a hop onto `www.blocked.example` is now refused),
  `pipeline/orchestrator.py:1114` (`blocked` still wins over `trusted` over `verified`), `cache.py:167`
  (`min(1, ttl_hours)` unchanged). `cache.py` may import from `url_validator`; `url_validator.py`
  must not import `cache` or `pipeline.orchestrator`.
- `/retrieve`'s three request lists (`models.py:249-257`) pass through `normalize_domain_entries`
  in the handler before the pipeline sees them (cap 64 each); the dropped count increments a new
  `RetrieveMetrics.policy_invalid_domain_entry` counter (`retrieval_app.py`, the `RetrieveMetrics`
  dataclass and its `/metrics` mirror — both `extra="forbid"`, so the mirror field and the
  handler's dict line move together; `tests/test_contract_metrics.py::
  test_dataclass_counters_and_their_models_carry_the_same_fields` (`:305`) pins the parity). Add
  the same field to `SearchMetrics` now (US-002 increments it) so the `/metrics` document moves
  once for this concern.
- Contract window (ruling 5): the three `RetrieveRequest` list descriptions and the `/metrics`
  counter are window changes — one `* ``1.3.0`` — …` line each in `pipeline/contract.py`'s
  docstring entry (read `:26-68` for the exact bullet shape; never `- `), state the rule in the
  field descriptions ("bare entries match exactly; a leading dot covers every subdomain;
  `blocked_domains` always covers subdomains"), `uv run python -m scripts.export_contract`,
  re-create `tests/golden/contract_1_3_0.json`, `--check` green. Record the classification in
  `contract/GOVERNANCE.md` "Recorded rulings": denylist widening is a tightening in the safe
  direction; the leading-dot form is additive; bare allowlist entries are unchanged.
- Rotation (ruling 6, R32): `pipeline/orchestrator.py` and `pipeline/contract.py` move. This is the
  first rotation that changes which hosts reach stage 3 (a `.example.com` entry skips PromptGuard
  for its subdomains), so `kit_tools/arch/DECISIONS.md:606`'s "Rotations to date, none changing
  sanitization behaviour" preamble is amended. The repo's rotation protocol is five files —
  `docs/bootstrap-notes.md` (next numbered heading, `before:/after:` pair), `CLAUDE.md`
  (Coexistence paragraph), `kit_tools/arch/DECISIONS.md` (table + preamble), `kit_tools/docs/
  GOTCHAS.md:410` ("moved fourteen times" counter and table), `kit_tools/arch/CODE_ARCH.md`
  (rotation narrative) — see `kit_tools/EXECUTION_LOG.md`'s record of the fourteenth. The rotation
  is also the cache invalidation for this semantics change (the key carries `sanitizer_revision`);
  add a one-line comment above `hostname_matches` saying any change to its semantics must be
  paired with a rotation, because matching semantics are a cache-key input in substance.
- Tests: `tests/test_url_validator.py` (blocklist), `tests/test_stage5_url_audit.py::
  TestBlocklistDuringFetch` (`:173`, redirect hop), new direct unit tests for
  `_resolve_request_trust_tier` (it has none today — `grep -rn _resolve_request_trust_tier tests/`
  is empty; the nearest is `tests/test_orchestrator.py::test_retrieve_trusted_tier_loaded_classifier_reports_skipped_trusted`),
  `tests/test_cache.py::TestTTLLogic`, and a `/retrieve` handler test for the counter.
- Docs the story owns (every site that says "exact"): `docs/configuration.md` `seed_blocklist` and
  `news_domains` rows (`:435`) plus a caution on the `trusted_domains` / `verified_domains` rows
  (never write a leading-dot entry for a multi-tenant apex such as `github.io` or
  `s3.amazonaws.com`); `kit_tools/docs/API_GUIDE.md:158` ("Exact-host match. Trusted content
  skips Prompt Guard entirely"); `kit_tools/arch/SECURITY.md:86` and the table row at `:107`;
  `kit_tools/docs/TROUBLESHOOTING.md:158` and `:417`; `kit_tools/docs/ENV_REFERENCE.md:86`;
  `kit_tools/arch/SERVICE_MAP.md:175` and `:262`. Each states the two-direction rule with the
  `evil.com` / `www.evil.com` / `notevil.com` and `example.com` / `.example.com` examples.

**Acceptance Criteria:**
- [ ] `hostname_matches` and `normalize_domain_entries` exist in `url_validator.py`; each of the
      three sites calls `hostname_matches` (`grep -c hostname_matches` ≥ 1 in `url_validator.py`,
      `pipeline/orchestrator.py` and `cache.py`) and none of the pre-existing expressions survives
      (`grep -n "lower_host in lower_blocked" url_validator.py`, `grep -n "in {d.lower()"
      pipeline/orchestrator.py` and `grep -n "in {item.lower()" cache.py` each return nothing).
- [ ] Parametrised matcher test covers at least: equal; `www.` prefix; deeper subdomain;
      `notevil.com`; `evil.com.attacker.net`; trailing dot on host and entry; `xn--mnchen-3ya.de`
      vs `münchen.de` in both directions; bare vs leading-dot allowlist entry; and the ignored
      shapes `com`, `.com`, `""`, `bad..entry`, a 254-char entry, an entry with a non-encodable
      label — each ignored, each counted once.
- [ ] `validate_url("https://www.evil.com/x", blocked_domains=["evil.com"])` raises
      `BlockedDomainError`; `validate_url("https://notevil.com/x", blocked_domains=["evil.com"])`
      does not; a redirect hop onto `www.blocked.example` under `blocked_domains=["blocked.example"]`
      is refused (`tests/test_stage5_url_audit.py::TestBlocklistDuringFetch`).
- [ ] `_resolve_request_trust_tier("www.example.com", ["example.com"], [], [])` returns
      `"standard"`; with `[".example.com"]` it returns `"trusted"`; with `blocked_domains=
      ["example.com"]` as well it returns `"blocked"`; `trusted_domains=["com"]` yields
      `"standard"` for `a.com`.
- [ ] `_effective_ttl_hours(24, domain="www.bbc.co.uk", news_domains=["bbc.co.uk"])` returns `24`
      and with `[".bbc.co.uk"]` returns `1`; `news_domains=["bbc.co.uk"]` with
      `domain="bbc.co.uk"` returns `1` (bare entries unchanged).
- [ ] A `/retrieve` request whose `trusted_domains` holds 70 entries including `"com"` and
      `" .Example.COM. "` applies exactly 64 valid normalised entries, and
      `/metrics` `retrieve.policy_invalid_domain_entry` advances by the dropped count; no 422.
- [ ] Every pre-existing exact-match test in `tests/test_url_validator.py`,
      `tests/test_stage5_url_audit.py`, `tests/test_orchestrator.py` and `tests/test_cache.py`
      passes unchanged.
- [ ] `grep -rn -iE "exact-host|exact host|exact, case-insensitive" kit_tools docs README.md`
      returns nothing; the eight doc sites named in the hints state the two-direction rule
      (`grep -c notevil.com` ≥ 1 in `docs/configuration.md`, `kit_tools/docs/API_GUIDE.md` and
      `kit_tools/arch/SECURITY.md`; the multi-tenant caution appears on the `trusted_domains` row).
- [ ] 1.3.0 window: docstring lines appended for the three list descriptions and the two
      `policy_invalid_domain_entry` counters; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created; `uv run python -m scripts.export_contract
      --check` green; `tests/golden/contract_1_2_0.json` unchanged; the GOVERNANCE ruling recorded.
- [ ] The `sanitizer_revision` rotation (`pipeline/orchestrator.py`, `pipeline/contract.py`) is
      measured by revert-and-reproduce and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md`,
      `kit_tools/arch/DECISIONS.md` (table and the amended "none changing sanitization" preamble),
      `kit_tools/docs/GOTCHAS.md` (counter incremented) and `kit_tools/arch/CODE_ARCH.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: `/search` honours `blocked_domains` and the operator's `seed_blocklist`

**Priority:** P1

**Description:** As a consumer and as an operator, I want a blocked domain — sent per request or
configured service-wide — to be excluded from search results exactly as it is refused on fetch, so
that a blocked domain's title, URL and snippet never reach the model through `/search`.

**Independent Test:** Through `POST /search` with a fake `searxng` provider published via
`tests/test_app.py::_borrowed_search_providers` (`:1625`) and `tests/fakes.py::FakeSearchProvider`
returning results on `a.example`, `www.blocked.example` and `blocked.example`: with
`{"blocked_domains": ["blocked.example"]}` the response carries one result and
`omitted_by_reason.blocked_url == 2`, `fallback_fired is False`, the paid fake's `calls == []`;
with no request field and `_load_config` patched to carry `seed_blocklist: ["blocked.example"]`
the same two results are omitted; with neither, the response equals a committed baseline fixture.

**Implementation Hints:**
- `SearchRequest.blocked_domains: list[str] = Field(default_factory=list, ...)` beside
  `providers`. **No pydantic `pattern`, `max_length` or item validation** — a FastAPI 422 would echo
  the caller's bytes (search-epic ruling 29). The handler runs `normalize_domain_entries` (US-001)
  and increments `SearchMetrics.policy_invalid_domain_entry` by the dropped count.
- Merge `config.get("seed_blocklist", [])` the way `run_retrieve_pipeline` does
  (`pipeline/orchestrator.py:247-252`); `run_search_pipeline` already receives `config=`
  (`retrieval_app.py:1815`, signature `pipeline/orchestrator.py:781`), so the merge is local to the
  orchestrator. Thread the normalised request list in as `blocked_domains: Sequence[str] = ()`.
- In the per-result loop, immediately after `_canonicalize_search_url` yields `domain` and after
  spec 1 US-003's audit, test `any(hostname_matches(domain, e, allow_suffix=True) for e in
  effective_blocklist)` and omit with `contract.OMIT_BLOCKED_URL` (spec 1 US-004's token; assert
  `OMISSION_REASONS` carries it before starting). Sufficiency for fallback is judged on raw
  provider results before this omission (search-epic ruling 17).
- Baseline: before touching the handler, capture
  `tests/fixtures/search_response_baseline_pre_blocked_domains.json` from the current code for
  the three fake results at threshold `0.85` (`request_id` excluded) and commit it; the after-test
  compares `json.loads(response.content)` minus `request_id` to it.
- Contract window (ruling 5): one `* ``1.3.0`` — …` docstring line for `blocked_domains`; the
  field description states the semantics; the copies of the boundary text that name
  `blocked_domains` as `/retrieve`-only are updated for this knob in the same story — the seven
  copies are `models.py:235` and `:287` (class docstrings → `components.schemas.*.description`),
  `retrieval_app.py:1588` and `:1792` (handler docstrings → operation descriptions),
  `kit_tools/docs/API_GUIDE.md:216-225`, `docs/configuration.md:28-37` and `README.md:58`;
  regenerate; re-create the 1.3.0 golden; `--check` green. (US-004 later pins all seven by test.)
- Docs: `kit_tools/docs/API_GUIDE.md` `/search` request table gains the row;
  `kit_tools/docs/MONITORING.md`'s `omitted_by_reason` row lists `blocked_url` as reachable from
  `blocked_domains` and `seed_blocklist`; `docs/configuration.md`'s `seed_blocklist` row and
  `kit_tools/arch/SECURITY.md:86` say "merged into both routes"; `kit_tools/arch/SERVICE_MAP.md:175`'s
  claim that the seed list is "also applied to `/search` result URLs" becomes true — leave it and
  cite it in the Implementation Notes.
- Rotation (ruling 6, R32): `pipeline/orchestrator.py` and `pipeline/contract.py` move; record on
  the five-file protocol named in US-001.

**Acceptance Criteria:**
- [ ] `SearchRequest.blocked_domains` exists, defaults to `[]`, carries no pydantic validation; a
      test sends 70 entries including `" BLOCKED.example. "` and `"com"` and asserts the match
      fires, `search.policy_invalid_domain_entry` advances by the dropped count, and no 422 occurs.
- [ ] A result whose `domain` matches a request entry or a `seed_blocklist` entry is omitted with
      `blocked_url`, counted in `omitted_by_reason`, after URL canonicalisation and before stage 2/3;
      `fallback_fired` is `False` and the paid fake's `calls == []`.
- [ ] The committed baseline fixture equals the response for a request sending no
      `blocked_domains` (and no `promptguard_threshold`), `request_id` excluded.
- [ ] 1.3.0 window: docstring line appended, contract regenerated, `tests/golden/contract_1_3_0.json`
      re-created, `--check` green, `contract_1_2_0.json` unchanged; the seven boundary-text copies no
      longer call `blocked_domains` a `/retrieve`-only knob (`grep -n "blocked_domains" models.py
      retrieval_app.py kit_tools/docs/API_GUIDE.md docs/configuration.md README.md` shows it named
      as shared in each).
- [ ] `kit_tools/docs/API_GUIDE.md` has the `/search` row (`grep -c blocked_domains
      kit_tools/docs/API_GUIDE.md` ≥ 2); `kit_tools/docs/MONITORING.md`'s `omitted_by_reason` row
      names `blocked_url`; `docs/configuration.md`'s `seed_blocklist` row says both routes.
- [ ] The `sanitizer_revision` rotation is measured and recorded on the five-file protocol.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-005: `promptguard_threshold` honoured on `/search`, defaulted from `config.yaml` on both routes

**Priority:** P1

**Description:** As an operator, I want `config.yaml`'s `promptguard_threshold` to be the default
on every route, and a caller's value to apply to `/search` as it does to `/retrieve`, so that
tightening the threshold is one edit and search is not scanned at a number nobody configured.

**Independent Test:** With `_load_config` patched to carry `promptguard_threshold: 0.5` and a
mocked classifier scoring `0.6`: `POST /search` omitting the field omits every result as
`injection_detected`, an explicit `0.85` serves them; `POST /retrieve` omitting the field blocks,
an explicit `0.85` serves; `/extract` behaves exactly as before (its own guarded read at
`retrieval_app.py:1703-1713` is untouched); a config carrying `promptguard_threshold: "abc"` or
`1.7` refuses boot with the same typed-error shape the `extraction:` block uses, naming the key.

**Implementation Hints:**
- `promptguard_threshold: float | None = Field(default=None, ge=0.0, le=1.0, ...)` on both
  `SearchRequest` and `RetrieveRequest` (`models.py:258`); `tests/test_models.py:194` asserts the
  old `0.85` default and is updated.
- Resolution lives in the operator-policy resolver spec 2 US-004 added to `retrieval_app.py`
  (locate it by `promptguard_threshold_ceiling`): it gains the rule "requested `None` → the
  validated config default"; `/search` passes the request value into it and the resolved value
  into `run_search_pipeline(promptguard_threshold=...)`. `/extract` keeps its own read and its
  `UnsupportedFormatError` mapping (ruling R10) — do not route it through the resolver.
- The config value is validated **at boot** on the `extraction_settings_from_config` precedent
  (`pipeline/extraction_limits.py:98-151`: bounded read, typed error, boot refused): float, `0.0 ≤ v
  ≤ 1.0`, absent → `0.85`. Publish the validated default on `app.state` beside the other settings;
  the resolver never re-reads `config` per request.
- The cache fingerprint (`cache.py:120`) receives the *resolved* threshold, never `None` — spec 2
  US-004 already passes the effective values into `run_retrieve_pipeline`; assert the same URL
  fetched with `null` and with an explicit value equal to the config default produces the same key.
- Contract window (ruling 5): one docstring line for the type change ("`null` means the server's
  configured threshold; honoured on `/search` from 1.3.0"); update the boundary-text copies that
  say `/search` has no per-request threshold — `kit_tools/docs/API_GUIDE.md:241`,
  `docs/configuration.md:37`, the two `models.py` and two `retrieval_app.py` docstrings;
  regenerate; golden; `--check` green.
- Upgrade note: `docs/configuration.md`'s `promptguard_threshold` row states that from `v1.2.0`
  the key is the default on all three routes (an operator tuned below `0.85` sees `/retrieve` and
  `/search` tighten on upgrade, and the content cache re-keys); the sentence is written so spec 8
  can lift it into the `v1.2.0` release notes.
- Rotation (ruling 6, R32): `pipeline/contract.py` moves (docstring line); `pipeline/orchestrator.py`
  moves only if the `0.85` default at `:783` is changed — measure and record either way.

**Acceptance Criteria:**
- [ ] `promptguard_threshold` is `float | None` defaulting to `None` on both request models;
      `tests/test_models.py`'s default assertion is updated.
- [ ] `/search` passes the resolved threshold into `run_search_pipeline`; with config `0.5` and a
      `0.6`-scoring classifier, omitting the field blocks on `/search` and `/retrieve`, an explicit
      `0.85` serves; `/extract`'s tests pass unchanged.
- [ ] A config `promptguard_threshold` of `"abc"`, `-0.1` or `1.7` refuses boot with a typed error
      naming the key; an absent key resolves to `0.85`.
- [ ] The cache-key assertion for `null` versus the explicit config default passes.
- [ ] 1.3.0 window: docstring line appended, regenerated, golden re-created, `--check` green;
      `grep -n "no per-request threshold" kit_tools/docs/API_GUIDE.md` and `grep -n "not applied on
      this route" docs/configuration.md` return nothing.
- [ ] `docs/configuration.md`'s `promptguard_threshold` row carries the upgrade note.
- [ ] The `sanitizer_revision` rotation is measured and recorded on the five-file protocol.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: `config.yaml` key registry — warn on unknown keys, docs parity by test

**Priority:** P2

**Description:** As an operator, I want a misspelled `config.yaml` key to be a boot WARNING naming
the key, and every key the service reads to have a documented row, so that a typo never silently
selects a default and the reference never lags the code.

**Independent Test:** With `_load_config` monkeypatched (the `tests/test_app.py` lifespan idiom)
to return the shipped config plus `promtguard_threshold: "SENTINEL-VALUE"` and `extraction:
{max_pagse: 1}`, the lifespan starts, `caplog` holds exactly two WARNING records whose
`getMessage()` contains `config_unknown_key` and the offending dotted key, `SENTINEL-VALUE` appears
nowhere in `caplog.text`, and `/health` is unaffected; a top-level YAML list starts the service
with no WARNING and no exception; a second test asserts `KNOWN_CONFIG_KEYS` equals the first-column
keys of `docs/configuration.md`'s three `config.yaml` tables (block names included) and is a
superset of the shipped `config.yaml`'s keys.

**Implementation Hints:**
- `KNOWN_CONFIG_KEYS: frozenset[str]` of dotted names next to `_load_config` (`retrieval_app.py:329`).
  Members: every top-level key (`user_agents`, `news_domains`, `seed_blocklist`,
  `promptguard_threshold`, `search_brave_*`, `extract_route_enabled`, …), the bare block names
  `cache` and `extraction`, and the dotted leaves the three readers consume
  (`cache_settings_from_config` `cache.py:~267`, `extraction_settings_from_config`
  `pipeline/extraction_limits.py:98-151`, `brave_settings_from_config` `brave.py:245-275`), plus
  every key this epic's earlier stories added (spec 2's `promptguard_fail_closed_floor`,
  `promptguard_threshold_ceiling`, `retrieve.*` keys). Later specs append their keys (spec 4's
  `cache.max_value_bytes`, spec 5's `search_searxng_*`, spec 6's envelope keys, spec 7's contiguity
  keys) — say so in a comment.
- `_warn_unknown_config_keys(config: object) -> list[str]`: if `config` is not a `dict`, return
  `[]` (a top-level list or scalar reaches the lifespan as `yaml.safe_load`'s value); walk one level
  into `cache:` and `extraction:` mappings; log one `logger.warning("config_unknown_key — key=%s",
  dotted)` per unknown key — the key name only, never the value (CLAUDE.md invariant 6); tokens in
  the message, never `extra=` (`kit_tools/arch/patterns/LOGGING.md`); call it in the lifespan
  right after `_load_config`. Never raise, never exit. A known key with a bad *value* still refuses
  boot through its reader's typed error (`ExtractionConfigurationError`, `CacheConfigurationError`)
  — the docs paragraph states that split.
- Docs-parity test in `tests/test_contract_metrics.py` (the module `kit_tools/testing/
  TESTING_GUIDE.md:279` maps `docs/configuration.md` to; it already holds `_CONFIGURATION_DOC`
  (`:59`) and `_posture_section()` (`:379`)); reuse `tests/test_governance_docs.py::_section`
  (`:128`, fence-aware) and `_cells` (`:158`) by import. Parse: the tables under `### Top-level
  keys`, `### The cache: block` and `### The extraction: block`; **first cell only**; block tables'
  keys are prefixed `cache.` / `extraction.`; the top-level table's `cache` and `extraction` rows
  register as bare block names. Model the shipped-config assertion on `tests/test_cache.py::
  TestCacheSettings::test_the_shipped_config_yaml_pins_the_documented_defaults`.
- Docs: `docs/configuration.md` gains a paragraph under `## config.yaml` stating the WARNING, that
  unknown keys are ignored, and that a known key with an invalid value still refuses boot (naming
  the two error types); `kit_tools/docs/MONITORING.md` `### Startup lines you may see` (`:229`)
  gains the `config_unknown_key` row; `kit_tools/arch/patterns/LOGGING.md`'s inventory gains the
  marker; `kit_tools/docs/TROUBLESHOOTING.md` gains a narrative `### config_unknown_key` note (it
  has no marker table — its tables are wire error codes).
- `retrieval_app.py` and the tests are not hashed; this story rotates nothing (verify
  `derive_sanitizer_revision({})` is unchanged after the story).

**Acceptance Criteria:**
- [ ] `KNOWN_CONFIG_KEYS` is a frozenset of dotted key names (plus the bare block names) in
      `retrieval_app.py`; a test asserts it is a superset of every key in the shipped `config.yaml`.
- [ ] A test asserts `KNOWN_CONFIG_KEYS` equals the first-column keys of `docs/configuration.md`'s
      three `config.yaml` tables with the stated prefixing rule (a key without a row, or a row
      without a key, is red).
- [ ] Booting with an unknown top-level key and an unknown `extraction.` key logs exactly one
      WARNING per key whose `getMessage()` contains `config_unknown_key` and the dotted key and
      never the value; the service starts; `/health` status is unchanged.
- [ ] Booting with a `config.yaml` whose top level is a list or a scalar starts the service, logs
      no `config_unknown_key` record and raises nothing from `_warn_unknown_config_keys`.
- [ ] Booting with the shipped `config.yaml` logs no `config_unknown_key` record.
- [ ] `docs/configuration.md`'s `## config.yaml` section states the warn-and-ignore rule and the
      bad-value-refuses-boot split; `kit_tools/docs/MONITORING.md`'s startup-lines table has a row
      whose first cell is `config_unknown_key`; `kit_tools/arch/patterns/LOGGING.md`'s inventory
      lists it; `kit_tools/docs/TROUBLESHOOTING.md` has the `### config_unknown_key` section.
- [ ] `docs/bootstrap-notes.md`'s latest `sanitizer_revision` record still matches
      `derive_sanitizer_revision({})`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: Boundary-text knob-parity guard across all seven copies

**Priority:** P2

**Description:** As a maintainer, I want the seven copies of the `/search`↔`/retrieve` boundary
text to be checked against the request models, so that the knobs this spec added (and any later
one) cannot be missing from the prose a consumer reads.

**Independent Test:** A test in `tests/test_contract_export.py` derives the route-specific knob
set as `(SearchRequest.model_fields.keys() ^ RetrieveRequest.model_fields.keys()) - _IDENTITY_FIELDS`
with `_IDENTITY_FIELDS = {"url", "query"}` named in the test, and asserts each name appears
backticked in all four `contract/openapi.yaml` strings (`paths['/search'].post.description`,
`paths['/retrieve'].post.description`, `components.schemas.SearchRequest.description`,
`components.schemas.RetrieveRequest.description`) and in the boundary paragraph of
`kit_tools/docs/API_GUIDE.md`, `docs/configuration.md` and `README.md`; temporarily removing a name
from one copy makes it fail.

**Implementation Hints:**
- The seven copies (US-002 and US-005 already touched them for their own knobs): `models.py:235`
  and `:287` (class docstrings → `components.schemas.*.description`), `retrieval_app.py:1588` and
  `:1792` (handler docstrings → operation descriptions), `kit_tools/docs/API_GUIDE.md:216-225`,
  `docs/configuration.md:28-37`, `README.md:58`. The existing guard
  `tests/test_contract_export.py::test_search_and_retrieve_descriptions_name_the_boundary`
  (`:181`) checks only that each names the other route; add the sibling beside it, reading
  `contract/openapi.yaml` through the same `CONTRACT_PATH`, and a Markdown half that slices each
  file's boundary paragraph (the paragraph containing "`/retrieve`" and "`/search`").
- After US-002 and US-005 the sets are: `/retrieve`-only `cache_ttl_hours`, `extract_mode`,
  `trusted_domains`, `verified_domains`; `/search`-only `allow_paid_fallback`, `num_results`,
  `providers`; shared `blocked_domains`, `promptguard_threshold`, `promptguard_fail_closed` —
  verify from `model_fields` at implementation time rather than from this list. Rewrite all seven
  copies to enumerate the route-specific knobs by backticked name and to name the shared ones as
  shared (there is no `ttl_hours` field; it is `cache_ttl_hours`).
- The four code copies feed the OpenAPI document (descriptions only): regenerate and re-create the
  1.3.0 golden (ruling 5); no docstring-entry line for a description-only change; `--check` green.
- `models.py`, `retrieval_app.py` and the tests are not hashed; this story rotates nothing.

**Acceptance Criteria:**
- [ ] The new test derives the knob set from `model_fields` minus the named identity fields and
      asserts every name in the four OpenAPI strings and the three Markdown paragraphs; deleting one
      name from any copy (and regenerating where applicable) makes it fail.
- [ ] All seven copies name every route-specific knob and mark the shared ones; the existing
      `test_search_and_retrieve_descriptions_name_the_boundary` still passes.
- [ ] `contract/openapi.yaml` regenerated; `tests/golden/contract_1_3_0.json` re-created;
      `uv run python -m scripts.export_contract --check` green.
- [ ] `docs/bootstrap-notes.md`'s latest `sanitizer_revision` record still matches
      `derive_sanitizer_revision({})`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-006: Name the engine-list sync mechanism (ruling 11)

**Priority:** P2

**Description:** As a maintainer reading the SearXNG docs, I want the two places that describe the
engine list to name the test that keeps it in step with `SEARXNG_ENGINES`, so nobody re-plans a
"single-sourcing" story for a list that is already pinned.

**Independent Test:** `grep -c test_enabled_engines_match_the_orchestrator docs/searxng.md
kit_tools/arch/SERVICE_MAP.md` returns `1` for each file.

**Implementation Hints:**
- `docs/searxng.md` `## Engines` and `kit_tools/arch/SERVICE_MAP.md`'s SearXNG section each gain
  one sentence: the enabled set in `searxng/config/settings.yml` and `SEARXNG_ENGINES` in
  `pipeline/search_providers/searxng.py` are kept in step by
  `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator`, which is the sync
  mechanism — there is no runtime read of the SearXNG config.
- Doc-only story; nothing rotates, no code, no test criteria.

**Acceptance Criteria:**
- [ ] `docs/searxng.md` and `kit_tools/arch/SERVICE_MAP.md` each name
      `test_enabled_engines_match_the_orchestrator` as the engine-list sync mechanism, once.

## Edge Cases

- Denylist entry `evil.com`, host `evil.com.attacker.net` → no match (suffix at a dot boundary,
  at the end). US-001.
- Allowlist entry `example.com`, host `www.example.com` → no match (bare entries stay exact);
  entry `.example.com` → match; entry `.example.com`, host `example.com` → match (apex included).
  US-001.
- Entry `com`, `.com`, `co` — fewer than two labels after the leading dot is removed → ignored
  and counted at every site; `co.uk` bare matches only the host `co.uk`; `.co.uk` is a deliberate
  operator opt-in covering every `.co.uk` host (the docs caution names this). US-001.
- Empty, whitespace-only, `bad..entry`, 254-character, or non-IDNA-encodable entry → ignored and
  counted; never a 422. US-001, US-002.
- Host `münchen.de` versus entry `xn--mnchen-3ya.de` (and the reverse) → match after IDNA
  normalisation; a host that fails IDNA encoding matches nothing (on `/search` spec 1 US-002 has
  already rejected it as `invalid_url`). US-001.
- Trailing dots on host or entry → stripped, match. US-001.
- A host on both `blocked_domains` and `trusted_domains` → `blocked` (existing precedence). US-001.
- A redirect hop onto a subdomain of a blocked entry → refused at that hop
  (`pipeline/stage5_url_audit.py:144`). US-001.
- More than 64 entries in any list → the first 64 valid entries apply; the rest are counted under
  `policy_invalid_domain_entry`; no 422. US-001 (`/retrieve`), US-002 (`/search`).
- `seed_blocklist` entry matching every search result → 200 with an empty list and
  `omitted_by_reason.blocked_url == n`; no fallback, no paid call. US-002.
- `promptguard_threshold: null` sent explicitly → identical to omitting it. US-005.
- `config.yaml` lacking `promptguard_threshold` → `0.85`; carrying a non-float or out-of-range
  value → boot refused with a typed error naming the key; `/extract`'s own guard is unchanged.
  US-005.
- Unknown key inside `cache:` → dotted `cache.<key>` WARNING; an unknown top-level block → one
  WARNING for the block name, its children not walked; a top-level list or scalar → no walk, no
  WARNING, no exception. US-003.
- A knob added to one request model without prose in every copy → US-004's guard is red.

## Out of Scope

- DNS-based checks at search time (ruling 7 — `validate_url` remains fetch-time only).
- `trusted_domains` / `verified_domains` on `/search` (trust tiers are a fetch-and-cache concept;
  search results carry no tier).
- A public-suffix list. Ruling R8's two-label minimum plus the explicit leading-dot opt-in is the
  guard; `.co.uk` is an operator's deliberate choice, documented as such.
- Refusing to boot on an unknown config key (ruling 12: warn, never refuse).
- Reading `searxng/config/settings.yml` at runtime, or generating `SEARXNG_ENGINES` from it
  (ruling 11: the parity test is the sync mechanism).
- Wire-level validation on any domain-list item (a pydantic pattern would echo the caller's bytes
  in a 422).
- The threshold ceiling and the `effective_*` response fields (spec 2 US-004, ruling R10).
- The resource-envelope keys (spec 6) and contiguity keys (spec 7) — they register themselves.

## Assumptions

- Spec 1 US-004 opened the 1.3.0 window and added `OMIT_BLOCKED_URL = "blocked_url"` to
  `pipeline/contract.py`'s `OMISSION_REASONS`; spec 2 US-004 added `promptguard_fail_closed_floor`,
  `promptguard_threshold_ceiling`, the operator-policy resolver in `retrieval_app.py`, and the
  `effective_*` response fields, and passes the effective values into `run_retrieve_pipeline`.
- Poppy is the only consumer; both new `/search` fields default to today's behaviour, so a 1.2.0
  client that never sends them sees no change **provided** its `config.yaml` carries the shipped
  `promptguard_threshold: 0.85` (an operator who tuned it sees the documented upgrade effect).
- Hostnames reaching the helper come from `urlsplit().hostname`, which lower-cases but does not
  IDNA-encode; the helper encodes both sides.
- The three settings readers stay `.get(key, default)`-shaped; the registry is asserted beside
  them, not woven into them.
- `/retrieve`'s request lists and `/search`'s share one normaliser and one 64-entry cap; the
  `providers` normaliser in `policy.py` (cap 8, `_MAX_POLICY_ENTRIES`, no trailing-dot rule) is a
  different function and stays separate.

## Technical Considerations

- **Rotation ledger (R32).** US-001: `pipeline/orchestrator.py` + `pipeline/contract.py`; US-002:
  the same two; US-005: `pipeline/contract.py` (+ `orchestrator.py` if the default moves); US-003,
  US-004, US-006: none. Each rotating story measures by revert-and-reproduce and records on the
  five-file protocol (`docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`,
  `kit_tools/docs/GOTCHAS.md`, `kit_tools/arch/CODE_ARCH.md`).
- **The rotation is the invalidation.** `cache_policy_fingerprint` keys on the domain *lists*, not
  the matching rule; US-001's change to what a list means is invalidated by the revision rotation
  (the fingerprint carries `sanitizer_revision`), the way `forage-cache-fallback` US-003 made it.
  The comment above `hostname_matches` says any later semantics change must be paired with one.
- **Threshold resolution and the cache key.** The resolver runs in the handler before anything
  reaches the cache; `None` never reaches `cache_policy_fingerprint`.
- **Ordering inside the result loop.** URL canonicalisation → spec 1's audit → `blocked_url` (this
  spec) → stage 2 → stage 3. A result rejected for its URL is counted once, under the first reason
  applied, and its text is never scanned.
- **Import graph.** `cache.py` and `pipeline/orchestrator.py` import from `url_validator.py`;
  `url_validator.py` imports neither.
- **`/extract` is untouched** by the threshold story (ruling R10); its guarded read and
  `UnsupportedFormatError` mapping stay the documented behaviour.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md), [SECURITY.md](../arch/SECURITY.md)
- Contract governance: [`contract/GOVERNANCE.md`](../../contract/GOVERNANCE.md)
- Operator reference: [`docs/configuration.md`](../../docs/configuration.md),
  [MONITORING.md](../docs/MONITORING.md), [API_GUIDE.md](../docs/API_GUIDE.md),
  [TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md), [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md)
- Logging vocabulary: [LOGGING.md](../arch/patterns/LOGGING.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** One `hostname_matches` helper plus one `normalize_domain_entries` normaliser in
`url_validator.py`, applied at three sites and to both routes' request lists.
**Rationale:** All three sites do `host in {lower-cased entries}` today (`url_validator.py:145-146`,
`pipeline/orchestrator.py:1114`, `cache.py:167`); one predicate cannot drift three ways, and one
normaliser gives both routes the same cap and the same IDNA rule.
**Alternatives considered:** Per-site suffix logic — rejected, that is how the drift started.
**Source:** explorer report 2026-09-19, item 4; WA-E punch list.

**Decision:** Suffix matching is unconditional for denylists and opt-in (leading dot) for
allowlists (ruling R8).
**Rationale:** `trusted` skips stage 3 entirely (`pipeline/stage3_promptguard.py:77-88`); a uniform
suffix rule would let `trusted_domains: ["com"]` disable the classifier. The leading-dot form is
unmatched by today's exact matcher, so it is additive and needs no MAJOR.
**Alternatives considered:** Uniform suffix matching with a public-suffix list — rejected as a
new dependency and a moving target; uniform exact matching — rejected, it leaves `www.evil.com`
unblocked.
**Source:** validation round 1 (salty engineer, security, second opinion).

**Decision:** Honour `promptguard_threshold` on `/search` through a `None`-means-config default,
validated at boot, with `/extract` untouched (ruling R10).
**Rationale:** The handler never passes a threshold today (`retrieval_app.py:1811-1815`);
`/extract`'s read is a guarded block raising `UnsupportedFormatError` (`:1703-1713`) that a plain
resolver would delete, and a bad config value must never become a per-request 500.
**Alternatives considered:** Routing `/extract` through the resolver — rejected (R10).
**Source:** validation round 1 (salty engineer, codebase fit, completionist).

**Decision:** Engine-list single-sourcing is closed by the existing parity test.
**Rationale:** `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator` fails
when `searxng/config/settings.yml` and `SEARXNG_ENGINES` diverge.
**Alternatives considered:** Runtime read of the SearXNG config — rejected, the image does not
ship it.
**Source:** ruling 11.

### Scope Adjustments

- Validation round 1 (R31): US-002 split into US-002 (`/search` `blocked_domains` +
  `seed_blocklist`) and US-005 (threshold); US-004 split into US-004 (seven-copy guard) and
  US-006 (engine-sync sentence). `execution_order` declared so the threshold story runs before the
  registry and guard stories.
- The `seed_blocklist` merge on `/search` was added (three reviewers found the omission).
- `/retrieve`'s three request lists gained the same normaliser and cap as `/search`'s, with one
  counter per route, so the "shared knob" prose is true.
- The WA-E engine-duplication item was dropped from code work (ruling 11).

### Decisions Made

- Warn, never refuse, on unknown config keys (ruling 12); a known key with a bad value still
  refuses boot through its reader — documented as a deliberate split.
- No pydantic validation on any domain-list item; normalise in the handler (search-epic ruling 29).
- The rotation protocol is five files, not three: `kit_tools/EXECUTION_LOG.md`'s record of the
  fourteenth rotation names `GOTCHAS.md` and `CODE_ARCH.md` alongside the three ruling 6 lists;
  this spec follows the repo's practice (parent to propagate to the wrapper).
- Overruled: the security reviewer's "same-name meaning change is a MAJOR" — bare allowlist
  entries keep their meaning and the denylist change is a tightening; classified and announced in
  the window instead (GOVERNANCE recorded ruling in US-001).
- Overruled: counting dropped entries once per request (search-epic pattern for `providers`) — one
  increment per dropped entry, because a safety list that is silently truncated must be
  observable entry by entry; the metric is content-free either way.
- The `providers` normaliser and the domain normaliser stay separate functions (caps 8 vs 64,
  trailing-dot and IDNA rules differ); the `_FORBIDDEN_IMPORTS` sweep
  (`tests/test_search_providers.py:229`) would permit sharing later.

## Clarifications

### Session 2026-09-19
- Q: Should hostname matching be suffix-based everywhere or only for the blocklist? → A:
  Originally everywhere (ruling 8); revised in validation round 1 to denylists unconditionally,
  allowlists by leading-dot opt-in (ruling R8).
- Q: Does `/search` get the full `/retrieve` domain-policy surface? → A: Only `blocked_domains`
  and the operator's `seed_blocklist`; trust tiers stay a fetch concept.
- Q: Is the engine list a config-sourcing problem? → A: No (ruling 11) — the parity test is the
  sync mechanism; document it.

### Session 2026-09-19 (validation round 1)
- Rulings applied: R8 (asymmetric matching, IDNA, two-label minimum, `policy_invalid_domain_entry`,
  `seed_blocklist` on search), R10 (honour + config default only; `/extract` untouched; the ceiling
  is spec 2's), R31 (US-002 → US-002 + US-005; US-004 → US-004 + US-006; `execution_order`), R32
  (rotation ledger corrected), R34 (docstring bullet format).
- Q: Does a bare `trusted_domains` entry change meaning on upgrade? → A: No; only the new
  leading-dot form widens, and the docs caution names multi-tenant apexes.
- Q: Where does a malformed config threshold fail? → A: At boot, typed error, never per request;
  `/extract` keeps its own guard.

## Open Questions

- [ ] Whether `KNOWN_CONFIG_KEYS` should also carry a per-key "read by" pointer for the docs table
      (non-blocking; a later docs sweep can add it).
- [ ] Whether a `FORAGE_TRUST_SUFFIX_DENYLIST`-style shipped list of multi-tenant apexes should
      refuse leading-dot allowlist entries outright (non-blocking; the docs caution and the
      two-label rule are the guard for this epic).
