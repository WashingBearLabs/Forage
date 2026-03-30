"""Tests for services/retrieval/cache.py — URL normalisation, cache round-trip, TTL."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

# Ensure the retrieval service package is importable.
import sys, pathlib  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "services" / "retrieval"))

from cache import (  # noqa: E402
    ContentCache,
    TrustTier,
    _NO_CACHE_TIERS,
    _TRACKING_PARAMS,
    cache_key,
    normalize_url,
)
from models import RetrievedContent, Stage2Verdict, Stage3Verdict  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_content(
    *,
    trust_tier: TrustTier = TrustTier.STANDARD,
    body: str = "Hello world",
) -> RetrievedContent:
    """Build a minimal ``RetrievedContent`` for testing."""
    return RetrievedContent(
        request_id="req-1",
        source_url="https://example.com",
        final_url="https://example.com",
        body=body,
        word_count=2,
        content_type="html",
        trust_score=0.5,
        trust_tier=trust_tier,
        stage2_verdict=Stage2Verdict.CLEAN,
        stage3_verdict=Stage3Verdict.SAFE,
        domain="example.com",
    )


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------


class TestNormalizeUrl:
    """URL normalisation edge-cases."""

    def test_lowercase_scheme_and_host(self) -> None:
        assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_strip_tracking_params(self) -> None:
        url = "https://example.com/page?utm_source=x&utm_medium=y&keep=1"
        assert normalize_url(url) == "https://example.com/page?keep=1"

    def test_strip_all_known_tracking_params(self) -> None:
        parts = "&".join(f"{p}=val" for p in sorted(_TRACKING_PARAMS))
        url = f"https://example.com?{parts}"
        assert normalize_url(url) == "https://example.com"

    def test_strip_fragment(self) -> None:
        assert normalize_url("https://example.com/page#section") == "https://example.com/page"

    def test_trailing_slash_removed(self) -> None:
        assert normalize_url("https://example.com/page/") == "https://example.com/page"

    def test_root_slash_preserved(self) -> None:
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_sort_query_params(self) -> None:
        url = "https://example.com?z=1&a=2&m=3"
        assert normalize_url(url) == "https://example.com?a=2&m=3&z=1"

    def test_combined_normalisation(self) -> None:
        url = "HTTPS://Example.COM/page/?utm_source=tw&b=2&a=1#frag"
        assert normalize_url(url) == "https://example.com/page?a=1&b=2"

    def test_empty_query_string(self) -> None:
        assert normalize_url("https://example.com/page") == "https://example.com/page"

    def test_only_tracking_params_leaves_no_query(self) -> None:
        url = "https://example.com/page?fbclid=abc&gclid=xyz"
        assert normalize_url(url) == "https://example.com/page"

    def test_duplicate_query_values(self) -> None:
        url = "https://example.com?tag=a&tag=b"
        result = normalize_url(url)
        assert "tag=a" in result
        assert "tag=b" in result

    def test_preserves_path_case(self) -> None:
        # Path is case-sensitive per RFC 3986.
        assert normalize_url("https://example.com/CasePath") == "https://example.com/CasePath"

    def test_port_preserved(self) -> None:
        assert normalize_url("https://Example.COM:8080/p") == "https://example.com:8080/p"


# ---------------------------------------------------------------------------
# cache_key
# ---------------------------------------------------------------------------


class TestCacheKey:
    """Cache key derivation."""

    def test_prefix(self) -> None:
        key = cache_key("https://example.com")
        assert key.startswith("ret:")

    def test_sha256(self) -> None:
        normalised = normalize_url("https://example.com")
        expected = f"ret:{hashlib.sha256(normalised.encode()).hexdigest()}"
        assert cache_key("https://example.com") == expected

    def test_same_url_different_tracking_params_same_key(self) -> None:
        assert cache_key("https://example.com?utm_source=a") == cache_key(
            "https://example.com?utm_source=b"
        )

    def test_different_urls_different_keys(self) -> None:
        assert cache_key("https://a.com") != cache_key("https://b.com")


# ---------------------------------------------------------------------------
# ContentCache round-trip
# ---------------------------------------------------------------------------


class TestContentCacheGetPut:
    """Cache get/put with a mocked Valkey client."""

    @pytest.fixture()
    def mock_redis(self) -> AsyncMock:
        client = AsyncMock()
        client.ping = AsyncMock(return_value=True)
        client.get = AsyncMock(return_value=None)
        client.set = AsyncMock(return_value=True)
        client.aclose = AsyncMock()
        return client

    @pytest.fixture()
    def cache(self, mock_redis: AsyncMock) -> ContentCache:
        c = ContentCache(valkey_url="redis://localhost:6379/4")
        c._client = mock_redis
        return c

    # -- get -----------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_get_miss_returns_none(self, cache: ContentCache) -> None:
        result = await cache.get("https://example.com")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_hit_sets_cache_fields(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content()
        mock_redis.get.return_value = content.model_dump_json().encode()

        result = await cache.get("https://example.com")
        assert result is not None
        assert result.cache_hit is True
        assert result.cached_at is not None
        assert isinstance(result.cached_at, datetime)
        assert result.cached_at.tzinfo == UTC

    @pytest.mark.asyncio()
    async def test_get_no_client_returns_none(self) -> None:
        cache = ContentCache()
        assert await cache.get("https://example.com") is None

    @pytest.mark.asyncio()
    async def test_get_error_returns_none(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.side_effect = ConnectionError("down")
        assert await cache.get("https://example.com") is None

    # -- put -----------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_put_standard_tier(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content(trust_tier=TrustTier.STANDARD)
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is True
        mock_redis.set.assert_awaited_once()
        call_kwargs = mock_redis.set.call_args
        # Default TTL = 24h = 86400s
        assert call_kwargs.kwargs["ex"] == 24 * 3600

    @pytest.mark.asyncio()
    async def test_put_trusted_tier(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content(trust_tier=TrustTier.TRUSTED)
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is True

    @pytest.mark.asyncio()
    async def test_put_untrusted_skipped(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content(trust_tier=TrustTier.UNTRUSTED)
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is False
        mock_redis.set.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_put_blocked_skipped(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content(trust_tier=TrustTier.BLOCKED)
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is False
        mock_redis.set.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_put_no_client_returns_false(self) -> None:
        cache = ContentCache()
        content = _make_content()
        assert await cache.put("https://x.com", content, domain="x.com") is False

    @pytest.mark.asyncio()
    async def test_put_error_returns_false(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        mock_redis.set.side_effect = ConnectionError("down")
        content = _make_content()
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is False

    # -- round-trip ----------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_round_trip(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        """Put then get returns equivalent content with cache_hit set."""
        original = _make_content(body="round trip test")

        # Capture what put() writes so get() can return it.
        stored: dict[str, bytes] = {}

        async def fake_set(key: str, value: bytes, *, ex: int) -> bool:
            stored[key] = value
            return True

        async def fake_get(key: str) -> bytes | None:
            return stored.get(key)

        mock_redis.set.side_effect = fake_set
        mock_redis.get.side_effect = fake_get

        await cache.put("https://example.com", original, domain="example.com")
        result = await cache.get("https://example.com")

        assert result is not None
        assert result.body == "round trip test"
        assert result.cache_hit is True
        assert result.cached_at is not None


# ---------------------------------------------------------------------------
# TTL logic — news domains
# ---------------------------------------------------------------------------


class TestTTLLogic:
    """TTL selection based on news domains."""

    @pytest.fixture()
    def mock_redis(self) -> AsyncMock:
        client = AsyncMock()
        client.ping = AsyncMock(return_value=True)
        client.set = AsyncMock(return_value=True)
        client.aclose = AsyncMock()
        return client

    @pytest.fixture()
    def cache(self, mock_redis: AsyncMock) -> ContentCache:
        c = ContentCache()
        c._client = mock_redis
        return c

    @pytest.mark.asyncio()
    async def test_news_domain_1h_ttl(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content()
        news = ["cnn.com", "bbc.co.uk"]
        await cache.put(
            "https://cnn.com/article",
            content,
            domain="cnn.com",
            news_domains=news,
        )
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs.kwargs["ex"] == 1 * 3600

    @pytest.mark.asyncio()
    async def test_non_news_domain_default_ttl(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content()
        news = ["cnn.com"]
        await cache.put(
            "https://example.com/page",
            content,
            domain="example.com",
            news_domains=news,
        )
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs.kwargs["ex"] == 24 * 3600

    @pytest.mark.asyncio()
    async def test_news_domain_case_insensitive(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content()
        await cache.put(
            "https://CNN.com/article",
            content,
            domain="CNN.com",
            news_domains=["cnn.com"],
        )
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs.kwargs["ex"] == 1 * 3600

    @pytest.mark.asyncio()
    async def test_custom_default_ttl(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content()
        await cache.put(
            "https://example.com",
            content,
            ttl_hours=48,
            domain="example.com",
        )
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs.kwargs["ex"] == 48 * 3600

    @pytest.mark.asyncio()
    async def test_empty_news_domains_uses_default(
        self, cache: ContentCache, mock_redis: AsyncMock
    ) -> None:
        content = _make_content()
        await cache.put(
            "https://example.com",
            content,
            domain="example.com",
            news_domains=[],
        )
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs.kwargs["ex"] == 24 * 3600


# ---------------------------------------------------------------------------
# connect / close
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Connection lifecycle."""

    @pytest.mark.asyncio()
    async def test_connect_success(self) -> None:
        with patch("cache.aioredis") as mock_mod:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_mod.from_url.return_value = mock_client

            c = ContentCache()
            ok = await c.connect()
            assert ok is True
            assert c._client is mock_client

    @pytest.mark.asyncio()
    async def test_connect_failure(self) -> None:
        with patch("cache.aioredis") as mock_mod:
            mock_mod.from_url.side_effect = ConnectionError("nope")

            c = ContentCache()
            ok = await c.connect()
            assert ok is False
            assert c._client is None

    @pytest.mark.asyncio()
    async def test_close(self) -> None:
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        c = ContentCache()
        c._client = mock_client
        await c.close()
        mock_client.aclose.assert_awaited_once()
        assert c._client is None
