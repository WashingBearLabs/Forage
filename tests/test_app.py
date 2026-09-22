"""Tests for Retrieval sidecar FastAPI server (US-001)."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import os
import stat
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import yaml
from fastapi import FastAPI
from starlette.types import Message, Receive, Scope, Send

import model_fetcher
import retrieval_app
import url_validator
from cache import (
    DEFAULT_CACHE_MAX_BYTES,
    DEFAULT_CACHE_MAX_ENTRIES,
    CacheConfigurationError,
    CacheMetrics,
    CacheSettings,
    ContentCache,
    InMemoryStorage,
    ValkeyStorage,
    _effective_ttl_hours,
)
from model_fetcher import DEFAULT_MODEL_REVISION, ModelMetrics
from models import (
    RetrievedContent,
    RetrieveRequest,
    SearchRequest,
    SearchResponse,
    Stage2Verdict,
    Stage3Verdict,
    TrustTier,
)
from pipeline import contract, orchestrator
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
from pipeline.retrieve_limits import (
    RetrieveConfigurationError,
    RetrieveSettings,
    retrieve_settings_from_config,
)
from pipeline.search_providers import SearchProviderConfigurationError
from pipeline.search_providers.base import (
    ProviderFailure,
    ProviderSearchResult,
    SearchProvider,
)
from pipeline.search_providers.brave import BRAVE_API_KEY_ENV_VAR
from pipeline.search_providers.searxng import DEFAULT_SEARXNG_URL
from pipeline.stage1_extraction import ExtractionResult, extract_html
from pipeline.stage5_url_audit import DEFAULT_MAX_CONTENT_BYTES, FetchResult
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
    FakeSearchProvider,
    FakeStorage,
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
    app.state.retrieve_settings = retrieve_settings_from_config(app.state.config)
    app.state.promptguard_threshold_default = 0.85
    app.state.policy_domain_entries_max_bytes = 65536
    settings = extraction_settings_from_config(app.state.config)
    app.state.extraction_settings = settings
    app.state.extraction_metrics = ExtractionMetrics()
    app.state.extraction_admission = ExtractionAdmissionController(
        settings,
        app.state.extraction_metrics,
    )
    app.state.search_metrics = SearchMetrics()
    app.state.retrieve_metrics = RetrieveMetrics()
    app.state.retrieve_admission = ExtractionAdmissionController.from_retrieve_settings(
        RetrieveSettings(), app.state.retrieve_metrics
    )
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
        "fallback_fired": 0,
        "paid_calls": 0,
        "policy_unknown_provider": 0,
        "policy_invalid_domain_entry": 0,
        "policy_suffix_trusted_skip": 0,
        "classification_wait_timeouts": 0,
    }
    assert body["retrieve"] == {
        "requests": 0,
        "errors": {},
        "cache_hits": 0,
        "cache_misses": 0,
        "blocked_by_reason": {},
        "promptguard_state": {},
        "policy_invalid_domain_entry": 0,
        "policy_suffix_trusted_skip": 0,
        "classification_wait_timeouts": 0,
        "semaphore_saturation": 0,
        "busy_rejections": 0,
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
        "corrupt_entries",
    }


@pytest.mark.parametrize(
    "shape", ["invalid-json", "wrong-schema", "wrong-retrieved-at"]
)
async def test_retrieve_repopulates_a_corrupt_cache_entry(
    client: httpx.AsyncClient,
    shape: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = CacheMetrics()
    storage = FakeStorage(metrics=metrics)
    app.state.cache = ContentCache(storage=storage, metrics=metrics)
    monkeypatch.setattr(app.state, "cache_metrics", metrics)
    url = "https://example.com/article"
    request = {"url": url, "trusted_domains": ["example.com"]}
    fetched = FetchResult(
        final_url=url,
        content_type="text/html",
        response_body=b"<html><body><p>A safe article.</p></body></html>",
        status_code=200,
    )
    with (
        patch(
            "pipeline.orchestrator.validate_url",
            return_value=("93.184.216.34", "example.com"),
        ),
        patch("pipeline.orchestrator.fetch_url", return_value=fetched) as fetch,
        caplog.at_level(logging.WARNING, logger="cache"),
    ):
        warm = await client.post("/retrieve", json=request)
        assert warm.status_code == 200
        assert not warm.json()["cache_hit"]
        (key,) = storage.entries
        if shape == "invalid-json":
            raw = "not-json-CORRUPT-VALUE-SENTINEL"
        elif shape == "wrong-schema":
            raw = '{"not": "CORRUPT-VALUE-SENTINEL"}'
        else:
            raw = json.dumps(
                warm.json() | {"retrieved_at": {"secret": "CORRUPT-VALUE-SENTINEL"}}
            )
        await storage.set(key, raw, ttl_seconds=3600)

        response = await client.post("/retrieve", json=request)
        assert response.status_code == 200
        assert response.json()["cache_hit"] is False
        assert response.json()["body"] == warm.json()["body"]
        assert storage.delete_calls == 1
        assert metrics.corrupt_entries == 1
        assert (
            RetrievedContent.model_validate_json(storage.entries[key][0]).body
            == (warm.json()["body"])
        )

        hit = await client.post("/retrieve", json=request)
        assert hit.status_code == 200
        assert hit.json()["cache_hit"] is True
        assert hit.json()["body"] == warm.json()["body"]
        assert fetch.call_count == 2
        assert metrics.corrupt_entries == 1

    warnings = [record for record in caplog.records if record.name == "cache"]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert warnings[0].getMessage() == (
        f"Content cache entry rejected (cache_entry_corrupt) key={key}"
    )
    assert "CORRUPT-VALUE-SENTINEL" not in caplog.text
    response_metrics = await client.get("/metrics")
    assert response_metrics.status_code == 200
    assert response_metrics.json()["cache"]["corrupt_entries"] == 1
    assert response_metrics.json()["retrieve"]["cache_misses"] == 2
    assert response_metrics.json()["retrieve"]["cache_hits"] == 1


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
    ) as pipeline:
        resp = await client.post("/retrieve", json={"url": "https://example.com/a"})
    assert resp.status_code == 200
    effective = pipeline.call_args.args[0]
    assert isinstance(effective, RetrieveRequest)
    assert effective.trusted_domains == []
    assert effective.verified_domains == []
    assert effective.blocked_domains == []

    metrics_resp = await client.get("/metrics")
    retrieve_metrics = metrics_resp.json()["retrieve"]
    assert retrieve_metrics["requests"] == 1
    assert retrieve_metrics["cache_hits"] == 1
    assert retrieve_metrics["cache_misses"] == 0
    assert retrieve_metrics["promptguard_state"] == {"scanned": 1}
    assert retrieve_metrics["blocked_by_reason"] == {}


def test_retrieve_copies_once_and_comparison_sites_never_normalize() -> None:
    source = inspect.getsource(retrieval_app.retrieve)
    tree = ast.parse(source)
    copies = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "body"
        and node.func.attr == "model_copy"
    ]
    assert len(copies) == 1
    update = copies[0].keywords[0]
    assert update.arg == "update"
    assert isinstance(update.value, ast.Dict)
    assert {
        key.value for key in update.value.keys if isinstance(key, ast.Constant)
    } == {"trusted_domains", "verified_domains", "blocked_domains"}
    assert (
        "_promptguard_policy_updates( body, retrieve_settings, "
        "request.app.state.promptguard_threshold_default )" in " ".join(source.split())
    )
    root = Path(__file__).resolve().parent.parent
    for filename in ("pipeline/orchestrator.py", "cache.py"):
        assert "normalize_domain_entries(" not in (root / filename).read_text()
    assert (root / "url_validator.py").read_text().count(
        "normalize_domain_entries("
    ) == 1


@pytest.mark.parametrize("field", ["trusted_domains", "verified_domains"])
@pytest.mark.parametrize("over_budget", [False, True])
@pytest.mark.parametrize("invalid_entry", ["com", "\ud800"])
async def test_retrieve_normalizes_each_list_once_in_the_single_policy_copy(
    client: httpx.AsyncClient,
    field: str,
    over_budget: bool,
    invalid_entry: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    budget = 4096
    monkeypatch.setattr(app.state, "policy_domain_entries_max_bytes", budget)
    monkeypatch.setattr(
        app.state,
        "retrieve_settings",
        RetrieveSettings(
            promptguard_fail_closed_floor=True, promptguard_threshold_ceiling=0.5
        ),
    )
    if over_budget:
        prefix = " .Example.COM. "
        tail = "straße.de"
        padding = " " * (budget - len((prefix + "\n" + tail).encode("utf-8")))
        raw_entries = [prefix, padding + tail, "overbudget.example", "bad..entry"]
        assert url_validator.domain_list_bytes(raw_entries[:2]) == budget
        expected = [".example.com", "xn--strae-oqa.de"]
        dropped = 2
    else:
        raw_entries = [invalid_entry, " .Example.COM. "] + [
            f"Site{index}.EXAMPLE." for index in range(68)
        ]
        expected = [".example.com"] + [f"site{index}.example" for index in range(68)]
        assert len(raw_entries) == 70
        assert len(expected) == 69
        dropped = 1
    other_field = (
        "verified_domains" if field == "trusted_domains" else "trusted_domains"
    )
    payload = {
        "url": "https://example.com/",
        "promptguard_threshold": 0.9,
        "promptguard_fail_closed": False,
        field: raw_entries,
        other_field: [" " * (budget - len(".Other.EXAMPLE")) + ".Other.EXAMPLE"],
        "blocked_domains": [" Evil.COM. ", "straße.de"]
        + (["\ud800"] if over_budget else []),
    }
    content = RetrievedContent(
        request_id="domain-policy",
        source_url="https://example.com/",
        final_url="https://example.com/",
        body="A calm page.",
        word_count=3,
        content_type="html",
        trust_score=0.7,
        trust_tier=TrustTier.STANDARD,
        stage2_verdict=Stage2Verdict.CLEAN,
        stage3_verdict=Stage3Verdict.SAFE,
        domain="example.com",
    )
    with (
        patch("retrieval_app.run_retrieve_pipeline", return_value=content) as pipeline,
        patch(
            "retrieval_app.normalize_domain_entries",
            wraps=url_validator.normalize_domain_entries,
        ) as normalize,
        patch.object(
            RetrieveRequest,
            "model_copy",
            autospec=True,
            side_effect=RetrieveRequest.model_copy,
        ) as copy,
    ):
        response = await client.post(
            "/retrieve",
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 200
    effective = pipeline.call_args.args[0]
    assert isinstance(effective, RetrieveRequest)
    assert getattr(effective, field) == expected
    assert getattr(effective, other_field) == [".other.example"]
    assert effective.blocked_domains == ["evil.com", "xn--strae-oqa.de"]
    assert effective.promptguard_threshold == 0.5
    assert effective.promptguard_fail_closed is True
    assert normalize.call_count == 3
    assert [call.kwargs for call in normalize.call_args_list] == [
        {"denylist": False, "budget_bytes": budget},
        {"denylist": False, "budget_bytes": budget},
        {"denylist": True, "budget_bytes": None},
    ]
    copy.assert_called_once()
    assert getattr(copy.call_args.args[0], field) == raw_entries
    assert set(copy.call_args.kwargs["update"]) == {
        "trusted_domains",
        "verified_domains",
        "blocked_domains",
        "promptguard_threshold",
        "promptguard_fail_closed",
    }
    counters = (await client.get("/metrics")).json()
    assert counters["retrieve"]["policy_invalid_domain_entry"] == dropped + (
        1 if over_budget else 0
    )
    assert counters["search"]["policy_invalid_domain_entry"] == 0
    assert counters["search"]["policy_suffix_trusted_skip"] == 0
    assert "\ud800" not in caplog.text
    assert "overbudget.example" not in caplog.text


@pytest.mark.parametrize("budget", [4096, 65536, 1048576])
async def test_retrieve_refuses_over_budget_denylist_before_any_canonicalization(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, budget: int
) -> None:
    monkeypatch.setattr(app.state, "policy_domain_entries_max_bytes", budget)
    # Raw bytes, not the stripped spelling or entry count, determine refusal.
    entries = [" " * budget + "evil.com"]
    with (
        patch("retrieval_app.run_retrieve_pipeline") as pipeline,
        patch("url_validator.canonicalize_host") as canonicalize,
        patch("retrieval_app.normalize_domain_entries") as normalize,
    ):
        response = await client.post(
            "/retrieve",
            json={
                "url": "https://example.com/",
                "blocked_domains": entries,
                "trusted_domains": [".example.com"],
                "verified_domains": [".other.example"],
            },
        )
    assert response.status_code == 422
    assert response.json() == {
        "error": "content_too_large",
        "reason": contract.POLICY_DOMAIN_LIST_TOO_LARGE,
        "request_id": response.json()["request_id"],
    }
    assert len(response.json()["request_id"]) == 32
    pipeline.assert_not_called()
    canonicalize.assert_not_called()
    normalize.assert_not_called()
    counters = (await client.get("/metrics")).json()["retrieve"]
    assert counters["errors"] == {"content_too_large": 1}
    assert counters["requests"] == 1
    assert counters["policy_invalid_domain_entry"] == 0


async def test_retrieve_enforces_a_denylist_at_the_exact_raw_byte_budget(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state, "policy_domain_entries_max_bytes", 4096)
    entries = [" " * (4096 - len("EVIL.com")) + "EVIL.com"]
    with (
        patch("pipeline.orchestrator.validate_url", new=url_validator.validate_url),
        patch("url_validator.socket.getaddrinfo") as dns,
    ):
        response = await client.post(
            "/retrieve",
            json={"url": "https://www.evil.com/", "blocked_domains": entries},
        )
    assert response.status_code == 422
    assert response.json()["error"] == "blocked_domain"
    dns.assert_not_called()
    assert app.state.retrieve_metrics.policy_invalid_domain_entry == 0


@pytest.mark.parametrize("field", ["trusted_domains", "verified_domains"])
@pytest.mark.parametrize("entry", [".example.com", "www.example.com"])
async def test_retrieve_wildcard_resolution_reaches_served_metrics(
    client: httpx.AsyncClient, field: str, entry: str
) -> None:
    classifier = MagicMock(spec=PromptGuardClassifier)
    classifier.loaded = field == "trusted_domains"
    app.state.classifier = classifier
    url = "https://www.example.com/"
    with (
        patch(
            "pipeline.orchestrator.validate_url",
            return_value=("93.184.216.34", "www.example.com"),
        ),
        patch(
            "pipeline.orchestrator.fetch_url",
            return_value=FetchResult(
                final_url=url,
                response_body=b"<html><body><p>A calm article.</p></body></html>",
                content_type="text/html",
                status_code=200,
            ),
        ),
    ):
        response = await client.post("/retrieve", json={"url": url, field: [entry]})
    assert response.status_code == 200
    state = "skipped_trusted" if field == "trusted_domains" else "unavailable_allowed"
    assert response.json()["promptguard_state"] == state
    counters = (await client.get("/metrics")).json()["retrieve"]
    assert counters["policy_suffix_trusted_skip"] == int(entry.startswith("."))
    assert counters["promptguard_state"] == {state: 1}
    classifier.classify.assert_not_called()


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
        provider_used="searxng",
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


async def test_metrics_search_records_the_url_audit_blocks(
    client: httpx.AsyncClient,
) -> None:
    """US-003: drive A's eighteen blocks reach `/metrics` through the handler.

    The real pipeline, not a stubbed `SearchResponse`: `blocked_url` has to
    survive `contract.OMISSION_REASONS` membership or it buckets to `other`,
    and that membership is the half a response-level assertion cannot see.
    """
    from tests.test_orchestrator import _AUDIT_DRIVE_A_ROWS

    hostile = [raw for raw, reason, _token in _AUDIT_DRIVE_A_ROWS if reason is not None]
    assert len(hostile) == 18
    fake = FakeSearchProvider(
        name="searxng",
        outcome=ProviderSearchResult(
            provider_name="searxng",
            results=[
                {"title": f"h{index}", "url": raw, "content": "a snippet"}
                for index, raw in enumerate(hostile)
            ],
            unresponsive_engines=[],
        ),
    )

    with _borrowed_search_providers([fake]):
        resp = await client.post(
            "/search",
            json={
                "query": "audit",
                "num_results": 10,
                "promptguard_fail_closed": False,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["results"] == []
    assert resp.json()["omitted_by_reason"] == {contract.OMIT_BLOCKED_URL: 18}

    metrics_resp = await client.get("/metrics")
    search_metrics = metrics_resp.json()["search"]
    assert search_metrics["omitted_by_reason"] == {contract.OMIT_BLOCKED_URL: 18}
    assert contract.METRICS_OTHER_BUCKET not in search_metrics["omitted_by_reason"]


async def test_metrics_search_omitted_by_reason_unknown_key_buckets_to_other(
    client: httpx.AsyncClient,
) -> None:
    """An out-of-vocabulary omission reason buckets to ``METRICS_OTHER_BUCKET``."""
    response = SearchResponse(
        results=[],
        request_id="s2",
        query="test",
        provider_used="searxng",
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
    # The `reason` format US-002 narrowed to: an origin plus a closed detail
    # token, never `str(exc)`. Written out by hand here because this test
    # patches `run_search_pipeline` away — so if the real composition changes
    # again, this fixture is what has to be brought back into step with it.
    exc = PipelineError(
        error="searxng_unavailable",
        reason=f"SearXNG not reachable at {searxng_url}: connect_error",
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


async def test_metrics_counts_search_unavailable_under_its_own_key(
    client: httpx.AsyncClient,
) -> None:
    """The 1.2.0 code reaches `/metrics` as itself, not as an overflow bucket.

    `SearchMetrics.record_error` keys straight off `PipelineError.error` with
    no closed-set filter, so a code added to the vocabulary is counted under
    its own name today. This pins that: a filter introduced later — or a
    mapping that folded the new code into the legacy pair — would fail here
    rather than quietly changing what an operator's dashboard sums.
    """
    exc = PipelineError(
        error="search_unavailable",
        reason="brave: quota",
        request_id="s4",
    )
    with patch("retrieval_app.run_search_pipeline", new=AsyncMock(side_effect=exc)):
        resp = await client.post("/search", json={"query": "test"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "search_unavailable"

    metrics_resp = await client.get("/metrics")
    body = metrics_resp.json()
    assert body["search"]["errors"] == {"search_unavailable": 1}
    assert body["search"]["requests"] == 1


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


async def test_health_answers_while_a_fetched_page_is_being_extracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/health` stays responsive while stage 1 parses a fetched page.

    The shape of the test above, with the same connected cache for the same
    reason — no reconnect floor to hide behind — and a different thing held:
    a `/retrieve` whose `extract_html` is parked in its worker thread, standing
    in for a pathological page. With stage 1 on the event loop every `/health`
    would wait for it (`hardening-retrieve-parity` US-002).
    """
    acquisition = threading.Event()
    monkeypatch.setattr(
        model_fetcher, "acquire_and_load", _blocking_acquisition(acquisition)
    )
    entered = threading.Event()
    release = threading.Event()

    def _blocking_extract_html(
        html_content: str, source_url: str | None = None
    ) -> ExtractionResult:
        entered.set()
        release.wait(timeout=10)
        return extract_html(html_content, source_url)

    fetch = AsyncMock(
        return_value=FetchResult(
            final_url="https://example.com/",
            response_body=b"<html><body><p>A calm page.</p></body></html>",
            content_type="text/html",
            status_code=200,
        )
    )
    try:
        with (
            patch(
                "pipeline.orchestrator.validate_url",
                new=AsyncMock(return_value=("93.184.216.34", "example.com")),
            ),
            patch("pipeline.orchestrator.fetch_url", new=fetch),
            patch("pipeline.orchestrator.extract_html", new=_blocking_extract_html),
        ):
            async with _running_app(cache_connected=True) as client:
                retrieve = asyncio.create_task(
                    client.post("/retrieve", json={"url": "https://example.com/"})
                )
                deadline = time.monotonic() + 10
                while not entered.is_set():
                    assert time.monotonic() < deadline, "extraction never started"
                    await asyncio.sleep(0.01)

                latencies: list[float] = []
                for _ in range(5):
                    started_at = time.monotonic()
                    response = await client.get("/health")
                    latencies.append(time.monotonic() - started_at)
                    assert response.status_code == 200

                assert max(latencies) < 1.0
                assert retrieve.done() is False
                release.set()
                assert (await retrieve).status_code == 200
    finally:
        release.set()
        acquisition.set()


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


