"""Corpus-owned doubles for the two collaborators a drive replaces.

They live here, not in ``tests/fakes.py``, because nothing under ``scripts/``
imports ``tests`` — that module pulls in ``pytest`` and the socket-guard helpers
at import time, which a host-side recorder must not inherit. The price is ~40
duplicated lines, and ``tests/test_corpus_harness.py`` pins both doubles against
the protocol and the ``ContentCache`` methods the service calls, so they cannot
drift silently.

The call surface was re-grepped at story start: the service reaches the cache
through ``connect`` and ``close`` (the lifespan), ``ping_if_due`` (``/health``)
and ``get`` / ``put`` / ``delete`` (``run_retrieve_pipeline``). It reads no
attribute of the cache object, so the double carries none.
"""

from __future__ import annotations

from typing import Literal

from models import RetrievedContent
from pipeline.search_providers.base import ProviderSearchResult


class CorpusSearchProvider:
    """A ``SearchProvider`` that returns one fixed, successful outcome.

    It cannot fail: an error code on ``/search`` is therefore a harness fault,
    never a corpus outcome. ``paid`` defaults to ``False`` so the request policy
    passes it through unfiltered.
    """

    def __init__(
        self,
        *,
        name: str = "corpus",
        paid: bool = False,
        origin: str | None = None,
        outcome: ProviderSearchResult,
    ) -> None:
        self.name = name
        self.paid = paid
        self.origin = origin
        self._outcome = outcome

    async def search(self, query: str, max_results: int) -> ProviderSearchResult:
        """Return the fixed outcome, whatever was asked."""
        return self._outcome


class CorpusContentCache:
    """A ``ContentCache`` that never remembers and reports itself connected.

    Every read misses and every write is refused, so a drive always runs the
    whole pipeline and no earlier record's body can be served for a later one.
    """

    async def connect(self) -> bool:
        """Report a usable cache, as the lifespan expects."""
        return True

    async def ping_if_due(self) -> bool:
        """Report a usable cache to ``/health``."""
        return True

    async def close(self) -> None:
        """Release nothing; the double owns no connection."""
        return None

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
