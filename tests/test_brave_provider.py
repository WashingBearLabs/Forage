"""Tests for ``BraveApiProvider`` (``feature-brave-provider`` US-010).

US-010 delivers the complete provider module — client hardening, the
``config.yaml`` tunables, and the parser over the owner-captured pinned
sample (``tests/fixtures/brave/llm_context_sample.json``) — plus this
story's core tests. The payload-bound, query/chunk-cap and ``max_results``
budget *exhaustive* tests, and the doc rows, are US-011's.
"""

from __future__ import annotations

import gzip
import json
import re
import ssl
from collections.abc import AsyncIterator, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import yaml
from fastapi import FastAPI

import retrieval_app
from models import SearchRequest
from pipeline.orchestrator import (
    _MAX_SEARCH_SNIPPET_LENGTH,
    _sanitize_search_text,
    run_search_pipeline,
)
from pipeline.search_providers.base import ProviderFailure, ProviderSearchResult
from pipeline.search_providers.brave import (
    _BRAVE_AUTH_HEADER,
    _BRAVE_LLM_CONTEXT_URL,
    _BRAVE_MAX_RESPONSE_BYTES,
    BRAVE_PROVIDER_NAME,
    DEFAULT_BRAVE_CHUNK_MAX_CHARS,
    DEFAULT_BRAVE_QUERY_MAX_CHARS,
    DEFAULT_BRAVE_TIMEOUT_SECONDS,
    BraveApiProvider,
    BraveConfigurationError,
    BraveSettings,
    brave_settings_from_config,
)
from retrieval_app import lifespan
from tests.fakes import FakeSearchProvider

# ---------------------------------------------------------------------------
# Fixture provenance — the pre-flight gate stays satisfied
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_BRAVE_FIXTURES_DIR = _FIXTURES_DIR / "brave"
_SAMPLE_PATH = _BRAVE_FIXTURES_DIR / "llm_context_sample.json"

_AUTH_HEADER_NAMES = ("X-Subscription-Token", "Authorization")
# 24 or more letters, digits, `_` or `-` in a row — the shape a real Brave
# key or bearer token takes. The pre-existing model fixtures live outside
# `tests/fixtures/brave/`, so this walk never sees their long identifiers.
_TOKEN_SHAPE_RE = re.compile(r"[A-Za-z0-9_-]{24,}")


def _load_sample_bytes() -> bytes:
    return _SAMPLE_PATH.read_bytes()


def _load_sample_dict() -> dict[str, Any]:
    return json.loads(_load_sample_bytes())


