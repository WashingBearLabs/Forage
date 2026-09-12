# Releases and published images

How a Forage version becomes a pullable image, what each tag promises, and what
the gate chain does and does not cover. Written by `forage-ci-and-image` US-007.

The service image is **`ghcr.io/washingbearlabs/forage`**. The companion SearXNG
image (`ghcr.io/washingbearlabs/forage-searxng`) has its own independent tag
lane and is documented separately in `docs/searxng.md` (US-004).

> **Public since US-008's 2026-09-10 flip** — repository and packages; anonymous
> pulls verified at the gate.

## Withdrawn tags

A publish run that fails the layer-identity gate leaves its tag pointing at an image
whose layers are NOT the ones `smoke` executed — the workflow's own doctrine calls that
tag untrusted. The rule, set when `v0.9.2-rc` did exactly this on a cold cache
(2026-09-11): **withdraw the git tag AND delete the GHCR package version.** Deleting the
version needs `delete:packages` (obtained ad hoc, dropped after); until both halves are
done the registry is advertising an ungated image, which is worse than none.
`0.9.2-rc`'s package version is pending deletion under this rule.

## The tag scheme

| You push | The registry gets | A GitHub Release? |
|---|---|---|
| `v1.2.3` | `1.2.3`, `1.2`, `latest` | yes |
| `v1.2.3-rc`, `v0.9.0-rc.2`, any tag with a `-` | `1.2.3-rc` only | yes, flagged **pre-release** |
| a commit on `main` | `sha-<short>` | no |
| `searxng-v*` | nothing on this image (US-004's lane) | no |

Four rules, one per line, in `.github/workflows/ci.yml`'s `docker/metadata-action`
step. Each carries its own `enable=` condition rather than leaning on the
action's `latest=auto` heuristic or on a `type=semver` rule quietly emitting
nothing for a branch ref: `tests/test_ci_workflow.py` *evaluates* those
conditions against sample refs, so the table above is a test, not a comment.

### The git tag is the version

There is no version string in the image, no `VERSION` file to bump, and
`pyproject.toml`'s `version = "0.1.0"` is packaging metadata that nothing in
the release path reads. **The tag you push is the version**, and `v` is a
prefix on the git ref only — the image tag is the bare semver.

The consequence worth stating: you cannot re-cut a version. Registry tags are
mutable in principle, but re-pushing `v1.2.3` at a different commit would leave
consumers holding a digest that no longer matches the tag they pinned. Cut a
new patch instead.

### Pre-releases move nothing consumers follow

A tag containing `-` is a semver pre-release. It publishes its exact version
and **neither `latest` nor the `X.Y` alias**, because both are pointers a
consumer follows and a release candidate is by definition not something to be
followed into. The corresponding GitHub Release is created with
`--prerelease`, which also keeps GitHub's own "latest release" pointer off it.

`latest` therefore **does not exist yet**. It starts existing at the first
non-pre-release `v*` tag, which `feature-forage-contract` puts at `v1.0.0`
once the OpenAPI contract is frozen. Everything before that is `0.x` or `-rc`.

## What has to be green first

`publish` declares `needs: [lint, typecheck, test, build-amd64, secret-grep,
smoke]` — every other job in the workflow. This is the reason all of them live
in one file: `needs:` cannot span workflow files, so a split would turn the
release gates into wishful thinking.

A tag pushed onto a red tree still *runs* the gates. They fail, and `publish`
never starts.

## Architectures

| Platform | Built | Gated | Notes |
|---|---|---|---|
| `linux/amd64` | yes | **yes** | built by `build-amd64`, history-scanned by `secret-grep`, executed by `smoke`, and verified layer-for-layer at publish time |
| `linux/arm64` | yes, under qemu emulation | **no** | same commit, same lock, same digest-pinned base — and nothing in CI has ever run it |

That asymmetry is deliberate and is the honest shape of the pipeline, so it is
written here rather than left for a consumer to discover. If you run Forage on
arm64, the image you pull has been *built* from gated sources but has not been
*executed* by anything before you. The cheapest way to close that today is to
run the committed `contract_smoke.py` against your own container:

```
uv run python contract_smoke.py --base-url http://127.0.0.1:8020
```

### The amd64 identity chain

A multi-arch push cannot ship the artifact the gates inspected: buildx builds a
manifest list across platforms, and `docker load` puts a single-platform image
in a daemon buildx does not push from. So the amd64 leg is **rebuilt** at
publish time — from `type=gha`, the cache `build-amd64` wrote with `mode=max`
earlier in the same run, at the same commit.

"Rebuilt from the same cache" is a claim, and the job checks it rather than
asserting it. `publish` downloads the same artifact `smoke` ran, reads its
`RootFS.Layers` (the diff IDs — the sha256 of each layer's *uncompressed* tar,
i.e. the filesystem itself), and after the push reads the published amd64
manifest's `rootfs.diff_ids` back out of the registry. They must be equal,
layer for layer and in order. Equal diff IDs mean an identical filesystem:
same base, same wheels, same source.

What it deliberately does not claim is that the published image *is* the loaded
one — the image IDs differ, legitimately, because the config records the
exporter's own metadata. Asserting on those would be asserting on a storage
detail.

### The emulation budget

Measured 2026-09-08 on a GitHub-hosted `ubuntu-latest` runner, cold — no cache
of any kind, every layer built:

| Step | Time |
|---|---|
| `apt-get install curl` | 44 s |
| `uv sync --locked --no-dev` (torch 151.9 MiB aarch64 wheel, 66 packages) | 39 s |
| `RUN python -c "import retrieval_app"` — the genuinely emulated CPU work | 54 s |
| base pull, context, rest | ~5 s |
| **total** | **2 m 22 s** |

Against a 30-minute feasibility budget, so multi-arch went ahead with no
amd64-only fallback. It is cheap because nothing in this image is *compiled* —
it is a wheel install, and qemu only slows the interpreter work.

**If that ever stops being true**, the fallback is one line:
`PUBLISH_PLATFORMS` in the workflow's `env:` block. Trim it to
`linux/amd64`, and the QEMU setup step drops out on its own (it is conditioned
on the platform list) and the verification step keeps working (it handles a
single-platform manifest). Update the table above in the same commit —
`tests/test_ci_workflow.py` ties the built platform list to that one variable,
but nothing can make it tie the documentation to reality except doing it.

The arm64 layers live in their own GHA cache scope (`scope=publish`). They
cannot share `build-amd64`'s: that job writes an amd64-only manifest on every
run, so a shared scope would evict the emulated layers each time and pay the
cold cost forever.

## What a GitHub Release means

The Release is created **last** — after the push and after the layer
verification. That ordering is the contract: *a Release implies a pullable,
gated image*. The reverse is not promised and does not need to be; a `main`
push produces a `sha-` image and no Release at all.

### The body says which contract the image serves

Since `feature-forage-contract` US-003 every Release body carries a line:

```
contract: 1.1.0
```

The image tag and the wire contract are **independent semvers**
([`contract/GOVERNANCE.md`](../contract/GOVERNANCE.md)), so the mapping between
them has to be stated — and it is stated mechanically rather than typed by a
human. Three steps in `publish` do it, on `v*` tags only:

1. `Read the contract version from the tagged tree` parses `CONTRACT_VERSION`
   out of the tagged commit's `pipeline/contract.py`;
2. `Create the GitHub Release` writes the body from that value;
3. `Assert the Release body advertises the tagged tree's contract version`
   reads the published body back with `gh release view` and fails the run on an
   anchored regex miss.

Emitting and asserting off one value in one job is the point: the claim and its
check cannot drift apart. No version literal appears anywhere in that job's
shell, and `tests/test_ci_workflow.py::TestReleaseContractMapping` keeps it that
way.

**If step 3 fails**, the image is pushed and the Release exists but advertises
the wrong contract — a red run with a wrong Release rather than a missing one.
`gh release create` refuses a tag that already has a Release, so recovery is
`gh release edit "$TAG" --notes ...` with the corrected body, or deleting the
Release and re-running the job.

### The Release carries the contract itself

Since `feature-forage-contract` US-004 every `v*` Release has two assets:

| Asset | What it is |
|---|---|
| `openapi.yaml` | the frozen wire contract at that tag |
| `openapi.yaml.sha256` | its committed anchor — a `sha256sum`-format line naming the basename, so it verifies from any directory |

They are attached by `gh release create` itself, not by a later upload step, so a
Release that exists without them is a failed step rather than a quietly
incomplete Release.

**A Release asset is mutable.** Anyone with write access can replace one
afterwards and nothing on the Release records it. That is the whole reason the
anchor is *committed at the tag*, and why
[`contract/GOVERNANCE.md`](../contract/GOVERNANCE.md)'s Consumers section tells
you to verify against it rather than against another copy. The publish job runs
that verification against the freshly published assets — downloading them back
out of the API into a scratch directory, comparing the downloaded anchor against
the committed one, then running the same `sha256sum -c` a consumer would. The
comparison against the *committed* anchor is the load-bearing half: a tampered
document and anchor replaced together verify perfectly against each other.

The third copy — `/app/contract/openapi.yaml` inside the image — is checked by
the `smoke` job on every run, against the same anchor and against the running
container's own `contract_version`.

## Reproducible builds

`publish` rebuilds the amd64 leg and then asserts its layers are the ones
`smoke` executed. Until US-004 that assertion only held while buildx served
every layer from cache: the first tag cut after a cache eviction failed it
twice, correctly, because two builds of the same commit genuinely produced
different layers.

Two normalizations make a cold rebuild reproducible, and both are required:

| Where | What |
|---|---|
| `.github/workflows/ci.yml` | `SOURCE_DATE_EPOCH` (the commit's committer date) plus `rewrite-timestamp=true` on every building job's exporter — all four compute the epoch with the same script |
| `Dockerfile` | the apt/dpkg logs and `ldconfig`'s `aux-cache` are removed, and the build-time import check runs under `PYTHONDONTWRITEBYTECODE=1` |

The Dockerfile half is there because `rewrite-timestamp` rewrites timestamps in
the layer's tar *headers*; a timestamp written *inside* a file — an apt log
line, a `.pyc`'s recorded source mtime — is out of its reach.

Measured at US-004, two `--no-cache` builds from two checkouts with different
file mtimes, layers compared one at a time: **19 of 19 identical** with both
halves; 16 of 18 with the exporter attribute alone; 7 of 19 with neither.

Two consequences worth knowing:

- **The image ships no bytecode cache.** It never had a usable one under
  timestamp rewriting — the sources read `SOURCE_DATE_EPOCH` while the `.pyc`
  recorded the build clock — so this drops dead files rather than a working
  optimisation. Cost, measured in the image: importing the app takes ~2.3 s
  instead of ~0.9 s, once per container start, against a 120 s health budget.
- **The known bound is one UTC day.** `useradd` stamps a "last changed" day
  count into `/etc/shadow`, so two builds either side of UTC midnight differ in
  that one layer. The parity gate compares two builds of the same run — but
  `publish` starts ~4–6 minutes after `build-amd64`, so a run STRADDLING UTC
  midnight can genuinely trip the gate on this layer (verifier correction
  2026-09-11: "unaffected" was too strong; exposure ≈0.3–0.5% of runs). A
  parity failure within ~10 minutes of 00:00 UTC: check the two `passwd`-layer
  timestamps FIRST, and re-run — that one is not a finding. A "rebuild this
  image next year and get the same digest" claim would also not hold, and is
  not made. Note also the clamp precondition: `rewrite-timestamp` CLAMPS
  timestamps newer than `SOURCE_DATE_EPOCH` down to it — it does not raise
  older ones — so reproducibility assumes checkout mtimes postdate the commit
  (always true in CI checkouts; not necessarily for a hand-built tree with
  backdated mtimes).

A cold publish is therefore expected to pass. The **first** one to do so will be
whichever release is cut after this change — until then the fix is proven
locally and by the workflow-shape tests, not by a live cold publish.

## Cutting a release

```
git switch main && git pull
# make sure the six gates are green for the commit you are about to tag
git tag v0.9.1-rc
git push origin v0.9.1-rc
```

Then watch the run. The tag push triggers the full workflow; `publish` is the
last job.

## When something goes wrong

**A gate fails.** Nothing was pushed — `publish` never started. Fix, and push a
new tag; do not move the old one.

**The push itself is interrupted.** buildx uploads blobs, then the per-platform
manifests, then the index — and a registry tag only moves when the index lands.
An interrupted push therefore leaves orphan blobs rather than a half-formed
tag. Re-running the same tag re-pushes and skips every blob the registry
already holds, so **re-running a failed publish is safe**.

**The layer verification fails after the push.** The tags exist but point at an
image that was never gated, the run is red, and the Release step never ran.
Treat those tags as untrusted: **a red publish is not a release.** Investigate
before re-running — a genuine mismatch means the publish build did not come
from the cache the gates filled, which is a finding, not a flake.

**The Release step fails after a successful push.** The image is live and the
Release is missing. That is the harmless direction and re-running the job fixes
it. The direction that would matter — a Release advertising an image nobody can
pull — is the one the ordering rules out.

## Required status checks

**Registered** — measured against the branch-protection API on 2026-09-11, not
assumed. The deferral this section used to describe (Free plan + private
repository → `403 Upgrade to GitHub Pro or make this repository public`, recorded
in [`docs/bootstrap-notes.md`](bootstrap-notes.md)) ended at the US-008 public
flip, and the settings were applied then; this section was simply not updated
with them.

On `main`:

| Setting | Value |
|---|---|
| Required status checks | `lint`, `typecheck`, `test`, `build-amd64`, `secret-grep`, `smoke` — the six `publish` hangs off |
| Pull request required | yes (`required_approving_review_count: 0` — a solo maintainer cannot approve their own PR) |
| Force pushes / deletions | blocked |
| Strict (up-to-date before merge) | off |

`.github/pull_request_template.md` states that list to contributors, and
`tests/test_governance_docs.py` ties its sentence to the workflow's own
`publish` `needs:` so the document cannot fall behind a seventh gate.

The complementary control remains that the gates are `needs:` edges rather than
merge policy: nothing can be published over a red gate whether or not a human
can merge over one.
