"""The SearXNG backend behind the ``SearchProvider`` seam.

SearXNG is Forage's key-less free floor: a deployment with no paid API key
still searches, because this provider needs nothing but a reachable SearXNG
instance. The HTTP call here is the one that used to sit inline in
``pipeline/orchestrator.py``'s ``run_search_pipeline``; moving it changed no
wire behaviour except the two documented deviations —
:data:`trust_env=False <httpx.AsyncClient>` on the client (contract point 2)
and the narrowed ``reason`` text the orchestrator composes from
:attr:`SearxngProvider.origin` instead of the raw configured URL.

``DEFAULT_SEARXNG_URL`` and ``SEARXNG_ENGINES`` are defined **here, once**.
``pipeline/orchestrator.py`` keeps ``_DEFAULT_SEARXNG_URL`` /
``_SEARXNG_ENGINES`` as assigned aliases for the three test modules that
import the private names from it; there is never a second copy of the engine
string.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from pipeline.bounded_body import (
    BodyTooLarge,
    MalformedBody,
    UnsupportedEncoding,
    read_bounded_body,
)
from pipeline.config_bounds import bounded_float, bounded_int
from pipeline.search_providers.base import (
    FailureClass,
    ProviderFailure,
    ProviderSearchResult,
)

logger = logging.getLogger(__name__)

# The chain token this provider answers to, and the one identifier it carries:
# the `FORAGE_SEARCH_PROVIDERS` entry an operator writes, the registry key, and
# the name `orchestrator` compares against when deciding whether a refusal gets
# the legacy `searxng_*` codes. A module constant rather than a read off
# `SearxngProvider` because the comparison must survive a test that patches the
# class as a factory — and because the token is a *configuration* string, not a
# property of any one implementation of it.
SEARXNG_PROVIDER_NAME = "searxng"

# Default SearXNG URL (overridable via the SEARXNG_URL environment variable —
# see docs/configuration.md). Deliberately a neutral service name: Forage has
# no opinion about the compose project it is dropped into.
DEFAULT_SEARXNG_URL = "http://searxng:8080"

# Engines pinned on every SearXNG query. Without an explicit list SearXNG
# fans out to every engine its image defaults enable — and with
# `use_default_settings: true` on a :latest image, upstream releases keep
# adding engines our config never vetted (observed live 2026-08-19: aol,
# "karmasearch videos"). Must stay in sync with the enabled set in
# searxng/config/settings.yml.
SEARXNG_ENGINES = "duckduckgo,brave,startpage,mojeek"

DEFAULT_SEARXNG_TIMEOUT_SECONDS = 10.0
DEFAULT_SEARXNG_QUERY_MAX_CHARS = 400

# The closed `detail` vocabulary, minus the one open family: a status-derived
# detail is `http_` followed by the integer status code (`http_429`,
# `http_500`). Nothing else may ever reach a `ProviderFailure` from here —
# in particular never `str(exc)`, which for httpx embeds the request URL and
# with it the query string and any userinfo in SEARXNG_URL.
_SEARXNG_FAILURE_DETAILS = frozenset(
    {
        "timeout",
        "connect_error",
        "body_too_large",
        "unsupported_encoding",
        "bad_json",
        "malformed_body",
        "unexpected",
    }
)

# Public because `pipeline/orchestrator.py` tells the status family apart
# from the closed tokens with it when it maps a failure onto
# `searxng_error` vs `searxng_unavailable`; one definition, read on both
# sides of the seam.
HTTP_STATUS_DETAIL_PREFIX = "http_"
_RATE_LIMITED_STATUS = 429

# What `origin` says when the operator's endpoint cannot be reduced to a
# scheme and a hostname at all. A closed token rather than the raw string,
# which could carry userinfo (contract point 7).
UNPARSEABLE_ENDPOINT = "unparseable-endpoint"


class SearxngConfigurationError(ValueError):
    """Raised when a SearXNG tunable in ``config.yaml`` exceeds its safe bounds."""


@dataclass(frozen=True, slots=True)
class SearxngSettings:
    """Validated SearXNG tunables for one process start."""

    timeout_seconds: float = DEFAULT_SEARXNG_TIMEOUT_SECONDS
    # Both raw input and Forage-decoded output are bounded before JSON parsing.
    max_response_bytes: int = 1_048_576
    query_max_chars: int = DEFAULT_SEARXNG_QUERY_MAX_CHARS


def searxng_settings_from_config(config: dict[str, Any]) -> SearxngSettings:
    """Validate settings at boot even when SearXNG is absent from the chain."""
    return SearxngSettings(
        timeout_seconds=bounded_float(
            config,
            "search_searxng_timeout_seconds",
            DEFAULT_SEARXNG_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=60.0,
            error=SearxngConfigurationError,
        ),
        query_max_chars=bounded_int(
            config,
            "search_searxng_query_max_chars",
            DEFAULT_SEARXNG_QUERY_MAX_CHARS,
            minimum=50,
            maximum=400,
            error=SearxngConfigurationError,
        ),
    )


def _compute_origin(base_url: str) -> str:
    """Reduce *base_url* to scheme, hostname and port, userinfo stripped.

    This runs in ``__init__`` and must not raise: ``run_search_pipeline``
    constructs the provider outside any ``try``, and ``retrieval_app.py``
    only handles :class:`PipelineError`, so a ``ValueError`` escaping here
    would turn a typo in ``SEARXNG_URL`` from a 422 into a 500. Both of
    ``urlsplit()``'s failure modes are therefore caught: the split itself
    (``http://[::1``) and the lazy ``.port`` parse (``http://host:99999``,
    ``http://host:notaport``). A port that will not parse still leaves a
    usable scheme and hostname; anything less falls back to the closed
    :data:`UNPARSEABLE_ENDPOINT` token, never the raw string.
    """
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return UNPARSEABLE_ENDPOINT

    hostname = parsed.hostname
    if not parsed.scheme or not hostname:
        return UNPARSEABLE_ENDPOINT

    # An IPv6 literal comes back from `hostname` unbracketed; a netloc needs
    # the brackets back (the `_canonicalize_search_url` idiom).
    host = f"[{hostname}]" if ":" in hostname else hostname
    try:
        port = parsed.port
    except ValueError:
        return f"{parsed.scheme}://{host}"

    if port is None:
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


class SearxngProvider:
    """Query a SearXNG instance and return its raw results, or a typed failure.

    The endpoint comes from operator configuration (``SEARXNG_URL``) read at
    process start and is exempt from ``validate_url`` on that basis alone
    (contract point 1). Results are attacker-controlled: this class
    normalizes, bounds and scans none of them — the orchestrator's
    sanitization loop is the only path to the wire (contract point 3).

    ``search()`` never raises. Every failure mode maps onto a
    :class:`ProviderFailure` with a ``detail`` from
    :data:`_SEARXNG_FAILURE_DETAILS` or the ``http_<status>`` family, ending
    in a catch-all (contract point 4).
    """

    name = SEARXNG_PROVIDER_NAME
    paid = False

    def __init__(
        self,
        base_url: str = DEFAULT_SEARXNG_URL,
        settings: SearxngSettings | None = None,
    ) -> None:
        self.base_url = base_url
        self.settings = settings if settings is not None else SearxngSettings()
        # Typed `str | None` to match the protocol member exactly — a
        # protocol's mutable attributes are invariant, so a narrower `str`
        # here would make this class stop satisfying `SearchProvider`. The
        # value is in practice always a string.
        self.origin: str | None = _compute_origin(base_url)

    async def search(
        self, query: str, max_results: int
    ) -> ProviderSearchResult | ProviderFailure:
        """Search with a capped outbound copy, leaving the caller's query intact."""
        outbound_query = query[: self.settings.query_max_chars]
        compressed = False
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                trust_env=False,
                follow_redirects=False,
                headers={"Accept-Encoding": "identity"},
            ) as client:
                async with asyncio.timeout(self.settings.timeout_seconds):
                    async with client.stream(
                        "GET",
                        f"{self.base_url}/search",
                        params={
                            "q": outbound_query,
                            "format": "json",
                            "pageno": 1,
                            "engines": SEARXNG_ENGINES,
                        },
                    ) as response:
                        compressed = (
                            response.headers.get("content-encoding", "identity")
                            .strip()
                            .lower()
                            != "identity"
                        )
                        status = response.status_code
                        if not 200 <= status < 300:
                            failure_class: FailureClass = (
                                "rate_limited"
                                if status == _RATE_LIMITED_STATUS
                                else "hard_error"
                            )
                            return self._failure(
                                failure_class,
                                f"{HTTP_STATUS_DETAIL_PREFIX}{status}",
                                compressed=compressed,
                            )
                        body = await read_bounded_body(
                            response, max_bytes=self.settings.max_response_bytes
                        )
                try:
                    payload = cast("object", json.loads(body))
                except ValueError:
                    return self._failure(
                        "hard_error", "bad_json", compressed=compressed
                    )
                return replace(
                    self._build_result(payload, max_results), compressed=compressed
                )
        except (TimeoutError, httpx.TimeoutException):
            return self._failure("timeout", "timeout", compressed=compressed)
        except BodyTooLarge:
            return self._failure("hard_error", "body_too_large", compressed=compressed)
        except UnsupportedEncoding:
            return self._failure(
                "hard_error", "unsupported_encoding", compressed=compressed
            )
        except MalformedBody:
            return self._failure("hard_error", "malformed_body", compressed=compressed)
        except httpx.HTTPError:
            return self._failure("hard_error", "connect_error", compressed=compressed)
        except Exception:
            return self._failure("hard_error", "unexpected", compressed=compressed)

    def _build_result(
        self, payload: object, max_results: int
    ) -> ProviderSearchResult | ProviderFailure:
        """Read the raw result dicts off a parsed SearXNG body."""
        if not isinstance(payload, dict):
            return self._failure("hard_error", "bad_json")
        data = cast("dict[str, Any]", payload)

        results = cast("object", data.get("results", []))
        if not isinstance(results, list):
            return self._failure("hard_error", "malformed_body")
        entries = cast("list[Any]", results)
        if any(not isinstance(entry, dict) for entry in entries):
            return self._failure("hard_error", "malformed_body")

        # Raw dicts, straight through, plus `date` read off SearXNG's
        # `publishedDate` (US-004 consumes it). No normalization here.
        raw_results = [
            {**entry, "date": entry.get("publishedDate")}
            for entry in cast("list[dict[str, Any]]", entries)[:max_results]
        ]

        # SearXNG reports an unresponsive engine as either a bare name or a
        # `(name, reason)` pair. Unchanged from the inline call; a body that
        # makes this raise lands on the `unexpected` catch-all, exactly as a
        # body that made the old blanket `except Exception` fire did.
        unresponsive_engines = [
            cast("Any", entry)[0] if isinstance(entry, list | tuple) else str(entry)
            for entry in cast("list[Any]", data.get("unresponsive_engines", []))
        ]
        return ProviderSearchResult(
            provider_name=self.name,
            results=raw_results,
            unresponsive_engines=cast("list[str]", unresponsive_engines),
        )

    def _failure(
        self, failure_class: FailureClass, detail: str, *, compressed: bool = False
    ) -> ProviderFailure:
        """Log the closed tokens and return the typed failure.

        The vocabulary is closed *by construction* rather than by review: a
        ``detail`` that is neither a registered token nor a member of the
        ``http_<status>`` family collapses to ``unexpected`` here, so no
        future caller can widen what reaches the wire by passing a new
        string.

        The log line carries the provider name, the failure class and the
        detail token in its message and nothing else: no ``exc_info``, no
        ``str(exc)``, no URL (CLAUDE.md invariant 6 — ``SEARXNG_URL`` may
        carry a password, and an httpx message embeds the request URL).
        """
        if detail not in _SEARXNG_FAILURE_DETAILS and not detail.startswith(
            HTTP_STATUS_DETAIL_PREFIX
        ):
            detail = "unexpected"
        # The three tokens go in the message itself, as `%s` arguments — not
        # in `extra=`, which no configured formatter renders
        # (`kit_tools/arch/patterns/LOGGING.md`): an operator reading the
        # bare marker could not tell a timeout from a 429 from a malformed
        # body. All three are closed vocabulary, so nothing here can carry a
        # credential or an endpoint.
        logger.warning(
            "search_provider_failure — provider=%s failure_class=%s detail=%s",
            self.name,
            failure_class,
            detail,
        )
        return ProviderFailure(
            provider_name=self.name,
            failure_class=failure_class,
            detail=detail,
            compressed=compressed,
        )
