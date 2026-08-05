"""Pipeline orchestrator -- wires Stages 1-5 into a single retrieve/search flow.

The orchestrator is the entry point for ``POST /retrieve`` and
``POST /search``.  It calls each stage function in sequence with hard
gates: a BLOCKED verdict at Stage 2 or an INJECTION_DETECTED verdict at
Stage 3 halts the pipeline and returns a quarantine
:class:`RetrievedContent`.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

import httpx

from cache import ContentCache, cache_policy_fingerprint
from models import (
    ExtractedContent,
    RetrievedContent,
    RetrieveRequest,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Stage2Verdict,
    Stage3Verdict,
    TrustTier,
    UploadProvenance,
)
from pipeline.stage1_extraction import extract_html
from pipeline.stage1_pdf import PDFExtractionError, detect_content_type, extract_pdf
from pipeline.stage1_upload import (
    UnsupportedUploadFormatError,
    detect_upload_content_type,
    extract_upload_text,
)
from pipeline.stage2_structural import scan_structural
from pipeline.stage3_promptguard import PromptGuardResult, run_promptguard
from pipeline.stage4_structuring import (
    build_extracted_content,
    build_retrieved_content,
)
from pipeline.stage5_url_audit import ContentTooLargeError, fetch_url
from url_validator import BlockedDomainError, PrivateIPError, validate_url

if TYPE_CHECKING:
    from promptguard.classifier import PromptGuardClassifier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Base class for pipeline errors that produce structured JSON."""

    def __init__(self, error: str, reason: str, request_id: str) -> None:
        self.error = error
        self.reason = reason
        self.request_id = request_id
        super().__init__(reason)

    def to_dict(self) -> dict[str, str]:
        return {
            "error": self.error,
            "reason": self.reason,
            "request_id": self.request_id,
        }


class UnsupportedFormatError(PipelineError):
    """Raised when an upload is not a supported PDF or valid text document."""

    def __init__(self, reason: str, request_id: str) -> None:
        super().__init__("unsupported_format", reason, request_id)


# ---------------------------------------------------------------------------
# Retrieve pipeline
# ---------------------------------------------------------------------------


