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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cache import ContentCache
from models import (
    ExtractedContent,
    RetrievedContent,
    RetrieveRequest,
    SearchRequest,
    SearchResponse,
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

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://poppy-valkey:6379/4")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://poppy-searxng:8080")


def _load_config() -> dict[str, Any]:
    """Load sidecar configuration from ``config.yaml``."""
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    logger.warning("config.yaml not found at %s", config_path)
    return {}


async def _check_valkey() -> bool:
    """Check if Valkey/Redis is reachable."""
    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        client: aioredis.Redis = aioredis.from_url(  # type: ignore[assignment]
            VALKEY_URL,
            socket_connect_timeout=2,
        )
        await client.ping()  # type: ignore[misc]
        await client.aclose()
        return True
    except Exception:
        logger.warning("Valkey connection check failed")
        return False


# -- Response models --


class HealthResponse(BaseModel):
    """Response body for ``GET /health``."""

    status: str
    promptguard_loaded: bool
    cache_connected: bool
    capabilities: dict[str, int]
    sanitizer_revision: str


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
    app.state.classification_semaphore = asyncio.Semaphore(
        settings.classification_concurrency
    )
    app.state.sanitizer_revision = derive_sanitizer_revision(config)
    logger.info("Sidecar config loaded (%d keys)", len(config))

    # Connect content cache
    cache = ContentCache(VALKEY_URL)
    cache_ok = await cache.connect()
    app.state.cache = cache
    app.state.valkey_connected = cache_ok
    if cache_ok:
        logger.info("Content cache connected (Valkey)")
    else:
        logger.warning("Content cache not available at startup")

    # Load PromptGuard 2 model (CPU inference)
    classifier = PromptGuardClassifier()
    if classifier.load():
        logger.info("PromptGuard 2 model ready")
    else:
        logger.warning("PromptGuard 2 not available — ML injection detection disabled")
    app.state.classifier = classifier

    yield

    # Shutdown
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
app.state.classification_semaphore = asyncio.Semaphore(
    _initial_extraction_settings.classification_concurrency
)
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
    """Return service health status."""
    # Re-check Valkey on each health call for accurate status
    valkey_connected = await _check_valkey()
    request.app.state.valkey_connected = valkey_connected
    return HealthResponse(
        status="healthy",
        promptguard_loaded=request.app.state.classifier.loaded,
        cache_connected=valkey_connected,
        capabilities={"search_sanitization": 1},
        sanitizer_revision=getattr(
            request.app.state,
            "sanitizer_revision",
            derive_sanitizer_revision(request.app.state.config),
        ),
    )


@app.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    """Expose internal extraction saturation and cgroup OOM-proximity counters."""
    controller: ExtractionAdmissionController = request.app.state.extraction_admission
    extraction_metrics: ExtractionMetrics = request.app.state.extraction_metrics
    return {
        "extraction": {
            "requests": extraction_metrics.requests,
            "busy_rejections": extraction_metrics.busy_rejections,
            "semaphore_saturation": extraction_metrics.semaphore_saturation,
            "active": controller.active,
            "queued": controller.queued,
            "queued_bytes": controller.queued_bytes,
            "verdicts": extraction_metrics.verdicts,
            **_cgroup_memory_snapshot(),
        }
    }


@app.post("/retrieve", response_model=RetrievedContent)
async def retrieve(request: Request, body: RetrieveRequest) -> RetrievedContent:
    """Retrieve and sanitize web content through the full pipeline."""
    return await run_retrieve_pipeline(
        body,
        cache=request.app.state.cache,
        classifier=request.app.state.classifier,
        config=request.app.state.config,
    )


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

    This unauthenticated endpoint is intentionally reachable only on poppy-net
    (Traefik is disabled). Filename and MIME hint are display-only metadata;
    downstream consumers must never use them as filesystem paths.

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
    return await run_search_pipeline(
        body,
        searxng_url=SEARXNG_URL,
        config=request.app.state.config,
        classifier=request.app.state.classifier,
    )
