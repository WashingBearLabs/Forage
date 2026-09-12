# Forage — web content fetching, extraction, and sanitization service.
#
# Four properties of this file are load-bearing for an image that is going to
# be pushed to a public registry, and all four are guarded mechanically rather
# than by convention:
#
#  1. NO SECRET EVER ENTERS THE BUILD. There is no `ARG HF_TOKEN` and no
#     model-bake step. A build argument is not a secret: Docker records it in
#     the finished image's layer history, where `docker history --no-trunc`
#     reads it straight back out — from the local image, and from every
#     registry the image reaches. Deleting the path is the fix; scrubbing the
#     filesystem is not. The PromptGuard weights arrive at *runtime* instead
#     (`feature-forage-model-bootstrap`); until they do, an image built from
#     this file has no weights and the service says so, reporting
#     `status: "degraded"` with `promptguard_unavailable`.
#     Guards: `tests/test_dockerfile.py` is the permanent canary on this file's
#     text, and CI's `secret-grep` job greps the built image's history on every
#     run (`.github/workflows/ci.yml`).
#
#  2. DEPENDENCIES COME FROM THE COMMITTED LOCK. `uv sync --locked` fails
#     rather than resolving something new, so the image contains exactly the
#     versions CI linted, type-checked and tested — and exactly the CPU-only
#     torch `uv.lock` pins, instead of the ~2.7 GB of `nvidia-*` CUDA wheels a
#     fresh resolution drags in. The previous form of this file parsed
#     `pyproject.toml` with a shell one-liner and pip-installed the unpinned
#     ranges, which is a different dependency set from the one under test.
#
#  3. THE BASE IS DIGEST-PINNED. US-005's `smoke` job proves that a specific
#     image behaves and US-007's `publish` job ships one; that argument
#     collapses if `python:3.12-slim` can move between the two builds.
#     Dockerfile syntax has no inline comments — a `#` after an instruction's
#     arguments is parsed as another argument and `FROM` rejects it — so the
#     tag this digest resolved to is recorded on the comment line directly
#     above, and `tests/test_dockerfile.py` asserts that pairing stays there.
#
#  4. EVERY DOWNLOADED BINARY IS PINNED AND CHECKSUMMED. The `oras` client the
#     runtime weights fetch shells out to arrives by `curl`, not by digest, so
#     its version is exact and its sha256 is pinned per architecture — see the
#     block above the install. A fetch tool that could move underneath us would
#     be a weaker link than the weights it is fetching, which are pinned
#     file-by-file in `weights_manifest.json`.
#
# Base tag at pin time: python:3.12-slim == 3.12.14-slim-trixie (2026-09-07)
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

WORKDIR /app

# System deps: curl for the container healthcheck.
#
# The logs and the ldconfig cache are deleted for **reproducibility**, not for
# size — they are the entire reason this layer used to differ between two builds
# of the same commit. `apt` and `dpkg` write wall-clock timestamps into their
# logs, and `ldconfig`'s `aux-cache` records inode metadata, so a rebuild minutes
# later produces different files and therefore a different layer digest. That is
# what failed `publish`'s diff_ids parity gate on a cold cache
# (kit_tools/docs/GOTCHAS.md, "A cold-cache publish fails its own parity gate").
#
# Measured layer-by-layer at US-004, exactly four files drifted:
# `/var/log/apt/history.log`, `/var/log/apt/term.log`, `/var/log/dpkg.log` and
# `/var/cache/ldconfig/aux-cache`. `/var/log/alternatives.log` is removed too and
# is precautionary rather than measured — same class, same one-line cost, and it
# appears the moment a future package triggers `update-alternatives`.
#
# Nothing reads any of them: the image performs exactly one apt transaction, at
# build time, and ships no package-manager workflow.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/log/apt /var/log/dpkg.log /var/log/alternatives.log \
       /var/cache/ldconfig/aux-cache

