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
classifies any change, answers the six standing examples, and records the five rulings
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
`docs/bootstrap-notes.md` carries
the before/after and the reasoning for each.
**Do not assume Poppy↔Forage revision parity** — compare contracts, not revisions.

---

## Session Scratchpad

After completing significant work (feature, bug fix, refactor, investigation, decision), append a note to `kit_tools/SESSION_SCRATCH.md`:

```
[HH:MM] Brief description of what was done
- Files: key files changed (if any)
- Decision: any non-obvious choices (if applicable)
```

Keep notes terse — one line plus optional details. This file survives context refreshes and gets processed on session close.
