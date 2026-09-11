"""Content cache for the retrieval sidecar, over a swappable storage backend.

``ContentCache`` owns the cache's *policy* and nothing else owns any of it:
refusal of untrusted and blocked tiers, read-time freshness revalidation with
news-domain shortening, rejection of tz-naive timestamps, zero-TTL purge, and
``policy_fingerprint`` key derivation. Raw key/value movement is delegated to a
:class:`CacheStorage` — :class:`ValkeyStorage` (DB 4, with the reconnect and
backoff machinery) or :class:`InMemoryStorage` (bounded, per-process). The
policy therefore has exactly one implementation and runs identically over every
storage; a storage that inspected a value or refused a key would be a second,
drifting copy of it.

Stores serialised ``RetrievedContent`` objects with TTL-based expiry keyed by
SHA-256 of the normalised URL, extraction mode, and the trust/PromptGuard
policy that shaped the content. News domains use the shorter of their one-hour
TTL and the caller's effective TTL. A zero TTL disables both reads and writes
for the matching cache variant.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import redis.asyncio as aioredis

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
    """In-process counters for content-cache reconnect and operation health.

    The ``storage_*`` counters are a different layer from ``/metrics``'
    ``retrieve.cache_hits`` / ``cache_misses``: those count *request* outcomes
    (did this ``/retrieve`` serve cached content?), these count *storage
    operations* (did this key lookup find live bytes?). One retrieval issues at
    most one storage read, but a zero-TTL purge or a policy-stale entry moves
    one counter and not the other, so the two are never expected to match.

    ``storage_evictions`` counts entries the in-memory storage dropped to stay
    inside its bounds — expired ones first, then least-recently-used. An entry
    that simply aged out and was noticed on the next read is a
    ``storage_misses``, not an eviction: nothing was under pressure.
    """

    reconnect_attempts: int = 0
    reconnect_successes: int = 0
    reconnect_failures: int = 0
    operation_failures: int = 0
    storage_hits: int = 0
    storage_misses: int = 0
    storage_evictions: int = 0
    storage_oversize_skips: int = 0


# ---------------------------------------------------------------------------
# In-memory storage configuration
# ---------------------------------------------------------------------------

MEBIBYTE = 1024 * 1024

# Sized to the container's real headroom: ``mem_limit: 1024m`` already reserves
# 512 MiB for the parent FastAPI + torch + PromptGuard process and 384 MiB for
# the spawned extraction child, leaving ~128 MiB. The 32 MiB default spends a
# quarter of that, and the ceiling below refuses a value that would spend all
# of it.
DEFAULT_CACHE_MAX_ENTRIES = 256
DEFAULT_CACHE_MAX_BYTES = 32 * MEBIBYTE

_MAX_CACHE_MAX_ENTRIES = 4096
_MIN_CACHE_MAX_BYTES = MEBIBYTE
_MAX_CACHE_MAX_BYTES = 128 * MEBIBYTE


class CacheConfigurationError(ValueError):
    """Raised when in-memory cache configuration exceeds safe bounds."""


@dataclass(frozen=True, slots=True)
class CacheSettings:
    """Validated bounds for one :class:`InMemoryStorage`."""

    max_entries: int = DEFAULT_CACHE_MAX_ENTRIES
    max_bytes: int = DEFAULT_CACHE_MAX_BYTES


def _bounded_int(
    config: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Read one bounded integer setting without accepting bool values.

    The same idiom as ``pipeline/extraction_limits.py``'s helper, deliberately
    re-stated rather than imported: the dependency between these modules runs
    the other way (``pipeline/orchestrator.py`` imports ``cache``), and reaching
    into ``pipeline`` from here would close that loop for twelve lines.
    """
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CacheConfigurationError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise CacheConfigurationError(f"{key} must be between {minimum} and {maximum}")
    return value


def cache_settings_from_config(config: dict[str, Any]) -> CacheSettings:
    """Build bounded in-memory cache settings from the sidecar configuration.

    Validated at startup whichever storage is active, so a typo in the
    ``cache:`` block fails the boot loudly rather than silently widening a
    memory bound — the same posture as the ``extraction:`` block.
    """
    raw_cache_config = config.get("cache", {})
    if not isinstance(raw_cache_config, dict):
        raise CacheConfigurationError("cache must be a mapping")
    cache_config = cast(dict[str, Any], raw_cache_config)

    return CacheSettings(
        max_entries=_bounded_int(
            cache_config,
            "max_entries",
            DEFAULT_CACHE_MAX_ENTRIES,
            minimum=1,
            maximum=_MAX_CACHE_MAX_ENTRIES,
        ),
        max_bytes=_bounded_int(
            cache_config,
            "max_bytes",
            DEFAULT_CACHE_MAX_BYTES,
            minimum=_MIN_CACHE_MAX_BYTES,
            maximum=_MAX_CACHE_MAX_BYTES,
        ),
    )


