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

from collections.abc import Callable, Sequence

from pipeline.search_providers.base import SearchProvider
from pipeline.search_providers.searxng import SearxngProvider

# Named here rather than in `retrieval_app.py` so the repo holds one copy of
# the string: the error message below quotes the variable, and
# `retrieval_app.SEARCH_PROVIDERS_ENV_VAR` re-exports this exact object as the
# single read site's name.
SEARCH_PROVIDERS_ENV_VAR = "FORAGE_SEARCH_PROVIDERS"

# The chain an operator who configured nothing gets: SearXNG alone, the
# key-less free floor.
DEFAULT_PROVIDER_NAME = "searxng"


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
    names: Sequence[str], *, searxng_url: str
) -> list[SearchProvider]:
    """Resolve *names* into constructed providers, in chain order.

    Resolution is a plain lookup in a static dict literal. No ``importlib``,
    ``getattr``, ``eval``, or entry-point discovery participates: an operator
    string never selects code by any path other than this table, so the set of
    reachable backends is the set written here and read in review.
    """
    registry: dict[str, Callable[[], SearchProvider]] = {
        "searxng": lambda: SearxngProvider(searxng_url),
    }
    chain: list[SearchProvider] = []
    for position, name in enumerate(names, start=1):
        factory = registry.get(name)
        if factory is None:
            raise SearchProviderConfigurationError(
                f"{SEARCH_PROVIDERS_ENV_VAR} entry {position} names a search "
                f"provider Forage does not know; known providers are "
                f"{', '.join(sorted(registry))}"
            )
        chain.append(factory())
    return chain
