"""The Brave LLM-Context backend behind the ``SearchProvider`` seam.

Brave's LLM-Context endpoint returns ranked, extracted **content chunks**
rather than one-line SERP snippets, so ``BraveApiProvider`` sets
``content_kind="chunk"`` on every batch it returns (contract 1.2.0) and
``engine="brave-api"`` on every result dict — deliberately not ``"brave"``,
which is already SearXNG's own sub-engine name for the same upstream
(``pipeline/search_providers/searxng.py``'s ``SEARXNG_ENGINES``); the two
must never be normalized into each other.

The parser here is written against exactly one fixture,
``tests/fixtures/brave/llm_context_sample.json`` — an owner-captured, real
response envelope with every chunk body, title, URL and date replaced by a
synthetic value (``tests/fixtures/README.md``). A hermetic suite cannot tell
a parser that matches Brave's real shape from one that only matches a guess
at it; the pinned sample is what closes that gap.

Brave is a paid backend (``paid = True``) with no free tier: ``$5`` per 1,000
queries, billed per query regardless of how many chunks come back. It is also
a privacy boundary: with a key configured, the caller's query — truncated to
``search_brave_query_max_chars`` — leaves the container for
``api.search.brave.com`` under the operator's Brave account and Brave's terms
(``docs/configuration.md`` records the same trade-off on the key's row). This
module never registers itself — the conditional wiring onto
``FORAGE_SEARCH_PROVIDERS`` is
``pipeline.search_providers.build_provider_chain``'s job
(``feature-brave-provider`` US-002). It does define the one shared predicate
for whether a key is usable at all: :func:`brave_key_present`.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Any, Final, cast

import httpx

from pipeline.contract import CONTENT_KIND_CHUNK
from pipeline.search_providers.base import (
    FailureClass,
    ProviderFailure,
    ProviderSearchResult,
)

logger = logging.getLogger(__name__)

# The chain token this provider answers to — the `FORAGE_SEARCH_PROVIDERS`
# entry, the registry key (US-002), and the `search_unavailable` reason
# prefix (`brave: <failure_class>`, US-003). Never confused with the
# per-result `engine` value below, which is provenance, not identity.
BRAVE_PROVIDER_NAME = "brave"

# The per-result `engine` provenance stamped on every mapped result —
# deliberately not `BRAVE_PROVIDER_NAME`, because `brave` is also SearXNG's own
# sub-engine name for the same upstream (`SEARXNG_ENGINES`), and the two must
# stay distinct on the wire (ruling 23). One definition, so the wire-visible
# literal is written here and nowhere else.
BRAVE_ENGINE: Final = "brave-api"

# The environment variable naming this deployment's Brave API key, on
# root-level `model_fetcher.py`'s `*_ENV_VAR` constant pattern (~179-183). No
# alias (ruling 10) — a fresh `FORAGE_*` name, never a back-compat `POPPY_*`
# one — and no vault or runtime key API: Forage is 12-factor and the
# consumer injects the key into the environment.
BRAVE_API_KEY_ENV_VAR: Final = "FORAGE_BRAVE_API_KEY"

# Characters stripped from both ends before a key is judged present: space,
# tab and line feed, but deliberately *not* carriage return. A file-backed
# secret routinely carries a trailing LF (the `model_fetcher._resolve_token()`
# rule, ~912); stripping only LF-family whitespace means a Windows-style CRLF
# ending strips down to a bare trailing CR, which then fails
# `brave_key_present` as an embedded control character rather than being
# silently absorbed by a wider `str.strip()`.
BRAVE_KEY_STRIP_CHARS: Final = " \t\n"


def brave_key_present(raw: str | None) -> bool:
    """Return whether *raw* is usable as a Brave API key.

    The single definition of "a key is present" (ruling 28): registration in
    `build_provider_chain` and `/health`'s `capabilities["brave_api_key"]`
    (spec 4 US-002) both consume this, so the two can never disagree.

    Three shapes are refused, all before any client exists:

    - Empty after stripping `BRAVE_KEY_STRIP_CHARS` — compose routinely renders
      `FORAGE_BRAVE_API_KEY=` from an unset shell variable, and a
      file-backed secret carries a trailing newline.
    - Not ASCII-only, or not printable (any control character, including a
      lone carriage return) — httpx encodes header values as latin-1, so a
      non-ASCII key would raise `UnicodeEncodeError` at request construction
      with the offending character quoted in the message, and a CR/LF in a
      header value is a client-side construction exception rather than an
      HTTP outcome whose message could carry the value.
    - Interior whitespace — `str.isprintable()` already excludes every
      whitespace character except the ASCII space, so this is the one
      remaining check needed to also refuse a pasted key with an embedded
      space.
    """
    if raw is None:
        return False
    value = raw.strip(BRAVE_KEY_STRIP_CHARS)
    if not value:
        return False
    if not value.isascii() or not value.isprintable():
        return False
    return " " not in value


def usable_brave_key(raw: str | None) -> str | None:
    """The key as it will be sent, or ``None`` when :func:`brave_key_present` says no.

    The one place the strip happens: ``build_provider_chain`` registers the
    provider with this value, and ``retrieval_app._resolve_brave_key`` hands
    the environment through it, so neither re-derives the strip set or can
    disagree with the presence predicate about what "usable" means.
    """
    if raw is None or not brave_key_present(raw):
        return None
    return raw.strip(BRAVE_KEY_STRIP_CHARS)


# A fixed `https://` endpoint — no environment override — so this constant is
# also the one seam a test patches. Confirmed by the owner's capture
# (`tests/fixtures/README.md`); see this feature spec's Implementation Notes
# for exactly what the capture did and did not confirm.
_BRAVE_LLM_CONTEXT_URL: Final = "https://api.search.brave.com/res/v1/llm/context"

# Brave's own auth header name, confirmed by the capture. The key travels
# only here — never in the URL or query string — so no URL-bearing log line
# can carry it.
_BRAVE_AUTH_HEADER: Final = "X-Subscription-Token"

# The shape of the one `sources[url].age` element `_map_generic_entry` takes
# `date` from. Brave sends the age as a list of renderings of the same instant
# (a long form, the ISO calendar date, a relative form, an ISO timestamp), and
# the capture pinned their order — but the mapping selects by shape rather than
# by position so a reordered list still yields the date instead of silently
# dropping every one. Calendar validity is `SearchResult`'s job (`models.py`).
_ISO_CALENDAR_DATE_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")

# The captured envelope was 30,344 bytes; this is at least ten times that,
# checked against `Content-Length` before any read and against a running
# total of decoded bytes while streaming, so a compressed body cannot expand
# past it either.
_BRAVE_MAX_RESPONSE_BYTES: Final = 1_048_576

# The closed `detail` vocabulary this provider ever emits. Twelve fixed
# tokens, never `str(exc)`, never a URL — `_failure` collapses anything else
# to `unexpected` so no future caller can widen what reaches the wire.
_BRAVE_FAILURE_DETAILS: Final = frozenset(
    {
        "http_401",
        "http_403",
        "http_429",
        "http_4xx",
        "http_5xx",
        "redirect_refused",
        "timeout",
        "transport_error",
        "bad_json",
        "malformed_body",
        "body_too_large",
        "unexpected",
    }
)

# ---------------------------------------------------------------------------
# Config.yaml tunables (ruling 9): three top-level scalars, the
# `promptguard_threshold` shape rather than a nested block.
# ---------------------------------------------------------------------------

DEFAULT_BRAVE_TIMEOUT_SECONDS: Final = 15.0
DEFAULT_BRAVE_CHUNK_MAX_CHARS: Final = 2000
DEFAULT_BRAVE_QUERY_MAX_CHARS: Final = 400

_MIN_BRAVE_TIMEOUT_SECONDS: Final = 1.0
_MAX_BRAVE_TIMEOUT_SECONDS: Final = 60.0
# The chunk cap's ceiling equals `_MAX_SEARCH_SNIPPET_LENGTH`
# (`pipeline/orchestrator.py`), so this bound and the loop's own truncation
# can never disagree about the largest chunk PromptGuard ever sees.
_MIN_BRAVE_CHUNK_MAX_CHARS: Final = 200
_MAX_BRAVE_CHUNK_MAX_CHARS: Final = 2000
# Brave documents a 400-character bound (plus a word limit) on `q`.
_MIN_BRAVE_QUERY_MAX_CHARS: Final = 50
_MAX_BRAVE_QUERY_MAX_CHARS: Final = 400


class BraveConfigurationError(ValueError):
    """Raised when a Brave tunable in ``config.yaml`` exceeds its safe bounds."""


@dataclass(frozen=True, slots=True)
class BraveSettings:
    """Validated Brave tunables for one process start."""

    timeout_seconds: float = DEFAULT_BRAVE_TIMEOUT_SECONDS
    chunk_max_chars: int = DEFAULT_BRAVE_CHUNK_MAX_CHARS
    query_max_chars: int = DEFAULT_BRAVE_QUERY_MAX_CHARS


def _bounded_float(
    config: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """Read one bounded float setting without accepting bool values."""
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BraveConfigurationError(f"{key} must be a number")
    value = float(value)
    if not minimum <= value <= maximum:
        raise BraveConfigurationError(f"{key} must be between {minimum} and {maximum}")
    return value


def _bounded_int(
    config: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Read one bounded integer setting without accepting bool values.

    The same idiom as ``cache.py``'s helper, deliberately re-stated: provider
    modules import nothing from the app's other configuration surfaces.
    """
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BraveConfigurationError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise BraveConfigurationError(f"{key} must be between {minimum} and {maximum}")
    return value