async def run_retrieve_pipeline(
    request: RetrieveRequest,
    *,
    cache: ContentCache | None,
    classifier: PromptGuardClassifier | None,
    config: dict[str, Any],
) -> RetrievedContent:
    """Run the full 5-stage retrieval pipeline.

    Parameters
    ----------
    request:
        Inbound retrieval request with URL and options.
    cache:
        Valkey content cache (may be ``None`` if disconnected).
    classifier:
        PromptGuard 2 classifier instance (may be ``None``).
    config:
        Sidecar configuration loaded from ``config.yaml``.

    Returns
    -------
    RetrievedContent — sanitised content or quarantine response.

    Raises
    ------
    PipelineError
        On validation failures (private IP, blocked domain, fetch
        timeout, invalid URL, etc.).
    """
    request_id = uuid.uuid4().hex

    # Merge blocklists: request-level + config seed_blocklist
    blocked_domains = list(request.blocked_domains)
    seed_blocklist: list[str] = config.get("seed_blocklist", [])
    for domain in seed_blocklist:
        if domain not in blocked_domains:
            blocked_domains.append(domain)
    cache_policy = cache_policy_fingerprint(
        trusted_domains=request.trusted_domains,
        verified_domains=request.verified_domains,
        blocked_domains=blocked_domains,
        promptguard_threshold=request.promptguard_threshold,
        promptguard_fail_closed=request.promptguard_fail_closed,
    )
    news_domains: list[str] = config.get("news_domains", [])

    # -- Step 1: Validate URL (RFC1918 + blocklist) --
    try:
        await validate_url(request.url, blocked_domains)
    except PrivateIPError as exc:
        raise PipelineError(
            error="private_ip",
            reason=str(exc),
            request_id=request_id,
        ) from exc
    except BlockedDomainError as exc:
        raise PipelineError(
            error="blocked_domain",
            reason=str(exc),
            request_id=request_id,
        ) from exc
    except ValueError as exc:
        raise PipelineError(
            error="invalid_url",
            reason=str(exc),
            request_id=request_id,
        ) from exc

    # -- Step 2: Check cache --
    if cache is not None:
        if request.cache_ttl_hours == 0:
            await cache.delete(
                request.url,
                extract_mode=request.extract_mode,
                policy_fingerprint=cache_policy,
            )
        else:
            cached = await cache.get(
                request.url,
                extract_mode=request.extract_mode,
                policy_fingerprint=cache_policy,
                ttl_hours=request.cache_ttl_hours,
                news_domains=news_domains,
            )
            if cached is not None:
                logger.info("Cache hit for %s", request.url)
                return cached.model_copy(update={"request_id": request_id})

    # -- Step 3: Fetch content --
    user_agents: list[str] = config.get("user_agents", [])
    try:
        fetch_result = await fetch_url(
            request.url,
            blocked_domains=blocked_domains,
            user_agents=user_agents if user_agents else None,
        )
    except PrivateIPError as exc:
        raise PipelineError(
            error="private_ip",
            reason=str(exc),
            request_id=request_id,
        ) from exc
    except BlockedDomainError as exc:
        raise PipelineError(
            error="blocked_domain",
            reason=str(exc),
            request_id=request_id,
        ) from exc
    except httpx.TimeoutException as exc:
        raise PipelineError(
            error="fetch_timeout",
            reason=f"Request timed out fetching {request.url}",
            request_id=request_id,
        ) from exc
    except ContentTooLargeError as exc:
        raise PipelineError(
            error="content_too_large",
            reason=str(exc),
            request_id=request_id,
        ) from exc
    except Exception as exc:
        raise PipelineError(
            error="fetch_error",
            reason=f"Failed to fetch {request.url}: {exc}",
            request_id=request_id,
        ) from exc

    # -- Step 4: Detect content type and run Stage 1 extraction --
    content_type = detect_content_type(
        fetch_result.content_type,
        fetch_result.response_body,
    )

    if content_type == "pdf":
        extraction = extract_pdf(fetch_result.response_body)
    else:
        html_text = fetch_result.response_body.decode("utf-8", errors="replace")
        extraction = extract_html(html_text, request.url)

    # -- Step 5: Stage 2 structural scan --
    structural = scan_structural(extraction.raw_text)

    # Determine domain from final URL
    parsed_final = urlparse(fetch_result.final_url)
    domain = parsed_final.hostname or ""

    if structural.verdict == Stage2Verdict.BLOCKED:
        # Hard gate: return quarantine response
        logger.warning(
            "Stage 2 BLOCKED for %s — returning quarantine",
            request.url,
        )
        # Build a PromptGuard placeholder (skipped)
        pg_result = PromptGuardResult(
            verdict=Stage3Verdict.SAFE,
            score=0.0,
            skipped=True,
        )
        content = build_retrieved_content(
            request_id=request_id,
            source_url=request.url,
            final_url=fetch_result.final_url,
            domain=domain,
            extract_mode=request.extract_mode,
            extraction=extraction,
            structural=structural,
            promptguard=pg_result,
            redirect_chain=fetch_result.redirect_chain,
            domain_changed_on_redirect=fetch_result.domain_changed_on_redirect,
            trusted_domains=request.trusted_domains,
            verified_domains=request.verified_domains,
            blocked_domains=blocked_domains,
            content_type=content_type,
        )
        return content.model_copy(update={"injection_detected": True})

    # -- Step 6: Stage 3 PromptGuard --
    # Resolve trust tier for skip logic
    _trust_tier = _resolve_request_trust_tier(
        domain,
        request.trusted_domains,
        request.verified_domains,
        blocked_domains,
    )
    pg_result = await run_promptguard(
        extraction.raw_text,
        classifier,
        threshold=request.promptguard_threshold,
        trust_tier=_trust_tier,
        fail_closed=request.promptguard_fail_closed,
    )

    if pg_result.verdict == Stage3Verdict.INJECTION_DETECTED:
        logger.warning(
            "Stage 3 INJECTION_DETECTED for %s — returning quarantine",
            request.url,
        )
        content = build_retrieved_content(
            request_id=request_id,
            source_url=request.url,
            final_url=fetch_result.final_url,
            domain=domain,
            extract_mode=request.extract_mode,
            extraction=extraction,
            structural=structural,
            promptguard=pg_result,
            redirect_chain=fetch_result.redirect_chain,
            domain_changed_on_redirect=fetch_result.domain_changed_on_redirect,
            trusted_domains=request.trusted_domains,
            verified_domains=request.verified_domains,
            blocked_domains=blocked_domains,
            content_type=content_type,
        )
        return content.model_copy(update={"injection_detected": True})

    # -- Step 7: Stage 4 structuring --
    content = build_retrieved_content(
        request_id=request_id,
        source_url=request.url,
        final_url=fetch_result.final_url,
        domain=domain,
        extract_mode=request.extract_mode,
        extraction=extraction,
        structural=structural,
        promptguard=pg_result,
        redirect_chain=fetch_result.redirect_chain,
        domain_changed_on_redirect=fetch_result.domain_changed_on_redirect,
        trusted_domains=request.trusted_domains,
        verified_domains=request.verified_domains,
        blocked_domains=blocked_domains,
        content_type=content_type,
    )

    # -- Step 8: Cache result --
    if (
        cache is not None
        and request.cache_ttl_hours > 0
        and content.trust_tier
        not in {
            TrustTier.UNTRUSTED,
            TrustTier.BLOCKED,
        }
    ):
        await cache.put(
            request.url,
            content,
            extract_mode=request.extract_mode,
            policy_fingerprint=cache_policy,
            ttl_hours=request.cache_ttl_hours,
            domain=domain,
            news_domains=news_domains,
        )

    return content


