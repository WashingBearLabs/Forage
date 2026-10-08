<!-- Template Version: 2.1.0 -->
# TESTING_GUIDE.md

> Last updated: 2026-09-23
> Updated by: Copilot (hardening-release US-004)

## Quick Start

```bash
uv run pytest
uv run ruff check .
```

Full set of commands:

```bash
# Environment (once per checkout / worktree)
uv sync --extra dev

# Everything
uv run pytest

# One module
uv run pytest tests/test_cache.py -q

# One test
uv run pytest tests/test_orchestrator.py -q -k test_search_returns_sanitized_results

# Static analysis
uv run ruff check .          # CI gate: must be clean
uv run ruff format --check . # CI gate since US-001: must be clean
uv run pyright               # CI gate since US-006: strict, zero errors
```

All four are blocking jobs in `.github/workflows/ci.yml` (`lint`, `typecheck` and, since
US-002, `test`). Run them before you push — a red gate costs a round trip on the free
Actions tier.

**Ten jobs, in two lanes.** The *service* lane runs `lint`, `typecheck`, `test`,
`build-amd64` and `secret-grep` (US-003) and `smoke` (US-005) on every push and PR, plus
`publish` (US-007) on a push to `main` or a `v*` tag, which `needs:` all six — see
[`docs/releases.md`](../../docs/releases.md) for the tag scheme and what a green publish
does and does not prove. The *companion* lane (US-004) is `searxng-build`,
`searxng-smoke` and `searxng-publish`, keyed off `searxng-v*` tags and documented in
[`docs/searxng.md`](../../docs/searxng.md); `searxng-publish` `needs:` its own two plus
`test`. The two lanes are mutually exclusive by ref prefix: a `searxng-v*` tag skips
every service-image job and a `v*` tag skips all three companion jobs, each asserted by
*evaluating* the job's own condition against both tag shapes.

`searxng-smoke` is worth knowing about separately: its blocking half stands the companion
image up beside a Valkey on a `--internal` Docker network (no egress at all) and runs four
phases through `searxng_smoke.py`, while a `--live` probe that reaches real engines runs
`continue-on-error: true` afterwards. Reproduce either with
`uv run python searxng_smoke.py --image forage-searxng:ci [--live]`.

`smoke` is the only service-lane job that *runs*
the image rather than inspecting it — it starts the built container with no Hugging Face
token and asserts the degraded `/health` contract through `contract_smoke.py`, under the
script's default `--expect-status degraded`. Its other mode, `--expect-status healthy`, is for a
container started with weights (the three PromptGuard-coupled checks invert and the wait
polls until `status` reads `healthy`); CI never runs it. You can run exactly what CI runs:

```bash
docker build -t forage:ci .
docker run -d --name forage-smoke -p 127.0.0.1:8020:8020 forage:ci   # loopback only -- no auth exists
uv run python contract_smoke.py --base-url http://127.0.0.1:8020
docker rm -f forage-smoke
```

Expect `status: "degraded"` with `promptguard_unavailable`. A weights-free image
reporting `healthy` is the nine-day production failure this repo exists not to repeat,
and the smoke is the mechanical guard against it.

**`test` is the job that makes the rest real.** Until it landed, nothing in CI ran pytest,
so `test_ci_workflow.py`, `test_pyright_policy.py`, `test_dependency_lock.py` and the
hermeticity canary all bit only on a developer's machine. The job runs `uv run pytest -q`
with no selection filters, preceded by a named `uv run pytest -q
tests/test_sanitizer_revision.py` step so the file-recall cache contract reports as its
own red line instead of as nine failures inside a full-suite log.

`pyright` runs with **no baseline and one carve-out**: `reportPrivateUsage` is off for
`tests/` and nothing else is relaxed anywhere. Type-ignore comments are disabled
(`enableTypeIgnoreComments = false`), so the only way to make pyright quiet is to make the
types right — or, for a genuine third-party gap, to add a minimal stub under `typings/`.
`tests/test_pyright_policy.py` fails if any of that drifts.

**Always go through `uv run`.** The lock pins the toolchain (ruff 0.16.6, pyright 1.1.411);
a system-installed tool reports different numbers and the backlog counts will not
reproduce. This is also why `kit_tools/worktree.yaml` sets `run_prefix: uv run` — without
it the detached orchestrator inherits the ambient PATH, pytest collection crashes, and the
crash reads as a false regression.

---

## Test Structure

**56 `test_*.py` modules** under `tests/`, flat, one per subject — 60 Python files in all
once `conftest.py`, `fakes.py`, `corpus_stage2.py` and `__init__.py` are counted — plus
`golden/`, `fixtures/` and `corpus/`. **5557 tests collected** on 2026-10-07 (`release-1-3-0` US-005; `epic-forage-structural-hardening`: 4860 -> 5389; before that, the 86M default split the shipped-envelope test into the 86M and 22M cases; the `/retrieve` bounded-decoder fix added nine before it; before that
4850, after the epic-wide validation fixes for `forage-injection-corpus`; `uv run pytest --collect-only -q`).
Every per-module row below is remeasured
from collection, not incremented from a previous story's count; their sum
equals the total. The complete local run on an owner checkout reports **5389 passed, 0 skipped,
no xfails**. On a clean checkout (and in CI) the four `tests/test_corpus_docs.py` findings checks
skip, because `kit_tools/AUDIT_FINDINGS.md` is gitignored by repo policy — PR #38's CI run
(`37231593223`, at 4845 collected) reported 4841 passed, 4 skipped. The `MIN_RECORDS` floor test
and the two `tests/test_corpus_record.py` owner-gate tests no longer skip (cassettes are committed);
fresh PR CI is still required. The former six strict raw-read xfails have
moved to `test_provider_transport.py`, where both real provider stacks drive a
network double that honors `read(max_bytes)`; all six now enforce the exact
upstream byte bound. Collection and scoped passes alone are not full-suite evidence.

The bounded-body and both provider rows were re-measured on 2026-09-22 during
`hardening-provider-bounds` US-003's retry. The helper includes ambiguous
wrapped/raw deflate, delayed rejection after output, EOF retry, raw-budget
exhaustion and exact-member checks across transport chunk boundaries.

