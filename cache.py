"""Valkey-backed content cache for the retrieval sidecar.

Connects to Valkey DB 4. Stores serialised ``RetrievedContent`` objects with
TTL-based expiry keyed by SHA-256 of the normalised URL, extraction mode, and
the trust/PromptGuard policy that shaped the content. News domains use the
shorter of their one-hour TTL and the caller's effective TTL. A zero TTL
disables both reads and writes for the matching cache variant.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import redis.asyncio as aioredis  # type: ignore[import-untyped]

from models import RetrievedContent, TrustTier

logger = logging.getLogger(__name__)

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
) -> str:
    """Return a stable cache-key input for content-shaping retrieval policy."""
    inputs = {
        "blocked_domains": sorted(
            {domain.strip().lower() for domain in blocked_domains}
        ),
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
        if self._client is None:
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
        except Exception:
            logger.warning("Cache GET failed for %s", key)
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
        if self._client is None:
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
        if self._client is None:
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
        except Exception:
            logger.warning("Cache PUT failed for %s", key)
            return False

    async def _delete_key(self, key: str) -> bool:
        """Delete a precomputed cache key while preserving cache degradation."""
        if self._client is None:
            return False
        try:
            await self._client.delete(key)  # type: ignore[misc]
            return True
        except Exception:
            logger.warning("Cache DELETE failed for %s", key)
            return False
