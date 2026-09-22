"""Poppy Retrieval sidecar -- FastAPI service for web content retrieval.

Isolated container that fetches, sanitizes, and caches web content.
No direct database access. Communicates with core via internal API only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import State
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import model_fetcher
from cache import (
    CacheMetrics,
    CacheSettings,
    CacheStorage,
    ContentCache,
    InMemoryStorage,
    ValkeyStorage,
    cache_settings_from_config,
)
from model_fetcher import ModelMetrics
from models import (
    ExtractedContent,
    RetrievedContent,
    RetrieveRequest,
    SearchRequest,
    SearchResponse,
)
from pipeline import contract
from pipeline.contract import (
    CONTRACT_VERSION,
    DEGRADED_CACHE_UNAVAILABLE,
    DEGRADED_PROMPTGUARD_UNAVAILABLE,
    POLICY_EXCLUDED_ALL_PROVIDERS,
    Admission413ErrorCode,
    DegradedReason,
    Extract422ErrorCode,
    Pipeline422ErrorCode,
    PromptGuardState,
    RateLimit429ErrorCode,
)
from pipeline.extraction_limits import (
    MAX_INPUT_BYTES,
    ExtractionSettings,
    extraction_settings_from_config,
)
from pipeline.orchestrator import (
    DOCUMENT_FAILURE_REASONS,
    AdmissionMetrics,
    PipelineError,
    UnsupportedFormatError,
    document_failure,
    run_extract_pipeline_from_file,
    run_retrieve_pipeline,
    run_search_pipeline,
)
from pipeline.pdf_subprocess import SpoolDirectoryError, spool_dir
from pipeline.retrieve_limits import (
    COMING_MAX_PROMPTGUARD_CHUNKS,
    RetrieveConfigurationError,
    RetrieveSettings,
    retrieve_settings_from_config,
)
from pipeline.sanitizer_revision import derive_sanitizer_revision
from pipeline.search_providers import (
    DEFAULT_PROVIDER_NAME,
    SEARCH_PROVIDERS_ENV_VAR,
    build_provider_chain,
    parse_provider_names,
)
from pipeline.search_providers.base import SearchProvider
from pipeline.search_providers.brave import (
    BRAVE_API_KEY_ENV_VAR,
    brave_settings_from_config,
    usable_brave_key,
)
from pipeline.search_providers.policy import apply_request_policy
from pipeline.search_providers.searxng import DEFAULT_SEARXNG_URL, SearxngProvider
from pipeline.stage5_url_audit import DEFAULT_MAX_CONTENT_BYTES
from promptguard.classifier import PromptGuardClassifier

logger = logging.getLogger(__name__)

# Runtime configuration is 12-factor: every setting arrives as an environment
# variable at container start (see ``docs/configuration.md``). There is no
# vault client and no secret-bearing config API — ``VALKEY_URL`` arrives
# ready-made, credentials and all, from the operator's env or secret store.
SEARXNG_URL = os.environ.get("SEARXNG_URL", DEFAULT_SEARXNG_URL)

# Which storage the content cache runs over, named once: the selection below
# returns it, the startup log says it, and ``HealthResponse.cache_backend``
# carries it on the wire (contract 1.1.0). Two literals, one source — a third
# name cannot appear in one place and not the others.
CacheBackend = Literal["valkey", "memory"]

# Break-glass capability override. ``FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION``
# is the current name — deliberately self-describing, so nobody arms it thinking
# it is a compatibility shim (it was ``FORAGE_LEGACY_CAPABILITY`` until the
# 2026-09-08 pre-public-flip rename; that never-deployed name is retired, not
# aliased). ``POPPY_RETRIEVAL_LEGACY_CAPABILITY`` is the pre-extraction alias,
# kept so a deployment that already carries it keeps working. Order matters only
# for which name the warning reports when both are armed.
_BREAK_GLASS_ENV_VAR = "FORAGE_BREAK_GLASS_ADVERTISE_SANITIZATION"
_DEPRECATED_BREAK_GLASS_ENV_VAR = "POPPY_RETRIEVAL_LEGACY_CAPABILITY"
_BREAK_GLASS_ENV_VARS = (
    _BREAK_GLASS_ENV_VAR,
    _DEPRECATED_BREAK_GLASS_ENV_VAR,
)


def _break_glass_arming_env_var() -> str | None:
    """Return the name of the env var arming the capability override, if any.

    Break-glass switch (see ``docs/configuration.md``) so an operator can
    reopen a consuming agent's web-search capability gate if this contract
    reaches production before that consumer does. Exact-match ``== "1"``
    semantics on both names — no truthiness, so ``true``/``yes``/``0`` do not
    arm it. Only ``capabilities['search_sanitization']`` lies under this
    flag; ``status``, ``degraded_reasons``, ``promptguard_loaded``, and
    ``capabilities['brave_api_key']`` stay honest.
    """
    for name in _BREAK_GLASS_ENV_VARS:
        if os.environ.get(name) == "1":
            return name
    return None


def _break_glass_advertisement_enabled() -> bool:
    """Return whether the deploy-transition capability override is active."""
    return _break_glass_arming_env_var() is not None


def _warn_if_break_glass_advertisement_enabled() -> bool:
    """Log a loud per-boot warning when the override is active; return its state."""
    armed_by = _break_glass_arming_env_var()
    if armed_by is not None:
        logger.warning(
            "break_glass_advertisement_active — %s=1 is forcing /health "
            "to advertise search_sanitization regardless of classifier state; "
            "unset once the consuming agent's own capability gate is deployed",
            armed_by,
        )
    return armed_by is not None


def _configured_valkey_url() -> str | None:
    """Return the operator's ``VALKEY_URL``, or ``None`` when it is fully unset.

    A callable rather than an import-time constant, on
    :func:`_break_glass_arming_env_var`'s pattern: the value is read once per
    *start* either way, but a function can be exercised per start, and the four
    starts this distinction exists for are exactly what the tests drive.

    **Only a fully unset variable means "no Valkey".** An empty value is
    configured-and-invalid, not absent: ``VALKEY_URL=${VALKEY_URL}`` rendered
    against nothing is a realistic deployment accident, and reading it as
    "unset" would answer a broken configuration by quietly running an
    unshared, non-persistent cache in production. It goes down the Valkey path
    instead, where it fails loudly as ``degraded: cache_unavailable``.
    """
    return os.environ.get("VALKEY_URL")


def _configured_provider_names() -> list[str]:
    """Return the search-provider chain names this start was configured with.

    The **one** read site for ``FORAGE_SEARCH_PROVIDERS``, on
    :func:`_configured_valkey_url`'s pattern: read once per start, in the
    lifespan, and never again while the process runs.

    A variable that is *set* but names no provider at all gets a WARNING
    before the default applies. Silence there would be the same silent
    substitution the refuse-boot rule exists to prevent — the operator asked
    for something and got the default instead, so the log says so.
    """
    raw = os.environ.get(SEARCH_PROVIDERS_ENV_VAR)
    if raw is not None and not any(token.strip() for token in raw.split(",")):
        logger.warning(
            "search_providers_blank — %s is set but names no provider; "
            "the default chain (%s) applies",
            SEARCH_PROVIDERS_ENV_VAR,
            DEFAULT_PROVIDER_NAME,
        )
    return parse_provider_names(raw)


def _resolve_brave_key() -> str | None:
    """Return the operator's Brave API key for this start, or ``None``.

    The **one** read site for ``FORAGE_BRAVE_API_KEY``, on
    :func:`_configured_provider_names`'s pattern: read once, in the
    lifespan, and never again while the process runs.

    A present-but-unusable value (see :func:`brave_key_present` for exactly
    what "unusable" means) is treated the same as an absent one, after a
    WARNING naming the variable — never the value — on the
    ``model_fetcher.py`` ``model_revision_invalid`` / ``weights_mirror_invalid``
    precedent (~898, ~1003). A value that is blank after a plain strip (the
    ``FORAGE_BRAVE_API_KEY=`` compose-renders-unset shape) is silently
    absent, exactly as a missing variable is: nothing was configured, so
    there is nothing to warn about.
    """
    raw = os.environ.get(BRAVE_API_KEY_ENV_VAR)
    if raw is None:
        return None
    key = usable_brave_key(raw)
    if key is not None:
        return key
    if raw.strip():
        logger.warning(
            "brave_key_invalid — %s is set but is not usable as an API key "
            "(must be ASCII, printable, and free of interior whitespace or "
            "control characters)",
            BRAVE_API_KEY_ENV_VAR,
        )
    return None


def _configured_cache_backend() -> CacheBackend:
    """Name the backend this start selects, without building it.

    :func:`_select_cache_storage` is the one that constructs the storage, and
    it answers the same question the same way; this exists for the one caller
    that needs the name with no storage to hand — ``/health`` before (or
    without) a lifespan. ``tests/test_app.py`` asserts the two agree for both
    environments rather than trusting that they were written to.
    """
    return "memory" if _configured_valkey_url() is None else "valkey"


def _select_cache_storage(
    *,
    settings: CacheSettings,
    metrics: CacheMetrics,
) -> tuple[CacheStorage, CacheBackend]:
    """Choose the content cache's storage for this start, with its name.

    Unset means the single-container deployment: a bounded in-memory cache,
    operational from the first request, reporting healthy. Anything else means
    the operator asked for Valkey, and asking for a Valkey that cannot be
    reached — or for one whose URL does not parse — is a configuration failure
    the service reports rather than papers over. Neither the unreachable nor
    the unparseable case falls back to memory: a silent fallback would turn a
    typo into a cache that never shares anything with the rest of the
    deployment, which is precisely the failure ``cache_unavailable`` exists to
    surface.

    The URL is handed straight to :class:`~cache.ValkeyStorage`, which parses
    it inside its own guarded connect and maps every failure to the closed log
    vocabulary. Nothing here inspects, splits or logs it — the value may carry
    a password, and a parse attempt at this layer would be a second place for
    one to escape into a log line.
    """
    url = _configured_valkey_url()
    if url is None:
        return InMemoryStorage(settings=settings, metrics=metrics), "memory"
    return ValkeyStorage(url, metrics=metrics), "valkey"


def _resolved_cache_backend(state: State) -> CacheBackend:
    """Return the backend this app selected at start, or the one it would pick.

    The lifespan publishes ``cache_backend`` once, and every request reads that
    — a process does not change backend while it runs. The fallback is
    defensive and production-unreachable (any transport that skipped lifespan
    events would 500 on the bare ``app.state.classifier`` read first, and the
    lifespan publishes this field before that one) — the same property
    ``sanitizer_revision``'s fallback has. It re-derives from the environment
    because the field is *required* on the wire: with nothing published the
    handler must still emit a literal, and re-deriving is the honest option.
    The drift test plus matrix case 4 pin it to the lifespan's rule.
    """
    backend: CacheBackend | None = getattr(state, "cache_backend", None)
    return backend if backend is not None else _configured_cache_backend()


def _resolved_sanitizer_revision(state: State) -> str:
    """Return the revision derived at start, deriving one if there is none."""
    revision: str | None = getattr(state, "sanitizer_revision", None)
    if revision is not None:
        return revision
    config: dict[str, Any] | None = getattr(state, "config", None)
    return derive_sanitizer_revision(config) if config is not None else "unknown"


def _resolved_search_providers(state: State) -> list[SearchProvider]:
    """Return the chain this app resolved at start, or the default chain.

    The lifespan publishes ``search_providers`` once — the chain **objects**,
    in chain order, never their names (any name list is derived with
    ``[p.name for p in chain]``). The fallback is defensive and
    production-unreachable, the same property :func:`_resolved_cache_backend`'s
    has: any transport that skipped lifespan events would 500 on the bare
    ``app.state.classifier`` read first.

    It is also **total** — it constructs the default provider directly rather
    than resolving a name, so it cannot raise. A configuration error can only
    ever surface at boot; the refuse-boot rule belongs to the lifespan alone.
    """
    chain: list[SearchProvider] | None = getattr(state, "search_providers", None)
    return chain if chain is not None else [SearxngProvider(DEFAULT_SEARXNG_URL)]


def _resolved_search_key_capabilities(state: State) -> tuple[str, ...]:
    """Return the key-presence capability tuple published at start, or none.

    Modelled on :func:`_resolved_cache_backend` / :func:`_resolved_sanitizer_revision`:
    the lifespan publishes this once, from its single ``brave_key_present()``
    verdict, and every request reads that. The fallback is defensive and
    production-unreachable, the same property those two have, and it never
    reads the environment — a transport that skipped lifespan events has no
    key to report on.
    """
    capabilities: tuple[str, ...] | None = getattr(
        state, "search_key_capabilities", None
    )
    return capabilities if capabilities is not None else ()


def _load_config() -> dict[str, Any]:
    """Load sidecar configuration from ``config.yaml``."""
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    logger.warning("config.yaml not found at %s", config_path)
    return {}


# -- Response models --

# The two capability keys ``/health`` can advertise, named once so the CI
# contract smoke (``contract_smoke.py``) can import the sanitization one
# instead of restating the wire string. The literals are still pinned by
# ``tests/test_app.py``, which spells them out: the constants single-source
# the *symbol*, those tests pin the *value*, and renaming a value without
# meaning to fails them. The two are computed independently (see
# ``HealthResponse.capabilities``): ``search_sanitization`` is a runtime
# claim the break-glass override can force, ``brave_api_key`` an environment
# fact no override touches.
CAPABILITY_SEARCH_SANITIZATION = "search_sanitization"
CAPABILITY_BRAVE_API_KEY = "brave_api_key"


class HealthResponse(BaseModel):
    """Response body for ``GET /health``.

    HTTP status is always 200, even when ``status == "degraded"`` (family
    decision 11) — the compose healthcheck is a bare ``curl -f`` that only
    inspects the HTTP status code, so consumers must read ``status`` and
    ``degraded_reasons`` rather than the response's non-2xx-ness.
    """

    status: Literal["healthy", "degraded"]
    promptguard_loaded: bool
    cache_connected: bool = Field(
        description=(
            "Whether the selected cache backend is operational. In Valkey mode "
            "this is a live ping, subject to reconnect backoff. In memory mode "
            "it is always true: the backend is in this process and there is no "
            "connection to lose. It is not a statement that Valkey is present "
            "— read cache_backend for that."
        )
    )
    capabilities: dict[str, int] = Field(
        description=(
            "Capabilities this deployment advertises, as a presence map: a "
            "key is present with the value 1 when the capability is "
            "available and absent otherwise. Two keys are defined in "
            "contract 1.2.0. 'search_sanitization' (contract 1.1.0) is a "
            "runtime claim, present when PromptGuard is loaded — or when "
            "the break-glass override is armed; see docs/configuration.md, "
            "which only ever forces this key. 'brave_api_key' (contract "
            "1.2.0) is an environment fact, present when this start "
            "resolved a usable FORAGE_BRAVE_API_KEY, independently of "
            "whether 'brave' actually appears in search_providers and "
            "untouched by the break-glass override. Deliberately a dict "
            "rather than an enum: a consumer reads the keys it knows and "
            "ignores the rest, so a future capability is an additive-safe "
            "MINOR change."
        )
    )
    sanitizer_revision: str
    contract_version: str
    cache_backend: CacheBackend = Field(
        description=(
            "Which storage the content cache selected at start: 'valkey' when "
            "VALKEY_URL was set, 'memory' when it was fully unset. Added in "
            "contract 1.1.0."
        )
    )
    search_providers: list[str] = Field(
        description=(
            "The resolved search-provider chain's names, in traversal "
            "order — the FORAGE_SEARCH_PROVIDERS entries this start "
            "resolved after key-gated skips (a 'brave' entry with no "
            "usable key is absent here, not just unusable). Configuration "
            "echo fixed for the life of the process, not a liveness probe: "
            "it says what this start resolved, never whether a provider is "
            "reachable right now. Added in contract 1.2.0."
        )
    )
    degraded_reasons: list[DegradedReason] = Field(
        default=[],
        description=(
            "Why status is 'degraded', as closed-vocabulary reason codes. "
            "Empty exactly when status is 'healthy'. The members are derived "
            "from contract.DegradedReason, which is also what this field is "
            "validated against on the way out — so a reason added to the "
            "handler without being added to the alias fails response "
            "validation loudly instead of reaching a consumer unannounced."
        ),
    )


class ExtractionMetricsResponse(BaseModel):
    """The ``extraction`` section of ``GET /metrics``.

    The three cgroup/OOM fields are **flat members of this section, not a
    nested object**: the handler splats ``_cgroup_memory_snapshot()`` in, and
    has since the fields existed. The model mirrors that flattening rather
    than tidying it — nesting them would be a wire change, and
    ``tests/test_app.py``'s ``oom_proximity_ratio in extraction`` assertion is
    the fossil guard that says so.
    """

    model_config = ConfigDict(extra="forbid")

    requests: int = Field(
        description="Extraction attempts that reached the handler, successes included."
    )
    busy_rejections: int = Field(
        description=(
            "Requests refused with 429 because the bounded admission queue was "
            "full or its byte reservation would have been exceeded."
        )
    )
    semaphore_saturation: int = Field(
        description=(
            "Requests that found every extraction slot busy. Counts queueing as "
            "well as rejection, so it is always >= busy_rejections."
        )
    )
    active: int = Field(description="Extraction slots in use right now.")
    queued: int = Field(description="Requests waiting for a slot right now.")
    queued_bytes: int = Field(
        description=(
            "Upload bytes conservatively reserved for the queued requests — the "
            "per-request maximum, not a measured size."
        )
    )
    verdicts: dict[str, int] = Field(
        description=(
            "Content-free outcomes by key: 'success', 'injection_detected', or "
            "the error code of a document failure. Additive-safe, like "
            "HealthResponse.capabilities — read the keys you know."
        )
    )
    cgroup_memory_current_bytes: int | None = Field(
        description=(
            "cgroup v2 memory.current for this container, or null where no "
            "cgroup v2 memory controller is readable (a plain host, macOS)."
        )
    )
    cgroup_memory_max_bytes: int | None = Field(
        description=(
            "cgroup v2 memory.max, or null when unreadable or literally 'max' "
            "(no limit set)."
        )
    )
    oom_proximity_ratio: float | None = Field(
        description=(
            "current/max as a fraction, or null when either side is null or "
            "max is zero. The concrete OOM-proximity signal an operator reads."
        )
    )


class SearchMetricsResponse(BaseModel):
    """The ``search`` section of ``GET /metrics``."""

    model_config = ConfigDict(extra="forbid")

    requests: int = Field(description="Search requests that reached the handler.")
    errors: dict[str, int] = Field(
        description=(
            "Refusals keyed by the /search error code (searxng_error, "
            "searxng_unavailable, search_unavailable)."
        )
    )
    omitted_by_reason: dict[str, int] = Field(
        description=(
            "Results withheld, keyed by the closed omission vocabulary; anything "
            "outside it is folded into contract.METRICS_OTHER_BUCKET rather than "
            "growing this map without bound."
        )
    )
    unscanned_results: int = Field(
        description="Results returned without an ML injection scan (degraded mode)."
    )
    fallback_fired: int = Field(
        description=(
            "Per-process count of the per-response `fallback_fired` bool — how "
            "many `/search` requests had their provider chain advance past the "
            "first provider, including ones that ended in a 422. Moves only "
            "when the configured chain has more than one provider; a "
            "`searxng`-only deployment's first-party signal is the "
            "`search_provider_failed` WARNING instead."
        )
    )
    paid_calls: int = Field(
        description=(
            "Count of calls made to a `paid=True` provider, incremented before "
            "the call so a call that times out is still counted — whether or "
            "not it went on to serve the response. Zero on a chain with no "
            "paid provider configured."
        )
    )
    policy_unknown_provider: int = Field(
        description=(
            "Per-request `providers` entries ignored — beyond the first eight, "
            "or matching no configured provider — never the offending name "
            "itself. Compare against `/health` `search_providers` to tell a "
            "bad name from a missing key."
        )
    )
    classification_wait_timeouts: int = Field(
        description=(
            "`/search` requests whose per-request PromptGuard wait budget "
            "expired — at most one per request, however many results were "
            "left unscanned afterwards. The classifier was loaded and busy, "
            "not absent: compare against `/health` `promptguard_loaded` and "
            "the `retrieve` counter of the same name."
        )
    )


class RetrieveMetricsResponse(BaseModel):
    """The ``retrieve`` section of ``GET /metrics``."""

    model_config = ConfigDict(extra="forbid")

    requests: int = Field(description="Retrieve requests that reached the handler.")
    errors: dict[str, int] = Field(
        description="Refusals keyed by the /retrieve error code."
    )
    cache_hits: int = Field(
        description=(
            "Requests served from the content cache. A *request* outcome — see "
            "the cache section for the storage-operation counters underneath."
        )
    )
    cache_misses: int = Field(description="Requests that had to fetch.")
    blocked_by_reason: dict[str, int] = Field(
        description=(
            "Injection blocks keyed by the leading diagnostic, bucketed into "
            "contract.METRICS_OTHER_BUCKET outside the closed vocabulary."
        )
    )
    promptguard_state: dict[str, int] = Field(
        description=(
            "Retrievals by classifier state (scanned / skipped / unavailable), "
            "bucketed the same way."
        )
    )
    classification_wait_timeouts: int = Field(
        description=(
            "`/retrieve` requests that waited `promptguard_wait_seconds` for "
            "the classification permit and gave up, taking the "
            "classifier-unavailable outcome under the request's own "
            "`promptguard_fail_closed`. Rising with `promptguard_loaded: true` "
            "on `/health` means permit contention, not a missing model."
        )
    )
    semaphore_saturation: int = Field(
        description=(
            "`/retrieve` requests that found every fetch slot of the admission "
            "gate busy (`retrieve.fetch_concurrency`) — not the classification "
            "permit, which is `classification_wait_timeouts`. Counts queueing "
            "as well as refusal, so it is always >= busy_rejections."
        )
    )
    busy_rejections: int = Field(
        description=(
            "`/retrieve` requests refused 422 `busy` (reason "
            "`admission_queue_full`) because the admission queue was at "
            "`retrieve.admission_queue_depth` or its byte reservation would "
            "have exceeded `retrieve.max_queued_fetch_bytes`."
        )
    )


class CacheMetricsResponse(BaseModel):
    """The ``cache`` section of ``GET /metrics``.

    Two layers share this response and are not duplicates of each other.
    ``retrieve.cache_hits``/``cache_misses`` count *request* outcomes; the
    ``storage_*`` counters here count *storage operations* underneath the
    cache's policy layer, so a zero-TTL purge or a policy-stale entry moves
    one and not the other. Only the in-memory storage can move
    ``storage_evictions``/``storage_oversize_skips`` — Valkey does its own
    eviction and has no byte bound of ours.
    """

    model_config = ConfigDict(extra="forbid")

    reconnect_attempts: int = Field(description="Reconnects attempted to the backend.")
    reconnect_successes: int = Field(
        description="Reconnects that restored the backend."
    )
    reconnect_failures: int = Field(description="Reconnects that failed.")
    operation_failures: int = Field(
        description="Cache operations that failed against the backend."
    )
    storage_hits: int = Field(description="Key lookups that found live bytes.")
    storage_misses: int = Field(
        description="Key lookups that found nothing, an aged-out entry included."
    )
    storage_evictions: int = Field(
        description=(
            "Entries the in-memory storage dropped to stay inside its bounds. "
            "Always 0 on Valkey."
        )
    )
    storage_oversize_skips: int = Field(
        description=(
            "Entries the in-memory storage refused as over its per-entry byte "
            "bound. Always 0 on Valkey."
        )
    )


class ModelMetricsResponse(BaseModel):
    """The ``model`` section of ``GET /metrics`` — weight acquisition.

    ``fetch_in_progress`` is what distinguishes "downloading ~270 MiB" from
    "wedged" while ``/health`` reports ``degraded`` for both, and
    ``retries_scheduled`` separates both of those from "waiting out a backoff".
    On a container whose logs drop INFO (``kit_tools/docs/GOTCHAS.md``) this
    section is the only place a fetch in flight is visible.
    """

    model_config = ConfigDict(extra="forbid")

    fetch_failures: int = Field(description="Acquisition attempts that failed.")
    verify_failures: int = Field(
        description="Weight sets refused by manifest verification."
    )
    quarantines: int = Field(
        description="Weight sets moved out of the loader's scan tree."
    )
    fetch_in_progress: bool = Field(
        description="Whether an acquisition is running right now."
    )
    retries_scheduled: int = Field(
        description=(
            "Retries armed after a failed acquisition. Non-zero with "
            "fetch_in_progress false is the 'waiting to try again' state."
        )
    )


# ``extra="forbid"`` on all six metrics models is a choice about failure mode,
# not tidiness, and the class docstrings stay consumer-facing because they are
# what the generated contract publishes. The rule, for whoever adds a counter:
# FastAPI validates a handler's return against its response model, and a
# permissive model would *silently filter* a counter the model does not carry —
# added, reviewed, deployed, and never on the wire. Forbidding extras makes that
# a loud 500 instead. So a new counter goes in the handler and in its section
# model, in the same commit and in the same position (the parity test compares
# serialized bytes, so order is contract). ``tests/test_contract_metrics.py``
# drives both halves, including the permissive counterfactual.


class MetricsResponse(BaseModel):
    """Response body for ``GET /metrics``.

    Unauthenticated, like every Forage endpoint (``docs/configuration.md``,
    "Deployment posture"). The counters are content-free by construction — no
    URL, query, filename or document text reaches any of them — but they do
    describe traffic volume and failure rates, so they are part of what network
    placement protects.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(
        description=(
            "The wire contract this process implements — the same value "
            "/health and the OpenAPI document's info.version carry."
        )
    )
    extraction: ExtractionMetricsResponse
    search: SearchMetricsResponse
    retrieve: RetrieveMetricsResponse
    cache: CacheMetricsResponse
    model: ModelMetricsResponse


