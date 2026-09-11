# Bootstrap notes (US-001)

Record of the history-preserving split that created this repository, plus the
GitHub settings that could not be applied yet. Written by Poppy spec
`forage-repo-bootstrap`, story US-001, on 2026-09-07.

## Pin record

| Field | Value |
|-------|-------|
| Poppy source commit the split was taken from | `f73e209165e7c74a3a91024caf4ae8f0f9c0036a` (`f73e2091`) |
| Poppy branch | `epic/forage-extraction` (identical to `main` at split time) |
| Split tool | `git filter-repo` (build `a40bce54`) |
| Rewritten history | 47 commits, 45 tracked files |
| First rewritten commit | `025c626` — `feat(retrieval-sidecar): US-001 - Sidecar scaffold and Docker configuration` |
| Last rewritten commit | `93acb6a` — `feat(web-access-fail-loud): autonomous implementation (#85)` (Poppy `e54cbb13`) |

Any Poppy hotfix touching `services/retrieval/`, `config/searxng/`, or
`tests/retrieval/` **before spec 6 pins Forage** must be replayed onto this
repo by hand, and the SHA above updated. Until spec 6 lands, Poppy's in-tree
copy remains the deployed source of truth and the two copies coexist.

### Split command

Run on a throwaway clone of Poppy, never on the working checkout:

```
git filter-repo --prune-degenerate always \
  --path services/retrieval --path config/searxng --path tests/retrieval \
  --path-rename services/retrieval/: \
  --path-rename config/searxng/:searxng/config/ \
  --path-rename tests/retrieval/:tests/
```

`--prune-degenerate always` is load-bearing: without it five degenerate merge
commits survive and the count is 52 rather than 47.

### Verification measured at split time

| Check | Expected | Measured |
|-------|----------|----------|
| Commits (pre-bootstrap) | 47 | 47 |
| Root/service files | 25 | 25 |
| `searxng/config/` files | 2 | 2 |
| `tests/` files | 18 | 18 |
| `derive_sanitizer_revision({})` | same hash as Poppy | identical at split time (`e6b2b56d…54000`) — **since deliberately diverged, see below** |
| `docker build .` with no HF token | succeeds | succeeds; guard branch logged "No HF_TOKEN provided" |
| Full-history secret scan | clean | clean — see `bootstrap-scan.txt` |

### Sanitizer revision: deliberately diverged from Poppy (US-002, 2026-09-07)

The table row above records the state **at split time**, when the two repos still hashed
to the same value. That is no longer true, and the divergence is intentional.

`pipeline/sanitizer_revision.py` hashes eight source files. US-002's vault-free
configuration work edited `pipeline/orchestrator.py`'s default-hostname lines — one of
those eight — so Forage's revision moved:

| | Value |
|---|---|
| At split (`forage-repo-bootstrap` US-001), identical to Poppy | `e6b2b56d…54000` |
| After the vault-free hostname defaults (`forage-repo-bootstrap` US-002) | `2b8d7e9a…` |
| After the `ruff format` gate (`forage-ci-and-image` US-001) | `cd00a8b4…c96b9a` |
| After the pyright-strict burn-down (`forage-ci-and-image` US-006) | `0537316d…e3e253` |
| After the weights pin joined the hashed identity (`forage-model-bootstrap` US-001) | `5927038d…19d111` |
| After the contract bump to `1.1.0` (`forage-cache-fallback` US-003) | `fa4691c5…93547c` |
| **Current (`forage-contract` US-001, error vocabulary; contract still `1.1.0`)** | **`8b1b7f78…196d7c`** |

The second rotation is **format-only**: installing the `ruff format --check` CI gate meant
burning the six-file backlog to zero, and one of those six —
`pipeline/stage2_structural.py` — is a `_REVISION_SOURCES` member. No sanitization
*behaviour* changed; the hash moved because the hash is over bytes. It was done at gate
installation on purpose, so the rotation happens once, early, and under a recorded
before/after rather than incidentally inside a later behavioural change.

