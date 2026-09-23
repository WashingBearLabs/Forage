# Releases and published images

How a Forage version becomes a pullable image, what each tag promises, and what
the gate chain does and does not cover. Written by `forage-ci-and-image` US-007.

The service image is **`ghcr.io/washingbearlabs/forage`**. The companion SearXNG
image (`ghcr.io/washingbearlabs/forage-searxng`) has its own independent tag
lane and is documented separately in `docs/searxng.md` (US-004).

> **Public since US-008's 2026-09-10 flip** — repository and packages; anonymous
> pulls verified at the gate.

## Released versions

Every non-pre-release tag, newest first. The `contract:`, `anchor:`, `index digest:` and
`tagged commit:` lines are the same fields `search-release` US-003's handoff record carries
into `kit_tools/specs/feature-search-release.md`'s Implementation Notes — that table is what
the Poppy `epic-search-policy` session reads to pin a digest; nothing here pushes to Poppy.

### Unreleased

**Upgrade actions:** Search provider timeouts now bound the **whole HTTP interaction**
(connect, headers and body), rather than each socket operation separately; their
values are unchanged (SearXNG 10.0 seconds, Brave 15.0 seconds). A previously working
slow SearXNG may now produce a 422 `searxng_unavailable` ending in `: timeout` on
the default chain, or a `searxng: timeout` `provider_errors` entry **with a paid call**
on `[searxng, brave]`. Read `search.provider_timeouts` and raise
`search_searxng_timeout_seconds` for such an instance. No four-engine fan-out latency
distribution has been measured; parsing, sanitization and classification remain
outside the budgets. The chain has no overall deadline.

Both providers request `Accept-Encoding: identity` but serve bounded gzip and
deflate replies from a compressing proxy. `search.provider_compressed_body` counts
every non-identity response header, including failed replies; `unsupported_encoding`
means this build cannot decode it. On a configured `[searxng]`-only chain this new
closed token can appear in the 422 reason; Brave's detail remains log-only.
Contract 1.3.0 adds these two counters without changing `SearchResponse`.

### v1.1.0 — 2026-09-18

- contract: 1.2.0
- anchor: `11435a17aabe7c11faf71aee0fd066a3784d5e9de557c451153e7f47d0d5615f`
- index digest: `sha256:e1b875ccbf6505d674e70802c07eeb5ad6c62548ae14dde0a18be0bf9cb47a52`
- tagged commit: `06b01b145d592787b32eb0425061fa8c1914d31f`

What shipped:

- The `SearchProvider` seam and `FORAGE_SEARCH_PROVIDERS` — an ordered, comma-separated
  provider chain (default `searxng`, the key-less free floor), resolved once in the
  lifespan.
- The Brave LLM-Context paid provider behind `FORAGE_BRAVE_API_KEY`. With the key set,
  anyone who can reach port 8020 can spend the operator's money — Forage enforces no
  budget cap by decision (ruling 12); the budget breaker is the consumer's. Network
  placement and a front-side proxy or rate limit are your controls;
  `/health` discloses key presence to anyone who can reach it, and `/metrics`
  `search.paid_calls` / `search.fallback_fired` are how spend is seen.
- Free-first, paid-on-failure fallback, with the `search_unavailable` code added to the
  `/search` 422 vocabulary for an exhausted provider chain.
- Per-request search policy on `SearchRequest` and provider status (`search_providers`) on
  `/health`.
- Three new `/metrics` `search.*` counters: `search.paid_calls`, `search.fallback_fired`
  and `search.policy_unknown_provider`.

### v1.0.0 — 2026-09-12

- contract: 1.1.0
- anchor: `00b1dbaa5971895e7e7f1532f52ab46026df5e789ee5572380fd822f6bb295c0`
- index digest: `sha256:d83639ccb0c186d1eeb0b67dce784348c6243a29a157a23b3e2e37f7e0c178da`
- tagged commit: `f4c2b16ae634b35814831e5d7a358ea3e8a739de`

What shipped:

- The first published, secret-free, multi-arch Forage image (extracted from the Poppy
  monorepo), minting `latest`, `1.0` and `1.0.0` for the first time.
- The frozen, versioned OpenAPI contract (`contract/openapi.yaml`) with a committed
  sha256 anchor, verified across the repository copy, the Release assets and the in-image
  copy.
- Reproducible, byte-identical multi-arch builds: a commit-derived `SOURCE_DATE_EPOCH`,
  an apt log scrub, and `rewrite-timestamp=true` on every exporter.
- `contract_smoke.py`, verifying a running container's `/health` against the contract and,
  with `--image`, the in-image contract against the committed anchor.

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

`latest` has existed since `v1.0.0` — the first non-pre-release `v*` tag,
cut by `feature-forage-contract` once the OpenAPI contract froze. `v1.1.0`
(`search-release` US-002) moved it again, alongside `1.1`. See "Released
versions" above for what each tag serves.

