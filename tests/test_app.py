"""Tests for Retrieval sidecar FastAPI server (US-001)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.types import Message, Receive, Scope, Send

from models import (
    RetrievedContent,
    SearchResponse,
    Stage2Verdict,
    Stage3Verdict,
    TrustTier,
)
from pipeline import contract
from pipeline.contract import CONTRACT_VERSION, DIAG_STRUCTURAL_BLOCKED
from pipeline.extraction_limits import (
    MAX_PROMPTGUARD_CHUNKS,
    ExtractionConfigurationError,
    extraction_settings_from_config,
)
from pipeline.orchestrator import PipelineError
from promptguard.classifier import (
    CHUNK_OVERLAP,
    MAX_SEQ_LEN,
    PromptGuardClassifier,
)
from retrieval_app import (
    _MAX_DOCUMENT_BYTES,
    DocumentSizeLimitMiddleware,
    ExtractionAdmissionController,
    ExtractionMetrics,
    RetrieveMetrics,
    SearchMetrics,
    _spool_upload,
    app,
)
from tests.fakes import FakeContentCache


@pytest.fixture
def client() -> httpx.AsyncClient:
    """Create an async test client for the retrieval app."""
    # Ensure app.state has the expected attributes (normally set by lifespan)
    app.state.classifier = PromptGuardClassifier()
    app.state.cache = FakeContentCache()
    app.state.config = {"extract_route_enabled": True}
    settings = extraction_settings_from_config(app.state.config)
    app.state.extraction_settings = settings
    app.state.extraction_metrics = ExtractionMetrics()
    app.state.extraction_admission = ExtractionAdmissionController(
        settings,
        app.state.extraction_metrics,
    )
    app.state.search_metrics = SearchMetrics()
    app.state.retrieve_metrics = RetrieveMetrics()
    app.state.classification_semaphore = asyncio.Semaphore(
        settings.classification_concurrency
    )

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
    """GET /health returns 200; the fixture's classifier is unloaded, so degraded."""
    app.state.cache.connected = True
    resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert isinstance(data["promptguard_loaded"], bool)
    assert isinstance(data["cache_connected"], bool)
    assert "search_sanitization" not in data["capabilities"]


async def test_health_promptguard_defaults_false(
    client: httpx.AsyncClient,
) -> None:
    """PromptGuard is not loaded yet (US-006), so it should be False."""
    app.state.cache.connected = True
    resp = await client.get("/health")

    assert resp.json()["promptguard_loaded"] is False


async def test_health_cache_connected_true(
    client: httpx.AsyncClient,
) -> None:
    """When Valkey is reachable, cache_connected should be True."""
    app.state.cache.connected = True
    resp = await client.get("/health")

    assert resp.json()["cache_connected"] is True


async def test_health_cache_disconnected(
    client: httpx.AsyncClient,
) -> None:
    """When Valkey is unreachable, cache_connected should be False and degraded."""
    app.state.cache.connected = False
    resp = await client.get("/health")

    assert resp.json()["cache_connected"] is False
    assert resp.json()["status"] == "degraded"
    assert "cache_unavailable" in resp.json()["degraded_reasons"]


async def test_health_missing_cache_reports_unavailable_never_raises(
    client: httpx.AsyncClient,
) -> None:
    """A ``None`` (or unset) ``app.state.cache`` degrades honestly instead of 500ing."""
    app.state.cache = None
    try:
        resp = await client.get("/health")
    finally:
        app.state.cache = FakeContentCache()

    assert resp.status_code == 200
    data = resp.json()
    assert data["cache_connected"] is False
    assert "cache_unavailable" in data["degraded_reasons"]


async def test_health_healthy_when_classifier_loaded_and_cache_connected(
    client: httpx.AsyncClient,
) -> None:
    """Loaded classifier + connected cache reports healthy with real capabilities."""
    mock_classifier = MagicMock(spec=PromptGuardClassifier)
    mock_classifier.loaded = True
    app.state.classifier = mock_classifier
    app.state.cache.connected = True
    try:
        resp = await client.get("/health")

        data = resp.json()
        assert data["status"] == "healthy"
        assert data["degraded_reasons"] == []
        assert data["capabilities"]["search_sanitization"] == 1
        assert data["contract_version"] == CONTRACT_VERSION
    finally:
        app.state.classifier = PromptGuardClassifier()


async def test_health_degraded_reports_promptguard_unavailable(
    client: httpx.AsyncClient,
) -> None:
    """Unloaded classifier reports the promptguard_unavailable degraded reason."""
    app.state.cache.connected = True
    resp = await client.get("/health")

    data = resp.json()
    assert data["status"] == "degraded"
    assert "promptguard_unavailable" in data["degraded_reasons"]
    assert data["contract_version"] == CONTRACT_VERSION


