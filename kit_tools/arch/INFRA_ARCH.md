<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: infrastructure
  required_sections:
    - "Overview"
  skip_if: no-infrastructure
-->
# INFRA_ARCH.md

> **TEMPLATE_INTENT:** Document cloud resources, networking, and infrastructure. The map of deployed systems.

> Last updated: 2026-09-23
> Updated by: Copilot (hardening-release US-004)

---

## Overview

Forage's entire infrastructure is **one container image, two compose fragments, one CI
workflow, and one registry**. It is **self-hosted**: the image runs under Docker or
`docker compose` on the operator's own private network. There is no cloud account, no
Terraform or Pulumi, no Kubernetes or Helm, no load balancer, no CDN, no DNS management,
and no managed database. Everything that defines the deployed system is committed in this
repo: `Dockerfile`, `compose/minimal.yml`, `compose/full.yml`, and
`.github/workflows/ci.yml`.

Infrastructure is managed via: **the committed `Dockerfile`, `compose/*.yml`, and the CI
workflow** — no IaC tool, no state file, no console.

There is one shipped artefact and no staging/production split in this repo. The only
"environments" are the places the same image runs:

| Environment | Purpose | Where |
|-------------|---------|-------|
| **Shipped image** | The artefact consumers deploy (Poppy today; open-source users via `compose/`) | `ghcr.io/washingbearlabs/forage:<tag>` on the operator's private network |
| **CI smoke (pre-publish)** | The candidate image is run token-less on `ubuntu-latest` and probed by `contract_smoke.py` before `publish` may push | Ephemeral, per workflow run |
| **Local development** | Build and run on a developer machine (see `kit_tools/docs/LOCAL_DEV.md`) | `http://127.0.0.1:8020` |

Poppy runs the image on its own private network; those deployment details (host, network
name, env file, its compose healthcheck) live in the Poppy repo and are out of scope here.
The runtime dependency and failure matrix is in `kit_tools/arch/SERVICE_MAP.md`; this
document covers what physically runs and where.

---

## Architecture Diagram

The compose project network is the only boundary. Forage is the sole published port, and it
is bound to loopback.

```
 host                                                      egress (outbound only)
 ┌───────────────────────────────────────────────────────┐
 │  127.0.0.1:8020  ◄── the only published port           │
 │        │                                               │
 │  ┌─────┼─────── compose network (forage-minimal /      │
 │  │     │                         forage-full) ───────┐ │
 │  │  ┌──▼──────────────────────┐                      │ │
 │  │  │ forage   :8020          │──── http://searxng:8080 ─┐ │
 │  │  │ uvicorn, user poppy     │                      │ │ │
 │  │  │ mem_limit 1024m         │──── redis://valkey:6379/4 (full.yml only)
 │  │  │ HF_HOME=/app/model-cache│                      │ │ │
 │  │  └──┬──────────────────────┘                      │ │ │
 │  │     │ volume forage-model-cache (~270 MiB weights) │ │ │
 │  │     │                                              │ │ │
 │  │  ┌──▼──────────────────────┐   ┌─────────────────┐ │ │ │
 │  │  │ searxng  :8080          │   │ valkey :6379    │ │ │ │
 │  │  │ no published ports      │   │ full.yml only   │ │ │ │
 │  │  │ SEARXNG_SECRET required │   │ no password     │ │ │ │
 │  │  └─────────────────────────┘   │ vol forage-     │ │ │ │
 │  │                                │  valkey-data    │ │ │ │
 │  │                                └─────────────────┘ │ │ │
 │  └────────────────────────────────────────────────────┘ │ │
 └─────────────────────────────────────────────────────────┘ │
                                                             │
   forage  ──► huggingface.co (gated weights, needs HF_TOKEN) ─┘
   forage  ──► ghcr.io/washingbearlabs/forage-weights (private oras mirror, FORAGE_MIRROR_TOKEN)
   forage  ──► arbitrary public web (/retrieve fetches; private IPs refused by url_validator.py)
   searxng ──► duckduckgo / brave / startpage / mojeek
```

---

## Container Image

`Dockerfile` builds a **single-stage** image, deliberately: `docker history` only covers the
final stage, so a multi-stage build would narrow what CI's `secret-grep` gate can see
(`tests/test_dockerfile.py::test_single_from_instruction`).

