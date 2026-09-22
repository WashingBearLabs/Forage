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
> allowlists (ruling R8, corrected in validation rounds 2–5) — give `/search` the domain-policy and
> threshold knobs `/retrieve` already has (rulings 9, R10 corrected), and put the `config.yaml`
> surface behind one key registry so an operator typo is a WARNING and every key has a documented
> row (ruling 12). Ruling 11 records that the SearXNG engine list is *already* single-sourced by
> test. Context: WA-E punch list ("Exact-hostname matching breaks blocklists, trust tiers, and news
> TTLs", "`blocked_domains` applies to fetch but never search", "`promptguard_threshold` is dead
> for 2 of 3 endpoints", "Nine distinct config surfaces"), audit finding 2026-09-16-058, and the
> epic's rulings 5, 6, 9, 11, 12, R8, R10, R24, R31, R32, R36, R37, R39, R40, R41, R43, R44 are binding here.
> Every line number in this spec was measured before its `depends_on` specs landed; re-locate every
> anchor by the symbol or quoted string beside it, never by the number alone.

## Overview

Three exact-match sites decide security-relevant things today, and all three read
`host in {lower-cased entries}`: `validate_url`'s `blocked_domains` check
(`url_validator.py:145-146`, `lower_host in lower_blocked`), `_resolve_request_trust_tier`
(`pipeline/orchestrator.py:1114`) and `_effective_ttl_hours`'s `news_domains` check
(`cache.py:176`). Blocking `evil.com` therefore does not block `www.evil.com`, and the one-hour news
TTL never applies to a `www.`-prefixed host — the second is closed here by rewriting the shipped
`news_domains` entries to the leading-dot form, not by widening the allowlist matcher (round 4).

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
changes meaning; every existing multi-label denylist entry now also covers its subdomains** (a
tightening in the safe direction, classified against GOVERNANCE's Example 6 and carried by an
upgrade note). Every **allowlist** entry must have at least two labels after any leading dot is
removed — the two-label minimum guards privilege grants; a **single-label denylist entry**
(`seed_blocklist: ["intranet"]`, `blocked_domains: ["wiki"]`) is accepted and keeps **exact-only**
matching (suffix widening applies only to entries with two or more labels), so no existing denylist
entry stops matching and no security-window classification is needed (R8 corrected, round 4).
Empty-label, over-long and non-IDNA entries are ignored and counted (request lists) or dropped with
one boot WARNING naming them (config lists — the operator's own file) — never a 422. The built-in private-name list (`localhost`, `.localhost`, `.local`)
is **not** a domain list: it keeps its own single-label check ahead of every list comparison, so the
two-label minimum never touches it (R8 corrected, round 3). An IP-literal host matches an entry only
by equality, never by suffix, at every site.

Normalisation has one owner per source, and **canonical strings are what cross every boundary**
(R8 corrected, round 3): the request fields stay `list[str]`, the three call sites keep their
`Sequence[str]` parameters, `app.state.config` holds canonical strings, `cache_policy_fingerprint`
receives canonical strings with the leading dot intact, and `hostname_matches` reads the leading-dot
marker off the string itself; `DomainEntry` is the normaliser's internal return only. Request lists
are normalised in the handler, once, and reach the pipeline through the same request replacement
spec 2 US-005 introduced (R24). Config lists (`seed_blocklist`, `news_domains`) are normalised once
in the lifespan and published on `app.state`. Until US-007 lands the handler normalisation, each
comparison site runs the entries it receives through `normalize_domain_entries` itself and its host
through `canonicalize_host` — the same helpers, one pass per call — so no intermediate commit ships
a denylist that stops matching a mixed-case **or non-ASCII** caller entry (`blocked_domains:
["straße.de"]` keeps blocking; R8 corrected, round 5); US-007 deletes the per-comparison entry
pass. Caller lists are bounded by **bytes, never
by entry count** (R8 corrected, round 4; R44): the joined raw entries of each request list are
measured before any normalisation against `policy_domain_entries_max_bytes` (`config.yaml`, default
64 KiB), so the UTS-46 work a request can buy is bounded by the budget and the host is encoded once
per call site, never once per entry. A denylist over budget **refuses the request** with the closed
reason `policy_domain_list_too_large` on each route's existing 422 code (ruling 42) — a denylist is
never partially enforced; an allowlist over budget is truncated at the budget boundary and the
remainder counted, because truncating an allowlist only narrows privilege. The operator's entries
merge first and can never be evicted; the operator's own lists are trusted input and carry no
budget.

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
  only comparison used by the three list sites; the host side reuses spec 1 US-003's
  `canonicalize_host` (exactly one IDNA implementation across `url_validator.py` and
  `pipeline/orchestrator.py`); a parametrised test proves `evil.com` (denylist) matches `evil.com`
  and `www.evil.com` and never `notevil.com` or `evil.com.attacker.net`; that a bare `example.com`
  allowlist entry matches only `example.com`; that `.example.com` matches `example.com` and
  `a.b.example.com`; that `straße.de` and `xn--strae-oqa.de` match each other (UTS-46 via the `idna`
  package); that an IP-literal host matches only by equality; that `com`, `.com`, `""`,
  `bad..entry` and a non-IDNA entry are rejected by the normaliser for an allowlist; and that a
  single-label denylist entry is accepted exact-only. The private-name check stays its own step:
  `localhost`, `localhost.`, `anything.localhost`, `printer.local` and `deep.sub.myhost.local` are
  refused before DNS after the story; `localhost.` and `anything.localhost` are newly refused at
  that stage (trailing-dot canonicalisation and `.localhost` in `_BLOCKED_SUFFIXES`), the other
  three exactly as before.
- Config-sourced lists survive the change: `seed_blocklist: [" Evil.COM. "]` blocks `www.evil.com`
  and `news_domains: ["BBC.co.uk"]` still shortens the TTL for `bbc.co.uk` after the story lands;
  a malformed config entry (or a single-label `news_domains` entry) is dropped with one boot WARNING
  naming the key, the count and the dropped entries — the operator's own file, not caller bytes; a
  single-label `seed_blocklist` entry is kept exact-only; the shipped `config.yaml`'s six
  `news_domains` entries are rewritten to the leading-dot form so `www.bbc.co.uk` gets the one-hour
  TTL on the default deployment; an un-canonicalisable fetch host is refused whether or not any blocklist is
  configured.
- Dropped domain-list entries and wildcard-caused classifier skips are observable on `/metrics`, per
  entry, with the offending entry never stored.
- `/search` honours `blocked_domains` and the operator's `seed_blocklist` (results omitted as
  `blocked_url`) and `promptguard_threshold` (per-request value, else `config.yaml`, then the
  operator ceiling); a request that sends neither field yields the same `results`,
  `omitted_by_reason`, `fallback_fired`, `provider_used` and `provider_errors` as the committed
  pre-story baseline.
- Zero silent config keys: every key `config.yaml` can carry is in `KNOWN_CONFIG_KEYS`, every
  registry key has a row in `docs/configuration.md`, every key the code reads is in the registry,
  an unknown key produces exactly one `config_unknown_key` WARNING naming the key (never its value),
  and `_warn_unknown_config_keys` never raises on a malformed document (the readers' own typed boot
  refusals for a non-mapping block are unchanged).
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
normalisation at boot, the contract window and the rotation; the request-surface plumbing, the cap
rule and the counters are US-007. Between the two stories each of the three sites normalises the
entries it receives through `normalize_domain_entries` and canonicalises its host through
`canonicalize_host` (one pass per call, the same helpers), so a caller's `blocked_domains:
["Evil.com"]` **and** `["straße.de"]` still match after this story alone — the seam never fails
open (R8 corrected, round 5).

**Independent Test:** A parametrised test over `hostname_matches`, `normalize_domain_entries` and
each of the three call sites — `validate_url("https://www.evil.com/x", blocked_domains=["evil.com"])`
with DNS patched as `tests/test_url_validator.py` does, `_resolve_request_trust_tier("www.example.com",
[".example.com"], [], [])` versus `(["example.com"])`, and `_effective_ttl_hours(24,
domain="www.bbc.co.uk", news_domains=[".bbc.co.uk"])` (all entries canonical strings, as the call
sites will receive them) — proves the rules above; `https://localhost/`, `https://localhost./`,
`https://anything.localhost/`, `https://printer.local/` and `https://deep.sub.myhost.local/` still
raise `PrivateIPError` at the hostname stage before DNS, with `blocked_domains=None` and with a
non-empty list; `https://xn--/` (un-encodable) is refused by `validate_url` with no blocklist
configured; a lifespan test with `_load_config` patched to `seed_blocklist: [" Evil.COM. ",
"bad..entry", "intranet"]` and `news_domains: ["BBC.co.uk", ".Example.ORG", "com"]` shows
`app.state.config["seed_blocklist"] == ["evil.com", "intranet"]` (a single-label denylist entry is
kept, exact-only: `intranet` is refused, `wiki.intranet` is not), `app.state.config["news_domains"]
== ["bbc.co.uk", ".example.org"]`, and exactly one WARNING per list whose `getMessage()` contains
`config_invalid_value`, the key name, `dropped=1` and the dropped entry (`bad..entry` / `com` — the
operator's own file, so the entry is named; a list with no drops logs nothing); every pre-existing
test in `tests/test_url_validator.py` (including `TestHostnameRejection`, `:197`),
`tests/test_stage5_url_audit.py`, `tests/test_orchestrator.py` and `tests/test_cache.py` passes
with no call-site change: every one passes lower-cased strings except `tests/test_cache.py:506`'s
`domain="CNN.com"` (`TestTTLLogic::test_news_domain_case_insensitive`, `:498-508`), which stays
green because `_effective_ttl_hours` canonicalises its host argument through the same helper
(round 5, codebase fit); `validate_url("https://straße.de/", blocked_domains=["straße.de"])` and
the `["xn--strae-oqa.de"]` spelling both raise `BlockedDomainError` after this story alone.

**Implementation Hints:**
- **Anchors.** Every `file:line` below was measured before specs 1 and 2 landed; locate by the
  symbol or quoted string, never by the number.
- **Reuse spec 1's canonicaliser; add no second IDNA rule.** Spec 1 US-003 lands
  `canonicalize_host(host) -> CanonicalHost | HostRejection` in `url_validator.py` (R27 corrected,
  round 4): a `CanonicalHost` carries the encoded host and its `kind` (`name`, `ipv4`, `ipv6`); a
  `HostRejection` carries the closed token (`idna`, `numeric_host`, `zone_id`, …) and **nothing
  raises** — confirm the final shape from spec 1's Implementation Notes, which are the tie-breaker.
  `normalize_domain_entries` calls it per entry after removing and remembering one leading dot: a
  `HostRejection` makes the entry invalid (no exception handling anywhere in the normaliser — a
  reviewer showed a `try/except idna.IDNAError` written against the old description would never
  fire and would let the rejection flow on as a host string, which is the fail-open direction); a
  `name`-class result must then split into labels with **no empty label** (checked on the split
  labels — `"".encode("idna")` succeeds, so the encode step alone never catches `bad..entry`) and
  total ≤ 253 characters, with **at least two labels for an allowlist entry** and any label count
  for a denylist entry (single-label denylist entries are exact-only); an IP-literal-class result
  is accepted as an **exact-only** entry (a leading dot on an IP literal is invalid).
  `hostname_matches` takes an **already-canonical** host and never canonicalises: each of the three
  comparison functions canonicalises the host it is handed, once per call, through
  `canonicalize_host` (`validate_url` at its top; `_resolve_request_trust_tier` and
  `_effective_ttl_hours` on entry — idempotent, so the `/retrieve` pipeline's already-canonical
  `domain` costs nothing new and a direct caller such as `tests/test_cache.py:506` needs no
  call-shape change; `/search` results arrive canonical from spec 1 US-003), so a request's UTS-46
  work is one encode per host per site plus one per raw entry within the byte budget, never
  entries × results (R8 corrected, round 5). An IP-literal-class host is
  equality-only at every site. `idna` is already a direct dependency (spec 1 US-003's criterion;
  `uv.lock` already resolves it as httpx's dependency, so no re-lock) — verify it is listed in
  `pyproject.toml` `dependencies`; a criterion pins that exactly one IDNA call site exists across
  the two files.
- Add to `url_validator.py` (not a `_REVISION_SOURCES` member) beside `_check_hostname_blocklist`
  (today `:95`; spec 1 will have moved it): `normalize_domain_entries(entries: Sequence[str], *,
  denylist: bool, budget_bytes: int | None) -> tuple[list[str], int]` returning **canonical
  strings** (leading dot kept for a wildcard allowlist entry; stripped on a denylist, where suffix
  matching is unconditional) plus the dropped count; `hostname_matches(host: str, entry: str, *,
  allow_suffix: bool) -> bool`, which reads the leading-dot marker off `entry` itself and applies
  the suffix rule only to entries with two or more labels; `matched_entry(host: str, entries:
  Sequence[str]) -> str | None` (the entry that matched under the allowlist rule, for US-007's
  counter — the resolver's own return stays a bare tier string); and `domain_list_bytes(entries:
  Sequence[str]) -> int` (the raw UTF-8 length of the entries joined by `\n`, measured before any
  normalisation — the budget's unit). `DomainEntry`
  (`@dataclass(frozen=True, slots=True)`, `name: str`, `wildcard: bool`; the repo's idiom, no
  `NamedTuple`) is the normaliser's **internal** return and never crosses a function boundary: the
  request fields stay `list[str]`, `validate_url(blocked_domains: list[str] | None)`,
  `_resolve_request_trust_tier(... list[str])` and `_effective_ttl_hours(news_domains: list[str] |
  None)` keep their signatures, `cache_policy_fingerprint` (`cache.py:148-162`) keeps its inline
  `strip().lower()` over already-canonical strings (idempotent; the leading dot survives verbatim,
  a criterion asserts it), and pyright strict is satisfied with no ignore. Normalisation order:
  strip → remember and remove one leading dot → `canonicalize_host` → label/length rules → re-attach
  the dot (allowlists). `budget_bytes=None` means no budget (config lists); with a budget, an
  allowlist is consumed in order until the next raw entry would take the running byte total over
  the budget, and everything from that entry on is dropped and counted; a denylist is never
  truncated here — the handler measures it with `domain_list_bytes` and refuses the request before
  calling (US-007, US-002).
- Matching: `hostname_matches(host, entry, allow_suffix=True)` is equal, or — when `entry_name` has
  two or more labels — `host` ends with `"." + entry_name`; a single-label entry is equal-only
  whatever the flag; denylist sites pass `allow_suffix=True` unconditionally; allowlist sites pass
  `allow_suffix=entry.startswith(".")`. **The host is canonicalised before any list comparison and
  regardless of whether a list is configured**: `validate_url` canonicalises first, ahead of the
  `if blocked_domains:` block (`url_validator.py:143`), and an un-canonicalisable host raises the
  existing `ValueError`-class error that `/retrieve` maps to `invalid_url`
  (`pipeline/orchestrator.py:268-284`) and stage 5 refuses on the hop
  (`pipeline/stage5_url_audit.py:144`) — the refusal never depends on an unrelated config value. On
  `/search` spec 1 US-002/US-003 already rejected such hosts. At the two allowlist sites an
  un-canonicalisable host simply matches nothing. **Precedence change and its wire effect** (round
  4): today the `blocked_domains` comparison runs before the private-name check
  (`url_validator.py:143-149`); after this story the private-name check runs first, so a host that
  is both denylisted and private-named (`blocked_domains: ["evil.local"]`, `https://evil.local/`)
  maps to `private_ip` instead of `blocked_domain` (`pipeline/orchestrator.py:268-284`) — a value
  swap between two existing 422 codes on one input shape, no shape change; the GOVERNANCE ruling
  classifies it, the 1.3.0 docstring line announces it and a criterion pins it.
- **The private-name list is not a domain list.** `_check_hostname_blocklist`
  (`url_validator.py:95-102`) keeps its own `==` / `endswith` check and runs unconditionally, before
  any list comparison, on the canonical host; it gains `".localhost"` beside `".local"` in
  `_BLOCKED_SUFFIXES` so `anything.localhost` is refused at the hostname stage (RFC 6761), and the
  bare host `local` stays allowed exactly as today. Spec 1 US-003's `is_blocklisted_hostname`
  wraps this same helper — do not route it through `hostname_matches`; the two-label minimum
  guards caller- and operator-supplied lists only, and a criterion pins that
  `normalize_domain_entries` still rejects every single-label **allowlist** entry so the guard
  cannot be relaxed to make a private-name test green (single-label denylist entries are accepted
  exact-only and never reach this check).
- Replace the three comparisons: `url_validator.py:145-146` (keep raising `BlockedDomainError`
  with the same message shape; `validate_url` runs on every redirect hop — `pipeline/stage5_url_audit.py:
  144` — so a hop onto `www.blocked.example` is now refused), `pipeline/orchestrator.py:1114`
  (`blocked` still wins over `trusted` over `verified`; the resolver's signature and bare-`str`
  return are **unchanged** — US-007 finds the matching entry with the separate `matched_entry`
  helper, called only when the tier resolved to `trusted` or `verified`; R8 corrected, round 4), `cache.py:176` (`min(1, ttl_hours)`
  unchanged). Each site runs the entries it receives through `normalize_domain_entries(entries,
  denylist=<site>, budget_bytes=None)` once per call — interim, the same helper the lifespan and,
  after US-007, the handlers use; US-007 deletes the per-comparison call once the handler
  normalises (a criterion there). A bare `.lower()` was rejected in round 5: it let
  `blocked_domains: ["straße.de"]` stop matching the canonical `xn--strae-oqa.de` host between the
  two stories, the fail-open direction. The interim pass is unbudgeted caller work for exactly the
  commits between US-001 and US-007, which is why US-007 is next in `execution_order`. `cache.py` may
  import from `url_validator`; `url_validator.py` must not import `cache` or `pipeline.orchestrator`.
- **Config-sourced lists are normalised once in the lifespan** (R8 corrected): after `_load_config`,
  run `seed_blocklist` (denylist) and `news_domains` (allowlist) through `normalize_domain_entries`
  with `budget_bytes=None` (`denylist=True` for `seed_blocklist`, `False` for `news_domains`) — the
  operator's file is trusted input; the byte budget is for caller lists —
  write the canonical spellings back into the published config dict
  (`app.state.config["seed_blocklist"]` / `["news_domains"]`) so `pipeline/orchestrator.py:249` and
  `:262` keep reading them through the existing `config.get` with no signature change; log one
  `logger.warning("config_invalid_value — key=%s dropped=%d entries=%s", key, n, ",".join(dropped))`
  per list **with drops** (tokens in the message, never `extra=`; the dropped entries **are named**
  because they come from the operator's own mounted file, not from a caller — CLAUDE.md invariant 6
  is about credential-bearing values, and a count with no entry is a WARNING nobody can act on; the
  request path stays count-only; a list with zero drops logs nothing). **The shipped
  `config.yaml`'s six `news_domains` entries are rewritten to the leading-dot form** (`.reuters.com`,
  `.apnews.com`, `.bbc.co.uk`, `.nytimes.com`, `.theguardian.com`, `.cnn.com`) so the default
  deployment gives `www.bbc.co.uk` the one-hour TTL the docs promise — the punch-list item this spec
  cites is not closed by the matcher alone; `docs/configuration.md`'s `news_domains` row carries the
  upgrade note (an operator's own bare entries stay exact; add the dot to cover subdomains).
  The per-request counters (US-007) do not tick for config entries. The raw config value stays
  untouched for `pipeline/sanitizer_revision.py:41`'s hash input.
- Contract window (ruling 5, R36 — copy exactly): the three `RetrieveRequest` list descriptions
  (`models.py:249-257`) gain the rule ("bare entries match exactly; a leading dot covers the apex
  and every subdomain; `blocked_domains` always covers subdomains; an IP literal matches only
  itself") **and, on `trusted_domains` and `verified_domains`, each tier's own consequence and the
  caution**: a leading-dot `trusted_domains` entry skips injection classification for every host
  under that suffix; a leading-dot `verified_domains` entry makes every host under the suffix
  **degrade open** when the classifier is unavailable — including under spec 2's
  `promptguard_fail_closed_floor` and the load-triggered wait timeout of spec 2 US-006 (round 4,
  security); neither may name a multi-tenant or registry-level apex (`.co.uk`, `.github.io`,
  `.s3.amazonaws.com`); `policy_suffix_trusted_skip` counts a wildcard-caused resolution to either
  tier and is the operator's signal for the form — a criterion asserts the caution text in **both**
  exported descriptions. Append one `* ``1.3.0`` — …` docstring
  line to `pipeline/contract.py`'s `CONTRACT_VERSION` entry (read `:26-68` for the bullet shape);
  `uv run python -m scripts.export_contract`; re-create `tests/golden/contract_1_3_0.json` through
  `tests/test_contract_schema.py::_SCHEMA_MODELS`; **nothing is appended to
  `_EXPECTED_ONE_THREE_ZERO_DIFF`** — `_added_paths` (`tests/test_contract_schema.py:130`) reports
  new properties and enum members only, and this story moves descriptions; moreover
  `RetrieveRequest` is **not** in `_SCHEMA_MODELS` (`:28-35`, six models — verified), so the golden
  does not move for this story at all (re-creating it is a no-op, done for the uniform block) and
  its real gates are `tests/test_contract_export.py`'s drift check
  (`test_the_committed_contract_is_what_the_app_generates`, `:130` — `contract/openapi.yaml` does
  carry `RetrieveRequest`, `trusted_domains` at `:782`) plus this story's own `contract/openapi.yaml`
  description test in the criteria (R8 corrected, round 5);
  refresh the four anchor-quoting pages
  `tests/test_governance_docs.py::_ANCHOR_QUOTING_PAGES` names; `--check` green.
- GOVERNANCE recorded ruling (`contract/GOVERNANCE.md` "Recorded rulings"): name **Example 6
  ("expedited security changes", `:150-167`) explicitly** — the multi-label denylist widening is a
  tightening (a value that matched exactly now also matches its subdomains); step 1's compatible
  ship (a leading-dot opt-in for denylists too) was considered and declined because it would leave
  `www.evil.com` unblocked for every existing entry, which is the bug; what stands in for step 2's
  compatibility window is the upgrade note in `docs/configuration.md` plus the Release-body line
  spec 8 lifts; there is **no** single-label loosening — single-label denylist entries keep exact-only matching,
  so the ruling records a pure tightening (round 4); the same ruling classifies the
  `blocked_domain` → `private_ip` precedence swap for a host that is both denylisted and
  private-named (a value swap between two existing codes, no shape change) and records
  `policy_domain_list_too_large` as a new closed reason on each route's existing 422 code (ruling
  42). Bare allowlist entries are unchanged; the leading-dot form is additive. The ruling is its
  own `### (<next free letter>) ` section with a `**Source:**` line (spec 1 US-003 takes `(e)` and
  `(f)` first; reconcile the letter at merge the way a rotation ordinal is), appended to
  `tests/test_governance_docs.py::_RULING_MARKERS` (`:93`), with the count words at
  `contract/GOVERNANCE.md:174`, `CLAUDE.md:89` and `tests/test_governance_docs.py:90` / `:355`
  incremented as found — a ruling outside the tuple is ungated (round 5; spec 4 US-001 adds the
  `_NUMBER_WORDS` test that pins the count). Use "the 1.3.0 window" for the version window
  everywhere in this spec and "compatibility window" only for Example 6's.
- Rotation (ruling 6, R32): `pipeline/orchestrator.py` and `pipeline/contract.py` move. Read
  `kit_tools/arch/DECISIONS.md`'s rotation ADR as found — spec 1 US-001 may already have amended its
  "none changing sanitization behaviour" preamble; if the sentence is still there, amend it here
  (a `.example.com` entry skips PromptGuard for its subdomains). Record on the five-site protocol:
  `docs/bootstrap-notes.md` (next numbered heading, `before:/after:` pair), `CLAUDE.md`
  (Coexistence paragraph), `kit_tools/arch/DECISIONS.md` (table + preamble), `kit_tools/docs/
  GOTCHAS.md` (the "`sanitizer_revision` has deliberately diverged" heading — increment its
  spelled-out counter and extend its table; refer to it by heading, not line), `kit_tools/arch/
  CODE_ARCH.md` (rotation narrative). Ordinals are written as "the next" and reconciled at merge
  if a sibling branch took the number first. Add a one-line comment above `hostname_matches`
  saying any change to its semantics must be paired with a rotation, because matching semantics
  are a cache-key input in substance. A comment fails no build, so `kit_tools/docs/GOTCHAS.md` also
  gains an entry stating that `url_validator.py` is a cache-key input in substance that
  `_REVISION_SOURCES` does not hash (a matcher-only change rotates nothing and serves content
  sanitised under the old semantics until the TTL); adding the file to `_REVISION_SOURCES` is an
  open question with its cost named (round 5).
- Tests (R40): `tests/test_url_validator.py` (`TestBlockedDomains` `:226-260` and
  `TestHostnameRejection` `:197` — the round-3 name `TestLocalhostAndLocal` never existed),
  `tests/test_stage5_url_audit.py::TestBlocklistDuringFetch` (`:173`, redirect hop), new direct
  unit tests for `_resolve_request_trust_tier` (none exist — `grep -rn _resolve_request_trust_tier
  tests/` is empty; nearest
  `tests/test_orchestrator.py::test_retrieve_trusted_tier_loaded_classifier_reports_skipped_trusted`),
  `tests/test_cache.py::TestTTLLogic`, and the lifespan config-normalisation test on the
  `tests/test_app.py:1196-1208` config idiom. The four modules' existing string call sites
  (`tests/test_url_validator.py:234`, `tests/test_stage5_url_audit.py:178`,
  `tests/test_orchestrator.py:703`, `tests/test_cache.py:506` (`domain="CNN.com"`), `:507`, `:1000`)
  are not rewritten.
- Docs the story owns — every site that says "exact", found by value (R39): run
  `grep -rn -iE "exact-host|exact host|exact, case-insensitive" docs kit_tools/docs kit_tools/arch
  README.md` at the start (nine line sites in six files today: `docs/configuration.md:435`,
  `kit_tools/docs/API_GUIDE.md:158`, `kit_tools/docs/TROUBLESHOOTING.md:158` and `:417`,
  `kit_tools/docs/ENV_REFERENCE.md:86`, `kit_tools/arch/SECURITY.md:86` and `:107`,
  `kit_tools/arch/SERVICE_MAP.md:175` and `:262`; `kit_tools/specs/**` and `kit_tools/.seed_cache`
  are excluded — the specs quote the old wording deliberately). Each states the two-direction rule
  with the `evil.com` / `www.evil.com` / `notevil.com` and `example.com` / `.example.com` examples;
  the `trusted_domains` / `verified_domains` rows carry the multi-tenant caution; the `seed_blocklist`
  row and the `/retrieve` `blocked_domains` description carry an **upgrade note** written so spec 8
  can lift it (existing multi-label entries now cover subdomains — review apex entries before
  upgrading, a multi-tenant apex in a denylist removes every tenant; single-label entries keep
  matching exactly as before); the `news_domains` row (`docs/configuration.md:435`) carries its own
  note (bare entries stay exact; the shipped entries now carry the dot) and the sentence that
  the list is observable through `/search`'s `blocked_url` counts and `/retrieve`'s refusal
  message, so it is policy, not a secret. `kit_tools/docs/MONITORING.md` `### Startup lines you may
  see` (`| Level | Line | When |`) gains a `WARNING` row whose Line cell names `config_invalid_value`
  (and says the domain-list form names the dropped entries);
  `kit_tools/arch/patterns/LOGGING.md`'s `retrieval_app` inventory line (`:45`) and its
  "Operator-facing misconfiguration at boot" bullet gain the marker (US-003 does the same for
  `config_unknown_key`).

**Acceptance Criteria:**
- [x] Pre-flight (this is the first story in `execution_order`): before any other work, a check
      asserts each assumed seam exists with the assumed shape — `canonicalize_host` importable from
      `url_validator` and returning `CanonicalHost | HostRejection`; `pipeline.config_bounds.
      bounded_float` and `bounded_int` importable; `contract.OMIT_BLOCKED_URL in OMISSION_REASONS`;
      `tests/golden/contract_1_3_0.json` present; `_EXPECTED_ONE_THREE_ZERO_DIFF` and
      `_ONE_THREE_ZERO_DIFFED_SCHEMAS` importable from `tests.test_contract_schema`, the tuple
      covering every top-level key of `tests/golden/contract_1_2_0.json` the way
      `test_the_1_1_0_to_1_2_0_diff_has_no_unlisted_additions` (`:221`) asserts for 1.2.0 (round 5 —
      the window stories depend on the diff machinery, not the file); the operator-policy resolver
      locatable in `retrieval_app.py` by `promptguard_threshold_ceiling`. A failing pre-flight stops the spec
      (recorded in Implementation Notes) instead of surfacing in US-005.
- [x] `hostname_matches`, `normalize_domain_entries`, `matched_entry`, `domain_list_bytes` and
      `DomainEntry` exist in `url_validator.py`;
      the three list sites call `hostname_matches` on canonical strings (`grep -c hostname_matches`
      ≥ 1 in `url_validator.py`, `pipeline/orchestrator.py` and `cache.py`); the three inline
      expressions are gone (`grep -n "lower_host in lower_blocked" url_validator.py`, `grep -n "in
      {d.lower()" pipeline/orchestrator.py` and `grep -n "in {item.lower()" cache.py` each return
      nothing); no function signature among `validate_url`, `_resolve_request_trust_tier`,
      `_effective_ttl_hours` and `cache_policy_fingerprint` changes; `uv run pyright` passes with no
      type-ignore comment.
- [x] Exactly one IDNA implementation: `grep -cE "idna\.(encode|decode)|encode\(\"idna\"\)"
      url_validator.py pipeline/orchestrator.py` sums to 1, inside spec 1's `canonicalize_host`;
      `idna` is listed in `pyproject.toml` `dependencies` (spec 1's work, verified here).
- [x] Parametrised matcher test covers at least: equal; `www.` prefix; deeper subdomain;
      `notevil.com`; `evil.com.attacker.net`; trailing dot on host and entry; `münchen.de` vs
      `xn--mnchen-3ya.de` and `straße.de` vs `xn--strae-oqa.de` in both directions; bare vs
      leading-dot allowlist entry; an IPv4 and an IPv6 literal host matching an identical entry and
      never a suffix-shaped one (`.2.3.4` is invalid; `1.2.3.4` vs `11.2.3.4` no match); and the
      rejected shapes `com`, `.com`, `""`, `bad..entry`, a 254-char entry, a non-encodable label —
      each dropped and counted on an allowlist; on a denylist `com` and `intranet` are accepted
      exact-only (`intranet` matches `intranet` and never `wiki.intranet`) and `.com`, `""`,
      `bad..entry` are still dropped. A separate test asserts `normalize_domain_entries(...,
      denylist=False)` rejects every single-label entry (the private-name guard is closed by test).
- [x] `validate_url("https://www.evil.com/x", blocked_domains=["evil.com"])` raises
      `BlockedDomainError`; `blocked_domains=["notevil.com"]` does not;
      `validate_url("https://straße.de/", blocked_domains=["straße.de"])` and with
      `["xn--strae-oqa.de"]` both raise `BlockedDomainError` after this story alone (the seam never
      fails open); a redirect hop onto
      `www.blocked.example` under `blocked.example` is refused
      (`tests/test_stage5_url_audit.py::TestBlocklistDuringFetch`); an un-canonicalisable host is
      refused by `validate_url` with `blocked_domains=None`, with `[]` and with a non-empty list,
      and a redirect hop onto one is refused, never fetched.
- [x] `https://localhost/`, `https://localhost./`, `https://anything.localhost/`,
      `https://printer.local/` and `https://deep.sub.myhost.local/` raise `PrivateIPError` at the
      hostname stage before DNS, with and without a blocklist; `https://local/` is unchanged
      (allowed at the hostname stage); `TestHostnameRejection` passes unchanged; `_BLOCKED_SUFFIXES`
      contains `.localhost` and `.local` and is not read through `hostname_matches`; a host that is
      both denylisted and private-named (`blocked_domains=["evil.local"]`, `https://evil.local/`)
      raises `PrivateIPError`, so `/retrieve` maps it to `private_ip` (the precedence the
      GOVERNANCE ruling records).
- [x] `_resolve_request_trust_tier("www.example.com", ["example.com"], [], [])` returns `"standard"`;
      with `[".example.com"]` it returns `"trusted"`; with `blocked_domains=["example.com"]` as well
      it returns `"blocked"`; `_resolve_request_trust_tier("a.com", ["com"], [], [])` returns
      `"standard"` — `com` is dropped by the allowlist normalisation the site runs in this story (the
      handler's after US-007), and a single-label entry is exact-only in any case; counting it under
      `policy_invalid_domain_entry` is US-007's.
- [x] `_effective_ttl_hours(24, domain="www.bbc.co.uk", news_domains=["bbc.co.uk"])` returns `24`
      and with `[".bbc.co.uk"]` returns `1`; `["bbc.co.uk"]` with `domain="bbc.co.uk"` returns `1`.
- [x] Lifespan normalisation: `seed_blocklist: [" Evil.COM. ", "bad..entry", "intranet"]` and
      `news_domains: ["BBC.co.uk", ".Example.ORG", "com"]` publish `["evil.com", "intranet"]` and
      `["bbc.co.uk", ".example.org"]`; `www.evil.com` and `intranet` are refused on `/retrieve`,
      `wiki.intranet` is not, and `bbc.co.uk` gets the one-hour TTL; exactly one
      `config_invalid_value` WARNING per list naming the key, `dropped=1` and the dropped entry
      (`bad..entry` / `com`); a 70-entry `news_domains` publishes all 70 valid entries; the shipped
      `config.yaml` logs no such WARNING and its six `news_domains` entries carry the leading dot,
      so `_effective_ttl_hours(24, domain="www.bbc.co.uk",
      news_domains=app.state.config["news_domains"])` returns `1`.
- [x] `trusted_domains=["example.com"]` and `trusted_domains=[".example.com"]` produce different
      `cache_policy_fingerprint` values for the same URL (the marker reaches the fingerprint as a
      string), and two spellings of one entry (`"Example.COM "` / `"example.com"`) produce the same
      value.
- [x] No test in `tests/test_url_validator.py`, `tests/test_stage5_url_audit.py`,
      `tests/test_orchestrator.py` and `tests/test_cache.py` is deleted, weakened or has its call
      shape changed (`tests/test_cache.py:506`'s `domain="CNN.com"` included — the helper
      canonicalises its host); any test that asserted exact-only **denylist** behaviour is updated in place
      and named in the Implementation Notes with the reason.
- [x] `grep -rn -iE "exact-host|exact host|exact, case-insensitive" docs kit_tools/docs
      kit_tools/arch README.md` returns nothing (nine line sites at the start); the six files
      state the two-direction rule (`grep -c notevil.com` ≥ 1 in `docs/configuration.md`,
      `kit_tools/docs/API_GUIDE.md` and `kit_tools/arch/SECURITY.md`); the multi-tenant caution is on
      the `trusted_domains` and `verified_domains` rows with each tier's consequence; the
      `seed_blocklist` row carries the upgrade note (subdomain widening; single-label entries
      unchanged) and the observability sentence; the `news_domains` row carries its note; MONITORING
      has the `config_invalid_value` row and LOGGING.md lists the marker; `kit_tools/docs/GOTCHAS.md`
      states that `url_validator.py` is an unhashed cache-key input.
- [x] 1.3.0 window (R36): the docstring line for the three list descriptions is appended in the
      `* ``1.3.0`` — …` format; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS` (a no-op — `RetrieveRequest`
      is outside `_SCHEMA_MODELS`, so the golden does not move); nothing appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF` (a description-only move; the gates are
      `tests/test_contract_export.py`'s drift check and the description test below — R8 corrected,
      round 5); the four anchor-quoting pages refreshed; `uv run python -m
      scripts.export_contract --check` green; `tests/golden/contract_1_2_0.json` unchanged; a test
      reads `contract/openapi.yaml` and asserts the multi-tenant caution appears in **both** the
      `trusted_domains` and `verified_domains` descriptions, that the `trusted_domains` one says
      classification is skipped, that the `verified_domains` one says the tier degrades open when
      the classifier is unavailable, and that both name `policy_suffix_trusted_skip`; the
      GOVERNANCE recorded ruling names Example 6, the declined step-1 alternative, the upgrade note
      as the step-2 window, the absence of any single-label loosening, the `blocked_domain` →
      `private_ip` precedence swap and the new `policy_domain_list_too_large` reason, is a
      `### (<letter>) ` section with a `**Source:**` line, and `_RULING_MARKERS` plus the four count
      words are updated as found.
- [x] The `sanitizer_revision` rotation (`pipeline/orchestrator.py`, `pipeline/contract.py`) is
      measured by revert-and-reproduce and recorded at the five sites (`docs/bootstrap-notes.md`,
      `CLAUDE.md`, `kit_tools/arch/DECISIONS.md` table and preamble as found,
      `kit_tools/docs/GOTCHAS.md` counter and table, `kit_tools/arch/CODE_ARCH.md`).
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-007: Request domain lists normalised once per request, with the `policy_invalid_domain_entry` and `policy_suffix_trusted_skip` counters

**Priority:** P1

**Description:** As a consumer, I want `/retrieve`'s three request lists normalised once in the
handler under a written-down byte budget, and as an operator I want to see dropped entries and
wildcard-caused classifier skips on `/metrics`, so that a truncated or malformed list is observable
and a leading-dot trust entry's blast radius is measurable rather than asserted. This story moves
the entry normalisation the sites ran inline (US-001's interim per-comparison
`normalize_domain_entries` call) into the handler and makes its drops observable.

**Independent Test:** A `/retrieve` request whose `trusted_domains` holds 70 entries including `"com"`
and `" .Example.COM. "` applies every valid normalised entry (69 — no entry count is enforced) and
`/metrics` `retrieve.policy_invalid_domain_entry` advances by one, with no 422; a `trusted_domains`
list whose raw entries join to more than `policy_domain_entries_max_bytes` (64 KiB by default)
applies the entries up to the budget boundary, counts the rest under the same counter, and returns
no 422; a `/retrieve` request carrying 500 valid junk `blocked_domains` entries plus a
`seed_blocklist` domain in config still refuses that domain (the operator list merges first and
every one of the 500 is enforced); a `blocked_domains` list whose raw entries join to more than the
budget is refused with 422 `content_too_large`, reason `policy_domain_list_too_large`, before any
entry is encoded (a patched `canonicalize_host` records zero calls), and the `/metrics` retrieve
error count for `content_too_large` advances (keyed on the code, as
`test_metrics_retrieve_error_keys_on_error_code_not_reason` pins); a `/retrieve` for
`www.example.com` with `trusted_domains: [".example.com"]` and a loaded classifier reports
`promptguard_state == "skipped_trusted"` and advances `retrieve.policy_suffix_trusted_skip` by one,
while `["www.example.com"]` advances it by zero, and `verified_domains: [".example.com"]` with the
classifier unavailable advances it by one as well; `/metrics` serves
`search.policy_invalid_domain_entry` and `search.policy_suffix_trusted_skip` at `0` before US-002
lands.

**Implementation Hints:**
- `/retrieve`'s three lists (`models.py:249-257`) are normalised in the handler and reach the
  pipeline as canonical strings through the **single** `body.model_copy(update={...})` replacement
  spec 2 US-005 introduced for the resolved policy values (R24; `feature-hardening-retrieve-parity.md`,
  US-005 hints) — one replacement carrying the normalised lists and the resolved threshold /
  fail-closed, never a second. `run_retrieve_pipeline` takes only `(request, cache, classifier,
  config, sanitizer_revision)` (`pipeline/orchestrator.py:208-215`) and reads the lists off the
  request at `:248`, `:254-255` and `:363-364`; no new kwarg; `model_copy(update=)` does not
  validate, which is fine because the replacement values are `list[str]`.
- Budget rule (R8 corrected, round 4; R44 — bounds on caller lists are **byte budgets, never entry
  counts**): a new top-level `config.yaml` key `policy_domain_entries_max_bytes` (default 65536,
  range 4 KiB – 1 MiB, read at boot beside `promptguard_threshold_from_config` through spec 2
  US-001's `pipeline/config_bounds.bounded_int` with the same warn-and-fall-back treatment —
  `config_invalid_value — key=policy_domain_entries_max_bytes`, default applied, never a boot
  refusal — published on `app.state.policy_domain_entries_max_bytes`; registered in
  `KNOWN_CONFIG_KEYS` by US-003; a `docs/configuration.md` top-level row here) bounds **each**
  request list separately, measured with `domain_list_bytes` on the raw entries **before any
  normalisation** — the reason it is a byte bound: `DocumentSizeLimitMiddleware` returns
  immediately for every path but `/extract` (`retrieval_app.py:1041`), so `/retrieve` and `/search`
  accept bodies of any length, and an entry count applied after normalisation would encode every
  entry before dropping any. Allowlists (`trusted_domains`, `verified_domains`) →
  `normalize_domain_entries(entries, denylist=False, budget_bytes=B)`: consumed in order up to the
  budget boundary, the remainder dropped and counted (truncating an allowlist only narrows
  privilege), no 422. Denylist (`blocked_domains`) → measured first; over budget → `raise
  PipelineError(error="content_too_large", reason=POLICY_DOMAIN_LIST_TOO_LARGE, request_id=...)`
  inside the handler's existing `try`, so the metrics recording path keys it on the code (the
  `/search` handler's `POLICY_EXCLUDED_ALL_PROVIDERS` precedent, `retrieval_app.py:1801-1809`);
  under budget → `normalize_domain_entries(entries, denylist=True, budget_bytes=None)` — a denylist
  is **never partially enforced**. `POLICY_DOMAIN_LIST_TOO_LARGE = "policy_domain_list_too_large"`
  is declared in `pipeline/contract.py` beside `POLICY_EXCLUDED_ALL_PROVIDERS` (`:318`) with a
  docstring naming both routes' codes — `content_too_large` on `/retrieve` (the request's own bytes
  are what is over a bound; its `RetrieveErrorCode` docstring gains that second raise site) and
  `search_unavailable` on `/search` (whose "exactly two shapes" docstring becomes three) — declared
  per route (ruling 42). The merge in `run_retrieve_pipeline` (`:247-252`) becomes operator-first:
  `seed_blocklist` (already canonical, US-001) then the caller's list; the budget applies to the
  caller's list alone, so a criterion proves no caller entry can evict an operator entry. Delete
  US-001's interim per-comparison `normalize_domain_entries(...)` call at the three sites in the
  same story (a criterion counts the call sites); the host-side `canonicalize_host` call at each
  site stays — it is idempotent and is what keeps a direct caller such as `tests/test_cache.py:506`
  green. The budget bounds **encode work only** (round 5): FastAPI has already parsed the whole
  JSON body into `list[str]` before the handler measures anything, so body-size admission on
  `/retrieve` and `/search` stays unbounded and is not this spec's (Out of Scope names it) — never
  cite this budget as the request-body DoS answer.
- Counters: `SearchMetrics` (`retrieval_app.py:875`) and `RetrieveMetrics` (`:903`) are plain
  `__init__` counter classes, not dataclasses; each gains `policy_invalid_domain_entry` and
  `policy_suffix_trusted_skip`; `RetrieveMetricsResponse` (`:538`) and `SearchMetricsResponse`
  (`:488`, both `extra="forbid"`) gain the matching `Field(description=...)`; the `/metrics` handler
  dict lines (search section ~`:1524`, retrieve section beside it) gain both. The guards that prove
  the three edits move together are
  `tests/test_contract_metrics.py::test_an_unmodeled_counter_fails_loudly` (`:229`) and
  `::test_served_metrics_are_the_handlers_dict_serialized` (`:135`) — **not** the dataclass-parity
  test at `:305`, which covers only `CacheMetrics`/`ModelMetrics`. `search.*` is added now (US-002
  increments `policy_invalid_domain_entry`; `search.policy_suffix_trusted_skip` stays `0` — `/search`
  has no trust tiers — and is documented as reserved for parity) so the `/metrics` document moves
  once for this concern.
- `policy_suffix_trusted_skip` increments when the tier resolved to `trusted` **or** `verified`
  through a wildcard (leading-dot) entry: the pipeline calls `matched_entry(domain,
  request.trusted_domains)` (or `verified_domains`) only when `_resolve_request_trust_tier` returned
  that tier — the resolver itself is untouched (R8 corrected, round 4) — and increments when the
  returned entry starts with `.`, beside the existing `promptguard_state` accounting; the
  `/metrics` description says it covers both tiers (a wildcard `verified` entry degrades open under
  classifier unavailability, which is why it is counted). One counter conflates "invalid" and
  "over-budget" allowlist drops deliberately: both only narrow an allowlist (safe), and a denylist
  is never truncated — over budget it is refused, so nothing fail-open is left to separate; say so
  in the MONITORING row and the TROUBLESHOOTING section.
- Contract window (R36 — copy exactly): one `* ``1.3.0`` — …` docstring line for the four counters
  and the new `policy_domain_list_too_large` reason on `/retrieve`'s `content_too_large` (read
  `pipeline/contract.py:26-68` for the shape); `uv run python -m scripts.export_contract`;
  re-create `tests/golden/contract_1_3_0.json` via `_SCHEMA_MODELS`; **nothing** is appended to
  `_EXPECTED_ONE_THREE_ZERO_DIFF` — the `*MetricsResponse` models are not in `_SCHEMA_MODELS`
  (`tests/test_contract_schema.py:28-35`; `test_the_1_1_0_to_1_2_0_diff_has_no_unlisted_additions`
  even asserts `"MetricsResponse" not in _SCHEMA_MODELS`, `:232`), so the four counters are
  golden-invisible by the 1.2.0 precedent (`_EXPECTED_ONE_TWO_ZERO_DIFF`, `:99-109`, holds none of
  the three 1.2.0 `/metrics` counters), and the reason string is not a schema path either (R8
  corrected, round 5 — the round-4 instruction to append them could never be satisfied). The
  counters' gates are `test_an_unmodeled_counter_fails_loudly` (`:229`),
  `test_served_metrics_are_the_handlers_dict_serialized` (`:135`), `--check`, and the field-set pin
  `tests/test_contract_schema.py::test_search_metrics_response_1_2_0_field_set_is_pinned_exactly`
  (`:368`), which pins `SearchMetricsResponse`'s set **exactly** — extend its set by the two
  `search.*` names as found (spec 2 US-006 extends it first) and add the `RetrieveMetricsResponse`
  twin beside it, pinned to the post-story set (no retrieve twin exists today — verified); refresh
  the four anchor-quoting pages; `--check` green.
  MONITORING `### retrieve` and `### search` counter tables gain rows on the
  `policy_unknown_provider` row's shape (`:150`): per-entry unit (one increment per dropped entry,
  not per request), invalid entries and the allowlist byte budget as the two causes, the offending
  entry never stored; the suffix-skip row states what it measures and that it covers both tiers.
  `docs/configuration.md` gains the `policy_domain_entries_max_bytes` row (default, range, the
  per-list unit, and that a denylist over budget is a 422 while an allowlist is truncated).
  `kit_tools/docs/TROUBLESHOOTING.md` gains a narrative `### policy_invalid_domain_entry` section
  (what makes it tick; the two causes; why the entry is not logged; the per-route difference —
  `/retrieve` counts invalid and over-budget allowlist drops, `/search` counts invalid denylist
  entries only; an over-budget denylist is a 422 with reason `policy_domain_list_too_large` on
  either route, never a count; what to check on the consumer side), matching the treatment US-003
  gives `config_unknown_key`.
- Rotation (R32): `pipeline/orchestrator.py` (the merge order and the wildcard flag) and
  `pipeline/contract.py` (docstring line) move; record on the five-site protocol.
- Tests (R40): the four `/retrieve` handler tests in `tests/test_app.py` —
  `test_metrics_retrieve_records_cache_hit_and_promptguard_state` (`:548`),
  `..._records_blocked_by_reason_from_diagnostic` (`:583`),
  `..._blocked_by_reason_unknown_diagnostic_buckets_to_other` (`:615`),
  `..._error_keys_on_error_code_not_reason` (`:646`) — every one patches
  `retrieval_app.run_retrieve_pipeline` with an `AsyncMock`, so the "lists reach the pipeline as
  canonical strings, normalised once" assertion reads the request object off `mock.call_args` (the
  round-3 anchors `:1523`, `:1593`, `:1734` were a `/health` Valkey test, a `_select_cache_storage`
  test and a lifespan test — corrected in round 4); the end-to-end cases (500 caller entries plus a
  `seed_blocklist` domain still refusing; the budget refusal's 422 body) belong in
  `tests/test_orchestrator.py`, which drives `run_retrieve_pipeline` for real with `_SAMPLE_CONFIG`
  (`:702-708`), and in `tests/test_app.py` for the handler-raised 422; `tests/test_contract_metrics.py`;
  `tests/test_orchestrator.py` trust-tier test at the name above.

**Acceptance Criteria:**
- [ ] The `/retrieve` handler contains exactly one `body.model_copy(update=...)` call, carrying the
      three normalised lists together with the resolved threshold / fail-closed values (assert on
      the handler function's source, not a whole-file `grep -c`); the pipeline never normalises;
      `grep -c "normalize_domain_entries(" url_validator.py` returns 1 (the definition) and `grep -n
      "normalize_domain_entries(" pipeline/orchestrator.py cache.py` returns nothing (US-001's
      interim per-comparison pass is gone; the lifespan and handler calls live in `retrieval_app.py`).
- [ ] 70 `trusted_domains` entries including `"com"` → 69 valid normalised entries applied (no entry
      count), `retrieve.policy_invalid_domain_entry` advanced by one, no 422; an allowlist over
      `policy_domain_entries_max_bytes` → entries up to the budget boundary applied, the remainder
      counted, no 422; the same for `verified_domains`; `grep -n "budget_bytes=" retrieval_app.py`
      shows the two allowlist calls and `grep -rn "limit=64\|limit=4096" retrieval_app.py pipeline
      url_validator.py` returns nothing.
- [ ] 500 valid junk `blocked_domains` plus a config `seed_blocklist` domain → the seed domain is
      refused and all 500 caller entries are enforced; a `blocked_domains` list over the budget →
      422 `content_too_large` with reason `policy_domain_list_too_large`, raised before any entry is
      canonicalised (a patched `canonicalize_host` records zero calls) and recorded on `/metrics`
      under the code; `POLICY_DOMAIN_LIST_TOO_LARGE` lives in `pipeline/contract.py` and
      `content_too_large`'s docstring names the second raise site; the merge in
      `pipeline/orchestrator.py` lists the operator entries first; `policy_domain_entries_max_bytes`
      is read at boot with `bounded_int`, a malformed value warns (`config_invalid_value`) and falls
      back to 65536, and the shipped `config.yaml` carries the key.
- [ ] `retrieve.policy_suffix_trusted_skip` advances by one for a wildcard-caused trusted skip, by
      one for a wildcard-caused `verified` resolution, and by zero for an exact-entry match of
      either tier; `promptguard_state` is `skipped_trusted` for both trusted cases;
      `_resolve_request_trust_tier`'s signature and `str` return are unchanged and `matched_entry`
      is called only on the trusted/verified branches (a `standard` resolution never calls it).
- [ ] All four counters exist on the counter classes, the response models and the handler dicts;
      `test_an_unmodeled_counter_fails_loudly` and `test_served_metrics_are_the_handlers_dict_serialized`
      pass; `GET /metrics` serves the `search.*` pair at `0`.
- [ ] MONITORING has the four rows with the per-entry unit and the two causes;
      `docs/configuration.md` has the `policy_domain_entries_max_bytes` row; TROUBLESHOOTING has the
      `### policy_invalid_domain_entry` section with the two causes, the per-route difference and
      the 422 sentence.
- [ ] 1.3.0 window (R36): docstring line appended; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; **nothing** appended to
      `_EXPECTED_ONE_THREE_ZERO_DIFF` (the `*MetricsResponse` models are outside `_SCHEMA_MODELS`;
      the new reason is announced in the docstring line and not appended — R8 corrected, round 5);
      the search field-set pin (`tests/test_contract_schema.py:368`) is extended by the two names
      and a `RetrieveMetricsResponse` twin pinned to the post-story set exists; the four
      anchor-quoting pages refreshed; `--check` green; `contract_1_2_0.json` unchanged.
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
`fallback_fired is False`, the paid fake's `calls == []`, and one INFO record per omission whose
`getMessage()` starts with `search_url_blocked host_class=policy_blocklist provider=` (spec 1
US-003's two-token line and level — `logger.info("search_url_blocked host_class=%s provider=%s",
host_class, provider_name)`, modelled on the `search_provider_failed` WARNING's shape at
`pipeline/orchestrator.py:907-908`; asserted as a `startswith` / substring check on `getMessage()`,
never exact equality — round 5; no em-dash, no URL, no host); with no request field and `_load_config` patched to
carry `seed_blocklist: [" Blocked.Example. "]` the same two results are omitted; with neither, the
response's `results`, `omitted_by_reason`, `fallback_fired`, `provider_used` and `provider_errors`
equal the committed pre-story baseline's.

**Implementation Hints:**
- `SearchRequest.blocked_domains: list[str] = Field(default_factory=list, ...)` beside
  `providers`. **No pydantic `pattern`, `max_length` or item validation** — a FastAPI 422 would echo
  the caller's bytes (search-epic ruling 29). The handler measures the list with `domain_list_bytes`
  against `app.state.policy_domain_entries_max_bytes` (US-007's budget) and, over budget, raises
  `PipelineError(error="search_unavailable", reason=POLICY_DOMAIN_LIST_TOO_LARGE, ...)` inside the
  same `try` as the `POLICY_EXCLUDED_ALL_PROVIDERS` raise (`retrieval_app.py:1801-1809`), before
  any entry is encoded; under budget it runs `normalize_domain_entries(..., denylist=True,
  budget_bytes=None)` (canonical strings out) and increments
  `SearchMetrics.policy_invalid_domain_entry` by the dropped count. `pipeline/contract.py`'s
  `POLICY_EXCLUDED_ALL_PROVIDERS` docstring ("exactly two shapes", `:319-325`) becomes three
  shapes, naming the new literal. `SearchErrorCode`'s docstring (`pipeline/contract.py:261-274`,
  "`POST /search` upstream failure codes"; `search_unavailable` as "the general code for every
  other chain") is amended in the same edit to say `search_unavailable` also carries the two
  per-request policy refusals (`policy_excluded_all_providers`, `policy_domain_list_too_large`),
  which are permanent client errors — **not retryable** — distinguishable by `reason`; the
  API_GUIDE `/search` error row says the same, because a consumer that treats the code as
  transient would retry a request that can never succeed (round 5; ruling 42 keeps the code).
- Merge the operator list first: `run_search_pipeline` already receives `config=`
  (`retrieval_app.py:1815`, signature `pipeline/orchestrator.py:781`); read the canonical
  `config.get("seed_blocklist", [])` (US-001 normalised it at boot), then the request list, into
  one `effective_blocklist`; thread the request list in as `blocked_domains: Sequence[str] = ()`
  (canonical strings, R8 corrected) — the **only** channel: `run_search_pipeline` reads the list
  solely from that parameter and never from `request.blocked_domains`, even though the whole
  request is in scope (a criterion asserts the function's source contains no
  `request.blocked_domains` read, mirroring US-005's `request.promptguard_threshold` guard —
  round 5). No caller entry can evict an operator entry (criterion).
- In the per-result loop, immediately after `_canonicalize_search_url` yields `domain` (already
  canonical from spec 1 US-003 — do not re-canonicalise) and after spec 1's audit, test
  `any(hostname_matches(domain, e, allow_suffix=True) for e in effective_blocklist)` (`domain` is
  canonical and encoded once; `hostname_matches` never encodes) and omit with
  `contract.OMIT_BLOCKED_URL` (spec 1 US-004's token; assert `OMISSION_REASONS` carries it before
  starting) through the reason channel spec 1 US-002 added to the canonicaliser's return.
  Sufficiency for fallback is judged on raw provider results before this omission (search-epic
  ruling 17). Log through spec 1 US-003's content-free INFO line, spelled exactly as spec 1 emits
  it (`search_url_blocked host_class=%s provider=%s`; both stories assert on `getMessage()`
  substrings), with a new token `policy_blocklist`, so logs distinguish "SearXNG returned 169.254.169.254" from "the
  operator blocked reddit.com"; the MONITORING row says `policy_blocklist` omissions are expected
  operator policy, not a suspicious-host signal.
- Baseline: before touching the handler, capture `tests/fixtures/search/baseline_pre_blocked_domains.json`
  from the current code for the three fake results at threshold `0.85`, holding only the five
  fields the story can disturb (`results`, `omitted_by_reason`, `fallback_fired`, `provider_used`,
  `provider_errors`); add a `tests/fixtures/README.md` section (when captured, from which commit,
  the three fake results, the threshold, that it is deliberately **not** regenerable after this
  story, and what a later story does when it goes red: a change in these five fields for a request
  sending neither new field is a behaviour change that story must classify under GOVERNANCE before
  editing the fixture). Whole-response equality is not asserted — later window stories add fields.
- Contract window (R36 — copy exactly): one `* ``1.3.0`` — …` docstring line for `blocked_domains`;
  the field description states the semantics; `uv run python -m scripts.export_contract`;
  `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; `blocked_domains` appended to
  `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four anchor-quoting pages refreshed; `--check` green. The
  seven boundary-text copies that name `blocked_domains` as `/retrieve`-only are updated for this
  knob (US-004 later pins all seven by test). They are the explicit seven-site list US-004 carries —
  `models.py:235` and `:287` (class docstrings), `retrieval_app.py:1588` and `:1792` (handler
  docstrings), `kit_tools/docs/API_GUIDE.md:216-225` (the phrase wraps across two lines, so a
  line-oriented grep misses it), `docs/configuration.md:28-37`, `README.md:58-59` (the two route
  table rows, which never contain the phrase) — located by `grep -rn -i "today's divergence\|documents
  today" models.py retrieval_app.py docs/configuration.md` (five hits) plus the API_GUIDE and README
  sites by their line anchors; the criterion names the five files, not a count.
- `kit_tools/docs/API_GUIDE.md`'s `/search` response-fields table enumerates the `omitted_by_reason`
  keys (`:259`); spec 1 US-004 owns adding `blocked_url` there — verify it did, and if the
  enumeration is still the four old tokens, add it here and note the hand-off in Implementation
  Notes.
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
      test sends 70 entries including `" BLOCKED.example. "`, `"com"` and `"bad..entry"` and asserts
      the match fires, `com` is applied exact-only (a result on `com` itself is omitted, one on
      `a.com` is not), `search.policy_invalid_domain_entry` advances by one (for `bad..entry`), and
      no 422 occurs; 500 valid caller entries plus a `seed_blocklist` domain still omit the seed
      domain; a list over `policy_domain_entries_max_bytes` → 422 `search_unavailable` with reason
      `policy_domain_list_too_large`, raised before any entry is encoded, and the seed domain is
      never partially enforced; `run_search_pipeline`'s source contains no `request.blocked_domains`
      read (the `blocked_domains=` parameter is the only channel); `SearchErrorCode`'s docstring
      names the two policy refusals as non-retryable.
- [ ] A result whose `domain` matches a request entry or a `seed_blocklist` entry is omitted with
      `blocked_url`, counted in `omitted_by_reason`, after URL canonicalisation and before stage 2/3;
      `fallback_fired` is `False` and the paid fake's `calls == []`; each omission logs one INFO
      record whose `getMessage()` starts with `search_url_blocked host_class=policy_blocklist
      provider=` and carries no URL and no host (substring check, never exact equality).
- [ ] The committed baseline's five fields equal the response's for a request sending no
      `blocked_domains` and no `promptguard_threshold`; `tests/fixtures/README.md` has the section.
- [ ] 1.3.0 window (R36): docstring line appended; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; `blocked_domains` appended
      to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four anchor-quoting pages refreshed; `--check` green;
      `contract_1_2_0.json` unchanged; none of the seven boundary-text copies in `models.py`,
      `retrieval_app.py`, `kit_tools/docs/API_GUIDE.md`, `docs/configuration.md` and `README.md` calls
      `blocked_domains` a `/retrieve`-only knob (each of the five files is checked by name).
- [ ] `kit_tools/docs/API_GUIDE.md` has the `/search` request row (`grep -c blocked_domains
      kit_tools/docs/API_GUIDE.md` ≥ 2) and its `omitted_by_reason` enumeration includes
      `blocked_url`; MONITORING's `omitted_by_reason` row names the three causes and the token and
      says `policy_blocklist` is expected policy; `docs/configuration.md`'s `seed_blocklist` row
      says both routes.
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
`retrieval_app.py:1703-1713` is untouched); a config carrying `promptguard_threshold: "abc"`,
`1.7` or `true` boots, logs one `config_invalid_value — key=promptguard_threshold` WARNING, and
resolves to `0.85` (a YAML boolean is rejected, never coerced to `1.0`); a YAML-quoted `"0.85"`
boots without a WARNING and resolves to `0.85`; `kit_tools/docs/API_GUIDE.md`'s `/search` request
table carries a `promptguard_threshold` row and its response table an
`effective_promptguard_threshold` row.

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
  `retrieval_app.py` beside the resolver **reuses `pipeline/config_bounds.bounded_float`** (spec 2
  US-001's shared reader, which takes the exception class as a parameter and keeps the
  `isinstance(value, bool)` rejection every bounded reader in the repo carries — `cache.py:260`,
  `brave.py:217`, `:236`, `extraction_limits.py:89`): call it with `0.0`–`1.0`, catch its typed
  error, log `config_invalid_value — key=promptguard_threshold` (key only, never the value) and
  return `0.85`; absent → `0.85`. A YAML boolean (`promptguard_threshold: true`) is therefore
  invalid, never `1.0` (`float(True)` would disable blocking). Back-compat with `/extract`'s
  `float(...)`-coercion of a quoted string (`:1702-1704`): coerce a `str` to `float` **before**
  handing it to `bounded_float`, so `"0.85"` still resolves; a non-numeric string is invalid.
  **Never refuses boot** (R10 corrected: a value that boots today must still boot). Publish the
  validated default on `app.state.promptguard_threshold_default`; the resolver never re-reads
  `config`. The raw config value is left in place, so `/extract`'s guard and
  `pipeline/sanitizer_revision.py:41`'s hash input are unchanged. **The `/extract` divergence is
  documented, not closed** (round 4): `/extract`'s own read (`retrieval_app.py:1702-1704`,
  `float(...)` then the `0.0 <= threshold <= 1.0` guard) turns a YAML boolean `true` into `1.0`,
  which passes its range check — so after this story `promptguard_threshold: true` warns and
  resolves to `0.85` on `/retrieve` and `/search` while `/extract` classifies at `1.0`; ruling R10
  keeps `/extract` out of the resolver, so the divergence is pinned by a test and stated in
  `docs/configuration.md`'s `promptguard_threshold` row (the three routes read the key differently,
  and which reading each gets) and in `kit_tools/docs/GOTCHAS.md` (a YAML boolean disables blocking
  on `/extract` only); closing it is an open question. The boot signal itself names the exception
  (round 5): for this key the `config_invalid_value` line carries a second sentence,
  `/extract reads the raw value through its own guard`, so an operator never reads one WARNING as
  a service-wide rejection. `derive_sanitizer_revision`'s docstring
  (`pipeline/sanitizer_revision.py`, "active threshold") is corrected to say it hashes the
  *configured* value and that the *active* threshold reaches the cache key through
  `cache_policy_fingerprint` — the file is not hashed, so this rotates nothing (round 5). Log the resolved default once at
  boot at **INFO**, `promptguard_threshold_resolved — value=%s` (not a credential); it joins
  MONITORING's "Dropped (INFO)" line and LOGGING.md's inventory (a criterion), and the
  `config_invalid_value` marker joins LOGGING.md's `retrieval_app` inventory line and its
  "Operator-facing misconfiguration at boot" bullet if US-001 has not already added it.