## What has to be green first

`publish` declares `needs: [lint, typecheck, test, build-amd64, secret-grep,
smoke]` — every other job in the workflow. This is the reason all of them live
in one file: `needs:` cannot span workflow files, so a split would turn the
release gates into wishful thinking.

A tag pushed onto a red tree still *runs* the gates. They fail, and `publish`
never starts.

**No `v*` tag while the contract 1.3.0 window is open.** `hardening-search-sanitization`
US-004 opened contract `1.3.0` with `tests/test_contract_schema.py`'s
`_EXPECTED_ONE_THREE_ZERO_DIFF` starting **empty** and mutable — every later story in the
epic that moves the wire appends to it and re-creates `tests/golden/contract_1_3_0.json`.
A tag cut while that set's opening comment still says the window is open would publish a
contract whose golden is still being edited mid-epic. The check today is this sentence, not
a CI gate; spec 8 US-002 rewrites that comment when it freezes the set, and a mechanical
gate keyed on the same comment is that story's to add (flagged in the epic wrapper).

**The `retrieve.max_promptguard_chunks` compatibility window.** The release that ships
contract `1.3.0` keeps that key's default at **`0`** — no pre-check on `/retrieve`, exactly
the behaviour that shipped before the key existed — and boot logs one WARNING,
`retrieve_budget_unset coming_default=256`. The **next MINOR** flips the default to `256`;
`0` remains a legal, documented opt-out after the flip, so no MAJOR is ever cut for it.
That is worked example 6 step 1 with the window named, and it belongs in this release's
Release body as well as here (`contract/GOVERNANCE.md` ruling (g)).