# The error bodies below are **mirrors**, not emitters. Every one of them
# documents a shape some site in this module already puts on the wire, and not
# one emission site was changed to route through them: ``responses=``
# declarations make them visible in ``app.openapi()``, and
# ``tests/test_contract_errors.py`` drives each site through the real routes
# and asserts the model reproduces the emitted body byte-for-byte. That pairing
# is the whole design — documentation follows the wire, and the parity test is
# what stops it drifting. ``extra="forbid"`` is part of the leash: a field
# appearing on the wire that the mirror does not carry fails the parity test at
# validation rather than passing unnoticed.


class Extract422ErrorResponse(BaseModel):
    """``POST /extract`` document failure — the 422 ``PipelineError`` shape.

    Emitted by :func:`pipeline_error_handler`, which appends
    ``sanitizer_revision`` on ``/extract`` paths only. That fourth field is a
    hard requirement of Poppy's client, which rejects a 422 without it as a
    protocol failure (``poppy/core/retrieval/client.py``), so it is documented
    here as required rather than left to be discovered from a rejection.
    """

    model_config = ConfigDict(extra="forbid")

    error: Extract422ErrorCode = Field(
        description="Stable machine-readable failure code."
    )
    reason: str = Field(description="Fixed, content-free user message for this code.")
    request_id: str = Field(
        description="Sidecar-generated id for correlating logs with this failure."
    )
    sanitizer_revision: str = Field(
        description=(
            "The pipeline revision that refused this document. Present on "
            "every /extract 422 and on no other error body."
        )
    )


