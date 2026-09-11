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
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
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
    PromptGuardState,
)
from pipeline.extraction_limits import (
    MAX_INPUT_BYTES,
    ExtractionSettings,
    extraction_settings_from_config,
)
from pipeline.orchestrator import (
    DOCUMENT_FAILURE_REASONS,
    PipelineError,
    UnsupportedFormatError,
    document_failure,
    run_extract_pipeline_from_file,
    run_retrieve_pipeline,
    run_search_pipeline,
)
from pipeline.sanitizer_revision import derive_sanitizer_revision
from promptguard.classifier import PromptGuardClassifier

logger = logging.getLogger(__name__)

# Runtime configuration is 12-factor: every setting arrives as an environment
# variable at container start (see ``docs/configuration.md``). There is no
# vault client and no secret-bearing config API — ``VALKEY_URL`` arrives
# ready-made, credentials and all, from the operator's env or secret store.
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")

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
    arm it. Only ``capabilities`` lies under this flag; ``status``,
    ``degraded_reasons``, and ``promptguard_loaded`` stay honest.
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
    — a process does not change backend while it runs. The fallback exists for
    the same reason ``sanitizer_revision``'s does: a transport that never fires
    lifespan events still has to get an honest answer out of ``/health``, and
    answering it from the environment is the same question the lifespan asked.
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


def _load_config() -> dict[str, Any]:
    """Load sidecar configuration from ``config.yaml``."""
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    logger.warning("config.yaml not found at %s", config_path)
    return {}


# -- Response models --

