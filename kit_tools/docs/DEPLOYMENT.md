<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: infrastructure, operations
  required_sections:
    - "Deployment Procedure"
  skip_if: no-infrastructure
-->
# DEPLOYMENT.md

> **TEMPLATE_INTENT:** Document deployment procedures and rollback processes. How to ship safely.

> Last updated: 2026-09-13
> Updated by: Claude (seed-project)

---

## Overview

Forage ships as one container image, `ghcr.io/washingbearlabs/forage`, and nothing in this
repository deploys it anywhere: CI builds, gates, and publishes the image to GHCR, and
deployment is a **consumer-side pull-and-pin**. The operator's unit of deployment is an
image tag (`X.Y.Z` from a `v*` git tag, `sha-<short>` from a push to `main`, `latest` for
the newest non-pre-release; any tag containing `-` is a pre-release and gets only its exact
tag). A green `publish` job proves six things: the `lint`, `typecheck`, `test`,
`build-amd64`, `secret-grep`, and `smoke` gates passed on that commit; the published amd64
layers are byte-identical to the tarball `smoke` executed; the published image config
carries no secret-shaped string; and, for `v*` tags, the Release body names the contract
version and the Release assets verify against the committed `contract/openapi.yaml.sha256`
anchor. The arm64 leg is built under qemu but never executed by CI. `docs/releases.md` is
the authoritative account of the tag scheme and what a green publish proves; how the image
is built and gated lives in `kit_tools/docs/CI_CD.md`.

What the operator owns: network placement (Forage has no authentication on any endpoint),
secrets injected at runtime, the `forage-model-cache` volume, and the companions (SearXNG
for `/search`, optionally Valkey for the content cache). See `kit_tools/arch/INFRA_ARCH.md`
for the shape of the deployment and `kit_tools/arch/SECURITY.md` for the posture.

---

## Environments

There is one artefact and no staging environment in this repository. The environments that
exist, in the order a change passes through them:

| Environment | What it is | Image | Notes |
|-------------|------------|-------|-------|
| CI `smoke` | Ephemeral pre-publish run on `ubuntu-latest` | `forage:ci` (amd64 tarball) | Started with **no environment at all**, so it exercises the weights-free degraded path; `contract_smoke.py --image forage:ci` must pass within 120 s |
| GHCR | The published registry | `ghcr.io/washingbearlabs/forage:<tag>` | Public, anonymous pulls; `publish` runs only on pushes to `main` and `v*` tags, after all six gates |
| Reference deployment | `compose/minimal.yml` and `compose/full.yml` | pinned tags in the fragments | The shape this document describes; runs on any Docker host on a private network |
| Poppy (downstream) | The consuming agent's own private network | Poppy's in-tree copy, until Poppy spec 6 pins a published image | Out of scope here except for the coexistence rule below |

**Coexistence rule (temporary).** Until Poppy pins a published Forage image, Poppy's
in-tree `services/retrieval` copy remains the deployed source of truth and the two copies
coexist. Any fix to `services/retrieval/`, `config/searxng/`, or `tests/retrieval/` on
either side is replayed by hand onto the other, and the Poppy source commit in the "Pin
record" table of `docs/bootstrap-notes.md` (currently `f73e209165e7c74a3a91024caf4ae8f0f9c0036a`)
is updated. Two further constraints (`CLAUDE.md` invariant 2 and the pin record): never push an image built from
Poppy's `services/retrieval/Dockerfile` (it still carries `ARG HF_TOKEN`), and do not point
Poppy at this image before its `env_file` becomes `required: true`, or production silently
drops to memory-cache mode. Full text: `CLAUDE.md`, "Coexistence with Poppy".

---

## Prerequisites

- A Docker host with `docker compose` on a **private network** (a bridge network, a
  host-local port, or a VPN). Forage binds `127.0.0.1:8020` in the reference fragments and
  that binding is the only access control; see the "Deployment posture" note in `README.md`.
- A weights source, or a decision to run without one: an `HF_TOKEN` (fine-grained read
  token on an account that has accepted the Meta license for
  `meta-llama/Llama-Prompt-Guard-2-22M`), or a reachable oras mirror via
  `FORAGE_WEIGHTS_MIRROR` plus `FORAGE_MIRROR_TOKEN` (the default mirror
  `ghcr.io/washingbearlabs/forage-weights` is private to WashingBearLabs). With neither,
  Forage runs in the supported, loud, `degraded` mode. `docs/weights.md` covers all of this.
