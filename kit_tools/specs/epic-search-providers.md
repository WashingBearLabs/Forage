<!-- Template Version: 2.1.0 -->
---
epic: search-providers
status: active
vision_ref: "T2.1 — Search-provider abstraction & reliable search"
created: 2026-09-14
updated: 2026-09-14
---

# Epic: Search Providers — Pluggable Search Backends + Free-First/Paid-on-Failure Fallback

> **Planned in Poppy, executes here.** This is the **Forage half of Web Access family Epic 3**
> (search-reliability). It was re-planned 2026-09-14 against the shipped Forage layout and
> **split into two repo-native epics** (family rule: no spec straddles a repo): *this* engine-side
> epic runs in Forage; a separate `epic-search-policy` (settings UI, policy plumbing, telemetry
> consumption) runs in Poppy and **consumes the `v1.1.0` image this epic publishes**. The canonical
> cross-repo planning record — the full Poppy⇄Forage work split, the landscape-research findings,
> and every owner decision — is Poppy's `kit_tools/specs/EPIC3_SEARCH_RELIABILITY_SPLIT.md`.
> **Validate with `/kit-tools:validate-epic` before executing.**

## Goal

Make Forage's web search **reliable without abandoning provider-independence**. Today the only search
path is SearXNG, called inline in `pipeline/orchestrator.py:run_search_pipeline` — and free upstream
engines rate-limit a single residential IP, so search intermittently returns nothing. This epic adds a
pluggable **`SearchProvider`** abstraction over that hardwired call, an **optional paid backend**
(Brave, via its LLM-Context endpoint), and a **free-first → paid-on-failure** fallback so the paid API
is hit only when free search actually fails — with the paid path guarded against cost-exhaustion.
Provider/fallback telemetry and per-request policy extend the existing fail-loud contract; `/health`
reports provider status; a documented `FORAGE_SEARCH_PROVIDERS`/`FORAGE_BRAVE_API_KEY` env contract makes the whole
thing configurable by any third-party operator. Every result — free or paid, snippet or chunk — flows
through the unchanged sanitization pipeline, and **SearXNG remains the key-less free floor**: a
deployment with no paid key must still work. Completing this epic turns "search is a coin flip" into
"search works, and when free search fails the operator can see the paid fallback fire, what it cost,
and which provider served each result."

## Decomposition

| Seq | Feature Spec | Status | Stories | Human gates | Dependencies |
|-----|-------------|--------|---------|-------------|--------------|
| 1 | [feature-search-provider-abstraction](feature-search-provider-abstraction.md) | active | ~4 | — | — |
| 2 | [feature-brave-provider](feature-brave-provider.md) | active | ~3 | **US-001 pre-flight** (owner captures the Brave sample, ruling 24) | search-provider-abstraction |
| 3 | [feature-search-fallback](feature-search-fallback.md) | active | ~4 | — | brave-provider |
| 4 | [feature-search-policy-and-health](feature-search-policy-and-health.md) | active | ~4 | — | search-fallback |
| 5 | [feature-search-release](feature-search-release.md) | active | ~4 | **US-002** (owner-gated `v1.1.0` cut) | search-policy-and-health |

