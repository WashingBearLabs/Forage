"""Valkey-backed content cache for the retrieval sidecar.

Connects to Valkey DB 4. Stores serialised ``RetrievedContent`` objects with
TTL-based expiry keyed by SHA-256 of the normalised URL, extraction mode, and
the trust/PromptGuard policy that shaped the content. News domains use the
shorter of their one-hour TTL and the caller's effective TTL. A zero TTL
disables both reads and writes for the matching cache variant.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import redis.asyncio as aioredis  # type: ignore[import-untyped]

from models import RetrievedContent, TrustTier

logger = logging.getLogger(__name__)

# Reconnect backoff: starts at 1s, doubles on each failed attempt, caps at 30s.
_RECONNECT_INITIAL_BACKOFF_S = 1.0
_RECONNECT_MAX_BACKOFF_S = 30.0
# Bounded deadline for a reconnect attempt (connect + ping).
_RECONNECT_TIMEOUT_S = 2.0

# Tracking query parameters stripped during normalisation.
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
        "source",
    }
)

# Trust tiers that must never be cached.
_NO_CACHE_TIERS: frozenset[TrustTier] = frozenset(
    {
        TrustTier.UNTRUSTED,
        TrustTier.BLOCKED,
    }
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Normalise a URL for cache-key generation.

    * Lowercase scheme and host
    * Strip tracking query parameters
    * Strip URL fragment
    * Normalise trailing slash (remove unless root ``/``)
    * Sort remaining query parameters alphabetically
    """
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Strip trailing slash unless path is exactly "/"
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Filter and sort query parameters
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
    sorted_query = urlencode(
        sorted(filtered.items()),
        doseq=True,
    )

    # Reassemble without fragment
    return urlunparse((scheme, netloc, path, parsed.params, sorted_query, ""))


def cache_key(
    url: str,
    *,
    extract_mode: Literal["summary", "full"] = "summary",
    policy_fingerprint: str | None = None,
) -> str:
    """Return the Valkey key for a URL, extraction mode, and retrieval policy."""
    cache_input = f"{normalize_url(url)}:{extract_mode}"
    if policy_fingerprint is not None:
        cache_input = f"{cache_input}:{policy_fingerprint}"
    digest = hashlib.sha256(cache_input.encode()).hexdigest()
    return f"ret:{digest}"


def cache_policy_fingerprint(
    *,
    trusted_domains: list[str],
    verified_domains: list[str],
    blocked_domains: list[str],
    promptguard_threshold: float,
    promptguard_fail_closed: bool,
    classifier_loaded: bool,
) -> str:
    """Return a stable cache-key input for content-shaping retrieval policy.

    ``classifier_loaded`` is included so a fail-open body sanitized while the
    PromptGuard model was absent misses the cache once the model loads —
    otherwise the stale unscanned entry would replay as if it had been
    scanned.
    """
    inputs = {
        "blocked_domains": sorted(
            {domain.strip().lower() for domain in blocked_domains}
        ),
        "classifier_loaded": classifier_loaded,
        "promptguard_fail_closed": promptguard_fail_closed,
        "promptguard_threshold": promptguard_threshold,
        "trusted_domains": sorted(
            {domain.strip().lower() for domain in trusted_domains}
        ),
        "verified_domains": sorted(
            {domain.strip().lower() for domain in verified_domains}
        ),
    }
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def _effective_ttl_hours(
    ttl_hours: int,
    *,
    domain: str,
    news_domains: list[str] | None,
) -> int:
    """Return the caller TTL, shortened for configured news domains."""
    if ttl_hours <= 0:
        return 0
    if news_domains and domain.lower() in {item.lower() for item in news_domains}:
        return min(1, ttl_hours)
    return ttl_hours


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class CacheMetrics:
    """In-process counters for content-cache reconnect and operation health."""

    reconnect_attempts: int = 0
    reconnect_successes: int = 0
    reconnect_failures: int = 0
    operation_failures: int = 0


def _closed_vocabulary_reason(exc: BaseException, *, default: str) -> str:
    """Map an exception to the closed log-reason vocabulary.

    Never logs ``str(exc)`` or the Valkey URL (which carries credentials) —
    only one of ``connect_failed`` / ``operation_failed`` / ``timeout``.
    """
    if isinstance(exc, TimeoutError):
        return "timeout"
    return default


# ---------------------------------------------------------------------------
# Cache class
# ---------------------------------------------------------------------------


