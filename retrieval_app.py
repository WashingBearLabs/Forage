"""Poppy Retrieval sidecar -- FastAPI service for web content retrieval.

Isolated container that fetches, sanitizes, and caches web content.
No direct database access. Communicates with core via internal API only.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
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
from pipeline.orchestrator import (
    DOCUMENT_FAILURE_REASONS,
    PipelineError,
    UnsupportedFormatError,
    document_failure,
    run_extract_pipeline,
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
_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class _RequestBodyTooLargeError(Exception):
    """Raised internally when an ASGI request body crosses its byte limit."""


class DocumentSizeLimitMiddleware:
    """Reject oversized extract requests while their ASGI body is still streaming."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Count received bytes without trusting Content-Length."""
        if scope["type"] != "http" or scope["path"] != "/extract":
            await self._app(scope, receive, send)
            return

        received_bytes = 0

        async def receive_limited() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._max_bytes:
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


async def _read_upload_bytes(
    file: UploadFile,
    *,
    max_bytes: int = _MAX_DOCUMENT_BYTES,
    chunk_size: int = _UPLOAD_READ_CHUNK_SIZE,
) -> bytes:
    """Read an upload incrementally and enforce the file-byte extraction cap."""
    chunks: list[bytes] = []
    received_bytes = 0
    while chunk := await file.read(chunk_size):
        received_bytes += len(chunk)
        if received_bytes > max_bytes:
            raise document_failure("content_too_large", uuid.uuid4().hex)
        chunks.append(chunk)
    return b"".join(chunks)


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
app.add_middleware(DocumentSizeLimitMiddleware, max_bytes=_MAX_DOCUMENT_BYTES)


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
        status_code=422,
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
    safe_filename, safe_mime_hint, safe_request_id = _sanitize_upload_metadata(
        filename=filename,
        mime_hint=mime_hint,
        request_id=request_id,
    )
    try:
        threshold = float(request.app.state.config.get("promptguard_threshold", 0.85))
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
    return await run_extract_pipeline(
        await _read_upload_bytes(file),
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
    )


@app.post("/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    """Run a web search through SearXNG with snippet sanitization."""
    return await run_search_pipeline(
        body,
        searxng_url=SEARXNG_URL,
        config=request.app.state.config,
        classifier=request.app.state.classifier,
    )