- A `SEARXNG_SECRET` for the companion (`head -c 32 /dev/urandom | base64`); the SearXNG
  container refuses to start without one.
- For the verification steps: a checkout of this repository at the same tag you are
  deploying (for `git show` and the smoke scripts, which are not shipped in the image), `uv`,
  and optionally `gh`.
- Where each variable is set and what it defaults to: `kit_tools/docs/ENV_REFERENCE.md` and
  `docs/configuration.md`.

---

## Pre-deploy Checklist

1. **Pick a tag.** Pin a full semver (`1.0.0`) or the `@sha256:` digest from the Release
   body; never pin `latest`, and treat `sha-<short>` tags from `main` as unreleased. Tags
   present at the time of writing: `v0.9.0-rc`, `v0.9.1-rc`, `v0.9.3-rc`, `v1.0.0`
   (2026-09-11, the first non-pre-release); `v0.9.2-rc` was withdrawn after failing the
   cold parity gate and must not be deployed. Whether the `v1.0.0` publish run went green,
   and therefore whether `latest` and `1.0` resolve on GHCR, could not be verified offline
   during this seed.
2. **Confirm the tag published green.** The tag's workflow run must show `publish` green
   and, for a `v*` tag, a GitHub Release carrying `openapi.yaml`, `openapi.yaml.sha256`, and
   a `contract: X.Y.Z` line in its body. A red `publish` after a push means the tags exist
   but are untrusted ("a red publish is not a release", `docs/releases.md`).
3. **Pull and verify the image yourself.** The in-image contract is read back and checked
   against the anchor committed *at the same tag*, never against another copy
   (`contract/GOVERNANCE.md`, Consumers). The history grep uses the same two patterns as
   CI's `secret-grep` job.

   ```bash
   TAG=1.0.0
   docker pull ghcr.io/washingbearlabs/forage:$TAG
   docker run --rm --entrypoint cat ghcr.io/washingbearlabs/forage:$TAG /app/contract/openapi.yaml > openapi.yaml
   docker run --rm --entrypoint cat ghcr.io/washingbearlabs/forage:$TAG /app/contract/openapi.yaml.sha256 > openapi.yaml.sha256
   git show v$TAG:contract/openapi.yaml.sha256 | cmp - openapi.yaml.sha256   # anchor from the same tag
   sha256sum -c openapi.yaml.sha256                                          # macOS: shasum -a 256 -c
   docker history --no-trunc ghcr.io/washingbearlabs/forage:$TAG | grep -Ei 'HF_TOKEN|hf_[A-Za-z0-9]{20,}'   # must print nothing
   ```

   `gh release download v$TAG --pattern 'openapi.yaml*'` is the alternative route to the
   same two files. The anchor at `HEAD` is
   `00b1dbaa5971895e7e7f1532f52ab46026df5e789ee5572380fd822f6bb295c0`.
4. **Check contract compatibility.** The image tag and `contract_version` are independent
   semvers (image `1.0.0` serves contract `1.1.0`). Compare the consumer's expected MAJOR
   against `info.version` in the `openapi.yaml` you just extracted; a MAJOR mismatch means
   **do not deploy** (the consumer is expected to refuse activation, `CLAUDE.md`
   invariant 4). Compare contracts, never `sanitizer_revision`, which has deliberately
   diverged from Poppy's six times.
5. **Confirm the weights source is reachable** from the host: an `HF_TOKEN` with gated-repo
   access, or mirror credentials. Weights are fetched at runtime, so a wrong token is a
   `degraded` boot, not a failed one.
6. **Confirm `compose/.env`** holds exactly `HF_TOKEN` and `SEARXNG_SECRET` (it is
   gitignored) and that no variable is being passed inline with `-e`.
7. **Confirm placement.** The `127.0.0.1:8020:8020` binding stays unless you have put your
   own access control in front; SearXNG and Valkey publish no ports at all.

---

## Deployment Procedure

### Compose path (reference)

Both fragments are standalone (no `extends`), validated by CI's `lint` job with
`docker compose config -q`, and share the fixed-name volume `forage-model-cache`. **They
currently pin `ghcr.io/washingbearlabs/forage:0.9.3-rc` and
`ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc`, even though `v1.0.0` is tagged;** the
in-file comments say to move them to `v1.0.0` when it lands, and that has not been done
yet. Edit the `image:` lines to the tag you verified above before bringing them up.

