"""Tests for Retrieval sidecar FastAPI server (US-001)."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message, Receive, Scope, Send

import model_fetcher
import retrieval_app
from cache import (
    DEFAULT_CACHE_MAX_BYTES,
    DEFAULT_CACHE_MAX_ENTRIES,
    CacheConfigurationError,
    CacheMetrics,
    CacheSettings,
    ContentCache,
    InMemoryStorage,
    ValkeyStorage,
)
from model_fetcher import DEFAULT_MODEL_REVISION, ModelMetrics
from models import (
    RetrievedContent,
    SearchResponse,
    Stage2Verdict,
    Stage3Verdict,
    TrustTier,
)
from pipeline import contract
from pipeline.contract import (
    CONTRACT_VERSION,
    DEGRADED_CACHE_UNAVAILABLE,
    DEGRADED_PROMPTGUARD_UNAVAILABLE,
    DIAG_STRUCTURAL_BLOCKED,
)
from pipeline.extraction_limits import (
    MAX_PROMPTGUARD_CHUNKS,
    ExtractionConfigurationError,
    extraction_settings_from_config,
)
from pipeline.orchestrator import PipelineError
from promptguard.classifier import (
    CHUNK_OVERLAP,
    MAX_SEQ_LEN,
    MODEL_ID,
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
    lifespan,
)
from tests.fakes import (
    FakeContentCache,
    hub_download_double,
    weights_manifest_document,
)


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
    app.state.model_metrics = ModelMetrics()
    app.state.classification_semaphore = asyncio.Semaphore(
        settings.classification_concurrency
    )

    transport = httpx.ASGITransport(app=app)
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


# Both break-glass names: the current one and the pre-extraction alias. Every
# break-glass test below is parametrized over this pair so the alias can
# never drift away from the name it aliases. (The interim name
# FORAGE_LEGACY_CAPABILITY was retired un-aliased at the 2026-09-08 rename —
# it never shipped in any deployment.)
_BREAK_GLASS_ENV_VARS = (
    "FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION",
    "POPPY_RETRIEVAL_LEGACY_CAPABILITY",
)

# Values that must NOT arm the override: the semantics are an exact ``== "1"``
# match, never a truthiness test.
_NON_ARMING_VALUES = ("", "0", "true", "TRUE", "yes", "on", " 1", "1 ", "11")


def _clear_break_glass_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset every break-glass name so a stray ambient value cannot arm it."""
    for name in _BREAK_GLASS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("env_var", _BREAK_GLASS_ENV_VARS)
async def test_health_break_glass_override_restores_advertisement(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
) -> None:
    """Either break-glass name keeps capabilities advertised while staying honest."""
    _clear_break_glass_env(monkeypatch)
    monkeypatch.setenv(env_var, "1")
    app.state.cache.connected = True
    resp = await client.get("/health")

    data = resp.json()
    assert data["capabilities"]["search_sanitization"] == 1
    assert data["status"] == "degraded"
    assert "promptguard_unavailable" in data["degraded_reasons"]
    assert data["promptguard_loaded"] is False


@pytest.mark.parametrize("env_var", _BREAK_GLASS_ENV_VARS)
async def test_health_break_glass_override_unset_withholds_advertisement(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
) -> None:
    """Only an exact ``1`` arms the override — on either name."""
    _clear_break_glass_env(monkeypatch)
    app.state.cache.connected = True

    resp = await client.get("/health")
    assert "search_sanitization" not in resp.json()["capabilities"]

    for value in _NON_ARMING_VALUES:
        monkeypatch.setenv(env_var, value)
        resp = await client.get("/health")
        assert "search_sanitization" not in resp.json()["capabilities"], value


@pytest.mark.parametrize("env_var", _BREAK_GLASS_ENV_VARS)
def test_break_glass_warning_logged_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    env_var: str,
) -> None:
    """The per-boot warning fires only when armed, and names the arming var."""
    import logging

    from retrieval_app import _warn_if_break_glass_advertisement_enabled

    _clear_break_glass_env(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="retrieval_app"):
        assert _warn_if_break_glass_advertisement_enabled() is False
    assert "break_glass_advertisement_active" not in caplog.text

    caplog.clear()
    monkeypatch.setenv(env_var, "1")
    with caplog.at_level(logging.WARNING, logger="retrieval_app"):
        assert _warn_if_break_glass_advertisement_enabled() is True
    assert "break_glass_advertisement_active" in caplog.text

    # The warning must name whichever variable actually armed it — an operator
    # who has to unset it needs the real name, not a hardcoded constant.
    assert env_var in caplog.text
    for other in _BREAK_GLASS_ENV_VARS:
        if other != env_var:
            assert other not in caplog.text


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
        "storage_hits",
        "storage_misses",
        "storage_evictions",
        "storage_oversize_skips",
    }