- The cache fingerprint (`cache.py:120`) receives the *resolved* threshold, never `None`; assert the
  same URL fetched with `null`, with an explicit value equal to the config default, and with the
  ceiling applied produces the expected keys.
- Contract window (R36): one docstring line ("`promptguard_threshold` added to `SearchRequest`;
  `null` means the server's configured threshold on both routes; the operator ceiling applies on
  `/search`; `effective_promptguard_threshold` on `SearchResponse`"); `uv run python -m
  scripts.export_contract`; `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; both
  fields appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four anchor-quoting pages refreshed;
  `--check` green. Update every copy that says `/search` has no per-request threshold or that the
  config value "is not applied" on a route — find them by value with the pattern that catches
  every spelling: `grep -rn -iE "not applied (here|there|on this route)|no per-request threshold"
  models.py retrieval_app.py kit_tools/docs/API_GUIDE.md docs/configuration.md README.md` (the
  line-oriented grep finds seven: `models.py:234`, `:284`, `kit_tools/docs/API_GUIDE.md:222`, `:241`,
  `:243`, `docs/configuration.md:37`, `README.md:58` — but there are **nine sites in five files**:
  the two it cannot see wrap across a line in the `/retrieve` and `/search` handler docstrings,
  `retrieval_app.py:1587-1588` ("is not / applied there") and `:1789-1790` ("is not applied /
  here"), the two copies FastAPI renders into `paths['/retrieve'|'/search'].post.description`; the
  criterion is therefore a whitespace-normalising test, not the grep — round 5, codebase fit; the
  criterion names the five files). `kit_tools/docs/
  API_GUIDE.md`'s "Request fields (`SearchRequest`)" table (`:229`) gains a `promptguard_threshold`
  row (`float | null`, default `null` = the server's configured threshold, bounded by
  `promptguard_threshold_ceiling`) and its "Response fields to read (`SearchResponse`)" table
  (`:250`) gains `effective_promptguard_threshold`. GOVERNANCE "Recorded rulings" gains the
  classification: the
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
- [ ] A config `promptguard_threshold` of `"abc"`, `-0.1`, `1.7` or `true` boots, logs exactly one
      `config_invalid_value` WARNING naming the key and never the value, and resolves to `0.85`
      (`grep -n "bounded_float" retrieval_app.py` shows the reader is the shared one; for this key
      the WARNING's `getMessage()` also names `/extract`'s own read); a quoted
      `"0.85"` resolves to `0.85` with no WARNING; an absent key resolves to `0.85`; the resolved
      default is logged once at INFO as `promptguard_threshold_resolved`, which MONITORING's
      "Dropped (INFO)" line and LOGGING.md's inventory name.
- [ ] The cache-key assertions (null vs explicit default vs ceiling) pass.
- [ ] 1.3.0 window (R36): docstring line appended; `uv run python -m scripts.export_contract` run;
      `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`; the new field and the
      `SearchResponse` field appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the four anchor-quoting
      pages refreshed; `--check` green; a test that collapses runs of whitespace in each of the five
      files **and** in the four `contract/openapi.yaml` description strings asserts the pattern
      `not applied (here|there|on this route)|no per-request threshold` is absent (nine sites at
      the start, in those five files — two of them line-wrapped in `retrieval_app.py`; the
      line-oriented grep alone returns seven and is not the gate); the GOVERNANCE ruling recorded
      as its own `### (<letter>) ` section with a `**Source:**` line, `_RULING_MARKERS` and the
      count words updated as found (US-001's mechanism); `derive_sanitizer_revision`'s docstring
      says "configured".
- [ ] `kit_tools/docs/API_GUIDE.md`'s `/search` request table has the `promptguard_threshold` row
      and its response table the `effective_promptguard_threshold` row.
- [ ] `docs/configuration.md`'s `promptguard_threshold` row carries the two-direction upgrade note,
      names `promptguard_threshold_ceiling` as the bound and states the three-route reading.
- [ ] A test pins the `/extract` divergence as known-and-unchanged: `promptguard_threshold: true`
      boots with one `config_invalid_value` WARNING, resolves to `0.85` on `/retrieve` and
      `/search`, and still reaches `/extract`'s `float()` coercion at `1.0`;
      `kit_tools/docs/GOTCHAS.md` states it.
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
**unit test on `_warn_unknown_config_keys` alone** (never a boot) shows a top-level YAML list, a
scalar, and a config whose `cache:` value is `"yes"` each return `[]`, log no `config_unknown_key`
record and raise nothing — booting with such a document is unchanged and still refuses through the
block's own reader (`cache.py:275`, `pipeline/extraction_limits.py:105`; R8 corrected, round 4); a
docs-parity test asserts `KNOWN_CONFIG_KEYS`
equals the first-column keys of every `config.yaml` table `docs/configuration.md` carries
(discovered by heading) and is a superset of the shipped `config.yaml`'s keys; an AST sweep asserts
every string-literal key the readers pass to `config.get(...)` / `config[...]` is in the registry.

**Implementation Hints:**
- `KNOWN_CONFIG_KEYS: frozenset[str]` of dotted names next to `_load_config` (`retrieval_app.py:329`).
  Members: every top-level key (`user_agents`, `news_domains`, `seed_blocklist`,
  `promptguard_threshold`, `promptguard_fail_closed_floor`, `promptguard_threshold_ceiling`,
  `promptguard_wait_seconds` (spec 2 US-001's third top-level key, wired by its US-006),
  `policy_domain_entries_max_bytes` (US-007), `search_brave_*`, `extract_route_enabled`, …), every bare block name (`cache`, `extraction`,
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
  made warn-and-fall-back (`promptguard_threshold`, `policy_domain_entries_max_bytes`, the two
  domain lists); the docs paragraph states that split.
- Docs-parity test in `tests/test_contract_metrics.py` (the module `kit_tools/testing/
  TESTING_GUIDE.md:279` maps `docs/configuration.md` to; it already holds `_CONFIGURATION_DOC`
  (`:59`) and `_posture_section()` (`:379`)); the section slicer and cell reader today live as
  private helpers in `tests/test_governance_docs.py` (`_section` `:128`, fence-aware, `startswith`
  + exactly-one-heading assert; `_cells` `:158`) — **borrow them by the repo's own precedent**
  rather than moving them: `tests/test_contract_smoke.py:146` does a function-scoped `from
  tests.test_contract_schema import _SCHEMA_MODELS` inside the test that needs it, with a docstring
  arguing the single-source point; do the same here (`from tests.test_governance_docs import
  _section, _cells` inside the parity test, docstring citing that precedent). The round-3 plan to
  extract both into a `tests/docs_helpers.py` is dropped (round 4): it rewrote 20 call sites in a
  module that is itself a contract guard, for no gain the precedent does not already give.
  Discover the tables under `## `config.yaml``: `### Top-level keys` plus every heading matching
  ``### The `<name>:` block`` (quote headings **with their backticks** — today `### The `cache:`
  block` at `:445` and `### The `extraction:` block` at `:471`; spec 2 adds `retrieve:`); **first
  cell only**; block-table keys are prefixed `<name>.`; the top-level table's block rows register as
  bare names; assert every sliced table is non-empty. Model the shipped-config assertion on
  `tests/test_cache.py::TestCacheSettings::test_the_shipped_config_yaml_pins_the_documented_defaults`.
- Code-parity sweep (the repo's idiom for hand-maintained vocabularies; modelled on
  `tests/test_contract_errors.py:188 _swept_error_codes`, but that sweep walks a flat literal
  vocabulary — this one must follow aliases). Walk the AST of `retrieval_app.py`, `cache.py`,
  `pipeline/extraction_limits.py`, `pipeline/search_providers/brave.py`, `pipeline/orchestrator.py`,
  `pipeline/sanitizer_revision.py`, spec 2's retrieve reader and `pipeline/config_bounds.py`
  (spec 2 US-001). The real call shapes (R8 corrected, rounds 3–4): (a) `config.get("<key>", …)` /
  `config["<key>"]` where the receiver is a bare `config` **or an attribute chain whose last
  attribute is `config`** (`request.app.state.config.get("promptguard_threshold", 0.85)` at
  `retrieval_app.py:1703`, `app.state.config[...]` in the lifespan — normalise the receiver by its
  trailing name, never require a `Name` node) — literal at argument 0; (b) the bounded helpers take
  the **mapping as argument 0 and the key literal as argument 1**: `_bounded_int(cache_config,
  "max_entries", …)` (`cache.py:244-250`, `:279-291`), `_bounded_int(extraction_config,
  "max_input_bytes", …)` (`pipeline/extraction_limits.py:78-85`, `:111-151`), `_bounded_float(config,
  "search_brave_timeout_seconds", …)` (`brave.py:206-212`, `:224-230`, `:254-273`), and spec 2's
  `bounded_int` / `bounded_float`; (c) the dotted prefix is recovered by tracking, within each reader
  function, the locals bound from `config.get("<block>", …)` (`cache_config` at `cache.py:274-277`,
  `extraction_config` at `extraction_limits.py:104-107`, spec 2's `retrieve_config`) and prefixing
  every literal read through such a local with `<block>.`; reads through the top-level `config`
  stay bare (`brave_settings_from_config` passes `config` straight through, so its keys are
  top-level). Assert the collected set is a subset of `KNOWN_CONFIG_KEYS`, **and** that the sweep
  found at least the **eight** literal-key shape-(a) read sites counted today
  (`retrieval_app.py:1703`, `cache.py:274`, `pipeline/orchestrator.py:249`, `:262`, `:307`,
  `sanitizer_revision.py:41`, `extraction_limits.py:100`, `:104`; the four variable-key sites
  `cache.py:259`, `brave.py:215`, `:237`, `extraction_limits.py:88` are a named skip list and never
  count — round 3 wrote "twelve" against a list of which four were skipped, an arithmetically
  unsatisfiable floor) plus at least one shape-(b) site per bounded reader, at least one dotted
  leaf per registered block, **and at least one literal key in every module of the list** (a reader
  the walk cannot parse — one that binds its block mapping through a helper call, say — is then
  red, not silent; the aggregate floor alone stays satisfied by the pre-existing sites — round 5),
  so an empty or broken walk goes red; the module list is hand-maintained, and the test's docstring
  says that adding a config reader means adding its module here; a companion test plants an
  unregistered `config.get("planted_key")` read in a temporary module and asserts the sweep
  reports it.
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
      included, and `policy_domain_entries_max_bytes`) in `retrieval_app.py`; a test asserts it is a
      superset of every key in the shipped `config.yaml`.
- [ ] The docs-parity test discovers every `config.yaml` table by heading (backticks verbatim),
      asserts each slice is non-empty, and asserts `KNOWN_CONFIG_KEYS` equals the union of their
      first-column keys with the prefixing rule (a key without a row, or a row without a key, is
      red).
- [ ] The AST sweep over the reader modules reads the key from the bounded helpers' second argument,
      prefixes leaves read through a block-local mapping with the block name, matches the
      attribute-chain receiver shape, asserts every literal config key read is in the registry,
      asserts it found ≥ eight literal-key read sites (the named list, with the four variable-key
      sites in a named skip list), ≥ one shape-(b) site per bounded reader, ≥ one dotted leaf per
      registered block and ≥ one literal key in every listed module, and reports a planted
      unregistered key.
- [ ] Booting with an unknown top-level key, an unknown `extraction.` key and an unknown `retrieve.`
      key logs exactly one WARNING per key whose `getMessage()` contains `config_unknown_key` and the
      dotted key and never the value; the service starts; `/health` status is unchanged.
- [ ] `_warn_unknown_config_keys` called directly with a list, a scalar, and a mapping whose
      `cache:` value is not a mapping returns `[]`, logs no `config_unknown_key` record and raises
      nothing (a unit test on the function — never a boot: the readers' typed refusals for a
      non-mapping block are unchanged, and a test asserts `cache: "yes"` still refuses boot with
      `CacheConfigurationError`).
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
  `tests/test_governance_docs.py::_section` asserts exactly one heading. **Placement per file**
  (round 4): in `kit_tools/docs/API_GUIDE.md` and `docs/configuration.md` the fences wrap the
  boundary paragraph; in `README.md` the copy is two rows of the HTTP-surface GFM table (`:58-59`),
  and an HTML comment on its own line inside a table body terminates the table — so the README
  fences wrap the **whole** table, and the test then keeps only the two rows whose first cell is
  `` `POST /search` `` / `` `POST /retrieve` ``, so a name in an unrelated row (`/health`,
  `/metrics`, `/extract`) cannot satisfy the guard (round 5). A fence line must never land between
  two rows of a table.
- After US-002 and US-005 the sets are: `/retrieve`-only `cache_ttl_hours`, `extract_mode`,
  `trusted_domains`, `verified_domains`; `/search`-only `allow_paid_fallback`, `num_results`,
  `providers`; shared `blocked_domains`, `promptguard_threshold`, `promptguard_fail_closed` —
  verify from `model_fields` at implementation time rather than from this list. Rewrite all seven
  copies to enumerate the route-specific knobs by backticked name and to name the shared ones in
  one sentence opening with the fixed lead-in `Shared by both routes:` — the guard slices on that
  lead-in, so every `_SHARED` name must sit inside that sentence and a shared knob described
  anywhere else in the copy is red by construction (there is no `ttl_hours` field; it is
  `cache_ttl_hours`).
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
- [ ] All seven copies name every route-specific knob and mark the shared ones: every `_SHARED`
      name appears inside the one sentence each copy opens with the fixed lead-in `Shared by both
      routes:` (a knob described anywhere else in the copy is red by construction — the negative
      "is not applied" assertion was dropped in round 5 because an absent English phrasing is a
      heuristic that rots); the existing `test_search_and_retrieve_descriptions_name_the_boundary`
      still passes.
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
- Allowlist entry `com`, `.com`, `co` — fewer than two labels after the leading dot is removed →
  rejected by the normaliser (request lists: counted; config lists: one boot WARNING naming it);
  denylist entry `com` or `intranet` → accepted, exact-only (`intranet` blocks `intranet`, never
  `wiki.intranet`); `co.uk` bare on an allowlist matches only the host `co.uk`, on a denylist every
  `.co.uk` host; `.co.uk` on an allowlist is a deliberate operator opt-in (the docs caution names
  this). US-001.
- Empty, whitespace-only, `bad..entry` (caught on the split labels, not by the encode step),
  254-character, or non-IDNA-encodable entry → rejected; never a 422. US-001, US-007, US-002.
- Host `straße.de` versus entry `xn--strae-oqa.de` (and the reverse), `münchen.de` versus
  `xn--mnchen-3ya.de` → match after UTS-46 canonicalisation; an un-canonicalisable host is refused
  at the denylist sites (`invalid_url` on `/retrieve`, the hop refused in stage 5) and matches
  nothing at the allowlist sites. US-001.
- Trailing dots on host or entry → stripped, match. `localhost`, `localhost.`, `anything.localhost`,
  `printer.local`, `deep.sub.myhost.local` → refused at the hostname stage by the private-name
  check, with or without a blocklist; bare `local` stays allowed as today. US-001.
- An IP-literal host (`1.2.3.4`, `2001:db8::1`) matches an entry only by equality at every site;
  `.2.3.4` is an invalid entry. US-001.
- A single-label denylist entry (`seed_blocklist: ["intranet"]`, `blocked_domains: ["wiki"]`) matched
  exactly today and still does — exact-only, never suffix-widened; no upgrade note is needed for
  it. US-001, US-007.
- A host that is both denylisted and private-named (`blocked_domains: ["evil.local"]`,
  `https://evil.local/`) → `private_ip`, not `blocked_domain` (the private-name check now runs
  first; classified in the GOVERNANCE ruling). US-001.
- `verified_domains: [".example.com"]` with the classifier unavailable → every host under the suffix
  is served unscanned (VERIFIED degrades open, including under the operator floor);
  `policy_suffix_trusted_skip` += 1; the exported description says so. US-001, US-007.
- Shipped `config.yaml` after this spec: the `news_domains` entries carry the leading dot, so
  `www.reuters.com` gets the one-hour TTL. US-001.
- An un-canonicalisable fetch host with **no** blocklist configured → still refused by `validate_url`
  (canonicalisation precedes the `if blocked_domains:` block). US-001.
- A host on both `blocked_domains` and `trusted_domains` → `blocked` (existing precedence). US-001.
- A redirect hop onto a subdomain of a blocked entry → refused at that hop
  (`pipeline/stage5_url_audit.py:144`). US-001.
- Config `seed_blocklist: [" Evil.COM. "]` / `news_domains: ["BBC.co.uk"]` → normalised at boot;
  still match; an invalid config entry → dropped with one `config_invalid_value` WARNING per list
  (count, never text). US-001.
- A **request allowlist** whose raw entries exceed `policy_domain_entries_max_bytes` → the entries
  up to the budget boundary apply; the rest are counted under `policy_invalid_domain_entry`; no
  422; no entry count is ever enforced (R44); the operator's `news_domains` has no budget. A caller
  denylist under the budget → every valid entry applies; over the budget → 422 with reason
  `policy_domain_list_too_large` before any entry is encoded, never partial enforcement; the
  operator's list is merged first and can never be evicted. US-007 (`/retrieve`), US-002
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
- `config.yaml` lacking `promptguard_threshold` → `0.85`; a quoted `"0.85"` → `0.85`; a non-numeric,
  boolean or out-of-range value → boots, one `config_invalid_value` WARNING, `0.85`; `/extract`'s own
  guard is unchanged and still reachable. US-005.
- Unknown key inside `cache:`, `extraction:` or `retrieve:` → dotted WARNING; an unknown top-level
  block → one WARNING for the block name, its children not walked; a registered block whose value is
  not a mapping → the helper skips it (no WARNING, no exception) and the block's own reader still
  refuses boot; a top-level list or scalar → the helper returns `[]` with no WARNING and no
  exception (the boot outcome for such a document is unchanged and not this story's). US-003.
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
- Any entry-count cap on a caller list (R44); the byte budget is the only bound.
- Rejecting a YAML boolean in `/extract`'s own `promptguard_threshold` read (R10: `/extract` is
  untouched; the divergence is documented and an open question).
- Making boot survive a non-mapping `config.yaml` document or block (the readers' typed refusals
  are the documented posture).
- Body-size admission on `/retrieve` and `/search`: `DocumentSizeLimitMiddleware` returns
  immediately for every path but `/extract` (`retrieval_app.py:1041`), and the byte budget bounds
  encode work only, after FastAPI has parsed the body; request-body bounds on the two routes are
  not this spec's (round 5).

## Assumptions

- Spec 1 US-004 opened the 1.3.0 window and added `OMIT_BLOCKED_URL = "blocked_url"` to
  `pipeline/contract.py`'s `OMISSION_REASONS`; spec 1 US-002 added a reason channel to
  `_canonicalize_search_url`'s return; spec 1 US-003 landed the public host canonicaliser in
  `url_validator.py` (`canonicalize_host(host) -> CanonicalHost | HostRejection`, non-raising —
  R27 corrected) and the `search_url_blocked host_class=` log line; spec 2 **US-005** added
  `promptguard_fail_closed_floor`, `promptguard_threshold_ceiling`, the operator-policy resolver in
  `retrieval_app.py` (R24: `body.model_copy(update={...})`) and the `/retrieve` `effective_*`
  fields; spec 2 added the `config.yaml` `retrieve:` block.
- Poppy is the only consumer; both new `/search` fields default to today's behaviour, so a 1.2.0
  client that never sends them sees no change **provided** its `config.yaml` carries the shipped
  `promptguard_threshold: 0.85` (an operator who tuned it sees the documented upgrade effect in
  either direction).
- Hosts reaching `hostname_matches` are always already canonical: on `/search` from spec 1 US-003;
  on `/retrieve` the pipeline canonicalises `urlsplit().hostname` once where `domain` is derived,
  and `validate_url` canonicalises once at its top — the matcher never encodes.
- The three settings readers stay `.get(key, default)`-shaped; the registry is asserted beside
  them, not woven into them.
- `/retrieve`'s request lists and `/search`'s share one normaliser and one byte-budget rule; the
  `providers` normaliser in `pipeline/search_providers/policy.py:35` (`_MAX_POLICY_ENTRIES = 8` at
  `:17`; no trailing-dot or IDNA rule) is a different function and stays separate.
- `idna` is already installed (httpx's dependency, `uv.lock`); listing it in `pyproject.toml` adds
  no new package.
- The 64 KiB `policy_domain_entries_max_bytes` default is asserted, not measured: the largest
  `blocked_domains` / `trusted_domains` list the one consumer (Poppy) sends today is measured — or
  recorded as "none" — in US-007's Implementation Notes before the default is confirmed, so the
  headroom has a number behind it (round 5).

## Technical Considerations

- **Rotation ledger (R32).** US-001: `pipeline/orchestrator.py` + `pipeline/contract.py`; US-007:
  the same two; US-002: the same two; US-005: `pipeline/contract.py` (+ `orchestrator.py` if the two
  reads are replaced here); US-003, US-004, US-006: none. Each rotating story measures by
  revert-and-reproduce and records at the five sites (`docs/bootstrap-notes.md`, `CLAUDE.md`,
  `kit_tools/arch/DECISIONS.md`, `kit_tools/docs/GOTCHAS.md`, `kit_tools/arch/CODE_ARCH.md`); the
  ordinal is written as "the next" and reconciled at merge if a sibling branch took it first
  (rulings 6 and 32 keep the record per story; the salty engineer's once-per-spec batching is
  overruled because an unrecorded intermediate rotation is exactly what the ledger exists to catch).
- **Carrier type (R8 corrected, round 3).** Canonical strings cross every boundary: the request
  fields stay `list[str]`; `validate_url`, `_resolve_request_trust_tier`, `_effective_ttl_hours` and
  `cache_policy_fingerprint` keep their `list[str]` parameters; `app.state.config` lists hold canonical
  strings; `hostname_matches(host: str, entry: str, *, allow_suffix)` parses the leading dot itself;
  `DomainEntry` is `normalize_domain_entries`'s internal return. No existing signature or test call
  shape changes, and pyright strict needs no ignore.
- **Normalisation owners.** Request lists: the `/retrieve` handler (US-007) and the `/search` handler
  (US-002), through the R24 replacement. Config lists: the lifespan (US-001), written back into
  `app.state.config`. `cache_policy_fingerprint`'s inline `strip().lower()` (`cache.py:148-162`)
  stays as a key-stability normalisation over already-canonical strings (leading dots intact); it is
  idempotent, so the two can never produce different keys for one request. Until US-007 lands, each
  site runs incoming entries through `normalize_domain_entries` and its host through
  `canonicalize_host` itself, so no intermediate commit weakens a denylist for a mixed-case or
  non-ASCII entry; US-007 deletes the entry pass, the host call stays (round 5). On `/search` the
  `blocked_domains=` keyword of `run_search_pipeline` is the authoritative channel (the pipeline
  never reads `request.blocked_domains`); on `/retrieve` the R24 replacement is.
- **Why a misconfiguration is a log line and not a `/health` reason.** `degraded_reasons` is the
  closed vocabulary for *dependency* state (classifier, cache); a typo'd key or an out-of-range
  threshold is an operator input error that ruling 12 made warn-and-continue. The security review
  asked for a runtime-queryable signal; it is recorded as a non-blocking question (a `config`
  section on `/metrics` would be additive) rather than widened here.
- **The rotation is the invalidation across a deploy; the marker is the invalidation within one.**
  `cache_policy_fingerprint` keys on the domain *lists* (with the leading-dot marker) and carries
  `sanitizer_revision`, so a semantics change is invalidated by the rotation and a wildcard entry
  never shares a key with a bare one. The comment above `hostname_matches` says any later semantics
  change must be paired with a rotation.
- **Work bound (round 4).** The UTS-46 cost of a request is bounded twice: the host is encoded once
  per call site (never inside `hostname_matches`), and the raw bytes of each request list are
  measured against `policy_domain_entries_max_bytes` before any entry is encoded — so the encode
  work a caller can buy is at most the budget per list, and a `/search` with 20 results against a
  64 KiB denylist costs 20 host encodes plus one pass over the entries, not entries × results. The
  round-3 claim that a 4,096-entry count closed the amplification was wrong on both counts (the
  host was re-encoded per comparison and the count was applied after normalisation); the budget
  replaces it.
- **Reason precedence in `validate_url`.** Private-name check first, then the denylist: a host that
  is both maps to `private_ip`. The GOVERNANCE ruling classifies the swap; it is a value change
  between two existing 422 codes on one input shape and carries no shape change.
- **Generated files at merge.** Every window story in this epic regenerates `contract/openapi.yaml`,
  re-derives `contract/openapi.yaml.sha256` and re-creates `tests/golden/contract_1_3_0.json`, and
  specs 1, 2 and 4–8 do the same to the same three files. On any merge that conflicts in one of
  them, take neither side: re-run `uv run python -m scripts.export_contract` and re-create the
  golden from `_SCHEMA_MODELS` on the merged tree, then `--check`. A hand-resolved `.sha256` is a
  silently wrong trust anchor (CLAUDE.md invariant 4), which is why the three files are named here
  on the same footing as the rotation ordinal.
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

### US-001 implementation (2026-09-22)

- Pre-flight passed before edits: canonical host/rejection return shape, both config
  bound helpers, `OMIT_BLOCKED_URL`, the held 1.3.0 golden, the complete 1.2.0-schema
  diff sweep, and the operator-policy resolver all exist. Read the actual spec-1
  US-003 notes: the rejection reasons are `unparseable`, `numeric_host`, `idna`;
  `.localhost` was already added there.
- Added the canonical-string domain normaliser and directional matcher, plus byte
  measure and matching-entry helper. No signature change at any of the three
  comparison sites or the fingerprint. The interim per-comparison entry pass remains
  unbudgeted until US-007; no request counters or cap raisers were added here.
  Config entries are each normalised once at boot into a published copy, retaining the
  untouched raw config for revision derivation. One warning names each list's drops;
  misplaced URL/credential-shaped entries are redacted to preserve invariant 6.
- The spec's unhashed-helper assumption is superseded: `url_validator.py` already
  belongs to `_ROOT_REVISION_SOURCES`. Removing it or documenting it as unhashed would
  regress spec 1. GOTCHAS records the resolved question and whole-file invalidation
  cost instead. Three source files rotate the revision. Read-only single-file
  reversals and the all-reverted control reproduce the clean base under both default
  and shipped config; all five rotation records are updated.
- Reconciled the preceding, previously unrecorded validation rotation from `fe211e3`
  separately: `d98f7dbe…` → `5a470872…` (twenty-sixth). This story is twenty-seventh,
  `5a470872…` → `328d386c…`, the fifth sanitization-behaviour change.
- The three request descriptions and GOVERNANCE ruling (h) land in the 1.3.0 window.
  Exported OpenAPI anchor: `b176ced35f6cacd32adbca96c5ca78daaaa2a50c99fc7a349be036018f24ccff`.
  Re-created the held golden through `_SCHEMA_MODELS`, byte-identical; older goldens
  and `_EXPECTED_ONE_THREE_ZERO_DIFF` stay unchanged. All four anchor quotations updated.
- No pre-existing matcher test was removed, weakened or given a different call shape.
  `test_config_loading` now expects `.reuters.com`, the intentionally changed shipped
  spelling. Formatting touched existing lines in the modified orchestrator test file;
  its pre-existing untyped tokenizer lambda became a typed equivalent for pyright.
  The lifespan regression explicitly installs the real validator at its import seam:
  a prior concurrent-mock test leaks a validator mock when the modules run together,
  while this regression passes independently without that patch.
- Full-suite execution remains deferred by the story-implementer instruction.
  Repository-wide formatting currently reports pre-existing drift in untouched
  `tests/test_retrieve_admission.py` from `fe211e3`; do not confuse that gate with a
  failure of the hostname tests. This file is outside the permitted changed-file
  formatter scope and was left untouched.
- Final focused gate: 1,142 tests across the twelve related modules passed; five
  expected unavailable-cache socket-guard warnings and one upstream Torch deprecation.
  Repository lint and strict pyright pass, as do changed-file formatting and
  `export_contract --check`. A separate AST/read-only check pins unchanged public
  signatures, one IDNA call site, the direct dependency, and all retained goldens.

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

**Decision:** Suffix matching is unconditional for multi-label denylist entries and opt-in (leading
dot) for allowlists (ruling R8); single-label denylist entries are exact-only; config lists are
normalised once at boot; denylists are never truncated and operator-first; caller lists are bounded
by bytes, never by count (round 4).
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
- Validation round 4 (R8 corrected, R44): the 64-entry allowlist cap and the 4,096-entry denylist
  work bound were replaced by one byte budget per list (`policy_domain_entries_max_bytes`) with
  truncation for allowlists and a 422 refusal (`policy_domain_list_too_large`) for denylists;
  single-label denylist entries are kept exact-only, deleting the upgrade loosening; the
  `tests/docs_helpers.py` extraction was dropped for the sibling-import precedent; the shipped
  `news_domains` entries are rewritten to the leading-dot form; `matched_entry` and
  `domain_list_bytes` were added so the resolver's signature really is unchanged.
- Validation round 5 (final, not re-reviewed — R8 corrected): the US-001→US-007 seam normalises
  both sides through the shared helpers instead of a bare `.lower()`; US-007 appends nothing to
  `_EXPECTED_ONE_THREE_ZERO_DIFF` and gains the metrics field-set pins as its gates; US-001's golden
  gate is the export drift check; the round-4 salty / codebase-fit warnings that were one-to-three
  line edits were applied and the rest recorded under "Known risks (validation close-out)".

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
- Round 3 (R8 corrected): the private-name list keeps its own single-label check ahead of the
  matcher and never passes through `normalize_domain_entries` — three reviewers showed a literal
  reading would delete the RFC 6761 / mDNS refusal or force the two-label guard to be relaxed;
  `anything.localhost` is refused by adding `.localhost` to that check's suffix set.
- Round 3: canonical strings cross every boundary; `DomainEntry` is internal (salty engineer,
  codebase fit: no carrier type satisfied all four of the spec's own constraints; strings satisfy
  them all with no signature change).
- Round 3: the operator's `news_domains` carries no count cap (`limit=None`) — the 64-cap is a
  guard on caller input, and a `config_invalid_value` line about six well-formed domains would have
  been a misleading token; over-bound *caller* denylist entries (past 4,096) were counted under
  `policy_invalid_domain_entry` and never enforced — **superseded in round 4** by the byte budget
  and the refusal (below): the count neither bounded the work nor kept the denylist honest.
- Round 3: `validate_url` canonicalises the host before any list comparison, so an un-canonicalisable
  host is refused with or without a blocklist (the security behaviour no longer depends on an
  unrelated config value).
- Round 3: GOVERNANCE's Example 6 is named in the recorded ruling — the step-1 compatible ship was
  considered and declined; the single-label denylist loosening is stated rather than hidden.
- Round 3: the multi-tenant caution and the classifier-skip consequence live in the exported field
  descriptions, not only in operator docs (the consumer who vendors `openapi.yaml` is the entity
  choosing a `trusted_domains` entry).
- Round 3: `search_url_blocked` stays at INFO with spec 1's exact message shape; `policy_blocklist`
  is documented as expected policy, not a suspicious-host signal.
- Round 3: `promptguard_threshold_from_config` reuses spec 2's `pipeline/config_bounds.bounded_float`
  (a third bounded-float reader was the drift this spec exists to end; `float(True)` is rejected).
- Overruled (round 3): the security review's runtime-queryable config-warning signal — ruling 12
  chose warn-and-continue and `degraded_reasons` is dependency state; recorded as a non-blocking
  question. Overruled (R37 corrected): the salty engineer's US-001a/US-001b split and the
  once-per-spec rotation record. Overruled: the second opinion's shipped multi-tenant-apex advisory
  set — stays an open question (a hand-maintained list is a moving target; the exported caution
  and the counter are this epic's guard).
- The baseline fixture is narrowed to five fields, moved under `tests/fixtures/search/` and given a
  README section, because whole-response equality would go red on every later window story.
- Round 4 (R8 corrected): single-label denylist entries are exact-only — the two-label minimum
  guards privilege grants, and over-matching a denylist only narrows access, so nothing needs the
  guard on that side; the salty engineer's Example 6 step-4 reading was correct and is closed by
  removing the loosening rather than classifying it.
- Round 4 (R8 corrected, R44): bounds on caller lists are byte budgets measured before
  normalisation. A denylist over budget is refused, never partially enforced — the security review
  was right that "counted, not enforced" was fail-open and that the round-2 "moot, denylists are
  uncapped" overruling had gone stale; the count-only 422 it asked for is exactly what ships, with
  a closed reason and no echo of caller bytes. `content_too_large` is the `/retrieve` code (the
  request's own bytes are over a bound; `blocked_domain` was declined because a consumer maps it
  to "the site is blocked"); `search_unavailable` is the `/search` code on the
  `POLICY_EXCLUDED_ALL_PROVIDERS` precedent.
- Round 4: the config-list WARNING names the dropped entries. CLAUDE.md invariant 6 protects
  credential-bearing values; `seed_blocklist` and `news_domains` are hostnames from the operator's
  own mounted file, and a count with no entry is a WARNING nobody can act on. The request path
  stays count-only (caller bytes). This narrows R8's "never an entry" to caller input — recorded
  for the wrapper.
- Round 4: `news_domains` stays an allowlist (exact by default, R8) — its blast radius is only a
  shorter TTL, but one matcher rule per direction is the point of the spec; the shipped entries are
  rewritten with the dot so the default deployment gets the documented TTL, and the Overview no
  longer presents the punch-list item as fixed by matching alone.
- Round 4: the private-name check runs before the denylist; the `blocked_domain` → `private_ip`
  swap for a host that is both is classified in the GOVERNANCE ruling rather than avoided by
  keeping the old order (canonicalisation must precede both anyway, and `private_ip` is the more
  specific refusal).
- Round 4 (R36 corrected): US-001 and US-004 move descriptions only and append nothing to
  `_EXPECTED_ONE_THREE_ZERO_DIFF`; US-007, US-002 and US-005 add properties and do; the new 422
  reason is announced in a docstring line and never appended.
- Round 4 (R43): the AST sweep's floor is eight literal-key sites with a named skip list and a
  third receiver shape; every grep in this spec names its path set and was run before its expected
  count was written.
- Overruled (round 4, ruling 37): the salty engineer's US-003 → US-003a/b/c split — no further
  splits; the story shrank instead (the helper extraction is gone, the sweep's floor is arithmetic
  that can pass).
- Overruled (round 4, R10): rejecting a YAML boolean in `/extract`'s own read — `/extract` is
  untouched; the divergence is pinned by test, documented in two places and recorded as an open
  question.
- Round 5 (R8 corrected): the interim seam between US-001 and US-007 runs `normalize_domain_entries`
  and `canonicalize_host` at each comparison site — a bare `.lower()` fail-opened a denylist for any
  non-ASCII entry (`straße.de` vs the canonical `xn--strae-oqa.de`) in a shipping commit, and the
  spec's own justification claimed the opposite. The interim unbudgeted work is accepted for the
  commits between two consecutive stories.
- Round 5 (R8 corrected): US-007 appends **nothing** to `_EXPECTED_ONE_THREE_ZERO_DIFF` — the
  `*MetricsResponse` models are outside `_SCHEMA_MODELS`, so the round-4 instruction could never be
  satisfied and the likely workaround (adding them to `_SCHEMA_MODELS`) was an unclassified
  expansion of the frozen surface. The gates are the two `tests/test_contract_metrics.py` guards and
  the field-set pins; the ruling's "retrieve twin" does not exist today and is added by US-007.
- Round 5 (R8 corrected): US-001's window gate is `tests/test_contract_export.py`'s drift check —
  `RetrieveRequest` is outside `_SCHEMA_MODELS`, so `test_contract_schema_matches_golden` passes
  whether or not the description work was done.
- Round 5: `search_unavailable` keeps carrying `policy_domain_list_too_large` (ruling 42), but the
  `SearchErrorCode` docstring stops calling every member an upstream failure and the two policy
  refusals are documented as non-retryable — the code's retry semantics now rest on `reason`.
- Round 5: US-004's guard asserts the positive (every shared name inside the `Shared by both
  routes:` sentence) instead of the absence of an English phrasing.
- Round 5: US-005's "is not applied" locator is a whitespace-normalising test — the two highest-value
  copies (the handler docstrings FastAPI exports) wrap across a line and a line-oriented grep
  reports green with the wire contract still wrong.
- Round 5: `hostname_matches`'s rotation pairing stays a comment plus a GOTCHAS entry; hashing
  `url_validator.py` is an open question with its rotate-on-every-edit cost named.

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

### Session 2026-09-19 (validation round 3)
- Rulings applied: R8 corrected (canonical strings end to end; the private-name list keeps its own
  check; the AST sweep against the real call shapes with a planted-key test), R10 corrected (the
  `/search` half of the ceiling here; `bounded_float` reuse; no boot refusal), R37 corrected (no
  further splits), R39 (locators that hit every site), R41 (every named fixture reproduced), the
  round-3 per-spec directives (US-001/US-007 seam: interim `.lower()` until US-007; Example 6 named;
  API_GUIDE rows; LOGGING/MONITORING marker rows; `search_url_blocked` level and shape).
- Q: What crosses the handler→pipeline boundary? → A: Canonical strings with the leading dot intact;
  `DomainEntry` never leaves the normaliser.
- Q: Does the two-label minimum apply to `localhost` / `.local`? → A: No — that list is not a domain
  list; it keeps its own check, which gains `.localhost`.
- Q: Is a caller's denylist really unbounded? → A: (Round 3) Enforced up to 4,096 entries, counted
  beyond — superseded in round 4 by the byte budget and the refusal; the operator's entries are
  never evicted.

### Session 2026-09-19 (validation round 4)
- Rulings applied: R8 corrected (the resolver's return is unchanged and `matched_entry` is a
  separate helper; single-label denylist entries are exact-only; the caller-list bound is a byte
  budget with truncation for allowlists and a 422 refusal for denylists; `verified_domains`
  wildcard consequence documented and counted; US-003's malformed-config criterion scoped to the
  function), R27 corrected (the `CanonicalHost | HostRejection` contract), R36 corrected
  (description-only window stories append nothing), R43 (greps scoped and executed), R44 (no
  entry-count caps), and the round-4 per-spec directives (test anchors and the
  `TestHostnameRejection` name fixed; the `/extract` boolean divergence documented).
- Q: Why is a too-large denylist a 422 when a too-large allowlist is truncated? → A: Truncating an
  allowlist only narrows privilege; truncating a denylist would silently fail open, so it is
  refused with a closed reason and no echo of caller bytes.
- Q: Which 422 code carries `policy_domain_list_too_large`? → A: `content_too_large` on `/retrieve`
  and `search_unavailable` on `/search` — each route's existing code, declared per route (ruling
  42); no new code, no shape change.
- Q: Does a single-label denylist entry still match after the spec? → A: Yes, exactly as today
  (exact-only); only multi-label denylist entries gain subdomain coverage.

### Session 2026-09-19 (validation round 5, final)
- Rulings applied: R8 corrected (both sides canonicalised at the three sites through the shared
  helpers between US-001 and US-007 — no interim fail-open; US-007 appends nothing to
  `_EXPECTED_ONE_THREE_ZERO_DIFF`, gated by the metrics guards and the field-set pins; US-001's
  golden-gate sentence corrected to the export drift check), plus the round-4 salty-engineer and
  codebase-fit warnings applied as one-to-three-line edits: spec 1's two-token
  `search_url_blocked … provider=` line and the substring assertion; `tests/test_cache.py:506`
  named; the nine-site whitespace-normalising locator for US-005; the `/search` `blocked_domains`
  single-channel criterion; `promptguard_wait_seconds` in the registry; the per-module AST floor;
  the pre-flight's diff-machinery check; the positive shared-knobs guard and the README two-row
  slice; the `/extract` sentence in the boot WARNING; the `derive_sanitizer_revision` docstring;
  the non-retryable wording for `search_unavailable`; the GOTCHAS entry for the unhashed matcher;
  the `_RULING_MARKERS` fan-out for the two recorded rulings; the encode-work-only sentence; the
  measured-default assumption; the rename deferral's price. This round was not re-reviewed.
- Q: Why not a bare `.lower()` between US-001 and US-007? → A: After US-001 the host is an A-label
  and a raw `straße.de` entry never equals `xn--strae-oqa.de`; the denylist would stop matching in
  a shipping commit. The same helper on both sides costs one pass per call and never fails open.
- Q: Where do the four `/metrics` counters get pinned if not in the golden? → A: By
  `tests/test_contract_metrics.py`'s two guards and by the field-set pin tests in
  `tests/test_contract_schema.py` (the search one exists at `:368` and is extended; the retrieve
  twin is added).

## Open Questions

- [ ] Whether `KNOWN_CONFIG_KEYS` should also carry a per-key "read by" pointer for the docs table
      (non-blocking; a later docs sweep can add it).
- [ ] Whether boot-time config fallbacks (`config_invalid_value`, `config_unknown_key`) should also be
      queryable at runtime — a `config` section on `/metrics` would be additive (non-blocking;
      ruling 12 chose the boot WARNING).
- [ ] Whether a shipped list of multi-tenant apexes should refuse leading-dot allowlist entries
      outright (non-blocking; the two-label rule, the docs caution and `policy_suffix_trusted_skip`
      are the guard for this epic; the corpus epic is the natural owner if the counter shows use).
- [ ] Whether `/extract`'s own `promptguard_threshold` read should reject a YAML boolean the way
      the bounded readers do (non-blocking; R10 keeps `/extract` untouched in this epic, and the
      divergence is pinned by test and documented).
- [ ] Whether `policy_suffix_trusted_skip` should be renamed now that it also counts wildcard
      `verified` resolutions (non-blocking; the description says what it counts). The deferral has a
      price (round 5): the `/metrics` models are `extra="forbid"`, so a later rename removes a served
      field — a bump plus a deprecation window under GOVERNANCE, not a free edit; if the name is to
      change, the cheapest moment is inside US-007, before it is served once.
- [ ] Whether `url_validator.py` should join `_REVISION_SOURCES` so a matcher-only semantics change
      rotates `sanitizer_revision` by mechanism rather than by comment (non-blocking; the cost is a
      rotation on every edit to a file that also carries the private-IP and DNS rules — round 5).

## Known risks (validation close-out)

Round-4 warnings not applied in round 5 (the final, un-reviewed fix pass), each with the reviewer,
the finding and why it is deferred rather than fixed:

- **Salty engineer — `search_unavailable` carries a permanent client error.** An over-budget
  `blocked_domains` list on `/search` is refused under a code whose docstring calls it an upstream
  failure, so a consumer that treats the code as transient may retry a request that can never
  succeed. Deferred: ruling 42 keeps each route's existing code; round 5 amended the docstring and
  documented the two policy refusals as non-retryable by `reason`, so the risk is a consumer that
  branches on the code alone.
- **Salty engineer — the matcher is a cache-key input that nothing hashes.** A future change to
  `hostname_matches`'s suffix rule in `url_validator.py` alone rotates nothing and serves content
  sanitised under the old semantics until the TTL. Deferred: the mechanism (hashing the file) is an
  open question with a real cost; this epic ships the comment plus a GOTCHAS entry.
- **Salty engineer — the interim seam does unbudgeted work.** Between US-001 and US-007 each
  comparison site normalises the raw caller list per call with no byte budget. Accepted: the window
  is the commits between two consecutive stories on one branch, and the alternative (a bare
  `.lower()`) fail-opened a denylist.
- **Salty engineer — the 64 KiB default is asserted, not measured.** `/retrieve` gains a hard 422
  on an existing field at a bound nobody has checked against what Poppy sends. Deferred to US-007's
  Implementation Notes (an Assumptions line now requires the measurement); the risk stands until
  it is written down.
- **Salty engineer — `policy_suffix_trusted_skip` ships under a name the spec admits is
  imprecise.** Deferred with its price recorded in Open Questions; renaming later is a bump plus a
  deprecation window.
- **Codebase fit — the metrics field-set "retrieve twin" named by the round-5 ruling does not
  exist.** Only `test_search_metrics_response_1_2_0_field_set_is_pinned_exactly` exists today;
  US-007 adds the `RetrieveMetricsResponse` twin. Risk: the implementer treats it as pre-existing
  and skips it — the criterion names it explicitly.
