"""Provider-agnostic web search seam.

Every search backend Forage can call — SearXNG today, a paid API later —
implements :class:`~pipeline.search_providers.base.SearchProvider`. This
package is the first nested package under ``pipeline/`` (see ``CLAUDE.md``
invariant 3: that invariant is about top-level packages and is unaffected).

It also owns the **name → provider** resolution the operator drives with
``FORAGE_SEARCH_PROVIDERS``: :func:`parse_provider_names` normalizes the raw
variable and :func:`build_provider_chain` resolves the result through a
static registry. ``retrieval_app.py`` imports both; nothing here imports the
app module, so the dependency runs one way only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from pipeline.search_providers.base import SearchProvider
from pipeline.search_providers.brave import (
    BRAVE_API_KEY_ENV_VAR,
    BRAVE_PROVIDER_NAME,
    BraveApiProvider,
    BraveSettings,
    usable_brave_key,
)
from pipeline.search_providers.searxng import SEARXNG_PROVIDER_NAME, SearxngProvider

logger = logging.getLogger(__name__)

# Named here rather than in `retrieval_app.py` so the repo holds one copy of
# the string: the error message below quotes the variable, and
# `retrieval_app.SEARCH_PROVIDERS_ENV_VAR` re-exports this exact object as the
# single read site's name.
SEARCH_PROVIDERS_ENV_VAR = "FORAGE_SEARCH_PROVIDERS"

# The chain an operator who configured nothing gets: SearXNG alone, the
# key-less free floor.
DEFAULT_PROVIDER_NAME = "searxng"

# Every name `build_provider_chain` recognises — a superset of the registry's
# own keys, since "brave" is a known name even on a start with no key (it is
# *skipped*, never treated as an unknown-name boot refusal). Built from each
# provider's own chain token so a rename cannot split the name an operator
# writes from the name `orchestrator._is_legacy_searxng_chain` compares.
_KNOWN_PROVIDER_NAMES = frozenset({SEARXNG_PROVIDER_NAME, BRAVE_PROVIDER_NAME})


class SearchProviderConfigurationError(ValueError):
    """The operator named a provider Forage has no implementation for.

    Raised out of :func:`build_provider_chain`, which the lifespan calls —
    so this refuses the boot rather than degrading. The chain selects code
    paths and there is no honest ``/health`` surface for it yet; running the
    default chain instead would be exactly the silent-substitution shape
    ``CLAUDE.md`` invariant 5 exists to prevent.

    The message names the variable, the 1-based position of the offending
    entry, and the sorted known names — **never the offending token and never
    the raw value**, both of which are operator-supplied text heading for a
    log line (the ``model_fetcher.resolve_revision()`` ruling).
    """


def parse_provider_names(raw: str | None) -> list[str]:
    """Normalize ``FORAGE_SEARCH_PROVIDERS`` into an ordered list of names.

    Splits on commas, strips surrounding whitespace, lower-cases, drops empty
    tokens (so ``" searxng, "`` and ``"searxng"`` are the same chain), and
    collapses duplicates keeping the first occurrence — order is the chain's
    meaning, so the first mention wins. An unset, blank, or token-less value
    yields the default one-name chain; this function never returns ``[]``.
    """
    names: list[str] = []
    for token in (raw or "").split(","):
        name = token.strip().lower()
        if name and name not in names:
            names.append(name)
    return names or [DEFAULT_PROVIDER_NAME]


def build_provider_chain(
    names: Sequence[str],
    *,
    searxng_url: str,
    brave_api_key: str | None = None,
    brave_settings: BraveSettings | None = None,
) -> list[SearchProvider]:
    """Resolve *names* into constructed providers, in chain order.

    Resolution is a plain lookup in a static dict literal. No ``importlib``,
    ``getattr``, ``eval``, or entry-point discovery participates: an operator
    string never selects code by any path other than this table, so the set of
    reachable backends is the set written here and read in review.

    ``"brave"`` is a **known** name whether or not *brave_api_key* is given —
    an unknown-name boot refusal never fires for it — but it is only in the
    registry, and so only ever constructed, when the key is *usable* by
    :func:`~pipeline.search_providers.brave.brave_key_present` (an empty or
    whitespace-only value counts as absent, and the stripped key is what the
    provider receives). Without one, a ``"brave"`` entry is skipped with a
    single WARNING (``brave_skipped_missing_key``); never raised for. That
    skip is explicit to Brave: any other known name that is somehow missing
    from the registry refuses the boot rather than being silently dropped.
    If every configured entry is skipped this way, the resolved chain would
    otherwise be empty, so a second WARNING
    (``search_chain_defaulted_to_searxng``) marks the substitution and the
    key-less SearXNG floor is used instead. A present
    key with ``"brave"`` absent from *names* registers nothing and logs
    nothing here — an unused key is not a misconfiguration.
    """
    registry: dict[str, Callable[[], SearchProvider]] = {
        SEARXNG_PROVIDER_NAME: lambda: SearxngProvider(searxng_url),
    }
    key = usable_brave_key(brave_api_key)
    if key is not None:
        usable_key = key
        settings = brave_settings
        registry[BRAVE_PROVIDER_NAME] = lambda: BraveApiProvider(usable_key, settings)

    chain: list[SearchProvider] = []
    for position, name in enumerate(names, start=1):
        if name not in _KNOWN_PROVIDER_NAMES:
            raise SearchProviderConfigurationError(
                f"{SEARCH_PROVIDERS_ENV_VAR} entry {position} names a search "
                f"provider Forage does not know; known providers are "
                f"{', '.join(sorted(_KNOWN_PROVIDER_NAMES))}"
            )
        factory = registry.get(name)
        if factory is None:
            if name == BRAVE_PROVIDER_NAME:
                logger.warning(
                    "brave_skipped_missing_key — no usable %s in the "
                    "environment, so the paid provider is not registered",
                    BRAVE_API_KEY_ENV_VAR,
                )
                continue
            # Unreachable while SearXNG is the only other known name (it is
            # always registered), and kept so a future key-gated provider
            # cannot inherit Brave's skip and its Brave-specific log line.
            raise SearchProviderConfigurationError(
                f"{SEARCH_PROVIDERS_ENV_VAR} entry {position} names a known "
                "search provider that has no registered implementation"
            )
        chain.append(factory())

    if not chain:
        logger.warning(
            "search_chain_defaulted_to_searxng — every configured search "
            "provider was skipped, so the key-less SearXNG floor is used "
            "instead"
        )
        chain = [SearxngProvider(searxng_url)]
    return chain