def _closed_vocabulary_reason(exc: BaseException, *, default: str) -> str:
    """Map an exception to the closed log-reason vocabulary.

    Never logs ``str(exc)`` or the Valkey URL (which carries credentials) —
    only one of ``connect_failed`` / ``operation_failed`` / ``timeout``.
    """
    if isinstance(exc, TimeoutError):
        return "timeout"
    return default


# ---------------------------------------------------------------------------
# Storage backends
# ---------------------------------------------------------------------------


class CacheStorage(Protocol):
    """Raw, policy-free key/value storage under :class:`ContentCache`.

    A storage moves opaque bytes under a TTL and reports whether it is
    operational. It never inspects a value, never derives a key, and never
    refuses one: every security behaviour the cache has lives one layer up, in
    ``ContentCache``, so that swapping storages cannot swap policy.

    ``connect()`` and ``close()`` are here because the service lifespan calls
    them (``retrieval_app.py``'s startup and shutdown); ``ping_if_due()`` is
    here because ``/health`` calls it on every poll and a storage with no
    connection to lose can answer it for free.
    """

    @property
    def connected(self) -> bool:
        """Whether the storage is operational (in-memory: always)."""
        ...

    async def connect(self) -> bool:
        """Open the storage, returning whether it is usable."""
        ...

    async def close(self) -> None:
        """Release whatever the storage holds."""
        ...

    async def ping_if_due(self) -> bool:
        """Re-check liveness, subject to the storage's own backoff."""
        ...

    async def get(self, key: str) -> bytes | None:
        """Return the stored bytes for *key*, or ``None`` on miss."""
        ...

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """Store *value* under *key* for *ttl_seconds*; report success."""
        ...

    async def delete(self, key: str) -> bool:
        """Remove *key*, reporting whether the storage accepted the request."""
        ...


class _ValkeyClient(Protocol):
    """The five Valkey operations this cache actually issues.

    ``redis.asyncio.Redis`` declares its commands through ``**kwargs`` typed as
    ``Any``, so under strict type checking every call site here decayed to an
    unknown type and had to be silenced. Naming the surface instead — and
    casting the connection to it once, where it is created — types the rest of
    the file precisely and doubles as a statement of exactly how much of Valkey
    Forage depends on. Widening this is a deliberate act, not an accident.
    """

    async def ping(self) -> bool: ...
    async def get(self, name: str) -> bytes | None: ...
    async def set(self, name: str, value: str, *, ex: int) -> bool | None: ...
    async def delete(self, name: str) -> int: ...
    async def aclose(self) -> None: ...


class ValkeyStorage:
    """:class:`CacheStorage` over Valkey, with the reconnect/backoff machinery.

    Every failure mode a network storage has lives here — the bounded connect
    deadline, the doubling backoff, the single-flight reconnect lock, and the
    closed log vocabulary that keeps a credential-bearing URL out of the log.
    None of it belongs to the policy layer, and none of it burdens a storage
    that cannot disconnect.
    """

    def __init__(
        self,
        valkey_url: str = "redis://poppy-valkey:6379/4",
        *,
        metrics: CacheMetrics | None = None,
    ) -> None:
        self._url = valkey_url
        self._client: _ValkeyClient | None = None
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
                client = cast(
                    "_ValkeyClient",
                    aioredis.from_url(
                        self._url,
                        socket_connect_timeout=_RECONNECT_TIMEOUT_S,
                        socket_timeout=_RECONNECT_TIMEOUT_S,
                    ),
                )
                await client.ping()
        except Exception as exc:
            logger.warning(
                "Valkey connection failed for content cache (%s)",
                _closed_vocabulary_reason(exc, default="connect_failed"),
            )
            self._client = None
            return False
        self._client = client
        return True

    async def connect(self) -> bool:
        """Open a connection to Valkey.  Returns ``True`` on success."""
        return await self._attempt_connect()

    async def _ensure_client(self) -> _ValkeyClient | None:
        """Return a connected client, reconnecting if the backoff has elapsed.

        A caller that finds a reconnect already in flight gets an immediate
        miss rather than waiting on it.

        Handing back the client rather than a bare ``bool`` is what lets each
        call site below use a narrowed, non-optional value: a boolean carries
        no correlation with ``self._client``, so every command would otherwise
        read as a possible attribute access on ``None``.
        """
        client = self._client
        if client is not None:
            return client
        now = time.monotonic()
        if self._next_retry_at is not None and now < self._next_retry_at:
            return None
        if self._reconnect_lock.locked():
            return None
        async with self._reconnect_lock:
            if self._client is not None:
                return self._client
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
            return self._client if ok else None

    async def ping_if_due(self) -> bool:
        """Reconnect (subject to backoff) if disconnected, else report connected.

        Lets ``/health`` alone detect recovery during zero-traffic windows,
        at the same bounded cost as any other cache operation.
        """
        return await self._ensure_client() is not None

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
        client = self._client
        if client is not None:
            await client.aclose()
            self._client = None

    # -- storage operations --------------------------------------------------

    async def get(self, key: str) -> bytes | None:
        """Return the stored bytes for *key*, or ``None`` on miss or outage."""
        client = await self._ensure_client()
        if client is None:
            return None
        try:
            raw = await client.get(key)
        except Exception as exc:
            self._mark_disconnected(exc)
            return None
        if raw is None:
            self._metrics.storage_misses += 1
        else:
            self._metrics.storage_hits += 1
        return raw

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """Write *value* under *key* with a Valkey ``EX`` expiry."""
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            await client.set(key, value, ex=ttl_seconds)
        except Exception as exc:
            self._mark_disconnected(exc)
            return False
        return True

    async def delete(self, key: str) -> bool:
        """Delete *key* while preserving cache degradation."""
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            await client.delete(key)
        except Exception as exc:
            self._mark_disconnected(exc)
            return False
        return True


