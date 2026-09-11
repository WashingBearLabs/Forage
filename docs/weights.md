# Weights: the pinned model, the private mirror, and how to re-vendor

Forage's injection classifier is Meta's **`meta-llama/Llama-Prompt-Guard-2-22M`**. Since
`forage-ci-and-image` US-003 no image contains it — a build that baked the weights needed
a Hugging Face token as a build ARG, and a build ARG is recoverable from any registry the
image reaches (`CLAUDE.md` invariant 2). The weights arrive at **run time** instead, into
a cache volume, verified against a committed manifest before anything opens them.

That leaves one dependency worth insuring: the upstream repository is **gated**. Access is
granted by Meta per account, and a gated repository is one vendor decision away from
unavailable. So we keep our own copy — a private OCI artifact at
`ghcr.io/washingbearlabs/forage-weights:<revision>` — and this document is how that copy
is made, checked and replaced.

Written by `feature-forage-model-bootstrap` US-003.

> **The mirror is private, and staying private is checked after every push.** See
> [Why the mirror is private](#why-the-mirror-is-private) for the licensing reasoning —
> it is a deliberate choice, not a restriction imposed on us.

---

## The three places the revision appears — and the rule about them

| Where | What it is |
|---|---|
| `model_fetcher.DEFAULT_MODEL_REVISION` | the default the container fetches and loads |
| `weights_manifest.json`'s `revision` | the pin the verifier walks and hashes against |
| `ghcr.io/washingbearlabs/forage-weights:<revision>` | the mirror tag US-004 falls back to |

**They move together, in one commit.** That is the whole rule, and it is the one thing to
carry away from this page.

A revision bump that lands without the manifest makes every start fail verification and
quarantine a good download. A manifest that lands without the mirror tag leaves the
fallback pointing at nothing, which is only discovered during an actual Hugging Face
outage — the worst possible moment to find out. The tag cannot drift on its own because
`scripts/vendor_weights.py` *derives* it from the constant rather than spelling it out
(`tests/test_vendor_weights.py::TestSingleSourceOfTruth`), and the constant-to-manifest
leg is locked by `tests/test_model_fetcher.py::TestRevisionPin`. What no test can check is
whether you actually pushed the tag — hence [the fresh-pull
check](#verify-a-fresh-pull-on-a-clean-machine).

`FORAGE_MODEL_REVISION` overrides the default at run time, for a container that needs a
different pin without a rebuild. It does **not** move the manifest, so an override without
a matching manifest and mirror tag will fail verification — which is the correct, loud
behaviour.

---

## What the artifact is

One gzipped tar of the snapshot directory `from_pretrained` resolves — flat, no directory
prefix — pushed as an OCI artifact with type
`application/vnd.washingbearlabs.forage-weights.v1+tar`.

Two properties are deliberate and both are tested:

- **Symlinks are dereferenced.** Hugging Face's cache stores content under `blobs/` and
  fills `snapshots/<revision>/` with symlinks into it. A plain `tar` (without `-h`) of
  that directory ships the *links*, which arrive on the far side as ~0 bytes of dangling
  nothing — and only fails at the moment the mirror is needed. Every member of our archive
  is built from the resolved file.
- **The archive is byte-for-byte reproducible.** Members are sorted; mtime, mode, uid, gid
  and owner names are pinned; the gzip header carries neither a timestamp nor a filename.
  Re-running the vendoring on the same revision produces an identical tarball, so a
  published artifact can be *re-derived* and compared rather than merely trusted.

---

## Vendoring: the procedure

### Prerequisites

| What | Why | Notes |
|---|---|---|
| A Hugging Face account with access to the gated repo | the download | Meta approves the access request; it is not instant |
| `HF_TOKEN` | authenticates the download | a **read** token is sufficient |
| `oras` on `PATH` | pushes the OCI artifact | `brew install oras`, or https://oras.land/docs/installation |
| `GHCR_USER` + `GHCR_TOKEN` | pushes to GHCR | `write:packages`; used by a human, once per re-vendor |
| `GITHUB_TOKEN` | confirms the package is private | `read:packages` is enough; falls back to `GHCR_TOKEN` |
| ~1 GB free disk | the download, the tarball, and the self-check extraction | outside the repo by default |

The script fails loudly and specifically if `oras` is missing rather than dying inside a
subprocess call three phases in.

### Run it

```bash
export HF_TOKEN=hf_...
export GHCR_USER=<your-github-login>
export GHCR_TOKEN=ghp_...          # write:packages
export GITHUB_TOKEN=ghp_...        # read:packages

# Rehearse. NOT read-only: --dry-run gates only the push and the API call —
# phases 1–4 still run, which spends the ~270 MiB download AND regenerates
# weights_manifest.json in place (US-003 verification finding). Point the
# manifest at a scratch path so the real first-generation diff is still
# yours to read on the real run:
uv run python -m scripts.vendor_weights --dry-run --manifest /tmp/rehearsal-manifest.json

# The real run.
uv run python -m scripts.vendor_weights
```

**No credential is ever a command-line flag.** They arrive in the environment, and they
stay there: the Hugging Face token goes only to `snapshot_download(token=...)`, the
registry credential reaches `oras` over a **pipe** (`oras login --password-stdin`), and
the GitHub token goes into an `Authorization` header. `ps` is world-readable and shell
history outlives the credentials in it.

### The six phases

The run is six functions, and `--step` runs any one of them alone — supervised runs want
to stop, look and resume rather than repeat a 270 MiB download.

| Step | What it does |
|---|---|
| `download` | `snapshot_download` at the pinned revision, with the service's own `allow_patterns` |
| `manifest` | regenerates `weights_manifest.json` and prints the diff against the committed one |
| `tar` | writes the deterministic, dereferenced tarball |
| `selfcheck` | extracts that tarball and runs it through the **real** verifier |
| `push` | `oras login` → `oras push <ref>` → `oras logout` |
| `visibility` | asks the GitHub API whether the package is private, and fails if it is not |

```bash
uv run python -m scripts.vendor_weights --step manifest   # e.g. just regenerate
```

**Read the manifest diff before you commit it.** A changed sha256 on a file whose revision
did *not* move means upstream mutated a pin, and that deserves a stop, not a `git add`.

### Then commit — all three together

```bash
git add weights_manifest.json          # plus model_fetcher.py if the revision moved
git commit -m "chore(weights): vendor <revision>"
```

The `_comment` block inside the generated manifest restates the bump-together rule, so
the rule is readable at the file as well as here.

---

## Verify a fresh pull on a clean machine

The self-check proves the tarball you *built* is good. This proves the artifact you
**published** is — the two are the same bytes only if the push did what it claimed.

```bash
export GHCR_USER=<your-github-login>
export GHCR_TOKEN=ghp_...             # read:packages is enough here
echo "$GHCR_TOKEN" | oras login ghcr.io -u "$GHCR_USER" --password-stdin

mkdir /tmp/forage-pull && cd /tmp/forage-pull
oras pull ghcr.io/washingbearlabs/forage-weights:<revision>

cd /path/to/Forage
uv run python -m scripts.vendor_weights --step selfcheck \
  --tarball /tmp/forage-pull/forage-weights-<revision>.tar.gz
```

`the extracted tarball verifies against the committed manifest`, followed by
`vendor_weights OK`, means the extracted snapshot satisfies the committed
`weights_manifest.json` through `model_fetcher.verify_weights()` — the same function the
running service uses, not a re-implementation. That is the Independent Test for this
story, and it is also what proves the dereferenced tar carried real bytes.

---

## Re-vendoring (bumping the revision)

Upstream moving its `main` is *not* a reason to re-vendor: the fetch is revision-pinned, so
a new upstream commit changes nothing here until someone decides it should. When that
decision is made:

1. Pick the new commit sha from the model repository's history.
2. Update `model_fetcher.DEFAULT_MODEL_REVISION`.
3. Run the vendoring (`uv run python -m scripts.vendor_weights`) — it downloads the new
   revision, regenerates the manifest, and pushes a **new tag**. Old tags are left alone,
   so a rollback is a revert of one commit plus a redeploy.
4. Read the manifest diff.
5. Commit the constant and the manifest **together**, and note the before/after
   `sanitizer_revision` — the hashed model identity is `MODEL_ID@revision`, so a revision
   bump rotates it and invalidates caches keyed on it. `docs/bootstrap-notes.md` records
   every rotation.

---

## Tokens, and least privilege for each

Four credentials appear on this page and none of them is the same credential.

### `HF_TOKEN` — the download

A Hugging Face **read** token, on an account whose access request Meta has approved. It is
also what a third-party operator supplies to run Forage without our mirror at all. It is
runtime-optional: a container without one is honestly `degraded`, never silently
unscanned.

### `GHCR_TOKEN` — the push

Needs `write:packages`. This is the only credential here that can change what is in the
mirror, so it belongs to a human running a vendoring session — never to CI, never in a
`.env`, and never in a container. The script logs out at the end of the run, including
when the push fails, so it does not linger in the machine's registry configuration.

### The mirror **read** token — what production uses

This is the one that ships to a deployment, so it is the one to be strict about.

- **Prefer a fine-grained PAT scoped to the single package.** Where GitHub's fine-grained
  permissions cover the package registry for your account, scope the token to
  `forage-weights` alone and to read. A leaked token then buys an attacker exactly one
  copy of some public model weights.
- **A classic `read:packages` PAT is account-wide.** It grants read to *every* package the
  account can see, in every repository, for as long as it lives. It works, and it is what
  you will fall back to if fine-grained scoping is unavailable — but be clear-eyed that
  its blast radius is the whole account, and give it a short expiry.
- **Never reuse the push token as the read token.** A `write:packages` credential in a
  deployment environment turns a container compromise into an artifact compromise: an
  attacker who can overwrite the mirror tag can serve weights of their choosing to every
  future cold start.

The consuming side lands with US-004, which reads this token from `FORAGE_MIRROR_TOKEN`
alongside `FORAGE_WEIGHTS_MIRROR`; `docs/configuration.md` gains both when they exist.
Creating the token now is the right order — the least-privilege decision is easier to make
before something is waiting on it.

### `GITHUB_TOKEN` — the visibility check

Read-only, deliberately separate: the check that the package is private should be able to
run with a credential that could not have made it public.

---

## Why the mirror is private

The Llama 4 Community License (the terms `NOTICE` cites) permits redistribution with
attribution — so publishing these weights would be *allowed*. We do not, for a reason that
has nothing to do with permission:

**being a weights distributor is a role, not a side effect.** A public mirror invites
people to depend on it, which means uptime expectations, bandwidth, and an implicit claim
that our copy is a trustworthy source of Meta's model. Forage is a retrieval sidecar. The
correct answer to "where do I get the weights?" for anyone who is not us is *Hugging
Face*, from Meta, under Meta's terms — which is exactly what the bring-your-own-token path
gives them.

Attribution is not contingent on any of this. **`NOTICE` ships in the image regardless**,
including the "Built with Llama" attribution, whether or not a given container ever loads
the weights.

The private posture is *verified*, not assumed: every vendoring run ends by asking the
GitHub API for the package's visibility and failing if it is anything but `private`. GHCR
creates a package on first push with the account's default visibility, and a default is
not a guarantee.

---

## The model cache volume

The weights live in a Docker volume named **`forage-model-cache`**, mounted at
`/app/model-cache` (the image's `HF_HOME`). This is the canonical spelling; compose
fragments elsewhere cite it from here rather than restating it.

Inside it:

```
/app/model-cache/
├── hub/
│   └── models--meta-llama--Llama-Prompt-Guard-2-22M/
│       ├── blobs/                     # the real bytes
│       └── snapshots/<revision>/      # symlinks into blobs/ — what the loader opens
├── quarantine/                        # a refused set, one generation only
└── .agent_harnesses.json              # huggingface_hub's own cache; not ours, harmless
```

Two things follow from that layout:

- **`hub/`, not the volume root.** `HF_HUB_CACHE` is `$HF_HOME/hub`, and both the download
  and the load are handed that directory explicitly. Passing the volume root writes one
  level too high and the loader then reads a different tree — a failure that presents as
  "the download worked and the model still isn't there".
- **Losing the volume costs a re-download, not a rebuild.** A container without the volume
  mounted still works; it just re-fetches on every recreate. That is documented behaviour,
  not an error.

---

## Related

- `docs/configuration.md` — every environment variable Forage reads
- `docs/releases.md` — the image tag scheme and what a green publish proves
- `kit_tools/docs/GOTCHAS.md` — the allowlist gotcha (`ALLOW_PATTERNS`) and the
  `huggingface_hub` header fetch, both of which bite during a vendoring run
- `NOTICE` — the attribution that ships regardless