# uv itself, digest-pinned, at the same version CI's setup-uv installs
# (ci.yml's `UV_VERSION`). Pinning the tag alone would leave the one tool that
# enforces the lock free to change underneath it.
COPY --from=ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb /uv /uvx /bin/

# `oras`, the OCI client the runtime weights fetch shells out to when Hugging
# Face cannot supply the pinned revision (`feature-forage-model-bootstrap`
# US-004). GHCR requires a bearer-token exchange even with a PAT; `oras` is
# here so we do not hand-roll one.
#
# Three properties, each guarded by tests/test_dockerfile.py:
#
#  1. EXACT VERSION, PER-ARCH SHA256. The tool that fetches the weights meets
#     the same supply-chain bar as the weights it fetches — the manifest pins
#     every weight file by sha256, so a `curl | tar` of a moving release would
#     be the weakest link in the chain, on both published platforms. The
#     digests below are from the release's own `oras_1.3.4_checksums.txt`
#     (https://github.com/oras-project/oras/releases/tag/v1.3.4).
#
#  2. NO BUILD ARG, NOT EVEN `TARGETARCH`. `ARG TARGETARCH` is the usual way to
#     select a per-architecture asset, but this file takes *no* build arguments
#     at all (CLAUDE.md invariant 2) and that absolute is worth more than the
#     convention: it is mechanically checkable, and "no ARG except the harmless
#     ones" is how the next HF_TOKEN gets argued in. `dpkg --print-architecture`
#     answers the same question from inside the build — the RUN executes in the
#     *target* platform's rootfs under buildx, so it reports the target, not the
#     builder — in exactly the names oras publishes (amd64, arm64), and it works
#     under the legacy builder too, where TARGETARCH would be empty.
#
#  3. AN UNKNOWN ARCHITECTURE FAILS THE BUILD. Falling back to "some arch" or
#     skipping the install would ship an image whose mirror path dies at
#     runtime, on the fallback, during the outage that made someone need it.
#
# oras 1.3.4 (released 2026-08-27)
RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) sha256=f27adb935022d94df8dc77719c322dda592c78a0d57a6f7dcdd8d900b248c454 ;; \
      arm64) sha256=15702c6e3a4a56a8bd8ac5c17efdbcab56d9bada661ccbcf017f5b10c1d89399 ;; \
      *) echo "no pinned oras build for ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/oras.tar.gz \
      "https://github.com/oras-project/oras/releases/download/v1.3.4/oras_1.3.4_linux_${arch}.tar.gz"; \
    echo "${sha256}  /tmp/oras.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/oras.tar.gz -C /usr/local/bin oras; \
    rm /tmp/oras.tar.gz; \
    chmod 0755 /usr/local/bin/oras; \
    oras version

# Dependencies first, before the source, so an application edit does not
# invalidate the (large) dependency layer.
#
#   --locked              fail if uv.lock is stale relative to pyproject.toml,
#                         rather than silently resolving different versions.
#   --no-dev              the test toolchain has no business in a runtime image.
#   --no-install-project  Forage's modules are flat at /app and are imported
#                         from the working directory — uvicorn puts the cwd on
#                         sys.path — exactly as the pre-uv image did. Installing
#                         the project as a wheel as well would ship a second
#                         copy of every module.
#
# UV_PYTHON_* keep uv on the base image's interpreter: its default preference
# is a uv-managed Python, which would download a *second* 3.12 and leave the
# image running one interpreter while CI type-checked another. They are set on
# the command rather than as ENV so nothing leaks into the runtime environment.
COPY pyproject.toml uv.lock ./
RUN UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    uv sync --locked --no-dev --no-install-project \
    && rm -rf /root/.cache/uv
ENV PATH="/app/.venv/bin:${PATH}"

# Non-root user, plus the model-cache directory it has to be able to write:
# this is where the runtime weights fetch will land.
RUN useradd -r -s /bin/false poppy \
    && mkdir -p /app/model-cache \
    && chown poppy:poppy /app/model-cache

