"""Per-request policy over a configured provider chain (contract 1.2.0).

``apply_request_policy`` narrows -- never widens, reorders, or keys -- the
operator-configured chain for one ``/search`` call, implementing ruling 29's
cost-monotonic guarantee: no request can cause a paid call the configured
chain would not already have made.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pipeline.search_providers.base import SearchProvider

_MAX_POLICY_ENTRIES = 8


class RequestPolicy(Protocol):
    """The two ``SearchRequest`` fields this module reads -- never ``models``.

    Declared here, on the consumer side, rather than importing ``models``
    directly: ruling 11 keeps every provider-package module from importing a
    wire model or a sanitization stage
    (``tests/test_search_providers.py``'s mechanical import sweep is the
    gate), the same seam shape ``SearchProvider`` itself uses.
    ``models.SearchRequest`` satisfies this structurally.
    """

    providers: list[str]
    allow_paid_fallback: bool


def apply_request_policy(
    chain: Sequence[SearchProvider], request: RequestPolicy
) -> tuple[list[SearchProvider], int]:
    """Return the effective chain and the count of ignored ``providers`` entries.

    Algorithm, in order (ruling 29):

    1. Normalise the first eight entries and count the remainder.
       Apply ``strip()`` and ``lower()`` to each kept entry; every entry
       past the eighth is ignored and counted.
    2. Match configured names and count each ignored entry.
       Each kept entry that equals no ``name`` in *chain* is
       ignored and counted (one increment per ignored entry, duplicates
       included; a duplicate of a matching name counts nothing); the matched
       names form the named set.
    3. Keep the longest named paid prefix and every free provider.
       Only when ``request.providers`` is non-empty, keep the longest prefix
       of the configured paid sequence whose every member is in the named
       set. A paid provider after an unnamed paid provider is dropped even
       if named. Free providers are never removed and nothing is reordered.
    4. Remove every remaining paid provider when paid fallback is forbidden.
       ``request.allow_paid_fallback is False`` removes every remaining
       ``paid=True`` provider.

    The output retains every free provider and only a prefix of *chain*'s
    paid sequence -- never reordered or keyed by anything but provider name.
    Empty ``providers`` keeps the whole paid sequence unless step 4 removes it.
    """
    # step 1 — Normalise the first eight entries and count the remainder.
    kept = [entry.strip().lower() for entry in request.providers[:_MAX_POLICY_ENTRIES]]
    ignored = max(0, len(request.providers) - _MAX_POLICY_ENTRIES)

    # step 2 — Match configured names and count each ignored entry.
    chain_names = {provider.name for provider in chain}
    named: set[str] = set()
    for entry in kept:
        if entry in chain_names:
            named.add(entry)
        else:
            ignored += 1

    # step 3 — Keep the longest named paid prefix and every free provider.
    effective: list[SearchProvider] = []
    paid_prefix_open = True
    for provider in chain:
        if provider.paid and request.providers and provider.name not in named:
            paid_prefix_open = False
        if not provider.paid or paid_prefix_open:
            effective.append(provider)

    # step 4 — Remove every remaining paid provider when paid fallback is forbidden.
    if request.allow_paid_fallback is False:
        effective = [provider for provider in effective if not provider.paid]
    return effective, ignored
