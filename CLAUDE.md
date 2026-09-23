# CLAUDE.md

**Forage** — extraction and telemetry service for safe web access by LLM agents.
By WashingBearLabs. Apache-2.0.

Read `README.md` for what Forage is and `kit_tools/AGENT_README.md` for how to navigate
this repo. This file carries only the invariants — the things that are expensive to
rediscover.

---

## Hard Invariants

### 1. Never import from `poppy`. Never depend on Poppy at all.

Forage was extracted from the Poppy monorepo on 2026-09-07 and is standalone. There is no
`poppy` package here, no path to one, and no circumstance in which importing one is
correct.

**Origin lesson (US-004).** The 24 service source files came out of the split clean, but
*one test* — `tests/test_orchestrator.py` — did `from poppy.core.abilities.builtin.canvas
import FILE_RECALL_FAILURE_MESSAGES` to assert that Forage's document-failure codes were a
subset of Poppy's recall-message vocabulary. A single line, easy to miss in review, and it
made the suite unrunnable outside the monorepo. The fix was not to vendor Poppy's constant
— it was to recognise the assertion as a *Poppy-side* property (Poppy owns both
vocabularies) and re-home it there. The import and the assertion were deleted here.

The generalisation: **if an assertion needs to see both sides of the boundary, it belongs
on the consumer's side, not here.** Forage may define and publish its own vocabulary; it
may never reach across to check what someone does with it.

Legacy `POPPY_*` environment-variable names survive only as back-compat aliases with a
`FORAGE_*` primary. No new name contains "poppy".

### 2. No secret may enter the image build. Ever.

