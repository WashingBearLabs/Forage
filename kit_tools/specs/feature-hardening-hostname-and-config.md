<!-- Template Version: 2.5.0 -->
---
feature: hardening-hostname-and-config
status: active
session_ready: true
depends_on: [hardening-retrieve-parity]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 3
epic_final: false
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Hostname Semantics + Config Single-Sourcing

> **Spec 3 of `epic-forage-hardening`.** Make every hostname-against-a-list comparison in the
> service mean the same thing (dot-boundary suffix matching, ruling 8), give `/search` the
> domain-policy and threshold knobs `/retrieve` already has (rulings 9, 10), and put the
> `config.yaml` surface behind one key registry so an operator typo is a WARNING and every key
> has a documented row (ruling 12). Ruling 11 records that the SearXNG engine list is *already*
> single-sourced by test and takes it off this spec's plate.
> Context: WA-E punch list ("Exact-hostname matching breaks blocklists, trust tiers, and news
> TTLs", "`blocked_domains` applies to fetch but never search", "`promptguard_threshold` is dead
> for 2 of 3 endpoints", "Nine distinct config surfaces"), audit finding 2026-09-16-058, and the
> epic's rulings 5, 6, 8, 9, 10, 11 and 12 are binding here.

## Overview

Three exact-match sites decide security-relevant things today, and all three read
`host in {lower-cased entries}`: `validate_url`'s `blocked_domains` check
(`url_validator.py:143-147`), `_resolve_request_trust_tier` (`pipeline/orchestrator.py:1114-1128`)
and `_effective_ttl_hours`'s `news_domains` check (`cache.py:167-178`). Blocking `evil.com`
therefore does not block `www.evil.com`; trusting `example.com` silently *downgrades*
`www.example.com` to the standard (fail-closed-eligible) tier; the one-hour news TTL never applies
to a `www.`-prefixed host. The load-bearing decision is **ruling 8**: one helper,
`hostname_matches(host, entry)`, true when the two are equal or when `host` ends with
`"." + entry`, applied at all three sites. A bare host still matches itself, so no existing
configuration changes meaning; `notevil.com` never matches `evil.com`.

The second decision is parity of policy between the two routes. `SearchRequest`
(`models.py:273-317`) carries no domain lists at all, so a blocked domain's title, URL and snippet
still reach the model through search results — only a *fetch* is refused. And the `/search` handler
(`retrieval_app.py:1778-1815`) never passes a threshold into `run_search_pipeline`, so its
`promptguard_threshold: float = 0.85` default (`pipeline/orchestrator.py:783`) wins regardless of
`config.yaml`'s `promptguard_threshold` (`config.yaml:15`) — which `/extract` reads live
(`retrieval_app.py:1704`) and `/retrieve` shadows with the request field's own `0.85` default
(`models.py:258-263`). Ruling 10 makes the request field `float | None` on both routes, meaning
"use the server's configured threshold", and ruling 9 adds `blocked_domains` to `/search` whose
matches are omitted under the `blocked_url` token spec 1 introduced. Both are additive and land
inside the unreleased 1.3.0 window spec 1 US-004 opened (ruling 5).

The third is the config surface itself. `_load_config` is a bare `yaml.safe_load`
(`retrieval_app.py:330-337`) and each subsystem pulls its own keys with `.get(key, default)`
(`cache_settings_from_config` `cache.py:267`, `extraction_settings_from_config`
`pipeline/extraction_limits.py:98-151`, `brave_settings_from_config` `brave.py:245-275`), so a
misspelled key is silently the default. Ruling 12 adds a registry and a boot WARNING — never a
refusal, because an operator typo must not take the service down — and a test that ties the
registry to `docs/configuration.md`. The engine list is out: `tests/test_searxng_docker.py::
test_enabled_engines_match_the_orchestrator` already pins `searxng/config/settings.yml`'s enabled
engines to `SEARXNG_ENGINES`; this spec only writes that fact down (ruling 11).

## Goals

- One hostname predicate: `hostname_matches` is the only comparison used by the three sites; a
  parametrised test proves `evil.com` matches `evil.com` and `www.evil.com` and never `notevil.com`
  or `evil.com.attacker.net`, at every site.
- `/search` honours `blocked_domains` (results omitted as `blocked_url`) and
  `promptguard_threshold` (per-request value, else `config.yaml`); a request that sends neither
  produces byte-identical output to today for a threshold of `0.85`.
- Zero silent config keys: every key `config.yaml` can carry is in `KNOWN_CONFIG_KEYS`, every
  registry key has a row in `docs/configuration.md`, and an unknown key produces exactly one
  `config_unknown_key` WARNING at boot and no other change.
- The `/search`↔`/retrieve` boundary text cannot drift again: a test derives each route's knob set
  from `model_fields` and fails when a knob is missing from any of the four prose copies.

## User Stories

### US-001: Dot-boundary hostname matching at the three list sites

**Priority:** P1

**Description:** As an operator, I want `blocked_domains`, `trusted_domains`, `verified_domains`
and `news_domains` entries to cover their subdomains, so that blocking `evil.com` blocks
`www.evil.com`, trusting `example.com` does not downgrade `www.example.com`, and the news TTL
applies to `www.`-prefixed hosts.

**Independent Test:** A parametrised test over `hostname_matches` and over each of the three call
sites — `validate_url(..., blocked_domains=["evil.com"])` (DNS patched, as the existing
`url_validator` tests do), `_resolve_request_trust_tier("www.example.com", ["example.com"], [], [])`,
and `_effective_ttl_hours(24, domain="www.bbc.co.uk", news_domains=["bbc.co.uk"])` — proves suffix
matching at a dot boundary and rejects `notevil.com` / `evil.com.attacker.net`; every pre-existing
exact-match test still passes.

**Implementation Hints:**
- Add `hostname_matches(host: str, entry: str) -> bool` to `url_validator.py` (not a
  `_REVISION_SOURCES` member) beside `_check_hostname_blocklist` (`:95-102`). Normalise both sides:
  lower-case, strip one trailing dot, strip one leading dot from `entry` (an operator may write
  `.evil.com`); an empty entry never matches. Match is `host == entry or host.endswith("." + entry)`.
- Replace the three set-membership checks with the helper: `url_validator.py:143-147` (keep raising
  `BlockedDomainError` with the same message shape), `pipeline/orchestrator.py:1114-1128` (blocked
  still wins over trusted over verified — preserve the existing order), `cache.py:167-178`
  (`min(1, ttl_hours)` unchanged). `cache.py` may import from `url_validator`; check the import
  graph first so no cycle appears (`url_validator.py` must not import `cache`).
- `pipeline/orchestrator.py` is hashed: measure the rotation by revert-and-reproduce and record it
  (ruling 6) in `docs/bootstrap-notes.md` (next numbered rotation heading with the `before:/after:`
  pair, following `:362-368`'s shape), `CLAUDE.md`'s Coexistence paragraph and
  `kit_tools/arch/DECISIONS.md`'s rotation table.
- Tests: extend `tests/test_url_validator.py` (or the module that today covers
  `validate_url`'s blocklist — locate it with `grep -rn "BlockedDomainError" tests/`),
  `tests/test_orchestrator.py`'s trust-tier tests, and `tests/test_cache.py::TestTTLLogic`.
- Docs the story owns: `docs/configuration.md`'s `news_domains` / `seed_blocklist` rows and
  `kit_tools/docs/API_GUIDE.md`'s `/retrieve` domain-list descriptions state the suffix rule with
  the `evil.com` / `www.evil.com` / `notevil.com` example; `kit_tools/arch/SECURITY.md`'s
  hostname/blocklist paragraph says the same.

**Acceptance Criteria:**
- [ ] `hostname_matches` exists in `url_validator.py`, is the only hostname-vs-entry comparison
      at the three sites (`grep -n "in {" url_validator.py pipeline/orchestrator.py cache.py`
      shows no lower-cased set-membership hostname check remains), and is parametrised over at
      least: equal, `www.` prefix, deeper subdomain, `notevil.com`, `evil.com.attacker.net`,
      trailing dot, leading-dot entry, empty entry.
- [ ] `validate_url("https://www.evil.com/x", blocked_domains=["evil.com"])` raises
      `BlockedDomainError`; `validate_url("https://notevil.com/x", blocked_domains=["evil.com"])`
      does not reach the blocklist branch.
- [ ] `_resolve_request_trust_tier("www.example.com", ["example.com"], [], [])` returns
      `"trusted"`; with `blocked_domains=["example.com"]` as well it returns `"blocked"`.
- [ ] `_effective_ttl_hours(24, domain="www.bbc.co.uk", news_domains=["bbc.co.uk"])` returns `1`.
- [ ] Every pre-existing exact-match test in the three test modules passes unchanged.
- [ ] `docs/configuration.md`, `kit_tools/docs/API_GUIDE.md` and `kit_tools/arch/SECURITY.md`
      each carry the suffix-rule sentence with the `www.evil.com` / `notevil.com` example (`grep -c
      notevil.com` ≥ 1 in each).
- [ ] The `sanitizer_revision` rotation is measured (revert-and-reproduce) and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: `/search` policy parity — `blocked_domains` and an honoured threshold

**Priority:** P1

**Description:** As a consumer, I want to send `blocked_domains` and `promptguard_threshold` on
`/search` exactly as I do on `/retrieve`, so that a blocked domain's snippet never reaches the
model through a search result and a tightened threshold applies to search, not only to fetch.

**Independent Test:** Through `POST /search` with a fake `searxng` provider (`tests/fakes.py::
FakeSearchProvider`) returning three results on `a.example`, `www.blocked.example` and
`blocked.example`: with `{"blocked_domains": ["blocked.example"]}` the response carries one result
and `omitted_by_reason.blocked_url == 2`; with `{"promptguard_threshold": 0.5}` a mocked classifier
scoring `0.6` omits every result as `injection_detected` where the default `0.85` serves them; with
neither field, the response bytes equal today's for the same fixtures; on `/retrieve`, omitting
`promptguard_threshold` uses `config.yaml`'s value (a test loads a config with `0.5` and asserts
the `0.6`-scoring classifier blocks).

**Implementation Hints:**
- `SearchRequest.blocked_domains: list[str] = Field(default_factory=list, ...)` beside
  `providers` (`models.py:302`). **No pydantic `pattern` or `max_length` on the items** — a FastAPI
  422 would echo the caller's bytes (the search epic's ruling 29). Normalise in the `/search`
  handler the way `apply_request_policy` normalises `providers` (`retrieval_app.py:1797-1799`,
  `pipeline/search_providers/policy.py`): strip, lower-case, strip a trailing dot, drop empties,
  keep the first 64; a dropped or over-limit entry is ignored silently (there is no
  policy-counter for domains — say so in the description).
- Thread the normalised list into `run_search_pipeline` as a new keyword
  (`blocked_domains: Sequence[str] = ()`); in the per-result loop, immediately after
  `_canonicalize_search_url` yields `domain` (`pipeline/orchestrator.py:~644`, `:969-997`), test
  `any(hostname_matches(domain, entry) for entry in blocked_domains)` and omit with
  `contract.OMIT_BLOCKED_URL` (spec 1 US-004's token; check `pipeline/contract.py`
  `OMISSION_REASONS` carries it before starting). Sufficiency for fallback is judged on raw
  provider results before this omission (the search epic's ruling 17) — a blocked SERP must not
  buy a paid call; assert `fallback_fired is False` in the test.
- Threshold (ruling 10): `promptguard_threshold: float | None = Field(default=None, ge=0.0,
  le=1.0, ...)` on both `SearchRequest` and `RetrieveRequest` (`models.py:258-263`, `:284-285`).
  One resolver in `retrieval_app.py`, `_resolved_promptguard_threshold(state, requested)`, returns
  `requested` when not `None`, else `float(state.config.get("promptguard_threshold", 0.85))` — the
  same read `/extract` performs at `:1704`; make `/extract` call the resolver too so there is one
  read site. The `/search` handler passes `promptguard_threshold=` into `run_search_pipeline`
  (`:1811-1815`), `/retrieve` passes the resolved value where it passes
  `request.promptguard_threshold` today (`pipeline/orchestrator.py:257,372` — `run_retrieve_pipeline`
  gains the resolved float as an argument rather than reading the request field). The cache
  fingerprint (`cache.py:120-164`) must receive the *resolved* threshold, never `None`.
- Contract window (ruling 5): both field descriptions state the semantics ("`null` (the default)
  means the server's `config.yaml` `promptguard_threshold`; honoured on `/search` from contract
  1.3.0"); append one line each for `blocked_domains` and the threshold change to the 1.3.0
  `CONTRACT_VERSION` docstring entry in `pipeline/contract.py`; run
  `uv run python -m scripts.export_contract`; re-create `tests/golden/contract_1_3_0.json`
  (`1_2_0` and older untouched); `--check` green.
- Docs: `kit_tools/docs/API_GUIDE.md` `/search` request table gains the two rows;
  `kit_tools/docs/MONITORING.md`'s `omitted_by_reason` row lists `blocked_url` as reachable from
  `blocked_domains`; `docs/configuration.md`'s `promptguard_threshold` row says it is the default
  for all three routes.
- Both `pipeline/orchestrator.py` and `pipeline/contract.py` are hashed: one rotation, measured and
  recorded (ruling 6).

**Acceptance Criteria:**
- [ ] `SearchRequest.blocked_domains` exists, defaults to `[]`, carries no pydantic pattern or
      length validation; the handler normalises (strip, lower, trailing dot, empties dropped,
      first 64 kept) and a test sends 70 entries including `" BLOCKED.example. "` and asserts the
      match still fires and no 422 is produced.
- [ ] A search result whose `domain` matches an entry by `hostname_matches` is omitted with
      `blocked_url` and counted in `omitted_by_reason`; the omission happens after URL
      canonicalisation and before stage 2/3, and never triggers fallback (`fallback_fired` is
      `False`, the paid fake's `calls == []`).
- [ ] `promptguard_threshold` is `float | None` defaulting to `None` on both request models; the
      `/search` handler passes the resolved value into `run_search_pipeline`; `/retrieve` and
      `/extract` resolve through the same helper; a config with `promptguard_threshold: 0.5` makes
      a `0.6`-scoring result blocked on all three routes when the request omits the field, and an
      explicit `0.85` on the request serves it.
- [ ] The cache fingerprint for `/retrieve` is computed from the resolved threshold: a test asserts
      the same URL fetched with `null` and with an explicit value equal to the config default
      produces the same cache key.
- [ ] A `/search` request sending neither field yields byte-identical JSON to the pre-story
      response for the same fake results at the default threshold (`0.85` in the test config).
- [ ] 1.3.0 window: docstring lines appended, `uv run python -m scripts.export_contract` run,
      `tests/golden/contract_1_3_0.json` re-created, `uv run python -m scripts.export_contract
      --check` green; `tests/golden/contract_1_2_0.json` unchanged (`git diff --stat`).
- [ ] `kit_tools/docs/API_GUIDE.md` and `kit_tools/docs/MONITORING.md` carry the rows named in the
      hints (`grep -c blocked_domains kit_tools/docs/API_GUIDE.md` ≥ 2 — one per route).
- [ ] The `sanitizer_revision` rotation is measured (revert-and-reproduce) and recorded in
      `docs/bootstrap-notes.md`, `CLAUDE.md` and `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: `config.yaml` key registry — warn on unknown keys, docs parity by test

**Priority:** P2

**Description:** As an operator, I want a misspelled `config.yaml` key to be a boot WARNING naming
the key, and every key the service reads to have a documented row, so that a typo never silently
selects a default and the reference never lags the code.

**Independent Test:** With `_load_config` monkeypatched (the `tests/test_app.py:1199+` idiom) to
return the shipped config plus `promtguard_threshold: 0.5` and `extraction: {max_pagse: 1}`, the
lifespan starts, `caplog` holds exactly two WARNING records whose `getMessage()` contains
`config_unknown_key` and the offending dotted key, and `/health` is unaffected; a second test
asserts `KNOWN_CONFIG_KEYS` equals the set of backticked keys in `docs/configuration.md`'s three
`config.yaml` tables and is a superset of the keys in the shipped `config.yaml`.

**Implementation Hints:**
- `KNOWN_CONFIG_KEYS: frozenset[str]` of dotted names (`promptguard_threshold`,
  `search_brave_timeout_seconds`, `cache.max_entries`, `extraction.max_pages`, …) next to
  `_load_config` in `retrieval_app.py:330-337`. Build the list from the three `*_settings_from_config`
  readers (`cache.py:267`, `pipeline/extraction_limits.py:98-151`, `brave.py:245-275`) and the
  top-level reads (`user_agents`, `news_domains`, `seed_blocklist`, `promptguard_threshold`,
  `search_brave_*`, `extract_route_enabled` — `config.yaml:1-48`), plus every key this epic's
  earlier stories added (`promptguard_fail_closed_floor` from spec 2 US-004). Later specs append
  to the registry when they add keys (spec 5's `search_searxng_query_max_chars`, spec 6's
  latency and thread keys, spec 7's contiguity keys) — say so in a comment.
- `_warn_unknown_config_keys(config: dict[str, Any]) -> list[str]` walks one level into the
  `cache:` and `extraction:` mappings (deeper nesting is not a config shape today), logs one
  `logger.warning("config_unknown_key — key=%s", dotted)` per unknown key with the token in the
  message (never `extra=`, per `kit_tools/arch/patterns/LOGGING.md`), and returns the list; call it
  in the lifespan right after `_load_config` (`retrieval_app.py:~1214`). Never raise, never exit.
- The docs-parity test lives in `tests/test_governance_docs.py`'s style (a doc-scanning test):
  parse the `| Key | …` tables under `docs/configuration.md` `### Top-level keys` (`:430`), `### The
  cache: block` (`:445`) and `### The extraction: block` (`:471`), collect the backticked key names,
  prefix the block name, compare with `KNOWN_CONFIG_KEYS`. Reuse `tests/test_cache.py::
  TestCacheSettings::test_the_shipped_config_yaml_pins_the_documented_defaults` (`:869`) as the
  model for the shipped-config assertion.
- Docs: `docs/configuration.md` gains a paragraph under `## config.yaml` (`:414`) stating the
  WARNING and that unknown keys are ignored; `kit_tools/docs/TROUBLESHOOTING.md` gains a
  `config_unknown_key` row; `kit_tools/arch/patterns/LOGGING.md`'s inventory gains the marker.
- `retrieval_app.py` is not hashed; this story rotates nothing.

**Acceptance Criteria:**
- [ ] `KNOWN_CONFIG_KEYS` is a frozenset of dotted key names in `retrieval_app.py`; a test asserts it
      is a superset of every key (top-level and one level under `cache:` / `extraction:`) in the
      shipped `config.yaml`.
- [ ] A test asserts `KNOWN_CONFIG_KEYS` equals the set of keys documented in
      `docs/configuration.md`'s three `config.yaml` tables (so adding a key without a row, or a row
      without a key, is red).
- [ ] Booting with an unknown top-level key and an unknown `extraction.` key logs exactly one
      WARNING per key whose `getMessage()` contains `config_unknown_key` and the dotted key; the
      service starts; `/health` status is unchanged.
- [ ] Booting with the shipped `config.yaml` logs no `config_unknown_key` record.
- [ ] `docs/configuration.md`, `kit_tools/docs/TROUBLESHOOTING.md` and
      `kit_tools/arch/patterns/LOGGING.md` carry the sentences/rows named in the hints.
- [ ] `docs/bootstrap-notes.md`'s latest `sanitizer_revision` record still matches
      `derive_sanitizer_revision()` (this story edits no `_REVISION_SOURCES` file).
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: Boundary-text knob-parity guard + the engine-sync sentence

**Priority:** P2

**Description:** As a maintainer, I want the four copies of the `/search`↔`/retrieve` boundary
text to be checked against the request models, so that the knobs this spec added (and any later
one) cannot be missing from the prose a consumer reads.

**Independent Test:** A test in `tests/test_contract_export.py` derives the symmetric difference of
`SearchRequest.model_fields` and `RetrieveRequest.model_fields`, and asserts each route-specific
field name appears backticked in that route's OpenAPI operation description and in the other
route's ("what the other route has that this one lacks"); temporarily removing a name from one
docstring makes it fail. `docs/searxng.md` and `kit_tools/arch/SERVICE_MAP.md` name the parity
test that keeps the engine list in step.

**Implementation Hints:**
- The four copies: `models.py:225-236` (`RetrieveRequest` docstring), `:277-288` (`SearchRequest`),
  `retrieval_app.py:1578-1590` (`retrieve` handler), `:1778-1793` (`search` handler). The existing
  guard is `tests/test_contract_export.py::test_search_and_retrieve_descriptions_name_the_boundary`
  (`:181-200`), which only checks that each names the other route; add the sibling test beside it
  reading `contract/openapi.yaml` the same way (`CONTRACT_PATH`, `paths[...]["post"]["description"]`).
- Rewrite the four copies to enumerate the route-specific knobs by backticked name: after
  US-002, `/retrieve`-only knobs are `trusted_domains`, `verified_domains`, `extract_mode`,
  `ttl_hours` (verify the exact set from `model_fields` rather than this list) and
  `/search`-only knobs are `num_results`, `providers`, `allow_paid_fallback`; shared knobs
  (`blocked_domains`, `promptguard_threshold`, `promptguard_fail_closed`) are named as shared.
  Because the docstrings feed the OpenAPI document, this moves `contract/openapi.yaml`
  (descriptions only): regenerate and re-create the 1.3.0 golden (ruling 5); no docstring-entry
  line is needed for a description-only change, but `--check` must be green.
- Engine sync (ruling 11): `docs/searxng.md` `## Engines` (`:184-192`) and
  `kit_tools/arch/SERVICE_MAP.md`'s SearXNG section gain one sentence: the enabled set in
  `searxng/config/settings.yml` and `SEARXNG_ENGINES` in `pipeline/search_providers/searxng.py:55`
  are kept in step by `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator`,
  which is the sync mechanism — there is no runtime read of the SearXNG config.
- `models.py` and `retrieval_app.py` are not hashed; this story rotates nothing.

**Acceptance Criteria:**
- [ ] A new test in `tests/test_contract_export.py` derives the route-specific knob sets from
      `model_fields` and asserts each backticked name is present in both operation descriptions
      in `contract/openapi.yaml`; deleting one name from a docstring (and regenerating) makes it fail.
- [ ] All four prose copies name every route-specific knob and mark the shared ones; the existing
      `test_search_and_retrieve_descriptions_name_the_boundary` still passes.
- [ ] `contract/openapi.yaml` regenerated; `tests/golden/contract_1_3_0.json` re-created;
      `uv run python -m scripts.export_contract --check` green.
- [ ] `docs/searxng.md` and `kit_tools/arch/SERVICE_MAP.md` each name
      `test_enabled_engines_match_the_orchestrator` as the engine-list sync mechanism.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- Entry `evil.com`, host `evil.com.attacker.net` → no match (suffix must be at a dot boundary and
  at the end). US-001.
- Entry with a leading dot (`.evil.com`) or trailing dot (`evil.com.`), host with a trailing dot →
  normalised, match. US-001.
- Empty or whitespace-only entry → never matches anything (an empty suffix would match every
  host). US-001, US-002.
- A host on both `blocked_domains` and `trusted_domains` → `blocked` (existing precedence kept).
  US-001.
- `/search` `blocked_domains` with more than 64 entries → first 64 apply, the rest are dropped
  silently; no 422. US-002.
- `blocked_domains` entry matching every result → 200 with an empty list and
  `omitted_by_reason.blocked_url == n`; no fallback, no paid call. US-002.
- `promptguard_threshold: null` sent explicitly → identical to omitting it. US-002.
- `config.yaml` lacking `promptguard_threshold` entirely → resolver falls back to `0.85`, the same
  code default `/extract` uses today. US-002.
- Unknown key inside `cache:` → dotted `cache.<key>` WARNING; an unknown *block* at top level →
  one WARNING for the block name, its children not walked. US-003.
- `config.yaml` that is not a mapping at top level → today's behaviour (whatever `_load_config`'s
  callers do with a non-dict) is preserved; the registry walk is skipped. US-003.

## Out of Scope

- DNS-based checks at search time (ruling 7 — `validate_url` remains fetch-time only).
- `trusted_domains` / `verified_domains` on `/search` (trust tiers are a fetch-and-cache concept;
  search results carry no tier).
- Refusing to boot on an unknown config key (ruling 12: warn, never refuse).
- Reading `searxng/config/settings.yml` at runtime, or generating `SEARXNG_ENGINES` from it
  (ruling 11: the parity test is the sync mechanism).
- Wire-level bounds on `blocked_domains` items (a pydantic pattern would echo the caller's bytes
  in a 422).
- The resource-envelope keys (spec 6) and contiguity keys (spec 7) — they register themselves.

## Assumptions

- Spec 1 US-004 opened the 1.3.0 window and added `OMIT_BLOCKED_URL = "blocked_url"` to
  `pipeline/contract.py`'s `OMISSION_REASONS`; spec 2 US-004 added `promptguard_fail_closed_floor`
  to `config.yaml` and its `docs/configuration.md` row.
- Poppy is the only consumer; both new `/search` fields default to today's behaviour, so a 1.2.0
  client that never sends them sees no change.
- Hostnames reaching the helper come from `urlsplit().hostname` (already lower-case ASCII or
  punycode); no IDNA normalisation is attempted.
- The three settings readers stay `.get(key, default)`-shaped; the registry is asserted beside
  them, not woven into them.

## Technical Considerations

- **Two rotations, not four.** US-001 and US-002 both edit `pipeline/orchestrator.py` (US-002 also
  `pipeline/contract.py`); US-003 and US-004 touch no hashed file. Record each rotation once
  (ruling 6) and confirm US-003/US-004 leave `derive_sanitizer_revision()` unchanged.
- **Threshold resolution and the cache key.** `cache_policy_fingerprint` (`cache.py:120-164`) takes
  the threshold as an input; passing `None` would silently create a second key space. The resolver
  runs in the handler before anything reaches the cache.
- **Ordering inside the result loop.** URL canonicalisation → `blocked_url` (this spec and spec 1
  US-003's audit) → stage 2 → stage 3. A result rejected for its URL is counted once, under the
  URL reason, and its text is never scanned.
- **Import graph.** `cache.py` importing `hostname_matches` from `url_validator.py` is new; keep
  `url_validator.py` free of `cache`/`orchestrator` imports.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md), [SECURITY.md](../arch/SECURITY.md)
- Contract governance: [`contract/GOVERNANCE.md`](../../contract/GOVERNANCE.md)
- Operator reference: [`docs/configuration.md`](../../docs/configuration.md),
  [MONITORING.md](../docs/MONITORING.md), [API_GUIDE.md](../docs/API_GUIDE.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** One `hostname_matches` helper in `url_validator.py`, applied at three sites.
**Rationale:** All three sites do `host in {lower-cased entries}` today
(`url_validator.py:143-147`, `pipeline/orchestrator.py:1114-1128`, `cache.py:167-178`); one
predicate cannot drift three ways.
**Alternatives considered:** Per-site suffix logic — rejected, that is how the drift started.
**Source:** explorer report 2026-09-19, items 4; WA-E punch list.

**Decision:** Honour `promptguard_threshold` on `/search` through a `None`-means-config default
rather than passing the request's `0.85`.
**Rationale:** The handler never passes a threshold today (`retrieval_app.py:1778-1815`), so the
orchestrator default wins; `/extract` reads config live (`:1704`) while `/retrieve` shadows it with
the field default — three behaviours for one key.
**Alternatives considered:** Keep `0.85` as the field default and only wire `/search` — rejected,
it leaves `config.yaml`'s key dead for two routes.
**Source:** explorer report items 6; WA-E "threshold is dead for 2 of 3 endpoints".

**Decision:** Engine-list single-sourcing is closed by the existing parity test.
**Rationale:** `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator`
(`:331-349`) fails when `searxng/config/settings.yml` and `SEARXNG_ENGINES` diverge.
**Alternatives considered:** Runtime read of the SearXNG config — rejected, the Forage image does
not ship it.
**Source:** ruling 11.

### Scope Adjustments

- The WA-E engine-duplication item was dropped from code work (ruling 11) and reduced to a
  documentation sentence in US-004.
- `blocked_domains` on `/search` (WA-E P3) was folded into this spec's parity story rather than
  spec 1's audit story, because it is a *policy* knob (caller-supplied), not a sanitization rule.

### Decisions Made

- Warn, never refuse, on unknown config keys (ruling 12).
- No pydantic validation on `blocked_domains` items; normalise in the handler (mirrors the search
  epic's ruling 29 for `providers`).

## Clarifications

### Session 2026-09-19
- Q: Should hostname matching be suffix-based everywhere or only for the blocklist? → A:
  Everywhere (ruling 8): blocklist, trust tiers and news TTL share one helper.
- Q: Does `/search` get the full `/retrieve` domain-policy surface? → A: Only `blocked_domains`;
  trust tiers stay a fetch concept.
- Q: Is the engine list a config-sourcing problem? → A: No (ruling 11) — the parity test is the
  sync mechanism; document it.

## Open Questions

- [ ] Whether `KNOWN_CONFIG_KEYS` should also carry a per-key "read by" pointer for the docs table
      (non-blocking; a later docs sweep can add it).
- [ ] Whether `blocked_domains` normalisation should share a helper with `providers`'
      normalisation in `policy.py` (non-blocking; a refactor if the two stay identical).