# The one capability key ``/health`` advertises, named once so the CI contract
# smoke (``contract_smoke.py``) can import it instead of restating the wire
# string. The literal itself is still pinned by ``tests/test_app.py``, which
# spells it out: the constant single-sources the *symbol*, those tests pin the
# *value*, and renaming the value without meaning to fails them.
CAPABILITY_SEARCH_SANITIZATION = "search_sanitization"


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
    capabilities: dict[str, int]
    sanitizer_revision: str
    contract_version: str
    cache_backend: CacheBackend = Field(
        description=(
            "Which storage the content cache selected at start: 'valkey' when "
            "VALKEY_URL was set, 'memory' when it was fully unset. Added in "
            "contract 1.1.0."
        )
    )
    degraded_reasons: list[str] = []


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
        metrics: ExtractionMetrics,
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
    """Spool a bounded upload to a 0600 sidecar-owned file for the parser child."""
    path: Path | None = None
    received_bytes = 0
    try:
        with tempfile.NamedTemporaryFile(
            prefix="poppy-extract-",
            suffix=".upload",
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
    app.state.extraction_metrics = ExtractionMetrics()
    app.state.extraction_admission = ExtractionAdmissionController(
        settings,
        app.state.extraction_metrics,
    )
    app.state.search_metrics = SearchMetrics()
    app.state.retrieve_metrics = RetrieveMetrics()
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

app = FastAPI(
    title="Poppy Retrieval Sidecar",
    version="0.1.0",
    lifespan=lifespan,
)
_initial_extraction_settings = extraction_settings_from_config({})
app.state.extraction_settings = _initial_extraction_settings
app.state.extraction_metrics = ExtractionMetrics()
app.state.extraction_admission = ExtractionAdmissionController(
    _initial_extraction_settings,
    app.state.extraction_metrics,
)
app.state.search_metrics = SearchMetrics()
app.state.retrieve_metrics = RetrieveMetrics()
app.state.model_metrics = ModelMetrics()
# Declared here as well as in the lifespan so the attributes exist for a
# transport that never fires lifespan events (`httpx.ASGITransport`, which the
# suite's `client` fixture uses) — `None` means "no acquisition was started".
app.state.model_task = None
app.state.model_acquisition = None
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
    """Return structured JSON for pipeline errors."""
    content = exc.to_dict()
    if request.url.path == "/extract":
        content["sanitizer_revision"] = getattr(
            request.app.state,
            "sanitizer_revision",
            derive_sanitizer_revision(request.app.state.config),
        )
    return JSONResponse(
        status_code=429 if exc.error == "busy" else 422,
        content=content,
    )


# -- Routes --


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
    degraded_reasons: list[str] = []
    if not classifier_loaded:
        degraded_reasons.append(DEGRADED_PROMPTGUARD_UNAVAILABLE)
    if not cache_connected:
        degraded_reasons.append(DEGRADED_CACHE_UNAVAILABLE)
    capabilities = (
        {CAPABILITY_SEARCH_SANITIZATION: 1}
        if classifier_loaded or _break_glass_advertisement_enabled()
        else {}
    )

    return HealthResponse(
        status="degraded" if degraded_reasons else "healthy",
        promptguard_loaded=classifier_loaded,
        cache_connected=cache_connected,
        capabilities=capabilities,
        sanitizer_revision=sanitizer_revision,
        contract_version=CONTRACT_VERSION,
        cache_backend=_resolved_cache_backend(request.app.state),
        degraded_reasons=degraded_reasons,
    )


@app.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    """Expose internal extraction, search, retrieve, cache, and model counters."""
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
        },
        "retrieve": {
            "requests": retrieve_metrics.requests,
            "errors": retrieve_metrics.errors,
            "cache_hits": retrieve_metrics.cache_hits,
            "cache_misses": retrieve_metrics.cache_misses,
            "blocked_by_reason": retrieve_metrics.blocked_by_reason,
            "promptguard_state": retrieve_metrics.promptguard_state,
        },
        # Two layers share this response and are not duplicates of each other.
        # `retrieve.cache_hits`/`cache_misses` above count *request* outcomes;
        # the `storage_*` counters here count *storage operations* underneath
        # the cache's policy layer, and only the in-memory storage can move
        # `storage_evictions` / `storage_oversize_skips` (Valkey does its own
        # eviction and has no byte bound of ours).
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
        # Weight acquisition (feature-forage-model-bootstrap). Additive:
        # `/metrics` is outside the frozen response-model surface, so this
        # section needs no CONTRACT_VERSION bump. `fetch_in_progress` is what
        # distinguishes "downloading ~270 MiB" from "wedged" while `/health`
        # reports degraded for both; `retries_scheduled` (US-005) separates
        # both of those from "waiting to try again", which is the state a
        # backoff introduces and nothing else reports.
        "model": {
            "fetch_failures": model_metrics.fetch_failures,
            "verify_failures": model_metrics.verify_failures,
            "quarantines": model_metrics.quarantines,
            "fetch_in_progress": model_metrics.fetch_in_progress,
            "retries_scheduled": model_metrics.retries_scheduled,
        },
    }


@app.post("/retrieve", response_model=RetrievedContent)
async def retrieve(request: Request, body: RetrieveRequest) -> RetrievedContent:
    """Retrieve and sanitize web content through the full pipeline."""
    retrieve_metrics: RetrieveMetrics = request.app.state.retrieve_metrics
    retrieve_metrics.requests += 1
    try:
        content = await run_retrieve_pipeline(
            body,
            cache=request.app.state.cache,
            classifier=request.app.state.classifier,
            config=request.app.state.config,
            sanitizer_revision=_resolved_sanitizer_revision(request.app.state),
        )
    except PipelineError as exc:
        retrieve_metrics.record_error(exc.error)
        raise
    retrieve_metrics.record_content(content)
    return content


@app.post("/extract", response_model=ExtractedContent)
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


@app.post("/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    """Run a web search through SearXNG with snippet sanitization."""
    search_metrics: SearchMetrics = request.app.state.search_metrics
    search_metrics.requests += 1
    try:
        response = await run_search_pipeline(
            body,
            searxng_url=SEARXNG_URL,
            config=request.app.state.config,
            classifier=request.app.state.classifier,
        )
    except PipelineError as exc:
        search_metrics.record_error(exc.error)
        raise
    search_metrics.record_response(response)
    return response
