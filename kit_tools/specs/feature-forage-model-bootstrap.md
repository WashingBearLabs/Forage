<!-- Template Version: 2.5.0 -->
---
feature: forage-model-bootstrap
status: active
session_ready: true
depends_on: [forage-ci-and-image]
vision_ref: Secure Web Retrieval / provider-independent web access
type: epic-child
size: L
epic: forage-extraction-forage-side
epic_seq: 2
epic_final: false
execution_order: [US-002, US-001, US-003, US-004, US-005, US-006]
created: 2026-09-02
updated: 2026-09-07
---

# Feature Spec: Forage Model Bootstrap — Download-at-Start Weights + Vendored Mirror

> **Executes in the Forage repo.** US-003 (vendoring the weights) is an ops story needing the
> owner's HF token and GHCR credentials — **supervised** (also marked in the Forage-local epic
> wrapper).

## Overview

Replace the deleted build-time weights bake (spec 2 removed the `HF_TOKEN` build-arg) with
runtime acquisition: on start, if the pinned PromptGuard revision is not verified-present in
the model-cache volume, Forage fetches it — HF gated repo first (operator's `HF_TOKEN`), then
the WashingBearLabs private GHCR OCI mirror (`forage-weights`) — **verifies integrity against a
committed sha256 manifest that doubles as a strict file allowlist** (safetensors only; any
pickle-format weight is refused — the runtime-fetch path must not open the `torch.load` RCE
door), loads via a thread-offloaded call (the event loop and `/health` never block), and keeps
`/health` honestly `degraded` the whole time the classifier isn't loaded (Epic 1 semantics,
wire shape unchanged). A background retry with backoff converges a late-credentialed or
transiently-failed sidecar to loaded without a restart. **Poppy-side deploy timing (tier-1
gate vs cold-volume first boot) is real and owned by spec 6** — the earlier "no Poppy
interaction required" claim was wrong (validation round 1).

## Goals

> **Spec-slot wire note (round-2 critical):** in this spec's execution slot, `/health` may
> still legitimately report `status: "degraded"` for `cache_unavailable` (spec 4's
> unset-Valkey-healthy semantics land *after* this spec). Every "loaded" goal below is
> therefore stated in PromptGuard terms — `promptguard_loaded: true`,
> `"promptguard_unavailable"` **absent** from `degraded_reasons`, and
> `capabilities.search_sanitization` advertised — never as `status: "healthy"`.

- Fresh container + valid `HF_TOKEN` + empty volume: classifier loaded (per the wire note)
  within 5 minutes on a 1-vCPU/1 GB container — weights are **283,347,432 bytes (~270 MiB)
  at the pinned revision** (measured on the live sidecar, round 2; the "~100 MB" planning
  figure was wrong); `/health` answers < 1 s throughout the fetch. The 5-minute budget is an
  AC with a named measurement (container start → `promptguard_loaded: true` flip).
- Fresh container + no token + mirror creds: same outcome via the mirror path — including
  actual classifier load from mirror-fetched files (not just files-on-disk).
- Fresh container + neither: `degraded` with `promptguard_unavailable`, one ERROR log naming
  both attempted sources, and a `/metrics` `model.fetch_failures` counter — zero silent paths.
- Warm volume: start-to-loaded under 60 s with **zero network calls** (verified via
  `local_files_only` + socket-guarded test).
- A corrupted, missing, extra, or non-safetensors file in the cache is detected, quarantined
  (bounded to one quarantine generation), and never loaded.

## User Stories

### US-001: Startup fetcher — HF source, pinned revision, non-blocking

**Priority:** P1

**Description:** As a Forage operator, I want the container to fetch the pinned PromptGuard
revision from HF at start using my `HF_TOKEN` env var, without the fetch ever blocking
`/health`.

**Independent Test:** Run the image with `HF_TOKEN` set and an empty volume at
`/app/model-cache`; `promptguard_loaded` flips false → true without a restart
(`promptguard_unavailable` leaves `degraded_reasons`), `/health` during the (mocked-slow)
fetch adds no latency beyond the pre-existing 2 s cache-reconnect floor (measured with a
connected/fake cache — micro-verify reconciliation with the AC), and the volume contains
the pinned revision's snapshot.