| Property | Value |
|----------|-------|
| Base | `python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea` (resolved `3.12.14-slim-trixie` on 2026-09-07; multi-arch index) |
| Build arguments | **None.** Zero `ARG` instructions of any kind, not even `TARGETARCH` — `CLAUDE.md` invariant 2, asserted by `tests/test_dockerfile.py::test_the_build_takes_no_arguments_at_all` |
| System packages | `curl` only (`--no-install-recommends`); apt lists and logs removed in the same layer for reproducibility, not size |
| `uv` | `COPY --from=ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb` — same version CI pins |
| Dependency install | `uv sync --locked --no-dev --no-install-project` into `/app/.venv` with `UV_PYTHON_DOWNLOADS=never`; `uv.lock` pins CPU-only torch (`torch==2.14.0+cpu` from the `pytorch-cpu` index, no `nvidia-*` wheels) |
| `oras` | 1.3.4 downloaded from GitHub releases over https; sha256 pinned per architecture; arch chosen by `dpkg --print-architecture` inside the `RUN` (works under buildx and the legacy builder); unknown arch exits 1 |
| User | `useradd -r -s /bin/false poppy`; `USER poppy` (the name is a legacy leftover and still the literal in the file) |
| Runtime env set by image | `PATH=/app/.venv/bin:...` and `HF_HOME=/app/model-cache` — nothing else |
| Workdir / contents of `/app` | Flat modules `retrieval_app.py`, `models.py`, `cache.py`, `url_validator.py`, `model_fetcher.py`, `config.yaml`, `weights_manifest.json`; packages `pipeline/`, `promptguard/`; `contract/` (`openapi.yaml`, `openapi.yaml.sha256`, `GOVERNANCE.md`); `docker-entrypoint.sh` |
| Not copied | `tests/`, `scripts/`, `kit_tools/`, `contract_smoke.py`, `searxng_smoke.py` (`.dockerignore` plus filename-enumerated `COPY`) |
| Build-time check | `RUN PYTHONDONTWRITEBYTECODE=1 python -c "import retrieval_app"` — fails the build on an import error without writing `.pyc` into the layer |
| Port / process | `EXPOSE 8020`; `ENTRYPOINT ["/app/docker-entrypoint.sh"]` (`exec "$@"`, prints nothing); `CMD uvicorn retrieval_app:app --host 0.0.0.0 --port 8020`, one worker |
| Healthcheck | **No image-level `HEALTHCHECK` instruction; both compose fragments declare a status-only liveness probe.** |
| Size | ~348 MB, 16-19 layers (recorded at `forage-ci-and-image` US-003; not re-measured here) |

The flat module layout is load-bearing for the `COPY` lines above (`CLAUDE.md` invariant
3). The in-image contract can be read back with
`docker run --rm --entrypoint cat <image> /app/contract/openapi.yaml` and verified against
`contract/openapi.yaml.sha256` at the same tag.

### Reproducible builds

CI sets `SOURCE_DATE_EPOCH` to the commit's committer date and passes
`rewrite-timestamp=true` on every exporter; the Dockerfile removes apt/dpkg logs and writes
no bytecode. Two `--no-cache` builds were measured at 19/19 identical layers. The known bound
is one UTC day, because `useradd` day-stamps `/etc/shadow`. This is what lets `publish`
rebuild amd64 from cache and prove, layer for layer, that it pushed the image `smoke` ran.

### Companion image