class Pipeline422ErrorResponse(BaseModel):
    """``POST /retrieve`` and ``POST /search`` refusal — the 422 shape.

    The same handler as :class:`Extract422ErrorResponse`, minus the
    ``sanitizer_revision`` it adds only on ``/extract`` paths.
    """

    model_config = ConfigDict(extra="forbid")

    error: Pipeline422ErrorCode = Field(
        description=(
            "Stable machine-readable refusal code. The fetch and URL-validation "
            "codes arrive on /retrieve, the searxng_* codes and "
            "search_unavailable on /search. busy arrives on /retrieve only, "
            "at 422, as the admission refusal (reason admission_queue_full); "
            "the same literal is /extract's 429. extraction_failed arrives on "
            "/retrieve only, as a fetched PDF the worker could not parse or "
            "spool (reason pdf_encrypted, pdf_no_text, pdf_extraction_error "
            "or pdf_spool_error)."
        )
    )
    reason: str = Field(
        description=(
            "Human-readable detail. Unlike the /extract reasons this is not "
            "content-free: a /retrieve refusal echoes the requested URL, and "
            "private_ip echoes the resolved address. Deployments exposing "
            "Forage beyond a private network should treat it accordingly."
        )
    )
    request_id: str = Field(
        description="Sidecar-generated id for correlating logs with this refusal."
    )