**Consumer note for the pending hardening release (spec 8 handoff).** From
`hardening-retrieve-parity`, a caller sending `promptguard_fail_closed: false` is
exposed to an unscanned-but-marked response whenever the classification permit is
contended for longer than `promptguard_wait_seconds`, signalled by `promptguard_state`
on `/retrieve` and `suspicious` / `promptguard_unavailable` / `unscanned_results` on
`/search`; `promptguard_fail_closed_floor: true` is the operator-side control
(`config.yaml`-only until spec 6's bind-mount procedure), bounding the flag but not
the caller's trusted-tier skip or VERIFIED fail-open exemption.

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
contract: <version>
```

where `<version>` is the tagged tree's `CONTRACT_VERSION` (`pipeline/contract.py`
at the tagged commit) — never a number typed into the workflow.

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

Since `search-release` US-004 the body also says **what changed**. Each version
has a **per-version entry** in the docstring under `CONTRACT_VERSION` — one
bullet at column 0 naming the version, continuation lines indented two spaces
([`contract/GOVERNANCE.md`](../contract/GOVERNANCE.md), step 7 of "Bumping the
contract"). Step 1 copies the tagged version's entry into
`${RUNNER_TEMP}/contract-entry.md` with a POSIX `awk` program and fails the run
when there is none: a release with an unannounced contract is the failure it
exists to catch. Step 2 writes the body to a notes file,
`${RUNNER_TEMP}/release-notes.md` — the fixed text with its `contract:` line,
then a `What changed in contract <version>:` heading, then the entry appended
byte-for-byte with `cat`, never interpolated into the shell — and publishes it
with `gh release create --notes-file`. Step 3 checks every line of the entry is
in the published body with a fixed-string `grep`. The version travels between
steps as a step output; the entry travels only as that file.

**`publish` runs nothing from the tagged tree in its job shell, and nothing
from the tree sees the token.** The job *reads* the tagged commit's files —
`pipeline/contract.py`, the contract and its anchor — but runs no `python3`, no
`uv` and no `scripts/` helper from it. That is a narrower claim than "executes
nothing": the tagged `Dockerfile`'s `RUN` steps do execute, inside BuildKit,
which has no `GH_TOKEN`; and on a tag push the workflow file itself is the
tagged copy. The boundary that actually holds is **who can push a `v*` tag**.
`publish` is the one job holding `contents: write`, `packages: write` and a
token, and it pushes the public image, so tag-push rights are publish rights; a
helper script run in the job shell would let a merged docstring change plus a
tag run arbitrary code with that token, which is why none is. A missing entry
is caught earlier than the tag in any case: `test_the_current_contract_version_has_a_docstring_entry` runs
the same `awk` program on the PR.

**If step 3 fails**, the image is pushed and the Release exists but advertises
the wrong contract or is missing a line of its entry — a red run with a wrong
Release rather than a missing one.
`gh release create` refuses a tag that already has a Release, so recovery is
`gh release edit "$TAG" --notes-file <file>` with the corrected body — the entry
extracted locally from the tagged tree (`git show "$TAG":pipeline/contract.py`
through the same `awk` program) so the Release carries it verbatim — or
deleting the Release and re-running the job.

### Cache-integrity upgrade note for the pending hardening release

**Spec 8 Release-body handoff:** `/metrics.cache` gains `integrity_rejects`.
`storage_oversize_skips` keeps its meaning but can now rise on Valkey as well
as memory, at the new `cache.max_value_bytes` bound (4 MiB by default).
No published contract golden is changed; these additions ride the held 1.3.0
window. The revision rotation orphans old cache keys.

Remove `decode_responses`, `encoding`, `encoding_errors` and `protocol` from
`VALKEY_URL` query options before upgrading: their presence now refuses boot
with a key-only diagnostic, because they can break byte-bounded reads.
Socket timeout tuning remains allowed. Use the same byte bound across all
replicas sharing Valkey; lowering it can reject past larger writes as
`oversize`, which is not by itself tampering. See
[`configuration.md`](configuration.md#the-cache-block) for the memory-sizing
relationship. The constructor-level HMAC machinery lands in US-001;
environment-key wiring and keyless-Valkey health reporting follow in US-002.

### Domain-list upgrade note for the pending hardening release

**Spec 8 Release-body handoff, the 1.3.0 window:** existing multi-label
`blocked_domains` and `seed_blocklist` entries now cover subdomains; review apex
entries before upgrading, because a multi-tenant apex removes every tenant.
Single-label entries keep matching exactly as before. Bare allowlist entries
stay exact; add a leading dot to opt into the apex and all subdomains. The six
shipped `news_domains` entries now opt in, so `www.bbc.co.uk` gets the one-hour TTL.
Canonical private-name rejection precedes caller denylists (`blocked_domain` →
`private_ip` when both match). GOVERNANCE ruling (h) records the explicit
Example 6 exception; this paragraph is the upgrade announcement, not a claim
that an opt-in compatibility window was implemented.

US-007 adds a **64 KiB raw UTF-8 budget per caller domain list**, configurable
with `policy_domain_entries_max_bytes` (4 KiB–1 MiB). `/retrieve` refuses an
oversized `blocked_domains` list whole with 422 `content_too_large` and reason
`policy_domain_list_too_large`; retrying unchanged cannot help. Allowlists keep
their in-budget prefix, counting invalid entries and over-budget remainders on
`retrieve.policy_invalid_domain_entry`. The operator's denylist cannot be evicted.
`retrieve.policy_suffix_trusted_skip` measures wildcard trusted **and verified**
resolutions; both corresponding `search.*` counters are also declared, with
search drop increments enabled by US-002 and suffix skips permanently zero.
Consumer-size evidence available in this checkout: **none** (no production
request capture); the default is the specified configurable bound, not a claim
about measured production headroom. This bounds encode work, not request-body
admission.

US-002 adds optional `/search` `blocked_domains`, merged after the operator's
`seed_blocklist` using the same directional hostname semantics as `/retrieve`.
Matching results are omitted whole as `blocked_url` before content scanning,
without firing paid fallback even when no result survives. This closes the
previous search bypass of an operator's existing seed policy. Invalid caller
entries increment `search.policy_invalid_domain_entry`; an over-budget list is
refused whole as 422 `search_unavailable` / `policy_domain_list_too_large`.
Like `policy_excluded_all_providers`, that reason is a permanent client error,
not retryable unchanged. The baseline for callers sending neither new field,
with the shipped empty seed list, remains unchanged.

US-005 adds optional `/search` `promptguard_threshold` and reports
`effective_promptguard_threshold` on every search 200. Both fetch routes now
interpret null/omitted threshold as the validated `config.yaml` default, then
cap it with `promptguard_threshold_ceiling`. Shipped 0.85 behavior is unchanged.
**Upgrade in both directions:** a key raised above 0.85 to quiet upload false
positives now **loosens** `/retrieve` and `/search` blocking unless capped; below
0.85 **tightens** them. The old upload-only config knob is gone and the content
cache re-keys. Invalid defaults warn and fall back to 0.85 on the fetch routes,
but `/extract` retains its raw guard, including YAML `true` becoming 1.0.
Consumers should use the effective field as policy, not proof of scanning.

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
Release is missing or wrong. The direction that would matter — a Release
advertising an image nobody can pull — is the one the ordering rules out.
Whether re-running helps depends on which step failed; the announcement steps
split two ways:

- **`gh release create` itself failed (network, API) with a correct entry.**
  Re-running the job is safe: the push skips every blob the registry already
  holds and the Release is created on the second pass.
- **The extractor found no entry, or the read-back found the wrong one.** A
  re-run reads the same immutable tagged tree and fails the same way. If the
  docstring entry is correct and only the extraction or read-back misfired,
  extract the entry locally from the tagged tree and repair the Release body
  with `gh release edit "$TAG" --notes-file <file>`; the tags stay. If the
  docstring entry is wrong or missing, no re-run can help: withdraw the tag
  (see "Withdrawn tags" above) and cut the next patch version with the
  corrected entry.

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
