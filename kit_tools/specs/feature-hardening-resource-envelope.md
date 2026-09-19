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
execution_order: [US-001, US-004, US-002, US-003]
created: 2026-09-19
updated: 2026-09-19
---

# Feature Spec: Configurable Resource Envelope — Sized to the Host, Not the Deployment

> **Epic 6 of `epic-forage-hardening`.** Make the service's size an operator decision: CPU and memory
> limits, torch thread count, classification concurrency and the search latency targets become
> documented knobs whose defaults change nothing, the compose fragments parameterise them, a
> latency-target overrun becomes a `/metrics` counter (with a high-water mark) instead of an invisible
> WARNING, and `docs/configuration.md` gains a sizing table the next spec's benchmark fills. Planning
> ruling 16 **as revised in validation round 1 (R16)** is binding; ruling 5 governs the `/metrics`
> additions (inside the open 1.3.0 window); ruling 12 (spec 3's config key registry) receives every
> new key; R33 forbids any secret-bearing capture. Source: the Epic-2 cutover finding of 2026-09-12
> (`search_promptguard_local_latency_target_exceeded` under a hard 1-CPU cap on a 28-core host, owner
> declined a self-specced bump — "make it configurable instead"), and the vision constraint "default
> sizing (~1 CPU / 1 GB) must be operator-configurable". Validation round 1 split the old US-001 into
> US-001 (threads, concurrency) and US-004 (latency targets, counter) and moved `/search`'s missing
> classification semaphore to spec 2 US-001, where the semaphore is actually acquired.

## Overview

Today the envelope is spread across places nobody configures. `compose/minimal.yml:110` and
`compose/full.yml:80` hard-code `mem_limit: 1024m` and declare **no `cpus` and no `healthcheck`** — a
container gets every host core; `classification_concurrency` is pinned to `1 – 1`
(`config.yaml:46`, `pipeline/extraction_limits.py:19,160-163`) **for memory reasons**
(`docs/configuration.md:488-489`: the 512 MiB parent reservation inside `mem_limit: 1024m`), and it
gates only the routes that acquire the semaphore — `/extract` today (`pipeline/orchestrator.py:539`,
the one call site `retrieval_app.py:1731`), `/retrieve` and `/search` after spec 2 US-001; nothing
sets torch's thread count, so torch believes it owns every host core even behind a 1-CPU quota; and
the two latency targets are module constants (`pipeline/orchestrator.py:586-587`,
`_LOCAL_PROMPTGUARD_TARGET_MS = 1_000`, `_TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS = 5_000`) whose overrun
is a `logger.warning(..., extra={...})` (`:1082-1092`) that never renders in the container
(`kit_tools/docs/GOTCHAS.md`, "Nothing configures logging").

**R16** fixes the shape without changing anything for an operator who sets nothing: `cpus:
${FORAGE_CPUS:-0}` (0 = no limit, which is today's state; the implementer proves Compose renders it
that way), `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` (today's value), a `promptguard_threads` key whose
default `0` means "leave torch's default" and whose positive values call `torch.set_num_threads` in
a guarded step of their own, `classification_concurrency` widened to 1–8 under a **memory** rule
(`FORAGE_MEM_LIMIT ≥ 512 MiB + concurrency × 384 MiB`), the latency constants turned into
`config.yaml` keys read through a module-owned settings builder, and two additive `/metrics` fields —
`search.promptguard_latency_target_exceeded` (count) and `search.promptguard_latency_max_ms`
(high-water mark) — so an operator can *see* the overrun that motivated this spec and tell "marginally
over" from "oversubscribed". A deployment that sets none of the knobs produces byte-identical wire
output and the same runtime behaviour as today.

Two things this spec deliberately does not do. It does not pick numbers for anyone: the sizing table
it adds has the structure, the memory rule, and the boot-latency figures already on record
(`docs/configuration.md:362-364`, 19 s cold / 9 s warm on 1 vCPU / 1 GB — a *weights-boot* figure,
not a classify latency), and leaves the classify-latency column to spec 7's benchmark. And it does
not conflate liveness with health: the compose `healthcheck` proves the process answers (and
captures no body), while `/health`'s body keeps telling the truth about degradation (invariant 5) —
a Docker-`healthy` container may be running with the classifier unloaded, and the docs say so.

## Goals

- Every sizing knob is one of: a compose variable whose default is today's state (`FORAGE_CPUS`
  → no limit, `FORAGE_MEM_LIMIT` → `1024m`), or a `config.yaml` key with a documented range and a
  default that changes nothing (`promptguard_threads` 0, `classification_concurrency` 1,
  `search_promptguard_latency_target_ms` 1000, `search_first_token_target_ms` 5000); a deployment
  that sets none of them produces identical `SearchResponse` output on spec 5 US-005's wire pins and
  the same torch thread count, CPU quota and semaphore size as before.
- A `/search` whose PromptGuard loop overruns the configured target increments
  `search.promptguard_latency_target_exceeded` by exactly 1 and raises
  `search.promptguard_latency_max_ms` to at least the measured duration, visible on `/metrics` under
  contract 1.3.0.
- Both compose fragments carry identical envelope keys, defaults and healthcheck, asserted by
  `tests/test_compose_fragments.py`; no secret value is ever pasted into a tracked file to prove it.
- `docs/configuration.md` has a top-level "Sizing the container" section with rows for 1 vCPU / 1 GB,
  2 / 2 and 4 / 4, the memory rule, which columns need a `config.yaml` bind mount, and a
  classify-latency column marked "measured in spec 7".

## User Stories

### US-001: CPU and memory sizing knobs at boot — `promptguard_threads`, `classification_concurrency`

**Priority:** P1

**Description:** As an operator, I want torch's thread count and the classification concurrency to
be `config.yaml` keys whose defaults change nothing, so I can size Forage to my host without a fork of
the image, and so a wrong value refuses boot instead of silently disabling the classifier.

**Independent Test:** With `promptguard_threads: 2` the classifier's load path calls
`torch.set_num_threads(2)` before `from_pretrained` (torch patched; no weights downloaded), and a
patched `set_num_threads` that raises leaves the classifier loaded and emits the distinct log line
`promptguard_threads_apply_failed`; with `classification_concurrency: 2` the lifespan builds
`asyncio.Semaphore(2)` and two concurrent `POST /retrieve` calls (spec 2 US-001 made `/retrieve`
acquire the semaphore) classify with overlap under a fake classifier that records timestamps, while
`classification_concurrency: 1` serialises them; `9` and `promptguard_threads: 17` refuse boot with
the module-owned error classes; with no overrides `set_num_threads` is never called and the semaphore
is `Semaphore(1)`, and every existing test passes unchanged.

**Implementation Hints:**
- `promptguard_threads` is owned by `promptguard/classifier.py` (not hashed; already the lazy torch
  owner). Follow the three settings precedents (`cache.py:244-267`, `pipeline/extraction_limits.py:
  79-106`, `pipeline/search_providers/brave.py:193-275`): a module-owned
  `PromptGuardConfigurationError(ValueError)`, a module-local bounded-int copy (the re-statement is
  a recorded decision, `brave.py:231-233`; a cross-module import of a private `_bounded_int` fails
  pyright strict outside `tests/`), and a public `promptguard_threads_from_config(config) -> int`
  (default `0`, range 0–16; `0` = do not touch torch). The value is applied by
  `PromptGuardClassifier.configure_threads(n)` — an attribute on the classifier object the lifespan
  already builds at `retrieval_app.py:1323`, set **before** `WeightAcquisition` starts (`:1324`) —
  and read inside `load()` (`promptguard/classifier.py:50-56`) in its own guarded step: a separate
  `try/except` around `torch.set_num_threads(n)` that logs `promptguard_threads_apply_failed` and
  proceeds to `from_pretrained`, placed *outside* the blanket `except Exception` at `:110-115` (which
  sets `_loaded = False` and would misattribute a torch refusal to missing weights). Keep the torch
  import lazy (`:72`). Because the attribute lives on the classifier object, `model_fetcher`'s retry
  path (`WeightAcquisition`, `acquire_and_load` `:1530`, `_load_verified` `:1474-1495`,
  `SupportsWeightLoad` `:1060`) is untouched — `acquire_and_load`'s parameters are "not a
  configuration surface" (`model_fetcher.py:1558`) and stay that way. The lifespan calls the builder
  unconditionally beside `extraction_settings_from_config` (`:1215`), stores
  `app.state.promptguard_threads`, and the module-level default block at `retrieval_app.py:1378-1402`
  gets a `0` default so lifespan-less transports have the attribute.
- `classification_concurrency` 1–1 → 1–8: `pipeline/extraction_limits.py:160-163` currently passes
  `CLASSIFICATION_CONCURRENCY` (`:19`) as both default and `maximum=`; introduce
  `_MAX_CLASSIFICATION_CONCURRENCY = 8` beside `_MAX_ADMISSION_QUEUE_DEPTH` (`:27`) and pass it as the
  maximum, leaving `CLASSIFICATION_CONCURRENCY = 1` as the default. State in the module docstring
  that `classification_concurrency` intentionally stops sharing `extraction_concurrency`'s
  default-is-the-maximum idiom (`:152-158`), because `extraction_concurrency` stays memory-pinned to
  one worker. The semaphore sites (`retrieval_app.py:1225-1228`, `:1399-1402`) are unchanged apart
  from the widened range.
- `docs/configuration.md:489` (`classification_concurrency` row) is rewritten by **this** story:
  range 1–8, "raise it only with memory to match: `FORAGE_MEM_LIMIT ≥ 512 MiB + concurrency ×
  384 MiB` (the parent reservation plus one PromptGuard working set per concurrent classification);
  above that, the failure is an OOM kill, which `/health` cannot report; it bounds the routes that
  acquire the classification semaphore — `/extract`, `/retrieve` and `/search`". `:488`
  (`extraction_concurrency`, "Pinned at 1 — the memory reservation above assumes exactly one
  worker") is **correct and must not be edited**. The new `promptguard_threads` row goes in the
  top-level table (`:430`) with "0 = torch's default (every visible core); set it to the CPU quota
  when `FORAGE_CPUS` is set — torch cannot see a cgroup quota".
- `KNOWN_CONFIG_KEYS` (spec 3, ruling 12) gains `promptguard_threads`; `kit_tools/docs/
  ENV_REFERENCE.md` `### Top-level keys` (`:81-94`) gains its row with the `Read site` column
  filled (the `search_brave_*` rows `:90-92` are the shape).
- Performance knobs are **not** `derive_sanitizer_revision` inputs (unlike `promptguard_threshold`,
  which is): they change latency, not sanitization output. No hashed file moves in this story.

**Acceptance Criteria:**
- [ ] `promptguard_threads` (default 0, range 0–16) is read at boot through
      `promptguard_threads_from_config`, refuses boot out of range with
      `PromptGuardConfigurationError`, and a positive value reaches `torch.set_num_threads` before
      `from_pretrained` via `configure_threads` on the lifespan's classifier object (patched torch,
      no weights); `0` never calls it.
