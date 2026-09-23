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
> limits, torch and tokenizer thread counts, classification concurrency and the search latency
> targets become documented knobs whose defaults change nothing, the compose fragments parameterise
> them and gain a liveness healthcheck, a latency-target overrun becomes a `/metrics` counter (with a
> per-process high-water mark) instead of a WARNING whose payload never renders, an under-sized
> memory ceiling becomes a boot WARNING instead of an unreportable OOM kill, and `docs/configuration.md`
> gains a sizing section the next spec's benchmark completes. Planning ruling 16 **as corrected in
> validation round 2 (R16)** is binding; rulings 5 and 36 govern the two window changes; ruling 12
> (spec 3's config key registry) receives every new key; R33 (corrected) forbids any secret-bearing
> capture; R39 makes every doc sweep a grep. Source: the Epic-2 cutover finding of 2026-09-12
> (`search_promptguard_local_latency_target_exceeded` under a hard 1-CPU cap on a 28-core host, owner
> declined a self-specced bump — "make it configurable instead"), and the vision constraint "default
> sizing (~1 CPU / 1 GB) must be operator-configurable". Validation round 1 split the old US-001 into
> US-001 (threads, concurrency) and US-004 (latency targets, counter) and moved `/search`'s missing
> classification semaphore to spec 2; round 2 corrected the memory rule, closed the Compose render
> question, made the render mechanically secret-free, moved the sizing prose into US-003, and gave
> US-002 the contract regenerate the healthcheck docstrings need; round 3 removed the memory rule's
> double count and gave it a cache term, pinned the shipped default silent, renamed the high-water
> mark for what it measures, made CI render both envelope branches, corrected "latency, not
> sanitization" against spec 2 US-006's bounded wait, and declined every further split (R37); round 4
> (R16 corrected again, R43) made the memory rule's terms conditional and honest — the cache term only
> under the in-memory backend, the child term read from configuration — corrected the "only tighten"
> invariant to name every key that can be raised, re-keyed the runbook on spec 2's wait-timeout
> counter, placed the memory check where all its operands exist, scoped every grep (retained goldens
> and the seed cache excluded), listed the envelope keys in the bind-mount warning, documented the
> probe's effect on the reconnect counters, and stated US-002's Docker requirement honestly. Validation
> round 5 (R16 corrected, the final pass — applied without re-review) removed the last two module-local
> bounded-int copies in favour of `pipeline/config_bounds`, registered both new readers with spec 3's
> AST sweep, made the memory rule model-dependent (a named parent constant plus the selected
> classifier's resident delta) and gave its Valkey branch the one-read term spec 4 promised, renamed the
> thread reader's error for what it bounds, reconciled the `SECURITY.md:374` restatement with its own
> grep, stated the reconnect WARNING rate per failure mode, and closed the pin question against spec 5.

## Overview

Today the envelope is spread across places nobody configures. `compose/minimal.yml:110` and
`compose/full.yml:80` hard-code `mem_limit: 1024m` and declare **no `cpus` and no `healthcheck`** — a
container gets every host core; `classification_concurrency` is pinned to `1 – 1`
(`config.yaml:46`, `pipeline/extraction_limits.py:19,160-163`) **for memory reasons**
(`docs/configuration.md:488-489`: the 512 MiB parent reservation inside `mem_limit: 1024m`), and it
gates only the routes that acquire the semaphore — `/extract` today (`pipeline/orchestrator.py:539`,
the one call site `retrieval_app.py:1731`), `/retrieve` and `/search` after spec 2 US-006; nothing
sets torch's intra-op thread count or the fast tokenizer's Rust pool, so both believe they own every
host core even behind a 1-CPU quota; and the two latency targets are module constants
(`pipeline/orchestrator.py:586-587`, `_LOCAL_PROMPTGUARD_TARGET_MS = 1_000`,
`_TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS = 5_000`) whose overrun is a `logger.warning(..., extra={...})`
(`:1082-1092`): the WARNING line itself renders in the container, but its `extra=` payload — the
measured duration and the target — does not (`kit_tools/docs/GOTCHAS.md`, "Nothing configures
logging"), so the magnitude is invisible.

**R16 as corrected** fixes the shape without changing anything for an operator who sets nothing:
`cpus: ${FORAGE_CPUS:-0}` — which Compose renders by **omitting the key** at `0` (verified against
Compose v2.40.3 in validation round 1), i.e. no CPU limit, today's state — `mem_limit:
${FORAGE_MEM_LIMIT:-1024m}` (today's value), a `promptguard_threads` key whose default `0` means "leave
torch's and the tokenizer's defaults" and whose positive values pin both pools in a guarded step of
their own, `classification_concurrency` widened to 1–8 under a **memory** rule whose terms are named
honestly and counted once — the 512 MiB parent reservation **with no classification in flight and the
default (22M) classifier resident**, plus — once spec 7 makes the classifier selectable — the selected
model's measured resident delta over the 22M, read from a per-model table the sizing section carries
(`0` for the 22M; spec 7 fills the 86M row), one
classifier working set per concurrent classification (a provisional 64 MiB until spec 7's benchmark
measures it), the pypdf child's `RLIMIT_AS` per extraction slot (`extraction.child_address_space_bytes`
as configured — 384 MiB shipped, 128–512 MiB allowed — never the literal), and a cache term that
depends on the backend: **when the in-memory backend is selected** (`VALKEY_URL` unset,
`retrieval_app.py:265-266`) the cache's `cache.max_bytes`; **under Valkey** (`compose/full.yml:73`,
whose memory is its own container's) one in-flight read, spec 4's `cache.max_value_bytes` (4 MiB
shipped) — which evaluates to 992 MiB at the shipped defaults with the in-memory backend
(`minimal.yml`) and 964 MiB under Valkey (`full.yml`), a stated 32 / 60 MiB margin under `mem_limit:
1024m` (round 2's wording double-counted the first classification and landed on exactly 1024 MiB by
accident) — with an advisory boot WARNING when the cgroup's `memory.max` is *strictly below* the
rule, the latency constants turned into `config.yaml` keys read through a module-owned
settings builder, and two additive `/metrics` fields — `search.promptguard_latency_target_exceeded`
(count) and `search.sanitization_latency_max_ms` (per-process high-water mark of the per-result
sanitization loop the target is measured over — `pipeline/orchestrator.py:960-1064`: structural scan
and PromptGuard per result, plus any classification-semaphore wait once spec 2 US-006 wires it; the
name says what it measures) — so an operator can *see* the overrun that motivated this spec. A
deployment that sets none of the knobs produces byte-identical wire output and the same limits as
today; the one visible difference is the healthcheck, which is new and inert (Docker gains a health
column; nothing acts on it).

One correction round 3 made to this spec's own framing. These knobs change latency, and under spec 2
US-006's bounded wait on the classification semaphore, latency decides whether a `/search` or
`/retrieve` result is scanned at all: a waiter that outlives `promptguard_wait_seconds` takes the
classifier-unavailable branch — `unavailable_blocked` for a fail-closed request, `unavailable_allowed`
(served unscanned and marked) for a fail-open one. The keys are still not `derive_sanitizer_revision`
inputs (the hash covers the sanitizer's rules, not its throughput), but the sizing guidance says the
under-sizing consequence out loud and the Security Considerations section records it.

Two things this spec deliberately does not do. It does not pick numbers for anyone: the sizing
section it adds has the structure, both rules, the boot-latency figures already on record
(`docs/configuration.md:362-364`, 19 s cold / 9 s warm on 1 vCPU / 1 GB — a *weights-boot* figure,
not a classify latency), and leaves the classify-latency column and the working-set coefficient to
spec 7's benchmark. And it does not conflate liveness with health: the compose `healthcheck` proves
the process answers (and captures no body), while `/health`'s body keeps telling the truth about
degradation (invariant 5) — a Docker-`healthy` container may be running with the classifier
unloaded, and the docs say so.

## Goals

- Every sizing knob is one of: a compose variable whose default is today's state (`FORAGE_CPUS`
  → no `cpus` key rendered, `FORAGE_MEM_LIMIT` → `1024m`), or a `config.yaml` key with a documented
  range and a default that changes nothing (`promptguard_threads` 0, `classification_concurrency` 1,
  `search_promptguard_latency_target_ms` 1000, `search_first_token_target_ms` 5000); a deployment
  that sets none of them produces identical `SearchResponse` output on spec 5 US-005's wire pins and
  the same torch thread count, tokenizer parallelism, CPU quota and semaphore size as before.
- A `/search` whose per-result sanitization loop overruns the configured target increments
  `search.promptguard_latency_target_exceeded` by exactly 1 and raises
  `search.sanitization_latency_max_ms` to at least the measured duration, visible on `/metrics` under
  contract 1.3.0, with both descriptions naming the window they measure.
- A boot whose cgroup `memory.max` is strictly below the memory rule for the configured concurrency,
  child address space, cache backend and selected classifier emits exactly one
  `envelope_memory_rule_unmet` WARNING naming the numbers, the model and the backend it counted, and
  proceeds; a boot
  whose limit equals or exceeds the rule — the shipped defaults against `mem_limit: 1024m` included —
  emits none.
- Both compose fragments carry identical envelope keys, defaults and healthcheck, asserted by
  `tests/test_compose_fragments.py` and parsed on every commit by the required `lint` job; no secret
  value is ever pasted into a tracked file to prove any of it.
- `docs/configuration.md` has a top-level "Sizing the container" section with rows for 1 vCPU / 1 GB,
  2 / 2 and 4 / 4, the memory rule with its named coefficient, the CPU rule with its precondition,
  the `config.yaml` delivery path, the sentence tying under-sizing to `promptguard_wait_seconds`, and
  a classify-latency column marked "measured in spec 7"; no tracked file still states that the
  fragments declare no healthcheck or no CPU quota, that the `extraction:` knobs can only tighten
  without naming the three keys that may be raised above their shipped values
  (`classification_concurrency`, `child_address_space_bytes`, `admission_queue_depth`), or that the
  latency target is a fixed 1000 ms.

## User Stories

### US-001: CPU and memory sizing knobs at boot — `promptguard_threads`, `classification_concurrency`

**Priority:** P1

**Description:** As an operator, I want torch's and the tokenizer's thread counts and the
classification concurrency to be `config.yaml` keys whose defaults change nothing, so I can size
Forage to my host without a fork of the image, so a wrong value refuses boot instead of silently
disabling the classifier, and so an under-sized memory ceiling is a WARNING I can grep instead of an
OOM kill nobody reports.

**Independent Test:** Following `tests/test_model_fetcher.py:779-791`
(`test_the_loader_is_pinned_to_safetensors`, which patches `transformers.AutoTokenizer` and
`transformers.AutoModelForSequenceClassification` around `PromptGuardClassifier().load()`): with
`configure_threads(2)` applied and `torch.set_num_threads` patched, `load()` calls
`set_num_threads(2)` and sets `os.environ["TOKENIZERS_PARALLELISM"] = "false"` before
`AutoTokenizer.from_pretrained` (the test seeds the variable with `monkeypatch.setenv` first, so
teardown restores it — it is not in `tests/conftest.py`'s `_CLEARED_ENV_VARS` (`:45-54`) and must not
be added, since `tests/test_hermeticity.py:122` pins that set exactly); a patched `set_num_threads`
that raises leaves `load()` returning `True` and emits `promptguard_threads_apply_failed` at WARNING
with the count and the exception type in the message; with `configure_threads(0)` the call never
happens and `TOKENIZERS_PARALLELISM` is unchanged from the sentinel the test seeded — asserted as
"unchanged", never as "absent", which would depend on the host environment.
`promptguard_threads_from_config({})` is `0`, `{"promptguard_threads": 17}` and `"abc"` raise
`PromptGuardThreadsConfigurationError`. With `classification_concurrency: 2` the lifespan builds
`asyncio.Semaphore(2)` and two concurrent `POST /retrieve` calls (spec 2 US-006 made `/retrieve`
acquire the semaphore) classify with overlap under a fake classifier that records timestamps, while
`1` serialises them; `9` refuses boot with `ExtractionConfigurationError`. With
`_cgroup_memory_snapshot` patched to report `memory.max` = 1 GiB and `classification_concurrency:
4` on the in-memory backend, boot logs exactly one `envelope_memory_rule_unmet` WARNING naming
`memory_max`, `required`, the operands, `model_id`, `parent_bytes` and `cache_backend=memory
cache_term_bytes=33554432`, and `/health` answers 200 (required = 512 + 256 + 384 + 32 = 1184 MiB);
with the snapshot reporting exactly `1073741824` and
the **shipped defaults** (`classification_concurrency` 1, `extraction_concurrency` 1,
`child_address_space_bytes` 384 MiB, in-memory `cache.max_bytes` 32 MiB) no WARNING is logged
(required = 992 MiB; the 32 MiB margin is asserted, not implied); with the snapshot reporting exactly
the rule's sum no WARNING is logged (strictly-below); with the snapshot reporting `None` no WARNING is
logged; with `VALKEY_URL` set — through `_started_with_valkey_url` (`tests/test_app.py:1270-1306`),
the seam that reaches the Valkey branch; the `_running_app` / `test_lifespan_*` shape cannot — and
`classification_concurrency: 4` against 1 GiB the WARNING fires with `cache_backend=valkey
cache_term_bytes=4194304` (required = 1156 MiB — under Valkey the term is one in-flight read, spec 4's
`cache.max_value_bytes`, 4 MiB shipped, never `cache.max_bytes`); with a second model id whose
`CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` row is monkeypatched to 448 MiB selected, and the other
defaults against 1 GiB, the WARNING fires with `model_id=<that id> parent_bytes=1006632960` (required =
992 + 448 = 1440 MiB — the parent term reads the selected model's row; with the default id the delta
is `0` and every figure above is unchanged); with the in-memory backend, `cache.max_bytes` at its
128 MiB ceiling (`cache.py:229`) and the other
defaults against 1 GiB the WARNING fires (required = 1088 MiB); with `extraction.
child_address_space_bytes: 536870912` (512 MiB) and the other defaults against 1 GiB the WARNING fires
(required = 1120 MiB — the rule reads configuration, not `MAX_CHILD_ADDRESS_SPACE_BYTES`). With no
overrides `set_num_threads` is never called,
the environment is untouched, the semaphore is `Semaphore(1)`, and every existing test passes
unchanged.

**Implementation Hints:**
- `promptguard_threads` is owned by `promptguard/classifier.py` (not hashed; already the lazy torch
  owner). Follow the three settings precedents for the *shape* (`cache.py:244-267`,
  `pipeline/extraction_limits.py:79-106`, `pipeline/search_providers/brave.py:193-275`) but **not**
  for the bounds helper (R16 corrected in round 5 — no new bounded-int copies; round-4 codebase-fit
  critical): a module-owned `PromptGuardThreadsConfigurationError(ValueError)` — named for what it
  bounds, the `PromptGuard<What>Error` shape `PromptGuardBudgetExceededError`
  (`promptguard/classifier.py:210`) already establishes, so the generic `PromptGuardConfigurationError`
  stays free for spec 7's `promptguard_settings_from_config` triple in `pipeline/stage3_promptguard.py`
  (spec 7 runs *after* this spec, so this story cannot import a class spec 7 declares, and two classes
  of one name in two modules would leave `MONITORING.md`'s boot-failure row ambiguous — round-4
  codebase-fit review; Decisions Made) — the bound read through `pipeline/config_bounds.bounded_int`
  (spec 2 US-001; takes the exception class as a parameter), which is the first `promptguard` →
  `pipeline` import and is cycle-free (`pipeline/__init__.py` is a docstring, `config_bounds` imports
  nothing from `promptguard`, and `pipeline/stage3_promptguard.py` imports the classifier lazily), and
  a public `promptguard_threads_from_config(config) -> int` (default `0`, range 0–16; `0` = touch
  nothing). The lifespan (`retrieval_app.py:1322-1324`,
  where `PromptGuardClassifier()` is built and `WeightAcquisition` starts) calls the builder and
  passes the value **straight into** `classifier.configure_threads(n)` — no `app.state.
  promptguard_threads` attribute and no module-level default (nothing outside the lifespan would read
  it; every entry in `retrieval_app.py:1378-1402` exists because a helper reads it). Inside `load()`
  (`promptguard/classifier.py:50-56`), a **nested** `try/except Exception` immediately after the lazy
  `import torch` at `:72` and before `AutoTokenizer.from_pretrained` at `:84`: when `n > 0`, set
  `os.environ["TOKENIZERS_PARALLELISM"] = "false"` — this **disables** the DeBERTa fast tokenizer's
  Rust pool (a boolean, not a count; the pool is moot at one chunk per call, `promptguard/classifier.
  py:185-193` — defence in depth; no `RAYON_NUM_THREADS`, recorded in Decisions Made), and it is the
  first environment variable Forage *writes*: `kit_tools/docs/ENV_REFERENCE.md` gains one sentence
  under `### Top-level keys` that Forage sets `TOKENIZERS_PARALLELISM=false` at load time when
  `promptguard_threads > 0`, overriding whatever the operator set — and call `torch.set_num_threads(n)`; on failure log
  `promptguard_threads_apply_failed` at **WARNING** (renders in-container; `GOTCHAS.md:130-143`) with
  `n` and `type(exc).__name__` in the message and fall through — so the outer blanket handler at
  `:110-115` (which sets `_loaded = False`) never sees it and never emits "PromptGuard model not
  available". Because the attribute lives on the classifier object, `model_fetcher`'s retry path
  (`WeightAcquisition`, `acquire_and_load` `:1530`, `_load_verified` `:1474-1495`, `SupportsWeightLoad`
  `:1060`) is untouched — `acquire_and_load`'s parameters are "not a configuration surface"
  (`model_fetcher.py:1558`) and stay that way.
- `classification_concurrency` 1–1 → 1–8: `pipeline/extraction_limits.py:160-166` currently passes
  `CLASSIFICATION_CONCURRENCY` (`:19`) as both default and `maximum=`; introduce
  `_MAX_CLASSIFICATION_CONCURRENCY = 8` beside `_MAX_ADMISSION_QUEUE_DEPTH` (`:27`) and pass it as the
  maximum, leaving `CLASSIFICATION_CONCURRENCY = 1` as the default. State in the module docstring
  that `classification_concurrency` intentionally stops sharing `extraction_concurrency`'s
  default-is-the-maximum idiom (`:153-159`), because `extraction_concurrency` stays memory-pinned to
  one worker. The semaphore sites (`retrieval_app.py:1225-1228`, `:1399-1402`) are unchanged apart
  from the widened range. It is a **nested** key (`extraction:` block, `config.yaml:46`): its rows are
  *rewritten*, not added — `docs/configuration.md:489` and `kit_tools/docs/ENV_REFERENCE.md:118` (the
  `### extraction:` block table at `:105`, today "`1` | 1 to 1 | Pinned, same reason") — **and the
  block's preamble at `ENV_REFERENCE.md:107`** ("Shipped values equal the maxima, so these knobs can
  only tighten") is restated **honestly, not narrowly** (R16 corrected in round 4; round-3 security
  review): the sentence is already false today — `child_address_space_bytes` is 128–512 MiB against a
  shipped 384 MiB (`pipeline/extraction_limits.py:25-26,132-137`) and `admission_queue_depth` is 0–4
  against a shipped 1 (`:27,167-173`), and the table two lines below prints both ranges — so it
  becomes "shipped values equal the maxima **except three keys that may be raised**:
  `classification_concurrency` (1–8, under the memory rule), `child_address_space_bytes` (128–512 MiB
  — a sandbox limit: raising it widens the untrusted-PDF child's `RLIMIT_AS`) and
  `admission_queue_depth` (0–4)", never "the one key"; US-003's `only tighten` / `never raise` sweep
  carries the same three-key wording to the other copies (`TROUBLESHOOTING.md:524`, `DECISIONS.md:105`,
  `SECURITY.md:182`) and leaves the single-key-scoped line (`TROUBLESHOOTING.md:161`).
- The memory rule, as R16 corrects it in rounds 3–5 — every term counted once: `FORAGE_MEM_LIMIT ≥
  PARENT_RESERVATION_BYTES + CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL[selected model] +
  classification_concurrency × CLASSIFIER_WORKING_SET + extraction_concurrency ×
  extraction.child_address_space_bytes + (cache.max_bytes if the in-memory backend is selected, else
  cache.max_value_bytes)`. **The parent term is model-dependent** (R16 corrected in round 5; round-4
  salty review): `PARENT_RESERVATION_BYTES = 512 * MEBIBYTE` is a **named constant** in
  `pipeline/extraction_limits.py` beside the coefficient — the parent process with the default (22M)
  classifier resident and no classification in flight, the reservation `docs/configuration.md:485`
  already states — and `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL: Mapping[str, int]`, keyed by model
  id, holds each allowlisted model's measured resident working set minus the 22M's (`{<22M id>: 0}` at
  this story — the only allowlisted id, so the shipped arithmetic is unchanged); the rule adds the
  selected model's row, because a bigger model's weights are *shared* across concurrent
  classifications and belong in the parent term, not in the per-classification coefficient — an RSS
  delta between concurrency 1 and 2 (spec 7's marginal measurement) can never capture them, so without
  this term an 86M selection on a 1 GiB host would be silent right up to the OOM kill. The selected id
  is `promptguard.classifier.MODEL_ID` until spec 7 US-006 makes it selectable, and the handoff is
  written on both sides: spec 7 US-004 fills the 86M row from its idle-RSS measurement with each model
  loaded (86M minus 22M) and the sizing table gains the per-model column; spec 7 US-006 routes the
  selected `FORAGE_MODEL_ID` into this rule (Open Questions). The WARNING prints `model_id=%s
  parent_bytes=%d`. The third term reads the
  **configured** `ExtractionSettings.child_address_space_bytes` (384 MiB shipped, 128–512 MiB allowed
  — `pipeline/extraction_limits.py:25-26,132-137`), never the `MAX_CHILD_ADDRESS_SPACE_BYTES` literal:
  a deployment that raised the sandbox limit to 512 MiB would be under-counted by 128 MiB per slot by
  the constant, and a rule that stays silent exactly when it should warn is worse than none (round-3
  security review); the 384 MiB is the **child's address space** (`:16,45`, `config.yaml:39-41`,
  `compose/minimal.yml:105-109`), never a classifier figure. The cache term is **conditional on the
  backend** (round-3 salty review): `_select_cache_storage` (`retrieval_app.py:265-266`) picks
  `InMemoryStorage` only when `VALKEY_URL` is unset; under Valkey (`compose/full.yml:73` sets it) the
  bound (`cache.py:219-229`) belongs to the Valkey container and the term is the one value Forage
  holds in its own memory at a time — one in-flight read, bounded by spec 4 US-001's
  `cache.max_value_bytes` (4 MiB shipped, 512 KiB–8 MiB; the read term spec 4 promised this rule —
  R16 corrected in round 5) — and the WARNING names the backend it counted **and the term it used**
  (`cache_backend=%s cache_term_bytes=%d`; the field is named for the term, not for
  `cache.max_bytes`, which under Valkey is still `33554432` in `app.state.cache_settings` and would
  mislead — round-4 salty review). The parent
  term excludes classification so the concurrency term starts at the *first* classification — round
  2's "including one in-process classification" double-counted it and made the reference row land on
  exactly 1024 MiB by accident. `CLASSIFIER_WORKING_SET` is a named coefficient: this story ships
  `PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES = 64 * MEBIBYTE` in `pipeline/extraction_limits.py`
  (`MEBIBYTE` is `:10`). Its derivation is stated where it is defined and in the docs: the reference
  envelope's residual after the parent, one child and the shipped cache is `1024 − 512 − 384 − 32 =
  96 MiB`; 64 MiB is that residual with a 32 MiB margin kept; it has **no** measurement behind it
  (the name says provisional), and spec 7 US-004 replaces it with the `docker stats` RSS delta between
  concurrency 1 and 2 on the 22M and the 86M. At the shipped defaults the rule evaluates to
  `512 + 0 + 64 + 384 + 32 = 992 MiB` against `mem_limit: 1024m` on `minimal.yml` (in-memory) and to
  `512 + 0 + 64 + 384 + 4 = 964 MiB` on `full.yml` (Valkey) — a stated, non-zero margin, not the
  boundary. The advisory check is
  a **new** `_cgroup_memory_snapshot()` call site — `retrieval_app.py:1010-1028` is the function's
  definition and its only caller today is the `/metrics` handler at `:1515`; there is no lifespan call
  to insert after — placed in the lifespan immediately after the backend selection that follows
  `app.state.cache_settings = cache_settings_from_config(config)` (`:1282`; `_select_cache_storage`
  returns the `"memory"` / `"valkey"` tag at `:1284`), because that is the first point at which every
  operand is in hand: `settings.classification_concurrency`, `settings.extraction_concurrency` and
  `settings.child_address_space_bytes` from `extraction_settings_from_config` at `:1215`,
  `PARENT_RESERVATION_BYTES` plus the selected model's delta row,
  `PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES`, and `app.state.cache_settings.max_bytes` /
  `.max_value_bytes` with the backend tag — an implementer who places it beside the semaphore build at
  `:1225-1228` has three terms and no cache bound (round-3 codebase-fit review). The shape is
  `_warn_if_break_glass_advertisement_enabled()` (`:142`, called from the lifespan at `:1234`): a
  named-marker WARNING helper called once, never a refusal, with a row in `MONITORING.md`'s startup
  table. When `cgroup_memory_max_bytes` is not `None` and it is **strictly below** the rule's sum,
  `logger.warning("envelope_memory_rule_unmet — memory_max=%d required=%d
  classification_concurrency=%d extraction_concurrency=%d child_address_space_bytes=%d model_id=%s
  parent_bytes=%d cache_backend=%s cache_term_bytes=%d", ...)` once and proceed (never a boot refusal:
  `None` on non-cgroup-v2 hosts and deliberate over-subscription stay supported; equality is silent).
  Test it by patching `_cgroup_memory_snapshot` (the `test_lifespan_*` shape in
  `tests/test_app.py:1199+` for the in-memory cases; `_started_with_valkey_url`, `:1270-1306`, for the
  Valkey case — round-4 codebase-fit review), including the shipped-defaults-against-1 GiB case, the
  exact-equality case, the Valkey case, the 128 MiB-cache case, the 512 MiB-child case and the
  monkeypatched-delta model case (Independent Test). What happens if spec 7 measures a coefficient
  above 96 MiB is decided now, not
  after the measurement: the shipped compose default `FORAGE_MEM_LIMIT=1024m` does **not** move; spec 7
  US-004 records the measured value, the sizing table's reference row states the measured minimum,
  and a 1 GiB deployment then boots with the advisory WARNING — the honest signal — rather than being
  silently under-sized (Decisions Made; Open Questions). `cache.py:219-223`'s comment that the
  ~128 MiB headroom is what the cache spends a quarter of stays true under the corrected rule (the
  headroom holds the 32 MiB cache, the 64 MiB provisional working set and the 32 MiB margin) and is
  **not** rewritten into a classifier claim — round-2 security review. `MONITORING.md:229-237`'s
  "Startup lines you may see" table (`:237` is the `PromptGuard model not available` row, the last one
  today) gains rows for `envelope_memory_rule_unmet` and
  `promptguard_threads_apply_failed`, and the "Boot failure" row (`:349`) names
  `PromptGuardThreadsConfigurationError`.
- `docs/configuration.md:489` (`classification_concurrency` row) is rewritten by **this** story:
  range 1–8, the memory rule with its coefficient and the pointer to § Sizing the container (US-003
  creates it — a forward reference US-003's criterion closes), "above the rule the failure is an OOM
  kill, which `/health` cannot report; boot warns `envelope_memory_rule_unmet` when the cgroup limit is
  readable", "it bounds the routes that acquire the classification semaphore — `/extract`, and
  `/retrieve` and `/search` after spec 2 US-006". `:488` (`extraction_concurrency`, "Pinned at 1 — the
  memory reservation above assumes exactly one worker") is **correct and must not be edited**. The new
  `promptguard_threads` row goes in the top-level table (`:430`): "0 = torch's and the tokenizer's
  defaults (every visible core); set it to whatever CPU quota the container actually runs under —
  `FORAGE_CPUS`, a Kubernetes limit, a host-level cgroup, an orchestrator's cap — because torch reads
  the host's core count, not the quota". The shipped `config.yaml` gains `promptguard_threads: 0`
  with that comment (the `search_promptguard_*` keys of US-004 are shipped too — the three new keys
  are shipped, the one widened key is rewritten in place).
- `KNOWN_CONFIG_KEYS` (spec 3, ruling 12) gains `promptguard_threads`, **and `promptguard/classifier.py`
  joins spec 3 US-003's AST code-parity reader list** — the hand-enumerated walk over `retrieval_app.py`,
  `cache.py`, `pipeline/extraction_limits.py`, `brave.py`, `pipeline/orchestrator.py`,
  `pipeline/sanitizer_revision.py`, spec 2's retrieve reader, `pipeline/config_bounds.py` and, after
  spec 5 US-001, `searxng.py` — because registering the key and registering its reader are two halves of
  the same registry, and the sweep's shape-(b) rule is written for exactly the `bounded_int(config,
  "<key>", …)` call this reader makes (round-4 codebase-fit review); `kit_tools/docs/
  ENV_REFERENCE.md` `### Top-level keys` (`:81-94`) gains its row with the `Read site` and `Shipped`
  columns filled (the `search_brave_*` rows `:90-92` are the shape).
- Tests: the builder's bounds and the load-path assertions in `tests/test_stage3_promptguard.py`
  (`TestClassifierUnit` is the classifier's existing home; the patching seam is
  `tests/test_model_fetcher.py:779-791`); the `classification_concurrency` 1–8 / boot-refusal tests
  and the memory-rule WARNING test beside the existing `extraction_settings_from_config` bound tests
  in `tests/test_app.py` (`test_lifespan_refuses_an_out_of_range_cache_bound` `:1208` is the shape).
- Performance knobs are **not** `derive_sanitizer_revision` inputs (unlike `promptguard_threshold`,
  which is): the hash covers the sanitizer's rules, not its throughput. They do change latency, and
  under spec 2 US-006's bounded wait latency can decide whether a fail-open request is scanned — see
  Security Considerations; the rows this story writes say so in one sentence. No hashed file moves in
  this story.

**Acceptance Criteria:**
- [x] `promptguard_threads` (default 0, range 0–16) is read at boot through
      `promptguard_threads_from_config` via `pipeline/config_bounds.bounded_int` (`grep -n '_bounded_'
      promptguard/classifier.py` returns nothing), refuses boot out of range or wrong-typed with
      `PromptGuardThreadsConfigurationError`, ships in `config.yaml` as `promptguard_threads: 0`, and a positive
      value reaches `torch.set_num_threads` and sets `TOKENIZERS_PARALLELISM=false` before
      `AutoTokenizer.from_pretrained` via `configure_threads` on the lifespan's classifier object
      (patched torch and transformers, no weights); `0` calls nothing and leaves
      `TOKENIZERS_PARALLELISM` unchanged from the sentinel the test seeded with `monkeypatch.setenv`;
      `_CLEARED_ENV_VARS` is unchanged; there is no `app.state.promptguard_threads`;
      `ENV_REFERENCE.md` states that Forage writes the variable when `promptguard_threads > 0`.
- [x] A `set_num_threads` failure leaves `load()` returning `True` and logs
      `promptguard_threads_apply_failed` at WARNING with the count and the exception type in the
      message; the blanket handler's "PromptGuard model not available" line is not emitted for it;
      pinned.
- [x] `classification_concurrency` accepts 1–8 via `_MAX_CLASSIFICATION_CONCURRENCY`; `8` builds
      `asyncio.Semaphore(8)`; `9` refuses boot with `ExtractionConfigurationError`; the default stays
      1; `extraction_concurrency` stays 1–1.
- [x] Two concurrent `/retrieve` classifications overlap at concurrency 2 and serialise at 1 (fake
      classifier with timestamps; the `/retrieve` semaphore from spec 2 US-006).
- [x] `PROVISIONAL_CLASSIFIER_WORKING_SET_BYTES` (64 MiB) exists, is named provisional in its comment
      and in the docs with its derivation and its no-measurement caveat; `PARENT_RESERVATION_BYTES`
      (512 MiB) is a named constant and `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` holds the 22M row
      at `0`; the rule has the five terms (the parent constant plus the selected model's delta row,
      concurrency × working set, extraction slots × the **configured** `child_address_space_bytes`,
      and `cache.max_bytes` when the backend tag is `memory` or `cache.max_value_bytes` when it is
      `valkey`); the check is a new `_cgroup_memory_snapshot()` call in the lifespan after the backend
      selection that follows `cache_settings_from_config` (`retrieval_app.py:1282-1284`); boot logs
      exactly one `envelope_memory_rule_unmet` WARNING — naming `memory_max`, `required`, the operands,
      `model_id`, `parent_bytes`, `cache_backend` and `cache_term_bytes` — when the cgroup limit is
      readable and strictly below the rule, none when it is `None`, none at the shipped defaults
      against exactly `1073741824` (required 992 MiB — the margin is asserted), none at exact equality;
      the Valkey case (required 1156 MiB at concurrency 4, `cache_term_bytes=4194304`, reached through
      `_started_with_valkey_url`), the 128 MiB-cache case (1088 MiB), the 512 MiB-child case (1120 MiB)
      and the monkeypatched-delta model case (1440 MiB, `parent_bytes` reflecting the row) each warn
      against 1 GiB; `/health` is 200 either way; pinned with a patched `_cgroup_memory_snapshot`;
      `MONITORING.md`'s startup table has both new WARNING rows.
- [x] The `classification_concurrency` rows in `docs/configuration.md`'s `extraction:` table and
      `ENV_REFERENCE.md`'s `extraction:` table (the rows reading "Pinned … same reason" today —
      `:489` / `:118` at planning time) state the 1–8 range, the memory rule with its named coefficient,
      the OOM consequence, the boot WARNING, the routes it bounds and the one-sentence
      `promptguard_wait_seconds` consequence; the `extraction:` preamble in `ENV_REFERENCE.md` (`:107`
      at planning time) names all three raisable keys with their ranges and never claims a single
      carve-out; the `extraction_concurrency` row (the one reading "Pinned at 1") is unchanged; the
      `promptguard_threads` rows exist in both files' top-level tables with the quota-agnostic wording;
      `promptguard_threads` is in `KNOWN_CONFIG_KEYS`.
- [x] `promptguard/classifier.py` is in spec 3 US-003's AST code-parity reader list.
- [x] `git diff --stat` shows no change under `pipeline/orchestrator.py`, `pipeline/contract.py`,
      `models.py` or `contract/`.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-004: Search latency targets as config — the overrun counter and the sanitization high-water mark

**Priority:** P1

**Description:** As an operator, I want the search latency targets to be `config.yaml` keys with
today's defaults, and an overrun to be countable on `/metrics` with its magnitude, so I can tell
"nudge the target" from "add cores" without a log line whose payload never renders.

**Independent Test:** With `search_promptguard_latency_target_ms: 100` (the bottom of the documented
range) read through `search_targets_from_config` and a fake classifier that sleeps 150 ms per call, one
`/search` returns 200 and `/metrics` shows `search.promptguard_latency_target_exceeded == 1` and
`search.sanitization_latency_max_ms >= 150`; a second `/search` on the same boot with a fake that
sleeps 0 ms leaves the max at its first value (a relative assertion: the second measured duration is
below the first — the 150 ms sleep dominates) and asserts nothing about the counter; the **no-overrun
case runs from a separate boot** at `search_promptguard_latency_target_ms: 5000` with the 0 ms fake —
one `/search` leaves the counter at 0 and the max at the measured value — never from the 100 ms boot,
where a counter assertion would be a wall-clock claim at the range's floor (round-3 salty review);
`search_promptguard_latency_target_ms: 5` refuses boot with `SearchTargetsConfigurationError` in a
separate boot test; a `/search` whose provider serves **zero raw results** on a `[searxng]`-only chain
(the served-empty shape, spec 5 US-005's `_ServedChain`) reaches the measurement site, never counts,
and may raise the max from 0 — while a `/search` that fails before the loop (`search_unavailable`
422) touches neither; with no overrides the counter stays 0 on a fast fake, the max stays 0 until the
first `/search` and then reflects it, and spec 5 US-005's wire pins are byte-identical.

**Implementation Hints:**
- Settings builder, module-owned (the precedent trio again; `tests/test_brave_provider.py` is the
  module-owned-settings test precedent): new non-hashed module `pipeline/search_targets.py` with a
  frozen `SearchTargets(promptguard_latency_target_ms: int = 1_000, first_token_target_ms: int =
  5_000)`, `SearchTargetsConfigurationError`, **no** module-local bounded-int copy — both ranges are
  read through `pipeline/config_bounds.bounded_int` with `SearchTargetsConfigurationError` as the
  exception class, the call shape spec 2's `pipeline/retrieve_limits.py` uses (R16 corrected in round
  5; round-4 codebase-fit critical) — and `search_targets_from_config(config)` reading
  `search_promptguard_latency_target_ms` (range 100–60000) and `search_first_token_target_ms` (range
  100–120000); both ship in `config.yaml` at today's values. `pipeline/search_targets.py` joins spec 3
  US-003's AST code-parity reader list beside its two `KNOWN_CONFIG_KEYS` entries; its tests — the two
  bounds, the `5` boot refusal and the lifespan wiring — land in `tests/test_app.py` beside
  `test_lifespan_refuses_an_out_of_range_cache_bound` (`:1208`), and `kit_tools/testing/
  TESTING_GUIDE.md`'s `test_mapping` gains the row `"pipeline/search_targets.py": "tests/test_app.py"`
  (every source module has a row; spec 2's `pipeline/retrieve_limits.py` → `tests/test_app.py` is the
  precedent — round-4 codebase-fit review). The lifespan calls it unconditionally, stores
  `app.state.search_targets`, and the
  module-level default block (`retrieval_app.py:1378-1402`) gets `SearchTargets()` with the block's
  usual comment (the `/search` handler reads it, so a lifespan-less transport needs it).
  `search_first_token_target_ms` is **log-only today**: it feeds the `extra=` dict at
  `orchestrator.py:1076-1078,1089` and nothing else; the `docs/configuration.md` row says so in one
  sentence ("tunes the `search_promptguard_complete` log line; no counter compares it").
- Replace the module constants at `pipeline/orchestrator.py:586-587` with two `run_search_pipeline`
  keyword parameters defaulting to today's values (`promptguard_latency_target_ms: int = 1_000`,
  `first_token_target_ms: int = 5_000`), fed by the `/search` handler (`retrieval_app.py:1811`) from
  `app.state.search_targets`; the INFO/WARNING `extra=` dicts at `:1068-1092` read the parameters.
  Keep the log lines. **What the timer measures** (R16, round 3): `promptguard_started` (`:960`)
  wraps the whole per-result loop (`:965-1060`) — `scan_structural` at `:997`, the PromptGuard call,
  and, once spec 2 US-006 wires it, the classification-semaphore wait — so the high-water mark is
  named for the loop, `search.sanitization_latency_max_ms`, while the counter keeps the name the
  target key and the existing log line already use, `search.promptguard_latency_target_exceeded`;
  both descriptions state the window in one sentence ("the per-result sanitization loop: structural
  scan, PromptGuard and any semaphore wait"). **Two placements**, not one: the high-water mark
  `metrics.sanitization_latency_max_ms = max(metrics.sanitization_latency_max_ms,
  int(promptguard_duration_ms))` goes immediately after `promptguard_duration_ms` is computed
  (`:1063-1066`), before the INFO line, unconditionally (`int()` truncates the rounded float); the
  counter `metrics.promptguard_latency_target_exceeded += 1` goes inside the existing `if
  promptguard_duration_ms > target:` at `:1082`. A `/search` that raises before `:960` (provider
  failure → 422) reaches neither site — pinned. The local is named `metrics` (`:863`, rebound from
  `search_metrics`). Six sites per field, plus the two test-side seams: `SearchMetricsSink` Protocol
  (`pipeline/orchestrator.py:747-756`, whose docstring **drops its field count** — it is stale today
  and spec 2 US-006 and spec 5 US-003 each move it; round-4 salty review), `_NullSearchMetrics.__init__` (`:759-771`,
  both attributes), `SearchMetrics.__init__` (`retrieval_app.py:875-886`, a plain class), the
  `/metrics` handler dict (`:1517-1524`, appended after the last key), `SearchMetricsResponse`
  (`:488-533`, appended last; descriptions name the config key and the window, both state that the
  loop runs once per served result so the figure **scales with `num_results` (1–20)** and readings
  compare only at the same `num_results` (round-3 salty review), and the max's description states it
  is **per-process, never resets, and is only meaningful read together with the exceeded count and
  `search.requests`** — restarting the container is the only way to clear it), and the literal field
  set in `tests/test_contract_schema.py:368`
  (`test_search_metrics_response_1_2_0_field_set_is_pinned_exactly`), which spec 5 US-003 already
  extends and which gains these two names in the same commit or stays red — plus
  `tests/fakes.py::RecordingSearchMetrics` (spec 5 US-001, the one test-side double every
  `SearchMetricsSink` member lands in) and `tests/test_app.py:482-490`'s exhaustive `body["search"]`
  literal, both of which gain both keys in the same commit. Missing the Protocol or
  the null sink fails pyright strict; a model field without its class counterpart 500s `/metrics`
  (`extra="forbid"`, GOTCHAS).
- Follow and update the repo's own runbook: `kit_tools/docs/MONITORING.md:433-439` ("Adding a
  counter to `/metrics`") — spec 5 US-003 already teaches it the Protocol / `_NullSearchMetrics` /
  handler-dict sites and the plain-class correction; this story verifies that edit landed (or makes
  it) and follows it — and adds one sentence that a field may be a **high-water mark** rather than a
  counter (updated with `max(...)` at the seam, per-process, never reset, read with its companion
  count), under the heading widened to "Adding a counter or gauge to `/metrics`", because this story
  adds the repo's first `/metrics` field that is neither a monotonic counter nor a dict (round-4
  codebase-fit review).
- Window mechanics (R36, verbatim): append the docstring lines (`* ``1.3.0`` — …` format), run
  `uv run python -m scripts.export_contract`, re-create `tests/golden/contract_1_3_0.json` via
  `_SCHEMA_MODELS`, append the fields to `_EXPECTED_ONE_THREE_ZERO_DIFF` in
  `tests/test_contract_schema.py`, refresh the four anchor-quoting pages, and run `--check`. For
  `/metrics` fields the golden and the diff list do not move (`/metrics` models are outside
  `_SCHEMA_MODELS`, `tests/test_contract_schema.py:28-35,81`; they are pinned by
  `tests/test_contract_metrics.py` — the order guards at `:135` and `:161`, the schema-fullness test
  at `:272-292` which requires the descriptions): the implementer runs every step, confirms the golden
  is byte-identical and the diff list needs no entry, and records both in Implementation Notes. **This
  story appends nothing to `_EXPECTED_ONE_THREE_ZERO_DIFF`** (R36 corrected): `_added_paths` (`:130`)
  reports new `properties` keys and `enum` members of `_SCHEMA_MODELS` only, and `/metrics` models are
  outside that set; the gate is `test_contract_schema_matches_golden` on the unchanged golden.
- `pipeline/orchestrator.py` and `pipeline/contract.py` are both hashed: one rotation measured with
  each reverted in turn and both reverted as the control (the search epic's ninth rotation is the
  precedent); record at the five sites (ruling 6).
- Docs (R39): `docs/configuration.md` top-level rows for both keys; `kit_tools/docs/MONITORING.md`
  gains rows for the counter and the max beside `search.fallback_fired` (`:148`), the max's row
  stating the per-process/never-resets semantics and the window, and the runbook sentence keyed on
  the **count over `search.requests` first and the max second**: "a rising exceeded-over-requests
  ratio with a max under 2× the target: raise the target; above it: raise `FORAGE_CPUS` /
  `promptguard_threads` (see § Sizing the container — a forward reference US-003 closes)" — and a
  second sentence keyed on **spec 2's counter, not on the max** (R16 corrected in round 4): "under
  spec 2 US-006 the signal that fail-open requests are being served unscanned is
  `search.classification_wait_timeouts` rising; the max is whole-loop wall time — structural scan,
  PromptGuard and semaphore wait, summed over every served result — so it is not comparable to the
  per-wait `promptguard_wait_seconds` and is never read against it" (the round-3 sentence "a max
  approaching `promptguard_wait_seconds × 1000`" was wrong in kind: one per-result wait against a
  whole-loop sum); both `MONITORING.md` rows carry the `num_results` comparability clause;
  `MONITORING.md:286`'s WARNING-line row ("exceeded the 1000 ms local target") is restated as
  "exceeded `search_promptguard_latency_target_ms` (default 1000)" — `grep -rn '1000 ms'
  kit_tools/docs/ docs/` then returns nothing, `grep -rn 'promptguard_wait_seconds × 1000\|
  promptguard_wait_seconds x 1000' kit_tools/docs/ kit_tools/arch/ docs/` returns nothing, and `grep
  -rn 'promptguard_latency_max_ms' retrieval_app.py pipeline/ tests/ contract/ docs/ kit_tools/docs/
  kit_tools/arch/` returns nothing because the field never existed under that name (R43: exact path
  sets; `kit_tools/specs/` and the result artefacts excluded); `ENV_REFERENCE.md` `### Top-level keys`
  rows; `KNOWN_CONFIG_KEYS` entries; the "Boot failure" row (`MONITORING.md:349`) names
  `SearchTargetsConfigurationError`.