| Fragment | Starts | Env it needs | Cache mode |
|----------|--------|--------------|------------|
| `compose/minimal.yml` (project `forage-minimal`) | `forage` + `searxng` | `HF_TOKEN` (bare pass-through, genuinely unset if absent), `SEARXNG_SECRET` (required-or-fail) | in-memory; `VALKEY_URL` is deliberately absent |
| `compose/full.yml` (project `forage-full`) | `forage` + `searxng` + `valkey` (`valkey/valkey:8` digest-pinned, 8.1.10) | the same two | `VALKEY_URL=redis://valkey:6379/4` as a literal; Valkey persists to `forage-valkey-data` (`--save 60 1`, no password, no ports) |

Common to both: `forage` publishes only `127.0.0.1:8020:8020`, runs with
`restart: unless-stopped` and `mem_limit: 1024m`, mounts `forage-model-cache:/app/model-cache`,
and leaves `SEARXNG_URL` unset so the built-in default `http://searxng:8080` resolves to the
companion by service name (the name `searxng` is load-bearing). There is no `depends_on` and
no `healthcheck:` block: Forage starts without SearXNG and reports `/search` failures
honestly as `searxng_unavailable`.

```bash
cd /path/to/Forage/compose
{ echo "HF_TOKEN=hf_..."; echo "SEARXNG_SECRET=$(head -c 32 /dev/urandom | base64)"; } > .env
docker compose -f minimal.yml up -d        # or: docker compose -f full.yml up -d
curl -s http://127.0.0.1:8020/health | jq
```

`docker compose down -v` deletes the weights volume; use `down` without `-v` for a routine
stop.

### Plain `docker run` path

```bash
docker run --rm -p 127.0.0.1:8020:8020 \
  --env-file ./forage.env \
  -v forage-model-cache:/app/model-cache \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  ghcr.io/washingbearlabs/forage:1.0.0
```

The env file carries `HF_TOKEN` and, if you run a Valkey, `VALKEY_URL`; only a *fully
unset* `VALKEY_URL` selects memory mode, so do not set it to an empty string. Without a
compose network there is no service called `searxng`, so set `SEARXNG_URL` in the env file
to wherever your SearXNG runs. The `config.yaml` mount is optional; a missing file logs a
warning and every key falls back to its code default, while a malformed `extraction:` or
`cache:` block fails the boot loudly.

### Post-deploy verification

1. **Read the `/health` body.** It always returns HTTP 200; the truth is in the body
   (`CLAUDE.md` invariant 5). On the first boot against an empty volume, expect
   `status: "degraded"` with `degraded_reasons: ["promptguard_unavailable"]` while the
   ~270 MiB weight set downloads (measured 19 s cold, 9 s warm on the 1 vCPU / 1 GB
   reference host). Once weights land, expect `promptguard_loaded: true`,
   `capabilities: {"search_sanitization": 1}`, `contract_version: "1.1.0"`, and
   `cache_backend` reading `valkey` (with `cache_connected: true`) under `full.yml` or
   `memory` under `minimal.yml`. A failed acquisition retries in the background at 30 s,
   doubling to a 600 s ceiling with +/-20% jitter, forever; it converges in place without a
   restart once the token or network is fixed.

   ```bash
   curl -s http://127.0.0.1:8020/health | jq
   curl -s http://127.0.0.1:8020/health | jq .promptguard_loaded
   ```

2. **Read `/metrics`** (typed JSON, not Prometheus). `model.fetch_in_progress` and
   `model.retries_scheduled` tell downloading from waiting-out-backoff from wedged, which
   `/health` alone cannot; `model.fetch_failures` climbing with `weights_fetch_failed`
   lines in `docker logs` means the token or mirror is wrong.

   ```bash
   curl -s http://127.0.0.1:8020/metrics | jq .model
   curl -s http://127.0.0.1:8020/openapi.json | jq -r .info.version   # 1.1.0
   ```