# ---------------------------------------------------------------------------
# Upload extraction pipeline
# ---------------------------------------------------------------------------


async def run_extract_pipeline(
    content_bytes: bytes,
    *,
    filename: str,
    mime_hint: str | None,
    extract_mode: str,
    request_id: str,
    classifier: PromptGuardClassifier | None,
    promptguard_threshold: float,
    sanitizer_revision: str,
) -> ExtractedContent:
    """Extract and sanitize an untrusted uploaded PDF or UTF-8 text document.

    Uploads intentionally have no caller-controlled trust policy. They always
    run PromptGuard as untrusted and fail closed when the classifier is absent.
    """
    try:
        content_type = detect_upload_content_type(content_bytes, mime_hint)
        extraction = (
            extract_pdf(content_bytes)
            if content_type == "pdf"
            else extract_upload_text(content_bytes, mime_hint)
        )
    except UnsupportedUploadFormatError as exc:
        raise UnsupportedFormatError(str(exc), request_id) from exc
    except PDFExtractionError as exc:
        raise UnsupportedFormatError(str(exc), request_id) from exc

    structural = scan_structural(extraction.raw_text)
    if structural.verdict == Stage2Verdict.BLOCKED:
        promptguard = PromptGuardResult(
            verdict=Stage3Verdict.SAFE,
            score=0.0,
            skipped=True,
        )
    else:
        promptguard = await run_promptguard(
            extraction.raw_text,
            classifier,
            threshold=promptguard_threshold,
            trust_tier=TrustTier.UNTRUSTED,
            fail_closed=True,
        )

    return build_extracted_content(
        request_id=request_id,
        provenance=UploadProvenance(filename=filename, mime_hint=mime_hint),
        extraction=extraction,
        structural=structural,
        promptguard=promptguard,
        extract_mode=extract_mode,
        content_type=content_type,
        sanitizer_revision=sanitizer_revision,
    )


# ---------------------------------------------------------------------------
# Search pipeline
# ---------------------------------------------------------------------------