**Acceptance Criteria:**
- [x] `search_promptguard_latency_target_ms` (default 1000, range 100–60000) and
      `search_first_token_target_ms` (default 5000, range 100–120000) ship in `config.yaml`, are
      bounded at boot by `search_targets_from_config` (`SearchTargetsConfigurationError` out of range —
      `5` refuses boot, pinned), reach `run_search_pipeline` from the `/search` handler via
      `app.state.search_targets`, and `grep -c '_LOCAL_PROMPTGUARD_TARGET_MS\|
      _TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS' pipeline/orchestrator.py` returns 0.
- [x] `/metrics` serves `search.promptguard_latency_target_exceeded` (incremented once per `/search`
      whose loop exceeds the target, strictly greater, never otherwise) and
      `search.sanitization_latency_max_ms` (updated on every `/search` that reaches the site after
      the duration is computed — a served-empty `/search` included, a pre-loop 422 excluded, both
      pinned); all six sites per field and the two test-side seams (`RecordingSearchMetrics`, the
      `tests/test_app.py:482-490` literal) are edited, the literal pin in `tests/test_contract_schema.py`
      included; `_NullSearchMetrics` carries both; the two order-guard tests pass with the keys
      appended; both descriptions name the window and the `num_results` scaling; the max's description
      states per-process, never-resets, read-with-the-count; `grep -rn 'promptguard_latency_max_ms'
      retrieval_app.py pipeline/ tests/ contract/ docs/ kit_tools/docs/ kit_tools/arch/` returns
      nothing; the no-overrun counter case runs from its own 5000 ms boot.