3. **Run the contract smoke** from a checkout at the deployed tag. It polls `/health` to
   200 (default budget 120 s), validates the body against `HealthResponse`, checks
   `contract_version` against the checkout's `pipeline/contract.py`, checks `/metrics`
   reports the same version, and with `--image` reads `/app/contract/openapi.yaml` out of
   the image and hashes it against the committed anchor. Exit `0` prints
   `Contract smoke PASSED: degraded, honest, and on-contract.`; exit `1` prints one
   `::error::` line per violation. **Caveat:** it asserts the *weights-free* contract as
   written (`EXPECTED_STATUS = "degraded"` is hard-coded), so against a container that has
   loaded weights the status, reason, and capability checks fail by design. Use it for a
   token-less bring-up or as the CI-equivalent image check, and rely on step 1 for a
   healthy container until a maintainer decides otherwise.

   ```bash
   uv run python contract_smoke.py --base-url http://127.0.0.1:8020 \
     --timeout-seconds 120 --poll-interval-seconds 2 \
     --image ghcr.io/washingbearlabs/forage:1.0.0
   ```

4. **Smoke the companion image** if you changed its pin. `searxng_smoke.py` runs five
   hermetic phases against a throwaway container (secret required, JSON envelope, request
   budget, limiter on with Valkey, limiter inert without) and exits `0`/`1`; `--live` swaps
   in the advisory real-engine probe, `--keep` leaves the containers up for debugging. It
   tests the image, not your running deployment.

   ```bash
   uv run python searxng_smoke.py --image ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc
   ```

For anything that does not come up clean, `kit_tools/docs/TROUBLESHOOTING.md` is organised
by symptom, and `kit_tools/docs/MONITORING.md` explains each `/health` field and `/metrics`
counter.

---

## Upgrading

1. Verify the new tag (Pre-deploy Checklist, steps 1 to 4).
2. Change the `image:` pin in the fragment you run; the companion has its own tag lane
   (`searxng-v*` publishes `ghcr.io/washingbearlabs/forage-searxng`) and its own bump
   procedure in `docs/searxng.md`.
3. Recreate and re-verify.

   ```bash
   docker pull ghcr.io/washingbearlabs/forage:1.0.1
   docker compose -f minimal.yml up -d
   curl -s http://127.0.0.1:8020/health | jq
   ```

Things that change across versions and are expected, not bugs:

- **`sanitizer_revision` rotates** whenever one of the eight hashed `pipeline/*.py` files,
  the pinned model revision, or `promptguard_threshold` changes. The revision is an input
  to Forage's own content-cache key, so every cached entry becomes a miss after the
  upgrade, and any consumer cache keyed on it must flush too. Every rotation is recorded
  with before/after values in `docs/bootstrap-notes.md` (six so far).
- **`contract_version` bumps** are governed by `contract/GOVERNANCE.md`: a MAJOR means the
  consumer refuses activation until it is updated; MINOR and PATCH move bytes the consumer
  may have vendored, so re-vendor the contract by the procedure there.
- **The weights revision moves** only when a maintainer re-vendors (`docs/weights.md`,
  "Re-vendoring": pick the upstream sha, update `model_fetcher.DEFAULT_MODEL_REVISION`,
  run `uv run python -m scripts.vendor_weights`, commit the constant and
  `weights_manifest.json` together, record the rotation). For the operator that means the
  next start is cold again (a fresh ~270 MiB fetch into `hub/`). Drop any
  `FORAGE_MODEL_REVISION` override when upgrading: an override that does not match the
  image's manifest fails verification loudly, by design.
- **`VALKEY_URL` and `extract_route_enabled` are read once at start**, so changing either
  needs a container restart, not just an edit.

---

## Rollback

There is no in-repo rollback mechanism and registry tags are treated as immutable by
policy; a rollback is a re-pin.

1. Set the `image:` line back to the previous verified tag.
2. Recreate: `docker compose -f minimal.yml up -d` (or `full.yml`).
3. Re-verify: `curl -s http://127.0.0.1:8020/health | jq`, and read `promptguard_loaded`,
   `cache_backend`, and `contract_version`.

What the exploration supports about state across a rollback:

- **The model-cache volume** is keyed by revision. A rollback between two tags that share
  the same `DEFAULT_MODEL_REVISION` is a warm, network-free start. Across a revision
  change, the volume layout keeps one `snapshots/<revision>/` tree per revision under
  `hub/` and nothing sweeps `hub/`, so the older set is normally still there; but a warm
  rollback across revisions is not something CI or the docs verify, so budget for a cold
  ~270 MiB re-fetch. Losing the volume entirely costs a re-download, never a rebuild.