**Implementation Hints:**
- Wire literal reminder (family-wide validation critical): `/health.status` is
  `Literal["healthy", "degraded"]` (`retrieval_app.py:115`, emitted `:625`) — there is no
  `"ok"`; Poppy coerces unknown statuses to degraded.
- New `model_fetcher.py` module (fetch → verify → load seams for US-002/US-004/US-005), not
  acquisition buried in `PromptGuardClassifier.load()` (`promptguard/classifier.py:44-70`,
  which today passes no token, no revision, and never retries).
- **Blocking work off the loop AND off the lifespan** (round-2 refinement): `from_pretrained`
  is synchronous network+torch work. The required shape is
  `app.state.model_task = asyncio.create_task(asyncio.to_thread(fetch_and_load))` — the
  `to_thread` alone is not enough, because an `await asyncio.to_thread(...)` placed before
  the lifespan's `yield` (where today's blocking load sits, `retrieval_app.py:530-536`)
  still delays startup: uvicorn serves nothing until lifespan startup returns, and the
  compose healthcheck (10 s × 5 retries, **no `start_period`**) restart-loops the container.
  Lifespan startup must yield immediately; the task handle lives on `app.state` for US-005's
  shutdown cancellation. In-repo `to_thread` idiom: `stage3_promptguard.py:130-131`.
- **Pin the revision** (validation critical): `FORAGE_MODEL_REVISION` env, default = the
  committed constant **`11614a155199674a0a95e6602d6ab0417b790ed0`** (the current upstream
  revision, measured round 2 — US-003 re-vendors at exactly this sha); pass `revision=` to
  `from_pretrained` and `snapshot_download`. A test locks the triple: constant == committed
  manifest revision == mirror tag (round-2 drift finding). Extend
  `pipeline/sanitizer_revision.py` to hash `MODEL_ID@revision`; this rotates
  `sanitizer_revision` once, acknowledged — including its Poppy-side blast radius
  (`stored_file_extractions` re-extraction, noted in spec 6).
- Fetch = `huggingface_hub.snapshot_download(MODEL_ID, revision=..., token=...)` resolving
  through the **hub cache tree** — precisely: `HF_HUB_CACHE = $HF_HOME/hub`, so the
  canonical location is `/app/model-cache/hub/models--meta-llama--Llama-Prompt-Guard-2-22M/`
  (verified on the live sidecar; round-3 critical: passing `cache_dir=$HF_HOME` writes one
  directory too high and `from_pretrained` reads another — never `local_dir` either, which
  flattens the `snapshots/<rev>/` + `blobs/` structure the US-002 verifier walks and US-004
  materializes into). Declare `huggingface_hub` as a direct dependency in `pyproject.toml`
  + re-lock in the same commit (spec 2's `uv sync --locked` hard-fails on a mismatch, and
  the re-lock must keep `grep nvidia- uv.lock` empty; round-3 finding). Volume-ownership:
  Dockerfile volume prep only — an entrypoint `chown` is impossible, `USER poppy` precedes
  the ENTRYPOINT (round-3 correction).
- Token is runtime-optional; absence is a supported degraded mode. `HF_TOKEN` never appears in
  logs (assert).
- Loaded-state readback stays the per-request `_loaded` check (`retrieval_app.py:593-632`).

**Acceptance Criteria:**
- [ ] **(CI-checkable)** Empty volume + `HF_TOKEN` → fetch (pinned revision), verify via
      the US-002 verifier (already landed per `execution_order`), load;
      `promptguard_loaded: true` with `search_sanitization` advertised and
      `promptguard_unavailable` cleared, no restart — all with mocked transport.
      **(Live half — recorded at US-003's supervised session, which holds the real
      token):** start→loaded ≤ 5 min on the reference container (round-3: a live gated-HF
      measurement cannot sit in an unsupervised story's checkbox; spec 6's split pattern
      adopted).
