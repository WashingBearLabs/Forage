"""Tests for Retrieval sidecar FastAPI server (US-001)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Add the retrieval service root to sys.path so app is importable
_retrieval_root = str(Path(__file__).resolve().parents[2] / "services" / "retrieval")
if _retrieval_root not in sys.path:
    sys.path.insert(0, _retrieval_root)

from promptguard.classifier import PromptGuardClassifier  # noqa: E402
from retrieval_app import app  # noqa: E402


@pytest.fixture
def client() -> httpx.AsyncClient:
    """Create an async test client for the retrieval app."""
    # Ensure app.state has the expected attributes (normally set by lifespan)
    app.state.classifier = PromptGuardClassifier()
    app.state.cache = None
    app.state.config = {}
    app.state.valkey_connected = False

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_health_returns_200(client: httpx.AsyncClient) -> None:
    """GET /health returns 200 with expected JSON structure."""
    with patch(
        "retrieval_app._check_valkey", new_callable=AsyncMock, return_value=True
    ):
        resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert isinstance(data["promptguard_loaded"], bool)
    assert isinstance(data["cache_connected"], bool)


async def test_health_promptguard_defaults_false(
    client: httpx.AsyncClient,
) -> None:
    """PromptGuard is not loaded yet (US-006), so it should be False."""
    with patch(
        "retrieval_app._check_valkey", new_callable=AsyncMock, return_value=True
    ):
        resp = await client.get("/health")

    assert resp.json()["promptguard_loaded"] is False


async def test_health_cache_connected_true(
    client: httpx.AsyncClient,
) -> None:
    """When Valkey is reachable, cache_connected should be True."""
    with patch(
        "retrieval_app._check_valkey", new_callable=AsyncMock, return_value=True
    ):
        resp = await client.get("/health")

    assert resp.json()["cache_connected"] is True


async def test_health_cache_disconnected(
    client: httpx.AsyncClient,
) -> None:
    """When Valkey is unreachable, cache_connected should be False."""
    with patch(
        "retrieval_app._check_valkey", new_callable=AsyncMock, return_value=False
    ):
        resp = await client.get("/health")

    assert resp.json()["cache_connected"] is False
    assert resp.json()["status"] == "healthy"
