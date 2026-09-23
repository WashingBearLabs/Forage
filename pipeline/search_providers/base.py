"""The ``SearchProvider`` seam: a typed success/failure surface over search backends.

``SearchProvider`` is the protocol-agnostic entry point every backend
(``SearxngProvider`` today, a paid API later) implements, so the orchestrator's
sanitization loop never has to know which backend served a request. Its
``search()`` returns exactly one of two internal (never wire) types:
:class:`ProviderSearchResult` on success, :class:`ProviderFailure` on a
closed, typed failure. Neither type is ever constructed from the orchestrator
side, and neither ever reaches ``/search``'s response body directly — the
orchestrator loop normalizes, bounds, and scans raw results into the wire
``SearchResult`` (``models.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

from pipeline.contract import CONTENT_KIND_SNIPPET, ContentKind

FailureClass = Literal["rate_limited", "timeout", "hard_error", "auth", "quota"]
"""Closed vocabulary for how a provider's ``search()`` call failed.

Every provider maps its own errors onto this fixed set and pairs each with a
``detail`` token from its own closed vocabulary (never ``str(exc)``, never a
URL). ``FailureClass`` stays internal to ``pipeline/search_providers/`` — the
orchestrator composes ``provider_errors`` and ``search_unavailable`` from
``name`` and ``failure_class`` alone. A configured ``[searxng]``-only chain
also exposes SearXNG's closed ``detail`` in its ``searxng_unavailable`` 422
reason; Brave's detail is not wire-visible. The Literal itself never crosses
into ``pipeline/contract.py``.
"""

FAILURE_CLASSES = frozenset(get_args(FailureClass))


@dataclass(frozen=True, slots=True)
class ProviderSearchResult:
    """A provider's successful ``search()`` response — internal, never the wire shape.

    ``results`` is a list of raw dicts in the ``{title, url, content, engine,
    date}`` shape a provider read off its backend, unnormalized, unbounded,
    and unscanned: the orchestrator's sanitization loop is the only path from
    here to a wire ``SearchResult``. An **empty** ``results`` list is still a
    success — a provider that legitimately found nothing returns this, never
    a :class:`ProviderFailure`.

    ``unresponsive_engines`` is SearXNG's vocabulary specifically: every other
    provider returns ``[]`` here.

    ``content_kind`` describes the whole batch, not a result: one ``search()``
    call reaches one backend in one mode, so every dict in ``results`` carries
    the same kind and the orchestrator copies it onto each wire
    ``SearchResult`` it builds. SearXNG returns engine summaries and leaves
    the ``"snippet"`` default; a provider that returns extracted page passages
    sets ``"chunk"``.
    """

    provider_name: str
    results: list[dict[str, Any]]
    unresponsive_engines: list[str]
    content_kind: ContentKind = CONTENT_KIND_SNIPPET
    compressed: bool = False


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """A provider's failed ``search()`` call — internal, never the wire shape.

    ``detail`` is always a token drawn from the provider's own closed
    vocabulary — never ``str(exc)``, never a URL, never a header or parameter
    value. ``provider_errors`` and ``search_unavailable`` compose from
    ``provider_name`` and ``failure_class`` alone; Brave's detail is not
    wire-visible. On a configured ``[searxng]``-only chain, SearXNG's detail
    also reaches the ``searxng_unavailable`` 422 reason, which is why that
    vocabulary must remain closed.
    """

    provider_name: str
    failure_class: FailureClass
    detail: str
    compressed: bool = False


class SearchProvider(Protocol):
    """A pluggable web-search backend behind a typed success/failure seam.

    ``name`` is the **one** identifier a provider carries: the
    ``FORAGE_SEARCH_PROVIDERS`` chain token, the provider registry key,
    per-request provider attribution, the ``search_unavailable`` reason, and
    ``/health``'s provider status all carry this same string and nothing
    else. Per-result ``engine`` (inside :class:`ProviderSearchResult`) is
    **provenance, never identity** — SearXNG passes its own sub-engine
    through (its ``brave`` sub-engine included) while a Brave-backed
    provider's ``name`` stays ``"brave"``, so the two stay distinguishable
    even though both can say "brave".

    ``paid`` is ``False`` for SearXNG; a paid-fallback policy stops before
    the first ``paid=True`` provider in the chain.

    ``origin`` is the operator-configured endpoint reduced to scheme,
    hostname and port with userinfo stripped, computed once when the
    provider is constructed — ``None`` for a provider whose endpoint is never
    echoed (e.g. a fixed third-party API). It is the only endpoint-shaped
    value the orchestrator may read through this protocol; anything else a
    concrete provider stores (such as a raw base URL) is not part of the
    seam.

    Every implementation preserves six rules:

    1. Endpoints come only from operator configuration read at process
       start, never from request data, and are exempt from ``validate_url``
       on that basis alone. Results are attacker-controlled and trusted by
       nothing.
    2. Every HTTP client a provider opens is constructed with
       ``follow_redirects`` at its ``False`` default, TLS verification on,
       and ``trust_env=False`` (ambient ``HTTP_PROXY`` / ``HTTPS_PROXY`` /
       ``.netrc`` / ``SSL_CERT_FILE`` never redirect provider egress or swap
       the CA bundle), and bounds the response body **before** it is parsed
       as JSON — an overrun is a :class:`ProviderFailure` (``hard_error``,
       ``body_too_large``), never a parse.
    3. Providers return raw dicts. They never construct a wire
       ``SearchResult``, never normalize, bound, or scan text — the
       orchestrator's sanitization loop is the only path to the wire.
    4. Failures are :class:`ProviderFailure` with a fixed ``detail``; a
       provider log line carries the failure class and the detail token
       only, never a credential or URL. Every provider's mapping ends in a
       catch-all: an unexpected exception becomes
       ``ProviderFailure(failure_class="hard_error", detail="unexpected")``,
       and a valid-JSON body of the wrong shape (``results`` not a list, an
       element not an object) becomes ``hard_error`` / ``malformed_body``.
       **Providers never raise into the orchestrator.**
    5. Provider result bodies are never cached, logged, or persisted.
    6. Providers are stateless per call: an HTTP client is opened inside
       ``search()`` and closed before it returns, so a chain of provider
       objects held for the life of the process needs no shutdown hook.
    """

    name: str
    paid: bool
    origin: str | None

    async def search(
        self, query: str, max_results: int
    ) -> ProviderSearchResult | ProviderFailure:
        """Search for *query*, returning at most *max_results* candidates.

        *max_results* is the literal candidate budget the caller chose for
        this call — a request, never a trusted bound. A provider that
        returns more leaves re-slicing to the caller.
        """
        ...