- [ ] A `set_num_threads` failure leaves the classifier loaded and logs
      `promptguard_threads_apply_failed`; the blanket handler's "PromptGuard model not available"
      line is not emitted for it; pinned.
- [ ] `classification_concurrency` accepts 1–8 via `_MAX_CLASSIFICATION_CONCURRENCY`; `8` builds
      `asyncio.Semaphore(8)`; `9` refuses boot with `ExtractionConfigurationError`; the default stays
      1; `extraction_concurrency` stays 1–1.
- [ ] Two concurrent `/retrieve` classifications overlap at concurrency 2 and serialise at 1 (fake
      classifier with timestamps; the `/retrieve` semaphore from spec 2 US-001).
- [ ] `docs/configuration.md:489` states the 1–8 range, the memory rule, the OOM consequence and the
      routes it bounds; `:488` is unchanged; the `promptguard_threads` row exists; both keys are in
      `KNOWN_CONFIG_KEYS` and `ENV_REFERENCE.md`'s top-level table.
- [ ] `git diff --stat` shows no change under `pipeline/orchestrator.py`, `pipeline/contract.py`,
      `models.py` or `contract/`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: Search latency targets as config — the overrun counter and high-water mark

**Priority:** P1

**Description:** As an operator, I want the search latency targets to be `config.yaml` keys with
today's defaults, and an overrun to be countable on `/metrics` with its magnitude, so I can tell
"nudge the target" from "add cores" without a log line that never renders.

