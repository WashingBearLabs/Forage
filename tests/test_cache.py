"""Tests for cache.py — URL normalisation, cache round-trip, TTL, storage."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import cache as cache_module
from cache import (
    _TRACKING_PARAMS,
    DEFAULT_CACHE_MAX_BYTES,
    DEFAULT_CACHE_MAX_ENTRIES,
    MEBIBYTE,
    CacheConfigurationError,
    CacheMetrics,
    CacheSettings,
    CacheStorage,
    ContentCache,
    InMemoryStorage,
    TrustTier,
    ValkeyStorage,
    cache_key,
    cache_policy_fingerprint,
    cache_settings_from_config,
    normalize_url,
)
from models import RetrievedContent, Stage2Verdict, Stage3Verdict
from tests.fakes import FakeStorage, ManualClock, assert_frozen

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# A plausible derived revision, shaped like `derive_sanitizer_revision`'s
# output. The fingerprint treats it as opaque, so one fixed sample serves every
# call that is not deliberately rotating it.
_SAMPLE_REVISION = "a" * 64


def _fingerprint_at_revision(sanitizer_revision: str) -> str:
    """One retrieval policy, fingerprinted under the given pipeline revision."""
    return cache_policy_fingerprint(
        trusted_domains=[],
        verified_domains=[],
        blocked_domains=[],
        promptguard_threshold=0.85,
        promptguard_fail_closed=True,
        classifier_loaded=True,
        sanitizer_revision=sanitizer_revision,
    )


def _make_content(
    *,
    trust_tier: TrustTier = TrustTier.STANDARD,
    body: str = "Hello world",
    domain: str = "example.com",
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
        domain=domain,
    )


def _mock_valkey_client() -> AsyncMock:
    """The five-command Valkey surface ``ValkeyStorage`` actually issues."""
    client = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.aclose = AsyncMock()
    return client


def _connected_valkey_storage(client: AsyncMock) -> ValkeyStorage:
    """A ``ValkeyStorage`` already holding *client*, skipping the connect dance.

    The seam the Valkey-specific tests below reach through. Policy tests do not
    need it — they run over ``FakeStorage`` — but the failure modes that are
    *Valkey's own* (an operation raising mid-flight, a dropped connection) have
    no meaning on any other storage and are asserted here.
    """
    storage = ValkeyStorage(valkey_url="redis://localhost:6379/4")
    storage._client = client
    return storage


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
        assert (
            normalize_url("https://example.com/page#section")
            == "https://example.com/page"
        )

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
        assert (
            normalize_url("https://example.com/CasePath")
            == "https://example.com/CasePath"
        )

    def test_port_preserved(self) -> None:
        assert (
            normalize_url("https://Example.COM:8080/p") == "https://example.com:8080/p"
        )


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
        expected_input = f"{normalised}:summary"
        expected = f"ret:{hashlib.sha256(expected_input.encode()).hexdigest()}"
        assert cache_key("https://example.com") == expected

    def test_same_url_different_tracking_params_same_key(self) -> None:
        assert cache_key("https://example.com?utm_source=a") == cache_key(
            "https://example.com?utm_source=b"
        )

    def test_different_urls_different_keys(self) -> None:
        assert cache_key("https://a.com") != cache_key("https://b.com")

    def test_different_extract_modes_have_different_keys(self) -> None:
        """Summary entries cannot be returned for a full extraction request."""
        url = "https://example.com"
        assert cache_key(url, extract_mode="summary") != cache_key(
            url,
            extract_mode="full",
        )

    def test_different_policy_fingerprints_have_different_keys(self) -> None:
        """Changing a trust or PromptGuard policy cannot reuse shaped content."""
        url = "https://example.com"
        standard = cache_policy_fingerprint(
            trusted_domains=[],
            verified_domains=[],
            blocked_domains=[],
            promptguard_threshold=0.85,
            promptguard_fail_closed=True,
            classifier_loaded=True,
            sanitizer_revision=_SAMPLE_REVISION,
        )
        trusted = cache_policy_fingerprint(
            trusted_domains=["example.com"],
            verified_domains=[],
            blocked_domains=[],
            promptguard_threshold=0.85,
            promptguard_fail_closed=True,
            classifier_loaded=True,
            sanitizer_revision=_SAMPLE_REVISION,
        )
        assert cache_key(url, policy_fingerprint=standard) != cache_key(
            url,
            policy_fingerprint=trusted,
        )

    def test_classifier_loaded_state_changes_fingerprint(self) -> None:
        """A fail-open body cached while the model was absent misses once loaded."""
        url = "https://example.com"
        model_absent = cache_policy_fingerprint(
            trusted_domains=[],
            verified_domains=[],
            blocked_domains=[],
            promptguard_threshold=0.85,
            promptguard_fail_closed=True,
            classifier_loaded=False,
            sanitizer_revision=_SAMPLE_REVISION,
        )
        model_loaded = cache_policy_fingerprint(
            trusted_domains=[],
            verified_domains=[],
            blocked_domains=[],
            promptguard_threshold=0.85,
            promptguard_fail_closed=True,
            classifier_loaded=True,
            sanitizer_revision=_SAMPLE_REVISION,
        )
        assert cache_key(url, policy_fingerprint=model_absent) != cache_key(
            url,
            policy_fingerprint=model_loaded,
        )

    def test_a_rotated_sanitizer_revision_changes_the_fingerprint(self) -> None:
        """A pipeline change cannot replay content the old pipeline sanitized.

        `pipeline/contract.py`'s docstring states the rule — a contract change
        must invalidate cached extractions sanitized under the old one — and
        until US-003 nothing enforced it: the key mixed in every caller-supplied
        knob but not the revision, so a deploy that rotated it kept serving the
        previous code's output for up to a TTL.
        """
        url = "https://example.com"
        before = _fingerprint_at_revision("0" * 64)
        after = _fingerprint_at_revision("1" * 64)

        assert before != after
        assert cache_key(url, policy_fingerprint=before) != cache_key(
            url,
            policy_fingerprint=after,
        )

    def test_an_unchanged_sanitizer_revision_keeps_the_fingerprint_stable(
        self,
    ) -> None:
        """The revision is an input, not a nonce — the same one keys the same."""
        assert _fingerprint_at_revision(_SAMPLE_REVISION) == _fingerprint_at_revision(
            _SAMPLE_REVISION
        )


# ---------------------------------------------------------------------------
# ContentCache round-trip
# ---------------------------------------------------------------------------


class TestContentCacheGetPut:
    """Cache get/put over the storage seam."""

    @pytest.fixture()
    def storage(self) -> FakeStorage:
        return FakeStorage()

    @pytest.fixture()
    def cache(self, storage: FakeStorage) -> ContentCache:
        return ContentCache(storage=storage)

    # -- get -----------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_get_miss_returns_none(self, cache: ContentCache) -> None:
        result = await cache.get("https://example.com")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_hit_sets_cache_fields(self, cache: ContentCache) -> None:
        await cache.put("https://example.com", _make_content(), domain="example.com")

        result = await cache.get("https://example.com")
        assert result is not None
        assert result.cache_hit is True
        assert result.cached_at is not None
        assert isinstance(result.cached_at, datetime)
        assert result.cached_at.tzinfo == UTC

    @pytest.mark.asyncio()
    async def test_summary_entry_is_not_returned_for_full_request(
        self, cache: ContentCache
    ) -> None:
        """A full request misses a cache entry produced by summary extraction."""
        await cache.put(
            "https://example.com",
            _make_content(body="summary body"),
            extract_mode="summary",
            domain="example.com",
        )

        assert await cache.get("https://example.com", extract_mode="full") is None

    @pytest.mark.asyncio()
    async def test_get_no_client_returns_none(self) -> None:
        cache = ContentCache()
        assert await cache.get("https://example.com") is None

    @pytest.mark.asyncio()
    async def test_get_error_returns_none(self) -> None:
        mock_redis = _mock_valkey_client()
        mock_redis.get.side_effect = ConnectionError("down")
        cache = ContentCache(storage=_connected_valkey_storage(mock_redis))
        assert await cache.get("https://example.com") is None

    @pytest.mark.asyncio()
    async def test_get_rejects_entries_older_than_current_policy(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        """A lowered TTL expires content before its original storage expiry."""
        content = _make_content().model_copy(
            update={"retrieved_at": datetime.now(UTC) - timedelta(hours=2)}
        )
        await cache.put("https://example.com", content, domain="example.com")

        assert await cache.get("https://example.com", ttl_hours=1) is None
        assert storage.delete_calls == 1

    @pytest.mark.asyncio()
    async def test_get_ttl_zero_skips_read_and_deletes_variant(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        """Disabled caching cannot revive an entry from an earlier TTL policy."""
        assert await cache.get("https://example.com", ttl_hours=0) is None
        assert storage.get_calls == 0
        assert storage.delete_calls == 1

    # -- put -----------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_put_standard_tier(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        content = _make_content(trust_tier=TrustTier.STANDARD)
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is True
        assert storage.set_calls == 1
        # Default TTL = 24h = 86400s
        assert storage.last_ttl_seconds == 24 * 3600

    @pytest.mark.asyncio()
    async def test_put_trusted_tier(self, cache: ContentCache) -> None:
        content = _make_content(trust_tier=TrustTier.TRUSTED)
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is True

    @pytest.mark.asyncio()
    async def test_put_untrusted_skipped(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        content = _make_content(trust_tier=TrustTier.UNTRUSTED)
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is False
        assert storage.set_calls == 0

    @pytest.mark.asyncio()
    async def test_put_blocked_skipped(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        content = _make_content(trust_tier=TrustTier.BLOCKED)
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is False
        assert storage.set_calls == 0

    @pytest.mark.asyncio()
    async def test_put_no_client_returns_false(self) -> None:
        cache = ContentCache()
        content = _make_content()
        assert await cache.put("https://x.com", content, domain="x.com") is False

    @pytest.mark.asyncio()
    async def test_put_error_returns_false(self) -> None:
        mock_redis = _mock_valkey_client()
        mock_redis.set.side_effect = ConnectionError("down")
        cache = ContentCache(storage=_connected_valkey_storage(mock_redis))
        content = _make_content()
        ok = await cache.put("https://example.com", content, domain="example.com")
        assert ok is False

    @pytest.mark.asyncio()
    async def test_put_ttl_zero_skips_write_and_deletes_variant(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        """No storage ever receives a zero TTL when caching is disabled."""
        ok = await cache.put(
            "https://example.com",
            _make_content(),
            ttl_hours=0,
            domain="example.com",
        )
        assert ok is False
        assert storage.set_calls == 0
        assert storage.delete_calls == 1

    # -- round-trip ----------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_round_trip(self, cache: ContentCache) -> None:
        """Put then get returns equivalent content with cache_hit set."""
        original = _make_content(body="round trip test")

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
    def storage(self) -> FakeStorage:
        return FakeStorage()

    @pytest.fixture()
    def cache(self, storage: FakeStorage) -> ContentCache:
        return ContentCache(storage=storage)

    @pytest.mark.asyncio()
    async def test_news_domain_1h_ttl(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        content = _make_content()
        news = ["cnn.com", "bbc.co.uk"]
        await cache.put(
            "https://cnn.com/article",
            content,
            domain="cnn.com",
            news_domains=news,
        )
        assert storage.last_ttl_seconds == 1 * 3600

    @pytest.mark.asyncio()
    async def test_non_news_domain_default_ttl(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        content = _make_content()
        news = ["cnn.com"]
        await cache.put(
            "https://example.com/page",
            content,
            domain="example.com",
            news_domains=news,
        )
        assert storage.last_ttl_seconds == 24 * 3600

    @pytest.mark.asyncio()
    async def test_news_domain_case_insensitive(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        content = _make_content()
        await cache.put(
            "https://CNN.com/article",
            content,
            domain="CNN.com",
            news_domains=["cnn.com"],
        )
        assert storage.last_ttl_seconds == 1 * 3600

    @pytest.mark.asyncio()
    async def test_custom_default_ttl(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        content = _make_content()
        await cache.put(
            "https://example.com",
            content,
            ttl_hours=48,
            domain="example.com",
        )
        assert storage.last_ttl_seconds == 48 * 3600

    @pytest.mark.asyncio()
    async def test_empty_news_domains_uses_default(
        self, cache: ContentCache, storage: FakeStorage
    ) -> None:
        content = _make_content()
        await cache.put(
            "https://example.com",
            content,
            domain="example.com",
            news_domains=[],
        )
        assert storage.last_ttl_seconds == 24 * 3600


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

            storage = ValkeyStorage()
            c = ContentCache(storage=storage)
            ok = await c.connect()
            assert ok is True
            assert storage._client is mock_client

    @pytest.mark.asyncio()
    async def test_connect_failure(self) -> None:
        with patch("cache.aioredis") as mock_mod:
            mock_mod.from_url.side_effect = ConnectionError("nope")

            storage = ValkeyStorage()
            c = ContentCache(storage=storage)
            ok = await c.connect()
            assert ok is False
            assert storage._client is None

    @pytest.mark.asyncio()
    async def test_close(self) -> None:
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        storage = _connected_valkey_storage(mock_client)
        c = ContentCache(storage=storage)
        await c.close()
        mock_client.aclose.assert_awaited_once()
        assert storage._client is None


# ---------------------------------------------------------------------------
# Drop detection, bounded reconnect, and backoff (US-002)
# ---------------------------------------------------------------------------


class TestReconnect:
    """A dropped or never-connected cache self-heals at a bounded, backed-off cost."""

    @pytest.mark.asyncio()
    async def test_operation_failure_marks_cache_disconnected(self) -> None:
        """A drop that begins *after* a successful connect clears the client."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=ConnectionError("dropped"))
        metrics = CacheMetrics()
        storage = ValkeyStorage(metrics=metrics)
        storage._client = mock_redis
        c = ContentCache(storage=storage, metrics=metrics)

        assert c.connected is True
        result = await c.get("https://example.com")

        assert result is None
        assert c.connected is False
        assert c._metrics.operation_failures == 1

    @pytest.mark.asyncio()
    async def test_get_reconnects_when_disconnected_and_backoff_elapsed(self) -> None:
        with patch("cache.aioredis") as mock_mod:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_client.get = AsyncMock(return_value=None)
            mock_mod.from_url.return_value = mock_client

            c = ContentCache()
            result = await c.get("https://example.com")

        assert result is None
        assert c.connected is True
        assert c._metrics.reconnect_attempts == 1
        assert c._metrics.reconnect_successes == 1

    @pytest.mark.asyncio()
    async def test_put_reconnects_when_disconnected_and_backoff_elapsed(self) -> None:
        with patch("cache.aioredis") as mock_mod:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_client.set = AsyncMock(return_value=True)
            mock_mod.from_url.return_value = mock_client

            c = ContentCache()
            ok = await c.put(
                "https://example.com",
                _make_content(),
                domain="example.com",
            )

        assert ok is True
        assert c.connected is True
        assert c._metrics.reconnect_attempts == 1

    @pytest.mark.asyncio()
    async def test_delete_reconnects_when_disconnected_and_backoff_elapsed(
        self,
    ) -> None:
        with patch("cache.aioredis") as mock_mod:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_client.delete = AsyncMock(return_value=1)
            mock_mod.from_url.return_value = mock_client

            c = ContentCache()
            ok = await c.delete("https://example.com")

        assert ok is True
        assert c.connected is True
        assert c._metrics.reconnect_attempts == 1

    @pytest.mark.asyncio()
    async def test_stale_entry_eviction_works_after_reconnect(self) -> None:
        """``_delete_key`` (eviction) is reachable via ``get()``'s own reconnect."""
        stale = _make_content().model_copy(
            update={"retrieved_at": datetime.now(UTC) - timedelta(hours=2)}
        )
        with patch("cache.aioredis") as mock_mod:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_client.get = AsyncMock(return_value=stale.model_dump_json().encode())
            mock_client.delete = AsyncMock(return_value=1)
            mock_mod.from_url.return_value = mock_client

            c = ContentCache()
            result = await c.get("https://example.com", ttl_hours=1)

        assert result is None
        mock_client.delete.assert_awaited_once()
        assert c.connected is True

    @pytest.mark.asyncio()
    async def test_backoff_limits_repeated_connection_attempts(self) -> None:
        """>= 10 consecutive ``get()`` calls within the backoff window try once."""
        with patch("cache.aioredis") as mock_mod:
            mock_mod.from_url.side_effect = ConnectionError("down")
            c = ContentCache()

            for _ in range(10):
                assert await c.get("https://example.com") is None

        assert mock_mod.from_url.call_count == 1
        assert c._metrics.reconnect_attempts == 1
        assert c._metrics.reconnect_failures == 1

    @pytest.mark.asyncio()
    async def test_reconnect_is_bounded_by_a_two_second_deadline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A black-holed Valkey cannot hang a reconnect past the deadline."""
        monkeypatch.setattr(cache_module, "_RECONNECT_TIMEOUT_S", 0.05)

        async def hang_forever() -> bool:
            await asyncio.sleep(10)
            return True

        with patch("cache.aioredis") as mock_mod:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=hang_forever)
            mock_mod.from_url.return_value = mock_client

            storage = ValkeyStorage()
            started = time.monotonic()
            ok = await storage._attempt_connect()
            elapsed = time.monotonic() - started

        assert ok is False
        assert elapsed < 1.0

    @pytest.mark.asyncio()
    async def test_concurrent_calls_open_at_most_one_connection(self) -> None:
        """A caller finding the reconnect lock held returns a miss immediately."""
        connect_started = asyncio.Event()
        release_connect = asyncio.Event()
        attempts = 0

        async def slow_ping() -> bool:
            nonlocal attempts
            attempts += 1
            connect_started.set()
            await release_connect.wait()
            return True

        with patch("cache.aioredis") as mock_mod:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=slow_ping)
            mock_client.get = AsyncMock(return_value=None)
            mock_mod.from_url.return_value = mock_client

            c = ContentCache()
            first = asyncio.create_task(c.get("https://example.com"))
            await connect_started.wait()

            concurrent_results = await asyncio.gather(
                *(c.get("https://example.com") for _ in range(5))
            )
            release_connect.set()
            first_result = await first

        assert first_result is None
        assert concurrent_results == [None] * 5
        assert attempts == 1
        assert c._metrics.reconnect_attempts == 1

    @pytest.mark.asyncio()
    async def test_connect_failure_never_logs_url_or_secret(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No code path logs a password-bearing VALKEY_URL.

        Two paths carry the URL and both are covered here: ``ContentCache``'s
        own connect failure (closed reason vocabulary), and service startup,
        which reads ``VALKEY_URL`` straight from the operator's environment
        and hands it to the cache. The entrypoint no longer builds that URL
        from a secret store — it arrives already populated — so startup is the
        first place a careless log line would leak it.
        """
        c = ContentCache(valkey_url="redis://:secret@unreachable:6379/4")

        with caplog.at_level(logging.WARNING, logger="cache"):
            ok = await c.connect()

        assert ok is False
        assert "secret" not in caplog.text
        assert "unreachable" not in caplog.text

        # --- Startup path: run the real lifespan with a credentialed URL. ---
        from fastapi import FastAPI

        import retrieval_app

        password = "hunter2-startup-password"
        startup_url = f"redis://:{password}@unreachable-startup-host:6379/4"
        # The env var itself since US-002 removed the module constant this
        # line used to patch: startup reads `VALKEY_URL` per start, so this
        # now exercises the operator's real path rather than a test seam.
        monkeypatch.setenv("VALKEY_URL", startup_url)
        # Keep the model load out of it: this test is about log content.
        monkeypatch.setattr(
            retrieval_app,
            "PromptGuardClassifier",
            lambda: MagicMock(load=MagicMock(return_value=False), loaded=False),
        )

        caplog.clear()
        probe_app = FastAPI()
        with caplog.at_level(logging.DEBUG):
            async with retrieval_app.lifespan(probe_app):
                pass

        # Canary: the startup path really did log (and really did fail to
        # reach the credentialed URL), so the absence checks below are not
        # passing vacuously on an empty capture.
        assert "Content cache not available at startup" in caplog.text

        assert password not in caplog.text
        assert startup_url not in caplog.text
        assert "unreachable-startup-host" not in caplog.text


