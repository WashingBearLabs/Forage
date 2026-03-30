# Poppy Retrieval sidecar — web content fetching and sanitization
FROM python:3.12-slim

WORKDIR /app

# System deps: curl for healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps from pyproject.toml
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    $(python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(' '.join(d['project']['dependencies']))")

# Copy application source
COPY app.py ./

# Non-root user
RUN useradd -r -s /bin/false poppy
USER poppy

EXPOSE 8020

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8020"]
