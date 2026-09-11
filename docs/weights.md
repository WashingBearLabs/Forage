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
>
> **Running Forage outside WashingBearLabs?** Most of this page is our vendoring
> runbook. Yours is [Bring your own token](#bring-your-own-token) — the mirror cannot
> serve you, and that section is honest about what that means.

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
also what a third-party operator supplies to run Forage without our mirror at all — the
walk-through for that is [Bring your own token](#bring-your-own-token). It is
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

The consuming side landed with US-004. Forage reads this token from `FORAGE_MIRROR_TOKEN`
and the repository from `FORAGE_WEIGHTS_MIRROR`; both are documented in
`docs/configuration.md`. Three things about how it is consumed are worth knowing when you
decide what to scope it to:

- **Read is all it ever needs.** The runtime pulls; it never pushes, never tags and never
  deletes.
- **It reaches `oras` on stdin, never as an argument**, and no captured output from that
  subprocess is logged — so it does not appear in `ps`, in shell history or in the
  container's logs.
- **The tag is not the token's to choose.** `FORAGE_WEIGHTS_MIRROR` names a *repository*;
  the tag is always the pinned revision, and whatever arrives is verified against the
  committed manifest before it is installed. A compromised read token cannot make the
  service load different weights — only a compromised *write* token could, which is why
  the two must never be the same credential.

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

## Bring your own token

Everything above is about *our* copy. This section is for everyone else: you are running
Forage outside WashingBearLabs and you want `/health` to reach `status: "healthy"`.

Be clear about one thing first: **the mirror is not available to you.**
`ghcr.io/washingbearlabs/forage-weights` is private and stays that way (previous
section), so `FORAGE_WEIGHTS_MIRROR` and `FORAGE_MIRROR_TOKEN` do nothing useful in a
third-party deployment — leave them unset. Your path to the weights is Hugging Face,
from Meta, under Meta's terms, and until [you vendor a mirror of your
own](#if-you-want-outage-insurance-of-your-own) it is your *only* path: a Hugging Face
outage leaves your deployment degraded (and retrying) rather than falling back to
anything. It is one credential and five steps, and only the wait in step 2 is out of
your hands.

### The walk-through

1. **Create a Hugging Face account** at https://huggingface.co if you do not have one.
2. **Request access to the gated repository.** Visit
   https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M while signed in and submit
   the access request — this is where you accept the **Llama 4 Community License
   Agreement** and **Acceptable Use Policy** (the terms `NOTICE` cites). Meta approves
   per account, and approval is **not instant**: minutes on a good day, longer on a bad
   one. There is nothing to configure while you wait.
3. **Create a read token.** Hugging Face → Settings → Access Tokens. A **fine-grained**
   token with *"Read access to contents of all public gated repos you can access"* is
   the narrowest scope that can perform the download and is what you should prefer; a
   classic `read` token also works. Nothing here ever needs write.
4. **Set `HF_TOKEN`** in the container's environment — through an env file or a secret
   store, never an inline `-e` flag (`docs/configuration.md` § "Credential handling"
   applies to this token too). That is the *only* variable you need: the revision pin is
   the committed default, so leave `FORAGE_MODEL_REVISION` alone — overriding it without
   a matching manifest fails verification, loudly and correctly (see [the
   rule](#the-three-places-the-revision-appears--and-the-rule-about-them)).
5. **Start the container and watch it converge.** Startup does not block on the
   download: the service answers immediately as `degraded`, fetches the ~270 MiB behind
   a live `/health`, verifies every byte against the committed manifest, and
   `promptguard_loaded` flips to `true` in place — no restart.

Mount a volume at `HF_HOME` (see [the volume section](#the-model-cache-volume)) and step
5 happens once: every later start finds the verified set and loads it with **zero
network** — the token is not even read.

### What you will see while it converges

`/health` reports `degraded` + `promptguard_unavailable` for the entire acquisition,
whether that is "downloading right now", "waiting to retry", or "wedged". The
`/metrics` `model` section is what separates those three states — read it rather than
tailing logs:

| Reading | Meaning |
|---|---|
| `fetch_in_progress: true` | downloading or verifying right now |
| `retries_scheduled > 0`, `fetch_in_progress: false` | a previous attempt failed; the backoff is waiting to try again |
| both zero/false, still degraded | the first attempt has not finished yet |

A failed attempt is never final: the retry schedule (30 s doubling to a 10-minute
ceiling, ±20% jitter, forever) means a token that Meta approves an hour after the
container started converges **without a restart**. The schedule, the metrics, and the
log lines each attempt emits are documented in `docs/configuration.md` § "Weights
acquisition".

Done looks like: `promptguard_loaded: true`, `promptguard_unavailable` gone from
`degraded_reasons`, and — cache connected — `status: "healthy"`. From a clean machine to
that state is this section's acceptance test.

### Running without the token — supported, honest, loud

No token is a **supported mode**, not a crippled one. Extraction, the structural
injection scan (stage 2) and the URL audit (stage 5) all still run; what is withheld is
stage 3's ML classification, and `/health` says so: `degraded`,
`promptguard_unavailable`, and no `search_sanitization` capability advertised. Your
consuming agent should treat standard-tier content as unscanned while
`promptguard_loaded` reads `false` — that is the honest contract.

It is deliberately not a *quiet* mode. A credential-less container retries at the
ceiling for as long as it runs and logs a terminal ERROR on every attempt — a service
missing a capability for six hours should still be saying so. If the drumbeat bothers
you, the fix is a token, not a log filter; and for monitoring, `/metrics` (above) is the
machine-readable readout.

One thing is **never** the answer to a missing token:
`FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION` (deprecated alias
`POPPY_RETRIEVAL_LEGACY_CAPABILITY`) forces `/health` to advertise
`search_sanitization` while the classifier is not running. It exists for one bounded
consumer-transition window, it makes your agent believe content is being ML-scanned when
it is not, and `docs/configuration.md` § "Break-glass" carries the full caveat. If you
are reaching for it because getting a token is inconvenient, stop — the walk-through
above is the steady-state answer.

### The obligations that travel with the weights

Forage's source is Apache-2.0; the weights you just fetched are not. They are **Llama
Materials**, governed by the Llama 4 Community License you accepted in step 2, which
carries its own attribution ("Built with Llama"), naming, and acceptable-use
obligations. `NOTICE` ships in every Forage image — including yours — whether or not the
weights ever load. What the image cannot do for you: if you redistribute an image,
volume snapshot, or artifact that *contains* the weights, the Llama terms travel with
it, and carrying them forward becomes your obligation.

### If you want outage insurance of your own

The vendoring procedure at the top of this page is not WashingBearLabs-specific.
`scripts/vendor_weights.py --repository <your-registry>/<you>/forage-weights` builds the
same deterministic artifact and pushes it to a registry you control (the final
visibility check speaks the GitHub API, so it is GHCR-specific; elsewhere, verify
privacy your own way). Then set `FORAGE_WEIGHTS_MIRROR` and a read-only
`FORAGE_MIRROR_TOKEN` exactly as `docs/configuration.md` describes and your deployment
has the same fallback ours does. Mind two things: your mirror must stay private for the
same licensing-posture reasons ours does, and a mirror *containing* the weights is a
redistribution — the obligations above apply to it.

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
├── staging/                           # a mirror pull in flight; never survives one
├── xet/                               # huggingface_hub's chunk cache; swept after a fetch
└── .agent_harnesses.json              # huggingface_hub's own cache; not ours, harmless
```

Three things follow from that layout:

- **`hub/`, not the volume root.** `HF_HUB_CACHE` is `$HF_HOME/hub`, and both the download
  and the load are handed that directory explicitly. Passing the volume root writes one
  level too high and the loader then reads a different tree — a failure that presents as
  "the download worked and the model still isn't there".
- **Everything except `hub/` is disposable, and two of them are swept.** `staging/` holds
  a mirror pull mid-flight — the tarball and its extraction, bounded to twice the
  manifest total plus 10% — and `xet/` is `huggingface_hub`'s chunk-dedup cache. Both are
  removed at the end of every acquisition, successful or not: they are caches for a *next*
  download of a weight set that is fetched once, and on a 1 GB container they are the
  space that next download would need. A mirror fetch that finds too little room refuses
  before spending the bandwidth.
- **Losing the volume costs a re-download, not a rebuild.** A container without the volume
  mounted still works; it just re-fetches on every recreate. That is documented behaviour,
  not an error.
- **Keeping the volume buys a network-free start.** A start that finds a verified set at
  the pinned revision verifies it and loads it and does nothing else — no download, no
  `oras`, and no hub request of any kind, so it works on a container with no egress at
  all. Measured on the reference envelope (1 vCPU / 1 GB): **9 s warm under
  `--network none`, against 19 s cold.** Changing `FORAGE_MODEL_REVISION` makes the next
  start cold again, which is the point of the pin.

---

## Related

- `docs/configuration.md` — every environment variable Forage reads
- `docs/releases.md` — the image tag scheme and what a green publish proves
- `kit_tools/docs/GOTCHAS.md` — the allowlist gotcha (`ALLOW_PATTERNS`) and the
  `huggingface_hub` header fetch, both of which bite during a vendoring run
- `NOTICE` — the attribution that ships regardless