@pytest.mark.parametrize(
    "config",
    [
        [],
        ["cache"],
        "scalar",
        1,
        None,
        {"cache": "yes"},
        {"extraction": []},
        {"retrieve": None},
    ],
)
def test_unknown_key_warning_leaves_malformed_values_to_readers(
    config: object, caplog: pytest.LogCaptureFixture
) -> None:
    assert retrieval_app._warn_unknown_config_keys(config) == []
    assert not caplog.records


def test_unknown_blocks_are_not_walked_and_values_are_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = {
        "unknown_block": {"nested": "SENTINEL-VALUE"},
        "cache": MappingProxyType({"max_entires": {"nested": "SENTINEL-VALUE"}}),
        "extraction": {"max_pages": {"not_a_config_key": "SENTINEL-VALUE"}},
    }
    assert retrieval_app._warn_unknown_config_keys(config) == [
        "unknown_block",
        "cache.max_entires",
    ]
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.WARNING, "config_unknown_key — key=unknown_block"),
        (logging.WARNING, "config_unknown_key — key=cache.max_entires"),
    ]
    assert "SENTINEL-VALUE" not in caplog.text


def test_unknown_key_warning_discovers_new_registered_blocks(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        retrieval_app,
        "KNOWN_CONFIG_KEYS",
        retrieval_app.KNOWN_CONFIG_KEYS | {"future", "future.limit"},
    )
    assert retrieval_app._warn_unknown_config_keys(
        {"future": {"limit": 1, "typo": "SENTINEL-VALUE"}}
    ) == ["future.typo"]
    assert [record.getMessage() for record in caplog.records] == [
        "config_unknown_key — key=future.typo"
    ]


