"""Shared test doubles for services/retrieval/cache.py."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

_retrieval_root = str(Path(__file__).resolve().parents[2] / "services" / "retrieval")
if _retrieval_root not in sys.path:
    sys.path.insert(0, _retrieval_root)

from cache import CacheMetrics  # noqa: E402

if TYPE_CHECKING:
    from models import RetrievedContent


class FakeContentCache:
    """Settable-``connected`` double for ``ContentCache`` used by app-level tests.

    ``get``/``put``/``delete`` are no-ops (always miss / never write) so
    fixtures that don't care about cache content can drop this in without
    exercising real Valkey I/O.
    """

    def __init__(self, *, connected: bool = False) -> None:
        self.connected = connected
        self.metrics = CacheMetrics()

    async def ping_if_due(self) -> bool:
        """Report the settable ``connected`` state, mirroring ``ContentCache``."""
        return self.connected

    async def get(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
        ttl_hours: int = 24,
        news_domains: list[str] | None = None,
    ) -> RetrievedContent | None:
        """Always miss."""
        return None

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
        """Never write."""
        return False

    async def delete(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
    ) -> bool:
        """Never write."""
        return False

    async def close(self) -> None:
        """No-op; the fake owns no real connection."""
        return None