# ---------------------------------------------------------------------------
# Storage configuration (US-001)
# ---------------------------------------------------------------------------


class TestCacheSettings:
    """The ``cache:`` config block, on the ``extraction:`` block's pattern."""

    def test_defaults_are_256_entries_and_32_mib(self) -> None:
        """The documented budget: a quarter of the container's ~128 MiB headroom."""
        settings = cache_settings_from_config({})

        assert settings.max_entries == 256
        assert settings.max_bytes == 32 * MEBIBYTE
        assert DEFAULT_CACHE_MAX_ENTRIES == 256
        assert DEFAULT_CACHE_MAX_BYTES == 32 * MEBIBYTE

    def test_values_from_the_cache_block_are_used(self) -> None:
        settings = cache_settings_from_config(
            {"cache": {"max_entries": 32, "max_bytes": 4 * MEBIBYTE}}
        )

        assert settings.max_entries == 32
        assert settings.max_bytes == 4 * MEBIBYTE

    def test_partial_block_keeps_the_other_default(self) -> None:
        settings = cache_settings_from_config({"cache": {"max_entries": 8}})

        assert settings.max_entries == 8
        assert settings.max_bytes == DEFAULT_CACHE_MAX_BYTES

    def test_settings_are_frozen(self) -> None:
        """A bound nothing can rewrite after validation."""
        assert_frozen(cache_settings_from_config({}), "max_entries", 1)

    @pytest.mark.parametrize(
        "block",
        [
            {"max_entries": 0},
            {"max_entries": 4097},
            {"max_bytes": 1024},
            {"max_bytes": 129 * MEBIBYTE},
        ],
    )
    def test_out_of_range_values_are_refused(self, block: dict[str, int]) -> None:
        """These are memory bounds — a typo fails the boot, never widens them."""
        with pytest.raises(CacheConfigurationError):
            cache_settings_from_config({"cache": block})

    @pytest.mark.parametrize("value", [True, 1.5, "256", None])
    def test_non_integer_values_are_refused(self, value: object) -> None:
        """``True`` is an ``int`` in Python and is refused anyway."""
        with pytest.raises(CacheConfigurationError):
            cache_settings_from_config({"cache": {"max_entries": value}})

    def test_a_non_mapping_cache_block_is_refused(self) -> None:
        with pytest.raises(CacheConfigurationError):
            cache_settings_from_config({"cache": [256, 32]})

    def test_the_shipped_config_yaml_pins_the_documented_defaults(self) -> None:
        """``config.yaml`` and the code defaults do not get to drift apart."""
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        shipped = yaml.safe_load(config_path.read_text())

        settings = cache_settings_from_config(shipped)
        assert settings.max_entries == DEFAULT_CACHE_MAX_ENTRIES
        assert settings.max_bytes == DEFAULT_CACHE_MAX_BYTES