@pytest.mark.parametrize("with_typos", [False, True])
async def test_lifespan_warns_for_unknown_keys_without_changing_health(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    with_typos: bool,
) -> None:
    _park_the_retry(monkeypatch)
    monkeypatch.setattr(app, "state", type(app.state)())
    async with _running_app() as running:
        baseline = (await running.get("/health")).json()
    caplog.clear()
    config = retrieval_app._load_config()
    expected: list[str] = []
    if with_typos:
        config["promtguard_threshold"] = "SENTINEL-VALUE"
        config["extraction"]["max_pagse"] = "SENTINEL-VALUE"
        config["retrieve"]["max_promptguard_chnuks"] = "SENTINEL-VALUE"
        expected = [
            "extraction.max_pagse",
            "retrieve.max_promptguard_chnuks",
            "promtguard_threshold",
        ]
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: config)
    with caplog.at_level(logging.DEBUG):
        async with _running_app() as running:
            response = await running.get("/health")
            assert response.status_code == 200
            health = response.json()
            for field in (
                "status",
                "degraded_reasons",
                "promptguard_loaded",
                "cache_connected",
                "sanitizer_revision",
            ):
                assert health[field] == baseline[field]
    warnings = [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if "config_unknown_key" in record.getMessage()
    ]
    assert warnings == [
        (logging.WARNING, f"config_unknown_key — key={key}") for key in expected
    ]
    assert "SENTINEL-VALUE" not in caplog.text


@pytest.mark.parametrize(
    ("config", "error"),
    [
        ({"cache": "yes"}, CacheConfigurationError),
        ({"cache": {"max_entries": False}}, CacheConfigurationError),
        ({"extraction": []}, ExtractionConfigurationError),
        ({"extraction": {"max_pages": False}}, ExtractionConfigurationError),
        ({"retrieve": "yes"}, RetrieveConfigurationError),
        ({"retrieve": {"fetch_concurrency": False}}, RetrieveConfigurationError),
    ],
)
async def test_known_bad_config_values_still_refuse_boot(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    config: dict[str, Any],
    error: type[ValueError],
) -> None:
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: config)
    with pytest.raises(error):
        async with lifespan(FastAPI()):
            pytest.fail("invalid configuration started")
    assert not any(
        "config_unknown_key" in record.getMessage() for record in caplog.records
    )


@pytest.mark.parametrize(
    ("config", "expected", "warn"),
    [
        ({}, 65536, False),
        ({"policy_domain_entries_max_bytes": 4096}, 4096, False),
        ({"policy_domain_entries_max_bytes": 1048576}, 1048576, False),
        *[
            ({"policy_domain_entries_max_bytes": value}, 65536, True)
            for value in (
                4095,
                1048577,
                True,
                False,
                None,
                "secret-value",
                65536.0,
                list[str](),
            )
        ],
    ],
)
async def test_lifespan_domain_budget_warns_and_falls_back_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    config: dict[str, Any],
    expected: int,
    warn: bool,
) -> None:
    _park_the_retry(monkeypatch)
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: config)
    # The global app's state is shared across tests; isolate this boot.
    monkeypatch.setattr(app, "state", type(app.state)())
    async with _running_app():
        assert app.state.policy_domain_entries_max_bytes == expected
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "config_invalid_value" in record.getMessage()
    ]
    assert warnings == (
        ["config_invalid_value — key=policy_domain_entries_max_bytes"] if warn else []
    )
    assert "secret-value" not in caplog.text


def test_shipped_domain_budget_is_64_kib() -> None:
    assert retrieval_app._load_config()["policy_domain_entries_max_bytes"] == 65536


@pytest.mark.parametrize(
    ("config", "expected", "warn"),
    [
        (dict[str, Any](), 0.85, False),
        *[
            ({"promptguard_threshold": value}, 0.85, True)
            for value in (
                "abc",
                -0.1,
                1.7,
                True,
                False,
                None,
                list[str](),
                dict[str, object](),
                float("nan"),
                float("inf"),
                -float("inf"),
                10**400,
                -(10**400),
            )
        ],
        *[
            ({"promptguard_threshold": value}, float(value), False)
            for value in ("0.85", "0.5", 0, 1, 0.5)
        ],
    ],
)
async def test_lifespan_validates_threshold_once_without_mutating_raw_config(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    config: dict[str, Any],
    expected: float,
    warn: bool,
) -> None:
    _park_the_retry(monkeypatch)
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: config)
    monkeypatch.setattr(app, "state", type(app.state)())
    with caplog.at_level(logging.INFO, logger="retrieval_app"):
        async with _running_app():
            assert app.state.promptguard_threshold_default == expected
            if "promptguard_threshold" in config:
                assert (
                    app.state.config["promptguard_threshold"]
                    is config["promptguard_threshold"]
                )
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "config_invalid_value" in record.getMessage()
    ]
    assert warnings == (
        [
            "config_invalid_value — key=promptguard_threshold. "
            "/extract reads the raw value through its own guard"
        ]
        if warn
        else []
    )
    resolved = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO
        and "promptguard_threshold_resolved" in record.getMessage()
    ]
    assert resolved == [f"promptguard_threshold_resolved — value={expected}"]


async def test_boolean_threshold_keeps_extracts_raw_coercion_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _park_the_retry(monkeypatch)
    raw = {"promptguard_threshold": True, "extract_route_enabled": True}
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: raw)
    monkeypatch.setattr(app, "state", type(app.state)())
    async with _running_app() as session:
        assert app.state.promptguard_threshold_default == 0.85
        classifier = MagicMock(spec=PromptGuardClassifier)
        classifier.loaded = True
        classifier.classify.return_value = (0.9, [])
        app.state.classifier = classifier
        app.state.search_providers = [
            FakeSearchProvider(
                name="searxng",
                outcome=ProviderSearchResult(
                    provider_name="searxng",
                    results=[
                        {
                            "url": "https://example.com/",
                            "title": "Gardening",
                            "snippet": "A calm article.",
                        }
                    ],
                    unresponsive_engines=[],
                ),
            )
        ]
        with (
            patch(
                "pipeline.orchestrator.validate_url",
                return_value=("93.184.216.34", "example.com"),
            ),
            patch(
                "pipeline.orchestrator.fetch_url",
                return_value=FetchResult(
                    final_url="https://example.com/",
                    content_type="text/html",
                    response_body=b"<p>A calm article.</p>",
                    status_code=200,
                ),
            ),
            patch(
                "pipeline.orchestrator.run_promptguard",
                wraps=orchestrator.run_promptguard,
            ) as scan,
        ):
            retrieve = await session.post(
                "/retrieve", json={"url": "https://example.com/"}
            )
            search = await session.post("/search", json={"query": "gardening"})
            extract = await session.post(
                "/extract",
                files={"file": ("article.txt", b"A calm article.", "text/plain")},
                data={"filename": "article.txt"},
            )
        assert [response.status_code for response in (retrieve, search, extract)] == [
            200,
            200,
            200,
        ]
        assert retrieve.json()["injection_detected"] is True
        assert search.json()["omitted_by_reason"] == {"injection_detected": 1}
        for response in (retrieve, search):
            assert response.json()["effective_promptguard_threshold"] == 0.85
        assert extract.json()["injection_detected"] is False
        assert [call.kwargs["threshold"] for call in scan.call_args_list] == [
            0.85,
            0.85,
            1.0,
        ]
    assert [
        record.getMessage()
        for record in caplog.records
        if "config_invalid_value" in record.getMessage()
    ] == [
        "config_invalid_value — key=promptguard_threshold. "
        "/extract reads the raw value through its own guard"
    ]


