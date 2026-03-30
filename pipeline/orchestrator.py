"""Pipeline orchestrator -- wires Stages 1-5 into a single retrieve/search flow.

The orchestrator is the entry point for ``POST /retrieve`` and
``POST /search``.  It calls each stage function in sequence with hard
gates: a BLOCKED verdict at Stage 2 or an INJECTION_DETECTED verdict at
Stage 3 halts the pipeline and returns a quarantine
:class:`RetrievedContent`.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from cache import ContentCache
from models import (
    RetrievedContent,
    RetrieveRequest,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Stage2Verdict,
    Stage3Verdict,
    TrustTier,
)
from pipeline.stage1_extraction import ExtractionResult, extract_html
from pipeline.stage1_pdf import detect_content_type, extract_pdf
from pipeline.stage2_structural import StructuralScanResult, scan_structural
from pipeline.stage3_promptguard import PromptGuardResult, run_promptguard
from pipeline.stage4_structuring import build_retrieved_content
from pipeline.stage5_url_audit import fetch_url
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


# ---------------------------------------------------------------------------
# Retrieve pipeline
# ---------------------------------------------------------------------------


async def run_retrieve_pipeline(
    request: RetrieveRequest,
    *,
    cache: ContentCache | None,
    classifier: "PromptGuardClassifier | None",
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

    # -- Step 1: Validate URL (RFC1918 + blocklist) --
    try:
        validate_url(request.url, blocked_domains)
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
        cached = await cache.get(request.url)
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
    except Exception as exc:
        raise PipelineError(
            error="fetch_error",
            reason=f"Failed to fetch {request.url}: {exc}",
            request_id=request_id,
        ) from exc

    # -- Step 4: Detect content type and run Stage 1 extraction --
    content_type = detect_content_type(
        fetch_result.content_type, fetch_result.response_body,
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
            "Stage 2 BLOCKED for %s — returning quarantine", request.url,
        )
        # Build a PromptGuard placeholder (skipped)
        pg_result = PromptGuardResult(
            verdict=Stage3Verdict.SAFE, score=0.0, skipped=True,
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
        )
        return content.model_copy(update={"injection_detected": True})

    # -- Step 6: Stage 3 PromptGuard --
    # Resolve trust tier for skip logic
    _trust_tier = _resolve_request_trust_tier(
        domain, request.trusted_domains, request.verified_domains, blocked_domains,
    )
    pg_result = run_promptguard(
        extraction.raw_text,
        classifier,
        threshold=request.promptguard_threshold,
        trust_tier=_trust_tier,
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
    )

    # -- Step 8: Cache result --
    if cache is not None and content.trust_tier not in {
        TrustTier.UNTRUSTED,
        TrustTier.BLOCKED,
    }:
        news_domains: list[str] = config.get("news_domains", [])
        await cache.put(
            request.url,
            content,
            domain=domain,
            news_domains=news_domains,
        )

    return content


# ---------------------------------------------------------------------------
# Search pipeline
# ---------------------------------------------------------------------------

# Default SearXNG URL (overridable via environment)
_DEFAULT_SEARXNG_URL = "http://poppy-searxng:8080"


async def run_search_pipeline(
    request: SearchRequest,
    *,
    searxng_url: str = _DEFAULT_SEARXNG_URL,
    config: dict[str, Any],
) -> SearchResponse:
    """Run a web search through SearXNG with snippet sanitization.

    SearXNG integration is stubbed -- Spec 2 will wire it fully.
    The HTTP call is attempted but connection errors are handled
    gracefully, returning an empty result set.

    Snippets from SearXNG are sanitized through Stage 1 extraction
    (HTML stripping) and Stage 2 structural scan.  Snippets flagged
    as BLOCKED are replaced with a safe placeholder.
    """
    request_id = uuid.uuid4().hex

    # -- Attempt SearXNG HTTP call --
    raw_results: list[dict[str, Any]] = []
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
            raw_results = data.get("results", [])[:request.num_results]
    except Exception:
        # SearXNG not available yet (Spec 2 will wire this).
        # Return empty results gracefully.
        logger.warning(
            "SearXNG not available at %s — returning empty results",
            searxng_url,
        )
        return SearchResponse(
            results=[],
            request_id=request_id,
            query=request.query,
        )

    # -- Sanitize snippets through Stage 1 + 2 --
    sanitized_results: list[SearchResult] = []
    for raw in raw_results:
        title = raw.get("title", "")
        url = raw.get("url", "")
        snippet = raw.get("content", "")
        engine = raw.get("engine")

        # Stage 1: strip any HTML from snippet
        if snippet:
            extraction = extract_html(snippet)
            clean_snippet = extraction.main_content

            # Stage 2: structural scan
            scan = scan_structural(clean_snippet)
            if scan.verdict == Stage2Verdict.BLOCKED:
                clean_snippet = "[Content removed — injection detected]"
        else:
            clean_snippet = ""

        sanitized_results.append(SearchResult(
            title=title,
            url=url,
            snippet=clean_snippet,
            engine=engine,
        ))

    return SearchResponse(
        results=sanitized_results,
        request_id=request_id,
        query=request.query,
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
