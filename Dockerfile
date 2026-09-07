# Forage — web content fetching, extraction, and sanitization service
# Note: Uses pip instead of uv for image simplicity (no uv installation needed in slim image)
FROM python:3.12-slim

WORKDIR /app

# System deps: curl for healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps from pyproject.toml (excluding torch — installed separately)
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    $(python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); deps=[x for x in d['project']['dependencies'] if 'torch' not in x]; print(' '.join(deps))")

# Install torch CPU-only (smaller image — no CUDA)
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

# Create non-root user and model cache dir BEFORE downloading
RUN useradd -r -s /bin/false poppy \
    && mkdir -p /app/model-cache \
    && chown poppy:poppy /app/model-cache
ENV HF_HOME=/app/model-cache

# Pre-download PromptGuard 2 model (gated — requires HF_TOKEN build arg)
# Model is saved to /app/model-cache which is readable by the poppy user
#
# OPERATIONAL: an image built WITHOUT the token has NO PromptGuard model —
# the service then runs fail-closed for standard-tier content and silently
# discards every search result and flags every fetch (live 2026-08-19..28:
# search "broken" while SearXNG returned 10 good results). Supply your own
# token — Forage reads no secret store, at build time or at run time:
#   docker build --build-arg HF_TOKEN="$HF_TOKEN" -t forage .
# Verify after deploy (the body never lies, even under the break-glass flag):
#   curl -s localhost:8020/health | jq .promptguard_loaded   → true
#
# NEVER push an image built with the token to a registry you do not fully
# control: the build arg is recoverable from the layer history.
ARG HF_TOKEN=""
RUN if [ -n "$HF_TOKEN" ]; then \
      HF_TOKEN="$HF_TOKEN" python3 -c "import os; \
        from transformers import AutoTokenizer, AutoModelForSequenceClassification; \
        t = os.environ['HF_TOKEN']; \
        AutoTokenizer.from_pretrained('meta-llama/Llama-Prompt-Guard-2-22M', token=t); \
        AutoModelForSequenceClassification.from_pretrained('meta-llama/Llama-Prompt-Guard-2-22M', token=t)" \
      && chown -R poppy:poppy /app/model-cache; \
    else \
      echo 'No HF_TOKEN provided — skipping PromptGuard model download'; \
    fi

# Copy application source
COPY retrieval_app.py models.py cache.py url_validator.py config.yaml ./
COPY promptguard/ ./promptguard/
COPY pipeline/ ./pipeline/

# Build-time import check — fails fast if the module is broken before image ships
RUN python -c "import retrieval_app"

# Copy the entrypoint. It is a bare `exec "$@"` shim — Forage takes all of its
# configuration from the environment (docs/configuration.md) and never talks to
# a secret store at boot.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Switch to non-root user
USER poppy

EXPOSE 8020

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "retrieval_app:app", "--host", "0.0.0.0", "--port", "8020"]
