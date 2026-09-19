<!-- Template Version: 2.5.0 -->
---
feature: hardening-resource-envelope
status: active
session_ready: true
depends_on: [hardening-provider-bounds]
vision_ref: "T2.2 — Forage hardening"
type: epic-child
size: L
epic: forage-hardening
epic_seq: 6
epic_final: false
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Configurable Resource Envelope — Sized to the Host, Not the Deployment

> **Epic 6 of `epic-forage-hardening`.** Make the service's size an operator decision: CPU and memory
> limits, torch thread count, classification concurrency and the search latency target become
> documented knobs with today's numbers as defaults, the compose fragments parameterise them, a
> latency-target overrun becomes a `/metrics` counter instead of an invisible WARNING, and
> `docs/configuration.md` gains a sizing table the next spec's benchmark fills. Planning ruling 16
> is binding; ruling 5 governs the `/metrics` addition (inside the open 1.3.0 window); ruling 12 (spec
> 3's config key registry) receives every new key. Source: the Epic-2 cutover finding of 2026-09-12
> (`search_promptguard_local_latency_target_exceeded` under a hard 1-CPU cap on a 28-core host, owner
> declined a self-specced bump — "make it configurable instead"), and the vision constraint "default
> sizing (~1 CPU / 1 GB) must be operator-configurable".

## Overview

Today the envelope is spread across places nobody configures. `compose/minimal.yml:110` and
`compose/full.yml:80` hard-code `mem_limit: 1024m` and declare no `cpus` and no `healthcheck`;
`classification_concurrency` is pinned to `1 – 1` (`config.yaml:46`, `docs/configuration.md:489`,
`pipeline/extraction_limits.py:56,160-163`); nothing sets torch's thread count, so a container
behind a 1-CPU quota runs a torch that believes it owns every host core; and the two latency targets
are module constants (`pipeline/orchestrator.py:586-587`, `_LOCAL_PROMPTGUARD_TARGET_MS = 1_000`,
`_TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS = 5_000`) whose overrun is a `logger.warning(...,
extra={...})` (`:1083-1092`) that never renders in the container (`kit_tools/docs/GOTCHAS.md`,
"Nothing configures logging").

**Ruling 16** fixes the shape: env-substituted compose values with today's numbers as defaults
(`cpus: ${FORAGE_CPUS:-1}`, `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}`), a `promptguard_threads` key
applied with `torch.set_num_threads` at load, `classification_concurrency` widened to 1–8, the two
latency constants turned into `config.yaml` keys, and an additive `/metrics` counter
`search.promptguard_latency_target_exceeded` so an operator can *see* the overrun that motivated
this spec. A key-less, unchanged deployment behaves byte-for-byte as it does today.

Two things this spec deliberately does not do. It does not pick numbers for anyone: the sizing table
it adds has the structure and the boot-latency figures already on record
(`docs/configuration.md:362-364`, 19 s cold / 9 s warm on 1 vCPU / 1 GB — a *weights-boot* figure,
not a classify latency), and leaves the classify-latency column to spec 7's benchmark. And it does
not conflate liveness with health: the compose `healthcheck` proves the process answers, while
`/health`'s body keeps telling the truth about degradation (invariant 5).

## Goals

- Every sizing knob is one of: a compose variable with a default equal to today's value
  (`FORAGE_CPUS`, `FORAGE_MEM_LIMIT`), or a `config.yaml` key with a documented range and today's
  default (`promptguard_threads`, `classification_concurrency`, `search_promptguard_latency_target_ms`,
  `search_first_token_target_ms`); a deployment that sets none of them produces identical behaviour
  and identical wire output.
- A `/search` whose PromptGuard loop overruns the configured target increments
  `search.promptguard_latency_target_exceeded` by exactly 1, visible on `/metrics` under contract
  1.3.0.
- Both compose fragments carry identical envelope keys, defaults and healthcheck, asserted by
  `tests/test_compose_fragments.py`.
- `docs/configuration.md` has a sizing table with rows for 1 vCPU / 1 GB, 2 / 2 and 4 / 4 naming
  the knob values for each, with the classify-latency column marked "measured in spec 7".

## User Stories

### US-001: Sizing knobs in code — threads, concurrency range, latency targets, the counter

**Priority:** P1

**Description:** As an operator, I want the thread count, classification concurrency and search
latency targets to be `config.yaml` keys with today's defaults, and an overrun of the latency target
to be countable on `/metrics`, so I can size Forage to my host and see when it is under-sized.

**Independent Test:** With `config.yaml` overrides `promptguard_threads: 2`,
`classification_concurrency: 2`, `search_promptguard_latency_target_ms: 5` and a fake classifier
that sleeps 20 ms per call, one `/search` returns 200 and `/metrics` shows
`search.promptguard_latency_target_exceeded == 1`; `torch.set_num_threads` was called with `2`
(patched and asserted); two concurrent `/search` calls classify in parallel (the fake records
overlap). With no overrides, `set_num_threads(1)` is called, the counter stays 0 on a fast fake, and
every existing test passes unchanged.

**Implementation Hints:**
- `promptguard_threads`: new top-level `config.yaml` key (default `1`, range 1–16), read in the
  lifespan with the `_bounded_int` idiom (`pipeline/extraction_limits.py:79`; boot refuses an
  out-of-range value like `tests/test_app.py::test_lifespan_refuses_an_out_of_range_cache_bound`
  (`:1208`) does for cache bounds), passed into `PromptGuardClassifier.load(...)`
  (`promptguard/classifier.py:50-127`) which calls `torch.set_num_threads(n)` before
  `from_pretrained` (`:84`, `:98`). Torch is imported lazily there (`:82` logs `torch.__version__`);
  keep the import inside `load` so hermetic tests that never load stay torch-free. Register the key
  with spec 3's `KNOWN_CONFIG_KEYS` (ruling 12).
- `classification_concurrency` range 1–1 → 1–8: `pipeline/extraction_limits.py:160-163`
  (`extraction_settings_from_config`), `docs/configuration.md:489` row text ("Pinned at 1 for the
  same reason" becomes the sizing-table guidance: `threads × concurrency ≤ FORAGE_CPUS`). The
  semaphore is built from it at `retrieval_app.py:1225-1228` and `:1399-1402`; both sites are
  unchanged apart from the widened range.
- Latency targets: replace the module constants at `pipeline/orchestrator.py:586-587` with two
  `run_search_pipeline` keyword parameters defaulting to today's values
  (`promptguard_latency_target_ms: int = 1_000`, `first_token_target_ms: int = 5_000`), fed by the
  `/search` handler (`retrieval_app.py:1778-1815`) from new `config.yaml` keys
  `search_promptguard_latency_target_ms` (range 100–60000) and `search_first_token_target_ms`
  (range 100–120000); the INFO/WARNING `extra=` dicts at `:1067-1092` read the parameters. Keep the
  log lines (they are the only place the two figures appear together); add the counter beside the
  WARNING at `:1083`: `search_metrics.promptguard_latency_target_exceeded += 1` — `search_metrics`
  already reaches this function for `paid_calls`/`fallback_fired` (`SearchMetrics`,
  `retrieval_app.py:875-885`). `pipeline/orchestrator.py` is hashed: measure and record the
  rotation (ruling 6).
- The counter on the wire: add `promptguard_latency_target_exceeded: int` to `SearchMetricsResponse`
  (`retrieval_app.py:488-535`, `extra="forbid"` at `:491` — an unmodelled key 500s `/metrics`,
  `GOTCHAS.md` "Adding a /metrics counter without adding it to the model") with a description naming
  the config key; the `/metrics` order guards `test_served_metrics_are_the_handlers_dict_serialized`
  and `test_metrics_mirror_round_trips_the_served_body` (`tests/test_app.py`) pin key order —
  append, never insert. This moves `contract/openapi.yaml`: append a line to the 1.3.0
  `CONTRACT_VERSION` docstring entry (`pipeline/contract.py:26-68`), regenerate
  (`uv run python -m scripts.export_contract`), re-create `tests/golden/contract_1_3_0.json`
  (ruling 5). `contract.py` is hashed too — the same rotation record covers both files, measured
  once with both reverted as the control (the search epic's ninth rotation is the precedent).
- `docs/configuration.md`: rows for the three new keys (table format `| Key | Default | Allowed
  range | Purpose |` at `:480-490` for the `extraction:` block; top-level keys use the five-column
  table at `:437-439`), and `kit_tools/docs/MONITORING.md` gains the counter row beside
  `search.fallback_fired`.

**Acceptance Criteria:**
- [ ] `promptguard_threads` (default 1, range 1–16) is read at boot, refuses boot when out of range,
      and `torch.set_num_threads` is called with its value before the model loads (patched in a
      test that never downloads weights).
- [ ] `classification_concurrency` accepts 1–8; a value of 8 builds an `asyncio.Semaphore(8)`;
      `9` refuses boot; the default remains 1.
- [ ] `search_promptguard_latency_target_ms` (default 1000) and `search_first_token_target_ms`
      (default 5000) exist in `config.yaml`, are bounded at boot, reach `run_search_pipeline` from
      the `/search` handler, and the module constants `_LOCAL_PROMPTGUARD_TARGET_MS` /
      `_TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS` are gone (`grep -c` returns 0).
- [ ] `/metrics` serves `search.promptguard_latency_target_exceeded`, incremented once per `/search`
      whose PromptGuard loop exceeds the configured target and never otherwise; the two order-guard
      tests pass with the key appended.
- [ ] All four new keys are in spec 3's `KNOWN_CONFIG_KEYS` and have `docs/configuration.md` rows;
      the registry parity test passes; `MONITORING.md` documents the counter.
- [ ] Contract regenerated inside the 1.3.0 window: docstring line appended, `contract/openapi.yaml`
      + `.sha256` regenerated, `tests/golden/contract_1_3_0.json` re-created,
      `uv run python -m scripts.export_contract --check` clean; `contract_1_2_0.json` untouched.
- [ ] A deployment with no overrides produces identical `SearchResponse` JSON to the pre-story tree
      for the existing search fixtures (reuse spec 5 US-003's committed fixtures).
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce, both hashed files reverted as
      the control) and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and
      `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Compose envelope — `cpus`, `mem_limit`, healthcheck, sizing table

**Priority:** P1

**Description:** As an operator running the published fragments, I want to size the container from
my `.env` instead of editing the fragment, and I want Docker to know whether the process is alive,
so `docker compose up` on a 4-core host can use four cores without a fork of the file.

**Independent Test:** `docker compose -f compose/minimal.yml config` with no `.env` renders
`cpus: 1`, `mem_limit: 1024m` (or the equivalent normalised forms) and a `healthcheck` on
`/health`; with `FORAGE_CPUS=4 FORAGE_MEM_LIMIT=4096m` it renders those values; the same holds for
`compose/full.yml`; `tests/test_compose_fragments.py`'s new class asserts the literal
`${FORAGE_CPUS:-1}` / `${FORAGE_MEM_LIMIT:-1024m}` strings and the healthcheck block are identical in
both fragments. (The `docker compose config` check is a manual verification step the implementer
records in Implementation Notes; the suite stays hermetic.)

**Implementation Hints:**
- Both fragments' `forage` service: add `cpus: ${FORAGE_CPUS:-1}` beside `mem_limit`, change
  `mem_limit` to `${FORAGE_MEM_LIMIT:-1024m}` (`compose/minimal.yml:105-110`, `compose/full.yml:80`),
  and rewrite the envelope comment at `minimal.yml:105-109` to explain the two variables and point
  at the sizing table. Comments only elsewhere; every existing key and value stays (the
  2026-09-19 housekeeping batch edited comments in both files — preserve its wording).
- Healthcheck. **Implementer check:** `grep -n curl Dockerfile` — at planning time the image installs
  `curl` explicitly for this purpose (`Dockerfile:48`, `:66-67`, "curl for the container
  healthcheck"). If `curl` is present: `test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8020/health"]`.
  If it has been removed by then: `test: ["CMD", "python3", "-c", "import urllib.request,sys;
  sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8020/health', timeout=5).status == 200
  else 1)"]`. Either way `interval: 30s`, `timeout: 5s`, `retries: 3`, `start_period: 30s` —
  `/health` answers 200 as soon as uvicorn listens, even while weights download and the body says
  `degraded` (invariant 5), so the start period covers process start, not the weights fetch. Write
  that sentence in the fragment comment: the healthcheck is liveness; `/health`'s body is health.
- `tests/test_compose_fragments.py`: new class `TestResourceEnvelope` using the `_services(
  fragments[name])[_FORAGE_SERVICE]` idiom (`:129`, `:290`) and the `raw_fragments` fixture (`:125`)
  for the literal substitution strings (the YAML loader keeps `${FORAGE_CPUS:-1}` as a string);
  assert both fragments carry identical `cpus`, `mem_limit` and `healthcheck` values, the healthcheck
  targets `127.0.0.1:8020/health`, and the `_comment_prose` (`:167`) of each fragment names both
  variables. There is no `mem_limit` assertion today — this class is the first.
- Sizing table in `docs/configuration.md` (new subsection "Sizing the container" beside the
  reference-envelope paragraph at `:362-372`): columns `Host envelope | FORAGE_CPUS | FORAGE_MEM_LIMIT
  | promptguard_threads | classification_concurrency | cache.max_bytes | classify latency`; rows
  `1 vCPU / 1 GB (reference)` = `1 / 1024m / 1 / 1 / 33554432`, `2 / 2 GB` = `2 / 2048m / 2 / 1 /
  67108864`, `4 / 4 GB` = `4 / 4096m / 2 / 2 / 134217728`; the last column reads "measured in spec
  7 (`feature-hardening-promptguard-86m` US-004)" in every row; a rule line `threads × concurrency
  ≤ FORAGE_CPUS`. State that the boot-latency figures at `:362` are weights-load times and not
  classify latency.
- `tests/test_compose_fragments.py::TestFragmentsAreDocumented` (the "fragments are documented"
  class) may assert the new subsection heading exists; follow its pattern for the other doc claims.

**Acceptance Criteria:**
- [ ] Both fragments declare `cpus: ${FORAGE_CPUS:-1}` and `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}`
      on the `forage` service, and a `healthcheck` block targeting `http://127.0.0.1:8020/health`
      with `interval: 30s`, `timeout: 5s`, `retries: 3`, `start_period: 30s`; no other key or value
      in either fragment changes (`git diff` shows only these keys and comment lines).
- [ ] The healthcheck command matches what the image contains (curl if `Dockerfile` installs it,
      the `python3 -c` form otherwise) — the implementer records which branch applied and the
      `docker compose config` output for both fragments, with and without the two variables set,
      in this spec's Implementation Notes.
- [ ] `tests/test_compose_fragments.py::TestResourceEnvelope` asserts identical envelope keys and
      defaults across both fragments, the healthcheck target, and that each fragment's comment
      prose names `FORAGE_CPUS` and `FORAGE_MEM_LIMIT`.
- [ ] `docs/configuration.md` has the "Sizing the container" table with the three rows and the
      `threads × concurrency ≤ FORAGE_CPUS` rule; the classify-latency column defers to spec 7 by
      name; the paragraph distinguishes weights-boot latency from classify latency.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Operator documentation for the envelope

**Priority:** P2

**Description:** As a third-party operator, I want every place that describes how to run Forage to
name the envelope variables and the sizing rule, so I do not discover the 1-CPU default by reading a
WARNING that never renders.

**Independent Test:** `grep -l FORAGE_CPUS README.md docs/configuration.md kit_tools/docs/
DEPLOYMENT.md kit_tools/docs/ENV_REFERENCE.md kit_tools/arch/INFRA_ARCH.md` lists all five files;
`kit_tools/arch/DECISIONS.md` has an entry dated 2026-09 titled with "sized to the host"; no doc
still describes `classification_concurrency` as pinned.

**Implementation Hints:**
- `kit_tools/docs/ENV_REFERENCE.md`: rows for `FORAGE_CPUS` and `FORAGE_MEM_LIMIT` in the existing
  seven-column format (`| VAR | default | unset-behavior | required | secret? | read-site | notes |`),
  with `read-site` = "compose substitution only — Forage itself never reads these" so nobody adds
  them to `_CLEARED_ENV_VARS`.
- `README.md` quickstart: one sentence after the `docker compose up` line pointing at the sizing
  table; `kit_tools/docs/DEPLOYMENT.md` and `kit_tools/arch/INFRA_ARCH.md`: replace the "1 GB
  container" statements with "the reference envelope (1 vCPU / 1 GB) — configurable, see
  `docs/configuration.md` § Sizing the container"; `kit_tools/docs/MONITORING.md`: the
  `search.promptguard_latency_target_exceeded` row from US-001 gains the runbook sentence "raise
  `FORAGE_CPUS` or `promptguard_threads` before raising the target".
- `kit_tools/arch/DECISIONS.md`: a decision entry "Sized to the host, not the deployment
  (2026-09-19)" citing the Epic-2 cutover finding and ruling 16, and the rotation table row from
  US-001.
- `kit_tools/docs/GOTCHAS.md`: a short active gotcha "torch sees the host's cores, not the cgroup
  quota" explaining why `promptguard_threads` defaults to 1.
- `docs/configuration.md:489`'s `classification_concurrency` row text no longer says "Pinned at 1".

**Acceptance Criteria:**
- [ ] `ENV_REFERENCE.md` has `FORAGE_CPUS` and `FORAGE_MEM_LIMIT` rows marked compose-substitution
      only; `tests/test_hermeticity.py`'s exact-set test is unchanged.
- [ ] `README.md`, `DEPLOYMENT.md`, `INFRA_ARCH.md` and `MONITORING.md` carry the sentences above;
      `grep -rn 'Pinned at 1' docs/ kit_tools/` returns nothing.
- [ ] `DECISIONS.md` has the dated decision entry; `GOTCHAS.md` has the torch-threads gotcha under
      "Active Gotchas".

## Edge Cases

- `FORAGE_CPUS` fractional (`0.5`) is valid Compose input and is passed through; the sizing rule
  rounds down to 0 threads worth of advice — the table says "below 1 vCPU is unsupported" (US-002).
- `promptguard_threads` larger than the cgroup quota oversubscribes; torch still runs, slower —
  documented, not refused, because Forage cannot read the quota portably (US-001, US-003).
- `classification_concurrency: 8` with `promptguard_threads: 4` on `FORAGE_CPUS=1` is accepted at
  boot; the sizing rule is guidance, not a gate (US-001).
- A latency target of exactly the measured duration does not count (strictly greater, matching the
  existing `>` at `orchestrator.py:1083`) (US-001).
- A `/search` that classifies zero results (all omitted before stage 3) measures a near-zero loop
  and never counts (US-001).
- `docker compose config` with `FORAGE_MEM_LIMIT=abc` fails at Compose, before Forage starts — the
  fragment comment says the value takes Docker's byte-unit syntax (US-002).
- The healthcheck passes while `/health` reports `degraded` — by design; the comment says so
  (US-002).

## Out of Scope

- Choosing the 86M model or measuring its latency — spec 7 (which fills the sizing table's last
  column).
- Auto-detecting the cgroup CPU quota to size threads automatically.
- Making the 1 MiB provider bounds or the 10 MB fetch cap configurable.
- Kubernetes manifests or any deployment target other than the two compose fragments.
- Changing `/health`'s semantics (invariant 5 stands; liveness is the compose healthcheck's job).

## Assumptions

- Poppy is the only consumer; the `/metrics` counter is additive and defaulted.
- The 1.3.0 contract window is open when this spec executes (spec 1 US-004 opened it) and spec 3
  US-003's `KNOWN_CONFIG_KEYS` registry exists.
- Docker Compose v2 variable substitution with `:-` defaults is available on every supported host
  (it is a Compose spec feature).
- The image keeps `curl` (installed at `Dockerfile:66-67` for exactly this healthcheck); the
  `python3 -c` branch is the fallback, not the plan.
- Today's defaults (1 CPU, 1024m, 1 thread, concurrency 1, 1000 ms, 5000 ms) are the reference
  envelope and stay the defaults; this spec changes no default.

## Technical Considerations

- `pipeline/orchestrator.py` and `pipeline/contract.py` are hashed; US-001 is this spec's only
  rotation (one measurement, both files as the control). US-002 and US-003 rotate nothing.
- `torch.set_num_threads` must run in the process that classifies; classification runs via
  `asyncio.to_thread` in the same process (`pipeline/stage3_promptguard.py:130`), so setting it once
  at load is sufficient. Intra-op threads only; inter-op is left at default.
- `extra="forbid"` on every metrics model (`retrieval_app.py:638-660`) means the new counter must be
  added to the model *and* the `SearchMetrics` dataclass in the same commit, or `/metrics` 500s.
- Compose keeps `cpus` as a float and `mem_limit` as a string; the YAML loader in
  `tests/test_compose_fragments.py` sees the unsubstituted `${...}` strings — assert on those.
- The healthcheck binds to `127.0.0.1` inside the container namespace, so the loopback-only posture
  test (`TestDeploymentPosture::test_every_published_port_binds_to_loopback`) is unaffected.

## Related Documentation

- Configuration: `docs/configuration.md` (reference envelope `:362-372`, `extraction:` table)
- Deployment: [DEPLOYMENT.md](../docs/DEPLOYMENT.md), [INFRA_ARCH.md](../arch/INFRA_ARCH.md)
- Monitoring: [MONITORING.md](../docs/MONITORING.md)
- Env contract: [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("Nothing configures logging", "/metrics counter
  without the model 500s")
- Rotations: `docs/bootstrap-notes.md`, [DECISIONS.md](../arch/DECISIONS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Env-substituted compose values with today's numbers as defaults, not a second fragment
per size.
**Rationale:** Compose `${VAR:-default}` substitution keeps one file per mode and makes an unset
`.env` identical to today; a per-size fragment set would drift (the fragments already have a
"duplication does not drift" test class).
**Alternatives considered:** `deploy.resources.limits` — Swarm-flavoured and ignored by plain
`docker compose` without `--compatibility`; a third fragment — drift.
**Source:** `compose/minimal.yml:105-110`, `compose/full.yml:80`; `tests/test_compose_fragments.py`
class list.

**Decision:** Count latency-target overruns on `/metrics` rather than fixing logging.
**Rationale:** The WARNING at `orchestrator.py:1083-1092` carries its data in `extra=` and nothing
configures logging in-container (GOTCHAS); a counter is the observability floor the search epic
already established (`search.fallback_fired`, `search.paid_calls`).
**Alternatives considered:** `logging.basicConfig` at startup — a separate, larger decision
(BACKLOG "Structured request logging") with its own credential-hygiene review.
**Source:** `pipeline/orchestrator.py:586-587,1063-1092`; `retrieval_app.py:488-535,875-885`.

**Decision:** `promptguard_threads` defaults to 1 and is applied with `torch.set_num_threads` at load.
**Rationale:** No thread setting exists anywhere (`promptguard/classifier.py`, `Dockerfile`,
`docker-entrypoint.sh` — grep confirmed); torch defaults to the host core count, which behind a
1-CPU quota is the oversubscription the cutover finding describes.
**Alternatives considered:** `OMP_NUM_THREADS` in the Dockerfile — an env default the operator
would have to know to override; a config key is discoverable and documented.
**Source:** cutover finding 2026-09-12; `promptguard/classifier.py:50-127`.

**Decision:** The healthcheck uses the `curl` the image already installs for this purpose.
**Rationale:** `Dockerfile:48` says "curl for the container healthcheck" and no fragment ever added
one; the intent is on record.
**Source:** `Dockerfile:48,66-67`; both fragments lack `healthcheck:` (grep).

### Scope Adjustments

- The classify-latency benchmark moved to spec 7 US-003/US-004 (needs weights and an owner gate);
  this spec only provides the knobs the benchmark varies and the table it fills.

### Decisions Made

- Today's values are the defaults; this spec changes no default and no wire byte for an unchanged
  deployment.
- Liveness (compose healthcheck) and health (`/health` body) are kept distinct on purpose.

## Clarifications

### Session 2026-09-19
- Q: Configurable envelope or a self-specced bump to the owner's host? → A: Configurable; sized to
  the host, not the deployment (owner, 2026-09-12; ruling 16).
- Q: Where do the latency targets live? → A: `config.yaml` keys with today's defaults; overruns
  counted on `/metrics` (ruling 16).
- Q: Does this spec pick sizes? → A: No; it documents the reference envelope and two larger rows,
  and spec 7's benchmark fills the classify-latency column.

## Open Questions

- [ ] Should `FORAGE_CPUS` also drive `extraction.extraction_concurrency` (the PDF worker admission
      slot)? Non-blocking; default no — the worker is memory-bound (`child_address_space_bytes`).
- [ ] Whether the 1 MiB provider bounds join the sizing knobs — carried from spec 5; non-blocking.
