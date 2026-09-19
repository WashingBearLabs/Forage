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
execution_order: [US-001, US-007, US-002, US-005, US-003, US-004, US-006]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Hostname Semantics + Config Single-Sourcing

> **Spec 3 of `epic-forage-hardening`.** Make every hostname-against-a-list comparison in the
> service mean one written-down thing — suffix matching for denylists, opt-in suffix matching for
> allowlists (ruling R8, corrected in validation round 2) — give `/search` the domain-policy and
> threshold knobs `/retrieve` already has (rulings 9, R10 corrected), and put the `config.yaml`
> surface behind one key registry so an operator typo is a WARNING and every key has a documented
> row (ruling 12). Ruling 11 records that the SearXNG engine list is *already* single-sourced by
> test. Context: WA-E punch list ("Exact-hostname matching breaks blocklists, trust tiers, and news
> TTLs", "`blocked_domains` applies to fetch but never search", "`promptguard_threshold` is dead
> for 2 of 3 endpoints", "Nine distinct config surfaces"), audit finding 2026-09-16-058, and the
> epic's rulings 5, 6, 9, 11, 12, R8, R10, R24, R31, R32, R36, R39, R40 are binding here.

## Overview

Three exact-match sites decide security-relevant things today, and all three read
`host in {lower-cased entries}`: `validate_url`'s `blocked_domains` check
(`url_validator.py:145-146`, `lower_host in lower_blocked`), `_resolve_request_trust_tier`
(`pipeline/orchestrator.py:1114`) and `_effective_ttl_hours`'s `news_domains` check
(`cache.py:176`). Blocking `evil.com` therefore does not block `www.evil.com`, and the one-hour news
TTL never applies to a `www.`-prefixed host.

The load-bearing decision is **ruling R8, and it is asymmetric by direction.** Suffix matching
*narrows* what a denylist lets through, so `blocked_domains`, the operator's `seed_blocklist` and
the private-name list match by dot-boundary suffix unconditionally. Suffix matching *widens* what
an allowlist privileges — and `trusted` is not a label, it is a bypass: `run_promptguard` returns
SAFE with `skip_reason="trusted_tier"` without classifying (`pipeline/stage3_promptguard.py:77-88`)
and stage 4 assigns a 0.95 base score. A uniformly-applied suffix rule would let one caller-supplied
entry such as `trusted_domains: ["com"]` switch the classifier off for most of the web. So bare
allowlist entries (`trusted_domains`, `verified_domains`, `news_domains`) keep **exact** matching;
an entry written with a leading dot (`.example.com`) opts into the apex plus every subdomain. The
leading-dot form is one today's exact matcher can never match, so **no existing allowlist entry
changes meaning; every existing denylist entry now also covers its subdomains** (a tightening in the
safe direction, announced in the window and in an upgrade note). Every entry in every list must
have at least two labels after any leading dot is removed; single-label, empty-label, over-long and
non-IDNA entries are ignored and counted (request lists) or dropped with one boot WARNING (config
lists) — never a 422.

Normalisation has one owner per source. Request lists are normalised in the handler, once, and
reach the pipeline through the same request replacement spec 2 US-005 introduced (R24). Config
lists (`seed_blocklist`, `news_domains`) are normalised once in the lifespan and published on
`app.state`, so the comparison sites never see a raw entry and today's inline lower-casing can be
deleted without a denylist silently weakening on upgrade. Denylists are never truncated in count:
the operator's list merges first, the caller's after, and no caller entry can evict an operator
entry. Allowlists keep a 64-entry cap because truncating an allowlist only narrows privilege.

The second decision is policy parity between the two routes. `SearchRequest` (`models.py:273`)
carries no domain list and no threshold field (verified: its fields are `query`, `num_results`,
`promptguard_fail_closed`, `providers`, `allow_paid_fallback`), `run_search_pipeline` never sees
`config['seed_blocklist']` — only `run_retrieve_pipeline` merges it (`pipeline/orchestrator.py:
247-252`) — so a blocked domain's title, URL and snippet still reach the model through search
results; and the `/search` handler never passes a threshold into `run_search_pipeline`
(`retrieval_app.py:1811-1815`), so the orchestrator's `promptguard_threshold: float = 0.85` default
(`pipeline/orchestrator.py:783`) wins regardless of `config.yaml`. Ruling 9 adds `blocked_domains`
(and the `seed_blocklist` merge) to `/search`; ruling R10 (corrected) **adds** `promptguard_threshold`
to `SearchRequest`, makes it `float | None` on both routes meaning "the server's configured
threshold", applies spec 2's operator ceiling on `/search` too, and leaves `/extract`'s own guarded
read exactly as it is.

The third is the config surface. `_load_config` is a bare `yaml.safe_load` (`retrieval_app.py:329`)
and each subsystem pulls its own keys with `.get(key, default)`, so a misspelled key is silently the
default. Ruling 12 adds a registry and a boot WARNING — never a refusal — a test that ties the
registry to `docs/configuration.md` (discovering every `config.yaml` block table, so spec 2's
`retrieve:` block is covered), and an AST sweep that ties the registry to the keys the code reads.
The engine list is out: `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator`
already pins `searxng/config/settings.yml`'s enabled engines to `SEARXNG_ENGINES`; this spec only
writes that fact down (ruling 11).

## Goals

- One hostname predicate, one entry normaliser and one host canonicaliser: `hostname_matches` is the
  only comparison used by the three sites plus the private-name list; the host side reuses spec 1
  US-003's canonicaliser (exactly one IDNA implementation across `url_validator.py` and
  `pipeline/orchestrator.py`); a parametrised test proves `evil.com` (denylist) matches `evil.com`
  and `www.evil.com` and never `notevil.com` or `evil.com.attacker.net`; that a bare `example.com`
  allowlist entry matches only `example.com`; that `.example.com` matches `example.com` and
  `a.b.example.com`; that `straße.de` and `xn--strae-oqa.de` match each other (UTS-46 via the `idna`
  package); and that `com`, `.com`, `""`, `bad..entry` and a non-IDNA entry are rejected by the
  normaliser.
- Config-sourced lists survive the change: `seed_blocklist: [" Evil.COM. "]` blocks `www.evil.com`
  and `news_domains: ["BBC.co.uk"]` still shortens the TTL for `bbc.co.uk` after the story lands;
  a malformed config entry is dropped with one boot WARNING naming the key and the count, never a
  value.
- `/search` honours `blocked_domains` and the operator's `seed_blocklist` (results omitted as
  `blocked_url`) and `promptguard_threshold` (per-request value, else `config.yaml`, then the
  operator ceiling); a request that sends neither field yields the same `results`,
  `omitted_by_reason`, `fallback_fired`, `provider_used` and `provider_errors` as the committed
  pre-story baseline.
- Zero silent config keys: every key `config.yaml` can carry is in `KNOWN_CONFIG_KEYS`, every
  registry key has a row in `docs/configuration.md`, every key the code reads is in the registry,
  an unknown key produces exactly one `config_unknown_key` WARNING naming the key (never its value),
  and a malformed top-level document never raises.
- The `/search`↔`/retrieve` boundary text cannot drift again: a test derives each route's
  route-specific **and** shared knob sets from `model_fields` and fails when a knob is missing from
  any of the seven authored copies or a shared knob is attributed to one route.
- The SearXNG engine list's sync mechanism is named in the two docs that describe the list.

## User Stories

### US-001: Hostname matching — suffix for denylists, opt-in suffix for allowlists

**Priority:** P1

**Description:** As an operator, I want `blocked_domains` and `seed_blocklist` entries to cover
their subdomains, and `trusted_domains` / `verified_domains` / `news_domains` entries to cover
subdomains only when I write them with a leading dot, so that blocking `evil.com` blocks
`www.evil.com` while trusting `example.com` never silently trusts a subdomain I do not control.
This story is the matching semantics, the three call-site replacements, the config-list
normalisation at boot and the rotation; the request-surface plumbing and the counters are US-007.

**Independent Test:** A parametrised test over `hostname_matches`, `normalize_domain_entries` and
each of the three call sites — `validate_url(..., blocked_domains=["evil.com"])` with DNS patched
as `tests/test_url_validator.py` does, `_resolve_request_trust_tier("www.example.com",
[".example.com"], [], [])` versus `(["example.com"])`, and `_effective_ttl_hours(24,
domain="www.bbc.co.uk", news_domains=[".bbc.co.uk"])` (all entries pre-normalised, as the call
sites will receive them) — proves the rules above; a lifespan test with `_load_config` patched to
`seed_blocklist: [" Evil.COM. ", "com"]` and `news_domains: ["BBC.co.uk", ".Example.ORG"]` shows
`app.state.config["seed_blocklist"] == ["evil.com"]`, `app.state.config["news_domains"] ==
["bbc.co.uk", ".example.org"]`, and exactly one WARNING per list whose `getMessage()` contains
`config_invalid_value`, the key name and `dropped=1`, and no entry text; every pre-existing
exact-match test in the four affected test modules still passes.

