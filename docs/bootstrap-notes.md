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
| `derive_sanitizer_revision({})` | same hash as Poppy | identical (`e6b2b56d…54000`) |
| `docker build .` with no HF token | succeeds | succeeds; guard branch logged "No HF_TOKEN provided" |
| Full-history secret scan | clean | clean — see `bootstrap-scan.txt` |

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

## Standing invariant while private

The pre-spec-2 `Dockerfile` still carries `ARG HF_TOKEN` (spec 2 removes it).
An image built *with* a token has that token recoverable via `docker history` —
never push such an image anywhere.
