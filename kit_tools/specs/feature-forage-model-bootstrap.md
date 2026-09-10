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
- [x] **(CI-checkable)** Empty volume + `HF_TOKEN` → fetch (pinned revision), verify via
      the US-002 verifier (already landed per `execution_order`), load;
      `promptguard_loaded: true` with `search_sanitization` advertised and
      `promptguard_unavailable` cleared, no restart — all with mocked transport.
      **(Live half — recorded at US-003's supervised session, which holds the real
      token):** start→loaded ≤ 5 min on the reference container (round-3: a live gated-HF
      measurement cannot sit in an unsupervised story's checkbox; spec 6's split pattern
      adopted).
- [x] Lifespan startup yields immediately (dedicated **lifespan test harness** — the
      existing `client` fixture uses `ASGITransport`, which never fires lifespan events;
      round-3 finding); `/health` during fetch adds no latency beyond the pre-existing 2 s
      cache-reconnect floor (`cache.py:32` `_RECONNECT_TIMEOUT_S` — the bare "<1 s" was
      unmeetable in this spec's slot for cache reasons unrelated to the fetch; round-3
      critical), measured with a connected/fake cache.
- [x] `FORAGE_MODEL_REVISION` respected end-to-end; the constant==manifest==mirror-tag
      lock test present; `sanitizer_revision` derivation includes the revision.
