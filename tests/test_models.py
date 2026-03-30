"""Tests for Retrieval sidecar Pydantic models (US-002)."""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Add the retrieval service root to sys.path so models is importable
_retrieval_root = str(
    Path(__file__).resolve().parents[2] / "services" / "retrieval"
)
if _retrieval_root not in sys.path:
    sys.path.insert(0, _retrieval_root)

from models import (  # noqa: E402
    RetrievedContent,
    RetrieveRequest,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Stage2Verdict,
    Stage3Verdict,
    TrustTier,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_retrieved_content(**overrides: object) -> RetrievedContent:
    """Build a valid ``RetrievedContent`` with sensible defaults."""
    defaults: dict[str, object] = {
        "request_id": str(uuid.uuid4()),
        "source_url": "https://example.com/page",
        "final_url": "https://example.com/page",
        "body": "Hello world content.",
        "word_count": 3,
        "content_type": "html",
        "trust_score": 0.9,
        "trust_tier": TrustTier.STANDARD,
        "stage2_verdict": Stage2Verdict.CLEAN,
        "stage3_verdict": Stage3Verdict.SAFE,
        "domain": "example.com",
    }
    defaults.update(overrides)
    return RetrievedContent.model_validate(defaults)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestEnums:
    """Verify enum members and string serialisation."""

    def test_trust_tier_values(self) -> None:
        assert set(TrustTier) == {
            TrustTier.TRUSTED,
            TrustTier.VERIFIED,
            TrustTier.STANDARD,
            TrustTier.UNTRUSTED,
            TrustTier.BLOCKED,
        }
        # str enum serialises to its value
        assert TrustTier.TRUSTED.value == "trusted"

    def test_stage2_verdict_values(self) -> None:
        assert set(Stage2Verdict) == {
            Stage2Verdict.CLEAN,
            Stage2Verdict.SUSPICIOUS,
            Stage2Verdict.BLOCKED,
        }

    def test_stage3_verdict_values(self) -> None:
        assert set(Stage3Verdict) == {
            Stage3Verdict.SAFE,
            Stage3Verdict.INJECTION_DETECTED,
        }


# ---------------------------------------------------------------------------
# RetrievedContent tests
# ---------------------------------------------------------------------------


class TestRetrievedContent:
    """Test the core ``RetrievedContent`` model."""

    def test_valid_construction(self) -> None:
        rc = _make_retrieved_content()
        assert rc.source_url == "https://example.com/page"
        assert rc.cache_hit is False
        assert rc.cached_at is None
        assert rc.injection_detected is False
        assert rc.injection_spans == []
        assert rc.structural_flags == []
        assert rc.redirect_chain == []
        assert rc.domain_changed_on_redirect is False
        assert rc.truncation_notice is None

    def test_retrieved_at_auto_set(self) -> None:
        before = datetime.now(UTC)
        rc = _make_retrieved_content()
        after = datetime.now(UTC)
        assert before <= rc.retrieved_at <= after

    def test_serialization_roundtrip(self) -> None:
        rc = _make_retrieved_content(
            title="Test Page",
            cache_hit=True,
            cached_at=datetime(2026, 1, 1, tzinfo=UTC),
            injection_detected=True,
            injection_spans=["ignore previous instructions"],
            structural_flags=["hidden_text"],
            redirect_chain=["https://short.url/abc"],
            domain_changed_on_redirect=True,
            truncation_notice="Omitted 3000 words from body.",
        )
        json_bytes = rc.model_dump_json()
        restored = RetrievedContent.model_validate_json(json_bytes)
        assert restored == rc
        assert restored.truncation_notice == "Omitted 3000 words from body."

    def test_trust_score_bounds(self) -> None:
        # Valid boundaries
        rc_low = _make_retrieved_content(trust_score=0.0)
        assert rc_low.trust_score == 0.0
        rc_high = _make_retrieved_content(trust_score=1.0)
        assert rc_high.trust_score == 1.0

    def test_trust_score_out_of_range(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            _make_retrieved_content(trust_score=1.5)
        with pytest.raises(Exception):  # noqa: B017
            _make_retrieved_content(trust_score=-0.1)

    def test_word_count_negative_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            _make_retrieved_content(word_count=-1)

    def test_content_type_validation(self) -> None:
        assert _make_retrieved_content(content_type="html").content_type == "html"
        assert _make_retrieved_content(content_type="pdf").content_type == "pdf"
        with pytest.raises(Exception):  # noqa: B017
            _make_retrieved_content(content_type="docx")

    def test_empty_request_id_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            _make_retrieved_content(request_id="")

    def test_truncation_notice_field(self) -> None:
        rc = _make_retrieved_content(
            truncation_notice="Body truncated from 5000 to 2000 words."
        )
        assert rc.truncation_notice == "Body truncated from 5000 to 2000 words."


# ---------------------------------------------------------------------------
# RetrieveRequest tests
# ---------------------------------------------------------------------------


class TestRetrieveRequest:
    """Test the ``RetrieveRequest`` model."""

    def test_defaults(self) -> None:
        req = RetrieveRequest(url="https://example.com")
        assert req.extract_mode == "summary"
        assert req.trusted_domains == []
        assert req.verified_domains == []
        assert req.blocked_domains == []
        assert req.promptguard_threshold == 0.85

    def test_custom_domains(self) -> None:
        req = RetrieveRequest(
            url="https://example.com",
            trusted_domains=["example.com"],
            verified_domains=["news.com"],
            blocked_domains=["evil.com"],
        )
        assert req.trusted_domains == ["example.com"]
        assert req.verified_domains == ["news.com"]
        assert req.blocked_domains == ["evil.com"]

    def test_extract_mode_full(self) -> None:
        req = RetrieveRequest(url="https://example.com", extract_mode="full")
        assert req.extract_mode == "full"

    def test_invalid_extract_mode_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            RetrieveRequest(url="https://example.com", extract_mode="partial")  # type: ignore[arg-type]

    def test_empty_url_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            RetrieveRequest(url="")

    def test_serialization_roundtrip(self) -> None:
        req = RetrieveRequest(
            url="https://example.com",
            extract_mode="full",
            trusted_domains=["t.com"],
            promptguard_threshold=0.5,
        )
        restored = RetrieveRequest.model_validate_json(req.model_dump_json())
        assert restored == req


# ---------------------------------------------------------------------------
# SearchRequest tests
# ---------------------------------------------------------------------------


class TestSearchRequest:
    """Test the ``SearchRequest`` model."""

    def test_defaults(self) -> None:
        req = SearchRequest(query="python pydantic")
        assert req.num_results == 5

    def test_num_results_bounds(self) -> None:
        assert SearchRequest(query="q", num_results=1).num_results == 1
        assert SearchRequest(query="q", num_results=20).num_results == 20

    def test_num_results_out_of_range(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            SearchRequest(query="q", num_results=0)
        with pytest.raises(Exception):  # noqa: B017
            SearchRequest(query="q", num_results=21)

    def test_empty_query_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            SearchRequest(query="")

    def test_serialization_roundtrip(self) -> None:
        req = SearchRequest(query="test query", num_results=10)
        restored = SearchRequest.model_validate_json(req.model_dump_json())
        assert restored == req


# ---------------------------------------------------------------------------
# SearchResult / SearchResponse tests
# ---------------------------------------------------------------------------


class TestSearchResult:
    """Test the ``SearchResult`` model."""

    def test_valid_construction(self) -> None:
        sr = SearchResult(
            title="Example", url="https://example.com", snippet="A snippet"
        )
        assert sr.engine is None

    def test_with_engine(self) -> None:
        sr = SearchResult(
            title="Example",
            url="https://example.com",
            snippet="A snippet",
            engine="brave",
        )
        assert sr.engine == "brave"


class TestSearchResponse:
    """Test the ``SearchResponse`` model."""

    def test_valid_construction(self) -> None:
        resp = SearchResponse(
            results=[
                SearchResult(
                    title="R1", url="https://r1.com", snippet="s1"
                ),
            ],
            request_id=str(uuid.uuid4()),
            query="test",
        )
        assert len(resp.results) == 1

    def test_empty_results(self) -> None:
        resp = SearchResponse(
            results=[], request_id="abc123", query="nothing"
        )
        assert resp.results == []

    def test_serialization_roundtrip(self) -> None:
        resp = SearchResponse(
            results=[
                SearchResult(
                    title="R1", url="https://r1.com", snippet="s1", engine="brave"
                ),
                SearchResult(
                    title="R2", url="https://r2.com", snippet="s2"
                ),
            ],
            request_id="req-001",
            query="pydantic models",
        )
        restored = SearchResponse.model_validate_json(resp.model_dump_json())
        assert restored == resp
        assert restored.results[0].engine == "brave"
        assert restored.results[1].engine is None