@dataclass(frozen=True, slots=True)
class _MemoryEntry:
    """One serialised payload, its expiry, and its exact byte cost."""

    value: bytes
    expires_at: float
    size: int


class InMemoryStorage:
    """Bounded, per-process :class:`CacheStorage` for a Valkey-free deployment.

    **Single-process by design.** The service runs one uvicorn worker, so this
    dictionary *is* the whole cache; nothing is shared between processes and
    nothing survives a restart. A multi-worker deployment would give each
    worker its own private cache, which is a correctness question for whoever
    proposes one, not a bug here.

    Values are stored as **serialised JSON bytes**, exactly as Valkey stores
    them. That is what makes the byte accounting exact, and it is also the
    security property: a caller can never be handed a live reference to a
    cached object and mutate what the next caller reads.

    Every mutation below is synchronous — no ``await`` sits between reading the
    dictionary and writing it back — so concurrent requests on the one event
    loop cannot interleave into torn state. Keep it that way.

    Bounds are enforced on both entry count and total bytes, and expired
    entries are purged before any live entry is evicted: pure LRU under mixed
    TTLs will happily discard a fresh entry while a dead one lingers.
    """

    def __init__(
        self,
        *,
        settings: CacheSettings | None = None,
        metrics: CacheMetrics | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings if settings is not None else CacheSettings()
        self._metrics = metrics if metrics is not None else CacheMetrics()
        self._clock = clock
        self._entries: OrderedDict[str, _MemoryEntry] = OrderedDict()
        self._total_bytes = 0

    # -- lifecycle -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Always ``True`` — there is no connection to lose."""
        return True

    async def connect(self) -> bool:
        """Nothing to open; report the storage operational."""
        return True

    async def close(self) -> None:
        """Drop every entry; the process owns the only copy."""
        self._entries.clear()
        self._total_bytes = 0

    async def ping_if_due(self) -> bool:
        """Always ``True`` — an in-process dictionary cannot go unreachable."""
        return True

    # -- introspection -------------------------------------------------------

    @property
    def entry_count(self) -> int:
        """Number of entries currently held."""
        return len(self._entries)

    @property
    def total_bytes(self) -> int:
        """Total serialised bytes currently held."""
        return self._total_bytes

    # -- storage operations --------------------------------------------------

    async def get(self, key: str) -> bytes | None:
        """Return live bytes for *key*, dropping it if its TTL has passed.

        An entry noticed dead here is a miss, not an eviction: nothing was
        under pressure, it simply aged out.
        """
        entry = self._entries.get(key)
        if entry is None:
            self._metrics.storage_misses += 1
            return None
        if entry.expires_at <= self._clock():
            self._discard(key)
            self._metrics.storage_misses += 1
            return None
        self._entries.move_to_end(key)
        self._metrics.storage_hits += 1
        return entry.value

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """Store *value* for *ttl_seconds*, or skip it if it cannot fit.

        An entry larger than the whole byte bound is never stored — storing it
        would evict everything else to make room for one document. It is
        counted (``storage_oversize_skips``) and the request is served
        uncached. Any existing entry for that key goes too, so a superseded
        payload can never be served in the new one's place.
        """
        payload = value.encode()
        size = len(payload)
        if ttl_seconds <= 0:
            self._discard(key)
            return False
        if size > self._settings.max_bytes:
            self._discard(key)
            self._metrics.storage_oversize_skips += 1
            return False
        self._discard(key)
        self._entries[key] = _MemoryEntry(
            value=payload,
            expires_at=self._clock() + ttl_seconds,
            size=size,
        )
        self._total_bytes += size
        self._enforce_bounds()
        return True

    async def delete(self, key: str) -> bool:
        """Remove *key* if present; the request is always accepted."""
        self._discard(key)
        return True

    # -- internals -----------------------------------------------------------

    def _discard(self, key: str) -> None:
        """Remove one entry and give its bytes back to the budget."""
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total_bytes -= entry.size

    def _over_bounds(self) -> bool:
        """Whether either bound is currently exceeded."""
        return (
            len(self._entries) > self._settings.max_entries
            or self._total_bytes > self._settings.max_bytes
        )

    def _enforce_bounds(self) -> None:
        """Evict until both bounds hold — expired entries first, then LRU."""
        if not self._over_bounds():
            return
        now = self._clock()
        expired = [k for k, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._discard(key)
            self._metrics.storage_evictions += 1
        while self._entries and self._over_bounds():
            oldest = next(iter(self._entries))
            self._discard(oldest)
            self._metrics.storage_evictions += 1


# ---------------------------------------------------------------------------
# Cache class
# ---------------------------------------------------------------------------


class ContentCache:
    """Policy layer for ``RetrievedContent`` caching over a swappable storage.

    Everything security-relevant about the cache is here and only here: which
    trust tiers may be cached, how a stored entry is revalidated against the
    *caller's* current freshness policy, what a tz-naive timestamp means, what
    a zero TTL means, and what goes into a key. The storage underneath is a
    dumb byte mover, which is what lets a Valkey-backed and a memory-backed
    deployment be the same service rather than two implementations of it.
    """

    def __init__(
        self,
        valkey_url: str = "redis://poppy-valkey:6379/4",
        *,
        metrics: CacheMetrics | None = None,
        storage: CacheStorage | None = None,
    ) -> None:
        self._metrics = metrics if metrics is not None else CacheMetrics()
        self._storage: CacheStorage = (
            storage
            if storage is not None
            else ValkeyStorage(valkey_url, metrics=self._metrics)
        )

    # -- lifecycle -----------------------------------------------------------

    @property
    def storage(self) -> CacheStorage:
        """The storage this cache's policy runs over."""
        return self._storage

    @property
    def connected(self) -> bool:
        """Return whether the selected storage is operational."""
        return self._storage.connected

    async def connect(self) -> bool:
        """Open the storage.  Returns ``True`` when it is usable."""
        return await self._storage.connect()

    async def ping_if_due(self) -> bool:
        """Re-check the storage, subject to its own backoff.

        Lets ``/health`` alone detect recovery during zero-traffic windows,
        at the same bounded cost as any other cache operation.
        """
        return await self._storage.ping_if_due()

    async def close(self) -> None:
        """Gracefully release the storage."""
        await self._storage.close()

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
        original storage expiration was longer.
        """
        key = cache_key(
            url,
            extract_mode=extract_mode,
            policy_fingerprint=policy_fingerprint,
        )
        if ttl_hours <= 0:
            await self._storage.delete(key)
            return None

        raw = await self._storage.get(key)
        if raw is None:
            return None

        content = RetrievedContent.model_validate_json(raw)
        retrieved_at = content.retrieved_at
        if retrieved_at.tzinfo is None:
            await self._storage.delete(key)
            return None

        effective_ttl_hours = _effective_ttl_hours(
            ttl_hours,
            domain=content.domain,
            news_domains=news_domains,
        )
        if datetime.now(UTC) - retrieved_at >= timedelta(hours=effective_ttl_hours):
            await self._storage.delete(key)
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
        """Delete a cache variant, returning whether the storage accepted it."""
        key = cache_key(
            url,
            extract_mode=extract_mode,
            policy_fingerprint=policy_fingerprint,
        )
        return await self._storage.delete(key)

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
        (storage unavailable, blocked tier, or write error).
        """
        key = cache_key(
            url,
            extract_mode=extract_mode,
            policy_fingerprint=policy_fingerprint,
        )
        if ttl_hours <= 0:
            await self._storage.delete(key)
            return False

        # Never cache content from untrusted / blocked domains.
        if content.trust_tier in _NO_CACHE_TIERS:
            return False

        effective_ttl_hours = _effective_ttl_hours(
            ttl_hours,
            domain=domain,
            news_domains=news_domains,
        )

        return await self._storage.set(
            key,
            content.model_dump_json(),
            ttl_seconds=effective_ttl_hours * 3600,
        )
