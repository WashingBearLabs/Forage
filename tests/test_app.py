"""Tests for Retrieval sidecar FastAPI server (US-001)."""

from __future__ import annotations

import asyncio
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

from pipeline.extraction_limits import (  # noqa: E402
    MAX_PROMPTGUARD_CHUNKS,
    ExtractionConfigurationError,
    extraction_settings_from_config,
)
from pipeline.orchestrator import PipelineError  # noqa: E402
from promptguard.classifier import (  # noqa: E402
    CHUNK_OVERLAP,
    MAX_SEQ_LEN,
    PromptGuardClassifier,
)
from retrieval_app import (  # noqa: E402
    _MAX_DOCUMENT_BYTES,
    DocumentSizeLimitMiddleware,
    ExtractionAdmissionController,
    ExtractionMetrics,
    _spool_upload,
    app,
)


@pytest.fixture
def client() -> httpx.AsyncClient:
    """Create an async test client for the retrieval app."""
    # Ensure app.state has the expected attributes (normally set by lifespan)
    app.state.classifier = PromptGuardClassifier()
    app.state.cache = None
    app.state.config = {"extract_route_enabled": True}
    settings = extraction_settings_from_config(app.state.config)
    app.state.extraction_settings = settings
    app.state.extraction_metrics = ExtractionMetrics()
    app.state.extraction_admission = ExtractionAdmissionController(
        settings,
        app.state.extraction_metrics,
    )
    app.state.classification_semaphore = asyncio.Semaphore(
        settings.classification_concurrency
    )
    app.state.valkey_connected = False

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def test_extraction_limit_defaults_are_bounded_and_derived() -> None:
    """The classifiable ceiling has one source: PromptGuard's chunk budget."""
    settings = extraction_settings_from_config({})

    assert settings.route_enabled is False
    assert settings.max_input_bytes == 50 * 1024 * 1024
    assert settings.max_pages == 500
    assert settings.child_cpu_seconds == 20
    assert settings.wall_clock_seconds == 90
    assert settings.extraction_concurrency == 1
    assert settings.max_extracted_characters == (
        (MAX_SEQ_LEN - CHUNK_OVERLAP) * MAX_PROMPTGUARD_CHUNKS * 4
    )
    with pytest.raises(ExtractionConfigurationError):
        extraction_settings_from_config({"extraction": {"max_pages": 501}})


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
        await _spool_upload(
            cast(Any, ChunkedUpload()),
            max_bytes=10,
            chunk_size=4,
        )

    assert chunk_sizes == [4, 4, 4]
    assert exc_info.value.error == "content_too_large"


async def test_extract_release_gate_returns_404_when_disabled(
    client: httpx.AsyncClient,
) -> None:
    """The default-off release gate hides the route before multipart parsing."""
    settings = extraction_settings_from_config({})
    app.state.extraction_settings = settings
    app.state.extraction_metrics = ExtractionMetrics()
    app.state.extraction_admission = ExtractionAdmissionController(
        settings,
        app.state.extraction_metrics,
    )

    response = await client.post(
        "/extract",
        files={"file": ("document.txt", b"safe", "text/plain")},
        data={"filename": "document.txt"},
    )

    assert response.status_code == 404


async def test_extract_benign_text_over_classification_budget_is_not_injection(
    client: httpx.AsyncClient,
) -> None:
    """A benign over-budget document reports the honest classification outcome."""
    settings = app.state.extraction_settings
    response = await client.post(
        "/extract",
        files={
            "file": (
                "large.txt",
                b"a" * (settings.max_extracted_characters + 1),
                "text/plain",
            )
        },
        data={"filename": "large.txt"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "content_too_large_to_classify"
    assert payload["error"] != "injection_detected"


async def test_extraction_admission_queue_rejects_when_full() -> None:
    """A reserved active slot and bounded queue yield an immediate busy outcome."""
    settings = extraction_settings_from_config(
        {
            "extract_route_enabled": True,
            "extraction": {
                "admission_queue_depth": 1,
                "max_queued_upload_bytes": _MAX_DOCUMENT_BYTES,
            },
        }
    )
    metrics = ExtractionMetrics()
    controller = ExtractionAdmissionController(settings, metrics)

    assert await controller.acquire() is True
    queued = asyncio.create_task(controller.acquire())
    await asyncio.sleep(0)
    assert controller.queued == 1
    assert controller.queued_bytes == _MAX_DOCUMENT_BYTES
    assert await controller.acquire() is False
    assert metrics.busy_rejections == 1

    await controller.release()
    assert await queued is True
    await controller.release()


async def test_metrics_expose_saturation_and_oom_proximity(
    client: httpx.AsyncClient,
) -> None:
    """The internal counters include cgroup-backed OOM-proximity fields."""
    response = await client.get("/metrics")

    assert response.status_code == 200
    extraction = response.json()["extraction"]
    assert "semaphore_saturation" in extraction
    assert "oom_proximity_ratio" in extraction