async def test_lifespan_normalizes_domain_lists_and_names_drops(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _park_the_retry(monkeypatch)
    raw: dict[str, Any] = {
        "seed_blocklist": [" Evil.COM. ", "bad..entry", "intranet"],
        "news_domains": ["BBC.co.uk", ".Example.ORG", "com"],
    }
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: raw)
    with (
        patch("pipeline.orchestrator.validate_url", new=url_validator.validate_url),
        patch(
            "url_validator.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ),
        patch(
            "pipeline.orchestrator.fetch_url",
            new=AsyncMock(
                return_value=FetchResult(
                    final_url="https://wiki.intranet/",
                    response_body=b"<html><body><p>A calm page.</p></body></html>",
                    content_type="text/html",
                    status_code=200,
                )
            ),
        ),
    ):
        async with _running_app() as client:
            assert app.state.config["seed_blocklist"] == ["evil.com", "intranet"]
            assert app.state.config["news_domains"] == ["bbc.co.uk", ".example.org"]
            assert raw["seed_blocklist"] == [" Evil.COM. ", "bad..entry", "intranet"]
            assert raw["news_domains"] == ["BBC.co.uk", ".Example.ORG", "com"]
            assert (
                _effective_ttl_hours(
                    24,
                    domain="bbc.co.uk",
                    news_domains=app.state.config["news_domains"],
                )
                == 1
            )
            for host in ("www.evil.com", "intranet"):
                response = await client.post(
                    "/retrieve", json={"url": f"https://{host}/"}
                )
                assert response.status_code == 422
                assert response.json()["error"] == "blocked_domain"
            response = await client.post(
                "/retrieve",
                json={
                    "url": "https://wiki.intranet/",
                    "promptguard_fail_closed": False,
                },
            )
            assert response.status_code == 200
            response = await client.post(
                "/retrieve",
                json={"url": "https://evil.local/", "blocked_domains": ["evil.local"]},
            )
            assert response.status_code == 422
            assert response.json()["error"] == "private_ip"
    warnings = [
        record
        for record in caplog.records
        if "config_invalid_value" in record.getMessage()
    ]
    assert len(warnings) == 2
    for record, key, entry in zip(
        warnings, ("seed_blocklist", "news_domains"), ("bad..entry", "com"), strict=True
    ):
        assert record.levelno == logging.WARNING
        assert f"key={key} dropped=1 entries={entry}" in record.getMessage()


@pytest.mark.parametrize("shipped", [True, False])
async def test_lifespan_valid_domain_lists_are_unbudgeted_and_quiet(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, shipped: bool
) -> None:
    _park_the_retry(monkeypatch)
    entries = [f"news{index}.example" for index in range(70)]
    if not shipped:
        monkeypatch.setattr(
            retrieval_app, "_load_config", lambda: {"news_domains": entries}
        )
    async with _running_app():
        if shipped:
            assert app.state.config["news_domains"] == [
                ".reuters.com",
                ".apnews.com",
                ".bbc.co.uk",
                ".nytimes.com",
                ".theguardian.com",
                ".cnn.com",
            ]
            assert (
                _effective_ttl_hours(
                    24,
                    domain="www.bbc.co.uk",
                    news_domains=app.state.config["news_domains"],
                )
                == 1
            )
        else:
            assert app.state.config["news_domains"] == entries
    assert not any(
        "config_invalid_value" in record.getMessage() for record in caplog.records
    )


async def test_domain_config_warning_does_not_echo_misplaced_credentials(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _park_the_retry(monkeypatch)
    monkeypatch.setattr(
        retrieval_app,
        "_load_config",
        lambda: {"news_domains": ["https://user:secret@example.com/"]},
    )
    async with _running_app():
        assert app.state.config["news_domains"] == []
    assert "key=news_domains dropped=1 entries=[redacted]" in caplog.text
    assert "secret" not in caplog.text


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
# `retrieve:` limits (`hardening-retrieve-parity` US-001)
# ---------------------------------------------------------------------------


class TestRetrieveSettingsReader:
    """`retrieve_settings_from_config` — defaults, bounds, and the derivation."""

    def test_an_empty_config_is_todays_behaviour(self) -> None:
        settings = retrieve_settings_from_config({})
        assert settings.max_promptguard_chunks == 0
        assert settings.max_extracted_characters is None
        assert settings.fetch_concurrency == 1
        assert settings.admission_queue_depth == 4
        assert settings.max_queued_fetch_bytes == 31457280
        assert settings.promptguard_fail_closed_floor is False
        assert settings.promptguard_threshold_ceiling == 1.0
        assert settings.promptguard_wait_seconds == 30.0

    def test_the_byte_bound_binds_before_the_depth_bound_at_the_defaults(self) -> None:
        """Both bounds are exercisable — the default is not depth x 10 MB."""
        settings = retrieve_settings_from_config({})
        assert (
            settings.max_queued_fetch_bytes
            < settings.admission_queue_depth * DEFAULT_MAX_CONTENT_BYTES
        )

    def test_a_set_budget_derives_the_character_ceiling(self) -> None:
        settings = retrieve_settings_from_config(
            {"retrieve": {"max_promptguard_chunks": 256}}
        )
        assert settings.max_promptguard_chunks == 256
        assert settings.max_extracted_characters == 458752

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("max_promptguard_chunks", -1),
            ("max_promptguard_chunks", 1025),
            ("fetch_concurrency", 0),
            ("fetch_concurrency", 2),
            ("admission_queue_depth", -1),
            ("admission_queue_depth", 17),
            ("max_queued_fetch_bytes", 1024),
            ("max_queued_fetch_bytes", 167772161),
            ("max_promptguard_chunks", True),
            ("fetch_concurrency", "1"),
        ],
    )
    def test_an_out_of_range_or_mistyped_block_key_refuses(
        self, key: str, value: object
    ) -> None:
        with pytest.raises(RetrieveConfigurationError):
            retrieve_settings_from_config({"retrieve": {key: value}})

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("promptguard_fail_closed_floor", "yes"),
            ("promptguard_threshold_ceiling", 1.5),
            ("promptguard_threshold_ceiling", -0.1),
            ("promptguard_threshold_ceiling", True),
            ("promptguard_wait_seconds", 0.01),
            ("promptguard_wait_seconds", 300.1),
            ("promptguard_wait_seconds", "30"),
        ],
    )
    def test_an_out_of_range_or_mistyped_top_level_key_refuses(
        self, key: str, value: object
    ) -> None:
        with pytest.raises(RetrieveConfigurationError):
            retrieve_settings_from_config({key: value})

    @pytest.mark.parametrize("sign", ["", "-"], ids=["positive", "negative"])
    def test_a_large_yaml_integer_ceiling_refuses_without_echoing_it(
        self, sign: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        literal = f"{sign}1{'0' * 400}"
        config = yaml.safe_load(f"promptguard_threshold_ceiling: {literal}\n")
        assert isinstance(config["promptguard_threshold_ceiling"], int)
        with pytest.raises(RetrieveConfigurationError) as exc:
            retrieve_settings_from_config(config)
        assert type(exc.value) is RetrieveConfigurationError
        assert str(exc.value) == (
            "promptguard_threshold_ceiling must be between 0.0 and 1.0"
        )
        assert literal not in str(exc.value)
        assert literal not in caplog.text

    def test_a_non_mapping_block_refuses(self) -> None:
        with pytest.raises(RetrieveConfigurationError):
            retrieve_settings_from_config({"retrieve": []})

    def test_the_wait_is_a_float_so_sub_second_values_are_in_range(self) -> None:
        settings = retrieve_settings_from_config({"promptguard_wait_seconds": 0.25})
        assert settings.promptguard_wait_seconds == 0.25

    def test_the_shipped_config_is_readable_and_at_its_defaults(self) -> None:
        """The shipped file stays the complete, boot-valid example."""
        config = yaml.safe_load(
            (Path(__file__).resolve().parent.parent / "config.yaml").read_text()
        )
        assert retrieve_settings_from_config(config) == RetrieveSettings()


async def test_lifespan_refuses_an_out_of_range_retrieve_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `retrieve:` bound outside its range fails the boot rather than widening."""
    monkeypatch.setattr(
        retrieval_app,
        "_load_config",
        lambda: {"retrieve": {"fetch_concurrency": 4}},
    )

    probe_app = FastAPI()
    with pytest.raises(RetrieveConfigurationError):
        async with lifespan(probe_app):
            pass


@pytest.mark.parametrize("value", ["credential-sentinel", 0, 1, None, [], {}])
async def test_lifespan_refuses_a_non_boolean_policy_floor_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    monkeypatch.setattr(
        retrieval_app, "_load_config", lambda: {"promptguard_fail_closed_floor": value}
    )
    with pytest.raises(RetrieveConfigurationError) as exc:
        async with lifespan(FastAPI()):
            pytest.fail("invalid policy started")
    assert str(exc.value) == "promptguard_fail_closed_floor must be a boolean"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("credential-sentinel", "must be a number"),
        (True, "must be a number"),
        (None, "must be a number"),
        ([], "must be a number"),
        (-0.01, "must be between 0.0 and 1.0"),
        (1.01, "must be between 0.0 and 1.0"),
        (float("nan"), "must be between 0.0 and 1.0"),
        (float("inf"), "must be between 0.0 and 1.0"),
        (-float("inf"), "must be between 0.0 and 1.0"),
    ],
)
async def test_lifespan_refuses_an_invalid_policy_ceiling_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, value: object, reason: str
) -> None:
    monkeypatch.setattr(
        retrieval_app, "_load_config", lambda: {"promptguard_threshold_ceiling": value}
    )
    with pytest.raises(RetrieveConfigurationError) as exc:
        async with lifespan(FastAPI()):
            pytest.fail("invalid policy started")
    assert str(exc.value) == f"promptguard_threshold_ceiling {reason}"


@pytest.mark.parametrize("sign", ["", "-"], ids=["positive", "negative"])
async def test_lifespan_refuses_a_large_yaml_integer_ceiling_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, sign: str, caplog: pytest.LogCaptureFixture
) -> None:
    literal = f"{sign}1{'0' * 400}"
    config = yaml.safe_load(f"promptguard_threshold_ceiling: {literal}\n")
    assert isinstance(config["promptguard_threshold_ceiling"], int)
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: config)
    with pytest.raises(RetrieveConfigurationError) as exc:
        async with lifespan(FastAPI()):
            pytest.fail("invalid policy started")
    assert type(exc.value) is RetrieveConfigurationError
    assert str(exc.value) == (
        "promptguard_threshold_ceiling must be between 0.0 and 1.0"
    )
    assert literal not in str(exc.value)
    assert literal not in caplog.text


@pytest.mark.parametrize("ceiling", [0, 0.5, 1])
async def test_lifespan_publishes_nondefault_policy_bounds(
    monkeypatch: pytest.MonkeyPatch, ceiling: float
) -> None:
    _park_the_retry(monkeypatch)
    monkeypatch.setattr(app.state, "retrieve_settings", app.state.retrieve_settings)
    monkeypatch.setattr(
        retrieval_app,
        "_load_config",
        lambda: {
            "promptguard_fail_closed_floor": True,
            "promptguard_threshold_ceiling": ceiling,
        },
    )
    async with _running_app():
        settings: RetrieveSettings = app.state.retrieve_settings
        assert settings.promptguard_fail_closed_floor is True
        assert settings.promptguard_threshold_ceiling == ceiling
        assert isinstance(settings.promptguard_threshold_ceiling, float)


@pytest.mark.parametrize(
    ("plant", "token"),
    [("mode_0755", "spool_dir_mode"), ("symlink", "spool_dir_symlink")],
)
async def test_lifespan_refuses_an_unsafe_spool_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plant: str,
    token: str,
) -> None:
    """A planted spool directory refuses the boot — refused, never repaired.

    The refusal is a ``RetrieveConfigurationError`` carrying the closed token,
    so boot refusals keep one vocabulary and no path reaches the message.
    """
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: dict[str, Any]())
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    spool = tmp_path / f"forage-spool-{os.geteuid()}"
    if plant == "mode_0755":
        spool.mkdir()
        spool.chmod(0o755)
    else:
        target = tmp_path / "planted"
        target.mkdir(mode=0o700)
        spool.symlink_to(target)

    probe_app = FastAPI()
    with pytest.raises(RetrieveConfigurationError) as exc_info:
        async with lifespan(probe_app):
            pass

    assert str(exc_info.value) == token
    assert str(tmp_path) not in str(exc_info.value)
    if plant == "mode_0755":
        assert stat.S_IMODE(os.lstat(spool).st_mode) == 0o755
    else:
        assert spool.is_symlink()


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, True),
        ({"retrieve": {"max_promptguard_chunks": 0}}, True),
        ({"retrieve": {"max_promptguard_chunks": 256}}, False),
    ],
)
async def test_lifespan_warns_exactly_once_while_the_budget_is_unset(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    config: dict[str, object],
    expected: bool,
) -> None:
    """One closed-token WARNING naming the coming default, nothing more."""
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: config)

    probe_app = FastAPI()
    with caplog.at_level(logging.WARNING, logger="retrieval_app"):
        async with lifespan(probe_app):
            pass

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "retrieve_budget_unset" in record.getMessage()
    ]
    assert warnings == (
        ["retrieve_budget_unset coming_default=256"] if expected else []
    )


async def test_the_module_level_fallback_publishes_settings_and_logs_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The lifespan-free path exists for tests, so it emits no boot warning."""
    assert isinstance(retrieval_app.app.state.retrieve_settings, RetrieveSettings)
    assert retrieval_app.app.state.retrieve_settings.max_promptguard_chunks == 0
    assert "retrieve_budget_unset" not in caplog.text


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


# ---------------------------------------------------------------------------
# Search-provider chain from the environment (`search-provider-abstraction`
# US-003)
# ---------------------------------------------------------------------------


@contextmanager
def _borrowed_search_providers(
    chain: list[SearchProvider] | None,
) -> Generator[None, None, None]:
    """Put *chain* on the module singleton and put the old value back.

    The save/`delattr`/`finally`-restore idiom (ruling 26d): these tests share
    one `app` object with every other test in the suite, and a chain left
    behind would silently serve the neighbouring `/search` tests that expect
    the real `SearxngProvider`. `None` is a real published value here — the
    module-scope sentinel — so "absent" is restored with `delattr`, not by
    assigning `None`.

    Carries `search_key_capabilities` along for the same reason (US-002):
    the lifespan always publishes the two together, so a real-lifespan test
    borrowing this context and then restoring only the chain would leak the
    capability tuple into whichever test runs next. Callers that only care
    about the chain get `()` for free while borrowed.
    """
    had_attr = hasattr(app.state, "search_providers")
    published = getattr(app.state, "search_providers", None)
    had_capabilities_attr = hasattr(app.state, "search_key_capabilities")
    published_capabilities = getattr(app.state, "search_key_capabilities", None)
    app.state.search_providers = chain
    app.state.search_key_capabilities = ()
    try:
        yield
    finally:
        if had_attr:
            app.state.search_providers = published
        else:
            delattr(app.state, "search_providers")
        if had_capabilities_attr:
            app.state.search_key_capabilities = published_capabilities
        else:
            delattr(app.state, "search_key_capabilities")


def test_the_searxng_url_default_is_the_providers_own_literal() -> None:
    """One copy of the default in the repo, read through the provider module."""
    assert retrieval_app.SEARXNG_URL == DEFAULT_SEARXNG_URL


def test_the_env_var_name_is_the_one_read_site() -> None:
    assert retrieval_app.SEARCH_PROVIDERS_ENV_VAR == "FORAGE_SEARCH_PROVIDERS"


def test_configured_provider_names_defaults_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FORAGE_SEARCH_PROVIDERS", raising=False)

    assert retrieval_app._configured_provider_names() == ["searxng"]


def test_configured_provider_names_reads_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", " SearXNG , searxng,")

    assert retrieval_app._configured_provider_names() == ["searxng"]


def test_a_set_but_blank_variable_warns_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Set-but-blank is a silent substitution unless the log says otherwise."""
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", " , ")

    with caplog.at_level(logging.WARNING, logger="retrieval_app"):
        names = retrieval_app._configured_provider_names()

    assert names == ["searxng"]
    assert "search_providers_blank" in caplog.text


def test_an_unset_variable_does_not_warn(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("FORAGE_SEARCH_PROVIDERS", raising=False)

    with caplog.at_level(logging.WARNING, logger="retrieval_app"):
        retrieval_app._configured_provider_names()

    assert "search_providers_blank" not in caplog.text


async def test_lifespan_publishes_the_resolved_chain_and_logs_its_names(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The chain objects reach `app.state`, and only their names reach the log."""
    _park_the_retry(monkeypatch)
    monkeypatch.delenv("FORAGE_SEARCH_PROVIDERS", raising=False)

    with (
        _borrowed_search_providers(None),
        caplog.at_level(logging.INFO, logger="retrieval_app"),
    ):
        async with _running_app():
            chain = cast("list[SearchProvider]", app.state.search_providers)

            assert [provider.name for provider in chain] == ["searxng"]
            assert chain[0].origin == retrieval_app.SEARXNG_URL

    assert "Search providers resolved: searxng" in caplog.text


async def test_lifespan_refuses_to_boot_on_an_unknown_provider_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown name fails the boot, and the message redacts the token.

    Driven against a throwaway `probe_app` (the `CacheConfigurationError`
    precedent above), never the module-global one: a lifespan that raises
    part-way through would otherwise leave the shared app half-configured.
    """
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,nope\nINFO: fake line")

    probe_app = FastAPI()
    with pytest.raises(SearchProviderConfigurationError) as exc_info:
        async with lifespan(probe_app):
            pass

    message = str(exc_info.value)
    assert "FORAGE_SEARCH_PROVIDERS" in message
    assert "entry 2" in message
    assert "searxng" in message
    assert "nope" not in message
    assert "fake line" not in message
    assert "\n" not in message
    assert not hasattr(probe_app.state, "search_providers")


def test_the_module_scope_sentinel_is_none_not_a_chain() -> None:
    """The attribute exists for a transport that never fires lifespan events."""
    source = Path(retrieval_app.__file__).read_text()

    assert "app.state.search_providers = None" in source


def test_the_fallback_is_a_one_element_searxng_chain() -> None:
    """With the sentinel in place, `_resolved_search_providers` is still total.

    Read through the protocol only — no `isinstance`, no `base_url` — because
    the seam is what every consumer of `app.state.search_providers` gets.
    """
    with _borrowed_search_providers(None):
        chain = retrieval_app._resolved_search_providers(app.state)

    assert len(chain) == 1
    assert chain[0].name == "searxng"
    assert chain[0].origin == "http://searxng:8080"


def test_the_fallback_returns_a_published_chain_untouched() -> None:
    fake = FakeSearchProvider(name="fake")

    with _borrowed_search_providers([fake]):
        chain = retrieval_app._resolved_search_providers(app.state)

    assert chain == [fake]


async def test_post_search_serves_through_the_published_chain(
    client: httpx.AsyncClient,
) -> None:
    """A fake chain on the lifespan-free client really serves `/search`."""
    fake = FakeSearchProvider(
        name="fake",
        outcome=ProviderSearchResult(
            provider_name="fake",
            results=[
                {
                    "title": "Fake",
                    "url": "https://example.com/fake",
                    "content": "From the fake chain.",
                    "engine": "fake-engine",
                }
            ],
            unresponsive_engines=[],
        ),
    )

    with _borrowed_search_providers([fake]):
        resp = await client.post(
            "/search",
            json={"query": "chain test", "promptguard_fail_closed": False},
        )

    assert resp.status_code == 200
    assert [query for query, _ in fake.calls] == ["chain test"]
    assert resp.json()["results"][0]["url"] == "https://example.com/fake"


async def test_a_following_search_still_sees_the_real_provider(
    client: httpx.AsyncClient,
) -> None:
    """The save/restore idiom leaves no chain behind for the next test."""
    with _borrowed_search_providers([FakeSearchProvider(name="fake")]):
        await client.post("/search", json={"query": "chain test"})

    with patch("pipeline.search_providers.searxng.httpx.AsyncClient") as client_cls:
        inner = AsyncMock()
        inner.get.side_effect = httpx.ConnectError("not available")
        inner.__aenter__ = AsyncMock(return_value=inner)
        inner.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = inner

        resp = await client.post("/search", json={"query": "after"})

    assert resp.status_code == 422
    assert resp.json()["error"] == "searxng_unavailable"


# ---------------------------------------------------------------------------
# search-fallback US-003: fallback telemetry + provenance (metadata only)
# ---------------------------------------------------------------------------


async def test_metrics_search_counts_fallback_and_paid_calls_through_the_app(
    client: httpx.AsyncClient,
) -> None:
    """The Independent Test's counters, driven through a real /search + /metrics."""
    failing = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="rate_limited", detail="http_429"
        ),
    )
    serving = FakeSearchProvider(
        name="brave",
        paid=True,
        outcome=ProviderSearchResult(
            provider_name="brave",
            results=[{"title": "R", "url": "https://example.com/1", "content": "c"}],
            unresponsive_engines=[],
        ),
    )

    with _borrowed_search_providers([failing, serving]):
        resp = await client.post(
            "/search", json={"query": "q", "promptguard_fail_closed": False}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_used"] == "brave"
    assert body["fallback_fired"] is True
    assert body["provider_errors"] == ["searxng: rate_limited"]
    assert body["results"][0]["domain"] == "example.com"

    metrics_body = (await client.get("/metrics")).json()
    assert metrics_body["search"]["fallback_fired"] == 1
    assert metrics_body["search"]["paid_calls"] == 1

    both_fail_first = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="rate_limited", detail="http_429"
        ),
    )
    both_fail_second = FakeSearchProvider(
        name="brave",
        paid=True,
        outcome=ProviderFailure(
            provider_name="brave", failure_class="timeout", detail="timeout"
        ),
    )
    with _borrowed_search_providers([both_fail_first, both_fail_second]):
        resp2 = await client.post(
            "/search", json={"query": "q", "promptguard_fail_closed": False}
        )
    assert resp2.status_code == 422

    metrics_body2 = (await client.get("/metrics")).json()
    assert metrics_body2["search"]["fallback_fired"] == 2
    assert metrics_body2["search"]["paid_calls"] == 2