def brave_settings_from_config(config: dict[str, Any]) -> BraveSettings:
    """Build bounded Brave settings from the sidecar configuration.

    Called **unconditionally** from the lifespan, beside
    ``cache_settings_from_config`` — whether or not ``"brave"`` appears in
    the resolved provider chain — so a wrong-typed or out-of-range value
    refuses boot on every deployment, exactly as the ``cache:`` block does.
    """
    return BraveSettings(
        timeout_seconds=_bounded_float(
            config,
            "search_brave_timeout_seconds",
            DEFAULT_BRAVE_TIMEOUT_SECONDS,
            minimum=_MIN_BRAVE_TIMEOUT_SECONDS,
            maximum=_MAX_BRAVE_TIMEOUT_SECONDS,
        ),
        chunk_max_chars=_bounded_int(
            config,
            "search_brave_chunk_max_chars",
            DEFAULT_BRAVE_CHUNK_MAX_CHARS,
            minimum=_MIN_BRAVE_CHUNK_MAX_CHARS,
            maximum=_MAX_BRAVE_CHUNK_MAX_CHARS,
        ),
        query_max_chars=_bounded_int(
            config,
            "search_brave_query_max_chars",
            DEFAULT_BRAVE_QUERY_MAX_CHARS,
            minimum=_MIN_BRAVE_QUERY_MAX_CHARS,
            maximum=_MAX_BRAVE_QUERY_MAX_CHARS,
        ),
    )