- [x] Window mechanics (R36): docstring lines appended in the `* ``1.3.0`` — …` format; `uv run
      python -m scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via
      `_SCHEMA_MODELS`; `_EXPECTED_ONE_THREE_ZERO_DIFF` reviewed and **nothing appended** (`/metrics`
      models are outside `_SCHEMA_MODELS` — R36 corrected); the four anchor-quoting pages refreshed;
      `uv run python -m scripts.export_contract --check` green — with Implementation Notes recording
      that the golden is byte-identical and the diff list carries no `/metrics` entry, and why.
- [x] With no overrides, spec 5 US-005's wire pins (`tests/test_search_pipeline_pins.py`) pass
      unchanged — correct as written because the pins compare a closed `_PINNED_COUNTERS` projection
      that excludes these two fields (spec 5 US-005, R13 corrected in round 5); the test this story
      moves is `tests/test_contract_schema.py::test_search_metrics_response_1_2_0_field_set_is_pinned_exactly`,
      never the pins.
- [x] `pipeline/search_targets.py` is in spec 3 US-003's AST code-parity reader list; `grep -n
      '_bounded_' pipeline/search_targets.py` returns nothing; `kit_tools/testing/TESTING_GUIDE.md`'s
      `test_mapping` carries a row for `pipeline/search_targets.py` naming `tests/test_app.py`.
- [x] `docs/configuration.md` rows (the first-token row saying "log-only today"), `MONITORING.md` rows
      with the per-process semantics and the `num_results` comparability clause, the count-first
      runbook sentence and the `search.classification_wait_timeouts` sentence (the max is never read
      against `promptguard_wait_seconds`), `ENV_REFERENCE.md` rows and `KNOWN_CONFIG_KEYS` entries exist
      for both keys; `MONITORING.md:433-439`'s runbook names the Protocol, `_NullSearchMetrics`,
      handler-dict and literal-pin sites and covers a high-water mark under its widened heading;
      `grep -rn '1000 ms' kit_tools/docs/ docs/` returns nothing and
      `grep -rn 'promptguard_wait_seconds × 1000\|promptguard_wait_seconds x 1000' kit_tools/docs/
      kit_tools/arch/ docs/` returns nothing.
- [x] `sanitizer_revision` rotation measured (revert `pipeline/orchestrator.py` and
      `pipeline/contract.py` each in turn, with a both-reverted control) and recorded at the five sites
      ruling 6 names — `docs/bootstrap-notes.md`, `CLAUDE.md`, `kit_tools/arch/DECISIONS.md`,
      `kit_tools/docs/GOTCHAS.md` (a new row in the divergence table) and `kit_tools/arch/CODE_ARCH.md`.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-002: Compose envelope — `FORAGE_CPUS`, `FORAGE_MEM_LIMIT` and a liveness healthcheck

**Priority:** P1

**Description:** As an operator running the published fragments, I want to size the container from
my `.env` instead of editing the fragment, and I want Docker to know whether the process is alive,
so `docker compose up` on a 4-core host can be told to use four cores without a fork of the file —
and so pulling the updated fragment changes nothing until I set a variable.

**Independent Test:** Hermetic half: `tests/test_compose_fragments.py` asserts the literal
`${FORAGE_CPUS:-0}` / `${FORAGE_MEM_LIMIT:-1024m}` substitution strings, the healthcheck block with
`-o /dev/null`, and the envelope comment prose (lowercase — `_comment_prose` `:167-179` lower-cases
and keeps whole-line comments only) in **both** fragments, and `TestTheDuplicationDoesNotDrift`
(`:705-743`) asserts the two fragments' envelope values are identical; the required `lint` job
gains a **second** fragment-render step after the existing one (`.github/workflows/ci.yml:201-239`,
pinned by `tests/test_ci_workflow.py::TestComposeFragmentValidation` `:752-800`) whose own `env:`
carries `FORAGE_CPUS: "2"`, `FORAGE_MEM_LIMIT: 2048m` and the same `SEARXNG_SECRET` placeholder, so
both envelope branches parse on every commit, and `tests/test_ci_workflow.py` asserts the second
step's two names and values. Manual half (R33 as corrected): from a scratch
project directory holding a copy of each fragment, `docker compose --env-file /dev/null
--project-directory "$scratch" -f "$scratch/minimal.yml" config` with a **complete** placeholder set
in the environment (`HF_TOKEN=placeholder SEARXNG_SECRET=placeholder FORAGE_SEARCH_PROVIDERS=searxng
FORAGE_BRAVE_API_KEY=placeholder FORAGE_CACHE_HMAC_KEY=placeholderplaceholderplaceholder32
VALKEY_URL=redis://placeholder`), piped through `grep -E '^[[:space:]]*(cpus|mem_limit|healthcheck|test|
interval|timeout|retries|start_period):'`, shows **no effective CPU limit** — no `cpus` line at all
(v2.40.3's rendering) or `cpus: 0` on a Compose minor that prints it — and a byte-normalised
`mem_limit` of `1073741824` with no variable set, and `cpus: 4` with `mem_limit` `4294967296` with
`FORAGE_CPUS=4 FORAGE_MEM_LIMIT=4096m`, for both fragments (the behaviour is the condition; the exact
filtered lines are the recorded evidence — round-3 salty review); the semantic half — `0` means no limit — is confirmed against a started
container **that needs no secret**: a throwaway `probe.yml` in the same scratch directory with one
`busybox` service (`command: sleep 60`, `cpus: ${FORAGE_CPUS:-0}`, nothing else), brought up with
`--env-file /dev/null`, and `docker inspect -f '{{.HostConfig.NanoCpus}}' <container>` is `0` with no
variable set and `4000000000` with `FORAGE_CPUS=4` — Docker applies the key identically to any image,
so the Forage image, its weights and its `HF_TOKEN` are not needed for this fact (the owner may run
the same inspect on a real Forage container instead; not required). Implementation Notes record only
the grep-filtered lines, the two inspect values and `docker compose version` — the raw render is
never written to a file inside the repo, and no `environment:` block or env value appears anywhere.
**Docker requirement, stated honestly** (R16 corrected in round 4): `docker compose config` is a
client-side render in Compose v2 and needs no daemon, so the render half runs wherever the Compose
plugin is installed; the `busybox` probe (`up` + `inspect`) needs a daemon. That is a verification
note, not an owner gate: when no daemon is available, the two CI render steps stand as the machine
proof that both envelope branches parse and interpolate, the Compose Spec's service-level `cpus`
(`0` = no quota, Docker's `HostConfig.NanoCpus` 0) carries the "`0` means no limit" claim, and
Implementation Notes record "`NanoCpus` probe not run — no Docker daemon" with the Compose version in
place of the two integers.

**Implementation Hints:**
- Both fragments' `forage` service: add `cpus: ${FORAGE_CPUS:-0}` beside `mem_limit`, change
  `mem_limit` to `${FORAGE_MEM_LIMIT:-1024m}` (`compose/minimal.yml:105-110`, `compose/full.yml:80`).
  Compose v2.40.3 renders `${FORAGE_CPUS:-0}` by **omitting the `cpus` key** (no limit) and
  normalises `mem_limit` to bytes — the round-1 measurement closed the old open question, so there is
  **no override-file fallback** (the `compose/envelope.yml` branch is deleted; it would have been a
  third fragment, which the Research Findings already reject as drift). Write an envelope comment in
  **both** files — `minimal.yml:105-109` is rewritten, `full.yml:80` has none today and gets new
  prose — naming both variables, "unset or 0 = no CPU limit (today's behaviour; Compose omits the
  key)", that `FORAGE_MEM_LIMIT` takes Docker's byte-unit syntax (`FORAGE_MEM_LIMIT=abc` fails at
  Compose, before Forage starts), "below 1 vCPU is unsupported", "service-level `cpus` needs Docker
  Compose v2 (Compose Spec); verified on v2.40.3", and the pointer to § Sizing the container. Comments
  only elsewhere; every existing key and value stays (the 2026-09-19 housekeeping batch edited
  comments in both files — preserve its wording). **No `config.yaml` volume line** is added to the
  fragments: a bind mount whose host path is missing mounts a *directory* and the container will not
  boot, so the shipped fragments stay mount-free and the sizing section (US-003) carries the real,
  copy-pasteable override snippet instead.