- [ ] Lifespan startup yields immediately (dedicated **lifespan test harness** — the
      existing `client` fixture uses `ASGITransport`, which never fires lifespan events;
      round-3 finding); `/health` during fetch adds no latency beyond the pre-existing 2 s
      cache-reconnect floor (`cache.py:32` `_RECONNECT_TIMEOUT_S` — the bare "<1 s" was
      unmeetable in this spec's slot for cache reasons unrelated to the fetch; round-3
      critical), measured with a connected/fake cache.
- [ ] `FORAGE_MODEL_REVISION` respected end-to-end; the constant==manifest==mirror-tag
      lock test present; `sanitizer_revision` derivation includes the revision.
- [ ] `HF_TOKEN` never logged; no-token runs log no error from this path (the combined
      failure log is US-004's).
- [ ] Tests written/updated for new functionality (fetch layer mocked; suite stays hermetic)
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-002: Integrity manifest + quarantine (all sources)

**Priority:** P1

**Description:** As the platform owner, I want every weight set verified against a committed
sha256 manifest that is also a strict file allowlist, so tampered, partial, or
pickle-format weights are never loaded — regardless of source.

**Independent Test:** With fixture manifests: a matching snapshot verifies; a corrupted blob,
a missing file, an extra file, and a `pytorch_model.bin` are each refused, quarantined, and
reported.

**Implementation Hints:**
- `weights_manifest.json` committed at repo root: model id, **revision**, file list with
  per-file sha256 + sizes. Verification is **exact-set AND fail-closed on the manifest
  itself** (round-2 critical): a missing, empty, or unparseable manifest is a verification
  FAILURE (degraded, loudly logged) — never "nothing to verify"; the injectable manifest
  path exists for fixtures only, with the production path a constant. Missing file → fail;
  extra file → fail; hash mismatch → fail. The Dockerfile's explicit `COPY` list gains
  `weights_manifest.json` and `model_fetcher.py` (round-2 finding: the COPY is
  filename-enumerated, so the likely first-run state was an image shipping without either).
- **Format allowlist** (validation critical — the RCE closure): only
  `*.safetensors`/tokenizer/config files admitted; any `*.bin`/pickle artifact fails
  verification outright, and `from_pretrained` is called with `use_safetensors=True` so the
  loader can never fall back to `torch.load` even if verification were bypassed.
- Hash the **resolved bytes the loader opens**: walk the snapshot directory
  (`snapshots/<revision>/`) resolving HF's `blobs/` symlinks — not a naive flat-file walk over
  the whole cache (validation finding on HF's blob/symlink layout).
- Quarantine: move offending snapshot aside to `<cache>/quarantine/` — **bounded to one
  generation** (delete any previous quarantine first; repeated ~100 MB quarantines would fill
  the 1 GB container — validation finding); never inside the directory the loader scans.
- This story is **fixture-driven** (synthetic manifests + files); the real manifest is
  generated by US-003 — the verifier must therefore take the manifest path as an input, not
  hardcode it.
- Metrics: add a `model` section to `/metrics` (shape at `retrieval_app.py:635-676`) with
  `fetch_failures`, `verify_failures`, `quarantines` counters (names pinned here — validation
  asked for named counters).

**Acceptance Criteria:**
- [ ] Exact-set manifest verification (missing/extra/mismatch each covered by test);
      absent/empty/unparseable **manifest** fails closed (each covered by test);
      safetensors-only allowlist enforced AND `use_safetensors=True` at the loader;
      Dockerfile COPY carries the manifest + fetcher module.
- [ ] A tiny **loadable** safetensors fixture committed (a real minimal model
      `from_pretrained(use_safetensors=True)` can open) — US-004's mirror-to-load test
      consumes it (round-2 finding: synthetic byte fixtures can't exercise the loader).
- [ ] One verification code path with a single public entry point, designed for all three
      call sites (fixture-proven here; the HF/mirror/warm callers wire in as US-001/US-004/
      US-005 land — forward reference made explicit, round-2 finding).
- [ ] Quarantine bounded to one generation, outside the loader's scan tree; counters
      `model.fetch_failures` / `verify_failures` / `quarantines` exposed in `/metrics`.
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-003: Vendor the weights (ops, supervised)

**Priority:** P1

**Description:** As the platform owner, I want the pinned PromptGuard revision vendored to our
private GHCR artifact with the real manifest committed, so we hold vendor-cadence insurance
and US-004 has a mirror to fall back to.

**Independent Test:** On a clean machine, `oras pull ghcr.io/washingbearlabs/forage-weights:
<revision>` yields a tarball whose extracted snapshot passes US-002's verifier against the
committed `weights_manifest.json`.

**Implementation Hints:**
- `scripts/vendor_weights.py`: `snapshot_download(MODEL_ID, revision=<pinned>)` with the
  owner token → deterministic tar of the snapshot **with symlinks dereferenced**
  (`tarfile` `dereference=True` / `tar -h` — round-2 critical: every file in
  `snapshots/<rev>/` is a symlink into `blobs/`, so a naive tar ships dangling links and ~0
  bytes) → generate/refresh `weights_manifest.json` **with the safetensors allowlist
  enforced at generation time** (a manifest that blesses a `.bin` must be ungeneratable —
  round-2 finding) → `oras push` tagged by revision sha (token via env, never argv) → print
  the manifest diff → verify the pushed package is **private**.
- GHCR package stays **private** (Llama license would permit redistribution with attribution,
  but we don't need to be a weights distributor; NOTICE ships regardless).
- `docs/weights.md`: vendor/re-vendor procedure (bump `FORAGE_MODEL_REVISION` default +
  manifest + tag together), license rationale, read-token setup — including the **least-
  privilege note**: use a fine-grained PAT scoped to the single package if available;
  classic `read:packages` is account-wide (validation finding).
- Commits the real `weights_manifest.json` (US-002 shipped only fixtures).

**Acceptance Criteria:**
- [ ] Weights artifact at `ghcr.io/washingbearlabs/forage-weights:<revision>` (private,
      visibility verified post-push); committed `weights_manifest.json` matches a fresh pull
      via the US-002 verifier (proving the dereferenced tar carries real bytes).
- [ ] `scripts/vendor_weights.py` committed and green under the repo's lint/type gates;
      allowlist enforced at generation time; token via env, never argv or logs.
- [ ] `docs/weights.md` covers vendor/re-vendor, license rationale, mirror-token
      least-privilege setup (dedicated read-only GHCR PAT; account-wide classic-PAT caveat).
- [ ] **US-001's live timing half recorded here**: this supervised session (the one with a
      real token) measures container start → `promptguard_loaded: true` on the reference
      envelope and records ≤ 5 min in Implementation Notes (micro-verify: the deferral
      previously had no landing AC).

### US-004: Mirror fallback fetch

**Priority:** P1

**Description:** As an operator without HF access (or during an HF outage), I want Forage to
fetch verified weights from the GHCR mirror so the classifier loads from our own
infrastructure.

**Independent Test:** With HF unreachable (mocked) and mirror creds set, fetch pulls the
tarball, safe-extracts, verifies, and the classifier **loads**; with both sources failing, one
ERROR names both attempts and `/health` stays degraded.

**Implementation Hints:**
- Transport (decided 2026-09-02): ship the pinned `oras` **static binary** in the image and
  shell out — hand-rolling the OCI token-exchange/manifest/blob dance is a bigger diff than
  this spec's budget (validation recommendation). Env: `FORAGE_WEIGHTS_MIRROR` (default the
  GHCR ref) + `FORAGE_MIRROR_TOKEN`. Note: GHCR blob pulls require a bearer token exchange
  even with a PAT — `oras` handles it; that's the point.
- Ship `oras` **sha256-pinned per-arch** in the Dockerfile (`TARGETARCH`-selected download
  with per-arch checksums, exact version pinned — round-2 finding: the fetch tool must meet
  the same supply-chain bar as the weights it fetches, across both published platforms).
- TLS verification stays on (test-asserted: no insecure flag can reach the oras invocation);
  `FORAGE_WEIGHTS_MIRROR` validated https-scheme with no userinfo component; log the
  resolved source on every attempt **with any credential-bearing userinfo redacted** (the
  redaction ACs cover token values AND credential-bearing refs — round-2 finding).
- Staging dir bounded: cap = **2× manifest total + 10% slack** (tarball and its extraction
  coexist at peak — the round-2 "total + 10%" figure was arithmetically insufficient and
  would abort every real fetch if enforced; round-3 critical), cleanup on every failure
  path, **including huggingface_hub's own `$HF_HOME/xet/` staging tree** (verified present
  on the live sidecar).
- **Safe extraction**: `tarfile.extractall(..., filter="data")` explicitly (Python 3.12 still
  defaults to the unsafe filter — validation finding); extract to a temp dir, verify
  (US-002), then atomically move into the snapshot layout `from_pretrained` resolves.
- Order: HF (if token) → mirror (if creds) → give up loudly (single ERROR naming both, the
  `model.fetch_failures` counter increments). Each attempt logged with source/duration/
  outcome; **`FORAGE_MIRROR_TOKEN` never logged** (same redaction AC as `HF_TOKEN` —
  validation finding).
- Poppy-side plumbing of `FORAGE_MIRROR_TOKEN` into `.env.forage` is spec 6's US-003
  (cross-ref exists there) — without it the mirror has no production consumer.

**Acceptance Criteria:**
- [ ] Mirror-only fetch reaches a **loaded classifier** (end-to-end with mocked transport +
      real verifier + real loader path); HF-first order when both present.
- [ ] Extraction uses `filter="data"` into a temp dir; only verified sets reach the snapshot
      layout.
- [ ] Both-sources-failed: one ERROR naming both, counter incremented, degraded persists;
      neither token ever logged (test-asserted for both).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-005: Warm-start cache lifecycle + degraded-recovery retry

**Priority:** P1

**Description:** As an operator, I want warm starts to skip the network entirely and a
degraded classifier to keep retrying acquisition so transient failures self-heal.

**Independent Test:** Second start with a warm volume loads in <60 s with zero network
attempts (socket-guarded test + `local_files_only`); with fetch failing then succeeding, the
classifier loads on a later retry without restart.

**Implementation Hints:**
- Warm path: verify cache (US-002) → load with **`local_files_only=True`** — without it
  transformers still makes a Hub etag call on a full cache hit, breaking the zero-network
  goal (validation critical); assert with the hermetic socket guard, not a mock of our own
  module.
- Retry: background task, exponential backoff **30 s doubling to a 10 min cap, jittered
  ±20% (normative — an AC value, not an example)**; stops once loaded; **single-flight** —
  one acquisition in flight ever (lock shared with the lifespan start). Pre-agreed split
  point if this story overruns a session: warm-path verification/load vs. the retry task
  (round-2 sizing note). Mirror the naming/metrics shape of the sidecar's existing recovery machinery in
  `cache.py:234-271` (`_next_retry_at`/`_backoff_s`, `CacheMetrics`) — that's the in-repo
  precedent (validation suggestion); a real task is justified here (a fetch takes minutes;
  the cache got away with request-driven `ping_if_due` because a ping is cheap).
- Shutdown-clean: the task must cancel cleanly on lifespan teardown (AC below — this is the
  service's first background task; a leak hangs pytest).
- The `forage-model-cache` volume name is shared with spec 4's compose fragments — keep the
  literal in one doc place (`docs/weights.md`) both specs cite.

**Acceptance Criteria:**
- [ ] Warm start: zero network (socket-guarded + `local_files_only=True`), verified, loaded
      <60 s.
- [ ] Backoff retry (normative schedule) converges after transient failure without restart;
      the quarantine → re-fetch → loaded recovery path test-asserted end-to-end (round-2
      gap: claimed in Edge Cases with no AC); single-flight enforced; task cancels cleanly
      on shutdown (test-asserted).
- [ ] `docs/configuration.md` updated with the three new env vars (`FORAGE_MODEL_REVISION`,
      `FORAGE_WEIGHTS_MIRROR`, `FORAGE_MIRROR_TOKEN`) — spec 1's "every env var" doc
      (round-2 gap).
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes (`uv run pytest`)
- [ ] `uv run ruff check . && uv run pyright` passes

### US-006: Third-party token documentation

**Priority:** P2

**Description:** As a third-party Forage adopter, I want step-by-step docs for obtaining HF
access to the gated PromptGuard repo so I can reach `status: "healthy"` on my own.

**Independent Test:** Following `docs/weights.md` § "Bring your own token" from zero on a
clean machine reaches a loaded classifier.

**Implementation Hints:**
- Cover: HF account → request access to the gated repo (Meta approval) → fine-grained read
  token → `HF_TOKEN` env → degraded-until-fetched behavior → reading `/health`/`/metrics`
  while waiting → "Built with Llama" obligations.
- Document running **without** the model as a supported honest mode: what still works
  (extraction, structural scan, URL audit), what is withheld (`search_sanitization`), and
  that `POPPY_RETRIEVAL_LEGACY_CAPABILITY`/`FORAGE_LEGACY_CAPABILITY` is break-glass only —
  never the steady-state workaround for a missing token (validation note).
- Doc-only story.

**Acceptance Criteria:**
- [ ] `docs/weights.md` third-party section complete (token walk-through, degraded-mode
      expectations, break-glass caveat, license obligations); README links it.

## Edge Cases

- Fetch interrupted mid-download → partial snapshot fails exact-set verification on next
  start → quarantined + re-fetched (US-002/US-005).
- Volume not mounted → cache in the container layer; works but re-fetches per recreate —
  documented, not an error (US-005 docs note).
- `HF_TOKEN` revoked → 401/403 logged (status only, no token) → falls through to mirror
  (US-001/US-004).
- Disk full during fetch on the 1 GB container → loud failure, degraded persists, retry
  continues, quarantine bound prevents compounding (US-002/US-004).
- Upstream bumps the HF repo's `main` → irrelevant: fetch is revision-pinned; a deliberate
  re-vendor bumps revision + manifest + default together (US-001/US-003).
- Retry fires while initial acquisition is in flight → single-flight lock (US-005).

## Out of Scope

- PromptGuard 2 **86M** upgrade + contiguity gating — Epic 4, per the 2026-08-30 research;
  the fetcher is revision/model-parameterized so that's a manifest + constants change.
- Any wire-shape change — `/health` fields are Epic 1's, untouched; the `/metrics` `model`
  section is additive and `/metrics` is not part of the frozen response-model surface
  (spec 5 documents that boundary).
- The Poppy-side deploy-transition handling (cold-volume first deploy vs tier-1 gate) —
  **owned by spec 6 US-004/US-007**, cross-referenced there; this spec's job is that
  `/health` tells the truth throughout.
- Automatic re-download of new revisions.

## Assumptions

- The 283 MB (~270 MiB) safetensors set at the pinned revision fits the fetch-and-load
  budget of the 1-vCPU/1 GB reference container (today's baked image loads the same bytes in
  that envelope); the quarantine/staging bounds protect **volume disk**, which is
  independent of the 1024m RAM limit (round-2 correction of a conflated rationale).
- The pinned `oras` binary adds acceptably few MB to the image.
- Meta's terms permit the private mirror (Llama 4 Community License redistribution clause;
  NOTICE shipped).

## Technical Considerations

- Sequenced **US-002 → US-001 → US-003 → US-004 → US-005 → US-006** (frontmatter
  `execution_order` is authoritative; round-3 caught this prose contradicting it): the
  fixture-driven verifier lands first so the fetcher consumes it; vendoring precedes the
  mirror consumer. US-003 is supervised (and records US-001's live timing half).
- `/metrics` `model` section additions: extend the shared `client` fixture so the nine
  existing `/metrics` tests (all in `test_app.py`) keep passing (round-3 finding;
  micro-verify count), and include a
  `fetch_in_progress` boolean so operators (and spec 6's 600 s readiness wait) can
  distinguish "downloading" from "wedged" (round-3 finding).
- This spec runs **before** spec 4 in the Forage-local wrapper (sequential, not parallel —
  both touch `/metrics` and the golden fixtures; the Poppy epic wrapper's order is updated to
  3 → 4).

## Related Documentation

- Epic (this repo): [epic-forage-extraction-forage-side.md](epic-forage-extraction-forage-side.md)
- Planned in Poppy (canonical planning record, one-way sync — see the wrapper's Notes):
  [`epic-forage-extraction.md`](https://github.com/WashingBearLabs/Poppy/blob/main/kit_tools/specs/epic-forage-extraction.md) ·
  [`WEB_ACCESS_FAMILY.md`](https://github.com/WashingBearLabs/Poppy/blob/main/kit_tools/specs/WEB_ACCESS_FAMILY.md) (PromptGuard research section)

## Implementation Notes

## Refinement Notes

### Research Findings

**Decision:** Thread-offloaded fetch/load (`asyncio.to_thread`), never a bare task around sync
code
**Rationale:** `from_pretrained` is blocking network+torch work; the loop must keep serving
`/health` under a 10s/5-retry healthcheck. In-repo idiom: `stage3_promptguard.py:130-131`.
**Source:** validation round 1 (second-opinion + codebase-fit + salty, independently).

**Decision:** Manifest = exact-set allowlist + `use_safetensors=True`
**Rationale:** runtime fetch opens a supply-chain path the build-time bake never had;
`from_pretrained` falls back to pickle (`torch.load` → RCE) when safetensors is absent, and a
vendored manifest would faithfully bless a swapped `.bin`. One AC closes it.
**Source:** validation round 1 security critical; `promptguard/classifier.py:58-59` (no
`use_safetensors` today).

**Decision:** Mirror stores the HF snapshot tree as a tarball; `oras` binary shipped in-image
**Rationale:** one canonical on-disk layout for both sources (the fetcher materializes the
same snapshot `from_pretrained(revision=)` resolves), one manifest, one verifier; hand-rolled
OCI HTTP was rejected as a spec-budget blowout for a rarely-exercised fallback.
**Source:** validation round 1 (two-layouts critical + second-opinion oras recommendation).

**Decision:** Revision pinning via `FORAGE_MODEL_REVISION` + revision-hashed
`sanitizer_revision`
**Rationale:** unpinned `main` turns any upstream commit into quarantine-loop "corruption",
and identical revisions must imply identical sanitization behavior hashes.
**Source:** validation round 1; `sanitizer_revision.py:29` hashes `MODEL_ID` only today.

### Scope Adjustments

- 2026-09-02 validation round 1: restructured 5 → 6 stories (integrity split out of the
  mirror story, fixture-driven, re-ordered via `execution_order` so vendoring precedes the
  mirror consumer); fixed the `status: "ok"` literal family-wide; added revision pinning,
  safetensors enforcement, safe tar extraction, quarantine bounding, mirror-token redaction,
  shutdown-clean AC, and the honest cross-reference to spec 6 for deploy timing (the "no
  Poppy interaction" claim was withdrawn).

### Decisions Made

## Clarifications

### Session 2026-09-02
- Q: Where does the vendored weights mirror live? → A: Private GHCR OCI artifact
  (`forage-weights`); restic/SFTP rejected (SSH-key-in-container secret shape), both rejected
  (double maintenance for a ~yearly artifact).
- Q (round 1): oras binary or hand-rolled OCI client? → A: pinned oras static binary.
- Q (round 1): who owns the cold-volume-vs-tier-1-gate deploy transition? → A: spec 6
  (US-004 deploy wait + US-007 drill); this spec keeps `/health` honest throughout.