class BraveApiProvider:
    """Query Brave's LLM-Context endpoint and return its chunks, or a typed failure.

    The endpoint is a fixed constant, never operator-configured, so
    ``origin`` is always ``None`` (contract point 1 of the ``SearchProvider``
    protocol docstring: "``None`` for a provider whose endpoint is never
    echoed"). The API key is held only in ``_api_key`` — never in ``__repr__``
    or ``__str__``, and it travels only in the ``X-Subscription-Token``
    header, never a URL or query string.

    ``search()`` never raises: every exception and every non-2xx response is
    caught and mapped onto a :class:`ProviderFailure` from a closed,
    twelve-token vocabulary (contract point 4).
    """

    name = BRAVE_PROVIDER_NAME
    paid = True

    def __init__(self, api_key: str, settings: BraveSettings | None = None) -> None:
        self._api_key = api_key
        self.settings = settings if settings is not None else BraveSettings()
        self.origin: str | None = None

    def __repr__(self) -> str:
        """Emit the provider name only — never the key (``str()`` delegates here)."""
        return f"{type(self).__name__}(name={self.name!r})"

    async def search(
        self, query: str, max_results: int
    ) -> ProviderSearchResult | ProviderFailure:
        """Search Brave for *query*, returning at most *max_results* dicts.

        *query* is truncated to ``settings.query_max_chars`` before it leaves
        the process (ruling 30) — a Brave-specific outbound bound, since
        ``SearchRequest.query`` itself carries no such limit on the wire.
        """
        outbound_query = query[: self.settings.query_max_chars]
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                verify=ssl.create_default_context(),
            ) as client:
                async with client.stream(
                    "GET",
                    _BRAVE_LLM_CONTEXT_URL,
                    params={"q": outbound_query, "count": max_results},
                    headers={_BRAVE_AUTH_HEADER: self._api_key},
                ) as response:
                    if response.status_code != 200:
                        return self._failure_for_status(response.status_code)

                    content_length = response.headers.get("content-length")
                    if (
                        content_length is not None
                        and content_length.isdigit()
                        and int(content_length) > _BRAVE_MAX_RESPONSE_BYTES
                    ):
                        return self._failure("hard_error", "body_too_large")

                    chunks: list[bytes] = []
                    running = 0
                    async for chunk in response.aiter_bytes():
                        running += len(chunk)
                        if running > _BRAVE_MAX_RESPONSE_BYTES:
                            return self._failure("hard_error", "body_too_large")
                        chunks.append(chunk)
                    body = b"".join(chunks)

                try:
                    payload = cast("object", json.loads(body))
                except ValueError:
                    return self._failure("hard_error", "bad_json")
                return self._build_result(payload, max_results)
        except httpx.TimeoutException:
            return self._failure("timeout", "timeout")
        except httpx.HTTPError:
            return self._failure("hard_error", "transport_error")
        except Exception:
            return self._failure("hard_error", "unexpected")

    def _build_result(
        self, payload: object, max_results: int
    ) -> ProviderSearchResult | ProviderFailure:
        """Read the raw result dicts off a parsed LLM-Context body.

        Follows the pinned sample (``tests/fixtures/brave/llm_context_sample.json``):
        a missing ``grounding`` or ``generic`` key, or a null one, is a clean
        zero-result success — the capture came from a query that returned
        results, so the empty shape is defined here. ``grounding.generic``
        present but not a list, or the top-level body not an object, or any
        element of ``generic`` not an object, is ``malformed_body``: one
        wrong-shaped element invalidates the whole response rather than
        being silently skipped (ruling 27's catch-all is for exceptions, not
        a per-element skip — see this spec's Implementation Notes). ``map``
        is ignored; an absent ``poi`` is tolerated by construction, since
        this parser never looks for it.
        """
        if not isinstance(payload, dict):
            return self._failure("hard_error", "malformed_body")
        data = cast("dict[str, Any]", payload)

        grounding = data.get("grounding")
        if grounding is None:
            return self._empty_result()
        if not isinstance(grounding, dict):
            return self._failure("hard_error", "malformed_body")
        grounding_dict = cast("dict[str, Any]", grounding)

        generic = grounding_dict.get("generic")
        if generic is None:
            return self._empty_result()
        if not isinstance(generic, list):
            return self._failure("hard_error", "malformed_body")
        generic_list = cast("list[Any]", generic)
        if any(not isinstance(entry, dict) for entry in generic_list):
            return self._failure("hard_error", "malformed_body")

        sources_raw = data.get("sources")
        sources: dict[str, Any] = (
            cast("dict[str, Any]", sources_raw) if isinstance(sources_raw, dict) else {}
        )

        results = [
            self._map_generic_entry(cast("dict[str, Any]", entry), sources)
            for entry in generic_list[:max_results]
        ]
        return ProviderSearchResult(
            provider_name=self.name,
            results=results,
            unresponsive_engines=[],
            content_kind=CONTENT_KIND_CHUNK,
        )

    def _map_generic_entry(
        self, entry: dict[str, Any], sources: dict[str, Any]
    ) -> dict[str, Any]:
        """Map one ``grounding.generic`` element to a raw result dict.

        A missing or non-string ``title``/``url`` maps to ``""``; the loop's
        existing omission rules (``pipeline/orchestrator.py``) decide from
        there. ``content`` is the element's ``snippets`` joined in order by a
        blank line and capped to ``settings.chunk_max_chars``. ``date`` is the
        first element of ``sources[url].age`` shaped like an ISO calendar date
        (``_ISO_CALENDAR_DATE_RE``), else ``None`` — validated as a *real*
        calendar date by ``SearchResult`` itself (``models.py``), not
        re-validated here.
        """
        url = entry.get("url")
        title = entry.get("title")
        snippets = entry.get("snippets")

        content = ""
        if isinstance(snippets, list):
            pieces = [
                piece for piece in cast("list[Any]", snippets) if isinstance(piece, str)
            ]
            content = "\n\n".join(pieces)[: self.settings.chunk_max_chars]

        date: str | None = None
        source = sources.get(url) if isinstance(url, str) else None
        if isinstance(source, dict):
            age = cast("dict[str, Any]", source).get("age")
            if isinstance(age, list):
                date = next(
                    (
                        candidate
                        for candidate in cast("list[Any]", age)
                        if isinstance(candidate, str)
                        and _ISO_CALENDAR_DATE_RE.fullmatch(candidate)
                    ),
                    None,
                )

        return {
            "title": title if isinstance(title, str) else "",
            "url": url if isinstance(url, str) else "",
            "content": content,
            "engine": BRAVE_ENGINE,
            "date": date,
        }

    def _empty_result(self) -> ProviderSearchResult:
        """A valid, zero-source response — a success, never a failure."""
        return ProviderSearchResult(
            provider_name=self.name,
            results=[],
            unresponsive_engines=[],
            content_kind=CONTENT_KIND_CHUNK,
        )

    def _failure_for_status(self, status_code: int) -> ProviderFailure:
        """Map a non-200 status onto the closed failure vocabulary."""
        if status_code == 401:
            return self._failure("auth", "http_401")
        if status_code == 403:
            return self._failure("auth", "http_403")
        if status_code == 429:
            return self._failure("rate_limited", "http_429")
        if 300 <= status_code < 400:
            return self._failure("hard_error", "redirect_refused")
        if 500 <= status_code < 600:
            return self._failure("hard_error", "http_5xx")
        if 400 <= status_code < 500:
            return self._failure("hard_error", "http_4xx")
        return self._failure("hard_error", "unexpected")

    def _failure(self, failure_class: FailureClass, detail: str) -> ProviderFailure:
        """Log the closed tokens and return the typed failure.

        The vocabulary is closed *by construction* rather than by review: a
        ``detail`` that is not one of the twelve fixed tokens collapses to
        ``unexpected`` here. The tokens go in the message itself, as ``%s``
        arguments (``kit_tools/arch/patterns/LOGGING.md`` ~138: ``extra=``
        renders nowhere an operator can see it) — never a URL, a header
        value, or ``str(exc)`` (CLAUDE.md invariant 6).
        """
        if detail not in _BRAVE_FAILURE_DETAILS:
            failure_class = "hard_error"
            detail = "unexpected"
        logger.warning("brave_search_failed — %s (%s)", detail, failure_class)
        return ProviderFailure(
            provider_name=self.name,
            failure_class=failure_class,
            detail=detail,
        )