async def test_a_searxng_only_chain_never_moves_either_counter(
    client: httpx.AsyncClient,
) -> None:
    fake = FakeSearchProvider(
        name="searxng",
        outcome=ProviderSearchResult(
            provider_name="searxng", results=[], unresponsive_engines=[]
        ),
    )

    with _borrowed_search_providers([fake]):
        resp = await client.post("/search", json={"query": "q"})
    assert resp.status_code == 200
    assert resp.json()["fallback_fired"] is False

    metrics_body = (await client.get("/metrics")).json()
    assert metrics_body["search"]["fallback_fired"] == 0
    assert metrics_body["search"]["paid_calls"] == 0


async def test_fallback_telemetry_is_metadata_only(
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A sentinel in the served Brave body never reaches a cache write, a log
    line, or any `/metrics` field — telemetry stays a token/bool/count/hostname.
    """
    sentinel = "SENTINEL-FALLBACK-BODY-must-never-be-cached-logged-or-metriced"
    storage = FakeStorage()
    app.state.cache = ContentCache(storage=storage)
    failing = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="rate_limited", detail="http_429"
        ),
    )
    serving = FakeSearchProvider(
        name="brave",
        paid=True,
        outcome=ProviderSearchResult(
            provider_name="brave",
            results=[
                {"title": "R", "url": "https://example.com/1", "content": sentinel}
            ],
            unresponsive_engines=[],
        ),
    )

    with (
        _borrowed_search_providers([failing, serving]),
        caplog.at_level(logging.INFO),
    ):
        resp = await client.post(
            "/search", json={"query": "q", "promptguard_fail_closed": False}
        )

    assert resp.status_code == 200
    assert sentinel in resp.json()["results"][0]["snippet"]

    assert storage.get_calls == 0
    assert storage.set_calls == 0
    assert storage.delete_calls == 0
    assert sentinel not in caplog.text

    metrics_body = (await client.get("/metrics")).json()
    assert sentinel not in json.dumps(metrics_body)


# ---------------------------------------------------------------------------
# search-policy-and-health US-010: per-request policy
# ---------------------------------------------------------------------------


def _searxng_result(**overrides: Any) -> ProviderSearchResult:
    defaults: dict[str, Any] = {
        "provider_name": "searxng",
        "results": [{"title": "R", "url": "https://example.com/1", "content": "c"}],
        "unresponsive_engines": [],
    }
    defaults.update(overrides)
    return ProviderSearchResult(**defaults)


def _domain_policy_results(
    hosts: tuple[str, ...] = (
        "a.example",
        "www.blocked.example",
        "blocked.example",
    ),
) -> ProviderSearchResult:
    return _searxng_result(
        results=[
            {
                "title": f"Result {i}",
                "url": f"https://{host}/article",
                "content": f"Calm search excerpt {i}.",
                "engine": "google",
            }
            for i, host in enumerate(hosts, start=1)
        ]
    )


def test_search_blocked_domains_has_no_validation_constraints() -> None:
    first = SearchRequest(query="q")
    second = SearchRequest(query="q")
    assert first.blocked_domains == []
    first.blocked_domains.append("anything")
    assert second.blocked_domains == []
    assert SearchRequest.model_fields["blocked_domains"].metadata == []
    schema = SearchRequest.model_json_schema()["properties"]["blocked_domains"]
    assert schema["items"] == {"type": "string"}
    assert not {"maxItems", "minItems", "pattern"} & schema.keys()
    assert "request.blocked_domains" not in inspect.getsource(
        orchestrator.run_search_pipeline
    )
    source = inspect.getsource(contract)
    doc = source.split("SearchErrorCode = Literal[", 1)[1].split(
        "SEARCH_ERROR_CODES =", 1
    )[0]
    assert all(
        text in doc
        for text in (
            "policy_excluded_all_providers",
            "policy_domain_list_too_large",
            "permanent client errors",
            "retryable",
        )
    )


async def test_search_without_domain_policy_matches_pre_story_baseline(
    client: httpx.AsyncClient,
) -> None:
    classifier = MagicMock(spec=PromptGuardClassifier)
    classifier.loaded = True
    classifier.classify.return_value = (0.1, [])
    app.state.classifier = classifier
    free = FakeSearchProvider(name="searxng", outcome=_domain_policy_results())
    paid = FakeSearchProvider(name="brave", paid=True)
    with (
        _borrowed_search_providers([free, paid]),
        patch(
            "pipeline.orchestrator.run_promptguard", wraps=orchestrator.run_promptguard
        ) as pg,
    ):
        response = await client.post(
            "/search", json={"query": "domain policy baseline", "num_results": 3}
        )
    assert response.status_code == 200
    baseline = json.loads(
        (
            Path(__file__).parent / "fixtures/search/baseline_pre_blocked_domains.json"
        ).read_text()
    )
    assert set(baseline) == {
        "results",
        "omitted_by_reason",
        "fallback_fired",
        "provider_used",
        "provider_errors",
    }
    assert {key: response.json()[key] for key in baseline} == baseline
    assert paid.calls == []
    assert classifier.classify.call_count == 3
    assert all(call.kwargs["threshold"] == 0.85 for call in pg.call_args_list)


@pytest.mark.parametrize("all_blocked", [False, True])
async def test_search_domain_policy_omits_before_scans_without_paid_fallback(
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
    all_blocked: bool,
) -> None:
    free = FakeSearchProvider(name="searxng", outcome=_domain_policy_results())
    paid = FakeSearchProvider(name="brave", paid=True)
    entries = ["blocked.example"] + (["a.example"] if all_blocked else [])
    with (
        _borrowed_search_providers([free, paid]),
        caplog.at_level(logging.INFO, logger="pipeline.orchestrator"),
        patch(
            "pipeline.orchestrator.scan_structural", wraps=orchestrator.scan_structural
        ) as scan,
        patch(
            "pipeline.orchestrator.run_promptguard", wraps=orchestrator.run_promptguard
        ) as pg,
    ):
        response = await client.post(
            "/search",
            json={
                "query": "q",
                "num_results": 3,
                "blocked_domains": entries,
                "promptguard_fail_closed": False,
            },
        )
    assert response.status_code == 200
    data = response.json()
    count = 3 if all_blocked else 2
    assert [result["domain"] for result in data["results"]] == (
        [] if all_blocked else ["a.example"]
    )
    assert data["omitted_by_reason"] == {contract.OMIT_BLOCKED_URL: count}
    assert data["fallback_fired"] is False
    assert data["provider_errors"] == []
    assert paid.calls == []
    assert pg.await_count == (0 if all_blocked else 1)
    assert scan.call_count == (0 if all_blocked else 6)
    for call in [*scan.call_args_list, *pg.call_args_list]:
        assert "blocked.example" not in call.args[0]
        assert "Result 2" not in call.args[0]
        assert "excerpt 2" not in call.args[0]
    records = [
        r for r in caplog.records if r.getMessage().startswith("search_url_blocked")
    ]
    assert len(records) == count
    for record in records:
        assert record.levelno == logging.INFO
        assert record.getMessage().startswith(
            "search_url_blocked host_class=policy_blocklist provider=searxng"
        )
        assert "example" not in record.getMessage()
        assert "https://" not in record.getMessage()
    counters = (await client.get("/metrics")).json()["search"]
    assert counters["omitted_by_reason"] == {contract.OMIT_BLOCKED_URL: count}
    assert counters["paid_calls"] == counters["fallback_fired"] == 0


async def test_search_domain_policy_accepts_70_entries_and_single_label_exact_only(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    entries = [" BLOCKED.example. ", "com", "bad..entry"] + [
        f"other{i}.example" for i in range(67)
    ]
    free = FakeSearchProvider(
        name="searxng",
        outcome=_domain_policy_results(
            (
                "www.blocked.example",
                "blocked.example",
                "com",
                "a.com",
                "notblocked.example",
            )
        ),
    )
    with _borrowed_search_providers([free]), caplog.at_level(logging.INFO):
        response = await client.post(
            "/search",
            json={
                "query": "q",
                "num_results": 5,
                "blocked_domains": entries,
                "promptguard_fail_closed": False,
            },
        )
    assert response.status_code == 200
    assert [result["domain"] for result in response.json()["results"]] == [
        "a.com",
        "notblocked.example",
    ]
    assert response.json()["omitted_by_reason"] == {contract.OMIT_BLOCKED_URL: 3}
    counters = (await client.get("/metrics")).json()["search"]
    assert counters["policy_invalid_domain_entry"] == 1
    assert counters["policy_suffix_trusted_skip"] == 0
    assert "bad..entry" not in caplog.text
    assert "bad..entry" not in response.text


async def test_search_domain_policy_seed_normalized_at_boot_and_cannot_be_evicted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _park_the_retry(monkeypatch)
    monkeypatch.setattr(
        retrieval_app,
        "_load_config",
        lambda: {"seed_blocklist": [" Blocked.Example. "]},
    )
    free = FakeSearchProvider(name="searxng", outcome=_domain_policy_results())
    paid = FakeSearchProvider(name="brave", paid=True)
    async with _running_app() as live:
        assert app.state.config["seed_blocklist"] == ["blocked.example"]
        with (
            _borrowed_search_providers([free, paid]),
            caplog.at_level(logging.INFO, logger="pipeline.orchestrator"),
            patch(
                "pipeline.orchestrator.hostname_matches",
                wraps=url_validator.hostname_matches,
            ) as match,
        ):
            for entries in (None, [f"caller{i}.example" for i in range(500)]):
                body: dict[str, Any] = {
                    "query": "q",
                    "num_results": 3,
                    "promptguard_fail_closed": False,
                }
                if entries is not None:
                    body["blocked_domains"] = entries
                response = await live.post("/search", json=body)
                assert response.status_code == 200
                assert [r["domain"] for r in response.json()["results"]] == [
                    "a.example"
                ]
                assert response.json()["omitted_by_reason"] == {
                    contract.OMIT_BLOCKED_URL: 2
                }
                assert response.json()["fallback_fired"] is False
        assert all(
            call.args[1] == "blocked.example"
            for call in match.call_args_list
            if call.args[0] in {"www.blocked.example", "blocked.example"}
        )
        assert app.state.config["seed_blocklist"] == ["blocked.example"]
        assert app.state.search_metrics.policy_invalid_domain_entry == 0
    assert paid.calls == []
    assert (
        sum(
            record.getMessage().startswith(
                "search_url_blocked host_class=policy_blocklist provider="
            )
            for record in caplog.records
        )
        == 4
    )


@pytest.mark.parametrize(
    "entries",
    [
        ["blocked.example", "x" * 4096],
        ["blocked.example", "é" * 2048],
        ["blocked.example", "\ud800" * 1366],
        ["blocked.example".ljust(4093), "\ud800"],
    ],
)
async def test_search_domain_policy_over_budget_refuses_before_encoding(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    entries: list[str],
) -> None:
    monkeypatch.setattr(app.state, "policy_domain_entries_max_bytes", 4096)
    monkeypatch.setitem(app.state.config, "seed_blocklist", ["blocked.example"])
    free = FakeSearchProvider(name="searxng", outcome=_domain_policy_results())
    with (
        _borrowed_search_providers([free]),
        patch(
            "retrieval_app.normalize_domain_entries",
            side_effect=AssertionError("must not normalize"),
        ) as normalize,
        patch(
            "url_validator.canonicalize_host",
            side_effect=AssertionError("must not encode"),
        ),
        caplog.at_level(logging.INFO),
    ):
        response = await client.post(
            "/search",
            content=json.dumps({"query": "q", "blocked_domains": entries}),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 422
    data = response.json()
    assert set(data) == {"error", "reason", "request_id"}
    assert data["error"] == "search_unavailable"
    assert data["reason"] == contract.POLICY_DOMAIN_LIST_TOO_LARGE
    assert data["request_id"]
    assert free.calls == []
    normalize.assert_not_called()
    counters = (await client.get("/metrics")).json()["search"]
    assert counters["requests"] == 1
    assert counters["errors"] == {"search_unavailable": 1}
    assert counters["policy_invalid_domain_entry"] == 0
    assert counters["omitted_by_reason"] == {}
    assert "blocked.example" not in response.text + caplog.text
    assert entries[1] not in response.text + caplog.text


async def test_search_domain_policy_exact_byte_budget_and_malformed_surrogate(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = ["blocked.example".ljust(4092), "\ud800"]
    budget = url_validator.domain_list_bytes(entries)
    assert budget == 4096
    monkeypatch.setattr(app.state, "policy_domain_entries_max_bytes", budget)
    free = FakeSearchProvider(name="searxng", outcome=_domain_policy_results())
    with _borrowed_search_providers([free]):
        response = await client.post(
            "/search",
            content=json.dumps(
                {
                    "query": "q",
                    "num_results": 3,
                    "blocked_domains": entries,
                    "promptguard_fail_closed": False,
                }
            ),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 200
    assert response.json()["omitted_by_reason"] == {contract.OMIT_BLOCKED_URL: 2}
    assert app.state.search_metrics.policy_invalid_domain_entry == 1


async def test_search_domain_policy_encodes_entries_once_and_hosts_once(
    client: httpx.AsyncClient,
) -> None:
    entries = [" STRAẞE.de. ", ".blocked.example"]
    hosts = ("straße.de", "xn--strae-oqa.de", "www.blocked.example", "a.example")
    free = FakeSearchProvider(name="searxng", outcome=_domain_policy_results(hosts))
    with (
        _borrowed_search_providers([free]),
        patch(
            "retrieval_app.normalize_domain_entries",
            wraps=url_validator.normalize_domain_entries,
        ) as normalize,
        patch(
            "url_validator.canonicalize_host", wraps=url_validator.canonicalize_host
        ) as entry_host,
        patch(
            "pipeline.orchestrator.canonicalize_host",
            wraps=orchestrator.canonicalize_host,
        ) as result_host,
        patch(
            "retrieval_app.run_search_pipeline", wraps=orchestrator.run_search_pipeline
        ) as pipeline,
    ):
        response = await client.post(
            "/search",
            json={
                "query": "q",
                "num_results": 4,
                "blocked_domains": entries,
                "promptguard_fail_closed": False,
            },
        )
    assert response.status_code == 200
    assert [r["domain"] for r in response.json()["results"]] == ["a.example"]
    assert response.json()["omitted_by_reason"] == {contract.OMIT_BLOCKED_URL: 3}
    normalize.assert_called_once_with(entries, denylist=True, budget_bytes=None)
    assert entry_host.call_count == len(entries)
    assert result_host.call_count == len(hosts)
    assert pipeline.call_args.kwargs["blocked_domains"] == [
        "xn--strae-oqa.de",
        "blocked.example",
    ]


async def test_search_domain_policy_pipeline_parameter_is_the_only_channel() -> None:
    request = SearchRequest(
        query="q",
        num_results=3,
        blocked_domains=["a.example"],
        promptguard_fail_closed=False,
    )
    response = await orchestrator.run_search_pipeline(
        request,
        config={},
        blocked_domains=["blocked.example"],
        providers=[
            FakeSearchProvider(name="searxng", outcome=_domain_policy_results())
        ],
    )
    assert [result.domain for result in response.results] == ["a.example"]
    assert response.omitted_by_reason == {contract.OMIT_BLOCKED_URL: 2}


async def test_search_domain_policy_runs_after_private_host_audit(
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    free = FakeSearchProvider(
        name="searxng", outcome=_domain_policy_results(("localhost",))
    )
    with _borrowed_search_providers([free]), caplog.at_level(logging.INFO):
        response = await client.post(
            "/search",
            json={
                "query": "q",
                "blocked_domains": ["localhost"],
            },
        )
    assert response.status_code == 200
    assert response.json()["omitted_by_reason"] == {contract.OMIT_BLOCKED_URL: 1}
    assert "search_url_blocked host_class=blocklisted_name provider=" in caplog.text
    assert "policy_blocklist" not in caplog.text


async def test_omitted_policy_params_traverse_the_configured_chain(
    client: httpx.AsyncClient,
) -> None:
    """Baseline: no `providers`/`allow_paid_fallback` behaves as spec 3 did."""
    searxng = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="rate_limited", detail="http_429"
        ),
    )
    brave = FakeSearchProvider(
        name="brave", paid=True, outcome=_searxng_result(provider_name="brave")
    )

    with _borrowed_search_providers([searxng, brave]):
        resp = await client.post(
            "/search", json={"query": "q", "promptguard_fail_closed": False}
        )

    assert resp.status_code == 200
    assert resp.json()["provider_used"] == "brave"
    assert resp.json()["fallback_fired"] is True
    assert len(searxng.calls) == 1
    assert len(brave.calls) == 1


async def test_providers_naming_searxng_never_calls_brave(
    client: httpx.AsyncClient,
) -> None:
    searxng = FakeSearchProvider(name="searxng", outcome=_searxng_result())
    brave = FakeSearchProvider(name="brave", paid=True)

    with _borrowed_search_providers([searxng, brave]):
        resp = await client.post(
            "/search",
            json={
                "query": "q",
                "promptguard_fail_closed": False,
                "providers": ["searxng"],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["provider_used"] == "searxng"
    assert brave.calls == []


async def test_providers_naming_brave_keeps_the_full_effective_chain(
    client: httpx.AsyncClient,
) -> None:
    """`providers: ["brave"]` restricts nothing — the free provider still runs."""
    searxng = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="rate_limited", detail="http_429"
        ),
    )
    brave = FakeSearchProvider(
        name="brave", paid=True, outcome=_searxng_result(provider_name="brave")
    )

    with _borrowed_search_providers([searxng, brave]):
        resp = await client.post(
            "/search",
            json={
                "query": "q",
                "promptguard_fail_closed": False,
                "providers": ["brave"],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["provider_used"] == "brave"
    assert len(searxng.calls) == 1
    assert len(brave.calls) == 1


async def test_providers_naming_an_unknown_provider_leaves_only_searxng(
    client: httpx.AsyncClient,
) -> None:
    """`providers: ["tavily"]` excludes brave -- proven by a failing SearXNG.

    A succeeding SearXNG would prove nothing here: free-first traversal
    stops at the first success either way, with or without brave in the
    effective chain. Failing SearXNG forces the exhausted-chain path, so
    `brave.calls == []` can only mean the policy actually removed it.
    """
    searxng = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="rate_limited", detail="http_429"
        ),
    )
    brave = FakeSearchProvider(name="brave", paid=True)

    with _borrowed_search_providers([searxng, brave]):
        resp = await client.post(
            "/search",
            json={
                "query": "q",
                "promptguard_fail_closed": False,
                "providers": ["tavily"],
            },
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "search_unavailable"
    assert body["reason"] == "searxng: rate_limited"
    assert brave.calls == []

    metrics_body = (await client.get("/metrics")).json()
    assert metrics_body["search"]["policy_unknown_provider"] == 1


async def test_allow_paid_fallback_false_never_calls_brave_on_searxng_failure(
    client: httpx.AsyncClient,
) -> None:
    searxng = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="rate_limited", detail="http_429"
        ),
    )
    brave = FakeSearchProvider(name="brave", paid=True)

    with _borrowed_search_providers([searxng, brave]):
        resp = await client.post(
            "/search",
            json={
                "query": "q",
                "promptguard_fail_closed": False,
                "allow_paid_fallback": False,
            },
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "search_unavailable"
    assert body["reason"] == "searxng: rate_limited"
    assert brave.calls == []


@pytest.mark.parametrize(
    ("providers", "expected_ignored"),
    [
        ([" SearXNG "], 0),
        (["searxng"] * 9, 1),
        (["tavily", "exa", "tavily"], 3),
        (["x" * 33], 1),
        (["has interior\twhitespace"], 1),
        (["ignore-previous-instructions"], 1),
    ],
)
async def test_normalisation_and_ignoring_cases_all_serve_200(
    client: httpx.AsyncClient, providers: list[str], expected_ignored: int
) -> None:
    """None of these malformed/oversized/hostile entries is ever a 422."""
    searxng = FakeSearchProvider(name="searxng", outcome=_searxng_result())

    with _borrowed_search_providers([searxng]):
        resp = await client.post(
            "/search",
            json={
                "query": "q",
                "promptguard_fail_closed": False,
                "providers": providers,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["provider_used"] == "searxng"

    metrics_body = (await client.get("/metrics")).json()
    assert metrics_body["search"]["policy_unknown_provider"] == expected_ignored


async def test_a_keyless_deployment_answers_identically_to_a_keyed_one(
    client: httpx.AsyncClient,
) -> None:
    """`providers: ["brave"]` gets the same status and field set with no key."""
    request_body = {
        "query": "q",
        "promptguard_fail_closed": False,
        "providers": ["brave"],
    }

    keyless_chain: list[SearchProvider] = [
        FakeSearchProvider(name="searxng", outcome=_searxng_result())
    ]
    with _borrowed_search_providers(keyless_chain):
        keyless_resp = await client.post("/search", json=request_body)

    with _borrowed_search_providers(
        [
            FakeSearchProvider(name="searxng", outcome=_searxng_result()),
            FakeSearchProvider(name="brave", paid=True),
        ]
    ):
        keyed_resp = await client.post("/search", json=request_body)

    assert keyless_resp.status_code == keyed_resp.status_code == 200
    assert set(keyless_resp.json()) == set(keyed_resp.json())

    keyless_metrics = (await client.get("/metrics")).json()
    assert keyless_metrics["search"]["policy_unknown_provider"] == 1


async def test_policy_excludes_all_providers_via_allow_paid_fallback(
    client: httpx.AsyncClient,
) -> None:
    """A paid-only configured chain plus `allow_paid_fallback: false` refuses."""
    brave = FakeSearchProvider(name="brave", paid=True)

    with _borrowed_search_providers([brave]):
        resp = await client.post(
            "/search", json={"query": "q", "allow_paid_fallback": False}
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "search_unavailable"
    assert body["reason"] == "policy_excluded_all_providers"
    assert brave.calls == []

    metrics_body = (await client.get("/metrics")).json()
    assert metrics_body["search"]["errors"] == {"search_unavailable": 1}


async def test_policy_excludes_all_providers_via_an_unregistered_name(
    client: httpx.AsyncClient,
) -> None:
    """A paid-only configured chain plus a `providers` naming nothing refuses."""
    brave = FakeSearchProvider(name="brave", paid=True)

    with _borrowed_search_providers([brave]):
        resp = await client.post(
            "/search", json={"query": "q", "providers": ["tavily"]}
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "search_unavailable"
    assert body["reason"] == "policy_excluded_all_providers"
    assert brave.calls == []

    metrics_body = (await client.get("/metrics")).json()
    assert metrics_body["search"]["errors"] == {"search_unavailable": 1}


async def test_exhaustion_code_follows_the_configured_not_the_effective_chain(
    client: httpx.AsyncClient,
) -> None:
    """`providers: ["searxng"]` on a two-provider chain yields the general code."""
    searxng = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="rate_limited", detail="http_429"
        ),
    )
    brave = FakeSearchProvider(name="brave", paid=True)

    with _borrowed_search_providers([searxng, brave]):
        resp = await client.post(
            "/search",
            json={
                "query": "q",
                "promptguard_fail_closed": False,
                "providers": ["searxng"],
            },
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "search_unavailable"
    assert body["reason"] == "searxng: rate_limited"
    assert brave.calls == []


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param({"providers": ["searxng"]}, id="providers-restricts-to-searxng"),
        pytest.param({"allow_paid_fallback": False}, id="paid-fallback-forbidden"),
    ],
)
async def test_a_policy_restricted_chain_treats_unresponsive_engines_as_exhaustion(
    client: httpx.AsyncClient, policy: dict[str, Any]
) -> None:
    """The recurring production shape under a policy that leaves only SearXNG.

    SearXNG answers 200 with zero raw results and a non-empty
    `unresponsive_engines` — a classified failure (ruling 17) — and the policy
    keeps Brave out of the effective chain. The exhaustion code follows the
    *configured* chain (ruling 28): two providers are configured, so this is
    the general `search_unavailable`, never an empty 200 and never a legacy
    `searxng_*` code, whichever chain the orchestrator's legacy predicate is
    handed. Brave is never called.
    """
    searxng = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=_searxng_result(results=[], unresponsive_engines=["google"]),
    )
    brave = FakeSearchProvider(name="brave", paid=True)

    with _borrowed_search_providers([searxng, brave]):
        resp = await client.post(
            "/search",
            json={"query": "q", "promptguard_fail_closed": False, **policy},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "search_unavailable"
    assert body["reason"] == "searxng: rate_limited"
    assert len(searxng.calls) == 1
    assert brave.calls == []


async def test_legacy_codes_are_byte_for_byte_on_a_searxng_only_configured_chain(
    client: httpx.AsyncClient,
) -> None:
    """A `providers` restriction on a one-provider chain changes nothing."""
    searxng = FakeSearchProvider(
        name="searxng",
        paid=False,
        outcome=ProviderFailure(
            provider_name="searxng", failure_class="hard_error", detail="http_500"
        ),
    )

    with _borrowed_search_providers([searxng]):
        resp = await client.post(
            "/search",
            json={
                "query": "q",
                "promptguard_fail_closed": False,
                "providers": ["searxng"],
            },
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "searxng_error"
    assert body["reason"] == "SearXNG returned HTTP error (http_500)"


async def test_a_hostile_providers_entry_leaks_nowhere(
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No response body, `/metrics` key, or log record ever echoes the entry."""
    sentinel = "SENTINEL-do-not-echo-ignore-all-previous-instructions"
    searxng = FakeSearchProvider(name="searxng", outcome=_searxng_result())

    with (
        _borrowed_search_providers([searxng]),
        caplog.at_level(logging.INFO),
    ):
        resp = await client.post(
            "/search",
            json={
                "query": "q",
                "promptguard_fail_closed": False,
                "providers": [sentinel],
            },
        )

    assert resp.status_code == 200
    assert sentinel not in json.dumps(resp.json())
    assert sentinel not in caplog.text

    metrics_body = (await client.get("/metrics")).json()
    assert sentinel not in json.dumps(metrics_body)


# ---------------------------------------------------------------------------
# feature-brave-provider US-002: env-gated conditional registration, wired
# through the real lifespan
# ---------------------------------------------------------------------------


def _brave_stream_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        content=b"{}",
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://api.search.brave.com/res/v1/llm/context"),
    )