Provider-bounds US-005 re-counted the orchestrator/provider/pin rows below:
353, 284, 145 and 7 tests respectively. Its 13-module related run passes
1,558 tests; the six pre-refactor captures are unchanged. At that story, the whole suite
was deferred to the authorized end-of-epic gate.

**US-005 retry validation (2026-09-22).** The explicit orchestrator-then-admission
pair passes **347 tests**; the combined threshold/story selection across sixteen
modules passes **1,196 tests in one process**. The inherited global-patch leak
is repaired: `_mock_retrieve_io` owns URL/fetch mocks once per test, outside all
concurrent `_retrieve_under` calls and their cleanup. Classification tests wait
for an entered event and a queued semaphore waiter, not a fixed number of
`sleep(0)` turns. Gates open and outstanding tasks are cancelled/drained in
`finally`, including deadline-test startup failures. Fixture teardown checks
both patched functions' original identities. Ruff lint/format, strict Pyright
and contract drift checks pass. That story left the full suite to the end-of-epic gate;
the story implementer did not run it.

| Module | Tests | Covers |
|--------|------:|--------|
| `tests/test_bench_promptguard.py` | 115 | Host-only `scripts/bench_promptguard.py`: seeded tokenizer-sized inputs checked against the real tiny tokenizer and classifier chunking; required single-input runs with first-POST ordering and separate fresh-service cold samples; null unselected fields; nearest-rank timing; healthy-and-loaded readiness; multipart fields; complete/partial fixed-key rows; configuration versus service failures and independent paired artifacts; cgroup and optional Docker memory; closed diagnostics and complete benchmark-config parity. Injected HTTP/command/clock seams, no Docker, network or weights. |
| `tests/test_bounded_body.py` | 107 | Raw and decoded byte ceilings, bounded gzip/zlib/raw-deflate outputs (including ambiguous headers and chunk-independent replay), encoding-first dispatch, path-specific length prechecks, exactly-one-member EOF validation, no-progress guard and fixed exception messages. No real waits. |
| `tests/test_model_fetcher.py` | 252 | `model_fetcher.py`: fail-closed manifest verification, exact-set + safetensors-only allowlist, symlink-resolving hashing, one-generation quarantine, the loadable safetensors fixture, the acquisition pipeline (revision pin, `$HF_HOME/hub` resolution, the mocked HF fetch, the `oras` mirror leg, token redaction), and US-005's warm start + retry loop — the counted-attempt proof that a warm load reaches no network, the normative 30 s→10 min jittered schedule, quarantine→re-fetch→loaded recovery on the real loader, single-flight, and clean cancellation |
| `tests/test_vendor_weights.py` | 110 | `scripts/vendor_weights.py`: the symlink-dereferenced tarball (built, extracted, bytes compared), tar determinism, generation-time allowlist refusal, the manifest round-trip through the real verifier, credential hygiene on the `oras` path, and the private-package visibility check — all fixture-driven, no registry and no token |
| `tests/test_stage2_structural.py` | 87 | Deterministic regex injection scan |
| `tests/test_orchestrator.py` | 391 | End-to-end pipeline drive, search + retrieve paths; readiness-transition admission and equivalent-IPv6 caller/operator policy regressions |
| `tests/test_search_pipeline_pins.py` | 7 | Four synthetic pre-refactor full wire/counter pins (only `request_id` excluded), two exhaustion payload/status/counter pins and the closed projection/exclusion guard. Regenerate with `uv run pytest tests/test_search_pipeline_pins.py --regenerate-search-pins -q` only for a deliberate wire or pinned-counter change; commit together and explain what moved and why. Additional metrics outside the five pinned counters do not change these fixtures. |
| `tests/test_search_providers.py` | 316 | The `SearchProvider` seam and `SearxngProvider`: protocol shape, the closed failure vocabulary, the AST sweep that keeps provider code away from the sanitization stages and the cache, the extracted SearXNG call (request shape, hardened client kwargs, raw-dict pass-through, `publishedDate` → `date`, every failure mapping, credential-free logging, `origin` including its four malformed-URL fallbacks) and the orchestrator side (candidate budget, the re-applied slice as an exact count, the `unresponsive_engines` 16×64 bound, the chain-shaped `ProviderFailure` mapping — `searxng_error` / `searxng_unavailable` for a lone `searxng` chain, `search_unavailable` for every other — and the `content_kind` / `date` copy onto each wire result); both-provider byte/encoding/status/timeout bounds and ambiguous raw-deflate replay; SearXNG query-cap bounds and chain-level outbound-only truncation, multibyte preservation and unchanged query echoes |
| `tests/test_provider_transport.py` | 84 | Genuine HTTPX/httpcore/h11 over a hermetic network seam: six exact raw-read regressions, all supported encodings, bytewise/coalesced headers, preserved 100 KiB incomplete-header allowance, header-first status/encoding decisions with malformed coalesced bodies, content-length/chunked/close framing, exact-bound completion/refusal, TLS verification, timeout forwarding, whole-call cancellation, failure vocabulary, no credential reflection and stream closure. |
| `tests/test_brave_provider.py` | 145 | `BraveApiProvider` (`feature-brave-provider`, US-010 → US-013 plus US-002): fixture-provenance guards, the pinned-sample parse, hardened client kwargs, the `config.yaml` tunables and the unconditional lifespan boot-refusal, the `chain[0]` candidate budget (spy provider + outbound `count` param, `num_results` 1/5/20), the three payload bounds (`Content-Length` fast-reject, a no-header streamed overrun, a compressed body whose decoded length overruns — all asserted never to reach `json.loads`), the chunk and query caps, the `engine="brave"` (SearXNG) vs `engine="brave-api"` (Brave) provenance split in both directions; then US-002's lifespan registration (`brave_key_present`, the `brave_skipped_missing_key` skip, `_CLEARED_ENV_VARS` exact set), US-012's failure taxonomy — every status and transport outcome to its closed `ProviderFailure`, the Brave-only-chain 422 `search_unavailable` on the wire, and key-never-leaks across the log line, the 422 body and `/metrics` on each failure path — and US-013's sanitization parity (a Brave chunk through stages 1-3 exactly as a SearXNG snippet) and the no-cache-write pin (`ContentCache.get`/`put`/`delete` spied through `FakeStorage`); supported compression, bounded output and whole-interaction timeout |
| `tests/test_url_validator.py` | 270 | SSRF defense: RFC1918, DNS rebinding, schemes; exact IPv6 network-list values and policy equivalence across compressed, expanded and leading-zero spellings, with refusal before DNS |
| `tests/test_cache.py` | 205 | Valkey cache incl. the never-log-the-URL invariant |
| `tests/test_smart_extraction.py` | 45 | Summary mode / high-signal preservation |
| `tests/test_stage1_extraction.py` | 66 | HTML extraction, `raw_text` vs `main_content`, shared config bounds including non-finite numbers and oversized integers (measured at US-005 retry) |
| `tests/test_stage4_structuring.py` | 43 | Response assembly + composite trust score |
| `tests/test_models.py` | 80 | Pydantic request/response models |
| `tests/test_app.py` | 492 | FastAPI endpoints, `/health`, capability break-glass, `/metrics`, provider policy, and lifespan wiring; closed-message policy bound refusals, threshold-default warning/fallback for invalid values (including booleans, non-finite and oversized numbers), numeric strings, Unicode/surrogate threshold boot safety, once-only INFO default publication, and the unchanged `/extract` boolean divergence; SearXNG query-cap boot wiring and unconditional refusal of invalid values |
| `tests/test_promptguard_policy.py` | 131 | Handler-side policy resolution, field-name guard, nullable bounded thresholds on both routes, default-before-ceiling classification and zero preservation, null/explicit/capped cache-key equivalence, resolved-keyword isolation, absent/contended classifier floors, trusted/VERIFIED exemptions, stamped hits (including old entries), unchanged `/extract` and policy-free 422s (recounted at hostname/config US-005) |
| `tests/test_stage3_promptguard.py` | 169 | ML scan; mocked inference plus real pinned-config resolution |
| `tests/test_ci_workflow.py` | 300 | Workflow shape, SHA pins/permissions, six-gate graph and both publish lanes; image/contract mapping, Release body/assets read-back against the anchor, reproducible exporters and four secret-grep patterns. Executes the actual POSIX awk docstring extractor against live and hostile inputs; whole-entry tense and uniqueness guards reject provisional publication-state clauses, with five permanent counterexamples for the frozen 1.3.0 announcement |
| `tests/test_compose_fragments.py` | 79 | Compose fragments, parse-only shape guards and the `forage:1.3.0` pin; search/model bare-name passthroughs on both services, the full-only HMAC key, resource envelope and status-only liveness probe |
| `tests/test_contract_smoke.py` | 94 | `contract_smoke.py`: every `/health` clause, polling, the single-source ties to the golden schema, and — since US-004 — the in-image contract checks: the `docker run --rm --entrypoint cat` argv it builds, the anchor comparisons against the committed trust root, and the `info.version` ↔ live `contract_version` claim, all driven through an injected runner so the suite never starts a container. `search-release` US-004 adds the status-aware `--expect-status`/`--anchor` coverage: a healthy body passes under `healthy` and fails under the default, a degraded body fails under `healthy`, `wait_for_health` under `healthy` keeps polling past a 200 `degraded` body until a `healthy` one arrives (or returns the last body once the deadline passes), and the in-image anchor is compared against the `--anchor` file rather than a hard-coded path |
| `tests/test_stage5_url_audit.py` | 37 | Outbound fetch + redirect-chain audit; bounded body decoding and pinned `Accept-Encoding` |
| `tests/test_fakes.py` | 18 | Shared streaming doubles: raw/decoded reads, no implicit Content-Length, delayed chunks, client patch restoration, per-instance and aggregate decoder observations including raw-deflate retry, and complete `SearchMetricsSink` parity |
| `tests/test_stage1_pdf.py` | 54 | PDF branch, subprocess isolation |
| `tests/test_dockerfile.py` | 50 | `Dockerfile` text: no secret may enter the build, digest-pinned base, lock-driven install, and — since US-004 — that the frozen contract is COPYed to `/app/contract/` (with `.dockerignore` checked for a pattern that would silently empty it) and that the two reproducibility normalizations stay: no timestamped apt artefacts, no bytecode from the import check |
| `tests/test_pyright_policy.py` | 12 | Type-checking policy: strict, one carve-out, no suppressions |
| `tests/test_searxng_smoke.py` | 61 | `searxng_smoke.py`: every evaluator branch, the Docker argv it builds, and the `--internal` wiring |
| `tests/test_searxng_docker.py` | 28 | `searxng/Dockerfile` + baked config: the negatives (no wildcard pass list, no baked secret, no header trust) and engine parity with `SEARXNG_ENGINES` (read through `pipeline/orchestrator.py`'s `_SEARXNG_ENGINES` alias) |
| `tests/test_hermeticity.py` | 10 | Executing canary for the autouse socket guard |
| `tests/test_sanitizer_revision.py` | 42 | Revision hashing over `_REVISION_SOURCES` and the `MODEL_ID@revision` model identity; exact ASCII-compatible UTF-8 bytes for numeric Unicode, invalid Unicode and lone-surrogate threshold strings |
| `tests/test_dependency_lock.py` | 3 | `uv.lock` stays CPU-only (no `nvidia-*` wheels) |
| `tests/test_contract_errors.py` | 53 | Documented error vocabulary and raise-site coverage, mirror/wire parity and route unions; redacted validation 422s on all three routes, 100-entry cap, runtime location allowlist and every-field marker fuzz, total-handler malformed-entry guards, root-log non-reflection and guard-of-the-guard counterexamples |
| `tests/test_contract_metrics.py` | 69 | Typed `/metrics` body and served app metadata: handler/wire parity, flat cgroup keys, `extra="forbid"`, counter/model ties, `info.version`, documented endpoints, latency counters and config registry/reader coverage. Resource-envelope US-003 adds the explicit dotted security/non-security registry partition and shipped-equals-code-default pin, reading actual empty-config lifespan/settings values rather than duplicating defaults. This protects the shipped baseline only, never an operator's stricter replacement. |
| `tests/test_contract_export.py` | 177 | The frozen `contract/openapi.yaml`: that the committed bytes are what the app generates, that the committed `.sha256` anchor is the sha256 of those bytes in `sha256sum -c` form, that the render is byte-stable across processes and `PYTHONHASHSEED` values (measured in subprocesses, not asserted), that the canonical form round-trips and carries no YAML anchors, and that `/extract` is documented while `extract_route_enabled` is `false`. The drift check's own failure case is committed as `tests/fixtures/contract/unregenerated_openapi.yaml` and fed to the same checker |
| `tests/test_governance_docs.py` | 72 | Governance, SECURITY and PR-template claims tied to code: current version, regeneration command, ten hashed sources, six required checks/examples, thirteen registered rulings and resolvable citations/links. Covers the validation-422 one-MINOR redaction window and second-MINOR removal ruling, directional policy/cache semantics, four current anchor quotations and the version-agnostic supported-versions policy |
| `tests/test_contract_schema.py` | 17 | Six-model frozen golden; retained 1.2.0 pair and frozen 1.3.0 exact-additions/announcement sweep for ten golden-visible additions, including all four effective-policy fields; metrics remain under dedicated coverage |
| `tests/test_corpus_lint.py` | 100 | The injection-corpus record format (`scripts/corpus/`): every lint rule parametrised with a failing record whose error is `<id>: <rule>` and never echoes the payload; the secret-shape negative control for every regex; the three declared URL exceptions (`scheme`, `private_ip`, `ipv6_zone` — the last only for link-local and documentation hosts) for both kinds; kind-keyed `params.variant`; loader order and an empty corpus; committed records lint-clean and stored only as `.jsonl`; `STAGE2_REGEX_NAMES` / probes / `STAGE2_REGEX_NO_BENIGN` pinned against the scanner via `tests/corpus_stage2.py`. The `MIN_RECORDS` floor test runs (un-skipped by `corpus-gates` US-002); and the rewrite guard — every `tests/test_corpus_*.py` module (by glob) and every other test module that mentions the corpus carries `PYTEST_DONT_REWRITE` in its docstring, so a failing assert prints only its message and never its operands, with a subprocess control that measures the marker's effect |
| `tests/test_corpus_harness.py` | 178 | The corpus harness (`scripts/corpus/replay.py`, `doubles.py`, `outcomes.py`, `drivers.py`): `ReplayClassifier` against the stage-3 seam (hash keying, `max_chunks` budget error, placeholder chunk labels, miss error, `classify` pooling checked against the real classifier's, test-only `fallback` and the AST guard that `scripts/` never passes one); `corpus_app` boot hermeticity (hostile `VALKEY_URL` / `FORAGE_MODEL_ID` / provider / HMAC / break-glass env, with a control that the same shell refuses the real lifespan; contiguity keys seen at boot; no weight load; `app.state.search_providers` / `cache` / `classifier` put back on exit so a drive never leaks an override into the next test); all three routes through `httpx.ASGITransport` for `blocked` / `flagged` / `neutralised` / `leaked` / `clean` and the `classifier=None` unavailable path; the closed `BLOCKING_ERRORS` map (three re-measured rows, a synthetic table of harness errors, real 429 / 422 `busy` and disabled-route 404 drives) with messages naming ids only; `UnrecordedRecordError`; the leak check (JSON walker ignoring `injection_spans` and nothing else, blocked-but-leaked title, `normalize_text` agreement over every invisible, zwsp marker, a per-variant survival test over all ten `ATTACK_VARIANTS`); a sentinel that never reaches a repr, error, log, capture or failing assertion; the doubles pinned against `SearchProvider` and the `ContentCache` methods the service calls; a stage-2 drift guard between `tests/corpus_stage2.py` and the driven wire. The `-k seed` section drives every committed seed record structural-only (`ReplayClassifier(fallback=0.0)`): lint-clean seed floors and file naming, the per-category and per-carrier expected outcomes, the generic `pinned` test (reads each pin from the record, both rule configs), untagged benign records `clean`, the two audit bypasses `blocked` on `/search`, the R26 decode / strip records shown to depend on the second entity decode and the NUL strip, the git-SHA `code` benign `flagged`, stage-2-clean stage-3 shapes, character budgets for the residual shapes (never a window count), and the drift guard over all 35 seed records. No `run_promptguard` mock and no network |
| `tests/test_corpus_record.py` | 37 | Score recording (`scripts/corpus/record.py`, `replay.py` cassette I/O, `corpus-recording` US-001): the cassette file (bare `owner--name@<revision>.json` slug, exact float round-trip through `ReplayClassifier.from_cassette`, sorted-key atomic write, malformed shapes refused by file name, the 2 MiB cap and parse lint over committed cassettes); `RecordingClassifier` on `tests/fixtures/tiny_model` through the real loader at a reduced 32/8 window (the unpatched fixture is pinned to raise on a full-length chunk) — unbudgeted recording with the budget error raised live and in replay; live-vs-replay equivalence on all three routes and both rule configs, no record text in the cassette, a deleted entry an `UnrecordedRecordError`; the recorder CLI (`--help` offline, `model_env_set` / `model_id_not_allowed` / `not_pinned` with the acquisition seam never called, `not_loaded`, `unscanned`, the seam handed the manifest pin and the cassette carrying it, the summary line, a fake token absent from output); the `NOTICE` cassette paragraph; the staleness guards (`corpus-recording` US-004: revision vs manifest pin, soft torch / transformers vs `uv.lock`, one cassette per model id); and the two owner-gate tests (full-corpus zero misses, `windows_min` promises), which run against the committed cassettes |
| `tests/test_corpus_report.py` | 59 | The corpus report (`scripts/corpus/report.py`, `poolers.py`, `corpus-gates` US-001): stage attribution as a total function (every bucket, the `/search` flagged precedence with its strict `> 0.5` boundary, every `BLOCKING_ERRORS` row landing in `refused`, an unattributable catch and `skipped_trusted` raising with id/route/signals only); the stage-3 `rule` recomputed from window scores per config; `build_report` on fallback cassettes (stage 3 / `sub_threshold`, blocked-but-leaked, `unmeasured_models` and `cassette_versions_differ` warnings); determinism of both renderers, JSON shape (sorted keys, 4 dp, `_regenerate` first) and a sentinel marker absent from both; the committed corpus (per-cell stage counts sum to catch, one row per record x model x config, flagged `/search` at score <= 0.5 carries a stage-2 hit); the offline poolers and `--sweep` (US-004); the CLI (a mode is required; `--check` refuses `--write-baseline` / `--write-floors` before any drive or write). Failure messages are ids and numbers |
| `tests/test_corpus_gate.py` | 19 | The corpus gate (`corpus-gates` US-002): the live report compared with the committed `tests/corpus/baseline.json` (regeneration command first; a drift names the record and the command), every cell held to `tests/corpus/floors.json` with conservative rounding and a raised floor / breached ceiling red, floor/cell completeness both ways, pinned records on every model and config, every cassette answering every record under every config, the corpus count floors, and payload-free failure messages on forced failures |
| `tests/test_corpus_docs.py` | 13 | The corpus documentation (`corpus-gates` US-005): `docs/corpus.md`'s nine sections and disclosure reasoning, numbers only in the decision inputs (whose cells are re-derived from `baseline.json`'s `offline` key), rejected sources with reasons, every relative link resolving to a **git-tracked** path, the README paragraph and SECURITY.md claims; four findings checks against `kit_tools/AUDIT_FINDINGS.md` that skip where that gitignored file is absent |
| `tests/test_corpus_attacks.py` | 53 | The attack corpus (`corpus-attacks` US-001/US-002/US-005): the structural families, obfuscation variants and surfaces; the hidden-markup carriers and their phrasing shapes; the two classifier-only categories across languages. Asserts that each record's labels tell the truth — the named variant is the transform it carries, the named regex can fire on it, its marker survives to the wire — and checks only the outcomes the records pin, read from the records. Failure messages are record ids, rules or closed tokens |
| `tests/test_corpus_ingest.py` | 117 | Third-party ingestion (`scripts/corpus/ingest/`, `corpus-attacks` US-004 and `corpus-benign`): the attack samplers (`agentdojo`, `llmail_inject`, `cyberseceval`) and the benign sampler (every `--source`, including `wikinews_intl` / `cpython_docs_long` with per-line pins, the `windows_min` estimate and the `--excerpt-cap` bounds) fed small native-shape fixtures under `tmp_path` — determinism under a fixed seed, limits and excerpt caps, reserved-host URL rewriting, pinned third-party provenance and licence resolution, secret-shape rejection, changed-input refusal, triage through `drive()` before writing, ids-and-counts-only output; then the committed benign corpus: per-genre floors with declared provenance (`not_ingested` or `synthetic` reason, never both), closed-reason `sampler_stats.json`, over-defence probe coverage, structural-only outcome/variant agreement, the search-shaped families and final totals |
| `tests/test_retrieve_admission.py` | 15 | Retrieve admission queue/budget refusals, permit ownership across actual HTML-task cancellation, absolute fetch deadline, off-loop stages, body release before classification and continued service responsiveness |
| `tests/test_search_policy.py` | 15 | Restrict-only request policy, known/unknown providers, free-provider retention and longest named paid-prefix behavior |
| `tests/test_structural_scan_forms.py` | 93 | Stage 2 derived scan forms: decoded form per route, combine rule (as-is flags on unmoved verdicts), lazy early stop, corpus mirrors via the builder |
| `tests/test_stage2_complexity.py` | 53 | All-patterns linearity sweep over five adversarial shape families (GC off while timing, hard timeouts) |
| `tests/test_confusables.py` | 104 | Generated confusable tables: `--check` drift, pinned data sha256, ASCII never folds, independent look-alike oracle, supplement and pre-NFKC rules |
| `tests/test_stage2_fold_forms.py` | 20 | Confusable fold forms on every route, both I/l readings, the 4x expansion refusal flagged `encoded_payload` |
| `tests/test_inline_scan_form.py` | 96 | Inline-joined scan form: linear block-boundary walk, split-tag property test, parse-once, `raw_text` byte-identity |
| `tests/test_stage2_raw_markup.py` | 20 | `_MARKUP_PATTERNS` and `scan_raw_markup`: first-match raw-source scan, widened `system_tag`, padding and linearity |
| `tests/test_visibility_pass.py` | 91 | Served-body visibility pass: per-signal prune/keep fixtures, inherited re-show, fallback flag, benign byte-identity |
| `tests/test_corpus_floors_diff.py` | 12 | Floors ratchet check: tightening, provable loosenings (denominator-only, owner-exempt records), headline and cell-set refusals, exact baseline FPR |
| `tests/test_corpus_docs_payloads.py` | 3 | No new tracked Markdown quotes attack-only payload text; the frozen pre-existing allowlist only shrinks |

Support files:

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Puts the repo root on `sys.path`; installs the autouse socket guard |
| `tests/corpus/` | Injection-corpus records: `attacks/<category>.jsonl`, `benign/<genre>.jsonl` (counts and per-genre provenance in the README — not repeated here), `benign/sampler_stats.json` (per-genre sampler counts under closed reasons), and a README documenting the record shape, outcome vocabulary, content rules, pinned records, the samplers and where their downloads live. Payload text is data, never quoted elsewhere; linted by `tests/test_corpus_lint.py` |
| `scripts/corpus/` | Corpus harness, host- and CI-side only (never in the image): `vocab.py` closed vocabularies, `records.py` record type / loader / lint, `replay.py` `ReplayClassifier` (the stage-3 stand-in, keyed by text hash) and cassette read, `record.py` `RecordingClassifier` and the host-side recorder CLI, `doubles.py` corpus-owned `CorpusSearchProvider` / `CorpusContentCache`, `outcomes.py` outcome model, closed `BLOCKING_ERRORS` map and leak check, `drivers.py` `corpus_app` / `drive` / `drive_all`. Imports nothing from `tests` |
| `tests/corpus/cassettes/` | Recorded classifier scores (`<owner>--<name>@<revision>.json`): per-window scores and window counts keyed by text SHA-256, model identity, revision and recording metadata — no text. Written by `uv run python -m scripts.corpus.record`, read by `ReplayClassifier.from_cassette`; capped at 2 MiB and covered by `NOTICE`, linted by `tests/test_corpus_record.py` |
| `tests/corpus_stage2.py` | Tests-side stage-2 naming: `stage2_hits`, `stage2_forms`, `stage2_record_hits` (runs the private `_PATTERNS`), and the `name-variants` entry point |
| `tests/fixtures/search/` | Synthetic, scrubbed pipeline response/exhaustion pins with `request_id` removed before writing; regenerate via `--regenerate-search-pins` in the same commit as a deliberate wire/pinned-counter change, explaining why in its message. The older `baseline_pre_blocked_domains.json` is separately frozen, never regenerated. |
| `tests/fakes.py` | Shared fakes and builders: `FakeStorage`, `FakeContentCache`, `FakeSearchProvider`, `assert_frozen`, the Hugging Face cache-layout helpers (`materialize_hub_snapshot`, `hub_download_double`, `weights_manifest_document`), and `record_network_attempts`. Streaming doubles: `ChunkStream` (raw chunks only, optional per-chunk delay), stream-backed `make_response` (no implicit Content-Length), `make_stream_cm`, `client_patch(target, ...)`, `RecordingDecompressor` and `record_decompressors()` (aggregate every decoder instance, including raw-deflate retries). `RecordingSearchMetrics` owns every `SearchMetricsSink` counter and its `counters` projection; add future sink fields here, not in local copies. |
| `tests/fixtures/tiny_model/` | A real, loadable 2-layer DeBERTa-v2 classifier (~96 KB, safetensors only) — the fixture that lets the *actual* loader be exercised rather than mocked |
| `tests/fixtures/contract/unregenerated_openapi.yaml` | The contract drift check's committed failure case: `contract/openapi.yaml` with `Extract422ErrorResponse.sanitizer_revision` removed — what the file would look like if a response model had changed and nobody regenerated. Written by `scripts/export_contract.py` alongside the contract, so one command keeps both in step |
| `tests/golden/contract_1_3_0.json` | Current six-model fixture, **frozen** by `hardening-release` US-002 even though publication is pending; no in-place regeneration. The historical `contract_1_0_0.json`, `contract_1_1_0.json` and `contract_1_2_0.json` remain unchanged. Fixtures pin `model_json_schema()`, including descriptions and enum rendering; follow `contract/GOVERNANCE.md` for the next version and retain every older fixture |

### Testing the lifespan

`tests/test_app.py`'s `client` fixture drives the app through `httpx.ASGITransport`,
which **never fires lifespan events**. That is fine for endpoint tests and useless for
startup ones: every assertion about what the lifespan does would pass vacuously. Since
`forage-model-bootstrap` US-001 the module carries a second harness, `_running_app()`,
which enters the real `lifespan(app)` context manager with only `ContentCache` replaced.
Anything asserting startup ordering, `app.state` population, background tasks or shutdown
belongs there; anything else should stay on the cheaper fixture.

The weight-acquisition tests mock exactly one thing — `huggingface_hub.snapshot_download`
— and let the real verifier, the real cache layout and the real
`PromptGuardClassifier.load()` run against the committed tiny-model fixture. The autouse
socket guard is what makes that a proof rather than a hope: a load that reached the
network would fail loudly instead of passing slowly.

---

## Rules

**The suite is hermetic and must stay that way.** `tests/conftest.py` installs an autouse
`pytest-socket` fixture (`disable_socket(allow_unix_socket=True)`). A test that touches the
real network fails loudly with `SocketBlockedError`. Mock at the seam — httpx,
`socket.getaddrinfo`, transformers/torch — never relax the guard. It caught three real DNS
calls in the cache tests the day it was added.

Since US-002 the guard has an executing canary, `tests/test_hermeticity.py`: eight tests
that assert TCP/UDP/IPv6 construction, both DNS entry points and `create_connection` are
blocked while `AF_UNIX` socketpairs and async tests still work. Delete or weaken the
fixture and those tests go red — before the canary, the loss produced no failure at all.
`forage-cache-fallback` US-002 added two more for `conftest.py`'s *other* autouse
fixture, the one that clears inherited environment settings (`HF_TOKEN`, `HF_HOME`, the
three `FORAGE_*` weight variables, and `VALKEY_URL` — which since that story selects the
cache backend): an exact-set gate on the list and a canary that none of them survives
into a test.
`TestHermeticityCanaryIsEnforced` in `tests/test_ci_workflow.py` covers the one thing the
canary cannot check about itself: that it is committed and not skipped.

**Async needs no decorator.** `asyncio_mode = "auto"` is set in `pyproject.toml`.

**The exact count is a gate, not a floor.** The extraction verified *exactly* 531 tests
moved (534 collected after alias parametrization); the suite has since grown (1427 at contract US-001 — the headline above is current) as CI
guards landed (US-001 +33, US-006 +19, US-002 +21, US-003 +49, US-005 +75, US-007 +40; then
`forage-model-bootstrap` US-002 +87, US-001 +56, US-003 +102, US-004 +76 and US-005 +24;
then `forage-cache-fallback` US-001 +63 and US-002 +13; then `forage-contract` US-001 +25,
US-005 +20, US-002 +18 and US-003 +67). A silently
dropped module cannot hide under a "≥ N passed" assertion. When you add tests, update the
counts here.

**Contract expectations have one source.** `contract_smoke.py` validates a live
`/health` body against the same `HealthResponse` model `tests/test_contract_schema.py`
pins against the golden fixture, and imports every wire value it compares
(`CONTRACT_VERSION`, the degraded reason, the capability key) rather than restating it.
`tests/test_contract_smoke.py::TestSingleSourceOfTruth` asserts that mechanically — same
objects, and no wire literal anywhere in the smoke's code. If you find yourself typing a
field name or a version into a workflow's shell, that is the thing this arrangement
exists to prevent.

**Tests ship with the code they cover, in the same commit.**

**Contract changes need the golden updated.** Any change to a response shape means bumping
`contract_version` in `pipeline/contract.py` and updating (or adding) the fixture under
`tests/golden/`.

**And the frozen contract regenerated.** Since US-002 anything that moves the OpenAPI
document — a response model, a `responses=` declaration, a field description, a FastAPI
bump — must be followed by:

```bash
uv run python -m scripts.export_contract   # rewrites all three artifacts
```

`tests/test_contract_export.py` fails until you do, naming that command. Commit
`contract/openapi.yaml`, `contract/openapi.yaml.sha256` and
`tests/fixtures/contract/unregenerated_openapi.yaml` together — one command writes all
three and they are only consistent as a set. Never hand-edit any of them.

---

## Test Mapping

Used by the KitTools orchestrator to pick the right tests for a changed file.

```yaml
test_mapping:
  "retrieval_app.py": ["tests/test_app.py", "tests/test_promptguard_policy.py", "tests/test_contract_smoke.py", "tests/test_contract_errors.py", "tests/test_contract_metrics.py", "tests/test_contract_export.py"]
  "models.py": ["tests/test_models.py", "tests/test_promptguard_policy.py", "tests/test_contract_errors.py", "tests/test_contract_export.py"]
  "cache.py": "tests/test_cache.py"
  "url_validator.py": "tests/test_url_validator.py"
  "pipeline/orchestrator.py": ["tests/test_orchestrator.py", "tests/test_search_pipeline_pins.py", "tests/test_promptguard_policy.py", "tests/test_retrieve_admission.py", "tests/test_corpus_gate.py"]
  "pipeline/contract.py": ["tests/test_contract_schema.py", "tests/test_contract_errors.py", "tests/test_contract_export.py"]
  "pipeline/sanitizer_revision.py": ["tests/test_sanitizer_revision.py", "tests/test_governance_docs.py"]
  "model_fetcher.py": ["tests/test_model_fetcher.py", "tests/test_app.py"]
  "weights_manifest.json": "tests/test_model_fetcher.py"
  "tests/fakes.py": ["tests/test_fakes.py", "tests/test_model_fetcher.py", "tests/test_app.py", "tests/test_brave_provider.py", "tests/test_stage5_url_audit.py", "tests/test_orchestrator.py"]
  "pipeline/bounded_body.py": "tests/test_bounded_body.py"
  "pipeline/provider_transport.py": "tests/test_provider_transport.py"
  "pipeline/stage1_extraction.py": ["tests/test_stage1_extraction.py", "tests/test_inline_scan_form.py", "tests/test_visibility_pass.py", "tests/test_corpus_gate.py"]
  "pipeline/stage1_pdf.py": "tests/test_stage1_pdf.py"
  "pipeline/pdf_subprocess.py": "tests/test_stage1_pdf.py"
  "pipeline/stage1_upload.py": "tests/test_app.py"
  "pipeline/stage2_structural.py": ["tests/test_stage2_structural.py", "tests/test_structural_scan_forms.py", "tests/test_stage2_complexity.py", "tests/test_stage2_fold_forms.py", "tests/test_stage2_raw_markup.py", "tests/test_corpus_gate.py"]
  "pipeline/confusables.py": ["tests/test_confusables.py", "tests/test_stage2_fold_forms.py", "tests/test_sanitizer_revision.py"]
  "scripts/generate_confusables.py": "tests/test_confusables.py"
  "pipeline/stage3_promptguard.py": ["tests/test_stage3_promptguard.py", "tests/test_corpus_gate.py"]
  "pipeline/stage4_structuring.py": ["tests/test_stage4_structuring.py", "tests/test_corpus_gate.py"]
  "pipeline/stage5_url_audit.py": "tests/test_stage5_url_audit.py"
  "pipeline/smart_extraction.py": "tests/test_smart_extraction.py"
  "pipeline/extraction_limits.py": ["tests/test_stage1_extraction.py", "tests/test_app.py"]
  "pipeline/config_bounds.py": ["tests/test_stage1_extraction.py", "tests/test_app.py"]
  "pipeline/retrieve_limits.py": ["tests/test_app.py", "tests/test_promptguard_policy.py"]
  "pipeline/search_targets.py": "tests/test_app.py"
  "pipeline/search_providers/__init__.py": "tests/test_search_providers.py"
  "pipeline/search_providers/base.py": "tests/test_search_providers.py"
  "pipeline/search_providers/searxng.py": "tests/test_search_providers.py"
  "pipeline/search_providers/brave.py": "tests/test_brave_provider.py"
  "pipeline/search_providers/policy.py": "tests/test_search_policy.py"
  "promptguard/classifier.py": ["tests/test_stage3_promptguard.py", "tests/test_model_fetcher.py"]
  "searxng/config/*": "tests/test_searxng_docker.py"
  "searxng/Dockerfile": "tests/test_searxng_docker.py"
  "searxng_smoke.py": "tests/test_searxng_smoke.py"
  "tests/conftest.py": "tests/test_hermeticity.py"
  ".github/workflows/ci.yml": "tests/test_ci_workflow.py"
  "Dockerfile": ["tests/test_dockerfile.py", "tests/test_contract_smoke.py"]
  "contract_smoke.py": "tests/test_contract_smoke.py"
  "scripts/bench_promptguard.py": "tests/test_bench_promptguard.py"
  "scripts/corpus/*": ["tests/test_corpus_docs_payloads.py", "tests/test_corpus_lint.py", "tests/test_corpus_harness.py", "tests/test_corpus_attacks.py", "tests/test_corpus_ingest.py", "tests/test_corpus_gate.py"]
  "scripts/corpus/report.py": ["tests/test_corpus_report.py", "tests/test_corpus_gate.py", "tests/test_corpus_docs.py"]
  "scripts/corpus/floors_diff.py": ["tests/test_corpus_floors_diff.py"]
  "scripts/corpus/poolers.py": ["tests/test_corpus_report.py", "tests/test_corpus_gate.py"]
  "scripts/corpus/record.py": ["tests/test_corpus_record.py", "tests/test_corpus_gate.py"]
  "scripts/corpus/staleness.py": ["tests/test_corpus_record.py", "tests/test_corpus_gate.py"]
  "scripts/corpus/ingest/*": ["tests/test_corpus_ingest.py", "tests/test_corpus_lint.py"]
  "tests/corpus_stage2.py": ["tests/test_corpus_lint.py", "tests/test_corpus_harness.py", "tests/test_corpus_attacks.py", "tests/test_corpus_ingest.py"]
  "tests/corpus/*": ["tests/test_corpus_lint.py", "tests/test_corpus_harness.py", "tests/test_corpus_attacks.py", "tests/test_corpus_ingest.py", "tests/test_corpus_gate.py"]
  "tests/corpus/baseline.json": ["tests/test_corpus_gate.py", "tests/test_corpus_report.py", "tests/test_corpus_docs.py"]
  "tests/corpus/floors.json": "tests/test_corpus_gate.py"
  "tests/corpus/cassettes/*": ["tests/test_corpus_gate.py", "tests/test_corpus_record.py", "tests/test_corpus_report.py"]
  "docs/corpus.md": "tests/test_corpus_docs.py"
  "tests/corpus/attacks/*": ["tests/test_corpus_lint.py", "tests/test_corpus_harness.py", "tests/test_corpus_attacks.py", "tests/test_corpus_gate.py"]
  "tests/corpus/benign/*": ["tests/test_corpus_lint.py", "tests/test_corpus_harness.py", "tests/test_corpus_ingest.py", "tests/test_corpus_gate.py"]
  "bench/config.yaml": "tests/test_bench_promptguard.py"
  "uv.lock": "tests/test_dependency_lock.py"
  "pyproject.toml": ["tests/test_dependency_lock.py", "tests/test_pyright_policy.py"]
  "typings/*": "tests/test_pyright_policy.py"
  "docs/configuration.md": "tests/test_contract_metrics.py"
  "config.yaml": ["tests/test_contract_metrics.py", "tests/test_app.py"]
  "contract/openapi.yaml": ["tests/test_contract_export.py", "tests/test_contract_smoke.py"]
  "contract/openapi.yaml.sha256": ["tests/test_contract_export.py", "tests/test_contract_smoke.py"]
  "scripts/export_contract.py": ["tests/test_contract_export.py", "tests/test_governance_docs.py"]
  "contract/GOVERNANCE.md": "tests/test_governance_docs.py"
  "SECURITY.md": "tests/test_governance_docs.py"
  ".github/pull_request_template.md": "tests/test_governance_docs.py"
```

`tests/conftest.py` maps to the canary because the fixture it installs is the thing under
test there — an edit to the socket guard must run `tests/test_hermeticity.py`.

The non-Python entries at the end — the workflow, the Dockerfile, the lock,
`pyproject.toml`, `typings/` (stub files no test imports) and `docs/configuration.md`
(whose deployment-posture section `tests/test_contract_metrics.py` checks against the
paths FastAPI actually serves) — are the ones most easily left unmapped.
They still need mappings: without one the orchestrator falls back to a heuristic glob over
the whole suite, and a workflow, lock, Dockerfile or config edit either runs everything or
nothing. `pyproject.toml` maps to two modules because it carries two independently-guarded
concerns: the CPU-only dependency lock and the type-checking policy.

`retrieval_app.py`, `models.py` and `pipeline/contract.py` all gained
`tests/test_contract_export.py` in `feature-forage-contract` US-002. That is the whole
point of the drift check: the three files that can move the OpenAPI document must run the
test that notices, or a response-model edit reaches a PR with `contract/openapi.yaml` still
describing the old shape.

US-004 widened three entries rather than adding a module. `Dockerfile` now also runs
`tests/test_contract_smoke.py`, because the smoke's in-image paths are tied to the COPY
destination `tests/test_dockerfile.py` asserts — move the destination and the tie is what
notices. `contract/openapi.yaml` and its anchor gained it too: the smoke's in-image checks
are driven against the *committed* pair, so a regeneration that left one of them behind
fails there as well as in the export test.

US-003 added three **Markdown** entries — `contract/GOVERNANCE.md`, `SECURITY.md` and
`.github/pull_request_template.md` — for the same reason `docs/configuration.md` has one:
each makes mechanical claims (a contract version, a regeneration command, a file count, the
list of required checks) that `tests/test_governance_docs.py` holds to the code. Two
*source* files gained it in the other direction: `pipeline/sanitizer_revision.py`, because
the PR template states how many files rotate the revision, and
`scripts/export_contract.py`, because two documents name its regeneration command and the
module owns the string they are compared against.

---

## What Is Not Covered by `pytest`

- **Building and running the container.** `tests/test_dockerfile.py` guards the
  Dockerfile's *text*, not a build — pytest never invokes Docker, and the suite stays
  hermetic. The build itself is CI's `build-amd64` job (US-003) and the runtime `/health`
  contract is the `smoke` job (US-005); `docker run` + `curl /health` remains a manual
  check locally.
- **Live SearXNG / live Valkey.** Every test mocks them. A real end-to-end smoke against
  running companions is a manual step, and `feature-forage-cache-fallback` carries an
  explicit manual-smoke half.
- **Real PromptGuard weights.** The classifier tests mock model inference, so
  the suite passes with no real weights present. The genuine pinned config is
  now hash-checked and resolved by real offline AutoConfig; this catches the
  label-metadata mismatch that defeated v1.2.0's synthetic fixtures. Both
  degraded and weights-loaded healthy image smokes remain mandatory manual
  evidence; a green weights-free suite alone cannot establish model readiness.
- **A real Hugging Face download.** CI has no token and the socket guard is autouse, so
  every fetch test drives a `snapshot_download` double. What that *does* prove is
  everything downstream of the transport: the arguments the fetcher passes, the cache
  tree the bytes land in, the verification, and a real `from_pretrained` opening the
  result. What it does **not** prove is that the gated repo answers, that the pinned
  revision exists upstream, or how long ~270 MiB takes on the reference container.
  `feature-forage-model-bootstrap` US-003 is the supervised session that holds a real
  token and records that measurement.
