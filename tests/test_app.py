"""Tests for Retrieval sidecar FastAPI server (US-001)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.types import Message, Receive, Scope, Send

# Add the retrieval service root to sys.path so app is importable
_retrieval_root = str(Path(__file__).resolve().parents[2] / "services" / "retrieval")
if _retrieval_root not in sys.path:
    sys.path.insert(0, _retrieval_root)

from pipeline.orchestrator import PipelineError  # noqa: E402
from promptguard.classifier import PromptGuardClassifier  # noqa: E402
from retrieval_app import (  # noqa: E402
    _MAX_DOCUMENT_BYTES,
    DocumentSizeLimitMiddleware,
    _read_upload_bytes,
    app,
)


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
    assert data["capabilities"]["search_sanitization"] == 1


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


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"content-length", b"1")],
        [(b"transfer-encoding", b"chunked")],
        [(b"content-length", b"100")],
    ],
    ids=["missing", "understated", "chunked", "oversized"],
)
async def test_extract_asgi_size_limit_ignores_content_length(
    headers: list[tuple[bytes, bytes]],
) -> None:
    """The ASGI limit counts streamed bytes for every Content-Length variant."""
    delivered: list[bytes] = []
    sent: list[Message] = []
    messages = iter(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"67890", "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ]
    )

    async def receive() -> Message:
        return next(messages)

    async def send(message: Message) -> None:
        sent.append(message)

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        del scope, send
        while True:
            message = await receive()
            delivered.append(message.get("body", b""))
            if not message.get("more_body", False):
                return

    middleware = DocumentSizeLimitMiddleware(downstream, max_bytes=10)
    scope: Scope = {
        "type": "http",
        "path": "/extract",
        "headers": headers,
    }

    await middleware(scope, receive, send)

    assert delivered == [b"12345", b"67890"]
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["error"] == "content_too_large"
    assert _MAX_DOCUMENT_BYTES == 50 * 1024 * 1024


async def test_bounded_upload_read_rejects_file_over_limit() -> None:
    """The file read repeats the cap after multipart parsing in bounded chunks."""
    chunk_sizes: list[int] = []
    chunks = iter([b"abcd", b"efgh", b"ijk"])

    class ChunkedUpload:
        """Minimal upload double that records each requested read size."""

        async def read(self, size: int = -1) -> bytes:
            chunk_sizes.append(size)
            return next(chunks, b"")

    with pytest.raises(PipelineError) as exc_info:
        await _read_upload_bytes(
            cast(Any, ChunkedUpload()),
            max_bytes=10,
            chunk_size=4,
        )

    assert chunk_sizes == [4, 4, 4]
    assert exc_info.value.error == "content_too_large"