class TestFixtureCarriesNoSecret:
    """The owner gate (ruling 24, satisfied at ``9794cba``) stays satisfied.

    Modelled on ``tests/test_dockerfile.py::test_no_token_shaped_literal_anywhere``.
    """

    def test_no_auth_header_name_anywhere_in_fixtures(self) -> None:
        for path in sorted(_FIXTURES_DIR.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            for header in _AUTH_HEADER_NAMES:
                assert header not in text, f"{path} names an auth header: {header}"

    def test_no_token_shaped_literal_anywhere_in_brave_fixtures(self) -> None:
        for path in sorted(_BRAVE_FIXTURES_DIR.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            matches = _TOKEN_SHAPE_RE.findall(text)
            assert matches == [], (
                f"{path} contains {len(matches)} token-shaped literal(s): {matches}"
            )


# ---------------------------------------------------------------------------
# Streaming client doubles — copied from
# tests/test_stage5_url_audit.py:43-82 for the streamed-envelope fixture path
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int = 200,
    content: bytes = b"{}",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a minimal httpx.Response."""
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=hdrs,
        request=httpx.Request("GET", _BRAVE_LLM_CONTEXT_URL),
    )


def _make_stream_cm(response: httpx.Response) -> MagicMock:
    """Async context manager mock that yields *response* on __aenter__."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


_BRAVE_CLIENT = "pipeline.search_providers.brave.httpx.AsyncClient"


@contextmanager
def _client_patch(
    *, response: httpx.Response | None = None
) -> Generator[tuple[MagicMock, MagicMock]]:
    """Intercept the provider's per-call ``httpx.AsyncClient``."""
    client = MagicMock()
    envelope = response if response is not None else _make_response()
    client.stream = MagicMock(return_value=_make_stream_cm(envelope))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch(_BRAVE_CLIENT, return_value=client) as client_cls:
        yield client_cls, client


_INTEGRATION_CONFIG: dict[str, Any] = {
    "user_agents": ["TestAgent/1.0"],
    "news_domains": [],
    "seed_blocklist": [],
    "extract_route_enabled": True,
}


# ---------------------------------------------------------------------------
# Provider shape
# ---------------------------------------------------------------------------


class TestProviderShape:
    def test_name_and_paid(self) -> None:
        provider = BraveApiProvider("sentinel-key")
        assert provider.name == "brave"
        assert provider.name == BRAVE_PROVIDER_NAME
        assert provider.paid is True

    def test_origin_is_always_none(self) -> None:
        """A fixed third-party endpoint is never echoed (protocol contract point 1)."""
        provider = BraveApiProvider("sentinel-key")
        assert provider.origin is None

    def test_repr_and_str_never_contain_the_key(self) -> None:
        provider = BraveApiProvider("sentinel-key-value-should-never-leak")
        assert "sentinel-key-value-should-never-leak" not in repr(provider)
        assert "sentinel-key-value-should-never-leak" not in str(provider)
        assert "brave" in repr(provider).lower()


# ---------------------------------------------------------------------------
# Wire names, recorded in the feature spec's Implementation Notes — pinned
# by end-to-end parsing of the owner-captured sample
# ---------------------------------------------------------------------------


class TestParsesThePinnedSample:
    """``search()`` parses ``llm_context_sample.json`` end to end."""

    @pytest.mark.asyncio()
    async def test_search_returns_one_chunk_result_per_source(self) -> None:
        provider = BraveApiProvider("sentinel-key")
        with _client_patch(response=_make_response(content=_load_sample_bytes())):
            outcome = await provider.search("history of the bicycle", 3)

        assert isinstance(outcome, ProviderSearchResult)
        assert outcome.provider_name == "brave"
        assert outcome.content_kind == "chunk"
        assert outcome.unresponsive_engines == []
        assert len(outcome.results) == 3
        for result in outcome.results:
            assert set(result) == {"title", "url", "content", "engine", "date"}
            assert result["engine"] == "brave-api"

    @pytest.mark.asyncio()
    async def test_fields_match_the_sample_values(self) -> None:
        provider = BraveApiProvider("sentinel-key")
        with _client_patch(response=_make_response(content=_load_sample_bytes())):
            outcome = await provider.search("history of the bicycle", 3)

        assert isinstance(outcome, ProviderSearchResult)
        first = outcome.results[0]
        assert first["url"] == "https://synthetic-1.example.invalid/synthetic-page-1"
        assert first["title"] == "Synthetic title for source 1 of 3"
        # The ten-character ISO element (index 1) of `age`, not the long-form
        # element (index 0) or the relative/timestamp elements (2, 3).
        assert first["date"] == "2026-01-01"
        assert first["content"].startswith("synthetic chunk 1 of 35 for source 1 of 3")
        # Source 1 has 35 snippets (tests/fixtures/README.md) — enough that
        # the joined content exceeds the default `chunk_max_chars` (2000),
        # so the second snippet is present but the cap (US-011's exhaustive
        # subject) is already visibly doing something here.
        assert "synthetic chunk 2 of 35 for source 1 of 3" in first["content"]
        assert len(first["content"]) <= 2000

    @pytest.mark.asyncio()
    async def test_max_results_bounds_the_mapped_sources(self) -> None:
        provider = BraveApiProvider("sentinel-key")
        with _client_patch(response=_make_response(content=_load_sample_bytes())):
            outcome = await provider.search("history of the bicycle", 1)

        assert isinstance(outcome, ProviderSearchResult)
        assert len(outcome.results) == 1


# ---------------------------------------------------------------------------
# Client hardening (protocol contract point 2)
# ---------------------------------------------------------------------------


class TestClientHardening:
    @pytest.mark.asyncio()
    async def test_constructor_kwargs_are_hardened(self) -> None:
        provider = BraveApiProvider("sentinel-key", BraveSettings(timeout_seconds=7.5))
        with patch(_BRAVE_CLIENT) as client_cls:
            client = MagicMock()
            client.stream = MagicMock(
                return_value=_make_stream_cm(
                    _make_response(content=_load_sample_bytes())
                )
            )
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client_cls.return_value = client

            await provider.search("q", 3)

        call_kwargs = client_cls.call_args.kwargs
        assert call_kwargs["trust_env"] is False
        assert call_kwargs["follow_redirects"] is False
        assert isinstance(call_kwargs["verify"], ssl.SSLContext)
        assert call_kwargs["timeout"] == 7.5

    @pytest.mark.asyncio()
    async def test_request_target_and_key_placement(self) -> None:
        sentinel_key = "sentinel-key-value-should-never-leak"
        provider = BraveApiProvider(sentinel_key)
        with _client_patch(response=_make_response(content=_load_sample_bytes())) as (
            _client_cls,
            client,
        ):
            await provider.search("q", 3)

        stream_call = client.stream.call_args
        assert stream_call.args[0] == "GET"
        assert stream_call.args[1] == _BRAVE_LLM_CONTEXT_URL
        assert _BRAVE_LLM_CONTEXT_URL.startswith("https://api.search.brave.com")
        assert stream_call.kwargs["headers"] == {_BRAVE_AUTH_HEADER: sentinel_key}
        # The key appears only in the auth header value — never in the URL,
        # the query params, or anywhere else in what was sent.
        assert sentinel_key not in _BRAVE_LLM_CONTEXT_URL
        assert sentinel_key not in str(stream_call.kwargs.get("params", {}))


# ---------------------------------------------------------------------------
# `run_search_pipeline` integration — the provider as `chain[0]`
# ---------------------------------------------------------------------------


class TestRunSearchPipelineIntegration:
    @pytest.mark.asyncio()
    async def test_wire_results_carry_chunk_kind_and_brave_api_engine(self) -> None:
        provider = BraveApiProvider("sentinel-key")
        with _client_patch(response=_make_response(content=_load_sample_bytes())):
            response = await run_search_pipeline(
                SearchRequest(
                    query="history of the bicycle",
                    num_results=5,
                    promptguard_fail_closed=False,
                ),
                providers=[provider],
                config=_INTEGRATION_CONFIG,
            )

        assert response.results, "expected at least one result to survive sanitization"
        for result in response.results:
            assert result.content_kind == "chunk"
            assert result.engine == "brave-api"

    @pytest.mark.asyncio()
    async def test_wire_snippet_equals_the_sanitized_chunk_content_exactly(
        self,
    ) -> None:
        """Equality, not ``startswith`` — the same sanitization the provider fed in."""
        provider = BraveApiProvider("sentinel-key")

        with _client_patch(response=_make_response(content=_load_sample_bytes())):
            direct_outcome = await provider.search("q", 5)
        assert isinstance(direct_outcome, ProviderSearchResult)
        raw_content = direct_outcome.results[0]["content"]
        expected_snippet, _ = _sanitize_search_text(
            raw_content, max_length=_MAX_SEARCH_SNIPPET_LENGTH
        )

        with _client_patch(response=_make_response(content=_load_sample_bytes())):
            response = await run_search_pipeline(
                SearchRequest(query="q", num_results=5, promptguard_fail_closed=False),
                providers=[provider],
                config=_INTEGRATION_CONFIG,
            )

        assert response.results
        assert response.results[0].snippet == expected_snippet

    @pytest.mark.asyncio()
    async def test_an_invalid_calendar_date_reaches_the_wire_as_none(self) -> None:
        """A regex-shaped but non-existent day (2026-02-30) yields date=None."""
        sample = _load_sample_dict()
        first_url = sample["grounding"]["generic"][0]["url"]
        sample["sources"][first_url]["age"][1] = "2026-02-30"

        provider = BraveApiProvider("sentinel-key")
        with _client_patch(
            response=_make_response(content=json.dumps(sample).encode())
        ):
            response = await run_search_pipeline(
                SearchRequest(query="q", num_results=5, promptguard_fail_closed=False),
                providers=[provider],
                config=_INTEGRATION_CONFIG,
            )

        assert response.results
        assert response.results[0].date is None


# ---------------------------------------------------------------------------
# config.yaml tunables (ruling 9)
# ---------------------------------------------------------------------------


class TestBraveSettingsFromConfig:
    def test_defaults(self) -> None:
        settings = brave_settings_from_config({})
        assert settings == BraveSettings(
            timeout_seconds=DEFAULT_BRAVE_TIMEOUT_SECONDS,
            chunk_max_chars=DEFAULT_BRAVE_CHUNK_MAX_CHARS,
            query_max_chars=DEFAULT_BRAVE_QUERY_MAX_CHARS,
        )

    def test_a_configured_value_is_read(self) -> None:
        settings = brave_settings_from_config({"search_brave_timeout_seconds": 30.0})
        assert settings.timeout_seconds == 30.0

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("search_brave_timeout_seconds", 0.5),
            ("search_brave_timeout_seconds", 60.1),
            ("search_brave_timeout_seconds", "15"),
            ("search_brave_timeout_seconds", True),
            ("search_brave_chunk_max_chars", 199),
            ("search_brave_chunk_max_chars", 2001),
            ("search_brave_chunk_max_chars", 200.0),
            ("search_brave_query_max_chars", 49),
            ("search_brave_query_max_chars", 401),
        ],
    )
    def test_out_of_range_or_wrong_typed_values_are_refused(
        self, key: str, value: object
    ) -> None:
        with pytest.raises(BraveConfigurationError):
            brave_settings_from_config({key: value})

    def test_the_shipped_config_yaml_pins_the_documented_defaults(self) -> None:
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        shipped = yaml.safe_load(config_path.read_text())

        settings = brave_settings_from_config(shipped)
        assert settings == BraveSettings()