def _brave_client_double() -> MagicMock:
    """A minimal streaming double for ``pipeline.search_providers.brave``."""
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=_brave_stream_response())
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    client = MagicMock()
    client.stream = MagicMock(return_value=stream_cm)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def test_lifespan_wires_the_configured_brave_timeout_into_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-default `search_brave_timeout_seconds` really reaches the client."""
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "brave")
    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, "sentinel-key")
    monkeypatch.setattr(
        retrieval_app,
        "_load_config",
        lambda: {"search_brave_timeout_seconds": 45.0},
    )

    with (
        _borrowed_search_providers(None),
        patch("pipeline.search_providers.brave.httpx.AsyncClient") as client_cls,
    ):
        client_cls.return_value = _brave_client_double()

        async with _running_app():
            chain = cast("list[SearchProvider]", app.state.search_providers)
            assert [provider.name for provider in chain] == ["brave"]
            outcome = await chain[0].search("q", 3)

    assert isinstance(outcome, ProviderSearchResult)
    assert client_cls.call_args.kwargs["timeout"] == 45.0


async def test_a_key_set_after_startup_does_not_change_the_resolved_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The variable is read once, in the lifespan — never again while running."""
    _park_the_retry(monkeypatch)
    monkeypatch.delenv("FORAGE_SEARCH_PROVIDERS", raising=False)
    monkeypatch.delenv(BRAVE_API_KEY_ENV_VAR, raising=False)

    with _borrowed_search_providers(None):
        async with _running_app():
            before = app.state.search_providers
            assert [provider.name for provider in before] == ["searxng"]

            monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, "sentinel-key")
            monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,brave")

            after = retrieval_app._resolved_search_providers(app.state)
            assert after is before
            assert [provider.name for provider in after] == ["searxng"]


