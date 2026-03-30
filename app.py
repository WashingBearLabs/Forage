"""Poppy Retrieval sidecar -- FastAPI service for web content retrieval.

Isolated container that fetches, sanitizes, and caches web content.
No direct database access. Communicates with core via internal API only.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cache import ContentCache
from models import RetrievedContent, RetrieveRequest, SearchRequest, SearchResponse
from pipeline.orchestrator import PipelineError, run_retrieve_pipeline, run_search_pipeline
from promptguard.classifier import PromptGuardClassifier

logger = logging.getLogger(__name__)

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://poppy-valkey:6379/4")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://poppy-searxng:8080")

# -- Module-level state --

_valkey_connected: bool = False
_classifier: PromptGuardClassifier = PromptGuardClassifier()
_cache: ContentCache | None = None
_config: dict[str, Any] = {}


def _load_config() -> dict[str, Any]:
    """Load sidecar configuration from ``config.yaml``."""
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    logger.warning("config.yaml not found at %s", config_path)
    return {}


async def _check_valkey() -> bool:
    """Check if Valkey/Redis is reachable."""
    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        client: aioredis.Redis = aioredis.from_url(  # type: ignore[assignment]
            VALKEY_URL, socket_connect_timeout=2,
        )
        await client.ping()  # type: ignore[misc]
        await client.aclose()
        return True
    except Exception:
        logger.warning("Valkey connection check failed")
        return False


# -- Response models --


class HealthResponse(BaseModel):
    """Response body for ``GET /health``."""

    status: str
    promptguard_loaded: bool
    cache_connected: bool


# -- Lifespan --


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown lifecycle."""
    global _valkey_connected, _cache, _config

    # Load config
    _config = _load_config()
    logger.info("Sidecar config loaded (%d keys)", len(_config))

    # Connect content cache
    _cache = ContentCache(VALKEY_URL)
    cache_ok = await _cache.connect()
    _valkey_connected = cache_ok
    if cache_ok:
        logger.info("Content cache connected (Valkey)")
    else:
        logger.warning("Content cache not available at startup")

    # Load PromptGuard 2 model (CPU inference)
    if _classifier.load():
        logger.info("PromptGuard 2 model ready")
    else:
        logger.warning("PromptGuard 2 not available — ML injection detection disabled")

    yield

    # Shutdown
    if _cache is not None:
        await _cache.close()
        _cache = None


# -- App --

app = FastAPI(
    title="Poppy Retrieval Sidecar",
    version="0.1.0",
    lifespan=lifespan,
)


# -- Error handler --


@app.exception_handler(PipelineError)
async def pipeline_error_handler(
    request: Request,
    exc: PipelineError,
) -> JSONResponse:
    """Return structured JSON for pipeline errors."""
    return JSONResponse(
        status_code=422,
        content=exc.to_dict(),
    )


# -- Routes --


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service health status."""
    global _valkey_connected
    # Re-check Valkey on each health call for accurate status
    _valkey_connected = await _check_valkey()
    return HealthResponse(
        status="healthy",
        promptguard_loaded=_classifier.loaded,
        cache_connected=_valkey_connected,
    )


@app.post("/retrieve", response_model=RetrievedContent)
async def retrieve(request: RetrieveRequest) -> RetrievedContent:
    """Retrieve and sanitize web content through the full pipeline."""
    return await run_retrieve_pipeline(
        request,
        cache=_cache,
        classifier=_classifier,
        config=_config,
    )


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    """Run a web search through SearXNG with snippet sanitization."""
    return await run_search_pipeline(
        request,
        searxng_url=SEARXNG_URL,
        config=_config,
    )