`Dockerfile` takes **no build arguments at all**, and it must stay that way. A build ARG is
not a secret: Docker records it in the image's layer history, where `docker history
--no-trunc` reads it straight back out of any registry the image reaches. Deleting the
downloaded file afterwards does nothing about it.

The old `ARG HF_TOKEN` + `from_pretrained` bake block was removed in
`forage-ci-and-image` US-003, and two mechanical guards keep it removed —
`tests/test_dockerfile.py` on the file's text (every `uv run pytest`) and CI's
`secret-grep` job on the built image's layer history. Adding a credential back to the
build means defeating both on purpose. Pass secrets at **runtime**, through the container
environment (`docs/configuration.md`); Forage reads no secret store at boot either.

`test_the_build_takes_no_arguments_at_all` asserts the *absolute*, not just the
secret-shaped names, because the absolute is the part that survives review: once "the
Dockerfile declares no ARG" stops being true, the next argument only has to clear "is it
as harmless as that one?". `forage-model-bootstrap` US-004 is the worked example — it
needed a per-architecture download, declined the conventional `ARG TARGETARCH`, and used
`dpkg --print-architecture` inside the `RUN` instead.

Two things that closure does not license:

- **Publishing.** The image is secret-free AND public (US-008's flip, 2026-09-10). The
  `publish` lane is live (US-007, 2026-09-08): a push reaches GHCR only behind all six
  gates plus a layer-identity check; anonymous pulls were verified at the flip gate.
  `docs/releases.md` is the reference for the tag scheme and what a green publish proves.
- **Poppy's copy.** `services/retrieval/Dockerfile` in the monorepo still carries
  `ARG HF_TOKEN` and will until spec 6 pins Poppy to a published Forage image. Never push
  an image built from *that* file.

### 3. The module layout is flat, and that is a decision.

`retrieval_app.py`, `models.py`, `cache.py`, `url_validator.py` sit at the repo root;
`pipeline/` and `promptguard/` are the only package directories. Do not reorganise them
into a `forage/` package. Three things depend on the current paths: the Dockerfile's
`COPY` lines, `pipeline/sanitizer_revision.py`'s `_REVISION_SOURCES` (which hashes files
by relative path), and the entire test suite. The rename is deferred to a scheduled epic
that will handle all three together.

Corollary: `retrieval_app.py` keeps its name. It was renamed from `app.py` to avoid a
`sys.modules['app']` collision, and while that collision cannot happen here, the filename
is now load-bearing for the two consumers above.

### 4. A change to a response shape is a contract change.

The response contract is versioned (`pipeline/contract.py`, `contract_version` currently
**1.3.0**), and consumers are expected to refuse activation on a major mismatch rather
than guess. Changing any response shape means: bump the version, add a golden fixture
under `tests/golden/` (older ones are retained, never edited), and note the change for the
consuming repo.

**The bump policy is written down: [`contract/GOVERNANCE.md`](contract/GOVERNANCE.md).**
Read it before touching `pipeline/contract.py` or the response models in `models.py`. It
classifies any change, answers the six standing examples, and records the twelve rulings
this epic already made — including the one that is not obvious from the code: the
`/extract` 413 is documented but unreachable (FastAPI turns it into a 400), documenting it
carried no bump, and *correcting* it is a MAJOR. `.github/pull_request_template.md` is the
short form of the same checklist.

Since US-002 the frozen surface is a file, `contract/openapi.yaml`, with a committed
`openapi.yaml.sha256` anchor beside it — the trust root every other copy of the contract
is verified against, because Release assets and registry tags are mutable and a checksum
in the git history is not. Both are **generated**; hand-editing either is always wrong.
Anything that moves the document — a response model, a `responses=` declaration, a field
description, a FastAPI bump — is followed by `uv run python -m scripts.export_contract`,
and `tests/test_contract_export.py` is red until it is.

Since US-004 that file ships in three places — the git tag, the `v*` Release's assets, and
`/app/contract/openapi.yaml` inside the image — and two of the three are *checked* rather
than promised: the `smoke` job reads the in-image copy back out of the candidate image and
the `publish` job reads the Release assets back out of the API, both verifying against the
committed anchor. Consumers vendor by the procedure in `contract/GOVERNANCE.md`; the rule
is always "verify against the anchor from the same tag, never against another copy".

### 5. Degradation is loud, never silent.

`/health` always returns 200; the truth is in the body (`status`, `degraded_reasons`,
`promptguard_loaded`, `cache_connected`). Never make a missing model, an unreachable
cache, or a refused fetch look healthy. A silent version of exactly this failure ran
unnoticed in production for nine days, and the honest-health contract is the fix. Do not
regress it in the name of a cleaner status code.

### 6. Never log a credential-bearing value.

`VALKEY_URL` may carry a password. `cache.py` keeps a *closed log vocabulary* — failures
map to fixed reason strings rather than interpolating the URL — and
`tests/test_cache.py` asserts it. The entrypoint deliberately prints nothing for the same
reason. Any new startup or cache code must preserve this.

---

## Development

```bash
uv sync --extra dev     # environment (creates .venv)
uv run pytest           # full suite green, hermetic — blocking CI gate (count: TESTING_GUIDE.md; 2105 at the post-epic housekeeping PR)
uv run ruff check .     # must stay clean — blocking CI gate
uv run ruff format .    # must stay clean — blocking CI gate
uv run pyright          # strict, ZERO errors — blocking CI gate
```

Always go through `uv run` — the lock pins the toolchain, and a system-installed ruff or
pyright will report numbers that don't reproduce.

All three are zero, with no baseline and no excludes. The single type-checking carve-out
in the repo is `reportPrivateUsage` for `tests/`; type-ignore comments are switched off
outright, so a suppression cannot quietly restore the green. The policy lives in
`pyproject.toml`'s `[tool.pyright]` comment and is asserted by
`tests/test_pyright_policy.py`. Third-party gaps go in `typings/` — minimal stubs
declaring only the symbols this repo calls; read `typings/README.md` before adding one.

The suite is hermetic: an autouse `pytest-socket` guard in `tests/conftest.py` fails any
test that touches the real network. Mock at the seam; never relax the guard.

Container:

```bash
docker build -t forage .                      # works with no HF token (degraded runtime)
docker run --rm -p 127.0.0.1:8020:8020 forage # private network only — no auth exists
```

---

## Spec Execution

Feature specs live in `kit_tools/specs/` and execute **here** via
`/kit-tools:execute-epic`. The Forage-side epic wrapper is
`kit_tools/specs/epic-forage-extraction-forage-side.md`.

The four feature specs were authored in Poppy and copied here at bootstrap. **Poppy's
copies are canonical**; edits flow Poppy → Forage, one way, at handoff only. Poppy's
copies are `status: on-hold` and its epic wrapper marks them "Moved to Forage — do not
execute here". Record work done here in this repo's Implementation Notes, not in Poppy's
originals.

`kit_tools/worktree.yaml` is the environment contract the orchestrator reads
(`env_bootstrap: uv sync --extra dev`, `run_prefix: uv run`). Keep it accurate — a missing
`run_prefix` makes the orchestrator run system Python and report false regressions.

---

## Coexistence with Poppy (temporary)

Until Poppy pins a published Forage image, **Poppy's in-tree copy remains the deployed
source of truth** and the two copies coexist. Any fix to the extracted paths on either
side must be replayed onto the other, with the Poppy source commit recorded in
`docs/bootstrap-notes.md`'s pin record.

One thing has already diverged deliberately: `derive_sanitizer_revision()` moved from
`e6b2b56d…` to `2b8d7e9a…` here when the vault-free hostname defaults landed, again to
`cd00a8b4…` when the `ruff format` CI gate reformatted `pipeline/stage2_structural.py`,
once more to `0537316d…` when the pyright-strict burn-down retyped
`pipeline/stage1_extraction.py` and `pipeline/stage2_structural.py`, and now to
`5927038d…` — the one rotation that changed an *input* rather than a source
byte: the hashed model identity is `MODEL_ID@revision` since weights became a runtime,
per-deployment input (`forage-model-bootstrap` US-001) — and a fifth time to
`fa4691c5…` with the contract bump to `1.1.0`, the first rotation whose *point* is the
invalidation (`forage-cache-fallback` US-003 made the revision an input to the content
cache's key), and a sixth to `8b1b7f78…` when `contract.py` gained the seventeen-code
error vocabulary (`forage-contract` US-001 — documentation only, contract still `1.1.0`,
and the one rotation that whole spec gets), and a seventh to `ee4450d9…` when the inline
SearXNG `httpx` call left `orchestrator.py` for `SearxngProvider`
(`search-provider-abstraction` US-002 — `pipeline/search_providers/searxng.py` is **not** a
`_REVISION_SOURCES` member, so `orchestrator.py` is the only hashed file that moved, which
was measured rather than assumed; the wire codes are unchanged and only the `reason` text
narrowed), and an eighth to `e7038672…` when `run_search_pipeline` gained the `providers=`
chain seam (`search-provider-abstraction` US-003 — again `orchestrator.py` alone; the
default `providers=None` path is the previous behaviour unchanged), and a ninth to
`b7871b20…` with the contract bump to `1.2.0` (`search-provider-abstraction` US-004 — the
first rotation of the epic with **two** hashed files moving: `contract.py` gained
`ContentKind` and `search_unavailable` and the version bump, `orchestrator.py` gained the
chain-shaped failure predicate and the `content_kind`/`date` copy; measured by reverting
each in turn, with a both-reverted control landing on `e7038672…`), and a tenth to
`55e2af1b…` when `run_search_pipeline` gained free-first chain traversal
(`search-fallback` US-001 — `orchestrator.py` alone, measured; the loop calls providers in
order, advances on a `ProviderFailure`, and stops at the first success, replace-not-merge; a
one-provider chain still makes exactly one call and produces byte-identical wire output),
and an eleventh to `5249def6…` when `run_search_pipeline` gained fallback telemetry and
per-result provenance (`search-fallback` US-003 — `orchestrator.py` alone, measured;
`models.py` gained `SearchResult.domain` and `SearchResponse.provider_used` /
`fallback_fired` / `provider_errors` but is not a `_REVISION_SOURCES` member, so those wire
additions do not move this hash on their own; no sanitization behaviour changed), and a
twelfth to `f0b93318…` when `run_search_pipeline` gained failure-class discrimination
(`search-fallback` US-002 — `orchestrator.py` alone, measured; a `ProviderSearchResult`
with zero raw results and a non-empty `unresponsive_engines` list is now classified as a
failure and advances the chain exactly as a `ProviderFailure` does — the recurring
production shape SearXNG answers with a 200 and never raises — except on a configured
`[searxng]`-only chain, which has nothing to fall back to and still serves that shape as
before; sufficiency stays judged on raw results before sanitization), and a thirteenth to
`dc3ff92a…` when `contract.py` gained the `POLICY_EXCLUDED_ALL_PROVIDERS` literal for the
new per-request policy 422 (`search-policy-and-health` US-010 — `contract.py` alone,
measured against all eight `_REVISION_SOURCES` files; `retrieval_app.py`, where the raise
site lives, is not hashed), and a fourteenth to `41ac98ca…` when `contract.py`'s
`CONTRACT_VERSION` docstring gained the completed 1.2.0 change record — every field,
counter and enum member the epic's specs 1-4 added, all additive, plus a note that the
`/search`/`/retrieve` boundary text landed inside this same unpublished window
(`search-policy-and-health` US-003 — `contract.py` alone, measured by reverting it to its
pre-story bytes and reproducing `dc3ff92a…` exactly; `retrieval_app.py` and `models.py`,
where the boundary text itself lives, are not `_REVISION_SOURCES` members), and a fifteenth
to `b0ca8d9a…` — **the first rotation that changes sanitization behaviour** — when
`orchestrator.py` gained `_scan_forms_for_search_text` (`hardening-search-sanitization`
US-001 — `orchestrator.py` alone, measured from a clean tree by reverting it and reproducing
`41ac98ca…`; `/search` now scans `title` and `snippet` in a newline-preserving form and ships
their whitespace collapse, so Stage 2's line-anchored patterns fire on any line rather than at
character 0 only, with two entity decode levels before the scan, two control strips around
them, truncation once on the scan form and a 4× parser-input bound), and a sixteenth to
`42485686…` — **the second rotation that changes sanitization behaviour** — when
`_canonicalize_search_url` became `_SEARCH_URL_RULES`
(`hardening-search-sanitization` US-002 — `orchestrator.py` alone, measured from a clean tree
by reverting it and reproducing `b0ca8d9a…`): an ordered registry of named pure rule
functions run over the **raw** provider URL, first rejection wins, returning a frozen
`SearchUrlOutcome` that carries the omission reason and a closed `SearchUrlRule` log token —
presence/length (rejection at 2 048 characters, never truncation), raw character class
(controls, whitespace and RFC 3986 excluded characters rejected, never deleted), parse
(`urlsplit` and the `parsed.port` read each in their own `try`), host code points (WHATWG
forbidden set, IPv6 colons exempt, `%25` zone id its own token), then a structural scan of
**both** the entity-decoded and the once-percent-decoded forms; `_sanitize_search_text`,
which routed the URL through `extract_html` and so ate tag-shaped text before the scan saw
it, is deleted), and a seventeenth to `05dbbb5c…` — **not** a rotation that changes
sanitization behaviour — when `contract.py` and `orchestrator.py` moved together for the
contract `1.3.0` bump (`hardening-search-sanitization` US-004 — two hashed files, measured:
`contract.py` for `CONTRACT_VERSION` and `OMIT_BLOCKED_URL`, `orchestrator.py` for
`SearchResult.engine`'s new `_MAX_SEARCH_ENGINE_LENGTH = 64` bound, routed through the same
`_normalize_search_text` call `title`/`snippet` already use; `engine` is bounded and
normalized, not structurally scanned, so this rotation does not join the fifteenth and
sixteenth as a third behaviour-changing one), and an eighteenth to `840c78fa…` —
**the third rotation that changes sanitization behaviour, and the first that adds *inputs*
rather than only moving source bytes** — with the search-time URL audit
(`hardening-search-sanitization` US-003): `orchestrator.py` gained rules (3a)–(3c) of
`_SEARCH_URL_RULES` (canonicalise the host, classify the address, check the name blocklist)
and now reads `domain` from `CanonicalHost.host`, `contract.py` gained the `1.3.0`
continuation line, **and two inputs joined the hash** — repo-root `url_validator.py` as
`_ROOT_REVISION_SOURCES` (hashed after the eight `pipeline/` sources, resolved against
`pipeline_dir.parent`; invariant 3's "by relative path" stays true) and `idna@<version>`
(UTS-46 tables decide which hosts are dropped, so a lock bump is a sanitization change with
no source byte to show for it). All four measured alone from a clean tree, with a control
that reverts both files *and* removes both inputs landing exactly on `05dbbb5c…`.
and a nineteenth to `6f0fa2de…` — **the fourth behaviour-changing rotation** — when the
`/search` scan loop began scanning **both** forms of each text field rather than only the
newline-preserving one (`hardening-search-sanitization` validation fix; `orchestrator.py`
alone, measured, and the revert reproduces `840c78fa…` exactly). Two of the twenty-four
Stage 2 patterns carry no `re.DOTALL`, so a payload split across a newline scanned clean on
the form that was scanned and blocked on the form that was served — a bypass this epic
introduced in US-001 and its own spec-level validation caught.
and a twentieth to `e55b5f06…` — **not** a behaviour-changing rotation — when
`run_retrieve_pipeline` gained its five new keyword-only dependencies and the pre-checked
chunk budget (`hardening-retrieve-parity` US-001): `orchestrator.py` for the signature, the
`AdmissionSlot`/`AdmissionMetrics`/`RetrieveMetricsSink` Protocols, the character pre-check
and the `PromptGuardBudgetExceededError` backstop; `contract.py` for `PROMPTGUARD_BUDGET`
and the `1.3.0` continuation line. Two hashed files, each measured by reverting it in turn,
with a both-reverted control landing exactly on `6f0fa2de…`. It does not join the four
behaviour-changing rotations because the shipped default is
`retrieve.max_promptguard_chunks: 0` — no pre-check, no `max_chunks` handed to the
classifier, byte-for-byte the previous behaviour; the new module
`pipeline/retrieve_limits.py` and the migrated `pipeline/config_bounds.py` are **not**
`_REVISION_SOURCES` members, so they do not move this hash on their own.
and a twenty-first to `d0433876…` — also **not** a behaviour-changing rotation, and the
first of this epic with **three** hashed files — when the classification semaphore reached
`/retrieve` and `/search` (`hardening-retrieve-parity` US-006): `orchestrator.py` for the
`_bounded_permit` context manager (the only place `asyncio.timeout` and
`semaphore.acquire()` appear, release in `finally` only when acquired), the two defaulted
`classification_semaphore` / `classification_wait_seconds` parameters on both
`sanitize_and_structure` and `run_search_pipeline`, `/search`'s one-deadline-per-request
budget, the `/extract` file route's acquisition moving inward from the outer `async with`
that wrapped stages 2-4, and step 8's refusal to cache an `unavailable_allowed` body while
the classifier is loaded; `stage3_promptguard.py` for the pure `unavailable_result` seam
that keeps the wait-timeout path and the absent-classifier path from drifting;
`contract.py` for the `1.3.0` continuation line. Each reverted in turn with an all-reverted
control landing exactly on `e55b5f06…`. What moved is *when* stage 3 runs and what happens
when the permit wait expires, not how any text is sanitized.
and a twenty-second to `f654be77…` — **not** a behaviour-changing rotation — when stages 1,
2 and 4 moved off the event loop and `/retrieve` gained its admission gate
(`hardening-retrieve-parity` US-002): `orchestrator.py` for `asyncio.to_thread` around
`extract_html`, `scan_structural` and `structure_sanitization_result`, the now-required
`admission: AdmissionSlot` acquired after the cache read and released in `finally` after
stage 1, the 422 `busy` refusal, and the deletion of `fetch_result` / `html_text` before the
classification wait; `contract.py` for `busy` in `RetrieveErrorCode`,
`RETRIEVE_ADMISSION_QUEUE_FULL` and the `1.3.0` continuation line. Each reverted in turn with
a both-reverted control landing exactly on `d0433876…`; `stage4_structuring.py` untouched.
The threaded functions are pure, so every route's output is byte-identical.
and a twenty-third to `464b6ad5…` — **not** a rotation that changes how any text is
sanitized, but the first of this epic to move a served outcome at the shipped defaults —
when fetched PDFs moved into the rlimited worker (`hardening-retrieve-parity` US-003):
`orchestrator.py` for the `asyncio.to_thread(extract_pdf_bytes_in_subprocess, …)` call
inside the admission slot, cancellation-safe waiting until worker reaping and spool cleanup,
and its most-specific-first mapping to 422s (plus the `retrieve_spool_error` WARNING);
`contract.py` for `extraction_failed` in
`RetrieveErrorCode`, the four `RETRIEVE_PDF_*` reason literals and the `1.3.0`
continuation line. Each reverted in turn with a both-reverted control landing exactly on
`f654be77…`; `pipeline/pdf_subprocess.py`, where `spool_dir()` and the bytes entry point
live, is not a `_REVISION_SOURCES` member. A fetched PDF within bounds serves the same text
as before; one over 114,688 characters or the worker's rlimits is now refused, and every
PDF failure is a coded 422 where it was a 500.
The recovered candidate's `6fd320da…` was never accepted: cancelling its `to_thread`
await released admission while the worker and spool survived. The corrected task retains
ownership even under repeated cancellation and propagates cancellation only after cleanup.
and a twenty-fourth to `664ee603…` — **not** a rotation that changes sanitization
behaviour — when `contract.py` gained the `1.3.0` continuation line for
`cache.corrupt_entries` (`hardening-retrieve-parity` US-004). It is the only hashed
file that moved; reverting its bytes alone reproduces `464b6ad5…` exactly.
`cache.py`'s guarded parse and `retrieval_app.py`'s metrics mirror/emission are not
hashed. Invalid cached JSON or schema now becomes a counted, logged miss with deletion
attempted rather than a 500; parse success still does not establish authenticity.
and a twenty-fifth to `d98f7dbe…` — **not** a rotation that changes sanitization
behaviour at shipped defaults — when `contract.py` gained the `1.3.0` continuation
line for the three effective-policy fields (`hardening-retrieve-parity` US-005).
It is the only hashed file that moved; a read-only whole-file revert reproduces
`664ee603…` exactly under both default and shipped configuration. The handler's
request replacement and post-pipeline response stamping (`retrieval_app.py`) and
the defaulted response fields (`models.py`) are not hashed. The operator can now
bound fail-closed on both fetch routes and the threshold on `/retrieve` only;
the defaults impose no bound and caller trust-tier exemptions remain intact.
and a twenty-sixth to `5a470872…` — the preceding retrieve-parity validation
commit `fe211e3` changed `orchestrator.py` and `stage3_promptguard.py` for
cancellation ownership, the absolute fetch deadline and timeout accounting;
the read-only pre-validation control reproduces `d98f7dbe…`. This is not a
change to the text sanitization algorithm. And a twenty-seventh to `328d386c…` —
**the fifth sanitization-behaviour-changing rotation** — for directional hostname
policy (`hardening-hostname-and-config` US-001): `orchestrator.py` uses the shared
matcher, `contract.py` announces the list descriptions and private-name precedence,
and **`url_validator.py` is already hashed** through `_ROOT_REVISION_SOURCES`, so
its normaliser/matcher moves the hash too. All three reversals were measured;
the all-reverted control reproduces `5a470872…` under default and shipped config.
Leading-dot trust can skip PromptGuard across a suffix; multi-label denylists now
block subdomains. Matching-only changes therefore rotate automatically, not silently.
And a twenty-eighth to `c8a907cf…` for request-domain normalization and metrics
(`hardening-hostname-and-config` US-007): `orchestrator.py` removes the interim
entry pass, merges operator entries first and counts wildcard trusted/verified
resolutions; `contract.py` announces four counters and the byte-cap refusal;
already-hashed `url_validator.py` removes its entry pass and sizes malformed
surrogate escapes without crashing (they still fail canonicalisation).
All three read-only reversals were measured; the all-reverted control reproduces
`328d386c…` under default and shipped config. This is the **sixth policy-driven
sanitization-behaviour change**: an over-budget allowlist can no longer grant
trust through its dropped tail, while an oversized denylist is refused whole.
In-budget matching and the text-scanning algorithm are unchanged.
And a twenty-ninth to `de1cea65…` for search domain policy
(`hardening-hostname-and-config` US-002): `orchestrator.py` merges canonical
operator entries before the explicit `blocked_domains=` parameter and omits
matches through the existing blocked outcome, logging `host_class=policy_blocklist`;
`contract.py` announces the optional field and both non-retryable policy reasons.
These are the only two hashed sources that move; both individual read-only
reversals were measured and the both-reverted control reproduces `c8a907cf…`
under default and shipped configuration. This is the **seventh policy-driven
sanitization-behaviour change**: search now enforces both domain lists, before
content scanning and without paid fallback. The empty-seed/no-new-field baseline
is unchanged; the text sanitization algorithm is unchanged.
And a thirtieth to `e00049c4…` for shared threshold resolution
(`hardening-hostname-and-config` US-005): `orchestrator.py` takes the required
resolved `promptguard_threshold` keyword for both its cache fingerprint and
classification, never reading the now-nullable request field; `contract.py`
announces the search request/response additions and config-default/ceiling policy.
Only those two hashed files move. Individual read-only reversals were measured;
both-reverted reproduces `de1cea65…` under default and shipped config.
This is the **eighth policy-driven sanitization-behaviour change** for tuned
deployments: both fetch routes default from the configured key before the
operator ceiling. Shipped 0.85 behavior and text-scanning algorithms stay the
same. `/extract`'s raw-value guard and the raw configured hash input stay intact;
the active float reaches the cache through `cache_policy_fingerprint`.
`docs/bootstrap-notes.md` carries
the before/after and the reasoning for each.
And a thirty-first to `aa288bc5…` for signed and bounded cache values
(`hardening-cache-integrity` US-001): only `contract.py` moves in the hash,
announcing `cache.integrity_rejects` and widened `storage_oversize_skips`
producers in the held 1.3.0 window. A read-only whole-file revert against
`b79504d` reproduces `e00049c4…` under default and shipped config; the other
eight hashed files are unchanged. HMAC/key binding and byte/type bounds in
`cache.py`, and construction/metrics wiring in `retrieval_app.py`, are not
hashed. This is not a change to text sanitization; the rotation invalidates
old cache keys. Full measurements are in `docs/bootstrap-notes.md`.
And a thirty-second to `0866963a…` for the boot signing verdict and health
vocabulary (`hardening-cache-integrity` US-002): only `contract.py` moves,
adding `cache_unauthenticated`, its constant and the 1.3.0 continuation for
that reason and `cache_hmac_key`. A read-only whole-file reversal against
clean `1e467c1` reproduces `aa288bc5…` under default and shipped config; all
other eight hashed sources are unchanged. The one-read key resolver and
Valkey-only signing wiring in `retrieval_app.py` are unhashed. This is not
a text-sanitization change; the rotation invalidates old cache keys.
Full measurements are in `docs/bootstrap-notes.md`.
**Do not assume Poppy↔Forage revision parity** — compare contracts, not revisions.

The thirty-third rotation is `0866963a…` → `c9bf6e0d…` for
`hardening-provider-bounds` US-003. Only `orchestrator.py` (compression/timeout
counters before every provider-loop exit, plus the re-classification flag) and
`contract.py` (1.3.0 continuation for both counters and the SearXNG-only
`unsupported_encoding` reason token) move in the hash. Whole-file read-only
reversals against clean `abf9df6` give `61d54562…` with only the orchestrator
reverted, `e736bb76…` with only the contract reverted, and the exact pre-story
`0866963a…` with both reverted, under default and shipped config.
The shared `bounded_body.py` and provider modules remain unhashed. This is
not a text-sanitization change, although bounded upstream reads and tightened
whole-interaction timeouts can change served outcomes and paid fallback.
Full values and the consumer handoff are in `docs/bootstrap-notes.md`.

The thirty-fourth rotation is `c9bf6e0d…` → `e3b9c138…` for
`hardening-provider-bounds` US-004. Only `contract.py` moves among the nine
hashed sources, announcing the paid-prefix rule and its all-paid-chain
policy 422. A read-only whole-file reversal against clean `0139ad6`
reproduces `c9bf6e0d…` exactly under default and shipped config; the other
eight sources are unchanged. `policy.py` and `models.py` are not hashed.
This changes neither text sanitization nor any currently constructible
production chain's outcome: one paid name plus duplicate collapse makes
the multi-paid case unreachable (GOVERNANCE ruling (k)). T3.1 inherits the
rule when it registers a second paid backend. Full measurements and the
consumer handoff are in `docs/bootstrap-notes.md`.

The thirty-fifth revision rotation is `e3b9c138…` → `d9db7586…` for
`hardening-provider-bounds` US-005. Only `orchestrator.py` moves: traversal
is extracted into `_query_provider_chain` with in-place sink increments
and frozen `_ServedChain`, the pipeline-only legacy keyword is retired,
omission logs lose result URLs, and failure name/class/detail tokens are
guarded. First commit `8e449fc` pins four full synthetic wire/counter runs
and two exhaustion payload/status/counter runs before any pipeline edit;
all six captures remain unchanged. A read-only whole-file reversal against
clean pre-story `2a275c5` reproduces `e3b9c138…` under default and shipped
config, with the other eight hashed files unchanged. This is not a
text-sanitization change; contract and generated artifacts are unchanged.
Full measurements and the direct-caller/log-consumer handoff are in
`docs/bootstrap-notes.md`.

The thirty-sixth rotation is `d9db7586…` → `bf5a1f3e…` for
`hardening-resource-envelope` US-004. Only `orchestrator.py` (configurable
observational targets, the once-per-request overrun counter and whole-loop
high-water mark) and `contract.py` (held 1.3.0 metrics continuation) move.
Read-only whole-file reversals against clean `7087c04` yield `66b50985…`
with only the orchestrator reverted, `3c699860…` with only the contract
reverted and exactly `d9db7586…` with both reverted, under default, shipped
and maximum-target configuration. The other seven hashed sources and hash
definition are unchanged; `search_targets.py` and its knobs are not inputs.
This is not a text-sanitization change; search wire/counter pins are unchanged.
The max measures structural scan, PromptGuard and semaphore wait across the
whole result loop, never one wait. Full values and the additive `/metrics`
consumer handoff are in `docs/bootstrap-notes.md`.

---

The thirty-seventh rotation is `bf5a1f3e…` → `4913fdc1…` for
`hardening-resource-envelope` US-002. Only `contract.py` moves among the nine
hashed sources, recording the shipped Compose healthcheck's description
correction in held 1.3.0. A read-only whole-file reversal against clean
`2aa6356` reproduces `bf5a1f3e…` under default and shipped config; the other
eight sources and hash definition are unchanged. This is not a sanitization
or response-shape change. OpenAPI and the held golden are regenerated because
the health model and route descriptions now name the status-only liveness
probe; historical goldens remain untouched. Full measurements and the
consumer handoff are in `docs/bootstrap-notes.md`.

`hardening-promptguard-86m` US-001 adds **no rotation**: default and shipped
revision remain `4913fdc1982cb48ba2db9c6972fcea10107408349970c45c9dc6b3ae5c1aa1fb`.
All nine hashed sources and the default model identity are unchanged; loading
the three pre-story modules read-only from `5ced1e9` reproduces the value.
The weights manifest is now per model (`models[model_id]`); the original 22M
revision/files are unchanged. Acquisition refuses unknown entries and shaped
non-pin revisions before any snapshot lookup, and `_load_verified` checks
requested/manifest path equality plus existence before loading. The classifier
does not independently verify identity. Runtime manifest entries, including
failures, are memoised; replacing the manifest requires a process restart.
`DEFAULT_MODEL_ID` replaces the old constant everywhere. Measurements and the
direct-caller handoff are in `docs/bootstrap-notes.md`; no 86M owner gate ran.

## Session Scratchpad

After completing significant work (feature, bug fix, refactor, investigation, decision), append a note to `kit_tools/SESSION_SCRATCH.md`:

```
[HH:MM] Brief description of what was done
- Files: key files changed (if any)
- Decision: any non-obvious choices (if applicable)
```

Keep notes terse — one line plus optional details. This file survives context refreshes and gets processed on session close.
