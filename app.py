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
    # Load config
    config = _load_config()
    app.state.config = config
    logger.info("Sidecar config loaded (%d keys)", len(config))

    # Connect content cache
    cache = ContentCache(VALKEY_URL)
    cache_ok = await cache.connect()
    app.state.cache = cache
    app.state.valkey_connected = cache_ok
    if cache_ok:
        logger.info("Content cache connected (Valkey)")
    else:
        logger.warning("Content cache not available at startup")

    # Load PromptGuard 2 model (CPU inference)
    classifier = PromptGuardClassifier()
    if classifier.load():
        logger.info("PromptGuard 2 model ready")
    else:
        logger.warning("PromptGuard 2 not available — ML injection detection disabled")
    app.state.classifier = classifier

    yield

    # Shutdown
    if app.state.cache is not None:
        await app.state.cache.close()
        app.state.cache = None


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
async def health(request: Request) -> HealthResponse:
    """Return service health status."""
    # Re-check Valkey on each health call for accurate status
    valkey_connected = await _check_valkey()
    request.app.state.valkey_connected = valkey_connected
    return HealthResponse(
        status="healthy",
        promptguard_loaded=request.app.state.classifier.loaded,
        cache_connected=valkey_connected,
    )


@app.post("/retrieve", response_model=RetrievedContent)
async def retrieve(request: Request, body: RetrieveRequest) -> RetrievedContent:
    """Retrieve and sanitize web content through the full pipeline."""
    return await run_retrieve_pipeline(
        body,
        cache=request.app.state.cache,
        classifier=request.app.state.classifier,
        config=request.app.state.config,
    )


@app.post("/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    """Run a web search through SearXNG with snippet sanitization."""
    return await run_search_pipeline(
        body,
        searxng_url=SEARXNG_URL,
        config=request.app.state.config,
    )