class ContentCache:
    """Async Valkey cache for ``RetrievedContent`` objects."""

    def __init__(
        self,
        valkey_url: str = "redis://poppy-valkey:6379/4",
        *,
        metrics: CacheMetrics | None = None,
    ) -> None:
        self._url = valkey_url
        self._client: aioredis.Redis | None = None
        self._metrics = metrics if metrics is not None else CacheMetrics()
        self._reconnect_lock = asyncio.Lock()
        self._next_retry_at: float | None = None
        self._backoff_s: float = _RECONNECT_INITIAL_BACKOFF_S

    # -- lifecycle -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Return whether the most recent operation left the client connected."""
        return self._client is not None

    async def _attempt_connect(self) -> bool:
        """Attempt one Valkey connection, bounded by a 2s deadline."""
        try:
            async with asyncio.timeout(_RECONNECT_TIMEOUT_S):
                client = aioredis.from_url(
                    self._url,
                    socket_connect_timeout=_RECONNECT_TIMEOUT_S,
                    socket_timeout=_RECONNECT_TIMEOUT_S,
                )
                await client.ping()  # type: ignore[misc]
        except Exception as exc:
            logger.warning(
                "Valkey connection failed for content cache (%s)",
                _closed_vocabulary_reason(exc, default="connect_failed"),
            )
            self._client = None
            return False
        self._client = client  # type: ignore[assignment]
        return True

    async def connect(self) -> bool:
        """Open a connection to Valkey.  Returns ``True`` on success."""
        return await self._attempt_connect()

    async def _ensure_client(self) -> bool:
        """Reconnect if disconnected and the backoff window has elapsed.

        A caller that finds a reconnect already in flight gets an immediate
        miss rather than waiting on it.
        """
        if self._client is not None:
            return True
        now = time.monotonic()
        if self._next_retry_at is not None and now < self._next_retry_at:
            return False
        if self._reconnect_lock.locked():
            return False
        async with self._reconnect_lock:
            if self._client is not None:
                return True
            self._metrics.reconnect_attempts += 1
            ok = await self._attempt_connect()
            if ok:
                self._metrics.reconnect_successes += 1
                self._next_retry_at = None
                self._backoff_s = _RECONNECT_INITIAL_BACKOFF_S
            else:
                self._metrics.reconnect_failures += 1
                self._next_retry_at = time.monotonic() + self._backoff_s
                self._backoff_s = min(
                    self._backoff_s * 2,
                    _RECONNECT_MAX_BACKOFF_S,
                )
            return ok

    async def ping_if_due(self) -> bool:
        """Reconnect (subject to backoff) if disconnected, else report connected.

        Lets ``/health`` alone detect recovery during zero-traffic windows,
        at the same bounded cost as any other cache operation.
        """
        return await self._ensure_client()

    def _mark_disconnected(self, exc: BaseException) -> None:
        """Clear the client after an operation failure so ``connected`` is honest."""
        self._client = None
        self._metrics.operation_failures += 1
        self._next_retry_at = time.monotonic() + self._backoff_s
        logger.warning(
            "Content cache operation failed (%s)",
            _closed_vocabulary_reason(exc, default="operation_failed"),
        )

    async def close(self) -> None:
        """Gracefully close the Valkey connection."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- public API ----------------------------------------------------------

    async def get(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
        ttl_hours: int = 24,
        news_domains: list[str] | None = None,
    ) -> RetrievedContent | None:
        """Fetch valid cached content for *url*, or ``None`` on miss.

        Entries older than the current caller policy are deleted even if their
        original Valkey expiration was longer.
        """
        if not await self._ensure_client():
            return None

        key = cache_key(
            url,
            extract_mode=extract_mode,
            policy_fingerprint=policy_fingerprint,
        )
        if ttl_hours <= 0:
            await self._delete_key(key)
            return None

        try:
            raw: bytes | None = await self._client.get(key)  # type: ignore[misc]
        except Exception as exc:
            self._mark_disconnected(exc)
            return None

        if raw is None:
            return None

        content = RetrievedContent.model_validate_json(raw)
        retrieved_at = content.retrieved_at
        if retrieved_at.tzinfo is None:
            await self._delete_key(key)
            return None

        effective_ttl_hours = _effective_ttl_hours(
            ttl_hours,
            domain=content.domain,
            news_domains=news_domains,
        )
        if datetime.now(UTC) - retrieved_at >= timedelta(hours=effective_ttl_hours):
            await self._delete_key(key)
            return None

        return content.model_copy(
            update={
                "cache_hit": True,
                "cached_at": retrieved_at,
            },
        )

    async def delete(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
    ) -> bool:
        """Delete a cache variant, returning whether Valkey accepted the request."""
        if not await self._ensure_client():
            return False
        key = cache_key(
            url,
            extract_mode=extract_mode,
            policy_fingerprint=policy_fingerprint,
        )
        return await self._delete_key(key)

    async def put(
        self,
        url: str,
        content: RetrievedContent,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
        ttl_hours: int = 24,
        domain: str = "",
        news_domains: list[str] | None = None,
    ) -> bool:
        """Store *content* in the cache.

        Returns ``True`` if the value was written, ``False`` otherwise
        (client not connected, blocked tier, or write error).
        """
        if not await self._ensure_client():
            return False

        key = cache_key(
            url,
            extract_mode=extract_mode,
            policy_fingerprint=policy_fingerprint,
        )
        if ttl_hours <= 0:
            await self._delete_key(key)
            return False

        # Never cache content from untrusted / blocked domains.
        if content.trust_tier in _NO_CACHE_TIERS:
            return False

        effective_ttl_hours = _effective_ttl_hours(
            ttl_hours,
            domain=domain,
            news_domains=news_domains,
        )

        serialised = content.model_dump_json()

        try:
            await self._client.set(  # type: ignore[misc]
                key,
                serialised,
                ex=effective_ttl_hours * 3600,
            )
            return True
        except Exception as exc:
            self._mark_disconnected(exc)
            return False

    async def _delete_key(self, key: str) -> bool:
        """Delete a precomputed cache key while preserving cache degradation."""
        if not await self._ensure_client():
            return False
        try:
            await self._client.delete(key)  # type: ignore[misc]
            return True
        except Exception as exc:
            self._mark_disconnected(exc)
            return False
