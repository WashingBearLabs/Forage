"""Route drivers: push one corpus record through the real app, read the wire.

Every drive is an HTTP request through ``httpx.ASGITransport`` against the app
booted by its real lifespan. The classifier double sits below stage 3 (see
``scripts.corpus.replay``), so the only things replaced are the boundaries the
service reaches outward through: the weight fetch, the content cache, the
search provider chain and — on ``/retrieve`` — ``validate_url`` and
``fetch_url``. No ``run_promptguard`` mock, no ``run_search_pipeline`` call.

Nothing here imports ``tests`` and nothing here names a private symbol:
``_load_config`` and ``ContentCache`` are patched by string target.

The ``/retrieve`` drive patches two module attributes with
``unittest.mock.patch``, which is process-global; drives are therefore
sequential, and a future parallel ``drive_all`` must serialise that seam.
"""

from __future__ import annotations

import copy
import os
from collections.abc import AsyncGenerator, Iterable, Sequence
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import httpx
import yaml

import model_fetcher
import retrieval_app
from pipeline.contract import CONTENT_KIND_CHUNK, CONTENT_KIND_SNIPPET, ContentKind
from pipeline.search_providers.base import ProviderSearchResult
from pipeline.stage5_url_audit import FetchResult
from retrieval_app import app, lifespan
from scripts.corpus.doubles import CorpusContentCache, CorpusSearchProvider
from scripts.corpus.outcomes import (
    ROUTE_BY_SURFACE,
    HarnessError,
    Route,
    RouteResult,
    RuleConfig,
    interpret_response,
)
from scripts.corpus.records import CorpusLintError, CorpusRecord, page_document
from scripts.corpus.replay import ReplayClassifier, UnrecordedTextError

NO_CLASSIFIER_MODEL_ID: Final = "unavailable"
"""``RouteResult.model_id`` when a drive runs with ``classifier=None``."""

# Everything the lifespan reads from the environment before a driver can touch
# state: the model selection, the cache backend and its signing key, the search
# chain and the Brave key, and the two break-glass switches.
_SCRUBBED_ENV: Final[tuple[str, ...]] = (
    "VALKEY_URL",
    "FORAGE_CACHE_HMAC_KEY",
    "FORAGE_MODEL_ID",
    "FORAGE_MODEL_REVISION",
    "FORAGE_SEARCH_PROVIDERS",
    "FORAGE_BRAVE_API_KEY",
    "FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION",
    "POPPY_RETRIEVAL_LEGACY_CAPABILITY",
)

_CONTIGUITY_KEYS: Final[dict[str, object]] = {
    "promptguard_contiguity_windows": 2,
    "promptguard_contiguity_threshold": 0.5,
}

# No `providers` and no `allow_paid_fallback`: with both omitted, a free
# provider passes the request policy unfiltered, which is what lets the drive
# swap the chain on `app.state` after boot.
_SEARCH_BODY: Final[dict[str, object]] = {
    "query": "corpus",
    "num_results": 5,
    "promptguard_fail_closed": True,
}
_FETCHED_ADDRESS: Final = "93.184.215.14"

# The `app.state` attributes a drive overwrites: the lifespan sets them and the
# drivers replace them per request. The app is a module-level singleton, so a
# value left behind reaches whatever runs next in the process — `search_providers`
# is `None` at import, and a leftover `CorpusSearchProvider` answers a later
# test's `/search` with a served result.
_DRIVEN_STATE: Final[tuple[str, ...]] = ("search_providers", "cache", "classifier")
_ABSENT: Final = object()


class UnrecordedRecordError(Exception):
    """A drive needed scores the cassette does not have.

    Names the record, route, configuration and model, and an 8-character hash
    prefix of the classified text — never the text.
    """

    def __init__(
        self,
        record_id: str,
        route: str,
        config: str,
        model_id: str,
        sha_prefix: str,
    ) -> None:
        super().__init__(record_id, route, config, model_id, sha_prefix)
        self.record_id = record_id
        self.route = route
        self.config = config
        self.model_id = model_id
        self.sha_prefix = sha_prefix

    def __str__(self) -> str:
        return (
            f"{self.record_id}: {self.route} [{self.config}] model={self.model_id} "
            f"has no recorded scores for text sha256:{self.sha_prefix}"
        )


@dataclass(frozen=True, slots=True)
class WireExchange:
    """One HTTP answer: the status and the decoded JSON body (``None`` if none)."""

    status_code: int
    body: object


def _shipped_config() -> dict[str, Any]:
    """The real ``config.yaml`` the service loads, as a fresh dict."""
    path = Path(retrieval_app.__file__).resolve().parent / "config.yaml"
    with path.open(encoding="utf-8") as handle:
        loaded: dict[str, Any] | None = yaml.safe_load(handle)
    return loaded or {}