**Implementation Hints:**
- **Reuse spec 1's canonicaliser; add no second IDNA rule.** Spec 1 US-003 lands the public host
  canonicaliser in `url_validator.py` (intended name `canonical_host(host) -> str | None`: strip one
  trailing dot, lower, UTS-46 IDNA via the `idna` package, `None` when un-encodable; confirm the
  name from spec 1's Implementation Notes and use it). `normalize_domain_entries` applies the same
  function to each entry after removing and remembering the leading dot; `hostname_matches` applies
  it to the host. Add `idna` to `pyproject.toml`'s `dependencies` explicitly (it is already in
  `uv.lock` as httpx's transitive dependency) so `straße.de` canonicalises to `xn--strae-oqa.de`,
  never `strasse.de` (Python's built-in codec is IDNA2003 and diverges). A criterion pins that
  exactly one IDNA call site exists across the two files.
- Add to `url_validator.py` (not a `_REVISION_SOURCES` member) beside `_check_hostname_blocklist`
  (today `:95`; spec 1 will have moved it): `normalize_domain_entries(entries, *, limit: int | None)
  -> tuple[list[DomainEntry], int]` and `hostname_matches(host, entry: DomainEntry, *, allow_suffix:
  bool) -> bool`. `DomainEntry` is a small `@dataclass(frozen=True, slots=True)` (the repo's
  idiom; no `NamedTuple`) carrying `name: str` (canonical, no leading dot) and `wildcard: bool`
  (the leading-dot marker); its `str()` is the canonical spelling **with** the leading dot when
  `wildcard` is true, so the marker survives into `cache_policy_fingerprint` verbatim (a criterion
  asserts it). Normalisation order: strip → remember and remove one leading dot → `canonical_host`
  (`None` → invalid) → split on `.` and require ≥ 2 labels, **no empty label** (checked on the
  split labels — `"".encode("idna")` succeeds, so the encode step alone never catches `bad..entry`),
  total ≤ 253 chars. Invalid entries are dropped and counted in the second return value;
  `limit=None` means no cap.
- Matching: `hostname_matches(host, entry, allow_suffix=True)` is equal, or `host` ends with `"." +
  entry.name`; denylist sites pass `allow_suffix=True` unconditionally; allowlist sites pass
  `allow_suffix=entry.wildcard`. An un-canonicalisable host **fails closed at the denylist sites**:
  `validate_url` raises the existing `ValueError`-class error that `/retrieve` maps to `invalid_url`
  (`pipeline/orchestrator.py:268-284`), and stage 5 refuses the hop (`pipeline/stage5_url_audit.py:
  144`); on `/search` spec 1 US-002/US-003 already rejected it. At the two allowlist sites it simply
  matches nothing.
- The private-name list joins the same predicate (ruling R8): `_BLOCKED_HOSTNAMES = {"localhost"}`
  and `_BLOCKED_SUFFIXES` (`url_validator.py:64-65`) are matched through
  `hostname_matches(host, entry, allow_suffix=True)`, so `anything.localhost` is refused at the
  hostname stage (RFC 6761), not only by the DNS + private-IP layer.
- Replace the three comparisons: `url_validator.py:145-146` (keep raising `BlockedDomainError` with
  the same message shape; `validate_url` runs on every redirect hop — `pipeline/stage5_url_audit.py:
  144` — so a hop onto `www.blocked.example` is now refused), `pipeline/orchestrator.py:1114`
  (`blocked` still wins over `trusted` over `verified`; the resolver also returns **which entry
  matched** so US-007 can count wildcard-caused trusted skips), `cache.py:176` (`min(1, ttl_hours)`
  unchanged). All three sites receive pre-normalised `DomainEntry` sequences and never normalise
  again. `cache.py` may import from `url_validator`; `url_validator.py` must not import `cache` or
  `pipeline.orchestrator`.
- **Config-sourced lists are normalised once in the lifespan** (R8 corrected): after `_load_config`,
  run `seed_blocklist` (denylist, `limit=None`) and `news_domains` (allowlist, `limit=64`) through
  `normalize_domain_entries`, write the canonical spellings back into the published config dict
  (`app.state.config["seed_blocklist"]` / `["news_domains"]`) so `pipeline/orchestrator.py:249` and
  `:262` keep reading them through the existing `config.get` with no signature change; log one
  `logger.warning("config_invalid_value — key=%s dropped=%d", key, n)` per list with drops (tokens
  in the message, never `extra=`, never an entry). The per-request counters (US-007) do not tick
  for config entries.
- `cache_policy_fingerprint` (`cache.py:154-163`) keeps its inline `strip().lower()` as a
  key-stability normalisation; it receives already-canonical strings (with leading dots intact),
  so it is idempotent and the two can never disagree. State this in Technical Considerations.
- Contract window (ruling 5, R36): the three `RetrieveRequest` list descriptions (`models.py:249-257`)
  gain the rule ("bare entries match exactly; a leading dot covers every subdomain;
  `blocked_domains` always covers subdomains") — a description change that moves the document.
  Record the classification in `contract/GOVERNANCE.md` "Recorded rulings": the denylist widening
  is a tightening in the safe direction; the leading-dot form is additive; bare allowlist entries
  are unchanged.
- Rotation (ruling 6, R32): `pipeline/orchestrator.py` and `pipeline/contract.py` move. Read
  `kit_tools/arch/DECISIONS.md`'s rotation ADR as found — spec 1 US-001 may already have amended its
  "none changing sanitization behaviour" preamble; if the sentence is still there, amend it here
  (a `.example.com` entry skips PromptGuard for its subdomains). Record on the five-site protocol:
  `docs/bootstrap-notes.md` (next numbered heading, `before:/after:` pair), `CLAUDE.md`
  (Coexistence paragraph), `kit_tools/arch/DECISIONS.md` (table + preamble), `kit_tools/docs/
  GOTCHAS.md` (the "`sanitizer_revision` has deliberately diverged" heading — increment its
  spelled-out counter and extend its table; refer to it by heading, not line), `kit_tools/arch/
  CODE_ARCH.md` (rotation narrative) — the practice `kit_tools/EXECUTION_LOG.md`'s
  thirteenth-rotation backfill record establishes. Add a one-line comment above `hostname_matches`
  saying any change to its semantics must be paired with a rotation, because matching semantics are
  a cache-key input in substance.
- Tests (R40): `tests/test_url_validator.py` (blocklist), `tests/test_stage5_url_audit.py::
  TestBlocklistDuringFetch` (`:173`, redirect hop), new direct unit tests for
  `_resolve_request_trust_tier` (none exist — `grep -rn _resolve_request_trust_tier tests/` is
  empty; nearest `tests/test_orchestrator.py::test_retrieve_trusted_tier_loaded_classifier_reports_skipped_trusted`),
  `tests/test_cache.py::TestTTLLogic`, and the lifespan config-normalisation test on the
  `tests/test_app.py:1196-1208` config idiom.
- Docs the story owns — every site that says "exact", found by value (R39): run
  `grep -rn -iE "exact-host|exact host|exact, case-insensitive" docs kit_tools/docs kit_tools/arch
  README.md --exclude-dir=.seed_cache` at the start (nine line sites in six files today:
  `docs/configuration.md:435`, `kit_tools/docs/API_GUIDE.md:158`, `kit_tools/docs/TROUBLESHOOTING.md:
  158` and `:417`, `kit_tools/docs/ENV_REFERENCE.md:86`, `kit_tools/arch/SECURITY.md:86` and `:107`,
  `kit_tools/arch/SERVICE_MAP.md:175` and `:262`; `kit_tools/specs/**` quotes the old wording
  deliberately and is out of scope). Each states the two-direction rule with the `evil.com` /
  `www.evil.com` / `notevil.com` and `example.com` / `.example.com` examples; the
  `trusted_domains` / `verified_domains` rows carry the multi-tenant caution (never write a
  leading-dot entry for `github.io`, `s3.amazonaws.com` or another multi-tenant apex); the
  `seed_blocklist` row and the `/retrieve` `blocked_domains` description carry an **upgrade note**
  written so spec 8 can lift it (existing entries now cover subdomains; review apex entries before
  upgrading — a multi-tenant apex in a denylist removes every tenant) and the sentence that the
  list is observable through `/search`'s `blocked_url` counts and `/retrieve`'s refusal message, so
  it is policy, not a secret. `kit_tools/docs/MONITORING.md` `### Startup lines you may see`
  (`| Level | Line | When |`) gains a `WARNING` row whose Line cell names `config_invalid_value`.

**Acceptance Criteria:**
- [ ] `hostname_matches`, `normalize_domain_entries` and `DomainEntry` exist in `url_validator.py`;
      the four sites (three list sites plus the private-name list) call `hostname_matches`
      (`grep -c hostname_matches` ≥ 1 in `url_validator.py`, `pipeline/orchestrator.py` and
      `cache.py`), and the parametrised matcher test replaces the three inline expressions
      (`grep -n "lower_host in lower_blocked" url_validator.py`, `grep -n "in {d.lower()"
      pipeline/orchestrator.py` and `grep -n "in {item.lower()" cache.py` each return nothing).
- [ ] Exactly one IDNA implementation: `grep -cE "idna\.(encode|decode)|encode\(\"idna\"\)"
      url_validator.py pipeline/orchestrator.py` sums to 1, inside spec 1's canonicaliser; `idna`
      is listed in `pyproject.toml` `dependencies`.
- [ ] Parametrised matcher test covers at least: equal; `www.` prefix; deeper subdomain;
      `notevil.com`; `evil.com.attacker.net`; trailing dot on host and entry; `münchen.de` vs
      `xn--mnchen-3ya.de` and `straße.de` vs `xn--strae-oqa.de` in both directions; bare vs
      leading-dot allowlist entry; `anything.localhost` refused; and the rejected shapes `com`,
      `.com`, `""`, `bad..entry`, a 254-char entry, a non-encodable label — each dropped and counted.
- [ ] `validate_url("https://www.evil.com/x", blocked_domains=[…evil.com…])` raises
      `BlockedDomainError`; `notevil.com` does not; a redirect hop onto `www.blocked.example` under
      `blocked.example` is refused (`tests/test_stage5_url_audit.py::TestBlocklistDuringFetch`); a
      redirect hop onto an un-canonicalisable host under a non-empty blocklist is refused, never
      fetched.
- [ ] `_resolve_request_trust_tier("www.example.com", [example.com], [], [])` returns `"standard"`;
      with `[.example.com]` it returns `"trusted"`; with `blocked_domains=[example.com]` as well it
      returns `"blocked"`; `trusted_domains=["com"]` never reaches the resolver (rejected by the
      normaliser) and `a.com` is `"standard"`.
- [ ] `_effective_ttl_hours(24, domain="www.bbc.co.uk", news_domains=[bbc.co.uk])` returns `24`
      and with `[.bbc.co.uk]` returns `1`; `bbc.co.uk` bare with `domain="bbc.co.uk"` returns `1`.
- [ ] Lifespan normalisation: `seed_blocklist: [" Evil.COM. ", "com"]` and `news_domains:
      ["BBC.co.uk", ".Example.ORG"]` publish `["evil.com"]` and `["bbc.co.uk", ".example.org"]`;
      `www.evil.com` is refused on `/retrieve` and `bbc.co.uk` gets the one-hour TTL; exactly one
      `config_invalid_value` WARNING per list, naming the key and `dropped=1`, containing no entry
      text; the shipped `config.yaml` logs no such WARNING.
- [ ] `trusted_domains=["example.com"]` and `trusted_domains=[".example.com"]` produce different
      `cache_policy_fingerprint` values for the same URL (the marker reaches the fingerprint), and
      two spellings of one entry (`"Example.COM "` / `"example.com"`) produce the same value.
- [ ] Every pre-existing exact-match test in `tests/test_url_validator.py`,
      `tests/test_stage5_url_audit.py`, `tests/test_orchestrator.py` and `tests/test_cache.py`
      passes unchanged.
- [ ] `grep -rn -iE "exact-host|exact host|exact, case-insensitive" docs kit_tools/docs
      kit_tools/arch README.md --exclude-dir=.seed_cache` returns nothing (nine line sites at the
      start); the six files state the two-direction rule (`grep -c notevil.com` ≥ 1 in
      `docs/configuration.md`, `kit_tools/docs/API_GUIDE.md` and `kit_tools/arch/SECURITY.md`); the
      multi-tenant caution is on the `trusted_domains` row; the `seed_blocklist` row carries the
      upgrade note and the observability sentence; MONITORING has the `config_invalid_value` row.
- [ ] 1.3.0 window (R36): the docstring line for the three list descriptions is appended in the
      `* ``1.3.0`` — …` format; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; the three field descriptions
      appended to `_EXPECTED_ONE_THREE_ZERO_DIFF` in `tests/test_contract_schema.py`; the four
      anchor-quoting pages refreshed; `uv run python -m scripts.export_contract --check` green;
      `tests/golden/contract_1_2_0.json` unchanged; the GOVERNANCE ruling recorded.
- [ ] The `sanitizer_revision` rotation (`pipeline/orchestrator.py`, `pipeline/contract.py`) is
      measured by revert-and-reproduce and recorded at the five sites (`docs/bootstrap-notes.md`,
      `CLAUDE.md`, `kit_tools/arch/DECISIONS.md` table and preamble as found,
      `kit_tools/docs/GOTCHAS.md` counter and table, `kit_tools/arch/CODE_ARCH.md`).
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-007: Request domain lists normalised once per request, with the `policy_invalid_domain_entry` and `policy_suffix_trusted_skip` counters

**Priority:** P1

**Description:** As a consumer, I want `/retrieve`'s three request lists normalised once in the
handler under a written-down cap rule, and as an operator I want to see dropped entries and
wildcard-caused classifier skips on `/metrics`, so that a truncated or malformed list is observable
and a leading-dot trust entry's blast radius is measurable rather than asserted.

**Independent Test:** A `/retrieve` request whose `trusted_domains` holds 70 entries including `"com"`
and `" .Example.COM. "` applies exactly 64 valid normalised entries and `/metrics`
`retrieve.policy_invalid_domain_entry` advances by the dropped count (invalid plus over-cap), with
no 422; a `/retrieve` request carrying 500 valid junk `blocked_domains` entries plus a
`seed_blocklist` domain in config still refuses that domain (the denylist is uncapped and the
operator list merges first); a `/retrieve` for `www.example.com` with `trusted_domains:
[".example.com"]` and a loaded classifier reports `promptguard_state == "skipped_trusted"` and
advances `retrieve.policy_suffix_trusted_skip` by one, while `["www.example.com"]` advances it by
zero; `/metrics` serves `search.policy_invalid_domain_entry` and `search.policy_suffix_trusted_skip`
at `0` before US-002 lands.

**Implementation Hints:**
- `/retrieve`'s three lists (`models.py:249-257`) are normalised in the handler and reach the
  pipeline through the **single** `body.model_copy(update={...})` replacement spec 2 US-005
  introduced for the resolved policy values (R24; `feature-hardening-retrieve-parity.md`, US-005
  hints) — one replacement carrying the normalised lists and the resolved threshold/fail-closed,
  never a second. `run_retrieve_pipeline` takes only `(request, cache, classifier, config,
  sanitizer_revision)` (`pipeline/orchestrator.py:248-254`) and reads the lists off the request at
  `:248`, `:254-255` and `:363-364`; no new kwarg.
- Cap rule (R8 corrected): `blocked_domains` → `normalize_domain_entries(entries, limit=None)`
  (uncapped in count; each entry is still bounded at 253 chars and the body by the request size);
  `trusted_domains` / `verified_domains` → `limit=64`. The merge in `run_retrieve_pipeline`
  (`:247-252`) becomes operator-first: `seed_blocklist` (already canonical, US-001) then the
  caller's list; a criterion proves no caller entry can evict an operator entry.
- Counters: `RetrieveMetrics` and `SearchMetrics` are plain `__init__` counter classes
  (`retrieval_app.py:875`, `:903` — not dataclasses); each gains `policy_invalid_domain_entry` and
  `policy_suffix_trusted_skip`; `RetrieveMetricsResponse` (`:538`) and `SearchMetricsResponse` (`:488`,
  both `extra="forbid"`) gain the matching `Field(description=...)`; the `/metrics` handler dict
  lines (search section ~`:1524`, retrieve section beside it) gain both. The guards that prove the
  three edits move together are `tests/test_contract_metrics.py::test_an_unmodeled_counter_fails_loudly`
  (`:229`) and `::test_served_metrics_are_the_handlers_dict_serialized` (`:135`) — **not** the
  dataclass-parity test at `:305`, which covers only `CacheMetrics`/`ModelMetrics`. `search.*` is
  added now (US-002 increments `policy_invalid_domain_entry`; `search.policy_suffix_trusted_skip`
  stays `0` — `/search` has no trust tiers — and is documented as reserved for parity) so the
  `/metrics` document moves once for this concern.
- `policy_suffix_trusted_skip` increments when `run_promptguard`'s trusted-tier skip was caused by a
  wildcard (leading-dot) entry: US-001's `_resolve_request_trust_tier` returns the matching
  `DomainEntry`; the pipeline passes `entry.wildcard` into the metrics increment beside the existing
  `promptguard_state` accounting. One counter conflates "invalid" and "over-cap" drops
  deliberately: both narrow an allowlist (safe) and denylists never truncate; say so in the
  MONITORING row.
- Contract window (R36): one docstring line for the four counters; MONITORING `### retrieve` and
  `### search` counter tables gain rows on the `policy_unknown_provider` row's shape (`:150`):
  per-entry unit (one increment per dropped entry, not per request), the allowlist cap as a cause,
  denylists uncapped, the offending entry never stored; the suffix-skip row states what it measures.
- Rotation (R32): `pipeline/orchestrator.py` (the merge order and the wildcard flag) and
  `pipeline/contract.py` (docstring line) move; record on the five-site protocol.
- Tests (R40): `tests/test_app.py` `/retrieve` handler tests (`:1523`, `:1593`, `:1734` region);
  `tests/test_contract_metrics.py`; `tests/test_orchestrator.py` trust-tier test at the name above.

**Acceptance Criteria:**
- [ ] The handler normalises all three lists and passes them through the one `model_copy`
      replacement (`grep -c "model_copy(update=" retrieval_app.py` shows one site in the `/retrieve`
      handler); the pipeline never normalises.
- [ ] 70 `trusted_domains` entries → 64 valid normalised entries applied, `retrieve.policy_invalid_domain_entry`
      advanced by the dropped count, no 422; the same for `verified_domains`.
- [ ] 500 valid junk `blocked_domains` plus a config `seed_blocklist` domain → the seed domain is
      refused and all 500 caller entries are enforced (`grep -n "limit=None" retrieval_app.py`
      shows the denylist call); the merge in `pipeline/orchestrator.py` lists the operator entries
      first.
- [ ] `retrieve.policy_suffix_trusted_skip` advances by one for a wildcard-caused trusted skip and by
      zero for an exact-entry skip; `promptguard_state` is `skipped_trusted` in both.
- [ ] All four counters exist on the counter classes, the response models and the handler dicts;
      `test_an_unmodeled_counter_fails_loudly` and `test_served_metrics_are_the_handlers_dict_serialized`
      pass; `GET /metrics` serves the `search.*` pair at `0`.
- [ ] MONITORING has the four rows with the per-entry unit and the cap/uncapped statement.
- [ ] 1.3.0 window (R36): docstring line appended; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; the four counters appended
      to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four anchor-quoting pages refreshed; `--check` green;
      `contract_1_2_0.json` unchanged.
- [ ] The `sanitizer_revision` rotation (`pipeline/orchestrator.py`, `pipeline/contract.py`) is
      measured and recorded at the five sites.
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
returning results on `a.example`, `www.blocked.example` and `blocked.example` (`num_results` set so
`fetch_limit = min(num_results * 2, 20)` covers all three): with `{"blocked_domains":
["blocked.example"]}` the response carries one result and `omitted_by_reason.blocked_url == 2`,
`fallback_fired is False`, the paid fake's `calls == []`, and one `search_url_blocked` WARNING with
`host_class=policy_blocklist` per omission; with no request field and `_load_config` patched to
carry `seed_blocklist: [" Blocked.Example. "]` the same two results are omitted; with neither, the
response's `results`, `omitted_by_reason`, `fallback_fired`, `provider_used` and `provider_errors`
equal the committed pre-story baseline's.

**Implementation Hints:**
- `SearchRequest.blocked_domains: list[str] = Field(default_factory=list, ...)` beside
  `providers`. **No pydantic `pattern`, `max_length` or item validation** — a FastAPI 422 would echo
  the caller's bytes (search-epic ruling 29). The handler runs `normalize_domain_entries(...,
  limit=None)` (US-001/US-007) and increments `SearchMetrics.policy_invalid_domain_entry` by the
  dropped count.
- Merge the operator list first: `run_search_pipeline` already receives `config=`
  (`retrieval_app.py:1815`, signature `pipeline/orchestrator.py:781`); read the canonical
  `config.get("seed_blocklist", [])` (US-001 normalised it at boot), then the request list, into
  one `effective_blocklist`; thread the request list in as `blocked_domains: Sequence[DomainEntry]
  = ()`. No caller entry can evict an operator entry (criterion).
- In the per-result loop, immediately after `_canonicalize_search_url` yields `domain` (already
  canonical from spec 1 US-003 — do not re-canonicalise) and after spec 1's audit, test
  `any(hostname_matches(domain, e, allow_suffix=True) for e in effective_blocklist)` and omit with
  `contract.OMIT_BLOCKED_URL` (spec 1 US-004's token; assert `OMISSION_REASONS` carries it before
  starting) through the reason channel spec 1 US-002 added to the canonicaliser's return.
  Sufficiency for fallback is judged on raw provider results before this omission (search-epic
  ruling 17). Log through spec 1 US-003's content-free `search_url_blocked — host_class=%s` line
  with a new token `policy_blocklist`, so logs distinguish "SearXNG returned 169.254.169.254" from
  "the operator blocked reddit.com".
- Baseline: before touching the handler, capture `tests/fixtures/search/baseline_pre_blocked_domains.json`
  from the current code for the three fake results at threshold `0.85`, holding only the five
  fields the story can disturb (`results`, `omitted_by_reason`, `fallback_fired`, `provider_used`,
  `provider_errors`); add a `tests/fixtures/README.md` section (when captured, from which commit,
  the three fake results, the threshold, and that it is deliberately **not** regenerable after this
  story). Whole-response equality is not asserted — later window stories add fields.
- Contract window (R36): one docstring line for `blocked_domains`; the field description states the
  semantics; the seven boundary-text copies that name `blocked_domains` as `/retrieve`-only are
  updated for this knob (US-004 later pins all seven by test). Find them by value (R39):
  `grep -rn "documents today's divergence" models.py retrieval_app.py kit_tools/docs/API_GUIDE.md
  docs/configuration.md README.md` (seven hits today).
- Docs: `kit_tools/docs/API_GUIDE.md` `/search` request table gains the row;
  `kit_tools/docs/MONITORING.md`'s `omitted_by_reason` row lists `blocked_url` as reachable from the
  URL audit, `blocked_domains` and `seed_blocklist`, and the "a rising `blocked_url` count …
  warrants investigation" sentence spec 1 US-003 wrote is rewritten to name all three causes and
  the `host_class` token that separates them; `docs/configuration.md`'s `seed_blocklist` row and
  `kit_tools/arch/SECURITY.md:86` say "merged into both routes"; `kit_tools/arch/SERVICE_MAP.md:175`'s
  claim that the seed list is "also applied to `/search` result URLs" becomes true — leave it and
  cite it in the Implementation Notes.
- Rotation (ruling 6, R32): `pipeline/orchestrator.py` and `pipeline/contract.py` move; record on
  the five-site protocol named in US-001.

**Acceptance Criteria:**
- [ ] `SearchRequest.blocked_domains` exists, defaults to `[]`, carries no pydantic validation; a
      test sends 70 entries including `" BLOCKED.example. "` and `"com"` and asserts the match
      fires, `search.policy_invalid_domain_entry` advances by the dropped count (one, for `com`), and
      no 422 occurs; 500 valid caller entries plus a `seed_blocklist` domain still omit the seed
      domain.
- [ ] A result whose `domain` matches a request entry or a `seed_blocklist` entry is omitted with
      `blocked_url`, counted in `omitted_by_reason`, after URL canonicalisation and before stage 2/3;
      `fallback_fired` is `False` and the paid fake's `calls == []`; each omission logs
      `search_url_blocked` with `host_class=policy_blocklist` and no URL.
- [ ] The committed baseline's five fields equal the response's for a request sending no
      `blocked_domains` and no `promptguard_threshold`; `tests/fixtures/README.md` has the section.
- [ ] 1.3.0 window (R36): docstring line appended; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; `blocked_domains` appended
      to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four anchor-quoting pages refreshed; `--check` green;
      `contract_1_2_0.json` unchanged; the seven boundary-text copies no longer call
      `blocked_domains` a `/retrieve`-only knob.
- [ ] `kit_tools/docs/API_GUIDE.md` has the `/search` row (`grep -c blocked_domains
      kit_tools/docs/API_GUIDE.md` ≥ 2); MONITORING's `omitted_by_reason` row names the three causes
      and the token; `docs/configuration.md`'s `seed_blocklist` row says both routes.
- [ ] The `sanitizer_revision` rotation is measured and recorded on the five-site protocol.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-005: `promptguard_threshold` added to `/search`, defaulted from `config.yaml` on both routes, capped by the operator ceiling

**Priority:** P1

**Description:** As an operator, I want `config.yaml`'s `promptguard_threshold` to be the default
on every route, a caller's value to apply to `/search` as it does to `/retrieve`, and spec 2's
operator ceiling to bound both, so that tightening the threshold is one edit, loosening it is
bounded, and search is not scanned at a number nobody configured.

**Independent Test:** With `_load_config` patched to carry `promptguard_threshold: 0.5` and a
mocked classifier scoring `0.6`: `POST /search` omitting the field omits every result as
`injection_detected`, an explicit `0.85` serves them; `POST /retrieve` omitting the field blocks,
an explicit `0.85` serves; with config `promptguard_threshold: 0.85` and
`promptguard_threshold_ceiling: 0.5`, a request omitting the field on either route classifies at
`0.5`, reports `effective_promptguard_threshold == 0.5`, and the `/retrieve` cache fingerprint
carries `0.5`; `/extract` behaves exactly as before (its own guarded read at
`retrieval_app.py:1703-1713` is untouched); a config carrying `promptguard_threshold: "abc"` or
`1.7` boots, logs one `config_invalid_value — key=promptguard_threshold` WARNING, and resolves to
`0.85`; a YAML-quoted `"0.85"` boots without a WARNING and resolves to `0.85`.

**Implementation Hints:**
- **Add** `promptguard_threshold: float | None = Field(default=None, ge=0.0, le=1.0, ...)` to
  `SearchRequest` (`models.py:273`; verified absent today) and change `RetrieveRequest`'s
  (`models.py:258`) from `float = 0.85` to the same; `tests/test_models.py:194` asserts the old
  `0.85` default and is updated (R40).
- Resolution lives in the operator-policy resolver spec 2 **US-005** added to `retrieval_app.py`
  (locate it by `promptguard_threshold_ceiling`; it is R24's "resolve once, in the handler, by
  replacing the request" via `body.model_copy(update={...})`). Order, pinned by a criterion:
  requested `None` → the validated config default **first**, then `min(value, ceiling)` — the
  operator's own default is subject to the operator's own ceiling, so omitting the field can never
  select a looser policy than sending it. Both routes call the same resolver; `/search`'s resolved
  value is passed as the existing `run_search_pipeline(promptguard_threshold=...)` keyword
  (`pipeline/orchestrator.py:783`) and `SearchResponse` gains `effective_promptguard_threshold`
  (this story's `/search` half of R10; spec 2 US-005 shipped the `/retrieve` half). `/extract`
  keeps its own read and its `UnsupportedFormatError` mapping (ruling R10) — do not route it
  through the resolver.
- **Single source, strict types.** After this story `request.promptguard_threshold` is read
  nowhere inside `pipeline/orchestrator.py`: the two `/retrieve` reads (`:257` into
  `cache_policy_fingerprint`, whose parameter is `promptguard_threshold: float` at `cache.py:125`,
  and `:372` into `sanitize_and_structure`) become the resolved keyword spec 2 US-005 passes into
  `run_retrieve_pipeline` (if spec 2 landed them as keyword parameters this is already true; if it
  landed only the `model_copy`, this story adds the keyword). `float | None` at a `float` parameter
  is a pyright-strict error with no escape hatch (`pyproject.toml:97` `enableTypeIgnoreComments =
  false`, asserted by `tests/test_pyright_policy.py:112`), so the narrowing must happen in the
  handler, not by a type-ignore.
- Config validation at boot: `promptguard_threshold_from_config(config) -> float` in
  `retrieval_app.py` beside the resolver: accepts `int`/`float` or a `float()`-coercible string
  (what `/extract`'s guard accepts today, `:1703-1713`), requires `0.0 ≤ v ≤ 1.0`, absent → `0.85`;
  anything else logs `config_invalid_value — key=promptguard_threshold` (key only, never the value)
  and returns `0.85` — **never refuses boot** (R10 corrected: a value that boots today must still
  boot). Publish the validated default on `app.state.promptguard_threshold_default`; the resolver
  never re-reads `config`. The raw config value is left in place, so `/extract`'s guard and
  `pipeline/sanitizer_revision.py:41`'s hash input are unchanged. Log the resolved default once at
  boot, `promptguard_threshold_resolved — value=%s` (not a credential; invariant 5 wants a
  route-wide default change loud).
- The cache fingerprint (`cache.py:120`) receives the *resolved* threshold, never `None`; assert the
  same URL fetched with `null`, with an explicit value equal to the config default, and with the
  ceiling applied produces the expected keys.
- Contract window (R36): one docstring line ("`promptguard_threshold` added to `SearchRequest`;
  `null` means the server's configured threshold on both routes; the operator ceiling applies on
  `/search`; `effective_promptguard_threshold` on `SearchResponse`"); update the boundary-text copies
  that say `/search` has no per-request threshold — find them by value: `grep -rn -i "no per-request
  threshold\|not applied on this route" kit_tools/docs/API_GUIDE.md docs/configuration.md models.py
  retrieval_app.py README.md`. GOVERNANCE "Recorded rulings" gains the classification: the
  `/search` field is additive; the `/retrieve` default's change from the literal `0.85` to "the
  configured default, shipped as `0.85`" is MINOR because a 1.2.0 client on the shipped config sees
  identical behaviour, and the visible change is bounded to operators who tuned the key.
- Upgrade note (`docs/configuration.md` `promptguard_threshold` row, written so spec 8 can lift it),
  **both directions**: an operator who raised the key above `0.85` (to quiet `/extract` false
  positives on their own uploads) now **loosens** injection blocking on `/retrieve` and `/search`
  unless `promptguard_threshold_ceiling` bounds it; an operator below `0.85` tightens them; the
  per-route knob is gone and the content cache re-keys.
- Rotation (ruling 6, R32): `pipeline/contract.py` moves (docstring line); `pipeline/orchestrator.py`
  moves if the two reads are replaced here — measure and record either way.
- Tests (R40): `tests/test_models.py:194`; the `/search` handler tests in `tests/test_app.py`
  (`grep -n "run_search_pipeline" tests/test_app.py` for the patched calls that must now receive
  the resolved threshold); `tests/test_orchestrator.py` `/retrieve` tests that construct
  `RetrieveRequest` with a threshold.

**Acceptance Criteria:**
- [ ] `promptguard_threshold` is `float | None` defaulting to `None` on both request models (new on
      `SearchRequest`); `tests/test_models.py`'s default assertion is updated.
- [ ] `/search` passes the resolved threshold into `run_search_pipeline`; with config `0.5` and a
      `0.6`-scoring classifier, omitting the field blocks on `/search` and `/retrieve`, an explicit
      `0.85` serves; `/extract`'s tests pass unchanged.
- [ ] Ceiling order: config `0.85` + ceiling `0.5` + omitted field → classification at `0.5`,
      `effective_promptguard_threshold == 0.5` on both responses, `0.5` in the `/retrieve` fingerprint.
- [ ] `grep -n "request.promptguard_threshold" pipeline/orchestrator.py` returns nothing; `uv run
      pyright` passes with no type-ignore comment added.
- [ ] A config `promptguard_threshold` of `"abc"`, `-0.1` or `1.7` boots, logs exactly one
      `config_invalid_value` WARNING naming the key and never the value, and resolves to `0.85`; a
      quoted `"0.85"` resolves to `0.85` with no WARNING; an absent key resolves to `0.85`; the
      resolved default is logged once as `promptguard_threshold_resolved`.
- [ ] The cache-key assertions (null vs explicit default vs ceiling) pass.
- [ ] 1.3.0 window (R36): docstring line appended; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; the new field and the
      `SearchResponse` field appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four anchor-quoting
      pages refreshed; `--check` green; the by-value grep for "no per-request threshold" / "not
      applied on this route" returns nothing; the GOVERNANCE ruling recorded.
- [ ] `docs/configuration.md`'s `promptguard_threshold` row carries the two-direction upgrade note
      and names `promptguard_threshold_ceiling` as the bound.
- [ ] The `sanitizer_revision` rotation is measured and recorded on the five-site protocol.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: `config.yaml` key registry — warn on unknown keys, docs and code parity by test

**Priority:** P2

**Description:** As an operator, I want a misspelled `config.yaml` key to be a boot WARNING naming
the key, and every key the service reads to have a documented row, so that a typo never silently
selects a default and the reference never lags the code.

**Independent Test:** With `_load_config` monkeypatched (the `tests/test_app.py` lifespan idiom)
to return the shipped config plus `promtguard_threshold: "SENTINEL-VALUE"`, `extraction:
{max_pagse: 1}` and `retrieve: {max_promptguard_chnuks: 1}`, the lifespan starts, `caplog` holds
exactly three WARNING records whose `getMessage()` contains `config_unknown_key` and the offending
dotted key, `SENTINEL-VALUE` appears nowhere in `caplog.text`, and `/health` is unaffected; a
top-level YAML list, and a `cache: "yes"` block value, each start the service with no
`config_unknown_key` WARNING and no exception; a docs-parity test asserts `KNOWN_CONFIG_KEYS`
equals the first-column keys of every `config.yaml` table `docs/configuration.md` carries
(discovered by heading) and is a superset of the shipped `config.yaml`'s keys; an AST sweep asserts
every string-literal key the readers pass to `config.get(...)` / `config[...]` is in the registry.

**Implementation Hints:**
- `KNOWN_CONFIG_KEYS: frozenset[str]` of dotted names next to `_load_config` (`retrieval_app.py:329`).
  Members: every top-level key (`user_agents`, `news_domains`, `seed_blocklist`,
  `promptguard_threshold`, `promptguard_fail_closed_floor`, `promptguard_threshold_ceiling`,
  `search_brave_*`, `extract_route_enabled`, …), every bare block name (`cache`, `extraction`,
  `retrieve` — spec 2's block — and any later one), and the dotted leaves the readers consume
  (`cache_settings_from_config` `cache.py:~267`, `extraction_settings_from_config`
  `pipeline/extraction_limits.py:98-151`, `brave_settings_from_config`
  `pipeline/search_providers/brave.py:245`, spec 2's `retrieve` reader). Later specs append their
  keys (spec 4's `cache.max_value_bytes`, spec 5's `search_searxng_*`, spec 6's envelope keys, spec 7's
  contiguity keys) — say so in a comment.
- `_warn_unknown_config_keys(config: object) -> list[str]`: if `config` is not a `dict`, return
  `[]`; **data-driven blocks** — for every registered bare block name whose value is a mapping,
  walk one level and check `block.leaf`; a registered block whose value is not a mapping (`cache:
  "yes"`, `extraction: []`) is skipped with no WARNING and no exception (its own reader refuses boot
  with its typed error); an unknown top-level block → one WARNING for the block name, its children
  not walked. Log `logger.warning("config_unknown_key — key=%s", dotted)` per unknown key — the key
  name only, never the value (CLAUDE.md invariant 6); tokens in the message, never `extra=`
  (`kit_tools/arch/patterns/LOGGING.md`); call it in the lifespan right after `_load_config`. Never
  raise, never exit. A known key with a bad *value* still refuses boot through its reader's typed
  error (`ExtractionConfigurationError`, `CacheConfigurationError`) — except the keys this epic
  made warn-and-fall-back (`promptguard_threshold`, the two domain lists); the docs paragraph
  states that split.
- Docs-parity test in `tests/test_contract_metrics.py` (the module `kit_tools/testing/
  TESTING_GUIDE.md:279` maps `docs/configuration.md` to; it already holds `_CONFIGURATION_DOC`
  (`:59`) and `_posture_section()` (`:379`)); reuse `tests/test_governance_docs.py::_section`
  (`:128`, fence-aware, `startswith` + exactly-one-heading assert) and `_cells` (`:158`) by import.
  Discover the tables under `## `config.yaml``: `### Top-level keys` plus every heading matching
  ``### The `<name>:` block`` (quote headings **with their backticks** — today `### The `cache:`
  block` at `:445` and `### The `extraction:` block` at `:471`; spec 2 adds `retrieve:`); **first
  cell only**; block-table keys are prefixed `<name>.`; the top-level table's block rows register as
  bare names; assert every sliced table is non-empty. Model the shipped-config assertion on
  `tests/test_cache.py::TestCacheSettings::test_the_shipped_config_yaml_pins_the_documented_defaults`.
- Code-parity sweep (the repo's idiom for hand-maintained vocabularies): model on
  `tests/test_contract_errors.py:188 _swept_error_codes` — walk the AST of `retrieval_app.py`,
  `cache.py`, `pipeline/extraction_limits.py`, `pipeline/search_providers/brave.py`,
  `pipeline/orchestrator.py`, `pipeline/sanitizer_revision.py` and spec 2's retrieve reader,
  collecting every string-literal first argument of `config.get(...)` / `config[...]` and of the
  bounded-read helpers (`_bounded_int`-style calls whose first argument is the key), and assert the
  set is a subset of `KNOWN_CONFIG_KEYS` (twelve read sites today, e.g. `cache.py:259,274`,
  `retrieval_app.py:1703`, `pipeline/orchestrator.py:249,262,307`).
- Docs: `docs/configuration.md` gains a paragraph under `## `config.yaml`` stating the WARNING, that
  unknown keys are ignored, and that a known key with an invalid value refuses boot through its
  reader (naming the two error types) except the warn-and-fall-back keys; `kit_tools/docs/MONITORING.md`
  `### Startup lines you may see` (`:229`, columns `| Level | Line | When |`) gains a row whose
  Level cell is `WARNING` and whose Line cell names `config_unknown_key`; `kit_tools/arch/patterns/
  LOGGING.md`'s inventory gains the marker; `kit_tools/docs/TROUBLESHOOTING.md` gains a narrative
  `### config_unknown_key` note (it has no marker table — its tables are wire error codes).
- `retrieval_app.py` and the tests are not hashed; this story rotates nothing (verify
  `derive_sanitizer_revision({})` is unchanged after the story).

**Acceptance Criteria:**
- [ ] `KNOWN_CONFIG_KEYS` is a frozenset of dotted key names (plus the bare block names, `retrieve`
      included) in `retrieval_app.py`; a test asserts it is a superset of every key in the shipped
      `config.yaml`.
- [ ] The docs-parity test discovers every `config.yaml` table by heading (backticks verbatim),
      asserts each slice is non-empty, and asserts `KNOWN_CONFIG_KEYS` equals the union of their
      first-column keys with the prefixing rule (a key without a row, or a row without a key, is
      red).
- [ ] The AST sweep over the reader modules asserts every literal config key read is in the registry.
- [ ] Booting with an unknown top-level key, an unknown `extraction.` key and an unknown `retrieve.`
      key logs exactly one WARNING per key whose `getMessage()` contains `config_unknown_key` and the
      dotted key and never the value; the service starts; `/health` status is unchanged.
- [ ] Booting with a `config.yaml` whose top level is a list or a scalar, or whose `cache:` value
      is not a mapping, starts the service, logs no `config_unknown_key` record and raises nothing
      from `_warn_unknown_config_keys`.
- [ ] Booting with the shipped `config.yaml` logs no `config_unknown_key` record.
- [ ] `docs/configuration.md`'s `## `config.yaml`` section states the warn-and-ignore rule and the
      bad-value split; MONITORING's startup-lines table has the `WARNING` / `config_unknown_key`
      row; LOGGING's inventory lists it; TROUBLESHOOTING has the `### config_unknown_key` section.
- [ ] `docs/bootstrap-notes.md`'s latest `sanitizer_revision` record still matches
      `derive_sanitizer_revision({})`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: Boundary-text knob-parity guard across all seven copies

**Priority:** P2

**Description:** As a maintainer, I want the seven copies of the `/search`↔`/retrieve` boundary
text to be checked against the request models — route-specific knobs and shared knobs alike — so
that the knobs this spec added (and any later one) cannot be missing from, or misattributed in,
the prose a consumer reads.

**Independent Test:** A test in `tests/test_contract_export.py` derives `_ROUTE_SPECIFIC =
(SearchRequest.model_fields.keys() ^ RetrieveRequest.model_fields.keys()) - _IDENTITY_FIELDS` and
`_SHARED = (SearchRequest.model_fields.keys() & RetrieveRequest.model_fields.keys()) -
_IDENTITY_FIELDS` with `_IDENTITY_FIELDS = {"url", "query"}`, and asserts every name in both sets
appears backticked in all four `contract/openapi.yaml` strings (`paths['/search'].post.description`,
`paths['/retrieve'].post.description`, `components.schemas.SearchRequest.description`,
`components.schemas.RetrieveRequest.description`) and in the fenced boundary region of
`kit_tools/docs/API_GUIDE.md`, `docs/configuration.md` and `README.md`, and that each `_SHARED`
name appears in the sentence that marks knobs as shared; temporarily removing one name from each
set in one copy makes it fail twice.

**Implementation Hints:**
- The seven copies (US-002 and US-005 already touched them for their own knobs): `models.py:235`
  and `:287` (class docstrings → `components.schemas.*.description`), `retrieval_app.py:1588` and
  `:1792` (handler docstrings → operation descriptions), `kit_tools/docs/API_GUIDE.md:216-225`,
  `docs/configuration.md:28-37`, `README.md:58`. The existing guard
  `tests/test_contract_export.py::test_search_and_retrieve_descriptions_name_the_boundary`
  (`:181`) checks only that each names the other route; add the sibling beside it, reading
  `contract/openapi.yaml` through the same `CONTRACT_PATH`.
- **Stable anchors, not a content heuristic**: "the paragraph containing `/retrieve` and `/search`"
  matches four paragraphs in `README.md`, seven in `API_GUIDE.md` and five in
  `docs/configuration.md`. Wrap each Markdown copy in HTML comment fences
  `<!-- boundary-text:start -->` / `<!-- boundary-text:end -->` and slice between them; the test
  asserts exactly one fenced region per file (zero or two is red), the way
  `tests/test_governance_docs.py::_section` asserts exactly one heading.
- After US-002 and US-005 the sets are: `/retrieve`-only `cache_ttl_hours`, `extract_mode`,
  `trusted_domains`, `verified_domains`; `/search`-only `allow_paid_fallback`, `num_results`,
  `providers`; shared `blocked_domains`, `promptguard_threshold`, `promptguard_fail_closed` —
  verify from `model_fields` at implementation time rather than from this list. Rewrite all seven
  copies to enumerate the route-specific knobs by backticked name and to name the shared ones in
  one "shared by both routes" sentence (there is no `ttl_hours` field; it is `cache_ttl_hours`).
- The four code copies feed the OpenAPI document (descriptions only): regenerate and re-create the
  1.3.0 golden (ruling 5); no docstring-entry line for a description-only change; `--check` green;
  the four anchor-quoting pages refreshed.
- `models.py`, `retrieval_app.py` and the tests are not hashed; this story rotates nothing.

**Acceptance Criteria:**
- [ ] The new test derives both sets from `model_fields` minus the named identity fields and asserts
      every name in the four OpenAPI strings and the three fenced Markdown regions, plus each shared
      name inside the shared-knobs sentence; deleting one route-specific name and one shared name
      from any copy (and regenerating where applicable) makes it fail.
- [ ] Each of the three Markdown files has exactly one `boundary-text:start` / `:end` fence pair
      (`grep -c "boundary-text:start"` returns 1 for each); the test is red on zero or two.
- [ ] All seven copies name every route-specific knob and mark the shared ones; the existing
      `test_search_and_retrieve_descriptions_name_the_boundary` still passes.
- [ ] `contract/openapi.yaml` regenerated; `tests/golden/contract_1_3_0.json` re-created;
      `uv run python -m scripts.export_contract --check` green; the four anchor-quoting pages refreshed.
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

**Independent Test:** `grep -o test_enabled_engines_match_the_orchestrator docs/searxng.md | wc -l`
and the same for `kit_tools/arch/SERVICE_MAP.md` each return at least `1`; the full suite is green
(`SERVICE_MAP.md` is one of `tests/test_governance_docs.py`'s anchor-quoting pages).

**Implementation Hints:**
- `docs/searxng.md` `## Engines` gains one sentence: the enabled set in
  `searxng/config/settings.yml` and `SEARXNG_ENGINES` in `pipeline/search_providers/searxng.py` are
  kept in step by `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator`, which
  is the sync mechanism — there is no runtime read of the SearXNG config.
- `kit_tools/arch/SERVICE_MAP.md:106` already says "Engine parity with `SEARXNG_ENGINES` is asserted
  by `tests/test_searxng_docker.py`"; extend that existing sentence with the test function name
  rather than adding a second claim of the same fact.
- Doc-only story; nothing rotates, no code.

**Acceptance Criteria:**
- [ ] `docs/searxng.md` and `kit_tools/arch/SERVICE_MAP.md` each name
      `test_enabled_engines_match_the_orchestrator` as the engine-list sync mechanism (occurrence
      count ≥ 1 by `grep -o … | wc -l`); `SERVICE_MAP.md:106`'s sentence is extended, not duplicated.
- [ ] Full test suite passes (`uv run pytest`).

## Edge Cases

- Denylist entry `evil.com`, host `evil.com.attacker.net` → no match (suffix at a dot boundary,
  at the end). US-001.
- Allowlist entry `example.com`, host `www.example.com` → no match (bare entries stay exact);
  entry `.example.com` → match; entry `.example.com`, host `example.com` → match (apex included).
  US-001.
- Entry `com`, `.com`, `co` — fewer than two labels after the leading dot is removed → rejected by
  the normaliser (request lists: counted; config lists: one boot WARNING); `co.uk` bare matches only
  the host `co.uk`; `.co.uk` is a deliberate operator opt-in covering every `.co.uk` host (the docs
  caution names this). US-001.
- Empty, whitespace-only, `bad..entry` (caught on the split labels, not by the encode step),
  254-character, or non-IDNA-encodable entry → rejected; never a 422. US-001, US-007, US-002.
- Host `straße.de` versus entry `xn--strae-oqa.de` (and the reverse), `münchen.de` versus
  `xn--mnchen-3ya.de` → match after UTS-46 canonicalisation; an un-canonicalisable host is refused
  at the denylist sites (`invalid_url` on `/retrieve`, the hop refused in stage 5) and matches
  nothing at the allowlist sites. US-001.
- Trailing dots on host or entry → stripped, match. `anything.localhost` → refused at the hostname
  stage. US-001.
- A host on both `blocked_domains` and `trusted_domains` → `blocked` (existing precedence). US-001.
- A redirect hop onto a subdomain of a blocked entry → refused at that hop
  (`pipeline/stage5_url_audit.py:144`). US-001.
- Config `seed_blocklist: [" Evil.COM. "]` / `news_domains: ["BBC.co.uk"]` → normalised at boot;
  still match; an invalid config entry → dropped with one `config_invalid_value` WARNING per list
  (count, never text). US-001.
- More than 64 entries in an **allowlist** → the first 64 valid entries apply; the rest are counted
  under `policy_invalid_domain_entry`; no 422. A denylist of any length → every valid entry applies;
  the operator's list is merged first and can never be evicted. US-007 (`/retrieve`), US-002
  (`/search`).
- `trusted_domains=[".example.com"]` and `["example.com"]` for the same URL → different cache keys
  (the marker reaches the fingerprint). US-001.
- A wildcard-caused trusted skip → `promptguard_state == "skipped_trusted"` and
  `policy_suffix_trusted_skip += 1`; an exact-entry skip → the same state, counter unchanged. US-007.
- `seed_blocklist` entry matching every search result → 200 with an empty list and
  `omitted_by_reason.blocked_url == n`; no fallback, no paid call. US-002.
- `promptguard_threshold: null` sent explicitly → identical to omitting it; config default above
  the ceiling with the field omitted → the ceiling applies (never a looser policy by omission).
  US-005.
- `config.yaml` lacking `promptguard_threshold` → `0.85`; a quoted `"0.85"` → `0.85`; a non-numeric
  or out-of-range value → boots, one `config_invalid_value` WARNING, `0.85`; `/extract`'s own guard
  is unchanged and still reachable. US-005.
- Unknown key inside `cache:`, `extraction:` or `retrieve:` → dotted WARNING; an unknown top-level
  block → one WARNING for the block name, its children not walked; a registered block whose value is
  not a mapping → skipped, no WARNING, no exception; a top-level list or scalar → no walk, no
  WARNING, no exception. US-003.
- A knob added to one request model without prose in every copy, or a shared knob attributed to one
  route → US-004's guard is red; a file with zero or two fenced regions → red. US-004.

## Out of Scope

- DNS-based checks at search time (ruling 7 — `validate_url` remains fetch-time only).
- `trusted_domains` / `verified_domains` on `/search` (trust tiers are a fetch-and-cache concept;
  search results carry no tier); `search.policy_suffix_trusted_skip` exists for `/metrics` parity
  and stays `0`.
- A public-suffix list. Ruling R8's two-label minimum plus the explicit leading-dot opt-in is the
  guard; `.co.uk` is an operator's deliberate choice, documented as such.
- Refusing to boot on an unknown config key (ruling 12: warn, never refuse), or on a malformed
  `promptguard_threshold` / domain-list entry (warn and fall back).
- Reading `searxng/config/settings.yml` at runtime, or generating `SEARXNG_ENGINES` from it
  (ruling 11: the parity test is the sync mechanism).
- Wire-level validation on any domain-list item (a pydantic pattern would echo the caller's bytes
  in a 422).
- The fail-closed floor, the threshold ceiling's `/retrieve` half and `effective_promptguard_fail_closed`
  (spec 2 US-005, ruling R10); this spec adds only the `/search` half of the ceiling.
- The resource-envelope keys (spec 6) and contiguity keys (spec 7) — they register themselves.

## Assumptions

- Spec 1 US-004 opened the 1.3.0 window and added `OMIT_BLOCKED_URL = "blocked_url"` to
  `pipeline/contract.py`'s `OMISSION_REASONS`; spec 1 US-002 added a reason channel to
  `_canonicalize_search_url`'s return; spec 1 US-003 landed the public host canonicaliser in
  `url_validator.py` and the `search_url_blocked — host_class=` log line; spec 2 **US-005** added
  `promptguard_fail_closed_floor`, `promptguard_threshold_ceiling`, the operator-policy resolver in
  `retrieval_app.py` (R24: `body.model_copy(update={...})`) and the `/retrieve` `effective_*`
  fields; spec 2 added the `config.yaml` `retrieve:` block.
- Poppy is the only consumer; both new `/search` fields default to today's behaviour, so a 1.2.0
  client that never sends them sees no change **provided** its `config.yaml` carries the shipped
  `promptguard_threshold: 0.85` (an operator who tuned it sees the documented upgrade effect in
  either direction).
- Hosts reaching `hostname_matches` on `/search` are already canonical (spec 1 US-003); on
  `/retrieve` they come from `urlsplit().hostname` and the helper canonicalises them with the same
  function — idempotent on canonical input.
- The three settings readers stay `.get(key, default)`-shaped; the registry is asserted beside
  them, not woven into them.
- `/retrieve`'s request lists and `/search`'s share one normaliser and one cap rule; the
  `providers` normaliser in `pipeline/search_providers/policy.py:35` (`_MAX_POLICY_ENTRIES = 8` at
  `:17`; no trailing-dot or IDNA rule) is a different function and stays separate.
- `idna` is already installed (httpx's dependency, `uv.lock`); listing it in `pyproject.toml` adds
  no new package.

## Technical Considerations

- **Rotation ledger (R32).** US-001: `pipeline/orchestrator.py` + `pipeline/contract.py`; US-007:
  the same two; US-002: the same two; US-005: `pipeline/contract.py` (+ `orchestrator.py` if the two
  reads are replaced here); US-003, US-004, US-006: none. Each rotating story measures by
  revert-and-reproduce and records at the five sites (`docs/bootstrap-notes.md`, `CLAUDE.md`,
  `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md`, `kit_tools/arch/CODE_ARCH.md`).
- **Normalisation owners.** Request lists: the `/retrieve` handler (US-007) and the `/search` handler
  (US-002), through the R24 replacement. Config lists: the lifespan (US-001), written back into
  `app.state.config`. `cache_policy_fingerprint`'s inline `strip().lower()` (`cache.py:154-163`)
  stays as a key-stability normalisation over already-canonical strings (leading dots intact); it is
  idempotent, so the two can never produce different keys for one request. The comparison sites
  never normalise.
- **The rotation is the invalidation across a deploy; the marker is the invalidation within one.**
  `cache_policy_fingerprint` keys on the domain *lists* (with the leading-dot marker) and carries
  `sanitizer_revision`, so a semantics change is invalidated by the rotation and a wildcard entry
  never shares a key with a bare one. The comment above `hostname_matches` says any later semantics
  change must be paired with a rotation.
- **Threshold resolution and the cache key.** The resolver runs in the handler before anything
  reaches the cache; `None` never reaches `cache_policy_fingerprint`; the ceiling clamps the config
  default as well as a caller's value.
- **Ordering inside the search result loop.** URL canonicalisation → spec 1's audit → `blocked_url`
  (this spec, `host_class=policy_blocklist`) → stage 2 → stage 3. A result rejected for its URL is
  counted once, under the first reason applied, and its text is never scanned.
- **Import graph.** `cache.py` and `pipeline/orchestrator.py` import from `url_validator.py`;
  `url_validator.py` imports neither; `idna` is imported only inside the canonicaliser.
- **`/extract` is untouched** by the threshold story (ruling R10); its guarded read and
  `UnsupportedFormatError` mapping stay the documented and still-reachable behaviour because the
  raw config value is never rewritten.
- **Strict typing.** No `# pyright: ignore` exists in the repo and none may be added
  (`pyproject.toml:97`); `float | None` request fields are narrowed in the handlers, never at a
  pipeline read.

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
`url_validator.py`, applied at three list sites, the private-name list, and both routes' request
lists; the host canonicaliser is spec 1 US-003's, reused.
**Rationale:** All three sites do `host in {lower-cased entries}` today (`url_validator.py:145-146`,
`pipeline/orchestrator.py:1114`, `cache.py:176`); one predicate cannot drift three ways, and a second
IDNA rule in the same epic (validation round 1, codebase fit and salty engineer) would be the drift
this spec exists to end.
**Alternatives considered:** Per-site suffix logic — rejected, that is how the drift started; a
matcher-local IDNA rule — rejected in round 2.
**Source:** explorer report 2026-09-19, item 4; WA-E punch list; validation rounds 1–2.

**Decision:** Suffix matching is unconditional for denylists and opt-in (leading dot) for
allowlists (ruling R8); config lists are normalised once at boot; denylists are uncapped and
operator-first.
**Rationale:** `trusted` skips stage 3 entirely (`pipeline/stage3_promptguard.py:77-88`); a uniform
suffix rule would let `trusted_domains: ["com"]` disable the classifier. Today's inline lower-casing
at the comparison sites is what keeps mixed-case config entries matching; once the sites stop
normalising, someone must, and a lifespan pass is the only place that runs once per config
(validation round 1: four reviewers). A capped or caller-evictable denylist is fail-open (security,
salty engineer).
**Alternatives considered:** Uniform suffix matching with a public-suffix list — rejected as a new
dependency and a moving target; uniform exact matching — rejected, it leaves `www.evil.com`
unblocked; `hostname_matches` normalising its own entry argument per call — rejected (IDNA per
entry per result on every request).
**Source:** validation rounds 1–2 (salty engineer, security, second opinion, completionist).

**Decision:** Honour `promptguard_threshold` on `/search` by adding the field, with `None` meaning
the boot-validated config default, clamped by spec 2's ceiling, and `/extract` untouched (ruling
R10 corrected).
**Rationale:** `SearchRequest` has no such field today (verified from `model_fields`); the handler
never passes a threshold (`retrieval_app.py:1811-1815`); `/extract`'s read is a guarded block raising
`UnsupportedFormatError` (`:1703-1713`) that a plain resolver would delete; a config value that boots
today must still boot (round 1 salty engineer: boot refusal was an unannounced upgrade break).
**Alternatives considered:** Routing `/extract` through the resolver — rejected (R10); refusing boot
on a malformed value — rejected in round 2.
**Source:** validation rounds 1–2.

**Decision:** Engine-list single-sourcing is closed by the existing parity test.
**Rationale:** `tests/test_searxng_docker.py::test_enabled_engines_match_the_orchestrator` fails
when `searxng/config/settings.yml` and `SEARXNG_ENGINES` diverge; `SERVICE_MAP.md:106` already
states it.
**Alternatives considered:** Runtime read of the SearXNG config — rejected, the image does not
ship it.
**Source:** ruling 11.

### Scope Adjustments

- Validation round 1 (R31): US-002 split into US-002 (`/search` `blocked_domains` +
  `seed_blocklist`) and US-005 (threshold); US-004 split into US-004 (seven-copy guard) and
  US-006 (engine-sync sentence).
- Validation round 2: US-001 split into US-001 (matcher semantics, call sites, config-list boot
  normalisation, rotation, docs) and US-007 (request-surface normalisation, the cap rule, the four
  counters and the `/metrics` mirror), so the story-verifier reads one concern per story;
  `execution_order` declared so US-007 precedes US-002 (which increments its counter).
- The `seed_blocklist` merge on `/search` was added (three reviewers found the omission); the
  `/search` half of the operator ceiling and `effective_promptguard_threshold` on `SearchResponse`
  moved here from spec 2 (the field it depends on is added here).
- The WA-E engine-duplication item was dropped from code work (ruling 11).

### Decisions Made

- Warn, never refuse, on unknown config keys (ruling 12); a known key with a bad value refuses boot
  through its reader — except `promptguard_threshold` and the two domain lists, which warn and fall
  back (R10 corrected) — documented as a deliberate split.
- No pydantic validation on any domain-list item; normalise in the handler (search-epic ruling 29).
- The rotation protocol is five sites (`kit_tools/EXECUTION_LOG.md`'s thirteenth-rotation backfill
  names `GOTCHAS.md` and `CODE_ARCH.md` alongside the three ruling 6 lists); references are by
  heading, not line, because specs 1 and 2 rotate first.
- One counter (`policy_invalid_domain_entry`) conflates invalid and over-cap drops: both only narrow
  an allowlist, and denylists are never truncated, so the split the salty engineer proposed has
  nothing fail-open to separate; recorded in the MONITORING row.
- `policy_suffix_trusted_skip` was added (security, info) because the wildcard form is the first
  way to disable the classifier for a subtree and `promptguard_state` cannot tell an exact match
  from a wildcard.
- Overruled: the security reviewer's "same-name meaning change is a MAJOR" — bare allowlist
  entries keep their meaning and the denylist change is a tightening; classified and announced in
  the window plus an upgrade note (GOVERNANCE recorded ruling in US-001).
- Overruled: per-response "entries dropped" signal or a count-only 422 for over-cap denylists —
  moot, denylists are uncapped.
- Overruled: a `FORAGE_TRUST_SUFFIX_DENYLIST` of multi-tenant apexes — stays an open question;
  the two-label rule, the docs caution and the new counter are the guard for this epic.
- The `providers` normaliser and the domain normaliser stay separate functions (caps 8 vs 64,
  trailing-dot and IDNA rules differ); the `_FORBIDDEN_IMPORTS` sweep
  (`tests/test_search_providers.py:229`) would permit sharing later.
- `_BLOCKED_HOSTNAMES` / `_BLOCKED_SUFFIXES` join `hostname_matches` (ruling R8 lists the
  private-name list among the denylists) rather than staying exempt.
- The baseline fixture is narrowed to five fields, moved under `tests/fixtures/search/` and given a
  README section, because whole-response equality would go red on every later window story.

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

### Session 2026-09-19 (validation round 2)
- Rulings applied: R8 corrected (config lists normalised once in the lifespan; denylists uncapped and
  operator-first; allowlists capped at 64; spec 1's canonicaliser reused; `idna` package, UTS-46;
  numeric hosts classified after canonicalisation; route-specific and shared knob sets; the
  leading-dot marker verbatim in the fingerprint; un-canonicalisable hosts fail closed at denylist
  sites), R10 corrected (`SearchRequest.promptguard_threshold` **added** as a window change; the
  `/search` half of the ceiling and `effective_promptguard_threshold` live here; malformed config
  threshold warns and falls back), R24 (one `model_copy` replacement), R32, R36 (uniform window
  block), R39 (site lists by value), R40 (tests named).
- Q: Where does a malformed config threshold fail? → A: Nowhere — it logs `config_invalid_value`
  and falls back to `0.85`; `/extract`'s own guard stays reachable.
- Q: Who normalises `seed_blocklist` and `news_domains`? → A: The lifespan, once, writing the
  canonical spellings back into `app.state.config`; a dropped entry is one boot WARNING per list.
- Q: Does a caller's `blocked_domains` cap ever truncate the operator's list? → A: No — denylists
  have no count cap and merge operator-first.

## Open Questions

- [ ] Whether `KNOWN_CONFIG_KEYS` should also carry a per-key "read by" pointer for the docs table
      (non-blocking; a later docs sweep can add it).
- [ ] Whether a shipped list of multi-tenant apexes should refuse leading-dot allowlist entries
      outright (non-blocking; the two-label rule, the docs caution and `policy_suffix_trusted_skip`
      are the guard for this epic; the corpus epic is the natural owner if the counter shows use).