class Admission413Response(BaseModel):
    """``POST /extract`` streaming size refusal — the 413 shape.

    :class:`DocumentSizeLimitMiddleware` rejects the request while its ASGI
    body is still streaming, before any handler or request id exists. That is
    why this body carries **no** ``request_id`` where every other coded error
    does — the difference is real, not an oversight to be tidied away.
    """

    model_config = ConfigDict(extra="forbid")

    error: Admission413ErrorCode = Field(
        description="Always 'content_too_large' at this status."
    )
    reason: str = Field(
        description="Fixed, content-free user message for the size limit."
    )


class RateLimit429Response(BaseModel):
    """``POST /extract`` admission refusal — the 429 shape.

    Emitted by :class:`ExtractionAdmissionMiddleware` when the bounded queue is
    full. Four fields: the middleware mints a request id and reads the process
    revision itself, so this body matches the /extract 422 shape even though it
    never passes through the error handler.
    """

    model_config = ConfigDict(extra="forbid")

    error: RateLimit429ErrorCode = Field(description="Always 'busy' at this status.")
    reason: str = Field(
        description="Fixed, content-free user message for the busy refusal."
    )
    request_id: str = Field(
        description="Middleware-generated id for correlating logs with this refusal."
    )
    sanitizer_revision: str = Field(
        description=(
            "The pipeline revision of the process that refused admission. "
            "Empty string if the request arrived before the lifespan "
            "published one."
        )
    )


class DetailResponse(BaseModel):
    """The bare ``{\"detail\": ...}`` body, as FastAPI's ``HTTPException`` emits it.

    Used by ``/extract`` for the release-gate 404 and the two admission 503s.
    These three carry no ``error`` code at all — they are pre-pipeline
    conditions rather than document failures, and homogenizing them into the
    coded envelope would be a wire change this spec forbids.
    """

    model_config = ConfigDict(extra="forbid")

    detail: str = Field(
        description="Short reason phrase: 'Not Found' (404) or 'Unavailable' (503)."
    )


class ValidationErrorDetail(BaseModel):
    """One entry of FastAPI's default request-validation error list.

    Deliberately **not** ``extra=\"forbid\"``: pydantic adds ``input`` and
    sometimes ``ctx``/``url`` per error type, and this model documents the
    stable trio rather than pretending to close the set.
    """

    loc: list[str | int] = Field(
        description="Path to the offending field within the request."
    )
    msg: str = Field(description="Human-readable validation message.")
    type: str = Field(description="Pydantic error type, e.g. 'missing'.")


class HTTPValidationError(BaseModel):
    """FastAPI's default 422 body for a malformed request.

    Mirrored here because declaring a 422 response stops FastAPI auto-adding
    its own — verified against the locked FastAPI in
    ``tests/test_contract_errors.py`` — and the service genuinely still returns
    this body for a request that fails schema validation before any pipeline
    code runs. Every declared 422 is therefore a union of the route's pipeline
    shape and this one.
    """

    detail: list[ValidationErrorDetail] = Field(
        default=[], description="One entry per failed field."
    )


_MAX_FILENAME_LENGTH = 255
_MAX_MIME_HINT_LENGTH = 255
_MAX_REQUEST_ID_LENGTH = 128
_MAX_DOCUMENT_BYTES = MAX_INPUT_BYTES
_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class _RequestBodyTooLargeError(Exception):
    """Raised internally when an ASGI request body crosses its byte limit."""


@dataclass(frozen=True, slots=True)
class _SpoolResult:
    """A sidecar-owned file and its accepted upload-byte count."""

    path: Path
    size: int


class ExtractionMetrics:
    """In-process counters exported by the internal ``/metrics`` endpoint."""

    def __init__(self) -> None:
        self.requests = 0
        self.busy_rejections = 0
        self.semaphore_saturation = 0
        self.verdicts: dict[str, int] = {}

    def record_verdict(self, verdict: str) -> None:
        """Record one content-free extraction outcome."""
        self.requests += 1
        self.verdicts[verdict] = self.verdicts.get(verdict, 0) + 1


_PROMPTGUARD_STATES = frozenset(get_args(PromptGuardState))


class SearchMetrics:
    """In-process counters exported by the internal ``/metrics`` endpoint."""

    def __init__(self) -> None:
        self.requests = 0
        self.errors: dict[str, int] = {}
        self.omitted_by_reason: dict[str, int] = {}
        self.unscanned_results = 0
        self.fallback_fired = 0
        self.paid_calls = 0
        self.policy_unknown_provider = 0
        # `pipeline.orchestrator.SearchMetricsSink`'s third counter, moved at
        # most once per request by the per-request classification wait budget.
        self.classification_wait_timeouts = 0

    def record_error(self, error: str) -> None:
        """Record one content-free search error, keyed by ``PipelineError.error``."""
        self.errors[error] = self.errors.get(error, 0) + 1

    def record_response(self, response: SearchResponse) -> None:
        """Fold one content-free search response's omission and scan counts in."""
        for reason, count in response.omitted_by_reason.items():
            key = (
                reason
                if reason in contract.OMISSION_REASONS
                else contract.METRICS_OTHER_BUCKET
            )
            self.omitted_by_reason[key] = self.omitted_by_reason.get(key, 0) + count
        self.unscanned_results += response.unscanned_results


class RetrieveMetrics:
    """In-process counters exported by the internal ``/metrics`` endpoint."""

    def __init__(self) -> None:
        self.requests = 0
        self.errors: dict[str, int] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self.blocked_by_reason: dict[str, int] = {}
        self.promptguard_state: dict[str, int] = {}
        # The three counters `pipeline.orchestrator.RetrieveMetricsSink`
        # declares. The first two are the `/retrieve` admission controller's
        # (`app.state.retrieve_admission` increments them by attribute, the
        # way `/extract`'s controller increments `ExtractionMetrics`); the
        # third is the classification-permit wait timeout.
        self.semaphore_saturation = 0
        self.busy_rejections = 0
        self.classification_wait_timeouts = 0

    def record_error(self, error: str) -> None:
        """Record one content-free retrieve error, keyed by ``PipelineError.error``."""
        self.errors[error] = self.errors.get(error, 0) + 1

    def record_content(self, content: RetrievedContent) -> None:
        """Fold one content-free retrieved-content's cache/block/state counts in."""
        if content.cache_hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1
        if content.injection_detected and content.injection_spans:
            diagnostic = content.injection_spans[0]
            key = (
                diagnostic
                if diagnostic in contract.DIAGNOSTICS
                else contract.METRICS_OTHER_BUCKET
            )
            self.blocked_by_reason[key] = self.blocked_by_reason.get(key, 0) + 1
        state = content.promptguard_state
        state_key = (
            state if state in _PROMPTGUARD_STATES else contract.METRICS_OTHER_BUCKET
        )
        self.promptguard_state[state_key] = self.promptguard_state.get(state_key, 0) + 1