# ---------------------------------------------------------------------------
# Policy parity across storages (US-001)
# ---------------------------------------------------------------------------


_PARITY_URL = "https://example.com/page"


@dataclass(frozen=True, slots=True)
class _ParityHarness:
    """One ``ContentCache`` and the storage its policy is running over."""

    cache: ContentCache
    storage: CacheStorage
    clock: ManualClock
    metrics: CacheMetrics

    async def stored(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
    ) -> bytes | None:
        """Read the raw bytes behind *url*, bypassing the policy layer.

        How a parity test asserts a *purge* without asking each storage a
        different question about its internals.
        """
        return await self.storage.get(
            cache_key(
                url,
                extract_mode=extract_mode,
                policy_fingerprint=policy_fingerprint,
            )
        )


@pytest.fixture(params=["fake_storage", "in_memory_storage"])
def harness(request: pytest.FixtureRequest) -> _ParityHarness:
    """The same policy layer, once per storage implementation.

    ``FakeStorage`` is the test double and ``InMemoryStorage`` the real thing;
    running the identical assertions over both is what makes single-sourced
    policy a proof rather than a claim. The Valkey storage is exercised
    separately — its failure modes, not its policy, are what differ.
    """
    clock = ManualClock()
    metrics = CacheMetrics()
    storage: CacheStorage = (
        FakeStorage(metrics=metrics, clock=clock)
        if request.param == "fake_storage"
        else InMemoryStorage(metrics=metrics, clock=clock)
    )
    return _ParityHarness(
        cache=ContentCache(storage=storage, metrics=metrics),
        storage=storage,
        clock=clock,
        metrics=metrics,
    )


