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
| **Current (`forage-ci-and-image` US-006 onward)** | **`0537316d…e3e253`** |

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
bug fix that strict typing surfaced: a multi-valued HTML attribute (bs4 hands those back
as `AttributeValueList`, not `str`) used to raise `AttributeError` out of
`tag[name].strip()` and now falls through to the next extraction strategy, which is what
the priority-ordered list already meant.

Five of the eight `_REVISION_SOURCES` files remain byte-identical to Poppy's copies;
`orchestrator.py` (hostname defaults), `stage2_structural.py` (formatting, then a type
argument) and `stage1_extraction.py` (typed metadata helpers) differ.

**Consequence for the Poppy-side specs: do not assume Poppy↔Forage revision parity.** A
consumer that compares `sanitizer_revision` across the two repos will see a mismatch that
means nothing. Compare `contract_version` instead — that is the field with cross-repo
semantics.

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

## Standing invariant while private

The pre-spec-2 `Dockerfile` still carries `ARG HF_TOKEN` (spec 2 removes it).
An image built *with* a token has that token recoverable via `docker history` —
never push such an image anywhere.