def _acquisition_that_never_loads(*_args: object, **_kwargs: object) -> bool:
    """One acquisition attempt that finishes instantly without loading."""
    return False


@asynccontextmanager
async def corpus_app(
    *,
    classifier: ReplayClassifier | None,
    config: RuleConfig,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Boot the app through its real lifespan and yield a client for it.

    The shape is ``tests/test_app.py``'s ``_running_app``, written out here as
    the public helper because ``scripts/`` cannot import a private test symbol.
    It differs in the one way that is its whole point: it can never fetch
    weights, and it is independent of the caller's shell.

    * ``config.yaml`` is loaded as shipped, plus ``extract_route_enabled`` (the
      middleware answers 404 otherwise) and, for ``"contiguity"``, the two
      contiguity keys — validated at boot into ``app.state.promptguard_settings``.
    * The environment the lifespan reads is scrubbed for the duration (see
      ``_SCRUBBED_ENV``), and ``ContentCache`` is a double, so a ``VALKEY_URL``
      or a non-allowlisted ``FORAGE_MODEL_ID`` in the caller's shell can neither
      open a connection nor refuse the boot.
    * ``model_fetcher.acquire_and_load`` is patched never to load and its retry
      backoff is pushed out an hour, so the acquisition loop does nothing. The
      replay classifier is installed *after* the lifespan starts, on
      ``app.state.classifier`` (handlers read it per request); the real,
      unloaded one the lifespan built is discarded and never loads. Patching
      the ``PromptGuardClassifier`` factory instead would close the brief
      window in which state holds it, at the cost of a no-op
      ``configure_threads`` on the replay class; this recipe was preferred.
    * With ``classifier=None`` nothing is installed and the lifespan's own
      unloaded classifier stays — exactly the weight-less boot, so stage 3
      takes its unavailable path.
    * ``app.state.search_providers``, ``cache`` and ``classifier`` are put back
      as they were on the way out, so nothing a drive installs outlives it.

    A ``contiguity`` config the lifespan refuses raises out of this manager.
    """
    overrides: dict[str, object] = {"extract_route_enabled": True}
    if config == "contiguity":
        overrides.update(_CONTIGUITY_KEYS)
    booted_config = {**_shipped_config(), **overrides}
    environment = {
        name: value for name, value in os.environ.items() if name not in _SCRUBBED_ENV
    }
    state_before = {name: getattr(app.state, name, _ABSENT) for name in _DRIVEN_STATE}
    try:
        async with _booted(
            classifier=classifier, booted_config=booted_config, environment=environment
        ) as client:
            yield client
    finally:
        for name, value in state_before.items():
            if value is _ABSENT:
                if hasattr(app.state, name):
                    delattr(app.state, name)
            else:
                setattr(app.state, name, value)


@asynccontextmanager
async def _booted(
    *,
    classifier: ReplayClassifier | None,
    booted_config: dict[str, Any],
    environment: dict[str, str],
) -> AsyncGenerator[httpx.AsyncClient, None]:
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        stack.enter_context(
            patch(
                "retrieval_app._load_config",
                side_effect=lambda: copy.deepcopy(booted_config),
            )
        )
        cache_factory = stack.enter_context(patch("retrieval_app.ContentCache"))
        cache_factory.return_value = CorpusContentCache()
        stack.enter_context(
            patch.object(
                model_fetcher, "acquire_and_load", _acquisition_that_never_loads
            )
        )
        stack.enter_context(
            patch.object(model_fetcher, "RETRY_INITIAL_BACKOFF_S", 3600.0)
        )
        async with lifespan(app):
            if classifier is not None:
                app.state.classifier = classifier
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                yield client


def route_of(record: CorpusRecord) -> Route:
    """The route a record's surface drives; an unknown surface fails the lint."""
    route = ROUTE_BY_SURFACE.get(record.surface)
    if route is None:
        raise CorpusLintError(record.id, "payload_keys")
    return route


def _field(record: CorpusRecord, key: str) -> str:
    value = record.payload.get(key)
    if value is None:
        raise CorpusLintError(record.id, "payload_keys")
    return value


def _search_outcome(record: CorpusRecord) -> ProviderSearchResult:
    kind = record.payload.get("content_kind", CONTENT_KIND_SNIPPET)
    if kind not in (CONTENT_KIND_SNIPPET, CONTENT_KIND_CHUNK):
        raise CorpusLintError(record.id, "payload_keys")
    content_kind: ContentKind = (
        CONTENT_KIND_CHUNK if kind == CONTENT_KIND_CHUNK else CONTENT_KIND_SNIPPET
    )
    raw: dict[str, Any] = {
        "title": _field(record, "title"),
        "url": _field(record, "url"),
        "content": _field(record, "content"),
        "date": None,
    }
    if "engine" in record.payload:
        raw["engine"] = record.payload["engine"]
    return ProviderSearchResult(
        provider_name="searxng",
        results=[raw],
        unresponsive_engines=[],
        content_kind=content_kind,
    )


def _decode(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


async def _post_search(
    client: httpx.AsyncClient, record: CorpusRecord
) -> httpx.Response:
    # The handler reads the chain per request, through the request policy; a
    # free provider passes it unfiltered while the body names no provider.
    app.state.search_providers = [
        CorpusSearchProvider(name="searxng", outcome=_search_outcome(record))
    ]
    return await client.post("/search", json=_SEARCH_BODY)


async def _post_retrieve(
    client: httpx.AsyncClient, record: CorpusRecord
) -> httpx.Response:
    url = _field(record, "url")
    host = urlsplit(url).hostname
    if host is None:
        raise CorpusLintError(record.id, "reserved_urls")
    fetched = FetchResult(
        final_url=url,
        response_body=page_document(record).encode("utf-8"),
        content_type="text/html; charset=utf-8",
        status_code=200,
    )
    app.state.cache = CorpusContentCache()
    with (
        patch(
            "pipeline.orchestrator.validate_url",
            new=AsyncMock(return_value=(_FETCHED_ADDRESS, host)),
        ),
        patch("pipeline.orchestrator.fetch_url", new=AsyncMock(return_value=fetched)),
    ):
        return await client.post(
            "/retrieve",
            json={
                "url": url,
                "extract_mode": "full",
                "promptguard_fail_closed": True,
            },
        )


async def _post_extract(
    client: httpx.AsyncClient, record: CorpusRecord
) -> httpx.Response:
    filename = _field(record, "filename")
    text = _field(record, "text")
    return await client.post(
        "/extract",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
        data={"filename": filename, "extract_mode": "full"},
    )


async def exchange(client: httpx.AsyncClient, record: CorpusRecord) -> WireExchange:
    """Send ``record`` to its route on a ``corpus_app`` client; return the wire.

    The one place the three routes differ. Per-record state — the provider
    chain for ``/search``, a fresh cache for ``/retrieve`` — is reset here.
    """
    route = route_of(record)
    if route == "/search":
        response = await _post_search(client, record)
    elif route == "/retrieve":
        response = await _post_retrieve(client, record)
    else:
        response = await _post_extract(client, record)
    return WireExchange(response.status_code, _decode(response))


async def _drive_one(
    client: httpx.AsyncClient,
    record: CorpusRecord,
    classifier: ReplayClassifier | None,
    config: RuleConfig,
) -> RouteResult:
    route = route_of(record)
    model_id = classifier.model_id if classifier is not None else NO_CLASSIFIER_MODEL_ID
    answered = 0 if classifier is None else len(classifier.calls)
    try:
        wire = await exchange(client, record)
    except UnrecordedTextError as exc:
        if classifier is None:
            raise
        raise UnrecordedRecordError(
            record.id, route, config, classifier.model_id, exc.sha256_hex[:8]
        ) from None
    calls = () if classifier is None else tuple(classifier.calls[answered:])
    if len(calls) > 1:
        raise HarnessError(
            record.id, route, wire.status_code, "multiple_classifications"
        )
    return interpret_response(
        record_id=record.id,
        route=route,
        config=config,
        model_id=model_id,
        marker=record.marker,
        status_code=wire.status_code,
        body=wire.body,
        replayed=calls[0].scores if calls else None,
    )


async def drive_all(
    records: Iterable[CorpusRecord],
    classifier: ReplayClassifier | None,
    *,
    configs: Sequence[RuleConfig],
) -> list[RouteResult]:
    """Drive every record under every config; one lifespan per config.

    Results are grouped by config in the order given, each group in record
    order, so a run is deterministic. ``classifier=None`` is the unavailable
    path: fail-closed, every scan refused, no cassette needed.

    Driving is sequential on purpose: ``/retrieve`` and ``/extract`` each admit
    one request at a time (``fetch_concurrency`` / ``extraction_concurrency``
    are 1 in the shipped config) and release the slot on the way out, so one
    request at a time never contends. A busy answer is a harness error.
    """
    batch = tuple(records)
    results: list[RouteResult] = []
    for config in configs:
        async with corpus_app(classifier=classifier, config=config) as client:
            for record in batch:
                results.append(await _drive_one(client, record, classifier, config))
    return results


async def drive(
    record: CorpusRecord,
    classifier: ReplayClassifier | None,
    *,
    config: RuleConfig,
) -> RouteResult:
    """Drive one record through its route under one rule configuration."""
    (result,) = await drive_all([record], classifier, configs=[config])
    return result
