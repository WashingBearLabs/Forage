# Poppy Retrieval sidecar — web content fetching and sanitization
# Note: Uses pip instead of uv for sidecar simplicity (no uv installation needed in slim image)
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

# Pre-download PromptGuard 2 model (gated — requires HF_TOKEN build arg)
ARG HF_TOKEN=""
ENV HF_TOKEN=${HF_TOKEN}
RUN if [ -n "$HF_TOKEN" ]; then \
      python3 -c "import os; \
        from transformers import AutoTokenizer, AutoModelForSequenceClassification; \
        t = os.environ['HF_TOKEN']; \
        AutoTokenizer.from_pretrained('meta-llama/Prompt-Guard-2-22M', token=t); \
        AutoModelForSequenceClassification.from_pretrained('meta-llama/Prompt-Guard-2-22M', token=t)"; \
    else \
      echo 'No HF_TOKEN provided — skipping PromptGuard model download'; \
    fi
# Clear the token from the environment after download
ENV HF_TOKEN=""

# Copy application source
COPY app.py models.py cache.py url_validator.py config.yaml ./
COPY promptguard/ ./promptguard/
COPY pipeline/ ./pipeline/

# Non-root user
RUN useradd -r -s /bin/false poppy
USER poppy

EXPOSE 8020

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8020"]