# Default SearXNG URL (overridable via environment)
_DEFAULT_SEARXNG_URL = "http://poppy-searxng:8080"
_MAX_SEARCH_RESULTS_SCANNED = 20
_MAX_SEARCH_TITLE_LENGTH = 512
_MAX_SEARCH_URL_LENGTH = 2_048
_MAX_SEARCH_SNIPPET_LENGTH = 2_000
_LOCAL_PROMPTGUARD_TARGET_MS = 1_000
_TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS = 5_000
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _normalize_search_text(value: object, *, max_length: int) -> str:
    """Normalize and bound a model-visible search field before scanning it."""
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFC", value)
    normalized = _CONTROL_CHARS_RE.sub("", normalized)
    normalized = " ".join(normalized.split())
    return normalized[:max_length]


def _sanitize_search_text(value: object, *, max_length: int) -> tuple[str, str]:
    """Apply Stage 1 extraction to one bounded search text field."""
    normalized = _normalize_search_text(value, max_length=max_length)
    extraction = extract_html(f"<div>{normalized}</div>")
    return (
        _normalize_search_text(extraction.raw_text, max_length=max_length),
        _normalize_search_text(extraction.raw_text, max_length=max_length),
    )


def _canonicalize_search_url(value: object) -> tuple[str, str] | None:
    """Normalize a result URL and allow only canonical HTTP(S) URLs."""
    normalized = _normalize_search_text(value, max_length=_MAX_SEARCH_URL_LENGTH)
    if not normalized or any(character.isspace() for character in normalized):
        return None

    # Stage 1 processes this field before its Stage 2 structural scan, even
    # though the model-visible form is the canonical URL rather than prose.
    _visible, scanned = _sanitize_search_text(
        unquote(normalized),
        max_length=_MAX_SEARCH_URL_LENGTH,
    )
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        return None

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    canonical = urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path,
            parsed.query,
            "",
        )
    )
    return (
        _normalize_search_text(canonical, max_length=_MAX_SEARCH_URL_LENGTH),
        scanned,
    )


def _search_result_promptguard_input(title: str, url: str, snippet: str) -> str:
    """Build the single bounded PromptGuard input for a complete result."""
    return f"Title: {title}\nURL: {url}\nSnippet: {snippet}"