async def test_lifespan_logs_exactly_one_skip_warning_for_a_keyless_brave_entry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,brave")
    monkeypatch.delenv(BRAVE_API_KEY_ENV_VAR, raising=False)

    with (
        _borrowed_search_providers(None),
        caplog.at_level(logging.WARNING, logger="pipeline.search_providers"),
    ):
        async with _running_app():
            chain = cast("list[SearchProvider]", app.state.search_providers)
            assert [provider.name for provider in chain] == ["searxng"]

    matching = [
        record
        for record in caplog.records
        if "brave_skipped_missing_key" in record.message
    ]
    assert len(matching) == 1


async def test_lifespan_never_leaks_an_invalid_brave_key_into_the_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unusable key is named by variable, never by value — even at boot."""
    sentinel = "café-invalid-sentinel-key"
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,brave")
    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, sentinel)

    with (
        _borrowed_search_providers(None),
        caplog.at_level(logging.WARNING, logger="retrieval_app"),
    ):
        async with _running_app():
            chain = cast("list[SearchProvider]", app.state.search_providers)
            assert [provider.name for provider in chain] == ["searxng"]

    assert "brave_key_invalid" in caplog.text
    assert sentinel not in caplog.text


# ---------------------------------------------------------------------------
# search-policy-and-health US-002: `/health` provider status
# ---------------------------------------------------------------------------


async def test_health_reports_the_chained_provider_and_the_present_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Key present and chained: `search_providers` and the capability both show it."""
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,brave")
    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, "sentinel-brave-key")

    with _borrowed_search_providers(None):
        async with _running_app() as client:
            data = (await client.get("/health")).json()

    assert data["search_providers"] == ["searxng", "brave"]
    assert data["capabilities"]["brave_api_key"] == 1