**Independent Test:** With `search_promptguard_latency_target_ms: 5` and a fake classifier that
sleeps 20 ms per call, one `/search` returns 200 and `/metrics` shows
`search.promptguard_latency_target_exceeded == 1` and `search.promptguard_latency_max_ms >= 20`; a
second, fast `/search` leaves both unchanged; with no overrides the counter stays 0 on a fast fake, the
max stays 0 until the first `/search`, and spec 5 US-005's wire pins are byte-identical.

**Implementation Hints:**
- Settings builder, module-owned (the precedent trio again): new non-hashed module
  `pipeline/search_targets.py` with a frozen `SearchTargets(promptguard_latency_target_ms: int =
  1_000, first_token_target_ms: int = 5_000)`, `SearchTargetsConfigurationError`, a module-local
  bounded-int copy, and `search_targets_from_config(config)` reading
  `search_promptguard_latency_target_ms` (range 100–60000) and `search_first_token_target_ms`
  (range 100–120000). The lifespan calls it unconditionally, stores `app.state.search_targets`, and
  the module-level default block (`retrieval_app.py:1378-1402`) gets `SearchTargets()`.
  `search_first_token_target_ms` is **log-only today**: it feeds the `extra=` dict at
  `orchestrator.py:1076-1078,1089` and nothing else; the `docs/configuration.md` row says so in one
  sentence ("tunes the `search_promptguard_complete` log line; no counter compares it") so nobody
  hunts for an effect.