- Healthcheck (both fragments): the `Dockerfile` is single-stage (`FROM` at `:44`) and installs
  `curl` at `:67` for exactly this ("curl for the container healthcheck", `:48`); no `HEALTHCHECK`
  instruction exists and neither fragment declares one. Use
  `test: ["CMD", "curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1:8020/health"]` — `-o /dev/null`
  so Docker's `State.Health.Log[].Output` captures no `/health` body — with `interval: 30s`, `timeout:
  5s`, `retries: 3`, `start_period: 30s`. `/health` answers 200 as soon as uvicorn listens, even while
  weights download and the body says `degraded` (invariant 5). The probe's cost and its worst case
  are stated in the comment: one `/health` every 30 s, which calls `cache.ping_if_due()`
  (`retrieval_app.py:1455`) and may spend up to `_RECONNECT_TIMEOUT_S` (2 s, `cache.py:44`) on a
  reconnect attempt when the cache is down — inside the 5 s probe timeout, which is why the timeout
  is 5 s and not 2 s; the interval stays 30 s (a tighter probe buys nothing, since nothing acts on
  the health column). The fragment comment states three
  things: the healthcheck is liveness, `/health`'s body is health; plain `docker compose` reports an
  unhealthy container but never restarts it (`restart:` reacts to exits); and a `healthy` container
  may be running with the classifier unloaded — never gate traffic, `depends_on: service_healthy` or
  a consumer's activation on it. Note the side effect for US-003's docs — **both halves** (round-3 salty review): `/health` calls
  `cache.ping_if_due()`, so the probe detects cache recovery within one interval even with no traffic;
  and on a dead Valkey each probe that lands after the reconnect backoff has elapsed (capped at 30 s,
  `_RECONNECT_MAX_BACKOFF_S`, `cache.py:42` — the same 30 s as the interval) runs `_attempt_connect()`,
  which logs `Valkey connection failed for content cache (connect_failed|timeout)` at WARNING
  (`cache.py:419-422`) and bumps `reconnect_attempts` / `reconnect_failures`, so a zero-traffic
  `full.yml` container with Valkey down emits that WARNING at **at most one reconnect attempt per
  probe**, at a rate that depends on how the attempt fails (round-4 salty review; R16 corrected in
  round 5): `_next_retry_at` is set *after* the attempt returns (`cache.py:432-466`), so a **refused**
  connection — the attempt returns in milliseconds — gives one WARNING per probe, while a
  **timing-out** one — the 2 s deadline pushes the next retry past the next probe — gives roughly one
  per two probes; both counters climb at that probe-driven rate with no traffic. The fragment comment
  says so in one clause ("with the cache down, expect at most one reconnect WARNING per probe — one
  per probe on connection refused, about one per two on a connect timeout"), and US-003's
  `MONITORING.md:174` / `:342` edits make the counters' non-zero baseline honest. The `python3 -c
  urllib` form is the fallback only if `curl` leaves the image.
- The healthcheck falsifies two docstrings **rendered into the frozen contract document**:
  `retrieval_app.py:355-361` (the `HealthResponse` class docstring) and `:1446-1451` (the `/health`
  route) both say "the compose healthcheck is a bare ``curl -f``", rendered at
  `contract/openapi.yaml:423` and `:1417`. The healthcheck and the correction are **inseparable** —
  the moment the fragments ship a probe, both sentences are false — which is why this story carries
  them and their rotation rather than a split (R37). This story corrects both to name the shipped
  probe (`curl -fsS -o /dev/null`, status only, liveness not health), and therefore **moves the
  document** — a description-only change (GOVERNANCE standing example 3), no bump, inside the
  unpublished 1.3.0 window. **The golden moves too**: `HealthResponse` is in `_SCHEMA_MODELS`
  (`tests/test_contract_schema.py:28-35`) and pydantic renders the class docstring as the schema's
  `description`, so re-creating `tests/golden/contract_1_3_0.json` is a real regeneration here, not a
  byte-identical confirmation; `_added_paths` (`:180-186`'s helper) records added keys, not changed
  values, so `_EXPECTED_ONE_THREE_ZERO_DIFF` gains no entry — both facts recorded in Implementation
  Notes. Window mechanics (R36, verbatim): append the docstring line (`* ``1.3.0`` — …` format;
  "healthcheck descriptions corrected — no shape change"), run `uv run python -m
  scripts.export_contract` (it rewrites `contract/openapi.yaml`, the `.sha256` anchor and
  `tests/fixtures/contract/unregenerated_openapi.yaml`), re-create `tests/golden/contract_1_3_0.json`
  via `_SCHEMA_MODELS`, append to `_EXPECTED_ONE_THREE_ZERO_DIFF` in `tests/test_contract_schema.py`
  (reviewed; no entry — recorded), refresh the four anchor-quoting pages (the set is
  `tests/test_governance_docs.py:60-64`: `API_GUIDE.md`, `CI_CD.md`, `DEPLOYMENT.md`,
  `SERVICE_MAP.md`; `docs/releases.md:23`'s anchor is the v1.1.0 release record and is historical —
  never refreshed), and run `--check`. `pipeline/contract.py` is hashed: this is the story's rotation,
  measured and recorded at the five sites (ruling 6). `contract_smoke.py:9` carries the same sentence
  (not rendered) and is corrected here too, as are the stale "10 s x 5 retries, no `start_period`"
  comments at `retrieval_app.py:1307` (the lifespan's weight-acquisition comment) and
  `model_fetcher.py:62` (the module docstring) and the docstring at `tests/test_app.py:936` — code
  files, so they land here; all four are the same correction (the shipped probe is this repo's `curl
  -fsS -o /dev/null`, 30 s interval, 30 s `start_period`), and neither `retrieval_app.py` nor
  `model_fetcher.py` is a `_REVISION_SOURCES` member, so the two comment edits rotate nothing (round-3
  codebase-fit review); the doc copies — `MONITORING.md:415`, `SERVICE_MAP.md:74`,
  `TROUBLESHOOTING.md:699-707`, `LOCAL_DEV.md:234-235` — are US-003's sweep. **The retained goldens
  keep the old sentence by design**: `tests/golden/contract_1_0_0.json:201`, `contract_1_1_0.json:201`
  and `contract_1_2_0.json:201` embed today's `HealthResponse` docstring as the schema `description`
  and are frozen (CLAUDE.md invariant 4 — retained, never edited); the only golden that moves is
  `contract_1_3_0.json`, which this story re-creates, and the acceptance grep excludes `tests/golden/`
  for exactly that reason. `tests/fixtures/contract/unregenerated_openapi.yaml:422,1416` are rewritten
  by `scripts.export_contract` (`:101-102,269`), never by hand. `kit_tools/docs/API_GUIDE.md:93` ("a
  bare `curl -f` proves only that the process is up") is a true, generic sentence the acceptance grep
  nevertheless hits; **this** story rewrites it to "the compose healthcheck's `curl -fsS -o /dev/null`
  proves only that the process is up" — a prescribed replacement, not a string-match casualty (round-4
  salty and codebase-fit reviews).
- CI: a **second** step in the `lint` job, placed immediately after the existing render step
  (`.github/workflows/ci.yml:201-239`) and named for the envelope ("Validate the compose fragments
  with the resource envelope set"), running the same two `config -q` commands with its own `env:` —
  `SEARXNG_SECRET: compose-config-validation-placeholder`, `FORAGE_CPUS: "2"`, `FORAGE_MEM_LIMIT:
  2048m` — so the non-default branch parses on every commit. Not the existing step's `env:`:
  `tests/test_ci_workflow.py:836-848` (`test_the_placeholder_is_not_a_plausible_real_secret`) requires
  every value in that step's `env:` to contain "placeholder", and `2` / `2048m` do not.
  `_compose_validate_step` (`:743-749`) returns the *first* `docker compose` step, so the existing
  assertions keep binding to the existing step by construction; add `_compose_envelope_step` (found
  by step name) and a test asserting its two names and values, and extend the placeholder test to
  iterate both steps: every value must still contain "placeholder" **except** the two envelope names,
  which are checked against a **size shape** rather than exempted — `FORAGE_CPUS` matches
  `^\d+(\.\d+)?$` and `FORAGE_MEM_LIMIT` matches `^\d+[kKmMgG]?[bB]?$` — so every value in every
  compose step is still checked against something and the "no secret-shaped literal in a workflow
  file" property survives intact (round-3 security review; neither variable is `:?`-required, so the
  required-variable assertion is unaffected either way). The two steps are the standing proof that both branches parse and
  interpolate; the manual render only has to establish what `0` *means*.
- Tests: `tests/test_compose_fragments.py` — cross-fragment parity goes into
  `TestTheDuplicationDoesNotDrift` as `test_the_shared_services_declare_the_same_envelope`: the
  `len(set(...)) == 1` idiom at `:716` compares strings, so the healthcheck mapping needs a hashable
  projection (`json.dumps(healthcheck, sort_keys=True)`) beside the two substitution strings;
  value-level claims go into a new `TestResourceEnvelope` using `fragments` (`:120`, the parsed
  YAML — an unquoted `${FORAGE_CPUS:-0}` scalar parses as the string `"${FORAGE_CPUS:-0}"`, so the
  assertion is a string equality on `_services(fragments[name])["forage"]["cpus"]`, `:129`) and
  `raw_fragments` (`:125`, the file text — for the comment prose only, through `_comment_prose`): the
  two substitution strings, the healthcheck target and `-o /dev/null`, and `"forage_cpus" in prose` /
  `"forage_mem_limit" in prose` / `"no cpu limit" in prose` / `"liveness" in prose` (lowercase). There
  is no `mem_limit` assertion today; these are the first.
- Minimum Compose: service-level `cpus` is a Compose Spec key (Compose v2); the comment and the
  sizing section (US-003) say so; the verified version (v2.40.3) is recorded in Implementation Notes.

**Acceptance Criteria:**
- [x] Both fragments declare `cpus: ${FORAGE_CPUS:-0}` and `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` on
      the `forage` service and a `healthcheck` block with `curl -fsS -o /dev/null
      http://127.0.0.1:8020/health`, `interval: 30s`, `timeout: 5s`, `retries: 3`, `start_period: 30s`;
      no `config.yaml` volume line is added; no other key or value in either fragment changes
      (`git diff` shows only these keys and comment lines); `grep -rn envelope.yml compose/ docs/
      README.md kit_tools/docs/ kit_tools/arch/` returns nothing (R43: this spec's own text in
      `kit_tools/specs/` names the deleted branch, so the spec directory is excluded).
- [x] The secret-free render (`--env-file /dev/null`, scratch project directory, complete placeholder
      set, grep-filtered) shows no effective CPU limit (no `cpus` key, or `cpus: 0`) and a
      byte-normalised `mem_limit` of `1073741824` at the defaults and `cpus: 4` / `4294967296` with the
      variables set, for both fragments — no daemon needed; a started `busybox` container's
      `HostConfig.NanoCpus` is `0` at the default and `4000000000` with `FORAGE_CPUS=4` when a daemon
      is available, and otherwise Implementation Notes say "`NanoCpus` probe not run — no Docker
      daemon" with the CI render steps and the Compose Spec cited as the standing proof; Implementation
      Notes record only the filtered lines, the two inspect values (or the not-run line) and `docker
      compose version` — never an `environment:` block, any env value, or the raw render.
- [x] `TestTheDuplicationDoesNotDrift::test_the_shared_services_declare_the_same_envelope` and
      `TestResourceEnvelope` pass; the `lint` job has a second render step carrying `FORAGE_CPUS: "2"`,
      `FORAGE_MEM_LIMIT: 2048m` and the `SEARXNG_SECRET` placeholder in its own `env:`;
      `tests/test_ci_workflow.py` asserts the second step's names and values, the existing step's
      `env:` is unchanged, and the placeholder test iterates both steps, checking the two envelope
      names against their size-shape regexes and every other value for "placeholder".
- [x] `retrieval_app.py:355-361` and `:1446-1451` name the shipped probe and say liveness, not
      health; `contract_smoke.py:9` and `tests/test_app.py:936` match; window mechanics (R36):
      docstring line appended in the `* ``1.3.0`` — …` format; `uv run python -m
      scripts.export_contract` run; `tests/golden/contract_1_3_0.json` re-created via `_SCHEMA_MODELS`
      (it moves — the `HealthResponse` description — recorded); `_EXPECTED_ONE_THREE_ZERO_DIFF`
      reviewed (no field — recorded); the four anchor-quoting pages refreshed; `uv run python -m
      scripts.export_contract --check` green; `grep -rn 'bare ``curl -f``\|bare `curl -f`\|10 s x 5'
      retrieval_app.py model_fetcher.py contract/ contract_smoke.py tests/ --exclude-dir=golden
      kit_tools/docs/API_GUIDE.md` returns nothing (R43: eleven hits at planning time, executed —
      `retrieval_app.py:358,1307,1449`, `model_fetcher.py:62`, `contract_smoke.py:9`,
      `contract/openapi.yaml:423,1417`, `tests/test_app.py:936`, `tests/fixtures/contract/
      unregenerated_openapi.yaml:422,1416`, `API_GUIDE.md:93` — the last three regenerated or rewritten;
      `tests/golden/contract_1_0_0.json`, `_1_1_0.json` and `_1_2_0.json:201` are excluded because
      invariant 4 freezes them, and `retrieval_app.py:1307` / `model_fetcher.py:62` are comment-only
      corrections that rotate nothing).
- [x] `sanitizer_revision` rotation measured (revert-and-reproduce on `pipeline/contract.py`) and
      recorded at the five sites ruling 6 names.
- [x] Tests written/updated for new functionality.
- [x] Full test suite passes (`uv run pytest`).
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

### US-003: Operator documentation — the sizing section and the by-value sweeps

**Priority:** P2

**Description:** As a third-party operator, I want one place that says how to size Forage, how to
reach every knob from the published fragments, and what the healthcheck does not mean — and I want
no tracked file left claiming the fragments declare no healthcheck, no CPU quota, or a fixed 1 vCPU /
1 GB box.

**Independent Test:** `grep -l 'Sizing the container' README.md docs/configuration.md kit_tools/docs/
DEPLOYMENT.md kit_tools/docs/MONITORING.md kit_tools/arch/INFRA_ARCH.md kit_tools/arch/SECURITY.md`
lists six files (one per line); `grep -l FORAGE_CPUS README.md docs/configuration.md kit_tools/docs/
ENV_REFERENCE.md kit_tools/arch/INFRA_ARCH.md` lists four; `grep -rn -iE '(no|without|neither)[^|]{0,40}healthcheck|No CPU quota|10 s x 5|bare .curl -f.' kit_tools/
docs/ kit_tools/arch/ docs/ README.md | grep -v 'unless the operator sets one'` returns only lines
about the image-level `HEALTHCHECK` instruction (which stays absent), each of which also says on the
same line that the compose fragments declare one — the `grep -v` excludes, by its own content, the one
true "no CPU quota" phrase the verbatim `SECURITY.md:374` restatement carries, so the restatement stays
verbatim and the criterion stays checkable (round-4 salty review; R16 corrected in round 5);
`INFRA_ARCH.md:348` ("Not provided — … a container `HEALTHCHECK`; CPU or pids limits"), which the
pattern does not match, is on the hand list and is rewritten to say the fragments declare a compose
healthcheck and a `cpus` knob; `grep -rn 'only tighten' kit_tools/docs/ kit_tools/arch/ docs/ README.md` and `grep -n 'never raise'
kit_tools/arch/SECURITY.md` (R43: `kit_tools/specs/`, the untracked `kit_tools/.seed_cache/` — whose
`tech-stack_summary.md:82` repeats the old claim and is a regenerated artefact left alone — and the
result artefacts are excluded; five hits at planning time, executed: `ENV_REFERENCE.md:107`,
`TROUBLESHOOTING.md:161`, `:524`, `DECISIONS.md:105`, `SECURITY.md:182`) return only lines that either
name all three raisable keys (`classification_concurrency`, `child_address_space_bytes`,
`admission_queue_depth`) or are scoped to a single key that is still its own maximum
(`TROUBLESHOOTING.md:161`), and no surviving line claims a single carve-out; `grep -rn '1000 ms' kit_tools/docs/
docs/` returns nothing; `grep -n 'Pinned at 1' docs/configuration.md` returns only the
`extraction_concurrency` row (`:488`); `kit_tools/arch/DECISIONS.md` has an entry dated 2026-09 titled
with "sized to the host"; `grep -n 'liveness' kit_tools/docs/DEPLOYMENT.md kit_tools/docs/
MONITORING.md` hits both; and every `1 vCPU / 1 GB` and `mem_limit: 1024m` hit outside `kit_tools/specs/`
and test docstrings carries a "configurable" or "reference envelope" qualifier on the same line or the
next.

**Implementation Hints:**
- `docs/configuration.md`: a new **top-level** `## Sizing the container` section placed immediately
  before `## config.yaml` (`:414`) — not inside `### Weights acquisition` (`:307-412`), where the
  reference-envelope paragraph at `:362-372` lives; that paragraph gains one cross-reference sentence
  distinguishing weights-boot latency from classify latency. Table columns `Host envelope |
  FORAGE_CPUS | FORAGE_MEM_LIMIT | promptguard_threads | classification_concurrency | cache.max_bytes
  | classify latency`; rows `1 vCPU / 1 GB (reference)` = `1 / 1024m / 1 / 1 / 33554432`, `2 / 2 GB`
  = `2 / 2048m / 2 / 1 / 67108864`, `4 / 4 GB` = `4 / 4096m / 2 / 2 / 134217728`; the last column
  reads "measured in spec 7 (`feature-hardening-promptguard-86m` US-004)" in every row and its header
  states the `num_results` the figure is measured at (the loop runs once per served result, so a
  classify latency without its `num_results` is not comparable — round-3 salty review); one sentence
  under the table says the `4 / 4 GB` row's `cache.max_bytes` `134217728` sits **at**
  `_MAX_CACHE_MAX_BYTES` (`cache.py:229`, 128 MiB) — the column does not keep doubling with the host and
  the ceiling is not configurable (round-4 salty review; Out of Scope records the deferral). Beneath
  it: the **memory** rule verbatim from US-001 (five terms — the named `PARENT_RESERVATION_BYTES` plus
  the selected model's resident delta, `0` for the 22M, with a per-model column spec 7 fills; the child
  term as the configured `child_address_space_bytes`; the cache term as `cache.max_bytes` under the
  in-memory backend or `cache.max_value_bytes`, one in-flight read, under Valkey — with one sentence
  that `full.yml`'s Valkey moves the cache's memory into its own container and leaves one read's
  worth here), with `CLASSIFIER_WORKING_SET` named as provisional (64 MiB, its derivation and its
  no-measurement caveat) until spec 7 measures it, the worked reference row (`512 + 0 + 64 + 384 + 32
  = 992 MiB` under `1024m` on `minimal.yml`, a 32 MiB margin; `964 MiB` on `full.yml`), the
  sentence "boot warns `envelope_memory_rule_unmet` when the cgroup limit is readable and strictly
  below the rule; above it the failure is an OOM kill `/health` cannot report", and the decided
  consequence of a larger measurement (the shipped default does not move; the reference row's
  recommended `FORAGE_MEM_LIMIT` rises and 1 GiB hosts get the WARNING); the **under-sizing is a
  security decision** sentence — under spec 2 US-006 a classification wait that outlives
  `promptguard_wait_seconds` is `unavailable_blocked` for fail-closed requests and
  `unavailable_allowed` (served unscanned and marked) for fail-open ones, so an envelope that
  cannot keep the loop under the target is choosing between availability and scanning for its
  fail-open callers; the **CPU** rule with its precondition — "**when `FORAGE_CPUS` is set to a non-zero
  value**, keep `promptguard_threads × classification_concurrency ≤ FORAGE_CPUS`; with `FORAGE_CPUS`
  unset or `0` the container sees every host core and the rule does not apply — size
  `promptguard_threads` to the cores you actually intend Forage to use, since torch cannot see a
  cgroup quota"; "with no variable set the fragment imposes no CPU limit (Compose omits the key)";
  the minimum Compose (v2, Compose Spec; verified v2.40.3); the two verification sentences —
  "confirm `FORAGE_MEM_LIMIT` landed via `/metrics` `extraction.cgroup_memory_max_bytes`
  (`retrieval_app.py:1010-1028`)" and "confirm the CPU quota with `docker inspect -f
  '{{.HostConfig.NanoCpus}}' <container>` — there is no in-service CPU-quota signal; auto-detection is
  deferred"; the **`config.yaml` delivery path** — the fragments do not mount `config.yaml`
  (`Dockerfile:164` bakes it; the only volume is `forage-model-cache`), so the three `config.yaml`
  columns and the two latency keys need a bind mount: a real, copy-pasteable `volumes:` snippet
  (`- ./config.yaml:/app/config.yaml:ro`) with the three warnings that `_load_config`
  (`retrieval_app.py:329-336`) **replaces, never merges** — start from the shipped file (the repo's
  `config.yaml`, or `docker compose cp forage:/app/config.yaml ./config.yaml`), because a two-line file
  silently drops `user_agents` and `news_domains` to their empty code defaults **and resets every
  security-relevant key to its code default** — `promptguard_threshold`, `extract_route_enabled`,
  `seed_blocklist`, spec 2's `promptguard_fail_closed_floor` / `promptguard_threshold_ceiling`, **and
  this spec's four envelope keys** — `promptguard_threads` (back to 0: torch sizes to the host's cores
  under a CPU quota, the 2026-09-12 incident), `classification_concurrency` (back to 1),
  `search_promptguard_latency_target_ms` and `search_first_token_target_ms` — with the consequence
  written out: an envelope reset to defaults on a tuned host pushes fail-open requests toward
  `unavailable_allowed` under spec 2 US-006; an operator who hardened one copy and later mounts a
  shorter one silently loses the hardening, and **nothing but this warning protects the operator's own
  baseline** — the shipped-equals-code-default test (its own bullet below) proves only that a short
  file cannot loosen anything relative to the *shipped* file (round-3 security review; boot-time
  logging of the effective values was considered and declined — INFO never renders and a WARNING for
  normal values is noise — Decisions Made); the snippet names all of those keys; that a host path that
  does not exist mounts a *directory* and the container will not boot; and that `docs/configuration.
  md:425-428`'s "the shipped file is not always the code default" applies; and the upgrade sentence:
  pulling the updated fragments changes nothing until a variable or key is set.