class TestPolicyParityAcrossStorages:
    """Five security behaviours plus storage TTL expiry, over both storages."""

    # -- 1. trust-tier refusal ----------------------------------------------

    @pytest.mark.parametrize("tier", [TrustTier.UNTRUSTED, TrustTier.BLOCKED])
    async def test_no_cache_tiers_never_reach_the_storage(
        self, harness: _ParityHarness, tier: TrustTier
    ) -> None:
        """Defence in depth: the orchestrator refuses too, and so does this."""
        ok = await harness.cache.put(
            _PARITY_URL,
            _make_content(trust_tier=tier),
            domain="example.com",
        )

        assert ok is False
        assert await harness.stored(_PARITY_URL) is None

    @pytest.mark.parametrize("tier", [TrustTier.STANDARD, TrustTier.TRUSTED])
    async def test_cacheable_tiers_are_stored(
        self, harness: _ParityHarness, tier: TrustTier
    ) -> None:
        ok = await harness.cache.put(
            _PARITY_URL,
            _make_content(trust_tier=tier),
            domain="example.com",
        )

        assert ok is True
        assert await harness.stored(_PARITY_URL) is not None

    # -- 2. read-time freshness revalidation --------------------------------

    async def test_a_lowered_ttl_expires_content_before_its_storage_expiry(
        self, harness: _ParityHarness
    ) -> None:
        """The *caller's* current policy decides, not the TTL at write time."""
        stale = _make_content().model_copy(
            update={"retrieved_at": datetime.now(UTC) - timedelta(hours=2)}
        )
        await harness.cache.put(_PARITY_URL, stale, domain="example.com")

        assert await harness.cache.get(_PARITY_URL, ttl_hours=24) is not None
        assert await harness.cache.get(_PARITY_URL, ttl_hours=1) is None
        assert await harness.stored(_PARITY_URL) is None

    async def test_a_news_domain_shortens_the_read_time_freshness_window(
        self, harness: _ParityHarness
    ) -> None:
        """News goes stale fast: one hour, whatever the caller asked for."""
        url = "https://cnn.com/article"
        aging = _make_content(domain="cnn.com").model_copy(
            update={"retrieved_at": datetime.now(UTC) - timedelta(hours=2)}
        )
        await harness.cache.put(url, aging, domain="cnn.com", ttl_hours=24)

        assert await harness.cache.get(url, ttl_hours=24) is not None
        assert (
            await harness.cache.get(url, ttl_hours=24, news_domains=["cnn.com"]) is None
        )

    # -- 3. tz-naive rejection ----------------------------------------------

    async def test_a_tz_naive_entry_is_refused_and_purged(
        self, harness: _ParityHarness
    ) -> None:
        """An entry whose age cannot be computed is not an entry."""
        naive = _make_content().model_copy(
            update={"retrieved_at": datetime.now(UTC).replace(tzinfo=None)}
        )
        await harness.cache.put(_PARITY_URL, naive, domain="example.com")
        assert await harness.stored(_PARITY_URL) is not None

        assert await harness.cache.get(_PARITY_URL) is None
        assert await harness.stored(_PARITY_URL) is None

    # -- 4. zero-TTL purge ---------------------------------------------------

    async def test_a_zero_ttl_read_purges_the_variant_without_reading_it(
        self, harness: _ParityHarness
    ) -> None:
        await harness.cache.put(_PARITY_URL, _make_content(), domain="example.com")

        assert await harness.cache.get(_PARITY_URL, ttl_hours=0) is None
        assert await harness.stored(_PARITY_URL) is None

    async def test_a_zero_ttl_write_stores_nothing_and_purges(
        self, harness: _ParityHarness
    ) -> None:
        await harness.cache.put(_PARITY_URL, _make_content(), domain="example.com")

        ok = await harness.cache.put(
            _PARITY_URL,
            _make_content(),
            ttl_hours=0,
            domain="example.com",
        )

        assert ok is False
        assert await harness.stored(_PARITY_URL) is None

    # -- 5. policy-fingerprint keying ---------------------------------------

    async def test_a_changed_policy_fingerprint_does_not_reuse_shaped_content(
        self, harness: _ParityHarness
    ) -> None:
        """Content shaped under one policy is invisible to another."""
        model_absent = cache_policy_fingerprint(
            trusted_domains=[],
            verified_domains=[],
            blocked_domains=[],
            promptguard_threshold=0.85,
            promptguard_fail_closed=True,
            classifier_loaded=False,
            sanitizer_revision=_SAMPLE_REVISION,
        )
        model_loaded = cache_policy_fingerprint(
            trusted_domains=[],
            verified_domains=[],
            blocked_domains=[],
            promptguard_threshold=0.85,
            promptguard_fail_closed=True,
            classifier_loaded=True,
            sanitizer_revision=_SAMPLE_REVISION,
        )
        await harness.cache.put(
            _PARITY_URL,
            _make_content(),
            policy_fingerprint=model_absent,
            domain="example.com",
        )

        assert (
            await harness.cache.get(_PARITY_URL, policy_fingerprint=model_absent)
            is not None
        )
        miss = await harness.cache.get(_PARITY_URL, policy_fingerprint=model_loaded)
        assert miss is None

    async def test_a_rotated_sanitizer_revision_misses_the_cached_entry(
        self, harness: _ParityHarness
    ) -> None:
        """Rotation invalidates: the stored entry survives, the read misses.

        The same proof as the sibling above, for the input US-003 added. Note
        what it asserts about the *storage*: nothing is deleted, because the
        old pipeline's entry is not wrong, merely unreachable — it ages out on
        its own TTL while the new revision fills a key of its own.
        """
        before = _fingerprint_at_revision("0" * 64)
        after = _fingerprint_at_revision("1" * 64)
        await harness.cache.put(
            _PARITY_URL,
            _make_content(),
            policy_fingerprint=before,
            domain="example.com",
        )

        assert (
            await harness.cache.get(_PARITY_URL, policy_fingerprint=before) is not None
        )
        assert await harness.cache.get(_PARITY_URL, policy_fingerprint=after) is None

    async def test_extract_mode_keys_the_variant(self, harness: _ParityHarness) -> None:
        await harness.cache.put(
            _PARITY_URL,
            _make_content(body="summary body"),
            extract_mode="summary",
            domain="example.com",
        )

        assert await harness.cache.get(_PARITY_URL, extract_mode="full") is None

    # -- 6. storage-level TTL expiry (the one genuine difference) ------------

    async def test_the_storage_expires_an_entry_on_its_own_clock(
        self, harness: _ParityHarness
    ) -> None:
        """Policy-only parity is identical by construction; this is not.

        Wall-clock time does not move here, so the policy layer still considers
        the entry perfectly fresh — the miss comes from the storage's own TTL
        and nowhere else.
        """
        await harness.cache.put(
            _PARITY_URL,
            _make_content(),
            domain="example.com",
            ttl_hours=2,
        )
        assert await harness.cache.get(_PARITY_URL, ttl_hours=2) is not None

        harness.clock.advance(2 * 3600 + 1)

        assert await harness.cache.get(_PARITY_URL, ttl_hours=2) is None

    async def test_an_entry_survives_right_up_to_its_storage_deadline(
        self, harness: _ParityHarness
    ) -> None:
        await harness.cache.put(
            _PARITY_URL,
            _make_content(),
            domain="example.com",
            ttl_hours=2,
        )

        harness.clock.advance(2 * 3600 - 1)

        assert await harness.cache.get(_PARITY_URL, ttl_hours=2) is not None

    # -- the seam itself -----------------------------------------------------

    async def test_a_storage_backed_cache_reports_connected_and_pings(
        self, harness: _ParityHarness
    ) -> None:
        assert await harness.cache.connect() is True
        assert harness.cache.connected is True
        assert await harness.cache.ping_if_due() is True
        assert harness.cache.storage is harness.storage

    async def test_storage_hit_and_miss_counters_move(
        self, harness: _ParityHarness
    ) -> None:
        assert await harness.cache.get(_PARITY_URL) is None
        assert harness.metrics.storage_misses == 1
        assert harness.metrics.storage_hits == 0

        await harness.cache.put(_PARITY_URL, _make_content(), domain="example.com")
        assert await harness.cache.get(_PARITY_URL) is not None

        assert harness.metrics.storage_hits == 1
        assert harness.metrics.storage_misses == 1