async def test_metrics_exposes_the_model_acquisition_counters(
    client: httpx.AsyncClient,
) -> None:
    """`/metrics` carries the weight-acquisition section, named counters and all."""
    response = await client.get("/metrics")

    assert response.json()["model"] == {
        "fetch_failures": 0,
        "verify_failures": 0,
        "quarantines": 0,
        "fetch_in_progress": False,
        "retries_scheduled": 0,
    }


async def test_metrics_model_counters_reflect_the_live_metrics_object(
    client: httpx.AsyncClient,
) -> None:
    """A refused weight set is visible to an operator, not only in the log."""
    model_metrics: ModelMetrics = app.state.model_metrics
    model_metrics.record_fetch_failure()
    model_metrics.record_verify_failure()
    model_metrics.record_quarantine()
    model_metrics.record_retry_scheduled()
    model_metrics.fetch_in_progress = True

    response = await client.get("/metrics")

    assert response.json()["model"] == {
        "fetch_failures": 1,
        "verify_failures": 1,
        "quarantines": 1,
        "fetch_in_progress": True,
        "retries_scheduled": 1,
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
    # The full URL, not the bare host: the metrics body legitimately contains
    # the "searxng_unavailable" error key, so the leak check needs a token that
    # only the URL can produce.
    searxng_url = "http://searxng:8080"
    exc = PipelineError(
        error="searxng_unavailable",
        reason=f"SearXNG not reachable at {searxng_url}: Connection refused",
        request_id="s3",
    )
    with patch("retrieval_app.run_search_pipeline", new=AsyncMock(side_effect=exc)):
        resp = await client.post("/search", json={"query": "test"})
    assert resp.status_code == 422
    assert searxng_url in resp.json()["reason"]

    metrics_resp = await client.get("/metrics")
    body = metrics_resp.json()
    assert body["search"]["errors"] == {"searxng_unavailable": 1}
    assert body["search"]["requests"] == 1
    assert searxng_url not in json.dumps(body)


# ---------------------------------------------------------------------------
# Lifespan: startup yields immediately, the fetch runs behind it
# ---------------------------------------------------------------------------
#
# `feature-forage-model-bootstrap` US-001. These tests need a harness of their
# own: the `client` fixture above drives the app through `httpx.ASGITransport`,
# which never fires lifespan events at all, so every assertion about startup
# would pass vacuously against it. The helper below runs the *real* `lifespan`
# context manager — the same code uvicorn runs — with only the Valkey client
# replaced, and is the only place in the suite where `app.state` is populated
# by the service rather than by a fixture.


class _LifespanCache(FakeContentCache):
    """`FakeContentCache` plus the two methods only the lifespan calls."""

    async def connect(self) -> bool:
        """Report the settable ``connected`` state, mirroring ``ContentCache``."""
        return self.connected


@asynccontextmanager
async def _running_app(
    *,
    cache_connected: bool = True,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Start the app through its real lifespan and hand back a client."""
    with patch("retrieval_app.ContentCache") as cache_factory:
        cache_factory.return_value = _LifespanCache(connected=cache_connected)
        async with lifespan(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as lifespan_client:
                yield lifespan_client


_TINY_MODEL_DIR = Path(__file__).resolve().parent / "fixtures" / "tiny_model"


async def _settled(task: asyncio.Task[bool], *, timeout: float = 15.0) -> bool:
    """Wait for the acquisition task to finish, generously.

    The budget is deliberately far larger than the work: a shared CI runner
    can stall a thread hand-off for a second or more, and a tight bound here
    would buy nothing but a flaky suite. The assertion that matters is what
    the task *did*, not how fast it did it.

    Only usable when the acquisition **succeeds**: since US-005 the task is a
    retry loop with no ending short of a loaded classifier, so awaiting it
    after a failure hangs until the timeout. :func:`_first_attempt_failed` is
    the observable for the failing case.
    """
    return await asyncio.wait_for(task, timeout=timeout)


def _park_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Push the first retry an hour out, so exactly one attempt runs.

    Every lifespan test that lets an acquisition *fail* needs this. The task
    retries until it loads, so without it a test asserting "one ERROR" would
    race the second attempt, and one asserting a counter would read whichever
    value the scheduler happened to leave. An hour is not a wait — the lifespan
    cancels the sleeping task on the way out — it is a guarantee that nothing
    else runs while the assertions do.
    """
    monkeypatch.setattr(model_fetcher, "RETRY_INITIAL_BACKOFF_S", 3600.0)


async def _first_attempt_failed(*, timeout: float = 15.0) -> None:
    """Wait until the acquisition has completed one attempt without loading.

    ``retries_scheduled`` moves when the loop arms the next attempt, which is
    the first observable moment after an attempt has finished and failed — and
    unlike awaiting the task, it exists in a world where the task never ends.
    """
    model_metrics: ModelMetrics = app.state.model_metrics
    deadline = time.monotonic() + timeout
    while model_metrics.retries_scheduled < 1:
        assert time.monotonic() < deadline, "the acquisition never finished an attempt"
        await asyncio.sleep(0.01)


@asynccontextmanager
async def _fetchable_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[MagicMock, None]:
    """A cache root, a manifest and a token that let exactly one fetch succeed.

    Everything here is the production seam: ``HF_HOME`` places the cache,
    ``HF_TOKEN`` authorises the fetch, and the manifest is the committed pin
    (swapped for one describing the tiny-model fixture, since the real one is
    US-003's). Only ``snapshot_download`` is a double.
    """
    files = {
        path.name: path.read_bytes()
        for path in sorted(_TINY_MODEL_DIR.iterdir())
        if path.is_file()
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cache_root = root / "model-cache"
        cache_root.mkdir()
        manifest = root / "weights_manifest.json"
        manifest.write_text(
            json.dumps(
                weights_manifest_document(
                    files,
                    model_id=MODEL_ID,
                    revision=DEFAULT_MODEL_REVISION,
                )
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("HF_HOME", str(cache_root))
        monkeypatch.setenv("HF_TOKEN", "hf_" + "x" * 34)
        monkeypatch.delenv("FORAGE_MODEL_REVISION", raising=False)
        monkeypatch.setattr(model_fetcher, "MANIFEST_PATH", manifest)
        with patch("huggingface_hub.snapshot_download") as download:
            download.side_effect = hub_download_double(files)
            yield download


def _blocking_acquisition(
    release: threading.Event,
    *,
    loads: bool = True,
) -> Callable[..., bool]:
    """An acquisition that parks in its worker thread until *release* is set.

    Stands in for the minutes a real ~270 MiB fetch takes, without the
    minutes. It runs in the thread `asyncio.to_thread` gives it, so a blocking
    wait here is exactly the pressure a real download applies to the event
    loop — which is to say, none, if the wiring is right.
    """

    def _acquire(
        classifier: PromptGuardClassifier,
        *,
        metrics: ModelMetrics | None = None,
        **_kwargs: object,
    ) -> bool:
        if metrics is not None:
            metrics.fetch_in_progress = True
        try:
            release.wait(timeout=10)
        finally:
            if metrics is not None:
                metrics.fetch_in_progress = False
        if loads:
            classifier._loaded = True
        return loads

    return _acquire


async def test_lifespan_startup_yields_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup must not wait on the fetch — uvicorn serves nothing until it returns.

    The compose healthcheck is 10 s x 5 retries with no `start_period`, so a
    startup that blocked for a ~270 MiB download would be restart-looped
    before it ever finished. The acquisition parks for up to 10 s here; if
    startup were awaiting it, this test would take that long instead of
    milliseconds.
    """
    release = threading.Event()
    monkeypatch.setattr(
        model_fetcher, "acquire_and_load", _blocking_acquisition(release)
    )
    try:
        started_at = time.monotonic()
        async with _running_app():
            elapsed = time.monotonic() - started_at

            assert elapsed < 2.0
            model_task = cast("asyncio.Task[bool]", app.state.model_task)
            assert model_task.done() is False
    finally:
        release.set()


async def test_health_answers_while_the_fetch_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/health` latency is unaffected by an acquisition running behind it.

    Measured with a *connected* cache, which is what the story's independent
    test asks for and what makes the number meaningful: the AC's "no latency
    beyond the pre-existing 2 s cache-reconnect floor" names a floor that only
    a *disconnected* cache can spend (`cache.py`'s `_RECONNECT_TIMEOUT_S`
    bounds one reconnect attempt), and that spend has nothing to do with the
    fetch. With the cache connected there is no floor to hide behind, so the
    assertion is the strict one: every response inside a fraction of a second,
    while the fetch is provably still running.
    """
    release = threading.Event()
    monkeypatch.setattr(
        model_fetcher, "acquire_and_load", _blocking_acquisition(release)
    )
    try:
        async with _running_app(cache_connected=True) as client:
            await asyncio.sleep(0.05)
            model_metrics: ModelMetrics = app.state.model_metrics
            assert model_metrics.fetch_in_progress is True

            latencies: list[float] = []
            for _ in range(5):
                started_at = time.monotonic()
                response = await client.get("/health")
                latencies.append(time.monotonic() - started_at)

                assert response.status_code == 200
                assert response.json()["promptguard_loaded"] is False

            assert max(latencies) < 1.0
            assert model_metrics.fetch_in_progress is True
    finally:
        release.set()


async def test_metrics_reports_fetch_in_progress_while_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counter that tells an operator "downloading" from "wedged"."""
    release = threading.Event()
    monkeypatch.setattr(
        model_fetcher, "acquire_and_load", _blocking_acquisition(release)
    )
    try:
        async with _running_app() as client:
            await asyncio.sleep(0.05)
            during = (await client.get("/metrics")).json()["model"]

            assert during["fetch_in_progress"] is True
            assert during["fetch_failures"] == 0
    finally:
        release.set()


async def test_promptguard_loaded_flips_without_a_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """False to true in place, through the real fetch-verify-load wiring.

    Only the transport is mocked: `snapshot_download` writes the committed
    tiny-model fixture into a real hub cache layout, and the module's own
    verifier and `PromptGuardClassifier.load()` do the rest. The `/health`
    body is read before and after, and the three fields a consumer gates on
    all move together.
    """
    async with (
        _fetchable_environment(monkeypatch) as download,
        _running_app() as client,
    ):
        before = (await client.get("/health")).json()

        assert before["promptguard_loaded"] is False
        assert DEGRADED_PROMPTGUARD_UNAVAILABLE in before["degraded_reasons"]
        assert "search_sanitization" not in before["capabilities"]

        await _settled(cast("asyncio.Task[bool]", app.state.model_task))
        after = (await client.get("/health")).json()

        assert after["promptguard_loaded"] is True
        assert DEGRADED_PROMPTGUARD_UNAVAILABLE not in after["degraded_reasons"]
        assert after["capabilities"]["search_sanitization"] == 1
        assert after["status"] == "healthy"
        assert download.call_count == 1


async def test_a_credential_less_boot_stays_degraded_and_says_so_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The stock image's honest state: no credentials, still up, audibly degraded.

    This is the container CI's `smoke` job runs and the one a third party gets
    from `docker run`. US-001 asserted the counters stayed at zero here and
    left a note that the combined "every source failed" ERROR was US-004's; it
    is now US-004's, so the counter moves and the line is logged — once,
    naming both sources. Nothing was *attempted* (both credentials are
    absent), and that is precisely why the acquisition itself counts as the
    failed attempt: a degraded container with every counter at zero is the
    silent path this spec exists to close.

    Since US-005 the attempt is the first turn of a retry loop, so the ERROR
    is "once per attempt" rather than "once ever" — `_park_the_retry` holds the
    second attempt off while the assertions run, and the loop is cancelled with
    the lifespan.
    """
    monkeypatch.setenv("HF_HOME", "/nonexistent-model-cache")
    _park_the_retry(monkeypatch)

    with (
        patch("huggingface_hub.snapshot_download") as download,
        patch("subprocess.run") as run,
        caplog.at_level("DEBUG", logger="model_fetcher"),
    ):
        async with _running_app() as client:
            await _first_attempt_failed()
            body = (await client.get("/health")).json()

            assert body["status"] == "degraded"
            assert body["promptguard_loaded"] is False
            assert DEGRADED_PROMPTGUARD_UNAVAILABLE in body["degraded_reasons"]
            assert cast(MagicMock, download).call_count == 0
            assert cast(MagicMock, run).call_count == 0
            assert (await client.get("/metrics")).json()["model"] == {
                "fetch_failures": 1,
                "verify_failures": 0,
                "quarantines": 0,
                "fetch_in_progress": False,
                "retries_scheduled": 1,
            }

    errors = [
        record.getMessage() for record in caplog.records if record.levelname == "ERROR"
    ]
    assert len(errors) == 1
    assert errors[0].startswith("weights_unavailable")
    assert "huggingface=skipped_no_token" in errors[0]
    assert "mirror=skipped_no_token" in errors[0]


async def test_the_running_acquisition_is_the_one_published_on_app_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`app.state.model_acquisition` must be the object holding the live lock.

    Single-flight only means anything if every caller shares one
    `WeightAcquisition`: a second caller that constructed its own would get its
    own `asyncio.Lock` and start a second ~270 MiB download beside the first.
    The published object is the *mechanism* by which they share it, so "it is
    published" is not enough — this asserts it is the one the running task is
    using, by reading `in_flight` while that task is provably parked inside an
    acquisition.

    Added after a mutation escape: replacing the published object with `None`
    broke nothing, because the reason it exists lives in the next story rather
    than in this diff.
    """
    release = threading.Event()
    monkeypatch.setattr(
        model_fetcher, "acquire_and_load", _blocking_acquisition(release)
    )
    try:
        async with _running_app():
            await asyncio.sleep(0.05)
            acquisition = cast(
                "model_fetcher.WeightAcquisition", app.state.model_acquisition
            )
            model_task = cast("asyncio.Task[bool]", app.state.model_task)

            assert acquisition.in_flight is True
            assert acquisition.metrics is app.state.model_metrics
            assert await acquisition.attempt_once() is False
            assert model_task.done() is False
    finally:
        release.set()


async def test_the_acquisition_task_does_not_outlive_the_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown cancels the handle it created — a leaked task hangs pytest.

    The retry loop has no ending short of a loaded classifier, so this is not
    tidiness: cancellation is the only thing that stops it, and a task nothing
    cancels outlives the lifespan — which under pytest is a hang rather than a
    warning.
    """
    release = threading.Event()
    monkeypatch.setattr(
        model_fetcher, "acquire_and_load", _blocking_acquisition(release)
    )
    try:
        async with _running_app():
            model_task = cast("asyncio.Task[bool]", app.state.model_task)

            assert model_task.done() is False

        assert model_task.cancelled() is True
    finally:
        release.set()


async def test_the_lifespan_calls_the_fetcher_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`asyncio.to_thread`, not a bare task around synchronous work.

    A task around a blocking call would still stall the loop the moment it was
    scheduled; the thread is what keeps `/health` answering. Asserted by
    recording the thread the acquisition actually runs on.
    """
    threads: list[int] = []

    def _record(
        classifier: PromptGuardClassifier,
        *,
        metrics: ModelMetrics | None = None,
        **_kwargs: object,
    ) -> bool:
        threads.append(threading.get_ident())
        return False

    _park_the_retry(monkeypatch)
    monkeypatch.setattr(model_fetcher, "acquire_and_load", _record)
    async with _running_app():
        await _first_attempt_failed()

    assert threads and threads[0] != threading.get_ident()


# `feature-forage-cache-fallback` US-001. The `cache:` bounds are validated at
# startup whichever storage is selected, so these run through the real lifespan
# for the same reason the weight-acquisition tests above do: the `client`
# fixture never fires it.


async def test_lifespan_publishes_the_validated_cache_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped `config.yaml` bounds reach `app.state` at startup."""
    _park_the_retry(monkeypatch)
    async with _running_app():
        settings = cast("CacheSettings", app.state.cache_settings)

        assert settings.max_entries == DEFAULT_CACHE_MAX_ENTRIES
        assert settings.max_bytes == DEFAULT_CACHE_MAX_BYTES


async def test_lifespan_refuses_an_out_of_range_cache_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A memory bound outside its range fails the boot rather than widening."""
    monkeypatch.setattr(
        retrieval_app,
        "_load_config",
        lambda: {"cache": {"max_bytes": 512 * 1024 * 1024}},
    )

    probe_app = FastAPI()
    with pytest.raises(CacheConfigurationError):
        async with lifespan(probe_app):
            pass


# ---------------------------------------------------------------------------
# Backend selection from VALKEY_URL (`feature-forage-cache-fallback` US-002)
# ---------------------------------------------------------------------------
#
# Five starts, one per configuration the operator can produce:
#
#   fully unset   -> in-memory, healthy, and no connection attempted at all
#   valid         -> Valkey, healthy
#   unreachable   -> Valkey, `degraded: cache_unavailable`
#   unparseable   -> Valkey, `degraded: cache_unavailable`
#   empty string  -> Valkey, `degraded: cache_unavailable`
#
# They run through the real `lifespan` *and the real `ContentCache`* — unlike
# `_running_app` above, which patches the cache away. That is the whole point:
# what is under test is which storage a start selects and what `/health` then
# says about it, and a patched `ContentCache` would answer both questions
# itself. Only Valkey's socket is a double.


_UNREACHABLE_VALKEY_URL = "redis://:unreachable-secret@valkey-that-is-not-there:6379/4"
_UNPARSEABLE_VALKEY_URL = "http://:unparseable-secret@wrong-scheme-host:6379/0"
_WORKING_VALKEY_URL = "redis://:working-secret@valkey:6379/4"


def _valkey_double() -> AsyncMock:
    """A Valkey client that answers every command this cache issues."""
    client = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.aclose = AsyncMock(return_value=None)
    return client


def _acquisition_that_never_loads(
    classifier: PromptGuardClassifier,
    *,
    metrics: ModelMetrics | None = None,
    **_kwargs: object,
) -> bool:
    """One acquisition attempt that finishes instantly without loading."""
    return False


@asynccontextmanager
async def _started_with_valkey_url(
    monkeypatch: pytest.MonkeyPatch,
    *,
    valkey_url: str | None,
    promptguard_loaded: bool = True,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Start the app through its real lifespan with `VALKEY_URL` exactly as given.

    ``valkey_url=None`` means *fully unset*, which is the one input that
    selects memory mode; every other value — including ``""`` — is a
    configured Valkey.

    The classifier is a double so the cache is the only thing `/health` can be
    degraded about: these tests assert `status` as well as `degraded_reasons`,
    and a real (unloaded) PromptGuard would make every run degraded for a
    reason that has nothing to do with the backend.
    """
    _park_the_retry(monkeypatch)
    monkeypatch.setattr(
        model_fetcher, "acquire_and_load", _acquisition_that_never_loads
    )
    monkeypatch.setattr(
        retrieval_app,
        "PromptGuardClassifier",
        lambda: MagicMock(spec=PromptGuardClassifier, loaded=promptguard_loaded),
    )
    if valkey_url is None:
        monkeypatch.delenv("VALKEY_URL", raising=False)
    else:
        monkeypatch.setenv("VALKEY_URL", valkey_url)

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as selection_client:
            yield selection_client


def test_only_a_fully_unset_valkey_url_reads_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_configured_valkey_url` distinguishes unset from empty, at the source.

    The distinction is a security decision, not a nicety: an empty value is
    what `VALKEY_URL=${VALKEY_URL}` renders to when the substitution has
    nothing to substitute, and collapsing it into "unset" (``... or None``)
    would answer a broken deployment with a silent, unshared memory cache.
    """
    monkeypatch.delenv("VALKEY_URL", raising=False)
    assert retrieval_app._configured_valkey_url() is None

    monkeypatch.setenv("VALKEY_URL", "")
    assert retrieval_app._configured_valkey_url() == ""

    monkeypatch.setenv("VALKEY_URL", _WORKING_VALKEY_URL)
    assert retrieval_app._configured_valkey_url() == _WORKING_VALKEY_URL


async def test_unset_valkey_url_runs_in_memory_healthy_and_never_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 1 of 5 — fully unset: in-memory, healthy, no connection attempted.

    The "no connection attempted" half is the behavioural form of "no baked
    env default remains": with the old `retrieval_app.py` default in place
    this start would build a `ValkeyStorage` for `redis://valkey:6379/4` and
    reach for it, so `from_url` not being called is what proves the default
    gone. The autouse socket guard is the second net under the same claim.
    """
    with patch("cache.aioredis") as aioredis:
        async with _started_with_valkey_url(monkeypatch, valkey_url=None) as client:
            cache = cast("ContentCache", app.state.cache)
            resp = await client.get("/health")

            assert isinstance(cache.storage, InMemoryStorage)
            aioredis.from_url.assert_not_called()

            data = resp.json()
            assert data["status"] == "healthy"
            assert data["degraded_reasons"] == []
            assert data["cache_connected"] is True


async def test_a_working_valkey_url_selects_valkey_and_stays_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 2 of 5 — set and working: Valkey, healthy, one connection made."""
    with patch("cache.aioredis") as aioredis:
        aioredis.from_url.return_value = _valkey_double()
        async with _started_with_valkey_url(
            monkeypatch, valkey_url=_WORKING_VALKEY_URL
        ) as client:
            cache = cast("ContentCache", app.state.cache)
            resp = await client.get("/health")

            assert isinstance(cache.storage, ValkeyStorage)
            assert aioredis.from_url.call_count == 1

            data = resp.json()
            assert data["status"] == "healthy"
            assert data["degraded_reasons"] == []
            assert data["cache_connected"] is True


@pytest.mark.parametrize(
    "valkey_url",
    [
        pytest.param(_UNREACHABLE_VALKEY_URL, id="unreachable"),
        pytest.param(_UNPARSEABLE_VALKEY_URL, id="unparseable"),
        pytest.param("", id="empty-string"),
    ],
)
async def test_a_broken_valkey_url_degrades_and_never_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
    valkey_url: str,
) -> None:
    """Cases 3-5 of 5 — configured and failing: Valkey, `cache_unavailable`.

    Unreachable, unparseable and empty are one behaviour on purpose. Each is a
    configuration the operator *wrote*, so each fails loudly rather than
    quietly running a cache nothing else in the deployment shares; the
    unparseable one additionally proves a bad URL is a degraded report and not
    a crashed boot.
    """
    async with _started_with_valkey_url(monkeypatch, valkey_url=valkey_url) as client:
        cache = cast("ContentCache", app.state.cache)
        resp = await client.get("/health")

        assert isinstance(cache.storage, ValkeyStorage)
        assert not isinstance(cache.storage, InMemoryStorage)

        data = resp.json()
        assert data["status"] == "degraded"
        assert data["degraded_reasons"] == ["cache_unavailable"]
        assert data["cache_connected"] is False


@pytest.mark.parametrize(
    ("valkey_url", "secret"),
    [
        pytest.param(None, None, id="unset"),
        pytest.param(_WORKING_VALKEY_URL, "working-secret", id="valid"),
        pytest.param(_UNREACHABLE_VALKEY_URL, "unreachable-secret", id="unreachable"),
        pytest.param(_UNPARSEABLE_VALKEY_URL, "unparseable-secret", id="unparseable"),
        pytest.param("", None, id="empty-string"),
    ],
)
async def test_no_selection_path_logs_the_valkey_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    valkey_url: str | None,
    secret: str | None,
) -> None:
    """All five starts keep the closed log vocabulary — no URL, no password.

    `cache.py`'s invariant, asserted here at the layer that now *chooses* the
    URL: selection reads the variable and hands it on, so a helpful "couldn't
    parse VALKEY_URL=…" anywhere along that path would leak a credential out
    of the one variable that routinely carries one.
    """
    with patch("cache.aioredis") as aioredis:
        aioredis.from_url.return_value = _valkey_double()
        with caplog.at_level(logging.DEBUG):
            async with _started_with_valkey_url(
                monkeypatch, valkey_url=valkey_url
            ) as client:
                await client.get("/health")

    # Canary: the start really did log, so the absence checks below are not
    # passing vacuously against an empty capture.
    assert caplog.text.strip()

    if valkey_url:
        assert valkey_url not in caplog.text
    if secret is not None:
        assert secret not in caplog.text
    assert "redis://" not in caplog.text
    assert "wrong-scheme-host" not in caplog.text
    assert "valkey-that-is-not-there" not in caplog.text


# ---------------------------------------------------------------------------
# `cache_backend` on /health, contract 1.1.0 (`feature-forage-cache-fallback`
# US-003)
# ---------------------------------------------------------------------------
#
# Four runs, each asserted by name rather than by loop:
#
#   memory mode          -> healthy  / memory / cache_connected true
#   Valkey up            -> healthy  / valkey / cache_connected true
#   Valkey down          -> degraded / valkey / cache_connected false
#   no cache on app.state-> /health still answers 200 and still names a backend
#
# The first three run through the same real `lifespan` the US-002 selection
# tests use; the fourth is the one `/health` path that has no cache to ask, and
# a *required* wire field read off `app.state` is exactly the shape that turns
# an honest degraded report into a 500.


@pytest.mark.parametrize(
    (
        "valkey_url",
        "valkey_reachable",
        "expected_status",
        "expected_backend",
        "expected_connected",
        "expected_reasons",
    ),
    [
        pytest.param(None, False, "healthy", "memory", True, [], id="memory-healthy"),
        pytest.param(
            _WORKING_VALKEY_URL,
            True,
            "healthy",
            "valkey",
            True,
            [],
            id="valkey-up-healthy",
        ),
        pytest.param(
            _UNREACHABLE_VALKEY_URL,
            False,
            "degraded",
            "valkey",
            False,
            [DEGRADED_CACHE_UNAVAILABLE],
            id="valkey-down-degraded",
        ),
    ],
)
async def test_health_names_the_backend_it_selected_for_this_start(
    monkeypatch: pytest.MonkeyPatch,
    valkey_url: str | None,
    valkey_reachable: bool,
    expected_status: str,
    expected_backend: str,
    expected_connected: bool,
    expected_reasons: list[str],
) -> None:
    """Cases 1-3 of 4 — the field says which storage this container is running.

    `cache_connected` alone cannot answer the operator's question. A `true`
    means "the selected backend is operational", which is what memory mode
    reports for free, so a deployment that silently lost its Valkey and a
    deployment that is healthily in memory mode look identical on Epic 1's
    surface. `cache_backend` is the field that separates them, and the live
    checklist in the consuming repo's spec 6 asserts it reads `valkey` in
    production.
    """
    with ExitStack() as stack:
        if valkey_reachable:
            aioredis = stack.enter_context(patch("cache.aioredis"))
            aioredis.from_url.return_value = _valkey_double()
        async with _started_with_valkey_url(
            monkeypatch, valkey_url=valkey_url
        ) as client:
            data = (await client.get("/health")).json()

    assert data["status"] == expected_status
    assert data["cache_backend"] == expected_backend
    assert data["cache_connected"] is expected_connected
    assert data["degraded_reasons"] == expected_reasons
    assert data["contract_version"] == CONTRACT_VERSION


async def test_health_without_a_cache_still_names_a_backend_and_never_500s(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 4 of 4 — no cache on `app.state`: 200, and the field still answers.

    `test_health_missing_cache_reports_unavailable_never_raises` guards this
    path for Epic 1's fields. `cache_backend` is *required* by the response
    model, so a start that published no value would fail model validation and
    500 the one endpoint that must never 500 — the fallback below is what makes
    it a degraded report instead. It reads the environment, the same question
    the lifespan asked, so the answer tracks the deployment rather than being a
    hard-coded guess.
    """
    published = getattr(app.state, "cache_backend", None)
    app.state.cache = None
    if published is not None:
        delattr(app.state, "cache_backend")
    try:
        monkeypatch.delenv("VALKEY_URL", raising=False)
        unconfigured = await client.get("/health")

        monkeypatch.setenv("VALKEY_URL", _WORKING_VALKEY_URL)
        configured = await client.get("/health")
    finally:
        app.state.cache = FakeContentCache()
        if published is not None:
            app.state.cache_backend = published

    assert unconfigured.status_code == 200
    assert unconfigured.json()["cache_backend"] == "memory"
    assert unconfigured.json()["cache_connected"] is False
    assert DEGRADED_CACHE_UNAVAILABLE in unconfigured.json()["degraded_reasons"]

    assert configured.status_code == 200
    assert configured.json()["cache_backend"] == "valkey"


def test_the_backend_name_never_drifts_from_the_storage_actually_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_configured_cache_backend` answers what `_select_cache_storage` builds.

    Two functions encode the same rule — one for the lifespan, which needs the
    storage, and one for `/health`'s fallback, which has nowhere to put one.
    This is the gate that keeps them the same rule.
    """
    monkeypatch.delenv("VALKEY_URL", raising=False)
    storage, backend = retrieval_app._select_cache_storage(
        settings=CacheSettings(),
        metrics=CacheMetrics(),
    )
    assert isinstance(storage, InMemoryStorage)
    assert backend == "memory" == retrieval_app._configured_cache_backend()

    monkeypatch.setenv("VALKEY_URL", _WORKING_VALKEY_URL)
    storage, backend = retrieval_app._select_cache_storage(
        settings=CacheSettings(),
        metrics=CacheMetrics(),
    )
    assert isinstance(storage, ValkeyStorage)
    assert backend == "valkey" == retrieval_app._configured_cache_backend()


async def test_the_lifespan_publishes_the_backend_it_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The value `/health` reads is decided once, at start, not per request.

    A request-time read of `VALKEY_URL` would report a backend the running
    process is not using the moment anyone changed the environment, which is
    the drift `app.state` exists to prevent.
    """
    async with _started_with_valkey_url(monkeypatch, valkey_url=None) as client:
        assert app.state.cache_backend == "memory"

        monkeypatch.setenv("VALKEY_URL", _WORKING_VALKEY_URL)
        data = (await client.get("/health")).json()

    assert data["cache_backend"] == "memory"
