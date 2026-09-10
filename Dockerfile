# Forage — web content fetching, extraction, and sanitization service.
#
# Three properties of this file are load-bearing for an image that is going to
# be pushed to a public registry, and all three are guarded mechanically rather
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
# Base tag at pin time: python:3.12-slim == 3.12.14-slim-trixie (2026-09-07)
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

WORKDIR /app

# System deps: curl for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# uv itself, digest-pinned, at the same version CI's setup-uv installs
# (ci.yml's `UV_VERSION`). Pinning the tag alone would leave the one tool that
# enforces the lock free to change underneath it.
COPY --from=ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb /uv /uvx /bin/

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

# Build-time import check — fails fast if the module is broken before it ships.
RUN python -c "import retrieval_app"

# The entrypoint is a bare `exec "$@"` shim. Forage takes all of its
# configuration from the environment (docs/configuration.md) and never talks to
# a secret store at boot.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

USER poppy

EXPOSE 8020

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "retrieval_app:app", "--host", "0.0.0.0", "--port", "8020"]