class ExtractionAdmissionController:
    """Bound active extraction work and pre-multipart waiting requests."""

    def __init__(
        self,
        settings: ExtractionSettings,
        metrics: AdmissionMetrics,
    ) -> None:
        self._limit = settings.extraction_concurrency
        self._queue_depth = settings.admission_queue_depth
        self._max_queued_bytes = settings.max_queued_upload_bytes
        self._reservation_bytes = settings.max_input_bytes
        self._metrics = metrics
        self._active = 0
        self._queued_bytes = 0
        self._waiters: list[asyncio.Future[None]] = []
        self._lock = asyncio.Lock()

    @classmethod
    def from_retrieve_settings(
        cls,
        settings: RetrieveSettings,
        metrics: AdmissionMetrics,
    ) -> ExtractionAdmissionController:
        """Build ``/retrieve``'s admission controller from ``retrieve:`` limits.

        The controller reads exactly four fields off an ``ExtractionSettings``,
        so this builds an ``ExtractionSettings``-shaped view carrying
        ``/retrieve``'s values in them: ``fetch_concurrency`` as the slot
        count, ``admission_queue_depth`` as the queue depth,
        ``max_queued_fetch_bytes`` as the queued-byte bound and the 10 MB fetch
        cap (``DEFAULT_MAX_CONTENT_BYTES``) as each queued request's
        reservation.

        The view is a **field carrier, not a validated ``extraction:``
        configuration**. ``ExtractionSettings`` has no ``__post_init__`` — its
        bounds live in ``extraction_settings_from_config``'s reader, which
        ``dataclasses.replace`` bypasses — so the view may legitimately hold an
        ``admission_queue_depth`` up to 16 and a queued-byte bound up to
        160 MB, above the ``extraction:`` maxima. ``retrieve_settings_from_config``
        is the gate that already bounded those values; nothing here re-checks
        them against the ``extraction:`` ranges, and nothing else reads the
        view.
        """
        view = replace(
            extraction_settings_from_config({}),
            extraction_concurrency=settings.fetch_concurrency,
            admission_queue_depth=settings.admission_queue_depth,
            max_queued_upload_bytes=settings.max_queued_fetch_bytes,
            max_input_bytes=DEFAULT_MAX_CONTENT_BYTES,
        )
        return cls(view, metrics)

    @property
    def active(self) -> int:
        """Return active extraction slots."""
        return self._active

    @property
    def queued(self) -> int:
        """Return waiting extraction requests."""
        return len(self._waiters)

    @property
    def queued_bytes(self) -> int:
        """Return conservatively reserved queued upload bytes."""
        return self._queued_bytes

    async def acquire(self) -> bool:
        """Reserve an active slot or bounded queue slot before multipart parsing."""
        async with self._lock:
            if self._active < self._limit:
                self._active += 1
                return True
            self._metrics.semaphore_saturation += 1
            if (
                len(self._waiters) >= self._queue_depth
                or self._queued_bytes + self._reservation_bytes > self._max_queued_bytes
            ):
                self._metrics.busy_rejections += 1
                return False
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            self._queued_bytes += self._reservation_bytes
        try:
            await waiter
            return True
        except BaseException:
            async with self._lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                    self._queued_bytes -= self._reservation_bytes
            raise

    async def release(self) -> None:
        """Release an active slot and promote exactly one bounded waiter."""
        async with self._lock:
            if self._waiters:
                waiter = self._waiters.pop(0)
                self._queued_bytes -= self._reservation_bytes
                if not waiter.done():
                    waiter.set_result(None)
                return
            self._active -= 1


def _cgroup_memory_snapshot() -> dict[str, int | float | None]:
    """Read cgroup v2 memory usage for a concrete OOM-proximity signal."""
    memory_current = Path("/sys/fs/cgroup/memory.current")
    memory_max = Path("/sys/fs/cgroup/memory.max")
    try:
        current = int(memory_current.read_text().strip())
        max_value = memory_max.read_text().strip()
        maximum = None if max_value == "max" else int(max_value)
    except (OSError, ValueError):
        return {
            "cgroup_memory_current_bytes": None,
            "cgroup_memory_max_bytes": None,
            "oom_proximity_ratio": None,
        }
    ratio: float | None = None if maximum is None or maximum == 0 else current / maximum
    return {
        "cgroup_memory_current_bytes": current,
        "cgroup_memory_max_bytes": maximum,
        "oom_proximity_ratio": ratio,
    }