# Points transformers/huggingface_hub at a directory the `poppy` user owns.
# The directory is empty in this image and stays empty until a runtime fetch
# fills it — see property 1 above, and GOTCHAS.md's PromptGuard entry for what
# "degraded" means for a consumer in the meantime.
ENV HF_HOME=/app/model-cache

# Application source. The COPY list is filename-enumerated on purpose — the
# CI-only smoke drivers stay out of the image that way — which also means a new
# module is invisible to the runtime until it is named here. `model_fetcher.py`
# and the `weights_manifest.json` it verifies against are both runtime inputs:
# without them the runtime weights fetch has no verifier and no pin, and would
# have to either load unverified bytes or refuse to load at all.
COPY retrieval_app.py models.py cache.py url_validator.py config.yaml ./
COPY model_fetcher.py weights_manifest.json ./
COPY promptguard/ ./promptguard/
COPY pipeline/ ./pipeline/

# The frozen wire contract, shipped *inside* the image so a consumer can read
# it out of the artifact they already pull:
#
#     docker run --rm --entrypoint cat <image> /app/contract/openapi.yaml
#
# Three properties are deliberate. The directory is copied whole rather than
# file-enumerated like the modules above, because `contract/` is a published
# unit: `openapi.yaml`, the `openapi.yaml.sha256` anchor that verifies it, and
# `GOVERNANCE.md` — the semver rules — travel together, and a consumer who
# vendors the document from the image gets the rules that govern it in the same
# directory. No module imports any of it: nothing in `retrieval_app` reads
# `/app/contract/`, so a missing file here cannot break the service, which is
# exactly why `tests/test_dockerfile.py` guards the COPY rather than relying on
# the build-time import check to notice. And the bytes are the committed bytes —
# `contract/openapi.yaml.sha256` is the trust anchor, so the in-image copy
# verifies against the same `sha256sum -c openapi.yaml.sha256` as the Release
# asset and the git tag (contract/GOVERNANCE.md, "Consumers"). CI's `smoke` job
# checks that on every run, against the running container's own
# `/health.contract_version`.
COPY contract/ /app/contract/

# Build-time import check — fails fast if the module is broken before it ships.
#
# `PYTHONDONTWRITEBYTECODE=1` is the second half of the reproducibility fix, and
# it costs nothing it was not already losing. Importing the app compiles ~580
# dependency modules and writes their `.pyc` into the venv; a timestamp-based
# `.pyc` embeds its source's mtime, which differs between two checkouts of the
# same commit, so this layer drifted by 579 files. It could not be normalized at
# export either: the mtime lives *inside* the `.pyc` bytes, not in the tar
# header.
#
# The cache those writes left behind is void anyway once the export rewrites
# timestamps — measured in a built image: every source reads
# `SOURCE_DATE_EPOCH` while its `.pyc` still records the build clock, so Python
# recompiles on import and cannot write the result back (the venv is root-owned
# and the service runs as `poppy`). So the choice was never "cache or no cache";
# it was "dead files that break the parity gate" or "no dead files". Measured
# cost of the import with no usable cache: ~2.3 s against ~0.9 s warm, once per
# container start, against a 120 s health budget.
#
# If that ever matters, the fix is hash-based `.pyc` (PEP 552,
# `compileall --invalidation-mode unchecked-hash`) over the modules the app
# actually imports — deterministic *and* valid. It is not here because
# compiling the whole venv would add minutes to the build and hundreds of MB of
# torch bytecode to the image.
RUN PYTHONDONTWRITEBYTECODE=1 python -c "import retrieval_app"

# The entrypoint is a bare `exec "$@"` shim. Forage takes all of its
# configuration from the environment (docs/configuration.md) and never talks to
# a secret store at boot.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

USER poppy

EXPOSE 8020

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "retrieval_app:app", "--host", "0.0.0.0", "--port", "8020"]