def test_every_storage_satisfies_the_cache_storage_protocol() -> None:
    """Conformance asserted where the type checker can see it.

    ``CacheStorage`` is a structural protocol, so the real gate is this
    annotated assignment under ``pyright --strict``; the runtime assertion just
    keeps the statement executing.
    """
    storages: list[CacheStorage] = [
        ValkeyStorage(),
        InMemoryStorage(),
        FakeStorage(),
    ]

    assert len(storages) == 3


# ---------------------------------------------------------------------------
# InMemoryStorage bounds and eviction (US-001)
# ---------------------------------------------------------------------------


def _memory_storage(
    *,
    max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
    max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
    metrics: CacheMetrics | None = None,
    clock: ManualClock | None = None,
) -> InMemoryStorage:
    """An ``InMemoryStorage`` with hand-picked bounds and a controllable clock."""
    return InMemoryStorage(
        settings=CacheSettings(max_entries=max_entries, max_bytes=max_bytes),
        metrics=metrics,
        clock=clock if clock is not None else ManualClock(),
    )


class TestInMemoryStorageBounds:
    """Both bounds, the eviction ordering, and the oversize skip."""

    async def test_values_are_stored_as_serialised_bytes(self) -> None:
        """Exact byte accounting, and no caller ever holds the cached object."""
        storage = _memory_storage()

        await storage.set("k", '{"a":1}', ttl_seconds=60)

        assert await storage.get("k") == b'{"a":1}'
        assert storage.total_bytes == 7
        assert storage.entry_count == 1

    async def test_the_entry_count_bound_evicts_the_least_recently_used(self) -> None:
        metrics = CacheMetrics()
        storage = _memory_storage(max_entries=2, metrics=metrics)

        await storage.set("a", "aaa", ttl_seconds=60)
        await storage.set("b", "bbb", ttl_seconds=60)
        await storage.set("c", "ccc", ttl_seconds=60)

        assert storage.entry_count == 2
        assert await storage.get("a") is None
        assert await storage.get("b") == b"bbb"
        assert await storage.get("c") == b"ccc"
        assert metrics.storage_evictions == 1

    async def test_the_byte_bound_evicts_until_the_total_fits(self) -> None:
        metrics = CacheMetrics()
        storage = _memory_storage(max_entries=100, max_bytes=30, metrics=metrics)

        await storage.set("a", "a" * 20, ttl_seconds=60)
        await storage.set("b", "b" * 20, ttl_seconds=60)

        assert storage.entry_count == 1
        assert storage.total_bytes == 20
        assert await storage.get("a") is None
        assert await storage.get("b") == b"b" * 20
        assert metrics.storage_evictions == 1

    async def test_expired_entries_are_purged_before_a_live_one_is_evicted(
        self,
    ) -> None:
        """Pure LRU would discard the fresh entry and keep the dead one.

        ``live`` is written first, so it is the least-recently-used of the two
        when the third write crosses the bound. Only expired-first eviction
        keeps it.
        """
        clock = ManualClock()
        metrics = CacheMetrics()
        storage = _memory_storage(max_entries=2, metrics=metrics, clock=clock)

        await storage.set("live", "live", ttl_seconds=3600)
        await storage.set("dead", "dead", ttl_seconds=10)
        clock.advance(20)

        await storage.set("new", "new", ttl_seconds=3600)

        assert await storage.get("live") == b"live"
        assert await storage.get("dead") is None
        assert await storage.get("new") == b"new"
        assert storage.entry_count == 2
        assert metrics.storage_evictions == 1

    async def test_reading_an_entry_makes_it_the_most_recently_used(self) -> None:
        storage = _memory_storage(max_entries=2)

        await storage.set("a", "aaa", ttl_seconds=60)
        await storage.set("b", "bbb", ttl_seconds=60)
        assert await storage.get("a") == b"aaa"

        await storage.set("c", "ccc", ttl_seconds=60)

        assert await storage.get("a") == b"aaa"
        assert await storage.get("b") is None

    async def test_an_entry_larger_than_the_byte_bound_is_skipped_not_stored(
        self,
    ) -> None:
        """Storing it would evict the whole cache to make room for one page."""
        metrics = CacheMetrics()
        storage = _memory_storage(max_entries=100, max_bytes=64, metrics=metrics)

        ok = await storage.set("huge", "x" * 65, ttl_seconds=60)

        assert ok is False
        assert storage.entry_count == 0
        assert storage.total_bytes == 0
        assert await storage.get("huge") is None
        assert metrics.storage_oversize_skips == 1
        assert metrics.storage_evictions == 0

    async def test_an_oversized_write_discards_the_entry_it_supersedes(self) -> None:
        """A skipped write must not leave the previous payload serving."""
        storage = _memory_storage(max_entries=100, max_bytes=64)
        await storage.set("k", "small", ttl_seconds=60)

        assert await storage.set("k", "x" * 65, ttl_seconds=60) is False

        assert await storage.get("k") is None
        assert storage.total_bytes == 0

    async def test_an_entry_exactly_at_the_byte_bound_is_stored(self) -> None:
        metrics = CacheMetrics()
        storage = _memory_storage(max_entries=100, max_bytes=64, metrics=metrics)

        assert await storage.set("k", "x" * 64, ttl_seconds=60) is True
        assert metrics.storage_oversize_skips == 0

    async def test_rewriting_a_key_does_not_double_count_its_bytes(self) -> None:
        storage = _memory_storage()

        await storage.set("k", "a" * 10, ttl_seconds=60)
        await storage.set("k", "b" * 4, ttl_seconds=60)

        assert storage.entry_count == 1
        assert storage.total_bytes == 4

    async def test_an_expired_read_is_a_miss_not_an_eviction(self) -> None:
        """Nothing was under pressure; the entry simply aged out."""
        clock = ManualClock()
        metrics = CacheMetrics()
        storage = _memory_storage(metrics=metrics, clock=clock)
        await storage.set("k", "value", ttl_seconds=10)

        clock.advance(11)

        assert await storage.get("k") is None
        assert metrics.storage_misses == 1
        assert metrics.storage_evictions == 0
        assert storage.entry_count == 0
        assert storage.total_bytes == 0

    async def test_a_non_positive_ttl_stores_nothing(self) -> None:
        storage = _memory_storage()
        await storage.set("k", "value", ttl_seconds=60)

        assert await storage.set("k", "value", ttl_seconds=0) is False

        assert storage.entry_count == 0
        assert await storage.get("k") is None

    async def test_delete_frees_the_bytes_and_always_accepts(self) -> None:
        storage = _memory_storage()
        await storage.set("k", "value", ttl_seconds=60)

        assert await storage.delete("k") is True
        assert await storage.delete("absent") is True
        assert storage.entry_count == 0
        assert storage.total_bytes == 0

    async def test_close_drops_every_entry(self) -> None:
        storage = _memory_storage()
        await storage.set("k", "value", ttl_seconds=60)

        await storage.close()

        assert storage.entry_count == 0
        assert storage.total_bytes == 0

    async def test_the_storage_is_always_connected(self) -> None:
        """There is no connection to lose, so ``/health`` never degrades on it."""
        storage = _memory_storage()

        assert storage.connected is True
        assert await storage.connect() is True
        assert await storage.ping_if_due() is True

    async def test_concurrent_writes_leave_the_accounting_exact(self) -> None:
        """Synchronous mutations: no ``await`` sits inside one, so none interleave."""
        storage = _memory_storage(max_entries=64, max_bytes=MEBIBYTE)

        await asyncio.gather(
            *(storage.set(f"k{i}", "x" * 100, ttl_seconds=60) for i in range(64))
        )

        assert storage.entry_count == 64
        assert storage.total_bytes == 64 * 100