class DocumentSizeLimitMiddleware:
    """Reject oversized extract requests while their ASGI body is still streaming."""

    def __init__(self, app: ASGIApp, *, max_bytes: int | None = None) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Count received bytes without trusting Content-Length."""
        if scope["type"] != "http" or scope["path"] != "/extract":
            await self._app(scope, receive, send)
            return

        app = scope.get("app")
        settings = getattr(getattr(app, "state", None), "extraction_settings", None)
        max_bytes = (
            self._max_bytes
            if self._max_bytes is not None
            else (
                settings.max_input_bytes
                if isinstance(settings, ExtractionSettings)
                else _MAX_DOCUMENT_BYTES
            )
        )
        received_bytes = 0

        async def receive_limited() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > max_bytes:
                    raise _RequestBodyTooLargeError
            return message

        try:
            await self._app(scope, receive_limited, send)
        except _RequestBodyTooLargeError:
            response = JSONResponse(
                status_code=413,
                content={
                    "error": "content_too_large",
                    "reason": DOCUMENT_FAILURE_REASONS["content_too_large"],
                },
            )
            await response(scope, receive, send)


class ExtractionAdmissionMiddleware:
    """Reject disabled or over-capacity requests before FastAPI parses multipart."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Apply the release gate and bounded admission queue to ``/extract``."""
        if scope["type"] != "http" or scope["path"] != "/extract":
            await self._app(scope, receive, send)
            return
        app = scope.get("app")
        settings = getattr(getattr(app, "state", None), "extraction_settings", None)
        if not isinstance(settings, ExtractionSettings) or not settings.route_enabled:
            await JSONResponse(status_code=404, content={"detail": "Not Found"})(
                scope, receive, send
            )
            return
        if app is None:
            await JSONResponse(status_code=503, content={"detail": "Unavailable"})(
                scope, receive, send
            )
            return
        controller = getattr(app.state, "extraction_admission", None)
        if not isinstance(controller, ExtractionAdmissionController):
            await JSONResponse(status_code=503, content={"detail": "Unavailable"})(
                scope, receive, send
            )
            return
        if not await controller.acquire():
            revision = getattr(app.state, "sanitizer_revision", "")
            await JSONResponse(
                status_code=429,
                content={
                    "error": "busy",
                    "reason": DOCUMENT_FAILURE_REASONS["busy"],
                    "request_id": uuid.uuid4().hex,
                    "sanitizer_revision": revision,
                },
            )(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        finally:
            await controller.release()


async def _spool_upload(
    file: UploadFile,
    *,
    max_bytes: int,
    chunk_size: int = _UPLOAD_READ_CHUNK_SIZE,
) -> _SpoolResult:
    """Spool a bounded upload to a 0600 sidecar-owned file for the parser child.

    Into :func:`spool_dir`, the process-private 0700 directory ``/retrieve``'s
    fetched PDFs share; created on first use, so a lifespan-free caller works.
    """
    path: Path | None = None
    received_bytes = 0
    try:
        with tempfile.NamedTemporaryFile(
            prefix="poppy-extract-",
            suffix=".upload",
            dir=spool_dir(),
            delete=False,
        ) as temporary:
            path = Path(temporary.name)
            while chunk := await file.read(chunk_size):
                received_bytes += len(chunk)
                if received_bytes > max_bytes:
                    raise document_failure("content_too_large", uuid.uuid4().hex)
                temporary.write(chunk)
        return _SpoolResult(path=path, size=received_bytes)
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def _sanitize_upload_metadata(
    *,
    filename: str,
    mime_hint: str | None,
    request_id: str | None,
) -> tuple[str, str | None, str]:
    """Bound and sanitize untrusted upload metadata before logging or response use."""
    safe_request_id = uuid.uuid4().hex
    if len(filename) > _MAX_FILENAME_LENGTH:
        raise PipelineError(
            "invalid_filename",
            f"filename exceeds {_MAX_FILENAME_LENGTH} characters",
            safe_request_id,
        )
    cleaned_filename = _CONTROL_CHARS_RE.sub("", filename).replace("\\", "/")
    cleaned_filename = cleaned_filename.rsplit("/", maxsplit=1)[-1].strip()
    if cleaned_filename in {"", ".", ".."}:
        raise PipelineError(
            "invalid_filename",
            "filename must contain a basename",
            safe_request_id,
        )

    cleaned_mime_hint: str | None = None
    if mime_hint is not None:
        if len(mime_hint) > _MAX_MIME_HINT_LENGTH:
            raise PipelineError(
                "invalid_mime_hint",
                f"mime_hint exceeds {_MAX_MIME_HINT_LENGTH} characters",
                safe_request_id,
            )
        cleaned_mime_hint = _CONTROL_CHARS_RE.sub("", mime_hint).strip() or None

    if request_id is None:
        return cleaned_filename, cleaned_mime_hint, safe_request_id
    if len(request_id) > _MAX_REQUEST_ID_LENGTH:
        raise PipelineError(
            "invalid_request_id",
            f"request_id exceeds {_MAX_REQUEST_ID_LENGTH} characters",
            safe_request_id,
        )
    cleaned_request_id = _CONTROL_CHARS_RE.sub("", request_id)
    if not _REQUEST_ID_RE.fullmatch(cleaned_request_id):
        raise PipelineError(
            "invalid_request_id",
            "request_id contains disallowed characters",
            safe_request_id,
        )
    return cleaned_filename, cleaned_mime_hint, cleaned_request_id


# -- Lifespan --


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown lifecycle."""
    # Load config
    config = _load_config()
    app.state.config = config
    settings = extraction_settings_from_config(config)
    app.state.extraction_settings = settings
    retrieve_settings = retrieve_settings_from_config(config)
    app.state.retrieve_settings = retrieve_settings
    # The spool directory is checked once here so a planted symlink, a foreign
    # owner or a group/other bit refuses the boot, under the same closed
    # vocabulary as every other `retrieve:` refusal — the token, never the
    # path. Each spool re-runs the same check, because a directory verified now
    # can be removed and re-created by another local user later.
    try:
        spool_dir()
    except SpoolDirectoryError as exc:
        raise RetrieveConfigurationError(str(exc)) from exc
    if retrieve_settings.max_promptguard_chunks == 0:
        # Exactly one WARNING, closed token plus the integer — no URL, no
        # config dump. `0` is the shipped default for one minor release
        # (`contract/GOVERNANCE.md` ruling (g)); this names the value the next
        # MINOR flips to, so an operator reading boot logs finds the window
        # rather than discovering it in a Release body.
        logger.warning(
            "retrieve_budget_unset coming_default=%d", COMING_MAX_PROMPTGUARD_CHUNKS
        )
    app.state.extraction_metrics = ExtractionMetrics()
    app.state.extraction_admission = ExtractionAdmissionController(
        settings,
        app.state.extraction_metrics,
    )
    app.state.search_metrics = SearchMetrics()
    app.state.retrieve_metrics = RetrieveMetrics()
    app.state.retrieve_admission = ExtractionAdmissionController.from_retrieve_settings(
        retrieve_settings,
        app.state.retrieve_metrics,
    )
    app.state.model_metrics = ModelMetrics()
    app.state.classification_semaphore = asyncio.Semaphore(
        settings.classification_concurrency
    )
    app.state.sanitizer_revision = derive_sanitizer_revision(config)
    logger.info(
        "Sidecar config loaded (%d keys); contract_version=%s",
        len(config),
        CONTRACT_VERSION,
    )
    _warn_if_break_glass_advertisement_enabled()

    # Resolved unconditionally, on the same posture as
    # `cache_settings_from_config` below: whether or not "brave" is in the
    # resolved chain, a wrong-typed or out-of-range value refuses boot
    # rather than shipping dead (`feature-brave-provider` US-002). Read
    # ahead of the chain build below because `build_provider_chain` needs
    # the resolved settings to hand a registered `BraveApiProvider`.
    app.state.brave_settings = brave_settings_from_config(config)

    # Resolve the ordered search-provider chain from the environment, once.
    # An unknown name raises `SearchProviderConfigurationError` straight out
    # of the lifespan — the `extraction_settings_from_config` /
    # `cache_settings_from_config` precedent: a typo fails the boot loudly
    # rather than quietly running a chain the operator did not ask for. A
    # `"brave"` entry with no usable key is skipped (WARNING), never a boot
    # refusal — the key-less deployment is the supported floor.
    # Evaluated exactly once — this single verdict feeds both the chain
    # build below and `search_key_capabilities`, so the two can never
    # disagree about whether the key is usable (US-002's "one evaluation"
    # rule). `brave_key is not None` is that verdict: `_resolve_brave_key`
    # already returns `None` for absent, blank, or `brave_key_invalid`
    # values.
    brave_key = _resolve_brave_key()
    search_providers = build_provider_chain(
        _configured_provider_names(),
        searxng_url=SEARXNG_URL,
        brave_api_key=brave_key,
        brave_settings=app.state.brave_settings,
    )
    app.state.search_providers = search_providers
    app.state.search_key_capabilities = (
        (CAPABILITY_BRAVE_API_KEY,) if brave_key is not None else ()
    )
    # Names only — never the configured endpoint or any other environment
    # value.
    logger.info(
        "Search providers resolved: %s",
        ", ".join(provider.name for provider in search_providers),
    )

    # Connect content cache. The `cache:` bounds are validated here whichever
    # storage ends up selected — a typo fails the boot loudly, exactly as the
    # `extraction:` block does, rather than silently widening a memory bound.
    #
    # The storage is chosen here, from `VALKEY_URL`, and injected: the service
    # never relies on `ContentCache`'s own Valkey default, which survives only
    # as the test-facing constructor convenience it always was.
    app.state.cache_settings = cache_settings_from_config(config)
    app.state.cache_metrics = CacheMetrics()
    storage, backend = _select_cache_storage(
        settings=app.state.cache_settings,
        metrics=app.state.cache_metrics,
    )
    cache = ContentCache(storage=storage, metrics=app.state.cache_metrics)
    cache_ok = await cache.connect()
    app.state.cache = cache
    # Published for `/health` on the `sanitizer_revision` precedent above:
    # decided once per start, read per request, never recomputed from the
    # environment while the process runs.
    app.state.cache_backend = backend
    if cache_ok:
        # `backend` is one of two literals, never the URL.
        logger.info("Content cache connected (%s)", backend)
    else:
        logger.warning("Content cache not available at startup")

    # Acquire and load the PromptGuard 2 weights (fetch → verify → load).
    #
    # A task around a thread, never an `await` — and the difference is the
    # whole point. `snapshot_download` + `from_pretrained` is minutes of
    # blocking network and torch work for a ~270 MiB weight set; uvicorn
    # serves nothing until lifespan startup returns, and the compose
    # healthcheck (10 s x 5 retries, no `start_period`) would restart-loop the
    # container before the first byte landed. So startup yields immediately,
    # `/health` answers honestly `degraded` with `promptguard_unavailable`
    # throughout, and `promptguard_loaded` flips to true in place when the
    # load finishes — no restart, no second request path.
    #
    # The handle lives on `app.state` so shutdown can cancel it.
    #
    # US-005 made the task a *loop*: `WeightAcquisition.run()` retries on a
    # bounded, jittered backoff until the classifier loads, so a sidecar that
    # started during a Hugging Face outage — or before its gated-repo approval
    # came through — converges without anyone restarting it. It holds the
    # single-flight lock, which is why the object is on `app.state` too: any
    # future caller that wants an acquisition has to go through the same lock
    # rather than starting a second ~270 MiB download alongside this one.
    classifier = PromptGuardClassifier()
    app.state.classifier = classifier
    acquisition = model_fetcher.WeightAcquisition(
        classifier,
        metrics=app.state.model_metrics,
    )
    app.state.model_acquisition = acquisition
    app.state.model_task = asyncio.create_task(acquisition.run())

    yield

    # Shutdown. The acquisition loop has no ending of its own short of a loaded
    # classifier, so cancelling it is not tidiness — it is the only thing that
    # stops it. A task nobody cancels outlives the lifespan, and under pytest
    # that is a hang rather than a warning.
    model_task: asyncio.Task[bool] | None = getattr(app.state, "model_task", None)
    if model_task is not None and not model_task.done():
        model_task.cancel()
        with suppress(asyncio.CancelledError):
            await model_task
    await app.state.cache.close()


# -- App --

# The served description. Whatever reads the contract — Swagger UI, a codegen
# run, a consumer opening `contract/openapi.yaml` — meets the deployment
# posture before it meets a route, because there is no authentication layer
# further in to discover. `docs/configuration.md` is the long form.
_APP_DESCRIPTION = (
    "Forage fetches, sanitizes and caches web content for an AI agent: it "
    "reports what it found and how confident it is, and its consumer owns "
    "every trust decision.\n\n"
    "**Forage ships no authentication.** Every endpoint below — and "
    "`/docs`, `/redoc` and `/openapi.json` with them — is unauthenticated by "
    "design. There is no API key, no bearer token, no allowlist and no rate "
    "limit, so **network placement is the access control**: run Forage only "
    "on a private network and never publish its port to an untrusted segment. "
    "Anyone who can reach it can make it fetch arbitrary URLs on your behalf "
    "and can read every counter `/metrics` exposes.\n\n"
    "Forage is not a trust boundary. A `healthy` Forage is not a promise that "
    "the content it returned is safe. See `docs/configuration.md`, "
    '"Deployment posture".'
)

# `version` is the wire contract's version, not the package's: consumers
# negotiate on `contract_version`, and a document whose `info.version` said
# anything else would be a second, disagreeing answer to the same question.
# `pyproject.toml`'s version is packaging metadata and stays independent
# (docs/releases.md).
app = FastAPI(
    title="Forage",
    version=CONTRACT_VERSION,
    description=_APP_DESCRIPTION,
    lifespan=lifespan,
)
_initial_extraction_settings = extraction_settings_from_config({})
app.state.extraction_settings = _initial_extraction_settings
# The file route's module-level fallback shape, for a transport that never
# fires lifespan events. Deliberately silent: the `retrieve_budget_unset`
# WARNING belongs to the lifespan, so a lifespan-free test does not emit a
# boot warning nobody configured.
app.state.retrieve_settings = retrieve_settings_from_config({})
app.state.extraction_metrics = ExtractionMetrics()
app.state.extraction_admission = ExtractionAdmissionController(
    _initial_extraction_settings,
    app.state.extraction_metrics,
)
app.state.search_metrics = SearchMetrics()
app.state.retrieve_metrics = RetrieveMetrics()
# One process-wide instance for every lifespan-free transport, exactly like
# `extraction_admission` above: a test that saturates it must build its own or
# reset this attribute, or it leaks held slots into the next test.
app.state.retrieve_admission = ExtractionAdmissionController.from_retrieve_settings(
    app.state.retrieve_settings,
    app.state.retrieve_metrics,
)
app.state.model_metrics = ModelMetrics()
# Declared here as well as in the lifespan so the attributes exist for a
# transport that never fires lifespan events (`httpx.ASGITransport`, which the
# suite's `client` fixture uses) — `None` means "no acquisition was started".
app.state.model_task = None
app.state.model_acquisition = None
# `None` means "no lifespan resolved a chain"; `_resolved_search_providers`
# reads it and falls back to the default one-element SearXNG chain.
app.state.search_providers = None
# `None` means "no lifespan evaluated brave_key_present()";
# `_resolved_search_key_capabilities` reads it and falls back to `()`.
app.state.search_key_capabilities = None
app.state.classification_semaphore = asyncio.Semaphore(
    _initial_extraction_settings.classification_concurrency
)
app.state.cache_metrics = CacheMetrics()
app.add_middleware(DocumentSizeLimitMiddleware)
app.add_middleware(ExtractionAdmissionMiddleware)


# -- Error handler --


@app.exception_handler(PipelineError)
async def pipeline_error_handler(
    request: Request,
    exc: PipelineError,
) -> JSONResponse:
    """Return structured JSON for pipeline errors.

    The status is chosen by route and code together, never by code alone:
    ``busy`` is 429 on ``/extract`` and 422 everywhere else, because
    ``/retrieve``'s admission refusal carries the same literal and a new
    status on a route would be a MAJOR contract change.
    """
    content = exc.to_dict()
    if request.url.path == "/extract":
        content["sanitizer_revision"] = getattr(
            request.app.state,
            "sanitizer_revision",
            derive_sanitizer_revision(request.app.state.config),
        )
    return JSONResponse(
        status_code=(
            429 if exc.error == "busy" and request.url.path == "/extract" else 422
        ),
        content=content,
    )


# -- Routes --
#
# The ``responses=`` declarations below name exactly the route/status pairs
# that emit an error body today, and no others. ``/health`` and ``/metrics``
# get none: both only ever answer 200 — ``/health`` reports degradation in its
# body by design (see :class:`HealthResponse`), and neither takes a request
# body that could fail validation. The 404/413/429/503 declarations sit on
# ``/extract`` alone because both middlewares gate on ``path == "/extract"``.

_PIPELINE_422_DESCRIPTION = (
    "Pipeline refusal (coded body) or request validation failure "
    "(FastAPI's default body)."
)


@app.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Return service health status.

    Always responds 200, even when degraded — the compose healthcheck
    (bare ``curl -f``) only inspects the HTTP status, so a non-2xx here would
    flap the container instead of surfacing the real problem. Callers must
    check ``status``/``degraded_reasons`` in the body.
    """
    # Ping (subject to backoff) so a zero-traffic window still detects recovery
    cache = getattr(request.app.state, "cache", None)
    cache_connected = cache is not None and await cache.ping_if_due()
    sanitizer_revision = _resolved_sanitizer_revision(request.app.state)

    classifier_loaded = request.app.state.classifier.loaded
    degraded_reasons: list[DegradedReason] = []
    if not classifier_loaded:
        degraded_reasons.append(DEGRADED_PROMPTGUARD_UNAVAILABLE)
    if not cache_connected:
        degraded_reasons.append(DEGRADED_CACHE_UNAVAILABLE)
    capabilities = (
        {CAPABILITY_SEARCH_SANITIZATION: 1}
        if classifier_loaded or _break_glass_advertisement_enabled()
        else {}
    )
    # Computed independently of the sanitization entry above — from the
    # lifespan's single `brave_key_present()` verdict, never re-read from
    # the environment here.
    for key in _resolved_search_key_capabilities(request.app.state):
        capabilities[key] = 1

    return HealthResponse(
        status="degraded" if degraded_reasons else "healthy",
        promptguard_loaded=classifier_loaded,
        cache_connected=cache_connected,
        capabilities=capabilities,
        sanitizer_revision=sanitizer_revision,
        contract_version=CONTRACT_VERSION,
        cache_backend=_resolved_cache_backend(request.app.state),
        search_providers=[
            provider.name for provider in _resolved_search_providers(request.app.state)
        ],
        degraded_reasons=degraded_reasons,
    )


@app.get("/metrics", response_model=MetricsResponse)
async def metrics(request: Request) -> dict[str, Any]:
    """Expose internal extraction, search, retrieve, cache, and model counters.

    The handler still builds the body as a dict and lets
    :class:`MetricsResponse` validate it on the way out — that ordering is the
    point. A counter added here and not to the model fails response validation
    loudly rather than being filtered out of the wire in silence.
    """
    controller: ExtractionAdmissionController = request.app.state.extraction_admission
    extraction_metrics: ExtractionMetrics = request.app.state.extraction_metrics
    search_metrics: SearchMetrics = request.app.state.search_metrics
    retrieve_metrics: RetrieveMetrics = request.app.state.retrieve_metrics
    cache_metrics: CacheMetrics = request.app.state.cache_metrics
    model_metrics: ModelMetrics = request.app.state.model_metrics
    return {
        "contract_version": CONTRACT_VERSION,
        "extraction": {
            "requests": extraction_metrics.requests,
            "busy_rejections": extraction_metrics.busy_rejections,
            "semaphore_saturation": extraction_metrics.semaphore_saturation,
            "active": controller.active,
            "queued": controller.queued,
            "queued_bytes": controller.queued_bytes,
            "verdicts": extraction_metrics.verdicts,
            **_cgroup_memory_snapshot(),
        },
        "search": {
            "requests": search_metrics.requests,
            "errors": search_metrics.errors,
            "omitted_by_reason": search_metrics.omitted_by_reason,
            "unscanned_results": search_metrics.unscanned_results,
            "fallback_fired": search_metrics.fallback_fired,
            "paid_calls": search_metrics.paid_calls,
            "policy_unknown_provider": search_metrics.policy_unknown_provider,
            "classification_wait_timeouts": (
                search_metrics.classification_wait_timeouts
            ),
        },
        "retrieve": {
            "requests": retrieve_metrics.requests,
            "errors": retrieve_metrics.errors,
            "cache_hits": retrieve_metrics.cache_hits,
            "cache_misses": retrieve_metrics.cache_misses,
            "blocked_by_reason": retrieve_metrics.blocked_by_reason,
            "promptguard_state": retrieve_metrics.promptguard_state,
            "classification_wait_timeouts": (
                retrieve_metrics.classification_wait_timeouts
            ),
            "semaphore_saturation": retrieve_metrics.semaphore_saturation,
            "busy_rejections": retrieve_metrics.busy_rejections,
        },
        # A different layer from `retrieve.cache_hits`/`cache_misses` above,
        # not a duplicate of it — :class:`CacheMetricsResponse` says why, and
        # says it where a consumer reading the contract will find it.
        "cache": {
            "reconnect_attempts": cache_metrics.reconnect_attempts,
            "reconnect_successes": cache_metrics.reconnect_successes,
            "reconnect_failures": cache_metrics.reconnect_failures,
            "operation_failures": cache_metrics.operation_failures,
            "storage_hits": cache_metrics.storage_hits,
            "storage_misses": cache_metrics.storage_misses,
            "storage_evictions": cache_metrics.storage_evictions,
            "storage_oversize_skips": cache_metrics.storage_oversize_skips,
        },
        # Weight acquisition (feature-forage-model-bootstrap); the states these
        # five counters separate are documented on
        # :class:`ModelMetricsResponse`. `/metrics` was outside the frozen
        # response-model surface when this section was added additively — it is
        # inside it now, so a sixth counter is a contract change to classify,
        # not a free addition.
        "model": {
            "fetch_failures": model_metrics.fetch_failures,
            "verify_failures": model_metrics.verify_failures,
            "quarantines": model_metrics.quarantines,
            "fetch_in_progress": model_metrics.fetch_in_progress,
            "retries_scheduled": model_metrics.retries_scheduled,
        },
    }


@app.post(
    "/retrieve",
    response_model=RetrievedContent,
    responses={
        422: {
            "model": Pipeline422ErrorResponse | HTTPValidationError,
            "description": _PIPELINE_422_DESCRIPTION,
        },
    },
)
async def retrieve(request: Request, body: RetrieveRequest) -> RetrievedContent:
    """Fetch and sanitize one caller-named URL through the full pipeline.

    `/retrieve` fetches and sanitizes one caller-named URL through the full
    pipeline, cached by `sanitizer_revision`; `/search` finds and returns
    provider-extracted content for a query across sources — snippets or
    chunks, per result `content_kind` — from the configured provider chain,
    every result sanitized, never cached.

    `promptguard_fail_closed` is honoured on both routes; `/retrieve`
    additionally honours `promptguard_threshold`, `trusted_domains`,
    `verified_domains`, `blocked_domains` and `cache_ttl_hours`, while
    `/search` additionally honours `providers` and `allow_paid_fallback`
    (contract 1.2.0) and scans every result at the fixed 0.85 default at
    trust tier `standard` (`config.yaml`'s `promptguard_threshold` is not
    applied there). This documents today's divergence; changing it belongs
    to `epic-forage-hardening`.
    """
    retrieve_metrics: RetrieveMetrics = request.app.state.retrieve_metrics
    retrieve_metrics.requests += 1
    try:
        retrieve_settings: RetrieveSettings = request.app.state.retrieve_settings
        content = await run_retrieve_pipeline(
            body,
            cache=request.app.state.cache,
            classifier=request.app.state.classifier,
            config=request.app.state.config,
            sanitizer_revision=_resolved_sanitizer_revision(request.app.state),
            settings=retrieve_settings,
            retrieve_metrics=retrieve_metrics,
            classification_semaphore=request.app.state.classification_semaphore,
            extraction_settings=request.app.state.extraction_settings,
            admission=request.app.state.retrieve_admission,
        )
    except PipelineError as exc:
        retrieve_metrics.record_error(exc.error)
        raise
    retrieve_metrics.record_content(content)
    return content


@app.post(
    "/extract",
    response_model=ExtractedContent,
    responses={
        400: {
            "model": DetailResponse,
            "description": (
                "The request body could not be parsed as multipart form data. "
                "FastAPI's own body-parsing guard produces this, with the "
                "fixed detail 'There was an error parsing the body' — and it "
                "is also what an over-sized upload actually receives today: "
                "the guard catches the streaming size refusal below before it "
                "can become a 413. Measured on the pinned FastAPI; see "
                "kit_tools/docs/GOTCHAS.md."
            ),
        },
        404: {
            "model": DetailResponse,
            "description": (
                "The extract route is disabled for this deployment "
                "(extract_route_enabled is false)."
            ),
        },
        413: {
            "model": Admission413Response,
            "description": (
                "The request body crossed the byte limit while streaming; "
                "refused before multipart parsing, so no request_id exists. "
                "Documented because DocumentSizeLimitMiddleware emits exactly "
                "this shape, but currently shadowed on this route: see the "
                "400 above."
            ),
        },
        422: {
            "model": Extract422ErrorResponse | HTTPValidationError,
            "description": (
                "Document failure (coded body, carrying sanitizer_revision) or "
                "request validation failure (FastAPI's default body)."
            ),
        },
        429: {
            "model": RateLimit429Response,
            "description": "Extraction is at capacity and the admission queue is full.",
        },
        503: {
            "model": DetailResponse,
            "description": (
                "The extraction admission controller is not wired up — a "
                "process that is starting, or one served by a transport that "
                "never ran the lifespan."
            ),
        },
    },
)
async def extract(
    request: Request,
    file: Annotated[UploadFile, File()],
    filename: Annotated[str, Form()],
    mime_hint: Annotated[str | None, Form()] = None,
    extract_mode: Annotated[Literal["summary", "full"], Form()] = "full",
    request_id: Annotated[str | None, Form()] = None,
    timeout_s: Annotated[float | None, Form(gt=0)] = None,
) -> ExtractedContent:
    """Extract an internal-network upload with fixed untrusted fail-closed policy.

    This endpoint is unauthenticated — like every other Forage route — so
    network placement is its only access control: keep the service on a
    private network and never publish port 8020 to an untrusted one (see
    ``docs/configuration.md``, "Deployment posture"). Filename and MIME hint
    are display-only metadata; downstream consumers must never use them as
    filesystem paths.

    Document failures use these stable ``error`` tokens: ``content_too_large``,
    ``content_too_large_to_classify``, ``pdf_encrypted``, ``pdf_no_text``,
    ``unsupported_format``, ``extraction_failed``, and ``busy``. ``reason`` is
    always a fixed, content-free user message.
    """
    del timeout_s
    settings: ExtractionSettings = request.app.state.extraction_settings
    if not settings.route_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    started_at = time.monotonic()
    safe_request_id = uuid.uuid4().hex
    size = 0
    content_type = "unknown"
    spool: _SpoolResult | None = None
    try:
        safe_filename, safe_mime_hint, safe_request_id = _sanitize_upload_metadata(
            filename=filename,
            mime_hint=mime_hint,
            request_id=request_id,
        )
        try:
            threshold = float(
                request.app.state.config.get("promptguard_threshold", 0.85)
            )
        except (TypeError, ValueError) as exc:
            raise UnsupportedFormatError(
                "Sidecar promptguard_threshold configuration is invalid",
                safe_request_id,
            ) from exc
        if not 0.0 <= threshold <= 1.0:
            raise UnsupportedFormatError(
                "Sidecar promptguard_threshold configuration is invalid",
                safe_request_id,
            )
        spool = await _spool_upload(file, max_bytes=settings.max_input_bytes)
        size = spool.size
        result = await run_extract_pipeline_from_file(
            spool.path,
            filename=safe_filename,
            mime_hint=safe_mime_hint,
            extract_mode=extract_mode,
            request_id=safe_request_id,
            classifier=request.app.state.classifier,
            promptguard_threshold=threshold,
            sanitizer_revision=getattr(
                request.app.state,
                "sanitizer_revision",
                derive_sanitizer_revision(request.app.state.config),
            ),
            settings=settings,
            classification_semaphore=request.app.state.classification_semaphore,
        )
        content_type = result.content_type
        verdict = "injection_detected" if result.injection_detected else "success"
        request.app.state.extraction_metrics.record_verdict(verdict)
        logger.info(
            "document extraction completed",
            extra={
                "request_id": safe_request_id,
                "size": size,
                "content_type": content_type,
                "verdict": verdict,
                "reason": verdict,
                "duration": round(time.monotonic() - started_at, 3),
            },
        )
        return result
    except PipelineError as exc:
        request.app.state.extraction_metrics.record_verdict(exc.error)
        logger.info(
            "document extraction completed",
            extra={
                "request_id": safe_request_id,
                "size": size,
                "content_type": content_type,
                "verdict": "failure",
                "reason": exc.error,
                "duration": round(time.monotonic() - started_at, 3),
            },
        )
        raise
    finally:
        if spool is not None:
            spool.path.unlink(missing_ok=True)


@app.post(
    "/search",
    response_model=SearchResponse,
    responses={
        422: {
            "model": Pipeline422ErrorResponse | HTTPValidationError,
            "description": _PIPELINE_422_DESCRIPTION,
        },
    },
)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    """Find and return provider-extracted content for a query, across sources.

    `/search` finds and returns provider-extracted content for a query
    across sources — snippets or chunks, per result `content_kind` — from
    the configured provider chain, every result sanitized, never cached;
    `/retrieve` fetches and sanitizes one caller-named URL through the full
    pipeline, cached by `sanitizer_revision`.

    `promptguard_fail_closed` is honoured on both routes; `/search`
    additionally honours `providers` and `allow_paid_fallback` (contract
    1.2.0) and scans every result at the fixed 0.85 default at trust tier
    `standard` (`config.yaml`'s `promptguard_threshold` is not applied
    here), while `/retrieve` additionally honours `promptguard_threshold`,
    `trusted_domains`, `verified_domains`, `blocked_domains` and
    `cache_ttl_hours`. This documents today's divergence; changing it
    belongs to `epic-forage-hardening`.
    """
    search_metrics: SearchMetrics = request.app.state.search_metrics
    search_metrics.requests += 1
    configured_chain = _resolved_search_providers(request.app.state)
    effective_chain, ignored_count = apply_request_policy(configured_chain, body)
    search_metrics.policy_unknown_provider += ignored_count
    try:
        # The policy 422 is raised inside the same `try` as the pipeline's
        # own, so one `except` records every `search_unavailable` from the
        # exception's typed `error` — there is no second, string-literal
        # recording path to drift from `ErrorCode`.
        if not effective_chain:
            raise PipelineError(
                error="search_unavailable",
                reason=POLICY_EXCLUDED_ALL_PROVIDERS,
                request_id=uuid.uuid4().hex,
            )
        response = await run_search_pipeline(
            body,
            providers=effective_chain,
            configured_chain=configured_chain,
            config=request.app.state.config,
            classifier=request.app.state.classifier,
            search_metrics=search_metrics,
            classification_semaphore=request.app.state.classification_semaphore,
            classification_wait_seconds=(
                request.app.state.retrieve_settings.promptguard_wait_seconds
            ),
        )
    except PipelineError as exc:
        search_metrics.record_error(exc.error)
        raise
    search_metrics.record_response(response)
    return response