- The shipped-equals-code-default pinning test, as its own deliverable (round-3 salty review): beside
  spec 3 US-003's registry test, a test that the shipped `config.yaml` value of each security-relevant
  key **equals** its code default, over an explicit constant `SECURITY_RELEVANT_CONFIG_KEYS` defined
  beside the test — `promptguard_threshold`, `extract_route_enabled`, `seed_blocklist` (today),
  `promptguard_fail_closed_floor` and `promptguard_threshold_ceiling` (spec 2 US-006 — if spec 2 landed
  them under other names, the constant is the one place to correct, and the test reads the names from
  it), and the four envelope keys — with a **partition** assertion (round-3 security review): every key
  in `KNOWN_CONFIG_KEYS` is in exactly one of `SECURITY_RELEVANT_CONFIG_KEYS` or an explicit
  `_NOT_SECURITY_RELEVANT_CONFIG_KEYS` (`user_agents` and `news_domains` deliberately differ from their
  code defaults, which is why a blanket all-keys test is unavailable), so a new `config.yaml` key goes
  red until someone classifies it. Both sets hold **dotted registry names** exactly as
  `KNOWN_CONFIG_KEYS` spells them (spec 3 US-003: top-level keys bare, block leaves as
  `extraction.<key>` / `cache.<key>` / `retrieve.<key>`, block names bare) and the failure message
  prints them that way; the **membership rule is written beside the constant** — *security-relevant: a
  key whose value decides whether or how content is scanned, served or sandboxed* (thresholds, floors
  and ceilings, route enablement, blocklists, the four envelope keys, the untrusted-PDF child's
  `RLIMIT_AS`); *not: a pure resource or throughput bound, a cosmetic list, or a bare block name* — so
  `extraction.child_address_space_bytes` is classified **in** (the sandbox limit this spec rewrites
  four files to call raisable), `extraction.admission_queue_depth` **out** (a queue depth), each
  explicitly rather than by omission, and the bare block names `cache`, `extraction`, `retrieve` go in
  the not-relevant set (round-4 salty review: an exhaustive partition with no stated criterion is a
  coin flip for every future key). It proves the shipped baseline only; the day a shipped value stops
  equalling its code default the test says so.