`searxng/Dockerfile` is `FROM` a digest-pinned upstream `searxng/searxng` (2026.9.7) plus
`COPY --chown=searxng:searxng config/ /etc/searxng/`. `searxng/config/settings.yml` enables
duckduckgo, brave, startpage, and mojeek, listens on `0.0.0.0:8080`, bakes no `secret_key`,
and leaves the limiter off (a working limiter 429s Forage's own client). See
`docs/searxng.md`.

---

## Compose Fragments

`compose/minimal.yml` (project `forage-minimal`) and `compose/full.yml` (project
`forage-full`) are standalone — no `extends`, no shared base — and their duplication is
guarded by `tests/test_compose_fragments.py`. CI's `lint` job runs `docker compose config -q`
on both. Both auto-read `compose/.env` (gitignored), which is expected to hold `HF_TOKEN` and
`SEARXNG_SECRET`:

```bash
cd compose
{ echo "HF_TOKEN=hf_..."; echo "SEARXNG_SECRET=$(head -c 32 /dev/urandom | base64)"; } > .env
docker compose -f minimal.yml up -d && curl -s localhost:8020/health | jq
```

| Service | In | Image (as pinned today) | Ports | Volumes | Limits / env |
|---------|----|-------------------------|-------|---------|--------------|
| `forage` | both | `ghcr.io/washingbearlabs/forage:1.2.1` (pending cut) | `127.0.0.1:8020:8020` | `forage-model-cache:/app/model-cache` | `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` (default `1024m`), `cpus: ${FORAGE_CPUS:-0}`, `restart: unless-stopped`; bare `HF_TOKEN`, `FORAGE_MODEL_ID`, `FORAGE_MODEL_REVISION`, `FORAGE_SEARCH_PROVIDERS` and `FORAGE_BRAVE_API_KEY` pass-through (each stays unset if unset; credentials belong in `compose/.env`, never inline); `SEARXNG_URL` not set (default `http://searxng:8080`) |
| `forage` extras | full only | same | same | same | literal `VALKEY_URL=redis://valkey:6379/4` (DB 4 matches `ContentCache`'s default), bare `FORAGE_CACHE_HMAC_KEY` for signing; unset means unsigned cached content and `cache_unauthenticated`. Minimal omits `VALKEY_URL` entirely so the cache runs in memory mode |
| `searxng` | both | `ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc` | **none published** | none | `SEARXNG_SECRET: ${SEARXNG_SECRET:?...}` — unset is a hard start failure; service name `searxng` is load-bearing for Forage's default URL |
| `valkey` | full only | `valkey/valkey:8@sha256:3fbd2e3e4b6e85e046c1e7c215e8f79087bc0357789184305806664e320996f3` (8.1.10) | none | `forage-valkey-data:/data` (project-scoped) | `valkey-server --save 60 1 --appendonly no`; no password |

Controls and deliberate omissions in both fragments:

- **Liveness healthcheck on Forage, no `depends_on`**. Forage does not need SearXNG
  or Valkey to start; a missing SearXNG surfaces per request as a 422, and a missing Valkey
  surfaces as `degraded: cache_unavailable` in `/health` (see `kit_tools/arch/SERVICE_MAP.md`).
- **Configurable CPU quota** via `cpus: ${FORAGE_CPUS:-0}` (unset/0 omits the cap),
  plus the memory limit; `pids_limit` is still absent.
- **No TLS, no auth.** The `127.0.0.1` binding is the deployment-posture control.

**Pins:** both fragments pin `forage:1.2.1` and `forage-searxng:0.1.1-rc`.
The current release target is **v1.2.1 / contract 1.3.0**, not yet published.
The service pin fails with `manifest unknown` until the owner cut; cut from the
replacement PR's merge commit in the same sitting or restore a verified release
per `docs/releases.md`, never defective v1.2.0.
That window is outstanding, with the exact commit recorded in the replacement PR.
The companion is published and stays a pre-release because no non-pre-release
`searxng-v*` tag exists.

---

## Registry and Tags

All images live on GitHub Container Registry under `ghcr.io/washingbearlabs/`. The repo and
the two service images have been public since 2026-09-10 (`forage-ci-and-image` US-008);
anonymous pulls were verified at that flip.

| Image | Visibility | Platforms | Purpose |
|-------|------------|-----------|---------|
| `ghcr.io/washingbearlabs/forage` | public | `linux/amd64`, `linux/arm64` | The service |
| `ghcr.io/washingbearlabs/forage-searxng` | public | `linux/amd64`, `linux/arm64` | Companion SearXNG, own `searxng-v*` tag lane |
| `ghcr.io/washingbearlabs/forage-weights:<revision>` | **private** | OCI artifact | Vendored PromptGuard weights mirror; WashingBearLabs-only, needs `FORAGE_MIRROR_TOKEN` at runtime |

Tag scheme (`docker/metadata-action` with `flavor: latest=false` and four explicit rules in
`.github/workflows/ci.yml`; reference: `docs/releases.md`):

| Git event | `forage` tags produced | Release |
|-----------|------------------------|---------|
| push to `main` | `sha-<short>` | none |
| tag `vX.Y.Z` | `X.Y.Z`, `X.Y`, `latest` | yes, with `openapi.yaml` + `openapi.yaml.sha256` assets |
| tag containing `-` (e.g. `v0.9.3-rc`) | exact version only | yes, `--prerelease` |
| tag `searxng-vX.Y.Z` | nothing on this image; `X.Y.Z`, `X.Y`, `latest` on `forage-searxng` | none |

Tags present at the time of writing: `v0.9.0-rc`, `v0.9.1-rc`, `v0.9.3-rc`, `v1.0.0`,
`v1.1.0`, `searxng-v0.1.0-rc`, `searxng-v0.1.1-rc`. `v0.9.2-rc` was withdrawn after failing
the post-push layer-parity gate; deletion of its GHCR package version is recorded as pending.
`v1.0.0` is the first non-pre-release tag and the first to move `latest`; `v1.1.0`
(`search-release` US-002) moved it again, alongside `1.1`. `docs/releases.md` §
"Released versions" has the digests.

The git tag **is** the version. `pyproject.toml`'s `version = "0.1.0"` is inert packaging
metadata, and the image tag (`v1.2.1`, pending) and `contract_version` (`1.3.0`)
are independent semvers. **arm64 is built under QEMU but never executed in CI** — run `contract_smoke.py`
against your own arm64 container before trusting it.

### Registry access

- **Pull:** anonymous for `forage` and `forage-searxng`.
- **Push:** only CI's `publish` / `searxng-publish` jobs, via `docker/login-action` with the
  workflow `GITHUB_TOKEN` (`packages: write`), and only behind all six required checks.
  Registry tags are treated as immutable by policy; rollback is re-pinning an older tag in
  the consumer's compose, never re-cutting a tag.
- **Weights vendoring:** a human with `GHCR_USER`/`GHCR_TOKEN` (`write:packages`) runs
  `scripts/vendor_weights.py`; CI never pushes to `forage-weights` (`docs/weights.md`).

---

## Storage

The only persistent storage this repo defines is the model-cache volume. Nothing here needs
a backup: the volume is regenerated by re-download, and Valkey is a cache whose loss is a
cold start.

| Volume | Mount | Declared in | Contents | Refresh |
|--------|-------|-------------|----------|---------|
| `forage-model-cache` (explicit `name:`, shared by both fragments) | `/app/model-cache` (`HF_HOME`) | `compose/minimal.yml`, `compose/full.yml` | `hub/` (the verified snapshot of `meta-llama/Llama-Prompt-Guard-2-22M` at revision `11614a155199674a0a95e6602d6ab0417b790ed0`), `staging/`, `quarantine/`, `xet/`; ~270 MiB (`model.safetensors` is 283,347,432 bytes) | Background task at every start: verify cached set against `weights_manifest.json` (5 files, sha256 + size, exact-set allowlist); if absent or refused, download from Hugging Face Hub (`HF_TOKEN`) then the oras mirror; refused sets move to `quarantine/` (one generation kept); retry 30 s doubling to 600 s, forever |
| `forage-valkey-data` (project-scoped) | `/data` | `compose/full.yml` only | Valkey RDB (`--save 60 1`, AOF off) | Operator's concern; not read by Forage |

A warm volume means a start with **zero network** (measured 9 s warm vs 19 s cold on the
reference envelope (1 vCPU / 1 GB), configurable via `FORAGE_CPUS` / `FORAGE_MEM_LIMIT` —
see `docs/configuration.md` § Sizing the container). Without a token the container stays up and loud:
`/health` reports `status: degraded`, `degraded_reasons: ["promptguard_unavailable"]`, and a
terminal ERROR is logged on every retry. Full details: `docs/weights.md` and
`kit_tools/docs/MONITORING.md`.

---

## Networking and Exposure

Forage ships **no authentication on any route** (`/health`, `/metrics`, `/search`,
`/retrieve`, `/extract`, and FastAPI's `/docs`, `/redoc`, `/openapi.json`) and **no TLS
termination**. Network placement is the only access control: the compose fragments bind the
single published port to `127.0.0.1`, SearXNG and Valkey publish nothing, and README's
deployment posture is "private network only". Anything that puts Forage on a routable
address is the operator's own reverse proxy and auth, outside this repo.

| Flow | Source | Destination | Port / protocol | When |
|------|--------|-------------|-----------------|------|
| Ingress | host loopback (`127.0.0.1`) | `forage` | 8020 / HTTP | always; the only published port |
| Compose-internal | `forage` | `searxng` | 8080 / HTTP (`SEARXNG_URL`, 10 s timeout, no retries) | per `/search` request |
| Compose-internal | `forage` | `valkey` | 6379 / RESP (`VALKEY_URL`, 2 s connect timeout) | `full.yml` only; start + per `/retrieve` |
| Egress | `forage` | `huggingface.co` | 443 / HTTPS | at start, until a verified weight set is loaded; needs `HF_TOKEN` |
| Egress | `forage` | `ghcr.io` (weights mirror, via `oras`) | 443 / HTTPS | fallback only when `FORAGE_MIRROR_TOKEN` is set |
| Egress | `forage` | `api.search.brave.com` | 443 / HTTPS, direct (`trust_env=False`, no proxy) | one request per `/search`, only when `FORAGE_BRAVE_API_KEY` is set |
| Egress | `forage` | arbitrary public web | 80/443 / HTTP(S) | per `/retrieve` request; private and loopback ranges refused by `url_validator.py` |
| Egress | `searxng` | duckduckgo, brave, startpage, mojeek | 443 / HTTPS | per `/search` request |

Note that `/retrieve` error `reason` strings echo the requested URL and, for `private_ip`,
the resolved address (a documented `contract/GOVERNANCE.md` ruling). That is a DNS oracle if
the service is ever exposed beyond a private network — another reason not to.

---

## Resource Envelope

The reference envelope (1 vCPU / 1 GB) is configurable via `FORAGE_CPUS` / `FORAGE_MEM_LIMIT`;
see [`docs/configuration.md` § Sizing the container](../../docs/configuration.md#sizing-the-container).
Add the ~270 MiB volume; these are the
figures the measurements in `docs/configuration.md` were taken on, not a tested floor or
ceiling. Torch is CPU-only by construction (no CUDA wheels in the lock), so no GPU is ever
used.

| Budget | Value | Source |
|--------|-------|--------|
| Container memory cap | `mem_limit: ${FORAGE_MEM_LIMIT:-1024m}` (default `1024m`) | both compose fragments |
| Container CPU cap | `${FORAGE_CPUS:-0}` (0 = no limit) | both compose fragments |
| — parent process (FastAPI + torch + PromptGuard) | ~512 MiB | budget breakdown in `docs/configuration.md` |
| — extraction child address space | 384 MiB (`extraction.*` in `config.yaml`) | same |
| — headroom | ~128 MiB: 32 MiB in-memory cache + 64 MiB provisional classifier working set + 32 MiB margin | same |
| Extraction admission | extraction concurrency 1, queue depth 1 (0–4), 50 MiB input, 500 pages, 20 s CPU, 90 s wall; `classification_concurrency` 1–8 under the memory rule | `config.yaml` (see `kit_tools/docs/ENV_REFERENCE.md`) |
| Weights boot to classifier loaded | 19 s cold / 9 s warm on the reference host; `/health` serves during acquisition, not a classify-latency figure | `docs/configuration.md` § Sizing the container |
| CI smoke budget | 120 s to first `/health` 200 | `SMOKE_TIMEOUT_SECONDS` in `ci.yml` |
| Disk | ~270 MiB volume; ~348 MB image (recorded, not re-measured); ~1 GB free recommended for vendoring | `docs/weights.md` |

Live memory pressure is readable from `/metrics` `extraction.cgroup_memory_current_bytes`,
`cgroup_memory_max_bytes`, and `oom_proximity_ratio` (cgroup v2 only; `null` on macOS).
For non-zero `FORAGE_CPUS`, keep threads × classification concurrency within the
quota; `docker inspect -f '{{.HostConfig.NanoCpus}}' <container>` verifies it.

---

## CI and Publish Pipeline

One GitHub Actions workflow, `.github/workflows/ci.yml`, on GitHub-hosted `ubuntu-latest`
runners, is the only pipeline. Six required checks on `main` — `lint`, `typecheck`, `test`,
`build-amd64`, `secret-grep`, `smoke` — gate a `publish` job that rebuilds amd64 from the
GHA cache, pushes the multi-arch image, then reads the published image back and proves its
amd64 `rootfs.diff_ids` equal the artefact `smoke` executed and `secret-grep` cleared. On
`v*` tags it also creates a Release carrying `contract/openapi.yaml` and its sha256 anchor
and verifies both back from the API. Every action is SHA-pinned, top-level permissions are
`contents: read`, and no image signing, provenance, or SBOM is produced (declared out of
scope). Job table, cache scopes, failure modes, and recovery: `kit_tools/docs/CI_CD.md` and
`docs/releases.md`.

---

## Secrets at Runtime

Every credential enters through the **container environment at runtime** and nothing else:
no build argument, no secret store, no config file. `docker-entrypoint.sh` prints nothing so
a `VALKEY_URL` password can never reach the log, and `cache.py` and `model_fetcher.py` log
only closed reason vocabularies (`CLAUDE.md` invariant 6).

| Variable | Consumed by | Purpose | Required? |
|----------|-------------|---------|-----------|
| `HF_TOKEN` | `forage` | Read access to the gated Hugging Face repo | No — absent means degraded mode |
| `FORAGE_MIRROR_TOKEN` | `forage` | Read-only token for the private oras weights mirror; handed to `oras` on stdin | No |
| `VALKEY_URL` | `forage` | May embed a password (`redis://:password@host:6379/4`) | No — fully unset means memory mode |
| `SEARXNG_SECRET` | `searxng` | SearXNG's own secret key | **Yes** for the companion; unset exits 1 |

Operator rules from `docs/configuration.md`: use `compose/.env` or `--env-file`, never inline
`-e` (shell history, `ps`); `docker inspect` exposes the environment to anyone with socket
access; rotation is a restart. The controls that keep secrets out of the image —
`tests/test_dockerfile.py`, CI `secret-grep` on layer history, the `publish` config grep,
and GitHub secret scanning with push protection — are described in
`kit_tools/arch/SECURITY.md`.

---

## Cost

There is no cloud bill: the only recurring infrastructure is GitHub-hosted CI runners and
GHCR storage for a public repository, both within GitHub's free allowance at current usage.

---

## Deployment

Deploying is pulling a tag and running it on a private network. The two compose fragments
are the reference; the `docker run --rm -p 127.0.0.1:8020:8020 --env-file ./forage.env
-v "$PWD/config.yaml:/app/config.yaml:ro" ghcr.io/washingbearlabs/forage:<tag>` form is
equivalent. Cutting a release is `git tag vX.Y.Z && git push origin vX.Y.Z` and watching the
run; rollback is re-pinning an older tag. Procedures: `kit_tools/docs/DEPLOYMENT.md`;
what a green publish proves: `docs/releases.md`; local builds: `kit_tools/docs/LOCAL_DEV.md`.

---

## Security Considerations

See `kit_tools/arch/SECURITY.md` for the full posture. The infrastructure-level facts:

- **No auth, no TLS in-repo** — the service is safe only on a private network; the compose
  loopback binding is the control, and it must not be loosened without adding a proxy.
- **Secrets are runtime-only** — zero build `ARG`s, guarded mechanically in the Dockerfile
  tests and in CI's layer-history and published-config greps.
- **Non-root process** — `USER poppy`; the model cache is the only writable path it needs.
- **Pinned supply chain** — base image, `uv`, `oras`, Valkey, and SearXNG by digest or
  per-arch sha256; every GitHub Action by commit SHA; `uv sync --locked`.
- **Provided at Compose level** — a status-only liveness healthcheck and the `cpus`
  knob; neither is an in-service classifier readiness or CPU auto-detection signal.
- **Not provided** — image signing, provenance, SBOM (explicitly deferred);
  pids limits; log shipping. Alerting is the operator's to build on
  the `/health` body and `/metrics` (`kit_tools/docs/MONITORING.md`).