- **There is no database**, so there are no migrations to reverse. Content-cache entries
  written under the newer `sanitizer_revision` simply become misses; under `full.yml` they
  persist harmlessly in the `forage-valkey-data` volume and are never read again.
- **A weights rollback** on the maintainer side is a revert of the one commit that moved
  the constant and the manifest, then a redeploy; old mirror tags are never deleted.
- **Never move or re-cut a tag.** A bad release is superseded by a new patch tag; a tag
  whose `publish` went red after the push is withdrawn (git tag deleted and GHCR package
  version deleted, the `v0.9.2-rc` precedent). `docs/releases.md`, "When something goes
  wrong", has the maintainer-side recovery for each failure.

---

## Secrets

Secrets enter at **runtime only**. The `Dockerfile` declares no `ARG` of any kind, because
a build argument is recorded in layer history and readable with `docker history --no-trunc`
from any registry the image reaches; `tests/test_dockerfile.py` and CI's `secret-grep` job
both keep it that way (`CLAUDE.md` invariant 2). Forage reads no secret store at boot; the
container environment is the only channel.

The credential-bearing variables Forage itself reads are `HF_TOKEN`, `FORAGE_MIRROR_TOKEN`,
and `VALKEY_URL` (which may embed a password); the companion additionally needs
`SEARXNG_SECRET`. Provide them through `compose/.env`, `--env-file`, or your secret store,
never as an inline `-e` flag (shell history, `ps`). Remember that `docker inspect` shows a
container's full environment to anyone who can reach the Docker socket, and that rotating
`VALKEY_URL` means a restart because it is read once. None of these values ever reach a
log line: `cache.py` and `model_fetcher.py` log closed reason vocabularies, and the
entrypoint prints nothing. The vendoring credentials (`GHCR_USER`, `GHCR_TOKEN`,
`GITHUB_TOKEN`) are human-only and never given to CI. Details: `kit_tools/arch/SECURITY.md`
and `kit_tools/docs/ENV_REFERENCE.md`.

---

## Resource Envelope

Reference figures, measured or declared in the repository; they are a starting point, not a
guarantee under your traffic.

| Resource | Reference figure | Source |
|----------|------------------|--------|
| Host | 1 vCPU / 1 GB; 19 s cold boot, 9 s warm | `docs/configuration.md` |
| Container memory | `mem_limit: 1024m` = 512 MiB parent (FastAPI + torch + PromptGuard) + 384 MiB pypdf extraction child + ~128 MiB headroom, of which 32 MiB is the in-memory content cache | `compose/*.yml` |
| Weights volume | ~270 MiB (`model.safetensors` is 283,347,432 bytes); a mirror pull needs roughly 2.2x the manifest bytes free during the fetch | `docs/weights.md` |
| Image | ~348 MB, single stage, CPU-only torch | `Dockerfile`, CI notes |
| Concurrency | one uvicorn worker; `/extract` concurrency pinned at 1 with queue depth 1 (excess is a 429 `busy`); `/retrieve` fetches time out at 30 s and cap bodies at 10 MiB | `config.yaml`, `pipeline/stage5_url_audit.py` |

Counters in `/metrics` are in-process and per container; if you run more than one
replica, aggregate them yourself.

---

## Monitoring

- **Logs:** `docker logs <container>`. Nothing configures the root logger, so only
  WARNING and ERROR lines from Forage reach the stream (plus uvicorn's own per-request
  access lines); the `weights_*` markers and the closed cache vocabulary are the lines to
  grep for. The entrypoint prints nothing by design.
- **Metrics:** `GET /metrics`, JSON only; there is no Prometheus exporter, OpenTelemetry,
  or hosted APM in the repository. Poll it with your own tooling.
- **Alerts:** none are defined in this repository. The image ships no `HEALTHCHECK` and the
  compose fragments declare no `healthcheck:`; if you add one, a bare
  `curl -f http://127.0.0.1:8020/health` proves only that the process answers, because
  `/health` is always 200 and the body is where degradation shows.

`kit_tools/docs/MONITORING.md` has the field-by-field reference and the alert-worthy
signals; `kit_tools/docs/TROUBLESHOOTING.md` maps each symptom to its remedy.