- Replace the module constants at `pipeline/orchestrator.py:586-587` with two `run_search_pipeline`
  keyword parameters defaulting to today's values (`promptguard_latency_target_ms: int = 1_000`,
  `first_token_target_ms: int = 5_000`), fed by the `/search` handler (`retrieval_app.py:1811`) from
  `app.state.search_targets`; the INFO/WARNING `extra=` dicts at `:1068-1092` read the parameters.
  Keep the log lines. Beside the WARNING at `:1082` add the two increments on the sink — the local
  is named `metrics` (`:863`, rebound from `search_metrics`), not `search_metrics`:
  `metrics.promptguard_latency_target_exceeded += 1` and `metrics.promptguard_latency_max_ms =
  max(metrics.promptguard_latency_max_ms, int(promptguard_duration_ms))` (the max is updated on every
  `/search`, not only on overrun). Five sites per field: `SearchMetricsSink` Protocol
  (`pipeline/orchestrator.py:747-756`, including its "two counters" docstring sentence — now four
  fields with spec 5's `provider_compressed_body`), `_NullSearchMetrics.__init__` (`:759-771`),
  `SearchMetrics.__init__` (`retrieval_app.py:875-886`, a plain class), the `/metrics` handler dict
  (`:1517-1524`, appended after the last key), and `SearchMetricsResponse` (`:488-533`, appended
  last, descriptions naming the config key). Missing any of the first two fails pyright strict; a
  model field without its class counterpart 500s `/metrics` (`extra="forbid"`, GOTCHAS).
- Contract: this moves `contract/openapi.yaml` (`/metrics` has a `response_model`); append the two
  lines to the 1.3.0 `CONTRACT_VERSION` docstring entry in the `* ``1.3.0`` — …` format
  (`pipeline/contract.py:26-68`, R34) and regenerate (`uv run python -m scripts.export_contract`).
  `tests/golden/contract_1_3_0.json` is **unchanged** — `/metrics` models are outside
  `_SCHEMA_MODELS` (`tests/test_contract_schema.py:28-35`) and are pinned by
  `tests/test_contract_metrics.py` instead (the order guards at `:135` and `:161`; the schema-fullness
  test at `:272-292` requires non-empty descriptions). Register both fields in spec 8 US-002's 1.3.0
  coverage sweep when it exists (`tests/test_contract_schema.py:99,189-236` is the 1.2.0 precedent).
- `pipeline/orchestrator.py` and `pipeline/contract.py` are both hashed: one rotation measured with
  both files reverted as the control (the search epic's ninth rotation is the precedent); record per
  ruling 6.
- Docs: `docs/configuration.md` top-level rows for both keys; `kit_tools/docs/MONITORING.md` gains
  rows for the counter and the max beside `search.fallback_fired`, with the runbook sentence keyed on
  the max: "under 2× the target, raise the target; above it, raise `FORAGE_CPUS` /
  `promptguard_threads` (see Sizing the container)"; `ENV_REFERENCE.md` `### Top-level keys` rows;
  `KNOWN_CONFIG_KEYS` entries.

**Acceptance Criteria:**
- [ ] `search_promptguard_latency_target_ms` (default 1000) and `search_first_token_target_ms`
      (default 5000) exist in `config.yaml`, are bounded at boot by `search_targets_from_config`
      (`SearchTargetsConfigurationError` out of range), reach `run_search_pipeline` from the `/search`
      handler via `app.state.search_targets`, and `grep -c '_LOCAL_PROMPTGUARD_TARGET_MS\|
      _TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS' pipeline/orchestrator.py` returns 0.
- [ ] `/metrics` serves `search.promptguard_latency_target_exceeded` (incremented once per `/search`
      whose loop exceeds the target, strictly greater, never otherwise) and
      `search.promptguard_latency_max_ms` (updated on every `/search`); all five sites per field are
      edited; the two order-guard tests pass with the keys appended.
- [ ] Contract regenerated inside the 1.3.0 window: two docstring lines appended, `contract/
      openapi.yaml` + `.sha256` regenerated, `uv run python -m scripts.export_contract --check`
      clean; `tests/golden/contract_1_3_0.json` and `contract_1_2_0.json` byte-identical to before
      (stated in Implementation Notes with the `_SCHEMA_MODELS` reason).
- [ ] With no overrides, spec 5 US-005's wire pins (`tests/test_search_pipeline_pins.py`) pass
      unchanged.
- [ ] `docs/configuration.md` rows (the first-token row saying "log-only today"), `MONITORING.md`
      rows with the magnitude-keyed runbook sentence, `ENV_REFERENCE.md` rows and
      `KNOWN_CONFIG_KEYS` entries exist for both keys.
- [ ] `sanitizer_revision` rotation measured (revert-and-reproduce, both hashed files reverted as
      the control) and recorded in `docs/bootstrap-notes.md`, `CLAUDE.md` and
      `kit_tools/arch/DECISIONS.md`.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Compose envelope — `FORAGE_CPUS`, `FORAGE_MEM_LIMIT`, liveness healthcheck, sizing table

**Priority:** P1

**Description:** As an operator running the published fragments, I want to size the container from
my `.env` instead of editing the fragment, and I want Docker to know whether the process is alive,
so `docker compose up` on a 4-core host can be told to use four cores without a fork of the file —
and so pulling the updated fragment changes nothing until I set a variable.

**Independent Test:** Hermetic half: `tests/test_compose_fragments.py` asserts the literal
`${FORAGE_CPUS:-0}` / `${FORAGE_MEM_LIMIT:-1024m}` substitution strings, the healthcheck block, the
commented-out `config.yaml` bind mount, and the envelope comment prose (lowercase — `_comment_prose`
`:167-179` lower-cases and keeps whole-line comments only) in **both** fragments, and
`TestTheDuplicationDoesNotDrift` (`:705-743`) asserts the two fragments' envelope values are
identical with its `len(set(...)) == 1` idiom. Manual half (R33): from a scratch directory with a
placeholder env — `env -i PATH="$PATH" HF_TOKEN=placeholder SEARXNG_SECRET=placeholder docker compose
-f compose/minimal.yml config` — the implementer records **only** the rendered `cpus:`, `mem_limit:`
and `healthcheck:` lines of the `forage` service and the `docker compose version` line, for both
fragments, with and without `FORAGE_CPUS=4 FORAGE_MEM_LIMIT=4096m`, in this spec's Implementation
Notes; the rendered `environment:` block is never pasted anywhere.

**Implementation Hints:**
- Both fragments' `forage` service: add `cpus: ${FORAGE_CPUS:-0}` beside `mem_limit`, change
  `mem_limit` to `${FORAGE_MEM_LIMIT:-1024m}` (`compose/minimal.yml:105-110`, `compose/full.yml:80`).
  **Implementer check (criterion):** `0` must render as "no CPU limit" — Docker's `--cpus 0` means
  unlimited; confirm with the placeholder render above and, if Compose instead rejects or
  mis-renders `0`, move the `cpus` key into a documented override file `compose/envelope.yml`
  (`docker compose -f compose/minimal.yml -f compose/envelope.yml up`) and state which branch applied.
  Write an envelope comment in **both** files — `minimal.yml:105-109` is rewritten, `full.yml:80` has
  none today and gets new prose — naming both variables, "0 = no CPU limit (today's behaviour)",
  that `FORAGE_MEM_LIMIT` takes Docker's byte-unit syntax (`FORAGE_MEM_LIMIT=abc` fails at Compose,
  before Forage starts), "below 1 vCPU is unsupported", and the pointer to the sizing table.
  Comments only elsewhere; every existing key and value stays (the 2026-09-19 housekeeping batch
  edited comments in both files — preserve its wording).
- Healthcheck (both fragments): the `Dockerfile` is single-stage (`FROM` at `:44`) and installs
  `curl` at `:67` for exactly this ("curl for the container healthcheck", `:48`); no `HEALTHCHECK`
  instruction exists and neither fragment declares one. Use
  `test: ["CMD", "curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1:8020/health"]` — `-o /dev/null`
  so Docker's `State.Health.Log[].Output` captures no `/health` body (the probe inspects status only,
  as `/health`'s docstring says) — with `interval: 30s`, `timeout: 5s`, `retries: 3`, `start_period:
  30s`. `/health` answers 200 as soon as uvicorn listens, even while weights download and the body
  says `degraded` (invariant 5). The fragment comment states three things: the healthcheck is
  liveness, `/health`'s body is health; plain `docker compose` reports an unhealthy container but
  never restarts it (`restart:` reacts to exits); and a `healthy` container may be running with the
  classifier unloaded — never gate traffic, `depends_on: service_healthy` or a consumer's activation
  on it. The `python3 -c urllib` form is the fallback only if `curl` leaves the image.
- `docs/configuration.md`: a new **top-level** `## Sizing the container` section placed immediately
  before `## config.yaml` (`:414`) — not inside `### Weights acquisition` (`:307-412`), where the
  reference-envelope paragraph at `:362-372` lives; that paragraph gains one cross-reference sentence
  distinguishing weights-boot latency from classify latency. Table columns `Host envelope |
  FORAGE_CPUS | FORAGE_MEM_LIMIT | promptguard_threads | classification_concurrency | cache.max_bytes
  | classify latency`; rows `1 vCPU / 1 GB (reference)` = `1 / 1024m / 1 / 1 / 33554432`, `2 / 2 GB`
  = `2 / 2048m / 2 / 1 / 67108864`, `4 / 4 GB` = `4 / 4096m / 2 / 2 / 134217728`; the last column
  reads "measured in spec 7 (`feature-hardening-promptguard-86m` US-004)" in every row. Rules stated
  beneath: the CPU rule `promptguard_threads × classification_concurrency ≤ FORAGE_CPUS`, the
  **memory** rule `FORAGE_MEM_LIMIT ≥ 512 MiB + classification_concurrency × 384 MiB` (the three
  rows co-scale CPU and memory; an operator raising concurrency alone is outside them), "with no
  variable set the fragment imposes no CPU limit", and the verification sentence "confirm
  `FORAGE_MEM_LIMIT` landed via `/metrics` `extraction.cgroup_memory_max_bytes`"
  (`retrieval_app.py:1010-1028`). State which columns the compose path **cannot** set: the three
  `config.yaml` columns require a bind mount — the fragments do not mount `config.yaml` today
  (`Dockerfile:164` bakes it; the only volume is `forage-model-cache`) — so both fragments gain a
  commented-out `# - ./config.yaml:/app/config.yaml:ro` volume line with a comment pointing here,
  and the table says so.
- Tests: `tests/test_compose_fragments.py` — cross-fragment parity goes into
  `TestTheDuplicationDoesNotDrift` as `test_the_shared_services_declare_the_same_envelope` (the
  `len(set(...)) == 1` idiom at `:709-720`); value-level claims go into a new `TestResourceEnvelope`
  using `raw_fragments` (`:125`; the YAML loader keeps `${FORAGE_CPUS:-0}` as a string) and
  `_services(fragments[name])` (`:129`): the two substitution strings, the healthcheck target and
  `-o /dev/null`, the commented-out bind-mount line, and `"forage_cpus" in prose` / `"forage_mem_limit"
  in prose` / `"no cpu limit" in prose` / `"liveness" in prose` (lowercase). The doc-section
  assertion follows `tests/test_contract_metrics.py::_posture_section` (`:379-386`) — the README-only
  class is `TestTheFragmentsAreDocumented` (`:747`) and has no other doc pattern to copy. There is no
  `mem_limit` assertion today; these are the first.
- Minimum Compose: service-level `cpus` is a Compose Spec key (Compose v2); the record in
  Implementation Notes includes `docker compose version`.

**Acceptance Criteria:**
- [ ] Both fragments declare `cpus: ${FORAGE_CPUS:-0}` and `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` on
      the `forage` service, a `healthcheck` block with `curl -fsS -o /dev/null
      http://127.0.0.1:8020/health`, `interval: 30s`, `timeout: 5s`, `retries: 3`, `start_period:
      30s`, and a commented-out `config.yaml` bind-mount line; no other key or value in either
      fragment changes (`git diff` shows only these keys and comment lines).
- [ ] The placeholder-env render (`env -i …`, R33) shows `cpus` rendered as no limit for `0` and as
      `4` with `FORAGE_CPUS=4`, and `mem_limit` `1024m` / `4096m`, for both fragments; Implementation
      Notes record only the `cpus:`/`mem_limit:`/`healthcheck:` lines and `docker compose version`
      — never an `environment:` block or any env value; or, if `0` does not render as unlimited, the
      `compose/envelope.yml` branch is applied and recorded instead.
- [ ] `TestTheDuplicationDoesNotDrift::test_the_shared_services_declare_the_same_envelope` and
      `TestResourceEnvelope` pass, asserting the substitution strings, the healthcheck target with
      `-o /dev/null`, the bind-mount comment, and the four lowercase prose claims in each fragment.
- [ ] `docs/configuration.md` has the top-level `## Sizing the container` section before
      `## config.yaml`, with the three rows, both rules, the "no CPU limit by default" sentence, the
      bind-mount requirement for the `config.yaml` columns, the `cgroup_memory_max_bytes`
      verification sentence, and the classify-latency column deferring to spec 7 by name; the
      weights-acquisition paragraph distinguishes boot latency from classify latency.
- [ ] Tests written/updated for new functionality.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Operator documentation for the envelope

**Priority:** P2

**Description:** As a third-party operator, I want every place that describes how to run Forage to
name the envelope variables, the sizing rules and what the healthcheck does not mean, so I do not
discover a knob from a WARNING that never renders or trust a `healthy` container that is not
classifying.

**Independent Test:** `grep -l FORAGE_CPUS README.md docs/configuration.md kit_tools/docs/DEPLOYMENT.md kit_tools/docs/ENV_REFERENCE.md kit_tools/arch/INFRA_ARCH.md kit_tools/arch/SECURITY.md`
lists all six files on one line; `kit_tools/arch/DECISIONS.md` has an entry dated 2026-09 titled with
"sized to the host"; `grep -n 'Pinned at 1' docs/configuration.md` returns only the
`extraction_concurrency` row (`:488`); `grep -n 'liveness' kit_tools/docs/DEPLOYMENT.md
kit_tools/docs/MONITORING.md` hits both.

**Implementation Hints:**
- `kit_tools/docs/ENV_REFERENCE.md`: a new section `### Container envelope (never read by Forage;
  Compose substitution only)` in the five-column shape of `### Companion SearXNG container` (`:56-58`,
  `| Variable | Default | Required | Secret | Effect |`) with `FORAGE_CPUS` and `FORAGE_MEM_LIMIT` —
  not the runtime table (`:38`, seven columns with `Legacy alias`), so nobody adds them to
  `_CLEARED_ENV_VARS`. The four new `config.yaml` keys' top-level rows were added by US-001/US-004;
  this story verifies they are present.
- `README.md` quickstart: one new sentence after the `docker compose up` line pointing at
  `docs/configuration.md` § Sizing the container (README has no envelope statement to replace).
- Replace the fixed-envelope statements — the phrase is "1 vCPU / 1 GB" — with "the reference
  envelope (1 vCPU / 1 GB), configurable: see `docs/configuration.md` § Sizing the container" at
  `kit_tools/arch/INFRA_ARCH.md:223,258,272`, `kit_tools/docs/DEPLOYMENT.md:189,356`, and the
  `mem_limit: 1024m` literals at `INFRA_ARCH.md:61,146,265` / `DEPLOYMENT.md:151,357`; reword
  `kit_tools/arch/SERVICE_MAP.md:270` ("sized for the 1 GiB", about `cache.max_bytes`) and the
  arithmetic comments the sizing table invalidates — `config.yaml:29-31,39-41`,
  `pipeline/extraction_limits.py:41-45`, `docs/configuration.md:343,452,485` — to "the reference
  envelope; see Sizing the container" (a comment-only `.py` edit, so the gates run).
- `kit_tools/docs/MONITORING.md`: the healthcheck paragraph — liveness only; must never gate traffic,
  `depends_on: service_healthy`, or a consumer's activation; the classifier's state is `/health`'s
  `promptguard_loaded` / `degraded_reasons` plus `/metrics` `search.unscanned_results` — and the
  `cgroup_memory_max_bytes` verification note beside the US-004 runbook line.
  `kit_tools/docs/DEPLOYMENT.md`: the same liveness paragraph beside the compose instructions.
- `kit_tools/arch/SECURITY.md` (R16): the "Upload ceilings" sentence (`:180-182`, "may lower but never
  raise") names `classification_concurrency` as the one key whose configured maximum now exceeds its
  shipped default, with the memory rule; the admission-control table's PromptGuard row (`:197`)
  states the configurable 1–8 range and the memory caveat.
- `kit_tools/arch/DECISIONS.md`: "Sized to the host, not the deployment (2026-09-19)" citing the
  Epic-2 cutover finding and R16, plus the rotation row from US-004. `kit_tools/docs/GOTCHAS.md`:
  two short active gotchas — "torch sees the host's cores, not the cgroup quota" (why
  `promptguard_threads` exists) and "a healthy container is not a classifying container".
- Upgrade note (one sentence in `docs/configuration.md` § Sizing the container): pulling the updated
  fragments changes nothing until a variable or key is set — no CPU limit, `1024m`, torch default
  threads, concurrency 1.

**Acceptance Criteria:**
- [ ] `ENV_REFERENCE.md` has the "Container envelope" section with both variables;
      `tests/test_hermeticity.py`'s exact-set test is unchanged; the four new top-level key rows are
      present.
- [ ] `README.md`, `DEPLOYMENT.md`, `INFRA_ARCH.md`, `SERVICE_MAP.md`, `MONITORING.md`, `SECURITY.md`
      carry the sentences above; the `config.yaml`, `extraction_limits.py` and `docs/configuration.md`
      arithmetic comments point at the sizing table; `grep -n 'Pinned at 1' docs/configuration.md`
      returns only `:488`.
- [ ] `DECISIONS.md` has the dated decision entry; `GOTCHAS.md` has both gotchas under "Active
      Gotchas"; the upgrade sentence exists.
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- `FORAGE_CPUS` unset or `0` → no CPU limit, today's behaviour; fractional (`0.5`) is valid Compose
  input and passed through; the table says "below 1 vCPU is unsupported" (US-002).
- `promptguard_threads: 0` → `set_num_threads` never called (US-001).
- `promptguard_threads` larger than the cgroup quota oversubscribes; torch still runs, slower —
  documented, not refused: auto-sizing is out of scope (the cgroup v2 quota is readable the way
  `retrieval_app._cgroup_memory_snapshot()` reads `memory.max`; a boot WARNING for `threads ×
  concurrency > quota` is deferred) (US-001, US-003).
- A `set_num_threads` failure never disables the classifier; it logs its own line (US-001).
- `classification_concurrency: 8` with `promptguard_threads: 4` on `FORAGE_CPUS=1`, `FORAGE_MEM_LIMIT`
  `1024m` is accepted at boot; the docs say the consequence above the memory rule is an OOM kill
  (US-001, US-002).
- A latency target of exactly the measured duration does not count (strictly greater, matching the
  existing `>` at `orchestrator.py:1082`); the max is updated regardless (US-004).
- A `/search` that classifies zero results (all omitted before stage 3) measures a near-zero loop,
  never counts, and may still raise the max from 0 (US-004).
- `FORAGE_MEM_LIMIT=abc` fails at Compose, before Forage starts — the fragment comment says the value
  takes Docker's byte-unit syntax (US-002).
- The healthcheck passes while `/health` reports `degraded`, and a `healthy` container may have the
  classifier unloaded — by design; the comment and the docs say so (US-002, US-003).
- Plain `docker compose` reports an unhealthy container and never restarts it (US-002).

## Out of Scope

- Choosing the 86M model or measuring its latency — spec 7 (which fills the sizing table's last
  column).
- Auto-detecting the cgroup CPU quota to size threads automatically (deferred; see Edge Cases).
- Parallelising `/search`'s per-result classification loop; wiring the classification semaphore into
  `/search` and `/retrieve` is spec 2 US-001, not this spec.
- Making the 1 MiB provider bounds or the 10 MB fetch cap configurable (this closes the carried-over
  question from spec 5: no).
- An image-level `HEALTHCHECK` instruction, and healthchecks on the companion `searxng`/`valkey`
  services — compose-level liveness on `forage` only.
- Kubernetes manifests or any deployment target other than the two compose fragments.
- Changing `/health`'s semantics (invariant 5 stands; liveness is the compose healthcheck's job).
- Giving `search_first_token_target_ms` an effect beyond the log line (it exists for symmetry with
  the ruling; documented as log-only).

## Assumptions

- Poppy is the only consumer; the `/metrics` fields are additive and defaulted.
- The 1.3.0 contract window is open when this spec executes (spec 1 US-004 opened it), spec 3
  US-003's `KNOWN_CONFIG_KEYS` registry exists, spec 2 US-001 has wired the classification semaphore
  into `/retrieve` and `/search`, and spec 5 US-005's wire pins exist.
- Docker Compose v2 (Compose Spec) is the supported runner; `${VAR:-default}` substitution and the
  service-level `cpus` key are Compose Spec features; `cpus: 0` is "no limit" (verified by the
  implementer, with the override-file fallback if not).
- The image keeps `curl` (installed at `Dockerfile:67` for exactly this healthcheck); the `python3
  -c` branch is the fallback, not the plan.
- Today's state is the default in every knob; this spec changes no default and no wire byte for an
  unchanged deployment.
- One PromptGuard 22M working set is ~384 MiB (the `compose/minimal.yml:105-109` arithmetic); the
  memory rule is revisited by spec 7's benchmark for 86M.

## Technical Considerations

- Rotation ledger (ruling 6, R32): US-004 is this spec's only rotation (`orchestrator.py` and
  `contract.py`, one measurement, both reverted as the control); US-001, US-002 and US-003 rotate
  nothing. The four new keys are performance knobs and change no sanitization output, so unlike
  `promptguard_threshold` they are not `derive_sanitizer_revision` inputs — the rotation comes only
  from the source bytes.