The third rotation is the pyright-strict burn-down (US-006), taken on the same principle
and at the same kind of boundary. Two `_REVISION_SOURCES` members were touched:
`stage2_structural.py` gained a type argument on one `field(default_factory=...)`, and
`stage1_extraction.py`'s metadata extraction moved behind two typed helpers,
`_attr_text()` and `_json_ld_documents()`. Behaviour is preserved, with one deliberate
defensive hardening strict typing surfaced (verifier: not reproducible as a live bug — no call site reads a multi-valued attribute; differential-tested 630 docs, zero mismatches): a multi-valued HTML attribute (bs4 hands those back
as `AttributeValueList`, not `str`) used to raise `AttributeError` out of
`tag[name].strip()` and now falls through to the next extraction strategy, which is what
the priority-ordered list already meant.

Five of the eight `_REVISION_SOURCES` files remain byte-identical to Poppy's copies;
`orchestrator.py` (hostname defaults), `stage2_structural.py` (formatting, then a type
argument) and `stage1_extraction.py` (typed metadata helpers) differ.

**No rotation across the rest of `forage-ci-and-image`.** US-002, US-003 and US-005 all
left the revision at `0537316d…e3e253`, verified before and after each story: none of the
CI test lane, the Dockerfile rework or the contract smoke touches a `_REVISION_SOURCES`
file (all eight live under `pipeline/`). `forage-model-bootstrap` US-002 left it there
too. Recorded because the stories around them moved it, and silence would be ambiguous —
"unchanged" is a measurement here, not an omission.

US-005 is the story where that stability became *observable from outside*: the smoke job
reads `sanitizer_revision` off a running container's `/health` and fails if it is empty
or the `"unknown"` fallback, so a build that silently lost its ability to derive one is
now a red gate rather than a field nobody looks at. The value the CI run reported —
`0537316d83510dab…e3e253` — is the same one that tree derived locally.

### The fourth rotation: the weights pin joined the hash (`forage-model-bootstrap` US-001, 2026-09-10)

```
before: 0537316d83510dab3cfafb6ebd61dafdffa5d51be2dd2e01fb9777bfd0e3e253
after:  5927038d64ed54a619e52b94899148f56bfede37d38f3c1a53c435edc719d111
```

**Attribution: not one byte of a `_REVISION_SOURCES` file moved.** All eight are
byte-identical across this change, verified by re-deriving with the *old* formula against
the *new* tree and getting `0537316d…e3e253` back exactly. The whole delta is one changed
input: `derive_sanitizer_revision()` now hashes `MODEL_ID@revision` where it hashed
`MODEL_ID` alone.

**Why the rotation was taken, rather than avoided.** Until this story the weights were a
constant of the image: one `MODEL_ID`, baked, identical everywhere, so hashing the id
alone described the model completely. They are a *runtime* input now — fetched at start,
pinned by commit sha, and overridable per-deployment with `FORAGE_MODEL_REVISION`. Two
containers running byte-identical code can therefore be scanning with different weights,
and a `sanitizer_revision` that could not tell them apart would be keying a cache on a
sanitization behaviour it does not actually describe. That is the precise failure this
value exists to prevent, so the input had to change; the rotation is the price.

Taken here on the same principle as the previous three: at a boundary, deliberately, with
the before/after measured — and in the one story where the *reason* for it is the story's
own subject, rather than as a drive-by inside something else.

**Blast radius.** Every cached sanitization keyed on the old revision is invalidated, in
this repo and in the consuming one. Poppy's `stored_file_extractions` are re-extracted on
next access; that transition is **owned by spec 6**, which pins Poppy to a published
Forage image and is the place the two sides meet. Nothing here needs to do anything about
it — the value is opaque and the consumer treats a change as "re-extract", which is
correct behaviour, not damage.

**And Poppy's copy has not moved.** It still derives the split-time value. The rule in the
next paragraph applies with one more reason behind it: Forage's revision now depends on an
environment variable Poppy's copy does not read.

**Consequence for the Poppy-side specs: do not assume Poppy↔Forage revision parity.** A
consumer that compares `sanitizer_revision` across the two repos will see a mismatch that
means nothing. Compare `contract_version` instead — that is the field with cross-repo
semantics.

### The fifth rotation: the contract bump to `1.1.0` (`forage-cache-fallback` US-003, 2026-09-10)