- [x] `HF_TOKEN` never logged; no-token runs log no error from this path (the combined
      failure log is US-004's).
- [x] Tests written/updated for new functionality (fetch layer mocked; suite stays hermetic)
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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
- [x] Exact-set manifest verification (missing/extra/mismatch each covered by test);
      absent/empty/unparseable **manifest** fails closed (each covered by test);
      safetensors-only allowlist enforced AND `use_safetensors=True` at the loader;
      Dockerfile COPY carries the manifest + fetcher module.
- [x] A tiny **loadable** safetensors fixture committed (a real minimal model
      `from_pretrained(use_safetensors=True)` can open) — US-004's mirror-to-load test
      consumes it (round-2 finding: synthetic byte fixtures can't exercise the loader).
- [x] One verification code path with a single public entry point, designed for all three
      call sites (fixture-proven here; the HF/mirror/warm callers wire in as US-001/US-004/
      US-005 land — forward reference made explicit, round-2 finding).
- [x] Quarantine bounded to one generation, outside the loader's scan tree; counters
      `model.fetch_failures` / `verify_failures` / `quarantines` exposed in `/metrics`.
- [x] Tests written/updated for new functionality
- [x] Full test suite passes (`uv run pytest`)
- [x] `uv run ruff check . && uv run pyright` passes

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
- **US-001 verification carry-forward:** `model.fetch_failures` does NOT increment on the
  no-token path (that is a *skip*, not a failure — correct for US-001's AC). The spec's
  Goals bullet promises the counter for the "neither source" case: making it move there is
  THIS story's job once the mirror leg exists — do not assume it already increments.
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
  that `FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` (alias `POPPY_RETRIEVAL_LEGACY_CAPABILITY`) is break-glass only —
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

### US-002: Integrity manifest + quarantine (2026-09-10)

**Shape.** `model_fetcher.py` at the repo root (flat layout, per `CLAUDE.md` invariant 3),
with exactly one public verification entry point:

```python
verify_weights(cache_root, *, manifest_path=MANIFEST_PATH, metrics=None) -> VerificationResult
```

`cache_root` is `$HF_HOME` (`/app/model-cache`), **not** `$HF_HUB_CACHE` — the `hub/`
level is added inside `snapshot_path()` so no call site has to remember it. US-001,
US-004 and US-005 all call this same function; `manifest_path` is injectable **for
fixtures only** and the production path is the module constant `MANIFEST_PATH`
(`tests/test_model_fetcher.py::TestSingleEntryPoint` asserts the default is that
constant and that the module reads no environment variable at all).

`VerificationResult` carries `ok`, every `failures` entry found (not just the first),
the parsed `manifest`, the `snapshot_dir`, and `quarantined_to`. Failures use a **closed
reason vocabulary** — `manifest_missing`, `manifest_empty`, `manifest_unparseable`,
`manifest_invalid`, `manifest_disallowed_format`, `snapshot_missing`, `file_missing`,
`file_extra`, `disallowed_format`, `size_mismatch`, `hash_mismatch`, `unreadable_file`,
`symlink_escape`, `disallowed_entry` — the same discipline `cache.py` applies to its
connection failures.

**`weights_manifest.json` ships as an honest fail-closed placeholder.** US-003 commits
the real one ("US-002 shipped only fixtures"), but the Dockerfile's `COPY` list gains the
filename in *this* story and a `COPY` of a non-existent path fails the build outright. So
the file is committed with `"files": []`, the pinned `model_id`/`revision`, and a
self-describing `_comment`. An empty file list is a **verification failure**
(`manifest_empty`), not "nothing to verify" — a manifest that pins nothing would happily
verify an empty snapshot, which is the exact hole this story exists to close.

**Interim runtime behaviour is unchanged, and that is deliberate.** Nothing calls
`verify_weights()` at boot yet — US-001 owns the fetch → verify → load wiring — so a
stock image still reports `degraded` with `promptguard_unavailable` because
`classifier.load()` cannot reach the gated repo. Live-verified on this branch: `docker
build` succeeds, `contract_smoke.py` against the running container reports **PASSED:
degraded, honest, and on-contract**, and the `secret-grep` pattern set finds nothing in
the image's layer history.

**Format allowlist → a binding contract for US-001/US-003.** `ALLOWED_SUFFIXES` is
`{.safetensors, .json, .txt, .model}` and is applied to the manifest's own entries as
well as to what is on disk, so a manifest that blesses a `pytorch_model.bin` fails with
`manifest_disallowed_format` even when the bytes match. The consequence the fetch stories
must honour: a plain `snapshot_download(MODEL_ID, revision=...)` also pulls `README.md`
and `.gitattributes`, and this verifier then refuses them as extra files. **Pass
`allow_patterns=model_fetcher.ALLOW_PATTERNS`** — the same constant, in
`huggingface_hub`'s pattern shape — so the fetch and the verification cannot disagree.
Recorded in `docs/GOTCHAS.md` as well, because it is the kind of thing that gets
rediscovered at the worst moment.

**Loader half of the closure.** `promptguard/classifier.py` now calls
`AutoModelForSequenceClassification.from_pretrained(MODEL_ID, use_safetensors=True)`.
`typings/transformers/__init__.pyi` grew the `use_safetensors` and `local_files_only`
keywords (the latter for US-005's zero-network warm start, and used by the fixture
tests). Editing `promptguard/classifier.py` does **not** rotate `sanitizer_revision` —
`_REVISION_SOURCES` hashes `pipeline/*.py` plus the `MODEL_ID` *string*, and neither
moved. Verified before and after: `0537316d83510dab3cfafb6ebd61dafdffa5d51be2dd2e01fb9777bfd0e3e253`,
unchanged, in the tree and in the running container. **US-001 owns the deliberate
rotation** when it hashes `MODEL_ID@revision`.

**Quarantine moves the whole model directory, not just `snapshots/<revision>/`.** The
spec says "the offending snapshot"; the implementation moves
`hub/models--<org>--<name>/` because the real bytes live in `blobs/` and
`huggingface_hub` treats a blob whose filename it already holds as cached *without
re-hashing it*. Leaving the blobs behind would let the next fetch re-link the same
corrupt bytes and quarantine them again, forever — the recovery path US-005 needs would
never converge. Destination is `<cache_root>/quarantine/`, a **sibling** of `hub/` and
never a child, bounded to one generation (the previous quarantine is deleted first).

**A manifest-level failure never quarantines.** If our own manifest is missing or
unparseable the weights may be perfectly good, and destroying a ~270 MiB download over
our bug is not a trade worth making. Only snapshot-level failures move anything.

**Symlink handling.** The walk uses `os.scandir` rather than `rglob`, resolves file
symlinks and hashes the resolved bytes, refuses *directory* symlinks outright (HF never
creates one; following one is how a walk both misses files and finds a cycle), and
refuses any link resolving outside the model's own cache directory
(`symlink_escape`) — which is the shape a hostile mirror tarball takes on US-004's
extraction path.

**The loadable fixture.** `tests/fixtures/tiny_model/` is a real 2-layer DeBERTa-v2
sequence classifier with random weights — 4 files, ~96 KB, `model.safetensors` only —
generated locally from configuration alone (no gated repo touched, no weights
downloaded). `tests/fixtures/README.md` carries the regeneration recipe.
`TestLoadableFixture` opens it with `from_pretrained(use_safetensors=True,
local_files_only=True)` under the autouse socket guard, which is what proves the load is
network-free, then materializes it into a real HF cache layout and runs it through the
verifier. US-004's mirror-to-load test consumes the same fixture.

**`/metrics` gained an additive `model` section** — `fetch_failures`, `verify_failures`,
`quarantines`, plus the `fetch_in_progress` boolean Technical Considerations asked for
(false until US-001 sets it, so US-001 does not have to re-shape the section). `/metrics`
is outside the frozen response-model surface, so no `CONTRACT_VERSION` bump: the golden
schema pins `HealthResponse`/`SearchResponse`/`RetrievedContent`/`ExtractedContent` only,
and `contract_smoke.py`'s `/metrics` check reads `contract_version` alone. `ModelMetrics`
lives in `model_fetcher.py` mirroring `CacheMetrics` in `cache.py`, and is initialised in
both the lifespan and the module-level `app.state` block.

**No dependency was added and `uv.lock` is untouched** — verification is stdlib
(`hashlib`, `json`, `os`, `shutil`). `huggingface_hub` becomes a direct dependency in
US-001, where it is actually called. `uv lock --check` is clean against the
`pyproject.toml` edit (the wheel `include` list gained `model_fetcher.py` and
`weights_manifest.json`); `grep nvidia- uv.lock` stays empty.

**Mutation-verified guards** (each applied to the committed tree, tests run, tree
restored):

| Mutation | Result |
|---|---|
| drop `use_safetensors=True` from the loader | `test_the_loader_is_pinned_to_safetensors` fails |
| add `.bin` to `ALLOWED_SUFFIXES` | 4 allowlist tests fail |
| quarantine keeps every generation | `test_quarantine_is_bounded_to_one_generation` fails |
| empty manifest treated as "nothing to verify" | 2 fail-closed tests fail |
| drop `weights_manifest.json` from the Dockerfile `COPY` | `test_runtime_file_is_copied[weights_manifest.json]` fails |
| disable symlink containment | `test_a_symlink_out_of_the_model_directory_is_refused` fails |
| remove the `/metrics` `model` section | 2 `test_app.py` tests fail |
| move the quarantine inside `hub/` | `test_the_quarantine_is_outside_the_hub_tree` fails |
| ignore extra files | 3 exact-set/allowlist tests fail |
| walk the whole model dir instead of the snapshot | every exact-set/fixture test fails |

One mutation — hashing through the symlink path rather than its resolved target — was
**not** caught, and correctly so: `open()` follows symlinks, so the two read identical
bytes. The property that actually matters (the walk root is `snapshots/<revision>/`, not
the cache tree) is covered by the last row and by
`test_unreferenced_blobs_are_not_part_of_the_set`.

**Suite:** 905 → 992 green (77 new in `tests/test_model_fetcher.py`, 2 in
`tests/test_app.py`, 8 in `tests/test_dockerfile.py`). `ruff check`, `ruff format
--check` and `pyright` (strict) all zero.

### US-001: Startup fetcher — HF source, pinned revision, non-blocking (2026-09-10)

**Shape.** `model_fetcher.py` grew the acquisition half it was designed for, so the module
is now the whole answer to "which bytes, from where, and are they the ones we pinned?":

```python
acquire_and_load(classifier, *, cache_root=None, revision=None,
                 manifest_path=None, metrics=None) -> bool
```

The pipeline is **verify what is cached → fetch → verify → load**, and every stop is a
logged `False`. `retrieval_app.lifespan` calls it as
`asyncio.create_task(asyncio.to_thread(...))` and keeps the handle on
`app.state.model_task`. The three keyword arguments default to the environment and the
committed constants; they exist so a test can drive the pipeline, not as a configuration
surface — `resolve_revision()`, `resolve_cache_root()` and the `MANIFEST_PATH` module
constant are what production uses.

**Yielding immediately is the requirement, and `to_thread` alone does not meet it.**
An `await asyncio.to_thread(...)` before the `yield` is still an await: uvicorn binds
nothing until lifespan startup returns, so a ~270 MiB download would hold the port closed
past the compose healthcheck's 10 s x 5 retries (no `start_period`) and the container
would restart-loop. `test_lifespan_startup_yields_immediately` parks the acquisition in
its worker thread for up to 10 s and asserts startup returned in under 2; the mutation
that replaces the task with an `await` fails 7 tests.

**A lifespan harness had to exist first.** The `client` fixture in `tests/test_app.py`
uses `httpx.ASGITransport`, which never fires lifespan events, so every startup assertion
would have passed vacuously against it. `_running_app()` enters the real `lifespan(app)`
with only `ContentCache` replaced. It is the only place in the suite where `app.state` is
populated by the service rather than by a fixture, and it is where the flip, the latency
and the shutdown tests live.

**`/health` latency during a fetch — measured, and reconciled with the AC.** The AC says
"no latency beyond the pre-existing 2 s cache-reconnect floor (`cache.py`'s
`_RECONNECT_TIMEOUT_S`), measured with a connected/fake cache", and those two halves pull
in opposite directions: with the cache **connected** there is no floor to spend, because
`ping_if_due()` returns without attempting anything. So the floor is not a budget this
story gets to use — it is context for why the spec could not say "<1 s" flatly in this
slot. The measurement is therefore the strict one, and it is not close: five sequential
`/health` calls with the fetch provably in flight (`fetch_in_progress` asserted true on
both sides of the loop) took **4.14, 0.28, 0.20, 0.18, 0.18 ms**. The first is FastAPI's
first-request path, not the fetch. The test asserts `max < 1.0 s` rather than the measured
figure, deliberately: a shared CI runner can stall a thread hand-off, and a bound tight
enough to catch a regression here would be tight enough to flake.

**The revision pin, and what "end-to-end" cost.** `DEFAULT_MODEL_REVISION` is
`11614a15…790ed0`, overridable by `FORAGE_MODEL_REVISION`, and the value is validated as a
40-character commit sha — an unusable override falls back to the pin with an ERROR that
does **not** echo the value (it is operator-supplied text heading for a log line, and it
is interpolated into a filesystem path). The revision reaches `snapshot_download`,
`verify_weights`'s snapshot walk, and both `from_pretrained` calls.

**The triple lock, honestly.** Two of the three legs exist and are locked
(`test_the_pin_is_locked_to_the_committed_manifest`): the constant equals the committed
manifest's `revision`, and the model ids match. US-002's placeholder already carried the
revision, so no manifest change was needed and its fail-closed `"files": []` emptiness is
untouched. **The third leg — the mirror tag
`ghcr.io/washingbearlabs/forage-weights:<revision>` — cannot be asserted yet because no
such tag exists.** US-003 creates it and owns closing that leg; the test's docstring says
so, in those terms, rather than pretending the lock is complete.

**`$HF_HOME/hub`, passed explicitly to both sides.** `hub_cache_dir()` is handed to
`snapshot_download(cache_dir=...)` *and* to `from_pretrained(cache_dir=...)`. Passing it
rather than relying on the environment is not belt-and-braces — it is necessary.
`huggingface_hub` samples `HF_HOME` into `constants.HF_HUB_CACHE` **at import time**, so a
process that sets the variable after import (which is every test, and any future
in-process reconfiguration) has the library reading one tree while the fetcher writes
another. Explicit `cache_dir` on both calls makes the two provably the same directory. The
round-3 critical stands and is guarded: `cache_dir=$HF_HOME` fails 9 tests, `local_dir=`
fails 11.

**Token handling.** `HF_TOKEN` is read at acquisition time, passed only to
`snapshot_download(token=...)`, and never logged. Download failures use a **closed reason
vocabulary** — `http_<status>`, `timeout`, `io_failed`, `fetch_failed` — the same
discipline `cache.py` applies to `VALKEY_URL`, and for a sharper reason than it looks:
`huggingface_hub`'s exceptions carry request context, so `logger.error(..., exc)` is a
credential leak waiting for the right exception. The guard test plants a token-shaped
literal *inside* the exception message and asserts it never reaches `caplog` at any level;
mutating the log line to `%s` the exception fails it.

**No token is a warning, not an error — and the AC's wording drove the control flow.**
"No-token runs log no error from this path" meant the fetch had to be *skipped*, not
attempted-and-failed, so `_acquire_and_load` checks the token before anything that could
log an ERROR and emits one WARNING (`weights_fetch_skipped`). Two consequences fell out
of taking that literally:

- **A cold cache is not a verification failure.** Calling `verify_weights()` on an empty
  volume would log `weights_verification_failed — snapshot_missing` at ERROR on every
  first boot, which is crying wolf at exactly the moment a real refusal needs to be
  readable. `_verify_cached()` returns `None` when the snapshot directory does not exist:
  there is nothing to verify yet, which is a different statement from "what is there is
  wrong".
- **A manifest-level refusal short-circuits.** If our own manifest cannot bless anything,
  neither a token nor another 270 MiB helps, so the pipeline stops with the refusal the
  verifier already logged. Without that branch a token-less boot on a broken manifest
  would report three causes — the refusal, "no HF_TOKEN", and "the pin blesses nothing" —
  two of which would send an operator looking for a credential. That is the property
  `test_a_manifest_level_refusal_diagnoses_one_cause_not_three` pins, and it is the test
  that closed the one mutation that initially escaped (see the table).

**The pre-flight pin check saves a doomed download.** With the placeholder manifest, a
container *with* a token would otherwise fetch ~270 MiB and then refuse it as
`manifest_empty`. `read_manifest_pin()` — a reader, not a second verification entry point;
it opens no weight file and logs nothing — is consulted before the fetch, and an
unusable pin is `weights_pin_unusable` with zero bytes transferred. Live-verified below.

**`local_files_only=True` at the load.** The exact file set was verified moments earlier,
so there is nothing left for the loader to look for, and an etag round-trip would make a
*loaded* classifier depend on the hub still being reachable. This is also what lets the
end-to-end test prove the load is network-free under the autouse socket guard. US-005 owns
the warm-start path proper (verify-then-load with no fetch at all, and the retry task);
the flag arriving here is the same flag, not that story's AC.

**The sanitizer_revision rotation, acknowledged and attributed.**

```
before: 0537316d83510dab3cfafb6ebd61dafdffa5d51be2dd2e01fb9777bfd0e3e253
after:  5927038d64ed54a619e52b94899148f56bfede37d38f3c1a53c435edc719d111
```

`derive_sanitizer_revision()` now hashes `MODEL_ID@revision` where it hashed `MODEL_ID`.
**Not one byte of a `_REVISION_SOURCES` file moved** — verified by re-deriving with the
old formula against the new tree and getting `0537316d…e3e253` back exactly, which makes
this the first rotation of the four attributable to an *input* rather than to a source
edit. The reason it had to happen: weights used to be a constant of the image, so the
model id described the model completely; they are a runtime, per-deployment input now, and
two containers running identical code can scan with different weights. A revision that
could not tell them apart would key a cache on a sanitization behaviour it does not
describe.

Propagated to every place that stated the old value: `CLAUDE.md`,
`docs/bootstrap-notes.md` (new fourth-rotation section with the before/after and the
reasoning), `kit_tools/docs/GOTCHAS.md` (table row + the "four rotations, and why the
fourth is a different kind" paragraph), `kit_tools/arch/CODE_ARCH.md`, and the epic
wrapper. `grep -rn 0537316d` now hits only archived specs and the historical rows that
should keep it. **Poppy-side blast radius, exactly as the spec words it:**
`stored_file_extractions` are re-extracted on next access; that transition is owned by
spec 6. `test_the_hashed_model_identity_is_model_id_at_revision` recomputes the whole
digest independently rather than asserting "it changed", so a future implementation that
hashed the two halves separately would fail.

**`fetch_in_progress` is real now.** Set before the download and cleared in a `finally`,
so it is false on the failure path too. `/metrics` exposes it unchanged from US-002's
shape — that section was built to accept this without re-shaping, and it did.

**Dependency.** `huggingface_hub>=0.30.0` is a direct dependency (it was already present
transitively via `transformers`; a module that imports a package must declare it), and
`uv lock` ran in the same commit — the lock gained exactly two lines and
`grep nvidia- uv.lock` stays empty. No Dockerfile change was needed: no new module, and
the dependency arrives through `uv sync --locked`.

**One local stub.** `typings/huggingface_hub/__init__.pyi` declares `snapshot_download`
and nothing else. Upstream annotates its `user_agent` parameter as a bare `dict`, which
makes the whole overload set partially unknown under strict pyright and the symbol
unusable at the call site. Per `typings/README.md` the stub carries only the five keywords
the fetcher passes. `typings/transformers/__init__.pyi` gained `revision` and `cache_dir`
on both `from_pretrained` factories, for the same reason it exists at all.

**A landed US-002 test was rewritten, deliberately.**
`test_the_production_manifest_path_is_not_configurable` asserted that `model_fetcher.py`
contained no `os.environ` and no `getenv` — a *proxy* for "the manifest path is not
configurable", and one that stopped being true the moment the module grew a revision pin
and a cache root. The proxy is replaced by three assertions that are strictly stronger:
the manifest constant is still the repo-root file, the exact set of environment variables
the module reads (parsed out of its own AST and resolved through its constants) is
`{FORAGE_MODEL_REVISION, HF_HOME, HF_TOKEN}`, and no read uses an inline string literal.
A fourth environment variable now fails a test rather than passing a substring check.

**Mutation-verified guards** (each applied to the committed tree, mapped tests run, tree
restored):

| Mutation | Result |
|---|---|
| lifespan `await`s the fetch instead of tasking it | 7 fail |
| shutdown leaves the acquisition task running | 1 fails |
| plain `snapshot_download`, no `allow_patterns` | 1 fails |
| `cache_dir=$HF_HOME` instead of `$HF_HOME/hub` | 9 fail |
| download uses `local_dir=` instead of the hub cache | 11 fail |
| `revision=` not passed to the download | 10 fail |
| fetch failure logs `str(exc)` | 2 fail |
| an unvalidated revision override is honoured | 6 fail |
| fetch attempted without a token | 3 fail |
| no manifest pre-check before the download | 1 fails |
| `fetch_in_progress` never set | 1 fails |
| the load is allowed to reach the hub (`local_files_only=False`) | 3 fail |
| a manifest-level refusal re-fetches anyway | **escaped**, then 1 fails |
| the verifier is called on a cold cache | 4 fail |
| sanitizer revision hashes `MODEL_ID` alone | 2 fail |
| classifier ignores the revision it is handed | 1 fails |
| `model_fetcher` reads a fourth environment variable | 1 fails |

The escape is the interesting row and is left in the table rather than tidied away. The
manifest short-circuit was masked by the pre-flight pin check downstream of it: both
refuse, so no test could tell them apart on outcome alone. They differ in *diagnosis*, and
the test added to close it asserts that difference (one cause logged, not three) rather
than re-asserting the outcome. 16 of 17 caught on the first pass; 17 of 17 after.

**Live-verified on a built image** (`docker build` + `docker run`, both branches):

| Run | Result |
|---|---|
| no token | `weights_fetch_skipped — no HF_TOKEN…` at WARNING, **zero ERROR lines**, `/health` degraded + `promptguard_unavailable`, volume untouched |
| `HF_TOKEN` set (fake), placeholder manifest | `weights_pin_unusable — /app/weights_manifest.json pins no verifiable file set…`, **no download attempted**, `/app/model-cache` still empty |
| both | `Application startup complete` precedes the acquisition line — startup yielded first — and `contract_smoke.py` reports **PASSED: degraded, honest, and on-contract** |

The live `/health` carries `sanitizer_revision: 5927038d…19d111`, so the rotation is
confirmed in the image and not only in the tree.

**What is CI-proven and what is not — plainly.** CI has no Hugging Face token and the
socket guard is autouse, so every fetch test drives a `snapshot_download` double. That
proves the arguments the fetcher passes, the tree the bytes land in, the verification, the
`false → true` flip, and a **real** `PromptGuardClassifier.load()` opening the result
(the committed tiny-model fixture, resolved through `MODEL_ID` + revision + `cache_dir`
exactly as the real weights will be — the socket guard is the proof it reached no
network). It does **not** prove that the gated repo answers, that
`11614a15…790ed0` exists upstream, or how long ~270 MiB takes on a 1-vCPU/1 GB container.
That is US-001's live half, and it lands at **US-003's supervised session** — which holds
the real token, vendors at this same sha, and has the AC to record the start→loaded
measurement. The AC checkbox above is worded that way for this reason; nothing here should
be read as having measured it.

**A gotcha found by the socket guard, and handed forward.** `huggingface_hub` 1.30 calls
`detect_agent()` while building request headers, which fetches a harness registry over
HTTP from `{ENDPOINT}/api/agent-harnesses` — on **every** hub call, including
`from_pretrained(..., local_files_only=True)` against a full cache. It is best-effort
(3 s timeout, all errors swallowed, cached 24 h at `$HF_HOME/.agent_harnesses.json`), so
nothing breaks and the load succeeds. But it is a real outbound attempt on the load path,
and **US-005's "zero network on a warm start" AC is measured against exactly this**:
`HF_HUB_OFFLINE=1` suppresses it, and can only be scoped to the warm-path load because it
would also disable the download. Recorded in `docs/GOTCHAS.md` with that trade spelled
out. The cache file lands in `$HF_HOME`, a sibling of `hub/`, so the verifier never sees
it — but a future check that assumes the volume holds only what we put there will.

**Shutdown, and one honest limitation.** The lifespan cancels `app.state.model_task` if it
is still pending. Cancelling the *task* stops the await; it does not stop the worker
thread, which `asyncio.to_thread` runs on the default executor, so an interpreter exit
during a live download still joins that thread. US-005 owns shutdown-clean for the retry
task and inherits the same seam; the container's stop grace period bounds the real-world
case. The test here asserts the task is cancelled, which is the part this story created.

**Docs updated with measured numbers, not derived ones.** `CODE_ARCH.md`'s module table
had drifted before this story (`cache.py` 425→456, `stage1_extraction.py` 297→337,
`stage2_structural.py` 218→304, `url_validator.py` 190→188); every row was re-measured
with `wc -l` while the table was open, and the Overview's "~5,750 lines" is now 6,353
service lines plus 1,146 of CI-only smoke drivers, stated as two figures because the
single number was ambiguous about which it meant.

**Suite:** 992 → 1048 green (+46 in `tests/test_model_fetcher.py`, +7 in
`tests/test_app.py`, +3 in `tests/test_sanitizer_revision.py`). `ruff check`,
`ruff format --check` and `pyright` (strict) all zero, with no new suppression — the one
place a test needed an exception carrying a `response` attribute uses a small local
exception class rather than the inline `pyright: ignore` that
`tests/test_pyright_policy.py` forbids.

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