async def test_health_legacy_capability_override_restores_advertisement(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The break-glass env var keeps capabilities advertised while staying honest."""
    monkeypatch.setenv("POPPY_RETRIEVAL_LEGACY_CAPABILITY", "1")
    app.state.cache.connected = True
    resp = await client.get("/health")

    data = resp.json()
    assert data["capabilities"]["search_sanitization"] == 1
    assert data["status"] == "degraded"
    assert "promptguard_unavailable" in data["degraded_reasons"]
    assert data["promptguard_loaded"] is False


async def test_health_legacy_capability_override_unset_withholds_advertisement(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the override, an unloaded classifier withholds the capability."""
    monkeypatch.delenv("POPPY_RETRIEVAL_LEGACY_CAPABILITY", raising=False)
    app.state.cache.connected = True
    resp = await client.get("/health")

    assert "search_sanitization" not in resp.json()["capabilities"]


def test_legacy_capability_warning_logged_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The per-boot warning fires only while the override env var is active."""
    import logging

    from retrieval_app import _warn_if_legacy_capability_advertisement_enabled

    monkeypatch.delenv("POPPY_RETRIEVAL_LEGACY_CAPABILITY", raising=False)
    with caplog.at_level(logging.WARNING, logger="retrieval_app"):
        assert _warn_if_legacy_capability_advertisement_enabled() is False
    assert "legacy_capability_advertisement_active" not in caplog.text

    caplog.clear()
    monkeypatch.setenv("POPPY_RETRIEVAL_LEGACY_CAPABILITY", "1")
    with caplog.at_level(logging.WARNING, logger="retrieval_app"):
        assert _warn_if_legacy_capability_advertisement_enabled() is True
    assert "legacy_capability_advertisement_active" in caplog.text


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


async def test_metrics_covers_search_retrieve_and_cache_sections(
    client: httpx.AsyncClient,
) -> None:
    """`/metrics` exposes fresh ``search``, ``retrieve``, and ``cache`` sections."""
    response = await client.get("/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == CONTRACT_VERSION
    assert body["search"] == {
        "requests": 0,
        "errors": {},
        "omitted_by_reason": {},
        "unscanned_results": 0,
    }
    assert body["retrieve"] == {
        "requests": 0,
        "errors": {},
        "cache_hits": 0,
        "cache_misses": 0,
        "blocked_by_reason": {},
        "promptguard_state": {},
    }
    assert set(body["cache"]) == {
        "reconnect_attempts",
        "reconnect_successes",
        "reconnect_failures",
        "operation_failures",
    }


async def test_metrics_retrieve_records_cache_hit_and_promptguard_state(
    client: httpx.AsyncClient,
) -> None:
    """`/retrieve` folds ``cache_hit``/``promptguard_state`` from the response model."""
    content = RetrievedContent(
        request_id="r1",
        source_url="https://example.com/a",
        final_url="https://example.com/a",
        cache_hit=True,
        title="T",
        body="body",
        word_count=1,
        content_type="html",
        trust_score=0.7,
        trust_tier=TrustTier.STANDARD,
        stage2_verdict=Stage2Verdict.CLEAN,
        stage3_verdict=Stage3Verdict.SAFE,
        promptguard_state="scanned",
        domain="example.com",
    )
    with patch(
        "retrieval_app.run_retrieve_pipeline", new=AsyncMock(return_value=content)
    ):
        resp = await client.post("/retrieve", json={"url": "https://example.com/a"})
    assert resp.status_code == 200

    metrics_resp = await client.get("/metrics")
    retrieve_metrics = metrics_resp.json()["retrieve"]
    assert retrieve_metrics["requests"] == 1
    assert retrieve_metrics["cache_hits"] == 1
    assert retrieve_metrics["cache_misses"] == 0
    assert retrieve_metrics["promptguard_state"] == {"scanned": 1}
    assert retrieve_metrics["blocked_by_reason"] == {}


async def test_metrics_retrieve_records_blocked_by_reason_from_diagnostic(
    client: httpx.AsyncClient,
) -> None:
    """`/retrieve` keys ``blocked_by_reason`` on the quarantine diagnostic label."""
    content = RetrievedContent(
        request_id="r2",
        source_url="https://example.com/b",
        final_url="https://example.com/b",
        body="Content quarantined due to potential prompt injection.",
        word_count=6,
        content_type="html",
        trust_score=0.0,
        trust_tier=TrustTier.STANDARD,
        injection_detected=True,
        injection_spans=[DIAG_STRUCTURAL_BLOCKED],
        stage2_verdict=Stage2Verdict.BLOCKED,
        stage3_verdict=Stage3Verdict.SAFE,
        promptguard_state="structural_blocked",
        domain="example.com",
    )
    with patch(
        "retrieval_app.run_retrieve_pipeline", new=AsyncMock(return_value=content)
    ):
        resp = await client.post("/retrieve", json={"url": "https://example.com/b"})
    assert resp.status_code == 200

    metrics_resp = await client.get("/metrics")
    retrieve_metrics = metrics_resp.json()["retrieve"]
    assert retrieve_metrics["blocked_by_reason"] == {DIAG_STRUCTURAL_BLOCKED: 1}
    assert retrieve_metrics["promptguard_state"] == {"structural_blocked": 1}


async def test_metrics_retrieve_blocked_by_reason_unknown_diagnostic_buckets_to_other(
    client: httpx.AsyncClient,
) -> None:
    """An out-of-vocabulary diagnostic buckets to ``contract.METRICS_OTHER_BUCKET``."""
    content = RetrievedContent(
        request_id="r3",
        source_url="https://example.com/c",
        final_url="https://example.com/c",
        body="Content quarantined due to potential prompt injection.",
        word_count=6,
        content_type="html",
        trust_score=0.0,
        trust_tier=TrustTier.STANDARD,
        injection_detected=True,
        injection_spans=["unexpected_diagnostic"],
        stage2_verdict=Stage2Verdict.BLOCKED,
        stage3_verdict=Stage3Verdict.SAFE,
        promptguard_state="structural_blocked",
        domain="example.com",
    )
    with patch(
        "retrieval_app.run_retrieve_pipeline", new=AsyncMock(return_value=content)
    ):
        resp = await client.post("/retrieve", json={"url": "https://example.com/c"})
    assert resp.status_code == 200

    metrics_resp = await client.get("/metrics")
    retrieve_metrics = metrics_resp.json()["retrieve"]
    assert retrieve_metrics["blocked_by_reason"] == {contract.METRICS_OTHER_BUCKET: 1}


async def test_metrics_retrieve_error_keys_on_error_code_not_reason(
    client: httpx.AsyncClient,
) -> None:
    """`/retrieve` errors key on the closed ``error`` code, never on ``reason``."""
    exc = PipelineError(
        error="private_ip",
        reason=(
            "URL resolves to a private/internal address: 10.0.0.5 for "
            "https://internal.example/secret"
        ),
        request_id="r4",
    )
    with patch("retrieval_app.run_retrieve_pipeline", new=AsyncMock(side_effect=exc)):
        resp = await client.post(
            "/retrieve", json={"url": "https://internal.example/secret"}
        )
    assert resp.status_code == 422
    assert "internal.example" in resp.json()["reason"]

    metrics_resp = await client.get("/metrics")
    body = metrics_resp.json()
    assert body["retrieve"]["errors"] == {"private_ip": 1}
    assert "internal.example" not in json.dumps(body)


async def test_metrics_search_records_omitted_and_unscanned_from_response(
    client: httpx.AsyncClient,
) -> None:
    """`/search` folds ``omitted_by_reason``/``unscanned_results`` from the response."""
    response = SearchResponse(
        results=[],
        request_id="s1",
        query="test",
        omitted_results=1,
        omitted_by_reason={contract.OMIT_INVALID_URL: 1},
        unscanned_results=2,
    )
    with patch(
        "retrieval_app.run_search_pipeline", new=AsyncMock(return_value=response)
    ):
        resp = await client.post("/search", json={"query": "test"})
    assert resp.status_code == 200

    metrics_resp = await client.get("/metrics")
    search_metrics = metrics_resp.json()["search"]
    assert search_metrics["requests"] == 1
    assert search_metrics["omitted_by_reason"] == {contract.OMIT_INVALID_URL: 1}
    assert search_metrics["unscanned_results"] == 2


async def test_metrics_search_omitted_by_reason_unknown_key_buckets_to_other(
    client: httpx.AsyncClient,
) -> None:
    """An out-of-vocabulary omission reason buckets to ``METRICS_OTHER_BUCKET``."""
    response = SearchResponse(
        results=[],
        request_id="s2",
        query="test",
        omitted_by_reason={"unexpected_reason": 1},
    )
    with patch(
        "retrieval_app.run_search_pipeline", new=AsyncMock(return_value=response)
    ):
        resp = await client.post("/search", json={"query": "test"})
    assert resp.status_code == 200

    metrics_resp = await client.get("/metrics")
    search_metrics = metrics_resp.json()["search"]
    assert search_metrics["omitted_by_reason"] == {contract.METRICS_OTHER_BUCKET: 1}


async def test_metrics_search_error_keys_are_content_free(
    client: httpx.AsyncClient,
) -> None:
    """`/search` never leaks the SearXNG URL from ``reason`` into the metrics key."""
    exc = PipelineError(
        error="searxng_unavailable",
        reason="SearXNG not reachable at http://poppy-searxng:8080: Connection refused",
        request_id="s3",
    )
    with patch("retrieval_app.run_search_pipeline", new=AsyncMock(side_effect=exc)):
        resp = await client.post("/search", json={"query": "test"})
    assert resp.status_code == 422
    assert "poppy-searxng" in resp.json()["reason"]

    metrics_resp = await client.get("/metrics")
    body = metrics_resp.json()
    assert body["search"]["errors"] == {"searxng_unavailable": 1}
    assert body["search"]["requests"] == 1
    assert "poppy-searxng" not in json.dumps(body)