```
before: 5927038d64ed54a619e52b94899148f56bfede37d38f3c1a53c435edc719d111
after:  fa4691c57449c52fe367208bdeeb650cbe474d93485c3b90cc8989b5e593547c
```

**Attribution: two `_REVISION_SOURCES` files moved, and both were load-bearing.** Measured
by re-deriving with each edit reverted in turn, which is the only way to say this rather
than assume it:

| Tree | Derived |
|---|---|
| Both files at their previous state | `5927038d…19d111` |
| Only `contract.py` reverted | `b9b716c3…5151a12` |
| Only `orchestrator.py` reverted | `7bfbeed5…1437f0` |
| *(supervisor correction 2026-09-13: the two intermediate values above were transposed as first recorded — the verifier's independent re-derivation fixed the labels; conclusion unchanged, both edits load-bearing)* | |
| Both edits (shipped) | **`fa4691c5…93547c`** |

- `pipeline/contract.py` — `CONTRACT_VERSION` `1.0.0` → `1.1.0` for the additive
  `/health` field `cache_backend`, plus the version history the docstring now carries.
- `pipeline/orchestrator.py` — `run_retrieve_pipeline` takes the derived revision and
  passes it to `cache_policy_fingerprint()`.

**This one is a different kind again, and it is worth naming.** The first three rotations
were byte churn (formatting, typing) and the fourth was a changed input; in all four the
invalidation was the *price* of the change. Here it is the *point*. `contract.py`'s own
docstring says a contract change must invalidate cached extractions sanitized under the
old contract — and nothing was enforcing that inside Forage: `cache_key()` /
`cache_policy_fingerprint()` mixed in every caller-supplied policy knob but not the
pipeline's own revision, so a deploy that rotated the revision kept serving entries the
previous pipeline had sanitized, for up to a full TTL, stamped as if they were current.
US-003 closes it by making the revision an input to the fingerprint, so this rotation is
also the first one that *actually* flushes Forage's own cache.

**No sanitization behaviour changed.** Stages 1-4 are untouched; the orchestrator edit
adds a keyword argument and threads it to the cache key, and the contract edit is a
version string and prose. What changed is what the cache considers the same content.

**Blast radius.** Forage-side: every live cache entry becomes unreachable at the next
start and ages out on its own TTL — free in memory mode (nothing survives a restart
anyway) and one TTL of extra fetches in Valkey mode. Consumer-side: Poppy's
`stored_file_extractions` are keyed on this value and are re-extracted on next access.
That is the same transition the fourth rotation already handed to the consuming repo's
spec 6, which owns it; nothing new is owed here.

### The sixth rotation: the error vocabulary joined the contract (`forage-contract` US-001, 2026-09-11)

```
before: fa4691c57449c52fe367208bdeeb650cbe474d93485c3b90cc8989b5e593547c
after:  8b1b7f78e85f733ef3b8ace5194632a8cf92410131b2c1af995c456f20196d7c
```

**A note on counting, because two documents number these differently and both are
right.** This file counts *moves*, so this is the sixth. `kit_tools/specs/epic-forage-extraction-forage-side.md`
counts *values*, starting from the at-split hash, so the same event is its **seventh**.
The values themselves are the unambiguous reference; prefer them over any ordinal.

**Attribution: exactly one `_REVISION_SOURCES` file moved — `pipeline/contract.py`.**
Measured, not assumed, by re-deriving with that file swapped back to `main`'s bytes:

| Tree | Derived |
|---|---|
| All eight sources at `main` (control) | `fa4691c5…93547c` |
| Only `contract.py` reverted to `main`, everything else as shipped | `fa4691c5…93547c` |
| The shipped tree | **`8b1b7f78…196d7c`** |

The control and the single-revert row agreeing *is* the attribution: with `contract.py`
put back the hash returns to the pre-story value exactly, so nothing else in the story
touched a hashed file. Read that as one measurement with one conclusion — unlike the
fifth rotation, where two files moved and the two intermediate values had to be kept
straight (they were transposed when first recorded, and a re-derivation fixed the
labels). There is no intermediate to transpose here.

The story's other edits — the mirror models and `responses=` declarations in
`retrieval_app.py`, the `omitted_by_reason` description in `models.py`, the new
`tests/test_contract_errors.py`, the regenerated golden fixture — are all outside
`pipeline/`, and `_REVISION_SOURCES` resolves relative to that directory. None of them
can move the hash, by construction.

**What moved inside `contract.py`.** The seventeen-code error vocabulary and the
`DegradedReason` alias, as `Literal` types with their frozensets derived from them
(`frozenset(get_args(...))`), plus the docstrings that explain each site's shape. No
constant changed value: `DEGRADED_REASONS` holds the same two strings it always did, and
`CONTRACT_VERSION` stays **`1.1.0`** under the documentation-only ruling — the whole
story is parity-tested to change zero wire bytes
(`tests/test_contract_errors.py`).

**Why the rotation was taken here, rather than avoided.** `contract.py` is where every
wire literal in this service is defined exactly once, and putting the error vocabulary
anywhere else to dodge the hash would have been the tail wagging the dog. The spec
acknowledged one rotation for the whole of `feature-forage-contract` and this is it: the
remaining stories (US-005, US-002, US-003, US-004) must either stay out of
`_REVISION_SOURCES` files or be content-neutral in them, so the shipped contract carries
one revision rather than five.

**Blast radius, and the part that is deliberate.** `cache_policy_fingerprint()` takes
the revision as an input since the fifth rotation, so every cached extraction keyed on
`fa4691c5…` becomes unreachable at the next start and ages out on its own TTL. That is
**correct behaviour, not collateral damage** — it is the mechanism the fifth rotation
installed on purpose, and a documentation pass that rotated the hash without flushing
the cache would be the broken outcome. Free in memory mode; one TTL of extra fetches in
Valkey mode. Consumer-side is unchanged from the fourth and fifth rotations: Poppy
re-extracts on next access, and spec 6 owns that transition.

## Deferred GitHub settings — for the spec 2 public flip

The `WashingBearLabs` org is on the **GitHub Free** plan, and this repository
is **private** for the whole of the bootstrap spec. Three settings the spec
asked for are unavailable under that combination and are deferred to spec 2's
public flip, where all three become free:

| Setting | API result 2026-09-07 | Action at public flip |
|---------|----------------------|-----------------------|
| Secret scanning | `422 Secret scanning is not available for this repository.` | `PATCH /repos/:o/:r` `security_and_analysis.secret_scanning.status=enabled` |
| Secret scanning push protection | same 422 (depends on secret scanning) | same PATCH, `secret_scanning_push_protection.status=enabled` |
| Branch protection on `main` | `403 Upgrade to GitHub Pro or make this repository public to enable this feature.` — both the classic `branches/main/protection` API and the newer `rulesets` API | PRs required, **no required status checks**, `required_approving_review_count: 0` |

Two notes for whoever applies them:

- **Do not set a nonzero required approving review count.** GitHub forbids
  self-approval, so any nonzero count deadlocks this spec's self-merged PRs and
  spec 2's autonomous server-side merges.
- **Do not add required status checks** until spec 2's CI exists — required
  checks that never report will deadlock every merge.

Until protection exists, the compensating control is the local
`gitleaks` full-history scan recorded in `bootstrap-scan.txt`, re-run before
the public flip.

### Required status checks — attempted and deferred (spec 2 US-007, 2026-09-08)

The "not until CI exists" condition above is now discharged: all six jobs
exist and report. Registration was **attempted and refused**, so it joins the
deferred list rather than being marked done.

| Attempt | Actor | Result |
|---|---|---|
| `PUT /repos/WashingBearLabs/Forage/branches/main/protection` with the six contexts | `wblabs001` — repo **ADMIN**, org token scoped `admin:org, repo, workflow, gist` | `403 Upgrade to GitHub Pro or make this repository public to enable this feature.` |
| `POST /repos/WashingBearLabs/Forage/rulesets` (the newer API) | same | same 403 |
| `GET /orgs/WashingBearLabs/rulesets` (org-level fallback) | same | `403 Upgrade to GitHub Team to enable this feature.` |

**A correction worth carrying**, because the spec's hint said otherwise: this
is not a *permissions* problem, so no credential fixes it. The hint read
"branch-protection edits need admin — a fine-grained PAT or the supervisor by
hand; `GITHUB_TOKEN` cannot". An admin token was used and got the same 403.
The blocker is the **plan**, not the actor: private repositories on GitHub Free
have no branch protection at all. Minting a PAT for this would have bought
nothing, and none was created.

**Apply at the public flip, alongside the three settings above:** required
status checks `lint`, `typecheck`, `test`, `build-amd64`, `secret-grep`,
`smoke` (six contexts — `actionlint` is a *step* inside `lint`, not a separate
job, so it must not be listed), `strict: false`, and
`required_approving_review_count: 0` per the first note above.

The compensating control in the meantime is stronger than it sounds and worth
stating plainly: the release gates are `needs:` edges inside one workflow file,
not merge policy. Required checks would stop a human merging over red; the
`needs:` chain stops anything *publishing* over red, which is the property that
actually protects consumers. See [`releases.md`](releases.md).

## Lint / type backlog handed to spec 2 (measured US-004, 2026-09-07)

US-004 fixed the ruff `check` errors and left the two backlogs below to
`feature-forage-ci-and-image`. All three numbers come from the toolchain the
committed `uv.lock` pins (ruff 0.16.6, pyright 1.1.411) — measure with
`uv run`, not a system tool, or the numbers will not reproduce.

| Lane | State at US-004 close | Owner |
|------|----------------------|-------|
| `uv run ruff check .` | **clean** (0 errors) | fixed here |
| `uv run ruff format --check .` | **6 files** would be reformatted: `pipeline/stage2_structural.py`, `pipeline/stage5_url_audit.py`, `url_validator.py`, `tests/test_stage2_structural.py`, `tests/test_stage5_url_audit.py`, `tests/test_url_validator.py` | spec 2 |
| `uv run pyright` (strict) | **214 errors** — 22 in service code (4 files: `pipeline/stage1_extraction.py`, `pipeline/stage2_structural.py`, `pipeline/stage5_url_audit.py`, `promptguard/classifier.py`), 192 in tests (incl. 35 `reportPrivateUsage`) | spec 2 |

**All three lanes are now closed and blocking in CI.** `ruff check` + `ruff format`
(spec 2 US-001, 2026-09-07) and `pyright` (spec 2 US-006, same day). One correction to the
214 figure for the record: it was measured with pyright honouring the 30 inherited
type-ignore comments the code carried. With those disabled — which is how the burn-down
was done, and how CI runs now — the real backlog was **269 errors** (57 service, 212
tests). Both numbers are true; the second is the one that was actually paid down.

Two measurement notes for spec 2's planning:

- The planning-time estimate (~34 service / ~280 test errors) was taken before
  a working `uv sync` existed. With dependencies actually installed, most
  missing-stub errors resolve; 214 is the real number.
- Ruff's own rule set moves between releases. The planning-time count of 22
  `check` errors was measured on ruff 0.8; on the locked 0.16.6 the set differs
  (`UP038` was removed, `RUF059` added). Pinning ruff in CI — the lock already
  does for `uv run` — is what keeps this lane reproducible.

## Standing invariant while private — DISCHARGED for this repo (2026-09-07)

This section used to read: "the pre-spec-2 `Dockerfile` still carries `ARG HF_TOKEN`
(spec 2 removes it). An image built *with* a token has that token recoverable via
`docker history` — never push such an image anywhere."

Spec 2 US-003 removed it. `Dockerfile` now declares no `ARG` at all, installs from the
committed `uv.lock`, and pins its base by digest; `tests/test_dockerfile.py` guards the
source text and CI's `secret-grep` job greps the built image's layer history on every run.
An image built from this repository can no longer carry a build-time credential.

Two boundaries the discharge does not cross:

- **Publishing is gated, and now live.** Secret-free is not public. US-007's `publish`
  lane shipped 2026-09-08 and has published `sha-e45f70f` and `0.9.0-rc`; every push runs
  behind all six gates plus a layer-identity check (`docs/releases.md`). The repository
  and both GHCR packages have been PUBLIC since US-008's 2026-09-10 flip.
- **Poppy's in-tree copy is unchanged.** `services/retrieval/Dockerfile` in the monorepo
  still carries `ARG HF_TOKEN`, and the two copies coexist until spec 6 pins Poppy to a
  published image. The old rule still applies there, verbatim.
