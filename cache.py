"""Valkey-backed content cache for the retrieval sidecar.

Connects to Valkey DB 4.  Stores serialised ``RetrievedContent`` objects
with TTL-based expiry keyed by SHA-256 of the normalised URL.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import redis.asyncio as aioredis  # type: ignore[import-untyped]

from models import RetrievedContent, TrustTier

logger = logging.getLogger(__name__)

# Tracking query parameters stripped during normalisation.
_TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "ref",
    "source",
})

# Trust tiers that must never be cached.
_NO_CACHE_TIERS: frozenset[TrustTier] = frozenset({
    TrustTier.UNTRUSTED,
    TrustTier.BLOCKED,
})


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
    filtered = {
        k: v
        for k, v in params.items()
        if k.lower() not in _TRACKING_PARAMS
    }
    sorted_query = urlencode(
        sorted(filtered.items()),
        doseq=True,
    )

    # Reassemble without fragment
    return urlunparse((scheme, netloc, path, parsed.params, sorted_query, ""))


def cache_key(url: str) -> str:
    """Return the Valkey key for a URL."""
    digest = hashlib.sha256(normalize_url(url).encode()).hexdigest()
    return f"ret:{digest}"


# ---------------------------------------------------------------------------
# Cache class
# ---------------------------------------------------------------------------


class ContentCache:
    """Async Valkey cache for ``RetrievedContent`` objects."""

    def __init__(self, valkey_url: str = "redis://poppy-valkey:6379/4") -> None:
        self._url = valkey_url
        self._client: aioredis.Redis | None = None

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> bool:
        """Open a connection to Valkey.  Returns ``True`` on success."""
        try:
            self._client = aioredis.from_url(  # type: ignore[assignment]
                self._url,
                socket_connect_timeout=2,
            )
            await self._client.ping()  # type: ignore[misc]
            return True
        except Exception:
            logger.warning("Valkey connection failed for content cache")
            self._client = None
            return False

    async def close(self) -> None:
        """Gracefully close the Valkey connection."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- public API ----------------------------------------------------------

    async def get(self, url: str) -> RetrievedContent | None:
        """Fetch cached content for *url*, or ``None`` on miss."""
        if self._client is None:
            return None

        key = cache_key(url)
        try:
            raw: bytes | None = await self._client.get(key)  # type: ignore[misc]
        except Exception:
            logger.warning("Cache GET failed for %s", key)
            return None

        if raw is None:
            return None

        content = RetrievedContent.model_validate_json(raw)
        return content.model_copy(
            update={
                "cache_hit": True,
                "cached_at": datetime.now(UTC),
            },
        )

    async def put(
        self,
        url: str,
        content: RetrievedContent,
        *,
        ttl_hours: int = 24,
        domain: str = "",
        news_domains: list[str] | None = None,
    ) -> bool:
        """Store *content* in the cache.

        Returns ``True`` if the value was written, ``False`` otherwise
        (client not connected, blocked tier, or write error).
        """
        if self._client is None:
            return False

        # Never cache content from untrusted / blocked domains.
        if content.trust_tier in _NO_CACHE_TIERS:
            return False

        # Determine TTL — 1 h for news domains, default otherwise.
        effective_ttl_hours = ttl_hours
        if news_domains and domain.lower() in {d.lower() for d in news_domains}:
            effective_ttl_hours = 1

        key = cache_key(url)
        serialised = content.model_dump_json()

        try:
            await self._client.set(  # type: ignore[misc]
                key,
                serialised,
                ex=effective_ttl_hours * 3600,
            )
            return True
        except Exception:
            logger.warning("Cache PUT failed for %s", key)
            return False
