"""Poppy Retrieval sidecar -- FastAPI service for web content retrieval.

Isolated container that fetches, sanitizes, and caches web content.
No direct database access. Communicates with core via internal API only.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://poppy-valkey:6379/4")

# -- Module-level state --

_valkey_connected: bool = False


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
    global _valkey_connected
    _valkey_connected = await _check_valkey()
    if _valkey_connected:
        logger.info("Valkey connection established")
    else:
        logger.warning("Valkey not available at startup")
    yield


# -- App --

app = FastAPI(
    title="Poppy Retrieval Sidecar",
    version="0.1.0",
    lifespan=lifespan,
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
        promptguard_loaded=False,  # US-006 will add PromptGuard model loading
        cache_connected=_valkey_connected,
    )