**Execution order:** 1 → 2 → 3 → 4 → 5 (**~19 stories, fully sequential** — the wire contract accretes
across 1/3/4 and each provider builds on the prior seam). **P1 MVP = specs 1–4** (reliable search +
telemetry, backend-only, autonomously executable). Spec 5 is P2: third-party docs + the owner-gated
release cut (frontend-free, but the release tag is a supervised human gate like Epic-2's v1.0.0).

`feature-search-provider-abstraction` carries `depends_on: []` — its only real precondition (the Forage
repo + a frozen contract 1.1.0) already shipped in `epic-forage-extraction-forage-side` (2026-09-12).

## Owner decisions (re-plan 2026-09-14; full rationale in Poppy's split doc)

1. **Two repo-native epics.** Forage owns the engine (this epic); Poppy owns policy/UI/telemetry
   consumption (`epic-search-policy`). No spec straddles the repo boundary.
2. **Brave is the paid default, via its LLM-Context (chunks) endpoint** — leaning LLM-native, and
   PromptGuard is actually *stronger* on multi-sentence chunks than on one-line snippets. **Brave has
   no free tier since Feb 2026** ($5/1,000 + ~1k-query/mo credit); it is purely the *paid* backend.
   SearXNG stays the key-less free floor. No spec text may imply Brave is free.
3. **Second provider left open** — the abstraction stays provider-agnostic; document the extension
   point generically, with **per-provider ToS/caching as a first-class provider attribute** (Tavily
   permits caching; Brave/Exa forbid persisting/redistributing result payloads). Tavily/Exa/LiteLLM
   adapters are Tier-3 future work, not this epic.
4. **12-factor keys, no vault.** The paid key is a plain env var (`FORAGE_BRAVE_API_KEY` — renamed, and
   the generic `SEARCH_API_KEY` alias dropped, by Forage-side ruling 10 below) at container start
   (HF_TOKEN precedent). Forage has no secrets manager and no secret-bearing runtime API; the vault→env
   plumbing is entirely the Poppy consumer's job.
5. **Keep the custom `SearchProvider` seam**, cribbing LiteLLM's `{title,url,snippet,date}` schema so a
   future `LiteLLMProvider` adapter is a drop-in — do NOT adopt LiteLLM wholesale (its fallback is
   unsupported, it's a heavy dep, and it can proxy provider-native tools the ADR rejects).
6. **Telemetry is metadata-only.** Record provider/fallback/error metadata + cheap per-result
   provenance (engine/domain); **never persist raw paid-provider result bodies** (Brave/Exa ToS).
7. **Cost-exhaustion hardening.** Fall back only on rate-limit / hard-error, **never on a clean
   zero-results**. ~~Backoff+jitter before declaring the free path failed~~ — *superseded by ruling 18
   (no in-request retries)*. (The *global* daily paid-fallback circuit breaker is the Poppy consumer's
   budget concern — noted, not built here; see ruling 12.)

### Forage-side rulings (review against the seeded `kit_tools/` docs, 2026-09-14)

Made by the owner in the Forage session after `/kit-tools:seed-project`; replayed into Poppy's
`EPIC3_SEARCH_RELIABILITY_SPLIT.md` on 2026-09-19, together with rulings 12–34 and the `v1.1.0` handoff
record (one-way sync, Forage → Poppy's record, at handoff only).

8. **An exhausted chain is a 422, never a 200 with an empty list.** GOVERNANCE's MAJOR row ("a status
   code a client observes changes") forbids turning today's 422 into a 200, and a 200-empty is the exact
   shape of the nine-day silent failure. SearXNG-only chains keep `searxng_error` / `searxng_unavailable`
   byte-for-byte; an exhausted fallback chain raises a new `search_unavailable` code (MINOR under
   GOVERNANCE ruling (b), announcement obligation). A clean zero-result from a healthy provider stays
   200 — that is what makes the cost-exhaustion guard (decision 7) work. Spec 3.
9. **The chain is an environment variable; tunables are `config.yaml`.** `FORAGE_SEARCH_PROVIDERS`
   (comma-separated, default `searxng`) is the single surface for the chain, read once in the lifespan;
   there is no `config.yaml` key and no precedence rule. `search_fallback_min_results` and per-provider
   timeouts stay in `config.yaml`. This matches the repo's existing split (environment = deployment
   wiring and secrets; `config.yaml` = tunables). Specs 1 and 5.
10. **New variables are `FORAGE_*`; keys are per-provider.** Every variable Forage itself introduced is
    `FORAGE_*` (`FORAGE_MODEL_REVISION`, `FORAGE_WEIGHTS_MIRROR`, `FORAGE_MIRROR_TOKEN`); `HF_TOKEN` keeps
    its name only because the Hugging Face client reads it directly, and nothing reads `BRAVE_API_KEY` on
    its own. So: `FORAGE_SEARCH_PROVIDERS`, `FORAGE_BRAVE_API_KEY`; the generic `SEARCH_API_KEY` alias is
    dropped (ambiguous the day a second paid provider exists). Specs 2 and 5.
11. **`sanitizer_revision` rotates in specs 1, 3 and 4** because `orchestrator.py` and `contract.py` are
    in `_REVISION_SOURCES`; expected, recorded in `docs/bootstrap-notes.md` each time. The new
    `pipeline/search_providers/` package is deliberately **not** hashed: provider code changes what is
    fetched, not how it is sanitized, and search results are never cached.

### Rulings from `/kit-tools:validate-epic` (2026-09-14, 30-review panel; 42 criticals resolved here)

12. **No spend ceiling in this epic.** A per-process paid-call cap or budget breaker is a v2 feature; the
    reviewers' "unauthenticated, uncapped spend" criticals are **accepted as a known risk**, not fixed.
    The observability floor is ruling 21 (`/metrics` counters) and the policy floor is ruling 16
    (callers cannot promote a paid provider). Reviewers: do not re-raise the ceiling as a finding.
13. **The failure taxonomy lives in spec 1's seam.** `SearchProvider.search()` returns either a
    `ProviderSearchResult` (success — a clean zero-result list is a *success*) or a `ProviderFailure`
    carrying `provider_name`, a closed `failure_class` (`rate_limited` | `timeout` | `hard_error` |
    `auth` | `quota`) and a closed-vocabulary `detail` (never exception text, never a URL). Providers
    declare `name: str` and `paid: bool`. Specs 2–4 consume these; nobody invents tokens.
14. **`search_unavailable` lands in spec 1 US-004**, the one story that already bumps the contract,
    regenerates, and creates the 1.2.0 golden (supersedes ruling 8's *placement* in spec 3; the
    semantics of ruling 8 are unchanged). Spec 2 raises it for a failed Brave-only chain; spec 3 raises
    it for an exhausted fallback chain. The raise site is `pipeline/orchestrator.py` (the raise-site
    sweep in `tests/test_contract_errors.py` covers only `orchestrator.py` and `retrieval_app.py`).
15. **Key presence on `/health` uses the existing `capabilities` presence map**, one entry per keyed
    paid provider (`capabilities["brave_api_key"] = 1` when set, absent otherwise) — not a single
    boolean. `search_providers: list[str]` (the resolved chain) is the only other `/health` addition.
    Key presence is **deliberately public** on the private network (it is also inferable from
    `search_providers`); the "no oracle" rule in spec 4 means *no second, differential channel* (no
    status code that varies with key presence), not secrecy.
16. **Per-request `providers` is restrict-only and order-preserving.** It filters the configured chain
    to the named providers in configured order; it can never add, reorder, or key a provider. Paid
    providers may always be excluded; a free provider may be excluded only if another free provider
    remains, otherwise the free floor is retained. Unknown *and* unregistered (key-absent) names are
    ignored identically, counted in `/metrics` (`search.policy_unknown_provider`), never a 422 — the
    response is byte-identical whether a name is unknown or key-absent. `allow_paid_fallback: false`
    stops traversal before any provider with `paid=True`. Both fields carry concrete defaults
    (`[]`, `True`) and bounds (max 8 names, `^[a-z0-9-]{1,32}$`).
17. **Free-path failure classification.** A provider outcome is a failure when it is a
    `ProviderFailure`, **or** when SearXNG answers 200 with zero raw results and a non-empty
    `unresponsive_engines` list (classified `rate_limited` — this is the recurring production failure
    in `kit_tools/docs/GOTCHAS.md` "SearXNG :latest rots", and it never raises). Sufficiency is judged on
    **raw provider results before sanitization**; post-sanitization omission never triggers fallback (a
    poisoned SERP must not buy a paid call). `search_fallback_min_results` is **dropped**.
18. **No in-request retries on the free path** (supersedes the backoff clause of decision 7). The
    fallback *is* the retry; each provider keeps its own per-call timeout (SearXNG 10 s; Brave's is a
    `config.yaml` tunable per ruling 9), so total traversal is bounded by the sum of per-provider
    timeouts. `kit_tools/arch/patterns/ERROR_HANDLING.md` ("retries only for background acquisition")
    stands.
19. **`date` is strictly validated.** `SearchResult.date: str | None` accepts only an ISO 8601 calendar
    date (four-digit year, two-digit month, two-digit day, validated by a Pydantic validator); anything else becomes `None` before the
    result is built. Nothing free-form reaches the model through this field, so it needs no scan.
    `SearxngProvider` maps SearXNG's `publishedDate` through it.
20. **Release plumbing is a spec-5 story, landed before the tag.** (a) The `publish` job appends the
    contract module's per-version docstring entry to the Release body so ruling (b)'s announcement is
    mechanical and the existing `^contract:` assertion still runs. (b) `contract_smoke.py` gains
    `--expect-status {healthy,degraded}` and `--anchor <path>` so it can verify a weights-loaded
    release image from the tag's checkout. (c) `compose/minimal.yml` and `compose/full.yml` move to
    `1.1.0` and list `FORAGE_SEARCH_PROVIDERS` / `FORAGE_BRAVE_API_KEY` in their env passthrough.
    (d) Both CI secret-grep pattern lists (`secret-grep` heredoc and the `publish` config grep) gain
    `FORAGE_BRAVE_API_KEY`, with `tests/test_ci_workflow.py` keeping the two copies in sync.
21. **`/metrics` gains two search counters** in spec 3 US-003: `search.fallback_fired` and
    `search.paid_calls` (additive MINOR; the metrics models use `extra="forbid"`, so the golden moves).
    Without a ceiling, this is how an operator sees spend.
22. **Housekeeping the reviewers found, owned explicitly:** the `cacheable` attribute is dropped
    (search results are never cached; spec 2 adds a regression test that `/search` never writes to the
    content cache instead). The provider chain is read in the lifespan **through the existing
    lifespan-or-fallback pattern** (`retrieval_app.py` `_resolved_*` helpers ~198–221 and the
    module-scope `app.state` mirror ~1182–1200) so the six lifespan-free `ASGITransport` test clients
    keep working. The seventeen-code claim and the embedded contract anchor are updated by the story
    that moves them (see the resolution map). Per-provider tunables live in `config.yaml`.

### Rulings from validate-epic round 2 (2026-09-14; 15 residual criticals resolved here)

23. **One identifier per provider.** `SearchProvider.name` is the chain token — `searxng`, `brave` —
    and that token is what `FORAGE_SEARCH_PROVIDERS`, `/health` `search_providers`, per-request
    `providers`, `provider_used`, `provider_errors` entries and the `search_unavailable` reason all
    carry. The per-result `engine` field is *provenance* and is `brave-api` for the Brave provider, so
    it stays distinct from SearXNG's own `brave` sub-engine. Spec 2's `name = "brave-api"` is corrected;
    operator docs write `brave: auth`, `brave: quota`.
24. **The Brave sample capture is a declared human gate** on spec 2 US-001 (decomposition table
    updated). Procedure: the owner makes one credentialed request from a shell with the key supplied
    through the environment (never argv), then commits only the response **envelope** with synthetic
    chunk bodies (title, url, content, date replaced by obviously synthetic values) under
    `tests/fixtures/brave/` with a provenance note; no Brave-authored text is ever committed (Brave ToS;
    owner decision 6), and a test asserts no key-shaped string appears under `tests/fixtures/`. If the
    gate has not run, execution stops at spec 2 US-001 and reports, exactly like spec 5 US-002.
25. **The paid candidate budget is written in spec 1 US-002** (the story that already edits
    `orchestrator.py` and rotates the revision): `max_results = request.num_results if provider.paid
    else fetch_limit`. Spec 2 consumes it and asserts it; it does not write it.
26. **Mechanical test facts the specs must name.** (a) `pipeline/search_providers/searxng.py` defines
    public `DEFAULT_SEARXNG_URL` and `SEARXNG_ENGINES`; `pipeline/orchestrator.py` keeps
    `_DEFAULT_SEARXNG_URL` / `_SEARXNG_ENGINES` as private *aliases assigned from* those names (an
    import of a private name fails pyright strict `reportPrivateUsage`, and `tests/test_searxng_docker.py`,
    `tests/test_compose_fragments.py`, `tests/test_searxng_smoke.py` import the private names).
    (b) `tests/test_hermeticity.py::test_the_cleared_environment_is_the_expected_exact_set` pins
    `_CLEARED_ENV_VARS` as an exact set and is updated by every story that grows it (spec 1 US-003,
    spec 2 US-002). (c) The no-cache-write test spies on `ContentCache.get`, `put` and `delete` (there
    is no `set`) through `tests/fakes.py::FakeStorage` counters, never a bare `MagicMock`. (d) Tests
    that set `app.state.*` on the module singleton use the save/`delattr`/restore idiom at
    `tests/test_app.py` ~1504–1517. (e) The `/metrics` order guards are
    `test_served_metrics_are_the_handlers_dict_serialized` and
    `test_metrics_mirror_round_trips_the_served_body` (key order matters); the dataclass-parity test does
    not cover `SearchMetrics`.
27. **Every provider has a catch-all.** The failure mapping ends in `except Exception` →
    `ProviderFailure(failure_class="hard_error", detail="unexpected")`; a valid-JSON body of the wrong
    shape (`results` not a list, elements not objects) is `hard_error` / `malformed_body`. Providers
    never raise into the orchestrator.
28. **The state seam.** `app.state.search_providers` holds `list[SearchProvider]` (spec 1); the
    module-scope sentinel is `None`; `_resolved_search_providers()` returns the chain objects, with a
    default one-element chain holding `SearxngProvider(DEFAULT_SEARXNG_URL)` when the lifespan has not run; `/health` derives
    `search_providers` as `[p.name for p in chain]` — there is no separate name list. Key presence is one
    shared helper, `brave_key_present()` (strip, non-empty), defined in spec 2 and reused by `/health` in
    spec 4. The `policy_excluded_all_providers` reason literal lives in `pipeline/contract.py`. The
    "legacy `searxng_*` codes" predicate is on the *configured* chain (exactly one provider, `searxng`),
    never on the per-request effective chain, so no status varies with policy.
29. **Policy simplifications.** A request may exclude only `paid=True` providers; free providers are
    never excluded, which makes the cost-monotonic invariant hold for any chain and is equivalent to
    ruling 16 on today's two-provider chain. `providers` items carry no pydantic pattern or length
    validation (a FastAPI 422 would echo the caller's bytes); the policy function normalises (strip,
    lower), keeps the first eight, and ignores-and-counts everything else — unknown, non-matching, or
    key-absent — identically. `SearchRequest.query` keeps its wire shape. Spec 1's `providers` parameter
    on `run_search_pipeline` uses `is None`, never a falsy check, to detect "not supplied".
30. **Outbound query bound.** A paid provider truncates the outbound copy of `query` to
    `search_brave_query_max_chars` (a `config.yaml` tunable, default 400) before egress. Adding
    `max_length` to `SearchRequest.query` would stop accepting a value that is accepted today — a MAJOR —
    so the bound lives in the provider, not on the wire.
31. **Release-plumbing hardening (spec 5 US-004).** (a) The announcement extractor is bash + awk in the
    workflow; `publish` never executes a script from the tagged tree; the entry is written to a file under
    `$RUNNER_TEMP` (never a multi-line `$GITHUB_OUTPUT`), passed with `gh release create --notes-file`,
    and read back with `grep -F`; `tests/test_ci_workflow.py` extracts the awk program from `ci.yml` and
    runs it via `subprocess` against a hostile synthetic module (backticks, `*`, `$(`, a fake terminator);
    US-002's pre-flight runs the same extractor locally on the tagged tree. (b) `--expect-status healthy`
    waits until `/health` reports `status: healthy` within a deadline, not merely the first 200.
    (c) The compose-pin grep anchors on the literal `forage:0.9.3-rc` (also `arch/INFRA_ARCH.md:146`,
    `docs/LOCAL_DEV.md:229`). (d) The smoke-caveat sentences at `MONITORING.md:352,370`,
    `TROUBLESHOOTING.md:122`, `LOCAL_DEV.md:303`, `TESTING_GUIDE.md:58`, `CODE_ARCH.md:109`,
    `DEPLOYMENT.md:215`, the two-pattern secret-set statements at `CI_CD.md:217–218`, `GOTCHAS.md:531`,
    `TROUBLESHOOTING.md:121`, `SECURITY.md:230`, and `contract/GOVERNANCE.md` step 7 (now pointing at
    the docstring entry) are all US-004 fan-out. US-002's criteria reference `--env-file`, never a
    token value.
32. **Doc fan-out additions.** Spec 1 US-002 owns the seven statements its reason-text change
    falsifies (`arch/SECURITY.md:354`, `arch/patterns/ERROR_HANDLING.md:158,262–266` and Rough Edge 1,
    `docs/TROUBLESHOOTING.md:172–173`, `arch/SERVICE_MAP.md:104,109`, `arch/patterns/LOGGING.md:183`,
    `docs/GOTCHAS.md:343`, `docs/MONITORING.md:143`, `docs/API_GUIDE.md:242–243`) with a grep criterion;
    spec 3 US-002 owns `ERROR_HANDLING.md:250` ("No fallback engine") and `API_GUIDE.md:245`; the
    contract anchor in `arch/SERVICE_MAP.md:202` joins every regeneration's fan-out; the bold
    `**1.1.0**` at `arch/CODE_ARCH.md:108` joins spec 1 US-004; each rotating story updates the
    rotation tables in `docs/bootstrap-notes.md`, `CLAUDE.md`, and `arch/DECISIONS.md`; suite-count
    bookkeeping (TESTING_GUIDE, AGENT_README, SYNOPSIS) is spec 5 US-003's close-out.

### Rulings from validate-epic round 3 (2026-09-15; 8 residual criticals resolved here)

33. **Mechanical closures.** (a) `SearchProvider` gains `origin: str | None` — scheme, hostname
    and port with userinfo stripped, computed once in `__init__` with `urlsplit(...).port`'s
    `ValueError` caught — and the `searxng_unavailable` reason reads `provider.origin`, never
    `base_url` (not a protocol member). (b) `run_search_pipeline` gains
    `configured_chain: Sequence[SearchProvider] | None = None` (spec 3); spec 4 passes the
    configured chain there beside the effective `providers=`, so the legacy-codes predicate never
    sees a filtered list. (c) The policy 422 (`POLICY_EXCLUDED_ALL_PROVIDERS`) is raised by the
    `/search` handler in `retrieval_app.py` before the pipeline runs; spec 1's `ValueError` on
    `providers=[]` stays. (d) The fixture-tree token guard walks `tests/fixtures/` for the two
    header names only and `tests/fixtures/brave/` for a generic token shape (26 legitimate
    identifiers elsewhere would match it). (e) Spec 1 US-004's fan-out includes GOVERNANCE's
    `## Two semvers` section (`tests/test_governance_docs.py:402` asserts the current version
    appears there) and its seventeen-code grep is scoped to current-vocabulary claims, sparing the
    rotation-history records. (f) Spec 3's `execution_order` is `[US-001, US-003, US-002, US-004]`
    because US-002 asserts fields US-003 adds. (g) Spec 5 US-001 requires the docs to state the
    two-variable enablement recipe (`FORAGE_SEARCH_PROVIDERS=searxng,brave` plus the key), and the
    healthy smoke uses a temporary env file holding only `HF_TOKEN`.

### Rulings from validate-epic round 4 (2026-09-15; 1 residual critical resolved here)

34. **Last closures.** (a) The policy 422 in spec 4 increments `search.errors.search_unavailable`
    from the handler's policy branch (the existing `record_error` call sits in the arm around
    `run_search_pipeline`, which the policy raise precedes). (b) Brave 429 maps to `rate_limited`;
    `quota` is used only for an explicit plan-exhausted signal recorded at capture. (c) A Brave
    body with no `grounding.generic` (missing, null or empty) is a clean zero-result success; only
    a present-but-wrong-typed `generic` is `malformed_body`. (d) `origin` also survives
    `urlsplit()`'s own `ValueError` and a missing scheme, falling back to the closed token
    `unparseable-endpoint`. (e) The announcement test compares the extracted entry byte-for-byte
    with the docstring entry, and `docs/releases.md` states both recovery cases (extractor fault
    → `gh release edit --notes-file`; wrong entry → cut `v1.1.1`).

### Resolution map (which story owns which validate-epic finding)

| Finding cluster | Owner |
|---|---|
| Failure taxonomy / `paid` attribute / `max_results` semantics | Spec 1 US-001 |
| Test blast radius of the refactor: `tests/test_orchestrator.py` patch target `pipeline.orchestrator.httpx.AsyncClient` (one permitted edit), `_SEARXNG_ENGINES` / `_DEFAULT_SEARXNG_URL` re-exported from `pipeline.orchestrator` for `tests/test_searxng_docker.py` and `tests/test_compose_fragments.py`, `SEARXNG_URL` threaded into `SearxngProvider`, `publishedDate` → `date` | Spec 1 US-002 |
| Chain read via lifespan-or-fallback; `/search` handler passes the chain; refuse-boot on unknown name; `docs/configuration.md` + `kit_tools/docs/ENV_REFERENCE.md` rows for `FORAGE_SEARCH_PROVIDERS` | Spec 1 US-003 |
| `search_unavailable` (17→18 codes, `SearchErrorCode` 2→3, count asserts and "seventeen" test names, `SearchMetricsResponse.errors` description, `contract.py` docstring), `date` validator, `content_kind`, `tests/golden/contract_1_2_0.json` (never edit `1_1_0`), `_SCHEMA_MODELS` extended to `SearchRequest` + the 422 error model, GOVERNANCE current-version sentence + `CONTRACT_VERSION` docstring entry, doc fan-out (TROUBLESHOOTING, API_GUIDE incl. anchor + "1.1.0" statements, ERROR_HANDLING, SERVICE_MAP, MONITORING, TESTING_GUIDE, AGENT_README, `README.md` and `CLAUDE.md` contract mentions, CI_CD/DEPLOYMENT anchor) | Spec 1 US-004 |
| Brave API surface pinned from a saved real response sample; `request.num_results` (no ×2 inflation) for paid providers; response-body bound before `resp.json()`; chunk cap vs `_MAX_SEARCH_SNIPPET_LENGTH`; hardened httpx client (`trust_env=False`, `follow_redirects=False`, constant endpoint) | Spec 2 US-001 |
| Lifespan read, WARNING-level skip log modelled on `weights_fetch_skipped`, `docs/configuration.md` + ENV_REFERENCE rows for `FORAGE_BRAVE_API_KEY` | Spec 2 US-002 |
| Brave failures → `ProviderFailure`; Brave-only chain failure → 422 `search_unavailable`; key-never-leaks tests on the *failure* paths (log, wire body, `/metrics`); no-cache-write test; `brave` vs `brave-api` provenance | Spec 2 US-003 |
| Traversal + exhausted-chain 422 with closed-vocabulary reason | Spec 3 US-001 |
| `unresponsive_engines` rule, pre-sanitization sufficiency, no retries, knob dropped | Spec 3 US-002 |
| `provider_errors: list[str]` typed, `domain` = lower-cased `urlsplit(url).hostname`, `/metrics` counters | Spec 3 US-003 |
| Policy params (restrict-only, bounds, defaults, unknown-name handling) | Spec 4 US-001 |
| `/health`: `search_providers` + `capabilities` entries; lifespan-or-fallback for startup; MONITORING/API_GUIDE `/health` sections | Spec 4 US-002 |
| Golden coverage sweep after the boundary-doc edits; GOVERNANCE record goes in the `CONTRACT_VERSION` docstring list, not the six-example table | Spec 4 US-003 |
| Third-party docs, credential-handling guidance, strike the SearXNG-degrade claim, named real tests | Spec 5 US-001 |
| Release plumbing (ruling 20 a–d) | Spec 5 US-004 (new) |
| Pre-flight per the archived v1.0.0 runbook, anonymous pull after `docker logout ghcr.io`, alias digest equality, `/search` + `/health` capability check on the published image | Spec 5 US-002 |
| Handoff record incl. anchor sha256; `docs/releases.md` entry format; withdraw-and-delete edge case; suite-count bookkeeping | Spec 5 US-003 |
| Round 2: provider identity (23) | Spec 2 US-001 (corrected); specs 3, 4 unchanged |
| Round 2: sample-capture human gate, envelope-only fixture (24); outbound query bound (30) | Spec 2 US-001 |
| Round 2: paid candidate budget (25); public names + private aliases (26a); catch-all mapping (27); US-002 doc fan-out (32) | Spec 1 US-002 (+ US-001 for the catch-all contract) |
| Round 2: hermeticity exact-set test (26b) | Spec 1 US-003, Spec 2 US-002 |
| Round 2: no-cache-write spy (26c); key-never-leaks on failure paths | Spec 2 US-003 |
| Round 2: state seam, `brave_key_present()`, `policy_excluded_all_providers` home, configured-chain predicate (28) | Spec 1 US-003, Spec 2 US-002, Spec 3 US-001, Spec 4 US-002 |
| Round 2: policy simplifications (29) | Spec 4 US-001 |
| Round 2: release-plumbing hardening (31) | Spec 5 US-004, US-002 |

## Contract versioning (governance approach for this epic)

The wire contract moves to **1.2.0 at spec 1** (the first additive wire change: the result schema gains
`content_kind`) and **holds** across specs 2–4; every wire-touching story **regenerates**
`contract/openapi.yaml` + `contract/openapi.yaml.sha256` (via `uv run python -m scripts.export_contract`)
and adds/updates its golden fixture so the drift gate (`tests/test_contract_export.py`) stays green,
and `contract/GOVERNANCE.md`'s "current version" sentence is set once (spec 1). All additions are
**MINOR** (additive, backward-compatible). Nothing publishes until **spec 5 cuts a `v1.1.0` image**
(image tag, MINOR) carrying contract 1.2.0 — the two-semver rule from Epic 2 (image tag ≠ contract
version). `pipeline/contract.py:CONTRACT_VERSION` is the single hand-edited source. The one vocabulary addition is
`search_unavailable` (landed in spec 1 US-004 per ruling 14, used by specs 2 and 3) — MINOR under
GOVERNANCE ruling (b), so the `v1.1.0` Release body carries its announcement mechanically (ruling 20a).
The other additive 1.2.0 changes: `content_kind` + `date` on `SearchResult` (spec 1), `provider_used` /
`fallback_fired` / `provider_errors` on `SearchResponse` and two `search.*` `/metrics` counters (spec 3),
`providers` / `allow_paid_fallback` on `SearchRequest` and `search_providers` + `capabilities` entries on
`/health` (spec 4). Golden coverage: `tests/test_contract_schema.py::_SCHEMA_MODELS` grows in spec 1
US-004 so request-model and error-body additions are pinned, not just the four response models.

## Success Criteria

- With a valid Brave key set, a search that free engines fail (rate-limit / hard-error) returns
  non-empty results via the paid fallback, through the full sanitization pipeline, in the same call.
- With **no** key configured, search behaves exactly as today (SearXNG only) — no crash, no new
  required secret, no degradation of the default path.
- A **clean zero-results** from the free path does **not** trigger a paid retry (cost-exhaustion guard).
- A chain that exhausts every provider returns **422** — the existing `searxng_*` codes for a SearXNG-only
  chain, `search_unavailable` for an exhausted fallback chain — never a 200 with an empty list.
- The response reports which provider served results and whether the paid fallback fired; provenance
  (engine/domain) is carried per result. **No raw paid-provider result body is persisted anywhere.**
- Every result — snippet or chunk — is sanitized by the identical stage-2/stage-3/PromptGuard path; a
  poisoned meta-description/chunk is caught by the same pipeline.
- `/health` reports provider status (`search_providers`, plus a `capabilities` entry per keyed paid
  provider) without ever exposing the key.
- The published `v1.1.0` image advertises contract 1.2.0 on `/health`; the drift gate is green; the
  image carries no secret and is byte-reproducible.

## Completion Criteria

- [x] `SearchProvider` seam extracted; SearXNG served through `SearxngProvider`; provider chain resolved
      from `FORAGE_SEARCH_PROVIDERS` (default `searxng`); result schema carries `content_kind`
      + provenance.
- [x] `BraveApiProvider` (LLM-Context) registers only when its env key is present; key-less deployments
      run SearXNG-only with no error.
- [x] Free-first→paid-on-failure fallback fires only on a classified free-path failure (rulings 13, 17 —
      including SearXNG's 200-with-empty-plus-unresponsive shape), never on a clean zero-result, with no
      in-request retries (ruling 18); `provider_used`/`fallback_fired`/`provider_errors`/provenance on
      `SearchResponse` and `search.fallback_fired`/`search.paid_calls` on `/metrics` (metadata only — no
      raw paid payloads persisted).
- [x] Per-request policy params on `SearchRequest` (restrict-only, ruling 16); `/health` provider status
      (ruling 15); `/search`↔`/retrieve` boundary documented.
- [x] Contract at **1.2.0**, drift gate green, GOVERNANCE updated; `FORAGE_SEARCH_PROVIDERS`/`FORAGE_BRAVE_API_KEY`
      documented in `README.md` + `docs/configuration.md` + `kit_tools/docs/ENV_REFERENCE.md`.
- [x] Release plumbing landed before the tag (ruling 20: mechanical announcement, smoke flags, compose
      pins at `1.1.0`, secret-grep patterns); `v1.1.0` image cut + published through the gated lane
      (owner gate), advertising contract 1.2.0; Poppy's `epic-search-policy` can pin it.

## Non-goals

- **Anything inside Poppy.** The consuming-side pin, policy UI, vault→env key plumbing, provider badge,
  and the global paid-fallback budget circuit breaker are the Poppy `epic-search-policy` epic and
  execute there. Nothing here edits Poppy.
- Additional paid backends beyond Brave (Tavily/Exa) and a `LiteLLMProvider` adapter — the abstraction
  must allow them; only Brave is implemented here (Tier-3 future).
- `/retrieve` hardening, cache integrity, PromptGuard-86M + contiguity gating, the configurable
  resource envelope — `epic-forage-hardening` (family Epic 4).
- An injection regression corpus in CI — `epic-forage-injection-corpus` (family Epic 6).
- Source-trust *weighting* and datamarking *policy* (this epic only emits the provenance signal +
  preserves per-result boundaries that enable them) — Poppy Epics 4/5.
- Provider-native / answer-engine APIs (Anthropic/OpenAI/Perplexity Sonar server-side search) — excluded
  by the 2026-08-28 provider-independence ADR; only raw-result search APIs qualify as `SearchProvider`s.

## Notes

### Cross-repo sync rule — Poppy is canonical, one-way, at handoff only

The planning context lives in **Poppy** (`EPIC3_SEARCH_RELIABILITY_SPLIT.md` + the family landing +
the 2026-08-28 / 2026-09-12 ADRs). This epic + its specs were **authored in the Poppy session** and
written here for execution. **Execute and record results here** — Implementation Notes, ticked
criteria, and learnings belong in *this repo's* specs. If a spec needs **re-planning** rather than
execution, that happens in Poppy and the result is re-copied here (one-way handoff). There is no
reverse sync.

### Standing context for every story

- **Flat layout, `uv`-run tooling.** Modules are flat (`retrieval_app.py`, `models.py`, `pipeline/`,
  `promptguard/`, `config.yaml`). Measure with `uv run` (lock pins ruff/pyright); all of
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` (strict) and `uv run pytest`
  are blocking CI gates. The suite count is a **gate, not a floor** — new tests raise it.
- **The provider seam does not exist yet.** SearXNG is an inline `httpx` call inside `run_search_pipeline`
  (`pipeline/orchestrator.py`, the block around lines 665–699); `_DEFAULT_SEARXNG_URL` at ~555. The
  per-result sanitization loop (~708–796) is already provider-agnostic once results are
  `{title,url,content,engine}` dicts — that is the extraction point.
- **Contract governance is real.** `pipeline/contract.py:CONTRACT_VERSION` (currently `1.1.0`) is the
  single source; `contract/openapi.yaml`(+`.sha256`) are generated by `scripts/export_contract`;
  `tests/test_contract_export.py` (drift) and `tests/test_governance_docs.py` enforce it. See the
  "Contract versioning" section above for how the version moves exactly once.
- **Fail-loud telemetry already exists** on `SearchResponse` (`omitted_by_reason: dict[str,int]`,
  `promptguard_unavailable`, `unscanned_results`, `omitted_results`, `unresponsive_engines`) — new
  provider fields **extend** these; do not create a parallel telemetry path.
- **The seeded `kit_tools/` docs are current as of 2026-09-13.** `arch/SERVICE_MAP.md` (failure matrix),
  `docs/API_GUIDE.md`, `docs/TROUBLESHOOTING.md` and `arch/patterns/ERROR_HANDLING.md` carry the 17-code
  tables that `search_unavailable` extends; `docs/ENV_REFERENCE.md` carries the variable-naming checklist.
  Update them in the same change as the code (the AGENT_README session-end table says which).
- **Test blast radius the refactors must name.** `tests/test_orchestrator.py` patches
  `pipeline.orchestrator.httpx.AsyncClient` through one helper (17 tests); `tests/test_searxng_docker.py`
  and `tests/test_compose_fragments.py` import `_SEARXNG_ENGINES` / `_DEFAULT_SEARXNG_URL` from
  `pipeline.orchestrator` (the engine list is the settings.yml sync guard); `tests/test_contract_errors.py`
  asserts `len(ERROR_CODES) == 17` and `len(SEARCH_ERROR_CODES) == 2` in tests named "seventeen" and
  AST-sweeps raise sites in `orchestrator.py` + `retrieval_app.py` only; `tests/test_contract_schema.py`
  pins four response models in `_SCHEMA_MODELS`; `tests/test_governance_docs.py::test_there_are_exactly_six`
  pins the GOVERNANCE worked-examples table; six `ASGITransport` clients never run the lifespan;
  `_MAX_SEARCH_SNIPPET_LENGTH = 2_000` already truncates `content`; `omitted_by_reason` keys outside
  `contract.OMISSION_REASONS` are bucketed to `other` in `/metrics`.