- `torch.set_num_threads` must run in the process that classifies; classification runs via
  `asyncio.to_thread` in the same process (`pipeline/stage3_promptguard.py:130`), so setting it once
  before the first load is sufficient. Intra-op threads only; inter-op is left at default.
- `extra="forbid"` on every metrics model (`retrieval_app.py:638-660`) means each new field is added
  to the model *and* the counter class in the same commit, or `/metrics` 500s; `SearchMetricsSink`
  and `_NullSearchMetrics` must carry it too or pyright fails.
- Golden movement: US-004 moves `contract/openapi.yaml` and the anchor but not the golden.
- Compose keeps `cpus` as a number and `mem_limit` as a string; the YAML loader in
  `tests/test_compose_fragments.py` sees the unsubstituted `${...}` strings — assert on those.
- The healthcheck binds to `127.0.0.1` inside the container namespace, so
  `TestDeploymentPosture::test_every_published_port_binds_to_loopback` (`:250-268`, iterates
  `_published_ports` only) is unaffected.
- `classification_concurrency` gates every route that acquires `app.state.classification_semaphore`;
  before spec 2 US-001 that is `/extract` only (`retrieval_app.py:1731` → `orchestrator.py:539`).

## Related Documentation

- Configuration: `docs/configuration.md` (Sizing the container; `extraction:` table `:471-490`)
- Deployment: [DEPLOYMENT.md](../docs/DEPLOYMENT.md), [INFRA_ARCH.md](../arch/INFRA_ARCH.md)
- Security: [SECURITY.md](../arch/SECURITY.md) (Upload ceilings, Admission control)
- Monitoring: [MONITORING.md](../docs/MONITORING.md)
- Env contract: [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("Nothing configures logging", "/metrics counter
  without the model 500s")
- Rotations: `docs/bootstrap-notes.md`, [DECISIONS.md](../arch/DECISIONS.md)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Env-substituted compose values whose defaults are today's state (`cpus` 0 = none,
`mem_limit` 1024m), not a second fragment per size and not a newly imposed CPU cap.
**Rationale:** Neither fragment declares `cpus` today, so any non-zero default would be a new hard
cap on every operator who pulls the fragment — the exact incident this spec exists to fix. `${VAR:-
default}` keeps one file per mode and makes an unset `.env` identical to today.
**Alternatives considered:** `cpus: ${FORAGE_CPUS:-1}` — rejected in validation round 1 (a new cap
sold as a preserved default); `deploy.resources.limits` — Swarm-flavoured; a third fragment — drift.
**Source:** `compose/minimal.yml:105-110`, `compose/full.yml:80` (no `cpus`, no `healthcheck`).

**Decision:** Count latency-target overruns on `/metrics` with a high-water mark, rather than fixing
logging.
**Rationale:** The WARNING at `orchestrator.py:1082-1092` carries its data in `extra=` and nothing
configures logging in-container (GOTCHAS); a counter is the observability floor the search epic
established, and a bare count cannot distinguish 1,001 ms from 30,000 ms — the max makes the runbook
sentence answerable.
**Alternatives considered:** `logging.basicConfig` at startup — a separate decision (BACKLOG
"Structured request logging") with its own credential-hygiene review.
**Source:** `pipeline/orchestrator.py:586-587,1064-1092`; `retrieval_app.py:488-535,875-886`.

**Decision:** `promptguard_threads` defaults to 0 (torch's default) and is owned by
`promptguard/classifier.py`, applied via a classifier attribute in a guarded step.
**Rationale:** No thread setting exists anywhere (grep confirmed); pinning to 1 would be a per-classify
regression of up to N× on every uncapped multi-core host, so the default must be inert. The lifespan
never calls `load()` — `model_fetcher._load_verified` does, through `SupportsWeightLoad` — so an
attribute on the classifier object is the only seam that leaves `acquire_and_load`'s "not a
configuration surface" decision intact and survives the retry path.
**Alternatives considered:** `OMP_NUM_THREADS` in the Dockerfile — an env default the operator would
have to know; threading a kwarg through five `model_fetcher` seams — reverses a recorded decision;
`os.sched_getaffinity` as the default — cgroup v1/v2 semantics make it approximate; deferred.
**Source:** cutover finding 2026-09-12; `promptguard/classifier.py:50-115`; `model_fetcher.py:
1060-1077,1474-1495,1530-1558`; `retrieval_app.py:1323-1324`.

**Decision:** `classification_concurrency` widens under a memory rule, not a CPU rule.
**Rationale:** `docs/configuration.md:488-489` pins it "for the same reason" as
`extraction_concurrency` — the 512 MiB parent reservation; the semaphore is the only backpressure on
PromptGuard inference for the unauthenticated routes, so the table must bound it against
`FORAGE_MEM_LIMIT`, and an overrun is an OOM kill `/health` cannot report.
**Source:** `docs/configuration.md:488-489`; `compose/minimal.yml:105-109`; `SECURITY.md:180-197`.

**Decision:** The healthcheck uses the `curl` the image already installs, with `-o /dev/null`, and is
documented as liveness that must never gate anything.
**Rationale:** `Dockerfile:48` says "curl for the container healthcheck" and no fragment ever added
one; `-o /dev/null` keeps the probe from capturing `/health` bodies into `docker inspect`; a
`healthy` container may run with the classifier unloaded (invariant 5), so the docs must say what the
signal does not mean.
**Source:** `Dockerfile:44,48,67`; both fragments lack `healthcheck:` (grep).

### Scope Adjustments

- The classify-latency benchmark moved to spec 7 US-003/US-004 (needs weights and an owner gate);
  this spec only provides the knobs the benchmark varies and the table it fills.
- Validation round 1: US-001 split into US-001 (threads, concurrency; no rotation) and US-004
  (latency targets, counter, max; one rotation); `/search` semaphore parity moved to spec 2 US-001;
  `cpus` default changed from `1` to `0`; `promptguard_threads` default changed from `1` to `0`;
  the `docker compose config` capture bounded to a placeholder render (R33); the sizing table gained
  the memory rule and the bind-mount requirement; the high-water mark added.

### Decisions Made

- Today's state is the default in every knob; this spec changes no default, no runtime behaviour and
  no wire byte for an unchanged deployment.
- Liveness (compose healthcheck) and health (`/health` body) are kept distinct on purpose, and the
  docs say what `healthy` does not mean.
- `search_first_token_target_ms` stays a key per ruling 16 but is documented as log-only; three
  reviewers proposed dropping it — recorded, overruled by the ruling, mitigated by the doc sentence.
- Overruled: a shared public `bounded_int` helper — the module-local copy is the recorded precedent
  (`brave.py:231-233`); a fourth copy in `promptguard/classifier.py` follows it.
- Overruled: `os.sched_getaffinity` as the threads default — approximate under cgroup v1; deferred.
- Overruled: an image-level `HEALTHCHECK` — out of scope, stated.

## Clarifications

### Session 2026-09-19
- Q: Configurable envelope or a self-specced bump to the owner's host? → A: Configurable; sized to
  the host, not the deployment (owner, 2026-09-12; ruling 16).
- Q: Where do the latency targets live? → A: `config.yaml` keys with today's defaults; overruns
  counted on `/metrics` (ruling 16).
- Q: Does this spec pick sizes? → A: No; it documents the reference envelope and two larger rows,
  and spec 7's benchmark fills the classify-latency column.

### Session 2026-09-19 (validation round 1)
- Rulings applied: R16 (`cpus` 0 = none with the render check and override-file fallback; `mem_limit`
  default kept; `promptguard_threads` 0 = torch default, applied outside the blanket except;
  concurrency 1–8 under the memory rule with the doc row rewritten; `/search` semaphore parity moved
  to spec 2; healthcheck `-o /dev/null` as liveness), R31 (US-001/US-004 split; `execution_order`),
  R33 (placeholder-env excerpt only), R32 (rotation ledger: US-004 alone), R34 (docstring format),
  ruling 5 (window criteria on US-004), ruling 12 (registry entries).
- Q: Which routes does `classification_concurrency` bound? → A: Every route that acquires the
  semaphore — `/extract` today; `/retrieve` and `/search` after spec 2 US-001; the docs row says so.
- Q: Does the golden move? → A: No — `/metrics` models are outside `_SCHEMA_MODELS`; the document and
  anchor move.

## Open Questions

- [ ] Should `FORAGE_CPUS` also drive `extraction.extraction_concurrency` (the PDF worker admission
      slot)? Non-blocking; default no — the worker is memory-bound (`child_address_space_bytes`).
- [ ] Whether Compose renders `cpus: 0` as "no limit" on every supported version — resolved by the
      implementer's render check with the override-file fallback; non-blocking.
