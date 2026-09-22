"""Handler-resolved operator policy (`hardening-retrieve-parity` US-005)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from cache import ContentCache, cache_policy_fingerprint
from models import (
    ExtractedContent,
    RetrievedContent,
    RetrieveRequest,
    SearchRequest,
    SearchResponse,
    TrustTier,
)
from pipeline.extraction_limits import extraction_settings_from_config
from pipeline.orchestrator import PipelineError
from pipeline.retrieve_limits import RetrieveSettings
from pipeline.search_providers.base import ProviderSearchResult
from pipeline.stage5_url_audit import FetchResult
from promptguard.classifier import PromptGuardClassifier
from retrieval_app import (
    ExtractionAdmissionController,
    ExtractionMetrics,
    RetrieveMetrics,
    SearchMetrics,
    _promptguard_policy_updates,
    app,
)
from tests.fakes import FakeSearchProvider, FakeStorage

_URL = "https://example.com/article"
_PAGE = b"<html><body><p>A calm article about gardening.</p></body></html>"


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    settings = RetrieveSettings(
        promptguard_fail_closed_floor=True,
        promptguard_threshold_ceiling=0.5,
        promptguard_wait_seconds=0.05,
    )
    config = {"extract_route_enabled": True, "promptguard_threshold": 0.95}
    extraction_settings = extraction_settings_from_config(config)
    metrics = RetrieveMetrics()
    extraction_metrics = ExtractionMetrics()
    admission = ExtractionAdmissionController.from_retrieve_settings(settings, metrics)
    state: dict[str, object] = {
        "config": config,
        "retrieve_settings": settings,
        "policy_domain_entries_max_bytes": 65536,
        "extraction_settings": extraction_settings,
        "retrieve_metrics": metrics,
        "extraction_metrics": extraction_metrics,
        "search_metrics": SearchMetrics(),
        "cache": None,
        "classifier": None,
        "sanitizer_revision": "policy-test-revision",
        "retrieve_admission": admission,
        "extraction_admission": ExtractionAdmissionController(
            extraction_settings, extraction_metrics
        ),
        "classification_semaphore": asyncio.Semaphore(1),
        "search_providers": [
            FakeSearchProvider(
                name="searxng",
                outcome=ProviderSearchResult(
                    provider_name="searxng",
                    unresponsive_engines=[],
                    results=[
                        {
                            "title": "Gardening",
                            "url": _URL,
                            "snippet": "A calm article.",
                        }
                    ],
                ),
            )
        ],
    }
    for name, value in state.items():
        monkeypatch.setattr(app.state, name, value, raising=False)
    with (
        patch(
            "pipeline.orchestrator.validate_url",
            return_value=("93.184.216.34", "example.com"),
        ),
        patch(
            "pipeline.orchestrator.fetch_url",
            return_value=FetchResult(
                final_url=_URL,
                content_type="text/html",
                response_body=_PAGE,
                status_code=200,
            ),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as session:
            yield session
    assert (admission.active, admission.queued, admission.queued_bytes) == (0, 0, 0)


def _classifier(score: float) -> MagicMock:
    classifier = MagicMock(spec=PromptGuardClassifier)
    classifier.loaded = True
    classifier.classify.return_value = (score, [])
    return classifier


@pytest.mark.parametrize("floor", [False, True])
@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("ceiling", [0.0, 0.5, 1.0])
@pytest.mark.parametrize("threshold", [0.0, 0.25, 0.85, 1.0])
def test_policy_copies_only_declared_fields_without_mutating_the_caller(
    floor: bool, flag: bool, ceiling: float, threshold: float
) -> None:
    settings = RetrieveSettings(
        promptguard_fail_closed_floor=floor,
        promptguard_threshold_ceiling=ceiling,
    )
    retrieve = RetrieveRequest(
        url=_URL, promptguard_fail_closed=flag, promptguard_threshold=threshold
    )
    search = SearchRequest(query="gardening", promptguard_fail_closed=flag)
    for body in (retrieve, search):
        original = body.model_dump()
        updates = _promptguard_policy_updates(body, settings)
        effective = body.model_copy(update=updates)
        expected: dict[str, bool | float] = {"promptguard_fail_closed": flag or floor}
        if isinstance(body, RetrieveRequest):
            expected["promptguard_threshold"] = min(threshold, ceiling)
        assert updates == expected
        assert expected.keys() <= type(body).model_fields.keys()
        assert effective.model_dump() == original | expected
        assert body.model_dump() == original
        assert effective is not body


@pytest.mark.parametrize(
    "body", [RetrieveRequest(url=_URL), SearchRequest(query="gardening")]
)
def test_unknown_policy_update_keys_fail_before_model_copy(
    body: RetrieveRequest | SearchRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    fields = dict(type(body).model_fields)
    del fields["promptguard_fail_closed"]
    monkeypatch.setattr(type(body), "model_fields", fields)
    with pytest.raises(AssertionError):
        _promptguard_policy_updates(body, RetrieveSettings())


@pytest.mark.parametrize("tier", [TrustTier.STANDARD, TrustTier.UNTRUSTED])
@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("unavailable", ["absent", "wait-timeout"])
async def test_retrieve_floor_blocks_unavailable_standard_and_untrusted_content(
    client: httpx.AsyncClient, tier: TrustTier, flag: bool, unavailable: str
) -> None:
    classifier = _classifier(0.0)
    semaphore = asyncio.Semaphore(0)
    if unavailable == "wait-timeout":
        app.state.classifier = classifier
        app.state.classification_semaphore = semaphore
    # No caller knob selects UNTRUSTED today; exercise that pipeline tier at its seam.
    with patch("pipeline.orchestrator._resolve_request_trust_tier", return_value=tier):
        response = await client.post(
            "/retrieve", json={"url": _URL, "promptguard_fail_closed": flag}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["promptguard_state"] == "unavailable_blocked"
    assert body["injection_detected"] is True
    assert "gardening" not in body["body"]
    assert body["effective_promptguard_fail_closed"] is True
    assert body["effective_promptguard_threshold"] == 0.5
    assert app.state.retrieve_metrics.classification_wait_timeouts == (
        1 if unavailable == "wait-timeout" else 0
    )
    classifier.classify.assert_not_called()
    assert semaphore._value == 0


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("unavailable", ["absent", "wait-timeout"])
async def test_search_floor_omits_unavailable_results(
    client: httpx.AsyncClient, flag: bool, unavailable: str
) -> None:
    classifier = _classifier(0.0)
    if unavailable == "wait-timeout":
        app.state.classifier = classifier
        app.state.classification_semaphore = asyncio.Semaphore(0)
    response = await client.post(
        "/search", json={"query": "gardening", "promptguard_fail_closed": flag}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert body["omitted_by_reason"] == {"promptguard_unavailable": 1}
    assert body["promptguard_unavailable"] is True
    assert body["effective_promptguard_fail_closed"] is True
    assert "effective_promptguard_threshold" not in body
    assert app.state.search_metrics.classification_wait_timeouts == (
        1 if unavailable == "wait-timeout" else 0
    )
    classifier.classify.assert_not_called()


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("route", ["/retrieve", "/search"])
async def test_default_operator_settings_preserve_the_callers_policy(
    client: httpx.AsyncClient, route: str, flag: bool
) -> None:
    app.state.retrieve_settings = RetrieveSettings()
    request: dict[str, object] = {"promptguard_fail_closed": flag}
    request.update({"url": _URL} if route == "/retrieve" else {"query": "gardening"})
    response = await client.post(route, json=request)
    assert response.status_code == 200
    body = response.json()
    assert body["effective_promptguard_fail_closed"] is flag
    if route == "/retrieve":
        assert body["effective_promptguard_threshold"] == 0.85
        assert body["promptguard_state"] == (
            "unavailable_blocked" if flag else "unavailable_allowed"
        )
    else:
        assert len(body["results"]) == (0 if flag else 1)
        if not flag:
            assert body["results"][0]["suspicious"] is True
            assert body["unscanned_results"] == 1


@pytest.mark.parametrize(
    ("trust_list", "state"),
    [
        ("trusted_domains", "skipped_trusted"),
        ("verified_domains", "unavailable_allowed"),
    ],
)
@pytest.mark.parametrize("unavailable", ["absent", "wait-timeout"])
async def test_trust_exemptions_are_not_overridden_by_the_floor(
    client: httpx.AsyncClient, trust_list: str, state: str, unavailable: str
) -> None:
    classifier = _classifier(0.7)
    if unavailable == "wait-timeout":
        app.state.classifier = classifier
        app.state.classification_semaphore = asyncio.Semaphore(0)
    response = await client.post(
        "/retrieve",
        json={
            "url": _URL,
            trust_list: ["example.com"],
            "promptguard_fail_closed": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["promptguard_state"] == state
    assert body["injection_detected"] is False
    assert "gardening" in body["body"]
    assert body["effective_promptguard_fail_closed"] is True
    assert body["effective_promptguard_threshold"] == 0.5
    classifier.classify.assert_not_called()
    assert app.state.retrieve_metrics.classification_wait_timeouts == (
        1 if unavailable == "wait-timeout" and trust_list == "verified_domains" else 0
    )


async def test_ceiling_changes_retrieve_classification_but_not_search_or_extract(
    client: httpx.AsyncClient,
) -> None:
    classifier = _classifier(0.7)
    app.state.classifier = classifier
    retrieve = await client.post(
        "/retrieve", json={"url": _URL, "promptguard_threshold": 1.0}
    )
    assert retrieve.status_code == 200
    assert retrieve.json()["injection_detected"] is True
    assert retrieve.json()["promptguard_state"] == "scanned"
    assert retrieve.json()["effective_promptguard_threshold"] == 0.5

    search = await client.post("/search", json={"query": "gardening"})
    assert search.status_code == 200
    assert len(search.json()["results"]) == 1
    assert search.json()["effective_promptguard_fail_closed"] is True
    assert "effective_promptguard_threshold" not in search.json()

    extract = await client.post(
        "/extract",
        files={"file": ("article.txt", b"A calm article.", "text/plain")},
        data={"filename": "article.txt"},
    )
    assert extract.status_code == 200
    assert extract.json()["injection_detected"] is False
    assert not any(key.startswith("effective_") for key in extract.json())
    assert classifier.classify.call_count == 3


@pytest.mark.parametrize("floor", [False, True])
async def test_extract_stays_fail_closed_without_any_policy_fields(
    client: httpx.AsyncClient, floor: bool
) -> None:
    app.state.retrieve_settings = RetrieveSettings(promptguard_fail_closed_floor=floor)
    response = await client.post(
        "/extract",
        files={"file": ("article.txt", b"A calm article.", "text/plain")},
        data={"filename": "article.txt"},
    )
    assert response.status_code == 200
    assert response.json()["promptguard_state"] == "unavailable_blocked"
    assert not any(key.startswith("effective_") for key in response.json())


@pytest.mark.parametrize("bound", ["floor", "ceiling"])
async def test_cache_fingerprint_uses_effective_values_and_never_serves_weaker_policy(
    client: httpx.AsyncClient, bound: str
) -> None:
    storage = FakeStorage()
    app.state.cache = ContentCache(storage=storage)
    app.state.retrieve_settings = RetrieveSettings()
    if bound == "ceiling":
        app.state.classifier = _classifier(0.7)
    request = {
        "url": _URL,
        "promptguard_fail_closed": False,
        "promptguard_threshold": 1.0,
    }
    with patch(
        "pipeline.orchestrator.cache_policy_fingerprint",
        wraps=cache_policy_fingerprint,
    ) as fingerprint:
        warm = await client.post("/retrieve", json=request)
        assert warm.status_code == 200
        assert warm.json()["cache_hit"] is False
        assert warm.json()["injection_detected"] is False
        assert len(storage.entries) == 1
        app.state.retrieve_settings = RetrieveSettings(
            promptguard_fail_closed_floor=bound == "floor",
            promptguard_threshold_ceiling=0.5 if bound == "ceiling" else 1.0,
        )
        guarded = await client.post("/retrieve", json=request)
    assert guarded.status_code == 200
    body = guarded.json()
    assert body["cache_hit"] is False
    assert body["injection_detected"] is True
    assert fingerprint.call_count == 2
    before, after = (call.kwargs for call in fingerprint.call_args_list)
    assert before["promptguard_fail_closed"] is False
    assert before["promptguard_threshold"] == 1.0
    assert after["promptguard_fail_closed"] == body["effective_promptguard_fail_closed"]
    assert after["promptguard_threshold"] == body["effective_promptguard_threshold"]
    assert cache_policy_fingerprint(**before) != cache_policy_fingerprint(**after)
    assert storage.set_calls == 1


@pytest.mark.parametrize("stored_fields", ["defaults", "absent", "stale"])
async def test_cache_hits_are_stamped_after_reading_even_for_pre_upgrade_entries(
    client: httpx.AsyncClient, stored_fields: str
) -> None:
    storage = FakeStorage()
    app.state.cache = ContentCache(storage=storage)
    classifier = _classifier(0.1)
    app.state.classifier = classifier
    request = {
        "url": _URL,
        "promptguard_fail_closed": False,
        "promptguard_threshold": 1.0,
    }
    miss = await client.post("/retrieve", json=request)
    assert miss.status_code == 200
    assert miss.json()["cache_hit"] is False
    (key,) = storage.entries
    cached: dict[str, Any] = json.loads(storage.entries[key][0])
    # The pipeline caches before the handler stamps; those defaults are not evidence.
    assert cached["effective_promptguard_threshold"] == 0.85
    if stored_fields == "absent":
        del cached["effective_promptguard_fail_closed"]
        del cached["effective_promptguard_threshold"]
    elif stored_fields == "stale":
        cached["effective_promptguard_fail_closed"] = False
        cached["effective_promptguard_threshold"] = 0.99
    await storage.set(key, json.dumps(cached), ttl_seconds=3600)
    with patch(
        "pipeline.orchestrator.fetch_url", side_effect=AssertionError("cache hit")
    ):
        hit = await client.post("/retrieve", json=request)
        equivalent = await client.post(
            "/retrieve",
            json=request
            | {"promptguard_fail_closed": True, "promptguard_threshold": 0.5},
        )
    for response in (hit, equivalent):
        assert response.status_code == 200
        assert response.json()["cache_hit"] is True
        assert response.json()["effective_promptguard_fail_closed"] is True
        assert response.json()["effective_promptguard_threshold"] == 0.5
        assert response.json()["body"] == miss.json()["body"]
    classifier.classify.assert_called_once()
    assert storage.entries[key][0] == json.dumps(cached).encode()


@pytest.mark.parametrize(
    ("route", "pipeline", "body", "code"),
    [
        ("/retrieve", "run_retrieve_pipeline", {"url": _URL}, "invalid_url"),
        (
            "/search",
            "run_search_pipeline",
            {"query": "gardening"},
            "search_unavailable",
        ),
    ],
)
async def test_pipeline_refusals_carry_no_effective_policy_fields(
    client: httpx.AsyncClient,
    route: str,
    pipeline: str,
    body: dict[str, str],
    code: str,
) -> None:
    with patch(
        f"retrieval_app.{pipeline}",
        side_effect=PipelineError(error=code, reason="refused", request_id="request"),
    ):
        response = await client.post(route, json=body)
    assert response.status_code == 422
    assert response.json() == {
        "error": code,
        "reason": "refused",
        "request_id": "request",
    }


def test_effective_field_defaults_and_scanning_scope_are_pinned() -> None:
    for model, names in (
        (
            RetrievedContent,
            {"effective_promptguard_fail_closed", "effective_promptguard_threshold"},
        ),
        (SearchResponse, {"effective_promptguard_fail_closed"}),
        (ExtractedContent, set[str]()),
    ):
        assert {
            key for key in model.model_fields if key.startswith("effective_")
        } == names
        for name in names:
            field = model.model_fields[name]
            assert field.default == (0.85 if name.endswith("threshold") else True)
            description = field.description or ""
            assert "not whether" in description
            assert "trusted_tier" in description
            assert "VERIFIED" in description
