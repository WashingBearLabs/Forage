"""Shared test doubles and helpers for the Forage suite."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

from cache import CacheMetrics

if TYPE_CHECKING:
    from models import RetrievedContent


def assert_frozen(instance: object, field: str, value: object) -> None:
    """Assert *instance* refuses assignment to *field* — the frozen guarantee.

    The write goes through ``setattr`` with a name the type checker cannot see
    through, and that indirection is the point: written plainly,
    ``instance.field = value`` is a *static* error precisely because the
    dataclass is frozen, so under strict type checking the runtime behaviour
    these tests exist to pin could not be expressed at all. The suite still
    asserts the real thing — that the assignment raises at run time.

    ``AttributeError`` rather than ``dataclasses.FrozenInstanceError`` because
    Forage's result types are ``slots=True`` as well as ``frozen=True``, and
    the two paths raise different subclasses of the same base.
    """
    with pytest.raises(AttributeError):
        setattr(instance, field, value)


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