- The sweeps are greps (R39); the lists below are starting points — floors, not ceilings — and the
  criterion is the grep:
  - `1 vCPU / 1 GB` — nine files at planning time (`grep -rln '1 vCPU / 1 GB' --include='*.md'
    --include='*.py' --exclude-dir=specs --exclude-dir=.seed_cache --exclude-dir=golden .` — R43;
    the result artefacts are `.json` and outside the include set — minus `tests/test_model_fetcher.py:
    3458`, a test docstring left alone): `LOCAL_DEV.md:207`, `MONITORING.md:109`, `DEPLOYMENT.md:189,356`,
    `TROUBLESHOOTING.md:233`, `INFRA_ARCH.md:223,258`, `SERVICE_MAP.md:93,158,304`,
    `docs/configuration.md:362`, `docs/weights.md:430` — each becomes "the reference envelope (1 vCPU /
    1 GB), configurable via `FORAGE_CPUS` / `FORAGE_MEM_LIMIT` — see `docs/configuration.md` § Sizing
    the container" or gains that qualifier on the same line.
  - `mem_limit: 1024m` — nine files: `cache.py:219`, `TROUBLESHOOTING.md:654`, `DEPLOYMENT.md:151,357`,
    `SERVICE_MAP.md:375`, `INFRA_ARCH.md:146,265`, `SECURITY.md:374` ("set `mem_limit: 1024m` and
    nothing else" — now `cpus` too), `docs/configuration.md:452`, plus the two fragments US-002 already
    changed — each restated as `${FORAGE_MEM_LIMIT:-1024m}` (default `1024m`) or qualified.
  - `384 MiB` — thirteen files; every one describes the pypdf child's `RLIMIT_AS` and is **correct as
    is**; the five that spell out the 512 + 384 + ~128 arithmetic (`cache.py:219-223`,
    `config.yaml:29-31`, `docs/configuration.md:452-453`, `TROUBLESHOOTING.md:655`,
    `DEPLOYMENT.md:357`) gain a pointer to § Sizing the container and, where they say what the
    headroom is for, the corrected decomposition — the ~128 MiB holds the 32 MiB cache, the 64 MiB
    provisional classifier working set and a 32 MiB margin — **never** the round-2 sentence that "the
    128 MiB headroom is the classifier's working set", which the corrected rule contradicts; the
    `cache.py` comment's own claim (the cache spends a quarter of the headroom) stays true and stays;
    the rest are left (disposition recorded in Implementation Notes per hit).
  - The healthcheck / CPU-quota claims — `MONITORING.md:28` (amend) and `:415-425` (the "There is no
    `HEALTHCHECK`" paragraph and the "Suggestion only" block: promote the block to the shipped
    `curl -fsS -o /dev/null` form and rewrite the paragraph as the liveness paragraph), `DEPLOYMENT.md:
    154` and `:375-377`, `LOCAL_DEV.md:234-235`, `TROUBLESHOOTING.md:699-707` ("Adding a container
    healthcheck" — now "the shipped healthcheck and what it means"), `INFRA_ARCH.md:104` (the
    image-level instruction stays absent — the row says compose declares one), `:153`, `:156` ("No
    CPU quota" → the `cpus` knob), `:348`, `SERVICE_MAP.md:74` (also its "10 s x 5" sentence),
    `SECURITY.md:374`, `MONITORING.md:415`'s "10 s × 5 retries … is **Poppy's** compose" sentence
    (now: the shipped probe is this repo's), and the two `reconnect_*` lines the probe now drives
    unconditionally (round-3 salty review) — `MONITORING.md:174`, whose "*if* you poll `/health`"
    conditional becomes "the compose healthcheck polls `/health` every 30 s, so on any deployment with
    Valkey down these counters have a non-zero, steadily climbing baseline — at most one attempt per
    probe: one per probe on connection refused, about one per two probes on a connect timeout — the
    *rate* is diagnostic, not the value", and `:342`, the "Cache flapping or down" row, which
    distinguishes `reconnect_failures` climbing at the probe-driven rate (the down-Valkey heartbeat —
    one per probe or one per two) from flapping (`successes` climbing too).
  - `only tighten` / `never raise` / `1000 ms` — `grep -rn 'only tighten' kit_tools/docs/ kit_tools/arch/
    docs/ README.md` and `grep -n 'never raise' kit_tools/arch/SECURITY.md` (`ENV_REFERENCE.md:107` is
    US-001's; `TROUBLESHOOTING.md:524` and `DECISIONS.md:105` gain the three-key wording —
    `classification_concurrency`, `child_address_space_bytes`, `admission_queue_depth` — never a
    single carve-out; `SECURITY.md:182` is corrected below; `TROUBLESHOOTING.md:161` is scoped to
    `max_promptguard_chunks` and stays; the untracked `kit_tools/.seed_cache/tech-stack_summary.md:82`
    is a regenerated artefact outside the sweep) and `grep -rn '1000 ms' kit_tools/docs/ docs/`
    (`MONITORING.md:286` is US-004's; anything else is this story's).
- `kit_tools/arch/INFRA_ARCH.md:256-275` is already a `## Resource Envelope` section with a `Budget |
  Value | Source` table: add a `Container CPU cap | ${FORAGE_CPUS:-0} (0 = no limit) | both compose
  fragments` row beside `:265`, restate `:265` as `${FORAGE_MEM_LIMIT:-1024m}`, note at `:269` that
  `classification_concurrency` is 1–8 under the memory rule, cross-reference § Sizing the container
  from `:258-261`, and leave `:272` (the Disk row) alone — the boot-latency row is `:270`.
- `kit_tools/docs/ENV_REFERENCE.md`: a new section `### Container envelope (never read by Forage;
  Compose substitution only)` in the five-column shape of `### Companion SearXNG container` (`:56-58`)
  with `FORAGE_CPUS` and `FORAGE_MEM_LIMIT` — not the runtime table (`:36`), so nobody adds them to
  `_CLEARED_ENV_VARS`. The three new top-level key rows (US-001, US-004) and the rewritten
  `classification_concurrency` row (`:118`) are verified present.
- `README.md`: the Quickstart's command is `docker compose -f minimal.yml up -d` at `:80`, inside the
  fenced block that closes at `:84`; one new sentence **after** the fence names `FORAGE_CPUS` /
  `FORAGE_MEM_LIMIT` and points at `docs/configuration.md` § Sizing the container.
- `kit_tools/docs/MONITORING.md`: the liveness paragraph — liveness only; must never gate traffic,
  `depends_on: service_healthy`, or a consumer's activation; the classifier's state is `/health`'s
  `promptguard_loaded` / `degraded_reasons` plus `/metrics` `search.unscanned_results`; the probe calls
  `/health`, which pings the cache when due, so `cache_connected` recovery is now detected within one
  probe interval even with no traffic — and, with Valkey down, at most one reconnect WARNING and one
  `reconnect_failures` increment per probe for as long as it stays down (one per probe when refused,
  about one per two when timing out — the down-Valkey heartbeat, `:174` / `:342`) — and the
  `cgroup_memory_max_bytes` verification note beside the
  US-004 runbook line, whose forward reference to § Sizing the container this story makes real.
  `kit_tools/docs/DEPLOYMENT.md`: the same liveness paragraph beside the compose instructions.
- `kit_tools/arch/SECURITY.md`: the "Upload ceilings" sentence (`:180-182`, "hard ceilings that
  `config.yaml` may lower but never raise") is **corrected, not merely extended** (R16 corrected in
  round 4): `MAX_CHILD_ADDRESS_SPACE_BYTES` 384 MiB leaves the never-raise list — it is the shipped
  default of a key configurable 128–512 MiB, so `config.yaml` *can* raise the untrusted-PDF child's
  `RLIMIT_AS` above shipped — and the sentence names the three raisable keys
  (`classification_concurrency` 1–8 under the memory rule and the boot WARNING,
  `child_address_space_bytes` 128–512 MiB, `admission_queue_depth` 0–4) while the true ceilings
  (`MAX_INPUT_BYTES`, `MAX_PDF_PAGES`, `MAX_CHILD_CPU_SECONDS`, `MAX_EXTRACTION_WALL_SECONDS`,
  `MAX_EXTRACTED_OUTPUT_BYTES`, the character ceiling) stay listed as such; the admission-control table's PromptGuard
  row (`:197`) states the configurable 1–8 range and the memory caveat; `:374`'s "Container hardening
  beyond non-root" bullet is restated **verbatim** as: "The compose fragments set `mem_limit:
  ${FORAGE_MEM_LIMIT:-1024m}` and `cpus: ${FORAGE_CPUS:-0}` (no CPU quota unless the operator sets
  one) and nothing else: no `read_only` rootfs, no `cap_drop`, no `no-new-privileges`, no seccomp
  profile, no `pids_limit`. Unknown whether that is a deliberate omission." — the five remaining
  omissions are named in Out of Scope, not silently dropped; and `SECURITY.md` gains the
  under-sizing sentence (the sixth `Sizing the container` file the Independent Test counts).
- `kit_tools/arch/DECISIONS.md`: "Sized to the host, not the deployment (2026-09-19)" citing the
  Epic-2 cutover finding and R16 (corrected), recording that runtime tunables stay `config.yaml`
  (precedent: `search_brave_*`, `promptguard_threshold`) and the vision's "overridable by env" is met
  at the compose layer plus the bind mount — no `FORAGE_*` env override for `promptguard_threads` —
  plus the rotation rows from US-002 and US-004. `kit_tools/docs/GOTCHAS.md`: two short active gotchas
  — "torch and the fast tokenizer see the host's cores, not the cgroup quota" (why `promptguard_threads`
  exists) and "a healthy container is not a classifying container".
- The arithmetic comments the sizing section supersedes — `config.yaml:29-31,39-41`,
  `pipeline/extraction_limits.py:41-45`, `docs/configuration.md:343,452-453,485` — point at the sizing
  section (a comment-only `.py` edit, so the gates run).

**Acceptance Criteria:**
- [ ] `docs/configuration.md` has the top-level `## Sizing the container` section before
      `## config.yaml`, with the three rows, the `num_results` in the classify column's header and the
      `4 / 4 GB` row's at-the-ceiling sentence, the five-term memory rule (the named parent constant
      plus the selected model's resident delta with its per-model column, the child term as the
      configured `child_address_space_bytes`, the cache term as in-memory `max_bytes` or Valkey
      `max_value_bytes`, the Valkey sentence) with its provisional 64 MiB coefficient, its derivation,
      the worked 992 MiB (`minimal.yml`) / 964 MiB (`full.yml`) reference row and the boot-WARNING
      sentence, the
      decided larger-measurement consequence, the under-sizing-is-a-security-decision sentence naming
      `promptguard_wait_seconds`, `unavailable_blocked` and `unavailable_allowed`, the CPU rule with its
      non-zero-`FORAGE_CPUS` precondition, the "no CPU limit by default (Compose omits the key)"
      sentence, the minimum Compose, both verification sentences, the real bind-mount snippet with the
      replace-not-merge warning naming the security-relevant keys **and the four envelope keys** with
      the "nothing but this warning protects the operator's own baseline" sentence, the missing-path
      warning, and the classify-latency column deferring to spec 7 by name; the weights-acquisition
      paragraph distinguishes boot latency from classify latency.
- [ ] The shipped-equals-code-default test exists and passes over `SECURITY_RELEVANT_CONFIG_KEYS`
      (the three keys of today, spec 2's floor and ceiling, this spec's four envelope keys,
      `extraction.child_address_space_bytes`), and its partition assertion places every
      `KNOWN_CONFIG_KEYS` entry — as dotted registry names — in exactly one of the two explicit sets,
      with `extraction.admission_queue_depth` and the bare block names in the not-relevant set and the
      membership rule written beside the constant.
- [ ] Every grep in the Independent Test — `Sizing the container` (six files), `FORAGE_CPUS` (four),
      the healthcheck/CPU-quota claims, `only tighten` / `never raise` (five tracked hits at planning
      time, each either three-key or single-key-scoped afterwards), `1000 ms`, `Pinned at 1`,
      `liveness` — returns exactly what it states, each scoped as written (R43); the `1 vCPU / 1 GB`,
      `mem_limit: 1024m` and `384 MiB` sweeps are recorded hit by hit in Implementation Notes with a
      disposition (rewritten / qualified / correct-as-is), no tracked file says the 128 MiB headroom
      *is* the classifier's working set, and `grep -rn 'mem_limit: 1024m' --include='*.md'
      --include='*.py' --exclude-dir=specs --exclude-dir=.seed_cache --exclude-dir=golden .` returns
      only lines that also contain `FORAGE_MEM_LIMIT` or "default".
- [ ] `INFRA_ARCH.md`'s Resource Envelope table has the CPU-cap row and the restated memory row;
      `ENV_REFERENCE.md` has the "Container envelope" section with both variables and
      `tests/test_hermeticity.py`'s exact-set test is unchanged; `README.md` names both variables after
      the Quickstart fence; `MONITORING.md` and `DEPLOYMENT.md` carry the liveness paragraph with the
      cache-ping sentence, the down-Valkey heartbeat clause and the 2 s-of-5 s worst case;
      `MONITORING.md:174` and `:342` state the probe-driven baseline of the `reconnect_*` counters;
      `SECURITY.md`'s never-raise list no longer contains `MAX_CHILD_ADDRESS_SPACE_BYTES` and names the
      three raisable keys, and the file carries the admission-row caveat, the verbatim `:374`
      restatement and the under-sizing sentence.
- [ ] `DECISIONS.md` has the dated decision entry with the config-not-env rationale; `GOTCHAS.md` has
      both gotchas under "Active Gotchas"; the upgrade sentence exists; `grep -n 'Pinned at 1'
      docs/configuration.md` returns only the `extraction_concurrency` row (`:488` at planning time).
- [ ] Full test suite passes (`uv run pytest`).
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pyright` pass.

## Edge Cases

- `FORAGE_CPUS` unset or `0` → Compose omits the `cpus` key → no CPU limit, today's behaviour;
  fractional (`0.5`) is valid Compose input and passed through; the table says "below 1 vCPU is
  unsupported" (US-002).
- `promptguard_threads: 0` → `set_num_threads` never called, `TOKENIZERS_PARALLELISM` never written;
  a positive value overwrites an operator-set `TOKENIZERS_PARALLELISM` — documented as the one
  variable Forage writes (US-001).
- `promptguard_threads` larger than the cgroup quota oversubscribes; torch still runs, slower —
  documented, not refused; auto-sizing is out of scope (US-001, US-003).
- A `set_num_threads` failure never disables the classifier; it logs its own WARNING (US-001).
- `classification_concurrency: 8` on a 1 GiB cgroup is accepted at boot with one
  `envelope_memory_rule_unmet` WARNING; the consequence above the rule is an OOM kill the docs
  describe (US-001, US-003).
- The shipped defaults on a 1 GiB cgroup (`memory.max` exactly `1073741824`) boot silent: the rule
  is 992 MiB and the comparison is strictly-below; a limit exactly equal to the rule is silent too
  (US-001).
- `cache.max_bytes` raised to its 128 MiB ceiling **with the in-memory backend** (`minimal.yml`,
  `VALKEY_URL` unset) on a 1 GiB cgroup at the default concurrency puts the rule at 1088 MiB — a
  WARNING, which is the point of the cache term; the same key under `full.yml`'s Valkey backend
  changes nothing, because the term there is one in-flight read (`cache.max_value_bytes`) and the
  bound is the Valkey container's (US-001).
- Selecting a non-default classifier (spec 7 US-006) adds that model's measured resident delta to the
  parent term, so an 86M selection on a 1 GiB host is a WARNING, never silent; with the 22M the delta
  is `0` and nothing changes (US-001; the row and the routing are spec 7's handoff).
- `extraction.child_address_space_bytes` raised to its 512 MiB ceiling on a 1 GiB cgroup at the
  shipped defaults puts the rule at 1120 MiB — a WARNING, because the rule reads the configured value,
  never the 384 MiB constant (US-001).
- A measured coefficient above 96 MiB in spec 7 moves the reference row's recommended
  `FORAGE_MEM_LIMIT`, not the shipped default; 1 GiB hosts then see the WARNING (US-001, US-003).
- A host without cgroup v2 (`cgroup_memory_max_bytes` is `None`) boots with no memory WARNING
  (US-001).
- A latency target of exactly the measured duration does not count (strictly greater, matching the
  existing `>` at `orchestrator.py:1082`); the max is updated regardless (US-004).
- A `/search` that classifies zero results (all omitted before stage 3, or served empty on a
  `[searxng]`-only chain) measures a near-zero loop, never counts, and may still raise the max from 0;
  a `/search` that fails before the loop (422) touches neither field (US-004).
- Under spec 2 US-006 a slow envelope shows up first as `search.sanitization_latency_max_ms` climbing
  (whole-loop wall time, scaled by `num_results`), then as `search.classification_wait_timeouts`
  rising — the signal that requests are being blocked or served unscanned — and as
  `unavailable_blocked` / `unavailable_allowed` outcomes; the runbook sentence and the sizing section
  name that progression and never compare the max to `promptguard_wait_seconds` (US-004, US-003).
- A `/search` with `num_results: 20` measures a loop roughly twenty times a `num_results: 1` loop;
  the max and the count are comparable only at the same `num_results`, and both descriptions and both
  `MONITORING.md` rows say so (US-004).
- One cold-boot outlier pins `search.sanitization_latency_max_ms` for the process's life — by design; the
  field description and the runbook say to read it with the count and `search.requests`, and that a
  restart is the only reset (US-004).
- `FORAGE_MEM_LIMIT=abc` fails at Compose, before Forage starts — the fragment comment says the value
  takes Docker's byte-unit syntax (US-002).
- The healthcheck passes while `/health` reports `degraded`, and a `healthy` container may have the
  classifier unloaded — by design; the comment and the docs say so (US-002, US-003).
- Plain `docker compose` reports an unhealthy container and never restarts it (US-002).
- The probe pings the cache when due, so cache recovery is detected within one interval with no
  traffic; with the cache down the probe may take up to 2 s (`_RECONNECT_TIMEOUT_S`), inside its 5 s
  timeout — and each probe that lands after the backoff logs a reconnect WARNING and bumps
  `reconnect_attempts` / `reconnect_failures`, so a down Valkey is a probe-driven heartbeat in the log
  — one per probe when refused, about one per two probes when timing out — and a steady climb on two
  counters with no traffic at all (US-002, US-003).
- A short bind-mounted `config.yaml` resets `promptguard_threshold`, `extract_route_enabled`,
  `seed_blocklist`, spec 2's floor/ceiling keys **and the four envelope keys** to code defaults — which
  equal the shipped values today, pinned by test; the operator's own tuned values are protected by
  nothing but the warning; the snippet says to start from the shipped file (US-003).
- A bind-mounted two-line `config.yaml` drops `user_agents` and `news_domains` to code defaults; a
  missing host path mounts a directory and boot fails — both documented beside the snippet (US-003).

## Out of Scope

- Choosing the 86M model or measuring its latency or working set — spec 7 (which fills the sizing
  table's last column and replaces the provisional coefficient).
- Auto-detecting the cgroup CPU quota to size threads automatically, and a CPU-side boot WARNING —
  deferred, with the security ordering stated honestly rather than reportability alone (round-3
  security review): memory under-sizing fails **stop** (an OOM kill; nothing is served unscanned),
  while CPU under-sizing — a `FORAGE_CPUS` cap with `promptguard_threads` left at 0, the 2026-09-12
  shape — fails **open** under spec 2 US-006 (`unavailable_allowed`), and is accepted here as
  observable only after the fact, through `search.classification_wait_timeouts` and the latency max.
  `/sys/fs/cgroup/cpu.max` is readable where `_cgroup_memory_snapshot` reads `memory.max`, so the
  symmetric WARNING ("`cpu.max` is a quota and `promptguard_threads` is 0") is a few lines for a
  follow-up story, which also has to decide what that condition means on a multi-tenant host; it is
  not silently dropped (Decisions Made).
- A `FORAGE_*` environment override for `promptguard_threads` or the latency keys: runtime tunables
  stay `config.yaml` (precedent `search_brave_*`, `promptguard_threshold`); the compose path reaches
  them by bind mount, documented.
- Parallelising `/search`'s per-result classification loop; wiring the classification semaphore into
  `/search` and `/retrieve` is spec 2 US-006, not this spec.
- Making the 1 MiB provider bounds or the 10 MB fetch cap configurable (this closes the carried-over
  question from spec 5: no).
- An image-level `HEALTHCHECK` instruction, and healthchecks on the companion `searxng`/`valkey`
  services — compose-level liveness on `forage` only.
- A windowed or resettable latency maximum — the per-process high-water mark is documented as such.
- Kubernetes manifests or any deployment target other than the two compose fragments.
- Changing `/health`'s semantics (invariant 5 stands; liveness is the compose healthcheck's job).
- Giving `search_first_token_target_ms` an effect beyond the log line (it exists for symmetry with
  the ruling; documented as log-only).
- The five container-hardening omissions `SECURITY.md:374` lists and this spec's restatement keeps
  listing — `read_only` rootfs, `cap_drop`, `no-new-privileges`, a seccomp profile, `pids_limit` —
  are not added here: each is a behaviour change for every operator who pulls the fragment, the
  opposite of this spec's "defaults change nothing" rule, and none is a sizing knob. They stay
  named as unknown-whether-deliberate for a hardening spec of their own.
- Wiring the classification semaphore's bounded wait — spec 2 US-006 owns `promptguard_wait_seconds`;
  this spec only documents how the envelope interacts with it.
- Widening `_MAX_CACHE_MAX_BYTES` (128 MiB, `cache.py:229`) under the memory rule — the `4 / 4 GB`
  sizing row sits at that ceiling; raising it is deliberately deferred, and the sizing section says the
  ceiling is not configurable (round-4 salty review).

## Assumptions

- Poppy is the only consumer; the `/metrics` fields are additive and defaulted; the two `/health`
  description corrections change no shape.
- The 1.3.0 contract window is open when this spec executes (spec 1 US-004 opened it), spec 3
  US-003's `KNOWN_CONFIG_KEYS` registry and AST reader list exist, spec 2 US-001's
  `pipeline/config_bounds.py` exists, spec 2 US-006 has wired the classification semaphore into
  `/retrieve` and `/search`, spec 4 US-001's `cache.max_value_bytes` exists, and spec 5 US-001's
  `RecordingSearchMetrics`, US-005's wire pins and US-003's runbook edit exist.
- Docker Compose v2 (Compose Spec) is the supported runner; `${VAR:-default}` substitution and the
  service-level `cpus` key are Compose Spec features; `cpus: 0` renders as no key (verified on
  v2.40.3, validation round 1).
- The image keeps `curl` (installed at `Dockerfile:67` for exactly this healthcheck); the `python3
  -c` branch is the fallback, not the plan.
- Today's state is the default in every knob; this spec changes no default and no wire byte for an
  unchanged deployment.
- The classifier's working set per concurrent classification is unmeasured; 64 MiB (the reference
  envelope's 96 MiB residual after the parent, one child and the shipped cache, with a 32 MiB margin
  kept) is the provisional coefficient until spec 7 US-004 measures it; the child term is the
  configured `child_address_space_bytes` (the pypdf child's address space, 384 MiB shipped) and its
  range is not revisited here; the cache term is `cache.max_bytes` under the in-memory backend and
  one in-flight read (`cache.max_value_bytes`) under Valkey; the 512 MiB parent term is the existing
  reservation (`docs/configuration.md:485`) with the 22M resident and is not re-measured here — a
  non-default model's resident delta is a per-model row spec 7 measures and fills (`0` for the 22M), so
  the rule is model-dependent by construction and never silent on a bigger model.
- Pydantic renders a model's class docstring as its JSON-schema `description`, so the `HealthResponse`
  correction moves the 1.3.0 golden; the route docstring moves only `contract/openapi.yaml`.
- `tests/test_ci_workflow.py`'s placeholder test binds to the first `docker compose` step in `lint`;
  a second step is invisible to it until the allow-list extension US-002 makes.
- The fast tokenizer's parallelism is moot at one chunk per call; pinning it is defence in depth.

## Technical Considerations

- Rotation ledger (ruling 6, R32): US-004 rotates `pipeline/orchestrator.py` and `pipeline/contract.py`
  (one measurement, each reverted in turn, both reverted as the control); US-002 rotates
  `pipeline/contract.py` (its docstring line for the description correction); US-001 and US-003
  rotate nothing. Both rotations are recorded at the five sites. The four new keys are performance
  knobs; the hash covers the sanitizer's rules, not its throughput, so unlike `promptguard_threshold`
  they are not `derive_sanitizer_revision` inputs — the rotations come only from the source bytes.
  They are not *security-neutral*, though: under spec 2 US-006 latency decides whether a fail-open
  request is scanned, which the Security Considerations section records.
- `torch.set_num_threads` must run in the process that classifies; classification runs via
  `asyncio.to_thread` in the same process (`pipeline/stage3_promptguard.py:130`), so setting it once
  before the first load is sufficient. Intra-op threads only; inter-op is left at default.
  `TOKENIZERS_PARALLELISM` must be set before the tokenizer is constructed, which `load()` guarantees.
- `extra="forbid"` on every metrics model (`retrieval_app.py:638-660`) means each new field is added
  to the model *and* the counter class in the same commit, or `/metrics` 500s; `SearchMetricsSink`
  and `_NullSearchMetrics` must carry it too or pyright fails.
- Golden movement: US-004 moves `contract/openapi.yaml` and the anchor but not the golden (its
  fields are on `/metrics` models, outside `_SCHEMA_MODELS`); US-002 moves the golden as well —
  `HealthResponse` is in `_SCHEMA_MODELS` and its class docstring is the schema `description` —
  while neither adds an entry to the 1.3.0 diff list (`_added_paths` records added keys, not
  changed values); both record which.
- The memory rule's five terms are additive and counted once: the named parent constant plus the
  selected model's resident delta (`0` for the 22M; no classification in flight),
  `classification_concurrency × 64 MiB`, `extraction_concurrency × child_address_space_bytes` (as
  configured), and `cache.max_bytes` when the backend tag is `memory` or `cache.max_value_bytes` when
  it is `valkey`; the shipped defaults evaluate to 992 MiB (in-memory) or 964 MiB (Valkey), the
  comparison is strictly-below, and the
  shipped `FORAGE_MEM_LIMIT` default never moves on a measurement — the reference row's recommendation
  does. The check is a new lifespan call site after the backend selection (`retrieval_app.py:1282-1284`)
  in the shape of `_warn_if_break_glass_advertisement_enabled()`.
- CI renders both envelope branches in two steps because the placeholder test
  (`tests/test_ci_workflow.py:836-848`) requires every value in the first step's `env:` to say
  "placeholder"; the second step is located by name and its two envelope names are allow-listed.
- The manual `NanoCpus` check uses a secret-free `busybox` service in the scratch directory; Docker
  applies `cpus` identically to any image, so the fact carries over.
- Compose keeps `cpus` as a number and `mem_limit` as a string; the YAML loader in
  `tests/test_compose_fragments.py` sees the unsubstituted `${...}` strings — assert on those; the
  rendered form (`config`) normalises `mem_limit` to bytes and drops a zero `cpus`.
- The healthcheck binds to `127.0.0.1` inside the container namespace, so
  `TestDeploymentPosture::test_every_published_port_binds_to_loopback` (`:250-268`, iterates
  `_published_ports` only) is unaffected.
- `classification_concurrency` gates every route that acquires `app.state.classification_semaphore`;
  before spec 2 US-006 that is `/extract` only (`retrieval_app.py:1731` → `orchestrator.py:539`).
- The memory-rule WARNING reads the cgroup, not `FORAGE_MEM_LIMIT`, so it needs no new plumbing and
  works under any orchestrator's limit.
- `promptguard/classifier.py` importing `pipeline.config_bounds` is the first `promptguard` →
  `pipeline` import: cycle-free (`pipeline/__init__.py` is a docstring, `config_bounds` is a leaf,
  `pipeline/stage3_promptguard.py` imports the classifier lazily) and it imports a bounds helper, not
  a pipeline stage; no test forbids the direction (`_FORBIDDEN_IMPORTS` guards
  `pipeline/search_providers/` only).

## Security Considerations

- **Latency is a security input under spec 2 US-006.** The bounded wait on the classification
  semaphore means an envelope that cannot keep the per-result sanitization loop under
  `promptguard_wait_seconds` produces `unavailable_blocked` (fail-closed: blocked or omitted) or
  `unavailable_allowed` (fail-open: served unscanned and marked) outcomes. This spec's knobs cannot
  cause that on their own — every default is today's state — but an operator who raises
  `classification_concurrency` on an under-sized host, or who caps `FORAGE_CPUS` without setting
  `promptguard_threads`, can. The sizing section, the `classification_concurrency` rows, the
  `MONITORING.md` runbook and this section all say so; spec 2's `search.classification_wait_timeouts`
  is the signal that requests are going unscanned, and the high-water mark is the earlier, coarser
  one (whole-loop wall time, scaled by `num_results`, never compared to `promptguard_wait_seconds`).
- **The memory WARNING is advisory, never a refusal**, so deliberate over-subscription stays a
  supported operator choice; the consequence above the rule is an OOM kill `/health` cannot report,
  which is why the WARNING exists and why the rule's terms are counted once with a stated margin.
- **The bind-mounted `config.yaml` replaces, never merges.** A short file resets the
  security-relevant keys (`promptguard_threshold`, `extract_route_enabled`, `seed_blocklist`, spec 2's
  `promptguard_fail_closed_floor` / `promptguard_threshold_ceiling`) **and the four envelope keys**
  (`promptguard_threads`, `classification_concurrency`, `search_promptguard_latency_target_ms`,
  `search_first_token_target_ms`) to their code defaults — an envelope reset on a tuned host pushes
  fail-open requests toward `unavailable_allowed`; the defaults equal the shipped values today and a
  test pins that with a partition over `KNOWN_CONFIG_KEYS`, the operator's own hardening is protected
  by nothing but the warning beside the snippet, and the snippet says to start from the shipped file.
- **The `only tighten` / `never raise` invariant is stated truthfully**: three `extraction:` keys can
  be raised above their shipped values from `config.yaml` — `classification_concurrency` (this spec),
  `child_address_space_bytes` (the untrusted-PDF child's `RLIMIT_AS`, 128–512 MiB) and
  `admission_queue_depth` (0–4) — and `SECURITY.md`'s ceilings list no longer claims the sandbox limit
  is one.
- **The healthcheck captures no body** (`-o /dev/null`) and is documented as liveness that must never
  gate traffic, `depends_on: service_healthy` or a consumer's activation; a `healthy` container may
  have the classifier unloaded (invariant 5).
- **Nothing in this spec writes a secret anywhere**: the render is `--env-file /dev/null` with a
  complete placeholder set from a scratch directory, grep-filtered; CI's envelope values are sizes;
  the `NanoCpus` probe is a `busybox` service; Implementation Notes record filtered lines and two
  integers.
- **`TOKENIZERS_PARALLELISM=false` is the one environment variable Forage writes**, only when
  `promptguard_threads > 0`, and it disables a thread pool rather than enabling anything;
  `ENV_REFERENCE.md` records it.
- The five container-hardening omissions at `SECURITY.md:374` stay listed as unknown-whether-
  deliberate and are named in Out of Scope; this spec neither adds nor hides them.

## Related Documentation

- Configuration: `docs/configuration.md` (Sizing the container; `extraction:` table `:471-490`)
- Deployment: [DEPLOYMENT.md](../docs/DEPLOYMENT.md), [INFRA_ARCH.md](../arch/INFRA_ARCH.md) (Resource Envelope)
- Security: [SECURITY.md](../arch/SECURITY.md) (Upload ceilings, Admission control)
- Monitoring: [MONITORING.md](../docs/MONITORING.md) ("Adding a counter to `/metrics`", the `search` rows)
- Env contract: [ENV_REFERENCE.md](../docs/ENV_REFERENCE.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) ("Nothing configures logging", "/metrics counter without the model 500s", the divergence table)
- Rotations: `docs/bootstrap-notes.md`, [DECISIONS.md](../arch/DECISIONS.md), [GOTCHAS.md](../docs/GOTCHAS.md), [CODE_ARCH.md](../arch/CODE_ARCH.md)

## Implementation Notes

### US-001 implementation — 2026-09-22

- Added the classifier-owned bounded thread reader and `configure_threads` seam.
  Every load attempt applies positive counts before tokenizer construction; zero
  touches neither torch nor the environment. A failed torch setter logs only the
  count and exception type at WARNING and model loading continues. Acquisition
  signatures, the environment-clearing fixture and app-state defaults are untouched.
- Widened only classification concurrency to 1–8; extraction remains 1–1.
  Boot-created semaphores are exercised at every accepted count; two real ASGI
  `/retrieve` requests use timestamped, event-gated classifier doubles to prove
  overlap at two and serialization at one, with both tasks drained before mocks exit.
- The once-per-boot cgroup advisory follows actual backend selection. It counts the
  named 512 MiB parent plus the selected model's resident delta, provisional 64 MiB
  per classification, configured child limit per extraction slot and memory storage
  or one bounded Valkey read. Regressions pin strict inequality, unreadable cgroups,
  992/964 MiB defaults and their 32/60 MiB margins, concurrency four, enlarged cache,
  enlarged child, a synthetic 448 MiB model delta and a raised Valkey value bound.
  This remains an advisory, not a peak-RSS guarantee: the existing combined PDF-slot
  and concurrent-cache-read caveats are retained in the configuration reference.
- Registered both the key and its AST-swept reader. Updated both operator references
  and startup/boot-failure monitoring rows; preserved the extraction-concurrency
  rows. The two reference preambles name all three raisable extraction keys.
  US-003 still owns the wider documentation sweep and the Sizing the container
  section; US-002 owns the Compose variables.
- **Spec 7 handoff:** US-004 must replace the provisional coefficient using the
  concurrency 1→2 RSS measurement for both models, and fill the 86M resident-delta
  row from idle loaded RSS (86M minus 22M), not the marginal classification delta.
  Add a per-model sizing-table column; US-006 must pass the selected model id into
  the boot rule. The table belongs to this spec's US-003, not US-002. If the measured
  coefficient exceeds 96 MiB, keep the 1024m shipped ceiling and publish the measured
  minimum: a 1 GiB deployment warns rather than refusing boot or silently resizing.
  Matching handoff recorded in spec 7's Implementation Notes.
- **Unchanged surface:** starting commit `8fdb50fce95ff4351c60bc8a65e296a3d0ce0ba0`.
  All nine hashed sources and the hash definition, `models.py`, `contract/`,
  `model_fetcher.py` and `tests/conftest.py` are byte-identical. Default, shipped
  and maximum-performance-knob configurations all derive
  `d9db75863ea8a464147da8b38c9fc6b8772cf75c485f58cab896e8130c81b6e0`;
  no revision rotation or contract regeneration is needed.
- **Evidence:** 65 focused checks passed, then 1,252 related tests passed (ten
  existing non-failing Torch/socket-guard warnings). Strict Pyright reports zero
  errors; changed-file Ruff lint and formatting, contract export `--check` and
  whitespace checks pass. Full suite not run, as this implementer invocation
  explicitly forbids it. Repository Ruff lint/format gates are blocked by
  pre-existing files: `pipeline/bounded_body.py:53` and
  `tests/test_search_providers.py:762,764,784,787` (five E501 findings, two
  unformatted files). Reproduced directly from the starting commit with
  `git show` piped to pinned Ruff; neither unrelated file was edited.
  Result remains partial / needs-work for these outstanding gates, not a failing
  sizing regression.

### US-004 implementation — 2026-09-22

- Added frozen `SearchTargets` and the module-owned configuration error. Both
  integer ranges use `pipeline.config_bounds.bounded_int`, are validated at every
  boot and are shipped at the previous 1000/5000 defaults. Lifespan publishes
  `app.state.search_targets`; the module-level fallback supports lifespan-free
  transports. The handler passes both targets to the pipeline; first-token stays
  log-only, never a deadline or counter comparison.
- Added the strict-overrun count and whole-loop high-water mark at their two
  specified sites, including the Protocol, null sink, plain class, response model,
  handler dict, shared fake and literal pins. The existing Protocol already had
  no field count. Found one older `_SearchCounters` test double that predated the
  shared fake; migrated its nine uses to `RecordingSearchMetrics` rather than
  growing another incomplete sink. Extended the provider-tail order guard to
  preserve its two fields immediately before the two newly appended metrics.
- Regressions exercise both inclusive ranges, bad types and out-of-range values,
  `5` refusing real boot on both configured provider choices, a 150 ms classifier
  at target 100, a faster second request preserving the maximum, and separate
  default/5000 boots for no-overrun counts. Deterministic clock cases pin equality
  versus strict overrun, rounding before integer truncation, once-per-request
  counting over three results, first-token's log-only behavior, served-empty
  measurement and an exhausted-provider 422 touching neither timing site.
  Descriptions pin the whole-loop window, `num_results` comparability and
  per-process/never-reset/read-with-count semantics.
- **R36:** appended the `* ``1.3.0`` — …` continuation and ran the exporter.
  Regenerated the held golden via `_SCHEMA_MODELS`; it is byte-identical, sha256
  `69eb2dd5b480274282763a1616d13d9805964b98514efcaef57dfe2f54d551cf`.
  **Nothing appended to `_EXPECTED_ONE_THREE_ZERO_DIFF`**: the search metrics
  model is outside `_SCHEMA_MODELS`. The metrics-specific class/model/wire/order
  and description tests own these additions. All four anchor pages now quote
  `9860c4d988295f39ee9e31ac65414dd1ce2c89c1031cd45c778b7fa8142923e4`;
  exporter `--check` passes. Older goldens and every search pin remain untouched.
- **Rotation 36:** clean starting commit
  `7087c04d4b288555bf382df1853beede6542e0e5`, with empty `git status --short`.
  Only `orchestrator.py` and `contract.py` change among nine hashed sources.
  The live derivation, with whole-file read-only reversals, gives
  `bf5a1f3e55aad4e2748e66d3a2e9554b7950a38cadc89d4551cc1b6820f3e75d`;
  orchestrator-only reversal gives
  `66b5098504ec86a7492eebad8d87ccf5b9e19f898bfb6981c6da9ff1d5e4c074`;
  contract-only reversal gives
  `3c6998608786777a0c6c91130fa7c0da35226c9f16961373b371e6f2cf664186`;
  both-reverted reproduces
  `d9db75863ea8a464147da8b38c9fc6b8772cf75c485f58cab896e8130c81b6e0`.
  All four values agree under default, shipped and maximum-target config.
  Recorded at all five required sites; no text-sanitization behavior or hash
  input changes. The consumer handoff is in `docs/bootstrap-notes.md`.
- **R39 / registry:** both operator references, known-key registry, AST reader
  module/caller list and test mapping are updated. Monitoring covers the
  count-first sizing rule, never-reset max, classification-wait-timeout signal,
  boot error and counter-or-gauge extension checklist. US-003 still owns the
  forward-referenced Sizing the container section. Required exact-scope greps
  returned zero hits for the old constants, `_bounded_` in the new module, the
  fixed-latency prose, wait-budget multiplication and the wrongly named max.
- **Evidence:** after changed-file safe Ruff fixes/formatting, all **1,803 related
  tests passed**, including the unchanged wire pins, golden, exporter, governance,
  metrics and admission/classification regressions. Three existing non-failing
  warnings remain (Torch deprecation and two hermetic socket-denial cases).
  Strict Pyright reports zero errors; changed-file Ruff, exporter `--check` and
  `git diff --check` pass. Full suite not run, as this invocation forbids it.
  Repository Ruff still reports five inherited E501 findings in
  `pipeline/bounded_body.py:53` and
  `tests/test_search_providers.py:762,764,784,787`; formatting names those same
  two files. Both failures were reproduced from the starting commit's bytes;
  neither file was edited. Result is partial / needs-work for these outstanding
  gates, not a known functional defect. No dependency change, branch switch,
  Poppy edit, push, tag or release occurred.

### US-002 implementation — 2026-09-22

- Both fragments now carry identical CPU/memory substitutions and a
  body-discarding curl liveness probe (30 s interval, 5 s timeout, three
  retries, 30 s start period). Parsed baseline comparisons confirm that no
  other key or value changed; no config bind mount or third fragment was
  added. Unrelated housekeeping comments and published image pins remain.
  The comments distinguish liveness from body health and classifier readiness,
  reject health-based activation/start ordering, explain that restart reacts
  to exits, and state the cache reconnect cost and failure-mode-dependent
  zero-traffic WARNING/counter baseline.
- Added the second render step immediately after the original in required
  `lint`, with its own envelope settings; the original environment block is
  unchanged. CI guards pin both settings and step placement, both fragment
  commands and failure propagation. The placeholder guard checks both steps:
  envelope values must match size syntax, every other value must advertise
  its placeholder status. `TestResourceEnvelope` pins values, the full probe,
  mount-free config delivery and comment semantics; the duplication guard
  compares both strings and the JSON-projected healthcheck.
- **Secret-free Compose evidence:** `Docker Compose version
  v2.40.3-desktop.1`. Each render ran against copied fragments in a scratch
  project directory, with `--env-file /dev/null`, a scrubbed environment and
  the complete prescribed placeholder set. Only the requested filtered lines
  were emitted; no raw render or environment block was persisted.

  `minimal`, defaults:

  ```text
      healthcheck:
        test:
        timeout: 5s
        interval: 30s
        retries: 3
        start_period: 30s
      mem_limit: "1073741824"
  ```

  `full`, defaults:

  ```text
      healthcheck:
        test:
        timeout: 5s
        interval: 30s
        retries: 3
        start_period: 30s
      mem_limit: "1073741824"
  ```

  `minimal`, configured:

  ```text
      cpus: 4
      healthcheck:
        test:
        timeout: 5s
        interval: 30s
        retries: 3
        start_period: 30s
      mem_limit: "4294967296"
  ```

  `full`, configured:

  ```text
      cpus: 4
      healthcheck:
        test:
        timeout: 5s
        interval: 30s
        retries: 3
        start_period: 30s
      mem_limit: "4294967296"
  ```

  The started, secret-free BusyBox probe was inspected while running:
  `HostConfig.NanoCpus=0` at defaults and `4000000000` when configured.
  Its isolated container and network were removed afterwards. Both CI render
  branches and explicit zero also parse; invalid memory syntax is rejected
  before startup. Service-level `cpus` requires Compose v2 (Compose Spec).
- **R36:** corrected both rendered health descriptions plus the acquisition
  comments, smoke docstring, startup-test docstring and prescribed API guide
  sentence. App, fetcher, smoke and startup-test ASTs are unchanged after
  stripping docstrings. Appended the held 1.3.0 continuation, ran the exporter,
  and regenerated the live golden through `_SCHEMA_MODELS`. The golden
  **really changes**, only at `HealthResponse.description`, to sha256
  `f74a99b97e088982665e726a9e011e2955e53c4f3c39f1d05754b7a6dc7526eb`.
  `_EXPECTED_ONE_THREE_ZERO_DIFF` was reviewed: **no entry added**, because
  `_added_paths` records fields/enum members, not changed description values.
  OpenAPI changes exactly the model and route descriptions; the generated
  anchor is `c9cd19bad84decd7415ba912ae81c826447f2a19edc41b57c857d4a7b4d42ab2`.
  All four anchor pages are refreshed and exporter `--check` passes.
  Historical goldens, release anchors and response shapes remain unchanged;
  no further version bump inside the unpublished window.
- **Rotation 37:** clean starting commit
  `2aa6356a23afdc41d3078a17efc52a9a842e50d6`. Only `pipeline/contract.py`
  moves among the nine hashed sources; the hash definition is unchanged.
  Default and shipped config both derive
  `4913fdc1982cb48ba2db9c6972fcea10107408349970c45c9dc6b3ae5c1aa1fb`.
  Substituting the entire baseline contract file through read-only
  `Path.read_bytes` interception reproduces the before value exactly:
  `bf5a1f3e55aad4e2748e66d3a2e9554b7950a38cadc89d4551cc1b6820f3e75d`.
  Recorded at all five rotation sites; no text-sanitization behavior changed.
  Consumer handoff is in `docs/bootstrap-notes.md`; US-003 owns the remaining
  sizing/monitoring/operator sweep, not this story.
- **Evidence / outstanding gates:** 1,449 related tests and 66 governance
  checks passed, with three existing non-failing warnings. Strict Pyright,
  changed-file Ruff lint/format, contract drift and whitespace checks pass.
  Both exact-scope acceptance greps return zero hits; the first grep attempt
  found one stale ignored pytest bytecode file, removed before repeating.
  Full suite not run, as this invocation explicitly prohibits it.
  Repository Ruff still fails on five inherited E501 findings in
  `pipeline/bounded_body.py:53` and
  `tests/test_search_providers.py:762,764,784,787`, and formatting names those
  same two files. Both failures reproduce from the starting commit; those
  files are byte-identical and untouched. Result remains partial / needs-work
  for these gates, not a known story defect. No dependency change, branch
  switch, Poppy edit, push, tag or release occurred.

## Refinement Notes

### Research Findings

**Decision:** Env-substituted compose values whose defaults are today's state (`cpus` 0 = key omitted,
`mem_limit` 1024m), not a second fragment per size, not an override file and not a newly imposed CPU
cap.
**Rationale:** Neither fragment declares `cpus` today, so any non-zero default would be a new hard
cap on every operator who pulls the fragment — the exact incident this spec exists to fix. Compose
v2.40.3 renders `${FORAGE_CPUS:-0}` by omitting the key (measured in validation round 1), so no
fallback branch is needed and none is kept. `${VAR:-default}` keeps one file per mode and makes an
unset `.env` identical to today.
**Alternatives considered:** `cpus: ${FORAGE_CPUS:-1}` — a new cap sold as a preserved default;
`deploy.resources.limits` — Swarm-flavoured; a third fragment or a `compose/envelope.yml` override —
drift, and it would invalidate the parity tests.
**Source:** `compose/minimal.yml:105-110`, `compose/full.yml:80`; the round-1 render measurement.

**Decision:** Count latency-target overruns on `/metrics` with a per-process high-water mark, rather
than fixing logging.
**Rationale:** The WARNING at `orchestrator.py:1082-1092` renders but carries its data in `extra=`,
which does not (GOTCHAS `:130-143`); a counter is the observability floor the search epic
established, and a bare count cannot distinguish 1,001 ms from 30,000 ms. The mark never resets;
the runbook therefore keys on the count-over-requests ratio first and says so.
**Alternatives considered:** `logging.basicConfig` at startup — a separate decision (BACKLOG
"Structured request logging"); a windowed max — more state for a signal the ratio already gives.
**Source:** `pipeline/orchestrator.py:586-587,1063-1092`; `retrieval_app.py:488-535,875-886`.

**Decision:** `promptguard_threads` defaults to 0, is owned by `promptguard/classifier.py`, is applied
via a classifier attribute in a nested guarded step, and pins the tokenizer pool alongside torch.
**Rationale:** No thread setting exists anywhere (grep confirmed, `TOKENIZERS_PARALLELISM` and
`RAYON_NUM_THREADS` included); pinning to 1 would be a per-classify regression on every uncapped
multi-core host, so the default must be inert. The lifespan never calls `load()` —
`model_fetcher._load_verified` does — so an attribute on the classifier object is the only seam that
leaves `acquire_and_load`'s "not a configuration surface" decision intact. The fast tokenizer's Rust
pool is a second thread-spawning surface the motivating incident would still hit.
**Alternatives considered:** `OMP_NUM_THREADS` in the Dockerfile — an env default the operator would
have to know; threading a kwarg through five `model_fetcher` seams — reverses a recorded decision;
`os.sched_getaffinity` as the default — cgroup v1/v2 semantics make it approximate; `RAYON_NUM_THREADS`
— one chunk per call makes it moot; deferred with the reason.
**Source:** cutover finding 2026-09-12; `promptguard/classifier.py:50-115`; `model_fetcher.py:
1060-1077,1474-1495,1530-1558`; `retrieval_app.py:1322-1324`.

**Decision:** `classification_concurrency` widens under a memory rule whose 384 MiB term is the pypdf
child's `RLIMIT_AS` and whose classifier term is a named, provisional coefficient — with an advisory
boot WARNING read from the cgroup.
**Rationale:** `docs/configuration.md:488-489` pins it "for the same reason" as
`extraction_concurrency` — the 512 MiB parent reservation; twelve sites in the repo say the 384 MiB
is the child's address space, none describes a classifier working set (round-1 salty review). The
semaphore is the only backpressure on PromptGuard inference, and an overrun is an OOM kill `/health`
cannot report — the one failure class invariant 5 exists for — so the rule is checked at boot with
`_cgroup_memory_snapshot()`, which already exists.
**Source:** `pipeline/extraction_limits.py:16,45`; `config.yaml:39-41`; `compose/minimal.yml:105-109`;
`docs/configuration.md:453,485,488-489`; `retrieval_app.py:1010-1028`; `SECURITY.md:180-197`.

**Decision:** The healthcheck uses the `curl` the image already installs, with `-o /dev/null`, is
documented as liveness that must never gate anything, and the two contract-rendered `/health`
docstrings that describe the old probe are corrected inside the window.
**Rationale:** `Dockerfile:48` says "curl for the container healthcheck" and no fragment ever added
one; `-o /dev/null` keeps the probe from capturing `/health` bodies into `docker inspect`; a
`healthy` container may run with the classifier unloaded (invariant 5). The docstrings at
`retrieval_app.py:358` and `:1449` render into `contract/openapi.yaml`, so leaving them would ship a
frozen document describing a probe that no longer exists.
**Source:** `Dockerfile:44,48,67`; `contract/openapi.yaml:423,1417`; `contract/GOVERNANCE.md`
standing example 3.

**Decision:** The render check is mechanically secret-free and half of it is CI's.
**Rationale:** `env -i` does not stop Compose loading `compose/.env` from the project directory, and
`compose/full.yml:54-60` passes through `FORAGE_BRAVE_API_KEY`; `--env-file /dev/null`, a scratch
project directory, a complete placeholder set and a grep filter make a leak impossible rather than
merely avoided. The required `lint` job already renders both fragments with a placeholder on every
commit (`.github/workflows/ci.yml:201-239`), which is the standing acceptance proof; the manual step
only establishes what `0` means.
**Source:** `.github/workflows/ci.yml:201-239`; `tests/test_ci_workflow.py:752-800`; `compose/full.yml:
54-60`; `.gitignore:32-35`.

### Scope Adjustments

- The classify-latency benchmark moved to spec 7 US-003/US-004 (needs weights and an owner gate);
  this spec only provides the knobs the benchmark varies, the table it fills, and the provisional
  coefficient it replaces.
- Validation round 1: US-001 split into US-001 (threads, concurrency) and US-004 (latency targets,
  counter, max); `/search` semaphore parity moved to spec 2; `cpus` default changed from `1` to `0`;
  `promptguard_threads` default changed from `1` to `0`; the high-water mark added.
- Validation round 2: the memory rule corrected (384 MiB = pypdf child; provisional classifier
  coefficient; boot WARNING); the tokenizer pool pinned with torch; `app.state.promptguard_threads`
  dropped; the Compose render question closed and the override-file fallback deleted; the render made
  secret-free (`--env-file /dev/null`, scratch directory, full placeholder set, grep filter) and CI
  cited; the healthcheck's two rendered docstrings given a regenerate inside US-002 (a second
  rotation); the sizing prose moved from US-002 to US-003; the by-value sweeps (R39) replaced the
  hand-typed lists; `classification_concurrency` treated as a widened nested key, not a new top-level
  one; US-004's Independent Test aligned with its own range; the two increment sites separated; the
  per-process semantics of the max documented; the config delivery path written down.
- Validation round 3: the memory rule's double count removed and a `cache.max_bytes` term added
  (992 MiB at the shipped defaults, strictly-below, equality silent); the provisional coefficient
  set to 64 MiB with its derivation and the larger-measurement consequence decided; the high-water
  mark renamed `search.sanitization_latency_max_ms` with both windows described; the literal
  `SearchMetricsResponse` pin added as a sixth site; CI given a second render step; the `NanoCpus`
  check made secret-free; the `HealthResponse` golden movement corrected; the "latency, not
  sanitization" claim corrected against spec 2 US-006 and a Security Considerations section added;
  the `:374` restatement made verbatim and the five omissions named in Out of Scope; the
  replace-not-merge warning extended to the security-relevant keys with a pinning test; no story
  split (R37).
- Validation round 4: the memory rule's cache term made conditional on the in-memory backend and its
  child term read from configuration; the check placed after the backend selection; the "only tighten"
  invariant corrected to three raisable keys and `SECURITY.md:182` with it; the runbook re-keyed on
  `search.classification_wait_timeouts`; US-002's grep scoped past the retained goldens and its Docker
  requirement made a verification note; the envelope keys added to the bind-mount warning; the
  reconnect-counter effect of the probe documented; every grep scoped (R43); no story split (R37).

### Decisions Made

- Today's state is the default in every knob; this spec changes no default, no runtime behaviour and
  no wire byte for an unchanged deployment.
- Liveness (compose healthcheck) and health (`/health` body) are kept distinct on purpose, and the
  docs say what `healthy` does not mean.
- Runtime tunables stay `config.yaml` (precedent `search_brave_*`, `promptguard_threshold`); the
  vision's "overridable by env" is met at the compose layer (`FORAGE_CPUS`, `FORAGE_MEM_LIMIT`) and by
  a documented bind mount for the rest; no `FORAGE_*` override for `promptguard_threads` (round-1
  completionist vision-alignment finding, recorded).
- `search_first_token_target_ms` stays a key per ruling 16 but is documented as log-only; four
  reviewers proposed dropping it — recorded, overruled by the ruling, mitigated by the doc sentence.
- Round 5 (R16 corrected): **reversed** — no new bounded-int copies. Spec 2 US-001 made
  `pipeline/config_bounds.bounded_int` the shared helper and spec 5 US-001 already reads through it;
  `promptguard/classifier.py`'s thread reader and `pipeline/search_targets.py` do the same and both
  join spec 3 US-003's AST reader list (round-4 codebase-fit critical). The count of module-local
  copies stays at two after spec 2 (`cache.py`, `brave.py`).
- Round 5 (R16 corrected): the thread reader's error is `PromptGuardThreadsConfigurationError` —
  named for what it bounds, the in-house `PromptGuard<What>Error` shape — because spec 7 (which runs
  after this spec) declares `PromptGuardConfigurationError` for its settings triple and this story can
  neither import a class that does not exist yet nor declare a second one of that name.
- Round 5 (R16 corrected): the parent term is `PARENT_RESERVATION_BYTES` plus the selected model's
  `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` row, so the rule is model-dependent and an 86M selection
  is never silent; the Valkey branch counts one in-flight read (`cache.max_value_bytes`, spec 4) rather
  than zero; the WARNING field is `cache_term_bytes`, named for the term.
- Round 5: the `SECURITY.md:374` restatement stays verbatim and the healthcheck/CPU-quota grep excludes
  its one true phrase by content (`grep -v 'unless the operator sets one'`); the reconnect WARNING
  rate is stated per failure mode (one per probe refused, about one per two timing out); the partition
  test holds dotted registry names with a written membership rule, `extraction.child_address_space_bytes`
  in and `extraction.admission_queue_depth` out; the runbook heading covers a gauge; the `4 / 4 GB`
  row is marked at the `cache.max_bytes` ceiling; `API_GUIDE.md:93` gets prescribed wording;
  `pipeline/search_targets.py` gets a `test_mapping` row; spec 5 US-005's closed `_PINNED_COUNTERS`
  projection is why "wire pins pass unchanged" holds and the literal field-set pin is the test that
  moves.
- Overruled: `os.sched_getaffinity` as the threads default — approximate under cgroup v1; deferred.
- Overruled: an image-level `HEALTHCHECK` — out of scope, stated.
- Overruled: the `compose/envelope.yml` fallback — the render question is closed; a third file would
  have been drift and would have invalidated the parity tests.
- Overruled (round-1 salty): a windowed or resettable latency maximum — the per-process mark is
  documented as such and the runbook keys on the ratio; a reset would be a new surface.
- The memory-rule check is a WARNING, never a refusal: `None` cgroups and deliberate over-subscription
  stay supported, and the coefficient is provisional.
- No `config.yaml` volume line in the fragments, commented or not: a missing host path mounts a
  directory and kills the boot; the sizing section carries the real snippet with the warnings.
- Round 3 (R16 corrected): the memory rule is `512 MiB (parent, no classification in flight) + C × 64
  MiB + E × 384 MiB + cache.max_bytes`, strictly-below triggers the WARNING, equality is silent, and
  the shipped defaults evaluate to 992 MiB — a margin the test asserts. The 64 MiB is the reference
  residual with a 32 MiB margin and has no measurement behind it. If spec 7 measures more than
  96 MiB, the shipped `FORAGE_MEM_LIMIT` default stays `1024m`, the reference row's recommendation
  rises, and 1 GiB hosts get the advisory WARNING — accepted, because the honest signal beats a
  silently moved default.
- Round 3: the high-water mark is `search.sanitization_latency_max_ms` — the timer wraps the whole
  per-result loop, structural scan and semaphore wait included — while the counter keeps
  `search.promptguard_latency_target_exceeded`, the name the target key and the log line already use;
  both descriptions state the window.
- Round 3: CI renders the envelope branch in a second step with its own `env:` because the
  placeholder test requires "placeholder" in every value of the first; the placeholder test
  allow-lists exactly the two envelope names.
- Round 3: the `NanoCpus` check runs against a `busybox` service, not the Forage image, so no secret
  is needed to establish what `0` means.
- Round 3: the healthcheck and the two docstring corrections stay in US-002 — inseparable, one
  rotation; the round-2 proposal to split the regenerate into its own story is overruled (R37).
- Round 3: "performance knobs change latency, not sanitization output" is replaced — under spec 2
  US-006 latency decides whether a fail-open request is scanned; the keys stay out of
  `derive_sanitizer_revision` (rules, not throughput) and the docs say the consequence.
- Round 3: `SECURITY.md:374` is restated verbatim with `cpus` added and the five other omissions
  kept and named in Out of Scope, not dropped.
- Overruled (R37 corrected): the round-2 proposals to split US-003's sweeps into a separate story
  and to split US-002's contract regenerate — already litigated; single concern each at size L.
- Overruled (round-2 salty): a windowed latency maximum, again — the ratio-first runbook stands.
- Overruled (round-2 second opinion): dropping the healthcheck to avoid the rotation — the
  `Dockerfile` installs `curl` for it, the docs already claim one, and the rotation is one measured
  line.
- Round 4 (R16 corrected): the memory rule is `512 MiB + C × 64 MiB + E × child_address_space_bytes
  (as configured) + (cache.max_bytes if the backend is memory, else 0)` — 992 MiB at the shipped
  defaults on `minimal.yml`, 960 MiB under `full.yml`'s Valkey; the WARNING names the backend it
  counted; the check is a new lifespan call after `_select_cache_storage` (`retrieval_app.py:1284`),
  the first point where all four operands exist, in the shape of the break-glass WARNING helper.
- Round 4 (R16 corrected): the "only tighten" preamble, `TROUBLESHOOTING.md:524`, `DECISIONS.md:105`
  and `SECURITY.md:182` name every key that can be raised — `classification_concurrency`,
  `child_address_space_bytes`, `admission_queue_depth` — because "the one key" was false against
  `pipeline/extraction_limits.py:25-27` and the falsehood was a sandbox ceiling. Overruled (round-3
  security review, the test branch of its own either/or): a range cross-check test over
  `ENV_REFERENCE.md`'s table — the grep is strengthened to reject any single-carve-out line instead;
  the table's ranges are prose and the registry a future bounds test would hang off is spec 3's.
- Round 4 (R16 corrected): the runbook's second sentence is keyed on spec 2's
  `search.classification_wait_timeouts`; the max is whole-loop wall time scaled by `num_results` and is
  never compared to `promptguard_wait_seconds` (the round-3 "approaching `promptguard_wait_seconds ×
  1000`" sentence compared one wait to a sum); the no-overrun counter case runs from its own 5000 ms
  boot.
- Round 4 (R16 corrected): US-002's Docker need is a verification note — `docker compose config` is a
  client-side render and needs no daemon; only the `busybox` probe does, and its not-run outcome is
  stated. Overruled (round-3 salty review): declaring US-002 an owner gate in `worktree.yaml` — the
  hermetic half and the CI render steps are the acceptance, and the probe is evidence for one word.
- Round 4: the bind-mount warning names the four envelope keys and says that nothing but the warning
  protects the operator's own baseline. Overruled (round-3 security review, its "cheaper" alternative):
  logging the effective security-relevant and envelope values at boot — INFO never renders (GOTCHAS)
  and a WARNING for ordinary values is noise; the honest sentence and the partitioned pinning test are
  the mitigation.
- Round 4: the CPU-side boot WARNING stays out of scope, with the fail-stop / fail-open ordering
  recorded as the reason it is a follow-up rather than reportability alone (round-3 security review).
- Round 4: the placeholder test checks the two envelope names against size-shape regexes rather than
  exempting them, so no compose-step value is ever unchecked (round-3 security review).

## Clarifications

### Session 2026-09-19
- Q: Configurable envelope or a self-specced bump to the owner's host? → A: Configurable; sized to
  the host, not the deployment (owner, 2026-09-12; ruling 16).
- Q: Where do the latency targets live? → A: `config.yaml` keys with today's defaults; overruns
  counted on `/metrics` (ruling 16).
- Q: Does this spec pick sizes? → A: No; it documents the reference envelope and two larger rows,
  and spec 7's benchmark fills the classify-latency column.

### Session 2026-09-19 (validation round 1)
- Rulings applied: R16 (`cpus` 0; `mem_limit` default kept; `promptguard_threads` 0; concurrency
  1–8 under a memory rule; `/search` semaphore parity moved to spec 2; healthcheck as liveness), R31,
  R33, R32, R34, ruling 5, ruling 12.

### Session 2026-09-19 (validation round 2)
- Rulings applied: R16 (corrected — memory rule terms, provisional coefficient, boot WARNING; Compose
  omits `cpus` at 0, fallback deleted; `TOKENIZERS_PARALLELISM`; the rendered docstrings regenerated
  in US-002; CI's render step cited; the real config delivery path), R33 (corrected — `--env-file
  /dev/null`, scratch directory, complete placeholder set, grep filter), R36 (window block verbatim on
  US-004 and US-002), R39 (sweeps by grep), R40 (test seams named), R32 (five-site records), ruling 6.
- Q: Is the 384 MiB in the memory rule a classifier figure? → A: No — it is the pypdf child's
  `RLIMIT_AS`; the classifier term is a provisional coefficient until spec 7 measures it (128 MiB
  in round 2; 64 MiB after round 3's double-count correction).
- Q: Does Compose accept `cpus: ${FORAGE_CPUS:-0}`? → A: Yes; it omits the key at 0 (v2.40.3) and the
  `lint` job renders both fragments on every commit.
- Q: Where does `promptguard_threads` live for an operator on the published fragments? → A: A
  bind-mounted copy of the shipped `config.yaml`; no env override (recorded).

### Session 2026-09-19 (validation round 3)
- Rulings applied: R16 (corrected — four-term memory rule counted once, `cache.max_bytes`, 64 MiB
  provisional coefficient with derivation, strictly-below with equality silent, the shipped default
  never moves on a measurement, `search.sanitization_latency_max_ms`, second CI render step,
  `busybox` `NanoCpus` probe, `HealthResponse` golden movement), R33 (corrected — secret-free `NanoCpus`),
  R36 (window block verbatim; the four anchor pages named; `docs/releases.md:23` historical), R37
  (corrected — no further splits), R39, R40, R41, R32, ruling 6.
- Q: Does the reference row land exactly on 1024 MiB? → A: No — 992 MiB; round 2 double-counted the
  first classification. The margin is asserted by test.
- Q: What if spec 7 measures a working set above the 96 MiB residual? → A: The shipped default stays;
  the reference row's recommendation rises; 1 GiB hosts get the advisory WARNING.
- Q: Why rename only the max? → A: The timer wraps the whole per-result loop, which the max reports;
  the counter compares against a key already named `search_promptguard_latency_target_ms`, and
  renaming the key would be a config-surface change with no benefit.
- Q: Does a description change move the golden? → A: For `HealthResponse`, yes (it is in
  `_SCHEMA_MODELS`); the diff list records added keys only, so it gains no entry.
- Q: Are the knobs security-neutral? → A: No — under spec 2 US-006 latency decides whether a fail-open
  request is scanned; recorded in Security Considerations and the sizing section.

### Session 2026-09-19 (validation round 4)
- Rulings applied: R16 (corrected — the cache term only under the in-memory backend, `VALKEY_URL`
  unset; the child term read from `extraction.child_address_space_bytes`; the "only tighten" sentence
  and `SECURITY.md:182` naming `classification_concurrency`, `child_address_space_bytes` and
  `admission_queue_depth`; the runbook keyed on `search.classification_wait_timeouts`; the memory check
  in the lifespan after the backend selection that follows `cache_settings_from_config`; US-002's grep
  excluding `tests/golden/` and naming `retrieval_app.py:1307` and `model_fetcher.py:62`; the sweeps
  excluding `kit_tools/.seed_cache/`; the four envelope keys in the replace-not-merge warning;
  `MONITORING.md:174,342` in the sweep; `docker compose config` needs no daemon — verified against
  Compose v2's client-side render — and the `NanoCpus` probe is a verification note), R36 (corrected —
  US-004 and US-002 both append nothing to `_EXPECTED_ONE_THREE_ZERO_DIFF`; the gate named), R43
  (every grep scoped to an exact path set, `kit_tools/specs/`, `kit_tools/.seed_cache/`, `tests/golden/`
  and the result artefacts excluded, and executed — five `only tighten` / `never raise` hits, eleven
  healthcheck-sentence hits), R41 (the 1184 / 1152 / 1120 / 1088 / 992 / 960 MiB arithmetic stated per
  case), R37 (corrected — no further splits), ruling 6.
- Q: Does `cache.max_bytes` always count? → A: Only when the in-memory backend is selected; under
  Valkey the term is 0 and the WARNING says which backend it counted.
- Q: Is 384 MiB a constant in the rule? → A: No — the rule reads the configured
  `child_address_space_bytes`; 384 MiB is the shipped default in the worked arithmetic only.
- Q: Which keys can `config.yaml` raise above shipped? → A: `classification_concurrency` (this spec),
  `child_address_space_bytes` and `admission_queue_depth` (already today); every "only tighten" /
  "never raise" sentence says so.
- Q: What does the latency max tell an operator about `promptguard_wait_seconds`? → A: Nothing
  directly — it is whole-loop wall time; spec 2's `search.classification_wait_timeouts` is the signal.
- Q: Does US-002 need Docker? → A: The render does not (`docker compose config` is client-side); the
  `busybox` probe does, and its not-run outcome is written down.

### Session 2026-09-19 (validation round 5, final)
- Rulings applied (this pass was not re-reviewed; the round-4 salty and codebase-fit findings were
  applied directly and the remainder recorded under Known risks): R16 (corrected — no new bounded-int
  copies: `promptguard/classifier.py`'s thread reader and `pipeline/search_targets.py` on
  `pipeline/config_bounds`, both added to spec 3's AST reader list, `pipeline/search_targets.py` with
  a `test_mapping` row; the thread reader's error named `PromptGuardThreadsConfigurationError` because
  spec 7's `PromptGuardConfigurationError` does not exist when this spec runs; the memory rule's parent
  term as `PARENT_RESERVATION_BYTES` plus the selected model's measured resident delta read from a
  per-model table the sizing section carries, so an 86M selection is not silent; the Valkey branch's
  one-read term `cache.max_value_bytes` that spec 4 promised; the `SECURITY.md:374` grep reconciled by
  excluding the verbatim line's phrase by content; the reconnect WARNING rate "one per probe on
  connection refused, about one per two on a connect timeout"), R43 (the new greps scoped and
  executed).
- Q: Can this spec import spec 7's `PromptGuardConfigurationError`? → A: No — spec 7 runs after this
  spec; the reader's error is named for what it bounds and the generic name stays spec 7's.
- Q: Where do a bigger model's weights land in the rule? → A: In the parent term, as the selected
  model's resident delta over the 22M — shared weights are not per-classification; spec 7 measures and
  fills the row and routes the selected id in.
- Q: Does spec 6 US-004 move spec 5 US-005's pins? → A: No — the pins project a closed counter set;
  the literal field-set pin in `tests/test_contract_schema.py` is the test that moves.

## Open Questions

- [ ] Should `FORAGE_CPUS` also drive `extraction.extraction_concurrency` (the PDF worker admission
      slot)? Non-blocking; default no — the worker is memory-bound (`child_address_space_bytes`).
- [ ] Whether the provisional 64 MiB classifier coefficient survives spec 7's measurement on the 22M
      and the 86M — non-blocking; the rule's structure does not change either way, and the
      consequence of a larger value is already decided (US-001: the shipped default stays, the
      reference row's recommendation rises, 1 GiB hosts warn).
- [ ] The model-dependent parent term's handoff (non-blocking, but it must belong to somebody): spec 7
      US-004 fills `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL`'s 86M row from its idle-RSS measurement
      and the sizing table's per-model column, and spec 7 US-006 routes the selected `FORAGE_MODEL_ID`
      into the rule — written here at close-out; spec 7's text must carry the same handoff (Known
      risks).
- [ ] **The admission controller's handoff leaks a slot on a racing cancellation** (recorded by
      `hardening-retrieve-parity` US-002; flagged for the epic wrapper). `release()` hands the slot
      to a popped waiter without decrementing `_active`; a waiter cancelled while queued or after
      its grant, racing a release, leaves `active == limit` with nobody holding a slot — at
      `retrieve.fetch_concurrency: 1` that wedges `/retrieve` for the life of the process. Reachable
      today only by task cancellation (server shutdown); no timer wraps `acquire()`. Fix direction:
      make the handoff idempotent (`release()` always decrements, the woken waiter re-increments
      under the lock). The envelope work that sizes the slot and adds `--limit-concurrency` is the
      natural owner. Full description: `kit_tools/docs/GOTCHAS.md`.

## Known risks (validation close-out)

Round-4 warnings not applied in the final pass, recorded per the round-5 rulings (reviewer, finding,
why deferred), plus the cross-spec handoffs this pass created:

- **Salty engineer, US-003 — the negative healthcheck claim is guarded by a one-time grep, not a
  test.** The reviewer asked for an assertion in `tests/test_compose_fragments.py` beside
  `_DOCUMENTED_VOLUME_RE` that `MONITORING.md` / `SERVICE_MAP.md` / `INFRA_ARCH.md` carry no surviving
  "no `healthcheck:` in `compose/*.yml`" sentence while the fragments declare one. Deferred: US-003 is
  doc-only by design (no test criteria on doc-only stories) and US-002, which does edit that test
  module, runs before the sweep, so the guard would be red at US-002. The one hit the grep misses
  (`INFRA_ARCH.md:348`) is on US-003's hand list. Risk: the claim can rot after the epic; a follow-up
  can add the guard once both stories have landed.
- **Cross-spec handoff, US-001 → spec 7 US-004 / US-006 (the model-dependent parent term).** This spec
  ships `CLASSIFIER_RESIDENT_DELTA_BYTES_BY_MODEL` with the 22M row only; spec 7 must fill the 86M row
  from its measurement, add the sizing table's per-model column, and route the selected
  `FORAGE_MODEL_ID` into the rule. Spec 7's text today says only that its marginal RSS delta replaces
  the working-set coefficient (`feature-hardening-promptguard-86m.md:1205-1208`) and cites the sizing
  table as "spec 6 US-002" (it is US-003's); both need the matching edit (flagged at close-out).
- **Cross-spec observation, spec 7 (not this spec's to fix).** Spec 7's settings triple re-states the
  bounded-read idiom locally in `pipeline/stage3_promptguard.py` (`:652-658`), which is the module-local
  copy R16 (corrected in round 5) removes here; if spec 7 keeps it, the copy count grows to three
  again. Recorded so the parent can relay it.