class TestLifespanCallsBraveSettingsUnconditionally:
    async def test_an_out_of_range_value_refuses_boot_with_no_brave_in_the_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`brave_settings_from_config` runs even when "brave" never registers.

        `FORAGE_SEARCH_PROVIDERS` is unset (cleared by the autouse fixture in
        `tests/conftest.py`), so the resolved chain is the default
        `["searxng"]` — `"brave"` is not even in `build_provider_chain`'s
        registry yet (US-002's job). The boot still refuses, because this
        call is unconditional.
        """
        monkeypatch.setattr(
            retrieval_app,
            "_load_config",
            lambda: {"search_brave_timeout_seconds": 999.0},
        )

        probe_app = FastAPI()
        with pytest.raises(BraveConfigurationError):
            async with lifespan(probe_app):
                pass


# ---------------------------------------------------------------------------
# Candidate budget (ruling 25) — US-011
# ---------------------------------------------------------------------------


class _SpyingBraveProvider(BraveApiProvider):
    """Records every ``search()`` call's arguments, then delegates for real."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, int]] = []

    async def search(
        self, query: str, max_results: int
    ) -> ProviderSearchResult | ProviderFailure:
        self.calls.append((query, max_results))
        return await super().search(query, max_results)


class TestCandidateBudget:
    """The paid budget is `request.num_results`, forwarded to Brave verbatim.

    `run_search_pipeline`'s `max_results = request.num_results if provider.paid
    else fetch_limit` branch is spec 1 US-002's edit, asserted here rather than
    written — `pipeline/orchestrator.py` is untouched by this story.
    """

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("num_results", [1, 5, 20])
    async def test_the_provider_and_the_outbound_count_param_both_see_num_results(
        self, num_results: int
    ) -> None:
        provider = _SpyingBraveProvider("sentinel-key")
        with _client_patch(response=_make_response(content=_load_sample_bytes())) as (
            _client_cls,
            client,
        ):
            await run_search_pipeline(
                SearchRequest(
                    query="q", num_results=num_results, promptguard_fail_closed=False
                ),
                providers=[provider],
                config=_INTEGRATION_CONFIG,
            )

        # Not `min(num_results * 2, 20)` — the free-provider fetch_limit.
        assert provider.calls == [("q", num_results)]
        stream_call = client.stream.call_args
        assert stream_call.kwargs["params"]["count"] == num_results

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("max_results", [1, 2])
    async def test_more_sources_than_max_results_yields_at_most_that_many_dicts(
        self, max_results: int
    ) -> None:
        """The pinned sample carries three sources."""
        provider = BraveApiProvider("sentinel-key")
        with _client_patch(response=_make_response(content=_load_sample_bytes())):
            outcome = await provider.search("q", max_results)

        assert isinstance(outcome, ProviderSearchResult)
        assert len(outcome.results) == max_results


# ---------------------------------------------------------------------------
# Payload bounds — US-011
# ---------------------------------------------------------------------------


class _ChunkStream(httpx.AsyncByteStream):
    """Yield fixed byte chunks with no synthesized ``Content-Length`` header."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class TestBodyBound:
    """The response body is bounded before any ``json.loads`` call."""

    @pytest.mark.asyncio()
    async def test_content_length_over_cap_is_rejected_before_json_loads(
        self,
    ) -> None:
        response = _make_response(
            content=b"{}",
            headers={"content-length": str(_BRAVE_MAX_RESPONSE_BYTES + 1)},
        )
        provider = BraveApiProvider("sentinel-key")
        with (
            _client_patch(response=response),
            patch(
                "pipeline.search_providers.brave.json.loads", wraps=json.loads
            ) as loads_spy,
        ):
            outcome = await provider.search("q", 5)

        loads_spy.assert_not_called()
        assert isinstance(outcome, ProviderFailure)
        assert outcome.failure_class == "hard_error"
        assert outcome.detail == "body_too_large"

    @pytest.mark.asyncio()
    async def test_streamed_body_over_cap_with_no_content_length_is_rejected(
        self,
    ) -> None:
        chunks = [b"X" * 400_000 for _ in range(3)]  # 1.2 MB total
        response = httpx.Response(
            200,
            stream=_ChunkStream(chunks),
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", _BRAVE_LLM_CONTEXT_URL),
        )
        # The precondition this test depends on: no Content-Length at all, so
        # only the running-byte-cap path (not the fast-reject one) can catch it.
        assert response.headers.get("content-length") is None

        provider = BraveApiProvider("sentinel-key")
        with (
            _client_patch(response=response),
            patch(
                "pipeline.search_providers.brave.json.loads", wraps=json.loads
            ) as loads_spy,
        ):
            outcome = await provider.search("q", 5)

        loads_spy.assert_not_called()
        assert isinstance(outcome, ProviderFailure)
        assert outcome.failure_class == "hard_error"
        assert outcome.detail == "body_too_large"

    @pytest.mark.asyncio()
    async def test_compressed_body_whose_decoded_length_exceeds_cap_is_rejected(
        self,
    ) -> None:
        raw = b"A" * (_BRAVE_MAX_RESPONSE_BYTES + 1)
        compressed = gzip.compress(raw)
        response = httpx.Response(
            200,
            content=compressed,
            headers={"content-encoding": "gzip", "content-type": "application/json"},
            request=httpx.Request("GET", _BRAVE_LLM_CONTEXT_URL),
        )
        # The compressed body is well under the cap; only the decoded stream
        # (read through `aiter_bytes()`, which transparently decompresses) is
        # oversized — otherwise this would just be the fast-reject case again.
        content_length = response.headers.get("content-length")
        assert content_length is not None
        assert int(content_length) <= _BRAVE_MAX_RESPONSE_BYTES

        provider = BraveApiProvider("sentinel-key")
        with (
            _client_patch(response=response),
            patch(
                "pipeline.search_providers.brave.json.loads", wraps=json.loads
            ) as loads_spy,
        ):
            outcome = await provider.search("q", 5)

        loads_spy.assert_not_called()
        assert isinstance(outcome, ProviderFailure)
        assert outcome.failure_class == "hard_error"
        assert outcome.detail == "body_too_large"


class TestPayloadCaps:
    """The chunk and query caps are payload bounds applied inside the provider."""

    @pytest.mark.asyncio()
    async def test_a_fifty_thousand_character_chunk_is_truncated_to_the_chunk_cap(
        self,
    ) -> None:
        sample = _load_sample_dict()
        sample["grounding"]["generic"][0]["snippets"] = ["Y" * 50_000]

        provider = BraveApiProvider("sentinel-key")
        with _client_patch(
            response=_make_response(content=json.dumps(sample).encode())
        ):
            outcome = await provider.search("q", 3)

        assert isinstance(outcome, ProviderSearchResult)
        assert len(outcome.results[0]["content"]) == DEFAULT_BRAVE_CHUNK_MAX_CHARS

    @pytest.mark.asyncio()
    async def test_a_five_thousand_character_query_is_truncated_to_the_query_cap(
        self,
    ) -> None:
        provider = BraveApiProvider("sentinel-key")
        long_query = "q" * 5_000

        with _client_patch(response=_make_response(content=_load_sample_bytes())) as (
            _client_cls,
            client,
        ):
            response = await run_search_pipeline(
                SearchRequest(
                    query=long_query, num_results=5, promptguard_fail_closed=False
                ),
                providers=[provider],
                config=_INTEGRATION_CONFIG,
            )

        stream_call = client.stream.call_args
        sent_query = stream_call.kwargs["params"]["q"]
        assert len(sent_query) == DEFAULT_BRAVE_QUERY_MAX_CHARS
        # Accepted end to end, not rejected — the bound lives in the
        # provider's outbound copy, never on `SearchRequest.query` itself.
        assert response.results


# ---------------------------------------------------------------------------
# Engine provenance stays distinct — US-011
# ---------------------------------------------------------------------------


class TestEngineProvenanceStaysDistinct:
    """SearXNG's own `brave` sub-engine and Brave's `engine="brave-api"` never merge."""

    @pytest.mark.asyncio()
    async def test_searxng_side_brave_and_brave_api_stay_distinct(self) -> None:
        searxng_shaped = FakeSearchProvider(
            name="searxng",
            paid=False,
            outcome=ProviderSearchResult(
                provider_name="searxng",
                results=[
                    {
                        "title": "SearXNG via its own brave sub-engine",
                        "url": "https://example.com/searxng-brave",
                        "content": "content",
                        "engine": "brave",
                        "date": None,
                    }
                ],
                unresponsive_engines=[],
            ),
        )
        searxng_response = await run_search_pipeline(
            SearchRequest(query="q", num_results=5, promptguard_fail_closed=False),
            providers=[searxng_shaped],
            config=_INTEGRATION_CONFIG,
        )

        brave_provider = BraveApiProvider("sentinel-key")
        with _client_patch(response=_make_response(content=_load_sample_bytes())):
            brave_response = await run_search_pipeline(
                SearchRequest(query="q", num_results=5, promptguard_fail_closed=False),
                providers=[brave_provider],
                config=_INTEGRATION_CONFIG,
            )

        assert searxng_response.results
        assert brave_response.results
        # Both directions, so neither normalization could pass unnoticed.
        assert searxng_response.results[0].engine == "brave"
        assert brave_response.results[0].engine == "brave-api"
        assert searxng_response.results[0].engine != brave_response.results[0].engine