async def run_search_pipeline(
    request: SearchRequest,
    *,
    searxng_url: str = _DEFAULT_SEARXNG_URL,
    config: dict[str, Any],
    classifier: Any = None,
    promptguard_threshold: float = 0.85,
) -> SearchResponse:
    """Run a web search through SearXNG with complete-result sanitization.

    Queries SearXNG for results, then sanitizes every model-visible title,
    URL, and snippet through Stage 1 (HTML extraction), Stage 2 (structural
    scan), and one aggregate Stage 3 PromptGuard classification per result.
    At most ``_MAX_SEARCH_RESULTS_SCANNED`` results are classified, bounding
    search-path inference work to 20 PromptGuard passes.

    - BLOCKED result fields (Stage 2 or 3) omit the entire result.
    - SUSPICIOUS fields are included with a ``suspicious`` flag.
    - SearXNG errors raise :class:`PipelineError` with a descriptive message.
    """
    request_id = uuid.uuid4().hex

    # -- Call SearXNG --
    # Request extra results to compensate for any BLOCKED omissions.
    fetch_limit = min(request.num_results * 2, _MAX_SEARCH_RESULTS_SCANNED)
    raw_results: list[dict[str, Any]] = []
    unresponsive_engines: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{searxng_url}/search",
                params={
                    "q": request.query,
                    "format": "json",
                    "pageno": 1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw_results = data.get("results", [])[:fetch_limit]
            unresponsive_engines = [
                e[0] if isinstance(e, (list, tuple)) else str(e)
                for e in data.get("unresponsive_engines", [])
            ]
    except httpx.HTTPStatusError as exc:
        raise PipelineError(
            error="searxng_error",
            reason=f"SearXNG returned HTTP {exc.response.status_code}",
            request_id=request_id,
        ) from exc
    except Exception as exc:
        raise PipelineError(
            error="searxng_unavailable",
            reason=f"SearXNG not reachable at {searxng_url}: {exc}",
            request_id=request_id,
        ) from exc

    # -- Sanitize complete results through Stages 1-3 --
    sanitized_results: list[SearchResult] = []
    promptguard_started = time.perf_counter()
    promptguard_scanned = 0
    for raw in raw_results:
        if len(sanitized_results) >= request.num_results:
            break

        title, title_scan_text = _sanitize_search_text(
            raw.get("title", ""),
            max_length=_MAX_SEARCH_TITLE_LENGTH,
        )
        canonical_url = _canonicalize_search_url(raw.get("url", ""))
        if canonical_url is None:
            logger.info("Omitting search result with invalid URL")
            continue
        url, url_scan_text = canonical_url
        snippet, snippet_scan_text = _sanitize_search_text(
            raw.get("content", ""),
            max_length=_MAX_SEARCH_SNIPPET_LENGTH,
        )
        engine = raw.get("engine")
        suspicious = False

        # Stage 2: scan every model-visible field before exposing the result.
        blocked = False
        for field_name, field_text in (
            ("title", title_scan_text),
            ("url", url_scan_text),
            ("snippet", snippet_scan_text),
        ):
            scan = scan_structural(field_text)
            if scan.verdict == Stage2Verdict.BLOCKED:
                logger.info(
                    "Omitting blocked search result (%s structural): %s",
                    field_name,
                    url,
                )
                blocked = True
                break
            if scan.verdict == Stage2Verdict.SUSPICIOUS:
                suspicious = True
        if blocked:
            continue

        # Stage 3 always runs, including when the classifier is unavailable.
        # run_promptguard then honors request.promptguard_fail_closed.
        pg_result = await run_promptguard(
            _search_result_promptguard_input(title, url, snippet),
            classifier,
            threshold=promptguard_threshold,
            trust_tier="standard",
            fail_closed=request.promptguard_fail_closed,
        )
        promptguard_scanned += 1
        if pg_result.verdict == Stage3Verdict.INJECTION_DETECTED:
            logger.info(
                "Omitting blocked search result (promptguard score=%.2f): %s",
                pg_result.score,
                url,
            )
            continue
        if pg_result.score > 0.5:
            suspicious = True

        sanitized_results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=snippet,
                engine=engine if isinstance(engine, str) else None,
                suspicious=suspicious,
            )
        )

    promptguard_duration_ms = round(
        (time.perf_counter() - promptguard_started) * 1000,
        2,
    )
    logger.info(
        "search_promptguard_complete",
        extra={
            "scanned_results": promptguard_scanned,
            "max_scanned_results": _MAX_SEARCH_RESULTS_SCANNED,
            "duration_ms": promptguard_duration_ms,
            "local_target_ms": _LOCAL_PROMPTGUARD_TARGET_MS,
            "tool_augmented_first_token_target_ms": (
                _TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS
            ),
        },
    )
    if promptguard_duration_ms > _LOCAL_PROMPTGUARD_TARGET_MS:
        logger.warning(
            "search_promptguard_local_latency_target_exceeded",
            extra={
                "duration_ms": promptguard_duration_ms,
                "local_target_ms": _LOCAL_PROMPTGUARD_TARGET_MS,
                "tool_augmented_first_token_target_ms": (
                    _TOOL_AUGMENTED_FIRST_TOKEN_TARGET_MS
                ),
            },
        )

    return SearchResponse(
        results=sanitized_results,
        request_id=request_id,
        query=request.query,
        unresponsive_engines=unresponsive_engines,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_request_trust_tier(
    domain: str,
    trusted_domains: list[str],
    verified_domains: list[str],
    blocked_domains: list[str],
) -> str:
    """Resolve the trust tier string for a domain from request lists."""
    lower = domain.lower()
    if lower in {d.lower() for d in blocked_domains}:
        return "blocked"
    if lower in {d.lower() for d in trusted_domains}:
        return "trusted"
    if lower in {d.lower() for d in verified_domains}:
        return "verified"
    return "standard"
