# Releases and published images

How a Forage version becomes a pullable image, what each tag promises, and what
the gate chain does and does not cover. Written by `forage-ci-and-image` US-007.

The service image is **`ghcr.io/washingbearlabs/forage`**. The companion SearXNG
image (`ghcr.io/washingbearlabs/forage-searxng`) has its own independent tag
lane and is documented separately in `docs/searxng.md` (US-004).

> **Public since US-008's 2026-09-10 flip** — repository and packages; anonymous
> pulls verified at the gate.

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

Not registered. `WashingBearLabs` is on the GitHub **Free** plan and this
repository is private, so both the branch-protection API and the rulesets API
return `403 Upgrade to GitHub Pro or make this repository public`. The
deferral, and the settings to apply at the public flip, are recorded in
[`docs/bootstrap-notes.md`](bootstrap-notes.md).

Until then the compensating control is that the gates are `needs:` edges rather
than merge policy: nothing can be published over a red gate whether or not a
human can merge over one.