async def test_health_reports_no_key_and_the_default_chain_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Key absent: `search_providers` is the key-less floor, no capability entry."""
    _park_the_retry(monkeypatch)
    monkeypatch.delenv("FORAGE_SEARCH_PROVIDERS", raising=False)
    monkeypatch.delenv(BRAVE_API_KEY_ENV_VAR, raising=False)

    with _borrowed_search_providers(None):
        async with _running_app() as client:
            data = (await client.get("/health")).json()

    assert data["search_providers"] == ["searxng"]
    assert "brave_api_key" not in data["capabilities"]


@pytest.mark.parametrize(
    "key_value",
    ["", "   ", "café-invalid-\x01-sentinel"],
    ids=["empty", "whitespace-only", "control-character"],
)
async def test_health_reports_no_key_for_every_absent_shaped_value(
    monkeypatch: pytest.MonkeyPatch,
    key_value: str,
) -> None:
    """Empty, whitespace-only, and `brave_key_invalid` values all report absent —
    advertisement and registration come from the same evaluation."""
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,brave")
    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, key_value)

    with _borrowed_search_providers(None):
        async with _running_app() as client:
            data = (await client.get("/health")).json()

    assert data["search_providers"] == ["searxng"]
    assert "brave_api_key" not in data["capabilities"]


async def test_health_reports_the_present_key_even_when_not_chained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyed but not chained: the capability names a key `search_providers` doesn't use.

    Together the two fields show "keyed but not chained".
    """
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng")
    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, "sentinel-brave-key")

    with _borrowed_search_providers(None):
        async with _running_app() as client:
            data = (await client.get("/health")).json()

    assert data["search_providers"] == ["searxng"]
    assert data["capabilities"]["brave_api_key"] == 1


async def test_health_capabilities_with_a_key_and_no_promptguard_is_key_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present key with PromptGuard unloaded advertises only the key capability."""
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "brave")
    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, "sentinel-brave-key")

    with _borrowed_search_providers(None):
        async with _running_app() as client:
            data = (await client.get("/health")).json()

    assert data["capabilities"] == {"brave_api_key": 1}


async def test_health_capabilities_with_break_glass_and_no_key_is_sanitization_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break-glass forces only `search_sanitization`; it never touches the key entry."""
    _clear_break_glass_env(monkeypatch)
    monkeypatch.setenv("FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION", "1")
    _park_the_retry(monkeypatch)
    monkeypatch.delenv("FORAGE_SEARCH_PROVIDERS", raising=False)
    monkeypatch.delenv(BRAVE_API_KEY_ENV_VAR, raising=False)

    with _borrowed_search_providers(None):
        async with _running_app() as client:
            data = (await client.get("/health")).json()

    assert data["capabilities"] == {"search_sanitization": 1}


async def test_health_never_echoes_the_key_value_in_body_or_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "sentinel-do-not-echo-brave-key"
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,brave")
    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, sentinel)

    with (
        _borrowed_search_providers(None),
        caplog.at_level(logging.INFO),
    ):
        async with _running_app() as client:
            resp = await client.get("/health")

    assert sentinel not in json.dumps(resp.json())
    assert sentinel not in caplog.text


async def test_the_pairing_of_chain_membership_and_capability_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`'brave' in search_providers` and `'brave_api_key' in capabilities` agree.

    Both come from the lifespan's single `brave_key_present()` verdict
    (US-002's "one evaluation" rule), so they can never disagree — checked
    with the key present and, separately, absent.
    """
    _park_the_retry(monkeypatch)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,brave")

    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, "sentinel-brave-key")
    with _borrowed_search_providers(None):
        async with _running_app() as client:
            present = (await client.get("/health")).json()
    assert ("brave" in present["search_providers"]) == (
        "brave_api_key" in present["capabilities"]
    )
    assert "brave" in present["search_providers"]

    monkeypatch.delenv(BRAVE_API_KEY_ENV_VAR, raising=False)
    with _borrowed_search_providers(None):
        async with _running_app() as client:
            absent = (await client.get("/health")).json()
    assert ("brave" in absent["search_providers"]) == (
        "brave_api_key" in absent["capabilities"]
    )
    assert "brave" not in absent["search_providers"]


async def test_health_without_a_lifespan_reports_the_default_chain_and_no_key(
    client: httpx.AsyncClient,
) -> None:
    """A transport that never ran the lifespan still answers with the fallback.

    Production-unreachable — the lifespan publishes both fields before it
    yields — but the suite's `client` fixture builds its `ASGITransport` with
    no lifespan event, so this is exercised directly. Saves, `delattr`s and
    restores both attributes on the idiom
    `test_health_without_a_cache_still_names_a_backend_and_never_500s` uses
    for `cache_backend`, so this passes when run after a real-lifespan test
    in the same session.
    """
    published_providers = getattr(app.state, "search_providers", None)
    if published_providers is not None:
        delattr(app.state, "search_providers")
    published_capabilities = getattr(app.state, "search_key_capabilities", None)
    if published_capabilities is not None:
        delattr(app.state, "search_key_capabilities")
    try:
        resp = await client.get("/health")
    finally:
        if published_providers is not None:
            app.state.search_providers = published_providers
        if published_capabilities is not None:
            app.state.search_key_capabilities = published_capabilities

    assert resp.status_code == 200
    data = resp.json()
    assert data["search_providers"] == ["searxng"]
    assert "brave_api_key" not in data["capabilities"]


async def test_health_unrelated_fields_are_unaffected_by_search_provider_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`status`, `degraded_reasons`, `promptguard_loaded`, `cache_connected`, and
    `cache_backend` are computed exactly as before, regardless of the search
    provider chain or key presence."""
    _park_the_retry(monkeypatch)
    monkeypatch.delenv("VALKEY_URL", raising=False)
    monkeypatch.setenv("FORAGE_SEARCH_PROVIDERS", "searxng,brave")
    monkeypatch.setenv(BRAVE_API_KEY_ENV_VAR, "sentinel-brave-key")

    with _borrowed_search_providers(None):
        async with _running_app() as client:
            data = (await client.get("/health")).json()

    assert data["status"] == "degraded"
    assert data["degraded_reasons"] == ["promptguard_unavailable"]
    assert data["promptguard_loaded"] is False
    assert data["cache_connected"] is True
    assert data["cache_backend"] == "memory"
